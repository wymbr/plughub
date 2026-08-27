#!/usr/bin/env bash
# ==============================================================================
# probe_unrestricted_claim.sh — Passo 2 do plano `accessible_pools` (2026-08-27)
# ==============================================================================
#
# O QUE ESTE GATE PROVA
# ---------------------
# Hoje "accessible_pools == []" significa "todos os pools" — convencao IMPLICITA,
# lida por sete tradutores em servicos diferentes. O passo 3 inverte esse
# significado para "nenhum pool". Sem uma forma EXPLICITA de dizer "este usuario
# nao tem recorte", a inversao apaga o acesso de quem depende da convencao, em
# silencio. O claim "unrestricted: true" e essa forma.
#
# O QUE O DEIXARIA VERMELHO
# -------------------------
#   S1  o claim nao ser cunhado no login;
#   S2  o claim ser cunhado SO no login e nao no refresh — o modo de falha classico
#       de claim novo: funciona por uma hora e degrada mudo na renovacao;
#   S3  nao existir principal irrestrito POR DECLARACAO. (Medido em 2026-08-27: o
#       admin do demo tem 22 pools EXPLICITOS, nao a lista vazia — nunca dependeu do
#       legado, e como o ramo restritivo vence a lista, conceder-lhe o claim seria
#       INERTE. Irrestrito exige as duas coisas: unrestricted=true E lista vazia.);
#   S4  a declaracao nao ter DENTES (irrestrito nao ver mais que o admin escopado),
#       ou o filtro de pool ter parado de valer. Sao DUAS proposicoes e dois numeros:
#       sem a segunda, a primeira passaria num mundo onde o filtro nao roda. O caso
#       'irrestrito == admin' e ramo PROPRIO (INCONCLUSIVO): 'nao ha dado fora do
#       escopo do admin nesta janela' nao e 'a declaracao nao vale'.
#
# S5 nao reprova: e o INVENTARIO do ramo legado, insumo do passo 3.
#
# INCONCLUSIVO e ramo proprio e conta como falha do INSTRUMENTO (fail=1) — um gate
# que sai 0 sem ter medido compra confianca sem dar nada.
# ==============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/_auth.sh"

ANALYTICS="${ANALYTICS:-http://localhost:3500}"
SUP_EMAIL="${SUP_EMAIL:-supervisor@plughub.local}"
SUP_PASS="${SUP_PASS:-changeme_supervisor}"

fail=0
ok()   { printf '  \033[32mOK\033[0m           %s\n' "$1"; }
bad()  { printf '  \033[31mFALHA\033[0m        %s\n' "$1"; fail=1; }
inc()  { printf '  \033[33mINCONCLUSIVO\033[0m %s\n' "$1"; fail=1; }
info() { printf '               %s\n' "$1"; }
sec()  { printf '\n\033[1m%s\033[0m\n' "$1"; }

