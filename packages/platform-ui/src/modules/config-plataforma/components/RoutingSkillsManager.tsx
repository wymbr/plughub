/**
 * RoutingSkillsManager.tsx
 * CRUD de competency skills de roteamento — Config API namespace "routing".
 *
 * Cada entrada: key (string) + domain (intervalo numérico, ex: [0-9], [0-1])
 * Armazenada via PUT /config/routing/{key}  → value: { domain: "[0-9]" }
 *
 * Consultada por: PoolsPage (multiselect routing_skills) e criação de usuários.
 */
import React, { useState } from 'react'
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
  const { entries, loading, error, reload } = useNamespace(tenantId, 'routing')

  const [showForm,   setShowForm]   = useState(false)
  const [editKey,    setEditKey]    = useState<string | null>(null)  // null = new
  const [formKey,    setFormKey]    = useState('')
  const [formDomain, setFormDomain] = useState('[0-9]')
  const [customDomain, setCustomDomain] = useState('')
  const [saving,     setSaving]     = useState(false)
  const [deleting,   setDeleting]   = useState<string | null>(null)
  const [confirmDel, setConfirmDel] = useState<string | null>(null)
  const [formError,  setFormError]  = useState('')

  // Build list from entries
  const skills: SkillEntry[] = Object.entries(entries).map(([k, e]) => ({
    key:    k,
    domain: typeof e.value === 'object' && e.value !== null
      ? ((e.value as Record<string, unknown>).domain as string) ?? ''
      : String(e.value ?? ''),
  }))

  function openNew() {
    setEditKey(null)
    setFormKey('')
    setFormDomain('[0-9]')
    setCustomDomain('')
    setFormError('')
    setShowForm(true)
  }

  function openEdit(skill: SkillEntry) {
    setEditKey(skill.key)
    setFormKey(skill.key)
    const preset = DOMAIN_PRESETS.includes(skill.domain)
    setFormDomain(preset ? skill.domain : '__custom__')
    setCustomDomain(preset ? '' : skill.domain)
    setFormError('')
    setShowForm(true)
  }

  function closeForm() {
    setShowForm(false)
    setEditKey(null)
    setFormError('')
  }

  async function handleSave() {
    const key    = formKey.trim()
    const domain = formDomain === '__custom__' ? customDomain.trim() : formDomain

    if (!key)    { setFormError('Key é obrigatória'); return }
    if (!domain) { setFormError('Domain é obrigatório'); return }
    if (!/^\[[\d]+-[\d]+\]$/.test(domain) && !/^\[\d+\]$/.test(domain)) {
      setFormError('Domain deve estar no formato [min-max], ex: [0-9]')
      return
    }
    if (!adminToken) { setFormError('Admin token necessário (cabeçalho da página)'); return }

    setSaving(true)
    setFormError('')
    try {
      await putConfig('routing', key, { domain }, tenantId, adminToken)
      reload()
      closeForm()
    } catch (e) {
      setFormError(e instanceof Error ? e.message : 'Erro ao salvar')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(key: string) {
    if (!adminToken) { setFormError('Admin token necessário'); return }
    setDeleting(key)
    try {
      await deleteConfig('routing', key, tenantId, adminToken)
      reload()
      setConfirmDel(null)
    } catch { /* stale */ }
    finally { setDeleting(null) }
  }

  return (
    <div style={{ padding: '16px 24px', color: '#e2e8f0' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 700 }}>Competency Skills de Roteamento</div>
          <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>
            Skills de competência de agentes — usados para ordenação estática na entrada da fila.
          </div>
        </div>
        {!showForm && (
          <button onClick={openNew} style={btnStyle('#3b82f6')}>
            + Nova Skill
          </button>
        )}
      </div>

      {!adminToken && (
        <div style={{ background: '#451a03', border: '1px solid #92400e', borderRadius: 6, padding: '8px 12px', fontSize: 12, color: '#fbbf24', marginBottom: 12 }}>
          ⚠️ Informe o Admin Token no cabeçalho da página para editar.
        </div>
      )}

      {/* Form */}
      {showForm && (
        <div style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, padding: 16, marginBottom: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12, color: '#93c5fd' }}>
            {editKey ? `Editar: ${editKey}` : 'Nova Competency Skill'}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <div>
              <label style={labelStyle}>Key *</label>
              <input
                value={formKey}
                onChange={e => setFormKey(e.target.value)}
                disabled={!!editKey}
                placeholder="ex: ingles, retencao, crm"
                style={{ ...inputStyle, opacity: editKey ? 0.6 : 1 }}
              />
              <div style={{ fontSize: 11, color: '#64748b', marginTop: 3 }}>
                snake_case, sem espaços
              </div>
            </div>

            <div>
              <label style={labelStyle}>Domain *</label>
              <select
                value={formDomain}
                onChange={e => setFormDomain(e.target.value)}
                style={inputStyle}
              >
                {DOMAIN_PRESETS.map(d => (
                  <option key={d} value={d}>{d}</option>
                ))}
                <option value="__custom__">Personalizado…</option>
              </select>
              {formDomain === '__custom__' && (
                <input
                  value={customDomain}
                  onChange={e => setCustomDomain(e.target.value)}
                  placeholder="ex: [0-100]"
                  style={{ ...inputStyle, marginTop: 6 }}
                />
              )}
              <div style={{ fontSize: 11, color: '#64748b', marginTop: 3 }}>
                Intervalo de valores válidos para o agente
              </div>
            </div>
          </div>

          {formError && (
            <div style={{ marginTop: 10, fontSize: 12, color: '#f87171', background: '#450a0a', border: '1px solid #7f1d1d', borderRadius: 4, padding: '6px 10px' }}>
              {formError}
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
            <button onClick={handleSave} disabled={saving} style={btnStyle('#3b82f6')}>
              {saving ? 'Salvando…' : 'Salvar'}
            </button>
            <button onClick={closeForm} style={btnStyle('#475569')}>Cancelar</button>
          </div>
        </div>
      )}

      {/* Table */}
      {loading && <div style={{ fontSize: 13, color: '#64748b', padding: '12px 0' }}>Carregando…</div>}
      {error   && <div style={{ fontSize: 12, color: '#f87171', marginBottom: 8 }}>{error}</div>}

      {!loading && skills.length === 0 && !showForm && (
        <div style={{ textAlign: 'center', padding: '32px 0', color: '#475569', fontSize: 13 }}>
          Nenhuma competency skill cadastrada
        </div>
      )}

      {skills.length > 0 && (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #1e293b' }}>
              <th style={thStyle}>Key</th>
              <th style={thStyle}>Domain</th>
              <th style={{ ...thStyle, width: 120, textAlign: 'right' }}>Ações</th>
            </tr>
          </thead>
          <tbody>
            {skills.map(skill => (
              <tr key={skill.key} style={{ borderBottom: '1px solid #0f172a' }}>
                <td style={tdStyle}>
                  <code style={{ fontSize: 12, color: '#93c5fd', background: '#0f172a', padding: '2px 6px', borderRadius: 4 }}>
                    {skill.key}
                  </code>
                </td>
                <td style={tdStyle}>
                  <span style={{ fontSize: 12, color: '#34d399', fontFamily: 'monospace', background: '#022c22', padding: '2px 8px', borderRadius: 4, border: '1px solid #065f46' }}>
                    {skill.domain}
                  </span>
                </td>
                <td style={{ ...tdStyle, textAlign: 'right' }}>
                  {confirmDel === skill.key ? (
                    <span style={{ display: 'flex', gap: 6, justifyContent: 'flex-end', alignItems: 'center' }}>
                      <span style={{ fontSize: 11, color: '#f87171' }}>Remover?</span>
                      <button
                        onClick={() => handleDelete(skill.key)}
                        disabled={deleting === skill.key}
                        style={smallBtnStyle('#dc2626')}
                      >
                        {deleting === skill.key ? '…' : 'Sim'}
                      </button>
                      <button onClick={() => setConfirmDel(null)} style={smallBtnStyle('#475569')}>Não</button>
                    </span>
                  ) : (
                    <span style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                      <button onClick={() => openEdit(skill)} style={smallBtnStyle('#3b82f6')}>Editar</button>
                      <button onClick={() => setConfirmDel(skill.key)} style={smallBtnStyle('#475569')}>Remover</button>
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

// ── Styles ──────────────────────────────────────────────────────────────────────

const labelStyle: React.CSSProperties = {
  display: 'block', fontSize: 12, fontWeight: 600, color: '#94a3b8', marginBottom: 4,
}

const inputStyle: React.CSSProperties = {
  width: '100%', background: '#0f172a', border: '1px solid #334155', borderRadius: 6,
  color: '#e2e8f0', fontSize: 13, padding: '6px 10px', outline: 'none', boxSizing: 'border-box',
}

const thStyle: React.CSSProperties = {
  textAlign: 'left', padding: '8px 12px', fontSize: 11, fontWeight: 700,
  color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em',
}

const tdStyle: React.CSSProperties = {
  padding: '10px 12px', color: '#e2e8f0',
}

function btnStyle(bg: string): React.CSSProperties {
  return {
    padding: '6px 16px', fontSize: 13, fontWeight: 600, borderRadius: 6,
    border: 'none', background: bg, color: '#fff', cursor: 'pointer',
  }
}

function smallBtnStyle(bg: string): React.CSSProperties {
  return {
    padding: '3px 10px', fontSize: 11, fontWeight: 600, borderRadius: 4,
    border: 'none', background: bg, color: '#fff', cursor: 'pointer',
  }
}
