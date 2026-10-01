/**
 * a2a-card.ts — o AgentCard A2A é PROJEÇÃO do pool, nunca documento editável
 * (AAS-03; adr-a2a-server-binding D2, D14.3).
 *
 * Um card descreve UM agente (spec A2A v1.0, `a2a.proto`: `AgentCard` tem um nome, uma
 * descrição, skills e interfaces) — logo é um card por POOL, endereçado por um
 * `ChannelEndpoint` de canal `a2a` (`{base}/a2a/{slug}`), como o slug do webchat e o
 * número da voz. Decisão do dono, 2026-10-01: a frase do ADR que pedia UM
 * `/.well-known/agent-card.json` listando vários pools não tem forma no protocolo (nada
 * na mensagem diz para qual pool ela vai).
 *
 * A função é PURA e mora aqui para que quem monta (agent-registry) e quem testa leiam a
 * mesma regra. Os nomes seguem o mapeamento JSON do proto (camelCase; `oneof` como chave
 * do campo; `StringList` como `{ list: [] }`).
 */
import { z } from "zod"

import type { PoolA2ADescriptor } from "./agent-registry"

/** Versão do protocolo anunciada na interface. */
export const A2A_PROTOCOL_VERSION = "1.0"

/**
 * Extensão PlugHub que leva o contrato de dados do pool. O `AgentCard` da v1.0 não tem
 * campo para JSON Schema de entrada e saída, e o descritor exige os dois (D3): sem esta
 * extensão o chamador veria "o que o agente faz" e não "o que ele precisa receber".
 * Extensão é o mecanismo do próprio protocolo para isso (`AgentCapabilities.extensions`).
 */
export const A2A_IO_SCHEMA_EXTENSION_URI = "urn:plughub:a2a:extension:io-schema:v1"

/**
 * Modos de mídia do card — DERIVADOS, nunca declarados (D14.3). Hoje todo pool fala
 * `text/plain` e `application/json`: a v1 do adapter é `text` + `data` (D14.1), arquivo só
 * entra depois da AAS-12, e o ai-gateway só consome texto. Quando arquivo entrar, a
 * derivação passa a olhar o pool (humano + Console que renderiza anexo) — aqui, não no card.
 */
export function a2aMediaModes(_pool: { agent_kind?: string | null }): string[] {
  return ["text/plain", "application/json"]
}

const StringListSchema = z.object({ list: z.array(z.string()) })

export const AgentCardSchema = z.object({
  name:        z.string().min(1),
  description: z.string().min(1),
  supportedInterfaces: z.array(z.object({
    url:             z.string().url(),
    protocolBinding: z.literal("JSONRPC"),
    protocolVersion: z.string(),
  })).min(1),
  version:      z.string().min(1),
  capabilities: z.object({
    streaming:         z.boolean(),
    pushNotifications: z.boolean(),
    extendedAgentCard: z.boolean(),
    extensions: z.array(z.object({
      uri:         z.string(),
      description: z.string(),
      required:    z.boolean(),
      params:      z.record(z.unknown()),
    })),
  }),
  securitySchemes: z.record(z.object({
    httpAuthSecurityScheme: z.object({ scheme: z.string(), description: z.string() }),
  })),
  securityRequirements: z.array(z.object({ schemes: z.record(StringListSchema) })).min(1),
  defaultInputModes:  z.array(z.string()).min(1),
  defaultOutputModes: z.array(z.string()).min(1),
  skills: z.array(z.object({
    id:          z.string(),
    name:        z.string(),
    description: z.string(),
    tags:        z.array(z.string()),
    examples:    z.array(z.string()),
  })).min(1),
}).strict()
export type AgentCard = z.infer<typeof AgentCardSchema>

/** Texto do esquema por tipo de principal (D6). Os dois são bearer no v1. */
const PRINCIPAL_SCHEME_DESCRIPTION: Record<"partner" | "customer_agent", string> = {
  partner:        "Credencial de parceiro emitida pelo administrador do tenant (Authorization: Bearer).",
  customer_agent: "Token pessoal emitido ao próprio cliente depois de prova de identidade (Authorization: Bearer).",
}

export interface AgentCardInput {
  /** URL pública do endereço do pool, já com o slug: `{base}/a2a/{slug}`. */
  interfaceUrl: string
  /** `set_at` do slot `current` — a identidade de versão do deploy (D2). */
  deployedAt:   string
  pool:         { agent_kind?: string | null }
  descriptor:   PoolA2ADescriptor
}

/**
 * Monta o card. Não decide SE o card existe (endpoint ativo, pool de contato, canal,
 * descoberta, deploy): isso é de quem chama, que precisa dizer o motivo da recusa.
 *
 * `extendedAgentCard: false` até o principal existir (AAS-04): o card estendido é o que um
 * principal autenticado vê, e anunciá-lo antes seria prometer uma rota que não responde.
 * `streaming: true` desde a AAS-07 (`SendStreamingMessage`/`SubscribeToTask` no adapter do
 * gateway). `pushNotifications` segue falso pela mesma razão do estendido: fora de escopo.
 */
export function projectAgentCard(input: AgentCardInput): AgentCard {
  const { descriptor: d } = input
  const modes = a2aMediaModes(input.pool)
  const kinds = [...d.principal_kinds]
  return {
    name:        d.display_name,
    description: d.description,
    supportedInterfaces: [{
      url:             input.interfaceUrl,
      protocolBinding: "JSONRPC",
      protocolVersion: A2A_PROTOCOL_VERSION,
    }],
    version: input.deployedAt,
    capabilities: {
      streaming:         true,
      pushNotifications: false,
      extendedAgentCard: false,
      extensions: [{
        uri:         A2A_IO_SCHEMA_EXTENSION_URI,
        description: "JSON Schema do que o agente precisa receber (input_schema) e do resultado que devolve (output_schema).",
        required:    false,
        params:      { input_schema: d.input_schema, output_schema: d.output_schema },
      }],
    },
    securitySchemes: Object.fromEntries(kinds.map(k => [k, {
      httpAuthSecurityScheme: { scheme: "Bearer", description: PRINCIPAL_SCHEME_DESCRIPTION[k] },
    }])),
    // uma exigência por tipo: QUALQUER um dos esquemas satisfaz (OR entre os itens)
    securityRequirements: kinds.map(k => ({ schemes: { [k]: { list: [] } } })),
    defaultInputModes:  modes,
    defaultOutputModes: modes,
    skills: d.skills.map(s => ({
      id:          s.id,
      name:        s.name,
      description: s.description,
      tags:        [...(s.tags ?? [])],
      examples:    [...(s.examples ?? [])],
    })),
  }
}

/**
 * Por que um endereço `a2a` NÃO tem card público. Uma resposta por motivo, para que o log
 * de quem recusa diga o que consertar — e o 404 público não diga nada (sem oráculo).
 */
export const A2ACardRefusalSchema = z.enum([
  "endpoint_not_found",     // não há ChannelEndpoint a2a com esse identificador
  "endpoint_inactive",      // há, desligado
  "endpoint_not_external",  // procedência que não é de porta pública
  "pool_not_found",         // o endpoint aponta para pool que não existe
  "pool_inactive",
  "pool_not_contact",       // o espelho `-int` herda o canal mas não tem contrato (AAS-01)
  "channel_absent",         // o pool deixou de declarar `a2a`
  "descriptor_absent",
  "not_discoverable",       // contrato existe, mas o card público não o lista (D2)
  "no_current_deploy",      // pool sem `current` não roda: o card mentiria
])
export type A2ACardRefusal = z.infer<typeof A2ACardRefusalSchema>
