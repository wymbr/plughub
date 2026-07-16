#!/usr/bin/env bash
#
# seed_dialog_promocao_deploy_form.sh — Aprovação (ADR adr-human-approval-workflow-step, A2).
#
# Form `dialog_promocao_deploy` — os CAMPOS EDITÁVEIS do pacote de aprovação do gate
# de promoção de deploy. O contexto read-only (resumo) e as DECISÕES vêm do
# delegate.context do workflow (skill_gate_promocao_v1), NÃO do form. O form carrega
# só os campos que o aprovador pode editar (form_ext) — exercita os tipos de campo
# e o valor pré-preenchido adicionados no A1 (DialogField.value/type/options).
#
# Uso:
#   DIALOG_API=http://localhost:3760 TENANT=tenant_demo ./infra/test/seed_dialog_promocao_deploy_form.sh
#
# Requer: curl, jq.
set -euo pipefail

DIALOG_API="${DIALOG_API:-http://localhost:3760}"
TENANT="${TENANT:-tenant_demo}"
FORM_ID="dialog_promocao_deploy"

echo "→ Criando DialogForm '${FORM_ID}' em ${DIALOG_API} (tenant=${TENANT})"

read -r -d '' BODY <<'JSON' || true
{
  "form_id": "dialog_promocao_deploy",
  "name": "Aprovação de promoção de deploy",
  "description": "Campos editáveis do pacote de aprovação do gate de promoção (homolog→prod). Decisões e resumo vêm do delegate.context do workflow.",
  "default_locale": "pt-BR",
  "locales": ["pt-BR"],
  "tags": ["approval"],
  "nodes": [
    {
      "id": "instrucoes",
      "kind": "statement",
      "text": { "pt-BR": "Revise a promoção acima e registre sua decisão. Você pode anexar uma justificativa." }
    },
    {
      "id": "campos",
      "kind": "question",
      "prompt": { "pt-BR": "Dados da aprovação" },
      "interaction": "form",
      "output_key": "campos",
      "timeout_s": -1,
      "fields": [
        {
          "id": "nota",
          "label": { "pt-BR": "Justificativa (opcional)" },
          "type": "text",
          "required": false
        },
        {
          "id": "notificar_equipe",
          "label": { "pt-BR": "Notificar a equipe após promover" },
          "type": "bool",
          "required": false,
          "value": "false"
        }
      ]
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
echo "Verifique o render (form_get expande os fields[]):"
echo "  curl -s '${DIALOG_API}/v1/dialog/forms/${FORM_ID}?status=published' -H 'X-Tenant-ID: ${TENANT}' | jq ."
