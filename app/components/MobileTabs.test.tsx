// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MobileTabs, TABS, type TabId } from "./MobileTabs";

afterEach(cleanup);

function setup(active: TabId = "chat", extra: { needsCount?: number; chatDot?: boolean } = {}) {
  const onSelect = vi.fn();
  render(<MobileTabs active={active} onSelect={onSelect} {...extra} />);
  return onSelect;
}

describe("MobileTabs", () => {
  it("renders an accessible tablist with one selected tab wired to its panel", () => {
    setup("today");
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(TABS.map((t) => t.label));
    const selected = tabs.filter((t) => t.getAttribute("aria-selected") === "true");
    expect(selected).toHaveLength(1);
    expect(selected[0].getAttribute("aria-controls")).toBe("panel-today");
    expect(selected[0].tabIndex).toBe(0);
    expect(tabs.filter((t) => t.tabIndex === -1)).toHaveLength(TABS.length - 1);
  });

  it("clicking a tab selects it", () => {
    const onSelect = setup();
    fireEvent.click(screen.getByRole("tab", { name: "Customers" }));
    expect(onSelect).toHaveBeenCalledWith("customers");
  });

  it("arrow keys wrap around, Home/End jump", () => {
    const onSelect = setup("chat");
    const list = screen.getByRole("tablist");
    fireEvent.keyDown(list, { key: "ArrowLeft" });
    expect(onSelect).toHaveBeenLastCalledWith("mcp");
    fireEvent.keyDown(list, { key: "ArrowRight" });
    expect(onSelect).toHaveBeenLastCalledWith("today");
    fireEvent.keyDown(list, { key: "End" });
    expect(onSelect).toHaveBeenLastCalledWith("mcp");
    fireEvent.keyDown(list, { key: "Home" });
    expect(onSelect).toHaveBeenLastCalledWith("chat");
  });

  it("shows the open-handoff badge and the chat dot only when set", () => {
    setup("today", { needsCount: 3, chatDot: true });
    expect(screen.getByLabelText("3 open").textContent).toBe("3");
    expect(screen.getByLabelText("new reply")).toBeTruthy();
    cleanup();
    setup("today", { needsCount: 0 });
    expect(screen.queryByLabelText(/open$/)).toBeNull();
    expect(screen.queryByLabelText("new reply")).toBeNull();
  });
});
