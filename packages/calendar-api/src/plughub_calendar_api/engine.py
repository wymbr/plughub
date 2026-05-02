"""
engine.py
Calendar computation engine — pure logic, no I/O.

Four public functions:
  is_open(cal, holidays, at)                 → bool
  next_open_slot(cal, holidays, after)       → datetime | None
  add_business_duration(cal, holidays, from_dt, hours) → datetime
  business_duration(cal, holidays, from_dt, to_dt)     → float (hours)

A "cal" dict is the raw calendar row from db_get_associations_for_engine.
A "holidays" list is the merged holidays from all linked holiday_set_ids.

Resolution priority (highest first):
  1. exceptions  — point-in-time overrides on the calendar
  2. holidays    — from linked holiday_sets
  3. weekly_schedule — recurring weekly rules

All datetimes are handled in the calendar's timezone.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any

import pytz

logger = logging.getLogger("plughub.calendar.engine")

_DAY_NAMES = [
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
]

_MAX_LOOKAHEAD_DAYS = 365  # safety limit for next_open_slot search


# ── Internal helpers ──────────────────────────────────────────────────────────

def _tz(timezone: str) -> pytz.BaseTzInfo:
    try:
        return pytz.timezone(timezone)
    except pytz.UnknownTimeZoneError:
        logger.warning("Unknown timezone %r — falling back to UTC", timezone)
        return pytz.UTC


def _to_local(dt: datetime, tz: pytz.BaseTzInfo) -> datetime:
    if dt.tzinfo is None:
        dt = pytz.UTC.localize(dt)
    return dt.astimezone(tz)


def _parse_time(s: str) -> time:
    """Parse 'HH:MM' into time object."""
    h, m = s.split(":")
    return time(int(h), int(m))


def _in_slots(t: time, slots: list[dict]) -> bool:
    """Return True if time t falls within any of the given slots."""
    for slot in slots:
        open_t  = _parse_time(slot["open"])
        close_t = _parse_time(slot["close"])
        if open_t <= t < close_t:
            return True
    return False


def _resolve_date(
    cal: dict[str, Any],
    holidays_by_date: dict[str, dict],
    d: date,
) -> tuple[bool, list[dict]]:
    """
    Resolve open/closed status and applicable slots for a calendar date.
    Returns (is_open, slots).

    Priority:
      1. Calendar exceptions
      2. Holidays from linked sets
      3. Weekly schedule
    """
    date_str = d.strftime("%Y-%m-%d")

    # 1. Exceptions (highest priority)
    for exc in cal.get("exceptions", []):
        if exc["date"] == date_str:
            if exc["override_slots"] is None:
                return False, []
            return True, exc["override_slots"]

    # 2. Holidays
    if date_str in holidays_by_date:
        holiday = holidays_by_date[date_str]
        if holiday.get("override_slots") is None:
            return False, []
        return True, holiday["override_slots"]

    # 3. Weekly schedule
    day_name = _DAY_NAMES[d.weekday()]
    for day_sched in cal.get("weekly_schedule", []):
        if day_sched.get("day") == day_name:
            if not day_sched.get("open", True):
                return False, []
            return True, day_sched.get("slots", [{"open": "00:00", "close": "23:59"}])

    # Day not in weekly_schedule → closed
    return False, []


def _build_holidays_index(holidays: list[dict]) -> dict[str, dict]:
    """Flatten a list of holiday dicts into a date→holiday index."""
    index: dict[str, dict] = {}
    for h in holidays:
        index[h["date"]] = h
    return index


# ── Single-calendar open check ────────────────────────────────────────────────

def _calendar_is_open(cal: dict, holidays: list[dict], at: datetime) -> bool:
    tz = _tz(cal["timezone"])
    local_dt = _to_local(at, tz)
    h_index = _build_holidays_index(holidays)
    is_open, slots = _resolve_date(cal, h_index, local_dt.date())
    if not is_open:
        return False
    return _in_slots(local_dt.time(), slots)


def _calendar_next_open(
    cal: dict, holidays: list[dict], after: datetime
) -> datetime | None:
    """Find next open moment in the calendar after `after` (UTC-aware)."""
    tz = _tz(cal["timezone"])
    local_dt = _to_local(after, tz)
    h_index = _build_holidays_index(holidays)

    for day_offset in range(_MAX_LOOKAHEAD_DAYS):
        check_date = local_dt.date() + timedelta(days=day_offset)
        is_open, slots = _resolve_date(cal, h_index, check_date)
        if not is_open or not slots:
            continue
        for slot in slots:
            open_t  = _parse_time(slot["open"])
            close_t = _parse_time(slot["close"])
            if day_offset == 0 and local_dt.time() >= close_t:
                continue  # already past this slot today
            candidate_time = open_t if (day_offset > 0 or local_dt.time() <= open_t) else local_dt.time()
            candidate = tz.localize(datetime.combine(check_date, candidate_time))
            return candidate.astimezone(pytz.UTC).replace(tzinfo=pytz.UTC)

    return None  # no open slot found within lookahead


# ── Aggregation over multiple calendars ───────────────────────────────────────

def _aggregate_is_open(
    associations: list[dict],
    holidays_by_cal: dict[str, list[dict]],
    at: datetime,
) -> bool:
    """
    Evaluate all calendar associations for an entity.
    UNION group → OR; then INTERSECTION items → AND.
    """
    if not associations:
        return False

    union_results = []
    intersection_results = []

    for assoc in associations:
        cal_id   = assoc["calendar_id"]
        holidays = holidays_by_cal.get(cal_id, [])
        result   = _calendar_is_open(assoc, holidays, at)
        if assoc["operator"] == "UNION":
            union_results.append(result)
        else:
            intersection_results.append(result)

    # Evaluate
    union_open = any(union_results) if union_results else True
    intersect_open = all(intersection_results) if intersection_results else True
    return union_open and intersect_open


# ── Public API ────────────────────────────────────────────────────────────────

def is_open(
    associations: list[dict],
    holidays_by_cal: dict[str, list[dict]],
    at: datetime | None = None,
) -> bool:
    """
    Return True if the entity is open at the given moment (default: now UTC).

    associations    — list of dicts from db_get_associations_for_engine
    holidays_by_cal — {calendar_id: [holiday, ...]} from db_get_holidays_for_sets
    at              — UTC datetime (default: datetime.utcnow())
    """
    if at is None:
        at = datetime.utcnow().replace(tzinfo=pytz.UTC)
    return _aggregate_is_open(associations, holidays_by_cal, at)


def next_open_slot(
    associations: list[dict],
    holidays_by_cal: dict[str, list[dict]],
    after: datetime | None = None,
) -> datetime | None:
    """
    Return the next moment (UTC) when the entity will be open.
    Returns None if no open slot found within _MAX_LOOKAHEAD_DAYS.
    """
    if after is None:
        after = datetime.utcnow().replace(tzinfo=pytz.UTC)

    # Separate UNION and INTERSECTION associations
    union_assocs = [a for a in associations if a["operator"] == "UNION"]
    inter_assocs = [a for a in associations if a["operator"] == "INTERSECTION"]

    # Find earliest open across all UNION calendars
    candidates = []
    for assoc in union_assocs:
        cal_id   = assoc["calendar_id"]
        holidays = holidays_by_cal.get(cal_id, [])
        slot     = _calendar_next_open(assoc, holidays, after)
        if slot is not None:
            candidates.append(slot)

    if not candidates:
        return None

    earliest = min(candidates)

    # Verify INTERSECTION constraints at that moment
    if inter_assocs:
        # Walk forward from `earliest` until all INTERSECTION cals are also open
        for _ in range(_MAX_LOOKAHEAD_DAYS * 24 * 60):  # minute-by-minute
            check_dt = earliest
            inter_ok = all(
                _calendar_is_open(a, holidays_by_cal.get(a["calendar_id"], []), check_dt)
                for a in inter_assocs
            )
            if inter_ok:
                return check_dt
            # Advance to next open slot across union
            new_candidates = []
            for assoc in union_assocs:
                slot = _calendar_next_open(assoc, holidays_by_cal.get(assoc["calendar_id"], []), check_dt + timedelta(minutes=1))
                if slot:
                    new_candidates.append(slot)
            if not new_candidates:
                return None
            earliest = min(new_candidates)

    return earliest


def add_business_duration(
    associations: list[dict],
    holidays_by_cal: dict[str, list[dict]],
    from_dt: datetime,
    hours: float,
) -> datetime:
    """
    Return the datetime that is `hours` business hours after `from_dt`.
    Advances through open windows, skipping closed periods.

    Uses only the first UNION calendar for business-hour calculations
    (the primary schedule). INTERSECTION calendars are respected as hard
    boundaries — closed INTERSECTION windows do not count as business time.
    """
    if not associations:
        # No calendar — fall back to wall-clock hours
        return from_dt + timedelta(hours=hours)

    remaining = timedelta(hours=hours)
    current   = from_dt if from_dt.tzinfo else pytz.UTC.localize(from_dt)

    # Use the lowest-priority UNION calendar as the primary schedule
    primary = next((a for a in associations if a["operator"] == "UNION"), associations[0])
    tz = _tz(primary["timezone"])
    h_index = _build_holidays_index(
        holidays_by_cal.get(primary["calendar_id"], [])
    )

    max_iterations = _MAX_LOOKAHEAD_DAYS * 24 * 60  # minute granularity
    for _ in range(max_iterations):
        if remaining <= timedelta(0):
            break

        local = _to_local(current, tz)
        is_open_now, slots = _resolve_date(primary, h_index, local.date())

        if not is_open_now or not _in_slots(local.time(), slots):
            # Not in a business window — jump to next open slot
            nxt = _calendar_next_open(primary, holidays_by_cal.get(primary["calendar_id"], []), current)
            if nxt is None:
                break  # no more open slots
            current = nxt
            continue

        # Find how much time remains in the current slot
        for slot in slots:
            open_t  = _parse_time(slot["open"])
            close_t = _parse_time(slot["close"])
            if not (open_t <= local.time() < close_t):
                continue
            slot_end = tz.localize(datetime.combine(local.date(), close_t))
            time_in_slot = slot_end - current
            if time_in_slot >= remaining:
                current = current + remaining
                remaining = timedelta(0)
            else:
                remaining -= time_in_slot
                current = slot_end
            break
        else:
            # Current time not in any slot (shouldn't happen) — advance 1 min
            current += timedelta(minutes=1)

    return current


def business_duration(
    associations: list[dict],
    holidays_by_cal: dict[str, list[dict]],
    from_dt: datetime,
    to_dt: datetime,
) -> float:
    """
    Return the number of business hours between from_dt and to_dt.
    Counts only time within open windows of the primary UNION calendar.
    """
    if not associations or from_dt >= to_dt:
        return 0.0

    primary = next((a for a in associations if a["operator"] == "UNION"), associations[0])
    tz = _tz(primary["timezone"])
    h_index = _build_holidays_index(
        holidays_by_cal.get(primary["calendar_id"], [])
    )

    current = from_dt if from_dt.tzinfo else pytz.UTC.localize(from_dt)
    end     = to_dt   if to_dt.tzinfo   else pytz.UTC.localize(to_dt)
    total   = timedelta(0)
    step    = timedelta(minutes=1)

    while current < end:
        local = _to_local(current, tz)
        is_open_now, slots = _resolve_date(primary, h_index, local.date())
        if is_open_now and _in_slots(local.time(), slots):
            advance = min(step, end - current)
            total  += advance
        current += step

    return total.total_seconds() / 3600
