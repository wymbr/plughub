# orchestrator-bridge — Orchestrator Bridge

## What it is

The Orchestrator Bridge is the central event processor that connects the Kafka event
bus to the platform's internal agents and routing state. It is a Python service that
runs a continuous event loop consuming from `conversations.events` and `conversations.inbound`.

## Responsibilities

1. Consume `conversations.inbound` — normalised inbound events from Channel Gateway
2. Route inbound events to the correct active agent (AI or human) for the session
3. Consume `conversations.events` — internal platform lifecycle events
4. Handle `contact_closed` events: clean up Redis state, restore agent instances to ready
5. Maintain session routing state in Redis (who is handling each session)
6. Activate human agents when escalation is requested (via Routing Engine)
7. Forward AI session turns to the Skill Flow Engine HTTP endpoint

## What is NOT this package's responsibility

- Does not normalise channel messages — that is Channel Gateway's responsibility
- Does not make routing decisions — Routing Engine is the sole arbiter
- Does not execute skill steps — delegates to skill-flow-engine HTTP service
- Does not communicate with customers directly — publishes to `conversations.outbound`

## Main file

- `src/plughub_orchestrator_bridge/main.py` — single-file event loop with all handlers

## Redis keys used

| Key | Type | Description |
|---|---|---|
| `session:{session_id}:ai` | String (JSON) | AI session state — current turn, partial params, consolidated turns |
| `session:{session_id}:meta` | String (JSON) | Session metadata: contact_id, channel, instance_id (most recent human agent) |
| `session:{session_id}:human_agents` | Set | SET of instance_ids of all active human agents in this session |
| `session:{session_id}:routing:{instance_id}` | String (JSON) | Per-instance routing snapshot — restored to Routing Engine on agent exit |
| `session:{session_id}:human_agent` | String | Legacy binary flag — still written for backward compat; use `human_agents` SET for conference logic |
| `menu:result:{session_id}` | List | LPUSH here to unblock a menu step BLPOP waiting for customer input |
| `menu:waiting:{session_id}` | String | Flag set by menu step before BLPOP; bridge checks this in conference to route messages |
| `session:closed:{session_id}` | List | LPUSH here to unblock a menu step BLPOP on customer disconnect |
| `agent:events:{session_id}` | Pub/Sub channel | Events forwarded to agent WebSocket (human agent UI) |
| `pool:events:{pool_id}` | Pub/Sub channel | Assignment events forwarded to human agents waiting in lobby |

## Kafka topics consumed

| Topic | Events handled |
|---|---|
| `conversations.inbound` | Normalised inbound messages (text, MenuSubmitEvent, etc.) |
| `conversations.events` | `contact_closed`, `agent.activated`, and other lifecycle events |

## Kafka topics produced

| Topic | Events published |
|---|---|
| `conversations.outbound` | Session closed notifications to customer WebSocket |
| `conversations.events` | Forwarded by REST `/agent_done` via mcp-server (not directly here) |

---

## Conference handling — rationale and design

A **conference** occurs when more than one agent (human or AI) is simultaneously active
in the same session. The canonical example: an AI Skill Flow agent is running a `menu`
step collecting customer input, and the operator also escalates to a human specialist.
Both agents are live, both may need to receive the customer's next message.

### The problem with a binary flag

The first implementation used a single `session:{session_id}:human_agent` Redis key as
a boolean: `1` when a human agent was active, deleted when the session ended. This broke
in conference scenarios for two reasons:

1. **LPUSH starved one channel**: the bridge had `if human_agent: push to agent:events
   else if menu_waiting: push to menu:result`. The `else` meant the AI menu BLPOP never
   received messages while a human was active, hanging indefinitely.

2. **First agent to leave cleared the flag**: if two human agents were in conference and
   one called `agent_done`, the flag was deleted even though the second was still active.

### Solution: independent checks + SET tracking

**Independent checks** (no else-if):

```python
is_human = await redis_client.get(f"session:{session_id}:human_agent")
menu_waiting = await redis_client.get(f"menu:waiting:{session_id}")

if is_human:
    await redis_client.publish(f"agent:events:{session_id}", ...)

if menu_waiting:
    await redis_client.lpush(f"menu:result:{session_id}", value)
```

Both checks run independently. A message in a conference reaches both the human agent
WebSocket and the AI menu BLPOP simultaneously.

**`human_agents` SET** (replaces binary flag):

```python
# On human agent activation:
await redis_client.sadd(f"session:{session_id}:human_agents", instance_id)
await redis_client.expire(f"session:{session_id}:human_agents", TTL)

# On one agent leaving (agent_closed):
await redis_client.srem(f"session:{session_id}:human_agents", instance_id)
remaining = await redis_client.scard(f"session:{session_id}:human_agents")
if remaining == 0:
    await redis_client.delete(f"session:{session_id}:human_agent")  # legacy flag
```

The `human_agent` legacy flag is cleared only when the SET becomes empty (last agent left).

