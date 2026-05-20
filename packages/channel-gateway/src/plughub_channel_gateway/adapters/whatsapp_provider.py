"""
adapters/whatsapp_provider.py
Provider abstraction for WhatsApp Business API.

Architecture: channel-gateway-multi-channel.md § 8.2.1

IWhatsAppProvider is a Protocol — any concrete implementation that matches
the interface can be used without changing WhatsAppAdapter.

Provided implementations:
  MetaCloudProvider   — calls graph.facebook.com/v19.0 directly
  MockWhatsAppProvider — in-memory stub for unit/integration tests

BSP adapters (Twilio, Infobip, 360dialog) should implement IWhatsAppProvider
and translate to/from Meta Cloud API format internally.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

import httpx

logger = logging.getLogger("plughub.channel-gateway.whatsapp.provider")


# ── Protocol ──────────────────────────────────────────────────────────────────

class IWhatsAppProvider(Protocol):
    """
    Abstract interface for WhatsApp outbound delivery and media resolution.
    All methods return the wamid (WhatsApp message ID) on success.
    """

    async def send_text(self, to: str, text: str) -> str:
        """Send a plain text message. Returns wamid."""
        ...

    async def send_interactive_buttons(
        self,
        to:      str,
        body:    str,
        buttons: list[dict[str, str]],  # [{"id": "...", "title": "..."}]
    ) -> str:
        """Send interactive button message (≤3 buttons). Returns wamid."""
        ...

    async def send_interactive_list(
        self,
        to:       str,
        header:   str,
        body:     str,
        sections: list[dict[str, Any]],
    ) -> str:
        """Send interactive list message (4-10 options). Returns wamid."""
        ...

    async def send_media(
        self,
        to:         str,
        media_type: str,   # "image" | "document" | "video" | "audio"
        link:       str,   # public URL
        caption:    str | None = None,
        filename:   str | None = None,
    ) -> str:
        """Send a media message via public URL. Returns wamid."""
        ...

    async def get_media_url(self, media_id: str) -> str:
        """
        Resolve a Meta media_id to a temporary download URL.
        The URL is valid for ~5 minutes — download immediately.
        """
        ...

    async def download_media(self, url: str) -> tuple[bytes, str]:
        """
        Download media bytes from a URL returned by get_media_url.
        Returns (bytes, mime_type).
        """
        ...


# ── Meta Cloud API ────────────────────────────────────────────────────────────

class MetaCloudProvider:
    """
    Concrete WhatsApp provider using Meta Cloud API (graph.facebook.com/v19.0).

    Args:
        access_token:    System User token from Meta Business Manager.
        phone_number_id: Phone Number ID from Meta Developer Portal.
        graph_api_url:   Base URL — override for BSP proxies or mocks.
    """

    def __init__(
        self,
        access_token:    str,
        phone_number_id: str,
        graph_api_url:   str = "https://graph.facebook.com/v19.0",
    ) -> None:
        self._token          = access_token
        self._phone_id       = phone_number_id
        self._base           = graph_api_url.rstrip("/")
        self._messages_url   = f"{self._base}/{phone_number_id}/messages"
        self._headers        = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type":  "application/json",
        }

    async def send_text(self, to: str, text: str) -> str:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type":    "individual",
            "to":                to,
            "type":              "text",
            "text":              {"preview_url": False, "body": text},
        }
        return await self._post(payload)

    async def send_interactive_buttons(
        self,
        to:      str,
        body:    str,
        buttons: list[dict[str, str]],
    ) -> str:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type":    "individual",
            "to":                to,
            "type":              "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body},
                "action": {
                    "buttons": [
                        {
                            "type":  "reply",
                            "reply": {"id": b["id"], "title": b["title"][:20]},
                        }
                        for b in buttons[:3]  # Meta hard limit: 3 buttons
                    ]
                },
            },
        }
        return await self._post(payload)

    async def send_interactive_list(
        self,
        to:       str,
        header:   str,
        body:     str,
        sections: list[dict[str, Any]],
    ) -> str:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type":    "individual",
            "to":                to,
            "type":              "interactive",
            "interactive": {
                "type":   "list",
                "header": {"type": "text", "text": header[:60]},
                "body":   {"text": body},
                "action": {
                    "button":   "Ver opções",
                    "sections": sections,
                },
            },
        }
        return await self._post(payload)

    async def send_media(
        self,
        to:         str,
        media_type: str,
        link:       str,
        caption:    str | None = None,
        filename:   str | None = None,
    ) -> str:
        media_obj: dict[str, Any] = {"link": link}
        if caption:
            media_obj["caption"] = caption
        if filename and media_type == "document":
            media_obj["filename"] = filename

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type":    "individual",
            "to":                to,
            "type":              media_type,
            media_type:          media_obj,
        }
        return await self._post(payload)

    async def get_media_url(self, media_id: str) -> str:
        url = f"{self._base}/{media_id}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=self._headers)
            resp.raise_for_status()
            data = resp.json()
            return data["url"]

    async def download_media(self, url: str) -> tuple[bytes, str]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=self._headers)
            resp.raise_for_status()
            mime = resp.headers.get("content-type", "application/octet-stream").split(";")[0]
            return resp.content, mime

    async def _post(self, payload: dict) -> str:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                self._messages_url, json=payload, headers=self._headers
            )
            resp.raise_for_status()
            data = resp.json()
            # Meta returns: {"messages": [{"id": "wamid.xxx"}]}
            wamid: str = data["messages"][0]["id"]
            logger.debug("whatsapp sent wamid=%s to=%s", wamid, payload.get("to"))
            return wamid


# ── Mock provider (tests) ─────────────────────────────────────────────────────

class MockWhatsAppProvider:
    """
    In-memory WhatsApp provider for unit and integration tests.
    Records all outbound calls in self.sent_messages for assertion.
    No network I/O.
    """

    def __init__(self) -> None:
        self.sent_messages: list[dict[str, Any]] = []
        self._wamid_counter = 0
        # Pre-loaded media: media_id → (bytes, mime_type)
        self._media_store: dict[str, tuple[bytes, str]] = {}
        # Pre-loaded media URLs: media_id → url
        self._media_urls: dict[str, str] = {}

    def _next_wamid(self) -> str:
        self._wamid_counter += 1
        return f"wamid.mock_{self._wamid_counter:04d}"

    def load_media(
        self, media_id: str, data: bytes, mime_type: str, url: str = ""
    ) -> None:
        """Pre-load mock media for get_media_url / download_media."""
        self._media_store[media_id] = (data, mime_type)
        self._media_urls[media_id] = url or f"https://mock.whatsapp/{media_id}"

    async def send_text(self, to: str, text: str) -> str:
        wamid = self._next_wamid()
        self.sent_messages.append({"type": "text", "to": to, "text": text, "wamid": wamid})
        return wamid

    async def send_interactive_buttons(
        self, to: str, body: str, buttons: list[dict[str, str]]
    ) -> str:
        wamid = self._next_wamid()
        self.sent_messages.append({
            "type": "interactive_buttons", "to": to, "body": body,
            "buttons": buttons, "wamid": wamid,
        })
        return wamid

    async def send_interactive_list(
        self, to: str, header: str, body: str, sections: list[dict[str, Any]]
    ) -> str:
        wamid = self._next_wamid()
        self.sent_messages.append({
            "type": "interactive_list", "to": to, "header": header,
            "body": body, "sections": sections, "wamid": wamid,
        })
        return wamid

    async def send_media(
        self,
        to:         str,
        media_type: str,
        link:       str,
        caption:    str | None = None,
        filename:   str | None = None,
    ) -> str:
        wamid = self._next_wamid()
        self.sent_messages.append({
            "type": f"media_{media_type}", "to": to, "link": link,
            "caption": caption, "filename": filename, "wamid": wamid,
        })
        return wamid

    async def get_media_url(self, media_id: str) -> str:
        if media_id not in self._media_urls:
            raise ValueError(f"MockWhatsAppProvider: unknown media_id={media_id!r}")
        return self._media_urls[media_id]

    async def download_media(self, url: str) -> tuple[bytes, str]:
        for data, mime in self._media_store.values():
            return data, mime
        return b"mock_bytes", "image/jpeg"
