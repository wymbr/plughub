#!/usr/bin/env bash
# ==============================================================================
# probe_config_permissions_split.sh — administrar PESSOA != conceder CAPACIDADE
# ==============================================================================
#
# O QUE ELE PROVA
# ---------------
# Ate 2026-08-27 `config.users` era a chave-mestra do tenant: um unico campo cobria
# "criar/editar usuario" E "conceder papel, modulo e escopo de pool". Consequencia
# medida: quem recebesse esse campo para administrar a operacao podia, na mesma tela,
# marcar `skill_flows.operacao` em si mesmo, trocar o proprio papel para admin, ligar
# `unrestricted` — ou simplesmente redefinir a senha do admin e entrar como ele.
# Toda fronteira ABAC do produto colapsava em "tem config.users".
#
# O split criou `config.permissions`. Este probe existe porque um split pode ser
# DECORATIVO: se so as rotas `/permissions` mudassem de campo, bastaria
# `PATCH /users/{id}` com `roles: ["admin"]` para escalar pela porta de administracao.
# Por isso o veredicto exercita as TRES portas: a rota, o CORPO e o ALVO.
#
# O QUE O DEIXARIA VERMELHO
# -------------------------
#   S2  criar usuario JA CONCEDENDO papel, sem `config.permissions`
#   S4  editar usuario mandando campo de capacidade no corpo
#   S5  escrever o `module_config` de alguem
#   S6  tocar um usuario PRIVILEGIADO (o vetor da senha: trocar a senha do admin e
#       campo de PESSOA, permitido — o que barra e a protecao do ALVO)
#   S8  auto-nomear-se SUPERVISOR de um grupo (concede escopo pelo claim
#       `supervised_user_ids`, que a evaluation-api consome)
#
# TESTEMUNHAS DE PRESENCA (sem elas, "tudo 403" seria lido como sucesso quando na
# verdade o endpoint quebrou para todo mundo):
#   S1  o mesmo principal CRIA usuario quando NAO concede nada
#   S3  o mesmo principal EDITA o nome desse usuario
#   S7  o admin (que tem `config.permissions`) escreve o module_config normalmente
#   S9  o mesmo principal adiciona MEMBRO a um grupo (organograma segue funcionando)
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/_auth.sh"

UA_EMAIL="${UA_EMAIL:-useradmin@plughub.local}"
UA_PASS="${UA_PASS:-changeme_useradmin}"
ALVO_EMAIL="${ALVO_EMAIL:-probe_target@plughub.local}"
ALVO_PASS="changeme_probe_target"

fail=0
ok()   { printf '  \033[32mOK\033[0m           %s\n' "$1"; }
bad()  { printf '  \033[31mFALHA\033[0m        %s\n' "$1"; fail=1; }
inc()  { printf '  \033[33mINCONCLUSIVO\033[0m %s\n' "$1"; fail=1; }
info() { printf '               %s\n' "$1"; }
sec()  { printf '\n\033[1m%s\033[0m\n' "$1"; }

command -v jq >/dev/null || { inc "jq ausente"; exit 2; }

login() { curl -s -X POST "$AUTH/login" -H 'content-type: application/json' \
          -d "{\"email\":\"$1\",\"password\":\"$2\",\"tenant_id\":\"$TENANT\"}" \
          | jq -r '.access_token // empty'; }

# $1 = token, $2 = metodo, $3 = path, $4 = corpo (opcional) -> ecoa o status HTTP
st() {
  local tok="$1" m="$2" p="$3" b="${4:-}"
  if [ -n "$b" ]; then
    curl -s -o /dev/null -w '%{http_code}' --max-time 15 -X "$m" "$AUTH$p" \
      -H "Authorization: Bearer $tok" -H 'content-type: application/json' -d "$b"
  else
    curl -s -o /dev/null -w '%{http_code}' --max-time 15 -X "$m" "$AUTH$p" \
      -H "Authorization: Bearer $tok"
  fi
}

uid_de() {  # $1 = token admin, $2 = email
  curl -s --max-time 15 "$AUTH/users?tenant_id=$TENANT" -H "Authorization: Bearer $1" \
    | jq -r --arg e "$2" '.[] | select(.email == $e) | .id' | head -1
}

