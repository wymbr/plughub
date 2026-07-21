"""
calendar_client.py
Thin async client for the calendar-api by-calendar_id engine endpoints (Fase 3a —
contact window gate). The outbound engine never re-implements "when/open" — it asks
calendar-api (ADR invariant, same as scheduler-api).

Degradation is graceful and LOUD: on any failure `is_open_status` returns None and
logs the reason; the eligibility engine then treats the window as OPEN (fireable)
rather than silently blocking a contact — a missed calendar check must not silently
drop a send (symmetric to the scheduler's "degrade to fireable").
"""
from __future__ import annotations

import logging
from datetime import datetime

import httpx

logger = logging.getLogger("plughub.mailing.calendar")


class CalendarClient:
    def __init__(self, base_url: str, timeout_s: float = 8.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    async def is_open_status(self, calendar_id: str, at: datetime) -> str | None:
        """Returns "open" | "closed" | "holiday", or None on error (degrade → open)."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                r = await client.get(
                    f"{self.base_url}/v1/engine/is-open-calendar",
                    params={"calendar_id": calendar_id, "at": at.isoformat()},
                )
                r.raise_for_status()
                return r.json().get("status")
        except Exception as exc:
            logger.warning(
                "calendar is-open failed (calendar=%s) — degrading to OPEN: %s",
                calendar_id, exc,
            )
            return None

    async def next_open_slot(self, calendar_id: str, after: datetime) -> datetime | None:
        """Returns the next open instant (UTC) at/after `after`, or None (no slot / error)."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                r = await client.get(
                    f"{self.base_url}/v1/engine/next-open-slot-calendar",
                    params={"calendar_id": calendar_id, "after": after.isoformat()},
                )
                r.raise_for_status()
                raw = r.json().get("next_open")
                return datetime.fromisoformat(raw) if raw else None
        except Exception as exc:
            logger.warning(
                "calendar next-open-slot failed (calendar=%s) — degrading: %s",
                calendar_id, exc,
            )
            return None
