"""Seed executor against the real Stripe test account (opt-in: `pytest -m stripe`).

Uses a unique seed tag and email suffix per run plus a tiny plan, so it never collides with (or
pollutes the totals of) the real demo seed, and cleans up through the reset path.
"""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import stripe
from sqlalchemy import text

from payments_assistant.core.config import Settings
from payments_assistant.core.stripe_types import CustomerInfo
from payments_assistant.seed import (
    PlannedInvoice,
    PlannedPayment,
    SeedCustomer,
    SeedPlan,
    StripeSeeder,
    seed_database,
)
from tests.db_fixtures import role_url

pytestmark = [pytest.mark.stripe, pytest.mark.integration]

KEY = os.environ.get("STRIPE_SECRET_KEY", "")
if not KEY.startswith("sk_test_") or "replace_me" in KEY:
    pytest.skip("needs a real STRIPE_SECRET_KEY (sk_test_...)", allow_module_level=True)


@pytest.fixture
def seeder():
    run = uuid.uuid4().hex[:8]
    s = StripeSeeder(
        stripe.StripeClient(KEY),
        tag=f"pa-test-{run}",
        email_suffix=f"pa-test-{run}",
        log=lambda _: None,
    )
    yield s
    s.reset(PLAN.customers)  # always clean up, even on failure


NOW = datetime.now(UTC)
PLAN = SeedPlan(
    customers=[SeedCustomer("a", "Test Alpha", "alpha"), SeedCustomer("b", "Test Beta", "beta")],
    payments=[
        PlannedPayment("a", 1_500, NOW - timedelta(days=8), "alpha ok", refund_amount=500),
        PlannedPayment("a", 2_500, NOW - timedelta(hours=1), "alpha declined", decline=True),
        PlannedPayment("b", 3_000, NOW - timedelta(minutes=5), "beta ok"),
    ],
    invoices=[
        PlannedInvoice("a", 4_200, "alpha open", days_until_due=7),
        PlannedInvoice("b", 1_100, "beta paid", days_until_due=7, paid=True),
    ],
)


def test_seed_is_idempotent_and_resettable(seeder):
    first = seeder.run(PLAN)
    assert sorted(first.created_customers) == ["a", "b"]
    assert (first.payments, first.declines, first.invoices) == (2, 1, 2)

    second = seeder.run(PLAN)
    assert second.created_customers == []
    assert sorted(second.existing_customers) == ["a", "b"]
    assert (second.payments, second.declines, second.invoices) == (0, 0, 0)
    assert second.customer_ids == first.customer_ids

    client = seeder.client
    alpha = first.customer_ids["a"]
    charges = client.v1.charges.list({"customer": alpha, "limit": 10}).data
    failed = [c for c in charges if c.status == "failed"]
    ok = [c for c in charges if c.status == "succeeded"]
    assert len(failed) == 1 and len(ok) == 1
    # Backdating metadata must reach the charge, including the declined one.
    for c in charges:
        assert c.metadata.to_dict()["pa_occurred_at"].isdigit()
    assert failed[0].outcome.reason == "insufficient_funds" or (
        failed[0].failure_code == "card_declined"
    )
    assert ok[0].amount_refunded == 500

    assert seeder.amount_owed(alpha) == 4_200
    assert seeder.amount_owed(first.customer_ids["b"]) == 0

    # Reset deletes the customers and hides their (undeletable) charges.
    assert seeder.reset(PLAN.customers) == 2
    assert all(seeder.find(c) is None for c in PLAN.customers)
    for c in client.v1.charges.list({"customer": alpha, "limit": 10}).data:
        assert c.metadata.to_dict().get("pa_hidden") == "1"


async def test_seed_database_upserts_accounts_invites_and_owner(migrated_db, admin_engine):
    settings = Settings(
        stripe_secret_key="sk_test_unit",
        telegram_bot_username="pa_test_bot",
        owner_email="Owner@Example.com",
        owner_password="correct horse battery staple",
    )
    run = uuid.uuid4().hex[:10]
    customers = [
        CustomerInfo(id=f"cus_live{run}a", name="Alpha", email="a@example.com"),
        CustomerInfo(id=f"cus_live{run}b", name="Beta", email=None),
    ]
    url = role_url("api")
    rows = await seed_database(settings, customers, database_url=url)
    again = await seed_database(settings, customers, database_url=url)

    assert [r.stripe_id for r in rows] == [c.id for c in customers]
    assert all(r.invite_url.startswith("https://t.me/pa_test_bot?start=") for r in rows)
    assert rows[0].invite_url != again[0].invite_url  # re-minted each run

    async with admin_engine.connect() as conn:
        accounts = (
            await conn.execute(
                text("select count(*) from customer_accounts where stripe_customer_id like :p"),
                {"p": f"cus_live{run}%"},
            )
        ).scalar_one()
        invites = (
            await conn.execute(
                text(
                    "select count(*) from customer_invites i join customer_accounts a "
                    "on a.id = i.customer_account_id where a.stripe_customer_id like :p"
                ),
                {"p": f"cus_live{run}%"},
            )
        ).scalar_one()
        owners = (
            await conn.execute(
                text("select count(*) from owners where lower(email) = 'owner@example.com'")
            )
        ).scalar_one()
    assert accounts == 2  # upserted, not duplicated
    assert invites == 4
    assert owners == 1
