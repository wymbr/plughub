#!/usr/bin/env bash
# probe_att08_agent_attachment.sh — ATT-08: o atendente manda arquivo ao cliente pelo Console.
#
# PROPOSIÇÃO: só quem ATENDE a sessão manda; o arquivo passa pela MESMA esteira do cliente (o
# commit do gateway recusa o que não é o que declara); a mensagem vai ao stream como `document`
# com o indicador, e o cliente do webchat a recebe como `msg.document` com URL assinada que
# devolve os mesmos bytes. Medido pela borda do platform-ui (o caminho do browser), com controle
# negativo (grant sem atender → 403) ao lado do positivo.
#
# Corpo em `_att08_agent_attachment.py` (roda dentro do gateway). Ramos: A0 N1 A1 A2 A3 A4 N2.
# Saída: 0 VERDE · 1 VERMELHO · 2 INCONCLUSIVO. Rodar de DENTRO do WSL.
set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
POOL="${POOL:-demo_ia}"   # pool webchat qualquer: o atendimento humano é a fixture `human_agents`

docker inspect "$GW" >/dev/null 2>&1 || { echo "  ? $GW fora do ar"; echo "VEREDICTO: INCONCLUSIVO"; exit 2; }
echo "══ probe_att08_agent_attachment — o atendente manda arquivo ao cliente ══"
docker exec -i -e POOL="$POOL" "$GW" python - < infra/test/_att08_agent_attachment.py
rc=$?
echo
case $rc in
  0) echo "VEREDICTO: VERDE" ;;
  2) echo "VEREDICTO: INCONCLUSIVO" ;;
  *) echo "VEREDICTO: VERMELHO"; rc=1 ;;
esac
exit $rc
