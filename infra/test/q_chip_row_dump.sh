#!/usr/bin/env bash
# q_chip_row_dump.sh — os CINCO campos do chip, crus, como a API os devolve.
#
# Existe porque a tela e a teoria discordaram: o chip desenha um número nu (`· 3`)
# que, sob o código atual do `ProcessChip.tsx`, não corresponde a nenhum estado
# possível — as três renderizações são `· {total}+` (marcador aceso), `· {total}`
# (sem quebra) e `· {access} + {internal}`. Quando a tela mostra o que o código não
# pode desenhar, a pergunta não é "por quê?", é **em qual camada**: a API já não
# manda, ou o bundle servido é anterior à mudança.
#
# Este script mede a PRIMEIRA camada. Se os campos vierem certos aqui, o resto é
# cache de front (`build` + `up -d platform-ui` + Ctrl+Shift+R — são dois caches).
#
# ⚠️ POPULAÇÃO (conserto 2026-08-26): o default pegava as PRIMEIRAS linhas com chip,
# e para um usuário irrestrito essas são as mais recentes — quase todas de processo
# de UMA sessão, onde `total == acc+int+unk` vale trivialmente (1 = 1+0+0). O dump
# respondia com uma população que não contém o caso da pergunta. Agora prefere
# processo com MAIS de um membro, e avisa quando não há nenhum.
#
# Uso:  bash infra/test/q_chip_row_dump.sh
#       ADMIN_EMAIL=admin@plughub.local ADMIN_PASS=changeme_admin bash …
#       SUFFIX=aeda66758c14 bash …        # só a linha que termina nisto
set -u

AN=${AN:-http://localhost:3500}
AUTH=${AUTH:-http://localhost:3202/auth}
TENANT=${TENANT:-tenant_demo}
ADMIN_EMAIL=${ADMIN_EMAIL:-supervisor@plughub.local}
ADMIN_PASS=${ADMIN_PASS:-changeme_supervisor}
FROM=${FROM:-2026-08-19T00:00:00}
TO=${TO:-2026-08-26T23:59:59}
SUFFIX=${SUFFIX:-}
LIMIT=${LIMIT:-8}

command -v jq >/dev/null || { echo "INCONCLUSIVO: jq ausente"; exit 2; }

TOK=$(curl -s -X POST "$AUTH/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASS\",\"tenant_id\":\"$TENANT\"}" \
  | jq -r '.access_token // empty')
[ -z "$TOK" ] && { echo "INCONCLUSIVO: login falhou para $ADMIN_EMAIL"; exit 2; }

PAY=$(echo "$TOK" | cut -d. -f2 | tr '_-' '/+')
case $(( ${#PAY} % 4 )) in 2) PAY="$PAY==";; 3) PAY="$PAY=";; esac
echo "usuário=$ADMIN_EMAIL · pools=$(echo "$PAY" | base64 -d 2>/dev/null | jq -c '.accessible_pools')"

RESP=$(curl -s -H "Authorization: Bearer $TOK" \
  "$AN/reports/sessions?tenant_id=$TENANT&from_dt=$FROM&to_dt=$TO&page=1&page_size=200")

# Seleção da população, em UM lugar e sem condicional dentro do pipe de impressão.
SEL='[ .data[] | select(.journey_id != null)
      | select(($sfx == "") or (.session_id | endswith($sfx))) ]'
N_ALL=$(  echo "$RESP" | jq --arg sfx "$SUFFIX" "$SEL | length")
N_MULTI=$(echo "$RESP" | jq --arg sfx "$SUFFIX" "$SEL | map(select((.journey_session_count // 0) > 1)) | length")
echo "linhas com chip=$N_ALL · com processo multi-sessão=$N_MULTI"
if [ -z "$SUFFIX" ] && [ "${N_MULTI:-0}" -eq 0 ]; then
  echo "⚠️ nenhum processo multi-sessão nesta janela — o dump abaixo é de processos de"
  echo "   UMA sessão, onde a invariante vale trivialmente e NÃO julga a quebra."
fi
echo

# ⚠️ `has()` em vez de `//`: precisamos distinguir CHAVE AUSENTE (imagem antiga) de
# valor `null` (medido como desconhecido) de `0`/`false` (medidos). O operador `//`
# do jq confunde os três — foi o que quase escondeu o marcador desligado num probe
# anterior desta mesma série.
# `$want`: 1 = só multi (default, quando existem); 0 = todas.
WANT=1
{ [ -n "$SUFFIX" ] || [ "${N_MULTI:-0}" -eq 0 ]; } && WANT=0

echo "$RESP" | jq -r --arg sfx "$SUFFIX" --argjson lim "$LIMIT" --argjson want "$WANT" '
  def f($k): if has($k) then (.[$k]|tostring) else "AUSENTE" end;
  [ .data[]
    | select(.journey_id != null)
    | select(($sfx == "") or (.session_id | endswith($sfx)))
    | select($want == 0 or ((.journey_session_count // 0) > 1))
  ][0:$lim][]
  | "\(.session_id[-14:])  jid=\(.journey_id[0:8])"
  + "  total=\(f("journey_session_count"))"
  + "  acc=\(f("journey_access_count"))"
  + "  int=\(f("journey_internal_step_count"))"
  + "  unk=\(f("journey_unclassified_count"))"
  + "  scoped_out=\(f("journey_has_scoped_out_members"))"
'
echo
echo "Leitura:"
echo "  · algum campo AUSENTE  ⇒ a analytics-api serve imagem anterior àquele campo."
echo "  · total > acc+int+unk  ⇒ a quebra não fecha: defeito de BACKEND (é o '5 × 3')."
echo "  · scoped_out=true e a tela sem '+' ⇒ o bundle do platform-ui é que está velho."
