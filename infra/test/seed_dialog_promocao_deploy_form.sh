#!/usr/bin/env bash
#
# seed_dialog_promocao_deploy_form.sh — Aprovação (ADR adr-human-approval-workflow-step, A2).
#
# Form `dialog_promocao_deploy` — os CAMPOS EDITÁVEIS do pacote de aprovação do gate
# de promoção de deploy. O contexto read-only (resumo) e as DECISÕES vêm do
# delegate.context do workflow (skill_gate_promocao_v1), NÃO do form. O form carrega
# só os campos que o aprovador pode editar (form_ext).
#
# FONTE ÚNICA: o JSON vive em `infra/dialog/dialog_promocao_deploy.json` e é semeado
# no boot pelo `dialog-seed` (seed-if-absent). Este script é o atalho manual.
#
# Uso:
#   DIALOG_API=http://localhost:3760 TENANT=tenant_demo ./infra/test/seed_dialog_promocao_deploy_form.sh
#
# Requer: curl.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIALOG_API="${DIALOG_API:-http://localhost:3760}"
DIALOG_ADMIN_TOKEN="${DIALOG_ADMIN_TOKEN:-demo_dialog_admin_token}"   # portao de escrita (sistema)
TENANT="${TENANT:-tenant_demo}"
FORM_ID="dialog_promocao_deploy"
FORM_FILE="${REPO_ROOT}/infra/dialog/${FORM_ID}.json"

[ -f "$FORM_FILE" ] || { echo "✗ arquivo do form não encontrado: $FORM_FILE" >&2; exit 1; }

echo "→ Criando DialogForm '${FORM_ID}' em ${DIALOG_API} (tenant=${TENANT}) a partir de ${FORM_FILE#"$REPO_ROOT"/}"

curl -fsS -X POST -H "X-Admin-Token: ${DIALOG_ADMIN_TOKEN}" "${DIALOG_API}/v1/dialog/forms" \
  -H 'Content-Type: application/json' \
  -H "X-Tenant-ID: ${TENANT}" \
  --data-binary @"${FORM_FILE}" >/dev/null
echo "✓ DialogForm criado (draft): ${FORM_ID}"

echo "→ Publicando"
curl -fsS -X POST -H "X-Admin-Token: ${DIALOG_ADMIN_TOKEN}" "${DIALOG_API}/v1/dialog/forms/${FORM_ID}/publish" \
  -H "X-Tenant-ID: ${TENANT}" >/dev/null
echo "✓ DialogForm publicado: ${FORM_ID}"
echo
echo "Verifique o render (form_get expande os fields[]):"
echo "  curl -s '${DIALOG_API}/v1/dialog/forms/${FORM_ID}?status=published' -H 'X-Tenant-ID: ${TENANT}' | jq ."
