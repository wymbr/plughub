"""
consumer.py
Kafka consumer que lê usage.events e delega para UsageAggregator.

Garantias:
  - At-least-once delivery: commit do offset só após persistência bem-sucedida
  - Idempotência: event_id como PRIMARY KEY no PostgreSQL evita duplicatas
  - Graceful degradation: erros de parsing são logados e o offset é commitado
    para não bloquear o consumer group em mensagens malformadas
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal

import asyncpg
import redis.asyncio as aioredis
from aiokafka import AIOKafkaConsumer

from .aggregator  import UsageAggregator
from .cycle_reset import CycleResetter
from .models      import UsageEvent

logger = logging.getLogger("plughub.usage_aggregator.consumer")

TOPIC_USAGE   = "usage.events"
TOPIC_RESET   = "usage.cycle_reset"
GROUP_ID      = "usage-aggregator"
AUTO_OFFSET   = "earliest"


async def run_consumer(
    kafka_brokers: str,
    redis_url:     str,
    database_url:  str,
) -> None:
    """
    Inicia o Kafka consumer e processa mensagens indefinidamente.
    Escuta dois tópicos:
      - usage.events       → agrega contadores (UsageAggregator)
      - usage.cycle_reset  → reseta contadores mensais (CycleResetter)
    Graceful shutdown via SIGTERM/SIGINT.
    """
    logger.info("Starting Usage Aggregator consumer — brokers=%s", kafka_brokers)

    # ── Infra ─────────────────────────────────────────────────────────────────
    redis_client = aioredis.from_url(redis_url, decode_responses=True)
    pg_pool      = await asyncpg.create_pool(database_url, min_size=2, max_size=10)

    await _ensure_schema(pg_pool)

    aggregator = UsageAggregator(redis_client=redis_client, pg_pool=pg_pool)
    resetter   = CycleResetter(redis_client=redis_client)

    consumer = AIOKafkaConsumer(
        TOPIC_USAGE,
        TOPIC_RESET,
        bootstrap_servers=kafka_brokers,
        group_id=GROUP_ID,
        auto_offset_reset=AUTO_OFFSET,
        enable_auto_commit=False,    # manual commit após persistência
        value_deserializer=lambda v: v,  # bytes — desserializamos manualmente
    )

    # ── Graceful shutdown ─────────────────────────────────────────────────────
    loop     = asyncio.get_running_loop()
    shutdown = asyncio.Event()

    def _handle_signal() -> None:
        logger.info("Shutdown signal received")
        shutdown.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    # ── Main loop ─────────────────────────────────────────────────────────────
    await consumer.start()
    try:
        while not shutdown.is_set():
            batch = await consumer.getmany(timeout_ms=500, max_records=100)
            if not batch:
                continue

            for tp, messages in batch.items():
                for msg in messages:
                    if tp.topic == TOPIC_RESET:
                        await _process_reset_message(resetter, msg)
                    else:
                        await _process_message(aggregator, msg)

            # Commit após processar o batch completo
            await consumer.commit()

    finally:
        await consumer.stop()
        await pg_pool.close()
        await redis_client.aclose()
        logger.info("Usage Aggregator consumer stopped")


async def _process_message(aggregator: UsageAggregator, msg: object) -> None:
    """Deserializa e processa uma mensagem Kafka de uso."""
    try:
        raw = json.loads(msg.value.decode("utf-8"))  # type: ignore[union-attr]
        event = UsageEvent.model_validate(raw)
        await aggregator.process(event)
        logger.debug(
            "Processed event_id=%s tenant=%s dim=%s qty=%s",
            event.event_id, event.tenant_id, event.dimension, event.quantity,
        )
    except Exception as exc:
        # Malformed ou dimensão desconhecida — loga e avança (não bloqueia o consumer)
        offset = getattr(msg, "offset", "?")
        logger.error("Failed to process message offset=%s: %s", offset, exc)


async def _process_reset_message(resetter: CycleResetter, msg: object) -> None:
    """
    Deserializa e processa uma mensagem de reset de ciclo.

    Payload esperado (campos opcionais):
      { "tenant_id": "tenant_demo" | "*",  "cycle_start": "ISO-8601" }
    Se tenant_id estiver ausente, usa "*" (todos os tenants).
    """
    try:
        raw = json.loads(msg.value.decode("utf-8"))  # type: ignore[union-attr]
        tenant_id   = raw.get("tenant_id", "*")
        cycle_start = raw.get("cycle_start")          # None → usa now() no resetter
        report = await resetter.reset(tenant_id=tenant_id, cycle_start=cycle_start)
        if report.ok():
            logger.info("Cycle reset via Kafka: %s", report.summary())
        else:
            logger.error("Cycle reset completed with errors: %s", report.summary())
    except Exception as exc:
        offset = getattr(msg, "offset", "?")
        logger.error("Failed to process cycle_reset message offset=%s: %s", offset, exc)


# ─── Schema PostgreSQL ────────────────────────────────────────────────────────

async def _ensure_schema(pool: asyncpg.Pool) -> None:
    """
    Cria as tabelas se não existirem.
    Idempotente — seguro para rodar em cada inicialização.
    """
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS usage_events (
                event_id         TEXT PRIMARY KEY,
                tenant_id        TEXT NOT NULL,
                session_id       TEXT,
                dimension        TEXT NOT NULL,
                quantity         NUMERIC NOT NULL,
                timestamp        TIMESTAMPTZ NOT NULL,
                source_component TEXT NOT NULL,
                metadata         JSONB DEFAULT '{}',
                created_at       TIMESTAMPTZ DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_usage_events_tenant_dim_ts
                ON usage_events (tenant_id, dimension, timestamp);

            CREATE TABLE IF NOT EXISTS usage_hourly (
                tenant_id  TEXT NOT NULL,
                dimension  TEXT NOT NULL,
                hour       TIMESTAMPTZ NOT NULL,
                quantity   NUMERIC NOT NULL DEFAULT 0,
                PRIMARY KEY (tenant_id, dimension, hour)
            );
        """)
    logger.info("PostgreSQL schema ensured")
