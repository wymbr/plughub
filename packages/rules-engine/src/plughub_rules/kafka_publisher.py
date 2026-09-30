"""
kafka_publisher.py
Kafka event publisher for escalation and shadow events.
Spec: PlugHub v24.0 section 3.2

RUL-02: os dois tópicos viajam com a CHAVE `session_id` — ordem no Kafka é por partição, e
eventos da mesma sessão sem chave não têm ordem nenhuma. A escalação ATIVA que não sai é
ERROR (a regra decidiu escalar e o contato ficou onde estava); o shadow é medida, WARNING.
Os dois devolvem se publicaram, para o chamador não afirmar o que não aconteceu.
"""

from __future__ import annotations
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from .models import EscalationTrigger, EvaluationContext

logger = logging.getLogger("plughub.rules")

TOPIC_SHADOW     = "rules.shadow.events"
TOPIC_ESCALATION = "rules.escalation.events"
# RUL-05 — o contexto que uma regra VIU em cada turno, de todo tenant, com ou sem regra: o
# dry-run histórico o relê com o mesmo avaliador. Quem grava é a analytics-api.
TOPIC_TURN_CONTEXT = "rules.turn_contexts"


class KafkaPublisher:
    def __init__(self, producer: Any) -> None:  # AIOKafkaProducer
        self._producer = producer

    async def _send(self, topic: str, trigger: EscalationTrigger) -> None:
        value = json.dumps(trigger.model_dump(), default=str).encode()
        await self._producer.send_and_wait(topic, value=value, key=trigger.session_id.encode())

    async def publish_shadow(self, trigger: EscalationTrigger) -> bool:
        """Publishes a shadow mode trigger event to TOPIC_SHADOW."""
        try:
            await self._send(TOPIC_SHADOW, trigger)
            return True
        except Exception as exc:
            logger.warning(
                "Failed to publish shadow event rule=%s session=%s: %s",
                trigger.rule_id, trigger.session_id, exc,
            )
            return False

    async def publish_escalation(self, trigger: EscalationTrigger) -> bool:
        """Publishes an active escalation trigger event to TOPIC_ESCALATION."""
        try:
            await self._send(TOPIC_ESCALATION, trigger)
            return True
        except Exception as exc:
            logger.error(
                "Escalação por regra NÃO publicada — o contato fica onde está: rule=%s "
                "session=%s → pool=%s: %s",
                trigger.rule_id, trigger.session_id, trigger.target_pool, exc,
            )
            return False

    async def publish_turn_context(self, ctx: EvaluationContext, trigger: str) -> bool:
        """RUL-05 — publica o contexto avaliado. Falha é WARNING: a regra continua avaliando;
        o que se perde é o turno no histórico do dry-run, e isso tem de aparecer."""
        body = {
            "event_id":          str(uuid.uuid4()),
            "tenant_id":         ctx.tenant_id,
            "session_id":        ctx.session_id,
            "observed_at":       datetime.now(timezone.utc).isoformat(),
            "trigger":           trigger,
            "turn_count":        ctx.turn_count,
            "elapsed_ms":        ctx.elapsed_ms,
            "sentiment_score":   ctx.sentiment_score,
            "intent_confidence": ctx.intent_confidence,
            "flags":             list(ctx.flags),
        }
        try:
            await self._producer.send_and_wait(
                TOPIC_TURN_CONTEXT, value=json.dumps(body).encode(), key=ctx.session_id.encode())
            return True
        except Exception as exc:
            logger.warning("contexto do turno NÃO publicado — o dry-run histórico não verá este "
                           "turno: session=%s: %s", ctx.session_id, exc)
            return False
