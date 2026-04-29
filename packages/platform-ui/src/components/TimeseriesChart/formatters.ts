/**
 * formatters.ts
 * Value-formatting helpers for TimeseriesChart.
 */

/** Format raw session count */
export function formatCount(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000)     return `${(value / 1_000).toFixed(1)}k`
  return String(Math.round(value))
}

/** Format duration_ms → "Xm Ys" or "Xh Ym" */
export function formatDurationMs(ms: number): string {
  if (!ms || ms <= 0) return '0s'
  const totalSeconds = Math.round(ms / 1000)
  const hours   = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  if (hours > 0)   return `${hours}h ${minutes}m`
  if (minutes > 0) return `${minutes}m ${seconds}s`
  return `${seconds}s`
}

/** Format a 0–1 evaluation score as percentage string */
export function formatScore(value: number): string {
  return `${(value * 100).toFixed(1)}%`
}

/** Pick a formatter by type key */
export type FormatType = 'count' | 'duration_ms' | 'score' | 'number'

export function getFormatter(type: FormatType): (v: number) => string {
  switch (type) {
    case 'duration_ms': return formatDurationMs
    case 'score':       return formatScore
    case 'count':
    case 'number':
    default:            return formatCount
  }
}

/** Shorten an ISO8601 bucket label for axis display */
export function formatBucketLabel(bucket: string, intervalMinutes: number): string {
  try {
    const d = new Date(bucket)
    if (intervalMinutes >= 1440) {
      // daily — show "Apr 29"
      return d.toLocaleDateString('pt-BR', { month: 'short', day: 'numeric' })
    }
    if (intervalMinutes >= 60) {
      // hourly — show "10:00"
      return d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
    }
    // sub-hour — show "10:30"
    return d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
  } catch {
    return bucket
  }
}
