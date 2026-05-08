/**
 * AnaliseAgentesPage — /analise/agentes
 *
 * Agent availability heatmap + pause breakdown.
 * Migrated from ContactsPage Agents tab (AgentsTab).
 */
import React, { useState } from 'react'
import { useAuth } from '@/auth/useAuth'
import { AgentsTab } from '@/modules/contacts/tabs/AgentsTab'
import type { ContactFilters } from '@/modules/contacts/types'
import { DEFAULT_FILTERS } from '@/modules/contacts/types'

const inp = 'text-sm border border-gray-300 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/30 bg-white'

export default function AnaliseAgentesPage() {
  const { tenantId } = useAuth()
  const [fromDt,  setFromDt]  = useState(DEFAULT_FILTERS.fromDt)
  const [toDt,    setToDt]    = useState(DEFAULT_FILTERS.toDt)
  const [poolId,  setPoolId]  = useState('')
  const [agentId, setAgentId] = useState('')

  const filters: ContactFilters = {
    ...DEFAULT_FILTERS,
    fromDt,
    toDt,
    poolId,
    agentId,
  }

  if (!tenantId) return (
    <div className="flex items-center justify-center h-full text-gray-400 text-sm">
      Nenhum tenant selecionado.
    </div>
  )

  return (
    <div className="flex flex-col h-full overflow-hidden bg-gray-50">
      {/* Header + filters */}
      <div className="bg-white border-b border-gray-200 px-4 py-2.5 flex-shrink-0">
        <div className="flex items-center gap-3 flex-wrap">
          <span className="font-bold text-gray-800 text-base">Agentes</span>

          <div className="flex items-center gap-1.5 text-xs text-gray-500 ml-2">
            <span>De</span>
            <input type="date" value={fromDt} onChange={e => setFromDt(e.target.value)} className={`${inp} text-xs py-1`} />
            <span>Até</span>
            <input type="date" value={toDt}   onChange={e => setToDt(e.target.value)}   className={`${inp} text-xs py-1`} />
          </div>

          <input type="text" value={poolId} onChange={e => setPoolId(e.target.value)}
            placeholder="Pool ID"
            className={`${inp} text-xs py-1 w-32`} />

          <input type="text" value={agentId} onChange={e => setAgentId(e.target.value)}
            placeholder="Agent ID"
            className={`${inp} text-xs py-1 w-44`} />
        </div>
      </div>

      <div className="flex-1 overflow-hidden">
        <AgentsTab tenantId={tenantId} filters={filters} />
      </div>
    </div>
  )
}
