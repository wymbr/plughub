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
from plughub_routing.models import AgentInstance, ConversationInboundEvent, PoolConfig


class _FakePoolRegistry:
    """PoolRegistry mínimo — devolve um único pool config para o route()."""
    def __init__(self, pool: PoolConfig | None) -> None:
        self._pool = pool

    async def get_pool(self, tenant_id: str, pool_id: str) -> PoolConfig | None:
        return self._pool if (self._pool and self._pool.pool_id == pool_id) else None

    async def get_candidate_pools(self, tenant_id: str, channel: str) -> list[PoolConfig]:
        return [self._pool] if self._pool else []


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
        pools=[pool], execution_model="stateful",
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


@pytest.mark.asyncio
async def test_expire_never_claimed_removes_from_queue(env):
    """
    I5 — o caso que hoje não tem quem limpe: item NUNCA reivindicado. O expire tira
    o membro do ZSET e o JSON; sem isso o item fica listado para sempre e o claim o
    recusa com not_in_queue quando o JSON expira por TTL.
    """
    reg, router, client, _p, tenant, pool = env
    sid = "ses-exp-nunca"
    await _queue_contact(reg, tenant, pool, sid)

    res = await router.work_task_expire(tenant, pool, sid, reason="acw_expired")
    assert res["expired"] is True
    assert res["was_queued"] is True
    assert res["was_claimed"] is False
    assert await client.zscore(_queue_key(tenant, pool), sid) is None
    assert await reg.get_full_queued_contact(tenant, sid) is None


@pytest.mark.asyncio
async def test_expire_claimed_frees_slot_without_requeue(env):
    """
    I5 — item reivindicado e não submetido: devolve a VAGA (a lease é a evidência do
    claim de pull) e NÃO re-enfileira (diferença essencial para o work_task_release).
    """
    reg, router, client, _p, tenant, pool = env
    sid, inst = "ses-exp-claim", "human-exp-1"
    await _register_instance(reg, tenant, pool, inst, max_concurrent=3)
    await _queue_contact(reg, tenant, pool, sid)
    await router.work_task_claim(tenant, pool, sid, inst)
    assert await reg.instance_session_count(tenant, inst) == 1

    res = await router.work_task_expire(tenant, pool, sid, reason="acw_expired")
    assert res["was_claimed"] is True and res["instance_id"] == inst
    assert res["was_queued"] is False          # o claim já havia feito o ZREM
    assert await reg.instance_session_count(tenant, inst) == 0
    assert await client.get(_claim_lease_key(tenant, pool, sid)) is None
    assert await client.zscore(_queue_key(tenant, pool), sid) is None   # NÃO re-enfileira


@pytest.mark.asyncio
async def test_expire_is_idempotent(env):
    """
    I5 — o expire roda em TODO resume; a 2ª passagem tem de ser inócua. Um segundo
    expire NÃO pode derrubar a vaga que o agente ocupa com OUTRA sessão.
    """
    reg, router, _c, _p, tenant, pool = env
    sid, inst = "ses-exp-idem", "human-exp-2"
    await _register_instance(reg, tenant, pool, inst, max_concurrent=3)
    await _queue_contact(reg, tenant, pool, sid)
    await router.work_task_claim(tenant, pool, sid, inst)
    # o mesmo agente atende outra sessão em paralelo — ela não pode ser afetada
    await reg.claim_instance(tenant, inst, "outra-sessao", None, max_concurrent=3)

    first  = await router.work_task_expire(tenant, pool, sid)
    second = await router.work_task_expire(tenant, pool, sid)
    assert first["was_claimed"] is True
    assert second["was_claimed"] is False and second["was_queued"] is False
    assert second["expired"] is True
    # sobra exatamente a outra sessão
    assert await reg.instance_session_count(tenant, inst) == 1


@pytest.mark.asyncio
async def test_route_pull_parks_and_clears_lease(env):
    """F1.1+F1.3: route() de um pool pull PARQUEIA (não aloca) e LIMPA a claim
    lease anterior da sessão (re-parque após desconexão)."""
    reg, _router, client, producer, tenant, pool = env
    sid = "ses-reparque"
    pool_cfg = PoolConfig(
        pool_id=pool, tenant_id=tenant, channel_types=["webchat"],
        sla_target_ms=30000, dispatch_mode="pull",
    )
    router = Router(reg, _FakePoolRegistry(pool_cfg), kafka_producer=producer)

    # lease órfã pré-existente (de um claim anterior)
    await reg.write_claim_lease(tenant, pool, sid, "human-old", 180)
    assert await client.get(_claim_lease_key(tenant, pool, sid)) is not None

    event = ConversationInboundEvent(
        session_id=sid, tenant_id=tenant, customer_id="",
        channel="webchat", pool_id=pool, started_at="2026-01-01T00:00:00Z",
    )
    result = await router.route(event)
    assert result.queued is True and result.allocated is False   # parqueou, não alocou

    await asyncio.sleep(0.05)   # deixa o create_task(delete_claim_lease) rodar
    assert await client.get(_claim_lease_key(tenant, pool, sid)) is None  # lease limpa
