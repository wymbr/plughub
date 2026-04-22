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

  const [formData, setFormData] = useState({
    agent_type_id: '',
    framework: 'anthropic_claude',
    execution_model: '',
    pools: [] as string[],
    skills: [] as string[]
  })

  const frameworkOptions = [
    { value: 'anthropic_claude', label: 'Anthropic Claude' },
    { value: 'openai_gpt', label: 'OpenAI GPT' },
    { value: 'human', label: 'Human' },
    { value: 'external', label: 'External' }
  ]

  useEffect(() => {
    loadData()
  }, [])

  const loadData = async () => {
    if (!session) return
    setIsLoading(true)
    try {
      const [agentsResult, poolsResult, skillsResult] = await Promise.all([
        registryApi.listAgentTypes(session.tenantId),
        registryApi.listPools(session.tenantId),
        registryApi.listSkills(session.tenantId)
      ])
      setAgentTypes(agentsResult.items || [])
      setPools(poolsResult.items || [])
      setSkills(skillsResult.items || [])
    } catch (err) {
      setError('Failed to load data')
    } finally {
      setIsLoading(false)
    }
  }

  const handleOpenCreate = () => {
    setFormData({
      agent_type_id: '',
      framework: 'anthropic_claude',
      execution_model: '',
      pools: [],
      skills: []
    })
    setError('')
    setIsOpen(true)
  }

  const handleClose = () => {
    setIsOpen(false)
  }

  const handleSubmit = async () => {
    if (!session || !formData.agent_type_id.trim()) {
      setError('Agent Type ID is required')
      return
    }

    setIsSaving(true)
    setError('')

    try {
      await registryApi.createAgentType(formData, session.tenantId)
      await loadData()
      handleClose()
    } catch (err) {
      setError('Failed to save agent type')
    } finally {
      setIsSaving(false)
    }
  }

  const columns = [
    { key: 'agent_type_id', label: t('agentTypes.fields.agentTypeId') },
    { key: 'framework', label: t('agentTypes.fields.framework') },
    { key: 'execution_model', label: t('agentTypes.fields.executionModel') },
    {
      key: 'pools',
      label: t('agentTypes.fields.pools'),
      render: (poolList: string[]) => <span className="text-sm">{poolList.length} pools</span>
    },
    {
      key: 'skills',
      label: t('agentTypes.fields.skills'),
      render: (skillList: string[]) => <span className="text-sm">{skillList.length} skills</span>
    }
  ]

  return (
    <div>
      <div className="mb-6 flex justify-end">
        <Button variant="primary" onClick={handleOpenCreate}>
          + {t('agentTypes.createAgentType')}
        </Button>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
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
        />
      )}

      <Modal
        isOpen={isOpen}
        onClose={handleClose}
        title={t('agentTypes.createAgentType')}
        footer={
          <>
            <Button variant="ghost" onClick={handleClose}>
              Cancel
            </Button>
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
            label={t('agentTypes.fields.agentTypeId')}
            value={formData.agent_type_id}
            onChange={(e) => setFormData({ ...formData, agent_type_id: e.target.value })}
            required
          />

          <Select
            label={t('agentTypes.fields.framework')}
            value={formData.framework}
            onChange={(e) => setFormData({ ...formData, framework: e.target.value })}
            options={frameworkOptions}
          />

          <Input
            label={t('agentTypes.fields.executionModel')}
            value={formData.execution_model}
            onChange={(e) => setFormData({ ...formData, execution_model: e.target.value })}
          />

          <div>
            <label className="text-sm font-semibold text-dark mb-2 block">
              {t('agentTypes.fields.pools')}
            </label>
            <div className="space-y-2 max-h-40 overflow-y-auto">
              {pools.map(pool => (
                <label key={pool.pool_id} className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={formData.pools.includes(pool.pool_id)}
                    onChange={(e) => {
                      setFormData(prev => ({
                        ...prev,
                        pools: e.target.checked
                          ? [...prev.pools, pool.pool_id]
                          : prev.pools.filter(p => p !== pool.pool_id)
                      }))
                    }}
                    className="w-4 h-4 rounded border-lightGray"
                  />
                  <span className="text-sm text-dark">{pool.pool_id}</span>
                </label>
              ))}
            </div>
          </div>

          <div>
            <label className="text-sm font-semibold text-dark mb-2 block">
              {t('agentTypes.fields.skills')}
            </label>
            <div className="space-y-2 max-h-40 overflow-y-auto">
              {skills.map(skill => (
                <label key={skill.skill_id} className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={formData.skills.includes(skill.skill_id)}
                    onChange={(e) => {
                      setFormData(prev => ({
                        ...prev,
                        skills: e.target.checked
                          ? [...prev.skills, skill.skill_id]
                          : prev.skills.filter(s => s !== skill.skill_id)
                      }))
                    }}
                    className="w-4 h-4 rounded border-lightGray"
                  />
                  <span className="text-sm text-dark">{skill.skill_id}</span>
                </label>
              ))}
            </div>
          </div>
        </div>
      </Modal>
    </div>
  )
}

export default AgentTypesPage
