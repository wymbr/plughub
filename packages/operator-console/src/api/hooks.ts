/**
 * hooks.ts
 * React hooks for real-time data from the analytics-api.
 *
 * usePoolSnapshots — SSE stream from GET /dashboard/operational
 * useSentimentLive — polling GET /dashboard/sentiment every 10s
 * useMetrics24h    — polling GET /dashboard/metrics every 60s
 * usePoolViews     — merges snapshots + sentiment into PoolView[]
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ActiveSession, ConnectionStatus, Metrics24h, PoolSnapshot, PoolView, SentimentEntry, StreamEntry, SupervisorState } from '../types'

const BASE = import.meta.env.VITE_API_BASE_URL ?? ''

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
      try {
        const data = JSON.parse(e.data) as PoolSnapshot[]
        setSnapshots(data)
        setStatus('connected')
      } catch {
        // ignore parse errors
      }
    })

    es.addEventListener('error', () => {
      setStatus('error')
    })

    es.onopen = () => setStatus('connected')

    return () => {
      es.close()
      esRef.current = null
      setStatus('closed')
    }
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
      if (res.ok) setEntries(await res.json())
    } catch {
      // ignore — stale data is acceptable
    }
  }, [tenantId])

  useEffect(() => {
    fetch_()
    const id = setInterval(fetch_, intervalMs)
    return () => clearInterval(id)
  }, [fetch_, intervalMs])

  return entries
}

// ─── useMetrics24h ────────────────────────────────────────────────────────────

export function useMetrics24h(tenantId: string, intervalMs = 60_000): Metrics24h | null {
  const [metrics, setMetrics] = useState<Metrics24h | null>(null)

  const fetch_ = useCallback(async () => {
    if (!tenantId) return
    try {
      const res = await fetch(`${BASE}/dashboard/metrics?tenant_id=${encodeURIComponent(tenantId)}`)
      if (res.ok) setMetrics(await res.json())
    } catch {
      // ignore
    }
  }, [tenantId])

  useEffect(() => {
    fetch_()
    const id = setInterval(fetch_, intervalMs)
    return () => clearInterval(id)
  }, [fetch_, intervalMs])

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

  const pools = useMemo<PoolView[]>(() => {
    return snapshots.map(s => {
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
    })
  }, [snapshots, sentimentMap])

  return { pools, status, metrics }
}

// ─── useActiveSessions ────────────────────────────────────────────────────────

/**
 * Polls GET /sessions/active for the given pool every intervalMs.
 * Returns the list sorted worst sentiment first (as returned by the API).
 */
export function useActiveSessions(
  tenantId: string,
  poolId:   string | null,
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
      if (res.ok) setSessions(await res.json())
    } catch {
      // stale data acceptable
    } finally {
      setLoading(false)
    }
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

/**
 * Opens an SSE connection to GET /sessions/{id}/stream.
 * First event type "history" delivers all existing entries.
 * Subsequent events type "entry" deliver new entries as they arrive.
 */
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
      try {
        const data = JSON.parse(e.data) as StreamEntry[]
        setEntries(data)
        setStatus('connected')
      } catch { /* ignore */ }
    })

    es.addEventListener('entry', (e: MessageEvent) => {
      try {
        const entry = JSON.parse(e.data) as StreamEntry
        setEntries(prev => [...prev, entry])
      } catch { /* ignore */ }
    })

    es.addEventListener('error', () => setStatus('error'))
    es.onopen = () => setStatus('connected')

    return () => {
      es.close()
      esRef.current = null
      setStatus('closed')
    }
  }, [tenantId, sessionId])

  return { entries, status }
}

// ─── useSupervisor ────────────────────────────────────────────────────────────

/**
 * Manages supervisor intervention lifecycle for a live session.
 *
 * join()    → POST /supervisor/join   → sets participantId
 * message() → POST /supervisor/message
 * leave()   → POST /supervisor/leave  → clears state
 *
 * Human operators bypass the MCP agent lifecycle entirely —
 * the backend writes directly to the Redis session stream.
 */
export function useSupervisor(tenantId: string, sessionId: string | null): {
  state:   SupervisorState
  join:    (operatorId?: string) => Promise<void>
  message: (text: string, visibility?: 'agents_only' | 'all') => Promise<void>
  leave:   () => Promise<void>
} {
  const [state, setState] = useState<SupervisorState>({
    status:        'idle',
    participantId: null,
    joinedAt:      null,
    error:         null,
  })

  // Reset when session changes
  useEffect(() => {
    setState({ status: 'idle', participantId: null, joinedAt: null, error: null })
  }, [sessionId])

  const join = useCallback(async (operatorId = 'operator') => {
    if (!sessionId || !tenantId) return
    setState(s => ({ ...s, status: 'joining', error: null }))
    try {
      const res = await fetch(`${BASE}/supervisor/join`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ tenant_id: tenantId, session_id: sessionId, operator_id: operatorId }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`)
      }
      const data = await res.json() as { participant_id: string; joined_at: string }
      setState({
        status:        'active',
        participantId: data.participant_id,
        joinedAt:      data.joined_at,
        error:         null,
      })
    } catch (err) {
      setState(s => ({ ...s, status: 'error', error: String(err) }))
    }
  }, [tenantId, sessionId])

  const message = useCallback(async (text: string, visibility: 'agents_only' | 'all' = 'agents_only') => {
    if (!sessionId || !tenantId || !state.participantId) return
    try {
      const res = await fetch(`${BASE}/supervisor/message`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          tenant_id:      tenantId,
          session_id:     sessionId,
          participant_id: state.participantId,
          text,
          visibility,
        }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`)
      }
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
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          tenant_id:      tenantId,
          session_id:     sessionId,
          participant_id: state.participantId,
        }),
      })
    } catch {
      // ignore — leave is best-effort
    } finally {
      setState({ status: 'idle', participantId: null, joinedAt: null, error: null })
    }
  }, [tenantId, sessionId, state.participantId])

  return { state, join, message, leave }
}
