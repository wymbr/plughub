/**
 * journey-merges.ts
 * Journey J3 — aresta de merge de journey.
 *
 * Topic: journey.merges — 1 tipo só (`journey_merged`). NÃO é o `journey.events`
 * de 9 tipos removido no Arc 19 Fase F.
 * Produtor: mcp-server-plughub (tool `journey_merge`).
 * Consumidor: analytics-api → tabela ClickHouse `journey_aliases`.
 *
 * Invariante: liga a raiz mais NOVA (source_root, absorvida) à mais ANTIGA
 * (canonical_root, sobrevivente). Ordem novo→antigo garante floresta sem ciclo.
 * `root_session_id` das sessões NUNCA é reescrito — o merge só grava a aresta.
 */
import { z } from "zod"

export const JourneyMergedEventSchema = z.object({
  event_id:       z.string().uuid(),
  tenant_id:      z.string().min(1),
  /** Raiz da journey mais NOVA (absorvida). */
  source_root:    z.string().min(1),
  /** Raiz da journey mais ANTIGA (sobrevivente / canônica). */
  canonical_root: z.string().min(1),
  /** ISO-8601 UTC. */
  merged_at:      z.string().min(1),
  /** Quem comandou o merge (skill_id / instance_id / participant). */
  actor:          z.string().default(""),
})
export type JourneyMergedEvent = z.infer<typeof JourneyMergedEventSchema>
