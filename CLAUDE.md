# PlugHub Platform — Global Architectural Context

## What PlugHub is

PlugHub is an enterprise orchestration platform that connects agents —
human and AI, from any origin — to business systems and customers,
with measurable quality and without creating lock-in. It is the infrastructure
that makes service delivery possible, not the delivery itself.

Full architectural specification: `plughub_spec_v1.docx` (root of this repo).

## Unified Session Model

Every contact is a conference room. There is no distinction between a normal flow
and a conference flow — the logic is singular. The Core creates the session on every
new contact; agents simply join the room with their queues and receive messages
according to their configured visibility options.

### Participant roles

| Role | Description |
|---|---|
| `primary` | Main agent responsible for the interaction |
| `specialist` | Invited expert (task step, assist mode) |
| `supervisor` | Human or AI supervisor monitoring the session |
| `evaluator` | Quality agent evaluating the session (online or post-session) |
| `reviewer` | Human agent reviewing the evaluator's output |

### Session status

| Status | Description |
|---|---|
| `active` | Session in progress with at least one participant |
| `closed` | Session ended normally |
| `abandoned` | No agent joined before the session ended |

### close_reason domain

```
no_resource          — no agents available and no queue configured
max_wait_exceeded    — max queue wait time exceeded
customer_disconnect  — client disconnected (connection_lost)
customer_hangup      — client ended actively (voice/video)
customer_abandon     — client left before being served
flow_complete        — Skill Flow complete step
agent_transfer       — transferred to another pool
agent_hangup         — agent ended actively
session_timeout      — session inactive beyond TTL
system_error         — unrecoverable error
```

### Message visibility

Three distinct modalities — not complementary:

| Visibility | Recipients | Typical use |
|---|---|---|
| `all` | All participants including the customer | Normal service message |
| `agents_only` | All agents, without the customer | Internal note between agents |
| `["part_abc", "part_xyz"]` | Only the listed participant_ids | Supervisor → specific agent, private, without other agents seeing |

## Invariants — never violate

- **AI Gateway is stateless** — processes one turn per LLM call. No state between turns.
- **Routing Engine is the sole arbiter** — no component routes a conversation without going through it.
- **MCP is the only integration protocol** — no direct REST between internal components.
- **pipeline_state persists to Redis on every step transition** — never in memory only.
- **Agent contract**: `agent_login` → `agent_ready` → `agent_busy` → `agent_done`
- **`agent_done` requires `handoff_reason`** when `outcome !== "resolved"`
- **`issue_status` is always required and never empty** in `agent_done`
- **Agents never access backend systems directly** — only via authorised MCP Servers
- **All domain MCP calls are intercepted** — native agents via `McpInterceptor` (in-process, `@plughub/sdk`); external agents via proxy sidecar (`plughub-sdk proxy` on localhost:7422). No MCP call reaches a domain server without permission validation, injection guard, and audit.
- **`insight.historico.*` persists via Kafka, never direct PostgreSQL write** — `insight_register` publishes `insight.registered` to `conversations.events`; a consumer promotes `insight.conversa.*` → `insight.historico.*` on `contact_closed`. Persistence boundary is the contact, not the agent session.

## MCP interception — hybrid proxy model

Domain MCP Servers (mcp-server-crm, mcp-server-telco, etc.) are separate from
mcp-server-plughub and are operated by the tenant. All calls to them must be
intercepted for permission validation and audit:

| Agent type | Interception mechanism | Network hop |
|---|---|---|
| Native agent (uses SDK) | `McpInterceptor` in-process (`@plughub/sdk`) | None |
| External agent (LangGraph, CrewAI) | `plughub-sdk proxy` sidecar on localhost:7422 | Loopback only |

Both paths perform the same checks per call (< 1ms total overhead):
1. **Permission validation** — `permissions[]` decoded from JWT locally (no network)
2. **Injection guard** — heuristic regex against 13 prompt injection patterns
3. **Audit record** — `AuditRecord` written async to Kafka topic `mcp.audit` (fire-and-forget)

### McpInterceptor (in-process — `@plughub/sdk`)

```typescript
const interceptor = new McpInterceptor({
  getSessionToken: () => lifecycle.currentToken,   // refreshed automatically
  delegate: (server, tool, args) => mcpClient.callTool(server, tool, args),
  kafka_brokers: ["kafka:9092"],
})
interceptor.start()

// In agent handler — replaces direct MCP client calls:
const result = await interceptor.callTool("mcp-server-crm", "customer_get", { customer_id })
```

Throws `McpInterceptorError` with `code: "PERMISSION_DENIED"` or `"INJECTION_DETECTED"`.
Call-level audit enrichment via `opts.audit_context`.

### Proxy sidecar (external agents — `plughub-sdk proxy`)

```bash
PLUGHUB_SESSION_TOKEN=<jwt> plughub-sdk proxy --config proxy_config.yaml
```

`proxy_config.yaml`:
```yaml
port: 7422
session_token_env: PLUGHUB_SESSION_TOKEN
kafka_brokers: ["kafka:9092"]          # omit for stdout fallback (dev)
audit_topic: mcp.audit
routes:
  mcp-server-crm:     ${MCP_CRM_URL}
  mcp-server-billing: ${MCP_BILLING_URL}
```

Path: `POST /mcp-server-crm/mcp` → forwards to `${MCP_CRM_URL}/mcp`.
Returns `403` on permission denied or `400` on injection detected (never forwards).

### AuditRecord (Kafka topic `mcp.audit`)

