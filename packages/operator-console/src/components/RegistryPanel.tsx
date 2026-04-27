/**
 * RegistryPanel.tsx
 * Registry Management — Pools, Agent Types, Skills, and running Instances.
 *
 * Layout:
 *   ┌─────────────────────────────────────────────────────────────────┐
 *   │ ← Back   Registry Management                                    │
 *   ├──────────┬──────────────────────────────┬───────────────────────┤
 *   │  Tabs    │  List                        │  Detail / Form        │
 *   │  Pools   │  ───────────────────────     │                       │
 *   │  Agents  │  item rows…                  │  (create or inspect)  │
 *   │  Skills  │                              │                       │
 *   │  Running │                              │                       │
 *   └──────────┴──────────────────────────────┴───────────────────────┘
 */
import { useState, type FormEvent } from 'react'
import {
  usePools, createPool, updatePool,
  useAgentTypes, createAgentType, deleteAgentType,
  useSkills, deleteSkill,
  useInstances,
} from '../api/registry-hooks'
import type {
  RegistryPool, RegistryAgentType, RegistrySkill, RegistryInstance,
} from '../types'

// ── Colour palette ─────────────────────────────────────────────────────────
const C = {
  bg:         '#0d1117',
  surface:    '#0f1923',
  border:     '#1e293b',
  borderHov:  '#334155',
  text:       '#e2e8f0',
  muted:      '#64748b',
  accent:     '#f97316',     // orange-500
  accentDark: '#431407',
  green:      '#22c55e',
  red:        '#ef4444',
  yellow:     '#fbbf24',
  blue:       '#3b82f6',
  indigo:     '#6366f1',
  cyan:       '#22d3ee',
} as const

// ── Small reusable helpers ─────────────────────────────────────────────────

function Pill({ label, color = C.muted }: { label: string; color?: string }) {
  return (
    <span style={{
      display: 'inline-block', padding: '1px 7px',
      borderRadius: 10, fontSize: 10, fontWeight: 600,
      background: color + '22', color, border: `1px solid ${color}44`,
      whiteSpace: 'nowrap',
    }}>
      {label}
    </span>
  )
}

function StatusDot({ status }: { status: string }) {
  const color = status === 'active' || status === 'ready' ? C.green
              : status === 'deprecated' || status === 'inactive' ? C.muted
              : C.yellow
  return (
    <span style={{
      display: 'inline-block', width: 7, height: 7,
      borderRadius: '50%', background: color, marginRight: 5,
      boxShadow: status === 'active' || status === 'ready' ? `0 0 5px ${color}` : 'none',
    }} />
  )
}

function SectionHeader({ title, onAdd, addLabel }: {
  title: string; onAdd?: () => void; addLabel?: string
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '12px 16px', borderBottom: `1px solid ${C.border}`, flexShrink: 0 }}>
      <span style={{ fontSize: 13, fontWeight: 700, color: C.text }}>{title}</span>
      {onAdd && (
        <button onClick={onAdd} style={{
          padding: '3px 10px', borderRadius: 4, border: `1px solid ${C.accent}`,
          background: C.accentDark, color: C.accent, cursor: 'pointer', fontSize: 11, fontWeight: 600,
        }}>
          + {addLabel ?? 'New'}
        </button>
      )}
    </div>
  )
}

function EmptyRow({ msg }: { msg: string }) {
  return (
    <div style={{ padding: '32px 16px', textAlign: 'center', color: C.muted, fontSize: 13 }}>
      {msg}
    </div>
  )
}

function ErrorMsg({ msg, onDismiss }: { msg: string; onDismiss: () => void }) {
  return (
    <div style={{ margin: 12, padding: '8px 12px', borderRadius: 6,
      background: '#450a0a', border: `1px solid ${C.red}55`, color: C.red,
      fontSize: 12, display: 'flex', justifyContent: 'space-between', gap: 8 }}>
      <span>{msg}</span>
      <button onClick={onDismiss} style={{ background: 'none', border: 'none', color: C.red, cursor: 'pointer', fontWeight: 700 }}>✕</button>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ fontSize: 10, fontWeight: 700, color: C.muted, textTransform: 'uppercase', marginBottom: 4 }}>
        {label}
      </div>
      {children}
    </div>
  )
}

