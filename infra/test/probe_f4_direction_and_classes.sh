#!/usr/bin/env bash
# probe_f4_direction_and_classes.sh — gate da F4 (visão 2) + do resíduo da F3
# (filtro por direção do acesso). ADR `adr-historico-unificado-duas-visoes.md`, D4/D8.
#
# O que ele julga, e por que cada ramo pode REPROVAR:
#
#   A. CONTRATO — o campo `direction` chega na linha. Antes da F4 a direção era
#      derivada na UI; se o alias não sair do SQL, a tela volta a mostrar `—` em
#      tudo e nada fica vermelho. AUSENTE (`has()` falso) ≠ vazio: ausente é
#      degradação de tier, vazio é "o backend não classificou esta sessão".
#
#   B. PARTIÇÃO — Σ(inbound, outbound, internal) ≤ total, e a diferença é a
#      população NÃO CLASSIFICADA. É o ramo que pega o modo de falha clássico do
#      filtro que não filtra: um parâmetro ignorado devolve a lista inteira em
#      cada balde, e Σ vira 3×total. O seletor que a F3 removeu falhava assim.
#
#   C. LINHA × FILTRO — toda linha devolvida sob `direction=d` tem `.direction == d`.
#      Hoje a coluna e o `WHERE` são a MESMA expressão, então este ramo é redundante
#      por construção — e é exatamente por isso que ele existe: no dia em que
#      alguém tocar um dos dois lados, é ele que fica vermelho.
#
#   D. AS DUAS CLASSES DE LINHA (D4) — no processo de referência, quantas sessões
#      são acesso do cliente e quantas são maquinaria. É o número que o cabeçalho
#      da visão 2 passou a mostrar. Zero acesso num processo com sessões é
#      REPROVA: significa que a tela conta zero protagonistas para um caso que o
#      cliente viveu.
#
#   E. TESTEMUNHA NEGATIVA — direção inválida tem de ser RECUSADA (422), não
#      ignorada. Sem ela, "aceita tudo" e "filtra certo" ficam indistinguíveis.
#
# INCONCLUSIVO (exit 2) quando falta amostra — nunca verde por ausência.
set -u
# Credencial (2026-08-27): o `pool_auth` deixou de devolver irrestrito na ausencia
# de header. O shim anexa o Bearer SO nas chamadas a analytics-api. Ver _auth.sh.
source "$(dirname "$0")/_auth.sh"; plughub_auth_curl_shim

API=${API:-http://localhost:3500}
TENANT=${TENANT:-tenant_demo}
# Processo de referência (o mesmo do gate da F3). Sobrescreva com J=… se sumir.
J=${J:-d62d7121-07b9-43dd-99ff-c5785d520e58}

command -v jq >/dev/null || { echo "INCONCLUSIVO: jq ausente"; exit 2; }

fail=0
note() { echo "  $*"; }

tot=$(curl -s "$API/reports/sessions?tenant_id=$TENANT&page_size=1" | jq -r '.meta.total // -1')
if [ "$tot" = "-1" ]; then
  echo "INCONCLUSIVO: /reports/sessions não respondeu meta.total (analytics-api de pé?)"
  exit 2
fi
if [ "$tot" -lt 1 ]; then
  echo "INCONCLUSIVO: total=$tot no período default — sem população, nenhum ramo abaixo"
  echo "              distingue 'filtra certo' de 'não há o que filtrar'."
  exit 2
fi
echo "amostra: $tot sessões no período default do endpoint · tenant $TENANT"
echo

# ── A. contrato ──────────────────────────────────────────────────────────────
echo "A · contrato do campo:"
row=$(curl -s "$API/reports/sessions?tenant_id=$TENANT&page_size=1" | jq -c '.data[0] // empty')
if [ -z "$row" ]; then
  echo "INCONCLUSIVO: a listagem devolveu meta.total=$tot e nenhuma linha"
  exit 2
fi
if echo "$row" | jq -e 'has("direction")' >/dev/null; then
  d=$(echo "$row" | jq -r '.direction // "null"')
  note "✓ direction presente = '$d'"
  case "$d" in
    inbound|outbound|internal|"") : ;;
    *) note "✗ valor fora do domínio: '$d'"; fail=1 ;;
  esac
else
  note "✗ direction AUSENTE (≠ vazio) — o alias não saiu do SQL, ou a query caiu de tier"
  fail=1
fi
echo

