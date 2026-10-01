#!/usr/bin/env bash
# probe_att01_attachment_door.sh — ATT-01: o tipo que o REMETENTE escolhe não é gravado, e a
# porta pública de anexos não serve nada como página.
#
# PROPOSIÇÃO: todo commit (webchat, WhatsApp, e-mail, gravação) confere allowlist da classe,
# tamanho real e assinatura; a porta pública manda `nosniff` + CSP `sandbox` e só exibe imagem
# inline. Antes: WhatsApp e e-mail gravavam o MIME do remetente, o magic byte era fail-open, e um
# `text/html` saía renderizável na origem do gateway.
#
# RAMOS (corpo em `_att01_attachment_door.py`, rodado dentro do gateway com o store REAL):
#   W0 controle: imagem gravada · W1 text/html recusado · W2 JPEG com bytes de HTML recusado
#   W3 controle: PDF gravado · S1 imagem inline + nosniff + sandbox · S2 PDF como download
#   S3 nome hostil não injeta cabeçalho · C1 censo: nenhuma gravada fora da allowlist da classe
#
# A amostra é do próprio probe (não depende de tráfego) e sai ao final. Sem o controle W0,
# INCONCLUSIVO — as recusas não provariam nada.
# O que NÃO mede: os adapters de WhatsApp e e-mail ponta a ponta (conferem antes do reserve; a
# suíte `test_att01_attachment_content.py` cobre) — aqui é a regra no store e a porta.
# Saída: 0 VERDE · 1 VERMELHO · 2 INCONCLUSIVO. Rode de dentro do WSL.
set -u
cd "$(dirname "$0")/../.."
GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
echo "════════════════════════════════════════════════════════════════════"
echo " anexo: o tipo do remetente é recusado, e a porta não serve página?"
echo "════════════════════════════════════════════════════════════════════"
if [ "$(docker inspect -f '{{.State.Running}}' "$GW" 2>/dev/null)" != true ]; then
  echo "  INCONCL gateway ($GW) fora do ar — nada medido"; exit 2
fi
if [ "$(docker exec "$GW" sh -c 'grep -c "def validate_content" "$(python -c "import plughub_channel_gateway.attachment_store as m;print(m.__file__)")"' 2>/dev/null)" != 1 ]; then
  echo "  VERMELHO a imagem do gateway não tem a regra da ATT-01 (rebuild?)"; exit 1
fi
docker cp infra/test/_att01_attachment_door.py "$GW":/tmp/_att01_attachment_door.py >/dev/null || {
  echo "  INCONCL nao copiou o corpo do probe"; exit 2; }
docker exec "$GW" python /tmp/_att01_attachment_door.py
rc=$?
echo "────────────────────────────────────────────────────────────────────"
case $rc in 0) echo " VERDE";; 1) echo " VERMELHO";; *) echo " INCONCLUSIVO";; esac
exit $rc
