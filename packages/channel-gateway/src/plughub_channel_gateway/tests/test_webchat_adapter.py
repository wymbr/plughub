"""
test_webchat_adapter.py
Integration-style tests for WebchatAdapter.
Tests the full contact lifecycle:
  - WebSocket connects → contact_open published
  - Text messages normalized → conversations.inbound
  - Menu submits normalized → conversations.inbound
  - Disconnect → contact_closed published
  - Timeout path
Spec: channel-gateway-webchat.md — WebchatAdapter lifecycle
"""

from __future__ import annotations
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, call, patch
from fastapi import WebSocketDisconnect

from plughub_channel_gateway.adapters.webchat import WebchatAdapter
from plughub_channel_gateway.models import ContextSnapshot


CONTACT_ID = "cid-test-001"
SESSION_ID = "sid-test-001"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def context_reader():
    cr = AsyncMock()
    cr.get_snapshot = AsyncMock(
        return_value=ContextSnapshot(intent="general_inquiry", sentiment_score=0.7, turn_number=1)
    )
    return cr


@pytest.fixture
def registry():
    reg = AsyncMock()
    reg.register = AsyncMock()
    reg.unregister = AsyncMock(return_value="2024-01-01T10:00:00Z")
    reg.send = AsyncMock(return_value=True)
    return reg


def make_adapter(ws, producer, registry, context_reader, settings):
    return WebchatAdapter(
        ws=ws,
        contact_id=CONTACT_ID,
        session_id=SESSION_ID,
        producer=producer,
        registry=registry,
        context_reader=context_reader,
        settings=settings,
    )


def make_ws(messages: list[str]):
    """WebSocket mock that yields messages then disconnects."""
    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    _msgs = list(messages)

    async def receive_text():
        if _msgs:
            return _msgs.pop(0)
        raise WebSocketDisconnect(code=1000)

    ws.receive_text = receive_text
    return ws


# ── Connection lifecycle ──────────────────────────────────────────────────────

class TestConnectionLifecycle:
    async def test_accept_and_register_on_connect(
        self, mock_producer, registry, context_reader, settings
    ):
        ws = make_ws([])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings)
        await adapter.handle()

        ws.accept.assert_called_once()
        registry.register.assert_called_once_with(CONTACT_ID, ws)

    async def test_sends_connection_accepted_to_client(
        self, mock_producer, registry, context_reader, settings
    ):
        ws = make_ws([])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings)
        await adapter.handle()

        # First call to send_json must be connection.accepted
        first_call = ws.send_json.call_args_list[0]
        payload = first_call.args[0]
        assert payload["type"] == "connection.accepted"
        assert payload["contact_id"] == CONTACT_ID
        assert payload["session_id"] == SESSION_ID

    async def test_publishes_contact_open_to_events(
        self, mock_producer, registry, context_reader, settings
    ):
        ws = make_ws([])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings)
        await adapter.handle()

        # Find the contact_open event in producer.send calls
        events_topic = settings.kafka_topic_events
        event_calls = [
            c for c in mock_producer.send.call_args_list
            if c.args[0] == events_topic
        ]
        assert len(event_calls) >= 1
        first_event = json.loads(event_calls[0].kwargs["value"].decode())
        assert first_event["event_type"] == "contact_open"
        assert first_event["contact_id"] == CONTACT_ID
        assert first_event["channel"] == "webchat"

    async def test_publishes_contact_closed_on_disconnect(
        self, mock_producer, registry, context_reader, settings
    ):
        ws = make_ws([])  # immediate disconnect
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings)
        await adapter.handle()

        events_topic = settings.kafka_topic_events
        event_calls = [
            c for c in mock_producer.send.call_args_list
            if c.args[0] == events_topic
        ]
        # Last event should be contact_closed
        last_event = json.loads(event_calls[-1].kwargs["value"].decode())
        assert last_event["event_type"] == "contact_closed"
        assert last_event["contact_id"] == CONTACT_ID
        assert last_event["reason"] == "client_disconnect"

    async def test_unregisters_on_disconnect(
        self, mock_producer, registry, context_reader, settings
    ):
        ws = make_ws([])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings)
        await adapter.handle()
        registry.unregister.assert_called_once_with(CONTACT_ID)


# ── Inbound text message ──────────────────────────────────────────────────────

class TestInboundTextMessage:
    async def test_text_message_published_to_inbound(
        self, mock_producer, registry, context_reader, settings
    ):
        msg = json.dumps({"type": "message.text", "text": "Quero cancelar meu plano"})
        ws = make_ws([msg])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings)
        await adapter.handle()

        inbound_topic = settings.kafka_topic_inbound
        inbound_calls = [
            c for c in mock_producer.send.call_args_list
            if c.args[0] == inbound_topic
        ]
        assert len(inbound_calls) == 1
        event = json.loads(inbound_calls[0].kwargs["value"].decode())
        assert event["direction"] == "inbound"
        assert event["channel"] == "webchat"
        assert event["contact_id"] == CONTACT_ID
        assert event["session_id"] == SESSION_ID
        assert event["author"]["type"] == "customer"
        assert event["content"]["type"] == "text"
        assert event["content"]["text"] == "Quero cancelar meu plano"

    async def test_text_message_includes_context_snapshot(
        self, mock_producer, registry, context_reader, settings
    ):
        context_reader.get_snapshot.return_value = ContextSnapshot(
            intent="churn_risk",
            sentiment_score=0.3,
            turn_number=2,
        )
        msg = json.dumps({"type": "message.text", "text": "Não estou satisfeito"})
        ws = make_ws([msg])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings)
        await adapter.handle()

        inbound_calls = [
            c for c in mock_producer.send.call_args_list
            if c.args[0] == settings.kafka_topic_inbound
        ]
        event = json.loads(inbound_calls[0].kwargs["value"].decode())
        snap = event["context_snapshot"]
        assert snap["intent"] == "churn_risk"
        assert snap["sentiment_score"] == 0.3
        assert snap["turn_number"] == 2

    async def test_multiple_messages_all_published(
        self, mock_producer, registry, context_reader, settings
    ):
        messages = [
            json.dumps({"type": "message.text", "text": f"msg {i}"})
            for i in range(3)
        ]
        ws = make_ws(messages)
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings)
        await adapter.handle()

        inbound_calls = [
            c for c in mock_producer.send.call_args_list
            if c.args[0] == settings.kafka_topic_inbound
        ]
        assert len(inbound_calls) == 3


