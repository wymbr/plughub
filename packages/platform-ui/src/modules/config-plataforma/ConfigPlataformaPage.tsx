/**
 * ConfigPlataformaPage — /config/platform
 *
 * Two tabs:
 *   Configuração  — namespace editor (config-api port 3600)
 *   Calendários   — holiday sets + calendar CRUD (calendar-api port 3700)
 *
 * The admin token for config mutations is entered inline and never persisted.
 */
import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import { NamespaceEditor } from './components/NamespaceEditor'
import { CalendarManager }  from './components/CalendarManager'

type Tab = 'config' | 'calendar'

export default function ConfigPlataformaPage() {
  const { t } = useTranslation('configPlataforma')
  const { tenantId } = useAuth()
  const orgId       = tenantId   // use tenantId as organization_id in single-tenant setup

  const [tab,        setTab]        = useState<Tab>('config')
  const [adminToken, setAdminToken] = useState('')
  const [showToken,  setShowToken]  = useState(false)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', backgroundColor: '#0a1628', color: '#e2e8f0', overflow: 'hidden' }}>
      {/* Page header */}
      <div style={pageHeaderStyle}>
        <div>
          <h1 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: '#e2e8f0' }}>⚙️ {t('title', { defaultValue: 'Platform Configuration' })}</h1>
          <p style={{ margin: '4px 0 0', fontSize: 13, color: '#64748b' }}>
            {t('description', { defaultValue: 'Platform settings and calendar management' })}
          </p>
        </div>

        {/* Admin token (for config mutations) */}
        {tab === 'config' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <label style={{ fontSize: 12, color: '#64748b' }}>{t('adminTokenLabel', { defaultValue: 'Admin Token:' })}</label>
            <input
              type={showToken ? 'text' : 'password'}
              value={adminToken}
              onChange={e => setAdminToken(e.target.value)}
              placeholder={t('adminTokenPlaceholder', { defaultValue: 'To enable editing' })}
              style={{
                background: '#0f172a', border: '1px solid #334155', borderRadius: 6,
                color: '#e2e8f0', fontSize: 12, padding: '4px 10px', outline: 'none',
                width: 180,
              }}
            />
            <button
              style={{ background: 'none', border: 'none', color: '#64748b', cursor: 'pointer', fontSize: 14, padding: '0 4px' }}
              onClick={() => setShowToken(v => !v)}
              title={showToken ? t('hideToken', { defaultValue: 'Hide' }) : t('showToken', { defaultValue: 'Show' })}
            >
              {showToken ? '🙈' : '👁'}
            </button>
            {adminToken && <span style={{ fontSize: 11, color: '#22c55e', fontWeight: 600 }}>✓ {t('tokenSet', { defaultValue: 'token set' })}</span>}
          </div>
        )}
      </div>

      {/* Tab bar */}
      <div style={tabBarStyle}>
        <TabBtn active={tab === 'config'}   onClick={() => setTab('config')}>⚙️ {t('configTab', { defaultValue: 'Configuration' })}</TabBtn>
        <TabBtn active={tab === 'calendar'} onClick={() => setTab('calendar')}>📅 {t('calendarTab', { defaultValue: 'Calendars' })}</TabBtn>
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflow: 'hidden' }}>
        {tab === 'config'   && <NamespaceEditor tenantId={tenantId} adminToken={adminToken} />}
        {tab === 'calendar' && <CalendarManager orgId={orgId} tenantId={tenantId} />}
      </div>
    </div>
  )
}

function TabBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: '10px 20px', fontSize: 14, fontWeight: active ? 600 : 400,
        background: 'none', border: 'none',
        borderBottom: active ? '2px solid #3b82f6' : '2px solid transparent',
        color: active ? '#93c5fd' : '#64748b', cursor: 'pointer',
      }}
    >
      {children}
    </button>
  )
}

const pageHeaderStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
  padding: '16px 24px', borderBottom: '1px solid #1e293b',
  backgroundColor: '#0a1628', flexShrink: 0,
}

const tabBarStyle: React.CSSProperties = {
  display: 'flex', borderBottom: '1px solid #1e293b',
  backgroundColor: '#0a1628', flexShrink: 0,
}
