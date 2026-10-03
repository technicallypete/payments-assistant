# Payments Assistant — v0 Spec: Auth, Data, and Scaffolding

Status: draft r4 · Scope: v0 (take-home, 3–5h budget) · Source brief: `docs/payments-assistant-project.md`

This spec covers **(1)** the stack and which local skills to compose for it, **(2)**
authentication for both actor types, **(3)** what lives in Postgres, including row-level security
(RLS) on everything customer-scoped, and **(4)** LLM configuration. Execution order belongs in
`docs/v0/plan.md`, which comes next.

Changes in r2: Python backend is **FastAPI** (was Django). Next.js route handlers are a BFF/proxy
and own the owner's cookie. LLM goes through LangChain with OpenRouter, selected by a `provider:model`
env var. The Stripe webhook is in v0. Compose uses `env_file`. `BUSINESS_TIMEZONE` defaults to
`America/New_York`.

Changes in r3: tools are defined once in a `core/tools/` registry (Pydantic in/out) and adapted to
LangChain and MCP. The **bonus feature** is an owner MCP server (`/mcp`, streamable HTTP) authenticated
with owner API keys. Customers get no MCP surface.

Changes in r4: owner chat **streams** over SSE (§6.1). Customer linking is **invite links only**; phone
matching is dropped.

---

## 1. Actors and trust boundaries

| Actor | Surface | Sees | Can do |
|---|---|---|---|
| **Business owner** | Browser → Next.js (`app`) → FastAPI (`api`) | Everything in the Stripe account | Summaries, queries, refunds, invoices, payment links, resolve handoffs |
| **Owner's MCP client** (bonus) | Claude Desktop/Code → Next `/mcp` → FastAPI `/mcp` | Same as owner | Same tool set as the web agent, plus `confirm_action` |
| **External customer** | Telegram → `bot` worker | **Only their own** invoices/payments | See what they owe, pay it if < $2,000, ask for a human |
| **LLM** (via OpenRouter) | Called by `api` and `bot` | Only what the calling surface's tools return | Proposes tool calls. It never holds authority. |
| **Stripe** | Source of truth for money; webhooks → `api` | n/a | Signed events |

Design rule: **authority comes from the authenticated session, never from the LLM.** The
customer's Stripe ID is bound from the Telegram identity → customer account mapping and is
injected into every tool. The LLM never passes it as a parameter.

Privacy is enforced in three layers:

1. **Tool layer.** Customer-bot tools have no `customer_id` argument. They wrap a
   `CustomerStripeGateway(stripe_customer_id)` that also checks `obj.customer == bound_id`
   on every object it fetches.
2. **DB role layer.** The bot process connects as Postgres role `bot`, which has grants on
   customer-scoped tables only.
3. **RLS layer.** Customer-scoped tables have `FORCE ROW LEVEL SECURITY`. `bot` sees rows only
   where `customer_account_id = current_setting('app.customer_account_id')`. If that setting is
   unset, queries return zero rows.

---

## 2. Stack and skill composition

### 2.1 Which Python backend: **FastAPI**

With Next.js route handlers as the public API and the owner's session, the Python service becomes an
**internal domain API plus workers**. That changes the trade-off from r1:

| Need | FastAPI | Django |
|---|---|---|
| Async LLM calls (LangChain `ainvoke`, tool loops) | native | `sync_to_async` at every boundary |
| Telegram worker (python-telegram-bot is asyncio) | same event-loop model, same libs | separate sync/async worlds |
| RLS `SET LOCAL` per transaction | one async session/transaction, no thread hops | must keep scope and queries in a single `sync_to_async` call (a footgun) |
| Typed contract for the Next proxy | Pydantic → OpenAPI → generated TS types | DRF serializers, weaker typing |
| MCP later | Python MCP SDK (FastMCP) mounts on the same ASGI app, **or** Next hosts MCP and calls this API | django-mcp (young, SSE only) |
| Auth/session, admin UI | lives in Next + a small owner table | Django's built-ins (mostly moot now that Next owns the session) |
| Skill reuse | `docker-setup` roles/compose patterns carry over | `django-project` and `auth-resend` reusable |

Django's main advantages were its auth and admin, and both matter less once Next owns the browser
session. The async-heavy parts (LLM, Telegram, webhooks → Telegram push) and the RLS scoping are
simpler in FastAPI. **Stack:** FastAPI + SQLAlchemy 2.0 (async, psycopg 3) + Alembic + Pydantic v2 +
uv, Python 3.13.

