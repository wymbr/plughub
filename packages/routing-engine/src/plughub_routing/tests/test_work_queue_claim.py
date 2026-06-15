"""
test_work_queue_claim.py — Frente 1 F1.2 (dispatch pull: claim atômico).

Valida work_task_claim / work_task_release do Router:
  - ZREM atômico = um único vencedor (claim concorrente da mesma task);
  - composição com o semáforo do recurso (claim_instance) + ROLLBACK ao perder
    capacidade (contato volta para a fila, sem órfão);
  - publish de conversations.routed em sucesso (reusa o downstream bridge/Console);
  - release re-enfileira e libera a vaga.

Teste de INTEGRAÇÃO: precisa de um Redis real. Pulado se indisponível.
    REDIS_URL=redis://localhost:6379 pytest test_work_queue_claim.py
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import redis.asyncio as aioredis

from plughub_routing.registry import (
    InstanceRegistry, _queue_key, _claim_lease_key, _instance_sessions_key,
)
from plughub_routing.router import Router
from plughub_routing.models import AgentInstance


REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")


class _FakeProducer:
    """Captura os eventos publicados (conversations.routed)."""
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def send(self, topic: str, value: dict) -> None:
        self.sent.append((topic, value))


@pytest.fixture
async def env():
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        pytest.skip(f"Redis indisponível em {REDIS_URL} — teste de integração pulado")
    reg      = InstanceRegistry(client)
    producer = _FakeProducer()
    router   = Router(reg, pool_registry=None, kafka_producer=producer)  # pool_registry não usado pelo claim
    tenant   = f"t_pull_{uuid.uuid4().hex[:8]}"
    pool     = "teste_demo"
    try:
        yield reg, router, client, producer, tenant, pool
    finally:
        # tenant aleatório isola; limpa as chaves principais por garantia
        async for k in client.scan_iter(f"{tenant}:*"):
            await client.delete(k)
        await client.aclose()


async def _register_instance(reg, tenant, pool, instance, max_concurrent=3):
    await reg.set_instance(AgentInstance(
        instance_id=instance, agent_type_id="human", tenant_id=tenant,
        pools=[pool], pool_id=pool, execution_model="stateful",
        max_concurrent=max_concurrent, current_sessions=0, state="ready",
    ))


async def _queue_contact(reg, tenant, pool, sid):
    await reg.add_queued_contact(
        tenant, pool, sid,
        {"session_id": sid, "tenant_id": tenant, "channel": "webchat"},
        queued_at_ms=1,
    )


@pytest.mark.asyncio
async def test_atomic_dequeue_single_winner(env):
    """N dequeues concorrentes da MESMA task → exatamente 1 vencedor (ZREM)."""
    reg, _router, _c, _p, tenant, pool = env
    sid = "ses-1winner"
    await _queue_contact(reg, tenant, pool, sid)

    results = await asyncio.gather(*[
        reg.atomic_claim_dequeue(tenant, pool, sid) for _ in range(20)
    ])
    assert sum(1 for r in results if r) == 1, f"esperava 1 vencedor: {results}"


@pytest.mark.asyncio
async def test_claim_happy_path(env):
    """Claim com sucesso: sai da fila, grava lease, publica conversations.routed."""
    reg, router, client, producer, tenant, pool = env
    sid, inst = "ses-ok", "human-ok-1"
    await _register_instance(reg, tenant, pool, inst, max_concurrent=3)
    await _queue_contact(reg, tenant, pool, sid)

    res = await router.work_task_claim(tenant, pool, sid, inst)
    assert res["claimed"] is True
    assert res["instance_id"] == inst
    # saiu do sorted set
    assert await client.zscore(_queue_key(tenant, pool), sid) is None
    # lease gravada
    assert await client.get(_claim_lease_key(tenant, pool, sid)) is not None
    # publicou conversations.routed com o resultado alocado
    assert len(producer.sent) == 1
    _topic, ev = producer.sent[0]
    assert ev["result"]["allocated"] is True
    assert ev["result"]["instance_id"] == inst
    assert ev["result"]["pool_id"] == pool


@pytest.mark.asyncio
async def test_claim_already_claimed(env):
    """Segundo claim da mesma task → already_claimed (ZREM perde)."""
    reg, router, _c, _p, tenant, pool = env
    sid, inst = "ses-dup", "human-dup-1"
    await _register_instance(reg, tenant, pool, inst)
    await _queue_contact(reg, tenant, pool, sid)

    first  = await router.work_task_claim(tenant, pool, sid, inst)
    second = await router.work_task_claim(tenant, pool, sid, inst)
    assert first["claimed"] is True
    assert second["claimed"] is False and second["reason"] == "already_claimed"


@pytest.mark.asyncio
async def test_claim_no_capacity_rollback(env):
    """Agente lotado (claim_instance=-1) → no_capacity E o contato volta pra fila."""
    reg, router, client, _p, tenant, pool = env
    sid, inst = "ses-full", "human-full-1"
    await _register_instance(reg, tenant, pool, inst, max_concurrent=1)
    # pré-ocupa a única vaga com outra sessão
    occ = await reg.claim_instance(tenant, inst, "other-session", None, max_concurrent=1)
    assert occ == 1
    await _queue_contact(reg, tenant, pool, sid)

    res = await router.work_task_claim(tenant, pool, sid, inst)
    assert res["claimed"] is False and res["reason"] == "no_capacity"
    # rollback: contato de volta no sorted set
    assert await client.zscore(_queue_key(tenant, pool), sid) is not None
    # sem lease
    assert await client.get(_claim_lease_key(tenant, pool, sid)) is None


@pytest.mark.asyncio
async def test_release_requeues_and_frees_slot(env):
    """Release: remove a lease, libera a vaga e re-enfileira o contato."""
    reg, router, client, _p, tenant, pool = env
    sid, inst = "ses-rel", "human-rel-1"
    await _register_instance(reg, tenant, pool, inst, max_concurrent=3)
    await _queue_contact(reg, tenant, pool, sid)

    await router.work_task_claim(tenant, pool, sid, inst)
    assert await reg.instance_session_count(tenant, inst) == 1

    rel = await router.work_task_release(tenant, pool, sid, inst)
    assert rel["released"] is True and rel["requeued"] is True
    # vaga liberada
    assert await reg.instance_session_count(tenant, inst) == 0
    # lease removida
    assert await client.get(_claim_lease_key(tenant, pool, sid)) is None
    # contato claimável de novo
    assert await client.zscore(_queue_key(tenant, pool), sid) is not None
