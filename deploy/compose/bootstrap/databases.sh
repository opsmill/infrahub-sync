#!/bin/sh
# Converge the two owner roles and the two databases this deployment needs.
#
# Run through the PostgreSQL administrator, which is the only credential this
# bundle mounts as a file. Everything here is safe to repeat: a second run finds
# both roles and both databases and changes nothing.
set -eu

PGPASSWORD="$(cat /run/secrets/postgres-admin-password)"
export PGPASSWORD

# `psql -c` interpolates nothing, so every operator-supplied value crosses into
# SQL through a bound variable and PostgreSQL's own quoting functions. A role
# name or password carrying a quote is then a value, never syntax.
psql --set=ON_ERROR_STOP=1 --quiet \
  --set=role="${INFRAHUB_SYNC_PRODUCT_ROLE}" \
  --set=password="${INFRAHUB_SYNC_PRODUCT_PASSWORD}" \
  --set=database="${INFRAHUB_SYNC_PRODUCT_DATABASE}" \
  --file=/dev/stdin <<'SQL'
SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L', :'role', :'password'
) WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \gexec
SELECT format(
  'CREATE DATABASE %I OWNER %I', :'database', :'role'
) WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'database') \gexec
SQL

psql --set=ON_ERROR_STOP=1 --quiet \
  --set=role="${INFRAHUB_SYNC_PREFECT_ROLE}" \
  --set=password="${INFRAHUB_SYNC_PREFECT_PASSWORD}" \
  --set=database="${INFRAHUB_SYNC_PREFECT_DATABASE}" \
  --file=/dev/stdin <<'SQL'
SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L', :'role', :'password'
) WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \gexec
SELECT format(
  'CREATE DATABASE %I OWNER %I', :'database', :'role'
) WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'database') \gexec
SQL

echo "infrahub-sync: product and Prefect roles and databases are present"