```typescript
AuditRecord {
  event_type: "mcp.tool_call"
  timestamp, tenant_id, session_id, instance_id
  server_name, tool_name
  allowed: boolean              // false = blocked before forwarding
  permissions_checked: string[] // permissions[] from JWT
  injection_detected: boolean
  injection_pattern?: string    // pattern_id if detected
  duration_ms: number           // 0 if blocked pre-forward
  data_categories?: DataCategory[]
  input_snapshot?: unknown      // only when audit_policy.capture_input = true
  output_snapshot?: unknown     // only when audit_policy.capture_output = true
  audit_context?: { reason?, correlation_id? }
  source: "in_process" | "proxy_sidecar"
}
```

The proxy sidecar validates `permissions[]` from the session_token JWT locally
(no network call, ~0.1ms) and writes audit events asynchronously to a local buffer
drained by a background thread to Kafka. Total overhead per MCP call: **< 1ms**.

### Audit policy — per tool, not per call

Each tool defines its own audit policy. The caller cannot opt out of audit records
(LGPD risk). The caller may only enrich with additional context:

```typescript
// On the tool definition (not per call)
audit_policy: {
  data_categories: DataCategory[]
  capture_input: boolean
  capture_output: boolean
  retention_days: number
  requires_consent: boolean
}

// Caller may add (never suppress)
audit_context?: { reason?: string, correlation_id?: string }
```

## Repository structure

```
plughub/
  CLAUDE.md                      ← this file
  plughub_spec_v1.docx           ← full architectural specification
  packages/
    schemas/                     ← @plughub/schemas — source of truth for contracts
    sdk/                         ← @plughub/sdk — integration SDK (TypeScript + Python)
    mcp-server-plughub/          ← mcp-server-plughub — Agent Runtime and BPM tools
    skill-flow-engine/           ← @plughub/skill-flow — Skill Flow interpreter
    ai-gateway/                  ← @plughub/ai-gateway — LLM calls and context extraction
    agent-registry/              ← @plughub/agent-registry — administrative API
    routing-engine/              ← @plughub/routing-engine — agent allocation
    rules-engine/                ← @plughub/rules-engine — monitoring and escalation
    channel-gateway/             ← @plughub/channel-gateway — channel adapters and inbound normalisation
```

## Stack per package

| Package | Language | Runtime | Notes |
|---|---|---|---|
| schemas | TypeScript | Node 20+ | Zod 3.23+ |
| sdk | TypeScript + Python | Node 20+ / Python 3.11+ | Two parallel packages |
| mcp-server-plughub | TypeScript | Node 20+ | Official Anthropic MCP SDK |
| skill-flow-engine | TypeScript | Node 20+ | State graph interpreter |
| ai-gateway | Python | Python 3.11+ | FastAPI + Anthropic SDK |
| agent-registry | TypeScript | Node 20+ | PostgreSQL + Prisma |
| routing-engine | Python | Python 3.11+ | Redis + Kafka |
| rules-engine | Python | Python 3.11+ | Redis + ClickHouse |
| channel-gateway | Python | Python 3.11+ | FastAPI + aiokafka + channel adapters |

## Package dependencies

```
schemas         ← base — no internal dependencies
sdk             ← depends on: schemas
mcp-server      ← depends on: schemas
skill-flow      ← depends on: schemas, mcp-server
ai-gateway      ← depends on: schemas
agent-registry  ← depends on: schemas
routing-engine  ← depends on: schemas, agent-registry
rules-engine    ← depends on: schemas, routing-engine
channel-gateway ← depends on: schemas   (no dependency on skill-flow or ai-gateway)
```

Never create circular dependencies. `schemas` never depends on any other package.

## Component responsibilities (summary)

| Component | Sole responsibility |
|---|---|
| **Core** | Session lifecycle, canonical stream, message masking, adapter coordination |
| **Channel Gateway** | Inbound normalisation, outbound rendering, fallback interaction collection, multi-site heartbeat |
| **AI Gateway** | Stateless LLM inference. Does not manage session or history. |
| **Agent Registry** | CRUD for AgentType, Pool, Skill, GatewayConfig. Cache invalidation via Kafka. |
| **Routing Engine** | Agent allocation, queue management, scoring algorithm, close_reason detection |
| **Rules Engine** | Post-routing event evaluation. Publishes consequences. No routing, no Redis polling. |
| **Skill Flow Engine** | Flow interpreter. Persists pipeline_state to Redis on every step. |

## Channel vs Medium

- **channel** = specific channel (`whatsapp`, `webchat`, `voice`, `email`, `sms`, `instagram`, `telegram`, `webrtc`) — **hard filter** for routing, mandatory match
- **medium** = base type (`voice`, `video`, `message`, `email`) — **score factor**, fine-tuning only

## Canonical stream

`session:{id}:stream` is the single source of truth for all session events.

```
StreamEventType:
  session_opened | session_closed
  participant_joined | participant_left
  customer_identified | medium_transitioned
  message | interaction_request | interaction_result
  flow_step_completed
```

Messages in the stream carry both `content` (masked, delivered to agents) and
`original_content` (unmasked, accessible only by authorised roles for LGPD audit).

## Sentiment tracking

Stored as a score-only array in Redis during the session. Labels are **not** stored — they are calculated at read time using tenant-configurable ranges. Persisted to PostgreSQL (`sentiment_timeline JSONB`) on `session_close`. Never published to the canonical stream.

```
session:{id}:sentiment → [
  { score:  0.40, timestamp: "..." },
  { score: -0.82, timestamp: "..." }
]
TTL: same as session TTL

# Ranges configurable per tenant (applied at read time):
[ 0.3,  1.0] → "satisfied"
[-0.3,  0.3] → "neutral"
[-0.6, -0.3] → "frustrated"
[-1.0, -0.6] → "angry"
```

## Skill Flow — nine step types

