/**
 * Chip de processo (`PRC-…`) — UMA renderização, dois lugares.
 *
 * ── Por que o componente existe ──────────────────────────────────────────────
 *
 * O chip é o ÚNICO pivô da visão 1 para a visão 2 (ADR D2: processo nunca é linha,
 * nunca é navegação livre) e aparece em DOIS lugares: a coluna "Processo" da lista
 * (`tabs/ListaTab.tsx`) e o selo do breadcrumb do drill (`SessionsPage.tsx`). Até
 * 2026-08-26 eram duas cópias — e elas já haviam divergido uma vez, na F3: uma
 * cortava o id em 4 caracteres e a outra em 8, então o mesmo processo tinha um
 * código no lugar de onde se clica e outro no lugar aonde se chega.
 *
 * ── O número que o chip publica (2026-08-26) ────────────────────────────────
 *
 * O chip publicava `· 5` sob o rótulo *"contatos"*; o cabeçalho da visão 2, para
 * onde ele pivota, publicava `3 acessos · 2 etapas internas`. Os dois estavam
 * certos e contavam coisas diferentes — foi a F4 que criou a divergência, ao dar
 * ao cabeçalho um domínio que o chip não tinha. O operador clicava num `5` e
 * chegava a um `3`.
 *
 * Agora o chip publica **os mesmos dois domínios**: `· 3 + 2`. Não é um recorte do
 * total — é o total, quebrado. `acesso + interna + não classificada === total`, por
 * construção (os três saem de `countIf` sobre a mesma `_DIRECTION_EXPR` no backend,
 * cujo `multiIf` é exaustivo).
 *
 * ⚠️ **A terceira classe só aparece quando existe.** Mesma regra do cabeçalho, que
 * só desenha `internalCount` com `> 0`. Somá-la aos acessos inflaria o número
 * protagonista com uma linha que ninguém sabe ler; escondê-la faria o chip deixar
 * de bater com o nº de linhas da tela.
 *
 * ⚠️ **O tooltip usa as chaves i18n DO CABEÇALHO** (`journeys.accessCount` etc.),
 * de propósito. Um vocabulário próprio aqui seria a mesma divergência de novo, na
 * camada de texto: o chip diria "contatos" e a tela aonde ele leva, "acessos".
 */
import { GitBranch } from 'lucide-react'
import { journeyLabel } from './types'
import type { ContactRow } from './types'

type TFunc = (key: string, opts?: Record<string, unknown>) => string

/** Os quatro números do chip. `access === null` ⇒ backend sem quebra (ou journey
 *  fora do agregado) ⇒ cai no número único. Nunca zerar só a quebra. */
export interface ProcessCounts {
  total:        number
  access:       number | null
  internal:     number | null
  unclassified: number | null
}

type CountFields = Pick<
  ContactRow,
  | 'journey_session_count'
  | 'journey_access_count'
  | 'journey_internal_step_count'
  | 'journey_unclassified_count'
>

/**
 * Extrai os números de uma linha. `null` ⇒ **não há chip**: o backend não contou, e
 * um `1` inventado afirmaria "este contato não pertence a processo nenhum".
 */
export function processCounts(row: CountFields): ProcessCounts | null {
  const total = row.journey_session_count
  if (total === null || total === undefined) return null
  const access = row.journey_access_count
  return {
    total,
    access:       access ?? null,
    internal:     row.journey_internal_step_count ?? null,
    unclassified: row.journey_unclassified_count ?? null,
  }
}

/** Regra do pivô, compartilhada pelos dois lugares: processo de UM contato não tem
 *  para onde pivotar, e um chip apontando para ele afirmaria uma relação que não
 *  existe. */
export function hasProcess(counts: ProcessCounts | null): boolean {
  return !!counts && counts.total > 1
}

export function chipTitle(c: ProcessCounts | null, t: TFunc, journeyId: string): string {
  if (!c) return journeyId
  if (c.access === null) return t('lista.processChipHint', { count: c.total })
  const parts = [t('journeys.accessCount', { count: c.access })]
  if (c.internal)     parts.push(t('journeys.internalCount',     { count: c.internal }))
  if (c.unclassified) parts.push(t('journeys.unclassifiedCount', { count: c.unclassified }))
  return `${parts.join(' · ')} — ${t('lista.processChipOpen')}`
}

/** Só os números, para quem já desenha a moldura. Acesso em destaque (é o
 *  protagonista); interna e não classificada em peso normal. */
export function ProcessCountsText({ c }: { c: ProcessCounts }) {
  if (c.access === null) {
    return <span className="tabular-nums font-semibold">· {c.total}</span>
  }
  return (
    <span className="tabular-nums">
      <span className="font-semibold">· {c.access}</span>
      {c.internal     ? <span className="opacity-70"> + {c.internal}</span>     : null}
      {c.unclassified ? <span className="opacity-70"> + {c.unclassified}</span> : null}
    </span>
  )
}

/**
 * O chip inteiro. `stopPropagation` embutido porque os dois call sites vivem dentro
 * de superfícies clicáveis (a linha da tabela, o breadcrumb) — deixá-lo por conta
 * de quem chama é como se abre o contato errado ao mirar no processo.
 */
export function ProcessChip({ journeyId, counts, t, onOpen }: {
  journeyId: string
  /** Ausente ⇒ chip só com o rótulo. Acontece no breadcrumb quando o processo veio
   *  da URL e a sessão ainda não foi resolvida: o caminho de volta existe e os
   *  números não — desenhar `· 0` ali seria inventar o tamanho do processo. */
  counts:    ProcessCounts | null
  t:         TFunc
  onOpen:    () => void
}) {
  return (
    <button
      type="button"
      onClick={e => { e.stopPropagation(); onOpen() }}
      title={chipTitle(counts, t, journeyId)}
      className="inline-flex items-center gap-1 text-xs font-mono px-2 py-0.5 rounded-full bg-primary-light text-primary hover:underline">
      <GitBranch className="w-3 h-3" aria-hidden="true" />
      {journeyLabel(journeyId)}
      {counts ? <ProcessCountsText c={counts} /> : null}
    </button>
  )
}
