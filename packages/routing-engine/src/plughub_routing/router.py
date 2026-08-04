"""
router.py
Main routing logic — two scenarios with distinct scorers.
Spec: PlugHub v24.0 section 3.3b
"""

from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .models import (
    ConversationInboundEvent,
    ConversationRoutedEvent,
    QueuedContact,
    AgentInstance,
    PoolConfig,
    RoutingResult,
    resolve_agent_type,
)
from .scorer import (
    score_resource,
    score_contact_in_queue,
    instance_has_capacity,
    determine_routing_mode,
    compute_priority_score,
)
from .registry import InstanceRegistry, PoolRegistry
from .config import get_settings
from .routing_config import routing_config

if TYPE_CHECKING:
    from aiokafka import AIOKafkaProducer

logger = logging.getLogger("plughub.routing.router")

# Fase A / D6 — TTL do registro durável de posse quando o item chega SEM
# `work_item_deadline`. 25 h é a mesma convenção do ledger
# `{t}:work_task:{session}` (24 h de prazo + folga), de propósito: as duas chaves
# descrevem a vida do MESMO item, e um registro que morre antes do ledger
# devolveria o fail-open que a Fase A fecha — só que ~20 h depois, quando
# ninguém está mais olhando.
_CLAIM_RECORD_FALLBACK_TTL_S = 25 * 3600

# Fase C (D3) — sufixo reservado das filas internas author-bound. É garantia por
# CONSTRUÇÃO, não convenção: o agent-registry rejeita criação manual de pool com
# este sufixo (ADR internal-work-queue, D6). Mesmo discriminador que o relatório
# de pendências usa em `mcp-server-plughub/lib/work-queue.ts`.
_INTERNAL_POOL_SUFFIX = "-int"


