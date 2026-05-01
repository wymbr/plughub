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
| auth-api | Python | Python 3.11+ | FastAPI + asyncpg + bcrypt + python-jose — port 3200 |

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
auth-api        ← no internal dependencies (standalone user store)
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
| `conversations.participants` | orchestrator-bridge | analytics-api → ClickHouse participation_intervals |
| `rules.escalation.events` | Rules Engine | Routing Engine |
| `rules.shadow.events` | Rules Engine | Analytics (shadow/monitoring mode only — no routing side-effect) |
| `rules.session_tagged` | Rules Engine | Agent Registry |
| `registry.changed` | Agent Registry | Routing Engine, Core, orchestrator-bridge |
| `config.changed` | Config API | orchestrator-bridge, routing-engine |
| `gateway.heartbeat` | Channel Gateway | Routing Engine |
| `agent.done` | Routing Engine | Rules Engine, Analytics |
| `queue.position_updated` | Routing Engine | Channel Gateway, Analytics |
| `mcp.audit` | McpInterceptor / proxy sidecar | Analytics, LGPD |
| `sentiment.updated` | AI Gateway (`sentiment_emitter.py`) | analytics-api (Arc 3) |
| `evaluation.events` | evaluation-api | analytics-api → ClickHouse `evaluation_results` + `evaluation_events` (Arc 6) |

## Kafka event schemas — Zod coverage

All Kafka events that cross package boundaries now have Zod schemas in `@plughub/schemas`.
This enables compile-time validation and IDE autocomplete for producers and consumers.

### New files (added in Zod schema cleanup — ~day 0.5)

| File | Schemas defined |
|---|---|
| `packages/schemas/src/rules-events.ts` | `RulesEvaluationContextSchema`, `RulesEscalationEventSchema`, `RulesActiveEventSchema`, `RulesShadowEventSchema`, `RulesEventSchema` |
| `packages/schemas/src/platform-events.ts` | `RegistryChangedEventSchema`, `ConfigChangedEventSchema`, `SentimentUpdatedEventSchema`, `QueuePositionUpdatedEventSchema`, `RoutingResultEventSchema`, `ConversationRoutedEventSchema`, `AgentLifecycleEventSchema` (discriminated union of 7 variants), `ConversationsEventSchema` (discriminated union of 3 variants) |

### Topic → schema mapping

| Topic | Schema | File |
|---|---|---|
| `rules.escalation.events` | `RulesEscalationEventSchema` (`shadow_mode: false`) | `rules-events.ts` |
| `rules.shadow.events` | `RulesEscalationEventSchema` (`shadow_mode: true`) | `rules-events.ts` |
| `registry.changed` | `RegistryChangedEventSchema` | `platform-events.ts` |
| `config.changed` | `ConfigChangedEventSchema` | `platform-events.ts` |
| `sentiment.updated` | `SentimentUpdatedEventSchema` | `platform-events.ts` |
| `queue.position_updated` | `QueuePositionUpdatedEventSchema` | `platform-events.ts` |
| `conversations.routed` | `ConversationRoutedEventSchema` (`result.allocated: true`) | `platform-events.ts` |
| `conversations.queued` | `ConversationRoutedEventSchema` (`result.allocated: false`) | `platform-events.ts` |
| `agent.lifecycle` | `AgentLifecycleEventSchema` (discriminated by `event` field) | `platform-events.ts` |
| `conversations.events` | `ConversationsEventSchema` (discriminated by `event_type`) | `platform-events.ts` |
| `workflow.events` | `WorkflowEventSchema` | `workflow.ts` (existing) |
| `collect.events` | `CollectEventSchema` | `workflow.ts` (existing) |
| `usage.events` | `UsageEventSchema` | `usage.ts` (existing) |
| `conversations.participants` | `ConversationParticipantEventSchema` | `contact-segment.ts` (existing) |
| `mcp.audit` | `AuditRecordSchema` | `audit.ts` (existing) |
| `evaluation.events` | `EvaluationEventSchema` | `evaluation.ts` (Arc 6) |

### Topic name discrepancy note

CLAUDE.md previously documented two Rules Engine topics with incorrect names:
- ❌ `rules.escalation_triggered` → ✅ `rules.escalation.events`
- ❌ `rules.notification_triggered` → ✅ `rules.shadow.events` (with `shadow_mode: true`)

These corrections have been applied to the Kafka topics table above. The actual topic strings are
defined in `packages/rules-engine/src/plughub_rules/kafka_publisher.py`.

### Topics without Zod schemas (not yet wired in production)

These topics appear in the architecture documentation but are not published in the current codebase:

| Topic | Status |
|---|---|
| `conversations.abandoned` | Documented but not published by routing-engine code |
| `conversations.dequeued` | Documented but not published |
| `rules.session_tagged` | Documented but not published |

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
- **Never leave deferred phases undocumented** — whenever a scope is split into phases and only some are implemented, every unimplemented phase MUST be registered immediately in the `## Pending (next iteration)` section with its name, parent feature, and a brief description of what remains. Marking something as "futuro", "deferred", or "Phase N (deferred)" in inline comments is NOT sufficient; the item must also appear in the centralised Pending list. This prevents work from being lost between sessions.

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

### Pendentes resolvidos (implementados em task #165)

- ~~Token resolution em MCP Tools de domínio~~: ✅ `McpInterceptorConfig.resolveToken?` — callback opcional que resolve `[category:tk_xxx:display]` nos args antes de encaminhar ao domain server. Fail-open. Wired pelos agentes nativos ao instanciar o interceptor.
- ~~Channel Gateway: exibir só `display_partial` (sem wrapper `[...]`) para o cliente~~: ✅ `_TOKEN_RE` regex + `_strip_tokens()` em `stream_subscriber.py` — aplicado ao texto antes da entrega WebSocket.
- ~~Masking config UI~~: ✅ `packages/platform-ui/src/modules/masking/MaskingPage.tsx` — rota `/config/masking`; 4 seções: controle de acesso (authorized_roles), audit capture (capture_input/output_default), retenção (default_retention_days), visão das categorias. `MaskingService.loadAccessPolicy()` atualizado com fallback chain: legacy key → Config API tenant cache → Config API global cache → hardcoded default.

### Arquitetura do modelo de mascaramento

O stream canônico (`session:{id}:stream`) armazena dois campos por mensagem:
- `content` — versão mascarada entregue ao agente (tokens inline `[cpf:tk_xxx:***-00]`)
- `original_content` — versão original acessível apenas por `authorized_roles` via `session_context_get`

O mascaramento é aplicado na **escrita** (`message_send`) e o controle de acesso ao valor original é feito na **leitura** por role. Para entrega ao cliente via WebSocket, `_strip_tokens()` extrai apenas o `display_partial` dos tokens — o cliente nunca vê o wrapper `[...]`.

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
- ~~`usage.cycle_reset` — reset mensal de contadores~~ ✅ `cycle_reset.py` + consumer `usage.cycle_reset` topic + `POST /admin/cycle-reset` (HTTP admin FastAPI — porta 3950)

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
- `tests/test_attachment_store.py` — 59 testes pytest (validate_mime, magic_bytes, reserve, commit, resolve, soft_expire, stream_bytes — FilesystemAttachmentStore + S3AttachmentStore; asyncpg mockado, filesystem real via tmp_path)
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
- ~~**jwt_secret por tenant**~~: ✅ `_decode_token` agora async: (1) decode sem verificação para ler `tenant_id`; (2) lookup Redis `{tenant_id}:config:webchat:jwt_secret`; (3) fallback para `settings.jwt_secret`. Single-tenant sem mudança de config. Tests: `TestStreamExpiredReconnect` (2 cases) + `TestMultiTenantJwtSecret` (3 cases). Total channel-gateway: 198/198 (incl. magic bytes + S3 tests).

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

## Pool Lifecycle Hooks

Permite que pools humanos declarem agentes especialistas que são ativados automaticamente
em pontos específicos do ciclo de atendimento, substituindo lógica hardcoded no Agent Assist UI.

### Schema — `@plughub/schemas/agent-registry.ts`

```typescript
PoolHookEntry { pool: string }   // pool_id do especialista a recrutar

PoolHooks {
  on_human_start: PoolHookEntry[]   // agente humano entra na sessão
  on_human_end:   PoolHookEntry[]   // agente humano chama agent_done (Fase B)
  post_human:     PoolHookEntry[]   // após on_human_end concluir (Fase B)
}
```

Declarado em `PoolRegistrationSchema.hooks?: PoolHooksSchema`.
O campo `copilot_skill_id` é `@deprecated` — substituído por `hooks.on_human_start`.

### Mecanismo de dispatch

O orchestrator-bridge despacha hooks publicando um `ConversationInboundEvent` sintético
no tópico `conversations.inbound` com `conference_id` preenchido:

```
hooks.on_human_start: [{pool: copilot_sac}]
  → bridge publica conversations.inbound { pool_id: "copilot_sac", conference_id: uuid }
  → routing engine aloca instância do pool copilot_sac
  → routing engine publica conversations.routed com conference_id
  → bridge recebe routed → process_routed → activate_native_agent (conference path)
```

Reutiliza 100% da infra de conferência/@mention — sem nova lógica de roteamento.

### Kafka producer no bridge

`_kafka_producer: AIOKafkaProducer` — variável de módulo inicializada em `run()`.
Usada por `fire_pool_hooks()` e `_trigger_contact_close()`.

### Trigger points implementados

| Hook | Status | Trigger |
|---|---|---|
| `on_human_start` | ✅ Fase A | Após `activate_human_agent()` em `process_routed()` |
| `on_human_end` | ✅ Fase B | `process_contact_event` agent_closed, last human drops → `fire_pool_hooks("on_human_end")` |
| `post_human` | ✅ Fase C | `on_human_end` pending reaches 0 → `fire_pool_hooks("post_human")` → pending reaches 0 → `_trigger_contact_close()` |

### Configuração (tenant_demo.yaml)

```yaml
pools:
  - pool_id: retencao_humano
    hooks:
      on_human_start: []          # vazio: copilot ativado via @mention
      on_human_end:
        - pool: finalizacao_ia    # agente NPS + encerramento (Fase B)
      post_human: []

  - pool_id: finalizacao_ia
    description: "NPS + encerramento após agente humano"
    channel_types: [webchat, whatsapp]
    sla_target_ms: 120000
```

### Agente de finalização (`agente_finalizacao_v1`)

Skill: `packages/skill-flow-engine/skills/agente_finalizacao_v1.yaml`

Fluxo:
```
agradecimento (notify) → solicitar_nps (menu button, timeout 60s)
  → registrar_nps (notify + context_tag session.nps_score_raw)
  → encerrar (complete resolved)
  [timeout/failure] → encerrar (complete resolved)
```

Ativação: via `on_human_end` hook em `retencao_humano` — ✅ wired (Fase B completa).

### Fase B — separação agent_done / contact_close (✅ implementado)

O human `agent_done` (REST `/agent_done`) NÃO mais fecha o WebSocket do cliente.
O bridge assume a propriedade do close e o atrasa até os hook agents concluírem.

**Fluxo completo:**
```
Humano clica "Encerrar"
  → mcp-server POST /agent_done (publica contact_closed reason="agent_closed")
  → process_contact_event(agent_closed): último humano → clear human_agent flags
      → get_pool_config → on_human_end hooks?
          Sim → fire_pool_hooks("on_human_end")
                  → publica conversations.inbound com conference_id por hook
                  → seta session:{id}:hook_pending:on_human_end = N
                  → seta session:{id}:hook_conf:{conf_id} por hook
          Não → asyncio.create_task(_trigger_contact_close())
  → process_routed recebe o hook agent ativado
      → activate_native_agent (agente_finalizacao_v1 executa: NPS + encerramento)
      → ao retornar: getdel hook_conf → decr hook_pending
          → pending == 0 → asyncio.create_task(_trigger_contact_close())
  → _trigger_contact_close():
      → publica conversations.outbound session.closed → channel-gateway fecha WS do cliente
      → publica conversations.events contact_closed reason="agent_done" → limpeza completa
```

**Redis keys de controle:**
| Key | TTL | Descrição |
|---|---|---|
| `session:{id}:hook_pending:on_human_end` | 4h | Counter de hooks pendentes |
| `session:{id}:hook_conf:{conference_id}` | 4h | Marca hook-spawned agents |

**Mudança em mcp-server `/agent_done`:** removida a publicação de `conversations.outbound session.closed`. O bridge passou a ser o único dono do close do WebSocket do cliente após o `on_human_end`.

### Fase C — participation analytics + post_human hook dispatch (✅ implementado)

**Kafka topic:** `conversations.participants` — publicado pelo orchestrator-bridge.

**Producer (orchestrator-bridge/main.py):**
- `_publish_participant_event()` — fire-and-forget helper, publica em `TOPIC_PARTICIPANTS`
- `activate_human_agent()` → publica `participant_joined`, armazena `participant_joined_at:{instance_id}` (Redis, TTL 4h)
- `activate_native_agent()` → publica `participant_joined` (antes) + `participant_left` com duration_ms (após)
- `process_contact_event(agent_closed)` → lê `participant_joined_at:{instance_id}` via GETDEL, calcula `duration_ms`, publica `participant_left`

**Payload de evento:**
```json
{
  "type":           "participant_joined" | "participant_left",
  "event_id":       "uuid",
  "session_id":     "sess_...",
  "tenant_id":      "tenant_demo",
  "participant_id": "agente_retencao_v1-001",
  "pool_id":        "retencao_humano",
  "agent_type_id":  "agente_retencao_v1",
  "role":           "primary" | "specialist",
  "agent_type":     "ai" | "human",
  "conference_id":  "conf_..." | null,
  "joined_at":      "ISO8601",
  "duration_ms":    180000 | null,
  "timestamp":      "ISO8601"
}
```

**Consumer (analytics-api):**
- `parse_participant_event()` em `models.py` — mapeia `participant_joined`/`participant_left` → `participation_intervals`
- `"conversations.participants"` em `_TOPICS` e `_PARSERS`
- `_write_row()` despacha `participation_intervals` → `store.upsert_participation_interval()`

**ClickHouse — participation_intervals:**
```sql
ENGINE = ReplacingMergeTree(left_at)
ORDER BY (tenant_id, session_id, participant_id)
```
`participant_joined` escreve com `left_at=NULL`; `participant_left` escreve com `left_at` preenchido.
Background merge seleciona a versão com maior `left_at` (non-NULL wins).

**API:** `GET /reports/participation` — filtros: `session_id`, `pool_id`, `agent_type_id`, `role`. Suporta `format=csv`.

**Kafka topics adicionados ao docker-compose:** `conversations.participants` em `docker-compose.test.yml`, `docker-compose.full.yml`, `docker-compose.demo.yml`.

**post_human dispatch:** quando `on_human_end` pending chega a 0, o bridge verifica `post_human` hooks no `pool_config`:
- Se existirem → `fire_pool_hooks("post_human")` — mesmo mecanismo de `on_human_end`
- Quando `post_human` pending chega a 0 → `_trigger_contact_close()`
- Se não existirem → `_trigger_contact_close()` diretamente

**Redis keys adicionais:**
| Key | TTL | Descrição |
|---|---|---|
| `session:{id}:hook_pending:post_human` | 4h | Counter de post_human hooks |
| `participant_joined_at:{instance_id}` | 4h | Timestamp ISO8601 de entrada do participante |

**Tests:**
- `test_consumer.py`: `TestParseParticipantEvent` (8 assertions), `TestWriteRowDispatch::test_participation_intervals_dispatched`
- `test_reports.py`: `TestQueryParticipationReport` (4 assertions)
- Total analytics-api (Arc 3 Fase C): **172/172**

**Arc 5 tests (appended):**
- `test_consumer.py`: `TestParseParticipantEvent` reescrito — `test_participant_joined_returns_two_rows`, `test_participation_row_correct`, `test_segment_row_correct`, `test_segment_id_passed_through`, `test_sequence_index_passed_through`, `test_parent_segment_id_passed_through`, `test_event_id_generated_when_absent`; `TestWriteRowDispatch::test_segments_dispatched`, `test_session_timeline_dispatched`
- `test_reports.py`: `TestQuerySegmentsReport` (3 assertions — `test_returns_segment_rows`, `test_filters_do_not_crash`, `test_error_returns_empty_with_error_key`)
- Total analytics-api: **176/176**

## Frontend Architecture — platform-ui as standard shell

All operator-facing UI lives in `packages/platform-ui/`. Never create standalone frontend packages.

### Shell structure

```
packages/platform-ui/
  src/
    app/          ← App.tsx, routes.tsx (React Router v6)
    auth/         ← AuthContext, useAuth, ProtectedRoute, LoginPage
    components/ui/ ← Button, Card, Table, Badge, Modal, Input, Select, Spinner, PageHeader, EmptyState
    modules/      ← one subfolder per route module
    shell/        ← Shell.tsx (layout), Sidebar.tsx, TopBar.tsx
    i18n/         ← pt-BR (default), en locale files
```

### Design tokens (Tailwind)

| Token | Hex | Uso |
|---|---|---|
| `primary` | `#1B4F8A` | Sidebar, botões principais, links primários |
| `secondary` | `#2D9CDB` | Ações secundárias, badges informativos |
| `accent` | `#00B4D8` | Destaques, hover states |
| `green` | `#059669` | Sucesso, status ativo |
| `warning` | `#D97706` | Alertas, estados de atenção |
| `red` | `#DC2626` | Erros, estados críticos |

Font: Inter (via Google Fonts). Never write hex colors inline — always use Tailwind tokens.

### Adding a new module

1. Create `src/modules/{name}/{ModulePage}.tsx` — use only components from `@/components/ui/`
2. Register route in `src/app/routes.tsx` as a child of the Shell route
3. Add `NavItem` to `navItems[]` in `src/shell/Sidebar.tsx` with `roles` filter

```typescript
// routes.tsx — add to children array
{ path: 'config/billing', element: <BillingPage /> }

// Sidebar.tsx — add to navItems array
{ label: t('nav.billing'), href: '/config/billing', icon: '💳', roles: ['admin'] }
```

### Auth pattern

```typescript
import { useAuth } from '@/auth/useAuth'
const { session } = useAuth()  // session.role: 'operator' | 'supervisor' | 'admin' | 'developer' | 'business'
```

### Roles

| Role | Acesso |
|---|---|
| `operator` | Monitor, Agent Assist, Analytics |
| `supervisor` | operator + Avaliação, Relatórios |
| `admin` | supervisor + Configuração, Skill Flows |
| `developer` | admin + Developer Tools |
| `business` | Home, Analytics, Business |

### Migrated panels — config-recursos tabs

The `packages/platform-ui/src/modules/config-recursos/` tab container holds 6 tabs:

| Tab | File | Description |
|---|---|---|
| Pools | `PoolsPage.tsx` | Pool CRUD |
| Agent Types | `AgentTypesPage.tsx` | AgentType CRUD |
| Skills | `SkillsPage.tsx` | Skill list + detail |
| Instances | `InstancesPage.tsx` | Running instances (read-only) |
| Canais | `ChannelsPage.tsx` | GatewayConfig CRUD (8 channel types), migrated from operator-console ChannelPanel |
| Agentes Humanos | `HumanAgentsPage.tsx` | Human instance live status + agent type CRUD, migrated from operator-console HumanAgentPanel |

