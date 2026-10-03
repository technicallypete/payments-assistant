"""Stripe-backed gateways (see `stripe_gateway.py` for the Protocols and the audience split).

Uses `StripeClient.v1.*` async methods (stripe-python 16, API version 2026-09-30.endive). Pure
mapping helpers (`charge_to_record`, `invoice_to_record`, ...) are module-level so unit tests can
exercise them with hand-built `StripeObject`s and no network.

API-version notes (verified against the installed SDK):
- `Charge` has no `invoice` field any more (invoice ↔ payment links moved to `invoice.payments`),
  so `PaymentRecord.invoice_id` is only filled from `metadata["invoice_id"]` when present.
- Decline details: `charge.failure_code` (e.g. "card_declined") and `charge.outcome.reason`
  (e.g. "insufficient_funds") for failed charges.
- Invoice items take `amount` + `currency` + `invoice` directly; payment links accept inline
  `price_data` with `product_data`, so no separate Price object is needed.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import stripe

from payments_assistant.core.config import Settings
from payments_assistant.core.stripe_types import (
    CustomerInfo,
    ForeignObjectError,
    InvoiceRecord,
    PaymentLinkResult,
    PaymentRecord,
    RefundResult,
    StripeObjectNotFound,
)

# Seeded demo data is created "now" (Stripe can't backdate charges); the seed stamps the intended
# business time here so summaries and week-over-week comparisons have history to work with.
OCCURRED_AT_KEY = "pa_occurred_at"
# Charges can't be deleted, so `seed --reset` tags superseded demo charges with this; every
# listing skips them.
HIDDEN_KEY = "pa_hidden"
# How far back list_payments looks by Stripe `created` before filtering on the effective time.
LOOKBACK = timedelta(days=120)

_FOREIGN = "Invoice not found."  # identical for "missing" and "someone else's"


def make_client(settings: Settings) -> stripe.StripeClient:
    return stripe.StripeClient(settings.stripe_secret_key)


# ------------------------------------------------------------------------------------- mapping


def _ts(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value, UTC) if value is not None else None


def _g(obj: Any, key: str) -> Any:
    """Field access that works for dicts and StripeObjects (which, in SDK 16, have no `.get`)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    try:
        return obj[key]
    except (KeyError, AttributeError, TypeError):
        return None


def _meta(obj: Any) -> dict[str, Any]:
    meta = _g(obj, "metadata")
    if not meta:
        return {}
    return meta.to_dict() if hasattr(meta, "to_dict") else dict(meta)


def _customer_ref(value: Any) -> tuple[str | None, str | None]:
    """A charge/invoice `customer` is an id string, an expanded Customer, or a DeletedCustomer."""
    if value is None:
        return None, None
    if isinstance(value, str):
        return value, None
    cid = _g(value, "id")
    name = None if _g(value, "deleted") else _g(value, "name")
    return cid, name


def effective_created(obj: Any) -> datetime:
    """Business timestamp: `metadata.pa_occurred_at` (seed backdating) if set, else `created`."""
    override = _meta(obj).get(OCCURRED_AT_KEY)
    if override:
        try:
            return datetime.fromtimestamp(int(override), UTC)
        except (TypeError, ValueError):
            pass
    created = _ts(obj["created"])
    assert created is not None
    return created


def is_hidden(obj: Any) -> bool:
    return _meta(obj).get(HIDDEN_KEY) == "1"


def customer_to_info(c: Any) -> CustomerInfo:
    return CustomerInfo(
        id=c["id"], name=_g(c, "name") or _g(c, "email") or c["id"], email=_g(c, "email")
    )


def charge_to_record(ch: Any) -> PaymentRecord:
    customer_id, customer_name = _customer_ref(_g(ch, "customer"))
    if customer_name is None:
        billing = _g(ch, "billing_details") or {}
        customer_name = _g(billing, "name") if billing else None
    status = _g(ch, "status")
    outcome = _g(ch, "outcome") or {}
    failed = status == "failed"
    return PaymentRecord(
        id=ch["id"],
        customer_id=customer_id,
        customer_name=customer_name,
        amount=ch["amount"],
        amount_refunded=_g(ch, "amount_refunded") or 0,
        currency=ch["currency"],
        status=status if status in ("succeeded", "failed", "pending") else "pending",
        failure_code=_g(ch, "failure_code") if failed else None,
        decline_code=(_g(outcome, "reason") if outcome else None) if failed else None,
        failure_message=_g(ch, "failure_message") if failed else None,
        description=_g(ch, "description"),
        invoice_id=_meta(ch).get("invoice_id"),
        occurred_at=effective_created(ch),
    )


