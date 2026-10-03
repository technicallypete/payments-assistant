# Write-up: Penny, the AI Payments Assistant

This covers the assumptions behind the build, what was hard, what's missing, and the bonus feature.
The full design is in `docs/v0/spec.md`. `docs/v0/progress.md` logs every iteration, including the
bugs found and how each was fixed.

---

## Assumptions

- **One business per deployment.** There's a single owner account, seeded from `.env`, and no sign-up.
  Supporting several businesses would add a `business_id` and a second RLS dimension (see Limitations).
- **The $2,000 rule applies per invoice**, to the amount still owed (`amount_remaining`). A payment of
  `>= $2,000.00` is never completed by the bot; an invoice for exactly $2,000 is handed off. The rule is
  enforced in `core.services.payments`, not in a prompt.
- **No partial payments.** An invoice at or above the threshold always goes to the owner as a handoff.
  The bot never offers to split it.
- **Customers link Telegram with a single-use invite link** (`t.me/<bot>?start=<token>`) that the owner
  or the seed generates. There's no phone or email matching. A forwarded link only works once.
- **Business timezone is `America/New_York`** (`BUSINESS_TIMEZONE`). "Today", "last week" and "next
  Friday" are resolved in code. Weeks run Monday to Sunday.
- **"Next Friday" means the first Friday strictly after today.** The confirmation card shows the
  resolved date, so the owner can see it and cancel if it's wrong.
- **Stripe test mode only.** The app refuses to start unless the key begins with `sk_test_`.
- **Seeded history is backdated through metadata.** Stripe can't backdate charges, so the seed puts
  `pa_occurred_at` on each PaymentIntent, and the gateways use it as the business timestamp. This is a
  demo-only mechanism; real charges use Stripe's `created`.
- **Charges can't be deleted.** The seed's `--reset` and the live tests' teardown tag them
  `pa_hidden=1`, and the gateways skip those charges.
- **Seeded customers have email addresses.** Stripe refuses to create `send_invoice` invoices for
  customers without one.

## Security & privacy model

Customer privacy is enforced in three independent layers, so one bug can't leak data on its own:

1. **Tool binding.** Customer tools have no customer-identifying inputs; a registry test enforces
   this. The customer's Stripe id is bound from their Telegram identity. The customer gateway also
   re-checks `obj.customer` on every object, and treats a foreign invoice exactly like an unknown one.
2. **Database role.** The bot connects as Postgres role `bot`. It has grants only on customer-scoped
   tables, plus `EXECUTE` on three `SECURITY DEFINER` identity functions (redeem, resolve, revoke).
3. **Row-level security.** Customer tables use `FORCE ROW LEVEL SECURITY`, keyed on a
   transaction-local `app.customer_account_id`. With no scope set, queries return zero rows. A garbage
   value makes queries fail instead of returning rows. The 61-case RLS suite runs as `bot`, never as
   the superuser, because the superuser bypasses RLS.

Owner authentication:
- **Browser:** the Next.js route handlers act as a BFF. The session token lives only in an httpOnly,
  SameSite=Lax cookie, and the proxy turns it into a Bearer header. Browser JavaScript never sees it.
  Sessions are opaque tokens stored as sha256 hashes, slide for 7 days, and are revoked on logout.
  Logins are throttled.
- **Proxy:** non-GET requests must pass an Origin check, and only an allowlist of API areas is
  reachable. Webhooks and docs stay internal.
- **API keys** (`pak_…`, stored as sha256) are accepted **only** on `/mcp`, and session tokens are
  refused there.

Money only moves through **propose → confirm**:
- The LLM can only create `owner_actions` proposals.
- A human confirms each one, via a button in the web UI or `confirm_action` over MCP using the same key
  that proposed it.
- Execution uses Stripe idempotency keys, and proposals expire after 10 minutes.

## Challenges and how they were overcome

- **Testing RLS honestly.** The first version of the tests would have passed while proving nothing,
  because a superuser bypasses RLS. The fixtures now open per-role engines (`api`, `bot`,
  `reporter`), and a test asserts that `bot` is not a superuser and has no `BYPASSRLS`. Table grants
  live in migrations rather than `ALTER DEFAULT PRIVILEGES`, so the throwaway test database matches dev.
