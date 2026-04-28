/**
 * ReportsPage.tsx
 * /evaluation/reports — Relatórios por campanha, agente e comparativo
 */

import React, { useState } from 'react'
import {
  useCampaigns,
  useCampaignReport,
  useAgentReport,
} from '@/api/evaluation-hooks'
import type { AgentEvaluationReport } from '@/types'

const TENANT = import.meta.env.VITE_TENANT_ID ?? 'tenant_demo'

function ScorePill({ score }: { score: number | null }) {
  if (score === null) return <span className="text-gray-400 text-xs">—</span>
  const bg = score >= 8 ? 'bg-green-100 text-green-800' : score >= 6 ? 'bg-yellow-100 text-yellow-800' : 'bg-red-100 text-red-800'
  return <span className={`px-2 py-0.5 rounded text-sm font-bold ${bg}`}>{score.toFixed(1)}</span>
}

function ProgressBar({ pct }: { pct: number }) {
  const clamp = Math.max(0, Math.min(100, pct))
  return (
    <div className="w-full bg-gray-200 rounded-full h-1.5">
      <div className="bg-primary h-1.5 rounded-full" style={{ width: `${clamp}%` }} />
    </div>
  )
}

// ── CampaignTab ────────────────────────────────────────────────────────────────

function CampaignTab() {
  const { campaigns } = useCampaigns(TENANT)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const { report, loading } = useCampaignReport(selectedId)

  return (
    <div className="flex gap-6">
      {/* Campaign selector */}
      <div className="w-64 shrink-0">
        <label className="block text-xs text-gray-500 mb-1">Campanha</label>
        <select
          className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm"
          value={selectedId ?? ''}
          onChange={e => setSelectedId(e.target.value || null)}
        >
          <option value="">Selecione…</option>
          {campaigns.map(c => (
            <option key={c.campaign_id} value={c.campaign_id}>{c.name}</option>
          ))}
        </select>
      </div>

      {/* Report */}
      <div className="flex-1">
        {!selectedId && (
          <div className="text-gray-400 text-sm text-center py-12">
            Selecione uma campanha para ver o relatório
          </div>
        )}
        {selectedId && loading && <div className="text-gray-400 text-sm py-4">Carregando…</div>}
        {report && (
          <div className="space-y-6">
            <div className="grid grid-cols-5 gap-3">
              {[
                { label: 'Total', value: report.total, color: 'text-gray-700' },
                { label: 'Concluídas', value: report.completed, color: 'text-green-700' },
                { label: 'Pendentes', value: report.pending, color: 'text-yellow-700' },
                { label: 'Em revisão', value: report.in_review, color: 'text-blue-700' },
                { label: 'Expiradas', value: report.expired, color: 'text-red-600' },
              ].map(k => (
                <div key={k.label} className="bg-gray-50 rounded p-3 text-center border">
                  <div className={`text-2xl font-bold ${k.color}`}>{k.value}</div>
                  <div className="text-xs text-gray-500 mt-1">{k.label}</div>
                </div>
              ))}
            </div>

            <div className="bg-white border rounded p-4 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-sm font-semibold text-gray-700">Conclusão</span>
                <span className="text-sm font-bold text-primary">{report.completion_pct.toFixed(1)}%</span>
              </div>
              <ProgressBar pct={report.completion_pct} />
            </div>

            <div className="grid grid-cols-3 gap-4">
              <div className="bg-white border rounded p-4 text-center">
                <div className="text-xs text-gray-500 mb-1">Nota média</div>
                <ScorePill score={report.avg_score} />
              </div>
              <div className="bg-white border rounded p-4 text-center">
                <div className="text-xs text-gray-500 mb-1">P25</div>
                <ScorePill score={report.score_p25} />
              </div>
              <div className="bg-white border rounded p-4 text-center">
                <div className="text-xs text-gray-500 mb-1">P75</div>
                <ScorePill score={report.score_p75} />
              </div>
            </div>

            {report.top_flags?.length > 0 && (
              <div className="bg-white border rounded p-4">
                <div className="text-sm font-semibold text-gray-700 mb-2">Flags mais frequentes</div>
                <div className="flex flex-wrap gap-2">
                  {report.top_flags.map(f => (
                    <span key={f} className="bg-red-50 text-red-700 text-xs px-2 py-1 rounded border border-red-100">{f}</span>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ── AgentTab ──────────────────────────────────────────────────────────────────

function AgentTab() {
  const [poolFilter, setPoolFilter] = useState('')
  const { rows, loading } = useAgentReport(TENANT, poolFilter || undefined)

  return (
    <div className="space-y-4">
      <div>
        <label className="block text-xs text-gray-500 mb-1">Filtrar por pool</label>
        <input
          className="border border-gray-300 rounded px-2 py-1.5 text-sm w-48"
          placeholder="Pool ID (opcional)"
          value={poolFilter}
          onChange={e => setPoolFilter(e.target.value)}
        />
      </div>

      {loading && <div className="text-gray-400 text-sm">Carregando…</div>}

      {!loading && rows.length === 0 && (
        <div className="text-center text-gray-400 py-8">Sem dados de avaliação por agente</div>
      )}

      {rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="bg-gray-50">
                <th className="text-left px-3 py-2 text-xs font-semibold text-gray-600 border-b">Agente</th>
                <th className="text-left px-3 py-2 text-xs font-semibold text-gray-600 border-b">Pool</th>
                <th className="text-center px-3 py-2 text-xs font-semibold text-gray-600 border-b">Sessões</th>
                <th className="text-center px-3 py-2 text-xs font-semibold text-gray-600 border-b">Avaliadas</th>
                <th className="text-center px-3 py-2 text-xs font-semibold text-gray-600 border-b">Nota média</th>
                <th className="text-left px-3 py-2 text-xs font-semibold text-gray-600 border-b">Principais melhorias</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r: AgentEvaluationReport) => (
                <tr key={`${r.agent_type_id}-${r.pool_id}`} className="border-b hover:bg-gray-50">
                  <td className="px-3 py-2 font-mono text-xs text-gray-600">{r.agent_type_id}</td>
                  <td className="px-3 py-2 text-gray-600">{r.pool_id}</td>
                  <td className="px-3 py-2 text-center text-gray-700">{r.total_sessions}</td>
                  <td className="px-3 py-2 text-center text-gray-700">{r.evaluated}</td>
                  <td className="px-3 py-2 text-center"><ScorePill score={r.avg_score} /></td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      {r.top_improvement?.slice(0, 2).map((t, i) => (
                        <span key={i} className="bg-orange-50 text-orange-700 text-xs px-1.5 py-0.5 rounded max-w-32 truncate">{t}</span>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── ReportsPage ────────────────────────────────────────────────────────────────

const TABS = [
  { id: 'campaign', label: '📋 Por campanha' },
  { id: 'agent',    label: '👤 Por agente' },
]

export default function ReportsPage() {
  const [tab, setTab] = useState('campaign')

  return (
    <div className="flex flex-col h-full">
      {/* Tab bar */}
      <div className="flex gap-1 border-b px-4 bg-white">
        {TABS.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-3 text-sm transition-colors border-b-2 -mb-px ${
              tab === t.id
                ? 'border-primary text-primary font-medium'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto p-6">
        {tab === 'campaign' && <CampaignTab />}
        {tab === 'agent' && <AgentTab />}
      </div>
    </div>
  )
}
