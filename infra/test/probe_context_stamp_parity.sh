#!/usr/bin/env bash
# probe_context_stamp_parity.sh — ALW-02 passo 2: as duas metades do carimbo concordam.
#
# ── Por que este gate existe, e por que ele É a decisão ──────────────────────────
#
# A ALW-02 escolheu **declaração compartilhada + duas implementações finas** em vez de um
# gêmeo Python solto (que reintroduz a cópia divergente que este arco persegue) ou de
# chamadas por rede ao funil TS (20 sítios de caminho quente contra a porta 3100, que saiu
# da LAN em 2026-09-01 por servir transporte anônimo).
#
# **Sem este gate, aquela escolha É o gêmeo solto com melhores intenções.** É ele, e nada
# mais, que separa as duas opções.
#
# O repositório tem o precedente que dá o tamanho do risco: o cálculo de sentimento teve
# duas implementações idênticas e, quando uma foi consertada, a que desenhava a tela ficou
# para trás — meses de "Neutral" sobre um cliente medido em -0,50.
#
# ── Como ele mede ────────────────────────────────────────────────────────────────
#
# Uma fixture única (`fixtures/context_stamp_cases.json`) alimenta os dois runners; as
# saídas vão com chaves ORDENADAS e são comparadas linha a linha. Caso novo entra na
# fixture e as duas metades passam a ser medidas contra ele de graça.
#
#   A. as duas metades RODAM (sem isso, "concordam" é sobre o vazio)
#   B. produzem o número de linhas da fixture — nem a mais, nem a menos
#   C. as saídas são IDÊNTICAS
#   D. testemunha de FALSEABILIDADE: o comparador acusa uma divergência plantada
#
# ── O ramo D não é cerimônia ─────────────────────────────────────────────────────
#
# Um gate de paridade tem um modo de falha próprio e silencioso: se os dois runners
# quebrarem do mesmo jeito (fixture não encontrada, saída vazia), o `diff` fica verde sobre
# nada. O A conta linhas contra a fixture e o D prova que o comparador reprova. Sem os
# dois, este arquivo é exatamente o teste que não pode ficar vermelho.
set -u
cd "$(dirname "$0")/../.." || exit 2

FAIL=0
ok()  { echo "  v $1"; }
bad() { echo "  x $1"; FAIL=1; }
huh() { echo "  ? $1"; FAIL=2; }

TMP="$(dirname "$0")/.parity_tmp"
mkdir -p "$TMP"
PY_OUT="$TMP/py.jsonl"
TS_OUT="$TMP/ts.jsonl"
trap 'rm -rf "$TMP"' EXIT

echo "=== probe_context_stamp_parity — ALW-02 (o carimbo tem UMA regra, em duas casas) ==="
echo

