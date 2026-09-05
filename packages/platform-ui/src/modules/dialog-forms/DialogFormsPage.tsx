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
  SlidersHorizontal, Check, AlertTriangle, FileText, Archive, ArchiveRestore, Braces,
} from 'lucide-react'
import { useAuth } from '@/auth/useAuth'
import { useNamespace } from '@/modules/config-plataforma/api/config-hooks'
import {
  useDialogForms,
  getDialogForm,
  createDialogForm,
  updateDialogForm,
  publishDialogForm,
  deleteDialogForm,
  undeleteDialogForm,
  type DialogForm,
  type DialogNode,
  type StatementNode,
  type QuestionNode,
  type DialogOption,
  type LocalizedText,
  type DialogVisibility,
  type DialogInteraction,
  type ScoreAggregation,
  type AskWhen,
  type AskWhenOp,
} from '@/api/dialog-hooks'
import { type Block, buildBlocks, flattenBlocks, reprojectionDrift, formQuestionOf } from './dialog-blocks'
import DialogJsonPanel from './DialogJsonPanel'
import FormBlockEditor from './FormBlockEditor'
import { OptionTreeEditor } from './OptionTreeEditor'
import {
  useFormatCatalog, useMaskedTypes, formatLabel, d8Verdict,
} from './catalog-hooks'

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
const CUSTOMER_VIS = ['@ctx.core.contact.customer_participant_id']
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

/**
 * `form` SAIU daqui em 2026-09-05 e virou TIPO DE BLOCO.
 *
 * Os quatro que sobraram dizem todos a mesma categoria de coisa — *um escalar por
 * turno*. `form` era o quinto valor de uma uniao discriminada: escolhe-lo mudava
 * o significado do painel inteiro e tornava inertes quatro controles da pergunta
 * (`options`, `validation` escalar, `retry`, `masked` do no). Como bloco, esse
 * estado deixa de EXISTIR em vez de precisar ser escondido.
 *
 * ⚠️ Valor legado (`form` numa pergunta de INSTRUMENTO, onde a dimensao vence)
 * ainda e exibido pelo `interactionOptions` abaixo — dropdown que nao mostra o
 * valor corrente o reescreve em silencio no primeiro `onChange`.
 */
const INTERACTIONS: DialogInteraction[] = ['text', 'button', 'list', 'checklist']
const interactionOptions = (atual?: DialogInteraction): DialogInteraction[] =>
  atual && !INTERACTIONS.includes(atual) ? [...INTERACTIONS, atual] : INTERACTIONS
const HAS_OPTIONS = (i?: DialogInteraction) => i === 'button' || i === 'list' || i === 'checklist'

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
const FORM_TYPE   = '__form__'

/** Tipos de campo que os renderizadores reconhecem. `DialogField.type` e string
 *  ABERTA no schema ("adapter maps to UI"), entao valor desconhecido e
 *  PRESERVADO como opcao extra — dropdown que nao mostra o valor corrente o
 *  apaga no primeiro toque. */
const FIELD_TYPES = ['text', 'number', 'money', 'date', 'bool', 'select']
const fieldTypeOptions = (atual?: string): string[] =>
  atual && !FIELD_TYPES.includes(atual) ? [...FIELD_TYPES, atual] : FIELD_TYPES

// ── Page ──────────────────────────────────────────────────────────────────────

