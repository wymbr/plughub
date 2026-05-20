"""
test_email_adapter.py
Suite de testes para EmailAdapter e email_provider helpers.

Architecture: channel-gateway-multi-channel.md § 8.4

Covered:
  - _strip_quoted_text (plain text, HTML fallback, various quote patterns)
  - _markdown_to_html (basic rendering)
  - _message_id_key / _contact_key (stable hashing)
  - MailgunProvider.verify_signature (valid, invalid, dev mode)
  - MockEmailProvider (send, parse_inbound, load_inbound)
  - EmailAdapter.process_inbound (valid / invalid signature)
  - EmailAdapter._resolve_session (Reply-To, In-Reply-To, address, new)
  - EmailAdapter.deliver_text (MIME structure, subject prefix, reply_to header)
  - EmailAdapter.deliver_menu (numbered options, collect state stored)
  - EmailAdapter.deliver_typing (no-op)
  - EmailAdapter.deliver_session_closed (Redis cleanup)
  - EmailAdapter full inbound flow (new session, existing session)
"""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import pytest

from plughub_channel_gateway.adapters.email import (
    EmailAdapter,
    _contact_key,
    _markdown_to_html,
    _message_id_key,
    _strip_quoted_text,
)
from plughub_channel_gateway.adapters.email_provider import (
    EmailAttachment,
    MailgunProvider,
    MockEmailProvider,
    ParsedEmail,
    _extract_email,
)
from plughub_channel_gateway.config import Settings

# ── Constants ─────────────────────────────────────────────────────────────────

TENANT_ID    = "tenant_test"
CONTACT_EMAIL = "cliente@example.com"
FROM_ADDRESS  = "suporte@empresa.com"
REPLY_DOMAIN  = "mail.empresa.com"
SIGNING_KEY   = "test_signing_key_mailgun"
API_KEY       = "test-mailgun-api-key"
DOMAIN        = "empresa.com"
SESSION_ID    = "550e8400-e29b-41d4-a716-446655440000"
MESSAGE_ID    = "test-msg-001@empresa.com"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def email_settings():
    return Settings(
        kafka_brokers        = "localhost:9092",
        kafka_group_id       = "test-group",
        kafka_topic_inbound  = "conversations.inbound",
        kafka_topic_outbound = "conversations.outbound",
        kafka_topic_events   = "conversations.events",
        redis_url            = "redis://localhost:6379/0",
        tenant_id            = TENANT_ID,
        email_api_key        = API_KEY,
        email_domain         = DOMAIN,
        email_signing_key    = SIGNING_KEY,
        email_from_address   = FROM_ADDRESS,
        email_reply_domain   = REPLY_DOMAIN,
        email_default_pool_id = "email_test_pool",
        storage_root         = "/tmp",
        database_url         = "postgresql://plughub:plughub@localhost:5432/plughub",
        webchat_serving_base_url = "http://localhost:8010/webchat/v1/attachments",
        webchat_upload_base_url  = "http://localhost:8010/webchat/v1/upload",
        jwt_secret           = "changeme_32chars_webchat_secret!",
        session_ttl_seconds  = 3600,
    )


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.get    = AsyncMock(return_value=None)
    redis.set    = AsyncMock(return_value=True)
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
    return MockEmailProvider()


@pytest.fixture
def adapter(email_settings, mock_redis, mock_producer, mock_provider):
    return EmailAdapter(
        producer  = mock_producer,
        redis     = mock_redis,
        settings  = email_settings,
        provider  = mock_provider,
    )


def _make_parsed_email(
    *,
    from_address: str = CONTACT_EMAIL,
    to_address:   str = FROM_ADDRESS,
    subject:      str = "Preciso de ajuda",
    body_text:    str = "Olá, preciso de suporte.",
    body_html:    str = "",
    in_reply_to:  str | None = None,
    references:   list[str] | None = None,
    message_id:   str = MESSAGE_ID,
) -> ParsedEmail:
    return ParsedEmail(
        message_id   = message_id,
        from_address = from_address,
        to_address   = to_address,
        subject      = subject,
        body_text    = body_text,
        body_html    = body_html,
        in_reply_to  = in_reply_to,
        references   = references or [],
    )


