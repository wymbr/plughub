"""
test_human_instance_identity.py — F1 do ADR `adr-human-agent-pool-scoped-identity`.

**Liveness ≠ identidade.** Um humano tem UMA instância (`human-{userId}`) e N
conexões WebSocket (o Console abre uma por pool selecionado). Cada conexão emite
seu próprio pong a cada 15 s; antes da F1 o `_upsert_instance` reconstruía o
registro INTEIRO a partir do evento, então `pools[]` e `agent_type_id` oscilavam
entre os pools conforme qual conexão pingou por último.

Consequências reais que estes testes travam:
  • contato roteado com o `agent_type_id` de OUTRO pool (o bridge roda o skill que
    aquele id resolver — foi assim que um contato da fila "sumiu", rodando
    `skill_wrapup_detached_v1` na própria sessão do contato);
  • humano desaparecendo de pools onde segue logado (`set_instance` faz SREM dos
    pool sets a partir do `pools[]` do momento).

Nenhum teste cobria multi-pool humano antes desta suíte — `test_work_queue_claim`
e `test_scorer` só constroem instâncias com `pools=[pool]`.

Teste de INTEGRAÇÃO: precisa de um Redis real. É pulado automaticamente se não
houver Redis acessível.
    REDIS_URL=redis://redis:6379 pytest test_human_instance_identity.py
"""
from __future__ import annotations

import json
import os
import uuid

import pytest
import redis.asyncio as aioredis

from plughub_routing.kafka_listener import LifecycleEventHandler
from plughub_routing.registry import (
    InstanceRegistry,
    _instance_key,
    _instance_meta_key,
    _instance_sessions_key,
    _pool_instances_key,
)


REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

POOL_A = "retencao_humano"
POOL_B = "formfill_demo"
POOL_C = "aprovacao_deploy"


@pytest.fixture
async def ctx():
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        pytest.skip(f"Redis indisponível em {REDIS_URL} — teste de integração pulado")

    tenant   = f"t_hid_{uuid.uuid4().hex[:8]}"
    user_id  = f"u_{uuid.uuid4().hex[:6]}"
    instance = f"human-{user_id}"
    reg      = InstanceRegistry(client)
    handler  = LifecycleEventHandler(reg)

    async def cleanup() -> None:
        keys = [
            _instance_key(tenant, instance),
            _instance_meta_key(tenant, instance),
            _instance_sessions_key(tenant, instance),
        ]
        for pool in (POOL_A, POOL_B, POOL_C):
            keys.append(_pool_instances_key(tenant, pool))
        await client.delete(*keys)

    await cleanup()
    try:
        yield reg, handler, client, tenant, instance, user_id
    finally:
        await cleanup()
        await client.aclose()


# ── helpers ───────────────────────────────────────────────────────────────────

async def _seed_human(
    client, tenant: str, instance: str, user_id: str, pools: list[str],
    *, max_concurrent: int = 3, current_sessions: int = 0,
) -> None:
    """Escreve o registro exatamente como `registerHumanAgent` (mcp-server) faz:
    JSON string, `source: human_login`, sem TTL, `pools` já mesclado."""
    await client.set(_instance_key(tenant, instance), json.dumps({
        "instance_id":      instance,
        "agent_type_id":    f"human_agent_{pools[0]}",
        "user_id":          user_id,
        "user_login":       f"{user_id}@demo.local",
        "tenant_id":        tenant,
        "pools":            pools,
        "execution_model":  "stateful",
        "max_concurrent":   max_concurrent,
        "current_sessions": current_sessions,
        "status":           "ready",
        "source":           "human_login",
    }))
    for pool in pools:
        await client.sadd(_pool_instances_key(tenant, pool), instance)


def _heartbeat(tenant: str, instance: str, user_id: str, pool: str) -> dict:
    """Pong de UMA conexão WS, no formato pós-F1 (sem pools/agent_type_id)."""
    return {
        "event":                   "agent_heartbeat",
        "tenant_id":               tenant,
        "instance_id":             instance,
        "heartbeat_pool":          pool,
        "user_id":                 user_id,
        "user_login":              f"{user_id}@demo.local",
        "status":                  "ready",
        "execution_model":         "stateful",
        "max_concurrent_sessions": 3,
        "timestamp":               "2026-07-27T12:00:00Z",
    }


