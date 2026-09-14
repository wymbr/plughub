#!/usr/bin/env bash
# probe_identity_evidence.sh — 2026-09-13  (PID-02)
#
# PERGUNTA: a evidência de identidade só é gravada por quem VERIFICA — e é gravada de fato?
#
# O DEFEITO QUE O ORIGINOU
#   `context_set` e `/api/inject-context` gravavam qualquer tag, e `core.*` não era reservado
#   na escrita. Medido antes do fix, numa sessão sintética: `context_set` gravou
#   `core.journey.identity.otp.status = verified`, o inject-context também (200), e
#   `otp_verify` sem token de sessão agiu. Nada produzia a evidência de verdade.
#
# CINCO RAMOS
#   A  UMA REGRA — prefixos reservados e o prefixo dinâmico idênticos em TS, Python e no mapa
#      VIVO do config-api; os quatro funis genéricos carregam o guard; `writeIdentityEvidence`
#      só é chamado pelas tools de OTP.
#   B  FUNIL TS VIVO — context_set e inject-context recusam os dois ramos sem gravar; controles;
#      otp_verify sem token recusa.
#   C  FUNIL PYTHON NA IMAGEM — raise recusa, warn descarta e loga, controle; mutação.
#   D  PRODUTOR PONTA A PONTA — roda a jornada (OTP dentro do chat) e exige a evidência
#      `verified` gravada no intervalo, com a sessão que provou, `verified_at` e o escritor
#      `identity:otp`.
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

