"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { ApiError, login, setUnauthorizedHandler } from "@/lib/client";

export function LoginForm() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setUnauthorizedHandler(() => {}); // a wrong password is a 401 too: stay on this page
    try {
      await login(email, password);
      router.replace("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Couldn't reach the server.");
    } finally {
      setBusy(false);
      // Restore the app-wide behaviour: any later 401 sends the owner back here.
      setUnauthorizedHandler(() => router.replace("/login"));
    }
  }

  return (
    <form onSubmit={submit} className="sheet sheet-ruled mt-8 space-y-4 p-6 pl-10" noValidate>
      <div>
        <label htmlFor="email" className="block text-xs font-semibold uppercase tracking-[0.12em] text-ink-2">
          Email
        </label>
        <input
          id="email"
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="mt-1 w-full rounded-lg border border-rule-strong bg-paper px-3 py-2 text-base text-ink"
        />
      </div>
      <div>
        <label htmlFor="password" className="block text-xs font-semibold uppercase tracking-[0.12em] text-ink-2">
          Password
        </label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mt-1 w-full rounded-lg border border-rule-strong bg-paper px-3 py-2 text-base text-ink"
        />
      </div>
      {error && (
        <p role="alert" className="text-sm text-margin">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={busy || !email || !password}
        className="w-full rounded-full bg-accent py-2.5 font-semibold text-accent-ink disabled:opacity-60"
      >
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
