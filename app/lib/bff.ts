/**
 * Backend-for-frontend: the only way the browser reaches the Python API.
 *
 * Plain functions (no Next imports) so they're unit-testable with an injected fetch.
 *   - session cookie → `Authorization: Bearer`, and a client-supplied Authorization is dropped
 *   - non-GET requests must come from our own origin (CSRF guard on top of SameSite=Lax)
 *   - only known API areas are reachable (webhooks, docs, etc. stay internal)
 *   - responses stream through unbuffered (SSE), and a browser abort cancels the upstream call
 */
import { readCookie, SESSION_COOKIE } from "./session";

export interface BffDeps {
  apiUrl: string; // e.g. http://api:8000
  appOrigin: string; // e.g. http://localhost:3010
  fetch: typeof fetch;
}

/** API areas the browser may reach through the catch-all proxy. */
export const ALLOWED_PREFIXES = [
  "auth/me",
  "conversations",
  "actions",
  "summaries",
  "handoffs",
  "customers",
  "api-keys",
] as const;

const FORWARD_REQUEST_HEADERS = ["content-type", "accept", "user-agent", "accept-language"];
const DROP_RESPONSE_HEADERS = new Set([
  "connection",
  "keep-alive",
  "transfer-encoding",
  "content-encoding", // body is re-streamed as-is; let Next handle encoding
  "content-length",
  "set-cookie", // the API never sets cookies; only our own auth routes do
]);
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export function jsonError(status: number, detail: string): Response {
  return Response.json({ detail }, { status });
}

/** CSRF guard: state-changing requests must carry our own Origin (or a same-origin Referer). */
export function originAllowed(req: Request, appOrigin: string): boolean {
  if (SAFE_METHODS.has(req.method.toUpperCase())) return true;
  const origin = req.headers.get("origin");
  if (origin) return origin === appOrigin;
  const referer = req.headers.get("referer");
  return !!referer && (referer === appOrigin || referer.startsWith(appOrigin + "/"));
}

export function isAllowedPath(segments: string[]): boolean {
  if (segments.some((s) => s === ".." || s === "." || s === "")) return false;
  const path = segments.join("/");
  return ALLOWED_PREFIXES.some((p) => path === p || path.startsWith(p + "/"));
}

export function upstreamHeaders(req: Request, token?: string): Headers {
  const out = new Headers();
  for (const name of FORWARD_REQUEST_HEADERS) {
    const v = req.headers.get(name);
    if (v) out.set(name, v);
  }
  if (token) out.set("authorization", `Bearer ${token}`);
  const fwd = req.headers.get("x-forwarded-for");
  if (fwd) out.set("x-forwarded-for", fwd);
  return out;
}

export function downstreamResponse(upstream: Response): Response {
  const headers = new Headers();
  upstream.headers.forEach((value, key) => {
    if (!DROP_RESPONSE_HEADERS.has(key.toLowerCase())) headers.set(key, value);
  });
  if ((upstream.headers.get("content-type") ?? "").startsWith("text/event-stream")) {
    headers.set("cache-control", "no-cache, no-transform");
    headers.set("x-accel-buffering", "no");
  }
  return new Response(upstream.body, { status: upstream.status, headers });
}

/** The catch-all proxy: /api/<segments...> → API_URL/<segments...> */
export async function proxy(req: Request, segments: string[], deps: BffDeps): Promise<Response> {
  if (!isAllowedPath(segments)) return jsonError(404, "Not found.");
  if (!originAllowed(req, deps.appOrigin)) return jsonError(403, "Cross-site request refused.");

  const token = readCookie(req.headers.get("cookie"), SESSION_COOKIE);
  if (!token) return jsonError(401, "Not signed in.");

  const search = new URL(req.url).search;
  const target = `${deps.apiUrl}/${segments.map(encodeURIComponent).join("/")}${search}`;
  const method = req.method.toUpperCase();
  const body = SAFE_METHODS.has(method) ? undefined : await req.arrayBuffer();

  let upstream: Response;
  try {
    upstream = await deps.fetch(target, {
      method,
      headers: upstreamHeaders(req, token),
      body: body && body.byteLength ? body : undefined,
      signal: req.signal, // browser abort → upstream abort → API persists the turn as interrupted
      redirect: "manual",
      cache: "no-store",
    });
  } catch {
    if (req.signal.aborted) return new Response(null, { status: 499 });
    return jsonError(502, "The assistant service is unavailable.");
  }
  return downstreamResponse(upstream);
}

export interface LoginResult {
  response: Response;
  token?: string;
  expiresAt?: Date;
}

/** POST /api/auth/login: exchange credentials for a session token (kept server-side). */
export async function login(req: Request, deps: BffDeps): Promise<LoginResult> {
  if (!originAllowed(req, deps.appOrigin)) {
    return { response: jsonError(403, "Cross-site request refused.") };
  }
  let creds: unknown;
  try {
    creds = await req.json();
  } catch {
    return { response: jsonError(400, "Expected JSON.") };
  }
  let upstream: Response;
  try {
    upstream = await deps.fetch(`${deps.apiUrl}/auth/login`, {
      method: "POST",
      headers: (() => {
        const h = upstreamHeaders(req);
        h.set("content-type", "application/json");
        return h;
      })(),
      body: JSON.stringify(creds),
      cache: "no-store",
    });
  } catch {
    return { response: jsonError(502, "The assistant service is unavailable.") };
  }
  const data = (await upstream.json().catch(() => ({}))) as {
    token?: string;
    expires_at?: string;
    owner?: { id: string; email: string };
    detail?: string;
  };
  if (!upstream.ok || !data.token) {
    return { response: jsonError(upstream.status || 502, data.detail ?? "Sign-in failed.") };
  }
  return {
    // The token is deliberately NOT in the body: only the httpOnly cookie carries it.
    response: Response.json({ owner: data.owner }, { status: 200 }),
    token: data.token,
    expiresAt: data.expires_at ? new Date(data.expires_at) : undefined,
  };
}

/** POST /api/auth/logout: revoke server-side, then the route clears the cookie. */
export async function logout(req: Request, deps: BffDeps): Promise<Response> {
  if (!originAllowed(req, deps.appOrigin)) return jsonError(403, "Cross-site request refused.");
  const token = readCookie(req.headers.get("cookie"), SESSION_COOKIE);
  if (token) {
    await deps
      .fetch(`${deps.apiUrl}/auth/logout`, {
        method: "POST",
        headers: { authorization: `Bearer ${token}` },
        cache: "no-store",
      })
      .catch(() => undefined); // clearing the cookie is what matters to the browser
  }
  return new Response(null, { status: 204 });
}
