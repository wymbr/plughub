FROM node:20-slim AS builder

# Install openssl for Prisma
RUN apt-get update && apt-get install -y openssl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy schemas package
COPY packages/schemas ./packages/schemas

# Copy agent-registry package
COPY packages/agent-registry ./packages/agent-registry

# Fix workspace:* reference to use file-based path
RUN sed -i 's|"@plughub/schemas": "workspace:\*"|"@plughub/schemas": "file:../schemas"|g' \
    packages/agent-registry/package.json

# Build schemas first
WORKDIR /app/packages/schemas
RUN npm install && npm run build

# Build agent-registry
WORKDIR /app/packages/agent-registry
RUN npm install && npm run build && npx prisma generate

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM node:20-slim AS runtime

RUN apt-get update && apt-get install -y openssl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/packages/schemas ./packages/schemas
COPY --from=builder /app/packages/agent-registry ./packages/agent-registry

WORKDIR /app/packages/agent-registry

EXPOSE 3300

CMD ["sh", "-c", "npx prisma db push --skip-generate && node dist/index.js"]
