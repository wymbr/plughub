#!/usr/bin/env bash
# probe_console_restore_after_reload.sh — o dono recupera o próprio item reivindicado?
#
# A PERGUNTA (cenário 2 da lacuna 2b). Depois do claim o item sai do ZSET, logo some da inbox.
# A partir daí o único lugar onde ele existe para o agente é o Console, cujo mapa de contatos
# nasce VAZIO a cada mount e é alimentado por `conversation.assigned` — que o bridge publica em
# **pub/sub** (`pool:events:{pool}`), sem histórico nem replay. Se for isso mesmo, um F5 apaga o
# trabalho da tela e a invisibilidade começa NO CLAIM, não aos 180 s da lease — e a lacuna 2b
# está descrita no lugar errado.
#
# HIPÓTESE (escrita como hipótese, método § 2): o Console NÃO restaura, nem antes nem depois da
# lease vencer. Se ela cair, sobra só a mentira de classificação do cenário 1, que é bem mais
# barata que um reaper.
#
# DUAS OBSERVAÇÕES, e é o par que decide — uma só não separa as três explicações:
#
#   A) F5 com a lease VIVA    (t < claim_lease_s)
#   B) F5 com a lease VENCIDA (t > claim_lease_s)
#
#   A=sim, B=sim  → hipótese REFUTADA. O retorno não depende da lease; resta o cenário 1.
#   A=sim, B=não  → a lease É o que sustenta o retorno. A janela 180 s→prazo é real como o TODO
#                   a descreve, e esticar o TTL da lease conserta o cenário 2 também.
#   A=não, B=não  → a invisibilidade começa no CLAIM. A lease não tem nada a ver com isso, e o
#                   alvo passa a ser a inbox do próprio dono (ledger `assigned_to`), não o reaper.
#   A=não, B=sim  → incoerente. NÃO inventar explicação: refazer o probe.
#
# O QUE ESTE PROBE NÃO FAZ: julgar o Console quando a montagem falhou. Item que não enfileira ou
# claim que não aconteceu ⇒ INCONCLUSIVO e o teste PARA (método § 6) — portão que aponta o lugar
# errado manda alguém consertar código correto.
#
# LIMITE DECLARADO DA AMOSTRA: usa o pool `formfill_demo`, que é pull mas NÃO é `-int` nem
# author-bound (`assigned_to` virá vazio, e aqui isso é normal, não anomalia). O caminho exercitado
# — mapa de contatos ← pub/sub, item fora do ZSET, renderer lendo o ctx — é o MESMO do wrap-up.
# O que ele não cobre é a reserva ao dono; essa é a Camada B e tem smoke próprio.
#
# Uso (da raiz do repo):  bash infra/test/probe_console_restore_after_reload.sh
# Requer: curl, jq, e VOCÊ no Console (é uma observação humana — não há como automatizar o F5 aqui).

set -uo pipefail

# CAP-12 (2026-09-01): as rotas `/api/*` do mcp-server exigem credencial. Sem esta
# linha as chamadas abaixo voltam 401, e o script contaria zero item como se a fila
# estivesse vazia. O shim anexa o Bearer so onde ele e conferido — ver _auth.sh.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_auth.sh"; plughub_auth_curl_shim

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
CG="http://localhost:8010"
DIALOG="http://localhost:3760"
UI="http://localhost:5174"
POOL_WH="formfill_demo_ia"
POOL_PULL="formfill_demo"
FORM="dialog_formfill_demo"

# Todo `docker exec` leva `< /dev/null`: sem isso ele CONSOME o stdin do script e o `read` das
# observações abaixo volta vazio sem erro nenhum — o probe terminaria sozinho parecendo concluído.
rcli() { $COMPOSE exec -T redis redis-cli "$@" < /dev/null 2>/dev/null; }

echo "══ probe: o dono recupera o item reivindicado depois de um F5? ══"
echo

# ── 0. Pré-condições ────────────────────────────────────────────────────────────
PRE=""
curl -fsS -m 5 "$UI/api/work_queue/list?pools=$POOL_PULL" >/dev/null 2>&1 || PRE="$PRE platform-ui(5174)"
rcli PING | grep -q PONG || PRE="$PRE redis"
curl -fsS -m 5 "$CG/health" >/dev/null 2>&1 || curl -fsS -m 5 "$CG/" >/dev/null 2>&1 || PRE="$PRE channel-gateway(8010)"
if [ -n "$PRE" ]; then
  echo "⚠️  INCONCLUSIVO — pré-condição falhou:$PRE"
  echo "   Nada foi observado sobre o Console."
  exit 2
