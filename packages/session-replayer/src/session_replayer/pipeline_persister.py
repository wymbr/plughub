"""
pipeline_persister.py
Pipeline-State Persister — snapshots the skill-flow pipeline_state on session_closed.

Responsabilidade única:
  conversations.session_closed → lê {tenant}:pipeline:{session_id} do Redis →
  escreve as transitions (trajetória REAL executada) em session_pipeline_state
  (PostgreSQL), tornando-as duráveis além do TTL de 24h da chave no Redis.

Por quê (Arc — Métricas de Avaliação, R5/B): o pipeline_state vive só no Redis
(chave {tenant}:pipeline:{session_id}, TTL 24h) e as transições NÃO vão para o
stream canônico (invariante). Sem este snapshot, a trajetória real some quando a
avaliação roda tarde/backfill. Capturar no fechamento (mesmo gatilho do Stream
Persister) trava a trajetória de forma durável. A trajetória ESPERADA vem do
agent-registry (GET /v1/skills/:flow_id) no momento da avaliação — não aqui.

Substrato compartilhado: esta mesma tabela serve de fonte durável de step metrics
para o R4 (steps_completed/retried/durations) — capturado uma vez, consumido por
amostragem (policy adherence) e por métricas quantitativas.

Tabela PostgreSQL:
  session_pipeline_state (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    flow_id         TEXT,
    status          TEXT,
    current_step_id TEXT,
    transitions     JSONB NOT NULL DEFAULT '[]',
    retry_counters  JSONB NOT NULL DEFAULT '{}',
    error_context   JSONB,
    started_at      TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ,
    persisted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, session_id)
  );
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import asyncpg
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS session_pipeline_state (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    flow_id         TEXT,
    status          TEXT,
    current_step_id TEXT,
    transitions     JSONB NOT NULL DEFAULT '[]',
    retry_counters  JSONB NOT NULL DEFAULT '{}',
    error_context   JSONB,
    started_at      TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ,
    persisted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, session_id)
);
CREATE INDEX IF NOT EXISTS idx_sps_session ON session_pipeline_state (tenant_id, session_id);
"""


