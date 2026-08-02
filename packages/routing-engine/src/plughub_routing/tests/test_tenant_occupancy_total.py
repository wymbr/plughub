"""
test_tenant_occupancy_total.py — P2: o `__total__` do tenant, exato.

**Por que não bastava o P1.** O watermark por pool não produz o total do tenant:
`max` de SOMAS ≠ soma de `max`. A série de 2026-08-02 é a prova viva — no minuto 16:39
quatro pools registraram pico 1 e o `__total__` foi 2, porque os picos ocorreram em
instantes diferentes e no máximo dois coexistiram. Somar os watermarks daria 4.

**O que foi construído.** Um ZSET `{t}:occupancy` (`instance → ocupação`, `ZREM` em
zero) como FONTE, e um contador `{t}:occupancy:total` como atalho O(1), atualizados
juntos num Lua que tira o delta de `ZSCORE` antes/depois. O contador é da mesma família
do `{t}:pool:{p}:active_count` que este arco removeu — e a diferença não é de forma, é
de regime: escopo certo, fonte contra a qual conferir, e conferência que de fato roda
(`reconcile_tenant_occupancy`, 1×/min no flusher). **Se a reconciliação sair, o contador
tem de sair junto** — sem ela ele é o `active_count` outra vez.

**Como estes testes reprovam.** O primeiro é o contrato: dois picos em instantes
diferentes no mesmo minuto, e o total publicado tem de ser o máximo REAL (2), não a soma
dos picos por pool (4) nem o valor do fim do minuto (0). Os demais prendem o invariante
do contador (nunca clampado, sempre conferível), o `ZREM` em zero, e o fato de que a
reconciliação corrige E DENUNCIA.

Teste de INTEGRAÇÃO: precisa de um Redis real (tudo é Lua).
    REDIS_URL=redis://redis:6379 pytest test_tenant_occupancy_total.py
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
import redis.asyncio as aioredis

from plughub_routing.models import AgentInstance
from plughub_routing.registry import (
    InstanceRegistry,
    _pool_instances_key,
    _pool_peak_key,
    _tenant_occupancy_total_key,
    _tenant_occupancy_zset_key,
    minute_bucket,
)


REDIS_URL = (
    os.environ.get("REDIS_URL")
    or os.environ.get("PLUGHUB_REDIS_URL")
    or "redis://localhost:6379"
)

POOL_A, POOL_B = "tot_pool_a", "tot_pool_b"
CAPACITY = 3


@pytest.fixture
async def env():
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        pytest.skip(f"Redis indisponível em {REDIS_URL} — teste de integração pulado")
    reg    = InstanceRegistry(client)
    tenant = f"t_tot_{uuid.uuid4().hex[:8]}"
    try:
        yield reg, client, tenant
    finally:
        async for k in client.scan_iter(f"{tenant}:*"):
            await client.delete(k)
        await client.aclose()


async def _login(reg, client, tenant, instance, pools, capacity=CAPACITY):
    await reg.set_instance(AgentInstance(
        instance_id=instance, agent_type_id="human", tenant_id=tenant,
        pools=list(pools), execution_model="stateful",
        max_concurrent=capacity, current_sessions=0, state="ready",
        source="human_login",
    ))
    for pool in pools:
        await client.sadd(_pool_instances_key(tenant, pool), instance)
    for pool in pools:
        await reg.write_pool_snapshot(
            tenant_id=tenant, pool_id=pool,
            sla_target_ms=480_000, channel_types=["webchat"],
        )


class _MinuteRolled(Exception):
    """Cenário atravessou a virada do minuto — INCONCLUSIVO, não falha."""


async def _within_one_minute(body, client, tenant, attempts: int = 4):
    for _ in range(attempts):
        async for k in client.scan_iter(f"{tenant}:*"):
            await client.delete(k)
        start = datetime.now(timezone.utc)
        try:
            return await body(start.replace(second=0, microsecond=0))
        except _MinuteRolled:
            continue
    pytest.fail(
        "o cenário atravessou a virada do minuto em todas as tentativas — nenhuma "
        "medição válida (isto NÃO é um verde)"
    )


def _assert_same_minute(started: str) -> None:
    if minute_bucket() != started:
        raise _MinuteRolled()


async def _published_total(client, tenant, when) -> int | None:
    raw = await client.get(_pool_peak_key(tenant, "__total__", minute_bucket(when)))
    return int(raw) if raw is not None else None


# ── 1. O contrato: max de somas, não soma de max ──────────────────────────────

@pytest.mark.asyncio
async def test_tenant_peak_is_the_real_maximum_not_the_sum_of_pool_peaks(env):
    """Dois pools atingem pico 1 em instantes DIFERENTES; o total real é 1, não 2.

    Reproduz o padrão medido em 16:39 de 2026-08-02. Somar os watermarks por pool daria
    2; o máximo verdadeiro do tenant é 1, porque as duas ocupações nunca coexistiram.
    """
    reg, client, tenant = env

    async def body(when):
        started = minute_bucket(when)
        inst_a, inst_b = "human-a", "human-b"
        await _login(reg, client, tenant, inst_a, [POOL_A], capacity=1)
        await _login(reg, client, tenant, inst_b, [POOL_B], capacity=1)

        # Pico de A, e some ANTES de B aparecer.
        assert await reg.claim_instance(tenant, inst_a, "s1", None, 1, pool_id=POOL_A) == 1
        await reg.mark_busy(tenant, POOL_A, inst_a, "s1")
        await reg.release_instance(tenant, inst_a, "s1")
        # Agora o pico de B.
        assert await reg.claim_instance(tenant, inst_b, "s2", None, 1, pool_id=POOL_B) == 1
        await reg.mark_busy(tenant, POOL_B, inst_b, "s2")
        _assert_same_minute(started)

        por_pool = 0
        for pool in (POOL_A, POOL_B):
            raw = await client.get(_pool_peak_key(tenant, pool, started))
            por_pool += int(raw or 0)
        assert por_pool == 2, f"setup: cada pool devia ter pico 1 (soma={por_pool})"

        total = await _published_total(client, tenant, when)
        assert total == 1, (
            f"`__total__` publicou {total}: a soma dos picos por pool é {por_pool}, mas "
            "as duas ocupações nunca coexistiram — `max` de SOMAS ≠ soma de `max`"
        )

    await _within_one_minute(body, client, tenant)


@pytest.mark.asyncio
async def test_tenant_peak_catches_simultaneity_that_pools_cannot_show(env):
    """Simétrico: duas ocupações que COEXISTEM dão total 2, e nenhum pool sozinho o vê.

    Sem o watermark de tenant esse 2 só apareceria se uma amostra caísse exatamente na
    janela em que ambas estavam vivas.
    """
    reg, client, tenant = env

    async def body(when):
        started = minute_bucket(when)
        inst_a, inst_b = "human-a", "human-b"
        await _login(reg, client, tenant, inst_a, [POOL_A], capacity=1)
        await _login(reg, client, tenant, inst_b, [POOL_B], capacity=1)

        assert await reg.claim_instance(tenant, inst_a, "s1", None, 1, pool_id=POOL_A) == 1
        await reg.mark_busy(tenant, POOL_A, inst_a, "s1")
        assert await reg.claim_instance(tenant, inst_b, "s2", None, 1, pool_id=POOL_B) == 1
        await reg.mark_busy(tenant, POOL_B, inst_b, "s2")
        # E somem as duas, dentro do mesmo minuto.
        await reg.release_instance(tenant, inst_a, "s1")
        await reg.release_instance(tenant, inst_b, "s2")
        _assert_same_minute(started)

        assert await reg.get_tenant_occupancy(tenant) == 0, "setup: tudo devia estar livre"
        total = await _published_total(client, tenant, when)
        assert total == 2, (
            f"`__total__` publicou {total}: as duas ocupações coexistiram e sumiram "
            "dentro do minuto — é o pico que a amostragem perde"
        )

    await _within_one_minute(body, client, tenant)


@pytest.mark.asyncio
async def test_allocation_alone_records_the_tenant_total(env):
    """Duas instâncias ocupadas, NENHUMA liberação — só o bump da alocação escreve.

    Este teste existe por causa de um achado da mutação M13
    (`infra/test/mutation_occupancy_peak.sh`): derivar o `__total__` da soma dos pools
    do fan-out corrente — o defeito que o P2 existe para evitar — **não derrubava**
    nenhum dos dois testes acima. Em ambos há uma liberação, e o seed de liberação grava
    o total pré-release, cobrindo o valor certo por outro caminho. Mesmo fenômeno da M2
    no P1: o contrato é satisfeito por uma peça enquanto a outra está quebrada.

    Sem liberação, a única escrita é a da ALOCAÇÃO, e ela tem de vir do contador do
    tenant. `mark_busy` só enxerga os pools da instância que acabou de ocupar — a soma
    do fan-out daria 1, e a verdade do tenant é 2.
    """
    reg, client, tenant = env

    async def body(when):
        started = minute_bucket(when)
        await _login(reg, client, tenant, "human-a", [POOL_A], capacity=1)
        await _login(reg, client, tenant, "human-b", [POOL_B], capacity=1)

        assert await reg.claim_instance(tenant, "human-a", "s1", None, 1, pool_id=POOL_A) == 1
        await reg.mark_busy(tenant, POOL_A, "human-a", "s1")
        assert await reg.claim_instance(tenant, "human-b", "s2", None, 1, pool_id=POOL_B) == 1
        await reg.mark_busy(tenant, POOL_B, "human-b", "s2")
        _assert_same_minute(started)

        assert await reg.get_tenant_occupancy(tenant) == 2, "setup: as duas vagas ocupadas"
        total = await _published_total(client, tenant, when)
        assert total == 2, (
            f"`__total__` publicou {total} sem nenhuma liberação no minuto: o bump da "
            "alocação derivou o total do fan-out (que só vê os pools da instância "
            "corrente) em vez de ler o contador do tenant"
        )

    await _within_one_minute(body, client, tenant)


# ── 2. O contador só é aceitável porque é conferível ──────────────────────────

@pytest.mark.asyncio
async def test_counter_tracks_the_zset_which_is_the_source(env):
    """Contador e ZSET andam juntos, e a instância livre SAI do ZSET.

    O `ZREM` em zero é o que mantém a cardinalidade em O(instâncias OCUPADAS) — é ele
    que torna a reconciliação barata o bastante para rodar sempre.
    """
    reg, client, tenant = env
    inst = "human-1"
    await _login(reg, client, tenant, inst, [POOL_A], capacity=CAPACITY)

    for i in (1, 2, 3):
        assert await reg.claim_instance(tenant, inst, f"s{i}", None, CAPACITY, pool_id=POOL_A) == i
        assert await reg.get_tenant_occupancy(tenant) == i
    assert await client.zscore(_tenant_occupancy_zset_key(tenant), inst) == 3

    for i, restante in ((1, 2), (2, 1), (3, 0)):
        await reg.release_instance(tenant, inst, f"s{i}")
        assert await reg.get_tenant_occupancy(tenant) == restante

    assert await client.zscore(_tenant_occupancy_zset_key(tenant), inst) is None, (
        "instância com ocupação 0 continua no ZSET — a cardinalidade passa a crescer "
        "com o total de agentes, não com os ocupados, e a reconciliação encarece"
    )


@pytest.mark.asyncio
async def test_reconciliation_corrects_the_counter_and_says_so(caplog):
    """Drift injetado no contador é corrigido PARA A FONTE, e denunciado.

    Este teste é a razão de o contador ser aceitável. O `active_count` que este arco
    removeu não tinha fonte contra a qual conferir nem ninguém conferindo — divergia em
    silêncio e a tela mentia. Se este comportamento sumir, o contador volta a ser aquele.
    """
    import logging
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        pytest.skip(f"Redis indisponível em {REDIS_URL}")
    reg    = InstanceRegistry(client)
    tenant = f"t_rec_{uuid.uuid4().hex[:8]}"
    inst   = "human-1"
    try:
        await _login(reg, client, tenant, inst, [POOL_A], capacity=CAPACITY)
        await reg.claim_instance(tenant, inst, "s1", None, CAPACITY, pool_id=POOL_A)
        assert await reg.get_tenant_occupancy(tenant) == 1

        # Drift: alguém escreveu o contador por fora (é o modo de falha real — um
        # caminho de vaga que não passa pelos três ganchos).
        await client.set(_tenant_occupancy_total_key(tenant), "7")

        with caplog.at_level(logging.WARNING, logger="plughub.routing"):
            soma, contador = await reg.reconcile_tenant_occupancy(tenant)

        assert (soma, contador) == (1, 7)
        assert await reg.get_tenant_occupancy(tenant) == 1, (
            "a reconciliação não corrigiu o contador para a fonte"
        )
        assert any("DRIFT de ocupação" in r.getMessage() for r in caplog.records), (
            "o drift foi corrigido em SILÊNCIO — conserto que apaga a evidência de que "
            "havia o que consertar é exatamente o defeito que este arco combate"
        )
    finally:
        async for k in client.scan_iter(f"{tenant}:*"):
            await client.delete(k)
        await client.aclose()


@pytest.mark.asyncio
async def test_wrapup_hold_does_not_move_the_tenant_total(env):
    """O swap para hold é net 0 — a ocupação do tenant não pode oscilar.

    Se o total caísse aqui, o pico do minuto seguinte poderia ser semeado baixo e a
    janela do wrap-up apareceria como capacidade livre que não existe.
    """
    reg, client, tenant = env
    inst = "human-1"
    await _login(reg, client, tenant, inst, [POOL_A], capacity=CAPACITY)

    assert await reg.claim_instance(tenant, inst, "s1", None, CAPACITY, pool_id=POOL_A) == 1
    antes = await reg.get_tenant_occupancy(tenant)
    assert await reg.swap_to_hold(tenant, inst, "s1", hold_ttl_s=90) == 1
    assert await reg.get_tenant_occupancy(tenant) == antes == 1, (
        "o hold mexeu no total do tenant — o swap é troca, não liberação"
    )


@pytest.mark.asyncio
async def test_counter_is_never_clamped(env):
    """Total negativo é DENUNCIADO, não clampado.

    A fatia 2 removeu o teto e o chão do `available` porque eles existiam para esconder
    um modelo errado. Clampar aqui repetiria isso: um total que não pode ser negativo
    ficando negativo é a única evidência de que há um caminho de vaga fora dos ganchos.
    """
    reg, client, tenant = env
    inst = "human-1"
    await _login(reg, client, tenant, inst, [POOL_A], capacity=CAPACITY)
    await reg.claim_instance(tenant, inst, "s1", None, CAPACITY, pool_id=POOL_A)

    # Contador adulterado para baixo; a liberação leva o delta a −1.
    await client.set(_tenant_occupancy_total_key(tenant), "0")
    await reg.release_instance(tenant, inst, "s1")

    assert await reg.get_tenant_occupancy(tenant) == -1, (
        "o total foi clampado em 0 — a evidência do drift some, e o próximo leitor vê "
        "um número plausível"
    )
    # E a reconciliação o traz de volta à fonte.
    soma, _ = await reg.reconcile_tenant_occupancy(tenant)
    assert soma == 0 and await reg.get_tenant_occupancy(tenant) == 0
