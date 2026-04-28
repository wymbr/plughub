"""
session.py
Read and write session state in Redis.
Spec: PlugHub v24.0 section 2.2a

Redis structure:
  session:{session_id}:ai → JSON with consolidated_turns + current_turn

sentiment_score is updated on every LLM call — not only at the end of the turn.
"""

from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Any
import redis.asyncio as aioredis

logger = logging.getLogger("plughub.ai_gateway.session")

from .config import get_settings
from .sentiment_emitter import emit_sentiment_updated, update_sentiment_live, write_context_store_sentiment


async def get_redis() -> aioredis.Redis:
    """
    Factory: creates and returns a connected aioredis client.
    Called once during lifespan startup in main.py.
    Uses PLUGHUB_REDIS_URL from settings.
    """
    settings = get_settings()
    client: aioredis.Redis = aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
    await client.ping()
    return client


@dataclass
class CurrentTurn:
    llm_calls:      list[dict[str, Any]] = field(default_factory=list)
    partial_params: dict[str, Any]       = field(default_factory=lambda: {
        "intent": None, "confidence": 0.0, "sentiment_score": 0.0,
    })
    detected_flags: list[str]            = field(default_factory=list)


@dataclass
class ConsolidatedTurn:
    turn_number:     int
    intent:          str | None
    confidence:      float
    sentiment_score: float
    flags:           list[str]


@dataclass
class SessionAIState:
    consolidated_turns: list[ConsolidatedTurn] = field(default_factory=list)
    current_turn:       CurrentTurn            = field(default_factory=CurrentTurn)


class SessionManager:
    def __init__(self, redis_client: aioredis.Redis, kafka_producer: Any = None) -> None:
        self._redis    = redis_client
        self._producer = kafka_producer
        self._settings = get_settings()

    def _key(self, session_id: str) -> str:
        return f"session:{session_id}:ai"

    async def get(self, session_id: str) -> SessionAIState:
        raw = await self._redis.get(self._key(session_id))
        if not raw:
            return SessionAIState()
        data = json.loads(raw)
        state = SessionAIState(
            consolidated_turns=[
                ConsolidatedTurn(**t) for t in data.get("consolidated_turns", [])
            ],
            current_turn=CurrentTurn(**data.get("current_turn", {})),
        )
        return state

    async def save(self, session_id: str, state: SessionAIState) -> None:
        await self._redis.set(
            self._key(session_id),
            json.dumps(asdict(state)),
            ex=self._settings.session_ttl_seconds,
        )

    async def update_partial_params(
        self,
        session_id:      str,
        tenant_id:       str,
        elapsed_ms:      int,
        intent:          str | None,
        confidence:      float,
        sentiment_score: float,
        flags:           list[str],
    ) -> None:
        """
        Updates partial parameters of the current_turn.
        Called on every LLM response — each call is treated as one monitoring
        turn: the previous current_turn is consolidated to history before the
        new data is written, so consolidated_turns grows with every step.
        Spec 2.2a: Rules Engine can evaluate current_turn in real time.
        """
        state = await self.get(session_id)

        # Close the previous turn: move current_turn data → consolidated_turns.
        # Only close if there was meaningful data (avoid empty initial turn).
        prev = state.current_turn.partial_params
        if prev.get("intent") is not None or prev.get("sentiment_score", 0.0) != 0.0:
            state.consolidated_turns.append(ConsolidatedTurn(
                turn_number     = len(state.consolidated_turns) + 1,
                intent          = prev.get("intent"),
                confidence      = float(prev.get("confidence", 0.0)),
                sentiment_score = float(prev.get("sentiment_score", 0.0)),
                flags           = list(state.current_turn.detected_flags),
            ))

        # Update current turn with new data
        state.current_turn.partial_params = {
            "intent":          intent,
            "confidence":      confidence,
            "sentiment_score": sentiment_score,
        }
        state.current_turn.detected_flags = flags
        await self.save(session_id, state)

        turn_count = len(state.consolidated_turns)

        # Publish sentiment.updated to Kafka + update sentiment_live in Redis (fire-and-forget).
        # pool_id is stored in session:{session_id}:meta under key "pool_id".
        # Missing key is normal for new sessions routed before first turn — silently skipped.
        try:
            pool_id_raw = await self._redis.hget(f"session:{session_id}:meta", "pool_id")
            pool_id = pool_id_raw if pool_id_raw else "unknown"
            await emit_sentiment_updated(
                producer   = self._producer,
                tenant_id  = tenant_id,
                session_id = session_id,
                pool_id    = pool_id,
                score      = sentiment_score,
            )
            await update_sentiment_live(
                redis      = self._redis,
                tenant_id  = tenant_id,
                pool_id    = pool_id,
                score      = sentiment_score,
                session_id = session_id,
            )
            await write_context_store_sentiment(
                redis      = self._redis,
                tenant_id  = tenant_id,
                session_id = session_id,
                score      = sentiment_score,
            )
        except Exception as exc:
            logger.warning("Sentiment pipeline failed for %s: %s", session_id, exc)

        # Publish to Rules Engine pub/sub channel (fire-and-forget).
        # A Rules Engine outage must never block the AI Gateway response path.
        rules_channel = f"{self._settings.redis_session_channel}:{session_id}"
        rules_payload = json.dumps({
            "session_id":        session_id,
            "tenant_id":         tenant_id,
            "sentiment_score":   sentiment_score,
            "intent_confidence": confidence,
            "flags":             flags,
            "turn_count":        turn_count,
            "elapsed_ms":        elapsed_ms,
        })
        try:
            await self._redis.publish(rules_channel, rules_payload)
        except Exception as exc:
            logger.warning("Failed to publish Rules Engine update for %s: %s", session_id, exc)

        # Also notify the Agent Assist UI supervisor dashboard via the agent
        # WebSocket channel (agent:events:{session_id}).  This triggers a
        # re-fetch of GET /supervisor_state/:sessionId so the UI stays live
        # even for AI-handled sessions where no message.text event is published.
        try:
            await self._redis.publish(
                f"agent:events:{session_id}",
                json.dumps({
                    "type":            "supervisor_state.updated",
                    "session_id":      session_id,
                    "turn_count":      turn_count,
                    "sentiment_score": sentiment_score,
                    "intent":          intent,
                }),
            )
        except Exception as exc:
            logger.warning(
                "Failed to publish supervisor_state.updated for %s: %s", session_id, exc
            )