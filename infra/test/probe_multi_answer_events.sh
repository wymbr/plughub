#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# probe_multi_answer_events — resposta de `checklist` produz N eventos, nunca UMA
# categoria-lixo. Testemunha negativa da F2 do `adr-dialog-tree-options`.
#
# POR QUE ESTE PROBE EXISTE
# ─────────────────────────
# O comentário do `deriveAgentEvents` dizia *"Multi-select chega como array ⇒ N
# eventos"*, e o `CLAUDE.md` do skill-flow-engine declarava, em tabela,
# `checklist → string[]`. Nenhum dos dois tinha mecanismo: o `menu` step gravava a
# resposta como ESCALAR, então o que chegava ao consumidor era a string
# `'["a","b"]'`, e o `String(item).replace(/[^a-z0-9_]+/g,"_")` a transformava em
# **`_a_b_`** — uma série inventada, no lugar de duas reais. Promessa em prosa sem
# produtor: a mesma família do DDL de `participation_intervals`.
#
# O dano é do tipo que não fica vermelho: a tool grava o wrap-up, o outcome é
# registrado, e só a série do Arc 12 nasce com uma categoria que não existe no
# vocabulário. Ninguém a procura, porque ninguém sabe que ela deveria estar lá.
#
# O QUE ESTE PROBE PODE REPROVAR
# ──────────────────────────────
#   A  **controle positivo**: a forma REAL precisa ter pergunta `checklist`   → INCONCLUSIVO
#      COM `capture.kind` — sem isso "não produziu lixo" e "não havia o que
#      produzir" são o mesmo verde.
#   B  lista de 2 marcações não produzir 2 eventos                            → VERMELHO
#   C  aparecer categoria com o padrão da colagem (`_a_b_`)                   → VERMELHO
#      — é a testemunha NEGATIVA, e ela é o motivo do probe existir.
#   D  marcação ÚNICA deixar de produzir 1 evento                             → VERMELHO
#      (regressão do caminho escalar, que continua sendo o de todo dia)
#
# A cadeia exercitada é a REAL, recortada dos dois arquivos: `coerceMultiAnswer`
# (o que o engine grava no `pipeline_state`) → `deriveAgentEvents` (o que a tool
# emite). Testar só o segundo passaria com o defeito no lugar: ele SEMPRE soube
# tratar array; quem não entregava array era o primeiro.
#
# COMO FALSEAR
# ────────────
#   1. `coerceMultiAnswer` devolvendo `raw` para checklist   → B e C vermelhos
#   2. `deriveAgentEvents` sem o ramo `Array.isArray(raw)`   → B vermelho
#
# Uso:  bash infra/test/probe_multi_answer_events.sh
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NODE_IMG="${NODE_IMG:-node:20-alpine}"
DA="${DIALOG_API:-http://localhost:3760}"
TENANT="${TENANT:-tenant_demo}"
FORMA="${WRAPUP_FORM:-dialog_wrapup_arc12_v1}"

RED=$'\e[31m'; GRN=$'\e[32m'; YEL=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
FAIL=0
ok()  { echo "  ${GRN}✓${RST} $*"; }
bad() { echo "  ${RED}✗${RST} $*"; FAIL=$((FAIL+1)); }
inconclusivo() { echo "  ${YEL}—${RST} INCONCLUSIVO: $1"; exit 2; }

echo "${BLD}probe_multi_answer_events — multi-seleção rende N eventos, nunca uma colagem${RST}"
echo

command -v docker >/dev/null || inconclusivo "docker ausente"
docker image inspect "$NODE_IMG" >/dev/null 2>&1 \
  || inconclusivo "imagem $NODE_IMG ausente (docker pull $NODE_IMG)"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ── A — a forma REAL, publicada; e ela precisa DECLARAR o que o probe julga ────
curl -sf "$DA/v1/dialog/forms/$FORMA?status=published" -H "X-Tenant-ID: $TENANT" \
  -o "$TMP/form.json" || inconclusivo "dialog-api não serviu '$FORMA' em $DA"

echo "A — controle positivo: a forma tem pergunta checklist com capture.kind"
OK_KEY="$(python3 -c "
import json,sys
d=json.load(open('$TMP/form.json'))
for n in d.get('nodes',[]):
    if n.get('interaction')=='checklist' and (n.get('capture') or {}).get('kind'):
        print(n.get('output_key') or n['id']); break
")"
[ -n "$OK_KEY" ] || inconclusivo "'$FORMA' não tem pergunta checklist com capture.kind — nada a julgar"
ok "pergunta '$OK_KEY' é checklist e declara captura"

# ── recorte da cadeia REAL ────────────────────────────────────────────────────
python3 - "$RAIZ" "$TMP" <<'PY'
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(sys.argv[1]) / "infra" / "test"))
from _ask_when_extract import recorta          # uma casa só para o casamento de chaves

raiz, saida = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
NL = chr(10)

