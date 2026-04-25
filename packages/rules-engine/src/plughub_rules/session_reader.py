"""
session_reader.py
Reads session parameters from Redis written by the AI Gateway.
Spec: PlugHub v24.0 section 3.2
"""

from __future__ import annotations
import json
import logging
from typing import Any

from .models import EvaluationContext

logger = logging.getLogger("plughub.rules")


class SessionParamsReader:
    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def read_turn_params(
        self,
        tenant_id:  str,
        session_id: str,
        turn_id:    str,
    ) -> dict | None:
        """
        Reads {tenant_id}:session:{session_id}:turn:{turn_id}:params
        Returns dict with: intent, confidence, sentiment_score, risk_flag, flags
        or None if key not found.
        """
        key = f"{tenant_id}:session:{session_id}:turn:{turn_id}:params"
        raw = await self._redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    async def read_session_history(self, session_id: str) -> list[float]:
        """
        Reads session:{session_id}:ai (written by AI Gateway session.py).
        Returns list of sentiment_scores from consolidated_turns (oldest first).
        """
        raw = await self._redis.get(f"session:{session_id}:ai")
        if not raw:
            return []
        try:
            data  = json.loads(raw)
            turns = data.get("consolidated_turns", [])
            return [float(t.get("sentiment_score", 0.0)) for t in turns]
        except Exception:
            return []

    async def build_evaluation_context(
        self,
        tenant_id:  str,
        session_id: str,
        turn_id:    str,
    ) -> EvaluationContext | None:
        """
        Builds an EvaluationContext from Redis data written by the AI Gateway.
        Returns None if the turn params key is not found.
        """
        params = await self.read_turn_params(tenant_id, session_id, turn_id)
        if params is None:
            return None

        sentiment_history = await self.read_session_history(session_id)

        return EvaluationContext(
            session_id=        session_id,
            tenant_id=         tenant_id,
            sentiment_score=   float(params.get("sentiment_score", 0.0)),
            intent_confidence= float(params.get("confidence", 0.0)),
            flags=             params.get("flags", []),
            sentiment_history= sentiment_history,
        )
