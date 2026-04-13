"""
test_writer.py
Unit tests for ConversationWriter._dispatch logic.
Covers: message buffering, lifecycle metadata accumulation,
        contact_closed persistence flow, transcript.created publishing.
"""

from __future__ import annotations
import json
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from plughub_conversation_writer.models import ContactMeta, InboundMessage
from plughub_conversation_writer.redis_buffer import RedisBuffer
from plughub_conversation_writer.writer import ConversationWriter

from .conftest import make_message, make_contact_closed


CONTACT_ID = "cid-test-001"


@pytest.fixture
def buffer():
    b = AsyncMock(spec=RedisBuffer)
    b.append_message = AsyncMock()
    b.get_messages   = AsyncMock(return_value=[])
    b.get_meta       = AsyncMock(return_value=ContactMeta(contact_id=CONTACT_ID))
    b.upsert_meta    = AsyncMock()
    b.cleanup        = AsyncMock()
    return b


@pytest.fixture
def writer(buffer, mock_db, mock_producer, settings):
    return ConversationWriter(
        buffer=buffer,
        db=mock_db,
        producer=mock_producer,
        settings=settings,
    )


# ── Message buffering ─────────────────────────────────────────────────────────

class TestMessageHandling:
    async def test_inbound_message_appended_to_buffer(self, writer, buffer, settings):
        payload = make_message(contact_id=CONTACT_ID, direction="inbound")
        await writer._dispatch(settings.kafka_topic_inbound, payload)
        buffer.append_message.assert_called_once()
        msg: InboundMessage = buffer.append_message.call_args.args[0]
        assert msg.contact_id == CONTACT_ID
        assert msg.direction == "inbound"

    async def test_outbound_message_appended_to_buffer(self, writer, buffer, settings):
        payload = make_message(contact_id=CONTACT_ID, direction="outbound")
        await writer._dispatch(settings.kafka_topic_outbound, payload)
        buffer.append_message.assert_called_once()
        msg: InboundMessage = buffer.append_message.call_args.args[0]
        assert msg.direction == "outbound"

    async def test_invalid_message_is_skipped(self, writer, buffer, settings):
        """Malformed message does not raise and does not call append."""
        await writer._dispatch(settings.kafka_topic_inbound, {"not": "a message"})
        buffer.append_message.assert_not_called()


# ── Contact lifecycle events ──────────────────────────────────────────────────

class TestLifecycleEvents:
    async def test_contact_open_upserts_meta(self, writer, buffer, settings):
        payload = {
            "event_type": "contact_open",
            "contact_id": CONTACT_ID,
            "session_id": "sid-001",
            "channel": "chat",
            "started_at": "2024-01-01T10:00:00Z",
        }
        await writer._dispatch(settings.kafka_topic_events, payload)
        buffer.upsert_meta.assert_called_once_with(
            CONTACT_ID,
            session_id="sid-001",
            started_at="2024-01-01T10:00:00Z",
        )

    async def test_agent_done_upserts_pool_agent(self, writer, buffer, settings):
        payload = {
            "event_type": "agent_done",
            "contact_id": CONTACT_ID,
            "pool_id": "retencao_humano",
            "agent_id": str(uuid.uuid4()),
            "agent_type": "human",
            "outcome": "resolved",
        }
        await writer._dispatch(settings.kafka_topic_events, payload)
        buffer.upsert_meta.assert_called_once()
        kwargs = buffer.upsert_meta.call_args.kwargs
        # All fields should be passed (some may be positional)
        call_args_str = str(buffer.upsert_meta.call_args)
        assert "retencao_humano" in call_args_str
        assert "resolved" in call_args_str

    async def test_unknown_event_type_is_ignored(self, writer, buffer, settings):
        payload = {"event_type": "agent_login", "contact_id": CONTACT_ID}
        await writer._dispatch(settings.kafka_topic_events, payload)
        buffer.append_message.assert_not_called()
        buffer.upsert_meta.assert_not_called()


# ── contact_closed full flow ──────────────────────────────────────────────────

