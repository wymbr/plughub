#!/usr/bin/env bash
# probe_dialog_return_pointer.sh — 2026-09-06  (RET-01 do ADR
# adr-tree-return-continuation.md)
#
# PERGUNTA: a folha ganhou o ponteiro de continuacao SEM mudar o que as formas
# publicadas renderizam hoje?
#
# POR QUE A PERGUNTA E ESSA, e nao "o ponteiro funciona"
#   Que o ponteiro funciona, a suite de unidade prova (dialog-return.test.ts, 14
#   testes). O risco desta mudanca nao esta ali: esta em `buildRender`, que serve
#   o `form_get` E a preview do editor, e que passou a saber renderizar A PARTIR
#   de uma question. Se a janela do bloco valesse para TODA chamada, o
#   `statement_after` das formas com varias questions mudaria em silencio.
#
#   Medido em 2026-09-06: **5 das 14 formas publicadas tem mais de uma question**
#   (survey e wrap-up). Por isso a janela e OPT-IN: sem `fromQuestionId`, o
#   caminho e literalmente o de antes.
#
# TRES RAMOS
#   A  PARIDADE — renderiza as 14 formas SEM `fromQuestionId` e compara com a
#      linha de base. Carrega a TESTEMUNHA: quantas tem multi-question (se for 0,
#      declara INCONCLUSIVO, porque e nelas que a mudanca morderia) e quantas
#      declaram `on_return` (0 hoje = o campo novo e inocuo para a populacao).
#   B  MUTACAO do comparador — adultera uma COPIA da linha de base e exige que o
#      ramo A reprove. Sem isto, o verde do A poderia ser um comparador que nao
#      compara, que e o modo de falha do catalogo.
#   C  o schemas esta BUILDADO e exporta o que promete. O probe le o `dist`, nao
#      o fonte: e o artefato que os consumidores usam, e um `dist` velho faria o
#      ramo A medir o codigo errado sem ficar vermelho.
#
# ⚠️ A linha de base e POS-mudanca — ela protege o FUTURO, nao prova o passado.
#    O passado tem cobertura em outra casa, e por duas vias que nao se
#    substituem: `dialog-render.test.ts` (formas de UMA question, intacta) e o
#    teste `mantem o comportamento de sempre` de `dialog-return.test.ts` (o caso
#    multi-question, que NAO estava coberto antes desta tarefa).
#
# EXIT: 0 OK · 1 FALHA · 3 SEM AMOSTRA/INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

export PATH="$HOME/.nvm/versions/node/v24.14.1/bin:$PATH"
BASE="infra/test/fixtures/return_render_baseline.json"
SCRIPT="infra/test/_return_render_parity.mjs"
DIST="packages/schemas/dist/index.js"
FALHA=0
INCONCL=0

echo "probe_dialog_return_pointer — o ponteiro de continuacao (RET-01)"

# ── C ────────────────────────────────────────────────────────────────────────
echo ""
echo "── C — o dist esta construido e exporta o contrato ───────────────────"
if [ ! -f "$DIST" ]; then
  echo "  SEM AMOSTRA — $DIST ausente; rode 'npm run build' em packages/schemas"
  echo ""
  echo "RESULTADO: SEM AMOSTRA"
  exit 3
fi
FALTA=""
for nome in buildRender entryQuestionId returnRefErrors; do
  grep -q "$nome" "$DIST" || FALTA="$FALTA $nome"
done
if [ -n "$FALTA" ]; then
  echo "  FALHA — o dist nao exporta:$FALTA"
  FALHA=$((FALHA + 1))
else
  echo "  OK — buildRender · entryQuestionId · returnRefErrors presentes no dist"
fi

# ── A ────────────────────────────────────────────────────────────────────────
echo ""
echo "── A — paridade do render das formas REAIS ──────────────────────────"
rc=0
node "$SCRIPT" confere "$BASE" || rc=$?
case "$rc" in
  0) ;;
  3) INCONCL=$((INCONCL + 1)) ;;
  *) FALHA=$((FALHA + 1)) ;;
esac

# ── B ────────────────────────────────────────────────────────────────────────
echo ""
echo "── B — mutacao: o comparador enxerga? ───────────────────────────────"
MUT="$(mktemp)"
python3 - "$BASE" "$MUT" <<'PYEOF'
import json, sys
base = json.load(open(sys.argv[1], encoding="utf-8"))
chave = sorted(base)[0]
base[chave]["statement_after"] = "TEXTO ADULTERADO PELO PROBE"
json.dump(base, open(sys.argv[2], "w", encoding="utf-8"))
print("   adulterei statement_after de: %s" % chave)
PYEOF
rc=0
node "$SCRIPT" confere "$MUT" >/dev/null 2>&1 || rc=$?
rm -f "$MUT"
if [ "$rc" = "1" ]; then
  echo "  OK — o comparador REPROVOU a base adulterada"
else
  echo "  FALHA — comparador cego: adulterei a base e ele saiu com rc=$rc"
  FALHA=$((FALHA + 1))
fi

echo ""
echo "═════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then
  echo "RESULTADO: FALHA — $FALHA ramo(s) reprovaram"
  exit 1
fi
if [ "$INCONCL" -gt 0 ]; then
  echo "RESULTADO: INCONCLUSIVO — $INCONCL ramo(s) sem populacao"
  exit 3
fi
echo "RESULTADO: OK — o ponteiro entrou e as formas publicadas renderizam igual"
exit 0
