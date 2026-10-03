/**
 * Browser-side API client. Everything goes to this app's own /api routes (same origin, cookie
 * auth); nothing here knows the Python API's address or sees the session token.
 */
import type { AgentEvent } from "./events";
import { readSse } from "./sse";

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
  }
}

/** Called on any 401 so the UI can send the owner back to /login. */
let onUnauthorized: () => void = () => {
  // Deliberately a full page load, not router.push: an expired session should drop all
  // in-memory client state (open chats, pending cards) rather than carry it to the login page.
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  if (typeof window !== "undefined") window.location.assign("/login");
};
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

async function failure(res: Response): Promise<ApiError> {
  const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
  const detail =
    typeof body.detail === "string" ? body.detail : `Request failed (${res.status})`;
  if (res.status === 401) onUnauthorized();
  return new ApiError(res.status, detail);
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api/${path.replace(/^\/+/, "")}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init.headers ?? {}) },
    credentials: "same-origin",
  });
  if (!res.ok) throw await failure(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** POST a JSON body to an SSE endpoint and deliver each event. Resolves when the stream ends. */
export async function stream(
  path: string,
  body: unknown,
  onEvent: (e: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`/api/${path.replace(/^\/+/, "")}`, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "text/event-stream" },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
    signal,
  });
  if (!res.ok || !res.body) throw await failure(res);
  for await (const msg of readSse(res.body)) {
    onEvent(JSON.parse(msg.data) as AgentEvent);
  }
}

export const login = (email: string, password: string) =>
  api<{ owner: { id: string; email: string } }>("auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });

export const logout = () => api<void>("auth/logout", { method: "POST" });
