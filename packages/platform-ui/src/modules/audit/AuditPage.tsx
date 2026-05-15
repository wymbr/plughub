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
import { useAuth } from '@/auth/useAuth'
import Spinner from '@/components/ui/Spinner'

type Tab = 'sessions' | 'mcp_calls' | 'user_access' | 'data_requests' | 'config_snapshot'

const TABS: { key: Tab; label: string; active: boolean }[] = [
  { key: 'sessions',        label: '📋 Sessões',        active: true  },
  { key: 'mcp_calls',       label: '🔧 MCP Calls',      active: true  },
  { key: 'user_access',     label: '👤 Acessos',        active: false },
  { key: 'data_requests',   label: '📄 Req. de Dados',  active: false },
  { key: 'config_snapshot', label: '🔒 Config Masking', active: false },
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
  const [input,    setInput]    = useState('')
  const [sessionId, setSessionId] = useState('')
  const [messages, setMessages] = useState<AuditMessage[]>([])
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState<string | null>(null)

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
      setError(e instanceof Error ? e.message : 'Erro ao carregar')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col gap-4 p-5">
      <div className="bg-amber-50 border border-amber-200 rounded-lg px-4 py-2 text-xs text-amber-800">
        ⚠️ Todo acesso a mensagens de sessão é registrado no log de auditoria imutável.
      </div>

      <div className="flex gap-2">
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && lookup()}
          placeholder="Session ID"
          className="flex-1 border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary/40"
        />
        <button
          onClick={lookup}
          disabled={loading || !input.trim()}
          className="px-4 py-2 bg-primary text-white rounded text-sm font-medium hover:bg-primary/90 disabled:opacity-50 flex items-center gap-2">
          {loading && <Spinner size="sm" />}
          Consultar
        </button>
      </div>

      {error && (
        <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">{error}</div>
      )}

      {messages.length > 0 && (
        <div className="border border-gray-200 rounded-lg overflow-hidden">
          <div className="bg-gray-50 px-4 py-2 border-b border-gray-200 text-xs text-gray-500 font-medium">
            {messages.length} mensagens — sessão {sessionId}
          </div>
          <div className="overflow-auto max-h-[60vh]">
            <table className="w-full text-xs">
              <thead className="bg-gray-50 sticky top-0">
                <tr>
                  {['Timestamp', 'Tipo', 'Autor', 'Role', 'Conteúdo'].map(h => (
                    <th key={h} className="text-left px-4 py-2 text-gray-500 font-medium border-b border-gray-200">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {messages.map((m, i) => (
                  <tr key={m.stream_entry_id} className={i % 2 === 0 ? 'bg-white' : 'bg-gray-50'}>
                    <td className="px-4 py-2 text-gray-400 font-mono whitespace-nowrap">
                      {new Date(m.created_at).toLocaleString('pt-BR')}
                    </td>
                    <td className="px-4 py-2 text-gray-600 font-mono">{m.event_type}</td>
                    <td className="px-4 py-2 text-gray-500 font-mono">{m.author_id ?? '—'}</td>
                    <td className="px-4 py-2 text-gray-500">{m.author_role ?? '—'}</td>
                    <td className="px-4 py-2 text-gray-700 max-w-xs truncate">{m.content}</td>
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
      setError(e instanceof Error ? e.message : 'Erro ao carregar')
    } finally {
      setLoading(false)
    }
  }, [tenantId, getToken, maskedOnly])

  useEffect(() => { load() }, [load])

  return (
    <div className="flex flex-col gap-4 p-5">
      <div className="flex items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-gray-600 cursor-pointer">
          <input type="checkbox" checked={maskedOnly} onChange={e => setMaskedOnly(e.target.checked)}
            className="rounded border-gray-300" />
          Somente calls com campos mascarados
        </label>
        <button onClick={load} disabled={loading}
          className="text-xs text-gray-400 hover:text-gray-600 px-2 py-1 transition-colors flex items-center gap-1">
          {loading ? <Spinner size="sm" /> : '↻ Atualizar'}
        </button>
      </div>

      {error && (
        <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded px-3 py-2">{error}</div>
      )}

      <div className="border border-gray-200 rounded-lg overflow-hidden">
        <div className="overflow-auto max-h-[65vh]">
          <table className="w-full text-xs">
            <thead className="bg-gray-50 sticky top-0">
              <tr>
                {['Timestamp', 'Server', 'Tool', 'Permitido', 'Injeção', 'Mascarados', 'ms', 'Sessão'].map(h => (
                  <th key={h} className="text-left px-3 py-2 text-gray-500 font-medium border-b border-gray-200">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {calls.length === 0 && !loading ? (
                <tr>
                  <td colSpan={8} className="px-4 py-10 text-center text-gray-400">Nenhum registro encontrado</td>
                </tr>
              ) : calls.map((c, i) => (
                <tr key={c.event_id} className={i % 2 === 0 ? 'bg-white' : 'bg-gray-50'}>
                  <td className="px-3 py-2 text-gray-400 font-mono whitespace-nowrap">
                    {new Date(c.created_at).toLocaleString('pt-BR')}
                  </td>
                  <td className="px-3 py-2 font-mono text-gray-600">{c.server_name}</td>
                  <td className="px-3 py-2 font-mono text-gray-700">{c.tool_name}</td>
                  <td className="px-3 py-2">
                    <span className={c.allowed ? 'text-green-600' : 'text-red-500'}>
                      {c.allowed ? '✓' : '✗'}
                    </span>
                  </td>
                  <td className="px-3 py-2">
                    {c.injection_detected && <span className="text-red-600 font-semibold">⚠ Sim</span>}
                  </td>
                  <td className="px-3 py-2 text-gray-500">
                    {c.masked_input_fields.length > 0
                      ? c.masked_input_fields.join(', ')
                      : <span className="text-gray-300">—</span>}
                  </td>
                  <td className="px-3 py-2 tabular-nums text-gray-400">{c.duration_ms}</td>
                  <td className="px-3 py-2 font-mono text-gray-400 truncate max-w-[120px]">
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

function StubTab({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center justify-center h-64 text-gray-400 gap-2">
      <div className="text-3xl">🚧</div>
      <div className="text-sm">{label} — em desenvolvimento</div>
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function AuditPage() {
  const { tenantId, getAccessToken } = useAuth()
  const [tab, setTab] = useState<Tab>('sessions')

  return (
    <div className="flex flex-col h-full bg-gray-50">

      {/* Header */}
      <div className="bg-white border-b border-gray-200 px-6 py-4 flex-shrink-0">
        <h1 className="text-lg font-semibold text-gray-800">Auditoria LGPD</h1>
        <p className="text-xs text-gray-500 mt-0.5">Acesso restrito — todos os acessos são registrados em log imutável.</p>
      </div>

      {/* Tabs */}
      <div className="bg-white border-b border-gray-200 px-6 flex gap-0 flex-shrink-0">
        {TABS.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className="relative px-4 py-3 text-sm font-medium transition-colors"
            style={{ color: tab === t.key ? '#1B4F8A' : '#6b7280' }}>
            {t.label}
            {!t.active && <span className="ml-1 text-[10px] text-gray-400">(em breve)</span>}
            {tab === t.key && (
              <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-primary rounded-t" />
            )}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto">
        {tab === 'sessions'        && <SessionsTab getToken={getAccessToken} />}
        {tab === 'mcp_calls'       && <McpCallsTab tenantId={tenantId} getToken={getAccessToken} />}
        {tab === 'user_access'     && <StubTab label="Logs de acesso de usuários" />}
        {tab === 'data_requests'   && <StubTab label="Solicitações SAR / Erasure" />}
        {tab === 'config_snapshot' && <StubTab label="Snapshot de configuração de masking" />}
      </div>

    </div>
  )
}
