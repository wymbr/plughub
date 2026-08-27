#!/usr/bin/env bash
# ==============================================================================
# probe_nav_backend_field_agreement.sh — o MENU e o BACKEND concordam sobre o campo?
# ==============================================================================
#
# O QUE ELE PROVA
# ---------------
# Uma tela e gateada DUAS vezes: pelo campo ABAC no `Sidebar.tsx` (quem VE) e pelo
# campo que o servico exige (quem ESCREVE). Nada obrigava os dois a serem o mesmo, e
# em 2026-08-27 nao eram: `nav.channels` apontava para `config.platform` enquanto o
# config-api ja gateava os namespaces de canal em `config.channels`. Efeito:
#
#   · conceder so `config.channels`  ->  a API funciona e o menu ESCONDE a tela
#   · conceder so `config.platform`  ->  o menu mostra a tela e a API RECUSA
#
# Nenhum dos dois fica vermelho em lugar nenhum: um vira "sumiu do menu", o outro vira
# "salvei e deu erro". Este probe mede a concordancia por COMPORTAMENTO — concede a um
# principal SO o campo que o menu exige e confere se o backend aceita a escrita.
#
# DECLARADO x DERIVADO (e por que importa)
# ----------------------------------------
# Declarado aqui: entrada de menu -> namespace que a tela escreve (vive no codigo da
# pagina, nao da para derivar). DERIVADO do `Sidebar.tsx`: o CAMPO. Copiar o campo
# para ca faria o probe repetir o erro do menu em vez de o denunciar — um instrumento
# que copia a coisa medida nao mede nada.
#
# O QUE O DEIXARIA VERMELHO
# -------------------------
#   403 na escrita usando o campo que o MENU exige — os dois discordam;
#   entrada de menu sem regra ABAC (nao ha o que conferir);
#   campo `config.*` novo no catalogo sem classificacao aqui (passaria por OMISSAO).
#
# TESTEMUNHA NEGATIVA (sem ela o probe passaria se a porta estivesse aberta):
#   o mesmo principal, SEM campo `config.*` algum, tem de levar 403 nas mesmas rotas.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/_auth.sh"

CFG="${CFG:-http://localhost:3600}"
SIDEBAR="$ROOT/packages/platform-ui/src/shell/Sidebar.tsx"
NAV_EMAIL="${NAV_EMAIL:-navprobe@plughub.local}"
NAV_PASS="${NAV_PASS:-changeme_navprobe}"

fail=0
ok()   { printf '  \033[32mOK\033[0m           %s\n' "$1"; }
bad()  { printf '  \033[31mFALHA\033[0m        %s\n' "$1"; fail=1; }
inc()  { printf '  \033[33mINCONCLUSIVO\033[0m %s\n' "$1"; fail=1; }
info() { printf '               %s\n' "$1"; }
sec()  { printf '\n\033[1m%s\033[0m\n' "$1"; }

command -v jq >/dev/null || { inc "jq ausente"; exit 2; }

printf '\033[1mprobe: campo do MENU x campo do BACKEND\033[0m\n'
printf '  config-api: %s   tenant: %s\n' "$CFG" "$TENANT"

# ── declarado: entrada de menu -> namespace do config-api ───────────────────
NAV_NS_platform=routing
NAV_NS_channels=webchat
NAV_NS_masking=masking
NAV_NS_dashboards=dashboards
NAVS="nav.platform nav.channels nav.masking nav.dashboards"

# campos `config.*` NAO servidos pelo config-api — declarados para que a conferencia
# de cobertura nao os cobre (e para que a lista seja visivel no diff)
NAO_CONFIG_API="resources users permissions calendars dialog_forms"

ns_de() {  # nav.x -> namespace
  case "$1" in
    nav.platform)   printf '%s' "$NAV_NS_platform" ;;
    nav.channels)   printf '%s' "$NAV_NS_channels" ;;
    nav.masking)    printf '%s' "$NAV_NS_masking" ;;
    nav.dashboards) printf '%s' "$NAV_NS_dashboards" ;;
  esac
}

# ── deriva o campo do Sidebar ───────────────────────────────────────────────
# shellcheck disable=SC2086
DERIV="$(python3 "$HERE/_nav_fields.py" "$SIDEBAR" $NAVS)" || {
  inc "nao consegui derivar os campos do Sidebar.tsx (ver stderr acima)"
  info "Um probe que nao le o menu nao mede concordancia com o menu."
  exit 2
}
if printf '%s\n' "$DERIV" | grep -q '^SEMREGRA:'; then
  bad "entrada de menu sem regra ABAC de modulo 'config':"
  printf '%s\n' "$DERIV" | grep '^SEMREGRA:' | sed 's/^/                 /'
fi
CAMPOS_USADOS="$(printf '%s\n' "$DERIV" | grep -v '^SEMREGRA:' | cut -d: -f1 | tr '\n' ' ')"

# ── cobertura: todo campo do catalogo esta classificado? ────────────────────
sec "cobertura - todo campo config.* esta classificado"
CATALOGO="$(python3 - "$ROOT/infra/modules.yaml" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
for m in d["modules"]:
    if m["module_id"] == "config":
        print(" ".join(sorted(m["permission_schema"].keys())))
PY
)"
if [ -z "$CATALOGO" ]; then
  inc "nao consegui ler os campos do modulo config em infra/modules.yaml"
  exit 2
fi
faltando=""
for c in $CATALOGO; do
  case " $CAMPOS_USADOS $NAO_CONFIG_API " in
    *" $c "*) ;;
    *) faltando="$faltando $c" ;;
  esac
