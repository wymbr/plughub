"""
consumer.py
Kafka consumer orchestrator do Session Replayer.

Consome dois tópicos:
  1. conversations.session_closed → Stream Persister → persistência imediata no PostgreSQL
  2. evaluation.requested         → Stream Hydrator + Replayer → ReplayContext no Redis

A separação em dois consumers independentes garante que:
  - A persistência (Persister) acontece independente de haver um evaluator disponível
  - O replay só é iniciado quando explicitamente solicitado
  - Falhas no replay não impedem a persistência (e vice-versa)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request
import uuid
from datetime import datetime, timezone

import asyncpg
import redis.asyncio as aioredis
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from .models import EvaluationRequest, SessionClosedEvent
from .replayer import Replayer, REPLAY_CONTEXT_TTL
from .stream_hydrator import StreamHydrator, StreamNotAvailableError, HYDRATION_TTL_SECONDS
from .stream_persister import StreamPersister
from .pipeline_persister import PipelineStatePersister
from .import_stream_consumer import ImportStreamConsumer

logger = logging.getLogger(__name__)


async def _fetch_config_value(
    config_api_url: str, namespace: str, key: str, tenant_id: str, default,
):
    """
    Fetches a single value from a Config API namespace at startup.
    GET /config/{namespace}?tenant_id=... — resolves tenant override → global default.
    Uses urllib (no extra dependency) in a thread executor to stay non-blocking.
    Falls back to `default` on any error / missing key.
    """
    url = f"{config_api_url.rstrip('/')}/config/{namespace}?tenant_id={tenant_id}"
    loop = asyncio.get_event_loop()

    def _get():
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
            body = json.loads(resp.read())
            entries = body.get("entries") or body
            entry = entries.get(key)
            if isinstance(entry, dict) and "value" in entry:
                return entry["value"]
            return entry

    try:
        result = await loop.run_in_executor(None, _get)
        if result is not None:
            logger.info("Config API %s.%s=%s", namespace, key, result)
            return result
    except Exception as exc:
        logger.warning(
            "Could not fetch %s.%s from Config API (%s) — using default %s: %s",
            namespace, key, url, default, exc,
        )
    return default


async def _fetch_config_ttl(
    config_api_url: str, key: str, tenant_id: str, default: int
) -> int:
    """Thin int wrapper over _fetch_config_value for the `session` namespace TTLs."""
    val = await _fetch_config_value(config_api_url, "session", key, tenant_id, default)
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


async def _fetch_evaluation_form(
    eval_api_url: str, form_id: str, tenant_id: str
) -> dict | None:
    """
    GET /v1/evaluation/forms/{form_id}?tenant_id=... — fetches the EvaluationForm so the
    Replayer can inject it into the ReplayContext (Arc 6). Public-read endpoint, no auth.
    Uses urllib in a thread executor to stay non-blocking. Returns None on any error.
    """
    loop = asyncio.get_event_loop()
    url = f"{eval_api_url.rstrip('/')}/v1/evaluation/forms/{form_id}?tenant_id={tenant_id}"

    def _get():
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
            return json.loads(resp.read())

    try:
        form = await loop.run_in_executor(None, _get)
        if isinstance(form, dict):
            logger.info("Replayer: fetched evaluation_form %s for ReplayContext", form_id)
            return form
    except Exception as exc:
        logger.warning(
            "Replayer: could not fetch evaluation_form %s from %s — %s",
            form_id, url, exc,
        )
    return None


class SessionReplayerConsumer:
    """
    Orquestra os dois consumers Kafka e os componentes do pipeline.
    """

    def __init__(self) -> None:
        self._kafka_brokers      = os.getenv("KAFKA_BROKERS",   "localhost:9092")
        self._redis_url          = os.getenv("REDIS_URL",        "redis://localhost:6379")
        self._postgres_dsn       = os.getenv("DATABASE_URL",     "postgresql://plughub:plughub@localhost:5432/plughub")
        self._config_api_url     = os.getenv("CONFIG_API_URL",   "http://localhost:3600")
        self._eval_api_url       = os.getenv("EVALUATION_API_URL", "http://localhost:3400")
        self._tenant_id          = os.getenv("PLUGHUB_TENANT_ID", "tenant_demo")
        # config-consolidation item 7b: evaluator_pool + replay speed come from the
        # Config API `evaluation` namespace (fetched in start()); these are the
        # in-code fallback defaults. Was env EVALUATOR_POOL / REPLAY_SPEED_FACTOR.
        self._evaluator_pool     = "avaliacao_ia"
        self._default_speed      = 10.0
        self._group_id_persister = "session-replayer-persister"
        self._group_id_replayer  = "session-replayer-replayer"

        self._redis:            aioredis.Redis    | None = None
        self._pg_pool:          asyncpg.Pool      | None = None
        self._producer:         AIOKafkaProducer  | None = None
        self._import_consumer:  ImportStreamConsumer | None = None
        self._hydration_ttl:    int = HYDRATION_TTL_SECONDS
        self._replay_context_ttl: int = REPLAY_CONTEXT_TTL

    async def start(self) -> None:
        """Inicializa infra e inicia os dois consumers em paralelo."""
        # Load config from the Config API before starting consumers.
        self._hydration_ttl = await _fetch_config_ttl(
            self._config_api_url, "replayer_hydration_ttl_s", self._tenant_id, HYDRATION_TTL_SECONDS
        )
        self._replay_context_ttl = await _fetch_config_ttl(
            self._config_api_url, "replay_context_ttl_s", self._tenant_id, REPLAY_CONTEXT_TTL
        )
        # config-consolidation item 7b: evaluator pool + replay speed from the
        # `evaluation` namespace (was env EVALUATOR_POOL / REPLAY_SPEED_FACTOR).
        self._evaluator_pool = str(await _fetch_config_value(
            self._config_api_url, "evaluation", "evaluator_pool",
            self._tenant_id, self._evaluator_pool,
        ))
        try:
            self._default_speed = float(await _fetch_config_value(
                self._config_api_url, "evaluation", "replay_speed_factor",
                self._tenant_id, self._default_speed,
            ))
        except (TypeError, ValueError):
            pass

        self._redis   = aioredis.from_url(self._redis_url, decode_responses=False)
        self._pg_pool = await asyncpg.create_pool(self._postgres_dsn, min_size=2, max_size=10)
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._kafka_brokers,
            value_serializer=lambda v: json.dumps(v).encode(),
        )
        await self._producer.start()

        # Garante tabelas PostgreSQL
        persister = StreamPersister(self._redis, self._pg_pool)
        await persister.ensure_schema()
        pipeline_persister = PipelineStatePersister(self._redis, self._pg_pool)
        await pipeline_persister.ensure_schema()

        # R13b — consumer Y: rebuilds session_stream_events for IMPORTED contacts from
        # the canonical events (gated source=external_import), sharing the Persister's
        # writer. Closes the ReplayContext gap for imported sessions (no Redis stream).
        import_consumer = ImportStreamConsumer(self._kafka_brokers, persister)
        self._import_consumer = import_consumer

        logger.info("SessionReplayerConsumer: starting consumers")
        await asyncio.gather(
            self._run_persister_consumer(),
            self._run_replayer_consumer(),
            import_consumer.run(),
        )

    async def stop(self) -> None:
        if self._import_consumer:
            await self._import_consumer.stop()
        if self._producer:
            await self._producer.stop()
        if self._redis:
            await self._redis.aclose()
        if self._pg_pool:
            await self._pg_pool.close()

    # ─────────────────────────────────────────
    # Consumer 1: Stream Persister
    # Tópico: conversations.session_closed
    # ─────────────────────────────────────────

    async def _run_persister_consumer(self) -> None:
        consumer = AIOKafkaConsumer(
            "conversations.session_closed",
            bootstrap_servers=self._kafka_brokers,
            group_id=self._group_id_persister,
            auto_offset_reset="earliest",
            value_deserializer=lambda v: json.loads(v.decode()),
        )
        await consumer.start()
        logger.info("Persister consumer started (topic: conversations.session_closed)")

        try:
            async for msg in consumer:
                await self._handle_session_closed(msg.value)
        finally:
            await consumer.stop()

    async def _handle_session_closed(self, payload: dict) -> None:
        try:
            event = SessionClosedEvent(**payload)
        except Exception as exc:
            logger.warning("Persister: invalid session_closed payload: %s — %s", payload, exc)
            return

        logger.info("Persister: persisting stream for session %s", event.session_id)

        persister = StreamPersister(self._redis, self._pg_pool)
        try:
            # Self-healing (F2 bancada): garante a tabela antes de cada persist.
            # O boot já chama ensure_schema(), mas um reset de volume/banco com o
            # serviço de pé deixava o persist quebrando com "relation does not
            # exist" até o próximo restart. CREATE TABLE IF NOT EXISTS é barato.
            await persister.ensure_schema()
            count = await persister.persist(event.session_id, event.tenant_id)
            logger.info("Persister: %d events persisted for session %s", count, event.session_id)
        except Exception as exc:
            logger.error("Persister: failed for session %s: %s", event.session_id, exc)
            return

        # R5/B — snapshot durável da trajetória do skill-flow (pipeline_state).
        # Best-effort e independente do Stream Persister: falha aqui não deve
        # impedir a persistência do stream (já concluída acima).
        try:
            pipeline_persister = PipelineStatePersister(self._redis, self._pg_pool)
            await pipeline_persister.ensure_schema()
            await pipeline_persister.persist(event.session_id, event.tenant_id)
        except Exception as exc:
            logger.warning(
                "Persister: pipeline_state snapshot failed for session %s: %s",
                event.session_id, exc,
            )

        # NOTA (S2.1 — modelo campaign-driven): o Persister NÃO dispara mais
        # `evaluation.requested` no fechamento. Avaliação é dirigida por CAMPANHA:
        # a evaluation-api amostra a sessão (cria EvaluationInstance=scheduled) e um
        # dispatcher publica `evaluation.requested` na janela do calendário da
        # campanha. Aqui só persistimos o stream (necessário para o replay quando a
        # avaliação rodar mais tarde). Avaliar no fim do atendimento, se desejado,
        # é opt-in via pool hooks genéricos (on_segment_end/on_contact_end).

    # ─────────────────────────────────────────
    # Consumer 2: Replayer
    # Tópico: evaluation.events (event_type: evaluation.requested)
    # ─────────────────────────────────────────

    async def _run_replayer_consumer(self) -> None:
        consumer = AIOKafkaConsumer(
            "evaluation.events",
            bootstrap_servers=self._kafka_brokers,
            group_id=self._group_id_replayer,
            auto_offset_reset="earliest",
            value_deserializer=lambda v: json.loads(v.decode()),
        )
        await consumer.start()
        logger.info("Replayer consumer started (topic: evaluation.events)")

        try:
            async for msg in consumer:
                payload = msg.value
                if payload.get("event_type") != "evaluation.requested":
                    continue
                await self._handle_evaluation_requested(payload)
        finally:
            await consumer.stop()

    async def _handle_evaluation_requested(self, payload: dict) -> None:
        try:
            req = EvaluationRequest(**payload)
        except Exception as exc:
            logger.warning("Replayer: invalid evaluation.requested payload: %s — %s", payload, exc)
            return

        logger.info(
            "Replayer: preparing replay for session %s (eval_id=%s, speed=%.1fx)",
            req.session_id, req.evaluation_id, req.speed_factor,
        )

        hydrator = StreamHydrator(self._redis, self._pg_pool, ttl=self._hydration_ttl)
        replayer = Replayer(
            redis_client     = self._redis,
            hydrator         = hydrator,
            evaluator_pool   = req.evaluator_pool,
            default_speed    = req.speed_factor,
            context_ttl      = self._replay_context_ttl,
            # R5/B — durable trajectory fetch for ReplayContext.pipeline_state
            pipeline_fetcher = PipelineStatePersister(self._redis, self._pg_pool),
        )

        # Reconstrói o SessionClosedEvent mínimo necessário para o Replayer
        from .models import SessionClosedEvent as SCE
        closed_event = SCE(
            session_id = req.session_id,
            tenant_id  = req.tenant_id,
        )

        # Arc 6 — busca o EvaluationForm da campanha para injetar no ReplayContext.
        # Sem o form, o agente avaliador não tem critérios para pontuar.
        evaluation_form = None
        if req.form_id:
            evaluation_form = await _fetch_evaluation_form(
                self._eval_api_url, req.form_id, req.tenant_id,
            )

        try:
            # prepare() → hydration + leitura stream + escrita ReplayContext no Redis
            await replayer.prepare(
                event           = closed_event,
                speed_factor    = req.speed_factor,
                comparison_mode = req.comparison_mode,
                dimensions      = req.dimensions,
                # Arc 6 — campaign context
                form_id         = req.form_id,
                campaign_id     = req.campaign_id,
                instance_id     = req.instance_id,
                evaluation_form = evaluation_form,
            )
            logger.info(
                "Replayer: ReplayContext ready for session %s — evaluator pool=%s",
                req.session_id, req.evaluator_pool,
            )
            # O Routing Engine agora deve alocar um agente evaluator para a sessão.
            # O agente chama evaluation_context_get via MCP para receber o ReplayContext
            # e evaluation_submit para publicar o EvaluationResult.

        except StreamNotAvailableError as exc:
            logger.error("Replayer: stream not available for session %s: %s", req.session_id, exc)
        except Exception as exc:
            logger.exception("Replayer: unexpected error for session %s: %s", req.session_id, exc)
