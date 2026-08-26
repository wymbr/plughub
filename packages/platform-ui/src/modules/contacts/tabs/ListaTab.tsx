/**
 * ListaTab — tabela paginada de contatos.
 * Consome ContactFilters do pai (ContactsPage) via props.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { apiFetch } from '@/api/apiFetch'
import type { ContactFilters, ContactRow, ContactsApiResponse } from '../types'
import {
  formatMs, formatDt, CHANNEL_ICONS, DIRECTION_ICONS,
  contactDirection,
} from '../types'
// O chip do processo (rótulo, números e a regra do pivô) mora num componente só,
// compartilhado com o selo do breadcrumb do drill — as duas cópias já divergiram
// uma vez, na F3 (4 caracteres aqui, 8 lá, no MESMO processo).
import { ProcessChip, processCounts, hasProcess } from '../ProcessChip'

const PAGE_SIZE = 50

interface Props {
  tenantId:     string
  filters:      ContactFilters
  /** Arc 19: channel is passed alongside sessionId so the parent can detect webhook sessions */
  onOpenDetail: (sessionId: string, channel: string) => void
  /** ADR wrapup-detached-pull §7 — escopo da listagem (`scope=all` quando true).
   *  Mora no PAI pela mesma razão que `filters`: o drill desmonta esta aba (a página
   *  faz `return <Detail/>` antes de renderizá-la), e estado local morre na volta. */
  scopeAll:           boolean
  onScopeAllChange:   (v: boolean) => void
  /** F3.3 — pivô para a visão 2 (o processo). O chip é o ÚNICO caminho: não há
   *  lista livre de processos (D2/ADR §D3 — processo é pivô, nunca navegação). */
  onOpenJourney?: (journeyId: string) => void
}

