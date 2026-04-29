/**
 * AccessPage — /config/access
 *
 * Two tabs:
 *   Usuários  — CRUD de usuários + roles + pool restrictions
 *   Templates — Permission templates: criar, editar, aplicar a usuários com escopo de pool
 */
import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import type { PlatformUser, CreateUserInput, UpdateUserInput, Pool, ModuleConfig } from '@/types'
import ModulePermissionForm, { type ModuleSchema } from '@/components/ModulePermissionForm'

// ── Constants ─────────────────────────────────────────────────────────────────

const ALL_ROLES = ['operator', 'supervisor', 'admin', 'developer', 'business'] as const
type RoleKey = typeof ALL_ROLES[number]

const ROLE_COLORS: Record<RoleKey, { bg: string; text: string }> = {
  operator:   { bg: 'bg-green-100',  text: 'text-green-800'  },
  supervisor: { bg: 'bg-blue-100',   text: 'text-blue-800'   },
  admin:      { bg: 'bg-purple-100', text: 'text-purple-800' },
  developer:  { bg: 'bg-indigo-100', text: 'text-indigo-800' },
  business:   { bg: 'bg-amber-100',  text: 'text-amber-800'  },
}

const ROLE_LABELS: Record<RoleKey, string> = {
  operator:   'Operator',
  supervisor: 'Supervisor',
  admin:      'Admin',
  developer:  'Developer',
  business:   'Business',
}

const MODULES = [
  { id: 'analytics',   label: 'Analytics'     },
  { id: 'evaluation',  label: 'Avaliação'      },
  { id: 'billing',     label: 'Faturamento'    },
  { id: 'config',      label: 'Configuração'   },
  { id: 'registry',    label: 'Recursos'       },
  { id: 'skill_flows', label: 'Skill Flows'    },
  { id: 'campaigns',   label: 'Campanhas'      },
  { id: 'workflows',   label: 'Workflows'      },
] as const

const ACTIONS = [
  { id: 'view',  label: 'Visualizar' },
  { id: 'edit',  label: 'Editar'     },
  { id: 'admin', label: 'Admin'      },
] as const

type ModuleId = typeof MODULES[number]['id']
type ActionId = typeof ACTIONS[number]['id']

// A template permission entry (scope is set at apply time, not in the template)
interface PermEntry { module: ModuleId; action: ActionId }

interface PermTemplate {
  id:          string
  tenant_id:   string
  name:        string
  description: string
  permissions: PermEntry[]
  created_at:  string
  updated_at:  string
}

// ── API helpers ───────────────────────────────────────────────────────────────

