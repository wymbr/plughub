#!/usr/bin/env bash
# ==============================================================================
# gate_hire_into_your_own_team.sh — quem CRIA um usuario passa a administra-lo
# ==============================================================================
#
# O QUE ELE PROVA
# ---------------
#   P1  delegado cria DENTRO do time que supervisiona -> 201, e no instante seguinte
#       ele VE (200), EDITA (200) e a pessoa APARECE na lista dele
#   P2  `admin` continua criando SEM grupo -> 201 (o caminho universal nao morreu)
#   N1  delegado cria SEM grupo -> 422 nomeando, e NENHUMA conta e criada
#   N2  delegado cria em time ALHEIO -> 403 nomeando, e NENHUMA conta e criada
#   N3  `admin` cita grupo INEXISTENTE -> 422 (aceitar em silencio faria o chamador
#       crer que vinculou)
#   P3  a SEGUNDA porta (`/users/from-template/{id}`, a rota da contratacao delegada)
#       obedece a mesma regra: 422 sem time, 201 com time, e o criador edita
#
# ⚠️ **P1 e o ramo que impede o verde errado**, e nao e formalidade: um servidor que
# recusasse toda criacao deixaria N1/N2/N3 verdes com a contratacao quebrada — e
# contratar e o que o supervisor faz todo dia (ADR de delegacao, ordem G1->G2->G3).
# Repare que P1 confere QUATRO coisas: criar nao basta, o ponto da ficha e o que vem
# depois.
#
# POR QUE ELE EXISTE (AUT-44, 2026-09-10)
# ---------------------------------------
# Residuo nomeado da AUT-39, que fez do organograma o eixo de administracao. A criacao
# nao punha ninguem em grupo, entao — medido ao vivo, com `supervisor@`:
#
#     1 · cria .................. HTTP 201
#     2 · aparece na lista dele .. NAO (1 usuario visivel: ele mesmo)
#     3 · edita .................. HTTP 403
#     4 · ve a ficha ............. HTTP 403
#
# A conta some da vista de quem acabou de emiti-la, e o unico acesso que sobra ao
# criador e a senha que ele proprio digitou. Criar e nao administrar nao e contratar:
# e produzir ORFAO.
#
# Das tres saidas que a ficha listava venceu **(a) declarar o grupo**: (b) proveniencia
# seria um SEGUNDO eixo ao lado do organograma recem-escolhido, e nele o delegado
# montaria um time invisivel para quem esta acima; (c) *"delegado nao cria"* contraria a
# ordem inegociavel do ADR de delegacao.
#
# ⚠️ O delegado NAO se declara supervisor sozinho (isso e CONCEDER escopo, exige
# `config.permissions`; medido: 403). Organograma e do admin, contratacao e do
# supervisor — e por isso o setup deste gate precisa do admin.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail
cd "$(dirname "$0")/../.." || { echo "INCONCLUSIVO: raiz do repo nao encontrada"; exit 2; }

A="${AUTH:-http://localhost:3202/auth}"
TENANT="${TENANT:-tenant_demo}"
ADMIN_EMAIL="${HIRE_ADMIN_EMAIL:-admin@plughub.local}"
ADMIN_PASS="${HIRE_ADMIN_PASS:-changeme_admin}"
DELEG_EMAIL="${HIRE_DELEG_EMAIL:-supervisor@plughub.local}"
DELEG_PASS="${HIRE_DELEG_PASS:-changeme_supervisor}"

FALHOU=0
ok()  { printf '  \033[32mOK\033[0m           %s\n' "$1"; }
bad() { printf '  \033[31mFALHA\033[0m        %s\n' "$1"; FALHOU=1; }
inc() { printf '  \033[33mINCONCLUSIVO\033[0m %s\n' "$1"; exit 2; }
sec() { printf '\n\033[1m%s\033[0m\n' "$1"; }

command -v jq >/dev/null || inc "jq ausente"

login() {
  curl -s --max-time 15 -X POST "$A/login" -H 'content-type: application/json' \
    -d "{\"email\":\"$1\",\"password\":\"$2\",\"tenant_id\":\"$TENANT\"}" | jq -r '.access_token // empty'
}

