#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T7b-2 (e2e) — avaliador real form-driven via tool-use, SELF-CONTAINED.
#
# Semeia um ReplayContext completo no Redis (transcript + form), cria instance,
# dá dispatch e inspeciona o resultado. Bypassa a re-hidratação (que depende de
# stream persistido de sessão real) → o avaliador roda o caminho real:
#   evaluation_context_get (lê o seed) → reason (tool-use c/ schema do form, T7b-1/2)
#   → evaluation_submit → ingest (recomputa nota + threads por critério, T7a).
#
# Prova checada: overall_score recomputado ≈ média dos critérios; criterion_responses
# com score; threads round-1 evaluator_ai. Conveyance (tool_use) → logs ai-gateway.
#
# Pré: stack demo no ar com o pool avaliador (avaliacao_ia) operante. Requer jq.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail

EVAL="${EVAL:-http://localhost:3400}"
TENANT="${TENANT:-tenant_demo}"
ADMIN="${ADMIN:-changeme_eval_admin_token_demo}"
EVALUATOR_POOL="${EVALUATOR_POOL:-avaliacao_ia}"
EVAL_POOL_ID="${EVAL_POOL_ID:-retencao_humano}"
COMPOSE="docker compose -f docker-compose.demo.yml"
CURL="curl -s --max-time 20"
JSON='-H Content-Type:application/json'
FAIL=0
assert_true() { if [ "$2" = "true" ]; then echo "  ✓ $1"; else echo "  ✗ $1 (=$2)"; FAIL=1; fi; }
uuid() { cat /proc/sys/kernel/random/uuid; }

