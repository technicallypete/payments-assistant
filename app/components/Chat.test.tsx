// @vitest-environment happy-dom
/**
 * Repro of the live bug: the owner clicks a suggestion before the initial load of their most
 * recent conversation (A) finishes. The turn goes to a new conversation (B); A's late-arriving
 * load must not replace B's live transcript, B's proposal must render inline, and the sidebar must
 * highlight B.
 */
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Chat } from "./Chat";

const T = "2026-10-03T19:00:00Z";
const FUTURE = new Date(Date.now() + 10 * 60_000).toISOString();

const convA = { id: "conv-A", title: "Refund Maya's last payment", created_at: T, last_message_at: T };
const convB = { id: "conv-B", title: null, created_at: T, last_message_at: T };
const actionA = {
  id: "act-A",
  action_type: "refund",
  preview: "Refund $415.00 to Maya Chen (old)",
  status: "proposed",
  stripe_object_id: null,
  error: null,
  expires_at: FUTURE,
  created_at: T,
};
const actionB = { ...actionA, id: "act-B", preview: "Refund $82.00 to Maya Chen (new)" };

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function sse(events: object[]) {
  const text = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
  return new Response(text, { headers: { "content-type": "text/event-stream" } });
}

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Chat", () => {
  it("a turn started before the initial load finishes is not wiped by that load", async () => {
    const detailA = deferred<Response>();
    let turnDone = false;
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url === "/api/conversations" && method === "GET")
        return json(turnDone ? [convB, convA] : [convA]);
      if (url === "/api/conversations/conv-A") return detailA.promise; // slow initial load
      if (url === "/api/actions") return json(turnDone ? [actionA, actionB] : [actionA]);
      if (url === "/api/conversations" && method === "POST") return json(convB, 201);
      if (url === "/api/conversations/conv-B/messages") {
        turnDone = true;
        return sse([
          { type: "message_start", model: "m", message_id: "msg-B" },
          { type: "tool_start", tool: "find_payments", label: "Looking up payments" },
          { type: "tool_end", tool: "find_payments", ok: true },
          { type: "action_proposed", action_id: "act-B", action_type: "refund", preview: actionB.preview, expires_at: FUTURE },
          { type: "token", delta: "Ready to refund $82.00 to Maya. Confirm below." },
          { type: "message_end", message_id: "msg-B", text: "Ready to refund $82.00 to Maya. Confirm below.", input_tokens: 1, output_tokens: 1, truncated: false },
        ]);
      }
      throw new Error(`unexpected ${method} ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<Chat />);
    // Sidebar shows A, but its transcript is still loading: the suggestions are visible.
    const chip = await screen.findByRole("button", { name: "Refund Maya's last payment" });

    await act(async () => {
      fireEvent.click(chip);
    });
    await screen.findByText("Ready to refund $82.00 to Maya. Confirm below.");

    // Now A's slow load lands. It must not replace B's transcript.
    await act(async () => {
      detailA.resolve(
        json({
          ...convA,
          messages: [
            { id: "a1", role: "user", content: "Refund Maya's last payment", status: "complete", tool_name: null, created_at: T },
            { id: "a2", role: "assistant", content: "I've proposed a $415.00 refund (old turn).", status: "complete", tool_name: null, created_at: T },
          ],
        }),
      );
    });

    expect(screen.getByText("Ready to refund $82.00 to Maya. Confirm below.")).toBeTruthy();
    expect(screen.queryByText(/\$415\.00 refund \(old turn\)/)).toBeNull();

    // B's proposal renders inline under Penny's reply...
    const reply = screen.getAllByRole("article", { name: "Penny" }).at(-1)!;
    expect(within(reply).getByText(actionB.preview)).toBeTruthy();
    expect(within(reply).getByRole("button", { name: "Confirm" })).toBeTruthy();

    // ...and is not duplicated in "Still waiting on you", which lists only A's older proposal.
    const confirmButtons = screen.getAllByRole("button", { name: "Confirm" });
    expect(confirmButtons).toHaveLength(2);
    expect(screen.getAllByText(actionB.preview)).toHaveLength(1);
    expect(screen.getByText(actionA.preview)).toBeTruthy();

    // The highlight follows the conversation the turn went to.
    const current = screen.getByRole("navigation", { name: "Conversations" }).querySelector("[aria-current=true]");
    expect(current?.textContent).toContain("New conversation");
    expect(current?.textContent).not.toContain("Refund Maya");

    // Suggestions are gone once there is a transcript.
    expect(screen.queryByRole("button", { name: "Summarize my day" })).toBeNull();
    expect(fetchMock).toHaveBeenCalledWith("/api/conversations/conv-B/messages", expect.anything());
  });

  it("opening the latest conversation on load shows its transcript and highlights it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url === "/api/conversations") return json([convA]);
        if (url === "/api/conversations/conv-A")
          return json({
            ...convA,
            messages: [{ id: "a2", role: "assistant", content: "Earlier answer", status: "complete", tool_name: null, created_at: T }],
          });
        if (url === "/api/actions") return json([]);
        throw new Error(url);
      }),
    );
    render(<Chat />);
    await screen.findByText("Earlier answer");
    const current = screen.getByRole("navigation", { name: "Conversations" }).querySelector("[aria-current=true]");
    expect(current?.textContent).toContain("Refund Maya's last payment");
    expect(screen.queryByRole("button", { name: "Summarize my day" })).toBeNull();
  });
});