export function ListaTab({ tenantId, filters, onOpenDetail, scopeAll, onScopeAllChange, onOpenJourney }: Props) {
  const { t } = useTranslation('contacts')
  const [rows,    setRows]    = useState<ContactRow[]>([])
  const [total,   setTotal]   = useState(0)
  const [page,    setPage]    = useState(1)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState('')
  // ADR wrapup-detached-pull §7 (fatia 3) — visibilidade ≠ contagem.
  // Desligado por padrão (estado no PAI): o default `scope=contacts` é bit-a-bit o
  // comportamento que a E2f fechou. Ligado, a tabela ganha LINHAS de pool interno;
  // a contagem de contatos do cabeçalho não muda por isso.
  const [totalContacts,  setTotalContacts]  = useState(0)
  const [totalInternal,  setTotalInternal]  = useState(0)
  // Tamanho do conjunto que classificou as linhas. 0 ⇒ não há como distinguir
  // nada ⇒ não prometer o recurso (o toggle nem é oferecido).
  const [internalPoolsKnown, setInternalPoolsKnown] = useState(0)
  // F3.3 — a janela de período incidiu? É o que torna o rodapé do chip CONDICIONAL:
  // sem recorte, "o chip conta o processo inteiro" não explica divergência nenhuma
  // e vira ruído fixo. Default `true` (o caminho de listagem sempre recorta), então
  // backend antigo — sem o marcador — degrada mostrando a frase, não escondendo-a.
  const [windowApplied,      setWindowApplied]      = useState(true)
  const pendingRef = useRef(false)

  const load = useCallback(async (p: number) => {
    if (pendingRef.current) return
    pendingRef.current = true
    setLoading(true); setError('')
    try {
      const params = new URLSearchParams({
        tenant_id: tenantId,
        page:      String(p),
        page_size: String(PAGE_SIZE),
      })
      const { fromDt, toDt, sessionIdSearch, channel, outcome, poolId, entryPoolId,
              agentId, insightCategory, insightTags, status, direction } = filters
      if (fromDt)          params.set('from_dt',          fromDt + 'T00:00:00')
      if (toDt)            params.set('to_dt',            toDt   + 'T23:59:59')
      if (sessionIdSearch) params.set('session_id',       sessionIdSearch)
      if (channel)         params.set('channel',          channel)
      if (outcome)         params.set('outcome',          outcome)
      // Os DOIS filtros de pool, e eles vão em parâmetros DIFERENTES: `pool_id` é
      // "atendido por" (subconsulta em segments) e `entry_pool_id` é "entrou por"
      // (a porta). Compõem por AND — é assim que se pergunta "entrou no sac_ia e
      // terminou no humano".
      if (poolId)          params.set('pool_id',          poolId)
      if (entryPoolId)     params.set('entry_pool_id',    entryPoolId)
      if (agentId)         params.set('agent_id',         agentId)
      if (insightCategory) params.set('insight_category', insightCategory)
      if (insightTags)     params.set('insight_tags',     insightTags)
      if (status)          params.set('status',           status)          // Arc 19
      // D8 — a direção viaja para o BACKEND, que a filtra pela mesma expressão que
      // desenha a coluna. Recortar no cliente daria um número certo para a página e
      // errado para o cabeçalho, que é servido pela contagem.
      if (direction)       params.set('direction',        direction)
      // ADR §7 — só o valor não-default viaja; `contacts` é o default do backend.
      if (scopeAll)        params.set('scope',            'all')

      const res = await apiFetch(`/reports/sessions?${params}`)
      if (!res.ok) { setError(t('lista.httpError', { status: res.status })); return }
      const data: ContactsApiResponse = await res.json()
      const items = Array.isArray(data) ? (data as unknown as ContactRow[]) : (data.data ?? [])
      setRows(items)
      const meta = Array.isArray(data) ? undefined : data.meta
      const listed = meta?.total ?? items.length
      setTotal(listed)
      // Backend antigo (sem os dois domínios) ⇒ cabeçalho degrada para o total
      // listado, que naquele backend É o total de contatos.
      setTotalContacts(meta?.total_contacts ?? listed)
      setTotalInternal(meta?.total_internal ?? 0)
      setInternalPoolsKnown(meta?.internal_pools_known ?? 0)
      setWindowApplied(meta?.window_applied ?? true)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false); pendingRef.current = false
    }
  }, [tenantId, filters, scopeAll])

  // Reset to page 1 whenever filters change
  useEffect(() => { setPage(1); load(1) }, [load])

  // Paginação sobre o que está LISTADO (`meta.total`), não sobre a contagem de
  // contatos do cabeçalho — com o escopo expandido os dois divergem por desenho.
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  function changePage(p: number) { setPage(p); load(p) }

  // A coluna `parent` (contato pai) só existe no escopo expandido: fora dele não
  // há linha interna, e uma coluna vazia prometeria um vínculo que a listagem de
  // contatos não tem.
  //
  // ── F3 ────────────────────────────────────────────────────────────────────
  // SAÍRAM `origin`/`destination` (ANI/DNIS): permanentemente vazias nos dois canais
  // existentes — zero valores em 314 sessões (achados 1 e 3 do desenho). Duas colunas
  // que só sabiam dizer `—`. **Não voltam.**
  //
  // ENTROU `direction` — e o nome importa: `origin` já significava ANI aqui, então
  // batizar a direção de "origin" faria o operador ler um e receber o outro.
  //
  // `channel` foi DOBRADA em `contact` (ícone + id), como no desenho §1: o canal é
  // atributo do contato, não coluna própria.
  //
  // ── Largura é requisito, não estética (corrigido na revisão da F3) ─────────
  // A 1ª versão embarcou 11 colunas (as 7 do desenho + `ended`/`status`/`segments`,
  // mantidas por instinto de mudança mínima) e a tabela passou a exigir SCROLL
  // HORIZONTAL — jogando a coluna `process` para fora da tela. O chip é o ÚNICO
  // caminho para a visão 2 (D2: processo é pivô, não navegação), então "mínima
  // mudança" tinha escondido justamente a entrega da fase.
  //   · `ended` SAIU — o desenho não a lista, e `started` + `duration` a dão.
  //   · `status` foi FUNDIDA em `outcome` — é a coluna 6 do desenho ("desfecho =
  //     outcome + close_reason"); eram duas células dizendo `resolved` e `closed`
  //     lado a lado.
  const columns: string[] = [
    'direction', 'contact', 'pools',
    ...(scopeAll ? ['parent'] : []),
    'started', 'duration', 'outcome', 'segments', 'process',
  ]

  if (error) {
    return (
      <div className="flex items-center justify-center h-full text-red text-sm p-8">
        {error}
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">

      {/* Count + pagination bar */}
      <div className="flex items-center justify-between px-4 py-2 bg-white border-b border-border flex-shrink-0 text-xs">
        <span className="text-muted-light flex items-center gap-3">
          {loading
            ? <><span className="animate-spin inline-block">⟳</span> {t('lista.loading')}</>
            : <span>
                {/* ADR §7.2 item 2 — DOIS domínios, nunca um total somado.
                    O cabeçalho é sempre o número de CONTATOS; a linha interna é
                    reportada à parte, e é a paginação (não o cabeçalho) que
                    dimensiona o que está listado. */}
                <strong className="text-dark">{t('lista.count', { count: totalContacts })}</strong>
                {scopeAll && (
                  <span className="ml-1 text-muted">· {t('lista.countInternal', { count: totalInternal })}</span>
                )}
                {totalPages > 1 && <span className="ml-2 text-muted">· {t('lista.page', { page, total: totalPages })}</span>}
              </span>
          }
          {/* Sem conjunto que classifique, não há como distinguir nada — não
              oferecer o recurso (fatia 3, item 4). */}
          {internalPoolsKnown > 0 && (
            <label className={`flex items-center gap-1.5 text-muted transition-colors ${
              loading ? 'opacity-50 cursor-wait' : 'cursor-pointer hover:text-dark'}`}
              title={t('lista.internalToggleHint')}>
              {/* Desabilitado em voo: o `pendingRef` do `load` descarta requisição
                  concorrente, então um clique aqui durante o fetch seria um no-op
                  silencioso — a tabela ficaria no escopo antigo com o toggle ligado. */}
              <input type="checkbox" checked={scopeAll} disabled={loading}
                onChange={e => onScopeAllChange(e.target.checked)}
                className="accent-primary cursor-pointer disabled:cursor-wait" />
              {t('lista.internalToggle')}
            </label>
          )}
        </span>
        {totalPages > 1 && (
          <div className="flex items-center gap-1">
            <button disabled={page <= 1} onClick={() => changePage(page - 1)}
              className="px-2 py-0.5 rounded border border-border text-muted disabled:opacity-40 hover:border-primary hover:text-primary transition-colors">
              {t('lista.prev')}
            </button>
            {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
              const start = Math.max(1, Math.min(page - 2, totalPages - 4))
              return start + i
            }).map(p => (
              <button key={p} onClick={() => changePage(p)}
                className={`px-2 py-0.5 rounded border transition-colors ${
                  p === page ? 'bg-primary text-white border-primary' : 'border-border text-muted hover:border-primary hover:text-primary'
                }`}>
                {p}
              </button>
            ))}
            <button disabled={page >= totalPages} onClick={() => changePage(page + 1)}
              className="px-2 py-0.5 rounded border border-border text-muted disabled:opacity-40 hover:border-primary hover:text-primary transition-colors">
              {t('lista.next')}
            </button>
          </div>
        )}
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {rows.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center h-full text-muted-light gap-2">
            <span className="text-3xl">📂</span>
            <span className="text-sm">{t('lista.empty')}</span>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-surface-muted border-b border-border z-10">
              <tr>
                {columns.map(col => (
                  <th key={col} className="text-left text-xs font-semibold text-muted uppercase tracking-wide px-4 py-2.5 whitespace-nowrap">
                    {t(`lista.columns.${col}`)}
                  </th>
                ))}
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map(row => (
                <ContactRowItem key={row.session_id} row={row} showParent={scopeAll}
                  onOpenParent={pid => onOpenDetail(pid, '')}
                  onOpenJourney={onOpenJourney}
                  onClick={() => {
                  // Fase C: classify by the REAL channel_type — não por presença de
                  // step delegate/suspend. O v2 preserva o canal no resume/conference
                  // (webchat continua webchat, webhook continua webhook), então o canal
                  // real decide a view: 'webhook' → WorkflowTraceList; demais → SegmentList.
                  // (O antigo fallback 'suspended → webhook' classificava errado uma
                  //  sessão webchat suspensa num delegate-wait.)
                  onOpenDetail(row.session_id, row.channel || '')
                }} />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* ── Rodapé do chip (decisão aberta #2 do ADR, fechada aqui) ─────────────
          O N do chip conta o processo INTEIRO, de propósito: uma janela que pega 2
          de 3 contatos mostra `· 3`. Isso vai parecer defeito para quem não souber,
          e esta linha é a única coisa que o impede de virar chamado.

          CONDICIONAL em `meta.window_applied`: no drill (por processo/por contato) a
          janela não incide, não há divergência a explicar, e a frase seria ruído. E
          condicional em haver chip na página — explicar um elemento que não está na
          tela é pior do que não explicar. */}
      {windowApplied && rows.some(r => (r.journey_session_count ?? 0) > 1) && (
        <div className="px-4 py-1.5 bg-surface-muted border-t border-border flex-shrink-0 text-2xs text-muted-light">
          {t('lista.processFootnote')}
        </div>
      )}

    </div>
  )
}

// ─── Row ──────────────────────────────────────────────────────────────────────

// ── Status helpers ────────────────────────────────────────────────────────────

const ABANDONED_REASONS = new Set([
  'customer_abandon', 'no_resource', 'max_wait_exceeded',
  'customer_disconnect', 'customer_hangup', 'session_timeout',
])

function SessionStatusBadge({ row }: { row: ContactRow }) {
  const { t } = useTranslation('contacts')
  // Fase C: badge "suspended" só para WEBHOOK (workflow sem cliente vivo aguardando
  // sinal externo). Uma sessão webchat "suspended" está num delegate-wait com o cliente
  // presente (specialist ativo) → lê como live, não suspended. (channel é um proxy de
  // "não há cliente vivo"; um contador de participantes vivos exigiria suporte no backend.)
  if (!row.closed_at && row.status === 'suspended' && row.channel === 'webhook') {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full bg-primary-light text-primary">
        <span className="w-1.5 h-1.5 rounded-full bg-primary inline-block opacity-60" /> {t('lista.suspended')}
      </span>
    )
  }
  if (!row.closed_at) {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full bg-green-light text-green-text">
        <span className="w-1.5 h-1.5 rounded-full bg-green inline-block" /> {t('lista.liveStatus')}
      </span>
    )
  }
  if (row.close_reason && ABANDONED_REASONS.has(row.close_reason)) {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full bg-warning-light text-warning-text"
        title={row.close_reason}>
        {t('lista.abandoned')}
      </span>
    )
  }
  return (
    <span className="inline-block text-xs px-2 py-0.5 rounded-full bg-surface-alt text-muted">
      {t('lista.closed')}
    </span>
  )
}

