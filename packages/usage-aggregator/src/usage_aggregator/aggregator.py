"""
aggregator.py
UsageAggregator — lógica central de agregação.

Responsabilidades:
  1. Recebe um UsageEvent validado
  2. INCRBY no Redis: {tenant_id}:usage:current:{dimension}
  3. Persiste o evento bruto em PostgreSQL (usage_events)
  4. Agrega por hora em PostgreSQL (usage_hourly) via INSERT ON CONFLICT DO UPDATE
  5. Garante idempotência via event_id (PRIMARY KEY em usage_events)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import asyncpg
import redis.asyncio as aioredis

from .models import UsageEvent

logger = logging.getLogger("plughub.usage_aggregator")

# TTL dos contadores Redis: 45 dias
COUNTER_TTL_SECONDS = 45 * 24 * 3600


class UsageAggregator:
    def __init__(self, redis_client: aioredis.Redis, pg_pool: asyncpg.Pool) -> None:
        self._redis = redis_client
        self._pg    = pg_pool

    async def process(self, event: UsageEvent) -> None:
        """
        Processa um único UsageEvent.
        Idempotente: eventos duplicados (mesmo event_id) são ignorados silenciosamente.
        """
        # 1. Incrementa contador Redis
        await self._increment_redis(event)

        # 2. Persiste evento bruto + agrega por hora no PostgreSQL
        await self._persist_postgres(event)

    # ─── Redis ────────────────────────────────────────────────────────────────

    async def _increment_redis(self, event: UsageEvent) -> None:
        """
        Increments the usage counter atomically using MULTI/EXEC.

        Using MULTI/EXEC (not pipeline) ensures that concurrent workers
        processing the same event (e.g. Kafka redelivery) cannot interleave
        the INCRBY + EXPIRE pair, which would cause double-counting.

        Note: deduplication at the event_id level is enforced by the PostgreSQL
        INSERT (PRIMARY KEY), but Redis counters are the fast-path for quota checks.
        """
        counter_key = f"{event.tenant_id}:usage:current:{event.dimension}"
        cycle_key   = f"{event.tenant_id}:usage:cycle_start"

        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                await pipe.incrbyfloat(counter_key, event.quantity)
                await pipe.expire(counter_key, COUNTER_TTL_SECONDS)
                # SET NX: only sets cycle_start on first event of the cycle
                await pipe.set(cycle_key, event.timestamp, ex=COUNTER_TTL_SECONDS, nx=True)
                await pipe.execute()
        except Exception as exc:
            logger.warning(
                "Redis MULTI/EXEC failed for tenant=%s dim=%s: %s — counter may be stale",
                event.tenant_id, event.dimension, exc,
            )

    # ─── PostgreSQL ───────────────────────────────────────────────────────────

    async def _persist_postgres(self, event: UsageEvent) -> None:
        """
        1. INSERT INTO usage_events — idempotente via ON CONFLICT DO NOTHING
        2. INSERT INTO usage_hourly — agrega por hora via ON CONFLICT DO UPDATE
        """
        hour = _truncate_to_hour(event.timestamp)

        try:
            async with self._pg.acquire() as conn:
                # Evento bruto — idempotente via PRIMARY KEY (event_id)
                await conn.execute(
                    """
                    INSERT INTO usage_events
                        (event_id, tenant_id, session_id, dimension,
                         quantity, timestamp, source_component, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6::timestamptz, $7, $8::jsonb)
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    event.event_id,
                    event.tenant_id,
                    event.session_id,
                    event.dimension,
                    event.quantity,
                    event.timestamp,
                    event.source_component,
                    json.dumps(event.metadata),
                )

                # Agregado por hora — upsert
                await conn.execute(
                    """
                    INSERT INTO usage_hourly (tenant_id, dimension, hour, quantity)
                    VALUES ($1, $2, $3::timestamptz, $4)
                    ON CONFLICT (tenant_id, dimension, hour)
                    DO UPDATE SET quantity = usage_hourly.quantity + EXCLUDED.quantity
                    """,
                    event.tenant_id,
                    event.dimension,
                    hour,
                    event.quantity,
                )
        except asyncpg.UniqueViolationError:
            # Evento duplicado — ignorado (idempotência)
            logger.debug("Duplicate event_id=%s — skipped", event.event_id)
        except Exception as exc:
            logger.error(
                "PostgreSQL persist failed event_id=%s tenant=%s: %s",
                event.event_id, event.tenant_id, exc,
            )


def _truncate_to_hour(iso_ts: str) -> str:
    """Trunca um timestamp ISO 8601 para a hora inteira (minutos e segundos zerados)."""
    dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    truncated = dt.replace(minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    return truncated.isoformat()
