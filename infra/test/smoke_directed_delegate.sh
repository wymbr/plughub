#!/usr/bin/env bash
# Camada E2 (keystone) — `assigned_to` no step `delegate` (pull direcionado do wrap-up).
#
# Prova o caminho RUNTIME produtor: um delegate com `assigned_to` → o inbound do
# especialista (handle_delegate_conference) carrega o campo → o routing parqueia no
# pool pull → o `contact_data` em {t}:queue_contact:{sid} carrega `assigned_to` +
# `fallback_to_pool_after_s` (declarados no ConversationInboundEvent, senão model_dump
# descarta). O consumidor (work_task_claim, Camada B) já os honra — coberto por
# smoke_directed_pull.sh.
#
# Exercita direto o `delegate-conference` do channel-gateway (o mesmo endpoint que o
# skill-flow-service chama no persistDelegate) — sem depender de construir o workflow
# de wrap-up ainda. As camadas TS (schema→executor→forwarder) são type-checked no build
# e serão exercitadas E2E quando o workflow de wrap-up existir (próxima fatia).
#
# Uso (raiz do repo, demo no ar):  bash infra/test/smoke_directed_delegate.sh
set -euo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
CG="http://localhost:8010"          # channel-gateway (publicado)
POOL="formfill_demo"                 # pool pull do demo do R0 (dispatch_mode: pull)
PARENT="sess_dd_$$"                  # sessão pai sintética (o conference parqueia por ela)
USER="ddUserA"
FALLBACK=5

R() { $COMPOSE exec -T redis redis-cli "$@"; }

echo "1) delegate-conference com assigned_to=$USER (pool pull $POOL) ..."
HTTP=$(curl -s -o /tmp/_dd_body -w '%{http_code}' -X POST \
  "$CG/v1/channels/webhook/delegate-conference" \
  -H 'content-type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"pool_id\":\"$POOL\",\"session_id\":\"$PARENT\",\"customer_id\":\"$PARENT\",\"resume_token\":\"tok_$$\",\"step_id\":\"coletar\",\"context\":{\"dialog_form_id\":\"dialog_formfill_demo\"},\"timeout_hours\":1,\"assigned_to\":\"$USER\",\"fallback_to_pool_after_s\":$FALLBACK}")
echo "   HTTP $HTTP"; cat /tmp/_dd_body; echo
[ "$HTTP" = "201" ] || { echo "FALHA: delegate-conference (HTTP $HTTP)"; exit 1; }

echo "2) Aguardando o routing consumir o inbound e parquear ..."
sleep 4

echo "3) contact_data em {t}:queue_contact:$PARENT (esperado: assigned_to=$USER, fallback=$FALLBACK):"
QC=$(R GET "${TENANT}:queue_contact:${PARENT}" | tr -d '\r')
echo "   $QC"

PASS=0; FAIL=0
chk() { if echo "$QC" | grep -q "$2"; then echo "  PASS — $1"; PASS=$((PASS+1)); else echo "  FAIL — $1"; FAIL=$((FAIL+1)); fi; }
chk "queue_contact carrega assigned_to"              "\"assigned_to\": *\"$USER\""
chk "queue_contact carrega fallback_to_pool_after_s" "\"fallback_to_pool_after_s\": *$FALLBACK"
# assigned_at_ms é auto-carimbado pelo add_queued_contact (Camada B) no 1º enqueue.
chk "queue_contact carrega assigned_at_ms (auto)"    "\"assigned_at_ms\":"

echo "4) Limpeza ..."
R DEL "${TENANT}:queue_contact:${PARENT}" "${TENANT}:pool:${POOL}:queue" >/dev/null 2>&1 || true
R ZREM "${TENANT}:pool:${POOL}:queue" "$PARENT" >/dev/null 2>&1 || true

echo
echo "======================================"
echo "  PASS=$PASS  FAIL=$FAIL"
if [ "$FAIL" = 0 ]; then
  echo "  ✅ SMOKE OK"
else
  echo "  ⚠️  Se FAIL: confira se $POOL está dispatch_mode:pull e se o routing consumiu"
  echo "      (grep 'add_queued_contact'/queue no log do routing-engine)."
  exit 1
fi