def invoice_to_record(inv: Any) -> InvoiceRecord:
    customer_id, expanded_name = _customer_ref(_g(inv, "customer"))
    if customer_id is None:
        raise ValueError(f"invoice {inv['id']} has no customer")
    created = _ts(inv["created"])
    assert created is not None
    return InvoiceRecord(
        id=inv["id"],
        customer_id=customer_id,
        customer_name=_g(inv, "customer_name") or expanded_name,
        number=_g(inv, "number"),
        status=_g(inv, "status") or "draft",
        currency=inv["currency"],
        amount_due=_g(inv, "amount_due") or 0,
        amount_paid=_g(inv, "amount_paid") or 0,
        amount_remaining=_g(inv, "amount_remaining") or 0,
        description=_g(inv, "description"),
        due_date=_ts(_g(inv, "due_date")),
        hosted_invoice_url=_g(inv, "hosted_invoice_url"),
        created=created,
    )


def in_window(records: list[PaymentRecord], start: datetime, end: datetime) -> list[PaymentRecord]:
    """start <= occurred_at < end, newest first."""
    kept = [r for r in records if start <= r.occurred_at < end]
    return sorted(kept, key=lambda r: r.occurred_at, reverse=True)


def end_of_day_utc(d: date) -> int:
    """Stripe wants `due_date` as a unix timestamp; use 23:59:59 UTC of the given day."""
    return int(datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=UTC).timestamp())


async def _collect(listing: Any) -> list[Any]:
    return [obj async for obj in listing.auto_paging_iter()]


# ------------------------------------------------------------------------------------- owner


