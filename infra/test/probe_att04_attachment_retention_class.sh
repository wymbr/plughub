#!/usr/bin/env bash
# probe_att04_attachment_retention_class.sh — ATT-04: o prazo do anexo é a classe
# `retention.attachment_days`, e só ela.
#
# PROPOSIÇÃO: o config-api tem a classe, a chave antiga `webchat.attachment_expiry_days` não existe
# mais (ficaria editável na tela sem efeito), o gateway resolve o prazo a partir de `retention`, e
# uma mudança feita pela API chega ao serviço EM EXECUÇÃO pelo `config.changed`.
#
# RAMOS
#   C1 `retention.attachment_days` existe no config-api
#   C2 `webchat.attachment_expiry_days` não existe
#   R1 `resolve_attachment_expiry_days` devolve o valor da classe (leitor real, processo novo)
#   P1 PUT global → o gateway em execução registra "retention namespace reloaded" depois do PUT
#      (o valor ORIGINAL é restaurado no fim, sempre)
#   R2 com o valor fora do default, o leitor devolve o valor da classe (o R1 sozinho não reprova:
#      semeado = default = 30)
#
# Saída: 0 VERDE · 1 VERMELHO · 2 INCONCLUSIVO. Rodar de DENTRO do WSL (jq).
set -uo pipefail
cd "$(dirname "$0")/../.."
GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
CFG="${CFG_CONTAINER:-plughub-demo-config-api-1}"
FAIL=0; INC=0; ORIG=""
ok()    { echo "  ✓ $1"; }
bad()   { echo "  ✗ $1"; FAIL=1; }
incon() { echo "  ? $1"; INC=1; }
ADMIN=$(docker exec "$CFG" printenv PLUGHUB_CONFIG_ADMIN_TOKEN 2>/dev/null)
# ATT-07: a escrita mantém a descrição gravada — a versão anterior a apagava a cada rodada
put() { docker exec -i -e NS=retention -e KEY=attachment_days -e VALUE="$1" -e ADMIN="$ADMIN" "$GW" python - < infra/test/_config_put.py 2>/dev/null | tail -1; }
fim() {
  [ -n "$ORIG" ] && put "$ORIG" >/dev/null
  echo
  if [ $FAIL -ne 0 ]; then echo "VEREDICTO: VERMELHO"; exit 1; fi
  if [ $INC -ne 0 ]; then echo "VEREDICTO: INCONCLUSIVO"; exit 2; fi
  echo "VEREDICTO: VERDE"; exit 0
}
command -v jq >/dev/null || { incon "jq ausente — rode de dentro do WSL"; fim; }
docker inspect "$GW" >/dev/null 2>&1 || { incon "$GW fora do ar"; fim; }

echo "══ probe_att04_attachment_retention_class — o prazo do anexo é classe de retenção ══"
R=$(docker exec -i -e MODE=read "$GW" python - < infra/test/_att04_retention.py 2>/dev/null | tail -1)
printf '%s' "$R" | jq -e . >/dev/null 2>&1 || { incon "leitura falhou: $R"; fim; }
[ "$(printf '%s' "$R" | jq -r .c1)" = true ] && ok "C1 retention.attachment_days = $(printf '%s' "$R" | jq -r .seeded)" \
  || bad "C1 retention.attachment_days AUSENTE no config-api (semente não rodou?)"
[ "$(printf '%s' "$R" | jq -r .c2)" = true ] && ok "C2 webchat.attachment_expiry_days não existe" \
  || bad "C2 a chave antiga ainda existe — editável na tela sem efeito"
[ "$(printf '%s' "$R" | jq -r .r1)" = true ] && ok "R1 o gateway resolve $(printf '%s' "$R" | jq -r .resolved) a partir de retention" \
  || bad "R1 o gateway resolveu $(printf '%s' "$R" | jq -r .resolved), a classe diz $(printf '%s' "$R" | jq -r .seeded)"

ORIG=$(printf '%s' "$R" | jq -r '.seeded // empty')
if [ -z "$ORIG" ] || [ -z "$ADMIN" ]; then incon "P1 sem valor original ou sem PLUGHUB_CONFIG_ADMIN_TOKEN — propagação não medida"; ORIG=""; fim; fi
T0=$(date -u +%Y-%m-%dT%H:%M:%SZ)
ST=$(put "$((ORIG + 1))" | jq -r .status 2>/dev/null)
[ "$ST" = 200 ] || { incon "P1 PUT respondeu $ST"; fim; }
ACHOU=0
for _ in $(seq 1 20); do
  docker logs --since "$T0" "$GW" 2>&1 | grep -q "config.changed: retention namespace reloaded" && { ACHOU=1; break; }
  sleep 1
done
[ $ACHOU = 1 ] && ok "P1 o gateway em execução recarregou a classe depois do PUT" \
  || bad "P1 o PUT não chegou ao gateway em 20 s (config.changed não ligado ao namespace retention?)"
# R2 — o R1 sozinho não reprova: o valor semeado (30) é igual ao default do código. Com um valor
# DIFERENTE do default, um leitor que ignorasse a classe devolveria 30 e ficaria vermelho aqui.
R2=$(docker exec -i -e MODE=read "$GW" python - < infra/test/_att04_retention.py 2>/dev/null | tail -1)
if [ "$(printf '%s' "$R2" | jq -r .resolved)" = "$((ORIG + 1))" ]; then
  ok "R2 com valor fora do default ($((ORIG + 1))) o gateway resolve o valor da classe"
else
  bad "R2 o gateway resolveu $(printf '%s' "$R2" | jq -r .resolved), a classe diz $((ORIG + 1))"
fi
fim
