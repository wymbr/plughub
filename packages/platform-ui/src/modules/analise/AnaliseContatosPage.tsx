/**
 * AnaliseContatosPage — /analise/contatos
 *
 * Aggregated contact metrics for the selected period.
 * Migrated from ContactsPage Analysis tab.
 */
import React, { useState } from 'react'
import { useAuth } from '@/auth/useAuth'
import { AnaliseTab } from '@/modules/contacts/tabs/AnaliseTab'
import type { ContactFilters } from '@/modules/contacts/types'
import { DEFAULT_FILTERS } from '@/modules/contacts/types'

// ── Period selector ───────────────────────────────────────────────────────────

type Period = 'day' | 'week' | 'month' | 'year'

function buildDateRange(period: Period, customFrom?: string, customTo?: string): { fromDt: string; toDt: string } {
  const today = new Date()
  const fmt   = (d: Date) => d.toISOString().slice(0, 10)
  const toDt  = customTo  ?? fmt(today)

  if (customFrom) return { fromDt: customFrom, toDt }

  const from = new Date(today)
  if (period === 'day')   { from.setDate(today.getDate() - 1) }
  else if (period === 'week')  { from.setDate(today.getDate() - 7) }
  else if (period === 'month') { from.setMonth(today.getMonth() - 1) }
  else if (period === 'year')  { from.setFullYear(today.getFullYear() - 1) }
  return { fromDt: fmt(from), toDt }
}

const PERIOD_LABELS: Record<Period, string> = {
  day:   'Dia',
  week:  'Semana',
  month: 'Mês',
  year:  'Ano',
}

const inp = 'text-sm border border-border-strong rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/30 bg-white'

// ── AnaliseContatosPage ───────────────────────────────────────────────────────

export default function AnaliseContatosPage() {
  const { tenantId } = useAuth()
  const [period,    setPeriod]    = useState<Period>('week')
  const [customFrom, setCustomFrom] = useState('')
  const [customTo,   setCustomTo]   = useState('')
  const [poolId,    setPoolId]    = useState('')
  const [channel,   setChannel]   = useState('')

  const { fromDt, toDt } = buildDateRange(period, customFrom || undefined, customTo || undefined)

  const filters: ContactFilters = {
    ...DEFAULT_FILTERS,
    fromDt,
    toDt,
    poolId,
    channel,
  }

  if (!tenantId) return (
    <div className="flex items-center justify-center h-full text-muted-light text-sm">
      Nenhum tenant selecionado.
    </div>
  )

  return (
    <div className="flex flex-col h-full overflow-hidden bg-surface-muted">
      {/* Header + filters */}
      <div className="bg-white border-b border-border px-4 py-2.5 flex-shrink-0">
        <div className="flex items-center gap-3 flex-wrap">
          <span className="font-bold text-dark text-base">Contatos</span>

          {/* Period selector */}
          <div className="flex items-center gap-0.5 bg-surface-alt rounded-lg p-0.5 ml-2">
            {(Object.keys(PERIOD_LABELS) as Period[]).map(p => (
              <button key={p} onClick={() => { setPeriod(p); setCustomFrom(''); setCustomTo('') }}
                className="px-3 py-1 rounded-md text-xs font-medium transition-all"
                style={{
                  backgroundColor: period === p && !customFrom ? '#fff' : 'transparent',
                  color:           period === p && !customFrom ? '#1B4F8A' : '#6b7280',
                  boxShadow:       period === p && !customFrom ? '0 1px 3px rgba(0,0,0,0.12)' : 'none',
                }}>
                {PERIOD_LABELS[p]}
              </button>
            ))}
          </div>

          {/* Custom date range */}
          <div className="flex items-center gap-1.5 text-xs text-muted">
            <span>De</span>
            <input type="date" value={customFrom || fromDt}
              onChange={e => setCustomFrom(e.target.value)}
              className={`${inp} text-xs py-1`} />
            <span>Até</span>
            <input type="date" value={customTo || toDt}
              onChange={e => setCustomTo(e.target.value)}
              className={`${inp} text-xs py-1`} />
          </div>

          {/* Additional filters */}
          <select value={channel} onChange={e => setChannel(e.target.value)} className={`${inp} text-xs py-1`}>
            <option value="">Todos os canais</option>
            {['webchat','whatsapp','voice','email','sms'].map(c => <option key={c} value={c}>{c}</option>)}
          </select>

          <input type="text" value={poolId} onChange={e => setPoolId(e.target.value)}
            placeholder="Pool ID"
            className={`${inp} text-xs py-1 w-32`} />
        </div>
      </div>

      <div className="flex-1 overflow-hidden">
        <AnaliseTab tenantId={tenantId} filters={filters} />
      </div>
    </div>
  )
}
