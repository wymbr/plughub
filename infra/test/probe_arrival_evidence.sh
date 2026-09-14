#!/usr/bin/env bash
# probe_arrival_evidence.sh — 2026-09-14  (PID-09)
#
# PERGUNTA: a chegada pelo WhatsApp do telefone AUTORITATIVO de um cliente prova a posse
#           que o OTP provaria — e a prova, de qualquer mecanismo, só vale para o cliente
#           de quem ela é?
#
# O QUE FOI MEDIDO ANTES
#   A evidência (`core.journey.identity.<mecanismo>.*`) dizia que ALGUÉM provou algo nesta
#   sessão, e não de quem. Vermelho ao vivo, na imagem anterior, pelo transporte MCP: a
#   sessão que provou por OTP o telefone do cliente A pediu a pendência do cliente B (com
#   posse durável) e LEVOU o token; e o `workflow_resume` com esse token passou o atestado.
#   O WhatsApp não gravava evidência nenhuma, e sem `whatsapp_app_secret` o adapter aceitava
#   webhook sem conferir a assinatura.
#
# TRÊS RAMOS
#   A  CENSO — todo `judgeResumeEvidence` fora de `@plughub/schemas` passa o `customerId`;
#      o `whatsapp` SATISFAZ `otp` e não é exigível por nome; a rota do gateway só autentica
#      com a assinatura CONFERIDA. Mutação do censo.
#   M  FALSEABILIDADE — as suítes rodam sobre CÓPIAS com o produto quebrado (schemas, mcp e
#      gateway) e cada mutação tem de reprovar POR ASSERÇÃO.
#   B  AO VIVO — `_arrival_evidence_exercise.cjs`: OTP cruzado retido (red-first); webhook
#      assinado do telefone autoritativo grava `verified` e libera a pendência sem OTP; a
#      mesma sessão não leva nem retoma o token de outro cliente; número sem cadastro grava
#      `failed`; webhook sem assinatura é 400. Fixtures limpas por hash e por id.
#
# ⚠️ CUSTO DECLARADO: cada rodada abre DUAS sessões WhatsApp sintéticas, e o roteamento as
# entrega a instâncias `sac_ia` (o WhatsApp ainda não tem endpoint → pool; ver PID-22). O
# watchdog as fecha como órfãs — o mesmo tipo de custo dos probes de porta que abrem
# contato real (`probe_portabilidade_door.sh`).
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO
set -uo pipefail
cd "$(dirname "$0")/../.."

TENANT="${TENANT:-tenant_demo}"
MCP="${MCP_CONTAINER:-plughub-demo-mcp-server-plughub-1}"
GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
GW_IMG="${GW_IMAGE:-plughub-demo-channel-gateway}"
AUTH="${AUTH_URL:-http://localhost:3202}"
AD_EMAIL="${AD_EMAIL:-admin@plughub.local}"; AD_PASS="${AD_PASS:-changeme_admin}"
NODE_BIN="${NODE_BIN:-/home/a1/.nvm/versions/node/v24.14.1/bin}"
export PATH="$NODE_BIN:$PATH"
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

echo "════════════════════════════════════════════════════════════════════"
echo " a chegada pelo WhatsApp prova a posse — e a prova é DE QUEM a fez?"
echo "════════════════════════════════════════════════════════════════════"

