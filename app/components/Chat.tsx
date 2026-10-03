"use client";

import {
  useCallback,
  useEffect,
  useReducer,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { ActionCard } from "@/components/ActionCard";
import { Markdown } from "@/components/Markdown";
import {
  chatReducer,
  emptyChat,
  visiblePending,
  type ChatMessage,
  type ProposedAction,
} from "@/lib/chat-state";
import { api, ApiError, stream } from "@/lib/client";
import { greetingFor, relativeTime } from "@/lib/format";
import type { Action, Conversation, ConversationDetail } from "@/lib/types";

export const SUGGESTIONS = [
  "Summarize my day",
  "Refund Maya's last payment",
  "Create a $250 invoice for Acme Corp due next Friday",
  "How much did we take last week compared to the week before?",
];

let keySeq = 0;
const nextKey = () => `local-${++keySeq}`;

export function Chat({ onActivity }: { onActivity?: () => void }) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [state, dispatch] = useReducer(chatReducer, emptyChat);
  const [draft, setDraft] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  /**
   * Navigation generation. Every user navigation (open, new conversation, send) bumps it, and an
   * async load only applies its result if the generation is unchanged. Without this, the initial
   * load of the most recent conversation could land after the owner had already started a turn
   * (e.g. clicked a suggestion before the page finished loading): it replaced the transcript,
   * deleting the live reply, so that turn's events, including its action card, were dropped.
   */
  const navRef = useRef(0);

  const refreshList = useCallback(async () => {
    const list = await api<Conversation[]>("conversations");
    setConversations(list);
    return list;
  }, []);

  const refreshPending = useCallback(async () => {
    const pending = await api<Action[]>("actions");
    dispatch({ type: "set_pending", pending });
  }, []);

  const open = useCallback(async (id: string) => {
    abortRef.current?.abort();
    const gen = ++navRef.current;
    setLoadError(null);
    try {
      const [detail, pending] = await Promise.all([
        api<ConversationDetail>(`conversations/${id}`),
        api<Action[]>("actions"),
      ]);
      if (gen !== navRef.current) return; // the owner moved on meanwhile
      // Activate only once loaded, so the highlight always matches the transcript shown.
      setActiveId(id);
      dispatch({ type: "load", messages: detail.messages, pending });
    } catch (err) {
      if (gen !== navRef.current) return;
      setLoadError(err instanceof ApiError ? err.detail : "Couldn't load this conversation.");
    }
  }, []);

  useEffect(() => {
    let alive = true;
    const gen = ++navRef.current;
    api<Conversation[]>("conversations")
      .then(async (list) => {
        if (!alive) return;
        setConversations(list);
        const first = list[0];
        const [detail, pending] = await Promise.all([
          first ? api<ConversationDetail>(`conversations/${first.id}`) : Promise.resolve(null),
          api<Action[]>("actions"),
        ]);
        if (!alive) return;
        if (gen !== navRef.current) {
          dispatch({ type: "set_pending", pending }); // still useful; the transcript isn't
          return;
        }
        setActiveId(first?.id ?? null);
        dispatch({ type: "load", messages: detail?.messages ?? [], pending });
      })
      .catch(() => alive && setLoadError("Couldn't load conversations."));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end", behavior: "smooth" });
  }, [state.messages]);

  async function newConversation() {
    abortRef.current?.abort();
    ++navRef.current;
    const conv = await api<Conversation>("conversations", { method: "POST", body: "{}" });
    ++navRef.current; // anything that started loading while we created this is stale too
    setConversations((c) => [conv, ...c.filter((x) => x.id !== conv.id)]);
    setActiveId(conv.id);
    dispatch({ type: "reset" });
    return conv.id;
  }

  async function send(text: string) {
    const content = text.trim();
    if (!content || state.streaming) return;
    const convId = activeId ?? (await newConversation());
    ++navRef.current; // a turn supersedes any load still in flight
    setActiveId(convId);
    setDraft("");
    dispatch({ type: "send", userKey: nextKey(), assistantKey: nextKey(), content });
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let ended = false;
    try {
      await stream(
        `conversations/${convId}/messages`,
        { content },
        (event) => {
          if (event.type === "message_end") ended = true;
          dispatch({ type: "event", event });
        },
        ctrl.signal,
      );
      if (!ended) dispatch({ type: "failed", error: "The reply ended unexpectedly." });
    } catch (err) {
      if (ctrl.signal.aborted) dispatch({ type: "aborted" });
      else dispatch({ type: "failed", error: err instanceof ApiError ? err.detail : "Connection lost." });
    } finally {
      abortRef.current = null;
      void refreshList().catch(() => undefined);
      void refreshPending().catch(() => undefined);
      onActivity?.();
    }
  }

  function stop() {
    abortRef.current?.abort();
    dispatch({ type: "aborted" });
  }

  const updateAction = useCallback(
    (id: string) => (patch: Partial<ProposedAction>) => {
      dispatch({ type: "action_update", actionId: id, patch });
      if (patch.state === "executed") onActivity?.();
    },
    [onActivity],
  );

  const empty = state.messages.length === 0;
  const pending = visiblePending(state);

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-4 md:flex-row">
      <nav aria-label="Conversations" className="md:w-56 md:shrink-0">
        <button
          type="button"
          onClick={() => void newConversation()}
          className="w-full rounded-full border border-rule-strong bg-card px-4 py-2 text-sm font-semibold text-ink hover:border-accent"
        >
          + New conversation
        </button>
        <ul className="mt-3 flex gap-2 overflow-x-auto md:flex-col md:overflow-visible">
          {conversations.map((c) => (
            <li key={c.id} className="shrink-0">
              <button
                type="button"
                aria-current={c.id === activeId ? "true" : undefined}
                onClick={() => void open(c.id)}
                className={`w-full max-w-60 truncate rounded-lg px-3 py-2 text-left text-sm ${
                  c.id === activeId ? "bg-accent-soft text-ink" : "text-ink-2 hover:bg-paper-2"
                }`}
              >
                <span className="block truncate">{c.title ?? "New conversation"}</span>
                <span className="block text-xs text-muted">{relativeTime(c.last_message_at)}</span>
              </button>
            </li>
          ))}
        </ul>
      </nav>

      <section aria-label="Chat with Penny" className="sheet flex min-h-[28rem] min-w-0 flex-1 flex-col">
        <div className="flex-1 space-y-5 overflow-y-auto p-5" aria-live="polite" aria-busy={state.streaming}>
          {loadError && <p role="alert" className="text-sm text-margin">{loadError}</p>}

          {pending.length > 0 && (
            <div>
              <p className="text-xs uppercase tracking-[0.14em] text-muted">Still waiting on you</p>
              {pending.map((a) => (
                <ActionCard key={a.id} action={a} onChange={updateAction(a.id)} />
              ))}
            </div>
          )}

          {empty && (
            <div className="py-6">
              <p className="font-display text-3xl leading-tight text-ink">
                <Greeting /> What should we look at?
              </p>
              <p className="mt-2 text-sm text-muted">
                Ask about takings, chase an invoice, or tell me what to do. I&apos;ll ask before any
                money moves.
              </p>
              <div className="mt-5 flex flex-wrap gap-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => void send(s)}
                    className="rounded-full border border-rule-strong bg-paper px-3 py-1.5 text-sm text-ink-2 hover:border-accent hover:text-ink"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {state.messages.map((m) => (
            <MessageRow key={m.key} message={m} onAction={updateAction} />
          ))}
          <div ref={endRef} />
        </div>

        <form
          className="flex items-end gap-2 border-t border-rule p-3"
          onSubmit={(e) => {
            e.preventDefault();
            void send(draft);
          }}
        >
          <label htmlFor="composer" className="sr-only">
            Message Penny
          </label>
          <textarea
            id="composer"
            rows={1}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send(draft);
              }
            }}
            placeholder="Ask Penny…"
            className="max-h-40 min-h-11 flex-1 resize-none rounded-xl border border-rule bg-paper px-3 py-2.5 text-[15px] text-ink placeholder:text-muted"
          />
          {state.streaming ? (
            <button
              type="button"
              onClick={stop}
              className="h-11 rounded-full border border-margin px-4 text-sm font-semibold text-margin"
            >
              Stop
            </button>
          ) : (
            <button
              type="submit"
              disabled={!draft.trim()}
              className="h-11 rounded-full bg-accent px-5 text-sm font-semibold text-accent-ink disabled:opacity-50"
            >
              Send
            </button>
          )}
        </form>
      </section>
    </div>
  );
}

