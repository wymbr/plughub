/**
 * EvaluationPermissionsPage.tsx
 * /evaluation/permissions — Arc 6 v2 — 2D permission management
 *
 * Allows admins to grant/revoke user permissions for reviewing and contesting
 * evaluation results, scoped to pool | campaign | global.
 *
 * Permission resolution: union of all matching scopes (campaign > pool > global
 * in specificity, but all matching scopes contribute their flags).
 */

import React, { useState } from 'react'
import {
  usePermissions,
  createPermission,
  updatePermission,
  deletePermission,
} from '@/api/evaluation-hooks'
import { useCampaigns } from '@/api/evaluation-hooks'
import type { EvaluationPermission } from '@/types'

const TENANT = import.meta.env.VITE_TENANT_ID ?? 'tenant_demo'

// ── Helpers ───────────────────────────────────────────────────────────────────

function scopeLabel(perm: EvaluationPermission): string {
  if (perm.scope_type === 'global') return 'Global'
  if (perm.scope_type === 'pool') return `Pool: ${perm.scope_id}`
  return `Campaign: ${perm.scope_id}`
}

function ScopeBadge({ perm }: { perm: EvaluationPermission }) {
  const styles: Record<string, string> = {
    global:   'bg-purple-100 text-purple-700',
    pool:     'bg-blue-100 text-blue-700',
    campaign: 'bg-teal-100 text-teal-700',
  }
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${styles[perm.scope_type] ?? ''}`}>
      {scopeLabel(perm)}
    </span>
  )
}

function PermBadge({ label, active }: { label: string; active: boolean }) {
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${
      active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-400'
    }`}>
      {label}
    </span>
  )
}

function formatDate(iso: string) {
  try { return new Date(iso).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }) }
  catch { return iso }
}

// ── Sidebar user list ─────────────────────────────────────────────────────────

interface SidebarProps {
  permissions:    EvaluationPermission[]
  selectedUser:   string | null
  onSelectUser:   (userId: string | null) => void
  onAdd:          () => void
}

function Sidebar({ permissions, selectedUser, onSelectUser, onAdd }: SidebarProps) {
  const users = Array.from(new Set(permissions.map(p => p.user_id))).sort()

  return (
    <aside className="w-64 border-r border-gray-200 flex flex-col">
      <div className="p-4 border-b border-gray-200 flex items-center justify-between">
        <h2 className="font-semibold text-sm text-gray-700">Usuários</h2>
        <button
          onClick={onAdd}
          className="text-xs bg-teal-600 text-white px-2 py-1 rounded hover:bg-teal-700"
        >
          + Permissão
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        <button
          onClick={() => onSelectUser(null)}
          className={`w-full text-left px-4 py-2 text-sm transition-colors ${
            selectedUser === null
              ? 'bg-teal-50 text-teal-800 font-semibold border-l-2 border-teal-600'
              : 'text-gray-600 hover:bg-gray-50'
          }`}
        >
          Todos os usuários
          <span className="ml-2 text-xs text-gray-400">({permissions.length})</span>
        </button>

        {users.map(userId => {
          const count = permissions.filter(p => p.user_id === userId).length
          const isSelected = selectedUser === userId
          return (
            <button
              key={userId}
              onClick={() => onSelectUser(userId)}
              className={`w-full text-left px-4 py-2 text-sm transition-colors ${
                isSelected
                  ? 'bg-teal-50 text-teal-800 font-semibold border-l-2 border-teal-600'
                  : 'text-gray-600 hover:bg-gray-50'
              }`}
            >
              <div className="truncate font-mono text-xs">{userId}</div>
              <div className="text-xs text-gray-400">{count} permissão{count !== 1 ? 'ões' : ''}</div>
            </button>
          )
        })}
      </div>
    </aside>
  )
}

// ── Permission row ────────────────────────────────────────────────────────────

interface PermRowProps {
  perm:      EvaluationPermission
  adminToken: string
  onUpdated: () => void
  onDeleted: () => void
}

