# Módulo: schemas (@plughub/schemas)

> Pacote: `schemas` (biblioteca)
> Runtime: TypeScript / Node 20+, Zod 3.23+
> Spec de referência: seções 3.4, 3.4a, 4.2, 4.5, 4.7

## O que é

O `@plughub/schemas` é a **fonte da verdade** para todos os contratos de dados da plataforma. Todos os pacotes que precisam de um tipo de domínio o importam daqui — nunca redefinem localmente.

É uma biblioteca TypeScript pura (sem runtime, sem servidor) que exporta schemas Zod e os tipos TypeScript inferidos a partir deles. Qualquer mudança de contrato começa aqui.

---

## Invariante central

> `schemas` **não depende de nenhum outro pacote interno**. É a raiz do grafo de dependências. Nunca adicionar imports de `sdk`, `mcp-server`, `routing-engine` ou qualquer outro módulo interno.

---

## Estrutura do Pacote

```
schemas/src/
  context-package.ts  ← ContextPackage, AgentDone, CustomerProfile, SessionItem
  skill.ts            ← Skill, SkillFlow, todos os 8 tipos de step
  agent-registry.ts   ← Pool, AgentType, PipelineState, RoutingDecision, TenantConfig
  index.ts            ← API pública — exports nomeados explícitos
```

---

## Grupo 1: Context Package (`context-package.ts`)

O `context_package` é o envelope de estado de uma sessão — construído progressivamente turno a turno no Redis e entregue ao agente no handoff.

### `ContextPackageSchema`

```typescript
ContextPackage {
  // Identificadores
  session_id:   UUID
  tenant_id:    string
  channel:      "chat" | "whatsapp" | "sms" | "voice" | "email" | "webrtc"

  // Cliente
  customer_data: {
    customer_id:        UUID
    tenant_id:          string
    tier:               "platinum" | "gold" | "standard"
    ltv?:               number (≥ 0)
    churn_risk?:        float [0.0, 1.0]
    preferred_channel?: Channel
  }

  // Conversa
  channel_context: {
    turn_count:      int ≥ 0
    started_at:      ISO datetime
    handoff_reason?: string
  }
  conversation_history: Array<{
    role:      "customer" | "agent" | "system"
    content:   string
    timestamp: ISO datetime
    agent_id?: string
  }>
  conversation_summary?:  string
  intent_history:         Array<{ intent, confidence, turn, timestamp }>
  sentiment_trajectory:   float[]       // [-1.0, 1.0] por turno
  attempted_resolutions:  string[]

  // Insights e pendências (modelo unificado spec 3.4a)
  conversation_insights: SessionItem[]  // insight.historico.* + insight.conversa.*
  pending_deliveries:    SessionItem[]  // outbound.*

  // BPM
  process_context?: { process_id?, process_instance?, status?, payload? }

  // Orquestrador nativo
  pipeline_state?: Record<string, unknown>

  schema_version: int ≥ 0  // default: 1
}
```

### `SessionItemSchema` — modelo unificado de insights e pendências (spec 3.4a)

`ConversationInsight` e `PendingDelivery` são **o mesmo modelo** (`SessionItem`), diferenciados pela `category`:

```typescript
SessionItem {
  item_id:            UUID
  customer_id:        UUID
  tenant_id:          string
  category:           string       // ex: "insight.conversa.servico.falha"
  content:            unknown      // estrutura livre definida pelo operator
  source:             string       // "crm" | "bpm" | "previous_agent" | etc.
  source_session_id?: UUID
  expires_at?:        ISO datetime
  priority:           int [0–100]  // default: 50
  status:             InsightStatus
  confidence?:        InsightConfidence  // apenas insight.conversa.*
  source_turn?:       int               // apenas insight.conversa.*
  registered_at?:     ISO datetime
}
```

**Prefixos de categoria reservados:**

| Prefixo | Tipo | Escopo |
|---|---|---|
| `insight.historico.*` | Memória de longo prazo | Carregada do Kafka no início da sessão |
| `insight.conversa.*` | Gerada na sessão atual | Expira no encerramento do contato |
| `outbound.*` | Pending delivery | Para entrega via Notification Agent |

**InsightStatus:** `pending`, `offered`, `accepted`, `delivered`, `consumed`, `expired`, `replaced`

**InsightConfidence:** `confirmed` (cliente confirmou), `inferred` (agente inferiu), `mentioned` (citado sem confirmar)

### `AgentDoneSchema` (spec 4.2)

Contrato de conclusão de qualquer agente. O refinement Zod valida que `handoff_reason` é **obrigatório** quando `outcome !== "resolved"`:

