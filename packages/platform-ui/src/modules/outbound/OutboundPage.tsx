/**
 * OutboundPage
 * Route: /config/outbound — Outbound module (mailings + campaigns + deliveries).
 *
 * Grant-first (strict ABAC): gated by outbound.configurar (no admin bypass — D2).
 * Single tabbed page: Mailings (audience + column_map + import) | Campaigns (orchestration
 * + ordering editor) | Deliveries (read-only monitor). Backend: mailing-api via
 * /v1/mailings and /v1/campaigns. Closes the "UI-editable" invariant (fatia 1b).
 */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import { makeOutboundApi } from './api'
import MailingsTab from './MailingsTab'
import CampaignsTab from './CampaignsTab'
import DeliveriesTab from './DeliveriesTab'

type Tab = 'mailings' | 'campaigns' | 'deliveries'

export default function OutboundPage() {
  const { t } = useTranslation('outbound')
  const { session, tenantId, perms } = useAuth()
  const api = useMemo(() => makeOutboundApi(tenantId), [tenantId])
  const [tab, setTab] = useState<Tab>('mailings')

  // Access guard (grant-first, no admin bypass — D2).
  if (!session || !perms.can('outbound', 'configurar')) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-muted">{t('restricted')}</p>
      </div>
    )
  }

  const TABS: Array<{ id: Tab; label: string }> = [
    { id: 'mailings',   label: t('tabs.mailings') },
    { id: 'campaigns',  label: t('tabs.campaigns') },
    { id: 'deliveries', label: t('tabs.deliveries') },
  ]

  return (
    <div className="flex flex-col h-full bg-surface-muted">
      <div className="bg-white flex-shrink-0 px-6 pt-4 border-b border-border">
        <h1 className="text-lg font-semibold text-dark">{t('title')}</h1>
        <p className="text-sm text-muted mt-0.5">{t('info')}</p>
        <div className="flex gap-1 mt-3">
          {TABS.map(x => (
            <button key={x.id} onClick={() => setTab(x.id)}
              className={`px-4 py-2 text-sm rounded-t-lg border-b-2 transition-colors ${tab === x.id
                ? 'border-primary text-primary font-medium'
                : 'border-transparent text-muted hover:text-dark'}`}>
              {x.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {tab === 'mailings' && <MailingsTab api={api} />}
        {tab === 'campaigns' && <CampaignsTab api={api} tenantId={tenantId} />}
        {tab === 'deliveries' && <DeliveriesTab api={api} />}
      </div>
    </div>
  )
}
