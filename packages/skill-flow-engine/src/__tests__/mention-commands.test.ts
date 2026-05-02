/**
 * mention-commands.test.ts
 * Unit tests for the @mention command processor.
 * Spec: docs/guias/mention-protocol.md
 *
 * Covered:
 *   1.  parseCommandName — extracts first token from args_raw
 *   2.  handleMentionCommand — unknown command → silently ignored
 *   3.  set_context — writes to ContextStore, acknowledge:true
 *   4.  set_context — acknowledge:false (no ack)
 *   5.  set_context — multiple fields written to ContextStore
 *   6.  set_context — contextStore absent (graceful degradation)
 *   7.  set_context — contextStore.set throws (non-fatal, returns handled:true)
 *   8.  trigger_step — returns step_id, acknowledge:false
 *   9.  trigger_step — returns step_id, acknowledge:true
 *   10. terminate_self — returns terminate_self:true, acknowledge:false
 *   11. mention_commands absent on skill — unknown → ignored
 */

import { describe, it, expect, vi } from "vitest"
import { handleMentionCommand, parseCommandName } from "../mention-commands"
import type { Skill }                             from "@plughub/schemas"
import type { IContextStore }                     from "../context-types"

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────

function makeContextStore(setImpl?: () => Promise<void>): IContextStore {
  return {
    set:         vi.fn().mockImplementation(setImpl ?? (() => Promise.resolve())),
    get:         vi.fn(),
    getValue:    vi.fn(),
    getAll:      vi.fn(),
    getByPrefix: vi.fn(),
    getMissing:  vi.fn(),
    delete:      vi.fn(),
    clearSession: vi.fn(),
  } as unknown as IContextStore
}

function makeCtx(store?: IContextStore) {
  return {
    sessionId:    "sess_test",
    tenantId:     "tenant_demo",
    contextStore: store,
  }
}

// Minimal skill with mention_commands
const skillWithCommands: Pick<Skill, "mention_commands"> = {
  mention_commands: {
    ativa: {
      action:      { set_context: { "session.copilot.mode": "active" } },
      acknowledge: true,
    },
    pausa: {
      action:      { set_context: { "session.copilot.mode": "passive" } },
      acknowledge: false,
    },
    multi: {
      action:      { set_context: { "session.a": "1", "session.b": "2" } },
      acknowledge: false,
    },
    goto: {
      action:      { trigger_step: "analise_step" },
      acknowledge: false,
    },
    goto_ack: {
      action:      { trigger_step: "responder_step" },
      acknowledge: true,
    },
    para: {
      action:      { terminate_self: true as const },
      acknowledge: false,
    },
  },
}

// ─────────────────────────────────────────────
// parseCommandName
// ─────────────────────────────────────────────

describe("parseCommandName", () => {
  it("extracts the first word from args_raw", () => {
    expect(parseCommandName("ativa")).toBe("ativa")
  })

  it("extracts first word when there are more tokens", () => {
    expect(parseCommandName("ativa conta=123 motivo=x")).toBe("ativa")
  })

  it("returns null for empty string", () => {
    expect(parseCommandName("")).toBeNull()
  })

  it("returns null for whitespace-only string", () => {
    expect(parseCommandName("   ")).toBeNull()
  })

  it("trims leading whitespace before extracting", () => {
    expect(parseCommandName("  pausa  ")).toBe("pausa")
  })
})

// ─────────────────────────────────────────────
// handleMentionCommand
// ─────────────────────────────────────────────

