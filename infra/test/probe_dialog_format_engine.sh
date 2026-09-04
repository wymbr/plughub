#!/usr/bin/env bash
# probe_dialog_format_engine.sh — F2 do ADR do catálogo de formatos.
#
# Três proposições, e elas falham de formas DIFERENTES:
#
#   A/B  o contrato ATRAVESSA o deploy. O Zod descarta campo desconhecido em
#        SILÊNCIO, então uma imagem construída com schema velho aceita a skill,
#        grava validation nula e ninguém fica vermelho. Por isso a pergunta é
#        feita à IMAGEM que está rodando, nunca ao fonte.
#   C    o form_get ENTREGA a validação do campo (D6). Antes da F2 o RenderField
#        era allowlist e a descartava ali — todo interaction=form era
#        estruturalmente incapaz de validar.
#   D    o engine JULGA (D5 + catálogo), medido pela suíte do pacote.
#
# Nenhum ramo passa por ausência: A/B e C carregam testemunha negativa (um campo
# SEM validação tem de chegar sem), e D exige contagem mínima.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2

RAIZ="$PWD"
MCP="${MCP_URL:-http://localhost:3100}"
TENANT="${PLUGHUB_TENANT_ID:-tenant_demo}"
REG_CT="${REG_CT:-plughub-demo-agent-registry-1}"
FORMA="${FORMA:-dialog_probe_format_v1}"
SCHEMAS_JS="/app/packages/agent-registry/node_modules/@plughub/schemas/dist/index.js"
FALHAS=0
INCONCLUSIVOS=0

ok()  { echo "  OK           $*"; }
bad() { echo "  REPROVA      $*"; FALHAS=$((FALHAS+1)); }
inc() { echo "  INCONCLUSIVO $*"; INCONCLUSIVOS=$((INCONCLUSIVOS+1)); }

echo "== probe_dialog_format_engine =="
echo

# ── A/B — o contrato atravessa a IMAGEM que está rodando ─────────────────────
JS=$(mktemp)
{
  echo "const S = require('$SCHEMAS_JS')"
  cat <<'NODE'
const passo = {
  id: 'coletar', type: 'menu', prompt: 'Informe', interaction: 'form',
  on_success: 'a', on_failure: 'b', timeout_s: 30,
  validation: { format: 'date_br' },
  fields: [
    { id: 'nasc', label: 'N', type: 'text', required: true,
      validation: { format: 'date_br', max_length: 10 } },
    { id: 'livre', label: 'L', type: 'text', required: false },
  ],
}
const r = S.MenuStepSchema.safeParse(passo)
if (!r.success) {
  console.log('PARSE_FALHOU ' + JSON.stringify(r.error.issues.slice(0, 2)))
  process.exit(0)
}
const d = r.data
const f0 = (d.fields || [])[0] || {}
const f1 = (d.fields || [])[1] || {}
console.log(JSON.stringify({
  step_format:  (d.validation || {}).format || null,
  field_format: (f0.validation || {}).format || null,
  field_maxlen: (f0.validation || {}).max_length || null,
  livre_tem:    !!f1.validation,
  catalogo:     typeof S.validateDialogFormat,
}))
NODE
} > "$JS"

SAIDA=$(docker exec -i "$REG_CT" node < "$JS" 2>&1 | tail -1)
rm -f "$JS"

case "$SAIDA" in
  PARSE_FALHOU*)
    bad "A. a imagem do agent-registry REJEITA o contrato novo — $SAIDA"
    ;;
  \{*)
    SF=$(echo "$SAIDA" | jq -r '.step_format // "null"')
    FF=$(echo "$SAIDA" | jq -r '.field_format // "null"')
    FM=$(echo "$SAIDA" | jq -r '.field_maxlen // "null"')
    LT=$(echo "$SAIDA" | jq -r '.livre_tem')
    CT=$(echo "$SAIDA" | jq -r '.catalogo')
    if [ "$SF" = "date_br" ]; then
      ok "A. a IMAGEM preserva validation.format no step (=$SF)"
    else
      bad "A. validation.format DESCARTADO pela imagem (=$SF) — schema velho na layer"
    fi
    if [ "$FF" = "date_br" ] && [ "$FM" = "10" ]; then
      ok "B. a IMAGEM preserva a validação do CAMPO (D6: format=$FF max_length=$FM)"
    else
      bad "B. a validação do campo foi DESCARTADA (format=$FF max_length=$FM)"
    fi
    if [ "$LT" = "false" ]; then
      ok "B'. testemunha negativa: campo sem regra continua sem regra"
    else
      bad "B'. campo sem regra GANHOU uma — algum default inventa validação"
    fi
    if [ "$CT" = "function" ]; then
      ok "A'. o interpretador do catálogo viaja no pacote (validateDialogFormat)"
    else
      bad "A'. validateDialogFormat ausente da imagem (=$CT)"
    fi
    ;;
  *)
    inc "A/B. não consegui interrogar a imagem: $(echo "$SAIDA" | head -c 160)"
    ;;
