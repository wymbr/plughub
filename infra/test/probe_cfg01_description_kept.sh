#!/usr/bin/env bash
# probe_cfg01_description_kept.sh — CFG-01: salvar uma chave sem descrição NÃO apaga a gravada.
#
# PROPOSIÇÃO: o `PUT /config/{ns}/{key}` com `description: ""` (o que a tela manda em toda
# gravação, e nenhuma rota de leitura devolve a descrição para ela reenviar) mantém a descrição
# gravada e muda o valor; com descrição não vazia, troca. Antes, apagava: 5 de 93 chaves medidas.
#
# Mede contra o Postgres REAL do config-api (a suíte do pacote usa pool de mentira), numa chave de
# rascunho GLOBAL que o probe cria e apaga. Corpo em `_cfg01_description.py`:
#   K1 vazia mantém · V1 o valor muda · C1 controle: descrição nova troca · L1 rascunho apagado
#   Z1 censo das chaves sem descrição (informativo)
# Saída: 0 VERDE · 1 VERMELHO · 2 INCONCLUSIVO. Rode de dentro do WSL.
set -u
cd "$(dirname "$0")/../.."
GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
CFG="${CFG_CONTAINER:-plughub-demo-config-api-1}"
echo "════════════════════════════════════════════════════════════════════"
echo " config: salvar sem descrição mantém a descrição gravada?"
echo "════════════════════════════════════════════════════════════════════"
for c in "$GW" "$CFG"; do
  [ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" = true ] || { echo "  INCONCL $c fora do ar"; exit 2; }
done
ADMIN=$(docker exec "$CFG" printenv PLUGHUB_CONFIG_ADMIN_TOKEN 2>/dev/null)
[ -n "$ADMIN" ] || { echo "  INCONCL sem PLUGHUB_CONFIG_ADMIN_TOKEN no config-api"; exit 2; }
docker cp infra/test/_cfg01_description.py "$GW":/tmp/_cfg01_description.py >/dev/null || { echo "  INCONCL nao copiou o corpo"; exit 2; }
docker exec -e ADMIN="$ADMIN" "$GW" python /tmp/_cfg01_description.py
rc=$?
echo "────────────────────────────────────────────────────────────────────"
case $rc in 0) echo " VERDE";; 1) echo " VERMELHO";; *) echo " INCONCLUSIVO";; esac
exit $rc
