"use client";

import { useEffect, useState } from "react";

import { actionResultPatch, type ProposedAction } from "@/lib/chat-state";
import { api, ApiError } from "@/lib/client";
import { countdown } from "@/lib/format";
import type { Action } from "@/lib/types";

const TITLES: Record<string, string> = {
  refund: "Refund",
  create_invoice: "New invoice",
  send_invoice: "Send invoice",
  create_payment_link: "Payment link",
};

const STAMPS: Partial<Record<ProposedAction["state"], { text: string; cls: string }>> = {
  executed: { text: "Done", cls: "text-accent border-accent" },
  failed: { text: "Failed", cls: "text-margin border-margin" },
  expired: { text: "Expired", cls: "text-muted border-muted" },
  cancelled: { text: "Cancelled", cls: "text-muted border-muted" },
};

/**
 * A proposed money movement. Nothing happens in Stripe until the owner presses Confirm, which
 * calls POST /actions/{id}/confirm (the only path that executes).
 */
export function ActionCard({
  action,
  onChange,
}: {
  action: ProposedAction;
  onChange: (patch: Partial<ProposedAction>) => void;
}) {
  const [now, setNow] = useState(() => new Date());
  const open = action.state === "proposed";
  const busy = action.state === "confirming" || action.state === "cancelling";
  const left = countdown(action.expiresAt, now);

  useEffect(() => {
    if (!open) return;
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, [open]);

  useEffect(() => {
    if (open && left === null) onChange({ state: "expired" });
  }, [open, left, onChange]);

  async function decide(kind: "confirm" | "cancel") {
    onChange({ state: kind === "confirm" ? "confirming" : "cancelling", error: null });
    try {
      const result = await api<Action>(`actions/${action.id}/${kind}`, { method: "POST" });
      onChange(actionResultPatch(result));
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : "Couldn't reach the server.";
      onChange({ state: err instanceof ApiError && err.status === 409 ? "failed" : "proposed", error: detail });
    }
  }

  const stamp = STAMPS[action.state];
  return (
    <section
      aria-label={`${TITLES[action.actionType] ?? "Action"} awaiting your decision`}
      className="sheet relative mt-3 overflow-hidden border-l-4 border-l-gold p-4"
    >
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-semibold uppercase tracking-[0.14em] text-gold">
          {TITLES[action.actionType] ?? action.actionType} · needs your OK
        </p>
        {open && left && (
          <span className="tabular text-xs text-muted" aria-label={`Expires in ${left}`}>
            {left}
          </span>
        )}
      </div>
      <p className="mt-2 text-[15px] leading-snug text-ink">{action.preview}</p>

      {(open || busy) && (
        <div className="mt-4 flex gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => decide("confirm")}
            className="rounded-full bg-accent px-4 py-1.5 text-sm font-semibold text-accent-ink disabled:opacity-60"
          >
            {action.state === "confirming" ? "Confirming…" : "Confirm"}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => decide("cancel")}
            className="rounded-full border border-rule-strong px-4 py-1.5 text-sm text-ink-2 disabled:opacity-60"
          >
            {action.state === "cancelling" ? "Cancelling…" : "Cancel"}
          </button>
        </div>
      )}

      {stamp && (
        <div className="mt-3 flex items-center gap-3">
          <span
            className={`stamp inline-block -rotate-3 rounded border-2 px-2 py-0.5 text-xs font-bold uppercase tracking-widest ${stamp.cls}`}
          >
            {stamp.text}
          </span>
          {action.state === "executed" && action.stripeObjectId && (
            <span className="tabular text-xs text-muted">{action.stripeObjectId}</span>
          )}
        </div>
      )}
      {action.error && (
        <p role="alert" className="mt-2 text-sm text-margin">
          {action.error}
        </p>
      )}
    </section>
  );
}
