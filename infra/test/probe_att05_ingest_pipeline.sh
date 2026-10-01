#!/usr/bin/env bash
# probe_att05_ingest_pipeline.sh — ATT-05: nenhum anexo de contato é gravado sem a esteira, e
# nenhum é servido sem o antivírus ter dito `clean`.
#
# PROPOSIÇÃO: imagem re-codificada (sai EXIF e carga poliglota), sha256 do gravado, antivírus
# (clamd do compose) com TRÊS desfechos — limpo serve, infectado não grava, inalcançável grava em
# QUARENTENA e só a nova varredura libera. Antes: EXIF com GPS gravado e servido, sem hash, sem
# antivírus, e a porta servia todo arquivo commitado.
#
# RAMOS (corpo em `_att05_ingest_pipeline.py`, rodado dentro do gateway com store e clamd REAIS):
#   A0 controle: antivírus configurado e respondendo · W0 controle: PDF limpo gravado e servido
#   V1 PDF com EICAR recusado · X1 EXIF fora (e orientação aplicada) · X2 carga poliglota fora
#   Q1 quarentena com o clamd fora (423 nas duas portas) · Q2 a varredura libera · C1 censo
#
# Sem A0 e W0, INCONCLUSIVO — as recusas não provariam nada.
# O que NÃO mede: o Console mostrando "em verificação" (a analytics-api repassa o 423; suíte
# `test_att02_attachment_door.py`) e os escritores WhatsApp/e-mail ponta a ponta.
# Saída: 0 VERDE · 1 VERMELHO · 2 INCONCLUSIVO. Rode de dentro do WSL.
set -u
cd "$(dirname "$0")/../.."
GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
echo "════════════════════════════════════════════════════════════════════"
echo " anexo: esteira de ingestão (re-codifica, hash, antivírus, quarentena)?"
echo "════════════════════════════════════════════════════════════════════"
if [ "$(docker inspect -f '{{.State.Running}}' "$GW" 2>/dev/null)" != true ]; then
  echo "  INCONCL gateway ($GW) fora do ar — nada medido"; exit 2
fi
if [ "$(docker exec "$GW" sh -c 'grep -c "def prepare_content" "$(python -c "import plughub_channel_gateway.attachment_store as m;print(m.__file__)")"' 2>/dev/null)" != 1 ]; then
  echo "  VERMELHO a imagem do gateway não tem a esteira da ATT-05 (rebuild?)"; exit 1
fi
docker cp infra/test/_att05_ingest_pipeline.py "$GW":/tmp/_att05_ingest_pipeline.py >/dev/null || {
  echo "  INCONCL nao copiou o corpo do probe"; exit 2; }
docker exec "$GW" python /tmp/_att05_ingest_pipeline.py
rc=$?
echo "────────────────────────────────────────────────────────────────────"
case $rc in 0) echo " VERDE";; 1) echo " VERMELHO";; *) echo " INCONCLUSIVO";; esac
exit $rc
