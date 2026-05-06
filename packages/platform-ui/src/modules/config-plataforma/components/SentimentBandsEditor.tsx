/**
 * SentimentBandsEditor.tsx
 *
 * Edits sentiment.bands in Config API (namespace=sentiment, key=bands).
 *
 * Format stored:
 *   { bands: [ { level: 1, min: -1.0, max: -0.6 }, ... ] }
 *
 * Levels are integers (1 = worst, N = best). Display labels are resolved by
 * the i18n layer (e.g. pt-BR: level 1 = "Muito Insatisfeito") — no text is
 * stored here to support internationalisation.
 *
 * Score range: -1.0 (most negative) → +1.0 (most positive).
 * Bands must be contiguous and cover [-1.0, 1.0] without gaps.
 */
import React, { useState, useEffect, useCallback } from 'react'
import { useNamespace, putConfig, type ConfigEntry } from '../api/config-hooks'
import Spinner from '@/components/ui/Spinner'

// ── Types ──────────────────────────────────────────────────────────────────────

interface SentimentBand {
  level: number   // 1 = worst, N = best
  min:   number   // inclusive lower bound
  max:   number   // exclusive upper bound (inclusive for level N)
}

interface BandsValue {
  bands: SentimentBand[]
}

// ── Defaults (mirrors seed sentiment.thresholds, converted to numeric levels) ─

const DEFAULT_BANDS: SentimentBand[] = [
  { level: 1, min: -1.0, max: -0.6 },
  { level: 2, min: -0.6, max: -0.3 },
  { level: 3, min: -0.3, max:  0.3 },
  { level: 4, min:  0.3, max:  1.0 },
]

// Placeholder labels shown in the editor (display-only, not stored)
const LEVEL_HINT: Record<number, string> = {
  1: 'Nível 1 — pior extremo',
  2: 'Nível 2',
  3: 'Nível 3',
  4: 'Nível 4',
  5: 'Nível 5',
  6: 'Nível 6 — melhor extremo',
}

