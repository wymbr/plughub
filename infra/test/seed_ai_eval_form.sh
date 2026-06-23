#!/usr/bin/env bash
#
# seed_ai_eval_form.sh — R6: semente do formulário "Avaliação de IA".
#
# Provisiona, VIA API OFICIAL (invariante de provisioning), um EvaluationForm com
# as três dimensões qualitativas de avaliação de IA (tier-2):
#   - tool_correctness  → usa tool_trace (chamadas reais de ferramenta)
#   - policy_adherence  → usa flow_definition (esperada) × actual_trajectory (real)
#   - faithfulness_kb   → usa knowledge_snippets (KB) como ground-truth
#
# Os três são critérios type=score (fluem para o output-schema do avaliador via
# buildEvaluationOutputSchema). O scoring_guidance instrui o LLM a usar a evidência
# de execução e a marcar `na` quando ela estiver ausente (decisão D).
#
# Uso:
#   EVAL_API=http://localhost:3400 TENANT=tenant_demo ./infra/test/seed_ai_eval_form.sh
#
# Requer: curl, jq.
set -euo pipefail

EVAL_API="${EVAL_API:-http://localhost:3400}"
TENANT="${TENANT:-tenant_demo}"
CREATED_BY="${CREATED_BY:-seed_ai_eval_form}"

echo "→ Criando form 'Avaliação de IA' em ${EVAL_API} (tenant=${TENANT})"

read -r -d '' BODY <<JSON || true
{
  "tenant_id": "${TENANT}",
  "name": "Avaliação de IA (tier-2)",
  "description": "Formulário semente para avaliar agentes de IA: tool correctness, policy adherence e faithfulness-vs-KB. Critérios marcam-se na quando a evidência de execução está ausente.",
  "scoring_method": "weighted_average",
  "min_passing_score": 7.0,
  "knowledge_domains": ["evaluation_policies"],
  "created_by": "${CREATED_BY}",
  "dimensions": [
    {
      "dimension_id": "ia_qualidade",
      "name": "Qualidade de IA",
      "description": "Dimensões específicas de avaliação de agentes de IA (não-humanos).",
      "weight": 1.0,
      "aggregation": "weighted_average",
      "criteria": [
        {
          "criterion_id": "tool_correctness",
          "dimension_id": "ia_qualidade",
          "label": "Tool correctness",
          "type": "score",
          "weight": 1.0,
          "max_score": 10,
          "min_score": 0,
          "na_allowed": true,
          "required": true,
          "evidence_required": true,
          "applies_when": "o agente avaliado é uma IA (não humano)",
          "question": "O agente usou as ferramentas certas, com chamadas válidas e recuperando de falhas?",
          "scoring_guidance": "Baseie-se em tool_trace (chamadas mcp.tool_call reais da sessão): a ferramenta certa foi chamada para a tarefa? allowed=false ou injection_detected=true indicam erro grave. Avalie ordem e recuperação de falhas. Se tool_trace estiver vazio, marque na=true (sem evidência de execução — não penalize)."
        },
        {
          "criterion_id": "policy_adherence",
          "dimension_id": "ia_qualidade",
          "label": "Policy adherence",
          "type": "score",
          "weight": 1.0,
          "max_score": 10,
          "min_score": 0,
          "na_allowed": true,
          "required": true,
          "evidence_required": true,
          "applies_when": "o agente avaliado é uma IA com skill-flow (há flow_definition e actual_trajectory)",
          "question": "O agente seguiu a trajetória esperada do skill-flow / política de negócio?",
          "scoring_guidance": "Compare actual_trajectory (pipeline_state.transitions reais) com flow_definition (steps esperados do flow). Desvios não justificados, loops ou pulos de steps obrigatórios reduzem a nota. Se actual_trajectory for null ou flow_definition ausente, marque na=true (decisão D)."
        },
        {
          "criterion_id": "faithfulness_kb",
          "dimension_id": "ia_qualidade",
          "label": "Faithfulness (vs base de conhecimento)",
          "type": "score",
          "weight": 1.0,
          "max_score": 10,
          "min_score": 0,
          "na_allowed": true,
          "required": true,
          "evidence_required": true,
          "applies_when": "o agente avaliado é uma IA",
          "question": "As afirmações do agente são sustentadas pela base de conhecimento (sem alucinação)?",
          "scoring_guidance": "Compare as afirmações factuais do agente (transcript) com knowledge_snippets (KB). Afirmações não sustentadas pela KB = alucinação → nota baixa. Citar a política/snippet correto eleva a nota. Se não houver snippets relevantes para checar uma afirmação, restrinja o julgamento ao que é verificável; se nada for verificável, na=true."
        }
      ]
    }
  ]
}
JSON

RESP="$(curl -fsS -X POST "${EVAL_API}/v1/evaluation/forms" \
  -H 'Content-Type: application/json' \
  -d "${BODY}")"

FORM_ID="$(printf '%s' "${RESP}" | jq -r '.form_id')"
echo "✓ Form criado: form_id=${FORM_ID}"

echo "→ Publicando form (torna imutável e usável por campanha)"
curl -fsS -X POST "${EVAL_API}/v1/evaluation/forms/${FORM_ID}/publish?tenant_id=${TENANT}" \
  -H 'Content-Type: application/json' \
  -d "{\"published_by\":\"${CREATED_BY}\"}" >/dev/null
echo "✓ Form publicado: ${FORM_ID}"
echo
echo "Próximo passo: associe este form a uma campanha apontando um pool de IA,"
echo "ex.: POST ${EVAL_API}/v1/evaluation/campaigns com form_id=${FORM_ID}."
