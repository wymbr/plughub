#!/usr/bin/env bash
# probe_invisibility_window.sh — a lacuna 2b tem número? (I5, § "Lacuna 2 — o que fechou e o que não")
#
# A PERGUNTA. Entre a expiração da lease (`routing.claim_lease_s`, default 180 s) e o prazo do
# item (`timeout_hours` do delegate, 24 h no wrap-up default), o trabalho está fora do ZSET e sem
# lease: ninguém o vê na inbox, **nem o próprio dono**. Um reaper que re-enfileirasse fecharia
# isso — mas o TODO exige política antes de código, e política precisa de número.
#
# POR QUE DUAS LENTES, E POR QUE A LENTE A SOZINHA NÃO RESPONDE.
#
#   Lente A (agora) — o estado `orphaned` do ledger `{t}:work_task:*`. É o instrumento que o TODO
#   indica, e ele é uma JANELA de ~25 h que NÃO ACUMULA: o ledger nasce com
#   `timeout_hours*3600 + 3600` e morre no resume. Num ambiente ocioso ele mede zero — e esse zero
#   NÃO é "não há órfãos", é "não há amostra". O script separa os dois casos em vez de somá-los
#   num número plausível.
#
#   Lente B (histórico) — `segments`, que acumula. Toda pendência author-bound fecha com um do
#   trio `task_submitted` / `acw_expired` / `acw_supervisor_closed`, e o segmento abre no CLAIM.
#   Logo `duration_ms` do submetido = claim→submit, que é **a mesma régua da lease**. É a
#   comparação que ninguém fez ainda: a `avg_fill_ms` da fatia 2 já existia, e nunca foi posta ao
#   lado dos 180 s.
#
# O NÚMERO QUE DECIDE A POLÍTICA não é quantos órfãos há hoje. É a fração de wrap-ups SUBMETIDOS
# que levou mais que a lease:
#
#   · fração baixa  → a janela é borda: o dono quase sempre entrega antes dos 180 s, e o reaper
#                     protege pouco. Re-enfileirar vira risco de dois donos pelo mesmo item.
#   · fração alta   → a janela é o caso NORMAL, não a exceção: todo submetido passou por ela e
#                     só chegou ao fim porque a aba ficou aberta. Aí o reaper deixa de ser
#                     higiene e vira correção, e a política (preservar `assigned_to`? por quantas
#                     vezes?) passa a ter base.
#
# PREVISÕES, escritas ANTES de rodar (método § 4 do TODO — previsão errada só ensina se estiver
# registrada). Autor: sessão 2026-08-04.
#
#   P1  Lente A devolve `scanned = 0` → INCONCLUSIVA por ausência de amostra. Razão: a última
#       atividade é de 2026-08-03 e a janela do ledger é 25 h.
#   P2  Lente B acha entre 12 e 25 pendências fechadas no período de 30 d (a fatia 2 mediu 9 em
#       2026-07-30, e a validação da Camada F somou contatos no mesmo dia).
#   P3  `avg_fill_ms` < 180 000 ms — porque quase toda a amostra do demo é smoke roteirizado, que
#       submete em segundos. Se P3 acertar, o veredicto correto NÃO é "a janela não morde": é
#       que **este ambiente não tem amostra humana para responder**, e a política não deve ser
#       decidida por ele.
#
# Uso:  bash infra/test/probe_invisibility_window.sh [tenant] [dias]
# Pré:  mcp-server-plughub (3100), clickhouse, config-api (3600). analytics-api (3500) é opcional
#       — sem ela a conferência cruzada não roda, e o script DIZ que não rodou.
# Saída: 0 = mediu e concluiu · 1 = mediu e o número pede ação · 2 = INCONCLUSIVO (não é zero).

set -uo pipefail
# Credencial (2026-08-27): o `pool_auth` deixou de devolver irrestrito na ausencia
# de header. O shim anexa o Bearer SO nas chamadas a analytics-api. Ver _auth.sh.
source "$(dirname "$0")/_auth.sh"; plughub_auth_curl_shim

DC="docker compose -f docker-compose.demo.yml"
TENANT="${1:-tenant_demo}"
DAYS="${2:-30}"
DB="plughub_demo"

MCP="http://localhost:3100"
CFG="http://localhost:3600"
ANA="http://localhost:3500"

chq() { $DC exec -T clickhouse clickhouse-client -d "$DB" --query "$1" < /dev/null 2>&1; }
num() { tr -d '\r\n[:space:]' <<<"${1:-}"; }

echo "══ probe: janela de invisibilidade (lacuna 2b) — tenant=$TENANT, $DAYS d ══"
echo

