/**
 * mcp-server-auth/src/index.ts
 *
 * Domain MCP Server — Authentication & PIN validation.
 * Demo stub: lightweight validation logic for E2E testing.
 *
 * Tools:
 *   validate_pin(customer_id, pin)               → { valid, message }
 *   change_pin(customer_id, current_pin, new_pin) → { changed, message }
 *
 * Demo validation rule (PIN):
 *   - Must be exactly 6 digits
 *   - Demo "valid" PIN for any customer: starts with "1" (e.g. "123456")
 *   - All other PINs return valid: false
 *
 * Transport: MCP SSE (same as mcp-server-plughub)
 *   GET  /sse      — open SSE connection
 *   POST /messages — receive MCP JSON-RPC
 *   GET  /health   — liveness probe
 */

import express, { type Request, type Response } from "express"
import { McpServer }         from "@modelcontextprotocol/sdk/server/mcp.js"
import { SSEServerTransport } from "@modelcontextprotocol/sdk/server/sse.js"
import { z } from "zod"

// ── Demo PIN validation logic ─────────────────────────────────────────────────
// In a real auth server this would call an HSM, LDAP, or identity provider.
// For the demo:
//   - valid PINs are exactly 6 digits AND start with "1"  (e.g. "123456", "100000")
//   - special test PIN "999999" always fails (for testing failure path)

function isValidPin(pin: string): boolean {
  if (!/^\d{6}$/.test(pin)) return false
  if (pin === "999999") return false  // force-failure test PIN
  return pin.startsWith("1")
}

// ── Tool registration factory ─────────────────────────────────────────────────
// Called once per McpServer instance (one per SSE connection), matching the
// mcp-server-plughub pattern of creating a fresh server per connection.

function registerTools(server: McpServer): void {
  // ── Tool: validate_pin ──────────────────────────────────────────────────────
  server.tool(
    "validate_pin",
    "Validates a customer's PIN for authentication. " +
    "Returns { valid: boolean, customer_id, message }. " +
    "Called by agente_auth_ia_v1 inside a begin_transaction block — " +
    "the PIN value comes from @masked.* and is never stored.",
    {
      customer_id: z.string().optional().describe("Customer identifier (optional — used for logging only)"),
      pin:         z.string().describe("PIN entered by the customer (sensitive — from masked scope)"),
    },
    async ({ customer_id, pin }) => {
      const valid = isValidPin(pin)
      console.log(`[mcp-server-auth] validate_pin customer=${customer_id ?? "unknown"} valid=${valid}`)
      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            valid,
            customer_id,
            message: valid
              ? "PIN válido — identidade verificada"
              : "PIN inválido. Verifique e tente novamente.",
          }),
        }],
      }
    },
  )

  // ── Tool: change_pin ────────────────────────────────────────────────────────
  server.tool(
    "change_pin",
    "Changes a customer's PIN after verifying the current PIN. " +
    "Returns { changed: boolean, customer_id, message }.",
    {
      customer_id: z.string().optional().describe("Customer identifier (optional — used for logging only)"),
      current_pin: z.string().describe("Current PIN (sensitive — from masked scope)"),
      new_pin:     z.string().describe("New PIN (sensitive — from masked scope)"),
    },
    async ({ customer_id, current_pin, new_pin }) => {
      const currentValid = isValidPin(current_pin)
      const newFormatOk  = /^\d{6}$/.test(new_pin)
      const changed      = currentValid && newFormatOk

      console.log(
        `[mcp-server-auth] change_pin customer=${customer_id ?? "unknown"} ` +
        `current_valid=${currentValid} new_format_ok=${newFormatOk} changed=${changed}`,
      )

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            changed,
            customer_id,
            message: changed
              ? "PIN alterado com sucesso"
              : !currentValid
                ? "PIN atual inválido"
                : "Novo PIN inválido — deve ter exatamente 6 dígitos",
          }),
        }],
      }
    },
  )
}

// ── HTTP + SSE server ─────────────────────────────────────────────────────────

const app = express()

// Global body parser — must be at app level (not per-route) so req.body is
// already populated when handlePostMessage(req, res, req.body) is called.
app.use(express.json())

const transports = new Map<string, SSEServerTransport>()

// Open SSE connection — create a fresh McpServer per connection (same pattern
// as mcp-server-plughub) to avoid "Already connected" errors on reconnect.
app.get("/sse", async (_req: Request, res: Response) => {
  const server    = new McpServer({ name: "mcp-server-auth", version: "1.0.0" })
  registerTools(server)

  const transport = new SSEServerTransport("/messages", res)
  transports.set(transport.sessionId, transport)
  console.log(`[mcp-server-auth] SSE session opened: ${transport.sessionId}`)

  res.on("close", () => {
    transports.delete(transport.sessionId)
    console.log(`[mcp-server-auth] SSE session closed: ${transport.sessionId}`)
  })

  await server.connect(transport)
})

// Receive MCP JSON-RPC — req.body already parsed by the global middleware above.
app.post("/messages", async (req: Request, res: Response) => {
  const sessionId = req.query["sessionId"] as string
  const transport = transports.get(sessionId)
  if (!transport) {
    res.status(400).json({ error: "Session not found", sessionId })
    return
  }
  await transport.handlePostMessage(req, res, req.body)
})

// Health probe
app.get("/health", (_req: Request, res: Response) => {
  res.json({ status: "ok", service: "mcp-server-auth", sessions: transports.size })
})

// ── Start ─────────────────────────────────────────────────────────────────────

const PORT = parseInt(process.env["PORT"] ?? "3150", 10)

app.listen(PORT, () => {
  console.log(`[mcp-server-auth] Listening on port ${PORT}`)
  console.log(`[mcp-server-auth] Tools: validate_pin, change_pin`)
  console.log(`[mcp-server-auth] Demo rule: valid PINs are 6 digits starting with "1"`)
})
