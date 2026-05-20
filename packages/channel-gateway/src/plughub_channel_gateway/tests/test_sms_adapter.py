"""
test_sms_adapter.py
Suite de testes para SMSAdapter e sms_provider helpers.

Architecture: channel-gateway-multi-channel.md § 8.3

Covered:
  - split_sms helper (single, multi-segment, truncation)
  - TwilioProvider.verify_signature (valid, invalid, dev mode)
  - MockSMSProvider (send_text single + multi-segment)
  - SMSAdapter.process_inbound (valid, invalid signature)
  - SMSAdapter._accumulate_parts (single, multi, out-of-order)
  - SMSAdapter._resolve_session (new session, existing session)
  - SMSAdapter.deliver_text (short, long → multi-segment)
  - SMSAdapter.deliver_menu (numbered options, sequential collect start)
  - SMSAdapter.deliver_typing (no-op)
  - SMSAdapter.deliver_session_closed (Redis cleanup)
  - SMSAdapter sequential collect (advance, invalid selection, complete)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import pytest

from plughub_channel_gateway.adapters.sms import SMSAdapter
from plughub_channel_gateway.adapters.sms_provider import (
    MockSMSProvider,
    TwilioProvider,
    split_sms,
)
from plughub_channel_gateway.config import Settings

# ── Constants ─────────────────────────────────────────────────────────────────

TENANT_ID   = "tenant_test"
CONTACT_NUM = "+5511999990000"
FROM_NUMBER = "+15005550006"  # Twilio magic test number
AUTH_TOKEN  = "test_auth_token_32chars_sms_ok!!"
ACCOUNT_SID = "ACtest00000000000000000000000000000"
WEBHOOK_URL = "https://example.com/webhooks/sms"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sms_settings():
    return Settings(
        kafka_brokers       = "localhost:9092",
        kafka_group_id      = "test-group",
        kafka_topic_inbound = "conversations.inbound",
        kafka_topic_outbound= "conversations.outbound",
        kafka_topic_events  = "conversations.events",
        redis_url           = "redis://localhost:6379/0",
        tenant_id           = TENANT_ID,
        sms_account_sid     = ACCOUNT_SID,
        sms_auth_token      = AUTH_TOKEN,
        sms_from_number     = FROM_NUMBER,
        sms_default_pool_id = "sms_test_pool",
        # suppress unrelated fields that need defaults
        storage_root        = "/tmp",
        database_url        = "postgresql://plughub:plughub@localhost:5432/plughub",
        webchat_serving_base_url = "http://localhost:8010/webchat/v1/attachments",
        webchat_upload_base_url  = "http://localhost:8010/webchat/v1/upload",
        jwt_secret          = "changeme_32chars_webchat_secret!",
        session_ttl_seconds = 3600,
    )


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
def mock_provider():
    return MockSMSProvider()


@pytest.fixture
def adapter(sms_settings, mock_redis, mock_producer, mock_provider):
    return SMSAdapter(
        producer  = mock_producer,
        redis     = mock_redis,
        settings  = sms_settings,
        provider  = mock_provider,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _twilio_signature(url: str, params: dict, token: str) -> str:
    """Compute a valid Twilio HMAC-SHA1 signature for testing."""
    sorted_pairs = "".join(f"{k}{v}" for k, v in sorted(params.items()))
    string_to_sign = url + sorted_pairs
    mac = hmac.new(token.encode(), string_to_sign.encode(), hashlib.sha1)
    return base64.b64encode(mac.digest()).decode()


def _sms_params(
    from_num: str = CONTACT_NUM,
    body:     str = "Olá",
    sms_sid:  str = "SM_test_0001",
    num_segs: int = 1,
    part_seq: int = 1,
) -> dict[str, str]:
    params: dict[str, str] = {
        "From":        from_num,
        "To":          FROM_NUMBER,
        "Body":        body,
        "SmsSid":      sms_sid,
        "SmsMessageSid": sms_sid,
        "NumSegments": str(num_segs),
    }
    if num_segs > 1:
        params["PartSequenceNumber"] = str(part_seq)
    return params


# ══════════════════════════════════════════════════════════════════════════════
# split_sms helper
# ══════════════════════════════════════════════════════════════════════════════

class TestSplitSms:
    def test_short_text_single_segment(self):
        result = split_sms("Olá mundo")
        assert result == ["Olá mundo"]

    def test_exactly_160_chars_single(self):
        text = "x" * 160
        result = split_sms(text)
        assert len(result) == 1
        assert result[0] == text

    def test_161_chars_splits_into_two(self):
        text = "x" * 161
        result = split_sms(text)
        assert len(result) == 2
        assert "(1/2)" in result[0]
        assert "(2/2)" in result[1]

    def test_multi_segment_content_preserved(self):
        text = "A" * 500
        result = split_sms(text)
        # Remove suffixes and rejoin — should reconstruct original (minus truncation)
        joined = "".join(seg.split(" (")[0] for seg in result)
        assert joined == text

    def test_very_long_text_truncated_at_10_segments(self):
        text = "B" * 5000  # way over limit
        result = split_sms(text)
        assert len(result) <= 10

    def test_suffix_format(self):
        text = "C" * 400
        result = split_sms(text)
        total = len(result)
        assert result[0].endswith(f"(1/{total})")
        assert result[-1].endswith(f"({total}/{total})")


# ══════════════════════════════════════════════════════════════════════════════
# TwilioProvider
# ══════════════════════════════════════════════════════════════════════════════

class TestTwilioProviderSignature:
    @pytest.fixture
    def twilio(self):
        return TwilioProvider(
            account_sid = ACCOUNT_SID,
            auth_token  = AUTH_TOKEN,
            from_number = FROM_NUMBER,
        )

    @pytest.mark.asyncio
    async def test_valid_signature(self, twilio):
        params = _sms_params()
        sig = _twilio_signature(WEBHOOK_URL, params, AUTH_TOKEN)
        assert await twilio.verify_signature(WEBHOOK_URL, params, sig) is True

    @pytest.mark.asyncio
    async def test_invalid_signature(self, twilio):
        params = _sms_params()
        assert await twilio.verify_signature(WEBHOOK_URL, params, "bad_sig") is False

    @pytest.mark.asyncio
    async def test_dev_mode_always_valid(self):
        provider = TwilioProvider(
            account_sid = ACCOUNT_SID,
            auth_token  = AUTH_TOKEN,
            from_number = FROM_NUMBER,
            dev_mode    = True,
        )
        assert await provider.verify_signature(WEBHOOK_URL, {}, "wrong") is True


# ══════════════════════════════════════════════════════════════════════════════
# MockSMSProvider
# ══════════════════════════════════════════════════════════════════════════════

class TestMockSMSProvider:
    @pytest.mark.asyncio
    async def test_send_short_text_single_message(self):
        provider = MockSMSProvider()
        sid = await provider.send_text(to=CONTACT_NUM, body="Olá")
        assert len(provider.sent_messages) == 1
        assert provider.sent_messages[0]["body"] == "Olá"
        assert provider.sent_messages[0]["to"] == CONTACT_NUM
        assert sid.startswith("SM_mock_")

    @pytest.mark.asyncio
    async def test_send_long_text_multiple_messages(self):
        provider = MockSMSProvider()
        long_text = "X" * 400
        await provider.send_text(to=CONTACT_NUM, body=long_text)
        assert len(provider.sent_messages) >= 2
        for msg in provider.sent_messages:
            assert msg["to"] == CONTACT_NUM

    @pytest.mark.asyncio
    async def test_verify_signature_default_true(self):
        provider = MockSMSProvider()
        assert await provider.verify_signature("url", {}, "sig") is True

    @pytest.mark.asyncio
    async def test_verify_signature_configured_false(self):
        provider = MockSMSProvider(verify_result=False)
        assert await provider.verify_signature("url", {}, "sig") is False

    @pytest.mark.asyncio
    async def test_sid_increments(self):
        provider = MockSMSProvider()
        await provider.send_text(CONTACT_NUM, "msg1")
        await provider.send_text(CONTACT_NUM, "msg2")
        sids = [m["sid"] for m in provider.sent_messages]
        assert sids[0] != sids[1]


# ══════════════════════════════════════════════════════════════════════════════
# SMSAdapter — process_inbound (signature verification)
# ══════════════════════════════════════════════════════════════════════════════

class TestProcessInbound:
    @pytest.mark.asyncio
    async def test_invalid_signature_aborts(self):
        bad_provider = MockSMSProvider(verify_result=False)
        adapter = SMSAdapter(
            producer  = AsyncMock(),
            redis     = AsyncMock(),
            settings  = Settings(
                kafka_topic_inbound="t.inbound",
                kafka_topic_events="t.events",
                tenant_id=TENANT_ID,
            ),
            provider  = bad_provider,
        )
        await adapter.process_inbound(
            params=_sms_params(),
            signature="bad",
            url=WEBHOOK_URL,
        )
        # No messages sent — processing aborted
        assert bad_provider.sent_messages == []

    @pytest.mark.asyncio
    async def test_valid_signature_schedules_background_task(
        self, adapter, mock_redis, mock_producer
    ):
        params = _sms_params(body="Teste de inbound")
        mock_redis.get.return_value = None  # no existing session, no collect

        with patch.object(adapter, "_handle_inbound", new_callable=AsyncMock) as mock_handle:
            await adapter.process_inbound(
                params=params,
                signature="",  # MockProvider ignores signature
                url=WEBHOOK_URL,
            )
            # Give the background task a moment to run
            await asyncio.sleep(0.01)
            mock_handle.assert_called_once_with(params)


# ══════════════════════════════════════════════════════════════════════════════
# SMSAdapter — _accumulate_parts
# ══════════════════════════════════════════════════════════════════════════════

class TestAccumulateParts:
    @pytest.mark.asyncio
    async def test_single_segment_returns_immediately(self, adapter):
        result = await adapter._accumulate_parts(
            contact_id=CONTACT_NUM, sms_sid="SM001",
            body="Hello", part_seq=1, num_segments=1,
        )
        assert result == "Hello"

    @pytest.mark.asyncio
    async def test_first_of_two_segments_returns_none(self, adapter, mock_redis):
        mock_redis.get.return_value = None
        result = await adapter._accumulate_parts(
            contact_id=CONTACT_NUM, sms_sid="SM002",
            body="Part one ", part_seq=1, num_segments=2,
        )
        assert result is None
        mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_second_segment_returns_full_text(self, adapter, mock_redis):
        # Simulate first part already in Redis
        first_part = json.dumps([[1, "Hello "]])
        mock_redis.get.return_value = first_part

        result = await adapter._accumulate_parts(
            contact_id=CONTACT_NUM, sms_sid="SM003",
            body="World", part_seq=2, num_segments=2,
        )
        assert result == "Hello World"
        mock_redis.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_out_of_order_segments_assembled_correctly(self, adapter, mock_redis):
        # Part 2 arrives first, already stored
        second_part = json.dumps([[2, " World"]])
        mock_redis.get.return_value = second_part

        result = await adapter._accumulate_parts(
            contact_id=CONTACT_NUM, sms_sid="SM004",
            body="Hello", part_seq=1, num_segments=2,
        )
        assert result == "Hello World"

    @pytest.mark.asyncio
    async def test_duplicate_part_ignored(self, adapter, mock_redis):
        # Same part sequence already in buffer
        existing = json.dumps([[1, "Original"]])
        mock_redis.get.return_value = existing

        result = await adapter._accumulate_parts(
            contact_id=CONTACT_NUM, sms_sid="SM005",
            body="Duplicate", part_seq=1, num_segments=2,
        )
        # Should return None (still waiting for part 2) and not duplicate
        assert result is None
        stored_json = json.loads(mock_redis.setex.call_args[0][2])
        part_seqs = [p[0] for p in stored_json]
        assert part_seqs.count(1) == 1  # no duplicate


# ══════════════════════════════════════════════════════════════════════════════
# SMSAdapter — _resolve_session
# ══════════════════════════════════════════════════════════════════════════════

class TestResolveSession:
    @pytest.mark.asyncio
    async def test_new_session_created(self, adapter, mock_redis, mock_producer):
        mock_redis.get.return_value = None

        session_id, tenant_id = await adapter._resolve_session(
            contact_id=CONTACT_NUM,
            channel_session_id="SM_new",
        )

        assert session_id  # UUID generated
        assert tenant_id == TENANT_ID
        mock_redis.setex.assert_called_once()
        # Should publish contact_open event
        assert mock_producer.send.call_count >= 1

    @pytest.mark.asyncio
    async def test_existing_session_resumed(self, adapter, mock_redis, mock_producer):
        mock_redis.get.return_value = "existing-session-uuid"

        session_id, tenant_id = await adapter._resolve_session(
            contact_id=CONTACT_NUM,
            channel_session_id="SM_existing",
        )

        assert session_id == "existing-session-uuid"
        mock_redis.expire.assert_called_once()
        # No contact_open event published
        mock_producer.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_new_session_publishes_contact_open(self, adapter, mock_redis, mock_producer):
        mock_redis.get.return_value = None
        await adapter._resolve_session(CONTACT_NUM, "SM_x")

        sent_payloads = [
            json.loads(call.args[1].decode())
            for call in mock_producer.send.call_args_list
        ]
        event_types = [p.get("type", p.get("channel")) for p in sent_payloads]
        assert "contact_open" in event_types or "sms" in event_types


# ══════════════════════════════════════════════════════════════════════════════
# SMSAdapter — deliver_text
# ══════════════════════════════════════════════════════════════════════════════

class TestDeliverText:
    @pytest.mark.asyncio
    async def test_short_text_single_sms(self, adapter, mock_provider):
        await adapter.deliver_text({
            "contact_id": CONTACT_NUM,
            "tenant_id":  TENANT_ID,
            "content":    {"text": "Olá, tudo bem?"},
        })
        assert len(mock_provider.sent_messages) == 1
        assert mock_provider.sent_messages[0]["body"] == "Olá, tudo bem?"

    @pytest.mark.asyncio
    async def test_long_text_split_into_multiple_sms(self, adapter, mock_provider):
        long_text = "D" * 500
        await adapter.deliver_text({
            "contact_id": CONTACT_NUM,
            "tenant_id":  TENANT_ID,
            "content":    {"text": long_text},
        })
        assert len(mock_provider.sent_messages) >= 2
        # All segments sent to same number
        for msg in mock_provider.sent_messages:
            assert msg["to"] == CONTACT_NUM

    @pytest.mark.asyncio
    async def test_missing_contact_id_noop(self, adapter, mock_provider):
        await adapter.deliver_text({
            "content": {"text": "Mensagem sem destinatário"},
        })
        assert mock_provider.sent_messages == []

    @pytest.mark.asyncio
    async def test_missing_text_noop(self, adapter, mock_provider):
        await adapter.deliver_text({
            "contact_id": CONTACT_NUM,
            "content": {},
        })
        assert mock_provider.sent_messages == []


# ══════════════════════════════════════════════════════════════════════════════
# SMSAdapter — deliver_menu
# ══════════════════════════════════════════════════════════════════════════════

class TestDeliverMenu:
    @pytest.mark.asyncio
    async def test_menu_with_title_and_options_sends_text(
        self, adapter, mock_provider, mock_redis
    ):
        await adapter.deliver_menu({
            "contact_id": CONTACT_NUM,
            "tenant_id":  TENANT_ID,
            "session_id": "sid-001",
            "content": {
                "title": "Como posso ajudar?",
                "menu_id": "menu_abc",
                "fields": [
                    {
                        "id": "topic",
                        "label": "Escolha o assunto",
                        "options": [
                            {"label": "Suporte técnico", "value": "suporte"},
                            {"label": "Faturamento",     "value": "faturamento"},
                        ],
                    }
                ],
            },
        })
        # Title should be sent as first SMS
        assert len(mock_provider.sent_messages) >= 1
        bodies = [m["body"] for m in mock_provider.sent_messages]
        assert any("Como posso ajudar?" in b for b in bodies)
        # Numbered options should appear
        assert any("1." in b or "Suporte técnico" in b for b in bodies)

    @pytest.mark.asyncio
    async def test_menu_starts_sequential_collect(
        self, adapter, mock_provider, mock_redis
    ):
        await adapter.deliver_menu({
            "contact_id": CONTACT_NUM,
            "tenant_id":  TENANT_ID,
            "session_id": "sid-002",
            "content": {
                "menu_id": "m2",
                "fields": [
                    {"id": "f1", "label": "Pergunta 1"},
                    {"id": "f2", "label": "Pergunta 2"},
                ],
            },
        })
        # Collect state stored in Redis
        mock_redis.setex.assert_called()
        stored_calls = [
            call for call in mock_redis.setex.call_args_list
            if "menu_collect" in str(call)
        ]
        assert len(stored_calls) >= 1

    @pytest.mark.asyncio
    async def test_missing_contact_id_noop(self, adapter, mock_provider):
        await adapter.deliver_menu({"content": {}})
        assert mock_provider.sent_messages == []


# ══════════════════════════════════════════════════════════════════════════════
# SMSAdapter — deliver_typing
# ══════════════════════════════════════════════════════════════════════════════

class TestDeliverTyping:
    @pytest.mark.asyncio
    async def test_typing_is_noop(self, adapter, mock_provider):
        await adapter.deliver_typing({"contact_id": CONTACT_NUM})
        assert mock_provider.sent_messages == []


# ══════════════════════════════════════════════════════════════════════════════
# SMSAdapter — deliver_session_closed
# ══════════════════════════════════════════════════════════════════════════════

class TestDeliverSessionClosed:
    @pytest.mark.asyncio
    async def test_session_key_deleted(self, adapter, mock_redis):
        await adapter.deliver_session_closed({
            "contact_id": CONTACT_NUM,
            "session_id": "sid-close",
        })
        mock_redis.delete.assert_called_once_with(
            f"channel:sms:{CONTACT_NUM}:session"
        )

    @pytest.mark.asyncio
    async def test_no_sms_sent_to_customer(self, adapter, mock_provider, mock_redis):
        await adapter.deliver_session_closed({
            "contact_id": CONTACT_NUM,
            "session_id": "sid-close",
        })
        assert mock_provider.sent_messages == []

    @pytest.mark.asyncio
    async def test_missing_contact_id_safe(self, adapter, mock_redis):
        # Should not raise
        await adapter.deliver_session_closed({"session_id": "sid-x"})
        mock_redis.delete.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# SMSAdapter — sequential collect
# ══════════════════════════════════════════════════════════════════════════════

class TestSequentialCollect:
    def _collect_state(self, fields: list, index: int = 0) -> str:
        return json.dumps({
            "menu_id":       "menu_seq",
            "fields":        fields,
            "current_index": index,
            "answers":       {},
        })

    @pytest.mark.asyncio
    async def test_text_field_advances_to_next(
        self, adapter, mock_redis, mock_provider
    ):
        fields = [
            {"id": "name",  "label": "Qual seu nome?"},
            {"id": "email", "label": "Qual seu e-mail?"},
        ]
        state_json = self._collect_state(fields, 0)

        await adapter._advance_sequential_collect(
            session_id    = "sid-seq",
            tenant_id     = TENANT_ID,
            contact_id    = CONTACT_NUM,
            collect_key   = f"channel:sms:sid-seq:menu_collect",
            collect_state = json.loads(state_json),
            value         = "João Silva",
        )
        # Should send next field prompt
        assert any("e-mail" in m["body"].lower() for m in mock_provider.sent_messages)

    @pytest.mark.asyncio
    async def test_option_selection_by_number(
        self, adapter, mock_redis, mock_producer, mock_provider
    ):
        fields = [
            {
                "id": "topic",
                "label": "Assunto:",
                "options": [
                    {"label": "Suporte", "value": "suporte"},
                    {"label": "Vendas",  "value": "vendas"},
                ],
            }
        ]
        state = json.loads(self._collect_state(fields, 0))

        await adapter._advance_sequential_collect(
            session_id    = "sid-opt",
            tenant_id     = TENANT_ID,
            contact_id    = CONTACT_NUM,
            collect_key   = "channel:sms:sid-opt:menu_collect",
            collect_state = state,
            value         = "1",  # user picks option 1 = "suporte"
        )
        # Single-field collect → publishes menu_result
        published = json.loads(mock_producer.send.call_args[0][1].decode())
        assert published["content"]["type"] == "menu_result"
        assert published["content"]["payload"]["answers"]["topic"] == "suporte"

    @pytest.mark.asyncio
    async def test_invalid_option_reprompts(
        self, adapter, mock_redis, mock_provider
    ):
        fields = [
            {
                "id": "choice",
                "label": "Escolha:",
                "options": [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}],
            }
        ]
        state = json.loads(self._collect_state(fields, 0))

        await adapter._advance_sequential_collect(
            session_id    = "sid-inv",
            tenant_id     = TENANT_ID,
            contact_id    = CONTACT_NUM,
            collect_key   = "channel:sms:sid-inv:menu_collect",
            collect_state = state,
            value         = "99",  # invalid
        )
        # Error message sent
        assert any(
            "inválida" in m["body"].lower() or "invalid" in m["body"].lower()
            for m in mock_provider.sent_messages
        )
        # State restored (setex called to preserve state)
        mock_redis.setex.assert_called()

    @pytest.mark.asyncio
    async def test_last_field_publishes_menu_result(
        self, adapter, mock_redis, mock_producer, mock_provider
    ):
        fields = [{"id": "feedback", "label": "Feedback:"}]
        # Only one field, current_index=0
        state = {"menu_id": "m_fb", "fields": fields, "current_index": 0, "answers": {}}

        await adapter._advance_sequential_collect(
            session_id    = "sid-done",
            tenant_id     = TENANT_ID,
            contact_id    = CONTACT_NUM,
            collect_key   = "channel:sms:sid-done:menu_collect",
            collect_state = state,
            value         = "Muito bom!",
        )
        # Collect key deleted
        mock_redis.delete.assert_called_once_with("channel:sms:sid-done:menu_collect")
        # menu_result published
        call_args = mock_producer.send.call_args[0]
        published = json.loads(call_args[1].decode())
        assert published["content"]["type"] == "menu_result"
        assert published["content"]["payload"]["answers"]["feedback"] == "Muito bom!"

    @pytest.mark.asyncio
    async def test_masked_field_prompt_adds_privacy_note(
        self, adapter, mock_redis, mock_provider
    ):
        await adapter._send_field_prompt(
            contact_id = CONTACT_NUM,
            tenant_id  = TENANT_ID,
            field      = {"id": "cpf", "label": "CPF:", "masked": True},
            index      = 0,
        )
        assert any(
            "confidencial" in m["body"].lower()
            for m in mock_provider.sent_messages
        )


# ══════════════════════════════════════════════════════════════════════════════
# SMSAdapter — full inbound text flow (integration)
# ══════════════════════════════════════════════════════════════════════════════

class TestInboundTextIntegration:
    @pytest.mark.asyncio
    async def test_new_contact_text_publishes_inbound_event(
        self, adapter, mock_redis, mock_producer
    ):
        mock_redis.get.return_value = None  # no session, no collect

        await adapter._handle_inbound(_sms_params(body="Preciso de ajuda"))

        # At least two publishes: contact_open + inbound event
        assert mock_producer.send.call_count >= 2

        payloads = [
            json.loads(c.args[1].decode())
            for c in mock_producer.send.call_args_list
        ]
        content_types = [p.get("content_type") for p in payloads]
        assert "text" in content_types

    @pytest.mark.asyncio
    async def test_existing_session_publishes_inbound_only(
        self, adapter, mock_redis, mock_producer
    ):
        mock_redis.get.return_value = "existing-sid"  # session exists, no collect

        await adapter._handle_inbound(_sms_params(body="Outra mensagem"))

        # Only inbound event (no contact_open)
        payloads = [
            json.loads(c.args[1].decode())
            for c in mock_producer.send.call_args_list
        ]
        assert len(payloads) == 1
        assert payloads[0]["content"]["text"] == "Outra mensagem"

    @pytest.mark.asyncio
    async def test_missing_from_field_skipped(self, adapter, mock_producer):
        params = _sms_params()
        params["From"] = ""
        await adapter._handle_inbound(params)
        mock_producer.send.assert_not_called()
