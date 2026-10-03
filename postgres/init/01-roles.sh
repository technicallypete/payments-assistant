#!/bin/bash
# Runs once, on the first start of an empty postgres volume, as the superuser (DB_ADMIN_USER).
# Creates the least-privilege runtime roles with passwords from env. Table-level grants and RLS
# policies are NOT here: they live in Alembic migrations, so every database (dev and test) gets
# identical, version-controlled privileges.
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v api_pw="${DB_API_PASSWORD:-api}" \
  -v bot_pw="${DB_BOT_PASSWORD:-bot}" \
  -v reporter_pw="${DB_REPORTER_PASSWORD:-reporter}" <<'SQL'
-- Runtime roles: LOGIN, not superuser, cannot bypass RLS.
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD %L', r.name, r.pw)
FROM (VALUES ('api', :'api_pw'), ('bot', :'bot_pw'), ('reporter', :'reporter_pw')) AS r(name, pw)
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = r.name)
\gexec

SELECT format('GRANT CONNECT ON DATABASE %I TO api, bot, reporter', current_database())
\gexec

GRANT USAGE ON SCHEMA public TO api, bot, reporter;
SQL
