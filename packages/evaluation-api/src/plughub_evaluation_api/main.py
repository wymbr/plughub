"""
main.py
FastAPI application entry point for evaluation-api.

Port: 3400 (configurable via PLUGHUB_EVALUATION_PORT)
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone

import httpx

from plughub_contextstore.loader import set_context_map_fetcher
import redis.asyncio as aioredis
import uvicorn
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from . import db as _db
from .sampling import should_sample, should_sample_quota, compute_priority, origin_from_source
from .router import router, _ingest_from_completed_event
from .contestation_router import contestation_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("plughub.evaluation")


# ─── workflow.events consumer ─────────────────────────────────────────────────

async def _on_workflow_event(db_pool: _db.asyncpg.Pool, msg_value: bytes) -> None:
    """
    Update evaluation result workflow state from workflow.events Kafka events.

    workflow.suspended → set action_required, current_round, deadline_at, resume_token
    workflow.completed → lock result with appropriate lock_reason

    LEGADO / Arc 6 "Workflow as review motor" — SUPERSEDED pelo Arc 13 (decisão 2026-06-25, S2.4).
    O contrato canônico de contest→review→finalize é REST (contestation_router →
    finalize_evaluation, que emite evaluation_finalized). Este consumer é apenas REATIVO e
    INERTE em produção: nada no backend dispara o review workflow (review_workflow_skill_id é
    config morta; só o e2e cenário 28 dá trigger). Termina em `lock` (NÃO finaliza) — por isso
    NÃO deve ser o caminho de qualidade. Mantido por compat com o cenário 28; remoção física é
    follow-up opcional. Só age em eventos que carregam result_id no context.
    """
    try:
        event = json.loads(msg_value)
    except Exception:
        return

    event_type = event.get("event_type", "")
    context = event.get("context") or {}
    result_id = context.get("result_id")
    tenant_id = context.get("tenant_id")

    if not result_id:
        return  # not an evaluation workflow event

    if event_type == "workflow.suspended":
        # Determine which party should act next based on the suspended step name
        suspended_step = event.get("suspended_at_step", "")
        if "revisao" in suspended_step or "review" in suspended_step:
            action_required = "review"
        elif "contestacao" in suspended_step or "contest" in suspended_step:
            action_required = "contestation"
        else:
            action_required = None

        deadline_at: datetime | None = None
        if event.get("resume_expires_at"):
            try:
                deadline_at = datetime.fromisoformat(event["resume_expires_at"])
            except Exception:
                pass

        await _db.update_result_workflow_state(
            db_pool,
            result_id,
            action_required=action_required,
            current_round=context.get("current_round", 1),
            deadline_at=deadline_at,
            resume_token=event.get("resume_token"),
            workflow_instance_id=event.get("instance_id"),
        )
        logger.info(
            "result %s workflow suspended: action=%s round=%s",
            result_id, action_required, context.get("current_round"),
        )

    elif event_type == "workflow.completed":
        lock_reason = context.get("lock_reason", "completed")
        await _db.lock_result(db_pool, result_id, lock_reason=lock_reason, locked_by="workflow")
        logger.info("result %s locked by workflow: lock_reason=%s", result_id, lock_reason)

    elif event_type == "workflow.timed_out":
        # Workflow timeout = freeze result at current state
        await _db.update_result_workflow_state(
            db_pool,
            result_id,
            action_required=None,
            locked=True,
            lock_reason="review_timeout",
        )
        logger.info("result %s locked (workflow timeout)", result_id)


async def _run_workflow_consumer(app: FastAPI) -> None:
    consumer = AIOKafkaConsumer(
        settings.workflow_events_topic,
        bootstrap_servers=settings.kafka_brokers,
        group_id="evaluation-api-workflow-consumer",
        auto_offset_reset="latest",
        enable_auto_commit=True,
    )
    await consumer.start()
    logger.info("workflow.events consumer started")
    try:
        async for msg in consumer:
            if msg.value:
                try:
                    await _on_workflow_event(app.state.db_pool, msg.value)
                except Exception as exc:
                    logger.error("workflow event processing error: %s", exc)
    finally:
        await consumer.stop()


# ─── conversations.session_closed → sampling (S2.1, campaign-driven) ───────────

def _duration_s(started_at: str | None, closed_at: str | None) -> float:
    """Duração da sessão em segundos a partir dos ISO timestamps (0 se faltarem)."""
    if not started_at or not closed_at:
        return 0.0
    try:
        a = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(closed_at).replace("Z", "+00:00"))
        return max(0.0, (b - a).total_seconds())
    except Exception:
        return 0.0


def _to_dt(v) -> "datetime | None":
    """Aceita ISO string (com 'Z') ou datetime; None se não parseável."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None