# ── A · CENSO ──────────────────────────────────────────────────────────────
echo ""
echo "── A · CENSO ──────────────────────────────────────────────────────────"
censo() {  # $1 raiz de packages → JSON
  python3 - "$1" <<'PY'
import json, os, re, sys
raiz = sys.argv[1]
sem_cliente, chamadas = [], 0
for d, subdirs, arquivos in os.walk(raiz):
    subdirs[:] = sorted(s for s in subdirs if s not in ("node_modules", "dist", "__tests__", "schemas") and not s.startswith("."))
    for a in sorted(arquivos):
        if not a.endswith(".ts") or a.endswith(".test.ts"):
            continue
        p = os.path.join(d, a)
        s = open(p, encoding="utf-8").read()
        for m in re.finditer(r"\bjudgeResumeEvidence\(", s):
            ini = m.end(); prof = 1; i = ini
            while i < len(s) and prof:
                prof += {"(": 1, ")": -1}.get(s[i], 0); i += 1
            chamadas += 1
            if "customerId" not in s[ini:i]:
                sem_cliente.append("%s:%d" % (os.path.relpath(p, raiz).replace(os.sep, "/"), s[:m.start()].count("\n") + 1))
ie = open(os.path.join(raiz, "schemas/src/identity-evidence.ts"), encoding="utf-8").read()
exig = re.search(r"REQUIRABLE_MECHANISMS\s*=\s*\[([^\]]*)\]", ie)
sat = re.search(r"otp:\s*\[([^\]]*)\]", ie)
main = open(os.path.join(raiz, "channel-gateway/src/plughub_channel_gateway/main.py"), encoding="utf-8").read()
wa = open(os.path.join(raiz, "channel-gateway/src/plughub_channel_gateway/adapters/whatsapp.py"), encoding="utf-8").read()
print(json.dumps({
    "chamadas": chamadas, "sem_cliente": sem_cliente,
    "exigiveis": re.findall(r'"([a-z_]+)"', exig.group(1)) if exig else None,
    "satisfazem_otp": re.findall(r'"([a-z_]+)"', sat.group(1)) if sat else None,
    "rota_autentica_so_conferida": 'authenticated=(veredito == "authenticated")' in main,
    "default_nao_autentica": "authenticated: bool = False) -> None:" in wa,
}))
PY
}
C=$(censo packages)
echo "   $C"
[ "$(jexpr "$C" "d['chamadas'] >= 3 and d['sem_cliente'] == []")" = "True" ] \
  && ok "todo judgeResumeEvidence fora de schemas passa customerId ($(jexpr "$C" "d['chamadas']") chamadas)" \
  || falha "chamada sem customerId (a prova não se amarra ao cliente): $(jexpr "$C" "d['sem_cliente']")"
[ "$(jexpr "$C" "d['exigiveis'] == ['otp'] and d['satisfazem_otp'] == ['otp', 'whatsapp']")" = "True" ] \
  && ok "exigível só 'otp'; 'otp' é satisfeito por otp e whatsapp" \
  || falha "equivalência errada: exigíveis=$(jexpr "$C" "d['exigiveis']") satisfazem=$(jexpr "$C" "d['satisfazem_otp']")"
[ "$(jexpr "$C" "d['rota_autentica_so_conferida'] and d['default_nao_autentica']")" = "True" ] \
  && ok "gateway: só a assinatura CONFERIDA autentica a chegada, e quem não diz não autentica" \
  || falha "gateway autentica sem conferir: $C"
TMPA=$(mktemp -d)
mkdir -p "$TMPA/mcp-server-plughub/src/tools" "$TMPA/schemas/src" "$TMPA/channel-gateway/src/plughub_channel_gateway/adapters"
cp packages/mcp-server-plughub/src/tools/workflow.ts "$TMPA/mcp-server-plughub/src/tools/"
cp packages/schemas/src/identity-evidence.ts "$TMPA/schemas/src/"
cp packages/channel-gateway/src/plughub_channel_gateway/main.py "$TMPA/channel-gateway/src/plughub_channel_gateway/"
cp packages/channel-gateway/src/plughub_channel_gateway/adapters/whatsapp.py "$TMPA/channel-gateway/src/plughub_channel_gateway/adapters/"
python3 - "$TMPA/mcp-server-plughub/src/tools/workflow.ts" <<'PY'
import sys
p = sys.argv[1]; s = open(p, encoding="utf-8").read()
a = "{ sessionId, nowMs, customerId })"
assert a in s
open(p, "w", encoding="utf-8").write(s.replace(a, "{ sessionId, nowMs } as any)", 1))
PY
CM=$(censo "$TMPA"); rm -rf "$TMPA"
[ "$(jexpr "$CM" "len(d['sem_cliente']) == 1")" = "True" ] \
  && ok "mutação 'um julgamento sem cliente' → censo acusa" || falha "mutação do censo não acusou: $CM"

