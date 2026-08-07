#!/usr/bin/env bash
# probe_workflow_cancel_callers.sh — o botão "Cancelar" tem ALVO? (I5, § "Lacuna 4b")
#
# A PERGUNTA. `POST /v1/workflow/instances/{id}/cancel` é 410 hard (workflow-api/router.py:462) e
# quatro telas o chamam (ProcessosPage:414, WorkflowsPage:52, WorkflowMonitorPage:69,
# MonitorTab:642), todas com `catch { alert(String(e)) }` — o operador confirma um cancelamento e
# recebe `Error: HTTP 410`. A mensagem que ele NÃO vê manda usar
# `DELETE /v1/channels/webhook/{session_id}`, que não existe.
#
# O conserto que parece óbvio — reapontar as 4 telas para `POST /api/force-complete/:sessionId`
# (BFF 3100, já ramificado 200/404/501) — tem DOIS pré-requisitos que ninguém conferiu, e é para
# eles que este probe existe. Reapontar sem medir troca `HTTP 410` por `HTTP 404`: defeito novo,
# com data recente, que passa por conserto.
#
#   Pré-requisito 1 (endereçamento): o `force-complete` é endereçado por `session_id`, e a linha da
#   lista traz `session_id?` OPCIONAL (hooks.ts:24). Quantas linhas o têm?
#
#   Pré-requisito 2 (alvo): mesmo com `session_id`, o `force-complete` só FAZ algo no ramo 1 — há
#   item parqueado no ledger `{t}:work_task:{sid}` com `resume_token`. Sem ele, ou é 501 (pipeline
#   em execução) ou 404 (nada a encerrar). Uma instância `suspended` de workflow não é, por si,
#   item de fila: pode estar suspensa esperando webhook/timer, sem ledger nenhum.
#
# TRÊS LENTES, e a 2 é a que pode DERRUBAR o conserto proposto:
#   Lente 1 — cobertura de `session_id`, separando o conjunto CANCELÁVEL (active+suspended) do
#             terminal. Cancelar instância terminal não é caso de uso; misturá-los daria uma
#             cobertura alta e irrelevante.
#   Lente 2 — para cada linha cancelável com `session_id`, qual RAMO o `force-complete` tomaria
#             (ledger → 200 · running → 501 · nada → 404). É a previsão da resposta do botão.
#   Lente 3 — os dois endpoints, exercidos: o 410 é real, e o substituto que ele nomeia não existe.
#             Com CONTROLE DE PRESENÇA ao lado (uma rota que existe no mesmo host precisa
#             responder) — senão "não existe" é indistinguível de "gateway fora".
#
# PREVISÕES, escritas ANTES de rodar (método § 4). Autor: sessão 2026-08-07.
#
#   P1  workflow.instances tem entre 20 e 400 linhas em tenant_demo (e2e 13/14 + smokes de
#       outbound/scheduler criam instâncias). ZERO ⇒ INCONCLUSIVO, não "cobertura 0%".
#   P2  o conjunto cancelável (active+suspended) fica entre 1 e 15% do total — smoke roteirizado
#       completa. Se for 0, este ambiente não pode responder à pergunta do botão.
#   P3  cobertura de `session_id` NO CANCELÁVEL ≥ 90%. Razão: sob Arc 19 a sessão nasce antes da
#       instância e é o identificador persistente. Se cair muito abaixo, a saída é (c) — remover o
#       botão —, não reapontá-lo.
#   P4  ⚠️ a previsão que interessa: a MAIORIA do cancelável cai no **ramo 3 (404)** — sem ledger
#       `work_task` e sem `pipeline:running`. Ledger é de item de FILA (wrap-up, aprovação,
#       formfill), e estas telas listam PROCESSOS; `running` só existe durante o step. Se P4
#       acertar, o `force-complete` NÃO é o conserto desta lacuna, e o probe terá evitado
#       reapontar quatro botões para um 404.
#
# Uso:  bash infra/test/probe_workflow_cancel_callers.sh [tenant]
# Pré:  workflow-api (3800), postgres, redis, channel-gateway (8010), mcp-server-plughub (3100).
# Saída: 0 = mediu e o conserto (a) está liberado · 1 = mediu e o número REPROVA o conserto (a)
#        · 2 = INCONCLUSIVO (não é zero, é ausência de medição).

