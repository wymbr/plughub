"""
test_pull_release_snapshot.py — F3a: a liberação do PULL também recomputa o snapshot.
**+ lacuna 2 (2026-08-03): a vaga volta mesmo quando a lease já expirou** (testes 3).

**O buraco.** A fatia 2 pôs o recompute + fan-out nos gatilhos de PUSH (`route`,
`mark_busy`, `remove_conversation`, `release_session_from_pool`). O pull entrou pela
metade: `work_task_claim` recompõe de carona no `mark_busy` que ele já chamava, mas
`work_task_release` e `work_task_expire` **liberam a vaga do recurso e não avisam
ninguém**. Entre a liberação e o próximo evento que toque qualquer pool do recurso, o
snapshot afirma uma vaga consumida que já voltou — e afirma isso em TODAS as linhas do
recurso, porque a capacidade é compartilhada.

O sintoma não é uma chave ausente (que se lê como "não sei"): é um número **plausível**
e errado, publicado no mesmo registro que o `system_availability_check` usa para decidir
oferta de canal ao cliente. Por isso a asserção é sobre o CONTEÚDO do snapshot depois da
liberação, nunca sobre a existência da chave.

**Por que estes testes podem reprovar.** Contra o código anterior à F3a eles reprovam
por construção: nada reescreve o snapshot depois de `work_task_release`/`work_task_expire`,
então `available` fica preso no valor de quando a vaga estava tomada. O primeiro teste
mede exatamente isso e o terceiro confirma que o irmão (que o release nem menciona) volta
junto — o fan-out é sobre os pools do RECURSO, não sobre o pool do item.

Teste de INTEGRAÇÃO: precisa de um Redis real (o recompute é Lua, roda no servidor).
    REDIS_URL=redis://redis:6379 pytest test_pull_release_snapshot.py
"""
from __future__ import annotations

import json
import os
import uuid

import pytest
import redis.asyncio as aioredis

from plughub_routing.models import AgentInstance
from plughub_routing.registry import InstanceRegistry, _pool_instances_key, _pool_snapshot_key
from plughub_routing.router import Router


# O serviço define `PLUGHUB_REDIS_URL`; ler só `REDIS_URL` faz o teste cair no default
# `localhost`, falhar o ping e PULAR — dentro do próprio container onde o Redis está a
# um hostname de distância. Um skip é um teste que não pode reprovar.
REDIS_URL = (
    os.environ.get("REDIS_URL")
    or os.environ.get("PLUGHUB_REDIS_URL")
    or "redis://localhost:6379"
)

# Um recurso humano de 2 vagas, logado em dois pools — o mínimo que distingue
# "recomputou o pool do item" de "recomputou os pools do RECURSO".
POOLS    = ["pull_ramal", "pull_ramal-int"]
CAPACITY = 2


class _FakeProducer:
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
    router   = Router(reg, pool_registry=None, kafka_producer=_FakeProducer())
    tenant   = f"t_f3a_{uuid.uuid4().hex[:8]}"
    instance = f"human-{uuid.uuid4().hex[:6]}"
    try:
        yield reg, router, client, tenant, instance
    finally:
        async for k in client.scan_iter(f"{tenant}:*"):
            await client.delete(k)
        await client.aclose()


async def _login(reg, client, tenant, instance):
    await reg.set_instance(AgentInstance(
        instance_id=instance, agent_type_id="human", tenant_id=tenant,
        pools=list(POOLS), execution_model="stateful",
        max_concurrent=CAPACITY, current_sessions=0, state="ready",
        source="human_login",
    ))
    for pool in POOLS:
        await client.sadd(_pool_instances_key(tenant, pool), instance)


async def _seed_snapshots(reg, tenant):
    """O que o bootstrap faz a cada 15 s — e o que o fan-out encontra para reescrever."""
    for pool in POOLS:
        await reg.write_pool_snapshot(
            tenant_id=tenant, pool_id=pool,
            sla_target_ms=480_000, channel_types=["webchat"],
        )