- **LLM turns hanging for minutes.** Two evaluation runs stalled indefinitely. A pytest
  `faulthandler_timeout` stack dump showed `langchain-openrouter`'s SDK retrying timed-out streaming
  requests with backoff, regardless of the timeout we set. Raw HTTP to OpenRouter answered the same
  request in 4–5 s. The fix was to route `openrouter:<model>` through LangChain's OpenAI-compatible
  client with OpenRouter's base URL. The `LLM_MODEL` format didn't change.
- **Model quirks became tool design.**
  - Kimi sometimes ended a turn with an empty reply after a tool call. Streamed `reasoning_details`
    were also being corrupted on concatenation and sent back to the provider. The loop now sends back
    clean messages and nudges the model once.
  - On "pay it", the model invented an invoice id it had never seen. `pay_invoice` now accepts an id,
    an invoice number, or nothing (meaning the only open invoice).
  - The model asked for a line-item description instead of proposing the invoice. The description now
    defaults to "Services".
- **A webhook bug that only appeared live.** Real Stripe events carry `*_decimal` fields. Stripe SDK 16
  turns those into Python `Decimal`s, which JSONB can't store, so some webhooks returned 500. My test
  payloads didn't include those fields. The webhook now processes the verified raw JSON body, and a
  regression test covers it. The failed event was replayed with `stripe events resend`.
- **Test pollution of the demo account.** The first live summary reported $4,505 across 17 payments
  with 11 declines, instead of the seeded $4,280 across 8 payments with 2 declines. The extra rows were
  charges left behind by live gateway tests, whose throwaway customers had been deleted but whose
  charges hadn't been hidden. I hid only charges belonging to verified deleted `pa_test` customers, and
  the test teardown now hides its own charges.
- **A UI race found by a headless browser.** A click that arrived before the first conversation
  finished loading let the late load wipe the live turn, so the new proposal card never appeared. A
  navigation guard fixed it, and a component test reproduces the race (it fails without the guard).
- **Streaming and disconnects.**
  - The user's message is persisted before streaming starts.
  - The reply, tool rows and proposals are committed in a shielded `finally` as
    complete, interrupted or error, so a closed tab doesn't lose a turn.
  - The Next proxy forwards the abort upstream, and the transcript is ordered by an identity column
    because timestamps tie within a turn.
- **Summary latency.** Kimi's default reasoning made the summary's first token take 28 s. Summaries
  use no tools, so reasoning is now off for them (`LLM_SUMMARY_REASONING_EFFORT=none`). The first token
  arrives in about 8 s and the full summary in about 10.6 s.

## Limitations and what I'd do with more time

- **No Stripe → Postgres mirror.** Summaries and lookups read Stripe live. With a webhook-driven mirror,
  money data could also sit under RLS, summaries would be faster, and history wouldn't need the 120-day
  lookback window.
- **MCP confirmation depends on the client.** It relies on the client honouring `destructiveHint` and
  asking the human. The web UI has a real button. Mitigations: proposals are bound to the key that made
  them and expire after 10 minutes.
- **MCP has no OAuth.** It uses static API keys. OAuth 2.1, as the MCP auth spec describes, would be
  the production path.
- **Single owner and single business.** No roles, no team members, no multi-tenancy.
- **No partial payments or payment plans** for invoices at or above $2,000. Those are handed off
  entirely.
- **Telegram uses long polling,** not webhooks. That's simpler to run locally, but a deployment would
  switch to webhooks.
- **Latency.** Kimi K2.6 with reasoning is cheap but can take several seconds per tool turn. A faster
  default model for the owner chat, or prompt caching, would help.
- **A small evaluation set:** 11 cases. It needs more phrasings, more adversarial privacy prompts and
  multi-turn flows, and to run in CI against a pinned model.
- **No end-to-end browser test in CI.** The UI was verified with headless Chromium manually, plus
  Vitest component tests. A Playwright smoke test (log in, summary, propose, cancel) would be next.
- **Seed backdating via metadata is a demo hack.** Test clocks, or a mirror with imported history,
  would be cleaner.
