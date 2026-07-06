#!/usr/bin/env bash
#
# seed_dialog_nps_form.sh — Dialog primitive (Fatia 2): DialogForm de NPS do survey.
#
# Provisiona, VIA API OFICIAL do dialog-api, o form `dialog_nps_v1` — o conteúdo
# (agradecimento + pergunta NPS) que o dialog-runner renderiza no fluxo de survey.
# O survey passa a ser o 2º consumidor do primitivo (o OTP foi o 1º).
#
# Nota v1: a pergunta usa `interaction: text` (o cliente digita 0–10). Botões/lista
# de NPS exigem campos `choice` com opções no render+adapter (refinamento Fatia 2+).
#
# Uso:
#   DIALOG_API=http://localhost:3760 TENANT=tenant_demo ./infra/test/seed_dialog_nps_form.sh
#
# Requer: curl, jq.
set -euo pipefail

DIALOG_API="${DIALOG_API:-http://localhost:3760}"
TENANT="${TENANT:-tenant_demo}"
FORM_ID="dialog_nps_v1"

echo "→ Criando DialogForm '${FORM_ID}' em ${DIALOG_API} (tenant=${TENANT})"

read -r -d '' BODY <<JSON || true
{
  "form_id": "${FORM_ID}",
  "name": "Survey — NPS de sessão",
  "description": "Agradecimento + coleta do NPS (0–10) pós-atendimento. Consumido pelo dialog-runner via delegate no fluxo de reconexão do survey.",
  "default_locale": "pt-BR",
  "locales": ["pt-BR"],
  "tags": ["survey", "nps"],
  "nodes": [
    {
      "id": "agradecer",
      "kind": "statement",
      "text": { "pt-BR": "Obrigado pelo seu contato! Gostaríamos de saber como foi sua experiência." }
    },
    {
      "id": "coletar_nps",
      "kind": "question",
      "prompt": { "pt-BR": "Em uma escala de 0 a 10, qual a probabilidade de você recomendar nosso atendimento? (digite o número)" },
      "interaction": "text",
      "output_key": "nps",
      "capture": { "metric": "nps" },
      "validation": { "numeric": true, "min": 0, "max": 10 },
      "timeout_s": 600
    }
  ]
}
JSON

curl -fsS -X POST "${DIALOG_API}/v1/dialog/forms" \
  -H 'Content-Type: application/json' \
  -H "X-Tenant-ID: ${TENANT}" \
  -d "${BODY}" >/dev/null
echo "✓ DialogForm criado (draft): ${FORM_ID}"

echo "→ Publicando"
curl -fsS -X POST "${DIALOG_API}/v1/dialog/forms/${FORM_ID}/publish" \
  -H "X-Tenant-ID: ${TENANT}" >/dev/null
echo "✓ DialogForm publicado: ${FORM_ID}"
echo
echo "Verifique: curl -s '${DIALOG_API}/v1/dialog/forms/${FORM_ID}?status=published' -H 'X-Tenant-ID: ${TENANT}' | jq ."
