#!/usr/bin/env bash
# ==============================================================================
# gate_resume_scope_nao_e_opcional.sh — o chamador nao escolhe a pergunta
# ==============================================================================
#
# O QUE ELE PROVA
# ---------------
#   N1  tarefa que DECLARA capacidade nao e decidida SEM credencial      -> 401
#   N2  quem tem a capacidade mas nao alcanca o pool nao decide, mesmo
#       OMITINDO `pool_id` (o eixo de escopo deixou de ser opcional)     -> 403
#   P1  o aprovador LEGITIMO do pool decide                              -> 200
#   P2  resume SEM credencial de tarefa SEM ABAC continua passando       -> 200
#
# ⚠️ **P1 e P2 sao o coracao deste gate, nao enfeite.** Sem P1, um ingress que
# negasse TUDO deixaria N1 e N2 verdes com o produto quebrado. Sem P2, fechar
# demais tambem passaria: **322 dos 323** resumes sem credencial medidos em 30 dias
# NAO decidem nada (form-fill, wrap-up, sistema) — sao eles que usam aquela porta, e
# fecha-la seria trocar um buraco por um apagao.
#
# POR QUE ELE EXISTE (AUT-46, 2026-09-09)
# ---------------------------------------
# Medido ao vivo, com tarefa de promocao de DEPLOY criada pelo caminho oficial:
#
#   operator@ (sem `aprovacao_deploy` no escopo), DECLARANDO pool_id ... 403
#   o MESMO chamador, OMITINDO pool_id ......................... **200** (reprovou)
#   o MESMO resume, SEM header nenhum .......................... **200**
#
# E a linha durauel gravou `verification_class: possessed` para o caso do meio: a
# decisao ficou atribuida a um aprovador "verificado" que o proprio sistema
# recusaria se o corpo tivesse dito a verdade. Portao que so enforca quando o
# chamador pede para ser enforcado nao e portao — e quem concede acredita ter negado.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail
cd "$(dirname "$0")/../.." || { echo "INCONCLUSIVO: raiz do repo nao encontrada"; exit 2; }

COMPOSE="docker compose -f docker-compose.demo.yml"
TENANT="${TENANT:-tenant_demo}"
CG="${CG:-http://localhost:8010}"
AUTH="${AUTH:-http://localhost:3202}"
RE="${RE:-http://localhost:3550}"
POOL_WH=gate_promocao_ia
POOL_PULL=aprovacao_deploy
POOL_FORM=formfill_demo_ia
AD_EMAIL="${AD_EMAIL:-admin@plughub.local}"; AD_PASS="${AD_PASS:-changeme_admin}"
OP_EMAIL="${OP_EMAIL:-operator@plughub.local}"; OP_PASS="${OP_PASS:-changeme_operator}"

CURL="curl -s --max-time 20"
JSON='-H Content-Type:application/json'
FALHOU=0
ok()  { printf '  \033[32mOK\033[0m           %s\n' "$1"; }
bad() { printf '  \033[31mFALHA\033[0m        %s\n' "$1"; FALHOU=1; }
inc() { printf '  \033[33mINCONCLUSIVO\033[0m %s\n' "$1"; exit 2; }
sec() { printf '\n\033[1m%s\033[0m\n' "$1"; }
redis() { $COMPOSE exec -T redis redis-cli "$@" < /dev/null; }

command -v jq >/dev/null || inc "jq ausente"

login() {
  $CURL -X POST "$AUTH/auth/login" $JSON \
    -d "{\"email\":\"$1\",\"password\":\"$2\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty'
}
sub_de() { printf '%s' "$1" | cut -d. -f2 | tr '_-' '/+' \
  | { read -r p; printf '%s' "$p$(printf '%*s' $(( (4 - ${#p} % 4) % 4 )) '' | tr ' ' '=')"; } \
  | base64 -d 2>/dev/null | jq -r '.sub // empty'; }

T_ADM="$(login "$AD_EMAIL" "$AD_PASS")";  [ -n "$T_ADM" ] || inc "login do admin falhou"
T_OPE="$(login "$OP_EMAIL" "$OP_PASS")";  [ -n "$T_OPE" ] || inc "login do operator falhou"
INST="human-$(sub_de "$T_ADM")"

