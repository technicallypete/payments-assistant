# v0 Goal: Loop Definition of Done

Used as the goal for a self-paced `/loop`. Each iteration moves v0 forward by one plan phase (or one
failing criterion) and re-checks this list. **The loop stops only when every criterion below passes,
verified by running the commands, not by assuming.**

Sources: `docs/v0/spec.md` (what), `docs/v0/plan.md` (order), `CLAUDE.md` (rules). All commands run
through Docker. Nothing is installed on the host.

---

## Each iteration

1. Read `docs/v0/progress.md` (create it on the first run). It records phase status, the last verification
   results, blockers, and the budget table. **Check the budget first**: if a limit is hit, follow its rule.
2. Pick the **first incomplete phase** in `plan.md`. If all phases are done, pick the first failing
   criterion below.
3. Implement it. Write **unit tests and integration tests alongside the code** for every behavior the
   phase adds. A phase with untested behavior is not done.
4. Run the verification block below (at minimum the parts the phase touched, plus all unit tests).
5. Fix failures before moving on. Never skip, `xfail`, or delete a test to make it pass. Never weaken an RLS
   or threshold assertion.
6. Commit at the phase checkpoint (✅ in the plan) with a descriptive message.
7. Update `progress.md` with what changed, the command results (pass/fail counts), and the next step.
8. If blocked on something only the user can provide (a missing secret, a decision the spec doesn't
   answer), record it in `progress.md`, tell the user, and stop the loop. Don't guess at secrets or
   invent scope.

## Budget

Track all of these in `progress.md` (a "Budget" table updated every iteration). Required scope (Phases
0–8) has priority under every limit, so running out of budget after Phase 8 still leaves a submittable app.

| Limit | Value | When hit |
|---|---|---|
| **Iterations** | 15 total (11 phases + ~4 fix iterations) | Stop. Record status in `progress.md` and give the user a summary of what's done, what's failing, and what was cut. |
| **Per-phase time box** | 2× the phase's estimate in `plan.md` (e.g. Phase 2: 45 min → 90 min) | Apply the plan's cut lines in order: Phase 9 (MCP bonus), then the polished handoff-queue UI (plain table instead), then the Playwright smoke test. Note the cut in `progress.md` and move on. **Never cut** the RLS suite, the threshold tests, or the unit tests a phase requires. |
| **Repeated failure** | The same test/check failing on 3 consecutive iterations | Stop and ask the user. Don't try a fourth workaround. |
| **OpenRouter spend** | ~$4 total (raised from ~$2 by the user on 2026-10-03), checked on the OpenRouter dashboard/key API; soft cap of 6 `pytest -m llm_eval` runs | Unit and integration tests use fake models and never call OpenRouter. Stop running evals when either cap is reached; record the latest results. |

Stripe test mode is free and doesn't count against the budget. Claude usage isn't measurable from inside
the loop; the iteration cap stands in for it, and the user can interrupt at any time.

---

## Done criteria

### A. Builds and runs
- [ ] `docker compose config` parses.
- [ ] `docker compose down -v && docker compose up --build -d` leaves every long-running service healthy
      (`docker compose ps`), and `api-migrate` exits 0.
- [ ] `curl -fs localhost:8010/health` returns 200, and `localhost:3010` serves the login page.
- [ ] `stripe-cli` has written `/run/stripe/whsec`, and `api` loaded it.

### B. Unit tests (no network, no DB)
- [ ] `docker compose run --rm api-test uv run pytest -m "not integration and not stripe and not llm_eval"` → 0 failures.
- [ ] Covers at minimum: the threshold at 199999/200000/200001; owner action
      propose → confirm → execute / cancel / expire; invite and session tokens (hash only, single use,
      expiry); `CustomerStripeGateway` rejecting foreign objects; date ranges in `BUSINESS_TIMEZONE`
      with a frozen clock ("today", "last week vs week before", "next Friday"); the registry invariants (no
      customer-identifying fields in customer tools; mutating tools return `ActionProposal`); webhook
      signature rejection and event dedupe; the agent loop's SSE event order with a scripted fake model.
