/**
 * vitest.config.ts
 * Configuração do Vitest para testes unitários (sem Docker, sem DB real).
 * Para integração com PostgreSQL real, use vitest.integration.config.ts.
 */

import { defineConfig } from "vitest/config"

export default defineConfig({
  test: {
    // Apenas unit tests — integration tests exigem Docker + vitest.integration.config.ts
    include: ["src/__tests__/**/*.test.ts"],
    exclude: ["src/__tests__/integration/**"],

    environment: "node",
    reporters:   ["verbose"],

    env: {
      NODE_ENV:     "test",
      DATABASE_URL: "postgresql://test:test@localhost:5432/test",
    },
  },
})
