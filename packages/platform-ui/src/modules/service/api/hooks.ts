/**
 * hooks.ts — Atendimento module
 * Real-time data hooks wrapping analytics-api and supervisor API.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

async function safeJson<T>(res: Response): Promise<T> {
  const ct = res.headers.get('content-type') ?? ''
  if (!ct.includes('application/json') && !ct.includes('text/json')) {
    throw new Error(`API indisponível (HTTP ${res.status})`)
  }
  return res.json() as Promise<T>
}
import type {
  ActiveSession, ConnectionStatus, ContactSegment, Metrics24h,
  PoolSnapshot, PoolSlaEntry, PoolView, SentimentEntry, StreamEntry, SupervisorState
} from '../types'
import { getAccessToken } from '@/auth/token-store'

const BASE = ''  // relative URLs — Vite proxies to analytics-api on port 3500

// Segurança Fase D — pool-scoping dos endpoints /dashboard/*: o token do domínio vai
// como QUERY PARAM porque o EventSource (SSE) não envia header Authorization. Sem token
// → sufixo vazio → backend degrada irrestrito (dashboards sem login seguem funcionando).
function _tok(): string {
  const t = getAccessToken()
  return t ? `&token=${encodeURIComponent(t)}` : ''
}

// Segurança 2026-08-27 — `POST /supervisor/{join,message,leave}` passou a EXIGIR token
// (antes a rota não tinha `Depends` nenhum: entrava-se numa conferência ao vivo e
// escrevia-se no stream dela sem credencial). Ao contrário do `_tok()` acima, aqui não
// há EventSource no caminho, então o token vai no header, que é onde ele pertence.
// Sem token o backend responde 401 — de propósito: é escrita, não relatório.
function _authHeaders(): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  const t = getAccessToken()
  if (t) h['Authorization'] = `Bearer ${t}`
  return h
}

// ─── usePoolSnapshots ─────────────────────────────────────────────────────────

export function usePoolSnapshots(tenantId: string): {
  snapshots:   PoolSnapshot[]
  status:      ConnectionStatus
  lastUpdated: number | null   // epoch ms of last non-empty snapshot; null = never received
  isStale:     boolean         // true when >120s since last real data (Redis TTL window)
} {
  // Use a Map keyed by pool_id so pools that disappear from a partial SSE response are
  // preserved rather than dropped. A pool is only removed when the user navigates away
  // (unmount) or when all pools have been gone for >125s (isStale).
  const snapshotMapRef = useRef<Map<string, PoolSnapshot>>(new Map())
  const [snapshots,   setSnapshots]   = useState<PoolSnapshot[]>([])
  const [status,      setStatus]      = useState<ConnectionStatus>('connecting')
  const [lastUpdated, setLastUpdated] = useState<number | null>(null)
  const [isStale,     setIsStale]     = useState(false)
  const esRef         = useRef<EventSource | null>(null)
  const staleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Refresh the 125s stale-detection timer whenever we get real data from any pool.
  // The timer fires only when NO pool has pushed a snapshot for 2+ minutes.
  const resetStaleTimer = (ts: number) => {
    if (staleTimerRef.current) clearTimeout(staleTimerRef.current)
    setIsStale(false)
    staleTimerRef.current = setTimeout(() => setIsStale(true), 125_000)
    setLastUpdated(ts)
  }

  useEffect(() => {
    if (!tenantId) return
    // Reset map when tenant changes
    snapshotMapRef.current = new Map()
    setSnapshots([])

    const url = `${BASE}/dashboard/operational?tenant_id=${encodeURIComponent(tenantId)}${_tok()}`
    const es  = new EventSource(url)
    esRef.current = es
    setStatus('connecting')

    es.addEventListener('pools', (e: MessageEvent) => {
      try {
        const incoming = JSON.parse(e.data) as PoolSnapshot[]

        if (incoming.length > 0) {
          // Merge incoming snapshots into the map — never delete absent entries.
          // This prevents a pool from vanishing from the UI when it temporarily has no
          // routing activity while other pools are still active.
          for (const s of incoming) snapshotMapRef.current.set(s.pool_id, s)
          setSnapshots(Array.from(snapshotMapRef.current.values()))
          resetStaleTimer(Date.now())
        }
        // When incoming is [] (all Redis TTLs expired), keep existing map and
        // let the stale timer eventually mark the data as stale.
        setStatus('connected')
      } catch { /* ignore parse errors */ }
    })

    es.addEventListener('error', () => setStatus('error'))
    es.onopen = () => setStatus('connected')

    return () => {
      es.close()
      esRef.current = null
      setStatus('closed')
      if (staleTimerRef.current) clearTimeout(staleTimerRef.current)
    }
  }, [tenantId])

  return { snapshots, status, lastUpdated, isStale }
}