MCP="${MCP_CONTAINER:-plughub-demo-mcp-server-plughub-1}"
GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
TENANT="${TENANT:-tenant_demo}"
CFG="${CFG:-http://localhost:3600}"
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
jexpr() { printf '%s' "$1" | python3 -c 'import json,sys
try:
    d = json.loads(sys.stdin.read())
    print(eval(sys.argv[1]))
except Exception as e:
    print("__ERRO__ %s" % e)' "$2"; }
julga() { for k in $2; do [ "$(jexpr "$1" "d['casos'].get('$k')")" = "True" ] && ok "$k" || falha "$k"; done; }
pyroda() { docker exec -i "$GW" python - "$@" < infra/test/_identity_evidence_py_exercise.py 2>/dev/null | tail -1; }

echo "════════════════════════════════════════════════════════════════════"
echo " a evidência de identidade só é gravada por quem verifica?"
echo "════════════════════════════════════════════════════════════════════"

echo ""
echo "── A · UMA REGRA ──────────────────────────────────────────────────────"
A=$(python3 - <<'EOF'
import json, re
ts = open("packages/schemas/src/identity-evidence.ts", encoding="utf-8").read()
tsmap = open("packages/schemas/src/context-map.ts", encoding="utf-8").read()
py = open("packages/py-contextstore/src/plughub_contextstore/__init__.py", encoding="utf-8").read()
pymap = open("packages/py-contextstore/src/plughub_contextstore/default_map.py", encoding="utf-8").read()
lista = lambda txt, nome: sorted(re.findall(r'"([^"]+)"', re.search(nome + r"[^=]*=\s*[\[(]([^\])]*)[\])]", txt).group(1)))
out = {
  "reserva_ts": lista(ts, "RESERVED_IDENTITY_PREFIXES"),
  "reserva_py": lista(py, "RESERVED_IDENTITY_PREFIXES"),
  "dyn_py": lista(py, "DEFAULT_DYNAMIC_PREFIXES"),
  "dyn_ts_default": "core.journey.identity." in re.search(r"dynamic_prefixes: z\.array[^\n]*", tsmap).group(0),
  "dyn_ts_map": "core.journey.identity." in re.search(r'\n  dynamic_prefixes: \[[^\n]*', tsmap).group(0),
  "dyn_pymap": "core.journey.identity." in re.search(r'"dynamic_prefixes": \[[^\n]*', pymap).group(0),
}
guardas = {
  "mcp writeContextTag": ("packages/mcp-server-plughub/src/tools/journey.ts", r"if \(isReservedIdentityTag\(tag\)\) throw new ReservedContextTagError"),
  "skill-flow-service ContextStore.set": ("packages/e2e-tests/services/skill-flow-service/src/context-store.ts", r"if \(isReservedIdentityTag\(tag\)\)"),
  "sdk ContextStore.set": ("packages/sdk/src/context-store.ts", r"if \(isReservedIdentityTag\(tag\)\)"),
  "py write_context_tags": ("packages/py-contextstore/src/plughub_contextstore/writer.py", r"reservadas = sorted\(t for t in tags if is_reserved_identity_tag\(t\)\)"),
}
out["sem_guard"] = [n for n, (f, pat) in guardas.items() if not re.search(pat, open(f, encoding="utf-8").read())]
import subprocess
chamadores = subprocess.run(["grep", "-rln", "writeIdentityEvidence(", "packages/mcp-server-plughub/src"], capture_output=True, text=True).stdout.split()
out["escritores"] = sorted(c for c in chamadores if "__tests__" not in c)
print(json.dumps(out))
EOF
)
echo "   $A"
[ "$(jexpr "$A" "d['reserva_ts'] == d['reserva_py'] == ['core.identity.', 'core.journey.identity.']")" = "True" ] \
  && ok "reserva idêntica em TS e Python" || falha "reserva diverge entre TS e Python"
[ "$(jexpr "$A" "d['dyn_ts_default'] and d['dyn_ts_map'] and d['dyn_pymap'] and 'core.journey.identity.' in d['dyn_py']")" = "True" ] \
  && ok "prefixo dinâmico nas quatro cópias do mapa (TS default, TS mapa, py constante, py mapa)" || falha "prefixo dinâmico ausente em alguma cópia"
VIVO=$(curl -s "$CFG/config/masking/context_map?tenant_id=$TENANT" | python3 -c "import sys,json; print('core.journey.identity.' in (json.load(sys.stdin).get('value') or {}).get('dynamic_prefixes', []))" 2>/dev/null)
[ "$VIVO" = "True" ] && ok "o mapa VIVO do config-api tem o prefixo" || falha "mapa vivo sem 'core.journey.identity.' (seed-if-absent: atualizar pela API)"
[ "$(jexpr "$A" "d['sem_guard'] == []")" = "True" ] && ok "os quatro funis genéricos carregam o guard" || falha "funis sem guard: $(jexpr "$A" "d['sem_guard']")"
# PID-09 (2026-09-14): server.ts entrou — a rota interna `/internal/identity-evidence`, pela
# qual o channel-gateway registra a CHEGADA pelo WhatsApp. Continua um escritor só; o que
# cresceu foi o conjunto de quem verifica (OTP no workflow.ts, chegada no server.ts).
[ "$(jexpr "$A" "d['escritores'] == ['packages/mcp-server-plughub/src/server.ts', 'packages/mcp-server-plughub/src/tools/journey.ts', 'packages/mcp-server-plughub/src/tools/workflow.ts']")" = "True" ] \
  && ok "writeIdentityEvidence: definido em journey.ts, chamado só em workflow.ts (OTP) e server.ts (chegada, PID-09)" || falha "escritores de evidência inesperados: $(jexpr "$A" "d['escritores']")"

echo ""
echo "── B · FUNIL TS VIVO ──────────────────────────────────────────────────"
J=$(docker exec -i "$MCP" sh -c "cd /app/packages/mcp-server-plughub && node - $TENANT" < infra/test/_identity_evidence_exercise.cjs 2>/dev/null | tail -1)
echo "   $J" | cut -c1-300
if [ "$(jexpr "$J" "'casos' in d")" != "True" ]; then incon "exercício TS sem JSON"; else
  julga "$J" "context_set_recusa context_set_core_identity context_set_controle inject_context_recusa inject_context_controle otp_sem_token_recusa"
fi

echo ""
echo "── C · FUNIL PYTHON NA IMAGEM ─────────────────────────────────────────"
J=$(pyroda funil)
echo "   $J"
if [ "$(jexpr "$J" "'casos' in d")" != "True" ]; then incon "exercício Python sem JSON"; else
  julga "$J" "raise_recusa warn_descarta controle_core"
  M=$(pyroda funil --mutar-reserva)
  [ "$(jexpr "$M" "d['casos']['warn_descarta'] is False and d['casos']['raise_recusa'] is False")" = "True" ] \
    && ok "--mutar-reserva derruba raise_recusa e warn_descarta" || falha "--mutar-reserva NÃO derrubou: $M"
  [ "$(jexpr "$M" "d['casos']['controle_core']")" = "True" ] && ok "--mutar-reserva mantém o controle" || falha "--mutar-reserva derrubou o controle"
fi

echo ""
echo "── D · PRODUTOR PONTA A PONTA (OTP na jornada) ────────────────────────"
if [ "${PULA_JORNADA:-}" = "1" ]; then
  incon "PULA_JORNADA=1 — produtor não medido"
else
  DESDE=$(date -u +%Y-%m-%dT%H:%M:%S)
  bash infra/test/probe_journey_merge_status_access.sh > /tmp/pid02_jornada.log 2>&1
  EJ=$?
  [ "$EJ" = "0" ] && ok "jornada verde (OTP dentro do chat)" || falha "jornada EXIT=$EJ (ver /tmp/pid02_jornada.log)"
  J=$(pyroda evidencia --desde "$DESDE")
  echo "   $J" | cut -c1-400
  julga "$J" "evidencia_verified prova_completa"
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then
  echo " FALHA ($FALHA)"; exit 1
elif [ "$INCONCL" -gt 0 ]; then
  echo " INCONCLUSIVO ($INCONCL)"; exit 2
fi
echo " OK"
exit 0
