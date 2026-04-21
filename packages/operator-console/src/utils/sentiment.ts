/**
 * Sentiment utility functions.
 * Thresholds mirror the platform defaults in config-api seed (sentiment.thresholds).
 * In Phase 2 these will be read from the Config API per tenant.
 */

export type SentimentCategory = 'satisfied' | 'neutral' | 'frustrated' | 'angry' | 'unknown'

/** Maps a score (-1 to 1) to a category label. */
export function scoreToCategory(score: number | null): SentimentCategory {
  if (score === null) return 'unknown'
  if (score >=  0.3)  return 'satisfied'
  if (score >= -0.3)  return 'neutral'
  if (score >= -0.6)  return 'frustrated'
  return 'angry'
}

/** Returns a CSS background color for a sentiment score. */
export function scoreToColor(score: number | null): string {
  if (score === null) return '#1e293b'          // slate-800 — no data
  if (score >=  0.3)  return scoreGreen(score)
  if (score >= -0.3)  return scoreAmber(score)
  if (score >= -0.6)  return scoreOrange(score)
  return scoreRed(score)
}

/** Returns a contrasting text color for the tile. */
export function scoreToBadgeStyle(score: number | null): React.CSSProperties {
  const bg = scoreToColor(score)
  return { backgroundColor: bg, color: '#fff' }
}

/** Returns a CSS border-left color for the panel accent. */
export function scoreToAccent(score: number | null): string {
  if (score === null) return '#475569'
  if (score >=  0.3)  return '#22c55e'
  if (score >= -0.3)  return '#eab308'
  if (score >= -0.6)  return '#f97316'
  return '#ef4444'
}

function scoreGreen(score: number): string {
  // 0.3 → #16a34a (green-600), 1.0 → #4ade80 (green-400)
  const t = (score - 0.3) / 0.7
  return interpolateHex('#16a34a', '#4ade80', t)
}

function scoreAmber(score: number): string {
  // -0.3 → #b45309 (amber-700), 0.3 → #fbbf24 (amber-400)
  const t = (score + 0.3) / 0.6
  return interpolateHex('#b45309', '#fbbf24', t)
}

function scoreOrange(score: number): string {
  // -0.6 → #c2410c (orange-700), -0.3 → #f97316 (orange-500)
  const t = (score + 0.6) / 0.3
  return interpolateHex('#c2410c', '#f97316', t)
}

function scoreRed(score: number): string {
  // -1.0 → #7f1d1d (red-900), -0.6 → #ef4444 (red-500)
  const t = (score + 1.0) / 0.4
  return interpolateHex('#7f1d1d', '#ef4444', t)
}

function interpolateHex(hex1: string, hex2: string, t: number): string {
  const clamp = Math.max(0, Math.min(1, t))
  const r1 = parseInt(hex1.slice(1, 3), 16)
  const g1 = parseInt(hex1.slice(3, 5), 16)
  const b1 = parseInt(hex1.slice(5, 7), 16)
  const r2 = parseInt(hex2.slice(1, 3), 16)
  const g2 = parseInt(hex2.slice(3, 5), 16)
  const b2 = parseInt(hex2.slice(5, 7), 16)
  const r  = Math.round(r1 + (r2 - r1) * clamp)
  const g  = Math.round(g1 + (g2 - g1) * clamp)
  const b  = Math.round(b1 + (b2 - b1) * clamp)
  return `rgb(${r},${g},${b})`
}

/** Formats a score as a readable string. */
export function formatScore(score: number | null): string {
  if (score === null) return '—'
  return score.toFixed(2)
}

/** Formats milliseconds as a human-readable duration. */
export function formatMs(ms: number | null): string {
  if (ms === null || ms === 0) return '—'
  if (ms < 1_000)  return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const mins = Math.floor(ms / 60_000)
  const secs = Math.round((ms % 60_000) / 1000)
  return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`
}

/** SLA compliance: ratio of queue_length × avg_handle_ms vs sla_target. */
export function slaStatus(
  queueLength: number,
  slaTargetMs: number,
  avgHandleMs: number | null,
): 'ok' | 'warning' | 'breach' {
  if (queueLength === 0) return 'ok'
  const estimated = queueLength * (avgHandleMs ?? slaTargetMs * 0.7)
  const ratio = estimated / slaTargetMs
  if (ratio <= 0.7) return 'ok'
  if (ratio <= 1.0) return 'warning'
  return 'breach'
}

// React import needed for CSSProperties type
import type React from 'react'
