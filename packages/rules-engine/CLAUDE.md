# plughub-rules-engine — Rules Engine

## What it is

Monitors conversations with an AI agent in real time and triggers escalations
when a tenant rule fires. Operates stateless — no dedicated instance per
conversation and no LLM dependency.

## Main flow

1. Listens for Redis updates (pub/sub on session:{id}:ai)
2. Loads active rules for the tenant
3. Evaluates each rule against the current turn's parameters
4. If a rule fires AND has target_pool → triggers escalation
5. If the rule is in shadow_mode → records without triggering
6. Records firing metrics in ClickHouse

## Observable parameters (spec 3.2)

- sentiment_score — per turn and moving average
- intent_confidence — per turn
- turn_count — number of turns without resolution
- elapsed_ms — total time vs sla_target_ms
- flags — human_requested, sensitive_topic, policy_limit_hit, handoff_requested

## Rule lifecycle (spec 3.2b)

draft → dry-run → shadow → active → disabled

New rules NEVER go directly to active without passing through dry-run.

## Invariants

- Stateless — no per-session state of its own
- No LLM — evaluates only declarative expressions
- When target_pool is absent → triggers nothing
- Shadow mode → evaluates but does not trigger the Escalation Engine
- Every escalation recorded in the audit log (ClickHouse)

## Stack

- Python 3.11+
- redis[hiredis] — pub/sub for session updates
- httpx — calls to mcp-server-plughub (conversation_escalate)
- asyncpg — metrics in ClickHouse (via HTTP driver)
- pydantic + pydantic-settings

## Spec reference

- 3.2  — parameters and rule configuration
- 3.2b — dry-run, shadow mode, session simulator
