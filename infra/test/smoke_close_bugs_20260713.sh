#!/usr/bin/env bash
# Smoke — bugs de FECHAMENTO de sessão (achados 2026-07-13), "corrigido, a validar".
#
# Valida DOIS fixes numa tacada, disparando UM processo de survey outbound (a
# cadeia do T5: processo N3 → survey outbound N2 [suspende no collect]):
#
#   Bug A — escrita PARCIAL apaga a identidade da sessão.
#     `sessions` é ReplacingMergeTree de LINHA INTEIRA; `session_suspended`
#     escrevia só o status e apagava o pool_id/channel/opened_at. Só ficava
#     visível numa sessão que NÃO fecha (o workflow de survey, suspended até 48h).
#     Fix: bridge repete a identidade no `session_suspended` + analytics-api
#     `_session_identity_cache` reinjeta identidade em todos os tópicos.
#     PASS ⇔ a(s) sessão(ões) SUSPENDED da journey têm pool_id != ''.
#
#   Bug B — hook `side=agent` NUNCA fecha a camada 1.
#     A camada 1 fecha quando `posatt:customer_active` zera, mas esse contador só
#     é incrementado por hooks side=customer. Um pool cujo único hook é
#     `on_process_end` (side=agent, survey outbound) nunca fechava a RAIZ.
#     Fix: guarda do caminho humano espelhada no ramo do AI primary.
#     PASS ⇔ a RAIZ do processo tem status='closed' AND closed_at IS NOT NULL.
#
# (O follow-up #4 — outcome 'suspended' após resume — já está ✅ validado e exige
#  COMPLETAR o survey (clique no link web), então fica fora deste automático.)
#
# PRÉ-REQUISITO (garante o fix no ar):
#   docker compose -f docker-compose.demo.yml build orchestrator-bridge analytics-api
#   docker compose -f docker-compose.demo.yml up -d --force-recreate orchestrator-bridge analytics-api
#
# USO (da raiz do repo, demo no ar):
#   PROCESS_ENDPOINT="pool/<pool_id_do_processo>"  bash infra/test/smoke_close_bugs_20260713.sh
#   # ou, se o processo é endereçado por skill legado:
#   PROCESS_ENDPOINT="<skill_slug>"                bash infra/test/smoke_close_bugs_20260713.sh
#
# PROCESS_ENDPOINT = o segmento após /v1/channels/webhook/ do PROCESSO (N3) que
# dispara o on_process_end (o mesmo que gerou a journey do T5). É DB-owned (pool
# criado pela UI), por isso não é autodetectado aqui.
set -euo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="${TENANT:-tenant_demo}"
CH_DB="${CH_DB:-plughub_demo}"
CG_URL="${CG_URL:-http://localhost:8010}"     # channel-gateway no host
CH="$COMPOSE exec -T clickhouse clickhouse-client"
POLL_MAX="${POLL_MAX:-20}"                     # tentativas
POLL_INT="${POLL_INT:-2}"                      # segundos entre tentativas

if [ -z "${PROCESS_ENDPOINT:-}" ]; then
  echo "ERRO: defina PROCESS_ENDPOINT (ex.: PROCESS_ENDPOINT=\"pool/survey_process_wf\")." >&2
  echo "      É o segmento após /v1/channels/webhook/ do processo de survey outbound." >&2
  exit 2
fi

red()  { printf '\033[31m%s\033[0m\n' "$1"; }
grn()  { printf '\033[32m%s\033[0m\n' "$1"; }

echo "1) Disparando o processo (webhook/$PROCESS_ENDPOINT) ..."
ROOT=$(curl -s -X POST "$CG_URL/v1/channels/webhook/$PROCESS_ENDPOINT" \
  -H 'content-type: application/json' -d "{\"tenant_id\":\"$TENANT\"}" \
  | sed -n 's/.*"session_id":"\([^"]*\)".*/\1/p')
