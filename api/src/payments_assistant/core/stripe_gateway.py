"""Stripe access, split by audience (spec §1, §2.5).

- `OwnerStripeGateway`: everything in the account; used by owner tools and the seed.
- `CustomerStripeGateway`: bound to ONE stripe customer id at construction. Every method is
  implicitly filtered to that customer, and every fetched object is re-checked
  (`obj.customer == bound id`) before anything is returned. There is no way to pass a different
  customer id in.

Both are Protocols so core services and tests can use fakes (tests/fakes.py).
The concrete Stripe-backed classes live in `stripe_live.py`.
"""

from datetime import date, datetime
from typing import Protocol

from payments_assistant.core.stripe_types import (
    CustomerInfo,
    InvoiceRecord,
    PaymentLinkResult,
    PaymentRecord,
    RefundResult,
)


class OwnerStripeGateway(Protocol):
    async def list_customers(self) -> list[CustomerInfo]: ...

    async def search_customers(self, query: str) -> list[CustomerInfo]:
        """Case-insensitive match on name or email."""
        ...

    async def list_payments(
        self,
        *,
        start: datetime,
        end: datetime,
        customer_id: str | None = None,
    ) -> list[PaymentRecord]:
        """Charges (succeeded and failed) with start <= occurred_at < end, newest first."""
        ...

    async def get_payment(self, charge_id: str) -> PaymentRecord: ...

    async def list_invoices(
        self,
        *,
        status: str | None = None,
        customer_id: str | None = None,
    ) -> list[InvoiceRecord]: ...

    async def refund(
        self, *, charge_id: str, amount: int | None, idempotency_key: str
    ) -> RefundResult: ...

    async def create_invoice(
        self,
        *,
        customer_id: str,
        amount: int,
        currency: str,
        description: str,
        due_date: date,
        idempotency_key: str,
    ) -> InvoiceRecord:
        """Creates, adds one line item, and finalizes (collection_method=send_invoice)."""
        ...

    async def send_invoice(self, *, invoice_id: str, idempotency_key: str) -> InvoiceRecord: ...

    async def create_payment_link(
        self, *, amount: int, currency: str, description: str, idempotency_key: str
    ) -> PaymentLinkResult: ...


class CustomerStripeGateway(Protocol):
    stripe_customer_id: str

    async def list_invoices(self, *, open_only: bool = False) -> list[InvoiceRecord]: ...

    async def get_invoice(self, invoice_id: str) -> InvoiceRecord:
        """Raises ForeignObjectError if the invoice belongs to someone else (or doesn't exist:
        the caller must not be able to tell the difference)."""
        ...

    async def list_payments(self, *, limit: int = 20) -> list[PaymentRecord]: ...
