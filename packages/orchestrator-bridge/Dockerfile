# Dockerfile — orchestrator-bridge
# Build context: monorepo root (context: ../..)
FROM python:3.11-slim

WORKDIR /app
COPY packages/orchestrator-bridge  ./packages/orchestrator-bridge
# Skills YAML fallback (used when Agent Registry has no skills registered)
COPY packages/skill-flow-engine/skills ./packages/skill-flow-engine/skills

WORKDIR /app/packages/orchestrator-bridge
RUN pip install --no-cache-dir -e .

CMD ["plughub-bridge"]
