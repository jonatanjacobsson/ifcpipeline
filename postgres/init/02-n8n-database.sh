#!/usr/bin/env bash
# Create dedicated database for n8n (runs only on first Postgres data directory init).
# Uses POSTGRES_USER as owner so n8n can share the same credentials as pipeline workers.
# For existing Postgres volumes created before this script, run the CREATE DATABASE
# command manually — see docs/n8n-postgres-migration.md

set -euo pipefail

N8N_DB="${N8N_POSTGRESDB_DATABASE:-n8n}"
OWNER="${POSTGRES_USER:?POSTGRES_USER must be set}"

exists="$(psql -v ON_ERROR_STOP=1 -U "$OWNER" -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname = '$N8N_DB'")"
if [ "$exists" = "1" ]; then
  echo "Database \"$N8N_DB\" already exists; skipping."
  exit 0
fi

psql -v ON_ERROR_STOP=1 -U "$OWNER" -d postgres -c "CREATE DATABASE \"$N8N_DB\" OWNER \"$OWNER\";"

echo "Created database \"$N8N_DB\" owned by \"$OWNER\"."