### 2.2 Skill composition

| Skill | Use |
|---|---|
| `docker-setup` | **Yes, adapted.** Compose layout, `pgvector/pgvector:pg16`, name-agnostic role bootstrap SQL with `ALTER DEFAULT PRIVILEGES`, one-shot migrate job (`alembic upgrade head` instead of `manage.py migrate`), `python:3.13-slim` + uv, `oven/bun:1`, Dockerfiles in `docker/`, scoped build contexts. Drop MinIO. Roles become `admin`/`api`/`bot`/`reporter`. Switch to `env_file`. |
| `nextjs-setup` | **Yes, adapted.** Bun, App Router, TS strict, Tailwind. Change: the browser never calls Python. All calls go to Next route handlers (`app/app/api/**/route.ts`), which call `API_URL` server-side. `NEXT_PUBLIC_API_URL` is not used. |
| `bootstrap-fullstack` | Ordering and naming discipline only (backend → frontend → compose → reconcile root). |
| `django-project`, `auth-resend` | Not used. Keep the **`core` + thin adapters** rule from `django-project`: `core` has no HTTP and no Telegram, and the `http`, `bot`, and webhook code import `core`, never each other. |

### 2.3 Services (compose)

| Service | Folder | DB role | Host port | Purpose |
|---|---|---|---|---|
| `postgres` | — | — | 5433 (dev) | stock image, `postgres/init/01-roles.sh` (shell, so role passwords come from env) |
| `api-migrate` | `./api` | `admin` | — | one-shot `alembic upgrade head` (tables, RLS, grants, functions) |
| `api` | `./api` | `api` | 8010 (dev only, for docs/debug) | FastAPI: owner domain API + `/webhooks/stripe` + owner MCP at `/mcp` |
| `bot` | `./api` | `bot` | — | python-telegram-bot **long polling** (`python -m payments_assistant.bot`) |
| `stripe-cli` | — | — | — | `stripe listen --forward-to api:8000/webhooks/stripe` |
| `app` | `./app` | — | 3010 | Next.js SPA + BFF route handlers |

`api`, `api-migrate`, and `bot` share one image and differ only in command and `DATABASE_URL`.

**`env_file`:** every service uses `env_file: [.env]`, with `.env.example` committed. `.env` holds only
secrets and reviewer-facing settings. Ports, DB names/roles, and service wiring are `${VAR:-default}` in
the compose file (§3.4). Per-service values, mainly `DATABASE_URL` with each service's role, go in that
service's `environment:` block, which overrides `env_file`. Host ports are listed in §3.4; container ports
are fixed.

**Webhook secret wiring:** `stripe listen` mints a `whsec_` per CLI device/account. The `stripe-cli`
entrypoint runs `stripe listen --print-secret > /run/stripe/whsec` before listening and writes to a
shared volume. `api` reads `STRIPE_WEBHOOK_SECRET`, falling back to `STRIPE_WEBHOOK_SECRET_FILE=/run/stripe/whsec`.
The reviewer copies nothing by hand. `stripe-cli` authenticates with `STRIPE_API_KEY=${STRIPE_SECRET_KEY}`.

**Docker-only development.** Every dev workflow runs in containers: running services, tests, migrations,
seeding, adding dependencies, lint. The host needs only Docker (Compose v2) and git. Source is bind-mounted
for hot reload, and dependencies are added with `docker compose run --rm <svc> uv add|bun add` so lockfiles
update in the repo. This is also how reviewers run it, so dev and review share one path.

### 2.4 Python package layout

```
api/
├── pyproject.toml            # uv; pytest config; ruff
├── alembic/ + alembic.ini    # migrations incl. RLS DDL via op.execute
└── src/payments_assistant/
    ├── core/                 # NO FastAPI / Telegram imports
    │   ├── db.py             # async engine/session factory
    │   ├── scoping.py        # customer_scope(session, account_id)
    │   ├── models.py         # SQLAlchemy models
    │   ├── stripe_gateway.py # OwnerStripeGateway, CustomerStripeGateway
    │   ├── llm.py            # get_chat_model(spec) via init_chat_model
    │   ├── tools/            # tool registry: one definition per tool (see §2.5)
    │   │   ├── registry.py   # @tool decorator, ToolContext, ToolSpec
    │   │   ├── owner.py      # owner tools
    │   │   └── customer.py   # customer tools (no customer_id params)
    │   ├── agents/           # owner_agent.py, customer_agent.py (tool loop over the registry)
    │   └── services/         # payments, invoices, handoffs, summaries, invites, auth
    ├── http/                 # FastAPI app, routers, deps (session + API-key auth), webhooks
    ├── mcp/                  # FastMCP server: registers owner tools from the registry
    ├── bot/                  # telegram handlers → core
    ├── notify.py             # Telegram sendMessage via HTTP (used by webhooks)
    └── seed.py               # `uv run python -m payments_assistant.seed`
```

