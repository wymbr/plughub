#!/usr/bin/env bash
# ==============================================================================
# mut_gates_manifest_coverage.sh — falseabilidade do probe da GAT-01
# ==============================================================================
#
# Um gate que confere COBERTURA e o candidato mais obvio a ficar verde por nao
# medir: basta a lista de scripts vir vazia, ou o parser do manifesto engolir
# tudo. Esta bateria planta os quatro defeitos que o probe existe para pegar e
# exige VERMELHO em cada um.
#
#   M1  um script deixa de ser declarado          -> ramo B
#   M2  o manifesto cita arquivo inexistente      -> ramo C
#   M3  um nome declarado em duas classes         -> ramo D
#   M4  o criterio informativo aceita TUDO        -> ramo E
#
# M1..M3 usam manifesto de mentira via `GATE_MANIFEST` e nao encostam no de
# verdade. M4 e o unico que edita arquivo — o censo — e restaura em `trap`.
#
# Saida: 0 todas as mutacoes foram pegas · 1 alguma sobreviveu · 2 nao mediu
# ==============================================================================
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROBE="$HERE/probe_gates_manifest_coverage.sh"
CENSO="$HERE/_gates_manifest_census.py"
MAN="$HERE/gates.manifest"

[ -f "$PROBE" ] || { echo "INCONCLUSIVO: probe ausente"; exit 2; }
[ -f "$CENSO" ] || { echo "INCONCLUSIVO: censo ausente"; exit 2; }

TMP="$(mktemp -d)"
BACKUP="$TMP/censo.orig"
cp "$CENSO" "$BACKUP"
restaura() { cp "$BACKUP" "$CENSO"; rm -rf "$TMP"; }
trap restaura EXIT INT TERM

FAIL=0
ok()  { printf '  \033[32mv\033[0m %s\n' "$1"; }
bad() { printf '  \033[31mx\033[0m %s\n' "$1"; FAIL=$((FAIL + 1)); }

# Controle POSITIVO: sem mutacao nenhuma, o probe tem de estar VERDE. Sem isso,
# uma bateria em que tudo reprova por outro motivo passaria inteira.
printf '\033[1m== mut_gates_manifest_coverage ==\033[0m\n\n'
echo "-- M0. controle positivo: sem mutacao, o probe passa --------------------"
if bash "$PROBE" >"$TMP/m0.log" 2>&1; then
  ok "probe VERDE antes de qualquer mutacao"
else
  bad "o probe JA esta vermelho — as mutacoes abaixo nao provariam nada"
  tail -6 "$TMP/m0.log" | sed 's/^/       /'
  echo; echo "INCONCLUSIVO: sem controle positivo nao ha o que falsear"; exit 2
fi

roda_com() {   # $1 = manifesto  $2 = rotulo  $3 = ramo esperado no texto
  if GATE_MANIFEST="$1" bash "$PROBE" >"$TMP/out.log" 2>&1; then
    bad "$2 SOBREVIVEU — o probe ficou verde com o defeito plantado"
  else
    if grep -q "$3" "$TMP/out.log"; then
      ok "$2 pego pelo ramo $3"
    else
      bad "$2 reprovou, mas NAO pelo ramo $3 (pegou outra coisa)"
      grep -E '^  x' "$TMP/out.log" | head -3 | sed 's/^/       /'
    fi
  fi
}

echo
echo "-- M1. um script deixa de ser declarado ---------------------------------"
grep -v '^probe_task_ledger\.sh' "$MAN" > "$TMP/m1.manifest"
roda_com "$TMP/m1.manifest" "M1" "B —"

echo
echo "-- M2. o manifesto cita arquivo inexistente -----------------------------"
cp "$MAN" "$TMP/m2.manifest"
printf 'probe_este_arquivo_nao_existe.sh\n' >> "$TMP/m2.manifest"
roda_com "$TMP/m2.manifest" "M2" "C —"

echo
echo "-- M3. um nome declarado em duas classes --------------------------------"
cp "$MAN" "$TMP/m3.manifest"
printf '?probe_task_ledger.sh\n' >> "$TMP/m3.manifest"
roda_com "$TMP/m3.manifest" "M3" "D —"

echo
echo "-- M4. o criterio informativo aceita TUDO -------------------------------"
# `e_candidato` passa a devolver True sempre. E a forma mais barata de um
# criterio "funcionar" sem discriminar nada — foi assim que a primeira versao
# deste censo contou 228 de 280 como gate.
python3 - "$CENSO" <<'PY'
import io, sys
p = sys.argv[1]
t = io.open(p, encoding="utf-8").read()
alvo = "def e_candidato(texto):"
assert alvo in t, "assinatura de e_candidato mudou — atualize a mutacao M4"
t = t.replace(alvo, alvo + "\n    return True  # MUTACAO M4", 1)
io.open(p, "w", encoding="utf-8", newline="").write(t)
PY
if [ $? -ne 0 ]; then
  bad "M4 nao pode ser aplicada (a assinatura mudou?)"
else
  if bash "$PROBE" >"$TMP/m4.log" 2>&1; then
    bad "M4 SOBREVIVEU — criterio que aceita tudo passou pelo ramo E"
  elif grep -q "E —" "$TMP/m4.log"; then
    ok "M4 pego pelo ramo E (controle negativo virou candidato)"
  else
    bad "M4 reprovou por outro ramo, nao pelo E"
    grep -E '^  x' "$TMP/m4.log" | head -3 | sed 's/^/       /'
  fi
fi
restaura_parcial=$(cp "$BACKUP" "$CENSO" && echo ok)
[ "$restaura_parcial" = "ok" ] || bad "NAO consegui restaurar o censo — confira $CENSO"

echo
echo "-- M5. conferencia: o censo voltou ao original --------------------------"
if diff -q "$BACKUP" "$CENSO" >/dev/null; then
  ok "censo identico ao original"
else
  bad "o censo FICOU mutado — restaure de $BACKUP antes de commitar"
fi

echo
if [ "$FAIL" -eq 0 ]; then
  printf '\033[32mVERDE\033[0m — as 4 mutacoes foram pegas, cada uma pelo ramo certo\n'
  exit 0
fi
printf '\033[31mVERMELHO\033[0m — %d mutacao(oes) nao foram pegas como deviam\n' "$FAIL"
exit 1