printf '\033[1mprobe: config.users x config.permissions\033[0m\n'
printf '  auth-api: %s   tenant: %s\n' "$AUTH" "$TENANT"

T_ADMIN="$(plughub_token)"

# ── setup: o principal que SO administra pessoas ─────────────────────────────
sec "setup - principal com config.users e SEM config.permissions"
UA_ID="$(uid_de "$T_ADMIN" "$UA_EMAIL")"
if [ -z "$UA_ID" ]; then
  UA_ID="$(curl -s --max-time 15 -X POST "$AUTH/users" \
    -H "Authorization: Bearer $T_ADMIN" -H 'content-type: application/json' \
    -d "{\"tenant_id\":\"$TENANT\",\"email\":\"$UA_EMAIL\",\"name\":\"Probe UserAdmin\",\"password\":\"$UA_PASS\",\"roles\":[\"supervisor\"]}" \
    | jq -r '.id // empty')"
  [ -z "$UA_ID" ] && { inc "nao consegui criar $UA_EMAIL"; exit 2; }
  info "criado $UA_EMAIL"
else
  info "$UA_EMAIL ja existe"
fi
# papel `supervisor` de proposito: se algum bypass de papel sobreviver, ele aparece
# aqui como um 2xx onde o teste espera 403.
S="$(st "$T_ADMIN" PUT "/users/$UA_ID/module-config" \
     '{"config":{"users":{"access":"read_write","scope":[]}}}')"
[ "$S" = "200" ] || { inc "nao consegui definir o module_config de $UA_EMAIL (HTTP $S)"; exit 2; }

T_UA="$(login "$UA_EMAIL" "$UA_PASS")"
[ -z "$T_UA" ] && { inc "login de $UA_EMAIL falhou"; exit 2; }
HAS_PERMS="$(printf '%s' "$T_UA" | cut -d. -f2 | tr '_-' '/+' \
  | awk '{n=length($0)%4; if(n==2)$0=$0"=="; else if(n==3)$0=$0"="; print}' \
  | base64 -d 2>/dev/null | jq -r '.module_config.config.permissions.access // "none"')"
if [ "$HAS_PERMS" != "none" ]; then
  inc "o token de $UA_EMAIL declara config.permissions=$HAS_PERMS — o experimento nao existe"
  info "este probe precisa de um principal que administre pessoas e NAO conceda."
  exit 2
fi
ok "token de $UA_EMAIL: config.users concedido, config.permissions ausente"

# ── S1/S2 — criar: sem capacidade passa, com capacidade recusa ───────────────
sec "S1/S2 - criar usuario"
OLD_ID="$(uid_de "$T_ADMIN" "$ALVO_EMAIL")"
[ -n "$OLD_ID" ] && curl -s -o /dev/null -X DELETE "$AUTH/users/$OLD_ID" \
  -H "Authorization: Bearer $T_ADMIN"

C1="$(st "$T_UA" POST "/users" \
  "{\"tenant_id\":\"$TENANT\",\"email\":\"$ALVO_EMAIL\",\"name\":\"Probe Target\",\"password\":\"$ALVO_PASS\"}")"
if [ "$C1" = "200" ] || [ "$C1" = "201" ]; then
  ok "S1 criou usuario SEM campo de capacidade (HTTP $C1) — testemunha de presenca"
else
  bad "S1 nao conseguiu criar usuario simples (HTTP $C1)"
  info "Sem esta testemunha os 403 abaixo nao provam nada: seriam indistinguiveis"
  info "de 'o endpoint parou de responder para este principal'."
fi

C2="$(st "$T_UA" POST "/users" \
  "{\"tenant_id\":\"$TENANT\",\"email\":\"probe_escalate@plughub.local\",\"name\":\"X\",\"password\":\"changeme_escalate\",\"roles\":[\"admin\"]}")"
if [ "$C2" = "403" ]; then
  ok "S2 criar JA CONCEDENDO roles=[admin] recusado (403)"
