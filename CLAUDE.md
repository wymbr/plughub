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
    calendar-api/                ← plughub-calendar-api — calendar engine + CRUD REST (Arc 4)
    workflow-api/                ← plughub-workflow-api — workflow instance lifecycle (Arc 4)
    skill-flow-worker/           ← skill-flow-worker — Kafka consumer, runs SkillFlow engine for workflow instances (Arc 4)
    pricing-api/                 ← plughub-pricing-api — capacity-based billing, invoice calculation, reserve pool activation (Arc 2)
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
| calendar-api | Python | Python 3.11+ | FastAPI + asyncpg — port 3700 |
| workflow-api | Python | Python 3.11+ | FastAPI + asyncpg — port 3800 |
| skill-flow-worker | TypeScript | Node 20+ | Kafka consumer + SkillFlowEngine bridge |
| channel-gateway | Python | Python 3.11+ | FastAPI + aiokafka + channel adapters |
| pricing-api | Python | Python 3.11+ | FastAPI + asyncpg + openpyxl — port 3900 |

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

## Instance Bootstrap — reconciliation-driven agent instance management

Implemented in `packages/orchestrator-bridge/src/plughub_orchestrator_bridge/instance_bootstrap.py`.

**Principle**: Agent Registry is the single source of truth. The Bootstrap operates as a
**reconciliation controller** (Kubernetes-style): it compares *desired state* (Registry)
with *actual state* (Redis) and applies only the minimum diff to converge them.
No restart needed for any configuration change — the controller self-heals.

### Reconciliation algorithm

```
reconcile(tenant_id):
  # Section A — Agent instances
  agent_types    = GET /v1/agent-types
  registry_pools = GET /v1/pools          ← single call, all pools
  desired        = build_desired_state(agent_types, registry_pools)
  actual         = scan {tenant}:instance:* from Redis

  diff:
    to_create  → write instance key + SADD pool SET
    to_delete  → status=ready: DELETE + SREM  |  status=busy: mark draining=True
    to_update  → status=ready: update payload  |  status=busy: mark pending_update=True
    to_renew   → EXPIRE only (payload identical, TTL refresh)

  sync pool:*:instances SETs

  # Section B — Pools
  for each pool in registry_pools:
    if pool_config key missing or content diverged → SET pool_config:{pool_id}
    else → EXPIRE only (renew TTL)

  for each pool_config:* key in Redis NOT in registry_pools:
    DELETE pool_config:{pool_id}
    if pool:{pool_id}:instances SET is empty → DELETE it too

  sync {tenant}:pools global SET (+adds, -removes)
```

### Trigger points

| Trigger | Action |
|---|---|
| Bridge startup | `reconcile()` — full diff + apply; logs ReconciliationReport |
| Heartbeat every 15s | `_heartbeat_tick()` — TTL renewal + drain/pending_update processing |
| Every 5 min (periodic) | `reconcile()` — auto-healing of any drift |
| `registry.changed` (Kafka) | `reconcile()` — immediate after signal |
| `config.changed` namespace=`quota` (Kafka) | `reconcile()` — quota limits changed, may affect instance count |

### Dry-run (audit without applying)

```python
report = await bootstrap.dry_run("tenant_demo")
print(report.summary())
# tenant=tenant_demo created=2 deleted=1 drained=0 updated=1 renewed=7 unchanged=0 errors=0 (45ms)
```

### ReconciliationReport fields

Instances: `created`, `deleted`, `drained`, `updated`, `renewed`, `unchanged`

Pools: `pools_written` (created or updated), `pools_removed` (deleted from Redis), `pools_set_sync` (IDs added/removed from `{tenant}:pools` SET)

Common: `errors`, `duration_ms`, `dry_run`

### Rules

- Human agents are NOT managed — login is user-initiated via Agent Assist UI.
- Busy/paused instances are never hard-deleted; they receive `draining=True` or `pending_update=True` and are processed by the heartbeat after the session ends.
- Idempotent: reconciling N times produces the same result as reconciling once.
- Instance IDs: `{agent_type_id}-{n+1:03d}` (e.g. `agente_demo_ia_v1-001`).
- `channel_types` on instances = union of `channel_types` from all associated pools.

### RegistrySyncer — YAML as single source of truth for PostgreSQL

Implemented in `packages/orchestrator-bridge/src/plughub_orchestrator_bridge/registry_syncer.py`.
Runs BEFORE InstanceBootstrap at bridge startup. Reads `infra/registry/*.yaml` and:

1. **Upserts** pools and agent_types via Agent Registry REST API (POST → 201 created, 409 → PATCH)
2. **Prunes** stale agent_types not declared in YAML (`REGISTRY_SYNC_PRUNE=true`, default)
   - Lists all agent_types via `GET /v1/agent-types` and DELETEs any not present in the YAML
   - DELETE publishes `registry.changed` to Kafka → InstanceBootstrap cleans up Redis automatically
   - Set `REGISTRY_SYNC_PRUNE=false` to disable (multi-tenant environments with external agent registrations)

A fresh environment is fully self-configuring from YAML alone. Stale entries from old seeds or manual API calls are removed automatically on every startup — making DROP TABLE unnecessary.

### Skill sync — YAML → Agent Registry (PostgreSQL)

In addition to pools and agent_types, RegistrySyncer also syncs **skill definitions** from
`packages/skill-flow-engine/skills/` to the Agent Registry at bridge startup.

**`skills_dir` parameter** — path to the skills directory passed to RegistrySyncer:
```python
syncer = RegistrySyncer(
    registry_url=AGENT_REGISTRY_URL,
    config_path=REGISTRY_CONFIG_DIR or None,
    skills_dir=SKILLS_DIR or None,        # e.g. /app/skills
)
```

**Requirements for a YAML to be synced:**
- Must have `id:` field matching regex `^skill_[a-z0-9_]+_v\d+$` (e.g. `skill_sac_ia_v1`)
- Must have `entry:` and `steps:` fields (minimal valid SkillFlow)
- `name:`, `version:`, `description:`, `classification:` are optional (sensible defaults applied)
- `mention_commands:` at the top-level YAML is included in the payload if present

Skills are PUT (upserted) before pools and agent_types to ensure agent_types can reference them.
Skill IDs that don't match the regex (e.g. missing `id:` field) are silently skipped.

**`SyncReport`** extended fields: `skills_upserted`, `skills_skipped`, `skills_errors`.

### Skill hot-reload — three-elo architecture

The skill hot-reload pipeline ensures that updating a YAML file propagates to running agents
without manual cache clearing. Three components work together:

```
Elo 1 — RegistrySyncer (startup sync)
  bridge restart → reads *.yaml from SKILLS_DIR
  → PUT /v1/skills/{skill_id} → PostgreSQL is source of truth

Elo 2 — registry.changed event (agent-registry/routes/skills.ts)
  PUT /v1/skills/{id} → publishRegistryChanged(entity_type="skill", entity_id=skill_id)
  DELETE /v1/skills/{id} → publishRegistryChanged(entity_type="skill", entity_id=skill_id)
  → Kafka topic: registry.changed

Elo 3 — cache invalidation (orchestrator-bridge/main.py)
  registry.changed received → entity_type == "skill"
  → del _skill_flow_cache[skill_id]
  → next agent activation fetches updated flow from Agent Registry
```

**Live production update (no restart required):**
```
PUT /v1/skills/skill_copilot_sac_v1  →  registry.changed  →  cache invalidated  →  immediate effect
```

**`_skill_flow_cache`** — in-memory dict in orchestrator-bridge `main.py` mapping
`skill_id → flow dict`. Populated on first agent activation (GET /v1/skills/{id}).
Invalidated individually per skill_id on `registry.changed` events.

**Note:** POST (create) on `/v1/skills` does NOT publish `registry.changed` — it is only
used by RegistrySyncer at startup, where a cache miss on first activation is acceptable.

**Known issue:** `agente_avaliacao_v1.yaml` has no `complete` or `escalate` step, which
causes Agent Registry to return HTTP 422. RegistrySyncer logs a warning and increments
`skills_errors` but does not block startup. The evaluator agent falls back to reading the
YAML file directly via `_load_yaml_fallback()`.

### Impact on seed

`infra/seed/seed.py` no longer writes Redis instance keys, pool instance sets, pool_config
keys, or the `{tenant}:pools` SET — all of those are handled exclusively by InstanceBootstrap.
The seed only registers pools and agent types in the Agent Registry API (PostgreSQL).