printf '\033[1mgate: contratar e contratar PARA UM TIME\033[0m\n'

TA="$(login "$ADMIN_EMAIL" "$ADMIN_PASS")"; [ -n "$TA" ] || inc "login de $ADMIN_EMAIL falhou"
TD="$(login "$DELEG_EMAIL" "$DELEG_PASS")"; [ -n "$TD" ] || inc "login de $DELEG_EMAIL falhou"
SUB_D="$(echo "$TD" | cut -d. -f2 | tr '_-' '/+' | base64 -d 2>/dev/null | jq -r '.sub // empty')"
[ -n "$SUB_D" ] || inc "nao consegui ler o sub de $DELEG_EMAIL"

# O delegado precisa mesmo ser delegado: com `admin` no papel, os ramos N passariam
# pelo caminho universal e o gate mediria outra proposicao.
if echo "$TD" | cut -d. -f2 | tr '_-' '/+' | base64 -d 2>/dev/null | jq -e '.roles | index("admin")' >/dev/null 2>&1; then
  inc "$DELEG_EMAIL tem papel admin — nao serve de delegado neste gate"
fi

MARCA="aut44gate_$$_$(date +%s)"
GID_MEU=""; GID_ALHEIO=""; IDS_CRIADOS=""

TPL_ID=""

limpeza() {
  for uid in $IDS_CRIADOS; do
    curl -s -o /dev/null -X DELETE "$A/users/$uid" -H "Authorization: Bearer $TA"
  done
  for gid in $GID_MEU $GID_ALHEIO; do
    curl -s -o /dev/null -X DELETE "$A/v1/groups/$gid" -H "Authorization: Bearer $TA"
  done
  [ -n "$TPL_ID" ] && curl -s -o /dev/null -X DELETE "$A/templates/$TPL_ID" -H "Authorization: Bearer $TA"
}
trap limpeza EXIT

cria_grupo() {  # $1=nome -> group_id
  curl -s --max-time 15 -X POST "$A/v1/groups" -H "Authorization: Bearer $TA" \
    -H 'content-type: application/json' \
    -d "{\"tenant_id\":\"$TENANT\",\"name\":\"$1\"}" | jq -r '.group_id // empty'
}

GID_MEU="$(cria_grupo "$MARCA meu time")"
GID_ALHEIO="$(cria_grupo "$MARCA time alheio")"
[ -n "$GID_MEU" ] && [ -n "$GID_ALHEIO" ] || inc "nao consegui criar os grupos de teste"
curl -s -o /dev/null -X POST "$A/v1/groups/$GID_MEU/supervisors" -H "Authorization: Bearer $TA" \
  -H 'content-type: application/json' -d "{\"user_id\":\"$SUB_D\"}"
echo "  setup: $DELEG_EMAIL supervisiona ${GID_MEU:0:8}… e NAO supervisiona ${GID_ALHEIO:0:8}…"

# ⚠️ o token do delegado tem de ser REEMITIDO: `supervised_groups` viaja no JWT, e o
# emitido antes do setup carrega a lista antiga. (O servidor le do banco, mas um gate
# que dependesse disso mediria a coisa certa por acidente.)
TD="$(login "$DELEG_EMAIL" "$DELEG_PASS")"

# $1=token $2=email $3=json de group_ids -> ecoa "codigo|id|detail"
cria_usuario() {
  local corpo cod id det
  corpo="$(jq -nc --arg t "$TENANT" --arg e "$2" --argjson g "$3" \
    '{tenant_id:$t, email:$e, name:"AUT-44 gate", password:"aut44_gate_123",
      roles:["operator"], accessible_pools:[], group_ids:$g}')"
  cod="$(curl -s -o /tmp/h44.json -w '%{http_code}' --max-time 20 -X POST "$A/users" \
        -H "Authorization: Bearer $1" -H 'content-type: application/json' -d "$corpo")"
  id="$(jq -r '.id // empty' /tmp/h44.json 2>/dev/null)"
  det="$(jq -r '.detail // empty' /tmp/h44.json 2>/dev/null | head -c 120)"
  [ -n "$id" ] && IDS_CRIADOS="$IDS_CRIADOS $id"
  printf '%s|%s|%s' "$cod" "$id" "$det"
}

