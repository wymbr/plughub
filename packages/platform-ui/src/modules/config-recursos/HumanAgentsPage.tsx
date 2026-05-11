/**
 * HumanAgentsPage.tsx
 * Human agent management: live instance status + profile CRUD.
 * Migrated from operator-console/HumanAgentPanel.tsx — uses platform-ui design system.
 */
import React, { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import { AgentType, AgentInstance, Pool } from '@/types'
import * as registryApi from '@/api/registry'
import Badge from '@/components/ui/Badge'
import Button from '@/components/ui/Button'
import Spinner from '@/components/ui/Spinner'
import EmptyState from '@/components/ui/EmptyState'

// HumanAgentTypeExt — same as AgentType, all fields already present in base
type HumanAgentTypeExt = AgentType

// ── Helpers ───────────────────────────────────────────────────────────────────

type InstanceStatus = 'ready' | 'busy' | 'paused' | 'login' | 'logout'

const STATUS_VARIANT: Record<string, 'active' | 'suspended' | 'failed' | 'default'> = {
  ready:  'active',
  busy:   'default',
  paused: 'suspended',
  login:  'default',
  logout: 'failed',
}

const STATUS_LABELS = ['all', 'ready', 'busy', 'paused', 'login', 'logout'] as const

// ── Main page ─────────────────────────────────────────────────────────────────

const HumanAgentsPage: React.FC = () => {
  const { t } = useTranslation('configRecursos')
  const [innerTab, setInnerTab] = useState<'live' | 'profiles'>('live')
  const [error,    setError]    = useState('')

  return (
    <div className="border border-lightGray rounded-lg overflow-hidden bg-white" style={{ minHeight: 520 }}>
      {/* Inner tab bar */}
      <div className="flex items-center gap-1 px-4 py-2 border-b border-lightGray bg-gray-50">
        <span className="text-sm font-bold text-dark mr-4">{t('humanAgents.title')}</span>
        {(['live', 'profiles'] as const).map(tabId => (
          <button
            key={tabId}
            onClick={() => { setInnerTab(tabId); setError('') }}
            className={`text-xs px-4 py-1.5 rounded-md font-medium transition-colors border ${
              innerTab === tabId
                ? 'bg-primary/10 border-primary text-primary'
                : 'bg-white border-lightGray text-gray hover:border-secondary hover:text-secondary'
            }`}
          >
            {tabId === 'live' ? t('humanAgents.liveTab') : t('humanAgents.profilesTab')}
          </button>
        ))}
      </div>

      {error && (
        <div className="px-4 py-2 bg-red/5 border-b border-red/30 text-red text-xs">{error}</div>
      )}

      <div className="flex overflow-hidden" style={{ minHeight: 460 }}>
        {innerTab === 'live'     && <LiveTab     onError={setError} />}
        {innerTab === 'profiles' && <ProfilesTab onError={setError} />}
      </div>
    </div>
  )
}

// ── Live Status tab ───────────────────────────────────────────────────────────

function LiveTab({ onError }: { onError: (m: string) => void }) {
  const { t } = useTranslation('configRecursos')
  const { session } = useAuth()
  const [instances,  setInstances]  = useState<AgentInstance[]>([])
  const [loading,    setLoading]    = useState(true)
  const [filter,     setFilter]     = useState<string>('all')
  const [acting,     setActing]     = useState<string | null>(null)

  // Arc 9 — non-empty means supervisor scope; admin keeps empty = unrestricted
  const supervisedAgentTypes = session?.supervisedAgentTypes ?? []

  const load = useCallback(async () => {
    if (!session) return
    setLoading(true)
    try {
      const result = await registryApi.listHumanInstances(session.tenantId)
      const items  = result.items ?? []
      // Transparent scope filter: only show instances whose agent_type is supervised
      setInstances(
        supervisedAgentTypes.length > 0
          ? items.filter(i => supervisedAgentTypes.includes(i.agent_type_id))
          : items,
      )
    } catch (e) {
      onError((e as Error).message)
    } finally { setLoading(false) }
  }, [session, supervisedAgentTypes, onError])

  useEffect(() => { load() }, [load])

  const filtered = filter === 'all' ? instances : instances.filter(i => i.status === filter)

  async function doAction(inst: AgentInstance, action: 'pause' | 'resume' | 'force_logout') {
    if (!session) return
    setActing(inst.instance_id)
    try {
      await registryApi.instanceAction(inst.instance_id, action, session.tenantId)
      await load()
      onError('')
    } catch (e) {
      onError((e as Error).message)
    } finally { setActing(null) }
  }

  if (loading) {
    return <div className="flex-1 flex justify-center items-center py-12"><Spinner /></div>
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-lightGray">
        <span className="text-xs text-gray">{t('humanAgents.tracked', { count: instances.length })}</span>
        <div className="flex gap-1">
          {STATUS_LABELS.map(statusLabel => (
            <button
              key={statusLabel}
              onClick={() => setFilter(statusLabel)}
              className={`text-xs px-3 py-1 rounded border transition-colors ${
                filter === statusLabel
                  ? 'bg-primary/10 border-primary text-primary font-medium'
                  : 'bg-white border-lightGray text-gray hover:border-secondary'
              }`}
            >
              {t(`humanAgents.statusLabels.${statusLabel}`)}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-y-auto">
        {filtered.length === 0 ? (
          <EmptyState
            title={filter === 'all' ? t('humanAgents.emptyLive') : t('humanAgents.emptyLiveWithStatus', { status: filter })}
            description={t('humanAgents.emptyLiveDesc')}
          />
        ) : (
          <table className="w-full text-xs border-collapse">
            <thead className="bg-gray-50 sticky top-0">
              <tr>
                {[
                  t('humanAgents.tableHeaders.instanceId'),
                  t('humanAgents.tableHeaders.agentType'),
                  t('humanAgents.tableHeaders.pool'),
                  t('humanAgents.tableHeaders.status'),
                  t('humanAgents.tableHeaders.updated'),
                  t('humanAgents.tableHeaders.actions'),
                ].map(h => (
                  <th key={h} className="text-left px-3 py-2 text-gray font-semibold uppercase tracking-wide text-xs border-b border-lightGray">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map(inst => (
                <tr
                  key={inst.instance_id}
                  className={`border-b border-lightGray ${acting === inst.instance_id ? 'bg-tableAlt' : 'hover:bg-gray-50'}`}
                >
                  <td className="px-3 py-2 font-mono text-gray">{inst.instance_id}</td>
                  <td className="px-3 py-2 text-dark font-medium">{inst.agent_type_id}</td>
                  <td className="px-3 py-2">
                    <Badge variant="default" className="text-xs">{inst.pool_id}</Badge>
                  </td>
                  <td className="px-3 py-2">
                    <Badge variant={STATUS_VARIANT[inst.status] ?? 'default'}>
                      {inst.status}
                    </Badge>
                  </td>
                  <td className="px-3 py-2 text-gray">
                    {new Date(inst.updated_at).toLocaleTimeString('pt-BR')}
                  </td>
                  <td className="px-3 py-2">
                    {acting === inst.instance_id ? (
                      <span className="text-gray text-xs">{t('humanAgents.actions.waiting')}</span>
                    ) : (
                      <InstanceActions status={inst.status as InstanceStatus} onAction={a => doAction(inst, a)} t={t} />
                    )}
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

function InstanceActions({ status, onAction, t }: {
  status:   InstanceStatus
  onAction: (a: 'pause' | 'resume' | 'force_logout') => void
  t:        (key: string) => string
}) {
  return (
    <div className="flex gap-1">
      {(status === 'ready' || status === 'busy') && (
        <ActionChip label={t('humanAgents.actions.pause')} variant="warning" onClick={() => onAction('pause')} />
      )}
      {status === 'paused' && (
        <ActionChip label={t('humanAgents.actions.resume')} variant="success" onClick={() => onAction('resume')} />
      )}
      {status !== 'logout' && (
        <ActionChip label={t('humanAgents.actions.forceLogout')} variant="danger" onClick={() => onAction('force_logout')} />
      )}
    </div>
  )
}

function ActionChip({ label, variant, onClick }: {
  label:   string
  variant: 'warning' | 'success' | 'danger'
  onClick: () => void
}) {
  const cls = {
    warning: 'border-warning text-warning hover:bg-warning/5',
    success: 'border-green text-green hover:bg-green/5',
    danger:  'border-red text-red hover:bg-red/5',
  }[variant]
  return (
    <button
      onClick={onClick}
      className={`text-xs px-2 py-0.5 rounded border bg-white transition-colors ${cls}`}
    >
      {label}
    </button>
  )
}

// ── Profiles tab ──────────────────────────────────────────────────────────────

function ProfilesTab({ onError }: { onError: (m: string) => void }) {
  const { t } = useTranslation('configRecursos')
  const { session } = useAuth()
  const [agentTypes, setAgentTypes] = useState<HumanAgentTypeExt[]>([])
  const [pools,      setPools]      = useState<Pool[]>([])
  const [loading,    setLoading]    = useState(true)
  const [selected,   setSelected]   = useState<HumanAgentTypeExt | null>(null)
  const [creating,   setCreating]   = useState(false)

  const load = useCallback(async () => {
    if (!session) return
    setLoading(true)
    try {
      const [atResult, poolResult] = await Promise.all([
        registryApi.listHumanAgentTypes(session.tenantId),
        registryApi.listPools(session.tenantId),
      ])
      setAgentTypes((atResult.items ?? []) as HumanAgentTypeExt[])
      setPools(poolResult.items ?? [])
    } catch (e) {
      onError((e as Error).message)
    } finally { setLoading(false) }
  }, [session, onError])

  useEffect(() => { load() }, [load])

  const active     = agentTypes.filter(a => a.status !== 'deprecated')
  const deprecated = agentTypes.filter(a => a.status === 'deprecated')

  if (loading) {
    return <div className="flex-1 flex justify-center items-center py-12"><Spinner /></div>
  }

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Left sidebar */}
      <div className="w-72 flex-shrink-0 border-r border-lightGray flex flex-col overflow-y-auto bg-gray-50">
        <div className="px-4 py-3 border-b border-lightGray flex items-center justify-between">
          <div>
            <div className="text-xs font-bold text-dark">{t('humanAgents.profiles.title')}</div>
            <div className="text-xs text-gray mt-0.5">{t('humanAgents.profiles.active', { count: active.length })}</div>
          </div>
          <button
            onClick={() => { setCreating(true); setSelected(null) }}
            className={`text-xs px-3 py-1 rounded border transition-colors ${
              creating
                ? 'bg-primary/10 border-primary text-primary'
                : 'bg-white border-lightGray text-gray hover:border-primary hover:text-primary'
            }`}
          >
            {t('humanAgents.profiles.newButton')}
          </button>
        </div>

        <div className="flex-1 py-2">
          {agentTypes.length === 0 && (
            <div className="px-4 py-5 text-center text-xs text-gray">
              {t('humanAgents.profiles.emptyProfiles')}
            </div>
          )}

          {active.length > 0 && (
            <>
              <div className="px-4 py-1 text-xs font-bold text-gray uppercase tracking-wider">{t('humanAgents.profiles.activeSection')}</div>
              {active.map(at => (
                <ProfileRow key={at.agent_type_id} at={at} selected={selected?.agent_type_id === at.agent_type_id}
                  onSelect={() => { setSelected(at); setCreating(false) }} />
              ))}
            </>
          )}

          {deprecated.length > 0 && (
            <>
              <div className="px-4 pt-3 pb-1 text-xs font-bold text-gray uppercase tracking-wider">{t('humanAgents.profiles.deprecatedSection')}</div>
              {deprecated.map(at => (
                <ProfileRow key={at.agent_type_id} at={at} selected={selected?.agent_type_id === at.agent_type_id}
                  onSelect={() => { setSelected(at); setCreating(false) }} />
              ))}
            </>
          )}
        </div>
      </div>

      {/* Right detail */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {creating && (
          <CreateProfileForm
            tenantId={session!.tenantId}
            poolIds={pools.map(p => p.pool_id)}
            onSaved={() => { setCreating(false); load() }}
            onCancel={() => setCreating(false)}
            onError={onError}
          />
        )}
        {selected && !creating && (
          <ProfileDetail
            tenantId={session!.tenantId}
            agentType={selected}
            onSaved={updated => { setSelected(updated as HumanAgentTypeExt); load() }}
            onDeprecated={() => { setSelected(null); load() }}
            onError={onError}
          />
        )}
        {!creating && !selected && (
          <EmptyState
            title={t('humanAgents.profiles.selectPrompt')}
            description={t('humanAgents.profiles.selectDesc')}
          />
        )}
      </div>
    </div>
  )
}

function ProfileRow({ at, selected, onSelect }: {
  at: HumanAgentTypeExt; selected: boolean; onSelect: () => void
}) {
  return (
    <button
      onClick={onSelect}
      className={`w-full text-left px-4 py-2 transition-colors border-l-2 ${
        selected
          ? 'bg-primary/5 border-l-primary'
          : 'bg-transparent border-l-transparent hover:bg-gray-100'
      }`}
    >
      <div className={`text-xs font-medium ${selected ? 'text-primary' : 'text-dark'}`}>
        {at.agent_type_id}
      </div>
      <div className="text-xs text-gray mt-0.5">
        {Array.isArray(at.pools) && at.pools.length > 0
          ? at.pools.map((p: any) => p.pool_id ?? String(p)).join(', ')
          : '—'}
      </div>
    </button>
  )
}

// ── CreateProfileForm ─────────────────────────────────────────────────────────

function CreateProfileForm({ tenantId, poolIds, onSaved, onCancel, onError }: {
  tenantId: string; poolIds: string[]
  onSaved:  () => void; onCancel: () => void; onError: (m: string) => void
}) {
  const { t } = useTranslation('configRecursos')
  const [agentTypeId,   setAgentTypeId]   = useState('')
  const [role,          setRole]          = useState('primary')
  const [maxConcurrent, setMaxConcurrent] = useState('5')
  const [selectedPools, setSelectedPools] = useState<string[]>([])
  const [permissions,   setPermissions]   = useState('')
  const [saving,        setSaving]        = useState(false)

  function togglePool(pid: string) {
    setSelectedPools(prev => prev.includes(pid) ? prev.filter(p => p !== pid) : [...prev, pid])
  }

  async function handleSave() {
    if (!agentTypeId.trim())    { onError(t('humanAgents.createProfile.validation.agentTypeIdRequired')); return }
    if (selectedPools.length === 0) { onError(t('humanAgents.createProfile.validation.poolsRequired'));    return }
    setSaving(true)
    try {
      await registryApi.createHumanAgentType({
        agent_type_id:           agentTypeId.trim(),
        role,
        pools:                   selectedPools,
        max_concurrent_sessions: parseInt(maxConcurrent, 10) || 5,
        permissions:             permissions.split(',').map(p => p.trim()).filter(Boolean),
      }, tenantId)
      onSaved(); onError('')
    } catch (e) { onError((e as Error).message) }
    finally { setSaving(false) }
  }

  const inputCls = 'w-full px-3 py-1.5 text-xs border border-lightGray rounded-md focus:outline-none focus:border-secondary bg-white text-dark'

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="text-sm font-bold text-dark mb-5">{t('humanAgents.createProfile.title')}</div>

      <SectionBlock title={t('humanAgents.createProfile.section.identity')}>
        <div className="mb-3">
          <FieldLabel>{t('humanAgents.createProfile.fields.agentTypeId')} <span className="text-red">*</span></FieldLabel>
          <input className={inputCls} value={agentTypeId}
            onChange={e => setAgentTypeId(e.target.value)} placeholder={t('humanAgents.createProfile.fields.agentTypeIdPlaceholder')} />
          <div className="text-xs text-gray mt-1">{t('humanAgents.createProfile.fields.agentTypeIdHelper')}</div>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <FieldLabel>{t('humanAgents.createProfile.fields.role')}</FieldLabel>
            <select className={`${inputCls} cursor-pointer`} value={role} onChange={e => setRole(e.target.value)}>
              {['primary', 'specialist', 'supervisor', 'evaluator', 'reviewer'].map(r => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          </div>
          <div>
            <FieldLabel>{t('humanAgents.createProfile.fields.maxConcurrent')}</FieldLabel>
            <input type="number" min={1} max={20} className={inputCls}
              value={maxConcurrent} onChange={e => setMaxConcurrent(e.target.value)} />
          </div>
        </div>
      </SectionBlock>

      <SectionBlock title={<>{t('humanAgents.createProfile.section.pools')} <span className="text-red">*</span></>}>
        {poolIds.length === 0 ? (
          <div className="text-xs text-gray">{t('humanAgents.profiles.noPoolsAvailable')}</div>
        ) : (
          <div className="flex flex-wrap gap-2">
            {poolIds.map(pid => (
              <button key={pid} onClick={() => togglePool(pid)}
                className={`text-xs px-3 py-1 rounded border transition-colors ${
                  selectedPools.includes(pid)
                    ? 'bg-primary/10 border-primary text-primary font-medium'
                    : 'bg-white border-lightGray text-gray hover:border-secondary'
                }`}
              >
                {pid}
              </button>
            ))}
          </div>
        )}
      </SectionBlock>

      <SectionBlock title={t('humanAgents.createProfile.section.permissions')}>
        <FieldLabel>{t('humanAgents.createProfile.fields.permissions')}</FieldLabel>
        <input className={inputCls} value={permissions}
          onChange={e => setPermissions(e.target.value)} placeholder={t('humanAgents.createProfile.fields.permissionsPlaceholder')} />
      </SectionBlock>

      <div className="flex gap-3 mt-2">
        <Button variant="primary" onClick={handleSave} disabled={saving}>
          {saving ? t('humanAgents.createProfile.buttons.creating') : t('humanAgents.createProfile.buttons.create')}
        </Button>
        <Button variant="ghost" onClick={onCancel}>{t('humanAgents.createProfile.buttons.cancel')}</Button>
      </div>
    </div>
  )
}

// ── ProfileDetail ─────────────────────────────────────────────────────────────

function ProfileDetail({ tenantId, agentType, onSaved, onDeprecated, onError }: {
  tenantId:    string
  agentType:   HumanAgentTypeExt
  onSaved:     (updated: AgentType) => void
  onDeprecated:() => void
  onError:     (m: string) => void
}) {
  const { t } = useTranslation('configRecursos')
  const [maxConcurrent, setMaxConcurrent] = useState(String(agentType.max_concurrent_sessions ?? 5))
  const [permissions,   setPermissions]   = useState((agentType.permissions ?? []).join(', '))
  const [saving,        setSaving]        = useState(false)
  const [confirmDep,    setConfirmDep]    = useState(false)
  const [deprecating,   setDeprecating]   = useState(false)
  const [modified,      setModified]      = useState(false)

  useEffect(() => {
    setMaxConcurrent(String(agentType.max_concurrent_sessions ?? 5))
    setPermissions((agentType.permissions ?? []).join(', '))
    setModified(false); setConfirmDep(false)
  }, [agentType.agent_type_id])

  const isActive = agentType.status !== 'deprecated'

  const inputCls = `w-full px-3 py-1.5 text-xs border border-lightGray rounded-md focus:outline-none focus:border-secondary bg-white text-dark ${!isActive ? 'opacity-50 cursor-not-allowed' : ''}`

  async function handleSave() {
    setSaving(true)
    try {
      const updated = await registryApi.updateHumanAgentType(agentType.agent_type_id, {
        max_concurrent_sessions: parseInt(maxConcurrent, 10) || 5,
        permissions: permissions.split(',').map(p => p.trim()).filter(Boolean),
      }, tenantId)
      setModified(false); onSaved(updated); onError('')
    } catch (e) { onError((e as Error).message) }
    finally { setSaving(false) }
  }

  async function handleDeprecate() {
    setDeprecating(true)
    try {
      await registryApi.deleteAgentType(agentType.agent_type_id, tenantId)
      onDeprecated(); onError('')
    } catch (e) {
      onError((e as Error).message)
      setDeprecating(false); setConfirmDep(false)
    }
  }

  // pools from the API are full Pool objects — extract pool_id strings
  const poolList: string[] = Array.isArray(agentType.pools)
    ? agentType.pools.map((p: any) => p.pool_id ?? String(p))
    : []

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="flex items-start justify-between mb-5">
        <div>
          <div className="text-base font-bold text-dark flex items-center gap-2">
            {agentType.agent_type_id}
            {modified && <span className="text-secondary text-sm">●</span>}
          </div>
          <div className="text-xs text-gray mt-0.5">{t('humanAgents.profileDetail.framework')} · {t('humanAgents.profileDetail.role')} {agentType.role ?? '—'}</div>
        </div>
        <Badge variant={isActive ? 'active' : 'default'}>{agentType.status}</Badge>
      </div>

      {/* Pools (read-only) */}
      <SectionBlock title={t('humanAgents.profileDetail.poolsAssigned')}>
        <div className="flex flex-wrap gap-2">
          {poolList.length === 0
            ? <span className="text-xs text-gray">{t('humanAgents.profileDetail.noPools')}</span>
            : poolList.map(pid => (
                <Badge key={pid} variant="default" className="text-xs">{pid}</Badge>
              ))
          }
        </div>
        <div className="text-xs text-gray mt-2">
          {t('humanAgents.profileDetail.poolsNote')}
        </div>
      </SectionBlock>

      {/* Editable */}
      <SectionBlock title={t('humanAgents.profileDetail.settings')}>
        <div className="grid grid-cols-2 gap-4 mb-3">
          <div>
            <FieldLabel>{t('humanAgents.createProfile.fields.role')}</FieldLabel>
            <div className={`${inputCls} opacity-60 cursor-not-allowed`}>{agentType.role ?? '—'}</div>
          </div>
          <div>
            <FieldLabel>{t('humanAgents.createProfile.fields.maxConcurrent')}</FieldLabel>
            <input type="number" min={1} max={20} className={inputCls}
              value={maxConcurrent}
              onChange={e => { setMaxConcurrent(e.target.value); setModified(true) }}
              disabled={!isActive}
            />
          </div>
        </div>
        <div>
          <FieldLabel>{t('humanAgents.createProfile.fields.permissions')}</FieldLabel>
          <input className={inputCls} value={permissions}
            onChange={e => { setPermissions(e.target.value); setModified(true) }}
            placeholder={t('humanAgents.createProfile.fields.permissionsPlaceholder')}
            disabled={!isActive}
          />
        </div>
      </SectionBlock>

      {/* Metadata */}
      <div className="flex gap-6 text-xs text-gray mb-5">
        {agentType.updated_at && <span>{t('humanAgents.profileDetail.metadata.updated')} {new Date(agentType.updated_at).toLocaleDateString('pt-BR')}</span>}
        <span>{t('humanAgents.profileDetail.metadata.created')} {new Date(agentType.created_at).toLocaleDateString('pt-BR')}</span>
      </div>

      {/* Actions */}
      {isActive && (
        <div className="flex gap-3 items-center">
          {modified && (
            <Button variant="primary" onClick={handleSave} disabled={saving}>
              {saving ? t('humanAgents.profileDetail.buttons.saving') : t('humanAgents.profileDetail.buttons.save')}
            </Button>
          )}
          {!confirmDep ? (
            <Button variant="ghost" onClick={() => setConfirmDep(true)}
              className="text-warning border-warning/30 hover:bg-warning/5">
              {t('humanAgents.profileDetail.buttons.deprecate')}
            </Button>
          ) : (
            <>
              <Button variant="primary" onClick={handleDeprecate} disabled={deprecating}
                className="bg-warning border-warning hover:bg-warning/90">
                {deprecating ? t('humanAgents.profileDetail.buttons.deprecating') : t('humanAgents.profileDetail.buttons.deprecateConfirm')}
              </Button>
              <Button variant="ghost" onClick={() => setConfirmDep(false)}>{t('humanAgents.profileDetail.buttons.cancel')}</Button>
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ── Shared helpers ────────────────────────────────────────────────────────────

function SectionBlock({ title, children }: { title: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="mb-5 pb-5 border-b border-lightGray last:border-0">
      <div className="text-xs font-bold text-gray uppercase tracking-wider mb-3">{title}</div>
      {children}
    </div>
  )
}

function FieldLabel({ children }: { children: React.ReactNode }) {
  return <label className="block text-xs font-medium text-gray mb-1">{children}</label>
}

export default HumanAgentsPage
