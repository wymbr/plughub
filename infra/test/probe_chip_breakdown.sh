#!/usr/bin/env bash
# probe_chip_breakdown.sh — gate da quebra do chip de processo (2026-08-26).
# ADR `adr-historico-unificado-duas-visoes.md`, D4/D8 + § "chip × cabeçalho".
#
# O DEFEITO QUE ELE EXISTE PARA IMPEDIR DE VOLTAR: o chip publicava `· 5` sob o
# rótulo "contatos" e o cabeçalho da visão 2 — para onde o próprio chip pivota —
# publicava `3 acessos · 2 etapas internas`. Os dois estavam certos; foi a F4 que
# criou a divergência, ao dar ao cabeçalho um domínio que o chip não tinha. O
# operador clicava num número e chegava a outro.
#
# O que cada ramo pode REPROVAR:
#
#   A. CONTRATO — os três campos da quebra chegam na linha e NÃO são nulos numa
#      linha que tem processo. Ausentes ⇒ a UI cai no número único e a divergência
#      volta em silêncio (a tela não fica vermelha: ela volta a mostrar `· 5`).
#
#   B. ARITMÉTICA — `acesso + interna + não classificada == total`, em TODA linha
#      com quebra. É a invariante que faz o chip e o cabeçalho contarem a mesma
#      população. Ela vale por construção (um `countIf` por balde sobre a mesma
#      `_DIRECTION_EXPR`, exaustiva) — e é justamente por isso que este ramo
#      existe: no dia em que alguém recortar o total por direção em vez de
#      quebrá-lo, é aqui que fica vermelho.
#
#   C. CHIP × CABEÇALHO — o `acesso` do chip tem de bater com o nº de acessos que
#      o drill do MESMO processo devolve. É a medida direta do defeito: são duas
#      queries diferentes (agregado por journey × listagem por `root_session_id`),
#      e nada além deste ramo as obriga a concordar.
#
#   D. TESTEMUNHA NEGATIVA — nenhuma linha pode ter quebra sem total. Zerar só a
#      quebra desenharia `· 0 + 0` num chip cujo tamanho ninguém sabe, que é o
#      valor plausível de sempre.
#
# INCONCLUSIVO (exit 2) quando falta amostra — nunca verde por ausência.
set -u
# Credencial (2026-08-27): o `pool_auth` deixou de devolver irrestrito na ausencia
# de header. O shim anexa o Bearer SO nas chamadas a analytics-api. Ver _auth.sh.
source "$(dirname "$0")/_auth.sh"; plughub_auth_curl_shim

