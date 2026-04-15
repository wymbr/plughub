/**
 * ecosystem.config.js — PM2 configuration for PlugHub
 *
 * Usage:
 *   pm2 start ecosystem.config.js          # start all services
 *   pm2 stop all                           # stop all
 *   pm2 restart all                        # restart all
 *   pm2 logs                               # tail all logs
 *   pm2 logs agent-registry                # tail one service
 *   pm2 status                             # show status table
 *   pm2 delete all                         # remove all from PM2 registry
 *
 * First-time setup:
 *   bash scripts/linux/setup.sh
 *
 * Demo seed (run once after setup):
 *   bash scripts/linux/seed-demo.sh
 *
 * If using a Python virtualenv, set PLUGHUB_PYTHON before starting:
 *   PLUGHUB_PYTHON=.venv/bin/python3 pm2 start ecosystem.config.js
 */

"use strict"
const path        = require("path")
const { execSync } = require("child_process")
const ROOT = __dirname

// ─────────────────────────────────────────────────────────────────────────────
// Resolve a command to its absolute path via `which`.
// Falls back to the raw name so the error message from PM2 is still readable.
// Override any command by setting the corresponding env var before pm2 start:
//   export PLUGHUB_PYTHON=.venv/bin/python3
//   export PLUGHUB_UVICORN=.venv/bin/uvicorn
// ─────────────────────────────────────────────────────────────────────────────
function which(cmd) {
  if (!cmd) return cmd
  try {
    return execSync(`which ${cmd} 2>/dev/null`, { encoding: "utf8" }).trim() || cmd
  } catch (_) {
    return cmd
  }
}

const PYTHON  = process.env.PLUGHUB_PYTHON  || which("python3")
const UVICORN = process.env.PLUGHUB_UVICORN || which("uvicorn")

// Python entry-point scripts installed by pip (plughub-routing, etc.)
// which() returns the absolute path so PM2 finds them regardless of cwd.
const CMD = {
  routing:    which("plughub-routing"),
  rules:      which("plughub-rules"),
  channel:    which("plughub-channel-gateway"),
  writer:     which("plughub-conversation-writer"),
  clickhouse: which("plughub-clickhouse-consumer"),
  bridge:     which("plughub-bridge"),
  dashboard:  which("plughub-dashboard-api"),
}

// ─────────────────────────────────────────────────────────────────────────────
// Shared environment — injected into every service.
// Sensitive values read from process.env so they are never committed to git.
// Set them before running pm2:
//   export PLUGHUB_ANTHROPIC_API_KEY="sk-ant-..."
//   export PLUGHUB_JWT_SECRET="your-secret"
// ─────────────────────────────────────────────────────────────────────────────
const ENV = {
  NODE_ENV:                         "production",

  // Infrastructure
  PLUGHUB_KAFKA_BROKERS:            "localhost:9092",
  KAFKA_BROKERS:                    "localhost:9092",
  PLUGHUB_REDIS_URL:                "redis://localhost:6379",
  REDIS_URL:                        "redis://localhost:6379",

  // PostgreSQL
  DATABASE_URL:                     "postgresql://plughub:plughub@localhost:5432/plughub",
  PLUGHUB_POSTGRES_DSN:             "postgresql://plughub:plughub@localhost:5432/plughub",
  POSTGRES_DSN:                     "postgresql://plughub:plughub@localhost:5432/plughub",

  // ClickHouse
  PLUGHUB_CLICKHOUSE_HOST:          "localhost",
  PLUGHUB_CLICKHOUSE_PORT:          "8123",
  PLUGHUB_CLICKHOUSE_DATABASE:      "plughub",
  PLUGHUB_CLICKHOUSE_USER:          "plughub",
  PLUGHUB_CLICKHOUSE_PASSWORD:      "plughub",

  // Secrets (read from environment — never hardcode)
  PLUGHUB_ANTHROPIC_API_KEY:        process.env.PLUGHUB_ANTHROPIC_API_KEY || "",
  PLUGHUB_JWT_SECRET:               process.env.PLUGHUB_JWT_SECRET        || "change-me-for-production",
  JWT_SECRET:                       process.env.PLUGHUB_JWT_SECRET        || "change-me-for-production",

  // Service URLs (used by services that call each other)
  AGENT_REGISTRY_URL:               "http://localhost:3300",
  PLUGHUB_AGENT_REGISTRY_URL:       "http://localhost:3300",
  MCP_SERVER_URL:                   "http://localhost:3100",
  PLUGHUB_MCP_SERVER_URL:           "http://localhost:3100",
  AI_GATEWAY_URL:                   "http://localhost:3200",
  PLUGHUB_ROUTING_ENGINE_URL:       "http://localhost:3200",
  SKILL_REGISTRY_URL:               "http://localhost:3400",
  PLUGHUB_SKILL_FLOW_SERVICE_URL:   "http://localhost:3400",
  SKILL_FLOW_URL:                   "http://localhost:3400",
  MCP_PROXY_URL:                    "http://localhost:7422",
  PLUGHUB_INSTANCE_TTL_SECONDS:     "3600",

  // Orchestrator Bridge — YAML skill flow fallback directory
  SKILLS_DIR:                       path.join(ROOT, "packages/skill-flow-engine/skills"),

  // Channel Gateway — pool_id comes from WebSocket URL path (/ws/chat/{pool_id})
  // PLUGHUB_ENTRY_POINT_POOL_ID is a legacy fallback for single-pool deployments without pool_id in URL
  PLUGHUB_ENTRY_POINT_POOL_ID:      process.env.PLUGHUB_ENTRY_POINT_POOL_ID || "",
  PLUGHUB_TENANT_ID:                process.env.PLUGHUB_TENANT_ID           || "default",
}