def _within_campaign_window(campaign: dict, closed_at) -> bool:
    """T17 — sessão entra na campanha se closed_at ∈ [period_start, period_end]
    (NULL = aberto). Sem closed_at → não filtra (não descarta)."""
    ps = _to_dt(campaign.get("period_start"))
    pe = _to_dt(campaign.get("period_end"))
    if ps is None and pe is None:
        return True
    cat = _to_dt(closed_at)
    if cat is None:
        return True
    if ps is not None and cat < ps:
        return False
    if pe is not None and cat > pe:
        return False
    return True


# ─── T2 — acumulador de segmentos por sessão (conversations.participants) ──────

def _segs_key(tenant_id: str, session_id: str) -> str:
    return f"{tenant_id}:eval:segs:{session_id}"


_SKILL_VERSION_CACHE: dict[tuple[str, str], tuple[float, str]] = {}
_SKILL_VERSION_TTL_S = 60.0


async def _fetch_skill_version(tenant_id: str, skill_id: str) -> str:
    """R9d — versão corrente do skill (GET agent-registry /v1/skills/{id}.version),
    cache TTL curto. Resolve deploy_version do segmento de IA quando o evento do
    bridge não a trouxe (mismatch de cache / YAML fallback). Degradação → ""."""
    base = settings.agent_registry_url
    if not (base and skill_id):
        return ""
    key = (tenant_id, skill_id)
    hit = _SKILL_VERSION_CACHE.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    version = ""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"{base.rstrip('/')}/v1/skills/{skill_id}",
                headers={"x-tenant-id": tenant_id},
            )
            resp.raise_for_status()
            version = str((resp.json() or {}).get("version") or "")
    except Exception as exc:
        logger.debug("skill version unavailable %s/%s: %s", tenant_id, skill_id, exc)
    _SKILL_VERSION_CACHE[key] = (time.monotonic() + _SKILL_VERSION_TTL_S, version)
    return version


async def _on_participant_event(redis_client, msg_value: bytes) -> None:
    """Acumula segmentos de AGENTE (role primary/specialist) por sessão, com a
    identidade do humano (user_id) e o tipo (human/ai). Exclui supervisor/evaluator."""
    try:
        ev = json.loads(msg_value)
    except Exception:
        return
    if ev.get("role") not in ("primary", "specialist"):
        return
    seg_id = ev.get("segment_id"); session_id = ev.get("session_id"); tenant_id = ev.get("tenant_id")
    if not (seg_id and session_id and tenant_id):
        return
    agent_type = ev.get("agent_type") or ""
    rec = {
        "segment_id":           seg_id,
        "role":                 ev.get("role"),
        "agent_type":           agent_type,
        "evaluated_agent_type": "human_agent" if agent_type == "human" else "ai_agent",
        "user_id":              ev.get("user_id", "") or "",
        "user_login":           ev.get("user_login", "") or "",
        "pool_id":              ev.get("pool_id", "") or "",
        "agent_type_id":        ev.get("agent_type_id", "") or "",
        "flow_id":              ev.get("flow_id", "") or "",
        "deploy_version":       ev.get("deploy_version", "") or "",   # R9d
    }
    # R9d — fallback robusto: se o evento do bridge não trouxe deploy_version mas há
    # flow_id (segmento de IA), resolve a versão corrente no agent-registry por flow_id.
    if rec["flow_id"] and not rec["deploy_version"]:
        rec["deploy_version"] = await _fetch_skill_version(tenant_id, rec["flow_id"])
    try:
        key = _segs_key(tenant_id, session_id)
        await redis_client.hset(key, seg_id, json.dumps(rec))
        await redis_client.expire(key, 90000)  # ~25h cobre a sessão + close
    except Exception as exc:
        logger.debug("participants accumulate failed: %s", exc)


