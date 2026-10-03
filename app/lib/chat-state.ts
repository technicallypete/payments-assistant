/**
 * Chat transcript state as a pure reducer, so streaming behaviour is unit-testable without a DOM.
 * One assistant message is "live" while a stream runs; agent events (spec §6.1) are folded into it.
 */
import type { AgentEvent } from "./events";
import type { Action, ApiMessage } from "./types";

export type ActionState =
  | "proposed"
  | "confirming"
  | "cancelling"
  | "executed"
  | "failed"
  | "expired"
  | "cancelled";

export interface ProposedAction {
  id: string;
  actionType: string;
  preview: string;
  expiresAt: string;
  state: ActionState;
  stripeObjectId?: string | null;
  error?: string | null;
}

export interface ToolChip {
  tool: string;
  label: string;
}

export type MessageStatus = "streaming" | "complete" | "error" | "interrupted";

export interface ChatMessage {
  key: string; // stable React key (local until the server id arrives)
  id: string | null; // server id
  role: "user" | "assistant";
  content: string;
  status: MessageStatus;
  tools: ToolChip[]; // currently running tools (transient chips)
  actions: ProposedAction[];
  error?: string;
}

export interface ChatState {
  messages: ChatMessage[];
  /** Pending proposals from earlier sessions (GET /actions), shown until decided. */
  pending: ProposedAction[];
  streaming: boolean;
}

export type ChatAction =
  | { type: "load"; messages: ApiMessage[]; pending: Action[] }
  | { type: "reset" } // empty transcript (new conversation); keeps the pending list
  | { type: "set_pending"; pending: Action[] }
  | { type: "send"; userKey: string; assistantKey: string; content: string }
  | { type: "event"; event: AgentEvent }
  | { type: "aborted" }
  | { type: "failed"; error: string }
  | { type: "action_update"; actionId: string; patch: Partial<ProposedAction> };

export const emptyChat: ChatState = { messages: [], pending: [], streaming: false };

export function fromApiAction(a: Action): ProposedAction {
  return {
    id: a.id,
    actionType: a.action_type,
    preview: a.preview,
    expiresAt: a.expires_at,
    state: a.status as ActionState,
    stripeObjectId: a.stripe_object_id,
    error: a.error,
  };
}

function updateLive(state: ChatState, fn: (m: ChatMessage) => ChatMessage): ChatState {
  const idx = state.messages.findLastIndex(
    (m) => m.role === "assistant" && m.status === "streaming",
  );
  if (idx === -1) return state;
  const messages = state.messages.slice();
  messages[idx] = fn(messages[idx]);
  return { ...state, messages };
}

function applyEvent(state: ChatState, e: AgentEvent): ChatState {
  switch (e.type) {
    case "message_start":
      return updateLive(state, (m) => ({ ...m, id: e.message_id ?? m.id }));
    case "token":
      return updateLive(state, (m) => ({ ...m, content: m.content + e.delta }));
    case "tool_start":
      return updateLive(state, (m) => ({ ...m, tools: [...m.tools, { tool: e.tool, label: e.label }] }));
    case "tool_end":
      return updateLive(state, (m) => {
        const i = m.tools.findIndex((t) => t.tool === e.tool);
        return i === -1 ? m : { ...m, tools: m.tools.filter((_, j) => j !== i) };
      });
    case "action_proposed":
      return updateLive(state, (m) => ({
        ...m,
        // One card per proposal id, even if the stream repeats it.
        actions: m.actions.some((a) => a.id === e.action_id)
          ? m.actions
          : [
          ...m.actions,
          {
            id: e.action_id,
            actionType: e.action_type,
            preview: e.preview,
            expiresAt: e.expires_at,
            state: "proposed",
          },
        ],
      }));
    case "error":
      return updateLive(state, (m) => ({ ...m, error: e.message }));
    case "message_end": {
      const next = updateLive(state, (m) => ({
        ...m,
        id: e.message_id ?? m.id,
        // The server's text is authoritative (it can include a note the stream didn't repeat).
        content: e.text || m.content,
        tools: [],
        status: m.error ? "error" : "complete",
      }));
      return { ...next, streaming: false };
    }
  }
}

function patchActions(list: ProposedAction[], id: string, patch: Partial<ProposedAction>) {
  return list.map((a) => (a.id === id ? { ...a, ...patch } : a));
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "load": {
      const messages: ChatMessage[] = action.messages
        .filter((m) => m.role === "user" || m.role === "assistant")
        .map((m) => ({
          key: m.id,
          id: m.id,
          role: m.role as "user" | "assistant",
          content: m.content,
          status: (m.role === "assistant" ? m.status : "complete") as MessageStatus,
          tools: [],
          // Cards from earlier turns come back with their current status: pending ones stay
          // actionable, finished/expired ones render as result stamps.
          actions: (m.actions ?? []).map(fromApiAction),
        }));
      return {
        messages,
        pending: action.pending.filter((a) => a.status === "proposed").map(fromApiAction),
        streaming: false,
      };
    }
    case "reset":
      return { ...state, messages: [], streaming: false };
    case "set_pending":
      return { ...state, pending: mergePending(state.pending, action.pending) };
    case "send":
      return {
        ...state,
        streaming: true,
        messages: [
          ...state.messages,
          {
            key: action.userKey,
            id: null,
            role: "user",
            content: action.content,
            status: "complete",
            tools: [],
            actions: [],
          },
          {
            key: action.assistantKey,
            id: null,
            role: "assistant",
            content: "",
            status: "streaming",
            tools: [],
            actions: [],
          },
        ],
      };
    case "event":
      return applyEvent(state, action.event);
    case "aborted":
      return {
        ...updateLive(state, (m) => ({ ...m, tools: [], status: "interrupted" })),
        streaming: false,
      };
    case "failed":
      return {
        ...updateLive(state, (m) => ({ ...m, tools: [], status: "error", error: action.error })),
        streaming: false,
      };
    case "action_update":
      return {
        ...state,
        pending: patchActions(state.pending, action.actionId, action.patch),
        messages: state.messages.map((m) =>
          m.actions.some((a) => a.id === action.actionId)
            ? { ...m, actions: patchActions(m.actions, action.actionId, action.patch) }
            : m,
        ),
      };
  }
}

/** Map a confirm/cancel API result onto the card state. */
export function actionResultPatch(a: Action): Partial<ProposedAction> {
  return { state: a.status as ActionState, stripeObjectId: a.stripe_object_id, error: a.error };
}

/** Server's proposed list, keeping local state (e.g. "confirming") for cards already shown. */
function mergePending(current: ProposedAction[], fresh: Action[]): ProposedAction[] {
  const local = new Map(current.map((a) => [a.id, a]));
  return fresh
    .filter((a) => a.status === "proposed")
    .map((a) => {
      const mine = local.get(a.id);
      return mine && mine.state !== "proposed" ? mine : fromApiAction(a);
    });
}

/** Pending proposals that aren't already rendered inline under a message (dedupe by id). */
export function visiblePending(state: ChatState): ProposedAction[] {
  const inline = new Set(state.messages.flatMap((m) => m.actions.map((a) => a.id)));
  return state.pending.filter((a) => !inline.has(a.id));
}
