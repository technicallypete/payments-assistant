import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import { serverApiUrl } from "./config";

describe("serverApiUrl", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("returns API_URL without a trailing slash", () => {
    vi.stubEnv("API_URL", "http://api:8000/");
    expect(serverApiUrl()).toBe("http://api:8000");
  });

  it("throws when API_URL is unset", () => {
    vi.stubEnv("API_URL", "");
    expect(() => serverApiUrl()).toThrow(/API_URL is not set/);
  });
});
