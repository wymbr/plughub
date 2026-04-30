/**
 * ListaTab — tabela paginada de contatos.
 * Consome ContactFilters do pai (ContactsPage) via props.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react'
import type { ContactFilters, ContactRow, ContactsApiResponse } from '../types'
import {
  formatMs, formatDt, CHANNEL_ICONS, OUTCOME_COLORS,
} from '../types'

const PAGE_SIZE = 50

interface Props {
  tenantId:     string
  filters:      ContactFilters
  onOpenDetail: (sessionId: string) => void
}

export function ListaTab({ tenantId, filters, onOpenDetail }: Props) {
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
              agentId, ani, dnis, insightCategory, insightTags } = filters
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

      const res = await fetch(`/reports/sessions?${params}`)
      if (!res.ok) { setError(`Erro HTTP ${res.status}`); return }
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
      <div className="flex items-center justify-center h-full text-red-500 text-sm p-8">
        {error}
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">

      {/* Count bar */}
      <div className="flex items-center gap-2 px-4 py-2 bg-white border-b border-gray-100 flex-shrink-0 text-xs text-gray-400">
        {loading
          ? <><span className="animate-spin">⟳</span> Carregando…</>
          : <><strong className="text-gray-700">{total.toLocaleString('pt-BR')}</strong> contato{total !== 1 ? 's' : ''}</>
        }
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {rows.length === 0 && !loading ? (
          <div className="flex flex-col items-center justify-center h-full text-gray-400 gap-2">
            <span className="text-3xl">📂</span>
            <span className="text-sm">Nenhum contato encontrado com os filtros aplicados.</span>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-gray-50 border-b border-gray-200 z-10">
              <tr>
                {['Session ID','Canal','Pool','Origem','Destino','Iniciado','Encerrado','Duração','Status / Outcome','Segmentos'].map(col => (
                  <th key={col} className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5 whitespace-nowrap">
                    {col}
                  </th>
                ))}
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map(row => (
                <ContactRowItem key={row.session_id} row={row} onClick={() => onOpenDetail(row.session_id)} />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between px-4 py-2 bg-white border-t border-gray-200 flex-shrink-0 text-sm">
          <span className="text-gray-500 text-xs">Página {page} de {totalPages}</span>
          <div className="flex gap-1">
            <button disabled={page <= 1} onClick={() => changePage(page - 1)}
              className="px-3 py-1 rounded border border-gray-200 text-xs text-gray-600 disabled:opacity-40 hover:border-primary hover:text-primary transition-colors">
              ← Anterior
            </button>
            {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
              const start = Math.max(1, Math.min(page - 2, totalPages - 4))
              return start + i
            }).map(p => (
              <button key={p} onClick={() => changePage(p)}
                className={`px-3 py-1 rounded border text-xs transition-colors ${
                  p === page ? 'bg-primary text-white border-primary' : 'border-gray-200 text-gray-600 hover:border-primary hover:text-primary'
                }`}>
                {p}
              </button>
            ))}
            <button disabled={page >= totalPages} onClick={() => changePage(page + 1)}
              className="px-3 py-1 rounded border border-gray-200 text-xs text-gray-600 disabled:opacity-40 hover:border-primary hover:text-primary transition-colors">
              Próxima →
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Row ──────────────────────────────────────────────────────────────────────

function ContactRowItem({ row, onClick }: { row: ContactRow; onClick: () => void }) {
  const isActive = !row.closed_at
  const outcome  = row.outcome
  const outColor = outcome ? (OUTCOME_COLORS[outcome] ?? '#6b7280') : null
  const shortId  = row.session_id.length > 16 ? '…' + row.session_id.slice(-14) : row.session_id

  return (
    <tr onClick={onClick} className="hover:bg-primary/5 cursor-pointer transition-colors">
      <td className="px-4 py-3 font-mono text-xs text-gray-700 whitespace-nowrap">{shortId}</td>
      <td className="px-4 py-3 text-gray-600 whitespace-nowrap">
        {CHANNEL_ICONS[row.channel] ?? '⬡'} {row.channel}
      </td>
      <td className="px-4 py-3 text-gray-500 text-xs whitespace-nowrap max-w-[120px] truncate" title={row.pool_id ?? ''}>
        {row.pool_id?.replace(/_/g, ' ') ?? '—'}
      </td>
      <td className="px-4 py-3 text-gray-500 text-xs whitespace-nowrap tabular-nums">
        {row.ani ? <span className="font-mono">{row.ani}</span> : <span className="text-gray-300">—</span>}
      </td>
      <td className="px-4 py-3 text-gray-500 text-xs whitespace-nowrap tabular-nums">
        {row.dnis ? <span className="font-mono">{row.dnis}</span> : <span className="text-gray-300">—</span>}
      </td>
      <td className="px-4 py-3 text-gray-500 text-xs tabular-nums whitespace-nowrap">{formatDt(row.opened_at)}</td>
      <td className="px-4 py-3 text-gray-500 text-xs tabular-nums whitespace-nowrap">
        {isActive ? <span className="text-green-600 font-medium">Em andamento</span> : formatDt(row.closed_at)}
      </td>
      <td className="px-4 py-3 text-gray-700 tabular-nums whitespace-nowrap text-xs">{formatMs(row.handle_time_ms)}</td>
      <td className="px-4 py-3 whitespace-nowrap">
        {isActive ? (
          <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full bg-green-100 text-green-700">
            <span className="w-1.5 h-1.5 rounded-full bg-green-500 inline-block" /> ativo
          </span>
        ) : outcome && outColor ? (
          <span className="inline-block text-xs font-semibold px-2 py-0.5 rounded-full"
            style={{ backgroundColor: outColor + '20', color: outColor }}>
            {outcome}
          </span>
        ) : <span className="text-gray-400 text-xs">—</span>}
      </td>
      <td className="px-4 py-3 text-center">
        {row.segment_count > 0 ? (
          <span className="inline-block text-xs font-semibold px-2 py-0.5 rounded-full bg-blue-50 text-blue-600 tabular-nums">
            {row.segment_count}
          </span>
        ) : <span className="text-gray-300 text-xs">—</span>}
      </td>
      <td className="px-4 py-3 text-gray-400 text-right">›</td>
    </tr>
  )
}
