// @vitest-environment happy-dom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "@/components/Toast";
import { MONEY_MOVED_EVENT } from "@/lib/format";

import { CustomersPanel } from "./CustomersPanel";

function customer(owed: number) {
  return {
    id: "00000000-0000-0000-0000-000000000001",
    display_name: "Acme Corp",
    email: null,
    amount_owed: `$${(owed / 100).toFixed(2)}`,
    amount_owed_cents: owed,
    telegram_linked: false,
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("CustomersPanel", () => {
  it("reloads balances when money moves (e.g. a confirmed invoice)", async () => {
    let owed = 470_000;
    const fetch = vi.fn(async () =>
      new Response(JSON.stringify([customer(owed)]), {
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetch);

    render(
      <ToastProvider>
        <CustomersPanel />
      </ToastProvider>,
    );
    expect(await screen.findByText("$4700.00")).toBeTruthy();

    owed = 520_000;
    await act(async () => {
      window.dispatchEvent(new Event(MONEY_MOVED_EVENT));
    });
    expect(await screen.findByText("$5200.00")).toBeTruthy();
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});
