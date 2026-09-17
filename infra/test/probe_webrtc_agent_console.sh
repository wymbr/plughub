#!/usr/bin/env bash
# probe_webrtc_agent_console.sh — 2026-09-15  (VOZ-04, fatia 2 · ADR adr-voice-media-plane V-F1)
#
# PERGUNTA: quando um contato WebRTC é atribuído a um agente HUMANO, o Console consegue abrir a
# sala — o agente sabe que o contato é WebRTC, a sala existe, e o token chega pelo caminho do
# browser?
#
# O DEFEITO QUE O ORIGINOU (vermelho ao vivo, antes do conserto, com agente humano headless)
#   · `conversation.assigned` chegava ao agente SEM `channel`, e o Console cria o contato como
#     `webchat` por default — a sobreposição WebRTC nunca montava;
#   · o stream da sessão só tinha `participant_joined`: o caminho REAL de ativação humana (pela
#     identidade da instância) não escrevia `routing.assigned` — só o ramo legado escrevia —,
#     então a sala nunca era criada e o token respondia sempre 404 "room not ready";
#   · o hook pedia `/api/webrtc/token/…`, que o proxy manda ao mcp-server: 404 em HTML;
#   · e mesmo com tudo certo o erro não aparecia: a sobreposição se escondia enquanto os tetos
#     estavam vazios, que é exatamente o estado de "conectando" e de "falhou".
#
# DOIS RAMOS
#   A  CONTRATO — o bridge anuncia o humano ao plano de mídia DENTRO de `activate_human_agent`
#      (e os três chamadores passam `http`); o `conversation.assigned` leva `channel` e o
#      Console o usa; o hook pede a rota do gateway pelo proxy que existe (vite e nginx) e só
#      repete no código que o gateway emite; a sobreposição mostra conectando/erro antes do teto.
#   B  AO VIVO — cliente (widget) + agente humano (protocolo do Console no `/agent/ws`) + token
#      pelo nginx do platform-ui + os dois na MESMA sala do SFU (G1..G5, com o controle G4) +
#      texto nos dois sentidos (G6 com o controle da nota `agents_only`, G7) + G8, a VOZ-15:
#      outro usuario com o MESMO grant no pool, que nao atende, leva 403. Era uma linha INFO
#      (200 medido em 2026-09-15) enquanto a ficha esteve aberta; fechou, virou veredicto.
#
# FATIA 3 (2026-09-15) — a ficha dizia que o texto do agente não chegava ao cliente, lendo o
#   `_stream_watcher`. Medido ao vivo: chegava (ele vai por `conversations.outbound`, não pelo
#   stream), nos dois sentidos, em milissegundos. O que estava errado era a HORA — o leitor pedia
#   `ts`, que nenhum produtor escreve, e o cliente recebia a hora da entrega (A6, G6) — e o widget
#   rotulava o aviso de fila do sistema como fala do "Agente" (A7).
#
# O agente headless apresenta a MESMA credencial do Console no subprotocolo do `/agent/ws`
#    (`plughub.bearer`, JWT com `agent_assist.atender` no pool) — exigida desde a CAP-19.
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
REG="${REGISTRY:-http://localhost:3300}"
AUTH="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
POOL="probe_voz04_webrtc"
AGENT_PUBLISH='["audio","video"]'
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
tally() {
  while IFS= read -r l; do
    case "$l" in OK\ *) ok "${l#OK }";; FALHA\ *) falha "${l#FALHA }";; INCONCL\ *) incon "${l#INCONCL }";;
      SID\ *) ;; INFO\ *) echo "  INFO    ${l#INFO }";; *) [ -n "$l" ] && incon "saida inesperada: $l";; esac
  done <<< "$1"
}

echo "════════════════════════════════════════════════════════════════════"
echo " o Console abre a sala de um contato WebRTC atribuido a um humano?"
echo "════════════════════════════════════════════════════════════════════"

