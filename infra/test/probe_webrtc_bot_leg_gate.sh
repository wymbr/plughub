#!/usr/bin/env bash
# probe_webrtc_bot_leg_gate.sh — 2026-09-15  (VOZ-05, fatia 1 · ADR adr-voice-media-plane V4)
#
# PERGUNTA: o bot leg (STT/TTS) entra na sala quando há o que TRANSCREVER (toda chamada com
# áudio, humano ou IA — a qualidade avalia sobre a transcrição) e o que CONVERTER (a IA ouve por
# STT e fala por TTS) — e, quando falta provedor, a plataforma diz o quê em vez de fingir?
#
# O DEFEITO QUE O ORIGINOU (medido ao vivo em 2026-09-15)
#   · sem chave de STT o provider virava `MockSTTProvider`: um participante oculto entrava na
#     sala (log do gateway: `identity=bot-19dcc959`, no contato de humano do probe do Console),
#     consumia o áudio e não transcrevia nada, sem uma linha de log — a degradação muda que a
#     VOZ-01 deixou registrada e o invariante 2 do ADR proíbe;
#   · o gatilho era "o teto do cliente tem áudio", e a IA consome só texto: o bot NUNCA entrava
#     numa chamada de IA, e ela nunca ganhava áudio, também sem dizer por quê;
#   · o `probe_webrtc_participant_media` desligava o STT de propósito, e por isso nunca viu.
#
# RAMOS: V1 humano sem STT → sem bot e "chamada NAO transcrita" no estado · V2 IA sem STT/TTS →
#   cliente sem áudio e motivo NOMEADO · V3 CONTROLE IA com STT/TTS → áudio e bot na sala · V4
#   CONTROLE humano com STT → bot na sala (presença perguntada ao SFU) · M1/M2 mutações que TÊM
#   de reprovar V1 e V2.
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
echo " o bot leg entra para quem le texto, e diz quando nao pode?"
echo "════════════════════════════════════════════════════════════════════"

IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
if [ -z "$IMG" ]; then
  incon "gateway fora do ar — nao medido"
else
  ENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | grep -E '^PLUGHUB_' | sed 's/^/-e /' | tr '\n' ' ')
  roda() {
    local mode=$1 name="probe_voz05_$$_$RANDOM"
    timeout "${EXERCISE_TIMEOUT_S:-120}" docker run --rm -i --name "$name" --network "$NET" --entrypoint python \
      $ENV -e MODE="$mode" "$IMG" - < infra/test/_webrtc_bot_leg_gate_exercise.py 2>&1 | grep -E '^(OK|FALHA|INCONCL) '
    [ "${PIPESTATUS[0]}" -eq 124 ] && { docker kill "$name" >/dev/null 2>&1; echo "FALHA TIMEOUT exercicio morto ($mode)"; }
  }

  echo ""
  echo "── cenario ────────────────────────────────────────────────────────────"
  OUT=$(roda full)
  while IFS= read -r l; do
    case "$l" in OK\ *) ok "${l#OK }";; FALHA\ *) falha "${l#FALHA }";; INCONCL\ *) incon "${l#INCONCL }";; "") ;; *) incon "saida inesperada: $l";; esac
  done <<< "$OUT"
  N=$(printf '%s\n' "$OUT" | grep -cE '^(OK|FALHA) (V[1-4]|LIMPEZA) ')
  [ "$N" -ge 5 ] || falha "exercicio emitiu $N de 5 veredictos"

  echo ""
  echo "── mutacoes (TEM de reprovar) ─────────────────────────────────────────"
  M1=$(roda mut_bot_sem_stt | grep -E ' V1 ')
  case "$M1" in FALHA*) ok "M1 bot entrando sem STT (o placebo) → V1 reprova (${M1#FALHA V1 })";;
    *) falha "M1 mutacao bot-sem-STT NAO reprovou o V1: $M1";; esac
  M2=$(roda mut_available_always | grep -E ' V2 ')
  case "$M2" in FALHA*) ok "M2 IA ganhando audio sem provedor → V2 reprova";;
    *) falha "M2 mutacao disponivel-sempre NAO reprovou o V2: $M2";; esac
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " VERMELHO — $FALHA falha(s), $INCONCL inconclusivo(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO — $INCONCL ramo(s) sem medida"; exit 2; fi
echo " VERDE"; exit 0
