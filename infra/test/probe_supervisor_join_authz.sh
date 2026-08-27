#!/usr/bin/env bash
# probe_supervisor_join_authz.sh — GATE de `POST /supervisor/{join,message,leave}`.
#
# O defeito que ele existe para reprovar (medido 2026-08-26/27): o router
# `supervisor.py` NAO tinha `Depends` nenhum. Qualquer um que alcancasse a porta
# entrava numa conferencia de cliente AO VIVO e ESCREVIA no stream dela
# (`participant_joined`), declarando o proprio `tenant_id` no corpo.
#
# Contrato de agora: token obrigatorio (401 sem header) + escopo de pool da sessao
# (403 fora do escopo) + recusa quando o escopo e INDETERMINAVEL.
#
# Como ele fica VERMELHO: rode contra a imagem anterior ao conserto — o ramo A sai 200.
#
# Testemunhas (sem elas o verde nao vale nada):
#   · ramo D  — presenca: o caminho FELIZ tem de dar 200. Sem ele, "quebrei tudo"
#               produz A/B/C/E verdes e passa como sucesso.
#   · ramo F  — o resolvedor de pools NAO e so `meta.pool_id`: sessao sem pool no meta
#               mas com instancia nos SETs de agente tem de ser autorizada pela UNIAO.
#   · ramo A' — a rota EXISTE e responde: com token, o mesmo POST nao da 401/404-de-rota.
#
# Roda do HOST. Nao precisa de build (o build e do SERVICO, nao deste script).
# Fabrica sessoes vivas direto no Redis — que e exatamente o estado que o endpoint le —
# e as APAGA no fim.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