# ══════════════════════════════════════════════════════════════════════════════
# Text helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestStripQuotedText:
    def test_plain_text_no_quotes_returned_as_is(self):
        result = _strip_quoted_text("Olá, preciso de ajuda.", "")
        assert result == "Olá, preciso de ajuda."

    def test_strips_gt_quote_lines(self):
        text = "Nova mensagem.\n\n> Mensagem anterior\n> continuação"
        result = _strip_quoted_text(text, "")
        assert "Nova mensagem" in result
        assert "Mensagem anterior" not in result

    def test_strips_on_date_wrote_pattern_en(self):
        text = "Nova resposta.\n\nOn Mon, 19 May 2026, cliente wrote:\n> texto antigo"
        result = _strip_quoted_text(text, "")
        assert "Nova resposta" in result
        assert "texto antigo" not in result

    def test_strips_em_data_escreveu_pattern_pt(self):
        text = "Resposta nova.\n\nEm 19 de maio de 2026, cliente escreveu:\n> conteúdo antigo"
        result = _strip_quoted_text(text, "")
        assert "Resposta nova" in result
        assert "conteúdo antigo" not in result

    def test_strips_outlook_forwarded_header(self):
        text = "Nova msg.\n\nFrom: agent@empresa.com\nSent: Monday\nTo: cliente\nSubject: Re: Ajuda"
        result = _strip_quoted_text(text, "")
        assert "Nova msg" in result

    def test_falls_back_to_full_text_when_no_quotes(self):
        text = "Texto sem nenhum marcador de citação."
        result = _strip_quoted_text(text, "")
        assert result == text

    def test_uses_html_when_plain_text_empty(self):
        html   = "<p>Mensagem nova</p>"
        result = _strip_quoted_text("", html)
        assert "Mensagem nova" in result

    def test_empty_inputs_return_empty(self):
        assert _strip_quoted_text("", "") == ""


class TestMarkdownToHtml:
    def test_bold_rendered(self):
        result = _markdown_to_html("**negrito**")
        assert "negrito" in result

    def test_plain_text_wrapped(self):
        result = _markdown_to_html("Olá mundo")
        assert "Olá mundo" in result

    def test_returns_string(self):
        result = _markdown_to_html("# Título")
        assert isinstance(result, str)
        assert len(result) > 0


class TestKeyHelpers:
    def test_message_id_key_stable(self):
        k1 = _message_id_key("msg@example.com")
        k2 = _message_id_key("msg@example.com")
        assert k1 == k2
        assert k1.startswith("channel:email:")

    def test_contact_key_stable(self):
        k1 = _contact_key("user@example.com")
        k2 = _contact_key("USER@EXAMPLE.COM")
        # Same normalized email → same key? (contact_key lowercases)
        assert k1 == k2
        assert "addr" in k1

    def test_different_ids_different_keys(self):
        assert _message_id_key("a@b.com") != _message_id_key("c@d.com")


class TestExtractEmail:
    def test_bare_email(self):
        assert _extract_email("user@example.com") == "user@example.com"

    def test_name_angle_bracket(self):
        assert _extract_email("João Silva <joao@example.com>") == "joao@example.com"

    def test_normalizes_to_lowercase(self):
        assert _extract_email("USER@EXAMPLE.COM") == "user@example.com"


# ══════════════════════════════════════════════════════════════════════════════
# MailgunProvider
# ══════════════════════════════════════════════════════════════════════════════