### 2.5 Tool registry and adapters (LangChain + MCP)

Each tool is defined **once** in `core/tools/` and adapted to every surface:

```python
class RefundInput(BaseModel):
    payment_query: str = Field(description="Which payment, e.g. \"Maya's last payment\"")
    amount_cents: int | None = Field(None, gt=0, description="Partial refund; omit for full")

class ActionProposal(BaseModel):
    action_id: UUID
    preview: str            # "Refund $82.00 to Maya Chen (ch_…)"
    expires_at: datetime

@tool(name="propose_refund", audience="owner", mutating=True)
async def propose_refund(ctx: ToolContext, args: RefundInput) -> ActionProposal: ...
```

- `ToolContext` carries the **authenticated actor**: the owner id, or the customer account id plus its
  bound Stripe gateway. It also carries the DB session, the conversation id, and the clock. Context is
  built by the adapter from the session, never from arguments.
- `audience` is `owner` or `customer`. A customer tool's input model must not have any customer-identifying
  field. A unit test walks the registry and asserts this.
- `mutating=True` tools only ever create `owner_actions` rows (§5.2). Execution happens through the
  confirm path.

Adapters (thin, no logic):

| Adapter | Exposes | Context from | Confirm path |
|---|---|---|---|
| LangChain (`core/agents`) | `StructuredTool(args_schema=InputModel)` with ctx bound by closure | web session / Telegram identity | web: UI button → `POST /actions/{id}/confirm`. Customer tools don't mutate Stripe beyond issuing a link. |
| MCP (`mcp/server.py`) | Owner tools only, registered on FastMCP with the same input/output models | owner API key (§3.3) | `confirm_action(action_id)` tool, annotated `destructiveHint: true` so clients ask the human |

**Owner MCP server (bonus feature).** Built with the Python MCP SDK's FastMCP, streamable HTTP transport,
mounted on the FastAPI app at `/mcp`. Next forwards `/mcp` with a `rewrites()` passthrough, which streams
and leaves the `Authorization` header untouched, so Next remains the single public edge. Pitch for the
write-up: "manage your payments from Claude Desktop or Claude Code with the same guardrails as the web app."
The guardrails are the propose → confirm gate, idempotency keys, and the audit log. MCP-originated
actions are audited with `actor_type = owner_mcp` and the key id.

Honest limitation for the write-up: on MCP, the human confirmation step depends on the client honoring
`destructiveHint` and asking before the second call. The web UI enforces it with a real button. Mitigation:
`confirm_action` refuses proposals older than 10 minutes and those created under a different API key.

**Customers get no MCP surface.** An MCP client can't establish which customer is calling the way a linked
Telegram identity can.

---

## 3. Authentication

### 3.1 Owner: Next BFF + Python-issued opaque session

```
Browser ──cookie──▶ Next route handler ──Bearer <session token>──▶ FastAPI (validates against owner_sessions)
```

- **Credentials live in Python/Postgres** (`owners` table, argon2id hash via `argon2-cffi`). The seed
  creates the owner from `OWNER_EMAIL` / `OWNER_PASSWORD`. There is no sign-up.
