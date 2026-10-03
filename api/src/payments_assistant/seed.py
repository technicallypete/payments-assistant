"""Seed a Stripe test account (and our DB) with the demo data the app expects.

    docker compose run --rm api uv run python -m payments_assistant.seed [--reset]

Idempotent: seeded customers are found by fixed email + `metadata.seed_tag` via `customers.list`
(Stripe Search is eventually consistent, so it isn't used). A customer whose activity finished
seeding is marked `seed_complete=true` and skipped on later runs; a half-seeded customer (a crash
mid-run) is deleted and re-created.

Stripe can't backdate charges, so every seeded PaymentIntent carries
`metadata.pa_occurred_at` (unix seconds). The app treats that as the business timestamp for
seeded data (see `core.stripe_gateway`). `--reset` deletes seeded customers; their charges can't be
deleted, so they're tagged `metadata.pa_hidden=1` and the app ignores them.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import stripe
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from payments_assistant.core.config import Settings, get_settings
from payments_assistant.core.db import make_engine
from payments_assistant.core.models import Owner
from payments_assistant.core.security import hash_password
from payments_assistant.core.services.accounts import upsert_from_stripe
from payments_assistant.core.services.invites import create_invite
from payments_assistant.core.stripe_types import CustomerInfo

SEED_TAG = "pa-v0"
EMAIL_SUFFIX = "pa-seed"
CURRENCY = "usd"
PM_OK = "pm_card_visa"
PM_INSUFFICIENT_FUNDS = "pm_card_chargeDeclinedInsufficientFunds"

# ------------------------------------------------------------------------------------------ plan


@dataclass(frozen=True)
class SeedCustomer:
    key: str
    name: str
    email_local: str

    def email(self, suffix: str) -> str:
        return f"{self.email_local}+{suffix}@example.com"


@dataclass(frozen=True)
class PlannedPayment:
    customer_key: str
    amount: int  # cents
    occurred_at: datetime  # aware, UTC
    description: str
    decline: bool = False
    refund_amount: int | None = None  # partial refund after success


@dataclass(frozen=True)
class PlannedInvoice:
    customer_key: str
    amount: int
    description: str
    days_until_due: int
    paid: bool = False


@dataclass(frozen=True)
class SeedPlan:
    customers: list[SeedCustomer]
    payments: list[PlannedPayment]
    invoices: list[PlannedInvoice]


CUSTOMERS = [
    SeedCustomer("maya", "Maya Chen", "maya.chen"),
    SeedCustomer("acme", "Acme Corp", "billing.acme"),
    SeedCustomer("bluebird", "Bluebird Bakery", "orders.bluebird"),
    SeedCustomer("jordan", "Jordan Lee", "jordan.lee"),
    SeedCustomer("northwind", "Northwind Traders", "ap.northwind"),
    SeedCustomer("priya", "Priya Patel", "priya.patel"),
    SeedCustomer("sam", "Sam Rivera", "sam.rivera"),
]

# Today: 8 successes totalling $4,280 (the brief's example) + 2 insufficient-funds declines.
# Maya's payment is last, so "refund Maya's last payment" targets a normal, unrefunded charge.
_TODAY: list[tuple[str, int, str, bool]] = [
    ("acme", 95_000, "Website retainer (October)", False),
    ("bluebird", 42_000, "Wholesale order #1182", False),
    ("northwind", 23_000, "Freight consulting", True),
    ("northwind", 68_000, "Freight consulting", False),
    ("priya", 31_000, "Brand workshop", False),
    ("jordan", 54_000, "Photography package", False),
    ("sam", 15_000, "Coaching session", True),
    ("sam", 22_500, "Coaching sessions (3)", False),
    ("acme", 74_000, "Landing page sprint", False),
    ("maya", 41_500, "Logo refresh", False),
]
# (day offset from the window start, local time, customer, cents, description)
_YESTERDAY = [
    (0, time(10, 20), "maya", 12_000, "Business cards"),
    (0, time(12, 5), "bluebird", 38_000, "Wholesale order #1176"),
    (0, time(15, 40), "priya", 27_500, "Copywriting"),
    (0, time(17, 15), "jordan", 45_000, "Event photography deposit"),
]
_LAST_WEEK = [
    (0, time(9, 30), "acme", 120_000, "Website retainer (September)"),
    (1, time(11, 0), "maya", 9_500, "Social media templates"),
    (2, time(10, 45), "northwind", 88_000, "Freight consulting"),
    (2, time(16, 10), "acme", 64_000, "SEO audit"),
    (3, time(13, 20), "bluebird", 41_000, "Wholesale order #1150"),
    (4, time(14, 0), "priya", 36_000, "Brand workshop"),
    (5, time(11, 30), "jordan", 52_000, "Portrait session"),
    (6, time(15, 0), "sam", 19_500, "Coaching sessions (2)"),
]
_WEEK_BEFORE = [
    (0, time(9, 45), "acme", 98_000, "Website retainer (August)"),
    (1, time(12, 30), "maya", 11_000, "Flyer design"),
    (2, time(10, 0), "bluebird", 39_000, "Wholesale order #1121"),
    (3, time(15, 15), "northwind", 72_000, "Freight consulting"),
    (4, time(11, 10), "priya", 28_000, "Copywriting"),
    (5, time(14, 40), "jordan", 47_000, "Headshots"),
]
# The Northwind last-week charge gets a partial refund.
_REFUND = ("northwind", 88_000, 5_000)

INVOICES = [
    PlannedInvoice("acme", 120_000, "Q4 maintenance retainer", days_until_due=14),
    PlannedInvoice("acme", 350_000, "Website redesign (phase 2)", days_until_due=30),
    PlannedInvoice("maya", 18_000, "Brand guidelines PDF", days_until_due=10),
    PlannedInvoice("jordan", 200_000, "Wedding photography balance", days_until_due=21),
    PlannedInvoice("bluebird", 26_000, "Menu redesign", days_until_due=14, paid=True),
]


def _local(d: date, t: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, t, tzinfo=tz).astimezone(UTC)


def build_plan(today: date, tz: ZoneInfo, now: datetime) -> SeedPlan:
    """Pure: everything the seed will create, with business timestamps. No I/O."""
    payments: list[PlannedPayment] = []

    def add_days(start: date, rows: list[tuple[int, time, str, int, str]]) -> None:
        for offset, t, key, amount, desc in rows:
            refund = _REFUND[2] if (key, amount) == _REFUND[:2] else None
            payments.append(
                PlannedPayment(
                    key,
                    amount,
                    _local(start + timedelta(days=offset), t, tz),
                    desc,
                    refund_amount=refund,
                )
            )

    last_week_start = today - timedelta(days=today.weekday() + 7)  # Monday of last week
    add_days(last_week_start - timedelta(days=7), _WEEK_BEFORE)
    add_days(last_week_start, _LAST_WEEK)
    add_days(today - timedelta(days=1), _YESTERDAY)

    # Today: spread evenly between local midnight and `now`, in order, never in the future.
    start = datetime.combine(today, time(0), tzinfo=tz).astimezone(UTC)
    span = max(now - start, timedelta(seconds=len(_TODAY) + 2))
    for i, (key, amount, desc, decline) in enumerate(_TODAY, start=1):
        at = start + span * i / (len(_TODAY) + 1)
        payments.append(PlannedPayment(key, amount, min(at, now), desc, decline=decline))

    return SeedPlan(customers=list(CUSTOMERS), payments=payments, invoices=list(INVOICES))


# ------------------------------------------------------------------------------------------ Stripe


def _meta(obj: Any) -> dict[str, str]:
    """SDK 16 StripeObjects aren't dicts (no `.get`); normalize metadata to a plain dict."""
    md = getattr(obj, "metadata", None)
    return md.to_dict() if md is not None else {}