set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
TENANT="${1:-tenant_demo}"
DB="plughub_demo"

WF="http://localhost:3800"
CGW="http://localhost:8010"

psqlq() { $DC exec -T postgres psql -U plughub -d "$DB" -tAc "$1" < /dev/null 2>/dev/null; }
redisq() { $DC exec -T redis redis-cli "$@" < /dev/null 2>/dev/null; }
num() { tr -d '\r\n[:space:]' <<<"${1:-}"; }

echo "══ probe: o botão Cancelar tem alvo? (I5 lacuna 4b) — tenant=$TENANT ══"
echo

# ── PREFLIGHT ─────────────────────────────────────────────────────────────────
# Método § 6: portão que julga o ALVO quando quem quebrou foi a MONTAGEM manda
# consertar código correto. Falha aqui PARA o probe.
#
# ⚠️ Correção 2026-08-07, 1ª execução: a v1 pingava `/health`, que NÃO EXISTE —
# o serviço expõe `/v1/health` (workflow-api/main.py:143). O comentário do
# docker-compose.demo.yml:207 documenta `localhost:3800/health`, e foi de lá que
# o caminho veio. Resultado: INCONCLUSIVO por montagem, com o serviço possivelmente
# de pé. Duas mudanças: (1) o preflight passou a exercer O ENDPOINT QUE O PROBE
# USA — health é proxy, o endpoint é a coisa; (2) ele DISTINGUE "serviço fora"
# (código 000, sem conexão) de "serviço de pé respondendo outra coisa", que é
# exatamente a diferença que o `curl -sf` colapsava num único falso.
FAIL_PRE=""
WF_CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 8 \
  "$WF/v1/workflow/instances?tenant_id=$TENANT&limit=1" 2>/dev/null)
case "$WF_CODE" in
  2*) : ;;
  000) FAIL_PRE="$FAIL_PRE workflow-api(3800):SEM-CONEXAO" ;;
  *)   FAIL_PRE="$FAIL_PRE workflow-api(3800):HTTP-$WF_CODE" ;;
esac
[ -n "$(num "$(psqlq 'SELECT 1')")" ] || FAIL_PRE="$FAIL_PRE postgres"
[ "$(num "$(redisq PING)")" = "PONG" ] || FAIL_PRE="$FAIL_PRE redis"
if [ -n "$FAIL_PRE" ]; then
  echo "⚠️  INCONCLUSIVO — pré-condição falhou:$FAIL_PRE"
  echo
  if [ "$WF_CODE" = "000" ]; then
    echo "   O workflow-api não atendeu na 3800. Conferir se ele SOBE:"
    echo "     docker compose -f docker-compose.demo.yml ps workflow-api"
    echo "     docker compose -f docker-compose.demo.yml logs --tail 40 workflow-api"
    echo
    echo "   ⚠️  Se ele estiver PARADO, isso não é ruído de ambiente — é um achado"
    echo "       maior que o desta sonda: as MESMAS quatro telas que têm o botão"
    echo "       Cancelar leem a lista por este serviço (hooks.ts:3 → /v1/workflow),"
    echo "       logo a página de Processos estaria vazia, e o botão seria o menor"
    echo "       dos problemas. Anotar no TODO § Lacuna 4b antes de seguir."
  fi
  exit 2
fi

# ── PROVAR QUE O LEITOR LÊ (contador-testemunha) ──────────────────────────────
# A tela lê pelo ENDPOINT; o probe conta pelo BANCO. Se o banco tem linhas e o
# endpoint devolve zero, o defeito é do leitor e qualquer cobertura medida pelo
# endpoint seria um número plausível e errado.
PG_TOTAL=$(num "$(psqlq "SELECT count(*) FROM workflow.instances WHERE tenant_id='$TENANT';")")
API_BODY=$(curl -sf -m 10 "$WF/v1/workflow/instances?tenant_id=$TENANT&limit=1000" 2>/dev/null)
API_TOTAL=$(jq -r 'if type=="array" then length else (.instances // [] | length) end' <<<"${API_BODY:-[]}" 2>/dev/null)
API_TOTAL=$(num "${API_TOTAL:-0}")

