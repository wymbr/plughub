/**
 * mention-commands.ts
 * Processes @mention commands for specialist agents running inside the Skill Flow Engine.
 * Spec: docs/guias/mention-protocol.md
 *
 * Design:
 *   When a human agent sends "@copilot ativa", routeMentions (mcp-server-plughub/session.ts)
 *   publishes a conversations.inbound event with mention_routing:true to the target pool.
 *   The orchestrator bridge receives it, looks up the active skill for that instance,
 *   and calls this handler.
 *
 *   Three action types:
 *     set_context    — writes fields to ContextStore (fire-and-forget)
 *     trigger_step   — returns step ID; caller interrupts the running engine via
 *                      LPUSH to menu:result:{sessionId} with special payload
 *     terminate_self — returns flag; caller calls agent_done to leave the conference
 *
 *   Unknown commands are ignored silently (spec requirement).
 *   Acknowledgment messages (acknowledge:true) are the caller's responsibility
 *   (notification_send with visibility agents_only).
 */

import type { MentionCommand, Skill }  from "@plughub/schemas"
import type { IContextStore }          from "./context-types"

// ─────────────────────────────────────────────
// Public types
// ─────────────────────────────────────────────

export interface MentionCommandContext {
  sessionId:    string
  tenantId:     string
  contextStore: IContextStore | undefined
}

export interface MentionCommandResult {
  /** true when the command was recognised — false means silently ignored */
  handled:        boolean
  /** Caller should send an agents_only acknowledgment message */
  acknowledge:    boolean
  /**
   * Set when action.trigger_step is declared.
   * Caller publishes LPUSH menu:result:{sessionId} with payload:
   *   JSON.stringify({ _mention_trigger_step: trigger_step })
   * The menu step (or the engine) detects this prefix and jumps to the step.
   */
  trigger_step?:  string
  /** Caller should invoke agent_done to leave the conference */
  terminate_self: boolean
}

// ─────────────────────────────────────────────
// parseCommandName
// ─────────────────────────────────────────────

/**
 * Extracts the first whitespace-delimited token from args_raw.
 * Returns null for a bare mention with no command (e.g. "@billing").
 *
 * Examples:
 *   "ativa"                       → "ativa"
 *   "ativa conta=123"             → "ativa"
 *   ""                            → null
 */
export function parseCommandName(args_raw: string): string | null {
  const trimmed = args_raw.trim()
  if (!trimmed) return null
  return trimmed.split(/\s+/)[0] ?? null
}

// ─────────────────────────────────────────────
// handleMentionCommand
// ─────────────────────────────────────────────

/**
 * Processes a single @mention command for a specialist agent.
 *
 * @param skill        - The agent's skill definition (only mention_commands used)
 * @param commandName  - The command token parsed from args_raw (e.g. "ativa")
 * @param ctx          - Minimal session context needed for ContextStore writes
 *
 * For set_context actions, writes to ContextStore synchronously before returning.
 * For trigger_step and terminate_self, returns the relevant field for the caller
 * to act on — this function does not perform the actual flow interrupt or agent_done.
 *
 * Fire-and-forget pattern: caller should not throw on ContextStore errors.
 */
export async function handleMentionCommand(
  skill:       Pick<Skill, "mention_commands">,
  commandName: string,
  ctx:         MentionCommandContext,
): Promise<MentionCommandResult> {

  const commands = skill.mention_commands ?? {}
  const cmd: MentionCommand | undefined = commands[commandName]

  if (!cmd) {
    // Unknown command — silently ignored (spec: "Comandos não reconhecidos são ignorados silenciosamente")
    return { handled: false, acknowledge: false, terminate_self: false }
  }

  const action = cmd.action

  // ── set_context — write fields to ContextStore ────────────────────────────
  if ("set_context" in action) {
    if (ctx.contextStore) {
      for (const [tag, value] of Object.entries(action.set_context)) {
        try {
          await ctx.contextStore.set(ctx.sessionId, tag, {
            value,
            confidence: 1.0,
            source:     `mention_command:${commandName}`,
            visibility: "agents_only",
          })
        } catch {
          // Non-fatal — ContextStore write failure never aborts mention handling
        }
      }
    }
    return { handled: true, acknowledge: cmd.acknowledge, terminate_self: false }
  }

  // ── trigger_step — return step_id for the caller to act on ────────────────
  if ("trigger_step" in action) {
    return {
      handled:        true,
      acknowledge:    cmd.acknowledge,
      trigger_step:   action.trigger_step,
      terminate_self: false,
    }
  }

  // ── terminate_self — caller invokes agent_done ─────────────────────────────
  if ("terminate_self" in action) {
    return { handled: true, acknowledge: cmd.acknowledge, terminate_self: true }
  }

  // Discriminated union exhaustiveness guard
  return { handled: false, acknowledge: false, terminate_self: false }
}