- [ ] Coverage on `payments_assistant/core` ≥ 85% (`--cov=payments_assistant.core --cov-fail-under=85`).
- [ ] `docker compose run --rm app bun run test` (Vitest) → 0 failures. Covers the proxy (strips client
      `Authorization`, cookie → Bearer, Origin check, unbuffered SSE passthrough, abort forwarding), the
      login/logout cookie flags, and the SSE parser.

### C. Integration tests (real Postgres)
- [ ] `docker compose run --rm api-test uv run pytest -m integration` → 0 failures.
- [ ] **RLS suite connects as role `bot`** (asserted inside the test) and passes every spec §8 case: no
      scope → 0 rows; A can't read or write B; no access to owner tables or `customer_invites`; no
      UPDATE/DELETE on `messages`/`audit_log`; no scope leak across pooled transactions; definer functions
      reject used, expired, or revoked tokens.
- [ ] HTTP: login/session/logout revocation, 401s, throttle, SSE streaming plus the `interrupted` disconnect
      path, action confirm.
- [ ] Bot: link via invite, owes → payment link, ≥ $2,000 → handoff with no URL, unlinked user, cross-customer
      injection refused, revoked identity.
- [ ] Webhook: signed `invoice.paid` → `payment_request` paid + Telegram notify (faked); duplicate event
      processed once.
- [ ] MCP (if Phase 9 is built): API-key auth on `/mcp` only, `tools/list` contains owner tools only,
      propose → `confirm_action` executes once, wrong-key or expired proposals are refused.

### D. Live checks (opt-in; run when the keys in `.env` are real)
- [ ] `pytest -m stripe` passes against the test account, and the seed is idempotent (running it twice
      creates no duplicates).
- [ ] `pytest -m llm_eval` passes ≥ 90% of the NL command set and 100% of the privacy/injection set on
      `LLM_MODEL`. Results are recorded in `write-up.md`.

### E. Brief requirements (manual smoke, recorded in `progress.md`)
- [ ] Owner: log in → the daily summary streams and reads like the brief's example (not a list).
- [ ] Owner: "Refund Maya's last payment" → Confirm card → refund exists in Stripe.
- [ ] Owner: "Create a $250 invoice for Acme Corp due next Friday" → Confirm → correct invoice and due date.
- [ ] Owner: "How much did we take last week compared to the week before?" → correct figures.
- [ ] Customer (Telegram, seeded invite): "what do I owe?" → only their invoices. Pay a < $2,000 invoice with
      `4242…` → "Payment received ✅" arrives via webhook. A ≥ $2,000 invoice → handoff, which then shows in
      the owner UI.
- [ ] Customer asks about another customer or about revenue → refused, with nothing leaked.

### F. Quality gates
- [ ] `docker compose run --rm api-test uv run ruff check .` and `ruff format --check .` are clean.
      `docker compose run --rm app bun run lint` and `docker compose run --rm app bun run typecheck` are clean.
- [ ] No secrets committed (`git grep -nE 'sk_test_[A-Za-z0-9]{10}|sk-or-|[0-9]{8,}:AA'` → none),
      and `.env` is not tracked.
- [ ] `core/` imports nothing from `http/`, `bot/`, or `mcp/` (a test or grep check enforces it).

### G. Deliverables
- [ ] `README.md`: architecture and API design choices (BFF, sessions, SSE contract, propose/confirm, RLS),
      the 4 secrets to provision, `docker compose up`, the seed command, and how to run every test tier
      and test each part.
- [ ] `write-up.md`: assumptions, challenges, limitations and next steps, the bonus rationale, and the
      model choice with eval results.
- [ ] **Fresh-sandbox run:** a new Stripe test account (or a fully cleaned one) + `down -v` + `up --build`
      + seed + sections A–C, E all pass, following only the README.
- [ ] Git history shows incremental commits per phase, on `main`.

---

When every box above is checked with evidence in `progress.md` (or a budget limit ends the loop early),
the loop marks v0 complete (or stopped), gives the
user a final summary (what was built, test counts, coverage, anything deferred), and stops.
