"""
test_shared_capacity_snapshot.py — capacidade compartilhada, fatia 2 (defeito A).

**O defeito.** `available`/`busy` do snapshot de pool vinham de `active_count`, um
contador POR POOL. Capacidade, porém, é do RECURSO: um humano `max_concurrent 3`
logado em 3 pools tem UMA reserva de 3 vagas, não três. Vaga tomada por um pool não
descontava nos irmãos — cada linha dizia `available 3` e a tela somava 6, quando a
verdade era 2. Medido ao vivo em 2026-07-31 (TODO § Linha de base medida).

**A correção.** `available`/`busy` passam a ser DERIVADOS do semáforo do recurso
(`{t}:instance:{iid}:sessions`), num recompute em Lua:

    total_capacity = Σ max_concurrent(i)                   sobre ready_set ∪ busy_set
    used_global    = Σ SCARD(sessions_i)                   inclui irmãos E holds
    used_here      = Σ #{ m : occupant_pool(m) == P }      projeção pela TAG (F1)
    available      = max(0, total_capacity − used_global)
    busy           = used_here
    busy_elsewhere = used_global − used_here

Nenhum contador entra na conta — nem `active_count` nem `current_sessions`. Trocar
um contador por outro não fecharia a classe de defeito, só mudaria qual mente depois.

**Por que estes testes podem reprovar.** Contra o código anterior à fatia 2 o
primeiro teste devolve `3/3/3` onde afirma `2/2/2` — o defeito medido, reproduzido.
Os demais afirmam o que o modelo derivado torna possível e o modelo por contador
não: consumo do irmão visível na linha (`busy_elsewhere`), membro legado publicado
em vez de descartado (`untagged`), e o fan-out reescrevendo o snapshot de pools que
o evento de roteamento não tocou.

Teste de INTEGRAÇÃO: precisa de um Redis real (o recompute é Lua, roda no servidor).
    REDIS_URL=redis://redis:6379 pytest test_shared_capacity_snapshot.py
"""
from __future__ import annotations

import json
import os
import uuid

import pytest
import redis.asyncio as aioredis

from plughub_routing.models import AgentInstance
from plughub_routing.registry import (
    InstanceRegistry,
    _instance_sessions_key,
    _pool_instances_key,
    _pool_snapshot_key,
)


# O serviço define `PLUGHUB_REDIS_URL`; ler só `REDIS_URL` faz o teste cair no
# default `localhost`, falhar o ping e PULAR — dentro do próprio container onde o
# Redis está a um hostname de distância. Um skip é um teste que não pode reprovar.
REDIS_URL = (
    os.environ.get("REDIS_URL")
    or os.environ.get("PLUGHUB_REDIS_URL")
    or "redis://localhost:6379"
)

# A linha de base medida: um humano de 3 vagas logado em três pools.
POOLS    = ["retencao_humano", "retencao_humano-int", "aprovacao_deploy"]
CAPACITY = 3


@pytest.fixture
async def env():
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        pytest.skip(f"Redis indisponível em {REDIS_URL} — teste de integração pulado")
    reg      = InstanceRegistry(client)
    tenant   = f"t_cap_{uuid.uuid4().hex[:8]}"
    instance = f"human-{uuid.uuid4().hex[:6]}"
    try:
        yield reg, client, tenant, instance
    finally:
        async for k in client.scan_iter(f"{tenant}:*"):
            await client.delete(k)
        await client.aclose()


async def _login(reg, client, tenant, instance, pools=POOLS, capacity=CAPACITY):
    """Humano logado nos `pools`, UM recurso com `capacity` vagas.

    O SADD no ready_set é manual de propósito: `set_instance` grava a CHAVE da
    instância; a membership é escrita pelo login (mcp-server) e pelo `_upsert_instance`.
    Sem ele o recompute veria pools vazios e o teste passaria por motivo errado.
    """
    await reg.set_instance(AgentInstance(
        instance_id=instance, agent_type_id="human", tenant_id=tenant,
        pools=list(pools), execution_model="stateful",
        max_concurrent=capacity, current_sessions=0, state="ready",
        source="human_login",
    ))
    for pool in pools:
        await client.sadd(_pool_instances_key(tenant, pool), instance)


async def _seed_snapshots(reg, client, tenant, pools=POOLS):
    """Snapshot pré-existente em cada pool — é o que o bootstrap faz a cada 15 s, e
    o que o fan-out encontra para reescrever."""
    for pool in pools:
        await reg.write_pool_snapshot(
            tenant_id=tenant, pool_id=pool,
            sla_target_ms=480_000, channel_types=["webchat"],
        )


async def _snap(client, tenant, pool) -> dict:
    raw = await client.get(_pool_snapshot_key(tenant, pool))
    assert raw, f"snapshot ausente para {pool} — o teste não tem o que julgar"
    return json.loads(raw)