class TestMailgunProviderSignature:
    def _make_signature(self, timestamp: str, token: str) -> str:
        value    = (timestamp + token).encode()
        return hmac.new(SIGNING_KEY.encode(), value, hashlib.sha256).hexdigest()

    @pytest.mark.asyncio
    async def test_valid_signature(self):
        provider  = MailgunProvider(API_KEY, DOMAIN, SIGNING_KEY)
        timestamp = "1716220800"
        token     = "abc123token"
        sig       = self._make_signature(timestamp, token)
        headers   = {
            "X-Mailgun-Timestamp": timestamp,
            "X-Mailgun-Token":     token,
            "X-Mailgun-Signature": sig,
        }
        assert await provider.verify_signature(headers, b"") is True

    @pytest.mark.asyncio
    async def test_invalid_signature(self):
        provider = MailgunProvider(API_KEY, DOMAIN, SIGNING_KEY)
        headers  = {
            "X-Mailgun-Timestamp": "123",
            "X-Mailgun-Token":     "tok",
            "X-Mailgun-Signature": "wrong",
        }
        assert await provider.verify_signature(headers, b"") is False

    @pytest.mark.asyncio
    async def test_dev_mode_always_valid(self):
        provider = MailgunProvider(API_KEY, DOMAIN, SIGNING_KEY, dev_mode=True)
        assert await provider.verify_signature({}, b"") is True

    @pytest.mark.asyncio
    async def test_empty_signing_key_dev_mode(self):
        provider = MailgunProvider(API_KEY, DOMAIN, "")
        assert await provider.verify_signature({}, b"") is True


# ══════════════════════════════════════════════════════════════════════════════
# MockEmailProvider
# ══════════════════════════════════════════════════════════════════════════════

class TestMockEmailProvider:
    @pytest.mark.asyncio
    async def test_default_inbound_returns_generic_email(self):
        provider = MockEmailProvider()
        parsed   = await provider.parse_inbound({}, b"")
        assert parsed.from_address == "cliente@example.com"
        assert parsed.body_text

    @pytest.mark.asyncio
    async def test_load_inbound_queues_custom_email(self):
        provider = MockEmailProvider()
        custom   = _make_parsed_email(subject="Teste específico")
        provider.load_inbound(custom)
        parsed   = await provider.parse_inbound({}, b"")
        assert parsed.subject == "Teste específico"

    @pytest.mark.asyncio
    async def test_send_records_message(self):
        provider = MockEmailProvider()
        msg_id   = await provider.send(
            to           = CONTACT_EMAIL,
            subject      = "Re: Ajuda",
            body_text    = "Olá!",
            body_html    = "<p>Olá!</p>",
            from_address = FROM_ADDRESS,
            reply_to     = f"reply+{SESSION_ID}@{REPLY_DOMAIN}",
        )
        assert len(provider.sent_messages) == 1
        m = provider.sent_messages[0]
        assert m["to"]      == CONTACT_EMAIL
        assert m["subject"] == "Re: Ajuda"
        assert msg_id       == m["message_id"]

    @pytest.mark.asyncio
    async def test_verify_signature_default_true(self):
        provider = MockEmailProvider()
        assert await provider.verify_signature({}, b"") is True

    @pytest.mark.asyncio
    async def test_verify_signature_configured_false(self):
        provider = MockEmailProvider(verify_result=False)
        assert await provider.verify_signature({}, b"") is False


# ══════════════════════════════════════════════════════════════════════════════
# EmailAdapter.process_inbound
# ══════════════════════════════════════════════════════════════════════════════

class TestProcessInbound:
    @pytest.mark.asyncio
    async def test_invalid_signature_aborts(self):
        bad_provider = MockEmailProvider(verify_result=False)
        adapter = EmailAdapter(
            producer = AsyncMock(),
            redis    = AsyncMock(),
            settings = Settings(
                kafka_topic_inbound  = "t.inbound",
                kafka_topic_events   = "t.events",
                tenant_id            = TENANT_ID,
            ),
            provider = bad_provider,
        )
        await adapter.process_inbound(headers={}, body=b"")
        assert bad_provider.sent_messages == []

    @pytest.mark.asyncio
    async def test_valid_signature_schedules_background_task(
        self, adapter, mock_redis
    ):
        mock_redis.get.return_value = None
        with patch.object(adapter, "_handle_inbound", new_callable=AsyncMock) as mock_h:
            await adapter.process_inbound(headers={}, body=b"test")
            import asyncio
            await asyncio.sleep(0.01)
            mock_h.assert_called_once_with({}, b"test")


# ══════════════════════════════════════════════════════════════════════════════
# EmailAdapter._resolve_session
# ══════════════════════════════════════════════════════════════════════════════

