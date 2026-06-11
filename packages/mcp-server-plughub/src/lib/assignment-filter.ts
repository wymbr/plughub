/**
 * assignment-filter.ts
 * Targeted-assignment filter for the Agent Assist WebSocket.
 *
 * `conversation.assigned` is published to the pool-wide channel
 * `pool:events:{poolId}`, so EVERY agent connected to a pool receives it. But a
 * contact is routed to exactly ONE instance (registerHumanAgent registers the
 * human as `human-{userId}` and the Routing Engine allocates that instance into
 * the event's `instance_id`). Without filtering, two agents in the same pool
 * (e.g. admin + operator) would both see — and serve — the same contact.
 *
 * `shouldDropAssignment` decides whether the WS connection must DROP an event
 * because it targets a different agent. It is intentionally conservative
 * (backward-compatible): it never drops anything it is not sure about.
 */

/**
 * @param eventType          the event `type` field
 * @param eventInstanceId    the event `instance_id` field (the routed target)
 * @param expectedInstanceId this connection's own instance ("human-{userId}"), or
 *                           "" for legacy clients that did not send a user_id
 * @returns true when the event must NOT be forwarded to this connection.
 *
 * Never drops when:
 *   - expectedInstanceId is "" (legacy/unknown identity) → preserve old behaviour;
 *   - the event is not a conversation.assigned;
 *   - the event carries no target instance_id (defensive — never over-filter).
 */
export function shouldDropAssignment(
  eventType:          unknown,
  eventInstanceId:    unknown,
  expectedInstanceId: string,
): boolean {
  if (!expectedInstanceId) return false
  if (eventType !== "conversation.assigned") return false
  const target = typeof eventInstanceId === "string" ? eventInstanceId : ""
  return target !== "" && target !== expectedInstanceId
}