// ─────────────────────────────────────────────────────────────────────────────
// Log directory — PM2 will create it if needed
// ─────────────────────────────────────────────────────────────────────────────
const LOG_DIR = path.join(ROOT, "logs")

function logPaths(name) {
  return {
    out_file:   path.join(LOG_DIR, `${name}.out.log`),
    error_file: path.join(LOG_DIR, `${name}.err.log`),
    log_date_format: "YYYY-MM-DD HH:mm:ss.SSS",
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Common options for all services
// ─────────────────────────────────────────────────────────────────────────────
const COMMON = {
  watch:         false,
  autorestart:   true,
  max_restarts:  5,
  min_uptime:    "5s",   // don't count as crash if dies within 5s of start
  restart_delay: 3000,   // wait 3s before restarting after a crash
}

module.exports = {
  apps: [

    // ── 1. Agent Registry ─────────────────────────────────────────────────────
    {
      ...COMMON,
      name:       "agent-registry",
      script:     "dist/index.js",
      cwd:        path.join(ROOT, "packages/agent-registry"),
      ...logPaths("agent-registry"),
      env:        { ...ENV, PORT: "3300" },
    },

    // ── 2. MCP Server ──────────────────────────────────────────────────────────
    {
      ...COMMON,
      name:       "mcp-server",
      script:     "dist/index.js",
      cwd:        path.join(ROOT, "packages/mcp-server-plughub"),
      ...logPaths("mcp-server"),
      env:        { ...ENV, PORT: "3100" },
    },

    // ── 3. AI Gateway ──────────────────────────────────────────────────────────
    {
      ...COMMON,
      name:        "ai-gateway",
      script:      UVICORN,
      args:        "plughub_ai_gateway.main:app --host 0.0.0.0 --port 3200",
      interpreter: "none",
      ...logPaths("ai-gateway"),
      env:         ENV,
    },

    // ── 4. Routing Engine (Kafka consumer — no HTTP port) ─────────────────────
    {
      ...COMMON,
      name:        "routing-engine",
      script:      CMD.routing,
      interpreter: "none",
      ...logPaths("routing-engine"),
      env:         ENV,
    },

    // ── 5a. Rules Engine API ──────────────────────────────────────────────────
    {
      ...COMMON,
      name:        "rules-engine-api",
      script:      UVICORN,
      args:        "plughub_rules.api:app --host 0.0.0.0 --port 3201",
      interpreter: "none",
      ...logPaths("rules-engine-api"),
      env:         ENV,
    },

    // ── 5b. Rules Engine Monitor (Kafka consumer) ─────────────────────────────
    {
      ...COMMON,
      name:        "rules-engine-monitor",
      script:      CMD.rules,
      interpreter: "none",
      ...logPaths("rules-engine-monitor"),
      env:         ENV,
    },

    // ── 6. Channel Gateway ────────────────────────────────────────────────────
    {
      ...COMMON,
      name:        "channel-gateway",
      script:      CMD.channel,
      interpreter: "none",
      ...logPaths("channel-gateway"),
      env:         ENV,
    },

    // ── 7. Conversation Writer (Kafka consumer) ───────────────────────────────
    {
      ...COMMON,
      name:        "conversation-writer",
      script:      CMD.writer,
      interpreter: "none",
      ...logPaths("conversation-writer"),
      env:         ENV,
    },

    // ── 8. Skill Flow Service ─────────────────────────────────────────────────
    {
      ...COMMON,
      name:       "skill-flow-service",
      script:     "dist/index.js",
      cwd:        path.join(ROOT, "packages/e2e-tests/services/skill-flow-service"),
      ...logPaths("skill-flow-service"),
      env:        { ...ENV, PORT: "3400" },
    },

    // ── 9. ClickHouse Consumer (Kafka consumer) ───────────────────────────────
    {
      ...COMMON,
      name:        "clickhouse-consumer",
      script:      CMD.clickhouse,
      interpreter: "none",
      ...logPaths("clickhouse-consumer"),
      env:         ENV,
    },

    // ── 10. Orchestrator Bridge (Kafka consumer) ──────────────────────────────
    {
      ...COMMON,
      name:        "orchestrator-bridge",
      script:      CMD.bridge,
      interpreter: "none",
      ...logPaths("orchestrator-bridge"),
      env:         ENV,
    },

    // ── 11. Dashboard API ─────────────────────────────────────────────────────
    {
      ...COMMON,
      name:        "dashboard-api",
      script:      CMD.dashboard,
      interpreter: "none",
      ...logPaths("dashboard-api"),
      env:         { ...ENV, PLUGHUB_DASHBOARD_API_PORT: "8082" },
    },

    // ── 12. Agent Assist UI (Vite dev server — porta 5173) ────────────────────
    // Para produção, use `npm run build` e sirva os estáticos com nginx.
    {
      ...COMMON,
      name:       "agent-assist-ui",
      script:     "node_modules/.bin/vite",
      args:       "--host 0.0.0.0 --port 5173",
      cwd:        path.join(ROOT, "packages/agent-assist-ui"),
      ...logPaths("agent-assist-ui"),
      env:        { NODE_ENV: "development" },
    },

    // ── 13. Dashboard UI (Vite dev server — porta 5174) ───────────────────────
    {
      ...COMMON,
      name:       "dashboard-ui",
      script:     "node_modules/.bin/vite",
      args:       "--host 0.0.0.0 --port 5174",
      cwd:        path.join(ROOT, "packages/dashboard/ui"),
      ...logPaths("dashboard-ui"),
      env:        { NODE_ENV: "development" },
    },

  ],
}