```typescript
AgentDone {
  session_id:             UUID
  agent_id:               string
  outcome:                "resolved" | "escalated_human" | "transferred_agent" | "callback"
  issue_status:           Issue[]   // mínimo 1 elemento — obrigatório
  resolution_summary?:    string
  context_package_final?: ContextPackage
  handoff_reason?:        string    // OBRIGATÓRIO quando outcome !== "resolved"
  pipeline_state?:        Record<string, unknown>
  conference_id?:         UUID
  participant_id?:        UUID
  completed_at:           ISO datetime
}

Issue {
  issue_id:    string
  description: string
  status:      "resolved" | "unresolved" | "transferred" | "pending_callback"
  resolved_at?: ISO datetime
}
```

---

## Grupo 2: Skill Registry (`skill.ts`)

### `SkillSchema`

```typescript
Skill {
  skill_id:    string  // regex: /^skill_[a-z0-9_]+_v\d+$/
  name:        string
  version:     string  // regex: /^\d+\.\d+$/ — ex: "2.0"
  description: string
  classification: {
    type:      "vertical" | "horizontal" | "orchestrator"
    vertical?: string   // ex: "telco", "finserv"
    domain?:   string   // ex: "portabilidade"
  }
  instruction: { prompt_id, language }  // language default: "pt-BR"
  tools:       SkillTool[]
  interface?:  { input_schema, output_schema }
  evaluation?: { template_id, criteria[], evaluate_independently }
  knowledge_domains: string[]
  compatibility?: { frameworks[], channels[] }
  flow?: SkillFlow   // obrigatório quando type === "orchestrator"
}
```

> **Refinement:** `classification.type === "orchestrator"` exige `flow` definido.

### `SkillFlowSchema`

```typescript
SkillFlow {
  entry: string      // deve referenciar um step existente (validado)
  steps: FlowStep[]  // mínimo 1; deve ter pelo menos um "complete" ou "escalate"
}
```

### Os 8 tipos de step (`FlowStepSchema`)

| Tipo | Campos principais |
|---|---|
| `task` | `target: { skill_id }`, `execution_mode: sync\|async`, `on_success`, `on_failure` |
| `choice` | `conditions: Condition[]` (JSONPath + operator + value + next), `default` |
| `catch` | `error_context` (step que falhou), `strategies[]`, `on_failure` |
| `escalate` | `target: { pool }`, `context: "pipeline_state"`, `error_reason?` |
| `complete` | `outcome: resolved\|escalated_human\|transferred_agent\|callback` |
| `invoke` | `target: { mcp_server, tool }`, `input?`, `output_as`, `on_success`, `on_failure` |
| `reason` | `prompt_id`, `input?`, `output_schema`, `output_as`, `max_format_retries [0–3]`, `on_success`, `on_failure` |
| `notify` | `message` (suporta `{{$.pipeline_state.*}}`), `channel`, `on_success`, `on_failure` |

**`CatchStrategy`** — discriminated union por `type`:
- `retry` → `max_attempts [1–5]`, `delay_ms`, `on_exhausted`
- `fallback` → `id`, `target: task|escalate`, `on_success`, `on_failure`

### `SkillRefSchema`

```typescript
SkillRef {
  skill_id:       string
  version_policy: "stable" | "latest" | "exact"   // default: "stable"
  exact_version?: string   // obrigatório quando version_policy === "exact"
}
```

---

## Grupo 3: Agent Registry (`agent-registry.ts`)

### `PoolRegistrationSchema`

```typescript
PoolRegistration {
  pool_id:                string  // regex: /^[a-z0-9_]+$/
  description?:           string
  channel_types:          Channel[]   // mínimo 1
  sla_target_ms:          int > 0
  routing_expression?:    RoutingExpression
  evaluation_template_id?: string
  supervisor_config?:     SupervisorConfig
}
```

**`RoutingExpression`** — pesos da fórmula de prioridade (spec 4.6):

```typescript
RoutingExpression {
  weight_sla:      float [0.0–2.0]  // default: 1.0
  weight_wait:     float [0.0–2.0]  // default: 0.8
  weight_tier:     float [0.0–2.0]  // default: 0.6
  weight_churn:    float [0.0–2.0]  // default: 0.9
  weight_business: float [0.0–2.0]  // default: 0.4
}
```

**`SupervisorConfig`**:

```typescript
SupervisorConfig {
  enabled:                    boolean
  history_window_days:        int [1–365]      // default: 30
  insight_categories:         string[]
  intent_capability_map:      Record<string, CapabilityEntry[]>
  sentiment_alert_threshold:  float [-1.0, 0.0]  // default: -0.30
  relevance_model?:           RelevanceModel
  proactive_delegation?:      ProactiveDelegation
}
```

### `AgentTypeRegistrationSchema`

```typescript
AgentTypeRegistration {
  agent_type_id:            string  // regex: /^[a-z][a-z0-9_]+_v\d+$/
  framework:                "langgraph" | "crewai" | "anthropic_sdk" | "azure_ai" |
                            "google_vertex" | "generic_mcp" | "human"
  execution_model:          "stateless" | "stateful"
  role:                     "executor" | "orchestrator"   // default: "executor"
  max_concurrent_sessions:  int ≥ 1                       // default: 1
  pools:                    string[]   // mínimo 1
  skills:                   SkillRef[] // obrigatório para role: "orchestrator"
  permissions:              string[]   // formato: "mcp-server-nome:tool_name"
  capabilities:             Record<string, string>
  agent_classification?:    { type: "vertical"|"horizontal", industry?, domain? }
  prompt_id?:               string | null  // null para agentes humanos
}
```

> **Refinement:** `role === "orchestrator"` exige `skills.length > 0`.

### `PipelineStateSchema` (spec 4.7 / 9.5i)

Estado do orquestrador — persistido no Redis por `skill-flow-engine`:

```typescript
PipelineState {
  flow_id:         string
  current_step_id: string
  status:          "in_progress" | "completed" | "failed"
  started_at:      ISO datetime
  updated_at:      ISO datetime
  results:         Record<string, unknown>  // chave = output_as do step
  retry_counters:  Record<string, int>      // chave = step id do catch
  error_context?:  { step_id, error, timestamp }
  transitions:     Array<{ from_step, to_step, reason, timestamp }>
  // reason: "on_success" | "on_failure" | "condition_match" | "default"
}
```

### `RoutingDecisionSchema` (spec 3.3)

```typescript
RoutingDecision {
  agent_type_id:     string   // agente primário alocado
  fallback?:         string   // agente de fallback
  mode:              "autonomous" | "hybrid" | "supervised"
  reevaluation_turn: int > 0 | null
}
```

### `TenantConfigSchema`

```typescript
TenantConfig {
  tenant_id:    string
  tier:         "standard" | "enterprise"
  workspace_id: string
  rate_limits: {
    requests_per_minute:     int > 0  // default: 1000
    max_concurrent_sessions: int > 0  // default: 500
    mcp_calls_per_second:    int > 0  // default: 100
  }
}
```

---

## API Pública — Exports Nomeados

Cada schema tem um tipo TypeScript correspondente inferido com o mesmo nome sem o sufixo `Schema`:

| Schema | Tipo TypeScript | Arquivo |
|---|---|---|
| `ContextPackageSchema` | `ContextPackage` | context-package.ts |
| `SessionItemSchema` | `SessionItem` | context-package.ts |
| `ConversationInsightSchema` | `ConversationInsight` | context-package.ts |
| `PendingDeliverySchema` | `PendingDelivery` | context-package.ts |
| `AgentDoneSchema` / `AgentDonePayloadSchema` | `AgentDone` / `AgentDonePayload` | context-package.ts |
| `SkillSchema` / `SkillRegistrationSchema` | `Skill` / `SkillRegistration` | skill.ts |
| `SkillFlowSchema` | `SkillFlow` | skill.ts |
| `FlowStepSchema` | `FlowStep` | skill.ts |
| `SkillRefSchema` | `SkillRef` | skill.ts |
| `PoolRegistrationSchema` | `PoolRegistration` | agent-registry.ts |
| `AgentTypeRegistrationSchema` / `AgentTypeSchema` | `AgentTypeRegistration` / `AgentType` | agent-registry.ts |
| `PipelineStateSchema` | `PipelineState` | agent-registry.ts |
| `RoutingDecisionSchema` | `RoutingDecision` | agent-registry.ts |
| `TenantConfigSchema` | `TenantConfig` | agent-registry.ts |

---

## Dependências

```
@plughub/schemas
  └── zod 3.23+   ← sem dependências internas
```

---

## Relação com Outros Módulos

```
@plughub/schemas ← importado por todos os pacotes internos, nunca importa de nenhum deles
  ↑ sdk
  ↑ mcp-server-plughub
  ↑ skill-flow-engine
  ↑ ai-gateway
  ↑ agent-registry
  ↑ routing-engine
  ↑ rules-engine
  ↑ channel-gateway
```