function levelHint(level: number, total: number) {
  if (level === 1) return `Nível ${level} — pior`
  if (level === total) return `Nível ${level} — melhor`
  return `Nível ${level}`
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function parseBands(entry: ConfigEntry | undefined): SentimentBand[] {
  if (!entry) return DEFAULT_BANDS
  const v = entry.value as BandsValue | null
  if (!v || !Array.isArray(v.bands) || v.bands.length === 0) return DEFAULT_BANDS
  return v.bands.map((b, i) => ({ level: b.level ?? i + 1, min: b.min, max: b.max }))
}

function validateBands(bands: SentimentBand[]): string | null {
  if (bands.length < 2) return 'Defina ao menos 2 faixas'
  if (bands.length > 6) return 'Máximo de 6 faixas'

  const sorted = [...bands].sort((a, b) => a.min - b.min)
  if (sorted[0].min !== -1.0) return 'A primeira faixa deve começar em -1.0'
  if (sorted[sorted.length - 1].max !== 1.0) return 'A última faixa deve terminar em 1.0'

  for (let i = 1; i < sorted.length; i++) {
    if (Math.abs(sorted[i].min - sorted[i - 1].max) > 0.001) {
      return `Faixa ${i + 1}: limite inferior (${sorted[i].min}) deve ser igual ao superior da faixa anterior (${sorted[i - 1].max})`
    }
  }
  return null
}

// ── Component ─────────────────────────────────────────────────────────────────

interface Props {
  tenantId:   string
  adminToken: string
}

export function SentimentBandsEditor({ tenantId, adminToken }: Props) {
  const { entries, loading, error, reload } = useNamespace(tenantId, 'sentiment')
  const [bands,   setBands]   = useState<SentimentBand[]>(DEFAULT_BANDS)
  const [saving,  setSaving]  = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)
  const [dirty,   setDirty]   = useState(false)

  // Sync from remote when entries load
  useEffect(() => {
    if (!loading) {
      setBands(parseBands(entries['bands']))
      setDirty(false)
    }
  }, [loading, entries])

  const validationError = validateBands(bands)

  // ── Handlers ────────────────────────────────────────────────────────────────

  const updateBand = useCallback((idx: number, field: 'min' | 'max', raw: string) => {
    const n = parseFloat(raw)
    if (isNaN(n)) return
    const clamped = Math.max(-1.0, Math.min(1.0, n))
    setBands(prev => {
      const next = prev.map((b, i) => i === idx ? { ...b, [field]: clamped } : b)
      return next
    })
    setDirty(true)
    setSaveErr(null)
  }, [])

  const addBand = useCallback(() => {
    setBands(prev => {
      if (prev.length >= 6) return prev
      // Split the last band in two
      const last = prev[prev.length - 1]
      const mid  = parseFloat(((last.min + last.max) / 2).toFixed(2))
      const newLevel = prev.length + 1
      const updated  = prev.map((b, i) => i === prev.length - 1 ? { ...b, max: mid } : b)
      return [...updated, { level: newLevel, min: mid, max: last.max }]
    })
    setDirty(true)
    setSaveErr(null)
  }, [])

  const removeBand = useCallback((idx: number) => {
    setBands(prev => {
      if (prev.length <= 2) return prev
      const next = prev.filter((_, i) => i !== idx)
      // Renumber levels
      return next.map((b, i) => ({ ...b, level: i + 1 }))
    })
    setDirty(true)
    setSaveErr(null)
  }, [])

  const handleSave = useCallback(async () => {
    const err = validateBands(bands)
    if (err) { setSaveErr(err); return }
    if (!adminToken) { setSaveErr('Admin token obrigatório'); return }

    const sorted = [...bands].sort((a, b) => a.min - b.min).map((b, i) => ({ ...b, level: i + 1 }))
    setSaving(true); setSaveErr(null)
    try {
      await putConfig('sentiment', 'bands', { bands: sorted }, null, adminToken)
      setBands(sorted)
      setDirty(false)
      reload()
    } catch (e) {
      setSaveErr(String(e))
    } finally {
      setSaving(false)
    }
  }, [bands, adminToken, reload])

  const handleReset = useCallback(() => {
    setBands(DEFAULT_BANDS)
    setDirty(true)
    setSaveErr(null)
  }, [])

  // ── Render ───────────────────────────────────────────────────────────────────

  return (
    <div className="p-6 max-w-xl">
      {/* Header */}
      <div className="flex items-center justify-between mb-1">
        <h2 className="text-sm font-semibold text-gray-900">Faixas de Sentimento</h2>
        <div className="flex items-center gap-2">
          {loading && <Spinner />}
          {error && <span className="text-xs text-red-600">⚠ {error}</span>}
          <button onClick={reload} className="text-xs text-secondary hover:text-primary">↻</button>
        </div>
      </div>
      <p className="text-xs text-gray-500 mb-5">
        Define as faixas numéricas do score de sentimento (-1.0 a +1.0). Os rótulos exibidos
        para operadores são definidos nos arquivos de idioma — aqui apenas os limites numéricos
        e o nível interno (1 = pior, {bands.length} = melhor).
      </p>

      {/* Score visual bar */}
      <div className="mb-5">
        <div className="flex h-4 rounded overflow-hidden">
          {[...bands].sort((a, b) => a.min - b.min).map((b, i) => {
            const pct = ((b.max - b.min) / 2.0) * 100
            const hue = Math.round(((b.level - 1) / (bands.length - 1)) * 120) // red → green
            return (
              <div
                key={i}
                style={{ width: `${pct}%`, backgroundColor: `hsl(${hue},70%,50%)` }}
                title={`Nível ${b.level}: ${b.min} a ${b.max}`}
              />
            )
          })}
        </div>
        <div className="flex justify-between text-[10px] text-gray-400 mt-0.5">
          <span>-1.0</span><span>0</span><span>+1.0</span>
        </div>
      </div>

      {/* Bands list */}
      <div className="space-y-2 mb-4">
        {[...bands].sort((a, b) => a.min - b.min).map((band, i) => (
          <div key={i} className="flex items-center gap-3 bg-gray-50 border border-gray-200 rounded px-3 py-2">
            <div className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold text-white shrink-0"
              style={{ backgroundColor: `hsl(${Math.round((i / (bands.length - 1)) * 120)},70%,45%)` }}>
              {band.level}
            </div>
            <span className="text-xs text-gray-500 w-28 shrink-0">{levelHint(band.level, bands.length)}</span>
            <div className="flex items-center gap-1.5 flex-1">
              <input
                type="number"
                min={-1} max={1} step={0.05}
                value={band.min}
                onChange={e => updateBand(i, 'min', e.target.value)}
                className="w-16 text-xs font-mono px-2 py-1 border border-gray-300 rounded focus:outline-none focus:border-blue-400"
              />
              <span className="text-gray-400 text-xs">→</span>
              <input
                type="number"
                min={-1} max={1} step={0.05}
                value={band.max}
                onChange={e => updateBand(i, 'max', e.target.value)}
                className="w-16 text-xs font-mono px-2 py-1 border border-gray-300 rounded focus:outline-none focus:border-blue-400"
              />
            </div>
            {bands.length > 2 && (
              <button
                onClick={() => removeBand(i)}
                className="text-xs text-red-400 hover:text-red-600 transition-colors shrink-0"
                title="Remover faixa"
              >✕</button>
            )}
          </div>
        ))}
      </div>

      {/* Add band */}
      {bands.length < 6 && (
        <button
          onClick={addBand}
          className="text-xs border border-dashed border-gray-300 rounded px-3 py-1.5 text-gray-500 hover:border-gray-400 hover:text-gray-700 transition-colors mb-4 w-full"
        >
          + Adicionar faixa
        </button>
      )}

      {/* Validation error */}
      {(validationError || saveErr) && (
        <p className="text-xs text-red-600 mb-3">{validationError ?? saveErr}</p>
      )}

      {/* Actions */}
      <div className="flex gap-2">
        <button
          onClick={handleSave}
          disabled={saving || !!validationError || !adminToken || !dirty}
          className="px-3 py-1.5 rounded text-xs font-semibold bg-primary text-white disabled:opacity-40 hover:bg-blue-800 transition-colors"
        >
          {saving ? 'Salvando…' : 'Salvar'}
        </button>
        <button
          onClick={handleReset}
          className="px-3 py-1.5 rounded text-xs border border-gray-300 text-gray-600 hover:text-gray-900 transition-colors"
        >
          ↺ Restaurar padrões
        </button>
        {!adminToken && (
          <span className="text-xs text-amber-600 self-center">⚠ Defina o admin token para salvar</span>
        )}
      </div>
    </div>
  )
}
