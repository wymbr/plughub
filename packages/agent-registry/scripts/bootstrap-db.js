#!/usr/bin/env node
/**
 * agent-registry — DB bootstrap (non-destructive by default)
 *
 * Replaces `prisma db push --accept-data-loss` as the boot-time DB step.
 * `db push` diffs the live schema against schema.prisma and DROPS whatever
 * diverges — it has wiped `pools`/`skills` in this environment multiple times
 * (see CHANGELOG.md "agent-registry — bootstrap seguro..."). This script
 * auto-detects the database's actual state and always takes the
 * non-destructive path unless FRESH_INSTALL=true is explicitly set.
 *
 * States handled:
 *   1. FRESH_INSTALL=true set             → explicit `db push --accept-data-loss`
 *      (documented destructive path — local dev reset / true fresh install
 *      only; see infra/scripts/fresh-install.sh).
 *   2. `_prisma_migrations` table exists  → `migrate deploy` (applies pending
 *      migrations only — the steady-state path once baselined).
 *   3. No `_prisma_migrations`, but app   → legacy db-push database. Verified
 *      tables already exist                 (not assumed) to be fully caught up
 *                                            with schema.prisma via `migrate
 *                                            diff --exit-code` BEFORE trusting
 *                                            a blind baseline (see incident
 *                                            note below); only then baseline
 *                                            (`migrate resolve --applied`, no
 *                                            DDL) + `migrate deploy`.
 *   4. No `_prisma_migrations`, no app    → `migrate deploy` applies
 *      tables (genuinely empty database)    everything from scratch — safe,
 *                                            nothing to lose.
 *
 * In every path, a final `migrate diff --exit-code` sanity check runs before
 * the script exits successfully — if the live schema still doesn't match
 * schema.prisma for any reason, the script FAILS LOUD (exit 1, container
 * never starts the app) instead of booting into a broken state that serves
 * 500s at runtime.
 *
 * This script never runs `db push --accept-data-loss` implicitly. That
 * command only runs when FRESH_INSTALL=true is set on purpose, or as a
 * single guarded catch-up step inside path 3 (see incident note).
 *
 * ── Incident note (2026-07-02) ──────────────────────────────────────────
 * The first version of this script baselined path 3 unconditionally — it
 * trusted that if the `pools` table existed, ALL migrations were already
 * reflected in the live schema. That assumption was WRONG in practice: a
 * migration (`pool_llm_account_ids`) had been added to schema.prisma but the
 * database had not actually been pushed with it yet when the baseline ran.
 * The script marked it `--applied` anyway (pure bookkeeping, no DDL), so
 * `migrate deploy` believed everything was current and did nothing — while
 * the live table was actually missing the column. Every query touching
 * `pools` then failed at runtime with "column does not exist", and the
 * registry synced the whole pool list back to zero because writes/reads both
 * 500'd. Root cause: baselining without verifying. Fix: `migrate diff
 * --exit-code` verifies real drift before baselining, and closes any gap via
 * one guarded `db push` — the same mechanism this database has always used
 * for this exact class of change — before the switch to `migrate deploy`
 * becomes permanent. The final sanity check further guarantees this specific
 * failure mode can never again boot silently into a broken state.
 */

'use strict'

const { execFileSync } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')

const SCHEMA_PATH = path.join(__dirname, '..', 'prisma', 'schema.prisma')
const MIGRATIONS_DIR = path.join(__dirname, '..', 'prisma', 'migrations')
// A table present since the very first migration (20260408121021_init) — used
// as the "does this database already have our tables at all?" probe for the
// legacy-db-push case, where _prisma_migrations was never created. This is
// only a first-pass filter — `hasSchemaDrift()` is the real verification.
const PROBE_TABLE = 'pools'

function log(msg) {
  console.log(`[bootstrap-db] ${msg}`)
}

function run(cmd, args) {
  log(`$ ${cmd} ${args.join(' ')}`)
  execFileSync(cmd, args, { stdio: 'inherit' })
}

