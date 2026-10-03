"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Chat } from "@/components/Chat";
import { ConnectMcpPanel } from "@/components/ConnectMcpPanel";
import { CustomersPanel } from "@/components/CustomersPanel";
import { HandoffsPanel } from "@/components/HandoffsPanel";
import { MobileTabs, panelId, TABS, tabId, type TabId } from "@/components/MobileTabs";
import { TodayPanel } from "@/components/TodayPanel";
import { ToastProvider } from "@/components/Toast";
import { api, logout } from "@/lib/client";
import type { Owner } from "@/lib/types";

export function Dashboard() {
  const router = useRouter();
  const [owner, setOwner] = useState<Owner | null>(null);
  const [tick, setTick] = useState(0); // bump to refresh side panels after chat activity
  // Phones show one section at a time (bottom tabs); from md up everything is visible.
  const [tab, setTab] = useState<TabId>("chat");
  // Direction of the last switch, for the phone slide-in (data-dir on the incoming panel).
  const [dir, setDir] = useState<"forward" | "back" | null>(null);
  const [needsCount, setNeedsCount] = useState(0);
  const [chatDot, setChatDot] = useState(false);

  useEffect(() => {
    api<Owner>("auth/me").then(setOwner).catch(() => undefined); // 401 → client redirects
  }, []);

  const bump = useCallback(() => {
    setTick((t) => t + 1);
    setChatDot(true); // only visible while another tab is showing (see below)
  }, []);

  function selectTab(id: TabId) {
    setChatDot(false);
    if (id === tab) return;
    const order = TABS.map((t) => t.id as TabId);
    setDir(order.indexOf(id) > order.indexOf(tab) ? "forward" : "back");
    setTab(id);
  }

  /** Hidden on phones unless it's the active tab; always visible from md up. */
  const shown = (id: TabId) => (tab === id ? "" : "max-md:hidden");
  const panel = (id: TabId) => ({
    id: panelId(id),
    role: "tabpanel" as const,
    "aria-labelledby": tabId(id),
    // Panels stay mounted; a panel animates in (phones only, CSS) when it becomes visible.
    "data-dir": tab === id && dir ? dir : undefined,
  });

  async function signOut() {
    await logout().catch(() => undefined);
    router.replace("/login");
  }

  if (!owner) {
    return (
      <main className="flex flex-1 items-center justify-center">
        <p className="font-display text-xl italic text-muted">Opening the ledger…</p>
      </main>
    );
  }

  return (
    <ToastProvider>
      <div className="mx-auto flex w-full max-w-[1400px] flex-1 flex-col px-4 py-5 md:px-8 max-md:h-dvh max-md:flex-none max-md:overflow-hidden max-md:px-3 max-md:pb-0 max-md:pt-3">
        <header className="mb-5 flex items-center justify-between gap-3 border-b-2 border-double border-rule-strong pb-3 max-md:mb-3 max-md:pb-2">
          <div className="flex shrink-0 items-baseline gap-3">
            <h1 className="font-display text-3xl font-semibold tracking-tight text-ink max-md:text-2xl">Penny</h1>
            <p className="hidden text-sm italic text-muted sm:block">your payments, kept in good order</p>
          </div>
          <div className="flex min-w-0 items-center gap-4 text-sm max-md:gap-2">
            <span
              data-testid="owner-email"
              title={owner.email}
              className="min-w-0 truncate text-muted max-md:text-xs"
            >
              {owner.email}
            </span>
            <button
              type="button"
              onClick={() => void signOut()}
              className="shrink-0 text-ink-2 underline-offset-2 hover:underline max-md:min-h-11 max-md:px-1"
            >
              Sign out
            </button>
          </div>
        </header>

        <div className="grid flex-1 gap-5 lg:grid-cols-[minmax(0,1fr)_380px] max-md:flex max-md:min-h-0 max-md:flex-col">
          <div {...panel("chat")} className={`flex min-h-0 min-w-0 flex-col gap-5 max-md:flex-1 ${shown("chat")}`}>
            <Chat onActivity={bump} />
          </div>
          <aside
            className={`flex min-w-0 flex-col gap-5 max-md:min-h-0 max-md:flex-1 max-md:overflow-y-auto max-md:pb-3 ${
              tab === "chat" ? "max-md:hidden" : ""
            }`}
          >
            <div {...panel("today")} className={shown("today")}>
              <TodayPanel />
            </div>
            <div {...panel("needs")} className={shown("needs")}>
              <HandoffsPanel refreshKey={tick} onCount={setNeedsCount} />
            </div>
            <div {...panel("customers")} className={shown("customers")}>
              <CustomersPanel refreshKey={tick} />
            </div>
            <div {...panel("mcp")} className={shown("mcp")}>
              <ConnectMcpPanel />
            </div>
          </aside>
        </div>

        <MobileTabs
          active={tab}
          onSelect={selectTab}
          needsCount={needsCount}
          chatDot={chatDot && tab !== "chat"}
        />
      </div>
    </ToastProvider>
  );
}