AN=${AN:-http://localhost:3500}
AUTH=${AUTH:-http://localhost:3202/auth}
TENANT=${TENANT:-tenant_demo}
USER_EMAIL=${USER_EMAIL:-supervisor@plughub.local}
USER_PASS=${USER_PASS:-changeme_supervisor}
DC=${DC:-docker compose -f docker-compose.demo.yml}
IN_POOL=${IN_POOL:-limite_ia}
OUT_POOL=${OUT_POOL:-sac_ia}

command -v jq >/dev/null || { echo "INCONCLUSIVO: jq ausente"; exit 2; }

fail=0
note() { printf '   %s\n' "$*"; }
verdict() { # nome esperado obtido
  if [ "$2" = "$3" ]; then printf '  ✅ %-46s %s\n' "$1" "$3"
  else printf '  ❌ %-46s esperado=%s obtido=%s\n' "$1" "$2" "$3"; fail=1; fi
}

TOK=$(curl -s -X POST "$AUTH/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$USER_EMAIL\",\"password\":\"$USER_PASS\",\"tenant_id\":\"$TENANT\"}" \
  | jq -r '.access_token // empty')
[ -z "$TOK" ] && { echo "INCONCLUSIVO: login falhou para $USER_EMAIL"; exit 2; }

PAY=$(echo "$TOK" | cut -d. -f2 | tr '_-' '/+')
case $(( ${#PAY} % 4 )) in 2) PAY="$PAY==";; 3) PAY="$PAY=";; esac
POOLS=$(echo "$PAY" | base64 -d 2>/dev/null | jq -c '.accessible_pools')
echo "usuario=$USER_EMAIL  pools=$POOLS"
if [ "$POOLS" = "[]" ] || [ "$POOLS" = "null" ]; then
  echo "INCONCLUSIVO: este usuario e IRRESTRITO ([] = todos os pools) — os ramos C e E"
  echo "  nao podem reprovar, porque o guard retorna cedo em `is_unrestricted`."
  exit 2
fi

# ── fabricar sessoes vivas ────────────────────────────────────────────────────
SID_IN="probe-authz-in-$$"
SID_OUT="probe-authz-out-$$"
SID_NOPOOL="probe-authz-nopool-$$"
SID_INST="probe-authz-inst-$$"
INST="probe-authz-instance-$$"

rc() { $DC exec -T redis redis-cli "$@" >/dev/null 2>&1; }
cleanup() {
  rc DEL "session:$SID_IN:meta" "session:$SID_OUT:meta" \
         "session:$SID_NOPOOL:meta" "session:$SID_INST:meta" \
         "session:$SID_INST:ai_agents" "$TENANT:instance:$INST" \
         "supervisor:$SID_IN:active" "supervisor:$SID_OUT:active" \
         "supervisor:$SID_NOPOOL:active" "supervisor:$SID_INST:active" \
         "session:$SID_IN:stream" "session:$SID_INST:stream"
}
trap cleanup EXIT

rc SETEX "session:$SID_IN:meta"     600 "{\"tenant_id\":\"$TENANT\",\"pool_id\":\"$IN_POOL\",\"channel\":\"webchat\"}"
rc SETEX "session:$SID_OUT:meta"    600 "{\"tenant_id\":\"$TENANT\",\"pool_id\":\"$OUT_POOL\",\"channel\":\"webchat\"}"
rc SETEX "session:$SID_NOPOOL:meta" 600 "{\"tenant_id\":\"$TENANT\",\"channel\":\"webhook\"}"
rc SETEX "session:$SID_INST:meta"   600 "{\"tenant_id\":\"$TENANT\",\"channel\":\"webhook\"}"
rc SETEX "$TENANT:instance:$INST"   600 "{\"instance_id\":\"$INST\",\"pools\":[\"$IN_POOL\"]}"
rc SADD  "session:$SID_INST:ai_agents" "$INST"
rc EXPIRE "session:$SID_INST:ai_agents" 600

post() { # $1=sid  $2=token|""   -> imprime "HTTP|detail"
  local hdr=() body
  [ -n "$2" ] && hdr=(-H "Authorization: Bearer $2")
  body=$(curl -s -w '\n%{http_code}' -X POST "$AN/supervisor/join" \
    -H 'content-type: application/json' "${hdr[@]}" \
    -d "{\"tenant_id\":\"$TENANT\",\"session_id\":\"$1\",\"operator_id\":\"probe\"}")
  local code; code=$(printf '%s' "$body" | tail -1)
  local det;  det=$(printf '%s' "$body" | sed '$d' | jq -r '.detail // .participant_id // "-"' 2>/dev/null)
  printf '%s|%s' "$code" "$det"
}

echo
echo "── A. sem Authorization ⇒ 401 (era 200: entrava sem credencial) ──"
A=$(post "$SID_IN" ""); verdict "A  anonimo em sessao viva" "401" "${A%%|*}"

echo "── A'. TESTEMUNHA: a rota existe e responde (o 401 e de AUTH, nao de rota) ──"
AL=$(post "$SID_IN" "$TOK"); AC=${AL%%|*}
if [ "$AC" = "404" ] || [ "$AC" = "405" ]; then
  note "❌ rota inalcancavel (HTTP $AC) — o 401 do ramo A nao prova nada"; fail=1
else
  printf '  ✅ %-46s HTTP %s\n' "A' rota alcancavel com token" "$AC"
fi

echo "── B. token invalido ⇒ 401 ──"
B=$(post "$SID_IN" "lixo.nao.jwt"); verdict "B  token invalido" "401" "${B%%|*}"

echo "── C. token valido, sessao FORA do escopo ⇒ 403 pool_scope_denied ──"
C=$(post "$SID_OUT" "$TOK")
verdict "C  fora do escopo (HTTP)"    "403"               "${C%%|*}"
verdict "C  fora do escopo (motivo)"  "pool_scope_denied" "${C##*|}"

echo "── D. TESTEMUNHA DE PRESENCA: dentro do escopo ⇒ 200 ──"
rc DEL "supervisor:$SID_IN:active"
D=$(post "$SID_IN" "$TOK")
verdict "D  dentro do escopo (HTTP)" "200" "${D%%|*}"
[ "${D%%|*}" != "200" ] && note "sem este 200, os 403 acima significam 'quebrei tudo', nao 'gate correto'"

echo "── E. escopo INDETERMINAVEL ⇒ 403 session_pools_undeterminable ──"
E=$(post "$SID_NOPOOL" "$TOK")
verdict "E  sem pool algum (HTTP)"   "403"                            "${E%%|*}"
verdict "E  sem pool algum (motivo)" "session_pools_undeterminable"   "${E##*|}"

echo "── F. TESTEMUNHA DA UNIAO: pool vem da INSTANCIA, nao do meta ⇒ 200 ──"
F=$(post "$SID_INST" "$TOK")
verdict "F  pool derivado da instancia" "200" "${F%%|*}"
[ "${F%%|*}" != "200" ] && note "o resolvedor colapsou para meta.pool_id — a uniao nao esta valendo"

echo
if [ "$fail" -eq 0 ]; then echo "RESULTADO: OK — todos os ramos, com as tres testemunhas."; exit 0
else echo "RESULTADO: FALHOU"; exit 1; fi