function authHeaders(adminToken: string): HeadersInit {
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  if (adminToken) h['X-Admin-Token'] = adminToken
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
    if (!tenantId) return
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
    if (!tenantId) return
    setLoading(true); setError(null)
    try {
      const data = await apiFetch<{ templates: PermTemplate[] }>(
        `/auth/templates?tenant_id=${encodeURIComponent(tenantId)}`,
        adminToken,
      )
      setTemplates(Array.isArray(data.templates) ? data.templates : [])
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
  const colors = ROLE_COLORS[key] ?? { bg: 'bg-gray-100', text: 'text-gray-700' }
  const label  = ROLE_LABELS[key] ?? role
  return (
    <span className={`inline-block text-xs font-medium px-2 py-0.5 rounded-full ${colors.bg} ${colors.text}`}>
      {label}
    </span>
  )
}

function StatusBadge({ active }: { active: boolean }) {
  return active
    ? <span className="inline-block text-xs font-semibold px-2 py-0.5 rounded-full bg-green-100 text-green-700">Ativo</span>
    : <span className="inline-block text-xs font-semibold px-2 py-0.5 rounded-full bg-red-100 text-red-600">Inativo</span>
}

function DeleteButton({ onConfirm }: { onConfirm: () => void }) {
  const [stage, setStage] = useState<0 | 1 | 2>(0)
  if (stage === 0) return (
    <button onClick={() => setStage(1)} className="text-xs text-red-500 hover:text-red-700 transition-colors">Remover</button>
  )
  if (stage === 1) return (
    <span className="flex items-center gap-1">
      <button onClick={() => setStage(2)} className="text-xs font-semibold text-red-600 hover:text-red-800">Confirmar</button>
      <span className="text-gray-300">|</span>
      <button onClick={() => setStage(0)} className="text-xs text-gray-400 hover:text-gray-600">Cancelar</button>
    </span>
  )
  return <button onClick={onConfirm} className="text-xs font-bold text-red-700 animate-pulse">⚠ Apagar</button>
}

// ── UserModal ─────────────────────────────────────────────────────────────────

interface UserModalProps {
  tenantId:       string
  adminToken:     string
  user:           PlatformUser | null
  availablePools: Pool[]
  modules:        ModuleSchema[]
  onClose:        () => void
  onSaved:        () => void
}

function UserModal({ tenantId, adminToken, user, availablePools, modules, onClose, onSaved }: UserModalProps) {
  const isEdit = user !== null
  const [name,         setName]         = useState(user?.name ?? '')
  const [email,        setEmail]        = useState(user?.email ?? '')
  const [password,     setPassword]     = useState('')
  const [roles,        setRoles]        = useState<string[]>(user?.roles ?? ['operator'])
  const [selectedPools,setSelectedPools]= useState<Set<string>>(
    new Set(user?.accessible_pools ?? [])
  )
  const [moduleConfig, setModuleConfig] = useState<ModuleConfig>(user?.module_config ?? {})
  const [active,  setActive]  = useState(user?.active ?? true)
  const [saving,  setSaving]  = useState(false)
  const [err,     setErr]     = useState<string | null>(null)
  const backdropRef = useRef<HTMLDivElement>(null)

  function togglePool(poolId: string) {
    setSelectedPools(prev => {
      const next = new Set(prev)
      next.has(poolId) ? next.delete(poolId) : next.add(poolId)
      return next
    })
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (roles.length === 0) { setErr('Selecione ao menos um perfil.'); return }
    setSaving(true); setErr(null)
    try {
      const accessiblePools = Array.from(selectedPools)
      if (isEdit) {
        const body: UpdateUserInput = { name: name || undefined, roles, accessible_pools: accessiblePools, active }
        if (password) body.password = password
        await apiFetch(`/auth/users/${user!.id}`, adminToken, { method: 'PATCH', body: JSON.stringify(body) })
        // Save ABAC module config separately (PUT replaces the whole config)
        await apiFetch(`/auth/users/${user!.id}/module-config`, adminToken, {
          method: 'PUT', body: JSON.stringify(moduleConfig),
        })
      } else {
        const body: CreateUserInput = { tenant_id: tenantId, email, name, password, roles, accessible_pools: accessiblePools }
        const created = await apiFetch<{ id: string }>('/auth/users', adminToken, { method: 'POST', body: JSON.stringify(body) })
        // Set ABAC module config on the newly created user if anything was configured
        if (Object.keys(moduleConfig).length > 0) {
          await apiFetch(`/auth/users/${created.id}/module-config`, adminToken, {
            method: 'PUT', body: JSON.stringify(moduleConfig),
          })
        }
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
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 flex-shrink-0">
          <h2 className="text-lg font-semibold text-gray-900">{isEdit ? 'Editar usuário' : 'Novo usuário'}</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
        </div>
        <form onSubmit={handleSubmit} className="px-6 py-5 space-y-4 overflow-y-auto flex-1">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Nome</label>
            <input type="text" value={name} onChange={e => setName(e.target.value)} required
              placeholder="Nome completo"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40" />
          </div>
          {!isEdit && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">E-mail</label>
              <input type="email" value={email} onChange={e => setEmail(e.target.value)} required
                placeholder="usuario@empresa.com"
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40" />
            </div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {isEdit ? 'Nova senha (opcional)' : 'Senha'}
            </label>
            <input type="password" value={password} onChange={e => setPassword(e.target.value)} required={!isEdit}
              placeholder={isEdit ? 'Deixe em branco para manter' : 'Mínimo 8 caracteres'}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Perfil de sistema</label>
            <select value={roles[0] ?? 'operator'} onChange={e => setRoles([e.target.value])}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40 bg-white">
              {ALL_ROLES.map(role => (
                <option key={role} value={role}>{ROLE_LABELS[role]}</option>
              ))}
            </select>
          </div>

          {/* Pool multi-select */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="text-sm font-medium text-gray-700">
                Pools acessíveis
                <span className="ml-1.5 text-xs font-normal text-gray-400">
                  {selectedPools.size === 0 ? '(nenhum selecionado = todos)' : `${selectedPools.size} selecionado${selectedPools.size !== 1 ? 's' : ''}`}
                </span>
              </label>
              {availablePools.length > 0 && (
                <button type="button" onClick={() => setSelectedPools(allSelected ? new Set() : new Set(availablePools.map(p => p.pool_id)))}
                  className="text-xs text-gray-400 hover:text-gray-600 transition-colors">
                  {allSelected ? 'Desmarcar todos' : 'Selecionar todos'}
                </button>
              )}
            </div>
            {availablePools.length === 0 ? (
              <div className="border border-gray-200 rounded-lg px-3 py-2 text-xs text-gray-400 italic bg-gray-50">
                Nenhum pool cadastrado — o usuário terá acesso a todos.
              </div>
            ) : (
              <div className="border border-gray-200 rounded-lg divide-y divide-gray-100 max-h-40 overflow-y-auto">
                {availablePools.map(pool => {
                  const checked = selectedPools.has(pool.pool_id)
                  return (
                    <label key={pool.pool_id}
                      className={`flex items-start gap-3 px-3 py-2 cursor-pointer transition-colors hover:bg-gray-50 ${checked ? 'bg-primary/5' : ''}`}>
                      <input type="checkbox" checked={checked} onChange={() => togglePool(pool.pool_id)}
                        className="w-4 h-4 rounded accent-primary flex-shrink-0 mt-0.5" />
                      <div className="flex-1 min-w-0 overflow-hidden">
                        <p className="text-sm font-mono text-gray-800 truncate">{pool.pool_id}</p>
                        {pool.description && (
                          <p className="text-xs text-gray-400 truncate">{pool.description}</p>
                        )}
                        {pool.channel_types.length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-1">
                            {pool.channel_types.map(ch => (
                              <span key={ch} className="text-xs bg-gray-100 text-gray-500 px-1 rounded">{ch}</span>
                            ))}
                          </div>
                        )}
                      </div>
                    </label>
                  )
                })}
              </div>
            )}
            <p className="text-xs text-gray-400 mt-1">Controla o domínio de dados visível em analytics e relatórios.</p>
          </div>

          {/* ABAC module permissions */}
          {modules.length > 0 && (
            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="text-sm font-medium text-gray-700">Permissões por módulo</label>
                <span className="text-xs text-gray-400">ABAC</span>
              </div>
              <div className="border border-gray-200 rounded-lg overflow-hidden">
                <ModulePermissionForm
                  modules={modules}
                  value={moduleConfig}
                  onChange={setModuleConfig}
                  readOnly={false}
                />
              </div>
              <p className="text-xs text-gray-400 mt-1">
                Define o nível de acesso do usuário dentro de cada módulo da plataforma.
              </p>
            </div>
          )}

          {isEdit && (
            <div className="flex items-center gap-3">
              <label className="text-sm font-medium text-gray-700">Status</label>
              <button type="button" onClick={() => setActive(v => !v)}
                className={`relative inline-flex h-6 w-11 rounded-full transition-colors ${active ? 'bg-green-500' : 'bg-gray-300'}`}>
                <span className={`absolute top-0.5 left-0.5 h-5 w-5 bg-white rounded-full shadow transition-transform ${active ? 'translate-x-5' : ''}`} />
              </button>
              <span className="text-sm text-gray-500">{active ? 'Ativo' : 'Inativo'}</span>
            </div>
          )}
          {err && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{err}</p>}
          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose}
              className="px-4 py-2 text-sm text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors">
              Cancelar
            </button>
            <button type="submit" disabled={saving}
              className="px-5 py-2 text-sm font-medium text-white bg-primary rounded-lg hover:bg-primary/90 disabled:opacity-50 transition-colors">
              {saving ? 'Salvando…' : isEdit ? 'Salvar alterações' : 'Criar usuário'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── ApplyTemplateModal ────────────────────────────────────────────────────────

interface ApplyModalProps {
  template:       PermTemplate
  users:          PlatformUser[]
  availablePools: Pool[]
  adminToken:     string
  tenantId:       string
  grantedBy:      string
  onClose:        () => void
  onApplied:      () => void
}

function ApplyTemplateModal({ template, users, availablePools, adminToken, tenantId, grantedBy, onClose, onApplied }: ApplyModalProps) {
  const [userId,    setUserId]    = useState('')
  const [scopeType, setScopeType] = useState<'global' | 'pool'>('global')
  const [poolId,    setPoolId]    = useState('')
  const [applying,  setApplying]  = useState(false)
  const [err,       setErr]       = useState<string | null>(null)
  const backdropRef = useRef<HTMLDivElement>(null)

  async function handleApply(e: React.FormEvent) {
    e.preventDefault()
    if (!userId) { setErr('Selecione um usuário.'); return }
    if (scopeType === 'pool' && !poolId.trim()) { setErr('Informe o ID do pool.'); return }
    setApplying(true); setErr(null)
    try {
      const body: Record<string, unknown> = { tenant_id: tenantId, user_id: userId, granted_by: grantedBy }
      if (scopeType === 'pool') body.scope_override = { scope_type: 'pool', scope_id: poolId.trim() }
      else body.scope_override = { scope_type: 'global', scope_id: null }
      await apiFetch(`/auth/templates/${template.id}/apply`, adminToken, { method: 'POST', body: JSON.stringify(body) })
      onApplied(); onClose()
    } catch (ex) { setErr(String(ex)) }
    finally { setApplying(false) }
  }

  const selectedUser = users.find(u => u.id === userId)

  return (
    <div ref={backdropRef} className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4"
      onClick={e => { if (e.target === backdropRef.current) onClose() }}>
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-md">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">Aplicar template</h2>
            <p className="text-sm text-gray-500 mt-0.5">{template.name}</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
        </div>

        <form onSubmit={handleApply} className="px-6 py-5 space-y-5">
          {/* User select */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Usuário</label>
            <select value={userId} onChange={e => setUserId(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40 bg-white">
              <option value="">— Selecione —</option>
              {users.filter(u => u.active).map(u => (
                <option key={u.id} value={u.id}>{u.name} ({u.email})</option>
              ))}
            </select>
            {selectedUser && (
              <div className="mt-2 flex flex-wrap gap-1">
                {selectedUser.roles.map(r => <RoleBadge key={r} role={r} />)}
                {selectedUser.accessible_pools.length > 0
                  ? selectedUser.accessible_pools.map(p => (
                    <span key={p} className="text-xs bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded font-mono">{p}</span>
                  ))
                  : <span className="text-xs text-gray-400 italic">Todos os pools</span>
                }
              </div>
            )}
          </div>

          {/* Scope */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Escopo de operação</label>
            <p className="text-xs text-gray-400 mb-3">Define em quais pools este usuário pode executar as ações do template.</p>
            <div className="space-y-2">
              <label className="flex items-start gap-3 cursor-pointer group">
                <input type="radio" name="scope" value="global" checked={scopeType === 'global'}
                  onChange={() => setScopeType('global')}
                  className="mt-0.5 accent-primary" />
                <div>
                  <span className="text-sm font-medium text-gray-800">Global — todos os pools</span>
                  <p className="text-xs text-gray-400 mt-0.5">Permissões aplicadas sem restrição de pool.</p>
                </div>
              </label>
              <label className="flex items-start gap-3 cursor-pointer group">
                <input type="radio" name="scope" value="pool" checked={scopeType === 'pool'}
                  onChange={() => setScopeType('pool')}
                  className="mt-0.5 accent-primary" />
                <div className="flex-1">
                  <span className="text-sm font-medium text-gray-800">Pool específico</span>
                  <p className="text-xs text-gray-400 mt-0.5">Permissões restritas a um pool.</p>
                  {scopeType === 'pool' && (
                    availablePools.length > 0 ? (
                      <select value={poolId} onChange={e => setPoolId(e.target.value)}
                        className="mt-2 w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40 bg-white font-mono">
                        <option value="">— Selecione um pool —</option>
                        {availablePools.map(p => (
                          <option key={p.pool_id} value={p.pool_id}>
                            {p.pool_id}{p.description ? ` — ${p.description}` : ''}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input type="text" value={poolId} onChange={e => setPoolId(e.target.value)}
                        placeholder="ID do pool (ex: retencao_humano)"
                        className="mt-2 w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40 font-mono" />
                    )
                  )}
                </div>
              </label>
            </div>
          </div>

          {/* Preview */}
          <div className="bg-gray-50 rounded-lg p-3 border border-gray-200">
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">Permissões que serão concedidas</p>
            <div className="flex flex-wrap gap-1">
              {template.permissions.map((p, i) => (
                <span key={i} className="text-xs bg-white border border-gray-200 text-gray-700 px-2 py-0.5 rounded font-mono">
                  {p.module}:{p.action}
                  {scopeType === 'pool' && poolId ? ` @${poolId}` : ' @global'}
                </span>
              ))}
              {template.permissions.length === 0 && <span className="text-xs text-gray-400 italic">Nenhuma permissão definida</span>}
            </div>
          </div>

          {err && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{err}</p>}

          <div className="flex justify-end gap-3 pt-1">
            <button type="button" onClick={onClose}
              className="px-4 py-2 text-sm text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors">
              Cancelar
            </button>
            <button type="submit" disabled={applying}
              className="px-5 py-2 text-sm font-medium text-white bg-primary rounded-lg hover:bg-primary/90 disabled:opacity-50 transition-colors">
              {applying ? 'Aplicando…' : 'Aplicar template'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── TemplateEditor ────────────────────────────────────────────────────────────

interface TemplateEditorProps {
  tenantId:       string
  adminToken:     string
  availablePools: Pool[]
  users:          PlatformUser[]
  grantedBy:      string
  template:       PermTemplate | null   // null = create mode
  onSaved:        () => void
  onDeleted?:     () => void
}

function TemplateEditor({ tenantId, adminToken, availablePools, users, grantedBy, template, onSaved, onDeleted }: TemplateEditorProps) {
  const isEdit = template !== null

  // Parse initial permissions into a Set of "module:action" strings for easy toggle
  const initPerms = new Set((template?.permissions ?? []).map(p => `${p.module}:${p.action}`))

  const [name,        setName]        = useState(template?.name ?? '')
  const [description, setDescription] = useState(template?.description ?? '')
  const [perms,       setPerms]       = useState<Set<string>>(initPerms)
  const [saving,      setSaving]      = useState(false)
  const [delStage,    setDelStage]    = useState(0)
  const [err,         setErr]         = useState<string | null>(null)
  const [applyModal,  setApplyModal]  = useState(false)
  const [successMsg,  setSuccessMsg]  = useState<string | null>(null)

  // Reset when template changes
  useEffect(() => {
    setName(template?.name ?? '')
    setDescription(template?.description ?? '')
    setPerms(new Set((template?.permissions ?? []).map(p => `${p.module}:${p.action}`)))
    setErr(null); setDelStage(0); setSuccessMsg(null)
  }, [template?.id])

  function togglePerm(mod: string, action: string) {
    const key = `${mod}:${action}`
    setPerms(prev => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })
  }

  function toggleModule(mod: string) {
    const allActions = ACTIONS.map(a => `${mod}:${a.id}`)
    const allSelected = allActions.every(k => perms.has(k))
    setPerms(prev => {
      const next = new Set(prev)
      allActions.forEach(k => allSelected ? next.delete(k) : next.add(k))
      return next
    })
  }

  function buildPermissions(): PermEntry[] {
    return Array.from(perms).map(k => {
      const [module, action] = k.split(':')
      return { module: module as ModuleId, action: action as ActionId }
    })
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) { setErr('Informe um nome para o template.'); return }
    setSaving(true); setErr(null); setSuccessMsg(null)
    try {
      const body = { tenant_id: tenantId, name: name.trim(), description: description.trim(), permissions: buildPermissions() }
      if (isEdit) {
        await apiFetch(`/auth/templates/${template!.id}`, adminToken, { method: 'PATCH', body: JSON.stringify(body) })
      } else {
        await apiFetch('/auth/templates', adminToken, { method: 'POST', body: JSON.stringify(body) })
      }
      setSuccessMsg(isEdit ? 'Template salvo.' : 'Template criado.')
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

  const permCount = perms.size

  return (
    <div className="flex-1 overflow-y-auto">
      <form onSubmit={handleSave}>
        {/* Header */}
        <div className="px-6 py-4 border-b border-gray-200 bg-white sticky top-0 z-10 flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-gray-900">
              {isEdit ? template.name : 'Novo template'}
            </h2>
            <p className="text-xs text-gray-400 mt-0.5">{permCount} permissão{permCount !== 1 ? 'ões' : ''} selecionada{permCount !== 1 ? 's' : ''}</p>
          </div>
          <div className="flex items-center gap-2">
            {isEdit && (
              <>
                <button type="button" onClick={() => setApplyModal(true)}
                  className="px-3 py-1.5 text-xs font-medium text-primary border border-primary rounded-lg hover:bg-primary/5 transition-colors">
                  ↗ Aplicar a usuário
                </button>
                {delStage === 0 && (
                  <button type="button" onClick={() => setDelStage(1)}
                    className="px-3 py-1.5 text-xs text-red-500 border border-red-200 rounded-lg hover:bg-red-50 transition-colors">
                    Excluir
                  </button>
                )}
                {delStage === 1 && (
                  <span className="flex items-center gap-1">
                    <button type="button" onClick={handleDelete}
                      className="text-xs font-semibold text-red-600 hover:text-red-800">Confirmar exclusão</button>
                    <span className="text-gray-300 text-xs">|</span>
                    <button type="button" onClick={() => setDelStage(0)} className="text-xs text-gray-400 hover:text-gray-600">Cancelar</button>
                  </span>
                )}
              </>
            )}
            <button type="submit" disabled={saving}
              className="px-4 py-1.5 text-xs font-medium text-white bg-primary rounded-lg hover:bg-primary/90 disabled:opacity-50 transition-colors">
              {saving ? 'Salvando…' : isEdit ? 'Salvar' : 'Criar template'}
            </button>
          </div>
        </div>

        <div className="px-6 py-5 space-y-6">
          {/* Name + description */}
          <div className="grid grid-cols-2 gap-4">
            <div className="col-span-2 sm:col-span-1">
              <label className="block text-sm font-medium text-gray-700 mb-1">Nome do template</label>
              <input type="text" value={name} onChange={e => setName(e.target.value)} required
                placeholder="Ex: Supervisor Regional"
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40" />
            </div>
            <div className="col-span-2 sm:col-span-1">
              <label className="block text-sm font-medium text-gray-700 mb-1">Descrição</label>
              <input type="text" value={description} onChange={e => setDescription(e.target.value)}
                placeholder="Acesso de supervisão ao SAC…"
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40" />
            </div>
          </div>

          {/* Permission matrix */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <label className="text-sm font-medium text-gray-700">Permissões</label>
              <button type="button" onClick={() => {
                if (perms.size > 0) setPerms(new Set())
                else setPerms(new Set(MODULES.flatMap(m => ACTIONS.map(a => `${m.id}:${a.id}`))))
              }} className="text-xs text-gray-400 hover:text-gray-600 transition-colors">
                {perms.size > 0 ? 'Limpar tudo' : 'Selecionar tudo'}
              </button>
            </div>
            <div className="border border-gray-200 rounded-xl overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr>
                    <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-4 py-2.5 w-44">Módulo</th>
                    {ACTIONS.map(a => (
                      <th key={a.id} className="text-center text-xs font-semibold text-gray-500 uppercase tracking-wider px-3 py-2.5">
                        {a.label}
                      </th>
                    ))}
                    <th className="text-center text-xs font-semibold text-gray-500 uppercase tracking-wider px-3 py-2.5">Todos</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 bg-white">
                  {MODULES.map(mod => {
                    const allSelected = ACTIONS.every(a => perms.has(`${mod.id}:${a.id}`))
                    const someSelected = ACTIONS.some(a => perms.has(`${mod.id}:${a.id}`))
                    return (
                      <tr key={mod.id} className="hover:bg-gray-50 transition-colors">
                        <td className="px-4 py-2.5">
                          <span className="text-sm font-medium text-gray-800">{mod.label}</span>
                          <span className="ml-2 text-xs text-gray-400 font-mono">{mod.id}</span>
                        </td>
                        {ACTIONS.map(a => {
                          const key = `${mod.id}:${a.id}`
                          const checked = perms.has(key)
                          return (
                            <td key={a.id} className="text-center px-3 py-2.5">
                              <input type="checkbox" checked={checked}
                                onChange={() => togglePerm(mod.id, a.id)}
                                className="w-4 h-4 rounded accent-primary cursor-pointer" />
                            </td>
                          )
                        })}
                        <td className="text-center px-3 py-2.5">
                          <input type="checkbox" checked={allSelected} ref={el => { if (el) el.indeterminate = someSelected && !allSelected }}
                            onChange={() => toggleModule(mod.id)}
                            className="w-4 h-4 rounded accent-primary cursor-pointer" />
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <p className="text-xs text-gray-400 mt-2">
              As permissões definem o que o usuário pode fazer. O pool de dados (domínio) é definido ao aplicar o template.
            </p>
          </div>

          {successMsg && (
            <p className="text-sm text-green-700 bg-green-50 border border-green-200 rounded-lg px-4 py-2">✓ {successMsg}</p>
          )}
          {err && (
            <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-4 py-2">{err}</p>
          )}
        </div>
      </form>

      {/* Apply modal */}
      {applyModal && template && (
        <ApplyTemplateModal
          template={template} users={users} availablePools={availablePools}
          adminToken={adminToken} tenantId={tenantId} grantedBy={grantedBy}
          onClose={() => setApplyModal(false)}
          onApplied={() => { setSuccessMsg('Template aplicado com sucesso.') }}
        />
      )}
    </div>
  )
}

// ── UsersPane ─────────────────────────────────────────────────────────────────

interface UsersPaneProps {
  tenantId:       string
  adminToken:     string
  availablePools: Pool[]
  modules:        ModuleSchema[]
  users:          PlatformUser[]
  loading:        boolean
  error:          string | null
  reload:         () => void
}

function UsersPane({ tenantId, adminToken, availablePools, modules, users, loading, error, reload }: UsersPaneProps) {
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
      <aside className="w-52 flex-shrink-0 border-r border-gray-200 bg-gray-50 flex flex-col overflow-y-auto">
        <div className="p-4 border-b border-gray-200 space-y-1">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">Resumo</p>
          <div className="flex justify-between text-sm"><span className="text-gray-600">Total</span><span className="font-semibold">{total}</span></div>
          <div className="flex justify-between text-sm"><span className="text-green-600">Ativos</span><span className="font-semibold text-green-700">{active}</span></div>
          <div className="flex justify-between text-sm"><span className="text-red-500">Inativos</span><span className="font-semibold text-red-600">{total - active}</span></div>
        </div>
        <div className="p-4 border-b border-gray-200">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">Perfil de sistema</p>
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
                      : 'text-gray-600 hover:bg-gray-100'
                  }`}>
                  <span>{isAll ? 'Todos' : ROLE_LABELS[role as RoleKey]}</span>
                  {!isAll && <span className="text-xs font-mono">{byRole[role] ?? 0}</span>}
                </button>
              )
            })}
          </div>
        </div>
        <div className="p-4">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">Status</p>
          <div className="space-y-1">
            {(['all', 'active', 'inactive'] as const).map(s => (
              <button key={s} onClick={() => setStatusFilter(s)}
                className={`w-full text-left text-sm px-2 py-1.5 rounded-lg transition-colors ${statusFilter === s ? 'bg-primary text-white' : 'text-gray-600 hover:bg-gray-100'}`}>
                {s === 'all' ? 'Todos' : s === 'active' ? 'Ativos' : 'Inativos'}
              </button>
            ))}
          </div>
        </div>
      </aside>

      {/* Main */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Subheader */}
        <div className="px-6 py-3 border-b border-gray-100 bg-white flex items-center gap-3 flex-shrink-0">
          <input type="search" value={search} onChange={e => setSearch(e.target.value)}
            placeholder="Buscar por nome ou e-mail…"
            className="flex-1 max-w-md border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40" />
          <button onClick={() => setModalUser(null)}
            className="ml-auto flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-white bg-primary rounded-lg hover:bg-primary/90 transition-colors">
            + Novo usuário
          </button>
        </div>

        {(error || actionErr) && (
          <div className="mx-6 mt-3 flex-shrink-0 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-2">
            {error ?? actionErr}
          </div>
        )}

        {loading ? (
          <div className="flex-1 flex items-center justify-center"><Spinner /></div>
        ) : (
          <div className="flex-1 overflow-auto">
            {filtered.length === 0 ? (
              <div className="flex items-center justify-center h-40">
                <p className="text-gray-400 text-sm">{users.length === 0 ? 'Nenhum usuário encontrado.' : 'Sem resultados para os filtros aplicados.'}</p>
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b border-gray-200 sticky top-0 z-10">
                  <tr>
                    <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-6 py-3">Usuário</th>
                    <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-4 py-3">Perfis</th>
                    <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-4 py-3">Pools (dados)</th>
                    <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-4 py-3">Status</th>
                    <th className="px-4 py-3" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 bg-white">
                  {filtered.map(u => (
                    <tr key={u.id} className="hover:bg-gray-50 transition-colors">
                      <td className="px-6 py-3">
                        <p className="font-medium text-gray-900">{u.name}</p>
                        <p className="text-xs text-gray-400 mt-0.5">{u.email}</p>
                      </td>
                      <td className="px-4 py-3"><div className="flex flex-wrap gap-1">{u.roles.map(r => <RoleBadge key={r} role={r} />)}</div></td>
                      <td className="px-4 py-3">
                        {u.accessible_pools.length === 0
                          ? <span className="text-xs text-gray-400 italic">Todos</span>
                          : <div className="flex flex-wrap gap-1">
                              {u.accessible_pools.slice(0, 3).map(p => (
                                <span key={p} className="text-xs bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded font-mono">{p}</span>
                              ))}
                              {u.accessible_pools.length > 3 && <span className="text-xs text-gray-400">+{u.accessible_pools.length - 3}</span>}
                            </div>
                        }
                      </td>
                      <td className="px-4 py-3"><StatusBadge active={u.active} /></td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-3 justify-end whitespace-nowrap">
                          <button onClick={() => setModalUser(u)} className="text-xs text-primary hover:text-primary/80 font-medium transition-colors">Editar</button>
                          <button onClick={() => handleToggleActive(u)}
                            className={`text-xs font-medium transition-colors ${u.active ? 'text-amber-600 hover:text-amber-800' : 'text-green-600 hover:text-green-800'}`}>
                            {u.active ? 'Desativar' : 'Reativar'}
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
          availablePools={availablePools} modules={modules}
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
  users:          PlatformUser[]
  grantedBy:      string
}

function TemplatesPane({ tenantId, adminToken, availablePools, users, grantedBy }: TemplatesPaneProps) {
  const { templates, loading, error, reload } = useTemplates(tenantId, adminToken)
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
      <aside className="w-64 flex-shrink-0 border-r border-gray-200 bg-gray-50 flex flex-col overflow-hidden">
        <div className="p-4 border-b border-gray-200 flex items-center justify-between">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Templates</p>
          <button onClick={() => setSelected(null)}
            className="text-xs font-medium text-primary hover:text-primary/80 transition-colors">+ Novo</button>
        </div>

        {error && <p className="text-xs text-red-600 px-4 py-2">{error}</p>}

        {loading ? (
          <div className="flex-1 flex items-center justify-center"><Spinner /></div>
        ) : (
          <div className="flex-1 overflow-y-auto divide-y divide-gray-100">
            {templates.length === 0 ? (
              <div className="p-4 text-center">
                <p className="text-sm text-gray-400">Nenhum template criado.</p>
                <button onClick={() => setSelected(null)} className="mt-2 text-xs text-primary hover:underline">Criar o primeiro</button>
              </div>
            ) : (
              templates.map(t => {
                const isActive = selected !== null && selected !== undefined && selected.id === t.id
                return (
                  <button key={t.id} onClick={() => setSelected(t)}
                    className={`w-full text-left px-4 py-3 transition-colors ${isActive ? 'bg-primary/5 border-l-2 border-primary' : 'hover:bg-gray-100 border-l-2 border-transparent'}`}>
                    <p className={`text-sm font-medium ${isActive ? 'text-primary' : 'text-gray-800'}`}>{t.name}</p>
                    {t.description && <p className="text-xs text-gray-400 mt-0.5 truncate">{t.description}</p>}
                    <p className="text-xs text-gray-400 mt-1">
                      {t.permissions.length} permissão{t.permissions.length !== 1 ? 'ões' : ''}
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
          <div className="text-center text-gray-400 max-w-xs">
            <p className="text-4xl mb-3">📋</p>
            <p className="text-base font-medium text-gray-600">Selecione um template</p>
            <p className="text-sm mt-1">Ou crie um novo para definir um conjunto reutilizável de permissões.</p>
            <button onClick={() => setSelected(null)}
              className="mt-4 px-4 py-2 text-sm font-medium text-white bg-primary rounded-lg hover:bg-primary/90 transition-colors">
              + Novo template
            </button>
          </div>
        </div>
      ) : (
        <TemplateEditor
          key={selected?.id ?? 'new'}
          tenantId={tenantId} adminToken={adminToken}
          users={users} availablePools={availablePools} grantedBy={grantedBy}
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
  const { session } = useAuth()
  const tenantId  = session?.tenantId ?? ''
  const grantedBy = session?.email ?? session?.userId ?? 'admin'

  const [activeTab,   setActiveTab]   = useState<PageTab>('users')
  const [adminToken,  setAdminToken]  = useState('')
  const [tokenSaved,  setTokenSaved]  = useState(false)

  const { users, loading, error, reload } = useUsers(tenantId, adminToken)
  const { pools } = usePools(tenantId)
  const { modules } = useModules(adminToken)

  function saveToken() {
    setTokenSaved(true); void reload()
    setTimeout(() => setTokenSaved(false), 2000)
  }

  const tabs: { id: PageTab; label: string; icon: string }[] = [
    { id: 'users',     label: 'Usuários',  icon: '👤' },
    { id: 'templates', label: 'Templates', icon: '📋' },
  ]

  return (
    <div className="flex flex-col h-full overflow-hidden">

      {/* Page header */}
      <div className="px-6 py-4 border-b border-gray-200 bg-white flex items-center justify-between flex-shrink-0">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Controle de Acesso</h1>
          <p className="text-sm text-gray-500 mt-0.5">Gerencie usuários e templates de permissão</p>
        </div>
        {/* Admin token — optional */}
        <div className="flex items-center gap-2">
          <input type="password" value={adminToken}
            onChange={e => { setAdminToken(e.target.value); setTokenSaved(false) }}
            onKeyDown={e => { if (e.key === 'Enter') saveToken() }}
            placeholder="Admin token (opcional em dev)"
            className="border border-gray-300 rounded-lg px-3 py-1.5 text-xs w-52 focus:outline-none focus:ring-2 focus:ring-primary/40" />
          {adminToken && (
            <button onClick={saveToken}
              className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${tokenSaved ? 'bg-green-100 text-green-700' : 'bg-primary text-white hover:bg-primary/90'}`}>
              {tokenSaved ? '✓' : 'Aplicar'}
            </button>
          )}
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex border-b border-gray-200 bg-white flex-shrink-0 px-6">
        {tabs.map(tab => (
          <button key={tab.id} onClick={() => setActiveTab(tab.id)}
            className={`flex items-center gap-2 py-3 px-4 text-sm font-medium border-b-2 transition-colors ${
              activeTab === tab.id ? 'border-primary text-primary' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}>
            <span>{tab.icon}</span>
            <span>{tab.label}</span>
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden flex">
        {activeTab === 'users' && (
          <UsersPane tenantId={tenantId} adminToken={adminToken}
            availablePools={pools} modules={modules}
            users={users} loading={loading} error={error} reload={reload} />
        )}
        {activeTab === 'templates' && (
          <TemplatesPane tenantId={tenantId} adminToken={adminToken}
            availablePools={pools} users={users} grantedBy={grantedBy} />
        )}
      </div>
    </div>
  )
}
