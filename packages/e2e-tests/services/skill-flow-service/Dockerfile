FROM node:20-slim AS builder

WORKDIR /app

# Copy schemas package
COPY packages/schemas ./packages/schemas

# Copy skill-flow-engine package (note: intentional typo in folder name)
COPY packages/skill-flow-engine ./packages/skill-flow-engine

# Copy the skill-flow-service
COPY packages/e2e-tests/services/skill-flow-service ./packages/e2e-tests/services/skill-flow-service

# Fix workspace:* reference for schemas in skill-flow-service
RUN sed -i 's|"@plughub/schemas": "workspace:\*"|"@plughub/schemas": "file:../../../schemas"|g' \
    packages/e2e-tests/services/skill-flow-service/package.json

# Fix workspace:* reference for schemas in skill-flow-engine
RUN sed -i 's|"@plughub/schemas": "workspace:\*"|"@plughub/schemas": "file:../schemas"|g' \
    packages/skill-flow-engine/package.json

# Build schemas
WORKDIR /app/packages/schemas
RUN npm install && npm run build

# Build skill-flow-engine
WORKDIR /app/packages/skill-flow-engine
RUN npm install && NODE_OPTIONS="--max-old-space-size=4096" npm run build

# Build skill-flow-service
# Add @plughub/skill-flow-engine as a local dependency via package.json
WORKDIR /app/packages/e2e-tests/services/skill-flow-service
RUN node -e " \
  const fs = require('fs'); \
  const pkg = JSON.parse(fs.readFileSync('package.json', 'utf8')); \
  pkg.dependencies['@plughub/skill-flow-engine'] = 'file:../../../skill-flow-engine'; \
  fs.writeFileSync('package.json', JSON.stringify(pkg, null, 2)); \
"
RUN npm install && NODE_OPTIONS="--max-old-space-size=4096" npm run build

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM node:20-slim AS runtime

# wget: required by Docker healthcheck (node:20-slim ships without it)
RUN apt-get update && apt-get install -y wget && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/packages/schemas ./packages/schemas
COPY --from=builder /app/packages/skill-flow-engine ./packages/skill-flow-engine
COPY --from=builder /app/packages/e2e-tests/services/skill-flow-service ./packages/e2e-tests/services/skill-flow-service

WORKDIR /app/packages/e2e-tests/services/skill-flow-service

EXPOSE 3400

CMD ["node", "dist/index.js"]
