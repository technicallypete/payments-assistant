---
name: Next.js Setup
description: Scaffold a Next.js frontend (the "app" service) that talks to a separate backend API over HTTP, running on Bun with hot reload inside Docker. Locks in App Router, TypeScript, the API-client convention, and dev-container env wiring. Use this whenever the user wants to set up a Next.js app, a React frontend, the "app" service of a full-stack project, or a frontend that calls a Django/Flask API, even if they don't spell out the conventions.
command: /nextjs-setup
---

# Next.js Setup

Scaffold the `app` service — a Next.js frontend that runs on Bun and calls the backend
`api` service over HTTP. It lives in `./app` and runs as the `app` container.

## Conventions to lock in

- **Runtime:** Bun (`oven/bun:1` in Docker; `bun run dev` locally). Not Alpine.
- **Router:** App Router (`app/` directory), not the legacy Pages Router.
- **Language:** TypeScript, strict mode.
- **Styling / lint:** whatever the user prefers — ask once, then bake in Tailwind +
  ESLint/Prettier config if they don't object.

## Talking to the backend

The frontend and backend are **separate services**, so the frontend never imports backend
code — it calls it over HTTP. Two base URLs, because server components and browser code
reach the API by different hostnames:

- `API_URL=http://api-web:8000` — server-side (inside the Docker network, service `api-web`).
- `NEXT_PUBLIC_API_URL=http://localhost:8000` — browser (host-reachable port; `API_WEB_HOST_PORT`).

Centralize this in one API client module so the split lives in exactly one place:

```ts
// app/lib/api.ts
const base = typeof window === "undefined"
  ? process.env.API_URL              // server component / route handler
  : process.env.NEXT_PUBLIC_API_URL; // browser

export async function apiFetch(path: string, init?: RequestInit) {
  const res = await fetch(`${base}${path}`, init);
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.json();
}
```

## Hot reload in Docker

Bind-mounting the source into the container can miss file-watch events on Mac/Windows
(inotify doesn't always propagate over Docker volumes). Set `WATCHPACK_POLLING=true` in the
`app` service env if edits don't trigger rebuilds; drop it on Linux hosts where inotify
works natively.

## Scaffolding steps

1. `bun create next-app app --typescript --app --no-src-dir` (adjust flags to the user's
   Tailwind/ESLint answers).
2. Add `app/lib/api.ts` with the dual base-URL client above.
3. Ensure `package.json` has `dev`, `build`, `start` scripts.
4. Confirm the Docker `app` service mounts `./app:/app` with an anonymous
   `/app/node_modules` volume, and sets both API base URLs.

## Verification

```bash
docker compose up -d app
docker compose logs -f app     # expect "Ready" from Next
# hit the app on http://localhost:3000
```
