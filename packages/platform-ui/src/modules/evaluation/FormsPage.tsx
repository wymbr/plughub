/**
 * FormsPage.tsx
 * /evaluation/forms — EvaluationForm editor
 * Allows admins to create/edit forms with dimensions[] and criteria[].
 */

import React, { useState } from 'react'
import { useForms, createForm, updateForm, deleteForm } from '@/api/evaluation-hooks'
import type { EvaluationForm, EvaluationDimension, EvaluationCriterion } from '@/types'

const TENANT = import.meta.env.VITE_TENANT_ID ?? 'tenant_demo'

function ScoreBadge({ score }: { score: number }) {
  const color = score >= 8 ? 'bg-green-100 text-green-800' : score >= 6 ? 'bg-yellow-100 text-yellow-800' : 'bg-red-100 text-red-800'
  return <span className={`px-2 py-0.5 rounded text-xs font-medium ${color}`}>{score.toFixed(1)}</span>
}

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    active: 'bg-green-100 text-green-800',
    archived: 'bg-gray-100 text-gray-600',
  }
  return <span className={`px-2 py-0.5 rounded text-xs font-medium ${styles[status] ?? 'bg-gray-100 text-gray-600'}`}>{status}</span>
}

// ── CriterionEditor ───────────────────────────────────────────────────────────

interface CriterionEditorProps {
  criterion: EvaluationCriterion
  onChange: (c: EvaluationCriterion) => void
  onDelete: () => void
}

