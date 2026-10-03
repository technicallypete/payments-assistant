"""Stripe webhook endpoint. Called by `stripe listen` on the compose network (not via the Next
proxy), so the body arrives byte-for-byte as Stripe signed it."""

import json
import logging

import stripe
from fastapi import APIRouter, HTTPException, Request, status

from payments_assistant.core.services import webhooks
from payments_assistant.http.deps import State

logger = logging.getLogger(__name__)
router = APIRouter(tags=["webhooks"], include_in_schema=False)


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, state: State) -> dict[str, object]:
    secret = state.settings.webhook_secret()
    if not secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Webhook secret not configured.")
    payload = await request.body()  # raw bytes: verify BEFORE parsing anything
    try:
        stripe.Webhook.construct_event(payload, request.headers.get("stripe-signature"), secret)
    except (ValueError, stripe.SignatureVerificationError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid signature.") from exc

    # Work from the verified raw JSON, not the SDK object: the SDK turns `*_decimal` fields into
    # Python Decimals, which can't be stored as JSONB (seen live with invoice.paid events).
    data = json.loads(payload)
    async with state.sessionmaker() as session, session.begin():
        outcome = await webhooks.process_event(
            session, data, now=state.clock(), timezone=state.settings.business_timezone
        )
    # After commit: tell linked customers (best effort; never fail the webhook over Telegram).
    for note in outcome.notifications:
        try:
            await state.notify(note.chat_id, note.text)
        except Exception:
            logger.exception("telegram notify failed for event %s", data["id"])
    return {"received": True, "duplicate": outcome.duplicate, "handled": outcome.handled}