| Type | Does | Interacts with |
|---|---|---|
| `task` | Delegates to agent via A2A (`assist` or `transfer` mode) | Routing Engine |
| `choice` | Conditional branching via JSONPath | pipeline_state |
| `catch` | Retry and fallback before escalation | pipeline_state |
| `escalate` | Routes to pool | Rules Engine |
| `complete` | Closes with defined outcome | agent_done |
| `invoke` | Calls MCP tool directly | MCP Server |
| `reason` | Invokes AI Gateway with output_schema | AI Gateway |
| `notify` | Sends message to customer (unidirectional) | Core → Channel Gateway |
| `menu` | Captures customer input and suspends until reply | Core → Channel Gateway |

### task step modes

| Mode | Mechanism | Description |
|---|---|---|
| `assist` | `session_invite` | Specialist joins as parallel participant |
| `transfer` | `session_escalate` | Full handoff to another agent/pool |

### menu step — interaction modes

| Interaction | Result type | Channels (native) | Fallback |
|---|---|---|---|
| `text` | `string` | All | — |
| `button` | `string` (option id) | WhatsApp (≤3), webchat | Numbered text |
| `list` | `string` (option id) | WhatsApp, webchat | Numbered text |
| `checklist` | `string[]` | Webchat | Comma-separated numbers |
| `form` | `object` | Webchat | Sequential field-by-field |

`timeout`: `0` = immediate return, `>0` = block N seconds, `-1` = block indefinitely.

Fallback collection for unsupported channels happens exclusively in the Channel Gateway
adapter. Skill Flow always receives a single normalised `interaction_result`.

## Routing algorithm — key rules

1. **channel is a hard filter** — allocating an agent that does not support the contact's channel is forbidden
2. **agent pause is a hard filter** — paused agents are excluded from allocation
3. **gateway heartbeat TTL** — agents on gateways with expired heartbeat (>90s) are excluded
4. **SLA lazy evaluation** — `min(wait_time / sla_target, max_score)` calculated only at routing time for the queue head, never periodically
5. **Tie-breaking** — equal-score pools are broken by shortest queue length
6. **close_reason detection** — `no_resource` when no queue configured; `max_wait_exceeded` by lazy evaluation at queue head on every routing event

## Rules Engine — scope

- Consumes: `conversations.routed`, `conversations.queued`, `conversations.abandoned`, `agent.done`
- Publishes: `rules.escalation_triggered`, `rules.notification_triggered`, `rules.session_tagged`
- **Does NOT**: monitor Redis, evaluate sentiment, make routing decisions, maintain state between events

## Kafka topics

| Topic | Producer | Consumer(s) |
|---|---|---|
| `conversations.inbound` | Channel Gateway | Core, Routing Engine |
| `conversations.routed` | Routing Engine | Core, Rules Engine |
| `conversations.queued` | Routing Engine | Rules Engine |
| `conversations.dequeued` | Routing Engine | Rules Engine |
| `conversations.abandoned` | Routing Engine | Core, Rules Engine |
| `conversations.session_opened` | Core | Analytics, LGPD |
| `conversations.session_closed` | Core | Analytics, LGPD |
| `conversations.message_sent` | Core | Analytics |
| `rules.escalation_triggered` | Rules Engine | Routing Engine |
| `rules.notification_triggered` | Rules Engine | Core |
| `rules.session_tagged` | Rules Engine | Agent Registry |
| `registry.changed` | Agent Registry | Routing Engine, Core |
| `gateway.heartbeat` | Channel Gateway | Routing Engine |
| `agent.done` | Routing Engine | Rules Engine, Analytics |
| `queue.position_updated` | Routing Engine | Channel Gateway, Analytics |
| `mcp.audit` | McpInterceptor / proxy sidecar | Analytics, LGPD |
| `sentiment.updated` | AI Gateway (`sentiment_emitter.py`) | analytics-api (Arc 3) |

## Naming conventions

```
skill_id:       skill_{name}_v{n}      →  skill_portabilidade_telco_v2
agent_type_id:  {name}_v{n}            →  agente_retencao_v1
pool_id:        snake_case no version  →  retencao_humano
mcp_server:     mcp-server-{name}      →  mcp-server-crm
tool:           snake_case             →  customer_get
insight:        insight.historico.*    →  customer long-term memory
                insight.conversa.*     →  generated in current session, expires on close
outbound:       outbound.*             →  pending deliveries for Notification Agent
```

## What never to do

- Never create a component that routes conversations without going through the Routing Engine
- Never access Redis directly from outside routing-engine or skill-flow-engine
- Never redefine types from `@plughub/schemas` locally in another package
- Never add business logic to mcp-server-plughub — it only exposes tools
- Never create a dependency on `ai-gateway` in TypeScript packages — only Python consumes it
- Never use `export *` in packages — always explicit named exports
- Never implement channel-specific rendering logic in skill-flow — channel adapters live exclusively in channel-gateway
- Never put form field validation (business rules) inside the `menu` step — validation belongs to subsequent steps
- Never allow a caller to opt out of MCP audit records — audit policy is defined on the tool, not the call
- Never write to `insight.historico.*` directly in PostgreSQL — always via Kafka
- Never inject context into Skill Flow automatically — the caller passes `contact_context` explicitly
- Never expose `original_content` of masked messages to agents — only to authorised roles via audit trail
- Never forward tool calls containing injection patterns — `injection_guard.ts` must be applied before any free-text field reaches a domain MCP server
- Never send tool list to LLM without applying `permissions[]` filter from the JWT — tools not in `permissions` are invisible to the agent

## SDK CLI

```bash
plughub-sdk certify            # validates execution contract
plughub-sdk verify-portability # verifies dependency isolation
plughub-sdk regenerate         # regenerates proprietary agent as native
plughub-sdk skill-extract      # extracts skill from existing agent
```

## Operational visibility — section 3.3c