// ─── useSentimentLive ─────────────────────────────────────────────────────────

export function useSentimentLive(tenantId: string, intervalMs = 10_000): SentimentEntry[] {
  const [entries, setEntries] = useState<SentimentEntry[]>([])
  const fetch_ = useCallback(async () => {
    if (!tenantId) return
    try {
      const res = await fetch(`${BASE}/dashboard/sentiment?tenant_id=${encodeURIComponent(tenantId)}${_tok()}`)
      if (res.ok) setEntries(await safeJson(res))
    } catch { /* stale data acceptable */ }
  }, [tenantId])
  useEffect(() => { fetch_(); const id = setInterval(fetch_, intervalMs); return () => clearInterval(id) }, [fetch_, intervalMs])
  return entries
}

// ─── useMetrics24h ────────────────────────────────────────────────────────────

export function useMetrics24h(tenantId: string, intervalMs = 60_000): Metrics24h | null {
  const [metrics, setMetrics] = useState<Metrics24h | null>(null)
  const fetch_ = useCallback(async () => {
    if (!tenantId) return
    try {
      const res = await fetch(`${BASE}/dashboard/metrics?tenant_id=${encodeURIComponent(tenantId)}`)
      if (res.ok) setMetrics(await safeJson(res))
    } catch { /* ignore */ }
  }, [tenantId])
  useEffect(() => { fetch_(); const id = setInterval(fetch_, intervalMs); return () => clearInterval(id) }, [fetch_, intervalMs])
  return metrics
}

// ─── usePoolSla ───────────────────────────────────────────────────────────────

export function usePoolSla(tenantId: string, intervalMs = 60_000): PoolSlaEntry[] {
  const [entries, setEntries] = useState<PoolSlaEntry[]>([])
  const fetch_ = useCallback(async () => {
    if (!tenantId) return
    try {
      const res = await fetch(`${BASE}/dashboard/pool-sla?tenant_id=${encodeURIComponent(tenantId)}${_tok()}`)
      if (res.ok) setEntries(await safeJson(res))
    } catch { /* stale data acceptable */ }
  }, [tenantId])
  useEffect(() => { fetch_(); const id = setInterval(fetch_, intervalMs); return () => clearInterval(id) }, [fetch_, intervalMs])
  return entries
}

// ─── usePoolViews ─────────────────────────────────────────────────────────────

