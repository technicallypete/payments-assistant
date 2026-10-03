# CLAUDE.md

AI Payments Assistant: a take-home (3–5h budget, see `docs/payments-assistant-project.md`).
The owner web chat manages a Stripe test account. A Telegram bot lets external customers see and
pay **only their own** invoices.

## Docs

- `docs/payments-assistant-project.md`: the original brief. Treat it as the requirements source of truth.
- `docs/v0/spec.md`: v0 spec covering stack, auth, data model, RLS, LLM config, and tests. Read it before
  touching auth, the DB, or LLM code.
- `docs/v0/plan.md`: phased execution plan with checkpoints, test gates, and cut lines.
- `docs/v0/goal.md`: v0 definition of done (loop goal). `docs/v0/progress.md` tracks status against it.

## Stack (see spec §2)

- `./app`: Next.js (Bun, App Router, TS strict, Tailwind). This is the **only** public edge. The
  browser calls Next route handlers only. `app/api/[...path]/route.ts` proxies to `API_URL` (FastAPI)
  with the session cookie converted to a Bearer header.
- `./api`: FastAPI + SQLAlchemy 2 (async, psycopg 3) + Alembic + Pydantic v2, managed with uv on
  Python 3.13. Package is `src/payments_assistant/`, with `core/` (no FastAPI or Telegram imports),
  `http/`, and `bot/`. Adapters import `core`, never each other.
- Compose: `postgres`, `api-migrate` (role `admin`), `api` (role `api`), `bot` (role `bot`),
  `stripe-cli` (webhook forwarder), `app`. Every service uses `env_file: [.env]`, which holds
  secrets and reviewer-facing settings only. Ports, DB, and wiring are `${VAR:-default}` in compose (host
  ports default to app 3010, api 8010, postgres 5433; container ports are fixed). Tuning knobs are
  pydantic `Settings` defaults. Per-service `DATABASE_URL` goes in `environment:`.
- LLM: `core.llm.get_chat_model(settings)` from a `provider:model` string (`LLM_MODEL`; default
  `openrouter:moonshotai/kimi-k2.6`, served via `ChatOpenAI` + OpenRouter base URL, not
  `langchain-openrouter`, which hangs). Never hard-code a model. Tests use `tests/fake_llm.py`.
- Tools: define each tool **once** in `core/tools/` (`@tool`, Pydantic input/output, `ToolContext` built
  from the authenticated actor). LangChain (`core/agents`) and MCP (`mcp/`) are thin adapters over the
  registry. Never define a tool inside an adapter.
- Owner MCP server (the bonus feature): FastMCP, streamable HTTP, mounted at `/mcp` on `api`, owner API
  keys only (`pak_…`). Owner tools only. Customers get no MCP surface.

## Development: Docker only

All development runs through Docker Compose. Host requirements are only Docker (Compose v2) and git.
Don't install or run Python, uv, Node, or bun on the host, and don't create a host `.venv` or
`node_modules`.

- Bring up: `docker compose up --build`. Source is bind-mounted with hot reload (uvicorn `--reload`,
  `bun run dev`).
- Run anything: `docker compose run --rm <service> <cmd>` for one-off commands, or
  `docker compose exec <service> <cmd>` against a running container.
- Add dependencies inside the container so the lockfiles update in the bind mount:
  `docker compose run --rm api uv add <pkg>` / `docker compose run --rm app bun add <pkg>`.
- Migrations: `docker compose run --rm api-migrate` (apply), and
  `docker compose run --rm api-migrate uv run alembic revision --autogenerate -m "<msg>"` (create).
- Seed: `docker compose run --rm api uv run python -m payments_assistant.seed`.
- Tests, lint, and type checks also run in containers (see Testing).

## Non-negotiables

- **Authority comes from the session, never the LLM.** Customer-bot tools take no customer ID. The bound
  `stripe_customer_id` is injected, and the gateway re-checks ownership of every Stripe object.
- **Customer-scoped tables use Postgres RLS** (`ENABLE` + `FORCE`), keyed on `customer_account_id`. A new
  customer-scoped table needs: the column, the `api` and `bot` policies, and explicit `bot` grants in the
  Alembic migration. All grants live in migrations (no default privileges), so dev and test DBs match.
- All bot DB access runs inside `core.scoping.customer_scope(session, account_id)` (transaction-local
  `set_config`).
- The $2,000 handoff rule (`HANDOFF_THRESHOLD_CENTS`) is enforced in `core.services`, not in prompts.
- Owner mutations follow propose → confirm → execute via `owner_actions`, with Stripe idempotency keys.
- The owner session token is httpOnly cookie → Bearer. It never reaches browser JS. Session tokens work
  only on the web API, and API keys work only on `/mcp`.
- Owner chat streams over SSE (spec §6.1). The Next proxy must pass bodies through unbuffered and forward
  aborts. Telegram never streams.
- Customer linking is by Telegram deep-link invite token only. There is no phone/contact matching.
- Customer tool input models must not contain any customer-identifying field (a registry test enforces this).
- Stripe webhooks hit `api` directly (raw body, signature verified first) and are deduped via `stripe_events`.
- Money is integer minor units. Dates resolve in `BUSINESS_TIMEZONE` (default `America/New_York`), and range
  math happens in code, not in the LLM.
- Stripe key must be test mode (`sk_test_`). Assert it at boot.

## Testing

Unit and integration tests are required for new behavior.

- Python: pytest + pytest-asyncio + httpx. Unit is `-m "not integration"` (fakes for Stripe, LLM, and
  Telegram; no network). Integration is `-m integration` (compose Postgres). Opt-in markers are `-m stripe`
  and `-m llm_eval`.
- RLS tests must connect as role `bot`, never `admin` (a superuser bypasses RLS).
- Next: Vitest (proxy and cookie handling), with msw mocking `API_URL`.

```bash
# Python (the api-test service has admin creds; integration tests create/migrate <DB_NAME>_test)
docker compose run --rm api-test uv run pytest -m "not integration and not stripe and not llm_eval"
docker compose run --rm api-test uv run pytest -m integration
docker compose run --rm api-test uv run pytest -m stripe      # opt-in, real test account
docker compose run --rm api-test uv run pytest -m llm_eval    # opt-in, costs OpenRouter credit
docker compose run --rm api-test sh -c 'uv run ruff check . && uv run ruff format --check .'
# Next
docker compose run --rm app sh -c 'bun run test && bun run lint && bun run typecheck'
```

## Git

Commit regularly (reviewers read the history). The default branch for PRs is `main`.
