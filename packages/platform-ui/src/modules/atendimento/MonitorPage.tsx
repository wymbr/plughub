/**
 * MonitorPage — /monitor
 *
 * 3-level drill-down:
 *   Level 0: Heatmap grid (all pools)
 *   Level 1: Session list (single pool)
 *   Level 2: Session transcript (single session)
 *
 * Uses tenant ID from auth session — no manual config input needed.
 */
import { useState } from 'react'
import type React from 'react'
import { useAuth } from '@/auth/useAuth'
import { usePoolViews } from './api/hooks'
import { HeatmapGrid }      from './components/HeatmapGrid'
import { MetricsPanel }     from './components/MetricsPanel'
import { SessionList }      from './components/SessionList'
import { SessionTranscript } from './components/SessionTranscript'
import HumanAgentsPage from '@/modules/config-recursos/HumanAgentsPage'
import InstancesPage   from '@/modules/config-recursos/InstancesPage'
import type { PoolView } from './types'

type MonitorTab = 'heatmap' | 'agentes' | 'instancias'
type Level = 'pools' | 'sessions' | 'transcript'

export default function MonitorPage() {
  const { tenantId } = useAuth()

  const { pools, status, metrics } = usePoolViews(tenantId)

  const [monitorTab,     setMonitorTab]     = useState<MonitorTab>('heatmap')
  const [level,          setLevel]          = useState<Level>('pools')
  const [selectedPool,   setSelectedPool]   = useState<PoolView | null>(null)
  const [selectedPoolId, setSelectedPoolId] = useState<string | null>(null)
  const [sessionId,      setSessionId]      = useState<string | null>(null)
  const [showMetrics,    setShowMetrics]    = useState(false)

  function handlePoolSelect(poolId: string | null) {
    setSelectedPoolId(poolId)
    setSelectedPool(poolId ? (pools.find(p => p.pool_id === poolId) ?? null) : null)
    setShowMetrics(poolId !== null)
  }

  function handleDrillDown(poolId: string) {
    setSelectedPoolId(poolId)
    setSelectedPool(pools.find(p => p.pool_id === poolId) ?? null)
    setLevel('sessions')
  }

  function handleSessionSelect(sid: string) {
    setSessionId(sid)
    setLevel('transcript')
  }

  function handleBackToSessions() {
    setSessionId(null)
    setLevel('sessions')
  }

  function handleBackToPools() {
    setSelectedPool(null)
    setSelectedPoolId(null)
    setSessionId(null)
    setLevel('pools')
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  if (!tenantId) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#64748b', fontSize: 14 }}>
        Tenant não configurado na sessão. Faça login novamente.
      </div>
    )
  }

  const monitorTabs: { id: MonitorTab; label: string; icon: string }[] = [
    { id: 'heatmap',    label: 'Heatmap',   icon: '🔥' },
    { id: 'agentes',    label: 'Agentes',   icon: '👥' },
    { id: 'instancias', label: 'Instâncias', icon: '⚡' },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', backgroundColor: monitorTab === 'heatmap' ? '#0f172a' : '#f8fafc' }}>

      {/* Monitor tab bar */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 0,
        borderBottom: monitorTab === 'heatmap' ? '1px solid #1e293b' : '1px solid #e2e8f0',
        backgroundColor: monitorTab === 'heatmap' ? '#0a1628' : '#ffffff',
        flexShrink: 0,
        paddingLeft: 8,
      }}>
        {monitorTabs.map(tab => (
          <button
            key={tab.id}
            onClick={() => setMonitorTab(tab.id)}
            style={{
              fontSize: 13,
              fontWeight: monitorTab === tab.id ? 600 : 400,
              padding: '10px 18px',
              cursor: 'pointer',
              border: 'none',
              borderBottom: monitorTab === tab.id
                ? (tab.id === 'heatmap' ? '2px solid #60a5fa' : '2px solid #1B4F8A')
                : '2px solid transparent',
              background: 'transparent',
              color: monitorTab === tab.id
                ? (tab.id === 'heatmap' ? '#93c5fd' : '#1B4F8A')
                : (tab.id === 'heatmap' ? '#64748b' : '#6b7280'),
              transition: 'all 0.15s',
              display: 'flex', alignItems: 'center', gap: 6,
            }}
          >
            <span>{tab.icon}</span>
            <span>{tab.label}</span>
          </button>
        ))}
      </div>

      {/* Heatmap tab */}
      {monitorTab === 'heatmap' && (
        <>
          {/* Top status bar */}
          <div style={topBarStyle}>
            <Breadcrumb level={level} poolId={selectedPoolId} sessionId={sessionId} onPools={handleBackToPools} onSessions={handleBackToSessions} />
            <ConnectionPill status={status} />
            <span style={{ marginLeft: 'auto', fontSize: 11, color: '#475569' }}>
              tenant: <code style={{ color: '#94a3b8' }}>{tenantId}</code>
            </span>
            {level === 'pools' && (
              <button
                style={{ fontSize: 12, background: showMetrics ? '#1e40af' : 'none', border: '1px solid #334155', color: showMetrics ? '#93c5fd' : '#64748b', borderRadius: 6, padding: '3px 10px', cursor: 'pointer' }}
                onClick={() => setShowMetrics(v => !v)}
                title="Mostrar painel de métricas"
              >
                📊 Métricas
              </button>
            )}
          </div>

          {/* Heatmap content */}
          <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
            <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
              {level === 'pools' && (
                <HeatmapGrid
                  pools={pools}
                  selectedPoolId={selectedPoolId}
                  onSelect={handlePoolSelect}
                  onDrillDown={handleDrillDown}
                />
              )}
              {level === 'sessions' && selectedPoolId && (
                <SessionList
                  tenantId={tenantId}
                  poolId={selectedPoolId}
                  onSelect={handleSessionSelect}
                  onBack={handleBackToPools}
                />
              )}
              {level === 'transcript' && sessionId && (
                <SessionTranscript
                  tenantId={tenantId}
                  sessionId={sessionId}
                  onBack={handleBackToSessions}
                />
              )}
            </div>

            {level === 'pools' && showMetrics && (
              <MetricsPanel
                pool={selectedPool}
                metrics={metrics}
                onClose={() => { setShowMetrics(false); setSelectedPoolId(null); setSelectedPool(null) }}
              />
            )}
          </div>
        </>
      )}

      {/* Agentes tab */}
      {monitorTab === 'agentes' && (
        <div style={{ flex: 1, overflow: 'auto', padding: '24px' }}>
          <HumanAgentsPage />
        </div>
      )}

      {/* Instâncias tab */}
      {monitorTab === 'instancias' && (
        <div style={{ flex: 1, overflow: 'auto', padding: '24px' }}>
          <InstancesPage />
        </div>
      )}
    </div>
  )
}

