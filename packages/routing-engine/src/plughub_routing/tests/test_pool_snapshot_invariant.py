"""
test_pool_snapshot_invariant.py — invariante `available ≤ total_instances`.

O Monitor exibia `available 4 / total 3`. As duas primeiras hipóteses (ready-set
órfão e `active_count` negativo) foram refutadas por leitura de dado; a causa real
estava numa ASSIMETRIA dentro de `InstanceRegistry.remove_conversation`:

    new_val = DECR(active_count)
    if new_val < 0: set(0)                     ← CHÃO existia
    ...
    snap["available"] = snap["available"] + 1  ← TETO não existia

Quando o DECR batia no chão — remoção sem `mark_busy` correspondente (`agent_done`
duplicado/tardio, ou sessão contabilizada noutro pool) — `busy` ficava em 0 e
`available` ainda ganhava +1. Repetido, ultrapassava a capacidade.

**A fatia 2 tirou o remendo em vez de reforçá-lo.** O contador e o patch `+1`
sumiram: `available`/`busy` são RECALCULADOS do semáforo do recurso a cada escrita
(`compute_pool_occupancy`), então nem o chão nem o teto têm mais o que segurar —
um valor derivado de `total_capacity − used_global` não tem como passar da
capacidade nem ficar negativo. Estes testes seguem aqui porque fixam o
INVARIANTE, não a implementação: qualquer caminho que volte a somar sobre o número
anterior reprova.

Teste de INTEGRAÇÃO: precisa de um Redis real. Pulado se indisponível.
    REDIS_URL=redis://localhost:6379 pytest test_pool_snapshot_invariant.py
"""
from __future__ import annotations

import json
import os
import uuid

import pytest
import redis.asyncio as aioredis

from plughub_routing.registry import InstanceRegistry, _pool_snapshot_key
from plughub_routing.models import AgentInstance


# O serviço define `PLUGHUB_REDIS_URL` (docker-compose.demo.yml); ler só `REDIS_URL`
# fazia o teste cair no default `localhost`, falhar o ping e PULAR — dentro do
# próprio container onde o Redis está a um hostname de distância. Um skip é um teste
# que não pode reprovar; procurar as duas variáveis é o que o faz de fato rodar.
REDIS_URL = (
    os.environ.get("REDIS_URL")
    or os.environ.get("PLUGHUB_REDIS_URL")
    or "redis://localhost:6379"
)


@pytest.fixture
async def env():
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        pytest.skip(f"Redis indisponível em {REDIS_URL} — teste de integração pulado")
    reg    = InstanceRegistry(client)
    tenant = f"t_snap_{uuid.uuid4().hex[:8]}"
    pool   = "pool_snap_demo"
    try:
        yield reg, client, tenant, pool
    finally:
        async for k in client.scan_iter(f"{tenant}:*"):
            await client.delete(k)
        await client.aclose()


async def _seed(reg, client, tenant, pool, capacity: int, instance: str):
    """
    Instância pronta + snapshot coerente (available == capacidade, busy 0).

    O SADD no ready_set é feito à mão de propósito: `set_instance` grava só a
    CHAVE da instância — quem faz a membership é o login (mcp-server, via EVAL) e
    o `_upsert_instance`. Sem ele o `write_pool_snapshot` não veria o agente e o
    teste mediria um pool vazio, passando por motivo errado.
    """
    await reg.set_instance(AgentInstance(
        instance_id=instance, agent_type_id="human", tenant_id=tenant,
        pools=[pool], execution_model="stateful",
        max_concurrent=capacity, current_sessions=0, state="ready",
        source="human_login",
    ))
    await client.sadd(f"{tenant}:pool:{pool}:instances", instance)
    await client.set(_pool_snapshot_key(tenant, pool), json.dumps({
        "pool_id": pool, "tenant_id": tenant,
        "available": capacity, "busy": 0, "total_instances": capacity,
        "queue_length": 0, "sla_target_ms": 480_000, "channel_types": ["webchat"],
    }), ex=120)


