"use client";

import { useCallback, useEffect, useState } from "react";

import { useToast } from "@/components/Toast";
import { api, ApiError } from "@/lib/client";
import type { Customer, Invite } from "@/lib/types";

export function CustomersPanel({ refreshKey = 0 }: { refreshKey?: number }) {
  const [items, setItems] = useState<Customer[] | null>(null);
  const [syncing, setSyncing] = useState(false);
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      setItems(await api<Customer[]>("customers"));
    } catch {
      setItems([]);
    }
  }, []);

  useEffect(() => {
    let alive = true;
    api<Customer[]>("customers")
      .then((rows) => alive && setItems(rows))
      .catch(() => alive && setItems([]));
    return () => {
      alive = false;
    };
  }, [refreshKey]);

  async function invite(c: Customer) {
    try {
      const inv = await api<Invite>(`customers/${c.id}/invite`, { method: "POST" });
      try {
        await navigator.clipboard.writeText(inv.url);
        toast(`Telegram invite for ${c.display_name} copied`);
      } catch {
        window.prompt(`Telegram invite for ${c.display_name}`, inv.url);
      }
    } catch (err) {
      toast(err instanceof ApiError ? err.detail : "Couldn't create an invite.", "error");
    }
  }

  async function sync() {
    setSyncing(true);
    try {
      const r = await api<{ synced: number }>("customers/sync", { method: "POST" });
      toast(`Synced ${r.synced} customers from Stripe`);
      await load();
    } catch (err) {
      toast(err instanceof ApiError ? err.detail : "Sync failed.", "error");
    } finally {
      setSyncing(false);
    }
  }

  return (
    <section aria-labelledby="customers-h" className="sheet p-5">
      <div className="flex items-baseline justify-between">
        <h2 id="customers-h" className="font-display text-xl text-ink">
          Customers
        </h2>
        <button
          type="button"
          onClick={() => void sync()}
          disabled={syncing}
          className="text-xs font-semibold uppercase tracking-[0.12em] text-accent disabled:opacity-50"
        >
          {syncing ? "Syncing…" : "Sync from Stripe"}
        </button>
      </div>
      {items === null ? (
        <p className="mt-3 text-sm text-muted">Loading…</p>
      ) : items.length === 0 ? (
        <p className="mt-3 text-sm text-muted">No customers yet. Run the seed or sync from Stripe.</p>
      ) : (
        <table className="mt-3 w-full text-sm">
          <caption className="sr-only">Customers and what they owe</caption>
          <thead>
            <tr className="text-left text-xs uppercase tracking-[0.1em] text-muted">
              <th scope="col" className="pb-2 font-normal">Name</th>
              <th scope="col" className="pb-2 text-right font-normal">Owes</th>
              <th scope="col" className="pb-2 text-right font-normal"><span className="sr-only">Telegram</span></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-rule">
            {items.map((c) => (
              <tr key={c.id}>
                <td className="break-words py-2 pr-2">
                  <span className="text-ink">{c.display_name}</span>
                  {c.telegram_linked && (
                    <span className="ml-2 rounded bg-accent-soft px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-accent">
                      Telegram
                    </span>
                  )}
                </td>
                <td className={`money py-2 text-right ${c.amount_owed_cents > 0 ? "text-ink" : "text-muted"}`}>
                  {c.amount_owed}
                </td>
                <td className="py-2 pl-2 text-right">
                  <button
                    type="button"
                    onClick={() => void invite(c)}
                    className="whitespace-nowrap text-xs text-accent underline-offset-2 hover:underline"
                    aria-label={`Copy Telegram invite for ${c.display_name}`}
                  >
                    Copy invite
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
