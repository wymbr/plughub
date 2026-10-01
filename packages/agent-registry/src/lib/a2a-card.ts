/**
 * lib/a2a-card.ts — monta o AgentCard de um endereço `a2a` (AAS-03; adr-a2a-server-binding D2).
 *
 * O card é PROJEÇÃO: endpoint `a2a` → pool → slot `current` → descritor. Nada aqui é
 * gravado, e nada no card é editável fora da tela do pool (one-source). A forma do card mora
 * em `@plughub/schemas` (`projectAgentCard`); aqui fica só a DECISÃO de servir ou não — e,
 * quando não, o MOTIVO, que o gateway loga e não publica (o 404 público é mudo, sem oráculo
 * que distinga "não existe" de "existe e não é descobrível").
 */
import { PoolA2ADescriptorSchema, projectAgentCard } from "@plughub/schemas"
import type { A2ACardRefusal, AgentCard } from "@plughub/schemas"

import { prisma } from "../db"
import type { ChannelEndpointDelegate } from "../types/channel-endpoint"

const channelEndpoint = (prisma as unknown as { channelEndpoint: ChannelEndpointDelegate }).channelEndpoint

/** O slug vai na URL pública: só minúsculas, dígitos, `_` e `-`, sem começar por separador. */
export const A2A_SLUG_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/

/**
 * AAS-09 — segmentos de `/a2a/…` que são da PLATAFORMA, não de um pool: o gateway serve a
 * retirada do token do cliente em `/a2a/customer-token/{código}`. Um endereço com esse slug
 * faria a mesma URL significar duas coisas.
 */
export const A2A_RESERVED_SLUGS: ReadonlySet<string> = new Set(["customer-token"])

export type A2ACardResult =
  | { card: AgentCard; pool_id: string }
  | { refusal: A2ACardRefusal; pool_id?: string; detail?: string }

export async function resolveA2ACard(
  tenantId:   string,
  identifier: string,
  baseUrl:    string,
): Promise<A2ACardResult> {
  const ep = await channelEndpoint.findFirst({
    where: { tenant_id: tenantId, channel: "a2a", identifier },
  })
  if (!ep) return { refusal: "endpoint_not_found" }
  if (!ep.active) return { refusal: "endpoint_inactive", pool_id: ep.pool_id }
  // Linha `internal`/`legacy_token` não é de porta pública (ADR do registro único, §7.6.3).
  if ((ep.origin ?? "external") !== "external") return { refusal: "endpoint_not_external", pool_id: ep.pool_id }

  const pool = await prisma.pool.findUnique({
    where: { pool_id_tenant_id: { pool_id: ep.pool_id, tenant_id: tenantId } },
  }) as unknown as Record<string, unknown> | null
  if (!pool) return { refusal: "pool_not_found", pool_id: ep.pool_id }
  if (pool["status"] !== "active") return { refusal: "pool_inactive", pool_id: ep.pool_id }
  if ((pool["purpose"] ?? "contact") !== "contact") return { refusal: "pool_not_contact", pool_id: ep.pool_id }
  if (!((pool["channel_types"] ?? []) as string[]).includes("a2a")) {
    return { refusal: "channel_absent", pool_id: ep.pool_id }
  }

  // O registry só grava descritor validado, mas linha antiga ou editada à mão no banco não
  // passou pelo portão: card de contrato inválido seria o card mentindo. Recusa e diz.
  if (pool["a2a"] == null) return { refusal: "descriptor_absent", pool_id: ep.pool_id }
  const parsed = PoolA2ADescriptorSchema.safeParse(pool["a2a"])
  if (!parsed.success) {
    return { refusal: "descriptor_absent", pool_id: ep.pool_id, detail: `descritor inválido: ${parsed.error.issues[0]?.message ?? "?"}` }
  }
  if (!parsed.data.discoverable) return { refusal: "not_discoverable", pool_id: ep.pool_id }

  const current = await (prisma as any).poolSkillSlot.findUnique({
    where: { pool_id_tenant_id_slot: { pool_id: ep.pool_id, tenant_id: tenantId, slot: "current" } },
  }) as Record<string, unknown> | null
  const setAt = current?.["set_at"]
  if (!current || !current["skill_id"] || !setAt) return { refusal: "no_current_deploy", pool_id: ep.pool_id }

  const card = projectAgentCard({
    // Barra final (AAS-08): o endereço do agente é também a BASE da descoberta, e cliente que
    // resolve `.well-known/agent-card.json` por RFC 3986 (o SDK JS) perde o slug sem ela.
    interfaceUrl: `${baseUrl.replace(/\/+$/, "")}/a2a/${identifier}/`,
    deployedAt:   setAt instanceof Date ? setAt.toISOString() : String(setAt),
    pool:         { agent_kind: pool["agent_kind"] as string | null },
    descriptor:   parsed.data,
  })
  return { card, pool_id: ep.pool_id }
}