@dataclass
class SeedResult:
    customer_ids: dict[str, str] = field(default_factory=dict)  # key → cus_...
    created_customers: list[str] = field(default_factory=list)
    existing_customers: list[str] = field(default_factory=list)
    payments: int = 0
    declines: int = 0
    invoices: int = 0


class StripeSeeder:
    """Executes a SeedPlan against Stripe. `tag`/`email_suffix` are injectable for tests."""

    def __init__(
        self,
        client: stripe.StripeClient,
        *,
        tag: str = SEED_TAG,
        email_suffix: str = EMAIL_SUFFIX,
        log: Callable[[str], None] = print,
    ) -> None:
        self.client = client
        self.tag = tag
        self.email_suffix = email_suffix
        self.log = log

    # -- lookup

    def find(self, customer: SeedCustomer) -> Any | None:
        found = self.client.v1.customers.list(
            {"email": customer.email(self.email_suffix), "limit": 10}
        )
        for c in found.data:
            if _meta(c).get("seed_tag") == self.tag:
                return c
        return None

    # -- run

    def run(self, plan: SeedPlan) -> SeedResult:
        result = SeedResult()
        for customer in plan.customers:
            existing = self.find(customer)
            if existing is not None and _meta(existing).get("seed_complete") == "true":
                self.log(f"  exists   {customer.name} ({existing.id})")
                result.customer_ids[customer.key] = existing.id
                result.existing_customers.append(customer.key)
                continue
            if existing is not None:  # half-seeded by an earlier crash: start that customer over
                self.log(f"  redo     {customer.name}: earlier run didn't finish")
                self._delete_customer(existing.id)
            cus = self._create_customer(customer)
            result.customer_ids[customer.key] = cus.id
            result.created_customers.append(customer.key)
            self._seed_activity(customer, cus.id, plan, result)
            self.client.v1.customers.update(cus.id, {"metadata": {"seed_complete": "true"}})
            self.log(f"  created  {customer.name} ({cus.id})")
        return result

    def reset(self, customers: list[SeedCustomer]) -> int:
        removed = 0
        for customer in customers:
            existing = self.find(customer)
            if existing is None:
                continue
            self._delete_customer(existing.id)
            self.log(f"  deleted  {customer.name} ({existing.id})")
            removed += 1
        return removed

    # -- helpers

    def _create_customer(self, customer: SeedCustomer) -> Any:
        return self.client.v1.customers.create(
            {
                "name": customer.name,
                "email": customer.email(self.email_suffix),
                "metadata": {"seed_tag": self.tag, "seed_key": customer.key},
            }
        )

    def _seed_activity(
        self, customer: SeedCustomer, customer_id: str, plan: SeedPlan, result: SeedResult
    ) -> None:
        for p in plan.payments:
            if p.customer_key == customer.key:
                self._pay(customer_id, p, result)
        for inv in plan.invoices:
            if inv.customer_key == customer.key:
                self._invoice(customer_id, inv)
                result.invoices += 1

    def _pay(self, customer_id: str, p: PlannedPayment, result: SeedResult) -> None:
        params: dict[str, Any] = {
            "amount": p.amount,
            "currency": CURRENCY,
            "customer": customer_id,
            "description": p.description,
            "payment_method": PM_INSUFFICIENT_FUNDS if p.decline else PM_OK,
            "confirm": True,
            "automatic_payment_methods": {"enabled": True, "allow_redirects": "never"},
            # Propagates to the charge, including the failed one on a decline.
            "metadata": {
                "seed_tag": self.tag,
                "pa_occurred_at": str(int(p.occurred_at.timestamp())),
            },
        }
        if p.decline:
            try:
                self.client.v1.payment_intents.create(params)
            except stripe.CardError:
                result.declines += 1
                return
            raise RuntimeError("expected a card decline for the insufficient-funds test card")
        pi = self.client.v1.payment_intents.create(params)
        result.payments += 1
        if p.refund_amount:
            self.client.v1.refunds.create(
                {
                    "payment_intent": pi.id,
                    "amount": p.refund_amount,
                    "reason": "requested_by_customer",
                }
            )

    def _invoice(self, customer_id: str, inv: PlannedInvoice) -> None:
        invoice = self.client.v1.invoices.create(
            {
                "customer": customer_id,
                "collection_method": "send_invoice",
                "days_until_due": inv.days_until_due,
                "auto_advance": False,  # Stripe never emails or auto-finalizes seeded invoices
                "pending_invoice_items_behavior": "exclude",
                "description": inv.description,
                "metadata": {"seed_tag": self.tag},
            }
        )
        self.client.v1.invoice_items.create(
            {
                "customer": customer_id,
                "invoice": invoice.id,
                "amount": inv.amount,
                "currency": CURRENCY,
                "description": inv.description,
            }
        )
        self.client.v1.invoices.finalize_invoice(invoice.id, {"auto_advance": False})
        if inv.paid:
            # Out of band: keeps seeded charge totals exactly as planned (no undated charge).
            self.client.v1.invoices.pay(invoice.id, {"paid_out_of_band": True})

    def _delete_customer(self, customer_id: str) -> None:
        for inv in self.client.v1.invoices.list({"customer": customer_id, "limit": 100}).data:
            if inv.status == "open":
                self.client.v1.invoices.void_invoice(inv.id)
            elif inv.status == "draft":
                self.client.v1.invoices.delete(inv.id)
        # Charges can't be deleted; hide them from the app instead.
        for ch in self.client.v1.charges.list({"customer": customer_id, "limit": 100}).data:
            self.client.v1.charges.update(ch.id, {"metadata": {"pa_hidden": "1"}})
        self.client.v1.customers.delete(customer_id)

    def amount_owed(self, customer_id: str) -> int:
        open_invoices = self.client.v1.invoices.list(
            {"customer": customer_id, "status": "open", "limit": 100}
        )
        return sum(i.amount_remaining for i in open_invoices.data)


