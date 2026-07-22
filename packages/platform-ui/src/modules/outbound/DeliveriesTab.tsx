/**
 * outbound/DeliveriesTab.tsx
 * Read-only monitor: pick a campaign → its per-entry deliveries (result, attempts, drill).
 */
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import Spinner from '@/components/ui/Spinner'
import EmptyState from '@/components/ui/EmptyState'
import { Campaign, CampaignDelivery, makeOutboundApi, fmtDateTime } from './api'
import { ResultPill, inputCls } from './_ui'

type Api = ReturnType<typeof makeOutboundApi>

// delivery.session_id holds a real session (active-contact worker) OR a survey token
// (survey worker overloads the field). Only a UUID is a drillable session — a token is
// shown as plain text. (Tech debt: survey should carry its token in its own field.)
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export default function DeliveriesTab({ api }: { api: Api }) {
  const { t } = useTranslation('outbound')
  const [campaigns, setCampaigns] = useState<Campaign[]>([])
  const [selected, setSelected] = useState('')
  const [deliveries, setDeliveries] = useState<CampaignDelivery[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    api.listCampaigns().then(r => {
      const cs = r.campaigns ?? []
      setCampaigns(cs)
      if (cs.length && !selected) setSelected(cs[0].id)
    }).catch(console.error)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api])

  const load = useCallback(async (id: string) => {
    if (!id) return
    setLoading(true)
    try { setDeliveries((await api.listDeliveries(id)).deliveries ?? []) }
    catch (e) { console.error(e) }
    finally { setLoading(false) }
  }, [api])
  useEffect(() => { if (selected) load(selected) }, [selected, load])

  const counts = deliveries.reduce<Record<string, number>>((acc, d) => {
    acc[d.result] = (acc[d.result] ?? 0) + 1; return acc
  }, {})

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <select value={selected} onChange={e => setSelected(e.target.value)} className={`${inputCls} max-w-md`}>
          <option value="" disabled>{t('delivery.pickCampaign')}</option>
          {campaigns.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <button onClick={() => load(selected)} disabled={!selected}
          className="px-3 py-2 text-sm text-primary hover:bg-primary-light rounded-lg disabled:opacity-40">
          {t('delivery.reload')}
        </button>
        <div className="flex gap-1 flex-wrap ml-auto">
          {Object.entries(counts).map(([r, n]) => (
            <span key={r} className="flex items-center gap-1"><ResultPill result={r} /><span className="text-xs text-muted">{n}</span></span>
          ))}
        </div>
      </div>

      {loading && <div className="flex justify-center py-8"><Spinner /></div>}
      {!loading && selected && deliveries.length === 0 && (
        <EmptyState icon="📭" title={t('delivery.empty.title')} description={t('delivery.empty.desc')} />
      )}

      {!loading && deliveries.length > 0 && (
        <div className="bg-white border border-border rounded-xl overflow-hidden">
          <table className="w-full text-xs">
            <thead><tr className="text-left text-muted bg-surface-muted border-b border-border">
              <th className="py-2 px-3">{t('delivery.col.result')}</th>
              <th className="py-2 px-3">{t('delivery.col.attempts')}</th>
              <th className="py-2 px-3">{t('delivery.col.session')}</th>
              <th className="py-2 px-3">{t('delivery.col.contactedAt')}</th>
              <th className="py-2 px-3">{t('delivery.col.error')}</th>
            </tr></thead>
            <tbody>
              {deliveries.map(d => (
                <tr key={d.id} className="border-b border-border/50 hover:bg-surface-muted/40">
                  <td className="py-2 px-3"><ResultPill result={d.result} /></td>
                  <td className="py-2 px-3">{d.attempts}</td>
                  <td className="py-2 px-3 font-mono text-muted-light">
                    {!d.session_id ? '—'
                      : UUID_RE.test(d.session_id)
                        ? <Link to={`/analise/sessions?session_id=${d.session_id}`} title={d.session_id}
                            className="text-primary hover:underline">{d.session_id.slice(0, 12)}… ↗</Link>
                        : <span title={t('delivery.tokenHint')}>{d.session_id.slice(0, 12)}…</span>}
                  </td>
                  <td className="py-2 px-3 text-muted-light">{fmtDateTime(d.contacted_at)}</td>
                  <td className="py-2 px-3 text-red-text">{d.error ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
