#!/usr/bin/env bash
# set-env.sh — PlugHub environment variables for Ubuntu / PM2
#
# Usage (edit the values below, then):
#   source scripts/linux/set-env.sh
#
# Or export before starting PM2 (PM2 inherits the shell environment):
#   source scripts/linux/set-env.sh && pm2 start ecosystem.config.js
#
# DO NOT commit this file with real credentials.
# Add scripts/linux/set-env.local.sh to .gitignore for a local override.

# ── Infrastructure ────────────────────────────────────────────────────────────
export PLUGHUB_KAFKA_BROKERS="localhost:9092"
export KAFKA_BROKERS="localhost:9092"
export PLUGHUB_REDIS_URL="redis://localhost:6379"
export REDIS_URL="redis://localhost:6379"

# ── PostgreSQL ─────────────────────────────────────────────────────────────────
export DATABASE_URL="postgresql://plughub:plughub@localhost:5432/plughub"
export PLUGHUB_POSTGRES_DSN="postgresql://plughub:plughub@localhost:5432/plughub"
export POSTGRES_DSN="postgresql://plughub:plughub@localhost:5432/plughub"

# ── ClickHouse ────────────────────────────────────────────────────────────────
export PLUGHUB_CLICKHOUSE_HOST="localhost"
export PLUGHUB_CLICKHOUSE_PORT="8123"
export PLUGHUB_CLICKHOUSE_DATABASE="plughub"
export PLUGHUB_CLICKHOUSE_USER="plughub"
export PLUGHUB_CLICKHOUSE_PASSWORD="plughub"

# ── Secrets ───────────────────────────────────────────────────────────────────
# IMPORTANT: replace with your real Anthropic API key before running the demo
export PLUGHUB_ANTHROPIC_API_KEY="sk-ant-SUA_CHAVE_AQUI"
export PLUGHUB_JWT_SECRET="change-me-for-production"
export JWT_SECRET="change-me-for-production"

# ── Service URLs ──────────────────────────────────────────────────────────────
export AGENT_REGISTRY_URL="http://localhost:3300"
export PLUGHUB_AGENT_REGISTRY_URL="http://localhost:3300"
export MCP_SERVER_URL="http://localhost:3100"
export PLUGHUB_MCP_SERVER_URL="http://localhost:3100"
export AI_GATEWAY_URL="http://localhost:3200"
export PLUGHUB_ROUTING_ENGINE_URL="http://localhost:3200"
export SKILL_REGISTRY_URL="http://localhost:3400"
export PLUGHUB_SKILL_FLOW_SERVICE_URL="http://localhost:3400"
export SKILL_FLOW_URL="http://localhost:3400"
export MCP_PROXY_URL="http://localhost:7422"
export PLUGHUB_INSTANCE_TTL_SECONDS="3600"

# ── Channel Gateway ───────────────────────────────────────────────────────────
# demo_ia  = Demo 2 (LLM flow with sentiment)
# sac_ia   = Demo 1 (rule-based IVR flow)
export PLUGHUB_ENTRY_POINT_POOL_ID="demo_ia"
export PLUGHUB_TENANT_ID="default"

# ── Python (optional) ─────────────────────────────────────────────────────────
# Uncomment if using a virtualenv:
# export PLUGHUB_PYTHON=".venv/bin/python3"
# export PLUGHUB_UVICORN=".venv/bin/uvicorn"

echo "✅ PlugHub environment variables loaded."
echo "   PLUGHUB_ANTHROPIC_API_KEY = ${PLUGHUB_ANTHROPIC_API_KEY:0:12}..."
echo "   PLUGHUB_ENTRY_POINT_POOL_ID = $PLUGHUB_ENTRY_POINT_POOL_ID"
