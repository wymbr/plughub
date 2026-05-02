# mcp-server-plughub — Platform MCP Server

## What it is

The MCP Server that exposes PlugHub Platform tools to two types of consumers:
- **External consumers** (BPM, business systems): conversation tools
- **Internal consumers** (agents during service): Agent Runtime tools

## Transport

SSE (Server-Sent Events) over HTTP.
Multiple simultaneous consumers — does not use stdio.
Default port: 3100.

## Exposed tools

### BPM (external consumers)
- `conversation_start`   — starts a service interaction (spec 9.4)
- `conversation_status`  — queries state (spec 9.4)
- `conversation_end`     — forced close (spec 9.4)
- `rule_dry_run`         — simulates a Rules Engine rule (spec 3.2b)

### Agent Runtime (internal consumers)
- `agent_login`          — registers instance (spec 4.5)
- `agent_ready`          — places in queue (spec 4.5)
- `agent_busy`           — marks as busy (spec 4.5)
- `agent_done`           — signals completion (spec 4.2)
- `agent_logout`         — deregisters instance (spec 4.5)
- `agent_heartbeat`      — renews instance TTL (spec 4.5)
- `insight_register`     — registers insight in session (spec 3.4a)
- `agent_delegate`       — delegates A2A subtask (spec 9.5)

### Supervisor (Agent Assist in human pools)
- `supervisor_state`        — conversation state (spec 3.2a)
- `supervisor_capabilities` — available capabilities (spec 3.2a)
- `agent_join_conference`   — joins AI+human conference (spec 3.2a)

## Invariants

- Never implement business logic — only receive and route
- Every tool validates input with Zod before processing
- Every tool authenticates via JWT in the Authorization header
- session_id is mandatory in all Agent Runtime tools
- tenant_id is inferred from the JWT — never from the request body

## Dependencies

- `@plughub/schemas` — data contracts
- `@modelcontextprotocol/sdk` — official Anthropic MCP SDK
- `zod` — input validation

## REST endpoints (Agent Assist bridge)

In addition to MCP tools, the server exposes HTTP REST endpoints consumed by the
agent-assist-ui via Vite proxy (`/api → :3100`):

| Method | Path | Description |
|---|---|---|
| GET  | `/supervisor_state/:sessionId`    | Live session AI state from Redis |
| GET  | `/supervisor_capabilities/:sessionId` | Suggested agents and escalations |
| POST | `/agent_done/:sessionId`          | Human agent signals end of session |
| GET  | `/health`                         | Service healthcheck |

### `/agent_done` and conference handling

When a human agent closes their session, the UI calls `POST /agent_done/:sessionId`.
This endpoint must publish a `contact_closed` event to Kafka so the Orchestrator Bridge
can restore that agent's Routing Engine instance and update the conference participant SET.

**The `instance_id` problem**: the REST endpoint only knows `session_id`. It does not
know which Routing Engine instance the agent occupied. The Orchestrator Bridge solves
this by writing the `instance_id` to `session:{sessionId}:meta` in Redis when it
activates a human agent. The REST endpoint reads it back:

```typescript
const metaRaw = await redis.get(`session:${sessionId}:meta`)
const instanceId = JSON.parse(metaRaw)["instance_id"] ?? ""

await kafka.publish("conversations.events", {
  event_type:  "contact_closed",
  session_id:  sessionId,
  instance_id: instanceId,  // bridge uses this for per-instance restore
  reason:      "agent_closed",
})
```

The `reason: "agent_closed"` value is the discriminator the bridge uses to classify
this event as `customer_side=False` — meaning only one agent left, the conversation
continues. If `instance_id` is empty (meta key expired or missing), the bridge
degrades gracefully: no instance is restored, but the conference continues.

See `packages/orchestrator-bridge/CLAUDE.md` for the full `contact_closed` reason
taxonomy and conference handling logic.

## agent_join_conference — conference invite flow

The `agent_join_conference` supervisor tool triggers the full conference activation
chain. It is the **only entry point** for bringing an AI agent into an active session
alongside a human agent.

### Input

| Field | Type | Description |
|---|---|---|
| `session_id` | UUID | The active session the human is currently handling |
| `agent_type_id` | string | AI agent type to invite (from `supervisor_capabilities`) |
| `pool_id` | string | Pool the AI agent belongs to (from `supervisor_capabilities`) |
| `interaction_model` | enum | `"conference"` (visible) or `"background"` (silent) |

### What the tool does

1. Reads `session:{session_id}:meta` from Redis to get `tenant_id`, `customer_id`, `channel`
2. Returns an error if the session meta is absent (session not active)
3. Generates `conference_id` (UUID) and `participant_id`
4. Publishes `ConversationInboundEvent` to `conversations.inbound` Kafka topic with
   `agent_type_id`, `pool_id`, and `conference_id` set
5. Returns `{ conference_id, participant_id, status: "joining" }` immediately

The tool returns optimistically — routing happens asynchronously. The human agent's
WebSocket will receive `conference.agent_completed` when the AI finishes (published
by the Orchestrator Bridge to `agent:events:{session_id}`).

### Why pool_id is required from the caller

`pool_id` is not inferred by the tool because looking it up from the registry would
add a synchronous HTTP call in the hot path. The `supervisor_capabilities` tool is
expected to return a list of `{ agent_type_id, pool_id }` pairs for available AI agents;
the human agent UI selects from this list and passes both fields to `agent_join_conference`.

### Event published to conversations.inbound

```json
{
  "session_id":    "<existing session>",
  "tenant_id":     "<from Redis meta>",
  "customer_id":   "<from Redis meta>",
  "channel":       "<from Redis meta>",
  "pool_id":       "<from input>",
  "agent_type_id": "<from input>",
  "conference_id": "<generated UUID>",
  "started_at":    "<now>",
  "elapsed_ms":    0
}
```

The Routing Engine receives this event, filters instances by `agent_type_id` within
the specified pool, allocates one, and publishes `conversations.routed` with
`conference_id` propagated in the result. The bridge then activates the AI agent
with `conference_id` in `session_context`.

## Spec reference

- 3.2a — Supervisor tools
- 3.2b — rule_dry_run
- 4.2  — completion contract (agent_done)
- 4.5  — lifecycle (agent_login/ready/busy/heartbeat/logout)
- 9.4  — Agent Runtime and BPM tools
- 9.5  — A2A protocol (agent_delegate)
