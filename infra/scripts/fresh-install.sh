#!/usr/bin/env bash
# infra/scripts/fresh-install.sh
#
# Explicit, DESTRUCTIVE reset of the agent-registry database. Runs
# `prisma db push --accept-data-loss`, which diffs the live schema against
# schema.prisma and DROPS whatever diverges. Use this only when you actually
# intend to wipe agent-registry's database (local dev, or a genuine fresh
# install where you want to skip the migration history).
#
# Normal boot (`docker compose up` / restart / rebuild) NEVER runs this —
# the container's default entrypoint is the non-destructive
# `packages/agent-registry/scripts/bootstrap-db.js`, which auto-detects the
# database state and only ever applies pending migrations (`prisma migrate
# deploy`), baselining a legacy db-push database the first time it sees one
# instead of dropping anything.
#
# This script just sets FRESH_INSTALL=true for a single run of that same
# script, through docker compose, so there is exactly one code path that
# knows how to run the destructive command.
#
# Usage:
#   ./infra/scripts/fresh-install.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker-compose.demo.yml"

echo "⚠️  This resets the agent-registry database: 'prisma db push --accept-data-loss'"
echo "    drops any table/column that diverges from schema.prisma. agent-registry runs"
echo "    against its own isolated database (plughub_registry — see docker-compose.demo.yml),"
echo "    so this does NOT touch config-api/auth/calendar/other services' data. It DOES wipe"
echo "    any pool/skill created via the UI that isn't declared in infra/registry/*.yaml —"
echo "    those get reseeded on next boot, anything else is lost."
read -r -p "Type 'yes' to continue: " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
  echo "Aborted."
  exit 1
fi

docker compose -f "$COMPOSE_FILE" run --rm -e FRESH_INSTALL=true agent-registry \
  node scripts/bootstrap-db.js

echo "✅ Reset complete. Start the service normally:"
echo "   docker compose -f \"$COMPOSE_FILE\" up -d agent-registry"