else
  bad "S2 criou/aceitou usuario admin sem config.permissions (HTTP $C2)"
  info "Este e o caminho curto de escalacao: criar um admin em vez de virar um."
  EID="$(uid_de "$T_ADMIN" "probe_escalate@plughub.local")"
  [ -n "$EID" ] && curl -s -o /dev/null -X DELETE "$AUTH/users/$EID" -H "Authorization: Bearer $T_ADMIN"
fi

ALVO_ID="$(uid_de "$T_ADMIN" "$ALVO_EMAIL")"
[ -z "$ALVO_ID" ] && { inc "o usuario-alvo nao existe — S3..S7 nao tem sobre o que rodar"; exit 2; }

# ── S3/S4/S5 — editar: pessoa passa, capacidade recusa ───────────────────────
sec "S3/S4/S5 - editar usuario e conceder"
C3="$(st "$T_UA" PATCH "/users/$ALVO_ID" '{"name":"Probe Target Renomeado"}')"
if [ "$C3" = "200" ]; then
  ok "S3 editou o NOME (200) — administrar pessoa continua funcionando"
else
  bad "S3 nao conseguiu editar o nome (HTTP $C3) — o split quebrou quem PODE administrar"
fi

C4="$(st "$T_UA" PATCH "/users/$ALVO_ID" '{"roles":["admin"]}')"
if [ "$C4" = "403" ]; then
  ok "S4 PATCH com roles no corpo recusado (403)"
else
  bad "S4 aceitou trocar o papel sem config.permissions (HTTP $C4)"
  info "O split seria DECORATIVO: as rotas /permissions mudaram, e a porta de"
  info "administracao continuaria concedendo."
fi

C4b="$(st "$T_UA" PATCH "/users/$ALVO_ID" '{"unrestricted":true}')"
if [ "$C4b" = "403" ]; then
  ok "S4b PATCH com unrestricted recusado (403)"
else
  bad "S4b ligou o claim irrestrito sem config.permissions (HTTP $C4b)"
fi

# ── S4c — o campo que a oferta de escopo da AUT-10 envia ─────────────────────
# Adicionado em 2026-08-31. O probe cobria `roles` e `unrestricted`, e NAO cobria
# `accessible_pools` — terceiro membro de `_CAPACITY_FIELDS`, e o unico que uma tela
# passou a mandar sozinho: a oferta "incluir o pool novo no meu escopo", logo apos criar
# um pool (AUT-10).
#
# Importa porque criar pool exige `config.resources` e conceder escopo exige
# `config.permissions` — MODULOS DIFERENTES. Sem esta recusa no servidor a oferta viraria
# uma SEGUNDA porta para a mesma decisao, e quem tivesse so `config.resources` se
# autoconcederia escopo criando pools. A guarda da UI apenas evita oferecer um botao que
# daria 403; quem RECUSA e esta linha.
C4c="$(st "$T_UA" PATCH "/users/$ALVO_ID" '{"accessible_pools":["sac_ia"]}')"
if [ "$C4c" = "403" ]; then
  ok "S4c PATCH com accessible_pools recusado (403)"
else
  bad "S4c alterou escopo de pool sem config.permissions (HTTP $C4c)"
  info "Escopo e campo de CAPACIDADE. Se ele passa, a oferta da AUT-10 vira porta larga."
fi

C5="$(st "$T_UA" PUT "/users/$ALVO_ID/module-config" \
  '{"config":{"permissions":{"access":"read_write","scope":[]}}}')"
if [ "$C5" = "403" ]; then
  ok "S5 escrever module_config recusado (403)"
else
  bad "S5 escreveu module_config sem config.permissions (HTTP $C5)"
fi

# ── S6 — o ALVO privilegiado ─────────────────────────────────────────────────
sec "S6 - tocar um usuario que DETEM config.permissions"
ADMIN_ID="$(uid_de "$T_ADMIN" "admin@plughub.local")"
if [ -z "$ADMIN_ID" ]; then
  inc "nao localizei admin@plughub.local — sem alvo privilegiado nao ha experimento"
