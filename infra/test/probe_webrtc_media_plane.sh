#!/usr/bin/env bash
# probe_webrtc_media_plane.sh — 2026-09-14  (VOZ-01 · ADR adr-voice-media-plane V-F0)
#
# PERGUNTA: o plano de mídia WebRTC EXISTE — e, quando falta, o canal RECUSA em vez
#           de fingir?
#
# O DEFEITO QUE O ORIGINOU
#   O Arc 15 foi marcado pronto sem SFU em ambiente algum. Medido ao vivo antes desta
#   fatia: sem credencial o `LiveKitProvider` ligava `_dev_mode` e devolvia token
#   `dev-token-agent-x-plughub-x`, sala `RM_dev_…` e egress `EG_dev_…`; o SDK nem era
#   dependência; nenhum compose tinha LiveKit ou TURN; e `GET /webrtc/token/{sid}`
#   emitia token — inclusive de SUPERVISOR OCULTO, com identidade escolhida na query —
#   a quem pedisse, sem credencial nenhuma, num prefixo publicável da borda. O placebo
#   tornava a rota inerte; o provisionamento a tornaria chave real. Nada disso ficava
#   vermelho: um token bem-formado e falso é o valor plausível na forma mais cara.
#
# SEIS RAMOS
#   A  DECLARACAO — config RESOLVIDA do compose (`docker compose config`, nao o texto):
#      `livekit` e `coturn` com imagem fixada; as credenciais do gateway CASAM com
#      `keys:` do SFU e a do TURN casa entre SFU e coturn (divergencia aqui = 401 em todo
#      join, sem mais nada vermelho); SDK no pyproject; e, por AST, o provider sem
#      `_dev_mode`, sem `except ImportError` e sem literal placebo.
#   B  IMAGEM — pergunta a IMAGEM do gateway, nunca o container: o SDK importa, com os
#      nomes que o provider usa.
#   C  RECUSA — na imagem: sem credencial o provider levanta nomeando as TRES envs; com
#      credencial (controle positivo) constroi. Sem o positivo, uma recusa incondicional
#      passaria.
#   D  SFU REAL — o provider da imagem cria sala e a le de volta; um participante entra
#      SO POR RELAY e publica trilha, e o SFU o lista; token de segredo errado e sala nao
#      criada sao RECUSADOS (o SFU verifica, nao e decoracao).
#   E  TURN — alocacao com a credencial declarada funciona; com credencial errada, falha.
#   F  ROTA — `/webrtc/token/{sid}` ao vivo: 401 sem Bearer e com assinatura invalida,
#      403 sem capacidade / fora do pool / supervisor sem `monitorar`, 404 outro tenant,
#      422 role desconhecido; e DOIS controles positivos (agente e supervisor) cujo token
#      o SFU aceita, com a identidade vinda do JWT e nao da query.
#
# MUTACAO EMBUTIDA
#   M1 — o mesmo `create_room` com o segredo trocado TEM de falhar. Sem ele, D poderia
#   estar verde contra um SFU que aceitasse qualquer coisa.
#   A bateria completa (coturn parado derruba D2; imagem ANTIGA derruba B/C/F; chave
#   divergente derruba A; literal placebo reintroduzido derruba A4) foi rodada na
#   entrega — `CHANGELOG.md` § 2026-09-14 VOZ-01.
#
# ⚠️ ESCOPO: plano de mídia DENTRO da rede do compose. Browser no host (candidato ICE
#    alcançavel de fora, TURN com endereço externo) e a VOZ-04, que mede um browser.
# ⚠️ Egress (gravação) NAO esta no compose — e a VOZ-06; este probe nao o cobra.
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO (stack fora do ar)

set -uo pipefail
cd "$(dirname "$0")/../.."

COMPOSE="${COMPOSE_FILE_DEMO:-docker-compose.demo.yml}"
GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
TURN_C="${TURN_CONTAINER:-plughub-demo-coturn-1}"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }

echo "════════════════════════════════════════════════════════════════════"
echo " o plano de mídia WebRTC existe — e, quando falta, o canal recusa?"
echo "════════════════════════════════════════════════════════════════════"

CFG_JSON=$(docker compose -f "$COMPOSE" config --format json 2>/dev/null)
if [ -z "$CFG_JSON" ]; then
  echo "INCONCLUSIVO: docker compose config nao resolveu $COMPOSE"; exit 2
fi