Routing Engine writes a pool snapshot to Redis after every routing event:
```
Key:  {tenant_id}:pool:{pool_id}:snapshot  TTL: 120s
Value: { pool_id, tenant_id, available, queue_length, sla_target_ms, channel_types, updated_at }
```

Three MCP tools (group `operational`) read these snapshots:

| Tool | Purpose |
|---|---|
| `queue_context_get` | Queue position + estimated wait for a queued session |
| `pool_status_get` | Pool availability: agents ready, queue depth, SLA target |
| `system_availability_check` | Cross-channel availability for offering channel switch |

When a contact is queued (no agent available), Routing Engine publishes `queue.position_updated` to Kafka with payload:
```json
{ "event": "queue.position_updated", "session_id", "pool_id", "queue_length",
  "available_agents", "estimated_wait_ms", "sla_target_ms", "published_at" }
```

Estimated wait = `queue_length × (sla_target_ms × 0.7)` — conservative p70 handle-time estimate.

## Security — section 9.5

### Tool permission filtering (AI Gateway)

`InferenceRequest` accepts an optional `permissions: list[str]` field populated from the session JWT.
When non-empty, `InferenceEngine.infer()` filters the `tools` list to only tools whose `name` appears in `permissions` before forwarding to the LLM. Empty list = no filtering (backward-compatible).

### Prompt injection guard (`injection_guard.ts`)

Applied in `mcp-server-plughub` before free-text fields reach domain MCP Servers.
Heuristic regex catalogue (13+ patterns) covering: override/ignore instructions, role hijack, persona pretend, system prompt leak, DAN patterns, developer-mode activation.
`assertNoInjection(toolName, input)` throws with `code: "INJECTION_DETECTED"` on match.
Currently applied in: `notification_send` (message), `conversation_escalate` (pipeline_state).
Future: apply at the PlugHubAdapter / proxy sidecar level for all domain tool calls.

## Message masking — tokenização com partial display

Implementado em `mcp-server-plughub`. ADR completo: `docs/adr/adr-message-masking.md`.

### Token format no stream

```
[{category}:{token_id}:{display_partial}]

[credit_card:tk_a8f3:****1234]           → AI confirma "final 1234" com o cliente
[cpf:tk_b7d2:***-00]                     → AI confirma "termina em 00"
[phone:tk_c1e9:(11) ****-4321]
[email_addr:tk_d4f0:j***@empresa.com]
```

### Componentes

| Arquivo | Responsabilidade |
|---|---|
| `schemas/audit.ts` | `MaskingAccessPolicySchema`, `DEFAULT_MASKING_RULES`, `preserve_pattern` em `MaskingRule` |
| `schemas/message.ts` | `MessageSchema` inclui `original_content: MessageContentSchema.optional()` — campo preservado pelo `SessionContextSchema.parse()` para roles autorizados |
| `mcp-server/lib/token-vault.ts` | Redis token store/resolve — key `{tenant_id}:token:{token_id}`, TTL = sessão |
| `mcp-server/lib/masking.ts` | `MaskingService.applyMasking`, `canReadOriginalContent`, `loadConfig`, `loadAccessPolicy` |
| `mcp-server/tools/session.ts` | `message_send` aplica mascaramento; `session_context_get` monta mensagens completas do stream (event_id→message_id, timestamp, author, visibility + payload) e filtra `original_content` por role |

### Controle de acesso ao `original_content`

- `MaskingAccessPolicy` por tenant — Redis key: `{tenant_id}:masking:access_policy`
- Default: `authorized_roles: ["evaluator", "reviewer"]`
- `primary` e `specialist` recebem token com partial display — operam via MCP Tools
- MCP Tools de domínio resolvem `token_id` → valor via `TokenVault.resolve()`

### Pendente neste módulo

- Token resolution em MCP Tools de domínio (`mcp-server-crm`, etc.)
- Channel Gateway: exibir só `display_partial` (sem wrapper `[...]`) para o cliente
- Masking config UI no Agent Registry

## Session Replayer — avaliação de qualidade pós-sessão

Implementado em `packages/session-replayer/`. ADR completo: `docs/adr/adr-session-replayer.md`.

### Padrão: ensure-before-read com Hydrator opcional

```
conversations.session_closed
  → Stream Persister (PostgreSQL)
  → evaluation.requested
      → Stream Hydrator  (Redis hit: no-op | Redis miss: reconstrói do PG)
      → Replayer         (sempre lê Redis)
          → ReplayContext em {tenant_id}:replay:{session_id}:context  TTL: 1h
          → Evaluator agent: evaluation_context_get → evaluation_submit
          → evaluation.events (Kafka) → consumer → PostgreSQL
```

### Componentes

| Módulo | Responsabilidade |
|--------|-----------------|
| `stream_persister.py` | `session_closed` → `session_stream_events` (PostgreSQL) |
| `stream_hydrator.py`  | `ensure(session_id)` — Redis hit: no-op; Redis miss: PG → Redis |
| `replayer.py`         | Lê Redis, calcula `delta_ms`, escreve `ReplayContext` |
| `consumer.py`         | Kafka: persister (session_closed) + replayer (evaluation.requested) |
| `evaluation_context_get` | MCP Tool — evaluator lê `ReplayContext` (inclui `original_content`) |
| `evaluation_submit`   | MCP Tool — publica `EvaluationResult` em `evaluation.events` |

### Componentes adicionais (Comparison Mode)

| Módulo | Responsabilidade |
|--------|-----------------|
| `comparator.py` | Jaccard similarity turn-a-turn, produz `ComparisonReport` — sem I/O |
| `ReplayContext.comparison_mode` | Flag que sinaliza ao evaluator para fornecer `comparison_turns` |
| `evaluation_submit.comparison_turns` | Input opcional com pares (production_text, replay_text) |
| `buildComparisonReport()` | Função TypeScript inline em `evaluation.ts` — computa similarity + deltas |

