/**
 * Sentiment utility functions.
 * Thresholds mirror platform defaults in config-api seed (sentiment.thresholds).
 */
import type React from 'react'

export type SentimentCategory = 'satisfied' | 'neutral' | 'frustrated' | 'angry' | 'unknown'

export function scoreToCategory(score: number | null): SentimentCategory {
  if (score === null) return 'unknown'
  if (score >=  0.3)  return 'satisfied'
  if (score >= -0.3)  return 'neutral'
  if (score >= -0.6)  return 'frustrated'
  return 'angry'
}

export function scoreToColor(score: number | null): string {
  if (score === null) return '#1e293b'
  if (score >=  0.3)  return scoreGreen(score)
  if (score >= -0.3)  return scoreAmber(score)
  if (score >= -0.6)  return scoreOrange(score)
  return scoreRed(score)
}

export function scoreToBadgeStyle(score: number | null): React.CSSProperties {
  return { backgroundColor: scoreToColor(score), color: '#fff' }
}

export function scoreToAccent(score: number | null): string {
  if (score === null) return '#475569'
  if (score >=  0.3)  return '#22c55e'
  if (score >= -0.3)  return '#eab308'
  if (score >= -0.6)  return '#f97316'
  return '#ef4444'
}

function scoreGreen(score: number): string {
  const t = (score - 0.3) / 0.7
  return interpolateHex('#16a34a', '#4ade80', t)
}

function scoreAmber(score: number): string {
  const t = (score + 0.3) / 0.6
  return interpolateHex('#b45309', '#fbbf24', t)
}

function scoreOrange(score: number): string {
  const t = (score + 0.6) / 0.3
  return interpolateHex('#c2410c', '#f97316', t)
}

function scoreRed(score: number): string {
  const t = (score + 1.0) / 0.4
  return interpolateHex('#7f1d1d', '#ef4444', t)
}

function interpolateHex(hex1: string, hex2: string, t: number): string {
  const clamp = Math.max(0, Math.min(1, t))
  const r1 = parseInt(hex1.slice(1, 3), 16), g1 = parseInt(hex1.slice(3, 5), 16), b1 = parseInt(hex1.slice(5, 7), 16)
  const r2 = parseInt(hex2.slice(1, 3), 16), g2 = parseInt(hex2.slice(3, 5), 16), b2 = parseInt(hex2.slice(5, 7), 16)
  return `rgb(${Math.round(r1 + (r2-r1)*clamp)},${Math.round(g1 + (g2-g1)*clamp)},${Math.round(b1 + (b2-b1)*clamp)})`
}

export function formatScore(score: number | null): string {
  if (score === null) return '—'
  return score.toFixed(2)
}

export function formatMs(ms: number | null): string {
  if (ms === null || ms === 0) return '—'
  if (ms < 1_000)  return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const mins = Math.floor(ms / 60_000)
  const secs = Math.round((ms % 60_000) / 1000)
  return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`
}

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
