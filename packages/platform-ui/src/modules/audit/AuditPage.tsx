/**
 * AuditPage — /audit
 *
 * LGPD Audit module for DPO/compliance role.
 * ABAC gate: module_config.audit.sessions
 *
 * Tabs:
 *   sessions      — session message audit trail (active)
 *   mcp_calls     — MCP call audit with masked_input_fields (active)
 *   user_access   — user access logs (stub — Fase 3)
 *   data_requests — SAR/erasure requests (stub — Fase 4)
 *   config_snapshot — masking config snapshot (stub — Fase 5)
 */
import React, { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'
import { FileText, Wrench, User, File, Lock, AlertTriangle, Check, X, Construction } from 'lucide-react'

type Tab = 'sessions' | 'mcp_calls' | 'user_access' | 'data_requests' | 'config_snapshot'

type TabIconComponent = React.FC<{ className?: string }>
const TABS: { key: Tab; Icon: TabIconComponent; active: boolean }[] = [
  { key: 'sessions',        Icon: FileText, active: true  },
  { key: 'mcp_calls',       Icon: Wrench,   active: true  },
  { key: 'user_access',     Icon: User,     active: false },
  { key: 'data_requests',   Icon: File,     active: false },
  { key: 'config_snapshot', Icon: Lock,     active: false },
]

// ── Session messages tab ──────────────────────────────────────────────────────

interface AuditMessage {
  stream_entry_id: string
  event_type:      string
  author_id:       string | null
  author_role:     string | null
  content:         string
  created_at:      string
}

function SessionsTab({ getToken }: { getToken: () => Promise<string | null> }) {
  const { t } = useTranslation('audit')
  const [input,     setInput]     = useState('')
  const [sessionId, setSessionId] = useState('')
  const [messages,  setMessages]  = useState<AuditMessage[]>([])
  const [loading,   setLoading]   = useState(false)
  const [error,     setError]     = useState<string | null>(null)

  async function lookup() {
    if (!input.trim()) return
    setLoading(true)
    setError(null)
    try {
      const token = await getToken()
      const res = await fetch(`/v1/audit/sessions/${input.trim()}/messages`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`)
      }
      const data = await res.json() as { messages: AuditMessage[] }
      setMessages(data.messages ?? [])
      setSessionId(input.trim())
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t('sessions.loadError'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col gap-4 p-5">
      <div className="flex items-start gap-2 bg-warning-light border border-warning/30 rounded-lg px-4 py-2 text-xs text-warning-text">
        <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" aria-hidden="true" />
        {t('sessions.accessNotice')}
      </div>

      <div className="flex gap-2">
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && lookup()}
          placeholder="Session ID"
          className="flex-1 border border-border-strong rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary/40"
        />
        <button
          onClick={lookup}
          disabled={loading || !input.trim()}
          className="px-4 py-2 bg-primary text-white rounded text-sm font-medium hover:bg-primary/90 disabled:opacity-50 flex items-center gap-2">
          {loading && <Spinner size="sm" />}
          {t('sessions.lookup')}
        </button>
      </div>

      {error && (
        <div className="text-sm text-red-text bg-red-light border border-red/30 rounded px-3 py-2">{error}</div>
      )}

      {messages.length > 0 && (
        <div className="border border-border rounded-lg overflow-hidden">
          <div className="bg-surface-muted px-4 py-2 border-b border-border text-xs text-muted font-medium">
            {t('sessions.results', { count: messages.length, sessionId })}
          </div>
          <div className="overflow-auto max-h-[60vh]">
            <table className="w-full text-xs">
              <thead className="bg-surface-muted sticky top-0">
                <tr>
                  {(['timestamp', 'type', 'author', 'role', 'content'] as const).map(col => (
                    <th key={col} className="text-left px-4 py-2 text-muted font-medium border-b border-border">
                      {t(`sessions.columns.${col}`)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {messages.map((m, i) => (
                  <tr key={m.stream_entry_id} className={i % 2 === 0 ? 'bg-white' : 'bg-surface-muted'}>
                    <td className="px-4 py-2 text-muted-light font-mono whitespace-nowrap">
                      {new Date(m.created_at).toLocaleString()}
                    </td>
                    <td className="px-4 py-2 text-muted font-mono">{m.event_type}</td>
                    <td className="px-4 py-2 text-muted font-mono">{m.author_id ?? '—'}</td>
                    <td className="px-4 py-2 text-muted">{m.author_role ?? '—'}</td>
                    <td className="px-4 py-2 text-dark max-w-xs truncate">{m.content}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

// ── MCP calls tab ─────────────────────────────────────────────────────────────

interface McpCall {
  event_id:            string
  server_name:         string
  tool_name:           string
  allowed:             boolean
  injection_detected:  boolean
  masked_input_fields: string[]
  duration_ms:         number
  tenant_id:           string
  session_id:          string | null
  created_at:          string
}

function McpCallsTab({ tenantId, getToken }: { tenantId: string; getToken: () => Promise<string | null> }) {
  const { t } = useTranslation('audit')
  const [calls,      setCalls]      = useState<McpCall[]>([])
  const [loading,    setLoading]    = useState(false)
  const [error,      setError]      = useState<string | null>(null)
  const [maskedOnly, setMaskedOnly] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const token = await getToken()
      const params = new URLSearchParams({ tenant_id: tenantId, limit: '100' })
      if (maskedOnly) params.set('masked_only', 'true')
      const res = await fetch(`/v1/audit/mcp-calls?${params}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json() as { calls: McpCall[] }
      setCalls(data.calls ?? [])
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t('mcpCalls.loadError'))
    } finally {
      setLoading(false)
    }
  }, [tenantId, getToken, maskedOnly, t])

  useEffect(() => { load() }, [load])

  return (
    <div className="flex flex-col gap-4 p-5">
      <div className="flex items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-muted cursor-pointer">
          <input type="checkbox" checked={maskedOnly} onChange={e => setMaskedOnly(e.target.checked)}
            className="rounded border-border-strong" />
          {t('mcpCalls.maskedOnly')}
        </label>
        <button onClick={load} disabled={loading}
          className="text-xs text-muted-light hover:text-muted px-2 py-1 transition-colors flex items-center gap-1">
          {loading ? <Spinner size="sm" /> : t('mcpCalls.refresh')}
        </button>
      </div>

      {error && (
        <div className="text-sm text-red-text bg-red-light border border-red/30 rounded px-3 py-2">{error}</div>
      )}

      <div className="border border-border rounded-lg overflow-hidden">
        <div className="overflow-auto max-h-[65vh]">
          <table className="w-full text-xs">
            <thead className="bg-surface-muted sticky top-0">
              <tr>
                {(['timestamp', 'server', 'tool', 'allowed', 'injection', 'masked', 'ms', 'session'] as const).map(col => (
                  <th key={col} className="text-left px-3 py-2 text-muted font-medium border-b border-border">
                    {t(`mcpCalls.columns.${col}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {calls.length === 0 && !loading ? (
                <tr>
                  <td colSpan={8} className="px-4 py-10 text-center text-muted-light">
                    {t('mcpCalls.noRecords')}
                  </td>
                </tr>
              ) : calls.map((c, i) => (
                <tr key={c.event_id} className={i % 2 === 0 ? 'bg-white' : 'bg-surface-muted'}>
                  <td className="px-3 py-2 text-muted-light font-mono whitespace-nowrap">
                    {new Date(c.created_at).toLocaleString()}
                  </td>
                  <td className="px-3 py-2 font-mono text-muted">{c.server_name}</td>
                  <td className="px-3 py-2 font-mono text-dark">{c.tool_name}</td>
                  <td className="px-3 py-2">
                    {c.allowed
                      ? <Check className="w-3.5 h-3.5 text-green-text" aria-label={t('mcpCalls.allowed')} />
                      : <X     className="w-3.5 h-3.5 text-red"       aria-label={t('mcpCalls.blocked')} />
                    }
                  </td>
                  <td className="px-3 py-2">
                    {c.injection_detected && (
                      <span className="inline-flex items-center gap-1 text-red-text font-semibold">
                        <AlertTriangle className="w-3 h-3" aria-hidden="true" /> {t('mcpCalls.injectionYes')}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-muted">
                    {c.masked_input_fields.length > 0
                      ? c.masked_input_fields.join(', ')
                      : <span className="text-border-strong">—</span>}
                  </td>
                  <td className="px-3 py-2 tabular-nums text-muted-light">{c.duration_ms}</td>
                  <td className="px-3 py-2 font-mono text-muted-light truncate max-w-[120px]">
                    {c.session_id ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ── Stub tab ──────────────────────────────────────────────────────────────────

function StubTab({ labelKey }: { labelKey: string }) {
  const { t } = useTranslation('audit')
  return (
    <div className="flex flex-col items-center justify-center h-64 text-muted-light gap-2">
      <Construction className="w-8 h-8" aria-hidden="true" />
      <div className="text-sm">{t('stub.inDevelopment', { label: t(labelKey) })}</div>
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function AuditPage() {
  const { t } = useTranslation('audit')
  const { tenantId, getAccessToken } = useAuth()
  const [activeTab, setActiveTab] = useState<Tab>('sessions')

  return (
    <div className="flex flex-col h-full bg-surface-muted">

      {/* Tab bar */}
      <div className="bg-white border-b border-border px-6 flex gap-0 flex-shrink-0">
        {TABS.map(tabDef => (
          <button key={tabDef.key} onClick={() => setActiveTab(tabDef.key)}
            className="relative inline-flex items-center gap-1.5 px-4 py-3 text-sm font-medium transition-colors"
            style={{ color: activeTab === tabDef.key ? '#1B4F8A' : '#6b7280' }}>
            <tabDef.Icon className="w-3.5 h-3.5" aria-hidden="true" />
            {t(`tabs.${tabDef.key}`)}
            {!tabDef.active && (
              <span className="ml-1 text-2xs text-muted-light">({t('comingSoon')})</span>
            )}
            {activeTab === tabDef.key && (
              <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-primary rounded-t" />
            )}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto">
        {activeTab === 'sessions'        && <SessionsTab getToken={getAccessToken} />}
        {activeTab === 'mcp_calls'       && <McpCallsTab tenantId={tenantId} getToken={getAccessToken} />}
        {activeTab === 'user_access'     && <StubTab labelKey="stub.userAccess" />}
        {activeTab === 'data_requests'   && <StubTab labelKey="stub.dataRequests" />}
        {activeTab === 'config_snapshot' && <StubTab labelKey="stub.configSnapshot" />}
      </div>

    </div>
  )
}
