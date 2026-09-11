#!/usr/bin/env bash
# ==============================================================================
# gate_session_state_pool_scope.sh — o ESTADO da sessao recorta por pool
# ==============================================================================
#
# O QUE ELE PROVA
# ---------------
#   N1  quem NAO alcanca o pool da sessao nao le o estado dela .......... 403
#   N2  ... e isso vale para o supervisor, que era quem levava junto a
#       CREDENCIAL da tarefa (`core.workflow.delegate_resume_token`) ..... 403
#   P1  quem ALCANCA o pool continua lendo tudo, token incluido ......... 200
#   P2  um chamador NAO-admin le a sessao de um pool que ele alcanca ..... 200
#       ... E recebe o PACOTE do formulario (form_id + token) ........... CNS-24
#   N3  sessao inexistente responde 404, nunca 403 ...................... 404
#   N4  tenant do token x tenant da sessao — DECLARADO nao-exercivel aqui
#
# ⚠️ **P1 e P2 sao o que impede o verde errado.** Um portao que negasse tudo deixaria
# N1/N2 verdes com o Console quebrado; e um portao que so deixasse o admin passar
# deixaria P1 verde com o operador de verdade trancado do lado de fora. N3 separa as
# duas ausencias: *"expirou"* nao pode chegar ao operador como *"voce nao pode"*.
#
# POR QUE ELE EXISTE (AUT-47, 2026-09-09)
# ---------------------------------------
# Medido ao vivo, com tarefa de promocao de deploy reivindicada:
#
#   admin@      (alcanca aprovacao_deploy) -> 200, corpo COM o resume_token
#   supervisor@ (NAO alcanca) ............ -> 200, corpo COM o resume_token
#   operator@   (NAO alcanca) ............ -> 200, sem o token
#
# O unico filtro em jogo era MASCARAMENTO POR PAPEL. Nao havia eixo de POOL — enquanto
# a `analytics-api` recorta o conteudo do MESMO contato por pool desde 2026-08-30.
# Duas portas para o mesmo dado, e so uma trancada; a destrancada entregava a
# credencial da tarefa a um supervisor de outro time.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail
cd "$(dirname "$0")/../.." || { echo "INCONCLUSIVO: raiz do repo nao encontrada"; exit 2; }

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="${TENANT:-tenant_demo}"
CG="${CG:-http://localhost:8010}"
MCP="${MCP:-http://localhost:3100}"
AUTH="${AUTH:-http://localhost:3202}"
RE_URL="${RE_URL:-http://localhost:3550}"
CURL="curl -s --max-time 20"
JSON='-H Content-Type:application/json'

FALHOU=0
ok()  { printf '  \033[32mOK\033[0m           %s\n' "$1"; }
bad() { printf '  \033[31mFALHA\033[0m        %s\n' "$1"; FALHOU=1; }
inc() { printf '  \033[33mINCONCLUSIVO\033[0m %s\n' "$1"; exit 2; }
sec() { printf '\n\033[1m%s\033[0m\n' "$1"; }
redis() { $COMPOSE exec -T redis redis-cli "$@" < /dev/null; }

command -v jq >/dev/null || inc "jq ausente"

login() { $CURL -X POST "$AUTH/auth/login" $JSON \
  -d "{\"email\":\"$1\",\"password\":\"$2\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty'; }
sub_de() { printf '%s' "$1" | cut -d. -f2 | tr '_-' '/+' \
  | { read -r p; printf '%s' "$p$(printf '%*s' $(( (4 - ${#p} % 4) % 4 )) '' | tr ' ' '=')"; } \
  | base64 -d 2>/dev/null | jq -r '.sub // empty'; }
pools_de() { printf '%s' "$1" | cut -d. -f2 | tr '_-' '/+' \
  | { read -r p; printf '%s' "$p$(printf '%*s' $(( (4 - ${#p} % 4) % 4 )) '' | tr ' ' '=')"; } \
  | base64 -d 2>/dev/null | jq -r '(.accessible_pools // []) | join(",")'; }

T_ADM="$(login admin@plughub.local changeme_admin)";            [ -n "$T_ADM" ] || inc "login admin"
T_OPE="$(login operator@plughub.local changeme_operator)";      [ -n "$T_OPE" ] || inc "login operator"
T_SUP="$(login supervisor@plughub.local changeme_supervisor)";  [ -n "$T_SUP" ] || inc "login supervisor"
INST="human-$(sub_de "$T_ADM")"

P_OPE="$(pools_de "$T_OPE")"
case ",$P_OPE," in *,aprovacao_deploy,*) inc "o operator ALCANCA aprovacao_deploy — N1 nao teria o que negar";; esac
case ",$P_OPE," in *,formfill_demo,*) : ;; *) inc "o operator NAO alcanca formfill_demo — P2 nao teria o que provar";; esac

