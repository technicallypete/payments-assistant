/** Friendly aliases for the generated API schema types (lib/api-types.ts). */
import type { components } from "./api-types";

type S = components["schemas"];
export type Conversation = S["ConversationOut"];
export type ConversationDetail = S["ConversationDetail"];
export type ApiMessage = S["MessageOut"];
export type Action = S["ActionOut"];
export type Handoff = S["HandoffOut"];
export type Customer = S["CustomerOut"];
export type Invite = S["InviteOut"];
export type Owner = S["OwnerOut"];