**New API functions in `src/api/registry.ts`:**
- `listChannels`, `createChannel`, `updateChannel`, `deleteChannel` → `/v1/channels`
- `listHumanInstances`, `instanceAction` → `/v1/instances?framework=human` / `PATCH /v1/instances/:id`
- `listHumanAgentTypes`, `createHumanAgentType`, `updateHumanAgentType`, `deleteAgentType` → `/v1/agent-types`
- `operatorHeaders()` — variant of `headers()` that includes `x-user-id: operator`

**Task #168 improvements (RegistryPanel migration):**
- `AgentTypesPage.tsx` — full rewrite: correct frameworks (plughub-native, human, external-mcp, langgraph, crewai, anthropic_sdk, azure_ai, google_vertex, generic_mcp), role select, max_concurrent_sessions, pool checkboxes, skills checkboxes, Deprecate→Confirm flow; pools rendered as chips in table
- `SkillsPage.tsx` — full rewrite: removed create form (skills are YAML-managed), info banner pointing to skill-flow-engine/skills/, detail modal shows tools/knowledge_domains chips
- `InstancesPage.tsx` — full rewrite: correct status filters (ready/busy/paused/draining), dynamic pool filter from API, channel_types column, 15s auto-refresh
- `PoolsPage.tsx` — added instagram/telegram/webrtc channel options
- Both i18n JSON files updated with new keys for executionModel, role, maxConcurrent, channels
- `types/index.ts` — fixed `AgentType.pools: Array<{pool_id: string}>`, `skills: Array<{skill_id; version_policy?}>`, added `updated_at?`
- `api/registry.ts` — `createAgentType` now maps `pools: string[]` → `{pool_id}[]` before POST

**New types in `src/types/index.ts`:**
`ChannelType`, `GatewayConfig`, `CreateGatewayConfigInput`, `UpdateGatewayConfigInput`,
`HumanAgentType`, `CreateHumanAgentInput`, `UpdateHumanAgentInput`, `AgentInstance`

Build: **486 kB JS / 143 kB gzip** (0 TypeScript errors).

### Migrated panels — billing module

`packages/platform-ui/src/modules/billing/BillingPage.tsx` — migrated from `packages/operator-console/src/components/PricingPanel.tsx`.

Route: `/config/billing` (role: `admin`). Nav entry: 💳 Faturamento under Configuração group.

**Components:**
- `ResourceSidebar` (220px left panel) — base + reserve resource list grouped by pool; admin token input
- `InvoiceTab` — base items table + reserve group blocks with activate/deactivate toggle; grand total; XLSX export link
- `ConsumptionTab` — usage dimensions from analytics-api with info banner (not included in billing)

**Inline hooks** (no separate hooks file needed):
- `useInvoice(tenantId)` → `GET /v1/pricing/invoice/{tenantId}`
- `useResources(tenantId)` → `GET /v1/pricing/resources/{tenantId}`
- `useUsage(tenantId)` → `GET /reports/usage?tenant_id={tenantId}`

**Vite proxy added** to `vite.config.ts`:
- `'^/v1/pricing'` → `http://localhost:3900` (before the generic `'^/v1'` → port 3300 entry)

**New types in `src/types/index.ts`:**
`InvoiceLineItem`, `ReserveGroup`, `Invoice`, `InstallationResource`

Build: **404 kB JS / 117 kB gzip** (0 TypeScript errors).

### Migrated panels — skill-flows module

`packages/platform-ui/src/modules/skill-flows/SkillFlowsPage.tsx` — migrated from `packages/operator-console/src/components/SkillFlowEditor.tsx`.

Route: `/skill-flows` (roles: `admin`, `developer`). Replaces the former `PlaceholderPage`.

**Features (fully ported):**
- Monaco YAML editor (`vs-dark` theme, `@monaco-editor/react`) with live YAML validation
- Left sidebar: skill list with search, type color-coding (orchestrator=violet, vertical=cyan, horizontal=yellow), modification indicator `●`
- New skill flow: prompts for skill_id, injects blank template with the entered id
- Save: YAML→JSON parse → `PUT /v1/skills/:id` — 422 validation errors shown in status bar
- Delete: three-stage confirmation (Delete → Confirmar → execute)
- Discard: reverts to last saved state
- ⌘S keyboard shortcut
- Auto-refresh skill list every 30s

**New dependencies added to `package.json`:** `@monaco-editor/react@^4.7.0`, `js-yaml@^4.1.1`, `@types/js-yaml@^4.0.9`

Build: **469 kB JS / 139 kB gzip** (0 TypeScript errors — Monaco adds ~65 kB gzipped).

### Migrated panels — campaigns module

`packages/platform-ui/src/modules/campaigns/CampaignsPage.tsx` — migrated from `packages/operator-console/src/components/CampaignPanel.tsx`.

Route: `/campaigns` (roles: `operator`, `supervisor`, `admin`, `business`). Accessible via Analytics → Campanhas nav entry.

**Features (fully ported):**
- Left 320px sidebar: global KPI bar (Campanhas / Total / Taxa), channel + status filter dropdowns, campaign card list with `MiniBar` (4-color status bar) and `RateBadge` (green/yellow/red)
- Right detail panel: campaign header with rate badge, 4-up KPI grid (Total / Respondidos / Expirados / Tempo médio), status distribution bar with legend, channel breakdown with progress bars, recent collect events table (token · canal · status · enviado · tempo)
- `useCampaignData` inline hook — polls `GET /reports/campaigns` every 30s, supports channel/status filters
- New types added to `src/types/index.ts`: `CampaignSummary`, `CollectEvent`
- i18n: `nav.campanhas` added to pt-BR and en locales

Build: **510 kB JS / 149 kB gzip** (0 TypeScript errors — Monaco included in bundle).

### Migrated panels — config-plataforma module (task #171)

`packages/platform-ui/src/modules/config-plataforma/components/NamespaceEditor.tsx` — upgraded to match full `ConfigPanel` feature set from operator-console.

Route: `/config/platform` (role: `admin`), tab ⚙️ Configuração. No new route needed — the ConfigPlataformaPage already existed.

**New features added to NamespaceEditor:**
- **Scope selector** in edit mode: 🌐 Global default vs 🏢 Tenant override — `putConfig(ns, key, value, null | tenantId, adminToken)`
- **"tenant override" badge** on entries where `entry.tenant_id ≠ '__global__'`
- **Reset button** (delete override) — restores global default; only shown when `adminToken` is set
- **Description display** per key (from `ConfigEntry.description`)
- **Tailwind redesign** — replaces inline CSS with design system tokens (`text-primary`, `bg-gray-50`, etc.)

**`config-hooks.ts` updated:**
- `ConfigEntry` extended with `tenant_id: string | null`, `namespace?: string`, `updated_at?: string`
- `useNamespace` return type changed from `Record<string, unknown>` → `Record<string, ConfigEntry>`
- Normalisation shim handles APIs that return plain values instead of `ConfigEntry` objects
- `AllConfig.config` type updated to `Record<string, Record<string, ConfigEntry>>`

**`MaskingPage.tsx` updated:** adapted to use `entries[key]?.value` instead of direct entry (due to type change).

Build: **513 kB JS / 150 kB gzip** (0 TypeScript errors).

### Legacy standalone apps — ✅ migração completa, pacotes removidos

