"""Customer scoping for the bot's Postgres role.

Every query the bot runs against customer data happens inside `customer_scope`, which opens a
transaction and sets `app.customer_account_id` transaction-locally. RLS policies compare against
that setting, so:
- outside a scope the setting is unset → policies match nothing → zero rows (fail closed);
- the setting resets at COMMIT/ROLLBACK, so a pooled connection can't carry it to the next user.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SET_SCOPE = text("SELECT set_config('app.customer_account_id', :account_id, true)")


@asynccontextmanager
async def customer_scope(session: AsyncSession, account_id: UUID) -> AsyncIterator[AsyncSession]:
    """Run the block in one transaction scoped to `account_id`. Commits on success."""
    if not isinstance(account_id, UUID):
        raise TypeError("account_id must be a UUID")  # never let a raw string reach the policy
    async with session.begin():
        await session.execute(_SET_SCOPE, {"account_id": str(account_id)})
        yield session
