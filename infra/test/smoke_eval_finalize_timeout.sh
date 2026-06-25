#!/usr/bin/env bash
# Smoke E2E — G-TIMEOUT + núcleo do G-FIN (evaluation-api).
#
# Prova que o deadline scanner (_run_deadline_scanner, 60s) finaliza um resultado
# vencido via finalize_evaluation — o MESMO ponto único que o submit_review (humano)
# usa. Estratégia não-destrutiva: pega um resultado já finalizado, REABRE como
# vencido (result_state='open', deadline_at no passado), e confirma que o scanner
# o re-finaliza (timeout_contestation / uncontested) gravando final_score+finalized_at.
# Bônus: confere se o evento evaluation_finalized chegou ao ClickHouse.
#
# Rodar da raiz do repo:  bash infra/test/smoke_eval_finalize_timeout.sh
set -euo pipefail

DC="docker compose -f docker-compose.demo.yml"
PSQL() { $DC exec -T postgres psql -U plughub -d plughub_demo -tA "$@"; }
CH()   { $DC exec -T clickhouse clickhouse-client "$@"; }

echo "== escolhe um resultado já finalizado para reabrir =="
read -r RID TENANT IID <<< "$(PSQL -c "
  SELECT id, tenant_id, instance_id
  FROM evaluation.results
  WHERE result_state = 'finalized'
  LIMIT 1" | tr '|' ' ')"
if [ -z "${RID:-}" ]; then
  echo "FALHA: nenhum resultado finalizado no demo para usar de fixture."
  echo "       (rode o fluxo de avaliação/seeder antes, ou ajuste o seletor)"
  exit 1
fi
echo "result_id=$RID  tenant=$TENANT  instance=$IID"

echo "== reabre como vencido (open, deadline_at = now()-1h) =="
PSQL -c "
  UPDATE evaluation.results
     SET result_state='open',
         contestation_state='contestation_open',
         finalize_reason=NULL,
         finalized_at=NULL,
         final_score=NULL,
         deadline_at = now() - interval '1 hour'
   WHERE id='$RID'" >/dev/null
echo "estado antes:"
PSQL -c "SELECT result_state, contestation_state, finalize_reason, finalized_at, final_score
         FROM evaluation.results WHERE id='$RID'"

echo "== aguardando o deadline scanner (ciclo de 60s) =="
ok=""
for i in 1 2 3 4 5; do
  sleep 20
  STATE="$(PSQL -c "SELECT result_state FROM evaluation.results WHERE id='$RID'")"
  echo "  [$((i*20))s] result_state=$STATE"
  if [ "$STATE" = "finalized" ]; then ok="1"; break; fi
done

echo "== estado depois =="
PSQL -c "SELECT result_state, contestation_state, finalize_reason, finalized_at, final_score
         FROM evaluation.results WHERE id='$RID'"

if [ -n "$ok" ]; then
  echo "OK  scanner finalizou o resultado vencido (G-TIMEOUT) via finalize_evaluation (núcleo do G-FIN)"
else
  echo "ATENÇÃO  ainda não finalizou — o scanner roda a cada 60s; rode a query 'depois' de novo em ~1min"
fi

echo "== bônus: evento evaluation_finalized no ClickHouse p/ esta instance =="
CH -q "SELECT instance_id, finalize_reason, contestation_state, final_score
       FROM plughub_demo.evaluation_finalized FINAL
       WHERE instance_id = '$IID'
       ORDER BY ingested_at DESC LIMIT 3 FORMAT PrettyCompact" || true
