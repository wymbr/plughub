#!/usr/bin/env bash
# probe_wch01_chat_call.sh — WCH-01: a chamada que se PRENDE a um contato de chat.
#
# PROPOSIÇÃO: a conexão `/ws/call` só prende chamada ao contato `webchat` aberto do PRÓPRIO
# cliente; sem atendente que ofereça mídia a espera é DITA e nenhuma sala nasce; desligar a
# chamada NÃO encerra o contato (o chat segue entregando); contato encerrado recusa chamada.
#
# RAMOS (corpo em `_wch01_chat_call.py`, rodado dentro do gateway):
#   A1 cliente alheio → session_not_found · A2 sessão inexistente → session_not_found
#   B1 chamada presa (controle positivo da porta) · B2 só IA atende → webrtc.call_pending
#   B3 nenhuma sala criada · B4 segunda chamada → call_already_active
#   C1 contato NÃO fecha com o fim da chamada · C2 mensagem depois da chamada chega ao stream
#   C3 sem `media.call` (a chamada nunca montou) · D1 contato encerrado → contact_closed
#
# O que NÃO mede: o caminho com humano e áudio (pede gente e microfone — validado no browser).
# Saída: 0 VERDE · 1 VERMELHO · 2 INCONCLUSIVO. Rode de dentro do WSL.
set -u
cd "$(dirname "$0")/../.."
GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
POOL="${WCH01_POOL:-demo_ia}"
echo "════════════════════════════════════════════════════════════════════"
echo " a chamada se prende ao contato de chat, e cair nao encerra o contato?"
echo "════════════════════════════════════════════════════════════════════"
if [ "$(docker inspect -f '{{.State.Running}}' "$GW" 2>/dev/null)" != true ]; then
  echo "  INCONCL gateway ($GW) fora do ar — nada medido"; exit 2
fi
if [ "$(docker exec "$GW" grep -c handle_call_ws /app/packages/channel-gateway/src/plughub_channel_gateway/main.py 2>/dev/null)" = "0" ]; then
  echo "  FALHA   a IMAGEM do gateway nao tem a rota /ws/call — build + up -d"; exit 1
fi
docker cp infra/test/_wch01_chat_call.py "$GW":/tmp/_wch01_chat_call.py >/dev/null || {
  echo "  INCONCL nao copiou o corpo do probe"; exit 2; }
docker exec -e POOL="$POOL" "$GW" python /tmp/_wch01_chat_call.py
rc=$?
echo "────────────────────────────────────────────────────────────────────"
case $rc in 0) echo " VERDE";; 1) echo " VERMELHO";; *) echo " INCONCLUSIVO";; esac
exit $rc