# ------------------------------------------------------------------------------------------ DB


@dataclass(frozen=True)
class DbCustomerOutput:
    name: str
    stripe_id: str
    invite_url: str


async def seed_database(
    settings: Settings,
    customers: list[CustomerInfo],
    *,
    database_url: str | None = None,
) -> list[DbCustomerOutput]:
    """Upsert accounts, mint a Telegram invite each, upsert the owner. One transaction."""
    engine = make_engine(database_url or settings.database_url)
    out: list[DbCustomerOutput] = []
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session, session.begin():
            for customer in customers:
                account = await upsert_from_stripe(session, customer)
                invite = await create_invite(
                    session,
                    customer_account_id=account.id,
                    bot_username=settings.telegram_bot_username,
                    ttl_days=settings.invite_ttl_days,
                )
                out.append(DbCustomerOutput(customer.name, customer.id, invite.url))
            if settings.owner_email and settings.owner_password:
                await upsert_owner(session, settings.owner_email, settings.owner_password)
    finally:
        await engine.dispose()
    return out


async def upsert_owner(session, email: str, password: str) -> Owner:
    owner = (
        await session.execute(select(Owner).where(func.lower(Owner.email) == email.lower()))
    ).scalar_one_or_none()
    if owner is None:
        owner = Owner(email=email, password_hash=hash_password(password))
        session.add(owner)
    else:
        owner.password_hash = hash_password(password)
    await session.flush()
    return owner


