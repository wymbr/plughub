FROM node:20-slim AS builder

WORKDIR /app

# Copy schemas package
COPY packages/schemas ./packages/schemas

# Copy mcp-server-plughub package
COPY packages/mcp-server-plughub ./packages/mcp-server-plughub

# Fix workspace:* reference to use file-based path
RUN sed -i 's|"@plughub/schemas": "workspace:\*"|"@plughub/schemas": "file:../schemas"|g' \
    packages/mcp-server-plughub/package.json

# Build schemas first
WORKDIR /app/packages/schemas
RUN npm install && npm run build

# Build mcp-server-plughub
WORKDIR /app/packages/mcp-server-plughub
RUN npm install && npm run build

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM node:20-slim AS runtime

WORKDIR /app

COPY --from=builder /app/packages/schemas ./packages/schemas
COPY --from=builder /app/packages/mcp-server-plughub ./packages/mcp-server-plughub

WORKDIR /app/packages/mcp-server-plughub

EXPOSE 3100

CMD ["node", "dist/index.js"]
