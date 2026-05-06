/**
 * config-canais/index.tsx
 * Configuração → Canais — /config/canais
 *
 * Cada canal tem sua própria aba de configuração. O design prevê
 * futuros canais (WhatsApp, Voice, Email, SMS) como sub-tabs.
 *
 * Canais ativos:
 *   webchat — Channel Gateway, namespace "webchat" no Config API
 *
 * Canais futuros (placeholder):
 *   whatsapp, voice, email, sms
 */
import React, { useState } from 'react'
import PageHeader from '@/components/ui/PageHeader'
import WebChatConfigPage from './WebChatConfigPage'

type ChannelTab = 'webchat' | 'whatsapp' | 'voice' | 'email' | 'sms'

const TABS: { id: ChannelTab; label: string; icon: string; available: boolean }[] = [
  { id: 'webchat',  label: 'WebChat',  icon: '💻', available: true  },
  { id: 'whatsapp', label: 'WhatsApp', icon: '💬', available: false },
  { id: 'voice',    label: 'Voice',    icon: '📞', available: false },
  { id: 'email',    label: 'E-mail',   icon: '✉️',  available: false },
  { id: 'sms',      label: 'SMS',      icon: '📱', available: false },
]

const ConfigCanaisIndex: React.FC = () => {
  const [activeTab, setActiveTab] = useState<ChannelTab>('webchat')

  return (
    <div>
      <PageHeader title="Configuração de Canais" />

      <div className="mb-6 border-b border-lightGray flex gap-8">
        {TABS.map(tab => (
          <button
            key={tab.id}
            onClick={() => tab.available && setActiveTab(tab.id)}
            className={`py-3 px-1 font-semibold transition-colors border-b-2 flex items-center gap-1.5 ${
              !tab.available
                ? 'text-gray/40 border-transparent cursor-not-allowed'
                : activeTab === tab.id
                  ? 'text-primary border-primary'
                  : 'text-gray border-transparent hover:text-dark'
            }`}
            title={!tab.available ? 'Em desenvolvimento' : undefined}
          >
            <span>{tab.icon}</span>
            <span>{tab.label}</span>
            {!tab.available && (
              <span className="text-[10px] font-normal text-gray/60 ml-1">em breve</span>
            )}
          </button>
        ))}
      </div>

      <div className="mt-6">
        {activeTab === 'webchat' && <WebChatConfigPage />}
      </div>
    </div>
  )
}

export default ConfigCanaisIndex
