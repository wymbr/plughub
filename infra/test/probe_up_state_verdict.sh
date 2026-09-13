#!/usr/bin/env bash
# probe_up_state_verdict.sh — a conferência de estado do `up.sh` reprova seed morto
# e não reprova seed concluído (GAT-05).
#
# O DEFEITO QUE ESTE PROBE FECHA
# ==============================
# O `up.sh` excluía os one-shots da conferência por uma LISTA de nomes, sem olhar o
# exit code. Medido em 2026-09-12/13, os dois sentidos do mesmo erro:
#   · o `auth-seed` saiu com exit 1 em toda subida desde a MOD-11, e o script dizia
#     "Stack no ar" (seed morto = seed concluído);
#   · o `context-map-seed` nunca entrou na lista, e o script reprovava toda subida
#     CORRETA desde 2026-09-02 (seed concluído = serviço caído).
#
# O julgamento hoje é `infra/scripts/_up_state_verdict.py`, função pura de
# (compose, ps). Este probe o exercita com entradas SINTÉTICAS — a única forma de o
# ramo "seed morto" ficar vermelho sem precisar quebrar um seed de verdade.
#
# RAMOS
#   A  classificação derivada do compose (restart:"no" ∪ service_completed_successfully)
#   B  veredicto: os casos que já custaram, cada um com o código esperado
#   C  a lista fixa não voltou ao `up.sh`
#   D  ao vivo: o compose REAL classifica os one-shots conhecidos, e o estado atual
#      é julgado (INCONCLUSIVO sem docker — nunca verde por ausência)
#
# Uso:  bash infra/test/probe_up_state_verdict.sh
set -uo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# UP_VERDICT existe só para a bateria de mutação apontar um avaliador adulterado.
V="${UP_VERDICT:-$RAIZ/infra/scripts/_up_state_verdict.py}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
FAIL=0; INC=0
T=$'\t'
ok()  { echo "  ✓ $*"; }
bad() { echo "  ✗ $*"; FAIL=$((FAIL+1)); }
inc() { echo "  ? $*"; INC=$((INC+1)); }
achatado() { tr '\n' ' ' < "$1"; }

echo "== probe_up_state_verdict — seed morto reprova, seed concluído não =="
command -v python3 >/dev/null || { echo "INCONCLUSIVO — python3 ausente"; exit 2; }
[ -f "$V" ] || { echo "VERMELHO — avaliador ausente: $V"; exit 1; }

# compose sintético: `api` long-running; `seed_no` com restart:"no"; `init_dep` sem
# restart, mas esperado por completed_successfully (a forma do minio-init);
# `seed_b` restart:"no".
cat > "$TMP/compose.json" <<'JSON'
{"services": {
  "api":      {"depends_on": {"init_dep": {"condition": "service_completed_successfully"}}},
  "seed_no":  {"restart": "no"},
  "init_dep": {},
  "seed_b":   {"restart": "no", "depends_on": {"api": {"condition": "service_healthy"}}}
}}
JSON

# ⚠️ SEM subshell: `X="$(caso …)"` rodaria `bad` num subshell e o incremento de FAIL
# morreria lá — o probe não poderia reprovar. A saída do avaliador vai para arquivo.
caso() {  # rot esperado linha-do-ps...
  local rot="$1" esp="$2"; shift 2
  printf '%s\n' "$@" > "$TMP/ps.tsv"
  python3 "$V" "$TMP/compose.json" "$TMP/ps.tsv" > "$TMP/out.txt" 2>&1
  local rc=$?
  if [ "$rc" = "$esp" ]; then
    ok "$rot → exit $rc"
  else
    bad "$rot → exit $rc (esperado $esp): $(achatado "$TMP/out.txt")"
  fi
}

echo
echo "A) classificação derivada do compose"
CLS="$(python3 "$V" --oneshots "$TMP/compose.json" | paste -sd, -)"
if [ "$CLS" = "init_dep,seed_b,seed_no" ]; then
  ok "one-shots = $CLS (o dependente-concluído entra sem declarar restart)"
else
  bad "one-shots = [$CLS] (esperado init_dep,seed_b,seed_no)"
fi

