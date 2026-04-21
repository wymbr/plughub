/**
 * App.tsx
 * Root component: layout orchestration + three-level navigation state.
 *
 * Level 1 — Heatmap:
 *   ┌─────────────── Header ───────────────────────────────┐
 *   │ PlugHub Operator Console     [tenant input]  ● Live  │
 *   ├──────────────────────────────────────┬───────────────┤
 *   │                                      │               │
 *   │       HeatmapGrid                    │  MetricsPanel │
 *   │   (pool tiles, worst first)          │  (pool or     │
 *   │                                      │   24h summary)│
 *   └──────────────────────────────────────┴───────────────┘
 *
 * Level 2 — Session list (drill-down per pool):
 *   SessionList — active sessions sorted worst sentiment first
 *
 * Level 3 — Session transcript (per session):
 *   SessionTranscript — live SSE read-only XREAD stream
 */
import { useState } from 'react'
import { usePoolViews } from './api/hooks'
import { Header } from './components/Header'
import { HeatmapGrid } from './components/HeatmapGrid'
import { MetricsPanel } from './components/MetricsPanel'
import { SessionList } from './components/SessionList'
import { SessionTranscript } from './components/SessionTranscript'

// Default tenant from env var (override in .env.local)
const DEFAULT_TENANT = import.meta.env.VITE_DEFAULT_TENANT ?? 'tenant_telco'

type View = 'heatmap' | 'sessions' | 'transcript'

export default function App() {
  const [tenantId, setTenantId]           = useState<string>(DEFAULT_TENANT)
  const [selectedPoolId, setSelectedPool] = useState<string | null>(null)
  const [selectedSession, setSession]     = useState<string | null>(null)
  const [view, setView]                   = useState<View>('heatmap')

  const { pools, status, metrics } = usePoolViews(tenantId)

  const selectedPool = pools.find(p => p.pool_id === selectedPoolId) ?? null
  const showPanel    = view === 'heatmap' && (selectedPool !== null || metrics !== null)

  // ── Navigation helpers ────────────────────────────────────────────────────

  function selectPool(poolId: string | null) {
    setSelectedPool(poolId)
    setView('heatmap')
  }

  function drillIntoPool(poolId: string) {
    setSelectedPool(poolId)
    setSession(null)
    setView('sessions')
  }

  function openTranscript(sessionId: string) {
    setSession(sessionId)
    setView('transcript')
  }

  function backToHeatmap() {
    setView('heatmap')
    setSession(null)
  }

  function backToSessions() {
    setView('sessions')
    setSession(null)
  }

  function changeTenant(id: string) {
    setTenantId(id)
    setSelectedPool(null)
    setSession(null)
    setView('heatmap')
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <Header
        tenantId       ={tenantId}
        onTenantChange ={changeTenant}
        status         ={status}
        poolCount      ={pools.length}
      />

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>

        {/* ── Level 1: Heatmap ────────────────────────────────────────────── */}
        {view === 'heatmap' && (
          <>
            <HeatmapGrid
              pools          ={pools}
              selectedPoolId ={selectedPoolId}
              onSelect       ={selectPool}
              onDrillDown    ={drillIntoPool}
            />
            {showPanel && (
              <MetricsPanel
                pool    ={selectedPool}
                metrics ={metrics}
                onClose ={() => setSelectedPool(null)}
              />
            )}
          </>
        )}

        {/* ── Level 2: Active sessions for a pool ─────────────────────────── */}
        {view === 'sessions' && selectedPoolId && (
          <SessionList
            tenantId ={tenantId}
            poolId   ={selectedPoolId}
            onSelect ={openTranscript}
            onBack   ={backToHeatmap}
          />
        )}

        {/* ── Level 3: Session transcript ─────────────────────────────────── */}
        {view === 'transcript' && selectedSession && (
          <SessionTranscript
            tenantId  ={tenantId}
            sessionId ={selectedSession}
            onBack    ={backToSessions}
          />
        )}

      </div>
    </div>
  )
}
