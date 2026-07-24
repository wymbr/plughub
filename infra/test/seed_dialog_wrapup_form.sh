#!/usr/bin/env bash
#
# seed_dialog_wrapup_form.sh — Camada E2 (wrap-up-α): DialogForm de DISPOSIÇÃO do
# wrap-up (`dialog_wrapup_v1`).
#
# Form genérico (statements + botões + texto) que o DialogFormRenderer (R0) mostra
# ao agente que reivindica o item de wrap-up no inbox pull. Os valores da
# classificação casam com o mapa de outcome do wrap-up (resolvido→resolved,
# pendente→suspended, escalado→escalated, cancelado→abandoned) — usados pela tool de
# gravação `segment_outcome_record` (sub-fatia 2). O submit devolve
# payload { source:"operator", answers:{ classificacao, resumo, proximos_passos } }.
#
# Uso:
#   DIALOG_API=http://localhost:3760 TENANT=tenant_demo ./infra/test/seed_dialog_wrapup_form.sh
#
# Requer: curl.
set -euo pipefail

DIALOG_API="${DIALOG_API:-http://localhost:3760}"
TENANT="${TENANT:-tenant_demo}"
FORM_ID="dialog_wrapup_v1"

echo "→ Criando DialogForm '${FORM_ID}' em ${DIALOG_API} (tenant=${TENANT})"

read -r -d '' BODY <<'JSON' || true
{
  "form_id": "dialog_wrapup_v1",
  "name": "Wrap-up do atendimento",
  "description": "Disposição do segmento humano (wrap-up destacado). Classificação → outcome; resumo; próximos passos.",
  "default_locale": "pt-BR",
  "locales": ["pt-BR"],
  "tags": ["wrapup"],
  "nodes": [
    {
      "id": "intro",
      "kind": "statement",
      "text": { "pt-BR": "Revise a conversa ao lado e registre a disposição do seu atendimento." }
    },
    {
      "id": "classificacao",
      "kind": "question",
      "prompt": { "pt-BR": "Como este atendimento terminou?" },
      "interaction": "button",
      "output_key": "classificacao",
      "timeout_s": -1,
      "options": [
        { "id": "resolvido", "label": { "pt-BR": "Resolvido" }, "value": "resolvido" },
        { "id": "pendente",  "label": { "pt-BR": "Pendente" },  "value": "pendente" },
        { "id": "escalado",  "label": { "pt-BR": "Escalado" },  "value": "escalado" },
        { "id": "cancelado", "label": { "pt-BR": "Cancelado pelo cliente" }, "value": "cancelado" }
      ]
    },
    {
      "id": "resumo",
      "kind": "question",
      "prompt": { "pt-BR": "Resumo do atendimento" },
      "interaction": "text",
      "output_key": "resumo",
      "timeout_s": -1
    },
    {
      "id": "proximos_passos",
      "kind": "question",
      "prompt": { "pt-BR": "Próximos passos (opcional)" },
      "interaction": "text",
      "output_key": "proximos_passos",
      "timeout_s": -1
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
