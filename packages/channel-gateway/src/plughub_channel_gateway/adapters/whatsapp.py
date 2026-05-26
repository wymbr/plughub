"""
adapters/whatsapp.py
WhatsApp channel adapter — inbound webhook + outbound delivery.

Architecture: channel-gateway-multi-channel.md § 8.2

Inbound flow (per webhook POST):
  1. verify_signature(body, header) — HMAC-SHA256 with app_secret
  2. HTTP 200 returned immediately
  3. asyncio.create_task(_process_inbound(body))
     a. _resolve_session(contact_id) — lookup or create session
     b. text → NormalizedInboundEvent directly
     c. media → background download → AttachmentStore → NormalizedInboundEvent

Outbound flow (per conversations.outbound Kafka message):
  deliver_text / deliver_menu / deliver_typing / deliver_session_closed
  called by OutboundConsumer registry — one singleton per process.

Session model:
  contact_id = customer E.164 phone number (e.g. "+5511999990000")
  Redis key: channel:whatsapp:{contact_id}:session → session_id (TTL 24h)
  Renewed on every inbound message from the customer.

Credential resolution:
  1. Redis {tenant_id}:config:whatsapp:access_token (per-tenant SaaS override)
  2. Settings.whatsapp_access_token (env var — default per installation)
  Same pattern as webchat JWT secret resolution.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

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
from ..channel_capability_registry import write_journey_channel_context
from .base import ChannelAdapter
from .whatsapp_provider import IWhatsAppProvider, MetaCloudProvider

logger = logging.getLogger("plughub.channel-gateway.whatsapp")

# Redis TTL for the session lookup key (24h — WhatsApp conversation window)
_SESSION_TTL = 86_400
# Redis TTL for sequential menu collect state (30 min)
_MENU_COLLECT_TTL = 1_800
# Max chars per WhatsApp text message (Meta limit)
_MAX_TEXT_LEN = 4096
# Button title max length (Meta limit)
_BTN_TITLE_MAX = 20
# List button label
_LIST_BUTTON_LABEL = "Ver opções"


class WhatsAppAdapter(ChannelAdapter):
    """
    Channel-level singleton adapter for WhatsApp.

    Args:
        producer:         Kafka producer for publishing normalised events.
        redis:            Async Redis client.
        settings:         Gateway settings (env vars).
        attachment_store: Shared attachment store for media downloads.
        provider:         IWhatsAppProvider implementation. If None, a
                          MetaCloudProvider is built from settings at runtime.
                          Pass a MockWhatsAppProvider in tests.
    """

    channel = "whatsapp"

    def __init__(
        self,
        *,
        producer:         AIOKafkaProducer,
        redis:            aioredis.Redis,
        settings:         Settings,
        attachment_store: AttachmentStore | None = None,
        provider:         IWhatsAppProvider | None = None,
    ) -> None:
        self._producer         = producer
        self._redis            = redis
        self._settings         = settings
        self._attachment_store = attachment_store
        self._provider         = provider  # None → resolved lazily per tenant

    # ── Inbound — called from the FastAPI webhook route ────────────────────────

    def verify_signature(self, body: bytes, signature_header: str) -> bool:
        """
        Validate X-Hub-Signature-256 header.
        Returns True if HMAC matches, False otherwise.
        signature_header format: "sha256=<hex>"
        """
        if not self._settings.whatsapp_app_secret:
            logger.warning("whatsapp_app_secret not configured — skipping signature check")
            return True  # dev/test mode without secret

        secret = self._settings.whatsapp_app_secret.encode()
        expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature_header or "")

    async def handle_inbound(self, body: bytes) -> None:
        """
        Entry point called by the webhook route after verify_signature passes.
        Responds to the caller immediately (HTTP 200 already sent by the route).
        Processing happens in a background task.
        """
        asyncio.create_task(self._process_inbound(body))

    # ── Inbound processing (background) ───────────────────────────────────────

    async def _process_inbound(self, body: bytes) -> None:
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            logger.error("whatsapp inbound invalid JSON: %s", exc)
            return

        # Meta Cloud API payload structure:
        # { "entry": [{ "changes": [{ "value": { "messages": [...], ... } }] }] }
        try:
            value    = data["entry"][0]["changes"][0]["value"]
            messages = value.get("messages", [])
        except (KeyError, IndexError):
            # Status update or other non-message event — ignore silently
            logger.debug("whatsapp: non-message webhook payload, skipping")
            return

        for msg in messages:
            await self._handle_message(value, msg)

    async def _handle_message(self, value: dict, msg: dict) -> None:
        """Process a single message object from the Meta webhook payload."""
        contact_id = msg.get("from", "")  # customer E.164 number
        wamid      = msg.get("id", "")
        msg_type   = msg.get("type", "")

        if not contact_id:
            logger.warning("whatsapp message missing 'from' field, skipping")
            return

        # Resolve or create session
        session_id, tenant_id, pool_id = await self._resolve_session(contact_id)
        started_at = datetime.now(timezone.utc).isoformat()

        logger.info(
            "whatsapp inbound type=%s contact_id=%s session_id=%s wamid=%s",
            msg_type, contact_id, session_id, wamid,
        )

        if msg_type == "text":
            text = msg.get("text", {}).get("body", "")
            await self._publish_text(
                contact_id=contact_id,
                session_id=session_id,
                tenant_id=tenant_id,
                text=text,
                wamid=wamid,
            )

        elif msg_type in ("image", "video", "document", "audio"):
            media = msg.get(msg_type, {})
            media_id = media.get("id", "")
            caption  = media.get("caption")
            await self._handle_media(
                contact_id=contact_id,
                session_id=session_id,
                tenant_id=tenant_id,
                msg_type=msg_type,
                media_id=media_id,
                caption=caption,
                wamid=wamid,
            )

        elif msg_type == "interactive":
            interactive = msg.get("interactive", {})
            itype       = interactive.get("type", "")
            if itype == "button_reply":
                reply = interactive["button_reply"]
                await self._check_sequential_collect(
                    contact_id=contact_id,
                    session_id=session_id,
                    tenant_id=tenant_id,
                    value=reply.get("id", reply.get("title", "")),
                    label=reply.get("title", ""),
                    wamid=wamid,
                )
            elif itype == "list_reply":
                reply = interactive["list_reply"]
                await self._check_sequential_collect(
                    contact_id=contact_id,
                    session_id=session_id,
                    tenant_id=tenant_id,
                    value=reply.get("id", reply.get("title", "")),
                    label=reply.get("title", ""),
                    wamid=wamid,
                )

        elif msg_type == "location":
            loc  = msg.get("location", {})
            text = f"lat:{loc.get('latitude', '')} lng:{loc.get('longitude', '')}"
            if loc.get("name"):
                text += f" ({loc['name']})"
            await self._publish_text(
                contact_id=contact_id,
                session_id=session_id,
                tenant_id=tenant_id,
                text=text,
                wamid=wamid,
            )

        else:
            logger.debug("whatsapp: unsupported message type=%s, skipping", msg_type)

    async def _publish_text(
        self,
        contact_id: str,
        session_id: str,
        tenant_id:  str,
        text:       str,
        wamid:      str,
    ) -> None:
        """Check pending collect states first; if none, publish normally."""
        # ── Arc 16 Phase D: capability-based pending collect ─────────────────
        pending_collect_key = f"channel:whatsapp:{contact_id}:pending_collect"
        pending_raw = await self._redis.get(pending_collect_key)
        if pending_raw:
            pending = json.loads(pending_raw)
            await self._redis.delete(pending_collect_key)
            journey_id_p = pending.get("journey_id")
            # Register channel contact in journey ContextStore
            if journey_id_p:
                tenant_id = self._settings.tenant_id
                await write_journey_channel_context(
                    redis      = self._redis,
                    tenant_id  = tenant_id,
                    journey_id = journey_id_p,
                    channel    = "whatsapp",
                    contact_id = contact_id,
                )
            payload = NormalizedInboundEvent(
                message_id       = str(uuid.uuid4()),
                contact_id       = contact_id,
                session_id       = session_id,
                channel          = "whatsapp",
                content_type     = "text",
                author           = MessageAuthor(type="customer"),
                content          = MessageContent(type="text", text=text),
                context_snapshot = ContextSnapshot(),
            ).model_dump()
            payload["collect_token"] = pending.get("collect_token")
            payload["journey_id"]    = journey_id_p
            payload["response_text"] = text
            await self._publish_inbound(payload)
            return

        # Check if there's an active sequential collect waiting for a text answer
        collect_key = f"channel:whatsapp:{session_id}:menu_collect"
        collect_raw = await self._redis.get(collect_key)
        if collect_raw:
            await self._advance_sequential_collect(
                session_id=session_id,
                tenant_id=tenant_id,
                contact_id=contact_id,
                collect_key=collect_key,
                collect_state=json.loads(collect_raw),
                value=text,
                label=text,
                wamid=wamid,
            )
            return

        event = NormalizedInboundEvent(
            message_id       = str(uuid.uuid4()),
            contact_id       = contact_id,
            session_id       = session_id,
            channel          = "whatsapp",
            content_type     = "text",
            author           = MessageAuthor(type="customer"),
            content          = MessageContent(type="text", text=text),
            context_snapshot = ContextSnapshot(),
        )
        await self._publish_inbound(event.model_dump())

    async def _handle_media(
        self,
        contact_id: str,
        session_id: str,
        tenant_id:  str,
        msg_type:   str,
        media_id:   str,
        caption:    str | None,
        wamid:      str,
    ) -> None:
        """Resolve media URL, download, store in AttachmentStore, publish event."""
        try:
            provider = await self._get_provider(tenant_id)
            media_url = await provider.get_media_url(media_id)
            raw_bytes, mime_type = await provider.download_media(media_url)
        except Exception as exc:
            logger.error(
                "whatsapp media download failed media_id=%s: %s", media_id, exc
            )
            return

        file_id: str | None = None
        if self._attachment_store is not None:
            try:
                from datetime import timedelta
                expires_at = datetime.now(timezone.utc) + timedelta(
                    days=self._settings.attachment_expiry_days
                )
                ext = _mime_to_ext(mime_type)
                file_id, _ = await self._attachment_store.reserve(
                    tenant_id  = tenant_id,
                    session_id = session_id,
                    file_name  = f"{media_id}{ext}",
                    mime_type  = mime_type,
                    size_bytes = len(raw_bytes),
                    expires_at = expires_at,
                )
                await self._attachment_store.commit(
                    file_id   = file_id,
                    data      = raw_bytes,
                    mime_type = mime_type,
                )
            except Exception as exc:
                logger.error("whatsapp attachment store failed: %s", exc)
                file_id = None

        content_type = _meta_type_to_content_type(msg_type)
        event = NormalizedInboundEvent(
            message_id       = str(uuid.uuid4()),
            contact_id       = contact_id,
            session_id       = session_id,
            channel          = "whatsapp",
            content_type     = content_type,
            author           = MessageAuthor(type="customer"),
            content          = MessageContent(
                type    = "media",
                payload = {
                    "media_type": msg_type,
                    "media_id":   media_id,
                    "file_id":    file_id,
                    "caption":    caption,
                    "wamid":      wamid,
                },
            ),
            context_snapshot = ContextSnapshot(),
        )
        await self._publish_inbound(event.model_dump())

    # ── Session management ─────────────────────────────────────────────────────

    async def _resolve_session(
        self, contact_id: str
    ) -> tuple[str, str, str]:
        """
        Lookup or create a session for this contact.
        Returns (session_id, tenant_id, pool_id).
        TTL is renewed on every call (any inbound message resets the 24h window).
        """
        tenant_id = self._settings.tenant_id
        key       = f"channel:whatsapp:{contact_id}:session"
        existing  = await self._redis.get(key)

        if existing:
            await self._redis.expire(key, _SESSION_TTL)
            session_id = existing
            logger.debug(
                "whatsapp: resumed session=%s contact=%s", session_id, contact_id
            )
        else:
            session_id = str(uuid.uuid4())
            await self._redis.setex(key, _SESSION_TTL, session_id)
            logger.info(
                "whatsapp: new session=%s contact=%s", session_id, contact_id
            )
            # Publish contact_open event for new sessions
            phone_id   = self._settings.whatsapp_phone_number_id
            pool_id    = phone_id  # pool resolved by routing engine from phone_number_id
            await self._publish_event(
                ContactOpenEvent(
                    contact_id         = contact_id,
                    session_id         = session_id,
                    tenant_id          = tenant_id,
                    channel            = "whatsapp",
                    channel_session_id = phone_id,
                ).model_dump()
            )
            await self._publish_inbound({
                "session_id":   session_id,
                "tenant_id":    tenant_id,
                "customer_id":  contact_id,
                "channel":      "whatsapp",
                "pool_id":      pool_id,
                "started_at":   datetime.now(timezone.utc).isoformat(),
                "elapsed_ms":   0,
            })

        pool_id = self._settings.whatsapp_phone_number_id
        return session_id, tenant_id, pool_id

    # ── Sequential collect (form fallback) ────────────────────────────────────

    async def _check_sequential_collect(
        self,
        contact_id: str,
        session_id: str,
        tenant_id:  str,
        value:      str,
        label:      str,
        wamid:      str,
    ) -> None:
        """Route interactive reply through sequential collect if active, else drop."""
        collect_key = f"channel:whatsapp:{session_id}:menu_collect"
        collect_raw = await self._redis.get(collect_key)
        if collect_raw:
            await self._advance_sequential_collect(
                session_id=session_id,
                tenant_id=tenant_id,
                contact_id=contact_id,
                collect_key=collect_key,
                collect_state=json.loads(collect_raw),
                value=value,
                label=label,
                wamid=wamid,
            )
        else:
            # Button/list reply with no active collect — treat as free text
            event = NormalizedInboundEvent(
                message_id       = str(uuid.uuid4()),
                contact_id       = contact_id,
                session_id       = session_id,
                channel          = "whatsapp",
                content_type     = "text",
                author           = MessageAuthor(type="customer"),
                content          = MessageContent(type="text", text=label or value),
                context_snapshot = ContextSnapshot(),
            )
            await self._publish_inbound(event.model_dump())

    async def _advance_sequential_collect(
        self,
        session_id:    str,
        tenant_id:     str,
        contact_id:    str,
        collect_key:   str,
        collect_state: dict,
        value:         str,
        label:         str,
        wamid:         str,
    ) -> None:
        """
        Record customer answer for the current field and either ask the next
        field or publish the completed menu_result event.
        """
        fields  = collect_state["fields"]
        idx     = collect_state["current_index"]
        answers = collect_state.get("answers", {})
        menu_id = collect_state["menu_id"]

        current_field = fields[idx]
        field_id      = current_field.get("id", f"field_{idx}")
        answers[field_id] = value

        next_idx = idx + 1
        if next_idx < len(fields):
            # More fields — ask the next one
            collect_state["current_index"] = next_idx
            collect_state["answers"]       = answers
            await self._redis.setex(collect_key, _MENU_COLLECT_TTL, json.dumps(collect_state))

            next_field = fields[next_idx]
            prompt     = next_field.get("label", next_field.get("id", ""))
            provider   = await self._get_provider(tenant_id)
            to         = contact_id.lstrip("+")
            await provider.send_text(to, prompt)
        else:
            # All fields collected — publish menu_result
            await self._redis.delete(collect_key)
            event = NormalizedInboundEvent(
                message_id       = str(uuid.uuid4()),
                contact_id       = contact_id,
                session_id       = session_id,
                channel          = "whatsapp",
                content_type     = "text",
                author           = MessageAuthor(type="customer"),
                content          = MessageContent(
                    type    = "menu_result",
                    payload = {
                        "menu_id":     menu_id,
                        "interaction": "form",
                        "result":      answers,
                    },
                ),
                context_snapshot = ContextSnapshot(),
            )
            await self._publish_inbound(event.model_dump())
            logger.info(
                "whatsapp sequential collect complete menu_id=%s session_id=%s",
                menu_id, session_id,
            )

    # ── ChannelAdapter interface (outbound delivery) ───────────────────────────

    async def deliver_text(self, payload: dict) -> None:
        """Send text message to customer via WhatsApp."""
        contact_id = payload.get("contact_id", "")
        text       = payload.get("text", "")
        session_id = payload.get("session_id", "")
        tenant_id  = self._settings.tenant_id

        if not contact_id or not text:
            return

        to       = contact_id.lstrip("+")
        provider = await self._get_provider(tenant_id)
        try:
            wamid = await provider.send_text(to, text[:_MAX_TEXT_LEN])
            logger.info(
                "whatsapp text sent contact_id=%s session_id=%s wamid=%s",
                contact_id, session_id, wamid,
            )
        except Exception as exc:
            logger.error(
                "whatsapp deliver_text failed contact_id=%s: %s", contact_id, exc
            )

    async def deliver_menu(self, payload: dict) -> None:
        """
        Render interaction.request as WhatsApp Interactive Message.
        Falls back to sequential collect for >10 options or form type.
        """
        contact_id  = payload.get("contact_id", "")
        session_id  = payload.get("session_id", "")
        menu_id     = payload.get("menu_id", "")
        interaction = payload.get("interaction", "text")
        prompt      = payload.get("prompt", "")
        options     = payload.get("options") or []
        fields      = payload.get("fields") or []
        tenant_id   = self._settings.tenant_id

        if not contact_id:
            return

        to       = contact_id.lstrip("+")
        provider = await self._get_provider(tenant_id)

        try:
            if interaction == "form" or len(fields) > 0:
                await self._start_sequential_collect(
                    provider=provider,
                    to=to,
                    session_id=session_id,
                    menu_id=menu_id,
                    prompt=prompt,
                    fields=fields or [{"id": f, "label": f} for f in payload.get("masked_fields", [])],
                )

            elif len(options) <= 3 and options:
                buttons = [
                    {"id": o.get("id", o.get("label", "")), "title": o.get("label", "")[:_BTN_TITLE_MAX]}
                    for o in options
                ]
                await provider.send_interactive_buttons(to, prompt, buttons)

            elif 4 <= len(options) <= 10:
                rows = [
                    {"id": o.get("id", o.get("label", "")), "title": o.get("label", "")[:24]}
                    for o in options
                ]
                sections = [{"rows": rows}]
                await provider.send_interactive_list(to, prompt[:60], prompt, sections)

            else:
                # >10 options — text fallback
                numbered = "\n".join(
                    f"{i+1}. {o.get('label', '')}" for i, o in enumerate(options)
                )
                text = f"{prompt}\n\n{numbered}\n\nResponda com o número da opção."
                await self._start_sequential_collect(
                    provider=provider,
                    to=to,
                    session_id=session_id,
                    menu_id=menu_id,
                    prompt=text,
                    fields=[{"id": "option", "label": text}],
                )

        except Exception as exc:
            logger.error(
                "whatsapp deliver_menu failed contact_id=%s: %s", contact_id, exc
            )

    async def deliver_typing(self, payload: dict) -> None:
        """WhatsApp has no typing indicator API — no-op."""
        pass

    async def deliver_session_closed(self, payload: dict) -> None:
        """
        Clean up Redis session key.
        No message sent to customer — WhatsApp has no "session ended" concept.
        """
        contact_id = payload.get("contact_id", "")
        session_id = payload.get("session_id", "")
        if contact_id:
            await self._redis.delete(f"channel:whatsapp:{contact_id}:session")
        if session_id:
            await self._redis.delete(f"channel:whatsapp:{session_id}:menu_collect")
        logger.info(
            "whatsapp session closed contact_id=%s session_id=%s", contact_id, session_id
        )

    # ── Collect event — outbound capability-based (Arc 16 Phase D) ───────────

    async def handle_collect_event(self, event: dict) -> None:
        """
        Send a collect prompt to the customer via WhatsApp and store a
        pending_collect key so the inbound handler can correlate the reply.

        Called by _collect_events_consumer() when collect.requested arrives with
        channel="whatsapp" (explicit or capability-selected).

        Redis key: channel:whatsapp:{contact_id}:pending_collect
          → {collect_token, journey_id, pool_id}  TTL = 30 min
        """
        contact_id    = event.get("target", "")
        collect_token = event.get("collect_token", "")
        journey_id    = event.get("journey_id")
        prompt        = event.get("prompt", "")
        tenant_id     = event.get("tenant_id", self._settings.tenant_id)
        pool_id       = event.get("pool_id", self._settings.whatsapp_phone_number_id or "")

        if not contact_id or not prompt:
            logger.warning(
                "whatsapp handle_collect_event: missing target or prompt "
                "(collect_token=%s)", collect_token,
            )
            return

        to = contact_id.lstrip("+")
        try:
            provider = await self._get_provider(tenant_id)
            await provider.send_text(to, prompt)
        except Exception as exc:
            logger.error(
                "whatsapp collect send failed contact=%s: %s", contact_id, exc,
            )
            return

        pending = {
            "collect_token": collect_token,
            "journey_id":    journey_id,
            "pool_id":       pool_id,
        }
        await self._redis.setex(
            f"channel:whatsapp:{contact_id}:pending_collect",
            1_800,  # 30 min
            json.dumps(pending),
        )

        logger.info(
            "whatsapp collect sent: contact=%s collect_token=%s journey=%s",
            contact_id, collect_token, journey_id,
        )

    # ── Sequential collect helpers ─────────────────────────────────────────────

    async def _start_sequential_collect(
        self,
        provider:   IWhatsAppProvider,
        to:         str,
        session_id: str,
        menu_id:    str,
        prompt:     str,
        fields:     list[dict],
    ) -> None:
        """
        Initialise sequential form collect: send the first field prompt and
        store state in Redis.
        """
        if not fields:
            await provider.send_text(to, prompt)
            return

        first_prompt = fields[0].get("label", fields[0].get("id", ""))
        # Prepend the overall menu prompt before the first field
        intro = f"{prompt}\n\n{first_prompt}" if prompt and prompt != first_prompt else first_prompt
        await provider.send_text(to, intro)

        collect_state = {
            "menu_id":       menu_id,
            "fields":        fields,
            "current_index": 0,
            "answers":       {},
        }
        collect_key = f"channel:whatsapp:{session_id}:menu_collect"
        await self._redis.setex(collect_key, _MENU_COLLECT_TTL, json.dumps(collect_state))
        logger.debug(
            "whatsapp sequential collect started menu_id=%s session_id=%s fields=%d",
            menu_id, session_id, len(fields),
        )

    # ── Provider resolution ────────────────────────────────────────────────────

    async def _get_provider(self, tenant_id: str) -> IWhatsAppProvider:
        """
        Returns the configured provider.  If a mock was injected (tests), use it.
        Otherwise build MetaCloudProvider with tenant-resolved credentials.
        """
        if self._provider is not None:
            return self._provider

        token    = await self._resolve_credential(tenant_id, "access_token")
        phone_id = await self._resolve_credential(tenant_id, "phone_number_id")

        return MetaCloudProvider(
            access_token    = token    or self._settings.whatsapp_access_token,
            phone_number_id = phone_id or self._settings.whatsapp_phone_number_id,
            graph_api_url   = self._settings.whatsapp_graph_api_url,
        )

    async def _resolve_credential(self, tenant_id: str, key: str) -> str:
        """
        Resolve per-tenant credential from Redis.
        Falls back to empty string (caller uses settings default).
        """
        try:
            val = await self._redis.get(f"{tenant_id}:config:whatsapp:{key}")
            return val or ""
        except Exception:
            return ""

    # ── Kafka helpers ──────────────────────────────────────────────────────────

    async def _publish_inbound(self, payload: dict) -> None:
        from ..config import get_settings
        topic = get_settings().kafka_topic_inbound
        await self._producer.send(topic, value=json.dumps(payload).encode())

    async def _publish_event(self, payload: dict) -> None:
        from ..config import get_settings
        topic = get_settings().kafka_topic_events
        await self._producer.send(topic, value=json.dumps(payload).encode())


# ── Helpers ────────────────────────────────────────────────────────────────────

def _meta_type_to_content_type(msg_type: str) -> str:
    return {
        "image":    "image",
        "video":    "video",
        "document": "document",
        "audio":    "audio_transcript",  # marked for future STT
    }.get(msg_type, "document")


def _mime_to_ext(mime_type: str) -> str:
    return {
        "image/jpeg":       ".jpg",
        "image/png":        ".png",
        "image/webp":       ".webp",
        "video/mp4":        ".mp4",
        "application/pdf":  ".pdf",
        "audio/ogg":        ".ogg",
        "audio/mpeg":       ".mp3",
    }.get(mime_type, ".bin")
