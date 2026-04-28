import React, { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import * as registryApi from '@/api/registry'
import { Skill } from '@/types'
import Table from '@/components/ui/Table'
import Modal from '@/components/ui/Modal'
import Button from '@/components/ui/Button'
import Badge from '@/components/ui/Badge'
import EmptyState from '@/components/ui/EmptyState'
import Spinner from '@/components/ui/Spinner'

interface SkillDetail extends Skill {
  tools?: Array<{ server: string; name: string }>
  knowledge_domains?: string[]
  entry?: string
}

const SkillsPage: React.FC = () => {
  const { session } = useAuth()
  const { t } = useTranslation('configRecursos')
  const [skills, setSkills] = useState<Skill[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<SkillDetail | null>(null)
  const [isDetailOpen, setIsDetailOpen] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)

  useEffect(() => { loadSkills() }, [])

  const loadSkills = async () => {
    if (!session) return
    setIsLoading(true)
    try {
      const result = await registryApi.listSkills(session.tenantId)
      setSkills(result.items || [])
    } catch {
      setError('Failed to load skills')
    } finally {
      setIsLoading(false)
    }
  }

  const handleRowClick = async (skill: Skill) => {
    setSelected(skill as SkillDetail)
    setConfirmDelete(false)
    setIsDetailOpen(true)
    // Fetch full detail
    try {
      const detail = await registryApi.getSkill(skill.skill_id, session!.tenantId)
      setSelected(detail as SkillDetail)
    } catch { /* keep summary */ }
  }

  const handleDelete = async () => {
    if (!session || !selected) return
    setIsDeleting(true)
    try {
      await registryApi.deleteSkill(selected.skill_id, session.tenantId)
      await loadSkills()
      setIsDetailOpen(false)
      setSelected(null)
      setConfirmDelete(false)
    } catch {
      setError('Failed to delete skill')
    } finally {
      setIsDeleting(false)
    }
  }

  const typeColor = (type?: string) => {
    if (type === 'orchestrator') return 'text-indigo-600 bg-indigo-50 border-indigo-200'
    if (type === 'vertical')     return 'text-cyan-600 bg-cyan-50 border-cyan-200'
    return 'text-yellow-600 bg-yellow-50 border-yellow-200'
  }

  const columns = [
    {
      key: 'skill_id',
      label: t('skills.fields.skillId'),
      render: (id: string) => <code className="text-xs text-secondary">{id}</code>,
    },
    { key: 'name',    label: t('skills.fields.name') },
    { key: 'version', label: t('skills.fields.version') },
    {
      key: 'classification',
      label: t('skills.fields.type'),
      render: (c?: { type?: string }) => c?.type ? (
        <span className={`text-xs font-semibold px-2 py-0.5 rounded border ${typeColor(c.type)}`}>
          {c.type}
        </span>
      ) : <span className="text-gray text-xs">—</span>,
    },
    {
      key: 'status',
      label: t('skills.fields.status'),
      render: (s: string) => <Badge variant={s === 'active' ? 'active' : 'default'}>{s}</Badge>,
    },
  ]

  return (
    <div>
      <div className="mb-4 bg-secondary/5 border border-secondary/20 rounded px-4 py-2 text-sm text-dark">
        Skills are managed via YAML files in{' '}
        <code className="text-primary text-xs">packages/skill-flow-engine/skills/</code>{' '}
        and synced at bridge startup. Use the{' '}
        <strong>Skill Flows</strong> module to edit them.
      </div>

      {error && (
        <div className="mb-4 bg-red/10 border border-red text-red px-3 py-2 rounded text-sm flex justify-between">
          <span>{error}</span>
          <button onClick={() => setError('')} className="font-bold">✕</button>
        </div>
      )}

      {isLoading ? (
        <div className="flex justify-center py-8"><Spinner /></div>
      ) : skills.length === 0 ? (
        <EmptyState title={t('skills.empty')} />
      ) : (
        <Table
          columns={columns}
          data={skills}
          keyField="skill_id"
          onRowClick={handleRowClick}
        />
      )}

      {/* Detail modal */}
      {selected && (
        <Modal
          isOpen={isDetailOpen}
          onClose={() => { setIsDetailOpen(false); setConfirmDelete(false) }}
          title={selected.skill_id}
          footer={
            confirmDelete ? (
              <>
                <span className="text-sm text-red self-center">Delete this skill?</span>
                <Button variant="ghost" onClick={() => setConfirmDelete(false)}>Cancel</Button>
                <Button variant="danger" onClick={handleDelete} disabled={isDeleting}>
                  {isDeleting ? 'Deleting...' : 'Confirm'}
                </Button>
              </>
            ) : (
              <>
                <Button variant="danger" onClick={() => setConfirmDelete(true)}>Delete</Button>
                <Button variant="ghost" onClick={() => setIsDetailOpen(false)}>Close</Button>
              </>
            )
          }
        >
          <div className="space-y-4 text-sm">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <div className="text-xs font-semibold text-gray uppercase mb-1">{t('skills.fields.name')}</div>
                <span className="text-dark">{selected.name}</span>
              </div>
              <div>
                <div className="text-xs font-semibold text-gray uppercase mb-1">{t('skills.fields.version')}</div>
                <span className="text-dark font-mono">{selected.version}</span>
              </div>
              {selected.classification?.type && (
                <div>
                  <div className="text-xs font-semibold text-gray uppercase mb-1">{t('skills.fields.type')}</div>
                  <span className={`text-xs font-semibold px-2 py-0.5 rounded border ${typeColor(selected.classification.type)}`}>
                    {selected.classification.type}
                  </span>
                </div>
              )}
              {selected.classification?.domain && (
                <div>
                  <div className="text-xs font-semibold text-gray uppercase mb-1">{t('skills.fields.domain')}</div>
                  <span className="text-dark">{selected.classification.domain}</span>
                </div>
              )}
            </div>

            {selected.description && (
              <div>
                <div className="text-xs font-semibold text-gray uppercase mb-1">Description</div>
                <p className="text-dark leading-relaxed">{selected.description}</p>
              </div>
            )}

            {selected.tools && selected.tools.length > 0 && (
              <div>
                <div className="text-xs font-semibold text-gray uppercase mb-2">
                  Tools ({selected.tools.length})
                </div>
                <div className="flex flex-wrap gap-1">
                  {selected.tools.map((tool, i) => (
                    <span key={i} className="text-xs px-1.5 py-0.5 rounded bg-gray/10 text-gray border border-gray/20 font-mono">
                      {tool.server}/{tool.name}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {selected.knowledge_domains && selected.knowledge_domains.length > 0 && (
              <div>
                <div className="text-xs font-semibold text-gray uppercase mb-2">Knowledge Domains</div>
                <div className="flex flex-wrap gap-1">
                  {selected.knowledge_domains.map((d, i) => (
                    <span key={i} className="text-xs px-1.5 py-0.5 rounded bg-gray/10 text-gray border border-gray/20">
                      {d}
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

export default SkillsPage
