/**
 * proxy/config.ts
 * Loads and validates proxy_config.yaml for the sidecar.
 * Spec: PlugHub v24.0 section 4.6k
 */

import * as fs   from "node:fs"
import { parseYaml } from "../certify/yaml"

// ─────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────

export interface CircuitBreakerConfig {
  timeout_ms:       number
  mode_on_failure:  "error_clear"
}

export interface ProxyConfig {
  port:                    number
  session_token_env:       string
  audit_buffer_size:       number
  audit_flush_interval_ms: number
  circuit_breaker:         CircuitBreakerConfig
  /** server name → resolved URL (env vars expanded) */
  routes:                  Record<string, string>
  /**
   * Brokers Kafka para escrita de AuditRecords (tópico mcp.audit).
   * Se omitido, os registros são escritos em stdout (modo MVP / desenvolvimento).
   * Exemplo: ["kafka:9092"]
   */
  kafka_brokers?: string[]
  /** Tópico Kafka para AuditRecords (default: "mcp.audit") */
  audit_topic?:   string
}

// ─────────────────────────────────────────────
// Loader
// ─────────────────────────────────────────────

export function loadProxyConfig(configPath: string): ProxyConfig {
  if (!fs.existsSync(configPath)) {
    throw new Error(`proxy_config.yaml not found: ${configPath}`)
  }

  const raw    = fs.readFileSync(configPath, "utf-8")
  const parsed = parseYaml(raw) as Record<string, unknown>

  const port                  = Number(parsed["port"] ?? 7422)
  const session_token_env     = String(parsed["session_token_env"] ?? "PLUGHUB_SESSION_TOKEN")
  const audit_buffer_size     = Number(parsed["audit_buffer_size"] ?? 1000)
  const audit_flush_interval_ms = Number(parsed["audit_flush_interval_ms"] ?? 500)

  const cb = (parsed["circuit_breaker"] ?? {}) as Record<string, unknown>
  const circuit_breaker: CircuitBreakerConfig = {
    timeout_ms:      Number(cb["timeout_ms"] ?? 50),
    mode_on_failure: "error_clear",
  }

  // Expand env vars in routes: ${MCP_CRM_URL} → process.env.MCP_CRM_URL
  const rawRoutes = (parsed["routes"] ?? {}) as Record<string, unknown>
  const routes: Record<string, string> = {}
  for (const [server, urlTemplate] of Object.entries(rawRoutes)) {
    const resolved = String(urlTemplate).replace(/\$\{([^}]+)\}/g, (_, name: string) => {
      return process.env[name] ?? urlTemplate as string
    })
    routes[server] = resolved
  }

  // kafka_brokers (optional)
  const rawBrokers = parsed["kafka_brokers"]
  let kafka_brokers: string[] | undefined
  if (Array.isArray(rawBrokers) && rawBrokers.length > 0) {
    kafka_brokers = rawBrokers.map(b =>
      String(b).replace(/\$\{([^}]+)\}/g, (_, name: string) => process.env[name] ?? b as string)
    )
  }

  const audit_topic = parsed["audit_topic"] ? String(parsed["audit_topic"]) : undefined

  const config: ProxyConfig = {
    port,
    session_token_env,
    audit_buffer_size,
    audit_flush_interval_ms,
    circuit_breaker,
    routes,
    kafka_brokers,
    audit_topic,
  }

  validateProxyConfig(config)
  return config
}

export function validateProxyConfig(config: ProxyConfig): void {
  if (!config.port || config.port < 1 || config.port > 65535) {
    throw new Error(`proxy_config: invalid port ${config.port}`)
  }
  if (!config.session_token_env) {
    throw new Error("proxy_config: session_token_env is required")
  }
  if (Object.keys(config.routes).length === 0) {
    throw new Error("proxy_config: routes is empty — at least one MCP server route is required")
  }
}
