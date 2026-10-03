from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from payments_assistant.core.timeutil import (
    DueDateError,
    Period,
    period_range,
    previous_range,
    resolve_due_date,
)

NY = ZoneInfo("America/New_York")
# Saturday 2026-10-03, 15:30 New York time (19:30 UTC).
NOW = datetime(2026, 10, 3, 15, 30, tzinfo=NY)


def local(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=NY)


@pytest.mark.parametrize(
    "period,start,end",
    [
        (Period.TODAY, local(2026, 10, 3), NOW),
        (Period.YESTERDAY, local(2026, 10, 2), local(2026, 10, 3)),
        (Period.THIS_WEEK, local(2026, 9, 28), NOW),  # Monday
        (Period.LAST_WEEK, local(2026, 9, 21), local(2026, 9, 28)),
        (Period.WEEK_BEFORE_LAST, local(2026, 9, 14), local(2026, 9, 21)),
        (Period.THIS_MONTH, local(2026, 10, 1), NOW),
        (Period.LAST_MONTH, local(2026, 9, 1), local(2026, 10, 1)),
        (Period.LAST_7_DAYS, NOW - timedelta(days=7), NOW),
    ],
)
def test_period_ranges(period, start, end):
    assert period_range(period, NOW, NY) == (start, end)


def test_ranges_use_business_timezone_not_utc():
    # 00:30 UTC on Oct 4 is still Oct 3 in New York.
    utc_now = datetime(2026, 10, 4, 0, 30, tzinfo=ZoneInfo("UTC"))
    start, _ = period_range(Period.TODAY, utc_now, NY)
    assert start == local(2026, 10, 3)


def test_today_compares_to_yesterday_same_time_of_day():
    assert previous_range(Period.TODAY, NOW, NY) == (local(2026, 10, 2), local(2026, 10, 2, 15, 30))


def test_last_week_compares_to_week_before():
    assert previous_range(Period.LAST_WEEK, NOW, NY) == period_range(
        Period.WEEK_BEFORE_LAST, NOW, NY
    )


def test_this_week_compares_to_same_span_last_week():
    start, end = previous_range(Period.THIS_WEEK, NOW, NY)
    assert start == local(2026, 9, 21)
    assert end == local(2026, 9, 26, 15, 30)


def test_last_month_compares_to_month_before():
    assert previous_range(Period.LAST_MONTH, NOW, NY) == (local(2026, 8, 1), local(2026, 9, 1))


def test_this_month_compares_to_same_elapsed_span_capped():
    start, end = previous_range(Period.THIS_MONTH, NOW, NY)
    assert start == local(2026, 9, 1)
    assert end == local(2026, 9, 3, 15, 30)


def test_dst_boundary_day_is_23_hours():
    # US DST starts 2026-03-08; that local day is 23h long.
    now = datetime(2026, 3, 9, 12, tzinfo=NY)
    start, end = period_range(Period.YESTERDAY, now, NY)
    # Same-tzinfo subtraction in Python is wall-clock; measure the real elapsed time in UTC.
    utc = ZoneInfo("UTC")
    assert end.astimezone(utc) - start.astimezone(utc) == timedelta(hours=23)


TODAY = date(2026, 10, 3)  # Saturday


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("2026-10-20", date(2026, 10, 20)),
        ("today", TODAY),
        ("Tomorrow", date(2026, 10, 4)),
        ("in 3 days", date(2026, 10, 6)),
        ("in 2 weeks", date(2026, 10, 17)),
        ("next Friday", date(2026, 10, 9)),
        ("friday", date(2026, 10, 9)),
        ("this friday", date(2026, 10, 9)),
        ("next saturday", date(2026, 10, 10)),  # strictly after today
        ("next  monday ", date(2026, 10, 5)),
        ("end of month", date(2026, 10, 31)),
        ("end of next month", date(2026, 11, 30)),
    ],
)
def test_resolve_due_date(phrase, expected):
    assert resolve_due_date(phrase, TODAY) == expected


def test_end_of_month_in_december_rolls_year():
    assert resolve_due_date("end of next month", date(2026, 12, 15)) == date(2027, 1, 31)


@pytest.mark.parametrize("phrase", ["whenever", "next fortnight", "in 1000 days", ""])
def test_resolve_due_date_rejects_unknown(phrase):
    with pytest.raises(DueDateError):
        resolve_due_date(phrase, TODAY)
