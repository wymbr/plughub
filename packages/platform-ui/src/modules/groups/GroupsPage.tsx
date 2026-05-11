/**
 * GroupsPage — /config/groups
 *
 * Arc 9 — Agent Groups & Supervisor Scope
 *
 * Manages AgentGroup entities: create/edit/delete groups, configure
 * members (agent_type_ids), supervisors (users), and shifts (time windows).
 *
 * Drawer tabs per group:
 *   Info        — name + description
 *   Members     — agent_type_id list (is_human flag)
 *   Supervisors — user list
 *   Shifts      — time-window definitions per supervisor
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'

// ── Types ─────────────────────────────────────────────────────────────────────

interface Group {
  group_id:    string
  tenant_id:   string
  name:        string
  description: string
  created_at:  string
  updated_at:  string
  member_count?:     number
  supervisor_count?: number
}

interface GroupDetail extends Group {
  members:     GroupMember[]
  users:       GroupUser[]
  supervisors: GroupUser[]
  shifts:      GroupShift[]
}

interface GroupMember {
  group_id:      string
  agent_type_id: string
  is_human:      boolean
}

interface GroupUser {
  user_id: string
  id?:     string
  email?:  string
  name?:   string
}

interface GroupShift {
  shift_id:           string
  group_id:           string
  supervisor_user_id: string
  days_of_week:       number[]
  time_start:         string
  time_end:           string
  timezone:           string
  active:             boolean
}

// ── Constants ─────────────────────────────────────────────────────────────────

const DAY_LABELS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const TIMEZONES = ['UTC', 'America/Sao_Paulo', 'America/New_York', 'Europe/London', 'Asia/Tokyo']

// ── API helpers ───────────────────────────────────────────────────────────────

function adminHeaders(token: string): HeadersInit {
  return { 'Content-Type': 'application/json', 'X-Admin-Token': token }
}

async function apiFetch<T>(url: string, token: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { ...adminHeaders(token), ...(init?.headers ?? {}) },
  })
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(`HTTP ${res.status}${body ? ': ' + body : ''}`)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

// ── Main component ────────────────────────────────────────────────────────────

export default function GroupsPage() {
  const { t }     = useTranslation('groups')
  const { session } = useAuth()

  const tenantId = session?.tenantId ?? 'tenant_demo'

  const [adminToken,  setAdminToken]  = useState('')
  const [tokenSaved,  setTokenSaved]  = useState(false)

  const [groups,  setGroups]  = useState<Group[]>([])
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)

  // Drawer state
  const [drawerGroup,  setDrawerGroup]  = useState<GroupDetail | null>(null)
  const [drawerOpen,   setDrawerOpen]   = useState(false)
  const [drawerTab,    setDrawerTab]    = useState<'info' | 'members' | 'supervisors' | 'shifts'>('info')
  const [drawerLoading, setDrawerLoading] = useState(false)

  // New group form
  const [newGroupOpen, setNewGroupOpen] = useState(false)
  const [newName,      setNewName]      = useState('')
  const [newDesc,      setNewDesc]      = useState('')
  const [saving,       setSaving]       = useState(false)

  // ── Load groups ─────────────────────────────────────────────────────────────

  const loadGroups = useCallback(async () => {
    if (!tenantId || !adminToken) return
    setLoading(true); setError(null)
    try {
      const data = await apiFetch<Group[]>(
        `/auth/v1/groups?tenant_id=${encodeURIComponent(tenantId)}`,
        adminToken,
      )
      setGroups(data ?? [])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [tenantId, adminToken])

  useEffect(() => { void loadGroups() }, [loadGroups])

  // ── Open group drawer ────────────────────────────────────────────────────────

  const openGroup = useCallback(async (groupId: string) => {
    setDrawerLoading(true); setDrawerOpen(true); setDrawerTab('info')
    try {
      const detail = await apiFetch<GroupDetail>(
        `/auth/v1/groups/${groupId}`,
        adminToken,
      )
      setDrawerGroup(detail)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setDrawerOpen(false)
    } finally {
      setDrawerLoading(false)
    }
  }, [adminToken])

  const closeDrawer = () => {
    setDrawerOpen(false)
    setDrawerGroup(null)
  }

  // ── Create group ─────────────────────────────────────────────────────────────

  const createGroup = async () => {
    if (!newName.trim()) return
    setSaving(true)
    try {
      await apiFetch<Group>('/auth/v1/groups', adminToken, {
        method: 'POST',
        body: JSON.stringify({ tenant_id: tenantId, name: newName.trim(), description: newDesc.trim() }),
      })
      setNewGroupOpen(false); setNewName(''); setNewDesc('')
      await loadGroups()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  // ── Delete group ─────────────────────────────────────────────────────────────

  const deleteGroup = async (groupId: string) => {
    if (!confirm(t('confirmDelete'))) return
    try {
      await apiFetch<void>(`/auth/v1/groups/${groupId}`, adminToken, { method: 'DELETE' })
      closeDrawer()
      await loadGroups()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  function saveToken() {
    setTokenSaved(true)
    void loadGroups()
    setTimeout(() => setTokenSaved(false), 2000)
  }

  // ── Render ────────────────────────────────────────────────────────────────────

  return (
    <div className="h-full flex flex-col bg-gray-50">
      {/* Header */}
      <div className="bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">{t('title')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{t('subtitle')}</p>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="password"
            value={adminToken}
            onChange={e => { setAdminToken(e.target.value); setTokenSaved(false) }}
            onKeyDown={e => { if (e.key === 'Enter') saveToken() }}
            placeholder={t('adminTokenPlaceholder')}
            className="border border-gray-300 rounded-lg px-3 py-1.5 text-xs w-48 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          {adminToken && (
            <button
              onClick={saveToken}
              className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${
                tokenSaved ? 'bg-green-100 text-green-700' : 'bg-blue-600 text-white hover:bg-blue-700'
              }`}
            >
              {tokenSaved ? '✓' : t('adminTokenApply')}
            </button>
          )}
          {adminToken && (
            <button
              onClick={() => setNewGroupOpen(true)}
              className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition-colors"
            >
              + {t('newGroup')}
            </button>
          )}
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="mx-6 mt-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700 flex justify-between">
          {error}
          <button onClick={() => setError(null)} className="ml-2 text-red-500 hover:text-red-700">✕</button>
        </div>
      )}

      {/* Groups list */}
      <div className="flex-1 overflow-auto p-6">
        {loading ? (
          <div className="flex justify-center py-16"><Spinner /></div>
        ) : groups.length === 0 ? (
          <div className="text-center py-16 text-gray-400">
            <div className="text-4xl mb-3">👥</div>
            <p className="text-sm">{t('empty')}</p>
          </div>
        ) : (
          <div className="grid gap-3">
            {groups.map(g => (
              <div
                key={g.group_id}
                onClick={() => openGroup(g.group_id)}
                className="bg-white border border-gray-200 rounded-lg px-5 py-4 flex items-center justify-between cursor-pointer hover:border-blue-300 hover:shadow-sm transition-all"
              >
                <div>
                  <p className="font-medium text-gray-900">{g.name}</p>
                  {g.description && (
                    <p className="text-sm text-gray-500 mt-0.5">{g.description}</p>
                  )}
                </div>
                <div className="flex items-center gap-4 text-sm text-gray-500">
                  {g.member_count != null && (
                    <span>{g.member_count} {t('members').toLowerCase()}</span>
                  )}
                  {g.supervisor_count != null && (
                    <span>{g.supervisor_count} {t('supervisors').toLowerCase()}</span>
                  )}
                  <span className="text-gray-300">›</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* New Group modal */}
      {newGroupOpen && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6">
            <h2 className="text-lg font-semibold mb-4">{t('newGroup')}</h2>
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('fieldName')}</label>
            <input
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm mb-3 focus:outline-none focus:ring-2 focus:ring-blue-500"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              placeholder={t('fieldNamePlaceholder')}
            />
            <label className="block text-sm font-medium text-gray-700 mb-1">{t('fieldDescription')}</label>
            <input
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm mb-5 focus:outline-none focus:ring-2 focus:ring-blue-500"
              value={newDesc}
              onChange={e => setNewDesc(e.target.value)}
              placeholder={t('fieldDescriptionPlaceholder')}
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => { setNewGroupOpen(false); setNewName(''); setNewDesc('') }}
                className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900"
              >
                {t('cancel')}
              </button>
              <button
                onClick={createGroup}
                disabled={saving || !newName.trim()}
                className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
              >
                {saving ? t('saving') : t('create')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Group detail drawer */}
      {drawerOpen && (
        <GroupDrawer
          group={drawerGroup}
          loading={drawerLoading}
          tab={drawerTab}
          onTabChange={setDrawerTab}
          adminToken={adminToken}
          onClose={closeDrawer}
          onDelete={deleteGroup}
          onRefresh={async (id) => {
            const detail = await apiFetch<GroupDetail>(`/auth/v1/groups/${id}`, adminToken)
            setDrawerGroup(detail)
            await loadGroups()
          }}
          t={t}
        />
      )}
    </div>
  )
}

// ── GroupDrawer ────────────────────────────────────────────────────────────────

interface DrawerProps {
  group:       GroupDetail | null
  loading:     boolean
  tab:         'info' | 'members' | 'supervisors' | 'shifts'
  onTabChange: (tab: 'info' | 'members' | 'supervisors' | 'shifts') => void
  adminToken:  string
  onClose:     () => void
  onDelete:    (id: string) => void
  onRefresh:   (id: string) => Promise<void>
  t:           (key: string, opts?: Record<string, unknown>) => string
}

function GroupDrawer({ group, loading, tab, onTabChange, adminToken, onClose, onDelete, onRefresh, t }: DrawerProps) {
  return (
    <div className="fixed inset-0 z-40 flex">
      {/* Backdrop */}
      <div className="flex-1 bg-black/30" onClick={onClose} />

      {/* Panel */}
      <div className="w-full max-w-lg bg-white shadow-2xl flex flex-col">
        {/* Drawer header */}
        <div className="border-b border-gray-200 px-6 py-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-900">
            {loading ? '…' : group?.name ?? ''}
          </h2>
          <div className="flex items-center gap-2">
            {group && (
              <button
                onClick={() => onDelete(group.group_id)}
                className="px-3 py-1.5 text-sm text-red-600 hover:bg-red-50 rounded-lg transition-colors"
              >
                {t('delete')}
              </button>
            )}
            <button onClick={onClose} className="p-1.5 text-gray-400 hover:text-gray-700 rounded">✕</button>
          </div>
        </div>

        {/* Tabs */}
        <div className="border-b border-gray-200 px-6 flex gap-1">
          {(['info', 'members', 'supervisors', 'shifts'] as const).map(tabKey => (
            <button
              key={tabKey}
              onClick={() => onTabChange(tabKey)}
              className={`px-3 py-3 text-sm font-medium border-b-2 transition-colors ${
                tab === tabKey
                  ? 'border-blue-600 text-blue-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              {t(`tab.${tabKey}`)}
            </button>
          ))}
        </div>

        {/* Tab content */}
        <div className="flex-1 overflow-auto">
          {loading ? (
            <div className="flex justify-center py-16"><Spinner /></div>
          ) : !group ? null : (
            <>
              {tab === 'info'        && <InfoTab        group={group} adminToken={adminToken} onRefresh={onRefresh} t={t} />}
              {tab === 'members'     && <MembersTab     group={group} adminToken={adminToken} onRefresh={onRefresh} t={t} />}
              {tab === 'supervisors' && <SupervisorsTab group={group} adminToken={adminToken} onRefresh={onRefresh} t={t} />}
              {tab === 'shifts'      && <ShiftsTab      group={group} adminToken={adminToken} onRefresh={onRefresh} t={t} />}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── InfoTab ───────────────────────────────────────────────────────────────────

function InfoTab({ group, adminToken, onRefresh, t }: {
  group: GroupDetail; adminToken: string; onRefresh: (id: string) => Promise<void>
  t: (k: string) => string
}) {
  const [name, setName]   = useState(group.name)
  const [desc, setDesc]   = useState(group.description)
  const [saving, setSaving] = useState(false)
  const [saved,  setSaved]  = useState(false)

  const save = async () => {
    setSaving(true)
    try {
      await apiFetch(`/auth/v1/groups/${group.group_id}`, adminToken, {
        method: 'PUT',
        body: JSON.stringify({ name, description: desc }),
      })
      setSaved(true); setTimeout(() => setSaved(false), 2000)
      await onRefresh(group.group_id)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="p-6 space-y-4">
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">{t('fieldName')}</label>
        <input
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          value={name}
          onChange={e => setName(e.target.value)}
        />
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">{t('fieldDescription')}</label>
        <textarea
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
          rows={3}
          value={desc}
          onChange={e => setDesc(e.target.value)}
        />
      </div>
      <div className="flex items-center gap-3">
        <button
          onClick={save}
          disabled={saving}
          className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
        >
          {saving ? t('saving') : t('save')}
        </button>
        {saved && <span className="text-sm text-green-600">✓ {t('saved')}</span>}
      </div>
      <div className="pt-4 border-t border-gray-100 text-xs text-gray-400 space-y-1">
        <p>{t('fieldGroupId')}: <span className="font-mono">{group.group_id}</span></p>
        <p>{t('fieldCreated')}: {new Date(group.created_at).toLocaleString()}</p>
      </div>
    </div>
  )
}

// ── MembersTab ────────────────────────────────────────────────────────────────

function MembersTab({ group, adminToken, onRefresh, t }: {
  group: GroupDetail; adminToken: string; onRefresh: (id: string) => Promise<void>
  t: (k: string) => string
}) {
  const [agentTypeId, setAgentTypeId] = useState('')
  const [isHuman,     setIsHuman]     = useState(false)
  const [adding,      setAdding]      = useState(false)

  const add = async () => {
    if (!agentTypeId.trim()) return
    setAdding(true)
    try {
      await apiFetch(`/auth/v1/groups/${group.group_id}/members`, adminToken, {
        method: 'POST',
        body: JSON.stringify({ agent_type_id: agentTypeId.trim(), is_human: isHuman }),
      })
      setAgentTypeId(''); setIsHuman(false)
      await onRefresh(group.group_id)
    } finally {
      setAdding(false)
    }
  }

  const remove = async (agentTypeId: string) => {
    await apiFetch(`/auth/v1/groups/${group.group_id}/members/${encodeURIComponent(agentTypeId)}`, adminToken, { method: 'DELETE' })
    await onRefresh(group.group_id)
  }

  return (
    <div className="p-6 space-y-4">
      <p className="text-sm text-gray-500">{t('membersHint')}</p>

      {/* Add form */}
      <div className="flex gap-2 items-center">
        <input
          className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          placeholder={t('memberAgentTypeId')}
          value={agentTypeId}
          onChange={e => setAgentTypeId(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && add()}
        />
        <label className="flex items-center gap-1.5 text-sm text-gray-600 whitespace-nowrap">
          <input type="checkbox" checked={isHuman} onChange={e => setIsHuman(e.target.checked)} className="rounded" />
          {t('memberIsHuman')}
        </label>
        <button
          onClick={add}
          disabled={adding || !agentTypeId.trim()}
          className="px-3 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 disabled:opacity-50 whitespace-nowrap"
        >
          {t('add')}
        </button>
      </div>

      {/* List */}
      {group.members.length === 0 ? (
        <p className="text-sm text-gray-400 text-center py-4">{t('noMembers')}</p>
      ) : (
        <div className="divide-y divide-gray-100 border border-gray-200 rounded-lg">
          {group.members.map(m => (
            <div key={m.agent_type_id} className="flex items-center justify-between px-4 py-3">
              <div>
                <span className="text-sm font-mono text-gray-800">{m.agent_type_id}</span>
                {m.is_human && (
                  <span className="ml-2 text-xs bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded">human</span>
                )}
              </div>
              <button
                onClick={() => remove(m.agent_type_id)}
                className="text-xs text-red-500 hover:text-red-700"
              >
                {t('remove')}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── SupervisorsTab ────────────────────────────────────────────────────────────

function SupervisorsTab({ group, adminToken, onRefresh, t }: {
  group: GroupDetail; adminToken: string; onRefresh: (id: string) => Promise<void>
  t: (k: string) => string
}) {
  const [userId,  setUserId]  = useState('')
  const [adding,  setAdding]  = useState(false)

  const add = async () => {
    if (!userId.trim()) return
    setAdding(true)
    try {
      await apiFetch(`/auth/v1/groups/${group.group_id}/supervisors`, adminToken, {
        method: 'POST',
        body: JSON.stringify({ user_id: userId.trim() }),
      })
      setUserId('')
      await onRefresh(group.group_id)
    } finally {
      setAdding(false)
    }
  }

  const remove = async (uid: string) => {
    await apiFetch(`/auth/v1/groups/${group.group_id}/supervisors/${uid}`, adminToken, { method: 'DELETE' })
    await onRefresh(group.group_id)
  }

  return (
    <div className="p-6 space-y-4">
      <p className="text-sm text-gray-500">{t('supervisorsHint')}</p>

      <div className="flex gap-2">
        <input
          className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          placeholder={t('supervisorUserId')}
          value={userId}
          onChange={e => setUserId(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && add()}
        />
        <button
          onClick={add}
          disabled={adding || !userId.trim()}
          className="px-3 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 disabled:opacity-50"
        >
          {t('add')}
        </button>
      </div>

      {group.supervisors.length === 0 ? (
        <p className="text-sm text-gray-400 text-center py-4">{t('noSupervisors')}</p>
      ) : (
        <div className="divide-y divide-gray-100 border border-gray-200 rounded-lg">
          {group.supervisors.map(s => {
            const uid = s.user_id ?? s.id ?? ''
            return (
              <div key={uid} className="flex items-center justify-between px-4 py-3">
                <div>
                  <p className="text-sm text-gray-800">{s.name ?? s.email ?? uid}</p>
                  {s.email && s.name && <p className="text-xs text-gray-400">{s.email}</p>}
                  <p className="text-xs text-gray-400 font-mono">{uid}</p>
                </div>
                <button
                  onClick={() => remove(uid)}
                  className="text-xs text-red-500 hover:text-red-700"
                >
                  {t('remove')}
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── ShiftsTab ─────────────────────────────────────────────────────────────────

const EMPTY_SHIFT = {
  supervisor_user_id: '',
  days_of_week: [1, 2, 3, 4, 5] as number[],
  time_start: '08:00',
  time_end: '18:00',
  timezone: 'UTC',
  active: true,
}

function ShiftsTab({ group, adminToken, onRefresh, t }: {
  group: GroupDetail; adminToken: string; onRefresh: (id: string) => Promise<void>
  t: (k: string) => string
}) {
  const [form,    setForm]    = useState({ ...EMPTY_SHIFT })
  const [adding,  setAdding]  = useState(false)
  const [showAdd, setShowAdd] = useState(false)

  const toggleDay = (dow: number) => {
    setForm(f => ({
      ...f,
      days_of_week: f.days_of_week.includes(dow)
        ? f.days_of_week.filter(d => d !== dow)
        : [...f.days_of_week, dow].sort(),
    }))
  }

  const add = async () => {
    if (!form.supervisor_user_id.trim()) return
    setAdding(true)
    try {
      await apiFetch(`/auth/v1/groups/${group.group_id}/shifts`, adminToken, {
        method: 'POST',
        body: JSON.stringify(form),
      })
      setForm({ ...EMPTY_SHIFT }); setShowAdd(false)
      await onRefresh(group.group_id)
    } finally {
      setAdding(false)
    }
  }

  const remove = async (shiftId: string) => {
    await apiFetch(`/auth/v1/groups/${group.group_id}/shifts/${shiftId}`, adminToken, { method: 'DELETE' })
    await onRefresh(group.group_id)
  }

  const toggleActive = async (shift: GroupShift) => {
    await apiFetch(`/auth/v1/groups/${group.group_id}/shifts/${shift.shift_id}`, adminToken, {
      method: 'PUT',
      body: JSON.stringify({ active: !shift.active }),
    })
    await onRefresh(group.group_id)
  }

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-500">{t('shiftsHint')}</p>
        <button
          onClick={() => setShowAdd(s => !s)}
          className="text-sm text-blue-600 hover:text-blue-800"
        >
          {showAdd ? t('cancel') : `+ ${t('addShift')}`}
        </button>
      </div>

      {/* Add shift form */}
      {showAdd && (
        <div className="border border-gray-200 rounded-lg p-4 space-y-3 bg-gray-50">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">{t('shiftSupervisorId')}</label>
            <input
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="user-uuid"
              value={form.supervisor_user_id}
              onChange={e => setForm(f => ({ ...f, supervisor_user_id: e.target.value }))}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">{t('shiftDays')}</label>
            <div className="flex gap-1">
              {DAY_LABELS.map((lbl, dow) => (
                <button
                  key={dow}
                  onClick={() => toggleDay(dow)}
                  className={`w-9 h-9 rounded text-xs font-medium transition-colors ${
                    form.days_of_week.includes(dow)
                      ? 'bg-blue-600 text-white'
                      : 'bg-white border border-gray-300 text-gray-600 hover:border-blue-400'
                  }`}
                >
                  {lbl}
                </button>
              ))}
            </div>
          </div>
          <div className="flex gap-3">
            <div className="flex-1">
              <label className="block text-xs font-medium text-gray-600 mb-1">{t('shiftStart')}</label>
              <input
                type="time"
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                value={form.time_start}
                onChange={e => setForm(f => ({ ...f, time_start: e.target.value }))}
              />
            </div>
            <div className="flex-1">
              <label className="block text-xs font-medium text-gray-600 mb-1">{t('shiftEnd')}</label>
              <input
                type="time"
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                value={form.time_end}
                onChange={e => setForm(f => ({ ...f, time_end: e.target.value }))}
              />
            </div>
            <div className="flex-1">
              <label className="block text-xs font-medium text-gray-600 mb-1">{t('shiftTimezone')}</label>
              <select
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                value={form.timezone}
                onChange={e => setForm(f => ({ ...f, timezone: e.target.value }))}
              >
                {TIMEZONES.map(tz => <option key={tz} value={tz}>{tz}</option>)}
              </select>
            </div>
          </div>
          <button
            onClick={add}
            disabled={adding || !form.supervisor_user_id.trim()}
            className="w-full py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {adding ? t('saving') : t('addShift')}
          </button>
        </div>
      )}

      {/* Shifts list */}
      {group.shifts.length === 0 && !showAdd ? (
        <p className="text-sm text-gray-400 text-center py-4">{t('noShifts')}</p>
      ) : (
        <div className="space-y-2">
          {group.shifts.map(s => (
            <div key={s.shift_id} className="border border-gray-200 rounded-lg px-4 py-3">
              <div className="flex items-start justify-between">
                <div className="space-y-1">
                  <p className="text-sm font-mono text-gray-700 text-xs">{s.supervisor_user_id}</p>
                  <div className="flex gap-1">
                    {DAY_LABELS.map((lbl, dow) => (
                      <span
                        key={dow}
                        className={`text-xs px-1.5 py-0.5 rounded ${
                          s.days_of_week.includes(dow)
                            ? 'bg-blue-100 text-blue-700'
                            : 'bg-gray-100 text-gray-400'
                        }`}
                      >
                        {lbl}
                      </span>
                    ))}
                  </div>
                  <p className="text-xs text-gray-500">
                    {s.time_start} – {s.time_end} <span className="text-gray-400">({s.timezone})</span>
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => toggleActive(s)}
                    className={`text-xs px-2 py-1 rounded-full font-medium transition-colors ${
                      s.active
                        ? 'bg-green-100 text-green-700 hover:bg-green-200'
                        : 'bg-gray-100 text-gray-500 hover:bg-gray-200'
                    }`}
                  >
                    {s.active ? t('shiftActive') : t('shiftInactive')}
                  </button>
                  <button
                    onClick={() => remove(s.shift_id)}
                    className="text-xs text-red-500 hover:text-red-700"
                  >
                    {t('remove')}
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
