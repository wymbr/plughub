"""
outbound_consumer.py
Kafka consumer for conversations.outbound.

Routes each outbound message to the correct ChannelAdapter based on the
`channel` field in the payload.  Each adapter handles delivery to the
customer in a channel-appropriate way.

Architecture: channel-gateway-multi-channel.md § 6 (OutboundConsumer registry)

Registry pattern
----------------
OutboundConsumer holds a dict[str, ChannelAdapter].  New channels are added
by registering their adapter singleton at startup — no changes to this file.

    consumer = OutboundConsumer(
        adapters  = {"webchat": WebchatChannelAdapter(registry)},
        settings  = settings,
    )
"""

from __future__ import annotations

import asyncio
import json
import logging

from aiokafka import AIOKafkaConsumer

from .adapters.base import ChannelAdapter
from .config import Settings
from plughub_tasks import disparar

logger = logging.getLogger("plughub.channel-gateway.outbound")


class OutboundConsumer:
    def __init__(
        self,
        adapters:  dict[str, ChannelAdapter],
        settings:  Settings,
    ) -> None:
        self._adapters  = adapters
        self._settings  = settings

    async def _cancelar_resumes_pendentes(self, payload: dict) -> None:
        """Delega ao dono dos tokens. Nunca aborta o fechamento por causa disto."""
        session_id = payload.get("session_id")
        tenant_id  = payload.get("tenant_id") or getattr(self._settings, "tenant_id", "")
        if not session_id or not tenant_id:
            logger.warning(
                "RET-03: session.closed sem session_id/tenant_id (session=%r tenant=%r) — "
                "resume_tokens NAO invalidados; o scanner pode tentar retomar um contato encerrado",
                session_id, tenant_id,
            )
            return
        alvo = self._adapters.get("webhook")
        cancelar = getattr(alvo, "cancel_pending_resumes", None)
        if cancelar is None:
            logger.warning(
                "RET-03: adapter `webhook` ausente ou sem cancel_pending_resumes — "
                "resume_tokens da sessao %s NAO invalidados", session_id,
            )
            return
        try:
            await cancelar(tenant_id, session_id)
        except Exception as exc:
            logger.warning(
                "RET-03: cancelamento de resume_tokens falhou (session=%s): %s", session_id, exc,
            )

    async def run(self) -> None:
        consumer = AIOKafkaConsumer(
            self._settings.kafka_topic_outbound,
            bootstrap_servers = self._settings.kafka_brokers,
            group_id          = self._settings.kafka_group_id,
            auto_offset_reset = "latest",
            # Low-latency tuning: reduce broker wait time before returning data.
            fetch_max_wait_ms = 100,
            fetch_min_bytes   = 1,
        )
        await consumer.start()
        logger.info(
            "outbound consumer started — topic=%s channels=%s",
            self._settings.kafka_topic_outbound,
            list(self._adapters),
        )

        try:
            async for msg in consumer:
                # RET-15: uma task por MENSAGEM, e ela nao tinha dono nem net —
                # `_dispatch` tem try em pedacos, e 8 das 9 instrucoes de topo
                # ficavam de fora. Entrega ao canal que falhasse sumia calada.
                disparar(
                    self._dispatch(json.loads(msg.value.decode())),
                    nome="outbound-dispatch",
                )
        finally:
            await consumer.stop()

    async def _dispatch(self, payload: dict) -> None:
        msg_type   = payload.get("type")
        contact_id = payload.get("contact_id")
        channel    = payload.get("channel")

        # ── RET-03: fechou a sessao ⇒ invalida os resume_tokens dela ──────────
        #
        # ANTES das guardas de `contact_id`/`channel`/adapter, de proposito: o
        # cancelamento e sobre a SESSAO, nao sobre a entrega ao canal. Um
        # fechamento de canal sem adapter registrado ainda tem de limpar o token,
        # senao o buraco volta pela porta menos vigiada.
        #
        # ⚠️ UM lugar, e nao um gancho por adapter: `session.closed` chega aqui
        # para todo canal, e N ganchos e o esquecido reabrindo tudo.
        if msg_type == "session.closed":
            await self._cancelar_resumes_pendentes(payload)

        if not contact_id or not channel:
            if msg_type or channel:
                logger.debug(
                    "outbound skipped type=%s channel=%s contact_id=%s",
                    msg_type, channel, contact_id,
                )
            return

        adapter = self._adapters.get(channel)
        if adapter is None:
            logger.debug(
                "outbound: no adapter registered for channel=%s type=%s contact_id=%s",
                channel, msg_type, contact_id,
            )
            return

        logger.info(
            "outbound dispatch type=%s channel=%s contact_id=%s session_id=%s",
            msg_type, channel, contact_id, payload.get("session_id"),
        )

        if channel == "webchat":
            # WCH-02 — a chamada presa ao contato de chat FALA o que o agente de IA escreve. Antes
            # da entrega ao chat e sem `await`: a fala entra na fila na ordem do Kafka.
            fala = getattr(self._adapters.get("webrtc"), "chat_call_outbound", None)
            if fala is not None:
                try:
                    fala(msg_type, payload)
                except Exception as exc:
                    logger.error("chamada de chat: fala NAO enfileirada type=%s session=%s: %s",
                                 msg_type, payload.get("session_id"), exc)

        try:
            if msg_type == "message.text":
                await adapter.deliver_text(payload)

            elif msg_type == "menu.payload":
                await adapter.deliver_menu(payload)

            elif msg_type == "agent.typing":
                await adapter.deliver_typing(payload)

            elif msg_type == "session.closed":
                await adapter.deliver_session_closed(payload)

            else:
                logger.debug(
                    "unhandled outbound type=%s channel=%s contact_id=%s",
                    msg_type, channel, contact_id,
                )

        except Exception as exc:
            logger.error(
                "dispatch error type=%s channel=%s contact_id=%s: %s",
                msg_type, channel, contact_id, exc,
            )
