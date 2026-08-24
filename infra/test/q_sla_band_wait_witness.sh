#!/usr/bin/env bash
# Testemunha da D14.1 — onde contatos REALMENTE esperaram, por pool.
#
# Pergunta: os 4 sites de ROTEAMENTO que consomem `sla_target_ms` como alvo de
# espera (`scorer.py:177` aging/breach · `decide.py:287` sla_urgency>1→inf ·
# `saturated.py:92` ETA · `main.py:1055` ETA ao cliente) mordem HOJE, ou o
# defeito é dedutivo?
#
# Com alvo de 24 h, `sla_ratio` após 10 min de espera é 0,0069 ⇒ aging ~0,7% do
# fator e breach ZERO para sempre. Mas isso só é dano onde contato de fato
# esperou. Por isso a saída é CRUA, por pool — nada de lista hardcoded de faixa
# que envelhece em silêncio; a faixa se lê contra o inventário do
# `q_sla_target_inventory.py`, rodado na mesma sessão.
#
# Veredicto (TRÊS ramos, e o ausente é INCONCLUSIVO):
#   pool de faixa `process` com esperas > 0 ⇒ VIVO
#   só pools de faixa `wait_plausible`      ⇒ LATENTE (declarar como dedução)
#   TOTAL = 0                               ⇒ INCONCLUSIVO (sem produtor ou sem
#                                             amostra — não é prova de ausência)
#
# Uso:  bash infra/test/q_sla_band_wait_witness.sh
set -u
cd "$(dirname "$0")/../.." || exit 1
DC="docker compose -f docker-compose.demo.yml"
TENANT="${TENANT:-tenant_demo}"

q() { $DC exec -T clickhouse clickhouse-client -q "$1" < /dev/null; }

echo "== esperas (role='queue') por pool — tenant $TENANT"
q "SELECT pool_id,
          count()               AS esperas,
          uniqExact(session_id) AS sessoes,
          countIf(duration_ms IS NULL) AS abertos,
          round(avg(duration_ms))      AS media_ms,
          max(duration_ms)             AS max_ms
   FROM plughub_demo.segments AS s FINAL
   WHERE tenant_id = '$TENANT' AND role = 'queue'
   GROUP BY pool_id ORDER BY esperas DESC FORMAT PrettyCompact"

echo
echo "== TESTEMUNHA DE PRESENÇA — o instrumento está medindo alguma coisa?"
q "SELECT count()                AS total_queue_segments,
          uniqExact(pool_id)     AS pools_distintos,
          uniqExact(session_id)  AS sessoes_distintas
   FROM plughub_demo.segments AS s FINAL
   WHERE tenant_id = '$TENANT' AND role = 'queue' FORMAT PrettyCompact"

echo
echo "-- se total_queue_segments = 0, NENHUM ramo acima vale: é INCONCLUSIVO,"
echo "   não 'não há defeito'."
