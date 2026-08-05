"""
test_claim_possession_record.py — Fase A do ADR
`adr-work-item-requeue-and-agent-affinity.md` (D6: o submit confere posse).

O que está sob teste é UMA propriedade, e ela é temporal: **a posse continua
legível depois que a `claim_lease` vence.** Enquanto a lease era a única fonte,
`work_task_holder` respondia `found=False` na janela em que o submit acontece, e
o check A5 do channel-gateway degradava para permissivo justamente ali — que é o
estado em que um F5 do Console deixa o item (lease apagada, item de volta no
ZSET, formulário ainda na tela do agente).

Cada teste diz o que o faria REPROVAR:
  · `test_claim_writes_durable_record`      → some a escrita do registro no claim
  · `test_record_ttl_follows_item_deadline` → TTL derivado da lease (o bug original)
  · `test_holder_survives_lease_expiry`     → registro não consultado no holder
  · `test_holder_reports_in_queue_*`        → `in_queue` ausente/constante
  · `test_release_clears_record`            → posse sobrevive ao release
  · `test_requeue_clears_record`            → posse sobrevive ao re-parque (o F5)
  · `test_expire_recovers_owner_from_record`→ expire volta a depender da lease

Teste de INTEGRAÇÃO: precisa de um Redis real.
    PLUGHUB_REDIS_URL=redis://redis:6379 pytest test_claim_possession_record.py

O skip por Redis ausente é EXPLÍCITO (nunca verde por ausência de ambiente) e lê
as DUAS variáveis — `REDIS_URL` não existe dentro do container, e ler só ela foi
o que fez 9 testes do claim nunca rodarem lá (§ Postura de Engenharia).
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import redis.asyncio as aioredis

from plughub_routing.registry import (
    InstanceRegistry, _queue_key, _claim_lease_key, _claim_record_key,
)
from plughub_routing.mute_queue import first_queued_key
from plughub_routing.router import Router, _CLAIM_RECORD_FALLBACK_TTL_S
from plughub_routing.models import AgentInstance, ConversationInboundEvent, PoolConfig


REDIS_URL = (
    os.environ.get("REDIS_URL")
    or os.environ.get("PLUGHUB_REDIS_URL")
    or "redis://localhost:6379"
)

CLAIM_LEASE_S = 180   # default de `routing.claim_lease_s`; a régua a bater


class _FakeProducer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def send(self, topic: str, value: dict) -> None:
        self.sent.append((topic, value))


class _PullPoolRegistry:
    """PoolRegistry mínimo cujo único pool é `dispatch_mode="pull"`."""
    def __init__(self, pool: PoolConfig) -> None:
        self._pool = pool

    async def get_pool(self, tenant_id: str, pool_id: str) -> PoolConfig | None:
        return self._pool if pool_id == self._pool.pool_id else None

    async def get_candidate_pools(self, tenant_id: str, channel: str) -> list[PoolConfig]:
        return [self._pool]


@pytest.fixture
async def env():
    client = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        pytest.skip(f"Redis indisponível em {REDIS_URL} — teste de integração pulado")
    reg      = InstanceRegistry(client)
    producer = _FakeProducer()
    tenant   = f"t_poss_{uuid.uuid4().hex[:8]}"
    pool     = "wrapup_detached_ia-int"
    pool_cfg = PoolConfig(
        pool_id=pool, tenant_id=tenant, channel_types=["webhook"],
        sla_target_ms=480_000, dispatch_mode="pull",
    )
    router = Router(reg, pool_registry=_PullPoolRegistry(pool_cfg), kafka_producer=producer)
    try:
        yield reg, router, client, producer, tenant, pool
    finally:
        async for k in client.scan_iter(f"{tenant}:*"):
            await client.delete(k)
        await client.aclose()


async def _register_instance(reg, tenant, pool, instance, max_concurrent=3):
    await reg.set_instance(AgentInstance(
        instance_id=instance, agent_type_id="human", tenant_id=tenant,
        pools=[pool], execution_model="stateful",
        max_concurrent=max_concurrent, current_sessions=0, state="ready",
    ))


async def _queue_contact(reg, tenant, pool, sid, *, deadline: str | None = None,
                         assigned_to: str = "", queued_at_ms: int = 1):
    contact: dict = {
        "session_id": sid, "tenant_id": tenant, "channel": "webhook",
        "pool_id": pool,
        # Produção SEMPRE carimba este campo no enfileiramento
        # (`main.py`: contact_data["queued_at_ms"] = now_ms), e é dele que saem o
        # aging (`score_contact_in_queue`) e o teto de retenção
        # (`_emit_queue_timeout`). O fixture o omitia — divergência do real
        # exatamente no campo que o re-enfileiramento lê, ou seja, no único lugar
        # onde um teste de requeue poderia reprovar. Corrigido em 2026-08-05.
        "queued_at_ms": queued_at_ms,
    }
    if deadline is not None:
        contact["work_item_deadline"] = deadline
    if assigned_to:
        contact["assigned_to"] = assigned_to
    await reg.add_queued_contact(tenant, pool, sid, contact, queued_at_ms=queued_at_ms)


def _iso_in(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


# ── 1. O claim grava o registro durável ───────────────────────────────────────

@pytest.mark.asyncio
async def test_claim_writes_durable_record(env):
    """Claim grava `claim_record` com instância E claimant (user_id, não instance)."""
    reg, router, client, _p, tenant, pool = env
    sid, inst = "ses-rec-1", "human-alice"
    await _register_instance(reg, tenant, pool, inst)
    await _queue_contact(reg, tenant, pool, sid, deadline=_iso_in(24))

    res = await router.work_task_claim(tenant, pool, sid, inst)
    assert res["claimed"] is True

    import json
    raw = await client.get(_claim_record_key(tenant, pool, sid))
    assert raw is not None, "claim NÃO gravou o registro durável de posse"
    rec = json.loads(raw)
    assert rec["instance_id"] == inst
    # `claimant_user_id` é o user, derivado de `human-{userId}` — é ele que casa
    # com `assigned_to`, e é por isso que a instância sozinha não basta.
    assert rec["claimant_user_id"] == "alice"
    assert rec.get("claimed_at")


@pytest.mark.asyncio
async def test_claim_record_uses_explicit_claimant_user_id(env):
    """`claimant_user_id` explícito vence a derivação do instance_id."""
    reg, router, client, _p, tenant, pool = env
    sid, inst = "ses-rec-2", "human-bob"
    await _register_instance(reg, tenant, pool, inst)
    await _queue_contact(reg, tenant, pool, sid, deadline=_iso_in(24))

    await router.work_task_claim(
        tenant, pool, sid, inst, claimant_user_id="bob@corp",
    )
    import json
    rec = json.loads(await client.get(_claim_record_key(tenant, pool, sid)))
    assert rec["claimant_user_id"] == "bob@corp"


# ── 2. TTL — a propriedade load-bearing da Fase A ─────────────────────────────

@pytest.mark.asyncio
async def test_record_ttl_follows_item_deadline(env):
    """
    TTL do registro = o que RESTA do prazo do item, NÃO o da lease.

    Este é o teste que teria pego o defeito original. A asserção decisiva não é
    "o TTL é ~7200"; é que ele é **maior que o da lease** — a razão de a chave
    existir.
    """
    reg, router, client, _p, tenant, pool = env
    sid, inst = "ses-ttl-1", "human-carol"
    await _register_instance(reg, tenant, pool, inst)
    await _queue_contact(reg, tenant, pool, sid, deadline=_iso_in(2))

    await router.work_task_claim(tenant, pool, sid, inst)

    ttl_record = await client.ttl(_claim_record_key(tenant, pool, sid))
    ttl_lease  = await client.ttl(_claim_lease_key(tenant, pool, sid))

    assert ttl_record > CLAIM_LEASE_S, (
        f"registro com TTL de {ttl_record}s não sobrevive à lease ({ttl_lease}s) — "
        "é a lease com outro nome, e o submit volta a falhar aberto"
    )
    # 2 h ± tolerância de execução
    assert 7000 < ttl_record <= 7200, f"TTL não seguiu o prazo do item: {ttl_record}"


@pytest.mark.asyncio
async def test_record_ttl_falls_back_when_deadline_absent(env):
    """Sem `work_item_deadline` → fallback de 25 h (convenção do ledger), não 180 s."""
    reg, router, client, _p, tenant, pool = env
    sid, inst = "ses-ttl-2", "human-dave"
    await _register_instance(reg, tenant, pool, inst)
    await _queue_contact(reg, tenant, pool, sid, deadline=None)

    await router.work_task_claim(tenant, pool, sid, inst)

    ttl = await client.ttl(_claim_record_key(tenant, pool, sid))
    assert ttl > CLAIM_LEASE_S
    assert abs(ttl - _CLAIM_RECORD_FALLBACK_TTL_S) < 60, (
        f"fallback errado: {ttl}s (esperado ~{_CLAIM_RECORD_FALLBACK_TTL_S}s)"
    )


@pytest.mark.asyncio
async def test_record_ttl_floors_on_past_deadline(env):
    """Prazo já vencido → piso de 60 s (não TTL negativo, que o Redis recusaria)."""
    reg, router, client, _p, tenant, pool = env
    sid, inst = "ses-ttl-3", "human-erin"
    await _register_instance(reg, tenant, pool, inst)
    await _queue_contact(reg, tenant, pool, sid, deadline=_iso_in(-3))

    await router.work_task_claim(tenant, pool, sid, inst)
    ttl = await client.ttl(_claim_record_key(tenant, pool, sid))
    assert 0 < ttl <= 60, f"piso do TTL não aplicado: {ttl}"


# ── 3. O holder responde depois da lease vencer ──────────────────────────────

@pytest.mark.asyncio
async def test_holder_survives_lease_expiry(env):
    """
    Lease apagada (= vencida) + registro vivo → holder AINDA responde, via="record".

    Simula a expiração apagando a chave: o que importa é a ausência da lease, não
    o mecanismo de sua remoção. Esperar 180 s para provar isto seria a versão
    temporal de "esperar volume" — o valor já é decidível agora.
    """
    reg, router, client, _p, tenant, pool = env
    sid, inst = "ses-hold-1", "human-frank"
    await _register_instance(reg, tenant, pool, inst)
    await _queue_contact(reg, tenant, pool, sid, deadline=_iso_in(24))
    await router.work_task_claim(tenant, pool, sid, inst)

    holder = await router.work_task_holder(tenant, pool, sid)
    assert holder["found"] is True and holder["via"] == "lease"

    await client.delete(_claim_lease_key(tenant, pool, sid))

    holder = await router.work_task_holder(tenant, pool, sid)
    assert holder["found"] is True, (
        "posse sumiu com a lease — é exatamente o fail-open que a Fase A fecha"
    )
    assert holder["via"] == "record"
    assert holder["instance_id"] == inst
    assert holder["in_queue"] is False, "item reivindicado não pode estar no ZSET"


@pytest.mark.asyncio
async def test_holder_reports_same_claimant_in_both_branches(env):
    """
    `claimant_user_id` NÃO pode depender de qual chave respondeu.

    Lacuna encontrada pelo smoke em 2026-08-04, com 12 pytests verdes: a lease não
    carrega o claimant, e o holder devolvia a chave crua — `null` nos primeiros
    180 s, correto depois. Um consumidor que o compare com `assigned_to` (Fase C)
    falharia aberto exatamente na janela quente.

    Reprova se o holder voltar a devolver a fonte em vez de compor.
    """
    reg, router, client, _p, tenant, pool = env
    sid, inst = "ses-claimant-1", "human-karla"
    await _register_instance(reg, tenant, pool, inst)
    await _queue_contact(reg, tenant, pool, sid, deadline=_iso_in(24))
    await router.work_task_claim(tenant, pool, sid, inst)

    by_lease = await router.work_task_holder(tenant, pool, sid)
    assert by_lease["via"] == "lease"

    await client.delete(_claim_lease_key(tenant, pool, sid))
    by_record = await router.work_task_holder(tenant, pool, sid)
    assert by_record["via"] == "record"

    assert by_lease["claimant_user_id"] == "karla", (
        "claimant ausente no ramo da lease — o holder devolveu a chave, não o fato"
    )
    assert by_lease["claimant_user_id"] == by_record["claimant_user_id"]
    assert by_lease["instance_id"] == by_record["instance_id"]


@pytest.mark.asyncio
async def test_holder_reports_in_queue_when_nobody_holds(env):
    """
    Item enfileirado e nunca reivindicado → `found=False, in_queue=True`.

    É a resposta POSITIVA ("ninguém detém, está na fila") que torna o veredicto
    do submit fechável. Sem `in_queue` este caso é indistinguível de "não sei".
    """
    reg, router, _c, _p, tenant, pool = env
    sid = "ses-hold-2"
    await _queue_contact(reg, tenant, pool, sid, deadline=_iso_in(24))

    holder = await router.work_task_holder(tenant, pool, sid)
    assert holder["found"] is False
    assert holder["in_queue"] is True
    assert holder["via"] == "none"


@pytest.mark.asyncio
async def test_holder_unknown_when_no_claim_and_no_queue(env):
    """
    Sessão que nunca passou pela fila → `found=False, in_queue=False`.

    Ausência honesta (pool push, item encerrado, claim pré-Fase A) — e o ÚNICO
    caso em que o chamador deve degradar para permissivo.
    """
    _reg, router, _c, _p, tenant, pool = env
    holder = await router.work_task_holder(tenant, pool, "ses-inexistente")
    assert holder == {"found": False, "via": "none", "in_queue": False}


# ── 4. Todo caminho que tira a posse apaga o registro ────────────────────────

@pytest.mark.asyncio
async def test_release_clears_record(env):
    """Release devolve o item → posse acaba, e o registro com ela."""
    reg, router, client, _p, tenant, pool = env
    sid, inst = "ses-rel-1", "human-gil"
    await _register_instance(reg, tenant, pool, inst)
    await _queue_contact(reg, tenant, pool, sid, deadline=_iso_in(24))
    await router.work_task_claim(tenant, pool, sid, inst)
    assert await client.get(_claim_record_key(tenant, pool, sid)) is not None

    await router.work_task_release(tenant, pool, sid, inst)

    assert await client.get(_claim_record_key(tenant, pool, sid)) is None, (
        "registro sobreviveu ao release — devolveria posse a quem devolveu o item"
    )
    holder = await router.work_task_holder(tenant, pool, sid)
    assert holder["found"] is False and holder["in_queue"] is True


@pytest.mark.asyncio
async def test_release_preserves_first_queued(env):
    """
    Fase B / D2 — `first_queued` sobrevive à devolução pelo `work_task_release`.

    O ADR afirma que *"`first_queued_ms` … hoje NÃO é escrito no caminho de
    re-enqueue"* e o coloca como trabalho da Fase B. A leitura de código diz o
    contrário: não é campo do JSON do contato — é a chave própria
    `{t}:queue:first_queued:{sid}`, que `add_queued_contact` escreve com **NX**
    (registry.py) e que o `listQueue` do inbox lê de lá. NX ⇒ o re-enfileiramento
    não a sobrescreve.

    Tentei conferir isso no espécime sujo deixado pela sessão de 2026-08-04 e a
    medição não serviu: o item já não existia (`TTL -2`, ledger `nil`). **Ausência
    num item morto não é evidência sobre o caminho de escrita** — é o mesmo erro
    de ler campo ausente como afirmação. Este teste responde num item VIVO, e
    responde para sempre, porque vira gate.

    Reprova se alguém trocar o `nx=True` por escrita incondicional (a idade do
    item na inbox passaria a reiniciar a cada devolução, escondendo espera real).
    """
    reg, router, client, _p, tenant, pool = env
    sid, inst = "ses-fq-1", "human-lia"
    await _register_instance(reg, tenant, pool, inst)
    await _queue_contact(reg, tenant, pool, sid, deadline=_iso_in(24))

    fq_key = first_queued_key(tenant, sid)
    fq_first = await client.get(fq_key)
    assert fq_first is not None, (
        "first_queued não foi escrito no PRIMEIRO enfileiramento — a premissa do "
        "inbox (idade real do item) não se sustenta em caminho nenhum"
    )

    await router.work_task_claim(tenant, pool, sid, inst)
    await asyncio.sleep(0.01)
    await router.work_task_release(tenant, pool, sid, inst)

    fq_after = await client.get(fq_key)
    assert fq_after == fq_first, (
        f"first_queued foi reescrito na devolução ({fq_first} → {fq_after}) — a "
        "espera real do item reiniciaria a cada queda de transporte"
    )


# ── Espera preservada na devolução (achado 4, 2026-08-05) ────────────────────

@pytest.mark.asyncio
async def test_release_preserves_zset_score(env):
    """
    Regra de produto: *item devolvido à fila preserva o timestamp original, logo é
    ordenado pela espera*. O `work_task_release` carimbava `now` no score do ZSET.

    **O efeito não era o que o achado dizia.** A medição de 2026-08-05 mostrou DUAS
    fontes de tempo, porque `add_queued_contact` grava o `contact_data` verbatim:
    o JSON `queued_at_ms` sobrevivia (e é dele que saem o aging e o
    `max_wait_exceeded`), enquanto o score do ZSET reiniciava — e é o score que a
    posição publicada ao cliente (`get_queue_rank`) e a urgência de SLA do pool
    (`get_oldest_queue_wait_ms`) leem. Quem decidia o atendimento estava certo;
    quem o cliente via, não.

    Reprova se o score voltar a ser `now`: a posição do contato devolvido salta
    para o fim da fila enquanto a idade exibida no inbox continua correta — os dois
    números na mesma tela, discordando, que foi como o achado apareceu.

    O `sleep` existe para que `now` seja mensuravelmente diferente do carimbo: sem
    ele, um relógio grosseiro deixaria o teste passar com o bug presente.
    """
    reg, router, client, _p, tenant, pool = env
    sid, inst = "ses-score-1", "human-ana"
    ORIGINAL_MS = 1_700_000_000_000          # bem no passado, impossível de confundir com `now`
    await _register_instance(reg, tenant, pool, inst)
    await _queue_contact(
        reg, tenant, pool, sid, deadline=_iso_in(24), queued_at_ms=ORIGINAL_MS
    )
    before = await client.zscore(_queue_key(tenant, pool), sid)
    assert before == float(ORIGINAL_MS), "premissa do teste falhou no enfileiramento"

    await router.work_task_claim(tenant, pool, sid, inst)
    await asyncio.sleep(0.01)
    await router.work_task_release(tenant, pool, sid, inst)

    after = await client.zscore(_queue_key(tenant, pool), sid)
    assert after == float(ORIGINAL_MS), (
        f"score do ZSET foi recarimbado na devolução ({before} → {after}) — a "
        "posição e a urgência de SLA reiniciam, contra a espera real do item"
    )


@pytest.mark.asyncio
async def test_release_without_queued_at_falls_back_to_now(env):
    """
    Contato SEM `queued_at_ms` (legado, ou JSON montado por outro caminho) não tem
    espera a preservar. O fallback é `now` — e é logado como WARNING, porque aí o
    item realmente vai para o fim e o sintoma seria inexplicável sem a linha.

    Este teste existe para o fallback não virar silencioso por acidente: se alguém
    trocar o `or now` por `or 0`, o item iria para o TOPO absoluto da fila
    (score 0), furando fila para sempre — falha grave e totalmente muda.
    """
    reg, router, client, _p, tenant, pool = env
    sid, inst = "ses-score-2", "human-bia"
    await _register_instance(reg, tenant, pool, inst)
    # Enfileirado à mão, SEM o campo — o helper agora sempre o inclui.
    await reg.add_queued_contact(
        tenant, pool, sid,
        {"session_id": sid, "tenant_id": tenant, "channel": "webhook",
         "pool_id": pool, "work_item_deadline": _iso_in(24)},
        queued_at_ms=1,
    )
    await router.work_task_claim(tenant, pool, sid, inst)
    floor_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    await router.work_task_release(tenant, pool, sid, inst)

    after = await client.zscore(_queue_key(tenant, pool), sid)
    assert after >= float(floor_ms), (
        f"sem `queued_at_ms` o score deveria cair em `now` (>= {floor_ms}); veio "
        f"{after} — score baixo aqui significa furar fila, não perdê-la"
    )


# ── Fase C (D3) — reserva por queda de transporte ────────────────────────────

@pytest.mark.asyncio
async def test_drop_release_reserves_to_previous_owner(env):
    """
    Release de QUEDA devolve o item reservado ao dono anterior, com transbordo.

    O dono sai do REGISTRO de posse (`claimant_user_id`), não da lease nem do
    instance_id: `assigned_to` casa por USER, e é assim que o gate da Camada B
    dentro do `work_task_claim` o compara.
    """
    reg, router, client, _p, tenant, pool = env
    sid, inst = "ses-resv-1", "human-marta"
    await _register_instance(reg, tenant, pool, inst)
    await _queue_contact(reg, tenant, pool, sid, deadline=_iso_in(24))
    await router.work_task_claim(tenant, pool, sid, inst)

    res = await router.work_task_release(
        tenant, pool, sid, inst, reserve_to_previous=True,
    )
    assert res["reserved_to"] == "marta"

    import json
    contact = json.loads(await client.get(f"{tenant}:queue_contact:{sid}"))
    assert contact["assigned_to"] == "marta"
    # O pool do fixture termina em `-int` ⇒ AUTHOR-BOUND ⇒ reserva PERMANENTE.
    # `fallback_to_pool_after_s` ausente é como a Camada B codifica "não
    # transborda". Um número aqui — qualquer número — seria o erro de categoria
    # que a migration 20260730000000_pool_internal_queue nomeia: entregar a outro
    # agente a classificação de um atendimento que ele não fez.
    assert "fallback_to_pool_after_s" not in contact, (
        f"fila interna ganhou janela de transbordo: "
        f"{contact.get('fallback_to_pool_after_s')}"
    )
    assert contact["assigned_at_ms"], "âncora da reserva não foi carimbada"


@pytest.mark.asyncio
async def test_deliberate_release_does_not_reserve(env):
    """
    O botão "Return to queue" NÃO reserva — e este é o default.

    É a distinção que a Fase C existe para preservar: desistência deliberada
    reservada ao desistente esconderia do pool inteiro, pela janela, um item que
    o agente acabou de largar de propósito. Reprova se o default virar `True`.
    """
    reg, router, client, _p, tenant, pool = env
    sid, inst = "ses-resv-2", "human-nuno"
    await _register_instance(reg, tenant, pool, inst)
    await _queue_contact(reg, tenant, pool, sid, deadline=_iso_in(24))
    await router.work_task_claim(tenant, pool, sid, inst)

    res = await router.work_task_release(tenant, pool, sid, inst)   # sem o flag
    assert res["reserved_to"] is None

    import json
    contact = json.loads(await client.get(f"{tenant}:queue_contact:{sid}"))
    assert not contact.get("assigned_to"), (
        "desistência deliberada reservou o item ao desistente — ele ficaria "
        "invisível ao resto do pool durante a janela"
    )


@pytest.mark.asyncio
async def test_drop_release_never_overwrites_author_binding(env):
    """
    Item já reservado ao AUTOR mantém o vínculo, mesmo que outro agente o tenha
    reivindicado por transbordo e caído.

    Sobrescrever transferiria silenciosamente o vínculo autoral (quem atendeu o
    contato) para um claimante de transbordo — fato mais fraco e mais volátil.
    """
    reg, router, client, _p, tenant, pool = env
    sid = "ses-resv-3"
    autor, outro = "autora", "human-outro"
    await _register_instance(reg, tenant, pool, outro)
    # Reserva autoral já vencida (transbordo liberado) → `outro` consegue claimar.
    await _queue_contact(
        reg, tenant, pool, sid, deadline=_iso_in(24), assigned_to=autor,
    )
    import json
    c = json.loads(await client.get(f"{tenant}:queue_contact:{sid}"))
    c["fallback_to_pool_after_s"] = 0          # transbordo imediato
    c["assigned_at_ms"] = int(datetime.now(timezone.utc).timestamp() * 1000) - 60_000
    await client.set(f"{tenant}:queue_contact:{sid}", json.dumps(c))

    claim = await router.work_task_claim(tenant, pool, sid, outro)
    assert claim["claimed"] is True, f"transbordo não liberou o claim: {claim}"

    res = await router.work_task_release(
        tenant, pool, sid, outro, reserve_to_previous=True,
    )
    assert res["reserved_to"] is None, "reserva de queda sobrepôs o vínculo autoral"

    contact = json.loads(await client.get(f"{tenant}:queue_contact:{sid}"))
    assert contact["assigned_to"] == autor


@pytest.mark.asyncio
async def test_internal_queue_never_overflows(env):
    """
    `-int` NÃO tem janela — tem ausência de janela.

    Não é "generosa": é categórica. Wrap-up é author-bound, e transbordá-lo
    entregaria a outro agente a classificação de um atendimento que ele não fez —
    o mesmo "fingir ser o autor" que a ADR author-bound recusou ao supervisor.
    Fila POOLED (aprovação, demos) é o caso oposto, e ali a janela existe e é curta.

    Reprova se alguém devolver um número para `-int` — inclusive um número grande,
    que seria a mesma tarefa deixando de ser do autor, só que mais tarde.
    """
    _reg, router, _c, _p, _tenant, pool = env
    assert pool.endswith("-int")
    assert router._drop_reserve_window_s(pool) is None
    assert router._drop_reserve_window_s("formfill_demo") == 30


@pytest.mark.asyncio
async def test_drop_reserve_derives_owner_when_record_is_gone(env):
    """
    Sem registro de posse (expirou), o dono é DEDUZIDO do `instance_id`.

    Fallback honesto — mas o log avisa que deduziu. `human-{userId}` é a mesma
    convenção que o `work_task_claim` usa para derivar o claimant.
    """
    reg, router, client, _p, tenant, pool = env
    sid, inst = "ses-resv-4", "human-olga"
    await _register_instance(reg, tenant, pool, inst)
    await _queue_contact(reg, tenant, pool, sid, deadline=_iso_in(24))
    await router.work_task_claim(tenant, pool, sid, inst)
    await client.delete(_claim_record_key(tenant, pool, sid))

    res = await router.work_task_release(
        tenant, pool, sid, inst, reserve_to_previous=True,
    )
    assert res["reserved_to"] == "olga"


@pytest.mark.asyncio
async def test_requeue_clears_record(env):
    """
    O F5: item reivindicado, contato re-roteado ao pool pull (`route()`) → o
    re-parque apaga lease E registro, e o item volta ao ZSET sem dono.

    Este é o cenário do ADR § 1 medido em `formfill_demo`. Se o registro
    sobrevivesse aqui, a aba velha continuaria provando posse sobre um item já
    disponível a outro agente — a Fase A não teria fechado nada.
    """
    reg, router, client, _p, tenant, pool = env
    sid, inst = "ses-f5-1", "human-hana"
    await _register_instance(reg, tenant, pool, inst)
    await _queue_contact(reg, tenant, pool, sid, deadline=_iso_in(24))
    await router.work_task_claim(tenant, pool, sid, inst)
    assert await client.zscore(_queue_key(tenant, pool), sid) is None

    # re-route genérico (main.py:6634) → route() com o pool pull
    await router.route(ConversationInboundEvent(
        session_id=sid, tenant_id=tenant, channel="webhook", pool_id=pool,
        customer_id="sys:test",
        started_at=datetime.now(timezone.utc).isoformat(),
    ))
    await asyncio.sleep(0.1)   # as limpezas do re-parque são create_task

    assert await client.get(_claim_lease_key(tenant, pool, sid)) is None
    assert await client.get(_claim_record_key(tenant, pool, sid)) is None, (
        "registro sobreviveu ao re-parque — a aba velha continuaria com posse"
    )


@pytest.mark.asyncio
async def test_expire_clears_record(env):
    """Expire encerra o item → o registro morre junto (idempotente)."""
    reg, router, client, _p, tenant, pool = env
    sid, inst = "ses-exp-1", "human-ivo"
    await _register_instance(reg, tenant, pool, inst)
    await _queue_contact(reg, tenant, pool, sid, deadline=_iso_in(24))
    await router.work_task_claim(tenant, pool, sid, inst)

    await router.work_task_expire(tenant, pool, sid, reason="supervisor")
    assert await client.get(_claim_record_key(tenant, pool, sid)) is None


@pytest.mark.asyncio
async def test_expire_recovers_owner_from_record(env):
    """
    Lease vencida + registro vivo → o expire acha o dono pelo REGISTRO e devolve a
    vaga (`claimed_via="record"`), sem cair na busca no semáforo.

    É o cenário que motiva o expire (reivindicado, nunca submetido, lease de 180 s
    contra prazo de 24 h) — o mesmo que a fix da lacuna 2 atacou pelo semáforo.
    """
    reg, router, client, _p, tenant, pool = env
    sid, inst = "ses-exp-2", "human-joao"
    await _register_instance(reg, tenant, pool, inst)
    await _queue_contact(reg, tenant, pool, sid, deadline=_iso_in(24))
    await router.work_task_claim(tenant, pool, sid, inst)

    await client.delete(_claim_lease_key(tenant, pool, sid))

    res = await router.work_task_expire(tenant, pool, sid, reason="expired")
    assert res.get("claimed_via") == "record", (
        f"expire não usou o registro: via={res.get('claimed_via')!r}"
    )
    assert res.get("instance_id") == inst


# ── Item 6 (2026-08-05) — a janela de candidatos lê a ponta certa do ZSET ─────
#
# `get_queued_contacts` lia com ZREVRANGE ("highest priority first"), premissa
# falsa: o score deste ZSET tem escritor único (`add_queued_contact`) e é
# `queued_at_ms`. Numa fila maior que `top_n`, os mais antigos não entravam na
# janela e portanto NUNCA eram pontuados pelo `score_contact_in_queue` — o aging
# e o breach, que existem para que nenhum contato espere para sempre, ficavam
# inertes justamente para quem mais esperava.
#
# Os dois testes abaixo são complementares de propósito: o primeiro reprova o
# SENTIDO da janela, o segundo reprova a AUSÊNCIA de janela. Só o primeiro
# passaria com `zrange(0, -1)` (que carrega a fila inteira e mata o limite de
# trabalho); só o segundo passaria com o ZREVRANGE original.

@pytest.mark.asyncio
async def test_queued_contacts_window_takes_oldest(env):
    """
    Fila MAIOR que a janela → vêm os mais ANTIGOS.

    Reprova se voltar o ZREVRANGE: a asserção nomeia os ids ausentes, então a
    falha diz qual ponta veio, não só que veio errado.

    Os `queued_at_ms` são explícitos e crescentes (`q-00` o mais antigo) — a
    ordem não depende do relógio nem da ordem de inserção, que é o que torna o
    teste determinístico e a mensagem de falha legível.
    """
    reg, _router, _client, _p, tenant, pool = env
    N, WINDOW = 15, 5
    for i in range(N):
        await _queue_contact(
            reg, tenant, pool, f"q-{i:02d}", queued_at_ms=1_700_000_000_000 + i * 1000
        )

    got = [c.session_id for c in await reg.get_queued_contacts(tenant, pool, WINDOW)]

    assert got == [f"q-{i:02d}" for i in range(WINDOW)], (
        f"janela devolveu {got} — esperado os {WINDOW} mais ANTIGOS "
        f"(q-00..q-{WINDOW - 1:02d}). Vindo q-{N - WINDOW:02d}.. significa ZREVRANGE "
        "de volta: os mais velhos nunca são pontuados pelo aging."
    )


@pytest.mark.asyncio
async def test_queued_contacts_window_respects_top_n(env):
    """
    A janela CORTA. Sem este teste, trocar a leitura por "a fila inteira" passaria
    no teste de sentido acima e reintroduziria trabalho ilimitado por drain — o
    limite existe para que `Router.dequeue` seja O(top_n), não O(fila).
    """
    reg, _router, _client, _p, tenant, pool = env
    for i in range(12):
        await _queue_contact(
            reg, tenant, pool, f"w-{i:02d}", queued_at_ms=1_700_000_000_000 + i * 1000
        )

    got = await reg.get_queued_contacts(tenant, pool, 4)
    assert len(got) == 4, f"janela não cortou: {len(got)} contatos para top_n=4"


@pytest.mark.asyncio
async def test_queued_contacts_window_diverges_from_reverse_reading(env):
    """
    CONTROLE NEGATIVO EMBUTIDO — a leitura de produção é comparada, na mesma fila,
    com a leitura ERRADA reconstruída (`zrevrange`), e as duas têm de DIVERGIR.

    Por que existe: o controle negativo "de verdade" (reverter o conserto,
    rebuildar, rodar, restaurar) é frágil por construção — três passos manuais na
    ordem certa com um rebuild no meio. Ele falhou DUAS vezes em 2026-08-05, das
    duas por pular o build, e em ambas o sintoma foi um resultado que *parecia*
    resposta: primeiro `238 deselected` (nenhum teste selecionado, código 0),
    depois `2 passed` contra um container que ainda tinha o código consertado.
    *Um procedimento de validação que depende de sequência manual não é validação
    — é uma chance de erro com aparência de rigor.*

    O que este teste garante e os dois acima não garantem: que a FIXTURE
    discrimina. Se alguém encolher N para dentro da janela, as duas leituras
    passam a coincidir, os testes de sentido continuam verdes e param de testar
    qualquer coisa — silenciosamente. Aqui isso fica vermelho, e a mensagem diz
    exatamente o que aconteceu.

    Reprova em três situações, todas úteis:
      · a produção volta ao ZREVRANGE      → os conjuntos coincidem
      · a fila deixa de exceder a janela   → os conjuntos coincidem
      · a produção passa a ler outra coisa → nenhum dos dois conjuntos bate
    """
    reg, _router, client, _p, tenant, pool = env
    N, WINDOW = 15, 5
    for i in range(N):
        await _queue_contact(
            reg, tenant, pool, f"d-{i:02d}", queued_at_ms=1_700_000_000_000 + i * 1000
        )

    prod = [c.session_id for c in await reg.get_queued_contacts(tenant, pool, WINDOW)]
    # A leitura ERRADA, reconstruída sobre a MESMA fila. Não é o código antigo
    # importado (ele não existe mais) — é a operação Redis que ele fazia.
    reverse = await client.zrevrange(_queue_key(tenant, pool), 0, WINDOW - 1)

    assert prod != reverse, (
        f"as duas leituras coincidiram ({prod}) — ou a produção voltou a ler a "
        f"ponta errada, ou a fila (N={N}) não excede mais a janela ({WINDOW}) e "
        "esta suíte parou de discriminar as duas semânticas"
    )
    assert prod == [f"d-{i:02d}" for i in range(WINDOW)], (
        f"produção não trouxe os mais ANTIGOS: {prod}"
    )
    assert reverse == [f"d-{i:02d}" for i in range(N - 1, N - 1 - WINDOW, -1)], (
        f"a leitura reversa não trouxe os mais NOVOS: {reverse} — a premissa do "
        "próprio controle mudou, e ele deixou de ser o oposto que diz ser"
    )