const inputStyle: React.CSSProperties = {
  width: '100%', padding: '6px 10px', borderRadius: 5,
  border: `1px solid ${C.border}`, background: '#1e293b',
  color: C.text, fontSize: 12, outline: 'none', boxSizing: 'border-box',
}

function Btn({ label, onClick, variant = 'default', disabled }: {
  label: string; onClick: () => void; variant?: 'default' | 'danger' | 'primary'; disabled?: boolean
}) {
  const colors = {
    default: { bg: '#1e293b', border: C.border, color: C.text },
    danger:  { bg: '#450a0a', border: C.red + '55', color: C.red },
    primary: { bg: C.accentDark, border: C.accent, color: C.accent },
  }[variant]
  return (
    <button onClick={onClick} disabled={disabled} style={{
      padding: '5px 12px', borderRadius: 5, border: `1px solid ${colors.border}`,
      background: colors.bg, color: colors.color, cursor: disabled ? 'not-allowed' : 'pointer',
      fontSize: 12, fontWeight: 600, opacity: disabled ? 0.5 : 1,
    }}>
      {label}
    </button>
  )
}

// ── FRAMEWORKS available ────────────────────────────────────────────────────

const FRAMEWORKS = [
  'plughub-native', 'human', 'external-mcp',
  'langgraph', 'crewai', 'anthropic_sdk',
  'azure_ai', 'google_vertex', 'generic_mcp',
]

const CHANNELS = ['webchat', 'whatsapp', 'voice', 'email', 'sms', 'instagram', 'telegram', 'webrtc']

// ── Pools tab ──────────────────────────────────────────────────────────────

function PoolRow({ pool, selected, onClick }: {
  pool: RegistryPool; selected: boolean; onClick: () => void
}) {
  return (
    <div onClick={onClick} style={{
      padding: '10px 14px', cursor: 'pointer',
      background: selected ? '#1e293b' : 'transparent',
      borderBottom: `1px solid ${C.border}`,
      borderLeft: `2px solid ${selected ? C.accent : 'transparent'}`,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        <StatusDot status={pool.status} />
        <span style={{ fontSize: 12, fontWeight: 600, color: C.text, fontFamily: 'monospace' }}>
          {pool.pool_id}
        </span>
      </div>
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
        {pool.channel_types.map(ch => (
          <Pill key={ch} label={ch} color={C.cyan} />
        ))}
        <Pill label={`SLA ${pool.sla_target_ms}ms`} color={C.muted} />
      </div>
    </div>
  )
}

function PoolDetail({ pool, tenantId, onSaved, onClose }: {
  pool: RegistryPool; tenantId: string; onSaved: () => void; onClose: () => void
}) {
  const [desc, setDesc]       = useState(pool.description ?? '')
  const [sla, setSla]         = useState(String(pool.sla_target_ms))
  const [channels, setChannels] = useState<string[]>(pool.channel_types)
  const [saving, setSaving]   = useState(false)
  const [err, setErr]         = useState<string | null>(null)

  async function handleSave() {
    setSaving(true); setErr(null)
    try {
      await updatePool(tenantId, pool.pool_id, {
        description:   desc || undefined,
        sla_target_ms: Number(sla),
        channel_types: channels,
      })
      onSaved()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Save failed')
    } finally { setSaving(false) }
  }

  function toggleChannel(ch: string) {
    setChannels(prev => prev.includes(ch) ? prev.filter(x => x !== ch) : [...prev, ch])
  }

  return (
    <div style={{ padding: 16, overflowY: 'auto', flex: 1 }}>
      {err && <ErrorMsg msg={err} onDismiss={() => setErr(null)} />}

      <Field label="Pool ID">
        <div style={{ ...inputStyle, color: C.muted, background: '#0d1117', cursor: 'default' }}>
          {pool.pool_id}
        </div>
      </Field>

      <Field label="Description">
        <input value={desc} onChange={e => setDesc(e.target.value)}
          style={inputStyle} placeholder="Optional description" />
      </Field>

      <Field label="SLA Target (ms)">
        <input type="number" value={sla} onChange={e => setSla(e.target.value)}
          style={inputStyle} min={0} />
      </Field>

      <Field label="Channel Types">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 4 }}>
          {CHANNELS.map(ch => (
            <label key={ch} style={{ display: 'flex', alignItems: 'center', gap: 4,
              cursor: 'pointer', fontSize: 11, color: channels.includes(ch) ? C.cyan : C.muted }}>
              <input type="checkbox" checked={channels.includes(ch)}
                onChange={() => toggleChannel(ch)} style={{ cursor: 'pointer' }} />
              {ch}
            </label>
          ))}
        </div>
      </Field>

      <Field label="Status">
        <Pill label={pool.status} color={pool.status === 'active' ? C.green : C.muted} />
      </Field>

      <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
        <Btn label={saving ? 'Saving…' : 'Save'} onClick={handleSave}
          variant="primary" disabled={saving || channels.length === 0} />
        <Btn label="Cancel" onClick={onClose} />
      </div>
    </div>
  )
}