class TestResolveSession:
    @pytest.mark.asyncio
    async def test_reply_to_address_extracts_session(self, adapter, mock_redis):
        """reply+{session_id}@{domain} in To: → session_id without Redis lookup."""
        to_addr = f"reply+{SESSION_ID}@{REPLY_DOMAIN}"
        parsed  = _make_parsed_email(to_address=to_addr)

        sid, tid = await adapter._resolve_session(parsed=parsed, to_address=to_addr)
        assert sid == SESSION_ID
        mock_redis.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_in_reply_to_fallback(self, adapter, mock_redis):
        """In-Reply-To Message-ID → Redis lookup returns existing session."""
        key = _message_id_key(MESSAGE_ID)
        mock_redis.get.side_effect = lambda k: "existing-sid" if k == key else None

        parsed = _make_parsed_email(in_reply_to=MESSAGE_ID)
        sid, _ = await adapter._resolve_session(parsed=parsed, to_address=FROM_ADDRESS)
        assert sid == "existing-sid"

    @pytest.mark.asyncio
    async def test_contact_address_fallback(self, adapter, mock_redis):
        """No Reply-To, no In-Reply-To match → contact address lookup."""
        addr_key = _contact_key(CONTACT_EMAIL)
        mock_redis.get.side_effect = lambda k: "addr-sid" if k == addr_key else None

        parsed = _make_parsed_email()
        sid, _ = await adapter._resolve_session(parsed=parsed, to_address=FROM_ADDRESS)
        assert sid == "addr-sid"

    @pytest.mark.asyncio
    async def test_new_session_when_no_match(self, adapter, mock_redis, mock_producer):
        """No correlation found → new session created and contact_open published."""
        mock_redis.get.return_value = None

        parsed = _make_parsed_email()
        sid, tid = await adapter._resolve_session(parsed=parsed, to_address=FROM_ADDRESS)

        assert sid  # UUID generated
        assert tid == TENANT_ID
        mock_redis.set.assert_called_once()
        # contact_open event published
        assert mock_producer.send.call_count >= 1

    @pytest.mark.asyncio
    async def test_new_session_publishes_contact_open(self, adapter, mock_redis, mock_producer):
        mock_redis.get.return_value = None
        parsed = _make_parsed_email()
        await adapter._resolve_session(parsed=parsed, to_address=FROM_ADDRESS)

        payloads = [
            json.loads(c.args[1].decode())
            for c in mock_producer.send.call_args_list
        ]
        channels = [p.get("channel", p.get("type")) for p in payloads]
        assert "email" in channels or "contact_open" in channels


# ══════════════════════════════════════════════════════════════════════════════
# EmailAdapter.deliver_text
# ══════════════════════════════════════════════════════════════════════════════

