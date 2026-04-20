"""
kafka_publisher.py
Kafka event publisher for escalation and shadow events.
Spec: PlugHub v24.0 section 3.2
"""

from __future__ import annotations
import json
import logging
from typing import Any

from .models import EscalationTrigger

logger = logging.getLogger("plughub.rules")

TOPIC_SHADOW     = "rules.shadow.events"
TOPIC_ESCALATION = "rules.escalation.events"


class KafkaPublisher:
    def __init__(self, producer: Any) -> None:  # AIOKafkaProducer
        self._producer = producer

    async def publish_shadow(self, trigger: EscalationTrigger) -> None:
        """Publishes a shadow mode trigger event to TOPIC_SHADOW."""
        try:
            value = json.dumps(trigger.model_dump(), default=str).encode()
            await self._producer.send_and_wait(TOPIC_SHADOW, value=value)
        except Exception as exc:
            logger.warning(
                "Failed to publish shadow event rule=%s session=%s: %s",
                trigger.rule_id, trigger.session_id, exc,
            )

    async def publish_escalation(self, trigger: EscalationTrigger) -> None:
        """Publishes an active escalation trigger event to TOPIC_ESCALATION."""
        try:
            value = json.dumps(trigger.model_dump(), default=str).encode()
            await self._producer.send_and_wait(TOPIC_ESCALATION, value=value)
        except Exception as exc:
            logger.warning(
                "Failed to publish escalation event rule=%s session=%s: %s",
                trigger.rule_id, trigger.session_id, exc,
            )
