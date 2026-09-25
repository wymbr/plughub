#!/usr/bin/env bash
# probe_detect_validator_parity.sh — CTX-12 (2026-09-25)
#
# PERGUNTA: o validador de DETECÇÃO (`cpf_dv`) decide igual nos quatro motores — e decide CERTO?
#
# POR QUE EXISTE
#   A regex não calcula dígito verificador. Separar CPF cru de telefone exigiu código, e o
#   código vive em quatro motores: a rede do engine e o `MaskingService` (os dois via
#   `passesDetectValidator`, em @plughub/schemas), o channel-gateway (via
#   `plughub_contextstore.masking.passes_detect_validator`) e a CÓPIA do quality-ingest.
#   Sem comparação, é o regime dos sete inventários de categoria que já divergiram calados.
#
# RAMOS
#   A  as duas metades rodam e produzem uma linha por caso (testemunha de presença)
#   B  os dois motores PYTHON concordam entre si (nenhuma linha `DIVERGE`)
#   C  TS e Python produzem saídas IDÊNTICAS
#   D  o comparador reprova divergência plantada (falseabilidade)
#   E  os valores são os CERTOS — motores que erram igual também concordam; o que decide
#      o certo é a medição de 2026-09-25 (DV separa CPF de celular sem colisão)
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO
set -u
cd "$(dirname "$0")/../.." || exit 2

FAIL=0
ok()  { echo "  v $1"; }
bad() { echo "  x $1"; FAIL=1; }
huh() { echo "  ? $1"; [ "$FAIL" = 0 ] && FAIL=2; }

FX=infra/test/fixtures/detect_validator_cases.json
TMP="$(dirname "$0")/.detect_tmp"
mkdir -p "$TMP"
PY_OUT="$TMP/py.jsonl"
TS_OUT="$TMP/ts.jsonl"
trap 'rm -rf "$TMP"' EXIT

echo "=== probe_detect_validator_parity — CTX-12 (um validador, quatro motores) ==="
echo

