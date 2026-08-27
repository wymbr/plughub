#!/usr/bin/env bash
# ==============================================================================
# probe_customer_history_authz.sh — o historico do cliente exige credencial e escopa?
# ==============================================================================
#
# O QUE ELE PROVA
# ---------------
# `GET /sessions/customer/{id}` e `.../search` serviam historico de contato chaveado
# por `customer_id` — dado pessoal — a QUALQUER chamador. Medido em 2026-08-27:
# `sessions.py` tinha ZERO ocorrencias de `pool_principal` e o `include_router` nao
# declara dependencia global; os dois respondiam 200 com ou sem token. Nenhum dos
# tres furos fechados no mesmo dia cobria este.
#
# O QUE O DEIXARIA VERMELHO
# -------------------------
#   S1  o endpoint voltar a responder sem credencial (a regressao que importa);
#   S2  responder a um token INVALIDO — o portao existe mas nao verifica;
#   S3  o escopo nao valer: um principal restrito ver o mesmo que o irrestrito;
#   S4  quebrar quem PODE ler — 401/500 para credencial legitima, ou zero linha
#       onde ha historico (a testemunha de presenca: sem ela, "escopo funciona"
#       seria indistinguivel de "o endpoint parou de responder").
#
# INSTRUMENTO: a discriminacao usa token INVALIDO, nao ausencia de header. O portao
# recusa token invalido mesmo com `analytics_open_access` ligado; "sem header"
# devolve 401 por dois motivos diferentes e nao separa "gateado" de "flag fechada".
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/_auth.sh"

AN="${AN:-http://localhost:3500}"
DC="docker compose -f $ROOT/docker-compose.demo.yml"
CH_DB="${CH_DB:-plughub_demo}"
PROBE_EMAIL="${PROBE_EMAIL:-probe@plughub.local}"
PROBE_PASS="${PROBE_PASS:-changeme_probe}"
SUP_EMAIL="${SUP_EMAIL:-supervisor@plughub.local}"
SUP_PASS="${SUP_PASS:-changeme_supervisor}"

fail=0
ok()   { printf '  \033[32mOK\033[0m           %s\n' "$1"; }
bad()  { printf '  \033[31mFALHA\033[0m        %s\n' "$1"; fail=1; }
inc()  { printf '  \033[33mINCONCLUSIVO\033[0m %s\n' "$1"; fail=1; }
info() { printf '               %s\n' "$1"; }
sec()  { printf '\n\033[1m%s\033[0m\n' "$1"; }

login() { curl -s -X POST "$AUTH/login" -H 'content-type: application/json' \
          -d "{\"email\":\"$1\",\"password\":\"$2\",\"tenant_id\":\"$TENANT\"}" \
          | jq -r '.access_token // empty'; }

code() { curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$@"; }
rows() { curl -s --max-time 20 "$1" -H "Authorization: Bearer $2" \
         | jq 'if type=="array" then length else -1 end' 2>/dev/null; }

# ── credenciais ──────────────────────────────────────────────────────────────
T_PROBE="$(login "$PROBE_EMAIL" "$PROBE_PASS")"
[ -z "$T_PROBE" ] && { inc "login do principal irrestrito ($PROBE_EMAIL) falhou"; \
  info "criar com: bash infra/test/mk_unrestricted_principal.sh"; exit 2; }
T_SUP="$(login "$SUP_EMAIL" "$SUP_PASS")"
[ -z "$T_SUP" ] && { inc "login do escopado ($SUP_EMAIL) falhou — sem ele nao ha comparacao"; exit 2; }

# ── alvo DERIVADO da pergunta, nao "o maior" ─────────────────────────────────
# Escolher "o cliente com mais sessoes" seleciona pelo motivo errado: medido em
# 2026-08-27, o maior tinha 21 sessoes TODAS em `limite_ia`, que e um dos pools do
# supervisor — os dois principals viam 21 e o filtro nunca era exercido.
# O alvo certo e um cliente com sessao fechada FORA dos pools do escopado, e esses
# pools sao lidos do TOKEN dele: hardcoda-los reintroduziria a divergencia entre o
# que o gate supoe e o que a credencial de fato carrega.
jwt_payload() {
  local x="${1#*.}"; x="${x%%.*}"
  x="$(printf '%s' "$x" | tr '_-' '/+')"
  case $(( ${#x} % 4 )) in 2) x="$x==";; 3) x="$x=";; esac
  printf '%s' "$x" | base64 -d 2>/dev/null
}
SUP_POOLS="$(jwt_payload "$T_SUP" | jq -r '[.accessible_pools[]? | "\u0027" + . + "\u0027"] | join(",")')"
if [ -z "$SUP_POOLS" ]; then
  inc "o token de $SUP_EMAIL nao declara `accessible_pools` — sem fronteira nao ha experimento"
  info "este gate precisa de um principal ESCOPADO; nao aponte para um irrestrito."
  exit 2
fi
info "pools do escopado (do token): $SUP_POOLS"

CID="$($DC exec -T clickhouse clickhouse-client -q \
  "SELECT customer_id FROM ${CH_DB}.sessions FINAL WHERE tenant_id='$TENANT' \
   AND customer_id != '' AND closed_at IS NOT NULL \
   GROUP BY customer_id \
   HAVING countIf(pool_id NOT IN ($SUP_POOLS)) > 0 \
   ORDER BY count() DESC LIMIT 1" 2>/dev/null | tr -d '\r')"
if [ -z "$CID" ]; then
  inc "nenhum cliente com sessao fechada FORA dos pools do escopado — nao ha fronteira a cruzar"
  info "E fato do DADO, nao veredicto sobre o produto: o filtro pode estar correto e"
  info "simplesmente nao ter o que filtrar neste ambiente."
  info "(se o banco tiver outro nome, passe CH_DB=...)"
  exit 2
fi

printf '\033[1mprobe: historico do cliente — credencial e escopo\033[0m\n'
printf '  analytics-api: %s   tenant: %s   cliente: %s\n' "$AN" "$TENANT" "$CID"

U_HIST="$AN/sessions/customer/$CID?tenant_id=$TENANT&limit=50"
U_SEARCH="$AN/sessions/customer/$CID/search?tenant_id=$TENANT&q=a"

# ── S1/S2 — o portao existe e verifica ───────────────────────────────────────
sec "S1/S2 - o portao existe, e verifica"
for_label() { printf '  %-34s ' "$1"; }

C_ANON_H="$(code "$U_HIST")"
C_ANON_S="$(code "$U_SEARCH")"
C_BAD_H="$(code -H 'Authorization: Bearer lixo.lixo.lixo' "$U_HIST")"
C_BAD_S="$(code -H 'Authorization: Bearer lixo.lixo.lixo' "$U_SEARCH")"
info "historico: anonimo=$C_ANON_H  token-lixo=$C_BAD_H"
info "busca:     anonimo=$C_ANON_S  token-lixo=$C_BAD_S"

if [ "$C_BAD_H" = "401" ] && [ "$C_BAD_S" = "401" ]; then
  ok "token INVALIDO recusado nos dois — o portao verifica, nao so exige"
else
  bad "token invalido devolveu historico=$C_BAD_H busca=$C_BAD_S (esperado 401 nos dois)"
  info "Este e o ramo que discrimina: 401 aqui nao depende da flag de demo."
fi
if [ "$C_ANON_H" = "401" ] && [ "$C_ANON_S" = "401" ]; then
  ok "anonimo recusado nos dois"
else
  bad "anonimo devolveu historico=$C_ANON_H busca=$C_ANON_S"
  info "Se for 200, ou o portao sumiu, ou \`analytics_open_access\` esta ligado"
  info "neste ambiente — os dois merecem investigacao antes de seguir."
fi

# ── S3/S4 — escopo vale, e nao quebra quem pode ler ──────────────────────────
sec "S3/S4 - o escopo vale, e quem PODE ler continua lendo"

N_FULL="$(rows "$U_HIST" "$T_PROBE")"
N_SCOP="$(rows "$U_HIST" "$T_SUP")"
info "linhas do historico: irrestrito=$N_FULL  escopado=$N_SCOP"

if [ "${N_FULL:--1}" = "-1" ]; then
  inc "a leitura irrestrita nao devolveu array — forma inesperada"
elif [ "$N_FULL" -eq 0 ]; then
  # Testemunha de PRESENCA. Sem ela, "escopado ve 0" seria lido como escopo
  # funcionando quando na verdade o endpoint parou de responder.
  bad "o irrestrito viu ZERO linhas para um cliente que o ClickHouse diz ter historico"
  info "O portao esta quebrando quem PODE ler — e este e o modo de falha caro:"
  info "a tela fica vazia e parece 'cliente sem historico'."
elif [ "${N_SCOP:--1}" = "-1" ]; then
  inc "a leitura escopada nao devolveu array"
elif [ "$N_SCOP" -lt "$N_FULL" ]; then
  ok "irrestrito ve $N_FULL e escopado ve $N_SCOP — o escopo esta filtrando"
elif [ "$N_SCOP" -eq "$N_FULL" ]; then
  inc "os dois veem $N_FULL — este cliente nao tem sessao fora do escopo do supervisor,"
  info "entao o filtro existe mas NAO foi exercido. Nao e o mesmo que 'nao vale':"
  info "escolha um cliente cujo historico atravesse pools fora de $SUP_EMAIL."
else
  bad "escopado ve $N_SCOP e irrestrito ve $N_FULL — o restrito viu MAIS que o irrestrito"
fi

# a busca segue o mesmo portao; aqui basta que responda a quem pode
S_FULL="$(rows "$U_SEARCH" "$T_PROBE")"
if [ "${S_FULL:--1}" = "-1" ]; then
  bad "a busca nao devolveu array para credencial legitima"
else
  ok "a busca responde a credencial legitima ($S_FULL sessao(oes) com o termo)"
fi

printf '\n'
if [ "$fail" -eq 0 ]; then
  printf '\033[32mVERDE\033[0m - historico do cliente exige credencial, verifica, escopa e nao quebra quem pode ler.\n'
else
  printf '\033[31mVERMELHO\033[0m - ver secoes acima.\n'
fi
exit "$fail"
