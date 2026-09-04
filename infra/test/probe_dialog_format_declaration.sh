#!/usr/bin/env bash
# probe_dialog_format_declaration.sh — F3 do ADR do catálogo de formatos.
#
# A F3 tem duas metades que falham diferente:
#   · `pattern` SAIU — e o que prova isso é a IMAGEM em execução descartá-lo,
#     não o `grep` no fonte. O Zod remove campo desconhecido em silêncio, então
#     "o fonte não tem mais" e "o que roda não aceita mais" são fatos distintos.
#   · a §D8 passou a ser RECUSA, não conselho — o publish devolve 422 quando o
#     `format` declarado contradiz o derivado do tipo mascarado.
#
# Todos os ramos com controle positivo: sem ele, "removeu" e "nunca teve" — ou
# "recusa o conflito" e "recusa tudo" — dão o mesmo verde.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2

DIALOG="${DIALOG_API:-http://localhost:3760}"
TOKEN="${DIALOG_ADMIN_TOKEN:-demo_dialog_admin_token}"
TENANT="${PLUGHUB_TENANT_ID:-tenant_demo}"
REG_CT="${REG_CT:-plughub-demo-agent-registry-1}"
SCHEMAS_JS="/app/packages/agent-registry/node_modules/@plughub/schemas/dist/index.js"
EDITOR="packages/platform-ui/src/modules/dialog-forms/DialogFormsPage.tsx"
FALHAS=0
INCONCLUSIVOS=0

ok()  { echo "  OK           $*"; }
bad() { echo "  REPROVA      $*"; FALHAS=$((FALHAS+1)); }
inc() { echo "  INCONCLUSIVO $*"; INCONCLUSIVOS=$((INCONCLUSIVOS+1)); }

echo "== probe_dialog_format_declaration =="
echo

# ── A — a IMAGEM descarta pattern e preserva format ──────────────────────────
JS=$(mktemp)
{
  echo "const S = require('$SCHEMAS_JS')"
  cat <<'NODE'
const base = {
  id: 'c', type: 'menu', prompt: 'p', interaction: 'text',
  on_success: 'a', on_failure: 'b', timeout_s: 30,
}
const r = S.MenuStepSchema.safeParse({
  ...base, validation: { format: 'date_br', pattern: '^[0-9]{6}$', max_length: 10 },
})
if (!r.success) { console.log('PARSE_FALHOU'); process.exit(0) }
const v = r.data.validation || {}
console.log(JSON.stringify({
  pattern_sobreviveu: Object.prototype.hasOwnProperty.call(v, 'pattern'),
  format:             v.format || null,
  max_length:         v.max_length || null,
}))
NODE
} > "$JS"
SAIDA=$(docker exec -i "$REG_CT" node < "$JS" 2>&1 | tail -1); rm -f "$JS"

case "$SAIDA" in
  \{*)
    PS=$(echo "$SAIDA" | jq -r '.pattern_sobreviveu')
    FM=$(echo "$SAIDA" | jq -r '.format // "null"')
    ML=$(echo "$SAIDA" | jq -r '.max_length // "null"')
    if [ "$PS" = "false" ]; then
      ok "A. a IMAGEM descarta pattern — o contrato removido não roda mais"
    else
      bad "A. pattern SOBREVIVEU no schema em execução — layer velha ou remoção incompleta"
    fi
    if [ "$FM" = "date_br" ] && [ "$ML" = "10" ]; then
      ok "A'. controle positivo: format e max_length atravessam (=$FM, $ML)"
    else
      bad "A'. o schema descartou campos VÁLIDOS (format=$FM max_length=$ML) — A passou pelo motivo errado"
    fi
    ;;
  PARSE_FALHOU) bad "A. a imagem rejeita o step inteiro" ;;
  *) inc "A. não consegui interrogar a imagem: $(echo "$SAIDA" | head -c 140)" ;;
esac

# ── B — o censo continua zero (regressão) ────────────────────────────────────
CENSO=$(curl -s -m 10 -H "X-Tenant-ID: $TENANT" "$DIALOG/v1/dialog/forms" 2>/dev/null)
if ! echo "$CENSO" | jq -e '.forms' >/dev/null 2>&1; then
  inc "B. dialog-api não listou formas — o censo NÃO foi refeito"
else
  IDS=$(echo "$CENSO" | jq -r '.forms[].form_id')
  N=0; COM=0
  for f in $IDS; do
    N=$((N + 1))
    C=$(curl -s -m 10 -H "X-Tenant-ID: $TENANT" "$DIALOG/v1/dialog/forms/$f" 2>/dev/null)
    if echo "$C" | grep -q '"pattern"'; then
      COM=$((COM + 1)); echo "       ainda usa pattern: $f"
    fi
  done
  if [ "$N" -lt 5 ]; then
    inc "B. só $N formas — amostra pequena demais para o censo significar algo"
  elif [ "$COM" -eq 0 ]; then
    ok "B. censo: 0 de $N formas publicadas usam pattern"
  else
    bad "B. $COM de $N formas ainda declaram pattern — a remoção quebrou dado vivo"
  fi
