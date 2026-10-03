"""Manage owner MCP API keys from the command line.

    docker compose run --rm api uv run python -m payments_assistant.keys create --name "Desktop"
    docker compose run --rm api uv run python -m payments_assistant.keys list
    docker compose run --rm api uv run python -m payments_assistant.keys revoke <key_id>

Runs as the `api` DB role (DATABASE_URL). The owner defaults to OWNER_EMAIL.
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from payments_assistant.core.client_config import client_snippets, format_snippets
from payments_assistant.core.config import get_settings
from payments_assistant.core.db import make_engine
from payments_assistant.core.models import Owner
from payments_assistant.core.services import api_keys


async def _owner(session, email: str) -> Owner:
    owner = (
        await session.execute(select(Owner).where(func.lower(Owner.email) == email.lower()))
    ).scalar_one_or_none()
    if owner is None:
        raise SystemExit(f"No owner with email {email!r}. Run the seed first.")
    return owner


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    email = args.owner_email or settings.owner_email
    if not email:
        raise SystemExit("Pass --owner-email or set OWNER_EMAIL.")
    engine = make_engine(settings.database_url)
    now = datetime.now(UTC)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s, s.begin():
            owner = await _owner(s, email)
            if args.command == "create":
                new = await api_keys.create(s, owner_id=owner.id, name=args.name, now=now)
                print(f"Created key {new.id} ({new.name}).")
                print("Copy it now; it will not be shown again:\n")
                print(f"  {new.key}\n")
                print(format_snippets(client_snippets(new.key)))
            elif args.command == "list":
                rows = await api_keys.list_keys(s, owner_id=owner.id)
                if not rows:
                    print("No keys.")
                for k in rows:
                    state = f"revoked {k.revoked_at:%Y-%m-%d}" if k.revoked_at else "active"
                    used = f"{k.last_used_at:%Y-%m-%d %H:%M}" if k.last_used_at else "never"
                    print(f"{k.id}  {k.key_prefix}…  {k.name!r:<24} {state:<20} last used {used}")
            elif args.command == "revoke":
                if not await api_keys.revoke(s, owner_id=owner.id, key_id=args.key_id, now=now):
                    print("No such active key.", file=sys.stderr)
                    return 1
                print(f"Revoked {args.key_id}.")
    finally:
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage owner MCP API keys.")
    parser.add_argument("--owner-email", help="defaults to OWNER_EMAIL")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="create a key and print client config")
    create.add_argument("--name", default="API key")
    sub.add_parser("list", help="list keys")
    revoke = sub.add_parser("revoke", help="revoke a key")
    revoke.add_argument("key_id", type=UUID)
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
