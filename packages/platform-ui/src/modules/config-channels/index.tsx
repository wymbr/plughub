/**
 * config-channels/index.tsx
 * Configuration → Channels — /config/channels
 *
 * Each channel tab has two inner sub-sections:
 *   1. Endpoints    — ChannelEndpoint records (mapping address → pool)
 *   2. Settings     — global defaults / credentials for the channel type
 *
 * Active channels:
 *   webchat  — endpoints + WebChatConfigPage for general settings
 *
 * Placeholder channels (endpoints available, settings coming soon):
 *   whatsapp, voice, email, sms
 */
import React, { useState } from 'react'
import PageHeader from '@/components/ui/PageHeader'
import { ChannelEndpointList } from './ChannelEndpointList'
import WebChatConfigPage from './WebChatConfigPage'
import type { ChannelEndpointChannel } from '@/types'

// ── Channel tabs ───────────────────────────────────────────────────────────────

type ChannelTab = ChannelEndpointChannel   // 'webchat' | 'whatsapp' | 'voice' | 'email' | 'sms'
type ChannelSubTab = 'endpoints' | 'settings'

const CHANNEL_TABS: { id: ChannelTab; label: string; icon: string }[] = [
  { id: 'webchat',  label: 'WebChat',  icon: '💻' },
  { id: 'whatsapp', label: 'WhatsApp', icon: '💬' },
  { id: 'voice',    label: 'Voice',    icon: '📞' },
  { id: 'email',    label: 'E-mail',   icon: '✉️'  },
  { id: 'sms',      label: 'SMS',      icon: '📱' },
]

// Channels that have a real "Settings" page; others show a placeholder
const HAS_SETTINGS = new Set<ChannelTab>(['webchat'])

// ── Component ─────────────────────────────────────────────────────────────────

const ConfigChannelsIndex: React.FC = () => {
  const [activeChannel, setActiveChannel] = useState<ChannelTab>('webchat')
  const [activeSubTab,  setActiveSubTab]  = useState<ChannelSubTab>('endpoints')

  function handleChannelChange(ch: ChannelTab) {
    setActiveChannel(ch)
    setActiveSubTab('endpoints')   // always land on Endpoints when switching channel
  }

  return (
    <div>
      <PageHeader title="Channel Configuration" />

      {/* ── Channel selector ── */}
      <div className="mb-0 border-b border-gray-200 flex gap-6">
        {CHANNEL_TABS.map(tab => (
          <button
            key={tab.id}
            onClick={() => handleChannelChange(tab.id)}
            className={`py-3 px-1 font-semibold text-sm transition-colors border-b-2 flex items-center gap-1.5 ${
              activeChannel === tab.id
                ? 'text-primary border-primary'
                : 'text-gray-500 border-transparent hover:text-gray-800'
            }`}
          >
            <span>{tab.icon}</span>
            <span>{tab.label}</span>
          </button>
        ))}
      </div>

      {/* ── Sub-tab selector ── */}
      <div className="mt-4 mb-6 flex gap-4 border-b border-gray-100">
        {(['endpoints', 'settings'] as ChannelSubTab[]).map(sub => {
          const isSettings = sub === 'settings'
          const disabled   = isSettings && !HAS_SETTINGS.has(activeChannel)
          return (
            <button
              key={sub}
              onClick={() => !disabled && setActiveSubTab(sub)}
              disabled={disabled}
              className={`pb-2 px-1 text-xs font-medium transition-colors border-b-2 capitalize ${
                disabled
                  ? 'text-gray-300 border-transparent cursor-not-allowed'
                  : activeSubTab === sub
                    ? 'text-primary border-primary'
                    : 'text-gray-500 border-transparent hover:text-gray-800'
              }`}
              title={disabled ? 'Coming soon' : undefined}
            >
              {sub === 'endpoints' ? 'Endpoints' : 'General Settings'}
              {disabled && <span className="ml-1 text-[10px] text-gray-300">coming soon</span>}
            </button>
          )
        })}
      </div>

      {/* ── Content ── */}
      <div>
        {activeSubTab === 'endpoints' && (
          <ChannelEndpointList channel={activeChannel} />
        )}

        {activeSubTab === 'settings' && activeChannel === 'webchat' && (
          <WebChatConfigPage />
        )}
      </div>
    </div>
  )
}

export default ConfigChannelsIndex
