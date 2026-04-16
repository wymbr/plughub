/**
 * tools/external-agent.ts
 * Tools para agentes externos integrados via MCP.
 * Spec: PlugHub v24.0 seção 4.6k (hybrid proxy model)
 *
 * Grupo: External Agent (4 tools)
 *   invoke, wait_for_assignment, send_message, wait_for_message
 *
 * Modelo de integração:
 *   1. Agente externo conecta ao mcp-server-plughub via SSE
 *   2. agent_login  → obtém session_token com permissions[] no JWT
 *   3. agent_ready  → anuncia disponibilidade nos pools
 *   4. wait_for_assignment → BLPOP em agent:queue:{instance_id} aguarda context_package
 *   5. invoke       → valida permission, chama domain MCP server (sem proxy sidecar)
 *   6. send_message → publica em conversations.outbound → Channel Gateway → cliente
 *   7. wait_for_message → XREADGROUP em session:{session_id}:messages — fan-out nativo
 *                         para múltiplos participantes (modelo de conferência). Cada agente
 *                         tem seu próprio consumer group keyed por instance_id.
 *                         Desconexão sinalizada por item {type: session_closed} no stream.
 *   8. agent_done   → encerra conversa
 *
 * Invariantes:
 *   - Validação de permissão em invoke é local (JWT) — sem rede, ~0.1ms
 *   - Toda tool valida JWT antes de qualquer operação
 *   - wait_for_message usa XREADGROUP BLOCK; responde mcpError("timeout") quando esgotado
 *   - Audit de chamadas de domínio publicado em audit.mcp_calls (Kafka, assíncrono)
 *   - Consumer group por instance_id: idempotente, criado com MKSTREAM na primeira chamada
 */

import { z }            from "zod"
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { Client }         from "@modelcontextprotocol/sdk/client/index.js"
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js"
import type { RedisClient }   from "../infra/redis"
import { keys }               from "../infra/redis"
import type { KafkaProducer } from "../infra/kafka"
import {
  verifySessionTokenSafe,
  InvalidTokenError,
} from "../infra/jwt"

/** Intervalo de heartbeat em segundos — deve ser menor que o instance TTL do routing engine (30s). */
const HEARTBEAT_INTERVAL_S = 15

// ─── Dependências injetadas ───────────────────────────────────────────────────

export interface ExternalAgentDeps {
  redis: RedisClient
  kafka: KafkaProducer
}

// ─── Schemas de input das tools ───────────────────────────────────────────────

const InvokeInputSchema = z.object({
  session_token: z.string().min(1),
  /** Nome do MCP server de domínio — ex: "mcp-server-crm" */
  mcp_server:    z.string().min(1),
  /** Nome da tool no domain server — ex: "customer_get" */
  tool:          z.string().min(1),
  /** Parâmetros da tool de domínio (passados sem modificação) */
  params:        z.record(z.unknown()).default({}),
})

const WaitForAssignmentInputSchema = z.object({
  session_token: z.string().min(1),
  /** Timeout em segundos (1–300). Default: 30. */
  timeout_s:     z.number().int().min(1).max(300).default(30),
})

const SendMessageInputSchema = z.object({
  session_token: z.string().min(1),
  session_id:    z.string().uuid(),
  contact_id:    z.string().min(1),
  text:          z.string().min(1),
  /** Canal de destino. Default: "chat". */
  channel:       z.string().default("chat"),
})

const WaitForMessageInputSchema = z.object({
  session_token: z.string().min(1),
  session_id:    z.string().uuid(),
  /** Timeout em segundos (1–300). Default: 60. */
  timeout_s:     z.number().int().min(1).max(300).default(60),
  /**
   * ID de conferência — opcional. Quando presente, o consumer group é criado
   * com offset 0 (lê desde o início do stream) para não perder mensagens
   * enviadas antes do agente IA entrar na conferência.
   * Ausente ou vazio: consumer group começa no final ($), modelo standard.
   */
  conference_id: z.string().uuid().optional(),
})

// ─── Helpers de resposta ──────────────────────────────────────────────────────

type ToolResult = {
  isError?: true
  content: Array<{ type: "text"; text: string }>
}

function ok(data: unknown): ToolResult {
  return { content: [{ type: "text" as const, text: JSON.stringify(data) }] }
}

function mcpError(code: string, message: string): ToolResult {
  return {
    isError: true,
    content: [{ type: "text" as const, text: JSON.stringify({ error: code, message }) }],
  }
}

