/**
 * Header.tsx
 * Top navigation bar with connection status, tenant selector, and view navigation.
 */
import type { ConnectionStatus } from '../types'

interface Props {
  tenantId:   string
  onTenantChange: (id: string) => void
  status:     ConnectionStatus
  poolCount:  number
  currentView?: 'heatmap' | 'sessions' | 'transcript' | 'workflows' | 'campaigns' | 'config' | 'pricing' | 'webhooks' | 'registry' | 'skills' | 'channels' | 'agents'
  onViewChange?: (view: 'heatmap' | 'workflows' | 'campaigns' | 'config' | 'pricing' | 'webhooks' | 'registry' | 'skills' | 'channels' | 'agents') => void
}

const STATUS_DOT: Record<ConnectionStatus, { color: string; label: string }> = {
  connecting: { color: '#fbbf24', label: 'Connecting' },
  connected:  { color: '#22c55e', label: 'Live'       },
  error:      { color: '#ef4444', label: 'Error'      },
  closed:     { color: '#475569', label: 'Closed'     },
}

export function Header({ tenantId, onTenantChange, status, poolCount, currentView, onViewChange }: Props) {
  const dot = STATUS_DOT[status]
  const isWorkflowView  = currentView === 'workflows'
  const isCampaignView  = currentView === 'campaigns'
  const isConfigView    = currentView === 'config'
  const isPricingView   = currentView === 'pricing'
  const isWebhooksView  = currentView === 'webhooks'
  const isRegistryView  = currentView === 'registry'
  const isSkillsView    = currentView === 'skills'
  const isChannelsView  = currentView === 'channels'
  const isAgentsView    = currentView === 'agents'
  const isHeatmapActive = !isWorkflowView && !isCampaignView && !isConfigView && !isPricingView && !isWebhooksView && !isRegistryView && !isSkillsView && !isChannelsView && !isAgentsView

  const navBtn = (
    view: 'heatmap' | 'workflows' | 'campaigns' | 'config' | 'pricing' | 'webhooks' | 'registry' | 'skills' | 'channels' | 'agents',
    label: string,
    active: boolean,
    activeColor: string,
    activeBg: string,
  ) => (
    <button
      onClick={() => onViewChange?.(view)}
      style={{
        padding: '4px 12px',
        borderRadius: 4,
        border: active ? `1px solid ${activeColor}` : '1px solid #334155',
        background: active ? activeBg : 'transparent',
        color: '#e2e8f0',
        cursor: 'pointer',
        fontSize: 12,
        fontWeight: active ? 600 : 400,
      }}
    >
      {label}
    </button>
  )

  return (
    <header style={headerStyle}>
      {/* Brand */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div style={logoStyle}>PH</div>
        <div>
          <div style={{ fontSize: 15, fontWeight: 700, color: '#e2e8f0' }}>
            Operator Console
          </div>
          <div style={{ fontSize: 11, color: '#475569' }}>PlugHub Platform</div>
        </div>
      </div>

      {/* Center: navigation and pool count */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        {navBtn('heatmap',   'Heatmap',   isHeatmapActive, '#3b82f6', '#0d47a1')}
        {navBtn('workflows', 'Workflows', isWorkflowView,  '#7c3aed', '#4c1d95')}
        {navBtn('campaigns', 'Campaigns', isCampaignView,  '#0891b2', '#164e63')}
        {navBtn('webhooks',  'Webhooks',  isWebhooksView,  '#6366f1', '#1e1b4b')}
        {navBtn('registry',  'Registry',  isRegistryView,  '#f97316', '#431407')}
        {navBtn('skills',    'Skills',    isSkillsView,    '#a78bfa', '#2e1065')}
        {navBtn('channels',  'Channels',  isChannelsView,  '#14b8a6', '#042f2e')}
        {navBtn('agents',    'Agents',    isAgentsView,    '#10b981', '#022c22')}
        {navBtn('config',    'Config',    isConfigView,    '#84cc16', '#1a2e05')}
        {navBtn('pricing',   'Pricing',   isPricingView,   '#f59e0b', '#451a03')}
        <div style={{ fontSize: 13, color: '#64748b', whiteSpace: 'nowrap' }}>
          {isHeatmapActive && (poolCount > 0
            ? <><span style={{ fontWeight: 700, color: '#94a3b8' }}>{poolCount}</span> pools monitored</>
            : 'No pools active'
          )}
        </div>
      </div>

      {/* Right: tenant + status */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        {/* Tenant selector */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <label style={{ fontSize: 11, color: '#475569', fontWeight: 600 }}>
            TENANT
          </label>
          <input
            value={tenantId}
            onChange={e => onTenantChange(e.target.value)}
            style={inputStyle}
            placeholder="tenant_id"
            spellCheck={false}
          />
        </div>

        {/* SSE status */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{
            width:        8,
            height:       8,
            borderRadius: '50%',
            background:   dot.color,
            display:      'inline-block',
            boxShadow:    status === 'connected' ? `0 0 6px ${dot.color}` : 'none',
          }} />
          <span style={{ fontSize: 12, color: '#64748b' }}>{dot.label}</span>
        </div>
      </div>
    </header>
  )
}

const headerStyle: React.CSSProperties = {
  height:         56,
  background:     '#0d1117',
  borderBottom:   '1px solid #1e293b',
  display:        'flex',
  alignItems:     'center',
  justifyContent: 'space-between',
  padding:        '0 24px',
  flexShrink:     0,
  gap:            16,
}

const logoStyle: React.CSSProperties = {
  width:          32,
  height:         32,
  borderRadius:   8,
  background:     'linear-gradient(135deg, #3b82f6, #2563eb)',
  display:        'flex',
  alignItems:     'center',
  justifyContent: 'center',
  fontSize:       13,
  fontWeight:     800,
  color:          '#fff',
  letterSpacing:  '0.5px',
}

const inputStyle: React.CSSProperties = {
  background:   '#1e293b',
  border:       '1px solid #334155',
  borderRadius: 6,
  color:        '#e2e8f0',
  fontSize:     12,
  padding:      '4px 10px',
  outline:      'none',
  width:        160,
  fontFamily:   'monospace',
}

import type React from 'react'