echo "══ aguardando evaluation-api ══"
for i in $(seq 1 30); do $CURL "$EVAL/health" >/dev/null 2>&1 && { echo "  ✓ no ar"; break; }; \
  [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

SID="sess-t7b2-$(date +%s)"
echo "══ setup: form (2 critérios score) + campanha + instance · session=$SID ══"
F=$($CURL -X POST "$EVAL/v1/evaluation/forms" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t7b2_e2e\",\"dimensions\":[
    {\"dimension_id\":\"d1\",\"name\":\"Atendimento\",\"weight\":1,\"criteria\":[
      {\"criterion_id\":\"clareza\",\"label\":\"Clareza\",\"type\":\"score\",\"weight\":1,\"max_score\":10,\"description\":\"O agente foi claro?\"},
      {\"criterion_id\":\"resolucao\",\"label\":\"Resolução\",\"type\":\"score\",\"weight\":1,\"max_score\":10,\"description\":\"O problema foi resolvido?\"}
    ]}]}" | jq -r '.form_id // .id // empty')
[ -n "$F" ] || { echo "  ✗ form falhou"; exit 1; }
$CURL -X POST "$EVAL/v1/evaluation/forms/$F/publish?tenant_id=$TENANT" $JSON -d '{"published_by":"e2e"}' >/dev/null
FORM_JSON=$($CURL "$EVAL/v1/evaluation/forms/$F?tenant_id=$TENANT")

C=$($CURL -X POST "$EVAL/v1/evaluation/campaigns" $JSON -d "{
  \"tenant_id\":\"$TENANT\",\"name\":\"t7b2_camp\",\"form_id\":\"$F\",
  \"pool_id\":\"$EVAL_POOL_ID\",\"evaluation_pool_id\":\"$EVAL_POOL_ID\",\"evaluator_pool\":\"$EVALUATOR_POOL\"}" \
  | jq -r '.campaign_id // .id // empty')
[ -n "$C" ] || { echo "  ✗ campaign falhou"; exit 1; }

INST=$($CURL -X POST "$EVAL/v1/evaluation/instances" $JSON \
  -d "{\"tenant_id\":\"$TENANT\",\"campaign_id\":\"$C\",\"session_id\":\"$SID\"}" \
  | jq -r '.id // .instance_id // empty')
[ -n "$INST" ] || { echo "  ✗ instance falhou"; exit 1; }
echo "  form=$F campaign=$C instance=$INST"

echo "══ semeando ReplayContext no Redis ══"
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
E1=$(uuid); E2=$(uuid); E3=$(uuid)
CTX=$(jq -nc \
  --argjson form "$FORM_JSON" \
  --arg sid "$SID" --arg cid "$C" --arg iid "$INST" --arg now "$NOW" \
  --arg e1 "$E1" --arg e2 "$E2" --arg e3 "$E3" '
{
  session_id: $sid, tenant_id: "tenant_demo",
  evaluation_form: $form, campaign_id: $cid, instance_id: $iid,
  comparison_mode: false,
  session_meta: { session_id:$sid, outcome:"resolved", channel:"webchat",
                  agent_type_id:"agente_humano_demo", started_at:$now, closed_at:$now, duration_ms:180000 },
  participants: [ { participant_id:"p_agent", role:"primary", agent_type_id:"agente_humano_demo" },
                  { participant_id:"p_cust",  role:"customer" } ],
  sentiment: [ {score:0.1}, {score:0.4}, {score:0.8} ],
  knowledge_snippets: [],
  events: [
    { event_id:$e1, type:"message", turn_index:0, author_role:"customer", author_id:"p_cust",
      content:"Minha internet está lenta há 3 dias e ninguém resolveu.",
      original_content:"Minha internet está lenta há 3 dias e ninguém resolveu." },
    { event_id:$e2, type:"message", turn_index:1, author_role:"agent", author_id:"p_agent",
      content:"Entendo o transtorno. Verifiquei sua linha, identifiquei instabilidade no sinal e fiz a reconfiguração agora. Pode testar, por favor?",
      original_content:"Entendo o transtorno. Verifiquei sua linha, identifiquei instabilidade no sinal e fiz a reconfiguração agora. Pode testar, por favor?" },
    { event_id:$e3, type:"message", turn_index:2, author_role:"customer", author_id:"p_cust",
      content:"Testei e melhorou bastante, obrigado!",
      original_content:"Testei e melhorou bastante, obrigado!" }
  ]
}')
printf '%s' "$CTX" | $COMPOSE exec -T redis redis-cli -x SET "tenant_demo:replay:${SID}:context" >/dev/null
$COMPOSE exec -T redis redis-cli EXPIRE "tenant_demo:replay:${SID}:context" 3600 >/dev/null
echo "  ✓ ReplayContext semeado (EXISTS=$($COMPOSE exec -T redis redis-cli EXISTS "tenant_demo:replay:${SID}:context" | tr -d '\r'))"

echo "══ dispatch ══"
D=$($CURL -X POST "$EVAL/v1/evaluation/campaigns/$C/dispatch?tenant_id=$TENANT" \
  -H "X-Admin-Token: $ADMIN" $JSON -d '{}')
echo "  $(echo "$D" | jq -c '{dispatched, evaluator_pool}' 2>/dev/null || echo "$D")"

echo "══ aguardando o avaliador (até 150s) ══"
RID=""
for i in $(seq 1 50); do
  RID=$($CURL "$EVAL/v1/evaluation/results?tenant_id=$TENANT&session_id=$SID&limit=20" \
        | jq -r '(.results // .data // [])[0].id // empty')
  [ -n "$RID" ] && { echo "  ✓ result=$RID após ~$((i*3))s"; break; }
  sleep 3
done
if [ -z "$RID" ]; then
  echo "  ✗ sem resultado. instance status:"
  $CURL "$EVAL/v1/evaluation/instances/$INST?tenant_id=$TENANT" | jq -c '{status}' 2>/dev/null || true
  echo "  logs: $COMPOSE logs --since 5m skill-flow-service | tail -40"
  echo "        $COMPOSE logs --since 5m mcp-server-plughub | tail -60"
  exit 1
fi

# ── inspeção ──────────────────────────────────────────────────────────────────
RES_ONE=$($CURL "$EVAL/v1/evaluation/results/$RID?tenant_id=$TENANT")
OVERALL=$(echo "$RES_ONE" | jq -r '.overall_score // empty')
CR=$($CURL "$EVAL/v1/evaluation/results/$RID/criteria?tenant_id=$TENANT")
N_SCORED=$(echo "$CR" | jq -r '[(.criterion_responses // [])[] | select(.score != null)] | length')
MEAN=$(echo "$CR" | jq -r '[(.criterion_responses // [])[] | select(.score != null) | .score] | if length>0 then (add/length) else -1 end')
TH=$($CURL "$EVAL/v1/evaluation/instances/$INST/threads" -H "X-Tenant-ID: $TENANT")
N_EV=$(echo "$TH" | jq -r '[(.threads // [])[] | select(.author_type=="evaluator_ai" and .round==1)] | length')

echo "══ resultado ══"
echo "  overall_score=$OVERALL · critérios com score=$N_SCORED · média=$MEAN · threads evaluator_ai=$N_EV"
echo "$CR" | jq -c '[(.criterion_responses // [])[] | {criterion_id, score, na}]'

echo "══ asserts ══"
assert_true "tem overall_score"                       "$([ -n "$OVERALL" ] && echo true || echo false)"
assert_true "≥1 critério com score (saída tool-use)"  "$([ "${N_SCORED:-0}" -ge 1 ] && echo true || echo false)"
assert_true "threads round-1 evaluator_ai por critério" "$([ "${N_EV:-0}" -ge 1 ] && echo true || echo false)"
assert_true "overall ≈ média dos critérios (recomputado do form)" \
  "$(awk -v o="${OVERALL:-0}" -v m="${MEAN:-0}" 'BEGIN{d=o-m; if(d<0)d=-d; print (m>=0 && d<=1.5)?"true":"false"}')"

echo
echo "  → conveyance (tool_use) nos logs do ai-gateway:"
echo "    $COMPOSE logs --since 3m ai-gateway | grep -iE 'reason|tool' | tail"
echo
[ "$FAIL" = 0 ] && echo "✅ T7b-2 e2e OK — avaliador form-driven via tool-use" \
                || { echo "❌ T7b-2 e2e com falhas"; exit 1; }
