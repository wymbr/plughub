#!/usr/bin/env bash
# probe_webrtc_contact_entry.sh — 2026-09-14  (VOZ-04, fatia de ENTRADA · ADR adr-voice-media-plane V-F1)
#
# PERGUNTA: um contato WebRTC ENTRA na plataforma — a plataforma o reconhece, o roteia, recebe o
# que o cliente diz e percebe quando ele sai?
#
# O DEFEITO QUE O ORIGINOU (vermelho ao vivo, antes do conserto)
#   · o widget de demo esperava um `conn.hello` que o servidor nunca manda → `auth_timeout`;
#   · com o handshake certo, o pedido de roteamento ia sem `started_at` e o routing-engine o
#     descartava ("Unrecognised inbound event") — o contato nunca era roteado;
#   · a mensagem ia com `content` sem `type` e o bridge a descartava ("Unknown content type");
#   · a abertura e o fechamento iam para `conversations.inbound`, onde ninguém os reconhece;
#   · a sessão gravava `meta` e nunca `ws_alive`: o watchdog do bridge a fecharia como ÓRFÃ.
#   Zero contato WebRTC tinha chegado a uma fila até então.
#
# TRÊS RAMOS
#   A  CONTRATO medido no LEITOR — os campos obrigatórios do `ConversationInboundEvent` do
#      routing-engine estão no pedido que o gateway monta; o adapter não publica dict solto; o
#      widget fala o que o servidor lê e lê o que o servidor manda; a chave que o watchdog do
#      bridge lê é a que o adapter grava.
#   B  AO VIVO — widget-protocolo contra gateway, routing-engine e bridge reais (E1..E6), com o
#      controle do instrumento K: o routing enfileira o formato novo e recusa o antigo.
#   C  LEITOR DA MENSAGEM — o bridge registrou a mensagem do contato como texto e não a descartou.
#      Sem agente na fila o veredicto do bridge não deixa estado, então é lido no log DELE,
#      filtrado pela sessão; ausência de linha é INCONCLUSIVO, nunca verde.
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
BRIDGE="${BRIDGE_CONTAINER:-plughub-demo-orchestrator-bridge-1}"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
REG="${REGISTRY:-http://localhost:3300}"
AUTH="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
POOL="probe_voz04_webrtc"     # pool webrtc de contato, humano, sem agente (fixture fixa)
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
tally() {
  while IFS= read -r l; do
    case "$l" in OK\ *) ok "${l#OK }";; FALHA\ *) falha "${l#FALHA }";; INCONCL\ *) incon "${l#INCONCL }";;
      SID\ *) ;; *) [ -n "$l" ] && incon "saida inesperada: $l";; esac
  done <<< "$1"
}

echo "════════════════════════════════════════════════════════════════════"
echo " um contato WebRTC entra na plataforma?"
echo "════════════════════════════════════════════════════════════════════"

# ── A ────────────────────────────────────────────────────────────────────────
echo ""
echo "── A · CONTRATO (medido no leitor) ────────────────────────────────────"
A_OUT=$(python3 - <<'PYEOF'
import ast, io, re
def r(ok, txt): print(("OK " if ok else "FALHA ") + txt)
def src(p): return io.open(p, encoding="utf-8").read()
GWA = "packages/channel-gateway/src/plughub_channel_gateway/adapters/"

# A1 — campos obrigatórios do leitor (routing-engine) ⊆ pedido montado pelo gateway
rt = ast.parse(src("packages/routing-engine/src/plughub_routing/models.py"))
cls = next(n for n in ast.walk(rt) if isinstance(n, ast.ClassDef) and n.name == "ConversationInboundEvent")
required = {s.target.id for s in cls.body if isinstance(s, ast.AnnAssign) and s.value is None}
cl = ast.parse(src(GWA + "contact_lifecycle.py"))
fn = next(n for n in cl.body if isinstance(n, ast.FunctionDef) and n.name == "routing_request")
ret = next(n for n in ast.walk(fn) if isinstance(n, ast.Return))
emitted = {k.value for k in ret.value.keys if isinstance(k, ast.Constant)}
r(bool(required) and required <= emitted,
  "A1 obrigatorios do ConversationInboundEvent %s estao no pedido do gateway (faltam %s)" % (sorted(required), sorted(required - emitted)))

# A2 — os dois adapters de WebSocket montam o pedido pela mesma casa, e o webrtc não publica dict solto
for nome in ("webrtc.py", "webchat.py"):
    t = ast.parse(src(GWA + nome))
    usa = any(isinstance(n, ast.Attribute) and n.attr == "routing_request" for n in ast.walk(t))
    r(usa, "A2 %s monta o pedido por contact_lifecycle.routing_request" % nome)
wt = ast.parse(src(GWA + "webrtc.py"))
soltos = [n.lineno for n in ast.walk(wt) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") in ("_publish_inbound", "_publish_event")
          and n.args and isinstance(n.args[0], ast.Dict)]
r(not soltos, "A2 webrtc.py nao publica dict literal em Kafka (linhas %s)" % soltos)

# A3 — a chave que o watchdog do bridge lê é a que o adapter grava
bridge = src("packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py")
m = re.search(r'async def _sweep_orphaned_sessions.*?redis_client\.exists\(f"session:\{session_id\}:(\w+)"\)\s*\n\s*if ws_alive', bridge, re.S)
chave = m.group(1) if m else None
r(chave is not None and ('f"session:{session_id}:%s"' % chave) in src(GWA + "webrtc.py"),
  "A3 watchdog do bridge le `session:{id}:%s` e o webrtc a grava" % chave)

# A4 — widget × servidor
w = src("infra/demo/web/webrtc-widget.html")
srv = src(GWA + "webrtc.py")
hs = re.search(r"msg\.type === 'conn\.ready'\) \{(.*?)return;", w, re.S)
r(hs is not None and "conn.hello" in hs.group(1) and "conn.authenticate" in hs.group(1)
  and 'msg.get("type") != "conn.hello"' in srv,
  "A4 widget responde conn.ready com hello+authenticate, que e o que o servidor espera")
envio = re.search(r"wsSend\(\{ type: 'webrtc\.message', ([^}]*)\}\)", w)
r(envio is not None and re.fullmatch(r"\s*text\s*", envio.group(1)) is not None and 'msg.get("text", "")' in srv,
  "A4 widget manda `text` plano, a chave que o servidor le (%s)" % (envio.group(1).strip() if envio else None))
fecho = re.search(r"msg\.type === 'webrtc\.session_closed'\) \{(.*?)\n    \}", w, re.S)
lidas = set(re.findall(r"\bmsg\.(\w+)", fecho.group(1))) - {"type"} if fecho else None
envia = set(re.findall(r'"type":\s*"webrtc\.session_closed",\s*"(\w+)"', srv))
r(lidas is not None and lidas <= envia, "A4 widget em session_closed le so chaves emitidas (le=%s, emite=%s)" % (sorted(lidas or []), sorted(envia)))
PYEOF
)
tally "$A_OUT"

