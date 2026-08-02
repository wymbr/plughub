#!/usr/bin/env bash
# mutation_occupancy_peak.sh — prova por MUTAÇÃO da F3a + P1 (pico event-driven).
#
# POR QUE ESTE SCRIPT EXISTE. Os testes de `test_pull_release_snapshot.py` e
# `test_pool_occupancy_peak.py` foram escritos junto com o código e rodados só depois
# do build — nunca foram vistos vermelhos. Um teste que não se sabe reprovar não é
# rede: é confiança comprada. Aqui cada mutação desliga UMA peça do código novo e
# afirma exatamente quais testes têm de ficar vermelhos e quais têm de continuar
# verdes. Se um "vermelho esperado" passar, o teste não testa o que diz testar.
#
# MODO DE FALHA QUE O SCRIPT PRECISA EVITAR: mutação que não se aplica (âncora moveu
# com uma edição posterior) e o verde subsequente parecer robustez. Por isso a
# aplicação CONFERE a contagem de ocorrências e aborta com código 2 se não bater.
#
# ATENÇÃO OPERACIONAL: as mutações são editadas no SOURCE DENTRO do container. Elas
# sobrevivem a `restart` e NÃO sobrevivem a `up -d --force-recreate` (que recria a
# partir da imagem). O `trap` restaura no fim, inclusive em Ctrl-C. Se o script morrer
# de um jeito que escape do trap, `up -d --force-recreate routing-engine` limpa tudo.
#
# Uso:  bash infra/test/mutation_occupancy_peak.sh
# Pré:  routing-engine no ar com o código novo já buildado (build --no-cache).

# Sem `set -e`: precisamos LER o código de saída do pytest, e um pytest vermelho é
# resultado esperado aqui. `set -e` mataria o script no primeiro sucesso do teste.
set -uo pipefail

DC="docker compose -f docker-compose.demo.yml"
SVC="routing-engine"
SRC="/app/packages/routing-engine/src/plughub_routing"
T_PULL="src/plughub_routing/tests/test_pull_release_snapshot.py"
T_PEAK="src/plughub_routing/tests/test_pool_occupancy_peak.py"
T_ROLL="src/plughub_routing/tests/test_tenant_capacity_rollup.py"
T_TOT="src/plughub_routing/tests/test_tenant_occupancy_total.py"

FAIL=0
OUT="$(mktemp)"

restore() {
  $DC exec -T "$SVC" sh -c \
    "cp /tmp/mut_registry.bak $SRC/registry.py;
     cp /tmp/mut_router.bak   $SRC/router.py;
     cp /tmp/mut_main.bak     $SRC/main.py" >/dev/null 2>&1
}
trap 'restore; rm -f "$OUT"' EXIT

echo "── pré-condição: pytest instalado no container ─────────────────────────────"
# Um `build --no-cache` apaga o `pip install` ad-hoc, e sem esta checagem TODA chamada
# de pytest sai com código 1 por "No module named pytest" — que o `expect_red` leria
# como reprovação. Foi o que aconteceu em 2026-08-02: um run inteiro de ✅ que não
# tinha executado teste nenhum. Não-zero não é vermelho; vermelho é o teste julgando e
# reprovando.
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
  "cp $SRC/registry.py /tmp/mut_registry.bak;
   cp $SRC/router.py   /tmp/mut_router.bak;
   cp $SRC/main.py     /tmp/mut_main.bak" || {
  echo "backup falhou — abortando antes de mexer em qualquer coisa"; exit 1
}

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

_run() {  # roda um node-id; devolve o código de saída do pytest
  $DC exec -T -e REDIS_URL=redis://redis:6379 "$SVC" sh -c \
    "cd /app/packages/routing-engine && python -m pytest -p no:cacheprovider -q '$1'" \
    >"$OUT" 2>&1
}

