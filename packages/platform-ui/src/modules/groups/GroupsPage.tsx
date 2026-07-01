/**
 * GroupsPage — /config/groups
 *
 * Arc 9 — Agent Groups & Supervisor Scope
 *
 * Manages AgentGroup entities: create/edit/delete groups, configure
 * members (users) and supervisors (users).
 *
 * Drawer tabs per group:
 *   Info        — name + description
 *   Members     — human user list
 *   Supervisors — user list
 *
 * Note (2026-07-02): the "Agents" (agent_type_id membership) and "Shifts"
 * (per-supervisor time windows) tabs were removed. Human/AI typing already
 * lives on Pool.agent_kind (Config > Resources > Pools) — duplicating it
 * here via free-text agent_type_id was an unvalidated second source of
 * truth. Differing shift needs are modeled as separate groups instead of
 * per-member time windows. Group membership is also editable directly from
 * the user's own form in Configuration > Access, so no separate reference
 * is needed here either. See docs/arcos/arc9-agent-groups.md.
 */
import React, { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import { Users, X, Check } from 'lucide-react'

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
  users:       GroupUser[]
  supervisors: GroupUser[]
}

interface GroupUser {
  user_id: string
  id?:     string
  email?:  string
  name?:   string
}

// ── API helpers ───────────────────────────────────────────────────────────────

