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
import uuid
from datetime import datetime, timezone

import asyncpg
import redis.asyncio as aioredis
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from .models import EvaluationRequest, SessionClosedEvent
from .replayer import Replayer
from .stream_hydrator import StreamHydrator, StreamNotAvailableError
from .stream_persister import StreamPersister

logger = logging.getLogger(__name__)


class SessionReplayerConsumer:
    """
    Orquestra os dois consumers Kafka e os componentes do pipeline.
    """

    def __init__(self) -> None:
        self._kafka_brokers     = os.getenv("KAFKA_BROKERS", "localhost:9092")
        self._redis_url         = os.getenv("REDIS_URL",     "redis://localhost:6379")
        self._postgres_dsn      = os.getenv("DATABASE_URL",  "postgresql://plughub:plughub@localhost:5432/plughub")
        self._evaluator_pool    = os.getenv("EVALUATOR_POOL", "avaliador_qualidade")
        self._default_speed     = float(os.getenv("REPLAY_SPEED_FACTOR", "10.0"))
        self._group_id_persister = "session-replayer-persister"
        self._group_id_replayer  = "session-replayer-replayer"

        self._redis:     aioredis.Redis | None = None
        self._pg_pool:   asyncpg.Pool   | None = None
        self._producer:  AIOKafkaProducer | None = None

    async def start(self) -> None:
        """Inicializa infra e inicia os dois consumers em paralelo."""
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

        hydrator = StreamHydrator(self._redis, self._pg_pool)
        replayer = Replayer(
            redis_client   = self._redis,
            hydrator       = hydrator,
            evaluator_pool = req.evaluator_pool,
            default_speed  = req.speed_factor,
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
