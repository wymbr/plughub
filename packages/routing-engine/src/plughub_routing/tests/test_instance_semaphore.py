"""
test_instance_semaphore.py — arco "Router: alocação atômica" (Fatia A/B).

Valida o par atômico claim_instance/release_instance (semáforo de contagem
por-instância via Lua sobre um SET de occupants). É a primitiva que elimina a
corrida de sobre-alocação do select→mark_busy não-atômico.

Modelo do occupant: "{session_id}::{conference_id}". Duas conferências da MESMA
sessão (conference_ids distintos) NÃO compartilham vaga. Release é por PREFIXO de
sessão ("{session_id}::"), pois o agent_done só carrega session_id.

Teste de INTEGRAÇÃO: precisa de um Redis real (Lua roda no servidor). É pulado
automaticamente se não houver Redis acessível. Aponte REDIS_URL para o Redis do
demo, ex.:
    REDIS_URL=redis://localhost:6379 pytest test_instance_semaphore.py
ou rode dentro da rede do compose (REDIS_URL=redis://redis:6379).
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import redis.asyncio as aioredis

from plughub_routing.registry import InstanceRegistry, _instance_sessions_key


REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")


@pytest.fixture
async def reg_and_redis():
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        pytest.skip(f"Redis indisponível em {REDIS_URL} — teste de integração pulado")
    reg = InstanceRegistry(client)
    tenant = f"t_sem_{uuid.uuid4().hex[:8]}"
    instance = "wrapup_test-001"
    key = _instance_sessions_key(tenant, instance)
    await client.delete(key)
    try:
        yield reg, client, tenant, instance
    finally:
        await client.delete(key)
        await client.aclose()


@pytest.mark.asyncio
async def test_concurrent_confs_same_session_single_capacity_only_one_wins(reg_and_redis):
    """O nó do bug: N conferências da MESMA sessão (conference_ids distintos)
    reivindicando concorrentemente uma instância max_concurrent=1 → exatamente 1
    ganha; as outras recebem -1 (e re-selecionariam outra instância)."""
    reg, client, tenant, instance = reg_and_redis

    sid = "ses-fanout"
    N = 25
    results = await asyncio.gather(*[
        reg.claim_instance(tenant, instance, sid, f"conf-{i}", max_concurrent=1)
        for i in range(N)
    ])

    winners = [r for r in results if r >= 1]
    losers  = [r for r in results if r == -1]
    assert len(winners) == 1, f"esperava 1 vencedor, veio {len(winners)}: {results}"
    assert len(losers) == N - 1
    assert await reg.instance_session_count(tenant, instance) == 1


@pytest.mark.asyncio
async def test_claim_is_idempotent(reg_and_redis):
    """Mesmo (session, conference) reivindicando 2× não duplica a vaga (redelivery)."""
    reg, client, tenant, instance = reg_and_redis

    r1 = await reg.claim_instance(tenant, instance, "ses-A", "conf-A", max_concurrent=1)
    r2 = await reg.claim_instance(tenant, instance, "ses-A", "conf-A", max_concurrent=1)
    assert r1 == 1 and r2 == 1
    assert await reg.instance_session_count(tenant, instance) == 1


@pytest.mark.asyncio
async def test_release_by_session_prefix_frees_slot_and_is_idempotent(reg_and_redis):
    """Release por session_id remove a vaga (mesmo sem conhecer o conference_id) e é
    idempotente."""
    reg, client, tenant, instance = reg_and_redis

    assert await reg.claim_instance(tenant, instance, "ses-A", "conf-A", max_concurrent=1) == 1
    # release só com o session_id (o agent_done não tem conference_id)
    assert await reg.release_instance(tenant, instance, "ses-A") == 0
    assert await reg.release_instance(tenant, instance, "ses-A") == 0  # idempotente
    assert await reg.instance_session_count(tenant, instance) == 0
    # vaga liberada → outra sessão consegue reivindicar
    assert await reg.claim_instance(tenant, instance, "ses-B", "conf-B", max_concurrent=1) == 1


@pytest.mark.asyncio
async def test_release_targets_only_its_session(reg_and_redis):
    """Numa instância max=2 com duas sessões distintas, release(sesA) remove só a
    vaga da sesA (prefixo), deixando a sesB intacta."""
    reg, client, tenant, instance = reg_and_redis

    assert await reg.claim_instance(tenant, instance, "ses-A", "conf-A", max_concurrent=2) == 1
    assert await reg.claim_instance(tenant, instance, "ses-B", "conf-B", max_concurrent=2) == 2
    assert await reg.release_instance(tenant, instance, "ses-A") == 1  # sobra a sesB
    assert await reg.instance_session_count(tenant, instance) == 1
    # sesA livre, sesB ocupando 1/2 → nova sesC entra; uma 2ª tentativa estoura
    assert await reg.claim_instance(tenant, instance, "ses-C", "conf-C", max_concurrent=2) == 2
    assert await reg.claim_instance(tenant, instance, "ses-D", "conf-D", max_concurrent=2) == -1


@pytest.mark.asyncio
async def test_multi_capacity_respects_ceiling(reg_and_redis):
    """max_concurrent=3 → até 3 occupants distintos entram; o resto recebe -1."""
    reg, client, tenant, instance = reg_and_redis

    rs = await asyncio.gather(*[
        reg.claim_instance(tenant, instance, f"ses-{i}", f"conf-{i}", max_concurrent=3)
        for i in range(8)
    ])
    assert sum(1 for r in rs if r >= 1) == 3
    assert sum(1 for r in rs if r == -1) == 5
    assert await reg.instance_session_count(tenant, instance) == 3


@pytest.mark.asyncio
async def test_claim_release_interleave_never_exceeds_capacity(reg_and_redis):
    """claim×release concorrentes nunca ultrapassam a capacidade (sem lost update)."""
    reg, client, tenant, instance = reg_and_redis

    assert await reg.claim_instance(tenant, instance, "ses-A", "conf-A", max_concurrent=1) == 1

    async def _claim(i):
        return await reg.claim_instance(tenant, instance, f"ses-{i}", f"conf-{i}", max_concurrent=1)

    release_task = reg.release_instance(tenant, instance, "ses-A")
    results = await asyncio.gather(release_task, *[_claim(i) for i in range(10)])

    final = await reg.instance_session_count(tenant, instance)
    assert final <= 1, f"ocupação final {final} excedeu a capacidade"
