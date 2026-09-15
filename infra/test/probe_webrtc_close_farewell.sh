#!/usr/bin/env bash
# probe_webrtc_close_farewell.sh — 2026-09-15  (VOZ-16)
#
# PERGUNTA: quando a PLATAFORMA fecha um contato WebRTC (teto de fila, sem recurso, outage),
# o cliente recebe o aviso que ela mandou e o motivo de verdade?
#
# O DEFEITO QUE O ORIGINOU (medido ao vivo em 2026-09-15, contato real sem agente)
#   · o routing publica `session.closed` com `farewell_text` (*"Tempo máximo de espera
#     atingido…"*); o adapter de webchat o mostra, o `WebRTCAdapter` o IGNORAVA;
#   · o adapter lia `close_reason`, que NENHUM produtor de `session.closed` escreve, e caía no
#     default `session_timeout` — o widget mostrou *"Atendimento encerrado (session_timeout)."*
#     para um `max_wait_exceeded`. O teste unitário passava porque ELE escrevia `close_reason`:
#     produtor e teste olhando um para o outro, nenhum dos dois para o produtor real.
#
# DOIS RAMOS
#   A  CONTRATO medido nos PRODUTORES — todo `session.closed` que o routing-engine publica em
#      `conversations.outbound` carrega `farewell_text` e `close_reason`, e o adapter WebRTC lê
#      as duas chaves.
#   B  AO VIVO — pool humano sem agente com `queue_config.max_wait_s` curto: o cliente recebe o
#      aviso de sistema ANTES do fechamento e o motivo `max_wait_exceeded` (F1..F4), com o
#      controle F2 (foi o routing que tirou o contato da fila) e o log do routing (F5).
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
RT="${ROUTING_CONTAINER:-plughub-demo-routing-engine-1}"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
REG="${REGISTRY:-http://localhost:3300}"
AUTH="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
POOL="probe_voz16_fila"
MAX_WAIT_S=10
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
echo " o cliente WebRTC recebe o aviso e o motivo quando a plataforma fecha?"
echo "════════════════════════════════════════════════════════════════════"

# ── A ────────────────────────────────────────────────────────────────────────
echo ""
echo "── A · CONTRATO (produtores do routing × leitor WebRTC) ───────────────"
A_OUT=$(python3 - <<'PYEOF'
import ast, io
def r(ok, txt): print(("OK " if ok else "FALHA ") + txt)
src = lambda p: io.open(p, encoding="utf-8").read()

rt = ast.parse(src("packages/routing-engine/src/plughub_routing/main.py"))
fechos = []
for fn in ast.walk(rt):
    if not isinstance(fn, ast.AsyncFunctionDef):
        continue
    for d in ast.walk(fn):
        if isinstance(d, ast.Dict):
            chaves = {k.value: v for k, v in zip(d.keys, d.values) if isinstance(k, ast.Constant)}
            t = chaves.get("type")
            if isinstance(t, ast.Constant) and t.value == "session.closed":
                # farewell pode entrar por atribuição posterior (close_payload["farewell_text"] = …)
                atrib = {s.targets[0].slice.value for s in ast.walk(fn)
                         if isinstance(s, ast.Assign) and isinstance(s.targets[0], ast.Subscript)
                         and isinstance(s.targets[0].slice, ast.Constant)}
                fechos.append((fn.name, d.lineno, set(chaves) | atrib))
r(len(fechos) >= 3, "A1 censo: %d session.closed no routing-engine (%s)" % (len(fechos), [f"{n}:{l}" for n, l, _ in fechos]))
for nome, linha, ks in fechos:
    falta = {"farewell_text", "close_reason"} - ks
    r(not falta, "A1 %s:%d carrega farewell_text e close_reason (faltam %s)" % (nome, linha, sorted(falta)))

wr = ast.parse(src("packages/channel-gateway/src/plughub_channel_gateway/adapters/webrtc.py"))
fn = next(n for n in ast.walk(wr) if isinstance(n, ast.AsyncFunctionDef) and n.name == "deliver_session_closed")
lidas = {c.args[0].value for c in ast.walk(fn) if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "get"
         and c.args and isinstance(c.args[0], ast.Constant) and isinstance(c.func.value, ast.Name) and c.func.value.id == "payload"}
r({"farewell_text", "close_reason"} <= lidas, "A2 WebRTCAdapter.deliver_session_closed le farewell_text e close_reason (le %s)" % sorted(lidas))
PYEOF
)
tally "$A_OUT"