# ── M · FALSEABILIDADE ─────────────────────────────────────────────────────
echo ""
echo "── M · as suítes reprovam quando o produto quebra ──────────────────────"
LOG=/tmp/_probe_pid09_suite.log
SCH=packages/schemas/.probe_pid09
MC=packages/mcp-server-plughub/.probe_pid09
trap 'rm -rf "$SCH" "$MC"' EXIT

julga_log() {  # $1 rótulo · $2 exit da suíte
  if [ "$2" = "0" ]; then falha "mutação '$1' passou na suíte — os testes não a julgam"
  elif grep -qE "AssertionError|expected .* to |assert .*(==|is|in) |AssertionError" "$LOG" \
       && ! grep -qE "Transform failed|SyntaxError|Failed to load|Cannot find module|ImportError|IndentationError" "$LOG"; then
    ok "mutação '$1' → vermelha por asserção"
  else
    incon "mutação '$1' vermelha sem asserção (erro de carga?): $(grep -m2 -E 'Error' "$LOG")"
  fi
}
aplica() {  # $1 arquivo · $2 âncora · $3 substituto
  python3 - "$1" "$2" "$3" <<'PY'
import sys
p, a, b = sys.argv[1:4]
s = open(p, encoding="utf-8").read()
if s.count(a) != 1: raise SystemExit(1)
open(p, "w", encoding="utf-8", newline="").write(s.replace(a, b))
PY
}
mut_ts() {  # $1 rótulo · $2 pacote · $3 cópia · $4 arquivo rel. ao src · $5 âncora · $6 substituto · $7.. testes
  local rot=$1 pkg=$2 cp_=$3 arq=$4 a=$5 b=$6; shift 6
  rm -rf "$cp_"; mkdir -p "$cp_"; cp -r "$pkg/src" "$cp_/src"
  if ! aplica "$cp_/src/$arq" "$a" "$b"; then incon "mutação '$rot' não se aplicou (âncora sumiu)"; return; fi
  (cd "$cp_" && npx vitest run --root . "$@" >"$LOG" 2>&1); julga_log "$rot" $?
}
controle_ts() {  # $1 pacote · $2 cópia · $3.. testes → 0 se passa
  local pkg=$1 cp_=$2; shift 2
  rm -rf "$cp_"; mkdir -p "$cp_"; cp -r "$pkg/src" "$cp_/src"
  (cd "$cp_" && npx vitest run --root . "$@" >"$LOG" 2>&1)
}

if ! command -v npx >/dev/null 2>&1 || [ ! -d packages/schemas/node_modules ] || [ ! -d packages/mcp-server-plughub/node_modules ]; then
  incon "npx ou node_modules ausente — falseabilidade TS não medida"
