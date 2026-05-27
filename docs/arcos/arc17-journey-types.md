# Arc 17 — JourneyType Governance

Arc 17 introduces **JourneyType** as a first-class platform entity — a named, per-tenant governance primitive that classifies service processes, carries optional SLA targets, and controls which pools may create each type.

---

## Model

```
journey_type_id   string (slug, unique per tenant)
tenant_id         string
description       string? (short label shown in UI)
sla_ms            number? (optional SLA target in milliseconds)
created_at        datetime
updated_at        datetime
```

### journey_type_id format
Lowercase letters, digits and `_` only — same convention as `pool_id` and `skill_id`. Examples: `portabilidade_telco`, `reembolso_sac_v1`, `contratacao_b2b`.

---

## Components

### @plughub/schemas — `JourneyTypeSchema`

Added to `packages/schemas/src/journey-type.ts`:

```typescript
export const JourneyTypeSchema = z.object({
  journey_type_id: z.string().regex(/^[a-z0-9_]+$/),
  tenant_id:       z.string(),
  description:     z.string().optional(),
  sla_ms:          z.number().int().positive().optional(),
  created_at:      z.string(),
  updated_at:      z.string(),
})
```

### agent-registry — CRUD REST

Route: `packages/agent-registry/src/routes/journey-types.ts`

| Method | Path | Description |
|--------|------|-------------|
| `GET`    | `/v1/journey-types`            | List all for tenant |
| `POST`   | `/v1/journey-types`            | Create |
| `PATCH`  | `/v1/journey-types/:id`        | Update description/sla_ms |
| `DELETE` | `/v1/journey-types/:id`        | Delete (hard delete; existing journeys retain reference) |

Auth: admin token (`X-Admin-Token`) required for write operations. Tenant scoped via `x-tenant-id` header.

Prisma table: `journey_types` in `agent-registry` schema. Unique index on `(tenant_id, journey_type_id)`.

### agent-registry — Pool authorization

`PoolRegistrationSchema.authorized_journey_types: z.array(z.string()).optional()`

Pool create/update endpoints accept `authorized_journey_types[]` (array of `journey_type_id` slugs). Empty array = pool cannot create any journey. `undefined` / omitted = pool inherits no restriction (backward-compatible for pre-Arc-17 pools). Stored as `String[]` in Prisma.

---

## Platform UI

### Config/Resources — Journey Types tab

`packages/platform-ui/src/modules/config-recursos/PoolsPage.tsx` hosts a dedicated "Journey Types" tab panel:

- Table with columns: ID, Description, SLA, Actions (edit inline / delete)
- "+ New Journey Type" button → inline create form
- Validation: `id` required, regex `^[a-z0-9_]+$`, SLA must be a positive integer
- Admin token required for write operations (same pattern as competency skills)
- i18n namespace: `configRecursos.journeyTypes` (en + pt-BR)

### Config/Resources — Pool form

`authorized_journey_types` multi-select checkbox list in the Pool create/edit drawer:

- Loads journey types from registry on open
- Each checkbox shows `journey_type_id` + optional `description`
- Empty selection = warning label "journey creation disabled for this pool"
- i18n key: `configRecursos.pools.authorizedJourneyTypes`

### ProcessosPage — JourneysTab (Analytics Explorer)

`packages/platform-ui/src/modules/agent-flow/ProcessosPage.tsx`:

- `Journey` interface extended with `journey_type_id?: string | null` and `pool_id?: string | null`
- `useJourneys` hook extended with `journeyTypeId?` (5th param) and `poolId?` (6th param) — passed as query params `journey_type_id` and `pool_id` to `/reports/journeys`
- **L1 chip row**: registered journey types rendered as toggle chips above the KPI strip; selecting a chip filters the entire journey list to that type; "All types" chip resets
- **Pool dropdown**: select populated from registry pools, passed as `pool_id` filter; hidden when no pools loaded
- **List badges**: `journey_type_id` shown as small purple badge on each journey list row
- **Detail panel**: `journey_type_id` purple badge + `pool_id` code label rendered when present

---

## analytics-api (#302 — implemented)

`journey_events` ClickHouse table has `journey_type_id Nullable(String)` and `pool_id Nullable(String)` columns (DDL + `_DDL_JOURNEY_EVENTS_MIGRATE_ARC17` migration). `parse_journey_event()` extracts both fields from Kafka payload. `_fetch_journeys()` applies them as `base_where` conditions on both the list query and the KPI aggregation — filters are fully end-to-end.

## Pending (backend — not yet implemented)

| Task | Package | Description |
|------|---------|-------------|
| #298 | routing-engine | After allocation, write `session.authorized_journey_types[]` to ContextStore (source: `routing_engine`, confidence 1.0). Enables AI agent to know which journey types it may create. |
| #299 | mcp-server | `journey_start` tool validates `journey_type_id` param: checks `session.authorized_journey_types` from ContextStore; rejects if type not in list or type not registered for tenant. |
| #300 | workflow-api | Add `journey_type_id` nullable FK column to `journeys` table. Persist on `POST /v1/journeys` and `POST /v1/journeys/from-instance/:id`. Include in all Journey response payloads. |
| #301 | skill YAML | When `creates_journey: true` is set on a skill step, require `journey_type_id` field; validator rejects YAML without it. Propagated to `journey_start` call in skill-flow-worker. |

---

## Authorization flow (full — when #298–302 are done)

1. Pool config declares `authorized_journey_types: [portabilidade_telco, reembolso_sac_v1]`
2. Routing Engine allocates agent, writes `session.authorized_journey_types = [portabilidade_telco, reembolso_sac_v1]` to ContextStore
3. AI agent calls `journey_start(journey_type_id="portabilidade_telco", ...)` via MCP
4. `mcp-server` reads `session.authorized_journey_types` → validates `portabilidade_telco` is in list → creates journey with `journey_type_id` set
5. Analytics reports can filter `GET /reports/journeys?journey_type_id=portabilidade_telco`

---

## i18n coverage

| Locale file | Namespace / keys |
|-------------|-----------------|
| `en/configRecursos.json` | `journeyTypes.*`, `pools.authorizedJourneyTypes.*`, `tabs.journeyTypes` |
| `pt-BR/configRecursos.json` | same |
| `en/contacts.json` | `processes.journeys.filters.journeyType`, `processes.journeys.filters.pool`, `processes.journeys.filters.allPools`, `processes.journeys.allTypes`, `processes.journeys.detail.journeyType`, `processes.journeys.detail.pool` |
| `pt-BR/contacts.json` | same |
