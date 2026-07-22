/**
 * outbound/MailingsTab.tsx
 * Mailings CRUD + column_map editor (Fase 4 parsing config) + file import + entries view.
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Spinner from '@/components/ui/Spinner'
import EmptyState from '@/components/ui/EmptyState'
import {
  Mailing, MailingEntry, ColumnMap, DedupPolicy, ImportReport, makeOutboundApi, fmtDateTime,
} from './api'
import { Field, Modal, ConfirmModal, inputCls } from './_ui'

type Api = ReturnType<typeof makeOutboundApi>

// ── column_map editor ─────────────────────────────────────────────────────────

interface CMState {
  idCol: string
  anchors: Array<{ kind: string; column: string }>
  contacts: Array<{ channel: string; column: string }>
  metaCols: string
}

function emptyCM(): CMState {
  return { idCol: '', anchors: [], contacts: [], metaCols: '' }
}

function cmFromMap(cm: ColumnMap | null): CMState {
  if (!cm) return emptyCM()
  return {
    idCol: cm.customer_id_column ?? '',
    anchors: cm.anchors ?? [],
    contacts: Object.entries(cm.contacts ?? {}).map(([channel, column]) => ({ channel, column })),
    metaCols: (cm.metadata_columns ?? []).join(', '),
  }
}

/** Build the ColumnMap (or null if nothing configured). */
function cmToMap(s: CMState): ColumnMap | null {
  const anchors = s.anchors.filter(a => a.kind && a.column)
  const contacts: Record<string, string> = {}
  for (const c of s.contacts) if (c.channel && c.column) contacts[c.channel] = c.column
  const metaCols = s.metaCols.split(',').map(x => x.trim()).filter(Boolean)
  if (!s.idCol && anchors.length === 0 && Object.keys(contacts).length === 0 && metaCols.length === 0) {
    return null
  }
  const map: ColumnMap = { anchors, contacts }
  if (s.idCol) map.customer_id_column = s.idCol
  if (metaCols.length) map.metadata_columns = metaCols
  return map
}

function ColumnMapEditor({ cm, setCm }: { cm: CMState; setCm: (s: CMState) => void }) {
  const { t } = useTranslation('outbound')
  const upd = (patch: Partial<CMState>) => setCm({ ...cm, ...patch })
  return (
    <div className="space-y-3 border border-border rounded-lg p-3 bg-surface-muted/40">
      <p className="text-xs text-muted-light">{t('mailing.columnMapHint')}</p>

      <Field label={t('mailing.cm.idColumn')} hint={t('mailing.cm.idColumnHint')}>
        <input value={cm.idCol} onChange={e => upd({ idCol: e.target.value })}
          placeholder="id_cliente" className={inputCls} />
      </Field>

      {/* Anchors: kind → column */}
      <div>
        <label className="block text-xs font-medium text-dark mb-1">{t('mailing.cm.anchors')}</label>
        <div className="space-y-1">
          {cm.anchors.map((a, i) => (
            <div key={i} className="flex items-center gap-2">
              <input value={a.kind} placeholder="phone|email|cpf"
                onChange={e => upd({ anchors: cm.anchors.map((x, idx) => idx === i ? { ...x, kind: e.target.value } : x) })}
                className={`${inputCls} w-32`} />
              <span className="text-muted-light text-xs">→</span>
              <input value={a.column} placeholder={t('mailing.cm.column')}
                onChange={e => upd({ anchors: cm.anchors.map((x, idx) => idx === i ? { ...x, column: e.target.value } : x) })}
                className={inputCls} />
              <button type="button" onClick={() => upd({ anchors: cm.anchors.filter((_, idx) => idx !== i) })}
                className="text-muted-light hover:text-red text-sm">×</button>
            </div>
          ))}
          <button type="button" onClick={() => upd({ anchors: [...cm.anchors, { kind: '', column: '' }] })}
            className="text-xs text-primary hover:text-primary-dark">+ {t('mailing.cm.addAnchor')}</button>
        </div>
      </div>

      {/* Contacts: channel → column */}
      <div>
        <label className="block text-xs font-medium text-dark mb-1">{t('mailing.cm.contacts')}</label>
        <div className="space-y-1">
          {cm.contacts.map((c, i) => (
            <div key={i} className="flex items-center gap-2">
              <input value={c.channel} placeholder="whatsapp|email|sms"
                onChange={e => upd({ contacts: cm.contacts.map((x, idx) => idx === i ? { ...x, channel: e.target.value } : x) })}
                className={`${inputCls} w-32`} />
              <span className="text-muted-light text-xs">→</span>
              <input value={c.column} placeholder={t('mailing.cm.column')}
                onChange={e => upd({ contacts: cm.contacts.map((x, idx) => idx === i ? { ...x, column: e.target.value } : x) })}
                className={inputCls} />
              <button type="button" onClick={() => upd({ contacts: cm.contacts.filter((_, idx) => idx !== i) })}
                className="text-muted-light hover:text-red text-sm">×</button>
            </div>
          ))}
          <button type="button" onClick={() => upd({ contacts: [...cm.contacts, { channel: '', column: '' }] })}
            className="text-xs text-primary hover:text-primary-dark">+ {t('mailing.cm.addContact')}</button>
        </div>
      </div>

      <Field label={t('mailing.cm.metadataColumns')} hint={t('mailing.cm.metadataColumnsHint')}>
        <input value={cm.metaCols} onChange={e => upd({ metaCols: e.target.value })}
          placeholder="prioridade, operadora" className={inputCls} />
      </Field>
    </div>
  )
}

