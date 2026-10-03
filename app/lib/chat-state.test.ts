import { describe, expect, it } from "vitest";

import {
  actionResultPatch,
  chatReducer,
  emptyChat,
  visiblePending,
  type ChatState,
} from "./chat-state";
import type { AgentEvent } from "./events";
import type { Action, ApiMessage } from "./types";

const send = (s: ChatState, content = "hi") =>
  chatReducer(s, { type: "send", userKey: "u1", assistantKey: "a1", content });
const ev = (s: ChatState, event: AgentEvent) => chatReducer(s, { type: "event", event });
const live = (s: ChatState) => s.messages[s.messages.length - 1];

const action = (over: Partial<Action> = {}): Action => ({
  id: "act-1",
  action_type: "refund",
  preview: "Refund $82.00 to Maya Chen",
  status: "proposed",
  stripe_object_id: null,
  error: null,
  expires_at: "2026-10-03T20:00:00Z",
  created_at: "2026-10-03T19:50:00Z",
  ...over,
});

describe("chatReducer", () => {
  it("send appends the user message and a streaming assistant placeholder", () => {
    const s = send(emptyChat, "Summarize my day");
    expect(s.streaming).toBe(true);
    expect(s.messages.map((m) => [m.role, m.status, m.content])).toEqual([
      ["user", "complete", "Summarize my day"],
      ["assistant", "streaming", ""],
    ]);
  });

  it("accumulates tokens and adopts the server message id", () => {
    let s = send(emptyChat);
    s = ev(s, { type: "message_start", model: "m", message_id: "srv-1" });
    s = ev(s, { type: "token", delta: "You took " });
    s = ev(s, { type: "token", delta: "$4,280" });
    expect(live(s).id).toBe("srv-1");
    expect(live(s).content).toBe("You took $4,280");
    expect(live(s).key).toBe("a1"); // React key stays stable
  });

  it("shows tool chips while tools run and clears them on tool_end", () => {
    let s = send(emptyChat);
    s = ev(s, { type: "tool_start", tool: "find_payments", label: "Looking up payments" });
    s = ev(s, { type: "tool_start", tool: "get_activity", label: "Pulling payment activity" });
    expect(live(s).tools.map((t) => t.label)).toEqual([
      "Looking up payments",
      "Pulling payment activity",
    ]);
    s = ev(s, { type: "tool_end", tool: "find_payments", ok: true });
    expect(live(s).tools.map((t) => t.tool)).toEqual(["get_activity"]);
    s = ev(s, { type: "tool_end", tool: "unknown_tool", ok: true });
    expect(live(s).tools).toHaveLength(1);
  });

  it("attaches proposals to the live message as proposed cards", () => {
    let s = send(emptyChat);
    s = ev(s, {
      type: "action_proposed",
      action_id: "act-9",
      action_type: "create_invoice",
      preview: "Invoice Acme Corp $250.00",
      expires_at: "2026-10-03T20:00:00Z",
    });
    expect(live(s).actions).toEqual([
      {
        id: "act-9",
        actionType: "create_invoice",
        preview: "Invoice Acme Corp $250.00",
        expiresAt: "2026-10-03T20:00:00Z",
        state: "proposed",
      },
    ]);
  });

  it("message_end completes the message with the server's text and stops streaming", () => {
    let s = send(emptyChat);
    s = ev(s, { type: "token", delta: "Partial" });
    s = ev(s, { type: "tool_start", tool: "x", label: "X" });
    s = ev(s, {
      type: "message_end",
      message_id: "srv-2",
      text: "Partial and complete.",
      input_tokens: 1,
      output_tokens: 2,
      truncated: false,
    });
    expect(s.streaming).toBe(false);
    expect(live(s)).toMatchObject({ status: "complete", content: "Partial and complete.", id: "srv-2", tools: [] });
  });

  it("an error event marks the message as error but keeps partial text", () => {
    let s = send(emptyChat);
    s = ev(s, { type: "token", delta: "Half an answ" });
    s = ev(s, { type: "error", code: "llm_error", message: "The assistant hit an error" });
    s = ev(s, { type: "message_end", message_id: null, text: "", input_tokens: 0, output_tokens: 0, truncated: false });
    expect(live(s)).toMatchObject({ status: "error", content: "Half an answ", error: "The assistant hit an error" });
    expect(s.streaming).toBe(false);
  });

  it("aborted and failed end the stream", () => {
    let s = ev(send(emptyChat), { type: "tool_start", tool: "x", label: "X" });
    const aborted = chatReducer(s, { type: "aborted" });
    expect(aborted.streaming).toBe(false);
    expect(live(aborted)).toMatchObject({ status: "interrupted", tools: [] });

    s = send(emptyChat);
    const failed = chatReducer(s, { type: "failed", error: "Connection lost." });
    expect(live(failed)).toMatchObject({ status: "error", error: "Connection lost." });
  });

  it("events with no live message are ignored", () => {
    const s = ev(emptyChat, { type: "token", delta: "stray" });
    expect(s).toBe(emptyChat);
  });

  it("load keeps user/assistant messages, drops tool rows, and keeps only proposed pending actions", () => {
    const messages: ApiMessage[] = [
      { id: "1", role: "user", content: "hi", status: "complete", tool_name: null, created_at: "t" },
      { id: "2", role: "tool", content: "", status: "complete", tool_name: "get_activity", created_at: "t" },
      { id: "3", role: "assistant", content: "Hello", status: "interrupted", tool_name: null, created_at: "t" },
    ];
    const s = chatReducer(emptyChat, {
      type: "load",
      messages,
      pending: [action(), action({ id: "act-2", status: "executed" })],
    });
    expect(s.messages.map((m) => [m.role, m.status])).toEqual([
      ["user", "complete"],
      ["assistant", "interrupted"],
    ]);
    expect(s.pending.map((a) => a.id)).toEqual(["act-1"]);
  });

  it("action_update patches cards in messages and in the pending list", () => {
    let s = chatReducer(emptyChat, { type: "load", messages: [], pending: [action()] });
    s = send(s);
    s = ev(s, { type: "action_proposed", action_id: "act-5", action_type: "refund", preview: "p", expires_at: "x" });
    s = chatReducer(s, { type: "action_update", actionId: "act-5", patch: { state: "confirming" } });
    s = chatReducer(s, { type: "action_update", actionId: "act-1", patch: { state: "cancelled" } });
    expect(live(s).actions[0].state).toBe("confirming");
    expect(s.pending[0].state).toBe("cancelled");
  });

  it("maps confirm results onto card state", () => {
    expect(actionResultPatch(action({ status: "executed", stripe_object_id: "re_123" }))).toEqual({
      state: "executed",
      stripeObjectId: "re_123",
      error: null,
    });
    expect(actionResultPatch(action({ status: "failed", error: "Card declined" })).error).toBe("Card declined");
  });
});

