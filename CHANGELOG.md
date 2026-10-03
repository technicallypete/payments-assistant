# Changelog

## 0.1.0 (2026-10-03)

First release: Penny, an AI payments assistant for a small business on Stripe (test mode).

**Owner web app (Next.js 16 + FastAPI)**
- Streaming chat with tools: daily summary, natural-language queries ("last week vs the week before"),
  and refunds, invoices and payment links as **proposals** confirmed with a button (idempotent,
  audited, 10-minute expiry).
- Today summary, a "Needs you" handoff queue, and customers with balances and Telegram invite links.
- BFF proxy: httpOnly session cookie, opaque server-side sessions, Origin check, path allowlist,
  unbuffered SSE.
- Ledger design in light and dark modes; phone layout with bottom tabs, a conversation picker sheet
  and tab transitions.

**Customer Telegram bot**
- Invite-link linking; customers see and pay only their own invoices via Stripe's hosted page.
- Payments of **$2,000 or more** are handed off to the owner (enforced in code).
- Privacy in three layers: tool binding, a separate Postgres role, and row-level security.

**Stripe**
- Webhook (signature-verified, idempotent) marks payments paid and pushes "Payment received ✅" to
  Telegram.
- Idempotent seed with demo history, invite links, an owner login and an MCP key.

**Bonus: owner MCP server**
- `/mcp` (streamable HTTP) with revocable `pak_` API keys; the same tools and confirm step as the
  web app.

**Quality**
- About 410 Python tests (including a 61-case RLS suite run as the restricted role) and 93 Vitest
  tests; ~94–95% core coverage.
- LLM evals 11/11 on `openrouter:moonshotai/kimi-k2.6`; total OpenRouter spend $0.18.

Fixed before release (found in hands-on use): refunds that defaulted to the full amount, along with
duplicate proposal cards; Connect MCP setup instructions; phone responsiveness.
