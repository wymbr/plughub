#!/usr/bin/env bash
# probe_webrtc_stt_speaches.sh — 2026-09-15  (VOZ-05, fatia 2 · ADR adr-voice-media-plane V-F2)
#
# PERGUNTA: quando o cliente FALA numa chamada atendida por agente de IA, o que ele disse chega
# ao bridge como texto do cliente — transcrito pelo STT auto-hospedado, e só a voz DELE?
#
# O ESTADO QUE O ORIGINOU (medido em 2026-09-15)
#   · o demo não tinha STT: sem chave de Deepgram a fábrica devolvia um mock mudo (fatia 1 o
#     tirou; desde então o bot não entra e o estado diz "chamada NAO transcrita");
#   · o bot assinava "a primeira trilha de áudio" da sala, sem olhar de quem — numa sala com
#     mais de um falante, as vozes iriam para a mesma fila de quadros.
#
# RAMOS (exercício): S1 CONTROLE o serviço transcreve a fixture sozinho (senão INCONCLUSIVO) ·
#   S2 IA de áudio atende e o bot entra (SFU) · S3 a fala do cliente chega ao bridge como
#   `audio_transcript` do cliente · S4 a fala de OUTRO participante, simultânea, não vaza ·
#   LAT latência informativa. Aqui: A1 serviço no ar · A2 os modelos que o GATEWAY pede estão
#   instalados no serviço (a troca de modelo tem de acontecer nos dois lados) · A3 dispositivo.
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
FALHA=0; INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }

echo "════════════════════════════════════════════════════════════════════"
echo " a fala do cliente chega ao agente de IA como texto?"
echo "════════════════════════════════════════════════════════════════════"

IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
if [ -z "$IMG" ]; then
  incon "gateway fora do ar — nao medido"
else
  ENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | grep -E '^PLUGHUB_' | sed 's/^/-e /' | tr '\n' ' ')
  # A1/A2 — o serviço está no ar E tem os modelos que o GATEWAY pede. Modelo pedido e não
  # instalado vira 404 do serviço a cada fala; é a divergência que troca de modelo num lugar só produz.
  A_OUT=$(docker run --rm --network "$NET" --entrypoint python $ENV "$IMG" -c '
import os, httpx
url = os.environ.get("PLUGHUB_WEBRTC_SPEACHES_URL", "")
pedidos = {"STT": os.environ.get("PLUGHUB_WEBRTC_STT_MODEL", ""), "TTS": os.environ.get("PLUGHUB_WEBRTC_TTS_MODEL", "")}
try:
    tem = sorted(m["id"] for m in httpx.get(url + "/v1/models", timeout=10).json().get("data", []))
except Exception as e:
    print("INCONCL A1 servico inalcancavel em %r: %s" % (url, type(e).__name__)); raise SystemExit
print("OK A1 servico no ar em %s com %s" % (url, tem))
for k, m in pedidos.items():
    print(("OK" if m in tem else "FALHA") + " A2 o gateway pede %s %r — instalado no servico=%s" % (k, m, m in tem))' 2>&1)
  while IFS= read -r l; do
    case "$l" in OK\ *) ok "${l#OK }";; FALHA\ *) falha "${l#FALHA }";; INCONCL\ *) incon "${l#INCONCL }";; "") ;; *) incon "A: $l";; esac
  done <<< "$A_OUT"
  # A3 — dispositivo: informativo (a escolha GPU × CPU é de deploy, o probe vale nos dois)
  echo "  INFO    A3 speaches: $(docker inspect -f '{{.Config.Image}}' plughub-demo-speaches-1 2>/dev/null | sed 's/@sha256:\(.\{12\}\).*/@\1…/') device=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' plughub-demo-speaches-1 2>/dev/null | sed -n 's/^WHISPER__INFERENCE_DEVICE=//p')"

  echo ""
  echo "── cenario ────────────────────────────────────────────────────────────"
  name="probe_voz05s_$$_$RANDOM"
  OUT=$(timeout "${EXERCISE_TIMEOUT_S:-180}" docker run --rm -i --name "$name" --network "$NET" --entrypoint python \
        $ENV "$IMG" - < infra/test/_webrtc_stt_speaches_exercise.py 2>&1 | grep -E '^(OK|FALHA|INCONCL|INFO) ')
  [ "${PIPESTATUS[0]}" = 124 ] && { docker kill "$name" >/dev/null 2>&1; OUT="$OUT
FALHA TIMEOUT exercicio morto"; }
  while IFS= read -r l; do
    case "$l" in OK\ *) ok "${l#OK }";; FALHA\ *) falha "${l#FALHA }";; INCONCL\ *) incon "${l#INCONCL }";;
      INFO\ *) echo "  INFO    ${l#INFO }";; "") ;; *) incon "saida inesperada: $l";; esac
  done <<< "$OUT"
  if ! printf '%s\n' "$OUT" | grep -qE '^INCONCL S1 '; then
    N=$(printf '%s\n' "$OUT" | grep -cE '^(OK|FALHA) (S[1-4]|LIMPEZA) ')
    [ "$N" -ge 5 ] || falha "exercicio emitiu $N de 5 veredictos"
  fi
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO — $INCONCL ramo(s) sem medida"; exit 2; fi
echo " VERDE"; exit 0