N_FIX=$(python3 -c "
import io,json
fx=json.load(io.open('infra/test/fixtures/context_stamp_cases.json',encoding='utf-8'))
print(len(fx['cases']))
" 2>/dev/null)
if [ -z "${N_FIX:-}" ] || [ "$N_FIX" -lt 1 ] 2>/dev/null; then
  huh "A: fixture ilegivel ou vazia"
  echo; echo "INCONCLUSIVO"; exit 2
fi
echo "     fixture: $N_FIX casos"

# ── A/B — metade Python ───────────────────────────────────────────────────────
if ! PYTHONIOENCODING=utf-8 python3 infra/test/_stamp_runner.py infra/test/fixtures/context_stamp_cases.json > "$PY_OUT" 2>"$TMP/py.err"; then
  huh "A: runner Python falhou — $(head -1 "$TMP/py.err")"
  echo; echo "INCONCLUSIVO"; exit 2
fi
N_PY=$(wc -l < "$PY_OUT" | tr -d ' ')
[ "$N_PY" = "$N_FIX" ] \
  && ok "A: Python rodou e produziu $N_PY linhas" \
  || bad "A: Python produziu $N_PY linhas para $N_FIX casos"

# ── A/B — metade TS ───────────────────────────────────────────────────────────
# Roda em container: a bancada e Windows sobre UNC, e `npx` quebra com ERR_INVALID_URL
# a partir daqui. O script vai para arquivo de proposito — comando aninhado em tres
# camadas de aspas (Git Bash -> wsl bash -> docker sh) ja comeu um `$?` neste arco, e o
# `RC=0` resultante quase virou verde com `error TS2345` impresso na tela.
cat > "$TMP/run_ts.sh" <<'INNER'
#!/bin/sh
cd /repo/packages/schemas || exit 90
# esbuild em vez de tsx/ts-node: e o unico transpilador ja presente em node_modules, e
# esta bancada nao tem rede dentro do container. `--bundle` puxa o fonte do schemas
# junto, entao o runner mede o FONTE, nunca um `dist/` que pode estar atrasado.
./node_modules/.bin/esbuild --bundle --platform=node --format=cjs \
  --log-level=error --outfile=/tmp/runner.cjs \
  /repo/infra/test/_stamp_runner.ts 2>/tmp/ts.err || exit 91
node /tmp/runner.cjs /repo/infra/test/fixtures/context_stamp_cases.json 2>>/tmp/ts.err
INNER
chmod +x "$TMP/run_ts.sh"

if command -v wsl.exe >/dev/null 2>&1; then
  DOCKER="wsl.exe -d ubuntu -- bash -lc"
else
  DOCKER="bash -lc"
fi
$DOCKER "docker run --rm -v /home/a1/projects/plughub:/repo node:20-alpine sh /repo/infra/test/.parity_tmp/run_ts.sh" \
  > "$TS_OUT" 2>"$TMP/ts.err"
# Normaliza CRLF nas DUAS metades. Bancada, nao contrato — e a atribuicao importa: quem
# suja e o `print()` do python de WINDOWS (traduz LF em CRLF no modo texto), NAO o
# `wsl.exe`, como a primeira versao deste gate presumia — normalizei a metade errada por
# ter presumido a causa em vez de medi-la. O runner ja corrige na fonte; isto e cinto para
# outra bancada. Sem a normalizacao o `diff` acusa TODAS as linhas por um byte e a
# divergencia real some no ruido.
sed -i 's/$//' "$TS_OUT" "$PY_OUT" 2>/dev/null
sed -i '/^$/d'   "$TS_OUT" "$PY_OUT" 2>/dev/null

N_TS=$(wc -l < "$TS_OUT" | tr -d ' ')
if [ "$N_TS" = "0" ]; then
  huh "A: runner TS nao produziu saida — $(head -3 "$TMP/ts.err" | tr '\n' ' ')"
  echo; echo "INCONCLUSIVO"; exit 2
fi
[ "$N_TS" = "$N_FIX" ] \
  && ok "A: TS rodou e produziu $N_TS linhas" \
  || bad "A: TS produziu $N_TS linhas para $N_FIX casos"

ok "B: as duas metades produziram linha para cada caso da fixture"

# ── C — as saidas sao IDENTICAS ───────────────────────────────────────────────
if diff -u "$PY_OUT" "$TS_OUT" > "$TMP/d.txt" 2>&1; then
  ok "C: as $N_FIX saidas sao IDENTICAS — uma regra, duas casas"
else
  bad "C: DIVERGENCIA entre as metades:"
  sed -n '1,40p' "$TMP/d.txt" | sed 's/^/       /'
fi

# ── D — testemunha de falseabilidade ──────────────────────────────────────────
sed 's/"origem":"canonical"/"origem":"PLANTADO"/' "$PY_OUT" > "$TMP/py_mut.jsonl"
if diff -q "$TMP/py_mut.jsonl" "$PY_OUT" >/dev/null 2>&1; then
  huh "D: a mutacao plantada nao mudou nada — o comparador nao esta sendo exercido"
elif diff -q "$TMP/py_mut.jsonl" "$TS_OUT" >/dev/null 2>&1; then
  bad "D: o comparador ACEITOU uma saida divergente"
else
  ok "D: o comparador reprova divergencia plantada"
fi

echo
if [ "$FAIL" = "0" ]; then
  echo "OK — o carimbo tem UMA regra, e as duas casas concordam nos $N_FIX casos"
elif [ "$FAIL" = "2" ]; then
  echo "INCONCLUSIVO"
else
  echo "FALHA"
fi
exit "$FAIL"
