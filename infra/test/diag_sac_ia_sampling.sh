#!/usr/bin/env bash
# diag_sac_ia_sampling.sh
# ---------------------------------------------------------------------------
# Localiza ONDE a cadeia de amostragem sac_ia quebra no demo.
#
# Cadeia esperada para gerar uma evaluation instance de um contato sac_ia:
#
#   (1) sessão sac_ia roda  →  conversations.participants (role=primary, pool=sac_ia,
#       flow_id=skill_atendimento_sac_v1, deploy_version)
#         → evaluation-api participants-consumer acumula em Redis
#           tenant_demo:eval:segs:{session_id}
#   (2) sac_ia completa (resolved)  →  orchestrator-bridge publica
#       conversations.session_closed {pool_id, channel, outcome, closed_at}
#   (3) evaluation-api sampling-consumer lê os segs do Redis, casa com a campanha
#       ATIVA, should_sample() → create_instance() → evaluation.instances (+deploy_version)
#
# Uso:
#   bash infra/test/diag_sac_ia_sampling.sh           # snapshot do estado atual
#   bash infra/test/diag_sac_ia_sampling.sh <sid>     # foca um session_id sac_ia
#
# Rode DEPOIS de fechar um contato sac_ia novo (os consumers usam offset=latest;
# pós-rebuild só enxergam sessões fechadas após o start).
# ---------------------------------------------------------------------------
set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
TENANT="tenant_demo"
SID="${1:-}"
PG="$DC exec -T postgres psql -U plughub -d plughub_demo -tAc"
REDIS="$DC exec -T redis redis-cli"

hr(){ printf '\n──────────────────────────────────────────────────────────────\n%s\n──────────────────────────────────────────────────────────────\n' "$1"; }

hr "0) Consumers de avaliação subiram? (procure 'consumer started')"
$DC logs evaluation-api 2>&1 | grep -iE "consumer started|sampling consumer|participants consumer" | tail -n 10

hr "1) Logs de amostragem recentes (scheduled / failed)"
echo "# 'sampling: scheduled instance ...'  = instance criada"
echo "# 'sampling: failed for <sid> ...'    = exceção em _sample_on_close (LEIA o erro)"
$DC logs --since 2h evaluation-api 2>&1 | grep -iE "sampling: (scheduled|failed)|create_instance|deploy_version|participant event error" | tail -n 30

hr "2) Campanha(s) — DEVE existir >=1 com status='active'"
echo "# Se status != active, list_campaigns() retorna [] e a amostragem nem roda."
$PG "SELECT id, name, status, evaluation_pool_id,
            (sampling_rules->>'mode')      AS mode,
            (sampling_rules->>'min_duration_s') AS min_dur,
            (sampling_rules->>'pool_ids')  AS pool_ids,
            (sampling_rules->>'outcome_filter') AS outcomes,
            period_start, period_end, total_instances
       FROM evaluation.campaigns ORDER BY created_at;" 2>/dev/null \
  | sed 's/^/  /'
echo
echo "# NOTA: epid = COALESCE(evaluation_pool_id, pool_id). Se setado, o seg.pool_id"
echo "#       (sac_ia) PRECISA bater. period_start/end fora da janela tambem descarta."

hr "3) Redis — segmentos acumulados por participants (chave por sessão)"
echo "# Sem chave => participants-consumer NAO acumulou => cai no fallback-por-sessão."
KEYS=$($REDIS --scan --pattern "${TENANT}:eval:segs:*" 2>/dev/null | head -n 20)
if [ -z "$KEYS" ]; then echo "  (nenhuma chave ${TENANT}:eval:segs:* — VER passo 4 e 5)"; else
  echo "$KEYS" | while read -r k; do
    [ -z "$k" ] && continue
    echo "  KEY $k"
    $REDIS hgetall "$k" 2>/dev/null | sed 's/^/      /'
  done
fi

