# plughub-rules-engine — Rules Engine

## What it is

Monitors conversations with an AI agent in real time and triggers escalations
when a tenant rule fires. Operates stateless — no dedicated instance per
conversation and no LLM dependency.

## Main flow

1. Listens for Redis pub/sub `session:updates:{session_id}` (published by ai-gateway on every `reason`/turn)
2. Loads active rules for the tenant
3. Evaluates each rule against the current turn's parameters — the sentiment is the MEASURED one,
   read from the ContextStore (`core.sentiment.current`), never the pub/sub field (RUL-04); the
   ai-gateway republishes the turn with `trigger: sentiment_measured` when a measurement lands.
   Unmeasured means `None` and never matches a sentiment condition
4. If a rule fires AND has target_pool → publishes `rules.escalation.events` (active) or
   `rules.shadow.events` (shadow), keyed by `session_id`
5. It never stops an agent nor routes: the orchestrator-bridge consumes the active event and the
   skill-flow engine stops the AI at the next step boundary (RUL-02, `docs/arcos/rules-escalation.md`)

## Observable parameters (spec 3.2)

- sentiment_score — the latest MEASUREMENT (no moving average: `window_turns` is refused, RUL-04)
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
- Every evaluated context is published to `rules.turn_contexts` (every tenant, with or without
  rules) — the historical dry-run re-reads it from ClickHouse `rule_turn_contexts` (RUL-05).
  The rules-engine READS ClickHouse; the analytics-api is the only writer

## Stack

- Python 3.11+
- redis[hiredis] — pub/sub for session updates
- aiokafka — publishes the rule events (RUL-01 removed the HTTP call to a route that never existed)
- asyncpg — metrics in ClickHouse (via HTTP driver)
- pydantic + pydantic-settings

## Spec reference

- 3.2  — parameters and rule configuration
- 3.2b — dry-run, shadow mode, session simulator