function PermRow({ perm, adminToken, onUpdated, onDeleted }: PermRowProps) {
  const [editing, setEditing]       = useState(false)
  const [canReview, setCanReview]   = useState(perm.can_review)
  const [canContest, setCanContest] = useState(perm.can_contest)
  const [saving, setSaving]         = useState(false)
  const [deleting, setDeleting]     = useState(false)
  const [confirmDel, setConfirmDel] = useState(false)
  const [error, setError]           = useState<string | null>(null)

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    try {
      await updatePermission(perm.id, { can_review: canReview, can_contest: canContest }, adminToken)
      setEditing(false)
      onUpdated()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    setDeleting(true)
    setError(null)
    try {
      await deletePermission(perm.id, adminToken)
      onDeleted()
    } catch (e) {
      setError(String(e))
      setDeleting(false)
      setConfirmDel(false)
    }
  }

  return (
    <tr className="border-b border-gray-100 hover:bg-gray-50">
      <td className="px-4 py-3 text-xs font-mono text-gray-600">{perm.user_id}</td>
      <td className="px-4 py-3"><ScopeBadge perm={perm} /></td>
      <td className="px-4 py-3 space-x-1">
        {editing ? (
          <>
            <label className="inline-flex items-center gap-1 text-xs cursor-pointer">
              <input type="checkbox" checked={canReview} onChange={e => setCanReview(e.target.checked)} />
              Revisar
            </label>
            <label className="inline-flex items-center gap-1 text-xs cursor-pointer ml-3">
              <input type="checkbox" checked={canContest} onChange={e => setCanContest(e.target.checked)} />
              Contestar
            </label>
          </>
        ) : (
          <>
            <PermBadge label="Revisar"    active={perm.can_review} />
            <PermBadge label="Contestar"  active={perm.can_contest} />
          </>
        )}
      </td>
      <td className="px-4 py-3 text-xs text-gray-500">{perm.granted_by}</td>
      <td className="px-4 py-3 text-xs text-gray-400">{formatDate(perm.created_at)}</td>
      <td className="px-4 py-3">
        {error && <p className="text-red-500 text-xs mb-1">{error}</p>}
        {editing ? (
          <div className="flex gap-2">
            <button
              onClick={handleSave}
              disabled={saving}
              className="text-xs bg-teal-600 text-white px-2 py-1 rounded hover:bg-teal-700 disabled:opacity-50"
            >
              {saving ? 'Salvando…' : 'Salvar'}
            </button>
            <button
              onClick={() => { setEditing(false); setCanReview(perm.can_review); setCanContest(perm.can_contest) }}
              className="text-xs text-gray-500 hover:text-gray-700 px-2 py-1"
            >
              Cancelar
            </button>
          </div>
        ) : confirmDel ? (
          <div className="flex gap-2">
            <button
              onClick={handleDelete}
              disabled={deleting}
              className="text-xs bg-red-600 text-white px-2 py-1 rounded hover:bg-red-700 disabled:opacity-50"
            >
              {deleting ? 'Removendo…' : 'Confirmar'}
            </button>
            <button onClick={() => setConfirmDel(false)} className="text-xs text-gray-500 px-2 py-1">
              Cancelar
            </button>
          </div>
        ) : (
          <div className="flex gap-2">
            <button
              onClick={() => setEditing(true)}
              className="text-xs text-blue-600 hover:text-blue-800"
            >
              Editar
            </button>
            <button
              onClick={() => setConfirmDel(true)}
              className="text-xs text-red-500 hover:text-red-700"
            >
              Revogar
            </button>
          </div>
        )}
      </td>
    </tr>
  )
}

// ── Create permission form ────────────────────────────────────────────────────

interface CreateFormProps {
  tenantId:   string
  adminToken: string
  campaigns:  { campaign_id: string; name: string }[]
  onCreated:  () => void
  onCancel:   () => void
}

