"""
escalator.py
Consequence of a rule that fires with a target_pool.
Spec: PlugHub v24.0 section 3.2

Shadow mode → evaluates and publishes to the shadow Kafka topic, does not act.
Active mode → REFUSED, loudly: rule-driven escalation has no path yet (RUL-02).

RUL-01 (2026-09-29) — o que havia aqui e por que saiu
-----------------------------------------------------
O modo ativo fazia `POST {mcp_server_url}/tools/conversation_escalate`, uma rota que o
mcp-server-plughub NUNCA teve (as tools vivem atrás do transporte MCP), sem conferir o
status — o 404 era engolido, o log dizia *"Escalation triggered"* e o evento de escalação
saía como se o contato tivesse ido ao pool. O teste que "provava" o caminho fazia o mock
responder 200: o mock CRIOU a rota que não existe.

Consertar só a rota não bastava: a tool `conversation_escalate` foi escrita para o FLUXO que
escala a si mesmo (grava `participant_left` do agente e reroteia). Chamada de fora, com a IA
ainda rodando o skill, ela mandaria o contato a um humano SEM parar a IA. O caminho de
verdade passa por quem é dono da ativação (o bridge) e é a ficha RUL-02. Até lá, o modo
ativo recusa, alto, e a API recusa ATIVAR regra com `target_pool` (rule_registry.py).
Medido no dia: nenhuma regra cadastrada em tenant nenhum, e o tópico de escalação nunca foi
criado no broker — exposição zero, dano zero.
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .models import EscalationTrigger, EvaluationResult

if TYPE_CHECKING:
    from .kafka_publisher import KafkaPublisher

logger = logging.getLogger("plughub.rules")

# Nomeado para os testes e para o log: a recusa diz ONDE está o trabalho que falta.
ESCALATION_PATH_TICKET = "RUL-02"


class Escalator:
    def __init__(self, kafka_publisher: "KafkaPublisher | None" = None) -> None:
        self._kafka = kafka_publisher

    async def trigger(self, result: EvaluationResult) -> EscalationTrigger | None:
        """
        Shadow: returns the would-be EscalationTrigger and publishes it to the shadow topic.
        Active: logs an ERROR and returns None — nothing escalated, nothing published as if
        it had been.
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

        if rule.status != "shadow":
            logger.error(
                "Escalação por regra SEM CAMINHO — NÃO escalada: rule=%s status=%s "
                "session=%s → pool=%s. O modo ativo não tem como escalar sem parar o agente em "
                "curso (%s); a API recusa ativar regra com target_pool. Se esta linha aparece, "
                "a regra foi ativada por fora da API.",
                rule.rule_id, rule.status, result.context.session_id, rule.target_pool,
                ESCALATION_PATH_TICKET,
            )
            return None

        trigger = EscalationTrigger(
            session_id=  result.context.session_id,
            tenant_id=   result.context.tenant_id,
            rule_id=     rule.rule_id,
            rule_name=   rule.name,
            target_pool= rule.target_pool,
            shadow_mode= True,
            triggered_at=datetime.now(timezone.utc).isoformat(),
            context=     result.context,
        )
        logger.info(
            "[SHADOW] Rule %s would escalate session=%s → pool=%s",
            rule.rule_id, trigger.session_id, rule.target_pool,
        )
        if self._kafka:
            await self._kafka.publish_shadow(trigger)
        return trigger
