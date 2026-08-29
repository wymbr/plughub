# Arc 8 — Agent Availability & Pause Tracking

> Última atualização: 2026-05-25 · Estado: Arc 16
>
> Full reference for human agent pause tracking, ClickHouse analytics, and the agent availability report.
> See CLAUDE.md for architectural summary.

---

## Overview

Arc 8 tracks human agent pause intervals for workforce management and operational analytics. It does not affect AI agent scheduling (AI agents are managed by the Bootstrap reconciliation loop).

Components:
- **mcp-server-plughub**: exposes `agent-pause` / `agent-resume` MCP endpoints
- **orchestrator-bridge**: updates Redis agent state on pause/resume
- **Kafka `agent.lifecycle`**: carries `agent_pause` and `agent_ready` events
- **analytics-api**: persists pause intervals to ClickHouse
- **platform-ui**: a bancada consome `/reports/agent-availability` e vive no **modo comparar** de `/analise/resources` (F3 do ADR de relatórios, 2026-08-29; `/analise/agents` virou redirect). A `AgentReportsPage.tsx` era órfã e foi removida em 2026-08-28

---

## Pause/Resume Endpoints

Both endpoints are in `mcp-server-plughub` and follow the standard agent contract lifecycle.

### `PUT /api/agent-pause`

Request body:
```json
{
  "agent_instance_id": "agente_retencao_v1-001",
  "reason_id":         "lunch",
  "reason_label":      "Almoço"
}
```

Actions:
1. Updates Redis agent state from `agent_ready` → `agent_paused`
2. Publishes `agent_pause` to `agent.lifecycle` Kafka topic with `reason_id`/`reason_label`
3. Routing Engine excludes paused agents immediately (pause is a hard filter)

### `PUT /api/agent-resume`

Actions:
1. Updates Redis state from `agent_paused` → `agent_ready`
2. Publishes `agent_ready` to `agent.lifecycle` Kafka topic
3. Agent becomes eligible for new session assignments immediately

---

## Pause Reasons

Configured via Config API namespace `agent_activity`, key `pause_reasons`:
```json
[
  { "id": "lunch",    "label": "Almoço",    "max_minutes": 60 },
  { "id": "break",    "label": "Intervalo", "max_minutes": 15 },
  { "id": "training", "label": "Treinamento" },
  { "id": "meeting",  "label": "Reunião" }
]
```

The `max_minutes` field is for UI hint only — not enforced by the backend.

---

## Kafka Event Schema

Both events use `AgentLifecycleEventSchema` on topic `agent.lifecycle`:

```json
{
  "event_type":        "agent_pause" | "agent_ready",
  "agent_instance_id": "agente_retencao_v1-001",
  "agent_type_id":     "agente_retencao_v1",
  "pool_id":           "retencao_humano",
  "tenant_id":         "...",
  "reason_id":         "lunch",          // present on agent_pause only
  "reason_label":      "Almoço",         // present on agent_pause only
  "timestamp":         "2026-05-09T..."
}
```

---

## ClickHouse Schema

### `agent_pause_intervals`

```sql
CREATE TABLE analytics.agent_pause_intervals (
    tenant_id        String,
    agent_instance_id String,
    agent_type_id    String,
    pool_id          String,
    reason_id        String,
    reason_label     String,
    paused_at        DateTime64(3, 'UTC'),
    resumed_at       Nullable(DateTime64(3, 'UTC')),
    duration_ms      Nullable(UInt32),   -- null while still paused
    _updated_at      DateTime64(3, 'UTC') DEFAULT now()
)
ENGINE = ReplacingMergeTree(_updated_at)
ORDER BY (tenant_id, agent_instance_id, paused_at)
PARTITION BY toYYYYMM(paused_at)
```

analytics-api's `agent.lifecycle` consumer:
- On `agent_pause`: INSERT row with `resumed_at = null`
- On `agent_ready`: UPDATE matching row via ReplacingMergeTree with `resumed_at` + `duration_ms`

---

## Analytics Endpoint

### `GET /reports/agent-availability`

Query parameters:
| Param | Required | Description |
|---|---|---|
| `tenant_id` | yes | |
| `pool_id` | no | Filter to specific pool |
| `agent_type_id` | no | Filter to specific agent type |
| `from` | yes | ISO-8601 date start |
| `to` | yes | ISO-8601 date end |

Response includes per-agent:
- Total pause time (ms)
- Pause count
- Pause time by reason
- Available vs unavailable ratio
- Longest single pause

---

## Platform-UI

~~`AgentReportsPage.tsx` at `/contacts/reports/agents`~~ — **REMOVIDA 2026-08-28** (órfã; F0 do ADR de relatórios). A superfície viva é o modo comparar de `/analise/resources` (F3, 2026-08-29).

Accessible to: `supervisor`, `admin`, `business` roles.
ABAC gate: `contacts.visualizar` (read access required).

Shows:
- Pool-level availability overview (stacked bar: available / busy / paused)
- Per-agent pause detail table with drill-down by reason
- Time-series chart of availability ratio

> **Pending**: `AgentsPage` Lista sub-tab requires `GET /reports/agent-performance/daily` from Arc 8 analytics — see TODO.md.

---

## Fase 1b — Logged Time & Availability (2026-06-02)