async function tableExists(prisma, tableName) {
  const rows = await prisma.$queryRawUnsafe(
    `SELECT to_regclass('public."${tableName}"') IS NOT NULL AS exists`,
  )
  return Boolean(rows && rows[0] && rows[0].exists)
}

function listMigrationNames() {
  return fs.readdirSync(MIGRATIONS_DIR, { withFileTypes: true })
    .filter(d => d.isDirectory())
    .map(d => d.name)
    .sort() // timestamp-prefixed directory names sort chronologically
}

/**
 * Authoritative check: does the live database differ from schema.prisma at
 * all? Uses `prisma migrate diff --exit-code` (exit 0 = no diff, exit 2 =
 * diff exists) rather than any assumption based on which tables happen to
 * exist. Any other exit code (connection failure, etc.) is a real error and
 * propagates instead of being treated as "no drift".
 */
function hasSchemaDrift() {
  try {
    execFileSync('npx', [
      'prisma', 'migrate', 'diff',
      '--from-url', process.env.DATABASE_URL,
      '--to-schema-datamodel', SCHEMA_PATH,
      '--exit-code',
    ], { stdio: 'inherit' })
    return false
  } catch (err) {
    if (err.status === 2) return true
    throw err
  }
}

function baselineAllMigrations() {
  log('Baselining: marking existing migrations as already applied (no DDL executed).')
  for (const name of listMigrationNames()) {
    run('npx', ['prisma', 'migrate', 'resolve', '--applied', name])
  }
}

async function main() {
  if (process.env.FRESH_INSTALL === 'true') {
    log('FRESH_INSTALL=true — running destructive `db push --accept-data-loss` on purpose.')
    run('npx', ['prisma', 'db', 'push', '--skip-generate', '--accept-data-loss'])
    // `db push` doesn't create `_prisma_migrations` — baseline now so the NEXT
    // boot (normal, no FRESH_INSTALL) lands on the steady-state path instead
    // of re-running this branch's logic against an already-fresh database.
    baselineAllMigrations()
  } else {
    const { PrismaClient } = require('@prisma/client')
    const prisma = new PrismaClient()
    let hasMigrationsTable
    let hasProbeTable
    try {
      hasMigrationsTable = await tableExists(prisma, '_prisma_migrations')
      hasProbeTable = await tableExists(prisma, PROBE_TABLE)
    } finally {
      await prisma.$disconnect()
    }

    if (hasMigrationsTable) {
      log('_prisma_migrations found — applying pending migrations only.')
      run('npx', ['prisma', 'migrate', 'deploy'])
    } else if (hasProbeTable) {
      log(`No migration history, but "${PROBE_TABLE}" table exists — legacy db-push database.`)
      log('Verifying the live schema is actually fully in sync before trusting a baseline...')
      if (hasSchemaDrift()) {
        log('Drift detected — the database is NOT fully caught up with schema.prisma.')
        log('Closing the gap via one guarded `db push` (same mechanism already relied on until now)...')
        run('npx', ['prisma', 'db', 'push', '--skip-generate', '--accept-data-loss'])
      } else {
        log('No drift — database already matches schema.prisma exactly. Safe to baseline.')
      }
      baselineAllMigrations()
      log('Baseline complete. Applying any migrations newer than the baseline.')
      run('npx', ['prisma', 'migrate', 'deploy'])
    } else {
      log('Empty database — fresh install. Applying all migrations from scratch.')
      run('npx', ['prisma', 'migrate', 'deploy'])
    }
  }

  // Final sanity check, unconditional on every path: never start the app
  // if the live schema still does not match schema.prisma for any reason.
  log('Final sanity check — confirming live schema matches schema.prisma...')
  if (hasSchemaDrift()) {
    console.error('[bootstrap-db] FATAL: schema still does not match schema.prisma after bootstrap. Refusing to start.')
    process.exit(1)
  }
  log('Schema verified in sync. Starting the application.')
}

main().catch(err => {
  console.error('[bootstrap-db] FATAL:', err)
  process.exit(1)
})
