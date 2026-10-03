import { cookies } from "next/headers";

import { logout } from "@/lib/bff";
import { appOrigin, serverApiUrl } from "@/lib/config";
import { SESSION_COOKIE } from "@/lib/session";

export async function POST(request: Request) {
  const response = await logout(request, {
    apiUrl: serverApiUrl(),
    appOrigin: appOrigin(),
    fetch,
  });
  if (response.status === 204) (await cookies()).delete(SESSION_COOKIE);
  return response;
}
