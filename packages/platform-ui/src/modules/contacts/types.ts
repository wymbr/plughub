/**
 * Shared types for the Contacts module (Lista | Monitor | Análise tabs).
 */

export interface ContactFilters {
  fromDt:          string
  toDt:            string
  sessionIdSearch: string
  channel:         string
  outcome:         string
  poolId:          string
  agentId:         string
  ani:             string
  dnis:            string
  insightCategory: string
  insightTags:     string
}

export interface ContactRow {
  session_id:     string
  tenant_id:      string
  channel:        string
  pool_id:        string | null
  customer_id:    string | null
  opened_at:      string | null
  closed_at:      string | null
  close_reason:   string | null
  outcome:        string | null
  wait_time_ms:   number | null
  handle_time_ms: number | null
  ani:            string | null
  dnis:           string | null
  segment_count:  number
}

export interface ContactsApiResponse {
  data: ContactRow[]
  meta?: { total?: number; page?: number; page_size?: number }
}

// ── Visualization format for Monitor + Análise ─────────────────────────────

export type VizFormat = 'heatmap' | 'bars' | 'donut' | 'tiles' | 'table'

// ── Helpers ────────────────────────────────────────────────────────────────

export function formatMs(ms: number | null): string {
  if (ms === null || ms === undefined) return '—'
  const s = Math.floor(ms / 1000)
  const m = Math.floor(s / 60)
  const h = Math.floor(m / 60)
  if (h > 0) return `${h}h ${m % 60}m`
  if (m > 0) return `${m}m ${s % 60}s`
  return `${s}s`
}

export function formatDt(dt: string | null): string {
  if (!dt) return '—'
  try {
    return new Date(dt).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })
  } catch { return dt }
}

export function iso7dAgo(): string {
  const d = new Date(); d.setDate(d.getDate() - 7)
  return d.toISOString().slice(0, 10)
}
export function isoToday(): string { return new Date().toISOString().slice(0, 10) }

export const CHANNEL_ICONS: Record<string, string> = {
  webchat: '💬', whatsapp: '📱', voice: '📞', email: '✉️',
  sms: '📟', instagram: '📷', telegram: '✈️', webrtc: '🎥',
}

export const OUTCOME_COLORS: Record<string, string> = {
  resolved:    '#059669',
  escalated:   '#d97706',
  transferred: '#2563eb',
  abandoned:   '#dc2626',
  timeout:     '#9333ea',
}

export const DEFAULT_FILTERS: ContactFilters = {
  fromDt:          iso7dAgo(),
  toDt:            isoToday(),
  sessionIdSearch: '',
  channel:         '',
  outcome:         '',
  poolId:          '',
  agentId:         '',
  ani:             '',
  dnis:            '',
  insightCategory: '',
  insightTags:     '',
}
