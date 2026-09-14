#!/usr/bin/env bash
# probe_webrtc_participant_media.sh — 2026-09-14  (VOZ-09 · ADR adr-voice-media-plane V11)
#
# PERGUNTA: a mídia do WebRTC é fato do PARTICIPANTE — e o SFU obedece?
#
# O DEFEITO QUE O ORIGINOU (vermelho ao vivo, antes do conserto)
#   O adapter escolhia UM meio para a sessão (`negotiated_medium`, gravado em
#   `channel:webrtc:{sid}:medium`) e cada `routing.assigned` o SOBRESCREVIA. Um humano de
#   vídeo atende, o cliente publica microfone, um especialista de IA de texto entra: o
#   cliente recebe `webrtc.renegotiate` mandando ir para `text`, o estado vira `text` — e o
#   SFU continua com `can_publish=True, can_publish_sources=[]`, que no LiveKit quer dizer
#   TODAS as fontes. Duas respostas para a mesma pergunta, e nenhuma certa. Por baixo, a
#   capacidade que decidia o meio (`media_capabilities`) não tinha produtor: todo contato
#   sairia em texto.
#
# QUATRO RAMOS
#   A  CONTRATO, medido no LEITOR — quem escreve e quem lê o mesmo campo, dos dois lados:
#      A1 o bridge grava `framework` no `routing.assigned` (e não mais `agent_type`), os
#         três chamadores passam um framework que a política conhece, e o gateway LÊ
#         `framework`; A2 nenhuma mensagem do servidor carrega `negotiated_medium`, e o
#         adapter não guarda `_mediums` nem a chave `:medium`; A3 os dois clientes (Console
#         e widget) leem SÓ chaves que o servidor emite, e a visão de supervisor pede
#         `role=supervisor`.
#   B  IMAGEM — a política e a troca de permissão existem na imagem que roda.
#   C  AO VIVO NO SFU — o cenário do defeito, com cliente LiveKit de verdade e a resposta
#      perguntada AO SFU: humano atende e o cliente publica; especialista de texto entra e
#      NADA muda; humano sai e o SFU RETIRA a trilha; outro humano atende e o cliente volta a
#      publicar sem reconectar; fora da sala o token novo traz o teto novo; supervisor oculto
#      não publica. Mutações embutidas: teto que nunca cai TEM de derrubar C3; o último
#      atendente substituindo (a semântica velha) TEM de derrubar C2.
#   D  PRODUTOR VIVO — entradas `routing.assigned` escritas pelo bridge DEPOIS que o container
#      subiu carregam `framework` conhecido. Sem entrada nova, INCONCLUSIVO — nunca verde por
#      ausência de amostra.
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
BRIDGE="${BRIDGE_CONTAINER:-plughub-demo-orchestrator-bridge-1}"
REDIS_C="${REDIS_CONTAINER:-plughub-demo-redis-1}"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
tally() {
  while IFS= read -r l; do
    case "$l" in OK\ *) ok "${l#OK }";; FALHA\ *) falha "${l#FALHA }";; INCONCL\ *) incon "${l#INCONCL }";;
      *) [ -n "$l" ] && incon "saida inesperada: $l";; esac
  done <<< "$1"
}

echo "════════════════════════════════════════════════════════════════════"
echo " a mídia do WebRTC é fato do participante — e o SFU obedece?"
echo "════════════════════════════════════════════════════════════════════"

# ── A ────────────────────────────────────────────────────────────────────────
echo ""
echo "── A · CONTRATO (medido no leitor) ────────────────────────────────────"
A_OUT=$(python3 - <<'PYEOF'
import ast, io, re
def r(ok, txt): print(("OK " if ok else "FALHA ") + txt)
def src(p): return io.open(p, encoding="utf-8").read()
def docnodes(tree):
    ids = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body \
                and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant):
            ids.add(id(n.body[0].value))
    return ids
def code_strings(tree):
    d = docnodes(tree)
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in d]

GW = "packages/channel-gateway/src/plughub_channel_gateway/"
BR = "packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py"

