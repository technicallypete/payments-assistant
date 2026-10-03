"""Stripe webhook: signature, idempotency, effects, and notify-after-commit."""

import hashlib
import hmac
import json
import time
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from payments_assistant.core.models import DailySummary, Handoff, PaymentRequest, StripeEvent

pytestmark = pytest.mark.integration
SECRET = "whsec_test_secret"


def signed(payload: dict, secret: str = SECRET, ts: int | None = None) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    t = ts or int(time.time())
    sig = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return body, {"stripe-signature": f"t={t},v1={sig}", "content-type": "application/json"}


def event(kind: str, obj: dict, eid: str | None = None) -> dict:
    return {
        "id": eid or f"evt_{uuid4().hex[:16]}",
        "object": "event",
        "type": kind,
        "livemode": False,
        "data": {"object": obj},
    }


@pytest.fixture
def notes(harness):
    sent: list[tuple[int, str]] = []

    async def notify(chat_id, text_):
        sent.append((chat_id, text_))

    harness.state.notify = notify
    harness.state.settings.stripe_webhook_secret = SECRET
    return sent


async def _customer_with_link(admin_engine, *, linked=True):
    aid, cus, inv = uuid4(), f"cus_{uuid4().hex[:10]}", f"in_{uuid4().hex[:10]}"
    chat = uuid4().int % 9_000_000_000 + 1
    async with admin_engine.begin() as c:
        await c.execute(
            text(
                "insert into customer_accounts (id, stripe_customer_id, display_name) "
                "values (:a, :c, 'Maya')"
            ),
            {"a": aid, "c": cus},
        )
        await c.execute(
            text(
                "insert into payment_requests "
                "(customer_account_id, stripe_invoice_id, amount, currency, hosted_url) "
                "values (:a, :i, 18000, 'usd', 'https://invoice.stripe.com/i/x')"
            ),
            {"a": aid, "i": inv},
        )
        if linked:
            await c.execute(
                text(
                    "insert into telegram_identities "
                    "(customer_account_id, telegram_user_id, telegram_chat_id) "
                    "values (:a, :u, :u)"
                ),
                {"a": aid, "u": chat},
            )
    return aid, cus, inv, chat


async def _status(admin_engine, inv):
    async with async_sessionmaker(admin_engine)() as s:
        return (
            await s.execute(
                select(PaymentRequest.status).where(PaymentRequest.stripe_invoice_id == inv)
            )
        ).scalar_one()


def invoice_obj(inv, cus, amount_paid=18000, number="MAYA-0007"):
    return {
        "id": inv,
        "object": "invoice",
        "customer": cus,
        "amount_paid": amount_paid,
        "currency": "usd",
        "number": number,
    }


async def test_bad_or_missing_signature_rejected(client, notes):
    body, headers = signed(event("invoice.paid", {"id": "in_x"}), secret="whsec_wrong")
    assert (await client.post("/webhooks/stripe", content=body, headers=headers)).status_code == 400
    r = await client.post(
        "/webhooks/stripe", content=body, headers={"content-type": "application/json"}
    )
    assert r.status_code == 400


async def test_stale_timestamp_rejected(client, notes):
    body, headers = signed(event("invoice.paid", {"id": "in_x"}), ts=int(time.time()) - 3600)
    assert (await client.post("/webhooks/stripe", content=body, headers=headers)).status_code == 400


async def test_tampered_body_rejected(client, notes):
    body, headers = signed(event("invoice.paid", {"id": "in_x"}))
    tampered = body.replace(b"in_x", b"in_y")
    assert (
        await client.post("/webhooks/stripe", content=tampered, headers=headers)
    ).status_code == 400


async def test_no_secret_configured_is_503(client, harness):
    harness.state.settings.stripe_webhook_secret = ""
    harness.state.settings.stripe_webhook_secret_file = (
        harness.state.settings.stripe_webhook_secret_file.with_name("nope")
    )
    body, headers = signed(event("invoice.paid", {"id": "in_x"}))
    assert (await client.post("/webhooks/stripe", content=body, headers=headers)).status_code == 503


async def test_invoice_paid_marks_request_paid_and_notifies(client, notes, admin_engine):
    _, cus, inv, chat = await _customer_with_link(admin_engine)
    body, headers = signed(event("invoice.paid", invoice_obj(inv, cus)))
    r = await client.post("/webhooks/stripe", content=body, headers=headers)
    assert r.status_code == 200 and r.json() == {
        "received": True,
        "duplicate": False,
        "handled": True,
    }
    assert await _status(admin_engine, inv) == "paid"
    assert notes == [(chat, "Payment received ✅ Thanks! MAYA-0007 ($180.00) is paid in full.")]


async def test_duplicate_event_processed_once(client, notes, admin_engine):
    _, cus, inv, _ = await _customer_with_link(admin_engine)
    ev = event("invoice.paid", invoice_obj(inv, cus))
    for _ in range(3):
        body, headers = signed(ev)
        r = await client.post("/webhooks/stripe", content=body, headers=headers)
        assert r.status_code == 200
    assert r.json()["duplicate"] is True
    assert len(notes) == 1
    async with async_sessionmaker(admin_engine)() as s:
        rows = (
            (await s.execute(select(StripeEvent).where(StripeEvent.event_id == ev["id"])))
            .scalars()
            .all()
        )
    assert len(rows) == 1 and rows[0].processed_at is not None


