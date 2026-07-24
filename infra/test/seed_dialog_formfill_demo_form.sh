#!/usr/bin/env bash
#
# seed_dialog_formfill_demo_form.sh — Renderer genérico de collect-form (R0,
# kickoff docs/product/approval-renderer-kickoff.md).
#
# Form GENÉRICO de disposição `dialog_formfill_demo` — statements + 1 question de
# BOTÕES + 1 question de TEXTO. Sem interaction:"form"/decisions[]: exercita o
# caminho GENÉRICO do <DialogFormRenderer> (NÃO o ApprovalPanel). O submit devolve
# payload { source:"operator", answers:{ satisfacao, observacao } } → o workflow lê
# $.pipeline_state.<delegate>.answers.satisfacao.
#
# Uso:
#   DIALOG_API=http://localhost:3760 TENANT=tenant_demo ./infra/test/seed_dialog_formfill_demo_form.sh
#
# Requer: curl, jq.
set -euo pipefail

DIALOG_API="${DIALOG_API:-http://localhost:3760}"
TENANT="${TENANT:-tenant_demo}"
FORM_ID="dialog_formfill_demo"

echo "→ Criando DialogForm '${FORM_ID}' em ${DIALOG_API} (tenant=${TENANT})"

read -r -d '' BODY <<'JSON' || true
{
  "form_id": "dialog_formfill_demo",
  "name": "Disposição do atendimento",
  "description": "Form genérico de collect-form no Console (R0). Statements + botões + texto, sem decisions[]/form-fields — exercita o caminho genérico do DialogFormRenderer.",
  "default_locale": "pt-BR",
  "locales": ["pt-BR"],
  "tags": ["formfill"],
  "nodes": [
    {
      "id": "intro",
      "kind": "statement",
      "text": { "pt-BR": "Revise a conversa ao lado e registre a disposição deste atendimento." }
    },
    {
      "id": "satisfacao",
      "kind": "question",
      "prompt": { "pt-BR": "O contato foi resolvido?" },
      "interaction": "button",
      "output_key": "satisfacao",
      "timeout_s": -1,
      "options": [
        { "id": "sim",     "label": { "pt-BR": "Resolvido" },     "value": "sim" },
        { "id": "nao",     "label": { "pt-BR": "Não resolvido" }, "value": "nao" },
        { "id": "escalar", "label": { "pt-BR": "Escalado" },      "value": "escalar" }
      ]
    },
    {
      "id": "observacao",
      "kind": "question",
      "prompt": { "pt-BR": "Observações / próximos passos" },
      "interaction": "text",
      "output_key": "observacao",
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
echo
echo "Verifique o render (form_get expande o form):"
echo "  curl -s '${DIALOG_API}/v1/dialog/forms/${FORM_ID}?status=published' -H 'X-Tenant-ID: ${TENANT}' | jq ."
