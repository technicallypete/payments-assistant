"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";

import { relativeTime } from "@/lib/format";
import type { Conversation } from "@/lib/types";

/**
 * Phone-only conversation switcher: a compact bar (current title ▾ + "New") that opens a bottom
 * sheet. The md+ sidebar list in Chat is unchanged. The sheet is portalled to <body> so a
 * transformed ancestor (tab transitions) can't offset its fixed positioning.
 */
export function ConversationPicker({
  conversations,
  activeId,
  onOpen,
  onNew,
}: {
  conversations: Conversation[];
  activeId: string | null;
  onOpen: (id: string) => void;
  onNew: () => void;
}) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const active = conversations.find((c) => c.id === activeId);
  const title = active ? (active.title ?? "New conversation") : "New conversation";

  function close() {
    setOpen(false);
    // Restore focus to what opened the sheet (after the sheet unmounts).
    requestAnimationFrame(() => triggerRef.current?.focus());
  }

  return (
    <div className="flex min-w-0 items-center gap-2 md:hidden">
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
        className="flex min-h-11 min-w-0 flex-1 items-center gap-2 rounded-xl border border-rule-strong bg-card px-3 text-left"
      >
        <span className="min-w-0 flex-1">
          <span className="block text-[11px] uppercase tracking-[0.12em] text-muted">Conversation</span>
          <span className="block truncate text-sm text-ink">{title}</span>
        </span>
        <span aria-hidden className="text-muted">
          ▾
        </span>
      </button>
      <button
        type="button"
        onClick={onNew}
        aria-label="New conversation"
        className="flex min-h-11 shrink-0 items-center rounded-xl border border-rule-strong bg-card px-3 text-sm font-semibold text-ink"
      >
        + New
      </button>
      {open && (
        <Sheet
          conversations={conversations}
          activeId={activeId}
          onClose={close}
          onPick={(id) => {
            close();
            if (id !== activeId) onOpen(id);
          }}
          onNew={() => {
            close();
            onNew();
          }}
        />
      )}
    </div>
  );
}

function Sheet({
  conversations,
  activeId,
  onClose,
  onPick,
  onNew,
}: {
  conversations: Conversation[];
  activeId: string | null;
  onClose: () => void;
  onPick: (id: string) => void;
  onNew: () => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden"; // lock background scroll while open
    dialogRef.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
      return;
    }
    if (e.key !== "Tab") return;
    // Focus trap: cycle within the sheet.
    const items = Array.from(
      dialogRef.current?.querySelectorAll<HTMLElement>("button:not([disabled])") ?? [],
    );
    if (items.length === 0) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-50 md:hidden" onKeyDown={onKeyDown}>
      <div
        data-testid="sheet-backdrop"
        aria-hidden
        onClick={onClose}
        className="sheet-backdrop absolute inset-0 bg-black/50"
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="conversations-sheet-title"
        className="sheet-panel absolute inset-x-0 bottom-0 flex max-h-[70dvh] flex-col rounded-t-2xl border-t border-rule-strong bg-card pb-[env(safe-area-inset-bottom)] shadow-2xl"
      >
        <div className="flex items-center justify-between px-4 pt-3">
          <h2 id="conversations-sheet-title" className="font-display text-lg text-ink">
            Conversations
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex min-h-11 min-w-11 items-center justify-center text-xl text-muted"
          >
            ×
          </button>
        </div>
        <div className="px-4 pb-2">
          <button
            type="button"
            data-autofocus
            onClick={onNew}
            className="min-h-11 w-full rounded-full border border-rule-strong px-4 text-sm font-semibold text-ink"
          >
            + New conversation
          </button>
        </div>
        <ul className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-2 pb-3">
          {conversations.map((c) => {
            const isActive = c.id === activeId;
            return (
              <li key={c.id}>
                <button
                  type="button"
                  aria-current={isActive ? "true" : undefined}
                  onClick={() => onPick(c.id)}
                  className={`flex min-h-12 w-full items-center gap-3 rounded-lg px-3 py-2 text-left ${
                    isActive ? "bg-accent-soft" : ""
                  }`}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-ink">
                      {c.title ?? "New conversation"}
                    </span>
                    <span className="block text-xs text-muted">{relativeTime(c.last_message_at)}</span>
                  </span>
                  {isActive && (
                    <span aria-hidden className="text-accent">
                      ✓
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </div>,
    document.body,
  );
}