function handleCaughtError(e: unknown): ToolResult {
  if (e instanceof z.ZodError) {
    return mcpError(
      "validation_error",
      e.errors.map(x => `${x.path.join(".")}: ${x.message}`).join("; ")
    )
  }
  if (e instanceof InvalidTokenError) {
    return mcpError("invalid_token", e.message)
  }
  return mcpError("internal_error", e instanceof Error ? e.message : String(e))
}

// ─── Pool de clientes de domínio MCP ─────────────────────────────────────────
//
// Mantém uma conexão SSE por domain server para evitar overhead de handshake
// por chamada. Conexões são criadas sob demanda (lazy) e reutilizadas.
// Em produção, substituir por pool com health-check e retry automático.

const _domainClients = new Map<string, Client>()

/**
 * Resolve a URL base de um domain MCP server a partir de variáveis de ambiente.
 * Convenção: MCP_SERVER_{NAME}_URL  onde NAME = mcp_server em UPPER_SNAKE_CASE.
 * Ex: mcp-server-crm → MCP_SERVER_MCP_SERVER_CRM_URL=http://localhost:3301
 */
function _resolveDomainUrl(mcpServer: string): string {
  const envKey = "MCP_SERVER_" + mcpServer.toUpperCase().replace(/[^A-Z0-9]/g, "_") + "_URL"
  const url    = process.env[envKey]
  if (!url) {
    throw new Error(
      `Domain MCP server '${mcpServer}' não configurado. ` +
      `Defina a variável de ambiente ${envKey}=http://<host>:<port>`
    )
  }
  return url
}

/**
 * Retorna cliente MCP conectado ao domain server, criando se necessário.
 * Conexão SSE é reutilizada entre chamadas (connection pool simples).
 */
async function _getDomainClient(mcpServer: string): Promise<Client> {
  const existing = _domainClients.get(mcpServer)
  if (existing) return existing

  const baseUrl   = _resolveDomainUrl(mcpServer)
  const client    = new Client(
    { name: "mcp-server-plughub", version: "1.0.0" },
    { capabilities: {} }
  )
  const transport = new SSEClientTransport(new URL(`${baseUrl}/sse`))
  await client.connect(transport)
  _domainClients.set(mcpServer, client)
  return client
}

// ─── Registro das tools ───────────────────────────────────────────────────────

