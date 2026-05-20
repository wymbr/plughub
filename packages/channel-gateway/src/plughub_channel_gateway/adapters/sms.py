"""
adapters/sms.py
SMS channel adapter — inbound webhook + outbound delivery.

Architecture: channel-gateway-multi-channel.md § 8.3

Inbound flow (per webhook POST):
  1. SMSAdapter.process_inbound(form_data, signature, url)
     → verify_signature (Twilio HMAC-SHA1)
     → asyncio.create_task(_handle_inbound)
  2. HTTP 200 + TwiML empty response returned immediately
  3. Background task:
     a. _accumulate_parts — buffer multi-segment SMS
     b. _resolve_session  — lookup or create session in Redis
     c. Publish NormalizedInboundEvent(content_type="text")

Outbound flow (per conversations.outbound Kafka message):
  deliver_text / deliver_menu / deliver_typing / deliver_session_closed
  called by OutboundConsumer registry — one singleton per process.

Session model:
  contact_id = customer E.164 phone number (e.g. "+5511999990000")
  Redis key: channel:sms:{contact_id}:session → session_id (TTL 24h)
  Renewed on every inbound message from the customer.

Credential resolution:
  1. Redis {tenant_id}:config:sms:account_sid  (per-tenant SaaS override)
  2. Settings.sms_account_sid (env var — default per installation)
  Same pattern as WhatsApp credential resolution.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
from aiokafka import AIOKafkaProducer

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
from .sms_provider import ISMSProvider, MockSMSProvider, TwilioProvider, split_sms

logger = logging.getLogger("plughub.channel-gateway.sms")

# Redis TTL for the session lookup key (24h, renewed on every inbound)
_SESSION_TTL = 86_400
# Redis TTL for SMS concatenation parts buffer (5 min — generous for UDH delay)
_PARTS_TTL = 300
# Redis TTL for sequential menu collect state (30 min)
_MENU_COLLECT_TTL = 1_800


class SMSAdapter(ChannelAdapter):
    """
    Channel-level singleton adapter for SMS (Twilio + ISMSProvider abstraction).

    Args:
        producer:  Kafka producer for publishing normalised events.
        redis:     Async Redis client.
        settings:  Gateway settings (env vars).
        provider:  ISMSProvider implementation. If None, TwilioProvider is
                   built from settings at runtime.
                   Pass a MockSMSProvider in tests.
    """

    channel = "sms"

    def __init__(
        self,
        *,
        producer:  AIOKafkaProducer,
        redis:     aioredis.Redis,
        settings:  Settings,
        provider:  ISMSProvider | None = None,
    ) -> None:
        self._producer  = producer
        self._redis     = redis
        self._settings  = settings
        self._provider  = provider  # None → resolved lazily per tenant

    # ── Inbound — called from the FastAPI webhook route ───────────────────────

    async def process_inbound(
        self,
        params:    dict[str, str],
        signature: str,
        url:       str,
    ) -> None:
        """
        Entry point called by the webhook route.
        Twilio sends form-encoded bodies — parse before calling this method.
        HTTP 200 + TwiML response must be returned by the route BEFORE awaiting.
        Processing happens in a background task.
        """
        provider = await self._get_provider(self._settings.tenant_id)
        if not await provider.verify_signature(url, params, signature):
            logger.warning("sms inbound rejected — invalid signature")
            return

        asyncio.create_task(self._handle_inbound(params))

    # ── Inbound processing (background) ──────────────────────────────────────

    async def _handle_inbound(self, params: dict[str, str]) -> None:
        """
        Process a single inbound Twilio SMS webhook.
        Accumulates multi-segment messages; publishes when complete.
        """
        try:
            contact_id    = params.get("From", "").strip()
            body          = params.get("Body", "")
            sms_sid       = params.get("SmsSid", params.get("MessageSid", ""))
            num_segments  = int(params.get("NumSegments", "1"))
            # PartSequenceNumber is 1-based; absent for single-segment messages
            part_seq      = int(params.get("PartSequenceNumber", "1"))

            if not contact_id:
                logger.warning("sms inbound missing 'From' field, skipping")
                return

            logger.info(
                "sms inbound from=%s sms_sid=%s part=%d/%d",
                contact_id, sms_sid, part_seq, num_segments,
            )

            # Accumulate multi-segment messages
            full_text = await self._accumulate_parts(
                contact_id=contact_id,
                sms_sid=sms_sid,
                body=body,
                part_seq=part_seq,
                num_segments=num_segments,
            )
            if full_text is None:
                # Still waiting for more segments
                return

            # Session resolution (after complete message assembled)
            session_id, tenant_id = await self._resolve_session(
                contact_id=contact_id,
                channel_session_id=sms_sid,
            )

            # Route through sequential collect state if active
            collect_key = f"channel:sms:{session_id}:menu_collect"
            collect_raw = await self._redis.get(collect_key)
            if collect_raw:
                await self._advance_sequential_collect(
                    session_id=session_id,
                    tenant_id=tenant_id,
                    contact_id=contact_id,
                    collect_key=collect_key,
                    collect_state=json.loads(collect_raw),
                    value=full_text,
                )
                return

            event = NormalizedInboundEvent(
                message_id       = str(uuid.uuid4()),
                contact_id       = contact_id,
                session_id       = session_id,
                channel          = "sms",
                content_type     = "text",
                author           = MessageAuthor(type="customer"),
                content          = MessageContent(type="text", text=full_text),
                context_snapshot = ContextSnapshot(),
            )
            await self._publish_inbound(event.model_dump())

        except Exception as exc:
            logger.exception("sms inbound processing failed: %s", exc)

    # ── SMS concatenation ─────────────────────────────────────────────────────

    async def _accumulate_parts(
        self,
        contact_id:   str,
        sms_sid:      str,
        body:         str,
        part_seq:     int,
        num_segments: int,
    ) -> str | None:
        """
        Buffer SMS segments in Redis.
        Returns assembled text when all parts received, None otherwise.

        Redis key: channel:sms:{contact_id}:sms_parts:{sms_sid}
        Value: JSON list of (part_seq, body) tuples, sorted on read.
        TTL: 5 minutes (generous for inter-fragment delay).
        """
        if num_segments == 1:
            # Single-segment message — no buffering needed
            return body

        parts_key = f"channel:sms:{contact_id}:sms_parts:{sms_sid}"

        # Load existing parts
        raw = await self._redis.get(parts_key)
        parts: list[list] = json.loads(raw) if raw else []

        # Append current part (avoid duplicates from Twilio retries)
        if not any(p[0] == part_seq for p in parts):
            parts.append([part_seq, body])
            await self._redis.setex(parts_key, _PARTS_TTL, json.dumps(parts))

        if len(parts) < num_segments:
            logger.debug(
                "sms concatenation: %d/%d parts received sms_sid=%s",
                len(parts), num_segments, sms_sid,
            )
            return None

        # All parts received — assemble and clean up
        await self._redis.delete(parts_key)
        ordered = sorted(parts, key=lambda p: p[0])
        return "".join(p[1] for p in ordered)

    # ── Session management ────────────────────────────────────────────────────

    async def _resolve_session(
        self,
        contact_id:        str,
        channel_session_id: str,
    ) -> tuple[str, str]:
        """
        Lookup or create a session for this contact.
        Returns (session_id, tenant_id).
        TTL is renewed on every call.
        """
        tenant_id = self._settings.tenant_id
        key       = f"channel:sms:{contact_id}:session"
        existing  = await self._redis.get(key)

        if existing:
            await self._redis.expire(key, _SESSION_TTL)
            session_id = existing
            logger.debug(
                "sms: resumed session=%s contact=%s", session_id, contact_id
            )
        else:
            session_id = str(uuid.uuid4())
            await self._redis.setex(key, _SESSION_TTL, session_id)
            logger.info(
                "sms: new session=%s contact=%s", session_id, contact_id
            )
            await self._publish_event(
                ContactOpenEvent(
                    contact_id         = contact_id,
                    session_id         = session_id,
                    tenant_id          = tenant_id,
                    channel            = "sms",
                    channel_session_id = channel_session_id,
                ).model_dump()
            )
            # Publish contact-open inbound so routing engine creates a session
            pool_id = self._settings.sms_default_pool_id or "sms_default"
            await self._publish_inbound({
                "session_id":  session_id,
                "tenant_id":   tenant_id,
                "customer_id": contact_id,
                "channel":     "sms",
                "pool_id":     pool_id,
                "type":        "contact_open",
            })

        return session_id, tenant_id

    # ── Sequential collect ────────────────────────────────────────────────────

    async def _start_sequential_collect(
        self,
        session_id: str,
        tenant_id:  str,
        contact_id: str,
        payload:    dict,
    ) -> None:
        """
        Begin a sequential menu collect for SMS.
        Sends the first field prompt to the customer.
        """
        fields  = payload.get("fields", [])
        menu_id = payload.get("menu_id", str(uuid.uuid4()))

        if not fields:
            logger.warning("sms sequential collect: no fields in payload")
            return

        state = {
            "menu_id":       menu_id,
            "fields":        fields,
            "current_index": 0,
            "answers":       {},
        }
        collect_key = f"channel:sms:{session_id}:menu_collect"
        await self._redis.setex(collect_key, _MENU_COLLECT_TTL, json.dumps(state))

        await self._send_field_prompt(
            contact_id=contact_id,
            tenant_id=tenant_id,
            field=fields[0],
            index=0,
        )

    async def _advance_sequential_collect(
        self,
        session_id:    str,
        tenant_id:     str,
        contact_id:    str,
        collect_key:   str,
        collect_state: dict,
        value:         str,
    ) -> None:
        """
        Process a customer reply during sequential collect.
        Validates input for options fields; advances to next field or completes.
        """
        fields  = collect_state["fields"]
        index   = collect_state["current_index"]
        answers = collect_state["answers"]

        current_field = fields[index]
        field_id      = current_field.get("id", f"field_{index}")
        options       = current_field.get("options", [])

        # Validate numeric option selection
        if options:
            try:
                chosen = int(value.strip()) - 1  # 1-based → 0-based
                if chosen < 0 or chosen >= len(options):
                    raise ValueError
                selected_value = options[chosen].get("value", options[chosen].get("label", ""))
                answers[field_id] = selected_value
            except (ValueError, TypeError):
                # Invalid selection — re-prompt
                valid_range = f"1 a {len(options)}"
                await self._send_text_to_contact(
                    contact_id=contact_id,
                    tenant_id=tenant_id,
                    text=f"Opção inválida. Por favor, responda com um número de {valid_range}.",
                )
                # Restore state (unchanged)
                await self._redis.setex(
                    collect_key, _MENU_COLLECT_TTL, json.dumps(collect_state)
                )
                return
        else:
            answers[field_id] = value

        next_index = index + 1
        if next_index < len(fields):
            # Advance to next field
            collect_state["current_index"] = next_index
            collect_state["answers"]       = answers
            await self._redis.setex(
                collect_key, _MENU_COLLECT_TTL, json.dumps(collect_state)
            )
            await self._send_field_prompt(
                contact_id=contact_id,
                tenant_id=tenant_id,
                field=fields[next_index],
                index=next_index,
            )
        else:
            # All fields collected — clean up and publish menu_result
            await self._redis.delete(collect_key)
            event = NormalizedInboundEvent(
                message_id       = str(uuid.uuid4()),
                contact_id       = contact_id,
                session_id       = session_id,
                channel          = "sms",
                content_type     = "text",
                author           = MessageAuthor(type="customer"),
                content          = MessageContent(
                    type    = "menu_result",
                    payload = {
                        "menu_id": collect_state["menu_id"],
                        "answers": answers,
                    },
                ),
                context_snapshot = ContextSnapshot(),
            )
            await self._publish_inbound(event.model_dump())

    async def _send_field_prompt(
        self,
        contact_id: str,
        tenant_id:  str,
        field:      dict,
        index:      int,
    ) -> None:
        """Send a single field prompt as SMS text."""
        label   = field.get("label", f"Campo {index + 1}")
        options = field.get("options", [])
        masked  = field.get("masked", False)

        if options:
            lines = [label + ":"]
            for i, opt in enumerate(options, start=1):
                lines.append(f"{i}. {opt.get('label', opt.get('value', ''))}")
            lines.append("")
            lines.append("Responda com o número da opção.")
            text = "\n".join(lines)
        elif masked:
            text = f"{label}:\n(Este campo é confidencial. Sua resposta será tratada com segurança.)"
        else:
            text = f"{label}:"

        await self._send_text_to_contact(
            contact_id=contact_id,
            tenant_id=tenant_id,
            text=text,
        )

    # ── ChannelAdapter interface — outbound ───────────────────────────────────

    async def deliver_text(self, payload: dict) -> None:
        """
        Deliver a plain text message to the SMS contact.
        Long texts are automatically split into multiple SMS segments.
        """
        contact_id = payload.get("contact_id", "")
        tenant_id  = payload.get("tenant_id", self._settings.tenant_id)
        text       = payload.get("content", {}).get("text", "")

        if not contact_id or not text:
            logger.warning("sms deliver_text: missing contact_id or text")
            return

        await self._send_text_to_contact(
            contact_id=contact_id,
            tenant_id=tenant_id,
            text=text,
        )

    async def deliver_menu(self, payload: dict) -> None:
        """
        Deliver a menu as numbered SMS text.
        SMS has no native interactive elements — all menus use sequential collect.
        """
        contact_id = payload.get("contact_id", "")
        tenant_id  = payload.get("tenant_id", self._settings.tenant_id)
        session_id = payload.get("session_id", "")
        menu       = payload.get("content", {})
        fields     = menu.get("fields", [])

        if not contact_id:
            logger.warning("sms deliver_menu: missing contact_id")
            return

        # Intro text (title / question)
        title = menu.get("title") or menu.get("question", "")
        if title:
            await self._send_text_to_contact(
                contact_id=contact_id,
                tenant_id=tenant_id,
                text=title,
            )

        if fields and session_id:
            # Start sequential collect for multi-field menus
            await self._start_sequential_collect(
                session_id=session_id,
                tenant_id=tenant_id,
                contact_id=contact_id,
                payload=menu,
            )
        elif fields:
            # No session_id — send as plain enumerated text (fallback)
            for field in fields:
                await self._send_field_prompt(
                    contact_id=contact_id,
                    tenant_id=tenant_id,
                    field=field,
                    index=0,
                )

    async def deliver_typing(self, payload: dict) -> None:
        """SMS has no typing indicator — no-op."""
        logger.debug("sms deliver_typing: no-op for SMS channel")

    async def deliver_session_closed(self, payload: dict) -> None:
        """
        Clean up Redis keys when the session is closed.
        No message is sent to the customer — session close is silent on SMS.
        """
        contact_id = payload.get("contact_id", "")
        session_id = payload.get("session_id", "")

        if contact_id:
            await self._redis.delete(f"channel:sms:{contact_id}:session")
            logger.info("sms: session closed contact=%s session=%s", contact_id, session_id)

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _send_text_to_contact(
        self,
        contact_id: str,
        tenant_id:  str,
        text:       str,
    ) -> None:
        """Resolve provider and send text, handling segment splitting internally."""
        try:
            provider = await self._get_provider(tenant_id)
            await provider.send_text(to=contact_id, body=text)
        except Exception as exc:
            logger.error(
                "sms send_text failed contact=%s: %s", contact_id, exc
            )

    async def _get_provider(self, tenant_id: str) -> ISMSProvider:
        """
        Return the configured provider.
        If a provider was injected (tests), use it.
        Otherwise resolve credentials: Redis per-tenant override → env var default.
        """
        if self._provider is not None:
            return self._provider

        # Resolve credentials
        account_sid = await self._resolve_credential(tenant_id, "account_sid",
                                                      self._settings.sms_account_sid)
        auth_token  = await self._resolve_credential(tenant_id, "auth_token",
                                                      self._settings.sms_auth_token)
        from_number = await self._resolve_credential(tenant_id, "from_number",
                                                      self._settings.sms_from_number)

        dev_mode = not auth_token

        return TwilioProvider(
            account_sid=account_sid,
            auth_token=auth_token,
            from_number=from_number,
            dev_mode=dev_mode,
        )

    async def _resolve_credential(
        self,
        tenant_id: str,
        key:       str,
        default:   str,
    ) -> str:
        """Redis per-tenant override → env var default."""
        redis_val = await self._redis.get(f"{tenant_id}:config:sms:{key}")
        return redis_val or default or ""

    async def _publish_inbound(self, payload: dict) -> None:
        """Publish to conversations.inbound Kafka topic."""
        await self._producer.send(
            self._settings.kafka_topic_inbound,
            json.dumps(payload).encode(),
        )

    async def _publish_event(self, payload: dict) -> None:
        """Publish to conversations.events Kafka topic."""
        await self._producer.send(
            self._settings.kafka_topic_events,
            json.dumps(payload).encode(),
        )