async def _read_session_segments(redis_client, tenant_id: str, session_id: str) -> list[dict]:
    try:
        h = await redis_client.hgetall(_segs_key(tenant_id, session_id))
        out: list[dict] = []
        for v in (h or {}).values():
            try:
                out.append(json.loads(v))
            except Exception:
                pass
        return out
    except Exception:
        return []


async def _run_participants_consumer(app: FastAPI) -> None:
    consumer = AIOKafkaConsumer(
        "conversations.participants",
        bootstrap_servers=settings.kafka_brokers,
        group_id="evaluation-api-participants-consumer",
        auto_offset_reset="latest",
    )
    await consumer.start()
    logger.info("conversations.participants consumer started")
    try:
        async for msg in consumer:
            if msg.value:
                try:
                    await _on_participant_event(app.state.redis, msg.value)
                except Exception as exc:
                    logger.error("participant event error: %s", exc)
    finally:
        await consumer.stop()


async def _sample_one_target(
    db_pool, campaigns: list, *, tenant_id: str, session_id: str,
    sample_key: str, meta: dict, segment_id: str | None = None,
    evaluated_user_id: str | None = None, closed_at=None,
    deploy_version: str | None = None, skill_id: str | None = None,
    redis_client=None,
) -> None:
    """Amostra UM alvo (segmento ou sessão-fallback) contra as campanhas ativas."""
    for c in campaigns:
        # Filtro de pool da amostragem = SÓ evaluation_pool_id (alinhado ao caminho do
        # avaliador, router.py:2202). Antes caía no pool_id legado (escopo ABAC) quando
        # evaluation_pool_id vazio — o que deixava um typo no pool_id ("sac" vs "sac_ia")
        # virar filtro e descartar todos os segmentos em silêncio. evaluation_pool_id
        # vazio = campanha cross-pool (avalia todos os pools). O create-DTO espelha
        # pool_id↔evaluation_pool_id; rows legadas são backfilladas (mirror) na migração.
        epid = c.get("evaluation_pool_id")
        if epid and meta.get("pool_id") != epid:
            continue
        # T17 — janela de dados (forward): descarta sessões fora de [period_start, period_end].
        if not _within_campaign_window(c, closed_at):
            continue
        rules = c.get("sampling_rules") or {}
        # R10 — quota mode is stateful (per-agent cumulative deficit, Redis INCR).
        # The other modes (percentage/fixed/all) stay stateless via should_sample().
        if rules.get("mode") == "quota":
            keep = await should_sample_quota(
                redis_client,
                tenant_id=tenant_id, campaign_id=c["id"],
                target_id=(segment_id or session_id), session_meta=meta,
                sampling_rules=rules, evaluated_user_id=evaluated_user_id,
                pool_id=meta.get("pool_id"), skill_id=skill_id,
                deploy_version=deploy_version,
            )
            if not keep:
                continue
        elif not should_sample(sample_key, meta, rules, counter=int(c.get("total_instances") or 0)):
            continue
        if segment_id is not None:
            if await _db.instance_exists_for_segment(db_pool, c["id"], segment_id, tenant_id):
                continue
        elif await _db.instance_exists_for_session(db_pool, c["id"], session_id, tenant_id):
            continue
        priority = compute_priority(meta, rules)
        # T6b — fixa a versão PUBLICADA do form no momento do agendamento (o avaliador
        # lê o snapshot dessa versão em T7). Sem versão publicada → versão viva (default 1).
        form_version = await _db.latest_published_version(db_pool, c["form_id"], tenant_id)
        if form_version is None:
            _form = await _db.get_form(db_pool, c["form_id"], tenant_id)
            form_version = int((_form or {}).get("version") or 1)
        inst = await _db.create_instance(
            db_pool, tenant_id=tenant_id, campaign_id=c["id"], form_id=c["form_id"],
            session_id=session_id, segment_id=segment_id,
            evaluated_user_id=evaluated_user_id, form_version=form_version, priority=priority,
            deploy_version=deploy_version,
        )
        logger.info(
            "sampling: scheduled instance %s (campaign=%s session=%s segment=%s pool=%s)",
            inst.get("id"), c["id"], session_id, segment_id, meta.get("pool_id"),
        )


