# Payments Assistant — v0 Execution Plan

Implements `docs/v0/spec.md` (r4). Each phase ends green (tests pass, `docker compose up` healthy)
with at least one commit. Reviewers read the history, so commit at every checkpoint marked ✅.

Budget: the brief says 3–5h. This plan is ordered so that **stopping after any phase leaves a working,
submittable app**. Required features come before the bonus. Time estimates are for focused work with AI
assistance.

---

## Phase 0 — Repo hygiene (10 min)

- Rename the default branch `master` → `main`. Add `.gitignore` (Python, Node, `.env`, `__pycache__`,
  `node_modules`, `.next`).
- Commit `docs/` and `CLAUDE.md` (spec + plan). ✅

## Phase 1 — Skeleton: compose, Postgres roles, FastAPI, Next (45 min)

Skills: `docker-setup` (adapted), `nextjs-setup` (adapted). Follow the `bootstrap-fullstack` order:
backend → frontend → compose → reconcile root.

1. `api/`: create the uv project, the `src/payments_assistant/{core,http,bot,mcp}` layout, and a FastAPI
   app with `/health`. Add pydantic-settings `Settings` that reads every env var in spec §3.4 and asserts
   `sk_test_`. Configure pytest + pytest-asyncio with the `integration`, `stripe`, and `llm_eval` markers.
   Add ruff.
2. `app/`: `bun create next-app` (TS, App Router, Tailwind, ESLint), Vitest + msw, and a placeholder page.
3. `docker/Dockerfile.api` (python:3.13-slim + uv), `docker/Dockerfile.app` (oven/bun:1), and
   `postgres/init/01-roles.sh` (reads `DB_*_PASSWORD` from env) with roles `admin`/`api`/`bot`/`reporter`. `api` goes in default
   privileges and `bot` does not.
4. `docker-compose.yml`: `postgres`, `api-migrate`, `api`, `bot` (stub loop), `stripe-cli`, `app`. Every
   service uses `env_file: [.env]`. Ports, DB, and wiring use `${VAR:-default}` per spec §3.4, and per-service `DATABASE_URL` goes in `environment:`. Add healthchecks
   and `depends_on` conditions, plus the `stripe-cli` → shared volume `whsec` file.
5. `.env.example` holds secrets and reviewer-facing settings only (already written). Write a root `README.md` stub with the one-command bring-up.

**Done when:** nothing is installed on the host beyond Docker and git, `docker compose up --build` has everything healthy, `curl localhost:8010/health` returns 200, the
Next page loads, `stripe-cli` has written `/run/stripe/whsec`, and both test suites run (empty). ✅

## Phase 2 — Database: schema, RLS, definer functions (45 min)

1. Alembic env (async). Migration `0001`: every table in spec §5 with constraints (the conversation XOR
   check, partial unique indexes, the `messages.customer_account_id` consistency trigger).
2. Migration `0002`: RLS `ENABLE`/`FORCE`, `api` and `bot` policies, explicit `bot` grants per the §5.4
   matrix, revoke UPDATE/DELETE on `messages`/`audit_log`, and the definer functions
   `redeem_customer_invite` and `resolve_telegram_identity` (owned by `admin`, pinned `search_path`,
   `EXECUTE` granted to `bot`).
3. `core/db.py` (engines per role in tests), `core/models.py`, `core/scoping.py` (`customer_scope`).
4. **Tests (integration):** the full RLS suite from spec §8, connected as `bot`. This is the
   security-load-bearing work, so finish it before moving on. ✅

## Phase 3 — Core domain + Stripe gateways + seed (50 min)

1. `core/stripe_gateway.py`: `OwnerStripeGateway` (list/search payments, charges, invoices, customers;
   refund; create/finalize invoice; payment link) and `CustomerStripeGateway(stripe_customer_id)`
   (open invoices, payment history, `hosted_invoice_url`; checks `obj.customer` on every object). Wrap the
   sync `stripe` SDK with `asyncio.to_thread`.
2. `core/services/`: date-range resolution in `BUSINESS_TIMEZONE`, payment aggregates for summaries and
   week-over-week, `request_customer_payment` (threshold → handoff), invites, the `owner_actions` lifecycle
   (propose/confirm/execute/expire with idempotency keys), audit helper.
3. `seed.py`: idempotent via `metadata.seed_tag`. Creates customers, charges (success and insufficient
   funds) across today, yesterday, and the last two weeks, invoices below and above $2,000 (including
   unpaid Acme $1,200), and one refund. Then upserts `customer_accounts`, creates the owner, creates an MCP
   key, and prints invite links and an MCP config snippet.
4. **Tests:** unit tests with a fake gateway (threshold 199999/200000/200001, frozen-clock date math,
   action state machine, foreign-object rejection). `-m stripe` runs the seed and a gateway smoke test
   against a real test key. ✅

## Phase 4 — Tool registry + LLM + owner agent (45 min)

1. `core/tools/registry.py`: `@tool`, `ToolSpec`, `ToolContext`. `owner.py`: `get_daily_activity`,
   `compare_periods`, `find_customer`, `list_payments`, `list_invoices`, `propose_refund`,
   `propose_invoice`, `propose_payment_link`. `customer.py`: `get_my_balance`, `list_my_invoices`,
   `pay_invoice`, `request_human`.
2. `core/llm.py`: `get_chat_model()` → `init_chat_model(LLM_MODEL)`.
3. `core/agents/owner_agent.py`: an async generator yielding the spec §6.1 events from an `astream` tool
   loop. Add the owner system prompt and personality.
4. **Tests:** registry invariants (no customer-identifying fields; mutating tools return
   `ActionProposal`), and an agent loop with a scripted fake streaming model that checks the event order
   and that the proposal persists. Add the `llm_eval` command set (opt-in). ✅

