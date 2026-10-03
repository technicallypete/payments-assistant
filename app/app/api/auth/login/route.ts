import { cookies } from "next/headers";

import { login } from "@/lib/bff";
import { appOrigin, serverApiUrl } from "@/lib/config";
import { SESSION_COOKIE, sessionCookieOptions } from "@/lib/session";

export async function POST(request: Request) {
  const origin = appOrigin();
  const result = await login(request, { apiUrl: serverApiUrl(), appOrigin: origin, fetch });
  if (result.token) {
    (await cookies()).set(
      SESSION_COOKIE,
      result.token,
      sessionCookieOptions(origin, result.expiresAt),
    );
  }
  return result.response;
}