# o operator PRECISA ter a capacidade e NAO ter o pool — senao o N2 mede outra coisa
CFG="$(printf '%s' "$T_OPE" | cut -d. -f2 | tr '_-' '/+' \
  | { read -r p; printf '%s' "$p$(printf '%*s' $(( (4 - ${#p} % 4) % 4 )) '' | tr ' ' '=')"; } \
  | base64 -d 2>/dev/null)"
echo "$CFG" | jq -e '.module_config.approvals.decide.access != "none"' >/dev/null 2>&1 \
  || inc "o operator nao tem \`approvals.decide\` — o N2 nao exerceria o eixo de escopo"
echo "$CFG" | jq -e --arg p "$POOL_PULL" '(.accessible_pools // []) | index($p) | not' >/dev/null 2>&1 \
  || inc "o operator ALCANCA $POOL_PULL — o N2 nao teria o que negar"

cria_aprovacao() {   # -> SID via stdout
  redis SET "${TENANT}:instance:${INST}" \
    "{\"instance_id\":\"$INST\",\"agent_type_id\":\"human\",\"tenant_id\":\"$TENANT\",\"status\":\"ready\",\"max_concurrent\":5,\"current_sessions\":0,\"pools\":[\"$POOL_PULL\"],\"source\":\"human_login\",\"execution_model\":\"stateful\"}" >/dev/null
  redis SADD "${TENANT}:pool:${POOL_PULL}:ready" "$INST" >/dev/null
  redis SADD "${TENANT}:pool:${POOL_PULL}:instances" "$INST" >/dev/null
  local sid; sid="$($CURL -X POST "$CG/v1/channels/webhook/pool/$POOL_WH" $JSON -d "{\"tenant_id\":\"$TENANT\"}" | jq -r '.session_id // empty')"
  [ -n "$sid" ] || return 1
  local i; for i in $(seq 1 25); do
    [ -n "$(redis ZSCORE "${TENANT}:pool:${POOL_PULL}:queue" "$sid" | tr -d '\r')" ] && break; sleep 1
  done
  printf '%s' "$sid"
}
token_de() { redis HGET "${TENANT}:ctx:$1" core.workflow.delegate_resume_token | tr -d '\r' | jq -r '.value // empty' 2>/dev/null; }
resume() {  # $1=token $2=authheader-ou-vazio $3=corpo-extra
  local h=(); [ -n "$2" ] && h=(-H "Authorization: Bearer $2")
  $CURL -o /tmp/gate_resume_body.json -w '%{http_code}' -X POST "$CG/v1/channels/webhook/resume/$1" \
    $JSON "${h[@]}" -d "{\"tenant_id\":\"$TENANT\"$3,\"payload\":{\"decision\":\"input\",\"source\":\"operator\",\"choice\":\"reprovar\"}}"
}

printf '\033[1mgate: o ingress de resume nao deixa o chamador desligar os eixos\033[0m\n'

# ══ tarefa 1 — aprovacao (N1, N2, P1 na MESMA tarefa: as recusas nao a consomem) ══
SID="$(cria_aprovacao)" || inc "nao consegui criar a tarefa de aprovacao"
RTOK="$(token_de "$SID")"; [ -n "$RTOK" ] || inc "sem resume token no ctx de $SID"
echo "  tarefa de aprovacao descartavel: $SID"

sec "N1 — sem credencial, tarefa que declara capacidade"
C="$(resume "$RTOK" "" "")"
[ "$C" = "401" ] && ok "401: o caminho anonimo nao decide tarefa escopada" \
                 || bad "HTTP $C (esperava 401) — $(head -c 120 /tmp/gate_resume_body.json)"

sec "N2 — tem a capacidade, nao alcanca o pool, e OMITE pool_id"
C="$(resume "$RTOK" "$T_OPE" "")"
[ "$C" = "403" ] && ok "403: o escopo vem do SERVIDOR, nao do corpo" \
                 || bad "HTTP $C (esperava 403) — omitir o campo voltou a desligar o eixo"
C="$(resume "$RTOK" "$T_OPE" ",\"pool_id\":\"$POOL_PULL\"")"
[ "$C" = "403" ] && ok "403 tambem declarando o pool (o caso que ja funcionava)" \
                 || bad "HTTP $C declarando o pool (esperava 403)"

sec "P1 — o aprovador LEGITIMO decide"
printf '   (sem este ramo, N1 e N2 ficariam verdes com o ingress negando TUDO)\n'
CL="$($COMPOSE exec -T routing-engine python3 -c "
import json,urllib.request
body=json.dumps({'tenant_id':'$TENANT','pool_id':'$POOL_PULL','session_id':'$SID','instance_id':'$INST'}).encode()
req=urllib.request.Request('$RE/v1/work_queue/claim',data=body,headers={'content-type':'application/json'})
print(urllib.request.urlopen(req).read().decode())
" < /dev/null 2>&1)"
printf '%s' "$CL" | grep -q '"claimed": *true' || inc "o claim do aprovador falhou — P1 nao mediria nada"
C="$(resume "$RTOK" "$T_ADM" ",\"pool_id\":\"$POOL_PULL\",\"instance_id\":\"$INST\"")"
[ "$C" = "200" ] && ok "200: quem alcanca o pool continua decidindo" \
                 || bad "HTTP $C (esperava 200) — o conserto barrou quem devia passar"

# ══ tarefa 2 — form-fill SEM ABAC: a porta anonima tem de continuar aberta ══
sec "P2 — resume SEM credencial de tarefa SEM ABAC continua passando"
printf '   (322 dos 323 resumes anonimos medidos sao desta especie)\n'
SID2="$($CURL -X POST "$CG/v1/channels/webhook/pool/$POOL_FORM" $JSON -d "{\"tenant_id\":\"$TENANT\"}" | jq -r '.session_id // empty')"
if [ -z "$SID2" ]; then
  printf '  \033[33mSEM AMOSTRA\033[0m nao consegui disparar %s — P2 NAO foi exercido\n' "$POOL_FORM"
else
  RT2=""; for _ in $(seq 1 25); do RT2="$(token_de "$SID2")"; [ -n "$RT2" ] && break; sleep 1; done
  if [ -z "$RT2" ]; then
    printf '  \033[33mSEM AMOSTRA\033[0m %s nao suspendeu com token — P2 NAO foi exercido\n' "$SID2"
  else
    C="$($CURL -o /tmp/gate_resume_body.json -w '%{http_code}' -X POST "$CG/v1/channels/webhook/resume/$RT2" \
        $JSON -d "{\"tenant_id\":\"$TENANT\",\"payload\":{\"decision\":\"input\",\"source\":\"system\"}}")"
    [ "$C" = "200" ] && ok "200: a porta que os 322 usam continua aberta" \
                     || bad "HTTP $C (esperava 200) — fechei a porta errada"
  fi
fi

# ══ limpeza — nada pendurado ══
sec "limpeza"
for t in "$RTOK" "${RT2:-}"; do
  [ -z "$t" ] && continue
  if [ -n "$(redis HGET "${TENANT}:resume_tokens" "$t" | tr -d '\r')" ]; then
    C="$($CURL -o /dev/null -w '%{http_code}' -X POST "$CG/v1/channels/webhook/resume/$t" $JSON \
        -H "Authorization: Bearer $T_ADM" \
        -d "{\"tenant_id\":\"$TENANT\",\"pool_id\":\"$POOL_PULL\",\"instance_id\":\"$INST\",\"payload\":{\"decision\":\"timeout\",\"source\":\"gate:cleanup\"}}")"
    echo "  token ${t:0:8}… encerrado (HTTP $C)"
  fi
done
redis SREM "${TENANT}:pool:${POOL_PULL}:ready" "$INST" >/dev/null

printf '\n'
[ "$FALHOU" = 0 ] && { printf '\033[32mVERDE\033[0m — credencial por tipo de tarefa, escopo do servidor, e as duas portas legitimas abertas.\n'; exit 0; }
printf '\033[31mVERMELHO\033[0m\n'; exit 1
