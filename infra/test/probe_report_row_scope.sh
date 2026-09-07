#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# probe_report_row_scope — os AGREGADOS de /reports recortam LINHA por pool (AUT-01)
# ═══════════════════════════════════════════════════════════════════════════════
#
# EXIGIR CREDENCIAL e RECORTAR LINHA sao dois fatos. O primeiro fechou em 2026-08-29
# (`probe_route_credential_coverage.sh`); este portao guarda o segundo, para os
# agregados. O conteudo de UM contato ja tem o seu (`probe_session_content_scope.sh`).
#
# O QUE ELE IMPEDE DE VOLTAR. Medido ao vivo em 2026-08-31, com controle positivo na
# MESMA rodada: `admin@` (36 pools) e um chamador escopado a UM pool liam numeros
# IDENTICOS em `/usage` (20), `/evaluations` (2), `/evaluations/summary` (1),
# `/evaluations/quality` (2), `/agent-events/summary` (3), `/agent-events/categories`
# (3) e `/customers/{id}/360` (21 contatos) — enquanto `/sessions` movia 386→323. Sem
# aquele 386→323 a rodada inteira nao valeria nada: "numeros iguais" e tambem o que
# uma medicao quebrada produz.
#
# AS DUAS METADES NAO SE SUBSTITUEM:
#
#   A. CENSO (AST, sem stack) — toda rota cai em ESCOPADA, ISENTA ou DIVIDA. Rota nova
#      em nenhuma delas reprova. Isto e o que a metade B nao ve: uma rota sem dado
#      passa na medicao por AUSENCIA, e ausencia nao e evidencia.
#
#   B. AO VIVO — o recorte esta no caminho que roda, nao so no fonte. Um `Depends` num
#      router que ninguem inclui nao gateia nada, e um `accessible_pools=` passado a
#      uma query que o ignora nao recorta nada; so a medicao separa as duas.
#
# ⚠️ ROTA SEM DADO SAI `SEM AMOSTRA`, NUNCA VERDE. Se admin e escopado leem os dois
# zero, o teste nao alcancou a condicao que deveria julgar — e verde por ausencia de
# amostra e como se compra confianca sem receber nada.
#
# Veredicto: 0 = VERDE · 1 = DEFEITO · 2 = INCONCLUSIVO (pre-condicao falhou).
# ═══════════════════════════════════════════════════════════════════════════════
set -uo pipefail

cd "$(dirname "$0")/../.." || { echo "INCONCLUSIVO: raiz do repo nao encontrada"; exit 2; }

AUTH="${AUTH_URL:-http://localhost:3202}"
AN="${ANALYTICS_URL:-http://localhost:3500}"
TENANT="${TENANT:-tenant_demo}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@plughub.local}"
ADMIN_PASS="${ADMIN_PASS:-changeme_admin}"
PROBE_EMAIL="probe_rowscope@plughub.local"
PROBE_PASS="probe_rowscope_123"

FAIL=0
ok()  { printf '  \033[32mOK\033[0m           %s\n' "$1"; }
bad() { printf '  \033[31mFALHA\033[0m        %s\n' "$1"; FAIL=1; }
inc() { printf '  \033[33mINCONCLUSIVO\033[0m %s\n' "$1"; exit 2; }
nte() { printf '  \033[33mSEM AMOSTRA\033[0m  %s\n' "$1"; }

echo "== probe_report_row_scope =="
echo
echo "A. CENSO — toda rota de reports.py cai em UMA classe"

command -v python3 >/dev/null || inc "python3 ausente"
CENSO="$(python3 infra/test/_report_scope_census.py 2>&1)"; RC=$?
[ "$RC" -eq 2 ] && inc "censo nao pode rodar: $CENSO"
N_ESC=$(printf '%s\n' "$CENSO" | grep -c '^ESCOPADA' || true)
N_ISE=$(printf '%s\n' "$CENSO" | grep -c '^ISENTA'   || true)
N_DIV=$(printf '%s\n' "$CENSO" | grep -c '^DIVIDA'   || true)
if [ "$RC" -ne 0 ]; then
  bad "rota sem classe (ou em duas tabelas):"
  printf '%s\n' "$CENSO" | grep -E '^(FALTA|AMBIGUA)' | sed 's/^/               /'