# A1 — produtor (bridge) × leitor (gateway)
bt = ast.parse(src(BR))
fn = next(n for n in ast.walk(bt) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_write_routing_assigned_to_stream")
keys = set()
for n in ast.walk(fn):
    if isinstance(n, ast.Dict):
        ks = {k.value for k in n.keys if isinstance(k, ast.Constant)}
        if "routing.assigned" in {v.value for v in n.values if isinstance(v, ast.Constant)}:
            keys = ks
r("framework" in keys and "agent_type" not in keys,
  "A1 bridge grava `framework` no routing.assigned e nao `agent_type` (chaves=%s)" % sorted(keys))
known = {"human", "native", "external-mcp"}
calls = [c for c in ast.walk(bt) if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "_write_routing_assigned_to_stream"]
fw = [next((k.value.value for k in c.keywords if k.arg == "framework" and isinstance(k.value, ast.Constant)), None) for c in calls]
r(len(calls) >= 3 and all(f in known for f in fw), "A1 %d chamador(es) passam framework conhecido: %s" % (len(calls), fw))
gt = ast.parse(src(GW + "adapters/webrtc.py"))
reads_fw = any(isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "get" and n.args
               and isinstance(n.args[0], ast.Constant) and n.args[0].value == "framework" for n in ast.walk(gt))
r(reads_fw, "A1 gateway LE `framework` do evento")
pt = ast.parse(src(GW + "adapters/media_policy.py"))
cons = next(n for n in pt.body if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "CONSUMES_BY_FRAMEWORK")
pol_keys = {k.value for k in cons.value.keys}
r(pol_keys == known, "A1 a politica conhece exatamente os frameworks do produtor (%s)" % sorted(pol_keys))

# A2 — sem meio único
cs = code_strings(gt)
r("negotiated_medium" not in cs, "A2 nenhuma mensagem do adapter carrega `negotiated_medium`")
r(not any(isinstance(n, ast.Attribute) and n.attr == "_mediums" for n in ast.walk(gt)),
  "A2 adapter nao guarda `_mediums`")
r(not any(s.endswith(":medium") for s in cs), "A2 adapter nao grava/le a chave `channel:webrtc:{sid}:medium`")

# A3 — leitores (Console e widget) leem só o que o servidor emite
emitted_token = set()
for n in ast.walk(gt):
    if isinstance(n, ast.AsyncFunctionDef) and n.name == "get_token":
        for d in ast.walk(n):
            if isinstance(d, ast.Return) and isinstance(d.value, ast.Dict):
                emitted_token = {k.value for k in d.value.keys if isinstance(k, ast.Constant)}
def emitted_for(msg_type):
    out = set()
    for d in ast.walk(gt):
        if isinstance(d, ast.Dict) and any(isinstance(v, ast.Constant) and v.value == msg_type for v in d.values):
            out |= {k.value for k in d.keys if isinstance(k, ast.Constant)}
    return out
hook = src("packages/platform-ui/src/modules/agent-assist/hooks/useWebRTCSession.ts")
read_hook = set(re.findall(r"\bbody\.(\w+)", hook))
r(read_hook and read_hook <= emitted_token,
  "A3 Console le do token so chaves emitidas (le=%s, emite=%s)" % (sorted(read_hook), sorted(emitted_token)))
r("negotiated_medium" not in re.sub(r"(//[^\n]*|/\*.*?\*/)", "", hook, flags=re.S),
  "A3 Console nao le `negotiated_medium` fora de comentario")
sup = src("packages/platform-ui/src/modules/agent-assist/components/WebRTCSupervisorView.tsx")
r(re.search(r'useWebRTCSession\([^)]*"supervisor"\)', sup) is not None, "A3 visao de supervisor pede role=supervisor")
widget = src("infra/demo/web/webrtc-widget.html")
def handler_reads(t):
    m = re.search(r"msg\.type === '%s'\) \{(.*?)\n    \}" % re.escape(t), widget, re.S)
    return set(re.findall(r"\bmsg\.(\w+)", m.group(1))) - {"type"} if m else None
for t in ("webrtc.ready", "webrtc.media"):
    reads = handler_reads(t)
    em = emitted_for(t) - {"type"}
    r(reads is not None and reads <= em, "A3 widget em `%s` le so chaves emitidas (le=%s, emite=%s)" % (t, sorted(reads or []), sorted(em)))
PYEOF
)
tally "$A_OUT"

IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
if [ -z "$IMG" ]; then echo ""; echo "INCONCLUSIVO: $GW fora do ar — B..D nao medidos"; exit 2; fi
LKENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | grep '^PLUGHUB_WEBRTC_LIVEKIT_' | sed 's/^/-e /' | tr '\n' ' ')
# ⚠️ `timeout` mata o CLI do docker, não o container — e um exercício pendurado que some
# calado é o teste que não pode reprovar. Por isso: nome próprio, kill explícito, e uma
# linha FALHA que o `tally` conta. (Medido na construção deste probe: o cliente LiveKit
# em Python pendura no `disconnect` depois de uma publicação recusada.)
run_img() {
  local name="probe_voz09_$$_$RANDOM" rc
  timeout "${EXERCISE_TIMEOUT_S:-300}" docker run --rm -i --name "$name" --network "$NET" --entrypoint python $LKENV "$@"
  rc=$?
  if [ "$rc" -eq 124 ]; then
    docker kill "$name" >/dev/null 2>&1
    echo "FALHA TIMEOUT exercicio passou de ${EXERCISE_TIMEOUT_S:-300}s e foi morto ($name)"
  fi
  return 0
}