# Códigos do pytest: 0 = passou · 1 = TESTE REPROVOU · 2 interrompido · 3 erro interno
# · 4 uso incorreto · 5 nada coletado. Só o 1 é vermelho. Qualquer outro — e o 1 vindo
# do interpretador ("No module named pytest") — é INCONCLUSIVO: o teste não chegou a
# julgar, e tratar isso como vermelho compra a mesma confiança falsa que o verde vazio.
_verdict() {   # → "pass" | "fail" | "inconclusive"
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
  # `local rc=$?` na MESMA linha do _run: um `local v;` intermediário zeraria `$?`
  # (o builtin `local` devolve 0), e o veredicto julgaria o código de saída do
  # próprio `local` em vez do do pytest. Foi o que aconteceu em 2026-08-02 — seis
  # vermelhos legítimos classificados como INCONCLUSIVO.
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
  _run "$1"; local rc=$?          # ver a nota em expect_red sobre `local` e `$?`
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
echo "── M1 · F3a: a liberação do pull deixa de recomputar o snapshot ────────────"
restore
mutate router.py \
  "await self._instances.refresh_snapshots_for_instance(" \
  "await _mut_no_refresh(" 2 || exit 2
$DC exec -T "$SVC" sh -c \
  "printf '\n\nasync def _mut_no_refresh(*a, **k):\n    return {}\n' >> $SRC/router.py"
expect_red   "$T_PULL::test_release_rewrites_the_snapshot_of_every_pool_of_the_resource"
expect_red   "$T_PULL::test_expire_of_a_claimed_item_rewrites_the_snapshot"
# Havia um terceiro esperado-vermelho aqui — `test_release_snapshot_shows_the_requeued_item`,
# sobre `queue_length`. Esta mutação o pegou PASSANDO: `add_queued_contact` já faz patch
# in-place daquele campo, então ele tem dois escritores e nunca dependeu do refresh. O
# teste foi deletado (ver o comentário no lugar dele em test_pull_release_snapshot.py).
# Este é o achado que o script existe para produzir: um teste que comprava confiança.
# O 4º é GUARDA (o expire de item nunca reivindicado não pode inventar capacidade):
# ele não depende do refresh, e ficar vermelho aqui significaria mutação vazando.
expect_green "$T_PULL::test_expire_of_a_never_claimed_item_does_not_invent_capacity"

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "── M2 · P1: o bump da ALOCAÇÃO desligado ───────────────────────────────────"
restore
mutate registry.py \
  '        b = minute_bucket()
        for pool_id, occ in (occupancies or {}).items():' \
  '        return   # MUTAÇÃO
        b = minute_bucket()
        for pool_id, occ in (occupancies or {}).items():' 1 || exit 2
expect_red   "$T_PEAK::test_peak_is_projected_per_pool_not_per_resource"
# DIVULGAÇÃO HONESTA: estes dois continuam verdes com o bump desligado, porque o SEED
# da liberação escreve o valor pré-release e cobre o mesmo minuto. Não é falha do
# teste — é o contrato sendo satisfeito por outra peça. Quem isola o bump é o de cima;
# quem prova que o contrato inteiro pode cair é a M4.
expect_green "$T_PEAK::test_peak_that_rises_and_falls_between_flushes_is_published"
expect_green "$T_PEAK::test_release_never_lowers_the_peak"

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "── M3 · P1: o seed por EVENTO na liberação desligado ───────────────────────"
restore
mutate registry.py \
  '        try:
            b = minute_bucket()' \
  '        return   # MUTAÇÃO
        try:
            b = minute_bucket()' 1 || exit 2
expect_red   "$T_PEAK::test_carried_load_is_seeded_by_the_release_event"
expect_green "$T_PEAK::test_peak_is_projected_per_pool_not_per_resource"

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "── M4 · P1: bump E seed desligados → o CONTRATO tem de cair ────────────────"
restore
mutate registry.py \
  '        b = minute_bucket()
        for pool_id, occ in (occupancies or {}).items():' \
  '        return   # MUTAÇÃO
        b = minute_bucket()
        for pool_id, occ in (occupancies or {}).items():' 1 || exit 2
mutate registry.py \
  '        try:
            b = minute_bucket()' \
  '        return   # MUTAÇÃO
        try:
            b = minute_bucket()' 1 || exit 2
# Este é o teste que representa o arco inteiro: um pico que sobe a 2 e volta a 0 dentro
# do minuto tem de aparecer no valor publicado. Sem nenhuma das duas escritas, o valor
# publicado é 0 — que é exatamente o que a amostragem de 5 s produzia.
expect_red   "$T_PEAK::test_peak_that_rises_and_falls_between_flushes_is_published"
expect_red   "$T_PEAK::test_release_never_lowers_the_peak"

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "── M5 · achado 1: capacidade volta a ser lida no FLUSH, não no pico ────────"
restore
mutate main.py \
  '        caps[(tenant_id, pool_id)]  = int(raw_cap) if raw_cap is not None else None' \
  '        caps[(tenant_id, pool_id)]  = None   # MUTAÇÃO' 1 || exit 2
expect_red   "$T_PEAK::test_capacity_is_captured_at_the_peak_instant"

# ─────────────────────────────────────────────────────────────────────────────
echo
echo "── M6 · invariante: o bump migra para dentro de write_pool_snapshot ────────"
restore
mutate registry.py \
  '        return occ

    async def get_pool_snapshot(' \
  '        await self.record_pool_peak(   # MUTAÇÃO
            tenant_id, pool_id, occ["used_here"], occ["total_capacity"])
        return occ

    async def get_pool_snapshot(' 1 || exit 2
# É a deriva que o desenho teme: quem escreve snapshot sobe o pico, a F3a passa a
# bumpar em LIBERAÇÕES, e o pico volta a ser amostrado nos instantes de escrita de
# snapshot — numericamente inofensivo, semanticamente de volta à amostragem. Este
# teste é o "nada ficando vermelho" deixando de ser nada.
expect_red   "$T_PEAK::test_writing_a_snapshot_does_not_record_a_peak"

# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# F4 — rollup de capacidade por tipo de licença.
#
# NOTA HONESTA sobre o que NÃO está aqui: o teste central
# (`test_shared_resource_is_counted_once_not_once_per_pool`) não tem mutação de uma
# linha que o derrube, porque a deduplicação é ESTRUTURAL — agregar por instância
# distinta não é uma linha que se desliga, é a forma do laço. A falsificabilidade dele
# vem de outro lugar, e é mais forte: o teste calcula o número ERRADO dentro dele mesmo
# (`_sum_of_pool_lines` → 6) e exige o certo (3). Qualquer implementação que agregue por
# pool reprova por construção. As mutações abaixo cobrem o que É uma linha.
echo
echo "── M7 · F4: tipo ausente vira 'human' em vez de 'unknown' ──────────────────"
restore
mutate registry.py \
  '                inst_kinds.setdefault(iid, set()).add(kind or "unknown")' \
  '                inst_kinds.setdefault(iid, set()).add(kind or "human")   # MUTAÇÃO' 1 || exit 2
# Assumir `human` é o default PLAUSÍVEL — e o pior: humano é a moeda cara, e
# superestimar disponibilidade humana faz oferecer atendimento que não existe.
expect_red   "$T_ROLL::test_pool_without_agent_kind_is_unknown_not_assumed_human"
# O caso MISTO fica verde aqui, e a expectativa contrária estava errada (não o teste):
# os dois pools DECLARAM tipo, então o default `kind or "unknown"` nunca é alcançado —
# aquele `unknown` vem do ramo `len(kinds) > 1`. São duas peças distintas; a M7b é a
# mutação da outra.
expect_green "$T_ROLL::test_instance_in_pools_of_different_kinds_falls_into_unknown"

echo
echo "── M7b · F4: conflito de tipo escolhe um lado em silêncio ──────────────────"
restore
mutate registry.py \
  '            if len(kinds) > 1:
                mixed.append(iid)
                kind = "unknown"' \
  '            if len(kinds) > 1:
                mixed.append(iid)
                kind = sorted(kinds)[0]   # MUTAÇÃO' 1 || exit 2
# Escolher um lado produz um número plausível: a instância consome UMA licença, e qual
# moeda é indeterminado a partir do dado. `unknown` publicado é a degradação honesta.
expect_red   "$T_ROLL::test_instance_in_pools_of_different_kinds_falls_into_unknown"

echo
echo "── M7c · F4: pools_available conta por CANAL, ignorando o tipo ─────────────"
restore
mutate registry.py \
  '                            key = (kind or "unknown", ch)' \
  '                            key = ("human", ch)   # MUTAÇÃO' 1 || exit 2
# O defeito real observado no tenant demo (human/whatsapp e ai/whatsapp com 19
# idênticos): a fungibilidade voltando pelo campo vizinho ao que a separa.
expect_red   "$T_ROLL::test_pools_available_survives_as_an_additive_count_but_per_kind"

echo
echo "── M8 · F4: um 'available' escalar no topo do rollup ───────────────────────"
restore
mutate registry.py \
  '        out = {
            "tenant_id":   tenant_id,
            "by_kind":     by_kind,' \
  '        out = {
            "tenant_id":   tenant_id,
            "available":   sum(k["available"] for k in by_kind.values()),  # MUTAÇÃO
            "by_kind":     by_kind,' 1 || exit 2
# Somar humano com IA é a falácia de aditividade um nível acima: em vez de contar o
# mesmo recurso duas vezes, soma recursos que não se substituem.
expect_red   "$T_ROLL::test_human_and_ai_are_never_summed"

echo
echo "── M9 · F4: throttle do rollup desligado ───────────────────────────────────"
restore
mutate registry.py \
  '        if not force:
            try:
                got = await self._redis.set(' \
  '        if False:   # MUTAÇÃO
            try:
                got = await self._redis.set(' 1 || exit 2
expect_red   "$T_ROLL::test_rollup_is_persisted_and_throttled"

# ─────────────────────────────────────────────────────────────────────────────
# P2 — total do tenant (ZSET fonte + contador conferível).
echo
echo "── M10 · P2: o espelho da ocupação do tenant desligado ─────────────────────"
restore
mutate registry.py \
        '        try:
            total = int(await self._redis.eval(
                _UPDATE_TENANT_OCCUPANCY_LUA, 2,' \
        '        return None   # MUTAÇÃO
        try:
            total = int(await self._redis.eval(
                _UPDATE_TENANT_OCCUPANCY_LUA, 2,' 1 || exit 2
expect_red   "$T_TOT::test_counter_tracks_the_zset_which_is_the_source"
expect_red   "$T_TOT::test_tenant_peak_catches_simultaneity_that_pools_cannot_show"

echo
echo "── M11 · P2: total negativo CLAMPADO em vez de denunciado ──────────────────"
restore
mutate registry.py \
        "redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[3]))
return total" \
        "redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[3]))
if total < 0 then total = 0 redis.call('SET', KEYS[2], 0) end   -- MUTAÇÃO
return total" 1 || exit 2
# O chão foi removido da fatia 2 porque escondia um modelo errado. Aqui esconderia a
# única evidência de que existe caminho de vaga fora dos três ganchos.
expect_red   "$T_TOT::test_counter_is_never_clamped"

echo
echo "── M12 · P2: reconciliação corrige em SILÊNCIO ─────────────────────────────"
restore
mutate registry.py \
        '            logger.warning(
                "DRIFT de ocupação tenant=%s' \
        '            logger.debug(   # MUTAÇÃO
                "DRIFT de ocupação tenant=%s' 1 || exit 2
# Conserto que apaga a evidência de que havia o que consertar. É o que separa este
# contador do `active_count`: sem a denúncia, ele volta a divergir em silêncio.
expect_red   "$T_TOT::test_reconciliation_corrects_the_counter_and_says_so"

echo
echo '── M13 · P2: __total__ volta a ser derivado dos pools ──────────────────────'
restore
mutate registry.py \
        '        # conferível (`{t}:occupancy:total`), leitura O(1).
        total = await self.get_tenant_occupancy(tenant_id)' \
        '        # conferível (`{t}:occupancy:total`), leitura O(1).
        total = sum(o.get("used_here", 0) for o in (occupancies or {}).values())  # MUTAÇÃO' 1 || exit 2
# A mutação mais próxima do defeito real: derivar o total do fan-out do momento em vez
# do contador. Quem a denuncia é o cenário SEM LIBERAÇÃO — e descobrir isso foi o
# achado desta mutação (2026-08-02): os dois testes de contrato passavam com o bump
# derivando dos pools, porque ambos têm uma liberação e o SEED de liberação grava o
# total pré-release, cobrindo o valor certo por outro caminho. Mesmo fenômeno da M2 no
# P1 — o contrato satisfeito por uma peça enquanto a outra está quebrada. O teste
# `test_allocation_alone_records_the_tenant_total` nasceu daqui.
expect_red   "$T_TOT::test_allocation_alone_records_the_tenant_total"
# DIVULGAÇÃO: os dois de contrato NÃO caem aqui, e a expectativa contrária (registrada
# na 1ª versão desta mutação) estava errada — não os testes.
expect_green "$T_TOT::test_tenant_peak_catches_simultaneity_that_pools_cannot_show"
expect_green "$T_TOT::test_tenant_peak_is_the_real_maximum_not_the_sum_of_pool_peaks"

echo
echo "── restauro + suíte limpa (tem de voltar tudo verde) ───────────────────────"
restore
$DC exec -T -e REDIS_URL=redis://redis:6379 "$SVC" sh -c \
  "cd /app/packages/routing-engine && python -m pytest -p no:cacheprovider -q \
     $T_PULL $T_PEAK $T_ROLL $T_TOT" 2>&1 | tail -5

echo
if [ "$FAIL" -eq 0 ]; then
  echo "RESULTADO: cada peça nova tem ao menos um teste que reprova sem ela."
else
  echo "RESULTADO: ❌ ver acima — teste que não reprova quando deveria, mutação que"
  echo "vazou, ou execução INCONCLUSIVA. Inconclusivo não é vermelho: significa que o"
  echo "teste não chegou a julgar, e nada neste run pode ser lido como prova."
fi
exit "$FAIL"