existe_conta() {  # $1=email -> sim/nao  (lido com o token do ADMIN, que ve todos)
  curl -s --max-time 15 "$A/users?tenant_id=$TENANT" -H "Authorization: Bearer $TA" \
    | jq -e --arg e "$1" 'map(.email) | index($e) != null' >/dev/null 2>&1 && echo sim || echo nao
}

# ── N1 ───────────────────────────────────────────────────────────────────────
sec "N1 — delegado cria SEM grupo"
E1="$MARCA-n1@plughub.local"
R="$(cria_usuario "$TD" "$E1" '[]')"
COD="${R%%|*}"; DET="${R##*|}"
if [ "$COD" = "422" ]; then
  ok "422 recusado, e a mensagem diz o que fazer: ${DET:0:90}…"
else
  bad "HTTP $COD (esperava 422) — a criacao sem time voltou a passar"
fi
[ "$(existe_conta "$E1")" = "nao" ] && ok "nenhuma conta ficou para tras" \
  || bad "a conta $E1 EXISTE apos a recusa — recusar depois de criar deixa o orfao de pe"

# ── N2 ───────────────────────────────────────────────────────────────────────
sec "N2 — delegado cria em time ALHEIO"
E2="$MARCA-n2@plughub.local"
R="$(cria_usuario "$TD" "$E2" "$(jq -nc --arg g "$GID_ALHEIO" '[$g]')")"
COD="${R%%|*}"; DET="${R##*|}"
if [ "$COD" = "403" ]; then
  ok "403 recusado, nomeando: ${DET:0:90}…"
else
  bad "HTTP $COD (esperava 403) — contratar para time alheio cria alguem que o autor nao administra"
fi
[ "$(existe_conta "$E2")" = "nao" ] && ok "nenhuma conta ficou para tras" \
  || bad "a conta $E2 EXISTE apos a recusa"

# ── P1 — o ramo que importa ──────────────────────────────────────────────────
sec "P1 — delegado cria DENTRO do time dele, e administra o que criou"
E3="$MARCA-p1@plughub.local"
R="$(cria_usuario "$TD" "$E3" "$(jq -nc --arg g "$GID_MEU" '[$g]')")"
COD="${R%%|*}"; ID3="$(printf '%s' "$R" | cut -d'|' -f2)"
if [ "$COD" = "201" ] && [ -n "$ID3" ]; then
  ok "201 criado dentro de ${GID_MEU:0:8}…"

  C="$(curl -s -o /dev/null -w '%{http_code}' "$A/users/$ID3" -H "Authorization: Bearer $TD")"
  [ "$C" = "200" ] && ok "o criador VE a ficha (200)" || bad "GET da ficha -> $C (era 403 antes da AUT-44; segue fora do alcance dele)"

  C="$(curl -s -o /dev/null -w '%{http_code}' -X PATCH "$A/users/$ID3" -H "Authorization: Bearer $TD" \
      -H 'content-type: application/json' -d '{"name":"AUT-44 editado pelo criador"}')"
  [ "$C" = "200" ] && ok "o criador EDITA (200)" || bad "PATCH -> $C (era 403 antes da AUT-44)"

  curl -s --max-time 15 "$A/users?tenant_id=$TENANT" -H "Authorization: Bearer $TD" \
    | jq -e --arg e "$E3" 'map(.email) | index($e) != null' >/dev/null 2>&1 \
    && ok "e a pessoa APARECE na lista de quem a criou" \
    || bad "o criado nao aparece na lista do criador — a conta some da vista de quem a emitiu"
else
  bad "HTTP $COD — o delegado nao consegue mais contratar nem no proprio time; o gate fechou a porta que existia para ele"
fi

# ── P2 ───────────────────────────────────────────────────────────────────────
sec "P2 — admin continua criando SEM grupo"
E4="$MARCA-p2@plughub.local"
R="$(cria_usuario "$TA" "$E4" '[]')"
COD="${R%%|*}"
[ "$COD" = "201" ] && ok "201 — o caminho universal do admin nao morreu" \
  || bad "HTTP $COD — exigir grupo do admin trancaria o dono do lado de fora num tenant com zero grupos"

