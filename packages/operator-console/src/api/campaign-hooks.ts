/**
 * campaign-hooks.ts
 * React hooks for campaign (collect) analytics.
 */
import { useCallback, useEffect, useState } from 'react'
import type { CampaignSummary, CollectEvent } from '../types'

const ANALYTICS_BASE = import.meta.env.VITE_ANALYTICS_BASE_URL ?? ''

export interface CampaignData {
  data:    CollectEvent[]
  summary: CampaignSummary[]
  meta:    { page: number; page_size: number; total: number; from_dt: string; to_dt: string }
}

export function useCampaignData(
  tenantId:    string,
  campaignId?: string,
  channel?:    string,
  status?:     string,
  intervalMs = 30_000,
): { campaign: CampaignData | null; loading: boolean; refresh: () => Promise<void> } {
  const [campaign, setCampaign] = useState<CampaignData | null>(null)
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    if (!tenantId) return
    setLoading(true)
    try {
      const params = new URLSearchParams({ tenant_id: tenantId, page_size: '200' })
      if (campaignId) params.append('campaign_id', campaignId)
      if (channel)    params.append('channel', channel)
      if (status)     params.append('status', status)
      const res = await fetch(`${ANALYTICS_BASE}/reports/campaigns?${params}`)
      if (res.ok) {
        const data = (await res.json()) as CampaignData
        setCampaign(data)
      }
    } catch {
      // stale data acceptable
    } finally {
      setLoading(false)
    }
  }, [tenantId, campaignId, channel, status])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, intervalMs)
    return () => clearInterval(id)
  }, [refresh, intervalMs])

  return { campaign, loading, refresh }
}
