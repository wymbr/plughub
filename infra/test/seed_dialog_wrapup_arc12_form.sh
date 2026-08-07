#!/usr/bin/env bash
#
# seed_dialog_wrapup_arc12_form.sh — fatia 3: form de wrap-up COM captura Arc 12.
#
# Irmão do `seed_dialog_wrapup_form.sh` (que segue válido e sem captura). Este
# acrescenta as duas formas que a §D2 define, e existe para haver **dado real** em
# `agent_business_events`:
#
#   fcr      → capture.kind = "scored"   → categoria `{pool}.wrapup.fcr`, value 0|1
#                                          ⇒ `avg_value` do summary É a taxa de FCR
#   servico  → capture.kind = "nominal"  → categoria `{pool}.wrapup.servico.{opção}`,
#                                          value 1 ⇒ `count` por serviço. Multi-select
#                                          vira N eventos.
#
# A folha nominal sai de `options[].value` — lista controlada, versionada e
# UI-editável (§D3). `resumo` e `proximos_passos` seguem SEM capture de propósito
# (§D6): prosa vai para as colunas do segmento, decidido pela AUSÊNCIA de capture.
#
# FONTE ÚNICA: o JSON vive em `infra/dialog/dialog_wrapup_arc12_v1.json` e é semeado
# no boot pelo `dialog-seed` (seed-if-absent). Este script é o atalho manual.
#
# Uso:
#   DIALOG_API=http://localhost:3760 TENANT=tenant_demo \
#     ./infra/test/seed_dialog_wrapup_arc12_form.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIALOG_API="${DIALOG_API:-http://localhost:3760}"
TENANT="${TENANT:-tenant_demo}"
FORM_ID="dialog_wrapup_arc12_v1"
FORM_FILE="${REPO_ROOT}/infra/dialog/${FORM_ID}.json"

[ -f "$FORM_FILE" ] || { echo "✗ arquivo do form não encontrado: $FORM_FILE" >&2; exit 1; }

echo "→ Criando DialogForm '${FORM_ID}' em ${DIALOG_API} (tenant=${TENANT}) a partir de ${FORM_FILE#"$REPO_ROOT"/}"

curl -fsS -X POST "${DIALOG_API}/v1/dialog/forms" \
  -H 'Content-Type: application/json' \
  -H "X-Tenant-ID: ${TENANT}" \
  --data-binary @"${FORM_FILE}" >/dev/null || {
    echo "  (POST falhou — form já existe ou API recusou; seguindo para o publish)"; }
echo "✓ DialogForm criado/atualizado: ${FORM_ID}"

echo "→ Publicando"
curl -fsS -X POST "${DIALOG_API}/v1/dialog/forms/${FORM_ID}/publish" \
  -H "X-Tenant-ID: ${TENANT}" >/dev/null
echo "✓ DialogForm publicado: ${FORM_ID}"