class Router:
    def __init__(
        self,
        instance_registry: InstanceRegistry,
        pool_registry:     PoolRegistry,
        local_site:        str = "site_local",
        kafka_producer:    "AIOKafkaProducer | None" = None,
    ) -> None:
        self._instances       = instance_registry
        self._pools           = pool_registry
        self._local_site      = local_site
        self._settings        = get_settings()
        # Usado para conversations.routed (work_task_claim). O queue.position_updated
        # saiu daqui: é publicado em main.py, pós-enqueue (ver CHANGELOG).
        self._producer        = kafka_producer

    # ─────────────────────────────────────────────
    # SCENARIO 1 — Contact arrives
    # ─────────────────────────────────────────────

    async def route(
        self,
        event:      ConversationInboundEvent,
        elapsed_ms: int = 0,
    ) -> RoutingResult:
        """
        Routes a contact to the most compatible resource.
        Tries local site first, remote sites as fallback.
        Total timeout: 150ms per site.
        """
        now = datetime.now(timezone.utc).isoformat()

        # When pool_id is explicit (set by entry point config or escalation target),
        # restrict routing to that pool only — no scanning of all tenant pools.
        # This is the expected path for all contacts: the channel entry point
        # declares the service pool, so the routing engine never needs to infer it.
        if event.pool_id:
            pool = await self._pools.get_pool(event.tenant_id, event.pool_id)
            pools = [pool] if pool else []
        else:
            # Legacy fallback: scan all pools compatible with the channel.
            # Only reached if the event was published without pool_id
            # (e.g. manual test events, external integrations not yet updated).
            pools = await self._pools.get_candidate_pools(event.tenant_id, event.channel)
            # Arc 19: webhook sessions carry no pool_id — the skill_id is the
            # endpoint (DNIS). Resolve deterministically by matching it against
            # each pool's webhook_skill_id, so multiple webhook pools route
            # correctly (not just the first/scored candidate). Falls back to the
            # unfiltered scan when no pool declares webhook_skill_id (backward-compat
            # with a single webhook pool).
            if event.channel == "webhook" and event.skill_id:
                matched = [
                    p for p in pools
                    if getattr(p, "webhook_skill_id", None) == event.skill_id
                ]
                if len(matched) > 1:
                    # S4 — endereço AMBÍGUO: o mesmo skill está deployado em N pools
                    # (regime legítimo — é o desenho do survey: um skill outbound em três
                    # pools, um por grão, cada um com `config_json` diferente). Escolher
                    # por score seria rodar um deploy que o chamador não pediu, em
                    # silêncio. O endereço canônico é o POOL (`pool_id` no evento).
                    logger.error(
                        "Webhook endpoint AMBÍGUO: skill_id=%s casa %d pools (%s) — "
                        "sessão %s NÃO será roteada. O endereço canônico é o POOL: use "
                        "workflow_trigger(pool_id=...) / POST /v1/channels/webhook/pool/{pool_id}. "
                        "skill_id só é endereço enquanto UM pool o declara.",
                        event.skill_id, len(matched),
                        ",".join(p.pool_id for p in matched), event.session_id,
                    )
                    pools = []
                elif matched:
                    pools = matched
                elif any(getattr(p, "webhook_skill_id", None) for p in pools):
                    # Regime multi-pool determinístico: veio um skill_id mas NENHUM pool
                    # webhook o declara → REJEITA (não misroteia para um pool alheio).
                    # pools=[] cai no no_resource/queued abaixo. O fallback não-filtrado
                    # fica só para o legado single-pool (nenhum pool declara webhook_skill_id).
                    pools = []

        if not pools:
            return self._build_queued_result(event, now)

        # ── Frente 1: dispatch pull ───────────────────────────────────────────
        # Pools em dispatch_mode "pull" NÃO auto-alocam: o contato é parqueado na
        # fila e um agente logado o retira explicitamente (work_task_claim, F1.2).
        # Pula o _allocate e segue o MESMO caminho de "queued" (release do pool de
        # origem + queue.position) que uma alocação que falha.
        if event.pool_id and getattr(pools[0], "dispatch_mode", "push") == "pull":
            # F1.3: re-parque limpa qualquer claim lease anterior desta sessão — um
            # contato re-enfileirado (ex.: agente desconectou → bridge re-roteia →
            # aqui) não pode ficar com lease órfã apontando a um claim que acabou.
            asyncio.create_task(
                self._instances.delete_claim_lease(
                    event.tenant_id, event.pool_id, event.session_id
                )
            )
            # Fase A / D6 — e o REGISTRO durável junto, pela mesma razão e com mais
            # força: este é o caminho do F5 (drop de transporte → bridge re-rota →
            # item volta ao ZSET). Se o registro sobrevivesse ao re-parque, a aba
            # velha do agente continuaria provando posse sobre um item que já está
            # na fila — que é exatamente o cenário que a Fase A existe para recusar.
            asyncio.create_task(
                self._instances.delete_claim_record(
                    event.tenant_id, event.pool_id, event.session_id
                )
            )
            queued_result = self._build_queued_result(event, now)
            asyncio.create_task(
                self._release_session_from_pool(
                    event.tenant_id, event.session_id, event.pool_id
                )
            )
            _pull_pool = pools[0]
            # queue.position_updated é publicado em main.py APÓS o enqueue
            # (_publish_queue_position) — aqui a fila ainda não contém a sessão.
            asyncio.create_task(
                self._write_snapshot(event.tenant_id, _pull_pool.pool_id, _pull_pool)
            )
            return queued_result

        # Try local site
        try:
            result = await asyncio.wait_for(
                self._allocate(event, pools, elapsed_ms, self._local_site),
                timeout=self._settings.routing_timeout_ms / 1000,
            )
            if result.allocated:
                # Fire-and-forget snapshot update after successful allocation
                if pools:
                    _matched = next((p for p in pools if p.pool_id == result.pool_id), pools[0])
                    asyncio.create_task(
                        self._write_snapshot(event.tenant_id, _matched.pool_id, _matched)
                    )
                return result
        except asyncio.TimeoutError:
            pass

        # Try remote sites (cross-site)
        remote_sites = {s for p in pools for s in p.remote_sites}
        for site in remote_sites:
            try:
                result = await asyncio.wait_for(
                    self._allocate(event, pools, elapsed_ms, site),
                    timeout=0.300,  # 300ms per remote site
                )
                if result.allocated:
                    result.cross_site     = True
                    result.allocated_site = site
                    return result
            except asyncio.TimeoutError:
                continue

        # Contact could not be allocated — queued
        queued_result = self._build_queued_result(event, now)

        # Release active-count from the session's previous pool.
        # Covers the escalation case: AI segment ends, session moves to a human
        # pool's queue.  agent_done fires only on full session close, so without
        # this call the origin pool's busy counter would stay elevated until the
        # session eventually closes.
        asyncio.create_task(
            self._release_session_from_pool(
                event.tenant_id, event.session_id, event.pool_id
            )
        )

        # queue.position_updated NÃO é publicado aqui: a posição só é verdadeira
        # DEPOIS do enqueue (`add_queued_contact`), e este ponto roda antes dele.
        # Publicar daqui — como se fazia — lia a fila sem esta sessão e mandava
        # posição/ETA zerados. O publish vive em main._publish_queue_position, ao
        # lado do _write_queue_context (mesma conta, uma fonte). Ver CHANGELOG.
        if event.pool_id and pools:
            _pool = next((p for p in pools if p.pool_id == event.pool_id), pools[0])
            asyncio.create_task(self._write_snapshot(event.tenant_id, _pool.pool_id, _pool))

        return queued_result

    async def _write_snapshot(
        self,
        tenant_id: str,
        pool_id:   str,
        pool:      PoolConfig,
    ) -> None:
        """Writes pool operational snapshot to Redis after a routing event."""
        try:
            await self._instances.write_pool_snapshot(
                tenant_id=               tenant_id,
                pool_id=                 pool_id,
                sla_target_ms=           pool.sla_target_ms,
                channel_types=           pool.channel_types,
                max_reply_time_ms=       pool.max_reply_time_ms,
                # Arc 19: forward webhook pool fields so snapshot includes them
                webhook_skill_id=        pool.webhook_skill_id,
                max_concurrent_sessions= pool.max_concurrent_sessions,
            )
        except Exception as exc:
            logger.warning(
                "Failed to write pool snapshot for pool %s: %s", pool_id, exc
            )

    async def _release_session_from_pool(
        self,
        tenant_id:   str,
        session_id:  str,
        new_pool_id: str | None = None,
    ) -> None:
        """
        Fire-and-forget wrapper: releases the session's active-count from its
        current pool when it moves to a queue without an agent being allocated.
        See InstanceRegistry.release_session_from_pool for full semantics.
        """
        try:
            await self._instances.release_session_from_pool(
                tenant_id, session_id, new_pool_id
            )
        except Exception as exc:
            logger.warning(
                "Failed to release session %s from previous pool: %s",
                session_id, exc,
            )

    async def _allocate(
        self,
        event:   ConversationInboundEvent,
        pools:   list[PoolConfig],
        elapsed: int,
        site:    str,
    ) -> RoutingResult:
        now = datetime.now(timezone.utc).isoformat()

        # Check session affinity (stateful)
        affinity_id = await self._instances.get_session_affinity(event.session_id)
        if affinity_id:
            result = await self._try_affinity(event, affinity_id, pools, now)
            if result:
                return result

        # Calculate resource_score for each available instance.
        # Router atomic allocation (Fatia B): coletamos TODOS os candidatos pontuados
        # e, em seguida, tentamos um CLAIM atômico do melhor; se ele falhar (perdeu a
        # corrida de concorrência / instância lotada), caímos no próximo best. Isso
        # elimina a sobre-alocação do antigo select→mark_busy não-atômico.
        # Arc 7d — performance_score_weight is dynamically overridable via
        # Config API namespace "routing" key "performance_score_weight".
        # Falls back to env-var setting when Config API is unavailable.
        perf_weight = routing_config.get(
            "performance_score_weight",
            self._settings.performance_score_weight,
        )

        candidates: list[tuple[float, PoolConfig, AgentInstance]] = []
        for pool in pools:
            instances = await self._instances.get_ready_instances(
                event.tenant_id, pool.pool_id
            )
            for inst in instances:
                if not instance_has_capacity(inst):
                    continue
                # Conference: only allocate instances of the requested agent type.
                # Prevents assigning a generic pool instance when the supervisor
                # explicitly invited a specific AI agent type.
                # F2: identidade por-pool é DERIVADA (o pool está em escopo aqui);
                # o campo armazenado é resíduo arbitrário para humano multi-pool.
                if event.agent_type_id and resolve_agent_type(
                    inst, pool.pool_id
                ) != event.agent_type_id:
                    continue

                # Arc 7d — fetch historical performance score when weight > 0.
                # Falls back to 0.5 (neutral) when no data is available.
                if perf_weight > 0.0:
                    perf_score = await self._instances.get_agent_performance_score(
                        event.tenant_id, resolve_agent_type(inst, pool.pool_id)
                    )
                else:
                    perf_score = 0.5  # unused — weight is 0.0

                rscore = score_resource(
                    event, inst, pool,
                    performance_score=perf_score,
                    performance_score_weight=perf_weight,
                )
                if rscore < 0:
                    continue  # hard filter
                candidates.append((rscore, pool, inst))

        # Maior score primeiro; o claim atômico arbitra alocações concorrentes.
        candidates.sort(key=lambda c: c[0], reverse=True)

        # claim_instance usa occupant "{session_id}::{conference_id}": duas confs da MESMA
        # sessão (conference_ids distintos) NÃO dividem vaga → a 2ª recebe -1 e re-seleciona
        # outra instância; re-rota da mesma sessão (mesmo occupant) é idempotente. Quem
        # recebe claim=-1 (lotada / tomada por chamador concorrente) tenta o próximo best.
        best_instance: AgentInstance | None = None
        best_pool:     PoolConfig   | None = None
        best_score:    float               = -1.0
        for rscore, pool, inst in candidates:
            claimed = await self._instances.claim_instance(
                event.tenant_id, inst.instance_id,
                event.session_id, event.conference_id, inst.max_concurrent,
                pool_id=pool.pool_id,
            )
            if claimed >= 1:
                best_instance, best_pool, best_score = inst, pool, rscore
                break
            logger.info(
                "router.claim: instance=%s busy/taken (claim=-1) — re-selecting next best: "
                "session=%s pool=%s", inst.instance_id, event.session_id, pool.pool_id,
            )

        if not best_instance or not best_pool:
            return RoutingResult(
                session_id=event.session_id, tenant_id=event.tenant_id,
                allocated=False, routed_at=now,
            )

        # Determine the session_id to pass to mark_busy:
        # - Normal events: always pass session_id (enables same-pool guard + cross-pool DECR).
        # - Conference events (hooks, specialists): normally pass None to avoid cross-pool
        #   DECR for independent parallel participants.
        # - EXCEPTION: if the conference event targets a pool that already owns the session
        #   (e.g. a conference specialist escalating back to the primary pool), pass the
        #   session_id so the same-pool re-entry guard fires. (Antes da fatia 2 isso
        #   evitava um INCR duplo de `active_count`; hoje evita reescrever o
        #   serving_pool e re-disparar o caminho de transferência cross-pool.)
        mark_busy_session_id: str | None
        if not event.conference_id:
            mark_busy_session_id = event.session_id
        else:
            current_serving = await self._instances.get_session_serving_pool(
                event.tenant_id, event.session_id
            )
            if current_serving and current_serving == best_pool.pool_id:
                # Conference specialist is escalating back to the pool that already
                # owns the session.  Pass session_id → same-pool guard returns early.
                mark_busy_session_id = event.session_id
                logger.info(
                    "router.mark_busy: conference event targets already-serving pool=%s "
                    "session=%s — passing session_id to trigger same-pool guard",
                    best_pool.pool_id, event.session_id,
                )
            else:
                # Fresh conference invite (hook, new specialist).  Pass None to avoid
                # inheriting the cross-pool DECR chain from the primary contact.
                mark_busy_session_id = None

        await self._instances.mark_busy(
            event.tenant_id, best_pool.pool_id, best_instance.instance_id,
            session_id=mark_busy_session_id,
        )
        if best_instance.execution_model == "stateful":
            await self._instances.set_session_affinity(
                event.session_id, best_instance.instance_id
            )

        mode = determine_routing_mode(
            event.confidence or 0.0,
            self._settings.routing_confidence_autonomous,
            self._settings.routing_confidence_hybrid,
            getattr(event.customer_profile, "risk_flag", False),
        )

        # Compute priority_score (spec 4.6) for the selected pool
        prio_score = compute_priority_score(
            routing_expr   = best_pool.routing_expression,
            sla_urgency    = elapsed / max(best_pool.sla_target_ms, 1),
            wait_time_norm = min(elapsed / max(best_pool.sla_target_ms, 1), 1.0),
            customer_tier  = event.customer_profile.tier,
            churn_risk     = event.customer_profile.churn_risk,
            business_score = event.customer_profile.business_score,
        )

        return RoutingResult(
            session_id=event.session_id,
            tenant_id=event.tenant_id,
            allocated=True,
            instance_id=best_instance.instance_id,
            # F2 — o que o bridge usa para escolher O QUE EXECUTAR. Derivado do
            # pool alocado, nunca lido do registro global do recurso.
            agent_type_id=resolve_agent_type(best_instance, best_pool.pool_id),
            pool_id=best_pool.pool_id,
            resource_score=best_score,
            priority_score=prio_score if prio_score != float("inf") else 9999.0,
            routing_mode=mode,   # type: ignore[arg-type]
            allocated_site=self._local_site,
            sla_target_ms=best_pool.sla_target_ms,
            routed_at=now,
            conference_id=event.conference_id,       # None for regular contacts
            channel_identity=event.channel_identity, # None for regular contacts
        )

    # ─────────────────────────────────────────────
    # SCENARIO 2 — Resource becomes available
    # ─────────────────────────────────────────────

    async def dequeue(
        self,
        instance:  AgentInstance,
        pool:      PoolConfig,
        now_ms:    int,
        top_n:     int = 10,
    ) -> QueuedContact | None:
        """
        Selects the highest-effective-priority queued contact
        that is compatible with this resource.

        Spec 3.3b Scenario 2:
          1. Load top_n contacts from Redis Sorted Set by current score
          2. Recalculate queue_scorer with now_ms for the top_n
          3. Check resource compatibility with each contact
          4. Allocate the highest-priority compatible contact
        """
        queued = await self._instances.get_queued_contacts(
            pool.tenant_id, pool.pool_id, top_n
        )
        if not queued:
            return None

        # Recalculate scores and sort
        scored = [
            (contact, score_contact_in_queue(contact, pool, now_ms))
            for contact in queued
        ]
        scored.sort(key=lambda x: x[1], reverse=True)

        # Return first contact compatible with this resource
        for contact, _ in scored:
            rscore = score_resource(
                # Build minimal event with contact requirements
                _contact_to_event(contact),
                instance,
                pool,
            )
            if rscore >= 0:
                return contact

        return None

    async def _try_affinity(
        self,
        event:      ConversationInboundEvent,
        instance_id: str,
        pools:       list[PoolConfig],
        now:         str,
    ) -> RoutingResult | None:
        for pool in pools:
            instances = await self._instances.get_ready_instances(
                event.tenant_id, pool.pool_id
            )
            for inst in instances:
                if inst.instance_id != instance_id:
                    continue
                if not instance_has_capacity(inst):
                    continue
                rscore = score_resource(event, inst, pool)
                if rscore < 0:
                    continue
                # Same guard as the main route() path: for conference events,
                # only pass None if the target pool is NOT already serving the session.
                dequeue_sid: str | None
                if not event.conference_id:
                    dequeue_sid = event.session_id
                else:
                    cur_sp = await self._instances.get_session_serving_pool(
                        event.tenant_id, event.session_id
                    )
                    dequeue_sid = event.session_id if (cur_sp and cur_sp == pool.pool_id) else None
                # Router atomic allocation (Fatia B): reserva atômica da instância de
                # afinidade antes do mark_busy. Se lotada (claim=-1), cai no roteamento
                # normal (return None) em vez de sobre-alocar.
                _aff_claimed = await self._instances.claim_instance(
                    event.tenant_id, inst.instance_id,
                    event.session_id, event.conference_id, inst.max_concurrent,
                    pool_id=pool.pool_id,
                )
                if _aff_claimed < 1:
                    logger.info(
                        "router.affinity: affinity instance %s busy (claim=-1) — "
                        "falling back to normal routing: session=%s",
                        inst.instance_id, event.session_id,
                    )
                    return None
                await self._instances.mark_busy(
                    event.tenant_id, pool.pool_id, inst.instance_id,
                    session_id=dequeue_sid,
                )
                return RoutingResult(
                    session_id=event.session_id, tenant_id=event.tenant_id,
                    allocated=True, instance_id=inst.instance_id,
                    agent_type_id=resolve_agent_type(inst, pool.pool_id),  # F2
                    pool_id=pool.pool_id,
                    resource_score=rscore, routing_mode="autonomous",
                    allocated_site=self._local_site, routed_at=now,
                    sla_target_ms=pool.sla_target_ms,
                    conference_id=event.conference_id,       # propagate conference context
                    channel_identity=event.channel_identity, # propagate channel identity
                )
        return None

    def _build_queued_result(
        self, event: ConversationInboundEvent, now: str
    ) -> RoutingResult:
        return RoutingResult(
            session_id=event.session_id, tenant_id=event.tenant_id,
            allocated=False, queued=True, routing_mode="supervised", routed_at=now,
            pool_id=event.pool_id,
        )

    # ─────────────────────────────────────────────
    # Frente 1 — Dispatch pull: claim / release
    # Operações expostas como tools/API na F2; aqui são as FUNÇÕES do routing —
    # o Routing Engine continua o único árbitro (ZREM/claim/mark_busy/lease/routed
    # acontecem DENTRO dele; a Console só solicita).
    # ─────────────────────────────────────────────

    @property
    def _claim_lease_s(self) -> int:
        # Config API namespace `routing` (cache routing_config); default 180.
        return int(routing_config.get("claim_lease_s", 180))

    @staticmethod
    def _claim_record_ttl_s(contact: dict, session_id: str) -> int:
        """
        Fase A / D6 — TTL do registro durável de posse = o que RESTA do prazo do
        item (`work_item_deadline`, carimbado no despacho pelo channel-gateway).

        Derivado, não configurável: o registro tem de cobrir a janela em que o
        item pode ser submetido, e essa janela é o prazo do item — um knob
        separado só criaria uma segunda régua que diverge da primeira no
        primeiro ajuste.

        Prazo ausente/ilegível → fallback de 25 h **com log**, mesma convenção do
        ledger `{t}:work_task:{session}`. Nunca cai no `claim_lease_s`: um
        registro de 180 s seria a lease com outro nome, e reabriria o fail-open.
        """
        raw = str(contact.get("work_item_deadline") or "")
        if raw:
            try:
                deadline = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                remaining = int(
                    (deadline - datetime.now(timezone.utc)).total_seconds()
                )
                if remaining > 0:
                    return remaining
                logger.info(
                    "claim_record: prazo do item já vencido (session=%s deadline=%s) — "
                    "TTL cai no piso; o expire é quem encerra",
                    session_id, raw,
                )
                return 60
            except Exception as exc:
                logger.warning(
                    "claim_record: work_item_deadline ilegível (session=%s valor=%r): %s "
                    "— usando fallback de 25 h", session_id, raw, exc,
                )
        else:
            logger.info(
                "claim_record: item sem work_item_deadline (session=%s) — fallback de "
                "25 h (convenção do ledger work_task)", session_id,
            )
        return _CLAIM_RECORD_FALLBACK_TTL_S

    async def work_task_holder(
        self,
        tenant_id:  str,
        pool_id:    str,
        session_id: str,
    ) -> dict:
        """
        A5 — quem detém este item de trabalho, respondido pelo ÁRBITRO. O ingress de
        resume (channel-gateway) usa isto para o check caller==claimant, sem ler o
        Redis do routing direto (invariante do árbitro único).

        Duas fontes, nesta ordem: a `claim_lease` (carimbo curto, mais barato) e o
        **registro durável** (`_claim_record_key`), que sobrevive ao TTL da lease e
        cobre a janela em que o submit de fato acontece. Qual respondeu vai em `via`
        — sem isso, "posse ausente" e "posse expirada" chegariam ao chamador como o
        mesmo silêncio.

        `in_queue` é o terceiro fato, e é o que torna o veredicto **fechável**: item
        que está no ZSET não tem dono nenhum (por construção — o claim é um ZREM), então
        `found=False, in_queue=True` é uma resposta POSITIVA ("ninguém detém, está na
        fila"), não uma ausência de informação. `found=False, in_queue=False` continua
        sendo ausência honesta (pool push, item já encerrado, claim pré-Fase A) e é o
        único caso em que o chamador deve degradar para permissivo.

        A resposta é COMPOSTA das duas fontes, não devolvida pela que respondeu
        primeiro. `via`/`instance_id`/`claimed_at` vêm de quem PROVOU a posse; o
        `claimant_user_id` vem sempre do **registro**, que é seu único escritor — a
        lease não o carrega. Devolver a chave crua fazia o campo ser `null` nos
        primeiros 180 s e correto depois: um consumidor que o comparasse com
        `assigned_to` (é o que a Fase C/D3 vai fazer) falharia aberto justamente na
        janela quente. Campo cuja presença depende do tier de armazenamento é o
        valor plausível que a § Postura de Engenharia proíbe.

        Retorna {found, instance_id?, claimant_user_id?, claimed_at?, via, in_queue}.
        """
        in_queue = await self._instances.is_queued(tenant_id, pool_id, session_id)
        lease    = await self._instances.read_claim_lease(tenant_id, pool_id, session_id)
        record   = await self._instances.read_claim_record(tenant_id, pool_id, session_id)

        proof = lease or record
        if not proof:
            return {"found": False, "via": "none", "in_queue": in_queue}

        return {
            "found":            True,
            "instance_id":      proof.get("instance_id"),
            "claimed_at":       proof.get("claimed_at"),
            # Único escritor. Ausente ⇒ o registro morreu antes da lease (só
            # acontece com prazo de item menor que 180 s) — `None` aqui é
            # ausência honesta, não "não há claimant".
            "claimant_user_id": (record or {}).get("claimant_user_id"),
            "via":              "lease" if lease else "record",
            "in_queue":         in_queue,
        }

    async def work_task_claim(
        self,
        tenant_id:         str,
        pool_id:           str,
        session_id:        str,
        instance_id:       str,
        conference_id:     str = "",
        claimant_user_id:  str | None = None,
    ) -> dict:
        """
        Pull: retirada explícita de um contato da fila por um agente logado.
        Atômica e composta:
          1. valida a instância do agente;
          2. lê o pacote do contato (para routed/rollback);
          2b. Camada B — elegibilidade do "ramal": se o item é reservado
             (assigned_to) e o claimant não é o dono, só passa após o transbordo
             (idade ≥ fallback_to_pool_after_s); senão → reserved_to_other;
          3. ZREM atômico da fila (um vencedor) — perdeu → already_claimed;
          4. reserva a vaga no semáforo do RECURSO (push+pull) — −1 (sem capacidade)
             → ROLLBACK re-enfileira → no_capacity;
          5. mark_busy + grava lease do claim;
          6. publica conversations.routed → reusa bridge/Console (vira atendimento).

        `claimant_user_id`: identidade do agente que puxa, para casar com
        `assigned_to`. Ausente → derivado do instance_id (`human-{userId}`).
        """
        now = datetime.now(timezone.utc).isoformat()

        inst = await self._instances.get_instance(tenant_id, instance_id)
        if inst is None:
            return {"claimed": False, "reason": "instance_not_found"}

        contact = await self._instances.get_full_queued_contact(tenant_id, session_id)
        if contact is None:
            return {"claimed": False, "reason": "not_in_queue"}

        # 2b — Camada B: gate de elegibilidade do pull direcionado ("ramal").
        # Item reservado (assigned_to) só é claimable por (a) o próprio dono, ou
        # (b) qualquer um do pool APÓS o transbordo (idade ≥ fallback_to_pool_after_s;
        # fallback ausente = reserva permanente). Sem query extra: a âncora
        # (assigned_at_ms, preservada no requeue; fallback queued_at_ms) já está no
        # pacote lido acima. INVARIANTE: filtro de claim sobre trabalho pooled —
        # nunca alvo de roteamento que bypassa o pool.
        assigned_to = contact.get("assigned_to")
        if assigned_to:
            claimant = claimant_user_id or (
                instance_id[len("human-"):] if instance_id.startswith("human-")
                else instance_id
            )
            if claimant != assigned_to:
                fallback_s = contact.get("fallback_to_pool_after_s")
                anchor_ms  = contact.get("assigned_at_ms") or contact.get("queued_at_ms")
                overflowed = False
                if fallback_s is not None and anchor_ms:
                    age_s = (
                        datetime.now(timezone.utc).timestamp() * 1000 - int(anchor_ms)
                    ) / 1000.0
                    overflowed = age_s >= float(fallback_s)
                if not overflowed:
                    # Degradação nunca silenciosa: loga o motivo da recusa.
                    logger.info(
                        "work_task_claim: reserved_to_other — session=%s assigned_to=%s "
                        "claimant=%s fallback_s=%s (not overflowed yet)",
                        session_id, assigned_to, claimant, fallback_s,
                    )
                    return {"claimed": False, "reason": "reserved_to_other"}

        # 3 — atomic win
        won = await self._instances.atomic_claim_dequeue(tenant_id, pool_id, session_id)
        if not won:
            return {"claimed": False, "reason": "already_claimed"}

        # 4 — reserva a vaga do recurso (semáforo compartilhado push+pull)
        # Phase 2 (hand-off): item de wrap-up AUTO-ATENDIDO reivindicado pelo PRÓPRIO
        # dono herda o hold que o close da origem deixou (swap net 0) — a ocupação
        # não oscila, então um push não toma a vaga na janela a max_concurrent=1.
        # Sem hold vivo (release já ocorreu / ordem invertida) cai no claim normal.
        # Gate estreito de propósito: só auto_attend + dono. Push nunca herda.
        _can_inherit = bool(contact.get("auto_attend")) and bool(assigned_to) and (
            (claimant_user_id or (
                instance_id[len("human-"):] if instance_id.startswith("human-")
                else instance_id
            )) == assigned_to
        )
        occ = await self._instances.claim_instance(
            tenant_id, instance_id, session_id,
            conference_id or None, int(inst.max_concurrent),
            pool_id=pool_id,
            can_inherit_hold=_can_inherit,
        )
        if occ == -1:
            # rollback — re-enfileira (JSON ainda armazenado)
            await self._instances.add_queued_contact(
                tenant_id, pool_id, session_id, contact,
                int(contact.get("queued_at_ms")
                    or int(datetime.now(timezone.utc).timestamp() * 1000)),
            )
            logger.info(
                "work_task_claim: no capacity — rolled back: session=%s instance=%s pool=%s",
                session_id, instance_id, pool_id,
            )
            return {"claimed": False, "reason": "no_capacity"}

        # 5 — mark_busy + lease + registro durável de posse
        await self._instances.mark_busy(tenant_id, pool_id, instance_id, session_id)
        _claimant = claimant_user_id or (
            instance_id[len("human-"):] if instance_id.startswith("human-")
            else instance_id
        )
        try:
            await self._instances.write_claim_lease(
                tenant_id, pool_id, session_id, instance_id, self._claim_lease_s,
            )
        except Exception as exc:
            logger.warning(
                "work_task_claim: could not write lease session=%s — %s", session_id, exc
            )
        # Fase A / D6 — o registro que o SUBMIT confere. Falhar aqui não desfaz o
        # claim (o agente já detém a vaga), mas tem de gritar: sem esta chave o
        # check A5 volta a degradar para permissivo assim que a lease vencer.
        try:
            await self._instances.write_claim_record(
                tenant_id, pool_id, session_id, instance_id, _claimant,
                self._claim_record_ttl_s(contact, session_id),
            )
        except Exception as exc:
            logger.warning(
                "work_task_claim: could not write claim RECORD session=%s instance=%s — %s "
                "(o submit desta sessão degradará para permissivo após a lease vencer)",
                session_id, instance_id, exc,
            )

        # 6 — publica conversations.routed (reusa todo o downstream)
        # Bug B fix: propagate the conference the caller opened (carried in the
        # queued contact and passed by the claimer) so the bridge attaches the
        # human as the conference PARTICIPANT of the suspended delegate — not a
        # bare primary. Without this the routed event omits the conference, the
        # occupant is "{session}::" (empty conf), and the Console cannot
        # (re-)attach the approval package on claim/re-claim (P2).
        result = RoutingResult(
            session_id=session_id, tenant_id=tenant_id, allocated=True,
            instance_id=instance_id,
            agent_type_id=resolve_agent_type(inst, pool_id),  # F2
            pool_id=pool_id, routing_mode="supervised",
            allocated_site=self._local_site, routed_at=now,
            conference_id=conference_id or None,
            channel_identity=contact.get("channel_identity"),
        )
        if self._producer:
            await self._producer.send(
                self._settings.kafka_topic_routed,
                value=ConversationRoutedEvent(
                    session_id=session_id, tenant_id=tenant_id,
                    result=result, routed_at=now,
                ).model_dump(),
            )
        logger.info(
            "work_task_claim: claimed session=%s instance=%s pool=%s occ=%d",
            session_id, instance_id, pool_id, occ,
        )
        return {
            "claimed": True, "instance_id": instance_id,
            "pool_id": pool_id, "contact": contact,
        }

    def _drop_reserve_window_s(self, pool_id: str) -> int | None:
        """
        Fase C (D3) — janela da reserva por queda. `None` = reserva PERMANENTE.

        **Fila interna (`-int`) não transborda. Nunca.** Não é uma janela generosa:
        é a ausência de janela. A migration `20260730000000_pool_internal_queue`
        já havia fechado isso, e vale citá-la porque é a razão inteira:

        > `fallback_to_pool_after_s` (transbordo) existe porque item de fila é
        > trabalho POOLED: qualquer agente do time serve, e a reserva é uma
        > preferência de roteamento. Wrap-up não é isso — só quem atendeu pode
        > classificar o próprio atendimento. A identidade do executor é parte da
        > DEFINIÇÃO da tarefa, não uma preferência: é trabalho AUTHOR-BOUND, e
        > aplicar transbordo a ele é **erro de categoria**.

        Transbordar um wrap-up entregaria a outro agente a classificação de um
        atendimento que ele não fez — o mesmo "fingir ser o autor" que a D5 da ADR
        author-bound recusou ao supervisor. A saída de um item `-int` abandonado é
        o PRAZO ou o `work_task_expire` do supervisor (D7), que encerra **sem
        disposição**; nunca outro autor.

        Demais filas pull são POOLED por definição — aprovação inclusive ("outro
        aprovador pode decidir") — e ali a janela existe e é curta: a preferência
        pelo agente anterior não pode custar tempo a quem espera.

        *Uma primeira versão desta função devolvia 300 s para `-int`, cometendo
        exatamente o erro que a migration nomeia. Achado por revisão em 2026-08-04,
        antes de qualquer efeito: o caminho normal nunca chega aqui, porque um
        wrap-up real já traz `assigned_to` do despacho e `_apply_drop_reservation`
        não sobrescreve reserva existente.*
        """
        if pool_id.endswith(_INTERNAL_POOL_SUFFIX):
            return None
        return int(routing_config.get("drop_reserve_window_default_s", 30))

    async def work_task_release(
        self,
        tenant_id:   str,
        pool_id:     str,
        session_id:  str,
        instance_id: str,
        reserve_to_previous: bool = False,
    ) -> dict:
        """
        Pull: devolve um contato claimado à fila — remove a lease, libera a vaga do
        recurso (release_instance) e re-enfileira pelos critérios do routing
        (add_queued_contact, NÃO preserva posição).

        **Dois chamadores com intenções OPOSTAS** (Fase C / D3), e é o que
        `reserve_to_previous` separa:

        · `False` (default) — **desistência deliberada**: o botão "Return to queue"
          do Console. O agente largou a task de propósito; reservá-la de volta a ele
          a esconderia do pool inteiro pela duração da janela. O default é este
          justamente porque é o inócuo: um chamador que ignore o parâmetro mantém o
          comportamento anterior.

        · `True` — **queda de transporte**: o bridge, no `agent_disconnect`. Aqui o
          agente NÃO desistiu, e a digitação parcial só existe no navegador dele. O
          item volta **reservado a ele** com transbordo automático
          (`_apply_drop_reservation`).

        **F3a** — a liberação recomputa o snapshot dos pools do RECURSO (fan-out). Sem
        isto a vaga voltava no semáforo e o snapshot seguia afirmando-a consumida, em
        TODAS as linhas do recurso, até que qualquer outro evento tocasse um dos pools.
        Não é chave ausente (que se lê como "não sei"): é número plausível e errado num
        registro que o `system_availability_check` usa para decidir oferta de canal ao
        cliente.

        O refresh vem depois do requeue porque é a ordem em que o recompute vê o estado
        final — **mas a posição não é load-bearing**, e a versão anterior deste
        docstring dizia que era. `add_queued_contact` faz um PATCH in-place de
        `queue_length` no snapshot, então na ordem inversa o campo seria gravado 0 e
        corrigido para 1 em seguida: as duas ordens convergem. Descoberto pela mutação
        M1 (`infra/test/mutation_occupancy_peak.sh`), que mostrou o teste de
        `queue_length` passando com este refresh desligado. O que só o recompute
        produz — e o que os testes cobrem — é a CAPACIDADE.
        """
        # Fase C — o dono anterior sai do REGISTRO, e por isso ele é lido ANTES de
        # ser apagado. A lease não serve: ela não carrega `claimant_user_id`, e
        # `assigned_to` é casado por user, não por instância.
        prev_record = (
            await self._instances.read_claim_record(tenant_id, pool_id, session_id)
            if reserve_to_previous else None
        )

        await self._instances.delete_claim_lease(tenant_id, pool_id, session_id)
        # Fase A / D6 — a posse acaba aqui, então o registro tem de acabar junto.
        # Registro sobrevivente a um release devolveria "eu detenho" a quem
        # devolveu o item — o mesmo defeito da Fase A, com o sinal trocado.
        await self._instances.delete_claim_record(tenant_id, pool_id, session_id)
        remaining = await self._instances.release_instance(
            tenant_id, instance_id, session_id
        )

        contact = await self._instances.get_full_queued_contact(tenant_id, session_id)
        reserved_to: str | None = None
        if contact is not None and reserve_to_previous:
            reserved_to = self._apply_drop_reservation(
                contact, pool_id, session_id, instance_id, prev_record,
            )
        if contact is not None:
            await self._instances.add_queued_contact(
                tenant_id, pool_id, session_id, contact,
                int(datetime.now(timezone.utc).timestamp() * 1000),   # re-ordena
            )
        await self._instances.refresh_snapshots_for_instance(
            tenant_id, instance_id, extra_pools=[pool_id],
        )
        logger.info(
            "work_task_release: released session=%s instance=%s pool=%s remaining=%d "
            "requeued=%s reserved_to=%s",
            session_id, instance_id, pool_id, remaining, contact is not None,
            reserved_to or "-",
        )
        return {
            "released": True,
            "requeued": contact is not None,
            # Explícito no retorno: `None` distingue "não pediu reserva" de "pediu
            # e não coube" (item já reservado ao autor). O chamador que só olhasse
            # `released` não veria a diferença.
            "reserved_to": reserved_to,
        }

    def _apply_drop_reservation(
        self,
        contact:     dict,
        pool_id:     str,
        session_id:  str,
        instance_id: str,
        prev_record: dict | None,
    ) -> str | None:
        """
        Fase C (D3) — muta `contact` para que ele volte à fila RESERVADO ao dono
        anterior, com transbordo automático. Devolve o user_id reservado, ou None.

        Reusa integralmente o mecanismo da **Camada B** (`assigned_to` +
        `fallback_to_pool_after_s` + `assigned_at_ms`), já implementado e com smoke
        próprio, e cujo gate vive dentro do `work_task_claim`. A alternativa
        descartada no ADR era uma *carência* — um timer novo, específico de
        desconexão, resolvendo um caso do que a reserva resolve em geral.

        Três regras, e nenhuma é arbitrária:

        1. **Nunca sobrescreve `assigned_to` existente.** Num item author-bound
           (`-int`) ele é o AUTOR do atendimento — fato mais forte e mais durável
           que "quem estava com ele agora". Sobrescrever transferiria o vínculo
           autoral para um claimante de transbordo, silenciosamente.
        2. **A âncora reinicia (`assigned_at_ms = agora`).** A reserva de queda é
           um evento novo; a janela conta a partir dela. Isso é o OPOSTO da âncora
           autoral, que `add_queued_contact` preserva de propósito através de
           re-enfileiramentos — são duas reservas com vidas diferentes, e por isso
           o carimbo é feito aqui, e não lá.
        3. **O dono sai do registro de posse; o `instance_id` é só o fallback.**
           `assigned_to` casa por USER (`work_task_claim` deriva `human-{userId}`),
           então usar a instância crua produziria uma reserva que nunca casa. Sem
           registro (expirou), deriva da instância — e diz no log que derivou.
        """
        existing = contact.get("assigned_to")
        if existing:
            logger.info(
                "work_task_release: reserva de queda NÃO aplicada — item já reservado "
                "a %s (vínculo autoral vence a posse transitória): session=%s pool=%s",
                existing, session_id, pool_id,
            )
            return None

        claimant = (prev_record or {}).get("claimant_user_id") or ""
        source   = "claim_record"
        if not claimant:
            claimant = (
                instance_id[len("human-"):] if instance_id.startswith("human-")
                else instance_id
            )
            source = "instance_id"
            # Degradação nunca silenciosa: sem registro, o dono é DEDUZIDO.
            logger.warning(
                "work_task_release: registro de posse ausente — dono da reserva "
                "derivado de instance_id=%s (session=%s pool=%s)",
                instance_id, session_id, pool_id,
            )
        if not claimant:
            logger.warning(
                "work_task_release: sem dono identificável — item volta à fila SEM "
                "reserva (session=%s pool=%s)", session_id, pool_id,
            )
            return None

        window_s = self._drop_reserve_window_s(pool_id)
        contact["assigned_to"]    = claimant
        contact["assigned_at_ms"] = int(
            datetime.now(timezone.utc).timestamp() * 1000
        )
        if window_s is None:
            # Reserva PERMANENTE: o campo fica AUSENTE, que é como a Camada B
            # codifica "não transborda" (`fallback ausente = reserva permanente`).
            # Gravar um número grande seria a mesma coisa com prazo de validade —
            # e trabalho author-bound não tem prazo para deixar de ser do autor.
            contact.pop("fallback_to_pool_after_s", None)
            logger.info(
                "work_task_release: item reservado a %s PERMANENTEMENTE (fila interna "
                "author-bound, fonte=%s) — session=%s pool=%s. Não transborda; a saída "
                "é o prazo ou o expire do supervisor.",
                claimant, source, session_id, pool_id,
            )
        else:
            contact["fallback_to_pool_after_s"] = window_s
            logger.info(
                "work_task_release: item reservado a %s por %ds (fonte=%s) — session=%s "
                "pool=%s. Transborda para o pool inteiro depois disso.",
                claimant, window_s, source, session_id, pool_id,
            )
        return claimant

    async def work_task_expire(
        self,
        tenant_id:  str,
        pool_id:    str,
        session_id: str,
        reason:     str = "expired",
    ) -> dict:
        """
        I5 — ENCERRA um item de trabalho: um caminho só, idempotente, para os dois
        gatilhos (prazo vencido e supervisor). Diferente do `work_task_release`, que
        DEVOLVE o item à fila: aqui o item deixa de existir.

        Faz exatamente o que ainda restar:
          1. ZREM do ZSET + delete do JSON (`remove_queued_contact`) — o caso NUNCA
             REIVINDICADO, o único que hoje não tem quem limpe: sem isto o membro
             fica no ZSET para sempre e a inbox continua exibindo um item que o
             claim recusa (`not_in_queue`);
          2. lease do claim apagada;
          3. vaga do recurso devolvida — pela lease **ou pelo semáforo**.

        **Por que o dono não pode vir só da lease (fix da lacuna 2, 2026-08-03).**
        A lease tem TTL de 180 s e este método dispara no prazo do ITEM, tipicamente
        24 h depois — ~480× maior. No cenário que MOTIVA o expire (reivindicado e
        nunca submetido) a lease já expirou há muito, `instance_id` saía vazio e a
        vaga **nunca era devolvida**: cada claim abandonado subtraía uma vaga do
        agente até o SET inteiro expirar. Não era questão de frequência — era
        aritmética. Pior, o docstring de `_claim_lease_key` afirmava que o "reap de
        ocupantes órfãos" a recuperava; ele só remove ocupante cuja sessão tenha
        `session:{sid}:closed`, e num claim abandonado o delegate está suspenso e a
        sessão está ABERTA — o reap passa ao lado.

        A segunda via é `find_occupant_instance`, que lê a vaga onde ela de fato
        mora. O medo que justificava depender da lease — derrubar o occupant de um
        contato de PUSH na mesma sessão — deixou de existir com a F1: o membro do
        semáforo leva o pool no 3º campo, então a busca discrimina `(sessão, pool)`
        e devolve `None` (com log) quando o único ocupante daquela sessão é de outro
        pool. A lease continua sendo consultada PRIMEIRO por ser mais barata e mais
        direta; a busca é o fallback, e qual das duas respondeu vai no log e no
        retorno (`claimed_via`) — degradação silenciosa aqui reapareceria como
        capacidade que encolhe sem motivo.

        Não re-enfileira, não publica `conversations.routed` e não fecha segmento: o
        fechamento do segmento humano é do bridge (H1, na entrega do resume), que é
        quem conhece `outcome` e `close_reason`. Aqui só se desfaz o parqueamento.

        Idempotente por construção: 2ª chamada devolve
        `was_queued=False, was_claimed=False` e não toca em nada — a 1ª liberou a
        vaga, então a busca não acha mais ocupante.
        """
        lease        = await self._instances.read_claim_lease(tenant_id, pool_id, session_id)
        instance_id  = (lease or {}).get("instance_id") or ""
        claimed_via  = "lease" if instance_id else ""
        if not instance_id:
            # Fase A / D6 — segunda via, ANTES do semáforo: o registro durável guarda
            # exatamente o dono, com TTL casado ao prazo do item, e é justamente no
            # cenário que motiva o expire (reivindicado, lease vencida) que ele ainda
            # está lá. A busca no semáforo continua como terceira via — ela responde
            # "quem ocupa a vaga", que é a mesma resposta por caminho mais caro e só
            # enquanto a vaga não foi devolvida por outro caminho.
            record = await self._instances.read_claim_record(
                tenant_id, pool_id, session_id
            )
            instance_id = (record or {}).get("instance_id") or ""
            if instance_id:
                claimed_via = "record"
        if not instance_id:
            instance_id = await self._instances.find_occupant_instance(
                tenant_id, pool_id, session_id
            ) or ""
            if instance_id:
                claimed_via = "semaphore"
                # Nunca silencioso: esta linha é a MEDIDA da lacuna 2 — item que foi
                # reivindicado, ficou sem lease, e cuja vaga estava presa até aqui.
                logger.warning(
                    "work_task_expire: lease AUSENTE mas vaga ocupada — session=%s "
                    "pool=%s instance=%s. Claim abandonado (lease 180 s × prazo do "
                    "item); a vaga estava presa e só agora volta.",
                    session_id, pool_id, instance_id,
                )

        was_queued = await self._instances.atomic_claim_dequeue(
            tenant_id, pool_id, session_id
        )
        await self._instances.remove_queued_contact(tenant_id, pool_id, session_id)
        await self._instances.delete_claim_lease(tenant_id, pool_id, session_id)
        # Fase A / D6 — o item deixa de existir; a posse dele também.
        await self._instances.delete_claim_record(tenant_id, pool_id, session_id)

        remaining = -1
        if instance_id:
            remaining = await self._instances.release_instance(
                tenant_id, instance_id, session_id
            )

        # F3a — recompute + fan-out. Roda nos DOIS casos, por motivos diferentes:
        #   · item reivindicado  → a vaga do recurso voltou (capacidade em todas as
        #     linhas do recurso);
        #   · nunca reivindicado → só a FILA encolheu, e `queue_length` é campo da
        #     mesma linha. Sem `instance_id` o fan-out não alcança pool nenhum do
        #     recurso — daí `extra_pools`, que garante ao menos a linha deste pool.
        # `instance_id` vazio segue seguro (pools do recurso = ∅); com o fallback do
        # semáforo ele só fica vazio quando NÃO há vaga desta sessão neste pool.
        await self._instances.refresh_snapshots_for_instance(
            tenant_id, instance_id, extra_pools=[pool_id],
        )

        logger.info(
            "work_task_expire: session=%s pool=%s reason=%s was_queued=%s "
            "was_claimed=%s via=%s instance=%s remaining=%s",
            session_id, pool_id, reason, was_queued, bool(instance_id),
            claimed_via or "-", instance_id or "-", remaining,
        )
        return {
            "expired":     True,
            "was_queued":  bool(was_queued),
            # `was_claimed` derivado da lease reportava False para item que FOI
            # reivindicado e cuja lease expirou — indistinguível de "nunca
            # reivindicado", justo a distinção que o estado `orphaned` do relatório
            # de pendências existe para preservar. Agora cobre as duas vias, e
            # `claimed_via` diz qual respondeu.
            "was_claimed": bool(instance_id),
            "claimed_via": claimed_via or None,
            "instance_id": instance_id,
            "remaining":   remaining,
            "reason":      reason,
        }


def _contact_to_event(contact: QueuedContact) -> ConversationInboundEvent:
    """Builds a minimal ConversationInboundEvent from a QueuedContact."""
    return ConversationInboundEvent(
        session_id=contact.session_id,
        tenant_id=contact.tenant_id,
        customer_id="",
        channel="webchat",
        requirements=contact.requirements,
        started_at="",
    )
