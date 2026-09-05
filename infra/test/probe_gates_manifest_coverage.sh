#!/usr/bin/env bash
# ==============================================================================
# probe_gates_manifest_coverage.sh — o manifesto presta contas de TODO script
# ==============================================================================
#
# POR QUE ELE EXISTE (GAT-01, 2026-09-04)
# ---------------------------------------
# O `run_gates.sh` garante que tudo o que o manifesto CITA foi executado. Nada
# garantia que o manifesto citasse tudo o que existe — e em 2026-09-02 mediu-se
# 37 de 103. E o modo de falha que o proprio runner foi escrito para impedir,
# uma casa acima: **uma lista parece completa por ser uma lista**, e o que falta
# nao aparece em contagem nenhuma.
#
# O QUE ELE JULGA
#   B  todo `.sh` de infra/test/ esta declarado em exatamente UMA classe
#   C  todo nome do manifesto existe em disco
#   D  nenhum nome declarado duas vezes
#
# O QUE ELE NAO JULGA — e por que
#   E  o criterio "tem cara de gate" e INFORMATIVO. Ele ordena a fila de
#      triagem; nao decide quem precisa ser declarado. Ja foi refutado duas
#      vezes por falso NEGATIVO (ver o cabecalho de `_gates_manifest_census.py`),
#      e criterio que decide QUEM E COBRADO pode esconder arquivo.
#      O ramo E existe para o criterio nao apodrecer em silencio: ele exige um
#      controle POSITIVO e um NEGATIVO. Sem os dois, um criterio que aceitasse
#      tudo (ou nada) ficaria verde.
#   F  o placar. Nao ha veredicto sobre "quantos nao-triados sao demais" —
#      numero-alvo inventado vira teatro. O valor do ramo F e a AUSENCIA
#      CONTADA: enquanto o numero for impresso a cada execucao, a lista para de
#      parecer completa.
#
# POR QUE O NAO-TRIADO E UMA LISTA NOMEADA, NAO UM CONTADOR
# ---------------------------------------------------------
# Mesmo motivo do `probe_authz_single_verifier`: um contador nao sabe dizer se
# um script novo entrou e outro saiu no mesmo commit. Com a lista nomeada,
# criar um script sem triar deixa o ramo B VERMELHO, e triar um e uma edicao
# visivel no diff.
#
# Saida: 0 VERDE · 1 VERMELHO · 2 INCONCLUSIVO
# ==============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CENSO="$HERE/_gates_manifest_census.py"
MANIFESTO="$HERE/gates.manifest"

FAIL=0
ok()  { printf '  \033[32mv\033[0m %s\n' "$1"; }
bad() { printf '  \033[31mx\033[0m %s\n' "$1"; FAIL=$((FAIL + 1)); }
inf() { printf '    %s\n' "$1"; }

printf '\033[1m== probe_gates_manifest_coverage ==\033[0m\n\n'

[ -f "$CENSO" ]     || { echo "INCONCLUSIVO: censo ausente ($CENSO)"; exit 2; }
[ -f "$MANIFESTO" ] || { echo "INCONCLUSIVO: manifesto ausente"; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "INCONCLUSIVO: sem python3"; exit 2; }

TSV="$(mktemp)"; trap 'rm -f "$TSV"' EXIT INT TERM
if ! python3 "$CENSO" > "$TSV" 2>"$TSV.err"; then
  echo "INCONCLUSIVO: o censo nao rodou"; sed 's/^/    /' "$TSV.err"; rm -f "$TSV.err"; exit 2
fi
rm -f "$TSV.err"
TOTAL=$(wc -l < "$TSV")
[ "$TOTAL" -gt 50 ] || { echo "INCONCLUSIVO: censo devolveu $TOTAL linhas (esperado > 50)"; exit 2; }

col() { cut -f"$1" "$TSV"; }
classe_de() { grep -P "^$1\t" "$TSV" | cut -f3 | head -1; }

# ── B ─────────────────────────────────────────────────────────────────────────
echo "── B — todo .sh de infra/test/ esta declarado ────────────────────────────"
NAO_DECL=$(grep -Pc '\t-$' "$TSV" || true)
if [ "${NAO_DECL:-0}" -eq 0 ]; then
  ok "os $TOTAL scripts estao todos declarados no manifesto"
