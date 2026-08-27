#!/usr/bin/env bash
# probe_f3_contact_list_contract.sh — gate da F3 (visão 1, lista de contatos).
#
# Julga TRÊS coisas que a tela não consegue provar sozinha:
#
#   A. CONTRATO — os campos que as colunas novas consomem chegam mesmo na resposta.
#      É o que impede a fase inteira de ser construída sobre um alias que o `_rich_sql`
#      promete e o tier de fallback não entrega. A distinção que importa é
#      AUSENTE (`has()` falso) × `null`: ausente significa que a query degradou para
#      um tier mais pobre e respondeu 200 mesmo assim — já aconteceu neste endpoint.
#
#   B. O N DO CHIP É DO PROCESSO, NÃO DA PÁGINA — e este é o ramo que pode reprovar.
#      Pedimos UMA sessão (`session_id=…`, página de 1 linha) que pertence a um
#      processo de N contatos. Se o número fosse computado sobre o conjunto devolvido,
#      viria `1`. Tem de vir `N`. Sem esta asserção, uma implementação errada passaria
#      despercebida em toda tela cuja janela por acaso contivesse o processo inteiro.
#
#   C. FONTE ÚNICA — esse mesmo N tem de bater com `session_count` do card de
#      `/reports/journeys`. São as duas pontas do pivô: divergirem significa que o
#      operador vê `· 2` no chip e `4` ao clicar nele.
#
# INCONCLUSIVO (exit 2) quando a amostra não existe — nunca verde por ausência.
set -u
# Credencial (2026-08-27): o `pool_auth` deixou de devolver irrestrito na ausencia
# de header. O shim anexa o Bearer SO nas chamadas a analytics-api. Ver _auth.sh.
source "$(dirname "$0")/_auth.sh"; plughub_auth_curl_shim

API=${API:-http://localhost:3500}
TENANT=${TENANT:-tenant_demo}
# Journey de referência (3 acessos + o trazido por alias), criada em 2026-08-14 e
# validada por `probe_journey_limite.sh` 5/0. Sobrescreva com J=… se ela sumir.
J=${J:-d62d7121-07b9-43dd-99ff-c5785d520e58}

command -v jq >/dev/null || { echo "INCONCLUSIVO: jq ausente"; exit 2; }

fail=0
note() { echo "  $*"; }

# ── amostra ──────────────────────────────────────────────────────────────────
members=$(curl -s "$API/reports/sessions?tenant_id=$TENANT&root_session_id=$J&page_size=50")
n_rows=$(echo "$members" | jq -r '.data | length' 2>/dev/null || echo 0)
if [ "${n_rows:-0}" -lt 2 ]; then
  echo "INCONCLUSIVO: a journey $J devolveu $n_rows sessão(ões) — sem amostra multi-contato,"
  echo "              o ramo B (o que de fato pode reprovar) não é exercido."
  exit 2
fi
sid=$(echo "$members" | jq -r '.data[0].session_id')
echo "amostra: journey $J · $n_rows sessões-membro · sessão de prova …${sid: -14}"

# ── A. contrato ──────────────────────────────────────────────────────────────
one=$(curl -s "$API/reports/sessions?tenant_id=$TENANT&session_id=$sid")
row=$(echo "$one" | jq -c '.data[0] // empty')
if [ -z "$row" ]; then
  echo "INCONCLUSIVO: lookup por session_id devolveu vazio"
  exit 2
fi
echo "A · contrato:"
for f in spawn_reason root_session_id elapsed_time_ms is_internal \
         journey_id journey_session_count attended_pool_ids; do
  if echo "$row" | jq -e --arg f "$f" 'has($f)' >/dev/null; then
    note "✓ $f = $(echo "$row" | jq -c --arg f "$f" '.[$f]')"
  else
    note "✗ $f AUSENTE (≠ null) — o alias não saiu do SQL, ou a query caiu de tier"
    fail=1
  fi
done

# ── B. o N é do processo, não da página ──────────────────────────────────────
n_chip=$(echo "$row" | jq -r '.journey_session_count // "null"')
echo "B · N do chip numa página de 1 linha: $n_chip"
if [ "$n_chip" = "null" ]; then
  note "✗ nulo — o pós-passe falhou (procure 'journey chip aggregation failed' no log)"
  fail=1
elif [ "$n_chip" -le 1 ]; then
  note "✗ $n_chip — contado sobre a PÁGINA, não sobre o processo. É o defeito que este ramo existe para pegar."
  fail=1
else
  note "✓ $n_chip > 1 com uma única linha devolvida"
fi

# ── C. fonte única (chip × card) ─────────────────────────────────────────────
card=$(curl -s "$API/reports/journeys?tenant_id=$TENANT&root_session_id=$J&page_size=1")
n_card=$(echo "$card" | jq -r '.data[0].session_count // "null"')
echo "C · fonte única: chip=$n_chip · card=$n_card"
if [ "$n_card" = "null" ]; then
  note "INCONCLUSIVO neste ramo: /reports/journeys não devolveu o card"
elif [ "$n_chip" != "$n_card" ]; then
  note "✗ divergem — o chip e o cabeçalho da visão 2 contam coisas diferentes"
  fail=1
else
  note "✓ iguais"
fi

# ── testemunha do filtro `entrou por` ────────────────────────────────────────
# Sem esta linha, um `entry_pool_id` que o backend IGNORASSE passaria: devolver tudo
# se parece com "não há o que filtrar". A testemunha é a comparação com o total.
tot=$(curl -s "$API/reports/sessions?tenant_id=$TENANT&page_size=1" | jq -r '.meta.total // 0')
sub=$(curl -s "$API/reports/sessions?tenant_id=$TENANT&page_size=1&entry_pool_id=__nao_existe__" | jq -r '.meta.total // -1')
echo "D · entry_pool_id: total=$tot · com pool inexistente=$sub"
if [ "$sub" = "-1" ]; then
  note "✗ o endpoint não respondeu meta.total — parâmetro rejeitado?"
  fail=1
elif [ "$sub" != "0" ]; then
  note "✗ um pool inexistente devolveu $sub sessões — o parâmetro está sendo IGNORADO"
  fail=1
elif [ "$tot" -lt 1 ]; then
  note "INCONCLUSIVO neste ramo: total=$tot, o zero acima não prova nada"
else
  note "✓ filtra (total $tot → 0)"
fi

echo
[ "$fail" = 0 ] && { echo "GATE F3: OK"; exit 0; }
echo "GATE F3: REPROVADO"; exit 1
