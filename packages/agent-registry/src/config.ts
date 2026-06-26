/**
 * config.ts
 * Configurações do Agent Registry via variáveis de ambiente.
 */

export const config = {
  port:          parseInt(process.env["PORT"]         ?? "3300", 10),
  host:          process.env["HOST"]                  ?? "0.0.0.0",
  database_url:  process.env["DATABASE_URL"]          ?? "",
  jwt_secret:    process.env["PLUGHUB_JWT_SECRET"]    ?? "",
  // G-PROBE platform-wide: credencial de serviço p/ callers internos (RegistrySyncer);
  // a UI usa Bearer+ABAC config.resources. Ambos vazios = gate no-op (postura atual).
  service_token: process.env["AGENT_REGISTRY_SERVICE_TOKEN"] ?? "",
  node_env:      process.env["NODE_ENV"]              ?? "development",
  kafka_brokers: process.env["KAFKA_BROKERS"]         ?? "localhost:9092",
  kafka_topic_registry: process.env["KAFKA_TOPIC_REGISTRY"] ?? "agent.registry.events",
  // Redis — for operational snapshots written by the Routing Engine
  redis_url: process.env["REDIS_URL"] ?? "redis://localhost:6379",
  // External service URLs for proxy endpoints
  analytics_api_url: process.env["ANALYTICS_API_URL"] ?? "http://localhost:3500",
  workflow_api_url:  process.env["WORKFLOW_API_URL"]   ?? "http://localhost:3800",
  // Config API — item 7a: teto do buffer da fila gratuita (queue_max_total)
  config_api_url:    process.env["CONFIG_API_URL"]     ?? "http://localhost:3600",
} as const
