/**
 * HumanAgentPanel.tsx
 * Human Agent Management panel.
 *
 * Accent: emerald (#10b981 / #022c22)
 *
 * Two tabs:
 *   Live Status — currently logged-in human agent instances, with pause/resume/force-logout
 *   Profiles    — human agent type records (CRUD); each represents a named human agent slot
 *
 * Layout (full panel):
 *   ┌──────────────────────────────────────────────────────────────────────┐
 *   │  Header: "Human Agents"  [Live Status] [Profiles]   [← Back]        │
 *   ├──────────────────────────────────────────────────────────────────────┤
 *   │  Tab content                                                         │
 *   └──────────────────────────────────────────────────────────────────────┘
 */
import { useState, useEffect } from 'react'
import {
  useHumanInstances, useHumanAgentTypes,
  instanceAction, createHumanAgent, deprecateHumanAgent, updateHumanAgent,
  type InstanceAction,
} from '../api/human-agent-hooks'
import { usePools } from '../api/registry-hooks'
import type { RegistryInstance, RegistryAgentType } from '../types'

// ── Colour palette ────────────────────────────────────────────────────────────
const C = {
  bg:          '#0f1923',
  surface:     '#1a2332',
  surfaceHi:   '#1e2a3a',
  border:      '#1e3a2e',
  borderFaint: '#162530',
  accent:      '#10b981',
  accentDark:  '#022c22',
  accentMid:   '#063d2a',
  text:        '#e2e8f0',
  textMid:     '#94a3b8',
  textFaint:   '#475569',
  danger:      '#ef4444',
  warn:        '#f59e0b',
  ready:       '#22c55e',
  busy:        '#3b82f6',
  paused:      '#f59e0b',
  logout:      '#475569',
  login:       '#a78bfa',
}

const STATUS_COLOR: Record<string, string> = {
  ready:  C.ready,
  busy:   C.busy,
  paused: C.warn,
  logout: C.logout,
  login:  C.login,
}

// ── Props ─────────────────────────────────────────────────────────────────────
interface Props {
  tenantId: string
  onBack:   () => void
}

type Tab = 'live' | 'profiles'

