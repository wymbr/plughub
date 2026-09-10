#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# probe_masked_field_echo_parity — as TRÊS casas do eco mascarado concordam.
#
# ── Por que existe ────────────────────────────────────────────────────────────
#
# O que o operador vê no lugar do valor de um campo mascarado é decidido em três
# lugares, em dois serviços e duas linguagens:
#
#   1. `orchestrator-bridge` · `main.masked_field_echo`
#        → eco AO VIVO (pub/sub `agent:events:{sid}`), o que chega depois
#   2. `channel-gateway` · `adapters.webchat.masked_field_echo`
#        → HISTÓRICO (`session:{sid}:messages`), o que a MESMA tela relê num F5
#   3. `platform-ui` · `agent-assist/maskedFieldEcho.ts`
#        → eco OTIMISTA do Console, o que aparece ANTES do round-trip
#
# Elas não podem se importar (dois serviços, e o Console não importa
# `@plughub/schemas` por decisão declarada). Até 2026-09-10 divergiam de verdade,
# e o operador via: **um campo ao vivo, três depois de recarregar a página** — a
# mesma submissão, contada de dois jeitos na mesma tela.
#
# ⚠️ Divergir entre 1 e 3 tem sintoma próprio: o campo **PISCA** — aparece no eco
# otimista e some quando o bridge responde. A ALW-10 já PEDIA que as duas
# concordassem, em prosa. O que faltava era o mecanismo, e é este arquivo.
#
# ── O que ele julga, e o que NÃO julga ────────────────────────────────────────
#
# Julga ACORDO, executando as três contra a MESMA tabela (`_masked_field_echo_cases.json`).
# Não julga se a regra está certa — isso é dos testes de unidade de cada casa.
#
# ⚠️ **Acordo sozinho não basta**, e é por isso que há testemunha: três casas
# igualmente quebradas concordariam. A tabela tem de produzir **as duas classes**
# (`••••••` e vazio); se produzir só uma, o instrumento não discrimina e o
# veredicto é INCONCLUSIVO — nunca verde.
#
# ⚠️ A casa TypeScript é COMPILADA com o `tsc` de verdade e EXECUTADA. Ler o
# fonte com regex diria que a função existe; só a execução diz o que ela devolve,
# e é a devolução que precisa concordar.
#
# Uso:  bash infra/test/probe_masked_field_echo_parity.sh
# Pré:  containers do bridge, do channel-gateway e do skill-flow-service no ar
#       (o último só empresta o `tsc`).
# SAÍDA: 0 = as três concordam · 1 = DIVERGEM · 2 = INCONCLUSIVO
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CT_BRIDGE="${CT_BRIDGE:-plughub-demo-orchestrator-bridge-1}"
CT_GATEWAY="${CT_GATEWAY:-plughub-demo-channel-gateway-1}"
CT_TSC="${CT_TSC:-plughub-demo-skill-flow-service-1}"

CASOS="$RAIZ/infra/test/_masked_field_echo_cases.json"
HARNESS_PY="$RAIZ/infra/test/_masked_field_echo_py.py"
HARNESS_TS="$RAIZ/infra/test/_masked_field_echo_ts.ts"
FONTE_TS="$RAIZ/packages/platform-ui/src/modules/agent-assist/maskedFieldEcho.ts"

RED=$'\e[31m'; GRN=$'\e[32m'; YEL=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
inc() { echo "  ${YEL}—${RST} ⛔ INCONCLUSIVO: $*"; exit 2; }

echo "${BLD}══ eco de campo mascarado — as três casas concordam? ══${RST}"

for f in "$CASOS" "$HARNESS_PY" "$HARNESS_TS" "$FONTE_TS"; do
  [ -f "$f" ] || inc "arquivo ausente: $f"
done

N_CASOS="$(python3 -c "import json,sys;print(len(json.load(open(sys.argv[1],encoding='utf-8'))))" "$CASOS" 2>/dev/null)"
case "${N_CASOS:-x}" in ''|*[!0-9]*) inc "tabela de casos ilegível" ;; esac
[ "$N_CASOS" -ge 4 ] || inc "tabela com $N_CASOS caso(s) — pequena demais para discriminar"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# ── casa 1 e 2: Python, cada uma no SEU container ────────────────────────────
casa_py() {  # $1 = container · $2 = módulo · $3 = arquivo de saída
  docker cp "$HARNESS_PY" "$1:/tmp/_mfe_py.py" >/dev/null 2>&1 \
    || { echo "COPIA_FALHOU"; return 2; }
  docker exec -i "$1" python /tmp/_mfe_py.py "$2" < "$CASOS" > "$3" 2>"$3.err"
}

casa_py "$CT_BRIDGE"  "plughub_orchestrator_bridge.main"          "$TMP/bridge.tsv"
[ -s "$TMP/bridge.tsv" ] || inc "bridge não produziu saída: $(head -2 "$TMP/bridge.tsv.err" 2>/dev/null)"

