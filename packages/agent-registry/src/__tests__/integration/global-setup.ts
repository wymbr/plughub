/**
 * integration/global-setup.ts
 * Setup global para testes de integração com PostgreSQL real via testcontainers.
 *
 * Executa UMA VEZ por suite de testes:
 *   1. Sobe container PostgreSQL 16
 *   2. Seta process.env.DATABASE_URL
 *   3. Aplica o schema via prisma db push
 *   4. Retorna teardown que derruba o container
 *
 * Compatível com vitest >= 1.3 (globalSetup com export function setup/teardown).
 * Requer pool: 'forks' no vitest config para que process.env seja herdado pelos workers.
 */

import { execSync }            from "child_process"
import { join }                from "path"
import { PostgreSqlContainer } from "@testcontainers/postgresql"
import type { StartedPostgreSqlContainer } from "@testcontainers/postgresql"

let container: StartedPostgreSqlContainer

export async function setup(): Promise<void> {
  console.log("\n[integration] Iniciando container PostgreSQL...")

  container = await new PostgreSqlContainer("postgres:16-alpine")
    .withDatabase("plughub_test")
    .withUsername("plughub_test")
    .withPassword("plughub_test")
    .start()

  const databaseUrl = container.getConnectionUri()
  process.env["DATABASE_URL"] = databaseUrl

  console.log(`[integration] PostgreSQL pronto: ${databaseUrl}`)

  // Aplica o schema Prisma ao banco de teste
  const pkgRoot = join(__dirname, "..", "..", "..")
  execSync("npx prisma db push --accept-data-loss --skip-generate", {
    cwd:   pkgRoot,
    env:   { ...process.env, DATABASE_URL: databaseUrl },
    stdio: "inherit",
  })

  console.log("[integration] Schema aplicado via prisma db push")
}

export async function teardown(): Promise<void> {
  if (container) {
    console.log("\n[integration] Derrubando container PostgreSQL...")
    await container.stop()
  }
}
