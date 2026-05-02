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
  PoolSnapshot, PoolView, SentimentEntry, StreamEntry, SupervisorState
} from '../types'

const BASE = ''  // relative URLs — Vite proxies to analytics-api on port 3500

// ─── usePoolSnapshots ─────────────────────────────────────────────────────────

export function usePoolSnapshots(tenantId: string): {
  snapshots: PoolSnapshot[]
  status:    ConnectionStatus
} {
  const [snapshots, setSnapshots] = useState<PoolSnapshot[]>([])
  const [status, setStatus]       = useState<ConnectionStatus>('connecting')
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    if (!tenantId) return
    const url = `${BASE}/dashboard/operational?tenant_id=${encodeURIComponent(tenantId)}`
    const es  = new EventSource(url)
    esRef.current = es
    setStatus('connecting')
    es.addEventListener('pools', (e: MessageEvent) => {
      try { setSnapshots(JSON.parse(e.data) as PoolSnapshot[]); setStatus('connected') } catch { /* ignore */ }
    })
    es.addEventListener('error', () => setStatus('error'))
    es.onopen = () => setStatus('connected')
    return () => { es.close(); esRef.current = null; setStatus('closed') }
  }, [tenantId])

  return { snapshots, status }
}

// ─── useSentimentLive ─────────────────────────────────────────────────────────

export function useSentimentLive(tenantId: string, intervalMs = 10_000): SentimentEntry[] {
  const [entries, setEntries] = useState<SentimentEntry[]>([])
  const fetch_ = useCallback(async () => {
    if (!tenantId) return
    try {
      const res = await fetch(`${BASE}/dashboard/sentiment?tenant_id=${encodeURIComponent(tenantId)}`)
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

// ─── usePoolViews ─────────────────────────────────────────────────────────────

export function usePoolViews(tenantId: string): {
  pools:   PoolView[]
  status:  ConnectionStatus
  metrics: Metrics24h | null
} {
  const { snapshots, status } = usePoolSnapshots(tenantId)
  const sentimentEntries      = useSentimentLive(tenantId)
  const metrics               = useMetrics24h(tenantId)

  const sentimentMap = useMemo(() => {
    const m: Record<string, SentimentEntry> = {}
    for (const e of sentimentEntries) m[e.pool_id] = e
    return m
  }, [sentimentEntries])

  const pools = useMemo<PoolView[]>(() => snapshots.map(s => {
    const sent = sentimentMap[s.pool_id] ?? null
    return {
      pool_id:         s.pool_id,
      tenant_id:       s.tenant_id,
      available:       s.available,
      queue_length:    s.queue_length,
      sla_target_ms:   s.sla_target_ms,
      channel_types:   s.channel_types,
      updated_at:      s.updated_at,
      avg_score:       sent?.avg_score   ?? null,
      sentiment_count: sent?.count       ?? 0,
      distribution:    sent?.distribution ?? null,
    }
  }), [snapshots, sentimentMap])

  return { pools, status, metrics }
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
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    setEntries([])
    if (!sessionId || !tenantId) return
    const url = `${BASE}/sessions/${encodeURIComponent(sessionId)}/stream?tenant_id=${encodeURIComponent(tenantId)}`
    const es  = new EventSource(url)
    esRef.current = es
    setStatus('connecting')
    es.addEventListener('history', (e: MessageEvent) => {
      try { setEntries(JSON.parse(e.data) as StreamEntry[]); setStatus('connected') } catch { /* ignore */ }
    })
    es.addEventListener('entry', (e: MessageEvent) => {
      try { setEntries(prev => [...prev, JSON.parse(e.data) as StreamEntry]) } catch { /* ignore */ }
    })
    es.addEventListener('error', () => setStatus('error'))
    es.onopen = () => setStatus('connected')
    return () => { es.close(); esRef.current = null; setStatus('closed') }
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

  const fetch_ = useCallback(async () => {
    if (!tenantId || !sessionId) return
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
    } catch (err) {
      setError(`Erro de rede: ${String(err)}`)
    }
    finally { setLoading(false) }
  }, [tenantId, sessionId])

  useEffect(() => {
    setSegments([])
    setError(null)
    if (!sessionId) return
    fetch_()
    const id = setInterval(fetch_, intervalMs)
    return () => clearInterval(id)
  }, [fetch_, sessionId, intervalMs])

  return { segments, loading, error }
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
        method: 'POST', headers: { 'Content-Type': 'application/json' },
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
        method: 'POST', headers: { 'Content-Type': 'application/json' },
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
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tenant_id: tenantId, session_id: sessionId, participant_id: state.participantId }),
      })
    } catch { /* leave is best-effort */ }
    finally {
      setState({ status: 'idle', participantId: null, joinedAt: null, error: null })
    }
  }, [tenantId, sessionId, state.participantId])

  return { state, join, message, leave }
}
