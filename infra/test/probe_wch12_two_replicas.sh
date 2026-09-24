#!/usr/bin/env bash
# probe_wch12_two_replicas.sh — 2026-09-24  (WCH-12)
#
# PERGUNTA: com DUAS réplicas do channel-gateway, a chamada telefônica continua sendo atendida — a IA
# fala, ouve e encerra — quando a saída do Kafka e o webhook do SFU caem na réplica que NÃO a segura?
#
# O ESTADO QUE O ORIGINOU: a chamada vive na memória de uma réplica (sala, bot leg, fila de fala). O
# `conversations.outbound` é consumido por UM grupo Kafka (3 partições) e o webhook do SFU chega a
# quem o DNS escolher; com N réplicas, a fala caía onde não havia chamada e a IA ficava MUDA, sem erro.
# O demo roda uma réplica só, então nada ficava vermelho. Correção: posse da chamada no Redis e
# encaminhamento à dona por pub/sub (`call_relay.py`).
#
# COMO: sobe uma réplica TEMPORÁRIA (`plughub-demo-channel-gateway-b`, mesma imagem e env do gateway,
# com o alias de rede `channel-gateway` — o SFU passa a dividir os webhooks), roda o
# `probe_voz02_sip_inbound.sh` N vezes (RODADAS, padrão 2) e remove a réplica.
#
# RAMOS:
#   P1 lado do TELEFONE em todas as rodadas: atendida, ouviu a IA, fala chegou ao fluxo, BYE da
#      plataforma, última fala antes do BYE (S1 S3 S4 S5 S5f) — sem FALHA em nenhum
#   P2 houve saída ENCAMINHADA entre réplicas (`outbound`) — senão SEM AMOSTRA (a divisão das
#      partições não exercitou o caminho), INCONCLUSIVO
#   P3 houve webhook ENCAMINHADO (`livekit`) — idem
#   P4 nenhum ERROR do relay nas duas réplicas
#   INFO chamadas abertas por réplica
#   ⚠️ Os ramos do voz02 que leem o log de UM container (K3p, S6, H1, linha RECUSADA) ficam fora do
#   veredicto: a chamada que nasce na réplica B deixa as linhas NELA. São evidência de log, não do
#   telefone; o voz02 com uma réplica continua sendo o gate deles.
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

A="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
B="plughub-demo-channel-gateway-b"
NET="${DEMO_NETWORK:-plughub-demo_plughub-demo}"
RODADAS="${RODADAS:-2}"
FALHA=0; INCONCL=0
ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
info()  { echo "  INFO    $*"; }
fim() {
  if [ "$FALHA" -gt 0 ]; then echo " VERMELHO ($FALHA falha(s), $INCONCL inconclusivo(s))"; exit 1; fi
  if [ "$INCONCL" -gt 0 ]; then echo " INCONCLUSIVO ($INCONCL)"; exit 2; fi
  echo " VERDE"; exit 0
}
limpa() { docker rm -f "$B" >/dev/null 2>&1; }
trap limpa EXIT INT TERM

echo "probe_wch12_two_replicas — a chamada atendida com duas replicas do gateway"
docker inspect "$A" >/dev/null 2>&1 || { incon "gateway $A nao existe"; fim; }
[ "$(docker exec "$A" sh -c 'test -f /app/packages/channel-gateway/src/plughub_channel_gateway/call_relay.py && echo s')" = s ] \
  || { incon "o gateway $A roda imagem SEM call_relay.py — rebuild antes de medir"; fim; }

IMG=$(docker inspect -f '{{.Image}}' "$A")
ENVF=$(mktemp)
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$A" | grep -v '^$' > "$ENVF"
limpa
docker run -d --name "$B" --network "$NET" --network-alias channel-gateway --env-file "$ENVF" "$IMG" >/dev/null \
  || { rm -f "$ENVF"; incon "replica B nao subiu"; fim; }
rm -f "$ENVF"
for _ in $(seq 1 60); do docker logs "$B" 2>&1 | grep -q "call_relay: ouvindo" && break; sleep 1; done
docker logs "$B" 2>&1 | grep -q "call_relay: ouvindo" || { incon "replica B nao ficou pronta"; fim; }
sleep 15   # rebalance do grupo de consumo

T0=$(date -u +%Y-%m-%dT%H:%M:%SZ)
TEL=""
for i in $(seq 1 "$RODADAS"); do
  OUT=$(bash infra/test/probe_voz02_sip_inbound.sh 2>&1)
  TEL="$TEL$(printf '%s\n' "$OUT" | grep -E '^\s+(OK|FALHA|INCONCL)\s+S(1|3|4|5|5f) ')
"
done
LOGA=$(docker logs --since "$T0" "$A" 2>&1)
LOGB=$(docker logs --since "$T0" "$B" 2>&1)

nok=$(printf '%s' "$TEL" | grep -c '^\s*OK ')
nruim=$(printf '%s' "$TEL" | grep -cE '^\s*(FALHA|INCONCL) ')
esperado=$((RODADAS * 5))
if [ "$nruim" -gt 0 ]; then
  falha "P1 lado do telefone: $(printf '%s' "$TEL" | grep -E '^\s*(FALHA|INCONCL) ' | head -3 | tr -s ' ' | tr '\n' ';' | cut -c1-400)"
elif [ "$nok" -lt "$esperado" ]; then
  incon "P1 lado do telefone com $nok de $esperado ramos — o voz02 nao chegou ao fim"
else
  ok "P1 lado do telefone: $nok de $esperado ramos OK em $RODADAS rodada(s) (atendida, ouviu a IA, fala no fluxo, BYE, ultima fala)"
fi

conta() { printf '%s\n' "$1" | grep 'encaminhado a dona' | grep -c "call_relay: $2 de"; }
OUTA=$(conta "$LOGA" outbound); OUTB=$(conta "$LOGB" outbound)
LKA=$(conta "$LOGA" livekit);   LKB=$(conta "$LOGB" livekit)
[ $((OUTA + OUTB)) -gt 0 ] && ok "P2 saida do Kafka encaminhada a dona: A=$OUTA B=$OUTB" \
                            || incon "P2 SEM AMOSTRA — nenhuma saida caiu na replica errada (particoes)"
[ $((LKA + LKB)) -gt 0 ] && ok "P3 webhook do SFU encaminhado a dona: A=$LKA B=$LKB" \
                          || incon "P3 SEM AMOSTRA — nenhum webhook caiu na replica errada"
ERR=$(printf '%s\n%s\n' "$LOGA" "$LOGB" | grep 'call_relay' | grep -c ' ERROR ')
[ "$ERR" = 0 ] && ok "P4 nenhum ERROR do relay nas duas replicas" \
               || falha "P4 $ERR ERROR do relay: $(printf '%s\n%s\n' "$LOGA" "$LOGB" | grep 'call_relay' | grep ' ERROR ' | head -2 | tr '\n' ' ' | cut -c1-300)"
info "chamadas abertas: A=$(printf '%s\n' "$LOGA" | grep -c 'virou contato') B=$(printf '%s\n' "$LOGB" | grep -c 'virou contato')"
fim