## Context-Aware Progressive Resolution

Padrão para coleta e acumulação inteligente de dados do cliente ao longo da sessão.
Evita re-coletar dados já presentes com confiança suficiente.

### ContactContext (`@plughub/schemas/contact-context.ts`)

Schema em `packages/schemas/src/contact-context.ts`. Armazenado em `pipeline_state.contact_context`.

Cada campo é um `ContactContextField`:
```typescript
{ value: string, confidence: number, source: ContactContextSource, resolved_at?: string }
```

**Fontes (ContactContextSource):**
| Source | Descrição |
|---|---|
| `pipeline_state` | Herdado de agente anterior na mesma sessão |
| `insight_historico` | Memória de longo prazo (contatos anteriores) |
| `insight_conversa` | Gerado na sessão atual por outro step |
| `mcp_call` | Consultado via MCP tool (CRM, billing, etc.) |
| `customer_input` | Fornecido diretamente pelo cliente nesta sessão |
| `ai_inferred` | Inferido pelo AI Gateway a partir da conversa |

**Modelo de confiança:**
| Range | Significado |
|---|---|
| 0.9–1.0 | Confirmado explicitamente — usar sem confirmação |
| 0.7–0.9 | Inferido com alta certeza — usar sem confirmação |
| 0.4–0.7 | Incerto — confirmar se `force_confirmation = true` |
| 0.0–0.4 | Desconhecido — coletar novamente |

**Campos:**
`customer_id`, `cpf`, `account_id`, `nome`, `telefone`, `email`, `motivo_contato`,
`intencao_primaria`, `sentimento_atual`, `resumo_conversa`, `resolucoes_tentadas[]`,
`dados_crm` (raw MCP payload), `campos_ausentes[]`, `campos_incertos[]`, `completeness_score`

### agente_contexto_ia_v1

Pool: `contexto_ia` (role: specialist — sem tráfego direto de clientes).
Skill: `packages/skill-flow-engine/skills/agente_contexto_ia_v1.yaml`.

**Invocação:** via `task` step com `mode: assist` + `execution_mode: sync` em qualquer agente especialista.

**Fluxo interno (v2 — usa ContextStore + @ctx.*):**
```
verificar_gaps (choice):  @ctx.caller.customer_id exists → buscar_crm
                          @ctx.caller.cpf exists         → buscar_crm
                          default                        → verificar_completude
verificar_completude (choice): @ctx.caller.motivo_contato confidence_gte 0.7 → finalizar
                               default → gerar_pergunta
buscar_crm (invoke: mcp-server-crm/customer_get)
  → context_tags.outputs: nome/cpf/account_id/… → caller.* (confidence=0.95, fire-and-forget)
gerar_pergunta (reason LLM #1): pergunta consolidada → session.pergunta_coleta
coletar_cliente (menu): prompt = {{@ctx.session.pergunta_coleta}}
extrair_campos (reason LLM #2): campos extraídos → caller.* via context_tags
finalizar (complete)
```

**Garantias:**
- 0 chamadas LLM quando CRM resolve o contexto; no máximo 2 quando necessário coletar
- Nunca pergunta ao cliente o que já está com `confidence ≥ 0.8`
- Gera uma única pergunta consolidada (não formulário campo por campo)
- Busca CRM automaticamente antes de perguntar ao cliente
- Nunca bloqueia o fluxo — `on_failure` sempre avança (`finalizar_parcial`)

### Propagação entre agentes

O ContextStore (`{tenantId}:ctx:{sessionId}`) persiste durante toda a sessão.
Todos os agentes da cadeia lêem e escrevem no mesmo hash Redis — sem cópia entre agentes:

```
agente_sac_ia_v1
  → analisar (reason): lê @ctx.caller.nome/@ctx.session.historico_mensagens
                        escreve session.ultima_resposta, session.escalar_solicitado via context_tags
  → verificar_escalada (choice): @ctx.session.escalar_solicitado eq true → acumular_contexto
  → acumular_contexto (task assist: agente_contexto_ia_v1)
       agente_contexto_ia_v1 enriquece caller.* no ContextStore
  → escalar → agente_retencao_humano_v1
       supervisor_state devolve context_snapshot ao Agent Assist UI
       ContextoTab (aba Contexto) exibe campos agrupados por namespace
```

### Adicionando context-awareness a um novo agente especialista

```yaml
# Após a saudação, antes de qualquer step que dependa de dados do cliente:
- id: acumular_contexto
  type: task
  target:
    skill_id: agente_contexto_ia_v1
  mode: assist
  execution_mode: sync
  on_success: proximo_step
  on_failure: proximo_step   # nunca bloquear
```

### Fase 2 — Co-pilot (próxima iteração)

Durante sessão do agente humano, AI Gateway analisa cada mensagem do cliente em background
usando `contact_context` e popula a aba "Capacidades" do Agent Assist UI com:
- Sugestão de resposta personalizada
- Flags de risco (sentimento, intenção detectada)
- Ações recomendadas com base no `motivo_contato`

### Fase 3 — Step `resolve` nativo (futuro)

Novo step type no `skill-flow-engine` que encapsula a lógica do `agente_contexto_ia_v1`
de forma declarativa, permitindo que qualquer agente defina seus pré-requisitos de contexto
inline no YAML sem depender de um agente externo.

## ContextStore — unified session state

O ContextStore substitui `pipeline_state.contact_context` como repositório de estado de sessão.
É um Redis hash por sessão no qual qualquer componente pode ler e escrever campos tipados.

### Redis key format

```
{tenantId}:ctx:{sessionId}   (hash Redis)
  field = tag name (e.g. "caller.nome", "session.sentimento.current")
  value = JSON-encoded ContextEntry
```

### ContextEntry schema (`@plughub/schemas/context-store.ts`)

```typescript
ContextEntry {
  value:      unknown           // string | number | boolean | object
  confidence: number            // 0.0–1.0
  source:     string            // "mcp_call:mcp-server-crm:customer_get" | "ai_inferred:sentiment_emitter" | …
  visibility: "agents_only" | "all"
  updated_at: string            // ISO-8601
}
```

### Tag namespaces

| Namespace | Escopo | Escrito por |
|---|---|---|
| `caller.*` | Dados do cliente (nome, cpf, conta, motivo) | ContextAccumulator via MCP tools; reason step context_tags |
| `session.*` | Estado da sessão atual | reason/invoke steps via context_tags; sentiment_emitter (session.sentimento.*) |
| `account.*` | Dados de conta (plano, status) | invoke step com buscar_crm via context_tags |

### context_tags on reason / invoke steps

Qualquer step `reason` ou `invoke` pode declarar mapeamentos de entrada/saída:

```yaml
context_tags:
  inputs:
    nome_cliente:
      tag: caller.nome
      required: false       # campo opcional
  outputs:
    resposta:
      tag: session.ultima_resposta
      confidence: 1.0
      merge: overwrite      # overwrite | append
    sentimento:
      tag: caller.sentimento_atual
      confidence: 0.80
      merge: overwrite
```

- **inputs**: antes de chamar o LLM / MCP tool, lê `@ctx.<namespace>.<field>` e popula os inputs do step
- **outputs**: após resposta bem-sucedida, extrai campos do output e grava no ContextStore (fire-and-forget)
- **confidence**: confiança default do entry; pode ser sobrescrita por campo

### @ctx.* references in step inputs

Qualquer campo de `input:` ou `message:` pode usar `@ctx.<namespace>.<field>`:

```yaml
input:
  nome_cliente:  "@ctx.caller.nome"       # resolve ContextEntry.value
  historico:     "@ctx.session.historico_mensagens"
message: "{{@ctx.session.ultima_resposta}}"
```

Resolução: lê o hash Redis, parseia o ContextEntry, retorna `entry.value`. Retorna `""` se ausente.

### @ctx.* in choice step conditions

```yaml
conditions:
  - field:     "@ctx.caller.customer_id"
    operator:  exists                # field present with any value
    next:      buscar_crm
  - field:     "@ctx.caller.motivo_contato"
    operator:  confidence_gte        # confidence >= value
    value:     0.7
    next:      finalizar
```

Operadores suportados: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `exists`, `not_exists`, `confidence_gte`.

### required_context (YAML header)

```yaml
required_context:
  caller.nome:
    min_confidence: 0.8
  caller.motivo_contato:
    min_confidence: 0.7
    optional: true
```

