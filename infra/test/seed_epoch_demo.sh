#!/usr/bin/env bash
# seed_epoch_demo.sh — Arc 6 Fase 2 / R15a–R15b (modo epoch da lente deploy).
#
# Insere avaliações finalizadas de IA com `deploy_version` carimbado em DUAS
# versões (1.0 e 2.0) do skill_atendimento_sac_v1 no pool sac_ia, para o modo
# "By version" (epoch) desenhar o eixo X = versões. O epoch só precisa de
# `segments` (deploy_version/flow_id/pool_id/agent_type) + `evaluation_finalized`
# (final_score, segment_id) — JOIN exato por segment_id (carimbo R9).
#
# v1.0 (qualidade ~0.73) → v2.0 (~0.84): mostra a qualidade subindo de uma versão
# para a outra. N=6/versão (< min_sample 30 → o aviso "Low sample" aparece, de
# propósito/honesto).
#
# ── DATAS RELATIVAS (2026-08-12) ──────────────────────────────────────────────
# Eram literais (18 e 21/06/2026) e envelheceram: em agosto a lente vinha vazia no
# período default e a demo dependia de mexer no seletor ao vivo. Agora ancoram em
# HOJE. Pinagem: `ANCHOR=2026-06-21 bash …` fixa a data da v2.0.
#
# Limpeza: automática no topo (prefixos *_epoch_* / seed1b). É NECESSÁRIA porque
# re-rodar com datas novas sem apagar deixaria as linhas antigas vivas — o
# ReplacingMergeTree só dedup dentro da partição, e a partição é por data. Duas
# execuções em dias diferentes dariam quatro epochs em vez de duas.
#
# Uso:  bash infra/test/seed_epoch_demo.sh
set -euo pipefail

COMPOSE="docker compose -f docker-compose.demo.yml"
# stdin de /dev/null: sem isso, `clickhouse-client -q "INSERT ... VALUES ..."`
# fica bloqueado lendo stdin esperando mais linhas de dados (o exec -T encaminha
# o stdin do terminal). SELECT não trava; INSERT trava — daí o </dev/null.
CH() { $COMPOSE exec -T clickhouse clickhouse-client -d plughub_demo "$@" < /dev/null; }

TENANT=tenant_demo
POOL=sac_ia
SKILL=skill_atendimento_sac_v1

# ── Datas ─────────────────────────────────────────────────────────────────────
# `date -d` é GNU. Ausente ⇒ aborta: data vazia produziria um INSERT inválido ou,
# pior, uma lente vazia que se lê como "a feature não funciona".
ANCHOR="${ANCHOR:-$(date -u +%F)}"
DAY_V2=$(date -u -d "$ANCHOR - 1 day" +%F 2>/dev/null)
DAY_V1=$(date -u -d "$ANCHOR - 4 day" +%F 2>/dev/null)
if [[ -z "$DAY_V1" || -z "$DAY_V2" ]]; then
  echo "⚠️  INCONCLUSIVO: \`date -d\` indisponível (precisa de GNU coreutils)." >&2
  exit 2
fi
echo "══ epochs relativos a $ANCHOR: v1.0 em $DAY_V1 → v2.0 em $DAY_V2 ══"

# ── Limpeza das execuções anteriores ─────────────────────────────────────────
CH -q "ALTER TABLE plughub_demo.segments DELETE
       WHERE tenant_id='$TENANT' AND session_id LIKE 'sess_epoch_%'
       SETTINGS mutations_sync=1"
CH -q "ALTER TABLE plughub_demo.evaluation_finalized DELETE
       WHERE tenant_id='$TENANT' AND session_id LIKE 'sess_epoch_%'
       SETTINGS mutations_sync=1"
echo "  ✓ linhas de execuções anteriores removidas"

seg_vals=""
fin_vals=""
emit() {  # ver day score i
  local ver=$1 day=$2 score=$3 i=$4
  local k="${ver//./_}_$i"
  local sid="seg_epoch_$k" sess="sess_epoch_$k" iid="inst_epoch_$k"
  seg_vals+="('$sid','$sess','$TENANT','part_$k','$POOL','$SKILL','$SKILL','$ver','webchat','$iid','primary','ai',0,'$day 10:00:00','$day'),"
  fin_vals+="('$iid','res_$iid','$sess','$TENANT',$score,'auto','none','ai','$sid',1,1,1000,'$day 10:05:00','$day'),"
}

# v1.0 — 6 avaliações em $DAY_V1 (~0.71..0.76)
for i in 1 2 3 4 5 6; do emit 1.0 "$DAY_V1" "0.7$i" "$i"; done
# v2.0 — 6 avaliações em $DAY_V2 (~0.81..0.86)
for i in 1 2 3 4 5 6; do emit 2.0 "$DAY_V2" "0.8$i" "$i"; done

CH -q "INSERT INTO plughub_demo.segments
  (segment_id,session_id,tenant_id,participant_id,pool_id,agent_type_id,flow_id,deploy_version,channel,instance_id,role,agent_type,sequence_index,started_at,date)
  VALUES ${seg_vals%,}"

CH -q "INSERT INTO plughub_demo.evaluation_finalized
  (instance_id,result_id,session_id,tenant_id,final_score,finalize_reason,contestation_state,evaluated_agent_type,segment_id,form_version,round,process_duration_ms,timestamp,date)
  VALUES ${fin_vals%,}"

echo "seeded (ClickHouse, finalizadas): 6×v1.0 ($DAY_V1) + 6×v2.0 ($DAY_V2) em $POOL/$SKILL ($TENANT)"

# ── Postgres: instâncias PENDENTES da v2.0 (micro-fatia 1b) ───────────────────
# 8 instâncias amostradas em avaliação (status in_progress) p/ a versão 2.0 →
# pending_n=8 no overlay do epoch (a v1.0 do demo já tem instâncias finalizadas
# com provisional_avg). Reusa uma campanha existente do pool sac_ia (FK).
PG() { $COMPOSE exec -T -e PGPASSWORD=plughub postgres \
       psql -U plughub -d plughub_demo -v ON_ERROR_STOP=1 -t "$@" < /dev/null; }

PG -c "
WITH camp AS (
  SELECT id, form_id FROM evaluation.campaigns
  WHERE tenant_id='$TENANT' AND (evaluation_pool_id='$POOL' OR pool_id='$POOL')
  ORDER BY created_at LIMIT 1
)
INSERT INTO evaluation.instances
  (id, tenant_id, campaign_id, form_id, session_id, segment_id, status, deploy_version,
   scheduled_at, created_at, updated_at)
SELECT 'evinstance_seed1b_v2_'||gs, '$TENANT', camp.id, camp.form_id,
       'sess_seed1b_v2_'||gs, 'seg_seed1b_v2_'||gs, 'in_progress', '2.0',
       '$DAY_V2 10:00:00+00', '$DAY_V2 10:00:00+00', now()
FROM camp, generate_series(1,8) AS gs
ON CONFLICT (id) DO UPDATE SET scheduled_at = EXCLUDED.scheduled_at,
                               created_at   = EXCLUDED.created_at,
                               updated_at   = now();
"

echo "seeded (Postgres, pendentes): 8×v2.0 in_progress em $POOL ($TENANT), $DAY_V2"
echo "→ abra a lente Deploy, selecione o pool sac_ia, toggle 'By version'."
echo "   esperado: v1.0 sólida+tracejada (provisória), v2.0 sólida + selo 'pendentes +8'."
