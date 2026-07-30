/**
 * lib/internal-queue.ts
 * Fila interna espelho por pool — ADR `docs/adr/adr-internal-work-queue-author-bound.md`.
 *
 * Ligar `internal_queue_enabled` num pool cria e mantém um pool FÍSICO `{pool_id}-int`
 * (`purpose: internal`, `dispatch_mode: pull`, `agent_kind: human`, mesmos canais) onde
 * vive o trabalho AUTHOR-BOUND gerado por ele — wrap-up à frente.
 *
 * Author-bound ≠ pooled: só quem atendeu pode classificar o próprio atendimento, logo a
 * identidade do executor é parte da DEFINIÇÃO da tarefa e transbordo é erro de categoria.
 * Aprovação É pooled (outro aprovador decide) e NÃO usa fila interna — o critério é este,
 * não "é humano" nem "é workflow".
 *
 * Por que pool REAL e não fila virtual: o invariante "o POOL é a unidade endereçável".
 * Com pool real, routing, capacidade, `segments.pool_id`, `pools_client` e a
 * `mv_agent_performance_daily` seguem com UMA gramática só — e o segmento humano do
 * wrap-up passa a carregar um pool `internal`, que o filtro da E2f já cobre.
 *
 * Espelho por pool (e não um pool de claim único) porque o ACW passa a ser legível
 * **por pool de origem**; um pool único teria misturado todos os times, e a MV, chaveada
 * por `pool_id`, não teria conserto por leitura.
 */

import { prisma, Prisma } from "../db"
import { publishRegistryEvent, publishRegistryChanged } from "../infra/kafka"

/**
 * Sufixo reservado POR CONSTRUÇÃO: a regex de `pool_id` (@plughub/schemas) é
 * `^[a-z0-9_]+(-int)?$`, então o hífen só é legal aqui e nenhum pool declarado por
 * tenant pode colidir. `endsWith` vira garantia, não convenção — é o que permite
 * derivar acesso (`p ∪ p+"-int"`) sem adivinhação.
 */
export const INTERNAL_QUEUE_SUFFIX = "-int"

/**
 * SLA do espelho. NÃO herda o do pai de propósito: o SLA do pai é o prazo do CONTATO,
 * o do espelho é o prazo para concluir o pós-atendimento. Herdar faria a inbox exibir
 * um número que mente (o wrap-up de um pool com SLA de 30 s nasceria estourado).
 * 24 h, mesmo horizonte do `timeout_hours` do delegate de wrap-up.
 */
export const INTERNAL_QUEUE_SLA_MS = 86_400_000

export function mirrorPoolId(poolId: string): string {
  return `${poolId}${INTERNAL_QUEUE_SUFFIX}`
}

export function isMirrorPoolId(poolId: string): boolean {
  return poolId.endsWith(INTERNAL_QUEUE_SUFFIX)
}

/** Pool de origem de um espelho, ou null quando o id não é de espelho. */
export function originPoolId(poolId: string): string | null {
  return isMirrorPoolId(poolId)
    ? poolId.slice(0, -INTERNAL_QUEUE_SUFFIX.length)
    : null
}

type ParentPool = {
  pool_id:       string
  channel_types: string[]
  description:   string | null
}

/**
 * Reconcilia o espelho com a flag do pai. Idempotente: chamada em todo create/update.
 *
 * Ligada  → cria (ou reativa e re-sincroniza os campos derivados) o espelho.
 * Desligada → desativa o espelho (`status: inactive`), preservando a linha e o histórico.
 *
 * Não há DELETE de pool na API por desenho (nem no `RegistrySyncer`, que não poda pools),
 * então "remover" é desativar. Isso é desejável aqui: o espelho aparece em `segments` e
 * em relatórios históricos, e apagá-lo tornaria ilegível o ACW já medido.
 *
 * Publica `pool.registered`/`pool.updated` + `registry.changed` para o espelho — sem isso
 * o `{tenant}:pool_config:{pool}-int` nunca é escrito, e um pool sem pool_config entrega
 * SLA ausente na inbox e some do lookup de hooks do bridge. Falha de ausência, não de erro.
 */
