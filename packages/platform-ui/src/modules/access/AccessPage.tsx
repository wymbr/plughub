/**
 * AccessPage — /config/access
 *
 * Two tabs:
 *   Usuários  — CRUD de usuários + roles + pool restrictions
 *   Templates — Permission templates: criar, editar, aplicar a usuários com escopo de pool
 */
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import { User, FileText, Check, AlertTriangle } from 'lucide-react'
import type { PlatformUser, CreateUserInput, UpdateUserInput, Pool, ModuleConfig } from '@/types'
import ModulePermissionForm, { type ModuleSchema } from '@/components/ModulePermissionForm'

// ── Constants ─────────────────────────────────────────────────────────────────

const ALL_ROLES = ['operator', 'supervisor', 'admin', 'developer', 'business'] as const
type RoleKey = typeof ALL_ROLES[number]

const ROLE_COLORS: Record<RoleKey, { bg: string; text: string }> = {
  operator:   { bg: 'bg-green-light',    text: 'text-green-text'  },
  supervisor: { bg: 'bg-primary-light',  text: 'text-primary'     },
  admin:      { bg: 'bg-ai-light',       text: 'text-ai-text'     },
  developer:  { bg: 'bg-info-light',     text: 'text-info-text'   },
  business:   { bg: 'bg-warning-light',  text: 'text-warning-text'},
}

const ROLE_LABELS: Record<RoleKey, string> = {
  operator:   'Operator',
  supervisor: 'Supervisor',
  admin:      'Admin',
  developer:  'Developer',
  business:   'Business',
}

// Fase 1 — preset copy-on-create: um template guarda um SNAPSHOT do cadastro de usuário
// (role + module_config ABAC rico + accessible_pools + max_concurrent_sessions). Ao
// criar um usuário, o template PRÉ-PREENCHE o form (cópia); não há vínculo vivo nem
// propagação (isso é a Fase 2). Reusa o mesmo ModulePermissionForm do form de usuário.
interface TemplateConfig {
  role?:                    string
  module_config?:           ModuleConfig
  accessible_pools?:        string[]
  max_concurrent_sessions?: number
}

interface PermTemplate {
  id:          string
  tenant_id:   string
  name:        string
  description: string
  config:      TemplateConfig
  created_at:  string
  updated_at:  string
}

// ── API helpers ───────────────────────────────────────────────────────────────

// G-PROBE platform-wide: auth-api passou a exigir Bearer+ABAC `config.usuarios`
// (strict, sem X-Admin-Token). `token` aqui é o access token da sessão.
function authHeaders(token: string): HeadersInit {
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) h['Authorization'] = `Bearer ${token}`
  return h
}

async function apiFetch<T>(url: string, adminToken: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { ...authHeaders(adminToken), ...(init?.headers ?? {}) },
  })
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(`HTTP ${res.status}${body ? ': ' + body : ''}`)
  }
  return res.json() as Promise<T>
}

// ── Hooks ─────────────────────────────────────────────────────────────────────

function useUsers(tenantId: string, adminToken: string) {
  const [users,   setUsers]   = useState<PlatformUser[]>([])
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!tenantId || !adminToken) return
    setLoading(true); setError(null)
    try {
      // auth-api returns a plain array, not {users: [...]}
      const data = await apiFetch<PlatformUser[] | { users: PlatformUser[] }>(
        `/auth/users?tenant_id=${encodeURIComponent(tenantId)}`,
        adminToken,
      )
      const arr = Array.isArray(data)
        ? data
        : (data as { users?: PlatformUser[] }).users ?? []
      setUsers(arr)
    } catch (err) { setError(String(err)) }
    finally { setLoading(false) }
  }, [tenantId, adminToken])

  useEffect(() => { void load() }, [load])
  return { users, loading, error, reload: load }
}

function useTemplates(tenantId: string, adminToken: string) {
  const [templates, setTemplates] = useState<PermTemplate[]>([])
  const [loading,   setLoading]   = useState(false)
  const [error,     setError]     = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!tenantId || !adminToken) return
    setLoading(true); setError(null)
    try {
      // auth-api returns a plain array (response_model=list[TemplateResponse]).
      const data = await apiFetch<PermTemplate[] | { templates: PermTemplate[] }>(
        `/auth/templates?tenant_id=${encodeURIComponent(tenantId)}`,
        adminToken,
      )
      const arr = Array.isArray(data) ? data : (data.templates ?? [])
      setTemplates(arr)
    } catch (err) { setError(String(err)) }
    finally { setLoading(false) }
  }, [tenantId, adminToken])

  useEffect(() => { void load() }, [load])
  return { templates, loading, error, reload: load }
}

function usePools(tenantId: string) {
  const [pools,   setPools]   = useState<Pool[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!tenantId) return
    setLoading(true)
    fetch('/v1/pools', {
      headers: {
        'x-tenant-id': tenantId,
        'x-user-id':   'operator',
      },
    })
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then((data: unknown) => {
        const arr = Array.isArray(data)
          ? data
          : (data as { pools?: Pool[]; items?: Pool[] }).pools
            ?? (data as { pools?: Pool[]; items?: Pool[] }).items
            ?? []
        setPools(arr as Pool[])
      })
      .catch(() => setPools([]))
      .finally(() => setLoading(false))
  }, [tenantId])

  return { pools, loadingPools: loading }
}

