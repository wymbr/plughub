/**
 * AgentPrincipalsPage — /config/agents (AAS-04; adr-a2a-server-binding D6).
 *
 * Principais de máquina EXTERNOS (`partner`): o software de um parceiro que chama pools pelo
 * canal A2A. Grant-first: `config.agents` — `read_only` vê, `read_write` cria, concede pools,
 * desativa e rotaciona a credencial. O backend (auth-api) decide pelo MESMO campo.
 *
 * A credencial aparece UMA vez, logo depois de criar ou rotacionar: o auth-api só guarda o hash,
 * então não existe "ver de novo". O seletor de pools só oferece pool que o backend aceitaria
 * (contato, canal a2a, contrato que admite `partner`) — e, para quem não é master, só os que a
 * pessoa alcança; a recusa do servidor continua sendo a palavra final.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import { apiFetch } from '@/api/apiFetch'
import * as registryApi from '@/api/registry'
import type { Pool } from '@/types'

interface AgentPrincipal {
  agent_principal_id:    string
  kind:                  'partner' | 'customer_agent'
  display_name:          string
  allowed_pools:         string[]
  credential_prefix:     string | null
  credential_rotated_at: string | null
  active:                boolean
  created_by:            string
  last_authenticated_at: string | null
}

const BASE = '/auth/v1/agent-principals'

async function call<T>(token: string, url: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(url, {
    ...init,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`, ...(init?.headers ?? {}) },
  })
  if (!res.ok) {
    let detail = ''
    try {
      const b = await res.json()
      detail = typeof b?.detail === 'string' ? b.detail : JSON.stringify(b?.detail ?? b)
    } catch { detail = await res.text().catch(() => '') }
    throw new Error(`HTTP ${res.status}${detail ? ': ' + detail : ''}`)
  }
  return res.json() as Promise<T>
}

/** O mesmo critério do auth-api (`_pool_exposes_a2a_to_partner`). */
function exposesToPartner(p: Pool): boolean {
  return (p.purpose ?? 'contact') === 'contact'
    && (p.channel_types ?? []).includes('a2a')
    && !!p.a2a && p.a2a.principal_kinds.includes('partner')
}

const inputCls = 'w-full text-sm border border-border-strong rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-primary/40'

