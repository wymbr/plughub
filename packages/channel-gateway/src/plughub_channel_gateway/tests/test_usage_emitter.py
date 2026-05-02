"""
test_usage_emitter.py
Unit tests for the Channel Gateway usage emitter.

Strategy:
  - All functions are fire-and-forget async; tested by inspecting mock producer calls.
  - Event payload structure is validated against the expected schema fields.
  - Error path: producer.send raises → function returns without propagating.
"""
from __future__ import annotations

import json
import math
from unittest.mock import AsyncMock, MagicMock

import pytest

from plughub_channel_gateway.usage_emitter import (
    emit_attachment,
    emit_email_message,
    emit_sms_segment,
    emit_voice_minutes,
    emit_whatsapp_conversation,
)

TENANT    = "tenant_test"
SESSION   = "sess-usage-001"
CONTACT   = "cid-usage-001"


# ── helpers ────────────────────────────────────────────────────────────────────

def make_producer() -> MagicMock:
    """Returns a mock producer whose send() is an AsyncMock."""
    p = MagicMock()
    p.send = AsyncMock()
    return p


def captured_event(producer: MagicMock) -> dict:
    """Extracts and JSON-parses the first payload sent to producer.send."""
    call = producer.send.call_args
    # send(topic, value=<bytes>)
    value_bytes: bytes = call.kwargs.get("value") or call.args[1]
    return json.loads(value_bytes)


# ── whatsapp_conversations ────────────────────────────────────────────────────

class TestEmitWhatsappConversation:
    async def test_sends_to_usage_events_topic(self):
        p = make_producer()
        await emit_whatsapp_conversation(p, TENANT, SESSION, CONTACT)
        topic = p.send.call_args.args[0]
        assert topic == "usage.events"

    async def test_event_fields(self):
        p = make_producer()
        await emit_whatsapp_conversation(p, TENANT, SESSION, CONTACT)
        ev = captured_event(p)
        assert ev["dimension"]        == "whatsapp_conversations"
        assert ev["quantity"]         == 1
        assert ev["tenant_id"]        == TENANT
        assert ev["session_id"]       == SESSION
        assert ev["source_component"] == "channel-gateway"
        assert ev["metadata"]["channel"] == "whatsapp"
        assert ev["metadata"]["contact_id"] == CONTACT

    async def test_event_id_is_unique(self):
        p = make_producer()
        await emit_whatsapp_conversation(p, TENANT, SESSION, CONTACT)
        await emit_whatsapp_conversation(p, TENANT, SESSION, CONTACT)
        calls = p.send.call_args_list
        id1 = json.loads(calls[0].kwargs["value"])["event_id"]
        id2 = json.loads(calls[1].kwargs["value"])["event_id"]
        assert id1 != id2

    async def test_silently_ignores_producer_error(self):
        p = make_producer()
        p.send = AsyncMock(side_effect=Exception("kafka down"))
        # Should NOT raise
        await emit_whatsapp_conversation(p, TENANT, SESSION, CONTACT)


# ── voice_minutes ─────────────────────────────────────────────────────────────

class TestEmitVoiceMinutes:
    async def test_rounds_up_to_nearest_minute(self):
        p = make_producer()
        await emit_voice_minutes(p, TENANT, SESSION, CONTACT, duration_seconds=61)
        ev = captured_event(p)
        assert ev["quantity"] == 2  # ceil(61/60) = 2

    async def test_exact_minute_does_not_round_up(self):
        p = make_producer()
        await emit_voice_minutes(p, TENANT, SESSION, CONTACT, duration_seconds=120)
        ev = captured_event(p)
        assert ev["quantity"] == 2  # exactly 2 minutes

    async def test_partial_first_minute_is_1(self):
        p = make_producer()
        await emit_voice_minutes(p, TENANT, SESSION, CONTACT, duration_seconds=30)
        ev = captured_event(p)
        assert ev["quantity"] == 1  # max(1, ceil(30/60)) = 1

    async def test_zero_seconds_is_1_minute(self):
        """Even a zero-duration call bills at minimum 1 minute."""
        p = make_producer()
        await emit_voice_minutes(p, TENANT, SESSION, CONTACT, duration_seconds=0)
        ev = captured_event(p)
        assert ev["quantity"] == 1

    async def test_duration_in_metadata(self):
        p = make_producer()
        await emit_voice_minutes(p, TENANT, SESSION, CONTACT, duration_seconds=90.5)
        ev = captured_event(p)
        assert ev["metadata"]["duration_seconds"] == 90.5
        assert ev["metadata"]["channel"] == "webrtc"

    async def test_dimension_name(self):
        p = make_producer()
        await emit_voice_minutes(p, TENANT, SESSION, CONTACT, duration_seconds=60)
        ev = captured_event(p)
        assert ev["dimension"] == "voice_minutes"

    async def test_silently_ignores_producer_error(self):
        p = make_producer()
        p.send = AsyncMock(side_effect=RuntimeError("timeout"))
        await emit_voice_minutes(p, TENANT, SESSION, CONTACT, duration_seconds=120)