class LiveOwnerGateway:
    """Everything in the Stripe account. Owner surfaces and the seed only."""

    def __init__(self, client: stripe.StripeClient):
        self._v1 = client.v1

    async def list_customers(self) -> list[CustomerInfo]:
        listing = await self._v1.customers.list_async({"limit": 100})
        return [customer_to_info(c) for c in await _collect(listing)]

    async def search_customers(self, query: str) -> list[CustomerInfo]:
        # Deliberately not Stripe Search: it's eventually consistent (fresh customers go missing).
        q = query.strip().lower()
        if not q:
            return []
        return [
            c
            for c in await self.list_customers()
            if q in c.name.lower() or (c.email and q in c.email.lower())
        ]

    async def list_payments(
        self,
        *,
        start: datetime,
        end: datetime,
        customer_id: str | None = None,
    ) -> list[PaymentRecord]:
        """Charges with start <= occurred_at < end, newest first.

        Demo trade-off: seeded charges are created "now" but carry a backdated `pa_occurred_at`,
        so a Stripe `created >= start` filter would miss them. We list everything created since
        min(start, now - 120 days) and filter on the effective time in code. Fine for a small
        test account; a real deployment would filter by `created` directly (or mirror via
        webhooks).
        """
        floor = min(start, datetime.now(UTC) - LOOKBACK)
        params: dict[str, Any] = {
            "limit": 100,
            "created": {"gte": int(floor.timestamp())},
            "expand": ["data.customer"],
        }
        if customer_id:
            params["customer"] = customer_id
        listing = await self._v1.charges.list_async(params)
        records = [charge_to_record(ch) for ch in await _collect(listing) if not is_hidden(ch)]
        if customer_id:
            records = [r for r in records if r.customer_id == customer_id]
        return in_window(records, start, end)

    async def get_payment(self, charge_id: str) -> PaymentRecord:
        try:
            ch = await self._v1.charges.retrieve_async(charge_id, {"expand": ["customer"]})
        except stripe.InvalidRequestError as err:
            raise StripeObjectNotFound(charge_id) from err
        if is_hidden(ch):
            raise StripeObjectNotFound(charge_id)
        return charge_to_record(ch)

    async def list_invoices(
        self,
        *,
        status: str | None = None,
        customer_id: str | None = None,
    ) -> list[InvoiceRecord]:
        params: dict[str, Any] = {"limit": 100}
        if status:
            params["status"] = status
        if customer_id:
            params["customer"] = customer_id
        listing = await self._v1.invoices.list_async(params)
        return [invoice_to_record(inv) for inv in await _collect(listing)]

    async def refund(
        self, *, charge_id: str, amount: int | None, idempotency_key: str
    ) -> RefundResult:
        params: dict[str, Any] = {"charge": charge_id}
        if amount is not None:
            params["amount"] = amount
        re = await self._v1.refunds.create_async(params, {"idempotency_key": idempotency_key})
        return RefundResult(
            id=re["id"],
            charge_id=charge_id,
            amount=re["amount"],
            currency=re["currency"],
            status=_g(re, "status") or "pending",
        )

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
        """Draft → one line item → finalize. `auto_advance=False` so Stripe never emails it.

        Each step gets a derived idempotency key, so retrying the whole call with the same key
        returns the same invoice instead of creating a second one.
        """
        inv = await self._v1.invoices.create_async(
            {
                "customer": customer_id,
                "currency": currency,
                "collection_method": "send_invoice",
                "due_date": end_of_day_utc(due_date),
                "description": description,
                "pending_invoice_items_behavior": "exclude",
                "auto_advance": False,
            },
            {"idempotency_key": f"{idempotency_key}:create"},
        )
        await self._v1.invoice_items.create_async(
            {
                "customer": customer_id,
                "invoice": inv["id"],
                "amount": amount,
                "currency": currency,
                "description": description,
            },
            {"idempotency_key": f"{idempotency_key}:item"},
        )
        final = await self._v1.invoices.finalize_invoice_async(
            inv["id"],
            {"auto_advance": False},
            {"idempotency_key": f"{idempotency_key}:finalize"},
        )
        return invoice_to_record(final)

    async def send_invoice(self, *, invoice_id: str, idempotency_key: str) -> InvoiceRecord:
        inv = await self._v1.invoices.send_invoice_async(
            invoice_id, None, {"idempotency_key": idempotency_key}
        )
        return invoice_to_record(inv)

    async def create_payment_link(
        self, *, amount: int, currency: str, description: str, idempotency_key: str
    ) -> PaymentLinkResult:
        link = await self._v1.payment_links.create_async(
            {
                "line_items": [
                    {
                        "price_data": {
                            "currency": currency,
                            "unit_amount": amount,
                            "product_data": {"name": description},
                        },
                        "quantity": 1,
                    }
                ]
            },
            {"idempotency_key": idempotency_key},
        )
        return PaymentLinkResult(id=link["id"], url=link["url"])


# ------------------------------------------------------------------------------------- customer


class LiveCustomerGateway:
    """Bound to one Stripe customer. Every call is filtered to it and every object re-checked."""

    def __init__(self, client: stripe.StripeClient, stripe_customer_id: str):
        if not stripe_customer_id:
            raise ValueError("stripe_customer_id is required")
        self._v1 = client.v1
        self.stripe_customer_id = stripe_customer_id

    def _owned(self, obj: Any) -> bool:
        customer_id, _ = _customer_ref(_g(obj, "customer"))
        return customer_id == self.stripe_customer_id

    async def list_invoices(self, *, open_only: bool = False) -> list[InvoiceRecord]:
        params: dict[str, Any] = {"customer": self.stripe_customer_id, "limit": 100}
        if open_only:
            params["status"] = "open"
        listing = await self._v1.invoices.list_async(params)
        return [invoice_to_record(inv) for inv in await _collect(listing) if self._owned(inv)]

    async def get_invoice(self, invoice_id: str) -> InvoiceRecord:
        try:
            inv = await self._v1.invoices.retrieve_async(invoice_id)
        except stripe.InvalidRequestError as err:
            raise ForeignObjectError(_FOREIGN) from err
        if not self._owned(inv):
            raise ForeignObjectError(_FOREIGN)
        return invoice_to_record(inv)

    async def list_payments(self, *, limit: int = 20) -> list[PaymentRecord]:
        listing = await self._v1.charges.list_async(
            {"customer": self.stripe_customer_id, "limit": min(max(limit, 1), 100)}
        )
        records = [
            charge_to_record(ch) for ch in listing.data if self._owned(ch) and not is_hidden(ch)
        ]
        return sorted(records, key=lambda r: r.occurred_at, reverse=True)[:limit]
