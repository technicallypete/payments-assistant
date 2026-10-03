import { proxy } from "@/lib/bff";
import { appOrigin, serverApiUrl } from "@/lib/config";

function deps() {
  return { apiUrl: serverApiUrl(), appOrigin: appOrigin(), fetch };
}

async function handle(request: Request, ctx: RouteContext<"/api/[...path]">) {
  const { path } = await ctx.params;
  return proxy(request, path, deps());
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
