/**
 * useTimeseriesData.ts
 * Fetch hook for analytics-api timeseries endpoints.
 *
 * The hook builds a URL from baseUrl + query params, fetches once on mount,
 * and optionally polls every `pollMs` milliseconds.
 *
 * accessToken is forwarded as Bearer when provided (evaluation reports scope).
 */
import { useEffect, useRef, useState } from 'react'
import type { TimeseriesResponse } from '@/types'

export interface TimeseriesParams {
  tenantId:    string
  fromDt?:     string   // ISO8601
  toDt?:       string   // ISO8601
  interval?:   number   // minutes, default 60
  breakdownBy?: string
  poolId?:     string
  campaignId?: string   // for /score endpoint
}

interface UseTimeseriesDataOptions {
  baseUrl:      string          // e.g. "/reports/timeseries/volume"
  params:       TimeseriesParams
  pollMs?:      number          // 0 = no polling (default)
  accessToken?: string
}

interface State {
  data:    TimeseriesResponse | null
  loading: boolean
  error:   string | null
}

export function useTimeseriesData({
  baseUrl,
  params,
  pollMs = 0,
  accessToken,
}: UseTimeseriesDataOptions): State {
  const [state, setState] = useState<State>({ data: null, loading: true, error: null })
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  function buildUrl(): string {
    const qs = new URLSearchParams()
    qs.set('tenant_id', params.tenantId)
    if (params.fromDt)      qs.set('from_dt', params.fromDt)
    if (params.toDt)        qs.set('to_dt', params.toDt)
    if (params.interval)    qs.set('interval', String(params.interval))
    if (params.breakdownBy) qs.set('breakdown_by', params.breakdownBy)
    if (params.poolId)      qs.set('pool_id', params.poolId)
    if (params.campaignId)  qs.set('campaign_id', params.campaignId)
    return `${baseUrl}?${qs.toString()}`
  }

  async function fetchData(cancelled: { v: boolean }) {
    try {
      const headers: Record<string, string> = {}
      if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`

      const res = await fetch(buildUrl(), { headers })
      if (cancelled.v) return

      if (!res.ok) {
        setState({ data: null, loading: false, error: `HTTP ${res.status}` })
        return
      }
      const json: TimeseriesResponse = await res.json()
      setState({ data: json, loading: false, error: json.error ?? null })
    } catch (err) {
      if (!cancelled.v) {
        setState({ data: null, loading: false, error: String(err) })
      }
    }
  }

  useEffect(() => {
    const cancelled = { v: false }
    setState(s => ({ ...s, loading: true, error: null }))
    fetchData(cancelled)

    if (pollMs > 0) {
      timerRef.current = setInterval(() => fetchData(cancelled), pollMs)
    }

    return () => {
      cancelled.v = true
      if (timerRef.current) clearInterval(timerRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [baseUrl, JSON.stringify(params), pollMs, accessToken])

  return state
}
