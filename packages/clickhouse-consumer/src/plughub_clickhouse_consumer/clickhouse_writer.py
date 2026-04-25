"""
clickhouse_writer.py
Writes evaluation scores and items to ClickHouse.
Spec: clickhouse-consumer.md — Mapeamento evaluation.completed → tabelas
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any

import clickhouse_connect

logger = logging.getLogger("plughub.clickhouse-consumer.writer")

# ── DDL ───────────────────────────────────────────────────────────────────────

_DDL_SCORES = """
CREATE TABLE IF NOT EXISTS evaluation_scores (
    evaluation_id     UUID,
    contact_id        UUID,
    agent_id          UUID,
    agent_type        Enum8('human' = 1, 'ai' = 2),
    pool_id           String,
    skill_id          String,
    section_id        String,
    score_type        Enum8('base_score' = 1, 'context_score' = 2),
    score             Float32,
    triggered_by_key  Nullable(String),
    triggered_by_val  Nullable(String),
    evaluated_at      DateTime,
    triggered_by_src  String
) ENGINE = MergeTree()
  PARTITION BY toYYYYMM(evaluated_at)
  ORDER BY (pool_id, agent_id, section_id, evaluated_at)
"""

_DDL_ITEMS = """
CREATE TABLE IF NOT EXISTS evaluation_items (
    evaluation_id   UUID,
    contact_id      UUID,
    agent_id        UUID,
    pool_id         String,
    section_id      String,
    subsection_id   String,
    item_id         String,
    value           UInt8,
    weight          UInt8,
    justification   String,
    evaluated_at    DateTime
) ENGINE = MergeTree()
  PARTITION BY toYYYYMM(evaluated_at)
  ORDER BY (pool_id, agent_id, section_id, item_id, evaluated_at)
"""

_SCORES_COLUMNS = [
    "evaluation_id", "contact_id", "agent_id", "agent_type",
    "pool_id", "skill_id", "section_id", "score_type", "score",
    "triggered_by_key", "triggered_by_val", "evaluated_at", "triggered_by_src",
]

_ITEMS_COLUMNS = [
    "evaluation_id", "contact_id", "agent_id", "pool_id",
    "section_id", "subsection_id", "item_id", "value", "weight",
    "justification", "evaluated_at",
]


class ClickHouseWriter:
    def __init__(self, client: clickhouse_connect.driver.Client) -> None:
        self._client = client

    @classmethod
    def create(
        cls,
        host: str,
        port: int,
        database: str,
        username: str,
        password: str,
    ) -> "ClickHouseWriter":
        client = clickhouse_connect.get_client(
            host=host,
            port=port,
            database=database,
            username=username,
            password=password,
        )
        return cls(client=client)

    def migrate(self) -> None:
        """Create tables if they don't exist. Safe to call on startup."""
        self._client.command(_DDL_SCORES)
        self._client.command(_DDL_ITEMS)
        logger.info("ClickHouse schema ready")

    def write_evaluation(self, event: dict) -> tuple[int, int]:
        """
        Write all scores and items for a single evaluation.completed event.
        Returns (n_scores, n_items) inserted.

        All inserts happen in a single batch (per table) before the Kafka
        offset is committed — if this raises, the event will be reprocessed.
        Spec: clickhouse-consumer.md — Fluxo de processamento section
        """
        evaluated_at = _parse_dt(event.get("evaluated_at", ""))
        agent_id     = event.get("agent_id") or "00000000-0000-0000-0000-000000000000"

        score_rows: list[list[Any]] = []
        item_rows:  list[list[Any]] = []

        for score in event.get("scores", []):
            triggered_by = score.get("triggered_by") or {}
            tb_key = next(iter(triggered_by.keys()), None)
            tb_val = next(iter(triggered_by.values()), None) if tb_key else None

            score_rows.append([
                event["evaluation_id"],
                event["contact_id"],
                agent_id,
                event.get("agent_type", "ai"),
                event.get("pool_id", ""),
                event.get("skill_id", ""),
                score["section_id"],
                score["score_type"],
                float(score["score"]),
                tb_key,
                str(tb_val) if tb_val is not None else None,
                evaluated_at,
                event.get("triggered_by", ""),
            ])

            for sub in score.get("subsections", []):
                for item in sub.get("items", []):
                    item_rows.append([
                        event["evaluation_id"],
                        event["contact_id"],
                        agent_id,
                        event.get("pool_id", ""),
                        score["section_id"],
                        sub["subsection_id"],
                        item["item_id"],
                        int(item["value"]),
                        int(item["weight"]),
                        item.get("justification", ""),
                        evaluated_at,
                    ])

        if score_rows:
            self._client.insert(
                "evaluation_scores",
                score_rows,
                column_names=_SCORES_COLUMNS,
            )

        if item_rows:
            self._client.insert(
                "evaluation_items",
                item_rows,
                column_names=_ITEMS_COLUMNS,
            )

        logger.info(
            "Persisted evaluation_id=%s scores=%d items=%d",
            event.get("evaluation_id"), len(score_rows), len(item_rows),
        )
        return len(score_rows), len(item_rows)


def _parse_dt(ts: str) -> datetime:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.replace(tzinfo=None)  # ClickHouse DateTime is naive UTC
    except Exception:
        return datetime.utcnow()