done
if [ -n "$faltando" ]; then
  bad "campo(s) config.* sem classificacao neste probe:$faltando"
  info "Classificar: ou a tela dele entra em NAVS (servida pelo config-api), ou o"
  info "campo entra em NAO_CONFIG_API. Sem isso, campo novo passa por OMISSAO."
else
  ok "os $(printf '%s' "$CATALOGO" | wc -w) campos de config estao classificados"
fi

# ── setup do principal mutavel ──────────────────────────────────────────────
T_ADMIN="$(plughub_token)"
uid_de() { curl -s --max-time 15 "$AUTH/users?tenant_id=$TENANT" -H "Authorization: Bearer $T_ADMIN" \
  | jq -r --arg e "$1" '.[] | select(.email == $e) | .id' | head -1; }

NAV_ID="$(uid_de "$NAV_EMAIL")"
if [ -z "$NAV_ID" ]; then
  NAV_ID="$(curl -s --max-time 15 -X POST "$AUTH/users" \
    -H "Authorization: Bearer $T_ADMIN" -H 'content-type: application/json' \
    -d "{\"tenant_id\":\"$TENANT\",\"email\":\"$NAV_EMAIL\",\"name\":\"Probe Nav\",\"password\":\"$NAV_PASS\",\"roles\":[\"operator\"]}" \
    | jq -r '.id // empty')"
  [ -z "$NAV_ID" ] && { inc "nao consegui criar $NAV_EMAIL"; exit 2; }
fi

set_cfg() {
  curl -s -o /dev/null -w '%{http_code}' --max-time 15 -X PUT "$AUTH/users/$NAV_ID/module-config" \
    -H "Authorization: Bearer $T_ADMIN" -H 'content-type: application/json' -d "$1"
}
login_nav() { curl -s -X POST "$AUTH/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$NAV_EMAIL\",\"password\":\"$NAV_PASS\",\"tenant_id\":\"$TENANT\"}" \
  | jq -r '.access_token // empty'; }
escreve() {
  curl -s -o /dev/null -w '%{http_code}' --max-time 15 -X PUT \
    "$CFG/config/$2/probe_nav_agreement?tenant_id=$TENANT" \
    -H "Authorization: Bearer $1" -H 'content-type: application/json' \
    -d '{"value":"probe"}'
}

# ── testemunha negativa ─────────────────────────────────────────────────────
sec "testemunha negativa - sem campo config.*, o backend recusa"
[ "$(set_cfg '{"contacts":{"visualizar":{"access":"read_only","scope":[]}}}')" = "200" ] \
  || { inc "nao consegui preparar o config do principal"; exit 2; }
T_NAV="$(login_nav)"
[ -z "$T_NAV" ] && { inc "login de $NAV_EMAIL falhou"; exit 2; }
vazio_ok=1
for nav in $NAVS; do
  ns="$(ns_de "$nav")"
  c="$(escreve "$T_NAV" "$ns")"
  if [ "$c" != "403" ]; then
    bad "namespace '$ns' aceitou escrita SEM campo config.* (HTTP $c)"
    info "Entao os 2xx abaixo nao provam concordancia — provam porta aberta."
    vazio_ok=0
  fi
done
[ "$vazio_ok" = "1" ] && ok "todos os namespaces recusam quem nao tem campo algum"

# ── o teste ─────────────────────────────────────────────────────────────────
sec "concordancia - o campo que o MENU exige basta para ESCREVER"
printf '%s\n' "$DERIV" | grep -v '^SEMREGRA:' | while IFS=: read -r campo nav; do
  ns="$(ns_de "$nav")"
  st="$(set_cfg "{\"config\":{\"$campo\":{\"access\":\"read_write\",\"scope\":[]}}}")"
  if [ "$st" != "200" ]; then
    inc "nao consegui conceder config.$campo (HTTP $st)"
    continue
  fi
  T2="$(login_nav)"
  c="$(escreve "$T2" "$ns")"
  case "$c" in
    2*)  ok "$nav (config.$campo)  ->  namespace '$ns'  (HTTP $c)" ;;
    403) bad "$nav gateia em config.$campo, que NAO basta para escrever em '$ns' (403)"
         info "Menu e backend discordam: quem tem este campo ve a tela e erra ao salvar;"
         info "quem tem o campo do BACKEND consegue salvar e nao ve a tela." ;;
    *)   inc "$nav (config.$campo) -> '$ns' devolveu HTTP $c (nem 2xx nem 403)" ;;
  esac
done > /tmp/_nav_agree_out.$$ 2>&1
cat /tmp/_nav_agree_out.$$
grep -q 'FALHA\|INCONCLUSIVO' /tmp/_nav_agree_out.$$ && fail=1
rm -f /tmp/_nav_agree_out.$$

# ── limpeza ─────────────────────────────────────────────────────────────────
# ⚠️ NAO zerar para `{}`: config vazio e o segundo bypass (degradacao graciosa — sem
# grants, `passesAbacRule` libera). Zerar aqui fabricaria, a cada execucao, mais um
# principal que ve o menu inteiro. Deixa-se um grant minimo e inofensivo.
set_cfg '{"contacts":{"visualizar":{"access":"read_only","scope":[]}}}' >/dev/null
for nav in $NAVS; do
  curl -s -o /dev/null --max-time 10 -X DELETE \
    "$CFG/config/$(ns_de "$nav")/probe_nav_agreement?tenant_id=$TENANT" \
    -H "Authorization: Bearer $T_ADMIN"
done

printf '\n'
if [ "$fail" -eq 0 ]; then
  printf '\033[32mVERDE\033[0m - o campo que o menu exige e o mesmo que o backend exige.\n'
else
  printf '\033[31mVERMELHO\033[0m - ver secoes acima.\n'
fi
exit "$fail"
