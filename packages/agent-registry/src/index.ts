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

  // Kafka producer conecta lazy (na primeira publicação) — não bloqueia startup
  console.log(`   Kafka brokers: ${config.kafka_brokers} → tópico: ${config.kafka_topic_registry}`)

  app.listen(config.port, config.host, () => {
    console.log(`✅ agent-registry iniciado em http://${config.host}:${config.port}`)
    console.log(`   Rotas: /v1/pools  /v1/agent-types  /v1/skills`)
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