else
  C6="$(st "$T_UA" PATCH "/users/$ADMIN_ID" '{"password":"trocada_pelo_probe_123"}')"
  if [ "$C6" = "403" ]; then
    ok "S6 redefinir a senha do admin recusado (403)"
  else
    bad "S6 redefiniu a senha de um usuario privilegiado (HTTP $C6)"
    info "Trocar senha e campo de PESSOA, entao a guarda de CORPO nao o pega — quem"
    info "pega e a protecao do ALVO. Sem ela o split nao entrega o que promete:"
    info "bastaria resetar a senha do admin e entrar como admin."
  fi
fi

# ── S7 — testemunha: quem TEM o campo continua concedendo ────────────────────
sec "S7 - testemunha de presenca do lado de quem PODE conceder"
C7="$(st "$T_ADMIN" PUT "/users/$ALVO_ID/module-config" \
  '{"contacts":{"visualizar":{"access":"read_only","scope":[]}}}')"
if [ "$C7" = "200" ]; then
  ok "S7 o admin escreveu module_config (200) — a rota nao esta quebrada, esta gateada"
else
  bad "S7 nem o admin consegue escrever module_config (HTTP $C7)"
  info "Entao os 403 acima medem 'a rota caiu', nao 'o campo separa'."
fi

# ── S8/S9 — a porta dos GRUPOS ───────────────────────────────────────────────
# Nomear supervisor de grupo concede ESCOPO: `resolve_supervisor_scope` deriva
# `supervised_user_ids` dos grupos supervisionados, e a evaluation-api usa esse claim
# para decidir de quem a pessoa ve as avaliacoes. Sob `config.users` isso era
# auto-concessao: crio o grupo, me nomeio supervisor, passo a ver as avaliacoes de quem
# esta nele. Membership FICA em `config.users` (so alcanca grupo que ja se supervisiona).
sec "S8/S9 - grupos: membership x supervisao"
GID="$(curl -s --max-time 15 -X POST "$AUTH/v1/groups" \
  -H "Authorization: Bearer $T_UA" -H 'content-type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"name\":\"probe_split_group\"}" \
  | jq -r '.group_id // empty')"
if [ -z "$GID" ]; then
  inc "nao consegui criar grupo com $UA_EMAIL — sem grupo nao ha experimento"
  info "criar grupo e `config.users` (organograma); se falhou aqui, o split quebrou"
  info "quem administra, e S8/S9 nao chegam a medir a supervisao."
else
  C9="$(st "$T_UA" POST "/v1/groups/$GID/users" "{\"user_id\":\"$ALVO_ID\"}")"
  if [ "$C9" = "200" ] || [ "$C9" = "201" ]; then
    ok "S9 adicionou MEMBRO ao grupo (HTTP $C9) — testemunha de presenca"
  else
    bad "S9 nao conseguiu adicionar membro (HTTP $C9) — organograma deixou de funcionar"
  fi

  C8="$(st "$T_UA" POST "/v1/groups/$GID/supervisors" "{\"user_id\":\"$UA_ID\"}")"
  if [ "$C8" = "403" ]; then
    ok "S8 auto-nomear-se SUPERVISOR do grupo recusado (403)"
  else
    bad "S8 nomeou supervisor sem config.permissions (HTTP $C8)"
    info "Escalacao por escopo: o claim `supervised_user_ids` passa a incluir os"
    info "membros do grupo, e a evaluation-api mostra as avaliacoes deles."
  fi
  curl -s -o /dev/null -X DELETE "$AUTH/v1/groups/$GID" -H "Authorization: Bearer $T_ADMIN"
fi

# ── limpeza ──────────────────────────────────────────────────────────────────
curl -s -o /dev/null -X DELETE "$AUTH/users/$ALVO_ID" -H "Authorization: Bearer $T_ADMIN"

printf '\n'
if [ "$fail" -eq 0 ]; then
  printf '\033[32mVERDE\033[0m - administrar pessoa e conceder capacidade sao campos separados, e ambos funcionam.\n'
else
  printf '\033[31mVERMELHO\033[0m - ver secoes acima.\n'
fi
exit "$fail"