# ── cria uma tarefa num pool pull e a reivindica; ecoa o session_id ──────────
cria() {   # $1=pool webhook  $2=pool pull
  redis SET "${TENANT}:instance:${INST}" \
    "{\"instance_id\":\"$INST\",\"agent_type_id\":\"human\",\"tenant_id\":\"$TENANT\",\"status\":\"ready\",\"max_concurrent\":5,\"current_sessions\":0,\"pools\":[\"$2\"],\"source\":\"human_login\",\"execution_model\":\"stateful\"}" >/dev/null
  redis SADD "${TENANT}:pool:$2:ready" "$INST" >/dev/null
  redis SADD "${TENANT}:pool:$2:instances" "$INST" >/dev/null
  local sid; sid="$($CURL -X POST "$CG/v1/channels/webhook/pool/$1" $JSON -d "{\"tenant_id\":\"$TENANT\"}" | jq -r '.session_id // empty')"
  [ -n "$sid" ] || return 1
  local i; for i in $(seq 1 25); do
    [ -n "$(redis ZSCORE "${TENANT}:pool:$2:queue" "$sid" | tr -d '\r')" ] && break; sleep 1
  done
  $COMPOSE exec -T routing-engine python3 -c "
import json,urllib.request
b=json.dumps({'tenant_id':'$TENANT','pool_id':'$2','session_id':'$sid','instance_id':'$INST'}).encode()
r=urllib.request.Request('$RE_URL/v1/work_queue/claim',data=b,headers={'content-type':'application/json'})
urllib.request.urlopen(r).read()
" < /dev/null >/dev/null 2>&1
  printf '%s' "$sid"
}
estado() { $CURL -o /tmp/gate47.json -w '%{http_code}' "$MCP/api/supervisor_state/$1" -H "Authorization: Bearer $2"; }
tem_token() { jq -e --arg t "$1" '[..|strings] | any(index($t) != null)' /tmp/gate47.json >/dev/null 2>&1 && echo sim || echo nao; }
encerra() {  # $1=sid $2=pool
  local rt; rt="$(redis HGET "${TENANT}:ctx:$1" core.workflow.delegate_resume_token | tr -d '\r' | jq -r '.value // empty' 2>/dev/null)"
  [ -z "$rt" ] && return 0
  [ -z "$(redis HGET "${TENANT}:resume_tokens" "$rt" | tr -d '\r')" ] && return 0
  $CURL -o /dev/null -X POST "$CG/v1/channels/webhook/resume/$rt" $JSON -H "Authorization: Bearer $T_ADM" \
    -d "{\"tenant_id\":\"$TENANT\",\"pool_id\":\"$2\",\"instance_id\":\"$INST\",\"payload\":{\"decision\":\"timeout\",\"source\":\"gate47\"}}"
  redis SREM "${TENANT}:pool:$2:ready" "$INST" >/dev/null
}

printf '\033[1mgate: o estado da sessao recorta por pool\033[0m\n'

SID="$(cria gate_promocao_ia aprovacao_deploy)" || inc "nao consegui criar a tarefa de aprovacao"
RTOK="$(redis HGET "${TENANT}:ctx:${SID}" core.workflow.delegate_resume_token | tr -d '\r' | jq -r '.value // empty')"
[ -n "$RTOK" ] || inc "sem resume token — P1 nao poderia provar que ele CONTINUA visivel"
echo "  sessao em aprovacao_deploy: $SID"

sec "N1/N2 — quem nao alcanca o pool nao le o estado"
C="$(estado "$SID" "$T_OPE")"
[ "$C" = "403" ] && ok "operator -> 403" || bad "operator -> $C (esperava 403)"
C="$(estado "$SID" "$T_SUP")"
if [ "$C" = "403" ]; then
  ok "supervisor -> 403 (era 200 COM a credencial da tarefa no corpo)"
else
  bad "supervisor -> $C (esperava 403); token no corpo? $(tem_token "$RTOK")"
fi