# ── Menu submit ───────────────────────────────────────────────────────────────

class TestMenuSubmit:
    async def test_button_submit_published(
        self, mock_producer, registry, context_reader, settings
    ):
        msg = json.dumps({
            "type": "menu.submit",
            "menu_id": "menu-001",
            "interaction": "button",
            "result": "opt_cancel",
        })
        ws = make_ws([msg])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings)
        await adapter.handle()

        inbound_calls = [
            c for c in mock_producer.send.call_args_list
            if c.args[0] == settings.kafka_topic_inbound
        ]
        assert len(inbound_calls) == 1
        event = json.loads(inbound_calls[0].kwargs["value"].decode())
        assert event["content"]["type"] == "menu_result"
        payload = event["content"]["payload"]
        assert payload["menu_id"] == "menu-001"
        assert payload["interaction"] == "button"
        assert payload["result"] == "opt_cancel"

    async def test_form_submit_published(
        self, mock_producer, registry, context_reader, settings
    ):
        msg = json.dumps({
            "type": "menu.submit",
            "menu_id": "form-001",
            "interaction": "form",
            "result": {"name": "João", "cpf": "000.000.000-00"},
        })
        ws = make_ws([msg])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings)
        await adapter.handle()

        inbound_calls = [
            c for c in mock_producer.send.call_args_list
            if c.args[0] == settings.kafka_topic_inbound
        ]
        event = json.loads(inbound_calls[0].kwargs["value"].decode())
        assert event["content"]["type"] == "menu_result"
        assert event["content"]["payload"]["result"]["name"] == "João"

    async def test_checklist_submit_published(
        self, mock_producer, registry, context_reader, settings
    ):
        msg = json.dumps({
            "type": "menu.submit",
            "menu_id": "check-001",
            "interaction": "checklist",
            "result": ["opt_a", "opt_c"],
        })
        ws = make_ws([msg])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings)
        await adapter.handle()

        inbound_calls = [
            c for c in mock_producer.send.call_args_list
            if c.args[0] == settings.kafka_topic_inbound
        ]
        event = json.loads(inbound_calls[0].kwargs["value"].decode())
        assert event["content"]["payload"]["result"] == ["opt_a", "opt_c"]


# ── Pong handling ─────────────────────────────────────────────────────────────

class TestPongHandling:
    async def test_pong_does_not_publish_inbound(
        self, mock_producer, registry, context_reader, settings
    ):
        """Heartbeat pong response must not produce an inbound event."""
        pong = json.dumps({"type": "pong"})
        ws = make_ws([pong])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings)
        await adapter.handle()

        inbound_calls = [
            c for c in mock_producer.send.call_args_list
            if c.args[0] == settings.kafka_topic_inbound
        ]
        assert len(inbound_calls) == 0


# ── Close from platform ───────────────────────────────────────────────────────

class TestClosedFromPlatform:
    async def test_close_from_platform_unregisters_and_publishes(
        self, mock_producer, registry, context_reader, settings
    ):
        ws = AsyncMock()
        ws.close = AsyncMock()
        adapter = WebchatAdapter(
            ws=ws,
            contact_id=CONTACT_ID,
            session_id=SESSION_ID,
            producer=mock_producer,
            registry=registry,
            context_reader=context_reader,
            settings=settings,
        )
        await adapter.close_from_platform(reason="agent_done")

        registry.unregister.assert_called_once_with(CONTACT_ID)
        ws.close.assert_called_once()

        events_calls = [
            c for c in mock_producer.send.call_args_list
            if c.args[0] == settings.kafka_topic_events
        ]
        event = json.loads(events_calls[0].kwargs["value"].decode())
        assert event["event_type"] == "contact_closed"
        assert event["reason"] == "agent_done"


# ── Normalization completeness ────────────────────────────────────────────────

class TestEventFields:
    async def test_inbound_event_has_required_fields(
        self, mock_producer, registry, context_reader, settings
    ):
        """All required NormalizedInboundEvent fields must be present."""
        msg = json.dumps({"type": "message.text", "text": "teste"})
        ws = make_ws([msg])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings)
        await adapter.handle()

        inbound_calls = [
            c for c in mock_producer.send.call_args_list
            if c.args[0] == settings.kafka_topic_inbound
        ]
        event = json.loads(inbound_calls[0].kwargs["value"].decode())

        required = [
            "message_id", "contact_id", "session_id", "timestamp",
            "direction", "channel", "author", "content", "context_snapshot",
        ]
        for field in required:
            assert field in event, f"Missing field: {field}"

    async def test_contact_open_has_required_fields(
        self, mock_producer, registry, context_reader, settings
    ):
        ws = make_ws([])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings)
        await adapter.handle()

        events_calls = [
            c for c in mock_producer.send.call_args_list
            if c.args[0] == settings.kafka_topic_events
        ]
        open_event = json.loads(events_calls[0].kwargs["value"].decode())
        assert open_event["event_type"] == "contact_open"
        for field in ["contact_id", "session_id", "channel", "started_at"]:
            assert field in open_event, f"Missing field: {field}"