else
  T_SCH=(src/resume-requirement.test.ts)
  if controle_ts packages/schemas "$SCH" "${T_SCH[@]}"; then
    ok "controle: suíte de schemas passa sobre a cópia"
    mut_ts "prova de outro cliente satisfaz" packages/schemas "$SCH" resume-requirement.ts \
      '  if (cliente !== opts.customerId) return "other_customer"' '  if (false) return "other_customer"' "${T_SCH[@]}"
    mut_ts "sem cliente satisfaz" packages/schemas "$SCH" resume-requirement.ts \
      '  if (!cliente || !opts.customerId) return "no_customer"' '  if (false) return "no_customer"' "${T_SCH[@]}"
    mut_ts "WhatsApp não satisfaz OTP" packages/schemas "$SCH" identity-evidence.ts \
      '  otp: ["otp", "whatsapp"],' '  otp: ["otp"],' "${T_SCH[@]}"
  else
    incon "a cópia de schemas sem mutação não passa: $(grep -m3 -E 'FAIL|Error' "$LOG")"
  fi
  T_MCP=(src/__tests__/arrival-evidence.test.ts src/__tests__/resume-identity-clearance.test.ts src/__tests__/resume-requirement-release.test.ts src/__tests__/identity-evidence.test.ts)
  if controle_ts packages/mcp-server-plughub "$MC" "${T_MCP[@]}"; then
    ok "controle: suítes do mcp-server passam sobre a cópia"
    mut_ts "chegada aceita fonte não autoritativa" packages/mcp-server-plughub "$MC" lib/arrival-evidence.ts \
      '    if (source !== "authoritative") {' '    if (false) {' "${T_MCP[@]}"
    mut_ts "chegada registra otp" packages/mcp-server-plughub "$MC" lib/arrival-evidence.ts \
      'export const ARRIVAL_MECHANISMS: readonly IdentityMechanism[] = ["whatsapp"]' 'export const ARRIVAL_MECHANISMS: readonly IdentityMechanism[] = ["whatsapp", "otp"]' "${T_MCP[@]}"
    mut_ts "resume aceita prova de qualquer cliente da journey" packages/mcp-server-plughub "$MC" tools/workflow.ts \
      '  const customerId = await tokenCustomer(redis, tenantId, evidenceCustomers(hash), resumeToken)' \
      '  const customerId = evidenceCustomers(hash)[0]' "${T_MCP[@]}"
    mut_ts "liberação sem cliente das pendências" packages/mcp-server-plughub "$MC" tools/workflow.ts \
      '? data["customer_id"] as string : undefined' '? data["customer_id"] as string : evidenceCustomers(hash)[0]' "${T_MCP[@]}"
    mut_ts "otp_verify não diz de quem é a posse" packages/mcp-server-plughub "$MC" tools/workflow.ts \
      '            customer_id:       p.data.customer_id,   // PID-09 — de quem é a posse provada' '' "${T_MCP[@]}"
  else
    incon "a cópia do mcp-server sem mutação não passa: $(grep -m3 -E 'FAIL|Error' "$LOG")"
  fi
fi

mut_py() {  # $1 rótulo · $2 arquivo rel. a plughub_channel_gateway · $3 âncora · $4 substituto
  local base64a base64b
  base64a=$(printf '%s' "$3" | base64 -w0); base64b=$(printf '%s' "$4" | base64 -w0)
  docker run --rm --entrypoint sh -e A="$base64a" -e B="$base64b" -e ARQ="$2" "$GW_IMG" -c '
    cd /app/packages/channel-gateway && python - <<PY
import base64, os, sys
p = "src/plughub_channel_gateway/" + os.environ["ARQ"]
a, b = base64.b64decode(os.environ["A"]).decode(), base64.b64decode(os.environ["B"]).decode()
s = open(p, encoding="utf-8").read()
if s.count(a) != 1: sys.exit(3)
open(p, "w", encoding="utf-8").write(s.replace(a, b))
PY
    [ $? = 3 ] && { echo __ANCORA__; exit 9; }
    python -m pytest -q -p no:cacheprovider src/plughub_channel_gateway/tests/test_arrival_evidence.py 2>&1' >"$LOG" 2>&1
  local e=$?
  if grep -q __ANCORA__ "$LOG"; then incon "mutação '$1' não se aplicou (âncora sumiu)"; return; fi
  if [ "$e" = "0" ]; then falha "mutação '$1' passou no pytest — os testes não a julgam"
  elif grep -qE "AssertionError|assert " "$LOG" && ! grep -qE "ImportError|SyntaxError|IndentationError|errors? during collection" "$LOG"; then
    ok "mutação '$1' → pytest vermelho por asserção"
  else
    incon "mutação '$1' vermelha sem asserção: $(grep -m2 -E 'Error' "$LOG")"
  fi
}
if ! docker image inspect "$GW_IMG" >/dev/null 2>&1; then
  incon "imagem $GW_IMG ausente — falseabilidade do gateway não medida"
else
  docker run --rm --entrypoint sh "$GW_IMG" -c 'cd /app/packages/channel-gateway && python -m pytest -q -p no:cacheprovider src/plughub_channel_gateway/tests/test_arrival_evidence.py' >"$LOG" 2>&1 \
    && ok "controle: test_arrival_evidence passa na imagem" \
    || incon "test_arrival_evidence não passa na imagem sem mutação (imagem velha?): $(tail -1 "$LOG")"
  mut_py "assinatura não conferida prova posse" arrival_evidence.py "        if not authenticated:" "        if False:"
  mut_py "número sem dono autoritativo vira verified" arrival_evidence.py "            if dono:
                # a procedência RELATADA é a que o cadastro devolveu — este módulo não a afirma
                cliente, procedencia = dono" "            if True:
                cliente, procedencia = dono or ('cus_inventado', 'authoritative')"
  mut_py "erro do cadastro apaga a prova" arrival_evidence.py '                return {"skipped": "lookup_failed"}' '                dono = None'
  mut_py "procedência qualquer é dona" identity/index.py '        if not row or row.get("provenance") != PROVENANCE_AUTHORITATIVE:' '        if not row:'
  mut_py "sem segredo a assinatura autentica" adapters/whatsapp.py '            return "unchecked"' '            return "authenticated"'
