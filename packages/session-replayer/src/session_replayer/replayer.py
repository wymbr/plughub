"""
replayer.py
Replayer — lê o stream do Redis e constrói o ReplayContext para o evaluator.

Responsabilidade única:
  - Lê session:{id}:stream (sempre do Redis — o Hydrator garante disponibilidade)
  - Reconstrói a conversa com delta_ms fiel ao timing original
  - Escreve ReplayContext em {tenant_id}:replay:{session_id}:context (Redis, TTL 1h)
  - Publica evaluation.requested em evaluation.events (Kafka)

O Replayer NÃO executa a avaliação — apenas prepara o contexto.
A avaliação é feita pelo agente evaluator via MCP tools (evaluation_context_get +
evaluation_submit).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis

from .models import (
    EvaluationRequest,
    ParticipantSummary,
    ReplayContext,
    ReplayEvent,
    SentimentEntry,
    SessionClosedEvent,
    SessionMeta,
)
from .stream_hydrator import StreamHydrator, StreamNotAvailableError

logger = logging.getLogger(__name__)

REPLAY_CONTEXT_TTL = 3600  # 1h — suficiente para a avaliação completar


class Replayer:
    """
    Orquestra hydration + leitura do stream + construção do ReplayContext.
    """

    def __init__(
        self,
        redis_client:    aioredis.Redis,
        hydrator:        StreamHydrator,
        evaluator_pool:  str          = "avaliador_qualidade",
        default_speed:   float        = 10.0,
    ) -> None:
        self._redis          = redis_client
        self._hydrator       = hydrator
        self._evaluator_pool = evaluator_pool
        self._default_speed  = default_speed

    async def prepare(
        self,
        event:          SessionClosedEvent,
        speed_factor:   float | None = None,
        comparison_mode: bool        = False,
        dimensions:     list[str]    | None = None,
    ) -> EvaluationRequest:
        """
        Ponto de entrada principal.

        1. Garante que o Redis tem o stream (via Hydrator)
        2. Lê e reconstrói o stream com delta_ms
        3. Lê metadados complementares (sentimento, participantes, session meta)
        4. Escreve ReplayContext no Redis
        5. Retorna EvaluationRequest para publicação no Kafka

        Raises StreamNotAvailableError se o stream não existe em nenhuma fonte.
        """
        session_id = event.session_id
        tenant_id  = event.tenant_id
        sf         = speed_factor if speed_factor is not None else self._default_speed

        # ── 1. Hydration (ensure Redis) ───────────────────────────────────────
        source = await self._hydrator.ensure(session_id, tenant_id)

        # ── 2. Lê stream do Redis ─────────────────────────────────────────────
        raw_events = await self._read_stream(session_id)

        # ── 3. Lê metadados complementares ───────────────────────────────────
        session_meta  = await self._read_session_meta(session_id, event)
        sentiment     = await self._read_sentiment(session_id)
        participants  = await self._read_participants(session_id)

        # ── 4. Constrói ReplayContext ─────────────────────────────────────────
        evaluation_id = str(uuid.uuid4())
        replay_id     = str(uuid.uuid4())

        context = ReplayContext(
            session_id   = session_id,
            tenant_id    = tenant_id,
            replay_id    = replay_id,
            session_meta = session_meta,
            events       = raw_events,
            sentiment    = sentiment,
            participants = participants,
            speed_factor = sf,
            source       = source,
        )

        # ── 5. Persiste ReplayContext no Redis ────────────────────────────────
        context_key = f"{tenant_id}:replay:{session_id}:context"
        await self._redis.set(
            context_key,
            context.model_dump_json(),
            ex=REPLAY_CONTEXT_TTL,
        )
        logger.info(
            "Replayer: ReplayContext written for session %s (source=%s, events=%d, speed=%.1fx)",
            session_id, source, len(raw_events), sf,
        )

        # ── 6. Monta EvaluationRequest ────────────────────────────────────────
        return EvaluationRequest(
            evaluation_id   = evaluation_id,
            session_id      = session_id,
            tenant_id       = tenant_id,
            evaluator_pool  = self._evaluator_pool,
            speed_factor    = sf,
            comparison_mode = comparison_mode,
            dimensions      = dimensions or [],
        )

    # ─────────────────────────────────────────
    # Leitura do stream
    # ─────────────────────────────────────────

    async def _read_stream(self, session_id: str) -> list[ReplayEvent]:
        stream_key = f"session:{session_id}:stream"
        try:
            entries: list[tuple[bytes, dict[bytes, bytes]]] = await self._redis.xrange(
                stream_key, "-", "+"
            )
        except Exception as exc:
            logger.error("Replayer: failed to XRANGE %s: %s", stream_key, exc)
            raise StreamNotAvailableError(str(exc)) from exc

        events: list[ReplayEvent] = []
        prev_ts: datetime | None = None

        for _sid, fields in entries:
            decoded = self._decode_fields(fields)

            raw_ts = decoded.get("timestamp")
            try:
                ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                ts = datetime.now(timezone.utc)

            delta_ms = 0.0
            if prev_ts is not None:
                delta_ms = max(0.0, (ts - prev_ts).total_seconds() * 1000)
            prev_ts = ts

            # Determina role com fallback
            author_raw = decoded.get("author")
            author: dict[str, Any] | None = None
            if isinstance(author_raw, dict):
                author = author_raw
            elif isinstance(author_raw, str):
                try:
                    author = json.loads(author_raw)
                except Exception:
                    author = None

            original_content = decoded.get("original_content")
            if isinstance(original_content, str):
                try:
                    original_content = json.loads(original_content)
                except Exception:
                    original_content = None

            masked_cats_raw = decoded.get("masked_categories", [])
            if isinstance(masked_cats_raw, str):
                try:
                    masked_cats_raw = json.loads(masked_cats_raw)
                except Exception:
                    masked_cats_raw = []

            events.append(ReplayEvent(
                event_id         = str(decoded.get("event_id", _sid)),
                type             = str(decoded.get("type", "unknown")),
                timestamp        = ts,
                author           = author,
                visibility       = decoded.get("visibility"),
                payload          = decoded.get("payload", {}),
                original_content = original_content,
                masked_categories = masked_cats_raw,
                delta_ms         = delta_ms,
            ))

        return events

    # ─────────────────────────────────────────
    # Metadados complementares
    # ─────────────────────────────────────────

    async def _read_session_meta(
        self,
        session_id: str,
        event:      SessionClosedEvent,
    ) -> SessionMeta:
        try:
            raw = await self._redis.get(f"session:{session_id}:meta")
            if raw:
                meta = json.loads(raw)
                opened_at_str = meta.get("started_at") or meta.get("opened_at")
                opened_at = (
                    datetime.fromisoformat(opened_at_str.replace("Z", "+00:00"))
                    if opened_at_str
                    else datetime.now(timezone.utc)
                )
                closed_at = None
                if event.closed_at:
                    try:
                        closed_at = datetime.fromisoformat(
                            event.closed_at.replace("Z", "+00:00")
                        )
                    except ValueError:
                        pass

                duration_ms = None
                if closed_at:
                    duration_ms = max(0.0, (closed_at - opened_at).total_seconds() * 1000)

                return SessionMeta(
                    channel      = str(meta.get("channel", "webchat")),
                    opened_at    = opened_at,
                    closed_at    = closed_at,
                    outcome      = event.outcome,
                    close_reason = event.close_reason,
                    duration_ms  = duration_ms,
                )
        except Exception as exc:
            logger.warning("Replayer: failed to read session meta for %s: %s", session_id, exc)

        return SessionMeta(
            channel   = "webchat",
            opened_at = datetime.now(timezone.utc),
            outcome   = event.outcome,
            close_reason = event.close_reason,
        )

    async def _read_sentiment(self, session_id: str) -> list[SentimentEntry]:
        try:
            raw = await self._redis.get(f"session:{session_id}:sentiment")
            if raw:
                entries = json.loads(raw)
                return [
                    SentimentEntry(
                        score     = float(e.get("score", 0)),
                        timestamp = datetime.fromisoformat(
                            str(e.get("timestamp", datetime.now(timezone.utc).isoformat()))
                            .replace("Z", "+00:00")
                        ),
                    )
                    for e in entries
                    if isinstance(e, dict)
                ]
        except Exception:
            pass
        return []

    async def _read_participants(self, session_id: str) -> list[ParticipantSummary]:
        try:
            raw = await self._redis.get(f"session:{session_id}:participants")
            if raw:
                parts = json.loads(raw)
                result = []
                for p in parts:
                    if not isinstance(p, dict):
                        continue
                    joined = p.get("joined_at", datetime.now(timezone.utc).isoformat())
                    left   = p.get("left_at")
                    result.append(ParticipantSummary(
                        participant_id = str(p.get("participant_id", "")),
                        role           = str(p.get("role", "primary")),
                        agent_type_id  = p.get("agent_type_id"),
                        joined_at      = datetime.fromisoformat(
                            str(joined).replace("Z", "+00:00")
                        ),
                        left_at        = datetime.fromisoformat(
                            str(left).replace("Z", "+00:00")
                        ) if left else None,
                    ))
                return result
        except Exception:
            pass
        return []

    # ─────────────────────────────────────────
    # Utils
    # ─────────────────────────────────────────

    @staticmethod
    def _decode_fields(fields: dict[bytes, bytes]) -> dict[str, Any]:
        decoded: dict[str, Any] = {}
        for k, v in fields.items():
            key = k.decode() if isinstance(k, bytes) else str(k)
            val = v.decode() if isinstance(v, bytes) else str(v)
            try:
                decoded[key] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                decoded[key] = val
        return decoded