function CreatePoolForm({ tenantId, onCreated, onClose }: {
  tenantId: string; onCreated: () => void; onClose: () => void
}) {
  const [poolId, setPoolId]   = useState('')
  const [desc, setDesc]       = useState('')
  const [sla, setSla]         = useState('30000')
  const [channels, setChannels] = useState<string[]>(['webchat'])
  const [saving, setSaving]   = useState(false)
  const [err, setErr]         = useState<string | null>(null)

  function toggleChannel(ch: string) {
    setChannels(prev => prev.includes(ch) ? prev.filter(x => x !== ch) : [...prev, ch])
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault(); setSaving(true); setErr(null)
    try {
      await createPool(tenantId, {
        pool_id: poolId, channel_types: channels,
        sla_target_ms: Number(sla), description: desc || undefined,
      })
      onCreated()
    } catch (ex: unknown) {
      setErr(ex instanceof Error ? ex.message : 'Create failed')
    } finally { setSaving(false) }
  }

  return (
    <form onSubmit={handleSubmit} style={{ padding: 16, overflowY: 'auto', flex: 1 }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: C.text, marginBottom: 16 }}>New Pool</div>
      {err && <ErrorMsg msg={err} onDismiss={() => setErr(null)} />}

      <Field label="Pool ID *">
        <input value={poolId} onChange={e => setPoolId(e.target.value)}
          style={inputStyle} placeholder="e.g. sac_ia" required pattern="^[a-z0-9_]+$" />
        <div style={{ fontSize: 10, color: C.muted, marginTop: 3 }}>lowercase, digits, underscores only</div>
      </Field>

      <Field label="Description">
        <input value={desc} onChange={e => setDesc(e.target.value)}
          style={inputStyle} placeholder="Optional" />
      </Field>

      <Field label="SLA Target (ms) *">
        <input type="number" value={sla} onChange={e => setSla(e.target.value)}
          style={inputStyle} min={1000} required />
      </Field>

      <Field label="Channel Types *">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 4 }}>
          {CHANNELS.map(ch => (
            <label key={ch} style={{ display: 'flex', alignItems: 'center', gap: 4,
              cursor: 'pointer', fontSize: 11, color: channels.includes(ch) ? C.cyan : C.muted }}>
              <input type="checkbox" checked={channels.includes(ch)}
                onChange={() => toggleChannel(ch)} />
              {ch}
            </label>
          ))}
        </div>
      </Field>

      <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
        <Btn label={saving ? 'Creating…' : 'Create Pool'} onClick={() => {}}
          variant="primary" disabled={saving || !poolId || channels.length === 0} />
        <Btn label="Cancel" onClick={onClose} />
      </div>
    </form>
  )
}

