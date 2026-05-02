"""
stream_persister.py
Stream Persister — persiste o stream canônico no PostgreSQL ao session_closed.

Responsabilidade única:
  conversations.session_closed → lê session:{id}:stream do Redis →
  escreve em session_stream_events (PostgreSQL) com retention conforme AuditPolicy.

A persistência garante que o Replayer possa operar mesmo após o TTL do Redis expirar.
O Stream Hydrator usará estes dados para reconstruir o Redis quando necessário.

Tabela PostgreSQL:
  session_stream_events (
    id            BIGSERIAL PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    session_id    TEXT NOT NULL,
    event_id      TEXT NOT NULL UNIQUE,
    event_type    TEXT NOT NULL,
    timestamp     TIMESTAMPTZ NOT NULL,
    author        JSONB,
    visibility    JSONB,
    payload       JSONB NOT NULL DEFAULT '{}',
    original_content JSONB,
    masked_categories TEXT[] DEFAULT '{}',
    delta_ms      FLOAT DEFAULT 0,
    persisted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
  );
  CREATE INDEX ON session_stream_events (tenant_id, session_id);
  CREATE INDEX ON session_stream_events (session_id, timestamp);
"""

from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS session_stream_events (
    id                BIGSERIAL PRIMARY KEY,
    tenant_id         TEXT NOT NULL,
    session_id        TEXT NOT NULL,
    event_id          TEXT NOT NULL,
    event_type        TEXT NOT NULL,
    timestamp         TIMESTAMPTZ NOT NULL,
    author            JSONB,
    visibility        JSONB,
    payload           JSONB NOT NULL DEFAULT '{}',
    original_content  JSONB,
    masked_categories TEXT[] DEFAULT '{}',
    delta_ms          FLOAT DEFAULT 0,
    persisted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, session_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_sse_session ON session_stream_events (tenant_id, session_id);
CREATE INDEX IF NOT EXISTS idx_sse_ts      ON session_stream_events (session_id, timestamp);
"""


class StreamPersister:
    """
    Lê session:{id}:stream do Redis e persiste no PostgreSQL.
    Chamado uma vez por sessão após session_closed.
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,
        pg_pool:      asyncpg.Pool,
    ) -> None:
        self._redis = redis_client
        self._pg    = pg_pool

    async def ensure_schema(self) -> None:
        """Cria tabela e índices se não existirem."""
        async with self._pg.acquire() as conn:
            await conn.execute(CREATE_TABLE_SQL)

    async def persist(self, session_id: str, tenant_id: str) -> int:
        """
        Lê o stream do Redis e persiste no PostgreSQL.
        Retorna o número de eventos persistidos.
        Idempotente: eventos duplicados são ignorados (ON CONFLICT DO NOTHING).
        """
        stream_key = f"session:{session_id}:stream"

        # XRANGE retorna lista de (id, {field: value, ...})
        try:
            entries: list[tuple[bytes, dict[bytes, bytes]]] = await self._redis.xrange(
                stream_key, "-", "+"
            )
        except Exception as exc:
            logger.warning("StreamPersister: failed to read stream %s: %s", stream_key, exc)
            return 0

        if not entries:
            logger.info("StreamPersister: stream %s is empty or expired", stream_key)
            return 0

        records = self._parse_entries(entries)
        if not records:
            return 0

        # Calcula delta_ms entre eventos consecutivos
        for i, rec in enumerate(records):
            if i == 0:
                rec["delta_ms"] = 0.0
            else:
                prev_ts = records[i - 1]["timestamp"]
                curr_ts = rec["timestamp"]
                delta = (curr_ts - prev_ts).total_seconds() * 1000
                rec["delta_ms"] = max(0.0, delta)

        async with self._pg.acquire() as conn:
            inserted = 0
            for rec in records:
                result = await conn.execute(
                    """
                    INSERT INTO session_stream_events
                        (tenant_id, session_id, event_id, event_type, timestamp,
                         author, visibility, payload, original_content,
                         masked_categories, delta_ms)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
                    ON CONFLICT (tenant_id, session_id, event_id) DO NOTHING
                    """,
                    tenant_id,
                    session_id,
                    rec["event_id"],
                    rec["event_type"],
                    rec["timestamp"],
                    json.dumps(rec["author"])           if rec["author"]           else None,
                    json.dumps(rec["visibility"])       if rec["visibility"]       else None,
                    json.dumps(rec["payload"]),
                    json.dumps(rec["original_content"]) if rec["original_content"] else None,
                    rec["masked_categories"],
                    rec["delta_ms"],
                )
                if result == "INSERT 0 1":
                    inserted += 1

        logger.info(
            "StreamPersister: persisted %d/%d events for session %s",
            inserted, len(records), session_id
        )
        return inserted

    # ─────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────

    @staticmethod
    def _parse_entries(
        entries: list[tuple[bytes, dict[bytes, bytes]]]
    ) -> list[dict[str, Any]]:
        """Converte entradas brutas do Redis Stream para dicts normalizados."""
        from datetime import datetime, timezone

        records: list[dict[str, Any]] = []
        for _stream_id, fields in entries:
            decoded: dict[str, Any] = {}
            for k, v in fields.items():
                key = k.decode() if isinstance(k, bytes) else k
                val = v.decode() if isinstance(v, bytes) else v
                try:
                    decoded[key] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    decoded[key] = val

            # Timestamp: usa o campo "timestamp" do evento ou deriva do stream id
            raw_ts = decoded.get("timestamp")
            if raw_ts:
                try:
                    ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                except ValueError:
                    ts = datetime.now(timezone.utc)
            else:
                ts = datetime.now(timezone.utc)

            records.append({
                "event_id":         decoded.get("event_id", str(_stream_id)),
                "event_type":       decoded.get("type", "unknown"),
                "timestamp":        ts,
                "author":           decoded.get("author"),
                "visibility":       decoded.get("visibility"),
                "payload":          decoded.get("payload", {}),
                "original_content": decoded.get("original_content"),
                "masked_categories": decoded.get("masked_categories", []),
                "delta_ms":         0.0,
            })
        return records