fi

if ! curl -fsS -m 5 "$DIALOG/v1/dialog/forms/$FORM?status=published" -H "X-Tenant-ID: $TENANT" >/dev/null 2>&1; then
  echo "→ form '$FORM' não publicado; seedando ..."
  DIALOG_API="$DIALOG" TENANT="$TENANT" bash infra/test/seed_dialog_formfill_demo_form.sh || {
    echo "⚠️  INCONCLUSIVO — seed do form falhou."; exit 2; }
fi

# A régua vem da fonte que o CÓDIGO lê (config-api), não do default do módulo.
CFG_URL="${CFG_URL:-http://localhost:3600}"
LEASE_S=$(curl -fsS -m 5 "$CFG_URL/config/routing?tenant_id=$TENANT" 2>/dev/null \
          | jq -r '(.claim_lease_s // .config.claim_lease_s // .data.claim_lease_s // empty)' 2>/dev/null)
case "$LEASE_S" in ''|*[!0-9]*) LEASE_S=180; LEASE_SRC="default do código (config-api não deu a chave)";; *) LEASE_SRC="config-api";; esac
echo "   claim_lease_s = ${LEASE_S}s   (fonte: $LEASE_SRC)"
echo

# ── 1. Cria o item ──────────────────────────────────────────────────────────────
echo "1) disparando o workflow que delega o form ao pool pull ..."
RESP=$(curl -fsS -m 15 -X POST "$CG/v1/channels/webhook/pool/$POOL_WH" \
  -H 'content-type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"context\":{\"session.briefing_session_id\":\"sess_briefing_demo\"}}" 2>/dev/null)
SID=$(jq -r '.session_id // empty' <<<"$RESP" 2>/dev/null)
if [ -z "$SID" ]; then
  echo "⚠️  INCONCLUSIVO — o trigger não devolveu session_id. Resposta: ${RESP:-<vazia>}"
  exit 2
fi
echo "   session_id = $SID"

echo "2) esperando o item parquear na fila pull ..."
FOUND=""
for _ in $(seq 1 15); do
  FOUND=$(curl -fsS -m 5 "$UI/api/work_queue/list?pools=$POOL_PULL" 2>/dev/null \
          | jq -r --arg s "$SID" '.contacts[]? | select(.session_id==$s) | .session_id' 2>/dev/null)
  [ -n "$FOUND" ] && break
  sleep 1
done
if [ -z "$FOUND" ]; then
  echo "⚠️  INCONCLUSIVO — o item não chegou à fila. Isto NÃO diz nada sobre o Console."
  echo "   Logs: $COMPOSE logs routing-engine orchestrator-bridge --tail=50"
  exit 2
fi
echo "   ✓ na fila"
echo

# ── 2. Claim (manual, no Console) ───────────────────────────────────────────────
cat <<EOF
────────────────────────────────────────────────────────────────────────────────
  AGORA NO CONSOLE ($UI/agent-assist):
    1. ative o pool  '$POOL_PULL'  no seletor de presença;
    2. na inbox pull, reivindique o item  $SID  (botão "Pull");
    3. confirme que o FORMULÁRIO aparece na tela.
────────────────────────────────────────────────────────────────────────────────
EOF
read -r -p "  ENTER quando o formulário estiver na tela (ou Ctrl-C para abortar) ... " _

IN_Z=$(rcli ZSCORE "${TENANT}:pool:${POOL_PULL}:queue" "$SID")
LEASE=$(rcli GET "${TENANT}:pool:${POOL_PULL}:claim:${SID}")
if [ -n "$IN_Z" ]; then
  echo
  echo "⚠️  INCONCLUSIVO — o item AINDA está no ZSET: o claim não aconteceu."
  echo "   O probe para aqui em vez de julgar o F5 sobre um estado que não é o do teste."
  exit 2
fi
echo "   ✓ claim confirmado no backend (fora do ZSET; lease: $([ -n "$LEASE" ] && echo presente || echo AUSENTE))"
echo

# ── 3. OBSERVAÇÃO A — F5 com a lease viva ───────────────────────────────────────
TTL=$(rcli TTL "${TENANT}:pool:${POOL_PULL}:claim:${SID}")
echo "── OBSERVAÇÃO A · lease viva (restam ~${TTL}s) ─────────────────────────────"
echo "   Dê F5 no Console AGORA (sem re-reivindicar nada)."
read -r -p "   O formulário voltou sozinho? [s/n] " OBS_A
OBS_A=$(tr '[:upper:]' '[:lower:]' <<<"${OBS_A:-}")

# ── 4. OBSERVAÇÃO B — F5 com a lease vencida ────────────────────────────────────
echo
echo "── esperando a lease vencer (nada a fazer) ─────────────────────────────────"
for _ in $(seq 1 $(( LEASE_S + 30 )) ); do
  [ -z "$(rcli GET "${TENANT}:pool:${POOL_PULL}:claim:${SID}")" ] && break
  sleep 1
done
if [ -n "$(rcli GET "${TENANT}:pool:${POOL_PULL}:claim:${SID}")" ]; then
  echo "⚠️  INCONCLUSIVO — a lease não venceu no prazo esperado; observação B não é comparável."
  exit 2
fi
echo "   ✓ lease vencida"

# Corrobora o cenário 1: como o relatório classifica ESTE item, que está sendo trabalhado.
CLS=$(curl -fsS -m 10 "$UI/api/work_queue/pending?tenant_id=$TENANT&all=1&max_keys=20000" 2>/dev/null \
      | jq -r --arg s "$SID" '.items[]? | select(.session_id==$s or .queue_session_id==$s) | .state' 2>/dev/null)
echo "   Monitor › Pendências classifica este item como: ${CLS:-<não encontrado no ledger>}"

echo
echo "── OBSERVAÇÃO B · lease vencida ────────────────────────────────────────────"
echo "   Dê F5 no Console de novo."
read -r -p "   O formulário voltou sozinho? [s/n] " OBS_B
OBS_B=$(tr '[:upper:]' '[:lower:]' <<<"${OBS_B:-}")

# ── 5. Veredicto ────────────────────────────────────────────────────────────────
echo
echo "══ VEREDICTO ═══════════════════════════════════════════════════════════════"
echo "   A (lease viva) = ${OBS_A:-?}    B (lease vencida) = ${OBS_B:-?}    classificação = ${CLS:-?}"
echo
case "${OBS_A}|${OBS_B}" in
  s\|s)
    echo "   HIPÓTESE REFUTADA. O retorno do dono não depende da lease, e o cenário 2"
    echo "   não existe como descrito. Sobra o cenário 1: o relatório chama de '${CLS:-orphaned}'"
    echo "   um item que está sendo trabalhado. Alvo vira a classificação, não o reaper."
    ;;
  s\|n)
    echo "   A LEASE É O QUE SUSTENTA O RETORNO. A janela 180 s→prazo é real como o TODO a"
    echo "   descreve, e o TTL da lease deixa de ser detalhe: esticá-lo até o prazo do item"
    echo "   fecha o cenário 2 junto com a classificação do cenário 1."
    ;;
  n\|n)
    echo "   A INVISIBILIDADE COMEÇA NO CLAIM, não aos ${LEASE_S}s. A lacuna 2b está descrita"
    echo "   no lugar errado: a janela é o prazo INTEIRO do item, e mexer na lease não a"
    echo "   fecha. Alvo = superfície do dono sobre o ledger (que já tem assigned_to e prazo)."
    ;;
  n\|s)
    echo "   INCOERENTE — voltar sem lease e não voltar com ela não tem mecanismo plausível."
    echo "   NÃO explicar: refazer. Suspeitos: F5 dado na aba errada, item já submetido,"
    echo "   ou outro agente logado no mesmo pool."
    ;;
  *)
    echo "   INCONCLUSIVO — resposta não reconhecida (esperado s/n)."
    ;;
esac

echo
echo "── limpeza (opcional; deixar sujo também é dado) ───────────────────────────"
echo "   curl -X POST '$UI/api/work_queue/release/$SID' -H 'content-type: application/json' \\"
echo "        -d '{\"tenant_id\":\"$TENANT\",\"pool_id\":\"$POOL_PULL\",\"instance_id\":\"human-<seu_user_id>\"}'"