// ── Main component ────────────────────────────────────────────────────────────
export function HumanAgentPanel({ tenantId, onBack }: Props) {
  const [tab,   setTab]   = useState<Tab>('live')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { setError(null) }, [tenantId])

  return (
    <div style={{ display: 'flex', flex: 1, overflow: 'hidden', background: C.bg, flexDirection: 'column' }}>

      {/* ── Header bar ─────────────────────────────────────────────────────── */}
      <div style={{
        padding: '12px 24px',
        borderBottom: `1px solid ${C.border}`,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div>
            <span style={{ fontSize: 15, fontWeight: 700, color: C.text }}>Human Agents</span>
            <span style={{ fontSize: 11, color: C.textFaint, marginLeft: 8 }}>
              Manage human agent profiles and monitor live status
            </span>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            {(['live', 'profiles'] as Tab[]).map(t => (
              <button
                key={t}
                onClick={() => { setTab(t); setError(null) }}
                style={{
                  padding: '4px 14px', borderRadius: 4, fontSize: 12, cursor: 'pointer',
                  fontWeight: tab === t ? 600 : 400,
                  background: tab === t ? C.accentMid : 'transparent',
                  border: `1px solid ${tab === t ? C.accent : C.border}`,
                  color: tab === t ? C.accent : C.textMid,
                }}
              >
                {t === 'live' ? '● Live Status' : '⚙ Profiles'}
              </button>
            ))}
          </div>
        </div>
        <button onClick={onBack} style={backBtnStyle}>← Back</button>
      </div>

      {/* ── Error banner ───────────────────────────────────────────────────── */}
      {error && (
        <div style={{
          padding: '8px 24px', background: '#2d0a0a',
          borderBottom: `1px solid ${C.danger}`,
          color: C.danger, fontSize: 12, flexShrink: 0,
        }}>
          {error}
        </div>
      )}

      {/* ── Tab content ────────────────────────────────────────────────────── */}
      <div style={{ flex: 1, overflow: 'hidden' }}>
        {tab === 'live'     && <LiveTab     tenantId={tenantId} onError={setError} />}
        {tab === 'profiles' && <ProfilesTab tenantId={tenantId} onError={setError} />}
      </div>
    </div>
  )
}

// ── Live Status tab ───────────────────────────────────────────────────────────
function LiveTab({ tenantId, onError }: { tenantId: string; onError: (m: string) => void }) {
  const { instances, total, loading, refresh } = useHumanInstances(tenantId)
  const [filter, setFilter] = useState<string>('all')
  const [acting, setActing] = useState<string | null>(null)

  const filtered = filter === 'all'
    ? instances
    : instances.filter(i => i.status === filter)

  async function doAction(inst: RegistryInstance, action: InstanceAction) {
    setActing(inst.instance_id)
    try {
      await instanceAction(tenantId, inst.instance_id, action)
      await refresh()
      onError('')
    } catch (e) {
      onError((e as Error).message)
    } finally { setActing(null) }
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', height: '100%' }}>

      {/* Toolbar */}
      <div style={{
        padding: '10px 24px', borderBottom: `1px solid ${C.borderFaint}`,
        display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0,
      }}>
        <span style={{ fontSize: 11, color: C.textFaint }}>
          {loading ? 'Refreshing…' : `${total} human agent(s) tracked`}
        </span>
        <div style={{ display: 'flex', gap: 4, marginLeft: 'auto' }}>
          {['all', 'ready', 'busy', 'paused', 'login', 'logout'].map(s => (
            <button
              key={s}
              onClick={() => setFilter(s)}
              style={{
                padding: '2px 10px', borderRadius: 4, fontSize: 11, cursor: 'pointer',
                background: filter === s ? C.surfaceHi : 'transparent',
                border: `1px solid ${filter === s ? C.accent : C.border}`,
                color: filter === s ? C.accent : C.textFaint,
              }}
            >
              {s === 'all' ? 'All' : s}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '12px 24px' }}>
        {filtered.length === 0 ? (
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            height: 200, flexDirection: 'column', gap: 8,
          }}>
            <div style={{ fontSize: 28 }}>👤</div>
            <div style={{ fontSize: 13, color: C.textMid }}>
              {filter === 'all' ? 'No human agents logged in' : `No agents with status "${filter}"`}
            </div>
            <div style={{ fontSize: 11, color: C.textFaint }}>
              Agents appear here when they log in via Agent Assist UI
            </div>
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr style={{ borderBottom: `1px solid ${C.border}` }}>
                {['Instance ID', 'Agent Type', 'Pool', 'Status', 'Sessions', 'Last updated', 'Actions'].map(h => (
                  <th key={h} style={{
                    textAlign: 'left', padding: '6px 10px',
                    fontSize: 10, fontWeight: 700, color: C.textFaint,
                    letterSpacing: '0.5px', textTransform: 'uppercase',
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map(inst => (
                <tr
                  key={inst.instance_id}
                  style={{
                    borderBottom: `1px solid ${C.borderFaint}`,
                    background: acting === inst.instance_id ? C.surfaceHi : 'transparent',
                  }}
                >
                  <td style={{ padding: '8px 10px', fontFamily: 'monospace', color: C.textMid, fontSize: 11 }}>
                    {inst.instance_id}
                  </td>
                  <td style={{ padding: '8px 10px', color: C.text }}>{inst.agent_type_id}</td>
                  <td style={{ padding: '8px 10px' }}>
                    <span style={{
                      fontSize: 10, padding: '2px 6px', borderRadius: 3,
                      background: C.accentMid, color: C.accent,
                    }}>{inst.pool_id}</span>
                  </td>
                  <td style={{ padding: '8px 10px' }}>
                    <StatusBadge status={inst.status} />
                  </td>
                  <td style={{ padding: '8px 10px', color: C.textMid, textAlign: 'center' }}>
                    {(inst as unknown as Record<string, unknown>)['current_sessions'] as number ?? 0}
                  </td>
                  <td style={{ padding: '8px 10px', color: C.textFaint, fontSize: 11 }}>
                    {new Date(inst.updated_at).toLocaleTimeString()}
                  </td>
                  <td style={{ padding: '8px 10px' }}>
                    <ActionButtons
                      inst={inst}
                      acting={acting === inst.instance_id}
                      onAction={action => doAction(inst, action)}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

// ── Profiles tab ──────────────────────────────────────────────────────────────
function ProfilesTab({ tenantId, onError }: { tenantId: string; onError: (m: string) => void }) {
  const { agentTypes, loading, refresh } = useHumanAgentTypes(tenantId)
  const { pools }                        = usePools(tenantId)
  const [selected, setSelected]          = useState<RegistryAgentType | null>(null)
  const [creating, setCreating]          = useState(false)

  useEffect(() => { setSelected(null); setCreating(false) }, [tenantId])

  const active     = agentTypes.filter(a => a.status === 'active')
  const deprecated = agentTypes.filter(a => a.status === 'deprecated')

  return (
    <div style={{ display: 'flex', flex: 1, overflow: 'hidden', height: '100%' }}>

      {/* Left sidebar */}
      <div style={{
        width: 280, flexShrink: 0,
        borderRight: `1px solid ${C.border}`,
        display: 'flex', flexDirection: 'column', overflow: 'hidden',
      }}>
        <div style={{
          padding: '10px 16px', borderBottom: `1px solid ${C.border}`,
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        }}>
          <div>
            <div style={{ fontSize: 12, fontWeight: 700, color: C.text }}>
              Agent Profiles
            </div>
            <div style={{ fontSize: 11, color: C.textFaint }}>
              {loading ? 'Loading…' : `${active.length} active`}
            </div>
          </div>
          <button
            onClick={() => { setCreating(true); setSelected(null) }}
            style={{
              background: creating ? C.accentMid : 'transparent',
              border: `1px solid ${creating ? C.accent : C.border}`,
              borderRadius: 4, color: C.accent, cursor: 'pointer',
              fontSize: 11, padding: '3px 10px',
            }}
          >
            + New
          </button>
        </div>

        <div style={{ flex: 1, overflowY: 'auto', padding: '6px 0' }}>
          {agentTypes.length === 0 && !loading && (
            <div style={{ padding: '20px 16px', textAlign: 'center', color: C.textFaint, fontSize: 12 }}>
              No human agent profiles yet
            </div>
          )}

          {active.length > 0 && (
            <>
              <div style={{ padding: '4px 16px', fontSize: 10, color: C.textFaint, fontWeight: 700, letterSpacing: '0.5px', textTransform: 'uppercase' }}>
                Active
              </div>
              {active.map(at => (
                <AgentTypeRow key={at.agent_type_id} at={at} selected={selected?.agent_type_id === at.agent_type_id} onSelect={() => { setSelected(at); setCreating(false) }} />
              ))}
            </>
          )}

          {deprecated.length > 0 && (
            <>
              <div style={{ padding: '8px 16px 4px', fontSize: 10, color: C.textFaint, fontWeight: 700, letterSpacing: '0.5px', textTransform: 'uppercase' }}>
                Deprecated
              </div>
              {deprecated.map(at => (
                <AgentTypeRow key={at.agent_type_id} at={at} selected={selected?.agent_type_id === at.agent_type_id} onSelect={() => { setSelected(at); setCreating(false) }} />
              ))}
            </>
          )}
        </div>
      </div>

      {/* Right detail / create */}
      <div style={{ flex: 1, overflow: 'hidden' }}>
        {creating && (
          <CreateProfileForm
            tenantId={tenantId}
            pools={pools.map(p => p.pool_id)}
            onSaved={() => { setCreating(false); refresh() }}
            onCancel={() => setCreating(false)}
            onError={onError}
          />
        )}
        {selected && !creating && (
          <ProfileDetail
            tenantId={tenantId}
            agentType={selected}
            pools={pools.map(p => p.pool_id)}
            onSaved={(updated) => { setSelected(updated); refresh() }}
            onDeprecated={() => { setSelected(null); refresh() }}
            onError={onError}
          />
        )}
        {!creating && !selected && (
          <div style={{
            flex: 1, height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
            flexDirection: 'column', gap: 8,
          }}>
            <div style={{ fontSize: 32 }}>🧑‍💼</div>
            <div style={{ fontSize: 14, color: C.textMid }}>Select a profile or create a new one</div>
            <div style={{ fontSize: 12, color: C.textFaint }}>
              Each profile represents a named human agent who can log in via Agent Assist UI
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── AgentTypeRow ──────────────────────────────────────────────────────────────
function AgentTypeRow({ at, selected, onSelect }: {
  at: RegistryAgentType; selected: boolean; onSelect: () => void
}) {
  return (
    <div
      onClick={onSelect}
      style={{
        padding: '7px 16px', cursor: 'pointer',
        background: selected ? C.accentMid : 'transparent',
        borderLeft: selected ? `2px solid ${C.accent}` : '2px solid transparent',
      }}
    >
      <div style={{ fontSize: 12, color: C.text, fontWeight: selected ? 600 : 400 }}>
        {at.agent_type_id}
      </div>
      <div style={{ fontSize: 11, color: C.textFaint, marginTop: 2 }}>
        {at.pools.map(p => p.pool_id).join(', ') || 'no pools assigned'}
      </div>
    </div>
  )
}

// ── CreateProfileForm ─────────────────────────────────────────────────────────
function CreateProfileForm({ tenantId, pools, onSaved, onCancel, onError }: {
  tenantId: string; pools: string[]
  onSaved:  () => void; onCancel: () => void; onError: (m: string) => void
}) {
  const [agentTypeId,   setAgentTypeId]   = useState('')
  const [selectedPools, setSelectedPools] = useState<string[]>([])
  const [maxConcurrent, setMaxConcurrent] = useState('5')
  const [role,          setRole]          = useState('primary')
  const [permissions,   setPermissions]   = useState('')
  const [saving,        setSaving]        = useState(false)

  async function handleSave() {
    if (!agentTypeId.trim()) { onError('Agent type ID is required'); return }
    if (selectedPools.length === 0) { onError('Assign at least one pool'); return }
    setSaving(true)
    try {
      await createHumanAgent(tenantId, {
        agent_type_id:            agentTypeId.trim(),
        role,
        pools:                    selectedPools,
        max_concurrent_sessions:  parseInt(maxConcurrent, 10) || 5,
        permissions:              permissions.split(',').map(p => p.trim()).filter(Boolean),
      })
      onSaved()
      onError('')
    } catch (e) { onError((e as Error).message) }
    finally { setSaving(false) }
  }

  function togglePool(pid: string) {
    setSelectedPools(prev => prev.includes(pid) ? prev.filter(p => p !== pid) : [...prev, pid])
  }

  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: 24 }}>
      <div style={{ fontSize: 14, fontWeight: 700, color: C.text, marginBottom: 20 }}>
        New Human Agent Profile
      </div>

      <div style={sectionStyle}>
        <div style={sectionLabelStyle}>Identity</div>
        <div style={{ marginBottom: 10 }}>
          <label style={fieldLabelStyle}>Agent Type ID <span style={{ color: C.danger }}>*</span></label>
          <input
            value={agentTypeId}
            onChange={e => setAgentTypeId(e.target.value)}
            style={inputStyle}
            placeholder="e.g. agente_humano_retencao_v1"
          />
          <div style={{ fontSize: 10, color: C.textFaint, marginTop: 3 }}>
            Used as the login identifier in Agent Assist UI
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 16px' }}>
          <div>
            <label style={fieldLabelStyle}>Role</label>
            <select value={role} onChange={e => setRole(e.target.value)} style={{ ...inputStyle, cursor: 'pointer' }}>
              <option value="primary">primary</option>
              <option value="specialist">specialist</option>
              <option value="supervisor">supervisor</option>
              <option value="evaluator">evaluator</option>
              <option value="reviewer">reviewer</option>
            </select>
          </div>
          <div>
            <label style={fieldLabelStyle}>Max Concurrent Sessions</label>
            <input
              type="number" min={1} max={20}
              value={maxConcurrent}
              onChange={e => setMaxConcurrent(e.target.value)}
              style={inputStyle}
            />
          </div>
        </div>
      </div>

      <div style={sectionStyle}>
        <div style={sectionLabelStyle}>Pool Assignment <span style={{ color: C.danger }}>*</span></div>
        {pools.length === 0 ? (
          <div style={{ fontSize: 12, color: C.textFaint }}>No pools available — create pools first in the Registry tab</div>
        ) : (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {pools.map(pid => (
              <button
                key={pid}
                onClick={() => togglePool(pid)}
                style={{
                  padding: '4px 10px', borderRadius: 4, fontSize: 11, cursor: 'pointer',
                  background: selectedPools.includes(pid) ? C.accentMid : 'transparent',
                  border: `1px solid ${selectedPools.includes(pid) ? C.accent : C.border}`,
                  color: selectedPools.includes(pid) ? C.accent : C.textFaint,
                }}
              >
                {pid}
              </button>
            ))}
          </div>
        )}
      </div>

      <div style={sectionStyle}>
        <div style={sectionLabelStyle}>Permissions (optional)</div>
        <label style={fieldLabelStyle}>Comma-separated MCP tool names</label>
        <input
          value={permissions}
          onChange={e => setPermissions(e.target.value)}
          style={inputStyle}
          placeholder="customer_get, contract_read, …"
        />
      </div>

      <div style={{ display: 'flex', gap: 10 }}>
        <button
          onClick={handleSave}
          disabled={saving}
          style={{ ...btnStyle, background: C.accent, color: '#000', opacity: saving ? 0.6 : 1 }}
        >
          {saving ? 'Creating…' : 'Create Profile'}
        </button>
        <button onClick={onCancel} style={{ ...btnStyle, background: 'transparent', border: `1px solid ${C.border}` }}>
          Cancel
        </button>
      </div>
    </div>
  )
}

// ── ProfileDetail ─────────────────────────────────────────────────────────────
function ProfileDetail({ tenantId, agentType, pools, onSaved, onDeprecated, onError }: {
  tenantId:     string
  agentType:    RegistryAgentType
  pools:        string[]
  onSaved:      (updated: RegistryAgentType) => void
  onDeprecated: () => void
  onError:      (m: string) => void
}) {
  const [maxConcurrent, setMaxConcurrent] = useState(String(agentType.max_concurrent_sessions))
  const [permissions,   setPermissions]   = useState((agentType.permissions ?? []).join(', '))
  const [saving,        setSaving]        = useState(false)
  const [confirmDep,    setConfirmDep]    = useState(false)
  const [deprecating,   setDeprecating]   = useState(false)
  const [modified,      setModified]      = useState(false)

  useEffect(() => {
    setMaxConcurrent(String(agentType.max_concurrent_sessions))
    setPermissions((agentType.permissions ?? []).join(', '))
    setModified(false)
    setConfirmDep(false)
  }, [agentType.agent_type_id])

  async function handleSave() {
    setSaving(true)
    try {
      const updated = await updateHumanAgent(tenantId, agentType.agent_type_id, {
        max_concurrent_sessions: parseInt(maxConcurrent, 10) || 5,
        permissions: permissions.split(',').map(p => p.trim()).filter(Boolean),
      })
      setModified(false)
      onSaved(updated)
      onError('')
    } catch (e) { onError((e as Error).message) }
    finally { setSaving(false) }
  }

  async function handleDeprecate() {
    setDeprecating(true)
    try {
      await deprecateHumanAgent(tenantId, agentType.agent_type_id)
      onDeprecated()
      onError('')
    } catch (e) {
      onError((e as Error).message)
      setDeprecating(false)
      setConfirmDep(false)
    }
  }

  const isActive = agentType.status === 'active'

  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: 24 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 700, color: C.text }}>
            {agentType.agent_type_id}
            {modified && <span style={{ color: C.accent, marginLeft: 6 }}>●</span>}
          </div>
          <div style={{ fontSize: 11, color: C.textFaint }}>
            framework: human · execution_model: {agentType.execution_model}
          </div>
        </div>
        <span style={{
          fontSize: 11, padding: '3px 10px', borderRadius: 4,
          background: isActive ? C.accentMid : '#1e293b',
          color:      isActive ? C.accent    : C.textFaint,
          border: `1px solid ${isActive ? C.accent : C.border}`,
        }}>
          {agentType.status}
        </span>
      </div>

      {/* Pools (read-only) */}
      <div style={sectionStyle}>
        <div style={sectionLabelStyle}>Pool Assignment</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {agentType.pools.map(p => (
            <span key={p.pool_id} style={{
              fontSize: 11, padding: '3px 10px', borderRadius: 4,
              background: C.accentMid, color: C.accent, border: `1px solid ${C.border}`,
            }}>{p.pool_id}</span>
          ))}
          {agentType.pools.length === 0 && (
            <span style={{ fontSize: 12, color: C.textFaint }}>No pools assigned</span>
          )}
        </div>
        <div style={{ fontSize: 10, color: C.textFaint, marginTop: 6 }}>
          Pool assignment is set at creation — re-create the profile to change pools
        </div>
      </div>

      {/* Editable fields */}
      <div style={sectionStyle}>
        <div style={sectionLabelStyle}>Settings</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 16px', marginBottom: 10 }}>
          <div>
            <label style={fieldLabelStyle}>Role</label>
            <div style={{ ...inputStyle, color: C.textFaint, display: 'flex', alignItems: 'center' }}>
              {agentType.role}
            </div>
          </div>
          <div>
            <label style={fieldLabelStyle}>Max Concurrent Sessions</label>
            <input
              type="number" min={1} max={20}
              value={maxConcurrent}
              onChange={e => { setMaxConcurrent(e.target.value); setModified(true) }}
              style={inputStyle}
              disabled={!isActive}
            />
          </div>
        </div>
        <div>
          <label style={fieldLabelStyle}>Permissions (comma-separated MCP tool names)</label>
          <input
            value={permissions}
            onChange={e => { setPermissions(e.target.value); setModified(true) }}
            style={inputStyle}
            placeholder="customer_get, contract_read, …"
            disabled={!isActive}
          />
        </div>
      </div>

      {/* Metadata */}
      <div style={{ ...sectionStyle, borderColor: 'transparent', paddingTop: 0 }}>
        <div style={{ fontSize: 11, color: C.textFaint }}>
          traffic_weight: {agentType.traffic_weight} · created: {new Date(agentType.created_at).toLocaleDateString()} · updated: {new Date(agentType.updated_at).toLocaleDateString()}
        </div>
      </div>

      {/* Actions */}
      {isActive && (
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          {modified && (
            <button
              onClick={handleSave}
              disabled={saving}
              style={{ ...btnStyle, background: C.accent, color: '#000', opacity: saving ? 0.6 : 1 }}
            >
              {saving ? 'Saving…' : 'Save Changes'}
            </button>
          )}
          {!confirmDep ? (
            <button
              onClick={() => setConfirmDep(true)}
              style={{ ...btnStyle, background: 'transparent', border: `1px solid ${C.warn}`, color: C.warn }}
            >
              Deprecate
            </button>
          ) : (
            <>
              <button
                onClick={handleDeprecate}
                disabled={deprecating}
                style={{ ...btnStyle, background: C.warn, color: '#000', opacity: deprecating ? 0.6 : 1 }}
              >
                {deprecating ? 'Deprecating…' : 'Confirm Deprecate'}
              </button>
              <button onClick={() => setConfirmDep(false)} style={{ ...btnStyle, background: 'transparent', border: `1px solid ${C.border}` }}>
                Cancel
              </button>
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ── ActionButtons ─────────────────────────────────────────────────────────────
function ActionButtons({ inst, acting, onAction }: {
  inst:     RegistryInstance
  acting:   boolean
  onAction: (a: InstanceAction) => void
}) {
  if (acting) return <span style={{ fontSize: 11, color: C.textFaint }}>Working…</span>

  const s = inst.status
  return (
    <div style={{ display: 'flex', gap: 4 }}>
      {(s === 'ready' || s === 'busy') && (
        <ActionBtn label="Pause" color={C.warn} onClick={() => onAction('pause')} />
      )}
      {s === 'paused' && (
        <ActionBtn label="Resume" color={C.ready} onClick={() => onAction('resume')} />
      )}
      {(s !== 'logout') && (
        <ActionBtn label="Force Logout" color={C.danger} onClick={() => onAction('force_logout')} />
      )}
    </div>
  )
}

function ActionBtn({ label, color, onClick }: { label: string; color: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: '3px 9px', borderRadius: 4, fontSize: 10, cursor: 'pointer',
        background: 'transparent', border: `1px solid ${color}`, color,
      }}
    >
      {label}
    </button>
  )
}

// ── StatusBadge ───────────────────────────────────────────────────────────────
function StatusBadge({ status }: { status: string }) {
  const color = STATUS_COLOR[status] ?? C.textFaint
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4,
      fontSize: 11, padding: '2px 8px', borderRadius: 4,
      background: color + '1a', border: `1px solid ${color}40`, color,
    }}>
      <span style={{ width: 5, height: 5, borderRadius: '50%', background: color, display: 'inline-block' }} />
      {status}
    </span>
  )
}

// ── Shared styles ─────────────────────────────────────────────────────────────
const sectionStyle: React.CSSProperties = {
  borderBottom: `1px solid ${C.borderFaint}`,
  paddingBottom: 16, marginBottom: 16,
}
const sectionLabelStyle: React.CSSProperties = {
  fontSize: 10, fontWeight: 700, color: C.textFaint,
  letterSpacing: '0.8px', textTransform: 'uppercase',
  marginBottom: 10,
}
const fieldLabelStyle: React.CSSProperties = {
  display: 'block', fontSize: 11, color: C.textMid, marginBottom: 4,
}
const inputStyle: React.CSSProperties = {
  background:   '#0d1117',
  border:       `1px solid ${C.border}`,
  borderRadius: 6,
  color:        C.text,
  fontSize:     12,
  padding:      '5px 10px',
  outline:      'none',
  width:        '100%',
  boxSizing:    'border-box',
}
const btnStyle: React.CSSProperties = {
  padding:      '6px 14px',
  borderRadius: 5,
  border:       'none',
  color:        C.text,
  cursor:       'pointer',
  fontSize:     12,
  fontWeight:   600,
}
const backBtnStyle: React.CSSProperties = {
  background:   'transparent',
  border:       `1px solid ${C.border}`,
  borderRadius: 4,
  color:        C.textMid,
  cursor:       'pointer',
  fontSize:     11,
  padding:      '4px 10px',
}

import type React from 'react'