async def _sample_on_close(db_pool: _db.asyncpg.Pool, redis_client, payload: dict) -> None:
    """T2 — no fechamento, faz fan-out por SEGMENTO de agente (acumulado de
    conversations.participants) e amostra cada um contra as campanhas ativas. Sem
    segmentos acumulados → fallback legado por sessão. NÃO roda o avaliador."""
    session_id = payload.get("session_id")
    tenant_id  = payload.get("tenant_id")
    if not session_id or not tenant_id:
        return
    campaigns = await _db.list_campaigns(db_pool, tenant_id, status="active", limit=500)
    if not campaigns:
        return

    # Substrate isolation (ADR): procedência da sessão derivada do source do evento
    # (external_import→import, internal:reeval→reeval, demais→live). Carimbada no meta
    # → o filtro opcional de origin da campanha (default live) elimina o cross-fire.
    origin = origin_from_source(payload.get("source"))

    segments = await _read_session_segments(redis_client, tenant_id, session_id)
    if segments:
        for seg in segments:
            seg_meta = {
                "pool_id":       seg.get("pool_id"),
                "channel":       payload.get("channel"),
                "outcome":       payload.get("outcome"),
                "agent_type_id": seg.get("agent_type_id"),
                "duration_s":    _duration_s(payload.get("started_at"), payload.get("closed_at")),
                "origin":        origin,
            }
            await _sample_one_target(
                db_pool, campaigns, tenant_id=tenant_id, session_id=session_id,
                sample_key=seg["segment_id"], meta=seg_meta,
                segment_id=seg["segment_id"], evaluated_user_id=(seg.get("user_id") or None),
                closed_at=payload.get("closed_at"),
                deploy_version=(seg.get("deploy_version") or None),
                skill_id=(seg.get("flow_id") or None), redis_client=redis_client,
            )
        return

    # Fallback: sem participants acumulados → comportamento legado por sessão.
    session_meta = {
        "pool_id":       payload.get("pool_id"),
        "channel":       payload.get("channel"),
        "outcome":       payload.get("outcome"),
        "agent_type_id": payload.get("agent_type_id"),
        "duration_s":    _duration_s(payload.get("started_at"), payload.get("closed_at")),
        "origin":        origin,
    }
    await _sample_one_target(
        db_pool, campaigns, tenant_id=tenant_id, session_id=session_id,
        sample_key=session_id, meta=session_meta,
        closed_at=payload.get("closed_at"), redis_client=redis_client,
    )


async def _run_session_closed_consumer(app: FastAPI) -> None:
    consumer = AIOKafkaConsumer(
        "conversations.session_closed",
        bootstrap_servers=settings.kafka_brokers,
        group_id="evaluation-api-sampling-consumer",
        auto_offset_reset="latest",
    )
    await consumer.start()
    logger.info("conversations.session_closed sampling consumer started")
    try:
        async for msg in consumer:
            if not msg.value:
                continue
            try:
                payload = json.loads(msg.value)
            except Exception:
                logger.warning("sampling: invalid session_closed payload")
                continue
            try:
                await _sample_on_close(app.state.db_pool, app.state.redis, payload)
            except Exception as exc:
                logger.error("sampling: failed for %s: %s", payload.get("session_id"), exc)
    finally:
        await consumer.stop()


async def _run_evaluation_completed_consumer(app: FastAPI) -> None:
    """Consume evaluation.events, filter `evaluation.completed` (published by the
    evaluation_submit MCP tool) and persist via ingest. This is the Arc 13
    real-evaluator link — previously missing, so only analytics-api/ClickHouse
    consumed these events while the evaluation-api Postgres (results + instance
    lifecycle) never advanced. group_id is independent of the other consumers."""
    consumer = AIOKafkaConsumer(
        settings.evaluation_topic,
        bootstrap_servers=settings.kafka_brokers,
        group_id="evaluation-api-ingest-consumer",
        auto_offset_reset="latest",
    )
    await consumer.start()
    logger.info("evaluation.events ingest consumer started (topic=%s)", settings.evaluation_topic)
    try:
        async for msg in consumer:
            if not msg.value:
                continue
            try:
                payload = json.loads(msg.value)
            except Exception:
                logger.warning("ingest-consumer: invalid evaluation.events payload")
                continue
            if payload.get("event_type") != "evaluation.completed":
                continue
            try:
                await _ingest_from_completed_event(
                    app.state.db_pool, app.state.kafka_producer, payload,
                )
            except Exception as exc:
                logger.error(
                    "ingest-consumer: failed for instance=%s: %s",
                    payload.get("instance_id"), exc,
                )
    finally:
        await consumer.stop()


