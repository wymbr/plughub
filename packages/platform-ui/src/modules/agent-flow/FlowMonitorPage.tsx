/**
 * FlowMonitorPage — /flow/monitor
 *
 * Real-time view of pool state: queue lengths, available agents,
 * sentiment scores, and drill-down into active sessions.
 *
 * Renders MonitorTab in sessions-only scope (Processos moved to /flow/processos).
 */
import React, { useState } from 'react'
import { useAuth } from '@/auth/useAuth'
import { MonitorTab } from '@/modules/contacts/tabs/MonitorTab'
import type { ContactFilters } from '@/modules/contacts/types'
import { DEFAULT_FILTERS } from '@/modules/contacts/types'

export default function FlowMonitorPage() {
  const { tenantId } = useAuth()
  // Minimal filter state — pool and channel are the only relevant filters here
  const [filters, setFilters] = useState<ContactFilters>(DEFAULT_FILTERS)

  if (!tenantId) {
    return (
      <div className="flex items-center justify-center h-full text-gray-400 text-sm">
        Nenhum tenant selecionado.
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Compact pool filter row */}
      <div className="bg-white border-b border-gray-200 px-4 py-2 flex items-center gap-3 flex-shrink-0">
        <span className="font-bold text-gray-800 text-base">Monitor</span>
        <div className="flex items-center gap-2 ml-4">
          <select
            value={filters.poolId}
            onChange={e => setFilters(f => ({ ...f, poolId: e.target.value }))}
            className="text-xs border border-gray-300 rounded-lg px-2.5 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-primary/30">
            <option value="">Todos os pools</option>
          </select>
          <select
            value={filters.channel}
            onChange={e => setFilters(f => ({ ...f, channel: e.target.value }))}
            className="text-xs border border-gray-300 rounded-lg px-2.5 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-primary/30">
            <option value="">Todos os canais</option>
            {['webchat','whatsapp','voice','email','sms'].map(c => (
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
