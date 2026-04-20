/**
 * integration/helpers.ts
 * Utilitários compartilhados entre os testes de integração.
 */

import { PrismaClient } from "@prisma/client"

// ─── Cliente Prisma de teste (criado por arquivo de teste, não singleton) ─────

export function createTestPrisma(): PrismaClient {
  return new PrismaClient({
    datasources: { db: { url: process.env["DATABASE_URL"]! } },
  })
}

// ─── Limpeza entre testes ─────────────────────────────────────────────────────

/** Trunca todas as tabelas do schema em ordem segura (respeita FK). */
export async function truncateAll(prisma: PrismaClient): Promise<void> {
  // Ordem: tabelas dependentes primeiro
  await prisma.$executeRawUnsafe(
    `TRUNCATE TABLE
      agent_instances,
      agent_type_pools,
      agent_types,
      pools,
      skills
    RESTART IDENTITY CASCADE`
  )
}

// ─── Fixtures ─────────────────────────────────────────────────────────────────

export const TENANT    = "tenant_integration_test"
export const USER_ID   = "user_test"
export const HEADERS   = { "x-tenant-id": TENANT, "x-user-id": USER_ID }

export const VALID_POOL = {
  pool_id:       "retencao_humano",
  channel_types: ["chat", "whatsapp", "voice"],
  sla_target_ms: 480000,
}

export const VALID_AGENT_TYPE = {
  agent_type_id:   "agente_retencao_v1",
  framework:       "langgraph",
  execution_model: "stateless",
  pools:           ["retencao_humano"],
}