### Schemas novos em `@plughub/schemas`

`EvaluationDimension`, `EvaluationResult`, `ReplayEvent`, `ReplayContext`,
`EvaluationRequest`, `ComparisonReport`

### Comparison Mode — fluxo completo

```
ReplayContext.comparison_mode: true
  → evaluator recebe flag via evaluation_context_get
  → evaluator gera comparison_turns: [{turn_index, production_text, replay_text, latency_ms?}]
  → evaluation_submit(comparison_turns, comparison_replay_outcome?, comparison_replay_sentiment?)
      → buildComparisonReport() — Jaccard, divergence_points (threshold=0.4), deltas
      → EvaluationResult.comparison = ComparisonReport
      → event_type: "evaluation.completed" publicado com .comparison presente
  → resultado retorna comparison_included: true
```

### Jaccard similarity

Coeficiente J(A,B) = |A ∩ B| / |A ∪ B| sobre tokens normalizados (lowercase, sem pontuação).
Sem dependências externas. Determinístico. Threshold default: 0.4.
Casos especiais: ambos vazios → 1.0; um vazio → 0.0.

### Timing fiel

`ReplayEvent.delta_ms` preserva o intervalo original entre eventos.
`speed_factor` escala o timing: `1.0` = real-time, `10.0` = default batch.

### Tests

- `session-replayer/tests/test_comparator.py` — 22 unit tests (pytest): Jaccard, compare, deltas, to_dict, threshold inválido

## Usage Metering — metering ≠ pricing

Implementado em `packages/usage-aggregator/`. Princípio: cada componente registra o que consumiu;
um módulo de pricing separado (a construir) lê esses dados e decide o que cobrar.

### Tópico Kafka: usage.events

Schema em `@plughub/schemas/usage.ts` — `UsageEventSchema`. Campos: `event_id`, `tenant_id`,
`session_id`, `dimension`, `quantity`, `timestamp`, `source_component`, `metadata`.
Sem `unit_price_cents` ou `plan_id` — esses campos pertencem ao módulo de pricing.

### Dimensões implementadas

| Dimensão | Unidade | Publicado por |
|---|---|---|
| `sessions` | por sessão atendida | Core (`agent_busy`) — guard SET NX anti-duplicata |
| `messages` | por mensagem `visibility: "all"` | Core (`message_send`) |
| `llm_tokens_input` | tokens de prompt | AI Gateway (`inference.py`) |
| `llm_tokens_output` | tokens de resposta | AI Gateway (`inference.py`) |
| `whatsapp_conversations`, `voice_minutes`, `sms_segments`, `email_messages` | por canal | Channel Gateway (pendente) |

### Componentes

| Arquivo | Responsabilidade |
|---|---|
| `schemas/usage.ts` | `UsageEventSchema`, `QuotaLimitSchema`, `UsageCycleResetSchema` + schemas de metadata por dimensão |
| `mcp-server/lib/usage-emitter.ts` | `emitSessionOpened`, `emitMessageSent` — fire-and-forget via Kafka |
| `ai-gateway/usage_emitter.py` | `emit_llm_tokens` — dois eventos separados (input/output) por inferência |
| `providers/base.py` | `LLMResponse` com `input_tokens` e `output_tokens` |
| `usage-aggregator/aggregator.py` | `UsageAggregator.process()` — INCRBY Redis + INSERT PostgreSQL |
| `usage-aggregator/consumer.py` | Kafka consumer `usage.events` + `_ensure_schema()` para DDL |
| `mcp-server/lib/quota-check.ts` | `assertQuota` (INCRBY-check-rollback) + `checkConcurrentSessions` |

### Redis keys de metering

| Chave | Conteúdo | TTL |
|---|---|---|
| `{t}:usage:current:{dimension}` | Counter INCRBY por ciclo | 45 dias |
| `{t}:usage:cycle_start` | ISO 8601 início do ciclo | 45 dias |
| `{t}:quota:limit:{dimension}` | Limite operacional (escrito pelo operador ou pricing) | sem TTL |
| `{t}:quota:max_concurrent_sessions` | Limite de sessões simultâneas | sem TTL |
| `{t}:quota:concurrent_sessions` | Gauge atual (INCR/DECR pelo Core) | 6h |
| `{t}:usage:session:{session_id}:counted` | Guard de idempotência para `sessions` | 5h |

### Tests

- `usage-aggregator/tests/test_aggregator.py` — 10 unit tests (pytest): Redis INCRBY, idempotência, graceful degradation, `_truncate_to_hour`
- `mcp-server/src/__tests__/quota-check.test.ts` — 13 unit tests (vitest): `assertQuota` + `checkConcurrentSessions`
- `e2e-tests/scenarios/regressions.ts` — 2 regression cases documentados (R1: ZodError em `session_context_get`, R2: parsing de `callTool()`)

### Pendente neste módulo

- Publicação de `usage.events` no Channel Gateway (voice, WhatsApp, SMS)
- Módulo de pricing: lê contadores + aplica planos + escreve `{t}:quota:limit:*`
- `usage.cycle_reset` — reset mensal de contadores

## WebChat Channel — hybrid stream model

Implementado em `packages/channel-gateway/`. Três canais distintos: `webchat`, `webrtc`, `whatsapp` — mantidos separados porque `channel` é filtro hard no roteamento.

### Protocolo WebSocket (typed envelope)

