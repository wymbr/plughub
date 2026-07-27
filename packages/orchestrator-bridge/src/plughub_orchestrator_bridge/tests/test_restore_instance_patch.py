"""
test_restore_instance_patch.py — F4 do ADR `adr-human-agent-pool-scoped-identity`.

`session:{sid}:routing:{iid}` guardava uma **cópia congelada** do registro da
instância, e `_restore_instance` a escrevia de volta INTEIRA por cima do registro
vivo no fim da sessão. Um humano que entrasse num pool DEPOIS do contato começar
sumia dele quando o contato fechasse — o último vetor de `pools` encolhendo que
sobrou depois da F1.

A chave encolheu para o único fato que ela tem e mais ninguém tem: **qual pool
esta instância serve NESTA sessão** (`{tenant_id, instance_id, pool_id}`) — o
mesmo dado que a F3 passou a ler para saber qual `active_count` decrementar. E a
restauração virou **patch**: toca só `status` e a ocupação (sincronizada do SCARD
do semáforo, que é a fonte de verdade desde a Fatia B), nunca identidade.

Chaves no formato ANTIGO continuam válidas: ninguém lê mais o sub-documento
`snapshot`, então ele é ignorado e o TTL de 4 h drena o resto. Sem migração — é
exatamente o que o teste `old_format` prova.

Teste de INTEGRAÇÃO: precisa de Redis real. Pulado automaticamente sem ele.
    REDIS_URL=redis://redis:6379 pytest test_restore_instance_patch.py
"""
from __future__ import annotations

import json
import os
import uuid

import pytest
import redis.asyncio as aioredis

import plughub_orchestrator_bridge.main as bridge_mod


REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379")

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

    tenant   = f"t_f4_{uuid.uuid4().hex[:8]}"
    session  = f"ses_{uuid.uuid4().hex[:8]}"
    instance = f"human-{uuid.uuid4().hex[:6]}"

    async def cleanup() -> None:
        keys = [
            f"{tenant}:instance:{instance}",
            f"{tenant}:instance:{instance}:sessions",
            f"session:{session}:routing:{instance}",
        ]
        for pool in (POOL_A, POOL_B, POOL_C):
            keys.append(f"{tenant}:pool:{pool}:instances")
        await client.delete(*keys)

    await cleanup()
    try:
        yield client, tenant, session, instance
    finally:
        await cleanup()
        await client.aclose()


async def _seed_live_human(client, tenant, instance, pools):
    await client.set(f"{tenant}:instance:{instance}", json.dumps({
        "instance_id": instance, "agent_type_id": f"human_agent_{pools[0]}",
        "tenant_id": tenant, "pools": pools, "pool_id": pools[0],
        "execution_model": "stateful", "max_concurrent": 3,
        "current_sessions": 1, "status": "busy", "source": "human_login",
        "user_id": "u1", "user_login": "u1@demo.local",
    }))


async def _read_live(client, tenant, instance) -> dict | None:
    raw = await client.get(f"{tenant}:instance:{instance}")
    return json.loads(raw) if raw else None


# ── o defeito ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_restore_does_not_shrink_pools_from_a_stale_snapshot(ctx):
    """O nó da F4, no formato ANTIGO da chave (o que está em produção agora):
    o humano foi roteado quando só estava em A; entrou em B e C durante o
    atendimento. O snapshot congelado diz `pools:[A]` — e não pode vencer."""
    client, tenant, session, instance = ctx
    await _seed_live_human(client, tenant, instance, [POOL_A, POOL_B, POOL_C])
    # chave no formato ANTIGO, com a cópia congelada de quando ele só tinha A
    await client.set(f"session:{session}:routing:{instance}", json.dumps({
        "tenant_id": tenant, "instance_id": instance, "pool_id": POOL_A,
        "snapshot": {
            "instance_id": instance, "agent_type_id": f"human_agent_{POOL_A}",
            "tenant_id": tenant, "pools": [POOL_A], "pool_id": POOL_A,
            "max_concurrent": 1, "current_sessions": 1, "status": "busy",
        },
    }), ex=14400)

    await bridge_mod._restore_instance(client, session, instance)

    live = await _read_live(client, tenant, instance)
    assert live is not None
    assert set(live["pools"]) == {POOL_A, POOL_B, POOL_C}
    assert live["max_concurrent"] == 3          # não voltou ao 1 do snapshot
    assert live["agent_type_id"] == f"human_agent_{POOL_A}"
    assert live["source"] == "human_login"      # identidade preservada