# ------------------------------------------------------------------------------------------ CLI


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Seed the Stripe test account and the database.")
    parser.add_argument("--reset", action="store_true", help="delete seeded customers first")
    args = parser.parse_args(argv)

    settings = get_settings()  # refuses non-test keys
    client = stripe.StripeClient(settings.stripe_secret_key)
    seeder = StripeSeeder(client)
    tz = ZoneInfo(settings.business_timezone)
    now = datetime.now(UTC)

    if args.reset:
        print("Resetting seeded customers...")
        print(f"  removed {seeder.reset(CUSTOMERS)} customer(s)")

    print(f"Seeding Stripe test account (tag {SEED_TAG}, timezone {tz.key})...")
    plan = build_plan(now.astimezone(tz).date(), tz, now)
    result = seeder.run(plan)
    print(
        f"  {len(result.created_customers)} customer(s) created, "
        f"{len(result.existing_customers)} already existed; "
        f"{result.payments} payments, {result.declines} declines, "
        f"{result.invoices} invoices created"
    )

    infos = [
        CustomerInfo(id=result.customer_ids[c.key], name=c.name, email=c.email(EMAIL_SUFFIX))
        for c in plan.customers
    ]
    rows = asyncio.run(seed_database(settings, infos))

    print(
        "\nCustomers (Telegram invite links are new each run; older links stay valid until expiry)"
    )
    print(f"  {'Customer':<20} {'Stripe id':<22} {'Owes':>11}  Telegram invite")
    for row in rows:
        owes = _money(seeder.amount_owed(row.stripe_id))
        print(f"  {row.name:<20} {row.stripe_id:<22} {owes:>11}  {row.invite_url}")

    app_url = os.environ.get("APP_ORIGIN", "http://localhost:3010")
    print(f"\nOwner login: {settings.owner_email or '(OWNER_EMAIL not set)'} at {app_url}")


if __name__ == "__main__":
    main()
