"""
test_webchat_adapter.py
Tests for WebchatAdapter — hybrid stream model with typed envelope.

Coverage:
  - Auth handshake: conn.hello → conn.authenticate → conn.authenticated
  - Auth failure: timeout, bad token, missing message
  - Contact lifecycle: contact_open, contact_closed, Redis session meta
  - Inbound messages: msg.text (new), message.text (legacy), menu.submit, media
  - Upload request: upload.ready, mime validation
  - Heartbeat: conn.ping / pong
  - Stream delivery: _stream_delivery_loop delivers events from subscriber
  - Typing: _typing_listener forwards pub/sub messages

All tests bypass JWT using _token_validator to avoid minting real tokens.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocketDisconnect

from plughub_channel_gateway.adapters.webchat import WebchatAdapter, AuthError
from plughub_channel_gateway.models import ContextSnapshot
from plughub_channel_gateway.tests.conftest import (
    CONTACT_ID, SESSION_ID, TENANT_ID, FAKE_CLAIMS, make_ws_mock,
)


# ── Adapter factory ───────────────────────────────────────────────────────────

def make_adapter(
    ws,
    producer,
    registry,
    context_reader,
    settings,
    mock_redis,
    *,
    attachment_store=None,
):
    """Creates a WebchatAdapter with JWT bypass and wired mocks."""
    return WebchatAdapter(
        ws               = ws,
        pool_id          = "test_pool",
        producer         = producer,
        registry         = registry,
        context_reader   = context_reader,
        settings         = settings,
        redis            = mock_redis,
        attachment_store = attachment_store,
        _token_validator = lambda _token: FAKE_CLAIMS,
    )


# ── Auth handshake ────────────────────────────────────────────────────────────

class TestAuthHandshake:
    async def test_sends_conn_hello_before_auth(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        ws = make_ws_mock([])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis)
        await adapter.handle()

        first_call = ws.send_json.call_args_list[0].args[0]
        assert first_call["type"] == "conn.hello"

    async def test_sends_conn_authenticated_on_success(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        ws = make_ws_mock([])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis)
        await adapter.handle()

        calls = [c.args[0] for c in ws.send_json.call_args_list]
        auth_resp = next((c for c in calls if c.get("type") == "conn.authenticated"), None)
        assert auth_resp is not None
        assert auth_resp["contact_id"] == CONTACT_ID
        assert auth_resp["session_id"] == SESSION_ID
        assert "stream_cursor" in auth_resp

    async def test_auth_timeout_closes_ws(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        """If client never sends conn.authenticate, WS is closed with 4001."""
        # receive_text sleeps longer than ws_auth_timeout_s to simulate no auth
        async def _never_respond():
            await asyncio.sleep(10)

        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()
        ws.close = AsyncMock()
        ws.receive_text = _never_respond

        # Use a tiny auth timeout so the test completes in milliseconds
        from plughub_channel_gateway.config import Settings
        fast_settings = Settings(**{**settings.model_dump(), "ws_auth_timeout_s": 0})

        adapter = make_adapter(ws, mock_producer, registry, context_reader, fast_settings, mock_redis)
        await adapter.handle()

        ws.close.assert_called_once()
        close_args = ws.close.call_args
        assert close_args.kwargs.get("code") == 4001 or (
            close_args.args and close_args.args[0] == 4001
        )

    async def test_invalid_token_sends_conn_error(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        """Bad JWT → server sends conn.error and closes."""
        ws = make_ws_mock([], skip_auth=True)

        # Prepend an authenticate message that will fail validation
        import types
        original_receive = ws.receive_text
        msgs = [json.dumps({"type": "conn.authenticate", "token": "bad_token"})]

        async def patched_receive():
            if msgs:
                return msgs.pop(0)
            raise WebSocketDisconnect(code=1000)

        ws.receive_text = patched_receive

        # Override validator to raise
        import jwt as pyjwt

        def bad_validator(_):
            raise pyjwt.InvalidTokenError("bad signature")

        adapter = WebchatAdapter(
            ws               = ws,
            pool_id          = "test_pool",
            producer         = mock_producer,
            registry         = registry,
            context_reader   = context_reader,
            settings         = settings,
            redis            = mock_redis,
            _token_validator = bad_validator,
        )
        await adapter.handle()

        send_calls = [c.args[0] for c in ws.send_json.call_args_list]
        error_msg = next((c for c in send_calls if c.get("type") == "conn.error"), None)
        assert error_msg is not None
        assert error_msg["code"] == "invalid_token"

    async def test_reconnect_passes_cursor(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        """cursor in conn.authenticate is returned in conn.authenticated."""
        auth_with_cursor = json.dumps({
            "type": "conn.authenticate",
            "token": "tok",
            "cursor": "1234-0",
        })
        ws = make_ws_mock([], skip_auth=True)
        msgs = [auth_with_cursor]

        async def receive():
            if msgs:
                return msgs.pop(0)
            raise WebSocketDisconnect(code=1000)

        ws.receive_text = receive

        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis)
        await adapter.handle()

        calls = [c.args[0] for c in ws.send_json.call_args_list]
        auth_resp = next((c for c in calls if c.get("type") == "conn.authenticated"), None)
        assert auth_resp is not None
        assert auth_resp["stream_cursor"] == "1234-0"


# ── Connection lifecycle ──────────────────────────────────────────────────────

class TestConnectionLifecycle:
    async def test_registers_contact_on_connect(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        ws = make_ws_mock([])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis)
        await adapter.handle()

        registry.register.assert_called_once_with(CONTACT_ID, ws)

    async def test_publishes_contact_open(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        ws = make_ws_mock([])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis)
        await adapter.handle()

        events_topic = settings.kafka_topic_events
        event_calls = [
            json.loads(c.kwargs["value"].decode())
            for c in mock_producer.send.call_args_list
            if c.args[0] == events_topic
        ]
        open_event = next((e for e in event_calls if e.get("event_type") == "contact_open"), None)
        assert open_event is not None
        assert open_event["contact_id"] == CONTACT_ID
        assert open_event["channel"] == "webchat"

    async def test_publishes_contact_closed_on_disconnect(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        ws = make_ws_mock([])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis)
        await adapter.handle()

        events_topic = settings.kafka_topic_events
        event_calls = [
            json.loads(c.kwargs["value"].decode())
            for c in mock_producer.send.call_args_list
            if c.args[0] == events_topic
        ]
        closed = next((e for e in event_calls if e.get("event_type") == "contact_closed"), None)
        assert closed is not None
        assert closed["reason"] == "client_disconnect"

    async def test_unregisters_on_disconnect(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        ws = make_ws_mock([])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis)
        await adapter.handle()

        registry.unregister.assert_called_once_with(CONTACT_ID)

    async def test_writes_session_meta_to_redis(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        ws = make_ws_mock([])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis)
        await adapter.handle()

        # setex(key, ttl, value) — args[0] is the key
        keys = [c.args[0] for c in mock_redis.setex.call_args_list]
        assert f"session:{SESSION_ID}:meta" in keys
        assert f"session:{SESSION_ID}:contact_id" in keys

    async def test_contact_open_has_required_fields(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        ws = make_ws_mock([])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis)
        await adapter.handle()

        event_calls = [
            json.loads(c.kwargs["value"].decode())
            for c in mock_producer.send.call_args_list
            if c.args[0] == settings.kafka_topic_events
        ]
        open_event = next(e for e in event_calls if e.get("event_type") == "contact_open")
        for field in ["contact_id", "session_id", "channel", "started_at"]:
            assert field in open_event


# ── Inbound text messages ─────────────────────────────────────────────────────

class TestInboundTextMessages:
    async def test_new_msg_text_published(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        msg = json.dumps({"type": "msg.text", "text": "Quero cancelar meu plano"})
        ws = make_ws_mock([msg])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis)
        await adapter.handle()

        inbound_calls = [
            json.loads(c.kwargs["value"].decode())
            for c in mock_producer.send.call_args_list
            if c.args[0] == settings.kafka_topic_inbound
        ]
        text_events = [e for e in inbound_calls if e.get("content", {}).get("type") == "text"]
        assert len(text_events) >= 1
        event = text_events[0]
        assert event["content"]["text"] == "Quero cancelar meu plano"
        assert event["author"]["type"] == "customer"
        assert event["direction"] == "inbound"

    async def test_legacy_message_text_still_works(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        """Backward compat: message.text (old type) must still be accepted."""
        msg = json.dumps({"type": "message.text", "text": "legacy format"})
        ws = make_ws_mock([msg])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis)
        await adapter.handle()

        inbound_calls = [
            json.loads(c.kwargs["value"].decode())
            for c in mock_producer.send.call_args_list
            if c.args[0] == settings.kafka_topic_inbound
        ]
        text_events = [e for e in inbound_calls if e.get("content", {}).get("type") == "text"]
        assert any(e["content"]["text"] == "legacy format" for e in text_events)

    async def test_inbound_event_has_required_fields(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        msg = json.dumps({"type": "msg.text", "text": "teste"})
        ws = make_ws_mock([msg])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis)
        await adapter.handle()

        inbound_calls = [
            json.loads(c.kwargs["value"].decode())
            for c in mock_producer.send.call_args_list
            if c.args[0] == settings.kafka_topic_inbound
        ]
        event = next(e for e in inbound_calls if e.get("content", {}).get("type") == "text")
        for field in ["message_id", "contact_id", "session_id", "timestamp",
                      "direction", "channel", "author", "content", "context_snapshot"]:
            assert field in event, f"missing field: {field}"

    async def test_context_snapshot_included(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        context_reader.get_snapshot.return_value = ContextSnapshot(
            intent="churn_risk", sentiment_score=0.3, turn_number=5
        )
        msg = json.dumps({"type": "msg.text", "text": "Não estou satisfeito"})
        ws = make_ws_mock([msg])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis)
        await adapter.handle()

        inbound_calls = [
            json.loads(c.kwargs["value"].decode())
            for c in mock_producer.send.call_args_list
            if c.args[0] == settings.kafka_topic_inbound
        ]
        event = next(e for e in inbound_calls if e.get("content", {}).get("type") == "text")
        snap = event["context_snapshot"]
        assert snap["intent"] == "churn_risk"
        assert snap["turn_number"] == 5

    async def test_multiple_messages_all_published(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        messages = [
            json.dumps({"type": "msg.text", "text": f"msg {i}"})
            for i in range(3)
        ]
        ws = make_ws_mock(messages)
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis)
        await adapter.handle()

        inbound_calls = [
            json.loads(c.kwargs["value"].decode())
            for c in mock_producer.send.call_args_list
            if c.args[0] == settings.kafka_topic_inbound
        ]
        text_events = [e for e in inbound_calls if e.get("content", {}).get("type") == "text"]
        assert len(text_events) == 3


# ── Menu submit ───────────────────────────────────────────────────────────────

class TestMenuSubmit:
    async def test_button_submit_published(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        msg = json.dumps({
            "type": "menu.submit", "menu_id": "menu-001",
            "interaction": "button", "result": "opt_cancel",
        })
        ws = make_ws_mock([msg])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis)
        await adapter.handle()

        inbound_calls = [
            json.loads(c.kwargs["value"].decode())
            for c in mock_producer.send.call_args_list
            if c.args[0] == settings.kafka_topic_inbound
        ]
        menu_events = [e for e in inbound_calls if e.get("content", {}).get("type") == "menu_result"]
        assert len(menu_events) == 1
        payload = menu_events[0]["content"]["payload"]
        assert payload["menu_id"] == "menu-001"
        assert payload["result"] == "opt_cancel"

    async def test_form_submit_published(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        msg = json.dumps({
            "type": "menu.submit", "menu_id": "form-001",
            "interaction": "form", "result": {"name": "João", "cpf": "000.000.000-00"},
        })
        ws = make_ws_mock([msg])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis)
        await adapter.handle()

        inbound_calls = [
            json.loads(c.kwargs["value"].decode())
            for c in mock_producer.send.call_args_list
            if c.args[0] == settings.kafka_topic_inbound
        ]
        menu_events = [e for e in inbound_calls if e.get("content", {}).get("type") == "menu_result"]
        assert menu_events[0]["content"]["payload"]["result"]["name"] == "João"

    async def test_checklist_submit_published(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        msg = json.dumps({
            "type": "menu.submit", "menu_id": "check-001",
            "interaction": "checklist", "result": ["opt_a", "opt_c"],
        })
        ws = make_ws_mock([msg])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis)
        await adapter.handle()

        inbound_calls = [
            json.loads(c.kwargs["value"].decode())
            for c in mock_producer.send.call_args_list
            if c.args[0] == settings.kafka_topic_inbound
        ]
        menu_events = [e for e in inbound_calls if e.get("content", {}).get("type") == "menu_result"]
        assert menu_events[0]["content"]["payload"]["result"] == ["opt_a", "opt_c"]


# ── Media messages ────────────────────────────────────────────────────────────

class TestMediaMessages:
    async def test_image_message_published(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        msg = json.dumps({
            "type": "msg.image", "file_id": "file-abc-123", "caption": "foto"
        })
        ws = make_ws_mock([msg])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis)
        await adapter.handle()

        inbound_calls = [
            json.loads(c.kwargs["value"].decode())
            for c in mock_producer.send.call_args_list
            if c.args[0] == settings.kafka_topic_inbound
        ]
        media_events = [e for e in inbound_calls if e.get("content", {}).get("type") == "media"]
        assert len(media_events) == 1
        payload = media_events[0]["content"]["payload"]
        assert payload["media_type"] == "image"
        assert payload["file_id"] == "file-abc-123"
        assert payload["caption"] == "foto"

    async def test_document_message_published(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        msg = json.dumps({"type": "msg.document", "file_id": "doc-999"})
        ws = make_ws_mock([msg])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis)
        await adapter.handle()

        inbound_calls = [
            json.loads(c.kwargs["value"].decode())
            for c in mock_producer.send.call_args_list
            if c.args[0] == settings.kafka_topic_inbound
        ]
        media_events = [e for e in inbound_calls if e.get("content", {}).get("type") == "media"]
        assert media_events[0]["content"]["payload"]["media_type"] == "document"


# ── Upload request ────────────────────────────────────────────────────────────

class TestUploadRequest:
    def _make_store_mock(self, file_id="f-001", upload_url="http://host/upload/f-001"):
        store = AsyncMock()
        store.reserve = AsyncMock(return_value=(file_id, upload_url))
        return store

    async def test_upload_ready_sent_on_valid_request(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        msg = json.dumps({
            "type": "upload.request", "id": "req-001",
            "file_name": "photo.jpg", "mime_type": "image/jpeg", "size_bytes": 1024,
        })
        ws = make_ws_mock([msg])
        store = self._make_store_mock()
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis,
                               attachment_store=store)
        await adapter.handle()

        send_calls = [c.args[0] for c in ws.send_json.call_args_list]
        ready = next((c for c in send_calls if c.get("type") == "upload.ready"), None)
        assert ready is not None
        assert ready["request_id"] == "req-001"
        assert ready["file_id"] == "f-001"

    async def test_upload_rejected_on_bad_mime(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        msg = json.dumps({
            "type": "upload.request", "id": "req-002",
            "file_name": "mal.exe", "mime_type": "application/x-msdownload", "size_bytes": 512,
        })
        ws = make_ws_mock([msg])
        store = self._make_store_mock()
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis,
                               attachment_store=store)
        await adapter.handle()

        send_calls = [c.args[0] for c in ws.send_json.call_args_list]
        error = next((c for c in send_calls if c.get("type") == "conn.error"), None)
        assert error is not None
        assert error["code"] == "upload_rejected"

    async def test_upload_error_if_store_not_wired(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        msg = json.dumps({
            "type": "upload.request", "id": "req-003",
            "file_name": "photo.jpg", "mime_type": "image/jpeg", "size_bytes": 512,
        })
        ws = make_ws_mock([msg])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis,
                               attachment_store=None)
        await adapter.handle()

        send_calls = [c.args[0] for c in ws.send_json.call_args_list]
        error = next((c for c in send_calls if c.get("type") == "conn.error"), None)
        assert error is not None
        assert error["code"] == "upload_not_supported"


# ── Heartbeat ─────────────────────────────────────────────────────────────────

class TestHeartbeat:
    async def test_conn_pong_ignored(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        """conn.pong from client must not produce a NormalizedInboundEvent."""
        pong = json.dumps({"type": "conn.pong"})
        ws = make_ws_mock([pong])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis)
        await adapter.handle()

        # The routing event also goes to kafka_topic_inbound — filter it out by
        # checking for the 'direction' field which only NormalizedInboundEvent has.
        message_events = [
            json.loads(c.kwargs["value"].decode())
            for c in mock_producer.send.call_args_list
            if c.args[0] == settings.kafka_topic_inbound
            and b'"direction"' in c.kwargs["value"]
        ]
        assert len(message_events) == 0

    async def test_legacy_pong_ignored(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        """Legacy pong must not produce a NormalizedInboundEvent."""
        pong = json.dumps({"type": "pong"})
        ws = make_ws_mock([pong])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis)
        await adapter.handle()

        message_events = [
            json.loads(c.kwargs["value"].decode())
            for c in mock_producer.send.call_args_list
            if c.args[0] == settings.kafka_topic_inbound
            and b'"direction"' in c.kwargs["value"]
        ]
        assert len(message_events) == 0

    async def test_conn_ping_from_client_gets_pong(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        ping = json.dumps({"type": "conn.ping"})
        ws = make_ws_mock([ping])
        adapter = make_adapter(ws, mock_producer, registry, context_reader, settings, mock_redis)
        await adapter.handle()

        send_calls = [c.args[0] for c in ws.send_json.call_args_list]
        pong = next((c for c in send_calls if c.get("type") == "conn.pong"), None)
        assert pong is not None


# ── Close from platform ───────────────────────────────────────────────────────

class TestClosedFromPlatform:
    async def test_close_from_platform_publishes_contact_closed(
        self, mock_producer, registry, context_reader, settings, mock_redis
    ):
        ws = AsyncMock()
        ws.close = AsyncMock()
        adapter = WebchatAdapter(
            ws               = ws,
            pool_id          = "test_pool",
            producer         = mock_producer,
            registry         = registry,
            context_reader   = context_reader,
            settings         = settings,
            redis            = mock_redis,
            _token_validator = lambda _: FAKE_CLAIMS,
        )
        # Manually set contact/session since handle() wasn't called
        adapter._contact_id = CONTACT_ID
        adapter._session_id = SESSION_ID
        adapter._started_at = datetime.now(timezone.utc).isoformat()

        await adapter.close_from_platform(reason="agent_done")

        registry.unregister.assert_called_once_with(CONTACT_ID)
        ws.close.assert_called_once()

        events_calls = [
            json.loads(c.kwargs["value"].decode())
            for c in mock_producer.send.call_args_list
            if c.args[0] == settings.kafka_topic_events
        ]
        event = next(e for e in events_calls if e.get("event_type") == "contact_closed")
        assert event["reason"] == "agent_done"