[ -n "$ROOT" ] || { red "FALHA: o trigger não devolveu session_id (endpoint certo? demo no ar?)"; exit 1; }
echo "   RAIZ (processo) = $ROOT"

echo "2) Poll até a RAIZ fechar (bug B) — máx $((POLL_MAX*POLL_INT))s ..."
ROOT_STATUS=""
for i in $(seq 1 "$POLL_MAX"); do
  ROOT_STATUS=$($CH -q "SELECT COALESCE(status,'') FROM ${CH_DB}.sessions FINAL \
                        WHERE tenant_id='$TENANT' AND session_id='$ROOT'" 2>/dev/null || true)
  [ "$ROOT_STATUS" = "closed" ] && break
  sleep "$POLL_INT"
done

echo
echo "3) Sessões-membro da journey (root_session_id = RAIZ):"
$CH -q "SELECT session_id, origin_session_id, spawn_reason, \
               COALESCE(pool_id,'∅') AS pool_id, COALESCE(channel,'∅') AS channel, \
               COALESCE(status,'∅') AS status, COALESCE(outcome,'∅') AS outcome, \
               opened_at, closed_at \
        FROM ${CH_DB}.sessions FINAL \
        WHERE tenant_id='$TENANT' AND root_session_id='$ROOT' \
        ORDER BY opened_at FORMAT PrettyCompact"

# ── Aferições ────────────────────────────────────────────────────────────────
FAIL=0

# Bug B — a RAIZ fecha.
ROOT_CLOSED=$($CH -q "SELECT count() FROM ${CH_DB}.sessions FINAL \
  WHERE tenant_id='$TENANT' AND session_id='$ROOT' \
    AND status='closed' AND closed_at IS NOT NULL")
echo
if [ "$ROOT_CLOSED" = "1" ]; then
  grn "PASS  Bug B — raiz do processo FECHOU (status=closed, closed_at not null)."
else
  red  "FAIL  Bug B — raiz AINDA ABERTA (status='$ROOT_STATUS'). O hook side=agent não fechou a camada 1."
  FAIL=1
fi

# Bug A — nenhuma sessão SUSPENDED da journey com pool_id vazio.
SUSP_TOTAL=$($CH -q "SELECT count() FROM ${CH_DB}.sessions FINAL \
  WHERE tenant_id='$TENANT' AND root_session_id='$ROOT' AND status='suspended'")
SUSP_EMPTY=$($CH -q "SELECT count() FROM ${CH_DB}.sessions FINAL \
  WHERE tenant_id='$TENANT' AND root_session_id='$ROOT' AND status='suspended' \
    AND (pool_id='' OR pool_id IS NULL)")
if [ "$SUSP_TOTAL" = "0" ]; then
  printf '\033[33m%s\033[0m\n' "N/A   Bug A — nenhuma sessão SUSPENDED criada (o processo disparou o survey outbound? endpoint certo?)."
elif [ "$SUSP_EMPTY" = "0" ]; then
  grn "PASS  Bug A — $SUSP_TOTAL sessão(ões) suspensa(s), TODAS com pool_id preenchido (identidade não apagada)."
else
  red  "FAIL  Bug A — $SUSP_EMPTY de $SUSP_TOTAL sessão(ões) suspensa(s) com pool_id VAZIO (escrita parcial apagou a identidade)."
  FAIL=1
fi

# Contexto: open_count da journey (a suspensa do survey pode ficar aberta — legítimo).
OPEN_CNT=$($CH -q "SELECT countIf(COALESCE(status,'')!='closed') FROM ${CH_DB}.sessions FINAL \
  WHERE tenant_id='$TENANT' AND root_session_id='$ROOT'")
echo "      (contexto) sessões abertas na journey: $OPEN_CNT — o survey suspenso pode ser 1 legítimo."

echo
if [ "$FAIL" = "0" ]; then
  grn "==== SMOKE OK — bugs A e B validados ===="
else
  red "==== SMOKE FALHOU — ver acima ===="
  exit 1
fi
