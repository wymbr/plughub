import React, { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import * as registryApi from '@/api/registry'
import { AgentType, Pool, Skill } from '@/types'
import Button from '@/components/ui/Button'
import Table from '@/components/ui/Table'
import Modal from '@/components/ui/Modal'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import Badge from '@/components/ui/Badge'
import EmptyState from '@/components/ui/EmptyState'
import Spinner from '@/components/ui/Spinner'

const FRAMEWORKS = [
  'plughub-native',
  'human',
  'external-mcp',
  'langgraph',
  'crewai',
  'anthropic_sdk',
  'azure_ai',
  'google_vertex',
  'generic_mcp',
]

const ROLES = ['executor', 'orchestrator', 'evaluator', 'supervisor']

const AgentTypesPage: React.FC = () => {
  const { session } = useAuth()
  const { t } = useTranslation('configRecursos')
  const [agentTypes, setAgentTypes] = useState<AgentType[]>([])
  const [pools, setPools] = useState<Pool[]>([])
  const [skills, setSkills] = useState<Skill[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [isOpen, setIsOpen] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [error, setError] = useState('')

  // Detail / deprecate dialog
  const [selected, setSelected] = useState<AgentType | null>(null)
  const [isDetailOpen, setIsDetailOpen] = useState(false)
  const [confirmDeprecate, setConfirmDeprecate] = useState(false)
  const [isDeprecating, setIsDeprecating] = useState(false)

  const [formData, setFormData] = useState({
    agent_type_id: '',
    framework: 'plughub-native',
    execution_model: 'stateless' as 'stateless' | 'stateful',
    role: 'executor',
    max_concurrent_sessions: 1,
    prompt_id: '',
    pools: [] as string[],
    skills: [] as string[],
  })

  const frameworkOptions = FRAMEWORKS.map(f => ({ value: f, label: f }))
  const execModelOptions = [
    { value: 'stateless', label: 'stateless' },
    { value: 'stateful',  label: 'stateful'  },
  ]
  const roleOptions = ROLES.map(r => ({ value: r, label: r }))

  useEffect(() => { loadData() }, [])

  const loadData = async () => {
    if (!session) return
    setIsLoading(true)
    try {
      const [agentsResult, poolsResult, skillsResult] = await Promise.all([
        registryApi.listAgentTypes(session.tenantId),
        registryApi.listPools(session.tenantId),
        registryApi.listSkills(session.tenantId),
      ])
      setAgentTypes(agentsResult.items || [])
      setPools(poolsResult.items || [])
      setSkills(skillsResult.items || [])
    } catch {
      setError('Failed to load data')
    } finally {
      setIsLoading(false)
    }
  }

  const handleOpenCreate = () => {
    setFormData({
      agent_type_id: '',
      framework: 'plughub-native',
      execution_model: 'stateless',
      role: 'executor',
      max_concurrent_sessions: 1,
      prompt_id: '',
      pools: [],
      skills: [],
    })
    setError('')
    setIsOpen(true)
  }

  const handleClose = () => setIsOpen(false)

  const handleSubmit = async () => {
    if (!session || !formData.agent_type_id.trim()) {
      setError('Agent Type ID is required')
      return
    }
    if (formData.pools.length === 0) {
      setError('Select at least one pool')
      return
    }
    setIsSaving(true)
    setError('')
    try {
      await registryApi.createAgentType({
        agent_type_id:           formData.agent_type_id,
        framework:               formData.framework,
        execution_model:         formData.execution_model,
        role:                    formData.role,
        max_concurrent_sessions: formData.max_concurrent_sessions,
        prompt_id:               formData.prompt_id || undefined,
        pools:                   formData.pools,
        skills:                  formData.skills.map(s => ({ skill_id: s, version_policy: 'stable' })),
      }, session.tenantId)
      await loadData()
      handleClose()
    } catch {
      setError('Failed to save agent type')
    } finally {
      setIsSaving(false)
    }
  }

  const handleDeprecate = async () => {
    if (!session || !selected) return
    setIsDeprecating(true)
    try {
      await registryApi.deleteAgentType(selected.agent_type_id, session.tenantId)
      await loadData()
      setIsDetailOpen(false)
      setSelected(null)
      setConfirmDeprecate(false)
    } catch {
      setError('Failed to deprecate agent type')
    } finally {
      setIsDeprecating(false)
    }
  }

  const roleColor = (role: string) => {
    if (role === 'orchestrator') return 'text-indigo-600 bg-indigo-50 border-indigo-200'
    if (role === 'evaluator')    return 'text-yellow-600 bg-yellow-50 border-yellow-200'
    if (role === 'supervisor')   return 'text-purple-600 bg-purple-50 border-purple-200'
    return 'text-cyan-600 bg-cyan-50 border-cyan-200'
  }

  const columns = [
    { key: 'agent_type_id', label: t('agentTypes.fields.agentTypeId') },
    {
      key: 'framework',
      label: t('agentTypes.fields.framework'),
      render: (fw: string) => (
        <span className="text-xs font-mono text-secondary">{fw}</span>
      ),
    },
    {
      key: 'role',
      label: t('agentTypes.fields.role'),
      render: (role: string) => (
        <span className={`text-xs font-semibold px-2 py-0.5 rounded border ${roleColor(role)}`}>
          {role}
        </span>
      ),
    },
    {
      key: 'pools',
      label: t('agentTypes.fields.pools'),
      render: (poolList: Array<{ pool_id: string }>) => (
        <div className="flex gap-1 flex-wrap">
          {(poolList || []).map(p => (
            <span key={p.pool_id} className="text-xs px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/20">
              {p.pool_id}
            </span>
          ))}
        </div>
      ),
    },
    {
      key: 'execution_model',
      label: t('agentTypes.fields.executionModel'),
      render: (m: string) => <span className="text-xs text-gray">{m}</span>,
    },
    {
      key: 'status',
      label: t('agentTypes.fields.status'),
      render: (s: string) => (
        <Badge variant={s === 'active' ? 'active' : 'default'}>{s}</Badge>
      ),
    },
  ]

  return (
    <div>
      <div className="mb-6 flex justify-end">
        <Button variant="primary" onClick={handleOpenCreate}>
          + {t('agentTypes.createAgentType')}
        </Button>
      </div>

      {error && (
        <div className="mb-4 bg-red/10 border border-red text-red px-3 py-2 rounded text-sm flex justify-between">
          <span>{error}</span>
          <button onClick={() => setError('')} className="font-bold">✕</button>
        </div>
      )}

      {isLoading ? (
        <div className="flex justify-center py-8"><Spinner /></div>
      ) : agentTypes.length === 0 ? (
        <EmptyState
          title={t('agentTypes.empty')}
          action={<Button onClick={handleOpenCreate}>Create first</Button>}
        />
      ) : (
        <Table
          columns={columns}
          data={agentTypes}
          keyField="agent_type_id"
          onRowClick={(at: AgentType) => { setSelected(at); setConfirmDeprecate(false); setIsDetailOpen(true) }}
        />
      )}

      {/* Create modal */}
      <Modal
        isOpen={isOpen}
        onClose={handleClose}
        title={t('agentTypes.createAgentType')}
        footer={
          <>
            <Button variant="ghost" onClick={handleClose}>Cancel</Button>
            <Button variant="primary" onClick={handleSubmit} disabled={isSaving}>
              {isSaving ? 'Saving...' : 'Save'}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          {error && (
            <div className="bg-red/10 border border-red text-red px-3 py-2 rounded text-sm">
              {error}
            </div>
          )}

          <Input
            label={`${t('agentTypes.fields.agentTypeId')} *`}
            value={formData.agent_type_id}
            onChange={e => setFormData({ ...formData, agent_type_id: e.target.value })}
            placeholder="e.g. agente_sac_ia_v2"
            required
          />
          <p className="text-xs text-gray -mt-3">
            Format: <code className="text-primary">name_v&#123;n&#125;</code>
          </p>

          <div className="grid grid-cols-2 gap-4">
            <Select
              label={`${t('agentTypes.fields.framework')} *`}
              value={formData.framework}
              onChange={e => setFormData({ ...formData, framework: e.target.value })}
              options={frameworkOptions}
            />
            <Select
              label={`${t('agentTypes.fields.executionModel')} *`}
              value={formData.execution_model}
              onChange={e => setFormData({ ...formData, execution_model: e.target.value as 'stateless' | 'stateful' })}
              options={execModelOptions}
            />
            <Select
              label={t('agentTypes.fields.role')}
              value={formData.role}
              onChange={e => setFormData({ ...formData, role: e.target.value })}
              options={roleOptions}
            />
            <Input
              label={t('agentTypes.fields.maxConcurrent')}
              type="number"
              value={formData.max_concurrent_sessions}
              onChange={e => setFormData({ ...formData, max_concurrent_sessions: parseInt(e.target.value) || 1 })}
            />
          </div>

          <Input
            label="Prompt ID"
            value={formData.prompt_id}
            onChange={e => setFormData({ ...formData, prompt_id: e.target.value })}
            placeholder="Optional"
          />

          <div>
            <label className="text-sm font-semibold text-dark mb-2 block">
              {t('agentTypes.fields.pools')} *
            </label>
            <div className="space-y-1 max-h-36 overflow-y-auto border border-lightGray rounded p-2">
              {pools.length === 0 && <span className="text-xs text-gray">No pools available</span>}
              {pools.map(pool => (
                <label key={pool.pool_id} className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={formData.pools.includes(pool.pool_id)}
                    onChange={e => setFormData(prev => ({
                      ...prev,
                      pools: e.target.checked
                        ? [...prev.pools, pool.pool_id]
                        : prev.pools.filter(p => p !== pool.pool_id),
                    }))}
                    className="w-4 h-4 rounded border-lightGray"
                  />
                  <span className="text-sm text-dark font-mono">{pool.pool_id}</span>
                </label>
              ))}
            </div>
          </div>

          {skills.length > 0 && (
            <div>
              <label className="text-sm font-semibold text-dark mb-2 block">
                {t('agentTypes.fields.skills')}
              </label>
              <div className="space-y-1 max-h-36 overflow-y-auto border border-lightGray rounded p-2">
                {skills.map(skill => (
                  <label key={skill.skill_id} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={formData.skills.includes(skill.skill_id)}
                      onChange={e => setFormData(prev => ({
                        ...prev,
                        skills: e.target.checked
                          ? [...prev.skills, skill.skill_id]
                          : prev.skills.filter(s => s !== skill.skill_id),
                      }))}
                      className="w-4 h-4 rounded border-lightGray"
                    />
                    <span className="text-sm text-dark font-mono">{skill.skill_id}</span>
                  </label>
                ))}
              </div>
            </div>
          )}
        </div>
      </Modal>

      {/* Detail / Deprecate modal */}
      {selected && (
        <Modal
          isOpen={isDetailOpen}
          onClose={() => { setIsDetailOpen(false); setConfirmDeprecate(false) }}
          title={selected.agent_type_id}
          footer={
            confirmDeprecate ? (
              <>
                <span className="text-sm text-red self-center">Deprecate this agent type?</span>
                <Button variant="ghost" onClick={() => setConfirmDeprecate(false)}>Cancel</Button>
                <Button variant="danger" onClick={handleDeprecate} disabled={isDeprecating}>
                  {isDeprecating ? 'Deprecating...' : 'Confirm'}
                </Button>
              </>
            ) : (
              <>
                <Button variant="danger" onClick={() => setConfirmDeprecate(true)}>Deprecate</Button>
                <Button variant="ghost" onClick={() => setIsDetailOpen(false)}>Close</Button>
              </>
            )
          }
        >
          <div className="space-y-4 text-sm">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <div className="text-xs font-semibold text-gray uppercase mb-1">Framework</div>
                <code className="text-secondary text-xs">{selected.framework}</code>
              </div>
              <div>
                <div className="text-xs font-semibold text-gray uppercase mb-1">Execution</div>
                <span className="text-dark">{selected.execution_model}</span>
              </div>
              <div>
                <div className="text-xs font-semibold text-gray uppercase mb-1">Role</div>
                <span className={`text-xs font-semibold px-2 py-0.5 rounded border ${roleColor(selected.role)}`}>
                  {selected.role}
                </span>
              </div>
              <div>
                <div className="text-xs font-semibold text-gray uppercase mb-1">Max Concurrent</div>
                <span className="text-dark">{selected.max_concurrent_sessions}</span>
              </div>
            </div>
            <div>
              <div className="text-xs font-semibold text-gray uppercase mb-2">Pools</div>
              <div className="flex gap-1 flex-wrap">
                {(selected.pools || []).map(p => (
                  <span key={p.pool_id} className="text-xs px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/20">
                    {p.pool_id}
                  </span>
                ))}
              </div>
            </div>
            {(selected.skills ?? []).length > 0 && (
              <div>
                <div className="text-xs font-semibold text-gray uppercase mb-2">Skills</div>
                <div className="flex gap-1 flex-wrap">
                  {(selected.skills ?? []).map(s => (
                    <span key={s.skill_id} className="text-xs px-1.5 py-0.5 rounded bg-green/10 text-green border border-green/20">
                      {s.skill_id}
                    </span>
                  ))}
                </div>
              </div>
            )}
            {(selected.permissions ?? []).length > 0 && (
              <div>
                <div className="text-xs font-semibold text-gray uppercase mb-2">Permissions</div>
                <div className="flex gap-1 flex-wrap">
                  {(selected.permissions ?? []).map((p, i) => (
                    <span key={i} className="text-xs px-1.5 py-0.5 rounded bg-gray/10 text-gray border border-gray/20 font-mono">
                      {typeof p === 'string' ? p : JSON.stringify(p)}
                    </span>
                  ))}
                </div>
              </div>
            )}
            <div>
              <div className="text-xs font-semibold text-gray uppercase mb-1">Status</div>
              <Badge variant={selected.status === 'active' ? 'active' : 'default'}>{selected.status}</Badge>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}

export default AgentTypesPage
