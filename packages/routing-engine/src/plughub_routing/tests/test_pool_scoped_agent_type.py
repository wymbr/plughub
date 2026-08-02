"""
test_pool_scoped_agent_type.py — F2/F3 do ADR `adr-human-agent-pool-scoped-identity`.

**F2 — identidade por-pool é derivada, não armazenada.** Para um humano,
`agent_type_id` é função pura do pool (`human_agent_{pool}`): a MESMA instância
atende N pools, então não existe um "tipo" único que o registro do recurso possa
guardar. O campo armazenado é resíduo arbitrário (o pool do primeiro login) — e é
ele que virava `conversations.routed.agent_type_id`, com o qual o bridge decide
**o que executar**. Para IA o campo é identidade legítima e passa intacto.

**F3 — qual pool serviu o contato é fato por-sessão.** `remove_conversation` dava
precedência a `meta.pools` (per-RECURSO = o conjunto INTEIRO de pools do agente)
sobre o `pools` do evento `agent_done`, emitido por quem sabe qual pool serviu
aquele contato. Para humano multi-pool isso decrementava o `active_count` de
pools que não serviram: o pool que serviu ficava com carga fantasma (fila não
drena) e os outros iam a zero.

**Fatia 2 — a F3 mudou de mecanismo.** Não existe mais contador por pool a
decrementar: `busy` é a projeção da TAG do membro do semáforo, então "qual pool
serviu" é lido do PRÓPRIO dado, não inferido de uma precedência de listas. A
classe de defeito ficou estruturalmente impossível — e é isso que os testes de
integração abaixo afirmam agora: um `agent_done` do contato servido em A zera o
`busy` de A e **não pode tocar** o de B, mesmo que B esteja em `meta.pools`. A
precedência sobrevive no código porque ainda decide a MEMBERSHIP dos SETs.

F2 é unitário (função pura). F3 precisa de Redis real e é pulado sem ele.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest
import redis.asyncio as aioredis

from plughub_routing.models import AgentInstance, resolve_agent_type
from plughub_routing.registry import (
    InstanceRegistry,
    _instance_key,
    _instance_sessions_key,
    _pool_instances_key,
    _pool_snapshot_key,
)


# O serviço define `PLUGHUB_REDIS_URL`; ler só `REDIS_URL` faz o teste PULAR dentro
# do próprio container onde o Redis está a um hostname de distância.
REDIS_URL = (
    os.environ.get("REDIS_URL")
    or os.environ.get("PLUGHUB_REDIS_URL")
    or "redis://localhost:6379"
)

POOL_A = "retencao_humano"
POOL_B = "formfill_demo"


# ── F2 — derivação (unitário, sem I/O) ───────────────────────────────────────

def _human(pools: list[str], stored_type: str) -> AgentInstance:
    return AgentInstance(
        instance_id="human-u123", agent_type_id=stored_type,
        tenant_id="t", pools=pools,
        execution_model="stateful", max_concurrent=3,
    )


def test_human_agent_type_follows_the_pool_not_the_record():
    """O nó da F2: o registro guarda o pool do primeiro login, mas o contato foi
    roteado a OUTRO pool. É o pool do roteamento que vale."""
    inst = _human([POOL_A, POOL_B], stored_type=f"human_agent_{POOL_A}")
    assert resolve_agent_type(inst, POOL_B) == f"human_agent_{POOL_B}"
    assert resolve_agent_type(inst, POOL_A) == f"human_agent_{POOL_A}"


def test_derivation_is_immune_to_a_corrupted_stored_value():
    """O defeito real observado: a instância humana foi reescrita com a
    identidade de um WORKFLOW e o bridge rodou `skill_wrapup_detached_v1` na
    sessão do contato, que completou e a fechou. Derivando, o valor podre no
    registro não tem por onde vazar."""
    inst = _human([POOL_A], stored_type="skill_wrapup_detached_v1")
    assert resolve_agent_type(inst, POOL_A) == f"human_agent_{POOL_A}"


def test_ai_instance_keeps_its_stored_agent_type():
    """IA: uma instância pertence a um agent type e a um pool — o campo é
    identidade legítima e não pode ser reescrito pela derivação."""
    ai = AgentInstance(
        instance_id="agente_retencao_v1-001", agent_type_id="agente_retencao_v1",
        tenant_id="t", pools=[POOL_A],
    )
    assert resolve_agent_type(ai, POOL_A) == "agente_retencao_v1"
    # Mesmo consultado com outro pool (não deve acontecer), nunca vira human_agent_*.
    assert resolve_agent_type(ai, POOL_B) == "agente_retencao_v1"


def test_no_pool_in_scope_falls_back_to_the_stored_value():
    """Sem pool não há o que derivar — devolve o armazenado em vez de inventar."""
    inst = _human([POOL_A], stored_type=f"human_agent_{POOL_A}")
    assert resolve_agent_type(inst, "") == f"human_agent_{POOL_A}"


def test_is_human_survives_the_model_round_trip():
    """`source` não sobrevive ao `model_validate → model_dump` do `mark_busy`
    (Pydantic descarta campo não declarado). Por isso o discriminador é o
    prefixo do instance_id — este teste trava essa dependência."""
    inst = _human([POOL_A], stored_type=f"human_agent_{POOL_A}")
    round_tripped = AgentInstance.model_validate(
        json.loads(json.dumps(inst.model_dump()))
    )
    assert round_tripped.is_human
    assert resolve_agent_type(round_tripped, POOL_B) == f"human_agent_{POOL_B}"


# ── F3 — precedência do pool a decrementar (integração) ──────────────────────

@pytest.fixture
async def ctx():
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        pytest.skip(f"Redis indisponível em {REDIS_URL} — teste de integração pulado")

    tenant   = f"t_f3_{uuid.uuid4().hex[:8]}"
    instance = f"human-{uuid.uuid4().hex[:6]}"
    reg      = InstanceRegistry(client)

    async def cleanup() -> None:
        async for k in client.scan_iter(f"{tenant}:*"):
            await client.delete(k)

    await cleanup()
    try:
        yield reg, client, tenant, instance
    finally:
        await cleanup()
        await client.aclose()


async def _snap(client, tenant, pool) -> dict:
    raw = await client.get(_pool_snapshot_key(tenant, pool))
    assert raw, f"snapshot ausente para {pool} — o teste não tem o que julgar"
    return json.loads(raw)


async def _human_in(reg, client, tenant, instance, pools: list[str], capacity=3):
    await client.set(_instance_key(tenant, instance), json.dumps({
        "instance_id": instance, "agent_type_id": f"human_agent_{pools[0]}",
        "tenant_id": tenant, "pools": pools,
        "execution_model": "stateful", "max_concurrent": capacity,
        "current_sessions": 0, "status": "ready", "source": "human_login",
    }))
    for pool in pools:
        await client.sadd(_pool_instances_key(tenant, pool), instance)
        await reg.write_pool_snapshot(
            tenant_id=tenant, pool_id=pool,
            sla_target_ms=480_000, channel_types=["webchat"],
        )


@pytest.mark.asyncio
async def test_the_serving_pool_is_read_from_the_data_not_inferred(ctx):
    """O nó da F3, no mecanismo novo.

    O humano está em A e B (meta = ambos) e atende UM contato em cada. O
    `agent_done` do contato de A não pode mexer no `busy` de B — e agora isso não
    depende de nenhuma precedência de listas: cada ocupante carrega a tag do pool
    que o serviu, então "qual pool serviu" é dado, não inferência.
    """
    reg, client, tenant, instance = ctx
    await _human_in(reg, client, tenant, instance, [POOL_A, POOL_B])
    # meta = conjunto INTEIRO de pools do recurso (o que a precedência antiga usava)
    await reg.update_instance_meta(
        tenant, instance, pools=[POOL_A, POOL_B],
        agent_type_id=f"human_agent_{POOL_A}",
    )

    assert await reg.claim_instance(tenant, instance, "ses-a", None, 3, pool_id=POOL_A) == 1
    assert await reg.claim_instance(tenant, instance, "ses-b", None, 3, pool_id=POOL_B) == 2
    await reg.mark_busy(tenant, POOL_A, instance, "ses-a")

    before_a, before_b = await _snap(client, tenant, POOL_A), await _snap(client, tenant, POOL_B)
    assert (before_a["busy"], before_a["busy_elsewhere"]) == (1, 1), before_a
    assert (before_b["busy"], before_b["busy_elsewhere"]) == (1, 1), before_b

    # agent_done do contato servido em A (o bridge sabe qual pool serviu)
    await reg.remove_conversation(
        tenant, instance, "ses-a", fallback_pools=[POOL_A],
    )

    after_a, after_b = await _snap(client, tenant, POOL_A), await _snap(client, tenant, POOL_B)
    assert after_a["busy"] == 0, f"A ficou com carga fantasma: {after_a}"
    assert after_b["busy"] == 1, (
        f"o contato de B foi levado junto pelo agent_done de A: {after_b}"
    )
    # A vaga devolvida é do RECURSO: a linha de B também precisa enxergá-la.
    assert after_b["busy_elsewhere"] == 0 and after_b["available"] == 2, after_b
    assert await reg.instance_session_count(tenant, instance) == 1


@pytest.mark.asyncio
async def test_meta_still_used_when_event_omits_pools(ctx):
    """O fallback que motivou a precedência antiga continua vivo: agentes que
    nunca publicaram agent_ready e cujo agent_done não traz `pools`. Ele decide a
    membership dos SETs e o alcance do fan-out do snapshot."""
    reg, client, tenant, instance = ctx
    await _human_in(reg, client, tenant, instance, [POOL_A], capacity=1)
    await reg.update_instance_meta(
        tenant, instance, pools=[POOL_A], agent_type_id="x",
    )
    assert await reg.claim_instance(tenant, instance, "ses-b", None, 1, pool_id=POOL_A) == 1
    await reg.mark_busy(tenant, POOL_A, instance, "ses-b")
    assert (await _snap(client, tenant, POOL_A))["busy"] == 1

    await reg.remove_conversation(tenant, instance, "ses-b", fallback_pools=[])

    snap = await _snap(client, tenant, POOL_A)
    assert (snap["busy"], snap["available"]) == (0, 1), snap
    assert not await client.exists(_instance_sessions_key(tenant, instance))