def _legacy_heartbeat(tenant: str, instance: str, user_id: str, pool: str) -> dict:
    """Pong de um produtor LEGADO (pré-F1): carrega identidade e membership da
    própria conexão. A routing precisa ignorá-los mesmo assim — a defesa não pode
    depender de todo produtor estar atualizado."""
    ev = _heartbeat(tenant, instance, user_id, pool)
    ev.pop("heartbeat_pool", None)
    ev["agent_type_id"]    = f"human_agent_{pool}"
    ev["pools"]            = [pool]
    ev["current_sessions"] = 0
    return ev


async def _read(client, tenant: str, instance: str) -> dict:
    return json.loads(await client.get(_instance_key(tenant, instance)))


# ── o defeito observado ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_heartbeat_from_one_pool_does_not_shrink_membership(ctx):
    """O nó do bug: humano logado em 3 pools, pong chega da conexão do pool B.
    Antes da F1 o registro virava `pools:[B]` e o agente sumia de A e C."""
    reg, handler, client, tenant, instance, user_id = ctx
    await _seed_human(client, tenant, instance, user_id, [POOL_A, POOL_B, POOL_C])

    await handler.handle(_heartbeat(tenant, instance, user_id, POOL_B))

    data = await _read(client, tenant, instance)
    assert set(data["pools"]) == {POOL_A, POOL_B, POOL_C}
    # E o agente continua nos SETs de roteamento dos três pools.
    for pool in (POOL_A, POOL_B, POOL_C):
        assert await client.sismember(_pool_instances_key(tenant, pool), instance)


@pytest.mark.asyncio
async def test_alternating_heartbeats_do_not_flip_agent_type_id(ctx):
    """Pongs alternados das N conexões faziam `agent_type_id` oscilar a cada 15 s —
    e é esse valor que vira `conversations.routed.agent_type_id`, com o qual o
    bridge escolhe o que executar."""
    reg, handler, client, tenant, instance, user_id = ctx
    await _seed_human(client, tenant, instance, user_id, [POOL_A, POOL_B])
    original = (await _read(client, tenant, instance))["agent_type_id"]

    for pool in (POOL_B, POOL_A, POOL_B, POOL_B, POOL_A):
        await handler.handle(_heartbeat(tenant, instance, user_id, pool))
        data = await _read(client, tenant, instance)
        assert data["agent_type_id"] == original
        assert set(data["pools"]) == {POOL_A, POOL_B}


@pytest.mark.asyncio
async def test_legacy_heartbeat_payload_is_ignored(ctx):
    """Defesa no consumidor: mesmo um produtor pré-F1 (que ainda manda
    `pools`/`agent_type_id` da própria conexão) não corrompe o registro."""
    reg, handler, client, tenant, instance, user_id = ctx
    await _seed_human(client, tenant, instance, user_id, [POOL_A, POOL_B, POOL_C])

    await handler.handle(_legacy_heartbeat(tenant, instance, user_id, POOL_B))

    data = await _read(client, tenant, instance)
    assert set(data["pools"]) == {POOL_A, POOL_B, POOL_C}
    assert data["agent_type_id"] == f"human_agent_{POOL_A}"


@pytest.mark.asyncio
async def test_heartbeat_does_not_overwrite_occupancy(ctx):
    """`current_sessions` do pong conta só as sessões DAQUELA conexão. A verdade é
    o SCARD do semáforo, espelhado por mark_busy/remove_conversation."""
    reg, handler, client, tenant, instance, user_id = ctx
    await _seed_human(
        client, tenant, instance, user_id, [POOL_A, POOL_B], current_sessions=2,
    )

    ev = _legacy_heartbeat(tenant, instance, user_id, POOL_B)
    ev["current_sessions"] = 0          # esta conexão não vê as sessões do outro pool
    await handler.handle(ev)

    assert (await _read(client, tenant, instance))["current_sessions"] == 2