function CreateForm({ tenantId, adminToken, campaigns, onCreated, onCancel }: CreateFormProps) {
  const [userId,     setUserId]     = useState('')
  const [scopeType,  setScopeType]  = useState<'pool' | 'campaign' | 'global'>('campaign')
  const [scopeId,    setScopeId]    = useState('')
  const [canReview,  setCanReview]  = useState(false)
  const [canContest, setCanContest] = useState(false)
  const [grantedBy,  setGrantedBy]  = useState('admin')
  const [saving,     setSaving]     = useState(false)
  const [error,      setError]      = useState<string | null>(null)

  const handleSubmit = async () => {
    if (!userId.trim()) { setError('User ID é obrigatório'); return }
    if (scopeType !== 'global' && !scopeId.trim()) { setError('Scope ID é obrigatório para pool/campaign'); return }
    if (!canReview && !canContest) { setError('Conceda ao menos uma permissão'); return }

    setSaving(true)
    setError(null)
    try {
      await createPermission({
        tenant_id:   tenantId,
        user_id:     userId.trim(),
        scope_type:  scopeType,
        scope_id:    scopeType === 'global' ? null : scopeId.trim(),
        can_review:  canReview,
        can_contest: canContest,
        granted_by:  grantedBy.trim() || 'admin',
      }, adminToken)
      onCreated()
    } catch (e) {
      setError(String(e))
      setSaving(false)
    }
  }

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-6 max-w-lg">
      <h3 className="font-semibold text-gray-800 mb-4">Nova Permissão</h3>

      <div className="space-y-4">
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">User ID</label>
          <input
            className="w-full border border-gray-300 rounded px-3 py-2 text-sm font-mono"
            placeholder="user_abc123 ou email"
            value={userId}
            onChange={e => setUserId(e.target.value)}
          />
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Escopo</label>
          <select
            className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
            value={scopeType}
            onChange={e => { setScopeType(e.target.value as 'pool' | 'campaign' | 'global'); setScopeId('') }}
          >
            <option value="campaign">Campanha específica</option>
            <option value="pool">Pool específico</option>
            <option value="global">Global (todos os pools e campanhas)</option>
          </select>
        </div>

        {scopeType === 'campaign' && (
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Campanha</label>
            {campaigns.length > 0 ? (
              <select
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
                value={scopeId}
                onChange={e => setScopeId(e.target.value)}
              >
                <option value="">Selecione uma campanha…</option>
                {campaigns.map(c => (
                  <option key={c.campaign_id} value={c.campaign_id}>
                    {c.name} ({c.campaign_id})
                  </option>
                ))}
              </select>
            ) : (
              <input
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm font-mono"
                placeholder="campaign_id"
                value={scopeId}
                onChange={e => setScopeId(e.target.value)}
              />
            )}
          </div>
        )}

        {scopeType === 'pool' && (
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Pool ID</label>
            <input
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm font-mono"
              placeholder="retencao_humano"
              value={scopeId}
              onChange={e => setScopeId(e.target.value)}
            />
          </div>
        )}

        <div>
          <label className="block text-xs font-medium text-gray-600 mb-2">Permissões</label>
          <div className="flex gap-6">
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={canReview}
                onChange={e => setCanReview(e.target.checked)}
                className="w-4 h-4 accent-teal-600"
              />
              <span>Revisar avaliações</span>
            </label>
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="checkbox"
                checked={canContest}
                onChange={e => setCanContest(e.target.checked)}
                className="w-4 h-4 accent-teal-600"
              />
              <span>Contestar avaliações</span>
            </label>
          </div>
          <p className="text-xs text-gray-400 mt-1">
            Resolução por union: todas as permissões matching contribuem (campanha + pool + global).
          </p>
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Concedido por</label>
          <input
            className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
            placeholder="admin"
            value={grantedBy}
            onChange={e => setGrantedBy(e.target.value)}
          />
        </div>

        {error && <p className="text-red-500 text-sm">{error}</p>}

        <div className="flex gap-3 pt-2">
          <button
            onClick={handleSubmit}
            disabled={saving}
            className="bg-teal-600 text-white px-4 py-2 rounded text-sm hover:bg-teal-700 disabled:opacity-50"
          >
            {saving ? 'Salvando…' : 'Conceder permissão'}
          </button>
          <button
            onClick={onCancel}
            className="text-gray-500 px-4 py-2 rounded text-sm hover:text-gray-700"
          >
            Cancelar
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Resolution explainer panel ────────────────────────────────────────────────

function ResolutionExplainer() {
  return (
    <div className="bg-blue-50 border border-blue-100 rounded-lg p-4 text-sm text-blue-800">
      <p className="font-semibold mb-2">Como a resolução de permissões funciona</p>
      <ul className="space-y-1 text-xs">
        <li>• <strong>Union semantics:</strong> todas as linhas matching (global + pool + campanha) contribuem suas flags.</li>
        <li>• Um usuário com <code className="bg-blue-100 px-1 rounded">can_review</code> no pool A e <code className="bg-blue-100 px-1 rounded">can_contest</code> na campanha C tem ambas as permissões.</li>
        <li>• O servidor computa <code className="bg-blue-100 px-1 rounded">available_actions</code> em cada GET de resultado — a UI nunca deve calcular permissões localmente.</li>
        <li>• Endpoints de revisão e contestação verificam JWT (<code className="bg-blue-100 px-1 rounded">Authorization: Bearer</code>) e repetem a checagem no servidor.</li>
        <li>• O campo <code className="bg-blue-100 px-1 rounded">round</code> no body é o anti-replay: deve ser igual a <code className="bg-blue-100 px-1 rounded">result.current_round</code> ou o servidor retorna 409.</li>
      </ul>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function EvaluationPermissionsPage() {
  const [adminToken,    setAdminToken]    = useState('')
  const [selectedUser,  setSelectedUser]  = useState<string | null>(null)
  const [showCreate,    setShowCreate]    = useState(false)
  const [filterScope,   setFilterScope]   = useState<string>('all')
  const [searchUser,    setSearchUser]    = useState('')

  const { permissions, loading, error, reload } = usePermissions(TENANT)
  const { campaigns }                            = useCampaigns(TENANT)

  const displayed = permissions.filter(p => {
    const matchUser  = selectedUser ? p.user_id === selectedUser : true
    const matchScope = filterScope !== 'all' ? p.scope_type === filterScope : true
    const matchSearch = searchUser ? p.user_id.toLowerCase().includes(searchUser.toLowerCase()) : true
    return matchUser && matchScope && matchSearch
  })

  if (showCreate) {
    return (
      <div className="p-6">
        <h1 className="text-xl font-semibold text-gray-800 mb-6">Permissões de Avaliação</h1>
        <CreateForm
          tenantId={TENANT}
          adminToken={adminToken}
          campaigns={campaigns}
          onCreated={() => { setShowCreate(false); reload() }}
          onCancel={() => setShowCreate(false)}
        />
      </div>
    )
  }

  return (
    <div className="flex h-full">
      <Sidebar
        permissions={permissions}
        selectedUser={selectedUser}
        onSelectUser={setSelectedUser}
        onAdd={() => setShowCreate(true)}
      />

      <main className="flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <div className="p-6 border-b border-gray-200">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h1 className="text-xl font-semibold text-gray-800">Permissões de Avaliação</h1>
              <p className="text-sm text-gray-500 mt-0.5">
                Modelo 2D: usuário × (pool | campanha | global). Resolução por union de todos os escopos matching.
              </p>
            </div>

            {/* Admin token input */}
            <div className="flex items-center gap-2">
              <label className="text-xs text-gray-500">Admin Token:</label>
              <input
                type="password"
                className="border border-gray-300 rounded px-2 py-1 text-xs w-48"
                placeholder="token de admin"
                value={adminToken}
                onChange={e => setAdminToken(e.target.value)}
              />
            </div>
          </div>

          <div className="flex items-center gap-3">
            {/* Search */}
            <input
              className="border border-gray-300 rounded px-3 py-1.5 text-sm w-56"
              placeholder="Buscar user ID…"
              value={searchUser}
              onChange={e => setSearchUser(e.target.value)}
            />

            {/* Scope filter */}
            <select
              className="border border-gray-300 rounded px-3 py-1.5 text-sm"
              value={filterScope}
              onChange={e => setFilterScope(e.target.value)}
            >
              <option value="all">Todos os escopos</option>
              <option value="campaign">Campanha</option>
              <option value="pool">Pool</option>
              <option value="global">Global</option>
            </select>

            <span className="text-xs text-gray-400 ml-auto">
              {displayed.length} permiss{displayed.length !== 1 ? 'ões' : 'ão'}
            </span>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {/* Resolution explainer */}
          <ResolutionExplainer />

          {loading && (
            <div className="flex justify-center py-12">
              <div className="w-6 h-6 border-2 border-teal-600 border-t-transparent rounded-full animate-spin" />
            </div>
          )}

          {error && (
            <div className="bg-red-50 border border-red-200 rounded p-4 text-red-700 text-sm">
              Erro ao carregar permissões: {error}
            </div>
          )}

          {!loading && !error && displayed.length === 0 && (
            <div className="text-center py-12 text-gray-400">
              <p className="text-4xl mb-3">🔐</p>
              <p className="text-sm">Nenhuma permissão encontrada.</p>
              <button
                onClick={() => setShowCreate(true)}
                className="mt-4 text-teal-600 text-sm hover:underline"
              >
                Conceder primeira permissão
              </button>
            </div>
          )}

          {!loading && !error && displayed.length > 0 && (
            <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                      User ID
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                      Escopo
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                      Flags
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                      Concedido por
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                      Data
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wide">
                      Ações
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {displayed.map(perm => (
                    <PermRow
                      key={perm.id}
                      perm={perm}
                      adminToken={adminToken}
                      onUpdated={reload}
                      onDeleted={reload}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Scope legend */}
          <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
            <p className="text-xs font-semibold text-gray-600 mb-3">Legenda de escopos</p>
            <div className="grid grid-cols-3 gap-4 text-xs text-gray-600">
              <div className="space-y-1">
                <span className="bg-purple-100 text-purple-700 px-2 py-0.5 rounded font-medium">Global</span>
                <p className="text-gray-500 mt-1">
                  Aplica a todos os pools e campanhas do tenant. Usar com parcimônia — concede acesso amplo.
                </p>
              </div>
              <div className="space-y-1">
                <span className="bg-blue-100 text-blue-700 px-2 py-0.5 rounded font-medium">Pool</span>
                <p className="text-gray-500 mt-1">
                  Aplica a todos os resultados de avaliações de sessões roteadas para o pool especificado.
                </p>
              </div>
              <div className="space-y-1">
                <span className="bg-teal-100 text-teal-700 px-2 py-0.5 rounded font-medium">Campaign</span>
                <p className="text-gray-500 mt-1">
                  Aplica apenas aos resultados da campanha específica. Escopo mais granular.
                </p>
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  )
}
