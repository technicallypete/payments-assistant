import { describe, expect, it, vi } from "vitest";

import { ALLOWED_PREFIXES, isAllowedPath, login, logout, originAllowed, proxy } from "./bff";

const APP = "http://localhost:3010";
const API = "http://api:8000";

function deps(fetchImpl: typeof fetch) {
  return { apiUrl: API, appOrigin: APP, fetch: fetchImpl };
}

function req(
  path: string,
  init: RequestInit & { cookie?: string; origin?: string | null } = {},
): Request {
  const headers = new Headers(init.headers);
  if (init.cookie) headers.set("cookie", init.cookie);
  if (init.origin !== null) headers.set("origin", init.origin ?? APP);
  return new Request(`${APP}/api/${path}`, { ...init, headers });
}

const ok = (body: unknown = {}, init: ResponseInit = {}) => Response.json(body, init);

describe("proxy: auth header handling", () => {
  it("turns the session cookie into a Bearer token", async () => {
    const f = vi.fn(async () => ok({ email: "o@x.com" }));
    const res = await proxy(req("auth/me", { cookie: "pa_session=tok123" }), ["auth", "me"], deps(f));
    expect(res.status).toBe(200);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${API}/auth/me`);
    expect(new Headers(init.headers).get("authorization")).toBe("Bearer tok123");
  });

  it("drops a client-supplied Authorization header and the cookie itself", async () => {
    const f = vi.fn(async () => ok());
    await proxy(
      req("conversations", {
        cookie: "pa_session=real; other=1",
        headers: { authorization: "Bearer attacker", "x-custom": "nope" },
      }),
      ["conversations"],
      deps(f),
    );
    const headers = new Headers((f.mock.calls[0] as unknown as [string, RequestInit])[1].headers);
    expect(headers.get("authorization")).toBe("Bearer real");
    expect(headers.get("cookie")).toBeNull();
    expect(headers.get("x-custom")).toBeNull();
  });

  it("rejects requests without a session before calling the API", async () => {
    const f = vi.fn();
    const res = await proxy(req("conversations"), ["conversations"], deps(f as unknown as typeof fetch));
    expect(res.status).toBe(401);
    expect(f).not.toHaveBeenCalled();
  });

  it("strips Set-Cookie from API responses", async () => {
    const f = vi.fn(async () => ok({}, { headers: { "set-cookie": "evil=1" } }));
    const res = await proxy(req("handoffs", { cookie: "pa_session=t" }), ["handoffs"], deps(f));
    expect(res.headers.get("set-cookie")).toBeNull();
  });
});

describe("proxy: CSRF and path allowlist", () => {
  it.each([
    ["missing origin", null],
    ["foreign origin", "https://evil.example"],
  ])("blocks POST with %s", async (_, origin) => {
    const f = vi.fn();
    const res = await proxy(
      req("actions/x/confirm", { method: "POST", cookie: "pa_session=t", origin }),
      ["actions", "x", "confirm"],
      deps(f as unknown as typeof fetch),
    );
    expect(res.status).toBe(403);
    expect(f).not.toHaveBeenCalled();
  });

  it("allows same-origin Referer when Origin is absent", () => {
    const r = new Request(`${APP}/api/x`, { method: "POST", headers: { referer: `${APP}/` } });
    expect(originAllowed(r, APP)).toBe(true);
  });

  it("allows GET without Origin", () => {
    expect(originAllowed(new Request(`${APP}/api/x`), APP)).toBe(true);
  });

  it.each([
    [["webhooks", "stripe"]],
    [["docs"]],
    [["openapi.json"]],
    [["health"]],
    [["auth", "login"]],
    [["conversations", "..", "webhooks"]],
    [["conversationsX"]],
  ])("refuses non-allowlisted path %j", async (segments) => {
    expect(isAllowedPath(segments)).toBe(false);
    const f = vi.fn();
    const res = await proxy(
      req(segments.join("/"), { cookie: "pa_session=t" }),
      segments,
      deps(f as unknown as typeof fetch),
    );
    expect(res.status).toBe(404);
    expect(f).not.toHaveBeenCalled();
  });

  it.each(ALLOWED_PREFIXES.map((p) => [p]))("allows %s", (prefix) => {
    expect(isAllowedPath(prefix.split("/"))).toBe(true);
  });
});

describe("proxy: bodies, query strings, streaming, abort", () => {
  it("forwards method, JSON body and query string", async () => {
    const f = vi.fn(async () => ok());
    await proxy(
      new Request(`${APP}/api/summaries/today?refresh=true`, {
        method: "POST",
        headers: { cookie: "pa_session=t", origin: APP, "content-type": "application/json" },
        body: JSON.stringify({ a: 1 }),
      }),
      ["summaries", "today"],
      deps(f),
    );
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${API}/summaries/today?refresh=true`);
    expect(init.method).toBe("POST");
    expect(new TextDecoder().decode(init.body as ArrayBuffer)).toBe('{"a":1}');
  });

  it("passes an SSE body through unbuffered, chunk by chunk", async () => {
    const enc = new TextEncoder();
    let push!: (s: string) => void;
    let close!: () => void;
    const upstreamBody = new ReadableStream<Uint8Array>({
      start(controller) {
        push = (s) => controller.enqueue(enc.encode(s));
        close = () => controller.close();
      },
    });
    const f = vi.fn(
      async () =>
        new Response(upstreamBody, { headers: { "content-type": "text/event-stream" } }),
    );
    const res = await proxy(
      req("conversations/c1/messages", { method: "POST", cookie: "pa_session=t" }),
      ["conversations", "c1", "messages"],
      deps(f),
    );
    expect(res.headers.get("content-type")).toBe("text/event-stream");
    expect(res.headers.get("x-accel-buffering")).toBe("no");

    const reader = res.body!.getReader();
    push("event: token\ndata: {}\n\n");
    // The first chunk is readable before the upstream finishes: nothing is buffered.
    const first = await reader.read();
    expect(new TextDecoder().decode(first.value)).toContain("event: token");
    close();
    expect((await reader.read()).done).toBe(true);
  });

  it("forwards the browser's abort signal upstream", async () => {
    const controller = new AbortController();
    let seen: AbortSignal | undefined;
    const f = vi.fn(async (_url: string, init: RequestInit) => {
      seen = init.signal ?? undefined;
      return ok();
    });
    await proxy(
      new Request(`${APP}/api/conversations`, {
        headers: { cookie: "pa_session=t" },
        signal: controller.signal,
      }),
      ["conversations"],
      deps(f as unknown as typeof fetch),
    );
    controller.abort();
    expect(seen?.aborted).toBe(true);
  });

  it("returns 502 when the API is down", async () => {
    const f = vi.fn(async () => {
      throw new TypeError("fetch failed");
    });
    const res = await proxy(req("handoffs", { cookie: "pa_session=t" }), ["handoffs"], deps(f));
    expect(res.status).toBe(502);
  });
});

