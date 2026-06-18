#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T7b-2 (proxy) — prova o conveyance form-driven SEM depender do replayer/routing.
#
# Constrói o MESMO JSON Schema que buildEvaluationOutputSchema (mcp-server) deriva
# de um form de 2 critérios (clareza, resolucao; envelope criterion_responses[] com
# score nullable ["number","null"], na, justification, evidence) e chama /v1/reason
# direto. Prova: form→schema→tool-use→criterion_responses CONFORME por construção,
# incluindo o score nullable (o ponto mais arriscado p/ o provedor).
#
# Requer ai-gateway (:3200) com chave Anthropic. Requer jq.
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail
GW="${GW:-http://localhost:3200}"
CURL="curl -s --max-time 60"
FAIL=0
assert_true() { if [ "$2" = "true" ]; then echo "  ✓ $1"; else echo "  ✗ $1 (=$2)"; FAIL=1; fi; }

echo "══ aguardando ai-gateway ══"
for i in $(seq 1 30); do $CURL "$GW/v1/health" >/dev/null 2>&1 && { echo "  ✓ no ar"; break; }; \
  [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

# Schema idêntico ao buildEvaluationOutputSchema p/ form {clareza, resolucao}, max 10.
read -r -d '' BODY <<'JSON'
{
  "session_id": "t7b2_proxy",
  "prompt_id": "evaluation_rubric_v3",
  "input": {
    "transcript": "Cliente: Minha internet está lenta há 3 dias e ninguém resolveu. Agente: Entendo o transtorno. Verifiquei sua linha, identifiquei instabilidade e fiz a reconfiguração agora — pode testar? Cliente: Testei e melhorou bastante, obrigado!",
    "instruction": "Avalie os critérios do formulário de 0 a 10 com justificativa e evidência."
  },
  "output_schema": {},
  "model_profile": "balanced",
  "json_schema": {
    "type": "object",
    "required": ["criterion_responses"],
    "properties": {
      "criterion_responses": {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["criterion_id", "score", "justification"],
          "properties": {
            "criterion_id":  { "type": "string", "enum": ["clareza", "resolucao"] },
            "score":         { "type": ["number", "null"], "minimum": 0, "maximum": 10 },
            "na":            { "type": "boolean" },
            "justification": { "type": "string" },
            "evidence": {
              "type": "array",
              "items": {
                "type": "object",
                "required": ["stream_entry_id"],
                "properties": {
                  "stream_entry_id": { "type": "string" },
                  "excerpt":         { "type": "string" },
                  "relevance_note":  { "type": "string" }
                }
              }
            }
          }
        }
      },
      "overall_observation": { "type": "string" },
      "highlights":          { "type": "array", "items": { "type": "string" } },
      "improvement_points":  { "type": "array", "items": { "type": "string" } }
    }
  }
}
JSON

echo "══ POST /v1/reason (schema do form via tool-use) ══"
R=$($CURL -X POST "$GW/v1/reason" -H 'Content-Type: application/json' -d "$BODY")
echo "$R" | jq . >/dev/null 2>&1 || { echo "  ✗ resposta não-JSON:"; echo "$R"; exit 1; }
if [ "$(echo "$R" | jq -r 'has("result")')" != "true" ]; then
  echo "  ✗ sem 'result' (chave Anthropic ausente? erro?). Resposta:"; echo "$R" | jq .; exit 1
fi

RES=$(echo "$R" | jq '.result')
echo "$RES" | jq -c '{criterion_responses, has_obs: (.overall_observation|type=="string")}'

echo "══ asserts ══"
assert_true "criterion_responses é array"          "$(echo "$RES" | jq -r '.criterion_responses|type=="array"')"
assert_true "≥1 critério avaliado"                 "$(echo "$RES" | jq -r '(.criterion_responses|length)>=1')"
assert_true "criterion_id ∈ {clareza,resolucao}"   "$(echo "$RES" | jq -r '[.criterion_responses[].criterion_id]|all(. as $x|["clareza","resolucao"]|index($x))')"
assert_true "score number(0..10) OU null (nullable)" \
  "$(echo "$RES" | jq -r '[.criterion_responses[].score]|all(.==null or (type=="number" and .>=0 and .<=10))')"
assert_true "justification string não-vazia"       "$(echo "$RES" | jq -r '[.criterion_responses[].justification]|all(type=="string" and length>0)')"

echo
[ "$FAIL" = 0 ] && echo "✅ T7b-2 (proxy) OK — envelope form-driven via tool-use conforme (inclui score nullable)" \
                || { echo "❌ T7b-2 (proxy) com falhas"; exit 1; }
