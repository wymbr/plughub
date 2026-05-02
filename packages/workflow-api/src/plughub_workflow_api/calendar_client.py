"""
calendar_client.py
HTTP client to call the Calendar API for business-hours deadline calculation.

Used by the persist-suspend endpoint when business_hours=True on the suspend step:
  POST {calendar_api_url}/v1/engine/add-business-duration

If the Calendar API is unreachable or no association exists, falls back to
wall-clock hours so the workflow is never blocked by calendar unavailability.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger("plughub.workflow.calendar_client")

_TIMEOUT_S = 5.0


async def calculate_deadline(
    calendar_api_url: str,
    tenant_id:        str,
    entity_type:      str,
    entity_id:        str,
    from_dt:          datetime,
    hours:            float,
) -> datetime:
    """
    Call calendar-api to calculate the business-hours deadline.

    Falls back to wall-clock if:
      - calendar-api is unreachable
      - no calendar is associated to the entity
      - entity_id is None/empty
    """
    if not entity_id:
        return _wall_clock(from_dt, hours)

    payload = {
        "tenant_id":   tenant_id,
        "entity_type": entity_type,
        "entity_id":   entity_id,
        "from_dt":     from_dt.isoformat(),
        "hours":       hours,
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(
                f"{calendar_api_url}/v1/engine/add-business-duration",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return datetime.fromisoformat(data["deadline"])

    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            # No calendar association — fall back to wall-clock
            logger.debug(
                "No calendar association for entity %s/%s — using wall-clock",
                entity_type, entity_id,
            )
        else:
            logger.warning(
                "Calendar API error %s for entity %s/%s — using wall-clock: %s",
                exc.response.status_code, entity_type, entity_id, exc,
            )
        return _wall_clock(from_dt, hours)

    except Exception as exc:
        logger.warning(
            "Calendar API unreachable — using wall-clock deadline: %s", exc
        )
        return _wall_clock(from_dt, hours)


def _wall_clock(from_dt: datetime, hours: float) -> datetime:
    """Fallback: add hours as wall-clock time."""
    delta_s = hours * 3600
    return from_dt.replace(tzinfo=timezone.utc) + __import__("datetime").timedelta(seconds=delta_s)