### `contact_closed` reason taxonomy

`contact_closed` events arrive on `conversations.events` from two sources:

| Reason | Source | Meaning | `customer_side` |
|---|---|---|---|
| `"client_disconnect"` | channel-gateway | Customer WebSocket/channel dropped | True |
| `"timeout"` | channel-gateway | Customer idle timeout | True |
| `"agent_done"` | channel-gateway | Platform closed customer WS (normal close) | True |
| `"agent_closed"` | mcp-server REST `/agent_done` | One human agent ended their session | False |

**`customer_side = True`** means the customer is gone from the conversation.
The bridge must:
1. Push `1` to `session:closed:{session_id}` — unblocks any active menu BLPOP
2. Notify all active human agents via `agent:events:{session_id}`
3. Restore all agent instances to ready state (`_restore_all_instances`)
4. Clean up all session Redis keys

**`customer_side = False`** (`agent_closed`) means only one human agent left.
The bridge must:
1. Restore only that instance to ready state (`_restore_instance(instance_id)`)
2. `SREM` the instance from `human_agents` SET
3. Clear the legacy `human_agent` flag only if the SET becomes empty
4. Do NOT push to `session:closed` — the conversation continues

```python
customer_side = reason in ("client_disconnect", "timeout", "agent_done")

if customer_side:
    await redis_client.lpush(f"session:closed:{session_id}", "1")
    await redis_client.publish(f"agent:events:{session_id}", json.dumps({
        "type": "session.closed", "reason": reason
    }))
    await _restore_all_instances(redis_client, session_id)
    # ... cleanup
else:
    # reason == "agent_closed"
    instance_id = event.get("instance_id", "")
    await _restore_instance(redis_client, session_id, instance_id)
    await redis_client.srem(f"session:{session_id}:human_agents", instance_id)
    remaining = await redis_client.scard(f"session:{session_id}:human_agents")
    if remaining == 0:
        await redis_client.delete(f"session:{session_id}:human_agent")
```

### Per-instance routing snapshots

When a human agent is activated, the bridge saves the Routing Engine allocation
snapshot for that specific instance:

```python
# Key: session:{session_id}:routing:{instance_id}
await redis_client.set(
    f"session:{session_id}:routing:{instance_id}",
    json.dumps(routing_snapshot),
    ex=TTL,
)
```

This prevents conference from overwriting a single shared snapshot key. When
`_restore_instance(session_id, instance_id)` is called, it reads the correct
per-instance snapshot and sends the restore call to the Routing Engine.

### How `instance_id` reaches `contact_closed`

The REST endpoint `/agent_done` on mcp-server-plughub does not natively know
which Routing Engine instance the agent occupied. The bridge stores the `instance_id`
in `session:{session_id}:meta` during human agent activation. The REST endpoint
reads it back and includes it in the `contact_closed` Kafka event:

```typescript
// mcp-server-plughub REST /agent_done
const metaRaw = await redis.get(`session:${sessionId}:meta`)
const instanceId = JSON.parse(metaRaw)["instance_id"] ?? ""

await kafka.publish("conversations.events", {
  event_type:  "contact_closed",
  session_id:  sessionId,
  instance_id: instanceId,
  reason:      "agent_closed",
})
```

The bridge then uses `event["instance_id"]` in `_restore_instance` to restore
precisely the right instance, without disturbing other agents in the same conference.

### Helper functions

```python
async def _restore_instance(redis_client, session_id: str, instance_id: str) -> None:
    """Restores a single Routing Engine instance to ready state."""
    snapshot_key = f"session:{session_id}:routing:{instance_id}"
    raw = await redis_client.get(snapshot_key)
    if raw:
        snapshot = json.loads(raw)
        # POST to routing engine restore endpoint
        await routing_engine_client.restore(snapshot)
        await redis_client.delete(snapshot_key)

async def _restore_all_instances(redis_client, session_id: str) -> None:
    """Restores all Routing Engine instances active in a session (customer_side=True)."""
    members = await redis_client.smembers(f"session:{session_id}:human_agents")
    for instance_id in members:
        await _restore_instance(redis_client, session_id, instance_id)
    await redis_client.delete(f"session:{session_id}:human_agents")
```

## HTTP timeout

The bridge calls the skill-flow-engine HTTP endpoint to process inbound AI turns.
The timeout must exceed the maximum BLPOP wait in a `menu` step:

- Default `menu` step `timeout_ms`: 300 000 ms (5 minutes)
- Bridge HTTP timeout: **360 seconds** (1 minute headroom above 300s BLPOP)

If the timeout were shorter than the BLPOP, the HTTP call would fail mid-BLPOP,
the AI agent would be stranded in `awaiting_selection`, and the instance would not
be returned to the Routing Engine.

## Conference flow — agent_join_conference activation chain

### Complete event sequence

