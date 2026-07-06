#!/usr/bin/env bash
#
# seed_dialog_survey_multi_form.sh — Dialog primitive (loop): survey MULTI-pergunta.
#
# Form `dialog_survey_multi_v1` — N perguntas numéricas (CSAT + CES), consumidas
# SEQUENCIALMENTE pelo step `loop` (skill_survey_multi_v1). Prova o loop no engine:
# o runner caminha uma pergunta por vez (canal pobre), acumula {metric,value} e
# grava tudo num survey_record. Cada pergunta tem capture.metric (obrigatório p/
# o survey_record) e valor numérico.
#
# Uso:
#   DIALOG_API=http://localhost:3760 TENANT=tenant_demo ./infra/test/seed_dialog_survey_multi_form.sh
#
# Requer: curl, jq.
set -euo pipefail

DIALOG_API="${DIALOG_API:-http://localhost:3760}"
TENANT="${TENANT:-tenant_demo}"
FORM_ID="dialog_survey_multi_v1"

echo "→ Criando DialogForm '${FORM_ID}' em ${DIALOG_API} (tenant=${TENANT})"

read -r -d '' BODY <<'JSON' || true
{
  "form_id": "dialog_survey_multi_v1",
  "name": "Survey multi-pergunta (CSAT + CES)",
  "description": "Duas perguntas numéricas sequenciais — consumidas pelo step loop (uma por turno).",
  "default_locale": "pt-BR",
  "locales": ["pt-BR"],
  "tags": ["survey", "multi"],
  "nodes": [
    {
      "id": "q_csat",
      "kind": "question",
      "prompt": { "pt-BR": "De 1 a 5, quão satisfeito você ficou com o atendimento?" },
      "interaction": "button",
      "output_key": "csat",
      "capture": { "metric": "csat" },
      "options": [
        { "id": "1", "label": { "pt-BR": "1" } },
        { "id": "2", "label": { "pt-BR": "2" } },
        { "id": "3", "label": { "pt-BR": "3" } },
        { "id": "4", "label": { "pt-BR": "4" } },
        { "id": "5", "label": { "pt-BR": "5" } }
      ]
    },
    {
      "id": "q_ces",
      "kind": "question",
      "prompt": { "pt-BR": "De 1 a 7, quão fácil foi resolver seu problema?" },
      "interaction": "list",
      "output_key": "ces",
      "capture": { "metric": "ces" },
      "options": [
        { "id": "1", "label": { "pt-BR": "1" } },
        { "id": "2", "label": { "pt-BR": "2" } },
        { "id": "3", "label": { "pt-BR": "3" } },
        { "id": "4", "label": { "pt-BR": "4" } },
        { "id": "5", "label": { "pt-BR": "5" } },
        { "id": "6", "label": { "pt-BR": "6" } },
        { "id": "7", "label": { "pt-BR": "7" } }
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