# ── A ────────────────────────────────────────────────────────────────────────
echo ""
echo "── A · DECLARACAO ─────────────────────────────────────────────────────"
A_OUT=$(CFG_JSON="$CFG_JSON" python3 - <<'PYEOF'
import ast, io, json, os, re
cfg = json.loads(os.environ["CFG_JSON"])
sv = cfg.get("services", {})
res = []
def r(ok, txt): res.append(("OK" if ok else "FALHA") + " " + txt)

for name in ("livekit", "coturn"):
    img = (sv.get(name) or {}).get("image", "")
    r(bool(img) and ":" in img and not img.endswith(":latest"),
      "A1 servico `%s` com imagem fixada (%s)" % (name, img or "AUSENTE"))

env = (sv.get("channel-gateway") or {}).get("environment", {}) or {}
url, key, sec = (env.get("PLUGHUB_WEBRTC_LIVEKIT_" + k, "") for k in ("URL", "API_KEY", "API_SECRET"))
pub = env.get("PLUGHUB_WEBRTC_LIVEKIT_PUBLIC_URL", "")
r(all((url, key, sec, pub)), "A2 gateway declara URL/PUBLIC_URL/API_KEY/API_SECRET")

lkcfg = ((sv.get("livekit") or {}).get("environment", {}) or {}).get("LIVEKIT_CONFIG", "")
keys, in_keys = {}, False
for line in lkcfg.splitlines():
    if re.match(r"^keys:\s*$", line):
        in_keys = True; continue
    if in_keys:
        m = re.match(r"^\s+([^:\s]+):\s*(\S+)\s*$", line)
        if m: keys[m.group(1)] = m.group(2)
        elif line.strip(): in_keys = False
r(bool(key) and keys.get(key) == sec,
  "A3 chave/segredo do gateway CASAM com `keys:` do SFU (%d chave(s) no SFU)" % len(keys))

turn_cred = re.search(r"credential:\s*(\S+)", lkcfg)
turn_user = re.search(r"username:\s*(\S+)", lkcfg)
cmd = (sv.get("coturn") or {}).get("command") or []
cmd = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
m = re.search(r"--user=([^:\s]+):(\S+)", cmd)
r(bool(turn_cred and turn_user and m) and (turn_user.group(1), turn_cred.group(1)) == (m.group(1), m.group(2)),
  "A3 credencial TURN do SFU CASA com o `--user` do coturn")
r("auto_create: false" in lkcfg, "A3 `room.auto_create: false` (sala nasce pelo gateway, nao pelo join)")

py = io.open("packages/channel-gateway/pyproject.toml", encoding="utf-8").read()
r(re.search(r'"livekit-api[>=<]', py) is not None and re.search(r'"livekit[>=<]', py) is not None,
  "A4 pyproject declara `livekit-api` e `livekit`")

src = io.open("packages/channel-gateway/src/plughub_channel_gateway/adapters/webrtc_provider.py",
              encoding="utf-8").read()
tree = ast.parse(src)
cls = next((c for c in tree.body if isinstance(c, ast.ClassDef) and c.name == "LiveKitProvider"), None)
if cls is None:
    r(False, "A4 classe LiveKitProvider nao encontrada")
else:
    # Docstrings citam o passado POR ESCRITO (e devem); literal de CODIGO nao pode.
    docnodes = set()
    for n in [cls] + [f for f in ast.walk(cls) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        if n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant):
            docnodes.add(id(n.body[0].value))
    achados = []
    for n in ast.walk(cls):
        if isinstance(n, ast.Attribute) and n.attr == "_dev_mode":
            achados.append("_dev_mode")
        if isinstance(n, ast.ExceptHandler) and n.type is not None and "ImportError" in ast.unparse(n.type):
            achados.append("except ImportError")
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docnodes:
            for marca in ("dev-token", "missing-sdk", "RM_dev", "EG_dev", "EG_missing", "RM_mock"):
                if marca in n.value:
                    achados.append("literal " + marca)
    r(not achados, "A4 LiveKitProvider sem placebo (%s)" % (", ".join(sorted(set(achados))) or "nada achado"))
print("\n".join(res))
PYEOF
)
while IFS= read -r l; do
  case "$l" in OK\ *) ok "${l#OK }";; FALHA\ *) falha "${l#FALHA }";; *) [ -n "$l" ] && incon "$l";; esac
done <<< "$A_OUT"

# Credenciais RESOLVIDAS, para B..F.
read -r LK_KEY LK_SECRET AUTH_SECRET TENANT <<< "$(CFG_JSON="$CFG_JSON" python3 -c '
import json, os
e = json.loads(os.environ["CFG_JSON"])["services"]["channel-gateway"]["environment"]
print(e.get("PLUGHUB_WEBRTC_LIVEKIT_API_KEY","-"), e.get("PLUGHUB_WEBRTC_LIVEKIT_API_SECRET","-"),
      e.get("PLUGHUB_AUTH_JWT_SECRET","-"), e.get("PLUGHUB_TENANT_ID","-"))')"
LK_URL="ws://livekit:7880"

IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
if [ -z "$IMG" ]; then
  echo ""; echo "INCONCLUSIVO: container $GW fora do ar — B..F nao medidos"; exit 2
fi
run_img() { docker run --rm -i --network "$NET" --entrypoint python "$@"; }