echo
echo "B) veredicto"
caso "tudo certo" 0 "api${T}running${T}0" "seed_no${T}exited${T}0" "init_dep${T}exited${T}0" "seed_b${T}exited${T}0"
caso "seed MORTO (o caso auth-seed)" 1 "api${T}running${T}0" "seed_no${T}exited${T}1" "init_dep${T}exited${T}0"
if grep -q "seed_no exited (exit 1)" "$TMP/out.txt"; then
  ok "a recusa NOMEIA o seed e o código"
else
  bad "a recusa não nomeia o seed: $(achatado "$TMP/out.txt")"
fi
caso "one-shot fora da lista antiga, exit 0 (o caso context-map-seed)" 0 "api${T}running${T}0" "init_dep${T}exited${T}0"
caso "long-running que saiu com 0 NÃO concluiu" 1 "api${T}exited${T}0" "seed_no${T}exited${T}0"
caso "one-shot ainda rodando é PENDENTE, não verde" 2 "api${T}running${T}0" "seed_b${T}running${T}0"
caso "one-shot em 'created' (dependência falhou)" 1 "api${T}running${T}0" "seed_b${T}created${T}0"
caso "pendente não mascara vermelho" 1 "api${T}exited${T}137" "seed_b${T}running${T}0"
caso "listagem vazia é INCONCLUSIVO" 3 ""
echo '{"nope": 1}' > "$TMP/ruim.json"
printf 'api\trunning\t0\n' > "$TMP/ps.tsv"
python3 "$V" "$TMP/ruim.json" "$TMP/ps.tsv" > /dev/null 2>&1; rc=$?
if [ "$rc" = 3 ]; then
  ok "compose sem services → exit 3 (sem classificação, sem veredicto)"
else
  bad "compose sem services → exit $rc (esperado 3)"
fi

echo
echo "C) a lista fixa não voltou"
UP="$RAIZ/infra/scripts/up.sh"
if grep -qE '^[[:space:]]*ONESHOTS=' "$UP"; then
  bad "up.sh voltou a classificar one-shot por LISTA de nomes"
elif ! grep -q "_up_state_verdict.py" "$UP"; then
  bad "up.sh não chama o avaliador — a conferência não usa o que este probe mede"
else
  ok "up.sh julga pelo avaliador, sem lista de nomes"
fi

echo
echo "D) ao vivo — compose REAL"
DC=(docker compose -f "$RAIZ/docker-compose.demo.yml")
if command -v docker >/dev/null && "${DC[@]}" config --format json > "$TMP/real.json" 2>/dev/null; then
  REAL=" $(python3 "$V" --oneshots "$TMP/real.json" | paste -sd' ' -) "
  FALTA=""
  for s in auth-seed config-seed context-map-seed dialog-seed eval-seed kafka-init minio-init pricing-seed; do
    case "$REAL" in *" $s "*) ;; *) FALTA="$FALTA $s" ;; esac
  done
  if [ -z "$FALTA" ]; then
    ok "o compose real classifica os 8 one-shots conhecidos:${REAL}"
  else
    bad "one-shot(s) conhecido(s) fora da classificação:$FALTA"
  fi
  if "${DC[@]}" ps -a --format "{{.Service}}${T}{{.State}}${T}{{.ExitCode}}" > "$TMP/real.tsv" 2>/dev/null \
     && [ -s "$TMP/real.tsv" ]; then
    python3 "$V" "$TMP/real.json" "$TMP/real.tsv" > "$TMP/out.txt" 2>&1; rc=$?
    case "$rc" in
      0) ok "estado atual da stack: $(achatado "$TMP/out.txt")" ;;
      1) bad "estado atual da stack VERMELHO: $(achatado "$TMP/out.txt")" ;;
      *) inc "estado atual não julgável agora (exit $rc): $(achatado "$TMP/out.txt")" ;;
    esac
  else
    inc "stack sem containers — o estado ao vivo não foi medido"
  fi
else
  inc "docker/compose indisponível — ramo D não mediu"
fi

echo "======================"
if [ "$FAIL" -gt 0 ]; then echo "VERMELHO ($FAIL)"; exit 1; fi
if [ "$INC" -gt 0 ]; then echo "INCONCLUSIVO ($INC)"; exit 2; fi
echo "VERDE"
