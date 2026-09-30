"""
escalator.py
Consequence of a rule that fires with a target_pool.
Spec: PlugHub v24.0 section 3.2

Shadow mode → publishes to `rules.shadow.events`, does not act.
Active mode → publishes to `rules.escalation.events`; the orchestrator-bridge acts (RUL-02).

RUL-02 (2026-09-30) — quem escala é o dono da ativação, não este serviço
------------------------------------------------------------------------
O rules-engine DECIDE que a regra disparou; ele não para agente nem roteia. O evento vai ao
bridge, que confere as condições (a IA conduz, não há humano, primeira vez na sessão) e marca
a preempção; o engine de skill-flow para a IA na fronteira do próximo passo e ela escala a
si mesma pelo `escalate` de sempre. Ver `orchestrator-bridge/rule_escalation.py` e
`skill-flow-engine/src/rule-preemption.ts`.

Esta casa segue SEM estado por sessão (invariante do serviço): a regra avalia a cada turno e
pode disparar de novo; quem garante "uma vez por sessão" é a marca do bridge (SET NX), e os
disparos repetidos morrem lá, contados no log.

RUL-01 (2026-09-29) — o que havia aqui antes
--------------------------------------------
O modo ativo fazia `POST {mcp_server_url}/tools/conversation_escalate`, rota que o
mcp-server-plughub NUNCA teve, e engolia o 404; chamada de fora, a tool mandaria o contato a
um humano SEM parar a IA. Por isso o caminho é o evento, não a tool.
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .models import EscalationTrigger, EvaluationResult

if TYPE_CHECKING:
    from .kafka_publisher import KafkaPublisher

logger = logging.getLogger("plughub.rules")


class Escalator:
    def __init__(self, kafka_publisher: "KafkaPublisher | None" = None) -> None:
        self._kafka = kafka_publisher

    async def trigger(self, result: EvaluationResult) -> EscalationTrigger | None:
        """
        Shadow: publishes the would-be trigger to the shadow topic.
        Active: publishes the trigger to the escalation topic — the bridge acts on it.
        Returns the trigger when it was PUBLISHED; None when nothing left this service.
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

        shadow = rule.status == "shadow"
        if not shadow and rule.status != "active":
            # draft/dry_run/disabled não chegam aqui pelo RuleStore (só active|shadow);
            # se chegarem, é leitura por fora do cache — recusar, dito.
            logger.error("Rule %s com status=%s disparou fora do ciclo — nada publicado",
                         rule.rule_id, rule.status)
            return None

        trigger = EscalationTrigger(
            session_id=      result.context.session_id,
            tenant_id=       result.context.tenant_id,
            rule_id=         rule.rule_id,
            rule_name=       rule.name,
            target_pool=     rule.target_pool,
            shadow_mode=     shadow,
            triggered_at=    datetime.now(timezone.utc).isoformat(),
            context=         result.context,
            customer_notice= rule.customer_notice,
        )

        if self._kafka is None:
            logger.error(
                "Rule %s disparou (session=%s → pool=%s, %s) e NÃO há publicador Kafka — "
                "nada saiu deste serviço", rule.rule_id, trigger.session_id,
                rule.target_pool, "shadow" if shadow else "ATIVA",
            )
            return None

        if shadow:
            logger.info("[SHADOW] Rule %s would escalate session=%s → pool=%s",
                        rule.rule_id, trigger.session_id, rule.target_pool)
            ok = await self._kafka.publish_shadow(trigger)
        else:
            logger.warning("[ACTIVE] Rule %s escalates session=%s → pool=%s",
                           rule.rule_id, trigger.session_id, rule.target_pool)
            ok = await self._kafka.publish_escalation(trigger)
        return trigger if ok else None
