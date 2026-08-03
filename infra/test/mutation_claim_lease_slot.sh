#!/usr/bin/env bash
# mutation_claim_lease_slot.sh — prova por MUTAÇÃO do fix da lacuna 2 (2026-08-03):
# a vaga volta no `work_task_expire` mesmo quando a `claim_lease` já expirou.
#
# POR QUE ESTE SCRIPT EXISTE. Os dois testes novos de `test_pull_release_snapshot.py`
# nasceram junto com o código e foram rodados só depois do build — verdes de primeira.
# Um teste que não se sabe reprovar não é rede: é confiança comprada. Pior aqui do que
# em outros lugares, porque o defeito consertado era invisível por construção (uma vaga
# a menos, sem erro, sem log, até o SET expirar) — se o teste também for cego, o
# conserto passa a ser uma afirmação sem testemunha.
#
# ESTRUTURA: cada mutação desliga UMA peça e declara quais testes têm de ficar
# vermelhos e quais têm de continuar verdes. M2 existe porque a segurança do fix mora
# numa comparação de UMA linha (`occupant_pool(member) == pool_id`), e uma comparação
# é exatamente o tipo de coisa que se afrouxa sem ninguém notar.
#
# ATENÇÃO OPERACIONAL: as mutações são editadas no SOURCE DENTRO do container. Elas
# sobrevivem a `restart` e NÃO sobrevivem a `up -d --force-recreate` (que recria a
# partir da imagem). O `trap` restaura no fim, inclusive em Ctrl-C.
#
# Uso:  bash infra/test/mutation_claim_lease_slot.sh
# Pré:  routing-engine no ar com o código novo buildado.

# Sem `set -e`: um pytest vermelho é resultado ESPERADO aqui.
set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
SVC="routing-engine"
SRC="/app/packages/routing-engine/src/plughub_routing"
T="src/plughub_routing/tests/test_pull_release_snapshot.py"

FAIL=0
OUT="$(mktemp)"

restore() {
  $DC exec -T "$SVC" sh -c \
    "cp /tmp/mutcl_registry.bak $SRC/registry.py;
     cp /tmp/mutcl_router.bak   $SRC/router.py" >/dev/null 2>&1
}
trap 'restore; rm -f "$OUT"' EXIT

echo "── pré-condição: pytest instalado no container ─────────────────────────────"
# `build --no-cache` apaga o `pip install` ad-hoc, e sem esta checagem TODA chamada de
# pytest sairia 1 por "No module named pytest" — que o `expect_red` leria como
# reprovação, produzindo um run inteiro de ✅ sem ter executado teste nenhum.
$DC exec -T "$SVC" sh -c 'pip install -q pytest pytest-asyncio >/dev/null 2>&1; \
                          python -c "import pytest, pytest_asyncio"' || {
  echo "pytest/pytest-asyncio indisponíveis no container — abortando."
  echo "Sem eles nenhum resultado abaixo teria significado."
  exit 1
}
echo "   ok"

echo
echo "── backup do source dentro do container ────────────────────────────────────"
$DC exec -T "$SVC" sh -c \
  "cp $SRC/registry.py /tmp/mutcl_registry.bak;
   cp $SRC/router.py   /tmp/mutcl_router.bak" || {
  echo "backup falhou — abortando antes de mexer em qualquer coisa"; exit 1
}
echo "   ok"

# mutate <arquivo> <âncora> <substituto> <ocorrências esperadas>
mutate() {
  $DC exec -T -e MUT_FILE="$SRC/$1" -e MUT_OLD="$2" -e MUT_NEW="$3" -e MUT_N="$4" \
    "$SVC" python - <<'PY'
import os, sys
path = os.environ["MUT_FILE"]
old, new, want = os.environ["MUT_OLD"], os.environ["MUT_NEW"], int(os.environ["MUT_N"])
src = open(path).read()
got = src.count(old)
if got != want:
    print(f"   ÂNCORA NÃO CONFERE em {path}: esperava {want}, achei {got}.")
    print("   A mutação NÃO foi aplicada. Qualquer verde a seguir é AUSÊNCIA DE")
    print("   MUTAÇÃO, não robustez — corrigir a âncora antes de acreditar em nada.")
    sys.exit(2)
open(path, "w").write(src.replace(old, new))
print(f"   mutação aplicada ({got}x)")
PY
}

_run() {
  $DC exec -T -e REDIS_URL=redis://redis:6379 "$SVC" sh -c \
    "cd /app/packages/routing-engine && python -m pytest -p no:cacheprovider -q '$1'" \
    >"$OUT" 2>&1
}

