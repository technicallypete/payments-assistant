"""Customer accounts: our tenant boundary, 1:1 with a Stripe customer."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from payments_assistant.core.models import CustomerAccount
from payments_assistant.core.stripe_types import CustomerInfo


async def upsert_from_stripe(session: AsyncSession, customer: CustomerInfo) -> CustomerAccount:
    """Create or refresh the account for a Stripe customer (owner/seed side; role `api`)."""
    stmt = (
        insert(CustomerAccount)
        .values(stripe_customer_id=customer.id, display_name=customer.name, email=customer.email)
        .on_conflict_do_update(
            index_elements=[CustomerAccount.stripe_customer_id],
            set_={"display_name": customer.name, "email": customer.email},
        )
    )
    await session.execute(stmt)
    return (
        await session.execute(
            select(CustomerAccount).where(CustomerAccount.stripe_customer_id == customer.id)
        )
    ).scalar_one()