# ── B. partição ──────────────────────────────────────────────────────────────
echo "B · partição (Σ das três ≤ total):"
sum=0
same_as_total=0
for d in inbound outbound internal; do
  n=$(curl -s "$API/reports/sessions?tenant_id=$TENANT&page_size=1&direction=$d" | jq -r '.meta.total // -1')
  if [ "$n" = "-1" ]; then
    note "✗ $d: endpoint não respondeu meta.total — parâmetro rejeitado?"
    fail=1
    continue
  fi
  note "  $d = $n"
  sum=$(( sum + n ))
  [ "$n" = "$tot" ] && same_as_total=$(( same_as_total + 1 ))
done
unclassified=$(( tot - sum ))
note "Σ = $sum · total = $tot · não classificadas = $unclassified"
if [ "$sum" -gt "$tot" ]; then
  note "✗ Σ > total — algum balde reivindica linha de outro (predicados sobrepostos)"
  fail=1
elif [ "$same_as_total" -ge 2 ]; then
  note "✗ dois ou mais baldes iguais ao total — o parâmetro está sendo IGNORADO"
  fail=1
else
  note "✓ partição consistente"
  [ "$unclassified" -gt 0 ] && note "  ⚠ $unclassified sessão(ões) com spawn_reason não classificado — FATO, não falha"
fi
echo

# ── C. linha × filtro ────────────────────────────────────────────────────────
echo "C · toda linha devolvida confere com o balde pedido:"
for d in inbound outbound internal; do
  bad=$(curl -s "$API/reports/sessions?tenant_id=$TENANT&page_size=50&direction=$d" \
        | jq -r --arg d "$d" '[.data[]? | select(.direction != $d)] | length')
  got=$(curl -s "$API/reports/sessions?tenant_id=$TENANT&page_size=50&direction=$d" \
        | jq -r '.data | length')
  if [ "${got:-0}" -eq 0 ]; then
    note "· $d: sem linha nesta página — ramo INCONCLUSIVO (não conta como verde)"
  elif [ "${bad:-0}" -ne 0 ]; then
    note "✗ $d: $bad de $got linhas com direção diferente da pedida"
    fail=1
  else
    note "✓ $d: $got/$got conferem"
  fi
done
echo

# ── D. as duas classes de linha no processo de referência (D4) ───────────────
echo "D · classes de linha no processo $J:"
members=$(curl -s "$API/reports/sessions?tenant_id=$TENANT&root_session_id=$J&page_size=50")
n_rows=$(echo "$members" | jq -r '.data | length' 2>/dev/null || echo 0)
if [ "${n_rows:-0}" -lt 1 ]; then
  note "INCONCLUSIVO neste ramo: o processo $J não devolveu sessões"
else
  acc=$(echo "$members" | jq -r '[.data[] | select(.direction == "inbound" or .direction == "outbound")] | length')
  int=$(echo "$members" | jq -r '[.data[] | select(.direction == "internal")] | length')
  unk=$(( n_rows - acc - int ))
  note "sessões=$n_rows · acessos do cliente=$acc · etapas internas=$int · não classificadas=$unk"
  if [ "$acc" -lt 1 ]; then
    note "✗ zero acessos do cliente num processo com $n_rows sessões — o cabeçalho da"
    note "  visão 2 contaria zero protagonistas para um caso que o cliente viveu"
    fail=1
  else
    note "✓ o cabeçalho tem o que contar ($acc), e as duas classes são separáveis"
  fi
  if [ "$acc" = "$n_rows" ]; then
    note "  ⚠ nenhuma etapa interna neste processo — o dobramento (D11) NÃO foi exercido aqui"
  fi
fi
echo

# ── E. testemunha negativa ───────────────────────────────────────────────────
echo "E · direção inválida é recusada, não ignorada:"
code=$(curl -s -o /dev/null -w '%{http_code}' \
       "$API/reports/sessions?tenant_id=$TENANT&page_size=1&direction=banana")
if [ "$code" = "422" ]; then
  note "✓ HTTP 422"
else
  note "✗ HTTP $code — um valor fora do domínio devolveu resposta; o filtro aceitaria"
  note "  qualquer coisa e a tela não teria como saber que o recorte não foi aplicado"
  fail=1
fi

echo
[ "$fail" = 0 ] && { echo "GATE F4/direção: OK"; exit 0; }
echo "GATE F4/direção: REPROVADO"; exit 1