# ── A ────────────────────────────────────────────────────────────────────────
echo ""
echo "── A · CONTRATO ───────────────────────────────────────────────────────"
A_OUT=$(python3 - <<'PYEOF'
import ast, io, re
def r(ok, txt): print(("OK " if ok else "FALHA ") + txt)
def src(p): return io.open(p, encoding="utf-8").read()
def nocomment_ts(s): return re.sub(r"(//[^\n]*|/\*.*?\*/)", "", s, flags=re.S)

BR = src("packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py")
bt = ast.parse(BR)
fn = next(n for n in bt.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "activate_human_agent")
def calls_in(node, name):
    return [c for c in ast.walk(node) if isinstance(c, ast.Call) and getattr(c.func, "id", "") == name]
w = calls_in(fn, "_write_routing_assigned_to_stream")
fw = [next((k.value.value for k in c.keywords if k.arg == "framework" and isinstance(k.value, ast.Constant)), None) for c in w]
r(len(w) == 1 and fw == ["human"], "A1 activate_human_agent anuncia o humano ao plano de midia (%d escrita(s), framework=%s)" % (len(w), fw))
fora = [c.lineno for c in calls_in(bt, "_write_routing_assigned_to_stream")
        if any(isinstance(k.value, ast.Constant) and k.value.value == "human" for k in c.keywords if k.arg == "framework")
        and not (fn.lineno <= c.lineno <= fn.end_lineno)]
r(not fora, "A1 nenhum routing.assigned humano escrito FORA dela (linhas %s)" % fora)
chamadas = calls_in(bt, "activate_human_agent")
sem_http = [c.lineno for c in chamadas if not any(k.arg == "http" for k in c.keywords)]
r(len(chamadas) >= 3 and not sem_http, "A1 %d chamador(es) passam http (sem: %s)" % (len(chamadas), sem_http))
canal = any(isinstance(n, ast.Assign) and any(isinstance(t, ast.Subscript) and getattr(t.slice, "value", None) == "channel" for t in n.targets)
            for n in ast.walk(fn))
r(canal, "A2 conversation.assigned recebe `channel` no bridge")

ctx = nocomment_ts(src("packages/platform-ui/src/modules/agent-assist/AgentAssistContext.tsx"))
types = nocomment_ts(src("packages/platform-ui/src/modules/agent-assist/types.ts"))
m = re.search(r"interface WsConversationAssigned \{(.*?)\}", types, re.S)
r(m is not None and re.search(r"\bchannel\?:\s*string", m.group(1)), "A2 tipo WsConversationAssigned declara channel")
r(re.search(r"makeContact\(session_id,\s*resolvedPool,\s*channel", ctx) is not None, "A2 Console cria o contato com o canal da atribuicao")

hook = nocomment_ts(src("packages/platform-ui/src/modules/agent-assist/hooks/useWebRTCSession.ts"))
r("/webrtc/token/" in hook and "/api/webrtc" not in hook, "A3 hook pede /webrtc/token/ (nao /api/webrtc)")
vite = src("packages/platform-ui/vite.config.ts")   # sem filtro de comentario: ele comeria o `//` da URL do target
vm = re.search(r"'\^/webrtc/token/':\s*\{\s*target:\s*'http://localhost:8010'", vite)
nginx = src("packages/platform-ui/Dockerfile")
nm = re.search(r"location ~ \^/webrtc/token/ \{.*?proxy_pass\s+\$(\w+)", nginx, re.S)
up = re.search(r"set \$%s http://channel-gateway:8010" % nm.group(1), nginx) if nm else None
r(vm is not None and up is not None, "A3 proxy /webrtc/token/ -> gateway no vite e no nginx")
gw = src("packages/channel-gateway/src/plughub_channel_gateway/main.py")
r('"room_not_ready"' in gw and re.search(r'code === "room_not_ready"', hook) is not None,
  "A4 hook repete so no codigo que o gateway emite (room_not_ready)")
ov = nocomment_ts(src("packages/platform-ui/src/modules/agent-assist/components/WebRTCOverlay.tsx"))
i_con, i_err, i_none = ov.find("if (connecting)"), ov.find("if (error)"), ov.find('view === "none"')
r(0 <= i_con < i_none and 0 <= i_err < i_none, "A5 sobreposicao mostra conectando/erro antes de esconder por teto vazio")

# A6 — a hora da mensagem: a chave que o LEITOR pede tem de ser a que os produtores escrevem.
def outbound_text_blocks(s):
    return [b for b in re.findall(r'publish\("conversations\.outbound",\s*\{(.*?)\n\s*\}\)', s, re.S)
            if 'type:       "message.text"' in b or re.search(r'type:\s*"message\.text"', b)]
prod = outbound_text_blocks(src("packages/mcp-server-plughub/src/server.ts")) + \
       outbound_text_blocks(src("packages/mcp-server-plughub/src/tools/bpm.ts"))
chaves = sorted({k for b in prod for k in re.findall(r"^\s*(timestamp|ts)\s*[:,]", b, re.M)})
wr = ast.parse(src("packages/channel-gateway/src/plughub_channel_gateway/adapters/webrtc.py"))
dt = next(n for n in ast.walk(wr) if isinstance(n, ast.AsyncFunctionDef) and n.name == "deliver_text")
lidas = [c.args[0].value for c in ast.walk(dt) if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "get"
         and isinstance(getattr(c.func, "value", None), ast.Name) and c.func.value.id == "payload"
         and c.args and isinstance(c.args[0], ast.Constant)]
r(len(prod) >= 2 and chaves == ["timestamp"] and "timestamp" in lidas and ("ts" not in lidas or lidas.index("timestamp") < lidas.index("ts")),
  "A6 deliver_text le `timestamp`, a chave dos %d produtor(es) de message.text (produtores escrevem %s; leitor pede %s)"
  % (len(prod), chaves, lidas))
wid = src("infra/demo/web/webrtc-widget.html")
r(re.search(r"msg\.author === 'system'", wid) is not None, "A7 widget nao rotula aviso do sistema como fala de agente")

# A8 — o Console TOCA o áudio remoto (1ª chamada de gente, 2026-09-15: vídeo nos dois sentidos,
# som de verdade nas duas trilhas do SFU, e o agente sem ouvir nada — nada anexava trilha de
# áudio). Censo em quem CONSOME: a sobreposição do agente e a do supervisor renderizam um
# componente que anexa trilhas de `Track.Kind.Audio`; e a recusa de autoplay vira aviso nos
# dois lados (Console e widget), em vez de silêncio.
import glob, os
comp_dir = "packages/platform-ui/src/modules/agent-assist/components"
tocadores = []
for f in glob.glob(comp_dir + "/*.tsx"):
    s = nocomment_ts(src(f))
    if "Track.Kind.Audio" in s and re.search(r"\.attach\(", s):
        tocadores.append(os.path.basename(f)[:-4])
usam = {}
for view in ("WebRTCOverlay", "WebRTCSupervisorView"):
    s = nocomment_ts(src(f"{comp_dir}/{view}.tsx"))
    usam[view] = [c for c in tocadores if re.search(r"<%s\b" % c, s)]
r(bool(tocadores) and all(usam.values()), "A8 agente e supervisor renderizam quem toca o audio remoto (%s)" % usam)
r("AudioPlaybackStatusChanged" in hook and "startAudio" in hook and "AudioPlaybackStatusChanged" in wid,
  "A8 recusa de autoplay vira aviso (hook do Console e widget)")
PYEOF
)
tally "$A_OUT"

