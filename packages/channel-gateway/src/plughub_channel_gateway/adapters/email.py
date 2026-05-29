"""
adapters/email.py
Email channel adapter — inbound webhook + outbound delivery.

Architecture: channel-gateway-multi-channel.md § 8.4

Inbound flow (per webhook POST):
  1. EmailAdapter.process_inbound(headers, body)
     → verify_signature (Mailgun HMAC-SHA256)
     → asyncio.create_task(_handle_inbound)
  2. HTTP 200 returned immediately
  3. Background task:
     a. provider.parse_inbound → ParsedEmail
     b. _resolve_session — Reply-To → In-Reply-To → address fallback
     c. _strip_quoted_text — extract only new text
     d. _store_attachments → AttachmentStore
     e. Publish NormalizedInboundEvent

Outbound flow (per conversations.outbound Kafka message):
  deliver_text → MIME multipart (text/plain + text/html from Markdown)
               + agent signature + Reply-To header for thread continuity
  deliver_menu → numbered text list + sequential collect start
  deliver_typing → no-op (email has no typing indicator)
  deliver_session_closed → Redis cleanup, no email to customer

Session model:
  contact_id = from_address normalized (e.g. "cliente@gmail.com")
  Primary: extract session_id from Reply-To address (reply+{id}@{domain})
  Fallback 1: In-Reply-To Message-ID → Redis lookup
  Fallback 2: contact email hash → Redis lookup
  New session: none of the above found

Mailbox config:
  Each mailbox is a ChannelEndpoint in agent-registry.
  Config (signing_key, api_key, from_address, reply_domain, templates)
  fetched from ChannelEndpoint metadata — not env vars.
  Multiple mailboxes supported simultaneously via Layer 2 resolve.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import textwrap
import uuid
from datetime import datetime, timedelta, timezone

import redis.asyncio as aioredis
from aiokafka import AIOKafkaProducer

from ..attachment_store import AttachmentStore
from ..config import Settings
from ..models import (
    ContactClosedEvent,
    ContactOpenEvent,
    ContextSnapshot,
    MessageAuthor,
    MessageContent,
    NormalizedInboundEvent,
)
from .base import ChannelAdapter
from .email_provider import (
    EmailAttachment,
    IEmailProvider,
    MailgunProvider,
    MockEmailProvider,
    ParsedEmail,
    _extract_email,
)

logger = logging.getLogger("plughub.channel-gateway.email")

# Redis TTL for Message-ID → session_id mapping (30 days)
_MESSAGE_ID_TTL = 86_400 * 30
# Redis TTL for sequential menu collect state (30 min)
_MENU_COLLECT_TTL = 1_800

# Patterns that signal the start of quoted/replied email content
_QUOTE_PATTERNS = [
    re.compile(r"^>", re.MULTILINE),
    re.compile(
        r"^-{3,}.*original message.*-{3,}",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(
        r"^On .+wrote:\s*$",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(
        r"^Em .+escreveu:\s*$",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(
        r"^From:\s*.+\nSent:\s*.+\nTo:\s*.+\nSubject:\s*",
        re.IGNORECASE | re.MULTILINE,
    ),
    re.compile(
        r"^De:\s*.+\nEnviado:\s*.+\nPara:\s*.+\nAssunto:\s*",
        re.IGNORECASE | re.MULTILINE,
    ),
]

# Regex to extract session_id from reply+{session_id}@{domain}
_REPLY_TO_RE = re.compile(r"reply\+([a-f0-9\-]{36})@", re.IGNORECASE)


class EmailAdapter(ChannelAdapter):
    """
    Channel-level singleton adapter for Email (Mailgun + IEmailProvider abstraction).

    Args:
        producer:         Kafka producer for publishing normalised events.
        redis:            Async Redis client.
        settings:         Gateway settings.
        attachment_store: Shared attachment store for inbound file attachments.
        provider:         IEmailProvider implementation. If None, MailgunProvider
                          is built from settings at runtime.
                          Pass a MockEmailProvider in tests.
    """

    channel = "email"

    def __init__(
        self,
        *,
        producer:         AIOKafkaProducer,
        redis:            aioredis.Redis,
        settings:         Settings,
        attachment_store: AttachmentStore | None = None,
        provider:         IEmailProvider | None = None,
    ) -> None:
        self._producer         = producer
        self._redis            = redis
        self._settings         = settings
        self._attachment_store = attachment_store
        self._provider         = provider

    # ── Inbound — called from FastAPI webhook route ───────────────────────────

    async def process_inbound(
        self,
        headers: dict[str, str],
        body:    bytes,
    ) -> None:
        """
        Entry point called by the webhook route.
        HTTP 200 must be returned by the route BEFORE awaiting this method.
        Signature verification happens synchronously; processing in background.
        """
        provider = await self._get_provider()
        if not await provider.verify_signature(headers, body):
            logger.warning("email inbound rejected — invalid signature")
            return

        asyncio.create_task(self._handle_inbound(headers, body))

    # ── Inbound processing (background) ──────────────────────────────────────

    async def _handle_inbound(
        self,
        headers: dict[str, str],
        body:    bytes,
    ) -> None:
        try:
            provider = await self._get_provider()
            parsed   = await provider.parse_inbound(headers, body)

            logger.info(
                "email inbound from=%s subject=%r message_id=%s",
                parsed.from_address, parsed.subject, parsed.message_id,
            )

            # Resolve or create session
            session_id, tenant_id = await self._resolve_session(
                parsed=parsed,
                to_address=parsed.to_address,
            )

            # Store Message-ID → session_id for future In-Reply-To lookups
            if parsed.message_id:
                key = _message_id_key(parsed.message_id)
                await self._redis.setex(key, _MESSAGE_ID_TTL, session_id)

            # Extract new text only (strip quoted thread)
            new_text     = _strip_quoted_text(parsed.body_text, parsed.body_html)
            original_text = parsed.body_text  # full text with quoted content

            # Store attachments
            attachment_refs = await self._store_attachments(
                session_id  = session_id,
                tenant_id   = tenant_id,
                attachments = parsed.attachments,
            )

            # ── Arc 16 Phase D: capability-based pending collect ─────────────
            pending_collect_key = f"channel:email:{parsed.from_address}:pending_collect"
            pending_raw = await self._redis.get(pending_collect_key)
            if pending_raw:
                pending = json.loads(pending_raw)
                await self._redis.delete(pending_collect_key)
                payload = NormalizedInboundEvent(
                    message_id       = str(uuid.uuid4()),
                    contact_id       = parsed.from_address,
                    session_id       = session_id,
                    channel          = "email",
                    content_type     = "text",
                    author           = MessageAuthor(type="customer"),
                    content          = MessageContent(type="text", text=new_text),
                    context_snapshot = ContextSnapshot(),
                ).model_dump()
                payload["collect_token"] = pending.get("collect_token")
                payload["response_text"] = new_text
                await self._publish_inbound(payload)
            else:
                # Publish normalised event
                event = NormalizedInboundEvent(
                    message_id       = str(uuid.uuid4()),
                    contact_id       = parsed.from_address,
                    session_id       = session_id,
                    channel          = "email",
                    content_type     = "text",
                    author           = MessageAuthor(type="customer"),
                    content          = MessageContent(
                        type    = "text",
                        text    = new_text,
                        payload = {
                            "subject":          parsed.subject,
                            "message_id":       parsed.message_id,
                            "in_reply_to":      parsed.in_reply_to,
                            "attachment_refs":  attachment_refs,
                            "original_text":    original_text,
                        },
                    ),
                    context_snapshot = ContextSnapshot(),
                )
                await self._publish_inbound(event.model_dump())

        except Exception as exc:
            logger.exception("email inbound processing failed: %s", exc)

    # ── Session resolution ────────────────────────────────────────────────────

    async def _resolve_session(
        self,
        parsed:     ParsedEmail,
        to_address: str,
    ) -> tuple[str, str]:
        """
        Resolve session_id via three-tier lookup.
        Returns (session_id, tenant_id).

        Tier 1: Extract session_id from Reply-To address
                reply+{session_id}@{domain} in the To: header
        Tier 2: In-Reply-To Message-ID → Redis lookup
        Tier 3: contact email hash → Redis lookup (active session)
        None found → new session
        """
        tenant_id  = self._settings.tenant_id
        contact_id = parsed.from_address

        # Tier 1: session_id embedded in To: address (Reply-To correlation)
        match = _REPLY_TO_RE.search(to_address)
        if match:
            session_id = match.group(1)
            logger.debug("email: session from Reply-To addr session=%s", session_id)
            return session_id, tenant_id

        # Tier 2: In-Reply-To header → Message-ID lookup
        if parsed.in_reply_to:
            key    = _message_id_key(parsed.in_reply_to)
            stored = await self._redis.get(key)
            if stored:
                logger.debug(
                    "email: session from In-Reply-To=%s session=%s",
                    parsed.in_reply_to, stored,
                )
                return stored, tenant_id

        # Tier 3: active session for this contact email
        addr_key = _contact_key(contact_id)
        stored   = await self._redis.get(addr_key)
        if stored:
            logger.debug(
                "email: session from contact address=%s session=%s",
                contact_id, stored,
            )
            return stored, tenant_id

        # New session
        session_id = str(uuid.uuid4())
        await self._redis.set(addr_key, session_id)  # no TTL — follows Core lifecycle
        logger.info("email: new session=%s contact=%s", session_id, contact_id)

        pool_id = self._settings.email_default_pool_id or "email_default"
        await self._publish_event(
            ContactOpenEvent(
                contact_id         = contact_id,
                session_id         = session_id,
                tenant_id          = tenant_id,
                channel            = "email",
                channel_session_id = parsed.message_id,
            ).model_dump()
        )
        await self._publish_inbound({
            "session_id":  session_id,
            "tenant_id":   tenant_id,
            "customer_id": contact_id,
            "channel":     "email",
            "pool_id":     pool_id,
            "type":        "contact_open",
            "metadata": {
                "subject":    parsed.subject,
                "message_id": parsed.message_id,
            },
        })
        return session_id, tenant_id

    # ── Attachment storage ────────────────────────────────────────────────────

    async def _store_attachments(
        self,
        session_id:  str,
        tenant_id:   str,
        attachments: list[EmailAttachment],
    ) -> list[dict]:
        """Store each attachment in AttachmentStore; return list of refs."""
        if not attachments or self._attachment_store is None:
            return []

        refs: list[dict] = []
        for att in attachments:
            try:
                expires_at = datetime.now(timezone.utc) + timedelta(
                    days=self._settings.attachment_expiry_days
                )
                file_id, serving_url = await self._attachment_store.reserve(
                    tenant_id  = tenant_id,
                    session_id = session_id,
                    file_name  = att.filename,
                    mime_type  = att.mime_type,
                    size_bytes = att.size_bytes,
                    expires_at = expires_at,
                )
                await self._attachment_store.commit(
                    file_id   = file_id,
                    data      = att.data,
                    mime_type = att.mime_type,
                )
                refs.append({
                    "file_id":     file_id,
                    "filename":    att.filename,
                    "mime_type":   att.mime_type,
                    "size_bytes":  att.size_bytes,
                    "serving_url": serving_url,
                })
            except Exception as exc:
                logger.error("email attachment store failed file=%s: %s", att.filename, exc)
        return refs

    # ── ChannelAdapter interface — outbound ───────────────────────────────────

    async def deliver_text(self, payload: dict) -> None:
        """
        Deliver a text message as an email reply.
        Renders Markdown → HTML (mistune); appends agent signature.
        Sets Reply-To and threading headers for thread continuity.
        """
        contact_id = payload.get("contact_id", "")
        session_id = payload.get("session_id", "")
        tenant_id  = payload.get("tenant_id", self._settings.tenant_id)
        text       = payload.get("content", {}).get("text", "")
        meta       = payload.get("metadata", {})

        if not contact_id or not text:
            logger.warning("email deliver_text: missing contact_id or text")
            return

        # Threading headers from previous message metadata
        subject      = meta.get("subject", "")
        in_reply_to  = meta.get("last_message_id")
        references   = meta.get("references", [])
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}" if subject else "Re: (sem assunto)"

        # Agent signature
        agent_signature_text = meta.get("agent_signature_text", "")
        agent_signature_html = meta.get("agent_signature_html", "")

        # Render Markdown → HTML
        body_html = _markdown_to_html(text)
        body_text = text

        # Append signature
        if agent_signature_text:
            body_text = body_text + "\n\n--\n" + agent_signature_text
        if agent_signature_html:
            body_html = body_html + "<br><br>--<br>" + agent_signature_html

        # Reply-To for thread continuity
        reply_domain = self._settings.email_reply_domain
        from_address = self._settings.email_from_address
        reply_to     = f"reply+{session_id}@{reply_domain}" if reply_domain else ""

        try:
            provider = await self._get_provider()
            msg_id   = await provider.send(
                to           = contact_id,
                subject      = subject,
                body_text    = body_text,
                body_html    = body_html,
                from_address = from_address,
                reply_to     = reply_to,
                in_reply_to  = in_reply_to,
                references   = references,
            )
            logger.info(
                "email delivered to=%s session=%s msg_id=%s",
                contact_id, session_id, msg_id,
            )
            # Store outbound Message-ID for future References chain
            if msg_id:
                key = _message_id_key(msg_id)
                await self._redis.setex(key, _MESSAGE_ID_TTL, session_id)
        except Exception as exc:
            logger.error("email deliver_text failed contact=%s: %s", contact_id, exc)

    async def deliver_menu(self, payload: dict) -> None:
        """
        Deliver a menu as numbered plain text list in an email.
        For single-field menus: sends numbered options in email body.
        For multi-field menus: sends each field as a separate paragraph.
        """
        contact_id = payload.get("contact_id", "")
        session_id = payload.get("session_id", "")
        tenant_id  = payload.get("tenant_id", self._settings.tenant_id)
        menu       = payload.get("content", {})
        meta       = payload.get("metadata", {})

        if not contact_id:
            logger.warning("email deliver_menu: missing contact_id")
            return

        title  = menu.get("title") or menu.get("question", "")
        fields = menu.get("fields", [])

        lines: list[str] = []
        if title:
            lines.append(title)
            lines.append("")

        for field in fields:
            label   = field.get("label", "")
            options = field.get("options", [])
            if label:
                lines.append(label)
            if options:
                for i, opt in enumerate(options, start=1):
                    lines.append(f"{i}. {opt.get('label', opt.get('value', ''))}")
                lines.append("")
                lines.append("Por favor, responda com o número da opção escolhida.")
            lines.append("")

        # Start sequential collect if session_id provided
        if session_id and fields:
            collect_key   = f"channel:email:{session_id}:menu_collect"
            collect_state = {
                "menu_id":       menu.get("menu_id", str(uuid.uuid4())),
                "fields":        fields,
                "current_index": 0,
                "answers":       {},
            }
            await self._redis.setex(
                collect_key, _MENU_COLLECT_TTL, json.dumps(collect_state)
            )

        body_text = "\n".join(lines)
        # Reuse deliver_text to handle MIME construction and threading
        await self.deliver_text({
            **payload,
            "content": {"text": body_text},
        })

    async def deliver_typing(self, payload: dict) -> None:
        """Email has no typing indicator — no-op."""
        logger.debug("email deliver_typing: no-op for email channel")

    async def deliver_session_closed(self, payload: dict) -> None:
        """
        Clean up Redis keys when the session is closed.
        No email is sent to the customer — session close is silent.
        """
        contact_id = payload.get("contact_id", "")
        session_id = payload.get("session_id", "")

        if contact_id:
            addr_key = _contact_key(contact_id)
            await self._redis.delete(addr_key)
            logger.info(
                "email: session closed contact=%s session=%s",
                contact_id, session_id,
            )

    # ── Collect event — outbound capability-based (Arc 16 Phase D) ───────────

    async def handle_collect_event(self, event: dict) -> None:
        """
        Send a collect prompt to the customer via email and store a pending_collect
        key so the inbound handler can correlate the customer's reply.

        Called by _collect_events_consumer() when collect.requested arrives with
        channel="email" (explicit or capability-selected).

        Redis key: channel:email:{contact_id}:pending_collect
          → {collect_token, journey_id, pool_id}  TTL = 30 min
        """
        contact_id    = event.get("target", "")
        collect_token = event.get("collect_token", "")
        journey_id    = event.get("journey_id")
        prompt        = event.get("prompt", "")
        tenant_id     = event.get("tenant_id", self._settings.tenant_id)
        pool_id       = event.get("pool_id", "")

        if not contact_id or not prompt:
            logger.warning(
                "email handle_collect_event: missing target or prompt "
                "(collect_token=%s)", collect_token,
            )
            return

        # Reuse deliver_text with a minimal payload
        await self.deliver_text({
            "contact_id": contact_id,
            "session_id": "",   # no session yet for capability-based collect
            "tenant_id":  tenant_id,
            "content":    {"text": prompt},
            "metadata":   {"subject": "Mensagem do atendimento"},
        })

        pending = {
            "collect_token": collect_token,
            "journey_id":    journey_id,
            "pool_id":       pool_id,
        }
        await self._redis.setex(
            f"channel:email:{contact_id}:pending_collect",
            1_800,  # 30 min
            json.dumps(pending),
        )

        logger.info(
            "email collect sent: contact=%s collect_token=%s journey=%s",
            contact_id, collect_token, journey_id,
        )

    # ── MCP tool support ──────────────────────────────────────────────────────

    async def send_template(
        self,
        session_id:  str,
        template_id: str,
        variables:   dict[str, str],
        contact_id:  str,
        tenant_id:   str,
        meta:        dict,
    ) -> None:
        """
        Called by email_send_template MCP tool.
        Fetches template from mailbox config, substitutes variables, sends.
        """
        # Templates are stored in ChannelEndpoint metadata (fetched via Redis cache)
        template_key = f"{tenant_id}:config:email:templates:{template_id}"
        template     = await self._redis.get(template_key)
        if not template:
            logger.warning(
                "email template not found id=%s tenant=%s", template_id, tenant_id
            )
            return

        body = template
        for var, val in variables.items():
            body = body.replace(f"{{{{{var}}}}}", val)

        await self.deliver_text({
            "contact_id": contact_id,
            "session_id": session_id,
            "tenant_id":  tenant_id,
            "content":    {"text": body},
            "metadata":   meta,
        })

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _get_provider(self) -> IEmailProvider:
        if self._provider is not None:
            return self._provider

        dev_mode = not self._settings.email_api_key
        return MailgunProvider(
            api_key     = self._settings.email_api_key,
            domain      = self._settings.email_domain,
            signing_key = self._settings.email_signing_key,
            dev_mode    = dev_mode,
        )

    async def _publish_inbound(self, payload: dict) -> None:
        await self._producer.send(
            self._settings.kafka_topic_inbound,
            json.dumps(payload).encode(),
        )

    async def _publish_event(self, payload: dict) -> None:
        await self._producer.send(
            self._settings.kafka_topic_events,
            json.dumps(payload).encode(),
        )


# ── Text helpers ──────────────────────────────────────────────────────────────

def _strip_quoted_text(body_text: str, body_html: str) -> str:
    """
    Extract only the new text from an email reply, stripping quoted content.

    Strategy:
    1. Use plain text if available.
    2. Fall back to html2text conversion of HTML body.
    3. Split at the first quote marker and return only the text above it.
    """
    text = body_text.strip() if body_text else ""

    if not text and body_html:
        try:
            import html2text
            h        = html2text.HTML2Text()
            h.ignore_links = False
            text     = h.handle(body_html).strip()
        except ImportError:
            # Fallback: crude tag strip
            text = re.sub(r"<[^>]+>", " ", body_html).strip()

    if not text:
        return ""

    # Find the earliest quote boundary
    earliest = len(text)
    for pattern in _QUOTE_PATTERNS:
        match = pattern.search(text)
        if match and match.start() < earliest:
            earliest = match.start()

    new_text = text[:earliest].strip()
    return new_text or text  # fallback to full text if no quote boundary found


def _markdown_to_html(text: str) -> str:
    """Render Markdown text to HTML for the text/html MIME part."""
    try:
        import mistune
        return mistune.html(text)
    except ImportError:
        # Fallback: wrap paragraphs in <p> tags
        paragraphs = text.split("\n\n")
        return "\n".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paragraphs)


def _message_id_key(message_id: str) -> str:
    """Redis key for Message-ID → session_id mapping."""
    hashed = hashlib.sha256(message_id.encode()).hexdigest()[:16]
    return f"channel:email:{hashed}:session"


def _contact_key(email: str) -> str:
    """Redis key for contact email address → session_id mapping."""
    hashed = hashlib.sha256(email.lower().encode()).hexdigest()[:16]
    return f"channel:email:addr:{hashed}:session"
