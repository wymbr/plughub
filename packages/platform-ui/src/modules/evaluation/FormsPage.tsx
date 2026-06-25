/**
 * FormsPage.tsx
 * /evaluation/forms — EvaluationForm editor
 *
 * Hierarchical weight UX (Task #54):
 *   Form → 100 pts total (fixed reference)
 *   Dimension → absolute pts (sum must = 100); auto-equalised on add
 *   Criterion  → absolute pts within parent dimension; auto-equalised on add
 *
 * Stored as fractions (0-1) in the API. Conversion at the boundary:
 *   dim.weight  = dim.pts / TOTAL_PTS
 *   crit.weight = crit.pts / dim.pts   (fraction of parent dimension)
 */

import { Check, AlertTriangle, X } from 'lucide-react'
import React, { useState, useRef, useEffect } from 'react'
import { useBlocker } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useForms, createForm, updateForm, deleteForm } from '@/api/evaluation-hooks'
import type { EvaluationForm, EvaluationDimension, EvaluationCriterion } from '@/types'
import { useAuth } from '@/auth/useAuth'

// ── Constants ─────────────────────────────────────────────────────────────────

const TOTAL_PTS = 100   // form-level reference; not stored — weights are fractions

// ── Working types (pts replaces weight in the editing layer) ──────────────────

type WCriterion = Omit<EvaluationCriterion, 'weight'> & { pts: number }
type WDimension = Omit<EvaluationDimension, 'weight' | 'criteria'> & {
  pts:      number
  criteria: WCriterion[]
}
type WForm = Omit<EvaluationForm, 'dimensions'> & { dimensions: WDimension[] }

// ── Conversions ───────────────────────────────────────────────────────────────

function apiToWorking(form: EvaluationForm): WForm {
  const dims: WDimension[] = (form.dimensions ?? []).map(dim => {
    const dim_pts = Math.round(dim.weight * TOTAL_PTS)
    return {
      ...dim,
      pts: dim_pts,
      criteria: (dim.criteria ?? []).map(c => ({
        ...c,
        pts: Math.round(c.weight * dim_pts),
      })),
    }
  })
  return { ...form, dimensions: dims }
}

function workingToApi(wf: WForm): EvaluationForm {
  const dims: EvaluationDimension[] = wf.dimensions.map(dim => ({
    ...dim,
    weight: dim.pts / TOTAL_PTS,
    criteria: dim.criteria.map(c => ({
      ...c,
      weight: dim.pts > 0 ? c.pts / dim.pts : 0,
    })),
  }))
  return { ...wf, dimensions: dims }
}

// ── Equalisers (redistribute pts evenly when adding / removing) ───────────────

function equaliseDims(dims: WDimension[]): WDimension[] {
  if (dims.length === 0) return []
  const each = Math.floor(TOTAL_PTS / dims.length)
  const remainder = TOTAL_PTS - each * (dims.length - 1)
  return dims.map((d, i) => ({ ...d, pts: i === dims.length - 1 ? remainder : each }))
}

function equaliseCriteria(criteria: WCriterion[], dimPts: number): WCriterion[] {
  if (criteria.length === 0) return []
  const each = Math.floor(dimPts / criteria.length)
  const remainder = dimPts - each * (criteria.length - 1)
  return criteria.map((c, i) => ({ ...c, pts: i === criteria.length - 1 ? remainder : each }))
}

// ── Small helpers ─────────────────────────────────────────────────────────────

function sum(ns: number[]) { return ns.reduce((a, b) => a + b, 0) }

function SumBadge({ current, total }: { current: number; total: number }) {
  const ok = current === total
  return (
    <span className={`text-xs font-mono px-2 py-0.5 rounded ${ok ? 'bg-green-light text-green-text' : 'bg-red-light text-red-text'}`}>
      {current} / {total} pts {ok ? <Check className="w-3 h-3 inline" aria-hidden="true" /> : <AlertTriangle className="w-3 h-3 inline" aria-hidden="true" />}
    </span>
  )
}

