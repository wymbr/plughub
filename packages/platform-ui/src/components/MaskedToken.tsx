/**
 * MaskedToken — renders a masking token from the canonical stream.
 *
 * Token format in stream: [category:tk_xxx:display_partial]
 * e.g. [cpf:tk_b7d2:***-00]  [credit_card:tk_a8f3:****1234]
 *
 * display_screen behaviour (from masking config):
 *   display_partial — show the display_partial value embedded in the token (default)
 *   full_mask       — always show •••••  regardless of display_partial
 *   hidden          — show only the category label, no value
 *
 * Usage:
 *   renderWithTokens(text, rules) — splits raw text by token pattern and
 *   returns React nodes with tokens replaced by <MaskedToken> chips.
 */
import React from 'react'
import { useEffect, useState } from 'react'
import { useAuth } from '@/auth/useAuth'

// ── Token regex ────────────────────────────────────────────────────────────────

export const TOKEN_RE = /\[([\w_]+):(tk_[a-f0-9]+):([^\]]+)\]/g

export interface ParsedToken {
  full:         string   // the full token string including brackets
  category:     string   // e.g. "cpf"
  token_id:     string   // e.g. "tk_b7d2"
  display:      string   // e.g. "***-00"
}

export function parseToken(raw: string): ParsedToken | null {
  const re = /^\[([\w_]+):(tk_[a-f0-9]+):([^\]]+)\]$/
  const m = raw.match(re)
  if (!m) return null
  return { full: raw, category: m[1]!, token_id: m[2]!, display: m[3]! }
}

// ── Display rule types ─────────────────────────────────────────────────────────

export type DisplayScreen = 'display_partial' | 'full_mask' | 'hidden'
export type DisplayVoice  = 'beep' | 'silence' | 'speak_placeholder'

export interface MaskingDisplayRule {
  display_screen:    DisplayScreen
  display_voice:     DisplayVoice
  echo_to_customer:  boolean
  echo_to_operator:  boolean
}

export const DEFAULT_DISPLAY_RULE: MaskingDisplayRule = {
  display_screen:   'display_partial',
  display_voice:    'silence',
  echo_to_customer: false,
  echo_to_operator: true,
}

export type MaskingRulesMap = Record<string, MaskingDisplayRule>

// ── Category metadata ─────────────────────────────────────────────────────────

const CATEGORY_META: Record<string, { icon: string; label: string }> = {
  credit_card: { icon: '💳', label: 'Cartão' },
  cpf:         { icon: '🪪', label: 'CPF' },
  phone:       { icon: '📞', label: 'Fone' },
  email_addr:  { icon: '📧', label: 'E-mail' },
  iban:        { icon: '🏦', label: 'IBAN' },
  passport:    { icon: '🛂', label: 'Passaporte' },
}

function getCategoryMeta(category: string) {
  return CATEGORY_META[category] ?? { icon: '🔒', label: category }
}

// ── MaskedToken component ─────────────────────────────────────────────────────

interface MaskedTokenProps {
  token: ParsedToken
  /** display context — 'screen' drives display_screen rule */
  context?: 'screen'
  /** masking rules map — if omitted, DEFAULT_DISPLAY_RULE is used */
  rules?: MaskingRulesMap
}

export function MaskedToken({ token, context = 'screen', rules }: MaskedTokenProps) {
  const rule: MaskingDisplayRule = rules?.[token.category] ?? DEFAULT_DISPLAY_RULE
  const meta = getCategoryMeta(token.category)

  let displayValue: string | null
  if (context === 'screen') {
    if (rule.display_screen === 'hidden')       displayValue = null
    else if (rule.display_screen === 'full_mask') displayValue = '•••••'
    else                                         displayValue = token.display // display_partial
  } else {
    displayValue = token.display
  }

  return (
    <span
      title={`${meta.label} — token: ${token.token_id}`}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 3,
        padding: '1px 6px', borderRadius: 10,
        fontSize: '0.85em', fontWeight: 600,
        backgroundColor: '#1e3a5f',
        border: '1px solid #3b82f644',
        color: '#93c5fd',
        verticalAlign: 'baseline',
        cursor: 'default',
        userSelect: 'none',
        whiteSpace: 'nowrap',
      }}
    >
      <span style={{ fontSize: '0.9em' }}>{meta.icon}</span>
      <span style={{ opacity: 0.75, fontWeight: 400, fontSize: '0.85em' }}>{meta.label}</span>
      {displayValue !== null && (
        <code style={{
          fontSize: '0.9em', fontWeight: 700,
          color: '#7dd3fc',
          fontFamily: 'monospace',
          letterSpacing: '0.05em',
        }}>
          {displayValue}
        </code>
      )}
    </span>
  )
}

// ── renderWithTokens ──────────────────────────────────────────────────────────

/**
 * Splits `text` by masking token pattern and returns an array of React nodes:
 * plain strings and <MaskedToken> elements interleaved.
 *
 * @param text    raw message text that may contain [category:tk_xxx:display] tokens
 * @param rules   optional map of display rules per category
 * @param context rendering context — 'screen' (default)
 */
export function renderWithTokens(
  text: string,
  rules?: MaskingRulesMap,
  context: 'screen' = 'screen',
): React.ReactNode[] {
  if (!text) return []

  const tokenRe = /\[([\w_]+):(tk_[a-f0-9]+):([^\]]+)\]/g
  const nodes: React.ReactNode[] = []
  let last = 0
  let m: RegExpExecArray | null

  // eslint-disable-next-line no-cond-assign
  while ((m = tokenRe.exec(text)) !== null) {
    if (m.index > last) {
      nodes.push(text.slice(last, m.index))
    }
    const tok: ParsedToken = {
      full:     m[0]!,
      category: m[1]!,
      token_id: m[2]!,
      display:  m[3]!,
    }
    nodes.push(
      <MaskedToken key={`${tok.token_id}-${m.index}`} token={tok} rules={rules} context={context} />
    )
    last = m.index + m[0]!.length
  }

  if (last < text.length) {
    nodes.push(text.slice(last))
  }

  return nodes.length > 0 ? nodes : [text]
}

// ── useMaskingDisplayRules hook ───────────────────────────────────────────────

/**
 * Fetches masking display rules from Config API namespace "masking".
 * Keys follow the pattern rule.{category} → MaskingDisplayRule object.
 * Returns empty map (all defaults) while loading or on error.
 */
export function useMaskingDisplayRules(): MaskingRulesMap {
  const { tenantId } = useAuth()
  const [rules, setRules] = useState<MaskingRulesMap>({})

  useEffect(() => {
    if (!tenantId) return
    fetch(`/api/config/${tenantId}/masking`)
      .then(r => r.ok ? r.json() : null)
      .then((data: Record<string, { value?: unknown }> | null) => {
        if (!data) return
        const map: MaskingRulesMap = {}
        for (const [key, entry] of Object.entries(data)) {
          if (!key.startsWith('rule.')) continue
          const category = key.slice('rule.'.length)
          const v = entry?.value ?? entry
          if (v && typeof v === 'object') {
            map[category] = v as MaskingDisplayRule
          }
        }
        setRules(map)
      })
      .catch(() => { /* keep empty map — defaults apply */ })
  }, [tenantId])

  return rules
}
