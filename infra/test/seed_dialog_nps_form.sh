#!/usr/bin/env bash
#
# seed_dialog_nps_form.sh — Dialog primitive (Fatia 2): DialogForm de NPS do survey.
#
# Form `dialog_nps_v1` — agradecimento + coleta do NPS (0–10) por TEXTO. Consumido
# pelo dialog-runner via delegate (veículo runner-especialista). O NPS de
# fim-de-contato (inline, botões) é o `dialog_nps_buttons`, outro form.
#
# FONTE ÚNICA: o JSON vive em `infra/dialog/dialog_nps_v1.json` e é semeado no boot
# pelo `dialog-seed` (seed-if-absent). Este script é o atalho manual.
#
# Uso:
#   DIALOG_API=http://localhost:3760 TENANT=tenant_demo ./infra/test/seed_dialog_nps_form.sh
#
# Requer: curl.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIALOG_API="${DIALOG_API:-http://localhost:3760}"
TENANT="${TENANT:-tenant_demo}"
FORM_ID="dialog_nps_v1"
FORM_FILE="${REPO_ROOT}/infra/dialog/${FORM_ID}.json"

[ -f "$FORM_FILE" ] || { echo "✗ arquivo do form não encontrado: $FORM_FILE" >&2; exit 1; }

echo "→ Criando DialogForm '${FORM_ID}' em ${DIALOG_API} (tenant=${TENANT}) a partir de ${FORM_FILE#"$REPO_ROOT"/}"

curl -fsS -X POST "${DIALOG_API}/v1/dialog/forms" \
  -H 'Content-Type: application/json' \
  -H "X-Tenant-ID: ${TENANT}" \
  --data-binary @"${FORM_FILE}" >/dev/null
echo "✓ DialogForm criado (draft): ${FORM_ID}"

echo "→ Publicando"
curl -fsS -X POST "${DIALOG_API}/v1/dialog/forms/${FORM_ID}/publish" \
  -H "X-Tenant-ID: ${TENANT}" >/dev/null
echo "✓ DialogForm publicado: ${FORM_ID}"
echo
echo "Verifique: curl -s '${DIALOG_API}/v1/dialog/forms/${FORM_ID}?status=published' -H 'X-Tenant-ID: ${TENANT}' | jq ."