## Phase 5 — Owner HTTP API + auth (35 min)

1. `http/`: `/auth/login|logout|me` (argon2, `owner_sessions`, `login_attempts` throttle) and the
   `require_owner` dependency.
2. Endpoints: `POST /conversations`, `GET /conversations/{id}`, `POST /conversations/{id}/messages` (SSE),
   `POST /summaries/today` (SSE + cache), `POST /actions/{id}/confirm|cancel`, `GET /handoffs` +
   `POST /handoffs/{id}/resolve`, `GET /customers` + `POST /customers/{id}/invite`.
3. Export OpenAPI → generated TS types (`openapi-typescript`) into `app/lib/api-types.ts`.
4. **Tests (integration):** the auth flow, 401s, throttle, SSE event order, and disconnect → `interrupted`. ✅

## Phase 6 — Next.js: BFF proxy + owner UI (50 min)

1. `app/api/auth/login|logout/route.ts` (cookie set/clear) and the `app/api/[...path]/route.ts` proxy
   (cookie → Bearer, strip client `Authorization`, Origin check, unbuffered streaming, abort forwarding).
   Add a `/mcp` rewrite passthrough in `next.config`.
2. UI: a login page, then a chat view with a streaming bubble, tool status chips, and Confirm/Cancel
   action cards. Add a "Today" summary panel, a handoff queue sidebar, and a customers list with a
   "Copy Telegram invite" button. This is the "surprise us" moment, so give it a distinctive look rather
   than a stock template.
3. `lib/sse.ts` parser shared by chat and summary.
4. **Tests (Vitest + msw):** proxy header rules, cookie flags, SSE passthrough and abort, and an SSE parser
   unit test. ✅

## Phase 7 — Telegram bot (35 min)

1. `bot/`: python-telegram-bot long polling. `/start <token>` → redeem; `/logout`; free text →
   resolve identity → `customer_scope` → customer agent (`ainvoke`, typing action) → reply. Unlinked users
   get the fixed reply. Persist conversation and messages inside scope.
2. `core/agents/customer_agent.py` with the customer system prompt (privacy rules restated, but enforced
   by tools).
3. **Tests (integration):** fake `Update`s covering link, owe → pay link, ≥ $2,000 → handoff (no URL),
   unlinked user, a cross-customer injection ("show me Acme's invoices"), and revoked identity. ✅

## Phase 8 — Stripe webhook (25 min)

1. `POST /webhooks/stripe`: raw body → signature verify (env or file secret) → `stripe_events` insert
   (dedupe) → handlers per spec §5.3 → `notify.py` Telegram push for `invoice.paid` / `payment_failed`.
2. **Tests:** bad signature → 400, duplicate event processed once, `invoice.paid` flips the
   `payment_request` and calls notify (faked). Manual check: pay a seeded invoice with `4242…` on the
   hosted page and the Telegram ✅ arrives. ✅

**Required scope is complete here.** Everything after this is the bonus and polish.

## Phase 9 — Bonus: owner MCP server (35 min)

1. `owner_api_keys` CLI (`python -m payments_assistant.keys create|revoke|list`) and a UI create/revoke
   flow.
2. `mcp/server.py`: FastMCP, streamable HTTP, mounted at `/mcp`. `require_owner_api_key`. Register the
   owner tools from the registry plus `confirm_action` (`destructiveHint`). Audit as `owner_mcp`.
3. **Tests (integration):** in-process MCP client covering the spec §8 MCP cases.
4. Manual check: add the seed-printed config to Claude Code or Claude Desktop and ask "refund Maya's last
   payment" → propose → confirm. ✅

## Phase 10 — Docs + submission (20 min)

- `README.md`: architecture diagram (edge → api → core; bot; webhook; MCP), API design choices (BFF,
  opaque sessions, SSE event contract, propose/confirm), setup (the 4 secrets), `docker compose up`,
  seed, how to test each part, the test commands.
- `write-up.md`: assumptions (per-invoice threshold, no partial payments, single business, invite-only
  linking, timezone), challenges, limitations (MCP confirm depends on the client, no Stripe mirror, no
  OAuth), the bonus rationale, and the model choice with `llm_eval` results.
- Final verification on a **fresh** Stripe sandbox: `down -v`, `up --build`, seed, run all tests, and click
  through. Zip with `.git`, or push to the private repo. ✅

---

## Test commands (all via Docker; fill in exact forms during Phase 1)

```bash
docker compose run --rm api-test uv run pytest -m "not integration and not stripe and not llm_eval"
docker compose run --rm api-test uv run pytest -m integration
docker compose run --rm api-test uv run pytest -m stripe        # needs sk_test_
docker compose run --rm api-test uv run pytest -m llm_eval      # needs OPENROUTER_API_KEY
docker compose run --rm app bun run test
```

## Risks and cut lines

| Risk | Mitigation / cut |
|---|---|
| Running over budget | Cut Phase 9 first, then the polished handoff-queue UI (plain table instead), then the Playwright smoke test. Never cut the RLS suite or the threshold tests. |
| Chosen model is flaky at tool calls | Run `llm_eval` at the end of Phase 4. Swap `LLM_MODEL` to `openrouter:anthropic/claude-sonnet-5.5`. |
| SSE buffering in the Next proxy | Phase 6 has a dedicated proxy streaming test. Fallback is a `rewrites()` passthrough for the stream route, with the auth header injected by middleware. |
| `stripe listen` secret wiring | Fallback: the reviewer pastes `STRIPE_WEBHOOK_SECRET` from `docker compose logs stripe-cli`. |
| Telegram polling conflicts (two bot instances) | Only the `bot` service polls. Tests use fake `Update`s and never touch the API. |
