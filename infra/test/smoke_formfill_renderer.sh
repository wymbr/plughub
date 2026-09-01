#!/usr/bin/env bash
#
# smoke_formfill_renderer.sh — Renderer GENÉRICO de collect-form no Console (R0,
# kickoff docs/product/approval-renderer-kickoff.md).
#
# Prova, no backend, que um `delegate` SEM `decisions[]` a um pool pull:
#   (1) enfileira um item no pool pull (aparece em /api/work_queue/list);
#   (2) a sessão-filha carrega no ContextStore tudo que o <DialogFormRenderer>
#       genérico lê: `session.dialog_form_id` + `session.briefing_session_id` +
#       um resume token (delegate/workflow/collect).
# O ciclo completo claim→render→resume é verificado na UI (Console) — este smoke
# garante o CONTRATO que a UI consome.
#
# Pré-requisitos: demo no ar; pools formfill_demo + formfill_demo_ia registrados
# (reconcile do registry após adicionar em infra/registry/tenant_demo.yaml); skill
# skill_formfill_demo_v1 deployado. Requer: curl, jq.
#
# Uso (da raiz do repo):
#   bash infra/test/smoke_formfill_renderer.sh
#   BRIEFING_SID=<session_id real> bash infra/test/smoke_formfill_renderer.sh   # p/ ver transcrição na UI
set -euo pipefail

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
BRIEFING_SID="${BRIEFING_SID:-sess_briefing_demo}"

echo "0) DialogForm '$FORM' publicado?"
if curl -fsS "$DIALOG/v1/dialog/forms/$FORM?status=published" -H "X-Tenant-ID: $TENANT" >/dev/null 2>&1; then
  echo "  ✓ publicado"
else
  echo "  → seedando via infra/test/seed_dialog_formfill_demo_form.sh"
  DIALOG_API="$DIALOG" TENANT="$TENANT" bash infra/test/seed_dialog_formfill_demo_form.sh
fi

echo "1) Disparando o workflow (delega o form ao pool pull, briefing=$BRIEFING_SID) ..."
RESP=$(curl -fsS -X POST "$CG/v1/channels/webhook/pool/$POOL_WH" \
  -H 'content-type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"context\":{\"session.briefing_session_id\":\"$BRIEFING_SID\"}}")
echo "  → $RESP"
# O delegate-conference parqueia a PRÓPRIA sessão do workflow (o humano entra na
# conferência no claim), logo o item pull = este session_id. Buscamos ESTE item
# (não contacts[0], que seria o mais antigo de runs anteriores — valor plausível
# que esconderia se este trigger de fato enfileirou).
SID=$(echo "$RESP" | jq -r '.session_id // empty')
if [ -z "$SID" ]; then echo "  ❌ trigger não retornou session_id"; exit 1; fi

echo "2) Aguardando o item '$SID' parquear no pool pull ($POOL_PULL) ..."
CHILD=""
for _ in $(seq 1 15); do
  LIST=$(curl -fsS "$UI/api/work_queue/list?pools=$POOL_PULL" 2>/dev/null || true)
  CHILD=$(echo "$LIST" | jq -r --arg sid "$SID" '.contacts[] | select(.session_id==$sid) | .session_id' 2>/dev/null || true)
  [ -n "$CHILD" ] && break
  sleep 1
done
if [ -z "$CHILD" ]; then
  echo "  ❌ o item '$SID' não apareceu na fila pull. Verifique: pools registrados"
  echo "     (reconcile) e skill deployado. Logs: $COMPOSE logs routing-engine orchestrator-bridge --tail=50"
  exit 1
fi
echo "  ✓ item na fila: $CHILD"

echo "3) Conferindo o ctx da sessão-filha (o que o renderer lê) ..."
CTX=$($COMPOSE exec -T redis redis-cli HGETALL "${TENANT}:ctx:${CHILD}")
FAIL=0
have() {
  if echo "$CTX" | grep -q "session.$1"; then echo "  ✓ session.$1"; else echo "  ❌ falta session.$1"; FAIL=1; fi
}
have dialog_form_id
have briefing_session_id
if echo "$CTX" | grep -qE "session\.(delegate|workflow|collect)_resume_token"; then
  echo "  ✓ resume token"
else
  echo "  ❌ falta resume token"; FAIL=1
fi

echo
echo "======================================"
if [ "$FAIL" = 0 ]; then
  echo "  ✅ SMOKE OK — item pull carrega form + briefing + resume token."
  echo "  UI: ative o pool '$POOL_PULL' no Console, reivindique '$CHILD',"
  echo "      veja o form genérico + briefing e submeta (payload.answers → workflow)."
else
  echo "  ❌ SMOKE FALHOU"; exit 1
fi
