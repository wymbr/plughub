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


class SessionReplayerConsumer:
    """
    Orquestra os dois consumers Kafka e os componentes do pipeline.
    """

    def __init__(self) -> None:
        self._kafka_brokers      = os.getenv("KAFKA_BROKERS",   "localhost:9092")
        self._redis_url          = os.getenv("REDIS_URL",        "redis://localhost:6379")
        self._postgres_dsn       = os.getenv("DATABASE_URL",     "postgresql://plughub:plughub@localhost:5432/plughub")
        self._config_api_url     = os.getenv("CONFIG_API_URL",   "http://localhost:3600")
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

        # Garante tabela PostgreSQL
        persister = StreamPersister(self._redis, self._pg_pool)
        await persister.ensure_schema()

        logger.info("SessionReplayerConsumer: starting consumers")
        await asyncio.gather(
            self._run_persister_consumer(),
            self._run_replayer_consumer(),
        )

    async def stop(self) -> None:
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

        # Publica evaluation.requested para iniciar o pipeline de avaliação
        # O pool e as dimensões podem ser configurados por tenant via Agent Registry
        # (aqui usamos defaults do ambiente)
        req = EvaluationRequest(
            evaluation_id  = str(uuid.uuid4()),
            session_id     = event.session_id,
            tenant_id      = event.tenant_id,
            evaluator_pool = self._evaluator_pool,
            speed_factor   = self._default_speed,
            requested_at   = datetime.now(timezone.utc),
        )

        try:
            await self._producer.send_and_wait(
                "evaluation.events",
                value=req.model_dump(mode="json"),
            )
            logger.info(
                "Persister: evaluation.requested published for session %s (eval_id=%s)",
                event.session_id, req.evaluation_id,
            )
        except Exception as exc:
            logger.error("Persister: failed to publish evaluation.requested: %s", exc)

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
            redis_client   = self._redis,
            hydrator       = hydrator,
            evaluator_pool = req.evaluator_pool,
            default_speed  = req.speed_factor,
            context_ttl    = self._replay_context_ttl,
        )

        # Reconstrói o SessionClosedEvent mínimo necessário para o Replayer
        from .models import SessionClosedEvent as SCE
        closed_event = SCE(
            session_id = req.session_id,
            tenant_id  = req.tenant_id,
        )

        try:
            # prepare() → hydration + leitura stream + escrita ReplayContext no Redis
            await replayer.prepare(
                event           = closed_event,
                speed_factor    = req.speed_factor,
                comparison_mode = req.comparison_mode,
                dimensions      = req.dimensions,
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
