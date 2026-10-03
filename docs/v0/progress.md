# v0 Progress

Tracks status against `docs/v0/goal.md`. Updated every loop iteration.

## Budget

| Limit | Cap | Used |
|---|---|---|
| Iterations | 15 | 4 |
| OpenRouter `llm_eval` runs | 3 (~$2) | **3 (limit reached)**; total OpenRouter spend $0.11 / 32 requests (owner's dashboard, 2026-10-03) |
| Repeated-failure streak | 3 | 0 |

| Phase | Estimate | Time box (2×) | Status |
|---|---|---|---|
| 0 Repo hygiene | 10m | 20m | ✅ done (by user before loop) |
| 1 Skeleton | 45m | 90m | ✅ done (iteration 1) |
| 2 DB schema, RLS | 45m | 90m | ✅ done (iteration 2) |
| 3 Core + Stripe + seed | 50m | 100m | ✅ done (iteration 3) |
| 4 Tools + LLM + owner agent | 45m | 90m | ✅ done (iteration 4) |
| 5 Owner HTTP API + auth | 35m | 70m | next |
| 6 Next BFF + UI | 50m | 100m | |
| 7 Telegram bot | 35m | 70m | |
| 8 Stripe webhook | 25m | 50m | |
| 9 Bonus: owner MCP | 35m | 70m | |
| 10 Docs + submission | 20m | 40m | |

## Iteration log

### Iteration 1: Phases 0–1 (2026-10-03)

**Phase 0:** already satisfied. Branch is `main`, docs committed, `.env` gitignored, no secrets tracked.

**Phase 1 built:**
- `api/`: uv project (FastAPI, SQLAlchemy async + psycopg 3, Alembic, pydantic-settings), the
  `src/payments_assistant/{core,http,bot,mcp}` layout, `/health`, `Settings` (asserts `sk_test_`;
  webhook secret from env or the stripe-cli file), Alembic env (sync psycopg, no revisions yet), a bot
  stub, ruff.
- `app/` (via subagent): Next 16.3.8, React 19, Tailwind 4, Vitest, `lib/config.ts` (`serverApiUrl`),
  placeholder page.
- `docker-compose.yml`: postgres, api-migrate, api, bot, stripe-cli, app, and `api-test` (profile
  `test`). Ports default 3010/8010/5433. Containers run as uid 1000, so bind-mount files stay host-owned.
- `postgres/init/01-roles.sh`: creates `api`/`bot`/`reporter` (NOBYPASSRLS) with env passwords.

**Decisions / deviations (docs updated):**
- All table grants go in migrations, not `ALTER DEFAULT PRIVILEGES`, so the test DB matches dev (spec §4.1).
- Tests run in a dedicated `api-test` compose service with admin creds, instead of `-e DATABASE_URL=...`.
- The Python venv lives at `/opt/venv` in the image (not bind-mounted). `uv run` re-syncs from
  `uv.lock` on start.
- Newer Stripe CLI requires `--events`; it's set to the spec §5.3 event list.
- The frontend type check is `bun run typecheck` (`next typegen && tsc --noEmit`). Next 16 generates
  route types first.

**Verification (fresh `down -v && up --build`):**
- All services up; `api-migrate` exited 0; `api` healthy; `curl :8010/health` → 200; `:3010` serves the
  placeholder.
- stripe-cli wrote `/run/stripe/whsec`; it matches the live listener's secret; `api` loads it.
- Python: 8 unit + 4 integration = **12 passed**; ruff check + format clean; core coverage 84% (skeleton).
- App: Vitest **2 passed**; eslint clean; typecheck clean.

**Notes:** the empty `app/.next` and `app/node_modules` mount stubs on the host are root-owned (Docker
creates them for the anonymous volumes). They're gitignored and harmless.

**Next:** Phase 2 (schema, RLS, definer functions, full RLS suite).

### Iteration 2: Phase 2 (2026-10-03)

**Built:**
- `alembic/versions/0001_schema.py`: all 15 tables from spec §5 as hand-written SQL. Includes the
  conversation actor/channel CHECK, the one-active-Telegram-link partial unique index, the
  one-open-payment-request-per-invoice partial unique index, and a `messages_check_customer` trigger. The
  trigger runs as the invoker, so under the bot's RLS another customer's conversation is invisible and
  the insert fails.
- `0002_rls_grants.py`: `app_current_customer()`, ENABLE + FORCE RLS, `api` (all rows) and `bot`
  (scoped) policies, the bot audit-insert-only policy, explicit grants per the §5.4 matrix (column-level
  UPDATE on conversations), and the SECURITY DEFINER `redeem_customer_invite`,
  `resolve_telegram_identity`, `revoke_telegram_identity` (pinned search_path; EXECUTE for bot only).
  Downgrade/upgrade round-trips.
- `core/models.py` (SQLAlchemy, client-side uuid4 PKs so bot INSERTs never need RETURNING) and
  `core/scoping.py` (`customer_scope`).

**Tests:** RLS suite (61, written by a subagent) + identity functions (11) + scoping (3) + schema and
constraints (9). The RLS suite runs as `bot`/`api`/`reporter`, never admin.

**Decisions / deviations (docs updated):**
- Added `revoke_telegram_identity()` for `/logout`, since the bot can't write identities directly (spec §3.2).
- `audit_log` gets RLS so bot audit rows are pinned to its own customer.
- The integration test DB is now `<DB_NAME>_test_<random>` per run and dropped afterwards, so parallel
  runs (subagents) can't clobber each other.
- Ruff E501 is disabled for `alembic/versions/*` (long SQL lines).

**Verification:** `pytest -m "not stripe and not llm_eval"` → **95 passed** (8 unit + 87 integration).
Core coverage 96%. ruff check + format clean. No leftover test databases.

**Next:** Phase 3 (Stripe gateways, core services, seed).

### Iteration 3: Phase 3 (2026-10-03)

Split across two subagents (live Stripe gateways + fakes; seed) while I wrote the contracts and
core services.

**Built:**
- Contracts: `core/stripe_types.py` (pydantic models, integer minor units), `core/stripe_gateway.py`
  (Owner/Customer gateway Protocols), `core/security.py` (token sha256, argon2id passwords).
- `core/stripe_live.py` (subagent): SDK 16 async (`client.v1.*_async`), API `2026-09-30.endive`.
  The customer gateway filters by customer in Stripe and re-checks ownership; unknown and foreign
  objects raise the same `ForeignObjectError`.
- `core/timeutil.py`: named periods and comparison windows in `BUSINESS_TIMEZONE` (DST-safe), and
  due-date phrases ("next Friday" = first Friday strictly after today).
- `core/services/`: `reporting` (stats the LLM narrates), `payments` (the $2,000 rule: link vs handoff,
  dedupe of open links/handoffs), `actions` (propose → confirm/cancel/expire with idempotency keys,
  surface/key binding), `invites`, `accounts`, `audit`.
- `seed.py` (subagent): idempotent, `--reset`. The real seed ran twice against the test account: 7
  customers, 26 payments, 2 declines, 5 invoices; second run created nothing. Today = $4,280 across 8
  payments + 2 insufficient-funds declines (the brief's example); Acme owes $1,200 + $3,500; Jordan
  $2,000 (boundary); Maya $180.

**Fixes found in review:**
- `actions.confirm` used to raise on expiry, which rolled back the `expired` status. It now returns the
  expired action so the caller's transaction commits it.
- The DB fixtures moved to a shared plugin (`tests/db_fixtures.py`) so integration and stripe_live
  tests share one test DB. The fixture now drops its DB even when setup fails.

**API quirks recorded (spec §7):** `pa_occurred_at` backdating, `pa_hidden` for reset charges,
`send_invoice` needs a customer email, `StripeObject` is no longer a dict (use `.to_dict()`), PaymentIntents
need `automatic_payment_methods` with `allow_redirects="never"`.

**Verification:** `pytest -m "not llm_eval"` → **210 passed** (includes the live `-m stripe` tests against
the real test account). Core coverage 95%. ruff clean. No leftover test DBs. The 14 warnings are an
httpx deprecation inside the Stripe SDK.

**Next:** Phase 4 (tool registry, LLM, streaming owner agent).

### Iteration 4: Phase 4 (2026-10-03)

Customer tools and agent were built by a subagent; I did the registry, loop, LLM factory, and owner
side.

**Built:**
- `core/tools/registry.py`: `@tool` (Pydantic in/out, docstring = model-facing description, UI label),
  `ToolContext` (actor from session/identity, never args), `ToolSpec.json_schema()` (shared by LangChain
  now and MCP later).
- Owner tools: `get_activity` (period + comparison + open invoices), `find_customers`, `find_payments`,
  `list_invoices`, and `propose_refund` / `propose_invoice` / `propose_payment_link` (proposals only).
- Customer tools (subagent): `get_my_balance`, `list_my_invoices`, `list_my_payments`, `pay_invoice`,
  `request_human`. There are no customer-identifying inputs, and a registry test enforces that.
- `core/agents/loop.py`: streaming tool loop emitting the spec §6.1 events (message_start, token,
  tool_start/end, action_proposed, message_end, error). Tool errors go back to the model; internals
  don't leak. Hop limit 6. A provider error keeps the partial text. The history sent back is cleaned
  (text + tool calls only). An empty final reply after tools gets one nudge.
- `owner_agent.py` ("Penny") and `customer_agent.py`. The owner prompt includes the local date, says
  numbers only come from tools, and says mutations are proposals.
- `core/llm.py`: `LLM_MODEL` provider:model → `ChatOpenAI` via OpenRouter, with timeout, retries, and
  reasoning settings.
- Tests: scripted streaming fake model (`tests/fake_llm.py`), demo account (`tests/demo_data.py`),
  registry invariants, loop behaviour, owner/customer tools, and agent turns end to end on the DB.

**LLM eval runs (budget 3 of 3 used):**
- Run 1: **8/11**. Failures: (a) the customer's "what do I owe" final text was empty; (b) the invoice
  command asked for a line-item description instead of proposing; (c) the summary check was too strict
  ("failed" vs "declined"; the text was correct: $822 today, +$522 vs yesterday, 2 insufficient-funds
  declines, Acme $1,200 open).
- Run 2: hung (stopped; no results).
- Run 3: 4/4 passed (all customer privacy prompts), then hung on the 2-turn "pay it" test (killed at
  15 min).

**Root causes found and fixed after the runs:**
- **Hang:** `langchain-openrouter`'s SDK retried timed-out streaming requests with backoff, ignoring our
  timeout (faulthandler stack: `openrouter/utils/retries.py` → `httpx.ReadTimeout`). Raw HTTP to OpenRouter
  answered the same request in 4–5s. Switched to `ChatOpenAI` + OpenRouter base URL: 3 consecutive
  diagnostic calls took 4.7–9.2s. `langchain-openrouter` removed.
- **Empty reply:** streamed `reasoning_details` got corrupted on chunk concatenation and were sent back.
  The loop now sends clean messages and nudges once on an empty final reply.
- **Invented invoice id on "pay it":** the model never saw the id (the history only has text).
  `pay_invoice` now takes `invoice_ref`: an id, an invoice number like MAYA-0007, or nothing (= the only
  open invoice).
- **Invoice description:** now defaults to "Services", and the prompt says to propose rather than ask
  about details that have defaults.

**Not yet validated by an eval run:** the fixes above are covered by scripted tests plus live diagnostic
calls, but the eval suite itself needs a 4th run (~$0.05), which exceeds the budget. Asked the user.

**Verification:** `pytest -m "not llm_eval and not stripe"` → **284 passed**; core coverage 91%; ruff clean.

**Next:** Phase 5 (owner HTTP API + auth + SSE).
