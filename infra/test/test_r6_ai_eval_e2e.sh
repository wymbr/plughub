#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# R6 (e2e) — avaliador de IA tier-2 com evidência de execução, SELF-CONTAINED.
#
# Estende o padrão do t7b2: semeia um ReplayContext com **pipeline_state** (trajetória
# REAL), insere **tool_trace** (mcp.tool_call no ClickHouse) e descobre um **flow_id**
# real (→ flow_definition via agent-registry). Cria form com os 3 critérios de IA,
# dá dispatch e inspeciona se o avaliador PONTUOU os critérios (na=false) — o que só
# acontece se a evidência (tool_trace + actual_trajectory + flow_definition) chegou
# ao evaluation_context_get e foi usada pelo reason.
#
# Prova: ≥1 critério de IA com score (na=false). Imprime os 3 + os inputs presentes.
#
# Pré: stack demo no ar, pool avaliador (avaliacao_ia) + ai-gateway operantes. jq.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail

EVAL="${EVAL:-http://localhost:3400}"
AREG="${AREG:-http://localhost:3300}"
TENANT="${TENANT:-tenant_demo}"
ADMIN="${ADMIN:-changeme_eval_admin_token_demo}"
EVALUATOR_POOL="${EVALUATOR_POOL:-avaliacao_ia}"
EVAL_POOL_ID="${EVAL_POOL_ID:-retencao_humano}"
CH_DB="${CH_DB:-plughub_demo}"
COMPOSE="docker compose -f docker-compose.demo.yml"
CURL="curl -s --max-time 20"
JSON='-H Content-Type:application/json'
FAIL=0
assert_true() { if [ "$2" = "true" ]; then echo "  ✓ $1"; else echo "  ✗ $1 (=$2)"; FAIL=1; fi; }
uuid() { cat /proc/sys/kernel/random/uuid; }

