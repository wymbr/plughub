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
import { WorkflowPanel } from './components/WorkflowPanel'
import { CampaignPanel } from './components/CampaignPanel'
import { ConfigPanel } from './components/ConfigPanel'
import { PricingPanel } from './components/PricingPanel'
import { WebhookPanel } from './components/WebhookPanel'
import { RegistryPanel } from './components/RegistryPanel'
import { SkillFlowEditor } from './components/SkillFlowEditor'
import { ChannelPanel }     from './components/ChannelPanel'
import { HumanAgentPanel } from './components/HumanAgentPanel'

// Default tenant from env var (override in .env.local)
const DEFAULT_TENANT = import.meta.env.VITE_DEFAULT_TENANT ?? 'tenant_telco'

type View = 'heatmap' | 'sessions' | 'transcript' | 'workflows' | 'campaigns' | 'config' | 'pricing' | 'webhooks' | 'registry' | 'skills' | 'channels' | 'agents'

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

  function goToWorkflows() {
    setView('workflows')
    setSelectedPool(null)
    setSession(null)
  }

  function goToCampaigns() {
    setView('campaigns')
    setSelectedPool(null)
    setSession(null)
  }

  function goToConfig() {
    setView('config')
    setSelectedPool(null)
    setSession(null)
  }

  function goToPricing() {
    setView('pricing')
    setSelectedPool(null)
    setSession(null)
  }

  function goToWebhooks() {
    setView('webhooks')
    setSelectedPool(null)
    setSession(null)
  }

  function goToRegistry() {
    setView('registry')
    setSelectedPool(null)
    setSession(null)
  }

  function goToSkills() {
    setView('skills')
    setSelectedPool(null)
    setSession(null)
  }

  function goToChannels() {
    setView('channels')
    setSelectedPool(null)
    setSession(null)
  }

  function goToAgents() {
    setView('agents')
    setSelectedPool(null)
    setSession(null)
  }

  function backToHeatmapFromWorkflows() {
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
        currentView    ={view}
        onViewChange   ={(v) => {
          if (v === 'workflows') goToWorkflows()
          else if (v === 'campaigns') goToCampaigns()
          else if (v === 'config') goToConfig()
          else if (v === 'pricing') goToPricing()
          else if (v === 'webhooks') goToWebhooks()
          else if (v === 'registry') goToRegistry()
          else if (v === 'skills')    goToSkills()
          else if (v === 'channels')  goToChannels()
          else if (v === 'agents')    goToAgents()
          else if (v === 'heatmap') {
            setView('heatmap')
            setSelectedPool(null)
            setSession(null)
          }
        }}
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

        {/* ── Workflows panel ─────────────────────────────────────────────── */}
        {view === 'workflows' && (
          <WorkflowPanel
            tenantId ={tenantId}
            onBack   ={backToHeatmapFromWorkflows}
          />
        )}

        {/* ── Campaigns panel ─────────────────────────────────────────────── */}
        {view === 'campaigns' && (
          <CampaignPanel
            tenantId ={tenantId}
            onBack   ={() => setView('heatmap')}
          />
        )}

        {/* ── Config panel ─────────────────────────────────────────────────── */}
        {view === 'config' && (
          <ConfigPanel
            tenantId ={tenantId}
            onBack   ={() => setView('heatmap')}
          />
        )}

        {/* ── Pricing panel ────────────────────────────────────────────────── */}
        {view === 'pricing' && (
          <PricingPanel
            tenantId ={tenantId}
            onBack   ={() => setView('heatmap')}
          />
        )}

        {/* ── Webhooks panel ───────────────────────────────────────────────── */}
        {view === 'webhooks' && (
          <WebhookPanel
            tenantId ={tenantId}
            onBack   ={() => setView('heatmap')}
          />
        )}

        {/* ── Registry Management panel ────────────────────────────────────── */}
        {view === 'registry' && (
          <RegistryPanel
            tenantId ={tenantId}
            onBack   ={() => setView('heatmap')}
          />
        )}

        {/* ── Skill Flow Editor ────────────────────────────────────────────── */}
        {view === 'skills' && (
          <SkillFlowEditor
            tenantId ={tenantId}
            onBack   ={() => setView('heatmap')}
          />
        )}

        {/* ── Channel Configuration panel ──────────────────────────────────── */}
        {view === 'channels' && (
          <ChannelPanel
            tenantId ={tenantId}
            onBack   ={() => setView('heatmap')}
          />
        )}

        {/* ── Human Agent Management panel ─────────────────────────────────── */}
        {view === 'agents' && (
          <HumanAgentPanel
            tenantId ={tenantId}
            onBack   ={() => setView('heatmap')}
          />
        )}

      </div>
    </div>
  )
}
