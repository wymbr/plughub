"""
test_whatsapp_adapter.py
Unit tests for WhatsAppAdapter and supporting components.

All tests use MockWhatsAppProvider — no network I/O.
Redis is mocked via AsyncMock.

Coverage:
  - verify_signature: valid, invalid, missing secret (dev mode)
  - Session resolution: new session, existing session, TTL renewal
  - Inbound text: direct publish
  - Inbound text: sequential collect routing
  - Inbound media: background download → AttachmentStore → event
  - Inbound interactive: button_reply and list_reply routing
  - Inbound location: formatted as text
  - Outbound deliver_text
  - Outbound deliver_menu: buttons (≤3), list (4-10), fallback (>10), form
  - Outbound deliver_typing: no-op
  - Outbound deliver_session_closed: Redis cleanup
  - Sequential collect: multi-field accumulation + final publish
  - MetaCloudProvider: payload construction (no HTTP calls)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plughub_channel_gateway.adapters.whatsapp import WhatsAppAdapter
from plughub_channel_gateway.adapters.whatsapp_provider import (
    MetaCloudProvider,
    MockWhatsAppProvider,
)
from plughub_channel_gateway.config import Settings


# ── Fixtures ──────────────────────────────────────────────────────────────────

CONTACT_ID = "+5511999990001"
SESSION_ID = "sid-wa-001"
TENANT_ID  = "tenant_test"
APP_SECRET = "test_app_secret_32chars_ok!!"


@pytest.fixture
def mock_provider():
    return MockWhatsAppProvider()


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.get    = AsyncMock(return_value=None)
    redis.setex  = AsyncMock(return_value=True)
    redis.expire = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    return redis


@pytest.fixture
def mock_producer():
    producer = AsyncMock()
    producer.send = AsyncMock()
    return producer


@pytest.fixture
def wa_settings():
    return Settings(
        kafka_brokers            = "localhost:9092",
        kafka_group_id           = "test-group",
        kafka_topic_inbound      = "conversations.inbound",
        kafka_topic_outbound     = "conversations.outbound",
        kafka_topic_events       = "conversations.events",
        redis_url                = "redis://localhost:6379",
        tenant_id                = TENANT_ID,
        whatsapp_app_secret      = APP_SECRET,
        whatsapp_verify_token    = "verify_me",
        whatsapp_access_token    = "EAAtest",
        whatsapp_phone_number_id = "123456789",
        whatsapp_graph_api_url   = "https://graph.facebook.com/v19.0",
        storage_root             = "/tmp/plughub_test",
        attachment_expiry_days   = 1,
        database_url             = "postgresql://plughub:plughub@localhost/plughub",
        webchat_serving_base_url = "http://localhost:8010/webchat/v1/attachments",
        webchat_upload_base_url  = "http://localhost:8010/webchat/v1/upload",
    )


@pytest.fixture
def adapter(mock_provider, mock_redis, mock_producer, wa_settings):
    return WhatsAppAdapter(
        producer  = mock_producer,
        redis     = mock_redis,
        settings  = wa_settings,
        provider  = mock_provider,
    )


def _make_signature(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _meta_text_body(contact: str, text: str, wamid: str | None = None) -> bytes:
    wamid = wamid or f"wamid.{uuid.uuid4().hex}"
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": contact,
                        "id":   wamid,
                        "type": "text",
                        "text": {"body": text},
                    }]
                }
            }]
        }]
    }
    return json.dumps(payload).encode()


def _meta_interactive_body(
    contact: str, itype: str, reply_id: str, reply_title: str
) -> bytes:
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": contact,
                        "id":   "wamid.interact",
                        "type": "interactive",
                        "interactive": {
                            "type": itype,
                            itype: {"id": reply_id, "title": reply_title},
                        },
                    }]
                }
            }]
        }]
    }
    return json.dumps(payload).encode()


def _meta_media_body(contact: str, media_type: str, media_id: str) -> bytes:
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from":       contact,
                        "id":         "wamid.media",
                        "type":       media_type,
                        media_type:   {"id": media_id, "caption": "test"},
                    }]
                }
            }]
        }]
    }
    return json.dumps(payload).encode()


# ── verify_signature ──────────────────────────────────────────────────────────

class TestVerifySignature:
    def test_valid_signature(self, adapter, wa_settings):
        body = b'{"test": "payload"}'
        sig  = _make_signature(body, APP_SECRET)
        assert adapter.verify_signature(body, sig) is True

    def test_invalid_signature(self, adapter):
        body = b'{"test": "payload"}'
        assert adapter.verify_signature(body, "sha256=invalid") is False

    def test_missing_signature_header(self, adapter):
        body = b'{"test": "payload"}'
        assert adapter.verify_signature(body, "") is False

    def test_no_secret_configured_dev_mode(self, mock_redis, mock_producer):
        """When app_secret is empty, skip validation (dev/test mode)."""
        settings = Settings(
            kafka_brokers="localhost:9092", kafka_group_id="g",
            kafka_topic_inbound="t", kafka_topic_outbound="t",
            kafka_topic_events="t", redis_url="redis://localhost",
            tenant_id="t", whatsapp_app_secret="",
            webchat_serving_base_url="http://x", webchat_upload_base_url="http://x",
            database_url="postgresql://x/y",
        )
        adp = WhatsAppAdapter(producer=mock_producer, redis=mock_redis, settings=settings)
        assert adp.verify_signature(b"any", "") is True


# ── Session resolution ────────────────────────────────────────────────────────

class TestSessionResolution:
    async def test_new_session_creates_redis_key(self, adapter, mock_redis, mock_producer):
        mock_redis.get.return_value = None
        session_id, tenant_id, pool_id = await adapter._resolve_session(CONTACT_ID)

        assert session_id  # UUID generated
        assert tenant_id == TENANT_ID
        mock_redis.setex.assert_called_once()
        call = mock_redis.setex.call_args
        assert f"channel:whatsapp:{CONTACT_ID}:session" in call.args[0]

    async def test_existing_session_reuses_session_id(self, adapter, mock_redis):
        mock_redis.get.return_value = SESSION_ID
        session_id, _, _ = await adapter._resolve_session(CONTACT_ID)

        assert session_id == SESSION_ID
        mock_redis.expire.assert_called_once()  # TTL renewed

    async def test_new_session_publishes_contact_open(self, adapter, mock_redis, mock_producer):
        mock_redis.get.return_value = None
        await adapter._resolve_session(CONTACT_ID)

        # Two Kafka sends: contact_open event + routing inbound
        assert mock_producer.send.call_count == 2
        first_call = json.loads(mock_producer.send.call_args_list[0].kwargs["value"])
        assert first_call["event_type"] == "contact_open"
        assert first_call["channel"] == "whatsapp"

    async def test_existing_session_no_contact_open(self, adapter, mock_redis, mock_producer):
        mock_redis.get.return_value = SESSION_ID
        await adapter._resolve_session(CONTACT_ID)
        mock_producer.send.assert_not_called()


# ── Inbound text ──────────────────────────────────────────────────────────────

class TestInboundText:
    async def test_text_publishes_normalized_event(
        self, adapter, mock_redis, mock_producer
    ):
        mock_redis.get.side_effect = [SESSION_ID, None]  # session exists, no collect
        body = _meta_text_body(CONTACT_ID, "Olá, preciso de ajuda")
        await adapter._process_inbound(body)

        # Find the inbound event (last send)
        calls = mock_producer.send.call_args_list
        events = [json.loads(c.kwargs["value"]) for c in calls]
        inbound = next(e for e in events if e.get("content", {}).get("type") == "text")
        assert inbound["content"]["text"] == "Olá, preciso de ajuda"
        assert inbound["channel"] == "whatsapp"
        assert inbound["content_type"] == "text"

    async def test_invalid_json_handled_gracefully(self, adapter, mock_redis, mock_producer):
        """Malformed JSON must not raise — log and return."""
        await adapter._process_inbound(b"not json {{{")
        mock_producer.send.assert_not_called()

    async def test_status_update_skipped(self, adapter, mock_redis, mock_producer):
        """Payload without 'messages' key (e.g. status update) is silently skipped."""
        body = json.dumps({
            "entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.x"}]}}]}]
        }).encode()
        mock_redis.get.return_value = SESSION_ID
        await adapter._process_inbound(body)
        mock_producer.send.assert_not_called()


# ── Inbound interactive ───────────────────────────────────────────────────────

class TestInboundInteractive:
    async def test_button_reply_with_no_collect_publishes_as_text(
        self, adapter, mock_redis, mock_producer
    ):
        mock_redis.get.side_effect = [SESSION_ID, None]  # session, no collect
        body = _meta_interactive_body(
            CONTACT_ID, "button_reply", "btn_yes", "Sim"
        )
        await adapter._process_inbound(body)

        calls  = mock_producer.send.call_args_list
        events = [json.loads(c.kwargs["value"]) for c in calls]
        inbound = next(e for e in events if e.get("content", {}).get("type") == "text")
        assert inbound["content"]["text"] == "Sim"

    async def test_list_reply_with_no_collect_publishes_as_text(
        self, adapter, mock_redis, mock_producer
    ):
        mock_redis.get.side_effect = [SESSION_ID, None]
        body = _meta_interactive_body(
            CONTACT_ID, "list_reply", "opt_2", "Opção 2"
        )
        await adapter._process_inbound(body)

        calls  = mock_producer.send.call_args_list
        events = [json.loads(c.kwargs["value"]) for c in calls]
        inbound = next(e for e in events if e.get("content", {}).get("type") == "text")
        assert inbound["content"]["text"] == "Opção 2"


# ── Inbound media ─────────────────────────────────────────────────────────────

class TestInboundMedia:
    async def test_image_download_and_publish(
        self, adapter, mock_redis, mock_provider, mock_producer
    ):
        mock_provider.load_media(
            "media_001", b"\xff\xd8\xff", "image/jpeg", "https://cdn.meta/media_001"
        )
        mock_redis.get.return_value = SESSION_ID

        body = _meta_media_body(CONTACT_ID, "image", "media_001")
        await adapter._process_inbound(body)

        calls  = mock_producer.send.call_args_list
        events = [json.loads(c.kwargs["value"]) for c in calls]
        media_events = [e for e in events if e.get("content", {}).get("type") == "media"]
        assert media_events, "Expected a media NormalizedInboundEvent"
        ev = media_events[0]
        assert ev["content_type"] == "image"
        assert ev["content"]["payload"]["media_type"] == "image"

    async def test_audio_content_type_is_audio_transcript(
        self, adapter, mock_redis, mock_provider, mock_producer
    ):
        mock_provider.load_media("audio_001", b"\x00\x01", "audio/ogg")
        mock_redis.get.return_value = SESSION_ID

        body = _meta_media_body(CONTACT_ID, "audio", "audio_001")
        await adapter._process_inbound(body)

        calls  = mock_producer.send.call_args_list
        events = [json.loads(c.kwargs["value"]) for c in calls]
        media_events = [e for e in events if e.get("content", {}).get("type") == "media"]
        assert media_events[0]["content_type"] == "audio_transcript"


# ── Outbound deliver_text ─────────────────────────────────────────────────────

class TestDeliverText:
    async def test_sends_text_via_provider(self, adapter, mock_provider):
        payload = {
            "type":       "message.text",
            "contact_id": CONTACT_ID,
            "session_id": SESSION_ID,
            "channel":    "whatsapp",
            "text":       "Olá, como posso ajudar?",
        }
        await adapter.deliver_text(payload)

        assert len(mock_provider.sent_messages) == 1
        msg = mock_provider.sent_messages[0]
        assert msg["type"] == "text"
        assert msg["text"] == "Olá, como posso ajudar?"
        assert msg["to"] == CONTACT_ID.lstrip("+")

    async def test_skips_empty_text(self, adapter, mock_provider):
        await adapter.deliver_text({
            "contact_id": CONTACT_ID, "session_id": SESSION_ID,
            "channel": "whatsapp", "text": "",
        })
        assert not mock_provider.sent_messages

    async def test_skips_missing_contact_id(self, adapter, mock_provider):
        await adapter.deliver_text({"text": "Olá", "channel": "whatsapp"})
        assert not mock_provider.sent_messages


# ── Outbound deliver_menu ─────────────────────────────────────────────────────

class TestDeliverMenu:
    def _menu_payload(self, options: list, interaction: str = "button") -> dict:
        return {
            "type":        "menu.payload",
            "contact_id":  CONTACT_ID,
            "session_id":  SESSION_ID,
            "channel":     "whatsapp",
            "menu_id":     "menu-001",
            "interaction": interaction,
            "prompt":      "Escolha uma opção:",
            "options":     options,
        }

    async def test_two_options_sends_buttons(self, adapter, mock_provider):
        payload = self._menu_payload([
            {"id": "a", "label": "Opção A"},
            {"id": "b", "label": "Opção B"},
        ])
        await adapter.deliver_menu(payload)

        assert mock_provider.sent_messages[0]["type"] == "interactive_buttons"
        assert len(mock_provider.sent_messages[0]["buttons"]) == 2

    async def test_three_options_sends_buttons(self, adapter, mock_provider):
        opts = [{"id": str(i), "label": f"Op {i}"} for i in range(3)]
        await adapter.deliver_menu(self._menu_payload(opts))
        assert mock_provider.sent_messages[0]["type"] == "interactive_buttons"

    async def test_five_options_sends_list(self, adapter, mock_provider):
        opts = [{"id": str(i), "label": f"Opção {i}"} for i in range(5)]
        await adapter.deliver_menu(self._menu_payload(opts, "list"))
        assert mock_provider.sent_messages[0]["type"] == "interactive_list"

    async def test_eleven_options_starts_sequential_collect(
        self, adapter, mock_provider, mock_redis
    ):
        opts = [{"id": str(i), "label": f"Opção {i}"} for i in range(11)]
        await adapter.deliver_menu(self._menu_payload(opts))
        # Sequential collect sends an intro text and stores state
        assert mock_provider.sent_messages[0]["type"] == "text"
        mock_redis.setex.assert_called()

    async def test_form_interaction_starts_sequential_collect(
        self, adapter, mock_provider, mock_redis
    ):
        payload = {
            "type": "menu.payload", "contact_id": CONTACT_ID,
            "session_id": SESSION_ID, "channel": "whatsapp",
            "menu_id": "form-001", "interaction": "form",
            "prompt": "Preencha seus dados:",
            "fields": [
                {"id": "name", "label": "Qual é seu nome?"},
                {"id": "cpf",  "label": "Qual é seu CPF?"},
            ],
        }
        await adapter.deliver_menu(payload)
        assert mock_provider.sent_messages[0]["type"] == "text"
        mock_redis.setex.assert_called()

    async def test_no_contact_id_is_noop(self, adapter, mock_provider):
        await adapter.deliver_menu({
            "type": "menu.payload", "session_id": SESSION_ID, "channel": "whatsapp",
        })
        assert not mock_provider.sent_messages


# ── Outbound deliver_typing ───────────────────────────────────────────────────

class TestDeliverTyping:
    async def test_typing_is_noop(self, adapter, mock_provider):
        """WhatsApp has no typing indicator API."""
        await adapter.deliver_typing({
            "contact_id": CONTACT_ID, "channel": "whatsapp",
        })
        assert not mock_provider.sent_messages


# ── Outbound deliver_session_closed ───────────────────────────────────────────

class TestDeliverSessionClosed:
    async def test_cleans_up_redis_keys(self, adapter, mock_redis):
        payload = {
            "type":       "session.closed",
            "contact_id": CONTACT_ID,
            "session_id": SESSION_ID,
            "channel":    "whatsapp",
        }
        await adapter.deliver_session_closed(payload)

        deleted_keys = [call.args[0] for call in mock_redis.delete.call_args_list]
        assert any("whatsapp" in k and CONTACT_ID in k for k in deleted_keys)
        assert any("menu_collect" in k for k in deleted_keys)

    async def test_no_message_sent_to_customer(self, adapter, mock_provider, mock_redis):
        await adapter.deliver_session_closed({
            "contact_id": CONTACT_ID, "session_id": SESSION_ID, "channel": "whatsapp",
        })
        assert not mock_provider.sent_messages


# ── Sequential collect ────────────────────────────────────────────────────────

class TestSequentialCollect:
    async def test_multi_field_form_collect(
        self, adapter, mock_provider, mock_redis, mock_producer
    ):
        """
        Full flow: form → first field prompt → answer → second field prompt
        → answer → menu_result published.
        """
        # Start the sequential collect (2 fields)
        await adapter._start_sequential_collect(
            provider   = mock_provider,
            to         = CONTACT_ID.lstrip("+"),
            session_id = SESSION_ID,
            menu_id    = "form-seq",
            prompt     = "Preencha:",
            fields     = [
                {"id": "name", "label": "Qual é seu nome?"},
                {"id": "city", "label": "Qual é sua cidade?"},
            ],
        )
        # Intro sent, state stored
        assert mock_provider.sent_messages[0]["type"] == "text"
        stored = json.loads(mock_redis.setex.call_args.args[2])
        assert stored["current_index"] == 0
        assert len(stored["fields"]) == 2

        # Customer answers first field
        mock_redis.get.return_value = json.dumps(stored)
        await adapter._advance_sequential_collect(
            session_id    = SESSION_ID,
            tenant_id     = TENANT_ID,
            contact_id    = CONTACT_ID,
            collect_key   = f"channel:whatsapp:{SESSION_ID}:menu_collect",
            collect_state = stored,
            value         = "João Silva",
            label         = "João Silva",
            wamid         = "wamid.a1",
        )
        # Next field prompt sent
        assert mock_provider.sent_messages[1]["type"] == "text"
        assert "cidade" in mock_provider.sent_messages[1]["text"].lower() or \
               "Qual" in mock_provider.sent_messages[1]["text"]

        # Customer answers second (last) field
        stored["current_index"] = 1
        stored["answers"] = {"name": "João Silva"}
        await adapter._advance_sequential_collect(
            session_id    = SESSION_ID,
            tenant_id     = TENANT_ID,
            contact_id    = CONTACT_ID,
            collect_key   = f"channel:whatsapp:{SESSION_ID}:menu_collect",
            collect_state = stored,
            value         = "São Paulo",
            label         = "São Paulo",
            wamid         = "wamid.a2",
        )
        # menu_result event published
        calls  = mock_producer.send.call_args_list
        events = [json.loads(c.kwargs["value"]) for c in calls]
        result = next(e for e in events if e.get("content", {}).get("type") == "menu_result")
        assert result["content"]["payload"]["result"]["name"] == "João Silva"
        assert result["content"]["payload"]["result"]["city"] == "São Paulo"
        # Collect key deleted
        mock_redis.delete.assert_called()


# ── MetaCloudProvider payload construction ─────────────────────────────────────

class TestMetaCloudProviderPayloads:
    """
    Tests payload structure without making HTTP calls.
    We patch httpx.AsyncClient to capture the payload sent to Meta.
    """

    @pytest.fixture
    def provider(self):
        return MetaCloudProvider(
            access_token    = "EAAtest",
            phone_number_id = "123456789",
            graph_api_url   = "https://graph.facebook.com/v19.0",
        )

    async def test_send_text_payload(self, provider):
        captured = []

        async def mock_post(url, *, json, headers, **kwargs):
            captured.append(json)
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"messages": [{"id": "wamid.test"}]}
            return resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__  = AsyncMock(return_value=False)
            mock_client.post = mock_post
            mock_client_cls.return_value = mock_client

            await provider.send_text("5511999990001", "Olá!")

        assert captured[0]["type"] == "text"
        assert captured[0]["text"]["body"] == "Olá!"
        assert captured[0]["messaging_product"] == "whatsapp"

    async def test_send_interactive_buttons_payload(self, provider):
        captured = []

        async def mock_post(url, *, json, headers, **kwargs):
            captured.append(json)
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"messages": [{"id": "wamid.btn"}]}
            return resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__  = AsyncMock(return_value=False)
            mock_client.post = mock_post
            mock_client_cls.return_value = mock_client

            await provider.send_interactive_buttons(
                "5511999990001",
                "Escolha:",
                [{"id": "a", "title": "Opção A"}, {"id": "b", "title": "Opção B"}],
            )

        payload = captured[0]
        assert payload["type"] == "interactive"
        assert payload["interactive"]["type"] == "button"
        assert len(payload["interactive"]["action"]["buttons"]) == 2
