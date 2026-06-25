#!/usr/bin/env bash
# Smoke E2E — Quality substrate isolation (ADR adr-quality-substrate-isolation).
#
# Re-emite UMA sessão LIVE como REEVAL pela porta de reavaliação
# (quality-export → quality-ingest → analytics-api consumer) e valida que:
#   (1) surgem linhas origin='reeval' no ClickHouse (procedência derivada do source);
#   (2) a produção (origin='live') NÃO é contaminada — contagens live estáveis.
#
# Pré-requisito: stack demo no ar (analytics-api, quality-export, quality-ingest,
# clickhouse, kafka). Rodar da raiz do repo:  bash infra/test/smoke_origin_reeval.sh
set -euo pipefail

DC="docker compose -f docker-compose.demo.yml"
CH() { $DC exec -T clickhouse clickhouse-client "$@"; }

# FINAL: o origin não está na ORDER BY key do ReplacingMergeTree; reavaliar a mesma
# sessão reescreve a linha (import→reeval) e só FINAL colapsa para o estado correto.
dist() {
  CH -q "SELECT t, origin, c FROM (
           SELECT 'sessions' t, origin, count() c FROM plughub_demo.sessions FINAL GROUP BY origin
           UNION ALL SELECT 'segments', origin, count() FROM plughub_demo.segments FINAL GROUP BY origin
           UNION ALL SELECT 'messages', origin, count() FROM plughub_demo.messages FINAL GROUP BY origin
         ) ORDER BY t, origin FORMAT PrettyCompact"
}

echo "== baseline: distribuição de origin =="
dist

# Escolhe um tenant+session LIVE que tenha segmentos (p/ a reavaliação gerar linhas).
read -r TENANT SID < <(CH -q "
  SELECT s.tenant_id, s.session_id
  FROM plughub_demo.sessions s
  WHERE s.origin = 'live'
    AND s.session_id IN (SELECT session_id FROM plughub_demo.segments WHERE origin = 'live')
  LIMIT 1 FORMAT TabSeparated")
if [ -z "${SID:-}" ]; then echo "FALHA: nenhuma sessão live elegível"; exit 1; fi
echo "alvo: tenant=$TENANT  session=$SID"

LIVE_BEFORE=$(CH -q "SELECT count() FROM plughub_demo.sessions FINAL WHERE origin='live'")
REEVAL_BEFORE=$(CH -q "SELECT count() FROM plughub_demo.sessions FINAL WHERE origin='reeval'")

echo "== dispara reavaliação (POST quality-export /v1/export/sessions) =="
$DC exec -T quality-export python3 - "$TENANT" "$SID" <<'PY'
import json, sys, urllib.request
tenant, sid = sys.argv[1], sys.argv[2]
body = json.dumps({"tenant_id": tenant, "session_ids": [sid]}).encode()
req = urllib.request.Request(
    "http://localhost:3852/v1/export/sessions",
    data=body,
    headers={"Content-Type": "application/json", "X-Tenant-ID": tenant},
)
print(urllib.request.urlopen(req, timeout=20).read().decode())
PY

echo "== aguardando pipeline (Kafka → analytics consumer) =="
sleep 10

echo "== depois: distribuição de origin =="
dist

LIVE_AFTER=$(CH -q "SELECT count() FROM plughub_demo.sessions FINAL WHERE origin='live'")
REEVAL_AFTER=$(CH -q "SELECT count() FROM plughub_demo.sessions FINAL WHERE origin='reeval'")

echo
echo "live:   $LIVE_BEFORE -> $LIVE_AFTER"
echo "reeval: $REEVAL_BEFORE -> $REEVAL_AFTER"
RC=0
if [ "$LIVE_AFTER" -eq "$LIVE_BEFORE" ]; then
  echo "OK  produção (live) estável — sem contaminação"
else
  echo "ATENÇÃO  contagem live mudou (esperado: estável)"; RC=1
fi
if [ "$REEVAL_AFTER" -gt "$REEVAL_BEFORE" ]; then
  echo "OK  reeval surgiu isolado no substrato"
else
  echo "ATENÇÃO  reeval não apareceu (consumer ainda processando? rode a query 'depois' de novo)"; RC=1
fi
exit $RC
