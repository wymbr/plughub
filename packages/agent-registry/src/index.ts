/**
 * index.ts
 * Entry point do Agent Registry.
 */

import { app }             from "./app"
import { config }          from "./config"
import { prisma }          from "./db"
import { disconnectKafka } from "./infra/kafka"

async function main() {
  // Verificar conexão com o banco
  await prisma.$connect()
  console.log("✅ PostgreSQL conectado")

  // Capacity-governance item 2: backfill de agent_kind (uma vez por pool).
  // Inferência: pool com deploy slot `current` ⇒ "ai"; senão ⇒ "human".
  // Daí em diante a declaração é explícita (YAML/UI); o backfill não re-toca.
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const untyped = await prisma.pool.findMany({
      where: { agent_kind: null } as never,
      select: { id: true, pool_id: true, tenant_id: true },
    }) as Array<{ id: string; pool_id: string; tenant_id: string }>
    if (untyped.length > 0) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const slots = await (prisma as any).poolSkillSlot.findMany({
        where: { slot: "current" },
        select: { pool_id: true, tenant_id: true, skill_id: true },
      }) as Array<{ pool_id: string; tenant_id: string; skill_id: string | null }>
      const deployed = new Set(
        slots.filter(s => !!s.skill_id).map(s => `${s.tenant_id}:${s.pool_id}`),
      )
      for (const p of untyped) {
        const kind = deployed.has(`${p.tenant_id}:${p.pool_id}`) ? "ai" : "human"
        await prisma.pool.update({ where: { id: p.id }, data: { agent_kind: kind } as never })
        console.log(`   agent_kind backfill: ${p.tenant_id}/${p.pool_id} → ${kind}`)
      }
      console.log(`✅ agent_kind backfill: ${untyped.length} pool(s)`)
    }
  } catch (err) {
    console.warn("⚠️ agent_kind backfill falhou (segue sem bloquear):", err)
  }

  // Kafka producer conecta lazy (na primeira publicação) — não bloqueia startup
  console.log(`   Kafka brokers: ${config.kafka_brokers} → tópico: ${config.kafka_topic_registry}`)

  app.listen(config.port, config.host, () => {
    console.log(`✅ agent-registry iniciado em http://${config.host}:${config.port}`)
    console.log(`   Rotas: /v1/pools  /v1/skills  /v1/instances  /v1/operational`)
  })
}

// Graceful shutdown
process.on("SIGTERM", async () => {
  await disconnectKafka()
  await prisma.$disconnect()
  process.exit(0)
})
process.on("SIGINT", async () => {
  await disconnectKafka()
  await prisma.$disconnect()
  process.exit(0)
})

main().catch((err) => {
  console.error("❌ Falha ao iniciar agent-registry:", err)
  process.exit(1)
})
