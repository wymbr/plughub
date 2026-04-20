/**
 * infra/injection_guard.ts
 * Heuristic prompt injection / input anomaly detector.
 * Spec: PlugHub v24.0 section 9.5
 *
 * Applied in the MCP interceptor (PlugHubAdapter / proxy sidecar) BEFORE
 * dispatching any tool call input to a domain MCP Server.
 *
 * The guard does NOT replace semantic validation by the LLM or the tool schema
 * validation by Zod — it is an additional, cheap heuristic layer that catches
 * the most common prompt injection patterns before they reach downstream systems.
 *
 * Detection strategy:
 *   1. Recursively stringify the tool input (all string values, keys, nested objects).
 *   2. Match against a list of heuristic regex patterns.
 *   3. Return an InjectionDetection result — never throws.
 *      The caller decides whether to block or log.
 *
 * Design decisions:
 *   - Patterns are tested against the FULL stringified payload, not just top-level strings,
 *     so nested injection attempts (e.g. inside a JSON body field) are also caught.
 *   - The guard is intentionally conservative: false positives are preferable to
 *     missed injections in a multi-tenant platform.
 *   - Only ASCII-normalised comparisons — no Unicode normalisation (to avoid bypasses
 *     via homoglyphs, add Unicode normalisation in a future iteration).
 */

// ─────────────────────────────────────────────
// Pattern catalogue
// ─────────────────────────────────────────────

interface InjectionPattern {
  id:          string
  regex:       RegExp
  severity:    "low" | "medium" | "high"
  description: string
}

export const INJECTION_PATTERNS: InjectionPattern[] = [
  {
    id:          "override_instructions",
    regex:       /ignore\s+(previous|all|prior|above)\s+(instructions?|directives?|commands?|prompts?)/i,
    severity:    "high",
    description: "Classic override: instructs the model to ignore prior instructions",
  },
  {
    id:          "role_hijack",
    regex:       /you\s+are\s+now\s+(a|an|acting\s+as|playing|assuming\s+the\s+role)/i,
    severity:    "high",
    description: "Role hijack: attempts to redefine the model's identity",
  },
  {
    id:          "forget_previous",
    regex:       /forget\s+(your|all|previous|everything|the\s+above)/i,
    severity:    "high",
    description: "Memory wipe pattern: instructs the model to forget previous context",
  },
  {
    id:          "new_instructions_header",
    regex:       /\bnew\s+(instructions?|directives?|task|objective)\s*:/i,
    severity:    "high",
    description: "Header pattern used to inject new instructions mid-input",
  },
  {
    id:          "disregard_pattern",
    regex:       /disregard\s+(previous|your|all|the\s+above|instructions?|rules?)/i,
    severity:    "high",
    description: "Synonym for 'ignore previous instructions'",
  },
  {
    id:          "pretend_persona",
    regex:       /pretend\s+(you\s+are|to\s+be|that\s+you\s+are)/i,
    severity:    "medium",
    description: "Persona injection via pretend construct",
  },
  {
    id:          "act_as_persona",
    regex:       /act\s+as\s+(if\s+you\s+are|though\s+you\s+are|a\s+different|an?\s+)/i,
    severity:    "medium",
    description: "Persona injection via 'act as' construct",
  },
  {
    id:          "system_prompt_leak",
    regex:       /\bsystem\s+prompt\b|\bsystem\s+message\b/i,
    severity:    "medium",
    description: "Attempts to reference or extract the system prompt",
  },
  {
    id:          "override_behavior",
    regex:       /override\s+(your\s+)?(instructions?|behavior|responses?|safety|restrictions?)/i,
    severity:    "high",
    description: "Explicit override of model behavior or safety restrictions",
  },
  {
    id:          "injection_keyword",
    regex:       /\bprompt\s+injection\b|\bjailbreak\b|\bdan\s+mode\b/i,
    severity:    "high",
    description: "Explicit injection-related keywords",
  },
  {
    id:          "developer_mode",
    regex:       /developer\s+mode\s+(enabled|on|activated)/i,
    severity:    "medium",
    description: "Fake 'developer mode' activation pattern",
  },
  {
    id:          "simulate_unrestricted",
    regex:       /simulate\s+(being\s+)?(an?\s+)?(unrestricted|unfiltered|uncensored|jailbroken)/i,
    severity:    "high",
    description: "Simulating an unrestricted version of the model",
  },
  {
    id:          "do_anything_now",
    regex:       /do\s+anything\s+now|DAN\b/,
    severity:    "high",
    description: "DAN (Do Anything Now) jailbreak pattern",
  },
]

// ─────────────────────────────────────────────
// Detection result
// ─────────────────────────────────────────────

export type InjectionDetection =
  | { detected: false }
  | {
      detected:    true
      pattern_id:  string
      severity:    "low" | "medium" | "high"
      description: string
      matched:     string   // the portion of input that matched
    }

// ─────────────────────────────────────────────
// Core detection function
// ─────────────────────────────────────────────

/**
 * Scans a tool input for prompt injection patterns.
 * Recursively stringifies the input and tests each pattern.
 *
 * @returns InjectionDetection — detected=false if clean, detected=true with details otherwise.
 */
export function detectInjection(input: unknown): InjectionDetection {
  const haystack = stringify(input)

  for (const pattern of INJECTION_PATTERNS) {
    const match = pattern.regex.exec(haystack)
    if (match) {
      return {
        detected:    true,
        pattern_id:  pattern.id,
        severity:    pattern.severity,
        description: pattern.description,
        matched:     match[0],
      }
    }
  }

  return { detected: false }
}

/**
 * Convenience wrapper: throws if injection is detected.
 * Use this at tool dispatch points to block the call.
 *
 * @param toolName  Name of the tool being called (for error message context)
 * @param input     Tool input object to scan
 * @throws Error with code INJECTION_DETECTED if a pattern matches
 */
export function assertNoInjection(toolName: string, input: unknown): void {
  const result = detectInjection(input)
  if (result.detected) {
    const err = new Error(
      `[injection_guard] Tool '${toolName}': ${result.description} ` +
      `(pattern: ${result.pattern_id}, severity: ${result.severity}, matched: "${result.matched}")`
    )
    ;(err as Error & { code: string }).code = "INJECTION_DETECTED"
    throw err
  }
}

// ─────────────────────────────────────────────
// Internal helpers
// ─────────────────────────────────────────────

/**
 * Recursively extracts all string content from an unknown value.
 * Objects are traversed depth-first; keys and values are both included.
 */
function stringify(value: unknown, depth = 0): string {
  if (depth > 8) return ""  // guard against deeply nested malicious payloads
  if (typeof value === "string")  return value
  if (typeof value === "number" || typeof value === "boolean") return String(value)
  if (Array.isArray(value)) {
    return value.map(v => stringify(v, depth + 1)).join(" ")
  }
  if (value !== null && typeof value === "object") {
    const parts: string[] = []
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      parts.push(k, stringify(v, depth + 1))
    }
    return parts.join(" ")
  }
  return ""
}
