#!/usr/bin/env bash
# ==============================================================================
# gate_user_admin_org_chart.sh — administrar PESSOA exige o organograma
# ==============================================================================
#
# O QUE ELE PROVA
# ---------------
#   1  NEGATIVO — supervisor com `config.users` e sem grupo nao alcanca ninguem:
#      nao lista, nao reseta senha, e a senha que ele tentaria impor nao vale
#   2  POSITIVO (admin) — o dono do tenant continua administrando todo mundo
#   3  POSITIVO (organograma) — com o grupo, o supervisor PASSA
#
# ⚠️ **O ramo 3 e o que impede o verde errado.** Um enforcement que negasse TUDO
# deixaria o ramo 1 verde e o produto quebrado: "ninguem administra ninguem"
# tambem nega o ataque. A regra da casa e escrever o caso que prova que o portao
# DEIXA alguem passar.
#
# POR QUE ELE EXISTE (AUT-39, 2026-09-09)
# ---------------------------------------
# Medido ao vivo ANTES da correcao, com alvo descartavel: um supervisor com
# `config.users` e **ZERO pools** listou os 9 usuarios do tenant, trocou a senha
# de um usuario de outro time (**HTTP 200**), **entrou na conta** com a senha nova
# e a original deixou de valer. Nao e escalacao — o alvo nao esta acima —, e sim
# **tomada lateral entre times**, e para ela nao existia eixo onde declarar
# *"administro estas pessoas"*.
#
# ⚠️ **A alternativa por POOL foi recusada por CENSO, nao por gosto:** sob
# `pools(alvo) ⊆ pools(ator)`, o `admin@` so administrava os outros porque
# ENUMERAVA os 41 pools do registry — e um pool novo o tirava dessa condicao **sem
# erro em lugar nenhum** (o discriminador da AUT-29). Membership de grupo e
# explicita: quem nao esta em grupo nenhum e recusado com 403 que NOMEIA o motivo.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -u

cd "$(dirname "$0")/../.." || { echo "INCONCLUSIVO: raiz do repo nao encontrada"; exit 2; }

AUTH="${AUTH:-http://localhost:3202}"
TENANT="${TENANT:-tenant_demo}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@plughub.local}"
ADMIN_PASS="${ADMIN_PASS:-changeme_admin}"
SUP_EMAIL="${SUP_EMAIL:-probe_rowscope@plughub.local}"
SUP_PASS="${SUP_PASS:-probe_rowscope_123}"
ALVO_EMAIL="gate_orgchart_alvo@plughub.local"
ALVO_PASS="gate_orgchart_123"

FALHOU=0
ok()  { printf '  \033[32mOK\033[0m    %s\n' "$1"; }
bad() { printf '  \033[31mFALHA\033[0m %s\n' "$1"; FALHOU=1; }
inc() { printf '  \033[33mINCONCLUSIVO\033[0m %s\n' "$1"; exit 2; }

login() {
  curl -s -m 20 -X POST "$AUTH/auth/login" -H 'content-type: application/json' \
    -d "{\"email\":\"$1\",\"password\":\"$2\",\"tenant_id\":\"$TENANT\"}" \
  | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("access_token",""))
