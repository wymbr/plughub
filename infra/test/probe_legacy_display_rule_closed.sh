#!/usr/bin/env bash
# probe_legacy_display_rule_closed.sh — GATE da fase V2b (arco ALLOWLIST,
# adr-contextstore-allowlist). Fecha a casa LEGADA de display rule.
#
# O que esta fase fecha, e por que um gate de "a nova funciona" nao serve:
#   a regra de canal (display_screen x display_voice x echo) teve DUAS casas — as
#   chaves soltas rule.{category} no ns masking e, desde a V2, o campo
#   mascara.display do TIPO no catalogo masking.types. Duas casas para a mesma
#   pergunta significam que uma delas decide sem que ninguem olhe: o leitor legado
#   VENCIA sobre o catalogo. O teste que importa nao e "a nova funciona" — e
#   "a antiga nao e mais escrita E ninguem a le".
#
# Quatro ramos, e nenhum deles julga sozinho:
#   A. FONTE   — zero leitores/escritores da forma antiga em packages/ (codigo, nao comentario)
#   B. ORACULO — o contador de A consegue mesmo ACUSAR? (fixture com codigo + comentario)
#   C. CONFIG  — zero chaves rule.* vivas, com testemunha de presenca ao lado
#   D. STORE   — zero rule.* em TODO o platform_config (todos os tenants, todos os ns)
#
# Ramo B existe porque A e um contador de AUSENCIA: "0 ocorrencias" e
# indistinguivel de "regex quebrada". E a exclusao de comentario nao e higiene —
# e LOAD-BEARING aqui: o cabecalho que documenta a remocao reescreve a string
# textualmente, e um contador ingenuo reproduziria o numero anterior ao conserto,
# acusando a propria prosa que explica a remocao. Medido antes do conserto:
# 6 linhas casavam a regex em packages/, das quais 2 eram comentario.
#
# Tres estados: OK / FALHA / INCONCLUSIVO (nunca OK com ramo inconclusivo).
set -u

cd "$(dirname "$0")/../.." || exit 2
DC="${DC:-docker compose -f docker-compose.demo.yml}"
CFG="${CFG:-http://localhost:3600}"
TENANT="${TENANT:-tenant_demo}"

fail=0
inconclusive=0
say()  { echo "  $*"; }
bad()  { echo "  x $*"; fail=$((fail+1)); }
ok()   { echo "  v $*"; }
huh()  { echo "  ? $*"; inconclusive=$((inconclusive+1)); }

# A forma antiga: a chave rule. construida como STRING ou TEMPLATE. Ancorada no
# delimitador imediatamente antes de rule. para nao acusar o objeto `rule` do
# rules-engine (rule.id, rule.pattern) nem "masking.rule." em prosa.
RE="['\"\`]rule\\."

