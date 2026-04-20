# packages/e2e-tests/Dockerfile
# E2E test runner container — executa os cenários e encerra.
# Build context: repo root (context: .)
# Usage: via docker-compose.demo.yml (service: e2e-runner)

FROM node:20-alpine AS base

WORKDIR /app

# Install ts-node globally for running TypeScript scenarios directly
RUN npm install -g ts-node typescript

# Copy workspace root manifests
COPY package*.json ./

# Copy shared packages (schemas + sdk) needed by e2e-tests
COPY packages/schemas/package*.json packages/schemas/
COPY packages/schemas/src            packages/schemas/src
COPY packages/schemas/tsconfig.json  packages/schemas/tsconfig.json

COPY packages/sdk/package*.json packages/sdk/
COPY packages/sdk/src            packages/sdk/src
COPY packages/sdk/tsconfig.json  packages/sdk/tsconfig.json

# Copy e2e-tests
COPY packages/e2e-tests/package*.json packages/e2e-tests/
COPY packages/e2e-tests/lib           packages/e2e-tests/lib
COPY packages/e2e-tests/scenarios     packages/e2e-tests/scenarios
COPY packages/e2e-tests/fixtures      packages/e2e-tests/fixtures
COPY packages/e2e-tests/services      packages/e2e-tests/services
COPY packages/e2e-tests/runner.ts     packages/e2e-tests/runner.ts
COPY packages/e2e-tests/tsconfig.json packages/e2e-tests/tsconfig.json

# Install dependencies for all packages
RUN cd packages/schemas   && npm install --ignore-scripts
RUN cd packages/sdk       && npm install --ignore-scripts
RUN cd packages/e2e-tests && npm install --ignore-scripts

ENV NODE_OPTIONS="--max-old-space-size=4096"

WORKDIR /app/packages/e2e-tests

# Default: run all demo scenarios.
# Override via E2E_EXTRA_ARGS env var in docker-compose.
CMD ["sh", "-c", "ts-node --project tsconfig.json runner.ts ${E2E_EXTRA_ARGS:---demo}"]