# ── N3 ───────────────────────────────────────────────────────────────────────
sec "N3 — admin cita grupo INEXISTENTE"
E5="$MARCA-n3@plughub.local"
R="$(cria_usuario "$TA" "$E5" '["00000000-0000-0000-0000-000000000000"]')"
COD="${R%%|*}"; DET="${R##*|}"
[ "$COD" = "422" ] && ok "422 — vinculo a grupo que nao existe e recusado, nao ignorado: ${DET:0:70}…" \
  || bad "HTTP $COD (esperava 422) — o chamador acreditaria ter vinculado"

# ── P3 — a segunda porta ─────────────────────────────────────────────────────
sec "P3 — a rota da contratacao DELEGADA (from-template) segue a mesma regra"
TPL_ID="$(curl -s --max-time 15 -X POST "$A/templates" -H "Authorization: Bearer $TA" \
  -H 'content-type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"name\":\"$MARCA tpl\",\"config\":{\"role\":\"operator\",\"module_config\":{}}}" \
  | jq -r '.id // empty')"
if [ -z "$TPL_ID" ]; then
  printf '  \033[33mSEM AMOSTRA\033[0m  nao consegui criar o template — a segunda porta nao foi exercida\n'
else
  tpl_cria() {  # $1=email $2=json de group_ids -> "codigo|id"
    local corpo cod id
    corpo="$(jq -nc --arg t "$TENANT" --arg e "$1" --argjson g "$2" \
      '{tenant_id:$t, email:$e, name:"AUT-44 gate tpl", password:"aut44_gate_123",
        accessible_pools:[], group_ids:$g}')"
    cod="$(curl -s -o /tmp/h44t.json -w '%{http_code}' --max-time 20 \
          -X POST "$A/users/from-template/$TPL_ID" -H "Authorization: Bearer $TD" \
          -H 'content-type: application/json' -d "$corpo")"
    id="$(jq -r '.id // empty' /tmp/h44t.json 2>/dev/null)"
    [ -n "$id" ] && IDS_CRIADOS="$IDS_CRIADOS $id"
    printf '%s|%s' "$cod" "$id"
  }

  R="$(tpl_cria "$MARCA-tpl-n@plughub.local" '[]')"
  [ "${R%%|*}" = "422" ] && ok "sem time -> 422 tambem pela porta do template" \
    || bad "sem time -> HTTP ${R%%|*} pela rota from-template: a regra vale numa porta e nao na outra"

  R="$(tpl_cria "$MARCA-tpl-p@plughub.local" "$(jq -nc --arg g "$GID_MEU" '[$g]')")"
  IDT="$(printf '%s' "$R" | cut -d'|' -f2)"
  if [ "${R%%|*}" = "201" ] && [ -n "$IDT" ]; then
    ok "com time -> 201 (a contratacao por template continua funcionando)"
    C="$(curl -s -o /dev/null -w '%{http_code}' -X PATCH "$A/users/$IDT" -H "Authorization: Bearer $TD" \
        -H 'content-type: application/json' -d '{"name":"AUT-44 tpl editado"}')"
    [ "$C" = "200" ] && ok "e o criador administra quem nasceu do template (200)" \
      || bad "PATCH -> $C: pela porta do template o criado continua fora do alcance de quem o criou"
  else
    bad "com time -> HTTP ${R%%|*}: a rota que existe para o delegado parou de contratar"
  fi
fi

sec "limpeza"
limpeza; trap - EXIT
N_G="$(curl -s "$A/v1/groups?tenant_id=$TENANT" -H "Authorization: Bearer $TA" | jq 'length')"
echo "  grupos do tenant apos a limpeza: $N_G"

printf '\n'
[ "$FALHOU" = 0 ] && { printf '\033[32mVERDE\033[0m — quem contrata administra quem contratou.\n'; exit 0; }
printf '\033[31mVERMELHO\033[0m\n'; exit 1
