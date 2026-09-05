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
#   E  o CAMINHO da taxonomia ser colapsado, ou estourar o teto de            → VERMELHO
#      segmentos da `category` (F4) — o teto é LIDO do schema, nunca escrito
#      aqui, então subir a profundidade da árvore sem subir o teto reprova.
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
#   3. `sanitizeCategoryPath` voltando a trocar `.` por `_`  → E vermelho
#
# Uso:  bash infra/test/probe_multi_answer_events.sh
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NODE_IMG="${NODE_IMG:-node:20-alpine}"
DA="${DIALOG_API:-http://localhost:3760}"
TENANT="${TENANT:-tenant_demo}"
FORMA="${WRAPUP_FORM:-dialog_wrapup_arvore_v1}"

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
OK_KEY_FUNDA="$(python3 -c "
import json
d=json.load(open('$TMP/form.json'))
def prof(o,k=1):
    return max([prof(c,k+1) for c in (o.get('options') or [])], default=k)
for n in d.get('nodes',[]):
    opts=n.get('options') or []
    if (n.get('capture') or {}).get('kind') and opts and max(prof(o) for o in opts) >= 3:
        print(n.get('output_key') or n['id']); break
")"
[ -n "$OK_KEY" ] || inconclusivo "'$FORMA' não tem pergunta checklist com capture.kind — nada a julgar"
ok "pergunta '$OK_KEY' é checklist e declara captura"
[ -n "$OK_KEY_FUNDA" ] || inconclusivo "a forma nao tem pergunta com captura e arvore de 3+ niveis — o ramo E passaria por ausencia"
ok "pergunta '$OK_KEY_FUNDA' tem arvore de 3+ niveis (o ramo E mede nela)"

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
    ("sanitizeCategoryPath",
     "packages/mcp-server-plughub/src/tools/segment.ts", "sanitize.ts", ""),
    ("deriveAgentEvents",
     "packages/mcp-server-plughub/src/tools/segment.ts", "derive.ts",
     "type FormQuestion = any" + NL
     + 'import { sanitizeCategoryPath } from "./sanitize.js"' + NL
     + "const AGENT_EVENT_CATEGORY_MAX_SEGMENTS = Number(process.env.MAXSEG)" + NL),
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
  --noResolve --skipLibCheck --outDir /t coerce.ts derive.ts sanitize.ts >"$TMP/tsc.log" 2>&1
[ -s "$TMP/coerce.js" ] && [ -s "$TMP/derive.js" ] || {
  sed 's/^/      /' "$TMP/tsc.log" | head -10
  inconclusivo "não transpilei os recortes (log acima)"; }

cat > "$TMP/run.mjs" <<'JS'
import { readFileSync } from 'node:fs'
import { coerceMultiAnswer } from '/t/coerce.js'
import { deriveAgentEvents } from '/t/derive.js'

const form = JSON.parse(readFileSync('/t/form.json', 'utf8'))
const key  = process.argv[2]
const keyFunda = process.argv[3]
const ctx  = { poolId: 'sac_humano', skillId: 'wrapup' }

// A cadeia REAL: o bridge codifica a lista em JSON (transporte), o engine a
// desfaz e grava no pipeline_state, a tool deriva os eventos.
const doCanal = (marcacoes) => JSON.stringify(marcacoes)
const doEngine = (transporte) => coerceMultiAnswer(transporte, 'checklist')

const duas  = deriveAgentEvents(form, { [key]: doEngine(doCanal(['cadastro.segunda_via', 'cadastro.troca_titularidade'])) }, ctx)
const uma   = deriveAgentEvents(form, { [key]: doEngine(doCanal(['cadastro.segunda_via'])) }, ctx)
const crua  = deriveAgentEvents(form, { [key]: doEngine('nenhum') }, ctx)  // canal sem JSON

// F4 — o caminho da taxonomia tem de sobreviver como CAMINHO. O sanitizador
// antigo trocava `.` por `_` e colapsava a hierarquia num segmento so.
const caminho = deriveAgentEvents(
  form, { [keyFunda]: doEngine(doCanal(['financeiro.cobranca.indevida'])) }, ctx)

console.log(JSON.stringify({ duas, uma, crua, caminho }))
JS

MAXSEG="$(grep -oE "AGENT_EVENT_CATEGORY_MAX_SEGMENTS = [0-9]+" "$RAIZ/packages/schemas/src/agent-events.ts" | grep -oE "[0-9]+$")"
[ -n "$MAXSEG" ] || inconclusivo "nao li AGENT_EVENT_CATEGORY_MAX_SEGMENTS do schema"
SAIDA="$(docker run --rm -e MAXSEG="$MAXSEG" -v "$TMP:/t" -w /t "$NODE_IMG" node /t/run.mjs "$OK_KEY" "$OK_KEY_FUNDA" 2>&1)"
echo "$SAIDA" | head -c 1 | grep -q '{' || {
  echo "$SAIDA" | sed 's/^/      /' | head -12
  inconclusivo "o runner não produziu JSON (log acima)"; }

CATS="$(python3 -c "
import json,sys
d=json.loads(sys.stdin.read())
print('DUAS', len(d['duas']), ' '.join(e['category'] for e in d['duas']))
print('UMA', len(d['uma']), ' '.join(e['category'] for e in d['uma']))
print('CRUA', len(d['crua']), ' '.join(e['category'] for e in d['crua']))
print('CAMINHO', len(d['caminho']), ' '.join(e['category'] for e in d['caminho']))
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
echo "E — caminho de taxonomia mantem os pontos e cabe na categoria do Arc 12"
CAT="$(grep "^CAMINHO" <<<"$CATS" | cut -d" " -f3)"
NSEG="$(awk -F. "{print NF}" <<<"$CAT")"
if [[ "$CAT" != *".financeiro.cobranca.indevida" ]]; then
  bad "o caminho foi colapsado: $CAT"
elif [ "$NSEG" -gt "$MAXSEG" ]; then
  bad "categoria com $NSEG segmentos, acima do teto $MAXSEG — o evento seria REJEITADO"
else
  ok "$CAT — $NSEG segmentos, teto $MAXSEG"
fi
case "$CAT" in
  *".financeiro."*) ok "alcancavel por startsWith(...'.financeiro.')" ;;
  *) bad "o prefixo da pasta nao alcanca a folha — a agregacao hierarquica da D10 nao funciona" ;;
esac

echo
if [ "$FAIL" -gt 0 ]; then
  echo "${RED}${BLD}VERMELHO${RST} — $FAIL ramo(s) reprovaram."
  exit 1
fi
echo "${GRN}${BLD}VERDE${RST} — multi-seleção chega ao Arc 12 como N eventos."
