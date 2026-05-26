/**
 * RoutingSkillsManager.tsx
 * CRUD de competency skills de roteamento — Config API namespace "competency_skills".
 *
 * Cada entrada: key (string) + domain (intervalo numérico, ex: [0-9], [0-1])
 * Armazenada via PUT /config/competency_skills/{key}  → value: { domain: "[0-9]" }
 *
 * Consultada por: PoolsPage (multiselect routing_skills) e criação de usuários.
 */
import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNamespace, putConfig, deleteConfig } from '../api/config-hooks'

const DOMAIN_PRESETS = ['[0-1]', '[0-3]', '[0-5]', '[0-9]', '[0-10]']

interface Props {
  tenantId:   string
  adminToken: string
}

interface SkillEntry {
  key:    string
  domain: string
}

export function RoutingSkillsManager({ tenantId, adminToken }: Props) {
  const { t } = useTranslation('configPlataforma')
  const { entries, loading, error, reload } = useNamespace(tenantId, 'competency_skills')

  const [showForm,     setShowForm]     = useState(false)
  const [editKey,      setEditKey]      = useState<string | null>(null)
  const [formKey,      setFormKey]      = useState('')
  const [formDomain,   setFormDomain]   = useState('[0-9]')
  const [customDomain, setCustomDomain] = useState('')
  const [saving,       setSaving]       = useState(false)
  const [deleting,     setDeleting]     = useState<string | null>(null)
  const [confirmDel,   setConfirmDel]   = useState<string | null>(null)
  const [formError,    setFormError]    = useState('')

  const skills: SkillEntry[] = Object.entries(entries).map(([k, e]) => ({
    key:    k,
    domain: typeof e.value === 'object' && e.value !== null
      ? ((e.value as Record<string, unknown>).domain as string) ?? ''
      : String(e.value ?? ''),
  }))

  function openNew() {
    setEditKey(null); setFormKey(''); setFormDomain('[0-9]')
    setCustomDomain(''); setFormError(''); setShowForm(true)
  }

  function openEdit(skill: SkillEntry) {
    setEditKey(skill.key); setFormKey(skill.key)
    const preset = DOMAIN_PRESETS.includes(skill.domain)
    setFormDomain(preset ? skill.domain : '__custom__')
    setCustomDomain(preset ? '' : skill.domain)
    setFormError(''); setShowForm(true)
  }

  function closeForm() {
    setShowForm(false); setEditKey(null); setFormError('')
  }

  async function handleSave() {
    const key    = formKey.trim()
    const domain = formDomain === '__custom__' ? customDomain.trim() : formDomain
    if (!key)    { setFormError(t('routingSkills.keyRequired')); return }
    if (!domain) { setFormError(t('routingSkills.domainRequired')); return }
    if (!/^\[[\d]+-[\d]+\]$/.test(domain) && !/^\[\d+\]$/.test(domain)) {
      setFormError(t('routingSkills.domainFormat')); return
    }
    if (!adminToken) { setFormError(t('routingSkills.adminRequired')); return }
    setSaving(true); setFormError('')
    try {
      await putConfig('competency_skills', key, { domain }, tenantId, adminToken)
      reload(); closeForm()
    } catch (e) {
      setFormError(e instanceof Error ? e.message : t('routingSkills.saveFailed'))
    } finally { setSaving(false) }
  }

  async function handleDelete(key: string) {
    if (!adminToken) { setFormError(t('routingSkills.adminRequiredShort')); return }
    setDeleting(key)
    try {
      await deleteConfig('competency_skills', key, tenantId, adminToken)
      reload(); setConfirmDel(null)
    } catch { /* stale */ }
    finally { setDeleting(null) }
  }

  return (
    <div className="px-6 py-4 text-dark">

      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <div className="text-sm font-bold text-dark">{t('routingSkills.title')}</div>
          <div className="text-xs text-muted mt-0.5">{t('routingSkills.subtitle')}</div>
        </div>
        {!showForm && (
          <button onClick={openNew} className="px-4 py-1.5 text-xs font-semibold rounded bg-primary text-white hover:bg-primary/90 transition-colors">
            {t('routingSkills.newSkill')}
          </button>
        )}
      </div>

      {/* Admin warning */}
      {!adminToken && (
        <div className="mb-3 px-3 py-2 rounded border border-warning/40 bg-warning/10 text-xs text-warning font-medium">
          {t('routingSkills.adminWarning')}
        </div>
      )}

      {/* Form */}
      {showForm && (
        <div className="mb-4 rounded-lg border border-border bg-surface-muted p-4">
          <div className="text-xs font-semibold text-primary mb-3">
            {editKey ? t('routingSkills.editSkill', { key: editKey }) : t('routingSkills.newSkillTitle')}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-semibold text-muted mb-1">Key *</label>
              <input
                value={formKey}
                onChange={e => setFormKey(e.target.value)}
                disabled={!!editKey}
                placeholder="ex: ingles, retencao, crm"
                className="w-full px-2.5 py-1.5 text-xs border border-border-strong rounded focus:outline-none focus:border-primary bg-white text-dark disabled:opacity-50"
              />
              <div className="text-[11px] text-muted-light mt-1">snake_case, sem espaços</div>
            </div>

            <div>
              <label className="block text-xs font-semibold text-muted mb-1">Domain *</label>
              <select
                value={formDomain}
                onChange={e => setFormDomain(e.target.value)}
                className="w-full px-2.5 py-1.5 text-xs border border-border-strong rounded focus:outline-none focus:border-primary bg-white text-dark"
              >
                {DOMAIN_PRESETS.map(d => <option key={d} value={d}>{d}</option>)}
                <option value="__custom__">Personalizado…</option>
              </select>
              {formDomain === '__custom__' && (
                <input
                  value={customDomain}
                  onChange={e => setCustomDomain(e.target.value)}
                  placeholder="ex: [0-100]"
                  className="w-full mt-1.5 px-2.5 py-1.5 text-xs border border-border-strong rounded focus:outline-none focus:border-primary bg-white text-dark"
                />
              )}
              <div className="text-[11px] text-muted-light mt-1">Intervalo de valores válidos para o agente</div>
            </div>
          </div>

          {formError && (
            <div className="mt-2.5 px-2.5 py-1.5 rounded border border-red/30 bg-red-light text-xs text-red-text">
              {formError}
            </div>
          )}

          <div className="flex gap-2 mt-3.5">
            <button onClick={handleSave} disabled={saving}
              className="px-4 py-1.5 text-xs font-semibold rounded bg-primary text-white hover:bg-primary/90 disabled:opacity-40 transition-colors">
              {saving ? t('namespace.saving') : t('namespace.save')}
            </button>
            <button onClick={closeForm}
              className="px-3 py-1.5 text-xs rounded border border-border-strong text-muted hover:text-dark transition-colors">
              {t('namespace.cancel')}
            </button>
          </div>
        </div>
      )}

      {/* State */}
      {loading && <div className="py-3 text-xs text-muted">{t('namespace.loading')}</div>}
      {error   && <div className="mb-2 text-xs text-red-text">{error}</div>}

      {!loading && skills.length === 0 && !showForm && (
        <div className="py-8 text-center text-sm text-muted-light">{t('routingSkills.empty')}</div>
      )}

      {/* Table */}
      {skills.length > 0 && (
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr className="border-b border-border">
              <th className="px-3 py-2 text-left text-[11px] font-semibold text-muted uppercase tracking-wider">Key</th>
              <th className="px-3 py-2 text-left text-[11px] font-semibold text-muted uppercase tracking-wider">Domain</th>
              <th className="px-3 py-2 text-right text-[11px] font-semibold text-muted uppercase tracking-wider w-28">{t('namespace.actions')}</th>
            </tr>
          </thead>
          <tbody>
            {skills.map(skill => (
              <tr key={skill.key} className="border-b border-border hover:bg-surface-muted/60 transition-colors">
                <td className="px-3 py-2.5">
                  <code className="text-xs font-mono text-primary bg-primary-light px-1.5 py-0.5 rounded">
                    {skill.key}
                  </code>
                </td>
                <td className="px-3 py-2.5">
                  <span className="text-xs font-mono text-green bg-green/10 border border-green/20 px-2 py-0.5 rounded">
                    {skill.domain}
                  </span>
                </td>
                <td className="px-3 py-2.5 text-right">
                  {confirmDel === skill.key ? (
                    <span className="inline-flex gap-1.5 items-center justify-end">
                      <span className="text-[11px] text-red">Remove?</span>
                      <button
                        onClick={() => handleDelete(skill.key)}
                        disabled={deleting === skill.key}
                        className="px-2 py-0.5 text-[11px] font-semibold rounded bg-red text-white hover:opacity-90 disabled:opacity-40 transition-opacity"
                      >
                        {deleting === skill.key ? '…' : 'Yes'}
                      </button>
                      <button onClick={() => setConfirmDel(null)}
                        className="px-2 py-0.5 text-[11px] rounded border border-border-strong text-muted hover:text-dark transition-colors">
                        No
                      </button>
                    </span>
                  ) : (
                    <span className="inline-flex gap-1.5 justify-end">
                      <button onClick={() => openEdit(skill)}
                        className="px-2 py-0.5 text-[11px] font-semibold rounded border border-border-strong text-muted hover:text-dark transition-colors">
                        {t('routingSkills.edit')}
                      </button>
                      <button onClick={() => setConfirmDel(skill.key)}
                        className="px-2 py-0.5 text-[11px] rounded border border-red/30 text-red hover:bg-red-light transition-colors">
                        Remove
                      </button>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
