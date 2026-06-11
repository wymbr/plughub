/**
 * assignment-filter.test.ts
 * Unit tests for lib/assignment-filter.ts (targeted-assignment WS filter).
 *
 * Regression for the multi-agent bug: conversation.assigned is published to the
 * pool-wide channel, so without filtering two agents in the same pool both saw
 * the same contact. shouldDropAssignment drops events targeting a different agent.
 */

import { describe, it, expect } from "vitest"
import { shouldDropAssignment } from "../lib/assignment-filter"

describe("shouldDropAssignment", () => {
  const ME = "human-u-admin"

  it("drops an assignment targeting another agent in the same pool", () => {
    expect(shouldDropAssignment("conversation.assigned", "human-u-operator", ME)).toBe(true)
  })

  it("keeps an assignment targeting THIS agent", () => {
    expect(shouldDropAssignment("conversation.assigned", ME, ME)).toBe(false)
  })

  it("keeps when expectedInstanceId is empty (legacy client, no user_id)", () => {
    expect(shouldDropAssignment("conversation.assigned", "human-u-operator", "")).toBe(false)
  })

  it("keeps (defensive) when the event has no target instance_id", () => {
    expect(shouldDropAssignment("conversation.assigned", "", ME)).toBe(false)
    expect(shouldDropAssignment("conversation.assigned", undefined, ME)).toBe(false)
    expect(shouldDropAssignment("conversation.assigned", null, ME)).toBe(false)
  })

  it("never drops non-assignment events (session.closed, message.text, …)", () => {
    expect(shouldDropAssignment("session.closed", "human-u-operator", ME)).toBe(false)
    expect(shouldDropAssignment("message.text", "human-u-operator", ME)).toBe(false)
    expect(shouldDropAssignment(undefined, "human-u-operator", ME)).toBe(false)
  })
})
