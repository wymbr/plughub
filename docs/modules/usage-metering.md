# Usage Metering

> Full reference for the usage metering pipeline: Kafka schema, dimensions, Redis counters, quota enforcement, and cycle reset.
> See CLAUDE.md for architectural summary.

---

## Design Principle

**Metering ≠ Pricing.** Usage records contain raw consumption counts only — no prices, no plan logic. The pricing-api reads Redis counters and applies plan configuration separately.

This separation means:
- Metering always runs, regardless of pricing plan
- Plan changes take effect without touching metering code
- Analytics can use raw usage data independently of billing

---

## Kafka Topic: `usage.events`

Schema: `UsageEventSchema` in `@plughub/schemas/src/usage.ts`

```json
{
  "event_id":         "<uuid>",
  "tenant_id":        "...",
  "session_id":       "...",
  "dimension":        "sessions",
  "quantity":         1,
  "source_component": "core",
  "metadata":         {}
}
```

| Field | Notes |
|---|---|
| `dimension` | One of the wired dimensions below |
| `quantity` | Always positive integer |
| `source_component` | `"core"` | `"ai_gateway"` | `"channel_gateway"` |
| `metadata` | Optional context (e.g. model name for token events) |

---

## Wired Dimensions

| Dimension | Producer | Guard | Notes |
|---|---|---|---|
| `sessions` | Core | SET NX guard prevents double-count on reconnect | 1 per unique session |
| `messages` | Core | visibility=all filter | Only customer-visible messages |
| `llm_tokens_input` | AI Gateway | — | Sum of `usage.input_tokens` per inference |
| `llm_tokens_output` | AI Gateway | — | Sum of `usage.output_tokens` per inference |
| `webchat_attachments` | Channel Gateway | — | 1 per uploaded file committed |

### Pending (adapters not yet wired)

| Dimension | Status |
|---|---|
| `whatsapp_conversations` | Function in `usage_emitter.py` ready — WhatsApp adapter not yet created |
| `voice_minutes` | Function ready — WebRTC/Voice adapter not yet created |
| `sms_segments` | Function ready — SMS adapter not yet created |
| `email_messages` | Function ready — Email adapter not yet created |

---

## Redis Counters

All counters are maintained by the `usage-aggregator` Kafka consumer (part of analytics-api):

```
{tenant}:usage:current:{dimension}   — running total, TTL 45 days
{tenant}:quota:limit:{dimension}     — limit written by pricing-api on plan activation
{tenant}:quota:concurrent_sessions   — current active session count (SET NX / DEL pattern in Core)
```

TTL of 45 days covers at least one full billing cycle plus grace period.

---

## Quota Enforcement

`assertQuota(tenant_id, dimension, quantity)` in Core and Channel Gateway:

```python
# Atomic INCRBY-check-rollback pattern
new_val = redis.incrby(f"{tenant}:usage:current:{dimension}", quantity)
limit   = redis.get(f"{tenant}:quota:limit:{dimension}")
if limit and new_val > int(limit):
    redis.decrby(f"{tenant}:usage:current:{dimension}", quantity)
    raise QuotaExceededError(dimension)
```

Concurrent session guard uses SET NX:
```python
active = redis.incr(f"{tenant}:quota:concurrent_sessions")
limit  = redis.get(f"{tenant}:quota:limit:concurrent_sessions")
if limit and active > int(limit):
    redis.decr(f"{tenant}:quota:concurrent_sessions")
    raise QuotaExceededError("concurrent_sessions")
```

Quota limits are written **directly by pricing-api** when a plan is activated — not seeded by Config API. See `docs/modules/pricing.md`.

---

## Cycle Reset

`POST /admin/cycle-reset` (analytics-api, port 3950)

Resets all `{tenant}:usage:current:*` counters to `0` at the start of a new billing cycle. Called by pricing-api's billing cycle job, not by operators directly.

Request body:
```json
{ "tenant_id": "...", "cycle_start": "2026-06-01" }
```

The reset is idempotent if called multiple times on the same cycle_start (guarded by a cycle sentinel key).