export function usePoolViews(tenantId: string): {
  pools:       PoolView[]
  status:      ConnectionStatus
  metrics:     Metrics24h | null
  isStale:     boolean
  lastUpdated: number | null
} {
  const { snapshots, status, isStale, lastUpdated } = usePoolSnapshots(tenantId)
  const sentimentEntries = useSentimentLive(tenantId)
  const slaEntries       = usePoolSla(tenantId)
  const metrics          = useMetrics24h(tenantId)

  const sentimentMap = useMemo(() => {
    const m: Record<string, SentimentEntry> = {}
    for (const e of sentimentEntries) m[e.pool_id] = e
    return m
  }, [sentimentEntries])

  const slaMap = useMemo(() => {
    const m: Record<string, PoolSlaEntry> = {}
    for (const e of slaEntries) m[e.pool_id] = e
    return m
  }, [slaEntries])

  const pools = useMemo<PoolView[]>(() => snapshots.map(s => {
    const sent = sentimentMap[s.pool_id] ?? null
    const sla  = slaMap[s.pool_id]      ?? null
    return {
      pool_id:            s.pool_id,
      tenant_id:          s.tenant_id,
      available:          s.available,
      busy:               s.busy ?? 0,    // backward-compat: older snapshots won't have the field
      total_instances:    s.total_instances ?? null,
      queue_length:       s.queue_length,
      sla_target_ms:      s.sla_target_ms,
      channel_types:      s.channel_types,
      updated_at:         s.updated_at,
      avg_score:          sent?.avg_score   ?? null,
      sentiment_count:    sent?.count       ?? 0,
      distribution:       sent?.distribution ?? null,
      // SLA performance (from /dashboard/pool-sla, null when no data yet)
      avg_wait_ms:        sla?.avg_wait_ms        ?? null,
      p90_wait_ms:        sla?.p90_wait_ms        ?? null,
      sla_compliance_pct: sla?.sla_compliance_pct ?? null,
      sla_sessions_count: sla?.sessions_count     ?? 0,
    }
  }), [snapshots, sentimentMap, slaMap])

  return { pools, status, metrics, isStale, lastUpdated }
}

// ─── useActiveSessions ────────────────────────────────────────────────────────

export function useActiveSessions(
  tenantId:   string,
  poolId:     string | null,
  intervalMs = 10_000,
): { sessions: ActiveSession[]; loading: boolean } {
  const [sessions, setSessions] = useState<ActiveSession[]>([])
  const [loading, setLoading]   = useState(false)

  const fetch_ = useCallback(async () => {
    if (!tenantId || !poolId) return
    setLoading(true)
    try {
      const res = await fetch(
        `${BASE}/sessions/active?tenant_id=${encodeURIComponent(tenantId)}&pool_id=${encodeURIComponent(poolId)}&limit=100`,
      )
      if (res.ok) setSessions(await safeJson(res))
    } catch { /* stale data acceptable */ }
    finally { setLoading(false) }
  }, [tenantId, poolId])

  useEffect(() => {
    setSessions([])
    if (!poolId) return
    fetch_()
    const id = setInterval(fetch_, intervalMs)
    return () => clearInterval(id)
  }, [fetch_, poolId, intervalMs])

  return { sessions, loading }
}

// ─── useSessionStream ─────────────────────────────────────────────────────────

export function useSessionStream(
  tenantId:  string,
  sessionId: string | null,
): { entries: StreamEntry[]; status: ConnectionStatus } {
  const [entries, setEntries] = useState<StreamEntry[]>([])
  const [status,  setStatus]  = useState<ConnectionStatus>('connecting')
  const esRef       = useRef<EventSource | null>(null)
  const gotHistory  = useRef(false)

  useEffect(() => {
    setEntries([])
    gotHistory.current = false
    if (!sessionId || !tenantId) return

    let retryCount = 0
    const maxRetries = 3
    let closed = false

    function connect() {
      if (closed) return
      const url = `${BASE}/sessions/${encodeURIComponent(sessionId!)}/stream?tenant_id=${encodeURIComponent(tenantId)}`
      const es  = new EventSource(url)
      esRef.current = es
      setStatus('connecting')

      es.addEventListener('history', (e: MessageEvent) => {
        try {
          const parsed = JSON.parse(e.data) as StreamEntry[]
          gotHistory.current = true
          retryCount = 0
          setEntries(parsed)
          setStatus('connected')
        } catch (err) {
          console.warn('[useSessionStream] Failed to parse history:', err)
          setStatus('error')
        }
      })
      es.addEventListener('entry', (e: MessageEvent) => {
        try { setEntries(prev => [...prev, JSON.parse(e.data) as StreamEntry]) } catch { /* ignore */ }
      })
      es.addEventListener('error', () => {
        // EventSource fires error on connection loss. If we never got history,
        // try reconnecting a few times before giving up.
        if (!gotHistory.current && retryCount < maxRetries) {
          retryCount++
          es.close()
          esRef.current = null
          setTimeout(connect, 1000 * retryCount)
        } else {
          setStatus('error')
        }
      })
      es.onopen = () => {
        if (gotHistory.current) setStatus('connected')
        // Don't set to 'connected' until we actually get the history event
      }
    }

    connect()

    return () => {
      closed = true
      esRef.current?.close()
      esRef.current = null
      setStatus('closed')
    }
  }, [tenantId, sessionId])

  return { entries, status }
}