describe("handleMentionCommand", () => {

  // ── 2. Unknown command ────────────────────────────────────────────────────

  it("silently ignores an unknown command", async () => {
    const store = makeContextStore()
    const r = await handleMentionCommand(skillWithCommands, "desconhecido", makeCtx(store))

    expect(r.handled).toBe(false)
    expect(r.acknowledge).toBe(false)
    expect(r.terminate_self).toBe(false)
    expect(store.set).not.toHaveBeenCalled()
  })

  // ── 3. set_context with acknowledge:true ─────────────────────────────────

  it("set_context: writes to ContextStore and returns acknowledge:true", async () => {
    const store = makeContextStore()
    const r = await handleMentionCommand(skillWithCommands, "ativa", makeCtx(store))

    expect(r.handled).toBe(true)
    expect(r.acknowledge).toBe(true)
    expect(r.terminate_self).toBe(false)
    expect(r.trigger_step).toBeUndefined()
    expect(store.set).toHaveBeenCalledOnce()
    expect(store.set).toHaveBeenCalledWith(
      "sess_test",
      "session.copilot.mode",
      expect.objectContaining({ value: "active", confidence: 1.0, source: "mention_command:ativa" }),
    )
  })

  // ── 4. set_context with acknowledge:false ────────────────────────────────

  it("set_context: returns acknowledge:false when not configured", async () => {
    const store = makeContextStore()
    const r = await handleMentionCommand(skillWithCommands, "pausa", makeCtx(store))

    expect(r.handled).toBe(true)
    expect(r.acknowledge).toBe(false)
    expect(store.set).toHaveBeenCalledWith(
      "sess_test",
      "session.copilot.mode",
      expect.objectContaining({ value: "passive" }),
    )
  })

  // ── 5. set_context — multiple fields ─────────────────────────────────────

  it("set_context: writes all fields in the set_context map", async () => {
    const store = makeContextStore()
    const r = await handleMentionCommand(skillWithCommands, "multi", makeCtx(store))

    expect(r.handled).toBe(true)
    expect(store.set).toHaveBeenCalledTimes(2)
    const calls = (store.set as ReturnType<typeof vi.fn>).mock.calls
    expect(calls[0]![1]).toBe("session.a")
    expect(calls[1]![1]).toBe("session.b")
  })

  // ── 6. set_context — contextStore absent ─────────────────────────────────

  it("set_context: handled:true even without contextStore", async () => {
    const r = await handleMentionCommand(skillWithCommands, "ativa", makeCtx(undefined))

    expect(r.handled).toBe(true)
    expect(r.acknowledge).toBe(true)
  })

  // ── 7. set_context — contextStore.set throws ─────────────────────────────

  it("set_context: non-fatal when contextStore.set throws", async () => {
    const store = makeContextStore(() => { throw new Error("Redis error") })
    const r = await handleMentionCommand(skillWithCommands, "ativa", makeCtx(store))

    // Must still return handled:true — ContextStore errors never abort the handler
    expect(r.handled).toBe(true)
    expect(r.acknowledge).toBe(true)
  })

  // ── 8. trigger_step — acknowledge:false ──────────────────────────────────

  it("trigger_step: returns the step_id for the caller to act on", async () => {
    const store = makeContextStore()
    const r = await handleMentionCommand(skillWithCommands, "goto", makeCtx(store))

    expect(r.handled).toBe(true)
    expect(r.acknowledge).toBe(false)
    expect(r.trigger_step).toBe("analise_step")
    expect(r.terminate_self).toBe(false)
    expect(store.set).not.toHaveBeenCalled()
  })

  // ── 9. trigger_step — acknowledge:true ───────────────────────────────────

  it("trigger_step: returns acknowledge:true when configured", async () => {
    const r = await handleMentionCommand(skillWithCommands, "goto_ack", makeCtx())

    expect(r.handled).toBe(true)
    expect(r.acknowledge).toBe(true)
    expect(r.trigger_step).toBe("responder_step")
  })

  // ── 10. terminate_self ────────────────────────────────────────────────────

  it("terminate_self: returns terminate_self:true for the caller to act on", async () => {
    const r = await handleMentionCommand(skillWithCommands, "para", makeCtx())

    expect(r.handled).toBe(true)
    expect(r.terminate_self).toBe(true)
    expect(r.trigger_step).toBeUndefined()
  })

  // ── 11. mention_commands absent on skill ──────────────────────────────────

  it("silently ignores command when skill has no mention_commands", async () => {
    const emptySkill: Pick<Skill, "mention_commands"> = {}
    const r = await handleMentionCommand(emptySkill, "qualquer", makeCtx())

    expect(r.handled).toBe(false)
  })
})
