"""Mapping and filtering in stripe_live, using hand-built StripeObjects (no network)."""

from datetime import UTC, date, datetime, timedelta

import pytest
import stripe

from payments_assistant.core.stripe_live import (
    LiveCustomerGateway,
    charge_to_record,
    customer_to_info,
    effective_created,
    end_of_day_utc,
    in_window,
    invoice_to_record,
    is_hidden,
)
from payments_assistant.core.stripe_types import ForeignObjectError

T0 = int(datetime(2026, 10, 3, 15, tzinfo=UTC).timestamp())


def obj(**fields) -> stripe.StripeObject:
    return stripe.StripeObject.construct_from(fields, "sk_test_x")


def charge(**overrides) -> stripe.StripeObject:
    fields = {
        "id": "ch_1",
        "object": "charge",
        "amount": 4_200,
        "amount_refunded": 0,
        "currency": "usd",
        "status": "succeeded",
        "created": T0,
        "customer": "cus_maya",
        "billing_details": {"name": "Maya Chen"},
        "metadata": {},
        "failure_code": None,
        "failure_message": None,
        "outcome": {"type": "authorized", "reason": None},
        "description": "Order 1",
    }
    fields.update(overrides)
    return obj(**fields)


def invoice(**overrides) -> stripe.StripeObject:
    fields = {
        "id": "in_1",
        "object": "invoice",
        "customer": "cus_acme",
        "customer_name": "Acme Corp",
        "number": "ACME-0001",
        "status": "open",
        "currency": "usd",
        "amount_due": 120_000,
        "amount_paid": 0,
        "amount_remaining": 120_000,
        "description": "Consulting",
        "due_date": T0 + 86_400,
        "hosted_invoice_url": "https://invoice.stripe.com/i/x",
        "created": T0,
        "metadata": {},
    }
    fields.update(overrides)
    return obj(**fields)


def test_effective_created_defaults_to_created():
    assert effective_created(charge()) == datetime.fromtimestamp(T0, UTC)


def test_effective_created_honors_seed_backdating():
    backdated = T0 - 9 * 86_400
    rec = charge_to_record(charge(metadata={"pa_occurred_at": str(backdated)}))
    assert rec.occurred_at == datetime.fromtimestamp(backdated, UTC)


def test_effective_created_ignores_garbage_override():
    assert effective_created(charge(metadata={"pa_occurred_at": "soon"})) == datetime.fromtimestamp(
        T0, UTC
    )


def test_successful_charge_mapping():
    rec = charge_to_record(charge())
    assert rec.status == "succeeded"
    assert rec.customer_id == "cus_maya"
    assert rec.customer_name == "Maya Chen"
    assert rec.failure_code is None and rec.decline_code is None
    assert rec.net_amount == 4_200


def test_declined_charge_mapping():
    rec = charge_to_record(
        charge(
            status="failed",
            failure_code="card_declined",
            failure_message="Your card has insufficient funds.",
            outcome={"type": "issuer_declined", "reason": "insufficient_funds"},
        )
    )
    assert rec.status == "failed"
    assert rec.failure_code == "card_declined"
    assert rec.decline_code == "insufficient_funds"
    assert rec.net_amount == 0


def test_expanded_customer_name_wins_and_deleted_customer_is_nameless():
    rec = charge_to_record(charge(customer=obj(id="cus_x", name="Expanded Name")))
    assert (rec.customer_id, rec.customer_name) == ("cus_x", "Expanded Name")
    gone = charge_to_record(
        charge(customer=obj(id="cus_gone", deleted=True), billing_details={"name": None})
    )
    assert (gone.customer_id, gone.customer_name) == ("cus_gone", None)


def test_partial_refund_net_amount():
    assert charge_to_record(charge(amount_refunded=1_000)).net_amount == 3_200


def test_invoice_mapping():
    rec = invoice_to_record(invoice())
    assert rec.customer_id == "cus_acme"
    assert rec.amount_remaining == 120_000
    assert rec.due_date == datetime.fromtimestamp(T0 + 86_400, UTC)
    assert rec.hosted_invoice_url


def test_customer_info_falls_back_to_email_then_id():
    assert customer_to_info(obj(id="cus_1", name=None, email="a@b.c")).name == "a@b.c"
    assert customer_to_info(obj(id="cus_2", name=None, email=None)).name == "cus_2"


