"""
test_pool_occupancy_peak.py — P1: pico de ocupação VERDADEIRO (event-driven).

**O método antigo estava errado por construção, não por calibragem.** O pico por pool
vinha de `_occupancy_sampler`, que lia a ocupação a cada 5 s e guardava o máximo das
amostras. Pico é o máximo de uma FUNÇÃO ESCADA, e qualquer intervalo de amostra pode
cair inteiro entre duas subidas — um contato que chega e sai dentro da janela nunca é
visto, e diminuir a janela só estreita a classe de falha. A alocação/liberação É o
instante em que o valor muda; é lá que ele passa a ser gravado.

**Regra de gravação (fechada no TODO § Pico de ocupação VERDADEIRO):**
  · `max` sobe SÓ na ALOCAÇÃO — liberação nunca cria máximo novo;
  · carga carregada (bucket que começa alto e só desce, ou sem transição alguma) entra
    por SEED na virada;
  · o buraco de relógio do seed por virada (o flusher acorda em 00:00.4, a ocupação
    caiu em 00:00.1) é coberto por um seed disparado por EVENTO na liberação.

**Que resultado faria estes testes ficarem vermelhos.** O primeiro é o CONTRATO
OBSERVÁVEL e é a razão de existir do arco: um pico que sobe e desce inteiramente entre
duas viradas tem de aparecer no valor publicado. Contra a amostragem de 5 s ele dá 0 —
não por bug, mas porque o método não consegue vê-lo. Os demais protegem a regra: um
`max` que descesse na liberação (2), carga carregada saindo como zero (3), o pico
migrando para a costura errada (4), e a capacidade lida no flush em vez de no pico (5,
o achado 1 de 2026-08-02: `peak 1 / provisioned 0`, impossível por construção).

Teste de INTEGRAÇÃO: precisa de um Redis real (o watermark é Lua, roda no servidor).
    REDIS_URL=redis://redis:6379 pytest test_pool_occupancy_peak.py
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
import redis.asyncio as aioredis

from plughub_routing.main import _read_pool_watermarks
from plughub_routing.models import AgentInstance
from plughub_routing.registry import (
    InstanceRegistry,
    _pool_instances_key,
    _pool_peak_key,
    minute_bucket,
)


# O serviço define `PLUGHUB_REDIS_URL`; ler só `REDIS_URL` faz o teste cair no default
# `localhost`, falhar o ping e PULAR — dentro do próprio container onde o Redis está a
# um hostname de distância. Um skip é um teste que não pode reprovar.
REDIS_URL = (
    os.environ.get("REDIS_URL")
    or os.environ.get("PLUGHUB_REDIS_URL")
    or "redis://localhost:6379"
)

POOLS    = ["peak_pool", "peak_pool-int"]
CAPACITY = 3


@pytest.fixture
async def env():
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        pytest.skip(f"Redis indisponível em {REDIS_URL} — teste de integração pulado")
    reg      = InstanceRegistry(client)
    tenant   = f"t_peak_{uuid.uuid4().hex[:8]}"
    instance = f"human-{uuid.uuid4().hex[:6]}"
    try:
        yield reg, client, tenant, instance
    finally:
        async for k in client.scan_iter(f"{tenant}:*"):
            await client.delete(k)
        await client.aclose()


async def _login(reg, client, tenant, instance, capacity=CAPACITY):
    await reg.set_instance(AgentInstance(
        instance_id=instance, agent_type_id="human", tenant_id=tenant,
        pools=list(POOLS), execution_model="stateful",
        max_concurrent=capacity, current_sessions=0, state="ready",
        source="human_login",
    ))
    for pool in POOLS:
        await client.sadd(_pool_instances_key(tenant, pool), instance)
    for pool in POOLS:
        await reg.write_pool_snapshot(
            tenant_id=tenant, pool_id=pool,
            sla_target_ms=480_000, channel_types=["webchat"],
        )


async def _published(client, tenant, pool, when) -> tuple[int, int | None]:
    """O que o flusher publicaria para este minuto — o valor OBSERVÁVEL, não a chave.

    Passa pelo mesmo `_read_pool_watermarks` que o `_flush_occupancy` usa, de propósito:
    um teste que lesse a chave crua concordaria com um flusher quebrado.
    """
    peaks, caps = await _read_pool_watermarks(client, {(tenant, pool)}, when)
    return peaks[(tenant, pool)], caps[(tenant, pool)]


class _MinuteRolled(Exception):
    """O cenário atravessou a virada do minuto — resultado INCONCLUSIVO, não falha."""


async def _within_one_minute(body, client, tenant, attempts: int = 4):
    """Roda `body(when)` garantindo que tudo caiu no MESMO minuto.

    O cenário mede um bucket de minuto; se o relógio virar no meio, o veredito seria
    sobre dois buckets e a falha seria de temporização, não do código. Repetir é a
    correção certa — e se todas as tentativas atravessarem a virada (impossível: cada
    uma leva milissegundos), o teste FALHA em vez de passar por ausência de amostra.

    Cada tentativa começa do zero (as chaves do tenant são apagadas): uma tentativa
    abortada no meio deixaria ocupantes vivos, e a seguinte mediria outro cenário —
    reprovando por sujeira, que é tão ruim quanto passar por sorte.
    """
    for _ in range(attempts):
        async for k in client.scan_iter(f"{tenant}:*"):
            await client.delete(k)
        start = datetime.now(timezone.utc)
        try:
            return await body(start.replace(second=0, microsecond=0))
        except _MinuteRolled:
            continue
    pytest.fail(
        "o cenário atravessou a virada do minuto em todas as tentativas — "
        "nenhuma medição válida foi obtida (isto NÃO é um verde)"
    )


def _assert_same_minute(started_bucket: str) -> None:
    if minute_bucket() != started_bucket:
        raise _MinuteRolled()


# ── 1. O contrato observável ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_peak_that_rises_and_falls_between_flushes_is_published(env):
    """Duas vagas ocupadas e devolvidas dentro do mesmo minuto, sem nenhuma amostra
    no meio → o minuto tem de publicar pico 2.

    É o caso que a amostragem por relógio não consegue ver: entre duas leituras de 5 s
    a ocupação subiu a 2 e voltou a 0, e o máximo das amostras é 0. Aqui não há amostra
    nenhuma — o valor vem das próprias transições.
    """
    reg, client, tenant, instance = env

    async def body(when):
        started = minute_bucket(when)   # MESMA âncora que `_published` vai ler
        await _login(reg, client, tenant, instance)
        for i in (1, 2):
            assert await reg.claim_instance(
                tenant, instance, f"ses-{i}", None, CAPACITY, pool_id=POOLS[0]
            ) == i
            await reg.mark_busy(tenant, POOLS[0], instance, f"ses-{i}")
        for i in (1, 2):
            await reg.release_instance(tenant, instance, f"ses-{i}")
        _assert_same_minute(started)

        assert await reg.instance_session_count(tenant, instance) == 0, (
            "setup: a ocupação não voltou a zero — o cenário não é o que se afirma"
        )
        peak, _cap = await _published(client, tenant, POOLS[0], when)
        assert peak == 2, (
            f"o minuto publicou pico {peak}: a ocupação chegou a 2 e voltou a 0 sem "
            "que nada a registrasse — é exatamente o pico que a amostragem perde"
        )

    await _within_one_minute(body, client, tenant)


@pytest.mark.asyncio
async def test_peak_is_projected_per_pool_not_per_resource(env):
    """A vaga é do RECURSO, mas o pico publicado é a projeção POR POOL.

    O irmão vê o consumo (`busy_elsewhere` no snapshot), e ainda assim seu pico é 0 —
    somar o consumo do irmão aqui reintroduziria, na série, a mesma contagem dupla que
    a fatia 2 tirou da tela.
    """
    reg, client, tenant, instance = env

    async def body(when):
        started = minute_bucket(when)   # MESMA âncora que `_published` vai ler
        await _login(reg, client, tenant, instance)
        assert await reg.claim_instance(
            tenant, instance, "ses-1", None, CAPACITY, pool_id=POOLS[0]
        ) == 1
        await reg.mark_busy(tenant, POOLS[0], instance, "ses-1")
        _assert_same_minute(started)

        assert (await _published(client, tenant, POOLS[0], when))[0] == 1
        assert (await _published(client, tenant, POOLS[1], when))[0] == 0, (
            "o pool irmão registrou pico de um contato que não serviu"
        )

    await _within_one_minute(body, client, tenant)


# ── 2. A regra de gravação ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_release_never_lowers_the_peak(env):
    """`max` é máximo do minuto, não ocupação corrente.

    Um watermark que acompanhasse a queda publicaria a ocupação do FIM do minuto — a
    grandeza que a tela já mostra, e não a que o dimensionamento pergunta.
    """
    reg, client, tenant, instance = env

    async def body(when):
        started = minute_bucket(when)   # MESMA âncora que `_published` vai ler
        await _login(reg, client, tenant, instance)
        for i in (1, 2, 3):
            assert await reg.claim_instance(
                tenant, instance, f"ses-{i}", None, CAPACITY, pool_id=POOLS[0]
            ) == i
            await reg.mark_busy(tenant, POOLS[0], instance, f"ses-{i}")
        await reg.release_instance(tenant, instance, "ses-3")
        _assert_same_minute(started)

        assert await reg.instance_session_count(tenant, instance) == 2
        peak, _cap = await _published(client, tenant, POOLS[0], when)
        assert peak == 3, f"a liberação derrubou o pico do minuto para {peak}"

    await _within_one_minute(body, client, tenant)


@pytest.mark.asyncio
async def test_carried_load_is_seeded_by_the_release_event(env):
    """Carga CARREGADA: bucket que começa com ocupação e só desce.

    O `DEL` do watermark reproduz um bucket recém-virado — não há alocação neste minuto
    para gravá-lo, e o flusher ainda não acordou para semear. Se a liberação também não
    semeasse, o minuto sairia com pico 0 tendo tido ocupação o tempo todo: o valor
    plausível que esconde exatamente a carga sustentada.
    """
    reg, client, tenant, instance = env

    async def body(when):
        started = minute_bucket(when)   # MESMA âncora que `_published` vai ler
        await _login(reg, client, tenant, instance)
        for i in (1, 2):
            assert await reg.claim_instance(
                tenant, instance, f"ses-{i}", None, CAPACITY, pool_id=POOLS[0]
            ) == i
            await reg.mark_busy(tenant, POOLS[0], instance, f"ses-{i}")

        # Bucket "novo": a carga atravessou a virada, nada a registrou ainda.
        await client.delete(_pool_peak_key(tenant, POOLS[0], started))
        assert (await _published(client, tenant, POOLS[0], when))[0] == 0

        await reg.release_instance(tenant, instance, "ses-2")
        _assert_same_minute(started)

        peak, _cap = await _published(client, tenant, POOLS[0], when)
        assert peak == 2, (
            f"carga carregada saiu do minuto como pico {peak}: a liberação não semeou "
            "o bucket com a ocupação de ANTES, e o flusher não estava lá para vê-la"
        )

    await _within_one_minute(body, client, tenant)


@pytest.mark.asyncio
async def test_writing_a_snapshot_does_not_record_a_peak(env):
    """**Guarda de invariante**: o pico sobe na costura de ALOCAÇÃO, nunca em quem
    escreve snapshot.

    Se o bump migrar para `write_pool_snapshot`, a liberação (F3a) passa a bumpar junto
    e o pico volta a ser *amostrado nos instantes em que alguém escreve snapshot* —
    numericamente inofensivo hoje, e semanticamente de volta à amostragem, sem nada
    ficar vermelho. Este teste é o "nada" ficando vermelho.
    """
    reg, client, tenant, instance = env

    async def body(when):
        started = minute_bucket(when)   # MESMA âncora que `_published` vai ler
        await _login(reg, client, tenant, instance)
        assert await reg.claim_instance(
            tenant, instance, "ses-1", None, CAPACITY, pool_id=POOLS[0]
        ) == 1
        await client.delete(_pool_peak_key(tenant, POOLS[0], started))

        # Caminho de LIBERAÇÃO/refresh puro: recomputa e reescreve a linha, sem alocar.
        await reg.refresh_pool_snapshot(tenant, POOLS[0])
        await reg.refresh_snapshots_for_instance(tenant, instance)
        _assert_same_minute(started)

        peak, _cap = await _published(client, tenant, POOLS[0], when)
        assert peak == 0, (
            f"escrever snapshot registrou pico {peak} — o bump saiu da costura de "
            "alocação e o pico voltou a ser amostrado nos instantes de escrita"
        )

    await _within_one_minute(body, client, tenant)


# ── 3. Achado 1 — capacidade no instante do pico ──────────────────────────────

@pytest.mark.asyncio
async def test_capacity_is_captured_at_the_peak_instant(env):
    """`provisioned_capacity` é do instante do PICO, não do instante do flush.

    O flusher chamava `_pool_capacity` na virada do minuto enquanto o pico vinha do
    minuto que passou. A série registrou `peak 1 / provisioned 0` — impossível por
    construção — e `headroom`/`utilization`, que a UI deriva das duas, ficavam com
    denominador de outro momento. Aqui a capacidade muda DEPOIS do pico; a linha do
    minuto tem de continuar com a capacidade de quando o pico aconteceu.
    """
    reg, client, tenant, instance = env

    async def body(when):
        started = minute_bucket(when)   # MESMA âncora que `_published` vai ler
        await _login(reg, client, tenant, instance, capacity=CAPACITY)
        assert await reg.claim_instance(
            tenant, instance, "ses-1", None, CAPACITY, pool_id=POOLS[0]
        ) == 1
        await reg.mark_busy(tenant, POOLS[0], instance, "ses-1")

        # Um segundo recurso entra no pool DEPOIS do pico (login novo).
        other = f"human-{uuid.uuid4().hex[:6]}"
        await reg.set_instance(AgentInstance(
            instance_id=other, agent_type_id="human", tenant_id=tenant,
            pools=[POOLS[0]], execution_model="stateful",
            max_concurrent=CAPACITY, current_sessions=0, state="ready",
            source="human_login",
        ))
        await client.sadd(_pool_instances_key(tenant, POOLS[0]), other)
        _assert_same_minute(started)

        peak, cap = await _published(client, tenant, POOLS[0], when)
        assert peak == 1
        assert cap == CAPACITY, (
            f"capacidade publicada {cap} é a de AGORA ({2 * CAPACITY} após o login "
            f"novo), não a de quando o pico aconteceu ({CAPACITY})"
        )
        assert peak <= (cap or 0), (
            "peak > capacity é impossível por construção — a assinatura exata do "
            "achado 1 na série de 2026-08-02"
        )

    await _within_one_minute(body, client, tenant)