# ── 1. O defeito medido ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_slot_taken_in_one_pool_is_discounted_in_the_siblings(env):
    """A linha de base de 2026-07-31, reproduzida.

    1 humano `max_concurrent 3` logado em 3 pools, 1 vaga ocupada num deles →
    **cada** linha deve dizer `available 2` (a capacidade é do recurso, e ele já
    gastou uma vaga). Antes da fatia 2: `3/3/3`, e a soma na tela dava 6.
    """
    reg, client, tenant, instance = env
    await _login(reg, client, tenant, instance)

    # Ocupa UMA vaga pelo caminho real: claim atômico (tagged com o pool que serviu)
    # seguido do mark_busy do roteamento.
    occ = await reg.claim_instance(
        tenant, instance, "ses-1", None, CAPACITY, pool_id=POOLS[0],
    )
    assert occ == 1, f"o setup não ocupou a vaga (claim={occ}) — nada a medir"
    assert await reg.instance_session_count(tenant, instance) == 1
    await reg.mark_busy(tenant, POOLS[0], instance, "ses-1")

    await _seed_snapshots(reg, client, tenant)

    for pool in POOLS:
        snap = await _snap(client, tenant, pool)
        assert snap["total_instances"] == CAPACITY, (
            f"{pool}: capacidade do recurso deveria ser {CAPACITY}, veio "
            f"{snap['total_instances']}"
        )
        assert snap["available"] == CAPACITY - 1, (
            f"{pool}: available={snap['available']} ignora a vaga consumida pelo "
            f"pool irmão — a capacidade é do RECURSO, não do pool"
        )


@pytest.mark.asyncio
async def test_the_line_explains_itself(env):
    """`available = total − busy − busy_elsewhere` fecha em CADA linha.

    Sem `busy_elsewhere` a linha do irmão fica aritmeticamente inexplicável
    (`available 2` com `busy 0` e `total 3`) e alguém eventualmente a "conserta" de
    volta para o modelo errado.
    """
    reg, client, tenant, instance = env
    await _login(reg, client, tenant, instance)
    assert await reg.claim_instance(
        tenant, instance, "ses-1", None, CAPACITY, pool_id=POOLS[0]
    ) == 1
    await _seed_snapshots(reg, client, tenant)

    served = await _snap(client, tenant, POOLS[0])
    assert served["busy"] == 1 and served["busy_elsewhere"] == 0

    for pool in POOLS[1:]:
        snap = await _snap(client, tenant, pool)
        assert snap["busy"] == 0, f"{pool} não serviu este contato"
        assert snap["busy_elsewhere"] == 1, (
            f"{pool}: o consumo do irmão sumiu da linha (busy_elsewhere="
            f"{snap['busy_elsewhere']})"
        )

    for pool in POOLS:
        snap = await _snap(client, tenant, pool)
        assert snap["available"] == (
            snap["total_instances"] - snap["busy"] - snap["busy_elsewhere"]
        ), f"{pool}: a aritmética da própria linha não fecha — {snap}"


# ── 2. Untagged: denunciado, nunca descartado ─────────────────────────────────

@pytest.mark.asyncio
async def test_untagged_member_consumes_capacity_and_is_published(env):
    """Membro LEGADO de 2 campos (escrito antes da tag; o SET tem TTL 24 h).

    Conta na ocupação do RECURSO — senão a capacidade apareceria maior do que é —
    e em projeção de pool nenhuma. O snapshot publica `untagged` para que a
    degradação não seja silenciosa: o número deve ir a zero em ≤ 24 h, e
    `untagged` persistente é bug de ESCRITOR, não ruído de migração.
    """
    reg, client, tenant, instance = env
    await _login(reg, client, tenant, instance)
    await client.sadd(_instance_sessions_key(tenant, instance), "ses-legado::conf-legado")
    await _seed_snapshots(reg, client, tenant)

    for pool in POOLS:
        snap = await _snap(client, tenant, pool)
        assert snap["untagged"] == 1, (
            f"{pool}: membro sem tag descartado em silêncio (untagged="
            f"{snap.get('untagged')})"
        )
        assert snap["busy"] == 0, f"{pool}: untagged não pode virar busy de ninguém"
        assert snap["available"] == CAPACITY - 1, (
            f"{pool}: untagged não descontou a capacidade do recurso"
        )


@pytest.mark.asyncio
async def test_wrapup_hold_occupies_and_keeps_its_pool(env):
    """O hold de wrap-up (Phase 2) É ocupação: ele SEGURA a vaga entre o fim do
    contato e o auto-claim. Ignorá-lo devolveria a vaga ao push exatamente na
    janela que o hold existe para fechar. A tag herdada mantém o hold na linha do
    pool que serviu."""
    reg, client, tenant, instance = env
    await _login(reg, client, tenant, instance)
    assert await reg.claim_instance(
        tenant, instance, "ses-1", None, CAPACITY, pool_id=POOLS[0]
    ) == 1
    assert await reg.swap_to_hold(tenant, instance, "ses-1", hold_ttl_s=90) == 1

    await _seed_snapshots(reg, client, tenant)

    served = await _snap(client, tenant, POOLS[0])
    assert served["available"] == CAPACITY - 1, "o hold deixou de segurar a vaga"
    assert served["busy"] == 1, "o hold perdeu a tag do pool que serviu"
    assert (await _snap(client, tenant, POOLS[1]))["busy_elsewhere"] == 1


