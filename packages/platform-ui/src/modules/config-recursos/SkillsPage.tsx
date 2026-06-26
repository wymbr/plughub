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
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import {
  useNamespace,
  putConfig,
  deleteConfig,
} from '@/modules/config-plataforma/api/config-hooks'
import Spinner from '@/components/ui/Spinner'

const NS = 'competency_skills'

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
  const { t } = useTranslation('configRecursos')
  return (
    <div className="flex items-center gap-1.5">
      <div className="flex gap-0.5">
        {Array.from({ length: 10 }, (_, i) => (
          <div
            key={i}
            className={`w-2 h-4 rounded-sm transition-colors ${
              i < value ? 'bg-primary' : 'bg-border'
            }`}
          />
        ))}
      </div>
      <span className="text-xs font-mono font-bold text-dark w-5 text-right">
        {value}
      </span>
      <span className="text-2xs text-muted-light">
        {t(`competencySkills.domainHints.${value}`)}
      </span>
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
  const { t } = useTranslation('configRecursos')
  return (
    <div className="flex items-center gap-3 flex-1">
      <input
        type="range"
        min={0}
        max={9}
        step={1}
        value={value}
        onChange={e => onChange(Number(e.target.value))}
        className="flex-1 max-w-40 accent-primary"
      />
      <span className="text-xs font-mono font-bold text-dark w-5">{value}</span>
      <span className="text-2xs text-muted-light">
        {t(`competencySkills.domainHints.${value}`)}
      </span>
    </div>
  )
}

// ── SkillsPage ─────────────────────────────────────────────────────────────────

