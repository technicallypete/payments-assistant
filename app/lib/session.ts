/**
 * The owner's session token lives ONLY in this httpOnly cookie. Browser JS never sees it; the
 * BFF route handlers read it and forward it to the Python API as a Bearer token.
 */
export const SESSION_COOKIE = "pa_session";

export interface CookieOptions {
  httpOnly: true;
  sameSite: "lax";
  secure: boolean;
  path: "/";
  expires?: Date;
}

export function sessionCookieOptions(appOrigin: string, expires?: Date): CookieOptions {
  return {
    httpOnly: true,
    sameSite: "lax",
    // Secure whenever the app is served over https; plain http only for localhost dev.
    secure: appOrigin.startsWith("https://"),
    path: "/",
    ...(expires ? { expires } : {}),
  };
}

/** Read one cookie from a raw Cookie header (route handlers can also use request.cookies). */
export function readCookie(cookieHeader: string | null, name: string): string | undefined {
  if (!cookieHeader) return undefined;
  for (const part of cookieHeader.split(";")) {
    const [k, ...rest] = part.trim().split("=");
    if (k === name) return decodeURIComponent(rest.join("="));
  }
  return undefined;
}