except Exception: print("")'
}
conta() { python3 -c 'import json,sys
try:
    d=json.load(sys.stdin); us = d if isinstance(d,list) else (d.get("users") or [])
    print(len(us))
except Exception: print("erro")'; }
uid_de() {
  curl -s -m 20 "$AUTH/auth/users?tenant_id=$TENANT" -H "Authorization: Bearer $1" \
  | python3 -c 'import json,sys
d=json.load(sys.stdin); us = d if isinstance(d,list) else (d.get("users") or [])
print(next((u["id"] for u in us if u.get("email")==sys.argv[1]), ""))' "$2"
}

T_ADMIN="$(login "$ADMIN_EMAIL" "$ADMIN_PASS")"
[ -n "$T_ADMIN" ] || inc "login do admin falhou (auth-api no ar? ADMIN_PASS certo?)"
T_SUP="$(login "$SUP_EMAIL" "$SUP_PASS")"
[ -n "$T_SUP" ] || inc "login de $SUP_EMAIL falhou — a fixture do supervisor existe?"

# ── alvo: criado pela API OFICIAL, nunca por escrita direta no store ──────────
curl -s -m 20 -o /dev/null -X POST "$AUTH/auth/users" \
  -H "Authorization: Bearer $T_ADMIN" -H 'content-type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"email\":\"$ALVO_EMAIL\",\"name\":\"Gate OrgChart\",\"password\":\"$ALVO_PASS\",\"roles\":[\"operator\"],\"accessible_pools\":[]}"
ALVO_ID="$(uid_de "$T_ADMIN" "$ALVO_EMAIL")"
[ -n "$ALVO_ID" ] || inc "nao criei nem achei o alvo"
# devolve ao estado inicial quando a execucao anterior o deixou mexido
curl -s -m 20 -o /dev/null -X PATCH "$AUTH/auth/users/$ALVO_ID" \
  -H "Authorization: Bearer $T_ADMIN" -H 'content-type: application/json' \
  -d "{\"active\": true, \"password\": \"$ALVO_PASS\"}"
SUP_ID="$(uid_de "$T_ADMIN" "$SUP_EMAIL")"

GRUPO=""
limpar() {
  [ -n "$GRUPO" ] && curl -s -m 20 -o /dev/null -X DELETE "$AUTH/auth/v1/groups/$GRUPO" \
    -H "Authorization: Bearer $T_ADMIN"
  curl -s -m 20 -o /dev/null -X DELETE "$AUTH/auth/users/$ALVO_ID" \
    -H "Authorization: Bearer $T_ADMIN"
}
trap limpar EXIT

# ══ 1. NEGATIVO ══════════════════════════════════════════════════════════════
printf '\n\033[1m1. supervisor SEM grupo nao alcanca ninguem\033[0m\n'
N=$(curl -s -m 20 "$AUTH/auth/users?tenant_id=$TENANT" -H "Authorization: Bearer $T_SUP" | conta)
if [ "$N" = "1" ]; then ok "a lista devolve so ele mesmo"
else bad "a lista devolveu $N — o recorte por organograma nao esta valendo"; fi

COD=$(curl -s -m 20 -o /dev/null -w '%{http_code}' -X PATCH "$AUTH/auth/users/$ALVO_ID" \
  -H "Authorization: Bearer $T_SUP" -H 'content-type: application/json' \
  -d '{"password": "tentativa_bloqueada_gate"}')
[ "$COD" = "403" ] && ok "PATCH password -> 403" || bad "PATCH password -> $COD, esperava 403"

T_X="$(login "$ALVO_EMAIL" "tentativa_bloqueada_gate")"
[ -z "$T_X" ] && ok "a senha que ele tentaria impor NAO vale — a conta fica com o dono" \
              || bad "a senha imposta funcionou: a tomada lateral voltou"

# ══ 2. POSITIVO (admin) ══════════════════════════════════════════════════════
printf '\n\033[1m2. o dono do tenant continua administrando\033[0m\n'
N=$(curl -s -m 20 "$AUTH/auth/users?tenant_id=$TENANT" -H "Authorization: Bearer $T_ADMIN" | conta)
if [ "$N" != "erro" ] && [ "$N" -ge 2 ] 2>/dev/null; then
  ok "admin ve $N usuarios"
else
  bad "admin ve $N — o enforcement prendeu o dono do lado de fora"
fi

# ══ 3. POSITIVO (organograma) ════════════════════════════════════════════════
printf '\n\033[1m3. com o grupo, o supervisor PASSA\033[0m\n'
printf '   (sem este ramo, o 1 ficaria verde por negar TUDO)\n'
GRUPO=$(curl -s -m 20 -X POST "$AUTH/auth/v1/groups" -H "Authorization: Bearer $T_ADMIN" \
  -H 'content-type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"name\":\"gate orgchart\",\"description\":\"AUT-39\"}" \
  | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("group_id",""))
except Exception: print("")')
if [ -z "$GRUPO" ]; then
  printf '  \033[33mINCONCLUSIVO\033[0m nao criei o grupo — o ramo 3 NAO mediu nada\n'
  exit 2
fi
curl -s -m 20 -o /dev/null -X POST "$AUTH/auth/v1/groups/$GRUPO/users" \
  -H "Authorization: Bearer $T_ADMIN" -H 'content-type: application/json' \
  -d "{\"user_id\":\"$ALVO_ID\"}"
curl -s -m 20 -o /dev/null -X POST "$AUTH/auth/v1/groups/$GRUPO/supervisors" \
  -H "Authorization: Bearer $T_ADMIN" -H 'content-type: application/json' \
  -d "{\"user_id\":\"$SUP_ID\"}"
T_SUP="$(login "$SUP_EMAIL" "$SUP_PASS")"
N=$(curl -s -m 20 "$AUTH/auth/users?tenant_id=$TENANT" -H "Authorization: Bearer $T_SUP" | conta)
[ "$N" = "2" ] && ok "ve 2: ele mesmo + o membro do grupo" || bad "ve $N — esperava 2"
COD=$(curl -s -m 20 -o /dev/null -w '%{http_code}' -X PATCH "$AUTH/auth/users/$ALVO_ID" \
  -H "Authorization: Bearer $T_SUP" -H 'content-type: application/json' \
  -d '{"name": "editado pelo supervisor do grupo"}')
[ "$COD" = "200" ] && ok "PATCH name -> 200: o supervisor DO GRUPO administra" \
                   || bad "PATCH name -> $COD: o modelo nega ate quem deveria passar"

# ══ 4. a TRILHA registrou? ═══════════════════════════════════════════════════
printf '\n\033[1m4. a administracao deixa rastro\033[0m\n'
PG="${PG:-plughub-demo-postgres-1}"
LINHAS=$(docker exec "$PG" psql -U plughub -d plughub_demo -t -A -c \
  "SELECT count(*) FROM auth.user_admin_log WHERE target_email = '$ALVO_EMAIL'" 2>/dev/null)
if [ -z "$LINHAS" ]; then
  printf '  \033[33mSEM AMOSTRA\033[0m nao consegui ler auth.user_admin_log\n'
elif [ "$LINHAS" -gt 0 ] 2>/dev/null; then
  ok "$LINHAS linha(s) de trilha para o alvo — quem mexeu fica nomeado"
else
  bad "nenhuma trilha: a administracao voltou a ser silenciosa"
fi

printf '\n'
[ "$FALHOU" = 0 ] && { printf '\033[32mVERDE\033[0m — nega o lateral, preserva o admin, deixa passar o grupo, e registra.\n'; exit 0; }
printf '\033[31mVERMELHO\033[0m\n'; exit 1
