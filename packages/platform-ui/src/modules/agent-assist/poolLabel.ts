/**
 * poolLabel.ts — nome de pool exibido ao agente.
 *
 * ADR adr-internal-work-queue-author-bound (D3): o espelho de fila interna `{origem}-int`
 * é OCULTO onde se escolhe e VISÍVEL onde se mede, mas em nenhuma superfície ele aparece
 * pelo id cru — `-int` é convenção de construção, não nome de produto, e o agente não tem
 * por que saber que existe um pool espelho.
 *
 * Existe como módulo próprio porque a regra estava replicada em três lugares com três
 * implementações do MESMO sufixo (contexto, inbox, barra do contato) — e a terceira não
 * aplicava o rótulo, o que produziu a inconsistência observada em 2026-07-30: a fila dizia
 * "Pós-atendimento — retencao_humano" e a barra do mesmo item dizia `retencao_humano-int`.
 * Regra repetida é regra que diverge; o sufixo agora tem um dono só.
 */

/**
 * Sufixo reservado do espelho. Garantido por CONSTRUÇÃO: a regex de `pool_id` no registry
 * (`^[a-z0-9_]+(-int)?$`) só admite hífen nesta posição, então nenhum pool declarado por
 * tenant pode colidir e `endsWith` é garantia, não convenção.
 */
export const INTERNAL_QUEUE_SUFFIX = "-int"

/** Pool de ORIGEM de um espelho, ou null quando o id não é de espelho. */
export function mirrorOriginOf(poolId: string): string | null {
  return poolId.endsWith(INTERNAL_QUEUE_SUFFIX)
    ? poolId.slice(0, -INTERNAL_QUEUE_SUFFIX.length)
    : null
}

/**
 * Rótulo de exibição de um pool. Espelho → "Pós-atendimento — {origem}"; qualquer outro
 * pool → o próprio id (que é o nome que o tenant escolheu).
 */
export function poolDisplayLabel(
  poolId: string,
  t: (k: string, o?: Record<string, unknown>) => string,
): string {
  const origin = mirrorOriginOf(poolId)
  if (!origin) return poolId
  return t("pullInbox.internalQueue", {
    defaultValue: "Pós-atendimento — {{pool}}",
    pool: origin,
  })
}
