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

SENTIMENT_TAG = "core.sentiment.current"


async def read_measured_sentiment(redis: Any, tenant_id: str, session_id: str) -> float | None:
    """RUL-04 — o sentimento que a regra vê é o do ContextStore (`core.sentiment.current`),
    a casa única onde os DOIS produtores gravam: a medição fora do turno (`sentiment_analyzer`)
    e o valor que um `reason` declara no `output_schema`. O `sentiment_score` do pub/sub era
    só o segundo — que nenhuma skill viva declara —, então regra de sentimento nunca casava.

    `None` = não medido (e não casa). Leitura que falha é dita, nunca vira neutro."""
    key = f"{tenant_id}:ctx:{session_id}"
    try:
        raw = await redis.hget(key, SENTIMENT_TAG)
    except Exception as exc:
        logger.warning("sentimento ilegível para session=%s (%s) — condições de sentimento não "
                       "casam nesta avaliação", session_id, exc)
        return None
    if not raw:
        return None
    try:
        entry = json.loads(raw)
        value = entry.get("value") if isinstance(entry, dict) else entry
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"valor não numérico: {value!r}")
        return float(value)
    except Exception as exc:
        logger.warning("%s de session=%s malformado (%s) — tratado como não medido",
                       SENTIMENT_TAG, session_id, exc)
        return None


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

        return EvaluationContext(
            session_id=        session_id,
            tenant_id=         tenant_id,
            sentiment_score=   await read_measured_sentiment(self._redis, tenant_id, session_id),
            intent_confidence= float(params.get("confidence", 0.0)),
            flags=             params.get("flags", []),
        )