def _parse_ts(raw: Any) -> datetime | None:
    """Best-effort ISO-8601 → datetime; None on absence/parse error."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


class PipelineStatePersister:
    """
    Lê {tenant}:pipeline:{session_id} do Redis e persiste no PostgreSQL.
    Chamado uma vez por sessão após session_closed, junto ao Stream Persister.
    Best-effort por design: a ausência da chave (TTL vencido / sessão sem
    skill-flow) NÃO é erro — apenas não há trajetória durável a gravar.
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,
        pg_pool:      asyncpg.Pool,
    ) -> None:
        self._redis = redis_client
        self._pg    = pg_pool

    async def ensure_schema(self) -> None:
        async with self._pg.acquire() as conn:
            await conn.execute(CREATE_TABLE_SQL)

    @staticmethod
    def pipeline_key(tenant_id: str, session_id: str) -> str:
        # Espelha skill-flow-engine/src/state.ts PIPELINE_KEY.
        return f"{tenant_id}:pipeline:{session_id}"

    async def _read_pipeline(self, session_id: str, tenant_id: str) -> dict[str, Any] | None:
        """Lê e decodifica o pipeline_state do Redis. None se ausente/inválido."""
        try:
            raw = await self._redis.get(self.pipeline_key(tenant_id, session_id))
        except Exception as exc:
            logger.warning(
                "PipelineStatePersister: Redis read failed for %s: %s", session_id, exc
            )
            return None
        if not raw:
            return None
        try:
            if isinstance(raw, bytes):
                raw = raw.decode()
            state = json.loads(raw)
            return state if isinstance(state, dict) else None
        except Exception as exc:
            logger.warning(
                "PipelineStatePersister: invalid pipeline_state JSON for %s: %s",
                session_id, exc,
            )
            return None

    async def persist(self, session_id: str, tenant_id: str) -> bool:
        """
        Snapshot do pipeline_state no PostgreSQL (upsert). Retorna True se gravou.
        Idempotente: re-fechamento atualiza a linha (a trajetória pode ter crescido).
        """
        state = await self._read_pipeline(session_id, tenant_id)
        if state is None:
            logger.info(
                "PipelineStatePersister: no pipeline_state for session %s (no skill-flow "
                "or TTL expired) — nothing to persist", session_id,
            )
            return False

        transitions    = state.get("transitions") or []
        retry_counters = state.get("retry_counters") or {}
        error_context  = state.get("error_context")

        async with self._pg.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO session_pipeline_state
                    (tenant_id, session_id, flow_id, status, current_step_id,
                     transitions, retry_counters, error_context, started_at, updated_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                ON CONFLICT (tenant_id, session_id) DO UPDATE SET
                    flow_id         = EXCLUDED.flow_id,
                    status          = EXCLUDED.status,
                    current_step_id = EXCLUDED.current_step_id,
                    transitions     = EXCLUDED.transitions,
                    retry_counters  = EXCLUDED.retry_counters,
                    error_context   = EXCLUDED.error_context,
                    started_at      = EXCLUDED.started_at,
                    updated_at      = EXCLUDED.updated_at,
                    persisted_at    = NOW()
                """,
                tenant_id,
                session_id,
                state.get("flow_id"),
                state.get("status"),
                state.get("current_step_id"),
                json.dumps(transitions),
                json.dumps(retry_counters),
                json.dumps(error_context) if error_context else None,
                _parse_ts(state.get("started_at")),
                _parse_ts(state.get("updated_at")),
            )

        logger.info(
            "PipelineStatePersister: persisted pipeline_state for session %s "
            "(flow=%s, status=%s, %d transitions)",
            session_id, state.get("flow_id"), state.get("status"), len(transitions),
        )
        return True

    # ── Read path (durável → fallback Redis vivo) ─────────────────────────────

    async def fetch(self, session_id: str, tenant_id: str) -> dict[str, Any] | None:
        """
        Recupera a trajetória durável para o ReplayContext. Lê o PostgreSQL
        primeiro (imune ao TTL); se ausente (sessão fechada antes do ship deste
        persister, ou persist perdido), tenta a chave viva do Redis como
        best-effort. Retorna None quando não há trajetória em lugar nenhum →
        o critério policy-adherence vira `na` (decisão D).
        """
        try:
            async with self._pg.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT flow_id, status, current_step_id, transitions,
                           retry_counters, error_context, started_at, updated_at
                    FROM   session_pipeline_state
                    WHERE  tenant_id = $1 AND session_id = $2
                    """,
                    tenant_id, session_id,
                )
        except Exception as exc:
            logger.warning(
                "PipelineStatePersister: PG fetch failed for %s: %s", session_id, exc
            )
            row = None

        if row is not None:
            return {
                "flow_id":         row["flow_id"],
                "status":          row["status"],
                "current_step_id": row["current_step_id"],
                "transitions":     _as_json(row["transitions"], []),
                "retry_counters":  _as_json(row["retry_counters"], {}),
                "error_context":   _as_json(row["error_context"], None),
                "source":          "postgres",
            }

        # Fallback best-effort: chave viva do Redis (avaliação dentro da janela TTL).
        live = await self._read_pipeline(session_id, tenant_id)
        if live is not None:
            return {
                "flow_id":         live.get("flow_id"),
                "status":          live.get("status"),
                "current_step_id": live.get("current_step_id"),
                "transitions":     live.get("transitions") or [],
                "retry_counters":  live.get("retry_counters") or {},
                "error_context":   live.get("error_context"),
                "source":          "redis",
            }
        return None


def _as_json(val: Any, default: Any) -> Any:
    """asyncpg JSONB columns come back as str (or already-parsed)."""
    if val is None:
        return default
    if isinstance(val, (list, dict)):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default
