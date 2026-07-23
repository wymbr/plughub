/**
 * ListaTab — tabela paginada de contatos.
 * Consome ContactFilters do pai (ContactsPage) via props.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { apiFetch } from '@/api/apiFetch'
import type { ContactFilters, ContactRow, ContactsApiResponse } from '../types'
import {
  formatMs, formatDt, CHANNEL_ICONS,
} from '../types'

const PAGE_SIZE = 50

interface Props {
  tenantId:     string
  filters:      ContactFilters
  /** Arc 19: channel is passed alongside sessionId so the parent can detect webhook sessions */
  onOpenDetail: (sessionId: string, channel: string) => void
}

export function ListaTab({ tenantId, filters, onOpenDetail }: Props) {
  const { t } = useTranslation('contacts')
  const [rows,    setRows]    = useState<ContactRow[]>([])
  const [total,   setTotal]   = useState(0)
  const [page,    setPage]    = useState(1)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState('')
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
      const { fromDt, toDt, sessionIdSearch, channel, outcome, poolId,
              agentId, ani, dnis, insightCategory, insightTags, status } = filters
      if (fromDt)          params.set('from_dt',          fromDt + 'T00:00:00')
      if (toDt)            params.set('to_dt',            toDt   + 'T23:59:59')
      if (sessionIdSearch) params.set('session_id',       sessionIdSearch)
      if (channel)         params.set('channel',          channel)
      if (outcome)         params.set('outcome',          outcome)
      if (poolId)          params.set('pool_id',          poolId)
      if (agentId)         params.set('agent_id',         agentId)
      if (ani)             params.set('ani',              ani)
      if (dnis)            params.set('dnis',             dnis)
      if (insightCategory) params.set('insight_category', insightCategory)
      if (insightTags)     params.set('insight_tags',     insightTags)
      if (status)          params.set('status',           status)          // Arc 19

      const res = await apiFetch(`/reports/sessions?${params}`)
      if (!res.ok) { setError(t('lista.httpError', { status: res.status })); return }
      const data: ContactsApiResponse = await res.json()
      const items = Array.isArray(data) ? (data as unknown as ContactRow[]) : (data.data ?? [])
      setRows(items)
      setTotal(data.meta?.total ?? items.length)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false); pendingRef.current = false
    }
  }, [tenantId, filters])

  // Reset to page 1 whenever filters change
  useEffect(() => { setPage(1); load(1) }, [load])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  function changePage(p: number) { setPage(p); load(p) }

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
        <span className="text-muted-light">
          {loading
            ? <><span className="animate-spin inline-block">⟳</span> {t('lista.loading')}</>
            : <><strong className="text-dark">{t('lista.count', { count: total })}</strong>
                {totalPages > 1 && <span className="ml-2 text-muted">· {t('lista.page', { page, total: totalPages })}</span>}
              </>
          }
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
                {(['sessionId','channel','pool','origin','destination','started','ended','duration','status','segments'] as const).map(col => (
                  <th key={col} className="text-left text-xs font-semibold text-muted uppercase tracking-wide px-4 py-2.5 whitespace-nowrap">
                    {t(`lista.columns.${col}`)}
                  </th>
                ))}
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map(row => (
                <ContactRowItem key={row.session_id} row={row} onClick={() => {
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

function ContactRowItem({ row, onClick }: { row: ContactRow; onClick: () => void }) {
  const { t } = useTranslation('contacts')
  const shortId = row.session_id.length > 16 ? '…' + row.session_id.slice(-14) : row.session_id

  return (
    <tr onClick={onClick} className="hover:bg-primary/5 cursor-pointer transition-colors">
      <td className="px-4 py-3 font-mono text-xs text-dark whitespace-nowrap">{shortId}</td>
      <td className="px-4 py-3 text-muted whitespace-nowrap">
        {CHANNEL_ICONS[row.channel] ?? '⬡'} {row.channel || <span className="text-border-strong">—</span>}
      </td>
      <td className="px-4 py-3 text-muted text-xs whitespace-nowrap max-w-[120px] truncate" title={row.pool_id ?? ''}>
        {row.pool_id?.replace(/_/g, ' ') ?? '—'}
      </td>
      <td className="px-4 py-3 text-muted text-xs whitespace-nowrap tabular-nums">
        {row.ani ? <span className="font-mono">{row.ani}</span> : <span className="text-border-strong">—</span>}
      </td>
      <td className="px-4 py-3 text-muted text-xs whitespace-nowrap tabular-nums">
        {row.dnis ? <span className="font-mono">{row.dnis}</span> : <span className="text-border-strong">—</span>}
      </td>
      <td className="px-4 py-3 text-muted text-xs tabular-nums whitespace-nowrap">{formatDt(row.opened_at)}</td>
      <td className="px-4 py-3 text-muted text-xs tabular-nums whitespace-nowrap">
        {!row.closed_at
          ? <span className="text-green-text font-medium">{t('lista.active')}</span>
          : formatDt(row.closed_at)}
      </td>
      <td className="px-4 py-3 text-dark tabular-nums whitespace-nowrap text-xs">{formatMs(row.handle_time_ms)}</td>
      {/* Status — active / closed / abandoned */}
      <td className="px-4 py-3 whitespace-nowrap"><SessionStatusBadge row={row} /></td>
      <td className="px-4 py-3 text-center">
        {row.segment_count > 0 ? (
          <span className="inline-block text-xs font-semibold px-2 py-0.5 rounded-full bg-primary-light text-primary tabular-nums">
            {row.segment_count}
          </span>
        ) : <span className="text-border-strong text-xs">—</span>}
      </td>
      <td className="px-4 py-3 text-muted-light text-right">›</td>
    </tr>
  )
}
