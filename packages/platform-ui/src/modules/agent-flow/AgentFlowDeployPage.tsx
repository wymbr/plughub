/**
 * AgentFlowDeployPage — /agent-flow/deploy
 *
 * Pool-centric 3-slot deploy lifecycle.
 * Spec: Task #31 revised
 *
 * Left panel  : pool list
 * Right panel :
 *   - 3-slot panel (Anterior / Corrente / Próxima)
 *     • Anterior + Corrente: read-only (imutável após promoção)
 *     • Próxima: editável — seleciona skill-flow + preenche config via interface_schema
 *       Botão "Copiar do Corrente": pré-preenche campos coincidentes, marca novos com badge
 *   - Promover / Rollback com confirmação
 *
 * Rules enforced in UI:
 *   - Only "next" slot has an Edit button
 *   - Config form is read-only for current/previous
 *   - Copy-from-current: intersection merge, new fields highlighted, dropped fields silently ignored
 *   - On rollback: previous slot's skill + config restored as-is (no schema revalidation)
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'

// ── Types ──────────────────────────────────────────────────────────────────────

interface Pool {
  pool_id:        string
  description?:   string
  channel_types?: string[]
  status?:        string
}

interface Skill {
  skill_id:         string
  name?:            string
  version?:         string
  description?:     string
  classification?:  Record<string, unknown>
  /** Computed server-side: "workflow" if flow has suspend/collect steps; "agent" otherwise. */
  flow_model?:      'agent' | 'workflow'
  folder?:          string   // optional view-only grouping path, e.g. "project/sub"
  interface?:       InterfaceSchema | null  // interface_schema exposed as "interface"
}

interface InterfaceSchema {
  type?:       string
  properties?: Record<string, FieldSchema>
  required?:   string[]
}

interface FieldSchema {
  type?:        string
  description?: string
  default?:     unknown
  enum?:        unknown[]
  items?:       FieldSchema
  minimum?:     number
  maximum?:     number
}

interface SlotData {
  slot:          string
  set:           boolean
  skill_id?:     string | null
  config_json?:  Record<string, unknown>
  yaml_snapshot?: unknown
  set_at?:       string
  set_by?:       string
}

interface SlotsResponse {
  pool_id: string
  slots: { previous: SlotData; current: SlotData; next: SlotData }
}

// ── API ────────────────────────────────────────────────────────────────────────

function _h(tenantId: string, token?: string | null): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json', 'x-tenant-id': tenantId }
  if (token) h['Authorization'] = `Bearer ${token}`
  return h
}

async function apiFetchPools(tenantId: string, token?: string | null): Promise<Pool[]> {
  const res = await fetch('/v1/pools', { headers: _h(tenantId, token) })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const body = await res.json()
  return Array.isArray(body) ? body : (body.pools ?? body.data ?? [])
}

async function apiFetchSkills(tenantId: string, token?: string | null): Promise<Skill[]> {
  const res = await fetch('/v1/skills', { headers: _h(tenantId, token) })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const body = await res.json()
  return Array.isArray(body) ? body : (body.skills ?? body.data ?? [])
}

async function apiFetchSlots(poolId: string, tenantId: string, token?: string | null): Promise<SlotsResponse> {
  const res = await fetch(`/v1/pools/${poolId}/slots`, { headers: _h(tenantId, token) })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

async function apiSetNextSlot(
  poolId: string, tenantId: string, token: string | null | undefined,
  payload: { skill_id: string; config_json: Record<string, unknown> },
): Promise<SlotData> {
  const res = await fetch(`/v1/pools/${poolId}/slots/next`, {
    method: 'PUT', headers: _h(tenantId, token), body: JSON.stringify(payload),
  })
  if (!res.ok) { const t = await res.text(); throw new Error(`HTTP ${res.status}: ${t}`) }
  return res.json()
}

async function apiPromote(poolId: string, tenantId: string, token?: string | null): Promise<SlotsResponse> {
  const res = await fetch(`/v1/pools/${poolId}/promote`, {
    method: 'POST', headers: _h(tenantId, token),
  })
  if (!res.ok) { const t = await res.text(); throw new Error(`HTTP ${res.status}: ${t}`) }
  return res.json()
}

async function apiRollback(poolId: string, tenantId: string, token?: string | null): Promise<SlotsResponse> {
  const res = await fetch(`/v1/pools/${poolId}/rollback`, {
    method: 'POST', headers: _h(tenantId, token),
  })
  if (!res.ok) { const t = await res.text(); throw new Error(`HTTP ${res.status}: ${t}`) }
  return res.json()
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function fmtDateShort(iso?: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })
}

