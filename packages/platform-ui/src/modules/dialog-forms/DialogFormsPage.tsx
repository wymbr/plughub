/**
 * DialogFormsPage.tsx
 * /config/dialog-forms — editor for the dialog primitive's DialogForms
 * (survey + OTP content), organized as a sequence of BLOCKS: each block is
 * either a scored INSTRUMENT (its questions compose one signal) or a plain
 * DIALOG block (unscored statements / verbatim / OTP). The block view is a
 * projection over the canonical flat nodes[] + dimensions[] (see dialog-blocks.ts);
 * the runtime is unchanged.
 *
 * Checkpoint A: block structure + projection + reused node editors. The instrument
 * still carries scale/aggregation; per-question interaction stays on the question.
 * Checkpoint B moves interaction/options to the instrument + folds in completude.
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Plus, Trash2, ArrowUp, ArrowDown, ChevronRight, ChevronDown,
  SlidersHorizontal, Check, AlertTriangle, FileText,
} from 'lucide-react'
import { useAuth } from '@/auth/useAuth'
import { useNamespace } from '@/modules/config-plataforma/api/config-hooks'
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
  type ScoreAggregation,
} from '@/api/dialog-hooks'
import { type Block, buildBlocks, flattenBlocks } from './dialog-blocks'

// ── LocalizedText / visibility helpers ────────────────────────────────────────

function ltToStr(t: LocalizedText | undefined, locale: string, defaultLocale: string): string {
  if (t == null) return ''
  if (typeof t === 'string') return locale === defaultLocale ? t : ''
  return t[locale] ?? ''
}

function setLt(current: LocalizedText | undefined, locale: string, value: string, defaultLocale: string): LocalizedText {
  let map: Record<string, string> = {}
  if (typeof current === 'string') map[defaultLocale] = current
  else if (current) map = { ...current }
  if (value === '') delete map[locale]
  else map[locale] = value
  const keys = Object.keys(map)
  if (keys.length === 0) return ''
  if (keys.length === 1 && keys[0] === defaultLocale) return map[defaultLocale]!
  return map
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
  return undefined
}

const INTERACTIONS: DialogInteraction[] = ['text', 'button', 'list', 'checklist', 'form']
const HAS_OPTIONS = (i: DialogInteraction) => i === 'button' || i === 'list' || i === 'checklist'

let _seq = 0
const nid = (p: string) => `${p}_${Date.now().toString(36)}${(_seq++).toString(36)}`

function emptyForm(locale: string): DialogForm {
  return { form_id: '', name: '', default_locale: locale, locales: [locale], nodes: [], dimensions: [], tags: [], status: 'draft' }
}

const AGGREGATIONS: ScoreAggregation[] = ['weighted_mean', 'min']

// Instrument catalog (spec §domain: CSAT/NPS/CES/PMF/FCR; OTP is not a survey
// instrument). Read from config-api (namespace `survey`); this is only the
// graceful-degradation fallback.
interface Instrument { id: string; label: string; scale: { min: number; max: number }; aggregation?: ScoreAggregation }
const DEFAULT_INSTRUMENTS: Instrument[] = [
  { id: 'csat', label: 'CSAT', scale: { min: 1, max: 5 } },
  { id: 'nps',  label: 'NPS',  scale: { min: 0, max: 10 } },
  { id: 'ces',  label: 'CES',  scale: { min: 1, max: 7 } },
  { id: 'pmf',  label: 'PMF',  scale: { min: 1, max: 3 } },
  { id: 'fcr',  label: 'FCR',  scale: { min: 0, max: 1 } },
]
function resolveInstruments(raw: unknown): Instrument[] {
  if (!Array.isArray(raw)) return DEFAULT_INSTRUMENTS
  const ok = raw.filter(
    (x): x is Instrument =>
      !!x && typeof x === 'object' && typeof (x as Instrument).id === 'string' &&
      !!(x as Instrument).scale && typeof (x as Instrument).scale.max === 'number',
  )
  return ok.length > 0 ? ok : DEFAULT_INSTRUMENTS
}

function newStatement(): StatementNode { return { id: nid('stmt'), kind: 'statement', text: '' } }
function newQuestion(): QuestionNode { return { id: nid('q'), kind: 'question', prompt: '', interaction: 'text', output_key: 'answer' } }

const DIALOG_TYPE = '__dialog__'
const CUSTOM_TYPE = '__custom__'

// ── Page ──────────────────────────────────────────────────────────────────────

const DialogFormsPage: React.FC = () => {
  const { t } = useTranslation('dialogForms')
  const { tenantId: TENANT } = useAuth()
  const { forms, loading, reload } = useDialogForms(TENANT)
  const { entries: surveyCfg } = useNamespace(TENANT, 'survey')
  const instruments = resolveInstruments(surveyCfg['instruments']?.value)

  const [draft, setDraft]   = useState<DialogForm | null>(null)   // form-level meta
  const [blocks, setBlocks] = useState<Block[]>([])               // structure (projection)
  const [isNew, setIsNew]   = useState(false)
  const [busy, setBusy]     = useState(false)
  const [msg, setMsg]       = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [editLocale, setEditLocale] = useState('pt-BR')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [adjust, setAdjust]     = useState<Set<number>>(new Set())

  useEffect(() => {
    if (draft) setEditLocale(draft.default_locale || 'pt-BR')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft?.form_id])

  const openNew = () => {
    setDraft(emptyForm('pt-BR')); setBlocks([]); setIsNew(true); setMsg(null)
    setExpanded(new Set()); setAdjust(new Set())
  }
  const openEdit = useCallback(async (formId: string) => {
    setMsg(null)
    try {
      const full = await getDialogForm(TENANT, formId)
      setDraft(full); setBlocks(buildBlocks(full)); setIsNew(false)
      setExpanded(new Set()); setAdjust(new Set())
    } catch (e) {
      setMsg({ kind: 'err', text: String(e) })
    }
  }, [TENANT])

  const patch = (p: Partial<DialogForm>) => setDraft(d => (d ? { ...d, ...p } : d))

  // ── Locale ─────────────────────────────────────────────────────────────────
  const localeList = draft ? (draft.locales?.length ? draft.locales : [draft.default_locale]) : []
  const addLocale = (loc: string) => {
    if (!draft) return
    const l = loc.trim()
    if (!l || localeList.includes(l)) return
    patch({ locales: [...localeList, l] }); setEditLocale(l)
  }
  const removeLocale = (loc: string) => {
    if (!draft || loc === draft.default_locale) return
    patch({ locales: localeList.filter(x => x !== loc) })
    if (editLocale === loc) setEditLocale(draft.default_locale)
  }

  // ── Blocks ───────────────────────────────────────────────────────────────────
  const addBlock = () => setBlocks(bs => [...bs, { kind: 'dialog', nodes: [] }])
  const updateBlock = (idx: number, b: Block) => setBlocks(bs => bs.map((x, i) => (i === idx ? b : x)))
  const removeBlock = (idx: number) => setBlocks(bs => bs.filter((_, i) => i !== idx))
  const moveBlock = (idx: number, dir: -1 | 1) => setBlocks(bs => {
    const j = idx + dir
    if (j < 0 || j >= bs.length) return bs
    const next = bs.slice();[next[idx], next[j]] = [next[j]!, next[idx]!]; return next
  })

  const toggleExpand = (id: string) => setExpanded(s => {
    const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n
  })
  const toggleAdjust = (idx: number) => setAdjust(s => {
    const n = new Set(s); n.has(idx) ? n.delete(idx) : n.add(idx); return n
  })

  const save = async (publish: boolean) => {
    if (!draft) return
    if (!draft.form_id.trim()) { setMsg({ kind: 'err', text: t('err.formIdRequired') }); return }
    const { nodes, dimensions } = flattenBlocks(blocks)
    if (nodes.length === 0) { setMsg({ kind: 'err', text: t('err.nodesRequired') }); return }
    if (dimensions.some(d => !d.dimension_id)) { setMsg({ kind: 'err', text: t('err.instrumentType') }); return }
    setBusy(true); setMsg(null)
    const body = {
      form_id: draft.form_id.trim(),
      name: draft.name,
      description: draft.description,
      default_locale: draft.default_locale || 'pt-BR',
      locales: Array.from(new Set([draft.default_locale || 'pt-BR', ...(draft.locales ?? [])])),
      nodes,
      dimensions,
      tags: draft.tags ?? [],
    }
    try {
      if (isNew) { await createDialogForm(TENANT, body); setIsNew(false) }
      else       { await updateDialogForm(TENANT, draft.form_id, body) }
      if (publish) await publishDialogForm(TENANT, draft.form_id)
      setMsg({ kind: 'ok', text: publish ? t('msg.published') : t('msg.saved') })
      await reload()
      const fresh = await getDialogForm(TENANT, draft.form_id)
      setDraft(fresh); setBlocks(buildBlocks(fresh))
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

            <LocaleBar locales={localeList} defaultLocale={draft.default_locale} editLocale={editLocale}
              onSelect={setEditLocale} onAdd={addLocale} onRemove={removeLocale} />

            {/* blocks */}
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-gray-700">{t('blocks.title')}</h3>
                <span className="text-[11px] text-gray-400">{t('dimensions.hint')}</span>
                <div className="flex-1" />
                <button onClick={addBlock}
                  className="text-xs border px-2 py-1 rounded hover:bg-gray-50">+ {t('blocks.add')}</button>
              </div>
              {blocks.length === 0 && <p className="text-[11px] text-gray-400 py-1">{t('blocks.empty')}</p>}
              {blocks.map((block, idx) => (
                <BlockCard key={idx} block={block} idx={idx} total={blocks.length}
                  instruments={instruments} locale={editLocale} defaultLocale={draft.default_locale}
                  expanded={expanded} onToggleExpand={toggleExpand}
                  adjust={adjust.has(idx)} onToggleAdjust={() => toggleAdjust(idx)}
                  onChange={b => updateBlock(idx, b)} onRemove={() => removeBlock(idx)}
                  onMove={dir => moveBlock(idx, dir)} />
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

// ── Block card ──────────────────────────────────────────────────────────────────

interface BlockCardProps {
  block: Block
  idx: number
  total: number
  instruments: Instrument[]
  locale: string
  defaultLocale: string
  expanded: Set<string>
  onToggleExpand: (id: string) => void
  adjust: boolean
  onToggleAdjust: () => void
  onChange: (b: Block) => void
  onRemove: () => void
  onMove: (dir: -1 | 1) => void
}

const BlockCard: React.FC<BlockCardProps> = ({
  block, idx, total, instruments, locale, defaultLocale, expanded, onToggleExpand,
  adjust, onToggleAdjust, onChange, onRemove, onMove,
}) => {
  const { t } = useTranslation('dialogForms')
  const isInstrument = block.kind === 'instrument'
  const dim = block.kind === 'instrument' ? block.dim : null
  const isCustom = !!dim && dim.dimension_id !== '' && !instruments.some(i => i.id === dim.dimension_id)

  // Node ops within this block
  const setNodes = (nodes: DialogNode[]) =>
    onChange(block.kind === 'instrument' ? { kind: 'instrument', dim: block.dim, nodes } : { kind: 'dialog', nodes })
  const addNode = (n: DialogNode) => setNodes([...block.nodes, n])
  const updateNode = (i: number, n: DialogNode) => setNodes(block.nodes.map((x, j) => (j === i ? n : x)))
  const removeNode = (i: number) => setNodes(block.nodes.filter((_, j) => j !== i))
  const moveNode = (i: number, dir: -1 | 1) => {
    const j = i + dir
    if (j < 0 || j >= block.nodes.length) return
    const next = block.nodes.slice();[next[i], next[j]] = [next[j]!, next[i]!]; setNodes(next)
  }

  const changeType = (val: string) => {
    if (val === DIALOG_TYPE) { onChange({ kind: 'dialog', nodes: block.nodes }); return }
    if (val === CUSTOM_TYPE) {
      onChange({ kind: 'instrument', dim: { dimension_id: '', scale: { min: 1, max: 5 }, aggregation: 'weighted_mean' }, nodes: block.nodes })
      return
    }
    const inst = instruments.find(i => i.id === val)
    onChange({
      kind: 'instrument',
      dim: { dimension_id: val, scale: inst ? { ...inst.scale } : { min: 1, max: 5 }, aggregation: inst?.aggregation ?? 'weighted_mean' },
      nodes: block.nodes,
    })
  }
  const updateDim = (p: Partial<NonNullable<typeof dim>>) => {
    if (dim) onChange({ kind: 'instrument', dim: { ...dim, ...p }, nodes: block.nodes })
  }

  // Weight %, computed within the block
  const qs = block.nodes.filter((n): n is QuestionNode => n.kind === 'question')
  const wsum = qs.reduce((a, q) => a + (q.capture?.weight ?? 1), 0)
  const pctOf = (q: QuestionNode) => (wsum > 0 ? Math.round(((q.capture?.weight ?? 1) / wsum) * 100) : 0)

  const typeValue = !isInstrument ? DIALOG_TYPE : (isCustom ? CUSTOM_TYPE : dim!.dimension_id)
  const accent = isInstrument ? 'border-l-blue-400' : 'border-l-gray-300'

  return (
    <div className={`border rounded-lg bg-white border-l-4 ${accent} p-3 space-y-2`}>
      {/* header */}
      <div className="flex items-center gap-2 flex-wrap">
        <select value={typeValue} onChange={e => changeType(e.target.value)}
          className="border rounded px-2 py-1 text-sm bg-white font-medium">
          <option value={DIALOG_TYPE}>{t('block.dialog')}</option>
          {instruments.map(i => <option key={i.id} value={i.id}>{i.label}</option>)}
          <option value={CUSTOM_TYPE}>{t('dimensions.custom')}</option>
        </select>
        {isInstrument && (isCustom || dim!.dimension_id === '') && (
          <input value={dim!.dimension_id} placeholder="metric_key"
            onChange={e => updateDim({ dimension_id: e.target.value })}
            className="w-32 border rounded px-2 py-1 text-sm bg-white font-mono" />
        )}
        {isInstrument ? (
          <>
            <input value={ltToStr(dim!.label, locale, defaultLocale)} placeholder={t('dimensions.labelPlaceholder')}
              onChange={e => updateDim({ label: setLt(dim!.label, locale, e.target.value, defaultLocale) })}
              className="flex-1 min-w-[100px] border rounded px-2 py-1 text-sm bg-white" />
            <span className="text-xs text-gray-500">{t('dimensions.scale')}</span>
            <input type="number" value={dim!.scale.min ?? 0}
              onChange={e => updateDim({ scale: { ...dim!.scale, min: Number(e.target.value) } })}
              className="w-12 border rounded px-1 py-0.5 text-center text-xs bg-white" />
            <span className="text-gray-400">–</span>
            <input type="number" value={dim!.scale.max}
              onChange={e => updateDim({ scale: { ...dim!.scale, max: Number(e.target.value) } })}
              className="w-12 border rounded px-1 py-0.5 text-center text-xs bg-white" />
            <select value={dim!.aggregation ?? 'weighted_mean'}
              onChange={e => updateDim({ aggregation: e.target.value as ScoreAggregation })}
              className="border rounded px-1 py-0.5 text-xs bg-white">
              {AGGREGATIONS.map(a => <option key={a} value={a}>{a}</option>)}
            </select>
          </>
        ) : (
          <span className="flex-1 text-xs text-gray-400">{t('block.dialogHint')}</span>
        )}
        <button onClick={() => onMove(-1)} disabled={idx === 0} className="text-gray-400 hover:text-gray-700 disabled:opacity-30"><ArrowUp size={15} /></button>
        <button onClick={() => onMove(1)} disabled={idx === total - 1} className="text-gray-400 hover:text-gray-700 disabled:opacity-30"><ArrowDown size={15} /></button>
        <button onClick={onRemove} className="text-red-400 hover:text-red-600"><Trash2 size={15} /></button>
      </div>

      {/* nodes */}
      <div className="space-y-1.5 pl-2 border-l-2 border-gray-100">
        {block.nodes.map((node, i) => (
          <NodeRow key={node.id} node={node} first={i === 0} last={i === block.nodes.length - 1}
            locale={locale} defaultLocale={defaultLocale}
            expanded={expanded.has(node.id)} onToggle={() => onToggleExpand(node.id)}
            showWeight={isInstrument && adjust} pct={node.kind === 'question' ? pctOf(node) : 0}
            onChange={n => updateNode(i, n)} onRemove={() => removeNode(i)} onMove={dir => moveNode(i, dir)} />
        ))}
      </div>

      {/* footer */}
      <div className="flex items-center gap-2 text-xs text-gray-500 pt-1">
        <button onClick={() => addNode(newQuestion())} className="hover:text-gray-800"><Plus size={12} className="inline" /> {t('node.question')}</button>
        <button onClick={() => addNode(newStatement())} className="hover:text-gray-800"><Plus size={12} className="inline" /> {t('node.statement')}</button>
        <div className="flex-1" />
        {isInstrument && qs.length > 0 && (
          <button onClick={onToggleAdjust} className={`flex items-center gap-1 ${adjust ? 'text-blue-700' : 'text-gray-500 hover:text-gray-800'}`}>
            <SlidersHorizontal size={12} /> {adjust ? t('block.weightsHide') : t('block.weightsAdjust')}
          </button>
        )}
      </div>
    </div>
  )
}

// ── Node row (collapsed summary + expandable editor) ────────────────────────────

const NodeRow: React.FC<{
  node: DialogNode; first: boolean; last: boolean; locale: string; defaultLocale: string
  expanded: boolean; onToggle: () => void; showWeight: boolean; pct: number
  onChange: (n: DialogNode) => void; onRemove: () => void; onMove: (dir: -1 | 1) => void
}> = ({ node, first, last, locale, defaultLocale, expanded, onToggle, showWeight, pct, onChange, onRemove, onMove }) => {
  const { t } = useTranslation('dialogForms')
  const isQ = node.kind === 'question'
  const summary = ltToStr(node.kind === 'question' ? node.prompt : node.text, locale, defaultLocale)
    || (isQ ? t('node.question') : t('node.statement'))
  return (
    <div className="border rounded bg-gray-50">
      <div className="flex items-center gap-2 px-2 py-1">
        <button onClick={onToggle} className="text-gray-400 hover:text-gray-700">
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
        <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${isQ ? 'bg-blue-100 text-blue-700' : 'bg-gray-200 text-gray-600'}`}>
          {isQ ? t('node.question') : t('node.statement')}
        </span>
        <span className="flex-1 text-xs text-gray-700 truncate">{summary}</span>
        {node.kind === 'question' && <span className="text-[10px] text-gray-400">{node.interaction}</span>}
        {node.kind === 'question' && showWeight && (
          <span className="flex items-center gap-1 text-[11px] text-gray-500">
            <input type="number" min={0} value={node.capture?.weight ?? 1}
              onChange={e => onChange({ ...node, capture: { ...node.capture, weight: e.target.value === '' ? undefined : Math.max(0, Number(e.target.value)) } })}
              className="w-12 border rounded px-1 py-0.5 bg-white" />
            <span className="text-blue-700 font-medium">{pct}%</span>
          </span>
        )}
        <button onClick={() => onMove(-1)} disabled={first} className="text-gray-400 hover:text-gray-700 disabled:opacity-30"><ArrowUp size={13} /></button>
        <button onClick={() => onMove(1)} disabled={last} className="text-gray-400 hover:text-gray-700 disabled:opacity-30"><ArrowDown size={13} /></button>
        <button onClick={onRemove} className="text-red-400 hover:text-red-600"><Trash2 size={13} /></button>
      </div>
      {expanded && (
        <div className="px-3 pb-2">
          {node.kind === 'statement'
            ? <StatementEditor node={node} locale={locale} defaultLocale={defaultLocale} onChange={onChange} />
            : <QuestionEditor node={node} locale={locale} defaultLocale={defaultLocale} onChange={onChange} />}
        </div>
      )}
    </div>
  )
}

const StatementEditor: React.FC<{ node: StatementNode; locale: string; defaultLocale: string; onChange: (n: DialogNode) => void }> =
({ node, locale, defaultLocale, onChange }) => {
  const { t } = useTranslation('dialogForms')
  return (
    <div className="space-y-2">
      <label className="block text-xs text-gray-600">
        {t('field.text')}
        <textarea value={ltToStr(node.text, locale, defaultLocale)} rows={2}
          onChange={e => onChange({ ...node, text: setLt(node.text, locale, e.target.value, defaultLocale) })}
          className="mt-1 w-full border rounded px-2 py-1 text-sm bg-white" />
      </label>
      <VisibilityRow value={node.visibility} onChange={v => onChange({ ...node, visibility: v })} />
    </div>
  )
}

const QuestionEditor: React.FC<{ node: QuestionNode; locale: string; defaultLocale: string; onChange: (n: DialogNode) => void }> =
({ node, locale, defaultLocale, onChange }) => {
  const { t } = useTranslation('dialogForms')
  const setOptions = (options: DialogOption[]) => onChange({ ...node, options })
  return (
    <div className="space-y-2">
      <label className="block text-xs text-gray-600">
        {t('field.prompt')}
        <textarea value={ltToStr(node.prompt, locale, defaultLocale)} rows={2}
          onChange={e => onChange({ ...node, prompt: setLt(node.prompt, locale, e.target.value, defaultLocale) })}
          className="mt-1 w-full border rounded px-2 py-1 text-sm bg-white" />
      </label>
      <div className="grid grid-cols-2 gap-2">
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
      </div>

      <VisibilityRow value={node.visibility} onChange={v => onChange({ ...node, visibility: v })} />

      {node.interaction === 'text' && (
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
      )}

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
                onChange={e => { const o = (node.options ?? []).slice(); o[i] = { ...o[i]!, id: e.target.value }; setOptions(o) }}
                className="w-24 border rounded px-2 py-0.5 text-xs bg-white" />
              <input value={ltToStr(opt.label, locale, defaultLocale)} placeholder={t('field.label')}
                onChange={e => { const o = (node.options ?? []).slice(); o[i] = { ...o[i]!, label: setLt(o[i]!.label, locale, e.target.value, defaultLocale) }; setOptions(o) }}
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

// ── Locale bar ──────────────────────────────────────────────────────────────

const LocaleBar: React.FC<{
  locales: string[]; defaultLocale: string; editLocale: string
  onSelect: (l: string) => void; onAdd: (l: string) => void; onRemove: (l: string) => void
}> = ({ locales, defaultLocale, editLocale, onSelect, onAdd, onRemove }) => {
  const { t } = useTranslation('dialogForms')
  const [adding, setAdding] = useState('')
  const commit = () => { onAdd(adding); setAdding('') }
  return (
    <div className="flex items-center flex-wrap gap-2 border rounded-lg bg-gray-50 px-3 py-2">
      <span className="text-xs text-gray-500">{t('locale.editing')}</span>
      {locales.map(l => (
        <span key={l}
          className={`inline-flex items-center gap-1 text-xs rounded px-2 py-0.5 border cursor-pointer ${
            l === editLocale ? 'bg-blue-700 text-white border-blue-700' : 'bg-white text-gray-700 hover:bg-gray-100'
          }`}>
          <button onClick={() => onSelect(l)}>{l}{l === defaultLocale ? ` · ${t('locale.default')}` : ''}</button>
          {l !== defaultLocale && (
            <button onClick={() => onRemove(l)}
              className={l === editLocale ? 'text-blue-100 hover:text-white' : 'text-gray-400 hover:text-red-600'}
              title={t('locale.remove')}>×</button>
          )}
        </span>
      ))}
      <input value={adding} onChange={e => setAdding(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); commit() } }}
        placeholder={t('locale.addPlaceholder')}
        className="w-24 border rounded px-2 py-0.5 text-xs bg-white" />
      <button onClick={commit} disabled={!adding.trim()}
        className="text-xs border px-2 py-0.5 rounded hover:bg-white disabled:opacity-40">+ {t('locale.add')}</button>
    </div>
  )
}

export default DialogFormsPage
