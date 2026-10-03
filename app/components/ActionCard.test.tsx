// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ProposedAction } from "@/lib/chat-state";

import { ActionCard } from "./ActionCard";

const base: ProposedAction = {
  id: "act-1",
  actionType: "refund",
  preview: "Refund $82.00 to Maya Chen (payment of $82.00 on Sat Oct 3)",
  expiresAt: new Date(Date.now() + 5 * 60_000).toISOString(),
  state: "proposed",
};

function Harness({ initial }: { initial: ProposedAction }) {
  const [a, setA] = useState(initial);
  return <ActionCard action={a} onChange={(p) => setA((x) => ({ ...x, ...p }))} />;
}

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("ActionCard", () => {
  it("shows the preview with Confirm and Cancel while proposed", () => {
    render(<Harness initial={base} />);
    expect(screen.getByText(/Refund \$82\.00 to Maya Chen/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Confirm" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
  });

  it("Confirm POSTs to the action and stamps it Done with the Stripe id", async () => {
    const fetch = mockFetch(200, {
      id: "act-1",
      action_type: "refund",
      preview: base.preview,
      status: "executed",
      stripe_object_id: "re_3Q123",
      error: null,
      expires_at: base.expiresAt,
      created_at: base.expiresAt,
    });
    render(<Harness initial={base} />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    });
    expect(fetch).toHaveBeenCalledWith("/api/actions/act-1/confirm", expect.objectContaining({ method: "POST" }));
    expect(await screen.findByText("Done")).toBeTruthy();
    expect(screen.getByText("re_3Q123")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
  });

  it("Cancel stamps it Cancelled", async () => {
    mockFetch(200, { ...base, action_type: "refund", status: "cancelled", stripe_object_id: null, error: null, expires_at: base.expiresAt, created_at: base.expiresAt });
    render(<Harness initial={base} />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    });
    expect(await screen.findByText("Cancelled")).toBeTruthy();
  });

  it("a 409 (already decided) shows the server's message and closes the card", async () => {
    mockFetch(409, { detail: "This action is already executed." });
    render(<Harness initial={base} />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    });
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "This action is already executed.");
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
  });

  it("a network failure keeps the card open so the owner can retry", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("network down");
    }));
    render(<Harness initial={base} />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    });
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Couldn't reach the server.");
    expect(screen.getByRole("button", { name: "Confirm" })).toBeTruthy();
  });

  it("expires client-side when the countdown runs out", async () => {
    vi.useFakeTimers();
    render(<Harness initial={{ ...base, expiresAt: new Date(Date.now() + 2_000).toISOString() }} />);
    await act(async () => {
      vi.advanceTimersByTime(3_000);
    });
    expect(screen.getByText("Expired")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
  });

  it("does not render buttons for decided actions", () => {
    render(<Harness initial={{ ...base, state: "executed", stripeObjectId: "in_1" }} />);
    expect(screen.getByText("Done")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
