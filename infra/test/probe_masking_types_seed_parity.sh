#!/usr/bin/env bash
# probe_masking_types_seed_parity.sh — o catalogo de tipos tem DUAS casas, e uma
# delas so e exercida em instalacao limpa.
#
# ── Por que existe ───────────────────────────────────────────────────────────
#
# `DEFAULT_DATA_TYPE_CATALOG` (@plughub/schemas/audit.ts) e a AUTORIDADE, e
# `seed.py` e a copia que semeia uma base vazia. Nada as comparava.
#
# Medido em 2026-09-04: `valor_declarado_pelo_cliente`, criado em 2026-09-02, estava
# na TS e na config VIVA e **nao estava no seed**. O buraco e MUDO no ambiente que ja
# subiu — a config viva ja o tem — e so aparece num `--wipe`, onde o `preview` de
# `skill_limite_processo_v1` passa a citar um tipo que o catalogo nao conhece, o
# caminho conservador mascara com `full`, e o cliente deixa de ver o valor que ele
# mesmo pediu. Ninguem fica vermelho.
#
# E a familia "ambiente que so sobe porque ja subiu antes" do CLAUDE.md, com o
# agravante de a copia divergente ser a que ninguem roda no dia a dia.
#
# ── O que cada ramo julga ────────────────────────────────────────────────────
#
#   A. as duas metades RODAM e produzem catalogo nao vazio (testemunha de presenca)
#   B. todo tipo da TS existe no seed  — a direcao que o defeito medido violou
#   C. `by_role` e `lgpd` CONCORDAM    — presenca nao basta: um tipo com mascara
#                                        diferente nas duas casas e pior que ausente
#   D. o comparador reprova divergencia plantada (falseabilidade)
#
# ⚠️ A direcao B e deliberadamente de UM lado. O seed pode ter tipo que a TS nao
# tem? Hoje nao, e o ramo B2 do `probe_context_map_audit` ja cobre "tipo citado que
# nao existe". Exigir igualdade nos dois sentidos transformaria em vermelho um
# regime legitimo futuro (tipo semeado so para um tenant), e a regra que interessa e
# a autoridade CHEGAR na copia.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2

FALHAS=0
INC=0
ok()  { echo "  v $*"; }
bad() { echo "  x $*"; FALHAS=$((FALHAS+1)); }
huh() { echo "  ? $*"; INC=$((INC+1)); }

echo "=== probe_masking_types_seed_parity ==="
echo

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# ── metade TS: le o FONTE via esbuild, nunca o dist/ (que pode estar atrasado) ──
cat > "$TMP/ts.sh" <<'INNER'
#!/bin/sh
cd /repo/packages/schemas || exit 90
cat > /tmp/cat.ts <<'EOF'
import { DEFAULT_DATA_TYPE_CATALOG } from "/repo/packages/schemas/src/audit"
const o: Record<string, unknown> = {}
for (const t of DEFAULT_DATA_TYPE_CATALOG.types) {
  o[t.id] = { by_role: t.mascara?.by_role ?? {}, lgpd: t.lgpd }
}
// ⚠️ NUNCA  — o 2o argumento e o
// REPLACER, nao uma ordenacao: passar os ids ali filtra as chaves INTERNAS e
// zera todo objeto ( para os 15). Foi o que a primeira versao fez, e o
// ramo C a pegou. A comparacao e por dict, entao ordem nao importa.
process.stdout.write(JSON.stringify(o))
EOF
./node_modules/.bin/esbuild --bundle --platform=node --format=cjs \
  --log-level=error --outfile=/tmp/cat.cjs /tmp/cat.ts 2>/tmp/ts.err || exit 91
node /tmp/cat.cjs 2>>/tmp/ts.err
INNER
chmod +x "$TMP/ts.sh"

docker run --rm -v "$PWD:/repo" -v "$TMP:/t" node:20-alpine sh /t/ts.sh > "$TMP/ts.json" 2>"$TMP/ts.err"

PY_JSON="$TMP/py.json"
python3 infra/test/_masking_types_seed_census.py > "$PY_JSON" 2>"$TMP/py.err" \
  || { huh "A: censo do seed falhou — $(head -1 "$TMP/py.err")"; }

N_TS=$(python3 -c "import json,sys;print(len(json.load(open(sys.argv[1]))))" "$TMP/ts.json" 2>/dev/null || echo 0)
N_PY=$(python3 -c "import json,sys;print(len(json.load(open(sys.argv[1]))))" "$PY_JSON" 2>/dev/null || echo 0)

if [ "${N_TS:-0}" -lt 5 ] || [ "${N_PY:-0}" -lt 5 ]; then
  huh "A: catalogo vazio ou ilegivel (TS=$N_TS seed=$N_PY) — $(head -c 200 "$TMP/ts.err")"
  echo; echo "INCONCLUSIVO"; exit 2
fi
ok "A: as duas metades rodaram (TS=$N_TS tipos, seed=$N_PY tipos)"

comparar() {
  python3 - "$1" "$2" <<'PY'
import json, sys
ts = json.load(open(sys.argv[1]))
sd = json.load(open(sys.argv[2]))
faltam = sorted(set(ts) - set(sd))
diverg = sorted(k for k in set(ts) & set(sd) if ts[k] != sd[k])
print(json.dumps({"faltam": faltam, "divergem": {
    k: {"ts": ts[k], "seed": sd[k]} for k in diverg}}))
PY
}

R=$(comparar "$TMP/ts.json" "$PY_JSON")
FALTAM=$(echo "$R" | python3 -c "import json,sys;print(len(json.load(sys.stdin)['faltam']))")
DIVERG=$(echo "$R" | python3 -c "import json,sys;print(len(json.load(sys.stdin)['divergem']))")

if [ "$FALTAM" = "0" ]; then
  ok "B: todo tipo da autoridade TS existe no seed"
else
  bad "B: $FALTAM tipo(s) da TS AUSENTES no seed — instalacao limpa nasce sem eles"
  echo "$R" | python3 -c "import json,sys;[print('       '+x) for x in json.load(sys.stdin)['faltam']]"
fi

if [ "$DIVERG" = "0" ]; then
  ok "C: by_role e lgpd concordam nas duas casas"
else
  bad "C: $DIVERG tipo(s) com politica DIFERENTE entre TS e seed"
  echo "$R" | python3 -c "
import json,sys
for k,v in json.load(sys.stdin)['divergem'].items():
    print('       %s  ts=%s  seed=%s' % (k, v['ts'], v['seed']))"
fi

# ── D — falseabilidade: planta a remocao de um tipo no lado do seed ──────────
python3 - "$PY_JSON" "$TMP/py_mut.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
alvo = sorted(d)[0]
del d[alvo]
json.dump(d, open(sys.argv[2], "w"))
PY
R_MUT=$(comparar "$TMP/ts.json" "$TMP/py_mut.json")
F_MUT=$(echo "$R_MUT" | python3 -c "import json,sys;print(len(json.load(sys.stdin)['faltam']))")
if [ "$F_MUT" -ge 1 ]; then
  ok "D: o comparador acusa um tipo removido do seed"
else
  bad "D: o comparador NAO acusou a remocao plantada — ele nao pode reprovar"
fi

echo
if [ "$FALHAS" -gt 0 ]; then echo "FALHA ($FALHAS)"; exit 1; fi
if [ "$INC" -gt 0 ]; then echo "INCONCLUSIVO ($INC)"; exit 2; fi
echo "OK — a autoridade TS chega inteira ao seed"
