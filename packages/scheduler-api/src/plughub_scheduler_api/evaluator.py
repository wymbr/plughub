"""
evaluator.py
Recurrence evaluator — computes the NEXT fire instant (aware UTC) for an agenda,
or None when exhausted (past ends_at / no match within the lookahead cap).

Design:
  - Only the NEXT occurrence is computed (bounded state); called on create and after
    each fire to re-arm.
  - Fire set of a recurring rule = (matching days) × (times). Times are INSTANTS in
    the agenda's timezone.
  - business_day_policy is evaluated at the DAY level, KEEPING the time-of-day
    (spec: "a shift moves the whole day"), probing calendar-api's is_open_status:
      ignore              → wall-clock, no calendar consulted
      only_business_days  → skip the day if the calendar isn't open at the fire instant
      shift_next/previous → move by whole days (same time) to the nearest open day
  - Degradation is loud: if the calendar check errors (client returns None), the day
    is treated as fireable (a missed check must not silently drop a fire).
"""
from __future__ import annotations

import calendar as _cal
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger("plughub.scheduler.evaluator")

_WEEKDAY_IDX = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

_MAX_LOOKAHEAD_DAYS = 366 * 3   # cap to avoid an unbounded search on a never-matching rule
_MAX_SHIFT_DAYS = 90            # cap for shift_next/previous open-day search


def _tz(name: str) -> ZoneInfo | timezone:
    try:
        return ZoneInfo(name)
    except Exception:
        logger.warning("unknown timezone %r — falling back to UTC", name)
        return timezone.utc


def _parse_utc(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _hhmm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def _local_to_utc(d: date, hh: int, mm: int, tz) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz).astimezone(timezone.utc)


def _nth_weekday(year: int, month: int, wd: int, nth) -> date | None:
    days_in_month = _cal.monthrange(year, month)[1]
    matches = [day for day in range(1, days_in_month + 1)
               if date(year, month, day).weekday() == wd]
    if not matches:
        return None
    if nth == "last":
        return date(year, month, matches[-1])
    idx = int(nth) - 1
    return date(year, month, matches[idx]) if 0 <= idx < len(matches) else None


def _date_matches(freq, d, anchor, interval, weekdays_idx, month_by, month_overflow) -> bool:
    if freq == "daily":
        delta = (d - anchor).days
        return delta >= 0 and delta % interval == 0

    if freq == "weekly":
        if d.weekday() not in weekdays_idx:
            return False
        anchor_monday = anchor - timedelta(days=anchor.weekday())
        d_monday = d - timedelta(days=d.weekday())
        week_delta = (d_monday - anchor_monday).days // 7
        return week_delta >= 0 and week_delta % interval == 0

    if freq == "monthly":
        month_delta = (d.year - anchor.year) * 12 + (d.month - anchor.month)
        if month_delta < 0 or month_delta % interval != 0:
            return False
        if not month_by:
            return d.day == anchor.day
        days_in_month = _cal.monthrange(d.year, d.month)[1]
        if month_by.get("kind") == "by_date":
            for spec in month_by.get("days", []):
                if spec == "last":
                    if d.day == days_in_month:
                        return True
                else:
                    n = int(spec)
                    if n > days_in_month:
                        if month_overflow == "clamp" and d.day == days_in_month:
                            return True
                    elif d.day == n:
                        return True
            return False
        # by_position
        target = _nth_weekday(d.year, d.month, _WEEKDAY_IDX[month_by["weekday"]], month_by["nth"])
        return target is not None and d.day == target.day

    return False


async def _shift_to_open_day(client, calendar_id, d, hh, mm, tz, direction) -> datetime | None:
    """Move by whole days (same time-of-day) until the calendar is open. Degrades to
    fireable when the check errors (client returns None)."""
    for i in range(_MAX_SHIFT_DAYS):
        dd = d + timedelta(days=direction * i)
        utc = _local_to_utc(dd, hh, mm, tz)
        st = await client.is_open_status(calendar_id, utc)
        if st is None or st == "open":
            return utc
    return None


async def compute_next_fire(agenda: dict, after_dt: datetime, calendar_client=None) -> datetime | None:
    """Next fire instant (aware UTC) strictly after `after_dt`, or None if exhausted."""
    schedule = agenda.get("schedule") or {}

    if schedule.get("mode") == "once":
        fire = _parse_utc(schedule["fire_at"])
        return fire if fire > after_dt else None

    rule = schedule.get("rule") or {}
    validity = agenda.get("validity") or {}
    tz = _tz(agenda.get("timezone") or "UTC")
    starts_at = _parse_utc(validity["starts_at"])
    ends_at = _parse_utc(validity["ends_at"]) if validity.get("ends_at") else None

    lower = max(after_dt, starts_at - timedelta(seconds=1))
    anchor_date = starts_at.astimezone(tz).date()
    times = sorted(set(rule.get("times", [])))
    weekdays_idx = {_WEEKDAY_IDX[w] for w in (rule.get("weekdays") or [])}
    interval = int(rule.get("interval", 1) or 1)
    freq = rule.get("frequency")
    bdp = rule.get("business_day_policy", "ignore")
    month_by = rule.get("month_by")
    month_overflow = rule.get("month_overflow", "clamp")
    use_calendar = bdp != "ignore" and bool(agenda.get("calendar_id")) and calendar_client is not None
    calendar_id = agenda.get("calendar_id")

    start_date = lower.astimezone(tz).date()
    for offset in range(_MAX_LOOKAHEAD_DAYS):
        d = start_date + timedelta(days=offset)
        if not _date_matches(freq, d, anchor_date, interval, weekdays_idx, month_by, month_overflow):
            continue
        for tstr in times:
            hh, mm = _hhmm(tstr)
            fire_utc = _local_to_utc(d, hh, mm, tz)
            if use_calendar:
                if bdp == "only_business_days":
                    st = await calendar_client.is_open_status(calendar_id, fire_utc)
                    if st is not None and st != "open":
                        continue
                elif bdp in ("shift_next", "shift_previous"):
                    direction = 1 if bdp == "shift_next" else -1
                    shifted = await _shift_to_open_day(
                        calendar_client, calendar_id, d, hh, mm, tz, direction
                    )
                    if shifted is None:
                        continue
                    fire_utc = shifted
            if fire_utc <= lower:
                continue
            if ends_at and fire_utc > ends_at:
                return None
            return fire_utc
    return None