def test_in_window_is_half_open_and_newest_first():
    start = datetime.fromtimestamp(T0, UTC)
    end = start + timedelta(days=1)
    recs = [
        charge_to_record(charge(id="at_start", created=T0)),
        charge_to_record(charge(id="inside", created=T0 + 3_600)),
        charge_to_record(charge(id="at_end", created=T0 + 86_400)),
        charge_to_record(charge(id="before", created=T0 - 1)),
    ]
    assert [r.id for r in in_window(recs, start, end)] == ["inside", "at_start"]


def test_end_of_day_utc():
    ts = end_of_day_utc(date(2026, 10, 9))
    assert datetime.fromtimestamp(ts, UTC) == datetime(2026, 10, 9, 23, 59, 59, tzinfo=UTC)


class _FakeInvoices:
    def __init__(self, by_id):
        self.by_id = by_id

    async def retrieve_async(self, invoice_id, *args, **kwargs):
        if invoice_id not in self.by_id:
            raise stripe.InvalidRequestError("No such invoice", "id", code="resource_missing")
        return self.by_id[invoice_id]

    async def list_async(self, params=None, *args, **kwargs):
        # Simulate Stripe ignoring the customer filter: the gateway must still re-check.
        return obj(data=list(self.by_id.values()), has_more=False)


class _FakeClient:
    def __init__(self, invoices):
        self.v1 = obj()
        self.v1.invoices = _FakeInvoices(invoices)


@pytest.fixture
def customer_gateway():
    client = _FakeClient(
        {
            "in_mine": invoice(id="in_mine", customer="cus_maya"),
            "in_theirs": invoice(id="in_theirs", customer="cus_acme"),
        }
    )
    return LiveCustomerGateway(client, "cus_maya")  # type: ignore[arg-type]


async def test_customer_gateway_returns_own_invoice(customer_gateway):
    assert (await customer_gateway.get_invoice("in_mine")).id == "in_mine"


@pytest.mark.parametrize("invoice_id", ["in_theirs", "in_missing"])
async def test_customer_gateway_foreign_and_missing_look_identical(customer_gateway, invoice_id):
    with pytest.raises(ForeignObjectError) as exc:
        await customer_gateway.get_invoice(invoice_id)
    assert str(exc.value) == "Invoice not found."


def test_customer_gateway_requires_customer_id():
    with pytest.raises(ValueError):
        LiveCustomerGateway(_FakeClient({}), "")  # type: ignore[arg-type]


def test_hidden_charges_flagged():
    assert is_hidden(charge(metadata={"pa_hidden": "1"}))
    assert not is_hidden(charge(metadata={"pa_hidden": "0"}))
    assert not is_hidden(charge())


class _Listing:
    """Minimal stand-in for stripe.ListObject: `.data` plus async auto-paging."""

    def __init__(self, data):
        self.data = data

    def auto_paging_iter(self):
        async def gen():
            for item in self.data:
                yield item

        return gen()


class _FakeCharges:
    def __init__(self, charges):
        self.charges = charges

    async def list_async(self, params=None, *args, **kwargs):
        return _Listing(self.charges)

    async def retrieve_async(self, charge_id, *args, **kwargs):
        return next(c for c in self.charges if c["id"] == charge_id)


def _charge_client(charges):
    client = _FakeClient({})
    client.v1.charges = _FakeCharges(charges)
    return client


async def test_hidden_charges_excluded_everywhere():
    from payments_assistant.core.stripe_live import LiveOwnerGateway
    from payments_assistant.core.stripe_types import StripeObjectNotFound

    charges = [
        charge(id="ch_visible", created=T0),
        charge(id="ch_hidden", created=T0, metadata={"pa_hidden": "1"}),
        charge(id="ch_other", created=T0, customer="cus_acme"),
    ]
    client = _charge_client(charges)
    owner = LiveOwnerGateway(client)  # type: ignore[arg-type]
    start = datetime.fromtimestamp(T0 - 60, UTC)
    end = datetime.fromtimestamp(T0 + 60, UTC)
    assert {p.id for p in await owner.list_payments(start=start, end=end)} == {
        "ch_visible",
        "ch_other",
    }
    with pytest.raises(StripeObjectNotFound):
        await owner.get_payment("ch_hidden")

    mine = LiveCustomerGateway(client, "cus_maya")  # type: ignore[arg-type]
    assert [p.id for p in await mine.list_payments()] == ["ch_visible"]
