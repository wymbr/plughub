"""
postgres_writer.py
PostgreSQL persistence layer for the Conversation Writer.
Writes to `transcripts` and `transcript_messages` tables.
Uses asyncpg for async PostgreSQL access.
Spec: conversation-writer.md — Persistência em PostgreSQL section
"""

from __future__ import annotations
import json
import logging
import uuid
from datetime import datetime, timezone

import asyncpg

from .models import ContactMeta, InboundMessage

logger = logging.getLogger("plughub.conversation-writer.postgres")

# ── DDL (reference — run once via migration) ─────────────────────────────────
DDL = """
CREATE TABLE IF NOT EXISTS transcripts (
    transcript_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id      UUID NOT NULL UNIQUE,
    pool_id         VARCHAR,
    agent_id        UUID,
    agent_type      VARCHAR,
    started_at      TIMESTAMPTZ NOT NULL,
    ended_at        TIMESTAMPTZ NOT NULL,
    outcome         VARCHAR,
    turn_count      INTEGER NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS transcript_messages (
    id              BIGSERIAL PRIMARY KEY,
    transcript_id   UUID NOT NULL REFERENCES transcripts(transcript_id),
    message_id      UUID NOT NULL,
    turn_number     INTEGER NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL,
    direction       VARCHAR NOT NULL,
    author_type     VARCHAR NOT NULL,
    author_id       UUID,
    display_name    VARCHAR,
    content_type    VARCHAR NOT NULL,
    content_text    TEXT,
    content_payload JSONB,
    intent          VARCHAR,
    sentiment_score FLOAT
);
CREATE INDEX IF NOT EXISTS idx_transcript_messages_transcript_turn
    ON transcript_messages(transcript_id, turn_number);
"""


class PostgresWriter:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def create(cls, dsn: str) -> "PostgresWriter":
        pool = await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=10)
        return cls(pool=pool)

    async def close(self) -> None:
        await self._pool.close()

    async def migrate(self) -> None:
        """Create tables if they don't exist. Safe to call on every startup."""
        async with self._pool.acquire() as conn:
            await conn.execute(DDL)
        logger.info("PostgreSQL schema ready")

    async def persist_transcript(
        self,
        meta: ContactMeta,
        messages: list[InboundMessage],
        ended_at: str,
    ) -> str:
        """
        Persist the full transcript for a contact.
        Returns the transcript_id (UUID string).

        Messages are sorted by timestamp before insertion.
        Runs as a single transaction — if any insert fails, nothing is committed.
        """
        transcript_id = str(uuid.uuid4())

        # Sort messages chronologically
        sorted_messages = sorted(messages, key=lambda m: m.timestamp)
        turn_count = len(sorted_messages)

        started_at = meta.started_at or _now_iso()
        ended_at_  = meta.ended_at or ended_at

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Insert transcript header
                await conn.execute(
                    """
                    INSERT INTO transcripts
                        (transcript_id, contact_id, pool_id, agent_id, agent_type,
                         started_at, ended_at, outcome, turn_count)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (contact_id) DO NOTHING
                    """,
                    uuid.UUID(transcript_id),
                    uuid.UUID(meta.contact_id),
                    meta.pool_id,
                    uuid.UUID(meta.agent_id) if meta.agent_id else None,
                    meta.agent_type,
                    _parse_ts(started_at),
                    _parse_ts(ended_at_),
                    meta.outcome,
                    turn_count,
                )

                # Insert messages
                for i, msg in enumerate(sorted_messages):
                    await conn.execute(
                        """
                        INSERT INTO transcript_messages
                            (transcript_id, message_id, turn_number, timestamp,
                             direction, author_type, author_id, display_name,
                             content_type, content_text, content_payload,
                             intent, sentiment_score)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                        """,
                        uuid.UUID(transcript_id),
                        uuid.UUID(msg.message_id),
                        i + 1,
                        _parse_ts(msg.timestamp),
                        msg.direction,
                        msg.author.type,
                        uuid.UUID(msg.author.id) if msg.author.id else None,
                        msg.author.display_name,
                        msg.content.type,
                        msg.content.text,
                        json.dumps(msg.content.payload) if msg.content.payload else None,
                        msg.context_snapshot.intent,
                        msg.context_snapshot.sentiment_score,
                    )

        logger.info(
            "transcript persisted transcript_id=%s contact_id=%s messages=%d",
            transcript_id, meta.contact_id, turn_count,
        )
        return transcript_id


def _parse_ts(ts: str) -> datetime:
    """Parse ISO 8601 timestamp to aware datetime."""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(timezone.utc)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
