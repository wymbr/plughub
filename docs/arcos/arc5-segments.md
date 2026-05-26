# Arc 5 — ContactSegment Analytics

> Última atualização: 2026-05-25 · Estado: Arc 16
>
> Full reference for the ContactSegment data model, ClickHouse schema, and analytics endpoints.
> See CLAUDE.md for architectural summary.

---

## ContactSegment — Data Model

Each participation block in a session is captured as a `ContactSegment`. Unlike a session (which is the full conference room), a segment tracks one agent's involvement window.

```
ContactSegment {
  segment_id        — UUID, primary key
  session_id        — parent session
  participant_id    — agent instance or pool ID
  pool_id           — pool the agent belongs to
  role              — "primary" | "specialist" | "supervisor" | "evaluator" | "reviewer"
  agent_type        — agent_type_id (e.g. "agente_retencao_v1")
  parent_segment_id — null for primary; specialist points to primary segment_id
  sequence_index    — increments per pool on sequential handoffs (0-based)
  started_at        — ISO-8601, when agent joined
  ended_at          — ISO-8601, when agent left (null while active)
  duration_ms       — computed on close
  outcome           — "resolved" | "transferred" | "escalated" | "abandoned" | ...
  close_reason      — from close_reason domain
}
```

### Conference topology

When a specialist is invited via the `task` Skill Flow step (assist mode):
- Specialist gets its own segment with `parent_segment_id = primary.segment_id`
- On transfer, primary segment closes and a new primary segment opens in the receiving pool
- `sequence_index` tracks position in the handoff chain (0 = first agent)

### Participation events

The orchestrator-bridge writes `ConversationParticipantEventSchema` to Kafka topic `conversations.participants` when:
- Agent joins → `event_type: "joined"`
- Agent leaves → `event_type: "left"`, includes `outcome` and `close_reason`

analytics-api consumes this topic and writes to ClickHouse.

---

## ClickHouse Schema

### `analytics.segments`

```sql
CREATE TABLE analytics.segments (
    tenant_id        String,
    session_id       String,
    segment_id       String,
    participant_id   String,
    pool_id          String,
    role             String,
    agent_type       String,
    parent_segment_id Nullable(String),
    sequence_index   UInt8,
    started_at       DateTime64(3, 'UTC'),
    ended_at         Nullable(DateTime64(3, 'UTC')),
    duration_ms      Nullable(UInt32),
    outcome          Nullable(String),
    close_reason     Nullable(String),
    _updated_at      DateTime64(3, 'UTC') DEFAULT now()
)
ENGINE = ReplacingMergeTree(_updated_at)
ORDER BY (tenant_id, session_id, segment_id)
PARTITION BY toYYYYMM(started_at)
```

### `analytics.session_timeline`

`session_timeline` is enriched with `segment_id` — each message in the canonical stream gets the `segment_id` of the active primary agent at the time. This allows per-segment message analysis.

### Materialized views

| View | Engine | Purpose |
|---|---|---|
| `mv_agent_performance_daily` | AggregatingMergeTree | `resolution_rate`, `escalation_rate`, `avg_duration_ms` per `agent_type` × `pool_id` × day |
| `mv_segment_summary` | AggregatingMergeTree | Segment count, avg duration, outcome distribution per pool |

---

## REST Endpoints (analytics-api)

| Endpoint | Description |
|---|---|
| `GET /reports/segments` | Paginated segment list with filters: `session_id`, `pool_id`, `agent_type`, `date_from/to` |
| `GET /reports/agents/performance` | Current performance stats from `mv_agent_performance_daily` |
| `GET /reports/agent-performance/daily` | Time-series performance per agent type (Arc 8 integration) |
| `GET /reports/sessions/complexity` | Sessions ranked by segment count (handoff depth) |

Query parameters common to all endpoints: `tenant_id` (required), `pool_id` (optional filter), `from`/`to` (date range).

---

## Invariants

- `segment_id` is always flat in the canonical stream — `writeStreamEntry()` guarantees this
- `parent_segment_id = null` for primary agents; never null for specialists invited via `task` step
- `sequence_index` resets to 0 when a session starts fresh in a new pool after transfer
- `ended_at = null` while the segment is active — analytics-api handles open segments with live Redis state

→ See also [`docs/adr/adr-contact-segments.md`](../adr/adr-contact-segments.md) for the original architecture decision record.
