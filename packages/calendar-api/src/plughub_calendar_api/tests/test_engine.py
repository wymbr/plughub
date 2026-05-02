"""
test_engine.py
Unit tests for the calendar engine — pure logic, no I/O.

Coverage:
  - is_open: weekly schedule, holidays, exceptions, UNION, INTERSECTION
  - next_open_slot: finds next window, handles closed days, wraps to next week
  - add_business_duration: calculates deadline in business hours
  - business_duration: counts open hours between two datetimes
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytz
import pytest

from plughub_calendar_api.engine import (
    add_business_duration,
    business_duration,
    is_open,
    next_open_slot,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

SAO_PAULO = "America/Sao_Paulo"
UTC = pytz.UTC

def dt(year, month, day, hour=0, minute=0, tz=SAO_PAULO) -> datetime:
    """Create a timezone-aware datetime in the given tz."""
    local = pytz.timezone(tz).localize(datetime(year, month, day, hour, minute))
    return local.astimezone(UTC)


def make_cal(
    timezone=SAO_PAULO,
    schedule=None,
    exceptions=None,
    holiday_set_ids=None,
) -> dict:
    """Create a minimal calendar dict for engine tests."""
    if schedule is None:
        # Mon–Fri 08:00–18:00
        schedule = [
            {"day": day, "open": True, "slots": [{"open": "08:00", "close": "18:00"}]}
            for day in ["monday", "tuesday", "wednesday", "thursday", "friday"]
        ] + [
            {"day": "saturday", "open": False, "slots": []},
            {"day": "sunday",   "open": False, "slots": []},
        ]
    return {
        "calendar_id":    "cal-001",
        "operator":       "UNION",
        "priority":       1,
        "timezone":       timezone,
        "weekly_schedule": schedule,
        "holiday_set_ids": holiday_set_ids or [],
        "exceptions":     exceptions or [],
    }


def assoc(cal: dict) -> list[dict]:
    return [cal]


def no_holidays() -> dict:
    return {}


# ── is_open ───────────────────────────────────────────────────────────────────

class TestIsOpen:
    def test_open_during_business_hours(self):
        # Monday 10:00 SP → should be open
        cal = make_cal()
        assert is_open(assoc(cal), no_holidays(), dt(2026, 4, 27, 10, 0)) is True

    def test_closed_before_opening(self):
        cal = make_cal()
        assert is_open(assoc(cal), no_holidays(), dt(2026, 4, 27, 7, 59)) is False

    def test_closed_after_closing(self):
        cal = make_cal()
        assert is_open(assoc(cal), no_holidays(), dt(2026, 4, 27, 18, 0)) is False

    def test_closed_on_weekend(self):
        cal = make_cal()
        # Saturday
        assert is_open(assoc(cal), no_holidays(), dt(2026, 4, 25, 10, 0)) is False

    def test_closed_on_holiday(self):
        cal = make_cal(holiday_set_ids=["hs-001"])
        holidays = {
            "cal-001": [{"date": "2026-04-27", "name": "Feriado", "override_slots": None}]
        }
        # Monday (normally open) but it's a holiday
        assert is_open(assoc(cal), holidays, dt(2026, 4, 27, 10, 0)) is False

    def test_holiday_with_override_slots(self):
        cal = make_cal(holiday_set_ids=["hs-001"])
        holidays = {
            "cal-001": [{"date": "2026-04-27", "name": "Meio expediente",
                         "override_slots": [{"open": "08:00", "close": "12:00"}]}]
        }
        # Monday with override — open in the morning
        assert is_open(assoc(cal), holidays, dt(2026, 4, 27, 9, 0)) is True
        assert is_open(assoc(cal), holidays, dt(2026, 4, 27, 13, 0)) is False

    def test_exception_closes_day(self):
        cal = make_cal(exceptions=[{
            "date": "2026-04-27",
            "reason": "Treinamento",
            "override_slots": None,
        }])
        assert is_open(assoc(cal), no_holidays(), dt(2026, 4, 27, 10, 0)) is False

    def test_exception_overrides_schedule(self):
        cal = make_cal(exceptions=[{
            "date": "2026-04-25",  # Saturday — normally closed
            "reason": "Plantão",
            "override_slots": [{"open": "09:00", "close": "13:00"}],
        }])
        assert is_open(assoc(cal), no_holidays(), dt(2026, 4, 25, 10, 0)) is True

    def test_exception_takes_priority_over_holiday(self):
        # Exception says open, holiday says closed — exception wins
        cal = make_cal(
            holiday_set_ids=["hs-001"],
            exceptions=[{
                "date": "2026-04-27",
                "reason": "Plantão emergencial",
                "override_slots": [{"open": "08:00", "close": "18:00"}],
            }]
        )
        holidays = {
            "cal-001": [{"date": "2026-04-27", "name": "Feriado", "override_slots": None}]
        }
        assert is_open(assoc(cal), holidays, dt(2026, 4, 27, 10, 0)) is True

    def test_no_associations_returns_false(self):
        assert is_open([], no_holidays()) is False


class TestUnionIntersection:
    def _cal_24x7(self) -> dict:
        return {
            "calendar_id": "cal-24x7",
            "operator":    "UNION",
            "priority":    2,
            "timezone":    SAO_PAULO,
            "weekly_schedule": [
                {"day": d, "open": True, "slots": [{"open": "00:00", "close": "23:59"}]}
                for d in ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
            ],
            "holiday_set_ids": [],
            "exceptions":      [],
        }

    def test_union_open_if_any_open(self):
        commercial = make_cal()  # Mon–Fri 08–18
        weekend    = {**self._cal_24x7(), "operator": "UNION"}
        # Saturday 10:00 — commercial closed, 24x7 open → UNION = open
        associations = [commercial, weekend]
        assert is_open(associations, no_holidays(), dt(2026, 4, 25, 10, 0)) is True

    def test_intersection_requires_all_open(self):
        commercial = make_cal()  # Mon–Fri 08–18
        regulatory = {**make_cal(), "calendar_id": "cal-reg", "operator": "INTERSECTION"}
        # regulatory closes at 17:00 on Fridays
        regulatory["weekly_schedule"] = [
            {"day": "friday", "open": True, "slots": [{"open": "08:00", "close": "17:00"}]},
        ]
        associations = [commercial, regulatory]
        # Friday 16:00 — both open
        assert is_open(associations, no_holidays(), dt(2026, 5, 1, 16, 0)) is True
        # Friday 17:30 — commercial open but regulatory closed → INTERSECTION = closed
        assert is_open(associations, no_holidays(), dt(2026, 5, 1, 17, 30)) is False


# ── next_open_slot ────────────────────────────────────────────────────────────

class TestNextOpenSlot:
    def test_returns_start_of_next_slot_if_closed(self):
        cal = make_cal()
        # Saturday 15:00 → next open is Monday 08:00
        after = dt(2026, 4, 25, 15, 0)
        nxt = next_open_slot(assoc(cal), no_holidays(), after)
        assert nxt is not None
        local = nxt.astimezone(pytz.timezone(SAO_PAULO))
        assert local.strftime("%A %H:%M") == "Monday 08:00"

    def test_returns_current_time_if_already_open(self):
        cal = make_cal()
        # Monday 10:30 — already open
        after = dt(2026, 4, 27, 10, 30)
        nxt = next_open_slot(assoc(cal), no_holidays(), after)
        assert nxt is not None
        assert abs((nxt - after).total_seconds()) < 120  # within 2 min

    def test_skips_holiday(self):
        cal = make_cal(holiday_set_ids=["hs-001"])
        holidays = {
            "cal-001": [{"date": "2026-04-27", "name": "Feriado", "override_slots": None}]
        }
        # Sunday 20:00 → next open skips Monday (holiday) → Tuesday 08:00
        after = dt(2026, 4, 26, 20, 0)
        nxt = next_open_slot(assoc(cal), holidays, after)
        assert nxt is not None
        local = nxt.astimezone(pytz.timezone(SAO_PAULO))
        assert local.strftime("%A %H:%M") == "Tuesday 08:00"


# ── add_business_duration ─────────────────────────────────────────────────────

class TestAddBusinessDuration:
    def test_simple_within_same_day(self):
        cal = make_cal()
        # Monday 09:00 + 2 business hours = Monday 11:00
        start = dt(2026, 4, 27, 9, 0)
        end   = add_business_duration(assoc(cal), no_holidays(), start, 2.0)
        local = end.astimezone(pytz.timezone(SAO_PAULO))
        assert local.hour == 11
        assert local.minute == 0

    def test_spans_end_of_day(self):
        cal = make_cal()
        # Monday 17:00 + 2 business hours → crosses 18:00 → continues Tuesday 08:00
        start = dt(2026, 4, 27, 17, 0)
        end   = add_business_duration(assoc(cal), no_holidays(), start, 2.0)
        local = end.astimezone(pytz.timezone(SAO_PAULO))
        assert local.strftime("%A %H:%M") == "Tuesday 09:00"

    def test_spans_weekend(self):
        cal = make_cal()
        # Friday 17:00 + 2 business hours → Mon 09:00
        start = dt(2026, 5, 1, 17, 0)
        end   = add_business_duration(assoc(cal), no_holidays(), start, 2.0)
        local = end.astimezone(pytz.timezone(SAO_PAULO))
        assert local.strftime("%A %H:%M") == "Monday 09:00"

    def test_spans_holiday(self):
        cal = make_cal(holiday_set_ids=["hs-001"])
        holidays = {
            "cal-001": [{"date": "2026-04-27", "name": "Feriado", "override_slots": None}]
        }
        # Sunday 20:00 + 1 business hour → skips Monday (holiday) → Tuesday 09:00
        start = dt(2026, 4, 26, 20, 0)
        end   = add_business_duration(assoc(cal), holidays, start, 1.0)
        local = end.astimezone(pytz.timezone(SAO_PAULO))
        assert local.strftime("%A %H:%M") == "Tuesday 09:00"

    def test_no_calendar_falls_back_to_wall_clock(self):
        start = datetime(2026, 4, 27, 9, 0, tzinfo=UTC)
        end   = add_business_duration([], {}, start, 2.0)
        assert (end - start) == timedelta(hours=2)


# ── business_duration ─────────────────────────────────────────────────────────

class TestBusinessDuration:
    def test_same_day(self):
        cal   = make_cal()
        start = dt(2026, 4, 27, 9, 0)
        end   = dt(2026, 4, 27, 11, 0)
        hours = business_duration(assoc(cal), no_holidays(), start, end)
        assert abs(hours - 2.0) < 0.05  # ~2h

    def test_excludes_lunch_break_if_schedule(self):
        cal = make_cal(schedule=[
            {"day": "monday", "open": True, "slots": [
                {"open": "08:00", "close": "12:00"},
                {"open": "13:00", "close": "18:00"},
            ]}
        ])
        start = dt(2026, 4, 27, 8, 0)
        end   = dt(2026, 4, 27, 14, 0)
        # 4h morning + 1h afternoon = 5h
        hours = business_duration(assoc(cal), no_holidays(), start, end)
        assert abs(hours - 5.0) < 0.1

    def test_excludes_weekend(self):
        cal   = make_cal()
        start = dt(2026, 4, 24, 17, 0)  # Friday 17:00
        end   = dt(2026, 4, 27, 9, 0)   # Monday 09:00
        # Friday: 1h (17:00–18:00), Mon: 1h (08:00–09:00) = 2h
        hours = business_duration(assoc(cal), no_holidays(), start, end)
        assert abs(hours - 2.0) < 0.1

    def test_from_after_to_returns_zero(self):
        cal   = make_cal()
        start = dt(2026, 4, 27, 11, 0)
        end   = dt(2026, 4, 27, 9, 0)
        assert business_duration(assoc(cal), no_holidays(), start, end) == 0.0

    def test_no_calendar_returns_zero(self):
        start = datetime(2026, 4, 27, 9, 0, tzinfo=UTC)
        end   = datetime(2026, 4, 27, 11, 0, tzinfo=UTC)
        assert business_duration([], {}, start, end) == 0.0
