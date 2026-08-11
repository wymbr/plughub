#!/usr/bin/env bash
#
# seed_dialog_limite_forms.sh — DialogForms do cenário de aumento de limite:
#   dialog_limite_solicitacao — coleta multi-campo (cvv com masked: true)
#   dialog_limite_aprovacao   — campos editáveis do pacote de análise
#
# FONTE ÚNICA: os JSONs vivem em `infra/dialog/` e são semeados no boot pelo
# serviço `dialog-seed` (seed-if-absent, `infra/dialog` é volume montado — não
# precisa rebuild). Este script é o atalho manual para quando a stack JÁ ESTÁ NO AR
# e o job de seed já rodou: ele cria e publica sem esperar o próximo boot.
#
# Tolera form já existente (segue para o publish) — o modo de falha que interessa
# é o publish, não o create.
#
# Uso:
#   DIALOG_API=http://localhost:3760 TENANT=tenant_demo bash infra/test/seed_dialog_limite_forms.sh
#
# Requer: curl.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIALOG_API="${DIALOG_API:-http://localhost:3760}"
TENANT="${TENANT:-tenant_demo}"

for FORM_ID in dialog_limite_solicitacao dialog_limite_aprovacao; do
  FORM_FILE="${REPO_ROOT}/infra/dialog/${FORM_ID}.json"
  [ -f "$FORM_FILE" ] || { echo "✗ arquivo do form não encontrado: $FORM_FILE" >&2; exit 1; }

  # POST só CRIA. Num form que já existe ele falha, e um `publish` logo em seguida
  # republica a versão ANTIGA — foi assim que uma correção de conteúdo virou no-op
  # silencioso (o form seguiu com question nodes em vez de fields, e o valor digitado
  # pelo aprovador não chegava ao workflow). O PUT é o caminho de ATUALIZAÇÃO.
  echo "→ Criando '${FORM_ID}' em ${DIALOG_API} (tenant=${TENANT})"
  if curl -fsS -X POST "${DIALOG_API}/v1/dialog/forms" \
       -H 'Content-Type: application/json' \
       -H "X-Tenant-ID: ${TENANT}" \
       --data-binary @"${FORM_FILE}" >/dev/null 2>&1; then
    echo "  ✓ criado (draft)"
  else
    echo "  · já existia — atualizando o draft via PUT"
    curl -fsS -X PUT "${DIALOG_API}/v1/dialog/forms/${FORM_ID}" \
      -H 'Content-Type: application/json' \
      -H "X-Tenant-ID: ${TENANT}" \
      --data-binary @"${FORM_FILE}" >/dev/null
    echo "  ✓ draft atualizado"
  fi

  echo "→ Publicando '${FORM_ID}'"
  curl -fsS -X POST "${DIALOG_API}/v1/dialog/forms/${FORM_ID}/publish" \
    -H "X-Tenant-ID: ${TENANT}" >/dev/null
  echo "  ✓ publicado"
done

echo "✓ DialogForms do cenário de limite prontos"