@pytest.mark.asyncio
async def test_agent_busy_preserves_membership(ctx):
    """`agent_busy` também não é evento de membership."""
    reg, handler, client, tenant, instance, user_id = ctx
    await _seed_human(client, tenant, instance, user_id, [POOL_A, POOL_B])

    await handler.handle({
        "event": "agent_busy", "tenant_id": tenant, "instance_id": instance,
        "agent_type_id": f"human_agent_{POOL_B}", "pools": [POOL_B],
        "status": "busy", "execution_model": "stateful",
        "conversation_id": "ses-x", "timestamp": "2026-07-27T12:00:00Z",
    })

    data = await _read(client, tenant, instance)
    assert set(data["pools"]) == {POOL_A, POOL_B}
    assert data["agent_type_id"] == f"human_agent_{POOL_A}"


# ── o que AINDA deve mudar a membership ───────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_ready_is_authoritative_partial_logout_shrinks(ctx):
    """Logout parcial é o caminho LEGÍTIMO de remoção: `agent_ready` é o único
    evento emitido por quem conhece o conjunto completo. Preservar aqui deixaria
    o agente preso num pool do qual ele saiu.

    A asserção do SET de roteamento expôs um buraco real: `set_instance` só
    percorre os pools que a instância AINDA declara, então o pool abandonado
    nunca era limpo por ele. Quem limpava era o `unregisterHumanAgent`
    (mcp-server) por escrita direta no Redis — logo o consumidor de lifecycle
    dependia de um efeito colateral de OUTRO serviço para não deixar a instância
    alocável num pool do qual ela saiu. Agora a limpeza acompanha o evento
    (`remove_from_pool_sets`), e o SREM do mcp-server vira redundância inofensiva."""
    reg, handler, client, tenant, instance, user_id = ctx
    await _seed_human(client, tenant, instance, user_id, [POOL_A, POOL_B, POOL_C])

    await handler.handle({
        "event": "agent_ready", "tenant_id": tenant, "instance_id": instance,
        "agent_type_id": f"human_agent_{POOL_A}", "status": "ready",
        "execution_model": "stateful", "pools": [POOL_A, POOL_C],
        "timestamp": "2026-07-27T12:00:00Z",
    })

    data = await _read(client, tenant, instance)
    assert set(data["pools"]) == {POOL_A, POOL_C}
    assert not await client.sismember(_pool_instances_key(tenant, POOL_B), instance)


@pytest.mark.asyncio
async def test_agent_ready_login_grows_membership(ctx):
    """Login num pool adicional (o mcp-server manda `mergedPools`)."""
    reg, handler, client, tenant, instance, user_id = ctx
    await _seed_human(client, tenant, instance, user_id, [POOL_A])

    await handler.handle({
        "event": "agent_ready", "tenant_id": tenant, "instance_id": instance,
        "agent_type_id": f"human_agent_{POOL_B}", "status": "ready",
        "execution_model": "stateful", "pools": [POOL_A, POOL_B],
        "timestamp": "2026-07-27T12:00:00Z",
    })

    data = await _read(client, tenant, instance)
    assert set(data["pools"]) == {POOL_A, POOL_B}
    for pool in (POOL_A, POOL_B):
        assert await client.sismember(_pool_instances_key(tenant, pool), instance)


@pytest.mark.asyncio
async def test_instance_meta_mirrors_the_record_not_the_event(ctx):
    """O `instance_meta` é lido pelo `remove_conversation` (qual pool decrementar) e
    pelo `crash_detector`. Se ele guardasse o valor do EVENTO, voltaria a divergir
    do registro que a F1 acabou de proteger."""
    reg, handler, client, tenant, instance, user_id = ctx
    await _seed_human(client, tenant, instance, user_id, [POOL_A, POOL_B])

    await handler.handle({
        "event": "agent_ready", "tenant_id": tenant, "instance_id": instance,
        # Evento propõe uma identidade que o registro vai preservar/ignorar.
        "agent_type_id": "skill_wrapup_detached_v1",
        "status": "ready", "execution_model": "stateful",
        "pools": [POOL_A, POOL_B], "timestamp": "2026-07-27T12:00:00Z",
    })

    meta = await reg.get_instance_meta(tenant, instance)
    record = await _read(client, tenant, instance)
    assert meta.agent_type_id == record["agent_type_id"]
    assert meta.agent_type_id != "skill_wrapup_detached_v1"
    assert set(meta.pools) == set(record["pools"])