else
  ok "$N_ESC escopadas · $N_ISE isentas (decidido) · $N_DIV dividas (com gatilho)"
fi
# A divida e CONTADA, nunca silenciosa: se crescer, entrou rota nova sem recorte.
if [ "$N_DIV" -gt 0 ]; then
  printf '  \033[33mDIVIDA\033[0m       %s\n' \
    "$(printf '%s\n' "$CENSO" | grep '^DIVIDA' | cut -f2 | tr '\n' ' ')"
fi

echo
echo "B. AO VIVO — o recorte esta no caminho que roda"

command -v curl >/dev/null || inc "curl ausente"
command -v jq   >/dev/null || inc "jq ausente"
curl -sf "$AN/v1/health" >/dev/null 2>&1 || inc "analytics-api fora do ar em $AN"

login() {
  curl -s -X POST "$AUTH/auth/login" -H 'Content-Type: application/json' \
    -d "{\"email\":\"$1\",\"password\":\"$2\",\"tenant_id\":\"$TENANT\"}" \
    | jq -r '.access_token // empty'
}
probe_id() {
  curl -s -H "Authorization: Bearer $T_ADMIN" "$AUTH/auth/users?tenant_id=$TENANT&limit=200" \
    | jq -r ".[] | select(.email==\"$PROBE_EMAIL\") | .id" | head -1
}

T_ADMIN="$(login "$ADMIN_EMAIL" "$ADMIN_PASS")"
[ -n "$T_ADMIN" ] || inc "login do admin falhou (ADMIN_PASS certo? auth-api no ar?)"

SESS="$(curl -s -H "Authorization: Bearer $T_ADMIN" "$AN/reports/sessions?tenant_id=$TENANT&page_size=200")"
# O pool com MAIS sessoes: escolher um pool raro faria toda rota sair `SEM AMOSTRA` e
# o portao ficaria verde sem julgar nada.
POOL="$(printf '%s' "$SESS" | jq -r '[.data[]?.pool_id // empty] | map(select(. != "")) | group_by(.) | max_by(length) | .[0] // empty')"
[ -n "$POOL" ] || inc "nenhum pool com sessao — sem populacao para medir"
# ⚠️ O CLIENTE do 360 NAO e escolhido aqui, e a razao esta na secao C: ele so tem
# poder de julgar depois que o principal escopado existe, porque a escolha precisa
# perguntar ao PRODUTO o que aquele escopo alcanca.
printf '%s' "$SESS" > /tmp/_rowscope_admin.json

OLD="$(probe_id)"
[ -n "$OLD" ] && curl -s -o /dev/null -X DELETE -H "Authorization: Bearer $T_ADMIN" "$AUTH/auth/users/$OLD"
curl -s -o /dev/null -X POST "$AUTH/auth/users" -H "Authorization: Bearer $T_ADMIN" \
  -H 'Content-Type: application/json' \
  -d "{\"tenant_id\":\"$TENANT\",\"email\":\"$PROBE_EMAIL\",\"name\":\"Probe RowScope\",\"password\":\"$PROBE_PASS\",\"roles\":[\"supervisor\"],\"accessible_pools\":[\"$POOL\"]}"
T_PROBE="$(login "$PROBE_EMAIL" "$PROBE_PASS")"
[ -n "$T_PROBE" ] || inc "login do usuario-sonda falhou"
echo "               pool do escopo: $POOL"

conta() { curl -s -H "Authorization: Bearer $2" "$AN$1" | jq -r "$3 // \"null\"" 2>/dev/null; }