const SkillsPage: React.FC = () => {
  const { session } = useAuth()
  const { t } = useTranslation('configRecursos')
  const tenantId = session?.tenantId ?? ''

  const { entries, loading, error: loadError, reload } = useNamespace(tenantId, NS)
  const sortedKeys = Object.keys(entries).sort()

  // G-PROBE platform-wide: escritas usam o Bearer do operador + ABAC `config.plataforma`
  // (namespace competency_skills → default) — sem caixa de admin-token.
  const adminToken = session?.accessToken ?? ''
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
    if (!key) { setOpError(t('competencySkills.keyRequired')); return }
    if (!/^[a-z0-9_]+$/.test(key)) {
      setOpError(t('competencySkills.keyInvalid'))
      return
    }
    if (entries[key]) { setOpError(t('competencySkills.keyExists', { key })); return }
    if (!adminToken) { setOpError(t('competencySkills.adminRequired')); return }
    setSaving(true); setOpError(null)
    try {
      await putConfig(NS, key, { domain: newDomain }, null, '', adminToken)
      reload()
      setIsAdding(false)
    } catch (e) {
      setOpError(String(e))
    } finally {
      setSaving(false)
    }
  }, [newKey, newDomain, adminToken, entries, reload, t])

  const handleEditStart = (key: string) => {
    setEditDomain(getDomain(entries[key]?.value))
    setEditingKey(key)
    setIsAdding(false)
    setOpError(null)
  }

  const handleEditSave = useCallback(async (key: string) => {
    if (!adminToken) { setOpError(t('competencySkills.adminRequired')); return }
    setSaving(true); setOpError(null)
    try {
      await putConfig(NS, key, { domain: editDomain }, null, '', adminToken)
      reload()
      setEditingKey(null)
    } catch (e) {
      setOpError(String(e))
    } finally {
      setSaving(false)
    }
  }, [editDomain, adminToken, reload, t])

  const handleDelete = useCallback(async (key: string) => {
    if (!adminToken) { setOpError(t('competencySkills.adminRequiredDelete')); return }
    if (!window.confirm(t('competencySkills.confirmDelete', { key }))) return
    setDeletingKey(key); setOpError(null)
    try {
      await deleteConfig(NS, key, null, '', adminToken)
      reload()
    } catch (e) {
      setOpError(String(e))
    } finally {
      setDeletingKey(null)
    }
  }, [adminToken, reload, t])

  // ── render ────────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col gap-4">
      {/* Info banner */}
      <div className="bg-info-light border border-info/30 rounded px-4 py-2.5 text-sm text-info-text">
        {t('competencySkills.infoBanner', {
          defaultValue:
            'Competency skills are used in static routing — agents and pools declare a level (0-9) per skill. Stored in competency_skills in the Config API.',
        })}
      </div>

      {/* Controls row */}
      <div className="flex items-center gap-3 flex-wrap">
        <button
          onClick={handleAddStart}
          disabled={isAdding}
          className="ml-auto px-3 py-1.5 text-xs font-semibold bg-primary text-white rounded hover:bg-primary-dark disabled:opacity-40 transition-colors"
        >
          {t('competencySkills.newSkill')}
        </button>
      </div>

      {/* Error */}
      {(opError || loadError) && (
        <div className="bg-red-light border border-red/30 text-red-text px-3 py-2 rounded text-xs flex justify-between items-center">
          <span>{opError ?? loadError}</span>
          <button onClick={() => setOpError(null)} className="ml-3 font-bold leading-none">✕</button>
        </div>
      )}

      {/* Table */}
      <div className="border border-border rounded overflow-hidden">
        {/* Column header */}
        <div className="flex gap-4 px-4 py-2 bg-surface-muted border-b border-border text-2xs font-semibold text-muted-light uppercase tracking-wide shrink-0">
          <span className="w-44 shrink-0">{t('competencySkills.columns.key')}</span>
          <span className="flex-1">{t('competencySkills.columns.domain')}</span>
          <span className="w-28 shrink-0 text-right">{t('competencySkills.columns.actions')}</span>
        </div>

        {/* Loading */}
        {loading && (
          <div className="flex justify-center py-8"><Spinner /></div>
        )}

        {/* Add row */}
        {isAdding && (
          <div className="flex items-center gap-4 px-4 py-3 border-b border-primary/20 bg-primary-light/40">
            <input
              value={newKey}
              onChange={e => setNewKey(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, ''))}
              placeholder={t('competencySkills.keyPlaceholder')}
              autoFocus
              className="w-44 shrink-0 text-xs font-mono px-2 py-1.5 border border-border-strong rounded focus:outline-none focus:border-primary bg-white"
            />
            <DomainSlider value={newDomain} onChange={setNewDomain} />
            <div className="flex gap-1.5 shrink-0">
              <button
                onClick={handleAddSave}
                disabled={saving || !adminToken}
                className="px-2.5 py-1 text-xs font-semibold bg-primary text-white rounded disabled:opacity-40 hover:bg-primary-dark transition-colors"
              >
                {saving ? '…' : t('competencySkills.save')}
              </button>
              <button
                onClick={() => { setIsAdding(false); setOpError(null) }}
                className="px-2.5 py-1 text-xs border border-border-strong rounded text-muted hover:text-dark transition-colors"
              >
                {t('competencySkills.cancel')}
              </button>
            </div>
          </div>
        )}

        {/* Empty state */}
        {!loading && sortedKeys.length === 0 && !isAdding && (
          <div className="px-4 py-8 text-center text-sm text-muted-light">
            {t('competencySkills.empty')}{' '}
            <button
              onClick={handleAddStart}
              className="text-primary hover:underline font-medium"
            >
              {t('competencySkills.newSkill')}
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
              className="flex items-center gap-4 px-4 py-3 border-b border-border last:border-0"
            >
              <span className="w-44 shrink-0 text-xs font-mono font-semibold text-dark">
                {key}
              </span>

              {isEditing ? (
                <>
                  <DomainSlider value={editDomain} onChange={setEditDomain} />
                  <div className="flex gap-1.5 shrink-0">
                    <button
                      onClick={() => handleEditSave(key)}
                      disabled={saving || !adminToken}
                      className="px-2.5 py-1 text-xs font-semibold bg-primary text-white rounded disabled:opacity-40 hover:bg-primary-dark transition-colors"
                    >
                      {saving ? '…' : t('competencySkills.save')}
                    </button>
                    <button
                      onClick={() => { setEditingKey(null); setOpError(null) }}
                      className="px-2.5 py-1 text-xs border border-border-strong rounded text-muted hover:text-dark transition-colors"
                    >
                      {t('competencySkills.cancel')}
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
                      className="px-2 py-1 text-xs border border-border-strong rounded text-muted hover:text-dark hover:border-border transition-colors"
                    >
                      {t('competencySkills.edit')}
                    </button>
                    {adminToken && (
                      <button
                        onClick={() => handleDelete(key)}
                        disabled={isDeleting}
                        className="px-2 py-1 text-xs border border-red/30 rounded text-red hover:bg-red-light disabled:opacity-40 transition-colors"
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