// ── entries drawer ─────────────────────────────────────────────────────────────

function EntriesModal({ api, mailing, onClose }: { api: Api; mailing: Mailing; onClose: () => void }) {
  const { t } = useTranslation('outbound')
  const [entries, setEntries] = useState<MailingEntry[]>([])
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    api.listEntries(mailing.id).then(r => setEntries(r.entries ?? [])).finally(() => setLoading(false))
  }, [api, mailing.id])
  return (
    <Modal wide title={`${t('mailing.entries')} — ${mailing.name}${loading ? '' : ` (${entries.length})`}`} onClose={onClose}>
      {loading ? <div className="flex justify-center py-6"><Spinner /></div> : (
        entries.length === 0 ? <p className="text-sm text-muted">{t('mailing.noEntries')}</p> : (
          <table className="w-full text-xs">
            <thead><tr className="text-left text-muted border-b border-border">
              <th className="py-1 pr-2">{t('mailing.entry.customer')}</th>
              <th className="py-1 pr-2">{t('mailing.entry.contacts')}</th>
              <th className="py-1 pr-2">{t('mailing.entry.status')}</th>
              <th className="py-1">{t('mailing.entry.source')}</th>
            </tr></thead>
            <tbody>
              {entries.map(e => (
                <tr key={e.id} className="border-b border-border/50">
                  <td className="py-1 pr-2 font-mono">{e.customer_id ?? '—'}</td>
                  <td className="py-1 pr-2">{Object.keys(e.contacts ?? {}).join(', ') || '—'}</td>
                  <td className="py-1 pr-2">{e.status}</td>
                  <td className="py-1 text-muted-light">{e.source ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )
      )}
    </Modal>
  )
}

// ── main tab ──────────────────────────────────────────────────────────────────

export default function MailingsTab({ api }: { api: Api }) {
  const { t } = useTranslation('outbound')
  const [mailings, setMailings] = useState<Mailing[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<Mailing | null>(null)
  const [delTarget, setDelTarget] = useState<Mailing | null>(null)
  const [entriesOf, setEntriesOf] = useState<Mailing | null>(null)
  const [saving, setSaving] = useState(false)
  const [importReport, setImportReport] = useState<{ name: string; rep: ImportReport } | null>(null)

  // form state
  const [fName, setFName] = useState('')
  const [fDesc, setFDesc] = useState('')
  const [fDedup, setFDedup] = useState<DedupPolicy>('customer_context')
  const [fContract, setFContract] = useState('')
  const [fTtl, setFTtl] = useState('')
  const [fCm, setFCm] = useState<CMState>(emptyCM())

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try { setMailings((await api.listMailings()).mailings ?? []) }
    catch (e) { setError(t('errors.loadFailed')); console.error(e) }
    finally { setLoading(false) }
  }, [api, t])
  useEffect(() => { load() }, [load])

  const openCreate = () => {
    setEditing(null); setFName(''); setFDesc(''); setFDedup('customer_context')
    setFContract(''); setFTtl(''); setFCm(emptyCM()); setShowForm(true)
  }
  const openEdit = (m: Mailing) => {
    setEditing(m); setFName(m.name); setFDesc(m.description ?? ''); setFDedup(m.dedup_policy)
    setFContract(m.metadata_contract ?? ''); setFTtl(m.entry_ttl_seconds ? String(m.entry_ttl_seconds) : '')
    setFCm(cmFromMap(m.column_map)); setShowForm(true)
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setSaving(true)
    try {
      const body: Record<string, unknown> = {
        name: fName,
        description: fDesc || undefined,
        dedup_policy: fDedup,
        metadata_contract: fContract || undefined,
        entry_ttl_seconds: fTtl ? parseInt(fTtl, 10) : undefined,
        column_map: cmToMap(fCm),
      }
      if (editing) await api.updateMailing(editing.id, body)
      else await api.createMailing(body)
      setShowForm(false); load()
    } catch (e) { alert(`${t('errors.saveFailed')}: ${String(e)}`) }
    finally { setSaving(false) }
  }

  const confirmDelete = async () => {
    if (!delTarget) return
    try { await api.removeMailing(delTarget.id); setDelTarget(null); load() }
    catch (e) { alert(String(e)) }
  }

  const onImport = async (m: Mailing, file: File) => {
    try {
      const rep = await api.importFile(m.id, file)
      setImportReport({ name: m.name, rep })
    } catch (e) { alert(`${t('mailing.importFailed')}: ${String(e)}`) }
  }

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <button onClick={openCreate}
          className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-primary-dark transition-colors">
          + {t('mailing.new')}
        </button>
      </div>

      {loading && <div className="flex justify-center py-8"><Spinner /></div>}
      {error && <p className="text-sm text-red-text">{error}</p>}
      {!loading && mailings.length === 0 && (
        <EmptyState icon="📇" title={t('mailing.empty.title')} description={t('mailing.empty.desc')} />
      )}

      {!loading && mailings.map(m => (
        <div key={m.id} className="flex items-start gap-3 px-4 py-3 bg-white border border-border rounded-xl">
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold text-dark">{m.name}</p>
            <div className="flex items-center gap-3 mt-1.5 flex-wrap text-xs">
              <button onClick={() => setEntriesOf(m)}
                className="bg-green/10 text-green px-2 py-0.5 rounded-full hover:bg-green/20 transition-colors">
                {t('mailing.entryCount', { count: m.entry_count ?? 0 })}
              </button>
              <span className="bg-surface-alt text-muted px-2 py-0.5 rounded-full">{t('mailing.dedup')}: {m.dedup_policy}</span>
              {m.column_map && <span className="bg-primary-light text-primary px-2 py-0.5 rounded-full">{t('mailing.hasColumnMap')}</span>}
              {m.metadata_contract && <span className="text-muted-light">{m.metadata_contract}</span>}
              <span className="text-muted-light">{fmtDateTime(m.updated_at)}</span>
            </div>
          </div>
          <div className="flex gap-1 flex-shrink-0 items-center">
            <label className={`px-3 py-1.5 text-xs rounded-lg cursor-pointer transition-colors ${m.column_map ? 'text-primary hover:bg-primary-light' : 'text-muted-light cursor-not-allowed'}`}
              title={m.column_map ? '' : t('mailing.importNeedsColumnMap')}>
              {t('mailing.import')}
              <input type="file" accept=".csv,.tsv,.xlsx,.xlsm" className="hidden" disabled={!m.column_map}
                onChange={e => { const f = e.target.files?.[0]; if (f) onImport(m, f); e.currentTarget.value = '' }} />
            </label>
            <button onClick={() => setEntriesOf(m)} className="px-3 py-1.5 text-xs text-muted hover:bg-surface-alt rounded-lg">{t('mailing.entries')}</button>
            <button onClick={() => openEdit(m)} className="px-3 py-1.5 text-xs text-primary hover:bg-primary-light rounded-lg">{t('actions.edit')}</button>
            <button onClick={() => setDelTarget(m)} className="px-3 py-1.5 text-xs text-red hover:bg-red-light rounded-lg">{t('actions.delete')}</button>
          </div>
        </div>
      ))}

      {showForm && (
        <Modal wide title={editing ? `${t('actions.edit')} — ${editing.name}` : t('mailing.new')} onClose={() => setShowForm(false)}>
          <form onSubmit={submit} className="space-y-4">
            <Field label={t('mailing.name')}>
              <input required value={fName} onChange={e => setFName(e.target.value)} className={inputCls} />
            </Field>
            <Field label={t('mailing.description')}>
              <input value={fDesc} onChange={e => setFDesc(e.target.value)} className={inputCls} />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label={t('mailing.dedup')}>
                <select value={fDedup} onChange={e => setFDedup(e.target.value as DedupPolicy)} className={inputCls}>
                  <option value="customer_context">customer_context</option>
                  <option value="customer">customer</option>
                  <option value="none">none</option>
                </select>
              </Field>
              <Field label={t('mailing.ttl')} hint={t('mailing.ttlHint')}>
                <input type="number" min={1} value={fTtl} onChange={e => setFTtl(e.target.value)} className={inputCls} />
              </Field>
            </div>
            <Field label={t('mailing.contract')} hint={t('mailing.contractHint')}>
              <input value={fContract} onChange={e => setFContract(e.target.value)} placeholder="survey_context_v1" className={inputCls} />
            </Field>

            <div>
              <label className="block text-xs font-medium text-dark mb-1">{t('mailing.columnMap')}</label>
              <ColumnMapEditor cm={fCm} setCm={setFCm} />
            </div>

            <div className="flex gap-2 justify-end pt-2 border-t border-border">
              <button type="button" onClick={() => setShowForm(false)} className="px-4 py-2 text-sm text-muted hover:text-dark">{t('actions.cancel')}</button>
              <button type="submit" disabled={saving} className="px-4 py-2 text-sm bg-primary text-white rounded-lg hover:bg-primary-dark disabled:opacity-50">
                {saving ? t('actions.saving') : (editing ? t('actions.save') : t('actions.create'))}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {entriesOf && <EntriesModal api={api} mailing={entriesOf} onClose={() => setEntriesOf(null)} />}

      {importReport && (
        <Modal title={`${t('mailing.importReport')} — ${importReport.name}`} onClose={() => setImportReport(null)}>
          <div className="space-y-2 text-sm">
            <div className="grid grid-cols-3 gap-2">
              {(['total', 'added', 'deduped', 'resolved', 'unresolved'] as const).map(k => (
                <div key={k} className="bg-surface-muted rounded-lg p-2 text-center">
                  <div className="text-lg font-semibold text-dark">{importReport.rep[k]}</div>
                  <div className="text-xs text-muted">{t(`mailing.report.${k}`)}</div>
                </div>
              ))}
              <div className="bg-surface-muted rounded-lg p-2 text-center">
                <div className="text-lg font-semibold text-red-text">{importReport.rep.rejected.length}</div>
                <div className="text-xs text-muted">{t('mailing.report.rejected')}</div>
              </div>
            </div>
            {importReport.rep.rejected.length > 0 && (
              <div className="mt-2 max-h-40 overflow-y-auto text-xs">
                {importReport.rep.rejected.map((r, i) => (
                  <div key={i} className="text-red-text">{t('mailing.report.line')} {r.row}: {r.reason}</div>
                ))}
              </div>
            )}
          </div>
        </Modal>
      )}

      {delTarget && (
        <ConfirmModal message={t('mailing.confirmDelete', { name: delTarget.name })}
          confirmLabel={t('actions.delete')} onCancel={() => setDelTarget(null)} onConfirm={confirmDelete} />
      )}
    </div>
  )
}