# ─── T4 — deadline scanner ────────────────────────────────────────────────────

async def _run_deadline_scanner(app: FastAPI) -> None:
    """Varre resultados com deadline_at vencido e finaliza por timeout:
    open → uncontested; under_review → review_timeout. Roteia pelo finalize_evaluation
    (emissor único, idempotente). O deadline_at é computado na entrada do estado via
    calendar-api; aqui só comparamos now() >= deadline_at."""
    from .router import finalize_evaluation
    logger.info("deadline scanner started (interval=60s)")
    while True:
        await asyncio.sleep(60)
        try:
            pool = app.state.db_pool
            producer = app.state.kafka_producer
            rows = await _db.list_expired_results(pool)
            for r in rows:
                is_open = r.get("result_state") == "open"
                reason  = "uncontested" if is_open else "review_timeout"
                cstate  = "timeout_contestation" if is_open else "timeout_review"
                res = await finalize_evaluation(
                    pool, producer,
                    result_id=r["id"], tenant_id=r.get("tenant_id", ""),
                    instance_id=r.get("instance_id", ""),
                    session_id=r.get("session_id", "") or "",
                    campaign_id=r.get("campaign_id", "") or "",
                    contestation_state=cstate,
                    finalize_reason=reason,
                    final_score=float(r.get("overall_score") or r.get("final_score") or 0),
                    evaluated_agent_type=r.get("evaluated_agent_type"),
                    process_duration_ms=0,
                )
                if res is not None:
                    logger.info("deadline scanner finalized result=%s reason=%s", r["id"], reason)

            # R8c — SLA SOFT da curadoria cega: marca reviews cegas pendentes vencidas
            # como expiradas (informativo; NÃO altera a avaliação). Idempotente.
            try:
                n_expired = await _db.expire_overdue_blind_reviews(pool)
                if n_expired:
                    logger.info("deadline scanner: %s blind curation review(s) soft-expired", n_expired)
            except Exception as exc:
                logger.error("blind SLA expiry error (non-fatal): %s", exc)
        except Exception as exc:
            logger.error("deadline scanner error: %s", exc)


# ─── T15 — dispatcher por janela de calendário ────────────────────────────────

