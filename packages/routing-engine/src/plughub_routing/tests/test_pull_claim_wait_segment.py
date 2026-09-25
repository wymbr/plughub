"""
test_pull_claim_wait_segment.py — WAI-01: o claim do PULL encerra a espera.

O pull é a única saída de fila que não re-roteia — nem `route()` nem o drain rodam —,
e por isso era a única sem produtor de espera. Medido em 2026-09-25: 100% das esperas
≥ 5 s sem linha (102) estavam em pool pull, e o carimbo que o claim deixava para trás
virava ABANDONO no fechamento da sessão (5 casos com humano atendendo dentro da janela).

Os testes vêm em PAR — registrou o fato / não registrou o não-fato:
  · `test_claim_emits_handoff_wait`             → some a chamada no claim
  · `test_claim_consumes_stamp_no_false_abandon`→ carimbo sobrevive ao claim (o 2º defeito)
  · `test_no_capacity_rollback_emits_nothing`   → emissão ANTES da vaga (rollback não é saída)
  · `test_lost_claim_emits_nothing`             → o perdedor da corrida emite de novo
  · `test_release_then_reclaim_is_same_row`     → devolução vira OUTRA passagem

Bateria de mutação (2026-09-25, 4/4 mortas): sem a chamada · antes da vaga · antes do
ZREM · `abandoned` no lugar de `handoff`. ⚠️ "Antes do ZREM" é morta pelo teste do
ROLLBACK, não pelo da corrida: o perdedor já não acha carimbo (o vencedor o consumiu),
então a corrida sozinha não distingue a posição da chamada.

Integração: precisa de Redis real. O skip é explícito e lê as duas variáveis.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
import redis.asyncio as aioredis

from plughub_routing.registry import InstanceRegistry
from plughub_routing.mute_queue import first_queued_key, resolve_queue_exit
from plughub_routing.router import Router
from plughub_routing.models import AgentInstance, PoolConfig


REDIS_URL = (
    os.environ.get("REDIS_URL")
    or os.environ.get("PLUGHUB_REDIS_URL")
    or "redis://localhost:6379"
)


class _KeyedProducer:
    """Aceita `key=` como o AIOKafkaProducer — o fake sem `key` faz o publish
    lançar TypeError, que `resolve_queue_exit` engole como aviso: o teste passaria
    sem emissão nenhuma."""
    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes | None, dict]] = []

    async def send(self, topic: str, value: dict, key: bytes | None = None) -> None:
        self.sent.append((topic, key, value))

    def waits(self) -> list[dict]:
        return [v for t, _k, v in self.sent
                if t == "conversations.participants" and v.get("role") == "queue"]


class _PullPoolRegistry:
    def __init__(self, pool: PoolConfig) -> None:
        self._pool = pool

    async def get_pool(self, tenant_id, pool_id):
        return self._pool if pool_id == self._pool.pool_id else None

    async def get_candidate_pools(self, tenant_id, channel):
        return [self._pool]


@pytest.fixture
async def env():
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        pytest.skip(f"Redis indisponível em {REDIS_URL} — teste de integração pulado")
    reg      = InstanceRegistry(client)
    producer = _KeyedProducer()
    tenant   = f"t_wai01_{uuid.uuid4().hex[:8]}"
    pool     = "aprovacao_credito"
    cfg = PoolConfig(pool_id=pool, tenant_id=tenant, channel_types=["webhook"],
                     sla_target_ms=300_000, dispatch_mode="pull")
    router = Router(reg, pool_registry=_PullPoolRegistry(cfg), kafka_producer=producer)
    try:
        yield reg, router, client, producer, tenant, pool
    finally:
        async for k in client.scan_iter(f"{tenant}:*"):
            await client.delete(k)
        await client.aclose()


async def _agent(reg, tenant, pool, iid, max_concurrent=3):
    await reg.set_instance(AgentInstance(
        instance_id=iid, agent_type_id="human", tenant_id=tenant, pools=[pool],
        execution_model="stateful", max_concurrent=max_concurrent,
        current_sessions=0, state="ready",
    ))


async def _enqueue(reg, tenant, pool, sid, waited_ms: int) -> int:
    queued_at = int(time.time() * 1000) - waited_ms
    await reg.add_queued_contact(tenant, pool, sid, {
        "session_id": sid, "tenant_id": tenant, "channel": "webhook",
        "pool_id": pool, "queued_at_ms": queued_at,
    }, queued_at)
    return queued_at


async def test_claim_emits_handoff_wait(env):
    reg, router, _c, producer, tenant, pool = env
    sid = f"s-{uuid.uuid4().hex[:6]}"
    await _agent(reg, tenant, pool, "human-ana")
    await _enqueue(reg, tenant, pool, sid, waited_ms=21_350)

    r = await router.work_task_claim(tenant, pool, sid, "human-ana")
    assert r["claimed"], r

    waits = producer.waits()
    assert len(waits) == 1, f"claim venceu e a espera não foi registrada: {producer.sent}"
    w = waits[0]
    assert w["outcome"] == "handoff" and w["pool_id"] == pool, w
    assert 21_000 <= w["duration_ms"] < 60_000, w["duration_ms"]
    keys = [k for t, k, v in producer.sent if v is w]
    assert keys == [sid.encode()], "publish sem key=session_id perde a ordem por partição"


async def test_claim_consumes_stamp_no_false_abandon(env):
    """O 2º defeito: o carimbo deixado pelo claim virava `abandoned` no fechamento."""
    reg, router, client, producer, tenant, pool = env
    sid = f"s-{uuid.uuid4().hex[:6]}"
    await _agent(reg, tenant, pool, "human-ana")
    await _enqueue(reg, tenant, pool, sid, waited_ms=5_000)
    assert await client.get(first_queued_key(tenant, sid)), "premissa: carimbo existe"

    await router.work_task_claim(tenant, pool, sid, "human-ana")
    assert not await client.get(first_queued_key(tenant, sid))

    # O que o `Queue cleanup` do SessionClosed faz ao fechar a sessão atendida:
    emitted = await resolve_queue_exit(client, producer, tenant, pool, sid, "abandoned")
    assert emitted is False
    assert [w["outcome"] for w in producer.waits()] == ["handoff"], producer.waits()


async def test_no_capacity_rollback_emits_nothing(env):
    reg, router, client, producer, tenant, pool = env
    await _agent(reg, tenant, pool, "human-ana", max_concurrent=1)
    busy, sid = f"s-{uuid.uuid4().hex[:6]}", f"s-{uuid.uuid4().hex[:6]}"
    await _enqueue(reg, tenant, pool, busy, waited_ms=1_000)
    await _enqueue(reg, tenant, pool, sid, waited_ms=1_000)
    assert (await router.work_task_claim(tenant, pool, busy, "human-ana"))["claimed"]
    producer.sent.clear()

    r = await router.work_task_claim(tenant, pool, sid, "human-ana")
    assert r == {"claimed": False, "reason": "no_capacity"}, r
    assert producer.waits() == [], "rollback registrou saída de uma fila que o item não deixou"
    assert await client.get(first_queued_key(tenant, sid)), "rollback consumiu o carimbo"


async def test_lost_claim_emits_nothing(env):
    reg, router, _c, producer, tenant, pool = env
    sid = f"s-{uuid.uuid4().hex[:6]}"
    await _agent(reg, tenant, pool, "human-ana")
    await _agent(reg, tenant, pool, "human-bia")
    await _enqueue(reg, tenant, pool, sid, waited_ms=1_000)
    assert (await router.work_task_claim(tenant, pool, sid, "human-ana"))["claimed"]

    r = await router.work_task_claim(tenant, pool, sid, "human-bia")
    assert r["claimed"] is False
    assert len(producer.waits()) == 1, producer.waits()


async def test_release_then_reclaim_is_same_row(env):
    """Devolução preserva a espera (regra de produto): o 2º claim deriva o MESMO
    `segment_id`, e a linha é substituída — nunca uma segunda passagem."""
    reg, router, _c, producer, tenant, pool = env
    sid = f"s-{uuid.uuid4().hex[:6]}"
    await _agent(reg, tenant, pool, "human-ana")
    await _agent(reg, tenant, pool, "human-bia")
    await _enqueue(reg, tenant, pool, sid, waited_ms=3_000)

    await router.work_task_claim(tenant, pool, sid, "human-ana")
    await router.work_task_release(tenant, pool, sid, "human-ana")
    await router.work_task_claim(tenant, pool, sid, "human-bia")

    waits = producer.waits()
    assert len(waits) == 2, waits
    assert waits[0]["segment_id"] == waits[1]["segment_id"], waits
    assert waits[1]["duration_ms"] >= waits[0]["duration_ms"]
