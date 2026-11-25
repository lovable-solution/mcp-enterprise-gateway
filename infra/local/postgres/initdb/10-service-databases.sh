#!/usr/bin/env bash
# Runs once, when the Postgres data directory is first initialised.
#
# One database per service, each owned by its own login role, with the default PUBLIC
# CONNECT privilege revoked so a role can reach only its own database. In particular the
# agent runtime (LangGraph checkpoints) cannot touch the gateway's approval records: an
# agent must never be able to approve its own tool call by writing to the database.
# This mirrors the per-service credentials used on RDS.
set -Eeuo pipefail

create_service_database() {
  local db="$1" owner="$2" password="$3"
  # Values reach psql as environment variables (\getenv), never via argv or shell-built
  # SQL; format() quotes them as an identifier (%I) or a literal (%L).
  SVC_DB="$db" SVC_OWNER="$owner" SVC_PASSWORD="$password" \
    psql --no-psqlrc -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<'SQL'
\getenv svc_db SVC_DB
\getenv svc_owner SVC_OWNER
\getenv svc_password SVC_PASSWORD
SELECT format('CREATE ROLE %I WITH LOGIN PASSWORD %L', :'svc_owner', :'svc_password') \gexec
SELECT format('CREATE DATABASE %I WITH OWNER %I', :'svc_db', :'svc_owner') \gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'svc_db') \gexec
SQL
  echo "created database '${db}' owned by role '${owner}' (PUBLIC access revoked)"
}

create_service_database "${GATEWAY_DB_NAME:?}" "${GATEWAY_DB_USER:?}" "${GATEWAY_DB_PASSWORD:?}"
create_service_database "${AGENT_DB_NAME:?}" "${AGENT_DB_USER:?}" "${AGENT_DB_PASSWORD:?}"
