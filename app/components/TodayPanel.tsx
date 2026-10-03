"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Markdown } from "@/components/Markdown";
import { ApiError, stream } from "@/lib/client";

/** Streams the daily summary (cached per day server-side; Refresh regenerates). */
export function TodayPanel() {
  const [text, setText] = useState("");
  const [status, setStatus] = useState<"streaming" | "done" | "error">("streaming");
  const [error, setError] = useState<string | null>(null);
  const ctrlRef = useRef<AbortController | null>(null);

  /** Start the stream; state is only touched from the event callback. */
  const start = useCallback(
    (refresh: boolean, signal: AbortSignal) =>
      stream(
        `summaries/today${refresh ? "?refresh=true" : ""}`,
        undefined,
        (e) => {
          if (e.type === "token") setText((t) => t + e.delta);
          if (e.type === "error") setError(e.message);
          if (e.type === "message_end") setText((t) => e.text || t);
        },
        signal,
      ),
    [],
  );

  const settle = useCallback((p: Promise<void>, ctrl: AbortController) => {
    p.then(
      () => setStatus("done"),
      (err: unknown) => {
        if (ctrl.signal.aborted) return;
        setError(err instanceof ApiError ? err.detail : "Couldn't load today's summary.");
        setStatus("error");
      },
    );
  }, []);

  function refresh() {
    ctrlRef.current?.abort();
    const ctrl = new AbortController();
    ctrlRef.current = ctrl;
    setText("");
    setError(null);
    setStatus("streaming");
    settle(start(true, ctrl.signal), ctrl);
  }

  useEffect(() => {
    const ctrl = new AbortController();
    ctrlRef.current = ctrl;
    start(false, ctrl.signal).then(
      () => setStatus("done"),
      (err: unknown) => {
        if (ctrl.signal.aborted) return;
        setError(err instanceof ApiError ? err.detail : "Couldn't load today's summary.");
        setStatus("error");
      },
    );
    return () => ctrl.abort();
  }, [start]);

  const today = new Date().toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  });

  return (
    <section aria-labelledby="today-h" className="sheet sheet-ruled p-5 pl-10">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="today-h" className="font-display text-2xl text-ink">
          Today
        </h2>
        <button
          type="button"
          onClick={refresh}
          disabled={status === "streaming"}
          className="text-xs font-semibold uppercase tracking-[0.12em] text-accent disabled:opacity-50"
        >
          {status === "streaming" ? "Writing…" : "Refresh"}
        </button>
      </div>
      <p className="text-xs text-muted">{today}</p>
      <div
        aria-live="polite"
        className={`mt-3 text-[15px] leading-7 text-ink ${status === "streaming" ? "caret" : ""}`}
      >
        {text ? <Markdown text={text} /> : status === "streaming" ? <span className="text-muted">Reading the ledger</span> : null}
      </div>
      {error && (
        <p role="alert" className="mt-2 text-sm text-margin">
          {error}
        </p>
      )}
    </section>
  );
}
