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
  // Credencial de SERVIÇO para falar com a analytics-api. O `handoff-status` conta
  // sessões vivas antes de um deploy e chamava ANÔNIMO; desde que a analytics fechou
  // credencial (2026-08-29) o `catch {}` engolia o 401 e devolvia `active_sessions: 0`
  // — um deploy com sessões em curso passava a parecer seguro. Tem de casar com
  // `ANALYTICS_SERVICE_TOKEN` do outro lado.
  analytics_service_token: process.env["ANALYTICS_SERVICE_TOKEN"] ?? "",
  workflow_api_url:  process.env["WORKFLOW_API_URL"]   ?? "http://localhost:3800",
  // Config API — item 7a: teto do buffer da fila gratuita (queue_max_total)
  config_api_url:    process.env["CONFIG_API_URL"]     ?? "http://localhost:3600",
  // Routing Engine — F4b: rollup de capacidade ESCOPADO ao domínio de pools do
  // usuário. Só o engine sabe deduplicar por instância distinta, e a dedução não
  // projeta sobre um subconjunto depois de agregada — daí a chamada em vez de
  // recomputar aqui (que seria a segunda implementação da mesma regra).
  routing_engine_url:   process.env["ROUTING_ENGINE_URL"]   ?? "http://localhost:3550",
  routing_admin_token:  process.env["ROUTING_ADMIN_TOKEN"]  ?? "",
} as const
