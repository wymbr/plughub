#!/usr/bin/env bash
# probe_context_tag_extractor_parity.sh — GATE da fatia F2 da V4 (arco ALLOWLIST).
#
# PROPOSIÇÃO: *as duas implementações do extrator de escritas de ContextStore concordam?*
#
#   TS     packages/schemas/src/context-map.ts        collectContextTagWrites
#   Python infra/test/censo_contextstore_cadastro.py  extrair_de_doc
#
# POR QUE HÁ DUAS, E POR QUE ISSO PRECISA DE GATE
# ------------------------------------------------
# O portão de publish roda no agent-registry, que é TypeScript. O censo — o instrumento
# que dimensiona a migração e que a D9.2 disse que o gate consumiria — é Python. Nenhum
# dos dois pode virar o outro: o portão não vai chamar um script de `infra/test/`, e o
# censo não vai virar um serviço Node.
#
# A resposta da casa para "duas implementações da mesma regra" é sempre a mesma, e já foi
# usada três vezes neste arco: **fixture única + comparação literal**. Sem ela, as duas
# divergem em silêncio — e a divergência aqui é fail-open no portão: uma superfície que
# só o Python vê é uma superfície pela qual uma tag não cadastrada ENTRA.
#
# ⚠️ É a segunda vez que este arco pisa nisso. A D9.2 registra a primeira: *"a D9 contava
# duas superfícies de autoria; são SEIS, e quatro não aparecem caminhando a árvore"* —
# `context_json` é string JSON, `context_set`/`context_write` guardam o nome em
# `input.tag`, `delegate/collect.context` têm o prefixo composto NO GATEWAY, e
# `mention_commands` vive no flow e não num step. *"Um portão escrito da forma óbvia
# ficaria verde com quatro superfícies passando por baixo: fail-open por invisibilidade."*
#
# Cinco ramos. Três estados: OK / FALHA / INCONCLUSIVO (nunca OK com ramo inconclusivo).
set -u

cd "$(dirname "$0")/../.." || exit 2
RAIZ=$(pwd)

FIX=infra/test/fixtures/context_tag_surfaces.json
TMP=infra/test/.ctx_parity_tmp
rm -rf "$TMP"; mkdir -p "$TMP"
PY_OUT="$TMP/py.txt"
TS_OUT="$TMP/ts.txt"

FALHAS=0; INCONCL=0
ok()   { echo "  v $*"; }
bad()  { echo "  X $*"; FALHAS=$((FALHAS+1)); }
huh()  { echo "  ? $*"; INCONCL=$((INCONCL+1)); }

echo "=== probe_context_tag_extractor_parity — V4/F2 (uma regra, duas casas) ==="

[ -f "$FIX" ] || { echo "INCONCLUSIVO: fixture ausente ($FIX)"; exit 2; }

# ── A. metade Python ─────────────────────────────────────────────────────────
echo
echo "-- A. as duas metades RODAM --"
python3 infra/test/_ctx_extract_runner.py "$FIX" > "$PY_OUT" 2>"$TMP/py.err"
N_PY=$(grep -c . "$PY_OUT" 2>/dev/null || echo 0)
if [ "$N_PY" -eq 0 ]; then
  huh "A: runner Python não produziu saída — $(head -3 "$TMP/py.err" | tr '\n' ' ')"
  echo; echo "INCONCLUSIVO"; exit 2
fi
ok "A: Python produziu $N_PY linha(s)"

# ── metade TS, em container ──────────────────────────────────────────────────
# `npx` quebra com ERR_INVALID_URL a partir desta bancada (Windows sobre UNC), e o script
# vai para ARQUIVO de propósito: comando aninhado em três camadas de aspas
# (Git Bash -> wsl bash -> docker sh) já comeu um `$?` neste arco, e o `RC=0` resultante
# quase virou verde com `error TS2345` impresso na tela.
cat > "$TMP/run_ts.sh" <<'INNER'
#!/bin/sh
cd /repo/packages/schemas || exit 90
# esbuild em vez de tsx/ts-node: é o único transpilador já presente em node_modules, e
# esta bancada não tem rede dentro do container. `--bundle` puxa o FONTE do schemas
# junto, então o runner mede o fonte, nunca um `dist/` que pode estar atrasado.
./node_modules/.bin/esbuild --bundle --platform=node --format=cjs \
  --log-level=error --outfile=/tmp/ctx_runner.cjs \
  /repo/infra/test/_ctx_extract_runner.ts 2>/tmp/ts.err || exit 91
node /tmp/ctx_runner.cjs /repo/infra/test/fixtures/context_tag_surfaces.json 2>>/tmp/ts.err
INNER
chmod +x "$TMP/run_ts.sh"

