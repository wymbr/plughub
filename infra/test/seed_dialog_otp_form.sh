#!/usr/bin/env bash
#
# seed_dialog_otp_form.sh — Dialog primitive (Fatia 1): semente do DialogForm de OTP.
#
# Provisiona, VIA API OFICIAL do dialog-api (invariante de provisioning), o form
# `dialog_otp_possession` — o conteúdo (statements + pergunta) que o dialog-runner
# renderiza para coletar o código de posse. O código gerado NUNCA vive aqui (só o
# script de diálogo); challenge/verify ficam no OtpService (costura de segredo).
#
# FONTE ÚNICA: o JSON vive em `infra/dialog/dialog_otp_possession.json` e é semeado
# no boot pelo `dialog-seed` (seed-if-absent). Este script é o atalho manual.
#
# Uso:
#   DIALOG_API=http://localhost:3760 TENANT=tenant_demo ./infra/test/seed_dialog_otp_form.sh
#
# Requer: curl.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIALOG_API="${DIALOG_API:-http://localhost:3760}"
TENANT="${TENANT:-tenant_demo}"
FORM_ID="dialog_otp_possession"
FORM_FILE="${REPO_ROOT}/infra/dialog/${FORM_ID}.json"

[ -f "$FORM_FILE" ] || { echo "✗ arquivo do form não encontrado: $FORM_FILE" >&2; exit 1; }

echo "→ Criando DialogForm '${FORM_ID}' em ${DIALOG_API} (tenant=${TENANT}) a partir de ${FORM_FILE#"$REPO_ROOT"/}"

curl -fsS -X POST "${DIALOG_API}/v1/dialog/forms" \
  -H 'Content-Type: application/json' \
  -H "X-Tenant-ID: ${TENANT}" \
  --data-binary @"${FORM_FILE}" >/dev/null
echo "✓ DialogForm criado (draft): ${FORM_ID}"

echo "→ Publicando (torna a versão corrente que o form_get resolve por padrão)"
curl -fsS -X POST "${DIALOG_API}/v1/dialog/forms/${FORM_ID}/publish" \
  -H "X-Tenant-ID: ${TENANT}" >/dev/null
echo "✓ DialogForm publicado: ${FORM_ID}"
echo
echo "Verifique: curl -s '${DIALOG_API}/v1/dialog/forms/${FORM_ID}?status=published' -H 'X-Tenant-ID: ${TENANT}' | jq ."
