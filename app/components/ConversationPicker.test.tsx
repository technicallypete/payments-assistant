// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConversationPicker } from "./ConversationPicker";

const T = "2026-10-03T19:00:00Z";
const convs = [
  { id: "c1", title: "refund acme corp $120", created_at: T, last_message_at: T },
  { id: "c2", title: null, created_at: T, last_message_at: T },
];

afterEach(cleanup);

function setup(activeId: string | null = "c1") {
  const onOpen = vi.fn();
  const onNew = vi.fn();
  render(<ConversationPicker conversations={convs} activeId={activeId} onOpen={onOpen} onNew={onNew} />);
  const trigger = screen.getByRole("button", { expanded: false });
  return { onOpen, onNew, trigger };
}

const flushFrame = () => act(() => new Promise((r) => requestAnimationFrame(() => r(null))));

describe("ConversationPicker", () => {
  it("shows the active conversation title on the trigger", () => {
    const { trigger } = setup();
    expect(trigger.textContent).toContain("refund acme corp $120");
  });

  it("opens a modal sheet listing conversations with the active one marked", () => {
    const { trigger } = setup();
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Conversations" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    const sheet = within(dialog);
    expect(sheet.getByRole("button", { name: "+ New conversation" })).toBeTruthy();
    const active = sheet.getByRole("button", { name: /refund acme corp/ });
    expect(active.getAttribute("aria-current")).toBe("true");
    expect(active.textContent).toContain("✓");
    expect(sheet.getByRole("button", { name: /^New conversation/ }).getAttribute("aria-current")).toBeNull();
    expect(document.body.style.overflow).toBe("hidden");
  });

  it("closes on Esc, backdrop and close button, unlocking scroll and returning focus", async () => {
    const { trigger } = setup();
    for (const close of [
      () => fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" }),
      () => fireEvent.click(screen.getByTestId("sheet-backdrop")),
      () => fireEvent.click(screen.getByRole("button", { name: "Close" })),
    ]) {
      fireEvent.click(trigger);
      expect(screen.getByRole("dialog")).toBeTruthy();
      close();
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(document.body.style.overflow).toBe("");
      await flushFrame();
      expect(document.activeElement).toBe(trigger);
    }
  });

  it("selecting another conversation opens it and closes the sheet", () => {
    const { trigger, onOpen } = setup();
    fireEvent.click(trigger);
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: /^New conversation/ }));
    expect(onOpen).toHaveBeenCalledWith("c2");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("selecting the active one just closes", () => {
    const { trigger, onOpen } = setup();
    fireEvent.click(trigger);
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: /refund acme corp/ }));
    expect(onOpen).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("+ New works from the bar and from the sheet", () => {
    const { trigger, onNew } = setup();
    fireEvent.click(screen.getByRole("button", { name: "New conversation" }));
    expect(onNew).toHaveBeenCalledTimes(1);
    fireEvent.click(trigger);
    fireEvent.click(screen.getByRole("button", { name: "+ New conversation" }));
    expect(onNew).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("traps Tab focus inside the sheet", () => {
    const { trigger } = setup();
    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog");
    const buttons = Array.from(dialog.querySelectorAll("button"));
    buttons[buttons.length - 1].focus();
    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(document.activeElement).toBe(buttons[0]);
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(buttons[buttons.length - 1]);
  });
});