# ── Pré-condições. Falha aqui é INCONCLUSIVO e o teste PARA (método § 6): portão que
#    julga o alvo quando quem quebrou foi a montagem manda alguém consertar código correto.
FAIL_PRE=""
curl -sf -m 5 "$MCP/api/work_queue/pending?tenant_id=$TENANT&max_keys=1" >/dev/null 2>&1 \
  || FAIL_PRE="$FAIL_PRE mcp-server(3100)"
chq 'SELECT 1' >/dev/null 2>&1 || FAIL_PRE="$FAIL_PRE clickhouse"
if [ -n "$FAIL_PRE" ]; then
  echo "⚠️  INCONCLUSIVO — pré-condição falhou:$FAIL_PRE"
  echo "   Isto NÃO é 'zero órfãos'. O probe não mediu nada."
  exit 2
fi

# ── A alavanca tem de ser a fonte que o CÓDIGO lê (método § 7). O routing-engine resolve
#    `claim_lease_s` pelo cache do config-api, não pelo default do módulo — então é o
#    config-api que se pergunta. Sem resposta, o valor fica DECLARADO como não medido.
LEASE_S=""
LEASE_SRC="config-api"
CFG_BODY=$(curl -sf -m 5 "$CFG/config/routing?tenant_id=$TENANT" 2>/dev/null)
if [ -n "$CFG_BODY" ]; then
  LEASE_S=$(jq -r '(.claim_lease_s // .config.claim_lease_s // .data.claim_lease_s // empty)' <<<"$CFG_BODY" 2>/dev/null)
fi
if [ -z "$(num "$LEASE_S")" ]; then
  LEASE_S=180
  LEASE_SRC="DEFAULT DO CÓDIGO (config-api não respondeu a chave) — não medido"
fi
LEASE_MS=$(( LEASE_S * 1000 ))
echo "── régua ──────────────────────────────────────────────────────────────────"
echo "   claim_lease_s = ${LEASE_S}s  (${LEASE_MS} ms)   fonte: $LEASE_SRC"
echo

# ══ LENTE A — agora (ledger, janela ~25 h, NÃO acumula) ════════════════════════
echo "── LENTE A · pendências AGORA, por estado ─────────────────────────────────"
PEND=$(curl -sf -m 10 "$MCP/api/work_queue/pending?tenant_id=$TENANT&all=1&max_keys=20000" 2>/dev/null)
if [ -z "$PEND" ]; then
  echo "   ⚠️  INCONCLUSIVA: o endpoint não respondeu."
  A_STATE="inconclusive"; A_ORPH=0
else
  SCANNED=$(num "$(jq -r '.scanned // 0' <<<"$PEND")")
  TRUNC=$(jq -r '.truncated // false' <<<"$PEND")
  A_ORPH=$(num "$(jq -r '[.items[]? | select(.state=="orphaned")] | length' <<<"$PEND")")
  echo "   chaves de ledger varridas: ${SCANNED}    truncado: ${TRUNC}"
  jq -r '.items // [] | group_by(.state) | .[] | "   \(.[0].state):\t\(length)"' <<<"$PEND" 2>/dev/null
  if [ "${SCANNED:-0}" -eq 0 ] 2>/dev/null; then
    echo
    echo "   ⚠️  INCONCLUSIVA — ZERO chaves no ledger. Isto é AUSÊNCIA DE AMOSTRA, não"
    echo "       ausência de órfãos: a janela é de ~25 h e nada acumula nela. Um ambiente"
    echo "       ocioso mede zero aqui para sempre, e o zero passaria por medição."
    A_STATE="no_sample"
  else
    A_STATE="measured"
    [ "$TRUNC" = "true" ] && echo "   ⚠️  resultado PARCIAL (bateu o teto do SCAN) — o total não é o total."
  fi
fi
echo

# ══ LENTE B — histórico (segments, acumula) ════════════════════════════════════
# Predicado COPIADO de `_fetch_wrapup_summary` (reports_query.py) de propósito: um predicado
# "equivalente" reescrito aqui daria um número plausível e diferente, que é o pior resultado
# possível. A conferência cruzada abaixo existe para provar que a cópia não derivou.
WRAPUP_WHERE="tenant_id = '$TENANT'
  AND started_at >= now() - INTERVAL $DAYS DAY
  AND agent_type = 'human'
  AND endsWith(pool_id, '-int')
  AND close_reason IN ('task_submitted','acw_expired','acw_supervisor_closed')
  AND origin = 'live'"

echo "── LENTE B · desfecho das pendências fechadas (últimos $DAYS d) ────────────"
chq "
  SELECT close_reason,
         count()                       AS n,
         round(avg(duration_ms))       AS avg_ms,
         round(median(duration_ms))    AS p50_ms,
         round(quantile(0.9)(duration_ms)) AS p90_ms,
         max(duration_ms)              AS max_ms
    FROM $DB.segments FINAL
   WHERE $WRAPUP_WHERE
   GROUP BY close_reason
   ORDER BY n DESC
   FORMAT PrettyCompactMonoBlock"