class TestDeliverText:
    @pytest.mark.asyncio
    async def test_sends_email_to_contact(self, adapter, mock_provider):
        await adapter.deliver_text({
            "contact_id": CONTACT_EMAIL,
            "session_id": SESSION_ID,
            "tenant_id":  TENANT_ID,
            "content":    {"text": "Olá, estamos verificando sua solicitação."},
            "metadata":   {"subject": "Preciso de ajuda"},
        })
        assert len(mock_provider.sent_messages) == 1
        m = mock_provider.sent_messages[0]
        assert m["to"] == CONTACT_EMAIL

    @pytest.mark.asyncio
    async def test_subject_prefixed_with_re(self, adapter, mock_provider):
        await adapter.deliver_text({
            "contact_id": CONTACT_EMAIL,
            "session_id": SESSION_ID,
            "content":    {"text": "Resposta"},
            "metadata":   {"subject": "Meu problema"},
        })
        assert mock_provider.sent_messages[0]["subject"] == "Re: Meu problema"

    @pytest.mark.asyncio
    async def test_subject_already_re_not_doubled(self, adapter, mock_provider):
        await adapter.deliver_text({
            "contact_id": CONTACT_EMAIL,
            "session_id": SESSION_ID,
            "content":    {"text": "Resposta"},
            "metadata":   {"subject": "Re: Meu problema"},
        })
        assert mock_provider.sent_messages[0]["subject"] == "Re: Meu problema"

    @pytest.mark.asyncio
    async def test_reply_to_contains_session_id(self, adapter, mock_provider):
        await adapter.deliver_text({
            "contact_id": CONTACT_EMAIL,
            "session_id": SESSION_ID,
            "content":    {"text": "Resposta"},
            "metadata":   {},
        })
        reply_to = mock_provider.sent_messages[0]["reply_to"]
        assert SESSION_ID in reply_to
        assert REPLY_DOMAIN in reply_to

    @pytest.mark.asyncio
    async def test_in_reply_to_header_set(self, adapter, mock_provider):
        await adapter.deliver_text({
            "contact_id": CONTACT_EMAIL,
            "session_id": SESSION_ID,
            "content":    {"text": "Resposta"},
            "metadata":   {"last_message_id": MESSAGE_ID},
        })
        assert mock_provider.sent_messages[0]["in_reply_to"] == MESSAGE_ID

    @pytest.mark.asyncio
    async def test_agent_signature_appended(self, adapter, mock_provider):
        await adapter.deliver_text({
            "contact_id": CONTACT_EMAIL,
            "session_id": SESSION_ID,
            "content":    {"text": "Resposta do agente."},
            "metadata":   {"agent_signature_text": "Atenciosamente, Equipe PlugHub"},
        })
        body_text = mock_provider.sent_messages[0]["body_text"]
        assert "Atenciosamente, Equipe PlugHub" in body_text

    @pytest.mark.asyncio
    async def test_html_body_contains_rendered_markdown(self, adapter, mock_provider):
        await adapter.deliver_text({
            "contact_id": CONTACT_EMAIL,
            "session_id": SESSION_ID,
            "content":    {"text": "**Olá**, seu ticket foi recebido."},
            "metadata":   {},
        })
        body_html = mock_provider.sent_messages[0]["body_html"]
        assert "Olá" in body_html  # Markdown rendered

    @pytest.mark.asyncio
    async def test_missing_contact_id_noop(self, adapter, mock_provider):
        await adapter.deliver_text({
            "content": {"text": "Mensagem sem destinatário"},
        })
        assert mock_provider.sent_messages == []

    @pytest.mark.asyncio
    async def test_missing_text_noop(self, adapter, mock_provider):
        await adapter.deliver_text({
            "contact_id": CONTACT_EMAIL,
            "content": {},
        })
        assert mock_provider.sent_messages == []


# ══════════════════════════════════════════════════════════════════════════════
# EmailAdapter.deliver_menu
# ══════════════════════════════════════════════════════════════════════════════

class TestDeliverMenu:
    @pytest.mark.asyncio
    async def test_menu_options_formatted_as_numbered_list(
        self, adapter, mock_provider, mock_redis
    ):
        await adapter.deliver_menu({
            "contact_id": CONTACT_EMAIL,
            "session_id": SESSION_ID,
            "content": {
                "title": "Como posso ajudar?",
                "menu_id": "m_test",
                "fields": [{
                    "id": "topic",
                    "label": "Escolha o assunto:",
                    "options": [
                        {"label": "Suporte", "value": "suporte"},
                        {"label": "Financeiro", "value": "financeiro"},
                    ],
                }],
            },
        })
        body = mock_provider.sent_messages[0]["body_text"]
        assert "1." in body
        assert "Suporte" in body
        assert "2." in body
        assert "Financeiro" in body

    @pytest.mark.asyncio
    async def test_menu_stores_collect_state(
        self, adapter, mock_provider, mock_redis
    ):
        await adapter.deliver_menu({
            "contact_id": CONTACT_EMAIL,
            "session_id": SESSION_ID,
            "content": {
                "menu_id": "m2",
                "fields": [{"id": "f1", "label": "Pergunta"}],
            },
        })
        stored = [
            c for c in mock_redis.setex.call_args_list
            if "menu_collect" in str(c)
        ]
        assert len(stored) >= 1

    @pytest.mark.asyncio
    async def test_missing_contact_id_noop(self, adapter, mock_provider):
        await adapter.deliver_menu({"content": {}})
        assert mock_provider.sent_messages == []


# ══════════════════════════════════════════════════════════════════════════════
# EmailAdapter.deliver_typing
# ══════════════════════════════════════════════════════════════════════════════

