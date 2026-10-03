"use client";

import { useRef, type KeyboardEvent } from "react";

export const TABS = [
  { id: "chat", label: "Chat" },
  { id: "today", label: "Today" },
  { id: "needs", label: "Needs you" },
  { id: "customers", label: "Customers" },
  { id: "mcp", label: "MCP" },
] as const;

export type TabId = (typeof TABS)[number]["id"];

export const tabId = (id: TabId) => `tab-${id}`;
export const panelId = (id: TabId) => `panel-${id}`;

/**
 * Phone-only bottom tab bar (hidden from md up, where every panel is visible at once).
 * WAI-ARIA tabs pattern: roving tabindex, arrow/Home/End keys move and select.
 */
export function MobileTabs({
  active,
  onSelect,
  needsCount = 0,
  chatDot = false,
}: {
  active: TabId;
  onSelect: (id: TabId) => void;
  needsCount?: number;
  chatDot?: boolean;
}) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const i = TABS.findIndex((t) => t.id === active);
    const next =
      e.key === "ArrowRight"
        ? (i + 1) % TABS.length
        : e.key === "ArrowLeft"
          ? (i - 1 + TABS.length) % TABS.length
          : e.key === "Home"
            ? 0
            : e.key === "End"
              ? TABS.length - 1
              : -1;
    if (next === -1) return;
    e.preventDefault();
    const id = TABS[next].id;
    onSelect(id);
    refs.current[id]?.focus();
  }

  return (
    <nav
      aria-label="Sections"
      className="-mx-3 mt-2 border-t border-rule-strong bg-card pb-[env(safe-area-inset-bottom)] md:hidden"
    >
      <div role="tablist" aria-label="Dashboard sections" onKeyDown={onKeyDown} className="relative flex">
        {/* One indicator that glides to the active tab (instant with reduced motion). */}
        <span
          aria-hidden
          data-testid="tab-indicator"
          className="pointer-events-none absolute top-0 left-0 h-0.5 bg-accent transition-transform duration-200 ease-out motion-reduce:transition-none"
          style={{
            width: `${100 / TABS.length}%`,
            transform: `translateX(${TABS.findIndex((t) => t.id === active) * 100}%)`,
          }}
        />
        {TABS.map((t) => {
          const selected = t.id === active;
          return (
            <button
              key={t.id}
              ref={(el) => {
                refs.current[t.id] = el;
              }}
              id={tabId(t.id)}
              type="button"
              role="tab"
              aria-selected={selected}
              aria-controls={panelId(t.id)}
              tabIndex={selected ? 0 : -1}
              onClick={() => onSelect(t.id)}
              className={`relative flex min-h-[52px] min-w-0 flex-1 flex-col items-center justify-center gap-0.5 px-0.5 text-[12px] font-semibold whitespace-nowrap transition-colors duration-200 motion-reduce:transition-none ${
                selected ? "text-accent" : "text-muted"
              }`}
            >
              <span className="flex items-center gap-1">
                {t.label}
                {t.id === "needs" && needsCount > 0 && (
                  <span
                    className="tabular min-w-[1.25rem] rounded-full bg-margin px-1.5 py-px text-[10px] text-paper"
                    aria-label={`${needsCount} open`}
                  >
                    {needsCount}
                  </span>
                )}
                {t.id === "chat" && chatDot && (
                  <span className="h-2 w-2 rounded-full bg-accent" aria-label="new reply" />
                )}
              </span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}
