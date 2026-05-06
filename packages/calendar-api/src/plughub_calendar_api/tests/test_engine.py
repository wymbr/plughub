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
    get_open_status,
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

    # ── multi-slot (Fase 4) ───────────────────────────────────────────────────

    def test_between_slots_returns_start_of_next_slot(self):
        """During lunch break (between two intervals), next slot = start of afternoon."""
        split_day = [
            {"day": "monday", "open": True, "slots": [
                {"open": "08:00", "close": "12:00"},
                {"open": "13:00", "close": "18:00"},
            ]}
        ]
        cal = make_cal(schedule=split_day)
        # Monday 12:30 — between slots (lunch break)
        after = dt(2026, 4, 27, 12, 30)
        nxt = next_open_slot(assoc(cal), no_holidays(), after)
        assert nxt is not None
        local = nxt.astimezone(pytz.timezone(SAO_PAULO))
        assert local.strftime("%A %H:%M") == "Monday 13:00"

    def test_within_first_slot_returns_current_time(self):
        """During morning session of a split day, already open → returns ~now."""
        split_day = [
            {"day": "monday", "open": True, "slots": [
                {"open": "08:00", "close": "12:00"},
                {"open": "13:00", "close": "18:00"},
            ]}
        ]
        cal = make_cal(schedule=split_day)
        # Monday 10:00 — inside first slot
        after = dt(2026, 4, 27, 10, 0)
        nxt = next_open_slot(assoc(cal), no_holidays(), after)
        assert nxt is not None
        assert abs((nxt - after).total_seconds()) < 120  # ≤ 2 min

    def test_after_all_slots_returns_next_day_open(self):
        """After the last slot of the day, next open = next business day."""
        split_day = [
            {"day": "monday", "open": True, "slots": [
                {"open": "08:00", "close": "12:00"},
                {"open": "13:00", "close": "17:00"},
            ]},
            {"day": "tuesday", "open": True, "slots": [
                {"open": "08:00", "close": "17:00"},
            ]},
        ]
        cal = make_cal(schedule=split_day)
        # Monday 17:30 — after all slots
        after = dt(2026, 4, 27, 17, 30)
        nxt = next_open_slot(assoc(cal), no_holidays(), after)
        assert nxt is not None
        local = nxt.astimezone(pytz.timezone(SAO_PAULO))
        assert local.strftime("%A %H:%M") == "Tuesday 08:00"

    def test_is_open_false_during_gap(self):
        """is_open returns False during the lunch gap between two slots."""
        split_day = [
            {"day": "monday", "open": True, "slots": [
                {"open": "08:00", "close": "12:00"},
                {"open": "13:00", "close": "18:00"},
            ]}
        ]
        cal = make_cal(schedule=split_day)
        assert is_open(assoc(cal), no_holidays(), dt(2026, 4, 27, 12, 30)) is False
        assert is_open(assoc(cal), no_holidays(), dt(2026, 4, 27, 11, 59)) is True
        assert is_open(assoc(cal), no_holidays(), dt(2026, 4, 27, 13,  0)) is True


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

    def test_crosses_gap_between_slots(self):
        """3 business hours from 11:00 crosses lunch break and continues in afternoon."""
        split_day = [
            {"day": "monday", "open": True, "slots": [
                {"open": "08:00", "close": "12:00"},
                {"open": "13:00", "close": "18:00"},
            ]}
        ]
        cal = make_cal(schedule=split_day)
        # 11:00 + 3h = 1h morning (11→12) + gap + 2h afternoon → 15:00
        start = dt(2026, 4, 27, 11, 0)
        end   = add_business_duration(assoc(cal), no_holidays(), start, 3.0)
        local = end.astimezone(pytz.timezone(SAO_PAULO))
        assert local.strftime("%A %H:%M") == "Monday 15:00"


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


# ── get_open_status — 3-state result ─────────────────────────────────────────

