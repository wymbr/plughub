/**
 * outbound/CampaignsTab.tsx
 * Campaigns CRUD + declarative ordering editor (reorderable {path, dir, type}).
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Spinner from '@/components/ui/Spinner'
import EmptyState from '@/components/ui/EmptyState'
import {
  Campaign, Mailing, OrderField, OrderDir, OrderFieldType, WebhookPool,
  makeOutboundApi, fetchWebhookPools, fetchCalendars, fmtDateTime,
} from './api'
import { Field, Modal, ConfirmModal, inputCls } from './_ui'

type Api = ReturnType<typeof makeOutboundApi>

// ── ordering editor — precedence = list order; added_at is the implicit tiebreaker ──

function OrderingEditor({ ordering, setOrdering }: {
  ordering: OrderField[]; setOrdering: (o: OrderField[]) => void
}) {
  const { t } = useTranslation('outbound')
  const move = (i: number, d: -1 | 1) => {
    const j = i + d
    if (j < 0 || j >= ordering.length) return
    const next = [...ordering]
    ;[next[i], next[j]] = [next[j], next[i]]
    setOrdering(next)
  }
  const upd = (i: number, patch: Partial<OrderField>) =>
    setOrdering(ordering.map((x, idx) => idx === i ? { ...x, ...patch } : x))
  return (
    <div className="space-y-2 border border-border rounded-lg p-3 bg-surface-muted/40">
      <p className="text-xs text-muted-light">{t('campaign.orderingHint')}</p>
      {ordering.map((o, i) => (
        <div key={i} className="flex items-center gap-2">
          <div className="flex flex-col">
            <button type="button" onClick={() => move(i, -1)} disabled={i === 0}
              className="text-muted-light hover:text-primary disabled:opacity-30 text-xs leading-none">▲</button>
            <button type="button" onClick={() => move(i, 1)} disabled={i === ordering.length - 1}
              className="text-muted-light hover:text-primary disabled:opacity-30 text-xs leading-none">▼</button>
          </div>
          <input value={o.path} placeholder={t('campaign.order.path')}
            onChange={e => upd(i, { path: e.target.value })} className={inputCls} />
          <select value={o.dir} onChange={e => upd(i, { dir: e.target.value as OrderDir })} className={`${inputCls} w-24`}>
            <option value="asc">asc</option>
            <option value="desc">desc</option>
          </select>
          <select value={o.type} onChange={e => upd(i, { type: e.target.value as OrderFieldType })} className={`${inputCls} w-28`}>
            <option value="text">text</option>
            <option value="number">number</option>
          </select>
          <button type="button" onClick={() => setOrdering(ordering.filter((_, idx) => idx !== i))}
            className="text-muted-light hover:text-red text-sm">×</button>
        </div>
      ))}
      <button type="button" onClick={() => setOrdering([...ordering, { path: '', dir: 'asc', type: 'text' }])}
        className="text-xs text-primary hover:text-primary-dark">+ {t('campaign.order.add')}</button>
      <p className="text-xs text-muted-light">{t('campaign.order.tiebreaker')}</p>
    </div>
  )
}

export default function CampaignsTab({ api, tenantId }: { api: Api; tenantId: string }) {
  const { t } = useTranslation('outbound')
  const [campaigns, setCampaigns] = useState<Campaign[]>([])
  const [mailings, setMailings] = useState<Mailing[]>([])
  const [pools, setPools] = useState<WebhookPool[]>([])
  const [calendars, setCalendars] = useState<Array<{ id: string; name: string }>>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<Campaign | null>(null)
  const [saving, setSaving] = useState(false)
  const [statusFilter, setStatusFilter] = useState<'visible' | 'all' | Campaign['status']>('visible')

  // form state
  const [fName, setFName] = useState('')
  const [fMailing, setFMailing] = useState('')
  const [fPool, setFPool] = useState('')
  const [fSelection, setFSelection] = useState('')
  const [fOrdering, setFOrdering] = useState<OrderField[]>([])
  const [fChannelPolicy, setFChannelPolicy] = useState('{}')
  const [fBatch, setFBatch] = useState('50')
  const [fTransactional, setFTransactional] = useState(false)
  const [fCalendar, setFCalendar] = useState('')
  const [fMaxAttempts, setFMaxAttempts] = useState('1')
  const [fStatus, setFStatus] = useState<Campaign['status']>('active')

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [c, m, wp, cals] = await Promise.all([
        api.listCampaigns(),
        api.listMailings(),
        fetchWebhookPools(tenantId).catch(() => []),
        fetchCalendars(tenantId).catch(() => []),
      ])
      setCampaigns(c.campaigns ?? []); setMailings(m.mailings ?? []); setPools(wp); setCalendars(cals)
    } catch (e) { setError(t('errors.loadFailed')); console.error(e) }
    finally { setLoading(false) }
  }, [api, tenantId, t])
  useEffect(() => { load() }, [load])

  const openCreate = () => {
    setEditing(null); setFName(''); setFMailing(mailings[0]?.id ?? ''); setFPool('')
    setFSelection(''); setFOrdering([]); setFChannelPolicy('{}'); setFBatch('50')
    setFTransactional(false); setFCalendar(''); setFMaxAttempts('1'); setFStatus('active')
    setShowForm(true)
  }
  const openEdit = (c: Campaign) => {
    setEditing(c); setFName(c.name); setFMailing(c.mailing_id); setFPool(c.pool_id)
    setFSelection(c.selection ? JSON.stringify(c.selection, null, 2) : '')
    setFOrdering(c.ordering ?? []); setFChannelPolicy(JSON.stringify(c.channel_policy ?? {}, null, 2))
    setFBatch(String(c.batch_size)); setFTransactional(c.transactional); setFCalendar(c.contact_calendar_id ?? '')
    setFMaxAttempts(String((c.retry as { max_attempts?: number })?.max_attempts ?? 1)); setFStatus(c.status)
    setShowForm(true)
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    let selection: Record<string, unknown> | null = null
    let channel_policy: Record<string, unknown> = {}
    try {
      if (fSelection.trim()) selection = JSON.parse(fSelection)
      channel_policy = JSON.parse(fChannelPolicy || '{}')
    } catch { alert(t('campaign.jsonInvalid')); return }
    setSaving(true)
    try {
      const body: Record<string, unknown> = {
        name: fName,
        pool_id: fPool,
        selection,
        ordering: fOrdering.filter(o => o.path),
        channel_policy,
        batch_size: parseInt(fBatch || '50', 10),
        transactional: fTransactional,
        contact_calendar_id: fCalendar || null,
        retry: { max_attempts: Math.max(1, parseInt(fMaxAttempts || '1', 10)) },
      }
      if (editing) { body.status = fStatus; await api.updateCampaign(editing.id, body) }
      else { body.mailing_id = fMailing; await api.createCampaign(body) }
      setShowForm(false); load()
    } catch (e) { alert(`${t('errors.saveFailed')}: ${String(e)}`) }
    finally { setSaving(false) }
  }

  const mailingName = (id: string) => mailings.find(m => m.id === id)?.name ?? id

  const shown = campaigns.filter(c =>
    statusFilter === 'all' ? true
      : statusFilter === 'visible' ? c.status !== 'archived'
      : c.status === statusFilter
  )

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <select value={statusFilter} onChange={e => setStatusFilter(e.target.value as typeof statusFilter)}
          className={`${inputCls} max-w-xs`}>
          <option value="visible">{t('campaign.filter.visible')}</option>
          <option value="all">{t('campaign.filter.all')}</option>
          <option value="active">active</option>
          <option value="paused">paused</option>
          <option value="completed">completed</option>
          <option value="archived">archived</option>
        </select>
        <button onClick={openCreate} disabled={mailings.length === 0}
          className="ml-auto px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-primary-dark disabled:opacity-50">
          + {t('campaign.new')}
        </button>
      </div>

      {loading && <div className="flex justify-center py-8"><Spinner /></div>}
      {error && <p className="text-sm text-red-text">{error}</p>}
      {!loading && shown.length === 0 && (
        <EmptyState icon="📣" title={t('campaign.empty.title')} description={t('campaign.empty.desc')} />
      )}

      {!loading && shown.map(c => (
        <div key={c.id} className="flex items-start gap-3 px-4 py-3 bg-white border border-border rounded-xl">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <p className="text-sm font-semibold text-dark">{c.name}</p>
              <span className="text-xs px-2 py-0.5 rounded-full bg-surface-alt text-muted">{c.status}</span>
            </div>
            <div className="flex items-center gap-3 mt-1.5 flex-wrap text-xs">
              <span className="bg-surface-alt text-muted px-2 py-0.5 rounded-full">📇 {mailingName(c.mailing_id)}</span>
              <span className="bg-primary-light text-primary px-2 py-0.5 rounded-full">🎯 {c.pool_id}</span>
              {c.ordering?.length > 0 && <span className="text-muted-light">↕ {c.ordering.map(o => o.path).join(', ')}</span>}
              <span className="text-muted-light">{t('campaign.batch')}: {c.batch_size}</span>
              <span className="text-muted-light">{fmtDateTime(c.created_at)}</span>
            </div>
          </div>
          <button onClick={() => openEdit(c)} className="px-3 py-1.5 text-xs text-primary hover:bg-primary-light rounded-lg flex-shrink-0">{t('actions.edit')}</button>
        </div>
      ))}

      {showForm && (
        <Modal wide title={editing ? `${t('actions.edit')} — ${editing.name}` : t('campaign.new')} onClose={() => setShowForm(false)}>
          <form onSubmit={submit} className="space-y-4">
            <Field label={t('campaign.name')}>
              <input required value={fName} onChange={e => setFName(e.target.value)} className={inputCls} />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label={t('campaign.mailing')} hint={editing ? t('campaign.mailingLocked') : undefined}>
                <select required value={fMailing} disabled={!!editing}
                  onChange={e => setFMailing(e.target.value)}
                  className={`${inputCls} ${editing ? 'opacity-60 cursor-not-allowed' : ''}`}>
                  <option value="" disabled>—</option>
                  {mailings.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
                </select>
              </Field>
              <Field label={t('campaign.pool')} hint={t('campaign.poolHint')}>
                {pools.length === 0 ? <p className="text-xs text-warning-text">{t('campaign.noWebhookPools')}</p> : (
                  <select required value={fPool} onChange={e => setFPool(e.target.value)} className={inputCls}>
                    <option value="" disabled>—</option>
                    {pools.map(p => <option key={p.pool_id} value={p.pool_id}>{p.pool_id}</option>)}
                  </select>
                )}
              </Field>
            </div>

            <div>
              <label className="block text-xs font-medium text-dark mb-1">{t('campaign.ordering')}</label>
              <OrderingEditor ordering={fOrdering} setOrdering={setFOrdering} />
            </div>

            <Field label={t('campaign.selection')} hint={t('campaign.selectionHint')}>
              <textarea value={fSelection} onChange={e => setFSelection(e.target.value)} rows={2}
                spellCheck={false} placeholder='{"segmento": "vip"}' className={`${inputCls} font-mono text-xs`} />
            </Field>
            <Field label={t('campaign.channelPolicy')} hint={t('campaign.channelPolicyHint')}>
              <textarea value={fChannelPolicy} onChange={e => setFChannelPolicy(e.target.value)} rows={2}
                spellCheck={false} className={`${inputCls} font-mono text-xs`} />
            </Field>

            <div className="grid grid-cols-3 gap-3">
              <Field label={t('campaign.batch')}>
                <input type="number" min={1} value={fBatch} onChange={e => setFBatch(e.target.value)} className={inputCls} />
              </Field>
              <Field label={t('campaign.maxAttempts')}>
                <input type="number" min={1} value={fMaxAttempts} onChange={e => setFMaxAttempts(e.target.value)} className={inputCls} />
              </Field>
              <Field label={t('campaign.calendar')}>
                <select value={fCalendar} onChange={e => setFCalendar(e.target.value)} className={inputCls}>
                  <option value="">{t('campaign.calendarNone')}</option>
                  {calendars.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              </Field>
            </div>

            <div className="flex items-center gap-4">
              <label className="flex items-center gap-2 text-sm text-dark">
                <input type="checkbox" checked={fTransactional} onChange={e => setFTransactional(e.target.checked)} />
                {t('campaign.transactional')}
              </label>
              {editing && (
                <Field label={t('campaign.status')}>
                  <select value={fStatus} onChange={e => setFStatus(e.target.value as Campaign['status'])} className={inputCls}>
                    {(['active', 'paused', 'completed', 'archived'] as const).map(s => <option key={s} value={s}>{s}</option>)}
                  </select>
                </Field>
              )}
            </div>

            <div className="flex gap-2 justify-end pt-2 border-t border-border">
              <button type="button" onClick={() => setShowForm(false)} className="px-4 py-2 text-sm text-muted hover:text-dark">{t('actions.cancel')}</button>
              <button type="submit" disabled={saving || pools.length === 0}
                className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-primary-dark disabled:opacity-50">
                {saving ? t('actions.saving') : (editing ? t('actions.save') : t('actions.create'))}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  )
}
