from datetime import UTC, datetime, timedelta

from payments_assistant.core.services.reporting import compare, open_invoices, summarize
from payments_assistant.core.stripe_types import InvoiceRecord, PaymentRecord

T0 = datetime(2026, 10, 3, 12, tzinfo=UTC)
START, END = T0 - timedelta(hours=12), T0 + timedelta(hours=1)


def pay(amount, *, status="succeeded", customer="Maya Chen", refunded=0, decline=None, cur="usd"):
    return PaymentRecord(
        id=f"ch_{amount}_{customer}_{status}",
        customer_id="cus_x",
        customer_name=customer,
        amount=amount,
        amount_refunded=refunded,
        currency=cur,
        status=status,
        failure_code="card_declined" if status == "failed" else None,
        decline_code=decline,
        occurred_at=T0,
    )


def test_summarize_counts_money_and_declines():
    stats = summarize(
        [
            pay(10_000),
            pay(25_000, customer="Acme Corp", refunded=5_000),
            pay(9_900, status="failed", decline="insufficient_funds"),
            pay(4_000, status="failed", decline="insufficient_funds"),
            pay(1_000, status="failed"),
            pay(99_999, cur="eur"),  # other currency ignored
        ],
        start=START,
        end=END,
        currency="USD",
    )
    assert stats.gross == 35_000
    assert stats.refunded == 5_000
    assert stats.net == 30_000
    assert stats.successful_count == 2
    assert stats.failed_count == 3
    assert stats.failures_by_reason == {"insufficient_funds": 2, "card_declined": 1}
    assert stats.average_payment == 17_500
    assert [c.customer for c in stats.top_customers] == ["Acme Corp", "Maya Chen"]


def test_summarize_empty_period():
    stats = summarize([], start=START, end=END, currency="usd")
    assert stats.gross == stats.net == stats.average_payment == 0
    assert stats.top_customers == []


def test_compare_change_and_pct():
    cur = summarize([pay(30_000)], start=START, end=END, currency="usd")
    prev = summarize([pay(20_000)], start=START, end=END, currency="usd")
    c = compare(cur, prev)
    assert c.gross_change == 10_000
    assert c.gross_change_pct == 50.0


def test_compare_with_empty_previous_has_no_pct():
    cur = summarize([pay(30_000)], start=START, end=END, currency="usd")
    prev = summarize([], start=START, end=END, currency="usd")
    assert compare(cur, prev).gross_change_pct is None


def inv(iid, remaining, status="open", due=None, name="Acme Corp"):
    return InvoiceRecord(
        id=iid,
        customer_id="cus_acme",
        customer_name=name,
        number=None,
        status=status,
        currency="usd",
        amount_due=remaining,
        amount_paid=0,
        amount_remaining=remaining,
        due_date=due,
        hosted_invoice_url="https://invoice.stripe.com/x",
        created=T0,
    )


def test_open_invoices_filters_sorts_and_flags_overdue():
    rows = open_invoices(
        [
            inv("in_small", 18_000, name="Maya Chen", due=T0 + timedelta(days=3)),
            inv("in_big", 120_000, due=T0 - timedelta(days=1)),
            inv("in_paid", 0, status="paid"),
            inv("in_void", 5_000, status="void"),
        ],
        now=T0,
    )
    assert [r.invoice_id for r in rows] == ["in_big", "in_small"]
    assert rows[0].overdue and not rows[1].overdue