# rota <TAB> jq <TAB> rotulo. TAB e nao espaco: os campos jq contem `|` e parenteses, e
# um separador que aparece dentro do campo parte a linha no meio — a primeira versao
# usou espaco e reprovou seis rotas boas enquanto ABSOLVIA o controle positivo, que e o
# pior dos dois erros.
CASOS="$(printf '%s\n' \
  "/reports/sessions?tenant_id=$TENANT	.meta.total	controle-positivo" \
  "/reports/usage?tenant_id=$TENANT	.meta.total	usage" \
  "/reports/evaluations?tenant_id=$TENANT	.meta.total	evaluations" \
  "/reports/evaluations/summary?tenant_id=$TENANT	(.data|length)	evaluations/summary" \
  "/reports/evaluations/quality?tenant_id=$TENANT	(.data|length)	evaluations/quality" \
  "/reports/agent-events/summary?tenant_id=$TENANT	(.data|length)	agent-events/summary" \
  "/reports/agent-events/categories?tenant_id=$TENANT	(.data|length)	agent-events/categories")"

CONTROLE_MOVEU=0
# `while read` alimentado por here-string, NUNCA por pipe: o pipe roda o laco num
# subshell e `FAIL`/`CONTROLE_MOVEU` morreriam com ele — o portao sairia verde tendo
# acusado defeito na tela.
while IFS=$'\t' read -r rota jqx rot; do
  [ -z "$rota" ] && continue
  a="$(conta "$rota" "$T_ADMIN" "$jqx")"
  p="$(conta "$rota" "$T_PROBE" "$jqx")"
  if [ -z "$a" ] || [ "$a" = "null" ]; then
    bad "$rot: admin nao devolveu numero — rota quebrada?"
  elif [ "$a" = "0" ]; then
    nte "$rot: admin=0 — sem populacao, nada a julgar"
  elif [ "$a" = "$p" ]; then
    bad "$rot: admin=$a e escopado=$p sao IGUAIS — nao recorta"
  else
    ok "$rot: admin=$a escopado=$p"
    [ "$rot" = "controle-positivo" ] && CONTROLE_MOVEU=1
  fi
done <<< "$CASOS"

# Sem o controle positivo a rodada inteira e inconclusiva: se `/sessions` — que recorta
# desde sempre — nao move, o que nao moveu foi o instrumento.
[ "$CONTROLE_MOVEU" -eq 1 ] || inc "o controle positivo (/reports/sessions) nao moveu — o instrumento nao mede"

echo
echo "C. 360 — o par (cliente, escopo) tem de DISCRIMINAR"
# ── Por que esta secao saiu da lista generica (AUT-36, 2026-09-07) ───────────
#
# O ramo do `/customers/{id}/360` comparava CONTAGENS, e igualdade so prova ausencia
# de filtro se o dado PUDER diferir. O cliente era escolhido pelo criterio *"o de mais
# sessoes"*, INDEPENDENTE do pool do escopo — entao nada garantia que ele tivesse
# alguma sessao fora do alcance daquele escopo.
#
# Medido em 2026-09-07: numa rodada o par sorteado foi POOL=`limite_ia` e o cliente
# `cus_2dec…`, cujas **21 sessoes estao TODAS em `limite_ia`**. `21 = 21` era o
# comportamento CERTO, e o gate gritava lobo. Noutra rodada, com o mesmo cliente e
# escopo `retencao_humano` — que nao entra nem atende nenhuma sessao dele —, a rota
# devolveu **0 contra 21 do admin**: o recorte funciona. O defeito era do INSTRUMENTO,
# e o vermelho dele custava a leitura de todo o portao.
#
# ⚠️ E a discriminacao NAO pode ser derivada aqui. O predicado de escopo e uma UNIAO
# (entrou por pool meu **ou** um pool meu ATENDEU), entao um cliente cujas sessoes
# entraram por outro pool ainda pode ser legitimamente visivel se o meu pool atendeu.
# Reimplementar a uniao no gate seria a SEGUNDA casa do mesmo predicado — o defeito
# que a AUT-01 fechou. Entao pergunta-se ao PRODUTO: a lista de contatos escopada ja
# aplica a uniao, e o cliente que nao aparece nela e, por construcao, invisivel.
#
# Com o par certo a assercao deixa de ser *"os numeros diferem"* e passa a ser
# **`escopado == 0`** — expectativa EXATA. `!=` deixaria passar um vazamento de 1.
VIS="$(curl -s -H "Authorization: Bearer $T_PROBE" \
       "$AN/reports/sessions?tenant_id=$TENANT&page_size=500")"
