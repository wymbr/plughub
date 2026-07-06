/**
 * DialogFormsPage.tsx
 * /config/dialog-forms — editor for the dialog primitive's DialogForms
 * (survey + OTP content). Closes the "form = tenant data, must be UI-editable"
 * debt (forms were seed-script-only). List + node editor + publish, against
 * the dialog-api (/v1/dialog proxy).
 *
 * MVP scope: single default-locale editing (LocalizedText edited as a plain
 * string). Multi-locale editing + preview are follow-ups.
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Trash2, ArrowUp, ArrowDown, Check, AlertTriangle, FileText } from 'lucide-react'
import { useAuth } from '@/auth/useAuth'
import {
  useDialogForms,
  getDialogForm,
  createDialogForm,
  updateDialogForm,
  publishDialogForm,
  type DialogForm,
  type DialogNode,
  type StatementNode,
  type QuestionNode,
  type DialogOption,
  type LocalizedText,
  type DialogVisibility,
  type DialogInteraction,
} from '@/api/dialog-hooks'

// ── LocalizedText / visibility helpers (single-locale MVP) ────────────────────

function ltToStr(t: LocalizedText | undefined, locale: string): string {
  if (t == null) return ''
  if (typeof t === 'string') return t
  return t[locale] ?? Object.values(t)[0] ?? ''
}

type VisSelect = 'all' | 'agents_only' | 'customer'
const CUSTOMER_VIS = ['@ctx.session.customer_participant_id']

function visToSelect(v: DialogVisibility | undefined): VisSelect {
  if (v === 'agents_only') return 'agents_only'
  if (Array.isArray(v)) return 'customer'
  return 'all'
}
function selectToVis(s: VisSelect): DialogVisibility | undefined {
  if (s === 'agents_only') return 'agents_only'
  if (s === 'customer') return CUSTOMER_VIS
  return undefined // "all" → omit
}

const INTERACTIONS: DialogInteraction[] = ['text', 'button', 'list', 'checklist', 'form']
const HAS_OPTIONS = (i: DialogInteraction) => i === 'button' || i === 'list' || i === 'checklist'

let _seq = 0
const nid = (p: string) => `${p}_${Date.now().toString(36)}${(_seq++).toString(36)}`

function emptyForm(locale: string): DialogForm {
  return {
    form_id: '', name: '', default_locale: locale, locales: [locale], nodes: [], tags: [], status: 'draft',
  }
}
function newStatement(): StatementNode {
  return { id: nid('stmt'), kind: 'statement', text: '' }
}
function newQuestion(): QuestionNode {
  return { id: nid('q'), kind: 'question', prompt: '', interaction: 'text', output_key: 'answer' }
}

// ── Page ──────────────────────────────────────────────────────────────────────

const DialogFormsPage: React.FC = () => {
  const { t } = useTranslation('dialogForms')
  const { tenantId: TENANT } = useAuth()
  const { forms, loading, reload } = useDialogForms(TENANT)

  const [draft, setDraft]     = useState<DialogForm | null>(null)
  const [isNew, setIsNew]     = useState(false)
  const [busy, setBusy]       = useState(false)
  const [msg, setMsg]         = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const openNew = () => { setDraft(emptyForm('pt-BR')); setIsNew(true); setMsg(null) }

  const openEdit = useCallback(async (formId: string) => {
    setMsg(null)
    try {
      const full = await getDialogForm(TENANT, formId)
      setDraft(full); setIsNew(false)
    } catch (e) {
      setMsg({ kind: 'err', text: String(e) })
    }
  }, [TENANT])

  const patch = (p: Partial<DialogForm>) => setDraft(d => (d ? { ...d, ...p } : d))
  const setNodes = (nodes: DialogNode[]) => patch({ nodes })

  const addNode = (n: DialogNode) => draft && setNodes([...draft.nodes, n])
  const updateNode = (idx: number, n: DialogNode) => {
    if (!draft) return
    const nodes = draft.nodes.slice(); nodes[idx] = n; setNodes(nodes)
  }
  const removeNode = (idx: number) => draft && setNodes(draft.nodes.filter((_, i) => i !== idx))
  const moveNode = (idx: number, dir: -1 | 1) => {
    if (!draft) return
    const j = idx + dir
    if (j < 0 || j >= draft.nodes.length) return
    const nodes = draft.nodes.slice();
    [nodes[idx], nodes[j]] = [nodes[j], nodes[idx]]
    setNodes(nodes)
  }

  const save = async (publish: boolean) => {
    if (!draft) return
    if (!draft.form_id.trim()) { setMsg({ kind: 'err', text: t('err.formIdRequired') }); return }
    if (draft.nodes.length === 0) { setMsg({ kind: 'err', text: t('err.nodesRequired') }); return }
    setBusy(true); setMsg(null)
    const body = {
      form_id: draft.form_id.trim(),
      name: draft.name,
      description: draft.description,
      default_locale: draft.default_locale || 'pt-BR',
      locales: draft.locales?.length ? draft.locales : [draft.default_locale || 'pt-BR'],
      nodes: draft.nodes,
      tags: draft.tags ?? [],
    }
    try {
      if (isNew) { await createDialogForm(TENANT, body); setIsNew(false) }
      else       { await updateDialogForm(TENANT, draft.form_id, body) }
      if (publish) await publishDialogForm(TENANT, draft.form_id)
      setMsg({ kind: 'ok', text: publish ? t('msg.published') : t('msg.saved') })
      await reload()
      const fresh = await getDialogForm(TENANT, draft.form_id)
      setDraft(fresh)
    } catch (e) {
      setMsg({ kind: 'err', text: String(e) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex h-full gap-4 p-4">
      {/* ── List ── */}
      <aside className="w-72 shrink-0 border rounded-lg bg-white overflow-auto">
        <div className="flex items-center justify-between px-3 py-2 border-b">
          <h2 className="font-semibold text-sm text-gray-700">{t('title')}</h2>
          <button onClick={openNew}
            className="flex items-center gap-1 text-xs bg-blue-700 text-white px-2 py-1 rounded hover:bg-blue-800">
            <Plus size={14} /> {t('new')}
          </button>
        </div>
        {loading && <div className="p-3 text-xs text-gray-400">{t('loading')}</div>}
        {!loading && forms.length === 0 && <div className="p-3 text-xs text-gray-400">{t('empty')}</div>}
        <ul>
          {forms.map(f => (
            <li key={f.form_id}>
              <button onClick={() => openEdit(f.form_id)}
                className={`w-full text-left px-3 py-2 border-b hover:bg-gray-50 ${draft?.form_id === f.form_id ? 'bg-blue-50' : ''}`}>
                <div className="text-sm text-gray-800 truncate">{f.name || f.form_id}</div>
                <div className="text-[11px] text-gray-400 flex gap-2">
                  <span>{f.form_id}</span>
                  <span className={f.status === 'published' ? 'text-green-600' : 'text-amber-600'}>
                    {f.status} v{f.version}
                  </span>
                </div>
              </button>
            </li>
          ))}
        </ul>
      </aside>

      {/* ── Editor ── */}
      <main className="flex-1 border rounded-lg bg-white overflow-auto">
        {!draft && (
          <div className="h-full flex flex-col items-center justify-center text-gray-400 gap-2">
            <FileText size={32} /> <span className="text-sm">{t('selectOrNew')}</span>
          </div>
        )}
        {draft && (
          <div className="p-4 space-y-4">
            {/* metadata */}
            <div className="grid grid-cols-2 gap-3">
              <label className="text-xs text-gray-600">
                {t('field.formId')}
                <input value={draft.form_id} disabled={!isNew}
                  onChange={e => patch({ form_id: e.target.value })}
                  placeholder="dialog_meu_form"
                  className="mt-1 w-full border rounded px-2 py-1 text-sm disabled:bg-gray-100" />
              </label>
              <label className="text-xs text-gray-600">
                {t('field.name')}
                <input value={draft.name} onChange={e => patch({ name: e.target.value })}
                  className="mt-1 w-full border rounded px-2 py-1 text-sm" />
              </label>
              <label className="text-xs text-gray-600">
                {t('field.defaultLocale')}
                <input value={draft.default_locale} onChange={e => patch({ default_locale: e.target.value })}
                  className="mt-1 w-full border rounded px-2 py-1 text-sm" />
              </label>
              <label className="text-xs text-gray-600">
                {t('field.tags')}
                <input value={(draft.tags ?? []).join(', ')}
                  onChange={e => patch({ tags: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })}
                  className="mt-1 w-full border rounded px-2 py-1 text-sm" />
              </label>
            </div>

            {/* nodes */}
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-gray-700">{t('nodes')}</h3>
                <div className="flex-1" />
                <button onClick={() => addNode(newStatement())}
                  className="text-xs border px-2 py-1 rounded hover:bg-gray-50">+ {t('node.statement')}</button>
                <button onClick={() => addNode(newQuestion())}
                  className="text-xs border px-2 py-1 rounded hover:bg-gray-50">+ {t('node.question')}</button>
              </div>

              {draft.nodes.map((node, idx) => (
                <NodeCard key={node.id} node={node} idx={idx} total={draft.nodes.length}
                  locale={draft.default_locale}
                  onChange={n => updateNode(idx, n)}
                  onRemove={() => removeNode(idx)}
                  onMove={dir => moveNode(idx, dir)} />
              ))}
            </div>

            {/* actions */}
            <div className="flex items-center gap-3 pt-2 border-t">
              <button disabled={busy} onClick={() => save(false)}
                className="text-sm bg-gray-700 text-white px-3 py-1.5 rounded hover:bg-gray-800 disabled:opacity-50">
                {t('action.saveDraft')}
              </button>
              <button disabled={busy} onClick={() => save(true)}
                className="text-sm bg-blue-700 text-white px-3 py-1.5 rounded hover:bg-blue-800 disabled:opacity-50">
                {t('action.publish')}
              </button>
              {msg && (
                <span className={`flex items-center gap-1 text-xs ${msg.kind === 'ok' ? 'text-green-600' : 'text-red-600'}`}>
                  {msg.kind === 'ok' ? <Check size={14} /> : <AlertTriangle size={14} />}{msg.text}
                </span>
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  )
}

// ── Node card ─────────────────────────────────────────────────────────────────

interface NodeCardProps {
  node: DialogNode
  idx: number
  total: number
  locale: string
  onChange: (n: DialogNode) => void
  onRemove: () => void
  onMove: (dir: -1 | 1) => void
}

const NodeCard: React.FC<NodeCardProps> = ({ node, idx, total, locale, onChange, onRemove, onMove }) => {
  const { t } = useTranslation('dialogForms')
  const isQ = node.kind === 'question'
  return (
    <div className="border rounded-lg p-3 bg-gray-50 space-y-2">
      <div className="flex items-center gap-2">
        <span className={`text-[10px] font-semibold px-2 py-0.5 rounded ${isQ ? 'bg-blue-100 text-blue-700' : 'bg-gray-200 text-gray-600'}`}>
          {isQ ? t('node.question') : t('node.statement')}
        </span>
        <input value={node.id} onChange={e => onChange({ ...node, id: e.target.value })}
          className="text-xs border rounded px-2 py-0.5 w-40 bg-white" />
        <div className="flex-1" />
        <button onClick={() => onMove(-1)} disabled={idx === 0} className="text-gray-400 hover:text-gray-700 disabled:opacity-30"><ArrowUp size={15} /></button>
        <button onClick={() => onMove(1)} disabled={idx === total - 1} className="text-gray-400 hover:text-gray-700 disabled:opacity-30"><ArrowDown size={15} /></button>
        <button onClick={onRemove} className="text-red-400 hover:text-red-600"><Trash2 size={15} /></button>
      </div>

      {node.kind === 'statement' && (
        <StatementEditor node={node} locale={locale} onChange={onChange} />
      )}
      {node.kind === 'question' && (
        <QuestionEditor node={node} locale={locale} onChange={onChange} />
      )}
    </div>
  )
}

const StatementEditor: React.FC<{ node: StatementNode; locale: string; onChange: (n: DialogNode) => void }> =
({ node, locale, onChange }) => {
  const { t } = useTranslation('dialogForms')
  return (
  <div className="space-y-2">
    <label className="block text-xs text-gray-600">
      {t('field.text')}
      <textarea value={ltToStr(node.text, locale)} rows={2}
        onChange={e => onChange({ ...node, text: e.target.value })}
        className="mt-1 w-full border rounded px-2 py-1 text-sm bg-white" />
    </label>
    <VisibilityRow value={node.visibility}
      onChange={v => onChange({ ...node, visibility: v })} />
  </div>
  )
}

const QuestionEditor: React.FC<{ node: QuestionNode; locale: string; onChange: (n: DialogNode) => void }> =
({ node, locale, onChange }) => {
  const { t } = useTranslation('dialogForms')
  const setOptions = (options: DialogOption[]) => onChange({ ...node, options })
  return (
    <div className="space-y-2">
      <label className="block text-xs text-gray-600">
        {t('field.prompt')}
        <textarea value={ltToStr(node.prompt, locale)} rows={2}
          onChange={e => onChange({ ...node, prompt: e.target.value })}
          className="mt-1 w-full border rounded px-2 py-1 text-sm bg-white" />
      </label>
      <div className="grid grid-cols-3 gap-2">
        <label className="text-xs text-gray-600">
          {t('field.interaction')}
          <select value={node.interaction}
            onChange={e => onChange({ ...node, interaction: e.target.value as DialogInteraction })}
            className="mt-1 w-full border rounded px-2 py-1 text-sm bg-white">
            {INTERACTIONS.map(i => <option key={i} value={i}>{i}</option>)}
          </select>
        </label>
        <label className="text-xs text-gray-600">
          {t('field.outputKey')}
          <input value={node.output_key} onChange={e => onChange({ ...node, output_key: e.target.value })}
            className="mt-1 w-full border rounded px-2 py-1 text-sm bg-white" />
        </label>
        <label className="text-xs text-gray-600">
          {t('field.metric')}
          <input value={node.capture?.metric ?? ''}
            onChange={e => onChange({ ...node, capture: { ...node.capture, metric: e.target.value || undefined } })}
            placeholder="nps / csat…"
            className="mt-1 w-full border rounded px-2 py-1 text-sm bg-white" />
        </label>
      </div>

      <VisibilityRow value={node.visibility} onChange={v => onChange({ ...node, visibility: v })} />

      {/* validation */}
      <div className="flex items-center gap-3 text-xs text-gray-600">
        <label className="flex items-center gap-1">
          <input type="checkbox" checked={!!node.validation?.numeric}
            onChange={e => onChange({ ...node, validation: { ...node.validation, numeric: e.target.checked || undefined } })} />
          {t('field.numeric')}
        </label>
        <label className="flex items-center gap-1">{t('field.min')}
          <input type="number" value={node.validation?.min ?? ''} className="w-16 border rounded px-1 py-0.5 bg-white"
            onChange={e => onChange({ ...node, validation: { ...node.validation, min: e.target.value === '' ? undefined : Number(e.target.value) } })} />
        </label>
        <label className="flex items-center gap-1">{t('field.max')}
          <input type="number" value={node.validation?.max ?? ''} className="w-16 border rounded px-1 py-0.5 bg-white"
            onChange={e => onChange({ ...node, validation: { ...node.validation, max: e.target.value === '' ? undefined : Number(e.target.value) } })} />
        </label>
      </div>

      {/* options (button/list/checklist) */}
      {HAS_OPTIONS(node.interaction) && (
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-gray-600">{t('field.options')}</span>
            <button onClick={() => setOptions([...(node.options ?? []), { id: '', label: '' }])}
              className="text-[11px] border px-1.5 py-0.5 rounded hover:bg-white">+ {t('field.option')}</button>
          </div>
          {(node.options ?? []).map((opt, i) => (
            <div key={i} className="flex items-center gap-2">
              <input value={opt.id} placeholder="id"
                onChange={e => { const o = (node.options ?? []).slice(); o[i] = { ...o[i], id: e.target.value }; setOptions(o) }}
                className="w-24 border rounded px-2 py-0.5 text-xs bg-white" />
              <input value={ltToStr(opt.label, locale)} placeholder={t('field.label')}
                onChange={e => { const o = (node.options ?? []).slice(); o[i] = { ...o[i], label: e.target.value }; setOptions(o) }}
                className="flex-1 border rounded px-2 py-0.5 text-xs bg-white" />
              <button onClick={() => setOptions((node.options ?? []).filter((_, k) => k !== i))}
                className="text-red-400 hover:text-red-600"><Trash2 size={13} /></button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const VisibilityRow: React.FC<{ value: DialogVisibility | undefined; onChange: (v: DialogVisibility | undefined) => void }> =
({ value, onChange }) => {
  const { t } = useTranslation('dialogForms')
  return (
  <label className="text-xs text-gray-600">
    {t('field.visibility')}{' '}
    <select value={visToSelect(value)} onChange={e => onChange(selectToVis(e.target.value as VisSelect))}
      className="border rounded px-2 py-0.5 text-sm bg-white">
      <option value="all">{t('vis.all')}</option>
      <option value="customer">{t('vis.customer')}</option>
      <option value="agents_only">{t('vis.agentsOnly')}</option>
    </select>
  </label>
  )
}

export default DialogFormsPage
