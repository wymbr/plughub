# Dockerfile — mcp-server-knowledge
# Build context: monorepo root (context: .)
FROM node:20-slim AS builder

WORKDIR /app

COPY packages/mcp-server-knowledge ./packages/mcp-server-knowledge

WORKDIR /app/packages/mcp-server-knowledge
RUN npm install && npm run build

# ── runtime ──────────────────────────────────────────────────────────────────
FROM node:20-slim

WORKDIR /app/packages/mcp-server-knowledge

COPY --from=builder /app/packages/mcp-server-knowledge/dist ./dist
COPY --from=builder /app/packages/mcp-server-knowledge/package.json ./

RUN npm install --omit=dev

EXPOSE 3401

ENV PORT=3401

CMD ["node", "dist/index.js"]
