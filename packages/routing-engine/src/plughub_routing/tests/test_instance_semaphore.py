"""
test_instance_semaphore.py — arco "Router: alocação atômica" (Fatia A/B).

Valida o par atômico claim_instance/release_instance (semáforo de contagem
por-instância via Lua sobre um SET de occupants). É a primitiva que elimina a
corrida de sobre-alocação do select→mark_busy não-atômico.

Modelo do occupant: "{session_id}::{conference_id}::{pool_id}". Duas conferências da
MESMA sessão (conference_ids distintos) NÃO compartilham vaga. Release é por PREFIXO
de sessão ("{session_id}::"), pois o agent_done só carrega session_id. O pool é
sempre o 3º campo "::" — TAG (projeção), nunca contagem: mudar de pool não muda
quantas vagas a sessão ocupa.

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

from plughub_routing.registry import (
    InstanceRegistry, _instance_sessions_key, occupant_pool,
)


# Dual-read (2026-08-02). Lendo só `REDIS_URL`, este arquivo PULAVA INTEIRO dentro do
# container — o serviço define `PLUGHUB_REDIS_URL`, e o default `localhost:6379` não
# resolve lá. São **24 testes** do semáforo de instância, incluindo os de concorrência,
# de teto e os 10 da tag de pool (fatia 1): a rede do `claim_instance`, que é o código
# de maior consequência da plataforma, nunca chegou a rodar no ambiente onde o serviço
# roda. Medido em 2026-08-02: `171 passed, 35 skipped`, e os 35 eram estes 24 mais os
# 11 de `test_human_instance_identity.py`.
#
# É a MESMA causa catalogada em 2026-07-30 para os 9 testes do claim pull. Ela reapareceu
# porque a correção de lá foi feita arquivo a arquivo, e ninguém varreu os vizinhos —
# `pytest.skip` sai VERDE, então o modo de falha não tem sintoma.
REDIS_URL = (
    os.environ.get("REDIS_URL")
    or os.environ.get("PLUGHUB_REDIS_URL")
    or "redis://localhost:6379"
)


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


# ─────────────────────────────────────────────────────────────────────────────
# Wrap-up unificado Phase 2 — hand-off da vaga (swap_to_hold × claim herdeiro)
#
# Modelo: no close de um contato com wrap-up INLINE seguindo, a vaga da origem é
# TROCADA por um hold ("__wrapup_hold__::{origin}::{expires_at_ms}") em vez de
# liberada; o auto-claim do wrap-up HERDA o hold (swap net 0). A ocupação nunca
# oscila → um push não toma a vaga na janela a max_concurrent=1.
# Spec: docs/product/wrapup-slot-handoff-phase2-spec.md
# ─────────────────────────────────────────────────────────────────────────────

HOLD_PREFIX = "__wrapup_hold__::"


async def _hold_members(client, tenant, instance) -> list[str]:
    return [
        m for m in await client.smembers(_instance_sessions_key(tenant, instance))
        if m.startswith(HOLD_PREFIX)
    ]


@pytest.mark.asyncio
async def test_handoff_happy_path_occupancy_never_oscillates(reg_and_redis):
    """Caminho feliz: origem ocupa 1 → swap_to_hold mantém 1 → wrap-up herda e
    continua 1. Nunca 0 (janela p/ push roubar) nem 2 (sobre-alocação)."""
    reg, client, tenant, instance = reg_and_redis

    assert await reg.claim_instance(tenant, instance, "ses-origin", None, max_concurrent=1) == 1
    assert await reg.swap_to_hold(tenant, instance, "ses-origin", hold_ttl_s=90) == 1
    assert await reg.instance_session_count(tenant, instance) == 1
    assert len(await _hold_members(client, tenant, instance)) == 1

    # o wrap-up é OUTRA sessão; herda o hold (net 0)
    occ = await reg.claim_instance(
        tenant, instance, "ses-wrapup", None, max_concurrent=1, can_inherit_hold=True,
    )
    assert occ == 1
    assert await reg.instance_session_count(tenant, instance) == 1
    assert await _hold_members(client, tenant, instance) == []   # hold consumido

    # e o release do wrap-up devolve a vaga normalmente
    assert await reg.release_instance(tenant, instance, "ses-wrapup") == 0


@pytest.mark.asyncio
async def test_push_never_inherits_a_live_hold(reg_and_redis):
    """Com hold VIVO e max=1, um push (can_inherit_hold=False) recebe -1 — é o
    ponto do arco: o agente não recebe contato novo com wrap-up pendente."""
    reg, client, tenant, instance = reg_and_redis

    assert await reg.claim_instance(tenant, instance, "ses-origin", None, max_concurrent=1) == 1
    await reg.swap_to_hold(tenant, instance, "ses-origin", hold_ttl_s=90)

    assert await reg.claim_instance(tenant, instance, "ses-push", None, max_concurrent=1) == -1
    assert len(await _hold_members(client, tenant, instance)) == 1   # hold intacto


@pytest.mark.asyncio
async def test_expired_hold_is_discarded_by_any_claim(reg_and_redis):
    """Vazamento (wrap-up nunca chega): o hold EXPIRA passivamente — qualquer claim
    (inclusive push) o descarta e a vaga volta. Sem isto o agente ficaria preso até
    o EXPIRE de 24h do SET."""
    reg, client, tenant, instance = reg_and_redis

    assert await reg.claim_instance(tenant, instance, "ses-origin", None, max_concurrent=1) == 1
    # ttl negativo → expires_at_ms já no passado
    await reg.swap_to_hold(tenant, instance, "ses-origin", hold_ttl_s=-1)
    assert len(await _hold_members(client, tenant, instance)) == 1

    assert await reg.claim_instance(tenant, instance, "ses-push", None, max_concurrent=1) == 1
    assert await _hold_members(client, tenant, instance) == []
    assert await reg.instance_session_count(tenant, instance) == 1


@pytest.mark.asyncio
async def test_swap_is_idempotent_on_redelivery(reg_and_redis):
    """agent_done redelivered → swap_to_hold 2× não duplica o hold."""
    reg, client, tenant, instance = reg_and_redis

    await reg.claim_instance(tenant, instance, "ses-origin", None, max_concurrent=1)
    assert await reg.swap_to_hold(tenant, instance, "ses-origin", hold_ttl_s=90) == 1
    assert await reg.swap_to_hold(tenant, instance, "ses-origin", hold_ttl_s=90) == 1
    assert len(await _hold_members(client, tenant, instance)) == 1


@pytest.mark.asyncio
async def test_swap_after_hold_consumed_does_not_resurrect_it(reg_and_redis):
    """O nó da idempotência: o wrap-up JÁ herdou o hold; um redelivery do agent_done
    não pode criar um hold novo (vaga fantasma permanente a max=1)."""
    reg, client, tenant, instance = reg_and_redis

    await reg.claim_instance(tenant, instance, "ses-origin", None, max_concurrent=1)
    await reg.swap_to_hold(tenant, instance, "ses-origin", hold_ttl_s=90)
    await reg.claim_instance(
        tenant, instance, "ses-wrapup", None, max_concurrent=1, can_inherit_hold=True,
    )
    assert await _hold_members(client, tenant, instance) == []

    await reg.swap_to_hold(tenant, instance, "ses-origin", hold_ttl_s=90)   # redelivery
    assert await _hold_members(client, tenant, instance) == []
    assert await reg.instance_session_count(tenant, instance) == 1          # só o wrap-up


@pytest.mark.asyncio
async def test_inverted_order_claim_before_swap_does_not_corrupt_count(reg_and_redis):
    """Ordem invertida (o item de pull chega ANTES do agent_done): o claim herdeiro
    não encontra hold e cai no claim normal → -1 a max=1 (cai na inbox, degradação
    graciosa). A contagem NÃO é corrompida e o retry após o swap herda."""
    reg, client, tenant, instance = reg_and_redis

    assert await reg.claim_instance(tenant, instance, "ses-origin", None, max_concurrent=1) == 1
    # wrap-up tenta antes do close ser processado
    assert await reg.claim_instance(
        tenant, instance, "ses-wrapup", None, max_concurrent=1, can_inherit_hold=True,
    ) == -1
    assert await reg.instance_session_count(tenant, instance) == 1

    await reg.swap_to_hold(tenant, instance, "ses-origin", hold_ttl_s=90)
    assert await reg.claim_instance(
        tenant, instance, "ses-wrapup", None, max_concurrent=1, can_inherit_hold=True,
    ) == 1
    assert await reg.instance_session_count(tenant, instance) == 1


@pytest.mark.asyncio
async def test_hold_occupies_only_one_slot_when_multi_capacity(reg_and_redis):
    """max=3: o hold ocupa 1 vaga; as outras 2 seguem disponíveis para push."""
    reg, client, tenant, instance = reg_and_redis

    await reg.claim_instance(tenant, instance, "ses-origin", None, max_concurrent=3)
    await reg.swap_to_hold(tenant, instance, "ses-origin", hold_ttl_s=90)

    assert await reg.claim_instance(tenant, instance, "ses-B", None, max_concurrent=3) == 2
    assert await reg.claim_instance(tenant, instance, "ses-C", None, max_concurrent=3) == 3
    assert await reg.claim_instance(tenant, instance, "ses-D", None, max_concurrent=3) == -1
    assert len(await _hold_members(client, tenant, instance)) == 1


@pytest.mark.asyncio
async def test_concurrent_swap_and_claims_never_exceed_capacity(reg_and_redis):
    """swap_to_hold × N claims concorrentes (max=1): ocupação final nunca > 1 e no
    máximo UM herdeiro ganha."""
    reg, client, tenant, instance = reg_and_redis

    await reg.claim_instance(tenant, instance, "ses-origin", None, max_concurrent=1)

    async def _claim(i):
        return await reg.claim_instance(
            tenant, instance, f"ses-w{i}", None, max_concurrent=1, can_inherit_hold=True,
        )

    results = await asyncio.gather(
        reg.swap_to_hold(tenant, instance, "ses-origin", hold_ttl_s=90),
        *[_claim(i) for i in range(10)],
    )
    winners = [r for r in results[1:] if r >= 1]
    assert len(winners) <= 1, f"mais de um herdeiro ganhou: {results}"
    assert await reg.instance_session_count(tenant, instance) <= 1


# ─────────────────────────────────────────────────────────────────────────────
# F1 — tag de pool no membro do semáforo (capacidade compartilhada)
#
# occupant = "{session_id}::{conference_id}::{pool_id}"
# hold     = "__wrapup_hold__::{origin}::{pool_id}::{expires_at_ms}"
# Invariante: o pool é SEMPRE o 3º campo "::" — um parse só (`occupant_pool`), e as
# três restrições preservadas: release por prefixo de sessão, prefixo do hold, e o
# parse numérico da expiração do hold.
#
# A tag é PROJEÇÃO, não contagem: quem serve continua sendo o RECURSO, e a capacidade
# não fragmenta por pool. Spec: docs/product/shared-capacity-pool-as-tag-design.md §1
# ─────────────────────────────────────────────────────────────────────────────

POOL_A = "retencao_humano"
POOL_B = "aprovacao_deploy"


async def _members(client, tenant, instance) -> list[str]:
    return sorted(await client.smembers(_instance_sessions_key(tenant, instance)))


@pytest.mark.asyncio
async def test_claim_grava_a_tag_do_pool_no_occupant(reg_and_redis):
    """O básico: o membro carrega o pool que consumiu a vaga, no 3º campo."""
    reg, client, tenant, instance = reg_and_redis

    assert await reg.claim_instance(
        tenant, instance, "ses-A", "conf-A", max_concurrent=1, pool_id=POOL_A,
    ) == 1
    assert await _members(client, tenant, instance) == [f"ses-A::conf-A::{POOL_A}"]
    assert occupant_pool(f"ses-A::conf-A::{POOL_A}") == POOL_A


@pytest.mark.asyncio
async def test_retag_cross_pool_nao_altera_a_ocupacao(reg_and_redis):
    """O nó da fatia. Transferência cross-pool para uma instância logada nos DOIS
    pools: a mesma (sessão, conferência) é reivindicada por outro pool. Tem de ser
    RE-TAG (SREM+SADD), não vaga nova.

    Vermelho antes da mudança: a idempotência era `SISMEMBER` EXATO, a string do 2º
    claim é diferente, e o membro extra entrava → SCARD 2 (dupla ocupação da mesma
    sessão no mesmo recurso — 1 acima e perfeitamente plausível).

    max_concurrent=2 DE PROPÓSITO: com 1, o código antigo devolveria -1 por teto e o
    teste passaria por ausência de vaga, não por corretude."""
    reg, client, tenant, instance = reg_and_redis

    assert await reg.claim_instance(
        tenant, instance, "ses-A", "conf-A", max_concurrent=2, pool_id=POOL_A,
    ) == 1
    assert await reg.claim_instance(
        tenant, instance, "ses-A", "conf-A", max_concurrent=2, pool_id=POOL_B,
    ) == 1

    assert await reg.instance_session_count(tenant, instance) == 1
    assert await _members(client, tenant, instance) == [f"ses-A::conf-A::{POOL_B}"]

    # e a vaga segue sendo UMA: o release por sessão zera a instância
    assert await reg.release_instance(tenant, instance, "ses-A") == 0


@pytest.mark.asyncio
async def test_reclaim_mesmo_pool_continua_idempotente(reg_and_redis):
    """Redelivery no MESMO pool: nada muda (nem SREM/SADD, nem contagem)."""
    reg, client, tenant, instance = reg_and_redis

    r1 = await reg.claim_instance(
        tenant, instance, "ses-A", "conf-A", max_concurrent=2, pool_id=POOL_A,
    )
    r2 = await reg.claim_instance(
        tenant, instance, "ses-A", "conf-A", max_concurrent=2, pool_id=POOL_A,
    )
    assert r1 == 1 and r2 == 1
    assert await _members(client, tenant, instance) == [f"ses-A::conf-A::{POOL_A}"]


@pytest.mark.asyncio
async def test_confs_distintas_seguem_vagas_distintas_mesmo_com_tag(reg_and_redis):
    """Guarda contra o excesso oposto: o prefixo de idempotência inclui a
    CONFERÊNCIA. Duas confs da mesma sessão continuam ocupando 2 vagas — se o
    prefixo fosse só "{session}::", o fan-out de wrap-up passaria a dividir vaga."""
    reg, client, tenant, instance = reg_and_redis

    assert await reg.claim_instance(
        tenant, instance, "ses-A", "conf-1", max_concurrent=2, pool_id=POOL_A,
    ) == 1
    assert await reg.claim_instance(
        tenant, instance, "ses-A", "conf-2", max_concurrent=2, pool_id=POOL_A,
    ) == 2


@pytest.mark.asyncio
async def test_release_por_prefixo_remove_membro_COM_tag(reg_and_redis):
    """A restrição nº 1 do formato: a tag entra DEPOIS do prefixo de sessão, então o
    release (que só conhece o session_id) segue removendo. Vermelho se alguém puser
    o pool antes — ex. "{pool}::{session}::…"."""
    reg, client, tenant, instance = reg_and_redis

    await reg.claim_instance(
        tenant, instance, "ses-A", "conf-A", max_concurrent=2, pool_id=POOL_A,
    )
    await reg.claim_instance(
        tenant, instance, "ses-B", "conf-B", max_concurrent=2, pool_id=POOL_B,
    )
    # pré-condição: os membros ESTÃO tagados (sem isto o teste passaria pré-mudança)
    assert all(occupant_pool(m) for m in await _members(client, tenant, instance))

    assert await reg.release_instance(tenant, instance, "ses-A") == 1
    assert await _members(client, tenant, instance) == [f"ses-B::conf-B::{POOL_B}"]


@pytest.mark.asyncio
async def test_hold_herda_a_tag_do_occupant_e_expira_com_o_parse_intacto(reg_and_redis):
    """A restrição nº 3: no hold a tag entra ANTES do timestamp, então o único parse
    numérico do Lua (`::(%d+)$`) continua valendo. Duas asserções que podem falhar
    separadamente: (1) o hold herdou o pool do occupant removido — sem parâmetro novo
    atravessando `remove_conversation`; (2) um hold EXPIRADO ainda é descartado por
    qualquer claim (se o parse quebrar, o Lua trata como `exp == nil` → também
    descarta, então a expiração é checada com um hold VIVO logo abaixo)."""
    reg, client, tenant, instance = reg_and_redis

    await reg.claim_instance(
        tenant, instance, "ses-origin", None, max_concurrent=1, pool_id=POOL_A,
    )
    assert await reg.swap_to_hold(tenant, instance, "ses-origin", hold_ttl_s=90) == 1

    holds = await _hold_members(client, tenant, instance)
    assert len(holds) == 1
    assert occupant_pool(holds[0]) == POOL_A, f"hold sem a tag herdada: {holds[0]}"
    assert holds[0].split("::")[-1].isdigit(), f"expiração ilegível: {holds[0]}"

    # hold VIVO com 4 campos: o parse funciona → push é barrado (não é descartado)
    assert await reg.claim_instance(
        tenant, instance, "ses-push", None, max_concurrent=1, pool_id=POOL_B,
    ) == -1
    assert len(await _hold_members(client, tenant, instance)) == 1

    # o herdeiro consome o hold e re-taga a vaga com o pool do wrap-up
    assert await reg.claim_instance(
        tenant, instance, "ses-wrapup", None, max_concurrent=1,
        pool_id=POOL_B, can_inherit_hold=True,
    ) == 1
    assert await _members(client, tenant, instance) == [f"ses-wrapup::::{POOL_B}"]


@pytest.mark.asyncio
async def test_hold_expirado_com_tag_ainda_e_descartado(reg_and_redis):
    """Mesmo cenário do teste Phase 2 de expiração, agora com o membro de 4 campos:
    o hold expirado é varrido e a vaga volta. Vermelho se a tag empurrar o timestamp
    para fora do `::(%d+)$` (o hold ficaria preso até o EXPIRE de 24h do SET)."""
    reg, client, tenant, instance = reg_and_redis

    await reg.claim_instance(
        tenant, instance, "ses-origin", None, max_concurrent=1, pool_id=POOL_A,
    )
    await reg.swap_to_hold(tenant, instance, "ses-origin", hold_ttl_s=-1)
    holds = await _hold_members(client, tenant, instance)
    assert len(holds) == 1 and occupant_pool(holds[0]) == POOL_A

    assert await reg.claim_instance(
        tenant, instance, "ses-push", None, max_concurrent=1, pool_id=POOL_B,
    ) == 1
    assert await _hold_members(client, tenant, instance) == []


# ── Compatibilidade: membros de 2 campos escritos antes do deploy (SET com TTL 24h)

@pytest.mark.asyncio
async def test_membro_legado_de_2_campos_conta_como_untagged(reg_and_redis):
    """Legado ocupa vaga do RECURSO (é o que `available` usa) e não pertence a
    projeção de pool nenhuma: `occupant_pool` devolve None, não "". Quem agregar por
    pool tem de publicar esse resto — untagged persistente é bug de escritor, não
    ruído de migração."""
    reg, client, tenant, instance = reg_and_redis

    key = _instance_sessions_key(tenant, instance)
    await client.sadd(key, "ses-legado::conf-legado")

    assert occupant_pool("ses-legado::conf-legado") is None
    assert occupant_pool("ses-legado::") is None          # sem conferência, sem pool
    assert occupant_pool("ses-A::conf-A::") is None       # 3º campo vazio ≠ pool ""
    # conta na ocupação do recurso
    assert await reg.instance_session_count(tenant, instance) == 1
    assert await reg.claim_instance(
        tenant, instance, "ses-nova", None, max_concurrent=1, pool_id=POOL_A,
    ) == -1


@pytest.mark.asyncio
async def test_reclaim_de_membro_legado_re_taga_sem_duplicar(reg_and_redis):
    """A janela de migração: um redelivery/re-rota da MESMA sessão sobre um membro
    legado não pode virar vaga nova. Vermelho se o Lua só reconhecer o prefixo de 3
    campos — o legado tem 2 e não casa → SCARD 2."""
    reg, client, tenant, instance = reg_and_redis

    key = _instance_sessions_key(tenant, instance)
    await client.sadd(key, "ses-A::conf-A")

    assert await reg.claim_instance(
        tenant, instance, "ses-A", "conf-A", max_concurrent=2, pool_id=POOL_A,
    ) == 1
    assert await _members(client, tenant, instance) == [f"ses-A::conf-A::{POOL_A}"]


@pytest.mark.asyncio
async def test_swap_de_occupant_legado_gera_hold_untagged_com_parse_intacto(reg_and_redis):
    """Origem untagged → hold untagged, mas ainda com 4 campos: o campo de pool fica
    VAZIO em vez de sumir, senão o timestamp escorregaria para o 3º campo e um leitor
    de tag passaria a ler o relógio como nome de pool."""
    reg, client, tenant, instance = reg_and_redis

    key = _instance_sessions_key(tenant, instance)
    await client.sadd(key, "ses-origin::")

    assert await reg.swap_to_hold(tenant, instance, "ses-origin", hold_ttl_s=90) == 1
    holds = await _hold_members(client, tenant, instance)
    assert len(holds) == 1
    assert occupant_pool(holds[0]) is None, f"tag fantasma no hold: {holds[0]}"
    assert holds[0].split("::")[-1].isdigit()
    assert len(holds[0].split("::")) == 4
