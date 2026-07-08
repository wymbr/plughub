#!/usr/bin/env bash
#
# seed_dialog_survey_multi_form.sh — Dialog primitive: survey com DIMENSION COMPOSTA.
#
# Form `dialog_survey_multi_v1` — prova a composição de nota multi-pergunta
# (ADR adr-survey-form-scoring-composition.md):
#   - dimension `csat` (escala 1–5, weighted_mean) composta por DUAS perguntas
#     (atendimento peso 2, resolução peso 1) → UM sinal `csat` ponderado;
#   - `nps` standalone (0–10, capture.metric legado) → sinal single.
# Consumido pelo `skill_survey_multi_v1` (step loop → survey_record com
# form_id+answers=respostas; a composição roda server-side no survey_record).
#
# Uso:
#   DIALOG_API=http://localhost:3760 TENANT=tenant_demo ./infra/test/seed_dialog_survey_multi_form.sh
#
# Requer: curl, jq (jq não é usado; só curl).
set -euo pipefail

DIALOG_API="${DIALOG_API:-http://localhost:3760}"
TENANT="${TENANT:-tenant_demo}"
FORM_ID="dialog_survey_multi_v1"

echo "→ Criando DialogForm '${FORM_ID}' em ${DIALOG_API} (tenant=${TENANT})"

read -r -d '' BODY <<'JSON' || true
{
  "form_id": "dialog_survey_multi_v1",
  "name": "Survey composto — CSAT (2 perguntas) + NPS",
  "description": "CSAT composto por atendimento (peso 2) e resolução (peso 1) numa dimension ponderada; NPS standalone. Composição server-side no survey_record.",
  "default_locale": "pt-BR",
  "locales": ["pt-BR"],
  "tags": ["survey", "multi", "composed"],
  "dimensions": [
    {
      "dimension_id": "csat",
      "label": { "pt-BR": "Satisfação (CSAT)" },
      "scale": { "min": 1, "max": 5 },
      "aggregation": "weighted_mean"
    }
  ],
  "nodes": [
    {
      "id": "q_atend",
      "kind": "question",
      "prompt": { "pt-BR": "De 1 a 5, quão satisfeito você ficou com o ATENDIMENTO?" },
      "interaction": "button",
      "output_key": "atendimento",
      "capture": { "dimension_id": "csat", "weight": 2 },
      "options": [
        { "id": "1", "label": { "pt-BR": "1" } },
        { "id": "2", "label": { "pt-BR": "2" } },
        { "id": "3", "label": { "pt-BR": "3" } },
        { "id": "4", "label": { "pt-BR": "4" } },
        { "id": "5", "label": { "pt-BR": "5" } }
      ]
    },
    {
      "id": "q_resol",
      "kind": "question",
      "prompt": { "pt-BR": "De 1 a 5, quão satisfeito você ficou com a RESOLUÇÃO do seu problema?" },
      "interaction": "button",
      "output_key": "resolucao",
      "capture": { "dimension_id": "csat", "weight": 1 },
      "options": [
        { "id": "1", "label": { "pt-BR": "1" } },
        { "id": "2", "label": { "pt-BR": "2" } },
        { "id": "3", "label": { "pt-BR": "3" } },
        { "id": "4", "label": { "pt-BR": "4" } },
        { "id": "5", "label": { "pt-BR": "5" } }
      ]
    },
    {
      "id": "q_motivo",
      "kind": "question",
      "prompt": { "pt-BR": "Poxa, o que faltou no atendimento? Conta pra gente melhorar." },
      "interaction": "text",
      "output_key": "motivo",
      "ask_when": { "field": "atendimento", "op": "lt", "value": 3 }
    },
    {
      "id": "q_nps",
      "kind": "question",
      "prompt": { "pt-BR": "De 0 a 10, quanto você recomendaria nossa empresa a um amigo?" },
      "interaction": "list",
      "output_key": "nps",
      "capture": { "metric": "nps" },
      "options": [
        { "id": "0",  "label": { "pt-BR": "0" } },
        { "id": "1",  "label": { "pt-BR": "1" } },
        { "id": "2",  "label": { "pt-BR": "2" } },
        { "id": "3",  "label": { "pt-BR": "3" } },
        { "id": "4",  "label": { "pt-BR": "4" } },
        { "id": "5",  "label": { "pt-BR": "5" } },
        { "id": "6",  "label": { "pt-BR": "6" } },
        { "id": "7",  "label": { "pt-BR": "7" } },
        { "id": "8",  "label": { "pt-BR": "8" } },
        { "id": "9",  "label": { "pt-BR": "9" } },
        { "id": "10", "label": { "pt-BR": "10" } }
      ]
    }
  ]
}
JSON

curl -fsS -X POST "${DIALOG_API}/v1/dialog/forms" \
  -H 'Content-Type: application/json' -H "X-Tenant-ID: ${TENANT}" \
  -d "${BODY}" >/dev/null
echo "✓ DialogForm criado (draft): ${FORM_ID}"

curl -fsS -X POST "${DIALOG_API}/v1/dialog/forms/${FORM_ID}/publish" \
  -H "X-Tenant-ID: ${TENANT}" >/dev/null
echo "✓ DialogForm publicado: ${FORM_ID}"

echo
echo "Composição esperada (exemplo): atendimento=5, resolucao=3 →"
echo "  csat = weighted_mean([5·2, 3·1]) = (10+3)/3 ≈ 4.33 ; nps = valor cru (0–10)."
