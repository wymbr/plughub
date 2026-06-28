/**
 * ConfigPlataformaPage — /config/platform
 *
 * Six flat top-level tabs (no sub-sidebar):
 *   routing_timeouts — routing + session namespaces (SLA, TTLs)
 *   consumer         — analytics-api Kafka consumer settings
 *   expurgo          — data retention periods
 *   sentimento       — SentimentBandsEditor (numeric bands)
 *   routing          — RoutingSkillsManager (competency skills)
 *
 * Calendars/holiday sets live in their own module (/config/calendars, CalendarsPage);
 * this page no longer renders calendar CRUD (was a redundant, mis-scoped duplicate).
 *
 * Admin token (for config mutations) is shown only on tabs that need write access.
 */
import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Settings, GitBranch } from 'lucide-react'
import { useAuth } from '@/auth/useAuth'
import { NamespacePanel }          from './components/NamespaceEditor'
import { RoutingSkillsManager }     from './components/RoutingSkillsManager'
import { SentimentBandsEditor }     from './components/SentimentBandsEditor'

// ── Tab definition ─────────────────────────────────────────────────────────────

type Tab = 'routing_timeouts' | 'consumer' | 'expurgo' | 'sentimento' | 'routing' | 'evaluation'

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
  // R8e — namespace `evaluation` editável na UI (limiar de divergência, N mínimo, etc.).
  evaluation: { namespaces: [{ ns: 'evaluation' }] },
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ConfigPlataformaPage() {
  const { t } = useTranslation('configPlataforma')
  // G-PROBE platform-wide: as escritas de config usam o Bearer do operador (session JWT)
  // + ABAC `config.plataforma` — sem caixa de admin-token.
  const { tenantId, session } = useAuth()
  const accessToken = session?.accessToken ?? ''

  const [tab,        setTab]        = useState<Tab>('routing_timeouts')
  const [editingKey, setEditingKey] = useState<string | null>(null)

  const handleTabChange = (next: Tab) => {
    setTab(next)
    setEditingKey(null)
  }

  const tabs: { id: Tab; label: string; icon?: React.ReactNode }[] = [
    { id: 'routing_timeouts', label: t('tabs.routingTimeouts') },
    { id: 'consumer',         label: t('tabs.consumer') },
    { id: 'expurgo',          label: t('tabs.dataRetention') },
    { id: 'evaluation',       label: t('tabs.evaluation') },
    { id: 'sentimento',       label: t('tabs.sentimento') },
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
                        : tab === 'evaluation'        ? 'evaluation'
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
                    accessToken={accessToken}
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
          <SentimentBandsEditor tenantId={tenantId} accessToken={accessToken} />
        )}

        {/* ── Roteamento ──────────────────────────────────────────────────────── */}
        {tab === 'routing' && (
          <RoutingSkillsManager tenantId={tenantId} accessToken={accessToken} />
        )}

      </div>
    </div>
  )
}
