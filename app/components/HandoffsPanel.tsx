"use client";

import { useCallback, useEffect, useState } from "react";

import { useToast } from "@/components/Toast";
import { api, ApiError } from "@/lib/client";
import { REASON_LABEL, relativeTime } from "@/lib/format";
import type { Handoff } from "@/lib/types";

/** "Needs you": customer conversations the bot handed to the owner (e.g. ≥ $2,000 payments). */
export function HandoffsPanel({
  refreshKey = 0,
  onCount,
}: {
  refreshKey?: number;
  onCount?: (count: number) => void; // e.g. the phone tab bar's badge
}) {
  const [items, setItems] = useState<Handoff[] | null>(null);
  const toast = useToast();

  useEffect(() => {
    if (items !== null) onCount?.(items.length);
  }, [items, onCount]);

  const load = useCallback(async () => {
    try {
      setItems(await api<Handoff[]>("handoffs?status=open"));
    } catch {
      setItems([]);
    }
  }, []);

  useEffect(() => {
    let alive = true;
    api<Handoff[]>("handoffs?status=open")
      .then((rows) => alive && setItems(rows))
      .catch(() => alive && setItems([]));
    return () => {
      alive = false;
    };
  }, [refreshKey]);

  async function act(h: Handoff, kind: "acknowledge" | "resolve") {
    try {
      await api(`handoffs/${h.id}/${kind}`, { method: "POST" });
      toast(kind === "resolve" ? `Resolved — ${h.customer}` : `Acknowledged — ${h.customer}`);
      await load();
    } catch (err) {
      toast(err instanceof ApiError ? err.detail : "That didn't work.", "error");
    }
  }

  const count = items?.length ?? 0;
  return (
    <section aria-labelledby="handoffs-h" className="sheet p-5">
      <h2 id="handoffs-h" className="flex items-center gap-2 font-display text-xl text-ink">
        Needs you
        {count > 0 && (
          <span className="tabular rounded-full bg-margin px-2 py-0.5 text-xs font-semibold text-paper" aria-label={`${count} open`}>
            {count}
          </span>
        )}
      </h2>
      {items === null ? (
        <p className="mt-3 text-sm text-muted">Loading…</p>
      ) : items.length === 0 ? (
        <p className="mt-3 text-sm text-muted">Nothing waiting. The bot has it handled.</p>
      ) : (
        <ul className="mt-3 divide-y divide-rule">
          {items.map((h) => (
            <li key={h.id} className="py-3">
              <div className="flex items-baseline justify-between gap-2">
                <p className="font-semibold text-ink">{h.customer}</p>
                {h.amount && <p className="money text-sm text-ink">{h.amount}</p>}
              </div>
              <p className="text-xs text-muted">
                {REASON_LABEL[h.reason] ?? h.reason} · {relativeTime(h.created_at)}
                {h.status === "acknowledged" && " · acknowledged"}
              </p>
              {h.summary && <p className="mt-1 text-sm text-ink-2">{h.summary}</p>}
              <div className="mt-2 flex gap-3 text-xs font-semibold uppercase tracking-[0.1em]">
                {h.status === "open" && (
                  <button type="button" onClick={() => void act(h, "acknowledge")} className="text-ink-2 hover:text-ink">
                    Acknowledge
                  </button>
                )}
                <button type="button" onClick={() => void act(h, "resolve")} className="text-accent">
                  Resolve
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
