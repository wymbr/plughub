import { defineConfig } from "vitest/config"
import { resolve }       from "path"

export default defineConfig({
  resolve: {
    alias: {
      "@plughub/schemas": resolve(__dirname, "../schemas/src/index.ts"),
    },
  },
  test: {
    globals: false,
  },
})