async def _snap(client, tenant, pool) -> dict:
    raw = await client.get(_pool_snapshot_key(tenant, pool))
    assert raw, f"snapshot ausente para {pool} — o teste não tem o que julgar"
    return json.loads(raw)


async def _queue(reg, tenant, pool, sid, **extra):
    contact = {"session_id": sid, "tenant_id": tenant, "channel": "webchat"}
    contact.update(extra)
    await reg.add_queued_contact(tenant, pool, sid, contact, queued_at_ms=1)


async def _claimed(reg, router, client, tenant, instance, sid):
    """Setup comum: logado, snapshots semeados, um item da fila reivindicado.

    A asserção intermediária existe para que uma falha de SETUP (nada foi ocupado)
    não se disfarce de sucesso do teste — sem ela, "voltou à capacidade cheia"
    passaria com o claim tendo falhado.
    """
    await _login(reg, client, tenant, instance)
    await _seed_snapshots(reg, tenant)
    await _queue(reg, tenant, POOLS[0], sid)

    res = await router.work_task_claim(tenant, POOLS[0], sid, instance)
    assert res["claimed"] is True, f"setup não reivindicou o item: {res}"
    for pool in POOLS:
        assert (await _snap(client, tenant, pool))["available"] == CAPACITY - 1, (
            f"{pool}: o claim não desceu a capacidade — nada a medir na liberação"
        )


# ── 1. release: a vaga volta, e o snapshot conta ──────────────────────────────

@pytest.mark.asyncio
async def test_release_rewrites_the_snapshot_of_every_pool_of_the_resource(env):
    """`work_task_release` devolve a vaga do recurso — e HOJE não avisa ninguém.

    Depois da liberação o semáforo já está certo (`instance_session_count == 0`) e o
    snapshot segue dizendo `available = CAPACITY − 1`. A distância entre os dois é o
    defeito: o dado certo existe, e o registro que a plataforma publica não o reflete.
    """
    reg, router, client, tenant, instance = env
    sid = "ses-rel"
    await _claimed(reg, router, client, tenant, instance, sid)

    await router.work_task_release(tenant, POOLS[0], sid, instance)

    assert await reg.instance_session_count(tenant, instance) == 0, (
        "a vaga não foi devolvida no semáforo — o defeito medido aqui seria outro"
    )
    for pool in POOLS:
        snap = await _snap(client, tenant, pool)
        assert snap["available"] == CAPACITY, (
            f"{pool}: snapshot NÃO foi recomputado após work_task_release — "
            f"available={snap['available']} enquanto o semáforo já está livre"
        )
        assert snap["busy"] == 0 and snap["busy_elsewhere"] == 0, (
            f"{pool}: ocupação fantasma sobrevivendo à liberação — {snap}"
        )


# NÃO existe aqui um teste de `queue_length` após o requeue, e a ausência é a
# conclusão — não um esquecimento.
#
# Havia um (`test_release_snapshot_shows_the_requeued_item`): afirmava que a linha
# passa a mostrar `queue_length 1` depois do `work_task_release`. Ele passava, e
# **continuou passando com o refresh da F3a desligado** (mutação M1 de
# `infra/test/mutation_occupancy_peak.sh`) — porque `add_queued_contact` já faz um
# PATCH in-place de `queue_length` no snapshot (registry.py, "Patch queue_length in
# the pool snapshot in-place"). O campo tem dois escritores, e o segundo o corrige em
# qualquer ordem: refresh-antes-do-requeue grava 0 e o patch põe 1 logo depois.
#
# Ou seja: o teste não conseguia reprovar pela ausência da peça que dizia cobrir, e a
# justificativa de ordenação que ele "provava" era falsa (as duas ordens convergem).
# Substituí-lo por outro sobre o mesmo campo repetiria o erro; deletá-lo e registrar o
# porquê é o resultado honesto. A capacidade — que só o recompute produz — segue
# coberta pelos três testes restantes.


# ── 2. expire: mesmo caminho, outro gatilho ───────────────────────────────────