describe("login / logout", () => {
  it("keeps the token out of the response body", async () => {
    const f = vi.fn(async () =>
      ok({ token: "secret-token", expires_at: "2026-10-10T00:00:00Z", owner: { id: "1", email: "o@x" } }),
    );
    const r = new Request(`${APP}/api/auth/login`, {
      method: "POST",
      headers: { origin: APP, "content-type": "application/json" },
      body: JSON.stringify({ email: "o@x", password: "pw" }),
    });
    const result = await login(r, deps(f));
    expect(result.token).toBe("secret-token");
    expect(result.expiresAt?.toISOString()).toBe("2026-10-10T00:00:00.000Z");
    const body = await result.response.text();
    expect(body).not.toContain("secret-token");
    expect(JSON.parse(body)).toEqual({ owner: { id: "1", email: "o@x" } });
  });

  it("passes through 401/429 without setting a token", async () => {
    for (const status of [401, 429]) {
      const f = vi.fn(async () => ok({ detail: "nope" }, { status }));
      const r = new Request(`${APP}/api/auth/login`, {
        method: "POST",
        headers: { origin: APP },
        body: "{}",
      });
      const result = await login(r, deps(f));
      expect(result.token).toBeUndefined();
      expect(result.response.status).toBe(status);
    }
  });

  it("refuses cross-site login posts", async () => {
    const f = vi.fn();
    const r = new Request(`${APP}/api/auth/login`, {
      method: "POST",
      headers: { origin: "https://evil.example" },
      body: "{}",
    });
    expect((await login(r, deps(f as unknown as typeof fetch))).response.status).toBe(403);
    expect(f).not.toHaveBeenCalled();
  });

  it("logout revokes upstream with the cookie token", async () => {
    const f = vi.fn(async () => new Response(null, { status: 204 }));
    const res = await logout(
      new Request(`${APP}/api/auth/logout`, {
        method: "POST",
        headers: { origin: APP, cookie: "pa_session=tok" },
      }),
      deps(f),
    );
    expect(res.status).toBe(204);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`${API}/auth/logout`);
    expect(new Headers(init.headers).get("authorization")).toBe("Bearer tok");
  });
});