```
Cliente → Servidor
  conn.authenticate  {token, cursor?}   — primeira mensagem após conn.hello
  msg.text           {id, text}
  msg.image          {id, file_id, caption?}
  msg.document       {id, file_id, caption?}
  msg.video          {id, file_id, caption?}
  upload.request     {id, file_name, mime_type, size_bytes}
  menu.submit        {menu_id, interaction, result}
  conn.ping                             — keepalive do cliente

Servidor → Cliente
  conn.hello         {server_version}   — imediato após accept
  conn.authenticated {contact_id, session_id, stream_cursor}
  conn.error         {code, message}    — falha de autenticação
  conn.pong                             — resposta ao conn.ping
  upload.ready       {request_id, file_id, upload_url}
  upload.committed   {file_id, url, mime_type, size_bytes, content_type}
  msg.text / msg.image / msg.document / msg.video  — entrega do stream
  interaction.request {menu_id, interaction, prompt, options?, fields?}
  presence.typing_start  {participant_id, role}
  presence.agent_joined  {participant_id, role}
  conn.session_ended {reason}
```

Token (JWT HS256) vai no corpo da mensagem — nunca na URL (evita logs de acesso).

### Hybrid stream model — por que não participante nomeado

O cliente webchat NÃO é registrado como participante na sessão. Em vez disso, o Channel Gateway faz XREAD bloqueante direto no `session:{id}:stream`. Vantagens:
- Reconnect por cursor: `XRANGE session:{id}:stream {cursor} +` — zero mensagens perdidas
- Sem propagação de role `customer` por todas as MCP Tools
- Sem complexidade de multi-tab (cada tab tem cursor próprio)
- Typing indicators efêmeros ficam no pub/sub `session:{id}:typing` — não poluem o stream

### WebchatAdapter — três tasks concorrentes

```python
receive_task  = _receive_loop()         # inbound do cliente → conversations.inbound
delivery_task = _stream_delivery_loop() # XREAD session stream → ws.send_json
typing_task   = _typing_listener()      # pub/sub typing → presence.*
asyncio.wait({receive, delivery, typing}, FIRST_COMPLETED) → cancel outros → _close
```

### Upload de arquivos — dois estágios

```
1. WS:  upload.request {file_name, mime_type, size_bytes}
2. WS:  upload.ready   {request_id, file_id, upload_url}
3. HTTP: POST /webchat/v1/upload/{file_id} (binary)
4. WS:  upload.committed {file_id, url, content_type}
5. WS:  msg.image|document|video {file_id, caption?}
```

### AttachmentStore — interface estável

| Fase | Implementação | Storage |
|---|---|---|
| Fase 1 | `FilesystemAttachmentStore` | Disco local + PostgreSQL (metadata) |
| Fase 2 | `S3AttachmentStore` | S3/MinIO (interface inalterada) |

Path: `{STORAGE_ROOT}/{tenant_id}/{YYYY}/{MM}/{DD}/{session_id}/{file_id}.{ext}`

MIME allowlist: image/jpeg, image/png, image/webp, image/gif (16 MB), application/pdf (100 MB), video/mp4, video/webm (512 MB).

Cron de expurgo dois estágios: Estágio 1 (horário) soft-delete; Estágio 2 (diário, grace 24h) delete físico.

### Rotas HTTP

| Rota | Descrição |
|---|---|
| `POST /webchat/v1/upload/{file_id}` | Recebe binário, chama `store.commit()`, envia `upload.committed` via WS |
| `GET  /webchat/v1/attachments/{file_id}` | Streaming do arquivo; 410 Gone se expirado |

### Tests

- `tests/test_webchat_adapter.py` — 28 testes pytest (auth handshake, lifecycle, text/media/upload/menu, heartbeat, close_from_platform)
- `tests/test_stream_subscriber.py` — 25 testes pytest (cursor tracking, filtro de visibilidade, mapeamento de todos os tipos de evento, resiliência a erros e cancelamento)
- `tests/test_attachment_store.py` — 30 testes pytest (validate_mime, reserve, commit, resolve, soft_expire, stream_bytes — asyncpg mockado, filesystem real via tmp_path)
- `tests/test_models.py` — 136 testes totais no pacote channel-gateway

### Usage Metering — Channel Gateway

Implementado em `usage_emitter.py`. Dimensões publicadas em `usage.events`:

| Dimensão | Quantidade | Publicado por | Quando |
|---|---|---|---|
| `whatsapp_conversations` | 1 por conversa | adapter WhatsApp (futuro) | contact_open |
| `voice_minutes` | ceil(segundos/60) | adapter WebRTC/Voice (futuro) | contact_close |
| `sms_segments` | 1 por segmento | adapter SMS (futuro) | inbound/outbound |
| `email_messages` | 1 por mensagem | adapter Email (futuro) | inbound/outbound |
| `webchat_attachments` | 1 por arquivo | `upload_router.py` | após store.commit() |

`webchat_attachments` é a única dimensão atualmente wired (commit de arquivo no upload flow).
As demais funções estão implementadas e documentadas, prontas para os adapters futuros.

Tests: `tests/test_usage_emitter.py` — 22 testes (todas as dimensões + error path).

### Novas dependências

`PyJWT>=2.8.0`, `asyncpg>=0.29.0`, `aiofiles>=23.2.1`

### Novos campos em Settings

| Campo | Padrão | Descrição |
|---|---|---|
| `jwt_secret` | `changeme_...` | Segredo HS256 para validar tokens de cliente |
| `ws_auth_timeout_s` | `30` | Timeout para receber conn.authenticate |
| `storage_root` | `/var/plughub/attachments` | Raiz dos arquivos de upload |
| `attachment_expiry_days` | `30` | TTL dos uploads |
| `database_url` | `postgresql://...` | DSN PostgreSQL para metadados |
| `webchat_serving_base_url` | `http://localhost:8010/...` | URL pública de download |
| `webchat_upload_base_url` | `http://localhost:8010/...` | URL de upload HTTP |

### Reconexão — casos pendentes (fase 2)