else
  bad "$NAO_DECL script(s) existem e o manifesto nao diz nada sobre eles"
  grep -P '\t-$' "$TSV" | cut -f1 | head -25 | sed 's/^/       /'
  [ "$NAO_DECL" -gt 25 ] && inf "... e mais $((NAO_DECL - 25))"
  inf "escolha uma classe para cada um: AUTO · !ASSISTIDO · =ISENTO · ?NAOTRIADO"
fi

# ── C ─────────────────────────────────────────────────────────────────────────
echo
echo "── C — todo nome do manifesto existe em disco ────────────────────────────"
AUSENTES=$(grep -c ':AUSENTE' "$TSV" || true)
if [ "${AUSENTES:-0}" -eq 0 ]; then
  ok "nenhum nome orfao"
else
  bad "$AUSENTES nome(s) citados pelo manifesto sem arquivo em disco"
  grep ':AUSENTE' "$TSV" | cut -f1,3 | sed 's/^/       /'
fi

# ── D ─────────────────────────────────────────────────────────────────────────
echo
echo "── D — nenhum nome em duas classes ──────────────────────────────────────"
CONFL=$(grep -c 'CONFLITO' "$TSV" || true)
if [ "${CONFL:-0}" -eq 0 ]; then
  ok "nenhuma declaracao duplicada"
else
  bad "$CONFL nome(s) declarados mais de uma vez"
  grep 'CONFLITO' "$TSV" | cut -f1 | sed 's/^/       /'
fi

# ── E ─────────────────────────────────────────────────────────────────────────
echo
echo "── E — o criterio informativo ainda DISCRIMINA ──────────────────────────"
# Controle positivo: um gate que julga varias proposicoes e sai com o placar.
# Controle negativo: um seed que provisiona um DialogForm e nao julga nada.
POS="probe_task_ledger.sh"
NEG="seed_dialog_nps_form.sh"
P=$(grep -P "^$POS\t" "$TSV" | cut -f2)
N=$(grep -P "^$NEG\t" "$TSV" | cut -f2)
if [ -z "$P" ] || [ -z "$N" ]; then
  bad "controle ausente do censo (pos=$POS:'$P' neg=$NEG:'$N') — troque por outro par"
elif [ "$P" = "1" ] && [ "$N" = "0" ]; then
  ok "positivo=$POS candidato · negativo=$NEG nao-candidato"
else
  bad "o criterio parou de discriminar: $POS=$P (esperado 1), $NEG=$N (esperado 0)"
fi

# ── F ─────────────────────────────────────────────────────────────────────────
echo
echo "── F — o placar (sem veredicto: a ausencia CONTADA) ─────────────────────"
n_auto=$(grep -Pc '\tAUTO$'      "$TSV" || true)
n_ass=$( grep -Pc '\tASSISTIDO$' "$TSV" || true)
n_ise=$( grep -Pc '\tISENTO$'    "$TSV" || true)
n_nt=$(  grep -Pc '\tNAOTRIADO$' "$TSV" || true)
n_cand_nt=$(grep -P '\t1\tNAOTRIADO$' "$TSV" | wc -l)
printf '    AUTO ......... %3d   o runner executa\n'                    "${n_auto:-0}"
printf '    ASSISTIDO .... %3d   listados, nao executados\n'            "${n_ass:-0}"
printf '    ISENTO ....... %3d   decidido que nao roda, com o motivo\n' "${n_ise:-0}"
printf '    NAO TRIADO ... %3d   dos quais %d tem cara de gate\n'       "${n_nt:-0}" "$n_cand_nt"
printf '    ------------------\n'
printf '    total ........ %3d\n' "$TOTAL"
if [ "${n_nt:-0}" -gt 0 ]; then
  inf "cobertura declarada: $(( 100 * (n_auto + n_ass + n_ise) / TOTAL ))% — o resto e divida NOMEADA"
fi

echo
if [ "$FAIL" -eq 0 ]; then
  printf '\033[32mVERDE\033[0m — o manifesto presta contas dos %d scripts\n' "$TOTAL"
  exit 0
fi
printf '\033[31mVERMELHO\033[0m — %d ramo(s) reprovaram\n' "$FAIL"
exit 1