// ─── Breadcrumb ───────────────────────────────────────────────────────────────

function Breadcrumb({ level, poolId, sessionId, onPools, onSessions }: {
  level:      string
  poolId:     string | null
  sessionId:  string | null
  onPools:    () => void
  onSessions: () => void
}) {
  const crumbStyle: React.CSSProperties = { fontSize: 13, color: '#64748b', cursor: 'pointer' }
  const sepStyle:   React.CSSProperties = { fontSize: 13, color: '#334155', margin: '0 4px' }
  const activeStyle: React.CSSProperties = { fontSize: 13, color: '#e2e8f0', fontWeight: 600 }

  if (level === 'pools') return <span style={activeStyle}>Pools</span>

  if (level === 'sessions') return (
    <span style={{ display: 'flex', alignItems: 'center' }}>
      <span style={crumbStyle} onClick={onPools}>Pools</span>
      <span style={sepStyle}>/</span>
      <span style={activeStyle}>{poolId?.replace(/_/g, ' ')}</span>
    </span>
  )

  return (
    <span style={{ display: 'flex', alignItems: 'center' }}>
      <span style={crumbStyle} onClick={onPools}>Pools</span>
      <span style={sepStyle}>/</span>
      <span style={crumbStyle} onClick={onSessions}>{poolId?.replace(/_/g, ' ')}</span>
      <span style={sepStyle}>/</span>
      <code style={{ ...activeStyle, fontSize: 12, backgroundColor: '#1e293b', borderRadius: 4, padding: '1px 6px' }}>
        {sessionId ? (sessionId.length > 14 ? '…' + sessionId.slice(-12) : sessionId) : ''}
      </code>
    </span>
  )
}

// ─── ConnectionPill ───────────────────────────────────────────────────────────

function ConnectionPill({ status }: { status: string }) {
  const colors: Record<string, { bg: string; text: string; label: string }> = {
    connecting: { bg: '#451a03', text: '#fbbf24', label: 'conectando' },
    connected:  { bg: '#052e16', text: '#22c55e', label: 'conectado'  },
    error:      { bg: '#3f0e0e', text: '#ef4444', label: 'erro SSE'   },
    closed:     { bg: '#1e293b', text: '#64748b', label: 'fechado'    },
  }
  const c = colors[status] ?? colors.closed
  return (
    <span style={{ fontSize: 11, fontWeight: 600, backgroundColor: c.bg, color: c.text, borderRadius: 4, padding: '2px 8px', letterSpacing: '0.04em' }}>
      {c.label}
    </span>
  )
}

// ─── Styles ───────────────────────────────────────────────────────────────────

const topBarStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 10,
  padding: '10px 16px',
  borderBottom: '1px solid #1e293b',
  backgroundColor: '#0a1628',
  flexShrink: 0,
}
