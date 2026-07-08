/**
 * AgentFlowDeployPage — /agent-flow/deploy
 *
 * Pool-centric 3-slot deploy lifecycle.
 *
 * Left panel  : pool list
 * Right panel :
 *   - 3-slot panel (Previous / Current / Next)
 *     • Previous + Current: read-only (immutable after promotion)
 *     • Next: editable — select skill-flow + fill config via interface_schema
 *       "Copy from Current": pre-fills matching fields, highlights new ones
 *   - Promote / Rollback with confirmation
 *
 * Rules enforced in UI:
 *   - Only "next" slot has an Edit button
 *   - Config form is read-only for current/previous
 *   - Copy-from-current: intersection merge, new fields highlighted, dropped fields silently ignored
 *   - On rollback: previous slot's skill + config restored as-is (no schema revalidation)
 */
import React, { useCallback, useEffect, useState } from 'react'
import { Settings } from 'lucide-react'
import { useTranslation } from 'react-i18next'
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
  folder?:          string
  interface?:       InterfaceSchema | null
  /** Deploy-time config params (canonical). Drives the deploy form; falls back to `interface.properties`. */
  config_params?:   ConfigParam[] | null
}

interface InterfaceSchema {
  type?:       string
  properties?: Record<string, FieldSchema>
  required?:   string[]
}

// SkillConfigParam mirror (see @plughub/schemas). `source` is an open string:
// known values render a system combo; unknown → text input (graceful fallback).
interface ConfigParamOption { value: string; label?: string }
interface ConfigParam {
  key:          string
  type?:        'string' | 'number' | 'boolean'
  label?:       string
  description?: string
  required?:    boolean
  default?:     unknown
  source?:      string
  options?:     ConfigParamOption[]
  min?:         number
  max?:         number
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
  slot:           string
  set:            boolean
  skill_id?:      string | null
  config_json?:   Record<string, unknown>
  yaml_snapshot?: unknown
  set_at?:        string
  set_by?:        string
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

/** Dialog forms — for the `source: "dialogforms"` config-param combo. */
async function apiFetchDialogForms(tenantId: string, token?: string | null): Promise<Array<{ form_id: string; name?: string }>> {
  const res = await fetch('/v1/dialog/forms', { headers: _h(tenantId, token) })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const body = await res.json()
  return Array.isArray(body) ? body : (body.forms ?? body.data ?? [])
}

// ── System sources (config-param combos) ─────────────────────────────────────────
// A config param's `source` hint is interpreted ONLY by the deploy UI: the parent
// builds a `sourceOptions` map keyed by the known sources (dialogforms/pools/skills)
// and ConfigForm renders a combo when it finds options for a param's source. An
// unknown source is not an error — the field degrades to a plain text input
// (forward-compat: a newer skill/schema may declare a source this build doesn't know).

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
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
}

/** Accent color per slot — semantic, data-driven — stays as inline style */
const SLOT_COLOR: Record<string, string> = {
  previous: '#94a3b8',
  current:  '#22c55e',
  next:     '#3b82f6',
}

/** Light-mode input style; readOnly uses muted surface */
function _inputStyle(readOnly: boolean): React.CSSProperties {
  return {
    width: '100%', boxSizing: 'border-box',
    background: readOnly ? '#f8fafc' : '#ffffff',
    border: `1px solid ${readOnly ? '#e2e8f0' : '#cbd5e1'}`,
    borderRadius: 5, padding: '6px 10px', fontSize: 12,
    color: readOnly ? '#94a3b8' : '#1e293b',
    outline: 'none', cursor: readOnly ? 'not-allowed' : 'text',
  }
}

// ── Config form ───────────────────────────────────────────────────────────────

interface ConfigFormProps {
  schema:        InterfaceSchema | null | undefined
  params?:       ConfigParam[] | null
  sourceOptions?: Record<string, ConfigParamOption[]>
  values:        Record<string, unknown>
  onChange:      (key: string, value: unknown) => void
  readOnly:      boolean
  newFields?:    Set<string>
}

/**
 * Canonical renderer: one field per `config_params` descriptor.
 * `source` known + options loaded → combo; static `options` → select; otherwise
 * an input by `type`. Unknown source silently degrades to a text input.
 */
