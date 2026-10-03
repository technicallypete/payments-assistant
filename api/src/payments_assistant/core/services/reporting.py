"""Turn raw payments/invoices into the aggregates the LLM narrates (daily summary, comparisons).

The LLM never adds up money: it receives these numbers and writes prose around them.
"""

from collections import Counter, defaultdict
from datetime import datetime

from pydantic import BaseModel

from payments_assistant.core.stripe_types import InvoiceRecord, PaymentRecord


class CustomerTotal(BaseModel):
    customer: str
    amount: int


class PeriodStats(BaseModel):
    start: datetime
    end: datetime
    currency: str
    gross: int  # successful charges
    refunded: int
    net: int
    successful_count: int
    failed_count: int
    failures_by_reason: dict[str, int]  # decline_code or failure_code → count
    average_payment: int
    top_customers: list[CustomerTotal]


class Comparison(BaseModel):
    current: PeriodStats
    previous: PeriodStats
    gross_change: int
    gross_change_pct: float | None  # None when the previous period had nothing


class OpenInvoice(BaseModel):
    invoice_id: str
    customer: str
    amount_remaining: int
    currency: str
    due_date: datetime | None
    overdue: bool


def summarize(
    payments: list[PaymentRecord],
    *,
    start: datetime,
    end: datetime,
    currency: str,
    top_n: int = 3,
) -> PeriodStats:
    ours = [p for p in payments if p.currency.lower() == currency.lower()]
    ok = [p for p in ours if p.status == "succeeded"]
    failed = [p for p in ours if p.status == "failed"]

    gross = sum(p.amount for p in ok)
    refunded = sum(p.amount_refunded for p in ok)
    by_customer: dict[str, int] = defaultdict(int)
    for p in ok:
        by_customer[p.customer_name or p.customer_id or "Unknown customer"] += p.amount
    top = sorted(by_customer.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]

    return PeriodStats(
        start=start,
        end=end,
        currency=currency.lower(),
        gross=gross,
        refunded=refunded,
        net=gross - refunded,
        successful_count=len(ok),
        failed_count=len(failed),
        failures_by_reason=dict(
            Counter(p.decline_code or p.failure_code or "unknown" for p in failed)
        ),
        average_payment=round(gross / len(ok)) if ok else 0,
        top_customers=[CustomerTotal(customer=c, amount=a) for c, a in top],
    )


def compare(current: PeriodStats, previous: PeriodStats) -> Comparison:
    change = current.gross - previous.gross
    pct = round(100 * change / previous.gross, 1) if previous.gross else None
    return Comparison(current=current, previous=previous, gross_change=change, gross_change_pct=pct)


def open_invoices(invoices: list[InvoiceRecord], *, now: datetime) -> list[OpenInvoice]:
    rows = [
        OpenInvoice(
            invoice_id=i.id,
            customer=i.customer_name or i.customer_id,
            amount_remaining=i.amount_remaining,
            currency=i.currency,
            due_date=i.due_date,
            overdue=bool(i.due_date and i.due_date < now),
        )
        for i in invoices
        if i.status == "open" and i.amount_remaining > 0
    ]
    return sorted(rows, key=lambda r: (-r.amount_remaining, r.customer))