async def test_unlinked_customer_gets_no_notification(client, notes, admin_engine):
    _, cus, inv, _ = await _customer_with_link(admin_engine, linked=False)
    body, headers = signed(event("invoice.paid", invoice_obj(inv, cus)))
    await client.post("/webhooks/stripe", content=body, headers=headers)
    assert await _status(admin_engine, inv) == "paid"
    assert notes == []


async def test_payment_failed_marks_failed_and_tells_customer(client, notes, admin_engine):
    _, cus, inv, chat = await _customer_with_link(admin_engine)
    body, headers = signed(event("invoice.payment_failed", invoice_obj(inv, cus, amount_paid=0)))
    await client.post("/webhooks/stripe", content=body, headers=headers)
    assert await _status(admin_engine, inv) == "failed"
    assert notes and notes[0][0] == chat and "didn't go through" in notes[0][1]


async def test_voided_invoice(client, notes, admin_engine):
    _, cus, inv, _ = await _customer_with_link(admin_engine)
    body, headers = signed(event("invoice.voided", invoice_obj(inv, cus)))
    await client.post("/webhooks/stripe", content=body, headers=headers)
    assert await _status(admin_engine, inv) == "void"


async def test_paid_invoice_resolves_open_handoff(client, notes, admin_engine):
    aid, cus, _, _ = await _customer_with_link(admin_engine)
    big = f"in_{uuid4().hex[:10]}"
    async with admin_engine.begin() as c:
        await c.execute(
            text(
                "insert into handoffs "
                "(customer_account_id, reason, stripe_invoice_id, amount, currency) "
                "values (:a, 'amount_over_threshold', :i, 250000, 'usd')"
            ),
            {"a": aid, "i": big},
        )
    body, headers = signed(event("invoice.paid", invoice_obj(big, cus, amount_paid=250000)))
    await client.post("/webhooks/stripe", content=body, headers=headers)
    async with async_sessionmaker(admin_engine)() as s:
        h = (await s.execute(select(Handoff).where(Handoff.stripe_invoice_id == big))).scalar_one()
    assert h.status == "resolved" and h.resolved_at is not None


async def test_money_events_invalidate_todays_summary(client, notes, admin_engine, harness):
    from zoneinfo import ZoneInfo

    from payments_assistant.core.timeutil import local_today

    tz = harness.state.settings.business_timezone
    day = local_today(harness.state.clock(), ZoneInfo(tz))
    async with admin_engine.begin() as c:
        await c.execute(
            text("delete from daily_summaries where summary_date = :d and timezone = :tz"),
            {"d": day, "tz": tz},
        )
        await c.execute(
            text(
                "insert into daily_summaries (summary_date, timezone, stats, summary, llm_model) "
                "values (:d, :tz, '{}', 'old', 'x')"
            ),
            {"d": day, "tz": tz},
        )
    body, headers = signed(event("charge.refunded", {"id": "ch_x", "object": "charge"}))
    await client.post("/webhooks/stripe", content=body, headers=headers)
    async with async_sessionmaker(admin_engine)() as s:
        left = (await s.execute(select(DailySummary).where(DailySummary.summary_date == day))).all()
    assert left == []


async def test_unknown_event_recorded_but_ignored(client, notes):
    body, headers = signed(event("customer.created", {"id": "cus_x", "object": "customer"}))
    r = await client.post("/webhooks/stripe", content=body, headers=headers)
    assert r.status_code == 200 and r.json()["handled"] is False


async def test_notify_failure_does_not_fail_webhook(client, harness, admin_engine):
    harness.state.settings.stripe_webhook_secret = SECRET

    async def boom(chat_id, text_):
        raise RuntimeError("telegram down")

    harness.state.notify = boom
    _, cus, inv, _ = await _customer_with_link(admin_engine)
    body, headers = signed(event("invoice.paid", invoice_obj(inv, cus)))
    assert (await client.post("/webhooks/stripe", content=body, headers=headers)).status_code == 200
    assert await _status(admin_engine, inv) == "paid"


async def test_webhook_needs_no_owner_auth_but_proxy_cannot_reach_it(client, notes):
    # Owner auth isn't required (Stripe signs instead); the Next allowlist blocks the path.
    body, headers = signed(event("customer.created", {"id": "cus_x"}))
    assert (await client.post("/webhooks/stripe", content=body, headers=headers)).status_code == 200


async def test_real_world_payload_with_decimal_fields(client, notes, admin_engine):
    """Live Stripe events carry `*_decimal` strings (e.g. line prices); the SDK parses those as
    Decimal, which once broke JSONB storage with a 500."""
    _, cus, inv, _ = await _customer_with_link(admin_engine)
    obj = invoice_obj(inv, cus) | {
        "lines": {"data": [{"price": {"unit_amount": 500, "unit_amount_decimal": "500"}}]},
        "amount_decimal": "18000.0",
    }
    body, headers = signed(event("invoice.paid", obj))
    r = await client.post("/webhooks/stripe", content=body, headers=headers)
    assert r.status_code == 200
    assert await _status(admin_engine, inv) == "paid"