# ── caminhos de borda ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_liveness_event_never_resurrects_absent_human(ctx):
    """Registro ausente + pong: NÃO recria. O caso real é uma aba esquecida que
    segue pingando depois do logout completo (que faz DEL da chave) — recriar
    ali produz um agente FANTASMA, presente para o roteamento e ausente para o
    humano, e os contatos alocados a ele não aparecem em Console nenhum.
    Falhar visível > falhar invisível."""
    reg, handler, client, tenant, instance, user_id = ctx
    # nada semeado — a chave não existe (agente deslogado)
    await handler.handle(_heartbeat(tenant, instance, user_id, POOL_B))
    assert await client.get(_instance_key(tenant, instance)) is None

    # Idem para o payload legado, que carrega pools/agent_type_id suficientes
    # para "parecer" um registro completo.
    await handler.handle(_legacy_heartbeat(tenant, instance, user_id, POOL_B))
    assert await client.get(_instance_key(tenant, instance)) is None


@pytest.mark.asyncio
async def test_first_agent_ready_creates_the_human_instance(ctx):
    """A contrapartida: criação é do LOGIN. `agent_ready` sem registro prévio
    (primeiro login) constrói normalmente — senão o agente nunca entraria."""
    reg, handler, client, tenant, instance, user_id = ctx

    await handler.handle({
        "event": "agent_ready", "tenant_id": tenant, "instance_id": instance,
        "agent_type_id": f"human_agent_{POOL_A}", "status": "ready",
        "execution_model": "stateful", "pools": [POOL_A, POOL_B],
        "max_concurrent_sessions": 3, "current_sessions": 0,
        "user_id": user_id, "user_login": f"{user_id}@demo.local",
        "timestamp": "2026-07-27T12:00:00Z",
    })

    data = await _read(client, tenant, instance)
    assert set(data["pools"]) == {POOL_A, POOL_B}
    assert data["execution_model"] == "stateful"   # senão o bridge não ativa o Console


@pytest.mark.asyncio
async def test_ai_instance_behaviour_unchanged(ctx):
    """IA é criada e mantida pelo reconciliador por-pool; `pools` é genuinamente
    unitário e o evento SEGUE sendo a fonte. A F1 não pode tê-la afetado."""
    reg, handler, client, tenant, _instance, _user_id = ctx
    ai_instance = "agente_retencao_v1-001"
    try:
        await client.set(_instance_key(tenant, ai_instance), json.dumps({
            "instance_id": ai_instance, "agent_type_id": "agente_retencao_v1",
            "tenant_id": tenant, "pool_id": POOL_A, "pools": [POOL_A],
            "execution_model": "stateless", "max_concurrent": 1,
            "current_sessions": 0, "status": "ready",
        }))

        await handler.handle({
            "event": "agent_heartbeat", "tenant_id": tenant,
            "instance_id": ai_instance, "agent_type_id": "agente_retencao_v2",
            "pools": [POOL_C], "status": "ready", "execution_model": "stateless",
            "max_concurrent_sessions": 2, "current_sessions": 1,
            "timestamp": "2026-07-27T12:00:00Z",
        })

        data = await _read(client, tenant, ai_instance)
        assert data["pools"] == [POOL_C]
        assert data["agent_type_id"] == "agente_retencao_v2"
        assert data["current_sessions"] == 1
    finally:
        await client.delete(_instance_key(tenant, ai_instance))
        await client.delete(_instance_meta_key(tenant, ai_instance))