hr "4) Kafka — últimas conversations.participants (role/pool/flow/deploy_version)"
echo "# Confirma que o bridge emite o segmento sac_ia com role=primary e pool_id=sac_ia."
timeout 8 $DC exec -T kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server kafka:29092 --topic conversations.participants \
  --from-beginning --timeout-ms 6000 2>/dev/null \
  | grep -iE 'sac_ia|skill_atendimento_sac' | tail -n 10 | sed 's/^/  /'

hr "5) Kafka — últimas conversations.session_closed (pool_id/outcome/closed_at)"
echo "# Sem mensagem aqui p/ a sessão sac_ia => bridge não publicou => passo (2) quebrou."
timeout 8 $DC exec -T kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server kafka:29092 --topic conversations.session_closed \
  --from-beginning --timeout-ms 6000 2>/dev/null \
  | tail -n 10 | sed 's/^/  /'

hr "6) Instances criadas nas últimas 2h (deploy_version preenchido = R9d-1 OK)"
$PG "SELECT id, campaign_id, session_id, segment_id, evaluated_user_id,
            deploy_version, status, created_at
       FROM evaluation.instances
      WHERE created_at > now() - interval '2 hours'
      ORDER BY created_at DESC LIMIT 20;" 2>/dev/null | sed 's/^/  /'
echo "  (vazio = nenhuma instance recente)"

if [ -n "$SID" ]; then
  hr "7) Foco no session_id = $SID"
  echo "# 7a) segs acumulados p/ esta sessão:"
  $REDIS hgetall "${TENANT}:eval:segs:${SID}" 2>/dev/null | sed 's/^/      /'
  echo "# 7b) instances p/ esta sessão:"
  $PG "SELECT id, segment_id, deploy_version, status, created_at
         FROM evaluation.instances WHERE session_id='${SID}';" 2>/dev/null | sed 's/^/      /'
  echo "# 7c) segmentos no analytics (ClickHouse) p/ esta sessão (R9 a-c):"
  $DC exec -T clickhouse clickhouse-client -u plughub --password plughub -d plughub_demo \
     -q "SELECT segment_id, pool_id, agent_type, flow_id, deploy_version, channel
           FROM analytics.segments WHERE session_id='${SID}' FORMAT Vertical" 2>/dev/null | sed 's/^/      /'
fi

hr "8) Survey — contatos in_progress/active acumulados (efeito colateral 13:47)"
echo "# inbound_only F10.2b.2: skill_survey_v1 suspende até timeout_hours:1 (reconexão)."
$PG "SELECT pool_id, status, count(*)
       FROM evaluation.instances WHERE 1=0;" >/dev/null 2>&1 # noop guard
$DC exec -T redis redis-cli --scan --pattern "${TENANT}:resume_tokens" >/dev/null 2>&1
echo "# 8a) resume_tokens pendentes (cada survey suspensa deixa 1):"
$REDIS hlen "${TENANT}:resume_tokens" 2>/dev/null | sed 's/^/      pending resume_tokens: /'
echo "# 8b) pending_workflow keys (1 por survey aguardando reconexão inbound):"
$REDIS --scan --pattern "${TENANT}:pending_workflow:*" 2>/dev/null | wc -l | sed 's/^/      pending_workflow keys: /'

hr "FIM — interpretação rápida"
cat <<'EOF'
  • Passo 2 mostra campanha sem status=active  → AMOSTRAGEM NÃO RODA (causa #1 provável).
  • Passo 3 vazio + passo 4 com participants OK → participants-consumer parado/erro.
  • Passo 4 vazio                              → bridge não emite participant sac_ia.
  • Passo 5 vazio p/ a sessão                  → bridge não publicou session_closed.
  • Passo 1 com 'sampling: failed'             → LEIA o traceback (ex.: coluna ausente).
  • Passo 3 OK + passo 6 vazio + passo 2 com evaluation_pool_id != sac_ia → mismatch de pool.
EOF