# ── B ────────────────────────────────────────────────────────────────────────
echo ""
echo "── B · IMAGEM (${IMG:7:12}) ───────────────────────────────────────────"
B_OUT=$(run_img "$IMG" -c '
import livekit.rtc
from livekit.api import (AccessToken, VideoGrants, LiveKitAPI, CreateRoomRequest, DeleteRoomRequest,
    ListRoomsRequest, ListParticipantsRequest, RoomCompositeEgressRequest, EncodedFileOutput, StopEgressRequest)
print("ok")' 2>&1 | tail -1)
[ "$B_OUT" = "ok" ] && ok "B1 SDK importa na IMAGEM com os nomes que o provider usa" \
                   || falha "B1 SDK na imagem: $B_OUT"

# ── C ────────────────────────────────────────────────────────────────────────
echo ""
echo "── C · RECUSA ─────────────────────────────────────────────────────────"
C_OUT=$(run_img "$IMG" -c '
from plughub_channel_gateway.adapters import webrtc_provider as m
Unav = getattr(m, "WebRTCProviderUnavailable", None)
try:
    t = m.LiveKitProvider("", "", "").generate_token(m.TokenGrants(room_name="r", identity="i"))
    print("FALHA C1 sem credencial devolveu token: %s" % t[:40])
except Exception as e:
    envs = ("PLUGHUB_WEBRTC_LIVEKIT_URL","PLUGHUB_WEBRTC_LIVEKIT_API_KEY","PLUGHUB_WEBRTC_LIVEKIT_API_SECRET")
    nomeia = all(v in str(e) for v in envs)
    print(("OK" if (Unav and isinstance(e, Unav) and nomeia) else "FALHA")
          + " C1 sem credencial RECUSA nomeando as tres envs (%s)" % str(e)[:70])
try:
    m.LiveKitProvider("ws://x", "k", "s"*32); print("OK C2 controle positivo: com credencial constroi")
except Exception as e:
    print("FALHA C2 com credencial tambem recusou: %s" % e)
' 2>&1)
while IFS= read -r l; do
  case "$l" in OK\ *) ok "${l#OK }";; FALHA\ *) falha "${l#FALHA }";; *) [ -n "$l" ] && incon "C: $l";; esac
done <<< "$C_OUT"

# ── D + F (exercicio na imagem) ──────────────────────────────────────────────
EX=infra/test/_webrtc_media_plane_exercise.py
if ! docker ps --format '{{.Names}}' | grep -q livekit; then
  echo ""; incon "D/F: servico livekit fora do ar"
else
  echo ""
  echo "── D · SFU REAL  +  F · ROTA  +  M1 · MUTACAO ─────────────────────────"
  EX_OUT=$(run_img -e LK_URL="$LK_URL" -e LK_KEY="$LK_KEY" -e LK_SECRET="$LK_SECRET" \
      -e GW_URL="http://channel-gateway:8010" -e REDIS_URL="redis://redis:6379" \
      -e AUTH_SECRET="$AUTH_SECRET" -e TENANT="$TENANT" "$IMG" - < "$EX" 2>/dev/null)
  MUT_OUT=$(run_img -e MODE=wrong_secret -e LK_URL="$LK_URL" -e LK_KEY="$LK_KEY" \
      -e LK_SECRET="$LK_SECRET" "$IMG" - < "$EX" 2>/dev/null)
  N=0
  while IFS= read -r l; do
    case "$l" in
      OK\ *)      ok "${l#OK }"; N=$((N+1));;
      FALHA\ *)   falha "${l#FALHA }"; N=$((N+1));;
      INCONCL\ *) incon "${l#INCONCL }";;
    esac
  done <<< "$EX_OUT
$MUT_OUT"
  # 4 (D) + 10 (F) + LIMPEZA + M1 = 16 linhas; menos que isso e exercicio que morreu no meio.
  [ "$N" -ge 16 ] || falha "exercicio emitiu $N de 16 veredictos — morreu no meio (rode $EX a mao)"
fi

# ── E ────────────────────────────────────────────────────────────────────────
echo ""
echo "── E · TURN ───────────────────────────────────────────────────────────"
if ! docker ps --format '{{.Names}}' | grep -qx "$TURN_C"; then
  incon "E: container $TURN_C fora do ar"
else
  read -r T_USER T_CRED <<< "$(CFG_JSON="$CFG_JSON" python3 -c '
import json, os, re
c = json.loads(os.environ["CFG_JSON"])["services"]["coturn"]["command"]
c = " ".join(c) if isinstance(c, list) else c
m = re.search(r"--user=([^:\s]+):(\S+)", c); print(m.group(1), m.group(2)) if m else print("- -")')"
  uc() { docker exec "$TURN_C" turnutils_uclient -u "$T_USER" -w "$1" -r plughub.local -n 1 -m 1 -l 32 -e 127.0.0.1 127.0.0.1 2>&1; }
  BOM=$(uc "$T_CRED"); RUIM=$(uc "credencial_errada_probe")
  if echo "$BOM" | grep -q "channel bind" && ! echo "$BOM" | grep -q "Cannot complete Allocation"; then
    ok "E1 alocacao TURN com a credencial declarada"
  else
    falha "E1 alocacao com a credencial declarada falhou: $(echo "$BOM" | tail -1)"
  fi
  echo "$RUIM" | grep -q "Cannot complete Allocation" \
    && ok "E2 credencial errada NAO aloca" \
    || falha "E2 credencial errada alocou (ou o cliente nao rodou): $(echo "$RUIM" | tail -1)"
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO — $INCONCL ramo(s) sem medida"; exit 2; fi
echo " VERDE"; exit 0