if command -v wsl.exe >/dev/null 2>&1; then DOCKER="wsl.exe -d ubuntu -- bash -lc"
else                                        DOCKER="bash -lc"; fi
$DOCKER "docker run --rm -v ${RAIZ}:/repo node:20-alpine sh /repo/${TMP}/run_ts.sh" \
  > "$TS_OUT" 2>"$TMP/ts.err"

# Normaliza CRLF nas DUAS metades. Bancada, não contrato — os dois runners já corrigem na
# fonte; isto é cinto. Sem normalizar, o `diff` acusa TODAS as linhas por um byte e a
# divergência REAL some no ruído.
sed -i 's/\r$//' "$TS_OUT" "$PY_OUT" 2>/dev/null
sed -i '/^$/d'   "$TS_OUT" "$PY_OUT" 2>/dev/null

N_TS=$(grep -c . "$TS_OUT" 2>/dev/null || echo 0)
if [ "$N_TS" -eq 0 ]; then
  huh "A: runner TS não produziu saída — $(head -3 "$TMP/ts.err" | tr '\n' ' ')"
  echo; echo "INCONCLUSIVO"; exit 2
fi
ok "A: TS produziu $N_TS linha(s)"

# ── B. a fixture EXERCE as cinco superfícies ─────────────────────────────────
# Sem este ramo, duas implementações que ignorassem a mesma superfície concordariam
# perfeitamente — e o gate ficaria verde exatamente no caso que ele existe para pegar.
echo
echo "-- B. a fixture exerce TODAS as superfícies (senão o acordo é vazio) --"
FALTOU=""
for sup in context_tags.outputs delegate.context collect.context \
           mention.set_context context_json invoke.context_set invoke.context_write; do
  grep -q "	${sup}	" "$TS_OUT" || FALTOU="$FALTOU $sup"
done
if [ -n "$FALTOU" ]; then
  bad "B: superfície(s) sem nenhuma linha na saída TS —$FALTOU
       Duas implementações que ignoram a MESMA superfície concordam, e o acordo não
       significa nada. É a forma exata do fail-open por invisibilidade da D9.2."
else
  ok "B: as 7 superfícies aparecem na saída"
fi

# ── C. a fixture tem caso DINÂMICO e ele é marcado, não descartado ───────────
echo
echo "-- C. nome dinâmico é COLETADO e MARCADO --"
D_TS=$(grep -c '	dyn$' "$TS_OUT" 2>/dev/null || echo 0)
D_PY=$(grep -c '	dyn$' "$PY_OUT" 2>/dev/null || echo 0)
if [ "$D_TS" -eq 0 ] || [ "$D_PY" -eq 0 ]; then
  bad "C: dinâmicos TS=$D_TS Python=$D_PY — pelo menos um lado DESCARTA em vez de marcar.
       A D9.2 mediu ZERO nomes dinâmicos no repositório, e o modelo inteiro vive desse
       zero: um coletor que os ignore fica verde justamente quando a premissa quebrar."
else
  ok "C: dinâmicos marcados nos dois lados (TS=$D_TS Python=$D_PY)"
fi

# ── D. os NEGATIVOS não entraram ─────────────────────────────────────────────
echo
echo "-- D. o que NÃO é escrita de contexto ficou de fora --"
VAZOU=""
grep -q "isto_nao_e_contexto"  "$TS_OUT" && VAZOU="$VAZOU tool-alheia"
grep -q "nao_deve_aparecer"    "$TS_OUT" && VAZOU="$VAZOU context-em-step-nao-delegate"
grep -q "session.cliente.cpf"  "$TS_OUT" && VAZOU="$VAZOU context_tags.inputs(LEITURA)"
if [ -n "$VAZOU" ]; then
  bad "D: entrou o que não devia —$VAZOU"
else
  ok "D: tool alheia, 'context' fora de delegate/collect e 'inputs' (leitura) ficaram fora"
fi

# ── E. AS SAÍDAS SÃO IDÊNTICAS ───────────────────────────────────────────────
echo
echo "-- E. uma regra, duas casas --"
if diff -u "$PY_OUT" "$TS_OUT" > "$TMP/d.txt" 2>&1; then
  ok "E: as $N_TS linhas são IDÊNTICAS"
else
  bad "E: DIVERGÊNCIA entre as metades (- Python / + TS):"
  sed -n '4,40p' "$TMP/d.txt" | sed 's/^/       /'
fi

echo
echo "======================"
if [ "$FALHAS" -gt 0 ]; then
  echo "FALHA — $FALHAS ramo(s) reprovado(s)"; exit 1
elif [ "$INCONCL" -gt 0 ]; then
  echo "INCONCLUSIVO — $INCONCL ramo(s) sem julgar"; exit 2
fi
echo "OK — o extrator é o MESMO nas duas casas, e a fixture exerce as 7 superfícies"