casa_py "$CT_GATEWAY" "plughub_channel_gateway.adapters.webchat"  "$TMP/gateway.tsv"
[ -s "$TMP/gateway.tsv" ] || inc "channel-gateway não produziu saída: $(head -2 "$TMP/gateway.tsv.err" 2>/dev/null)"

# ── casa 3: TypeScript, compilada e executada ────────────────────────────────
docker exec "$CT_TSC" sh -c 'rm -rf /tmp/_mfe && mkdir -p /tmp/_mfe' >/dev/null 2>&1 \
  || inc "não consegui preparar /tmp/_mfe em $CT_TSC"
docker cp "$FONTE_TS"   "$CT_TSC:/tmp/_mfe/maskedFieldEcho.ts"    >/dev/null 2>&1 || inc "cópia do fonte TS falhou"
docker cp "$HARNESS_TS" "$CT_TSC:/tmp/_mfe/_masked_field_echo_ts.ts" >/dev/null 2>&1 || inc "cópia do harness TS falhou"

TSC_OUT="$(docker exec "$CT_TSC" sh -c '
  cd /tmp/_mfe &&
  /app/packages/skill-flow-engine/node_modules/.bin/tsc \
      --module commonjs --target es2020 --moduleResolution node \
      --strict --skipLibCheck --types node \
      --typeRoots /app/packages/skill-flow-engine/node_modules/@types \
      _masked_field_echo_ts.ts maskedFieldEcho.ts 2>&1' 2>&1)"
docker exec "$CT_TSC" sh -c '[ -f /tmp/_mfe/_masked_field_echo_ts.js ]' 2>/dev/null \
  || inc "o \`tsc\` não produziu JS — a casa do Console não pôde ser EXECUTADA:
      $(printf '%s' "$TSC_OUT" | head -4)"

docker exec -i "$CT_TSC" node /tmp/_mfe/_masked_field_echo_ts.js < "$CASOS" > "$TMP/console.tsv" 2>"$TMP/console.err"
[ -s "$TMP/console.tsv" ] || inc "a casa do Console não produziu saída: $(head -2 "$TMP/console.err" 2>/dev/null)"

# ── testemunha: a tabela DISCRIMINA? ─────────────────────────────────────────
N_OCULTO="$(cut -f2 "$TMP/bridge.tsv" | grep -c '•' || true)"
N_VAZIO="$(awk -F'\t' '$2 == "" {n++} END {print n+0}' "$TMP/bridge.tsv")"
echo "      casos: $N_CASOS · com valor oculto: ${N_OCULTO:-0} · vazios: ${N_VAZIO:-0}"
[ "${N_OCULTO:-0}" -gt 0 ] || inc "nenhum caso produziu \`••••••\` — a tabela não exerce a classe
      PREENCHIDO, e três casas igualmente quebradas concordariam."
[ "${N_VAZIO:-0}" -gt 0 ]  || inc "nenhum caso produziu vazio — a tabela não exerce a classe
      VAZIO, que é justamente a distinção que 2026-09-10 acrescentou."

# ── o veredicto ──────────────────────────────────────────────────────────────
DIV=0
echo
while IFS=$'\t' read -r idx saida_b; do
  saida_g="$(awk -F'\t' -v i="$idx" '$1 == i {print $2}' "$TMP/gateway.tsv")"
  saida_c="$(awk -F'\t' -v i="$idx" '$1 == i {print $2}' "$TMP/console.tsv")"
  if [ "$saida_b" != "$saida_g" ] || [ "$saida_b" != "$saida_c" ]; then
    entrada="$(python3 -c "
import json,sys
print(json.dumps(json.load(open(sys.argv[1],encoding='utf-8'))[int(sys.argv[2])], ensure_ascii=False))
" "$CASOS" "$idx" 2>/dev/null)"
    echo "   ${RED}✗${RST} caso $idx  entrada=$entrada"
    echo "        bridge=[$saida_b]  gateway=[$saida_g]  console=[$saida_c]"
    DIV=$((DIV + 1))
  fi
done < "$TMP/bridge.tsv"

echo "── veredicto ───────────────────────────────────────────────────────────────"
if [ "$DIV" -eq 0 ]; then
  echo "   ${GRN}✅${RST} as três casas devolvem o mesmo em $N_CASOS caso(s),"
  echo "      com as DUAS classes exercidas — a testemunha que separa acordo real"
  echo "      de três implementações igualmente quebradas."
  exit 0
fi
echo "   ${RED}❌${RST} $DIV caso(s) em que as casas DIVERGEM."
echo "      Divergência entre o bridge e o Console faz o campo PISCAR na tela;"
echo "      entre o bridge e o gateway, muda o que o operador vê depois de um F5."
exit 1