// G-PROBE platform-wide: auth-api exige Bearer+ABAC `config.usuarios` (strict, sem
// X-Admin-Token). `token` aqui é o access token da sessão.
function adminHeaders(token: string): HeadersInit {
  return token
    ? { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` }
    : { 'Content-Type': 'application/json' }
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

  // G-PROBE platform-wide: autoriza pelo Bearer do operador (session JWT) + ABAC
  // config.usuarios — sem caixa de admin-token (nome `adminToken` mantido p/ diff mínimo).
  const adminToken = session?.accessToken ?? ''

  const [groups,  setGroups]  = useState<Group[]>([])
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)

  // Drawer state
  const [drawerGroup,  setDrawerGroup]  = useState<GroupDetail | null>(null)
  const [drawerOpen,   setDrawerOpen]   = useState(false)
  const [drawerTab,    setDrawerTab]    = useState<'info' | 'members' | 'owners'>('info')
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

  // ── Render ────────────────────────────────────────────────────────────────────

  return (
    <div className="h-full flex flex-col bg-surface-muted">
      {/* Header */}
      <div className="bg-white border-b border-border px-6 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-dark">{t('title')}</h1>
          <p className="text-sm text-muted mt-0.5">{t('subtitle')}</p>
        </div>
        <div className="flex items-center gap-2">
          {adminToken && (
            <button
              onClick={() => setNewGroupOpen(true)}
              className="px-4 py-2 bg-primary text-white text-sm font-medium rounded-lg hover:bg-primary-dark transition-colors"
            >
              + {t('newGroup')}
            </button>
          )}
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="mx-6 mt-4 p-3 bg-red-light border border-red/30 rounded-lg text-sm text-red-text flex justify-between">
          {error}
          <button onClick={() => setError(null)} className="ml-2 text-red hover:text-red-text" aria-label="Fechar erro"><X className="w-3.5 h-3.5" /></button>
        </div>
      )}

      {/* Groups list */}
      <div className="flex-1 overflow-auto p-6">
        {loading ? (
          <div className="flex justify-center py-16"><Spinner /></div>
        ) : groups.length === 0 ? (
          <div className="text-center py-16 text-muted-light">
            <Users className="w-10 h-10 mb-3 mx-auto text-muted-light" aria-hidden="true" />
            <p className="text-sm">{t('empty')}</p>
          </div>
        ) : (
          <div className="grid gap-3">
            {groups.map(g => (
              <div
                key={g.group_id}
                onClick={() => openGroup(g.group_id)}
                className="bg-white border border-border rounded-lg px-5 py-4 flex items-center justify-between cursor-pointer hover:border-primary/30 hover:shadow-sm transition-all"
              >
                <div>
                  <p className="font-medium text-dark">{g.name}</p>
                  {g.description && (
                    <p className="text-sm text-muted mt-0.5">{g.description}</p>
                  )}
                </div>
                <div className="flex items-center gap-4 text-sm text-muted">
                  {g.member_count != null && (
                    <span>{g.member_count} {t('tab.members').toLowerCase()}</span>
                  )}
                  {g.supervisor_count != null && (
                    <span>{g.supervisor_count} {t('tab.owners').toLowerCase()}</span>
                  )}
                  <span className="text-border-strong">›</span>
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
            <label className="block text-sm font-medium text-dark mb-1">{t('fieldName')}</label>
            <input
              className="w-full border border-border-strong rounded-lg px-3 py-2 text-sm mb-3 focus:outline-none focus:ring-2 focus:ring-primary/50"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              placeholder={t('fieldNamePlaceholder')}
            />
            <label className="block text-sm font-medium text-dark mb-1">{t('fieldDescription')}</label>
            <input
              className="w-full border border-border-strong rounded-lg px-3 py-2 text-sm mb-5 focus:outline-none focus:ring-2 focus:ring-primary/50"
              value={newDesc}
              onChange={e => setNewDesc(e.target.value)}
              placeholder={t('fieldDescriptionPlaceholder')}
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => { setNewGroupOpen(false); setNewName(''); setNewDesc('') }}
                className="px-4 py-2 text-sm text-muted hover:text-dark"
              >
                {t('cancel')}
              </button>
              <button
                onClick={createGroup}
                disabled={saving || !newName.trim()}
                className="px-4 py-2 bg-primary text-white text-sm font-medium rounded-lg hover:bg-primary-dark disabled:opacity-50 transition-colors"
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
          tenantId={tenantId}
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

type DrawerTab = 'info' | 'members' | 'owners'

interface DrawerProps {
  group:       GroupDetail | null
  loading:     boolean
  tab:         DrawerTab
  onTabChange: (tab: DrawerTab) => void
  tenantId:    string
  adminToken:  string
  onClose:     () => void
  onDelete:    (id: string) => void
  onRefresh:   (id: string) => Promise<void>
  t:           (key: string, opts?: Record<string, unknown>) => string
}

function GroupDrawer({ group, loading, tab, onTabChange, tenantId, adminToken, onClose, onDelete, onRefresh, t }: DrawerProps) {
  return (
    <div className="fixed inset-0 z-40 flex">
      {/* Backdrop */}
      <div className="flex-1 bg-black/30" onClick={onClose} />

      {/* Panel */}
      <div className="w-full max-w-lg bg-white shadow-2xl flex flex-col">
        {/* Drawer header */}
        <div className="border-b border-border px-6 py-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-dark">
            {loading ? '…' : group?.name ?? ''}
          </h2>
          <div className="flex items-center gap-2">
            {group && (
              <button
                onClick={() => onDelete(group.group_id)}
                className="px-3 py-1.5 text-sm text-red hover:bg-red-light rounded-lg transition-colors"
              >
                {t('delete')}
              </button>
            )}
            <button onClick={onClose} className="p-1.5 text-muted-light hover:text-dark rounded" aria-label="Fechar"><X className="w-4 h-4" /></button>
          </div>
        </div>

        {/* Tabs */}
        <div className="border-b border-border px-6 flex gap-1">
          {(['info', 'members', 'owners'] as const).map(tabKey => (
            <button
              key={tabKey}
              onClick={() => onTabChange(tabKey)}
              className={`px-3 py-3 text-sm font-medium border-b-2 transition-colors ${
                tab === tabKey
                  ? 'border-primary text-primary'
                  : 'border-transparent text-muted hover:text-dark'
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
              {tab === 'info'    && <InfoTab   group={group} adminToken={adminToken} onRefresh={onRefresh} t={t} />}
              {tab === 'members' && <GroupUserChecklist kind="users"       group={group} tenantId={tenantId} adminToken={adminToken} onRefresh={onRefresh} t={t} />}
              {tab === 'owners'  && <GroupUserChecklist kind="supervisors" group={group} tenantId={tenantId} adminToken={adminToken} onRefresh={onRefresh} t={t} />}
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
        <label className="block text-sm font-medium text-dark mb-1">{t('fieldName')}</label>
        <input
          className="w-full border border-border-strong rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50"
          value={name}
          onChange={e => setName(e.target.value)}
        />
      </div>
      <div>
        <label className="block text-sm font-medium text-dark mb-1">{t('fieldDescription')}</label>
        <textarea
          className="w-full border border-border-strong rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50 resize-none"
          rows={3}
          value={desc}
          onChange={e => setDesc(e.target.value)}
        />
      </div>
      <div className="flex items-center gap-3">
        <button
          onClick={save}
          disabled={saving}
          className="px-4 py-2 bg-primary text-white text-sm font-medium rounded-lg hover:bg-primary-dark disabled:opacity-50 transition-colors"
        >
          {saving ? t('saving') : t('save')}
        </button>
        {saved && <span className="inline-flex items-center gap-1 text-sm text-green-text"><Check className="w-3.5 h-3.5" aria-hidden="true" />{t('saved')}</span>}
      </div>
      <div className="pt-4 border-t border-border text-xs text-muted-light space-y-1">
        <p>{t('fieldGroupId')}: <span className="font-mono">{group.group_id}</span></p>
        <p>{t('fieldCreated')}: {new Date(group.created_at).toLocaleString()}</p>
      </div>
    </div>
  )
}

// ── GroupUserChecklist (Members = users, Owners = supervisors) ──────────────────

type UserLite = { id: string | number; name?: string; email?: string }

function GroupUserChecklist({ kind, group, tenantId, adminToken, onRefresh, t }: {
  kind: 'users' | 'supervisors'
  group: GroupDetail; tenantId: string; adminToken: string
  onRefresh: (id: string) => Promise<void>
  t: (k: string, opts?: Record<string, unknown>) => string
}) {
  const [allUsers, setAllUsers] = useState<{ id: string; name: string; email: string }[]>([])
  const [busy,     setBusy]     = useState<string | null>(null)
  const [search,   setSearch]   = useState('')

  useEffect(() => {
    apiFetch<UserLite[] | { users?: UserLite[] }>(`/auth/users?tenant_id=${encodeURIComponent(tenantId)}`, adminToken)
      .then(d => {
        const arr = Array.isArray(d) ? d : (d.users ?? [])
        setAllUsers(arr.map(u => ({ id: String(u.id), name: u.name ?? '', email: u.email ?? '' })))
      })
      .catch(() => { /* user list optional */ })
  }, [tenantId, adminToken])

  const current  = kind === 'users' ? group.users : group.supervisors
  const selected = new Set(current.map(u => u.user_id ?? u.id ?? ''))
  const hintKey  = kind === 'users' ? 'membersHint' : 'ownersHint'
  const emptyKey = kind === 'users' ? 'noMembers'   : 'noOwners'

  const toggle = async (uid: string) => {
    setBusy(uid)
    try {
      if (selected.has(uid)) {
        await apiFetch(`/auth/v1/groups/${group.group_id}/${kind}/${uid}`, adminToken, { method: 'DELETE' })
      } else {
        await apiFetch(`/auth/v1/groups/${group.group_id}/${kind}`, adminToken, { method: 'POST', body: JSON.stringify({ user_id: uid }) })
      }
      await onRefresh(group.group_id)
    } finally { setBusy(null) }
  }

  const q = search.trim().toLowerCase()
  const filtered = q ? allUsers.filter(u => u.name.toLowerCase().includes(q) || u.email.toLowerCase().includes(q)) : allUsers

  return (
    <div className="p-6 space-y-3">
      <p className="text-sm text-muted">{t(hintKey)}</p>
      <input
        className="w-full border border-border-strong rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/50"
        placeholder={t('searchUsers')}
        value={search}
        onChange={e => setSearch(e.target.value)}
      />
      {allUsers.length === 0 ? (
        <p className="text-sm text-muted-light text-center py-4">{t(emptyKey)}</p>
      ) : (
        <div className="divide-y divide-border border border-border rounded-lg max-h-80 overflow-y-auto">
          {filtered.map(u => (
            <label key={u.id} className="flex items-center gap-3 px-4 py-2.5 cursor-pointer hover:bg-surface-muted">
              <input
                type="checkbox"
                checked={selected.has(u.id)}
                disabled={busy === u.id}
                onChange={() => toggle(u.id)}
                className="rounded border-border-strong text-primary focus:ring-primary/40"
              />
              <span className="flex-1 min-w-0">
                <span className="text-sm text-dark">{u.name || u.email || u.id}</span>
                {u.name && u.email && <span className="block text-xs text-muted-light">{u.email}</span>}
              </span>
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

