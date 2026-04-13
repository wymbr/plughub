"""
test_outbound_consumer.py
Unit tests for OutboundConsumer._dispatch.
Verifies correct WS model mapping for each message type and channel filtering.
"""

from __future__ import annotations
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from plughub_channel_gateway.outbound_consumer import OutboundConsumer


@pytest.fixture
def registry():
    reg = AsyncMock()
    reg.send = AsyncMock(return_value=True)
    return reg


@pytest.fixture
def consumer(registry, settings):
    return OutboundConsumer(registry=registry, settings=settings)


class TestDispatchFiltering:
    async def test_ignores_non_webchat_channel(self, consumer, registry):
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
            "channel": "chat",
            "text": "Olá",
        }
        await consumer._dispatch(payload)
        registry.send.assert_not_called()

    async def test_ignores_unknown_type(self, consumer, registry):
        payload = {
            "type": "unknown.event",
            "contact_id": "c1",
            "channel": "chat",
        }
        await consumer._dispatch(payload)
        registry.send.assert_not_called()


class TestMessageTextDispatch:
    async def test_dispatches_message_text(self, consumer, registry):
        payload = {
            "type": "message.text",
            "contact_id": "c1",
            "channel": "chat",
            "message_id": "msg-001",
            "author": {"type": "agent_ai"},
            "text": "Posso ajudar com sua solicitação.",
            "timestamp": "2024-01-01T10:00:00Z",
        }
        await consumer._dispatch(payload)

        registry.send.assert_called_once()
        contact_id, ws_payload = registry.send.call_args.args
        assert contact_id == "c1"
        assert ws_payload["type"] == "message.text"
        assert ws_payload["text"] == "Posso ajudar com sua solicitação."
        assert ws_payload["message_id"] == "msg-001"

    async def test_message_text_missing_fields_uses_defaults(self, consumer, registry):
        """Partial payload — optional fields use empty defaults."""
        payload = {
            "type": "message.text",
            "contact_id": "c1",
            "channel": "chat",
        }
        await consumer._dispatch(payload)
        registry.send.assert_called_once()
        _, ws_payload = registry.send.call_args.args
        assert ws_payload["text"] == ""
        assert ws_payload["author"] == {}


class TestMenuPayloadDispatch:
    async def test_dispatches_menu_with_options(self, consumer, registry):
        payload = {
            "type": "menu.payload",
            "contact_id": "c1",
            "channel": "chat",
            "menu_id": "menu-001",
            "interaction": "button",
            "prompt": "Deseja continuar?",
            "options": [{"id": "yes", "label": "Sim"}, {"id": "no", "label": "Não"}],
        }
        await consumer._dispatch(payload)

        registry.send.assert_called_once()
        _, ws_payload = registry.send.call_args.args
        assert ws_payload["type"] == "menu.render"
        assert ws_payload["menu_id"] == "menu-001"
        assert ws_payload["interaction"] == "button"
        assert len(ws_payload["options"]) == 2

    async def test_dispatches_menu_form(self, consumer, registry):
        payload = {
            "type": "menu.payload",
            "contact_id": "c1",
            "channel": "chat",
            "menu_id": "form-001",
            "interaction": "form",
            "prompt": "Preencha seus dados:",
            "fields": [{"id": "name", "label": "Nome", "type": "text"}],
        }
        await consumer._dispatch(payload)

        _, ws_payload = registry.send.call_args.args
        assert ws_payload["type"] == "menu.render"
        assert ws_payload["interaction"] == "form"
        assert ws_payload["fields"][0]["id"] == "name"

    async def test_dispatches_menu_without_options(self, consumer, registry):
        payload = {
            "type": "menu.payload",
            "contact_id": "c1",
            "channel": "chat",
            "menu_id": "menu-text",
            "interaction": "text",
            "prompt": "Digite sua resposta:",
        }
        await consumer._dispatch(payload)
        _, ws_payload = registry.send.call_args.args
        assert ws_payload["options"] is None


class TestAgentTypingDispatch:
    async def test_dispatches_agent_typing(self, consumer, registry):
        payload = {
            "type": "agent.typing",
            "contact_id": "c1",
            "channel": "chat",
            "author_type": "agent_ai",
        }
        await consumer._dispatch(payload)

        registry.send.assert_called_once()
        _, ws_payload = registry.send.call_args.args
        assert ws_payload["type"] == "agent.typing"
        assert ws_payload["author_type"] == "agent_ai"

    async def test_agent_typing_default_author_type(self, consumer, registry):
        payload = {
            "type": "agent.typing",
            "contact_id": "c1",
            "channel": "chat",
        }
        await consumer._dispatch(payload)
        _, ws_payload = registry.send.call_args.args
        assert ws_payload["author_type"] == "agent_ai"


class TestSessionClosedDispatch:
    async def test_dispatches_session_closed(self, consumer, registry):
        payload = {
            "type": "session.closed",
            "contact_id": "c1",
            "channel": "chat",
            "reason": "agent_done",
        }
        await consumer._dispatch(payload)

        registry.send.assert_called_once()
        _, ws_payload = registry.send.call_args.args
        assert ws_payload["type"] == "session.closed"
        assert ws_payload["reason"] == "agent_done"

    async def test_session_closed_default_reason(self, consumer, registry):
        payload = {
            "type": "session.closed",
            "contact_id": "c1",
            "channel": "chat",
        }
        await consumer._dispatch(payload)
        _, ws_payload = registry.send.call_args.args
        assert ws_payload["reason"] == "agent_done"


class TestDispatchErrorHandling:
    async def test_dispatch_handles_registry_error_gracefully(self, consumer, registry):
        """An error in registry.send must not propagate — log and continue."""
        registry.send.side_effect = Exception("Redis connection lost")
        payload = {
            "type": "message.text",
            "contact_id": "c1",
            "channel": "chat",
            "message_id": "m1",
            "author": {},
            "text": "Olá",
            "timestamp": "2024-01-01T10:00:00Z",
        }
        # Should not raise
        await consumer._dispatch(payload)