O engine pré-computa um `GapsReport` antes do primeiro step e escreve `@ctx.__gaps__` no ContextStore.
O step inicial pode inspecionar os gaps para decidir se precisa coletar dados.

### McpInterceptor auto-accumulation

O `McpInterceptor` (em `@plughub/sdk`) detecta `contextRegistry[serverName][toolName]` e extrai
inputs/outputs automaticamente, antes e depois de cada `callTool()` bem-sucedido.
Os agentes nativos que usam o SDK recebem acumulação de contexto sem código adicional.

### AI Gateway — sentiment_emitter writes

`write_context_store_sentiment(redis, tenant_id, session_id, score)` é chamado dentro de
`SessionManager.update_partial_params` após cada turno LLM.
Escreve dois campos:

| Tag | Valor | Confidence | Source |
|---|---|---|---|
| `session.sentimento.current` | score arredondado (4 decimais) | 0.80 | `ai_inferred:sentiment_emitter` |
| `session.sentimento.categoria` | "satisfied" / "neutral" / "frustrated" / "angry" | 0.80 | `ai_inferred:sentiment_emitter` |

TTL: 14 400 s (4 horas). Fire-and-forget: nunca levanta exceção.

### supervisor_state — context_snapshot

O MCP tool `supervisor_state` lê o ContextStore diretamente do Redis em vez de buscar
em `pipeline_state.contact_context`. Retorna:

```json
"customer_context": {
  "context_snapshot": {
    "caller.nome":                { "value": "João", "confidence": 0.95, "source": "mcp_call:...", ... },
    "session.sentimento.current": { "value": -0.41, "confidence": 0.80, "source": "ai_inferred:...", ... }
  },
  "contact_context": null   // null quando context_snapshot presente; legacy fallback
}
```

### Agent Assist UI — ContextoTab

A aba "Contexto" detecta automaticamente qual formato usar:
- **`context_snapshot` presente** → renderiza `ContextSnapshotCard` (teal) com campos agrupados por namespace
- **Apenas `contact_context` presente** → renderiza `ContactContextCard` (emerald) — fallback legado

Fontes como `mcp_call:mcp-server-crm:customer_get` são exibidas como "CRM".
Entradas com `visibility: "agents_only"` exibem um badge âmbar 🔒.

### agente_contexto_ia_v1 — versão 2 (simplificada)

A versão 2 do skill usa `choice` com `@ctx.*` em vez de múltiplas chamadas LLM:

```
verificar_gaps (choice):
  @ctx.caller.customer_id exists  → buscar_crm
  @ctx.caller.cpf exists          → buscar_crm
  default                         → verificar_completude

verificar_completude (choice):
  @ctx.caller.motivo_contato confidence_gte 0.7  → finalizar
  default                                         → gerar_pergunta

buscar_crm (invoke: mcp-server-crm/customer_get):
  context_tags.outputs: nome/cpf/account_id/telefone/email/plano_atual/status_conta
  → confidence 0.95, source mcp_call

gerar_pergunta (reason LLM #1):
  context_tags.outputs: pergunta → session.pergunta_coleta

coletar_cliente (menu):
  prompt: "{{@ctx.session.pergunta_coleta}}"

extrair_campos (reason LLM #2):
  context_tags.outputs: todos os campos extraídos → caller.*
```

0 chamadas LLM quando CRM resolve o contexto; no máximo 2 quando é necessário coletar do cliente.

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

## Skill Flow — eleven step types

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
| `suspend` | Suspends workflow until external signal (approval, input, webhook, timer) | workflow-api |
| `collect` | Contacts target via channel, awaits response, suspends until replied or expired | workflow-api → Channel Gateway |

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
| `registry.changed` | Agent Registry | Routing Engine, Core, orchestrator-bridge |
| `config.changed` | Config API | orchestrator-bridge, routing-engine |
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
- Never write masked input values to `pipeline_state`, Redis, stream, or logs — `masked_scope` is in-memory only, cleared at `end_transaction`
- Never allow AI agents to emit `@mention` commands — only `role: primary` or `role: human` participants may issue mentions; AI agents use `task` step for coordination
- Never route a `@mention` to a pool not listed in `mentionable_pools` of the origin pool — domain is always closed by pool configuration

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

## @mention — protocolo de endereçamento de participantes

Permite que agentes humanos enviem comandos a qualquer agente especialista em conferência usando sintaxe `@alias`. Spec completa: `docs/guias/mention-protocol.md`.

### Regras fundamentais

- Apenas `role: primary` ou `role: human` podem emitir mentions com efeito de roteamento
- O domínio de aliases possíveis é fechado pela configuração `mentionable_pools` do pool de origem
- A mensagem é sempre entregue a todos os participantes `agents_only` — o roteamento é adicional, não substitutivo
- A confirmação de convite é o evento `participant_joined` (já existente) — sem ack separado

### Pool configuration

```yaml
pools:
  - id: retencao_humano
    mentionable_pools:
      copilot:  copilot_retencao     # @copilot → recruta do pool copilot_retencao
      billing:  billing_especialista # @billing → recruta do pool billing_especialista
```

### Sintaxe com interpolação de contexto

```
@billing conta=@ctx.caller.account_id motivo=@ctx.caller.motivo_contato
@copilot cliente tem plano @ctx.caller.plano_atual|"não identificado"
@billing @suporte analise o contexto    ← múltiplos destinatários
```

Referências `@ctx.*` são resolvidas pelo mcp-server-plughub antes do roteamento. Fallback inline: `@ctx.campo|"default"`.

### `mention_commands` no skill YAML

```yaml
mention_commands:
  ativa:
    action:
      set_context: { session.copilot.mode: "active" }
    acknowledge: true
  pausa:
    action:
      set_context: { session.copilot.mode: "passive" }
    acknowledge: true
  para:
    action:
      terminate_self: true
```

Ações disponíveis: `set_context` (escreve no ContextStore), `trigger_step` (salta para step do flow), `terminate_self` (agente sai da conferência).

---

## Masked Input — captura segura de dados sensíveis

Garante que dados altamente sensíveis (senhas, PINs, OTPs) nunca entrem no stream, `pipeline_state`, Redis ou logs. Spec completa: `docs/guias/masked-input.md`.

### Atributo `masked` no menu step

```yaml
- id: coletar_senha
  type: menu
  interaction: form
  masked: true                    # step-level: todos os campos
  fields:
    - id: senha
      masked: true                # ou field-level individual
```

### `begin_transaction` / `end_transaction`

Toda captura sensível → validação → ação é uma unidade atômica. Falha em qualquer step dentro do bloco descarta o `masked_scope` e executa `on_failure`.

```yaml
- id: tx_inicio
  type: begin_transaction
  on_failure: coletar_senha       # rewind explícito — nunca inferido

- id: coletar_senha
  type: menu
  masked: true
  ...

- id: validar
  type: invoke
  input:
    senha: "@masked.senha"        # namespace @masked.* — lê do scope em memória

- id: tx_fim
  type: end_transaction           # caminho feliz — rollback é sempre implícito
  result_as: operacao_status
```

### Invariantes

- `masked_scope` existe apenas em memória — nunca escrito em Redis, `pipeline_state` ou stream
- `end_transaction` é exclusivamente o caminho de sucesso; rollback é automático e implícito
- `reason` step dentro de bloco masked é erro de design, rejeitado pelo agent-registry
- Retry nunca re-usa valor mascarado — recoleta sempre exige nova entrada do usuário
- Audit record inclui `masked_input_fields: string[]` registrando quais campos foram omitidos
- Channels sem `supports_masked_input` executam `masked_fallback` — nunca tentam renderizar o formulário

### ChannelCapabilities

```typescript
supports_masked_input?: boolean   // default: false
masked_fallback?: "message" | "link" | "decline"
```

| Canal | Suporte | Comportamento |
|---|---|---|
| `webchat` | `true` | Overlay fora do chat; `<input type="password">`; placeholder no replay |
| `whatsapp` | `false` | `masked_fallback` configurado |
| `voice` | `true` | DTMF nativo — semântico |
| `sms`, `email` | `false` | `masked_fallback` configurado |

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

### Masked fields delivery chain

`masked_fields` propagates from the Skill Flow Engine through the full delivery stack:

