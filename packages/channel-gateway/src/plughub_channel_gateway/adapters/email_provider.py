"""
adapters/email_provider.py
Provider abstraction for Email delivery and inbound parsing.

Architecture: channel-gateway-multi-channel.md § 8.4.1

IEmailProvider is a Protocol — any concrete implementation can be used
without changing EmailAdapter.

Provided implementations:
  MailgunProvider   — Mailgun HTTP API v3, HMAC-SHA256 webhook verification
  MockEmailProvider — in-memory stub for unit/integration tests

Future providers (not yet implemented):
  SendGridProvider, AWSSESProvider, GraphAPIProvider (Exchange/O365),
  GmailProvider, IMAPSMTPProvider (polling model — different from webhook)
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import logging
from email import policy as email_policy
from email.parser import BytesParser
from typing import Any, Protocol

import httpx

logger = logging.getLogger("plughub.channel-gateway.email.provider")


# ── Data models ───────────────────────────────────────────────────────────────

@dataclasses.dataclass
class EmailAttachment:
    filename:   str
    mime_type:  str
    data:       bytes
    size_bytes: int = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        self.size_bytes = len(self.data)


@dataclasses.dataclass
class ParsedEmail:
    message_id:   str
    from_address: str
    to_address:   str
    subject:      str
    body_text:    str           # plain text (may need strip of quoted text)
    body_html:    str           # raw HTML (may be empty)
    in_reply_to:  str | None    # Message-ID this is a reply to
    references:   list[str]     # full References chain
    attachments:  list[EmailAttachment] = dataclasses.field(default_factory=list)


# ── Protocol ──────────────────────────────────────────────────────────────────

class IEmailProvider(Protocol):
    """Minimal interface every email provider must expose."""

    async def verify_signature(
        self,
        headers: dict[str, str],
        body:    bytes,
    ) -> bool:
        """Verify the inbound webhook came from the provider."""
        ...

    async def parse_inbound(
        self,
        headers: dict[str, str],
        body:    bytes,
    ) -> ParsedEmail:
        """Parse provider-specific inbound webhook payload → ParsedEmail."""
        ...

    async def send(
        self,
        *,
        to:           str,
        subject:      str,
        body_text:    str,
        body_html:    str,
        from_address: str,
        reply_to:     str,
        in_reply_to:  str | None       = None,
        references:   list[str]        = (),
        attachments:  list[EmailAttachment] = (),
    ) -> str:
        """Send an email. Returns the provider-assigned Message-ID."""
        ...


# ── Mailgun provider ──────────────────────────────────────────────────────────

class MailgunProvider:
    """
    Mailgun HTTP API v3 email provider.

    Inbound: Mailgun sends form-encoded POST with MIME part + headers.
    Outbound: POST https://api.mailgun.net/v3/{domain}/messages
    Verification: HMAC-SHA256 over (timestamp + token).
    """

    _API_BASE = "https://api.mailgun.net/v3"

    def __init__(
        self,
        api_key:     str,
        domain:      str,
        signing_key: str,
        *,
        dev_mode: bool = False,
    ) -> None:
        self._api_key     = api_key
        self._domain      = domain
        self._signing_key = signing_key
        self._dev_mode    = dev_mode

    async def verify_signature(
        self,
        headers: dict[str, str],
        body:    bytes,
    ) -> bool:
        """
        Mailgun webhook signature verification (v2).
        https://documentation.mailgun.com/docs/mailgun/user-manual/tracking-messages/#securing-webhooks

        Signs: timestamp + token  with  signing_key  via  HMAC-SHA256.
        Headers checked: X-Mailgun-Timestamp, X-Mailgun-Token, X-Mailgun-Signature.
        """
        if self._dev_mode or not self._signing_key:
            return True

        timestamp = headers.get("X-Mailgun-Timestamp", "")
        token     = headers.get("X-Mailgun-Token", "")
        signature = headers.get("X-Mailgun-Signature", "")

        value    = (timestamp + token).encode()
        computed = hmac.new(
            self._signing_key.encode(), value, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(computed, signature)

    async def parse_inbound(
        self,
        headers: dict[str, str],
        body:    bytes,
    ) -> ParsedEmail:
        """
        Parse Mailgun inbound webhook.
        Mailgun sends multipart/form-data with individual fields extracted from
        the original MIME message plus the raw MIME body in 'body-mime'.
        """
        # Parse form fields from body (Mailgun sends form-encoded)
        import urllib.parse
        # For multipart form data we use the raw MIME body if present
        # Mailgun sends: From, To, Subject, body-plain, body-html,
        #   Message-Id, In-Reply-To, References, Content-Type, body-mime
        try:
            from multipart import MultipartDecoder  # type: ignore
            content_type = headers.get("Content-Type", "")
            decoder      = MultipartDecoder(body, content_type)
            fields: dict[str, str | bytes] = {}
            for part in decoder.parts:
                cd = part.headers.get(b"Content-Disposition", b"").decode()
                name_start = cd.find('name="')
                if name_start == -1:
                    continue
                name_start += 6
                name_end = cd.find('"', name_start)
                name     = cd[name_start:name_end]
                fields[name] = part.content
        except ImportError:
            # Fallback: parse form-encoded body
            parsed = urllib.parse.parse_qs(body.decode(errors="replace"))
            fields = {k: v[0] for k, v in parsed.items()}

        def _field(name: str) -> str:
            val = fields.get(name, b"" if isinstance(fields.get(name), bytes) else "")
            return val.decode(errors="replace") if isinstance(val, bytes) else str(val)

        message_id  = _field("Message-Id") or _field("message-id")
        from_addr   = _field("From") or _field("sender")
        to_addr     = _field("To") or _field("recipient")
        subject     = _field("Subject") or _field("subject")
        body_text   = _field("body-plain")
        body_html   = _field("body-html")
        in_reply_to = _field("In-Reply-To") or None
        references  = [r.strip() for r in _field("References").split() if r.strip()]

        # Normalize from_address to bare email
        from_addr = _extract_email(from_addr)
        to_addr   = _extract_email(to_addr)

        # Parse attachments from body-mime if present
        attachments: list[EmailAttachment] = []
        raw_mime = fields.get("body-mime", b"")
        if isinstance(raw_mime, str):
            raw_mime = raw_mime.encode()
        if raw_mime:
            attachments = _extract_attachments_from_mime(raw_mime)

        return ParsedEmail(
            message_id   = message_id.strip("<>"),
            from_address = from_addr,
            to_address   = to_addr,
            subject      = subject,
            body_text    = body_text,
            body_html    = body_html,
            in_reply_to  = in_reply_to.strip("<>") if in_reply_to else None,
            references   = references,
            attachments  = attachments,
        )

    async def send(
        self,
        *,
        to:           str,
        subject:      str,
        body_text:    str,
        body_html:    str,
        from_address: str,
        reply_to:     str,
        in_reply_to:  str | None           = None,
        references:   list[str]            = (),
        attachments:  list[EmailAttachment] = (),
    ) -> str:
        url = f"{self._API_BASE}/{self._domain}/messages"

        data: dict[str, str] = {
            "from":    from_address,
            "to":      to,
            "subject": subject,
            "text":    body_text,
            "html":    body_html,
            "h:Reply-To": reply_to,
        }
        if in_reply_to:
            data["h:In-Reply-To"] = f"<{in_reply_to}>"
        if references:
            data["h:References"] = " ".join(f"<{r}>" for r in references)

        files = [
            ("attachment", (att.filename, att.data, att.mime_type))
            for att in attachments
        ]

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                auth=("api", self._api_key),
                data=data,
                files=files or None,
            )
            resp.raise_for_status()
            result = resp.json()
            msg_id = result.get("id", "")
            logger.debug("Mailgun sent to=%s id=%s", to, msg_id)
            return msg_id.strip("<>")


# ── Mock provider ─────────────────────────────────────────────────────────────

class MockEmailProvider:
    """
    In-memory email provider for unit and integration tests.

    Inbound: parse_inbound returns whatever ParsedEmail is set via
             load_inbound(); defaults to a generic test email.
    Outbound: sent_messages records all send() calls.
    Verification: always returns verify_result (default True).
    """

    def __init__(self, *, verify_result: bool = True) -> None:
        self.sent_messages: list[dict[str, Any]] = []
        self._verify_result    = verify_result
        self._queued_inbounds: list[ParsedEmail] = []
        self._msg_counter      = 0

    def load_inbound(self, email: ParsedEmail) -> None:
        """Queue a ParsedEmail to be returned by the next parse_inbound call."""
        self._queued_inbounds.append(email)

    def _next_id(self) -> str:
        self._msg_counter += 1
        return f"mock-msg-{self._msg_counter:04d}@mock.mailgun.org"

    async def verify_signature(
        self,
        headers: dict[str, str],
        body:    bytes,
    ) -> bool:
        return self._verify_result

    async def parse_inbound(
        self,
        headers: dict[str, str],
        body:    bytes,
    ) -> ParsedEmail:
        if self._queued_inbounds:
            return self._queued_inbounds.pop(0)
        # Default generic email
        return ParsedEmail(
            message_id   = self._next_id(),
            from_address = "cliente@example.com",
            to_address   = "suporte@empresa.com",
            subject      = "Preciso de ajuda",
            body_text    = "Olá, preciso de suporte.",
            body_html    = "<p>Olá, preciso de suporte.</p>",
            in_reply_to  = None,
            references   = [],
        )

    async def send(
        self,
        *,
        to:           str,
        subject:      str,
        body_text:    str,
        body_html:    str,
        from_address: str,
        reply_to:     str,
        in_reply_to:  str | None           = None,
        references:   list[str]            = (),
        attachments:  list[EmailAttachment] = (),
    ) -> str:
        msg_id = self._next_id()
        self.sent_messages.append({
            "to":           to,
            "subject":      subject,
            "body_text":    body_text,
            "body_html":    body_html,
            "from_address": from_address,
            "reply_to":     reply_to,
            "in_reply_to":  in_reply_to,
            "references":   list(references),
            "attachments":  [a.filename for a in attachments],
            "message_id":   msg_id,
        })
        return msg_id


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_email(header_value: str) -> str:
    """Extract bare email address from 'Name <email>' or 'email' format."""
    header_value = header_value.strip()
    if "<" in header_value and ">" in header_value:
        start = header_value.rfind("<") + 1
        end   = header_value.rfind(">")
        return header_value[start:end].strip().lower()
    return header_value.lower()


def _extract_attachments_from_mime(raw_mime: bytes) -> list[EmailAttachment]:
    """Extract non-inline attachments from a raw MIME message."""
    attachments: list[EmailAttachment] = []
    try:
        msg = BytesParser(policy=email_policy.default).parsebytes(raw_mime)
        for part in msg.walk():
            content_disposition = part.get_content_disposition()
            if content_disposition not in ("attachment",):
                continue
            filename  = part.get_filename() or "attachment"
            mime_type = part.get_content_type()
            data      = part.get_payload(decode=True) or b""
            attachments.append(EmailAttachment(
                filename  = filename,
                mime_type = mime_type,
                data      = data,
            ))
    except Exception as exc:
        logger.warning("attachment extraction failed: %s", exc)
    return attachments