function MessageRow({
  message: m,
  onAction,
}: {
  message: ChatMessage;
  onAction: (id: string) => (patch: Partial<ProposedAction>) => void;
}) {
  if (m.role === "user") {
    return (
      <div className="flex justify-end">
        <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-ink px-4 py-2.5 text-[15px] text-paper">
          {m.content}
        </p>
      </div>
    );
  }
  return (
    <article className="max-w-[92%]" aria-label="Penny">
      <p className="mb-1 font-display text-sm italic text-accent">Penny</p>
      <div className={`text-[15px] leading-relaxed text-ink ${m.status === "streaming" && !m.tools.length ? "caret" : ""}`}>
        {m.content ? <Markdown text={m.content} /> : m.status === "streaming" && !m.tools.length ? (
          <span className="text-muted">Thinking</span>
        ) : null}
      </div>
      {m.tools.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {m.tools.map((t, i) => (
            <span key={`${t.tool}-${i}`} className="rounded-full bg-gold-soft px-3 py-1 text-xs text-gold">
              {t.label}…
            </span>
          ))}
        </div>
      )}
      {m.actions.map((a) => (
        <ActionCard key={a.id} action={a} onChange={onAction(a.id)} />
      ))}
      {m.status === "interrupted" && <p className="mt-1 text-xs text-muted">Stopped.</p>}
      {m.error && (
        <p role="alert" className="mt-1 text-sm text-margin">
          {m.error}
        </p>
      )}
    </article>
  );
}

/** Time-of-day greeting. The server renders a neutral word; the client reads its own clock, so
 * hydration doesn't mismatch (useSyncExternalStore uses the server snapshot during hydration). */
const noopSubscribe = () => () => {};
function Greeting() {
  const word = useSyncExternalStore(
    noopSubscribe,
    () => greetingFor(new Date().getHours()),
    () => "Hello.",
  );
  return <>{word}</>;
}
