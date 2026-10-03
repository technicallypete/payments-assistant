# v0 Progress

Tracks status against `docs/v0/goal.md`. Updated every loop iteration.

## Budget

| Limit | Cap | Used |
|---|---|---|
| Iterations | 15 | 9 |
| OpenRouter spend / `llm_eval` runs | ~$4 / 6 runs (raised from ~$2 / 3 by the user) | 4 runs; ~$0.20 incl. live smoke/browser tests (dashboard showed $0.11 / 32 requests before run 4) |
| Repeated-failure streak | 3 | 0 |

| Phase | Estimate | Time box (2×) | Status |
|---|---|---|---|
| 0 Repo hygiene | 10m | 20m | ✅ done (by user before loop) |
| 1 Skeleton | 45m | 90m | ✅ done (iteration 1) |
| 2 DB schema, RLS | 45m | 90m | ✅ done (iteration 2) |
| 3 Core + Stripe + seed | 50m | 100m | ✅ done (iteration 3) |
| 4 Tools + LLM + owner agent | 45m | 90m | ✅ done (iteration 4) |
| 5 Owner HTTP API + auth | 35m | 70m | ✅ done (iteration 5) |
| 6 Next BFF + UI | 50m | 100m | ✅ done (iteration 6) |
| 7 Telegram bot | 35m | 70m | ✅ done (iteration 7) |
| 8 Stripe webhook | 25m | 50m | ✅ done (iteration 8) — **all required scope complete** |
| 9 Bonus: owner MCP | 35m | 70m | ✅ done (iteration 9) |
| 10 Docs + submission | 20m | 40m | next |

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

### Iteration 5: Phase 5 (2026-10-03)

The user raised the OpenRouter budget by $2 (now ~$4, soft cap 6 eval runs). Summaries and
handoffs/customers were built by two subagents; I did the app skeleton, auth, conversations/SSE, and
actions.

**Built:**
- `http/state.py` (`AppState`: settings, engine, sessionmaker, gateway/model factories, clock;
  swappable in tests), `http/deps.py` (`Session`, `Owner` via Bearer session token; `pak_` keys
  rejected), `http/sse.py`, a route registry, and `http/export_openapi.py` → `app/lib/openapi.json`
  (16 paths).
- `core/services/auth.py`: argon2 password login, opaque session tokens (sha256 stored), sliding 7-day
  expiry, logout revocation, throttle of 5 failures / 15 min per email (counted even when throttled),
  dummy-hash timing for unknown emails.
- Routes: `/auth/login|logout|me`, `/conversations` CRUD + `POST /conversations/{id}/messages` (SSE;
  the user message is persisted before streaming; the assistant reply, tool rows, and proposals are
  committed in a shielded `finally` as complete/interrupted/error), `/actions` list/confirm/cancel,
  `/summaries/today` (subagent; cached per local date and timezone, SSE), `/handoffs`
  list/acknowledge/resolve and `/customers` list/invite/sync (subagent).
- Migration 0003: `messages.seq` identity column. Timestamps tie within a turn, so transcripts order
  by seq.

**Eval run 4 (after the Phase 4 fixes): 10/11, no hangs, 70s.** All 6 owner commands passed (refund
proposal $82 to Maya; Acme $250 due Fri Oct 9; last week $1,000 vs $800 (+25%); an accurate day
summary; refused to skip confirmation; Bluebird $250). The 1 failure was test criteria: the bot
politely refused "show me Acme Corp's invoices" and repeated the name the customer had typed. The
check now only flags terms not in the customer's own message, and never allows other customers'
amounts or invoice numbers. Privacy prompts: 4/4 leaked nothing.

**Found during live smoke testing (real api container, live Stripe, real LLM):**
- The running `api` container lacked the Phase 3–5 dependencies (`uv run` only syncs at container
  start). Fixed by restarting; the rule is now in CLAUDE.md.
- The daily summary showed $4,505 / 17 payments / 11 declines: 18 charges left by the live gateway
  tests (throwaway customers deleted, charges not hidden). All 18 were verified to belong to deleted
  `pa_test` customers before hiding them. The test teardown now hides its charges. After cleanup the
  live summary reads: "You've taken in $4,280.00 across 8 payments so far today, a strong lift from
  yesterday's $500.00 at this same time. Two payments failed for insufficient funds. ... Acme Corp
  has $3,500.00 and another $1,200.00 outstanding ..."

**Verification:** `pytest -m "not llm_eval and not stripe"` → **329 passed**; `-m stripe` live gateway
tests pass; core coverage **93%** (coverage now traces greenlets, which async SQLAlchemy needs);
ruff clean. Live: login 200, `/customers` balances correct, summary streamed.

**Next:** Phase 6 (Next BFF proxy + owner UI).

### Iteration 6: Phase 6 (2026-10-03)

The UI was built by a subagent; I did the BFF layer, the live/browser verification, and the fixes.

