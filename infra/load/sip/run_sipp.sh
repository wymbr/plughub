#!/usr/bin/env bash
# run_sipp.sh — rodada de voz SIP do teste de carga (PRD-02).
#
# Sustenta CONCURRENCY chamadas simultâneas: taxa = CONCURRENCY / duração média da
# chamada, com teto `-l` para o SIPp não passar do alvo. Grava estatística a cada 10 s
# (`-trace_stat`) e o tempo de estabelecimento por chamada (`-trace_rtt`).
#
# Uso (no gerador, com sipp >= 3.6 compilado com PCAP e RTP stream):
#   SIP_TARGET=10.0.0.20:5060 SIP_NUMBER=+551140000000 SIP_USER=plughub_demo \
#   SIP_PASSWORD=... CONCURRENCY=800 TALK_MS=90000 RAMP_CPS=5 bash run_sipp.sh
#
# ⚠️ Cada chamada abre portas RTP próprias: `-mp` define a base, e o gerador precisa
# de ~4 portas por chamada livres a partir dela. Acima de ~500 chamadas por gerador o
# SIPp satura CPU com rtp_stream — reparta entre geradores (ver README).
set -euo pipefail

: "${SIP_TARGET:?host:porta do livekit-sip}"
: "${SIP_NUMBER:?número discado (ChannelEndpoint voice)}"
: "${SIP_USER:?usuário do tronco}"
: "${SIP_PASSWORD:?senha do tronco}"
CONCURRENCY="${CONCURRENCY:-50}"
TALK_MS="${TALK_MS:-90000}"
RAMP_CPS="${RAMP_CPS:-2}"          # chamadas por segundo na rampa
TOTAL_CALLS="${TOTAL_CALLS:-$((CONCURRENCY * 4))}"
MEDIA_PORT="${MEDIA_PORT:-20000}"
OUT_DIR="${OUT_DIR:-./sipp-$(date +%Y%m%d-%H%M%S)}"

cd "$(dirname "$0")"
mkdir -p "$OUT_DIR"
[ -f audio/fala_ptbr_8k_ulaw.wav ] || { echo "falta audio/fala_ptbr_8k_ulaw.wav (ver README)" >&2; exit 2; }

exec sipp "$SIP_TARGET" \
  -sf uac_speech.xml \
  -s "$SIP_NUMBER" -au "$SIP_USER" -ap "$SIP_PASSWORD" \
  -set talk_ms "$TALK_MS" \
  -r "$RAMP_CPS" -l "$CONCURRENCY" -m "$TOTAL_CALLS" \
  -mp "$MEDIA_PORT" -min_rtp_port "$MEDIA_PORT" -max_rtp_port $((MEDIA_PORT + CONCURRENCY * 4 + 100)) \
  -trace_stat -stf "$OUT_DIR/stat.csv" -fd 10 \
  -trace_rtt -rtt_freq 1 \
  -trace_err -error_file "$OUT_DIR/errors.log"
