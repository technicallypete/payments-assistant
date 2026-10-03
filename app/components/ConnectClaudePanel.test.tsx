// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/Toast";

import { ConnectClaudePanel } from "./ConnectClaudePanel";

const KEY = "pak_live_secret_key_value_1234567890";
const stored = {
  id: "k1",
  name: "Claude",
  display_prefix: "pak_live_sec",
  created_at: "2026-10-03T12:00:00Z",
  last_used_at: null,
  revoked_at: null as string | null,
};

function json(body: unknown, status = 200) {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function backend() {
  let keys: (typeof stored)[] = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (url === "/api/api-keys" && method === "GET") return json(keys);
    if (url === "/api/api-keys" && method === "POST") {
      keys = [{ ...stored }];
      return json(
        {
          id: "k1",
          name: "Claude",
          display_prefix: stored.display_prefix,
          key: KEY,
          mcp_url: "http://localhost:3010/mcp",
          config: {
            claude_code: `claude mcp add --transport http penny http://localhost:3010/mcp --header "Authorization: Bearer ${KEY}"`,
            claude_desktop: "{}",
          },
        },
        201,
      );
    }
    if (url === "/api/api-keys/k1" && method === "DELETE") {
      keys = keys.map((k) => ({ ...k, revoked_at: "2026-10-03T13:00:00Z" }));
      return new Response(null, { status: 204 });
    }
    return json({ detail: "unexpected" }, 500);
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

function renderPanel() {
  return render(
    <ToastProvider>
      <ConnectClaudePanel />
    </ToastProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ConnectClaudePanel", () => {
  it("shows a new key once, with copyable client config, then lists only its prefix", async () => {
    backend();
    renderPanel();
    expect(await screen.findByText("No active keys.")).toBeTruthy();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Create key" }));
    });
    expect(await screen.findByText(KEY)).toBeTruthy();
    expect(screen.getByText(/won.t be shown again/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy Claude Code command" })).toBeTruthy();
    expect(screen.getByText("pak_live_sec…")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "I've saved it" }));
    await waitFor(() => expect(screen.queryByText(KEY)).toBeNull());
    // The full key is gone for good; only the display prefix remains.
    expect(screen.getByText("pak_live_sec…")).toBeTruthy();
  });

  it("copies the Claude Code command to the clipboard", async () => {
    backend();
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    renderPanel();
    await act(async () => {
      fireEvent.click(await screen.findByRole("button", { name: "Create key" }));
    });
    await act(async () => {
      fireEvent.click(await screen.findByRole("button", { name: "Copy Claude Code command" }));
    });
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining(`Bearer ${KEY}`));
  });

  it("revokes a key and drops it from the active list", async () => {
    const fetch = backend();
    renderPanel();
    await act(async () => {
      fireEvent.click(await screen.findByRole("button", { name: "Create key" }));
    });
    await act(async () => {
      fireEvent.click(await screen.findByRole("button", { name: "Revoke key pak_live_sec" }));
    });
    expect(fetch).toHaveBeenCalledWith("/api/api-keys/k1", expect.objectContaining({ method: "DELETE" }));
    expect(await screen.findByText("No active keys.")).toBeTruthy();
    expect(screen.queryByText(KEY)).toBeNull();
  });
});