// ── Row ───────────────────────────────────────────────────────────────────────

function ContactRowItem({ row, onClick, showParent, onOpenParent, onOpenJourney }: {
  row: ContactRow
  onClick: () => void
  /** escopo expandido (`scope=all`) — só então existe a coluna de contato pai */
  showParent: boolean
  onOpenParent: (parentSessionId: string) => void
  onOpenJourney?: (journeyId: string) => void
}) {
  const { t } = useTranslation('contacts')
  const shortId = row.session_id.length > 16 ? '…' + row.session_id.slice(-14) : row.session_id
  const parentId = row.origin_session_id || ''

  const direction = contactDirection(row)
  // Entrada × atendimento. O último da lista é quem atendeu POR ÚLTIMO (a lista vem
  // ordenada por primeiro segmento). A seta só aparece quando os dois DIFEREM: num
  // contato sem handoff, `sac_ia → sac_ia` seria ruído com cara de informação.
  const entryPool    = row.pool_id || ''
  const attended     = row.attended_pool_ids ?? []
  const lastAttended = attended.length ? attended[attended.length - 1] : ''
  const showHandoff  = !!lastAttended && lastAttended !== entryPool

  // Chip só quando há processo COM MAIS DE UMA sessão: processo de um contato não
  // tem para onde pivotar, e é o caso majoritário. `null`/ausente (falha de contagem
  // no backend) cai aqui e NÃO desenha — ver `journey_session_count` em types.ts.
  // A regra e os números vivem em `ProcessChip.tsx`, com o selo do breadcrumb.
  const counts    = processCounts(row)
  const journeyId = row.journey_id || row.root_session_id || ''
  const showChip  = hasProcess(counts) && !!journeyId

  return (
    <tr onClick={onClick} className="hover:bg-primary/5 cursor-pointer transition-colors">
      {/* Direção do acesso (D8) — derivada NO BACKEND, nunca armazenada, e lida daqui
          sem re-derivar: é a mesma expressão que o filtro `?direction=` aplica. */}
      <td className="px-4 py-3 whitespace-nowrap text-center">
        {direction ? (
          <span className="text-base" title={t(`lista.direction.${direction}`)}
            aria-label={t(`lista.direction.${direction}`)}>
            {DIRECTION_ICONS[direction]}
          </span>
        ) : (
          <span className="text-border-strong text-xs" title={t('lista.direction.unknownHint', { value: row.spawn_reason ?? '' })}>—</span>
        )}
      </td>
      {/* Contato = canal + id (desenho §1, coluna 2). */}
      <td className="px-4 py-3 font-mono text-xs text-dark whitespace-nowrap">
        <span className="mr-1.5" title={row.channel || ''}>{CHANNEL_ICONS[row.channel] ?? '⬡'}</span>
        {/* Tag por veredicto do backend (`is_internal`) — a UI não reclassifica por pool_id. */}
        {row.is_internal && (
          <span className="mr-2 inline-block text-2xs font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded bg-surface-alt text-muted border border-border"
            title={t('lista.internalBadgeHint')}>
            {t('lista.internalBadge')}
          </span>
        )}
        {shortId}
      </td>
      {/* `entrou por → atendido por` — os DOIS pools, nunca um só chamado "Pool". */}
      <td className="px-4 py-3 text-muted text-xs whitespace-nowrap max-w-[280px] truncate"
        title={showHandoff ? `${entryPool} → ${lastAttended}` : entryPool}>
        {entryPool ? entryPool.replace(/_/g, ' ') : <span className="text-border-strong">—</span>}
        {showHandoff && (
          <>
            <span className="mx-1 text-border-strong">→</span>
            <span className="text-dark">{lastAttended.replace(/_/g, ' ')}</span>
          </>
        )}
      </td>
      {showParent && (
        <td className="px-4 py-3 text-xs whitespace-nowrap">
          {parentId ? (
            <button
              onClick={e => { e.stopPropagation(); onOpenParent(parentId) }}
              title={parentId}
              className="font-mono text-primary hover:underline">
              {'…' + parentId.slice(-14)}
            </button>
          ) : <span className="text-border-strong">—</span>}
        </td>
      )}
      <td className="px-4 py-3 text-muted text-xs tabular-nums whitespace-nowrap">{formatDt(row.opened_at)}</td>
      {/* Duração = `elapsed_time_ms` (D9): o tempo do CASO, esperas incluídas.
          NUNCA `agent_time_ms` (outra grandeza, agente × tempo) e NUNCA Σ segmentos —
          eles se SOBREPÕEM (@mention é rotina), então a soma nem é uma duração.
          `handle_time_ms` fica só como fallback de backend antigo, não como fonte. */}
      <td className="px-4 py-3 text-dark tabular-nums whitespace-nowrap text-xs">
        {formatMs(row.elapsed_time_ms ?? row.handle_time_ms)}
      </td>
      {/* Desfecho = estado + `outcome`, com `close_reason` no título (desenho §1,
          coluna 6). UMA célula: o badge responde "como terminou" (ou que ainda não
          terminou — sessão viva não tem outcome) e o texto responde "com que
          resultado". Separadas, eram duas colunas dizendo `closed` e `resolved`
          lado a lado, e foi esse par redundante que empurrou o chip para fora da
          tela. */}
      <td className="px-4 py-3 whitespace-nowrap" title={row.close_reason ?? ''}>
        <SessionStatusBadge row={row} />
        {row.outcome && <span className="ml-1.5 text-xs text-muted">{row.outcome}</span>}
      </td>
      <td className="px-4 py-3 text-center">
        {row.segment_count > 0 ? (
          <span className="inline-block text-xs font-semibold px-2 py-0.5 rounded-full bg-primary-light text-primary tabular-nums">
            {row.segment_count}
          </span>
        ) : <span className="text-border-strong text-xs">—</span>}
      </td>
      {/* Chip de processo — o ÚNICO pivô para a visão 2 (D2: processo nunca é linha,
          nunca é navegação livre). Os números contam o processo INTEIRO (ver o
          rodapé) e nos MESMOS dois domínios do cabeçalho aonde o clique leva. */}
      <td className="px-4 py-3 whitespace-nowrap">
        {showChip && counts ? (
          <ProcessChip
            journeyId={journeyId} counts={counts} t={t}
            onOpen={() => onOpenJourney?.(journeyId)} />
        ) : <span className="text-border-strong text-xs">—</span>}
      </td>
      <td className="px-4 py-3 text-muted-light text-right">›</td>
    </tr>
  )
}