const DialogFormsPage: React.FC = () => {
  const { t } = useTranslation('dialogForms')
  const { tenantId: TENANT } = useAuth()
  const [showArchived, setShowArchived] = useState(false)
  const { forms, loading, reload } = useDialogForms(TENANT, showArchived)
  const { entries: surveyCfg } = useNamespace(TENANT, 'survey')
  const instruments = resolveInstruments(surveyCfg['instruments']?.value)

  const [draft, setDraft]   = useState<DialogForm | null>(null)   // form-level meta
  const [blocks, setBlocks] = useState<Block[]>([])               // structure (projection)
  const [isNew, setIsNew]   = useState(false)
  const [busy, setBusy]     = useState(false)
  const [msg, setMsg]       = useState<{ kind: 'ok' | 'warn' | 'err'; text: string } | null>(null)
  /** Editor JSON (importar · editar · pre-visualizar) — a superficie que autora `fields[]`. */
  const [jsonOpen, setJsonOpen] = useState(false)
  const [editLocale, setEditLocale] = useState('pt-BR')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [adjust, setAdjust]     = useState<Set<number>>(new Set())
  /** Caminhos de opção do documento CARREGADO — travam a edição do `id` (D6). */
  const [idsSalvos, setIdsSalvos] = useState<Set<string>>(() => new Set())

  useEffect(() => {
    if (draft) setEditLocale(draft.default_locale || 'pt-BR')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft?.form_id])

  const openNew = () => {
    setDraft(emptyForm('pt-BR')); setBlocks([]); setIsNew(true); setMsg(null)
    setIdsSalvos(new Set())
    setExpanded(new Set()); setAdjust(new Set())
  }
  const openEdit = useCallback(async (formId: string) => {
    setMsg(null)
    try {
      const full = await getDialogForm(TENANT, formId)
      setDraft(full); setBlocks(buildBlocks(full)); setIsNew(false)
      setIdsSalvos(caminhosDeOpcao(full.nodes))
      setExpanded(new Set()); setAdjust(new Set())
    } catch (e) {
      setMsg({ kind: 'err', text: String(e) })
    }
  }, [TENANT])

  const patch = (p: Partial<DialogForm>) => setDraft(d => (d ? { ...d, ...p } : d))

  // ── Editor JSON ─────────────────────────────────────────────────────────────
  /** O documento como o autor deve vê-lo: meta do draft + a estrutura VIVA dos blocos. */
  const docAtual = (): DialogForm => ({ ...(draft as DialogForm), ...flattenBlocks(blocks) })

  /**
   * Aplica o JSON editado. NÃO grava: só troca o rascunho em memória — o
   * Salvar/Publicar continua sendo o único caminho de escrita, com as regras de
   * arquivado e o 409 do servidor intactas.
   *
   * O aviso de reprojeção existe porque `flattenBlocks` não é identidade: ela
   * reescreve `capture` e materializa `interaction`/`options` de instrumento. Com
   * widgets isso era invisível (eles só produziam o que a projeção produziria);
   * com JSON à mão, seria *"editei, apliquei e mudou sozinho"*.
   */
  const aplicarJson = (novo: DialogForm) => {
    const drift = reprojectionDrift(novo)
    setDraft(novo)
    setBlocks(buildBlocks(novo))
    setJsonOpen(false)
    setMsg(
      drift.length
        ? {
            kind: 'warn',
            text: t('json.drift', {
              nodes: drift.map(d => `${d.node_id} (${d.keys.join(', ')})`).join('; '),
            }),
          }
        : { kind: 'ok', text: t('json.applied') },
    )
  }

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

  // Prior question output_keys per node — the backward-only references an
  // `ask_when` guard may point at (walks the flattened block order).
  const priorKeys: Record<string, string[]> = {}
  {
    const seen: string[] = []
    for (const b of blocks) for (const nd of b.nodes) {
      priorKeys[nd.id] = seen.slice()
      if (nd.kind === 'question') seen.push(nd.output_key)
    }
  }

  const save = async (publish: boolean) => {
    if (!draft) return
    if (!draft.form_id.trim()) { setMsg({ kind: 'err', text: t('err.formIdRequired') }); return }
    const { nodes, dimensions } = flattenBlocks(blocks)
    if (nodes.length === 0) { setMsg({ kind: 'err', text: t('err.nodesRequired') }); return }
    if (dimensions.some(d => !d.dimension_id)) { setMsg({ kind: 'err', text: t('err.instrumentType') }); return }
    // ask_when forward-reference: a guard's field must be a PRIOR question's output_key.
    {
      const seen = new Set<string>()
      for (const nd of nodes) {
        if (nd.ask_when && !seen.has(nd.ask_when.field)) {
          setMsg({ kind: 'err', text: t('err.askWhenRef', { field: nd.ask_when.field }) }); return
        }
        if (nd.kind === 'question') seen.add(nd.output_key)
      }
    }
    setBusy(true); setMsg(null)
    const body = {
      form_id: draft.form_id.trim(),
      name: draft.name,
      description: draft.description,
      default_locale: draft.default_locale || 'pt-BR',
      locales: Array.from(new Set([draft.default_locale || 'pt-BR', ...(draft.locales ?? [])])),
      nodes,
      dimensions,
      composite: draft.composite,
      tags: draft.tags ?? [],
    }
    try {
      if (isNew) { await createDialogForm(TENANT, body); setIsNew(false) }
      else       { await updateDialogForm(TENANT, draft.form_id, body) }
      if (publish) await publishDialogForm(TENANT, draft.form_id)
      setMsg({ kind: 'ok', text: publish ? t('msg.published') : t('msg.saved') })
      await reload()
      const fresh = await getDialogForm(TENANT, draft.form_id)
      setDraft(fresh); setBlocks(buildBlocks(fresh)); setIdsSalvos(caminhosDeOpcao(fresh.nodes))
    } catch (e) {
      setMsg({ kind: 'err', text: String(e) })
    } finally {
      setBusy(false)
    }
  }

  // ── Arquivar / restaurar (ADR adr-dialog-form-deletion) ────────────────────
  // `ever_published` vem da LISTA e decide se o DELETE arquiva ou PURGA. Sem ele não dá
  // para avisar direito, e avisar errado num ato irreversível é pior que não oferecer o
  // botão — por isso a ação fica DESABILITADA enquanto o dado não estiver à mão, em vez de
  // supor o caso reversível (que é o palpite confortável, e o errado).
  const listEntry  = draft ? forms.find(f => f.form_id === draft.form_id) : undefined
  const isArchived = !!(draft?.deleted_at || listEntry?.deleted_at)
  const willPurge  = listEntry?.ever_published === false
  const canArchive = !!draft && !isNew && !isArchived && listEntry?.ever_published !== undefined

  const archive = async () => {
    if (!draft || !canArchive) return
    const question = willPurge
      ? t('archive.confirmPurge', { id: draft.form_id })
      : t('archive.confirm', { id: draft.form_id })
    if (!window.confirm(question)) return
    setBusy(true); setMsg(null)
    try {
      const res = await deleteDialogForm(TENANT, draft.form_id)
      setMsg({ kind: 'ok', text: res.purged ? t('msg.purged') : t('msg.archived') })
      await reload()
      if (res.purged) { setDraft(null); setBlocks([]) }
      else {
        const fresh = await getDialogForm(TENANT, draft.form_id)
        setDraft(fresh); setBlocks(buildBlocks(fresh)); setIdsSalvos(caminhosDeOpcao(fresh.nodes))
      }
    } catch (e) {
      setMsg({ kind: 'err', text: String(e) })
    } finally { setBusy(false) }
  }

  const restore = async () => {
    if (!draft) return
    setBusy(true); setMsg(null)
    try {
      await undeleteDialogForm(TENANT, draft.form_id)
      setMsg({ kind: 'ok', text: t('msg.restored') })
      await reload()
      const fresh = await getDialogForm(TENANT, draft.form_id)
      setDraft(fresh); setBlocks(buildBlocks(fresh)); setIdsSalvos(caminhosDeOpcao(fresh.nodes))
    } catch (e) {
      setMsg({ kind: 'err', text: String(e) })
    } finally { setBusy(false) }
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
        <div className="px-3 py-1.5 border-b">
          <button onClick={() => setShowArchived(v => !v)}
            className={`flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded hover:bg-surface-alt ${showArchived ? 'text-blue-700' : 'text-muted'}`}>
            <Archive size={12} /> {showArchived ? t('archive.showActive') : t('archive.showArchived')}
          </button>
        </div>
        {loading && <div className="p-3 text-xs text-gray-400">{t('loading')}</div>}
        {!loading && forms.length === 0 && <div className="p-3 text-xs text-gray-400">{t('empty')}</div>}
        <ul>
          {forms.map(f => (
            <li key={f.form_id}>
              <button onClick={() => openEdit(f.form_id)}
                className={`w-full text-left px-3 py-2 border-b hover:bg-gray-50 ${draft?.form_id === f.form_id ? 'bg-blue-50' : ''}`}>
                <div className={`text-sm truncate ${f.deleted_at ? 'text-muted-light line-through' : 'text-dark'}`}>
                  {f.name || f.form_id}
                </div>
                <div className="text-[11px] text-gray-400 flex gap-2">
                  <span>{f.form_id}</span>
                  <span className={f.status === 'published' ? 'text-green-600' : 'text-amber-600'}>
                    {f.status} v{f.version}
                  </span>
                  {f.deleted_at && <span className="text-muted">{t('archive.badge')}</span>}
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
            {isArchived && (
              <div className="flex items-start gap-2 text-xs bg-amber-50 border border-amber-200 text-amber-900 rounded px-3 py-2">
                <Archive size={14} className="mt-0.5 shrink-0" />
                {/* O texto diz o que de fato acontece. "Apagado" seria mentira: o form
                    continua sendo resolvido por quem já está vinculado a ele. */}
                <span>{t('archive.banner', { at: draft.deleted_at ?? listEntry?.deleted_at ?? '' })}</span>
              </div>
            )}
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
                <select value={draft.default_locale} onChange={e => patch({ default_locale: e.target.value })}
                  className="mt-1 w-full border rounded px-2 py-1 text-sm bg-white">
                  {localeList.map(l => <option key={l} value={l}>{l}</option>)}
                </select>
              </label>
              <label className="text-xs text-gray-600">
                {t('field.tags')}
                <input value={(draft.tags ?? []).join(', ')}
                  onChange={e => patch({ tags: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })}
                  className="mt-1 w-full border rounded px-2 py-1 text-sm" />
              </label>
              <label className="text-xs text-gray-600 col-span-2">
                {t('field.description')}
                <input value={draft.description ?? ''} onChange={e => patch({ description: e.target.value || undefined })}
                  className="mt-1 w-full border rounded px-2 py-1 text-sm" />
              </label>
            </div>

            {/* composite (health score) */}
            <div className="flex items-center gap-2 text-xs text-gray-600">
              <label className="flex items-center gap-1">
                <input type="checkbox" checked={!!draft.composite}
                  onChange={e => patch({ composite: e.target.checked ? { metric: draft.composite?.metric || 'health' } : undefined })} />
                {t('composite.label')}
              </label>
              {draft.composite && (
                <>
                  <input value={draft.composite.metric} placeholder="health"
                    onChange={e => patch({ composite: { metric: e.target.value } })}
                    className="w-32 border rounded px-2 py-0.5 bg-white font-mono" />
                  <span className="text-gray-400">{t('composite.hint')}</span>
                </>
              )}
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
                  priorKeys={priorKeys} idsSalvos={idsSalvos} compositeOn={!!draft.composite}
                  expanded={expanded} onToggleExpand={toggleExpand}
                  adjust={adjust.has(idx)} onToggleAdjust={() => toggleAdjust(idx)}
                  onChange={b => updateBlock(idx, b)} onRemove={() => removeBlock(idx)}
                  onMove={dir => moveBlock(idx, dir)} />
              ))}
            </div>

            {/* actions */}
            <div className="flex items-center gap-3 pt-2 border-t">
              {isArchived ? (
                <button disabled={busy} onClick={restore}
                  className="flex items-center gap-1 text-sm border border-border px-3 py-1.5 rounded hover:bg-surface-muted disabled:opacity-50">
                  <ArchiveRestore size={14} /> {t('archive.restore')}
                </button>
              ) : (
                <button disabled={busy || !canArchive} onClick={archive}
                  title={willPurge ? t('archive.purgeHint') : t('archive.hint')}
                  className="flex items-center gap-1 text-sm border border-border text-muted px-3 py-1.5 rounded hover:bg-surface-muted disabled:opacity-50">
                  <Archive size={14} /> {willPurge ? t('archive.purgeAction') : t('archive.action')}
                </button>
              )}
              {/* Arquivado recusa escrita no backend (409). Desabilitar aqui não é
                  duplicar a regra: é evitar que o operador receba um código HTTP cru
                  no lugar de uma explicação. O portão continua sendo o do servidor. */}
              {/* `bg-dark`, não `bg-gray-700`: o token `gray` do tailwind.config.ts é uma
                  cor CHAPADA, o que apaga a escala inteira do Tailwind — nenhuma classe
                  `*-gray-N` existe no CSS construído. Aqui isso era invisível no pior
                  sentido: fundo nenhum + `text-white` = botão branco no branco, com a
                  área de clique intacta. Ver TODO § classes gray-N inertes. */}
              {/* O painel edita o DOCUMENTO inteiro (form_id, name, locales, nodes),
                  entao ele mora na linha de acoes do documento. No cabecalho de
                  `Blocks` ele afirmava, pela posicao, um escopo menor do que tem. */}
              <button onClick={() => setJsonOpen(true)}
                className="flex items-center gap-1 text-sm border border-border px-3 py-1.5 rounded hover:bg-surface-muted">
                <Braces size={14} /> {t('json.open')}
              </button>
              <button disabled={busy || isArchived} onClick={() => save(false)}
                title={isArchived ? t('archive.readOnly') : undefined}
                className="text-sm bg-dark text-white px-3 py-1.5 rounded hover:opacity-90 disabled:opacity-50">
                {t('action.saveDraft')}
              </button>
              <button disabled={busy || isArchived} onClick={() => save(true)}
                title={isArchived ? t('archive.readOnly') : undefined}
                className="text-sm bg-blue-700 text-white px-3 py-1.5 rounded hover:bg-blue-800 disabled:opacity-50">
                {t('action.publish')}
              </button>
              {msg && (
                <span className={`flex items-center gap-1 text-xs ${
                  msg.kind === 'ok' ? 'text-green-600' : msg.kind === 'warn' ? 'text-amber-700' : 'text-red-600'
                }`}>
                  {msg.kind === 'ok' ? <Check size={14} /> : <AlertTriangle size={14} />}{msg.text}
                </span>
              )}
            </div>
          </div>
        )}
      </main>

      {jsonOpen && draft && (
        <DialogJsonPanel
          doc={docAtual()}
          locale={editLocale}
          tenantId={TENANT}
          {...(isNew ? {} : { lockedFormId: draft.form_id })}
          readOnly={isArchived}
          onApply={aplicarJson}
          onClose={() => setJsonOpen(false)}
        />
      )}
    </div>
  )
}

