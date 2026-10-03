/** Agent stream events (mirror of api/src/payments_assistant/core/agents/events.py). */
export interface MessageStart {
  type: "message_start";
  model: string;
  message_id: string | null;
}
export interface TokenEvent {
  type: "token";
  delta: string;
}
export interface ToolStart {
  type: "tool_start";
  tool: string;
  label: string;
}
export interface ToolEnd {
  type: "tool_end";
  tool: string;
  ok: boolean;
}
export interface ActionProposed {
  type: "action_proposed";
  action_id: string;
  action_type: string;
  preview: string;
  expires_at: string;
}
export interface MessageEnd {
  type: "message_end";
  message_id: string | null;
  text: string;
  input_tokens: number;
  output_tokens: number;
  truncated: boolean;
}
export interface ErrorEvent {
  type: "error";
  code: string;
  message: string;
}
export type AgentEvent =
  | MessageStart
  | TokenEvent
  | ToolStart
  | ToolEnd
  | ActionProposed
  | MessageEnd
  | ErrorEvent;
