/**
 * vitest.integration.config.ts
 * Configuração do Vitest para testes de integração com PostgreSQL real.
 *
 * Requisitos:
 *   - Docker disponível (testcontainers sobe um container PostgreSQL)
 *   - pool: 'forks' — garante que process.env do globalSetup é herdado pelos workers
 *   - testTimeout alto — containers levam alguns segundos para iniciar
 */

import { defineConfig } from "vitest/config"

export default defineConfig({
  test: {
    include: ["src/__tests__/integration/**/*.integration.test.ts"],
    globalSetup: ["src/__tests__/integration/global-setup.ts"],

    // 'forks' garante que process.env.DATABASE_URL do globalSetup é propagado
    pool: "forks",

    // Containers podem demorar para iniciar
    testTimeout:  60_000,
    hookTimeout:  60_000,

    // Rodar arquivos de integração sequencialmente para evitar conflito de tabelas
    sequence: { concurrent: false },

    reporters: ["verbose"],

    env: {
      NODE_ENV: "test",
    },
  },
})