sec "P1 — quem alcanca continua lendo TUDO"
printf '   (sem este ramo, N1/N2 ficariam verdes com o Console quebrado)\n'
C="$(estado "$SID" "$T_ADM")"
if [ "$C" = "200" ]; then
  ok "admin -> 200"
  [ "$(tem_token "$RTOK")" = "sim" ] && ok "e o corpo AINDA traz o resume_token — nao virou recusa disfarcada" \
                                     || bad "admin 200 mas SEM o token: o portao mutilou o pacote de quem pode"
else
  bad "admin -> $C (esperava 200) — o portao barrou quem devia passar"
fi
encerra "$SID" aprovacao_deploy

sec "P2 — um NAO-admin le a sessao de um pool que ele alcanca"
printf '   (sem este ramo, o portao poderia ser so \"admin ve, o resto nao\")\n'
SID2="$(cria formfill_demo_ia formfill_demo)" || inc "nao consegui criar a tarefa de form-fill"
RTOK2="$(redis HGET "${TENANT}:ctx:${SID2}" core.workflow.delegate_resume_token | tr -d '' | jq -r '.value // empty')"
C="$(estado "$SID2" "$T_OPE")"
if [ "$C" = "200" ]; then
  ok "operator -> 200 em formfill_demo (pool dele)"
  # -- CNS-24 (2026-09-11): 200 NAO basta ---------------------------------------
  # Este ramo existia para impedir "o operador de verdade trancado do lado de
  # fora" - e ficou VERDE por dez dias com o operador trancado, porque conferia
  # so o STATUS. A CNS-11 moveu o pacote do formulario para `core.workflow.*`, o
  # portao de namespace do operador o escondeu, e o 200 continuou vindo: com o
  # corpo sem o que a Console precisa. O wrap-up do operador abria VAZIO.
  # O que prova que ele pode fazer a tarefa e o CORPO, nao o codigo HTTP.
  if jq -e '.customer_context.context_snapshot["core.workflow.dialog_form_id"].value // empty | length > 0'        /tmp/gate47.json >/dev/null 2>&1; then
    ok "e o corpo traz o dialog_form_id - o formulario tem o que renderizar"
  else
    bad "operator 200 mas SEM dialog_form_id: a Console mostra contato VAZIO (CNS-24)"
  fi
  # Sem token nao ha o que procurar, e `index("")` casaria qualquer string -
  # verde vacuo. Ausencia aqui e pre-condicao, nao aprovacao.
  [ -n "$RTOK2" ] || inc "a tarefa de form-fill nasceu SEM resume token - P2 nao prova o pacote"
  [ "$(tem_token "$RTOK2")" = "sim" ] && ok "e o corpo traz o resume_token - o operador consegue SUBMETER"                                       || bad "operator 200 mas SEM o token: o formulario nao pode ser enviado (CNS-24)"
else
  bad "operator -> $C em pool PROPRIO (esperava 200) - fechei demais"
fi
encerra "$SID2" formfill_demo

sec "N4 — tenant: DECLARADO nao-exercivel"
printf '   `accessible_pools` traz pool_id CRU, e pool_id nao e unico entre tenants —\n'
printf '   dois tenants com um `sac_ia` cada fariam a lista de um autorizar a sessao do\n'
printf '   outro. O portao compara o tenant do token com o do `meta` ANTES do pool.\n'
N_TEN="$(redis --scan --pattern "*:ctx:*" | cut -d: -f1 | sort -u | wc -l)"
if [ "${N_TEN:-1}" -le 1 ]; then
  printf '  \033[33mNAO EXERCIDO\033[0m ha %s tenant na instalacao — nao existe o outro lado\n' "${N_TEN:-1}"
  printf '               para pedir. Verde aqui seria vacuo; o ramo fica declarado.\n'
else
  printf '               ha %s tenants: este ramo passou a ser exercivel — escreva-o\n' "$N_TEN"
  printf '               (esta linha existe para ser encontrada quando isso acontecer).\n'
fi

sec "N3 — as duas ausencias nao se confundem"
C="$(estado "00000000-0000-0000-0000-000000000000" "$T_ADM")"
[ "$C" = "404" ] && ok "sessao inexistente -> 404, nao 403" \
                 || bad "sessao inexistente -> $C (esperava 404): 'expirou' chegando como 'voce nao pode'"

printf '\n'
[ "$FALHOU" = 0 ] && { printf '\033[32mVERDE\033[0m — o estado da sessao pertence ao pool dela.\n'; exit 0; }
printf '\033[31mVERMELHO\033[0m\n'; exit 1
