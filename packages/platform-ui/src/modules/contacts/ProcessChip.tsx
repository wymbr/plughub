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
 *
 * ── O marcador `+` (2026-08-26) ─────────────────────────────────────────────
 *
 * Os números vêm contados SOB a ABAC do usuário — deliberado no backend, para não
 * revelar o tamanho de um processo que toca pools fora do escopo dele. A
 * consequência não prevista era esta tela AFIRMAR o contrário do que sabia: com
 * `total = 1`, `hasProcess` escondia o chip e a linha passava a dizer *"este
 * contato não pertence a processo nenhum"* — a mesma mentira tranquila que o
 * backend já proibia para o caso de FALHA. Medido: 6 linhas do `tenant_demo`
 * reportando `1` para processos de 4, 3, 4, 4 e 4 sessões.
 *
 * `journey_has_scoped_out_members` é a EXISTÊNCIA, não o tamanho: o chip volta como
 * `PRC-xxxx · 1+`, dizendo *"há mais, você não alcança"*.
 *
 * ⚠️ **Nesse ramo a QUEBRA não é desenhada, e não é economia de espaço.** A quebra
 * também é escopada: uma classe inteira pode estar fora do alcance e aparecer como
 * `0`. Publicar `· 3 + 0` afirmaria *"não há etapa interna"* — o mesmo defeito um
 * nível abaixo, agora com a agravante de conviver com o marcador que anuncia o
 * oposto. Sob o marcador, o chip publica UM número (o que se alcança) e o `+`.
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
  /** Há membros do processo fora do escopo ABAC de quem olha. Ver o cabeçalho. */
  scopedOut:    boolean
}

type CountFields = Pick<
  ContactRow,
  | 'journey_session_count'
  | 'journey_access_count'
  | 'journey_internal_step_count'
  | 'journey_unclassified_count'
  | 'journey_has_scoped_out_members'
>

/**
 * Extrai os números de uma linha. `null` ⇒ **não há chip**: o backend não contou, e
 * um `1` inventado afirmaria "este contato não pertence a processo nenhum".
 *
 * ⚠️ **`scopedOut` colapsa `null` em `false`, e a direção do colapso é que o torna
 * seguro.** O campo tem três estados no backend (há / medi e não há / não medi), mas
 * aqui ele só decide se um `+` aparece. Colapsar para `false` faz o desconhecido não
 * AFIRMAR nada — no máximo deixa de revelar. Colapsar para `true` seria inventar
 * membros que ninguém contou, que é o defeito que este campo existe para consertar.
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
    scopedOut:    row.journey_has_scoped_out_members === true,
  }
}

/** Regra do pivô, compartilhada pelos dois lugares: processo de UM contato não tem
 *  para onde pivotar, e um chip apontando para ele afirmaria uma relação que não
 *  existe.
 *
 *  `scopedOut` é a segunda razão para pivotar, e sem ela a primeira mente: `total`
 *  é ESCOPADO, então um processo de quatro sessões das quais o operador alcança uma
 *  chega aqui como `1` e desapareceria da tela. O `total > 1` sozinho não separa
 *  "processo de um contato" de "processo de um contato *visível*". */
export function hasProcess(counts: ProcessCounts | null): boolean {
  return !!counts && (counts.total > 1 || counts.scopedOut)
}

export function chipTitle(c: ProcessCounts | null, t: TFunc, journeyId: string): string {
  if (!c) return journeyId
  // O marcador vem PRIMEIRO: sob ele o número é um piso, e o tooltip da quebra
  // ("3 acessos · 2 etapas internas") leria como inventário completo do processo.
  if (c.scopedOut) return t('lista.processChipScopedHint', { count: c.total })
  if (c.access === null) return t('lista.processChipHint', { count: c.total })
  const parts = [t('journeys.accessCount', { count: c.access })]
  if (c.internal)     parts.push(t('journeys.internalCount',     { count: c.internal }))
  if (c.unclassified) parts.push(t('journeys.unclassifiedCount', { count: c.unclassified }))
  return `${parts.join(' · ')} — ${t('lista.processChipOpen')}`
}

/** Só os números, para quem já desenha a moldura. Acesso em destaque (é o
 *  protagonista); interna e não classificada em peso normal. */
export function ProcessCountsText({ c }: { c: ProcessCounts }) {
  // Piso, não inventário: um número e o `+`. A quebra fica de fora porque ela também
  // é escopada — ver o ⚠️ do cabeçalho. O `+` aqui não é o mesmo `+` que separa as
  // classes abaixo, e é justamente por isso que os dois nunca aparecem juntos.
  if (c.scopedOut) {
    return <span className="tabular-nums font-semibold">· {c.total}+</span>
  }
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
