# Dockerfile — skill-flow-worker
# Build context: monorepo root (context: ../..)
FROM node:20-slim AS builder

WORKDIR /app

# Copy local dependencies first
COPY packages/schemas         ./packages/schemas
COPY packages/skill-flow-engine ./packages/skill-flow-engine
COPY packages/skill-flow-worker ./packages/skill-flow-worker

# Fix workspace:* references
RUN sed -i 's|"workspace:\*"|"file:../schemas"|g' \
        packages/skill-flow-engine/package.json && \
    sed -i 's|"@plughub/schemas": "file:../schemas"|"@plughub/schemas": "file:../schemas"|g' \
        packages/skill-flow-worker/package.json

# Build schemas
WORKDIR /app/packages/schemas
RUN npm install && npm run build

# Build skill-flow-engine
WORKDIR /app/packages/skill-flow-engine
RUN npm install && npm run build

# Build skill-flow-worker
WORKDIR /app/packages/skill-flow-worker
RUN npm install && npm run build

# ── Runtime ───────────────────────────────────────────────────────────────────
FROM node:20-slim AS runtime

WORKDIR /app
COPY --from=builder /app/packages/schemas         ./packages/schemas
COPY --from=builder /app/packages/skill-flow-engine ./packages/skill-flow-engine
COPY --from=builder /app/packages/skill-flow-worker ./packages/skill-flow-worker

WORKDIR /app/packages/skill-flow-worker
CMD ["node", "dist/index.js"]
