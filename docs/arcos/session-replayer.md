# Session Replayer — Quality Evaluation Pipeline

> Última atualização: 2026-05-25 · Estado: Arc 16
>
> Full reference for the session replay pipeline used by quality evaluation.
> See CLAUDE.md for architectural summary and [`docs/adr/adr-session-replayer.md`](../adr/adr-session-replayer.md) for the architecture decision record.

---

## Pipeline Overview

```
session_closed
    │
    ▼
Stream Persister (analytics-api)
    │  Writes canonical stream events to PostgreSQL session_events table
    │
    ▼
evaluation.requested  ←──── Kafka (sampling engine triggers this)
    │
    ▼
Hydrator
    │  Redis hit?  → no-op (stream already in Redis cache)
    │  Redis miss? → reads from PostgreSQL → writes back to Redis
    │
    ▼
Replayer (always reads Redis)
    │  Reconstructs session with timing (delta_ms between events)
    │  Applies speed_factor (default 10× for batch evaluation)
    │
    ▼
ReplayContext  →  Redis: {tenant}:replay:{session_id}:context  (TTL 1h)
    │
    ▼
Evaluator agent (agente_avaliacao_v1)
    │  evaluation_context_get → evaluation_submit
    │
    ▼
evaluation.events  →  ClickHouse
```

---

## Pattern: ensure-before-read

The Hydrator implements an "ensure before read" pattern: the Replayer always reads from Redis, and the Hydrator ensures the data is there before the Replayer runs. This decouples the live session path (which writes to Redis) from the evaluation path (which needs a stable snapshot).

Benefits:
- Zero impact on live sessions (no blocking reads from PostgreSQL during session)
- Evaluation always has consistent snapshot (not affected by late Kafka delivery)
- Re-evaluation is free (data stays in Redis for TTL 1h)

---

## ReplayContext

Stored at `{tenant}:replay:{session_id}:context` (TTL 1h):

```python
ReplayContext {
    session_id:        str
    tenant_id:         str
    pool_id:           str
    events:            list[ReplayEvent]
    evaluation_form:   dict | None     # Arc 6: loaded if campaign has form_id
    campaign_context:  dict | None     # Arc 6: campaign metadata
    knowledge_snippets: list[dict]     # Arc 6: top-5 knowledge matches (RAG)
}

ReplayEvent {
    type:       str           # event type from stream
    payload:    dict
    delta_ms:   int           # ms since previous event (preserves original timing)
    timestamp:  str           # original event timestamp
}
```

### Comparison Mode

When `comparison_mode: true` is set in the evaluation request, the Replayer also builds `comparison_turns`:
- Original session turns paired with "ideal" turns from the knowledge base
- Jaccard similarity computed per turn (threshold 0.4 — below = divergence point)
- `buildComparisonReport()` returns `divergence_points[]` for the evaluator agent

---

## Timing Replay

`ReplayEvent.delta_ms` preserves the original inter-event intervals from the live session.

`speed_factor` (default `10`) scales all `delta_ms` values:
```python
adjusted_delta_ms = event.delta_ms / speed_factor
```

This means a 10-minute session replays in ~1 minute at default speed.

---

## Stream Persister

Kafka consumer for `conversations.session_opened` and `conversations.session_closed`.

On `session_closed`:
1. Reads all entries from `session:{id}:stream` (Redis)
2. Writes to `analytics.session_events` (PostgreSQL) with `session_id` + `event_order` index
3. Sets `sessions.stream_persisted = true`

This is the authoritative persistence step — the Redis stream TTL can expire after this.

---

## Hydrator

Called when `evaluation.requested` event is received and Redis cache for the session has expired.

```python
async def hydrate(session_id, tenant_id):
    if await redis.exists(f"session:{session_id}:stream"):
        return  # already in Redis, no-op

    events = await pg.fetch(
        "SELECT * FROM analytics.session_events WHERE session_id = $1 ORDER BY event_order",
        session_id
    )
    for event in events:
        await redis.xadd(f"session:{session_id}:stream", ...)
    await redis.expire(f"session:{session_id}:stream", 3600)  # 1h TTL for evaluation window
```

→ See also [`docs/adr/adr-session-replayer.md`](../adr/adr-session-replayer.md) for full design rationale.