echo "── leitor ─────────────────────────────────────────────────────────────────"
echo "   linhas no banco (workflow.instances):  ${PG_TOTAL:-?}"
echo "   linhas devolvidas pelo endpoint:       ${API_TOTAL:-?}   (a tela lê por aqui)"
if [ "${PG_TOTAL:-0}" -eq 0 ] 2>/dev/null; then
  echo
  echo "   ⚠️  INCONCLUSIVO — ZERO instâncias no tenant. Isto é AUSÊNCIA DE AMOSTRA."
  echo "       P1 REPROVADA. Rodar o e2e 13/14 ou um smoke de outbound e repetir."
  exit 2
fi
if [ "${API_TOTAL:-0}" -eq 0 ] 2>/dev/null; then
  echo
  echo "   ⚠️  INCONCLUSIVO — o banco tem ${PG_TOTAL} linhas e o endpoint devolveu 0."
  echo "       O LEITOR está quebrado; medir cobertura por ele daria número plausível."
  exit 2
fi
echo

# ── LENTE 1 · cobertura de session_id, separando o conjunto CANCELÁVEL ────────
echo "── LENTE 1 · cobertura de session_id por status ───────────────────────────"
psqlq "
  SELECT status,
         count(*)                                              AS n,
         count(*) FILTER (WHERE session_id IS NOT NULL AND session_id <> '') AS com_sid,
         count(*) FILTER (WHERE session_id IS NULL  OR  session_id  = '')    AS sem_sid
    FROM workflow.instances
   WHERE tenant_id='$TENANT'
   GROUP BY status
   ORDER BY n DESC;" | awk -F'|' '{printf "   %-12s n=%-6s com_sid=%-6s sem_sid=%s\n",$1,$2,$3,$4}'

CANCELAVEL=$(num "$(psqlq "
  SELECT count(*) FROM workflow.instances
   WHERE tenant_id='$TENANT' AND status IN ('active','suspended');")")
CANC_SID=$(num "$(psqlq "
  SELECT count(*) FROM workflow.instances
   WHERE tenant_id='$TENANT' AND status IN ('active','suspended')
     AND session_id IS NOT NULL AND session_id <> '';")")

echo
echo "   CANCELÁVEL (active+suspended): ${CANCELAVEL}   com session_id: ${CANC_SID}"
if [ "${CANCELAVEL:-0}" -eq 0 ] 2>/dev/null; then
  echo
  echo "   ⚠️  INCONCLUSIVO para a decisão — nenhuma instância cancelável agora."
  echo "       P2 REPROVADA. O botão não tem alvo NESTE INSTANTE; isso não prova que"
  echo "       nunca tem. Repetir com uma instância suspensa viva."
  exit 2
fi
COB=$(( CANC_SID * 100 / CANCELAVEL ))
echo "   cobertura de session_id no cancelável: ${COB}%   (P3 previu ≥ 90%)"
echo

# ── LENTE 2 · qual RAMO o force-complete tomaria ──────────────────────────────
# Ramo 1 (200): ledger {t}:work_task:{sid} com resume_token
# Ramo 2 (501): {t}:pipeline:{sid}:running
# Ramo 3 (404): nenhum dos dois — o botão trocaria 410 por 404
echo "── LENTE 2 · ramo que o force-complete tomaria em cada cancelável ─────────"
R1=0; R2=0; R3=0; VISTOS=0; CAP=50; TRUNC="false"
SIDS=$(psqlq "
  SELECT session_id FROM workflow.instances
   WHERE tenant_id='$TENANT' AND status IN ('active','suspended')
     AND session_id IS NOT NULL AND session_id <> ''
   ORDER BY created_at DESC LIMIT $((CAP + 1));")

while IFS= read -r SID; do
  [ -z "$SID" ] && continue
  if [ "$VISTOS" -ge "$CAP" ]; then TRUNC="true"; break; fi
  VISTOS=$(( VISTOS + 1 ))
  LED=$(redisq GET "${TENANT}:work_task:${SID}")
  RUN=$(redisq GET "${TENANT}:pipeline:${SID}:running")
  if [ -n "$LED" ] && grep -q 'resume_token' <<<"$LED"; then
    R1=$(( R1 + 1 ))
  elif [ -n "$RUN" ]; then
    R2=$(( R2 + 1 ))
  else
    R3=$(( R3 + 1 ))
  fi
done <<< "$SIDS"

echo "   amostradas: ${VISTOS}${TRUNC:+   (truncado no teto de $CAP: $TRUNC)}"
echo "   ramo 1 — ledger work_task presente   → 200 encerra de verdade : ${R1}"
echo "   ramo 2 — pipeline em execução        → 501 nomeia a ausência   : ${R2}"
echo "   ramo 3 — nada a encerrar             → 404                     : ${R3}"
echo

# ── LENTE 3 · os dois endpoints, exercidos ────────────────────────────────────
echo "── LENTE 3 · o 410 e o substituto que ele nomeia ──────────────────────────"
ANY_ID=$(psqlq "SELECT id FROM workflow.instances WHERE tenant_id='$TENANT' LIMIT 1;" | tr -d '\r\n ')
CANCEL_CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 8 -X POST \
  "$WF/v1/workflow/instances/${ANY_ID}/cancel" \
  -H 'Content-Type: application/json' -d "{\"tenant_id\":\"$TENANT\"}" 2>/dev/null)
