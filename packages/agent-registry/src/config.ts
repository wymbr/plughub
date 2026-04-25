/**
 * config.ts
 * Configurações do Agent Registry via variáveis de ambiente.
 */

export const config = {
  port:          parseInt(process.env["PORT"]         ?? "3300", 10),
  host:          process.env["HOST"]                  ?? "0.0.0.0",
  database_url:  process.env["DATABASE_URL"]          ?? "",
  jwt_secret:    process.env["PLUGHUB_JWT_SECRET"]    ?? "",
  node_env:      process.env["NODE_ENV"]              ?? "development",
  kafka_brokers: process.env["KAFKA_BROKERS"]         ?? "localhost:9092",
  kafka_topic_registry: process.env["KAFKA_TOPIC_REGISTRY"] ?? "agent.registry.events",
} as const