echo "══ aguardando evaluation-api ══"
for i in $(seq 1 30); do $CURL "$EVAL/health" >/dev/null 2>&1 && { echo "  ✓ no ar"; break; }; \
  [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

# G-PROBE (posterior a este script): create_form/publish/create_campaign exigem Bearer +
# ABAC `evaluation.formularios:read_write`, e o dispatch aceita Bearer rw OU
# X-Service-Token — X-Admin-Token deixou de servir (`_require_service` não tem fallback
# p/ admin). Sem isto o script morria em "form falhou". Bearer mintado no container
# (mesmo jwt_secret que a API valida), padrão de smoke_gprobe_service_auth.sh.
JWT_SECRET="${JWT_SECRET:-changeme_auth_jwt_secret_demo_32c}"
TOK=$($COMPOSE exec -T evaluation-api python - "$JWT_SECRET" <<'PY' 2>/dev/null | tr -d '\r' | tail -n1
import sys, jwt
print(jwt.encode({"sub":"u_r6","tenant_id":"tenant_demo","roles":["operator"],
                  "module_config":{"evaluation":{"formularios":{"access":"read_write","scope":[]}}}},
                 sys.argv[1], algorithm="HS256"))
PY
)
[ -n "$TOK" ] || { echo "  ✗ mint do Bearer falhou"; exit 1; }
BH="Authorization: Bearer $TOK"

SID="sess-r6ai-$(date +%s)"

echo "══ descobrindo um flow_id real (agent-registry) p/ flow_definition ══"
FLOW_ID=$($CURL "$AREG/v1/skills" -H "x-tenant-id: $TENANT" | jq -r '(.skills // [])[0].skill_id // empty')
[ -n "$FLOW_ID" ] || FLOW_ID="skill_retencao_v2"
echo "  flow_id=$FLOW_ID"

echo "══ setup: form de IA (3 critérios) + campanha + instance · session=$SID ══"
F=$($CURL -X POST "$EVAL/v1/evaluation/forms" $JSON -H "$BH" -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"r6_ai_e2e\",\"min_passing_score\":7.0,\"dimensions\":[
    {\"dimension_id\":\"ia_qualidade\",\"name\":\"Qualidade de IA\",\"weight\":1,\"criteria\":[
      {\"criterion_id\":\"tool_correctness\",\"label\":\"Tool correctness\",\"type\":\"score\",\"weight\":1,\"max_score\":10,\"na_allowed\":true,
       \"description\":\"Corretude do uso de ferramentas pelo agente de IA.\",
       \"scoring_guidance\":\"O input inclui o campo tool_trace: a lista das chamadas de ferramenta REAIS desta sessao (cada item com tool_name, allowed, injection_detected, duration_ms). Avalie se as ferramentas foram adequadas e bem usadas e atribua nota 0-10 (10=impecavel; reduza se houver allowed=false ou injection_detected=true). Use na=true APENAS se tool_trace for uma lista vazia ([]).\"},
      {\"criterion_id\":\"policy_adherence\",\"label\":\"Policy adherence\",\"type\":\"score\",\"weight\":1,\"max_score\":10,\"na_allowed\":true,
       \"description\":\"Aderencia do agente de IA a trajetoria esperada do skill-flow.\",
       \"scoring_guidance\":\"O input inclui actual_trajectory (as transitions REAIS executadas, em pipeline_state) e flow_definition (os steps ESPERADOS do skill-flow). Compare a trajetoria real com a esperada e atribua nota 0-10 (desvios nao justificados reduzem). Use na=true APENAS se actual_trajectory for null.\"},
      {\"criterion_id\":\"faithfulness_kb\",\"label\":\"Faithfulness vs KB\",\"type\":\"score\",\"weight\":1,\"max_score\":10,\"na_allowed\":true,
       \"description\":\"Afirmacoes do agente sustentadas pela base de conhecimento.\",
       \"scoring_guidance\":\"O input inclui knowledge_snippets. Verifique afirmacoes factuais contra a KB e atribua nota 0-10. Use na=true se nao houver snippets para checar nenhuma afirmacao.\"}
    ]}]}" | jq -r '.form_id // .id // empty')
[ -n "$F" ] || { echo "  ✗ form falhou"; exit 1; }
$CURL -X POST "$EVAL/v1/evaluation/forms/$F/publish?tenant_id=$TENANT" $JSON -H "$BH" -d '{"published_by":"e2e"}' >/dev/null
FORM_JSON=$($CURL "$EVAL/v1/evaluation/forms/$F?tenant_id=$TENANT")

C=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -H "$BH" -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"r6_ai_camp\",\"form_id\":\"$F\",
  \"pool_id\":\"$EVAL_POOL_ID\",\"evaluation_pool_id\":\"$EVAL_POOL_ID\",\"evaluator_pool\":\"$EVALUATOR_POOL\"}" \
  | jq -r '.campaign_id // .id // empty')
[ -n "$C" ] || { echo "  ✗ campaign falhou"; exit 1; }

INST=$($CURL -X POST "$EVAL/v1/evaluation/instances" $JSON \
  -d "{\"tenant_id\":\"$TENANT\",\"campaign_id\":\"$C\",\"session_id\":\"$SID\"}" \
  | jq -r '.id // .instance_id // empty')
[ -n "$INST" ] || { echo "  ✗ instance falhou"; exit 1; }
echo "  form=$F campaign=$C instance=$INST"

echo "══ semeando tool_trace (mcp.tool_call) no ClickHouse p/ $SID ══"
$COMPOSE exec -T clickhouse clickhouse-client -d "$CH_DB" --query "INSERT INTO session_timeline FORMAT JSONEachRow" <<JSON
{"event_id":"$(uuid)","tenant_id":"$TENANT","session_id":"$SID","segment_id":"seg-r6","event_type":"mcp.tool_call","actor_id":"agente_retencao_ia_v1","actor_role":"primary","payload":"{\"server_name\":\"mcp-server-crm\",\"tool_name\":\"customer_get\",\"allowed\":true,\"injection_detected\":false,\"duration_ms\":40}","timestamp":"2026-06-20 10:00:02.000"}
{"event_id":"$(uuid)","tenant_id":"$TENANT","session_id":"$SID","segment_id":"seg-r6","event_type":"mcp.tool_call","actor_id":"agente_retencao_ia_v1","actor_role":"primary","payload":"{\"server_name\":\"mcp-server-crm\",\"tool_name\":\"offer_apply\",\"allowed\":true,\"injection_detected\":false,\"duration_ms\":75}","timestamp":"2026-06-20 10:00:06.000"}
JSON
TT_N=$($COMPOSE exec -T clickhouse clickhouse-client -d "$CH_DB" --query \
  "SELECT count() FROM session_timeline WHERE tenant_id='$TENANT' AND session_id='$SID' AND event_type='mcp.tool_call'" | tr -d '\r')
echo "  ✓ tool_trace rows = $TT_N"

echo "══ semeando ReplayContext (com pipeline_state) no Redis ══"
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
E1=$(uuid); E2=$(uuid); E3=$(uuid)
CTX=$(jq -nc \
  --argjson form "$FORM_JSON" \
  --arg sid "$SID" --arg cid "$C" --arg iid "$INST" --arg now "$NOW" --arg flow "$FLOW_ID" \
  --arg e1 "$E1" --arg e2 "$E2" --arg e3 "$E3" '
{
  session_id: $sid, tenant_id: "tenant_demo",
  evaluation_form: $form, campaign_id: $cid, instance_id: $iid,
  comparison_mode: false,
  session_meta: { session_id:$sid, outcome:"resolved", channel:"webchat",
                  agent_type_id:"agente_retencao_ia_v1", started_at:$now, closed_at:$now, duration_ms:180000 },
  participants: [ { participant_id:"p_agent", role:"primary", agent_type_id:"agente_retencao_ia_v1" },
                  { participant_id:"p_cust",  role:"customer" } ],
  sentiment: [ {score:0.0}, {score:0.5} ],
  knowledge_snippets: [],
  # R5/B — trajetória REAL (o avaliador lê em context.pipeline_state)
  pipeline_state: {
    flow_id: $flow, status: "completed", current_step_id: "complete_ok",
    source: "postgres",
    transitions: [
      { from_step:"login", to_step:"lookup", reason:"on_success", timestamp:$now },
      { from_step:"lookup", to_step:"offer",  reason:"on_success", timestamp:$now },
      { from_step:"offer",  to_step:"complete_ok", reason:"condition_match", timestamp:$now }
    ]
  },
  events: [
    { event_id:$e1, type:"message", turn_index:0, author_role:"customer", author_id:"p_cust",
      content:"Quero cancelar meu plano, está caro.",
      original_content:"Quero cancelar meu plano, está caro." },
    { event_id:$e2, type:"message", turn_index:1, author_role:"agent", author_id:"p_agent",
      content:"Consultei seu cadastro e apliquei um desconto de retenção de 20% por 6 meses. Fica melhor assim?",
      original_content:"Consultei seu cadastro e apliquei um desconto de retenção de 20% por 6 meses. Fica melhor assim?" },
    { event_id:$e3, type:"message", turn_index:2, author_role:"customer", author_id:"p_cust",
      content:"Assim sim, pode manter então.",
      original_content:"Assim sim, pode manter então." }
  ]
}')
printf '%s' "$CTX" | $COMPOSE exec -T redis redis-cli -x SET "tenant_demo:replay:${SID}:context" >/dev/null
$COMPOSE exec -T redis redis-cli EXPIRE "tenant_demo:replay:${SID}:context" 3600 >/dev/null
echo "  ✓ ReplayContext semeado (EXISTS=$($COMPOSE exec -T redis redis-cli EXISTS "tenant_demo:replay:${SID}:context" | tr -d '\r'))"

echo "══ checagem determinística dos INSUMOS (independe do LLM) ══"
FLOW_OK=$($CURL "$AREG/v1/skills/$FLOW_ID" -H "x-tenant-id: $TENANT" | jq -r 'if (.flow != null) then "true" else "false" end')
assert_true "tool_trace presente no ClickHouse (≥2)"  "$([ "${TT_N:-0}" -ge 2 ] && echo true || echo false)"
assert_true "flow_definition disponível (agent-registry tem .flow)" "$FLOW_OK"
assert_true "pipeline_state no ReplayContext" "$(printf '%s' "$CTX" | jq -e '.pipeline_state.flow_id' >/dev/null 2>&1 && echo true || echo false)"

echo "══ dispatch ══"
D=$($CURL -X POST "$EVAL/v1/evaluation/campaigns/$C/dispatch?tenant_id=$TENANT" \
  -H "$BH" $JSON -d '{}')
echo "  $(echo "$D" | jq -c '{dispatched, evaluator_pool}' 2>/dev/null || echo "$D")"

echo "══ aguardando o avaliador (até 180s) ══"
RID=""
for i in $(seq 1 60); do
  RID=$($CURL "$EVAL/v1/evaluation/results?tenant_id=$TENANT&session_id=$SID&limit=20" \
        | jq -r '(.results // .data // [])[0].id // empty')
  [ -n "$RID" ] && { echo "  ✓ result=$RID após ~$((i*3))s"; break; }
  sleep 3
done
if [ -z "$RID" ]; then
  echo "  ✗ sem resultado. instance status:"
  $CURL "$EVAL/v1/evaluation/instances/$INST?tenant_id=$TENANT" | jq -c '{status}' 2>/dev/null || true
  echo "  logs: $COMPOSE logs --since 5m mcp-server-plughub | tail -60"
  echo "        $COMPOSE logs --since 5m ai-gateway | tail -40"
  exit 1
fi

# ── inspeção ──────────────────────────────────────────────────────────────────
CR=$($CURL "$EVAL/v1/evaluation/results/$RID/criteria?tenant_id=$TENANT")
echo "══ critérios de IA ══"
echo "$CR" | jq -c '[(.criterion_responses // [])[] | {criterion_id, score, na}]'

# NB: usar (.na|tostring) — o operador // do jq trata `false` como vazio, então
# `.na // "missing"` devolveria "missing" para na=false (gotcha). Evitado abaixo.
na_of() { echo "$CR" | jq -r --arg id "$1" '[(.criterion_responses // [])[] | select(.criterion_id==$id)][0] | if .==null then "missing" else (.na|tostring) end'; }
scored() { local n; n=$(na_of "$1"); [ "$n" = "false" ] && echo true || echo false; }
N_SCORED=$(echo "$CR" | jq -r '[(.criterion_responses // [])[] | select(.criterion_id|test("tool_correctness|policy_adherence|faithfulness_kb")) | select(.na==false)] | length')

echo "══ asserts ══"
assert_true "avaliação produzida"                       "$([ -n "$RID" ] && echo true || echo false)"
assert_true "≥1 critério de IA pontuado (na=false)"     "$([ "${N_SCORED:-0}" -ge 1 ] && echo true || echo false)"
echo "  (detalhe — pontuou? tool_correctness=$(scored tool_correctness) policy_adherence=$(scored policy_adherence) faithfulness_kb=$(scored faithfulness_kb))"

echo
[ "$FAIL" = 0 ] && echo "✅ R6 e2e OK — avaliador de IA usou a evidência de execução (tier-2)" \
                || { echo "❌ R6 e2e com falhas"; exit 1; }
