/**
 * MaskedToken — renders a masking token from the canonical stream.
 *
 * Token format in stream: [category:tk_xxx:display_partial]
 * e.g. [cpf:tk_b7d2:***-00]  [credit_card:tk_a8f3:****1234]
 *
 * token_display behaviour (from masking config, channel-abstract):
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
import { apiFetch } from '@/api/apiFetch'

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

// ⚠️ Espelho de `MaskingDisplayRule` (@plughub/schemas/audit.ts). A deduplicacao
// exige o platform-ui depender de @plughub/schemas — divida registrada no TODO.md.
// Enquanto for copia, ela ACOMPANHA o canonico (ALW-10, 2026-09-02).

/** Como um TOKEN mascarado aflora. CHANNEL-ABSTRACT: absorveu `display_voice`,
 *  e cada adapter traduz (em voz, `full_mask` vira bipe ou placeholder falado). */
export type TokenDisplayMode = 'display_partial' | 'full_mask' | 'hidden'

/** ECO — a entrada FRESCA do cliente volta, e como. SEM parcial: eco nao
 *  renderiza token, devolve o que a pessoa digitou, e nao ha parcial embutido
 *  para ler. Eco e coisa de INPUT; armazenamento segue o masking padrao. */
export type EchoMode = 'plain' | 'none' | 'masked'

export interface MaskingDisplayRule {
  token_display:     TokenDisplayMode
  echo_to_customer:  EchoMode
  echo_to_operator:  EchoMode
}

export const DEFAULT_DISPLAY_RULE: MaskingDisplayRule = {
  token_display:    'display_partial',
  echo_to_customer: 'none',
  echo_to_operator: 'masked',
}

export type MaskingRulesMap = Record<string, MaskingDisplayRule>

// ── Category metadata ─────────────────────────────────────────────────────────

// ⚠️ Fallback de ÚLTIMA instância. A fonte é o catálogo de tipos
// (`masking.types` no config-api, ver useMaskingDisplayRules); este mapa só serve
// ao primeiro render, antes de o catálogo chegar.
//
// `iban` e `passport` foram REMOVIDOS em 2026-08-26 (fase V2 do arco ALLOWLIST):
// não existem no enum DataCategory, não têm regex de detecção e não têm regra —
// nenhum token jamais pôde chegar aqui com essas categorias. Eram o 7º inventário
// de categoria do repositório, e o único lugar onde os dois "existiam".
const CATEGORY_META: Record<string, { icon: string; label: string }> = {
  credit_card: { icon: '💳', label: 'Cartão' },
  cpf:         { icon: '🪪', label: 'CPF' },
  phone:       { icon: '📞', label: 'Fone' },
  email_addr:  { icon: '📧', label: 'E-mail' },
}

function getCategoryMeta(category: string) {
  return CATEGORY_META[category] ?? { icon: '🔒', label: category }
}

// ── MaskedToken component ─────────────────────────────────────────────────────

interface MaskedTokenProps {
  token: ParsedToken
  /** display context — 'screen' aplica `token_display`; outros canais sao do adapter */
  context?: 'screen'
  /** masking rules map — if omitted, DEFAULT_DISPLAY_RULE is used */
  rules?: MaskingRulesMap
}

export function MaskedToken({ token, context = 'screen', rules }: MaskedTokenProps) {
  const rule: MaskingDisplayRule = rules?.[token.category] ?? DEFAULT_DISPLAY_RULE
  const meta = getCategoryMeta(token.category)

  let displayValue: string | null
  if (context === 'screen') {
    if (rule.token_display === 'hidden')         displayValue = null
    else if (rule.token_display === 'full_mask') displayValue = '•••••'
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
 *
 * Fonte ÚNICA: o CATÁLOGO DE TIPOS (`masking.types` → `type.mascara.display`),
 * declaração única do arco ALLOWLIST.
 *
 * ⚠️ A casa legada — as chaves soltas `rule.{category}`, que a tela gravava antes
 * do catálogo — foi FECHADA na fase V2b (2026-08-29). Não é limpeza cosmética: o
 * leitor legado VENCIA sobre o catálogo, então enquanto ele existisse a mesma
 * pergunta ("como este token aparece?") tinha duas respostas e a que valia não era
 * a declarada. A remoção foi autorizada por medição, não por decreto — o contador
 * que este mesmo hook publicava zerou, e o ramo D do `probe_type_catalog.sh` existia
 * justamente para dizer quando. Medido antes de remover: zero chaves `rule.*` em
 * todo o `platform_config` (todos os tenants, todos os namespaces), contra 8 linhas
 * do namespace `masking` de testemunha de presença. Guarda: `probe_legacy_display_rule_closed.sh`.
 *
 * ⚠️ Corrigido em 2026-08-26: a URL era `/api/config/{tenant}/masking`, caminho que
 * NENHUM proxy do platform-ui serve (a base é `/config` — a ocorrência era única no
 * repositório inteiro). O 404 morria em `r.ok ? … : null` e o resto no `.catch(() => {})`
 * vazio, então o mapa era SEMPRE vazio e toda regra de display por categoria era
 * inerte. Degradação sem log é o que fez isso durar: agora ela NOMEIA o que deixa de
 * valer.
 */
export function useMaskingDisplayRules(): MaskingRulesMap {
  const { tenantId } = useAuth()
  const [rules, setRules] = useState<MaskingRulesMap>({})

  useEffect(() => {
    if (!tenantId) return
    apiFetch(`/config/masking?tenant_id=${encodeURIComponent(tenantId)}`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((body: { entries?: Record<string, unknown> } | null) => {
        const entries = body?.entries
        if (!entries || typeof entries !== 'object') {
          console.warn('[masking] resposta sem .entries — regras de display por categoria NÃO aplicadas; vale o default para todo token')
          return
        }
        const unwrap = (v: unknown): unknown =>
          (v !== null && typeof v === 'object' && 'value' in (v as object))
            ? (v as { value: unknown }).value
            : v

        // Fonte ÚNICA: o catálogo de tipos (`masking.types` → `type.mascara.display`).
        const map: MaskingRulesMap = {}
        const catalog = unwrap(entries['types']) as { types?: Array<{ id?: string; mascara?: { display?: unknown } }> } | undefined
        for (const t of catalog?.types ?? []) {
          const display = t?.mascara?.display
          if (t?.id && display && typeof display === 'object') {
            map[t.id] = display as MaskingDisplayRule
          }
        }

        if (Object.keys(map).length === 0) {
          console.warn('[masking] catálogo masking.types ausente, vazio ou sem `mascara.display` — NENHUMA regra de display por categoria aplicada; vale DEFAULT_DISPLAY_RULE para todo token')
        }
        setRules(map)
      })
      .catch(e => {
        console.warn(`[masking] falha ao ler o catálogo (${String(e)}) — regras de display por categoria NÃO aplicadas; vale o default para todo token`)
      })
  }, [tenantId])

  return rules
}