| Layer | File | Change |
|---|---|---|
| Skill Flow Engine | `skill-flow-engine/src/steps/menu.ts` | Computes `maskedFieldIds[]` from `step.masked` + field-level `field.masked`, passes to `notification_send` |
| BPM tool | `mcp-server-plughub/src/tools/bpm.ts` | Reads `masked_fields` from `notification_send` args, includes in `conversations.outbound` Kafka payload |
| Channel Gateway models | `channel-gateway/models.py` | `WsMenuRender.masked_fields: list[str] \| None` |
| Outbound consumer | `channel-gateway/outbound_consumer.py` | Extracts `masked_fields`, logs warning for non-webchat channels, passes to `WsMenuRender` |
| Stream subscriber | `channel-gateway/stream_subscriber.py` | Conditionally adds `masked_fields` to `interaction.request` WS event |

WebChat renders masked fields as `<input type="password">` overlay (outside chat transcript).
Non-webchat channels use `masked_fallback` (configured per channel).

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

- ~~**Stream TTL expirado pós-session_ended**~~: ✅ `StreamExpiredError` levantado em `StreamSubscriber.messages()` quando cliente reconecta com cursor != "0" mas `EXISTS session:{id}:stream` retorna 0. `_stream_delivery_loop` captura e envia `{"type": "conn.session_ended", "reason": "session_expired"}`. Falha no EXISTS presume que stream existe (graceful degradation).
- ~~**jwt_secret por tenant**~~: ✅ `_decode_token` agora async: (1) decode sem verificação para ler `tenant_id`; (2) lookup Redis `{tenant_id}:config:webchat:jwt_secret`; (3) fallback para `settings.jwt_secret`. Single-tenant sem mudança de config. Tests: `TestStreamExpiredReconnect` (2 cases) + `TestMultiTenantJwtSecret` (3 cases). Total channel-gateway: 168/168.

## Pricing Module — capacity-based billing

Implementado em `packages/pricing-api/` (Python FastAPI, porta 3900). Princípio: cobrança por capacidade configurada, não por consumo. Dados de consumo variável permanecem visíveis no painel para curadoria de qualidade, mas não entram no cálculo de faturamento.

### Modelo de cobrança

Dois componentes:

| Componente | Descrição | Granularidade |
|---|---|---|
| **Base capacity** | Recursos sempre ativos (ai_agent, human_agent, whatsapp_number, etc.) | Mensal proporcional (dias úteis no ciclo) |
| **Reserve pools** | Capacidade adicional ativada/desativada manualmente | Dia inteiro por ativação (full-day billing) |

**Full-day billing para reserve pools**: se um pool é ativado em qualquer momento do dia D, o dia D inteiro é faturável. O detalhe de ativação/desativação é persistido em `pricing.reserve_activation_log` com datas de tipo `DATE` (sem horário).

### PostgreSQL schema

```sql
-- Recursos configurados por instalação
CREATE TABLE pricing.installation_resources (
    id               UUID PRIMARY KEY,
    tenant_id        TEXT NOT NULL,
    installation_id  TEXT NOT NULL DEFAULT 'default',
    resource_type    TEXT NOT NULL,  -- ai_agent | human_agent | whatsapp_number | ...
    quantity         INT  NOT NULL,
    pool_type        TEXT NOT NULL DEFAULT 'base',  -- base | reserve
    reserve_pool_id  TEXT,           -- pool lógico para agrupar recursos de reserva
    active           BOOL NOT NULL DEFAULT TRUE,
    billing_unit     TEXT NOT NULL DEFAULT 'monthly',
    label            TEXT NOT NULL DEFAULT '',
    created_at       TIMESTAMPTZ DEFAULT now(),
    updated_at       TIMESTAMPTZ DEFAULT now()
);

-- Log de ativações de reserve pools (full-day billing)
CREATE TABLE pricing.reserve_activation_log (
    id                 UUID PRIMARY KEY,
    tenant_id          TEXT NOT NULL,
    reserve_pool_id    TEXT NOT NULL,
    activation_date    DATE NOT NULL,
    deactivation_date  DATE,          -- NULL = ainda ativo
    activated_by       TEXT NOT NULL DEFAULT 'operator',
    created_at         TIMESTAMPTZ DEFAULT now(),
    UNIQUE (tenant_id, reserve_pool_id, activation_date)
);
```

### Preços padrão (Config API — namespace `pricing`)

| Recurso | Preço mensal (BRL) |
|---|---|
| `ai_agent` | 120,00 |
| `human_agent` | 50,00 |
| `whatsapp_number` | 15,00 |
| `voice_trunk_in` | 40,00 |
| `voice_trunk_out` | 40,00 |
| `email_inbox` | 25,00 |
| `sms_number` | 10,00 |
| `webchat_instance` | 20,00 |

`reserve_markup_pct` (padrão `0.0`): surcharge percentual aplicado ao preço de reserve pools.
`billing_cycle_day` (padrão `1`): dia do mês em que o ciclo de cobrança se inicia.

### Cálculo de fatura

```
# Base items
daily_rate  = unit_price / billing_days
subtotal    = daily_rate × quantity × billing_days   # (sempre billing_days para base)

# Reserve items
reserve_unit = unit_price × (1 + reserve_markup_pct / 100)
reserve_daily = reserve_unit / billing_days
subtotal      = reserve_daily × quantity × days_active  # days_active = dias distintos do log
```

### Endpoints

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/v1/pricing/invoice/{tenant_id}` | Fatura em JSON (ciclo atual ou explícito) |
| `GET` | `/v1/pricing/invoice/{tenant_id}?format=xlsx` | Export XLSX com layout de fatura |
| `GET` | `/v1/pricing/resources/{tenant_id}` | Lista recursos configurados |
| `POST` | `/v1/pricing/resources/{tenant_id}` | Upsert recurso (admin) |
| `DELETE` | `/v1/pricing/resources/{tenant_id}/{resource_id}` | Remove recurso (admin) |
| `POST` | `/v1/pricing/reserve/{tenant_id}/{pool_id}/activate` | Ativa reserve pool (admin) |
| `POST` | `/v1/pricing/reserve/{tenant_id}/{pool_id}/deactivate` | Desativa reserve pool (admin) |
| `GET` | `/v1/pricing/reserve/{tenant_id}/activity` | Log de ativações |

Auth: `X-Admin-Token` header verificado contra `Settings.admin_token` (vazio = sem auth).

### Arquivos principais

| Arquivo | Responsabilidade |
|---|---|
| `db.py` | DDL + CRUD: `list_resources`, `upsert_resource`, `delete_resource`, `set_reserve_active`, `record_activation`, `record_deactivation`, `list_activation_log`, `count_active_days` |
| `calculator.py` | `PricingCalculator.calculate()` → `Invoice` dataclass; `invoice_to_xlsx()` openpyxl; `load_price_table()` via Config API com fallback |
| `router.py` | FastAPI endpoints, `require_admin` dependency, `load_price_table` importado do calculator |
| `main.py` | FastAPI app + lifecycle startup/shutdown (asyncpg pool) |
| `config.py` | `Settings` com prefixo `PLUGHUB_PRICING_`; `config_api_url`, `admin_token`, `port` |
| `tests/test_calculator.py` | 23 unit tests: TestUnitPrice, TestBaseCalculation, TestReserveCalculation, TestBillingCycle, TestInvoiceToDict, TestXlsxExport |
| `tests/test_router.py` | 16 integration tests: TestHealth, TestGetInvoice (4), TestResources (5), TestReserveActivation (4), TestActivationLog (2) |

Total: **39/39 testes passando**.

### Operator Console — PricingPanel

`packages/operator-console/src/components/PricingPanel.tsx` — dois tabs:

- **Invoice tab**: tabela de base items + grupos de reserve pools com toggle Activate/Deactivate; totais por seção; GrandTotal em destaque; botão export XLSX via link direto `?format=xlsx`.
- **Consumption tab**: dados de `GET /reports/usage` da analytics-api; agrega por dimensão com nota explícita "não incluído no faturamento — disponível para curadoria de qualidade".
- **ResourceSidebar**: lista de recursos agrupados por pool_type + campo de admin token local.

Hooks: `packages/operator-console/src/api/pricing-hooks.ts` — `useInvoice`, `useResources`, `useActivationLog`, `activateReservePool`, `deactivateReservePool`. Todos usam URL relativa (`VITE_PRICING_API_BASE_URL ?? ''`) para proxy Vite.

Proxy Vite: `/v1/pricing` → `http://localhost:3900`.

### Config API — namespace `pricing`

Quatro chaves seedadas em `packages/config-api/src/plughub_config_api/seed.py`:
- `pricing.currency` — `"BRL"`
- `pricing.unit_prices` — mapa recurso→preço mensal
- `pricing.reserve_markup_pct` — `0.0`
- `pricing.billing_cycle_day` — `1`