const SLOT_LABEL: Record<string, string> = { previous: 'Anterior', current: 'Corrente', next: 'Próxima' }
const SLOT_DESC:  Record<string, string> = {
  previous: 'Alvo seguro de rollback — somente leitura',
  current:  'Versão em produção — somente leitura',
  next:     'Candidata preparada pelo desenvolvedor',
}
const SLOT_COLOR: Record<string, string> = { previous: '#94a3b8', current: '#22c55e', next: '#3b82f6' }
const SLOT_BG:    Record<string, string> = { previous: '#1c2535', current: '#0f2818', next: '#0c1b35' }

// ── Config form: render fields from interface_schema ──────────────────────────

interface ConfigFormProps {
  schema:      InterfaceSchema | null | undefined
  values:      Record<string, unknown>
  onChange:    (key: string, value: unknown) => void
  readOnly:    boolean
  newFields?:  Set<string>   // fields added in this version (highlighted)
}

function ConfigForm({ schema, values, onChange, readOnly, newFields }: ConfigFormProps) {
  if (!schema?.properties || Object.keys(schema.properties).length === 0) {
    return (
      <div style={{ fontSize: 12, color: '#475569', fontStyle: 'italic', padding: '8px 0' }}>
        Esta skill não define parâmetros de configuração (interface_schema vazio).
      </div>
    )
  }

  const required = new Set(schema.required ?? [])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {Object.entries(schema.properties).map(([key, field]) => {
        const isNew      = newFields?.has(key)
        const isRequired = required.has(key)
        const value      = values[key]

        return (
          <div key={key}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
              <label style={{ fontSize: 12, color: '#94a3b8', fontWeight: 500 }}>
                {key}
                {isRequired && <span style={{ color: '#f87171', marginLeft: 2 }}>*</span>}
              </label>
              {isNew && (
                <span style={{ fontSize: 10, padding: '1px 5px', borderRadius: 3, background: '#1d4ed822', color: '#60a5fa', border: '1px solid #1d4ed844' }}>
                  novo
                </span>
              )}
              {field.description && (
                <span style={{ fontSize: 11, color: '#475569' }}>— {field.description}</span>
              )}
            </div>

            {field.enum ? (
              <select
                value={String(value ?? '')}
                disabled={readOnly}
                onChange={e => onChange(key, e.target.value)}
                style={_inputStyle(readOnly)}
              >
                <option value="">Selecione…</option>
                {field.enum.map(opt => (
                  <option key={String(opt)} value={String(opt)}>{String(opt)}</option>
                ))}
              </select>
            ) : field.type === 'boolean' ? (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <input
                  type="checkbox"
                  checked={Boolean(value)}
                  disabled={readOnly}
                  onChange={e => onChange(key, e.target.checked)}
                  style={{ accentColor: '#3b82f6', width: 14, height: 14 }}
                />
                <span style={{ fontSize: 12, color: readOnly ? '#475569' : '#94a3b8' }}>
                  {Boolean(value) ? 'true' : 'false'}
                </span>
              </div>
            ) : field.type === 'integer' || field.type === 'number' ? (
              <input
                type="number"
                value={value != null ? String(value) : ''}
                disabled={readOnly}
                min={field.minimum}
                max={field.maximum}
                onChange={e => onChange(key, field.type === 'integer' ? parseInt(e.target.value, 10) : parseFloat(e.target.value))}
                style={_inputStyle(readOnly)}
              />
            ) : field.type === 'array' || field.type === 'object' ? (
              <textarea
                value={value != null ? JSON.stringify(value, null, 2) : ''}
                disabled={readOnly}
                rows={3}
                onChange={e => {
                  try { onChange(key, JSON.parse(e.target.value)) } catch { onChange(key, e.target.value) }
                }}
                style={{ ..._inputStyle(readOnly), fontFamily: 'monospace', fontSize: 11, resize: 'vertical' }}
              />
            ) : (
              <input
                type="text"
                value={value != null ? String(value) : ''}
                disabled={readOnly}
                onChange={e => onChange(key, e.target.value)}
                style={_inputStyle(readOnly)}
              />
            )}
          </div>
        )
      })}
    </div>
  )
}

function _inputStyle(readOnly: boolean): React.CSSProperties {
  return {
    width: '100%', boxSizing: 'border-box',
    background: readOnly ? '#0a0f1a' : '#0a1628',
    border: `1px solid ${readOnly ? '#1e293b' : '#1e3a5f'}`,
    borderRadius: 5, padding: '6px 10px', fontSize: 12,
    color: readOnly ? '#475569' : '#e2e8f0',
    outline: 'none', cursor: readOnly ? 'not-allowed' : 'text',
  }
}