async def _snap(client, tenant, pool) -> dict:
    return json.loads(await client.get(_pool_snapshot_key(tenant, pool)))


@pytest.mark.asyncio
async def test_available_never_exceeds_capacity_on_spurious_removal(env):
    """
    Remoções SEM mark_busy correspondente não podem inflar `available`.

    É a reprodução exata do `available 4 / total 3`: o pool tem capacidade 3 e
    ninguém ocupado; três `remove_conversation` espúrios somariam +3 ao available.
    """
    reg, client, tenant, pool = env
    inst = "human-snap-1"
    await _seed(reg, client, tenant, pool, capacity=3, instance=inst)

    for i in range(3):
        await reg.remove_conversation(tenant, inst, f"conv-spurious-{i}")

    snap = await _snap(client, tenant, pool)
    assert snap["available"] <= snap["total_instances"], (
        f"available={snap['available']} passou de total_instances="
        f"{snap['total_instances']} — alguém voltou a SOMAR sobre o valor anterior "
        f"em vez de recalcular"
    )
    # E a ocupação publicada nunca é negativa nem inventada (fatia 2: derivada).
    assert snap["busy"] == 0 and snap["busy_elsewhere"] == 0, snap
    assert snap["available"] == 3, (
        f"remoções espúrias não podem MUDAR a capacidade: {snap}"
    )


@pytest.mark.asyncio
async def test_available_still_increments_on_legitimate_release(env):
    """
    Regressão simétrica: recalcular não pode congelar a devolução LEGÍTIMA.

    Com uma sessão realmente ocupada (available 2 de 3), terminar devolve a vaga —
    available volta a 3. Um recompute que lesse a fonte errada (ou um teto mal
    posto, no modelo antigo) faria a vaga nunca voltar, que é pior que o defeito
    original: capacidade some.
    """
    reg, client, tenant, pool = env
    inst = "human-snap-2"
    await _seed(reg, client, tenant, pool, capacity=3, instance=inst)

    # Estado "uma sessão em curso" pelo caminho REAL: claim atômico (com a tag do
    # pool) + mark_busy. Forçar o snapshot à mão não serviria — o recompute não lê
    # o valor anterior, é justamente esse o ponto.
    assert await reg.claim_instance(
        tenant, inst, "conv-real", None, 3, pool_id=pool
    ) == 1
    await reg.mark_busy(tenant, pool, inst, "conv-real")
    mid = await _snap(client, tenant, pool)
    assert (mid["available"], mid["busy"]) == (2, 1), (
        f"o setup não chegou ao estado 'uma sessão em curso': {mid}"
    )

    await reg.remove_conversation(tenant, inst, "conv-real")

    snap = await _snap(client, tenant, pool)
    assert snap["available"] == 3, (
        f"a vaga não voltou: available={snap['available']} (esperado 3)"
    )
    assert snap["available"] <= snap["total_instances"]


@pytest.mark.asyncio
async def test_logged_out_agent_stops_counting_capacity(env):
    """
    Agente deslogado NÃO pode continuar somando capacidade.

    `_deactivate_instance` marcava `state=logged_out` mas só removia dos pool sets
    no ramo de fallback (chave já apagada). No caso normal a instância seguia
    membro do ready_set, e o `write_pool_snapshot` contava o `max_concurrent` dela
    pelo laço de membros PULADOS — o Monitor exibia `available 1 / total 1` num
    pool sem ninguém logado. Capacidade fantasma: o roteamento acredita nela.
    """
    from plughub_routing.kafka_listener import LifecycleEventHandler

    reg, client, tenant, pool = env
    inst = "human-snap-logout"
    await _seed(reg, client, tenant, pool, capacity=3, instance=inst)
    assert await client.sismember(f"{tenant}:pool:{pool}:instances", inst)

    handler = LifecycleEventHandler(reg, pool_registry=None)
    await handler._deactivate_instance(tenant, inst, {
        "event": "agent_logout", "tenant_id": tenant,
        "instance_id": inst, "pools": [pool],
    })

    assert not await client.sismember(f"{tenant}:pool:{pool}:instances", inst), (
        "instância deslogada continua no ready_set — voltará a somar capacidade"
    )

    await reg.write_pool_snapshot(
        tenant_id=tenant, pool_id=pool, sla_target_ms=480_000,
        channel_types=["webchat"],
    )
    snap = await _snap(client, tenant, pool)
    assert snap["total_instances"] == 0, (
        f"pool sem ninguém logado ainda declara capacidade {snap['total_instances']}"
    )
    assert snap["available"] == 0


