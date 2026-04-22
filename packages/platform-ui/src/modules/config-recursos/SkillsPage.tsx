import React, { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import * as registryApi from '@/api/registry'
import { Skill } from '@/types'
import Button from '@/components/ui/Button'
import Table from '@/components/ui/Table'
import Modal from '@/components/ui/Modal'
import Input from '@/components/ui/Input'
import Select from '@/components/ui/Select'
import Badge from '@/components/ui/Badge'
import EmptyState from '@/components/ui/EmptyState'
import Spinner from '@/components/ui/Spinner'

const SkillsPage: React.FC = () => {
  const { session } = useAuth()
  const { t } = useTranslation('configRecursos')
  const [skills, setSkills] = useState<Skill[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [isOpen, setIsOpen] = useState(false)
  const [isSaving, setIsSaving] = useState(false)
  const [error, setError] = useState('')
  const [showDeleteConfirm, setShowDeleteConfirm] = useState<string | null>(null)
  const [isDeleting, setIsDeleting] = useState(false)

  const [formData, setFormData] = useState({
    skill_id: '',
    name: '',
    version: '',
    description: '',
    classification: {
      type: '',
      vertical: '',
      domain: ''
    }
  })

  const typeOptions = [
    { value: 'orchestrator', label: 'Orchestrator' },
    { value: 'executor', label: 'Executor' },
    { value: 'evaluator', label: 'Evaluator' },
    { value: 'specialist', label: 'Specialist' }
  ]

  useEffect(() => {
    loadSkills()
  }, [])

  const loadSkills = async () => {
    if (!session) return
    setIsLoading(true)
    try {
      const result = await registryApi.listSkills(session.tenantId)
      setSkills(result.items || [])
    } catch (err) {
      setError('Failed to load skills')
    } finally {
      setIsLoading(false)
    }
  }

  const handleOpenCreate = () => {
    setFormData({
      skill_id: '',
      name: '',
      version: '',
      description: '',
      classification: { type: '', vertical: '', domain: '' }
    })
    setError('')
    setIsOpen(true)
  }

  const handleClose = () => {
    setIsOpen(false)
  }

  const handleSubmit = async () => {
    if (!session || !formData.skill_id.trim() || !formData.name.trim()) {
      setError('Skill ID and Name are required')
      return
    }

    setIsSaving(true)
    setError('')

    try {
      await registryApi.upsertSkill(formData.skill_id, formData, session.tenantId)
      await loadSkills()
      handleClose()
    } catch (err) {
      setError('Failed to save skill')
    } finally {
      setIsSaving(false)
    }
  }

  const handleDelete = async (skillId: string) => {
    if (!session) return
    setIsDeleting(true)
    try {
      await registryApi.deleteSkill(skillId, session.tenantId)
      await loadSkills()
      setShowDeleteConfirm(null)
    } catch (err) {
      setError('Failed to delete skill')
    } finally {
      setIsDeleting(false)
    }
  }

  const columns = [
    { key: 'skill_id', label: t('skills.fields.skillId') },
    { key: 'name', label: t('skills.fields.name') },
    { key: 'version', label: t('skills.fields.version') },
    {
      key: 'classification',
      label: t('skills.fields.type'),
      render: (classification?: { type?: string }) => (
        <span className="text-sm">{classification?.type || '—'}</span>
      )
    },
    {
      key: 'skill_id',
      label: 'Actions',
      render: (skillId: string) => (
        <button
          onClick={(e) => {
            e.stopPropagation()
            setShowDeleteConfirm(skillId)
          }}
          className="text-red hover:text-red-700 text-sm font-semibold"
        >
          Delete
        </button>
      )
    }
  ]

  return (
    <div>
      <div className="mb-6 flex justify-end">
        <Button variant="primary" onClick={handleOpenCreate}>
          + {t('skills.createSkill')}
        </Button>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-8">
          <Spinner />
        </div>
      ) : skills.length === 0 ? (
        <EmptyState
          title={t('skills.empty')}
          action={<Button onClick={handleOpenCreate}>Create first</Button>}
        />
      ) : (
        <Table
          columns={columns}
          data={skills}
          keyField="skill_id"
        />
      )}

      <Modal
        isOpen={isOpen}
        onClose={handleClose}
        title={t('skills.createSkill')}
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
            label={t('skills.fields.skillId')}
            value={formData.skill_id}
            onChange={(e) => setFormData({ ...formData, skill_id: e.target.value })}
            required
          />

          <Input
            label={t('skills.fields.name')}
            value={formData.name}
            onChange={(e) => setFormData({ ...formData, name: e.target.value })}
            required
          />

          <Input
            label={t('skills.fields.version')}
            value={formData.version}
            onChange={(e) => setFormData({ ...formData, version: e.target.value })}
            placeholder="e.g., 1.0.0"
          />

          <Input
            label="Description"
            value={formData.description}
            onChange={(e) => setFormData({ ...formData, description: e.target.value })}
          />

          <Select
            label={t('skills.fields.type')}
            value={formData.classification.type}
            onChange={(e) => setFormData({
              ...formData,
              classification: { ...formData.classification, type: e.target.value }
            })}
            options={[{ value: '', label: 'Select...' }, ...typeOptions]}
          />

          <Input
            label="Vertical"
            value={formData.classification.vertical}
            onChange={(e) => setFormData({
              ...formData,
              classification: { ...formData.classification, vertical: e.target.value }
            })}
          />

          <Input
            label={t('skills.fields.domain')}
            value={formData.classification.domain}
            onChange={(e) => setFormData({
              ...formData,
              classification: { ...formData.classification, domain: e.target.value }
            })}
          />
        </div>
      </Modal>

      {showDeleteConfirm && (
        <Modal
          isOpen={true}
          onClose={() => setShowDeleteConfirm(null)}
          title="Confirm Delete"
          footer={
            <>
              <Button variant="ghost" onClick={() => setShowDeleteConfirm(null)}>
                Cancel
              </Button>
              <Button
                variant="danger"
                onClick={() => handleDelete(showDeleteConfirm)}
                disabled={isDeleting}
              >
                {isDeleting ? 'Deleting...' : 'Delete'}
              </Button>
            </>
          }
        >
          <p className="text-dark">This action cannot be undone. Delete this skill?</p>
        </Modal>
      )}
    </div>
  )
}

export default SkillsPage