N_FIX=$(python3 -c "
import io,json
fx=json.load(io.open('$FX',encoding='utf-8'))
print(len(fx['validator_cases'])+len(fx['text_cases']))
" 2>/dev/null)
if [ -z "${N_FIX:-}" ] || [ "$N_FIX" -lt 1 ] 2>/dev/null; then
  huh "A: fixture ilegível ou vazia"; echo; echo "INCONCLUSIVO"; exit 2
fi
echo "     fixture: $N_FIX casos"

if ! PYTHONIOENCODING=utf-8 python3 infra/test/_detect_runner.py "$FX" > "$PY_OUT" 2>"$TMP/py.err"; then
  huh "A: runner Python falhou — $(head -1 "$TMP/py.err")"; echo; echo "INCONCLUSIVO"; exit 2
fi

cat > "$TMP/run_ts.sh" <<'INNER'
cd /repo/packages/schemas || exit 90
./node_modules/.bin/esbuild --bundle --platform=node --format=cjs \
  --log-level=error --outfile=/tmp/runner.cjs \
  /repo/infra/test/_detect_runner.ts 2>/tmp/ts.err || exit 91
node /tmp/runner.cjs /repo/infra/test/fixtures/detect_validator_cases.json 2>>/tmp/ts.err
INNER
chmod +x "$TMP/run_ts.sh"
if command -v wsl.exe >/dev/null 2>&1; then DOCKER="wsl.exe -d ubuntu -- bash -lc"; else DOCKER="bash -lc"; fi
$DOCKER "docker run --rm -v /home/a1/projects/plughub:/repo node:20-alpine sh /repo/infra/test/.detect_tmp/run_ts.sh" \
  > "$TS_OUT" 2>"$TMP/ts.err"
sed -i 's/\r$//' "$TS_OUT" "$PY_OUT" 2>/dev/null
sed -i '/^$/d'   "$TS_OUT" "$PY_OUT" 2>/dev/null

N_PY=$(wc -l < "$PY_OUT" | tr -d ' ')
N_TS=$(wc -l < "$TS_OUT" | tr -d ' ')
if [ "$N_TS" = "0" ]; then
  huh "A: runner TS não produziu saída — $(head -3 "$TMP/ts.err" | tr '\n' ' ')"; echo; echo "INCONCLUSIVO"; exit 2
fi
[ "$N_PY" = "$N_FIX" ] && [ "$N_TS" = "$N_FIX" ] \
  && ok "A: as duas metades produziram $N_FIX linhas" \
  || bad "A: Python=$N_PY TS=$N_TS linhas para $N_FIX casos"

if grep -q DIVERGE "$PY_OUT"; then
  bad "B: os dois motores Python DIVERGEM:"; grep DIVERGE "$PY_OUT" | sed 's/^/       /'
else
  ok "B: py-contextstore (channel-gateway) e a cópia do quality-ingest concordam"
fi

if diff -u "$PY_OUT" "$TS_OUT" > "$TMP/d.txt" 2>&1; then
  ok "C: as $N_FIX saídas são IDÊNTICAS entre TS e Python"
else
  bad "C: DIVERGÊNCIA entre TS e Python:"; sed -n '1,30p' "$TMP/d.txt" | sed 's/^/       /'
fi

sed 's/"passa":true/"passa":false/' "$PY_OUT" > "$TMP/py_mut.jsonl"
if diff -q "$TMP/py_mut.jsonl" "$PY_OUT" >/dev/null 2>&1; then
  huh "D: a mutação plantada não mudou nada — o comparador não foi exercido"
elif diff -q "$TMP/py_mut.jsonl" "$TS_OUT" >/dev/null 2>&1; then
  bad "D: o comparador ACEITOU a divergência plantada"
else
  ok "D: o comparador reprova divergência plantada"
fi

# E — os valores esperados (golden). Chave: name → valor serializado.
ESPERADO='
cpf_cru_dv_valido={"name":"cpf_cru_dv_valido","passa":true}
cpf_cru_dv_errado={"name":"cpf_cru_dv_errado","passa":false}
celular_cru={"name":"celular_cru","passa":false}
digitos_repetidos={"name":"digitos_repetidos","passa":false}
zeros={"name":"zeros","passa":false}
cpf_cru_dv_valido_zero={"name":"cpf_cru_dv_valido_zero","passa":true}
pontuado_dv_errado={"name":"pontuado_dv_errado","passa":true}
pontuado_dv_valido={"name":"pontuado_dv_valido","passa":true}
nao_onze_digitos={"name":"nao_onze_digitos","passa":true}
sem_validador={"name":"sem_validador","passa":true}
validador_desconhecido={"name":"validador_desconhecido","passa":false}
texto_cpf_cru={"categorias":["cpf"],"name":"texto_cpf_cru"}
texto_celular_cru={"categorias":["phone"],"name":"texto_celular_cru"}
texto_cpf_e_celular={"categorias":["cpf","phone"],"name":"texto_cpf_e_celular"}
texto_cpf_cru_dv_errado={"categorias":["phone"],"name":"texto_cpf_cru_dv_errado"}
texto_dois_cpfs_crus={"categorias":["cpf"],"name":"texto_dois_cpfs_crus"}
texto_cartao={"categorias":["credit_card"],"name":"texto_cartao"}
texto_sem_dado={"categorias":[],"name":"texto_sem_dado"}
'
ERR_E=0
while IFS='=' read -r nome esperado; do
  [ -z "$nome" ] && continue
  obtido=$(grep -F "\"name\":\"$nome\"" "$TS_OUT" | head -1)
  if [ "$obtido" != "$esperado" ]; then
    bad "E: $nome — esperado $esperado, obtido ${obtido:-<ausente>}"; ERR_E=1
  fi
done <<< "$ESPERADO"
[ "$ERR_E" = 0 ] && ok "E: os $N_FIX valores são os esperados (CPF cru válido → cpf; DV errado e celular → phone)"

echo
case "$FAIL" in
  0) echo "OK"; exit 0 ;;
  2) echo "INCONCLUSIVO"; exit 2 ;;
  *) echo "FALHA"; exit 1 ;;
esac
