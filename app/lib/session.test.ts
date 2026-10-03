import { describe, expect, it } from "vitest";

import { readCookie, sessionCookieOptions } from "./session";

describe("session cookie", () => {
  it("is httpOnly, SameSite=Lax, path=/", () => {
    const o = sessionCookieOptions("http://localhost:3010");
    expect(o).toMatchObject({ httpOnly: true, sameSite: "lax", path: "/", secure: false });
  });

  it("is Secure when served over https", () => {
    expect(sessionCookieOptions("https://pay.example").secure).toBe(true);
  });

  it("carries the API's expiry", () => {
    const d = new Date("2026-10-10T00:00:00Z");
    expect(sessionCookieOptions("http://x", d).expires).toBe(d);
  });

  it("reads a cookie from a header", () => {
    expect(readCookie("a=1; pa_session=abc%3D; b=2", "pa_session")).toBe("abc=");
    expect(readCookie("a=1", "pa_session")).toBeUndefined();
    expect(readCookie(null, "pa_session")).toBeUndefined();
  });
});
