#!/usr/bin/env bash
# ==============================================================================
# probe_unrestricted_claim.sh — INVERTIDO em 2026-08-31 (AUT-15)
# ==============================================================================
#
# ── O que ele media, e por que virou do avesso ────────────────────────────────
#
# Nasceu no passo 2 do plano `accessible_pools` (2026-08-27) para provar que o claim
# `unrestricted` ERA cunhado, que sobrevivia ao refresh, que existia um principal
# irrestrito POR DECLARACAO e que essa declaracao tinha DENTES. Era o pre-requisito da
# inversao do passo 3 (`[]` = NENHUM pool): sem uma forma explicita de dizer "este
# usuario nao tem recorte", a inversao apagaria acesso em silencio.
#
# O passo 3 aconteceu, e o dono decidiu o oposto do pre-requisito: **escopo de pool e
# sempre ENUMERADO**. O claim saiu do token (AUT-13), o ramo saiu do `resolve_scope`
# (AUT-12) e o campo saiu da persistencia e da API (AUT-15).
#
# ⚠️ E o gate ficou VERMELHO acusando o contrario do que acontecia. Ele dizia
# *"irrestrito ve MENOS que o escopado (0 < 2) - a declaracao ESTREITA"*, e a leitura
# obvia — "alguem quebrou o escopo" — era falsa: o principal chamado irrestrito passara
# a ser um principal de escopo VAZIO, porque o `mk_unrestricted_principal.sh` continuava
# criando `unrestricted:true` + lista vazia, arranjo que a AUT-03 converteu em "nenhum
# pool". Instrumento fiel ao proprio ramo, publicando um defeito que nao existe — a
# mesma familia da AUT-27 e da AUT-30, terceira ocorrencia no mesmo dia.
#
# ── O que ele mede AGORA ──────────────────────────────────────────────────────
#
# Apagar deixaria o caminho livre para a porta larga voltar sem nada acusar, que e a
# postura ja aplicada duas vezes hoje. Entao as proposicoes foram INVERTIDAS, e sao as
# tres que a remocao precisa que continuem verdadeiras:
#
#   N1  o token NAO carrega `unrestricted` — nem no login, nem no refresh (o modo de
#       falha classico e o campo voltar por UM dos dois caminhos);
#   N2  a API RECUSA o campo, nomeando (422), em vez de aceitar em silencio: pydantic
#       ignora chave desconhecida, e um 200 sobre no-op e uma concessao que o chamador
#       acredita ter feito;
#   N3  o principal que os gates chamam de "irrestrito" ALCANCA o tenant inteiro —
#       hoje por enumeracao. Sem N3 os outros dois passariam num mundo onde ninguem
#       enxerga nada, que e exatamente o estado em que este arquivo foi encontrado.
#
# Veredicto: 0 = VERDE · 1 = DEFEITO · 2 = INCONCLUSIVO (pre-condicao falhou).
# ==============================================================================
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE/../.." || { echo "INCONCLUSIVO: raiz do repo nao encontrada"; exit 2; }
# shellcheck source=/dev/null
source "$HERE/_auth.sh"

FAIL=0
ok()  { printf '  \033[32mOK\033[0m           %s\n' "$1"; }
bad() { printf '  \033[31mFALHA\033[0m        %s\n' "$1"; FAIL=1; }
inc() { printf '  \033[33mINCONCLUSIVO\033[0m %s\n' "$1"; exit 2; }

command -v jq >/dev/null || inc "jq ausente"

claims() {  # $1 = jwt
  local p; p="$(printf '%s' "$1" | cut -d. -f2)"
  p="$p$(printf '%*s' $(( (4 - ${#p} % 4) % 4 )) '' | tr ' ' '=')"
  printf '%s' "$p" | tr '_-' '/+' | base64 -d 2>/dev/null
}

echo "== probe_unrestricted_claim (INVERTIDO — AUT-15) =="
echo

echo "N1 - o token NAO carrega o claim, nem no login nem no refresh"
LOGIN="$(curl -s -X POST "$AUTH/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$_PH_EMAIL\",\"password\":\"$_PH_PASS\",\"tenant_id\":\"$TENANT\"}")"
ACC="$(printf '%s' "$LOGIN" | jq -r '.access_token // empty')"
REF="$(printf '%s' "$LOGIN" | jq -r '.refresh_token // empty')"
[ -n "$ACC" ] || inc "login do admin falhou — sem token nao ha o que medir"

if [ "$(claims "$ACC" | jq 'has("unrestricted")')" = "false" ]; then
  ok "N1a login: o claim nao esta no JWT"
else
  bad 'N1a o claim unrestricted voltou ao JWT de login'
fi