# ── B ────────────────────────────────────────────────────────────────────────
echo ""
echo "── B · AO VIVO (gateway + routing-engine + bridge) ────────────────────"
IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
if [ -z "$IMG" ] || [ -z "$TOKEN" ]; then
  incon "B/C: gateway fora do ar ou login do admin falhou — nao medido"
else
  H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')
  st=$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$REG/v1/pools/$POOL")
  if [ "$st" = 404 ]; then
    st=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools" -d "{\"pool_id\":\"$POOL\",\"channel_types\":[\"webrtc\"],\"sla_target_ms\":60000,\"agent_kind\":\"human\",\"description\":\"fixture do probe_webrtc_contact_entry (VOZ-04)\",\"media_policy\":{\"customer_publish\":[\"audio\",\"video\"],\"agent_publish\":[\"audio\",\"video\"]}}")
    [ "$st" = 201 ] || incon "fixture $POOL nao criada (http $st)"
    sleep 5   # pool.registered chega ao routing-engine
  fi
  ENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | grep -E '^PLUGHUB_(JWT_SECRET|TENANT_ID|KAFKA_BROKERS)=' | sed 's/^/-e /' | tr '\n' ' ')
  WD=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$BRIDGE" | sed -n 's/^SESSION_WATCHDOG_INTERVAL_S=//p')
  T0=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  name="probe_voz04_$$_$RANDOM"
  B_OUT=$(timeout "${EXERCISE_TIMEOUT_S:-180}" docker run --rm -i --name "$name" --network "$NET" --entrypoint python \
          $ENV -e POOL="$POOL" -e WATCHDOG_S="${WD:-120}" "$IMG" - < infra/test/_webrtc_contact_entry_exercise.py 2>&1)
  [ $? -eq 124 ] && { docker kill "$name" >/dev/null 2>&1; B_OUT="$B_OUT
FALHA TIMEOUT exercicio morto apos ${EXERCISE_TIMEOUT_S:-180}s"; }
  tally "$(printf '%s\n' "$B_OUT" | grep -E '^(OK|FALHA|INCONCL|SID) ')"
  N=$(printf '%s\n' "$B_OUT" | grep -cE '^(OK|FALHA) (E[1-6]|K|LIMPEZA) ')
  [ "$N" -ge 7 ] || falha "exercicio emitiu $N de 7 veredictos: $(printf '%s' "$B_OUT" | tail -3 | tr '\n' ' ' | cut -c1-240)"

  # ── C ──────────────────────────────────────────────────────────────────────
  echo ""
  echo "── C · LEITOR DA MENSAGEM (bridge) ────────────────────────────────────"
  SID=$(printf '%s\n' "$B_OUT" | sed -n 's/^SID //p')
  if [ -z "$SID" ]; then
    incon "C: sem sessao do ramo B"
  else
    LOG=$(docker logs --since "$T0" "$BRIDGE" 2>&1 | grep "$SID")
    if printf '%s\n' "$LOG" | grep -q "Unknown content type"; then
      falha "C1 bridge DESCARTOU a mensagem da sessao $SID como tipo desconhecido"
    elif printf '%s\n' "$LOG" | grep -q "Inbound customer message: session=$SID content_type=text"; then
      ok "C1 bridge recebeu a mensagem da sessao como texto"
    else
      incon "C1 nenhuma linha do bridge sobre a mensagem da sessao $SID"
    fi
  fi
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO — $INCONCL ramo(s) sem medida"; exit 2; fi
echo " VERDE"; exit 0