fi

# ── C — o publish RECUSA o conflito da D8 ────────────────────────────────────
mk() {
  jq -nc --arg id "$1" --arg fmt "$2" '{
    form_id: $id, title: "sonda D8", default_locale: "pt-BR", locales: ["pt-BR"],
    nodes: [ ({
      id: "q", kind: "question", prompt: "p", interaction: "text",
      output_key: "v", timeout_s: 30, masked: "cpf"
    }) + (if $fmt == "" then {} else { validation: { format: $fmt } } end) ]
  }'
}
pub() {
  curl -s -o /dev/null -w "%{http_code}" -m 10 -X POST \
    -H "X-Admin-Token: $TOKEN" -H "X-Tenant-ID: $TENANT" \
    "$DIALOG/v1/dialog/forms/$1/publish"
}
# `DELETE` de DialogForm é ARQUIVAR, não apagar (adr-dialog-form-deletion), e
# purga real só existe para o nunca-publicado. Logo a limpeza do fim deixa as
# duas sondas ARQUIVADAS, e uma segunda rodada bateria em 409 — um gate que só
# passa na primeira execução ensina todo mundo a ignorá-lo. Desarquivar antes de
# criar reusa os mesmos ids e não acumula lixo a cada rodada.
cria() {
  curl -s -o /dev/null -m 10 -X POST -H "X-Admin-Token: $TOKEN" \
    -H "X-Tenant-ID: $TENANT" "$DIALOG/v1/dialog/forms/$1/undelete"
  curl -s -o /dev/null -m 10 -X PUT -H "X-Admin-Token: $TOKEN" \
    -H "X-Tenant-ID: $TENANT" -H 'content-type: application/json' \
    -d "$2" "$DIALOG/v1/dialog/forms/$1"
  curl -s -o /dev/null -m 10 -X POST -H "X-Admin-Token: $TOKEN" \
    -H "X-Tenant-ID: $TENANT" -H 'content-type: application/json' \
    -d "$2" "$DIALOG/v1/dialog/forms"
}

F_CONF="dialog_probe_d8_conflito"
F_OK="dialog_probe_d8_coerente"
cria "$F_CONF" "$(mk "$F_CONF" date_br)"
cria "$F_OK"   "$(mk "$F_OK" "")"

RC_CONF=$(pub "$F_CONF")
RC_OK=$(pub "$F_OK")

if [ "$RC_CONF" = "422" ]; then
  ok "C. publish RECUSA o conflito (masked=cpf + format=date_br → 422)"
else
  bad "C. o conflito da D8 foi PUBLICADO (HTTP $RC_CONF) — o guarda não rodou"
fi
if [ "$RC_OK" = "200" ] || [ "$RC_OK" = "201" ]; then
  ok "C'. controle positivo: forma coerente publica (HTTP $RC_OK)"
else
  bad "C'. forma COERENTE foi recusada (HTTP $RC_OK) — o guarda recusa demais"
fi

for f in "$F_CONF" "$F_OK"; do
  curl -s -o /dev/null -m 10 -X DELETE -H "X-Admin-Token: $TOKEN" \
    -H "X-Tenant-ID: $TENANT" "$DIALOG/v1/dialog/forms/$f"
done

# ── D — o editor perdeu o campo livre e ganhou os dois seletores ─────────────
# `platform-ui` não tem runner de teste, e isto é grep — declarado como tal. Ele
# prova AUSÊNCIA da afordância antiga e PRESENÇA da nova no FONTE, não que a
# tela funcione. A metade que falta é dívida conhecida do pacote.
if grep -q 'validation?.pattern' "$EDITOR"; then
  bad "D. o editor ainda tem o campo livre de regex"
elif grep -q "t('field.format')" "$EDITOR" && grep -q "t('field.maskedType')" "$EDITOR"; then
  ok "D. editor com os dois seletores e sem campo livre de regex (grep, não execução)"
else
  bad "D. seletor de formato ou de tipo mascarado ausente do editor"
fi

echo
echo "======================"
if [ "$FALHAS" -gt 0 ]; then echo "REPROVADO ($FALHAS)"; exit 1; fi
if [ "$INCONCLUSIVOS" -gt 0 ]; then echo "INCONCLUSIVO ($INCONCLUSIVOS) — não é verde"; exit 2; fi
echo "VERDE"