# ── sms_segments ──────────────────────────────────────────────────────────────

class TestEmitSmsSegment:
    async def test_inbound_event(self):
        p = make_producer()
        await emit_sms_segment(p, TENANT, SESSION, CONTACT, direction="inbound")
        ev = captured_event(p)
        assert ev["dimension"]           == "sms_segments"
        assert ev["quantity"]            == 1
        assert ev["metadata"]["direction"] == "inbound"
        assert ev["metadata"]["channel"]   == "sms"

    async def test_outbound_event(self):
        p = make_producer()
        await emit_sms_segment(p, TENANT, SESSION, CONTACT, direction="outbound")
        ev = captured_event(p)
        assert ev["metadata"]["direction"] == "outbound"

    async def test_silently_ignores_producer_error(self):
        p = make_producer()
        p.send = AsyncMock(side_effect=Exception("broker unavailable"))
        await emit_sms_segment(p, TENANT, SESSION, CONTACT, direction="inbound")


# ── email_messages ────────────────────────────────────────────────────────────

class TestEmitEmailMessage:
    async def test_outbound_email(self):
        p = make_producer()
        await emit_email_message(p, TENANT, SESSION, CONTACT, direction="outbound")
        ev = captured_event(p)
        assert ev["dimension"]           == "email_messages"
        assert ev["quantity"]            == 1
        assert ev["metadata"]["channel"] == "email"
        assert ev["metadata"]["direction"] == "outbound"

    async def test_inbound_email(self):
        p = make_producer()
        await emit_email_message(p, TENANT, SESSION, CONTACT, direction="inbound")
        ev = captured_event(p)
        assert ev["metadata"]["direction"] == "inbound"

    async def test_silently_ignores_producer_error(self):
        p = make_producer()
        p.send = AsyncMock(side_effect=Exception("network error"))
        await emit_email_message(p, TENANT, SESSION, CONTACT, direction="outbound")


# ── webchat_attachments ───────────────────────────────────────────────────────

class TestEmitAttachment:
    async def test_attachment_event_fields(self):
        p = make_producer()
        await emit_attachment(
            producer   = p,
            tenant_id  = TENANT,
            session_id = SESSION,
            file_id    = "file-123",
            mime_type  = "image/jpeg",
            size_bytes = 204800,
        )
        ev = captured_event(p)
        assert ev["dimension"]             == "webchat_attachments"
        assert ev["quantity"]              == 1
        assert ev["tenant_id"]             == TENANT
        assert ev["session_id"]            == SESSION
        assert ev["metadata"]["file_id"]   == "file-123"
        assert ev["metadata"]["mime_type"] == "image/jpeg"
        assert ev["metadata"]["size_bytes"] == 204800
        assert ev["metadata"]["channel"]   == "webchat"

    async def test_attachment_event_id_present(self):
        p = make_producer()
        await emit_attachment(p, TENANT, SESSION, "fid", "application/pdf", 1024)
        ev = captured_event(p)
        assert "event_id" in ev
        assert len(ev["event_id"]) > 0

    async def test_attachment_timestamp_present(self):
        p = make_producer()
        await emit_attachment(p, TENANT, SESSION, "fid", "video/mp4", 5_000_000)
        ev = captured_event(p)
        assert "timestamp" in ev

    async def test_silently_ignores_producer_error(self):
        p = make_producer()
        p.send = AsyncMock(side_effect=Exception("disconnected"))
        await emit_attachment(p, TENANT, SESSION, "fid", "image/png", 512)

    async def test_source_component(self):
        p = make_producer()
        await emit_attachment(p, TENANT, SESSION, "fid", "image/jpeg", 100)
        ev = captured_event(p)
        assert ev["source_component"] == "channel-gateway"
