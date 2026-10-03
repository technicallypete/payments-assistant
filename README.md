# Payments Assistant

An AI assistant for a small business's Stripe account:

- **Owner web app**: a chat that summarizes the day's payments and carries out natural-language
  commands ("Refund Maya's last payment") with a confirm step.
- **Customer Telegram bot**: external customers see and pay only their own invoices. Payments of
  $2,000 or more are handed off to the owner.

> Work in progress. See `docs/v0/` for the spec, plan, and progress.

## Quick start (Docker only)

Host requirements: Docker (Compose v2) and git. Nothing else is installed on the host.

```bash
cp .env.example .env    # fill in STRIPE_SECRET_KEY, OPENROUTER_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_BOT_USERNAME
docker compose up --build
```

| URL | What |
|---|---|
| http://localhost:3010 | owner web app |
| http://localhost:8010/docs | API docs (dev only) |

Ports are defaults in `docker-compose.yml`; override with `APP_HOST_PORT`, `API_HOST_PORT`, or
`POSTGRES_HOST_PORT` in `.env`.

## Tests

```bash
docker compose run --rm api-test uv run pytest -m "not integration and not stripe and not llm_eval"  # unit
docker compose run --rm api-test uv run pytest -m integration                                         # integration
docker compose run --rm app sh -c 'bun run test && bun run lint && bun run typecheck'                 # frontend
```

Architecture, setup details, seeding, and API design notes are coming as the phases land.
