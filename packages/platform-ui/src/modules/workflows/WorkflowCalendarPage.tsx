/**
 * WorkflowCalendarPage — /workflows/calendar
 *
 * Two tabs:
 *   📅 Calendários — holiday sets, calendars, associations (from CalendarsPage)
 *   🔗 Webhooks    — webhook triggers (from WebhooksTab)
 */
import React, { useState } from 'react'
import CalendarsPage from '@/modules/calendars/CalendarsPage'
import WebhooksTab from './WebhooksTab'

type Tab = 'calendars' | 'webhooks'

const TABS: { key: Tab; label: string }[] = [
  { key: 'calendars', label: '📅 Calendários' },
  { key: 'webhooks',  label: '🔗 Webhooks'    },
]

export default function WorkflowCalendarPage() {
  const [activeTab, setActiveTab] = useState<Tab>('calendars')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {/* Tab bar */}
      <div style={{
        display: 'flex', alignItems: 'center',
        borderBottom: '1px solid #1e293b', flexShrink: 0,
        paddingLeft: 20, paddingRight: 20,
        backgroundColor: '#0a1628',
      }}>
        {TABS.map(t => (
          <button
            key={t.key}
            onClick={() => setActiveTab(t.key)}
            style={{
              padding: '10px 16px', fontSize: 13,
              fontWeight: activeTab === t.key ? 700 : 400,
              color: activeTab === t.key ? '#93c5fd' : '#64748b',
              background: 'none', border: 'none', cursor: 'pointer',
              borderBottom: activeTab === t.key ? '2px solid #93c5fd' : '2px solid transparent',
              marginBottom: -1,
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div style={{ flex: 1, overflow: 'hidden' }}>
        {activeTab === 'calendars' && <CalendarsPage />}
        {activeTab === 'webhooks'  && (
          <div style={{ display: 'flex', height: '100%', backgroundColor: '#0a1628' }}>
            <WebhooksTab />
          </div>
        )}
      </div>
    </div>
  )
}
