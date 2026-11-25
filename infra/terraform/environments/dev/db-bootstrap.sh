set -eu
# db-bootstrap: one database and owning login role per service, mirroring
# infra/local/postgres/initdb/10-service-databases.sh on RDS. Idempotent: run it
# after the first apply and again after rotating a database password.
#
# Connects as the RDS-managed master user (PGUSER/PGPASSWORD come from the master
# secret). Values reach psql as environment variables (\getenv), never through argv
# or shell-built SQL; format() quotes them as identifiers (%I) or literals (%L).
psql --no-psqlrc --set=ON_ERROR_STOP=1 --quiet <<'SQL'
\getenv gateway_db GATEWAY_DB_NAME
\getenv gateway_user GATEWAY_DB_USER
\getenv gateway_password GATEWAY_DB_PASSWORD
\getenv agent_db AGENT_DB_NAME
\getenv agent_user AGENT_DB_USER
\getenv agent_password AGENT_DB_PASSWORD

-- Serialise concurrent runs.
SELECT pg_advisory_lock(7419) \gset

-- Login roles: created once, password re-synced on every run (rotation).
SELECT format('CREATE ROLE %I LOGIN', :'gateway_user') WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'gateway_user') \gexec
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'gateway_user', :'gateway_password') \gexec
SELECT format('CREATE ROLE %I LOGIN', :'agent_user') WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'agent_user') \gexec
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'agent_user', :'agent_password') \gexec

-- The master user may hand databases to these roles only as a member of them.
SELECT format('GRANT %I TO CURRENT_USER', :'gateway_user') \gexec
SELECT format('GRANT %I TO CURRENT_USER', :'agent_user') \gexec

-- One database per service, owned by its role. RDS created the gateway's database
-- (owned by the master user); it is handed over here.
SELECT format('CREATE DATABASE %I OWNER %I', :'gateway_db', :'gateway_user') WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'gateway_db') \gexec
SELECT format('ALTER DATABASE %I OWNER TO %I', :'gateway_db', :'gateway_user') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'agent_db', :'agent_user') WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'agent_db') \gexec
SELECT format('ALTER DATABASE %I OWNER TO %I', :'agent_db', :'agent_user') \gexec

-- Each role reaches its own database only: the agent runtime can never touch the
-- approval records.
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'gateway_db') \gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'agent_db') \gexec
SQL
echo "db-bootstrap: service roles and databases are in place"