API=${API:-http://localhost:3500}
TENANT=${TENANT:-tenant_demo}
# Processo de referência (o mesmo dos gates da F3/F4). Sobrescreva com J=… se sumir.
J=${J:-d62d7121-07b9-43dd-99ff-c5785d520e58}

command -v jq >/dev/null || { echo "INCONCLUSIVO: jq ausente"; exit 2; }

fail=0
# Ramo que não pôde julgar NÃO pode sair verde no veredicto global — foi o primeiro
# check de falseabilidade deste gate que pegou isto: com um `J` inexistente, o ramo
# C (o único que mede a divergência que a fatia fechou) saiu INCONCLUSIVO e o gate
# ainda assim imprimiu OK. Um processo de referência que suma apagaria a medição
# principal sem nada ficar vermelho.
incon=0
note() { echo "  $*"; }

page=$(curl -s "$API/reports/sessions?tenant_id=$TENANT&page_size=200")
tot=$(echo "$page" | jq -r '.meta.total // -1')
if [ "$tot" = "-1" ]; then
  echo "INCONCLUSIVO: /reports/sessions não respondeu meta.total (analytics-api de pé?)"
  exit 2
fi

# Testemunha de PRESENÇA, ao lado do contador de ausência: sem linha com processo
# de mais de uma sessão, todo ramo abaixo passaria por não ter o que reprovar.
with_chip=$(echo "$page" | jq -r '[.data[]? | select((.journey_session_count // 0) > 1)] | length')
echo "amostra: $tot sessões no período default · $with_chip com processo (N > 1) · tenant $TENANT"
if [ "${with_chip:-0}" -lt 1 ]; then
  echo "INCONCLUSIVO: nenhuma linha com processo de mais de uma sessão nesta janela —"
  echo "              sem chip na tela, 'quebra certa' e 'quebra ausente' são iguais."
  exit 2
fi
echo

# ── A. contrato ──────────────────────────────────────────────────────────────
echo "A · contrato dos campos da quebra:"
row=$(echo "$page" | jq -c '[.data[]? | select((.journey_session_count // 0) > 1)][0]')
for f in journey_access_count journey_internal_step_count journey_unclassified_count; do
  if ! echo "$row" | jq -e --arg f "$f" 'has($f)' >/dev/null; then
    note "✗ $f AUSENTE da linha — backend antigo, ou o campo não saiu do pós-passe"
    fail=1
  elif [ "$(echo "$row" | jq -r --arg f "$f" '.[$f] // "null"')" = "null" ]; then
    note "✗ $f NULO numa linha COM processo — a UI cairá no número único"
    fail=1
  else
    note "✓ $f = $(echo "$row" | jq -r --arg f "$f" '.[$f]')"
  fi
done
echo

# ── B. aritmética ────────────────────────────────────────────────────────────
echo "B · acesso + interna + não classificada == total, em toda linha com quebra:"
broken=$(echo "$page" | jq -r '
  [ .data[]?
    | select(.journey_access_count != null)
    | select((.journey_access_count + .journey_internal_step_count
              + .journey_unclassified_count) != .journey_session_count) ] | length')
checked=$(echo "$page" | jq -r '[.data[]? | select(.journey_access_count != null)] | length')
if [ "${checked:-0}" -lt 1 ]; then
  note "INCONCLUSIVO neste ramo: nenhuma linha trouxe a quebra"
  incon=1
elif [ "${broken:-0}" -ne 0 ]; then
  note "✗ $broken de $checked linhas não fecham — o chip virou um RECORTE do total,"
  note "  não a quebra dele; o número que o operador clica deixou de ser o da tela"
  fail=1
else
  note "✓ $checked/$checked linhas fecham"
  pop=$(echo "$page" | jq -r '[.data[]? | select((.journey_unclassified_count // 0) > 0)] | length')
  note "  não classificadas > 0 em $pop linha(s) — FATO, não falha (0 = ramo nunca exercido)"
fi
echo

# ── C. chip × cabeçalho, no processo de referência ───────────────────────────
echo "C · o chip e o cabeçalho contam a MESMA população ($J):"
members=$(curl -s "$API/reports/sessions?tenant_id=$TENANT&root_session_id=$J&page_size=200")
n_rows=$(echo "$members" | jq -r '.data | length' 2>/dev/null || echo 0)
if [ "${n_rows:-0}" -lt 1 ]; then
  note "INCONCLUSIVO neste ramo: o processo $J não devolveu sessões"
  note "  (é o ÚNICO ramo que compara o chip com o cabeçalho — sem ele o gate não"
  note "   mediu a divergência que existe para pegar; sobrescreva com J=…)"
  incon=1
else
  # Lado CABEÇALHO: contado como a visão 2 conta (uma linha, uma classe).
  h_acc=$(echo "$members" | jq -r '[.data[] | select(.direction == "inbound" or .direction == "outbound")] | length')
  h_int=$(echo "$members" | jq -r '[.data[] | select(.direction == "internal")] | length')
  # Lado CHIP: o agregado por journey, lido de qualquer linha do processo.
  c_row=$(echo "$members" | jq -c '[.data[] | select(.journey_access_count != null)][0] // empty')
  if [ -z "$c_row" ]; then
    note "✗ nenhuma linha do processo trouxe a quebra — o chip não tem o que publicar"
    fail=1
  else
    c_tot=$(echo "$c_row" | jq -r '.journey_session_count')
    c_acc=$(echo "$c_row" | jq -r '.journey_access_count')
    c_int=$(echo "$c_row" | jq -r '.journey_internal_step_count')
    note "cabeçalho: $h_acc acessos · $h_int internas ($n_rows linhas)"
    note "chip:      $c_acc acessos · $c_int internas ($c_tot total)"
    if [ "$c_acc" != "$h_acc" ] || [ "$c_int" != "$h_int" ]; then
      note "✗ divergem — o operador clica num número e chega a outro. São duas queries"
      note "  (agregado por journey × listagem por root_session_id); nada além deste"
      note "  ramo as obriga a concordar."
      fail=1
    else
      note "✓ conferem nos dois domínios"
    fi
    [ "$c_int" = "0" ] && note "  ⚠ zero etapas internas aqui — o segundo número não foi exercido"
  fi
fi
echo

# ── D. testemunha negativa ───────────────────────────────────────────────────
echo "D · nenhuma quebra sem total:"
orphan=$(echo "$page" | jq -r '
  [.data[]? | select(.journey_access_count != null and .journey_session_count == null)] | length')
if [ "${orphan:-0}" -ne 0 ]; then
  note "✗ $orphan linha(s) com quebra e sem total — chip desenharia um tamanho que"
  note "  o backend não sabe"
  fail=1
else
  note "✓ 0"
fi

echo
[ "$fail" != 0 ] && { echo "GATE chip/quebra: REPROVADO"; exit 1; }
[ "$incon" != 0 ] && {
  echo "GATE chip/quebra: INCONCLUSIVO — algum ramo não teve o que julgar."
  echo "  Verde por ausência de amostra é a forma mais barata de comprar confiança"
  echo "  sem dar nada em troca; por isso sai 2, não 0."
  exit 2
}
echo "GATE chip/quebra: OK"; exit 0