export default function AgentPrincipalsPage() {
  const { t } = useTranslation('agents')
  const { session, tenantId, perms } = useAuth()
  const accessiblePools = session?.accessiblePools
  const token = session?.accessToken ?? ''
  const canWrite = perms.can('config', 'agents', 'read_write')
  const isMaster = perms.can('config', 'permissions', 'read_write')

  const [rows, setRows]       = useState<AgentPrincipal[]>([])
  const [pools, setPools]     = useState<Pool[]>([])
  const [error, setError]     = useState('')
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState<AgentPrincipal | 'new' | null>(null)
  const [name, setName]       = useState('')
  const [chosen, setChosen]   = useState<string[]>([])
  const [secret, setSecret]   = useState<{ name: string; credential: string } | null>(null)

  const load = useCallback(async () => {
    if (!token) return
    setLoading(true); setError('')
    try {
      setRows(await call<AgentPrincipal[]>(token, BASE))
      if (tenantId) setPools((await registryApi.listPools(tenantId)).items)
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setLoading(false) }
  }, [token, tenantId])
  useEffect(() => { void load() }, [load])

  // pools que o servidor aceitaria conceder: expõem A2A a partner e, sem master, estão no escopo
  const grantable = useMemo(() => pools.filter(p =>
    exposesToPartner(p) && (isMaster || (accessiblePools ?? []).includes(p.pool_id))), [pools, isMaster, accessiblePools])

  const open = (r: AgentPrincipal | 'new') => {
    setEditing(r); setError('')
    setName(r === 'new' ? '' : r.display_name)
    setChosen(r === 'new' ? [] : r.allowed_pools)
  }

  const save = async () => {
    if (!editing) return
    setError('')
    try {
      if (editing === 'new') {
        const out = await call<AgentPrincipal & { credential: string }>(token, BASE, {
          method: 'POST', body: JSON.stringify({ display_name: name, allowed_pools: chosen }) })
        setSecret({ name: out.display_name, credential: out.credential })
      } else {
        await call(token, `${BASE}/${editing.agent_principal_id}`, {
          method: 'PUT', body: JSON.stringify({ display_name: name, allowed_pools: chosen }) })
      }
      setEditing(null); await load()
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }

  const setActive = async (r: AgentPrincipal, active: boolean) => {
    try {
      await call(token, `${BASE}/${r.agent_principal_id}`, { method: 'PUT', body: JSON.stringify({ active }) })
      await load()
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }

  const rotate = async (r: AgentPrincipal) => {
    if (!window.confirm(t('rotateConfirm', { name: r.display_name }))) return
    try {
      const out = await call<AgentPrincipal & { credential: string }>(token, `${BASE}/${r.agent_principal_id}/credential`, { method: 'POST' })
      setSecret({ name: out.display_name, credential: out.credential }); await load()
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }

  const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : t('never'))

  return (
    <div className="p-6 max-w-5xl">
      <div className="flex items-start justify-between mb-4">
        <div>
          <h1 className="text-lg font-semibold text-dark">{t('title')}</h1>
          <p className="text-xs text-muted mt-1 max-w-3xl">{t('subtitle')}</p>
          {!canWrite && <p className="text-xs text-muted-light mt-2">{t('readOnly')}</p>}
        </div>
        {canWrite && (
          <button type="button" onClick={() => open('new')}
                  className="text-sm bg-primary text-white rounded px-3 py-1.5 shrink-0">{t('new')}</button>
        )}
      </div>

      {error && <p className="text-sm text-red-text bg-red-light rounded px-3 py-2 mb-3">⚠ {error}</p>}

      {secret && (
        <div className="border border-warning rounded p-3 mb-4 bg-warning-light">
          <p className="text-sm font-semibold text-dark">{t('secretTitle', { name: secret.name })}</p>
          <p className="text-xs text-dark mt-1">{t('secretHint')}</p>
          <code className="block mt-2 text-xs font-mono break-all bg-surface rounded px-2 py-1">{secret.credential}</code>
          <div className="flex gap-3 mt-2">
            <button type="button" className="text-xs text-primary underline"
                    onClick={() => void navigator.clipboard?.writeText(secret.credential)}>{t('copy')}</button>
            <button type="button" className="text-xs text-muted underline" onClick={() => setSecret(null)}>{t('secretDone')}</button>
          </div>
        </div>
      )}

      {editing && (
        <div className="border border-border rounded p-4 mb-4 space-y-3">
          <p className="text-sm font-semibold text-dark">{editing === 'new' ? t('new') : t('edit', { name: editing.display_name })}</p>
          <label className="block">
            <span className="text-xs font-medium text-dark">{t('displayName')}</span>
            <input className={inputCls} value={name} onChange={e => setName(e.target.value)} />
          </label>
          <div>
            <span className="text-xs font-medium text-dark">{t('pools')}</span>
            <span className="block text-2xs text-muted-light mb-1">{t('poolsHint')}</span>
            {grantable.length === 0 && <p className="text-xs text-muted">{t('noGrantablePools')}</p>}
            {[...new Set([...grantable.map(p => p.pool_id), ...chosen])].sort().map(pid => (
              <label key={pid} className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" className="w-4 h-4 rounded accent-primary" checked={chosen.includes(pid)}
                       onChange={() => setChosen(c => c.includes(pid) ? c.filter(x => x !== pid) : [...c, pid])} />
                <code className="text-xs text-dark">{pid}</code>
              </label>
            ))}
          </div>
          <div className="flex gap-2">
            <button type="button" disabled={!name.trim() || chosen.length === 0} onClick={() => void save()}
                    className="text-sm bg-primary text-white rounded px-3 py-1.5 disabled:opacity-50">{t('save')}</button>
            <button type="button" onClick={() => setEditing(null)} className="text-sm text-muted px-3 py-1.5">{t('cancel')}</button>
          </div>
        </div>
      )}

      {loading ? <p className="text-sm text-muted-light">{t('loading')}</p> : rows.length === 0 ? (
        <p className="text-sm text-muted">{t('empty')}</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted border-b border-border">
              <th className="py-2 pr-4">{t('colName')}</th>
              <th className="py-2 pr-4">{t('colPools')}</th>
              <th className="py-2 pr-4">{t('colCredential')}</th>
              <th className="py-2 pr-4">{t('colLastAuth')}</th>
              <th className="py-2 pr-4">{t('colStatus')}</th>
              <th className="py-2" />
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.agent_principal_id} className="border-b border-border align-top">
                <td className="py-2.5 pr-4 text-dark">{r.display_name}<span className="block text-2xs text-muted-light">{t(`kind.${r.kind}`)}</span></td>
                <td className="py-2.5 pr-4"><code className="text-xs">{r.allowed_pools.join(', ')}</code></td>
                <td className="py-2.5 pr-4"><code className="text-xs">{r.credential_prefix ? `${r.credential_prefix}…` : '—'}</code>
                  <span className="block text-2xs text-muted-light">{t('rotatedAt', { when: fmt(r.credential_rotated_at) })}</span></td>
                <td className="py-2.5 pr-4 text-xs text-muted">{fmt(r.last_authenticated_at)}</td>
                <td className="py-2.5 pr-4">
                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${r.active ? 'bg-green-light text-green-text' : 'bg-surface-alt text-muted'}`}>
                    {t(r.active ? 'active' : 'inactive')}
                  </span>
                </td>
                <td className="py-2.5 text-right whitespace-nowrap">
                  {canWrite && (<>
                    <button type="button" className="text-xs text-primary underline mr-3" onClick={() => open(r)}>{t('editShort')}</button>
                    <button type="button" className="text-xs text-primary underline mr-3" onClick={() => void rotate(r)}>{t('rotate')}</button>
                    <button type="button" className="text-xs text-red underline" onClick={() => void setActive(r, !r.active)}>
                      {t(r.active ? 'deactivate' : 'activate')}
                    </button>
                  </>)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
