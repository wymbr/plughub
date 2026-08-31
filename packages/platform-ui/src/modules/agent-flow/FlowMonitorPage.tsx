/**
 * FlowMonitorPage — /flow/monitor
 *
 * Real-time view of pool state: queue lengths, available agents,
 * sentiment scores, and drill-down into active sessions.
 *
 * Renders MonitorTab in sessions-only scope (Processos moved to /flow/processos).
 */
import React, { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import { listPools } from '@/api/registry'
import { MonitorTab } from '@/modules/contacts/tabs/MonitorTab'
import type { ContactFilters } from '@/modules/contacts/types'
import { DEFAULT_FILTERS } from '@/modules/contacts/types'
import type { Pool } from '@/types'

export default function FlowMonitorPage() {
  const { t } = useTranslation('contacts')
  const { tenantId, currentUser } = useAuth()
  // Minimal filter state — pool and channel are the only relevant filters here
  const [filters, setFilters] = useState<ContactFilters>(DEFAULT_FILTERS)
  const [pools,   setPools]   = useState<Pool[]>([])

  useEffect(() => {
    if (!tenantId) return
    listPools(tenantId)
      .then(res => {
        // Segurança Fase E — dropdown = domínio (listPools ∩ accessiblePools).
        // AUT-06 (2026-08-31): vazio = NENHUM, não "todos" — ver `AgentAssistContext`.
        const active = res.items.filter(p => p.status === 'active')
        const dom = currentUser?.accessiblePools ?? []
        setPools(active.filter(p => dom.includes(p.pool_id)))
      })
      .catch(() => setPools([]))
  }, [tenantId, currentUser])

  if (!tenantId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-light text-sm">
        Nenhum tenant selecionado.
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Compact pool filter row */}
      <div className="bg-white border-b border-border px-4 py-2 flex items-center gap-3 flex-shrink-0">
        <div className="flex items-center gap-2">
          <select
            value={filters.poolId}
            onChange={e => setFilters(f => ({ ...f, poolId: e.target.value }))}
            className="text-xs border border-border-strong rounded-lg px-2.5 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-primary/30">
            <option value="">{t('monitor.allPools')}</option>
            {pools.map(p => (
              <option key={p.pool_id} value={p.pool_id}>{p.pool_id}</option>
            ))}
          </select>
          <select
            value={filters.channel}
            onChange={e => setFilters(f => ({ ...f, channel: e.target.value }))}
            className="text-xs border border-border-strong rounded-lg px-2.5 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-primary/30">
            <option value="">{t('filter.allChannels')}</option>
            {['webchat','whatsapp','voice','email','sms','instagram','telegram','webrtc','webhook'].map(c => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="flex-1 overflow-hidden">
        <MonitorTab tenantId={tenantId} filters={filters} />
      </div>
    </div>
  )
}