@pytest.mark.asyncio
async def test_expire_of_a_claimed_item_rewrites_the_snapshot(env):
    """`work_task_expire` de item JÁ REIVINDICADO devolve a vaga (só quando há lease).

    Gatilho diferente do release (prazo vencido / supervisor), mesma consequência para
    a capacidade — e a mesma omissão hoje.
    """
    reg, router, client, tenant, instance = env
    sid = "ses-exp"
    await _claimed(reg, router, client, tenant, instance, sid)

    res = await router.work_task_expire(tenant, POOLS[0], sid, reason="lease_expired")
    assert res["was_claimed"] is True, f"setup: expire não viu a lease do claim ({res})"

    assert await reg.instance_session_count(tenant, instance) == 0
    for pool in POOLS:
        snap = await _snap(client, tenant, pool)
        assert snap["available"] == CAPACITY, (
            f"{pool}: snapshot NÃO foi recomputado após work_task_expire — "
            f"available={snap['available']}"
        )


@pytest.mark.asyncio
async def test_expire_of_a_never_claimed_item_does_not_invent_capacity(env):
    """Item que expira SEM nunca ter sido reivindicado não libera vaga nenhuma.

    O simétrico do teste anterior, e a razão de o release de vaga no `work_task_expire`
    ser condicionado à lease: sem ela não há vaga desta fila para devolver, e recomputar
    "por via das dúvidas" com um instance_id vazio derrubaria o occupant de um contato de
    PUSH alocado na mesma sessão. Aqui a fila encolhe, a capacidade não se mexe.
    """
    reg, router, client, tenant, instance = env
    await _login(reg, client, tenant, instance)
    await _seed_snapshots(reg, tenant)

    # Uma vaga ocupada por PUSH (não é item de pull), e um item parado na fila.
    assert await reg.claim_instance(
        tenant, instance, "ses-push", None, CAPACITY, pool_id=POOLS[0]
    ) == 1
    await reg.mark_busy(tenant, POOLS[0], instance, "ses-push")
    await _queue(reg, tenant, POOLS[0], "ses-orfa")

    res = await router.work_task_expire(tenant, POOLS[0], "ses-orfa", reason="expired")
    assert res["was_claimed"] is False and res["was_queued"] is True, res

    assert await reg.instance_session_count(tenant, instance) == 1, (
        "o expire de item nunca reivindicado derrubou a vaga de um contato de PUSH"
    )
    for pool in POOLS:
        snap = await _snap(client, tenant, pool)
        assert snap["available"] == CAPACITY - 1, (
            f"{pool}: capacidade inventada por um expire que não liberou nada — {snap}"
        )


# ── 3. lacuna 2: a lease morre 480× antes do prazo do item ────────────────────
#
# O teste 2 acima passa porque o expire acontece com a lease VIVA — o que, no
# caminho real, quase nunca é o caso: a lease dura 180 s e o expire dispara no
# `timeout_hours` do delegate (24 h no wrap-up default). Os dois testes abaixo
# cobrem o cenário que de fato motiva o `work_task_expire`, e que até 2026-08-03
# não tinha teste nenhum — nem aqui, nem em `test_work_queue_claim.py`, que grava a
# lease e testa o delete mas nunca avança o relógio além do TTL.


