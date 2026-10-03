"""A small, fixed demo account (mirrors the seed's shape) for tool/agent tests and LLM evals.

Clock: Saturday 2026-10-03 15:30 America/New_York.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from payments_assistant.core.stripe_types import CustomerInfo, InvoiceRecord, PaymentRecord
from tests.fakes import FakeOwnerGateway

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 3, 15, 30, tzinfo=NY)

CUSTOMERS = [
    CustomerInfo(id="cus_maya", name="Maya Chen", email="maya@example.com"),
    CustomerInfo(id="cus_acme", name="Acme Corp", email="ap@acme.example"),
    CustomerInfo(id="cus_blue", name="Bluebird Bakery", email="hi@bluebird.example"),
]


def _pay(cid, name, amount, when, status="succeeded", refunded=0, decline=None, n=[0]):  # noqa: B006
    n[0] += 1
    return PaymentRecord(
        id=f"ch_{n[0]:03d}_{cid[4:]}",
        customer_id=cid,
        customer_name=name,
        amount=amount,
        amount_refunded=refunded,
        currency="usd",
        status=status,
        failure_code="card_declined" if status == "failed" else None,
        decline_code=decline,
        occurred_at=when,
    )


def _at(days_ago: int, hour: int) -> datetime:
    return (NOW - timedelta(days=days_ago)).replace(hour=hour, minute=0)


PAYMENTS = [
    # today: $430 + $250 + $60 + $82 = $822 succeeded, 2 declines
    _pay("cus_acme", "Acme Corp", 43_000, _at(0, 9)),
    _pay("cus_blue", "Bluebird Bakery", 25_000, _at(0, 11)),
    _pay("cus_maya", "Maya Chen", 6_000, _at(0, 10)),
    _pay("cus_maya", "Maya Chen", 8_200, _at(0, 14)),  # Maya's LAST payment
    _pay("cus_blue", "Bluebird Bakery", 9_900, _at(0, 12), "failed", decline="insufficient_funds"),
    _pay("cus_acme", "Acme Corp", 4_000, _at(0, 13), "failed", decline="insufficient_funds"),
    # yesterday
    _pay("cus_acme", "Acme Corp", 30_000, _at(1, 10)),
    # last week (Mon 9/21 – Sun 9/27): $1,000 ; week before (9/14 – 9/20): $800
    _pay("cus_acme", "Acme Corp", 100_000, datetime(2026, 9, 23, 10, tzinfo=NY)),
    _pay("cus_maya", "Maya Chen", 80_000, datetime(2026, 9, 16, 10, tzinfo=NY), refunded=5_000),
]
MAYA_LAST_CHARGE = "ch_004_maya"

INVOICES = [
    InvoiceRecord(
        id="in_acme_1200",
        customer_id="cus_acme",
        customer_name="Acme Corp",
        number="ACME-0001",
        status="open",
        currency="usd",
        amount_due=120_000,
        amount_paid=0,
        amount_remaining=120_000,
        due_date=datetime(2026, 10, 10, tzinfo=UTC),
        hosted_invoice_url="https://invoice.stripe.com/i/acme1200",
        created=NOW - timedelta(days=5),
    ),
]


def owner_gateway() -> FakeOwnerGateway:
    return FakeOwnerGateway(
        customers=list(CUSTOMERS), payments=list(PAYMENTS), invoices=list(INVOICES)
    )