esac

# ── C — o form_get ENTREGA a validação do campo ──────────────────────────────
if ! curl -sf -o /dev/null -m 5 "$MCP/health"; then
  inc "C. mcp-server não responde em $MCP — a entrega NÃO foi verificada"
else
  SSE=$(mktemp)
  curl -sN "$MCP/sse" > "$SSE" 2>/dev/null &
  SSE_PID=$!
  EP=""
  for _ in $(seq 1 40); do
    EP=$(sed -n 's#^data: \(/messages?[^ ]*\)#\1#p' "$SSE" | head -1)
    [ -n "$EP" ] && break
    sleep 0.25
  done
  if [ -z "$EP" ]; then
    inc "C. o transporte SSE não anunciou endpoint em 10 s"
  else
    envia() { curl -s -o /dev/null "$MCP$EP" -H 'content-type: application/json' -d "$1"; }
    envia '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe-fmt","version":"1"}}}'
    envia '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'
    ARGS=$(jq -nc --arg f "$FORMA" --arg t "$TENANT" \
      '{jsonrpc:"2.0",id:2,method:"tools/call",params:{name:"form_get",arguments:{form_id:$f,tenant_id:$t}}}')
    envia "$ARGS"
    RESP=""
    for _ in $(seq 1 60); do
      RESP=$(sed -n 's/^data: //p' "$SSE" | jq -Rc 'fromjson? | select(.id? == 2)' 2>/dev/null | head -1)
      [ -n "$RESP" ] && break
      sleep 0.25
    done
    if [ -z "$RESP" ]; then
      inc "C. form_get não respondeu em 15 s"
    else
      CORPO=$(printf '%s' "$RESP" | jq -r '.result.content[0].text' 2>/dev/null)
      COM=$(printf '%s' "$CORPO" | jq '[.render.fields[]? | select(.validation != null)] | length' 2>/dev/null)
      SEM=$(printf '%s' "$CORPO" | jq '[.render.fields[]? | select(.validation == null)] | length' 2>/dev/null)
      if [ "${COM:-0}" -ge 1 ] && [ "${SEM:-0}" -ge 1 ]; then
        ok "C. form_get entrega a validação do campo — $COM com regra, $SEM sem (D6 viva)"
      elif [ "${COM:-0}" -ge 1 ]; then
        bad "C. todos os campos vieram com validação (com=$COM sem=$SEM) — sem testemunha negativa"
      else
        bad "C. nenhum campo do render carrega validação (com=$COM sem=$SEM) — RenderField ainda descarta"
      fi
    fi
  fi
  kill "$SSE_PID" 2>/dev/null
  rm -f "$SSE"
fi

# ── D — o engine julga ───────────────────────────────────────────────────────
VT=$(docker run --rm -v "$RAIZ:/w" -w /w/packages/skill-flow-engine node:20-alpine \
       npx vitest run src/__tests__/steps/menu.test.ts 2>&1)
N=$(echo "$VT" | grep -oE 'Tests +[0-9]+ passed' | grep -oE '[0-9]+' | head -1)
if echo "$VT" | grep -qE '^ *Tests +[0-9]+ passed' && [ "${N:-0}" -ge 20 ]; then
  ok "D. o engine julga — $N testes de menu verdes (D5 + catálogo + D6)"
else
  bad "D. a suíte de menu reprovou ou encolheu (n=${N:-0}, mínimo 20)"
  echo "$VT" | grep -E 'FAIL|AssertionError' | head -8 | sed 's/^/       /'
fi

echo
echo "======================"
if [ "$FALHAS" -gt 0 ]; then echo "REPROVADO ($FALHAS)"; exit 1; fi
if [ "$INCONCLUSIVOS" -gt 0 ]; then echo "INCONCLUSIVO ($INCONCLUSIVOS) — não é verde"; exit 2; fi
echo "VERDE"
