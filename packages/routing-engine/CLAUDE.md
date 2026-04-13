# plughub-routing-engine — Routing Engine

## What it is

Sole arbiter for allocating conversations to agents.
Every conversation passes through here — no component routes without the Routing Engine.

## Main flow

1. Consumes `conversations.inbound` event from Kafka
2. Identifies candidate pools (channel + tenant + status active)
3. For each candidate pool, fetches agent_ready instances from Redis
4. Computes priority_score using the pool's routing_expression
5. Allocates the instance with the highest score
6. Publishes `conversations.routed` to Kafka
7. Maintains session affinity for stateful agents

## priority_score (spec 3.3)

```
score = (sla_weight    × sla_urgency)
      + (wait_weight   × normalised_wait_time)
      + (tier_weight   × tier_score)
      + (churn_weight  × churn_risk)
      + (business_weight × business_score)

sla_urgency = wait_time_ms / sla_target_ms
```

## Decision timeout: 150ms (spec 3.3)

## Invariants

- Never allocates without verifying agent_ready in Redis
- Stateful agents maintain session affinity — same instance
- Agents with current_sessions == max_concurrent_sessions are skipped
- 150ms timeout — if no allocation, queues according to channel policy

## Stack

- Python 3.11+
- aiokafka (async consumer/producer)
- redis[hiredis]
- httpx (agent-registry queries)
- pydantic + pydantic-settings

## CrashDetector — false-positive handling for native AI agents

### Problem: heartbeat TTL vs. execution duration

Every agent instance must refresh its heartbeat key (`{tenant_id}:instance:{instance_id}`,
TTL 30 s) to remain visible to the Routing Engine. Human agents and external agents
send continuous heartbeats. Native AI agents, however, are stateless: once they start
executing a skill flow, they do not send heartbeats until the flow completes.

A skill flow containing a `menu` step blocks for up to 300 seconds (BLPOP timeout).
The 30-second heartbeat TTL expires 10× before the menu step can complete. Without
additional protection, the CrashDetector would declare the instance crashed and
re-queue all its active conversations — creating a spurious duplicate execution.

### Solution: check the Skill Flow execution lock before re-queuing

The Skill Flow Engine maintains an execution lock per session:

```
{tenant_id}:pipeline:{session_id}:running   (TTL 400s, renewed by long-running steps)
```

Before re-queuing any conversation, `CrashDetector._handle_crash` checks:

```python
lock_key = f"{tenant_id}:pipeline:{conversation_id}:running"
if await redis.exists(lock_key):
    # Engine still executing — not a real crash — skip this conversation
    continue
```

Only conversations without an active engine lock are re-queued. Conversations
with an active lock belong to native AI agents that are still alive and executing;
their instance heartbeat expiry is expected and benign.

### pool_id and agent_type_id in the re-queued event

When a genuine crash is detected (lock absent), the re-queued `conversations.inbound`
event includes `pool_id` and `agent_type_id` from `InstanceMeta`. This allows the
Router to target the same pool that was serving the conversation before the crash,
avoiding misrouting caused by the absence of intent/confidence context in the minimal
crash-recovery event.

```python
"pool_id":       meta.pools[0] if meta.pools else "",
"agent_type_id": meta.agent_type_id,
```

### Invariant

The CrashDetector must never re-queue a conversation whose engine lock is present.
Violating this invariant would create two simultaneous executions advancing the same
`pipeline_state`, corrupting the session with non-deterministic transitions.

## Spec reference

- 3.3  — routing dimensions and priority_score
- 3.3a — behaviour with saturated pools
- 4.5  — instance lifecycle (agent_ready, agent_busy)
- 4.7  — Skill Flow execution lock (spec 4.7, engine section)