function useModules(adminToken: string) {
  const [modules, setModules] = useState<ModuleSchema[]>([])
  useEffect(() => {
    fetch('/auth/modules?active_only=true')
      .then(r => r.ok ? r.json() : [])
      .then((data: unknown) => {
        setModules(Array.isArray(data) ? data as ModuleSchema[] : [])
      })
      .catch(() => setModules([]))
  // Re-fetch when adminToken changes (token may unlock tenant-specific modules)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adminToken])
  return { modules }
}

// ── Shared sub-components ─────────────────────────────────────────────────────

function RoleBadge({ role }: { role: string }) {
  const key    = role as RoleKey
  const colors = ROLE_COLORS[key] ?? { bg: 'bg-surface-alt', text: 'text-dark' }
  const label  = ROLE_LABELS[key] ?? role
  return (
    <span className={`inline-block text-xs font-medium px-2 py-0.5 rounded-full ${colors.bg} ${colors.text}`}>
      {label}
    </span>
  )
}

function StatusBadge({ active }: { active: boolean }) {
  const { t } = useTranslation('access')
  return active
    ? <span className="inline-block text-xs font-semibold px-2 py-0.5 rounded-full bg-green-light text-green-text">{t('users.active')}</span>
    : <span className="inline-block text-xs font-semibold px-2 py-0.5 rounded-full bg-red-light text-red-text">{t('users.inactive')}</span>
}

function DeleteButton({ onConfirm }: { onConfirm: () => void }) {
  const { t } = useTranslation('access')
  const [stage, setStage] = useState<0 | 1 | 2>(0)
  if (stage === 0) return (
    <button onClick={() => setStage(1)} className="text-xs text-red hover:text-red-text transition-colors">{t('form.delete')}</button>
  )
  if (stage === 1) return (
    <span className="flex items-center gap-1">
      <button onClick={() => setStage(2)} className="text-xs font-semibold text-red-text hover:text-red-text/80">{t('form.confirm')}</button>
      <span className="text-border-strong">|</span>
      <button onClick={() => setStage(0)} className="text-xs text-muted-light hover:text-muted">{t('form.cancel')}</button>
    </span>
  )
  return <button onClick={onConfirm} className="inline-flex items-center gap-1 text-xs font-bold text-red-text animate-pulse"><AlertTriangle className="w-3 h-3" aria-hidden="true" />{t('form.delete')}</button>
}

// ── UserModal ─────────────────────────────────────────────────────────────────

interface UserModalProps {
  tenantId:       string
  adminToken:     string
  user:           PlatformUser | null
  availablePools: Pool[]
  modules:        ModuleSchema[]
  templates:      PermTemplate[]
  onClose:        () => void
  onSaved:        () => void
}

function UserModal({ tenantId, adminToken, user, availablePools, modules, templates, onClose, onSaved }: UserModalProps) {
  const { t } = useTranslation('access')
  const isEdit = user !== null
  const [templateId, setTemplateId] = useState('')

  // Copy-on-create: applying a template pre-fills the form from its snapshot. No live
  // link — the admin can tweak freely afterwards (any edit is just a normal user).
  function applyTemplate(id: string) {
    setTemplateId(id)
    const tpl = templates.find(x => x.id === id)
    if (!tpl) return
    const c = tpl.config ?? {}
    if (c.role) setRoles([c.role])
    setModuleConfig(c.module_config ?? {})
    setSelectedPools(new Set(c.accessible_pools ?? []))
    if (c.max_concurrent_sessions) setMaxConcurrentSessions(c.max_concurrent_sessions)
  }
  const [name,         setName]         = useState(user?.name ?? '')
  const [email,        setEmail]        = useState(user?.email ?? '')
  const [password,     setPassword]     = useState('')
  const [roles,        setRoles]        = useState<string[]>(user?.roles ?? ['operator'])
  const [selectedPools,setSelectedPools]= useState<Set<string>>(
    new Set(user?.accessible_pools ?? [])
  )
  const [unrestricted, setUnrestricted] = useState(user?.unrestricted ?? false)
  const [moduleConfig,            setModuleConfig]            = useState<ModuleConfig>(user?.module_config ?? {})
  const [maxConcurrentSessions,   setMaxConcurrentSessions]   = useState(user?.max_concurrent_sessions ?? 3)
  const [active,  setActive]  = useState(user?.active ?? true)
  const [saving,  setSaving]  = useState(false)
  const [err,     setErr]     = useState<string | null>(null)
  const backdropRef = useRef<HTMLDivElement>(null)

  // The user list/detail response does NOT include module_config — it lives behind a
  // dedicated endpoint. Hydrate the ABAC form on edit so assigned permissions show up
  // (otherwise every field falsely renders as "no access").
  useEffect(() => {
    if (!user?.id) return
    apiFetch<ModuleConfig>(`/auth/users/${user.id}/module-config`, adminToken)
      .then(cfg => setModuleConfig(cfg ?? {}))
      .catch(() => { /* keep current (empty) config on failure */ })
  }, [user?.id, adminToken])

  // ── Group association (Arc 9) ────────────────────────────────────────────────
  // Group membership/supervision is stored group-side (no reverse endpoint), so we
  // list groups and probe each group's detail to learn this user's current state.
  const [allGroups,        setAllGroups]        = useState<{ group_id: string; name: string }[]>([])
  const [memberGroups,     setMemberGroups]     = useState<Set<string>>(new Set())
  const [supervisorGroups, setSupervisorGroups] = useState<Set<string>>(new Set())
  const initialGroupsRef = useRef<{ member: Set<string>; supervisor: Set<string> }>({ member: new Set(), supervisor: new Set() })

  useEffect(() => {
    let cancelled = false
    type GUser = { user_id?: string; id?: string }
    apiFetch<{ group_id: string; name: string }[]>(
      `/auth/v1/groups?tenant_id=${encodeURIComponent(tenantId)}`, adminToken,
    )
      .then(async groups => {
        const list = groups ?? []
        if (!cancelled) setAllGroups(list.map(g => ({ group_id: g.group_id, name: g.name })))
        if (!user?.id) return
        const mem = new Set<string>(); const sup = new Set<string>()
        const has = (arr?: GUser[]) => (arr ?? []).some(u => u.user_id === user.id || u.id === user.id)
        await Promise.all(list.map(async g => {
          const d = await apiFetch<{ users?: GUser[]; supervisors?: GUser[] }>(`/auth/v1/groups/${g.group_id}`, adminToken)
          if (has(d.users)) mem.add(g.group_id)
          if (has(d.supervisors)) sup.add(g.group_id)
        }))
        if (cancelled) return
        setMemberGroups(new Set(mem)); setSupervisorGroups(new Set(sup))
        initialGroupsRef.current = { member: new Set(mem), supervisor: new Set(sup) }
      })
      .catch(() => { /* groups optional */ })
    return () => { cancelled = true }
  }, [user?.id, adminToken, tenantId])

  function toggleGroup(kind: 'member' | 'supervisor', gid: string) {
    const setter = kind === 'member' ? setMemberGroups : setSupervisorGroups
    setter(prev => {
      const next = new Set(prev)
      next.has(gid) ? next.delete(gid) : next.add(gid)
      return next
    })
  }

  // Diff desired vs initial group state and apply via the group-side endpoints.
  async function applyGroupChanges(userId: string) {
    const init = initialGroupsRef.current
    const tasks: Promise<unknown>[] = []
    for (const gid of memberGroups) if (!init.member.has(gid))
      tasks.push(apiFetch(`/auth/v1/groups/${gid}/users`, adminToken, { method: 'POST', body: JSON.stringify({ user_id: userId }) }))
    for (const gid of init.member) if (!memberGroups.has(gid))
      tasks.push(apiFetch(`/auth/v1/groups/${gid}/users/${userId}`, adminToken, { method: 'DELETE' }))
    for (const gid of supervisorGroups) if (!init.supervisor.has(gid))
      tasks.push(apiFetch(`/auth/v1/groups/${gid}/supervisors`, adminToken, { method: 'POST', body: JSON.stringify({ user_id: userId }) }))
    for (const gid of init.supervisor) if (!supervisorGroups.has(gid))
      tasks.push(apiFetch(`/auth/v1/groups/${gid}/supervisors/${userId}`, adminToken, { method: 'DELETE' }))
    await Promise.all(tasks)
  }

  function togglePool(poolId: string) {
    setSelectedPools(prev => {
      const next = new Set(prev)
      next.has(poolId) ? next.delete(poolId) : next.add(poolId)
      return next
    })
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (roles.length === 0) { setErr(t('errors.selectRole')); return }
    setSaving(true); setErr(null)
    try {
      const accessiblePools = Array.from(selectedPools)
      if (isEdit) {
        const body: UpdateUserInput = { name: name || undefined, roles, accessible_pools: accessiblePools, unrestricted, active, max_concurrent_sessions: maxConcurrentSessions }
        if (password) body.password = password
        await apiFetch(`/auth/users/${user!.id}`, adminToken, { method: 'PATCH', body: JSON.stringify(body) })
        // Save ABAC module config separately (PUT replaces the whole config)
        await apiFetch(`/auth/users/${user!.id}/module-config`, adminToken, {
          method: 'PUT', body: JSON.stringify(moduleConfig),
        })
        await applyGroupChanges(user!.id)
      } else {
        const body: CreateUserInput = { tenant_id: tenantId, email, name, password, roles, accessible_pools: accessiblePools, unrestricted, max_concurrent_sessions: maxConcurrentSessions }
        const created = await apiFetch<{ id: string }>('/auth/users', adminToken, { method: 'POST', body: JSON.stringify(body) })
        // Set ABAC module config on the newly created user if anything was configured
        if (Object.keys(moduleConfig).length > 0) {
          await apiFetch(`/auth/users/${created.id}/module-config`, adminToken, {
            method: 'PUT', body: JSON.stringify(moduleConfig),
          })
        }
        await applyGroupChanges(created.id)
      }
      onSaved(); onClose()
    } catch (ex) { setErr(String(ex)) }
    finally { setSaving(false) }
  }

  const allSelected = availablePools.length > 0 && availablePools.every(p => selectedPools.has(p.pool_id))

  return (
    <div ref={backdropRef} className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4"
      onClick={e => { if (e.target === backdropRef.current) onClose() }}>
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[90vh] flex flex-col">
        <div className="flex items-center justify-between px-6 py-4 border-b border-border flex-shrink-0">
          <h2 className="text-lg font-semibold text-dark">{isEdit ? t('users.editUser') : t('users.newUser')}</h2>
          <button onClick={onClose} className="text-muted-light hover:text-muted text-xl leading-none">&times;</button>
        </div>
        <form onSubmit={handleSubmit} className="px-6 py-5 space-y-4 overflow-y-auto flex-1">
          {!isEdit && templates.length > 0 && (
            <div className="bg-surface-muted border border-border rounded-lg p-3">
              <label className="block text-sm font-medium text-dark mb-1">{t('users.template')}</label>
              <select value={templateId} onChange={e => applyTemplate(e.target.value)}
                className="w-full border border-border-strong rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40 bg-white">
                <option value="">{t('users.templateNone')}</option>
                {templates.map(tpl => <option key={tpl.id} value={tpl.id}>{tpl.name}</option>)}
              </select>
              <p className="text-xs text-muted-light mt-1">{t('users.templateHint')}</p>
            </div>
          )}
          <div>
            <label className="block text-sm font-medium text-dark mb-1">{t('users.name')}</label>
            <input type="text" value={name} onChange={e => setName(e.target.value)} required
              placeholder={t('users.fullName')}
              className="w-full border border-border-strong rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40" />
          </div>
          {!isEdit && (
            <div>
              <label className="block text-sm font-medium text-dark mb-1">{t('users.email')}</label>
              <input type="email" value={email} onChange={e => setEmail(e.target.value)} required
                placeholder={t('users.emailPlaceholder')}
                className="w-full border border-border-strong rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40" />
            </div>
          )}
          <div>
            <label className="block text-sm font-medium text-dark mb-1">
              {isEdit ? t('users.newPassword') : t('users.password')}
            </label>
            <input type="password" value={password} onChange={e => setPassword(e.target.value)} required={!isEdit}
              placeholder={isEdit ? t('users.passwordBlank') : t('users.passwordMin8')}
              className="w-full border border-border-strong rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40" />
          </div>
          <div>
            <label className="block text-sm font-medium text-dark mb-1">{t('users.role')}</label>
            <select value={roles[0] ?? 'operator'} onChange={e => setRoles([e.target.value])}
              className="w-full border border-border-strong rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40 bg-white">
              {ALL_ROLES.map(role => (
                <option key={role} value={role}>{ROLE_LABELS[role]}</option>
              ))}
            </select>
          </div>

          {/* Max concurrent sessions */}
          <div>
            <label className="block text-sm font-medium text-dark mb-1">{t('users.maxConcurrentSessions')}</label>
            <input
              type="number"
              min={1}
              max={50}
              value={maxConcurrentSessions}
              onChange={e => setMaxConcurrentSessions(Math.max(1, parseInt(e.target.value) || 1))}
              className="w-full border border-border-strong rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
            />
            <p className="text-xs text-muted-light mt-1">{t('users.maxConcurrentSessionsDescription')}</p>
          </div>

          {/* Escopo irrestrito — declaracao EXPLICITA (passo 2, 2026-08-27).
              Marcar aqui e diferente de "nao escolher nenhum pool": o segundo depende
              da convencao implicita `[] = todos`, que sera invertida. */}
          <div className="rounded-lg border border-border-strong p-3">
            <label className="flex items-start gap-2 cursor-pointer">
              <input type="checkbox" checked={unrestricted}
                onChange={e => { setUnrestricted(e.target.checked); if (e.target.checked) setSelectedPools(new Set()) }}
                className="mt-0.5" />
              <span>
                <span className="text-sm font-medium text-dark">{t('users.unrestricted')}</span>
                <span className="block text-xs text-muted-light mt-0.5">{t('users.unrestrictedDescription')}</span>
              </span>
            </label>
          </div>

          {/* Pool multi-select */}
          <div className={unrestricted ? 'opacity-40 pointer-events-none' : undefined}>
            <div className="flex items-center justify-between mb-1.5">
              <label className="text-sm font-medium text-dark">
                {t('users.pools')}
                <span className="ml-1.5 text-xs font-normal text-muted-light">
                  {selectedPools.size === 0 ? t('users.poolsNone') : t('users.poolsSelected', { count: selectedPools.size })}
                </span>
              </label>
              {availablePools.length > 0 && (
                <button type="button" onClick={() => setSelectedPools(allSelected ? new Set() : new Set(availablePools.map(p => p.pool_id)))}
                  className="text-xs text-muted-light hover:text-muted transition-colors">
                  {allSelected ? t('users.deselectAll') : t('users.selectAll')}
                </button>
              )}
            </div>
            {availablePools.length === 0 ? (
              <div className="border border-border rounded-lg px-3 py-2 text-xs text-muted-light italic bg-surface-muted">
                {t('users.noPoolsConfigured')}
              </div>
            ) : (
              <div className="border border-border rounded-lg divide-y divide-border max-h-40 overflow-y-auto">
                {availablePools.map(pool => {
                  const checked = selectedPools.has(pool.pool_id)
                  return (
                    <label key={pool.pool_id}
                      className={`flex items-start gap-3 px-3 py-2 cursor-pointer transition-colors hover:bg-surface-muted ${checked ? 'bg-primary/5' : ''}`}>
                      <input type="checkbox" checked={checked} onChange={() => togglePool(pool.pool_id)}
                        className="w-4 h-4 rounded accent-primary flex-shrink-0 mt-0.5" />
                      <div className="flex-1 min-w-0 overflow-hidden">
                        <p className="text-sm font-mono text-dark truncate">{pool.pool_id}</p>
                        {pool.description && (
                          <p className="text-xs text-muted-light truncate">{pool.description}</p>
                        )}
                        {pool.channel_types.length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-1">
                            {pool.channel_types.map(ch => (
                              <span key={ch} className="text-xs bg-surface-alt text-muted px-1 rounded">{ch}</span>
                            ))}
                          </div>
                        )}
                      </div>
                    </label>
                  )
                })}
              </div>
            )}
            <p className="text-xs text-muted-light mt-1">{t('users.poolsDescription')}</p>
          </div>

          {/* ABAC module permissions */}
          {modules.length > 0 && (
            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="text-sm font-medium text-dark">{t('users.permissions')}</label>
                <span className="text-xs text-muted-light">ABAC</span>
              </div>
              <div className="border border-border rounded-lg overflow-hidden">
                <ModulePermissionForm
                  modules={modules}
                  value={moduleConfig}
                  onChange={setModuleConfig}
                  readOnly={false}
                />
              </div>
              <p className="text-xs text-muted-light mt-1">
                {t('users.permissionsDescription')}
              </p>
            </div>
          )}

          {/* Group association (Arc 9) */}
          {allGroups.length > 0 && (
            <div>
              <label className="text-sm font-medium text-dark">{t('users.groupsSection')}</label>
              <div className="border border-border rounded-lg divide-y divide-border mt-2">
                <div className="flex items-center px-3 py-1.5 bg-surface-muted text-2xs font-semibold text-muted-light uppercase tracking-wide">
                  <span className="flex-1">{t('users.group')}</span>
                  <span className="w-20 text-center">{t('users.colMember')}</span>
                  <span className="w-20 text-center">{t('users.colSupervisor')}</span>
                </div>
                {allGroups.map(g => (
                  <div key={g.group_id} className="flex items-center px-3 py-2 text-sm">
                    <span className="flex-1 text-dark truncate">{g.name}</span>
                    <span className="w-20 flex justify-center">
                      <input
                        type="checkbox"
                        checked={memberGroups.has(g.group_id)}
                        onChange={() => toggleGroup('member', g.group_id)}
                        className="rounded border-border-strong text-primary focus:ring-primary/40"
                      />
                    </span>
                    <span className="w-20 flex justify-center">
                      <input
                        type="checkbox"
                        checked={supervisorGroups.has(g.group_id)}
                        onChange={() => toggleGroup('supervisor', g.group_id)}
                        className="rounded border-border-strong text-primary focus:ring-primary/40"
                      />
                    </span>
                  </div>
                ))}
              </div>
              <p className="text-xs text-muted-light mt-1">{t('users.groupsHint')}</p>
            </div>
          )}

          {isEdit && (
            <div className="flex items-center gap-3">
              <label className="text-sm font-medium text-dark">{t('form.status')}</label>
              <button type="button" onClick={() => setActive(v => !v)}
                className={`relative inline-flex h-6 w-11 rounded-full transition-colors ${active ? 'bg-green' : 'bg-border-strong'}`}>
                <span className={`absolute top-0.5 left-0.5 h-5 w-5 bg-white rounded-full shadow transition-transform ${active ? 'translate-x-5' : ''}`} />
              </button>
              <span className="text-sm text-muted">{active ? t('users.active') : t('users.inactive')}</span>
            </div>
          )}
          {err && <p className="text-sm text-red-text bg-red-light border border-red/30 rounded-lg px-3 py-2">{err}</p>}
          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose}
              className="px-4 py-2 text-sm text-muted border border-border-strong rounded-lg hover:bg-surface-muted transition-colors">
              {t('form.cancel')}
            </button>
            <button type="submit" disabled={saving}
              className="px-5 py-2 text-sm font-medium text-white bg-primary rounded-lg hover:bg-primary/90 disabled:opacity-50 transition-colors">
              {saving ? t('form.saving') : isEdit ? t('users.saveChanges') : t('users.createUser')}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── TemplateEditor (Fase 1 — preset copy-on-create) ──────────────────────────

interface TemplateEditorProps {
  tenantId:       string
  adminToken:     string
  availablePools: Pool[]
  modules:        ModuleSchema[]
  template:       PermTemplate | null   // null = create mode
  onSaved:        () => void
  onDeleted?:     () => void
}

function TemplateEditor({ tenantId, adminToken, availablePools, modules, template, onSaved, onDeleted }: TemplateEditorProps) {
  const { t } = useTranslation('access')
  const isEdit = template !== null
  const cfg0 = template?.config ?? {}

  const [name,         setName]         = useState(template?.name ?? '')
  const [description,  setDescription]  = useState(template?.description ?? '')
  const [role,         setRole]         = useState<string>(cfg0.role ?? 'operator')
  const [moduleConfig, setModuleConfig] = useState<ModuleConfig>(cfg0.module_config ?? {})
  const [selectedPools,setSelectedPools]= useState<Set<string>>(new Set(cfg0.accessible_pools ?? []))
  const [maxConcurrent,setMaxConcurrent]= useState(cfg0.max_concurrent_sessions ?? 3)
  const [saving,       setSaving]       = useState(false)
  const [delStage,     setDelStage]     = useState(0)
  const [err,          setErr]          = useState<string | null>(null)
  const [successMsg,   setSuccessMsg]   = useState<string | null>(null)

  // Reset when template changes
  useEffect(() => {
    const c = template?.config ?? {}
    setName(template?.name ?? '')
    setDescription(template?.description ?? '')
    setRole(c.role ?? 'operator')
    setModuleConfig(c.module_config ?? {})
    setSelectedPools(new Set(c.accessible_pools ?? []))
    setMaxConcurrent(c.max_concurrent_sessions ?? 3)
    setErr(null); setDelStage(0); setSuccessMsg(null)
  }, [template?.id])

  function togglePool(poolId: string) {
    setSelectedPools(prev => {
      const next = new Set(prev)
      next.has(poolId) ? next.delete(poolId) : next.add(poolId)
      return next
    })
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) { setErr(t('errors.templateName')); return }
    setSaving(true); setErr(null); setSuccessMsg(null)
    try {
      const config: TemplateConfig = {
        role,
        module_config: moduleConfig,
        accessible_pools: Array.from(selectedPools),
        max_concurrent_sessions: maxConcurrent,
      }
      const body = { tenant_id: tenantId, name: name.trim(), description: description.trim(), config }
      if (isEdit) await apiFetch(`/auth/templates/${template!.id}`, adminToken, { method: 'PATCH', body: JSON.stringify(body) })
      else        await apiFetch('/auth/templates', adminToken, { method: 'POST', body: JSON.stringify(body) })
      setSuccessMsg(isEdit ? t('messages.templateSaved') : t('messages.templateCreated'))
      onSaved()
    } catch (ex) { setErr(String(ex)) }
    finally { setSaving(false) }
  }

  async function handleDelete() {
    try {
      const res = await fetch(`/auth/templates/${template!.id}`, {
        method: 'DELETE', headers: authHeaders(adminToken),
      })
      if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`)
      onDeleted?.()
    } catch (ex) { setErr(String(ex)) }
  }

  const allSelected = availablePools.length > 0 && availablePools.every(p => selectedPools.has(p.pool_id))

  return (
    <div className="flex-1 overflow-y-auto">
      <form onSubmit={handleSave}>
        {/* Header */}
        <div className="px-6 py-4 border-b border-border bg-white sticky top-0 z-10 flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-dark">{isEdit ? template.name : t('templates.newTemplate')}</h2>
            <p className="text-xs text-muted-light mt-0.5">{t('templates.presetHint')}</p>
          </div>
          <div className="flex items-center gap-2">
            {isEdit && delStage === 0 && (
              <button type="button" onClick={() => setDelStage(1)}
                className="px-3 py-1.5 text-xs text-red border border-red/30 rounded-lg hover:bg-red-light transition-colors">{t('form.delete')}</button>
            )}
            {isEdit && delStage === 1 && (
              <span className="flex items-center gap-1">
                <button type="button" onClick={handleDelete} className="text-xs font-semibold text-red-text hover:text-red-text/80">{t('templates.confirmDelete')}</button>
                <span className="text-border-strong text-xs">|</span>
                <button type="button" onClick={() => setDelStage(0)} className="text-xs text-muted-light hover:text-muted">{t('form.cancel')}</button>
              </span>
            )}
            <button type="submit" disabled={saving}
              className="px-4 py-1.5 text-xs font-medium text-white bg-primary rounded-lg hover:bg-primary/90 disabled:opacity-50 transition-colors">
              {saving ? t('form.saving') : isEdit ? t('form.save') : t('templates.createTemplate')}
            </button>
          </div>
        </div>

        <div className="px-6 py-5 space-y-5 max-w-2xl">
          {/* Name + description */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-dark mb-1">{t('templates.name')}</label>
              <input type="text" value={name} onChange={e => setName(e.target.value)} required
                placeholder={t('templates.namePlaceholder')}
                className="w-full border border-border-strong rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40" />
            </div>
            <div>
              <label className="block text-sm font-medium text-dark mb-1">{t('templates.description')}</label>
              <input type="text" value={description} onChange={e => setDescription(e.target.value)}
                placeholder={t('templates.descriptionPlaceholder')}
                className="w-full border border-border-strong rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40" />
            </div>
          </div>

          {/* Role + max concurrent */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-dark mb-1">{t('users.role')}</label>
              <select value={role} onChange={e => setRole(e.target.value)}
                className="w-full border border-border-strong rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40 bg-white">
                {ALL_ROLES.map(r => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-dark mb-1">{t('users.maxConcurrentSessions')}</label>
              <input type="number" min={1} max={50} value={maxConcurrent}
                onChange={e => setMaxConcurrent(Math.max(1, parseInt(e.target.value) || 1))}
                className="w-full border border-border-strong rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40" />
            </div>
          </div>

          {/* Pools */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="text-sm font-medium text-dark">{t('users.pools')}
                <span className="ml-1.5 text-xs font-normal text-muted-light">
                  {selectedPools.size === 0 ? t('users.poolsNone') : t('users.poolsSelected', { count: selectedPools.size })}
                </span>
              </label>
              {availablePools.length > 0 && (
                <button type="button" onClick={() => setSelectedPools(allSelected ? new Set() : new Set(availablePools.map(p => p.pool_id)))}
                  className="text-xs text-muted-light hover:text-muted transition-colors">{allSelected ? t('users.deselectAll') : t('users.selectAll')}</button>
              )}
            </div>
            {availablePools.length === 0 ? (
              <div className="border border-border rounded-lg px-3 py-2 text-xs text-muted-light italic bg-surface-muted">{t('users.noPoolsConfigured')}</div>
            ) : (
              <div className="border border-border rounded-lg divide-y divide-border max-h-40 overflow-y-auto">
                {availablePools.map(pool => {
                  const checked = selectedPools.has(pool.pool_id)
                  return (
                    <label key={pool.pool_id} className={`flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-surface-muted ${checked ? 'bg-primary/5' : ''}`}>
                      <input type="checkbox" checked={checked} onChange={() => togglePool(pool.pool_id)} className="w-4 h-4 rounded accent-primary" />
                      <span className="text-sm font-mono text-dark truncate">{pool.pool_id}</span>
                    </label>
                  )
                })}
              </div>
            )}
            <p className="text-xs text-muted-light mt-1">{t('users.poolsDescription')}</p>
          </div>

          {/* ABAC module permissions (reuses the user form editor) */}
          {modules.length > 0 && (
            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="text-sm font-medium text-dark">{t('users.permissions')}</label>
                <span className="text-xs text-muted-light">ABAC</span>
              </div>
              <div className="border border-border rounded-lg overflow-hidden">
                <ModulePermissionForm modules={modules} value={moduleConfig} onChange={setModuleConfig} readOnly={false} />
              </div>
            </div>
          )}

          {successMsg && (
            <p className="inline-flex items-center gap-1.5 text-sm text-green-text bg-green-light border border-green/30 rounded-lg px-4 py-2"><Check className="w-3.5 h-3.5" aria-hidden="true" />{successMsg}</p>
          )}
          {err && (
            <p className="text-sm text-red-text bg-red-light border border-red/30 rounded-lg px-4 py-2">{err}</p>
          )}
        </div>
      </form>
    </div>
  )
}

// ── UsersPane ─────────────────────────────────────────────────────────────────

interface UsersPaneProps {
  tenantId:       string
  adminToken:     string
  availablePools: Pool[]
  modules:        ModuleSchema[]
  templates:      PermTemplate[]
  users:          PlatformUser[]
  loading:        boolean
  error:          string | null
  reload:         () => void
}

function UsersPane({ tenantId, adminToken, availablePools, modules, templates, users, loading, error, reload }: UsersPaneProps) {
  const { t } = useTranslation('access')
  const [search,       setSearch]       = useState('')
  const [roleFilter,   setRoleFilter]   = useState('all')
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'inactive'>('all')
  const [modalUser,    setModalUser]    = useState<PlatformUser | null | undefined>(undefined)
  const [actionErr,    setActionErr]    = useState<string | null>(null)

  const filtered = users.filter(u => {
    const q = search.toLowerCase()
    return (!q || u.name.toLowerCase().includes(q) || u.email.toLowerCase().includes(q))
      && (roleFilter === 'all' || u.roles.includes(roleFilter))
      && (statusFilter === 'all' || (statusFilter === 'active' ? u.active : !u.active))
  })

  const total    = users.length
  const active   = users.filter(u => u.active).length
  const byRole   = ALL_ROLES.reduce<Record<string, number>>((acc, r) => {
    acc[r] = users.filter(u => u.roles.includes(r)).length; return acc
  }, {})

  async function handleToggleActive(u: PlatformUser) {
    setActionErr(null)
    try {
      await apiFetch(`/auth/users/${u.id}`, adminToken, { method: 'PATCH', body: JSON.stringify({ active: !u.active }) })
      void reload()
    } catch (ex) { setActionErr(String(ex)) }
  }

  async function handleDelete(u: PlatformUser) {
    setActionErr(null)
    try {
      const res = await fetch(`/auth/users/${u.id}`, { method: 'DELETE', headers: authHeaders(adminToken) })
      if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`)
      void reload()
    } catch (ex) { setActionErr(String(ex)) }
  }

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Sidebar */}
      <aside className="w-52 flex-shrink-0 border-r border-border bg-surface-muted flex flex-col overflow-y-auto">
        <div className="p-4 border-b border-border space-y-1">
          <p className="text-xs font-semibold text-muted uppercase tracking-wider mb-2">{t('users.summary')}</p>
          <div className="flex justify-between text-sm"><span className="text-muted">{t('users.total')}</span><span className="font-semibold">{total}</span></div>
          <div className="flex justify-between text-sm"><span className="text-green">{t('users.active')}</span><span className="font-semibold text-green-text">{active}</span></div>
          <div className="flex justify-between text-sm"><span className="text-red">{t('users.inactive')}</span><span className="font-semibold text-red-text">{total - active}</span></div>
        </div>
        <div className="p-4 border-b border-border">
          <p className="text-xs font-semibold text-muted uppercase tracking-wider mb-3">{t('users.role')}</p>
          <div className="space-y-1">
            {(['all', ...ALL_ROLES] as const).map(role => {
              const isAll = role === 'all'
              const active = roleFilter === role
              const colors = !isAll ? ROLE_COLORS[role as RoleKey] : null
              return (
                <button key={role} onClick={() => setRoleFilter(role)}
                  className={`w-full text-left text-sm px-2 py-1.5 rounded-lg flex justify-between items-center transition-colors ${
                    active
                      ? colors ? `${colors.bg} ${colors.text} font-semibold` : 'bg-primary text-white'
                      : 'text-muted hover:bg-surface-alt'
                  }`}>
                  <span>{isAll ? t('users.filterAll') : ROLE_LABELS[role as RoleKey]}</span>
                  {!isAll && <span className="text-xs font-mono">{byRole[role] ?? 0}</span>}
                </button>
              )
            })}
          </div>
        </div>
        <div className="p-4">
          <p className="text-xs font-semibold text-muted uppercase tracking-wider mb-3">{t('users.status')}</p>
          <div className="space-y-1">
            {(['all', 'active', 'inactive'] as const).map(s => (
              <button key={s} onClick={() => setStatusFilter(s)}
                className={`w-full text-left text-sm px-2 py-1.5 rounded-lg transition-colors ${statusFilter === s ? 'bg-primary text-white' : 'text-muted hover:bg-surface-alt'}`}>
                {s === 'all' ? t('users.filterAll') : s === 'active' ? t('users.active') : t('users.inactive')}
              </button>
            ))}
          </div>
        </div>
      </aside>

      {/* Main */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Subheader */}
        <div className="px-6 py-3 border-b border-border bg-white flex items-center gap-3 flex-shrink-0">
          <input type="search" value={search} onChange={e => setSearch(e.target.value)}
            placeholder={t('users.searchPlaceholder')}
            className="flex-1 max-w-md border border-border-strong rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40" />
          <button onClick={() => setModalUser(null)}
            className="ml-auto flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-white bg-primary rounded-lg hover:bg-primary/90 transition-colors">
            + {t('users.create')}
          </button>
        </div>

        {(error || actionErr) && (
          <div className="mx-6 mt-3 flex-shrink-0 text-sm text-red-text bg-red-light border border-red/30 rounded-lg px-4 py-2">
            {error ?? actionErr}
          </div>
        )}

        {loading ? (
          <div className="flex-1 flex items-center justify-center"><Spinner /></div>
        ) : (
          <div className="flex-1 overflow-auto">
            {filtered.length === 0 ? (
              <div className="flex items-center justify-center h-40">
                <p className="text-muted-light text-sm">{users.length === 0 ? t('users.noUsers') : t('users.noResults')}</p>
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-surface-muted border-b border-border sticky top-0 z-10">
                  <tr>
                    <th className="text-left text-xs font-semibold text-muted uppercase tracking-wider px-6 py-3">{t('users.tableUser')}</th>
                    <th className="text-left text-xs font-semibold text-muted uppercase tracking-wider px-4 py-3">{t('users.tableRoles')}</th>
                    <th className="text-left text-xs font-semibold text-muted uppercase tracking-wider px-4 py-3">{t('users.tablePools')}</th>
                    <th className="text-left text-xs font-semibold text-muted uppercase tracking-wider px-4 py-3">{t('users.status')}</th>
                    <th className="px-4 py-3" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-border bg-white">
                  {filtered.map(u => (
                    <tr key={u.id} className="hover:bg-surface-muted transition-colors">
                      <td className="px-6 py-3">
                        <p className="font-medium text-dark">{u.name}</p>
                        <p className="text-xs text-muted-light mt-0.5">{u.email}</p>
                      </td>
                      <td className="px-4 py-3"><div className="flex flex-wrap gap-1">{u.roles.map(r => <RoleBadge key={r} role={r} />)}</div></td>
                      <td className="px-4 py-3">
                        {u.accessible_pools.length === 0
                          ? (u.unrestricted
                              ? <span className="text-xs text-muted-light italic">{t('users.allPools')}</span>
                              : <span className="text-xs text-warning italic" title={t('users.legacyAllPoolsHint')}>{t('users.legacyAllPools')}</span>)
                          : <div className="flex flex-wrap gap-1">
                              {u.accessible_pools.slice(0, 3).map(p => (
                                <span key={p} className="text-xs bg-surface-alt text-muted px-1.5 py-0.5 rounded font-mono">{p}</span>
                              ))}
                              {u.accessible_pools.length > 3 && <span className="text-xs text-muted-light">+{u.accessible_pools.length - 3}</span>}
                            </div>
                        }
                      </td>
                      <td className="px-4 py-3"><StatusBadge active={u.active} /></td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-3 justify-end whitespace-nowrap">
                          <button onClick={() => setModalUser(u)} className="text-xs text-primary hover:text-primary/80 font-medium transition-colors">{t('form.edit')}</button>
                          <button onClick={() => handleToggleActive(u)}
                            className={`text-xs font-medium transition-colors ${u.active ? 'text-warning hover:text-warning-text' : 'text-green hover:text-green-text'}`}>
                            {u.active ? t('users.deactivate') : t('users.reactivate')}
                          </button>
                          <DeleteButton onConfirm={() => handleDelete(u)} />
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>

      {modalUser !== undefined && (
        <UserModal tenantId={tenantId} adminToken={adminToken} user={modalUser}
          availablePools={availablePools} modules={modules} templates={templates}
          onClose={() => setModalUser(undefined)} onSaved={reload} />
      )}
    </div>
  )
}

// ── TemplatesPane ─────────────────────────────────────────────────────────────

interface TemplatesPaneProps {
  tenantId:       string
  adminToken:     string
  availablePools: Pool[]
  modules:        ModuleSchema[]
  templates:      PermTemplate[]
  loading:        boolean
  error:          string | null
  reload:         () => void
}

function TemplatesPane({ tenantId, adminToken, availablePools, modules, templates, loading, error, reload }: TemplatesPaneProps) {
  const { t } = useTranslation('access')
  const [selected, setSelected]   = useState<PermTemplate | null | undefined>(undefined)
  // undefined = nothing selected; null = create mode; PermTemplate = edit mode

  function handleSaved() {
    void reload()
    // If creating, switch back to list (don't auto-select new)
  }

  function handleDeleted() {
    setSelected(undefined)
    void reload()
  }

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Template list */}
      <aside className="w-64 flex-shrink-0 border-r border-border bg-surface-muted flex flex-col overflow-hidden">
        <div className="p-4 border-b border-border flex items-center justify-between">
          <p className="text-xs font-semibold text-muted uppercase tracking-wider">{t('templates.title')}</p>
          <button onClick={() => setSelected(null)}
            className="text-xs font-medium text-primary hover:text-primary/80 transition-colors">+ {t('templates.create')}</button>
        </div>

        {error && <p className="text-xs text-red-text px-4 py-2">{error}</p>}

        {loading ? (
          <div className="flex-1 flex items-center justify-center"><Spinner /></div>
        ) : (
          <div className="flex-1 overflow-y-auto divide-y divide-border">
            {templates.length === 0 ? (
              <div className="p-4 text-center">
                <p className="text-sm text-muted-light">{t('templates.noTemplates')}</p>
                <button onClick={() => setSelected(null)} className="mt-2 text-xs text-primary hover:underline">{t('templates.createFirst')}</button>
              </div>
            ) : (
              templates.map(template => {
                const isActive = selected !== null && selected !== undefined && selected.id === template.id
                return (
                  <button key={template.id} onClick={() => setSelected(template)}
                    className={`w-full text-left px-4 py-3 transition-colors ${isActive ? 'bg-primary/5 border-l-2 border-primary' : 'hover:bg-surface-alt border-l-2 border-transparent'}`}>
                    <p className={`text-sm font-medium ${isActive ? 'text-primary' : 'text-dark'}`}>{template.name}</p>
                    {template.description && <p className="text-xs text-muted-light mt-0.5 truncate">{template.description}</p>}
                    <p className="text-xs text-muted-light mt-1">
                      {(template.config?.role ?? '—')} · {t('templates.moduleCount', { count: Object.keys(template.config?.module_config ?? {}).length })}
                    </p>
                  </button>
                )
              })
            )}
          </div>
        )}
      </aside>

      {/* Editor / empty state */}
      {selected === undefined ? (
        <div className="flex-1 flex items-center justify-center bg-white">
          <div className="text-center text-muted-light max-w-xs">
            <FileText className="w-10 h-10 mb-3 mx-auto text-muted-light" aria-hidden="true" />
            <p className="text-base font-medium text-muted">{t('templates.selectTemplate')}</p>
            <p className="text-sm mt-1">{t('templates.selectDescription')}</p>
            <button onClick={() => setSelected(null)}
              className="mt-4 px-4 py-2 text-sm font-medium text-white bg-primary rounded-lg hover:bg-primary/90 transition-colors">
              + {t('templates.createNew')}
            </button>
          </div>
        </div>
      ) : (
        <TemplateEditor
          key={selected?.id ?? 'new'}
          tenantId={tenantId} adminToken={adminToken}
          availablePools={availablePools} modules={modules}
          template={selected}
          onSaved={handleSaved}
          onDeleted={handleDeleted}
        />
      )}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

type PageTab = 'users' | 'templates'

export default function AccessPage() {
  const { t } = useTranslation('access')
  const { session, tenantId } = useAuth()

  const [activeTab,   setActiveTab]   = useState<PageTab>('users')
  // G-PROBE platform-wide: a página autoriza pelo Bearer do operador (session JWT) +
  // ABAC `config.usuarios` — sem caixa de admin-token. O token threadado abaixo é o
  // access token da sessão (nome `adminToken` mantido só p/ minimizar o diff).
  const adminToken = session?.accessToken ?? ''

  const { users, loading, error, reload } = useUsers(tenantId, adminToken)
  const { pools } = usePools(tenantId)
  const { modules } = useModules(adminToken)
  // Single source: templates shared by the Templates tab (list + save/reload) AND the
  // create-user modal (preset selector). Creating one refreshes both.
  const { templates, loading: tplLoading, error: tplError, reload: reloadTemplates } = useTemplates(tenantId, adminToken)

  type LucideIcon = React.FC<{ className?: string }>
  const tabs: { id: PageTab; label: string; Icon: LucideIcon }[] = [
    { id: 'users',     label: t('tabs.users'),     Icon: User     },
    { id: 'templates', label: t('tabs.templates'), Icon: FileText },
  ]

  return (
    <div className="flex flex-col h-full overflow-hidden">

      {/* Page header */}
      <div className="px-6 py-4 border-b border-border bg-white flex items-center justify-between flex-shrink-0">
        <div>
          <h1 className="text-xl font-bold text-dark">{t('title')}</h1>
          <p className="text-sm text-muted mt-0.5">{t('pageSubtitle')}</p>
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex border-b border-border bg-white flex-shrink-0 px-6">
        {tabs.map(tab => (
          <button key={tab.id} onClick={() => setActiveTab(tab.id)}
            className={`flex items-center gap-2 py-3 px-4 text-sm font-medium border-b-2 transition-colors ${
              activeTab === tab.id ? 'border-primary text-primary' : 'border-transparent text-muted hover:text-dark'
            }`}>
            <tab.Icon className="w-3.5 h-3.5" aria-hidden="true" />
            <span>{tab.label}</span>
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden flex">
        {activeTab === 'users' && (
          <UsersPane tenantId={tenantId} adminToken={adminToken}
            availablePools={pools} modules={modules} templates={templates}
            users={users} loading={loading} error={error} reload={reload} />
        )}
        {activeTab === 'templates' && (
          <TemplatesPane tenantId={tenantId} adminToken={adminToken}
            availablePools={pools} modules={modules}
            templates={templates} loading={tplLoading} error={tplError} reload={reloadTemplates} />
        )}
      </div>
    </div>
  )
}
