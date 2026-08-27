#!/usr/bin/env bash
#
# seed_dialog_wrapup_form.sh — Camada E2 (wrap-up-α): DialogForm de DISPOSIÇÃO do
# wrap-up (`dialog_wrapup_v1`).
#
# Form genérico (statements + botões + texto) que o DialogFormRenderer (R0) mostra
# ao agente que reivindica o item de wrap-up (pull manual ou auto-atendimento). Os
# valores da classificação casam com o mapa de outcome do wrap-up (resolvido→resolved,
# pendente→suspended, escalado→escalated, cancelado→abandoned), usados por
# `segment_outcome_record`. O submit devolve
# payload { source:"operator", answers:{ classificacao, resumo, proximos_passos } }.
#
# FONTE ÚNICA: o JSON vive em `infra/dialog/dialog_wrapup_v1.json` e é semeado no
# boot pelo serviço `dialog-seed` (seed-if-absent). Este script é o atalho manual —
# ele SEMPRE cria+publica uma versão nova.
#
# Uso:
#   DIALOG_API=http://localhost:3760 TENANT=tenant_demo ./infra/test/seed_dialog_wrapup_form.sh
#
# Requer: curl.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIALOG_API="${DIALOG_API:-http://localhost:3760}"
DIALOG_ADMIN_TOKEN="${DIALOG_ADMIN_TOKEN:-demo_dialog_admin_token}"   # portao de escrita (sistema)
TENANT="${TENANT:-tenant_demo}"
FORM_ID="dialog_wrapup_v1"
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
