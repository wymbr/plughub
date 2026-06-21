#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Seed de demonstração — lente `deploy` do bench de Agentes (Arc 6 Fase 2).
#
# Ilumina a lente `deploy` com dado ALINHADO: segments (AI, role=primary) +
# evaluation_finalized (modo Oficial) compartilhando session_id, e flow_id =
# skill_id REAL — para que (a) a SÉRIE atribua por agent_key e (b) os MARKERS
# do agent-registry batam com o mesmo skill_id (resolve o ponto §8).
#
# Cria 3 dias de qualidade (queda → recuperação) em torno de um deploy registrado
# via API do agent-registry, dentro da janela 2026-06-13..2026-06-20 do demo.
#
# Uso:  bash infra/test/seed_deploy_lens_demo.sh
# Limpeza: as linhas usam session_id com prefixo dlz_ — fácil de filtrar/apagar.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail

CH="${CH:-http://localhost:8123}"; CH_USER="${CH_USER:-plughub}"; CH_PASS="${CH_PASS:-plughub}"
DB="${DB:-plughub_demo}"
TENANT="${TENANT:-tenant_demo}"
REGISTRY="${REGISTRY:-http://localhost:3300}"
ANALYTICS="${ANALYTICS:-http://localhost:3500}"

# Skill REAL existente no registry (skill_id == flow_id que vamos gravar nos segments).
SKILL="${SKILL:-skill_atendimento_sac_v1}"
POOL="${POOL:-sac_ia}"
CURL="curl -s --max-time 15"
ch() { $CURL -u "$CH_USER:$CH_PASS" "$CH/" --data-binary "$1"; }

echo "══ seed segments (AI, primary, flow_id=$SKILL) ══"
ch "INSERT INTO $DB.segments
    (segment_id,session_id,tenant_id,participant_id,pool_id,agent_type_id,flow_id,instance_id,role,agent_type,sequence_index,started_at,date)
    FORMAT JSONEachRow
{\"segment_id\":\"dlz_seg_1\",\"session_id\":\"dlz_s1\",\"tenant_id\":\"$TENANT\",\"participant_id\":\"dlz_p1\",\"pool_id\":\"$POOL\",\"agent_type_id\":\"$SKILL\",\"flow_id\":\"$SKILL\",\"instance_id\":\"dlz_i1\",\"role\":\"primary\",\"agent_type\":\"ai\",\"sequence_index\":0,\"started_at\":\"2026-06-15 10:00:00.000\",\"date\":\"2026-06-15\"}
{\"segment_id\":\"dlz_seg_2\",\"session_id\":\"dlz_s2\",\"tenant_id\":\"$TENANT\",\"participant_id\":\"dlz_p2\",\"pool_id\":\"$POOL\",\"agent_type_id\":\"$SKILL\",\"flow_id\":\"$SKILL\",\"instance_id\":\"dlz_i2\",\"role\":\"primary\",\"agent_type\":\"ai\",\"sequence_index\":0,\"started_at\":\"2026-06-17 10:00:00.000\",\"date\":\"2026-06-17\"}
{\"segment_id\":\"dlz_seg_3\",\"session_id\":\"dlz_s3\",\"tenant_id\":\"$TENANT\",\"participant_id\":\"dlz_p3\",\"pool_id\":\"$POOL\",\"agent_type_id\":\"$SKILL\",\"flow_id\":\"$SKILL\",\"instance_id\":\"dlz_i3\",\"role\":\"primary\",\"agent_type\":\"ai\",\"sequence_index\":0,\"started_at\":\"2026-06-19 10:00:00.000\",\"date\":\"2026-06-19\"}" >/dev/null && echo "  ✓ 3 segments"

echo "══ seed evaluation_finalized (Oficial, mesmos session_id) ══"
# Queda no dia do deploy (17) e recuperação depois (19) — para a linha ter forma.
ch "INSERT INTO $DB.evaluation_finalized
    (instance_id,result_id,session_id,tenant_id,campaign_id,final_score,finalize_reason,contestation_state,evaluated_agent_type,segment_id,form_version,round,process_duration_ms,timestamp,date)
    FORMAT JSONEachRow
{\"instance_id\":\"dlz_e1\",\"result_id\":\"dlz_r1\",\"session_id\":\"dlz_s1\",\"tenant_id\":\"$TENANT\",\"campaign_id\":\"dlz_camp\",\"final_score\":0.82,\"finalize_reason\":\"uncontested\",\"contestation_state\":\"uncontested\",\"evaluated_agent_type\":\"ai_agent\",\"segment_id\":\"dlz_seg_1\",\"form_version\":1,\"round\":1,\"process_duration_ms\":1000,\"timestamp\":\"2026-06-15 10:05:00.000\",\"date\":\"2026-06-15\"}
{\"instance_id\":\"dlz_e2\",\"result_id\":\"dlz_r2\",\"session_id\":\"dlz_s2\",\"tenant_id\":\"$TENANT\",\"campaign_id\":\"dlz_camp\",\"final_score\":0.61,\"finalize_reason\":\"uncontested\",\"contestation_state\":\"uncontested\",\"evaluated_agent_type\":\"ai_agent\",\"segment_id\":\"dlz_seg_2\",\"form_version\":1,\"round\":1,\"process_duration_ms\":1000,\"timestamp\":\"2026-06-17 10:05:00.000\",\"date\":\"2026-06-17\"}
{\"instance_id\":\"dlz_e3\",\"result_id\":\"dlz_r3\",\"session_id\":\"dlz_s3\",\"tenant_id\":\"$TENANT\",\"campaign_id\":\"dlz_camp\",\"final_score\":0.88,\"finalize_reason\":\"uncontested\",\"contestation_state\":\"uncontested\",\"evaluated_agent_type\":\"ai_agent\",\"segment_id\":\"dlz_seg_3\",\"form_version\":1,\"round\":1,\"process_duration_ms\":1000,\"timestamp\":\"2026-06-19 10:05:00.000\",\"date\":\"2026-06-19\"}" >/dev/null && echo "  ✓ 3 evaluation_finalized"

echo "══ registra deploy da skill (markers) via agent-registry ══"
DEP=$($CURL -X POST "$REGISTRY/v1/skills/$SKILL/deploy" \
  -H "Content-Type: application/json" -H "X-Tenant-ID: $TENANT" -H "X-User-Id: seed_demo" \
  -d "{\"pool_ids\":[\"$POOL\"],\"notes\":\"seed deploy-lens demo\"}")
echo "  resposta: $(echo "$DEP" | head -c 300)"
echo "  (se 404 'Skill não encontrada' → a skill $SKILL não existe no registry; ajuste SKILL=)"

echo "══ verificação rápida pela API da lente ══"
Q="$ANALYTICS/reports/agents/compare?tenant_id=$TENANT&from_dt=2026-06-13&to_dt=2026-06-20&lens=deploy&entities=$SKILL"
echo "  $Q"
$CURL "$Q" | (jq -c '{lens:.meta.lens,min:.meta.min_sample,pts:(.data.entities[0].series|length),markers:(.deploy_markers|length)}' 2>/dev/null || cat)
echo
echo "✅ seed pronto. No bench: Analytics → Agents, lente 'Deploy (quality)',"
echo "   marque a entidade '$SKILL' (janela 13–20/06). Série com queda no 17 +"
echo "   recuperação no 19; marker vertical se o deploy caiu na janela."
