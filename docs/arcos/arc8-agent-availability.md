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
- **platform-ui**: `AgentReportsPage.tsx` renders availability reports

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

`AgentReportsPage.tsx` at `/contacts/reports/agents`.

Accessible to: `supervisor`, `admin`, `business` roles.
ABAC gate: `contacts.visualizar` (read access required).

Shows:
- Pool-level availability overview (stacked bar: available / busy / paused)
- Per-agent pause detail table with drill-down by reason
- Time-series chart of availability ratio

> **Pending**: `AgentsPage` Lista sub-tab requires `GET /reports/agent-performance/daily` from Arc 8 analytics — see TODO.md.
