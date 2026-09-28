"""
test_outbound_consumer.py
Unit tests for OutboundConsumer._dispatch and WebchatChannelAdapter.

After the Phase 1 refactor the consumer delegates delivery to ChannelAdapter
singletons.  Tests cover:
  - Consumer routing by channel (registry lookup)
  - Consumer skipping unknown/missing channels
  - WebchatChannelAdapter behaviour for each message type
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from plughub_channel_gateway.outbound_consumer import OutboundConsumer
from plughub_channel_gateway.adapters.webchat_channel import WebchatChannelAdapter


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def registry():
    reg = AsyncMock()
    reg.send = AsyncMock(return_value=True)
    reg.close_connection = AsyncMock()
    return reg


@pytest.fixture
def webchat_adapter(registry):
    return WebchatChannelAdapter(registry=registry)


@pytest.fixture
def consumer(webchat_adapter, settings):
    return OutboundConsumer(
        adapters={"webchat": webchat_adapter},
        settings=settings,
    )


# ── OutboundConsumer routing ───────────────────────────────────────────────────

class TestConsumerRouting:
    async def test_ignores_unregistered_channel(self, consumer, registry):
        """Messages for channels without a registered adapter are silently dropped."""
        payload = {
            "type": "message.text",
            "contact_id": "c1",
            "channel": "whatsapp",
            "text": "Olá",
        }
        await consumer._dispatch(payload)
        registry.send.assert_not_called()

    async def test_ignores_missing_contact_id(self, consumer, registry):
        payload = {
            "type": "message.text",
            "channel": "webchat",
            "text": "Olá",
        }
        await consumer._dispatch(payload)
        registry.send.assert_not_called()

    async def test_ignores_missing_channel(self, consumer, registry):
        payload = {
            "type": "message.text",
            "contact_id": "c1",
            "text": "Olá",
        }
        await consumer._dispatch(payload)
        registry.send.assert_not_called()

    async def test_routes_to_registered_adapter(self, consumer, webchat_adapter, registry):
        """Consumer delegates to WebchatChannelAdapter for webchat messages."""
        payload = {
            "type": "agent.typing",
            "contact_id": "c1",
            "channel": "webchat",
            "author_type": "agent_ai",
        }
        await consumer._dispatch(payload)
        registry.send.assert_called_once()

    async def test_ignores_unknown_type_no_crash(self, consumer, registry):
        payload = {
            "type": "unknown.event",
            "contact_id": "c1",
            "channel": "webchat",
        }
        # Must not raise
        await consumer._dispatch(payload)


# ── WebchatChannelAdapter — deliver_text ──────────────────────────────────────

class TestWebchatDeliverText:
    async def test_does_not_send_to_websocket(self, webchat_adapter, registry):
        """Hybrid stream model — text must NOT go via registry.send."""
        payload = {
            "type": "message.text",
            "contact_id": "c1",
            "session_id": "s1",
            "channel": "webchat",
            "message_id": "m1",
            "author": {"type": "agent_ai"},
            "text": "Texto.",
            "timestamp": "2024-01-01T10:00:00Z",
        }
        await webchat_adapter.deliver_text(payload)
        registry.send.assert_not_called()

    async def test_system_text_goes_to_the_socket_without_false_failure(
        self, webchat_adapter, registry, caplog,
    ):
        """O aviso de sistema (fila muda) não tem entrega pelo stream ao widget: sai pelo WS. O
        log de sucesso lê `session_id`; sem a variável, o NameError caía no `except` e dizia
        "could not deliver" de um aviso que tinha sido entregue (achado na ALW-18)."""
        payload = {
            "type": "message.text", "contact_id": "c1", "session_id": "s1", "channel": "webchat",
            "message_id": "m-sys", "author": {"type": "system", "id": "routing-engine"},
            "text": "Aguardando agente.", "timestamp": "2026-09-28T10:00:00Z",
        }
        with caplog.at_level("INFO"):
            await webchat_adapter.deliver_text(payload)
        registry.send.assert_awaited_once()
        enviado = registry.send.await_args.args[1]
        assert enviado["type"] == "msg.text" and enviado["text"] == "Aguardando agente."
        assert "could not deliver" not in caplog.text
        assert "session=s1" in caplog.text


# ── WebchatChannelAdapter — deliver_menu ──────────────────────────────────────

class TestWebchatDeliverMenu:
    async def test_masked_menu_does_nothing_on_the_socket(self, webchat_adapter, registry):
        """ALW-18: o menu chega ao widget pelo stream, e os campos mascarados não são mais
        guardados para redigir uma lista de histórico — a lista saiu."""
        payload = {
            "type": "menu.payload",
            "contact_id": "c1",
            "channel": "webchat",
            "menu_id": "menu-001",
            "interaction": "form",
            "prompt": "Dados:",
            "masked_fields": ["cpf", "senha"],
        }
        await webchat_adapter.deliver_menu(payload)
        registry.send.assert_not_called()

    async def test_no_send_to_websocket(self, webchat_adapter, registry):
        """Hybrid stream model — interaction.request must NOT go via registry.send."""
        payload = {
            "type": "menu.payload",
            "contact_id": "c1",
            "channel": "webchat",
            "menu_id": "m1",
            "interaction": "button",
            "prompt": "Continuar?",
            "options": [],
        }
        await webchat_adapter.deliver_menu(payload)
        registry.send.assert_not_called()


# ── WebchatChannelAdapter — deliver_typing ────────────────────────────────────

class TestWebchatDeliverTyping:
    async def test_sends_typing_frame(self, webchat_adapter, registry):
        payload = {
            "type": "agent.typing",
            "contact_id": "c1",
            "channel": "webchat",
            "author_type": "agent_ai",
        }
        await webchat_adapter.deliver_typing(payload)

        registry.send.assert_called_once()
        contact_id, ws_payload = registry.send.call_args.args
        assert contact_id == "c1"
        assert ws_payload["type"] == "agent.typing"
        assert ws_payload["author_type"] == "agent_ai"

    async def test_typing_default_author_type(self, webchat_adapter, registry):
        payload = {
            "type": "agent.typing",
            "contact_id": "c1",
            "channel": "webchat",
        }
        await webchat_adapter.deliver_typing(payload)
        _, ws_payload = registry.send.call_args.args
        assert ws_payload["author_type"] == "agent_ai"


# ── WebchatChannelAdapter — deliver_session_closed ───────────────────────────

class TestWebchatDeliverSessionClosed:
    async def test_sends_session_ended_and_closes(self, webchat_adapter, registry):
        payload = {
            "type": "session.closed",
            "contact_id": "c1",
            "channel": "webchat",
            "reason": "agent_done",
        }
        await webchat_adapter.deliver_session_closed(payload)

        registry.send.assert_called_once()
        _, ws_payload = registry.send.call_args.args
        assert ws_payload["type"] == "conn.session_ended"
        assert ws_payload["reason"] == "agent_done"
        registry.close_connection.assert_called_once_with("c1")

    async def test_default_reason(self, webchat_adapter, registry):
        payload = {
            "type": "session.closed",
            "contact_id": "c1",
            "channel": "webchat",
        }
        await webchat_adapter.deliver_session_closed(payload)
        _, ws_payload = registry.send.call_args.args
        assert ws_payload["reason"] == "agent_done"


# ── Error handling ─────────────────────────────────────────────────────────────

class TestDispatchErrorHandling:
    async def test_adapter_error_does_not_propagate(self, consumer, registry):
        """An error in the adapter must be caught by the consumer — log and continue."""
        registry.send.side_effect = Exception("Redis connection lost")
        payload = {
            "type": "agent.typing",
            "contact_id": "c1",
            "channel": "webchat",
            "author_type": "agent_ai",
        }
        # Must not raise
        await consumer._dispatch(payload)