**Built:**
- BFF (`app/lib/bff.ts`, plain functions with an injected fetch; route files are thin):
  `/api/auth/login` (the token goes only into the httpOnly `pa_session` cookie, SameSite=Lax, Secure
  on https, expiry from the API; never in the body), `/api/auth/logout` (revokes upstream and clears the
  cookie), and the catch-all `/api/[...path]` proxy (cookie → Bearer, client Authorization dropped,
  Origin/Referer CSRF check on non-GET, allowlist `auth/me, conversations, actions, summaries,
  handoffs, customers` so webhooks/docs stay internal, Set-Cookie stripped, unbuffered SSE
  passthrough, `request.signal` forwarded for abort).
- `lib/sse.ts` (fetch-body SSE reader), `lib/client.ts` (typed browser client, 401 → /login),
  `lib/events.ts`, and `lib/api-types.ts` generated from `lib/openapi.json` (`bun run gen:api`).
- UI (subagent): `/login`; dashboard with Chat (conversation list, streamed tokens with caret, tool
  chips, inline Confirm/Cancel cards with countdown and result stamps, Stop, suggested prompts, pending
  proposals restored), the Today panel (streamed summary + refresh), Needs you (handoffs ack/resolve),
  and Customers (balances, Telegram badge, copy invite, Stripe sync). The "ledger" design: Fraunces +
  Instrument Sans + IBM Plex Mono tabular money, paper/ink light and dark themes, ruled cards,
  aria-live streaming, reduced motion. Screenshots are in `docs/screenshots/`.

**Deviation:** the plan said msw for proxy tests. The proxy logic is plain functions with an
injected `fetch`, tested directly, which is simpler and deterministic. The live checks below cover the
real Next runtime.

**Verified live (curl through Next + headless Chromium via the Playwright image):** login sets the
cookie flags correctly with no token in the body; `/api/auth/me` 200 / 401 without cookie;
cross-site POST 403; `/api/webhooks/stripe` 404; logout clears the cookie and later calls get 401; the
summary streams token by token through Next (~60ms apart); the full UI renders in light, dark, and
mobile; a real chat turn ("Refund Maya's last payment") streamed and showed the inline $415.00 refund
card in 14s; Cancel produced a CANCELLED stamp and the DB status `cancelled`.

**Found and fixed during verification:**
- Summary first token took 28s (Kimi reasoning). Added `LLM_SUMMARY_REASONING_EFFORT` (default `none`):
  first token 8s, total 10.6s.
- A race: a click before the initial conversation load finished let the late load wipe the live turn
  (the new proposal card was dropped). Fixed with a navigation guard plus `reset`/`set_pending` reducer
  actions and pending dedupe. A component test reproduces it and fails without the guard.
- The greeting was hard-coded "Morning." It's now time-aware (useSyncExternalStore, hydration-safe).
  The mobile placeholder was shortened.
- `GET /actions` returned expired proposals; it now filters by `expires_at > now`, with a test.
- Rebuilt the app image (new deps); jsdom doesn't run under Bun, so component tests use happy-dom.

**Verification:** Vitest **68 passed**, eslint clean, typecheck clean; Python **331 passed**; ruff clean.

**Next:** Phase 7 (Telegram bot).

### Iteration 7: Phase 7 (2026-10-03)

I wrote the library-independent bot service; a subagent wrote the python-telegram-bot adapter.

**Built:**
- `bot/service.py`: `handle_start` (deep-link token → `redeem_customer_invite`; welcome by first name;
  used/expired/unknown → "ask for a fresh link"), `handle_logout` (`revoke_telegram_identity`),
  `handle_text` (resolve identity; unlinked → fixed reply, no LLM, nothing stored; linked → one
  customer-agent turn inside `customer_scope` with a gateway bound to that customer's Stripe id; user
  message, tool rows, and reply persisted in one ongoing conversation per chat; empty reply → fallback
  with status `error`). Everything runs as the `bot` role.
- `bot/telegram_app.py` (subagent): private chats only; `/start [payload]`, `/logout`, `/help`, text;
  typing action refreshed every 4s; Markdown → Telegram HTML (`bot/formatting.py`, conservative,
  escapes HTML, leaves amounts/ids/URLs intact, no javascript: links) with a plain-text fallback;
  4096-char splitting; errors → fallback reply, never internals; `notify()` helper for the webhook.
- `bot/__main__.py`: real long-polling entrypoint (bot DB role, live customer gateway, configured
  model); idles with a clear log if the token is missing; httpx logging quieted so the token never
  hits logs. Running live as @edger_payments_bot.
- Fix: `append_message` only sets titles on owner conversations (the bot role can't update
  conversation titles, by design).

**Tests:** bot service integration 10 (as `bot`): single-use invites, unlinked → no LLM/no rows,
owe → pay by number → link, ≥ $2,000 → handoff with no URL anywhere, cross-customer pay attempts →
not_payable with nothing leaked, customers never see each other's history, logout, empty-reply
fallback. Adapter unit tests 30 (formatting 17, handlers 13 with fake Updates).

**Verification:** `pytest -m "not llm_eval and not stripe"` → **371 passed**; core+bot coverage 92%;
ruff clean; bot container logs "Application started". A manual Telegram test needs a human (invite
link sent to the user).