# Payload de um JWT (base64url). Sem verificar assinatura: aqui so interessa
# QUAIS claims viajaram, e a verificacao e do servico, nao do probe.
jwt_payload() {
  local p="${1#*.}"
  p="${p%%.*}"
  p="$(printf '%s' "$p" | tr '_-' '/+')"
  case $(( ${#p} % 4 )) in 2) p="$p==";; 3) p="$p=";; esac
  printf '%s' "$p" | base64 -d 2>/dev/null
}

login_json() {  # $1=email $2=senha
  curl -s -X POST "$AUTH/login" -H 'content-type: application/json' \
    -d "{\"email\":\"$1\",\"password\":\"$2\",\"tenant_id\":\"$TENANT\"}"
}

# ─────────────────────────────────────────────────────────────────────────────
sec "S1 - o claim e CUNHADO no login"

LJ="$(login_json "$_PH_EMAIL" "$_PH_PASS")"
ACCESS="$(printf '%s' "$LJ" | jq -r '.access_token // empty')"
REFRESH="$(printf '%s' "$LJ" | jq -r '.refresh_token // empty')"

if [ -z "$ACCESS" ]; then
  inc "login de $_PH_EMAIL falhou — sem token nao ha o que medir: $(printf '%s' "$LJ" | head -c 160)"
else
  PAY="$(jwt_payload "$ACCESS")"
  # Testemunha de presenca: se accessible_pools tambem sumir, quem falhou foi o
  # decode, nao a cunhagem. Sem ela, "chave ausente" e indistinguivel de "parse quebrou".
  if ! printf '%s' "$PAY" | jq -e 'has("accessible_pools")' >/dev/null 2>&1; then
    inc "payload sem nem accessible_pools — o decode e que falhou, nao o claim"
  elif printf '%s' "$PAY" | jq -e 'has("unrestricted")' >/dev/null 2>&1; then
    ok "claim unrestricted presente no access token do login"
  else
    bad "claim unrestricted AUSENTE no login (a testemunha accessible_pools esta la)"
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
sec "S2 - o claim sobrevive ao REFRESH"
info "ramo que degrada mudo: token vive 1h, entao um claim so-no-login funciona"
info "a tarde inteira e some na renovacao."

if [ -z "${REFRESH:-}" ]; then
  inc "login nao devolveu refresh_token"
else
  RJ="$(curl -s -X POST "$AUTH/refresh" -H 'content-type: application/json' \
        -d "{\"refresh_token\":\"$REFRESH\"}")"
  RACC="$(printf '%s' "$RJ" | jq -r '.access_token // empty')"
  if [ -z "$RACC" ]; then
    inc "refresh falhou: $(printf '%s' "$RJ" | head -c 160)"
  elif printf '%s' "$(jwt_payload "$RACC")" | jq -e 'has("unrestricted")' >/dev/null 2>&1; then
    ok "claim presente tambem no token renovado"
  else
    bad "claim no login e AUSENTE no refresh — cunhagem so num dos dois caminhos"
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
sec "S3 - existe um principal IRRESTRITO POR DECLARACAO"
info "medido em 2026-08-27: o admin do demo tem 22 pools EXPLICITOS, nao a lista"
info "vazia — logo ele nunca dependeu do legado, e com a ordem restritivo-vence"
info "conceder o claim a ele seria INERTE. Irrestrito de verdade exige as duas"
info "coisas: unrestricted=true E lista vazia."

PROBE_EMAIL="${PROBE_EMAIL:-probe@plughub.local}"
PROBE_PASS="${PROBE_PASS:-changeme_probe}"

PJ="$(login_json "$PROBE_EMAIL" "$PROBE_PASS")"
PROBE_TOK="$(printf '%s' "$PJ" | jq -r '.access_token // empty')"

if [ -z "$PROBE_TOK" ]; then
  bad "principal irrestrito ($PROBE_EMAIL) nao existe ou nao autentica"
  info "criar com: bash infra/test/mk_unrestricted_principal.sh   (ou POST $AUTH/users com"
  info "           accessible_pools=[] e unrestricted=true)"
else
  PP="$(jwt_payload "$PROBE_TOK")"
  PU="$(printf '%s' "$PP" | jq -r 'if has("unrestricted") then (.unrestricted|tostring) else "ausente" end')"
  PN="$(printf '%s' "$PP" | jq -r '.accessible_pools | length')"
  if [ "$PU" = "true" ] && [ "$PN" = "0" ]; then
    ok "$PROBE_EMAIL declara unrestricted=true com lista vazia"
  elif [ "$PU" != "true" ]; then
    bad "$PROBE_EMAIL tem unrestricted=$PU — nao e principal irrestrito"
  else
    bad "$PROBE_EMAIL declara unrestricted=true mas lista $PN pools: o restritivo"
    info "vence, entao a declaracao fica inerte. Zerar accessible_pools."
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
sec "S4 - a declaracao tem DENTES, e o escopo AINDA vale"
info "duas proposicoes distintas, dois numeros — sem a segunda, a primeira passaria"
info "num mundo onde o filtro simplesmente nao roda."

count_pools() {  # $1 = token -> pools DISTINTOS visiveis na serie
  curl -s "$ANALYTICS/reports/pools/queue?tenant_id=$TENANT" \
       -H "Authorization: Bearer $1" \
    | jq -r 'try (.data.series | map(.pool_id) | unique | length) catch -1' 2>/dev/null
}

N_ADMIN="$(count_pools "${ACCESS:-x}")"
N_PROBE="$(count_pools "${PROBE_TOK:-x}")"
SUP_TOK="$(login_json "$SUP_EMAIL" "$SUP_PASS" | jq -r '.access_token // empty')"
N_SUP=""
[ -n "$SUP_TOK" ] && N_SUP="$(count_pools "$SUP_TOK")"

info "pools distintos: irrestrito=$N_PROBE  admin(22 pools)=$N_ADMIN  supervisor=${N_SUP:-?}"

bad_shape() { case "${1:-}" in ""|-1|null) return 0;; *) return 1;; esac; }

if bad_shape "$N_PROBE" || bad_shape "$N_ADMIN"; then
  inc "/reports/pools/queue nao devolveu .data.series - forma inesperada"
elif [ "$N_PROBE" -gt "$N_ADMIN" ]; then
  ok "irrestrito ve MAIS que o admin escopado ($N_PROBE > $N_ADMIN) - a declaracao foi exercida"
elif [ "$N_PROBE" -lt "$N_ADMIN" ]; then
  bad "irrestrito ve MENOS que o admin ($N_PROBE < $N_ADMIN) - a declaracao esta ESTREITANDO"
else
  # Ramo proprio, e nao e o mesmo que reprovar: 'nao ha dado fora do escopo do
  # admin nesta janela' e diferente de 'a declaracao nao vale'. Exposicao e dano
  # sao grandezas separadas; declarar OK aqui seria afirmar o que nao se mediu.
  inc "irrestrito e admin veem o MESMO ($N_PROBE) - nenhum dado fora dos 22 pools do"
  info "admin nesta janela, entao a declaracao existe mas NAO foi exercida."
  info "Alargue a janela do relatorio e repita antes de concluir qualquer coisa."
fi

if bad_shape "${N_SUP:-}"; then
  inc "login/resposta do supervisor ($SUP_EMAIL) falhou - sem o escopado nao ha testemunha"
elif [ "$N_SUP" -lt "$N_ADMIN" ]; then
  ok "escopado ve MENOS ($N_SUP < $N_ADMIN) - o filtro de pool esta rodando"
else
  bad "escopado ve $N_SUP e admin ve $N_ADMIN - o filtro NAO esta separando"
fi

# ─────────────────────────────────────────────────────────────────────────────
sec "S5 - INVENTARIO do ramo legado (mede, nao reprova)"
info "cada linha aqui e um portador da convencao implicita [] = todos, e e"
info "exatamente a lista que o passo 3 precisa ter antes de inverter."

LOG="$(cd "$ROOT" && docker compose -f docker-compose.demo.yml logs --since 5m analytics-api 2>/dev/null)"
if [ -z "$LOG" ]; then
  info "(logs do analytics-api indisponiveis nesta execucao — inventario nao medido)"
else
  N_LEG="$(printf '%s' "$LOG" | grep -c 'LEGADO_POOLS_VAZIO' || true)"
  info "ocorrencias nos ultimos 5 min: $N_LEG"
  if [ "${N_LEG:-0}" -gt 0 ]; then
    printf '%s' "$LOG" | grep -o 'claim_presente=[A-Za-z]* sub=[^ ]*' | sort | uniq -c | sed 's/^/               /'
    info "claim_presente=False -> token velho, ou emissor que nao conhece o claim"
    info "claim_presente=True  -> usuario sem escopo declarado: DECISAO de alguem"
  fi
fi

printf '\n'
if [ "$fail" -eq 0 ]; then
  printf '\033[32mGATE VERDE\033[0m - irrestrito e declarado, o refresh concorda, o escopo vale.\n'
else
  printf '\033[31mGATE VERMELHO\033[0m - ver secoes acima.\n'
fi
exit "$fail"