function StatusBadge({ status }: { status: string }) {
  const { t } = useTranslation('evaluation')
  const styles: Record<string, string> = {
    active:   'bg-green-light text-green-text',
    archived: 'bg-surface-alt text-muted',
  }
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${styles[status] ?? 'bg-surface-alt text-muted'}`}>
      {t(`forms.${status}`, status)}
    </span>
  )
}

// ── CriterionEditor ───────────────────────────────────────────────────────────

interface CriterionEditorProps {
  criterion:    WCriterion
  dimPts:       number
  remainingPts: number   // dim_pts − sum(all other criteria) — for smart-fill on focus
  onChange:     (c: WCriterion) => void
  onDelete:     () => void
}

function CriterionEditor({ criterion, remainingPts, onChange, onDelete }: CriterionEditorProps) {
  const { t } = useTranslation('evaluation')
  return (
    <div className="border border-border rounded p-3 space-y-2 bg-surface-muted">
      <div className="flex gap-2 items-center">
        <input
          className="flex-1 border border-border-strong rounded px-2 py-1 text-sm"
          placeholder={t('forms.criterion.labelPlaceholder')}
          value={criterion.label}
          onChange={e => onChange({ ...criterion, label: e.target.value })}
        />
        {/* Pts input — shows empty when 0; smart-fills remaining pts on focus */}
        <div className="flex items-center gap-1 shrink-0">
          <input
            className="w-16 border border-border-strong rounded px-2 py-1 text-sm text-center"
            type="number"
            min={0}
            value={criterion.pts === 0 ? '' : criterion.pts}
            placeholder="0"
            onFocus={e => {
              if (criterion.pts === 0 && remainingPts > 0) {
                onChange({ ...criterion, pts: remainingPts })
              }
              e.target.select()
            }}
            onChange={e => onChange({ ...criterion, pts: Math.max(0, parseInt(e.target.value || '0', 10) || 0) })}
          />
          <span className="text-xs text-muted">{t('forms.pts')}</span>
        </div>
        <button
          type="button"
          onClick={onDelete}
          className="text-red hover:text-red-text px-1"
          aria-label={t('forms.criterion.deleteAriaLabel')}
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>

      <textarea
        className="w-full border border-border-strong rounded px-2 py-1 text-sm resize-none"
        rows={2}
        placeholder={t('forms.criterion.descriptionPlaceholder')}
        value={criterion.description}
        onChange={e => onChange({ ...criterion, description: e.target.value })}
      />

      <div className="flex gap-4 items-center text-xs text-muted">
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            checked={criterion.allows_na}
            onChange={e => onChange({ ...criterion, allows_na: e.target.checked })}
          />
          {t('forms.criterion.allowsNa')}
        </label>
        <label className="flex items-center gap-2">
          {t('forms.criterion.maxScore')}
          <input
            type="number"
            min={1}
            max={10}
            className="w-12 border border-border-strong rounded px-1 py-0.5 text-center"
            value={criterion.max_score}
            onChange={e => onChange({ ...criterion, max_score: Number(e.target.value) })}
          />
        </label>
        <input
          className="flex-1 border border-border-strong rounded px-2 py-0.5"
          placeholder={t('forms.criterion.appliesWhenPlaceholder')}
          value={criterion.applies_when ?? ''}
          onChange={e => onChange({ ...criterion, applies_when: e.target.value || null })}
        />
      </div>
    </div>
  )
}

// ── DimensionEditor ───────────────────────────────────────────────────────────

interface DimensionEditorProps {
  dim:      WDimension
  onChange: (d: WDimension) => void
  onDelete: () => void
}

function DimensionEditor({ dim, onChange, onDelete }: DimensionEditorProps) {
  const { t } = useTranslation('evaluation')
  const critSum = sum(dim.criteria.map(c => c.pts))

  const addCriterion = () => {
    const newCrit: WCriterion = {
      criterion_id: `crit_${Date.now()}`,
      label:        '',
      description:  '',
      pts:          0,
      allows_na:    false,
      max_score:    10,
      applies_when: null,
    }
    const newCriteria = equaliseCriteria([...dim.criteria, newCrit], dim.pts)
    onChange({ ...dim, criteria: newCriteria })
  }

  const removeCriterion = (idx: number) => {
    const remaining = dim.criteria.filter((_, j) => j !== idx)
    onChange({ ...dim, criteria: equaliseCriteria(remaining, dim.pts) })
  }

  const updateCriterion = (idx: number, updated: WCriterion) => {
    const criteria = [...dim.criteria]
    criteria[idx] = updated
    onChange({ ...dim, criteria })
  }

  // Criteria are out of balance when they exist but don't sum to dim.pts
  const critError = dim.criteria.length > 0 && critSum !== dim.pts

  return (
    <div
      className="rounded p-3 space-y-2 transition-colors"
      style={critError
        ? { backgroundColor: '#fecaca', borderLeft: '4px solid #ef4444' }
        : { border: '1px solid #bfdbfe', backgroundColor: 'rgba(239,246,255,0.3)' }
      }
    >
      {/* Dimension header */}
      <div className="flex gap-2 items-center">
        <input
          className="flex-1 border border-border-strong rounded px-2 py-1 text-sm font-medium"
          placeholder={t('forms.dimension.namePlaceholder')}
          value={dim.label}
          onChange={e => onChange({ ...dim, label: e.target.value })}
        />
        {/* Pts input — re-equalises criteria whenever total changes */}
        <div className="flex items-center gap-1 shrink-0">
          <input
            className="w-16 border border-border-strong rounded px-2 py-1 text-sm text-center font-medium"
            type="number"
            min={0}
            max={TOTAL_PTS}
            value={dim.pts === 0 ? '' : dim.pts}
            placeholder="0"
            onFocus={e => e.target.select()}
            onChange={e => {
              const newPts = Math.min(TOTAL_PTS, Math.max(0, parseInt(e.target.value || '0', 10) || 0))
              // Re-equalize all criteria to fit within the new dimension total
              const newCriteria = dim.criteria.length > 0 ? equaliseCriteria(dim.criteria, newPts) : []
              onChange({ ...dim, pts: newPts, criteria: newCriteria })
            }}
          />
          <span className="text-xs text-muted">{t('forms.pts')}</span>
        </div>
        <button
          type="button"
          onClick={() => {
            const label = dim.label || t('forms.dimension.unnamed')
            if (!confirm(t('forms.dimension.confirmDelete', { label }))) return
            onDelete()
          }}
          className="text-red hover:text-red-text text-xs px-2"
        >
          <X className="w-3.5 h-3.5 inline mr-1" aria-hidden="true" />
          {t('forms.dimension.deleteLabel')}
        </button>
      </div>

      {/* Criteria */}
      <div className="space-y-2 pl-2">
        {dim.criteria.map((c, i) => {
          const otherSum = sum(dim.criteria.filter((_, j) => j !== i).map(cc => cc.pts))
          const remainingPts = Math.max(0, dim.pts - otherSum)
          return (
            <CriterionEditor
              key={c.criterion_id}
              criterion={c}
              dimPts={dim.pts}
              remainingPts={remainingPts}
              onChange={updated => updateCriterion(i, updated)}
              onDelete={() => removeCriterion(i)}
            />
          )
        })}
      </div>

      {/* Criteria footer: add + sum indicator */}
      <div className="flex items-center justify-between pt-1">
        <button
          type="button"
          onClick={addCriterion}
          className={`text-xs border rounded px-2 py-1 ${critError ? 'text-red-text hover:text-red-text/80 border-red/30' : 'text-primary hover:text-primary-dark border-primary/30'}`}
        >
          {t('forms.dimension.addCriterion')}
        </button>
        {dim.criteria.length > 0 && <SumBadge current={critSum} total={dim.pts} />}
      </div>
    </div>
  )
}

// ── FormDetail ─────────────────────────────────────────────────────────────────

interface FormDetailProps {
  form:          EvaluationForm | null
  accessToken?:  string        // session Bearer JWT — grant-first ABAC on the API
  onSaved:       () => void
  onNew:         () => void
  onDirtyChange: (dirty: boolean) => void
  onCancel:      () => void   // called when user cancels a new-form creation
  triggerNew:    number       // increment to programmatically trigger startNew()
}

function FormDetail({ form, accessToken, onSaved, onNew, onDirtyChange, onCancel, triggerNew }: FormDetailProps) {
  const { t } = useTranslation('evaluation')
  const { tenantId: TENANT } = useAuth()
  const [editing, setEditing] = useState<WForm | null>(null)
  const [isDirty, setIsDirty] = useState(false)
  const [saving,  setSaving]  = useState(false)
  const [error,   setError]   = useState<string | null>(null)
  // snapshot of the last loaded/saved state — used to detect dirty
  const cleanRef = useRef<string>('null')

  // When form prop changes (select / deselect), reload editing and reset dirty
  useEffect(() => {
    const w = form ? apiToWorking(form) : null
    setEditing(w)
    cleanRef.current = JSON.stringify(w)
    setIsDirty(false)
    onDirtyChange(false)
  }, [form]) // eslint-disable-line react-hooks/exhaustive-deps

  // Warn browser tab close / refresh while dirty
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (isDirty) { e.preventDefault(); e.returnValue = '' }
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [isDirty])

  // Wrapper: update editing state and mark as dirty
  const updateEditing = (w: WForm | null) => {
    setEditing(w)
    const dirty = JSON.stringify(w) !== cleanRef.current
    setIsDirty(dirty)
    onDirtyChange(dirty)
  }

  const markClean = () => {
    cleanRef.current = JSON.stringify(editing)
    setIsDirty(false)
    onDirtyChange(false)
  }

  const startNew = () => {
    updateEditing({
      form_id:             '',
      tenant_id:           TENANT,
      name:                '',
      description:         '',
      status:              'active',
      dimensions:          [],
      knowledge_namespace: null,
      created_at:          '',
      updated_at:          '',
    })
    onNew()
  }

  // Sidebar "+ New Form" button increments triggerNew to call startNew()
  useEffect(() => {
    if (triggerNew > 0) startNew()
  }, [triggerNew]) // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    if (!editing) return
    setSaving(true)
    setError(null)
    try {
      const payload = workingToApi(editing)
      if (!editing.form_id || editing.form_id === '') {
        await createForm(TENANT, payload, accessToken)
      } else {
        await updateForm(editing.form_id, payload, accessToken)
      }
      markClean()
      onSaved()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  const archive = async () => {
    if (!editing?.form_id) return
    if (!confirm(t('forms.detail.archiveConfirm'))) return
    setSaving(true)
    try {
      await deleteForm(editing.form_id, accessToken)
      markClean()
      onSaved()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  const addDimension = () => {
    if (!editing) return
    const newDim: WDimension = {
      dimension_id: `dim_${Date.now()}`,
      label:        '',
      pts:          0,
      criteria:     [],
    }
    const newDims = equaliseDims([...editing.dimensions, newDim])
    updateEditing({ ...editing, dimensions: newDims })
  }

  const removeDimension = (idx: number) => {
    if (!editing) return
    const remaining = editing.dimensions.filter((_, j) => j !== idx)
    updateEditing({ ...editing, dimensions: equaliseDims(remaining) })
  }

  const updateDimension = (idx: number, updated: WDimension) => {
    if (!editing) return
    const dims = [...editing.dimensions]
    dims[idx] = updated
    updateEditing({ ...editing, dimensions: dims })
  }

  if (!editing) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-muted-light gap-4">
        <p>{t('forms.detail.empty')}</p>
        <button onClick={startNew} className="bg-primary text-white px-4 py-2 rounded text-sm hover:bg-primary-dark">
          {t('forms.create')}
        </button>
      </div>
    )
  }

  const dimSum = sum(editing.dimensions.map(d => d.pts))

  // Form-level validation errors that block saving
  const dimSumError    = editing.dimensions.length > 0 && dimSum !== TOTAL_PTS
  const critSumErrors  = editing.dimensions.filter(d => d.criteria.length > 0 && sum(d.criteria.map(c => c.pts)) !== d.pts)
  const hasFormErrors  = dimSumError || critSumErrors.length > 0

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 p-4 border-b bg-white">
        <input
          className="flex-1 text-lg font-semibold border-b border-transparent hover:border-border-strong focus:border-primary outline-none py-0.5"
          placeholder={t('forms.detail.namePlaceholder')}
          value={editing.name}
          onChange={e => updateEditing({ ...editing, name: e.target.value })}
        />
        <StatusBadge status={editing.status} />
        {/* Total pts pill */}
        <span className="text-xs font-medium bg-surface-alt text-muted px-3 py-1 rounded-full">
          {t('forms.detail.totalPts', { total: TOTAL_PTS })}
        </span>
        {/* + Novo / Cancelar: when creating a new form, show Cancelar instead */}
        {editing.form_id === '' ? (
          <button
            type="button"
            onClick={() => { setEditing(null); setIsDirty(false); onDirtyChange(false); onCancel() }}
            className="text-xs text-muted hover:text-dark border rounded px-2 py-1"
          >
            {t('forms.detail.cancel')}
          </button>
        ) : (
          <button type="button" onClick={startNew} className="text-xs text-muted hover:text-dark border rounded px-2 py-1">
            {t('forms.detail.newBtn')}
          </button>
        )}
        {/* Descartar — reloads from last saved state */}
        {form && editing.form_id !== '' && isDirty && (
          <button
            type="button"
            onClick={() => { setEditing(apiToWorking(form)); setIsDirty(false); onDirtyChange(false) }}
            className="text-xs text-muted hover:text-dark border rounded px-2 py-1"
          >
            {t('forms.detail.discard')}
          </button>
        )}
        {editing.form_id && (
          <button type="button" onClick={archive} className="text-xs text-red hover:text-red-text border border-red/30 rounded px-2 py-1">
            {t('forms.detail.archive')}
          </button>
        )}
        <button
          type="button"
          onClick={save}
          disabled={saving || hasFormErrors}
          title={hasFormErrors ? t('forms.detail.saveErrorTooltip') : undefined}
          className="bg-primary text-white text-sm px-4 py-1.5 rounded hover:bg-primary-dark disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {saving ? t('forms.saving') : t('forms.save')}
        </button>
      </div>

      {/* Form-level errors banner */}
      {hasFormErrors && (
        <div className="bg-red-light border-b border-red/30 text-red-text text-xs px-4 py-2 flex gap-3 flex-wrap">
          {dimSumError && (
            <span dangerouslySetInnerHTML={{ __html: t('forms.errors.dimSum', { dimSum, total: TOTAL_PTS }).replace(
              String(dimSum), `<strong>${dimSum}</strong>`
            )}} />
          )}
          {critSumErrors.map(d => {
            const critSum = sum(d.criteria.map(c => c.pts))
            return (
              <span key={d.dimension_id} dangerouslySetInnerHTML={{ __html: t('forms.errors.critSum', {
                label:   d.label || t('forms.dimension.unnamed'),
                critSum,
                dimPts:  d.pts,
              }).replace(String(critSum), `<strong>${critSum}</strong>`) }} />
            )
          })}
        </div>
      )}
      {error && <div className="bg-red-light text-red-text text-sm px-4 py-2">{error}</div>}

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        <textarea
          className="w-full border border-border-strong rounded px-3 py-2 text-sm resize-none"
          rows={2}
          placeholder={t('forms.detail.descriptionPlaceholder')}
          value={editing.description}
          onChange={e => updateEditing({ ...editing, description: e.target.value })}
        />

        <div className="flex gap-4 items-center text-sm text-muted">
          <label className="flex items-center gap-2">
            {t('forms.detail.knowledgeNamespace')}
            <input
              className="border border-border-strong rounded px-2 py-1 text-sm w-48"
              placeholder="evaluation_policies"
              value={editing.knowledge_namespace ?? ''}
              onChange={e => updateEditing({ ...editing, knowledge_namespace: e.target.value || null })}
            />
          </label>
        </div>

        {/* Dimensions — container turns red when dim totals don't add up to 100 */}
        <div
          className="space-y-3 rounded-lg p-3 -mx-3 transition-colors"
          style={dimSumError ? { backgroundColor: '#fecaca', borderLeft: '4px solid #ef4444' } : undefined}
        >
          {/* Dimensions header */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <h3 className={`font-semibold ${dimSumError ? 'text-red-text' : 'text-dark'}`}>
                {t('forms.dimensions')}
              </h3>
              {editing.dimensions.length > 0 && (
                <SumBadge current={dimSum} total={TOTAL_PTS} />
              )}
            </div>
            <button
              type="button"
              onClick={addDimension}
              className="text-xs text-primary hover:text-primary-dark border border-primary/30 rounded px-2 py-1"
            >
              {t('forms.addDimension')}
            </button>
          </div>

          {editing.dimensions.map((d, i) => (
            <DimensionEditor
              key={d.dimension_id}
              dim={d}
              onChange={updated => updateDimension(i, updated)}
              onDelete={() => removeDimension(i)}
            />
          ))}

          {editing.dimensions.length === 0 && (
            <p className="text-sm text-muted-light text-center py-4">
              {t('forms.detail.noDimensions')}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

// ── FormsPage ─────────────────────────────────────────────────────────────────

export default function FormsPage() {
  const { t } = useTranslation('evaluation')
  const { tenantId: TENANT, session } = useAuth()
  const { forms, loading, reload }  = useForms(TENANT, session?.accessToken)
  const [selected, setSelected]     = useState<EvaluationForm | null>(null)
  const [isDirty,  setIsDirty]      = useState(false)
  const [triggerNew, setTriggerNew] = useState(0)

  // Block React Router navigation while there are unsaved changes
  const blocker = useBlocker(isDirty)
  useEffect(() => {
    if (blocker.state === 'blocked') {
      if (confirm(t('forms.blocker.discardAndLeave'))) {
        blocker.proceed()
      } else {
        blocker.reset()
      }
    }
  }, [blocker, t])

  // Guard function: ask before discarding unsaved changes on sidebar click
  const handleSelectForm = (f: EvaluationForm) => {
    if (isDirty) {
      if (!confirm(t('forms.blocker.discardAndOpen'))) return
    }
    setSelected(f)
    setIsDirty(false)
  }

  // "+ New Form" button in sidebar — guard dirty state then delegate to FormDetail
  const handleNewFormRequest = () => {
    if (isDirty && !confirm(t('forms.blocker.discardAndOpen'))) return
    setSelected(null)
    setIsDirty(false)
    setTriggerNew(n => n + 1)
  }

  return (
    <div className="flex h-full">
      {/* Sidebar */}
      <aside className="w-72 border-r flex flex-col bg-surface-muted">
        <div className="p-3 border-b flex gap-2">
          <button
            onClick={handleNewFormRequest}
            className="flex-1 bg-primary text-white text-xs px-2 py-1 rounded hover:bg-primary-dark"
          >
            {t('forms.detail.newBtn')}
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {loading && <p className="text-sm text-muted-light p-2">{t('loading')}</p>}
          {forms.map(f => (
            <button
              key={f.form_id}
              onClick={() => handleSelectForm(f)}
              className={`w-full text-left px-3 py-2 rounded text-sm transition-colors ${
                selected?.form_id === f.form_id
                  ? 'bg-primary text-white'
                  : 'hover:bg-border text-dark'
              }`}
            >
              <div className="font-medium truncate">{f.name}</div>
              <div className="text-xs opacity-70">
                {t('forms.sidebar.dimensionCount', { count: (f.dimensions ?? []).length })} · {t(`forms.${f.status}`, f.status)}
              </div>
            </button>
          ))}
          {!loading && forms.length === 0 && (
            <p className="text-xs text-muted-light text-center py-6">{t('forms.sidebar.noForms')}</p>
          )}
        </div>
      </aside>

      {/* Detail */}
      <FormDetail
        form={selected}
        accessToken={session?.accessToken}
        onSaved={() => { reload(); setSelected(null); setIsDirty(false) }}
        onNew={() => setSelected(null)}
        onDirtyChange={setIsDirty}
        onCancel={() => setSelected(null)}
        triggerNew={triggerNew}
      />
    </div>
  )
}