// ─── useSessionSegments ──────────────────────────────────────────────────────

export function useSessionSegments(
  tenantId:   string,
  sessionId:  string | null,
  intervalMs = 5_000,
): { segments: ContactSegment[]; loading: boolean; error: string | null } {
  const [segments, setSegments] = useState<ContactSegment[]>([])
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState<string | null>(null)
  // Track whether all segments are finalized (no active ones) — stop polling when true
  const allFinalized = useRef(false)

  const fetch_ = useCallback(async () => {
    if (!tenantId || !sessionId) return
    // Skip polling if all segments already ended — no new data expected
    if (allFinalized.current) return
    setLoading(true)
    try {
      const url = `${BASE}/reports/segments?tenant_id=${encodeURIComponent(tenantId)}&session_id=${encodeURIComponent(sessionId)}&page_size=50`
      const res = await fetch(url)
      if (!res.ok) {
        setError(`API indisponível (HTTP ${res.status})`)
        return
      }
      const data = await safeJson<{ data: ContactSegment[]; error?: string }>(res)
      if (data.error) {
        setError('Erro ao carregar segmentos — verifique se o analytics-api está online')
        return
      }
      // Sort: primary before specialist, then by started_at ascending
      const sorted = (data.data ?? []).slice().sort((a, b) => {
        if (a.started_at < b.started_at) return -1
        if (a.started_at > b.started_at) return 1
        return 0
      })
      setSegments(sorted)
      setError(null)
      // If we got segments and none are active, mark as finalized to stop polling
      if (sorted.length > 0 && sorted.every(s => s.ended_at !== null)) {
        allFinalized.current = true
      }
    } catch (err) {
      setError(`Erro de rede: ${String(err)}`)
    }
    finally { setLoading(false) }
  }, [tenantId, sessionId])

  useEffect(() => {
    setSegments([])
    setError(null)
    allFinalized.current = false
    if (!sessionId) return
    fetch_()
    const id = setInterval(fetch_, intervalMs)
    return () => clearInterval(id)
  }, [fetch_, sessionId, intervalMs])

  return { segments, loading, error }
}

// ─── useSessionChildren (S1/S3 — timeline do contato) ────────────────────────
//
// Filhas de UM SALTO desta sessão (`origin_session_id`). NÃO é a journey: o fecho
// transitivo traria, em processo multi-contato, as filhas do contato IRMÃO
// penduradas neste. Sem janela de data — o backend a ignora quando o filtro é este,
// porque a filha nasce DEPOIS do pai e some no recorte do dia.
//
// Uma passada só, sem polling: a lista é curta e o caso vivo (filha nascendo agora)
// já é coberto pelo poll dos segmentos ao lado.

export interface SessionChild {
  session_id:         string
  channel:            string
  pool_id:            string | null
  opened_at:          string | null
  closed_at:          string | null
  outcome:            string | null
  status:             string | null
  elapsed_time_ms:    number | null
  handle_time_ms:     number | null
  spawn_reason:       string | null
  root_session_id:    string | null
  origin_session_id:  string | null
  is_internal?:       boolean
}