describe("pending proposals", () => {
  it("reset clears the transcript but keeps pending proposals", () => {
    let s = chatReducer(emptyChat, { type: "load", messages: [], pending: [action()] });
    s = send(s);
    s = chatReducer(s, { type: "reset" });
    expect(s.messages).toEqual([]);
    expect(s.streaming).toBe(false);
    expect(s.pending.map((a) => a.id)).toEqual(["act-1"]);
  });

  it("set_pending keeps only proposed actions and preserves a card being decided", () => {
    let s = chatReducer(emptyChat, { type: "load", messages: [], pending: [action()] });
    s = chatReducer(s, { type: "action_update", actionId: "act-1", patch: { state: "confirming" } });
    s = chatReducer(s, {
      type: "set_pending",
      pending: [action(), action({ id: "act-2" }), action({ id: "act-3", status: "executed" })],
    });
    expect(s.pending.map((a) => [a.id, a.state])).toEqual([
      ["act-1", "confirming"],
      ["act-2", "proposed"],
    ]);
  });

  it("visiblePending hides proposals already rendered inline under a message", () => {
    // Repro of the live bug: this turn's proposal (act-B) is inline; the refreshed pending list
    // contains both it and an older conversation's proposal (act-A).
    let s = send(emptyChat, "Refund Maya's last payment");
    s = ev(s, { type: "action_proposed", action_id: "act-B", action_type: "refund", preview: "Refund $82", expires_at: "x" });
    s = ev(s, { type: "message_end", message_id: "m", text: "Confirm?", input_tokens: 0, output_tokens: 0, truncated: false });
    s = chatReducer(s, { type: "set_pending", pending: [action({ id: "act-A" }), action({ id: "act-B" })] });
    expect(visiblePending(s).map((a) => a.id)).toEqual(["act-A"]);
    expect(live(s).actions.map((a) => a.id)).toEqual(["act-B"]);
  });

  it("a stale load after send would wipe the live reply (why Chat guards loads by generation)", () => {
    let s = send(emptyChat, "Refund Maya's last payment");
    s = chatReducer(s, { type: "load", messages: [], pending: [] });
    const after = ev(s, { type: "action_proposed", action_id: "act-B", action_type: "refund", preview: "p", expires_at: "x" });
    expect(after.messages.flatMap((m) => m.actions)).toEqual([]); // event dropped: nothing live
  });
});