function PoolsTab({ tenantId }: { tenantId: string }) {
  const { pools, loading, refresh } = usePools(tenantId)
  const [selected, setSelected]     = useState<RegistryPool | null>(null)
  const [creating, setCreating]     = useState(false)

  function handleCreated() { setCreating(false); refresh() }
  function handleSaved()   { setSelected(null); refresh() }

  return (
    <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
      {/* List */}
      <div style={{ width: 280, flexShrink: 0, borderRight: `1px solid ${C.border}`,
        display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <SectionHeader
          title={`Pools${loading ? ' …' : ` (${pools.length})`}`}
          onAdd={() => { setCreating(true); setSelected(null) }}
          addLabel="Pool"
        />
        <div style={{ overflowY: 'auto', flex: 1 }}>
          {pools.length === 0 && !loading && <EmptyRow msg="No pools found" />}
          {pools.map(p => (
            <PoolRow key={p.pool_id} pool={p}
              selected={selected?.pool_id === p.pool_id && !creating}
              onClick={() => { setSelected(p); setCreating(false) }} />
          ))}
        </div>
      </div>

      {/* Detail */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {creating
          ? <CreatePoolForm tenantId={tenantId} onCreated={handleCreated} onClose={() => setCreating(false)} />
          : selected
            ? <PoolDetail pool={selected} tenantId={tenantId}
                onSaved={handleSaved} onClose={() => setSelected(null)} />
            : <EmptyRow msg="Select a pool or create a new one" />
        }
      </div>
    </div>
  )
}

// ── Agent Types tab ────────────────────────────────────────────────────────

function AgentRow({ at, selected, onClick }: {
  at: RegistryAgentType; selected: boolean; onClick: () => void
}) {
  const roleColor = at.role === 'orchestrator' ? C.indigo : at.role === 'evaluator' ? C.yellow : C.cyan
  return (
    <div onClick={onClick} style={{
      padding: '10px 14px', cursor: 'pointer',
      background: selected ? '#1e293b' : 'transparent',
      borderBottom: `1px solid ${C.border}`,
      borderLeft: `2px solid ${selected ? C.accent : 'transparent'}`,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        <StatusDot status={at.status} />
        <span style={{ fontSize: 12, fontWeight: 600, color: C.text, fontFamily: 'monospace' }}>
          {at.agent_type_id}
        </span>
      </div>
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
        <Pill label={at.framework} color={C.blue} />
        <Pill label={at.role} color={roleColor} />
        {at.pools.map(p => (
          <Pill key={p.pool_id} label={p.pool_id} color={C.muted} />
        ))}
      </div>
    </div>
  )
}

function AgentDetail({ at, tenantId, onDeleted, onClose }: {
  at: RegistryAgentType; tenantId: string; onDeleted: () => void; onClose: () => void
}) {
  const [deleting, setDeleting]       = useState(false)
  const [confirmDelete, setConfirm]   = useState(false)
  const [err, setErr]                 = useState<string | null>(null)

  async function handleDelete() {
    setDeleting(true); setErr(null)
    try {
      await deleteAgentType(tenantId, at.agent_type_id)
      onDeleted()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Delete failed')
      setDeleting(false)
      setConfirm(false)
    }
  }

  return (
    <div style={{ padding: 16, overflowY: 'auto', flex: 1 }}>
      {err && <ErrorMsg msg={err} onDismiss={() => setErr(null)} />}

      <Field label="Agent Type ID">
        <div style={{ ...inputStyle, color: C.muted, background: '#0d1117', cursor: 'default', fontFamily: 'monospace' }}>
          {at.agent_type_id}
        </div>
      </Field>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <Field label="Framework"><Pill label={at.framework} color={C.blue} /></Field>
        <Field label="Execution"><Pill label={at.execution_model} color={C.cyan} /></Field>
        <Field label="Role"><Pill label={at.role} color={C.indigo} /></Field>
        <Field label="Max Concurrent">
          <span style={{ fontSize: 12, color: C.text }}>{at.max_concurrent_sessions}</span>
        </Field>
      </div>

      <Field label="Pools">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {at.pools.map(p => <Pill key={p.pool_id} label={p.pool_id} color={C.accent} />)}
        </div>
      </Field>

      {at.skills.length > 0 && (
        <Field label="Skills">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {at.skills.map(s => <Pill key={s.skill_id} label={s.skill_id} color={C.green} />)}
          </div>
        </Field>
      )}

      {at.permissions.length > 0 && (
        <Field label="Permissions">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {at.permissions.map((p, i) => <Pill key={i} label={p} color={C.muted} />)}
          </div>
        </Field>
      )}

      <Field label="Status"><StatusDot status={at.status} /><span style={{ fontSize: 12, color: C.text }}>{at.status}</span></Field>
      <Field label="Traffic Weight">
        <span style={{ fontSize: 12, color: C.text }}>{at.traffic_weight}</span>
      </Field>

      <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
        {confirmDelete
          ? <>
              <span style={{ fontSize: 12, color: C.red, alignSelf: 'center' }}>Deprecate this agent type?</span>
              <Btn label={deleting ? 'Deleting…' : 'Confirm'} onClick={handleDelete} variant="danger" disabled={deleting} />
              <Btn label="Cancel" onClick={() => setConfirm(false)} />
            </>
          : <>
              <Btn label="Deprecate" onClick={() => setConfirm(true)} variant="danger" />
              <Btn label="Close" onClick={onClose} />
            </>
        }
      </div>
    </div>
  )
}

function CreateAgentForm({ tenantId, pools, skills, onCreated, onClose }: {
  tenantId: string
  pools: RegistryPool[]
  skills: RegistrySkill[]
  onCreated: () => void
  onClose: () => void
}) {
  const [id, setId]                   = useState('')
  const [framework, setFramework]     = useState('plughub-native')
  const [execModel, setExecModel]     = useState<'stateless' | 'stateful'>('stateless')
  const [role, setRole]               = useState('executor')
  const [selectedPools, setPools]     = useState<string[]>([])
  const [selectedSkills, setSkills]   = useState<string[]>([])
  const [maxSessions, setMaxSessions] = useState('1')
  const [promptId, setPromptId]       = useState('')
  const [saving, setSaving]           = useState(false)
  const [err, setErr]                 = useState<string | null>(null)

  function togglePool(id: string) {
    setPools(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id])
  }
  function toggleSkill(id: string) {
    setSkills(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id])
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault(); setSaving(true); setErr(null)
    try {
      await createAgentType(tenantId, {
        agent_type_id:           id,
        framework,
        execution_model:         execModel,
        role,
        pools:                   selectedPools,
        skills:                  selectedSkills.map(s => ({ skill_id: s, version_policy: 'stable' })),
        max_concurrent_sessions: Number(maxSessions),
        prompt_id:               promptId || undefined,
      })
      onCreated()
    } catch (ex: unknown) {
      setErr(ex instanceof Error ? ex.message : 'Create failed')
    } finally { setSaving(false) }
  }

  const selectStyle: React.CSSProperties = { ...inputStyle, cursor: 'pointer' }

  return (
    <form onSubmit={handleSubmit} style={{ padding: 16, overflowY: 'auto', flex: 1 }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: C.text, marginBottom: 16 }}>New Agent Type</div>
      {err && <ErrorMsg msg={err} onDismiss={() => setErr(null)} />}

      <Field label="Agent Type ID *">
        <input value={id} onChange={e => setId(e.target.value)} style={inputStyle}
          placeholder="e.g. agente_sac_ia_v2" required pattern="^[a-z][a-z0-9_]+_v\d+$" />
        <div style={{ fontSize: 10, color: C.muted, marginTop: 3 }}>
          Format: <code style={{ color: C.accent }}>name_v{'{n}'}</code>
        </div>
      </Field>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <Field label="Framework *">
          <select value={framework} onChange={e => setFramework(e.target.value)} style={selectStyle}>
            {FRAMEWORKS.map(f => <option key={f} value={f}>{f}</option>)}
          </select>
        </Field>

        <Field label="Execution Model *">
          <select value={execModel} onChange={e => setExecModel(e.target.value as 'stateless' | 'stateful')}
            style={selectStyle}>
            <option value="stateless">stateless</option>
            <option value="stateful">stateful</option>
          </select>
        </Field>

        <Field label="Role *">
          <select value={role} onChange={e => setRole(e.target.value)} style={selectStyle}>
            <option value="executor">executor</option>
            <option value="orchestrator">orchestrator</option>
            <option value="evaluator">evaluator</option>
          </select>
        </Field>

        <Field label="Max Concurrent Sessions">
          <input type="number" value={maxSessions}
            onChange={e => setMaxSessions(e.target.value)}
            style={inputStyle} min={1} />
        </Field>
      </div>

      <Field label="Pools * (select at least one)">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 4 }}>
          {pools.length === 0 && <span style={{ fontSize: 11, color: C.muted }}>No pools available</span>}
          {pools.map(p => (
            <label key={p.pool_id} style={{
              display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer',
              fontSize: 11, color: selectedPools.includes(p.pool_id) ? C.accent : C.muted,
            }}>
              <input type="checkbox" checked={selectedPools.includes(p.pool_id)}
                onChange={() => togglePool(p.pool_id)} />
              {p.pool_id}
            </label>
          ))}
        </div>
      </Field>

      {skills.length > 0 && (
        <Field label="Skills">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 4 }}>
            {skills.map(s => (
              <label key={s.skill_id} style={{
                display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer',
                fontSize: 11, color: selectedSkills.includes(s.skill_id) ? C.green : C.muted,
              }}>
                <input type="checkbox" checked={selectedSkills.includes(s.skill_id)}
                  onChange={() => toggleSkill(s.skill_id)} />
                {s.skill_id}
              </label>
            ))}
          </div>
        </Field>
      )}

      <Field label="Prompt ID">
        <input value={promptId} onChange={e => setPromptId(e.target.value)}
          style={inputStyle} placeholder="Optional" />
      </Field>

      <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
        <Btn label={saving ? 'Creating…' : 'Create Agent Type'} onClick={() => {}}
          variant="primary" disabled={saving || !id || selectedPools.length === 0} />
        <Btn label="Cancel" onClick={onClose} />
      </div>
    </form>
  )
}

