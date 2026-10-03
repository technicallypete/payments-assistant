-- 01-roles.sql — runs once at Postgres first-start as the superuser.
--
-- Name-agnostic by design: roles are cluster-global (no DB name needed), and
-- the CONNECT grant uses current_database() so this same file is correct for
-- whatever POSTGRES_DB the compose stack set — no templating required.
--
-- Creates least-privilege LOGIN roles, grants CONNECT + schema USAGE, and sets
-- DEFAULT PRIVILEGES so tables created by future admin-run migrations are
-- auto-granted to the runtime roles (migrations run as admin, who owns every
-- table; without this the runtime roles get "permission denied").

-- 1. Runtime roles (idempotent).
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'web')      THEN CREATE ROLE web      WITH LOGIN PASSWORD 'web';      END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mcp')      THEN CREATE ROLE mcp      WITH LOGIN PASSWORD 'mcp';      END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'reporter') THEN CREATE ROLE reporter WITH LOGIN PASSWORD 'reporter'; END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app')      THEN CREATE ROLE app      WITH LOGIN PASSWORD 'app';      END IF;
END
$$;

-- 2. CONNECT on whichever DB the entrypoint created (resolved at runtime).
DO $$
BEGIN
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO web, mcp, reporter, app', current_database());
END
$$;

-- 3. Schema usage (PG15+ no longer grants this implicitly).
GRANT USAGE ON SCHEMA public TO web, mcp, reporter, app;

-- 4. DEFAULT PRIVILEGES — future admin-created tables/sequences auto-granted.
--    web: full read/write (REST surface).
ALTER DEFAULT PRIVILEGES FOR ROLE admin IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO web;
--    mcp: read/write by default — tighten to SELECT only if the agent-facing
--    surface is read-mostly (that's where the least-privilege payoff is).
ALTER DEFAULT PRIVILEGES FOR ROLE admin IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO mcp;
--    reporter: read-only (BI / reporting; not wired to any container by default).
ALTER DEFAULT PRIVILEGES FOR ROLE admin IN SCHEMA public
  GRANT SELECT ON TABLES TO reporter;
--    sequences for the write roles (INSERT needs nextval).
ALTER DEFAULT PRIVILEGES FOR ROLE admin IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO web, mcp;
