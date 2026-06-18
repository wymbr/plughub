#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# T7b-1 — ai-gateway: reason aceita JSON Schema e usa tool-use nativo (§5.4).
#
# POSTa /v1/reason com `json_schema` (montado como seria a partir de um form) e
# verifica que o resultado vem CONFORME por construção: criterion_responses[] com
# criterion_id ∈ enum, score 0..10 e justification string. (Conteúdo é do LLM e
# não-determinístico; aqui validamos o SHAPE, que é o que o tool-use garante.)
#
# Requer ai-gateway no ar (:3200) COM chave Anthropic (PLUGHUB_ANTHROPIC_API_KEY).
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail

GW="${GW:-http://localhost:3200}"
CURL="curl -s --max-time 60"
FAIL=0
assert() { if [ "$2" = "$3" ]; then echo "  ✓ $1 = $3"; else echo "  ✗ $1: esperado [$2], veio [$3]"; FAIL=1; fi; }
assert_true() { if [ "$2" = "true" ]; then echo "  ✓ $1"; else echo "  ✗ $1 (veio: $2)"; FAIL=1; fi; }

echo "══ aguardando ai-gateway ══"
for i in $(seq 1 30); do $CURL "$GW/v1/health" >/dev/null 2>&1 && { echo "  ✓ no ar"; break; }; \
  [ "$i" = 30 ] && { echo "  ✗ timeout"; exit 1; }; sleep 1; done

read -r -d '' BODY <<'JSON'
{
  "session_id": "t7b1_test",
  "prompt_id": "test",
  "input": {
    "transcript": "Cliente: minha TV parou. Agente: vou verificar... reiniciei o sinal e voltou. Cliente: resolvido, obrigado!",
    "instruction": "Avalie clareza e resolução de 0 a 10, com justificativa."
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
            "criterion_id": { "type": "string", "enum": ["clareza", "resolucao"] },
            "score":        { "type": "number", "minimum": 0, "maximum": 10 },
            "justification":{ "type": "string" }
          }
        }
      }
    }
  }
}
JSON

echo "══ POST /v1/reason (tool-use, json_schema) ══"
R=$($CURL -X POST "$GW/v1/reason" -H 'Content-Type: application/json' -d "$BODY")
echo "$R" | jq . >/dev/null 2>&1 || { echo "  ✗ resposta não-JSON:"; echo "$R"; exit 1; }

# Se faltou chave LLM, o gateway devolve erro — mostra cru.
if [ "$(echo "$R" | jq -r 'has("result")')" != "true" ]; then
  echo "  ✗ sem 'result' (chave Anthropic ausente? erro?). Resposta:"; echo "$R" | jq .; exit 1
fi

RES=$(echo "$R" | jq '.result')
assert_true "result.criterion_responses é array" "$(echo "$RES" | jq -r '.criterion_responses | type=="array"')"
assert_true "≥1 critério avaliado"               "$(echo "$RES" | jq -r '(.criterion_responses|length) >= 1')"
assert_true "todo criterion_id ∈ {clareza,resolucao}" \
  "$(echo "$RES" | jq -r '[.criterion_responses[].criterion_id] | all(. as $x | ["clareza","resolucao"]|index($x))')"
assert_true "todo score é número em 0..10" \
  "$(echo "$RES" | jq -r '[.criterion_responses[].score] | all(type=="number" and . >= 0 and . <= 10)')"
assert_true "toda justification é string não-vazia" \
  "$(echo "$RES" | jq -r '[.criterion_responses[].justification] | all(type=="string" and length>0)')"

echo "  → resultado:"; echo "$RES" | jq -c '.criterion_responses'
echo
[ "$FAIL" = 0 ] && echo "✅ T7b-1 OK — reason form-driven via tool-use (shape garantido)" \
                || { echo "❌ T7b-1 com falhas"; exit 1; }