- **`send_invoice` needs a customer email.** Creating an invoice for a customer without one surfaces
  Stripe's error instead of collecting the email first.

## Bonus feature: an owner MCP server

**Why:** owners increasingly live in Claude (Desktop, Code). Running their payments from where they
already work, alongside email, spreadsheets and calendars, is more useful than another dashboard tab.

**What it is:** the same owner tools as the web agent, served at `/mcp`. It runs on MCP SDK 2.3 as a
stateless, streamable-HTTP low-level `Server`, and Next rewrites the app's `/mcp` to it. The "Connect
Claude" dashboard panel (or `python -m payments_assistant.keys create`) mints a key and shows
ready-to-paste config for Claude Code and Claude Desktop.

**Same guardrails as the web app:**
- Tool schemas come straight from the shared registry, so there's no drift.
- Reads are annotated read-only, and `propose_*` only records a proposal.
- `confirm_action` is annotated destructive, so clients ask the human first.
- Every Stripe call uses the proposal's idempotency key.
- Everything is audited as `owner_mcp` with the key id.

**Security:** keys are `pak_…`, stored as sha256 hashes, revocable, and valid only on `/mcp`. Browser
sessions are refused there. Customers get no MCP surface, because an MCP client can't establish which
customer is calling the way a linked Telegram identity can.

**Verified live:** a real MCP client session through the Next rewrite listed the 9 tools.
`get_activity` returned the live Stripe figures ($4,280.00 across 8 payments, 2 failed). A request
without a key gets a 401.

## Model choice & eval results

> **Placeholder: the parent fills in run 5 below.**

- **Default model:** `openrouter:moonshotai/kimi-k2.6`, at $0.43 in / $1.83 out per million tokens on
  OpenRouter. The Kimi K2 line is built for agentic tool use, and it costs a fraction of Sonnet 5.5
  ($2 / $10). The model is set by the `LLM_MODEL` env var (`provider:model`), so switching is a
  config change.
- **Evaluation suite** (`pytest -m llm_eval`): 6 owner commands from the brief, plus 5 customer
  privacy and payment cases.
  - **Run 1: 8/11.** The customer got an empty reply after a tool call; the model asked for an invoice
    description instead of proposing; and one summary check was too strict ("failed" vs "declined").
  - **Runs 2–3: hung** (the `langchain-openrouter` SDK retry bug above). Run 3 passed its first 4
    tests, all customer-privacy prompts, before the hang.
  - **Run 4: 10/11 in 70 s.** All 6 owner commands passed. The one failure was in the test criteria:
    the bot politely refused "show me Acme Corp's invoices" and repeated the name the customer had
    typed. The check now only flags terms that weren't in the customer's own message, and other
    customers' figures or invoice numbers are never allowed.
  - **Run 5 (final code): 11/11 in 152 s.** All 6 owner commands passed: the $82.00 refund
    proposal for Maya, the $250 Acme invoice due Fri Oct 9, $1,000 vs $800 (+25%), an accurate day
    summary, refusing to skip confirmation, and Bluebird's $250. All 5 customer cases passed. That
    meets the goal's thresholds (≥ 90% of commands, 100% of privacy).
- **OpenRouter spend** for the whole build, including 5 evaluation runs and the live checks: **$0.18**
  (from the OpenRouter key's usage figure).

## How AI tools were used

- I built this with **Claude Code**, using subagents for parallel work (for example, the RLS test
  suite, the Stripe gateways, the seed and the UI were each delegated while the core was written).
- The work was driven by written documents:
  - a **spec** (`docs/v0/spec.md`) for auth, data and RLS;
  - a phased **plan** with cut lines (`docs/v0/plan.md`);
  - a **definition of done** with a budget (`docs/v0/goal.md`), run in a self-paced loop of one phase
    per iteration;
  - a running **progress log** (`docs/v0/progress.md`).
- Claims were checked rather than assumed:
  - every phase ended with unit and integration tests;
  - live checks ran against the real Stripe test account, the real model and the real Telegram bot;
  - headless-browser screenshots were used for the UI.

  Several of the bugs above, including the Decimal webhook, the test pollution and the UI race, were
  found by those live checks, not by the unit tests.
