#!/usr/bin/env bash
# probe_identity_operator.sh — 2026-09-13  (IDN-08)
#
# PERGUNTA: o cadastro que o operador faz no Console chega ao cadastro DURAVEL com
#           procedencia `operator` — so por quem atende aquele contato, sem mover ancora
#           de outro cliente e sem descartar em silencio o que foi digitado?
#
# O DEFEITO QUE O ORIGINOU
#   A aba Cliente criava o cadastro pelo `/identity/resolve` com `provision: true`
#   (prospect e indice so no Redis, procedencia nenhuma) e mandava `kind: "telefone"`,
#   que nao e kind: a ancora era DESCARTADA calada e o cliente nascia sem ela, so com
#   o nome gravado por `/attributes`. Sem identificador, caia num `contact_identifier`
#   que tambem nao e kind. O carimbo `operator` nao tinha portador.
#
# TRES RAMOS
#   A  ROTA VIVA — o portao (Bearer, `agent_assist.atender` no pool da sessao, tenant do
#      JWT), a recusa nomeada do kind invalido, o cadastro com procedencia e nome, o
#      `existing` sem duplicar, e a leitura da procedencia pelo resolve.
#   B  EXERCICIO NA IMAGEM — procedencia `operator`, conflito que nao move, controle.
#   C  MUTACOES DO B — procedencia trocada · guarda de conflito desligada.
#
# EXIT: 0 OK · 1 FALHA · 2 INCONCLUSIVO

set -uo pipefail
cd "$(dirname "$0")/../.."

GW="${GW_CONTAINER:-plughub-demo-channel-gateway-1}"
FALHA=0
INCONCL=0

ok()    { echo "  OK      $*"; }
falha() { echo "  FALHA   $*"; FALHA=$((FALHA + 1)); }
incon() { echo "  INCONCL $*"; INCONCL=$((INCONCL + 1)); }
roda()  { docker exec -i "$GW" python - "$@" < infra/test/_identity_operator_exercise.py 2>/dev/null | tail -1; }
expr()  { printf '%s' "$1" | python3 -c 'import json,sys
try:
    d = json.loads(sys.stdin.read())
    print(eval(sys.argv[1]))
except Exception as e:
    print("__ERRO__ %s" % e)' "$2"; }
caso()  { expr "$1" "d.get('casos', {}).get('$2')"; }
julga() {  # $1 json · $2 casos
  if [ -z "$1" ] || [ "$(expr "$1" "'casos' in d")" != "True" ]; then
    incon "o exercicio nao devolveu JSON"; return
  fi
  for k in $2; do
    [ "$(caso "$1" "$k")" = "True" ] && ok "$k" || falha "$k"
  done
}

echo "════════════════════════════════════════════════════════════════════"
echo " o cadastro do operador e duravel, carimbado e so de quem atende?"
echo "════════════════════════════════════════════════════════════════════"

echo ""
echo "── A · ROTA VIVA ──────────────────────────────────────────────────────"
J=$(roda rota)
echo "   $J" | cut -c1-500
julga "$J" "sem_bearer_401 sem_campo_403 escopo_outro_pool_403 sessao_outro_tenant_404 kind_invalido_422 cria_200_operator existente_200 resolve_le_operator"

echo ""
echo "── B · EXERCICIO NA IMAGEM ────────────────────────────────────────────"
J=$(roda exercicio)
echo "   $J" | cut -c1-400
julga "$J" "cria_operator conflito_nao_move controle_existente"

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
muta --mutar-procedencia "cria_operator" "conflito_nao_move controle_existente"
# sem a guarda o cadastro MOVE o e-mail do outro cliente, e o controle seguinte deixa
# de ter base: so o caso anterior e cobrado
muta --mutar-conflito "conflito_nao_move" "cria_operator"

echo ""
echo "════════════════════════════════════════════════════════════════════"
if [ "$FALHA" -gt 0 ]; then
  echo " FALHA ($FALHA)"; exit 1
elif [ "$INCONCL" -gt 0 ]; then
  echo " INCONCLUSIVO ($INCONCL)"; exit 2
fi
echo " OK"
exit 0
