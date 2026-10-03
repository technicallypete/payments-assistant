import type { Metadata } from "next";

import { LoginForm } from "@/components/LoginForm";

export const metadata: Metadata = { title: "Sign in — Penny" };

export default function LoginPage() {
  return (
    <main className="flex flex-1 items-center justify-center px-4 py-16">
      <div className="w-full max-w-sm">
        <h1 className="font-display text-5xl font-semibold tracking-tight text-ink">Penny</h1>
        <p className="mt-2 text-sm italic text-muted">your payments, kept in good order</p>
        <LoginForm />
      </div>
    </main>
  );
}
