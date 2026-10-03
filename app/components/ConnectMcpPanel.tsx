"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

import { useToast } from "@/components/Toast";
import { api, ApiError } from "@/lib/client";
import { relativeTime } from "@/lib/format";
import type { ApiKey, CreatedKey } from "@/lib/types";

/**
 * Bonus: let the owner drive Penny from any MCP client (Claude Code / Claude Desktop shown as the
 * worked examples). Keys are shown once; the API stores only a hash. Every money movement still
 * needs a confirm_action call.
 */
const noopSubscribe = () => () => {};

/** This app's /mcp URL (the Next rewrite forwards it to the API). Server render uses a neutral
 * placeholder so hydration matches; the client fills in its real origin. */
function useMcpUrl(): string {
  return useSyncExternalStore(
    noopSubscribe,
    () => `${window.location.origin}/mcp`,
    () => "/mcp",
  );
}

export function ConnectMcpPanel() {
  const mcpUrl = useMcpUrl();
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
          Connect MCP
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
        Use Penny&apos;s tools from an MCP client such as Claude Code or Claude Desktop. Refunds
        and invoices still wait for your confirmation.
      </p>

      <McpSteps mcpUrl={created?.mcp_url ?? mcpUrl} keyHint={created ? created.key : null} />

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

const STEP_CODE = "money mt-1 block break-all rounded-md bg-paper px-2 py-1 text-[12px] text-ink";

/** Setup guide. With a freshly created key the commands include it; otherwise a placeholder. */
function McpSteps({ mcpUrl, keyHint }: { mcpUrl: string; keyHint: string | null }) {
  const key = keyHint ?? "pak_YOUR_KEY";
  return (
    <details className="mt-3 rounded-xl border border-rule p-3 text-sm text-ink-2">
      <summary className="cursor-pointer select-none font-semibold text-ink">How to connect</summary>
      <ol className="mt-3 list-decimal space-y-3 pl-5">
        <li>
          Click <strong>Create key</strong> and copy it. It&apos;s shown only once; only a hash
          is stored.
        </li>
        <li>
          <strong>Claude Code:</strong> in your terminal run
          <code className={STEP_CODE}>
            claude mcp add --transport http penny {mcpUrl} --header &quot;Authorization: Bearer{" "}
            {key}&quot;
          </code>
          then start <code className="money">claude</code> and check <code className="money">/mcp</code>{" "}
          lists <strong>penny</strong> as connected.
        </li>
        <li>
          <strong>Claude Desktop:</strong> use <strong>Copy Claude Desktop config</strong> (after
          creating a key), paste it into <code className="money">claude_desktop_config.json</code>{" "}
          and restart Desktop. It bridges with <code className="money">npx mcp-remote</code>, so
          Node must be installed.
        </li>
        <li>
          <strong>Other MCP clients:</strong> point them at{" "}
          <code className="money break-all">{mcpUrl}</code> (streamable HTTP) with the header{" "}
          <code className="money">Authorization: Bearer &lt;key&gt;</code>.
        </li>
        <li>
          Try: &ldquo;How did we do today compared to yesterday?&rdquo;, &ldquo;Who still owes us
          money?&rdquo;, &ldquo;Refund $50 of Maya&apos;s last payment&rdquo;.
        </li>
        <li>
          Money only moves when the client calls <code className="money">confirm_action</code>,
          which is marked destructive, so Claude asks for your approval first. Proposals made over
          MCP can only be confirmed with the same key. <strong>Revoke</strong> a key here when
          you&apos;re done.
        </li>
      </ol>
    </details>
  );
}
