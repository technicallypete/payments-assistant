---
name: Bootstrap Full-Stack Project
description: Orchestrate a from-scratch full-stack project bootstrap in an empty directory — a Django backend (core/web/mcp), a Next.js frontend, and a Docker Compose environment with Postgres and MinIO, scaffolded in the correct order and reconciled at the root. Use this whenever the user wants to bootstrap, scaffold, spin up, or start a new full-stack project, set up a whole repo from nothing, or run the docker + Next.js + Django setup together, even if they only name one part.
command: /bootstrap-fullstack
---

# Bootstrap Full-Stack Project

Scaffold a complete full-stack repo from an empty directory by invoking the component
skills **in order** and then reconciling the shared root files. Skills run sequentially,
not in parallel — this is a feature, because bootstrapping is inherently ordered and
parallel writers would collide on shared root files (`.env`, `docker-compose.yml`).

## Order matters — follow it

1. **Django backend** → `django-project` skill. Produces `./api` (core/web/mcp, settings
   split, pytest). Do this first: the compose file references what it produces.
2. **Next.js frontend** → `nextjs-setup` skill. Produces `./app` (Bun, App Router, API
   client). Second, for the same reason.
3. **Docker Compose** → `docker-setup` skill. Produces `docker-compose.yml`, `docker/`,
   and the `.dockerignore` / `.env.example` files. **Last**, because it wires together the
   two services that now exist.

Then **reconcile the root**:

- Merge into a single `.env` (from `docker-setup`'s `.env.example`), filling any values the
  backend/frontend skills assumed.
- Confirm one source of truth for the DB name and bucket name (`${DB_NAME}`, `${S3_BUCKET}`)
  across every service.
- Confirm the service/folder names line up: `app`↔`./app`, `api`↔`./api`. The Django apps
  inside are `core`/`web`/`mcp` — never `app` or `api` (those name the container layer).
- Write a root `README.md` with the one-command bring-up.

## Naming contract (enforce across all three)

| Layer            | Names                                  |
|------------------|----------------------------------------|
| Docker services  | `app`, `api-web`, `api-mcp`, `api-migrate`, `postgres`, `minio`, `minio-init` |
| Top-level folders| `./app`, `./api`, `./docker`, `./postgres` |
| Django apps      | `core`, `web`, `mcp`                    |
| Postgres roles   | `admin` (migrations), `web` (api), `mcp` (mcp), `reporter`, `app` |

Note `api-web` and `api-mcp` are two containers off the **same** `./api` image — they differ only
in `DATABASE_URL` (role `web` vs `mcp`) and which port/surface they expose. The `api-migrate`
job runs first as `admin`; both gate on it. This is set up by the `docker-setup` skill.

If any component skill tries to introduce a Django app named `app` or `api`, correct it —
that collision is the single most common bootstrap mistake.

## One-command bring-up (goes in the README)

```bash
cp .env.example .env          # set DB_NAME / S3_BUCKET (the skill prompts for these)
docker compose up --build
# app  → http://localhost:3000
# api-web → http://localhost:8000/health        (REST, web role)
# api-mcp → http://localhost:8001/mcp/sse        (MCP over SSE, mcp role)
# minio console → http://localhost:9001
```

The `docker-setup` skill prompts for the project name — nothing ships with a literal
project name baked in.

## Final verification

```bash
docker compose config                              # whole graph parses
docker compose run --rm api-web uv run python manage.py check
docker compose run --rm api-web uv run pytest
docker compose up --build -d && docker compose ps  # all healthy
```

## If the user wants only part

They may invoke a single component skill directly (`/django-project`, `/nextjs-setup`,
`/docker-setup`). This orchestrator is for the whole-repo case. When only one service is
wanted, defer to that skill and skip the reconciliation step.
