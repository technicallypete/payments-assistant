---
name: Docker Compose Setup
description: Scaffold a Docker Compose dev environment for a full-stack project — Postgres (pgvector), MinIO (S3-compatible), and build stanzas for a Next.js frontend and a Django/Python backend. Use this whenever the user wants to set up Docker, docker-compose, containerized services, a multi-container dev environment, local Postgres/MinIO, or wire services together for hot-reload development, even if they don't say "Docker Compose" explicitly.
command: /docker-setup
---

# Docker Compose Setup

Scaffold a `docker-compose.yml` plus the `docker/` build files for a full-stack dev
environment. Default stack: **Postgres (pgvector)**, **MinIO**, a **Next.js `app`**
service, and a **Django `api`** service. Everything runs concurrently with hot reload
and healthcheck-gated startup ordering.

## First: prompt for project identity

Before writing any files, ask the user for the project name and use it to set `DB_NAME`
and `S3_BUCKET`. Do not hard-code a project name into the compose file or the init SQL.

Ask something like: *"What should I name the database and storage bucket? (default: `myapp`)"*
If they give one name, use it for both. If they want them different, take both. Then:

- Write the answer into `.env` as `DB_NAME` and `S3_BUCKET`.
- Leave the compose file referencing `${DB_NAME:-myapp}` / `${S3_BUCKET:-myapp}` — never the
  literal. The name lives only in `.env`.
- The init SQL needs **no** substitution — it's name-agnostic by design (see Postgres roles).

Slugify the name for identifiers: lowercase, digits, and hyphens only (Postgres DB names and
S3 buckets both dislike spaces and uppercase). If the user's name has spaces, convert to
hyphens and confirm.

## Postgres roles (least privilege)

Bootstrap four cluster-global LOGIN roles via `postgres/init/01-roles.sql`, which runs once
at first container start as the superuser:

| Role       | Privilege                    | Used by                          |
|------------|------------------------------|----------------------------------|
| `admin`    | superuser (POSTGRES_USER)    | `migrate` job — owns every table |
| `web`      | SELECT/INSERT/UPDATE/DELETE  | `api-web` service (REST surface) |
| `mcp`      | read/write (tighten to read) | `api-mcp` service (agent surface)|
| `reporter` | SELECT only                  | external BI / reporting (unwired)|
| `app`      | created but unwired          | only if Next needs direct DB     |

Two things make this work and are easy to get wrong:

1. **The init SQL is name-agnostic.** Roles are cluster-global (no DB name in `CREATE ROLE`),
   and the CONNECT grant uses `current_database()`, so the one file is correct for any
   `POSTGRES_DB`. This is why "make it generic" requires no templating of the SQL — only the
   compose `POSTGRES_DB` / `DATABASE_URL`s carry the name, and those are already variables.

2. **Migrations run as `admin`, so admin owns every table.** A superuser owning a table does
   not grant access to other roles. Use `ALTER DEFAULT PRIVILEGES FOR ROLE admin` at init time
   so every table created by future migrations is auto-granted to the runtime roles — no
   post-migrate grant step, and it covers migrations that don't exist yet.

Separate DB roles per surface require **separate processes**: a connection authenticates as
exactly one role, set by that process's `DATABASE_URL`. So `api-web` (role `web`) and `api-mcp`
(role `mcp`) run as two containers off the same image, differing only in `DATABASE_URL` and which
port they expose. `core` never learns which surface called it — the boundary is enforced at
the socket, not in code.

## Migrations: a one-shot `migrate` job

Django has no native `DATABASE_ADMIN_URL`. The idiomatic equivalent is a `migrate` service
(`api-migrate`) that connects as `admin`, runs `manage.py migrate`, and exits. `api-web` and `api-mcp`
gate on it with `depends_on: api-migrate: { condition: service_completed_successfully }`.

**Tests need `CREATEDB`**, which the runtime roles lack. Run pytest as `admin`:

```bash
docker compose run --rm \
  -e DATABASE_URL=postgresql://${DB_ADMIN_USER:-admin}:${DB_ADMIN_PASSWORD:-admin}@postgres:5432/${DB_NAME} \
  api-web uv run pytest
```

## Service and folder naming

Keep the container/infra layer and any in-app module names separate — never reuse
`app` or `api` for both a container and a code module.