@pytest.mark.asyncio
async def test_expire_returns_the_slot_even_after_the_lease_expired(env):
    """Claim abandonado: a lease expirou, e a VAGA tem de voltar assim mesmo.

    **Por que este teste reprova contra o código anterior.** O `work_task_expire`
    derivava o dono exclusivamente da `claim_lease` — a chave que, neste cenário,
    já expirou. `instance_id` saía vazio, `release_instance` não era chamado e a
    vaga ficava presa até o SET inteiro expirar. Contra aquele código este teste
    falha em três lugares: `available` fica em `CAPACITY − 1`, o semáforo segue com
    1 ocupante, e `was_claimed` volta `False` — indistinguível de "nunca
    reivindicado", que é precisamente a distinção que o estado `orphaned` do
    relatório de pendências existe para preservar.

    Apagar a lease é a simulação FIEL da expiração: o TTL do Redis faz exatamente
    isto, e nada mais (não há reaper que reaja a ela — a lacuna 2).

    O que este teste NÃO cobre: a janela entre os 180 s e o prazo do item, em que o
    trabalho fica invisível a todos os agentes. Essa é a lacuna 2 propriamente
    dita e segue aberta — aqui só se conserta a vaga.
    """
    reg, router, client, tenant, instance = env
    sid = "ses-abandonada"
    await _claimed(reg, router, client, tenant, instance, sid)

    # A lease expira (TTL 180 s); ninguém reage — nem reaper, nem heartbeat.
    await reg.delete_claim_lease(tenant, POOLS[0], sid)
    assert await reg.read_claim_lease(tenant, POOLS[0], sid) is None, (
        "setup: a lease ainda existe — este teste mediria o caminho do teste 2"
    )
    assert await reg.instance_session_count(tenant, instance) == 1, (
        "setup: a vaga não está ocupada — não há o que devolver, nada a medir"
    )

    res = await router.work_task_expire(tenant, POOLS[0], sid, reason="acw_expired")

    assert res["was_claimed"] is True, (
        f"expire não reconheceu um item que FOI reivindicado — reportá-lo como "
        f"nunca-reivindicado apaga a evidência da lacuna 2 ({res})"
    )
    assert res["claimed_via"] == "semaphore", (
        f"a vaga deveria ter sido encontrada pelo semáforo, não pela lease ({res})"
    )
    assert await reg.instance_session_count(tenant, instance) == 0, (
        "VAGA PRESA: a lease expirou e o expire não devolveu a vaga do recurso — "
        "cada claim abandonado subtrai capacidade do agente permanentemente"
    )
    for pool in POOLS:
        snap = await _snap(client, tenant, pool)
        assert snap["available"] == CAPACITY, (
            f"{pool}: capacidade não voltou após o expire sem lease — {snap}"
        )


@pytest.mark.asyncio
async def test_expire_without_lease_never_takes_a_slot_of_another_pool(env):
    """A busca pelo semáforo discrimina `(sessão, POOL)` — e é isso que a torna segura.

    **Este teste NÃO nasce vermelho, e o motivo importa:** contra o código anterior
    ele passa trivialmente, porque aquele código não devolvia vaga nenhuma sem
    lease. Ele existe como rede do fix, não como prova do defeito — o risco que
    justificava depender da lease era exatamente *"derrubar o occupant de um contato
    alocado na mesma sessão"*, e trocá-lo por uma busca cega seria substituir um
    palpite por outro.

    O que o torna capaz de reprovar é a MUTAÇÃO: se `find_occupant_instance` deixar
    de comparar `occupant_pool(member) == pool_id` (ou passar a aceitar membro
    `untagged`), este teste fica vermelho e o anterior continua verde.
    """
    reg, router, client, tenant, instance = env
    sid = "ses-vizinha"
    await _login(reg, client, tenant, instance)
    await _seed_snapshots(reg, tenant)

    # A vaga desta sessão foi consumida pelo pool IRMÃO, não pelo pool do item.
    assert await reg.claim_instance(
        tenant, instance, sid, None, CAPACITY, pool_id=POOLS[1]
    ) == 1
    await reg.mark_busy(tenant, POOLS[1], instance, sid)
    await _queue(reg, tenant, POOLS[0], sid)

    assert await reg.find_occupant_instance(tenant, POOLS[0], sid) is None, (
        "a busca devolveu uma instância cuja vaga pertence a OUTRO pool"
    )

    res = await router.work_task_expire(tenant, POOLS[0], sid, reason="expired")
    assert res["was_claimed"] is False and res["claimed_via"] is None, res
    assert await reg.instance_session_count(tenant, instance) == 1, (
        "o expire derrubou a vaga que o pool irmão detinha para a mesma sessão"
    )
