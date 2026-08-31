#!/usr/bin/env bash
# ==============================================================================
# probe_accessible_pools_scope.sh — ninguem pode fundir `[]` com `None`
# ==============================================================================
#
# `accessible_pools` tem TRES valores e dois deles sao falsy:
#
#     None  -> irrestrito      (falsy)
#     []    -> NENHUM pool     (falsy)   <-- depois do passo 3
#     [...] -> recorte         (truthy)
#
# Logo `if not accessible_pools:` colapsa irrestrito com nenhum-acesso. Hoje o
# ramo e inalcancavel (`resolve_scope` devolve `None` para lista vazia); no dia em
# que `LEGACY_EMPTY_MEANS_UNRESTRICTED` virar `False`, cada sitio assim vira
# LIBERACAO onde deveria haver RECUSA — sem erro, sem log, sem tela vermelha.
#
# Este portao existe para que a inversao (AUT-03) seja um ato de UMA LINHA, e nao
# uma auditoria de 217 referencias feita as pressas no dia.
#
# DUAS METADES, e nenhuma substitui a outra:
#   (A) CENSO — nenhum sitio novo pode decidir por truthiness. Os 6 tolerados
#       estao NOMEADOS abaixo, cada um com o motivo; a lista e a tabela do gate,
#       nao documentacao.
#   (B) TESTEMUNHA — os 3 sitios consertados em 2026-08-31 (AUT-05) precisam
#       continuar distinguindo. Sem esta metade, reverter um conserto sairia
#       verde: o sitio voltaria para a lista tolerada e ninguem notaria.
#
# SAIDA: 0 = VERDE · 1 = VERMELHO · 2 = INCONCLUSIVO
# ==============================================================================
set -uo pipefail

cd "$(dirname "$0")/../.." || { echo "INCONCLUSIVO: raiz do repo nao encontrada"; exit 2; }
CENSO="infra/test/_accessible_pools_census.py"
[ -f "$CENSO" ] || { echo "INCONCLUSIVO: $CENSO ausente"; exit 2; }

OUT="$(python3 "$CENSO" packages 2>/dev/null)" || { echo "INCONCLUSIVO: censo falhou"; exit 2; }
[ -n "$OUT" ] || { echo "INCONCLUSIVO: censo sem saida"; exit 2; }
FAIL=0

echo "== probe_accessible_pools_scope =="

# --- tolerados: cada linha e `arquivo:funcao  # motivo` -----------------------
# Verificados um a um em 2026-08-31 lendo os chamadores, nao a funcao isolada.
TOLERADOS="
pool_auth.py:authorize_session_scope
reports_query.py:_session_scope_clause
reports_query.py:_fetch_contact_insights
reports_query.py:_fetch_workflow_summary
reports_query.py:_fetch_session_complexity
reports_query.py:_events_sql_branches
"

# ------------------------------------------------------- metade A: censo
NOVOS=""
while read -r _k path fn; do
  [ -z "${fn:-}" ] && continue
  key="$(basename "${path%%:*}"):$fn"
  printf '%s\n' $TOLERADOS | grep -qx "$key" || NOVOS="$NOVOS $key"
done < <(printf '%s\n' "$OUT" | awk '$1=="FUNDE" && $2 ~ /\// {print $1, $2, $3}')

if [ -n "$NOVOS" ]; then
  echo "A. VERMELHO — sitio NOVO decidindo por truthiness (funde [] com None):"
  printf '   %s\n' $NOVOS
  echo "   Conserto: 'if accessible_pools is not None and not accessible_pools: <recusa>'"
  FAIL=1
else
  N_TOL="$(printf '%s\n' $TOLERADOS | grep -c . || true)"
  echo "A. verde — nenhum sitio novo; $N_TOL tolerados, todos nomeados e verificados"
fi

# ------------------------------------------- metade B: testemunha dos 3 fixes
# Sem isto, reverter um conserto seria VERDE: o sitio so voltaria para a lista A.
for alvo in "reports_query.py:query_workflow_summary" "db.py:list_survey_responses" "db.py:list_results"; do
  arq="${alvo%%:*}"; fn="${alvo##*:}"
  if printf '%s\n' "$OUT" | awk '/^DISTINGUE /{print $2, $3}' | grep -q "$arq.*:.* $fn$"; then
    echo "B. verde — $fn distingue [] de None"
  else
    echo "B. VERMELHO — $fn deixou de distinguir [] de None (conserto de 2026-08-31 revertido?)"
    FAIL=1
  fi
done

echo "=================================="
printf '%s\n' "$OUT" | tail -4
[ "$FAIL" -eq 0 ] && { echo "VERDE"; exit 0; }
echo "VERMELHO"; exit 1