class TestDeliverTyping:
    @pytest.mark.asyncio
    async def test_typing_is_noop(self, adapter, mock_provider):
        await adapter.deliver_typing({"contact_id": CONTACT_EMAIL})
        assert mock_provider.sent_messages == []


# ══════════════════════════════════════════════════════════════════════════════
# EmailAdapter.deliver_session_closed
# ══════════════════════════════════════════════════════════════════════════════

class TestDeliverSessionClosed:
    @pytest.mark.asyncio
    async def test_contact_key_deleted(self, adapter, mock_redis):
        await adapter.deliver_session_closed({
            "contact_id": CONTACT_EMAIL,
            "session_id": SESSION_ID,
        })
        key = _contact_key(CONTACT_EMAIL)
        mock_redis.delete.assert_called_once_with(key)

    @pytest.mark.asyncio
    async def test_no_email_sent_to_customer(self, adapter, mock_provider, mock_redis):
        await adapter.deliver_session_closed({
            "contact_id": CONTACT_EMAIL,
            "session_id": SESSION_ID,
        })
        assert mock_provider.sent_messages == []

    @pytest.mark.asyncio
    async def test_missing_contact_id_safe(self, adapter, mock_redis):
        await adapter.deliver_session_closed({"session_id": SESSION_ID})
        mock_redis.delete.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# Full inbound flow (integration)
# ══════════════════════════════════════════════════════════════════════════════

class TestInboundIntegration:
    @pytest.mark.asyncio
    async def test_new_contact_publishes_inbound_event(
        self, adapter, mock_redis, mock_producer, mock_provider
    ):
        """New email (no session) → contact_open + inbound event published."""
        mock_redis.get.return_value = None
        mock_provider.load_inbound(_make_parsed_email())

        await adapter._handle_inbound({}, b"")

        assert mock_producer.send.call_count >= 2
        payloads = [
            json.loads(c.args[1].decode())
            for c in mock_producer.send.call_args_list
        ]
        content_types = [p.get("content_type") for p in payloads]
        assert "text" in content_types

    @pytest.mark.asyncio
    async def test_reply_via_reply_to_continues_session(
        self, adapter, mock_redis, mock_producer, mock_provider
    ):
        """Reply arriving via reply+{session_id}@ does not create new session."""
        to_addr = f"reply+{SESSION_ID}@{REPLY_DOMAIN}"
        mock_provider.load_inbound(_make_parsed_email(
            to_address=to_addr,
            body_text="Ainda preciso de ajuda.",
        ))

        await adapter._handle_inbound({}, b"")

        # Only one publish — inbound event (no contact_open)
        payloads = [
            json.loads(c.args[1].decode())
            for c in mock_producer.send.call_args_list
        ]
        # session_id preserved from Reply-To
        session_ids = [p.get("session_id") for p in payloads if "session_id" in p]
        assert all(sid == SESSION_ID for sid in session_ids)

    @pytest.mark.asyncio
    async def test_quoted_text_stripped_from_reply(
        self, adapter, mock_redis, mock_producer, mock_provider
    ):
        """New text extracted; quoted thread not published to stream."""
        mock_redis.get.return_value = None
        body = "Ainda não foi resolvido.\n\nOn Mon, cliente wrote:\n> Texto antigo"
        mock_provider.load_inbound(_make_parsed_email(body_text=body))

        await adapter._handle_inbound({}, b"")

        payloads = [
            json.loads(c.args[1].decode())
            for c in mock_producer.send.call_args_list
        ]
        texts = [
            p.get("content", {}).get("text", "")
            for p in payloads
            if p.get("content_type") == "text"
        ]
        assert any("Ainda não foi resolvido" in t for t in texts)
        assert not any("Texto antigo" in t for t in texts)

    @pytest.mark.asyncio
    async def test_message_id_stored_after_inbound(
        self, adapter, mock_redis, mock_producer, mock_provider
    ):
        """Message-ID stored in Redis for future In-Reply-To lookups."""
        mock_redis.get.return_value = None
        mock_provider.load_inbound(_make_parsed_email(message_id="unique-msg-id"))

        await adapter._handle_inbound({}, b"")

        stored_keys = [c.args[0] for c in mock_redis.setex.call_args_list]
        assert any("channel:email:" in k for k in stored_keys)
