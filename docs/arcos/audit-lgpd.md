# Audit LGPD — Compliance Role Architecture

> Última atualização: 2026-05-25 · Estado: Arc 16

## Overview

The Audit module provides a dedicated ABAC-gated surface for Data Protection Officers (DPOs) and compliance teams to inspect personal data processed by the PlugHub platform, in accordance with LGPD requirements.

The module is **not a fixed role** — it is implemented as an ABAC module (`audit`) so any user with a JWT containing `module_config.audit.*` fields gains scoped access, orthogonally to `operator`/`supervisor`/`admin` roles.

---

## ABAC Module — `audit`

Defined in `infra/modules.yaml` under the `audit` module key. Five fields:

| Field | Scope | Description |
|---|---|---|
| `sessions` | read_only | View session messages (masked) + immutable access log |
| `mcp_calls` | read_only | View MCP tool calls with masked input fields |
| `user_access` | read_only | Authentication logs and refresh token rotation *(stub)* |
| `data_requests` | read_write | SAR / erasure requests CRUD *(stub)* |
| `config_snapshot` | read_only | Active masking rules and retention policies *(stub)* |

The `PermissionChecker` (platform-ui) and `_check_audit_field` (analytics-api) both use the same `_ACCESS_ORDER` map:
```python
{"none": 0, "read_only": 1, "write_only": 1, "read_write": 2}
```

---

## Backend — analytics-api

### New ClickHouse Tables

**`analytics.mcp_audit_log`** (`ReplacingMergeTree`, ORDER BY `(tenant_id, event_id)`):
- Stores every MCP tool call with full `masked_input_fields` array (not just the truncated timeline entry).
- Partitioned by `toYYYYMM(date)`.
- Fed by `parse_mcp_audit_event()` dual-write: returns `[timeline_row, log_row]`. The `timeline_row` goes to `session_timeline`; the `log_row` goes to `mcp_audit_log`.
- `ReplacingMergeTree` ensures idempotency on consumer replay.

**`analytics.audit_access_log`** (`MergeTree`, ORDER BY `(tenant_id, accessed_at, access_id)`):
- Records every DPO read of session messages. Intentionally **not** `ReplacingMergeTree` — every access is a permanent, non-deduplicated record.
- Columns: `access_id`, `tenant_id`, `accessed_by`, `resource`, `resource_id`, `accessed_at`, `date`.

### Kafka → ClickHouse Consumer Update

`parse_mcp_audit_event()` in `models.py` now returns `list[dict] | None` instead of `dict | None`. The consumer's `_write_row()` dispatcher has two new branches:

```python
elif table == "mcp_audit_log":
    await store.insert_mcp_audit_log(row)
elif table == "audit_access_log":
    await store.insert_audit_access_log(row)
```

This is backward-compatible because `_process_message` already normalizes results:
```python
rows = result if isinstance(result, list) else [result]
```

### audit_router.py

New FastAPI router at prefix `/v1/audit`, registered in `main.py`.

**ABAC helper** `_require_audit_access(field, credentials)`:
- Decodes Bearer JWT using `auth_jwt_secret`.
- Checks `module_config.audit.{field} >= read_only`.
- Raises 401 (missing/expired/invalid), 403 (insufficient perms), 503 (secret not configured).
- Returns decoded payload (contains `tenant_id`, `sub`, `module_config`).

**`GET /v1/audit/sessions/{session_id}/messages`**:
- Requires `audit.sessions >= read_only`.
- Queries `analytics.messages` table via `store.query_session_messages()`.
- Returns masked `content` (token format `[category:id:partial]`). Full unmasked `original_content` requires token resolution via Core — deferred.
- **Side effect**: writes immutable row to `audit_access_log` (fire-and-forget — never fails the read on log error).
- Response: `{ session_id, tenant_id, count, messages[] }`.

**`GET /v1/audit/mcp-calls`**:
- Requires `audit.mcp_calls >= read_only`.
- Tenant isolation: `caller_tenant_id` from JWT must match `tenant_id` query param, or 403.
- Query params: `tenant_id` (required), `session_id` (optional), `from_dt`/`to_dt` (ISO8601), `masked_only=true` (default), `limit=200` (max 1000).
- Response: `{ tenant_id, masked_only, count, calls[] }`.

---

## Frontend — platform-ui

### AuditPage.tsx (`/audit`)

Located at `packages/platform-ui/src/modules/audit/AuditPage.tsx`.

Five tabs, two active and three stubs:

| Tab | Status | Description |
|---|---|---|
| Sessions | Active | Input for session_id → fetch messages → masked timeline with role color chips |
| MCP Calls | Active | Filters (session_id, masked_only, from/to) → table with masked_input_fields badges |
| User Access | Stub | "Em desenvolvimento" |
| Data Requests | Stub | "Em desenvolvimento" |
| Config Snapshot | Stub | "Em desenvolvimento" |

Auth: calls `getAccessToken()` from `useAuth()` (in-memory JWT, not localStorage). Base URL from `import.meta.env.VITE_ANALYTICS_URL`.

Warning banner shown on every tab: *"Todo acesso a esta área é registrado em log de auditoria"* (red/amber background).

### Nav Entry

Added to `Sidebar.tsx` as a standalone item between Analytics and Configuração groups:

```typescript
{
  label: t('nav.audit'),
  href:  '/audit',
  icon:  '🔍',
  roles: ['admin', 'supervisor'],
  abac:  { module: 'audit', field: 'sessions' },
}
```

Only visible when user has `module_config.audit.sessions >= read_only`. Admin and supervisor roles bypass ABAC check per `passesAbac()` logic.

i18n: `nav.audit = "Audit"` (en), `nav.audit = "Auditoria LGPD"` (pt-BR).

---

## Deferred Phases

### Phase 2 — Full unmasked content (`original_content`)

Session messages in ClickHouse store masked `content` only. To expose `original_content` for DPO review, analytics-api would need to call Core's token resolution endpoint. Decision: deferred until Core exposes a dedicated batch token-resolution REST endpoint for audit use.

### Phase 3 — user_access logs

Auth-api currently logs refresh token rotation and failed logins to PostgreSQL but does not stream them to analytics. Requires a `user_access.events` Kafka topic and a new ClickHouse table.

### Phase 4 — SAR/Erasure pipeline

Subject Access Requests (data_requests) require:
- CRUD endpoints in a dedicated `audit-api` or extension of `auth-api`
- Pseudonymization step for `sessions_stream` in Core
- Anonymization job for ClickHouse analytics tables (UPDATE/DELETE not native in ClickHouse — requires TTL or partition replacement strategy)

### Phase 5 — config_snapshot

Read-only snapshot of `masking` namespace from Config API, accessible to DPO for verification that masking rules are correctly configured.
