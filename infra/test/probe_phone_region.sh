#!/usr/bin/env bash
# probe_phone_region.sh — 2026-09-13  (IDN-14)
#
# PERGUNTA: o mesmo celular digitado com e sem codigo do pais identifica o MESMO
#           cliente — com o pais vindo da config do tenant, e sem adivinhar?
#
# O DEFEITO QUE O ORIGINOU
#   `normalize_anchor` fazia `"+" + digitos`: `11 99999-0001` virava `+11999990001` e
#   `+55 11 99999-0001` virava `+5511999990001` — dois hashes, dois clientes. O
#   step-up do limite (PID-10) pede o celular cadastrado, e quem omitisse o `55` tinha o
#   desafio recusado. E o `contact_identifier` do webchat (`cli_52989317358`) virava
#   ancora phone com os digitos do id de contato.
#
# QUATRO RAMOS
#   A  CONFIG — o resolvedor de producao le `identity.default_phone_region` do tenant, e
#      o valor e uma regiao conhecida. Sem ela, telefone sem DDI e recusado: FALHA.
#   B  EXERCICIO NA IMAGEM — formas nacional / DDI sem `+` / internacional casam o mesmo
#      cliente (quente e frio), sem pais recusa, `cli_…` nao vira ancora, e a rota do
#      processo de pe resolve o numero nacional. Cada lado com o seu controle.
#   C  MUTACOES DO B — normalizacao antiga · letras aceitas.
#   D  INVALIDACAO AO VIVO — override `PT` no config-api faz a rota parar de achar o
#      cliente sem boot; tirar o override o traz de volta. O override sai num `finally`.
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
CFG_ADMIN_TOKEN="${CFG_ADMIN_TOKEN:-demo_config_admin_token}"
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
roda()  { docker exec -i -e CFG_ADMIN_TOKEN="$CFG_ADMIN_TOKEN" "$GW" python - "$@" < infra/test/_phone_region_exercise.py 2>/dev/null | tail -1; }
expr()  { printf '%s' "$1" | python3 -c 'import json,sys
try:
    d = json.loads(sys.stdin.read())
    print(eval(sys.argv[1]))
except Exception as e:
    print("__ERRO__ %s" % e)' "$2"; }
caso()  { expr "$1" "d.get('casos', {}).get('$2')"; }

echo "════════════════════════════════════════════════════════════════════"
echo " o mesmo celular, com e sem DDI, e o mesmo cliente?"
echo "════════════════════════════════════════════════════════════════════"

echo ""
echo "── A · CONFIG ─────────────────────────────────────────────────────────"
J=$(roda config)
echo "   $J"
R=$(expr "$J" "d['regiao']")
if [ -z "$J" ] || [ "${R#__ERRO__}" != "$R" ]; then
  incon "o exercicio nao devolveu JSON"
elif [ "$R" = "None" ]; then
  falha "identity.default_phone_region nao configurado (ou invalido) para o tenant — telefone sem DDI e recusado"
else
  ok "pais padrao do tenant: $R"
fi

echo ""
echo "── B · EXERCICIO NA IMAGEM ────────────────────────────────────────────"
CASOS="internacional_casa nacional_casa_quente ddi_sem_mais_casa nacional_casa_frio sem_regiao_recusa cli_nao_vira_ancora cli_controle rota_viva_nacional"
J=$(roda exercicio)
echo "   $J" | cut -c1-400
if [ -z "$J" ] || [ "$(expr "$J" "'casos' in d")" != "True" ]; then
  incon "o exercicio nao devolveu JSON"
else
  for k in $CASOS; do
    [ "$(caso "$J" "$k")" = "True" ] && ok "$k" || falha "$k"
  done
fi

echo ""
echo "── C · MUTACOES DO B ──────────────────────────────────────────────────"
muta() {  # $1 flag · $2 casos que TEM de cair · $3 controles que TEM de ficar
  local J; J=$(roda exercicio "$1")
  [ -z "$J" ] && { incon "$1: sem JSON"; return; }
  for k in $2; do
    [ "$(caso "$J" "$k")" = "False" ] && ok "$1 derruba $k" || falha "$1 NAO derrubou $k — o caso nao mede a regra"
  done
  for k in $3; do
    [ "$(caso "$J" "$k")" = "True" ] && ok "$1 mantem o controle $k" || falha "$1 derrubou o controle $k"
  done
}
# a normalizacao antiga tambem aceitava letra — por isso o caso do `cli_` cai junto.
# `ddi_sem_mais_casa` NAO cai: com o `55` digitado, `"+" + digitos` ja acertava.
# `rota_viva_nacional` fica: a mutacao e do processo do exercicio, nao do gateway.
muta --mutar-normalizacao "nacional_casa_quente nacional_casa_frio cli_nao_vira_ancora" "internacional_casa cli_controle rota_viva_nacional"
muta --mutar-letras "cli_nao_vira_ancora" "internacional_casa nacional_casa_quente cli_controle"

echo ""
echo "── D · INVALIDACAO AO VIVO ────────────────────────────────────────────"
J=$(roda invalidacao)
echo "   $J"
if [ -z "$J" ] || [ "$(expr "$J" "'casos' in d")" != "True" ]; then
  incon "o exercicio nao devolveu JSON — CONFIRA o override: curl -X DELETE 'localhost:3600/config/identity/default_phone_region?tenant_id=tenant_demo'"
else
  for k in troca_de_pais_vale_sem_boot volta_ao_default; do
    [ "$(caso "$J" "$k")" = "True" ] && ok "$k" || falha "$k"
  done
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then
  echo " FALHA ($FALHA)"; exit 1
elif [ "$INCONCL" -gt 0 ]; then
  echo " INCONCLUSIVO ($INCONCL)"; exit 2
fi
echo " OK"
exit 0
