/**
 * index.ts
 * Entry point do Agent Registry.
 */

import { app }    from "./app"
import { config } from "./config"
import { prisma } from "./db"

async function main() {
  // Verificar conexão com o banco
  await prisma.$connect()
  console.log("✅ PostgreSQL conectado")

  app.listen(config.port, config.host, () => {
    console.log(`✅ agent-registry iniciado em http://${config.host}:${config.port}`)
    console.log(`   Rotas: /v1/pools  /v1/agent-types  /v1/skills`)
  })
}

main().catch((err) => {
  console.error("❌ Falha ao iniciar agent-registry:", err)
  process.exit(1)
})
