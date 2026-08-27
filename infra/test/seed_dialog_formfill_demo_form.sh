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
# FONTE ÚNICA: o JSON vive em `infra/dialog/dialog_formfill_demo.json` e é semeado
# no boot pelo `dialog-seed` (seed-if-absent). Este script é o atalho manual.
#
# Uso:
#   DIALOG_API=http://localhost:3760 TENANT=tenant_demo ./infra/test/seed_dialog_formfill_demo_form.sh
#
# Requer: curl.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIALOG_API="${DIALOG_API:-http://localhost:3760}"
DIALOG_ADMIN_TOKEN="${DIALOG_ADMIN_TOKEN:-demo_dialog_admin_token}"   # portao de escrita (sistema)
TENANT="${TENANT:-tenant_demo}"
FORM_ID="dialog_formfill_demo"
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
echo "Verifique o render (form_get expande o form):"
echo "  curl -s '${DIALOG_API}/v1/dialog/forms/${FORM_ID}?status=published' -H 'X-Tenant-ID: ${TENANT}' | jq ."