- **Stream TTL expirado pós-session_ended**: se o Redis stream expirou (TTL do `session:{id}:stream` venceu após a sessão ser encerrada), o XREAD em stream inexistente retorna `nil` e o adapter fica aguardando eventos que nunca chegam. Solução: ao iniciar o `StreamSubscriber`, verificar se o stream existe (`XLEN`); se não existir, enviar `conn.session_ended` imediatamente e fechar a conexão.
- **jwt_secret por tenant**: fase 1 usa segredo único por instância (`settings.jwt_secret`). Produção multi-tenant exige secret por tenant ou validação via JWKS endpoint.

## Pending (next iteration)

### Arc 2 — fechamento

- ~~E2E scenario 12: webchat auth flow + media upload end-to-end~~ ✅
- ~~Usage Metering no Channel Gateway (voice_minutes, whatsapp_conversations, sms_segments)~~ ✅
- WebChat reconexão fase 2: tratar stream TTL expirado + jwt_secret por tenant
- AttachmentStore fase 2: S3/MinIO
- Magic bytes validation no upload (phase 2)
- Pricing Module v1: planos, tarifas, ciclo de billing

### Arc 3 — Analytics, Dashboard Operacional e Relatórios

**Dependência prévia:** ~~AI Gateway deve publicar `sentiment.updated` no Kafka antes da analytics-api poder agregar sentimento por pool em real-time.~~ ✅ Implementado: `sentiment_emitter.py` publica `sentiment.updated` no Kafka e mantém `{tenant_id}:pool:{pool_id}:sentiment_live` no Redis após cada turno LLM.

**Novos pacotes:**
- `packages/analytics-api/` — consumer Kafka→ClickHouse, API REST, SSE
- `packages/operator-console/` — React app: heatmap, drill-down, intervenção

**Tasks:**

1. ~~**AI Gateway — publicar sentiment.updated**~~: ✅ `sentiment_emitter.py` — `emit_sentiment_updated` (Kafka topic `sentiment.updated`) + `update_sentiment_live` (Redis hash `{tenant_id}:pool:{pool_id}:sentiment_live`, TTL 300s, avg_score + distribuição por categoria). Wired em `SessionManager.update_partial_params`. Tests: `test_sentiment_emitter.py` (41 assertions).

2. ~~**analytics-api — consumer + ClickHouse schema**~~: ✅ `packages/analytics-api/` — 6 tabelas ClickHouse (`sessions`, `queue_events`, `agent_events`, `messages`, `usage_events`, `sentiment_events`), todas `ReplacingMergeTree` para idempotência. Consumer multi-topic (8 tópicos) com commit manual após batch. Parsers por topic (models.py). ClickHouse + analytics-api adicionados ao docker-compose.test.yml. Tests: `test_consumer.py` (30 assertions).

3. ~~**analytics-api — endpoints dashboard**~~: ✅ `GET /dashboard/operational` (SSE, Redis snapshots, 5s interval, `event: pools`), `GET /dashboard/metrics` (ClickHouse últimas 24h — sessions/agent_events/usage/sentiment agregados, retorna 503 em erro), `GET /dashboard/sentiment` (Redis `sentiment_live` por pool). Query helpers em `query.py`: `get_metrics_24h` (4 queries CH, `asyncio.to_thread`), `get_pool_snapshots` (scan+mget), `get_sentiment_live` (scan+hgetall). Tests: `test_dashboard.py` (18 assertions).

4. ~~**analytics-api — endpoints reports + BI export**~~: ✅ `GET /reports/sessions`, `/reports/agents`, `/reports/quality`, `/reports/usage`. Filtros opcionais por endpoint (channel, outcome, close_reason, pool_id, agent_type_id, event_type, dimension, source_component, category). Paginação (`page`, `page_size`): max 1000 JSON / 10000 CSV. `format=csv` retorna `text/csv` com `Content-Disposition: attachment`. Helpers em `reports_query.py` (`asyncio.to_thread`, count + data query, `_to_csv`). Tests: `test_reports.py` (26 assertions).

5. ~~**analytics-api — camada admin consolidada**~~: ✅ `GET /admin/consolidated` com agregação cross-tenant por canal e por pool; RBAC: tenant operator vê apenas `tenant_id = X`, admin vê tudo. Auth Bearer JWT HS256 (`admin_jwt_secret`). `Principal.effective_tenant()` aplica o filtro correto por role. `admin_query.py`: 3 queries CH (`by_channel` com breakdown por outcome, `by_pool` sessions + sentinel overlay de `sentiment_events`). Tests: `test_admin.py` (21 assertions — `TestPrincipal`, `TestRequirePrincipal`, `TestQueryConsolidated`).

6. ~~**operator-console fase 1 — heatmap + métricas realtime**~~: ✅ heatmap de sentimento por pool (tiles coloridos por avg_score, ordered worst-first), painel lateral com métricas do pool (available/queue/SLA/distribuição) e resumo 24h; atualização via SSE ~5s. `packages/operator-console/` — React 18 + TypeScript + Vite. Hooks: `usePoolSnapshots` (SSE EventSource), `useSentimentLive` (poll 10s), `useMetrics24h` (poll 60s), `usePoolViews` (merge). Componentes: `HeatmapGrid`, `PoolTile` (cor interpolada, badge SLA breach), `MetricsPanel` (pool detail + distribution bars + 24h summary), `Header` (tenant input, status dot). Build: `tsc -b && vite build` → 157 kB JS gzip 50 kB.

