"""Date math in the business timezone (spec §7: range math happens in code, not in the LLM).

The LLM picks a named `Period` or a due-date phrase; these functions turn them into exact instants.
Weeks run Monday–Sunday. All returned datetimes are timezone-aware.
"""

import re
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo


class Period(StrEnum):
    TODAY = "today"
    YESTERDAY = "yesterday"
    THIS_WEEK = "this_week"
    LAST_WEEK = "last_week"
    WEEK_BEFORE_LAST = "week_before_last"
    LAST_7_DAYS = "last_7_days"
    LAST_30_DAYS = "last_30_days"
    THIS_MONTH = "this_month"
    LAST_MONTH = "last_month"


def local_today(now: datetime, tz: ZoneInfo) -> date:
    return now.astimezone(tz).date()


def _start_of(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz)


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _add_months(day: date, months: int) -> date:
    y, m = divmod(day.month - 1 + months, 12)
    return date(day.year + y, m + 1, 1)


def period_range(period: Period, now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[start, end) for the period. Current periods ("today", "this week") end at `now`, so
    they compare like-for-like with what has happened so far."""
    today = local_today(now, tz)
    monday = today - timedelta(days=today.weekday())
    now_local = now.astimezone(tz)
    match period:
        case Period.TODAY:
            return _start_of(today, tz), now_local
        case Period.YESTERDAY:
            return _start_of(today - timedelta(days=1), tz), _start_of(today, tz)
        case Period.THIS_WEEK:
            return _start_of(monday, tz), now_local
        case Period.LAST_WEEK:
            return _start_of(monday - timedelta(days=7), tz), _start_of(monday, tz)
        case Period.WEEK_BEFORE_LAST:
            return _start_of(monday - timedelta(days=14), tz), _start_of(
                monday - timedelta(days=7), tz
            )
        case Period.LAST_7_DAYS:
            return now_local - timedelta(days=7), now_local
        case Period.LAST_30_DAYS:
            return now_local - timedelta(days=30), now_local
        case Period.THIS_MONTH:
            return _start_of(_month_start(today), tz), now_local
        case Period.LAST_MONTH:
            first = _month_start(today)
            return _start_of(_add_months(first, -1), tz), _start_of(first, tz)
    raise ValueError(period)  # pragma: no cover


def previous_range(period: Period, now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """The comparison window for `period`:
    - in-progress periods compare against the same elapsed span one period earlier (today until now
      vs yesterday until the same time), so "ahead of yesterday" is fair;
    - calendar months use the previous calendar month (months differ in length);
    - everything else uses the equal-length window immediately before."""
    start, end = period_range(period, now, tz)
    match period:
        case Period.TODAY:
            return start - timedelta(days=1), end - timedelta(days=1)
        case Period.THIS_WEEK:
            return start - timedelta(days=7), end - timedelta(days=7)
        case Period.THIS_MONTH:
            prev_start = _start_of(_add_months(start.date(), -1), tz)
            return prev_start, min(prev_start + (end - start), start)
        case Period.LAST_MONTH:
            return _start_of(_add_months(start.date(), -1), tz), start
        case _:
            return start - (end - start), start


_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_IN_N = re.compile(r"^in (\d{1,3}) (day|days|week|weeks)$")
_NEXT_WD = re.compile(r"^(next|this|on)? ?(" + "|".join(_WEEKDAYS) + r")$")


class DueDateError(ValueError):
    pass


def resolve_due_date(phrase: str, today: date) -> date:
    """Turn an LLM-extracted phrase into a date.

    Supported: ISO "2026-10-09", "today", "tomorrow", "in N days|weeks", "<weekday>",
    "this|next <weekday>", "end of month", "end of next month".
    "next friday" / "friday" / "this friday" all mean the first Friday strictly after today
    (documented assumption; the confirm card shows the resolved date before anything happens).
    """
    p = " ".join(phrase.strip().lower().split())
    try:
        return date.fromisoformat(p)
    except ValueError:
        pass
    if p == "today":
        return today
    if p == "tomorrow":
        return today + timedelta(days=1)
    if m := _IN_N.match(p):
        n = int(m.group(1))
        return today + timedelta(days=n * (7 if m.group(2).startswith("week") else 1))
    if m := _NEXT_WD.match(p):
        target = _WEEKDAYS.index(m.group(2))
        delta = (target - today.weekday()) % 7 or 7
        return today + timedelta(days=delta)
    if p in ("end of month", "end of the month"):
        return _add_months(today, 1) - timedelta(days=1)
    if p in ("end of next month", "end of the next month"):
        return _add_months(today, 2) - timedelta(days=1)
    raise DueDateError(f"can't interpret due date {phrase!r}; use a date like 2026-10-09")