@pytest.mark.asyncio
async def test_agent_ready_without_resource_facts_does_not_recreate_human(env):
    """
    `agent_ready` tardio (aba obsoleta / republish) NÃO pode recriar a instância
    humana — recriar grava os DEFAULTS (`max_concurrent=1`, identidade vazia), que
    na tela é indistinguível de uma capacidade configurada.

    O discriminador é o PAYLOAD: só o login manda `user_id` + `max_concurrent_sessions`.
    """
    from plughub_routing.kafka_listener import LifecycleEventHandler

    reg, client, tenant, pool = env
    inst = "human-snap-ghost"
    handler = LifecycleEventHandler(reg, pool_registry=None)

    # Chave ausente (pós-logout) + agent_ready SEM os fatos de recurso.
    res = await handler._upsert_instance(tenant, inst, {
        "event": "agent_ready", "tenant_id": tenant, "instance_id": inst,
        "pools": [pool], "agent_type_id": f"human_agent_{pool}",
    }, event_type="agent_ready")

    assert res is None, "recriou a instância a partir de um evento sem autoridade"
    assert await client.get(f"{tenant}:instance:{inst}") is None, (
        "instância fantasma criada — teria max_concurrent=1 e identidade vazia"
    )

    # O login (com os fatos) SEGUE criando — o guard não pode barrar o caminho real.
    res2 = await handler._upsert_instance(tenant, inst, {
        "event": "agent_ready", "tenant_id": tenant, "instance_id": inst,
        "pools": [pool], "agent_type_id": f"human_agent_{pool}",
        "user_id": "u-123", "user_login": "a@b.c",
        "max_concurrent_sessions": 3, "execution_model": "stateful",
    }, event_type="agent_ready")

    assert res2 is not None, "o guard barrou o LOGIN — pior que o defeito original"
    live = json.loads(await client.get(f"{tenant}:instance:{inst}"))
    assert live["max_concurrent"] == 3, f"capacidade do login perdida: {live}"


@pytest.mark.asyncio
async def test_previous_snapshot_never_contaminates_the_next(env):
    """
    O snapshot anterior NÃO é entrada do próximo.

    Era: o `+1` somava sobre o `available` que estivesse gravado, então um snapshot
    legado, truncado ou corrompido se propagava indefinidamente — e o teste desta
    posição precisava tratar "sem `total_instances`" como caso especial.

    Agora o recompute deriva tudo do semáforo; o valor gravado é saída, nunca
    entrada. Este teste grava um snapshot deliberadamente MENTIROSO (available 99,
    busy 42, sem `total_instances`) e exige que a próxima escrita o ignore por
    completo — 2 vagas, nenhuma ocupada.
    """
    reg, client, tenant, pool = env
    inst = "human-snap-3"
    await reg.set_instance(AgentInstance(
        instance_id=inst, agent_type_id="human", tenant_id=tenant,
        pools=[pool], execution_model="stateful",
        max_concurrent=2, current_sessions=0, state="ready",
    ))
    await client.set(_pool_snapshot_key(tenant, pool), json.dumps({
        "pool_id": pool, "tenant_id": tenant,
        "available": 99, "busy": 42,        # mentira, e sem total_instances (legado)
        "queue_length": 0, "sla_target_ms": 480_000, "channel_types": ["webchat"],
    }), ex=120)

    await reg.remove_conversation(tenant, inst, "conv-legacy")

    snap = await _snap(client, tenant, pool)
    assert (snap["available"], snap["busy"], snap["total_instances"]) == (2, 0, 2), (
        f"o snapshot anterior contaminou o novo: {snap}"
    )
