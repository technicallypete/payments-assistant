"""Plain data the rest of the app sees instead of raw Stripe objects.

Gateways translate Stripe responses into these models, which keeps Stripe API-version churn in one
place and gives tests something easy to fake. Money is always integer minor units.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

ChargeStatus = Literal["succeeded", "failed", "pending"]
InvoiceStatus = Literal["draft", "open", "paid", "void", "uncollectible"]


class CustomerInfo(BaseModel):
    id: str  # cus_...
    name: str
    email: str | None = None


class PaymentRecord(BaseModel):
    """One charge attempt (successful or failed)."""

    id: str  # ch_...
    customer_id: str | None
    customer_name: str | None
    amount: int
    amount_refunded: int = 0
    currency: str
    status: ChargeStatus
    failure_code: str | None = None  # e.g. "card_declined"
    decline_code: str | None = None  # e.g. "insufficient_funds"
    failure_message: str | None = None
    description: str | None = None
    invoice_id: str | None = None
    # When it happened, for business purposes. Normally Stripe's `created`; seeded demo data may
    # backdate it via metadata (see stripe_gateway.effective_created).
    occurred_at: datetime

    @property
    def net_amount(self) -> int:
        return self.amount - self.amount_refunded if self.status == "succeeded" else 0


class InvoiceRecord(BaseModel):
    id: str  # in_...
    customer_id: str
    customer_name: str | None
    number: str | None
    status: InvoiceStatus
    currency: str
    amount_due: int
    amount_paid: int
    amount_remaining: int
    description: str | None = None
    due_date: datetime | None = None
    hosted_invoice_url: str | None = None
    created: datetime


class RefundResult(BaseModel):
    id: str  # re_...
    charge_id: str
    amount: int
    currency: str
    status: str


class PaymentLinkResult(BaseModel):
    id: str  # plink_...
    url: str


class ForeignObjectError(PermissionError):
    """A Stripe object that doesn't belong to the bound customer. Never reveal anything about it."""


class StripeObjectNotFound(LookupError):
    pass