alvos = [
    ("coerceMultiAnswer",
     "packages/skill-flow-engine/src/steps/menu.ts", "coerce.ts", ""),
    ("deriveAgentEvents",
     "packages/mcp-server-plughub/src/tools/segment.ts", "derive.ts",
     "type FormQuestion = any" + NL),
]
falhou = False
for nome, rel, arq, prelude in alvos:
    fonte = (raiz / rel).read_text(encoding="utf-8")
    corpo = recorta(fonte, nome)
    if not corpo:
        print("FALHA: nao achei " + nome + " em " + rel)
        falhou = True
        continue
    if not corpo.startswith("export"):
        corpo = "export " + corpo
    (saida / arq).write_text(prelude + corpo + NL, encoding="utf-8")
    print("OK " + nome + " · " + rel)
sys.exit(1 if falhou else 0)
PY
[ $? -eq 0 ] || inconclusivo "recorte falhou — função renomeada ou arquivo movido (acima)"

docker run --rm -v "$TMP:/t" -v "$RAIZ:/w:ro" -w /t "$NODE_IMG" \
  node /w/packages/platform-ui/node_modules/typescript/bin/tsc \
  --module es2020 --target es2020 --moduleResolution bundler \
  --noResolve --skipLibCheck --outDir /t coerce.ts derive.ts >"$TMP/tsc.log" 2>&1
[ -s "$TMP/coerce.js" ] && [ -s "$TMP/derive.js" ] || {
  sed 's/^/      /' "$TMP/tsc.log" | head -10
  inconclusivo "não transpilei os recortes (log acima)"; }

cat > "$TMP/run.mjs" <<'JS'
import { readFileSync } from 'node:fs'
import { coerceMultiAnswer } from '/t/coerce.js'
import { deriveAgentEvents } from '/t/derive.js'

const form = JSON.parse(readFileSync('/t/form.json', 'utf8'))
const key  = process.argv[2]
const ctx  = { poolId: 'sac_humano', skillId: 'wrapup' }

// A cadeia REAL: o bridge codifica a lista em JSON (transporte), o engine a
// desfaz e grava no pipeline_state, a tool deriva os eventos.
const doCanal = (marcacoes) => JSON.stringify(marcacoes)
const doEngine = (transporte) => coerceMultiAnswer(transporte, 'checklist')

const duas  = deriveAgentEvents(form, { [key]: doEngine(doCanal(['suporte', 'financeiro'])) }, ctx)
const uma   = deriveAgentEvents(form, { [key]: doEngine(doCanal(['suporte'])) }, ctx)
const crua  = deriveAgentEvents(form, { [key]: doEngine('suporte') }, ctx)  // canal sem JSON

console.log(JSON.stringify({ duas, uma, crua }))
JS

SAIDA="$(docker run --rm -v "$TMP:/t" -w /t "$NODE_IMG" node /t/run.mjs "$OK_KEY" 2>&1)"
echo "$SAIDA" | head -c 1 | grep -q '{' || {
  echo "$SAIDA" | sed 's/^/      /' | head -12
  inconclusivo "o runner não produziu JSON (log acima)"; }

CATS="$(python3 -c "
import json,sys
d=json.loads(sys.stdin.read())
print('DUAS', len(d['duas']), ' '.join(e['category'] for e in d['duas']))
print('UMA', len(d['uma']), ' '.join(e['category'] for e in d['uma']))
print('CRUA', len(d['crua']), ' '.join(e['category'] for e in d['crua']))
" <<<"$SAIDA")"

# ── B — duas marcações, dois eventos ──────────────────────────────────────────
echo
echo "B — duas marcações rendem DOIS eventos, um por folha"
LINHA="$(grep '^DUAS' <<<"$CATS")"
N="$(awk '{print $2}' <<<"$LINHA")"
if [ "$N" = "2" ]; then ok "2 eventos: $(cut -d' ' -f3- <<<"$LINHA")"
else bad "esperados 2 eventos, vieram $N — $(cut -d' ' -f3- <<<"$LINHA")"; fi

# ── C — testemunha NEGATIVA ───────────────────────────────────────────────────
echo
echo "C — nenhuma categoria com o padrão da colagem (\`_a_b_\`)"
if grep -qE '\.[a-z0-9_]*_[a-z0-9]+_[a-z0-9]+_' <<<"$CATS"; then
  bad "categoria colada presente — a lista virou UMA série inventada"
  echo "$CATS" | sed 's/^/        /'
else
  ok "nenhuma colagem: a lista não vira categoria única"
fi

# ── D — o caminho escalar não regrediu ────────────────────────────────────────
echo
echo "D — marcação única continua rendendo UM evento (JSON e cru)"
NU="$(awk '{print $2}' <<<"$(grep '^UMA' <<<"$CATS")")"
NC="$(awk '{print $2}' <<<"$(grep '^CRUA' <<<"$CATS")")"
if [ "$NU" = "1" ] && [ "$NC" = "1" ]; then
  ok "1 evento em ambos — canal que manda JSON e canal que manda escalar"
else
  bad "esperado 1 evento em cada; vieram JSON=$NU cru=$NC"
  echo "$CATS" | sed 's/^/        /'
fi

echo
if [ "$FAIL" -gt 0 ]; then
  echo "${RED}${BLD}VERMELHO${RST} — $FAIL ramo(s) reprovaram."
  exit 1
fi
echo "${GRN}${BLD}VERDE${RST} — multi-seleção chega ao Arc 12 como N eventos."
