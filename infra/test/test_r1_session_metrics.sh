#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# R1 — SessionMetricsExtractor fiado no _ingest_core (lazy) + auto_computed na nota.
#
# Semeia session_stream_events (2 msgs cliente role NULL + 2 msgs agente primary,
# visibility "all") para uma sessão, cria um form com 1 critério LLM (score) + 1
# critério auto_computed (computation_source=session_metric.customer_messages,
# gte 2 → passa), cria instance e chama /ingest. Valida:
#   - instance.session_metrics gravado com total=4 / customer=2 / agent=2 (mapeamento
#     customer=role NULL + média ponderada);
#   - o critério auto_computed é preenchido pelo extractor e ENTRA na nota:
#     overall = (LLM 6 + auto 10) / 2 = 8.
#
# Fontes no mesmo banco plughub_demo (session_stream_events, evaluation.*).
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail

EVAL="${EVAL:-http://localhost:3400}"
TENANT="${TENANT:-tenant_demo}"
COMPOSE="${COMPOSE:-docker compose -f docker-compose.demo.yml}"
CURL="curl -s --max-time 10"
JSON='-H Content-Type:application/json'
SID="sess_r1_$$"
FAIL=0

assert()     { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }
assert_num() { if awk "BEGIN{exit !(($2)==($3))}" 2>/dev/null; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }
psql_q()     { $COMPOSE exec -T postgres psql -U plughub -d plughub_demo -tA -c "$1"; }

echo "══ aguardando evaluation-api ══"
for i in $(seq 1 30); do $CURL "$EVAL/health" >/dev/null 2>&1 && { echo "  ✓ no ar"; break; }; \
  [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

echo "══ seed session_stream_events (sid=$SID) ══"
psql_q "
INSERT INTO session_stream_events
  (tenant_id, session_id, event_id, event_type, timestamp, author, visibility, payload, masked_categories, delta_ms) VALUES
  ('$TENANT','$SID','r1e1','message', now() - interval '60 seconds', NULL,                 '\"all\"'::jsonb, '{\"content\":\"oi, preciso de ajuda\"}'::jsonb,        '{}', 0),
  ('$TENANT','$SID','r1e2','message', now() - interval '50 seconds', '{\"role\":\"primary\"}'::jsonb, '\"all\"'::jsonb, '{\"content\":\"claro, em que posso ajudar hoje?\"}'::jsonb, '{}', 0),
  ('$TENANT','$SID','r1e3','message', now() - interval '40 seconds', NULL,                 '\"all\"'::jsonb, '{\"content\":\"minha conta esta bloqueada\"}'::jsonb,     '{}', 0),
  ('$TENANT','$SID','r1e4','message', now() - interval '30 seconds', '{\"role\":\"primary\"}'::jsonb, '\"all\"'::jsonb, '{\"content\":\"vou verificar isso para voce agora\"}'::jsonb, '{}', 0)
ON CONFLICT DO NOTHING;" >/dev/null
echo "  ✓ 4 eventos semeados (2 cliente role NULL + 2 agente primary)"

echo "══ setup: form (LLM + auto_computed) + campanha + instance ══"
F=$($CURL -X POST "$EVAL/v1/evaluation/forms" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"r1_metrics\",\"dimensions\":[
    {\"dimension_id\":\"d1\",\"name\":\"Atendimento\",\"weight\":1,\"criteria\":[
      {\"criterion_id\":\"c_llm\",\"label\":\"Clareza\",\"type\":\"score\",\"weight\":1,\"max_score\":10},
      {\"criterion_id\":\"c_auto\",\"label\":\"Houve diálogo do cliente\",\"type\":\"auto_computed\",\"weight\":1,\"max_score\":10,
       \"computation_source\":\"session_metric.customer_messages\",\"threshold_pass\":2,\"comparison\":\"gte\"}
    ]}]}" | jq -r '.form_id // .id // empty')
[ -n "$F" ] || { echo "  ✗ form falhou"; exit 1; }
C=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"r1_camp\",\"form_id\":\"$F\",
  \"pool_id\":\"retencao_humano\",\"evaluation_pool_id\":\"retencao_humano\"}" \
  | jq -r '.campaign_id // .id // empty')
[ -n "$C" ] || { echo "  ✗ campaign falhou"; exit 1; }
I=$($CURL -X POST "$EVAL/v1/evaluation/instances" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"campaign_id\":\"$C\",\"session_id\":\"$SID\"}" \
  | jq -r '.id // .instance_id // empty')
[ -n "$I" ] || { echo "  ✗ instance falhou"; exit 1; }
echo "  form=$F campaign=$C instance=$I"

echo "══ ingest (só o critério LLM; o auto é preenchido pelo extractor) ══"
R=$($CURL -X POST "$EVAL/v1/evaluation/ingest" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"instance_id\":\"$I\",\"session_id\":\"$SID\",
  \"campaign_id\":\"$C\",\"form_id\":\"$F\",\"evaluator_agent_id\":\"agente_avaliacao_v1\",
  \"evaluated_agent_type\":\"human_agent\",
  \"overall_score\":2.0,
  \"criterion_responses\":[{\"criterion_id\":\"c_llm\",\"score\":6,\"max_score\":10,\"na\":false,\"notes\":\"ok\"}]}")

echo "══ asserts ══"
# (a) session_metrics gravado com o mapeamento correto
M=$(psql_q "SELECT (session_metrics->>'total_messages')||'/'||(session_metrics->>'customer_messages')||'/'||(session_metrics->>'agent_messages') FROM evaluation.instances WHERE id='$I';")
assert "session_metrics total/customer/agent" "4/2/2" "$M"

# (b) auto_computed entrou na nota: overall = (6 + 10)/2 = 8
assert_num "overall_score (LLM 6 + auto 10)/2" 8 "$(echo "$R" | jq -r .overall_score)"
assert_num "dim d1 score"                       8 "$(echo "$R" | jq -r '.final_scores_by_dimension[] | select(.dimension_id=="d1") | .score')"

echo "══ cleanup ══"
psql_q "DELETE FROM session_stream_events WHERE tenant_id='$TENANT' AND session_id='$SID';" >/dev/null
echo "  ✓ stream seed removido"

echo
[ "$FAIL" = 0 ] && echo "✅ R1 OK — session_metrics extraído (customer=role NULL) + auto_computed na nota" \
                || { echo "❌ R1 com falhas"; exit 1; }