printf '%s' "$VIS" > /tmp/_rowscope_vis.json
CID="$(python3 - /tmp/_rowscope_admin.json /tmp/_rowscope_vis.json <<'PY'
import collections, json, sys

def linhas(p):
    try:
        return json.load(open(p)).get("data") or []
    except Exception:
        return []

admin, visiveis = linhas(sys.argv[1]), linhas(sys.argv[2])
vistos = {s.get("customer_id") for s in visiveis if s.get("customer_id")}
por_cliente = collections.Counter(
    s["customer_id"] for s in admin if s.get("customer_id"))
# O de MAIS sessoes entre os que o escopo nao alcanca em NENHUMA delas.
cands = [(n, c) for c, n in por_cliente.items() if c not in vistos]
print(sorted(cands, reverse=True)[0][1] if cands else "")
PY
)"
if [ -z "$CID" ]; then
  nte "customers/360: nenhum cliente INVISIVEL ao escopo — o par nao discrimina."
  printf '               %s\n' "Sem ele, nem o verde nem o vermelho deste ramo significam algo:"
  printf '               %s\n' "contagens iguais seriam o comportamento correto."
else
  A360="$(conta "/reports/customers/$CID/360?tenant_id=$TENANT" "$T_ADMIN" ".contacts.total")"
  P360="$(conta "/reports/customers/$CID/360?tenant_id=$TENANT" "$T_PROBE" ".contacts.total")"
  printf '               cliente discriminante: %s (admin=%s)\n' "$CID" "$A360"
  if [ -z "$A360" ] || [ "$A360" = "null" ] || [ "$A360" = "0" ]; then
    nte "customers/360: o admin nao ve contato deste cliente — sem populacao"
  elif [ "$P360" = "0" ] || [ "$P360" = "null" ]; then
    ok "customers/360: admin=$A360 e escopado=$P360 — recorta, e o zero e EXATO"
  else
    bad "customers/360: escopado=$P360 num cliente que o escopo NAO alcanca (admin=$A360)"
    printf '               %s\n' "A lista escopada nao mostra NENHUMA sessao deste cliente,"
    printf '               %s\n' "entao o 360 esta contando linha que o chamador nao pode abrir."
  fi
fi
rm -f /tmp/_rowscope_admin.json /tmp/_rowscope_vis.json

echo
# Testemunha negativa: `[]` = NENHUM pool nao pode voltar a significar "todos" (AUT-03).
PID="$(probe_id)"
curl -s -o /dev/null -X PATCH "$AUTH/auth/users/$PID" -H "Authorization: Bearer $T_ADMIN" \
  -H 'Content-Type: application/json' -d '{"accessible_pools":[]}'
T_ZERO="$(login "$PROBE_EMAIL" "$PROBE_PASS")"
Z="$(conta "/reports/usage?tenant_id=$TENANT" "$T_ZERO" ".meta.total")"
if [ "$Z" = "0" ]; then
  ok "escopo VAZIO le 0 linhas (nao virou 'todos')"
else
  bad "escopo VAZIO leu $Z linhas — a lista vazia voltou a significar 'todos'"
fi

echo
echo "====================="
[ "$FAIL" -eq 0 ] && { echo "VERDE"; exit 0; }
echo "VERMELHO"; exit 1