B_TOTAL=$(num "$(chq "SELECT count() FROM $DB.segments FINAL WHERE $WRAPUP_WHERE;")")

if [ "${B_TOTAL:-0}" -eq 0 ] 2>/dev/null; then
  echo
  echo "   ⚠️  INCONCLUSIVA — nenhuma pendência author-bound fechada em $DAYS d."
  echo "       Sem amostra histórica não há política a decidir: o reaper protegeria um"
  echo "       fluxo que este ambiente não exercita."
  echo
  echo "══ VEREDICTO: INCONCLUSIVO nas DUAS lentes ═════════════════════════════════"
  exit 2
fi

# ── Conferência cruzada: o predicado copiado tem de bater com o da API que a UI usa.
#    Divergir = a cópia derivou, e todo o resto desta saída é sobre outra população.
XCHECK="não conferido (analytics-api não respondeu)"
API_TOTAL=$(curl -sf -m 10 "$ANA/reports/wrapup-summary?tenant_id=$TENANT" 2>/dev/null \
            | jq -r '.totals.total // empty' 2>/dev/null)
if [ -n "$(num "$API_TOTAL")" ]; then
  # A janela default da API pode diferir da deste probe; só se compara o que é comparável.
  XCHECK="API=$API_TOTAL × probe=$B_TOTAL (janelas podem diferir — compare a ORDEM, não a igualdade)"
fi

# ── O número que decide: submetidos que levaram MAIS que a lease.
SUB_TOTAL=$(num "$(chq "SELECT count() FROM $DB.segments FINAL WHERE $WRAPUP_WHERE AND close_reason='task_submitted' AND duration_ms > 0;")")
SUB_OVER=$(num "$(chq "SELECT count() FROM $DB.segments FINAL WHERE $WRAPUP_WHERE AND close_reason='task_submitted' AND duration_ms > $LEASE_MS;")")
EXPIRED=$(num "$(chq "SELECT count() FROM $DB.segments FINAL WHERE $WRAPUP_WHERE AND close_reason='acw_expired';")")

echo
echo "── leitura ────────────────────────────────────────────────────────────────"
echo "   conferência do predicado: $XCHECK"
echo "   pendências fechadas no período ......... ${B_TOTAL}"
echo "   submetidas com duração > 0 ............. ${SUB_TOTAL:-0}"
echo "   …destas, acima da lease (${LEASE_S}s) ....... ${SUB_OVER:-0}"
echo "   expiradas sem entrega (acw_expired) .... ${EXPIRED:-0}"
echo "   órfãs AGORA (lente A) .................. ${A_ORPH:-0}   [$A_STATE]"
echo

if [ "${SUB_TOTAL:-0}" -eq 0 ] 2>/dev/null; then
  echo "   ⚠️  Nenhuma submissão com duração medível: só há expiradas. A régua"
  echo "       claim→submit não existe nesta amostra."
  echo
  echo "══ VEREDICTO: INCONCLUSIVO para a política ════════════════════════════════"
  exit 2
fi

PCT=$(( SUB_OVER * 100 / SUB_TOTAL ))
echo "   ► fração dos submetidos que atravessou a janela: ${PCT}%  (${SUB_OVER}/${SUB_TOTAL})"
echo

if [ "$PCT" -ge 30 ]; then
  echo "══ VEREDICTO: a janela MORDE — ${PCT}% dos submetidos passaram por ela ══════"
  echo "   Cada um desses só chegou ao fim porque a aba continuou aberta: o item já"
  echo "   estava fora do ZSET e sem lease. A janela não é borda, é o caminho comum."
  echo "   A política do reaper passa a ter base — e a pergunta de desenho vira 'o"
  echo "   re-enfileiramento preserva o assigned_to por quantas rodadas', não 'se'."
  exit 1
fi

echo "══ VEREDICTO: a janela NÃO MORDE NESTA AMOSTRA — ${PCT}% ═══════════════════"
echo "   ⚠️  Leia com o cuidado que o número merece: amostra de ambiente de demo é"
echo "       dominada por smoke roteirizado, que submete em segundos. '${PCT}%' aqui"
echo "       diz que ESTE ambiente não exercita o caso humano — não que o caso humano"
echo "       não exista. Decidir 'não construir o reaper' com base nisto seria a mesma"
echo "       falácia de tratar ausência de amostra como ausência de fenômeno."
echo "   O corte honesto: separar as durações dos wrap-ups de atendimento REAL (os de"
echo "   2026-07-27 em diante, feitos à mão) das dos smokes, e olhar só os primeiros."
exit 0
