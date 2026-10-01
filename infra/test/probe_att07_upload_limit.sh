#!/usr/bin/env bash
# probe_att07_upload_limit.sh — ATT-07: o teto de upload que a tela de WebChat edita VALE.
#
# PROPOSIÇÃO: `webchat.upload_limits_mb` (config-api) chega ao gateway EM EXECUÇÃO (config.changed)
# e passa a decidir o upload do webchat — no tamanho DECLARADO (reserve) e no REAL (POST) —, por
# tipo. Antes nenhum código o lia: mudar na tela não mudava nada.
#
# Grava `image = 1` (MB) pela API, mantendo pdf/video e a DESCRIÇÃO da chave (`_config_put.py`),
# espera o reload e mede pelo caminho do cliente (corpo em `_att07_upload_limit.py`):
#   C1 controle: imagem pequena reserva · R1 imagem declarada com 2 MB recusada · R2 corpo real de
#   2 MB num slot pequeno → 413 · C2 controle: corpo pequeno → 204 · P1 PDF de 2 MB reserva
#   L1 o reload do namespace webchat chegou ao gateway
# Restaura o valor original no fim, mesmo em falha.
# Saída: 0 VERDE · 1 VERMELHO · 2 INCONCLUSIVO. Rode de dentro do WSL.
set -uo pipefail
cd "$(dirname "$0")/../.."
GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
CFG="${CFG_CONTAINER:-plughub-demo-config-api-1}"
POOL="${ATT07_POOL:-demo_ia}"
FAIL=0; INC=0; ORIG=""
ok()    { echo "  ✓ $1"; }
bad()   { echo "  ✗ $1"; FAIL=1; }
incon() { echo "  ? $1"; INC=1; }
ADMIN=$(docker exec "$CFG" printenv PLUGHUB_CONFIG_ADMIN_TOKEN 2>/dev/null)
put() { docker exec -i -e NS=webchat -e KEY=upload_limits_mb -e VALUE="$1" -e ADMIN="$ADMIN" "$GW" python - \
          < infra/test/_config_put.py 2>/dev/null | tail -1; }
fim() {
  [ -n "$ORIG" ] && put "$ORIG" >/dev/null
  echo
  if [ $FAIL -ne 0 ]; then echo "VEREDICTO: VERMELHO"; exit 1; fi
  if [ $INC -ne 0 ]; then echo "VEREDICTO: INCONCLUSIVO"; exit 2; fi
  echo "VEREDICTO: VERDE"; exit 0
}
command -v jq >/dev/null || { incon "jq ausente — rode de dentro do WSL"; fim; }
docker inspect "$GW" >/dev/null 2>&1 || { incon "$GW fora do ar"; fim; }
[ -n "$ADMIN" ] || { incon "sem PLUGHUB_CONFIG_ADMIN_TOKEN no config-api"; fim; }

echo "══ probe_att07_upload_limit — o teto de upload da tela de WebChat vale ══"
ATUAL=$(docker exec "$GW" python -c "import urllib.request,json;print(json.dumps(json.loads(urllib.request.urlopen('http://config-api:3600/config/webchat/upload_limits_mb?tenant_id=tenant_demo',timeout=5).read())['value']))" 2>/dev/null)
printf '%s' "$ATUAL" | jq -e '.image and .pdf and .video' >/dev/null 2>&1 || { incon "valor atual ilegível: $ATUAL"; fim; }
NOVO=$(printf '%s' "$ATUAL" | jq -c '.image = 1')
T0=$(date -u +%Y-%m-%dT%H:%M:%SZ)
ST=$(put "$NOVO" | jq -r .status 2>/dev/null)
[ "$ST" = 200 ] || { incon "PUT respondeu $ST"; fim; }
ORIG="$ATUAL"
ACHOU=0
for _ in $(seq 1 20); do
  docker logs --since "$T0" "$GW" 2>&1 | grep -q "config.changed: webchat namespace reloaded" && { ACHOU=1; break; }
  sleep 1
done
[ $ACHOU = 1 ] && ok "L1 o gateway em execução recarregou o namespace webchat ($NOVO)" \
  || { bad "L1 o PUT não chegou ao gateway em 20 s"; fim; }

docker cp infra/test/_att07_upload_limit.py "$GW":/tmp/_att07_upload_limit.py >/dev/null || { incon "nao copiou o corpo"; fim; }
docker exec -e POOL="$POOL" "$GW" python /tmp/_att07_upload_limit.py
case $? in 0) ;; 1) FAIL=1 ;; *) INC=1 ;; esac
fim