function AgentsTab({ tenantId }: { tenantId: string }) {
  const { agentTypes, loading, refresh } = useAgentTypes(tenantId)
  const { pools }                         = usePools(tenantId)
  const { skills }                        = useSkills(tenantId)
  const [selected, setSelected]           = useState<RegistryAgentType | null>(null)
  const [creating, setCreating]           = useState(false)

  function handleCreated() { setCreating(false); refresh() }
  function handleDeleted() { setSelected(null); refresh() }

  return (
    <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
      <div style={{ width: 300, flexShrink: 0, borderRight: `1px solid ${C.border}`,
        display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <SectionHeader
          title={`Agent Types${loading ? ' …' : ` (${agentTypes.length})`}`}
          onAdd={() => { setCreating(true); setSelected(null) }}
          addLabel="Agent Type"
        />
        <div style={{ overflowY: 'auto', flex: 1 }}>
          {agentTypes.length === 0 && !loading && <EmptyRow msg="No agent types found" />}
          {agentTypes.map(at => (
            <AgentRow key={at.agent_type_id} at={at}
              selected={selected?.agent_type_id === at.agent_type_id && !creating}
              onClick={() => { setSelected(at); setCreating(false) }} />
          ))}
        </div>
      </div>

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {creating
          ? <CreateAgentForm tenantId={tenantId} pools={pools} skills={skills}
              onCreated={handleCreated} onClose={() => setCreating(false)} />
          : selected
            ? <AgentDetail at={selected} tenantId={tenantId}
                onDeleted={handleDeleted} onClose={() => setSelected(null)} />
            : <EmptyRow msg="Select an agent type or create a new one" />
        }
      </div>
    </div>
  )
}

// ── Skills tab ─────────────────────────────────────────────────────────────

function SkillRow({ skill, selected, onClick }: {
  skill: RegistrySkill; selected: boolean; onClick: () => void
}) {
  const typeColor = skill.classification.type === 'orchestrator' ? C.indigo
                  : skill.classification.type === 'vertical' ? C.cyan : C.yellow
  return (
    <div onClick={onClick} style={{
      padding: '10px 14px', cursor: 'pointer',
      background: selected ? '#1e293b' : 'transparent',
      borderBottom: `1px solid ${C.border}`,
      borderLeft: `2px solid ${selected ? C.accent : 'transparent'}`,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        <StatusDot status={skill.status} />
        <span style={{ fontSize: 12, fontWeight: 600, color: C.text, fontFamily: 'monospace' }}>
          {skill.skill_id}
        </span>
      </div>
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
        <Pill label={skill.version} color={C.muted} />
        <Pill label={skill.classification.type} color={typeColor} />
        {skill.classification.domain && (
          <Pill label={skill.classification.domain} color={C.muted} />
        )}
      </div>
    </div>
  )
}

function SkillDetail({ skill, tenantId, onDeleted, onClose }: {
  skill: RegistrySkill; tenantId: string; onDeleted: () => void; onClose: () => void
}) {
  const [confirmDelete, setConfirm] = useState(false)
  const [deleting, setDeleting]     = useState(false)
  const [err, setErr]               = useState<string | null>(null)

  async function handleDelete() {
    setDeleting(true); setErr(null)
    try {
      await deleteSkill(tenantId, skill.skill_id)
      onDeleted()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : 'Delete failed')
      setDeleting(false); setConfirm(false)
    }
  }

  return (
    <div style={{ padding: 16, overflowY: 'auto', flex: 1 }}>
      {err && <ErrorMsg msg={err} onDismiss={() => setErr(null)} />}

      <Field label="Skill ID">
        <div style={{ ...inputStyle, color: C.muted, background: '#0d1117', fontFamily: 'monospace' }}>
          {skill.skill_id}
        </div>
      </Field>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <Field label="Name"><span style={{ fontSize: 12, color: C.text }}>{skill.name}</span></Field>
        <Field label="Version"><Pill label={skill.version} color={C.muted} /></Field>
        <Field label="Type"><Pill label={skill.classification.type} color={C.cyan} /></Field>
        {skill.classification.domain && (
          <Field label="Domain"><Pill label={skill.classification.domain} color={C.muted} /></Field>
        )}
      </div>

      <Field label="Description">
        <div style={{ fontSize: 12, color: C.text, lineHeight: 1.5 }}>{skill.description}</div>
      </Field>

      {skill.tools.length > 0 && (
        <Field label={`Tools (${skill.tools.length})`}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {skill.tools.map((t, i) => (
              <Pill key={i} label={`${t.server}/${t.name}`} color={C.muted} />
            ))}
          </div>
        </Field>
      )}

      {skill.knowledge_domains.length > 0 && (
        <Field label="Knowledge Domains">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {skill.knowledge_domains.map((d, i) => <Pill key={i} label={d} color={C.muted} />)}
          </div>
        </Field>
      )}

      <Field label="Status">
        <StatusDot status={skill.status} />
        <span style={{ fontSize: 12, color: C.text }}>{skill.status}</span>
      </Field>

      <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
        {confirmDelete
          ? <>
              <span style={{ fontSize: 12, color: C.red, alignSelf: 'center' }}>Delete this skill?</span>
              <Btn label={deleting ? 'Deleting…' : 'Confirm'} onClick={handleDelete} variant="danger" disabled={deleting} />
              <Btn label="Cancel" onClick={() => setConfirm(false)} />
            </>
          : <>
              <Btn label="Delete" onClick={() => setConfirm(true)} variant="danger" />
              <Btn label="Close" onClick={onClose} />
            </>
        }
      </div>
    </div>
  )
}

function SkillsTab({ tenantId }: { tenantId: string }) {
  const { skills, loading, refresh } = useSkills(tenantId)
  const [selected, setSelected]      = useState<RegistrySkill | null>(null)

  function handleDeleted() { setSelected(null); refresh() }

  return (
    <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
      <div style={{ width: 300, flexShrink: 0, borderRight: `1px solid ${C.border}`,
        display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <SectionHeader title={`Skills${loading ? ' …' : ` (${skills.length})`}`} />
        <div style={{ overflowY: 'auto', flex: 1 }}>
          {skills.length === 0 && !loading && <EmptyRow msg="No skills found" />}
          {skills.map(s => (
            <SkillRow key={s.skill_id} skill={s}
              selected={selected?.skill_id === s.skill_id}
              onClick={() => setSelected(s)} />
          ))}
        </div>
      </div>

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {selected
          ? <SkillDetail skill={selected} tenantId={tenantId}
              onDeleted={handleDeleted} onClose={() => setSelected(null)} />
          : (
            <div style={{ padding: 24, color: C.muted, fontSize: 13 }}>
              <p>Select a skill to view its details.</p>
              <p style={{ marginTop: 8 }}>
                Skills are managed via YAML files in{' '}
                <code style={{ color: C.accent, fontSize: 11 }}>
                  packages/skill-flow-engine/skills/
                </code>{' '}
                and synced to the registry at bridge startup.
              </p>
            </div>
          )
        }
      </div>
    </div>
  )
}

// ── Instances tab (read-only) ──────────────────────────────────────────────

function InstanceRow({ inst }: { inst: RegistryInstance }) {
  const statusColor = inst.status === 'ready'    ? C.green
                    : inst.status === 'busy'     ? C.yellow
                    : inst.status === 'draining' ? C.accent
                    : C.muted
  return (
    <div style={{ padding: '10px 14px', borderBottom: `1px solid ${C.border}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        <StatusDot status={inst.status} />
        <span style={{ fontSize: 12, fontWeight: 600, color: C.text, fontFamily: 'monospace' }}>
          {inst.instance_id}
        </span>
        <Pill label={inst.status} color={statusColor} />
      </div>
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
        <Pill label={`pool: ${inst.pool_id}`} color={C.accent} />
        <Pill label={inst.agent_type_id} color={C.blue} />
        {inst.channel_types.map(ch => <Pill key={ch} label={ch} color={C.muted} />)}
      </div>
    </div>
  )
}

function InstancesTab({ tenantId }: { tenantId: string }) {
  const [filterPool, setFilterPool] = useState('')
  const { instances, total, loading } = useInstances(tenantId, filterPool || undefined)

  return (
    <div style={{ display: 'flex', flex: 1, overflow: 'hidden', flexDirection: 'column' }}>
      <div style={{ padding: '8px 14px', borderBottom: `1px solid ${C.border}`,
        display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontSize: 12, color: C.muted, fontWeight: 600 }}>
          Running Instances{loading ? ' …' : ` (${total})`}
        </span>
        <input value={filterPool} onChange={e => setFilterPool(e.target.value)}
          style={{ ...inputStyle, width: 160 }} placeholder="Filter by pool_id" />
      </div>
      <div style={{ overflowY: 'auto', flex: 1 }}>
        {instances.length === 0 && !loading && <EmptyRow msg="No running instances" />}
        {instances.map(inst => <InstanceRow key={inst.instance_id} inst={inst} />)}
      </div>
    </div>
  )
}

// ── Root component ─────────────────────────────────────────────────────────

type TabId = 'pools' | 'agents' | 'skills' | 'instances'

const TABS: Array<{ id: TabId; label: string }> = [
  { id: 'pools',     label: 'Pools'        },
  { id: 'agents',    label: 'Agent Types'  },
  { id: 'skills',    label: 'Skills'       },
  { id: 'instances', label: 'Running'      },
]

interface Props {
  tenantId: string
  onBack:   () => void
}

export function RegistryPanel({ tenantId, onBack }: Props) {
  const [tab, setTab] = useState<TabId>('pools')

  return (
    <div style={{
      flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden',
      background: C.bg,
    }}>
      {/* Header bar */}
      <div style={{
        height: 44, flexShrink: 0, borderBottom: `1px solid ${C.border}`,
        display: 'flex', alignItems: 'center', paddingLeft: 12, gap: 16, background: C.surface,
      }}>
        <button onClick={onBack} style={{
          background: 'none', border: 'none', color: C.muted, cursor: 'pointer',
          fontSize: 18, lineHeight: 1, padding: '0 4px',
        }} title="Back">←</button>
        <span style={{ fontSize: 14, fontWeight: 700, color: C.text }}>Registry Management</span>
        <div style={{ flex: 1 }} />
        <div style={{ display: 'flex', gap: 4, paddingRight: 12 }}>
          {TABS.map(t => (
            <button key={t.id} onClick={() => setTab(t.id)} style={{
              padding: '4px 12px', borderRadius: 4, fontSize: 12, fontWeight: tab === t.id ? 700 : 400,
              background: tab === t.id ? C.accentDark : 'transparent',
              color: tab === t.id ? C.accent : C.muted,
              border: `1px solid ${tab === t.id ? C.accent : C.border}`,
              cursor: 'pointer',
            }}>
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {tab === 'pools'     && <PoolsTab     tenantId={tenantId} />}
        {tab === 'agents'    && <AgentsTab    tenantId={tenantId} />}
        {tab === 'skills'    && <SkillsTab    tenantId={tenantId} />}
        {tab === 'instances' && <InstancesTab tenantId={tenantId} />}
      </div>
    </div>
  )
}

import type React from 'react'
