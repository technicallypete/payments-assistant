"use client";

import { useEffect, useState } from "react";

import { useToast } from "@/components/Toast";
import { api, ApiError } from "@/lib/client";
import { relativeTime } from "@/lib/format";
import type { ApiKey, CreatedKey } from "@/lib/types";

/**
 * Bonus: let the owner drive Penny from Claude (Desktop or Code) over MCP. Keys are shown once;
 * the API stores only a hash. Every money movement still needs a confirm_action call.
 */
export function ConnectClaudePanel() {
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [created, setCreated] = useState<CreatedKey | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  async function load() {
    try {
      setKeys(await api<ApiKey[]>("api-keys"));
    } catch {
      setKeys([]);
    }
  }

  useEffect(() => {
    let alive = true;
    api<ApiKey[]>("api-keys")
      .then((rows) => alive && setKeys(rows))
      .catch(() => alive && setKeys([]));
    return () => {
      alive = false;
    };
  }, []);

  async function create() {
    setBusy(true);
    try {
      const key = await api<CreatedKey>("api-keys", {
        method: "POST",
        body: JSON.stringify({ name: "Claude" }),
      });
      setCreated(key);
      await load();
    } catch (err) {
      toast(err instanceof ApiError ? err.detail : "Couldn't create a key.", "error");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(k: ApiKey) {
    try {
      await api<void>(`api-keys/${k.id}`, { method: "DELETE" });
      if (created?.id === k.id) setCreated(null);
      toast(`Revoked ${k.display_prefix}…`);
      await load();
    } catch (err) {
      toast(err instanceof ApiError ? err.detail : "Couldn't revoke the key.", "error");
    }
  }

  async function copy(label: string, value: string) {
    try {
      await navigator.clipboard.writeText(value);
      toast(`${label} copied`);
    } catch {
      window.prompt(label, value);
    }
  }

  const active = (keys ?? []).filter((k) => !k.revoked_at);

  return (
    <section aria-labelledby="connect-h" className="sheet p-5">
      <div className="flex items-baseline justify-between">
        <h2 id="connect-h" className="font-display text-xl text-ink">
          Connect Claude
        </h2>
        <button
          type="button"
          onClick={() => void create()}
          disabled={busy}
          className="text-xs font-semibold uppercase tracking-[0.12em] text-accent disabled:opacity-50"
        >
          {busy ? "Creating…" : "Create key"}
        </button>
      </div>
      <p className="mt-2 text-sm text-muted">
        Manage payments from Claude Desktop or Claude Code. Refunds and invoices still wait for
        your confirmation.
      </p>

      {created && (
        <div role="status" className="mt-4 rounded-xl border border-gold bg-gold-soft p-3">
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-gold">
            Copy it now: this key won&apos;t be shown again
          </p>
          <code className="money mt-2 block break-all text-[13px] text-ink">{created.key}</code>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void copy("Key", created.key)}
              className="rounded-full border border-rule px-3 py-1 text-xs text-ink-2"
            >
              Copy key
            </button>
            <button
              type="button"
              onClick={() => void copy("Claude Code command", created.config.claude_code)}
              className="rounded-full border border-rule px-3 py-1 text-xs text-ink-2"
            >
              Copy Claude Code command
            </button>
            <button
              type="button"
              onClick={() => void copy("Claude Desktop config", created.config.claude_desktop)}
              className="rounded-full border border-rule px-3 py-1 text-xs text-ink-2"
            >
              Copy Claude Desktop config
            </button>
          </div>
          <p className="mt-2 text-xs text-muted">
            Endpoint: <span className="money">{created.mcp_url}</span>
          </p>
          <button
            type="button"
            onClick={() => setCreated(null)}
            className="mt-2 text-xs text-muted underline-offset-2 hover:underline"
          >
            I&apos;ve saved it
          </button>
        </div>
      )}

      {keys === null ? (
        <p className="mt-3 text-sm text-muted">Loading…</p>
      ) : active.length === 0 ? (
        <p className="mt-3 text-sm text-muted">No active keys.</p>
      ) : (
        <ul className="mt-3 divide-y divide-rule text-sm">
          {active.map((k) => (
            <li key={k.id} className="flex items-center justify-between py-2">
              <span>
                <span className="money text-ink">{k.display_prefix}…</span>
                <span className="ml-2 text-xs text-muted">
                  {k.last_used_at ? `used ${relativeTime(k.last_used_at)}` : "never used"}
                </span>
              </span>
              <button
                type="button"
                onClick={() => void revoke(k)}
                aria-label={`Revoke key ${k.display_prefix}`}
                className="text-xs text-margin underline-offset-2 hover:underline"
              >
                Revoke
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