# O refresh e ramo PROPRIO: o modo de falha classico de claim e funcionar por uma hora
# e voltar na renovacao, porque os dois caminhos montam o payload em lugares diferentes.
if [ -n "$REF" ]; then
  ACC2="$(curl -s -X POST "$AUTH/refresh" -H 'content-type: application/json' \
    -d "{\"refresh_token\":\"$REF\"}" | jq -r '.access_token // empty')"
  if [ -z "$ACC2" ]; then
    bad "N1b o refresh nao devolveu token — nao foi possivel medir o segundo caminho"
  elif [ "$(claims "$ACC2" | jq 'has("unrestricted")')" = "false" ]; then
    ok "N1b refresh: o claim tambem nao esta la"
  else
    bad "N1b o claim voltou pelo REFRESH (o login esta limpo e a renovacao nao)"
  fi
else
  bad "N1b login nao devolveu refresh_token — o segundo caminho ficou sem medida"
fi

echo
echo "N2 - a API RECUSA o campo NOMEANDO, em vez de aceitar calada"
TOK="$(plughub_token)"
[ -n "$TOK" ] || inc "sem token de admin"
ALVO="probe_lapide_$$@plughub.local"
RESP="$(curl -s -w '\n%{http_code}' -X POST "$AUTH/users" -H 'content-type: application/json' \
  -H "Authorization: Bearer $TOK" \
  -d "{\"tenant_id\":\"$TENANT\",\"email\":\"$ALVO\",\"password\":\"lapide_probe_123\",\"unrestricted\":true}")"
CODE="$(printf '%s' "$RESP" | tail -1)"
CORPO="$(printf '%s' "$RESP" | sed '$d')"
if [ "$CODE" = "422" ]; then
  ok "N2a POST com o campo removido = 422"
  case "$(printf '%s' "$CORPO" | jq -r '.detail[0].msg // .detail // ""')" in
    *AUT-15*|*REMOVIDO*) ok "N2b a recusa NOMEIA o motivo" ;;
    *) bad 'N2b recusou sem dizer por que — a lapide perdeu a mensagem (tipo None em vez de Any?)' ;;
  esac
elif [ "$CODE" = "200" ] || [ "$CODE" = "201" ]; then
  bad "N2a campo aceito em SILENCIO ($CODE) — pydantic ignorou e o chamador acha que concedeu"
  ID="$(printf '%s' "$CORPO" | jq -r '.id // empty')"
  [ -n "$ID" ] && curl -s -o /dev/null -X DELETE "$AUTH/users/$ID" -H "Authorization: Bearer $TOK"
else
  bad "N2a esperado 422, veio $CODE"
fi

# Controle positivo na MESMA rodada: sem ele o N2 passaria num mundo onde a rota
# simplesmente nao cria mais usuario nenhum.
RESP2="$(curl -s -w '\n%{http_code}' -X POST "$AUTH/users" -H 'content-type: application/json' \
  -H "Authorization: Bearer $TOK" \
  -d "{\"tenant_id\":\"$TENANT\",\"email\":\"ok_$ALVO\",\"password\":\"lapide_probe_123\"}")"
CODE2="$(printf '%s' "$RESP2" | tail -1)"
if [ "$CODE2" = "200" ] || [ "$CODE2" = "201" ]; then
  ok "N2c controle positivo: o MESMO corpo sem o campo cria ($CODE2)"
  ID2="$(printf '%s' "$RESP2" | sed '$d' | jq -r '.id // empty')"
  [ -n "$ID2" ] && curl -s -o /dev/null -X DELETE "$AUTH/users/$ID2" -H "Authorization: Bearer $TOK"
else
  bad "N2c a rota nao cria nem sem o campo ($CODE2) — o N2a passou pelo motivo errado"
fi

echo
echo "N3 - o principal 'irrestrito' dos gates ALCANCA o tenant inteiro"
SAIDA="$(bash "$HERE/mk_unrestricted_principal.sh" 2>&1)"; RC=$?
if [ "$RC" -eq 2 ]; then
  inc "mk_unrestricted_principal nao pode rodar: $(printf '%s' "$SAIDA" | head -1)"
elif [ "$RC" -ne 0 ]; then
  bad "mk_unrestricted_principal falhou:"; printf '%s\n' "$SAIDA" | tail -3 | sed 's/^/               /'
else
  LINHA="$(printf '%s' "$SAIDA" | grep 'conferencia:' || true)"
  N_P="$(printf '%s' "$LINHA" | sed -n 's/.*principal=\([0-9]*\).*/\1/p')"
  N_A="$(printf '%s' "$LINHA" | sed -n 's/.*admin=\([0-9]*\).*/\1/p')"
  if [ -z "${N_P:-}" ] || [ "${N_P:-0}" -eq 0 ]; then
    bad "o principal ficou com ${N_P:-?} pools — e o estado de escopo VAZIO com nome de irrestrito"
  elif [ "${N_P:-0}" -ge "${N_A:-0}" ]; then
    ok "N3 principal=$N_P pools · admin=$N_A pools"
  else
    bad "N3 o principal ($N_P) alcanca MENOS que o admin ($N_A)"
  fi
fi

echo
echo "====================="
[ "$FAIL" -eq 0 ] && { echo "VERDE — o campo saiu, a recusa nomeia, e o principal enxerga o tenant"; exit 0; }
echo "VERMELHO"; exit 1