**Next:** Phase 8 (Stripe webhook → payment_requests + Telegram "payment received").

### Iteration 8: Phase 8 (2026-10-03). Required scope complete

Done directly (small, tightly coupled phase).

**Built:**
- `POST /webhooks/stripe` (`http/routes/webhooks.py`): reached by `stripe-cli` on the compose network
  (the Next allowlist blocks it). It verifies `Stripe-Signature` on the raw body first (bad, missing,
  stale, or tampered → 400; no secret → 503), then processes the verified raw JSON.
- `core/services/webhooks.py`: `stripe_events` insert-or-lock, processed only while `processed_at` is
  NULL, so Stripe retries and replays can't double-apply. `invoice.paid` → payment requests `paid`
  and open handoffs for that invoice resolved; `invoice.payment_failed` → `failed`;
  `invoice.voided|marked_uncollectible` → `void`. Any money event invalidates today's cached summary.
  All events are audited.
- Notifications (`AppState.notify`, Telegram `notify()` when a token is set) are returned by the service
  and sent **after commit**, best effort. A Telegram failure never fails the webhook. Only customers
  with an active Telegram link are messaged.

**Live bug found by the end-to-end test (unit tests had missed it):** real Stripe events carry
`*_decimal` fields; `event.to_dict()` turns them into `Decimal`, which JSONB can't store, so the
webhook returned 500. The fix processes the verified raw JSON body instead, with a regression test.
I confirmed the old path fails on that payload.

**Live E2E:** created a $5 `pa_test` invoice for Maya, recorded a payment request, paid it with a test
card through the Stripe API, then replayed the event with `stripe events resend` after the fix: 200,
payment request `paid`, event marked processed. The test charge was hidden, the leftover test
invoice voided, and the test card detached.

**Tests:** 15 webhook integration tests (signatures, idempotency, effects, notify-after-commit,
unlinked customers, handoff resolution, summary invalidation, unknown events, decimals).

**Verification:** `pytest -m "not llm_eval"` (includes live Stripe) → **390 passed**; core coverage 95%; ruff clean.

Phases 0–8 are complete: every required feature of the brief is built. Next: Phase 9 (bonus: owner MCP).

### Iteration 9: Phase 9, bonus owner MCP server (2026-10-03)

I built the key service and the MCP server; a subagent built the key CLI, HTTP routes, UI panel,
Next rewrite, and seed integration.

**Built:**
- `core/services/api_keys.py`: `pak_` keys (sha256 stored + display prefix), resolve (last_used
  throttled), list, and revoke (audited).
- `mcp/server.py` on **MCP SDK 2.3.0**: a low-level `Server` with `on_list_tools`/`on_call_tool`,
  stateless streamable HTTP with JSON responses. Tool schemas come straight from the registry (no
  drift). Annotations: reads `read_only`, `propose_*` non-destructive, `confirm_action`
  `destructive_hint` (clients ask the human), plus `cancel_action`. Calls run in an `api`-role
  transaction with `ToolContext(api_key_id=...)`, so proposals are bound to the key and audited as
  `owner_mcp`. `MCPEndpoint` ASGI wrapper: Bearer `pak_` only (session tokens → 401 with
  WWW-Authenticate); the principal passes via a ContextVar.
- Mounted as an exact raw-ASGI `Route("/mcp")` (a Mount 307-redirects POST /mcp → /mcp/). The
  session manager runs in the FastAPI lifespan. DNS-rebinding protection is off deliberately
  (keyed auth; reached as `api:8000` via Next).
- Subagent: `python -m payments_assistant.keys create|list|revoke`; `/api-keys` GET/POST/DELETE (the
  key is shown once with ready-to-paste Claude Code and Claude Desktop (`mcp-remote`) config);
  `core/client_config.py`; the "Connect Claude" dashboard panel; a Next `beforeFiles` rewrite of
  `/mcp` → API (the client's Authorization passes through); BFF allowlist `api-keys`; the seed prints a
  key and snippets; OpenAPI/TS types regenerated (18 paths).

**Tests:** MCP integration 13 (real MCP `ClientSession` over httpx2 ASGI with lifespan): 401 for
missing/fake/revoked keys and session tokens, API keys rejected on the web API, `tools/list` ==
owner registry (schemas identical, no customer tools), structured reads, tool errors as `is_error`,
propose → confirm executes once with `owner_mcp` audit, other key / expired / cancelled refused.
API keys HTTP 8 + client config 2. Vitest +3 (panel).

**Live:** after restarting `api` (new dependency), POST localhost:3010/mcp without a key → 401
through the Next rewrite. With a CLI-minted key, a real MCP session via `http://app:3000/mcp`:
server `penny-payments`, 9 tools, `get_activity(today)` → $4,280.00 / 8 payments / 2 failed (live
Stripe), Maya's last payment $415.00. Test key revoked.

**Verification:** Python **413 passed** (incl. live Stripe), coverage core+http+mcp 94%; Vitest **72**;
ruff/eslint/typecheck clean.

**Next:** Phase 10 (README, write-up, fresh-sandbox run).
