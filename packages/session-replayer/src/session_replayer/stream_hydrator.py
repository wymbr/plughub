"""
stream_hydrator.py
Stream Hydrator — garante que o Redis está populado antes do Replayer rodar.

Padrão: ensure-before-read.
  - Redis hit  → retorna imediatamente (no-op)
  - Redis miss → lê PostgreSQL → reconstrói stream no Redis com TTL curto
                → Replayer sempre lê do Redis, independente da origem

O Replayer não sabe se os dados vieram do Redis original (hot) ou do PostgreSQL (cold).
A diferença fica registrada apenas em ReplayContext.source para observabilidade.

TTL de hydration: HYDRATION_TTL_SECONDS (default 3600 = 1h)
  Suficiente para cobrir a avaliação + margem. Não interfere com o TTL original
  da sessão (que pode já ter expirado).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Literal

import asyncpg
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

HYDRATION_TTL_SECONDS = 3600  # 1h — cobre avaliação + margem


class StreamHydrator:
    """
    Verifica se session:{id}:stream existe no Redis.
    Se não, lê de PostgreSQL e reconstrói as chaves com TTL de hydration.
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,
        pg_pool:      asyncpg.Pool,
    ) -> None:
        self._redis = redis_client
        self._pg    = pg_pool

    async def ensure(
        self,
        session_id: str,
        tenant_id:  str,
    ) -> Literal["redis", "postgres"]:
        """
        Garante que session:{id}:stream está disponível no Redis.

        Retorna:
          "redis"    — stream já estava presente (hot path)
          "postgres" — stream foi reconstruído do PostgreSQL (cold path)

        Raises StreamNotAvailableError se o stream não existe em nenhuma fonte.
        """
        stream_key = f"session:{session_id}:stream"

        # ── Hot path: Redis já tem o stream ──────────────────────────────────
        try:
            length = await self._redis.xlen(stream_key)
            if length > 0:
                logger.debug("StreamHydrator: hit Redis for session %s (%d events)", session_id, length)
                return "redis"
        except Exception as exc:
            logger.warning("StreamHydrator: Redis xlen failed for %s: %s", session_id, exc)

        # ── Cold path: lê PostgreSQL e reconstrói Redis ──────────────────────
        logger.info("StreamHydrator: Redis miss for session %s — hydrating from PostgreSQL", session_id)

        rows = await self._fetch_from_postgres(session_id, tenant_id)
        if not rows:
            raise StreamNotAvailableError(
                f"Stream for session {session_id} not found in Redis or PostgreSQL"
            )

        await self._rebuild_redis_stream(stream_key, rows)

        logger.info(
            "StreamHydrator: hydrated %d events for session %s (TTL=%ds)",
            len(rows), session_id, HYDRATION_TTL_SECONDS
        )
        return "postgres"

    # ─────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────

    async def _fetch_from_postgres(
        self,
        session_id: str,
        tenant_id:  str,
    ) -> list[asyncpg.Record]:
        async with self._pg.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT event_id, event_type, timestamp, author, visibility,
                       payload, original_content, masked_categories, delta_ms
                FROM   session_stream_events
                WHERE  tenant_id  = $1
                  AND  session_id = $2
                ORDER BY timestamp ASC, id ASC
                """,
                tenant_id,
                session_id,
            )
        return list(rows)

    async def _rebuild_redis_stream(
        self,
        stream_key: str,
        rows:       list[asyncpg.Record],
    ) -> None:
        """
        Reconstrói session:{id}:stream no Redis a partir de rows do PostgreSQL.
        Usa XADD com IDs auto-gerados (*) — a ordem é garantida pela iteração.
        Seta TTL via EXPIRE após a reconstrução.
        """
        pipe = self._redis.pipeline()

        for row in rows:
            ts: datetime = row["timestamp"]
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)

            # Serializa campos opcionais
            author_val           = row["author"]           or "{}"
            visibility_val       = row["visibility"]       or '"all"'
            original_content_val = row["original_content"]

            fields: dict[str, str] = {
                "event_id":         row["event_id"],
                "type":             row["event_type"],
                "timestamp":        ts.isoformat(),
                "author":           author_val if isinstance(author_val, str) else json.dumps(author_val),
                "visibility":       visibility_val if isinstance(visibility_val, str) else json.dumps(visibility_val),
                "payload":          row["payload"] if isinstance(row["payload"], str) else json.dumps(row["payload"]),
                "masked_categories": json.dumps(row["masked_categories"] or []),
                "delta_ms":         str(row["delta_ms"] or 0.0),
            }

            if original_content_val:
                fields["original_content"] = (
                    original_content_val
                    if isinstance(original_content_val, str)
                    else json.dumps(original_content_val)
                )

            pipe.xadd(stream_key, fields)  # type: ignore[arg-type]

        pipe.expire(stream_key, HYDRATION_TTL_SECONDS)
        await pipe.execute()


class StreamNotAvailableError(Exception):
    """Raised when stream is not found in Redis or PostgreSQL."""
