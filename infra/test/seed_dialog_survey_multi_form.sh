#!/usr/bin/env bash
#
# seed_dialog_survey_multi_form.sh — Dialog primitive: survey com DIMENSION COMPOSTA.
#
# Form `dialog_survey_multi_v1` — prova a composição de nota multi-pergunta
# (ADR adr-survey-form-scoring-composition.md):
#   - dimension `csat` (escala 1–5, weighted_mean) composta por DUAS perguntas
#     (atendimento peso 2, resolução peso 1) → UM sinal `csat` ponderado;
#   - `nps` standalone (0–10, capture.metric legado) → sinal single.
# Consumido pelo `skill_survey_multi_v1` (step loop → survey_record com
# form_id+answers=respostas; a composição roda server-side no survey_record).
#
# FONTE ÚNICA: o JSON vive em `infra/dialog/dialog_survey_multi_v1.json` e é semeado
# no boot pelo `dialog-seed` (seed-if-absent). Este script é o atalho manual.
#
# Uso:
#   DIALOG_API=http://localhost:3760 TENANT=tenant_demo ./infra/test/seed_dialog_survey_multi_form.sh
#
# Requer: curl.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIALOG_API="${DIALOG_API:-http://localhost:3760}"
TENANT="${TENANT:-tenant_demo}"
FORM_ID="dialog_survey_multi_v1"
FORM_FILE="${REPO_ROOT}/infra/dialog/${FORM_ID}.json"

[ -f "$FORM_FILE" ] || { echo "✗ arquivo do form não encontrado: $FORM_FILE" >&2; exit 1; }

echo "→ Criando DialogForm '${FORM_ID}' em ${DIALOG_API} (tenant=${TENANT}) a partir de ${FORM_FILE#"$REPO_ROOT"/}"

curl -fsS -X POST "${DIALOG_API}/v1/dialog/forms" \
  -H 'Content-Type: application/json' -H "X-Tenant-ID: ${TENANT}" \
  --data-binary @"${FORM_FILE}" >/dev/null
echo "✓ DialogForm criado (draft): ${FORM_ID}"

curl -fsS -X POST "${DIALOG_API}/v1/dialog/forms/${FORM_ID}/publish" \
  -H "X-Tenant-ID: ${TENANT}" >/dev/null
echo "✓ DialogForm publicado: ${FORM_ID}"

echo
echo "Composição esperada (exemplo): atendimento=5, resolucao=3 →"
echo "  csat = weighted_mean([5·2, 3·1]) = (10+3)/3 ≈ 4.33 ; nps = valor cru (0–10)."