# ── B ────────────────────────────────────────────────────────────────────────
echo ""
echo "── B · IMAGEM (${IMG:7:12}) ───────────────────────────────────────────"
B_OUT=$(run_img "$IMG" -c '
from plughub_channel_gateway.adapters import media_policy, webrtc_provider as p
ok = hasattr(media_policy, "customer_ceiling") and hasattr(p.LiveKitProvider, "update_participant_permission")
print(("OK " if ok else "FALHA ") + "B1 politica por participante e troca de permissao presentes na imagem")' 2>&1 | tail -1)
case "$B_OUT" in OK\ *|FALHA\ *) tally "$B_OUT";; *) falha "B1 imagem sem a politica por participante: $B_OUT";; esac

# ── C ────────────────────────────────────────────────────────────────────────
echo ""
echo "── C · AO VIVO NO SFU  +  mutações ────────────────────────────────────"
EX=infra/test/_webrtc_participant_media_exercise.py
C_OUT=$(run_img "$IMG" - < "$EX" 2>/dev/null)
tally "$C_OUT"
N=$(printf '%s\n' "$C_OUT" | grep -cE '^(OK|FALHA) (C[1-6]|LIMPEZA) ')
[ "$N" -ge 7 ] || falha "exercicio emitiu $N de 7 veredictos — morreu no meio (rode $EX a mao)"

for mut in "mut_replace:C2" "mut_never_falls:C3"; do
  mode=${mut%%:*}; must=${mut##*:}
  M_OUT=$(run_img -e MODE="$mode" "$IMG" - < "$EX" 2>/dev/null)
  if printf '%s\n' "$M_OUT" | grep -q "^FALHA $must "; then
    ok "M $mode derruba $must (o ramo reprova quando o defeito volta)"
  else
    falha "M $mode NAO derrubou $must — o ramo passaria com o defeito: $(printf '%s' "$M_OUT" | grep " $must " | head -1)"
  fi
done

# ── D ────────────────────────────────────────────────────────────────────────
echo ""
echo "── D · PRODUTOR VIVO (bridge) ─────────────────────────────────────────"
STARTED=$(docker inspect -f '{{.State.StartedAt}}' "$BRIDGE" 2>/dev/null)
if [ -z "$STARTED" ]; then
  incon "D: container $BRIDGE fora do ar"
else
  START_MS=$(( $(date -d "$STARTED" +%s) * 1000 ))
  NOVAS=0; SEM=0; DESC=""
  # Uma varredura só, dentro do container do Redis: um `docker exec` por stream levava minutos.
  # Busca só a janela posterior ao boot do bridge (`XRANGE <start_ms> +`).
  while IFS='|' read -r k eid fw; do
    [ -z "$eid" ] && continue
    NOVAS=$((NOVAS+1))
    case "$fw" in human|native|external-mcp) ;; *) SEM=$((SEM+1)); DESC="$k $eid framework='$fw'";; esac
  done < <(docker exec "$REDIS_C" sh -c '
    for k in $(redis-cli --scan --pattern "session:*:stream"); do
      redis-cli --raw XRANGE "$k" '"$START_MS"' + | awk -v k="$k" "
        /^[0-9]+-[0-9]+\$/ { if (id!=\"\" && t==\"routing.assigned\") print k \"|\" id \"|\" fw; id=\$0; t=\"\"; fw=\"\"; prev=\"\"; next }
        { if (prev==\"type\") t=\$0; if (prev==\"framework\") fw=\$0; prev=\$0 }
        END { if (id!=\"\" && t==\"routing.assigned\") print k \"|\" id \"|\" fw }"
    done' 2>/dev/null)
  if [ "$NOVAS" -eq 0 ]; then
    incon "D: nenhum routing.assigned escrito depois que o bridge subiu ($STARTED) — sem amostra"
  elif [ "$SEM" -eq 0 ]; then
    ok "D1 $NOVAS routing.assigned novo(s), todos com framework conhecido"
  else
    falha "D1 $SEM de $NOVAS routing.assigned novo(s) sem framework conhecido (ex.: $DESC)"
  fi
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO — $INCONCL ramo(s) sem medida"; exit 2; fi
echo " VERDE"; exit 0
