#!/usr/bin/env bash
# q_scope_delta_stage2.sh — o recorte REMOVEU as linhas, ou COLAPSOU a contagem?
#
# Continuação de `q_process_chip_delta.sh`, que provou causa comum (com token:
# contatos 120→86 E multi 29→0, com `page_size` inerte). O que ele NÃO pode
# responder é qual das duas leituras vale, e elas pedem consertos opostos:
#
#   (i)  as 29 linhas multi estão entre as 34 REMOVIDAS  ⇒ o chip está certo, o
#        assunto é escopo/config.
#   (ii) as linhas ficaram e `journey_session_count` foi computado SOB o mesmo
#        recorte ⇒ um processo de 3 sessões das quais o principal vê 1 reporta 1,
#        e o chip MENTE para todo usuário escopado. Defeito próprio, e pior: some
#        em silêncio, que é a família que a § Postura de Engenharia persegue.
#
# O discriminador é a INTERSEÇÃO: `multi_ids ∩ auth_ids`. Vazia ⇒ (i). Não-vazia
# ⇒ (ii), e as linhas da interseção são a prova, com o valor que cada uma reporta
# nos dois escopos lado a lado.
#
# Mede também o TERCEIRO fato que não fecha: `accessible_pools` vazio deveria ser
# IRRESTRITO. Se o recorte existe mesmo assim, ou o claim não é o que se supõe, ou
# o recorte vem de outro claim — e as duas hipóteses se distinguem lendo o token,
# não o código.
#
# Uso:  bash infra/test/q_scope_delta_stage2.sh
set -u

AN=${AN:-http://localhost:3500}
AUTH=${AUTH:-http://localhost:3202/auth}
TENANT=${TENANT:-tenant_demo}
ADMIN_EMAIL=${ADMIN_EMAIL:-admin@plughub.local}
ADMIN_PASS=${ADMIN_PASS:-changeme_admin}
FROM=${FROM:-2026-08-19T00:00:00}
TO=${TO:-2026-08-26T23:59:59}

command -v jq >/dev/null || { echo "INCONCLUSIVO: jq ausente"; exit 2; }

TOK=$(curl -s -X POST "$AUTH/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASS\",\"tenant_id\":\"$TENANT\"}" \
  | jq -r '.access_token // empty')
[ -z "$TOK" ] && { echo "INCONCLUSIVO: login admin falhou"; exit 2; }

# ── 1. o que o token realmente CARREGA ───────────────────────────────────────
echo "1 · claims do token de admin (o recorte tem de vir de um destes):"
PAY=$(echo "$TOK" | cut -d. -f2 | tr '_-' '/+')
case $(( ${#PAY} % 4 )) in 2) PAY="$PAY==";; 3) PAY="$PAY=";; esac
echo "$PAY" | base64 -d 2>/dev/null \
  | jq -c '{roles, accessible_pools, supervised_groups, supervised_user_ids,
            analytics_open_access, module_config: (.module_config|keys?)}' \
  || echo "  (não decodificou — token opaco?)"
echo "  ⚠️ \`accessible_pools: []\` significa IRRESTRITO. Se está vazio e ainda"
echo "     assim recorta, o recorte vem de outro lugar."
echo

URL="$AN/reports/sessions?tenant_id=$TENANT&from_dt=$FROM&to_dt=$TO&page=1&page_size=200"
ANON=$(curl -s "$URL")
AUTHD=$(curl -s "$URL" -H "Authorization: Bearer $TOK")

# ── 2. o discriminador ───────────────────────────────────────────────────────
MULTI_IDS=$(echo "$ANON"  | jq -r '.data[] | select((.journey_session_count // 0) > 1) | .session_id' | sort -u)
AUTH_IDS=$(echo  "$AUTHD" | jq -r '.data[].session_id' | sort -u)

N_MULTI=$(echo "$MULTI_IDS" | grep -c . )
N_AUTH=$(echo  "$AUTH_IDS"  | grep -c . )
INTER=$(comm -12 <(echo "$MULTI_IDS") <(echo "$AUTH_IDS"))
N_INTER=$(echo "$INTER" | grep -c . )

echo "2 · multi(anon)=$N_MULTI · visíveis(auth)=$N_AUTH · INTERSEÇÃO=$N_INTER"
echo
if [ "$N_MULTI" -eq 0 ]; then
  echo "INCONCLUSIVO: nenhuma linha multi no escopo anônimo — sem população, o"
  echo "  discriminador não discrimina. (A janela mudou? O ambiente mudou?)"
  exit 2
elif [ "$N_INTER" -eq 0 ]; then
  echo "VEREDICTO (i): as linhas multi FORAM REMOVIDAS pelo recorte."
  echo "  O chip não tem defeito — ele não tem linha. O assunto é ESCOPO:"
  echo "  por que o admin não alcança os pools dessas 29 sessões."
  echo
  echo "  Pools de ENTRADA das multi (anon), para saber quais são:"
  echo "$ANON" | jq -r --argjson ids "$(echo "$MULTI_IDS" | jq -R . | jq -s .)" \
    '[.data[] | select(.session_id as $s | $ids | index($s))
              | (.entry_pool_id // .pool_id // "?")] | group_by(.)
     | map({pool: .[0], n: length}) | sort_by(-.n) | .[] | "    \(.n)× \(.pool)"'
else
  echo "VEREDICTO (ii): $N_INTER linha(s) multi ESTÃO VISÍVEIS ao admin — e ainda"
  echo "  assim o chip não aparece. Logo \`journey_session_count\` é computado SOB o"
  echo "  recorte: o processo encolhe para o tamanho que o principal enxerga, e a"
  echo "  tela diz 'não há processo' onde há."
  echo
  echo "  Prova, lado a lado (anon × auth) nas primeiras 5:"
  for sid in $(echo "$INTER" | head -5); do
    a=$(echo "$ANON"  | jq -r --arg s "$sid" '.data[]|select(.session_id==$s)|.journey_session_count')
    b=$(echo "$AUTHD" | jq -r --arg s "$sid" '.data[]|select(.session_id==$s)|.journey_session_count')
    echo "    ${sid: -14}  anon=$a  auth=$b"
  done
fi
