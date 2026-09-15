#!/usr/bin/env bash
# gate_webrtc_console_live.sh — 2026-09-15  (VOZ-04, item 5 · ADR adr-voice-media-plane V-F1)
#
# ASSISTIDO: rode DURANTE a chamada do roteiro `docs/guias/roteiro-validacao-webrtc-console.md`
# (agente humano logado no Console, cliente no widget, os dois com camera e microfone).
#
# PERGUNTA: numa chamada feita por gente, no browser do host, a midia atravessa de verdade — os
# dois lados estao na sala do SFU publicando audio e video, e quem esta la e quem foi atribuido?
#
# Por que nao e automatico: o que se quer provar e o caminho do BROWSER (login real, permissao
# de camera, ICE pelo loopback do host). Os probes `probe_webrtc_agent_console.sh` e
# `probe_webrtc_media_plane.sh` cobrem o resto sem browser; este e o pedaco que so um humano
# consegue exercer. Ele NAO cria nada — so le o stream da sessao e o SFU.
#
# Ramos (no exercicio): L1 atribuido a humano · L2 agente e cliente na sala · L3 cada um publica
# audio e video nao mudos · L4 identidade na sala = instancia atribuida · L5 o audio tem SINAL
# (ouvinte oculto; silencio digital reprova). Que o BROWSER toque o som continua sendo o V4.
#
# USO:   bash infra/test/gate_webrtc_console_live.sh [pool_id] [session_id]
#        pool padrao `webrtc_atendimento`; sem session_id, acha o unico contato aberto do pool.
# EXIT:  0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
POOL="${1:-webrtc_atendimento}"
SID="${2:-}"
FALHA=0; INCONCL=0

echo "════════════════════════════════════════════════════════════════════"
echo " a chamada do roteiro atravessa midia pelo SFU? (pool=$POOL)"
echo "════════════════════════════════════════════════════════════════════"

IMG=$(docker inspect -f '{{.Image}}' "$GW" 2>/dev/null)
if [ -z "$IMG" ]; then
  echo "  INCONCL gateway fora do ar"; echo " INCONCLUSIVO"; exit 2
fi
ENV=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$GW" | grep -E '^PLUGHUB_WEBRTC_LIVEKIT_(URL|API_KEY|API_SECRET)=' | sed 's/^/-e /' | tr '\n' ' ')
OUT=$(timeout 60 docker run --rm -i --network "$NET" --entrypoint python $ENV -e POOL="$POOL" -e SID="$SID" \
      "$IMG" - < infra/test/_webrtc_console_live_exercise.py 2>&1)

while IFS= read -r l; do
  case "$l" in
    OK\ *)      echo "  OK      ${l#OK }" ;;
    FALHA\ *)   echo "  FALHA   ${l#FALHA }"; FALHA=$((FALHA + 1)) ;;
    INCONCL\ *) echo "  INCONCL ${l#INCONCL }"; INCONCL=$((INCONCL + 1)) ;;
    SID\ *)     echo "  sessao  ${l#SID }" ;;
    *livekit_ffi*) ;;   # aviso de teardown do SDK nativo do ouvinte (L5), nao e veredicto
    "") ;;
    *)          echo "  INCONCL saida inesperada: $l"; INCONCL=$((INCONCL + 1)) ;;
  esac
done <<< "$OUT"

N=$(printf '%s\n' "$OUT" | grep -cE '^(OK|FALHA) L[1-5] ')
[ "$INCONCL" -eq 0 ] && [ "$N" -lt 7 ] && { echo "  FALHA   exercicio emitiu $N de 7 veredictos"; FALHA=$((FALHA + 1)); }

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then echo " VERMELHO — $FALHA falha(s)"; exit 1; fi
if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO — $INCONCL ramo(s) sem medida"; exit 2; fi
echo " VERDE"; exit 0
