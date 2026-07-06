#!/usr/bin/env bash
#
# seed_dialog_otp_form.sh — Dialog primitive (Fatia 1): semente do DialogForm de OTP.
#
# Provisiona, VIA API OFICIAL do dialog-api (invariante de provisioning), o form
# `dialog_otp_possession` — o conteúdo (statements + pergunta) que o dialog-runner
# renderiza para coletar o código de posse. O código gerado NUNCA vive aqui (só o
# script de diálogo); challenge/verify ficam no OtpService (costura de segredo).
#
# Uso:
#   DIALOG_API=http://localhost:3760 TENANT=tenant_demo ./infra/test/seed_dialog_otp_form.sh
#
# Requer: curl, jq.
set -euo pipefail

DIALOG_API="${DIALOG_API:-http://localhost:3760}"
TENANT="${TENANT:-tenant_demo}"
FORM_ID="dialog_otp_possession"

echo "→ Criando DialogForm '${FORM_ID}' em ${DIALOG_API} (tenant=${TENANT})"

read -r -d '' BODY <<JSON || true
{
  "form_id": "${FORM_ID}",
  "name": "OTP — prova de posse de canal",
  "description": "Prompts do step-up de OTP: aviso de envio + coleta do código digitado pelo cliente.",
  "default_locale": "pt-BR",
  "locales": ["pt-BR"],
  "tags": ["otp"],
  "nodes": [
    {
      "id": "aviso_envio",
      "kind": "statement",
      "text": { "pt-BR": "Enviamos um código de 6 dígitos para o seu número por SMS." }
    },
    {
      "id": "coletar_codigo",
      "kind": "question",
      "prompt": { "pt-BR": "Digite o código para confirmar:" },
      "interaction": "text",
      "output_key": "code",
      "validation": { "numeric": true, "min_length": 6, "max_length": 6 },
      "retry": { "reprompt": { "pt-BR": "Código inválido. Digite os 6 dígitos:" }, "max_attempts": 3 },
      "timeout_s": 180
    }
  ]
}
JSON

curl -fsS -X POST "${DIALOG_API}/v1/dialog/forms" \
  -H 'Content-Type: application/json' \
  -H "X-Tenant-ID: ${TENANT}" \
  -d "${BODY}" >/dev/null
echo "✓ DialogForm criado (draft): ${FORM_ID}"

echo "→ Publicando (torna a versão corrente que o form_get resolve por padrão)"
curl -fsS -X POST "${DIALOG_API}/v1/dialog/forms/${FORM_ID}/publish" \
  -H "X-Tenant-ID: ${TENANT}" >/dev/null
echo "✓ DialogForm publicado: ${FORM_ID}"
echo
echo "Verifique: curl -s '${DIALOG_API}/v1/dialog/forms/${FORM_ID}?status=published' -H 'X-Tenant-ID: ${TENANT}' | jq ."
