#!/usr/bin/env bash
# probe_att09_download_name.sh — ATT-09: o Console salva o anexo com o nome do BALÃO, mascarado.
#
# PROPOSIÇÃO: o nome do download sai do texto `[Anexo: nome] legenda` que o balão desenha, com a
# MESMA regra de exibição do token (`token_display`), nunca do nome cru; sem indicador legível,
# o nome genérico de antes. Exercita `platform-ui/src/components/attachmentName.ts` (puro),
# empacotado pelo esbuild do próprio pacote, e confere que os dois componentes o usam.
#
# Ramos:
#   N1 nome simples, com legenda → o nome (CONTROLE POSITIVO)
#   N2 CPF no nome, display_partial → `•••00` (`*` é proibido no Windows), e o número cru nunca aparece
#   N3 full_mask → `•••••` · N4 hidden → só o rótulo da categoria
#   N5 nome sem extensão → ganha a do tipo REAL · N6 caractere proibido → `_`
#   N7 texto sem indicador → nome genérico (`document-xxxxxxxx.pdf`)
#   W1 o AttachmentView usa a função e não LÊ o `Content-Disposition` (nome cru); os dois balões passam o texto
#   W2 o MaskedToken aplica a MESMA regra (`tokenScreenValue`) — uma casa
#
# Saída: 0 VERDE · 1 VERMELHO · 2 INCONCLUSIVO. Rodar de DENTRO do WSL.
set -uo pipefail
cd "$(dirname "$0")/../.."
UI=packages/platform-ui
[ -d "$HOME/.nvm/versions/node" ] && export PATH="$(ls -d "$HOME"/.nvm/versions/node/*/bin | tail -1):$PATH"
command -v node >/dev/null || { echo "  ? node ausente"; echo "VEREDICTO: INCONCLUSIVO"; exit 2; }
[ -x "$UI/node_modules/.bin/esbuild" ] || { echo "  ? esbuild ausente em $UI"; echo "VEREDICTO: INCONCLUSIVO"; exit 2; }
FAIL=0
ok()  { echo "  ✓ $1"; }
bad() { echo "  ✗ $1"; FAIL=1; }

echo "══ probe_att09_download_name — o nome do download é o do balão, mascarado ══"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
"$UI/node_modules/.bin/esbuild" "$UI/src/components/attachmentName.ts" --format=cjs --platform=node \
  --outfile="$TMP/n.cjs" --log-level=error || { echo "  ? esbuild falhou"; echo "VEREDICTO: INCONCLUSIVO"; exit 2; }

node - "$TMP/n.cjs" <<'JS' || FAIL=1
const { attachmentDownloadName: n } = require(process.argv[2])
const pdf = { media_type: 'document', file_id: 'a8383516-4319', mime_type: 'application/pdf' }
let falhou = 0
const chk = (r, cond, txt) => { console.log(`  ${cond ? '✓' : '✗'} ${r} ${txt}`); if (!cond) falhou = 1 }
const tok = '[cpf:tk_ab12:***00]'
let v
v = n('[Anexo: PlugHub — folder técnico.pdf] segue', pdf)
chk('N1', v === 'PlugHub — folder técnico.pdf', `nome simples → ${v}`)
v = n(`[Anexo: cpf ${tok}.pdf]`, pdf, { cpf: { token_display: 'display_partial', echo_to_customer: 'none' } })
chk('N2', v === 'cpf •••00.pdf' && !/tk_|\d{3}\.\d{3}/.test(v), `display_partial → ${v}`)
v = n(`[Anexo: cpf ${tok}.pdf]`, pdf, { cpf: { token_display: 'full_mask', echo_to_customer: 'none' } })
chk('N3', v === 'cpf •••••.pdf', `full_mask → ${v}`)
v = n(`[Anexo: cpf ${tok}.pdf]`, pdf, { cpf: { token_display: 'hidden', echo_to_customer: 'none' } })
chk('N4', v === 'cpf CPF.pdf' && !v.includes('***'), `hidden → ${v}`)
v = n('[Anexo: contrato] legenda', pdf)
chk('N5', v === 'contrato.pdf', `sem extensão → ${v}`)
v = n('[Anexo: a/b:c?.pdf]', pdf)
chk('N6', v === 'a_b_c_.pdf', `caractere proibido → ${v}`)
v = n('mensagem sem indicador', pdf)
chk('N7', v === 'document-a8383516.pdf', `sem indicador → ${v}`)
process.exit(falhou)
JS

AV="$UI/src/components/AttachmentView.tsx"
if grep -q 'attachmentDownloadName(text, attachment, maskingRules)' "$AV" && ! grep -qiE "headers\.get\(['\"]content-disposition" "$AV" \
   && grep -q 'text={message.text}' "$UI/src/modules/agent-assist/components/MessageBubble.tsx" \
   && grep -q 'text={normalized.text}' "$UI/src/modules/service/components/SessionTranscript.tsx"; then
  ok "W1 o AttachmentView usa o nome do balão e os dois balões passam o texto"
else
  bad "W1 o download não usa o nome do balão em algum caminho"
fi
grep -q 'tokenScreenValue(rule.token_display, token.display)' "$UI/src/components/MaskedToken.tsx" \
  && ok "W2 o chip e o nome aplicam a mesma regra (tokenScreenValue)" \
  || bad "W2 o MaskedToken tem regra própria — o nome pode divergir do balão"

echo
[ $FAIL -eq 0 ] && { echo "VEREDICTO: VERDE"; exit 0; } || { echo "VEREDICTO: VERMELHO"; exit 1; }