Editáveis por tenant via ConfigPanel do Operator Console (namespace `pricing`).

## Pending (next iteration)

### Arc 2 — fechamento

- ~~E2E scenario 12: webchat auth flow + media upload end-to-end~~ ✅
- ~~Usage Metering no Channel Gateway (voice_minutes, whatsapp_conversations, sms_segments)~~ ✅
- ~~WebChat reconexão fase 2: tratar stream TTL expirado + jwt_secret por tenant~~ ✅
- AttachmentStore fase 2: S3/MinIO
- Magic bytes validation no upload (phase 2)
- ~~Pricing Module v1: planos, tarifas, ciclo de billing~~ ✅

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

7. ~~**operator-console fase 2 — drill-down read-only**~~: ✅ pool → lista de sessões ativas → transcrição ao vivo. Backend: `sessions.py` em `analytics-api` — `GET /sessions/active` (ClickHouse `closed_at IS NULL` + Redis pipeline LRANGE sentiment, sorted worst-first), `GET /sessions/{id}/stream` (SSE: evento `history` com XRANGE + eventos `entry` via XREAD bloqueante, keepalive 15s), e `GET /sessions/customer/{customer_id}` (histórico de contatos fechados por cliente, `ORDER BY opened_at DESC`, com `FINAL` para dedup ReplacingMergeTree). ClickHouse `sessions` table acrescida de coluna `customer_id Nullable(String)` + migration idempotente `ALTER TABLE ADD COLUMN IF NOT EXISTS`. Consumer/models: `parse_inbound` e `parse_conversations_event` passam `contact_id`/`customer_id` do evento Kafka. Frontend: `useActiveSessions` (poll 10s), `useSessionStream` (EventSource SSE), `SessionList`, `SessionTranscript`, `HeatmapGrid`/`PoolTile` drill-down. `App.tsx` refatorado para 3 níveis: heatmap → sessions → transcript. Build: 168 kB JS gzip 53 kB. Tests: `test_sessions.py` (54 assertions — TestClassify, TestSafeJson, TestParseEntry, TestFetchActiveSessions, TestOverlaySentiment, TestListActiveSessionsEndpoint, TestFetchCustomerHistory, TestCustomerHistoryEndpoint). Total analytics-api: 149/149.

8. ~~**operator-console fase 3 — intervenção ativa**~~: ✅ Supervisores humanos entram em sessões ativas diretamente via REST (bypass do ciclo MCP agent_login). Backend: `packages/analytics-api/src/plughub_analytics_api/supervisor.py` — `POST /supervisor/join` (cria `supervisor:{session_id}:active` no Redis TTL 4h, XADD `participant_joined` agents_only), `POST /supervisor/message` (XADD `message` no formato `StreamSubscriber._map_event()`, visibility `agents_only` ou `all`), `POST /supervisor/leave` (XADD `participant_left`, DELETE Redis key, idempotente). Router wired em `main.py`. Frontend: `SupervisorPanel.tsx` (composer com visibility toggle, Enter=send, Shift+Enter=newline, Leave button), `SupervisorJoinButton` (inline no header), `useSupervisor` hook (`join/message/leave` com estado `idle|joining|active|leaving|error`), `SupervisorState` type. `SessionTranscript.tsx` atualizado: botão "Entrar como supervisor" no header → `SupervisorPanel` na base quando ativo. Build: 173 kB JS gzip 54 kB.

9. ~~**Metabase setup**~~: ✅ `docker-compose.infra.yml` — serviços `metabase-driver-init` (baixa driver ClickHouse v1.3.2), `metabase` (v0.50.0, porta 3000, persiste em PostgreSQL), `metabase-setup` (one-shot via API Metabase). `infra/metabase/clickhouse_users.sql` — usuários CH read-only por tenant + Row Policies em 6 tabelas (sandboxing por `tenant_id` via conexão isolada). `infra/metabase/setup.py` — inicialização automatizada: admin account, conexões ClickHouse por tenant, 5 questions base (Sessões por Canal, Queue Events, Agent Performance, Usage Metering, Sentiment Timeline), dashboard "PlugHub Analytics" com grid de 5 cards. Acesso: http://localhost:3000 · admin@plughub.local.

10. ~~**Config Management Module — separação env vars × configuração de módulo**~~: ✅ `packages/config-api/` com tabela PostgreSQL `platform_config (tenant_id, namespace, key, value JSONB, updated_at)` + API REST CRUD (`GET/PUT/DELETE /config/{namespace}/{key}`) + seed de todos os valores atuais hardcoded. Leitura com cache Redis (TTL 60s) para não adicionar latência no hot path. ~~Fase 2: UI de visualização no operator-console~~ ✅ `ConfigPanel.tsx` — sidebar de namespaces, tabela de keys com valores resolvidos, EditDrawer com JSON editor inline, scope toggle (global vs tenant), admin token local.
    - **Dois níveis**: `tenant_id = '__global__'` para defaults de plataforma; tenant real para overrides específicos. Lookup: tenant wins over global.
    - **8 namespaces seedados**: `sentiment` (thresholds, live_ttl_s), `routing` (snapshot_ttl_s, sla_default_ms, score_weights, estimated_wait_factor, congestion_sla_factor), `session` (ai_gateway_ttl_s, channel_gateway_ttl_s), `consumer` (batch_size, timeout_ms, restart_delay_s, max_restart_delay_s), `dashboard` (sse_interval_s, sse_retry_ms), `webchat` (auth_timeout_s, attachment_expiry_days, upload_limits_mb), `masking` (authorized_roles, default_retention_days, capture_input_default, capture_output_default), `quota` (max_concurrent_sessions, llm_tokens_daily, messages_daily).
    - **`ConfigStore`**: `get()` (cache hit → DB miss), `get_or_default()`, `list_namespace()` (com cache de namespace), `list_all()`, `set()` (upsert + invalidação imediata), `delete()`. Invalidação global faz SCAN para limpar variantes de tenant.
    - **`config.changed` (Kafka)**: Config API publica no tópico `config.changed` após cada PUT/DELETE bem-sucedido. Payload: `{event, tenant_id, namespace, key, operation, updated_at}`. Consumidores roteiam por namespace:
      | Namespace | Consumidor | Reação |
      |---|---|---|
      | `quota` | orchestrator-bridge | `bootstrap.request_refresh()` — reconcilia instâncias |
      | `routing` | routing-engine (futuro) | invalida cache local de SLA/scoring |
      | `masking`, `session`, `webchat`, `sentiment`, `consumer`, `dashboard` | (cache Redis 60s) | sem ação imediata; propagação natural via TTL |
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

## Arc 4 — Workflow Automation

Permite que agentes nativos sejam usados como automação de processos com etapas manuais (aprovação, input, webhook, timer), sem BPM formal.

### Novos pacotes

- `packages/calendar-api/` — Python FastAPI, porta 3700. Engine puro de calendário.
- `packages/workflow-api/` — Python FastAPI, porta 3800. Ciclo de vida de WorkflowInstance.

### Novos schemas em `@plughub/schemas`

| Schema | Arquivo | Descrição |
|---|---|---|
| `SuspendStep` | `skill.ts` | Novo step type no FlowStepSchema |
| `WorkflowInstance` | `workflow.ts` | Registro persistido em PostgreSQL |
| `WorkflowTrigger`, `WorkflowResume` | `workflow.ts` | Requests de entrada |
| `WorkflowEvent` | `workflow.ts` | 7 eventos Kafka (started/suspended/resumed/completed/timed_out/failed/cancelled) |
| `HolidaySet`, `Calendar`, `CalendarAssociation` | `calendar.ts` | Hierarquia de calendários |
| `InstallationContext`, `ResourceScope` | `platform.ts` | Contexto de instalação |

### Calendar API — engine puro (no I/O)

| Função | Descrição |
|---|---|
| `is_open(associations, holidays, at)` | Verifica se uma entidade está aberta num instante |
| `next_open_slot(associations, holidays, after)` | Próxima janela aberta |
| `add_business_duration(associations, holidays, from_dt, hours)` | Deadline em horas úteis |
| `business_duration(associations, holidays, from_dt, to_dt)` | Horas úteis entre dois instantes |

Resolução de prioridade: exceptions > holidays > weekly_schedule.
Operadores: UNION (OR) + INTERSECTION (AND) por entidade.
Tests: `test_engine.py` — 25 assertions.

