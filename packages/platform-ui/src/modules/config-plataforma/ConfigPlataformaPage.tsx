/**
 * ConfigPlataformaPage — /config/platform
 *
 * Six flat top-level tabs (no sub-sidebar):
 *   routing_timeouts — routing + session namespaces (SLA, TTLs)
 *   consumer         — analytics-api Kafka consumer settings
 *   expurgo          — data retention periods
 *   sentimento       — SentimentBandsEditor (numeric bands)
 *   calendar         — holiday sets + calendar CRUD (calendar-api port 3700)
 *   routing          — RoutingSkillsManager (competency skills)
 *
 * Admin token (for config mutations) is shown only on tabs that need write access.
 */
import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Settings, Calendar, GitBranch, Eye, EyeOff, Check } from 'lucide-react'
import { useAuth } from '@/auth/useAuth'
import { NamespacePanel }          from './components/NamespaceEditor'
import { CalendarManager }          from './components/CalendarManager'
import { RoutingSkillsManager }     from './components/RoutingSkillsManager'
import { SentimentBandsEditor }     from './components/SentimentBandsEditor'

// ── Tab definition ─────────────────────────────────────────────────────────────

type Tab = 'routing_timeouts' | 'consumer' | 'expurgo' | 'sentimento' | 'calendar' | 'routing'

/** Tabs where the admin token input must be visible */
const CONFIG_WRITE_TABS: Tab[] = ['routing_timeouts', 'consumer', 'expurgo', 'sentimento']

/** Namespace tabs: each entry defines the API namespace(s) to render */
const NS_TABS: Record<string, { namespaces: { ns: string; label?: string }[] }> = {
  routing_timeouts: {
    namespaces: [
      { ns: 'routing', label: 'Routing (SLA & TTL)' },
      { ns: 'session', label: 'Component Timeouts'  },
    ],
  },
  consumer: { namespaces: [{ ns: 'consumer' }] },
  expurgo:  { namespaces: [{ ns: 'expurgo'  }] },
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ConfigPlataformaPage() {
  const { t } = useTranslation('configPlataforma')
  const { tenantId } = useAuth()
  const orgId = tenantId

  const [tab,        setTab]        = useState<Tab>('routing_timeouts')
  const [adminToken, setAdminToken] = useState('')
  const [showToken,  setShowToken]  = useState(false)
  const [editingKey, setEditingKey] = useState<string | null>(null)

  const handleTabChange = (next: Tab) => {
    setTab(next)
    setEditingKey(null)
  }

  const needsAdminToken = CONFIG_WRITE_TABS.includes(tab)

  const tabs: { id: Tab; label: string; icon?: React.ReactNode }[] = [
    { id: 'routing_timeouts', label: t('tabs.routingTimeouts') },
    { id: 'consumer',         label: t('tabs.consumer') },
    { id: 'expurgo',          label: t('tabs.dataRetention') },
    { id: 'sentimento',       label: t('tabs.sentimento') },
    { id: 'calendar',         label: t('tabs.calendar'),  icon: <Calendar  size={13} aria-hidden="true" /> },
    { id: 'routing',          label: t('tabs.routing'),   icon: <GitBranch size={13} aria-hidden="true" /> },
  ]

  return (
    <div className="flex flex-col h-full overflow-hidden">

      {/* ── Page header ──────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-border shrink-0 bg-white">
        <div>
          <h1 className="text-lg font-bold text-dark flex items-center gap-2">
            <Settings size={18} aria-hidden="true" className="text-muted" />
            {t('title')}
          </h1>
          <p className="text-xs text-muted mt-0.5">{t('description')}</p>
        </div>

        {/* Admin token — only when the active tab needs write access */}
        {needsAdminToken && (
          <div className="flex items-center gap-2">
            <label className="text-xs text-muted shrink-0">{t('adminTokenLabel')}</label>
            <input
              type={showToken ? 'text' : 'password'}
              value={adminToken}
              onChange={e => setAdminToken(e.target.value)}
              placeholder={t('adminTokenPlaceholder')}
              className="w-44 text-xs font-mono px-2.5 py-1.5 border border-border-strong rounded focus:outline-none focus:ring-1 focus:ring-primary bg-white"
            />
            <button
              onClick={() => setShowToken(v => !v)}
              className="text-muted hover:text-dark transition-colors"
              title={showToken ? t('hideToken') : t('showToken')}
            >
              {showToken ? <EyeOff size={14} aria-hidden="true" /> : <Eye size={14} aria-hidden="true" />}
            </button>
            {adminToken && (
              <span className="text-xs text-green font-semibold flex items-center gap-1">
                <Check size={11} aria-hidden="true" /> {t('tokenSet')}
              </span>
            )}
          </div>
        )}
      </div>

      {/* ── Tab bar ──────────────────────────────────────────────────────────── */}
      <div className="flex border-b border-border bg-white shrink-0 px-2">
        {tabs.map(tb => (
          <button
            key={tb.id}
            onClick={() => handleTabChange(tb.id)}
            className={`flex items-center gap-1.5 py-3 px-4 text-sm font-medium border-b-2 transition-colors ${
              tab === tb.id
                ? 'border-primary text-primary'
                : 'border-transparent text-muted hover:text-dark'
            }`}
          >
            {tb.icon}
            {tb.label}
          </button>
        ))}
      </div>

      {/* ── Tab content ──────────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-auto">

        {/* ── Namespace tabs (routing_timeouts | consumer | expurgo) ─────────── */}
        {tab in NS_TABS && (() => {
          const cfg = NS_TABS[tab]
          const descKey = tab === 'routing_timeouts' ? 'routingTimeouts'
                        : tab === 'consumer'          ? 'consumer'
                        : 'dataRetention'
          return (
            <div className="flex flex-col h-full">
              {/* Description banner */}
              <div className="px-6 py-2.5 bg-surface-muted border-b border-border text-xs text-muted">
                {t(`nsDesc.${descKey}`)}
              </div>

              {/* Column headers */}
              <div className="flex gap-4 px-5 py-2 bg-surface-muted border-b border-border text-2xs font-semibold text-muted-light uppercase tracking-wide shrink-0">
                <span className="w-52 shrink-0">{t('namespace.key')}</span>
                <span className="flex-1">{t('namespace.value')}</span>
                <span className="w-20 shrink-0 text-right">{t('namespace.actions')}</span>
              </div>

              {/* Panels */}
              <div className="flex-1 overflow-y-auto">
                {cfg.namespaces.map(({ ns, label }) => (
                  <NamespacePanel
                    key={ns}
                    nsId={ns}
                    sectionLabel={cfg.namespaces.length > 1 ? label : undefined}
                    tenantId={tenantId}
                    adminToken={adminToken}
                    editingKey={editingKey}
                    setEditingKey={setEditingKey}
                  />
                ))}
              </div>
            </div>
          )
        })()}

        {/* ── Sentimento ──────────────────────────────────────────────────────── */}
        {tab === 'sentimento' && (
          <SentimentBandsEditor tenantId={tenantId} adminToken={adminToken} />
        )}

        {/* ── Calendários ─────────────────────────────────────────────────────── */}
        {tab === 'calendar' && (
          <CalendarManager orgId={orgId} tenantId={tenantId} />
        )}

        {/* ── Roteamento ──────────────────────────────────────────────────────── */}
        {tab === 'routing' && (
          <RoutingSkillsManager tenantId={tenantId} adminToken={adminToken} />
        )}

      </div>
    </div>
  )
}
