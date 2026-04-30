/**
 * AgentFlowReportPage — /agent-flow/report
 *
 * Analytics for AgentFlow (skill) executions:
 * outcome distribution, avg duration, escalation rate by skill.
 * Reads from analytics-api GET /reports/agents/performance.
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'

// ── Types ──────────────────────────────────────────────────────────────────────

interface AgentPerf {
  agent_type_id: string
  pool_id:       string
  role:          string
  total_sessions: number
  avg_duration_ms: number
  escalation_rate: number
  handoff_rate:    number
  outcomes:        Record<string, number>
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function authHeaders(token?: string | null): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) h['Authorization'] = `Bearer ${token}`
  return h
}

function fmtDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const m = Math.floor(ms / 60_000)
  const s = Math.round((ms % 60_000) / 1000)
  return `${m}m ${s}s`
}

function fmtPct(v: number): string {
  return `${(v * 100).toFixed(1)}%`
}

// ── Main page ──────────────────────────────────────────────────────────────────

export default function AgentFlowReportPage() {
  const { session, getAccessToken } = useAuth()
  const tenantId = session?.tenantId ?? ''

  const [rows,    setRows]    = useState<AgentPerf[]>([])
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)
  const [sortKey, setSortKey] = useState<keyof AgentPerf>('total_sessions')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [filter,  setFilter]  = useState('')

  const load = useCallback(async () => {
    if (!tenantId) return
    setLoading(true); setError(null)
    try {
      const token = await getAccessToken()
      const res = await fetch(`/reports/agents/performance?tenant_id=${tenantId}`, {
        headers: authHeaders(token),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const body = await res.json()
      const data: AgentPerf[] = Array.isArray(body) ? body : (body.data ?? [])
      setRows(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [tenantId])

  useEffect(() => { load() }, [load])

  const toggleSort = (key: keyof AgentPerf) => {
    if (sortKey === key) setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))
    else { setSortKey(key); setSortDir('desc') }
  }

  const filtered = rows
    .filter(r =>
      !filter ||
      r.agent_type_id.toLowerCase().includes(filter.toLowerCase()) ||
      r.pool_id.toLowerCase().includes(filter.toLowerCase())
    )
    .sort((a, b) => {
      const av = a[sortKey] as number
      const bv = b[sortKey] as number
      return sortDir === 'asc' ? av - bv : bv - av
    })

  // Summary KPIs
  const totalSessions = rows.reduce((s, r) => s + r.total_sessions, 0)
  const avgDuration   = rows.length > 0
    ? rows.reduce((s, r) => s + r.avg_duration_ms * r.total_sessions, 0) / (totalSessions || 1)
    : 0
  const avgEscalation = rows.length > 0
    ? rows.reduce((s, r) => s + r.escalation_rate, 0) / rows.length
    : 0

  return (
    <div style={page}>
      {/* Top bar */}
      <div style={topBar}>
        <div>
          <span style={{ fontWeight: 700, fontSize: 17, color: '#e2e8f0' }}>📊 Relatório de AgentFlow</span>
          <span style={{ marginLeft: 10, fontSize: 12, color: '#64748b' }}>
            {loading ? '⟳' : `${rows.length} skill(s)`}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input
            value={filter}
            onChange={e => setFilter(e.target.value)}
            placeholder="Filtrar por skill ou pool…"
            style={searchInput}
          />
          <button style={btnSecondary} onClick={load}>↻ Atualizar</button>
        </div>
      </div>

      {error && (
        <div style={{ padding: '10px 20px', background: '#7f1d1d', color: '#fca5a5', fontSize: 12 }}>
          Erro: {error}
        </div>
      )}

      {/* KPI bar */}
      <div style={kpiBar}>
        <KpiCard label="Total de sessões" value={totalSessions.toLocaleString('pt-BR')} color="#3b82f6" />
        <KpiCard label="Duração média"    value={fmtDuration(avgDuration)}              color="#22c55e" />
        <KpiCard label="Taxa de escalação" value={fmtPct(avgEscalation)}               color="#eab308" />
        <KpiCard label="Skills monitorados" value={rows.length.toString()}             color="#a78bfa" />
      </div>

      {/* Table */}
      <div style={{ flex: 1, overflow: 'auto', padding: '0 20px 20px' }}>
        {loading && rows.length === 0 && (
          <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><Spinner /></div>
        )}
        {!loading && rows.length === 0 && !error && (
          <div style={{ padding: '60px 24px', textAlign: 'center', color: '#475569', fontSize: 13 }}>
            Nenhum dado de performance disponível. Execute sessões de agentes para ver relatórios.
          </div>
        )}
        {filtered.length > 0 && (
          <table style={table}>
            <thead>
              <tr>
                {([
                  ['agent_type_id',  'Skill / Agente',    false],
                  ['pool_id',        'Pool',              false],
                  ['total_sessions', 'Sessões',           true],
                  ['avg_duration_ms','Duração média',     true],
                  ['escalation_rate','Taxa de escalação', true],
                  ['handoff_rate',   'Taxa de handoff',   true],
                ] as [keyof AgentPerf, string, boolean][]).map(([key, label, sortable]) => (
                  <th
                    key={key}
                    style={thStyle}
                    onClick={sortable ? () => toggleSort(key) : undefined}
                  >
                    {label}
                    {sortable && sortKey === key && (sortDir === 'asc' ? ' ↑' : ' ↓')}
                  </th>
                ))}
                <th style={thStyle}>Outcomes</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r, i) => (
                <tr key={`${r.agent_type_id}-${r.pool_id}`} style={{ background: i % 2 === 0 ? '#0f1f35' : 'transparent' }}>
                  <td style={tdStyle}>
                    <code style={{ color: '#93c5fd', fontSize: 12 }}>{r.agent_type_id}</code>
                  </td>
                  <td style={tdStyle}>
                    <span style={{ fontSize: 11, color: '#94a3b8' }}>{r.pool_id}</span>
                  </td>
                  <td style={{ ...tdStyle, textAlign: 'right' }}>{r.total_sessions.toLocaleString('pt-BR')}</td>
                  <td style={{ ...tdStyle, textAlign: 'right' }}>{fmtDuration(r.avg_duration_ms)}</td>
                  <td style={{ ...tdStyle, textAlign: 'right' }}>
                    <span style={{ color: r.escalation_rate > 0.2 ? '#f87171' : r.escalation_rate > 0.1 ? '#fbbf24' : '#4ade80' }}>
                      {fmtPct(r.escalation_rate)}
                    </span>
                  </td>
                  <td style={{ ...tdStyle, textAlign: 'right' }}>{fmtPct(r.handoff_rate)}</td>
                  <td style={tdStyle}>
                    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                      {Object.entries(r.outcomes ?? {}).map(([outcome, count]) => (
                        <span key={outcome} style={outcomePill(outcome)}>
                          {outcome}: {count}
                        </span>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function KpiCard({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ background: '#1e293b', borderRadius: 8, padding: '16px 20px', flex: 1, borderTop: `2px solid ${color}` }}>
      <div style={{ fontSize: 11, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 700, color, marginTop: 4 }}>{value}</div>
    </div>
  )
}

function outcomePill(outcome: string): React.CSSProperties {
  const colors: Record<string, [string, string]> = {
    resolved:   ['#22c55e', '#052e16'],
    escalated:  ['#f97316', '#1c0a00'],
    transferred:['#3b82f6', '#172554'],
    abandoned:  ['#6b7280', '#111827'],
    timeout:    ['#ef4444', '#450a0a'],
  }
  const [fg, bg] = colors[outcome] ?? ['#94a3b8', '#1e293b']
  return { fontSize: 10, padding: '1px 6px', borderRadius: 3, background: bg, color: fg, border: `1px solid ${fg}33` }
}

// ── Styles ─────────────────────────────────────────────────────────────────────

const page: React.CSSProperties       = { display: 'flex', flexDirection: 'column', height: '100%', backgroundColor: '#0a1628', color: '#e2e8f0', overflow: 'hidden' }
const topBar: React.CSSProperties     = { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 20px', borderBottom: '1px solid #1e293b', flexShrink: 0 }
const kpiBar: React.CSSProperties     = { display: 'flex', gap: 12, padding: '16px 20px', borderBottom: '1px solid #1e293b', flexShrink: 0 }
const table: React.CSSProperties      = { width: '100%', borderCollapse: 'collapse', fontSize: 13, marginTop: 12 }
const thStyle: React.CSSProperties    = { padding: '10px 12px', textAlign: 'left', fontSize: 11, fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #1e293b', cursor: 'pointer', whiteSpace: 'nowrap' }
const tdStyle: React.CSSProperties    = { padding: '10px 12px', borderBottom: '1px solid #1e293b', verticalAlign: 'middle' }
const searchInput: React.CSSProperties = { background: '#1e293b', border: '1px solid #334155', borderRadius: 6, padding: '5px 10px', color: '#e2e8f0', fontSize: 12, width: 220 }
const btnSecondary: React.CSSProperties = { background: 'none', border: '1px solid #334155', color: '#94a3b8', borderRadius: 6, padding: '5px 12px', cursor: 'pointer', fontSize: 12 }
