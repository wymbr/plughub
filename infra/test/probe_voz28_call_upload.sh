#!/usr/bin/env bash
# probe_voz28_call_upload.sh — VOZ-28: o cliente sobe arquivo DURANTE a chamada do chat.
#
# PROPOSIÇÃO: com a chamada presa ao contato de chat (WCH-01) de pé, o caminho de anexos do chat
# funciona inteiro — reserva, binário, confirmação, mensagem no stream, arquivo servido — e a
# chamada não cai por isso. A chamada é MEIO do contato de chat; o anexo é do chat.
#
# RAMOS (corpo em `_voz28_call_upload.py`, rodado dentro do gateway):
#   P1 o preflight CORS do NAVEGADOR é aceito (WCH-16; de fora do container, como o browser)
#   C0 a chamada abriu (`webrtc.ready`) — sem ela, INCONCLUSIVO
#   U1 upload.ready · U2 POST 204 + upload.committed no chat · U3 msg.document no stream
#   U4 o arquivo servido devolve os mesmos bytes · U5 a chamada segue de pé depois
#   N1 controle: PDF declarado com bytes que não são PDF → 415
#
# O que NÃO mede: o canal `webrtc` AVULSO (widget de chamada sem chat) — ele tem caminho de
# texto próprio até a WCH-04, e o anexo nele fica com ela.
#
# PREMISSA CONFERIDA: o pool é de IA COM `media_policy` de áudio (desde a WCH-09 a IA atende a
# chamada do chat pelo bot leg). Sem política, a chamada fica em espera e C0 não abre.
# Saída: 0 VERDE · 1 VERMELHO · 2 INCONCLUSIVO. Rode de dentro do WSL.
set -u
cd "$(dirname "$0")/../.."
GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
POOL="${VOZ28_POOL:-demo_ia}"
echo "════════════════════════════════════════════════════════════════════"
echo " o cliente sobe arquivo durante a chamada do chat, e a chamada segue?"
echo "════════════════════════════════════════════════════════════════════"
if [ "$(docker inspect -f '{{.State.Running}}' "$GW" 2>/dev/null)" != true ]; then
  echo "  INCONCL gateway ($GW) fora do ar — nada medido"; exit 2
fi
POLITICA=$(curl -s -H "x-tenant-id: ${TENANT:-tenant_demo}" "${AGENT_REGISTRY_URL:-http://localhost:3300}/v1/pools/$POOL" \
  | python3 -c 'import json,sys
try: d=json.load(sys.stdin)
except Exception: print("ilegivel"); raise SystemExit
p=d.get("media_policy") or {}
print("ausente" if "pool_id" not in d else ("audio" if "audio" in (p.get("customer_publish") or []) else "sem"))')
case "$POLITICA" in
  audio) echo "  premissa: pool $POOL de IA com politica de audio" ;;
  *)     echo "  INCONCL premissa falsa: o pool $POOL nao oferece audio ($POLITICA) — escolha outro (VOZ28_POOL)"; exit 2 ;;
esac
# P1 (WCH-16) — o NAVEGADOR sobe o binário de outra origem: sem resposta ao preflight, todo upload
# do widget morria em "Failed to fetch". O corpo abaixo roda dentro do container, onde CORS não
# existe — por isso este ramo é de FORA, como o navegador faz.
PRE=$(curl -s -o /dev/null -D - -X OPTIONS "${GW_URL:-http://localhost:8010}/webchat/v1/upload/x" \
  -H "Origin: http://site-do-cliente.example" -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type")
if printf '%s' "$PRE" | grep -qi '^access-control-allow-origin:' && printf '%s' "$PRE" | grep -qiE '^HTTP/[0-9.]+ 2'; then
  echo "  OK     P1 o preflight do navegador e aceito (CORS no upload)"
  P1=0
else
  echo "  FALHA  P1 o preflight do navegador e recusado: $(printf '%s' "$PRE" | head -1 | tr -d '\r')"
  P1=1
fi
docker cp infra/test/_voz28_call_upload.py "$GW":/tmp/_voz28_call_upload.py >/dev/null || {
  echo "  INCONCL nao copiou o corpo do probe"; exit 2; }
docker exec -e POOL="$POOL" "$GW" python /tmp/_voz28_call_upload.py
rc=$?
[ "$rc" = 0 ] && [ "$P1" = 1 ] && rc=1
echo "────────────────────────────────────────────────────────────────────"
case $rc in 0) echo " VERDE";; 1) echo " VERMELHO";; *) echo " INCONCLUSIVO";; esac
exit $rc