# Conta linhas de CODIGO (comentario excluido) que casam com a forma antiga.
# Recebe o caminho a varrer; imprime "arquivo:linha:conteudo" por ocorrencia.
scan_code() {
  grep -rnE "$RE" --include=*.ts --include=*.tsx --include=*.js --include=*.py "$1" 2>/dev/null \
    | grep -v '/node_modules/' | grep -v '/dist/' | grep -v '/build/' \
    | awk '{
        line = $0
        sub(/^[^:]*:[0-9]+:/, "", line)
        sub(/^[ \t]+/, "", line)
        if (line !~ /^(\/\/|\*|\/\*|#)/) print
      }'
}

echo "=== probe_legacy_display_rule_closed — V2b do arco ALLOWLIST ==="

# -- P0. PREFLIGHT DE ANCORA --------------------------------------------------
# Sem este ramo, renomear/apagar os arquivos faria o ramo A contar zero e o gate
# aprovaria por AUSENCIA DE ALVO — o modo de falha classico de contador de ausencia.
echo
echo "-- P0. preflight de ancora (os arquivos que decidem existem?) --"
PAGE="packages/platform-ui/src/modules/masking/MaskingPage.tsx"
TOKEN="packages/platform-ui/src/components/MaskedToken.tsx"
anchor_ok=1
for pair in "$PAGE:getMaskingRule" "$TOKEN:useMaskingDisplayRules"; do
  f="${pair%%:*}"; sym="${pair##*:}"
  if [ ! -s "$f" ]; then
    huh "arquivo ausente ou vazio: $f"; anchor_ok=0
  elif ! grep -q "$sym" "$f"; then
    huh "simbolo '$sym' ausente de $f — o alvo mudou de lugar; a contagem nao vale"; anchor_ok=0
  else
    ok "$f contem $sym"
  fi
done
if [ "$anchor_ok" = "0" ]; then
  echo; echo "VEREDICTO: INCONCLUSIVO — nada medido"; exit 2
fi

# -- B. TESTEMUNHA DO ORACULO (antes de A, porque A depende dela) -------------
echo
echo "-- B. testemunha — o contador consegue ACUSAR? ----------------"
FIX="$(mktemp -d)"
{
  echo '// linha de COMENTARIO citando `rule.${category}` — o contador TEM de ignorar'
  echo 'const legacy = maskingEntries[`rule.${category}`]'
  echo '/*'
  echo ' * outra linha de COMENTARIO com '"'"'rule.'"'"' — tambem ignorada'
  echo ' */'
  echo "const outra = 'rule.'"
} > "$FIX/fixture.tsx"
N_WIT="$(scan_code "$FIX" | wc -l | tr -d ' ')"
N_WIT_RAW="$(grep -rnE "$RE" "$FIX" 2>/dev/null | wc -l | tr -d ' ')"
rm -rf "$FIX"
if [ "$N_WIT" = "2" ] && [ "$N_WIT_RAW" = "4" ]; then
  ok "fixture: 4 linhas casam a regex, 2 sao codigo — contador acusa 2 e ignora 2 comentarios"
else
  bad "contador quebrado (codigo=${N_WIT}, esperado 2; bruto=${N_WIT_RAW}, esperado 4) — o ramo A nao vale nada"
fi

# -- A. FONTE -----------------------------------------------------------------
echo
echo "-- A. fonte — leitores/escritores da forma antiga -------------"
HITS="$(scan_code packages)"
if [ -z "$HITS" ]; then N_HITS=0; else N_HITS="$(echo "$HITS" | wc -l | tr -d ' ')"; fi
N_RAW="$(grep -rnE "$RE" --include=*.ts --include=*.tsx --include=*.js --include=*.py packages 2>/dev/null | grep -v '/node_modules/' | grep -v '/dist/' | wc -l | tr -d ' ')"
say "linhas que casam a regex em packages/ = ${N_RAW}   (destas, codigo = ${N_HITS}; a diferenca e comentario)"
if [ "$N_HITS" = "0" ]; then
  ok "nenhuma casa legada em CODIGO — a chave rule.{category} nao e lida nem escrita"
else
  bad "${N_HITS} casa(s) legada(s) viva(s) em codigo:"
  echo "$HITS" | sed 's/^/      /'
fi

# -- C. CONFIG VIVA -----------------------------------------------------------
echo
echo "-- C. config viva (${TENANT}) --------------------------------"
LIVE="$(curl -s --max-time 10 "${CFG}/config/masking?tenant_id=${TENANT}")"
if [ -z "$LIVE" ]; then
  huh "config-api em ${CFG} nao respondeu"
else
  NKEYS="$(echo "$LIVE" | jq -r 'if has("entries") then (.entries | length) else "ERR" end' 2>/dev/null)"
  if [ -z "$NKEYS" ] || [ "$NKEYS" = "ERR" ]; then
    huh "resposta sem .entries (ou jq ausente) — leitor quebrado, nao ausencia de dado"
  elif [ "$NKEYS" = "0" ]; then
    huh "ns masking VAZIA — zero rule.* aqui nao prova nada (base nao semeada)"
  else
    N_LEGACY="$(echo "$LIVE" | jq -r '[.entries | keys[] | select(startswith("rule."))] | length')"
    say "TOTAL_KEYS=${NKEYS}   (testemunha de presenca — zero sobre zero nao e aprovacao)"
    if [ "$N_LEGACY" = "0" ]; then
      ok "zero chaves rule.* vivas"
    else
      bad "${N_LEGACY} chave(s) rule.* viva(s) — remover o leitor apagaria politica EM VIGOR:"
      echo "$LIVE" | jq -r '.entries | keys[] | select(startswith("rule."))' | sed 's/^/      . /'
    fi
  fi
fi

# -- D. STORE INTEIRO ---------------------------------------------------------
# C mede UM tenant. "Zero para tenant_demo" nao e "zero" — a chave e por-tenant.
echo
echo "-- D. platform_config inteiro (todos os tenants, todos os ns) --"
PG="$($DC exec -T postgres psql -U plughub -d plughub_demo -t -A -c "SELECT count(*) FROM public.platform_config WHERE key LIKE 'rule.%';" 2>/dev/null | tr -d '\r' | head -1)"
PG_TOTAL="$($DC exec -T postgres psql -U plughub -d plughub_demo -t -A -c "SELECT count(*) FROM public.platform_config WHERE namespace='masking';" 2>/dev/null | tr -d '\r' | head -1)"
case "$PG" in
  ''|*[!0-9]*)
    huh "postgres nao respondeu (obtido: '${PG}') — o store nao foi medido"
    ;;
  *)
    if [ "${PG_TOTAL:-0}" = "0" ]; then
      huh "ns masking VAZIA no store — zero rule.* nao prova nada"
    else
      say "linhas no ns masking = ${PG_TOTAL}   (testemunha de presenca)"
      if [ "$PG" = "0" ]; then
        ok "zero linhas rule.* em todo o platform_config"
      else
        bad "${PG} linha(s) rule.* no store — ha politica legada gravada em algum tenant"
      fi
    fi
    ;;
esac

# -- veredicto ----------------------------------------------------------------
echo
echo "==============================================================="
if [ "$fail" -gt 0 ]; then
  echo "VEREDICTO: FALHA — ${fail} verificacao(oes) vermelha(s)"
  exit 1
fi
if [ "$inconclusive" -gt 0 ]; then
  echo "VEREDICTO: INCONCLUSIVO — ${inconclusive} ramo(s) nao julgado(s). NAO e OK."
  exit 2
fi
echo "VEREDICTO: OK"
exit 0