echo "   POST /v1/workflow/instances/{id}/cancel        → HTTP ${CANCEL_CODE}   (esperado 410)"

# Controle de PRESENÇA ao lado do contador de AUSÊNCIA: uma rota que EXISTE no
# mesmo host tem de responder. Sem isso, 404/405 no DELETE é indistinguível de
# "channel-gateway fora do ar".
CTRL_CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 8 \
  "$CGW/v1/channels/webhook/probe-nonexistent-session/status" 2>/dev/null)
DEL_CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 8 -X DELETE \
  "$CGW/v1/channels/webhook/probe-nonexistent-session" 2>/dev/null)
echo "   GET  /v1/channels/webhook/{sid}/status  [controle] → HTTP ${CTRL_CODE}"
echo "   DELETE /v1/channels/webhook/{sid}       [apontado] → HTTP ${DEL_CODE}   (405/404 = não existe)"
echo

# ── VEREDICTO — ramifica sobre o valor medido, três estados ───────────────────
echo "══ veredicto ══════════════════════════════════════════════════════════════"
RC=0

if [ "$CANCEL_CODE" != "410" ]; then
  echo "⚠️  INCONCLUSIVO — o /cancel devolveu ${CANCEL_CODE}, não 410. A premissa da"
  echo "    lacuna mudou desde a leitura de 2026-08-07; reler o router antes de decidir."
  exit 2
fi
if [ "$CTRL_CODE" = "000" ]; then
  echo "⚠️  INCONCLUSIVO — o controle de presença não respondeu (channel-gateway fora)."
  echo "    Nada se pode afirmar sobre a ausência do DELETE."
  exit 2
fi
echo "· o 410 é real, e o DELETE que a mensagem dele nomeia responde ${DEL_CODE}"
echo "  (controle vivo em ${CTRL_CODE}) — o substituto documentado NÃO existe. Confirmado."

if [ "$COB" -lt 90 ]; then
  echo "· P3 REPROVADA (cobertura ${COB}%): reapontar ao force-complete deixaria"
  echo "  $(( CANCELAVEL - CANC_SID )) linha(s) sem endereço. Saída (b) ou (c), não (a)."
  RC=1
else
  echo "· P3 confirmada (cobertura ${COB}%): endereçamento não bloqueia."
fi

if [ "$R1" -eq 0 ] 2>/dev/null; then
  echo "· ⚠️  P4 CONFIRMADA — NENHUMA instância cancelável tem item parqueado."
  echo "  Reapontar as 4 telas ao force-complete trocaria HTTP 410 por HTTP 404:"
  echo "  o conserto 'óbvio' está REPROVADO pela medição. Ir para (b)/(c) — declarar"
  echo "  na tela por que não há o que encerrar, ou remover o botão."
  RC=1
elif [ "$R1" -lt "$(( VISTOS / 2 + 1 ))" ] 2>/dev/null; then
  echo "· P4 parcialmente confirmada — só ${R1}/${VISTOS} teriam alvo real."
  echo "  Reapontar serve à minoria; a maioria precisa da saída (b)."
  RC=1
else
  echo "· P4 REPROVADA — ${R1}/${VISTOS} têm item parqueado. Reapontar ao"
  echo "  force-complete é o conserto certo (saída (a))."
fi

echo
echo "(rc=$RC · 0 = conserto (a) liberado · 1 = a medição reprova (a) · 2 = inconclusivo)"
exit $RC