function ConfigParamsForm({
  params, sourceOptions, values, onChange, readOnly, newFields,
}: {
  params: ConfigParam[]
  sourceOptions: Record<string, ConfigParamOption[]>
  values: Record<string, unknown>
  onChange: (key: string, value: unknown) => void
  readOnly: boolean
  newFields?: Set<string>
}) {
  const { t } = useTranslation('agentFlow')

  return (
    <div className="flex flex-col gap-2.5">
      {params.map(param => {
        const key   = param.key
        const value = values[key]
        const isNew = newFields?.has(key)
        const comboOptions = param.source ? sourceOptions[param.source] : undefined
        const useCombo     = Array.isArray(comboOptions) && comboOptions.length > 0
        const staticOptions = param.options

        return (
          <div key={key}>
            <div className="flex items-center gap-1.5 mb-1">
              <label className="text-xs font-medium text-muted">
                {param.label ?? key}
                {param.required && <span className="text-red ml-0.5">*</span>}
              </label>
              {isNew && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/20">
                  {t('deploy.newField')}
                </span>
              )}
              {param.description && (
                <span className="text-xs text-muted-light ml-1">— {param.description}</span>
              )}
            </div>

            {useCombo ? (
              <select
                value={String(value ?? '')}
                disabled={readOnly}
                onChange={e => onChange(key, e.target.value)}
                style={_inputStyle(readOnly)}
              >
                <option value="">{t('deploy.selectOption')}</option>
                {comboOptions!.map(opt => (
                  <option key={opt.value} value={opt.value}>{opt.label ?? opt.value}</option>
                ))}
              </select>
            ) : staticOptions && staticOptions.length > 0 ? (
              <select
                value={String(value ?? '')}
                disabled={readOnly}
                onChange={e => onChange(key, e.target.value)}
                style={_inputStyle(readOnly)}
              >
                <option value="">{t('deploy.selectOption')}</option>
                {staticOptions.map(opt => (
                  <option key={opt.value} value={opt.value}>{opt.label ?? opt.value}</option>
                ))}
              </select>
            ) : param.type === 'boolean' ? (
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={Boolean(value)}
                  disabled={readOnly}
                  onChange={e => onChange(key, e.target.checked)}
                  className="accent-primary w-3.5 h-3.5"
                />
                <span className={`text-xs ${readOnly ? 'text-muted' : 'text-muted-light'}`}>
                  {Boolean(value) ? 'true' : 'false'}
                </span>
              </div>
            ) : param.type === 'number' ? (
              <input
                type="number"
                value={value != null ? String(value) : ''}
                disabled={readOnly}
                min={param.min}
                max={param.max}
                onChange={e => onChange(key, parseFloat(e.target.value))}
                style={_inputStyle(readOnly)}
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

function ConfigForm({ schema, params, sourceOptions, values, onChange, readOnly, newFields }: ConfigFormProps) {
  const { t } = useTranslation('agentFlow')

  // Canonical path — config_params drives the form.
  if (params && params.length > 0) {
    return (
      <ConfigParamsForm
        params={params}
        sourceOptions={sourceOptions ?? {}}
        values={values}
        onChange={onChange}
        readOnly={readOnly}
        newFields={newFields}
      />
    )
  }

  // Legacy fallback — interface.properties (kept for skills without config_params).
  if (!schema?.properties || Object.keys(schema.properties).length === 0) {
    return (
      <p className="text-xs text-muted italic py-2">{t('deploy.noConfigParams')}</p>
    )
  }

  const required = new Set(schema.required ?? [])

  return (
    <div className="flex flex-col gap-2.5">
      {Object.entries(schema.properties).map(([key, field]) => {
        const isNew      = newFields?.has(key)
        const isRequired = required.has(key)
        const value      = values[key]

        return (
          <div key={key}>
            <div className="flex items-center gap-1.5 mb-1">
              <label className="text-xs font-medium text-muted">
                {key}
                {isRequired && <span className="text-red ml-0.5">*</span>}
              </label>
              {isNew && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/20">
                  {t('deploy.newField')}
                </span>
              )}
              {field.description && (
                <span className="text-xs text-muted-light ml-1">— {field.description}</span>
              )}
            </div>

            {field.enum ? (
              <select
                value={String(value ?? '')}
                disabled={readOnly}
                onChange={e => onChange(key, e.target.value)}
                style={_inputStyle(readOnly)}
              >
                <option value="">{t('deploy.selectOption')}</option>
                {field.enum.map(opt => (
                  <option key={String(opt)} value={String(opt)}>{String(opt)}</option>
                ))}
              </select>
            ) : field.type === 'boolean' ? (
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={Boolean(value)}
                  disabled={readOnly}
                  onChange={e => onChange(key, e.target.checked)}
                  className="accent-primary w-3.5 h-3.5"
                />
                <span className={`text-xs ${readOnly ? 'text-muted' : 'text-muted-light'}`}>
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

// ── Slot card ──────────────────────────────────────────────────────────────────

interface SlotCardProps {
  slotName:  string
  data:      SlotData
  skill?:    Skill | null
  onEdit?:   () => void
}

function SlotCard({ slotName, data, skill, onEdit }: SlotCardProps) {
  const { t } = useTranslation('agentFlow')
  const color      = SLOT_COLOR[slotName] ?? '#94a3b8'
  const isEditable = slotName === 'next'

  return (
    <div
      className="bg-white rounded-lg border border-border flex flex-col gap-2.5 p-4 min-h-[180px]"
      style={{ borderTop: `3px solid ${color}` }}
    >
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <div className="text-sm font-bold" style={{ color }}>{t(`deploy.slots.${slotName}`)}</div>
          <div className="text-xs text-muted mt-0.5">{t(`deploy.slots.${slotName}Desc`)}</div>
        </div>
        {isEditable && onEdit && (
          <button
            onClick={onEdit}
            className="shrink-0 px-2.5 py-1 text-xs rounded border border-primary/20 bg-primary/10 text-primary hover:bg-primary/20 transition-colors"
          >
            {t('deploy.editSlot')}
          </button>
        )}
        {!isEditable && data.set && (
          <span className="text-xs text-muted-light shrink-0">{t('deploy.slotReadOnly')}</span>
        )}
      </div>

      {/* Body */}
      {!data.set ? (
        <div className="flex-1 flex items-center justify-center text-xs text-muted-light italic">
          {t('deploy.emptySlot')}
        </div>
      ) : (
        <div className="text-xs flex flex-col gap-1.5">
          {/* Skill ID */}
          <div>
            <span className="text-muted">{t('deploy.skillLabel')}: </span>
            <code className="font-mono text-[11px]" style={{ color }}>{data.skill_id ?? '—'}</code>
            {skill?.name && <span className="text-muted ml-1.5">({skill.name})</span>}
          </div>

          {/* Config preview */}
          {data.config_json && Object.keys(data.config_json).length > 0 && (
            <div>
              <div className="text-muted text-[11px] mb-1">{t('deploy.configDisplay')}</div>
              <div className="flex flex-col gap-0.5">
                {Object.entries(data.config_json).slice(0, 5).map(([k, v]) => (
                  <div key={k} className="flex gap-2">
                    <span className="text-muted text-[11px] min-w-[80px] shrink-0">{k}</span>
                    <span className="text-muted-light text-[11px] font-mono break-all">
                      {typeof v === 'object' ? JSON.stringify(v) : String(v ?? '—')}
                    </span>
                  </div>
                ))}
                {Object.keys(data.config_json).length > 5 && (
                  <div className="text-muted-light text-[10px]">
                    {t('deploy.moreFields', { count: Object.keys(data.config_json).length - 5 })}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Set by */}
          {data.set_at && (
            <div className="text-muted-light text-[10px] mt-1">
              {t('deploy.setAt', { user: data.set_by ?? '?', date: fmtDateShort(data.set_at) })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Skill grouping for select ──────────────────────────────────────────────────

interface SkillGroup {
  label:  string
  skills: Skill[]
}

function groupSkillsForSelect(skills: Skill[], agentLabel: string, workflowLabel: string): SkillGroup[] {
  const agents    = skills.filter(s => s.flow_model !== 'workflow')
  const workflows = skills.filter(s => s.flow_model === 'workflow')

  const groups: SkillGroup[] = []

  for (const [rootLabel, list] of [[agentLabel, agents], [workflowLabel, workflows]] as [string, Skill[]][]) {
    if (list.length === 0) continue

    const byFolder = new Map<string, Skill[]>()
    for (const s of list) {
      const raw    = s.folder?.trim().replace(/^\/+|\/+$/g, '') ?? ''
      const folder = raw ? raw.split('/').slice(0, 2).join('/') : ''
      if (!byFolder.has(folder)) byFolder.set(folder, [])
      byFolder.get(folder)!.push(s)
    }

    const unfiled = byFolder.get('') ?? []
    if (unfiled.length > 0) groups.push({ label: rootLabel, skills: unfiled })

    const folders = Array.from(byFolder.keys()).filter(k => k !== '').sort()
    for (const folder of folders) {
      groups.push({ label: `${rootLabel} / ${folder}`, skills: byFolder.get(folder)! })
    }
  }

  return groups
}

// ── Next slot editor modal ────────────────────────────────────────────────────

interface NextSlotEditorProps {
  poolId:        string
  tenantId:      string
  skills:        Skill[]
  sourceOptions: Record<string, ConfigParamOption[]>
  currentSlot:   SlotData
  existingNext:  SlotData
  onSave:        (payload: { skill_id: string; config_json: Record<string, unknown> }) => Promise<void>
  onClose:       () => void
  saving:        boolean
  saveError:     string | null
}

function NextSlotEditor({
  skills, sourceOptions, currentSlot, existingNext, onSave, onClose, saving, saveError,
}: NextSlotEditorProps) {
  const { t } = useTranslation('agentFlow')
  const [selectedSkillId,       setSelectedSkillId]       = useState<string>(existingNext.skill_id ?? '')
  const [configValues,          setConfigValues]          = useState<Record<string, unknown>>(existingNext.config_json ?? {})
  const [newFields,             setNewFields]             = useState<Set<string>>(new Set())
  // max_concurrent_sessions is a platform-level field stored in config_json but managed
  // separately so it is not reset when the operator changes the skill-flow.
  const [maxConcurrentSessions, setMaxConcurrentSessions] = useState<number>(
    typeof existingNext.config_json?.max_concurrent_sessions === 'number'
      ? existingNext.config_json.max_concurrent_sessions
      : 1
  )

  const selectedSkill = skills.find(s => s.skill_id === selectedSkillId) ?? null
  const schema        = selectedSkill?.interface ?? null
  const params        = selectedSkill?.config_params ?? null

  /** Config field keys the selected skill declares — config_params first, else legacy properties. */
  const configKeysOf = (sk: Skill | null | undefined): string[] => {
    if (sk?.config_params && sk.config_params.length > 0) return sk.config_params.map(p => p.key)
    return Object.keys(sk?.interface?.properties ?? {})
  }

  const handleSkillChange = (skillId: string) => {
    setSelectedSkillId(skillId)
    setNewFields(new Set())
    const sk = skills.find(s => s.skill_id === skillId)
    const defaults: Record<string, unknown> = {}
    if (sk?.config_params && sk.config_params.length > 0) {
      for (const p of sk.config_params) {
        defaults[p.key] = p.default ?? (p.type === 'boolean' ? false : p.type === 'number' ? 0 : '')
      }
    } else if (sk?.interface?.properties) {
      for (const [key, field] of Object.entries(sk.interface.properties)) {
        defaults[key] = field.default ?? (field.type === 'boolean' ? false : field.type === 'integer' || field.type === 'number' ? 0 : '')
      }
    }
    setConfigValues(defaults)
  }

  const handleCopyFromCurrent = () => {
    if (!currentSlot.set || !currentSlot.config_json) return
    const currentConfig = currentSlot.config_json
    const merged: Record<string, unknown> = { ...configValues }
    const detected = new Set<string>()

    for (const key of configKeysOf(selectedSkill)) {
      if (key in currentConfig) {
        merged[key] = currentConfig[key]
      } else {
        detected.add(key)
      }
    }

    setConfigValues(merged)
    setNewFields(detected)
  }

  const handleFieldChange = (key: string, value: unknown) => {
    setConfigValues(prev => ({ ...prev, [key]: value }))
  }

  const handleSave = async () => {
    if (!selectedSkillId) { alert(t('deploy.selectSkillAlert')); return }
    await onSave({
      skill_id:    selectedSkillId,
      config_json: { ...configValues, max_concurrent_sessions: maxConcurrentSessions },
    })
  }

  const hasCurrent = currentSlot.set && !!currentSlot.config_json

  return (
    <div className="fixed inset-0 bg-black/75 flex items-center justify-center z-[999]">
      <div className="bg-white border border-border rounded-xl w-[580px] max-h-[88vh] overflow-auto p-7 flex flex-col gap-5">

        {/* Header */}
        <div className="flex items-center gap-2.5">
          <div className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: SLOT_COLOR.next }} />
          <span className="text-base font-bold text-dark">{t('deploy.configureNext')}</span>
        </div>

        {/* Skill selector */}
        <div>
          <label className="text-xs font-semibold text-muted uppercase tracking-wider block mb-1.5">
            {t('deploy.skillFlowLabel')}
          </label>
          <select
            value={selectedSkillId}
            onChange={e => handleSkillChange(e.target.value)}
            className="w-full bg-white border border-border-strong rounded-md px-3 py-2 text-xs text-dark outline-none focus:border-primary box-border"
          >
            <option value="">{t('deploy.selectSkillFlow')}</option>
            {groupSkillsForSelect(skills, t('editor.groups.agents'), t('editor.groups.workflows')).map(group => (
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
            <div className="text-xs text-muted mt-1">{selectedSkill.description}</div>
          )}
        </div>

        {/* Concurrent sessions — platform-level capacity field, always visible */}
        <div>
          <label className="text-xs font-semibold text-muted uppercase tracking-wider block mb-1.5">
            {t('deploy.concurrentSessionsLabel')}
          </label>
          <div className="flex items-center gap-3">
            <input
              type="number"
              min={1}
              max={500}
              value={maxConcurrentSessions}
              onChange={e => setMaxConcurrentSessions(Math.max(1, parseInt(e.target.value, 10) || 1))}
              style={{ width: 90, ..._inputStyle(false) }}
            />
            <span className="text-xs text-muted leading-snug" style={{ maxWidth: 380 }}>
              {t('deploy.concurrentSessionsDesc')}
            </span>
          </div>
        </div>

        {/* Skill-specific config form */}
        {selectedSkillId && (
          <div>
            <div className="flex items-center justify-between mb-2.5">
              <label className="text-xs font-semibold text-muted uppercase tracking-wider">
                {t('deploy.configSection')}
              </label>
              {hasCurrent && (
                <button
                  onClick={handleCopyFromCurrent}
                  className="text-xs px-2.5 py-1 rounded border border-border bg-surface-alt text-primary hover:bg-primary/5 transition-colors"
                >
                  {t('deploy.copyFromCurrent')}
                </button>
              )}
            </div>

            {newFields.size > 0 && (
              <div className="mb-2.5 px-3 py-1.5 bg-primary/5 border border-primary/20 rounded text-xs text-primary">
                {t('deploy.newFieldsNotice', { count: newFields.size })}
              </div>
            )}

            <ConfigForm
              schema={schema}
              params={params}
              sourceOptions={sourceOptions}
              values={configValues}
              onChange={handleFieldChange}
              readOnly={false}
              newFields={newFields}
            />
          </div>
        )}

        {saveError && (
          <div className="px-3 py-2 bg-red-light border border-red/30 rounded text-xs text-red-text">
            {saveError}
          </div>
        )}

        {/* Footer */}
        <div className="flex justify-end gap-2.5">
          <button
            onClick={onClose}
            disabled={saving}
            className="px-5 py-2 rounded-md text-sm bg-white border border-border text-muted hover:bg-surface-muted transition-colors disabled:opacity-50"
          >
            {t('deploy.cancel')}
          </button>
          <button
            onClick={handleSave}
            disabled={saving || !selectedSkillId}
            className={`px-5 py-2 rounded-md text-sm font-semibold transition-colors ${
              saving || !selectedSkillId
                ? 'bg-surface-alt text-muted-light cursor-not-allowed'
                : 'bg-primary text-white hover:bg-primary/90'
            }`}
          >
            {saving ? t('deploy.saving') : t('deploy.saveNextSlot')}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Confirm modal ──────────────────────────────────────────────────────────────

function ConfirmModal({
  title, message, confirmLabel, confirmColor = '#1B4F8A',
  onConfirm, onCancel, running, error,
}: {
  title:         string
  message:       React.ReactNode
  confirmLabel:  string
  confirmColor?: string
  onConfirm:     () => void
  onCancel:      () => void
  running:       boolean
  error:         string | null
}) {
  const { t } = useTranslation('agentFlow')

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-[999]">
      <div className="bg-white border border-border rounded-xl w-[420px] p-7 flex flex-col gap-4">
        <div className="text-base font-bold text-dark">{title}</div>
        <div className="text-sm text-muted leading-relaxed">{message}</div>
        {error && (
          <div className="px-3 py-2 bg-red-light border border-red/30 rounded text-xs text-red-text">
            {error}
          </div>
        )}
        <div className="flex justify-end gap-2.5 pt-1">
          <button
            onClick={onCancel}
            disabled={running}
            className="px-5 py-2 rounded-md text-sm bg-white border border-border text-muted hover:bg-surface-muted transition-colors disabled:opacity-50"
          >
            {t('deploy.cancel')}
          </button>
          <button
            onClick={onConfirm}
            disabled={running}
            className="px-5 py-2 rounded-md text-sm font-semibold text-white transition-colors disabled:opacity-50"
            style={{ background: running ? '#94a3b8' : confirmColor }}
          >
            {running ? t('deploy.waiting') : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Role helpers ───────────────────────────────────────────────────────────────

function hasEditRole(roles: string[]): boolean {
  return roles.some(r => r === 'developer' || r === 'admin')
}

function hasOperateRole(roles: string[]): boolean {
  return roles.some(r => r === 'operator' || r === 'supervisor' || r === 'admin')
}

// ── Main page ──────────────────────────────────────────────────────────────────

export default function AgentFlowDeployPage() {
  const { getAccessToken, tenantId, session } = useAuth()
  const { t } = useTranslation('agentFlow')

  const roles      = session?.roles ?? []
  const canEdit    = hasEditRole(roles)
  const canOperate = hasOperateRole(roles)

  const bannerRole = roles.includes('developer') ? 'developer'
    : roles.includes('supervisor')               ? 'supervisor'
    : roles.includes('operator')                 ? 'operator'
    : null
  const showBanner = bannerRole !== null && !(canEdit && canOperate)

  const [pools,        setPools]        = useState<Pool[]>([])
  const [skills,       setSkills]       = useState<Skill[]>([])
  const [dialogForms,  setDialogForms]  = useState<Array<{ form_id: string; name?: string }>>([])
  const [filter,       setFilter]       = useState('')
  const [selected,     setSelected]     = useState<Pool | null>(null)
  const [slots,        setSlots]        = useState<SlotsResponse | null>(null)
  const [slotsLoading, setSlotsLoading] = useState(false)
  const [slotsError,   setSlotsError]   = useState<string | null>(null)

  const [pageLoading, setPageLoading] = useState(true)
  const [pageError,   setPageError]   = useState<string | null>(null)

  const [editing,   setEditing]   = useState(false)
  const [saving,    setSaving]    = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const [confirmAction, setConfirmAction] = useState<'promote' | 'rollback' | null>(null)
  const [actionRunning, setActionRunning] = useState(false)
  const [actionError,   setActionError]   = useState<string | null>(null)

  useEffect(() => {
    if (!tenantId) return
    ;(async () => {
      try {
        const token = await getAccessToken()
        const [pl, sk, df] = await Promise.all([
          apiFetchPools(tenantId, token),
          apiFetchSkills(tenantId, token),
          apiFetchDialogForms(tenantId, token).catch(() => []),  // combos degrade gracefully if dialog-api is down
        ])
        setPools(pl)
        setSkills(sk)
        setDialogForms(df)
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
  const skillMap    = Object.fromEntries(skills.map(s => [s.skill_id, s]))

  // ── Early exits ────────────────────────────────────────────────────────────

  if (!tenantId) return (
    <div className="flex items-center justify-center h-full text-muted-light text-sm">
      {t('deploy.noTenant')}
    </div>
  )
  if (pageLoading) return (
    <div className="flex items-center justify-center h-full"><Spinner /></div>
  )
  if (pageError) return (
    <div className="flex items-center justify-center h-full text-red text-sm">
      {t('deploy.error')}: {pageError}
    </div>
  )

  // ── Slot labels (for modal messages) ──────────────────────────────────────

  const slotNext     = t('deploy.slots.next')
  const slotCurrent  = t('deploy.slots.current')
  const slotPrevious = t('deploy.slots.previous')

  return (
    <div className="flex h-full overflow-hidden bg-surface-muted text-dark">

      {/* ─── Left: pool list ──────────────────────────────────────────────── */}
      <div className="w-64 shrink-0 bg-white border-r border-border flex flex-col overflow-hidden">

        {/* Header (no title — breadcrumb handles it) */}
        <div className="px-4 py-3 border-b border-border shrink-0">
          <input
            value={filter}
            onChange={e => setFilter(e.target.value)}
            placeholder={t('deploy.filterPools')}
            className="w-full px-3 py-1.5 text-xs bg-white border border-border-strong rounded-md text-dark placeholder:text-muted focus:outline-none focus:border-primary box-border"
          />
        </div>

        {/* Pool list */}
        <div className="flex-1 overflow-y-auto">
          {filteredPools.length === 0 ? (
            <div className="px-6 py-6 text-center text-xs text-muted-light">{t('deploy.noPool')}</div>
          ) : filteredPools.map(pool => {
            const isActive = selected?.pool_id === pool.pool_id
            return (
              <button
                key={pool.pool_id}
                onClick={() => handleSelectPool(pool)}
                className="w-full text-left px-4 py-2.5 border-b border-border transition-colors hover:bg-primary/5"
                style={{
                  background:  isActive ? '#EBF2FA' : undefined,
                  borderLeft:  `3px solid ${isActive ? '#1B4F8A' : 'transparent'}`,
                }}
              >
                <code className={`text-xs block break-all ${isActive ? 'text-primary' : 'text-dark'}`}>
                  {pool.pool_id}
                </code>
                {pool.description && (
                  <div className="text-xs text-muted mt-0.5 truncate">{pool.description}</div>
                )}
                {pool.channel_types && pool.channel_types.length > 0 && (
                  <div className="text-[10px] text-muted-light mt-0.5">{pool.channel_types.join(' · ')}</div>
                )}
              </button>
            )
          })}
        </div>
      </div>

      {/* ─── Right: slot panel ────────────────────────────────────────────── */}
      <div className="flex-1 overflow-auto p-6 flex flex-col gap-5">

        {/* Role permission banner */}
        {showBanner && bannerRole && (
          <div className={`px-3.5 py-2 rounded-md text-xs border shrink-0 ${
            bannerRole === 'developer'
              ? 'bg-blue-50 border-blue-200 text-blue-700'
              : 'bg-green-50 border-green-200 text-green-700'
          }`}>
            🔑 {t(bannerRole === 'developer' ? 'deploy.bannerDeveloper' : 'deploy.bannerOperator')}
          </div>
        )}

        {/* Empty state */}
        {!selected ? (
          <div className="flex-1 flex flex-col items-center justify-center text-muted-light gap-2">
            <Settings className="w-8 h-8 text-muted" aria-hidden="true" />
            <span className="text-sm">{t('deploy.selectPool')}</span>
          </div>
        ) : (
          <>
            {/* Pool header */}
            <div className="flex items-start justify-between">
              <div>
                <code className="text-base font-bold text-primary">{selected.pool_id}</code>
                {selected.description && (
                  <div className="text-sm text-muted mt-1">{selected.description}</div>
                )}
              </div>
              <button
                onClick={() => loadSlots(selected)}
                className="shrink-0 px-3.5 py-1.5 rounded-md text-xs bg-white border border-border text-muted shadow-sm hover:bg-surface-muted transition-colors"
              >
                {slotsLoading ? <Spinner /> : t('actions.refresh')}
              </button>
            </div>

            {slotsError && (
              <div className="px-3.5 py-2.5 bg-red-light border border-red/30 rounded-md text-xs text-red-text">
                {t('deploy.error')}: {slotsError}
              </div>
            )}

            {/* Slots */}
            <div>
              <div className="mb-3.5">
                <div className="text-sm font-bold text-dark mb-0.5">{t('deploy.slotsTitle')}</div>
                <div className="text-xs text-muted">{t('deploy.slotsDesc')}</div>
              </div>

              <div className="grid grid-cols-3 gap-3.5">
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
              <div className="flex gap-3 mt-4 items-center flex-wrap">
                <button
                  onClick={() => { setConfirmAction('promote'); setActionError(null) }}
                  disabled={!canPromote || !canOperate || slotsLoading}
                  title={!canOperate ? t('deploy.bannerOperator') : undefined}
                  className={`px-5 py-2 rounded-md text-sm font-semibold transition-colors ${
                    canPromote && canOperate && !slotsLoading
                      ? 'bg-primary text-white hover:bg-primary/90'
                      : 'bg-surface-alt text-muted-light cursor-not-allowed'
                  }`}
                >
                  {t('deploy.promote')}
                </button>

                <button
                  onClick={() => { setConfirmAction('rollback'); setActionError(null) }}
                  disabled={!canRollback || !canOperate || slotsLoading}
                  title={!canOperate ? t('deploy.bannerOperator') : undefined}
                  className={`px-5 py-2 rounded-md text-sm font-semibold transition-colors ${
                    canRollback && canOperate && !slotsLoading
                      ? 'text-white hover:opacity-90'
                      : 'bg-surface-alt text-muted-light cursor-not-allowed'
                  }`}
                  style={canRollback && canOperate && !slotsLoading ? { background: '#b45309' } : undefined}
                >
                  {t('deploy.rollbackBtn')}
                </button>

                {slots && (
                  <span className="text-xs text-muted-light">
                    {!canOperate
                      ? t('deploy.noOpPermission')
                      : (!canPromote ? t('deploy.setNextToPromote') + ' ' : '')
                        + (!canRollback ? t('deploy.noPrevForRollback') : '')}
                  </span>
                )}
              </div>
            </div>
          </>
        )}
      </div>

      {/* ─── Modals ───────────────────────────────────────────────────────── */}

      {editing && selected && slots && (
        <NextSlotEditor
          poolId={selected.pool_id}
          tenantId={tenantId}
          skills={skills}
          sourceOptions={{
            dialogforms: dialogForms.map(f => ({ value: f.form_id, label: f.name ? `${f.form_id} — ${f.name}` : f.form_id })),
            pools:       pools.map(p => ({ value: p.pool_id, label: p.pool_id })),
            skills:      skills.map(s => ({ value: s.skill_id, label: s.name ? `${s.skill_id} — ${s.name}` : s.skill_id })),
          }}
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
          title={t('deploy.promoteTitle')}
          message={
            <span>
              {t('deploy.promoteNotice1', { next: slotNext, current: slotCurrent, previous: slotPrevious })}
              <br /><br />
              {t('deploy.promoteNotice2')}
            </span>
          }
          confirmLabel={t('deploy.confirmPromote')}
          confirmColor="#1B4F8A"
          onConfirm={handleConfirmAction}
          onCancel={() => setConfirmAction(null)}
          running={actionRunning}
          error={actionError}
        />
      )}

      {confirmAction === 'rollback' && (
        <ConfirmModal
          title={t('deploy.rollbackTitle')}
          message={
            <span>
              {t('deploy.rollbackNotice1', { previous: slotPrevious, current: slotCurrent })}
              <br /><br />
              {t('deploy.rollbackNotice2')}
            </span>
          }
          confirmLabel={t('deploy.confirmRollback')}
          confirmColor="#b45309"
          onConfirm={handleConfirmAction}
          onCancel={() => setConfirmAction(null)}
          running={actionRunning}
          error={actionError}
        />
      )}
    </div>
  )
}
