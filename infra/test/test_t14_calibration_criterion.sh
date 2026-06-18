#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T14 (c) — criterion_id na CalibrationNote (spec §6/§18.3).
# Valida o round-trip do critério pelo laço mole de calibração:
#   setup: campanha IA + regra random_baseline rate=1.0 (toda finalização IA vira review)
#   1. ingest avaliação IA (em-faixa) → finaliza auto_ai → cria CurationReview;
#   2. curador resolve `recalibrated` com criterion_id → cria CalibrationNote (cobre (b):
#      resolve_curation sem NameError);
#   3. GET /calibration-notes → a nota carrega o criterion_id (e dimension_id) → o RAG
#      pode ancorar a orientação no critério certo.
# (O "scoring desloca" de fato é (a), e2e-blocked pelo avaliador real — fora desta leva.)
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
EVAL="${EVAL:-http://localhost:3400}"
TENANT="${TENANT:-tenant_demo}"
ADMIN="${ADMIN:-changeme_eval_admin_token_demo}"
EVAL_POOL_ID="${EVAL_POOL_ID:-retencao_humano}"
USER="${USER_ID:-supervisor@plughub.local}"
CURL="curl -s --max-time 15"
JSON='-H Content-Type:application/json'
TH="-H X-Tenant-ID:$TENANT"
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }

echo "══ aguardando evaluation-api ══"
for i in $(seq 1 30); do $CURL "$EVAL/health" >/dev/null 2>&1 && { echo "  ✓ no ar"; break; }; \
  [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

echo "══ setup: form (crit c1) + campanha + regra random_baseline rate=1.0 ══"
F=$($CURL -X POST "$EVAL/v1/evaluation/forms" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t14\",\"dimensions\":[
    {\"dimension_id\":\"d1\",\"name\":\"D\",\"weight\":1,\"criteria\":[
      {\"criterion_id\":\"c1\",\"label\":\"C1\",\"type\":\"score\",\"weight\":1,\"max_score\":10}]}]}" \
  | jq -r '.form_id // .id // empty')
C=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t14_camp\",\"form_id\":\"$F\",
  \"pool_id\":\"$EVAL_POOL_ID\",\"evaluation_pool_id\":\"$EVAL_POOL_ID\"}" | jq -r '.campaign_id // .id // empty')
[ -n "$F" ] && [ -n "$C" ] || { echo "  ✗ setup falhou"; exit 1; }
$CURL -X POST "$EVAL/v1/evaluation/campaigns/$C/sampling-rules" $TH $JSON \
  -d '{"rule_type":"random_baseline","params":{"rate":1.0},"enabled":true}' >/dev/null

echo "══ CASO 1 — ingest IA em-faixa → finaliza → CurationReview ══"
I=$($CURL -X POST "$EVAL/v1/evaluation/instances" $JSON \
  -d "{\"tenant_id\":\"$TENANT\",\"campaign_id\":\"$C\",\"session_id\":\"sess-t14\"}" | jq -r '.id // .instance_id // empty')
$CURL -X POST "$EVAL/v1/evaluation/ingest" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"instance_id\":\"$I\",\"session_id\":\"s\",\"campaign_id\":\"$C\",
  \"form_id\":\"$F\",\"evaluator_agent_id\":\"agente_avaliacao_v1\",\"evaluated_agent_type\":\"ai_agent\",
  \"criterion_responses\":[{\"criterion_id\":\"c1\",\"score\":8,\"justification\":\"sintético\"}]}" >/dev/null

# run_curation_sampling é fire-and-forget → poll pela review do nosso instance
RID=""
for i in $(seq 1 15); do
  RID=$($CURL "$EVAL/v1/evaluation/curations?campaign_id=$C&status=pending" $TH \
    | jq -r --arg I "$I" '.reviews[]? | select(.evaluation_instance_id==$I) | .id' | head -1)
  [ -n "$RID" ] && break; sleep 1
done
assert "CurationReview criada p/ a instance" true "$([ -n "$RID" ] && echo true || echo false)"
[ -n "$RID" ] || { echo "  ✗ sem review — abortando"; exit 1; }

echo "══ CASO 2 — resolve recalibrated com criterion_id (cobre (b) sem NameError) ══"
RES=$($CURL -X POST "$EVAL/v1/evaluation/curations/$RID/resolve" $TH -H "X-User-ID:$USER" $JSON -d "{
  \"status\":\"recalibrated\",\"calibration_note_text\":\"avaliador leniente no critério c1\",
  \"dimension_id\":\"d1\",\"criterion_id\":\"c1\",
  \"evaluator_id\":\"agente_avaliacao_v1\",\"skill_version\":\"v1\",\"severity\":\"medium\"}")
NOTE_CRIT=$(echo "$RES" | jq -r '.calibration_note.criterion_id // empty')
assert "resolve OK (calibration_note criada)" true "$([ -n "$(echo "$RES" | jq -r '.calibration_note.id // empty')" ] && echo true || echo false)"
assert "criterion_id na resposta do resolve" c1 "$NOTE_CRIT"

echo "══ CASO 3 — GET /calibration-notes carrega o criterion_id ══"
N=$($CURL "$EVAL/v1/evaluation/calibration-notes?campaign_id=$C" $TH)
GOT_CRIT=$(echo "$N" | jq -r --arg I "$I" '.notes[0].criterion_id // empty')
assert "criterion_id persistido/retornado" c1 "$GOT_CRIT"
assert "dimension_id mantido"               d1 "$(echo "$N" | jq -r '.notes[0].dimension_id // empty')"

echo
[ "$FAIL" = 0 ] && echo "✅ T14 (c) OK — criterion_id flui resolve→CalibrationNote→list (RAG por critério); resolve sem NameError" \
                || { echo "❌ T14 (c) com falhas"; exit 1; }