- `POST /api/auth/login` (Next) forwards to `POST /auth/login` (FastAPI). On success, FastAPI mints
  `secrets.token_urlsafe(32)` and stores its SHA-256 in `owner_sessions` with a 7-day sliding TTL.
  Next sets it as cookie `pa_session`: **httpOnly, Secure (in prod), SameSite=Lax, Path=/**.
- The Next catch-all proxy `app/api/[...path]/route.ts` reads the cookie, forwards to `API_URL` with
  `Authorization: Bearer <token>`, and streams the response back (SSE passthrough for chat streaming).
  It strips client-supplied `Authorization` and hop-by-hop headers.
- FastAPI dependency `require_owner` hashes the bearer, looks it up, checks `revoked_at`/`expires_at`,
  and bumps `last_seen_at` (at most once a minute).
- `POST /api/auth/logout` revokes the server-side session and clears the cookie. `GET /api/auth/me`
  returns the owner.
- **CSRF:** SameSite=Lax plus JSON-only mutations plus an `Origin`/`Host` check in the Next proxy for
  non-GET requests.
- **Login throttling:** a per-email+IP counter in `login_attempts` (5 per 15 min) inside FastAPI.

Why opaque sessions over JWT: the token never touches browser JS, logout means real revocation, and
there is no refresh-token dance. The DB lookup costs nothing at this scale. MCP clients use API keys
(§3.3) checked by a sibling dependency.

`api` is reachable only on the compose network. Host port 8010 (`API_HOST_PORT`) is published in dev for `/docs`, which is
also owner-auth gated except `/health`.

### 3.2 Customer (Telegram)

A Telegram user is anonymous until **linked** to a customer account. A customer account maps 1:1 to a
Stripe customer.

**Linking is by deep-link invite token only** (phone/contact matching was considered and dropped for v0).

1. `core.services.invites.create(account)` mints `secrets.token_urlsafe(24)` and stores its SHA-256.
   The token is single use with a 7-day TTL. The function returns `https://t.me/<TELEGRAM_BOT_USERNAME>?start=<token>`.
2. The customer taps the link, and Telegram delivers `/start <token>`.
3. The bot calls the `SECURITY DEFINER` function `redeem_customer_invite(token_hash, tg_user_id, chat_id)`,
   which burns the token, upserts `telegram_identities`, and returns `customer_account_id`.
4. Later messages resolve through `resolve_telegram_identity(tg_user_id)`, also `SECURITY DEFINER`, and
   then enter the RLS scope.

The seed prints an invite link per seeded customer. The owner UI has "Copy Telegram invite" per customer.

Rules: one Telegram user maps to at most one account, and re-linking revokes the old link. Unlinked users
get a fixed reply ("ask the business for your invite link") with no LLM call, no stored messages, and no data. `/logout` revokes the link. Card
details are never collected in chat.

### 3.3 Owner API keys (MCP)

- Format: `pak_` + `secrets.token_urlsafe(32)`. Only the SHA-256 is stored, plus a display prefix
  (`pak_AbCd…`).
- Created from the owner UI ("Create MCP key", shown once), or with
  `python -m payments_assistant.keys create --name "Claude Desktop"`. The seed creates one and prints it
  next to a ready-to-paste MCP client config snippet.
- Sent as `Authorization: Bearer pak_…`. `require_owner_api_key` resolves it to the owner, rejects revoked
  keys, and bumps `last_used_at`.
- Keys are accepted **only** on `/mcp`, and session tokens are not accepted there. Each credential type
  works on one surface only.
- Revoke from the owner UI. OAuth 2.1 (the MCP auth spec) is out of scope for v0.

### 3.4 Configuration

**`.env`** (copied from `.env.example`) holds only secrets and the settings a reviewer might reasonably change:

| Var | Notes |
|---|---|
| `STRIPE_SECRET_KEY` | **required**; must start `sk_test_`, asserted at boot |
| `OPENROUTER_API_KEY` | **required** |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` | **required** |
| `STRIPE_WEBHOOK_SECRET` / `STRIPE_WEBHOOK_SECRET_FILE` | blank secret → file auto-written by `stripe-cli` |
| `LLM_MODEL`, `LLM_SUMMARY_MODEL`, `LLM_TEMPERATURE`, `LLM_MAX_TOOL_HOPS` | `provider:model`, see §6 |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY` | optional tracing |
| `BUSINESS_TIMEZONE` (`America/New_York`), `DEFAULT_CURRENCY` (`usd`), `HANDOFF_THRESHOLD_CENTS` (`200000`) | business rules |
| `OWNER_EMAIL`, `OWNER_PASSWORD` | seed only |

**Defaults that live outside `.env`.** Each can still be overridden by adding it to `.env`.

*In `docker-compose.yml`* as `${VAR:-default}` interpolation:

| Var | Default | Notes |
|---|---|---|
| `APP_HOST_PORT` | `3010` | host → `app:3000` |
| `API_HOST_PORT` | `8010` | host → `api:8000` (dev only, for `/docs`) |
| `POSTGRES_HOST_PORT` | `5433` | host → `postgres:5432` |
| `APP_ORIGIN` | `http://localhost:${APP_HOST_PORT:-3010}` | proxy Origin check, cookie `Secure` flag |
| `DB_NAME` | `payments_assistant` | |
| `DB_ADMIN_USER` / `DB_ADMIN_PASSWORD` | `admin` / `admin` | also `POSTGRES_USER`/`POSTGRES_PASSWORD` |
| `DB_API_PASSWORD`, `DB_BOT_PASSWORD`, `DB_REPORTER_PASSWORD` | `api`, `bot`, `reporter` | applied by the init script on the **first** start of an empty volume; `down -v` to change |
| `ENVIRONMENT`, `LOG_LEVEL` | `development`, `info` | |
| `WATCHPACK_POLLING` | `false` | |

Not configurable (fixed wiring inside the compose network): container ports `app:3000`, `api:8000`, and
`postgres:5432`; `API_URL=http://api:8000`; each service's `DATABASE_URL`, built from the DB vars with that
service's role. Only **host** ports vary, so internal URLs never change.

*In the app's pydantic `Settings`* (code defaults, so tests outside compose get them too):
`SESSION_TTL_DAYS=7`, `INVITE_TTL_DAYS=7`, `ACTION_PROPOSAL_TTL_MINUTES=10`, `LOGIN_MAX_ATTEMPTS=5`,
`LOGIN_WINDOW_MINUTES=15`.

---

## 4. Postgres roles and RLS mechanics

### 4.1 Roles

Adapted from the `docker-setup` skill's `01-roles.sql`:

| Role | Attributes | Used by | Customer-scoped tables |
|---|---|---|---|
| `admin` | superuser, owns all tables | `api-migrate`, test DB setup | bypasses RLS. **Never test RLS as admin.** |
| `api` | LOGIN, `NOBYPASSRLS` | `api` (owner + webhooks) | policy `TO api USING (true)` |
| `bot` | LOGIN, `NOBYPASSRLS` | `bot` worker | policy scoped to `app.customer_account_id` |
| `reporter` | LOGIN, SELECT only | unwired | no customer policy (deny) |

`api` is in `ALTER DEFAULT PRIVILEGES` (CRUD). `bot` is **not**. Each migration grants `bot` exactly what
it needs (deny by default).

### 4.2 Scoping

```sql
-- per customer-scoped table (Alembic op.execute)
ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
ALTER TABLE <t> FORCE  ROW LEVEL SECURITY;
CREATE POLICY <t>_api ON <t> TO api USING (true) WITH CHECK (true);
CREATE POLICY <t>_bot ON <t> TO bot
  USING      (customer_account_id = nullif(current_setting('app.customer_account_id', true), '')::uuid)
  WITH CHECK (customer_account_id = nullif(current_setting('app.customer_account_id', true), '')::uuid);
```

```python
# core/scoping.py
@asynccontextmanager
async def customer_scope(session: AsyncSession, account_id: UUID):
    async with session.begin():
        await session.execute(text("select set_config('app.customer_account_id', :id, true)"),
                              {"id": str(account_id)})
        yield session
```

- The setting is transaction-local (`is_local=true`) and resets on commit or rollback, so pooled
  connections can't carry it over.
- All `bot` DB work runs inside `customer_scope`. Async SQLAlchemy keeps the transaction on one
  connection, which avoids the thread-hop problem Django had.
- Pre-scope lookups go only through the two `SECURITY DEFINER` functions, owned by `admin` with a pinned
  `search_path`. `bot` gets `EXECUTE` on them and nothing on `customer_invites`.

---

## 5. Data model

Stripe is the **source of truth for money**. v0 reads it live through scoped gateways and does not mirror
it. Postgres holds identity, sessions, conversations, initiated actions and payment requests, handoffs,
webhook events, and audit records.

Conventions: UUID PKs (`gen_random_uuid()`), `timestamptz` defaulting to `now()`, money as `bigint` minor
units plus `char(3)` currency, Stripe ids as `text`.

### 5.1 Customer-scoped (RLS)

**`customer_accounts`**: the tenant boundary. The policy column is `id`.
`id uuid pk · stripe_customer_id text unique not null · display_name · email ·
status (active|disabled) · created_at · updated_at`. `bot` gets S.

**`telegram_identities`**
`id · customer_account_id fk · telegram_user_id bigint (unique where revoked_at is null) ·
telegram_chat_id bigint · telegram_username · invite_id fk · linked_at · revoked_at`.
`bot` gets S. Writes go through definer functions.

**`conversations`**: one table for both channels.
`id · channel (owner_web|customer_telegram) · customer_account_id fk null · owner_id fk null ·
external_chat_id · title · status (open|closed) · created_at · last_message_at`.
CHECK: exactly one of `customer_account_id` or `owner_id` is set. Owner conversations have a NULL
customer id, so the `bot` policy never matches them. `bot` gets S, I, U(`last_message_at`, `status`).

**`messages`**: append-only.
`id · conversation_id fk · customer_account_id null (denormalized; trigger asserts it matches the
conversation) · role (user|assistant|tool) · content text · status (complete|interrupted|error) · tool_name · tool_payload jsonb ·
llm_model · input_tokens · output_tokens · created_at`. Index `(conversation_id, created_at)`.
`bot` gets S, I.

**`payment_requests`**: payment links the bot issued.
`id · customer_account_id fk · conversation_id fk · stripe_invoice_id · amount bigint · currency ·
hosted_url · status (link_sent|paid|failed|expired|void) · created_at · updated_at`.
Partial unique index on `(stripe_invoice_id)` where `status = 'link_sent'`. `bot` gets S, I, U.
The webhook (role `api`) updates status.

**`handoffs`**: escalations to the owner.
`id · customer_account_id fk · conversation_id fk · reason (amount_over_threshold|customer_requested|dispute|other) ·
stripe_invoice_id · amount · currency · summary · status (open|acknowledged|resolved) · resolved_by fk null ·
resolved_at · created_at`. `bot` gets S, I.

**`customer_invites`**: RLS enabled with the `api` policy only.
`id · customer_account_id fk · token_hash unique · expires_at · used_at · used_by_telegram_user_id ·
created_by (owner fk, null = seed) · created_at`. `bot` reaches it only through `redeem_customer_invite()`.

### 5.2 Owner-scoped (`api` only; `bot` has no grant)

**`owners`**: `id · email unique · password_hash · created_at · last_login_at`.

**`owner_sessions`**: `id · owner_id fk · token_hash unique · created_at · last_seen_at · expires_at ·
revoked_at · user_agent · ip`.

**`login_attempts`**: `id · email · ip · succeeded bool · created_at`. Pruned after 24h.

**`owner_api_keys`**: `id · owner_id fk · name · key_prefix · key_hash unique · created_at ·
last_used_at · revoked_at`.

**`owner_actions`**: mutations follow propose → confirm → execute. The LLM can only create a `proposed`
row. The UI confirm button calls `POST /actions/{id}/confirm`.
`id · conversation_id fk null (null for MCP) · owner_id fk · api_key_id fk null (set when proposed via MCP) · action_type (refund|create_invoice|send_invoice|create_payment_link) ·
params jsonb · preview text · status (proposed|confirmed|executed|failed|cancelled|expired) ·
idempotency_key uuid unique (sent to Stripe) · stripe_object_id · error · created_at · confirmed_at ·
executed_at`. Proposals expire after 10 min.

**`daily_summaries`**: `id · summary_date date · timezone · stats jsonb (the exact aggregates given to the
LLM) · summary text · llm_model · generated_at`. Unique `(summary_date, timezone)`.

### 5.3 Webhooks and audit

**`stripe_events`** (`api` only): `event_id text pk · type · livemode bool · payload jsonb · received_at ·
processed_at · error`. Insert-first with `ON CONFLICT DO NOTHING` gives idempotency. Unknown types are
recorded and ignored.

Handled events (v0):

| Event | Effect |
|---|---|
| `invoice.paid` | matching `payment_requests` → `paid`; Telegram push "Payment received ✅"; audit |
| `invoice.payment_failed` | → `failed`; Telegram push with retry hint |
| `invoice.voided` / `invoice.marked_uncollectible` | → `void` |
| `charge.refunded` | audit; invalidate today's `daily_summaries` |
| `payment_intent.succeeded` / `payment_intent.payment_failed` | invalidate today's summary (decline counts) |

The webhook handler verifies the signature on the **raw body** before anything else. `stripe-cli` posts
straight to `api`, not through the Next proxy, so the body stays raw.

**`audit_log`**: append-only. `api` and `bot` get S/I and I respectively, no UPDATE or DELETE.
`id · actor_type (owner|owner_mcp|customer|system|stripe) · actor_id · customer_account_id null · action · target ·
payload jsonb · created_at`.

### 5.4 Grant matrix

| table | api | bot |
|---|---|---|
| customer_accounts | CRUD | S (RLS) |
| telegram_identities | CRUD | S (RLS), writes via definer fn |
| customer_invites | CRUD | — (definer fn) |
| conversations | CRUD | S, I, U (RLS) |
| messages | S, I | S, I (RLS) |
| payment_requests | CRUD | S, I, U (RLS) |
| handoffs | CRUD | S, I (RLS) |
| owners, owner_sessions, owner_api_keys, login_attempts, owner_actions, daily_summaries, stripe_events | CRUD | — |
| audit_log | S, I | I |

---

## 6. LLM configuration

- **LangChain `init_chat_model(LLM_MODEL)`** with the `provider:model` string (`langchain-openrouter`
  supplies `ChatOpenRouter` for the `openrouter` provider). Switching to direct Anthropic or OpenAI
  means changing the env var and adding that `langchain-*` package. No code changes.
- Tool calling uses `.bind_tools()` with tools adapted from the `core/tools` registry (§2.5). A small
  hand-rolled loop (≤ 6 tool hops) rather than a framework agent keeps the confirm gate and scoping
  explicit.
- Use `temperature=0` for extraction and tool turns. Avoid `:batch` model variants (async batch pricing,
  not interactive).

**Default model (by price, from OpenRouter's catalog on 2026-10-02, $/1M tokens in / out):**

| Model | In | Out | Notes |
|---|---|---|---|
| `deepseek/deepseek-v4-pro` | 0.21 | 0.42 | cheapest credible option |
| `openai/gpt-5.6-luna` | 0.20 | 1.20 | cheap OpenAI tier |
| **`moonshotai/kimi-k2.6`** | 0.43 | 1.83 | **default**: the Kimi K2 line is built for agentic tool use |
| `z-ai/glm-5.3-flashx` | 0.37 | 1.25 | |
| `openai/gpt-5.4-mini` | 0.75 | 4.50 | |
| `anthropic/claude-sonnet-5.5` | 2.00 | 10.00 | quality fallback |
| `moonshotai/kimi-k3` | 2.70 | 13.50 | more than Sonnet 5.5 at these prices |

`LLM_MODEL=openrouter:moonshotai/kimi-k2.6`. A typical turn (~4k in, ~400 out) costs about $0.0025 on
Kimi K2.6 and about $0.012 on Sonnet 5.5, so either is negligible for a demo. Public tool-calling
leaderboards don't cover most of these current models, so **the eval suite (§7, `-m llm_eval`)
decides**. Run the NL command set (refund Maya, invoice Acme next Friday, week-over-week, customer
injection attempts) against 2–3 candidates and pin the winner in `.env.example`.

---

### 6.1 Streaming (owner chat)

- `POST /conversations/{id}/messages {content}` → `text/event-stream`. The user message is persisted
  **before** streaming starts.
- The agent loop uses LangChain `astream`. Text deltas go out as they arrive. Tool-call chunks are
  accumulated, the tool runs, and the loop continues, up to 6 hops.
- SSE events (JSON `data:`). The same Pydantic models are exported as TS types for the client:

  | event | payload | UI |
  |---|---|---|
  | `message_start` | `{message_id, model}` | open assistant bubble |
  | `token` | `{delta}` | append text |
  | `tool_start` | `{tool, label}` e.g. "Looking up Maya's payments…" | status chip |
  | `tool_end` | `{tool, ok}` | clear chip |
  | `action_proposed` | `ActionProposal` | render **Confirm / Cancel** card |
  | `message_end` | `{message_id, input_tokens, output_tokens}` | finalize |
  | `error` | `{code, message}` | inline error; partial text kept |

- The assistant message and its tool rows are persisted at `message_end`. On client disconnect, FastAPI
  sees the cancellation, persists the partial text with `status = interrupted`, and stops the loop. Stripe
  mutations can't be half-done, because tools only propose.
- `POST /summaries/today` streams with the same event set, so the daily summary types out. The cached
  `daily_summaries` row replays instantly as a single `token` event.
- **Next proxy:** returns `new Response(upstream.body, …)` unbuffered (`X-Accel-Buffering: no`,
  `Cache-Control: no-cache`, no compression), uses `runtime = "nodejs"` and `dynamic = "force-dynamic"`,
  and forwards `request.signal` so a browser abort cancels upstream.
- **Client:** `fetch` + `ReadableStream` with an SSE line parser (not `EventSource`, which can't POST or
  send a body).
- **Telegram does not stream.** The bot sends `typing` chat actions while the loop runs, then one final
  message. Customer turns use `ainvoke`.

`messages` gets a `status` column (`complete|interrupted|error`).

## 7. Business rules that touch auth and data

- **$2,000 threshold** is enforced in `core.services.payments.request_customer_payment()`. If
  `amount_due >= HANDOFF_THRESHOLD_CENTS`, the service creates a `handoffs` row and returns no URL, and
  the bot says the business will follow up. The rule is per invoice. Partial payments are out of scope,
  so an invoice ≥ $2,000 always goes to handoff. Record this assumption in `write-up.md`.
- **Payment link** is the invoice's `hosted_invoice_url`. Seeded invoices use `collection_method=send_invoice`,
  are finalized, and are not emailed. The URL is only handed out below the threshold.
- **Customer-visible data:** only objects whose `customer == bound stripe_customer_id`. No other names, no
  totals beyond what the customer owes, no account-level figures.
- **Time:** every "today", "last week", and "next Friday" is resolved in `BUSINESS_TIMEZONE`. Range math
  happens in code, not in the LLM.
- **Seed** (`docker compose run --rm api python -m payments_assistant.seed`) is idempotent via Stripe
  `metadata.seed_tag`. It creates customers (Maya, Acme Corp, …), succeeded and declined charges
  (`pm_card_visa`, `pm_card_chargeDeclinedInsufficientFunds`) spread over today, yesterday, and the last
  two weeks, open invoices below and above $2,000, and a refund. It then upserts `customer_accounts`,
  creates the owner, and prints Telegram invite links.

---

## 8. Testing

**Python:** pytest + pytest-asyncio + httpx `AsyncClient` (ASGI transport). Markers:

- **Unit** (default, `-m "not integration"`, no network): `core` services with fake Stripe gateways and
  LangChain fakes (`GenericFakeChatModel` scripted with tool calls). Covers the threshold boundary
  (199999/200000/200001), propose/confirm/expire, invite and session token mint/hash/expiry/reuse,
  gateway rejecting foreign objects, date resolution against a frozen clock in `BUSINESS_TIMEZONE`,
  webhook signature rejection, and event idempotency. Registry invariants: no customer tool input model has
  a customer-identifying field, and every `mutating` tool returns an `ActionProposal`.
- **Integration** (`-m integration`, compose Postgres): migrations run as `admin`, tests connect as
  `api`/`bot`.
  - **RLS suite.** As `bot`: no scope → 0 rows everywhere; scope A cannot see B and inserts with B's id
    fail; no access to owner tables or `customer_invites`; no UPDATE or DELETE on `messages`/`audit_log`;
    scope doesn't leak across transactions on a pooled connection; definer functions redeem once and
    reject expired, used, or revoked tokens.
  - Streaming: with a scripted streaming fake model, `/conversations/{id}/messages` emits
    `message_start → token… → tool_start → tool_end → action_proposed → message_end` in order. The user
    message is persisted before the first event. A client disconnect mid-stream persists `interrupted`.
  - HTTP: login → session → `/auth/me` → logout revokes; 401s; throttle; webhook end-to-end with a
    locally signed payload.
  - MCP: in-process MCP client session against the mounted server. Covers no key / revoked key → 401, a
    session token rejected on `/mcp`, an API key rejected on the web API, `tools/list` contains owner tools
    only with schemas matching the registry, propose → `confirm_action` executes once, a proposal from a
    different key or an expired one is refused, and audit rows carry `owner_mcp`.
  - Bot: fake `Update`s through link, owe → link, ≥ $2,000 handoff, unlinked user, and a cross-customer
    injection prompt.
- **`-m stripe`** (opt-in, needs `sk_test_`): seed and gateway against the real test account.
- **`-m llm_eval`** (opt-in, needs `OPENROUTER_API_KEY`): the NL command set and injection set against
  `LLM_MODEL`. Used for model selection.

**Next.js:** Vitest for unit tests (proxy header handling: strips client `Authorization`, injects bearer
from cookie, Origin check, SSE passthrough; cookie flags on login/logout). Integration tests drive the route
handlers against a mocked `API_URL` (msw). A Playwright smoke test (login → summary → logout) is optional
if time allows.

---

## 9. Out of scope for v0

MCP OAuth 2.1 · customer MCP surface · magic-link login · multi-business tenancy · mirroring Stripe objects
into RLS tables · partial payments / payment plans ≥ $2,000 · object storage.

## 10. Open questions

None blocking. Next step: `docs/v0/plan.md`.
