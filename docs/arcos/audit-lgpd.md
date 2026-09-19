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
{"none": 0, "read_only": 1, "read_write": 2}   # `write_only` removido na AUT-40
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

---

## O gate `_check_audit_access` — detalhe movido do `CLAUDE.md` em 2026-09-06 (DOC-02)

`analytics-api` expõe dois endpoints em `/v1/audit`: `GET /sessions/{id}/messages` e
`GET /mcp-calls`. O portão é `_check_audit_access(request, field)` (`audit.py`), com **cinco ramos
declarados**, cada um com o seu código:

| ramo | desfecho | por quê |
|---|---|---|
| `analytics_open_access` | **LIBERA**, nomeando o ator como `open_access` | modo aberto de demo, e a trilha registra quem foi |
| sem `auth_jwt_secret` | **503** | falha do SERVIÇO — postura **oposta** à do `pool_auth`, que degrada aberto: lá é escopo de leitura, aqui é dado pessoal |
| credencial ausente ou não verificável | **401** | — |
| `module_config.audit.{sessions\|mcp_calls}` ≥ `read_only` | **LIBERA** | — |
| senão | **403**, e a recusa **nomeia quem foi barrado** | — |

O verificador é o CANÔNICO (`plughub_authz`) desde 2026-08-28 — a lista indexada local, onde
`write_only` era maior que `read_only`, saiu junto. **Nunca `enforce_write` aqui:** ele responde
direto, e esta casa precisa GRAVAR antes de responder.

### A trilha só vale se a recusa também for gravada — e a sem credencial não era *(fechado 2026-08-28)*

As duas rotas carregavam `optional_pool_principal` só pelo `tenant_id` (o `accessible_pools` nunca
foi lido: auditoria é ortogonal a pool). Sendo um `Depends`, o `401` dele era levantado **antes do
corpo do handler**, então `_record_access` nunca rodava — e o banner da tela prometia que todo
acesso fica registrado.

> **Regra derivada:** portão que decide dentro de um `Depends` não pode ter efeito colateral no
> handler; se a recusa precisa gravar, ela decide onde grava.

Hoje a identidade sai do próprio portão. Gates: `infra/test/probe_audit_surface.sh` (P4) +
`tests/test_audit_handler_trail.py` — este último nasceu porque uma mutação
(`status_code=denied.status` → `403`) sobreviveu a **23 testes verdes**: eles cobriam o VEREDICTO, e
nada atravessava a rota.

### ⚠️ Correção de 2026-08-22, por medição

O `CLAUDE.md` afirmava `_require_audit_access()` e o dual-write `[timeline_row, mcp_audit_log_row]`
como entregues (CHANGELOG de 2026-05-14). **Nada disso existia na árvore:** nenhum gate no handler —
só `optional_pool_principal`, que confere ASSINATURA e não autorização, então qualquer token válido
do tenant lia dado pessoal —, nenhum `INSERT`, e nenhuma das duas tabelas em `_ALL_DDL`
(`probe_audit_surface.sh`: 0 de 2, com `session_timeline` de testemunha). O `401` que o token
malformado devolve é o que fazia o buraco parecer coberto.

### Acesso servido FORA da analytics-api — o tópico `audit.access` *(VOZ-36, 2026-09-18)*

A gravação da chamada é servida pelo channel-gateway (onde o arquivo está), e cada escuta,
exportação e recusa tem de estar nesta trilha. Em vez de uma segunda escritora no ClickHouse, o
gateway publica **`audit.access`** (`AuditAccessEventSchema`, `.strict()`: quem acessou o quê, nunca
o conteúdo) e o consumer da analytics-api grava a linha (`parse_audit_access_event`; evento
incompleto é recusado com log, nunca completado com valor inventado). `access_id` = `event_id` do
produtor; `endpoint` = `channel-gateway:recording.listen|export`; `target_kind` = `recording`;
`target_id` = `{session_id}/{file_id}`. O ator sem credencial fica `anonymous`, como aqui.

⚠️ Kafka é *at-least-once*, e a tabela não deduplica por design: uma reentrega duplica a linha.
Para contar escutas, a chave é `access_id`, não a linha.

### ClickHouse — uma tabela existe, a outra não, e isso é decisão

`audit_access_log` é `MergeTree` e **nunca** deduplicado, por design LGPD: o valor da trilha é dizer
**quantas vezes** um dado foi acessado e por quem.

**`mcp_audit_log` NÃO existe e não foi criada de propósito** — medido zero tráfego na borda `invoke`
neste ambiente (`session_timeline` recebe linha de um único parser, o de `mcp.audit`, e está vazia),
e criar tabela que ninguém preenche é o *"existe ≠ está pronto"* de novo. Dívida dormente registrada
no `TODO.md`. `parse_mcp_audit_event()` grava **uma** linha, em `session_timeline`, que é de onde
`/v1/audit/mcp-calls` lê.

### platform-ui

`AuditPage` em `/audit` — 5 abas (Sessions + MCP Calls ativos; 3 stubs). Entrada de nav standalone
"Auditoria LGPD" (🔍) com gate ABAC `audit.sessions`. Banner de aviso: todo acesso é registrado.
