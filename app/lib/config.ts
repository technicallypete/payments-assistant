import "server-only";

/**
 * Base URL of the Python API on the compose network.
 *
 * Server-side only: the browser never calls Python directly. Every request goes through
 * a Next route handler, which calls this URL.
 */
export function serverApiUrl(): string {
  const url = process.env.API_URL;
  if (!url) {
    throw new Error("API_URL is not set (expected e.g. http://api:8000)");
  }
  return url.replace(/\/+$/, "");
}

/** Public origin of this app (proxy Origin check, cookie Secure flag). */
export function appOrigin(): string {
  return (process.env.APP_ORIGIN ?? "http://localhost:3010").replace(/\/+$/, "");
}