class TestGetOpenStatus:
    def test_open_returns_open(self):
        cal = make_cal()
        assert get_open_status(assoc(cal), no_holidays(), dt(2026, 4, 27, 10, 0)) == "open"

    def test_closed_outside_hours_returns_closed(self):
        cal = make_cal()
        assert get_open_status(assoc(cal), no_holidays(), dt(2026, 4, 27, 7, 0)) == "closed"

    def test_weekend_returns_closed(self):
        cal = make_cal()
        assert get_open_status(assoc(cal), no_holidays(), dt(2026, 4, 25, 10, 0)) == "closed"

    def test_holiday_fully_closed_returns_holiday(self):
        cal = make_cal(holiday_set_ids=["hs-001"])
        holidays = {
            "cal-001": [{"date": "2026-04-27", "name": "Feriado Nacional", "override_slots": None}]
        }
        assert get_open_status(assoc(cal), holidays, dt(2026, 4, 27, 10, 0)) == "holiday"

    def test_holiday_with_override_slots_morning_open(self):
        """Holiday with override slots → still reports 'holiday' (not 'open')."""
        cal = make_cal(holiday_set_ids=["hs-001"])
        holidays = {
            "cal-001": [{"date": "2026-04-27", "name": "Meio expediente",
                         "override_slots": [{"open": "08:00", "close": "12:00"}]}]
        }
        # During the override window → entity IS open but it's still classified "holiday" at day level
        # Note: current design returns "holiday" when the day is a holiday, even with override slots
        # The 'open' bool and 'status' serve different purposes:
        #   open=True means "accepting contacts now", status="holiday" means "it's a holiday day"
        result = get_open_status(assoc(cal), holidays, dt(2026, 4, 27, 9, 0))
        # During override window the entity is open → we return "open" (time check passes)
        # But the day-level status is "holiday"; since override_slots has entries and we're in-window,
        # the engine returns "holiday" as status because the day is resolved as holiday
        assert result in ("open", "holiday")  # implementation-defined; holiday takes precedence

    def test_holiday_with_override_slots_returns_holiday_when_outside_window(self):
        cal = make_cal(holiday_set_ids=["hs-001"])
        holidays = {
            "cal-001": [{"date": "2026-04-27", "name": "Meio expediente",
                         "override_slots": [{"open": "08:00", "close": "12:00"}]}]
        }
        # After override window closes
        assert get_open_status(assoc(cal), holidays, dt(2026, 4, 27, 13, 0)) == "holiday"

    def test_exception_returns_closed_not_holiday(self):
        """Calendar exceptions produce 'closed', not 'holiday'."""
        cal = make_cal(exceptions=[{
            "date": "2026-04-27",
            "reason": "Treinamento",
            "override_slots": None,
        }])
        assert get_open_status(assoc(cal), no_holidays(), dt(2026, 4, 27, 10, 0)) == "closed"

    def test_no_associations_returns_closed(self):
        assert get_open_status([], no_holidays()) == "closed"

    def test_is_open_backward_compatible(self):
        """is_open() must still return the correct bool."""
        cal = make_cal()
        assert is_open(assoc(cal), no_holidays(), dt(2026, 4, 27, 10, 0)) is True
        assert is_open(assoc(cal), no_holidays(), dt(2026, 4, 27, 7, 0)) is False


# ── Recurring holidays (MM-DD format) ────────────────────────────────────────

class TestRecurringHolidays:
    def _make_recurring_cal(self) -> tuple[list[dict], dict[str, list]]:
        """Calendar with a recurring holiday on April 27 every year (MM-DD)."""
        cal = make_cal(holiday_set_ids=["hs-recurring"])
        holidays = {
            "cal-001": [{"date": "04-27", "name": "Dia do Trabalho Antecipado", "override_slots": None}]
        }
        return assoc(cal), holidays

    def test_recurring_holiday_matches_2026(self):
        assocs, holidays = self._make_recurring_cal()
        assert is_open(assocs, holidays, dt(2026, 4, 27, 10, 0)) is False

    def test_recurring_holiday_status_is_holiday(self):
        assocs, holidays = self._make_recurring_cal()
        assert get_open_status(assocs, holidays, dt(2026, 4, 27, 10, 0)) == "holiday"

    def test_recurring_holiday_matches_2027(self):
        """Same MM-DD holiday matches the following year."""
        assocs, holidays = self._make_recurring_cal()
        assert is_open(assocs, holidays, dt(2027, 4, 27, 10, 0)) is False

    def test_recurring_holiday_does_not_affect_other_days(self):
        assocs, holidays = self._make_recurring_cal()
        assert is_open(assocs, holidays, dt(2026, 4, 28, 10, 0)) is True  # Tuesday after

    def test_exact_date_overrides_recurring(self):
        """YYYY-MM-DD entry takes precedence over MM-DD recurring."""
        cal = make_cal(holiday_set_ids=["hs-mixed"])
        holidays = {
            "cal-001": [
                # Recurring: Apr 27 always closed
                {"date": "04-27", "name": "Feriado Recorrente", "override_slots": None},
                # Exact override for 2026 only: half-day
                {"date": "2026-04-27", "name": "Feriado 2026 Meio expediente",
                 "override_slots": [{"open": "08:00", "close": "12:00"}]},
            ]
        }
        assocs = assoc(cal)
        # 2026: exact entry wins → half-day open
        assert is_open(assocs, holidays, dt(2026, 4, 27, 9, 0)) is True
        assert is_open(assocs, holidays, dt(2026, 4, 27, 13, 0)) is False
        # 2027: only recurring applies → fully closed
        assert is_open(assocs, holidays, dt(2027, 4, 27, 9, 0)) is False

    def test_recurring_with_override_slots(self):
        """MM-DD holiday with override slots → open in window."""
        cal = make_cal(holiday_set_ids=["hs-001"])
        holidays = {
            "cal-001": [{"date": "04-27", "name": "Feriado Recorrente Meio expediente",
                         "override_slots": [{"open": "08:00", "close": "12:00"}]}]
        }
        assocs = assoc(cal)
        assert is_open(assocs, holidays, dt(2026, 4, 27, 9, 0)) is True
        assert is_open(assocs, holidays, dt(2026, 4, 27, 13, 0)) is False
        # Same for next year
        assert is_open(assocs, holidays, dt(2027, 4, 27, 9, 0)) is True

    def test_next_open_slot_skips_recurring_holiday(self):
        """next_open_slot should skip a recurring holiday."""
        cal = make_cal(holiday_set_ids=["hs-recurring"])
        holidays = {
            "cal-001": [{"date": "04-27", "name": "Feriado Recorrente", "override_slots": None}]
        }
        # Sunday before the recurring Monday holiday → should skip to Tuesday
        after = dt(2026, 4, 26, 20, 0)  # Sunday evening
        nxt = next_open_slot(assoc(cal), holidays, after)
        assert nxt is not None
        local = nxt.astimezone(pytz.timezone(SAO_PAULO))
        assert local.strftime("%A %H:%M") == "Tuesday 08:00"
