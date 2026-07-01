-- Dedicated database for agent-registry.
--
-- The agent-registry container runs `prisma db push --accept-data-loss` on every
-- boot, which DROPS tables in the public schema that are not in its Prisma schema
-- (e.g. config-api's platform_config, auth, calendar tables). Giving it a separate
-- database keeps it from clobbering plughub_demo.
--
-- Runs only on a fresh Postgres data volume (docker-entrypoint-initdb.d). For an
-- existing volume, create it once manually:
--   docker exec <postgres> psql -U plughub -d plughub_demo -c "CREATE DATABASE plughub_registry;"

SELECT 'CREATE DATABASE plughub_registry'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'plughub_registry')\gexec