# Códigos do pytest: 0 = passou · 1 = TESTE REPROVOU · 2 interrompido · 3 erro interno
# · 4 uso incorreto · 5 nada coletado. Só o 1 é vermelho; o resto é INCONCLUSIVO.
_verdict() {
  local rc="$1"
  if grep -qE 'No module named|ImportError|ModuleNotFoundError' "$OUT"; then
    echo inconclusive; return
  fi
  case "$rc" in
    0) grep -qE '[0-9]+ passed' "$OUT" && echo pass || echo inconclusive ;;
    1) grep -qE '[0-9]+ failed' "$OUT" && echo fail || echo inconclusive ;;
    *) echo inconclusive ;;
  esac
}

_inconclusive() {
  echo "   ⚠️  INCONCLUSIVO (o teste não chegou a julgar): $1"
  tail -8 "$OUT" | sed 's/^/      /'
  FAIL=1
}

expect_red() {
  # `local rc=$?` na MESMA linha do _run: um `local v;` intermediário zeraria `$?`.
  _run "$1"; local rc=$?
  local v; v="$(_verdict "$rc")"
  case "$v" in
    fail) echo "   ✅ vermelho: $1" ;;
    pass) echo "   ❌ ESPERAVA VERMELHO e passou: $1"
          echo "      → o teste não exercita a peça desligada; não é rede para ela."
          FAIL=1 ;;
    *)    _inconclusive "$1" ;;
  esac
}

expect_green() {
  _run "$1"; local rc=$?
  local v; v="$(_verdict "$rc")"
  case "$v" in
    pass) echo "   ✅ verde (não depende desta peça): $1" ;;
    fail) echo "   ❌ ESPERAVA VERDE e reprovou: $1"
          echo "      → a mutação atingiu mais do que devia; a atribuição está errada."
          tail -15 "$OUT" | sed 's/^/      /'
          FAIL=1 ;;
    *)    _inconclusive "$1" ;;
  esac
}

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "── M1 · o expire volta a derivar o dono SÓ da lease (código pré-fix) ───────"
# Reproduz exatamente o comportamento anterior: sem lease, sem dono, vaga presa.
restore
mutate router.py \
'            instance_id = await self._instances.find_occupant_instance(
                tenant_id, pool_id, session_id
            ) or ""' \
'            instance_id = ""   # MUTAÇÃO: sem fallback pelo semáforo' 1 || exit 2
expect_red   "$T::test_expire_returns_the_slot_even_after_the_lease_expired"
# GUARDA: o caminho com lease viva não passa pelo fallback. Se ficar vermelho aqui, a
# mutação vazou para além da peça que ela desliga.
expect_green "$T::test_expire_of_a_claimed_item_rewrites_the_snapshot"
expect_green "$T::test_expire_of_a_never_claimed_item_does_not_invent_capacity"
expect_green "$T::test_expire_without_lease_never_takes_a_slot_of_another_pool"

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "── M2 · a busca deixa de discriminar o POOL do ocupante ────────────────────"
# A segurança inteira do fix mora nesta comparação. Sem ela a busca devolve a vaga de
# QUALQUER pool que a sessão ocupe — que é precisamente o risco ("derrubar occupant
# alheio") usado para justificar depender só da lease. Se este teste passar mutado, o
# fix está apoiado numa linha que ninguém cobre.
restore
mutate registry.py \
'                if mp == pool_id:' \
'                if True:   # MUTAÇÃO: aceita ocupante de qualquer pool' 1 || exit 2
expect_red   "$T::test_expire_without_lease_never_takes_a_slot_of_another_pool"
# GUARDA: afrouxar a comparação não pode quebrar o caminho feliz — lá o pool CASA.
expect_green "$T::test_expire_returns_the_slot_even_after_the_lease_expired"

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "── restaurando o source e conferindo que a suíte voltou ao verde ───────────"
restore
_run "$T"; RC=$?
V="$(_verdict "$RC")"
if [ "$V" = pass ]; then
  echo "   ✅ suíte restaurada e verde"
else
  echo "   ❌ a suíte NÃO voltou ao verde após restaurar ($V) — o container pode ter"
  echo "      ficado com source mutado. Rode: $DC up -d --force-recreate $SVC"
  tail -15 "$OUT" | sed 's/^/      /'
  FAIL=1
fi

echo
if [ "$FAIL" -eq 0 ]; then
  echo "══ TODAS as atribuições conferem ═══════════════════════════════════════════"
  echo "   Cada peça do fix tem um teste que a derruba, e nenhum teste depende de"
  echo "   peça que não é sua."
  exit 0
fi
echo "══ HÁ ATRIBUIÇÃO ERRADA — ver acima ═══════════════════════════════════════"
echo "   Um 'esperava vermelho e passou' é o achado: aquele teste compra confiança"
echo "   sem entregar cobertura. INCONCLUSIVO não é vermelho — o teste não julgou."
exit 1
