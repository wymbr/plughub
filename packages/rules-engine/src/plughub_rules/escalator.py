"""
escalator.py
Triggers conversation_escalate via mcp-server-plughub.
Spec: PlugHub v24.0 section 3.2

When a rule fires AND has target_pool → triggers escalation.
Shadow mode → evaluates and publishes to shadow Kafka topic, does not trigger.
Active mode → triggers via mcp-server AND publishes to escalation Kafka topic.
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx

from .models import EscalationTrigger, EvaluationResult
from .config import get_settings

if TYPE_CHECKING:
    from .kafka_publisher import KafkaPublisher

logger = logging.getLogger("plughub.rules")


class Escalator:
    def __init__(
        self,
        http_client:     httpx.AsyncClient,
        kafka_publisher: "KafkaPublisher | None" = None,
    ) -> None:
        self._http      = http_client
        self._kafka     = kafka_publisher
        self._settings  = get_settings()

    async def trigger(self, result: EvaluationResult) -> EscalationTrigger | None:
        """
        Triggers escalation if the rule fired and has a target_pool.
        Returns EscalationTrigger or None if not applicable.
        """
        rule = result.rule

        if not result.triggered:
            return None

        if not rule.target_pool:
            logger.debug(
                "Rule %s triggered but no target_pool configured — no action",
                rule.rule_id,
            )
            return None

        is_shadow = rule.status == "shadow"

        trigger = EscalationTrigger(
            session_id=  result.context.session_id,
            tenant_id=   result.context.tenant_id,
            rule_id=     rule.rule_id,
            rule_name=   rule.name,
            target_pool= rule.target_pool,
            shadow_mode= is_shadow,
            triggered_at=datetime.now(timezone.utc).isoformat(),
            context=     result.context,
        )

        if is_shadow:
            # Shadow mode: record what would happen but do NOT call mcp-server
            logger.info(
                "[SHADOW] Rule %s would escalate session=%s → pool=%s",
                rule.rule_id, trigger.session_id, rule.target_pool,
            )
            if self._kafka:
                await self._kafka.publish_shadow(trigger)
            return trigger

        # Active mode: trigger escalation via mcp-server AND publish Kafka event
        try:
            await self._call_conversation_escalate(trigger)
            logger.info(
                "Escalation triggered: rule=%s session=%s → pool=%s",
                rule.rule_id, trigger.session_id, rule.target_pool,
            )
        except Exception as exc:
            logger.error(
                "Failed to escalate session=%s rule=%s: %s",
                trigger.session_id, rule.rule_id, exc,
            )

        if self._kafka:
            await self._kafka.publish_escalation(trigger)

        return trigger

    async def _call_conversation_escalate(self, trigger: EscalationTrigger) -> None:
        """Calls the conversation_escalate tool on mcp-server-plughub."""
        await self._http.post(
            f"{self._settings.mcp_server_url}/tools/conversation_escalate",
            json={
                "session_id":  trigger.session_id,
                "target_pool": trigger.target_pool,
                "reason":      f"rule:{trigger.rule_id}",
                "context":     trigger.context.model_dump(),
            },
            timeout=5.0,
        )