export function useSessionChildren(
  tenantId:  string,
  sessionId: string | null,
): { children: SessionChild[]; loading: boolean; error: string | null } {
  const [children, setChildren] = useState<SessionChild[]>([])
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState<string | null>(null)

  useEffect(() => {
    setChildren([]); setError(null)
    if (!tenantId || !sessionId) return
    let cancelled = false
    setLoading(true)
    const url = `${BASE}/reports/sessions?tenant_id=${encodeURIComponent(tenantId)}`
      + `&origin_session_id=${encodeURIComponent(sessionId)}&page_size=50`
    fetch(url)
      .then(async res => {
        if (cancelled) return
        if (!res.ok) { setError(`HTTP ${res.status}`); return }
        const data = await safeJson<{ data: SessionChild[]; error?: string }>(res)
        if (cancelled) return
        // Degradação do backend NÃO pode virar "não originou nada": um erro aqui
        // é indistinguível de ausência na tela se não for dito.
        if (data.error) { setError(data.error); return }
        setChildren((data.data ?? []).slice().sort((a, b) =>
          (a.opened_at ?? '') < (b.opened_at ?? '') ? -1 : 1))
      })
      .catch(err => { if (!cancelled) setError(String(err)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [tenantId, sessionId])

  return { children, loading, error }
}

// ─── useSupervisor ────────────────────────────────────────────────────────────

export function useSupervisor(tenantId: string, sessionId: string | null): {
  state:   SupervisorState
  join:    (operatorId?: string) => Promise<void>
  message: (text: string, visibility?: 'agents_only' | 'all') => Promise<void>
  leave:   () => Promise<void>
} {
  const [state, setState] = useState<SupervisorState>({
    status: 'idle', participantId: null, joinedAt: null, error: null,
  })

  useEffect(() => {
    setState({ status: 'idle', participantId: null, joinedAt: null, error: null })
  }, [sessionId])

  const join = useCallback(async (operatorId = 'operator') => {
    if (!sessionId || !tenantId) return
    setState(s => ({ ...s, status: 'joining', error: null }))
    try {
      const res = await fetch(`${BASE}/supervisor/join`, {
        method: 'POST', headers: _authHeaders(),
        body: JSON.stringify({ tenant_id: tenantId, session_id: sessionId, operator_id: operatorId }),
      })
      if (!res.ok) { const e = await safeJson<{detail?:string}>(res).catch(() => ({})); throw new Error((e as {detail?:string}).detail ?? `HTTP ${res.status}`) }
      const data = await safeJson<{ participant_id: string; joined_at: string }>(res)
      setState({ status: 'active', participantId: data.participant_id, joinedAt: data.joined_at, error: null })
    } catch (err) {
      setState(s => ({ ...s, status: 'error', error: String(err) }))
    }
  }, [tenantId, sessionId])

  const message = useCallback(async (text: string, visibility: 'agents_only' | 'all' = 'agents_only') => {
    if (!sessionId || !tenantId || !state.participantId) return
    try {
      const res = await fetch(`${BASE}/supervisor/message`, {
        method: 'POST', headers: _authHeaders(),
        body: JSON.stringify({ tenant_id: tenantId, session_id: sessionId, participant_id: state.participantId, text, visibility }),
      })
      if (!res.ok) { const e = await safeJson<{detail?:string}>(res).catch(() => ({})); throw new Error((e as {detail?:string}).detail ?? `HTTP ${res.status}`) }
    } catch (err) {
      setState(s => ({ ...s, error: String(err) }))
    }
  }, [tenantId, sessionId, state.participantId])

  const leave = useCallback(async () => {
    if (!sessionId || !tenantId || !state.participantId) {
      setState({ status: 'idle', participantId: null, joinedAt: null, error: null })
      return
    }
    setState(s => ({ ...s, status: 'leaving', error: null }))
    try {
      await fetch(`${BASE}/supervisor/leave`, {
        method: 'POST', headers: _authHeaders(),
        body: JSON.stringify({ tenant_id: tenantId, session_id: sessionId, participant_id: state.participantId }),
      })
    } catch { /* leave is best-effort */ }
    finally {
      setState({ status: 'idle', participantId: null, joinedAt: null, error: null })
    }
  }, [tenantId, sessionId, state.participantId])

  return { state, join, message, leave }
}