fi

# ── B · AO VIVO ────────────────────────────────────────────────────────────
echo ""
echo "── B · AO VIVO ────────────────────────────────────────────────────────"
T_MCP_TOKEN=$(docker exec "$MCP" printenv MCP_INTERNAL_SERVICE_TOKEN 2>/dev/null)
T_GW_TOKEN=$(docker exec "$GW" printenv PLUGHUB_MCP_INTERNAL_SERVICE_TOKEN 2>/dev/null)
WA=$(docker exec "$GW" printenv PLUGHUB_WHATSAPP_APP_SECRET 2>/dev/null)
if [ -n "$T_MCP_TOKEN" ] && [ "$T_MCP_TOKEN" = "$T_GW_TOKEN" ] && [ -n "$WA" ] && [ -n "$(docker exec "$GW" printenv PLUGHUB_MCP_SERVER_URL 2>/dev/null)" ]; then
  ok "envs: gateway e mcp-server com o mesmo token interno, URL do mcp e segredo do app WhatsApp"
else
  falha "envs incompletas: token mcp=$([ -n "$T_MCP_TOKEN" ] && echo set || echo VAZIO) gateway=$([ "$T_MCP_TOKEN" = "$T_GW_TOKEN" ] && echo igual || echo DIFERENTE) segredo_wa=$([ -n "$WA" ] && echo set || echo VAZIO)"
fi
T_ADM=$(curl -s -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$AD_EMAIL\",\"password\":\"$AD_PASS\",\"tenant_id\":\"$TENANT\"}" \
  | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("access_token",""))
except Exception: print("")')
if [ -z "$T_ADM" ]; then
  incon "login do admin falhou — importação de fixtures impossível, B não medido"
else
  J=$(docker exec -i -e T_ADM="$T_ADM" -e WA_SECRET="$WA" "$MCP" sh -c "cd /app/packages/mcp-server-plughub && node - $TENANT" \
      < infra/test/_arrival_evidence_exercise.cjs 2>/dev/null | tail -1)
  L=$(jexpr "$J" "json.dumps(d.get('limpeza', {}))")
  if [ -n "$L" ] && [ "${L#__ERRO__}" = "$L" ]; then
    LJ=$(docker exec -i "$GW" python - "$L" < infra/test/_arrival_evidence_cleanup.py 2>/dev/null | tail -1)
    echo "   limpeza: $LJ"
  fi
  if [ "$(jexpr "$J" "'casos' in d and not d.get('erro')")" != "True" ]; then
    incon "exercício sem casos: $(printf '%s' "$J" | cut -c1-300)"
  else
    for k in r_controle_b r_otp_outro_cliente r_resume_outro_recusa w_controle_sem_chegada w_evidencia_verified \
             w_libera_sem_otp w_resume_passa w_outro_cliente_retido w_resume_outro_recusa u_failed nao_assinado_400; do
      [ "$(jexpr "$J" "d['casos'].get('$k')")" = "True" ] && ok "$k" || falha "$k — $(jexpr "$J" "json.dumps(d['detalhe'].get('$k') or d['detalhe'].get('${k%_*}'))" | cut -c1-200)"
    done
  fi
  SOBRA=$(docker exec plughub-demo-redis-1 redis-cli --scan --pattern '*probe-pid09*' 2>/dev/null | wc -l)
  [ "$SOBRA" = "0" ] && ok "nenhuma chave de fixture sobrou no Redis" || falha "$SOBRA chave(s) *probe-pid09* sobraram no Redis"
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
