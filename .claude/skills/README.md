# Bootstrap skills

Verbatim copies (taken 2026-10-03, unedited) of the personal Claude Code skills used to bootstrap
this repo. They live here as project-scoped skills so the scaffolding approach is reproducible and
reviewable alongside the code it produced. The project then deliberately diverged from them in
several places:

| Skill | How this project adapted it |
|---|---|
| `docker-setup` | Postgres roles are `admin`/`api`/`bot`/`reporter`, created by a shell init script that reads passwords from env (`postgres/init/01-roles.sh` instead of `01-roles.sql`). Table grants moved into Alembic migrations instead of `ALTER DEFAULT PRIVILEGES`, so dev and test DBs match. No MinIO. Services are FastAPI `api`, `bot`, `api-migrate`, and `api-test` instead of `api-web`/`api-mcp`. `env_file` holds secrets; ports and wiring default via `${VAR:-default}` (3010/8010/5433). The venv lives at `/opt/venv`, and containers run as uid 1000. |
| `nextjs-setup` | The browser never calls Python (no `NEXT_PUBLIC_API_URL`): Next route handlers act as a BFF with a catch-all proxy that turns the httpOnly session cookie into a Bearer token. Typecheck is `next typegen && tsc` (Next 16). Component tests use happy-dom (jsdom doesn't run under Bun). |
| `bootstrap-fullstack` | Used for ordering and naming discipline only (backend → frontend → compose → reconcile). Django was swapped for FastAPI. |

`django-project` and `auth-resend` were considered and intentionally **not** copied: the backend is
FastAPI, and owner auth is password login with opaque sessions behind the Next BFF (see
`docs/v0/spec.md` §2–3).
