/**
 * lib/mention-parser.ts
 * Parses @alias mention tokens from agent messages.
 * Spec: docs/guias/mention-protocol.md
 *
 * Supported syntax:
 *   @billing
 *   @billing conta=@ctx.caller.account_id
 *   @copilot cliente tem plano @ctx.caller.plano_atual|"não identificado"
 *   @billing @suporte analise o contexto   ← multi-mention
 *
 * Distinction:
 *   @alias         — starts with letter or underscore, NO following dot
 *   @ctx.namespace.field  — namespace reference, has dot after first token
 *
 * Rules:
 *   - @alias must be at the start of the string or preceded by whitespace
 *   - @ctx.* tokens inside args are extracted as context references (not aliases)
 *   - Unresolved @ctx.* references fall back to inline default: @ctx.field|"default"
 */

// ─────────────────────────────────────────────
// Public types
// ─────────────────────────────────────────────

export interface CtxRef {
  /** Full dotted path, e.g. "caller.account_id" */
  field:    string
  /** Inline fallback value if the ContextStore entry is absent */
  fallback: string
}

export interface ParsedMention {
  /** Alias without the @ prefix, e.g. "billing" */
  alias:     string
  /** Raw argument string following this @alias (unparsed) */
  args_raw:  string
  /** @ctx.* references extracted from args_raw */
  ctx_refs:  CtxRef[]
}

export interface MentionParseResult {
  mentions:      ParsedMention[]
  has_mentions:  boolean
  /**
   * Message text after stripping @alias tokens and their structured args.
   * Free prose (unstructured text following a mention) is preserved.
   */
  stripped_text: string
}

// ─────────────────────────────────────────────
// Core parser
// ─────────────────────────────────────────────

/**
 * Matches @alias tokens that are NOT context references (@ctx.*).
 *
 * (?<!\S)       — preceded by whitespace or start-of-string
 * (?!\w|\.)     — NOT followed by a word character or dot
 *                 This prevents backtracking that would allow @ctx.field to match
 *                 as @ct (the engine would otherwise accept `ct` because `x` ≠ dot).
 *                 With (?!\w|\.), the regex fails for ANY suffix that continues the
 *                 word or adds a namespace dot, making @ctx.field cleanly unmatchable.
 */
const ALIAS_RE = /(?<!\S)@([A-Za-z_][A-Za-z0-9_]*)(?!\w|\.)/g

/**
 * Matches @ctx.namespace.field with optional |"fallback".
 * Group 1 = dotted path, Group 2 = fallback (may be undefined).
 */
const CTX_REF_RE = /@ctx\.([A-Za-z][A-Za-z0-9_.]*)(?:\|"([^"]*)")?/g

/**
 * parseMentions — extracts @alias tokens from a message text.
 *
 * Returns an ordered list of mentions (left-to-right), each with its
 * raw arg string and extracted @ctx.* references.
 *
 * Does NOT resolve ContextStore values — callers must do that using ctx_refs.
 */
export function parseMentions(text: string): MentionParseResult {
  const positions: Array<{ alias: string; start: number; tokenEnd: number }> = []

  for (const m of text.matchAll(ALIAS_RE)) {
    positions.push({
      alias:    m[1]!,
      start:    m.index!,
      tokenEnd: m.index! + m[0].length,
    })
  }

  if (positions.length === 0) {
    return { mentions: [], has_mentions: false, stripped_text: text }
  }

  // For each @alias, args_raw is the text between this token's end and the
  // next @alias token (or the end of the string).
  const mentions: ParsedMention[] = positions.map((pos, i) => {
    const nextStart = i + 1 < positions.length ? positions[i + 1]!.start : text.length
    const args_raw  = text.slice(pos.tokenEnd, nextStart).trim()
    return {
      alias:    pos.alias,
      args_raw,
      ctx_refs: extractCtxRefs(args_raw),
    }
  })

  // stripped_text: any text before the first @alias, plus free prose from each
  // mention's args (i.e. args with @ctx.* refs and key=value pairs removed).
  const prefix        = text.slice(0, positions[0]!.start).trim()
  const strippedParts = prefix ? [prefix] : []

  for (const m of mentions) {
    const free = m.args_raw
      .replace(CTX_REF_RE, "")                  // remove @ctx.* refs
      .replace(/\b[A-Za-z_]\w*=\S+/g, "")       // remove key=value args
      .trim()
    if (free) strippedParts.push(free)
  }

  return {
    mentions,
    has_mentions: true,
    stripped_text: strippedParts.join(" ").trim() || text,
  }
}

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────

function extractCtxRefs(args_raw: string): CtxRef[] {
  // Reset lastIndex before each call (global regex is stateful)
  CTX_REF_RE.lastIndex = 0
  const refs: CtxRef[] = []
  for (const m of args_raw.matchAll(new RegExp(CTX_REF_RE.source, "g"))) {
    refs.push({ field: m[1]!, fallback: m[2] ?? "" })
  }
  return refs
}