```
[Human Agent UI]
  calls: agent_join_conference(session_id, agent_type_id, pool_id, ...)

[mcp-server / supervisor.ts]
  reads:    session:{session_id}:meta → tenant_id, customer_id, channel
  generates: conference_id (UUID)
  publishes → conversations.inbound:
    { session_id, tenant_id, customer_id, channel,
      pool_id, agent_type_id, conference_id, started_at }

[routing-engine / router.py]
  receives conversations.inbound with agent_type_id + conference_id
  filters instances: only allocates instances of the requested agent_type_id
  publishes → conversations.routed:
    { session_id, tenant_id,
      result: { allocated, instance_id, agent_type_id, pool_id, conference_id } }

[orchestrator-bridge / process_routed]
  extracts conference_id from result
  calls: activate_native_agent(..., conference_id=conference_id)
    → POST /execute to skill-flow-service:
      { ..., session_context: { conference_id, is_conference: true } }
  (AI agent executes skill flow — human + AI both receive customer messages)
  activate_native_agent returns { outcome, pipeline_state }
  publishes → agent:events:{session_id}:
    { type: "conference.agent_completed", conference_id, agent_type_id,
      outcome, pipeline_state, completed_at }
  restores AI instance in Routing Engine (already implemented)

[Human Agent UI]
  receives conference.agent_completed via WebSocket
  human resumes full control — session continues with customer
```

### Message routing during conference (already implemented)

`process_inbound` checks both flags independently for every customer message:

```python
is_human     = await redis.get(f"session:{session_id}:human_agent")
menu_waiting = await redis.get(f"menu:waiting:{session_id}")

if is_human:      # → agent:events:{session_id}  (human WebSocket)
if menu_waiting:  # → menu:result:{session_id}   (AI BLPOP)
```

Both can be true simultaneously. There is no special-casing for conference — the
bridge routes to each active consumer independently.

### Human leaving during conference

If the human calls REST `/agent_done` while the AI is still running:
- mcp-server publishes `contact_closed { reason: "agent_closed" }` to conversations.events
- Bridge: `customer_side=False` → only the human's instance is restored
- AI continues executing (session:closed is NOT pushed)
- When AI finishes naturally → `conference.agent_completed` is published, human is gone
  but the UI notification is harmless (no subscribers)

### What the AI agent sees

`session_context` passed to the skill flow includes:

```json
{ "conference_id": "<uuid>", "is_conference": true, ... }
```

The skill flow can use `$.session.conference_id` in `invoke` or `reason` step inputs
to adapt behavior (e.g., produce a handoff summary, avoid channel actions that would
confuse a supervised customer interaction).

## Execution lease and instance_id pass-through

When activating a native AI agent (`activate_native_agent`), the bridge passes the
`instance_id` received from `conversations.routed` in the skill-flow-service payload:

```json
{ "instance_id": "<native_instance_id>" }
```

The Skill Flow Engine stores this value as the execution lock key value:

```
{tenant_id}:pipeline:{session_id}:running  →  "<instance_id>"   (TTL 400s)
```

This serves two purposes:

1. **Duplicate execution prevention** — if a second invocation arrives (network
   retry, crash recovery), `SET NX` fails and the engine returns `PRECONDITION_FAILED`
   rather than starting a duplicate pipeline.

2. **False-positive crash detection elimination** — the CrashDetector in the Routing
   Engine checks whether this lock exists before re-queuing a conversation. As long
   as the engine holds the lock, the instance heartbeat expiry (30 s TTL) is
   irrelevant — the conversation will not be re-queued spuriously.

### instance_id lifecycle in process_routed

```
conversations.routed → result["instance_id"] = native_instance_id
    → stored in Redis snapshot (before skill-flow-service call)
    → passed to activate_native_agent(instance_id=native_instance_id)
    → forwarded to skill-flow-service POST /execute { instance_id: ... }
    → engine.run({ instanceId: ... })
    → stored in {tenant_id}:pipeline:{session_id}:running
```

If `instance_id` is absent from the routed event (legacy or test scenario),
an empty string is passed, and the lock value is `""`. The CrashDetector still
finds the key present and skips re-queuing.

## Invariants

- Never route a message without consulting the Routing Engine first
- Never access pipeline_state directly — only menu:result and session:closed lists
- `session:closed` is only pushed when `customer_side=True`
- `_restore_instance` always uses per-instance snapshot keys, never a shared key
- Bridge HTTP timeout (360s) must always exceed the maximum `menu` step `timeout_ms` (300s)
- Always pass `instance_id` from routed event to `activate_native_agent` — never omit it

## Stack

- Python 3.11+
- aiokafka — async Kafka consumer
- redis[hiredis] — async Redis client
- httpx — async HTTP calls to skill-flow-engine and routing-engine

## Spec reference

- 3.3  — Routing Engine allocation and restore protocol
- 3.5  — Channel Gateway: `contact_closed` reason taxonomy
- 4.5  — Agent lifecycle: instance states and restore
- 4.7m — menu step: BLPOP protocol, menu:waiting flag, session:closed signal
- 9.5  — A2A and conference (multi-agent session) protocol