Extends Arc 8 from "pause time only" to **logged time + availability**, via a new
`agent_login_intervals` table that mirrors `agent_pause_intervals`.

### Event reuse (no producer change)

No new login event is emitted. The analytics consumer derives login intervals
from events it already receives on `agent.lifecycle`:

| Signal | Event | Identity source |
|---|---|---|
| Open interval | first `agent_ready` (human) / `agent_login` (native) | `user_id`/`user_login` ride the human `agent_ready` (C1) |
| Keep open | subsequent `agent_ready` (resume-from-pause, pool refresh) | — (only refreshes TTL) |
| Close interval | `agent_logout` | reads open state from Redis by `instance_id` |

This avoids a new event that the routing engine could process into an incomplete
`_upsert_instance` (the wipe-bug class). The pause state machine is untouched.

### ClickHouse — `agent_login_intervals`

```sql
CREATE TABLE {db}.agent_login_intervals (
    interval_id    String,
    tenant_id      String,
    instance_id    String,
    user_id        String,
    user_login     String,
    agent_type_id  String,
    pool_id        String,
    logged_in_at   DateTime64(3, 'UTC'),
    logged_out_at  Nullable(DateTime64(3, 'UTC')),
    duration_ms    Nullable(Int64),
    ingested_at    DateTime DEFAULT now(),
    date           Date
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, instance_id, logged_in_at)
```

Consumer (`consumer.py`): `_handle_login_interval` with Redis key
`{tenant_id}:login:{instance_id}` (TTL 24h, refreshed on each `agent_ready` while
open). Open writes a row with `logged_out_at`/`duration_ms` NULL; `agent_logout`
writes the close row; ReplacingMergeTree keeps the close (later `ingested_at`).

### Endpoint — `GET /reports/agent-availability` (reworked)

`_fetch_agent_availability` now groups by **`instance_id`** (per person — humans
are no longer collapsed into `human_agent_{pool}`) and merges login + pause +
reason-breakdown in Python. New per-row fields: `instance_id`, `user_login`,
`user_id`, `logged_ms`, `total_logins`, `available_ms` (= `logged_ms −
total_pause_ms`, clamped at 0). Legacy fields (`agent_type_id`, `total_pause_ms`,
`reason_breakdown`, `period_date`) are preserved — backward compatible.

### Platform-UI — `AgentsTab.tsx`

Embedded in Analytics/Agents (Human tab) and ContactsPage. The **Availability**
sub-tab is a per-identity summary (Agent=`user_login` · Pool · Logged · Paused ·
Available · Avail%) plus a **pause-reason donut** (Recharts `PieChart` over
`reason_breakdown`). The **Pauses** sub-tab labels rows by `user_login`.

### Known limitations / deferred

- Login interval TTL 24h: a shift >24h with no event leaves an orphan open interval.
- **Occupancy** (busy time from segments ÷ logged time) deferred to a later step.
- **Pause-reason management UI** (Configuration CRUD + pool association + agent-side
  reason picker) is a separate pending task — the data model already carries
  `reason_id`/`reason_label` and the report shows the donut.

---

## Timeline — Per-Pool Presence (2026-06-02)

Swimlane timeline per agent: a **Total** lane (logged time) plus one lane **per
pool**, on a shared time axis, with pause blocks overlaid on every lane. Answers
"how long logged in total **and** how long covering each pool" without summing —
the two views are non-additive (a person has one clock; per-pool time is an
attribution, not a partition).

### ClickHouse — `agent_pool_intervals`

```sql
CREATE TABLE {db}.agent_pool_intervals (
    interval_id       String,
    login_interval_id String,        -- links to the parent login interval
    tenant_id         String,
    instance_id       String,
    user_id           String,
    user_login        String,
    agent_type_id     String,
    pool_id           String,
    entered_at        DateTime64(3, 'UTC'),
    left_at           Nullable(DateTime64(3, 'UTC')),
    duration_ms       Nullable(Int64),
    ingested_at       DateTime DEFAULT now(),
    date              Date
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, instance_id, pool_id, entered_at)
```

### Pool diff (consumer)

`_handle_login_interval` keeps `pools_open = {pool_id: {interval_id, entered_at}}`
in the `{tenant}:login:{instance}` Redis state. On each `agent_ready` carrying an
**authoritative** `pools[]` snapshot (registerHumanAgent and partial-logout publish
the full list; resume-from-pause does **not** — those are ignored), it diffs:
entered pools → open a presence row; left pools → close one. `agent_logout` closes
every open presence, then the login interval.

### Endpoint — `GET /reports/agent-timeline`

`?tenant_id&instance_id&from_dt&to_dt` → `{ login_intervals, pause_intervals,
pool_intervals }` (ISO timestamps; open intervals have a null end). Pool scoping
restricts `pool_intervals` to the caller's `accessible_pools`.

### Platform-UI

`AgentTimeline.tsx` renders the swimlanes as a modal opened by **drill-down**:
clicking an agent row in the Availability sub-tab opens their timeline for the
selected period. Bars are positioned by percentage over the data's time domain;
pauses overlay every lane.

### Known limitation

Per-pool precision is approximate: the full presence interval is attributed to
each pool the agent touched during it. Exact per-pool sub-intervals are a future
refinement.
