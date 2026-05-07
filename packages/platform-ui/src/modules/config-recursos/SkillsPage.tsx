/**
 * SkillsPage.tsx — CRUD for competency (routing) skills
 *
 * Stored in config-api namespace: competency_skills
 * Each entry:
 *   key   — skill identifier  (e.g. "ingles", "cobranca", "retencao")
 *   value — { domain: 0-9 }  — default strength scale for routing
 *
 * Write operations require an admin token (same pattern as NamespaceEditor).
 */
import React, { useState, useCallback } from 'react'
import { useAuth } from '@/auth/useAuth'
import {
  useNamespace,
  putConfig,
  deleteConfig,
} from '@/modules/config-plataforma/api/config-hooks'
import Spinner from '@/components/ui/Spinner'

const NS = 'competency_skills'

const DOMAIN_HINTS: Record<number, string> = {
  0: 'Não requerido',
  1: 'Mínimo',
  2: 'Básico',
  3: 'Básico',
  4: 'Intermediário',
  5: 'Intermediário',
  6: 'Bom',
  7: 'Avançado',
  8: 'Avançado',
  9: 'Especialista',
}

// ── helpers ────────────────────────────────────────────────────────────────────

function getDomain(value: unknown): number {
  if (typeof value === 'number') return Math.min(9, Math.max(0, Math.round(value)))
  if (typeof value === 'object' && value !== null) {
    const d = (value as Record<string, unknown>).domain
    return typeof d === 'number' ? Math.min(9, Math.max(0, Math.round(d))) : 5
  }
  return 5
}

// ── DomainBar — visual 0-9 pip bar ────────────────────────────────────────────

function DomainBar({ value }: { value: number }) {
  return (
    <div className="flex items-center gap-1.5">
      <div className="flex gap-0.5">
        {Array.from({ length: 10 }, (_, i) => (
          <div
            key={i}
            className={`w-2 h-4 rounded-sm transition-colors ${
              i < value ? 'bg-primary' : 'bg-gray-200'
            }`}
          />
        ))}
      </div>
      <span className="text-xs font-mono font-bold text-gray-700 w-5 text-right">
        {value}
      </span>
      <span className="text-[10px] text-gray-400">{DOMAIN_HINTS[value]}</span>
    </div>
  )
}

// ── DomainSlider ──────────────────────────────────────────────────────────────

function DomainSlider({
  value,
  onChange,
}: {
  value: number
  onChange: (v: number) => void
}) {
  return (
    <div className="flex items-center gap-3 flex-1">
      <input
        type="range"
        min={0}
        max={9}
        step={1}
        value={value}
        onChange={e => onChange(Number(e.target.value))}
        className="flex-1 max-w-[160px] accent-primary"
      />
      <span className="text-xs font-mono font-bold text-gray-700 w-5">{value}</span>
      <span className="text-[10px] text-gray-400">{DOMAIN_HINTS[value]}</span>
    </div>
  )
}

// ── SkillsPage ─────────────────────────────────────────────────────────────────

