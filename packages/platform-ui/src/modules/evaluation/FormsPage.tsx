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

import React, { useState, useRef, useEffect } from 'react'
import { useBlocker } from 'react-router-dom'
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
    <span className={`text-xs font-mono px-2 py-0.5 rounded ${ok ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
      {current} / {total} pts {ok ? '✓' : '⚠'}
    </span>
  )
}

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    active:   'bg-green-100 text-green-800',
    archived: 'bg-gray-100 text-gray-600',
  }
  return <span className={`px-2 py-0.5 rounded text-xs font-medium ${styles[status] ?? 'bg-gray-100 text-gray-600'}`}>{status}</span>
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
  return (
    <div className="border border-gray-200 rounded p-3 space-y-2 bg-gray-50">
      <div className="flex gap-2 items-center">
        <input
          className="flex-1 border border-gray-300 rounded px-2 py-1 text-sm"
          placeholder="Label do critério"
          value={criterion.label}
          onChange={e => onChange({ ...criterion, label: e.target.value })}
        />
        {/* Pts input — shows empty when 0; smart-fills remaining pts on focus */}
        <div className="flex items-center gap-1 shrink-0">
          <input
            className="w-16 border border-gray-300 rounded px-2 py-1 text-sm text-center"
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
          <span className="text-xs text-gray-500">pts</span>
        </div>
        <button type="button" onClick={onDelete} className="text-red-500 hover:text-red-700 text-xs px-1">✕</button>
      </div>

      <textarea
        className="w-full border border-gray-300 rounded px-2 py-1 text-sm resize-none"
        rows={2}
        placeholder="Descrição e instruções para o avaliador"
        value={criterion.description}
        onChange={e => onChange({ ...criterion, description: e.target.value })}
      />

      <div className="flex gap-4 items-center text-xs text-gray-600">
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            checked={criterion.allows_na}
            onChange={e => onChange({ ...criterion, allows_na: e.target.checked })}
          />
          Permite N/A
        </label>
        <label className="flex items-center gap-2">
          Nota máx:
          <input
            type="number"
            min={1}
            max={10}
            className="w-12 border border-gray-300 rounded px-1 py-0.5 text-center"
            value={criterion.max_score}
            onChange={e => onChange({ ...criterion, max_score: Number(e.target.value) })}
          />
        </label>
        <input
          className="flex-1 border border-gray-300 rounded px-2 py-0.5"
          placeholder="applies_when (opcional)"
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
          className="flex-1 border border-gray-300 rounded px-2 py-1 text-sm font-medium"
          placeholder="Nome da dimensão"
          value={dim.label}
          onChange={e => onChange({ ...dim, label: e.target.value })}
        />
        {/* Pts input — re-equalises criteria whenever total changes */}
        <div className="flex items-center gap-1 shrink-0">
          <input
            className="w-16 border border-gray-300 rounded px-2 py-1 text-sm text-center font-medium"
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
          <span className="text-xs text-gray-500">pts</span>
        </div>
        <button
          type="button"
          onClick={() => {
            if (!confirm(`Remover dimensão "${dim.label || 'sem nome'}" e todos os seus critérios?`)) return
            onDelete()
          }}
          className="text-red-400 hover:text-red-600 text-xs px-2"
        >
          ✕ Dimensão
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
          className={`text-xs border rounded px-2 py-1 ${critError ? 'text-red-600 hover:text-red-800 border-red-300' : 'text-blue-600 hover:text-blue-800 border-blue-300'}`}
        >
          + Critério
        </button>
        {dim.criteria.length > 0 && <SumBadge current={critSum} total={dim.pts} />}
      </div>
    </div>
  )
}

// ── FormDetail ─────────────────────────────────────────────────────────────────

interface FormDetailProps {
  form:          EvaluationForm | null
  adminToken:    string
  onSaved:       () => void
  onNew:         () => void
  onDirtyChange: (dirty: boolean) => void
  onCancel:      () => void   // called when user cancels a new-form creation
}

function FormDetail({ form, adminToken, onSaved, onNew, onDirtyChange, onCancel }: FormDetailProps) {
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

  const save = async () => {
    if (!editing) return
    setSaving(true)
    setError(null)
    try {
      const payload = workingToApi(editing)
      if (!editing.form_id || editing.form_id === '') {
        await createForm(TENANT, payload, adminToken)
      } else {
        await updateForm(editing.form_id, payload, adminToken)
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
    if (!confirm('Arquivar este formulário?')) return
    setSaving(true)
    try {
      await deleteForm(editing.form_id, adminToken)
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
      <div className="flex-1 flex flex-col items-center justify-center text-gray-400 gap-4">
        <p>Selecione um formulário ou crie um novo</p>
        <button onClick={startNew} className="bg-primary text-white px-4 py-2 rounded text-sm hover:bg-blue-800">
          + Novo Formulário
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
          className="flex-1 text-lg font-semibold border-b border-transparent hover:border-gray-300 focus:border-primary outline-none py-0.5"
          placeholder="Nome do formulário"
          value={editing.name}
          onChange={e => updateEditing({ ...editing, name: e.target.value })}
        />
        <StatusBadge status={editing.status} />
        {/* Total pts pill */}
        <span className="text-xs font-medium bg-gray-100 text-gray-600 px-3 py-1 rounded-full">
          Total: {TOTAL_PTS} pts
        </span>
        {/* + Novo / Cancelar: when creating a new form, show Cancelar instead */}
        {editing.form_id === '' ? (
          <button
            type="button"
            onClick={() => { setEditing(null); setIsDirty(false); onDirtyChange(false); onCancel() }}
            className="text-xs text-gray-500 hover:text-gray-700 border rounded px-2 py-1"
          >
            Cancelar
          </button>
        ) : (
          <button type="button" onClick={startNew} className="text-xs text-gray-500 hover:text-gray-700 border rounded px-2 py-1">
            + Novo
          </button>
        )}
        {/* Descartar — reloads from last saved state */}
        {form && editing.form_id !== '' && isDirty && (
          <button
            type="button"
            onClick={() => { setEditing(apiToWorking(form)); setIsDirty(false); onDirtyChange(false) }}
            className="text-xs text-gray-500 hover:text-gray-700 border rounded px-2 py-1"
          >
            Descartar
          </button>
        )}
        {editing.form_id && (
          <button type="button" onClick={archive} className="text-xs text-red-500 hover:text-red-700 border border-red-200 rounded px-2 py-1">
            Arquivar
          </button>
        )}
        <button
          type="button"
          onClick={save}
          disabled={saving || hasFormErrors}
          title={hasFormErrors ? 'Corrija os erros de pontuação antes de salvar' : undefined}
          className="bg-primary text-white text-sm px-4 py-1.5 rounded hover:bg-blue-800 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {saving ? 'Salvando…' : 'Salvar'}
        </button>
      </div>

      {/* Form-level errors banner */}
      {hasFormErrors && (
        <div className="bg-red-50 border-b border-red-200 text-red-700 text-xs px-4 py-2 flex gap-3 flex-wrap">
          {dimSumError && (
            <span>⚠ Dimensões somam <strong>{dimSum}</strong> pts (esperado: {TOTAL_PTS} pts)</span>
          )}
          {critSumErrors.map(d => (
            <span key={d.dimension_id}>
              ⚠ "{d.label || 'Dimensão sem nome'}": critérios somam <strong>{sum(d.criteria.map(c => c.pts))}</strong> pts (esperado: {d.pts} pts)
            </span>
          ))}
        </div>
      )}
      {error && <div className="bg-red-50 text-red-700 text-sm px-4 py-2">{error}</div>}

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        <textarea
          className="w-full border border-gray-300 rounded px-3 py-2 text-sm resize-none"
          rows={2}
          placeholder="Descrição do formulário"
          value={editing.description}
          onChange={e => updateEditing({ ...editing, description: e.target.value })}
        />

        <div className="flex gap-4 items-center text-sm text-gray-600">
          <label className="flex items-center gap-2">
            Namespace do conhecimento:
            <input
              className="border border-gray-300 rounded px-2 py-1 text-sm w-48"
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
              <h3 className={`font-semibold ${dimSumError ? 'text-red-700' : 'text-gray-700'}`}>Dimensões</h3>
              {editing.dimensions.length > 0 && (
                <SumBadge current={dimSum} total={TOTAL_PTS} />
              )}
            </div>
            <button
              type="button"
              onClick={addDimension}
              className="text-xs text-primary hover:text-blue-800 border border-blue-300 rounded px-2 py-1"
            >
              + Dimensão
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
            <p className="text-sm text-gray-400 text-center py-4">
              Clique em "+ Dimensão" para adicionar dimensões ao formulário
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

// ── FormsPage ─────────────────────────────────────────────────────────────────

export default function FormsPage() {
  const { tenantId: TENANT } = useAuth()
  const [adminToken, setAdminToken] = useState('')
  const { forms, loading, reload }  = useForms(TENANT)
  const [selected, setSelected]     = useState<EvaluationForm | null>(null)
  const [isDirty,  setIsDirty]      = useState(false)

  // Block React Router navigation while there are unsaved changes
  const blocker = useBlocker(isDirty)
  useEffect(() => {
    if (blocker.state === 'blocked') {
      if (confirm('Há alterações não salvas. Descartar e sair?')) {
        blocker.proceed()
      } else {
        blocker.reset()
      }
    }
  }, [blocker])

  // Guard function: ask before discarding unsaved changes on sidebar click
  const handleSelectForm = (f: EvaluationForm) => {
    if (isDirty) {
      if (!confirm('Há alterações não salvas. Descartar e abrir outro formulário?')) return
    }
    setSelected(f)
    setIsDirty(false)
  }

  return (
    <div className="flex h-full">
      {/* Sidebar */}
      <aside className="w-72 border-r flex flex-col bg-gray-50">
        <div className="p-3 border-b">
          <input
            className="w-full border border-gray-300 rounded px-2 py-1 text-xs"
            type="password"
            placeholder="Admin token"
            value={adminToken}
            onChange={e => setAdminToken(e.target.value)}
          />
        </div>

        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {loading && <p className="text-sm text-gray-400 p-2">Carregando…</p>}
          {forms.map(f => (
            <button
              key={f.form_id}
              onClick={() => handleSelectForm(f)}
              className={`w-full text-left px-3 py-2 rounded text-sm transition-colors ${
                selected?.form_id === f.form_id
                  ? 'bg-primary text-white'
                  : 'hover:bg-gray-200 text-gray-700'
              }`}
            >
              <div className="font-medium truncate">{f.name}</div>
              <div className="text-xs opacity-70">
                {(f.dimensions ?? []).length} dimensões · {f.status}
              </div>
            </button>
          ))}
          {!loading && forms.length === 0 && (
            <p className="text-xs text-gray-400 text-center py-6">Nenhum formulário criado ainda</p>
          )}
        </div>
      </aside>

      {/* Detail */}
      <FormDetail
        form={selected}
        adminToken={adminToken}
        onSaved={() => { reload(); setSelected(null); setIsDirty(false) }}
        onNew={() => setSelected(null)}
        onDirtyChange={setIsDirty}
        onCancel={() => setSelected(null)}
      />
    </div>
  )
}
