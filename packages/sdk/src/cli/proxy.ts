/**
 * cli/proxy.ts
 * plughub-sdk proxy --config <path>
 * Starts the proxy sidecar for external agents.
 * Spec: PlugHub v24.0 section 4.6k
 *
 * Usage:
 *   plughub-sdk proxy --config ./output/proxy_config.yaml
 */

import { Command }           from "commander"
import * as path             from "path"
import { loadProxyConfig }   from "../proxy/config"
import { createProxySidecar } from "../proxy/server"

export function registerProxyCommand(program: Command): void {
  program
    .command("proxy")
    .description("Starts the proxy sidecar for external agents (spec 4.6k)")
    .option("--config <path>", "Path to proxy_config.yaml", "./output/proxy_config.yaml")
    .action(async (opts: { config: string }) => {
      const configPath = path.resolve(opts.config)

      // Load config
      let config: ReturnType<typeof loadProxyConfig>
      try {
        config = loadProxyConfig(configPath)
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        console.error(`\n❌ Failed to load proxy config: ${msg}`)
        process.exit(1)
      }

      // Validate session token
      const tokenEnv = config.session_token_env
      const token    = process.env[tokenEnv]
      if (!token) {
        console.error(
          `\n❌ Session token not found.\n` +
          `   Set environment variable: ${tokenEnv}\n` +
          `   (Provided by the Routing Engine at agent startup)`
        )
        process.exit(1)
      }

      // Start sidecar
      const sidecar = createProxySidecar(config, token)

      try {
        await sidecar.start()
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        console.error(`\n❌ Failed to start proxy: ${msg}`)
        process.exit(1)
      }

      // Graceful shutdown
      process.on("SIGTERM", async () => {
        process.stdout.write("[plughub-sdk proxy] SIGTERM — shutting down\n")
        await sidecar.stop()
        process.exit(0)
      })

      process.on("SIGINT", async () => {
        process.stdout.write("[plughub-sdk proxy] SIGINT — shutting down\n")
        await sidecar.stop()
        process.exit(0)
      })

      // Keep running
    })
}
