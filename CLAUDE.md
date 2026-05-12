# PlugHub Platform — Global Architectural Context

PlugHub is an enterprise orchestration platform that connects agents — human and AI, from any origin — to business systems and customers, with measurable quality and without creating lock-in. Full spec: `plughub_spec_v1.docx`.

---

## Saúde do CLAUDE.md — Regras de Manutenção

> **Target: ≤ 800 linhas.** Quando ultrapassar, aplicar as regras abaixo.

### O que FICA no CLAUDE.md

| Categoria | Critério |
|-----------|----------|
| Invariantes e regras | "never do X", contratos de componente, limites arquiteturais |
| Modelo de sessão e domínios | roles, status, close_reason, visibilidade de mensagens |
| Responsabilidades dos componentes | tabela de uma linha por componente |
| Stack por pacote | tabela compacta (linguagem, runtime, porta) |
| Estrutura do repositório | árvore de diretórios do nível `packages/` |
| Kafka topics | tabela de tópicos × producer × consumer |
| Convenções de nomenclatura | padrões de ID |
| Seções de arquitetura ativa | resumo de 15–20 linhas com link para `docs/arcos/` |
| Pending genuíno | máx 50 linhas — apenas itens não implementados |

### O que NÃO pertence ao CLAUDE.md

| Proibido | Vai para |
|----------|----------|
| Itens marcados com ✅ | `CHANGELOG.md` |
| Histórico de implementação (task #N, testes X/Y, build N kB) | `CHANGELOG.md` |
| Documentação completa de um Arc ou módulo (> 50 linhas) | `docs/arcos/{arc}.md` |
| Snippets de código longos (> 10 linhas) fora de invariantes | `docs/arcos/{arc}.md` |
| Detalhes de UI (props, componentes, hooks por feature) | `docs/arcos/{arc}.md` |
| "Pendente (fase 2)" que já foi implementado | Deletar |

### Estrutura de arquivos de referência

```
plughub/
  CLAUDE.md          ← arquitetura viva, regras, invariantes, resumos (≤ 800 linhas)
  TODO.md            ← itens genuinamente não implementados
  CHANGELOG.md       ← histórico de implementações concluídas
  docs/
    modulos/                  ← docs de páginas/features da UI (uma por rota)
    arcos/                    ← docs de implementação por Arc (detalhe técnico)
      arc4-workflow.md        ← Arc 4 completo (workflow, calendar, collect, webhooks)
      arc5-segments.md        ← Arc 5 ContactSegment analytics
      arc6-evaluation.md      ← Arc 6 Evaluation platform completo
      arc7-auth.md            ← Arc 7 Auth + ABAC completo
      arc8-agent-availability.md ← Arc 8 disponibilidade e pausas
      arc9-agent-groups.md    ← Arc 9 Agent Groups + Supervisor Scope
      arc10-journey.md        ← Arc 10 Journey multi-session
      instance-bootstrap.md   ← reconciliação, RegistrySyncer, hot-reload
      platform-ui.md          ← Frontend Architecture + Agent Assist UI
      ai-gateway.md           ← AI Gateway multi-account, copilot, stateless
      usage-metering.md       ← metering por dimensão, Redis, quota
      pricing.md              ← faturamento por capacidade, billing API
      session-replayer.md     ← Session Replayer, Hydrator, ReplayContext
      session-conference-lifecycle.md ← modelo de 3 camadas, gaps conhecidos
      dashboard.md            ← Dashboard #35, DisplayTool registry, catalog
    guias/
      context-store.md        ← ContextStore, @ctx.*, segment-scoped
      masked-input.md         ← Masked Input, begin_transaction
      mention-protocol.md     ← @mention protocol
      pool-hooks.md           ← Pool lifecycle hooks
    adr/
      adr-message-masking.md  ← masking architecture decision
      adr-webchat-channel.md  ← webchat channel architecture
      adr-session-replayer.md ← session replayer architecture
      adr-contact-segments.md ← Arc 5 architecture
      adr-instance-bootstrap.md
```

### Como adicionar uma nova feature

1. **Feature pequena** (< 20 linhas): inline na seção H2 existente mais próxima.
2. **Feature média** (20–50 linhas): subseção `###` dentro da seção H2 mais próxima.
3. **Feature grande** (> 50 linhas): criar `docs/arcos/{nome}.md`; adicionar resumo de 15–20 linhas aqui.
4. **Fase pendente concluída**: mover do `## Pending` para `CHANGELOG.md`; atualizar `TODO.md`; **nunca deixar ✅ aqui**.

### Regra de persistência de planejamento

| Tipo de decisão | Onde registrar imediatamente |
|---|---|
| Nova tarefa planejada | Task no tracker (`TaskCreate`) |
| Decisão técnica (> 3 linhas) | Entrada em `TODO.md` com raciocínio |
| Invariante ou regra arquitetural | Seção neste arquivo |
| Implementação concluída | `CHANGELOG.md` |

### Convenção de pastas de documentação

| Pasta | Conteúdo | Quando criar arquivo aqui |
|---|---|---|
| `docs/modulos/` | Docs de páginas e features da UI | Nova rota/módulo de interface |
| `docs/arcos/` | Docs de implementação por Arc | Arc novo ou refactoring de backend significativo |
| `docs/guias/` | Padrões transversais a múltiplos pacotes | Novo padrão (mascaramento, @mention, hooks, etc.) |
| `docs/adr/` | Decisões arquiteturais com trade-offs | Toda decisão estrutural relevante |
| `docs/pacotes/` | Contratos públicos de cada pacote | Novo pacote no monorepo |

### Regra de atualização de documentação

> Toda entrada em `CHANGELOG.md` deve ter um doc correspondente **criado ou atualizado** antes de ser considerada concluída. Se a feature afeta uma rota de UI → atualizar `docs/modulos/`. Se é um Arc ou backend significativo → atualizar ou criar `docs/arcos/`. Se é um padrão transversal → atualizar `docs/guias/`.

---

## Unified Session Model

Every contact is a conference room. Core creates the session on every new contact; agents join the room with their queues and receive messages according to visibility options.

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

| Visibility | Recipients | Typical use |
|---|---|---|
| `all` | All participants including the customer | Normal service message |
| `agents_only` | All agents, without the customer | Internal note between agents |
| `["part_abc", "part_xyz"]` | Only the listed participant_ids | Supervisor → specific agent, private |

---

## Invariants — never violate

- **AI Gateway is stateless** — processes one turn per LLM call. No state between turns.
- **Routing Engine is the sole arbiter** — no component routes a conversation without going through it.
- **MCP is the only integration protocol** — no direct REST between internal components.
- **pipeline_state persists to Redis on every step transition** — never in memory only.
- **Agent contract**: `agent_login` → `agent_ready` → `agent_busy` → `agent_done`
- **`agent_done` requires `handoff_reason`** when `outcome !== "resolved"`
- **`issue_status` is always required and never empty** in `agent_done`
- **Agents never access backend systems directly** — only via authorised MCP Servers
- **All domain MCP calls are intercepted** — native agents via `McpInterceptor` (in-process); external agents via proxy sidecar on localhost:7422. No MCP call reaches a domain server without permission validation, injection guard, and audit.
- **`insight.historico.*` persists via Kafka, never direct PostgreSQL write**

---

## MCP Interception — Hybrid Proxy Model

| Agent type | Mechanism | Network hop |
|---|---|---|
| Native agent (SDK) | `McpInterceptor` in-process (`@plughub/sdk`) | None |
| External agent (LangGraph, CrewAI) | `plughub-sdk proxy` sidecar on localhost:7422 | Loopback only |

Checks per call (< 1ms): permission validation (JWT local decode) → injection guard (13 patterns) → audit record (Kafka `mcp.audit`, fire-and-forget). Audit policy defined per tool, not per call — caller cannot opt out (LGPD). `AuditRecord` includes: `server_name`, `tool_name`, `allowed`, `injection_detected`, `duration_ms`, `source` (`in_process`|`proxy_sidecar`).

---

## Repository Structure

```
plughub/
  CLAUDE.md                      ← this file
  plughub_spec_v1.docx           ← full architectural specification
  packages/
    schemas/                     ← @plughub/schemas — Zod contracts
    sdk/                         ← @plughub/sdk — TypeScript + Python
    mcp-server-plughub/          ← Agent Runtime and BPM tools
    skill-flow-engine/           ← Skill Flow interpreter
    ai-gateway/                  ← LLM calls and context extraction (Python)
    agent-registry/              ← CRUD for AgentType, Pool, Skill, GatewayConfig
    routing-engine/              ← Agent allocation and queue management
    rules-engine/                ← Post-routing event evaluation
    channel-gateway/             ← Channel adapters and inbound normalisation
    calendar-api/                ← Calendar engine + CRUD REST (Arc 4) — port 3700
    workflow-api/                ← Workflow instance lifecycle (Arc 4) — port 3800
    skill-flow-worker/           ← Kafka consumer, runs SkillFlow for workflow instances
    pricing-api/                 ← Capacity-based billing, invoice — port 3900
    auth-api/                    ← Auth, JWT, ABAC — port 3200
    evaluation-api/              ← Quality evaluation platform (Arc 6) — port 3400
    mcp-server-knowledge/        ← Vector knowledge base for RAG agents
    platform-ui/                 ← All operator-facing UI (React + Vite)
```

## Stack per Package

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
| calendar-api | Python | Python 3.11+ | FastAPI + asyncpg — port 3700 |
| workflow-api | Python | Python 3.11+ | FastAPI + asyncpg — port 3800 |
| skill-flow-worker | TypeScript | Node 20+ | Kafka consumer + SkillFlowEngine bridge |
| channel-gateway | Python | Python 3.11+ | FastAPI + aiokafka + channel adapters |
| pricing-api | Python | Python 3.11+ | FastAPI + asyncpg + openpyxl — port 3900 |
| auth-api | Python | Python 3.11+ | FastAPI + asyncpg + bcrypt + python-jose — port 3200 |
| evaluation-api | Python | Python 3.11+ | FastAPI + asyncpg — port 3400 |
| platform-ui | TypeScript | Node 20+ / Vite | React 18, Tailwind, i18n |

## Package Dependencies

```
schemas         ← base — no internal dependencies
sdk             ← depends on: schemas
mcp-server      ← depends on: schemas
skill-flow      ← depends on: schemas, mcp-server
ai-gateway      ← depends on: schemas
agent-registry  ← depends on: schemas
routing-engine  ← depends on: schemas, agent-registry
rules-engine    ← depends on: schemas, routing-engine
channel-gateway ← depends on: schemas
auth-api        ← no internal dependencies (standalone user store)
```

Never create circular dependencies. `schemas` never depends on any other package.

## Component Responsibilities (Summary)

| Component | Sole responsibility |
|---|---|
| **Core** | Session lifecycle, canonical stream, message masking, adapter coordination |
| **Channel Gateway** | Inbound normalisation, outbound rendering, fallback interaction collection |
| **AI Gateway** | Stateless LLM inference. Does not manage session or history. |
| **Agent Registry** | CRUD for AgentType, Pool, Skill, GatewayConfig. Cache invalidation via Kafka. |
| **Routing Engine** | Agent allocation, queue management, scoring algorithm, close_reason detection |
| **Rules Engine** | Post-routing event evaluation. Publishes consequences. No routing, no Redis polling. |
| **Skill Flow Engine** | Flow interpreter. Persists pipeline_state to Redis on every step. |

---

## Instance Bootstrap — Reconciliation-Driven Agent Management

Kubernetes-style reconciliation controller in `orchestrator-bridge/instance_bootstrap.py`. Compares desired state (Agent Registry) vs actual state (Redis) and applies minimum diff. Triggers: startup, heartbeat 15s, periodic 5min, `registry.changed`/`config.changed` Kafka. ReconciliationReport: `created/deleted/drained/updated/renewed/unchanged/errors/duration_ms/dry_run`.

**RegistrySyncer** runs before Bootstrap: upserts pools+agent_types from `infra/registry/*.yaml`; prunes stale (`REGISTRY_SYNC_PRUNE=true`). Skill sync: PUTs `skill-flow-engine/skills/*.yaml` before pools; regex `^skill_[a-z0-9_]+_v\d+$`. **Skill hot-reload** (3-elo): startup PUT → `registry.changed` Kafka → `_skill_flow_cache[skill_id]` invalidation → immediate effect without restart. Instance IDs: `{agent_type_id}-{n+1:03d}`. Human agents NOT managed by Bootstrap. Seed no longer writes Redis keys.

→ See [`docs/arcos/instance-bootstrap.md`](docs/arcos/instance-bootstrap.md)

---

## ContextStore & Context-Aware Progressive Resolution

Redis hash `{tenantId}:ctx:{sessionId}`. `ContextEntry`: `{value, confidence 0-1, source, visibility, updated_at}`. Tag namespaces: `caller.*` (customer data), `session.*` (session state), `account.*` (account data), `segment.{segId}.*` (per-agent isolated). Confidence: ≥0.9 confirmed; ≥0.7 high certainty; 0.4-0.7 uncertain; <0.4 unknown.

`@ctx.*` resolves in step inputs, choice conditions (`exists`/`confidence_gte`/`eq`/etc.), and visibility arrays. `@segment.*` prefixed with `segment.{segId}.` isolates parallel agents. `context_tags` on reason/invoke/notify: `inputs` (pre-call) + `outputs` (post-call, fire-and-forget, confidence + merge strategy). Sentiment emitter writes `session.sentimento.current` + `session.sentimento.categoria` (confidence 0.80, TTL 4h).

**Step `resolve`**: 5-phase inline accumulation (gap check → CRM → LLM question → BLPOP → LLM extract). **agente_contexto_ia_v1**: 0 LLM when CRM resolves; max 2 when collecting. **Copilot**: fire-and-forget analysis per client message → `session.copilot.*` tags. `supervisor_state` returns `context_snapshot` from ContextStore.

→ See [`docs/guias/context-store.md`](docs/guias/context-store.md)

---

## Channel vs Medium

- **channel** = specific channel (`whatsapp`, `webchat`, `voice`, `email`, `sms`, `instagram`, `telegram`, `webrtc`) — **hard filter** for routing, mandatory match
- **medium** = base type (`voice`, `video`, `message`, `email`) — **score factor**, fine-tuning only

## Canonical Stream

`session:{id}:stream` is the single source of truth for all session events. **All XADD calls MUST go through `writeStreamEntry()`** in `lib/write-stream-entry.ts` — never call `redis.xadd()` directly. Sole exception: `session_opened`/`session_closed` in Core `server.ts`. Guarantees: `event_id` always present, `segment_id` always flat, `author_id`/`author_role` flat fields, Zod validation before write.

Messages carry `content` (masked) and `original_content` (unmasked, authorized roles only for LGPD audit).

## Sentiment Tracking

Score-only array in Redis during session. Labels calculated at read time using tenant-configurable ranges. Persisted to PostgreSQL (`sentiment_timeline JSONB`) on session close. Never published to canonical stream.

```
session:{id}:sentiment → [{ score: 0.40, timestamp: "..." }, ...]
TTL: same as session TTL
Ranges: [ 0.3, 1.0] → satisfied | [-0.3, 0.3] → neutral | [-0.6,-0.3] → frustrated | [-1.0,-0.6] → angry
```

## Skill Flow — Thirteen Step Types

| Type | Does | Interacts with |
|---|---|---|
| `task` | Delegates to agent via A2A (`assist`/`transfer`) | Routing Engine |
| `choice` | Conditional branching via JSONPath | pipeline_state |
| `catch` | Retry and fallback before escalation | pipeline_state |
| `escalate` | Routes to pool | Rules Engine |
| `complete` | Closes with defined outcome | agent_done |
| `invoke` | Calls MCP tool directly | MCP Server |
| `reason` | Invokes AI Gateway with output_schema | AI Gateway |
| `notify` | Sends message to customer (unidirectional) | Core → Channel Gateway |
| `menu` | Captures customer input, suspends until reply | Core → Channel Gateway |
| `suspend` | Suspends workflow until external signal | workflow-api |
| `collect` | Contacts target via channel, awaits response | workflow-api → Channel Gateway |
| `resolve` | Inline context accumulation (5-phase pipeline) | ContextStore + AI Gateway |
| `begin_transaction` / `end_transaction` | Masked input atomic block | in-memory only |
| `receive` | Suspends awaiting next stream message from any participant (no prompt sent to channel) | Redis BLPOP on `receive:result:{sid}:{iid}` |

`menu` interaction modes: `text`, `button` (≤3 WhatsApp), `list`, `checklist`, `form`. Fallback for unsupported channels in Channel Gateway adapter only.

## Routing Algorithm — Key Rules

1. **channel is a hard filter** — agent not supporting contact channel = forbidden
2. **agent pause is a hard filter** — paused agents excluded
3. **gateway heartbeat TTL** — agents on gateways >90s expired = excluded
4. **SLA lazy evaluation** — `min(wait_time / sla_target, max_score)` at queue head only
5. **Tie-breaking** — equal-score pools broken by shortest queue length
6. **close_reason detection** — `no_resource` when no queue; `max_wait_exceeded` by lazy eval

## Rules Engine — Scope

Consumes: `conversations.routed`, `conversations.queued`, `conversations.abandoned`, `agent.done`. Publishes: `rules.escalation.events`, `rules.shadow.events`, `rules.session_tagged`. Does NOT: monitor Redis, evaluate sentiment, make routing decisions, maintain state between events.

---

## Kafka Topics

| Topic | Producer | Consumer(s) |
|---|---|---|
| `conversations.inbound` | Channel Gateway | Core, Routing Engine |
| `conversations.routed` | Routing Engine | Core, Rules Engine |
| `conversations.queued` | Routing Engine | Rules Engine |
| `conversations.abandoned` | Routing Engine | Core, Rules Engine |
| `conversations.session_opened/closed` | Core | Analytics, LGPD |
| `conversations.message_sent` | Core | Analytics |
| `conversations.participants` | orchestrator-bridge | analytics-api → ClickHouse |
| `rules.escalation.events` | Rules Engine | Routing Engine |
| `rules.shadow.events` | Rules Engine | Analytics |
| `registry.changed` | Agent Registry | Routing Engine, Core, orchestrator-bridge |
| `config.changed` | Config API | orchestrator-bridge, routing-engine |
| `gateway.heartbeat` | Channel Gateway | Routing Engine |
| `agent.done` | Routing Engine | Rules Engine, Analytics |
| `queue.position_updated` | Routing Engine | Channel Gateway, Analytics |
| `mcp.audit` | McpInterceptor / proxy sidecar | Analytics, LGPD |
| `sentiment.updated` | AI Gateway | analytics-api |
| `evaluation.events` | evaluation-api | analytics-api → ClickHouse |
| `workflow.events` | workflow-api | skill-flow-worker |
| `collect.events` | workflow-api | analytics-api |
| `journey.events` | workflow-api | analytics-api → ClickHouse |
| `usage.events` | Core, AI Gateway, Channel Gateway | usage-aggregator |

## Kafka Event Schemas — Zod Coverage

All cross-package Kafka events have Zod schemas in `@plughub/schemas`:

| Topic | Schema | File |
|---|---|---|
| `rules.escalation.events` | `RulesEscalationEventSchema` | `rules-events.ts` |
| `registry.changed` | `RegistryChangedEventSchema` | `platform-events.ts` |
| `config.changed` | `ConfigChangedEventSchema` | `platform-events.ts` |
| `sentiment.updated` | `SentimentUpdatedEventSchema` | `platform-events.ts` |
| `queue.position_updated` | `QueuePositionUpdatedEventSchema` | `platform-events.ts` |
| `conversations.routed/queued` | `ConversationRoutedEventSchema` | `platform-events.ts` |
| `agent.lifecycle` | `AgentLifecycleEventSchema` | `platform-events.ts` |
| `workflow.events` | `WorkflowEventSchema` | `workflow.ts` |
| `collect.events` | `CollectEventSchema` | `workflow.ts` |
| `journey.events` | `JourneyEventSchema` | `journey.ts` |
| `usage.events` | `UsageEventSchema` | `usage.ts` |
| `conversations.participants` | `ConversationParticipantEventSchema` | `contact-segment.ts` |
| `mcp.audit` | `AuditRecordSchema` | `audit.ts` |
| `evaluation.events` | `EvaluationEventSchema` | `evaluation.ts` |

---

## Naming Conventions

```
skill_id:       skill_{name}_v{n}      →  skill_portabilidade_telco_v2
agent_type_id:  {name}_v{n}            →  agente_retencao_v1
pool_id:        snake_case no version  →  retencao_humano
mcp_server:     mcp-server-{name}      →  mcp-server-crm
tool:           snake_case             →  customer_get
insight:        insight.historico.*    →  customer long-term memory
                insight.conversa.*     →  generated in current session, expires on close
```

### Language Rule — English in code, Portuguese only in display

All technical identifiers MUST be in English: URL routes, TypeScript/Python variable names, function names, interface names, type union values, i18n key names, file names, folder names, navKeys, tab IDs, ABAC field names, Kafka topic names, Redis key patterns, and API endpoint paths.

Portuguese is allowed ONLY in: i18n value strings (the translated text shown to the user) and in business-domain entity IDs (`agente_*`, `skill_*`, `pool_id`, `tenant_id`) that represent named instances configured by the tenant — these are data, not code.

```
✅  route: /config/channels        href: t('nav.channels')    tab: 'report'
❌  route: /config/canais          href: t('nav.canais')       tab: 'relatorio'

✅  agente_retencao_v1   (entity ID configured by tenant — data, not code)
❌  const atendimento =  (TypeScript variable)
❌  def mascaramento():  (Python function)
```

## What Never To Do

- Never create a component that routes conversations without going through the Routing Engine
- Never access Redis directly from outside routing-engine or skill-flow-engine
- Never redefine types from `@plughub/schemas` locally in another package
- Never add business logic to mcp-server-plughub — it only exposes tools
- Never create a dependency on `ai-gateway` in TypeScript packages — only Python consumes it
- Never use `export *` in packages — always explicit named exports
- Never implement channel-specific rendering logic in skill-flow — adapters live exclusively in channel-gateway
- Never allow a caller to opt out of MCP audit records — policy defined on the tool
- Never write to `insight.historico.*` directly in PostgreSQL — always via Kafka
- Never expose `original_content` of masked messages to agents — only to authorised roles via audit trail
- Never forward tool calls containing injection patterns
- Never send tool list to LLM without applying `permissions[]` filter from JWT
- Never write masked input values to `pipeline_state`, Redis, stream, or logs
- Never allow AI agents to emit `@mention` commands — only `role: primary` or `role: human`
- Never call `redis.xadd()` directly in mcp-server-plughub — use `writeStreamEntry()`
- **Never leave deferred phases undocumented** — every unimplemented phase MUST be registered in `## Pending`
- Never create a new `packages/my-ui/` standalone frontend app — add a module to platform-ui

## SDK CLI

```bash
plughub-sdk certify            # validates execution contract
plughub-sdk verify-portability # verifies dependency isolation
plughub-sdk regenerate         # regenerates proprietary agent as native
plughub-sdk skill-extract      # extracts skill from existing agent
plughub-sdk proxy              # starts proxy sidecar on localhost:7422
```

## Operational Visibility — Section 3.3c

Routing Engine writes pool snapshot to Redis after every routing event:
`{tenant_id}:pool:{pool_id}:snapshot` (TTL 120s) — `{ pool_id, available, queue_length, sla_target_ms, channel_types, updated_at }`.

Three MCP tools (group `operational`): `queue_context_get`, `pool_status_get`, `system_availability_check`. When contact is queued, Routing Engine publishes `queue.position_updated` to Kafka.

## Security — Section 9.5

**Tool permission filtering**: `InferenceRequest.permissions` from JWT → `InferenceEngine.infer()` filters tool list. Empty = no filtering (backward-compatible).

**Injection guard** (`injection_guard.ts`): 13+ heuristic regex patterns. Applied in `notification_send` (message) and `conversation_escalate` (pipeline_state). Future: apply at proxy sidecar level for all domain tool calls.

---

## Message Masking, @mention & Masked Input

Token format in stream: `[{category}:{token_id}:{display_partial}]` (e.g. `[cpf:tk_b7d2:***-00]`). Stream stores `content` (masked) + `original_content` (unmasked). Default `authorized_roles: ["evaluator", "reviewer"]`. Domain MCP tools resolve tokens via `McpInterceptor.resolveToken` callback. Channel Gateway strips to `display_partial` only before WS delivery.

**@mention**: only `role: primary` or `role: human` may issue mentions. Domain closed by `mentionable_pools` pool config. `mention_commands` YAML declares actions: `set_context`, `trigger_step`, `terminate_self`.

**Masked Input**: `masked: true` on menu step (field-level or step-level). `begin_transaction`/`end_transaction` wraps collection-validation-action as atomic block. `@masked.*` namespace in-memory only — never written to Redis, pipeline_state, stream, or logs. Retry always recolects; never re-uses masked values.

→ See [`docs/adr/adr-message-masking.md`](docs/adr/adr-message-masking.md), [`docs/guias/masked-input.md`](docs/guias/masked-input.md), [`docs/guias/mention-protocol.md`](docs/guias/mention-protocol.md)

---

## Session Replayer — Quality Evaluation Pipeline

Pattern: ensure-before-read with optional Hydrator. Pipeline: `session_closed` → Stream Persister (PostgreSQL) → `evaluation.requested` → Hydrator (Redis hit: no-op; miss: PG→Redis) → Replayer (always reads Redis) → `ReplayContext` at `{tenant}:replay:{session_id}:context` (TTL 1h) → Evaluator (evaluation_context_get → evaluation_submit) → `evaluation.events` → ClickHouse.

`ReplayContext` extended for Arc 6: `evaluation_form`, `campaign_context`, `knowledge_snippets` (top-5). **Comparison Mode**: `comparison_turns` with Jaccard similarity (threshold 0.4); `buildComparisonReport()` with divergence_points. `ReplayEvent.delta_ms` preserves original intervals; `speed_factor` scales timing (default 10x batch).

→ See [`docs/arcos/session-replayer.md`](docs/arcos/session-replayer.md), [`docs/adr/adr-session-replayer.md`](docs/adr/adr-session-replayer.md)

---

## Session & Conference Lifecycle — Three-Layer Model

Three independent layers must not be collapsed: **(1) contact lifecycle** (customer perspective, statistics frozen at customer departure); **(2) agent segment lifecycle** (each participant's window, pool resource freed at `agent_done`); **(3) conference infrastructure** (the room, destroyed only when all participants leave). The current implementation conflates layers 1 and 3 — `_trigger_contact_close()` currently serves both. Known gaps: G1 (AHT inflated by wrap-up time), G2 (`remaining` ignores AI specialists), G3 (AI instance restored while still running), G4 (supervisor has no heartbeat cleanup), G5 (primary AI close expels supervisor), G6 (redundant restore on agent_done close). Fixes applied 2026-05-10: busy counter on cross-pool transfer, pool counter on queue entry, `agent_done` publish from bridge for native/YAML-fallback agents.

→ See [`docs/arcos/session-conference-lifecycle.md`](docs/arcos/session-conference-lifecycle.md)

---

## Usage Metering

Kafka topic `usage.events` — `UsageEventSchema`: `event_id`, `tenant_id`, `session_id`, `dimension`, `quantity`, `source_component`, `metadata`. No pricing in usage records — metering ≠ pricing.

Dimensions wired: `sessions` (Core, SET NX guard), `messages` (Core, visibility=all), `llm_tokens_input/output` (AI Gateway), `webchat_attachments` (Channel Gateway). Pending: `whatsapp_conversations`, `voice_minutes`, `sms_segments`, `email_messages` (functions ready, adapters not yet wired).

Redis: `{t}:usage:current:{dimension}` (45d), `{t}:quota:limit:{dimension}`, `{t}:quota:concurrent_sessions`. `assertQuota` (INCRBY-check-rollback). Cycle reset: `POST /admin/cycle-reset` (port 3950).

→ See [`docs/arcos/usage-metering.md`](docs/arcos/usage-metering.md)

---

## WebChat Channel — Hybrid Stream Model

Three distinct channels: `webchat`, `webrtc`, `whatsapp`. Client is NOT a named participant — Channel Gateway does XREAD on `session:{id}:stream` directly. Reconnect via cursor: zero messages lost. WebchatAdapter: 3 concurrent async tasks (receive_loop, stream_delivery_loop, typing_listener).

Upload (2-stage): WS `upload.request` → `upload.ready` (file_id, upload_url) → HTTP POST binary → `upload.committed` → WS `msg.image/document/video`. MIME allowlist: JPEG/PNG/WebP/GIF (16MB), PDF (100MB), MP4/WebM (512MB). Expiry: soft-delete hourly, physical delete daily (+24h grace). JWT via message body, never URL. `jwt_secret` per tenant via Redis `{tenant_id}:config:webchat:jwt_secret`.

Masked fields delivery chain: `step.masked` → `notification_send` args → `conversations.outbound` Kafka → `WsMenuRender.masked_fields` → `interaction.request` WS event → `<input type="password">` overlay in webchat.

→ See [`docs/adr/adr-webchat-channel.md`](docs/adr/adr-webchat-channel.md)

---

## Pricing Module — Capacity-Based Billing

`packages/pricing-api/` — Python FastAPI, port 3900. Billing by configured capacity, not consumption. Two components: **base capacity** (monthly pro-rated, billing_days) + **reserve pools** (full-day billing per activation day). `billing_cycle_day` default 1. `reserve_markup_pct` default 0%.

Endpoints: `GET /v1/pricing/invoice/{tenant_id}` (JSON + `?format=xlsx`), `POST /v1/pricing/resources/{tenant_id}`, `POST /v1/pricing/reserve/{tenant_id}/{pool_id}/activate|deactivate`. Config API namespace `pricing`: `unit_prices`, `reserve_markup_pct`, `billing_cycle_day`, `currency`. Platform-UI BillingPage at `/config/billing` (role: admin). Quota limits written to Redis on plan activation — not seeded by Config API.

→ See [`docs/arcos/pricing.md`](docs/arcos/pricing.md)

---

## Pool Lifecycle Hooks

Hooks declared in pool YAML (`PoolHooks.on_human_start`/`on_human_end`/`post_human`). Bridge dispatches synthetic `conversations.inbound` with `conference_id` — reuses 100% of conference infrastructure.

**on_human_end** → NPS + wrap-up agents activated in parallel. NPS visibility = `["@ctx.session.customer_participant_id"]` (customer-only). Wrap-up visibility = `["@ctx.session.human_agent_participant_id"]` (agent-only). **Phase B**: `agent_done` does NOT close WS; bridge holds close until all hook agents complete. `hook_pending` Redis counter controls when `_trigger_contact_close()` fires. **Phase C**: `post_human` hooks fire after all `on_human_end` agents complete. Participation events (`conversations.participants`) written by bridge for analytics.

Pre-hook ContextStore writes (before hooks fire): `session.close_origin`, `session.customer_participant_id`, `session.human_agent_participant_id`.

→ See [`docs/guias/pool-hooks.md`](docs/guias/pool-hooks.md)

---

## Arc 5 — ContactSegment Analytics

`ContactSegment`: `segment_id`, `session_id`, `participant_id`, `pool_id`, `role`, `agent_type`, `parent_segment_id` (null for primary), `sequence_index`, `started_at`, `ended_at`, `duration_ms`, `outcome`, `close_reason`. Conference topology: specialist `parent_segment_id` → primary `segment_id`. Sequential handoffs: `sequence_index` increments.

ClickHouse tables: `analytics.segments` (`ReplacingMergeTree` ORDER BY `(tenant_id, session_id, segment_id)`), `analytics.session_timeline` (enriched with `segment_id`), `mv_agent_performance_daily` (AggregatingMergeTree), `mv_segment_summary`. Endpoints: `GET /reports/segments`, `GET /reports/agents/performance`, `GET /reports/agent-performance/daily`, `GET /reports/sessions/complexity`.

→ See [`docs/arcos/arc5-segments.md`](docs/arcos/arc5-segments.md), [`docs/adr/adr-contact-segments.md`](docs/adr/adr-contact-segments.md)

---

## AI Gateway — Multi-Account Rotation

`AccountSelector` in `account_selector.py` — Redis-backed, stateless per call. Algorithm: for each account, check throttle key (`ai_gw:{provider}:{key_id}:throttled`); score = `rpm_used/rpm_limit × 0.7 + tpm_used/tpm_limit × 0.3`; pick lowest score. On 429/529: `mark_throttled` → next account → cross-provider fallback (`FallbackConfig`).

Config: `PLUGHUB_ANTHROPIC_API_KEYS=sk-1,sk-2,sk-3` (multi-key activates AccountSelector). `PLUGHUB_OPENAI_API_KEYS` optional fallback. Model profiles: `realtime` (Sonnet → gpt-4o), `balanced` (Haiku → gpt-4o-mini), `evaluation` (Haiku — isolated from realtime). Config API namespace `ai_gateway`: `account_rotation_enabled`, `throttle_retry_after_s`, `evaluation_model`.

→ See [`docs/arcos/ai-gateway.md`](docs/arcos/ai-gateway.md)

---

## Arc 8 — Agent Availability & Pause Tracking

Pipeline for tracking human agent pauses. Config API namespace `agent_activity`, key `pause_reasons` (seedable pause reason list). Pause endpoints: `PUT /api/agent-pause` and `PUT /api/agent-resume` in mcp-server-plughub — updates Redis state, publishes `agent_pause`/`agent_ready` to `agent.lifecycle` Kafka with `reason_id`/`reason_label`. ClickHouse table: `agent_pause_intervals` (ReplacingMergeTree). Analytics: `GET /reports/agent-availability` with pool scoping. Platform-UI: `AgentReportsPage.tsx` at `/contacts/reports/agents`.

→ See [`docs/arcos/arc8-agent-availability.md`](docs/arcos/arc8-agent-availability.md)

---

## Frontend Architecture — platform-ui

Single-app shell in `packages/platform-ui/`. Design tokens: `primary=#1B4F8A`, `secondary=#2D9CDB`, `accent=#00B4D8`, `green=#059669`, `warning=#D97706`, `red=#DC2626`. Font: Inter. Never use inline hex — Tailwind tokens only.

Roles: `operator` (Monitor+Contacts), `supervisor` (+Evaluation+Reports), `admin` (+Config+Skills), `developer` (+DevTools), `business` (cross-cutting, no operational items). **ABAC gates** on nav items: `operacao` field gates Monitor/Editor/Calendar/Deploy/AgentAssist; `visualizar` gates Reports/Análise tabs.

Nav groups (navKey): Home 🏠, Console 🖥️ (contacts.operacao), Monitor 📡 (Sessions/Agents/Pools/Events/Processes), Fluxo 🔄 (Editor/Deploy → skill_flows.operacao), Avaliação ✓ (Forms/Campaigns/Knowledge/Evaluations), Analytics 📊 (Sessions/Agents/Events/Processes/Quality → visualizar/report), Configuração ⚙️ (Dashboards/Resources/Platform/Channels/Calendars/Masking/Billing/Access). Legacy redirects: `/workflows` → `/workflow/monitor`, `/skill-flows` → `/agent-flow/editor`, `/reports` → `/contacts?tab=analise`.

**Skill Deploy Lifecycle**: `deploy_status` (draft/published) + `skill_deployments` table. `PUT /v1/skills` always sets `deploy_status=draft` on new skills, NEVER modifies it on updates. `POST /v1/skills/:id/deploy` — only action that sets published.

**Agent Assist UI** at `/agent-assist`: 4-tab right panel (Estado, Capacidades, Contexto, Histórico). Substitution mode for menu cards. Visibility array routing for NPS/wrap-up agents. Optimistic echo for button selections.

→ See [`docs/arcos/platform-ui.md`](docs/arcos/platform-ui.md)

---

## Arc 7 — Auth, RBAC + ABAC, Performance Routing

**auth-api** (port 3200): users + sessions in PostgreSQL schema `auth`. JWT HS256 TTL 1h; refresh token rotation (43-char opaque, SHA-256 stored). Silent re-auth from `localStorage('plughub_refresh_token')`. `accessible_pools[]` in JWT: empty = all pools; non-empty = row-level filter in analytics-api.

**ABAC** (`module_config` in JWT): `auth.module_registry` seeded from `infra/modules.yaml`. 8 modules: `evaluation`, `contacts`, `billing`, `config`, `skill_flows`, `workflows`, `agent_assist`, `campaigns`. Each field has `access: none|read_only|write_only|read_write` + `scope[]`. `PermissionChecker.can(module, field, minAccess?, scopeId?)`. Graceful degradation for legacy accounts without `module_config`.

**Performance routing** (Arc 7d): `performance_score = resolution_rate × (1 − escalation_rate)`. Blending: `(1-w) × competency + w × performance`; `w = performance_score_weight` (default 0.0, env `PLUGHUB_PERFORMANCE_SCORE_WEIGHT`). Redis key `{tenant}:agent_perf:{agent_type_id}` (TTL 6h). Batch job in analytics-api runs every 5min, lookback 7 days, min 5 sessions for statistical significance.

→ See [`docs/arcos/arc7-auth.md`](docs/arcos/arc7-auth.md)

---

## Arc 6 — Quality Evaluation Platform

**evaluation-api** (port 3400): Forms CRUD, Campaigns (sampling + reviewer rules + contestation policy), Instances (auto-created by sampling engine on `session_closed`), Results, Contestations. Auth: admin via `X-Admin-Token`; review/contest via `Bearer JWT` with ABAC `module_config.evaluation.revisar/contestar`. `available_actions: ["review"|"contest"]` computed server-side — never client-side. Anti-replay: `round` field must match `result.current_round` or 409.

**Workflow as review motor**: `campaign.review_workflow_skill_id` (e.g. `skill_revisao_treplica_v1`) drives state. Submit result → `POST /v1/workflow/trigger` → workflow suspends → `workflow.events` consumer updates `action_required`, `deadline_at`, `resume_token`. Human acts → evaluation-api writes `session.review_decision` to ContextStore → `POST /v1/workflow/resume`. Timeout → `locked=true`. YAML is sole owner of round count logic.

**mcp-server-knowledge** (TypeScript, port 3401): pgvector knowledge base for RAG. Tools: `knowledge_search`, `knowledge_upsert`, `knowledge_delete`. **agente_avaliacao_v1**: loads form + knowledge snippets via `evaluation_context_get`, scores each criterion with evidence, submits via `evaluation_submit`. Analytics: `evaluation_results` + `evaluation_events` ClickHouse tables; `GET /reports/evaluations` + `/reports/evaluations/summary`.

→ See [`docs/arcos/arc6-evaluation.md`](docs/arcos/arc6-evaluation.md)

---

## Arc 4 — Workflow Automation

**workflow-api** (port 3800): `WorkflowInstance` lifecycle. Endpoints: `/trigger`, `/instances/{id}/persist-suspend`, `/resume`, `/complete`, `/fail`, `/cancel`. Timeout scanner: background task, 60s interval, atomic UPDATE. Kafka topic `workflow.events` (7 event types).

**Suspend step**: `reason: approval|input|webhook|timer`, `timeout_hours`, `business_hours` (uses calendar-api). Two-stage idempotency sentinel. **collect step**: contacts target via channel, suspends until response or timeout. `collect_token` for correlation; `campaign_id` as free-form grouper across instances.

**Calendar API** (port 3700): pure engine. Functions: `is_open`, `next_open_slot`, `add_business_duration`, `business_duration`. Feriados recorrentes `MM-DD`. Status 3-state: `open/closed/holiday`. Timezone per tenant. 4 MCP tools wrapping calendar engine.

**Webhooks**: `plughub_wh_{43-char}` token, SHA-256 stored. CRUD (X-Admin-Token) + public `POST /v1/workflow/webhook/{id}` (X-Webhook-Token). Delivery log with timing and status. `origin_session_id` in WorkflowInstance links workflow to parent contact session.

**Skill Deploy** (Phase 2): `POST /v1/skills/:id/deploy` → `skill_deployments` table → `publishRegistryChanged`. Scheduled deploy via `skill_scheduled_deploy_v1` workflow YAML. `GET /v1/skills/:id/handoff-status` for safe deploys.

→ See [`docs/arcos/arc4-workflow.md`](docs/arcos/arc4-workflow.md)

---

## Arc 9 — Agent Groups & Supervisor Scope

`AgentGroup` is a people-management entity, orthogonal to Pool (Pool = routing; Group = org chart). Tables in `auth` schema: `agent_groups`, `agent_group_members` (agent_type_id + is_human), `agent_group_users`, `agent_group_supervisors`, `agent_group_shifts` (days_of_week[], time_start/end TIME, timezone).

**Login/refresh denormalization**: `resolve_supervisor_scope(pool, user_id, role)` in auth-api computes active groups at JWT issue time via shift resolution (spec DOW 0=Sun; Python `weekday()` converted via `(dow+1)%7`). JWT carries `supervised_groups[]`, `supervised_agent_types[]`, `supervised_user_ids[]`. Admin role → `([], [], [])` = no restriction. Supervisor with active groups but no members → `["__no_active_shift__"]` sentinel (prevents empty=unrestricted misinterpretation).

**analytics-api scope filtering**: `PoolPrincipal.supervised_agent_types` (None = unrestricted, list = filter). `_apply_agent_scope()` for segments/performance/availability (direct WHERE). `_agent_scope_session_join()` for sessions (LEFT JOIN on segments FINAL — sessions table has no agent_type_id column). All 5 report endpoints pass `supervised_agent_types` to query functions.

**auth-api REST** (`/v1/groups`, admin-token): full CRUD for groups + sub-resources (members, users, supervisors, shifts). `groups_router.py` registered in `main.py`.

**platform-ui**: `GroupsPage` at `/config/groups` (roles: admin, ABAC `config.users`). List + side drawer with 4 tabs (Info, Members, Supervisors, Shifts). Nav entry added to Configuração group. i18n namespace `groups` (en + pt-BR). `Session.supervisedAgentTypes` + `CurrentUser.supervisedAgentTypes` added to auth layer. Monitor Heatmap filtered by `accessiblePools`; Agents/Instances tabs filtered by `supervisedAgentTypes` (client-side, transparent). Console already scoped via `accessiblePools` in `AgentAssistContext.fetchPools`.

→ See [`docs/arcos/arc9-agent-groups.md`](docs/arcos/arc9-agent-groups.md)

---

## Arc 10 — Journey: Multi-Session Service Automation

Journey é a unidade de serviço que transcende a sessão — agrupa todos os contatos (`session_id[]`) de um mesmo processo de atendimento. Nível acima de Session na hierarquia de observabilidade: Journey → Session → Segment (drill-down aditivo, sem quebrar modelo existente).

**Entidade Journey**: `journey_id` (UUID, separado de `workflow_instance_id`), `tenant_id`, `skill_id`, `workflow_instance_id` (nullable FK), `customer_id`, `origin_session_id`, `status` (`active|suspended|completed|failed|cancelled`), `metadata`. Sessions ganham campo `journey_id` nullable — sessões standalone não mudam.

**Formas de iniciar**: (1) MCP tool `journey_start(skill_id, session_id, metadata?)` — chamada por AI agent ou humano; (2) `@mention` `@journey:<skill_id>` por agente primário — pool config `mentionable_journeys`; (3) flag `creates_journey: true` na skill YAML — automático no primeiro step; (4) botão "Iniciar Processo" no ActionBar do Console (chama a mesma MCP tool). Toda criação passa pelo McpInterceptor (auditoria).

**Vinculação de sessões subsequentes**: sessões criadas por `collect` step recebem `journey_id` via Kafka `collect.events`. Recontatos manuais via `journey_link_session(journey_id, session_id)`. Correlação automática de recontatos espontâneos é fase posterior.

**Fases A–E implementadas**: `workflow-api` `/v1/journeys` (6 endpoints, inclui `POST /from-instance/{id}`) + MCP tools `journey_start`/`journey_link_session`/`journey_merge` + `analytics-api` consumer `journey.events` → `journey_events` ClickHouse. Phase B: `creates_journey:true` no YAML + skill-flow-worker auto-cria Journey; `collect` step propaga `journey_id`; `respond_collect` emite `journey_session_linked` quando sessão filha chega. Phase C: `GET /reports/journeys` (argMax aggregation, KPI strip por skill_id) + `ProcessosPage` com tabs Jornadas/Instâncias + hooks `useJourneys`/`useJourney`. Phase D: `HistoricoTab` seção "Processos em aberto" por `customer_id`; `ActionBar` botão "Iniciar Processo" dropdown filtrado por `pool.mentionable_journeys`; `MergeButton` no painel de detalhe de jornadas. Phase D.5: `journey_session_linked` enriquecido com `current_step`/`session_outcome`/`session_started_at`/`session_ended_at`; regra de interseção — `sessions.journey_id` apenas em collect sessions, nunca na origin; múltiplas journeys podem compartilhar `origin_session_id`. Phase E: 4 display endpoints no `display.py` + 4 entradas em `catalog.ts` — `journey-active-count` (metric_card), `journey-resolution-rate` (bar_chart), `journey-funnel` (donut), `journey-median-duration` (bar_chart); queries `argMax(status, event_time)` sobre `journey_events FINAL`.

**Kafka**: `journey.events` — 8 tipos: `journey_started`, `journey_session_linked`, `journey_suspended`, `journey_resumed`, `journey_completed`, `journey_failed`, `journey_cancelled`, `journey_merged`.

→ See [`docs/arcos/arc10-journey.md`](docs/arcos/arc10-journey.md)

---

## Pending (Next Iteration)

### Usage Metering — Channel Gateway Adapters
- `whatsapp_conversations`, `voice_minutes`, `sms_segments`, `email_messages` *(deferred)*: functions in `usage_emitter.py` ready, adapters not yet calling them.

### Pricing Module
- **Integração metering × pricing** *(deferred)*: módulo que aplica planos e escreve `{tenant}:quota:limit:*`.

### CLAUDE.md — Otimização
- **Fase 3** *(next)*: Revisão final para confirmar target ≤ 800 linhas; mover seções remanescentes se necessário.
