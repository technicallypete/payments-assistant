"""In-memory gateways implementing the Protocols in `core/stripe_gateway.py`.

Deterministic, no network. Mutating calls are recorded in `.calls` (one dict per call with
"method", "idempotency_key", and the call's kwargs) and honor idempotency keys: repeating a key
returns the first result and records nothing new. `FakeCustomerGateway` enforces the same
ownership rule as the live one.
"""

from datetime import UTC, date, datetime, time
from typing import Any

from payments_assistant.core.stripe_types import (
    CustomerInfo,
    ForeignObjectError,
    InvoiceRecord,
    PaymentLinkResult,
    PaymentRecord,
    RefundResult,
    StripeObjectNotFound,
)


class FakeOwnerGateway:
    def __init__(
        self,
        *,
        customers: list[CustomerInfo] | None = None,
        payments: list[PaymentRecord] | None = None,
        invoices: list[InvoiceRecord] | None = None,
    ):
        self.customers = list(customers or [])
        self.payments = list(payments or [])
        self.invoices = list(invoices or [])
        self.calls: list[dict[str, Any]] = []
        self._by_key: dict[str, Any] = {}
        self._seq = 0

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}_fake{self._seq:04d}"

    def _replay(self, key: str) -> Any | None:
        return self._by_key.get(key)

    def _record(self, method: str, key: str, result: Any, **kwargs: Any) -> Any:
        self.calls.append({"method": method, "idempotency_key": key, **kwargs})
        self._by_key[key] = result
        return result

    def _name(self, customer_id: str) -> str | None:
        return next((c.name for c in self.customers if c.id == customer_id), None)

    # ---- reads

    async def list_customers(self) -> list[CustomerInfo]:
        return list(self.customers)

    async def search_customers(self, query: str) -> list[CustomerInfo]:
        q = query.strip().lower()
        if not q:
            return []
        return [
            c
            for c in self.customers
            if q in c.name.lower() or (c.email is not None and q in c.email.lower())
        ]

    async def list_payments(
        self, *, start: datetime, end: datetime, customer_id: str | None = None
    ) -> list[PaymentRecord]:
        kept = [
            p
            for p in self.payments
            if start <= p.occurred_at < end
            and (customer_id is None or p.customer_id == customer_id)
        ]
        return sorted(kept, key=lambda p: p.occurred_at, reverse=True)

    async def get_payment(self, charge_id: str) -> PaymentRecord:
        for p in self.payments:
            if p.id == charge_id:
                return p
        raise StripeObjectNotFound(charge_id)

    async def list_invoices(
        self, *, status: str | None = None, customer_id: str | None = None
    ) -> list[InvoiceRecord]:
        return [
            i
            for i in self.invoices
            if (status is None or i.status == status)
            and (customer_id is None or i.customer_id == customer_id)
        ]

    # ---- writes

    async def refund(
        self, *, charge_id: str, amount: int | None, idempotency_key: str
    ) -> RefundResult:
        if (prior := self._replay(idempotency_key)) is not None:
            return prior
        payment = await self.get_payment(charge_id)  # raises StripeObjectNotFound, no call recorded
        refundable = payment.amount - payment.amount_refunded
        value = refundable if amount is None else amount
        if value <= 0 or value > refundable:
            raise ValueError(f"cannot refund {value} of {charge_id} (refundable {refundable})")
        idx = self.payments.index(payment)
        self.payments[idx] = payment.model_copy(
            update={"amount_refunded": payment.amount_refunded + value}
        )
        result = RefundResult(
            id=self._next_id("re"),
            charge_id=charge_id,
            amount=value,
            currency=payment.currency,
            status="succeeded",
        )
        return self._record("refund", idempotency_key, result, charge_id=charge_id, amount=amount)

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
        if (prior := self._replay(idempotency_key)) is not None:
            return prior
        invoice = InvoiceRecord(
            id=self._next_id("in"),
            customer_id=customer_id,
            customer_name=self._name(customer_id),
            number=f"FAKE-{self._seq:04d}",
            status="open",
            currency=currency,
            amount_due=amount,
            amount_paid=0,
            amount_remaining=amount,
            description=description,
            due_date=datetime.combine(due_date, time(23, 59, 59), tzinfo=UTC),
            hosted_invoice_url=f"https://invoice.stripe.com/i/fake_{self._seq:04d}",
            created=datetime.now(UTC),
        )
        self.invoices.append(invoice)
        return self._record(
            "create_invoice",
            idempotency_key,
            invoice,
            customer_id=customer_id,
            amount=amount,
            currency=currency,
            description=description,
            due_date=due_date,
        )

    async def send_invoice(self, *, invoice_id: str, idempotency_key: str) -> InvoiceRecord:
        if (prior := self._replay(idempotency_key)) is not None:
            return prior
        invoice = next((i for i in self.invoices if i.id == invoice_id), None)
        if invoice is None:
            raise StripeObjectNotFound(invoice_id)
        return self._record("send_invoice", idempotency_key, invoice, invoice_id=invoice_id)

    async def create_payment_link(
        self, *, amount: int, currency: str, description: str, idempotency_key: str
    ) -> PaymentLinkResult:
        if (prior := self._replay(idempotency_key)) is not None:
            return prior
        link_id = self._next_id("plink")
        result = PaymentLinkResult(id=link_id, url=f"https://buy.stripe.com/test_{link_id}")
        return self._record(
            "create_payment_link",
            idempotency_key,
            result,
            amount=amount,
            currency=currency,
            description=description,
        )


class FakeCustomerGateway:
    def __init__(
        self,
        stripe_customer_id: str,
        *,
        invoices: list[InvoiceRecord] | None = None,
        payments: list[PaymentRecord] | None = None,
    ):
        self.stripe_customer_id = stripe_customer_id
        # May deliberately include other customers' objects: the gateway must filter them out.
        self.invoices = list(invoices or [])
        self.payments = list(payments or [])

    def _owned_invoices(self) -> list[InvoiceRecord]:
        return [i for i in self.invoices if i.customer_id == self.stripe_customer_id]

    async def list_invoices(self, *, open_only: bool = False) -> list[InvoiceRecord]:
        return [i for i in self._owned_invoices() if not open_only or i.status == "open"]

    async def get_invoice(self, invoice_id: str) -> InvoiceRecord:
        for i in self._owned_invoices():
            if i.id == invoice_id:
                return i
        raise ForeignObjectError("Invoice not found.")  # unknown and foreign look identical

    async def list_payments(self, *, limit: int = 20) -> list[PaymentRecord]:
        owned = [p for p in self.payments if p.customer_id == self.stripe_customer_id]
        return sorted(owned, key=lambda p: p.occurred_at, reverse=True)[:limit]