@pytest.mark.asyncio
async def test_restore_patches_availability_only(ctx):
    """O que a função É dona: devolver a instância ao estado disponível."""
    client, tenant, session, instance = ctx
    await _seed_live_human(client, tenant, instance, [POOL_A, POOL_B])
    await bridge_mod._write_routing_ref(client, session, tenant, instance, POOL_A)

    await bridge_mod._restore_instance(client, session, instance)

    live = await _read_live(client, tenant, instance)
    assert live["status"] == "ready"
    # ocupação vem do SCARD do semáforo (vazio aqui), não de um `-1` cego
    assert live["current_sessions"] == 0
    assert await client.sismember(f"{tenant}:pool:{POOL_A}:instances", instance)
    # a referência é consumida
    assert await client.get(f"session:{session}:routing:{instance}") is None


@pytest.mark.asyncio
async def test_occupancy_comes_from_the_semaphore_not_from_a_decrement(ctx):
    """Com outra sessão ainda ocupando a instância, o restore não pode zerar a
    ocupação — ele sincroniza com o SET de occupants."""
    client, tenant, session, instance = ctx
    await _seed_live_human(client, tenant, instance, [POOL_A])
    await client.sadd(f"{tenant}:instance:{instance}:sessions", "outra-sessao::")
    await bridge_mod._write_routing_ref(client, session, tenant, instance, POOL_A)

    await bridge_mod._restore_instance(client, session, instance)

    live = await _read_live(client, tenant, instance)
    assert live["current_sessions"] == 1


@pytest.mark.asyncio
async def test_absent_instance_is_not_resurrected(ctx):
    """Registro ausente = agente deslogado (ou IA que o reconciliador vai
    recriar do Registry, que é autoritativo). Ressuscitar de uma cópia de até
    4 h atrás produz agente fantasma: presente pro roteamento, ausente de fato."""
    client, tenant, session, instance = ctx
    # nada de registro vivo — só a referência, no formato antigo (com snapshot,
    # que teria tudo o que seria preciso para recriar — e ainda assim não recria)
    await client.set(f"session:{session}:routing:{instance}", json.dumps({
        "tenant_id": tenant, "instance_id": instance, "pool_id": POOL_A,
        "snapshot": {
            "instance_id": instance, "agent_type_id": f"human_agent_{POOL_A}",
            "tenant_id": tenant, "pools": [POOL_A], "status": "busy",
        },
    }), ex=14400)

    await bridge_mod._restore_instance(client, session, instance)

    assert await _read_live(client, tenant, instance) is None
    # a referência é consumida de qualquer forma (não fica lixo pendurado)
    assert await client.get(f"session:{session}:routing:{instance}") is None


# ── formato novo ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_routing_ref_holds_only_the_per_session_fact(ctx):
    """A chave guarda o pool desta sessão para esta instância — e nada da cópia
    do registro. É esse `pool_id` que a F3 lê no agent_done do claimante."""
    client, tenant, session, instance = ctx
    await bridge_mod._write_routing_ref(client, session, tenant, instance, POOL_B)

    ref = json.loads(await client.get(f"session:{session}:routing:{instance}"))
    assert ref == {
        "tenant_id": tenant, "instance_id": instance, "pool_id": POOL_B,
    }
    assert await client.ttl(f"session:{session}:routing:{instance}") > 0