// ── ask_when helpers (block-level guard = the guard all nodes share) ────────────

function guardsEqual(a?: AskWhen, b?: AskWhen): boolean {
  if (!a || !b) return a === b
  return a.field === b.field && a.op === b.op && JSON.stringify(a.value) === JSON.stringify(b.value)
}
/** The guard shared by ALL nodes of a block (the block-level guard), or undefined. */
function commonGuard(nodes: DialogNode[]): AskWhen | undefined {
  const first = nodes[0]?.ask_when
  if (!first) return undefined
  return nodes.every(n => guardsEqual(n.ask_when, first)) ? first : undefined
}

// ── Block card ──────────────────────────────────────────────────────────────────

interface BlockCardProps {
  block: Block
  idx: number
  total: number
  instruments: Instrument[]
  locale: string
  defaultLocale: string
  priorKeys: Record<string, string[]>
  idsSalvos: Set<string>
  compositeOn: boolean
  expanded: Set<string>
  onToggleExpand: (id: string) => void
  adjust: boolean
  onToggleAdjust: () => void
  onChange: (b: Block) => void
  onRemove: () => void
  onMove: (dir: -1 | 1) => void
}

const BlockCard: React.FC<BlockCardProps> = ({
  block, idx, total, instruments, locale, defaultLocale, priorKeys, idsSalvos, compositeOn, expanded, onToggleExpand,
  adjust, onToggleAdjust, onChange, onRemove, onMove,
}) => {
  const { t } = useTranslation('dialogForms')
  const isInstrument = block.kind === 'instrument'
  const dim = block.kind === 'instrument' ? block.dim : null
  const isCustom = !!dim && dim.dimension_id !== '' && !instruments.some(i => i.id === dim.dimension_id)

  // Node ops within this block
  const setNodes = (nodes: DialogNode[]) =>
    onChange(
      block.kind === 'instrument' ? { kind: 'instrument', dim: block.dim, nodes }
      : block.kind === 'form'     ? { kind: 'form', nodes }
      : { kind: 'dialog', nodes },
    )
  const addNode = (n: DialogNode) => setNodes([...block.nodes, n])
  const updateNode = (i: number, n: DialogNode) => setNodes(block.nodes.map((x, j) => (j === i ? n : x)))
  const removeNode = (i: number) => setNodes(block.nodes.filter((_, j) => j !== i))
  const moveNode = (i: number, dir: -1 | 1) => {
    const j = i + dir
    if (j < 0 || j >= block.nodes.length) return
    const next = block.nodes.slice();[next[i], next[j]] = [next[j]!, next[i]!]; setNodes(next)
  }

  const changeType = (val: string) => {
    if (val === DIALOG_TYPE) {
      // Sair de `form` devolve a pergunta ao mundo escalar — e os `fields[]` TEM
      // de sair junto. Guarda-los "por seguranca" criaria o pior estado possivel:
      // `buildRender` testa `node.fields?.length` ANTES da interacao, entao uma
      // pergunta `text` com campos residuais continuaria renderizando como
      // formulario (e o WhatsApp dispararia coleta sequencial por
      // `len(fields) > 0`) sem que a tela mostrasse campo nenhum.
      //
      // Como e perda de trabalho do autor, ela e CONSENTIDA, nunca silenciosa.
      if (block.kind === 'form') {
        const q = block.nodes.find((n): n is QuestionNode => n.kind === 'question')
        const n = q?.fields?.length ?? 0
        if (n > 0 && !window.confirm(t('form.confirmToDialog', { n }))) return
      }
      const nodes = block.kind === 'form'
        ? block.nodes.map(x => {
            if (x.kind !== 'question' || x.interaction !== 'form') return x
            const q2 = { ...x, interaction: 'text' as DialogInteraction }
            delete (q2 as { fields?: unknown }).fields
            return q2
          })
        : block.nodes
      onChange({ kind: 'dialog', nodes }); return
    }
    if (val === FORM_TYPE) {
      // Um bloco form E uma pergunta `form`. Se ja ha pergunta, ela vira o
      // formulario (preservando prompt/output_key/campos); se nao ha, cria-se.
      const jaTem = block.nodes.some(n => n.kind === 'question')
      const nodes = jaTem
        ? block.nodes.map((n, i) => (n.kind === 'question' && block.nodes.findIndex(x => x.kind === 'question') === i
            ? { ...n, interaction: 'form' as DialogInteraction } : n))
        : [...block.nodes, { ...newQuestion(), interaction: 'form' as DialogInteraction, output_key: 'dados' }]
      onChange({ kind: 'form', nodes }); return
    }
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
  const [showAnchors, setShowAnchors] = useState(false)
  const scalePoints = dim
    ? Array.from({ length: Math.max(0, dim.scale.max - (dim.scale.min ?? 0) + 1) }, (_, k) => (dim.scale.min ?? 0) + k)
    : []
  const setAnchor = (i: number, val: string) => {
    if (!dim) return
    const arr: LocalizedText[] = scalePoints.map((_, k) => dim.anchors?.[k] ?? '')
    arr[i] = setLt(dim.anchors?.[i], locale, val, defaultLocale)
    updateDim({ anchors: arr })
  }

  // Weight %, computed within the block
  const qs = block.nodes.filter((n): n is QuestionNode => n.kind === 'question')
  const wsum = qs.reduce((a, q) => a + (q.capture?.weight ?? 1), 0)
  const pctOf = (q: QuestionNode) => (wsum > 0 ? Math.round(((q.capture?.weight ?? 1) / wsum) * 100) : 0)

  const isForm = block.kind === 'form'
  const formQ  = block.kind === 'form' ? formQuestionOf(block) : undefined
  const typeValue = isInstrument ? (isCustom ? CUSTOM_TYPE : dim!.dimension_id)
                  : isForm       ? FORM_TYPE
                  : DIALOG_TYPE
  const accent = isInstrument ? 'border-l-blue-400' : isForm ? 'border-l-emerald-400' : 'border-l-gray-300'

  /** Patch na pergunta que DA identidade ao bloco form. */
  const updateFormQ = (patch: Partial<QuestionNode>) => {
    if (!formQ) return
    setNodes(block.nodes.map(n => (n.id === formQ.id ? { ...(n as QuestionNode), ...patch } : n)))
  }

  // Block-level ask_when guard = the guard all nodes share (fan-out on set). Its
  // field references questions BEFORE the block (the first node's prior keys).
  const blockPriorKeys = block.nodes[0] ? (priorKeys[block.nodes[0].id] ?? []) : []
  const blockGuard = commonGuard(block.nodes)
  const setBlockGuard = (g: AskWhen | undefined) => setNodes(block.nodes.map(n => {
    if (g) return { ...n, ask_when: g }
    const copy = { ...n }; delete (copy as { ask_when?: unknown }).ask_when; return copy
  }))

  return (
    <div className={`border rounded-lg bg-white border-l-4 ${accent} p-3 space-y-2`}>
      {/* header */}
      <div className="flex items-center gap-2 flex-wrap">
        <select value={typeValue} onChange={e => changeType(e.target.value)}
          className="border rounded px-2 py-1 text-sm bg-white font-medium">
          <option value={DIALOG_TYPE}>{t('block.dialog')}</option>
          <option value={FORM_TYPE}>{t('block.form')}</option>
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
            <span className="text-xs text-gray-500">{t('field.interaction')}</span>
            <select value={dim!.interaction ?? 'button'}
              onChange={e => updateDim({ interaction: e.target.value as DialogInteraction })}
              className="border rounded px-1 py-0.5 text-xs bg-white">
              {interactionOptions(dim!.interaction).map(i => <option key={i} value={i}>{i}</option>)}
            </select>
            {compositeOn && (
              <span className="flex items-center gap-1 text-xs text-gray-500">
                {t('composite.weight')}
                <input type="number" min={0} value={dim!.weight ?? 1}
                  onChange={e => updateDim({ weight: e.target.value === '' ? undefined : Math.max(0, Number(e.target.value)) })}
                  className="w-12 border rounded px-1 py-0.5 text-center bg-white" />
              </span>
            )}
          </>
        ) : (
          <span className="flex-1 text-xs text-gray-400">{t('block.dialogHint')}</span>
        )}
        <button onClick={() => onMove(-1)} disabled={idx === 0} className="text-gray-400 hover:text-gray-700 disabled:opacity-30"><ArrowUp size={15} /></button>
        <button onClick={() => onMove(1)} disabled={idx === total - 1} className="text-gray-400 hover:text-gray-700 disabled:opacity-30"><ArrowDown size={15} /></button>
        <button onClick={onRemove} className="text-red-400 hover:text-red-600"><Trash2 size={15} /></button>
      </div>

      {/* anchors (scale-point labels) — instrument with option-based render */}
      {isInstrument && dim!.interaction && HAS_OPTIONS(dim!.interaction) && (
        <div className="pl-2 text-xs text-gray-600">
          <button onClick={() => setShowAnchors(s => !s)} className="text-gray-500 hover:text-gray-800">
            {showAnchors ? '−' : '+'} {t('block.anchors')}
          </button>
          {showAnchors && (
            <div className="flex flex-wrap gap-2 mt-1">
              {scalePoints.map((v, i) => (
                <div key={v} className="flex items-center gap-1">
                  <span className="text-gray-400 w-4 text-right">{v}</span>
                  <input value={ltToStr(dim!.anchors?.[i], locale, defaultLocale)} placeholder={String(v)}
                    onChange={e => setAnchor(i, e.target.value)}
                    className="w-24 border rounded px-1 py-0.5 bg-white" />
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* corpo do bloco FORM: a pergunta que o identifica vira o cabecalho */}
      {isForm && formQ && (
        <FormBlockEditor node={formQ} locale={locale} defaultLocale={defaultLocale}
          onChange={updateFormQ}
          visibilityRow={<VisibilityRow value={formQ.visibility}
            onChange={v => updateFormQ({ visibility: v })} />} />
      )}

      {/* nodes — no bloco form a pergunta e o proprio bloco, entao nao vira linha */}
      <div className="space-y-1.5 pl-2 border-l-2 border-gray-100">
        {block.nodes.map((node, i) => (
          isForm && node.id === formQ?.id ? null :
          <NodeRow key={node.id} node={node} first={i === 0} last={i === block.nodes.length - 1}
            locale={locale} defaultLocale={defaultLocale} scored={isInstrument}
            priorKeys={priorKeys[node.id] ?? []}
            idsSalvos={idsSalvos}
            expanded={expanded.has(node.id)} onToggle={() => onToggleExpand(node.id)}
            showWeight={isInstrument && adjust} pct={node.kind === 'question' ? pctOf(node) : 0}
            onChange={n => updateNode(i, n)} onRemove={() => removeNode(i)} onMove={dir => moveNode(i, dir)} />
        ))}
      </div>

      {/* block-level guard (fans out to all nodes) */}
      {block.nodes.length > 0 && blockPriorKeys.length > 0 && (
        <AskWhenRow guard={blockGuard} priorKeys={blockPriorKeys} onSet={setBlockGuard} labelKey="askWhen.blockLabel" />
      )}

      {/* footer */}
      <div className="flex items-center gap-2 text-xs text-gray-500 pt-1">
        {/* Bloco form ja TEM a sua pergunta — o bloco e o turno, e uma segunda
            pergunta faria a tela prometer dois turnos onde o runner faz um. */}
        {!isForm && (
          <button onClick={() => addNode(newQuestion())} className="hover:text-gray-800"><Plus size={12} className="inline" /> {t('node.question')}</button>
        )}
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
  node: DialogNode; first: boolean; last: boolean; locale: string; defaultLocale: string; scored: boolean
  priorKeys: string[]
  expanded: boolean; onToggle: () => void; showWeight: boolean; pct: number
  idsSalvos: Set<string>
  onChange: (n: DialogNode) => void; onRemove: () => void; onMove: (dir: -1 | 1) => void
}> = ({ node, first, last, locale, defaultLocale, scored, priorKeys, idsSalvos, expanded, onToggle, showWeight, pct, onChange, onRemove, onMove }) => {
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
            ? <StatementEditor node={node} locale={locale} defaultLocale={defaultLocale} priorKeys={priorKeys} onChange={onChange} />
            : <QuestionEditor node={node} locale={locale} defaultLocale={defaultLocale} scored={scored} priorKeys={priorKeys} idsSalvos={idsSalvos} onChange={onChange} />}
        </div>
      )}
    </div>
  )
}

const StatementEditor: React.FC<{ node: StatementNode; locale: string; defaultLocale: string; priorKeys: string[]; onChange: (n: DialogNode) => void }> =
({ node, locale, defaultLocale, priorKeys, onChange }) => {
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
      <AskWhenRow guard={node.ask_when} priorKeys={priorKeys} onSet={g => onChange({ ...node, ask_when: g })} />
    </div>
  )
}

/**
 * Todos os caminhos de `id` de opção do documento como ele foi CARREGADO.
 * É este conjunto que trava a edição do `id` (D6): o que veio do store tem
 * série histórica atrás, e trocar o `id` funde duas coisas na agregação por
 * prefixo, em silêncio. O que é novo ainda não tem histórico — pode ser
 * corrigido à vontade até salvar.
 */
function caminhosDeOpcao(nodes: DialogNode[] | undefined): Set<string> {
  const out = new Set<string>()
  const anda = (opts: DialogOption[] | undefined, prefixo: string): void => {
    for (const o of opts ?? []) {
      const id = o.value ?? o.id
      if (!id) continue
      const cam = prefixo ? `${prefixo}.${id}` : id
      out.add(cam)
      anda(o.options, cam)
    }
  }
  for (const n of nodes ?? []) {
    if (n.kind !== "question") continue
    anda(n.options, "")
    for (const f of n.fields ?? []) anda(f.options, "")
  }
  return out
}

const QuestionEditor: React.FC<{ node: QuestionNode; locale: string; defaultLocale: string; scored?: boolean; priorKeys: string[]; idsSalvos: Set<string>; onChange: (n: DialogNode) => void }> =
({ node, locale, defaultLocale, scored, priorKeys, idsSalvos, onChange }) => {
  const { t } = useTranslation('dialogForms')
  const { tenantId } = useAuth()
  // Os dois catálogos vêm do STORE (config-api), nunca do default embutido: é
  // ele a fonte de verdade em runtime, e ler o embutido faria a tela mostrar um
  // catálogo enquanto o tenant tem outro. O cache do módulo faz as N perguntas
  // de um formulário compartilharem UMA requisição.
  const { formats, erro: errFmt }  = useFormatCatalog(tenantId)
  const { types: maskedTypes, erro: errMsk } = useMaskedTypes(tenantId)
  const catalogoErro = errFmt ?? errMsk

  // D8 — o campo nomeia o tipo UMA vez: `masked` que tenha contraparte de formato
  // DERIVA o formato e trava o seletor; declarar os dois com valores diferentes é
  // conflito, que a tela MOSTRA em vez de corrigir sozinha (o publish recusa 422).
  //
  // A regra mora em `d8Verdict` desde 2026-09-05, porque o editor de CAMPO passou
  // a fazer a mesma pergunta — duas implementações divergiriam, e a divergência
  // aqui seria "o campo aceita o que a pergunta recusa".
  const { derivado, conflito } = d8Verdict(node.masked, node.validation?.format, formats)

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
        {scored ? (
          <div className="text-xs text-gray-400 flex items-end pb-1">{t('scoring.renderInherited')}</div>
        ) : (
          <label className="text-xs text-gray-600">
            {t('field.interaction')}
            <select value={node.interaction}
              onChange={e => onChange({ ...node, interaction: e.target.value as DialogInteraction })}
              className="mt-1 w-full border rounded px-2 py-1 text-sm bg-white">
              {interactionOptions(node.interaction).map(i => <option key={i} value={i}>{i}</option>)}
            </select>
          </label>
        )}
        <label className="text-xs text-gray-600">
          {t('field.outputKey')}
          <input value={node.output_key} onChange={e => onChange({ ...node, output_key: e.target.value })}
            className="mt-1 w-full border rounded px-2 py-1 text-sm bg-white" />
        </label>
      </div>

      <VisibilityRow value={node.visibility} onChange={v => onChange({ ...node, visibility: v })} />

      {!scored && (
        <div className="flex items-center gap-3 text-xs text-gray-600 flex-wrap">
          {/*
            FMT-08 — o checkbox virou SELETOR DE TIPO.
            `MaskedDeclarationSchema` é `false | string` desde a T1, e a
            referência tipada já era usada (`card_cvv` em
            `dialog_limite_solicitacao`) — mas vinha de JSON semeado à mão,
            porque o checkbox só conseguia emitir `true`, que significa
            `opaque` ("mascarado SEM tipo"). O ADR que aboliu a declaração
            anônima deixava a única superfície de autoria emitindo justamente a
            anônima. Afordância ausente não é neutra: ela escolhe o default.
            `opaque` continua disponível, agora como opção NOMEADA.
          */}
          {/* O `masked` do NÓ nao alcanca `form`: a mascara e por CAMPO. O
              timeout, sim — e do turno. */}
          {node.interaction !== 'form' && (
            <label className="flex items-center gap-1">{t('field.maskedType')}
              <select
                value={node.masked === true ? 'opaque' : (node.masked || '')}
                onChange={e => onChange({ ...node, masked: e.target.value || undefined })}
                className="border rounded px-1 py-0.5 bg-white">
                <option value="">{t('field.maskedNone')}</option>
                {maskedTypes.map(mt => (
                  <option key={mt.id} value={mt.id}>
                    {(mt.icon ? mt.icon + ' ' : '') + (mt.label ?? mt.id)}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="flex items-center gap-1">{t('field.timeout')}
            <input type="number" value={node.timeout_s ?? ''} className="w-16 border rounded px-1 py-0.5 bg-white"
              onChange={e => onChange({ ...node, timeout_s: e.target.value === '' ? undefined : Number(e.target.value) })} />
          </label>
        </div>
      )}

      {!scored && node.interaction === 'text' && (
        <div className="space-y-1.5 border-t pt-2">
          <div className="flex items-center gap-3 text-xs text-gray-600 flex-wrap">
            <label className="flex items-center gap-1">
              <input type="checkbox" checked={!!node.validation?.numeric}
                onChange={e => onChange({ ...node, validation: { ...node.validation, numeric: e.target.checked || undefined } })} />
              {t('field.numeric')}
            </label>
            <label className="flex items-center gap-1">{t('field.min')}
              <input type="number" value={node.validation?.min ?? ''} className="w-14 border rounded px-1 py-0.5 bg-white"
                onChange={e => onChange({ ...node, validation: { ...node.validation, min: e.target.value === '' ? undefined : Number(e.target.value) } })} />
            </label>
            <label className="flex items-center gap-1">{t('field.max')}
              <input type="number" value={node.validation?.max ?? ''} className="w-14 border rounded px-1 py-0.5 bg-white"
                onChange={e => onChange({ ...node, validation: { ...node.validation, max: e.target.value === '' ? undefined : Number(e.target.value) } })} />
            </label>
            <label className="flex items-center gap-1">{t('field.minLength')}
              <input type="number" value={node.validation?.min_length ?? ''} className="w-14 border rounded px-1 py-0.5 bg-white"
                onChange={e => onChange({ ...node, validation: { ...node.validation, min_length: e.target.value === '' ? undefined : Number(e.target.value) } })} />
            </label>
            <label className="flex items-center gap-1">{t('field.maxLength')}
              <input type="number" value={node.validation?.max_length ?? ''} className="w-14 border rounded px-1 py-0.5 bg-white"
                onChange={e => onChange({ ...node, validation: { ...node.validation, max_length: e.target.value === '' ? undefined : Number(e.target.value) } })} />
            </label>
          </div>
          {/*
            F3 — o campo LIVRE de regex virou seletor de catálogo.
            Regex crua não fala com o teclado, não tem rótulo de erro e não
            alcança validade semântica (`31/02/2026` casa qualquer regex de
            data). A regex não saiu do sistema: mudou de AUTOR, e hoje é a
            implementação de uma entrada revisada uma vez.
          */}
          <label className="flex items-center gap-1 text-xs text-gray-600">{t('field.format')}
            <select
              value={node.validation?.format ?? ''}
              disabled={!!derivado}
              onChange={e => onChange({ ...node, validation: { ...node.validation, format: e.target.value || undefined } })}
              className="flex-1 border rounded px-2 py-0.5 bg-white disabled:bg-gray-100">
              <option value="">{t('field.formatNone')}</option>
              {formats.map(f => (
                <option key={f.id} value={f.id}>{formatLabel(f, locale)}</option>
              ))}
            </select>
          </label>
          {/* D8 — o campo nomeia o tipo UMA vez. */}
          {derivado && (
            <p className="text-[11px] text-blue-700">
              {t('field.formatDerived', { format: formatLabel(derivado, locale), tipo: String(node.masked) })}
            </p>
          )}
          {conflito && (
            <p className="text-[11px] text-red-700 flex items-center gap-1">
              <AlertTriangle className="w-3 h-3" />
              {t('field.formatConflict', { declarado: node.validation?.format ?? '', derivado: conflito })}
            </p>
          )}
          {catalogoErro && (
            <p className="text-[11px] text-amber-700">{t('field.catalogUnavailable')}</p>
          )}
          <div className="flex items-center gap-2 text-xs text-gray-600">
            <span>{t('field.retry')}</span>
            <input value={ltToStr(node.retry?.reprompt, locale, defaultLocale)} placeholder={t('field.repromptPlaceholder')}
              onChange={e => onChange({ ...node, retry: e.target.value
                ? { reprompt: setLt(node.retry?.reprompt, locale, e.target.value, defaultLocale), max_attempts: node.retry?.max_attempts }
                : undefined })}
              className="flex-1 border rounded px-2 py-0.5 bg-white" />
            <span>{t('field.maxAttempts')}</span>
            <input type="number" min={1} value={node.retry?.max_attempts ?? ''} className="w-14 border rounded px-1 py-0.5 bg-white"
              onChange={e => onChange({ ...node, retry: { reprompt: node.retry?.reprompt ?? '', max_attempts: e.target.value === '' ? undefined : Number(e.target.value) } })} />
          </div>
        </div>
      )}

      {!scored && HAS_OPTIONS(node.interaction) && (
        <OptionTreeEditor
          options={node.options ?? []}
          onChange={setOptions}
          locale={locale}
          defaultLocale={defaultLocale}
          idsSalvos={idsSalvos}
        />
      )}

      <AskWhenRow guard={node.ask_when} priorKeys={priorKeys} onSet={g => onChange({ ...node, ask_when: g })} />
    </div>
  )
}

// ── ask_when guard builder (conditional skip-logic) ─────────────────────────────

// `prefix` (D12) entrou no schema com a F1 e faltava AQUI: o avaliador conhecia
// o op e o autor não tinha como escolhê-lo. Lista de op é mais uma casa que
// afirma o mesmo conjunto — quando divergem, o editor é quem fica para trás,
// e a falta aparece como "o schema aceita mas a tela não oferece".
const AW_OPS: AskWhenOp[] = ['lt', 'lte', 'gt', 'gte', 'eq', 'ne', 'in', 'prefix']
const isNum = (s: string) => /^-?\d+(\.\d+)?$/.test(s.trim())
function parseAwValue(op: AskWhenOp, raw: string): AskWhen['value'] {
  if (op === 'in') return raw.split(',').map(s => s.trim()).filter(Boolean).map(s => (isNum(s) ? Number(s) : s))
  return isNum(raw) ? Number(raw) : raw
}
function awValueToStr(v: AskWhen['value'] | undefined): string {
  if (Array.isArray(v)) return v.join(', ')
  return v === undefined ? '' : String(v)
}

const AskWhenRow: React.FC<{ guard?: AskWhen; priorKeys: string[]; onSet: (g: AskWhen | undefined) => void; labelKey?: string }> =
({ guard: g, priorKeys, onSet, labelKey }) => {
  const { t } = useTranslation('dialogForms')
  if (!g && priorKeys.length === 0) return null   // nothing to reference yet
  const set = (patch: Partial<AskWhen>) => {
    const base: AskWhen = g ?? { field: priorKeys[0] ?? '', op: 'lt', value: 0 }
    onSet({ ...base, ...patch })
  }
  return (
    <div className="flex items-center gap-2 flex-wrap text-xs text-gray-600 border-t pt-2">
      <label className="flex items-center gap-1">
        <input type="checkbox" checked={!!g}
          onChange={e => onSet(e.target.checked ? { field: priorKeys[0] ?? '', op: 'lt', value: 0 } : undefined)} />
        {t(labelKey ?? 'askWhen.label')}
      </label>
      {g && (
        <>
          <select value={g.field} onChange={e => set({ field: e.target.value })}
            className="border rounded px-1 py-0.5 bg-white">
            {priorKeys.map(k => <option key={k} value={k}>{k}</option>)}
            {!priorKeys.includes(g.field) && <option value={g.field}>{g.field || '—'}</option>}
          </select>
          <select value={g.op} onChange={e => set({ op: e.target.value as AskWhenOp })}
            className="border rounded px-1 py-0.5 bg-white">
            {AW_OPS.map(o => <option key={o} value={o}>{o}</option>)}
          </select>
          <input value={awValueToStr(g.value)} placeholder={g.op === 'in' ? t('askWhen.valueList') : t('askWhen.value')}
            onChange={e => set({ value: parseAwValue(g.op, e.target.value) })}
            className="w-28 border rounded px-2 py-0.5 bg-white" />
        </>
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
