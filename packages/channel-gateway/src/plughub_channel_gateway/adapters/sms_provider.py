"""
adapters/sms_provider.py
Provider abstraction for SMS delivery.

Architecture: channel-gateway-multi-channel.md § 8.3.1

ISMSProvider is a Protocol — any concrete implementation that matches the
interface can be used without changing SMSAdapter.

Provided implementations:
  TwilioProvider   — calls Twilio REST API, verifies HMAC-SHA1 webhooks
  MockSMSProvider  — in-memory stub for unit/integration tests

Adding a new provider (Telnyx, Vonage, AWS SNS):
  1. Implement ISMSProvider Protocol
  2. Set PLUGHUB_SMS_PROVIDER=<name> env var
  3. Register in SMSAdapter._build_provider()
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import urllib.parse
from typing import Any, Protocol

import httpx

logger = logging.getLogger("plughub.channel-gateway.sms.provider")

# ── Segment constants ─────────────────────────────────────────────────────────
# SMS encoding limits
_SINGLE_SMS_MAX  = 160   # single-part SMS max chars
_MULTIPART_MAX   = 153   # per-segment chars when using UDH header
_MAX_SEGMENTS    = 10    # hard cap → max 1530 content chars
_SUFFIX_TMPL     = " ({n}/{t})"


# ── Text splitting helper ─────────────────────────────────────────────────────

def split_sms(text: str) -> list[str]:
    """
    Split *text* into SMS-sized segments.

    Rules:
    - ≤ 160 chars → returned as-is (single element list)
    - > 160 chars → split at 153-char boundaries; each segment gets suffix "(N/T)"
    - Content exceeding 1530 chars is truncated and marked with '…' before split
    """
    if len(text) <= _SINGLE_SMS_MAX:
        return [text]

    # Reserve space for the longest possible suffix, e.g. " (10/10)" = 9 chars
    suffix_overhead = len(f" ({_MAX_SEGMENTS}/{_MAX_SEGMENTS})")
    chunk_size      = _MULTIPART_MAX - suffix_overhead

    max_content = chunk_size * _MAX_SEGMENTS
    if len(text) > max_content:
        text = text[: max_content - 1] + "…"

    # Split into raw chunks
    chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
    total  = len(chunks)
    return [f"{chunk} ({i + 1}/{total})" for i, chunk in enumerate(chunks)]


# ── Protocol ──────────────────────────────────────────────────────────────────

class ISMSProvider(Protocol):
    """Minimal interface every SMS provider must expose."""

    async def send_text(self, to: str, body: str) -> str:
        """
        Send *body* to the *to* E.164 number.
        Returns the provider-assigned message SID.
        Long texts should be split into multiple segments by the provider impl
        (or the caller can pass pre-split segments).
        """
        ...

    async def verify_signature(
        self,
        url:       str,
        params:    dict[str, str],
        signature: str,
    ) -> bool:
        """
        Verify that the inbound webhook came from the provider.
        Returns True if valid, False otherwise.
        Dev mode implementations may always return True.
        """
        ...


# ── Twilio provider ───────────────────────────────────────────────────────────

class TwilioProvider:
    """
    Twilio REST API SMS provider.

    Webhook verification: HMAC-SHA1 over (url + sorted_params).
    Outbound: POST https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json
    Long texts are auto-split into multiple API calls with segment suffixes.
    """

    _BASE_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"

    def __init__(
        self,
        account_sid:  str,
        auth_token:   str,
        from_number:  str,
        *,
        dev_mode: bool = False,
    ) -> None:
        self._account_sid = account_sid
        self._auth_token  = auth_token
        self._from_number = from_number
        self._dev_mode    = dev_mode

    async def send_text(self, to: str, body: str) -> str:
        """Send one or more SMS segments; returns SID of last message."""
        segments = split_sms(body)
        url      = self._BASE_URL.format(sid=self._account_sid)
        last_sid = ""

        async with httpx.AsyncClient() as client:
            for segment in segments:
                resp = await client.post(
                    url,
                    auth=(self._account_sid, self._auth_token),
                    data={
                        "To":   to,
                        "From": self._from_number,
                        "Body": segment,
                    },
                )
                resp.raise_for_status()
                data     = resp.json()
                last_sid = data.get("sid", "")
                logger.debug("Twilio SMS sent to=%s sid=%s", to, last_sid)

        return last_sid

    async def verify_signature(
        self,
        url:       str,
        params:    dict[str, str],
        signature: str,
    ) -> bool:
        """
        Twilio HMAC-SHA1 signature verification.
        https://www.twilio.com/docs/usage/webhooks/webhooks-security

        Algorithm:
        1. Take the full URL of the request URL
        2. Append all POST params sorted alphabetically (key+value concatenated)
        3. HMAC-SHA1 with auth_token
        4. Base64-encode
        """
        if self._dev_mode:
            return True

        # Build the string to sign
        sorted_pairs = "".join(
            f"{k}{v}" for k, v in sorted(params.items())
        )
        string_to_sign = url + sorted_pairs

        mac      = hmac.new(
            self._auth_token.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            hashlib.sha1,
        )
        expected = base64.b64encode(mac.digest()).decode("utf-8")
        return hmac.compare_digest(expected, signature)


# ── Mock provider ─────────────────────────────────────────────────────────────

class MockSMSProvider:
    """
    In-memory SMS provider for unit and integration tests.

    All outbound messages are appended to *sent_messages*.
    Signature verification always returns *verify_result* (default True).
    """

    def __init__(self, *, verify_result: bool = True) -> None:
        self.sent_messages: list[dict[str, Any]] = []
        self._verify_result = verify_result
        self._sid_counter   = 0

    def _next_sid(self) -> str:
        self._sid_counter += 1
        return f"SM_mock_{self._sid_counter:04d}"

    async def send_text(self, to: str, body: str) -> str:
        segments = split_sms(body)
        last_sid = ""
        for segment in segments:
            last_sid = self._next_sid()
            self.sent_messages.append({
                "type":    "text",
                "to":      to,
                "body":    segment,
                "sid":     last_sid,
            })
        return last_sid

    async def verify_signature(
        self,
        url:       str,
        params:    dict[str, str],
        signature: str,
    ) -> bool:
        return self._verify_result
