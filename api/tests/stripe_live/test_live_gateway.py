"""Live gateways against the real Stripe TEST account (opt-in: `pytest -m stripe`).

Creates throwaway customers tagged `metadata.pa_test=1` and cleans them up.
"""

import os
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
import stripe

from payments_assistant.core.stripe_live import LiveCustomerGateway, LiveOwnerGateway
from payments_assistant.core.stripe_types import ForeignObjectError

pytestmark = [
    pytest.mark.stripe,
    pytest.mark.skipif(
        not os.environ.get("STRIPE_SECRET_KEY", "").startswith("sk_test_"),
        reason="needs a Stripe test-mode key",
    ),
]


@pytest.fixture
def client() -> stripe.StripeClient:
    return stripe.StripeClient(os.environ["STRIPE_SECRET_KEY"])


@pytest.fixture
async def two_customers(client):
    tag = uuid.uuid4().hex[:8]
    made = [
        await client.v1.customers.create_async(
            {
                "name": f"PA Test {who} {tag}",
                # send_invoice invoices require a customer email
                "email": f"pa-test-{who.lower()}-{tag}@example.com",
                "metadata": {"pa_test": "1"},
            }
        )
        for who in ("Mine", "Theirs")
    ]
    yield made
    for c in made:
        await client.v1.customers.delete_async(c.id)


async def test_invoice_ownership_and_payment_link(client, two_customers):
    mine, theirs = two_customers
    owner = LiveOwnerGateway(client)
    key = uuid.uuid4().hex

    inv = await owner.create_invoice(
        customer_id=mine.id,
        amount=1_234,
        currency="usd",
        description="PA live gateway test",
        due_date=date.today() + timedelta(days=7),
        idempotency_key=key,
    )
    try:
        assert inv.status == "open"
        assert inv.amount_remaining == 1_234
        assert inv.hosted_invoice_url

        # Same idempotency key → same invoice, not a second one.
        again = await owner.create_invoice(
            customer_id=mine.id,
            amount=1_234,
            currency="usd",
            description="PA live gateway test",
            due_date=date.today() + timedelta(days=7),
            idempotency_key=key,
        )
        assert again.id == inv.id

        my_gw = LiveCustomerGateway(client, mine.id)
        assert inv.id in {i.id for i in await my_gw.list_invoices(open_only=True)}
        assert (await my_gw.get_invoice(inv.id)).id == inv.id

        their_gw = LiveCustomerGateway(client, theirs.id)
        assert await their_gw.list_invoices() == []
        with pytest.raises(ForeignObjectError):
            await their_gw.get_invoice(inv.id)
        with pytest.raises(ForeignObjectError):
            await their_gw.get_invoice("in_doesnotexist000")

        found = await owner.search_customers(mine.name)  # "PA Test Mine <tag>"
        assert [c.id for c in found] == [mine.id]

        link = await owner.create_payment_link(
            amount=500, currency="usd", description="PA test link", idempotency_key=uuid.uuid4().hex
        )
        assert link.id.startswith("plink_") and link.url.startswith("https://")
    finally:
        await client.v1.invoices.void_invoice_async(inv.id)


async def test_charges_declines_and_refund(client, two_customers):
    mine, _ = two_customers
    owner = LiveOwnerGateway(client)
    start = datetime.now(UTC) - timedelta(minutes=5)

    ok = await client.v1.payment_intents.create_async(
        {
            "amount": 2_500,
            "currency": "usd",
            "customer": mine.id,
            "payment_method": "pm_card_visa",
            "automatic_payment_methods": {"enabled": True, "allow_redirects": "never"},
            "confirm": True,
        }
    )
    with pytest.raises(stripe.CardError):
        await client.v1.payment_intents.create_async(
            {
                "amount": 9_900,
                "currency": "usd",
                "customer": mine.id,
                "payment_method": "pm_card_chargeDeclinedInsufficientFunds",
                "automatic_payment_methods": {"enabled": True, "allow_redirects": "never"},
                "confirm": True,
            }
        )

    payments = await owner.list_payments(
        start=start, end=datetime.now(UTC) + timedelta(minutes=5), customer_id=mine.id
    )
    by_status = {p.status: p for p in payments}
    assert by_status["succeeded"].amount == 2_500
    declined = by_status["failed"]
    assert declined.failure_code == "card_declined"
    assert declined.decline_code == "insufficient_funds"
    assert declined.customer_name == mine.name

    charge_id = ok.latest_charge
    refund = await owner.refund(charge_id=charge_id, amount=1_000, idempotency_key=uuid.uuid4().hex)
    assert refund.amount == 1_000
    assert (await owner.get_payment(charge_id)).amount_refunded == 1_000

    mine_only = await LiveCustomerGateway(client, mine.id).list_payments()
    assert {p.customer_id for p in mine_only} == {mine.id}