### Skill Flow `suspend` step

```typescript
// Flow definition
{ type: "suspend", id: "aguardar_aprovacao",
  reason: "approval",       // approval | input | webhook | timer
  timeout_hours: 48,
  business_hours: true,     // uses calendar-api for deadline
  on_resume:  { next: "processar" },
  on_timeout: { next: "escalar" },
  on_reject:  { next: "notificar_rejeicao" },
  notify: { visibility: "agents_only", text: "Token: {{resume_token}}" }
}
```

Mecanismo de idempotência (dois estágios): sentinel `"suspending"` → `"suspended"` em pipeline_state.results. Crash entre os dois stages resulta em re-suspend seguro na retomada.

`SkillFlowEngineConfig.persistSuspend` — callback opcional injetado pelo workflow-api worker. Quando ausente, deadline é wall-clock.
`engine.run({ resumeContext: { decision, step_id, payload } })` — sinal de retomada passa direto para o suspend step.

Tests: `suspend.test.ts` — 13 assertions.

### @mention — mention_commands handler (skill-flow-engine)

`packages/skill-flow-engine/src/mention-commands.ts` — pure async handler for specialist agent @mention commands.

| Export | Description |
|---|---|
| `parseCommandName(args_raw)` | Extracts first whitespace-delimited token from args_raw; `null` for bare mention |
| `handleMentionCommand(skill, commandName, ctx)` | Dispatches command: `set_context` → ContextStore write (fire-and-forget, non-fatal), `trigger_step` → returns `trigger_step` field for caller, `terminate_self` → returns flag for caller |

`MentionCommandResult`: `{ handled, acknowledge, trigger_step?, terminate_self }` — caller is responsible for Redis LPUSH and agent_done; this function does no I/O besides ContextStore writes.

Unknown commands return `{ handled: false }` — silently ignored per spec.

Tests: `mention-commands.test.ts` — 15 assertions (parseCommandName ×5, handleMentionCommand ×10: unknown, set_context ack/no-ack, multiple fields, no contextStore, ContextStore throws, trigger_step, terminate_self, empty mention_commands).

### Masked Input — begin_transaction / end_transaction step tests

`packages/skill-flow-engine/src/__tests__/steps/transaction.test.ts` — 9 unit tests for `executeBeginTransaction` and `executeEndTransaction`:
- `begin_transaction` clears maskedScope, sets `transactionOnFailure`, returns `__transaction_begin__`
- `end_transaction` clears maskedScope + transactionOnFailure, uses `__transaction_end__` or explicit `on_success`
- `result_as` persists `{ status: "ok", fields_collected: [...] }` — field names only, never values

`packages/skill-flow-engine/src/__tests__/engine-transaction.test.ts` — 5 engine integration tests:
- Happy path: `begin_transaction` → `menu(masked)` → `invoke(@masked.*)` → `end_transaction(result_as)` → `complete`; masked value passed to invoke, `tx_result` persisted without sensitive content
- Failure: invoke fails inside block → engine rewinds to `begin_transaction.on_failure`, maskedScope cleared
- Menu timeout inside block → rewind to `on_failure`

Total skill-flow-engine: **86/86 tests** (10 test files).

### agent-registry — masked block validation

`packages/agent-registry/src/validators/skill.ts` — `validateMaskedBlock(flow: SkillFlow): string[]`

