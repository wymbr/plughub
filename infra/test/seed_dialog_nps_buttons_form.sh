#!/usr/bin/env bash
#
# seed_dialog_nps_buttons_form.sh — Dialog primitive (Fatia 2b): NPS de fim-de-contato.
#
# Form `dialog_nps_buttons` — o conteúdo do NPS ATIVO (hook on_contact_end): botões
# 0-10 (interaction=list) + visibilidade customer-only (o agente humano não vê o NPS).
# Renderizado pelo dialog_runner via delegate no agente_nps_v1. O runner usa a
# interação/opções/visibilidade nativas do form (§17.4 — dinâmicos no engine).
#
# Uso:
#   DIALOG_API=http://localhost:3760 TENANT=tenant_demo ./infra/test/seed_dialog_nps_buttons_form.sh
#
# Requer: curl, jq.
set -euo pipefail

DIALOG_API="${DIALOG_API:-http://localhost:3760}"
TENANT="${TENANT:-tenant_demo}"
FORM_ID="dialog_nps_buttons"

echo "→ Criando DialogForm '${FORM_ID}' em ${DIALOG_API} (tenant=${TENANT})"

read -r -d '' BODY <<'JSON' || true
{
  "form_id": "dialog_nps_buttons",
  "name": "NPS de fim-de-contato (botões)",
  "description": "Agradecimento + NPS 0-10 por botões, customer-only. Conteúdo do NPS ativo (hook on_contact_end) renderizado pelo dialog-runner.",
  "default_locale": "pt-BR",
  "locales": ["pt-BR"],
  "tags": ["survey", "nps"],
  "nodes": [
    {
      "id": "agradecer",
      "kind": "statement",
      "text": { "pt-BR": "Obrigado por entrar em contato conosco! 🙏 Foi um prazer te atender." }
    },
    {
      "id": "coletar_nps",
      "kind": "question",
      "prompt": { "pt-BR": "Em uma escala de 0 a 10, qual a probabilidade de você recomendar nosso atendimento para um amigo ou familiar?" },
      "interaction": "list",
      "output_key": "nps",
      "capture": { "metric": "nps" },
      "visibility": ["@ctx.session.customer_participant_id"],
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
      ],
      "timeout_s": 30
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