- `packages/operator-console/` — ✅ **Removido** (diretório deletado; docker-compose atualizado). Todos os 12 painéis migrados para `platform-ui`:
  - ✅ ChannelPanel → `config-recursos/ChannelsPage.tsx`
  - ✅ HumanAgentPanel → `config-recursos/HumanAgentsPage.tsx`
  - ✅ PricingPanel → `modules/billing/BillingPage.tsx`
  - ✅ SkillFlowEditor → `modules/agent-flow/AgentFlowEditorPage.tsx` (YAML editor, now under `/agent-flow/editor`)
  - ✅ RegistryPanel (Pools/AgentTypes/Skills/Instances) → `config-recursos/` tabs (task #168)
  - ✅ WorkflowPanel + WebhookPanel → `modules/workflows/WorkflowEditorPage.tsx` + `WorkflowMonitorPage.tsx` + `WorkflowReportPage.tsx` + `WorkflowCalendarPage.tsx` under `/workflow/*`
  - ✅ CampaignPanel → `modules/campaigns/CampaignsPage.tsx` (task #170)
  - ✅ ConfigPanel → `modules/config-plataforma/components/NamespaceEditor.tsx` (task #171 — merged into existing ConfigPlataformaPage at `/config/platform`)
- `packages/agent-assist-ui/` (port 5175) — chat + right panel → ✅ migrated to `modules/agent-assist/AgentAssistPage.tsx` (task #172)

### Nav structure — groups, roles and ABAC gates

The Sidebar has expandable collapsible groups (children[]). The `business` role is **cross-cutting**: it is included in every group's `roles[]` but operational items are hidden via ABAC `operacao` field (business users have `operacao: none`).

| Group | Roles | Children / ABAC gate |
|-------|-------|----------------------|
| **Atendimento** (📞) | operator, supervisor, admin, **business** | Contatos (no gate); Monitor 📡, AgentAssist 🤖 → `contacts.operacao` |
| **Workflow** (⚙️) | operator, supervisor, admin, **business** | Editor ▶️, Monitor 📡, Calendar 📅 → `workflows.operacao`; Report 📊 → `workflows.visualizar` |
| **AgentFlow** (🔄) | admin, developer, **business** | Editor ✏️, Monitor 📡, Deploy 🚀 → `skill_flows.operacao`; Report 📊 → `skill_flows.visualizar` |
| **Avaliação** (✓) | operator, supervisor, admin, **business** | Forms, Campaigns → `evaluation.formularios`; Reports → `evaluation.relatorio`; others role-gated |
| **Configuração** (⚙️) | admin, **business** | Templates de Dashboard 📊 → `config.plataforma`; Recursos/Plataforma/Mascaramento/Acesso → ABAC config fields; Faturamento (no ABAC gate — always visible to admin + business) |
| **Developer** (👨‍💻) | developer, admin | (no children) |

**Analytics group was dissolved** — its content lives in: Contacts (MonitorTab + AnaliseTab), workflow/report, agent-flow/report, evaluation/reports. No separate Analytics nav group exists.

**Two-tier ABAC pattern for analytics access:**
- `operacao` gates **active operational items** (Monitor, Editor, Calendar, Deploy, AgentAssist) — users with `operacao: none` (including `business`) don't see these items
- `visualizar` gates **read-only analytical items** (Report, Análise tab) — allows business/analyst roles to access dashboards and reports without operational access

**Contacts tab-level ABAC** — `ContactsPage` filters its own tabs at render time:
- `Lista` tab — always visible (role-gated at group level)
- `Monitor` tab — requires `contacts.operacao`
- `Análise` tab — requires `contacts.visualizar`
Active tab falls back to first visible tab if the URL-requested tab is not accessible.

**Templates de Dashboard** — the DashboardsPage (`/dashboards`) is for template management only (admin). It moved from a top-level nav item to inside Configuração, gated by `config.plataforma`. Dashboard content (analytics cards) is embedded inside each module's respective page and is accessed through the module's own `visualizar` permission, not a separate dashboard route.

Legacy redirects in `routes.tsx`:
- `/workflows` → `/workflow/monitor`
- `/campaigns` → `/workflow/report`
- `/config/calendars` → `/workflow/calendar`
- `/skill-flows` → `/agent-flow/editor`
- `/reports` → `/contacts?tab=analise`
- `/business` → `/` (business accesses modules via ABAC)

### Skill Deploy Lifecycle (Phase 1) — ✅ implemented

Skills follow a two-stage lifecycle: **draft** (saved YAML not yet in production) → **published** (deployed to pools).

**PostgreSQL additions** (migration `20260430000000_add_skill_deployments`):
- `skills.deploy_status: TEXT DEFAULT 'draft'` — `"draft"` or `"published"`
- `skills.published_at: TIMESTAMPTZ?` — timestamp of first/last deploy
- `skill_deployments` table — deployment history with `pool_ids[]`, `yaml_snapshot`, `deployed_by`, `deployed_at`, `notes`

**New agent-registry endpoints:**

| Method | Route | Description |
|--------|-------|-------------|
| `POST` | `/v1/skills/:skill_id/deploy` | Deploys skill to `pool_ids[]`; sets `deploy_status=published`, records `SkillDeployment`, triggers `publishRegistryChanged` |
| `GET` | `/v1/skills/:skill_id/deployments` | Returns deployment history (newest first, `limit` param, max 200) |

**Invariants:**
- `PUT /v1/skills/:id` (save) always sets `deploy_status = "draft"` on new skills; NEVER modifies `deploy_status` on updates — only the deploy action changes it
- Every deploy snapshot the `flow` JSON at deploy time into `yaml_snapshot` for rollback reference
- Deploy calls `publishRegistryChanged(tenantId, "skill", skillId, "updated")` to trigger hot-reload in orchestrator-bridge

**Rollback** = trigger a new deploy pointing to the previous `yaml_snapshot` version (Phase 2: automated rollback button in Deploy UI).

**Phase 2 (deferred):** calendar-scheduled deploys, graceful handoff monitor, automated rollback button.

### What never to do

- Never create a new `packages/my-ui/` standalone app — add a module to platform-ui
- Never use inline hex colors — use Tailwind tokens (`text-primary`, `bg-secondary`)
- Never write custom CSS when a Tailwind class exists
- Never create a NavItem without `roles` filter
- Never modify `deploy_status` in PUT /v1/skills — only the deploy action owns that field

## Arc 7 — Autenticação Real, Permissões e Roteamento por Performance

### Arc 7a — auth-api (✅ implementado)

Usuários reais, JWT HS256, session lifecycle com refresh token rotation.
Substitui o modelo de `x-tenant-id`/`x-user-id` como headers livres.

**Pacote:** `packages/auth-api/` — Python FastAPI, porta 3200.

#### PostgreSQL schema (schema `auth`)

```sql
CREATE TABLE auth.users (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        TEXT NOT NULL,
    email            TEXT NOT NULL,
    name             TEXT NOT NULL DEFAULT '',
    password_hash    TEXT NOT NULL,   -- bcrypt rounds=12
    roles            TEXT[] NOT NULL DEFAULT '{}',
    accessible_pools TEXT[] NOT NULL DEFAULT '{}',  -- [] = todos os pools
    active           BOOL NOT NULL DEFAULT TRUE,
    created_at, updated_at TIMESTAMPTZ,
    UNIQUE (tenant_id, email)
);

CREATE TABLE auth.sessions (
    id                 UUID PRIMARY KEY,
    user_id            UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    tenant_id          TEXT NOT NULL,
    refresh_token_hash TEXT NOT NULL UNIQUE,  -- SHA-256(plain_token)
    expires_at         TIMESTAMPTZ NOT NULL,
    last_used_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

#### JWT claims (access token — HS256, TTL 1h)

```json
{
  "sub":              "user-uuid",
  "tenant_id":        "tenant_demo",
  "email":            "user@example.com",
  "name":             "User Name",
  "roles":            ["operator", "supervisor"],
  "accessible_pools": ["retencao_humano", "sac"],
  "exp": ..., "iat": ...
}
```

`accessible_pools: []` significa acesso a todos os pools (usuário admin/developer).

#### Refresh token

Token opaco de 43 chars URL-safe (~258 bits de entropia). Armazenado como SHA-256 em `auth.sessions` — plain token nunca persisted. Rotation automática em cada `POST /auth/refresh` (novo par emitido, hash antigo substituído atomicamente).

#### Endpoints

| Método | Rota | Auth | Descrição |
|---|---|---|---|
| `POST` | `/auth/login` | — | Login email+senha → access_token + refresh_token |
| `POST` | `/auth/refresh` | body refresh_token | Rotation → novo par |
| `POST` | `/auth/logout` | body refresh_token | Invalida refresh_token (idempotente) |
| `GET` | `/auth/me` | Bearer | Claims do access token |
| `GET` | `/auth/users` | X-Admin-Token | Lista usuários do tenant |
| `POST` | `/auth/users` | X-Admin-Token | Cria usuário |
| `GET` | `/auth/users/{id}` | X-Admin-Token | Detalhe do usuário |
| `PATCH` | `/auth/users/{id}` | X-Admin-Token | Atualiza usuário (name, password, roles, accessible_pools, active) |
| `DELETE` | `/auth/users/{id}` | X-Admin-Token | Remove usuário |
| `GET` | `/health` | — | Healthcheck |

#### Seed automático

Ao iniciar, `seed_admin_if_absent()` cria o usuário admin configurado via env vars se não existir. Idempotente — sem erro em re-inicializações.

#### Variáveis de ambiente (prefixo `PLUGHUB_AUTH_`)

| Var | Default | Descrição |
|---|---|---|
| `DATABASE_URL` | `postgresql://plughub:plughub@postgres:5432/plughub` | DSN PostgreSQL |
| `JWT_SECRET` | `changeme_auth_jwt_secret_at_least_32_chars` | Segredo HS256 |
| `JWT_ALGORITHM` | `HS256` | Algoritmo JWT |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | TTL do access token |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | TTL do refresh token |
| `ADMIN_TOKEN` | `""` | Token admin (vazio = sem auth em dev) |
| `SEED_ADMIN_EMAIL` | `admin@plughub.local` | Email do admin seed |
| `SEED_ADMIN_PASSWORD` | `changeme_admin` | Senha do admin seed |
| `SEED_TENANT_ID` | `tenant_demo` | Tenant do admin seed |

#### Arquivos

| Arquivo | Responsabilidade |
|---|---|
| `config.py` | Settings com prefixo `PLUGHUB_AUTH_` |
| `password.py` | `hash_password()`, `verify_password()` — bcrypt rounds=12 |
| `jwt_utils.py` | `create_access_token()`, `decode_access_token()`, `generate_refresh_token()`, `hash_refresh_token()` |
| `models.py` | Pydantic: LoginRequest, RefreshRequest, LogoutRequest, CreateUserRequest, UpdateUserRequest, TokenResponse, UserResponse, MeResponse |
| `db.py` | DDL + CRUD asyncpg: `ensure_schema`, `create_user`, `get_user_by_email`, `get_user_by_id`, `list_users`, `update_user`, `delete_user`, `create_session`, `get_session_by_token_hash`, `rotate_session`, `delete_session`, `seed_admin_if_absent` |
| `router.py` | FastAPI routes — login/refresh/logout/me + CRUD admin |
| `main.py` | FastAPI app + lifespan asyncpg pool + seed |
| `tests/test_router.py` | **58/58 testes** — TestHealth, TestLogin (4), TestRefresh (3), TestLogout (2), TestMe (3), TestCreateUser (3), TestListUsers (1), TestGetUser (2), TestUpdateUser (2), TestDeleteUser (2), TestSeedAdmin (2), TestPasswordUtils (3), TestJwtUtils (3), TestHashRefreshToken (3), TestGrantPermission (3), TestListPermissions (2), TestRevokePermission (2), TestResolvePermission (3), TestTemplates (6), TestApplyTemplate (2), TestResolvePermissionsLogic (6) |

### Arc 7b — platform_permissions (✅ implementado)

Generaliza `evaluation_permissions` para todo o sistema. Implementado em `packages/auth-api/`.

#### PostgreSQL schema (schema `auth`)

```sql
-- Permissão explícita: uma linha por (user_id, module, action, scope_type, scope_id)
CREATE TABLE auth.platform_permissions (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   TEXT        NOT NULL,
    user_id     TEXT        NOT NULL,
    module      TEXT        NOT NULL,   -- analytics | evaluation | billing | config | registry | skill_flows | campaigns | workflows | *
    action      TEXT        NOT NULL,   -- view | edit | admin | *
    scope_type  TEXT        NOT NULL CHECK (scope_type IN ('pool', 'global')),
    scope_id    TEXT,                   -- pool_id for scope_type='pool'; NULL for global
    granted_by  TEXT        NOT NULL DEFAULT 'system',
    template_id UUID,                   -- FK para permission_templates (auditoria)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, user_id, module, action, scope_type, COALESCE(scope_id, ''))
);

-- Template nomeado de permissões (conjunto reutilizável)
CREATE TABLE auth.permission_templates (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   TEXT        NOT NULL,
    name        TEXT        NOT NULL,
    description TEXT        NOT NULL DEFAULT '',
    permissions JSONB       NOT NULL DEFAULT '[]',   -- list[{module, action, scope_type, scope_id}]
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
);
```

#### Domínios válidos

| Campo | Valores |
|---|---|
| `module` | `analytics`, `evaluation`, `billing`, `config`, `registry`, `skill_flows`, `campaigns`, `workflows`, `*` |
| `action` | `view`, `edit`, `admin`, `*` |
| `scope_type` | `pool` (scope_id = pool_id), `global` (scope_id = NULL) |

Curingas: `module='*'` ou `action='*'` batem em qualquer valor pedido.

#### Endpoints (X-Admin-Token)

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/auth/permissions` | Concede permissão (upsert idempotente) |
| `GET` | `/auth/permissions?tenant_id=&user_id=&module=` | Lista permissões com filtros |
| `DELETE` | `/auth/permissions/{id}` | Revoga permissão |
| `GET` | `/auth/permissions/resolve?tenant_id=&user_id=&module=&action=&pool_id=` | Resolve se usuário tem permissão (sem admin token) |
| `POST` | `/auth/templates` | Cria template |
| `GET` | `/auth/templates?tenant_id=` | Lista templates |
| `GET` | `/auth/templates/{id}` | Detalhe do template |
| `PATCH` | `/auth/templates/{id}` | Atualiza template |
| `DELETE` | `/auth/templates/{id}` | Remove template |
| `POST` | `/auth/templates/{id}/apply` | Materializa permissões do template para um usuário |

#### Funções principais (`permissions.py`)

```python
grant_permission(...)       → dict   # ON CONFLICT DO UPDATE (idempotente)
revoke_permission(...)      → bool
list_permissions(...)       → list[dict]   # filtros: tenant_id, user_id, module
resolve_permissions(...)    → bool   # global scope primeiro, depois pool scope
get_accessible_pools_for_module(...)  → list[str] | None
# None = acesso global (todos os pools); [] = sem acesso; [...] = pools específicos

apply_template(pool, template_id, tenant_id, user_id, granted_by, scope_override=None)
# Materializa template → platform_permissions (sem lookup em cadeia no runtime)
# scope_override: {"scope_type": "pool", "scope_id": "pool_sac"} para restringir ao bind
```

#### Resolução de permissão

```
resolve_permissions(tenant_id, user_id, module, action, pool_id=None):
  1. Busca linhas WHERE (module=$m OR module='*') AND (action=$a OR action='*')
  2. scope_type='global'                     → True
  3. scope_type='pool' AND scope_id=$pool_id → True (se pool_id fornecido)
  4. Nenhuma match                           → False
```

#### Arquivos

| Arquivo | Responsabilidade |
|---|---|
| `permissions.py` | DDL + CRUD: `ensure_permissions_schema`, grant/revoke/list/resolve, templates CRUD + apply |
| `router.py` | Endpoints de permissão e template adicionados ao router existente |
| `models.py` | `GrantPermissionRequest`, `PermissionResponse`, `PermissionEntry`, `CreateTemplateRequest`, `UpdateTemplateRequest`, `TemplateResponse`, `ApplyTemplateRequest`, `ResolvePermissionResponse` |

### Arc 7c — visibilidade por pool em analytics (✅ implementado)

JWT carrega `accessible_pools[]`. analytics-api injeta `WHERE pool_id IN (...)` nas queries ClickHouse. Row-level security sem subselects — whitelist de pool_ids vem diretamente do JWT.

#### Arquivos novos / modificados

| Arquivo | Alteração |
|---|---|
| `analytics-api/config.py` | Campo `auth_jwt_secret: str = ""` — segredo HS256 do auth-api (deve coincidir com `PLUGHUB_AUTH_JWT_SECRET`) |
| `analytics-api/pool_auth.py` | **NOVO** — `PoolPrincipal` + `optional_pool_principal` FastAPI dependency |
| `analytics-api/reports_query.py` | `_apply_pool_scope()` helper; parâmetro `accessible_pools: list[str] | None` em 6 funções: sessions, agents, quality, participation, segments, agent_performance |
| `analytics-api/reports.py` | `Depends(optional_pool_principal)` em 6 endpoints; `accessible_pools` propagado para query helpers |

#### PoolPrincipal — semântica de acessível

```python
accessible_pools = None    # acesso irrestrito — todos os pools
accessible_pools = [...]   # restrito a esses pool_ids (JWT com lista de pools)
```

#### optional_pool_principal — comportamento por cenário

| Cenário | Resultado |
|---|---|
| `analytics_open_access=True` OU `auth_jwt_secret=""` | `accessible_pools=None` (sem restrição) |
| Sem header Authorization | `accessible_pools=None` (backward-compatible) |
| JWT válido, `accessible_pools=[]` | `accessible_pools=None` (convenção auth-api: `[]` = todos os pools) |
| JWT válido, `accessible_pools=["sac","retencao"]` | `accessible_pools=["sac","retencao"]` |
| JWT inválido/expirado | HTTP 401 |

#### `_apply_pool_scope` — aplicado nas queries

Quando `accessible_pools` é uma lista não-vazia, injeta:
```sql
AND pool_id IN ('pool_sac', 'pool_retencao')
```
Quando `accessible_pools=[]` (lista vazia), o caller retorna `{"data": [], "meta": {total: 0}}` sem chamar o ClickHouse (short-circuit).

#### Env var

```
PLUGHUB_ANALYTICS_AUTH_JWT_SECRET=<mesmo valor que PLUGHUB_AUTH_JWT_SECRET>
```

Quando vazia (default), pool scoping é desabilitado (todos os pools visíveis).

#### Endpoints com pool scoping

`GET /reports/sessions`, `/reports/agents`, `/reports/quality`, `/reports/participation`, `/reports/segments`, `/reports/agents/performance`

Os endpoints que não têm dimensão `pool_id` (`/reports/usage`, `/reports/workflows`, `/reports/campaigns`, `/reports/evaluations`) não foram modificados.

#### Testes

`analytics-api/tests/test_reports.py` — 63/63 passando. Classes novas Arc 7c:
- `TestApplyPoolScope` (4) — helper puro: None noop, lista vazia retorna False, IN clause gerado corretamente
- `TestPoolScopedSessionsReport` (3) — None passa, vazia short-circuits, lista injeta IN clause no SQL
- `TestPoolScopedAgentsReport` (2) — idem para agent_events
- `TestPoolPrincipalAuth` (9) — open_access, sem secret, sem token, JWT []→None, JWT lista→restrito, JWT inválido→401

### Arc 7d — roteamento por performance (✅ implementado)

Batch job lê `mv_agent_performance_daily` (ClickHouse) e escreve scores normalizados em Redis.
`score_resource()` no routing-engine blenda competência com performance histórica com peso configurável.

#### Score formula

```
performance_score = resolution_rate × (1 − min(escalation_rate, 1.0))
```

Resultado em [0.0, 1.0]. Recompensa alta taxa de resolução, penaliza escalação.

#### Blending no score_resource()

```
final = (1 − w) × competency_score + w × performance_score

w = performance_score_weight (0.0–1.0)
  0.0 = puro competency (padrão — backward-compatible, sem Redis reads)
  0.3 = 70% competência + 30% performance histórica (recomendado em produção)
```

Hard filter (-1.0) é preservado independente do performance_score.
Quando sem dados (novo agent, primeiros 7 dias), `performance_score = 0.5` (neutro — sem viés).

#### Redis key pattern

```
{tenant_id}:agent_perf:{agent_type_id}
  Value: str(float) in [0.0, 1.0]
  TTL:   21600s (6h) — renovado a cada sync (5 min)
```

#### Configuração

```
PLUGHUB_PERFORMANCE_SCORE_WEIGHT=0.3    # env var no routing-engine
```

Ou via Config API namespace `routing` key `performance_score_weight` (editável por tenant no Operator Console).

#### Componentes implementados

| Arquivo | Responsabilidade |
|---|---|
| `analytics-api/performance_job.py` | `compute_performance_score()`, `run_performance_sync()`, `run_performance_job_loop()` — batch job query + Redis write |
| `analytics-api/main.py` | Inicializa `perf_task` background em lifespan; `POST /admin/performance-sync` para trigger manual |
| `routing-engine/registry.py` | `_agent_perf_key()` helper; `InstanceRegistry.get_agent_performance_score()` — lê Redis com fallback 0.5 |
| `routing-engine/scorer.py` | `score_resource()` estendida com `performance_score` + `performance_score_weight` params |
| `routing-engine/router.py` | `_allocate()` lê `perf_weight` de `routing_config.get()` com fallback para env var |
| `routing-engine/config.py` | `performance_score_weight: float = 0.0` (env `PLUGHUB_PERFORMANCE_SCORE_WEIGHT` — fallback quando Config API indisponível) |
| `routing-engine/routing_config.py` | **NOVO** — `RoutingConfigCache`: busca namespace `routing` do Config API no startup; invalidado por `ConfigChangedHandler` via `config.changed` Kafka; defaults built-in garantem operação offline |
| `routing-engine/kafka_listener.py` | `ConfigChangedHandler` — invalida e recarrega `RoutingConfigCache` em background quando `namespace=routing` |
| `config-api/seed.py` | Seed entry `routing.performance_score_weight = 0.0` com descrição |

#### Parâmetros do batch job

| Constante | Valor | Descrição |
|---|---|---|
| `PERF_KEY_TTL` | 21600s (6h) | TTL das chaves Redis de performance |
| `LOOKBACK_DAYS` | 7 | Janela de lookback no ClickHouse |
| `MIN_SESSIONS` | 5 | Mínimo de sessões para significância estatística |
| Intervalo do loop | 300s (5 min) | Frequência de sync performance → Redis |

#### Tests

- `analytics-api/tests/test_performance_job.py` — 12 assertions: `TestComputePerformanceScore` (6 — fórmula, edge cases), `TestRunPerformanceSync` (6 — Redis write, key format, TTL, CH error, Redis error)
- `routing-engine/tests/test_scorer.py`: `TestResourceScorerPerformanceBlending` (6 assertions — zero weight backward-compat, high perf boost, low perf penalty, hard filter preserved, neutral default no-bias, no-requirements pool blending)

### Platform-UI — integração real com auth-api (✅ implementado)

A platform-ui foi integrada ao auth-api real (porta 3200), substituindo o formulário mock por autenticação JWT completa.

#### Token storage strategy

| Token | Localização | Motivo |
|---|---|---|
| `access_token` | Memória (React state) | Não persiste entre reloads — re-obtido via refresh silencioso |
| `refresh_token` | `localStorage('plughub_refresh_token')` | Sobrevive reload — base para silent re-auth |
| Metadados (name, role, tenant) | `localStorage('plughub_session_meta')` | Persiste sem expor token |

#### Arquivos modificados/criados

| Arquivo | Descrição |
|---|---|
| `src/api/auth.ts` (NOVO) | `apiLogin`, `apiRefresh`, `apiLogout` — client HTTP para auth-api; `AuthApiError` com status HTTP |
| `src/auth/AuthContext.tsx` | Reescrito: JWT flow real, auto-refresh (60s antes da expiração), silent re-auth no mount |
| `src/auth/LoginPage.tsx` | Reescrito: email + password reais, tratamento de erros por status HTTP (401/403/5xx) |
| `src/auth/ProtectedRoute.tsx` | Atualizado: spinner durante `isInitializing`; preserva URL de destino em `location.state` |
| `src/auth/useAuth.ts` | Inalterado (expõe novo `isInitializing` e `getAccessToken` via context) |
| `src/types/index.ts` | `Session` extendido com `email`, `roles[]`, `accessiblePools[]`, `accessToken`, `refreshToken`, `expiresAt` |
| `src/shell/TopBar.tsx` | `handleLogout` tornou-se async; exibe `session.email` em vez de `session.userId` |
| `vite.config.ts` | Proxy `'^/auth'` → `http://localhost:3200` adicionado |

#### Fluxo de autenticação

```
Login:
  LoginPage → apiLogin(email, password) → TokenResponse
  → buildSession() → setState + localStorage + scheduleRefresh()

Auto-refresh:
  setTimeout (60s antes de expiresAt) → apiRefresh(refreshToken)
  → novo TokenResponse → re-agendamento

Silent re-auth no mount:
  localStorage tem refresh_token → apiRefresh()
    → sucesso: session restaurada, isInitializing=false
    → falha: clearStorage(), isInitializing=false (→ login page)

Logout:
  clearTimeout, setSession(null), clearStorage()
  → apiLogout(refreshToken, accessToken) — best-effort
```

#### `getAccessToken()` — para API clients

Método disponível em `useAuth()`:
```typescript
const token = await getAccessToken()   // null se não autenticado
// Verifica expiração, faz refresh se necessário, deduplica chamadas concorrentes
```

#### `isInitializing` — evita flash do login

`ProtectedRoute` mostra spinner enquanto `isInitializing=true`, evitando que usuários com refresh_token válido vejam o formulário de login por 100–500ms antes do redirect.

---

## Sistema ABAC — Permissões Declarativas por Módulo

O sistema ABAC (Attribute-Based Access Control) complementa o RBAC existente (roles: operator/supervisor/admin/developer/business) com permissões granulares declaradas por módulo. Cada módulo registra seus próprios campos de permissão em `infra/modules.yaml`. As permissões são armazenadas em `auth.users.module_config` (JSONB), carregadas no JWT e avaliadas localmente no frontend — sem round-trip ao servidor.

### auth-api — extensões

#### Nova tabela: `auth.module_registry`

```sql
CREATE TABLE auth.module_registry (
    module_id          TEXT PRIMARY KEY,
    label              TEXT NOT NULL,
    icon               TEXT NOT NULL DEFAULT '',
    nav_path           TEXT NOT NULL DEFAULT '',
    active             BOOL NOT NULL DEFAULT TRUE,
    permission_schema  JSONB NOT NULL DEFAULT '{}',
    created_at         TIMESTAMPTZ DEFAULT now(),
    updated_at         TIMESTAMPTZ DEFAULT now()
);
```

#### Nova coluna em `auth.users`

```sql
ALTER TABLE auth.users
  ADD COLUMN IF NOT EXISTS module_config JSONB NOT NULL DEFAULT '{}';
```

#### Novos endpoints

| Método | Rota | Auth | Descrição |
|---|---|---|---|
| `GET` | `/auth/modules?active_only=true` | — | Lista módulos com permission_schema |
| `PUT` | `/auth/users/{id}/module-config` | X-Admin-Token | Salva module_config do usuário |

#### Seed automático

Função `seed_modules_from_yaml()` lê `infra/modules.yaml` e faz upsert em `auth.module_registry` ao iniciar o auth-api. Idempotente — sem erro em re-inicializações.

### `infra/modules.yaml` — registro central de módulos

Arquivo YAML com 8 módulos registrados. Cada módulo define:
- `module_id`, `label`, `icon`, `nav_path`, `active`
- `permission_schema`: mapa de campos, onde cada campo tem `label`, `domain` (lista de `PermissionAccess` permitidos), `scopable` (bool), `scope_type` (pool|campaign|global), `default`

**Módulos registrados:**

| Módulo | Campos de permissão | Descrição |
|---|---|---|
| `evaluation` | `contestar`, `revisar`, `relatorio`, `formularios` | Avaliação de qualidade |
| `contacts` | **`operacao`**, `visualizar`, `exportar` | Contatos — `operacao` gatea Monitor + AgentAssist |
| `billing` | `visualizar`, `gerenciar` | Faturamento e preços |
| `config` | `plataforma`, `recursos`, `canais`, `usuarios`, `mascaramento` | Configuração de plataforma |
| `skill_flows` | **`operacao`**, `visualizar`, `editar` | AgentFlow — `operacao` gatea Editor, Monitor, Deploy |
| `workflows` | **`operacao`**, `visualizar`, `cancelar`, `webhooks` | Automação — `operacao` gatea Editor, Monitor, Calendar |
| `agent_assist` | `atender`, `supervisionar` | Atendimento humano |
| `campaigns` | `visualizar`, `gerenciar` | Campanhas de coleta |

**Nota:** o módulo `analytics` foi removido do `infra/modules.yaml`. Seus dados estão disponíveis nas abas MonitorTab e AnaliseTab do ContactsPage, não como módulo ABAC separado.

### JWT — `module_config` incluído

O access token agora inclui o campo `module_config` extraído de `auth.users`:

```json
{
  "sub": "user-uuid",
  "tenant_id": "tenant_demo",
  "email": "user@example.com",
  "roles": ["supervisor"],
  "accessible_pools": [],
  "module_config": {
    "evaluation": {
      "contestar": { "access": "none", "scope": [] },
      "revisar":   { "access": "read_write", "scope": ["pool:retencao_humano"] }
    },
    "analytics": {
      "view": { "access": "read_only", "scope": [] }
    }
  }
}
```

### platform-ui — tipos e helper

#### Tipos em `src/types/index.ts`

```typescript
export type PermissionAccess = 'none' | 'read_only' | 'write_only' | 'read_write'

export interface ModuleFieldConfig {
  access: PermissionAccess
  scope: string[]           // [] = acesso global; ['pool:sac', 'pool:retencao'] = restrito
}

export type ModuleConfig = Record<string, Record<string, ModuleFieldConfig>>

// Integrado em Session
export interface Session {
  // ...campos existentes...
  moduleConfig: ModuleConfig
}
```

#### Helper puro: `src/lib/permissions.ts`

```typescript
export class PermissionChecker {
  constructor(private moduleConfig: ModuleConfig | undefined) {}

  // Verifica se usuário tem acesso a um campo (qualquer nível acima de 'none')
  can(module: string, field: string): boolean
  can(module: string, field: string, minAccess: PermissionAccess): boolean
  can(module: string, field: string, minAccess: PermissionAccess, scopeId: string): boolean

  // Retorna o nível de acesso (sem verificar scope)
  access(module: string, field: string): PermissionAccess

  // Retorna a lista de escopos para um campo
  scopeOf(module: string, field: string): string[]
}

export function makePermissions(moduleConfig: ModuleConfig | undefined): PermissionChecker
```

**Exemplos de uso:**

```typescript
const perms = makePermissions(session?.moduleConfig)

perms.can('evaluation', 'revisar')                               // tem qualquer acesso?
perms.can('evaluation', 'revisar', 'read_write')                 // tem read_write?
perms.can('evaluation', 'revisar', 'read_write', 'pool:sac')     // tem acesso ao pool sac?
perms.access('evaluation', 'contestar')                          // 'none'|'read_only'|'write_only'|'read_write'
perms.scopeOf('evaluation', 'revisar')                           // [] = global; ['pool:x', 'pool:y']
```

**Hierarquia de acesso:** `none < read_only < write_only < read_write`. Quando `scope: []`, acesso é global (qualquer pool/campaign passa na validação de escopo).

#### Componente: `src/components/ModulePermissionForm.tsx`

Formulário dinâmico de permissões ABAC para uso na gestão de usuários:

```typescript
<ModulePermissionForm
  modules={modules}              // retorno de GET /auth/modules?active_only=true
  value={userModuleConfig}       // estado atual
  onChange={(newConfig) => {...}}
  readOnly={false}
  adminToken={adminToken}
/>
```

**Comportamento:**
- Renderiza acordeões colapsáveis por módulo
- Cada campo do `permission_schema` vira um `<select>` com opções: None, Read Only, Write Only, Read Write
- Quando `access != none` e `scopable: true`, renderiza input de escopo com chips remoníveis (pool_id ou campaign_id)
- Suporta modo `readOnly` (desabilita inputs, buttons)
- Validação inline: rejeita duplicatas de escopo, detecta escopos inválidos

### Integração no gerenciamento de usuários

#### `src/modules/access/AccessPage.tsx` — CRUD de usuários com ABAC

O modal de criação/edição de usuário agora inclui `ModulePermissionForm`:

- Hook `useModules(adminToken)` → `GET /auth/modules?active_only=true`
- Ao criar usuário: `POST /auth/users` → depois `PUT /auth/users/{id}/module-config` com config
- Ao editar: `PATCH /auth/users/{id}` + `PUT /auth/users/{id}/module-config` (ambos simultâneos via Promise.all)
- Modal ampliado de `max-w-lg` para `max-w-2xl` para acomodar formulário de permissões
- Estados de salvamento e erro propagados para `ModulePermissionForm`

### Sidebar — filtragem ABAC de itens de nav

#### `src/shell/Sidebar.tsx` — gates ABAC opcionais em `NavItem`

```typescript
export interface NavItem {
  label: string
  href: string
  icon: string
  roles?: string[]              // filtro RBAC
  abac?: {                       // novo — filtro ABAC
    module: string
    field: string
  }
}
```

#### Função `passesAbac(item, moduleConfig)`

- Sem campo `abac` → sempre visível
- Sem `moduleConfig` no session (conta legacy) → graceful degradation, mostra o item (backward-compatible)
- Com `moduleConfig` → avalia `perms.can(item.abac.module, item.abac.field)` → visível se true

**`operacao` gates — contacts, workflows, skill_flows (added in sidebar refactor):**

```typescript
// Operational items hidden for users with operacao: none (e.g. business role)
{ href: '/contacts?tab=monitor', abac: { module: 'contacts',    field: 'operacao' } },
{ href: '/agent-assist',         abac: { module: 'contacts',    field: 'operacao' } },
{ href: '/workflow/editor',      abac: { module: 'workflows',   field: 'operacao' } },
{ href: '/workflow/monitor',     abac: { module: 'workflows',   field: 'operacao' } },
{ href: '/workflow/calendar',    abac: { module: 'workflows',   field: 'operacao' } },
{ href: '/agent-flow/editor',    abac: { module: 'skill_flows', field: 'operacao' } },
{ href: '/agent-flow/monitor',   abac: { module: 'skill_flows', field: 'operacao' } },
{ href: '/agent-flow/deploy',    abac: { module: 'skill_flows', field: 'operacao' } },
// Report pages have NO gate — visible to all roles that reach the group
```

**Items de avaliação com gates ABAC definidos:**

```typescript
// Em navItems array
{
  label: t('nav.evaluation.forms'),
  href: '/evaluation/forms',
  icon: '📋',
  roles: ['admin'],
  abac: { module: 'evaluation', field: 'formularios' }
},
{
  label: t('nav.evaluation.campaigns'),
  href: '/evaluation/campaigns',
  icon: '📢',
  roles: ['supervisor', 'admin'],
  abac: { module: 'evaluation', field: 'formularios' }
},
{
  label: t('nav.eval.avaliacoes'),
  href: '/evaluation/avaliacoes',
  icon: '🗂️',
  roles: ['operator', 'supervisor', 'admin'],
  // No ABAC gate — page is visible to all; available_actions are computed server-side per row
},
{
  label: t('nav.evaluation.reports'),
  href: '/evaluation/reports',
  icon: '📊',
  roles: ['supervisor', 'admin'],
  abac: { module: 'evaluation', field: 'relatorio' }
}
```

### Integração no módulo de avaliação

#### `AvaliacoesPage.tsx` — tabela unificada (substitui ReviewPage + MyEvaluationsPage)

Visão única de todas as avaliações com filtros completos e drill-down lateral.
`available_actions` é computado server-side com Bearer JWT — nunca localmente:

```
GET /v1/evaluation/results?tenant_id=...&action_required=any     → Aguardando minha ação
GET /v1/evaluation/results?tenant_id=...&eval_status=submitted   → filtro por status
GET /v1/evaluation/results?tenant_id=...&campaign_id=...         → filtro por campanha
```

Por row, o servidor retorna `available_actions: ["review" | "contest"]` baseado no ABAC do JWT do caller.
A UI desabilita/oculta botões com base nesse campo — nunca computa permissão localmente.

#### Contestação por critério — `ContestPanel`

Operadores contestam avaliações critério a critério em vez de escrever uma razão genérica.

**Componentes:**
- `CriterionContestRow` — exibe `criterion_id`, score atribuído e justificativa do avaliador IA como contexto; checkbox para selecionar o critério para contestação; textarea de discordância (≥ 30 chars) com contador de caracteres e borda verde/vermelha
- `ContestPanel` — lista todos os critérios não-NA via `CriterionContestRow`; estado `crState: Record<criterion_id, { checked, justification }>`; botão de envio desabilitado até que ao menos um critério esteja selecionado e todas as justificativas tenham ≥ 30 chars

**Formato estruturado do campo `reason`** (compilado por `buildReason()`, armazenado no backend como string):
```
[criterion_id] Nota atribuída: 7.0/10
Avaliação do sistema: O agente seguiu o protocolo...
Discordância: Na verdade o agente não...

---

[criterion_id2] Nota atribuída: 4.0/10
Avaliação do sistema: ...
Discordância: ...
```

Blocos separados por `\n\n---\n\n`. Backward-compatible — contestações legadas (texto livre) são tratadas como fallback.

**`parseContestationReason(reason: string): ParsedCriterionContestation[]`** — helper puro que faz split em `\n\n---\n\n` e extrai `criterion_id`, `score_label`, `system_evaluation`, `disagreement` por bloco. Fallback gracioso para texto não estruturado.

#### Revisão por critério — `ReviewPanel`

Revisores (IA e humanos) respondem por critério, no mesmo formato estruturado.

- Contestações abertas renderizadas via `parseContestationReason()` — cada critério contestado em card próprio mostrando score original, avaliação do sistema e discordância do contestante
- Textareas de nota por critério abaixo de cada card (opcional por linha)
- Nota geral (obrigatória apenas para `adjusted_approved` ou `rejected`)
- `buildReviewNote()` compila `[criterion_id] nota\n\n---\n\n[geral] nota` no mesmo formato estruturado
- Estado `crNotes: Record<criterion_id, string>` + `generalNote: string`

**Invariante:** o mesmo parser `parseContestationReason` é usado tanto para exibir a contestação ao revisor quanto para exibir a resposta do revisor ao contestante — formato é simétrico.

#### `CampaignsPage.tsx` — campos Arc 6 v2 no CreateModal e painel de detalhe

**CreateModal** (expandido para 620 px, layout 2 colunas):
- Dropdown "Skill de revisão" → `review_workflow_skill_id` (opções: `skill_revisao_simples_v1`, `skill_revisao_treplica_v1`)
- Seção "Política de contestação" opt-in via checkbox:
  - Máximo de rounds, prazo de revisão (horas), alçada (`supervisor` / `manager` / `director`), auto-lock por timeout
  - Informação contextual: "sem política = contestação desabilitada"

**Painel de detalhe** — 4 cards: Sampling, Reviewer IA, Skill de revisão (id monoespaçado + descrição), Política de contestação (max rounds / prazo / auto-lock / breakdown por round)

### Invariantes (nunca violar)

- **`module_config` é avaliado localmente no frontend** — nunca fazer round-trip ao servidor para verificar permissão ABAC em roteamento de UI
- **Graceful degradation:** usuários sem `moduleConfig` recebem acesso baseado apenas em `role` (backward-compatible com contas legacy)
- **O `module_registry` é seed automático** ao iniciar auth-api — nunca inserir manualmente via SQL
- **`makePermissions` é puro** (sem efeitos colaterais) — seguro chamar em render sem memoização
- **Permissão ABAC é um filtro adicional** ao filtro de roles — roles continuam sendo a primeira barreira. Ambos devem passar (`AND` lógico)
- **Nunca persistir `PermissionAccess` como strings hardcoded** — sempre usar tipos TypeScript para evitar typos
- **Escopo sempre em formato `{type}:{id}`** — ex: `pool:retencao_humano`, `campaign:camp_abc123`, nunca valores soltos

---

## Pending (next iteration)

> **Regra de registro:** toda vez que um escopo é dividido em fases e apenas algumas são implementadas, as fases restantes são obrigatoriamente listadas aqui antes de encerrar a sessão de trabalho. Marcar como "futuro" ou "deferred" em comentários inline não é suficiente.

### Context-Aware / ContextStore

- **Fase 2 — Co-pilot** *(deferred)*: AI Gateway analisa cada mensagem do cliente em background durante a sessão de um agente humano e popula a aba "Capacidades" do Agent Assist com sugestões de resposta personalizada, flags de risco (sentimento, intenção detectada) e ações recomendadas baseadas em `motivo_contato`. Depende de `session.sentimento.*` e `caller.*` já populados no ContextStore.
- **Fase 3 — Step `resolve` nativo** *(deferred)*: novo step type no skill-flow-engine que encapsula a lógica do `agente_contexto_ia_v1` de forma declarativa inline no YAML (sem depender de agente externo via `task` step). Permite que qualquer agente defina pré-requisitos de contexto inline.

### Arc 5 — ContactSegment (v2)

- **Enrichment post-hoc de `segment_id`** *(deferred)*: eventos sem `segment_id` emitidos por componentes externos (ex: `sentiment_events`, `mcp.audit`) precisam ser enriquecidos retroativamente com o `segment_id` correto a partir de lookup no Redis/ClickHouse pelo `session_id` + `participant_id`.
- **Materialized views ClickHouse** *(deferred)*: criar `mv_segment_summary` (métricas por segmento: avg_duration_ms, resolution_rate, escalation_rate) e `mv_agent_performance` (métricas por agent_type_id + pool_id, base para Arc 7d performance routing).

### Skill Deploy Lifecycle (Phase 2)

- **Deploy agendado via Workflow engine** *(deferred)*: integrar com workflow-api para criar uma instância `suspend → deploy no horário configurado`. UI: seletor de data/hora no DeployPage + listagem de deploys agendados pendentes.
- **Graceful handoff monitor** *(deferred)*: painel no DeployPage mostrando progresso por pool — quantas sessões ainda usam a versão anterior vs nova, com indicador visual de convergência.
- **Automated rollback button** *(deferred)*: botão na UI de Deploy que dispara um novo deploy apontando para o `yaml_snapshot` de uma versão anterior listada no histórico.

### Usage Metering — Channel Gateway adapters

- **whatsapp_conversations, voice_minutes, sms_segments, email_messages** *(deferred)*: as funções em `usage_emitter.py` estão implementadas e documentadas, mas os adapters de canal ainda não as chamam. Será wired quando cada adapter for criado (WhatsApp, WebRTC/Voice, SMS, Email).

### Pricing Module

- **Integração metering × pricing** *(deferred)*: módulo que lê os contadores de `usage.events` no Redis/ClickHouse, aplica planos configurados no Config API e escreve `{tenant}:quota:limit:*` no Redis. Atualmente separação está incompleta — metering registra mas pricing não consome.

### Config API / Routing Engine

~~**Consumo de `config.changed` namespace `routing`**~~ ✅ Implementado. `RoutingConfigCache` em `routing_config.py` faz fetch do Config API no startup e é invalidado/recarregado via `ConfigChangedHandler` ao receber `config.changed` com `namespace=routing`. `performance_score_weight` lido dinamicamente do cache com fallback para env var. 15/15 testes em `test_routing_config.py`.

### Agent Assist UI — MenuCard substitution mode (Phase 2)

- **Modo substituição nos MenuCards** *(deferred)*: os MenuCards renderizados no chat do agente humano são read-only (todos os inputs têm `disabled`). Para ativar o modo substituição: (1) remover `disabled` dos elementos interativos em `MenuCard.tsx`; (2) adicionar handler `POST /api/menu_submit/{sessionId}` com payload `{ menu_id, interaction, result }`; (3) flag `substitutionMode: boolean` prop no componente para alternar entre os dois modos sem duplicar código.

### Arc 2 — fechamento

- ~~E2E scenario 12: webchat auth flow + media upload end-to-end~~ ✅
- ~~Usage Metering no Channel Gateway (voice_minutes, whatsapp_conversations, sms_segments)~~ ✅
- ~~WebChat reconexão fase 2: tratar stream TTL expirado + jwt_secret por tenant~~ ✅
- ~~AttachmentStore fase 2: S3/MinIO~~ ✅
- ~~Magic bytes validation no upload (phase 2)~~ ✅
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
    - **9 namespaces seedados**: `sentiment` (thresholds, live_ttl_s), `routing` (snapshot_ttl_s, sla_default_ms, score_weights, estimated_wait_factor, congestion_sla_factor), `session` (ai_gateway_ttl_s, channel_gateway_ttl_s), `consumer` (batch_size, timeout_ms, restart_delay_s, max_restart_delay_s), `dashboard` (sse_interval_s, sse_retry_ms), `webchat` (auth_timeout_s, attachment_expiry_days, upload_limits_mb), `masking` (authorized_roles, default_retention_days, capture_input_default, capture_output_default), `quota` (max_concurrent_sessions, llm_tokens_daily, messages_daily), `dashboards` (default_template_id, allow_user_customization, max_cards_per_dashboard — templates armazenados como `template:{uuid}`, layouts pessoais como `layout:{tenant}:{user}`).
    - **`ConfigStore`**: `get()` (cache hit → DB miss), `get_or_default()`, `list_namespace()` (com cache de namespace), `list_all()`, `set()` (upsert + invalidação imediata), `delete()`. Invalidação global faz SCAN para limpar variantes de tenant.
    - **`config.changed` (Kafka)**: Config API publica no tópico `config.changed` após cada PUT/DELETE bem-sucedido. Payload: `{event, tenant_id, namespace, key, operation, updated_at}`. Consumidores roteiam por namespace:
      | Namespace | Consumidor | Reação |
      |---|---|---|
      | `quota` | orchestrator-bridge | `bootstrap.request_refresh()` — reconcilia instâncias |
      | `routing` | routing-engine (futuro) | invalida cache local de SLA/scoring |
      | `masking`, `session`, `webchat`, `sentiment`, `consumer`, `dashboard` | (cache Redis 60s) | sem ação imediata; propagação natural via TTL |
    - Tests: `test_store.py` (27 assertions — TestConfigCache, TestConfigStoreGet, TestConfigStoreSet, TestConfigStoreDelete, TestConfigStoreList, TestSeedData).

11. ~~**Timeseries endpoints (analytics-api)**~~: ✅ `GET /reports/timeseries/volume`, `/reports/timeseries/handle_time`, `/reports/timeseries/score`. Parâmetros: `tenant_id`, `interval` (minutos, 1–1440, default 60), `from_dt`, `to_dt`, `breakdown_by` (pool_id|channel para volume/handle_time; campaign_id|form_id para score), `pool_id`, `format` (json|csv). Usa `toStartOfInterval(timestamp, toIntervalMinute($interval))` para bucketing dinâmico. Arquivo `packages/analytics-api/src/plughub_analytics_api/timeseries_query.py`. Pool scoping via `optional_pool_principal` (Arc 7c). Resposta: `{ buckets: [{bucket, value, breakdown}], meta: {interval_minutes, from_dt, to_dt, total} }`.

12. ~~**Dashboard com drag-and-drop e templates (platform-ui)**~~: ✅ `packages/platform-ui/src/modules/dashboards/DashboardsPage.tsx` — substitui `PlaceholderPage` em `/dashboards`. Componentes principais: `TimeseriesChart` genérico (`src/components/TimeseriesChart/`), `DashboardsPage` com react-grid-layout, sistema de templates via Config API namespace `dashboards`.

    **TimeseriesChart** (`src/components/TimeseriesChart/`):
    - `TimeseriesChart.tsx` — componente Recharts; `compact=true` = sparkline + KPI para cards; `compact=false` = modo completo com interval picker, date range, exportação CSV (nível 1) e raw (nível 2)
    - `useTimeseriesData.ts` — hook de fetch + polling; suporta `accessToken` para Bearer
    - `formatters.ts` — `formatCount`, `formatDurationMs`, `formatScore`, `formatBucketLabel`
    - `chartType: bar | line | area`; paleta de 8 cores para breakdown series

    **DashboardsPage** — rota `/dashboards`, acessível via Configuração → Templates de Dashboard (gate: `config.plataforma`):
    - Sidebar (admin only): lista de templates + botão "Novo template"; nome + descrição salvos no Config API como `dashboards.template:{uuid}`
    - Grid: react-grid-layout 12 colunas, cards draggable/resizable em modo edição
    - Card types: `timeseries_volume`, `timeseries_handle_time`, `timeseries_score`, `pool_status`
    - Modo admin: criar/remover templates, adicionar/remover cards, salvar layout no template compartilhado
    - Modo usuário: drag/resize persiste como layout pessoal (`dashboards.layout:{tenant}:{user}`) sem alterar o template compartilhado
    - Template padrão lido de `module_config.dashboard.default_template_id` do JWT (ABAC)
    - Fallback: layouts pessoais armazenados em localStorage quando admin token ausente
    - Botão "Editar" desabilitado quando nenhum template está selecionado (previne erros de operação)
    - Grid e editMode resetam automaticamente quando template é deletado (useEffect null-template cleanup)

    **Config API — namespace `dashboards`** (3 novas chaves seedadas):
    - `default_template_id` → null
    - `allow_user_customization` → true
    - `max_cards_per_dashboard` → 20

    **Config API — fix: admin token via query param fallback** (`router.py`):
    - `_require_admin` aceita o token tanto via `X-Admin-Token` header quanto via `?admin_token=` query param
    - O fallback por query param garante que DELETE requests funcionem mesmo quando reverse proxies (nginx) removem headers customizados
    - `dashboard-hooks.ts`: `configDelete` envia o token em ambos (header + query param); todas as funções propagam `tenant_id` como query param; `configListNamespace` faz unwrap correto do envelope `{ entries: {...} }` da Config API

    **Tipos em `src/types/index.ts`:** `TimeseriesBreakdown`, `TimeseriesBucket`, `TimeseriesMeta`, `TimeseriesResponse`, `DashboardCardType`, `TimeseriesCardConfig`, `KpiCardConfig`, `PoolStatusCardConfig`, `DashboardCardConfig`, `DashboardCard`, `DashboardTemplate`

    **Deps adicionadas ao `package.json`:** `react-grid-layout@^1.4.4`, `@types/react-grid-layout@^1.3.5`

**Arquitetura de dados:**
```
Kafka topics → analytics-api consumer → ClickHouse
  (conversations.*, agent.done, usage.events, queue.position_updated, sentiment.updated)

Redis snapshots + sentiment_live → analytics-api → SSE → operator-console
PostgreSQL (evaluation, sentiment_timeline) → analytics-api (queries pontuais)

ClickHouse → /reports/timeseries/* → TimeseriesChart (platform-ui) → DashboardsPage cards
ClickHouse → Metabase (relatórios self-service)
analytics-api REST → BI externos (PowerBI, Looker, Tableau)
```

### Arc 5 — ContactSegment (✅ implementado — v1)

Base analítica para SLA por agente, avaliação granular e relatórios de participação com duração real.
ADR: `docs/adr/adr-contact-segments.md`.

**ContactSegment** é a entidade que representa uma janela de participação contígua de um agente numa sessão.
Cada segmento tem `segment_id` próprio, `sequence_index` para handoffs sequenciais, e `parent_segment_id`
para a topologia de conferência (specialist aponta para o primary segment).

#### Schemas — `@plughub/schemas/src/contact-segment.ts`

```typescript
ContactSegmentSchema {
  segment_id:        UUID
  session_id, tenant_id, participant_id, pool_id, agent_type_id, instance_id
  role:              "primary" | "specialist" | "supervisor" | "evaluator" | "reviewer"
  agent_type:        "ai" | "human"
  parent_segment_id: UUID | null      // null para primary; specialist aponta para primary
  sequence_index:    number           // 0 para primeiro primary; 1+ para handoffs sequenciais
  started_at, ended_at, duration_ms
  outcome:           "resolved" | "escalated" | "transferred" | "abandoned" | "timeout" | null
  close_reason, handoff_reason, issue_status
}

ConversationParticipantEventSchema {
  event_type:        "participant.joined" | "participant.left"
  segment_id:        UUID    // obrigatório — gerado no orchestrator-bridge
  sequence_index, parent_segment_id
  ...demais campos existentes...
}
```

#### orchestrator-bridge — geração de segment_id

`_publish_participant_event` estendida com os parâmetros: `segment_id`, `sequence_index`, `parent_segment_id`, `outcome`, `close_reason`, `handoff_reason`, `issue_status`.

| Evento | Redis key | Lógica |
|--------|-----------|--------|
| `activate_human_agent` | `session:{id}:segment:{instance_id}` (TTL 4h) + `session:{id}:primary_segment` + INCR `session:{id}:segment_seq` | Gera `_seg_id`, armazena, publica `participant_joined` com `sequence_index` |
| `process_routed` (native joined) | `session:{id}:segment:{native_instance_id}` (TTL 4h) | Lê `primary_segment` para conferência (→ `parent_segment_id`), armazena novo `_part_seg_id` |
| `process_routed` (native left) | GETDEL `session:{id}:segment:{instance_id}` | Recupera o mesmo UUID usado no joined; passa `outcome` do `agent_result` |
| `process_contact_event` (human left) | GETDEL `session:{id}:segment:{instance_id}` | Mesmo padrão |

#### analytics-api — tabelas ClickHouse

| Tabela | Engine | Descrição |
|--------|--------|-----------|
| `analytics.segments` | `ReplacingMergeTree(ingested_at)` ORDER BY `(tenant_id, session_id, segment_id)` | Uma linha por participação; `participant_left` win sobre `participant_joined` no merge |
| `analytics.session_timeline` | `ReplacingMergeTree(ingested_at)` ORDER BY `(tenant_id, session_id, timestamp, event_id)` | Série temporal de eventos enriquecidos com `segment_id` |

#### analytics-api — `models.py`

`parse_participant_event` retorna `list[dict]` em vez de `dict` — dois rows por evento:
- `participation_row` → tabela `participation_intervals` (legado, compatibilidade Arc 3 Fase C)
- `segment_row` → tabela `segments` (Arc 5); inclui `segment_id`, `parent_segment_id`, `sequence_index`, `outcome`, `close_reason`, `handoff_reason`, `issue_status`

#### analytics-api — endpoints

| Endpoint | Filtros | Descrição |
|----------|---------|-----------|
| `GET /reports/segments` | `session_id`, `pool_id`, `agent_type_id`, `role`, `outcome`, `from_dt`, `to_dt`, `page`, `page_size`, `format` | Linhas de `segments FINAL` (ReplacingMergeTree dedup); `format=csv` disponível |
| `GET /reports/agents/performance` | `pool_id`, `agent_type_id`, `role`, `from_dt`, `to_dt`, `format` | Métricas agregadas por `(agent_type_id, pool_id, role)`: `total_sessions`, `avg_duration_ms`, `escalation_rate`, `handoff_rate`, breakdowns por outcome; sem paginação |

#### Arquivos modificados / criados

| Arquivo | Alteração |
|---------|-----------|
| `packages/schemas/src/contact-segment.ts` | CRIADO — `SegmentOutcomeSchema`, `ContactSegmentSchema`, `ConversationParticipantEventSchema` |
| `packages/schemas/src/index.ts` | Exporta os novos schemas de contact-segment |
| `packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py` | `_publish_participant_event` + Redis keys de segment tracking em `activate_human_agent`, `process_routed`, `process_contact_event` |
| `packages/analytics-api/src/plughub_analytics_api/clickhouse.py` | `_DDL_SEGMENTS`, `_DDL_SESSION_TIMELINE`, `upsert_segment()`, `insert_timeline_event()` |
| `packages/analytics-api/src/plughub_analytics_api/models.py` | `parse_participant_event` → `list[dict]`; segment_row completo |
| `packages/analytics-api/src/plughub_analytics_api/consumer.py` | Dispatch `segments` → `upsert_segment`; `session_timeline` → `insert_timeline_event` |
| `packages/analytics-api/src/plughub_analytics_api/reports_query.py` | `query_segments_report` + `_fetch_segments`; `query_agent_performance_report` + `_fetch_agent_performance` |
| `packages/analytics-api/src/plughub_analytics_api/reports.py` | `GET /reports/segments` endpoint; `GET /reports/agents/performance` endpoint |
| `packages/analytics-api/.../tests/test_consumer.py` | `TestParseParticipantEvent` (8 assertions); `test_segments_dispatched`, `test_session_timeline_dispatched` |
| `packages/analytics-api/.../tests/test_reports.py` | `TestQuerySegmentsReport` (3 assertions); `TestQueryAgentPerformanceReport` (5 assertions) |
| `packages/e2e-tests/scenarios/23_contact_segments.ts` | CRIADO — Parts A/B/C (11 assertions) |
| `packages/e2e-tests/runner.ts` | `--segments` flag + import `scenario23` |

**Pendente (v2):**
- Enrichment post-hoc de `segment_id` em eventos sem o campo (sentimento, mcp.audit)
- Materialized views `segment_summary` e `agent_performance`

### AI Gateway — Multi-account rotation, workload isolation, fallback chain (✅ implementado)

Suporte a múltiplas chaves de API Anthropic + OpenAI como fallback, com isolamento de workloads por `model_profile` e rotação automática sob throttling.

#### AccountSelector — Redis-backed load balancing

Implementado em `packages/ai-gateway/src/plughub_ai_gateway/account_selector.py`.

**Princípio:** stateless a cada chamada. Nenhum estado em memória — todas as decisões baseadas em Redis.

```
AccountSelector.pick(provider):
  1. Para cada conta registrada do provider:
     a. Verifica throttle key (MGET ai_gw:{provider}:{key_id}:throttled) — exclui se presente
     b. Calcula score: (rpm_used/rpm_limit × rpm_weight) + (tpm_used/tpm_limit × tpm_weight)
  2. Retorna provider_key da conta com menor score
  3. None → sem contas disponíveis → fallback cross-provider
```

**Redis keys:**

| Key | TTL | Conteúdo |
|---|---|---|
| `ai_gw:{provider}:{key_id}:throttled` | `throttle_retry_after_s` (Config API) | `"1"` — conta excluída da rotação |
| `ai_gw:{provider}:{key_id}:rpm` | 60 s (rolling) | Contador de requests no minuto atual |
| `ai_gw:{provider}:{key_id}:tpm` | 60 s (rolling) | Contador de tokens no minuto atual |

**`key_id`** = SHA-256(api_key)[:16] — nunca persiste o valor bruto da chave.

**Scoring:** `score = (rpm_used/rpm_limit × 0.7) + (tpm_used/tpm_limit × 0.3)`. Pesos configuráveis via Config API namespace `ai_gateway` (`utilization_rpm_weight`).

#### Configuração multi-chave

```bash
# Uma chave (backward compatible)
PLUGHUB_ANTHROPIC_API_KEY=sk-ant-...

# Múltiplas chaves (vírgula separado — AccountSelector ativado)
PLUGHUB_ANTHROPIC_API_KEYS=sk-ant-...,sk-ant-...,sk-ant-...

# OpenAI como fallback (opcional — requer pacote openai>=1.0.0)
PLUGHUB_OPENAI_API_KEYS=sk-...
```

`Settings.get_anthropic_keys()` / `get_openai_keys()` normalizam ambos os formatos.

#### Registro de providers em main.py

```python
# Anthropic: um provider por chave + alias "anthropic" → primeira chave (backward compat /v1/turn, /v1/reason)
for api_key in anthropic_keys:
    acc = LLMAccount(provider="anthropic", api_key=api_key, rpm_limit=..., tpm_limit=...)
    providers[acc.provider_key] = AnthropicProvider(api_key=api_key)   # "anthropic:{key_id}"
    accounts.append(acc)
providers["anthropic"] = providers[accounts[0].provider_key]            # alias primeira chave

# OpenAI: idem, opcional
for api_key in openai_keys:
    acc = LLMAccount(provider="openai", api_key=api_key, ...)
    providers[acc.provider_key] = OpenAIProvider(api_key=api_key)       # "openai:{key_id}"
    accounts.append(acc)
```

`AccountSelector(redis, accounts)` criado se `accounts` não-vazio; `None` em dev sem chaves.

#### _call_with_fallback — cadeia completa

```
InferenceEngine._call_with_fallback(profile, messages, tools):

  1. AccountSelector.pick(profile.provider)
       → provider_key (e.g. "anthropic:abc123")
       → None → vai direto ao fallback cross-provider

  2. provider.call(messages, tools, model_id, max_tokens)
       ✓ success → record_usage(provider_key, tokens) → return
       ✗ retryable (rate_limit / status_429 / status_529):
           → mark_throttled(provider_key, ttl=throttle_retry_after_s)
           → AccountSelector.pick(provider) novamente (próxima conta)
               → retry → sucesso ou exaustão
       ✗ não-retryable → raise ProviderError

  3. Sem contas disponíveis (todas throttled) OU sem AccountSelector:
       profile.fallback presente?
           Sim → FallbackConfig(provider, model_id)
                 → providers[fallback.provider].call(model_id=fallback.model_id, ...)
                 → return (provider_used=fallback.provider)
           Não → raise ProviderError(retryable=False)
```

#### Isolamento de workloads por model_profile

| Profile | Model | Fallback | Uso |
|---|---|---|---|
| `realtime` | `claude-sonnet-4-6` | `gpt-4o` (se OpenAI configurado) | Agentes em atendimento ao vivo |
| `balanced` | `claude-haiku-4-5-20251001` | `gpt-4o-mini` | Fluxos de baixa latência |
| `evaluation` | `claude-haiku-4-5-20251001` | `model_balanced` | Avaliação batch — isolado de tráfego realtime |

O profile `evaluation` garante que workloads de avaliação não compitam com agentes em sessão.
`evaluation_model` e `evaluation_max_tokens` são configuráveis via Config API namespace `ai_gateway`.

#### OpenAIProvider

`packages/ai-gateway/src/plughub_ai_gateway/providers/openai_provider.py`

- Degrada graciosamente se pacote `openai` ausente (levanta `ProviderError(error_code="sdk_not_installed")`)
- Converte formato de tools Anthropic (`input_schema`) para OpenAI (`function.parameters`)
- Role mapping: `customer→user`, `agent→assistant`, `system→system` (system vai como `role: system`, diferente do Anthropic que usa campo `system=`)
- Stop reason: `tool_calls→tool_use`, `length→max_tokens`, else `end_turn`

#### Config API — namespace ai_gateway

| Key | Default | Descrição |
|---|---|---|
| `account_rotation_enabled` | `true` | Habilita AccountSelector; `false` = sempre usa primeira chave |
| `throttle_retry_after_s` | `60` | TTL de exclusão após 429/529 |
| `utilization_rpm_weight` | `0.7` | Peso RPM no score (TPM = 1 - rpm_weight) |
| `evaluation_model` | `claude-haiku-4-5-20251001` | Modelo do profile `evaluation` |
| `evaluation_max_tokens` | `2048` | Max tokens para inferência de avaliação |
| `openai_fallback_enabled` | `false` | Documenta se fallback OpenAI está ativo (operacional) |

#### Tests

`packages/ai-gateway/src/plughub_ai_gateway/tests/test_account_selector.py` — 29 assertions:
`TestLLMAccount` (5), `TestAccountSelectorPick` (8), `TestMarkThrottled` (2), `TestRecordUsage` (3), `TestHealthSummary` (3), `TestProvidersFor` (2), `TestSettingsKeyParsing` (6).

## Arc 6 — Plataforma de Avaliação de Qualidade

Plataforma completa de avaliação de qualidade de interações: formulários configuráveis, campanhas de amostragem, agentes avaliadores com RAG, revisão humana, contestação e relatórios analíticos.

### Novos pacotes

- `packages/evaluation-api/` — Python FastAPI, porta 3400. Ciclo de vida completo de formulários, campanhas, instâncias, resultados e contestações.
- `packages/mcp-server-knowledge/` — TypeScript MCP Server. Base de conhecimento vetorial (pgvector) para RAG nos agentes avaliadores.

### Novos schemas em `@plughub/schemas`

| Schema | Arquivo | Descrição |
|---|---|---|
| `EvaluationForm`, `EvaluationCriterion` | `evaluation.ts` | Formulário com critérios configuráveis |
| `EvaluationCampaign`, `SamplingRules`, `ReviewerRules` | `evaluation.ts` | Campanha de amostragem com regras |
| `ContestationPolicy`, `ContestationRound` | `evaluation.ts` | Política de contestação configurável por campanha (Arc 6 v2) |
| `EvaluationPermission` | `evaluation.ts` | Permissão 2D usuário × (pool \| campanha) (Arc 6 v2) |
| `EvaluationInstance` | `evaluation.ts` | Instância de avaliação de uma sessão |
| `EvaluationResult`, `EvaluationCriterionResponse` | `evaluation.ts` | Resultado com respostas por critério |
| `EvaluationResultWithActions` | `evaluation.ts` | Resultado + `available_actions` computado server-side (Arc 6 v2) |
| `EvaluationContestation` | `evaluation.ts` | Contestação de resultado |
| `EvaluationEvent` | `evaluation.ts` | Evento Kafka `evaluation.events` |
| `KnowledgeSnippet` | `evaluation.ts` | Snippet da base de conhecimento |

#### EvaluationForm / EvaluationCriterion

```typescript
EvaluationCriterion {
  id: string                  // criterion_id único no formulário
  label: string               // "Seguiu protocolo de saudação"
  description: string         // instrução para o avaliador
  weight: number              // 0.0–1.0, sum of all criteria = 1.0
  type: "score" | "pass_fail" | "text" | "na_allowed"
  options?: { value: number; label: string }[]   // para score com escala customizada
}

EvaluationForm {
  form_id: string
  tenant_id: string
  name: string
  description?: string
  criteria: EvaluationCriterion[]
  knowledge_namespace?: string    // namespace RAG para snippets relevantes
  active: boolean
  created_at, updated_at: string
}
```

#### EvaluationCampaign / SamplingRules / ReviewerRules

```typescript
SamplingRules {
  mode: "all" | "random" | "pool_filter" | "segment_filter"
  sample_rate?: number          // 0.0–1.0 (modo random)
  pool_ids?: string[]           // filtro por pool
  outcome_filter?: string[]     // filtro por outcome (resolved/escalated/…)
  min_duration_ms?: number      // ignora sessões muito curtas
}

ReviewerRules {
  auto_approve_above: number    // score ≥ threshold → approved sem revisão humana
  auto_reject_below: number     // score < threshold → rejected sem revisão humana
  require_human_review: boolean // força revisão humana independente do score
}

// Política de contestação configurável por campanha
ContestationRound {
  round_number:     number        // 1-based
  contestation_roles: string[]   // roles que podem contestar neste round
  review_roles:     string[]      // roles que podem revisar neste round
  authority_level:  string        // "supervisor" | "manager" | "director"
  review_deadline_hours: number   // SLA do round (business_hours: true implícito)
}

ContestationPolicy {
  contestation_roles: string[]           // roles globais que podem contestar
  review_roles_by_round: Record<number, string[]>  // role por round (herda contestation_roles como fallback)
  authority_by_round: Record<number, string>       // authority_level por round
  review_deadline_hours: number          // SLA padrão de revisão
}

EvaluationCampaign {
  campaign_id: string
  tenant_id: string
  name: string
  form_id: string
  pool_id?: string
  sampling: SamplingRules
  reviewer_rules: ReviewerRules
  contestation_policy: ContestationPolicy   // configura ciclos de revisão/contestação
  review_workflow_skill_id: string          // skill YAML que roda como motor de estado (ex: "skill_revisao_treplica_v1")
  status: "active" | "paused" | "completed"
  evaluator_pool_id: string     // pool do agente_avaliacao_v1
  created_at, updated_at: string
}
```

#### EvaluationInstance / EvaluationResult / EvaluationCriterionResponse

```typescript
EvaluationCriterionResponse {
  criterion_id: string
  score?: number        // valor numérico (para type=score)
  passed?: boolean      // (para type=pass_fail)
  na: boolean           // critério marcado como N/A
  evidence?: string     // trecho da transcrição usado como evidência
  note?: string         // observação do avaliador
}

EvaluationResult {
  result_id: string
  instance_id: string
  session_id: string
  tenant_id: string
  evaluator_id: string              // instance_id do agente avaliador
  form_id: string
  campaign_id?: string
  criterion_responses: EvaluationCriterionResponse[]
  overall_score: number             // ponderado pelos weights dos critérios
  eval_status: "submitted" | "under_review" | "reviewed" | "contested" | "locked"
  locked: boolean                   // resultado finalizado, imutável
  lock_reason?: string              // "review_timeout" | "max_rounds_reached" | "manual"
  compliance_flags: string[]        // ["sla_breached", "escalation_required"]
  review_note?: string              // nota do revisor humano
  reviewed_by?: string
  reviewed_at?: string
  timestamp: string                 // ISO-8601 de submissão
  // Campos do motor de workflow (Arc 6 v2)
  workflow_instance_id?: string     // UUID da instância workflow-api associada
  resume_token?: string             // token atual para retomar o workflow (TTL = deadline do suspend)
  action_required?: string          // "review" | "contestation" | null (persisted from workflow.events consumer)
  current_round: number             // round atual do ciclo (0 = pré-revisão)
  deadline_at?: string              // ISO-8601 do prazo do round atual
}

// Retornado pelo endpoint GET /v1/evaluation/results/{id}?caller_user_id=
// Campo adicional computado server-side — nunca persisted no banco
EvaluationResultWithActions extends EvaluationResult {
  available_actions: ("review" | "contest")[]   // [] quando locked ou sem permissão
  action_context?: {
    deadline_at: string
    round: number
    authority_level: string
  }
}
```

### evaluation-api (porta 3400)

#### Forms CRUD

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/v1/evaluation/forms?tenant_id=` | Lista formulários ativos |
| `POST` | `/v1/evaluation/forms` | Cria formulário |
| `PATCH` | `/v1/evaluation/forms/{form_id}` | Atualiza formulário |
| `DELETE` | `/v1/evaluation/forms/{form_id}` | Remove formulário |

#### Campaigns CRUD + controle

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/v1/evaluation/campaigns?tenant_id=` | Lista campanhas |
| `POST` | `/v1/evaluation/campaigns` | Cria campanha |
| `POST` | `/v1/evaluation/campaigns/{id}/pause` | Pausa campanha |
| `POST` | `/v1/evaluation/campaigns/{id}/resume` | Retoma campanha |

#### Instances lifecycle

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/v1/evaluation/instances?campaign_id=&status=` | Lista instâncias por campanha |

Instâncias são criadas automaticamente pelo **sampling engine** ao consumir eventos `conversations.session_closed`. O engine avalia `SamplingRules` e cria a instância se a sessão for selecionada.

#### Results

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/v1/evaluation/results?tenant_id=&campaign_id=&evaluator_id=` | Lista resultados |
| `GET` | `/v1/evaluation/results/{id}?caller_user_id=` | Detalhe com `available_actions` computado server-side |
| `POST` | `/v1/evaluation/results/{result_id}/review` | Revisor humano age (requer JWT + permissão de review no pool/campanha) |

Body de review: `{ decision: "approved" | "rejected", round: number, review_note? }`. O campo `round` é anti-replay — deve ser igual a `result.current_round` ou o servidor retorna `409`.

#### Contestations

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/v1/evaluation/contestations?tenant_id=&result_id=` | Lista contestações |
| `POST` | `/v1/evaluation/contestations` | Cria contestação (requer JWT + permissão de contest no pool/campanha) |
| `POST` | `/v1/evaluation/contestations/{id}/adjudicate` | Adjudica contestação — mantido para compatibilidade (fluxo legado sem workflow) |

Body de contestation: `{ result_id, reason, round: number }`. O campo `round` é anti-replay — deve ser igual a `result.current_round`.

#### Reports

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/v1/evaluation/reports/campaigns/{id}` | Relatório por campanha |
| `GET` | `/v1/evaluation/reports/agents?tenant_id=&pool_id=` | Relatório por agente |

#### Auth

- **Operações admin** (CRUD de formulários e campanhas): `X-Admin-Token` header
- **Operações de revisão e contestação**: `Authorization: Bearer <jwt>` com claims `sub` (user_id), `roles[]` e `module_config`
  - O evaluation-api extrai `caller.user_id` do `sub` do JWT para registrar `reviewed_by` / `contested_by`
  - Permissões verificadas via ABAC `module_config.evaluation.revisar` / `module_config.evaluation.contestar` (sem DB lookup)
  - `_check_abac_permission(jwt_payload, field, pool_id)` — graceful degradation: sem module_config → permite (conta legacy)
  - Escopo: scope list vazio → acesso global; não-vazio → `pool:{pool_id}` deve estar na lista

**Nota:** a tabela `evaluation.permissions` e os endpoints `GET/POST/PATCH/DELETE /v1/evaluation/permissions` foram removidos. Permissões de avaliação são configuradas exclusivamente via ABAC no auth-api (`PUT /auth/users/{id}/module-config`), eliminando o risco de inconsistência entre os dois sistemas.

### mcp-server-knowledge

MCP Server separado para a base de conhecimento vetorial dos agentes avaliadores.

**PostgreSQL schema (pgvector):**
```sql
CREATE TABLE knowledge_snippets (
    snippet_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    TEXT NOT NULL,
    namespace    TEXT NOT NULL,           -- ex: "politicas_sac", "sla_contrato"
    content      TEXT NOT NULL,
    embedding    vector(1536),            -- OpenAI text-embedding-3-small
    source_ref   TEXT,                   -- documento de origem
    metadata     JSONB DEFAULT '{}',
    created_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON knowledge_snippets USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX ON knowledge_snippets (tenant_id, namespace);
```

**Tools expostos:**
| Tool | Descrição |
|---|---|
| `knowledge_search` | Busca semântica top-K no namespace, retorna `KnowledgeSnippet[]` |
| `knowledge_upsert` | Insere/atualiza snippet com embedding automático |
| `knowledge_delete` | Remove snippet por snippet_id |

**API REST (proxied via Vite `/v1/knowledge`):**
| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/v1/knowledge/search?tenant_id=&query=&namespace=&top_k=` | Busca semântica |
| `POST` | `/v1/knowledge/snippets` | Upsert snippet |
| `DELETE` | `/v1/knowledge/snippets/{id}` | Remove snippet |

### Agents

#### agente_avaliacao_v1 — form-aware + RAG + evidência

`packages/skill-flow-engine/skills/agente_avaliacao_v1.yaml`

Fluxo:
```
carregar_contexto (invoke: evaluation_context_get)
  → ReplayContext: stream events, form definition, campaign_context, knowledge_snippets (top-5)

avaliar_criterios (reason LLM):
  - Para cada critério do formulário:
    - Analisa transcrição → score / pass_fail / N/A
    - Extrai evidence (trecho textual)
    - Computa overall_score ponderado
  - Incorpora knowledge_snippets como contexto normativo
  - Detecta compliance_flags (sla_breached, escalation_required, protocol_violation)

submeter_resultado (invoke: evaluation_submit):
  - criterion_responses[] com evidence por critério
  - overall_score, compliance_flags
  - eval_status: "submitted"
```

**Fallback:** se agent-registry retorna HTTP 422 (YAML sem `complete`/`escalate`), `_load_yaml_fallback()` no orchestrator-bridge lê o arquivo YAML diretamente.

#### agente_reviewer_ia_v1 — auto-aprovação/rejeição

`packages/skill-flow-engine/skills/agente_reviewer_ia_v1.yaml`

Fluxo:
```
carregar_resultado (invoke: evaluation_context_get)
  → EvaluationResult + critérios + threshold rules

decisao_automatica (choice):
  overall_score >= reviewer_rules.auto_approve_above → aprovar
  overall_score <  reviewer_rules.auto_reject_below  → rejeitar
  reviewer_rules.require_human_review eq true        → fila_humana
  default                                            → fila_humana

aprovar (invoke: evaluation_submit):
  eval_status: "approved", review_note: "Auto-aprovado por score ≥ threshold"

rejeitar (invoke: evaluation_submit):
  eval_status: "rejected", review_note: "Auto-rejeitado por score < threshold"

fila_humana (notify agents_only):
  Sinaliza ao supervisor para revisão manual
```

### Modelo de Permissão 2D — usuário × (pool | campanha)

Eixo de permissão independente do papel do usuário no sistema: um mesmo usuário pode ter permissão de contestar num pool e de revisar em outro, e ter ambas em uma campanha específica.

#### PostgreSQL schema

```sql
CREATE TABLE evaluation_permissions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    scope_type  TEXT NOT NULL CHECK (scope_type IN ('pool', 'campaign', 'global')),
    scope_id    TEXT,           -- pool_id ou campaign_id; NULL para global
    can_contest BOOL NOT NULL DEFAULT FALSE,
    can_review  BOOL NOT NULL DEFAULT FALSE,
    granted_by  TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE (tenant_id, user_id, scope_type, scope_id)
);
```

#### Resolução de permissão (herança de escopo)

Resolução do mais específico para o mais geral. Permissões são acumulativas (union), não se excluem:

```
campaign-level  >  pool-level  >  global
```

Exemplo: usuário X com `can_review=true` no pool A e `can_contest=true` na campanha C (que usa o pool A) → X pode revisar via herança do pool, e contestar via herança da campanha. Ambas as permissões são válidas.

```python
async def resolve_permissions(tenant_id, user_id, campaign_id, pool_id) -> set[str]:
    rows = await db.fetch("""
        SELECT can_contest, can_review FROM evaluation_permissions
        WHERE tenant_id = $1 AND user_id = $2
          AND (
            (scope_type = 'campaign' AND scope_id = $3)
            OR (scope_type = 'pool'     AND scope_id = $4)
            OR (scope_type = 'global')
          )
    """, tenant_id, user_id, campaign_id, pool_id)

    permissions = set()
    for row in rows:
        if row["can_contest"]: permissions.add("contest")
        if row["can_review"]:  permissions.add("review")
    return permissions
```

#### `available_actions` — campo computado server-side

O endpoint `GET /v1/evaluation/results/{id}?caller_user_id=` devolve `available_actions` já calculado, combinando o estado do workflow (`action_required` + `locked`) com as permissões do usuário. A UI nunca computa permissão localmente — apenas lê o campo:

```
available_actions = []
if not locked and action_required == "review"       and "review"  in perms → ["review"]
if not locked and action_required == "contestation" and "contest" in perms → ["contest"]
```

| `action_required` | Permissão do caller | `available_actions` | Botões na UI |
|---|---|---|---|
| `"review"` | `can_review=true` | `["review"]` | Revisar ✓ / Contestar ✗ |
| `"review"` | `can_contest=true` | `[]` | Revisar ✗ / Contestar ✗ |
| `"contestation"` | `can_contest=true` | `["contest"]` | Revisar ✗ / Contestar ✓ |
| `null` (outra parte age) | qualquer | `[]` | Ambos desabilitados + mensagem "Aguardando {authority}" |
| `null` + `locked=true` | qualquer | `[]` | Badge "Encerrado" |

**Defesa em profundidade**: a UI desabilita botões com base em `available_actions`, mas o endpoint de submit repete a verificação de permissão no servidor. O servidor nunca confia no estado calculado pelo cliente.

---

### Workflow como Motor de Contestação/Revisão

O ciclo de revisão/contestação é executado pelo Workflow API (Arc 4) como motor de estado. O YAML da skill define quantos rounds existem, timeouts e alçadas — sem lógica hardcoded no evaluation-api. Mudar o ciclo de um cliente = atualizar um YAML via `PUT /v1/skills/{id}`.

#### Ligação campanha → workflow skill

```
EvaluationCampaign.review_workflow_skill_id = "skill_revisao_simples_v1"
                                             | "skill_revisao_treplica_v1"
                                             | qualquer skill configurada pelo cliente
```

#### Ciclo de vida completo

```
1. EvaluationResult submetido
   → evaluation-api: POST /v1/workflow/trigger
     { flow_id: campaign.review_workflow_skill_id,
       origin_session_id: result.session_id,
       context: { result_id, campaign_id, tenant_id } }
   → workflow entra no primeiro suspend (aguardar_revisao)
   → workflow.events consumer atualiza o result:
       action_required = "review", current_round = 1, deadline_at, resume_token

2. Usuário age na UI — endpoint ecoa o round recebido (anti-replay)
   POST /v1/evaluation/results/{id}/review   { decision, round: 1 }
   POST /v1/evaluation/contestations         { result_id, round: 1, reason }
   → evaluation-api verifica permissão (resolve_permissions)
   → verifica anti-replay: round_body == result.current_round ou rejeita 409
   → grava no banco (audit trail)
   → escreve no ContextStore:
       session.review_decision  = "approved" | "contested"
       session.reviewer_id      = caller.user_id
       session.round_echoed     = 1
   → POST /v1/workflow/resume { token: result.resume_token, decision: "input" }

3. Workflow lê @ctx.session.review_decision no choice step → transita
   → próximo suspend: escreve current_round incrementado no ContextStore
   → workflow.events consumer atualiza action_required, current_round, deadline_at, resume_token

4. Timeout: suspend expira sem retomada
   → workflow entra em on_timeout → congelar_resultado step
   → evaluation-api consumer: locked=true, lock_reason="review_timeout", action_required=null
   → qualquer chamada subsequente sobre o result_id retorna 409 Conflict — result locked
```

#### Padrão do round counter — controlado pelo workflow, ecoado pela UI

O workflow escreve `@ctx.session.current_round` ao entrar em cada suspend. A UI lê o valor recebido no result e o devolve no submit. O evaluation-api usa esse valor para o anti-replay check. O YAML é o único lugar com lógica de quantas voltas existem.

```yaml
# Fragmento — o workflow incrementa o próprio contador
- id: incrementar_round
  type: invoke
  tool: context_write
  input:
    tag: session.current_round
    value: "{{add(@ctx.session.current_round, 1)}}"
  on_success: verificar_limite

- id: verificar_limite
  type: choice
  conditions:
    - field: "@ctx.session.review_decision"
      operator: eq
      value: "approved"
      next: encerrar_aprovado
    - field: "@ctx.session.current_round"
      operator: gt
      value: 3              # tréplica: único lugar onde o limite existe
      next: congelar_resultado
    - field: "@ctx.session.review_decision"
      operator: eq
      value: "contested"
      next: aguardar_contestacao
```

Clientes com réplica configuram `value: 2`; tréplica, `value: 3` — sem nenhuma alteração de código.

#### ContextStore keys usadas pelo motor de avaliação

| Tag | Valor | Escrito por |
|---|---|---|
| `session.current_round` | `number` | Workflow (ao entrar no suspend) |
| `session.action_required` | `"review" \| "contestation"` | Workflow (ao entrar no suspend) |
| `session.review_decision` | `"approved" \| "contested"` | evaluation-api (antes do resume) |
| `session.reviewer_id` | `user_id` | evaluation-api (antes do resume) |
| `session.round_echoed` | `number` | evaluation-api (confirmação do anti-replay) |

TTL: os campos de workflow de avaliação usam TTL de 7 dias (`604800s`) — diferente do TTL padrão de 4h do ContextStore — para suportar ciclos de revisão longos. Configurável via Config API namespace `evaluation` key `workflow_context_ttl_s`.

#### consumer `workflow.events` no evaluation-api

```python
async def on_workflow_event(event):
    result_id = event.get("context", {}).get("result_id")
    if not result_id:
        return

    if event["event_type"] == "workflow.suspended":
        step = event.get("suspended_at_step", "")
        action = "review" if "revisao" in step else "contestation" if "contestacao" in step else None
        await db.update_result_workflow_state(result_id,
            action_required  = action,
            current_round    = event["context"].get("current_round", 1),
            deadline_at      = event.get("resume_expires_at"),
            resume_token     = event.get("resume_token"),
        )

    elif event["event_type"] == "workflow.completed":
        lock_reason = event.get("context", {}).get("lock_reason", "completed")
        await db.update_result_workflow_state(result_id,
            action_required = None,
            resume_token    = None,
            locked          = True,
            lock_reason     = lock_reason,
        )
```

#### Exemplo de YAML para ciclo com tréplica

`packages/skill-flow-engine/skills/skill_revisao_treplica_v1.yaml`

```yaml
id: skill_revisao_treplica_v1
entry: init_round
steps:
  - id: init_round
    type: invoke
    tool: context_write
    input: { tag: session.current_round, value: 1 }
    on_success: aguardar_revisao

  - id: aguardar_revisao
    type: suspend
    reason: input
    timeout_hours: 48
    business_hours: true
    on_resume:  { next: verificar_decisao }
    on_timeout: { next: congelar_resultado }

  - id: verificar_decisao
    type: choice
    conditions:
      - field: "@ctx.session.review_decision"
        operator: eq
        value: "approved"
        next: encerrar_aprovado
      - field: "@ctx.session.review_decision"
        operator: eq
        value: "contested"
        next: incrementar_round

  - id: incrementar_round
    type: invoke
    tool: context_write
    input:
      tag: session.current_round
      value: "{{add(@ctx.session.current_round, 1)}}"
    on_success: verificar_limite

  - id: verificar_limite
    type: choice
    conditions:
      - field: "@ctx.session.current_round"
        operator: gt
        value: 3
        next: congelar_resultado
    default: aguardar_contestacao

  - id: aguardar_contestacao
    type: suspend
    reason: input
    timeout_hours: 72
    business_hours: true
    on_resume:  { next: aguardar_revisao }
    on_timeout: { next: congelar_resultado }

  - id: congelar_resultado
    type: invoke
    tool: evaluation_lock
    input:
      result_id:   "@ctx.session.result_id"
      lock_reason: "review_timeout"
    on_success: encerrar
    on_failure: encerrar

  - id: encerrar_aprovado
    type: complete
    outcome: resolved

  - id: encerrar
    type: complete
    outcome: resolved
```

#### Novos campos PostgreSQL em `evaluation_results`

```sql
ALTER TABLE evaluation_results
  ADD COLUMN workflow_instance_id UUID,
  ADD COLUMN resume_token         TEXT,
  ADD COLUMN action_required      TEXT CHECK (action_required IN ('review', 'contestation')),
  ADD COLUMN current_round        INT  NOT NULL DEFAULT 0,
  ADD COLUMN deadline_at          TIMESTAMPTZ,
  ADD COLUMN lock_reason          TEXT;

ALTER TABLE evaluation_contestations
  ADD COLUMN round_number     INT  NOT NULL DEFAULT 1,
  ADD COLUMN authority_level  TEXT;
```

#### Novos campos Kafka `evaluation.events`

```json
{
  "event_type": "submitted | reviewed | contested | locked",
  "round_number": 1,
  "authority_level": "supervisor",
  "lock_reason": "review_timeout | max_rounds_reached | manual",
  ...
}
```

---

### session-replayer — extensões Arc 6

O `ReplayContext` foi estendido com campos de avaliação:

```python
@dataclass
class ReplayContext:
    # ... campos existentes ...
    evaluation_form:     dict | None     # formulário associado pela campanha
    campaign_context:    dict | None     # metadados da campanha (sampling, reviewer_rules)
    knowledge_snippets:  list[dict]      # top-K snippets do namespace do formulário
```

O Replayer busca `evaluation_form` e `campaign_context` via evaluation-api ao construir o `ReplayContext` quando `evaluation_instance_id` está presente no evento `evaluation.requested`.

### MCP tools — extensões Arc 6

#### evaluation_context_get (estendido)

Retorna `ReplayContext` enriquecido com `evaluation_form`, `campaign_context` e `knowledge_snippets`. O agente avaliador vê os critérios do formulário e os snippets de conhecimento relevantes num único call.

#### evaluation_submit (estendido)

```typescript
// Input Arc 6 (estendido)
{
  result_id?:           string      // novo resultado ou update de rascunho
  instance_id:          string
  session_id:           string
  form_id:              string
  campaign_id?:         string
  criterion_responses:  EvaluationCriterionResponse[]
  overall_score:        number
  eval_status:          string
  compliance_flags?:    string[]
  review_note?:         string
  reviewed_by?:         string
  // Comparison Mode (Arc 3 — mantido)
  comparison_turns?:    ComparisonTurn[]
  comparison_replay_outcome?:   string
  comparison_replay_sentiment?: number
}
```

### platform-ui — módulo de avaliação

6 páginas sob `/evaluation`:

| Rota | Arquivo | Roles | Descrição |
|---|---|---|---|
| `/evaluation/forms` | `FormsPage.tsx` | admin | CRUD de formulários com critérios |
| `/evaluation/campaigns` | `CampaignsPage.tsx` | supervisor, admin | Campanhas + KPIs em tempo real |
| `/evaluation/knowledge` | `KnowledgePage.tsx` | admin | Base de conhecimento vetorial |
| `/evaluation/avaliacoes` | `AvaliacoesPage.tsx` | operator, supervisor, admin | Tabela unificada: todas as avaliações, filtros completos, drill-down, ações disponíveis via ABAC |
| `/evaluation/reports` | `ReportsPage.tsx` | supervisor+ | Dashboard analítico (analytics-api) |

Nav group "Avaliação" adicionado ao `Sidebar.tsx`.

**`src/api/evaluation-hooks.ts`** — hooks de API completos:

| Hook / Função | Endpoint | Descrição |
|---|---|---|
| `useForms(tenantId)` | `GET /v1/evaluation/forms` | Lista formulários |
| `createForm`, `updateForm`, `deleteForm` | POST/PATCH/DELETE | CRUD |
| `useCampaigns(tenantId, pollMs)` | `GET /v1/evaluation/campaigns` | Lista campanhas (polling) |
| `createCampaign`, `pauseCampaign`, `resumeCampaign` | POST | Ações de campanha |
| `useInstances(campaignId, status, pollMs)` | `GET /v1/evaluation/instances` | Instâncias por campanha |
| `useResults(tenantId, campaignId, evaluatorId, pollMs)` | `GET /v1/evaluation/results` | Resultados |
| `reviewResult(resultId, body)` | `POST /v1/evaluation/results/{id}/review` | Revisão humana |
| `useContestations(tenantId, resultId)` | `GET /v1/evaluation/contestations` | Contestações |
| `createContestation`, `adjudicateContestation` | POST | Ações de contestação |
| `useCampaignReport(campaignId)` | `GET /v1/evaluation/reports/campaigns/{id}` | Relatório por campanha |
| `useAgentReport(tenantId, poolId)` | `GET /v1/evaluation/reports/agents` | Relatório por agente |
| `searchKnowledge(tenantId, query, namespace, topK)` | `GET /v1/knowledge/search` | Busca RAG |
| `upsertSnippet`, `deleteSnippet` | POST/DELETE | CRUD de snippets |
| `useEvaluationsAnalytics(tenantId, params, pollMs)` | `GET /reports/evaluations` | analytics-api ClickHouse |
| `useEvaluationsSummary(tenantId, params, pollMs)` | `GET /reports/evaluations/summary` | Sumário agregado |

Endpoints `/v1/evaluation` e `/v1/knowledge` proxied pelo Vite para porta 3400; `/reports` proxied para porta 3500 (analytics-api).

### analytics-api — ClickHouse Arc 6

#### Tabelas

```sql
-- Estado atual de cada resultado (ReplacingMergeTree — latest eval_status wins)
CREATE TABLE analytics.evaluation_results (
    result_id        String,
    instance_id      String,
    session_id       String,
    tenant_id        String,
    evaluator_id     String,
    form_id          String,
    campaign_id      Nullable(String),
    overall_score    Float64,
    eval_status      String,
    locked           UInt8,
    compliance_flags Array(String),
    timestamp        DateTime,
    ingested_at      DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(ingested_at)
  ORDER BY (tenant_id, result_id);

-- Log append-only de eventos (submitted/reviewed/contested/locked)
CREATE TABLE analytics.evaluation_events (
    event_id      String,
    result_id     String,
    session_id    String,
    tenant_id     String,
    event_type    String,    -- "submitted" | "reviewed" | "contested" | "locked"
    actor_id      String,    -- evaluator_id / reviewed_by / contested_by
    eval_status   String,
    overall_score Nullable(Float64),
    timestamp     DateTime,
    ingested_at   DateTime DEFAULT now()
) ENGINE = MergeTree()
  ORDER BY (tenant_id, result_id, timestamp);
```

#### Kafka consumer

Tópico `evaluation.events` adicionado a `_TOPICS` e `_PARSERS` em `consumer.py`.

`parse_evaluation_event(msg)` retorna dois rows por evento:
- `{"table": "evaluation_results", ...}` — estado atual do resultado (upsert)
- `{"table": "evaluation_events", ...}` — entrada do log de auditoria

`_write_row` despacha via `store.upsert_evaluation_result()` e `store.insert_evaluation_event()`.

#### Endpoints analytics

| Endpoint | Filtros | Descrição |
|---|---|---|
| `GET /reports/evaluations` | `tenant_id`, `from_dt`, `to_dt`, `campaign_id`, `form_id`, `evaluator_id`, `eval_status`, `page`, `page_size`, `format` | Linhas individuais de `evaluation_results FINAL` |
| `GET /reports/evaluations/summary` | `tenant_id`, `from_dt`, `to_dt`, `campaign_id`, `form_id`, `group_by` | Agregação por `campaign_id` / `evaluator_id` / `form_id` / `date` |

**Campos do sumário:** `total_evaluated`, `count_submitted`, `count_approved`, `count_rejected`, `count_contested`, `count_locked`, `count_locked_flag`, `avg_score`, `min_score`, `max_score`, `score_excellent (≥0.9)`, `score_good (0.7–0.9)`, `score_fair (0.5–0.7)`, `score_poor (<0.5)`, `with_compliance_flags`.

**Proteção SQL injection:** `group_by` validado contra whitelist `{"campaign_id", "evaluator_id", "form_id", "date"}` antes de injetar na cláusula GROUP BY. Valores inválidos retornam ao default `campaign_id`.

### Kafka topics

| Topic | Producer | Consumer(s) |
|---|---|---|
| `evaluation.events` | evaluation-api (result submit + review + contestation + lock) | analytics-api → ClickHouse `evaluation_results` + `evaluation_events` |

**Payload `evaluation.events`:**
```json
{
  "event_type":        "submitted" | "reviewed" | "contested" | "locked",
  "result_id":         "uuid",
  "instance_id":       "uuid",
  "session_id":        "sess_...",
  "tenant_id":         "tenant_demo",
  "evaluator_id":      "agente_avaliacao_v1-001",
  "form_id":           "form_sac_padrao",
  "campaign_id":       "camp_...",
  "overall_score":     0.87,
  "eval_status":       "approved",
  "locked":            false,
  "compliance_flags":  [],
  "reviewed_by":       null,
  "contested_by":      null,
  "timestamp":         "ISO8601"
}
```

### Vite proxies adicionados (platform-ui)

| Prefixo | Target | Porta |
|---|---|---|
| `^/v1/evaluation` | evaluation-api | 3400 |
| `^/v1/knowledge` | mcp-server-knowledge | 3401 |

### Repository additions

```
plughub/
  packages/
    evaluation-api/               ← plughub-evaluation-api (Python FastAPI — porta 3400)
    mcp-server-knowledge/         ← mcp-server-knowledge (TypeScript MCP Server)
  packages/platform-ui/src/
    modules/evaluation/           ← 5 páginas: FormsPage, CampaignsPage, KnowledgePage,
    │                                          AvaliacoesPage (tabela unificada), ReportsPage
    api/evaluation-hooks.ts       ← hooks completos (evaluation-api + analytics-api)
```

### Tests

- `analytics-api/tests/test_consumer.py`: `TestParseEvaluationEvent` (14 assertions), `TestWriteRowDispatchEvaluation` (2 assertions)
- `analytics-api/tests/test_reports.py`: `TestQueryEvaluationsReport` (4 assertions), `TestQueryEvaluationsSummary` (4 assertions)
- Total analytics-api: **108/108**

### Arc 6 — Tests

- `e2e-tests/scenarios/24_evaluation_campaign.ts` — 14 assertions (--evaluation flag)
- `e2e-tests/scenarios/25_evaluation_contestation.ts` — 10 assertions (--contestation flag)
- `e2e-tests/scenarios/26_ai_gateway_fallback.ts` — 10 assertions (--fallback flag; inference parts require ANTHROPIC_API_KEY)

### Arc 6 v2 — ✅ Implementado (Permissões 2D + Workflow Motor)

Todos os componentes abaixo foram implementados:

- ~~`evaluation_permissions` table + endpoints~~ → **removido**: permissões unificadas no ABAC (`module_config.evaluation.revisar` / `module_config.evaluation.contestar`); tabela dropada no DDL startup
- ✅ `_check_abac_permission(jwt_payload, field, pool_id)` em `router.py` — substitui `resolve_permissions()`; graceful degradation para tokens legacy sem `module_config`
- ✅ `EvaluationCampaign`: campos `review_workflow_skill_id` + `contestation_policy` (DDL + schema update em `db.py`)
- ✅ `EvaluationResult`: campos `workflow_instance_id`, `resume_token`, `action_required`, `current_round`, `deadline_at`, `lock_reason` (DDL em `db.py`)
- ✅ `EvaluationContestation`: campos `round_number`, `authority_level` (DDL em `db.py`)
- ✅ `GET /v1/evaluation/results/{id}` — `available_actions` computado server-side via `_compute_available_actions(result, jwt_payload, pool_id)` (ABAC; Bearer opcional)
- ✅ `POST /v1/evaluation/results/{id}/review` — JWT decode, `_check_abac_permission(…, "revisar", pool_id)`, anti-replay de `round`, ContextStore write, workflow resume
- ✅ `POST /v1/evaluation/contestations` — JWT decode, `_check_abac_permission(…, "contestar", pool_id)`, anti-replay de `round`, ContextStore write (`session.review_decision = "contested"`), workflow resume
- ✅ Consumer `workflow.events` no `evaluation-api/main.py` — `_on_workflow_event()`: atualiza `action_required`, `current_round`, `deadline_at`, `resume_token`, `locked`, `lock_reason` via `update_result_workflow_state()` e `lock_result()`
- ✅ Trigger de workflow ao submeter resultado: `POST /v1/workflow/trigger` com `flow_id = campaign.review_workflow_skill_id`
- ✅ `packages/skill-flow-engine/skills/skill_revisao_simples_v1.yaml` — ciclo simples (1 round, 6 steps)
- ✅ `packages/skill-flow-engine/skills/skill_revisao_treplica_v1.yaml` — tréplica (até 3 rounds, 10 steps); alterar `value: 3` para `value: 2` para réplica
- ✅ MCP tool `evaluation_lock` em `mcp-server-plughub/src/tools/evaluation.ts` — idempotente: 409 = já locked (tratado como sucesso)
- ✅ ContextStore TTL 7 dias: Config API namespace `evaluation` key `workflow_context_ttl_s = 604800` + 4 keys adicionais (`default_review_skill_id`, `review_deadline_hours`, `contestation_deadline_hours`, `auto_lock_on_workflow_complete`)
- ~~platform-ui: `EvaluationPermissionsPage.tsx`~~ → **removido**: permissões de avaliação configuradas na tela de usuários (AccessPage) via `ModulePermissionForm`
- ~~platform-ui: `ReviewPage.tsx` + `MyEvaluationsPage.tsx`~~ → **unificados** em `AvaliacoesPage.tsx` (`/evaluation/avaliacoes`): tabela com filtros completos (status, campanha, "Aguardando minha ação"), drill-down lateral, `available_actions` server-side via Bearer JWT + ABAC
- ✅ E2E scenarios 27/28: `27_evaluation_permissions.ts` (11 assertions, `--permissions`) + `28_evaluation_workflow_cycle.ts` (11 assertions, `--workflow-review`)

### Arc 6 v2 — Tests

- `e2e-tests/scenarios/27_evaluation_permissions.ts` — 11 assertions (--permissions flag): grant campaign/pool/global, list, update, resolve via available_actions, UNIQUE idempotency, revoke
- `e2e-tests/scenarios/28_evaluation_workflow_cycle.ts` — 11 assertions (--workflow-review flag; requires JWT_SECRET + workflow-api): submit → trigger → suspended → anti-replay 409 → review → ContextStore → workflow.completed → locked

**Invariantes desta arquitetura (nunca violar):**
- Nunca computar `available_actions` no cliente — sempre vem do servidor
- Nunca pular a verificação de `round` no submit — `round_body != result.current_round` → 409
- Nunca escrever `resume_token` em logs — é um segredo de retomada do workflow
- Nunca modificar resultado com `locked=true` — qualquer tentativa retorna 409
- Nunca fazer `workflow/resume` sem antes gravar `session.review_decision` no ContextStore — o choice step do workflow depende desse valor
- O YAML da skill é o único lugar com lógica de quantos rounds existem — nunca hardcodar `max_rounds` no evaluation-api

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

Tests: `test_router.py` — 48 assertions (TestTrigger, TestPersistSuspend, TestResume, TestComplete, TestFail, TestCancel, TestList, TestDetail, TestHealth, TestTimeoutScanner, TestWebhookCRUD, TestWebhookTrigger, TestWebhookDeliveries).

### Webhook Trigger — authenticated public endpoints

Permite que sistemas externos (Salesforce, ERP, etc.) disparem workflows via URL pública autenticada por token, substituindo o trigger manual do operador.

#### Token format

```
plughub_wh_<url-safe-43-chars>    (~258 bits de entropia)
```

Armazenamento: **SHA-256 hex digest** em `workflow.webhooks.token_hash` — plain token nunca é persistido.
`token_prefix` (16 primeiros chars) é armazenado para exibição no admin UI.
Comparação: `hmac.compare_digest` para proteção contra timing attacks.

#### PostgreSQL schema

```sql
-- Webhooks registrados (um por flow/tenant)
CREATE TABLE workflow.webhooks (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         TEXT        NOT NULL,
    flow_id           TEXT        NOT NULL,
    description       TEXT        NOT NULL DEFAULT '',
    token_hash        TEXT        NOT NULL UNIQUE,
    token_prefix      TEXT        NOT NULL,
    active            BOOL        NOT NULL DEFAULT TRUE,
    trigger_count     BIGINT      NOT NULL DEFAULT 0,
    last_triggered_at TIMESTAMPTZ,
    context_override  JSONB       NOT NULL DEFAULT '{}',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Log append-only de disparos (auditoria)
CREATE TABLE workflow.webhook_deliveries (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    webhook_id   UUID        NOT NULL REFERENCES workflow.webhooks(id) ON DELETE CASCADE,
    tenant_id    TEXT        NOT NULL,
    triggered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status_code  INT         NOT NULL,
    payload_hash TEXT        NOT NULL,
    instance_id  UUID,
    error        TEXT,
    latency_ms   INT
);
```

#### Endpoints

| Método | Rota | Auth | Descrição |
|--------|------|------|-----------|
| `POST`   | `/v1/workflow/webhooks`                         | `X-Admin-Token` | Cria webhook; retorna plain token (exibido uma vez) |
| `GET`    | `/v1/workflow/webhooks`                         | `X-Admin-Token` | Lista webhooks do tenant (filtros: `active`, `limit`, `offset`) |
| `GET`    | `/v1/workflow/webhooks/{id}`                    | `X-Admin-Token` | Detalhe do webhook |
| `PATCH`  | `/v1/workflow/webhooks/{id}`                    | `X-Admin-Token` | Atualiza `description`, `active`, `context_override` |
| `POST`   | `/v1/workflow/webhooks/{id}/rotate`             | `X-Admin-Token` | Rotaciona token (invalida o anterior; retorna novo plain token) |
| `DELETE` | `/v1/workflow/webhooks/{id}`                    | `X-Admin-Token` | Remove webhook e cascade-deletes deliveries |
| `GET`    | `/v1/workflow/webhooks/{id}/deliveries`         | `X-Admin-Token` | Últimos N registros de entrega (padrão 50, máx 200) |
| `POST`   | `/v1/workflow/webhook/{id}`                     | `X-Webhook-Token` (plain) | **Público** — dispara workflow |

#### Trigger flow (POST /v1/workflow/webhook/{id})

```
1. Lê raw body → SHA-256 payload_hash
2. Autentica: SHA-256(X-Webhook-Token) → lookup DB por token_hash
3. verify_token(plain, stored_hash) — constant-time guard extra
4. Verifica active; se inativo → db_record_delivery(403) + 403
5. Merge: context = {**webhook.context_override, **body_json}
6. db_create_instance + emit_started (mesmo que trigger manual)
7. db_record_delivery(202, instance_id, latency_ms)
8. trigger_count++, last_triggered_at = now() (atômico, 2xx only)
9. Retorna 202 { instance_id, flow_id, webhook_id, status: "accepted" }
```

#### Arquivos

| Arquivo | Responsabilidade |
|---------|-----------------|
| `webhooks.py` | `generate_token()`, `_hash_token()`, `verify_token()` — utilitários de token |
| `db.py` | DDL + CRUD: `db_create_webhook`, `db_get_webhook`, `db_get_webhook_by_token_hash`, `db_list_webhooks`, `db_update_webhook`, `db_rotate_webhook_token`, `db_delete_webhook`, `db_record_delivery`, `db_list_deliveries` |
| `router.py` | 7 endpoints admin + 1 endpoint público; `_require_admin` Dependency |
| `tests/test_router.py` | `TestWebhookCRUD` (11), `TestWebhookTrigger` (5), `TestWebhookDeliveries` (4) |

### Operator Console — WebhookPanel

`packages/operator-console/src/components/WebhookPanel.tsx` — gestão completa de webhook triggers:

- **Sidebar esquerda**: lista de webhooks com nome, flow_id, status ativo/inativo e contagem de triggers; campo de admin token local; botão "New Webhook" abre formulário de criação.
- **Formulário de criação**: flow_id, description, context_override (JSON textarea com validação inline) — chama `POST /v1/workflow/webhooks`, exibe plain token UMA vez via `CopyBox` com dismiss obrigatório.
- **Detalhe de webhook**: grid de metadados (flow_id, description, token_prefix, trigger_count, last_triggered_at); URL pública copiável; ações — ativar/desativar, rotate token (exige confirmação), delete (exige confirmação).
- **Delivery log**: tabela das últimas 20 entregas com timestamp, status HTTP (colorido por faixa), latency_ms, instance_id e error message.
- **CopyBox**: componente de exibição one-time do plain token com botão copy-to-clipboard e botão "I've saved it" para dismiss — implementa o requisito de segurança de exibição única.

Hooks: `packages/operator-console/src/api/webhook-hooks.ts` — `useWebhooks` (poll 15s), `useWebhookDeliveries` (poll on mount), `createWebhook`, `patchWebhook`, `rotateWebhookToken`, `deleteWebhook`. Todos usam `VITE_WORKFLOW_API_BASE_URL ?? ''` — proxied pelo Vite via `/v1/workflow` → `http://localhost:3800`.

Nav: botão "Webhooks" (indigo — `#6366f1`/`#1e1b4b`) adicionado ao `Header.tsx`.

Build: 234 kB JS / 67 kB gzip.

### Operator Console — RegistryPanel

`packages/operator-console/src/components/RegistryPanel.tsx` — gestão de recursos do Agent Registry com quatro tabs:

- **Pools**: lista de pools com status, channels e SLA; formulário de criação (pool_id, channel_types, sla_target_ms, description); edição inline de SLA, channels e descrição.
- **Agent Types**: lista com framework, role e pools vinculados; formulário de criação (agent_type_id, framework, execution_model, role, max_concurrent_sessions, pools, skills, prompt_id); soft-delete (→ deprecated).
- **Skills**: lista read-mostly com tipo de classificação e versão; detail view com tools, domains, description; delete com confirmação. Skills são gerenciadas por YAML — o painel exibe o estado atual do banco.
- **Running**: lista read-only de instâncias ativas filtráveis por pool_id; atualização automática a cada 15s.

Hooks: `packages/operator-console/src/api/registry-hooks.ts` — `usePools`, `createPool`, `updatePool`, `useAgentTypes`, `createAgentType`, `deleteAgentType`, `useSkills`, `deleteSkill`, `useInstances`. Todos usam `x-tenant-id` + `x-user-id: operator` headers.

Proxy Vite: `/v1/pools`, `/v1/agent-types`, `/v1/skills`, `/v1/instances` → `http://localhost:3300`.

Nav: botão "Registry" (orange — `#f97316`/`#431407`) adicionado ao `Header.tsx`.

Build: 260 kB JS / 72 kB gzip.

### Operator Console — SkillFlowEditor

`packages/operator-console/src/components/SkillFlowEditor.tsx` — Monaco-based YAML editor para skills:

- **Left sidebar**: lista de skills com busca por skill_id/name; tipo de classificação e versão; botão "+ New Skill" abre prompt de criação com ID.
- **Editor principal**: Monaco com `defaultLanguage="yaml"`, `theme="vs-dark"`, `wordWrap`, `bracketPairColorization`. Validação YAML live em tempo real (parse errors mostrados na status bar).
- **Conversão JSON↔YAML**: skills são armazenadas como JSON na API e exibidas como YAML para legibilidade. `js-yaml.dump()` ao carregar; `js-yaml.load()` ao salvar.
- **Status bar**: mostra estado (`loading`, `saving`, `saved`, `error`, `parse_error`) com cor por estado.
- **Ações**: ⌘S / botão Save (`PUT /v1/skills/:id`), Discard (reverte ao estado salvo), Delete (com confirmação), modificado indicado por `●` no header.
- **Blank template**: novo skill inicia com YAML de exemplo completo (skill_id, name, version, description, classification, comentários para flow).

Hooks adicionados a `registry-hooks.ts`: `fetchSkill(tenantId, skillId)` (GET), `upsertSkill(tenantId, skillId, body)` (PUT).

Nav: botão "Skills" (violet — `#a78bfa`/`#2e1065`) adicionado ao `Header.tsx`.

Deps adicionadas ao `package.json`: `@monaco-editor/react@^4.7.0`, `js-yaml@^4.1.1`, `@types/js-yaml@^4.0.9`.

Build: 325 kB JS / 94 kB gzip.

### Operator Console — ChannelPanel

`packages/operator-console/src/components/ChannelPanel.tsx` — channel credential management for all supported channel types:

- **Left sidebar**: grouped by channel type (WhatsApp, Webchat, Voice, Email, SMS, Instagram, Telegram, WebRTC) with emoji icon, config count badge, and per-channel "+ Add" button.
- **CreateForm**: channel-aware form with per-channel `fields` (sensitive credentials rendered as `<input type="password">`) and `settingFields` (non-sensitive settings in 2-column grid). Toggle for active status.
- **ConfigDetail**: shows current masked values (`••••••`) for each credential field alongside new-value inputs; only fields with non-empty new values are included in PUT payload. Settings are editable in-place. `●` indicator for unsaved changes. Three-stage delete: Delete → Confirm → execute.
- **Accent**: teal (`#14b8a6` / `#042f2e`).
- **ToggleSwitch**: inline active/inactive toggle used in both create and detail forms.

Channel-specific credential templates:

| Channel | Credential fields | Setting fields |
|---------|-------------------|----------------|
| WhatsApp | access_token, phone_number_id, waba_id, webhook_verify_token | api_version, webhook_path |
| Webchat | jwt_secret | ws_auth_timeout_s, attachment_expiry_days, serving_base_url, cors_origins |
| Voice | api_key, api_secret, account_sid | inbound_number, provider, region |
| Email | smtp_password, api_key | smtp_host, smtp_port, from_address, from_name, provider |
| SMS | api_key, api_secret | sender_id, provider |
| Instagram | access_token, app_secret, webhook_verify_token | page_id, api_version |
| Telegram | bot_token | webhook_path, bot_username |
| WebRTC | turn_secret | stun_url, turn_url, turn_username |

Backend: `packages/agent-registry/src/routes/channels.ts` — CRUD for `gateway_configs` table. `_maskCredentials()` replaces all credential values with `••••••` before returning. Triggers `registry.changed` on create/update/delete.

**Backend model:**
```prisma
model GatewayConfig {
  id           String   @id @default(uuid())
  tenant_id    String
  channel      String   // whatsapp | webchat | voice | email | sms | instagram | telegram | webrtc
  display_name String
  active       Boolean  @default(true)
  credentials  Json     @default("{}")   // masked on API read
  settings     Json     @default("{}")
  created_at   DateTime @default(now())
  updated_at   DateTime @updatedAt
  created_by   String   @default("operator")
  @@index([tenant_id])
  @@index([tenant_id, channel])
  @@map("gateway_configs")
}
```

Migration: `packages/agent-registry/prisma/migrations/20260427000000_add_gateway_configs/migration.sql`

Type shim: `packages/agent-registry/src/types/gateway-config.ts` — `GatewayConfigDelegate` interface used until `prisma generate` can run in a network-connected environment.

Hooks: `packages/operator-console/src/api/channel-hooks.ts` — `useChannels`, `createChannel`, `updateChannel`, `deleteChannel`. Proxied via Vite `/v1/channels` → `http://localhost:3300`.

Nav: botão "Channels" (teal — `#14b8a6`/`#042f2e`) adicionado ao `Header.tsx`.

Build: 345 kB JS / 97 kB gzip (estimated).

### Operator Console — HumanAgentPanel

`packages/operator-console/src/components/HumanAgentPanel.tsx` — human agent lifecycle management with two tabs:

- **Live Status tab** (`LiveTab`): table of all running human agent instances (`framework=human`), polling every 10 s. Status filter bar (All / Ready / Busy / Paused). `StatusBadge` renders colored pill per status. `ActionButtons` shows contextual actions:
  - Ready → Pause
  - Busy → Pause, Force Logout
  - Paused → Resume, Force Logout
  - PATCH `/v1/instances/:id` with `{ action: pause | resume | force_logout }`.
- **Profiles tab** (`ProfilesTab`): sidebar (280 px) listing active and deprecated `AgentType` records filtered client-side for `framework === 'human'`. Selecting a profile opens `ProfileDetail`; "+ New Profile" opens `CreateProfileForm`.
  - `CreateProfileForm`: `agent_type_id`, `role` select (primary/specialist/supervisor), `max_concurrent_sessions`, pool chip multi-select via comma-delimited input, permissions textarea. POSTs to `/v1/agent-types` with `framework: 'human'`, `execution_model: 'stateful'`.
  - `ProfileDetail`: read-only pool chips, editable `max_concurrent_sessions` and `permissions`. PUT via `/v1/agent-types/:id`. Three-stage Deprecate flow (Deprecate → Confirm → DELETE).
- **Accent**: emerald (`#10b981` / `#022c22`).

Backend changes (instances.ts):
- Added optional `framework` query param to `GET /v1/instances` — filters via nested `agent_type: { framework }` Prisma relation.
- Added `GET /v1/instances/:instance_id` detail endpoint (full `agent_type` join including pools).
- Added `PATCH /v1/instances/:instance_id` endpoint for operator actions; maps `pause → paused`, `resume → ready`, `force_logout → logout`; publishes `registry.changed`.

Hooks: `packages/operator-console/src/api/human-agent-hooks.ts` — `useHumanInstances` (poll 10 s), `instanceAction`, `useHumanAgentTypes`, `createHumanAgent`, `updateHumanAgent`, `deprecateHumanAgent`. Reuses existing Vite proxies `/v1/instances` and `/v1/agent-types` → `http://localhost:3300`.

Nav: botão "Agents" (emerald — `#10b981`/`#022c22`) adicionado ao `Header.tsx`.

Build: 360 kB JS / 101 kB gzip (estimated).

### Status transitions

### Kafka topic: workflow.events

Publicado pelo workflow-api em todos os status transitions. Consumido pelo Skill Flow worker para disparar `engine.run()` com `resumeContext`.

### Implementado neste módulo

- `packages/skill-flow-worker/` — TypeScript worker: consome `workflow.events`, roda engine.run() com resumeContext, wired com persistSuspend callback para deadline calculation
- Operator Console — painel de instâncias Workflow (WorkflowPanel.tsx): status filter, timeline, resume token, cancel action
- Operator Console — WebhookPanel (WebhookPanel.tsx): CRUD de webhooks, delivery log, one-time token display, activate/deactivate/rotate/delete
- Operator Console — RegistryPanel (RegistryPanel.tsx): Pools / Agent Types / Skills / Running instances CRUD via agent-registry REST
- Operator Console — SkillFlowEditor (SkillFlowEditor.tsx): Monaco YAML editor for SkillFlow definitions, live validation, JSON↔YAML conversion
- Operator Console — ChannelPanel (ChannelPanel.tsx): channel credential management for WhatsApp, Webchat, Voice, Email, SMS, Instagram, Telegram, WebRTC; credentials masked on read
- Operator Console — HumanAgentPanel (HumanAgentPanel.tsx): Live Status tab (human instances, operator actions) + Profiles tab (AgentType CRUD for human framework)
- agent-registry — GatewayConfig model + migration + `routes/channels.ts` CRUD (`GET/POST /v1/channels`, `GET/PUT/DELETE /v1/channels/:id`)
- agent-registry — `GET /v1/instances?framework=human`, `GET /v1/instances/:id` detail, `PATCH /v1/instances/:id` operator actions (pause/resume/force_logout)
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

## Agent Assist UI — `packages/platform-ui/src/modules/agent-assist/` (task #172)

**Migrated from `packages/agent-assist-ui/` to platform-ui shell.** Route: `/agent-assist` (roles: operator, supervisor, admin). agentName from `useAuth()` session.name; poolId from `?pool=` URL param with inline picker when absent. Uses `h-full` instead of `h-screen` (Shell provides the outer container).

Vite proxies added: `'^/api'` → `http://localhost:3100`, `'^/agent-ws'` → `ws://localhost:3100` (ws: true).

New dependency: `recharts@^2.x` (used by EstadoTab sentiment line chart).

**Module structure:**
```
modules/agent-assist/
  AgentAssistPage.tsx          ← main page (adapts App.tsx)
  types.ts                     ← all type definitions
  hooks/
    useAgentWebSocket.ts       ← persistent WS, reconnect, heartbeat
    useSupervisorState.ts      ← polls /api/supervisor_state/{sessionId}
    useSupervisorCapabilities.ts
    useCustomerHistory.ts      ← GET /analytics/sessions/customer/{id}
  components/
    Header.tsx                 ← handle-time, SLA bar, WS dot
    ChatArea.tsx               ← messages + live sentiment strip
    AgentInput.tsx             ← textarea + Encerrar button
    CloseModal.tsx             ← issue_status + outcome + handoff_reason
    MessageBubble.tsx          ← per-author styles + MenuCard delegation
    MenuCard.tsx               ← read-only menu interaction preview
    ContactList.tsx            ← per-contact cards with sentiment/SLA/timer
    RightPanel.tsx             ← 4-tab container
    ToastContainer.tsx         ← fixed bottom-right notifications
    tabs/
      EstadoTab.tsx            ← sentiment chart (recharts), intent, flags, SLA
      CapacidadesTab.tsx       ← suggested agents + escalation options
      ContextoTab.tsx          ← ContextSnapshotCard (teal) + ContactContextCard (emerald)
      HistoricoTab.tsx         ← customer session history
```

**Legacy app** (`packages/agent-assist-ui/`, port 5175) — frozen, kept as reference.

React 18 + TypeScript + Vite. **Original** porta de dev: 5175. Proxy: `/api` → mcp-server-plughub (3100), `/agent-ws` → WS mcp-server (3100), `/analytics` → analytics-api (3500).

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
| 20 | `20_masked_form.ts` | Masked Form field-level masking policy: Part A — interaction:form, 3 fields (email plain, senha masked, codigo_2fa masked) → email survives pipeline_state, masked values absent, @masked.senha forwarded to invoke (6 assertions); Part B — step.masked=true with field.masked=false override: cpf survives (override wins), pin absent (inherits step.masked) (5 assertions) (--masked flag) |
| 21 | `21_masked_retry.ts` | Masked Retry begin_transaction rollback cycle: inject invalid PIN "000000" → validate_pin fails → rewind to tx_inicio (maskedScope cleared) → inject valid PIN "123456" → success; asserts both PINs absent from pipeline_state (5 assertions) (--masked flag) |
| 22 | `22_pool_hooks_fase_b.ts` | Pool Lifecycle Hooks Fase B + C: Part A — no-hooks pool → agent_done → conversations.outbound session.closed immediate, hook_pending absent (3 assertions); Part B — hooks pool → agent_done → hook_pending=1, conversations.inbound hook event with conference_id + hook_type=on_human_end + target pool, conversations.outbound NOT published within 2s (9 assertions); Part C — simulate hook completion via GETDEL+DECR → publish contact_closed → conversations.outbound session.closed arrives, human tracking keys cleaned (4 assertions); Part D — pool with on_human_end+post_human hooks → simulate on_human_end completion → bridge fires post_human → conversations.inbound hook_type=post_human + hook_pending:post_human=1 (5 assertions); Part E — publish participant_joined+left to conversations.participants → analytics-api consumer → ClickHouse participation_intervals → GET /reports/participation row with duration_ms (4 assertions) (--hooks flag, 60s timeout) |
| 23 | `23_contact_segments.ts` | Arc 5 ContactSegment analytics pipeline: Part A — publish participant_joined+left with segment_id (sequence_index=0, outcome=resolved) → analytics-api consumer → ClickHouse segments FINAL → GET /reports/segments row with correct segment_id, sequence_index=0, duration_ms, outcome (4 assertions); Part B — conference specialist topology: primary (segment_id=A, parent=null) + specialist (segment_id=B, parent=A) published → GET /reports/segments returns both rows, specialist has parent_segment_id=A, primary has sequence_index=0 (4 assertions); Part C — sequential handoff: two primary segments sequence_index=0 and sequence_index=1 → both rows distinguishable by sequence_index (3 assertions) (--segments flag, 60s timeout) |
| 24 | `24_evaluation_campaign.ts` | Arc 6 Evaluation Campaign pipeline: Part A — Form CRUD (POST/GET/PATCH /v1/evaluation/forms, 3 assertions); Part B — Campaign CRUD + pause/resume (4 assertions); Part C — Kafka evaluation.events (submitted) → analytics-api ClickHouse → GET /reports/evaluations row with result_id, overall_score=0.85, eval_status=submitted (4 assertions); Part D — approved event via Kafka → ReplacingMergeTree FINAL → eval_status=approved; GET /reports/evaluations/summary count_approved≥1 (3 assertions) (--evaluation flag, 60s timeout) |
| 25 | `25_evaluation_contestation.ts` | Arc 6 Contestation + human review: Part A — POST /v1/evaluation/results → submitted; reviewer approves (3 assertions); Part B — agent creates contestation, contestation appears in list, supervisor adjudicates upheld, result status consistent (4 assertions); Part C — final review with review_note, reviewed_by populated, analytics shows approved (3 assertions) (--contestation flag, 60s timeout) |
| 26 | `26_ai_gateway_fallback.ts` | AI Gateway multi-account fallback: Part A — Config API ai_gateway namespace accessible, analytics dashboard healthy, AI Gateway /health (3 assertions); Part B — throttle marker written to Redis for account_0, POST /v1/reason routes around throttled account, response arrives despite throttle, throttle key present (4 assertions); Part C — throttle cleared, key absent after clear, AI Gateway responds after recovery (3 assertions) (--fallback flag, requires ANTHROPIC_API_KEY for inference assertions, others gracefully skip) |
| 27 | `27_evaluation_permissions.ts` | Arc 6 v2 — 2D Permission Model: Part A — grant campaign/pool/global perms → list all three (4 assertions); Part B — PATCH flip can_review, GET reflects update (2 assertions); Part C — GET result with caller_user_id → available_actions, no-perm user gets [], UNIQUE constraint idempotency (3 assertions); Part D — DELETE campaign perm → only pool+global remain (2 assertions) (--permissions flag) |
| 28 | `28_evaluation_workflow_cycle.ts` | Arc 6 v2 — Workflow Review/Contestation Cycle: Part A — submit result + trigger skill_revisao_simples_v1 workflow (3 assertions); Part B — poll workflow.suspended → action_required=review, available_actions=["review"], deadline_at set (3 assertions); Part C — wrong round → 409 anti-replay, correct round + JWT → 200 (2 assertions); Part D — ContextStore review_decision=approved, workflow.completed, result locked=true (3 assertions) (--workflow-review flag, 90s timeout, requires JWT_SECRET + workflow-api) |
| R  | `regressions.ts` | regression suite: ZodError em session_context_get, parsing de callTool (--regression flag) |

Run with: `ts-node runner.ts --conference` or `ts-node runner.ts --only 06` or `ts-node runner.ts --only 12` or `ts-node runner.ts --webchat` or `ts-node runner.ts --workflow` or `ts-node runner.ts --only 13` or `ts-node runner.ts --collect` or `ts-node runner.ts --only 14` or `ts-node runner.ts --bootstrap` or `ts-node runner.ts --only 15` or `ts-node runner.ts --reconcile` or `ts-node runner.ts --only 16` or `ts-node runner.ts --ctx` or `ts-node runner.ts --only 17` or `ts-node runner.ts --worker` or `ts-node runner.ts --only 18` or `ts-node runner.ts --mention` or `ts-node runner.ts --only 19` or `ts-node runner.ts --masked` or `ts-node runner.ts --only 20` or `ts-node runner.ts --only 21` or `ts-node runner.ts --hooks` or `ts-node runner.ts --only 22` or `ts-node runner.ts --segments` or `ts-node runner.ts --only 23` or `ts-node runner.ts --evaluation` or `ts-node runner.ts --only 24` or `ts-node runner.ts --contestation` or `ts-node runner.ts --only 25` or `ts-node runner.ts --fallback` or `ts-node runner.ts --only 26` or `ts-node runner.ts --permissions` or `ts-node runner.ts --only 27` or `ts-node runner.ts --workflow-review` or `ts-node runner.ts --only 28`

Scenario 06 covers two parts:
- **Part A** — Conference happy path: primary agent busy → supervisor calls `agent_join_conference` → Redis `conference:*` keys verified → specialist `agent_done` with `conference_id` (session stays open) → primary `agent_done` closes session
- **Part B** — Reconnect resilience: agent busy → MCP transport torn down → new transport reconnected → re-login with same `instance_id` → Redis state (agent instance + active sessions) persists → `agent_done` with new session_token concludes session cleanly