const SkillsPage: React.FC = () => {
  const { session } = useAuth()
  const tenantId = session?.tenantId ?? ''

  const { entries, loading, error: loadError, reload } = useNamespace(tenantId, NS)
  const sortedKeys = Object.keys(entries).sort()

  const [adminToken, setAdminToken] = useState('')
  const [editingKey, setEditingKey] = useState<string | null>(null)
  const [editDomain, setEditDomain] = useState(5)

  const [isAdding, setIsAdding] = useState(false)
  const [newKey, setNewKey] = useState('')
  const [newDomain, setNewDomain] = useState(5)

  const [saving, setSaving] = useState(false)
  const [deletingKey, setDeletingKey] = useState<string | null>(null)
  const [opError, setOpError] = useState<string | null>(null)

  // ── actions ──────────────────────────────────────────────────────────────────

  const handleAddStart = () => {
    setIsAdding(true)
    setNewKey('')
    setNewDomain(5)
    setEditingKey(null)
    setOpError(null)
  }

  const handleAddSave = useCallback(async () => {
    const key = newKey.trim()
    if (!key) { setOpError('Key obrigatória'); return }
    if (!/^[a-z0-9_]+$/.test(key)) {
      setOpError('Key: apenas letras minúsculas, dígitos e _')
      return
    }
    if (entries[key]) { setOpError(`Key "${key}" já existe`); return }
    if (!adminToken) { setOpError('Admin token obrigatório para salvar'); return }
    setSaving(true); setOpError(null)
    try {
      await putConfig(NS, key, { domain: newDomain }, null, adminToken)
      reload()
      setIsAdding(false)
    } catch (e) {
      setOpError(String(e))
    } finally {
      setSaving(false)
    }
  }, [newKey, newDomain, adminToken, entries, reload])

  const handleEditStart = (key: string) => {
    setEditDomain(getDomain(entries[key]?.value))
    setEditingKey(key)
    setIsAdding(false)
    setOpError(null)
  }

  const handleEditSave = useCallback(async (key: string) => {
    if (!adminToken) { setOpError('Admin token obrigatório para salvar'); return }
    setSaving(true); setOpError(null)
    try {
      await putConfig(NS, key, { domain: editDomain }, null, adminToken)
      reload()
      setEditingKey(null)
    } catch (e) {
      setOpError(String(e))
    } finally {
      setSaving(false)
    }
  }, [editDomain, adminToken, reload])

  const handleDelete = useCallback(async (key: string) => {
    if (!adminToken) { setOpError('Admin token obrigatório para excluir'); return }
    if (!window.confirm(`Remover competency skill "${key}"?`)) return
    setDeletingKey(key); setOpError(null)
    try {
      await deleteConfig(NS, key, null, adminToken)
      reload()
    } catch (e) {
      setOpError(String(e))
    } finally {
      setDeletingKey(null)
    }
  }, [adminToken, reload])

  // ── render ────────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col gap-4">
      {/* Info banner */}
      <div className="bg-blue-50 border border-blue-200 rounded px-4 py-2.5 text-sm text-blue-800">
        Competency skills são usadas no roteamento estático — agentes e pools declaram
        um nível (0-9) por skill. Armazenadas em{' '}
        <code className="font-mono text-xs bg-blue-100 px-1 rounded">competency_skills</code>{' '}
        na Config API.
      </div>

      {/* Controls row */}
      <div className="flex items-center gap-3 flex-wrap">
        <label className="text-xs font-semibold text-gray-600 shrink-0">Admin Token</label>
        <input
          type="password"
          value={adminToken}
          onChange={e => setAdminToken(e.target.value)}
          placeholder="Token para escrita"
          className="w-52 text-xs font-mono px-2 py-1.5 border border-gray-300 rounded focus:outline-none focus:border-blue-400 bg-white"
        />
        {!adminToken && (
          <span className="text-xs text-amber-600">⚠ Defina o admin token para editar</span>
        )}
        <button
          onClick={handleAddStart}
          disabled={isAdding}
          className="ml-auto px-3 py-1.5 text-xs font-semibold bg-primary text-white rounded hover:bg-blue-800 disabled:opacity-40 transition-colors"
        >
          + Nova Skill
        </button>
      </div>

      {/* Error */}
      {(opError || loadError) && (
        <div className="bg-red-50 border border-red-300 text-red-700 px-3 py-2 rounded text-xs flex justify-between items-center">
          <span>{opError ?? loadError}</span>
          <button onClick={() => setOpError(null)} className="ml-3 font-bold leading-none">✕</button>
        </div>
      )}

      {/* Table */}
      <div className="border border-gray-200 rounded overflow-hidden">
        {/* Column header */}
        <div className="flex gap-4 px-4 py-2 bg-gray-50 border-b border-gray-200 text-[10px] font-semibold text-gray-400 uppercase tracking-wide shrink-0">
          <span className="w-44 shrink-0">Chave</span>
          <span className="flex-1">Domínio (0-9)</span>
          <span className="w-28 shrink-0 text-right">Ações</span>
        </div>

        {/* Loading */}
        {loading && (
          <div className="flex justify-center py-8"><Spinner /></div>
        )}

        {/* Add row */}
        {isAdding && (
          <div className="flex items-center gap-4 px-4 py-3 border-b border-blue-100 bg-blue-50/40">
            <input
              value={newKey}
              onChange={e => setNewKey(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, ''))}
              placeholder="ex: ingles"
              autoFocus
              className="w-44 shrink-0 text-xs font-mono px-2 py-1.5 border border-gray-300 rounded focus:outline-none focus:border-blue-400 bg-white"
            />
            <DomainSlider value={newDomain} onChange={setNewDomain} />
            <div className="flex gap-1.5 shrink-0">
              <button
                onClick={handleAddSave}
                disabled={saving || !adminToken}
                className="px-2.5 py-1 text-xs font-semibold bg-primary text-white rounded disabled:opacity-40 hover:bg-blue-800 transition-colors"
              >
                {saving ? '…' : 'Salvar'}
              </button>
              <button
                onClick={() => { setIsAdding(false); setOpError(null) }}
                className="px-2.5 py-1 text-xs border border-gray-300 rounded text-gray-600 hover:text-gray-900 transition-colors"
              >
                Cancelar
              </button>
            </div>
          </div>
        )}

        {/* Empty state */}
        {!loading && sortedKeys.length === 0 && !isAdding && (
          <div className="px-4 py-8 text-center text-sm text-gray-400">
            Nenhuma competency skill cadastrada.{' '}
            <button
              onClick={handleAddStart}
              className="text-primary hover:underline font-medium"
            >
              + Nova Skill
            </button>
          </div>
        )}

        {/* Rows */}
        {sortedKeys.map(key => {
          const domain = getDomain(entries[key]?.value)
          const isEditing = editingKey === key
          const isDeleting = deletingKey === key

          return (
            <div
              key={key}
              className="flex items-center gap-4 px-4 py-3 border-b border-gray-100 last:border-0"
            >
              <span className="w-44 shrink-0 text-xs font-mono font-semibold text-gray-700">
                {key}
              </span>

              {isEditing ? (
                <>
                  <DomainSlider value={editDomain} onChange={setEditDomain} />
                  <div className="flex gap-1.5 shrink-0">
                    <button
                      onClick={() => handleEditSave(key)}
                      disabled={saving || !adminToken}
                      className="px-2.5 py-1 text-xs font-semibold bg-primary text-white rounded disabled:opacity-40 hover:bg-blue-800 transition-colors"
                    >
                      {saving ? '…' : 'Salvar'}
                    </button>
                    <button
                      onClick={() => { setEditingKey(null); setOpError(null) }}
                      className="px-2.5 py-1 text-xs border border-gray-300 rounded text-gray-600 hover:text-gray-900 transition-colors"
                    >
                      Cancelar
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <div className="flex-1">
                    <DomainBar value={domain} />
                  </div>
                  <div className="flex gap-1.5 shrink-0">
                    <button
                      onClick={() => handleEditStart(key)}
                      className="px-2 py-1 text-xs border border-gray-300 rounded text-gray-600 hover:text-gray-900 hover:border-gray-400 transition-colors"
                    >
                      ✏ Editar
                    </button>
                    {adminToken && (
                      <button
                        onClick={() => handleDelete(key)}
                        disabled={isDeleting}
                        className="px-2 py-1 text-xs border border-red-200 rounded text-red-500 hover:bg-red-50 disabled:opacity-40 transition-colors"
                      >
                        {isDeleting ? '…' : '🗑'}
                      </button>
                    )}
                  </div>
                </>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default SkillsPage
