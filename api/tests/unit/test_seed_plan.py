"""The seed plan is pure: these pin the demo story the brief's example commands rely on."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import time_machine

from payments_assistant.seed import build_plan

TZ = ZoneInfo("America/New_York")


def _plan_at(local: datetime):
    """Freeze the clock at a local time and build the plan the way main() does."""
    with time_machine.travel(local, tick=False):
        now = datetime.now(UTC)
        return build_plan(now.astimezone(TZ).date(), TZ, now), now


def _local_date(dt: datetime) -> date:
    return dt.astimezone(TZ).date()


# Wednesday afternoon, a Monday just after midnight, and a DST-change Sunday.
MOMENTS = [
    datetime(2026, 10, 7, 15, 30, tzinfo=TZ),
    datetime(2026, 10, 5, 0, 20, tzinfo=TZ),
    datetime(2026, 11, 1, 9, 0, tzinfo=TZ),
]


@pytest.fixture(params=MOMENTS, ids=["wed-afternoon", "mon-just-after-midnight", "dst-sunday"])
def plan_and_now(request):
    return _plan_at(request.param)


def _successes_on(plan, day: date) -> int:
    return sum(
        p.amount for p in plan.payments if not p.decline and _local_date(p.occurred_at) == day
    )


def _week_total(plan, monday: date) -> int:
    days = {monday + timedelta(days=i) for i in range(7)}
    return sum(
        p.amount for p in plan.payments if not p.decline and _local_date(p.occurred_at) in days
    )


def test_today_matches_brief_example_and_is_ahead_of_yesterday(plan_and_now):
    plan, now = plan_and_now
    today = _local_date(now)
    assert _successes_on(plan, today) == 428_000  # "$4,280 across ... payments today"
    assert _successes_on(plan, today) > _successes_on(plan, today - timedelta(days=1))


def test_last_week_differs_from_week_before(plan_and_now):
    plan, now = plan_and_now
    today = _local_date(now)
    last_monday = today - timedelta(days=today.weekday() + 7)
    last_week = _week_total(plan, last_monday)
    week_before = _week_total(plan, last_monday - timedelta(days=7))
    assert last_week > 0 and week_before > 0
    assert last_week != week_before


def test_at_least_two_declines_today(plan_and_now):
    plan, now = plan_and_now
    today = _local_date(now)
    declines = [p for p in plan.payments if p.decline and _local_date(p.occurred_at) == today]
    assert len(declines) >= 2


def test_mayas_latest_payment_is_a_clean_success_today(plan_and_now):
    plan, now = plan_and_now
    mayas = sorted(
        (p for p in plan.payments if p.customer_key == "maya"), key=lambda p: p.occurred_at
    )
    latest = mayas[-1]
    assert not latest.decline
    assert latest.refund_amount is None
    assert _local_date(latest.occurred_at) == _local_date(now)
    # Maya has history, so "last" is meaningful.
    assert len(mayas) >= 3


def test_exactly_one_partial_refund_not_on_mayas_latest(plan_and_now):
    plan, _ = plan_and_now
    refunded = [p for p in plan.payments if p.refund_amount]
    assert len(refunded) == 1
    assert refunded[0].customer_key != "maya"
    assert 0 < refunded[0].refund_amount < refunded[0].amount


def test_invoices_cover_handoff_boundary_and_brief(plan_and_now):
    plan, _ = plan_and_now
    open_invoices = {(i.customer_key, i.amount) for i in plan.invoices if not i.paid}
    assert ("acme", 120_000) in open_invoices  # brief: unpaid $1,200 with Acme Corp
    assert ("acme", 350_000) in open_invoices  # ≥ $2,000 → handoff
    assert ("jordan", 200_000) in open_invoices  # exactly $2,000 → handoff (boundary)
    assert ("maya", 18_000) in open_invoices  # payable via the bot
    assert any(i.paid for i in plan.invoices)
    assert all(i.days_until_due > 0 for i in plan.invoices)


def test_nothing_happens_in_the_future(plan_and_now):
    plan, now = plan_and_now
    assert all(p.occurred_at <= now for p in plan.payments)


def test_today_payments_are_today_and_ordered(plan_and_now):
    plan, now = plan_and_now
    today = _local_date(now)
    todays = [p for p in plan.payments if _local_date(p.occurred_at) == today]
    assert len(todays) == 10
    assert [p.occurred_at for p in todays] == sorted(p.occurred_at for p in todays)


def test_every_payment_customer_is_seeded(plan_and_now):
    plan, _ = plan_and_now
    keys = {c.key for c in plan.customers}
    assert {p.customer_key for p in plan.payments} <= keys
    assert {i.customer_key for i in plan.invoices} <= keys
    assert len({c.email("x") for c in plan.customers}) == len(plan.customers)