export async function syncInternalQueueMirror(
  tenantId:  string,
  parent:    ParentPool,
  enabled:   boolean,
  createdBy: string,
): Promise<{ mirror_pool_id: string; action: "created" | "updated" | "deactivated" | "noop" }> {
  const mirrorId = mirrorPoolId(parent.pool_id)

  const existing = await prisma.pool.findUnique({
    where: { pool_id_tenant_id: { pool_id: mirrorId, tenant_id: tenantId } },
  })

  if (!enabled) {
    if (!existing || (existing as { status: string }).status === "inactive") {
      return { mirror_pool_id: mirrorId, action: "noop" }
    }
    const updated = await prisma.pool.update({
      where: { id: existing.id },
      data:  { status: "inactive" as never },
    })
    await _publishMirror(tenantId, updated, "updated")
    return { mirror_pool_id: mirrorId, action: "deactivated" }
  }

  // Campos DERIVADOS do pai, re-sincronizados a cada update. Capacidade não entra:
  // a vaga é do RECURSO (semáforo `claim_instance`, chaveado por instância sem pool),
  // não da fila — redefini-la aqui criaria um segundo teto que ninguém lê.
  const derived = {
    agent_kind:             "human",
    description:            `Fila interna (pós-atendimento) de ${parent.pool_id}`,
    channel_types:          parent.channel_types,
    sla_target_ms:          INTERNAL_QUEUE_SLA_MS,
    dispatch_mode:          "pull",
    purpose:                "internal",
    internal_queue_enabled: false,   // espelho de espelho não existe
    status:                 "active" as never,
  }

  if (existing) {
    const updated = await prisma.pool.update({
      where: { id: existing.id },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      data:  derived as any,
    })
    await _publishMirror(tenantId, updated, "updated")
    return { mirror_pool_id: mirrorId, action: "updated" }
  }

  const created = await prisma.pool.create({
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    data: {
      pool_id:            mirrorId,
      tenant_id:          tenantId,
      created_by:         createdBy,
      routing_expression: Prisma.DbNull,
      supervisor_config:  Prisma.DbNull,
      queue_config:       Prisma.DbNull,
      mentionable_pools:  Prisma.DbNull,
      hooks:              Prisma.DbNull,
      context_visibility: Prisma.DbNull,
      ...derived,
    } as any,
  })
  await _publishMirror(tenantId, created, "created")
  return { mirror_pool_id: mirrorId, action: "created" }
}

async function _publishMirror(
  tenantId:  string,
  pool:      Record<string, unknown>,
  operation: "created" | "updated",
): Promise<void> {
  const { id: _id, ...formatted } = pool
  await publishRegistryEvent({
    event:     operation === "created" ? "pool.registered" : "pool.updated",
    tenant_id: tenantId,
    pool:      formatted,
  })
  await publishRegistryChanged(tenantId, "pool", String(pool["pool_id"]), operation)
}

/**
 * Guarda de configuração: um hook `dispatch: detached` + `side: agent` é trabalho
 * author-bound por definição — ele existe para o agente classificar o PRÓPRIO segmento,
 * e o skill genérico o entrega em `@ctx.hook.wrapup_pool`. Sem a fila interna ligada
 * essa tag não é injetada e o delegate falha em runtime, longe de onde se conserta.
 *
 * Falhar aqui move o erro para a configuração, que é onde ele tem resposta. O custo de
 * um falso positivo é ligar uma flag que não custa nada; o de um falso negativo é um
 * wrap-up que só quebra depois do próximo atendimento real.
 */
export function detachedHookViolation(
  hooks:   unknown,
  enabled: boolean,
): Record<string, unknown> | null {
  if (enabled) return null
  const h = (hooks ?? {}) as Record<string, unknown>
  const offending: string[] = []
  for (const key of ["on_human_end", "on_contact_end", "on_process_end", "post_human"]) {
    const entries = Array.isArray(h[key]) ? (h[key] as Record<string, unknown>[]) : []
    for (const e of entries) {
      const side = (e["side"] as string) ?? "agent"
      if (e["dispatch"] === "detached" && side === "agent") {
        offending.push(`${key}[pool=${String(e["pool"])}]`)
      }
    }
  }
  if (offending.length === 0) return null
  return {
    error:
      "hook `dispatch: detached` + `side: agent` é trabalho author-bound e exige " +
      "`internal_queue_enabled: true` neste pool — sem a fila interna o skill do hook " +
      "não recebe `@ctx.hook.wrapup_pool` e o delegate falha em runtime.",
    details: { offending_hooks: offending },
  }
}