# ── B ────────────────────────────────────────────────────────────────────────
echo ""
echo "── B · AO VIVO (cliente + agente humano + nginx do platform-ui + SFU) ──"
IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
if [ -z "$IMG" ] || [ -z "$TOKEN" ]; then
  incon "B: gateway fora do ar ou login do admin falhou — nao medido"
else
  H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')
  st=$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$REG/v1/pools/$POOL")
  if [ "$st" = 404 ]; then
    curl -s -o /dev/null -X POST "${H[@]}" "$REG/v1/pools" -d "{\"pool_id\":\"$POOL\",\"channel_types\":[\"webrtc\"],\"sla_target_ms\":60000,\"agent_kind\":\"human\",\"description\":\"fixture VOZ-04\",\"media_policy\":{\"customer_publish\":[\"audio\",\"video\"],\"agent_publish\":$AGENT_PUBLISH}}"
    sleep 5
  fi
  # a política da fixture é afirmada, não suposta
  curl -s -o /dev/null -X PUT "${H[@]}" "$REG/v1/pools/$POOL" -d "{\"media_policy\":{\"customer_publish\":[\"audio\",\"video\"],\"agent_publish\":$AGENT_PUBLISH}}"
  ENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | grep -E '^PLUGHUB_(JWT_SECRET|AUTH_JWT_SECRET|TENANT_ID|WEBRTC_LIVEKIT_URL|WEBRTC_LIVEKIT_API_KEY|WEBRTC_LIVEKIT_API_SECRET)=' | sed 's/^/-e /' | tr '\n' ' ')
  name="probe_voz04c_$$_$RANDOM"
  B_OUT=$(timeout "${EXERCISE_TIMEOUT_S:-240}" docker run --rm -i --name "$name" --network "$NET" --entrypoint python \
          $ENV -e POOL="$POOL" -e AGENT_PUBLISH="$AGENT_PUBLISH" "$IMG" - < infra/test/_webrtc_agent_console_exercise.py 2>&1)
  [ $? -eq 124 ] && { docker kill "$name" >/dev/null 2>&1; B_OUT="$B_OUT
FALHA TIMEOUT exercicio morto apos ${EXERCISE_TIMEOUT_S:-240}s"; }
  tally "$(printf '%s\n' "$B_OUT" | grep -E '^(OK|FALHA|INCONCL|SID|INFO) ')"
  N=$(printf '%s\n' "$B_OUT" | grep -cE '^(OK|FALHA) (G[1-7]|LIMPEZA) ')
  [ "$N" -ge 8 ] || falha "exercicio emitiu $N de 8 veredictos: $(printf '%s' "$B_OUT" | tail -3 | tr '\n' ' ' | cut -c1-260)"
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO — $INCONCL ramo(s) sem medida"; exit 2; fi
echo " VERDE"; exit 0
