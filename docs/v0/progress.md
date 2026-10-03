# v0 Progress

Tracks status against `docs/v0/goal.md`. Updated every loop iteration.

## Budget

| Limit | Cap | Used |
|---|---|---|
| Iterations | 15 | 2 |
| OpenRouter `llm_eval` runs | 3 (~$2) | 0 |
| Repeated-failure streak | 3 | 0 |

| Phase | Estimate | Time box (2×) | Status |
|---|---|---|---|
| 0 Repo hygiene | 10m | 20m | ✅ done (by user before loop) |
| 1 Skeleton | 45m | 90m | ✅ done (iteration 1) |
| 2 DB schema, RLS | 45m | 90m | ✅ done (iteration 2) |
| 3 Core + Stripe + seed | 50m | 100m | next |
| 4 Tools + LLM + owner agent | 45m | 90m | |
| 5 Owner HTTP API + auth | 35m | 70m | |
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