function CriterionEditor({ criterion, onChange, onDelete }: CriterionEditorProps) {
  return (
    <div className="border border-gray-200 rounded p-3 space-y-2 bg-gray-50">
      <div className="flex gap-2">
        <input
          className="flex-1 border border-gray-300 rounded px-2 py-1 text-sm"
          placeholder="Label do critério"
          value={criterion.label}
          onChange={e => onChange({ ...criterion, label: e.target.value })}
        />
        <input
          className="w-20 border border-gray-300 rounded px-2 py-1 text-sm text-center"
          type="number"
          min={0}
          max={100}
          placeholder="Peso %"
          value={criterion.weight}
          onChange={e => onChange({ ...criterion, weight: Number(e.target.value) })}
        />
        <button onClick={onDelete} className="text-red-500 hover:text-red-700 text-xs px-2">✕</button>
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
  dim: EvaluationDimension
  onChange: (d: EvaluationDimension) => void
  onDelete: () => void
}

function DimensionEditor({ dim, onChange, onDelete }: DimensionEditorProps) {
  const addCriterion = () => {
    const c: EvaluationCriterion = {
      criterion_id: `crit_${Date.now()}`,
      label: '',
      description: '',
      weight: 10,
      allows_na: false,
      max_score: 10,
      applies_when: null,
    }
    onChange({ ...dim, criteria: [...dim.criteria, c] })
  }

  return (
    <div className="border border-blue-200 rounded p-3 space-y-2 bg-blue-50/30">
      <div className="flex gap-2 items-center">
        <input
          className="flex-1 border border-gray-300 rounded px-2 py-1 text-sm font-medium"
          placeholder="Nome da dimensão"
          value={dim.label}
          onChange={e => onChange({ ...dim, label: e.target.value })}
        />
        <input
          className="w-20 border border-gray-300 rounded px-2 py-1 text-sm text-center"
          type="number"
          min={0}
          max={100}
          placeholder="Peso %"
          value={dim.weight}
          onChange={e => onChange({ ...dim, weight: Number(e.target.value) })}
        />
        <button onClick={onDelete} className="text-red-400 hover:text-red-600 text-xs px-2">✕ Dimensão</button>
      </div>

      <div className="space-y-2 pl-2">
        {dim.criteria.map((c, i) => (
          <CriterionEditor
            key={c.criterion_id}
            criterion={c}
            onChange={updated => {
              const criteria = [...dim.criteria]
              criteria[i] = updated
              onChange({ ...dim, criteria })
            }}
            onDelete={() => onChange({ ...dim, criteria: dim.criteria.filter((_, j) => j !== i) })}
          />
        ))}
      </div>

      <button
        onClick={addCriterion}
        className="text-xs text-blue-600 hover:text-blue-800 border border-blue-300 rounded px-2 py-1"
      >
        + Critério
      </button>
    </div>
  )
}

// ── FormDetail ─────────────────────────────────────────────────────────────────

interface FormDetailProps {
  form: EvaluationForm | null
  adminToken: string
  onSaved: () => void
  onNew: () => void
}

function FormDetail({ form, adminToken, onSaved, onNew }: FormDetailProps) {
  const [editing, setEditing] = useState<EvaluationForm | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  React.useEffect(() => { setEditing(form ? { ...form } : null) }, [form])

  const startNew = () => {
    setEditing({
      form_id: '',
      tenant_id: TENANT,
      name: '',
      description: '',
      status: 'active',
      dimensions: [],
      knowledge_namespace: null,
      created_at: '',
      updated_at: '',
    })
    onNew()
  }

  const save = async () => {
    if (!editing) return
    setSaving(true)
    setError(null)
    try {
      if (!editing.form_id || editing.form_id === '') {
        await createForm(TENANT, editing, adminToken)
      } else {
        await updateForm(editing.form_id, editing, adminToken)
      }
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
      onSaved()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  const addDimension = () => {
    if (!editing) return
    const d: EvaluationDimension = {
      dimension_id: `dim_${Date.now()}`,
      label: '',
      weight: 25,
      criteria: [],
    }
    setEditing({ ...editing, dimensions: [...editing.dimensions, d] })
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

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 p-4 border-b bg-white">
        <input
          className="flex-1 text-lg font-semibold border-b border-transparent hover:border-gray-300 focus:border-primary outline-none py-0.5"
          placeholder="Nome do formulário"
          value={editing.name}
          onChange={e => setEditing({ ...editing, name: e.target.value })}
        />
        <StatusBadge status={editing.status} />
        <button onClick={startNew} className="text-xs text-gray-500 hover:text-gray-700 border rounded px-2 py-1">+ Novo</button>
        {editing.form_id && <button onClick={archive} className="text-xs text-red-500 hover:text-red-700 border border-red-200 rounded px-2 py-1">Arquivar</button>}
        <button onClick={save} disabled={saving} className="bg-primary text-white text-sm px-4 py-1.5 rounded hover:bg-blue-800 disabled:opacity-50">
          {saving ? 'Salvando…' : 'Salvar'}
        </button>
      </div>

      {error && <div className="bg-red-50 text-red-700 text-sm px-4 py-2">{error}</div>}

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        <textarea
          className="w-full border border-gray-300 rounded px-3 py-2 text-sm resize-none"
          rows={2}
          placeholder="Descrição do formulário"
          value={editing.description}
          onChange={e => setEditing({ ...editing, description: e.target.value })}
        />

        <div className="flex gap-4 items-center text-sm text-gray-600">
          <label className="flex items-center gap-2">
            Namespace do conhecimento:
            <input
              className="border border-gray-300 rounded px-2 py-1 text-sm w-48"
              placeholder="evaluation_policies"
              value={editing.knowledge_namespace ?? ''}
              onChange={e => setEditing({ ...editing, knowledge_namespace: e.target.value || null })}
            />
          </label>
        </div>

        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold text-gray-700">Dimensões</h3>
            <button onClick={addDimension} className="text-xs text-primary hover:text-blue-800 border border-blue-300 rounded px-2 py-1">
              + Dimensão
            </button>
          </div>

          {(editing.dimensions ?? []).map((d, i) => (
            <DimensionEditor
              key={d.dimension_id}
              dim={d}
              onChange={updated => {
                const dims = [...(editing.dimensions ?? [])]
                dims[i] = updated
                setEditing({ ...editing, dimensions: dims })
              }}
              onDelete={() => setEditing({ ...editing, dimensions: (editing.dimensions ?? []).filter((_, j) => j !== i) })}
            />
          ))}

          {(editing.dimensions ?? []).length === 0 && (
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
  const [adminToken, setAdminToken] = useState('')
  const { forms, loading, reload } = useForms(TENANT)
  const [selected, setSelected] = useState<EvaluationForm | null>(null)

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
              onClick={() => setSelected(f)}
              className={`w-full text-left px-3 py-2 rounded text-sm transition-colors ${
                selected?.form_id === f.form_id ? 'bg-primary text-white' : 'hover:bg-gray-200 text-gray-700'
              }`}
            >
              <div className="font-medium truncate">{f.name}</div>
              <div className="text-xs opacity-70">{(f.dimensions ?? []).length} dimensões · {f.status}</div>
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
        onSaved={() => { reload(); setSelected(null) }}
        onNew={() => setSelected(null)}
      />
    </div>
  )
}
