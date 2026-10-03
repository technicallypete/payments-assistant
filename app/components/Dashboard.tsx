"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Chat } from "@/components/Chat";
import { ConnectMcpPanel } from "@/components/ConnectMcpPanel";
import { CustomersPanel } from "@/components/CustomersPanel";
import { HandoffsPanel } from "@/components/HandoffsPanel";
import { TodayPanel } from "@/components/TodayPanel";
import { ToastProvider } from "@/components/Toast";
import { api, logout } from "@/lib/client";
import type { Owner } from "@/lib/types";

export function Dashboard() {
  const router = useRouter();
  const [owner, setOwner] = useState<Owner | null>(null);
  const [tick, setTick] = useState(0); // bump to refresh side panels after chat activity

  useEffect(() => {
    api<Owner>("auth/me").then(setOwner).catch(() => undefined); // 401 → client redirects
  }, []);

  const bump = useCallback(() => setTick((t) => t + 1), []);

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
      <div className="mx-auto flex w-full max-w-[1400px] flex-1 flex-col px-4 py-5 md:px-8">
        <header className="mb-5 flex items-center justify-between border-b-2 border-double border-rule-strong pb-3">
          <div className="flex items-baseline gap-3">
            <h1 className="font-display text-3xl font-semibold tracking-tight text-ink">Penny</h1>
            <p className="hidden text-sm italic text-muted sm:block">your payments, kept in good order</p>
          </div>
          <div className="flex items-center gap-4 text-sm">
            <span className="hidden text-muted md:inline">{owner.email}</span>
            <button type="button" onClick={() => void signOut()} className="text-ink-2 underline-offset-2 hover:underline">
              Sign out
            </button>
          </div>
        </header>

        <div className="grid flex-1 gap-5 lg:grid-cols-[minmax(0,1fr)_380px]">
          <div className="flex min-h-0 min-w-0 flex-col gap-5">
            <Chat onActivity={bump} />
          </div>
          <aside className="flex min-w-0 flex-col gap-5">
            <TodayPanel />
            <HandoffsPanel refreshKey={tick} />
            <CustomersPanel refreshKey={tick} />
            <ConnectMcpPanel />
          </aside>
        </div>
      </div>
    </ToastProvider>
  );
}