| Docker service | Folder      | Base image / build          |
|----------------|-------------|-----------------------------|
| `app`          | `./app`     | `oven/bun:1` (Debian)       |
| `api-web`      | `./api`     | `python:3.13-slim` + `uv`   |
| `api-mcp`      | `./api`     | `python:3.13-slim` + `uv` (same image) |
| `api-migrate`  | `./api`     | `python:3.13-slim` + `uv` (one-shot) |
| `postgres`     | —           | `pgvector/pgvector:pg16`    |
| `minio`        | —           | `minio/minio:latest`        |
| `minio-init`   | —           | `minio/minio:latest` (job)  |

`postgres` and `minio` use stock images — **no Dockerfile**. Only `app` and `api`
get built, and their Dockerfiles live centrally in `docker/`.

## Base image rules (do not deviate without reason)

- **Never use Alpine for Python.** musl libc forces source builds of wheels that ship
  as glibc manylinux binaries — slow builds and periodic breakage. Use `python:3.13-slim`
  (Debian).
- **Never use Alpine for Bun.** Bun expects glibc; the musl variant is flaky. Use the
  official `oven/bun:1`.
- Use **`psycopg[binary]`** (psycopg 3 prebuilt wheel) so the `api` image needs no
  `gcc`/`libpq-dev` and no `apt install` line.

## Dockerfile layout: `docker/Dockerfile.<service>`

Centralize built Dockerfiles under `docker/` with a scoped build context per service:

```yaml
api:
  build:
    context: ./api
    dockerfile: ../docker/Dockerfile.api
```

Scoped context (`./api`) means each build only ships its own folder — faster, cleaner
cache isolation than a root context. The `../docker/...` path places the Dockerfile
outside its context, which is allowed under BuildKit (default since Docker 23). Ship a
tight `.dockerignore` in each service folder regardless.

If the user's existing compose already uses `context: .` (root), offer the root-context
variant instead (`dockerfile: docker/Dockerfile.api`) to match their convention, and warn
that a change in one service folder can then invalidate the other's build cache.

## Parameterization

The DB name and bucket name must be params with sensible defaults, so the same compose
seeds any project:

```yaml
POSTGRES_DB: ${DB_NAME:-myapp}
# minio-init:
mc mb --ignore-existing local/${S3_BUCKET:-myapp}
```

Wire the same `${DB_NAME}` / `${S3_BUCKET}` into every `DATABASE_URL` and S3 env so
there's one source of truth. Ship a `.env.example` documenting every var.

## Running all services in parallel

`docker compose up` runs every service concurrently — there is no separate "docker"
process to run alongside. `app` (bun dev) and `api` (Django runserver) both mount their
source as a volume and both `depends_on` the `postgres` healthcheck. Startup order is
enforced by healthchecks + `condition: service_healthy`, not by launch timing.

**Hot-reload caveat across volume mounts:** inotify events don't always propagate on
Mac/Windows Docker volumes. Django's `StatReloader` polls mtimes and works fine. For
Next/bun, set `WATCHPACK_POLLING=true` (or turbopack's poll flag) if edits don't trigger
rebuilds. The skill sets this in the `app` service env by default and notes it can be
removed on Linux hosts.

## Assets

Copy and adapt these into the project root:

- `assets/docker-compose.yml` — full stack: `postgres`, `migrate`, `api` (web role), `mcp`
  (mcp role), `app`, `minio`, `minio-init`. Parameterized on `${DB_NAME}` / `${S3_BUCKET}`.
- `assets/postgres/init/01-roles.sql` → `postgres/init/` — name-agnostic role bootstrap.
- `assets/docker/Dockerfile.api` — Django, slim + uv (shared by `migrate`, `api`, `mcp`).
- `assets/docker/Dockerfile.app` — Next.js, bun.
- `assets/.dockerignore.api` → `api/.dockerignore`
- `assets/.dockerignore.app` → `app/.dockerignore`
- `assets/.env.example` — every variable with a default; `DB_NAME`/`S3_BUCKET` come from the
  prompt.

## Verification

After scaffolding, confirm the config parses and the graph is sane:

```bash
docker compose config          # validates + shows resolved config
docker compose up -d postgres minio
docker compose ps              # healthchecks should go healthy
```