# ── B ────────────────────────────────────────────────────────────────────────
echo ""
echo "── B · AO VIVO (gateway + routing-engine) ─────────────────────────────"
IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
TOKEN=$(curl -s --max-time 20 -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@plughub.local\",\"password\":\"changeme_admin\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty')
if [ -z "$IMG" ] || [ -z "$TOKEN" ]; then
  incon "B: gateway fora do ar ou login do admin falhou — nao medido"
else
  H=(-H "Authorization: Bearer $TOKEN" -H "x-tenant-id: $TENANT" -H 'Content-Type: application/json')
  if [ "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$REG/v1/pools/$POOL")" = 404 ]; then
    st=$(curl -s -o /dev/null -w '%{http_code}' -X POST "${H[@]}" "$REG/v1/pools" -d "{\"pool_id\":\"$POOL\",\"channel_types\":[\"webrtc\"],\"sla_target_ms\":60000,\"agent_kind\":\"human\",\"description\":\"fixture do probe_webrtc_close_farewell (VOZ-16): sem agente, teto curto\",\"queue_config\":{\"max_wait_s\":$MAX_WAIT_S},\"media_policy\":{\"customer_publish\":[\"audio\"],\"agent_publish\":[\"audio\"]}}")
    [ "$st" = 201 ] || incon "fixture $POOL nao criada (http $st)"
    sleep 5
  fi
  HUMANOS=$(docker exec plughub-demo-redis-1 redis-cli smembers "$TENANT:pool:$POOL:instances" | grep -c '^human-')
  if [ "$HUMANOS" -gt 0 ]; then
    incon "B: ha $HUMANOS agente(s) humano(s) no $POOL — o contato seria atendido, nao estouraria a fila"
  else
    ENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | grep -E '^PLUGHUB_(JWT_SECRET|TENANT_ID)=' | sed 's/^/-e /' | tr '\n' ' ')
    T0=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    name="probe_voz16_$$_$RANDOM"
    B_OUT=$(timeout "${EXERCISE_TIMEOUT_S:-150}" docker run --rm -i --name "$name" --network "$NET" --entrypoint python \
            $ENV -e POOL="$POOL" -e MAX_WAIT_S="$MAX_WAIT_S" "$IMG" - < infra/test/_webrtc_close_farewell_exercise.py 2>&1)
    [ $? -eq 124 ] && { docker kill "$name" >/dev/null 2>&1; B_OUT="$B_OUT
FALHA TIMEOUT exercicio morto"; }
    tally "$(printf '%s\n' "$B_OUT" | grep -E '^(OK|FALHA|INCONCL|SID) ')"
    N=$(printf '%s\n' "$B_OUT" | grep -cE '^(OK|FALHA) F[1-4] ')
    [ "$N" -ge 4 ] || falha "exercicio emitiu $N de 4 veredictos: $(printf '%s' "$B_OUT" | tail -3 | tr '\n' ' ' | cut -c1-240)"
    SID=$(printf '%s\n' "$B_OUT" | sed -n 's/^SID //p')
    if [ -n "$SID" ]; then
      # o routing loga o QUEUE TIMEOUT DEPOIS de publicar o fechamento: o cliente pode ter
      # recebido o close antes da linha existir (medido — sem a espera, F5 saía vermelho falso)
      achou=""
      for _ in $(seq 1 20); do
        docker logs --since "$T0" "$RT" 2>&1 | grep -q "QUEUE TIMEOUT: session=$SID" && { achou=1; break; }
        sleep 0.5
      done
      if [ -n "$achou" ]; then
        ok "F5 routing-engine registrou QUEUE TIMEOUT da sessao $SID"
      else
        falha "F5 nenhum QUEUE TIMEOUT do routing-engine para $SID — o fechamento veio de outro caminho"
      fi
    fi
  fi
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO — $INCONCL ramo(s) sem medida"; exit 2; fi
echo " VERDE"; exit 0
