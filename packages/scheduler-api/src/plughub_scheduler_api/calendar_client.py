"""
calendar_client.py
Thin async client for the calendar-api's by-calendar_id engine endpoints.

The scheduler never re-implements the "when/open" logic (ADR invariant) — it asks
calendar-api. Degradation is graceful and LOUD: on any failure the caller is told
(None returned) and logs the reason; the evaluator then treats the day as fireable
rather than silently skipping (a missed calendar check must not silently drop a fire).
"""
from __future__ import annotations

import logging
from datetime import datetime

import httpx

logger = logging.getLogger("plughub.scheduler.calendar")


class CalendarClient:
    def __init__(self, base_url: str, timeout_s: float = 10.0, service_token: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        # AUT-63 — a calendar-api exige credencial também no motor. Vazio ⇒ 401, e cada
        # consulta degrada pelo caminho (com WARNING) que já existia.
        self.headers = {"X-Service-Token": service_token} if service_token else {}

    async def is_open_status(self, calendar_id: str, at: datetime) -> str | None:
        """Returns "open" | "closed" | "holiday", or None on error (degrade)."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                r = await client.get(
                    f"{self.base_url}/v1/engine/is-open-calendar",
                    params={"calendar_id": calendar_id, "at": at.isoformat()},
                    headers=self.headers,
                )
                r.raise_for_status()
                return r.json().get("status")
        except Exception as exc:
            logger.warning(
                "calendar is-open failed (calendar=%s) — degrading to fireable: %s",
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
                    headers=self.headers,
                )
                r.raise_for_status()
                raw = r.json().get("next_open")
                if not raw:
                    return None
                dt = datetime.fromisoformat(raw)
                return dt
        except Exception as exc:
            logger.warning(
                "calendar next-open-slot failed (calendar=%s) — degrading: %s",
                calendar_id, exc,
            )
            return None