export function registerExternalAgentTools(server: McpServer, deps: ExternalAgentDeps): void {
  const { redis, kafka } = deps

  // ── invoke ────────────────────────────────────────────────────────────────
  server.tool(
    "invoke",
    "Chama uma tool de um domain MCP server com validação de permissão JWT. " +
    "A permissão '{mcp_server}:{tool}' deve estar no JWT (emitido por agent_login). " +
    "Validação é local (~0.1ms, sem rede). Audit publicado em audit.mcp_calls. Spec 4.6k.",
    InvokeInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const { session_token, mcp_server, tool, params } = InvokeInputSchema.parse(input)
        const { tenant_id, instance_id, permissions } = verifySessionTokenSafe(session_token)

        // ── Validação de permissão (local, sem rede) ──────────────────────
        const required = `${mcp_server}:${tool}`
        if (!permissions.includes(required)) {
          return mcpError(
            "permission_denied",
            `Agente '${instance_id}' não tem permissão '${required}'. ` +
            `Permissões autorizadas: [${permissions.join(", ")}]`
          )
        }

        // ── Chamada ao domain MCP server ──────────────────────────────────
        const client = await _getDomainClient(mcp_server)
        const result = await client.callTool({ name: tool, arguments: params })

        // ── Audit log assíncrono (não bloqueia resposta) ──────────────────
        kafka.publish("audit.mcp_calls", {
          event_type:  "domain_tool_called",
          tenant_id,
          instance_id,
          mcp_server,
          tool,
          permission:  required,
          timestamp:   new Date().toISOString(),
        }).catch(() => { /* non-fatal — log local */ })

        return ok({ content: result.content })
      } catch (e) {
        return handleCaughtError(e)
      }
    }
  )

  // ── wait_for_assignment ───────────────────────────────────────────────────
  server.tool(
    "wait_for_assignment",
    "Aguarda alocação de conversa pelo Routing Engine. " +
    "Bloqueia em BLPOP na fila agent:queue:{instance_id} até receber context_package ou timeout. " +
    "Envia agent_heartbeat a cada 15s para renovar o TTL de instância no Routing Engine. " +
    "O Routing Engine faz LPUSH nesta chave ao alocar o agente. Spec 4.6k.",
    WaitForAssignmentInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const { session_token, timeout_s } = WaitForAssignmentInputSchema.parse(input)
        const { tenant_id, instance_id }   = verifySessionTokenSafe(session_token)

        const queueKey = `${tenant_id}:agent:queue:${instance_id}`

        // Ler campos da instância para incluir nos heartbeats
        const instanceKey      = keys.agentInstance(tenant_id, instance_id)
        const poolsRaw         = await redis.hget(instanceKey, "pools")
        const pools: string[]  = poolsRaw ? (JSON.parse(poolsRaw) as string[]) : []
        const executionModel   = await redis.hget(instanceKey, "execution_model") ?? "stateless"
        const maxConcurrentRaw = await redis.hget(instanceKey, "max_concurrent_sessions") ?? "1"
        const currentSessRaw   = await redis.hget(instanceKey, "current_sessions") ?? "0"

        // Loop com heartbeats periódicos para manter o TTL do routing engine ativo.
        // O instance TTL do routing engine é 30s — o heartbeat a cada 15s garante
        // que o agente permaneça visível enquanto aguarda um contato.
        const deadline = Date.now() + timeout_s * 1000

        while (true) {
          const remainingMs = deadline - Date.now()
          if (remainingMs <= 0) {
            return mcpError(
              "timeout",
              `Nenhuma conversa alocada em ${timeout_s}s. Chame wait_for_assignment novamente.`
            )
          }

          // BLPOP por no máximo HEARTBEAT_INTERVAL_S ou o tempo restante
          const waitSecs = Math.min(HEARTBEAT_INTERVAL_S, Math.ceil(remainingMs / 1000))
          const result   = await redis.blpop(queueKey, waitSecs)

          if (result) {
            const [, raw] = result
            let context_package: unknown
            try   { context_package = JSON.parse(raw) }
            catch { context_package = { raw } }
            return ok({ context_package })
          }

          // Sem contato — enviar heartbeat para renovar TTL de 30s no routing engine
          kafka.publish("agent.lifecycle", {
            event:                   "agent_heartbeat",
            tenant_id,
            instance_id,
            pools,
            status:                  "ready",
            execution_model:         executionModel,
            max_concurrent_sessions: parseInt(maxConcurrentRaw, 10),
            current_sessions:        parseInt(currentSessRaw, 10),
            timestamp:               new Date().toISOString(),
          }).catch(() => { /* não bloqueia o wait */ })
        }
      } catch (e) {
        return handleCaughtError(e)
      }
    }
  )

  // ── send_message ──────────────────────────────────────────────────────────
  server.tool(
    "send_message",
    "Envia mensagem de texto ao cliente. " +
    "Publica em conversations.outbound → Channel Gateway entrega ao canal do cliente. " +
    "Spec 4.6k.",
    SendMessageInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const { session_token, session_id, contact_id, text, channel } =
          SendMessageInputSchema.parse(input)
        const { tenant_id, instance_id } = verifySessionTokenSafe(session_token)

        const message_id = crypto.randomUUID()

        await kafka.publish("conversations.outbound", {
          type:       "message.text",
          tenant_id,
          contact_id,
          session_id,
          message_id,
          channel,
          direction:  "outbound",
          author:     { type: "agent_ai", id: instance_id },
          content:    { type: "text", text },
          text,
          timestamp:  new Date().toISOString(),
        })

        return ok({ sent: true, message_id })
      } catch (e) {
        return handleCaughtError(e)
      }
    }
  )

  // ── wait_for_message ──────────────────────────────────────────────────────
  //
  // Mecanismo: Redis Streams (XREADGROUP) em vez de BLPOP.
  //
  // Por que Streams em vez de BLPOP:
  //   - Fan-out nativo: o bridge faz um único XADD e TODOS os participantes
  //     da conferência recebem a mensagem de forma independente via consumer groups.
  //   - Sem race condition de mensagem perdida: o stream persiste mesmo antes
  //     do consumer group existir (MKSTREAM); não há janela onde o LPUSH chega
  //     antes do BLPOP e a mensagem é descartada silenciosamente.
  //   - Sem coordenação explícita: o bridge não precisa conhecer quais agentes
  //     estão esperando — faz XADD cego; cada agente consome do seu grupo.
  //   - session_closed: item especial no próprio stream (type=session_closed)
  //     em vez de chave separada; garante que o sinal de desconexão respeita
  //     a mesma ordem de entrega das mensagens normais.
  //
  // Estrutura Redis:
  //   stream:  session:{session_id}:messages         (XADD pelo bridge)
  //   grupo:   agent:{instance_id}                   (um por instância de agente)
  //   offset:  $ (standard) | 0 (conferência — lê histórico desde o join)
  //
  // TTL do stream: 4h (setado pelo bridge no XADD; renovado a cada mensagem).
  // O grupo é deletado automaticamente com a chave quando o TTL expira.
  server.tool(
    "wait_for_message",
    "Aguarda mensagem do cliente via Redis Streams (XREADGROUP). " +
    "Fan-out nativo: múltiplos agentes em conferência recebem a mesma mensagem " +
    "de forma independente sem coordenação. " +
    "Desconexão sinalizada por item {type: session_closed} no stream. " +
    "Spec 4.6k.",
    WaitForMessageInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      try {
        const { session_token, session_id, timeout_s, conference_id } =
          WaitForMessageInputSchema.parse(input)

        const { instance_id } = verifySessionTokenSafe(session_token)

        const streamKey = `session:${session_id}:messages`
        const groupName = `agent:${instance_id}`

        // ── Criar consumer group (idempotente) ───────────────────────────────
        // MKSTREAM cria o stream se não existir.
        // Offset $ = só mensagens novas (padrão).
        // Offset 0 = lê desde o início do stream quando em modo conferência —
        //   garante que o agente IA receba mensagens enviadas pelo cliente
        //   entre o agent_join_conference e o primeiro wait_for_message.
        const startOffset = conference_id ? "0" : "$"
        try {
          await redis.xgroup("CREATE", streamKey, groupName, startOffset, "MKSTREAM")
        } catch (e: unknown) {
          // BUSYGROUP = grupo já existe (chamada repetida / reconexão) — ok, continua
          if (!(e instanceof Error) || !e.message.includes("BUSYGROUP")) {
            throw e
          }
        }

        // ── Bloquear até mensagem ou timeout ─────────────────────────────────
        // XREADGROUP retorna null quando BLOCK expira sem mensagem.
        // Formato de retorno: [[streamKey, [[id, [field, value, ...]]]]]
        const timeoutMs = timeout_s * 1000
        const xResult = await (redis as any).xreadgroup(
          "GROUP", groupName, "consumer1",
          "COUNT",  "1",
          "BLOCK",  String(timeoutMs),
          "STREAMS", streamKey, ">",
        ) as Array<[string, Array<[string, string[]]>]> | null

        if (!xResult || xResult.length === 0) {
          return mcpError(
            "timeout",
            `Nenhuma mensagem recebida em ${timeout_s}s. Considere send_message e aguardar novamente.`
          )
        }

        // ── Parsear entrada do stream ─────────────────────────────────────────
        // xResult[0] = [streamKey, entries]
        // entries[0] = [messageId, fieldsArray]
        // fieldsArray = ["type", "message.text", "text", "...", ...]  (interleaved key-value)
        const streamEntries = xResult[0]?.[1]
        const entry         = streamEntries?.[0]
        if (!entry) {
          return mcpError("timeout", `Stream retornou vazio em ${timeout_s}s.`)
        }

        const messageId = entry[0]!
        const fields    = entry[1]!   // flat array: [key, val, key, val, ...]

        // Converter array interleaved em objeto
        const item: Record<string, string> = {}
        for (let i = 0; i < fields.length - 1; i += 2) {
          item[fields[i]!] = fields[i + 1]!
        }

        // ── ACK — confirma processamento ─────────────────────────────────────
        // Permite que o stream faça GC de itens já processados por todos os grupos.
        redis.xack(streamKey, groupName, messageId).catch(() => { /* non-fatal */ })

        // ── Detectar sinal de sessão encerrada ───────────────────────────────
        if (item["type"] === "session_closed") {
          return mcpError(
            "client_disconnected",
            "Cliente desconectou durante a espera. Chame agent_done para encerrar."
          )
        }

        // ── Mensagem normal do cliente ────────────────────────────────────────
        const raw = item["text"] ?? item["payload"] ?? JSON.stringify(item)
        let message: unknown
        try {
          message = JSON.parse(raw)
        } catch {
          message = raw
        }

        return ok({ message })
      } catch (e) {
        return handleCaughtError(e)
      }
    }
  )
}