async def _run_dispatch_scanner(app: FastAPI) -> None:
    """Varre as campanhas ativas (todos os tenants) e despacha as instances `scheduled`
    que estão na janela de calendário da campanha, emitindo `evaluation.requested`.
    Idempotente (cooldown via `dispatched_at`); não re-despacha assigned/in_progress.
    Spec §18.4. Complementa o `POST /campaigns/{id}/dispatch` manual ("Rodar agora")."""
    from .router import dispatch_campaign_scheduled
    interval = settings.dispatch_scanner_interval_s
    logger.info("dispatch scanner started (interval=%ss)", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            pool = app.state.db_pool
            producer = app.state.kafka_producer
            campaigns = await _db.list_active_campaigns(pool, limit=500)
            for campaign in campaigns:
                res = await dispatch_campaign_scheduled(
                    pool, producer, campaign,
                    calendar_api_url=settings.calendar_api_url,
                    cooldown_s=settings.dispatch_redispatch_cooldown_s,
                    batch_limit=settings.dispatch_batch_limit,
                )
                if res.get("dispatched"):
                    logger.info("dispatch scanner: campaign=%s dispatched=%s",
                                res["campaign_id"], res["dispatched"])
        except Exception as exc:
            logger.error("dispatch scanner error: %s", exc)


def create_app() -> FastAPI:
    app = FastAPI(
        title="PlugHub Evaluation API",
        version="1.0.0",
        description="Arc 6 quality evaluation platform",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    async def startup() -> None:
        # Database pool
        logger.info("connecting to PostgreSQL…")
        app.state.db_pool = await _db.create_pool(settings.database_url)
        await _db.ensure_schema(app.state.db_pool)
        logger.info("evaluation schema ready")

        # Redis (for ContextStore writes)
        logger.info("connecting to Redis…")
        app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)

        # ALW-02 — transporte do carregador do mapa do ContextStore, registrado UMA vez.
        # Este é o ÚNICO dos cinco serviços que não falava com o config-api antes (medido
        # em 2026-09-02: bridge 5 arquivos, gateway 8, routing 10, ai-gateway 6, este 0),
        # e ele tem um único sítio de escrita no ctx. Não registrar não quebra nada — o
        # carregador cai no mapa embutido e AVISA nomeando a causa.
        async def _ctx_map_fetch(url: str) -> object:
            async with httpx.AsyncClient(timeout=5.0) as c:
                resp = await c.get(url)
                resp.raise_for_status()
                return resp.json()

        set_context_map_fetcher(_ctx_map_fetch)
        logger.info("Redis client ready")

        # Kafka producer
        logger.info("connecting to Kafka…")
        producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_brokers,
            enable_idempotence=True,
        )
        await producer.start()
        app.state.kafka_producer = producer
        logger.info("Kafka producer ready")

        # Start workflow.events consumer as background task
        app.state.workflow_consumer_task = asyncio.create_task(
            _run_workflow_consumer(app),
            name="workflow-events-consumer",
        )
        logger.info("workflow.events consumer task scheduled")

        # S2.1 — conversations.session_closed sampling consumer (campaign-driven)
        app.state.sampling_consumer_task = asyncio.create_task(
            _run_session_closed_consumer(app),
            name="session-closed-sampling-consumer",
        )
        logger.info("conversations.session_closed sampling consumer task scheduled")

        # Arc 13 real-evaluator link — evaluation.completed → ingest
        # (persists EvaluationResult + advances instance; was missing).
        app.state.ingest_consumer_task = asyncio.create_task(
            _run_evaluation_completed_consumer(app),
            name="evaluation-completed-ingest-consumer",
        )
        logger.info("evaluation.events ingest consumer task scheduled")

        # T4 — deadline scanner (finaliza por timeout)
        app.state.deadline_scanner_task = asyncio.create_task(
            _run_deadline_scanner(app),
            name="deadline-scanner",
        )
        logger.info("deadline scanner task scheduled")

        # T2 — participants consumer (acumula segmentos por sessão p/ fan-out)
        app.state.participants_consumer_task = asyncio.create_task(
            _run_participants_consumer(app),
            name="participants-consumer",
        )
        logger.info("conversations.participants consumer task scheduled")

        # T15 — dispatcher por janela de calendário (§18.4)
        if settings.dispatch_scanner_enabled:
            app.state.dispatch_scanner_task = asyncio.create_task(
                _run_dispatch_scanner(app),
                name="dispatch-scanner",
            )
            logger.info("dispatch scanner task scheduled")

    @app.on_event("shutdown")
    async def shutdown() -> None:
        if hasattr(app.state, "workflow_consumer_task"):
            app.state.workflow_consumer_task.cancel()
        if hasattr(app.state, "sampling_consumer_task"):
            app.state.sampling_consumer_task.cancel()
        if hasattr(app.state, "ingest_consumer_task"):
            app.state.ingest_consumer_task.cancel()
        if hasattr(app.state, "deadline_scanner_task"):
            app.state.deadline_scanner_task.cancel()
        if hasattr(app.state, "participants_consumer_task"):
            app.state.participants_consumer_task.cancel()
        if hasattr(app.state, "dispatch_scanner_task"):
            app.state.dispatch_scanner_task.cancel()
        if hasattr(app.state, "kafka_producer"):
            await app.state.kafka_producer.stop()
        if hasattr(app.state, "redis"):
            await app.state.redis.aclose()
        if hasattr(app.state, "db_pool"):
            await app.state.db_pool.close()

    app.include_router(router)
    app.include_router(contestation_router)  # Arc 13 — contestation, curation, calibration
    return app


app = create_app()


def run() -> None:
    uvicorn.run("plughub_evaluation_api.main:app", host="0.0.0.0", port=settings.port, reload=False)


if __name__ == "__main__":
    run()
