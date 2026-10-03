// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Dashboard } from "./Dashboard";

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push: vi.fn() }) }));

const T = "2026-10-03T19:00:00Z";
const handoff = (id: string) => ({
  id,
  customer: "Jordan Lee",
  reason: "amount_over_threshold",
  amount: "$2,000.00",
  stripe_invoice_id: "in_1",
  summary: "Asked to pay a $2,000 invoice",
  status: "open",
  created_at: T,
  resolved_at: null,
});

function json(body: unknown) {
  return new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function renderDashboard() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url === "/api/auth/me") return json({ id: "o1", email: "owner@example.com" });
      if (url.startsWith("/api/handoffs")) return json([handoff("h1"), handoff("h2")]);
      if (url.startsWith("/api/summaries"))
        return new Response("", { headers: { "content-type": "text/event-stream" } });
      return json([]);
    }),
  );
  await act(async () => {
    render(<Dashboard />);
  });
  await screen.findByRole("tablist");
}

const panel = (id: string) => document.getElementById(`panel-${id}`) as HTMLElement;
const hiddenOnPhone = (id: string) => panel(id).className.includes("max-md:hidden");

describe("Dashboard phone tabs", () => {
  it("starts on Chat; every panel stays mounted but only the active one shows on phones", async () => {
    await renderDashboard();
    for (const id of ["chat", "today", "needs", "customers", "mcp"]) expect(panel(id)).toBeTruthy();
    expect(hiddenOnPhone("chat")).toBe(false);
    expect(["today", "needs", "customers", "mcp"].every(hiddenOnPhone)).toBe(true);
    expect(screen.getByRole("tab", { name: "Chat" }).getAttribute("aria-selected")).toBe("true");
  });

  it("switching tabs swaps the visible panel without unmounting the chat", async () => {
    await renderDashboard();
    fireEvent.click(screen.getByRole("tab", { name: /Customers/ }));
    expect(hiddenOnPhone("customers")).toBe(false);
    expect(hiddenOnPhone("chat")).toBe(true);
    expect(screen.getByRole("textbox", { name: /message/i })).toBeTruthy(); // composer still mounted
    expect(screen.getByRole("tab", { name: /Customers/ }).getAttribute("aria-selected")).toBe("true");
  });

  it("the Needs you tab shows the open-handoff count", async () => {
    await renderDashboard();
    expect((await screen.findByLabelText("2 open", { selector: "[role=tab] span" })).textContent).toBe("2");
  });
});