7. ~~**operator-console fase 2 — drill-down read-only**~~: ✅ pool → lista de sessões ativas → transcrição ao vivo. Backend: `sessions.py` em `analytics-api` — `GET /sessions/active` (ClickHouse `closed_at IS NULL` + Redis pipeline LRANGE sentiment, sorted worst-first) e `GET /sessions/{id}/stream` (SSE: evento `history` com XRANGE + eventos `entry` via XREAD bloqueante, keepalive 15s). Frontend: `useActiveSessions` (poll 10s), `useSessionStream` (EventSource SSE), `SessionList` (lista com accent colorido, badge de categoria, handle time, botão drill-in), `SessionTranscript` (auto-scroll, message bubbles diferenciados por role, system events com linha separadora, `agents_only` destacado, status dot ao vivo). `HeatmapGrid`/`PoolTile` atualizados com botão "sessions →" para drill-down. `App.tsx` refatorado para 3 níveis: heatmap → sessions → transcript. Build: 168 kB JS gzip 53 kB. Tests: `test_sessions.py` (39 assertions — TestClassify, TestSafeJson, TestParseEntry, TestFetchActiveSessions, TestOverlaySentiment, TestListActiveSessionsEndpoint). Total analytics-api: 134/134.

8. **operator-console fase 3 — intervenção ativa**: botão "entrar como supervisor" → agent_login → agent_ready → session_invite (role=supervisor); canal WebSocket bidirecional; mensagens `agents_only` e intervenção direta; auditoria via `mcp.audit`.

9. **Metabase setup**: configuração de conexão ao ClickHouse, queries base por tenant, sandboxing multi-tenant por `tenant_id`, dashboards de relatório self-service.

10. ~~**Config Management Module — separação env vars × configuração de módulo**~~: ✅ `packages/config-api/` com tabela PostgreSQL `platform_config (tenant_id, namespace, key, value JSONB, updated_at)` + API REST CRUD (`GET/PUT/DELETE /config/{namespace}/{key}`) + seed de todos os valores atuais hardcoded. Leitura com cache Redis (TTL 60s) para não adicionar latência no hot path. Fase 2: UI de visualização no operator-console.
    - **Dois níveis**: `tenant_id = '__global__'` para defaults de plataforma; tenant real para overrides específicos. Lookup: tenant wins over global.
    - **8 namespaces seedados**: `sentiment` (thresholds, live_ttl_s), `routing` (snapshot_ttl_s, sla_default_ms, score_weights, estimated_wait_factor, congestion_sla_factor), `session` (ai_gateway_ttl_s, channel_gateway_ttl_s), `consumer` (batch_size, timeout_ms, restart_delay_s, max_restart_delay_s), `dashboard` (sse_interval_s, sse_retry_ms), `webchat` (auth_timeout_s, attachment_expiry_days, upload_limits_mb), `masking` (authorized_roles, default_retention_days, capture_input_default, capture_output_default), `quota` (max_concurrent_sessions, llm_tokens_daily, messages_daily).
    - **`ConfigStore`**: `get()` (cache hit → DB miss), `get_or_default()`, `list_namespace()` (com cache de namespace), `list_all()`, `set()` (upsert + invalidação imediata), `delete()`. Invalidação global faz SCAN para limpar variantes de tenant.
    - Tests: `test_store.py` (27 assertions — TestConfigCache, TestConfigStoreGet, TestConfigStoreSet, TestConfigStoreDelete, TestConfigStoreList, TestSeedData).

**Arquitetura de dados:**
```
Kafka topics → analytics-api consumer → ClickHouse
  (conversations.*, agent.done, usage.events, queue.position_updated, sentiment.updated)

Redis snapshots + sentiment_live → analytics-api → SSE → operator-console
PostgreSQL (evaluation, sentiment_timeline) → analytics-api (queries pontuais)

ClickHouse → Metabase (relatórios self-service)
analytics-api REST → BI externos (PowerBI, Looker, Tableau)
```

## E2E test suite — scenarios

| Scenario | File | Coverage |
|---|---|---|
| 01 | `01_happy_path.ts` | agent lifecycle, skill flow, pipeline_state |
| 02 | `02_escalation_handoff.ts` | escalation, handoff |
| 03 | `03_resume_after_failure.ts` | resume from partial pipeline_state |
| 04 | `04_rules_engine.ts` | rules engine evaluation |
| 05 | `05_routing_latency.ts` | routing performance (--perf flag) |
| 06 | `06_conference.ts` | conference flow + reconnect resilience (--conference flag) |
| 07 | `07_inbound_full.ts` | full inbound flow: AI triage → escalate → human + conference + supervisor |
| 08 | `08_outbound.ts` | outbound contact: request → AI open → human close |
| 09 | `09_session_replayer.ts` | session replayer pipeline: session_closed → ReplayContext → evaluation_submit (11 assertions) |
| 10 | `10_masking.ts` | message masking: MaskingConfig → tokens inline → role-based original_content (9 assertions) |
| 11 | `11_comparison_mode.ts` | comparison mode: ReplayContext.comparison_mode → evaluation_submit com comparison_turns → ComparisonReport (12 assertions) |
| 12 | `12_webchat_channel.ts` | webchat channel: auth handshake WS, text message → Kafka, upload flow completo (upload.request→ready→HTTP→committed→msg.image), reconnect com cursor (14 assertions) |
| R  | `regressions.ts` | regression suite: ZodError em session_context_get, parsing de callTool (--regression flag) |

Run with: `ts-node runner.ts --conference` or `ts-node runner.ts --only 06` or `ts-node runner.ts --only 12` or `ts-node runner.ts --webchat`

Scenario 06 covers two parts:
- **Part A** — Conference happy path: primary agent busy → supervisor calls `agent_join_conference` → Redis `conference:*` keys verified → specialist `agent_done` with `conference_id` (session stays open) → primary `agent_done` closes session
- **Part B** — Reconnect resilience: agent busy → MCP transport torn down → new transport reconnected → re-login with same `instance_id` → Redis state (agent instance + active sessions) persists → `agent_done` with new session_token concludes session cleanly
