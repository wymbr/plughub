# PlugHub Platform — Global Architectural Context

## What PlugHub is

PlugHub is an enterprise orchestration platform that connects agents —
human and AI, from any origin — to business systems and customers,
with measurable quality and without creating lock-in. It is the infrastructure
that makes service delivery possible, not the delivery itself.

## Invariants — never violate

- **AI Gateway is stateless** — processes one turn per LLM call. No state between turns.
- **Routing Engine is the sole arbiter** — no component routes a conversation without going through it.
- **MCP is the only integration protocol** — no direct REST between internal components.
- **pipeline_state persists to Redis on every step transition** — never in memory only.
- **Agent contract**: `agent_login` → `agent_ready` → `agent_busy` → `agent_done`
- **`agent_done` requires `handoff_reason`** when `outcome !== "resolved"`
- **`issue_status` is always required and never empty** in `agent_done`
- **Agents never access backend systems directly** — only via authorised MCP Servers
- **All domain MCP calls are intercepted** — native agents via PlugHubAdapter (in-process); external agents via proxy sidecar (`plughub-sdk proxy`). No MCP call reaches a domain server without permission validation and audit.
- **`insight.historico.*` persists via Kafka, never direct PostgreSQL write** — `insight_register` publishes `insight.registered` to `conversations.events`; a consumer promotes `insight.conversa.*` → `insight.historico.*` on `contact_closed`. Persistence boundary is the contact, not the agent session.

## MCP interception — hybrid proxy model (spec 4.6k, strategy section 11)

Domain MCP Servers (mcp-server-crm, mcp-server-telco, etc.) are separate from mcp-server-plughub and are operated by the tenant. All calls to them must be intercepted for permission validation and audit:

| Agent type | Interception mechanism | Network hop |
|---|---|---|
| Native agent (uses SDK) | PlugHubAdapter in-process | None |
| External agent (LangGraph, CrewAI) | `plughub-sdk proxy` sidecar on localhost:7422 | Loopback only |
| GitAgent (output of `regenerate`) | PlugHubAdapter in-process (generated code) | None |

The proxy sidecar validates `permissions[]` from the session_token JWT locally (no network call, ~0.1ms) and writes audit events asynchronously to a local buffer drained by a background thread to Kafka. Total overhead per MCP call: **< 1ms**. Viable in SaaS multi-site deployments.

## Repository structure

```
plughub/
  CLAUDE.md                      ← this file
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

## Native platform agent types

| Agent | Logic | Operator-configurable |
|---|---|---|
| Orchestrator | Configurable via Skill Flow | The complete flow in JSON |

## Skill Flow — nine step types

| Type | Does | Interacts with |
|---|---|---|
| `task` | Delegates to agent with skill via A2A | Routing Engine |
| `choice` | Conditional branching via JSONPath | pipeline_state |
| `catch` | Retry and fallback before escalation | pipeline_state |
| `escalate` | Routes to pool via Rules Engine | Rules Engine |
| `complete` | Closes with defined outcome | agent_done |
| `invoke` | Calls MCP tool directly | MCP Server |
| `reason` | Invokes AI Gateway with output_schema | AI Gateway |
| `notify` | Sends message to customer (unidirectional) | Notification Agent |
| `menu` | Captures customer input and suspends until reply | Channel Gateway via Notification Agent |

### menu step — interaction modes

| Interaction | Result type | Channels (native) | Fallback |
|---|---|---|---|
| `text` | `string` | All | — |
| `button` | `string` (option id) | WhatsApp (≤3), web chat | Numbered text |
| `list` | `string` (option id) | WhatsApp, web chat | Numbered text |
| `checklist` | `string[]` | Web chat | Comma-separated numbers |
| `form` | `object` | Web chat | Sequential field-by-field |

**Channel Gateway responsibility**: sequential fallback collection (for `button`, `list`, `checklist`, `form` on channels without native support) happens in the Channel Gateway adapter — not in skill-flow or Notification Agent. skill-flow always receives a single normalised event with the complete result regardless of how many channel turns were needed.

## SDK CLI

```bash
plughub-sdk certify            # validates execution contract
plughub-sdk verify-portability # verifies dependency isolation
plughub-sdk regenerate         # regenerates proprietary agent as native
plughub-sdk skill-extract      # extracts skill from existing agent
```

## What never to do

- Never create a component that routes conversations without going through the Routing Engine
- Never access Redis directly from outside routing-engine or skill-flow-engine
- Never redefine types from `@plughub/schemas` locally in another package
- Never add business logic to mcp-server-plughub — it only exposes tools
- Never create a dependency on `ai-gateway` in TypeScript packages — only Python consumes it
- Never use `export *` in packages — always explicit named exports
- Never implement channel-specific rendering logic in skill-flow or Notification Agent — channel adapters live exclusively in channel-gateway
- Never put form field validation (business rules) inside the `menu` step — validation belongs to subsequent steps or delegated agents in the flow

## Spec reference

All architectural decisions are documented in the **PlugHub spec v24.0**.
When in doubt about the expected behaviour of any component,
the spec is the source of truth — not the existing code.

Most relevant sections per package:
- schemas: 3.4, 3.4a, 4.2, 4.5, 4.7
- sdk: 4.6a–4.6j
- mcp-server: 9.4, 9.5
- skill-flow: 4.7, 9.5i
- ai-gateway: section 2 (AI Gateway)
- agent-registry: 4.5, 4.7
- routing-engine: 3.3
- rules-engine: 3.2
- channel-gateway: 3.5 (Channel Gateway — normalisation, adapters, form collection)