class TestContactClosed:
    async def _run_close(self, writer, buffer, mock_db, mock_producer, settings,
                         messages=None, meta_overrides=None):
        """Helper: set up mocks and call dispatch with contact_closed."""
        msgs = [InboundMessage.model_validate(m) for m in (messages or [
            make_message(contact_id=CONTACT_ID, text="Olá", turn=1),
            make_message(contact_id=CONTACT_ID, direction="outbound", text="Posso ajudar?", turn=1),
        ])]
        buffer.get_messages.return_value = msgs

        meta = ContactMeta(
            contact_id=CONTACT_ID,
            pool_id="retencao_humano",
            agent_id=str(uuid.uuid4()),
            agent_type="human",
            outcome="resolved",
            started_at="2024-01-01T10:00:00Z",
            ended_at="2024-01-01T10:30:00Z",
            **(meta_overrides or {}),
        )
        buffer.get_meta.return_value = meta

        transcript_id = str(uuid.uuid4())
        mock_db.persist_transcript.return_value = transcript_id

        payload = make_contact_closed(contact_id=CONTACT_ID)
        await writer._dispatch(settings.kafka_topic_events, payload)
        return transcript_id, msgs, meta

    async def test_persists_messages_to_db(self, writer, buffer, mock_db, mock_producer, settings):
        await self._run_close(writer, buffer, mock_db, mock_producer, settings)
        mock_db.persist_transcript.assert_called_once()

    async def test_persist_called_with_correct_messages(
        self, writer, buffer, mock_db, mock_producer, settings
    ):
        transcript_id, msgs, meta = await self._run_close(
            writer, buffer, mock_db, mock_producer, settings
        )
        call_kwargs = mock_db.persist_transcript.call_args.kwargs
        assert call_kwargs["meta"].contact_id == CONTACT_ID
        assert len(call_kwargs["messages"]) == 2

    async def test_publishes_transcript_created(
        self, writer, buffer, mock_db, mock_producer, settings
    ):
        await self._run_close(writer, buffer, mock_db, mock_producer, settings)
        mock_producer.send.assert_called_once()
        topic, = mock_producer.send.call_args.args
        assert topic == settings.kafka_topic_eval_events

    async def test_transcript_created_has_correct_fields(
        self, writer, buffer, mock_db, mock_producer, settings
    ):
        transcript_id, _, _ = await self._run_close(
            writer, buffer, mock_db, mock_producer, settings
        )
        raw = mock_producer.send.call_args.kwargs["value"].decode()
        event = json.loads(raw)
        assert event["event_type"] == "transcript.created"
        assert event["contact_id"] == CONTACT_ID
        assert event["transcript_id"] == transcript_id
        assert event["turn_count"] == 2
        assert "created_at" in event

    async def test_cleanup_called_after_success(
        self, writer, buffer, mock_db, mock_producer, settings
    ):
        await self._run_close(writer, buffer, mock_db, mock_producer, settings)
        buffer.cleanup.assert_called_once_with(CONTACT_ID)

    async def test_no_cleanup_on_db_failure(
        self, writer, buffer, mock_db, mock_producer, settings
    ):
        buffer.get_messages.return_value = [
            InboundMessage.model_validate(make_message(contact_id=CONTACT_ID))
        ]
        buffer.get_meta.return_value = ContactMeta(contact_id=CONTACT_ID)
        mock_db.persist_transcript.side_effect = Exception("DB connection lost")

        payload = make_contact_closed(contact_id=CONTACT_ID)
        await writer._dispatch(settings.kafka_topic_events, payload)

        mock_producer.send.assert_not_called()
        buffer.cleanup.assert_not_called()

    async def test_empty_messages_still_persists(
        self, writer, buffer, mock_db, mock_producer, settings
    ):
        """contact_closed with no buffered messages — write empty transcript."""
        await self._run_close(
            writer, buffer, mock_db, mock_producer, settings,
            messages=[],
        )
        mock_db.persist_transcript.assert_called_once()

    async def test_invalid_contact_closed_is_skipped(
        self, writer, buffer, mock_db, mock_producer, settings
    ):
        """Malformed contact_closed event does not crash."""
        payload = {"event_type": "contact_closed", "invalid": True}
        await writer._dispatch(settings.kafka_topic_events, payload)
        mock_db.persist_transcript.assert_not_called()


# ── Error isolation ───────────────────────────────────────────────────────────

class TestErrorIsolation:
    async def test_dispatch_error_does_not_propagate(
        self, writer, buffer, settings
    ):
        """Any exception inside _dispatch must be caught — never crash the consumer loop."""
        buffer.append_message.side_effect = Exception("unexpected error")
        payload = make_message(contact_id=CONTACT_ID)
        # Should not raise
        await writer._dispatch(settings.kafka_topic_inbound, payload)