// ── Slot card ──────────────────────────────────────────────────────────────────

interface SlotCardProps {
  slotName:   string
  data:       SlotData
  skill?:     Skill | null
  onEdit?:    () => void   // only for "next"
}

function SlotCard({ slotName, data, skill, onEdit }: SlotCardProps) {
  const color = SLOT_COLOR[slotName] ?? '#94a3b8'
  const bg    = SLOT_BG[slotName]   ?? '#1e293b'
  const isEditable = slotName === 'next'

  return (
    <div style={{
      background: bg, borderRadius: 8, border: `1px solid ${color}33`,
      borderTop: `3px solid ${color}`, padding: '14px 16px',
      display: 'flex', flexDirection: 'column', gap: 10, minHeight: 180,
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color }}>{SLOT_LABEL[slotName]}</div>
          <div style={{ fontSize: 11, color: '#475569', marginTop: 2 }}>{SLOT_DESC[slotName]}</div>
        </div>
        {isEditable && onEdit && (
          <button onClick={onEdit}
            style={{ padding: '4px 10px', borderRadius: 4, fontSize: 11, background: '#1e3a5f', color: '#93c5fd', border: '1px solid #1d4ed822', cursor: 'pointer', flexShrink: 0 }}>
            ✏ Editar
          </button>
        )}
        {!isEditable && data.set && (
          <span style={{ fontSize: 10, color: '#334155', flexShrink: 0 }}>🔒 somente leitura</span>
        )}
      </div>

      {!data.set ? (
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#334155', fontSize: 12 }}>
          — vazio —
        </div>
      ) : (
        <div style={{ fontSize: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div>
            <span style={{ color: '#475569' }}>Skill: </span>
            <code style={{ color: color, fontSize: 11 }}>{data.skill_id ?? '—'}</code>
            {skill?.name && <span style={{ color: '#475569', marginLeft: 6 }}>({skill.name})</span>}
          </div>

          {data.config_json && Object.keys(data.config_json).length > 0 && (
            <div>
              <div style={{ color: '#475569', fontSize: 11, marginBottom: 4 }}>Configuração:</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                {Object.entries(data.config_json).slice(0, 5).map(([k, v]) => (
                  <div key={k} style={{ display: 'flex', gap: 8 }}>
                    <span style={{ color: '#475569', minWidth: 80, fontSize: 11 }}>{k}</span>
                    <span style={{ color: '#64748b', fontSize: 11, wordBreak: 'break-all' }}>
                      {typeof v === 'object' ? JSON.stringify(v) : String(v ?? '—')}
                    </span>
                  </div>
                ))}
                {Object.keys(data.config_json).length > 5 && (
                  <div style={{ color: '#334155', fontSize: 10 }}>
                    +{Object.keys(data.config_json).length - 5} campo(s)…
                  </div>
                )}
              </div>
            </div>
          )}

          {data.set_at && (
            <div style={{ color: '#334155', fontSize: 10, marginTop: 4 }}>
              Definido por <span style={{ color: '#475569' }}>{data.set_by ?? '?'}</span>{' '}
              em {fmtDateShort(data.set_at)}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Skill grouping for select ─────────────────────────────────────────────────

interface SkillGroup {
  label:  string    // optgroup label
  skills: Skill[]
}

/** Groups skills into optgroup sections: root type → folder path (flat, no nesting in HTML) */
function groupSkillsForSelect(skills: Skill[]): SkillGroup[] {
  const agents    = skills.filter(s => s.flow_model !== 'workflow')
  const workflows = skills.filter(s => s.flow_model === 'workflow')

  const groups: SkillGroup[] = []

  for (const [rootLabel, list] of [['Agentes', agents], ['Workflows', workflows]] as [string, Skill[]][]) {
    if (list.length === 0) continue

    // Collect skills by folder path (max 2 levels)
    const byFolder = new Map<string, Skill[]>()  // '' = unfiled
    for (const s of list) {
      const raw    = s.folder?.trim().replace(/^\/+|\/+$/g, '') ?? ''
      const folder = raw ? raw.split('/').slice(0, 2).join('/') : ''
      if (!byFolder.has(folder)) byFolder.set(folder, [])
      byFolder.get(folder)!.push(s)
    }

    // Emit unfiled first, then folders sorted alphabetically
    const unfiled = byFolder.get('') ?? []
    if (unfiled.length > 0) groups.push({ label: rootLabel, skills: unfiled })

    const folders = Array.from(byFolder.keys()).filter(k => k !== '').sort()
    for (const folder of folders) {
      groups.push({ label: `${rootLabel} / ${folder}`, skills: byFolder.get(folder)! })
    }
  }

  return groups
}

// ── Next slot edit panel ───────────────────────────────────────────────────────

interface NextSlotEditorProps {
  poolId:       string
  tenantId:     string
  skills:       Skill[]
  currentSlot:  SlotData
  existingNext: SlotData
  onSave:       (payload: { skill_id: string; config_json: Record<string, unknown> }) => Promise<void>
  onClose:      () => void
  saving:       boolean
  saveError:    string | null
}

function NextSlotEditor({
  skills, currentSlot, existingNext, onSave, onClose, saving, saveError,
}: NextSlotEditorProps) {
  const [selectedSkillId, setSelectedSkillId] = useState<string>(existingNext.skill_id ?? '')
  const [configValues,    setConfigValues]    = useState<Record<string, unknown>>(existingNext.config_json ?? {})
  const [newFields,       setNewFields]       = useState<Set<string>>(new Set())

  const selectedSkill = skills.find(s => s.skill_id === selectedSkillId) ?? null
  const schema        = selectedSkill?.interface ?? null

  // When skill selection changes: reset config to defaults from schema
  const handleSkillChange = (skillId: string) => {
    setSelectedSkillId(skillId)
    setNewFields(new Set())
    const sk = skills.find(s => s.skill_id === skillId)
    if (sk?.interface?.properties) {
      const defaults: Record<string, unknown> = {}
      for (const [key, field] of Object.entries(sk.interface.properties)) {
        defaults[key] = field.default ?? (field.type === 'boolean' ? false : field.type === 'integer' || field.type === 'number' ? 0 : '')
      }
      setConfigValues(defaults)
    } else {
      setConfigValues({})
    }
  }

  // Copy from current slot: intersection merge with new fields highlighted
  const handleCopyFromCurrent = () => {
    if (!currentSlot.set || !currentSlot.config_json) return
    const schemaProps = schema?.properties ?? {}
    const currentConfig = currentSlot.config_json

    const merged: Record<string, unknown>  = { ...configValues }
    const detected = new Set<string>()

    for (const key of Object.keys(schemaProps)) {
      if (key in currentConfig) {
        merged[key] = currentConfig[key]        // copy matching field
      } else {
        detected.add(key)                        // new field — not in current
      }
      // fields in currentConfig but NOT in schemaProps are silently dropped
    }

    setConfigValues(merged)
    setNewFields(detected)
  }

  const handleFieldChange = (key: string, value: unknown) => {
    setConfigValues(prev => ({ ...prev, [key]: value }))
  }

  const handleSave = async () => {
    if (!selectedSkillId) { alert('Selecione uma skill-flow'); return }
    await onSave({ skill_id: selectedSkillId, config_json: configValues })
  }

  const hasCurrent = currentSlot.set && !!currentSlot.config_json

  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,.75)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 999,
    }}>
      <div style={{
        background: '#0f172a', border: '1px solid #1d4ed844', borderRadius: 10,
        width: 580, maxHeight: '88vh', overflow: 'auto', padding: 28,
        display: 'flex', flexDirection: 'column', gap: 18,
      }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ width: 10, height: 10, borderRadius: '50%', background: '#3b82f6' }} />
          <span style={{ fontSize: 15, fontWeight: 700, color: '#f1f5f9' }}>
            Configurar slot Próxima
          </span>
        </div>

        {/* Skill selector */}
        <div>
          <label style={{ fontSize: 11, fontWeight: 600, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: 6 }}>
            Skill-flow
          </label>
          <select
            value={selectedSkillId}
            onChange={e => handleSkillChange(e.target.value)}
            style={{ width: '100%', background: '#1e293b', border: '1px solid #334155', borderRadius: 6, padding: '8px 10px', fontSize: 12, color: '#e2e8f0', outline: 'none', boxSizing: 'border-box' }}
          >
            <option value="">Selecione uma skill-flow…</option>
            {groupSkillsForSelect(skills).map(group => (
              <optgroup key={group.label} label={group.label}>
                {group.skills.map(s => (
                  <option key={s.skill_id} value={s.skill_id}>
                    {s.skill_id}{s.name ? ` — ${s.name}` : ''}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
          {selectedSkill?.description && (
            <div style={{ fontSize: 11, color: '#475569', marginTop: 4 }}>{selectedSkill.description}</div>
          )}
        </div>

        {/* Config form */}
        {selectedSkillId && (
          <div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
              <label style={{ fontSize: 11, fontWeight: 600, color: '#64748b', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Configuração
              </label>
              {hasCurrent && (
                <button onClick={handleCopyFromCurrent}
                  style={{ fontSize: 11, padding: '4px 10px', borderRadius: 4, background: '#1e293b', color: '#60a5fa', border: '1px solid #334155', cursor: 'pointer' }}>
                  ⬇ Copiar do Corrente
                </button>
              )}
            </div>

            {newFields.size > 0 && (
              <div style={{ marginBottom: 10, padding: '6px 10px', background: '#1d4ed811', border: '1px solid #1d4ed833', borderRadius: 4, fontSize: 11, color: '#60a5fa' }}>
                {newFields.size} campo(s) novo(s) nesta versão — marcados com <strong>novo</strong>. Campos removidos foram descartados.
              </div>
            )}

            <ConfigForm
              schema={schema}
              values={configValues}
              onChange={handleFieldChange}
              readOnly={false}
              newFields={newFields}
            />
          </div>
        )}

        {saveError && (
          <div style={{ padding: '8px 12px', background: '#7f1d1d', color: '#fca5a5', borderRadius: 4, fontSize: 12 }}>
            {saveError}
          </div>
        )}

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
          <button onClick={onClose} disabled={saving}
            style={{ padding: '8px 20px', borderRadius: 6, fontSize: 13, background: '#1e293b', color: '#94a3b8', border: '1px solid #334155', cursor: 'pointer' }}>
            Cancelar
          </button>
          <button onClick={handleSave} disabled={saving || !selectedSkillId}
            style={{
              padding: '8px 22px', borderRadius: 6, fontSize: 13, fontWeight: 600, border: 'none',
              background: saving || !selectedSkillId ? '#1e3a5f' : '#1d4ed8',
              color:      saving || !selectedSkillId ? '#334155' : '#fff',
              cursor:     saving || !selectedSkillId ? 'not-allowed' : 'pointer',
            }}>
            {saving ? '⟳ Salvando…' : 'Salvar slot Próxima'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Confirm modal ──────────────────────────────────────────────────────────────

function ConfirmModal({ title, message, confirmLabel, confirmColor = '#1d4ed8', onConfirm, onCancel, running, error }: {
  title: string; message: React.ReactNode; confirmLabel: string; confirmColor?: string
  onConfirm: () => void; onCancel: () => void; running: boolean; error: string | null
}) {
  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 999 }}>
      <div style={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 10, width: 420, padding: 28 }}>
        <div style={{ fontSize: 16, fontWeight: 700, color: '#f1f5f9', marginBottom: 10 }}>{title}</div>
        <div style={{ fontSize: 13, color: '#94a3b8', marginBottom: 20 }}>{message}</div>
        {error && <div style={{ padding: '8px 12px', background: '#7f1d1d', color: '#fca5a5', borderRadius: 4, fontSize: 12, marginBottom: 16 }}>{error}</div>}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10 }}>
          <button onClick={onCancel} disabled={running}
            style={{ padding: '8px 20px', borderRadius: 6, fontSize: 13, background: '#1e293b', color: '#94a3b8', border: '1px solid #334155', cursor: 'pointer' }}>
            Cancelar
          </button>
          <button onClick={onConfirm} disabled={running}
            style={{ padding: '8px 20px', borderRadius: 6, fontSize: 13, fontWeight: 600, background: running ? '#334155' : confirmColor, color: running ? '#64748b' : '#fff', border: 'none', cursor: running ? 'not-allowed' : 'pointer' }}>
            {running ? '⟳ Aguarde…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Role helpers ───────────────────────────────────────────────────────────────

/** Can prepare the "next" slot — developer + admin */
function hasEditRole(roles: string[]): boolean {
  return roles.some(r => r === 'developer' || r === 'admin')
}

/** Can promote / rollback — operator, supervisor + admin */
function hasOperateRole(roles: string[]): boolean {
  return roles.some(r => r === 'operator' || r === 'supervisor' || r === 'admin')
}

const ROLE_BANNER: Record<string, { label: string; color: string; bg: string; border: string }> = {
  developer: {
    label:  'Você pode configurar o slot Próxima. Apenas operadores e supervisores podem promover ou reverter.',
    color:  '#93c5fd', bg: '#0c1b35', border: '#1d4ed844',
  },
  operator: {
    label:  'Você pode promover e reverter deploys. Apenas desenvolvedores podem configurar o slot Próxima.',
    color:  '#86efac', bg: '#0a1f10', border: '#16a34a44',
  },
  supervisor: {
    label:  'Você pode promover e reverter deploys. Apenas desenvolvedores podem configurar o slot Próxima.',
    color:  '#86efac', bg: '#0a1f10', border: '#16a34a44',
  },
}

// ── Main page ──────────────────────────────────────────────────────────────────

export default function AgentFlowDeployPage() {
  const { getAccessToken, tenantId, session } = useAuth()

  // Derived role capabilities — based on all roles the user holds
  const roles      = session?.roles ?? []
  const canEdit    = hasEditRole(roles)
  const canOperate = hasOperateRole(roles)

  // Banner: pick the most specific role descriptor (developer > supervisor > operator)
  const bannerRole = roles.includes('developer') ? 'developer'
    : roles.includes('supervisor')               ? 'supervisor'
    : roles.includes('operator')                 ? 'operator'
    : null
  // Admin has both capabilities — no banner needed
  const showBanner = bannerRole !== null && !(canEdit && canOperate)

  const [pools,        setPools]        = useState<Pool[]>([])
  const [skills,       setSkills]       = useState<Skill[]>([])
  const [filter,       setFilter]       = useState('')
  const [selected,     setSelected]     = useState<Pool | null>(null)
  const [slots,        setSlots]        = useState<SlotsResponse | null>(null)
  const [slotsLoading, setSlotsLoading] = useState(false)
  const [slotsError,   setSlotsError]   = useState<string | null>(null)

  const [pageLoading, setPageLoading] = useState(true)
  const [pageError,   setPageError]   = useState<string | null>(null)

  // Edit next slot
  const [editing,   setEditing]   = useState(false)
  const [saving,    setSaving]    = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  // Promote / rollback
  const [confirmAction, setConfirmAction] = useState<'promote' | 'rollback' | null>(null)
  const [actionRunning, setActionRunning] = useState(false)
  const [actionError,   setActionError]   = useState<string | null>(null)

  // Load master data
  useEffect(() => {
    if (!tenantId) return
    ;(async () => {
      try {
        const token = await getAccessToken()
        const [pl, sk] = await Promise.all([
          apiFetchPools(tenantId, token),
          apiFetchSkills(tenantId, token),
        ])
        setPools(pl)
        setSkills(sk)
      } catch (e) {
        setPageError(e instanceof Error ? e.message : String(e))
      } finally {
        setPageLoading(false)
      }
    })()
  }, [tenantId, getAccessToken])

  const loadSlots = useCallback(async (pool: Pool) => {
    if (!tenantId) return
    setSlotsLoading(true); setSlotsError(null)
    try {
      const token = await getAccessToken()
      const sl = await apiFetchSlots(pool.pool_id, tenantId, token)
      setSlots(sl)
    } catch (e) {
      setSlotsError(e instanceof Error ? e.message : String(e))
    } finally {
      setSlotsLoading(false)
    }
  }, [tenantId, getAccessToken])

  const handleSelectPool = (pool: Pool) => {
    setSelected(pool); setSlots(null); setSlotsError(null); setActionError(null)
    loadSlots(pool)
  }

  // Save next slot
  const handleSaveNext = async (payload: { skill_id: string; config_json: Record<string, unknown> }) => {
    if (!selected || !tenantId) return
    setSaving(true); setSaveError(null)
    try {
      const token = await getAccessToken()
      await apiSetNextSlot(selected.pool_id, tenantId, token, payload)
      const updated = await apiFetchSlots(selected.pool_id, tenantId, token)
      setSlots(updated)
      setEditing(false)
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  // Promote / rollback
  const handleConfirmAction = async () => {
    if (!selected || !tenantId || !confirmAction) return
    setActionRunning(true); setActionError(null)
    try {
      const token = await getAccessToken()
      const result = confirmAction === 'promote'
        ? await apiPromote(selected.pool_id, tenantId, token)
        : await apiRollback(selected.pool_id, tenantId, token)
      setSlots(result)
      setConfirmAction(null)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e))
    } finally {
      setActionRunning(false)
    }
  }

  const filteredPools = pools.filter(p => {
    if (!filter.trim()) return true
    const q = filter.toLowerCase()
    return p.pool_id.toLowerCase().includes(q) || (p.description ?? '').toLowerCase().includes(q)
  })

  const canPromote  = slots?.slots.next.set     === true
  const canRollback = slots?.slots.previous.set === true

  // Resolve skill names for slot cards
  const skillMap = Object.fromEntries(skills.map(s => [s.skill_id, s]))

  if (!tenantId) return (
    <div className="flex items-center justify-center h-full text-gray-400 text-sm">Nenhum tenant selecionado.</div>
  )
  if (pageLoading) return (
    <div className="flex items-center justify-center h-full"><Spinner /></div>
  )
  if (pageError) return (
    <div className="flex items-center justify-center h-full text-red-400 text-sm">Erro: {pageError}</div>
  )

  return (
    <div style={{ display: 'flex', height: '100%', overflow: 'hidden', background: '#0a1628', color: '#e2e8f0' }}>

      {/* ─── Left: pool list ───────────────────────────────────────────── */}
      <div style={{ width: 260, flexShrink: 0, borderRight: '1px solid #1e293b', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ padding: '14px 16px 10px', borderBottom: '1px solid #1e293b', flexShrink: 0 }}>
          <div style={{ fontSize: 15, fontWeight: 700, color: '#f1f5f9', marginBottom: 10 }}>Deploy de Skills</div>
          <input
            value={filter}
            onChange={e => setFilter(e.target.value)}
            placeholder="Filtrar pools…"
            style={{ width: '100%', background: '#1e293b', border: '1px solid #334155', borderRadius: 6, padding: '6px 10px', fontSize: 12, color: '#e2e8f0', outline: 'none', boxSizing: 'border-box' }}
          />
        </div>

        <div style={{ flex: 1, overflowY: 'auto' }}>
          {filteredPools.length === 0 ? (
            <div style={{ padding: 24, textAlign: 'center', color: '#334155', fontSize: 12 }}>Nenhum pool encontrado</div>
          ) : filteredPools.map(pool => {
            const isActive = selected?.pool_id === pool.pool_id
            return (
              <button key={pool.pool_id} onClick={() => handleSelectPool(pool)}
                style={{
                  width: '100%', textAlign: 'left', padding: '10px 16px',
                  background: isActive ? '#1e3a5f' : 'transparent',
                  borderLeft: `3px solid ${isActive ? '#3b82f6' : 'transparent'}`,
                  borderBottom: '1px solid #1e293b', borderTop: 'none', borderRight: 'none',
                  cursor: 'pointer', transition: 'background .12s',
                }}>
                <code style={{ fontSize: 11, color: isActive ? '#93c5fd' : '#e2e8f0', display: 'block', wordBreak: 'break-all' }}>
                  {pool.pool_id}
                </code>
                {pool.description && (
                  <div style={{ fontSize: 11, color: '#475569', marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {pool.description}
                  </div>
                )}
                {pool.channel_types && pool.channel_types.length > 0 && (
                  <div style={{ fontSize: 10, color: '#334155', marginTop: 2 }}>
                    {pool.channel_types.join(' · ')}
                  </div>
                )}
              </button>
            )
          })}
        </div>
      </div>

      {/* ─── Right: slot panel ────────────────────────────────────────── */}
      <div style={{ flex: 1, overflow: 'auto', padding: 24, display: 'flex', flexDirection: 'column', gap: 20 }}>

        {/* Role permission banner */}
        {showBanner && bannerRole && (
          <div style={{
            padding: '8px 14px', borderRadius: 6, fontSize: 12,
            background: ROLE_BANNER[bannerRole].bg,
            color:      ROLE_BANNER[bannerRole].color,
            border:     `1px solid ${ROLE_BANNER[bannerRole].border}`,
            flexShrink: 0,
          }}>
            🔑 {ROLE_BANNER[bannerRole].label}
          </div>
        )}

        {!selected ? (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: '#334155', gap: 8 }}>
            <span style={{ fontSize: 32 }}>⚙</span>
            <span style={{ fontSize: 14 }}>Selecione um pool para gerenciar o deploy</span>
          </div>
        ) : (
          <>
            {/* Header */}
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
              <div>
                <code style={{ fontSize: 16, fontWeight: 700, color: '#93c5fd' }}>{selected.pool_id}</code>
                {selected.description && <div style={{ fontSize: 13, color: '#64748b', marginTop: 4 }}>{selected.description}</div>}
              </div>
              <button onClick={() => loadSlots(selected)}
                style={{ padding: '6px 14px', borderRadius: 6, fontSize: 12, background: '#1e293b', color: '#64748b', border: '1px solid #334155', cursor: 'pointer', flexShrink: 0 }}>
                {slotsLoading ? <Spinner /> : '↻ Atualizar'}
              </button>
            </div>

            {slotsError && (
              <div style={{ padding: '10px 14px', background: '#7f1d1d', color: '#fca5a5', borderRadius: 6, fontSize: 12 }}>
                Erro: {slotsError}
              </div>
            )}

            {/* Slots */}
            <div>
              <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: '#f1f5f9', marginBottom: 2 }}>Slots de versão</div>
                <div style={{ fontSize: 11, color: '#475569' }}>
                  Próxima → Corrente → Anterior &nbsp;·&nbsp;
                  Desenvolvedor configura Próxima; Operador promove ou reverte
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 14 }}>
                {(['previous', 'current', 'next'] as const).map(sn => {
                  const slotData  = slots?.slots[sn] ?? { slot: sn, set: false }
                  const slotSkill = slotData.skill_id ? (skillMap[slotData.skill_id] ?? null) : null
                  return (
                    <SlotCard
                      key={sn}
                      slotName={sn}
                      data={slotData}
                      skill={slotSkill}
                      onEdit={sn === 'next' && canEdit ? () => { setEditing(true); setSaveError(null) } : undefined}
                    />
                  )
                })}
              </div>

              {/* Action buttons */}
              <div style={{ display: 'flex', gap: 12, marginTop: 16, alignItems: 'center', flexWrap: 'wrap' }}>
                <button
                  onClick={() => { setConfirmAction('promote'); setActionError(null) }}
                  disabled={!canPromote || !canOperate || slotsLoading}
                  title={!canOperate ? 'Apenas operadores e supervisores podem promover' : undefined}
                  style={{
                    padding: '9px 22px', borderRadius: 6, fontSize: 13, fontWeight: 600, border: 'none',
                    background: canPromote && canOperate && !slotsLoading ? '#1d4ed8' : '#1e293b',
                    color:      canPromote && canOperate && !slotsLoading ? '#fff'    : '#334155',
                    cursor:     canPromote && canOperate && !slotsLoading ? 'pointer' : 'not-allowed', transition: 'all .12s',
                  }}>
                  ↑ Promover (Próxima → Corrente)
                </button>
                <button
                  onClick={() => { setConfirmAction('rollback'); setActionError(null) }}
                  disabled={!canRollback || !canOperate || slotsLoading}
                  title={!canOperate ? 'Apenas operadores e supervisores podem reverter' : undefined}
                  style={{
                    padding: '9px 22px', borderRadius: 6, fontSize: 13, fontWeight: 600, border: 'none',
                    background: canRollback && canOperate && !slotsLoading ? '#78350f' : '#1e293b',
                    color:      canRollback && canOperate && !slotsLoading ? '#fde68a' : '#334155',
                    cursor:     canRollback && canOperate && !slotsLoading ? 'pointer' : 'not-allowed', transition: 'all .12s',
                  }}>
                  ↩ Rollback (Anterior → Corrente)
                </button>
                {slots && (
                  <span style={{ fontSize: 12, color: '#334155' }}>
                    {!canOperate
                      ? 'Sem permissão para promover ou reverter — apenas operadores e supervisores.'
                      : (!canPromote ? 'Configure o slot "Próxima" para habilitar a promoção. ' : '')
                        + (!canRollback ? 'Sem slot "Anterior" para rollback.' : '')}
                  </span>
                )}
              </div>
            </div>
          </>
        )}
      </div>

      {/* ─── Modals ──────────────────────────────────────────────────── */}

      {editing && selected && slots && (
        <NextSlotEditor
          poolId={selected.pool_id}
          tenantId={tenantId}
          skills={skills}
          currentSlot={slots.slots.current}
          existingNext={slots.slots.next}
          onSave={handleSaveNext}
          onClose={() => setEditing(false)}
          saving={saving}
          saveError={saveError}
        />
      )}

      {confirmAction === 'promote' && (
        <ConfirmModal
          title="Promover versão"
          message={
            <span>
              O slot <strong style={{ color: '#3b82f6' }}>Próxima</strong> passará para{' '}
              <strong style={{ color: '#22c55e' }}>Corrente</strong>, e o atual{' '}
              <strong style={{ color: '#22c55e' }}>Corrente</strong> será arquivado em{' '}
              <strong style={{ color: '#94a3b8' }}>Anterior</strong>.
              <br /><br />
              Corrente e Anterior se tornarão imutáveis.
            </span>
          }
          confirmLabel="↑ Confirmar promoção"
          confirmColor="#1d4ed8"
          onConfirm={handleConfirmAction}
          onCancel={() => setConfirmAction(null)}
          running={actionRunning}
          error={actionError}
        />
      )}

      {confirmAction === 'rollback' && (
        <ConfirmModal
          title="Rollback de versão"
          message={
            <span>
              O slot <strong style={{ color: '#94a3b8' }}>Anterior</strong> será restaurado como{' '}
              <strong style={{ color: '#22c55e' }}>Corrente</strong>, com a skill e configuração exatas que estavam em produção anteriormente.
              <br /><br />
              O slot Anterior será removido após o rollback.
            </span>
          }
          confirmLabel="↩ Confirmar rollback"
          confirmColor="#78350f"
          onConfirm={handleConfirmAction}
          onCancel={() => setConfirmAction(null)}
          running={actionRunning}
          error={actionError}
        />
      )}
    </div>
  )
}