# ── 3. Fan-out: o irmão é reescrito sem que nada o toque ──────────────────────

@pytest.mark.asyncio
async def test_mark_busy_fans_out_to_the_sibling_pools(env):
    """O gatilho, não só a fórmula.

    `write_pool_snapshot` tinha um único call site (o pool roteado). Mesmo com a
    fórmula certa, a linha do irmão só seria reescrita quando algo o tocasse — que
    é literalmente o defeito relatado. Asserção sobre o CONTEÚDO (o número mudou),
    não sobre a existência da chave.
    """
    reg, client, tenant, instance = env
    await _login(reg, client, tenant, instance)
    await _seed_snapshots(reg, client, tenant)

    before = {p: await _snap(client, tenant, p) for p in POOLS}
    assert all(s["available"] == CAPACITY for s in before.values()), before

    assert await reg.claim_instance(
        tenant, instance, "ses-1", None, CAPACITY, pool_id=POOLS[0]
    ) == 1
    await reg.mark_busy(tenant, POOLS[0], instance, "ses-1")

    for pool in POOLS[1:]:
        snap = await _snap(client, tenant, pool)
        assert snap["available"] == CAPACITY - 1, (
            f"{pool}: snapshot do irmão não foi reescrito pelo mark_busy — "
            f"available segue {snap['available']}"
        )


@pytest.mark.asyncio
async def test_release_restores_capacity_in_every_pool(env):
    """Simétrico: terminado o contato, as três linhas voltam à capacidade cheia.
    Um fan-out que só sabe descontar troca um viés por outro."""
    reg, client, tenant, instance = env
    await _login(reg, client, tenant, instance)
    await _seed_snapshots(reg, client, tenant)

    assert await reg.claim_instance(
        tenant, instance, "ses-1", None, CAPACITY, pool_id=POOLS[0]
    ) == 1
    await reg.mark_busy(tenant, POOLS[0], instance, "ses-1")
    assert (await _snap(client, tenant, POOLS[1]))["available"] == CAPACITY - 1

    await reg.remove_conversation(tenant, instance, "ses-1", fallback_pools=[POOLS[0]])

    assert await reg.instance_session_count(tenant, instance) == 0
    for pool in POOLS:
        snap = await _snap(client, tenant, pool)
        assert snap["available"] == CAPACITY, (
            f"{pool}: a vaga não voltou (available={snap['available']})"
        )
        assert snap["busy"] == 0 and snap["busy_elsewhere"] == 0


# ── 4. O contador morreu ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_per_pool_counter_is_written_anymore(env):
    """`{t}:pool:{p}:active_count` não pode ter escritor.

    Deixá-lo sendo escrito sem entrar na conta é pior que removê-lo: convida a que
    volte a ser lido. Este teste percorre o ciclo inteiro (claim → mark_busy →
    remove) e exige que a chave nunca apareça.
    """
    reg, client, tenant, instance = env
    await _login(reg, client, tenant, instance)
    await _seed_snapshots(reg, client, tenant)

    await reg.claim_instance(tenant, instance, "ses-1", None, CAPACITY, pool_id=POOLS[0])
    await reg.mark_busy(tenant, POOLS[0], instance, "ses-1")
    await reg.release_session_from_pool(tenant, "ses-1", new_pool_id=POOLS[1])
    await reg.remove_conversation(tenant, instance, "ses-1", fallback_pools=[POOLS[0]])

    for pool in POOLS:
        assert not await client.exists(f"{tenant}:pool:{pool}:active_count"), (
            f"{pool}: alguém ainda escreve o contador por pool"
        )


@pytest.mark.asyncio
async def test_leaked_occupant_shrinks_available_instead_of_hiding(env):
    """Vaga órfã (sessão que morreu sem `agent_done`) agora APARECE.

    Com o contador por pool ela ficava invisível: `active_count` voltava a 0 e o
    snapshot anunciava capacidade cheia enquanto o semáforo seguia lotado — o
    agente parava de receber contato sem que número nenhum mudasse. Derivando do
    SET, a mentira sai da alocação e entra na tela, que é onde dá para vê-la.
    """
    reg, client, tenant, instance = env
    await _login(reg, client, tenant, instance)
    # Três vagas ocupadas, nenhuma liberada — o estado que o reap procura.
    for i in range(CAPACITY):
        assert await reg.claim_instance(
            tenant, instance, f"ses-{i}", None, CAPACITY, pool_id=POOLS[0]
        ) == i + 1
    await _seed_snapshots(reg, client, tenant)

    for pool in POOLS:
        snap = await _snap(client, tenant, pool)
        assert snap["available"] == 0, (
            f"{pool}: recurso lotado ainda anuncia available={snap['available']}"
        )