Position-based BFS: for each `begin_transaction` at array position N, seeds BFS from `steps[N+1]` (matching engine's positional advance via `__transaction_begin__`). Visits success edges only (`on_success`, `choice.conditions[].next`, `choice.default`, `suspend.on_resume.next`, `collect.on_response.next`). Stops at `end_transaction`. Reports error for any `reason` step found inside the block.

HTTP 422 returned by both POST and PUT `/v1/skills` routes:
```json
{ "error": "invalid_masked_block", "details": ["Step \"bad_reason\" (reason) is inside masked transaction block..."] }
```

Tests: `packages/agent-registry/src/__tests__/skill-validator.test.ts` — 14 unit tests covering: no begin_transaction, empty steps, clean block, reason before/after block, reason directly inside, reason via on_success chain, reason via choice branch/default, on_failure exit (not visited), multiple blocks, last-step begin_transaction (no crash), end_transaction stops propagation.

### Workflow API — ciclo de vida

Tabela PostgreSQL `workflow.instances` (schema `workflow`).

| Endpoint | Chamado por | O que faz |
|---|---|---|
| `POST /v1/workflow/trigger` | Sistema externo / operator | Cria WorkflowInstance, emite `workflow.started` |
| `POST /v1/workflow/instances/{id}/persist-suspend` | Skill Flow worker (TS) | Calcula deadline (calendar-api ou wall-clock), persiste suspensão, emite `workflow.suspended` |
| `POST /v1/workflow/resume` | Sistema externo / aprovador | Valida token, verifica expiração, registra decisão, emite `workflow.resumed` |
| `POST /v1/workflow/instances/{id}/complete` | Skill Flow worker | Marca completed, emite `workflow.completed` |
| `POST /v1/workflow/instances/{id}/fail` | Skill Flow worker | Marca failed, emite `workflow.failed` |
| `POST /v1/workflow/instances/{id}/cancel` | Operator Console | Cancela active/suspended, emite `workflow.cancelled` |
| `GET /v1/workflow/instances` | Operator Console | Lista com filtros (tenant_id, status, flow_id) |
| `GET /v1/workflow/instances/{id}` | Operator Console | Detalhe |

**Timeout scanner** — asyncio background task (intervalo configurável, padrão 60s). `UPDATE ... SET status='timed_out' WHERE status='suspended' AND resume_expires_at < now()` — atômico, sem double-processing.

Tests: `test_router.py` — 27 assertions (TestTrigger, TestPersistSuspend, TestResume, TestComplete, TestFail, TestCancel, TestList, TestDetail, TestHealth, TestTimeoutScanner).

### Status transitions

```
active → suspended | completed | failed | cancelled
suspended → active (resume) | timed_out | cancelled
timed_out / failed / completed / cancelled → terminal
```

### Kafka topic: workflow.events

Publicado pelo workflow-api em todos os status transitions. Consumido pelo Skill Flow worker para disparar `engine.run()` com `resumeContext`.

### Implementado neste módulo

- `packages/skill-flow-worker/` — TypeScript worker: consome `workflow.events`, roda engine.run() com resumeContext, wired com persistSuspend callback para deadline calculation
- Operator Console — painel de instâncias Workflow (WorkflowPanel.tsx): status filter, timeline, resume token, cancel action
- Vite proxy configuration para `/v1/workflow` routes

### Collect Step — async multi-channel data collection

Novo step type `collect` no Skill Flow. Permite que um workflow entre em contato com um alvo (customer/agent/external) via qualquer canal, apresenta uma interação estruturada, e suspende até receber resposta ou expirar o prazo.

```typescript
// Flow definition
{ type: "collect", id: "coletar_cpf",
  target:        { type: "customer", id: "{{customer_id}}" },
  channel:       "whatsapp",
  interaction:   "form",
  prompt:        "Por favor informe seu CPF",
  fields:        [{ id: "cpf", label: "CPF", type: "text" }],
  delay_hours:   0,            // envio imediato (ou scheduled_at para horário absoluto)
  timeout_hours: 24,
  business_hours: true,
  campaign_id:   "camp_cobranca_jan",
  output_as:     "cpf_response",
  on_response:   { next: "processar_cpf" },
  on_timeout:    { next: "escalar_sem_resposta" },
}
```

#### Timing

| Parâmetro | Descrição |
|---|---|
| `scheduled_at` | ISO-8601 absoluto — quando contatar o alvo |
| `delay_hours` | Relativo: agora + N horas |
| (nenhum) | Envio imediato |
| `timeout_hours` | Quanto esperar pela resposta após o envio (business-hours-aware) |

#### Correlação via collect_token

O Skill Flow gera um UUID (`collect_token`) e o workflow-api o persiste no `collect_instances`. O channel-gateway lê o token nos metadados da sessão outbound e publica `collect.responded` ao fechar a sessão → workflow-api resume o workflow com `decision: "input"`.

#### Campaign = N instâncias com mesmo campaign_id

Não há entidade "campaign" separada. Um `campaign_id` é um agrupador livre em `workflow.instances` e `collect_instances`. A CampaignPanel do Operator Console agrega via `collect_events` no ClickHouse.

#### Implementado

- `packages/schemas/src/skill.ts` — `CollectTargetSchema`, `CollectStepSchema` (inclui scheduled_at, delay_hours, timeout_hours, business_hours, campaign_id)
- `packages/schemas/src/workflow.ts` — `CollectStatusSchema`, `CollectRequestedSchema`, `CollectSentSchema`, `CollectRespondedSchema`, `CollectTimedOutSchema`, `CollectEventSchema`; `campaign_id` em `WorkflowInstanceSchema`
- `packages/skill-flow-engine/src/steps/collect.ts` — executor com idempotência de dois estágios, resume path (input/timeout), wall-clock fallback
- `packages/skill-flow-engine/src/executor.ts` — `persistCollect?` callback em `StepContext`, dispatch `case "collect"`
- `packages/workflow-api/src/plughub_workflow_api/db.py` — tabela `workflow.collect_instances` + funções CRUD; `campaign_id` em `workflow.instances`
- `packages/workflow-api/src/plughub_workflow_api/kafka_emitter.py` — `emit_collect_requested/sent/responded/timed_out` (topic `collect.events`)
- `packages/workflow-api/src/plughub_workflow_api/config.py` — `collect_topic: str = "collect.events"`
- `packages/workflow-api/src/plughub_workflow_api/router.py` — `POST /v1/workflow/instances/{id}/collect/persist`, `POST /v1/workflow/collect/respond`, `GET /v1/workflow/campaigns/{id}/collects`
- `packages/workflow-api/src/plughub_workflow_api/timeout_job.py` — scanner de collect_instances expiradas → collect.timed_out + resume with decision=timeout
- `packages/analytics-api/src/plughub_analytics_api/clickhouse.py` — tabelas `workflow_events` + `collect_events` (ReplacingMergeTree)
- `packages/analytics-api/src/plughub_analytics_api/models.py` — `parse_workflow_event`, `parse_collect_event`
- `packages/analytics-api/src/plughub_analytics_api/consumer.py` — topics `workflow.events` + `collect.events`
- `packages/analytics-api/src/plughub_analytics_api/reports_query.py` — `query_workflows_report`, `query_campaigns_report` (com summary aggregado por campaign_id)
- `packages/analytics-api/src/plughub_analytics_api/reports.py` — `GET /reports/workflows`, `GET /reports/campaigns`
- `packages/operator-console/src/components/CampaignPanel.tsx` — painel de campanhas: summary cards com response rate, mini-bar de status, detail com KPIs + channel breakdown + collect event list
- `packages/operator-console/src/api/campaign-hooks.ts` — `useCampaignData` hook (poll 30s)
- `packages/operator-console/src/types/index.ts` — `CollectEvent`, `CampaignSummary`, `campaign_id` em `WorkflowInstance`
- `packages/operator-console/src/components/Header.tsx` — botão "Campaigns" na nav
- `packages/operator-console/src/App.tsx` — view `campaigns` + `CampaignPanel`

#### Kafka topics

| Topic | Producer | Consumer(s) |
|---|---|---|
| `collect.events` | workflow-api (collect endpoints + timeout scanner) | analytics-api → ClickHouse collect_events |

#### Implementado (fase 2)

- ~~Skill Flow worker: mcpCall/aiGatewayCall com rotas HTTP reais~~ ✅
  - `mcpCall` → JSON-RPC `tools/call` com Authorization Bearer + MCP result unwrap
  - `aiGatewayCall` → `POST /v1/reason` (URL corrigida de `/infer`)
  - `persistCollect` → `POST /v1/workflow/instances/{id}/collect/persist` (novo callback)
  - `SkillFlowEngineConfig` estendido com `persistCollect` opcional; wired no `makeContext`
  - `WorkflowClient` + `config.ts` atualizados (`calendarApiUrl`, `mcpSessionToken`, `defaultTenantId`)
  - `worker.ts`: `decision` aceita `"input"` para collect responses; `response_data` propagado como payload
- ~~Operator Console — Config Management UI~~ ✅ (`packages/operator-console/src/components/ConfigPanel.tsx`)
  - Sidebar de namespaces (8: sentiment, routing, session, consumer, dashboard, webchat, masking, quota)
  - Tabela de keys com valor resolvido (tenant override wins over global)
  - EditDrawer com JSON editor inline + validação + scope selector (global vs tenant)
  - DELETE override (Reset) volta para o default global
  - Admin token local (salvo em estado, nunca persisted) requerido para mutations
  - `config-hooks.ts`: `useConfigAll`, `useConfigNamespace`, `putConfig`, `deleteConfig`
  - Vite proxy: `/config` → `http://localhost:3600` (config-api)
  - Botão "Config" (verde) adicionado na nav do Operator Console
  - Build: 202 kB JS / 60 kB gzip
- ~~E2E scenario 14~~ ✅ (collect step — ver tabela acima)

### ContextStore integration — origin_session_id

Workflows lançados a partir de uma sessão ativa de cliente (via `task` step `mode: transfer`,
escalação, ou coleta outbound) devem ler e escrever no ContextStore da sessão originadora —
não no hash do workflow UUID.

**Regra:** `{tenant}:ctx:{origin_session_id}` é o ContextStore key correto para @ctx.* em workflows.

**Campo `origin_session_id`** adicionado a:
- `WorkflowInstanceSchema` (`@plughub/schemas/workflow.ts`) — campo nullable, documenta a sessão originadora
- `workflow.instances` (PostgreSQL) — coluna `origin_session_id TEXT` com migration idempotente
- `TriggerRequest` (workflow-api `router.py`) — campo opcional no body do trigger
- `WorkflowInstance` interface (`skill-flow-worker/workflow-client.ts`) — campo opcional

**Resolução no EngineRunner** (`skill-flow-worker/engine-runner.ts`):
```typescript
// origin_session_id presente → usa ContextStore da sessão real do cliente
// origin_session_id ausente  → usa instance.id (headless/standalone workflow)
const contextSessionId = instance.origin_session_id ?? instance.id

await engine.run({
  tenantId:  instance.tenant_id,
  sessionId: contextSessionId,   // ← chave do ContextStore ({tenant}:ctx:{contextSessionId})
  instanceId: instance.id,       // ← UUID do workflow para pipeline_state e lifecycle
  ...
})
```

**Como usar no trigger:**
```json
POST /v1/workflow/trigger
{
  "tenant_id":         "tenant_demo",
  "flow_id":           "fluxo_cobranca_v1",
  "trigger_type":      "event",
  "session_id":        "sess_abc123",
  "origin_session_id": "sess_abc123",
  "context": { "invoice_id": "INV-001", "amount": 15000 }
}
```

Quando o workflow executa steps `reason` com `context_tags.inputs`, os campos
`@ctx.caller.nome`, `@ctx.caller.cpf` etc. são lidos do ContextStore da sessão `sess_abc123` —
onde foram acumulados pelo `agente_contexto_ia_v1` durante o atendimento.

**Workflows standalone** (sem sessão originadora — triggers de schedule, webhook externo):
`origin_session_id = null` → engine usa `{tenant}:ctx:{instance.id}` — hash isolado por workflow.

## Agent Assist UI — `packages/agent-assist-ui/`

React 18 + TypeScript + Vite. Porta de dev: 5173. Proxy: `/api` → mcp-server-plughub (3100), `/agent-ws` → WS mcp-server (3100), `/analytics` → analytics-api (3500).

### Layout

```
┌─────────────────────────────────────────┐
│  Header (agente, pool, sessão, SLA, WS) │
├────────────────────┬────────────────────┤
│  ChatArea (60%)    │  RightPanel (40%)  │
├────────────────────┴────────────────────┤
│  AgentInput + CloseModal trigger        │
└─────────────────────────────────────────┘
```

### Fluxo de sessão

1. UI abre em modo lobby (`wsSessionId=null`, conecta via `pool` no WS)
2. `conversation.assigned` chega via `pool:events:{poolId}` → `setSessionId`, `fetchHistory`, atualiza URL
3. Mensagens chegam por `message.text` WS events → adicionadas a `messages[]`
4. Agente encerra → `handleClose` → POST `/api/agent_done/{sessionId}` → volta ao lobby
5. Cliente desconecta → `session.closed` com `client_disconnect` → modal de encerramento pendente

### Componentes

| Componente | Responsabilidade |
|---|---|
| `Header` | Nome do agente, pool, session_id, status WS, SLA badge, timer de atendimento ao vivo |
| `ChatArea` | Lista de mensagens + indicador de digitação AI + painel de sentimento ao vivo |
| `AgentInput` | Input de texto, botão enviar, trigger do CloseModal |
| `CloseModal` | issue_status, outcome, handoff_reason antes de chamar agent_done |
| `RightPanel` | Tab container: Estado / Capacidades / Contexto / Histórico |
| `ToastContainer` | Notificações temporárias e persistentes |

### RightPanel — tabs

| Tab | Conteúdo |
|---|---|
| `estado` | `EstadoTab` — sentimento (score, trend, alert), intent, SLA, flags |
| `capacidades` | `CapacidadesTab` — suggested_agents + escalation suggestions |
| `contexto` | `ContextoTab` — historical_insights (azul) + conversation_insights (roxo) |
| `historico` | `HistoricoTab` — últimos 20 contatos fechados do cliente via analytics-api |

### HistoricoTab — implementação

- Hook `useCustomerHistory(customerId)` — fetch `GET /analytics/sessions/customer/{id}?tenant_id=VITE_TENANT_ID&limit=20`
- Env vars: `VITE_ANALYTICS_URL` (default `/analytics`), `VITE_TENANT_ID` (default `tenant_demo`)
- Re-busca automaticamente quando `customerId` muda
- Cancela fetch anterior em cada re-render (cleanup via flag `cancelled`)
- `HistoryRow` — expansível: summary (ícone de canal, badge de outcome, data, duração, close_reason) + detalhes (pool, canal, session_id)
- Estado vazio quando `customerId === null` ("Cliente não identificado")
- Graceful degradation: erro retorna `[]` com mensagem de erro não-bloqueante

### Auto-reconexão WebSocket

`useAgentWebSocket` — reconnect automático com delay de 3s em close inesperado:
- `reconnectCount` state: incrementado por `ws.onclose` quando `!intentionalClose.current`
- `intentionalClose` ref: setado no cleanup do useEffect (unmount ou mudança de dep)
- Dependency array: `[sessionId ?? poolId, reconnectCount]` — reconecta ao bump de `reconnectCount`
- Na reconexão, mcp-server entrega `pool:pending_assignment:{poolId}` (TTL 300s) para retomar sessão em andamento

### Handle-time counter

`Header.tsx` recebe `sessionStartedAt: Date | null` — prop passado de App.tsx quando `conversation.assigned` chega. `useEffect`/`setInterval` a cada 1s atualiza `handleMs = Date.now() - sessionStartedAt`. Formato: `M:SS` (< 1h) ou `H:MM:SS` (≥ 1h). Vira laranja após 30 minutos para alertar o agente. Resetado para `null` ao encerrar sessão em ambos os fluxos.

### Renderização de mensagens `agents_only`

**Backend fix** — `message_send` em `mcp-server/tools/session.ts` agora publica no canal Redis `agent:events:{session_id}` depois do XADD. Publicação ocorre para `visibility: "all"` e `"agents_only"` (não para arrays de participant_ids). O `author.type` no envelope WS é determinado consultando `{tenant_id}:agent:instance:{participant_id}` — se tiver `agent_type_id`, emite `"agent_ai"`, caso contrário `"agent_human"`. Entrega WS é best-effort (try/catch não-fatal).

**Gap corrigido** — o bridge de orquestração só encaminhava `conversations.inbound` (mensagens do cliente). Com essa mudança, mensagens de agentes IA com `visibility: "all"` e notas internas com `visibility: "agents_only"` chegam ao agente humano em tempo real.

**Frontend** — `ChatMessage.visibility?: string` e `WsMessageText.visibility?: string` adicionados em `types.ts`. `App.tsx` propaga `event.visibility` ao construir o `ChatMessage`. `MessageBubble.tsx` detecta `visibility === "agents_only"` e renderiza:
- Background âmbar (`bg-amber-50`) com borda tracejada âmbar (`border-dashed border-amber-400`)
- Badge "Interno" em âmbar antes do label do autor
- Posicionado à esquerda (nunca à direita, independente do autor)

### Menu de aprovação — renderização no chat (modo observação)

`ChatMenuData` interface adicionada em `types.ts` com campos `menu_id`, `interaction`, `prompt`, `options?`, `fields?`. `ChatMessage.menuData?: ChatMenuData` adicionado — quando presente, `MessageBubble.tsx` delega para `MenuCard` em vez de renderizar um bubble normal.

**`components/MenuCard.tsx`** (novo) — card read-only com badge de tipo de interação + label "IA → Cliente · observação". Renderizadores por tipo:

| Tipo | Renderização |
|---|---|
| `text` | Prompt + indicador "Aguardando resposta em texto livre…" |
| `button` | Chips com borda indigo arredondada, `disabled` |
| `list` | Lista numerada com itens separados por linha, `disabled` |
| `checklist` | Checkboxes com labels, todos `disabled` |
| `form` | Campos `<input>` com label acima, `disabled` |

**`App.tsx`** — evento `menu.render` agora popula `menuData` estruturado no lugar do texto plano com bullets. O campo `text` mantém o `prompt` como fallback para consumidores simples.

**Modo substituição (futuro)** — todos os elementos interativos têm apenas `disabled`; ativar substitution mode requer remover o atributo + adicionar handler `POST /api/menu_submit/{sessionId}`.

### Build: 566 kB JS / 164 kB gzip

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
| 13 | `13_workflow_automation.ts` | workflow automation Arc 4: trigger → persist-suspend → resume (approved) → complete + cancel path (13 assertions) |
| 14 | `14_collect_step.ts` | collect step Arc 4: trigger with campaign_id → persist-collect (token, send_at, expires_at, instance=suspended) → respond (elapsed_ms, workflow_resumed) → complete + campaign list (16 assertions) |
| 15 | `15_instance_bootstrap.ts` | instance bootstrap: Agent Registry → Redis instance keys (status=ready, TTL>0, source=bootstrap, channel_types), pool SET completeness, pool_config cache (--bootstrap flag) |
| 16 | `16_live_reconciliation.ts` | live reconciliation: POST new AgentType to Registry → await registry.changed → verify new instances appear in Redis ≤30 s, status=ready, source=bootstrap, TTL>0, pool SET updated (--reconcile flag) |
| 17 | `17_context_store.ts` | ContextStore: key format, caller/session namespace writes, sentiment rounding, TTL, supervisor_state context_snapshot (18 assertions) (--ctx flag) |
| 18 | `18_workflow_worker_chain.ts` | Kafka→worker→engine chain: trigger → workflow.started → skill-flow-worker consumes → engine suspend step → workflow.suspended Kafka → resume REST → workflow.resumed → engine complete step → workflow.completed Kafka (16 assertions) (--worker flag, 120s timeout) |
| 19 | `19_mention_copilot_auth.ts` | @mention co-pilot + masked PIN auth: Part A — agente_auth_ia_v1 happy path (valid PIN → resolved, PIN absent from pipeline_state); Part B — failure path (PIN 999999 → escalated_human, no leak); Part C — agente_copilot_v1 @mention trigger → LLM reason → analise.sugestao populated → terminate → resolved (14 assertions) (--mention flag, 90s timeout, requires demo stack + ANTHROPIC_API_KEY) |
| R  | `regressions.ts` | regression suite: ZodError em session_context_get, parsing de callTool (--regression flag) |

Run with: `ts-node runner.ts --conference` or `ts-node runner.ts --only 06` or `ts-node runner.ts --only 12` or `ts-node runner.ts --webchat` or `ts-node runner.ts --workflow` or `ts-node runner.ts --only 13` or `ts-node runner.ts --collect` or `ts-node runner.ts --only 14` or `ts-node runner.ts --bootstrap` or `ts-node runner.ts --only 15` or `ts-node runner.ts --reconcile` or `ts-node runner.ts --only 16` or `ts-node runner.ts --ctx` or `ts-node runner.ts --only 17` or `ts-node runner.ts --worker` or `ts-node runner.ts --only 18` or `ts-node runner.ts --mention` or `ts-node runner.ts --only 19`

Scenario 06 covers two parts:
- **Part A** — Conference happy path: primary agent busy → supervisor calls `agent_join_conference` → Redis `conference:*` keys verified → specialist `agent_done` with `conference_id` (session stays open) → primary `agent_done` closes session
- **Part B** — Reconnect resilience: agent busy → MCP transport torn down → new transport reconnected → re-login with same `instance_id` → Redis state (agent instance + active sessions) persists → `agent_done` with new session_token concludes session cleanly
