# PlugHub — Modelos de Dados e Persistência

> Camadas de persistência: Redis · PostgreSQL · ClickHouse
> Convenção de chave: todas as chaves Redis são prefixadas com `{tenant_id}:` (isolamento multi-tenant)

---

## Sumário

- [Redis — Visão Geral](#redis--visão-geral)
- [Redis — mcp-server-plughub](#redis--mcp-server-plughub)
- [Redis — skill-flow-engine](#redis--skill-flow-engine)
- [Redis — routing-engine](#redis--routing-engine)
- [Redis — ai-gateway](#redis--ai-gateway)
- [Redis — rules-engine](#redis--rules-engine)
- [PostgreSQL — agent-registry](#postgresql--agent-registry)
- [ClickHouse — rules-engine](#clickhouse--rules-engine)
- [Matriz de Acesso por Módulo](#matriz-de-acesso-por-módulo)

---

## Redis — Visão Geral

O Redis é a camada de estado operacional da plataforma — tudo que precisa ser consultado com baixa latência durante o atendimento vive aqui. Cada módulo tem sua própria subárvore de chaves, sem sobreposição:

| Subárvore | Módulo dono | Propósito |
|---|---|---|
| `{t}:agent:*` | mcp-server-plughub | Ciclo de vida de instâncias de agente |
| `{t}:pool:{id}:available` | mcp-server-plughub | Set de instâncias disponíveis por pool |
| `{t}:insight:*` | mcp-server / routing-engine | Insights de conversa e histórico do cliente |
| `{t}:pending:*` | Notification Agent | Pending deliveries (outbound.*) |
| `{t}:pipeline:*` | skill-flow-engine | Estado do pipeline e locks de execução |
| `{t}:instance:*` | routing-engine | Snapshot de instâncias para alocação |
| `{t}:pool_config:*` | routing-engine | Cache de configurações de pool |
| `{t}:pool:{id}:queue` | routing-engine | Fila de contatos em espera |
| `{t}:routing:instance:*` | routing-engine | Metadata persistente (crash recovery) |
| `{t}:session:{id}:context` | routing-engine | Contexto consolidado entregue ao agente |
| `{t}:session:{id}:turn:*:params` | ai-gateway | Parâmetros extraídos por turno |
| `{t}:cache:*` | ai-gateway | Cache semântico de respostas LLM |
| `{t}:ratelimit:*` | ai-gateway | Contadores de rate limiting |
| `rules:{t}:active` | rules-engine | Regras ativas do tenant |

> `{t}` = `{tenant_id}` nas tabelas abaixo.

---

## Redis — mcp-server-plughub

### `{t}:agent:instance:{instance_id}` — HASH

Estado canônico de uma instância de agente. Escrito por `agent_login`; atualizado por `agent_ready`, `agent_busy`, `agent_done`, `agent_pause`, `agent_logout`.

| Campo HASH | Tipo | Descrição |
|---|---|---|
| `state` | string | `logged_in` \| `ready` \| `busy` \| `paused` \| `draining` \| `logged_out` |
| `agent_type_id` | string | Tipo do agente |
| `current_sessions` | string (int) | Sessões ativas no momento |
| `max_concurrent_sessions` | string (int) | Limite configurado no AgentType |
| `pools` | string (JSON) | `string[]` — pools a que a instância pertence |
| `logged_in_at` | string (ISO) | Timestamp de login |

TTL: `SESSION_TOKEN_TTL_S` (alinhado com expiração do JWT); renovado a cada `agent_heartbeat`.

Transição automática: quando `draining` + `current_sessions == 0` após `agent_done`, a chave é deletada (logout implícito).

---

### `{t}:agent:token:{session_token}` — STRING

Índice de lookup `session_token → instance_id`. Permite que qualquer tool valide o token e encontre a instância em O(1).

Valor: `instance_id` (UUID)
TTL: `SESSION_TOKEN_TTL_S`

---

### `{t}:pool:{pool_id}:available` — SET

Set de `instance_id`s com `state == ready` em um pool. Consultado pelo Routing Engine para encontrar candidatos.

- `SADD` em `agent_ready` (instância disponível)
- `SREM` em `agent_busy` quando `current_sessions >= max` (instância saturada)
- `SREM` em `agent_pause` e `agent_logout`

Sem TTL — gerenciado por transições de estado.

---

### `{t}:agent:instance:{instance_id}:conversations` — SET

Set de `conversation_id`s ativos na instância. Atualizado atomicamente:

- `SADD` em `agent_busy`
- `SREM` em `agent_done`

---

### `{t}:insight:{conversation_id}:{item_id}` — STRING

Insight registrado durante a conversa atual (`insight.conversa.*`). Escrito por `insight_register` (mcp-server).

```json
{
  "item_id":           "uuid",
  "tenant_id":         "string",
  "source_session_id": "uuid",
  "source":            "instance_id",
  "category":          "insight.conversa.*",
  "content":           { ... },
  "priority":          0–100,
  "status":            "pending",
  "registered_at":     "ISO datetime",
  "expires_at":        "ISO datetime (optional)"
}
```

TTL: determinado por `expires_at` se fornecido; padrão = 4h (duração típica de sessão).

> Restrição: `insight_register` aceita **apenas** `insight.conversa.*`. Chaves `insight.historico.*` são escritas por sistemas externos (via Kafka consumer), nunca diretamente pelo agente.

---

## Redis — skill-flow-engine

### `{t}:pipeline:{session_id}` — STRING

Estado completo do pipeline de uma sessão. Serialização do `PipelineState` (schema `@plughub/schemas`).

```json
{
  "flow_id":          "skill_id",
  "current_step_id":  "step_id",
  "status":           "in_progress | completed | failed",
  "started_at":       "ISO datetime",
  "updated_at":       "ISO datetime",
  "results":          { "output_as_field": <any> },
  "retry_counters":   { "step_id": <int> },
  "transitions": [
    {
      "from_step":  "step_id",
      "to_step":    "step_id",
      "reason":     "completed | catch | escalated | error",
      "timestamp":  "ISO datetime"
    }
  ]
}
```

TTL: 86400s (24h — alinhado com validade máxima de sessão).

> **Invariante**: persiste a **cada transição de step** — antes de executar o próximo. Garante retomada após falha sem perda de estado.

---

### `{t}:pipeline:{session_id}:running` — STRING

Lock distribuído de execução do pipeline. Impede execução concorrente da mesma sessão em múltiplas instâncias.

Valor: `"1"`
TTL: 300s (5 minutos — tempo máximo de execução de um step)

Adquirido via `SET ... NX EX` (atômico). Se retorna `null`, a sessão já está em execução em outra instância.

---

### `{t}:pipeline:{session_id}:job:{step_id}` — STRING

`job_id` do `agent_delegate` associado a um step `task`. Garante idempotência: se o step for retomado após falha, usa o mesmo `job_id` em vez de criar uma nova delegação.

Valor: `job_id` (UUID)
TTL: 86400s (alinhado com o pipeline)

---

## Redis — routing-engine

### `{t}:instance:{instance_id}` — STRING

Snapshot do estado de uma instância para uso exclusivo do Routing Engine. **Populado pelo `kafka_listener` via eventos `agent.lifecycle`** — o Routing Engine nunca acessa o mcp-server diretamente.

```json
{
  "instance_id":      "string",
  "agent_type_id":    "string",
  "tenant_id":        "string",
  "pool_id":          "string",
  "pools":            ["pool_id"],
  "execution_model":  "stateless | stateful",
  "max_concurrent":   1,
  "current_sessions": 0,
  "status":           "ready | busy | paused | ...",
  "last_seen":        "ISO datetime",
  "registered_at":    "ISO datetime",
  "profile":          { "competency": score }
}
```

TTL: 30s — renovado a cada evento `agent_ready` ou `agent_busy`. A ausência de TTL renewal por 30s é detectada pelo CrashDetector como crash da instância.

---

### `{t}:pool:{pool_id}:instances` — SET

Set de `instance_id`s atualmente em estado `ready` no pool, na perspectiva do Routing Engine. Atualizado pelo `kafka_listener` ao processar eventos `agent.lifecycle`.

---

### `{t}:pool_config:{pool_id}` — STRING

Cache de configuração de pool, populado por `kafka_listener` ao receber eventos `agent.registry.events`. O Routing Engine **nunca acessa PostgreSQL diretamente**.

```json
{
  "pool_id":        "string",
  "tenant_id":      "string",
  "channel_types":  ["chat", "whatsapp", ...],
  "sla_target_ms":  3000,
  "routing_expression": {
    "weight_sla":      1.0,
    "weight_wait":     0.8,
    "weight_tier":     0.6,
    "weight_churn":    0.9,
    "weight_business": 0.4
  },
  "competency_weights": { ... },
  "aging_factor":   0.4,
  "breach_factor":  0.8,
  "remote_sites":   [],
  "is_human_pool":  false
}
```

TTL: `pool_config_ttl_seconds` (padrão 24 horas — configurável via `PLUGHUB_POOL_CONFIG_TTL_SECONDS`). Pool configs são estáticas durante a operação normal; o TTL longo evita que o routing engine perca visibilidade de pools entre reinicios do agent-registry.

---

### `{t}:pools` — SET

Set de `pool_id`s do tenant. Usado para enumerar pools candidatos sem scan. Atualizado quando um novo pool é registrado.

---

### `{t}:pool:{pool_id}:queue` — SORTED SET

Fila de contatos aguardando alocação. Score = `queued_at_ms` (timestamp epoch em ms — o menor score = mais antigo). Suporta re-priorização dinâmica via `ZADD`.

Membro: `session_id`

---

### `{t}:queue_contact:{session_id}` — STRING

Dados do contato em espera, referenciado pelo ZSET da fila.

```json
{
  "session_id":  "uuid",
  "tenant_id":   "string",
  "pool_id":     "string",
  "tier":        "platinum | gold | standard",
  "queued_at_ms": 1234567890,
  "requirements": { "competency": score }
}
```

---

### `session_instance:{session_id}` — STRING

Afinidade de sessão para agentes `stateful`. Garante que uma sessão retorne sempre para a mesma instância.

Valor: `instance_id`
TTL: 86400s (24h)

Nota: esta chave **não** é prefixada com `tenant_id` — a chave global por `session_id` é suficiente porque `session_id` é UUID único globalmente.

---

### `{t}:routing:instance:{instance_id}:meta` — HASH (sem TTL)

Metadata persistente da instância, usada pelo CrashDetector para recuperar conversas órfãs após crash.

| Campo | Tipo | Descrição |
|---|---|---|
| `pools` | string (JSON) | Lista de pool_ids da instância |
| `agent_type_id` | string | Tipo do agente |

Sem TTL — sobrevive ao crash da instância (a chave com TTL 30s expira; esta não).

---

### `{t}:routing:instance:{instance_id}:conversations` — SET (sem TTL)

Set de `conversation_id`s ativos na instância, na perspectiva do Routing Engine. Populado em `agent_busy`, removido em `agent_done`. Usado pelo CrashDetector para identificar conversas que precisam ser re-enfileiradas após crash.

---

### `{t}:session:{conversation_id}:context` — STRING

Contexto consolidado entregue ao agente no início do atendimento. Construído pelo Routing Engine no início de cada contato, consumido pelo AI Gateway e pelo Supervisor.

```json
{
  "customer_id":           "string",
  "conversation_id":       "uuid",
  "conversation_insights": [
    { "item_id": "...", "category": "insight.conversa.*|insight.historico.*", "content": {...}, "priority": 80 }
  ],
  "pending_deliveries": [
    { "item_id": "...", "category": "outbound.*", "content": {...}, "priority": 60 }
  ]
}
```

TTL: 3600s (1h)

Inclui tanto `insight.conversa.*` (sessões anteriores da conversa atual) quanto `insight.historico.*` (memória de longo prazo do cliente).

---

### `{t}:insight:h:{customer_id}:{item_id}` — STRING

Insight histórico do cliente (`insight.historico.*`). Diferente dos insights de conversa, esta chave é indexada por `customer_id`, não por `conversation_id` — persiste entre contatos.

Escrito por consumer Kafka (`insight.historico.*` promovido a partir de `insight.conversa.*` no fechamento do contato).

---

### `{t}:pending:{customer_id}:{item_id}` — STRING

Pending delivery ativo (`outbound.*`). Representa uma entrega pendente para o cliente — promoção, lembrete, ação de follow-up — que deve ser apresentada no próximo atendimento.

---

## Redis — ai-gateway

### `{t}:session:{session_id}:turn:{turn_id}:params` — STRING

Parâmetros extraídos do turno atual pelo AI Gateway. Escrito **antes de retornar a resposta** (invariante).

```json
{
  "intent":          "cancellation | complaint | support | upgrade | billing | general",
  "confidence":      0.0–1.0,
  "sentiment_score": -1.0–1.0,
  "risk_flag":       false,
  "semantic_flags": {
    "churn_signal":     false,
    "high_frustration": false,
    "urgency":          false,
    "high_value":       false,
    "escalation_hint":  false
  }
}
```

Sem TTL explícito — gerenciado pelo ciclo de vida da sessão.

---

### `{t}:cache:{sha256_hash[:32]}` — STRING

Cache semântico de resposta LLM. A chave é os primeiros 32 caracteres do SHA-256 do histórico de mensagens normalizado (lowercase + strip).

Valor: `InferenceResponse` serializado (inclui `response_text`, `extracted_params`, etc.)
TTL: 300s (5 minutos)

---

### `{t}:ratelimit:{agent_type_id}:{window_minute}` — STRING

Contador de chamadas LLM por janela de 1 minuto, por `(tenant_id, agent_type_id)`.

`window_minute` = `unix_timestamp // 60`

Valor: contador inteiro
TTL: 60s (expira automaticamente ao fim da janela)

---

## Redis — rules-engine

### `rules:{t}:active` — STRING

Array JSON com todas as regras ativas (`status == "active"` ou `"shadow"`) do tenant.

```json
[
  {
    "rule_id":    "string",
    "tenant_id":  "string",
    "name":       "string",
    "status":     "active | shadow",
    "conditions": [
      {
        "parameter":    "sentiment_score | intent_confidence | turn_count | elapsed_ms | flag",
        "operator":     "lt | lte | gt | gte | eq | neq | contains",
        "value":        0.0,
        "window_turns": null,
        "flag_name":    null
      }
    ],
    "logic":       "AND | OR",
    "target_pool": "pool_id | null",
    "priority":    1–10,
    "created_at":  "ISO datetime",
    "updated_at":  "ISO datetime"
  }
]
```

Sem TTL. Cache local no processo com TTL de 60s para evitar reads repetidos.

---

## PostgreSQL — agent-registry

Fonte de verdade administrativa da plataforma. Apenas o `agent-registry` escreve neste banco. Outros módulos consomem os dados via cache Redis (populado por eventos Kafka `agent.registry.events`).

### `Pool`

```
Pool {
  id             String   PK — snake_case, sem versão (ex: retencao_humano)
  tenant_id      String
  description    String?
  channel_types  String[] — Channel enum values
  sla_target_ms  Int
  status         String   — "active" | "inactive"
  routing_expr   Json?    — RoutingExpression serializado
  supervisor_cfg Json?    — SupervisorConfig serializado
  created_at     DateTime
  updated_at     DateTime

  UNIQUE (id, tenant_id)
}
```

Regras:
- Pools nunca são deletados — apenas `status: "inactive"`
- `routing_expr` tem os cinco pesos de `priority_score` (sla, wait, tier, churn, business)

---

### `AgentType`

```
AgentType {
  id                      String   PK — formato: {nome}_v{n}
  tenant_id               String
  framework               String
  execution_model         String   — "stateless" | "stateful"
  role                    String   — "executor" | "orchestrator"
  max_concurrent_sessions Int
  permissions             String[] — "mcp-server-nome:tool_name"
  capabilities            Json
  agent_classification    Json?
  prompt_id               String?
  traffic_weight          Float?   — canary: null | 0.10 | 0.20 | 0.50 | 1.00
  status                  String   — "active" | "archived"
  created_at              DateTime
  updated_at              DateTime

  pools   AgentTypePool[]  — junction
  skills  AgentTypeSkill[] — junction

  UNIQUE (id, tenant_id)
}
```

Regras:
- `id` é imutável após criação — nova versão = novo registro com `_v{n+1}`
- Canary: `traffic_weight` nulo = sem canary; progressão 0.10 → 0.20 → 0.50 → 1.00

---

### `AgentTypePool` (junction)

```
AgentTypePool {
  agent_type_id  String  — FK → AgentType.id
  pool_id        String  — FK → Pool.id
  tenant_id      String

  UNIQUE (agent_type_id, pool_id, tenant_id)
}
```

---

### `AgentInstance`

```
AgentInstance {
  instance_id    String   PK — UUID gerado em agent_login
  tenant_id      String
  agent_type_id  String
  pool_id        String
  session_id     String?  — preenchido em agent_busy
  status         String   — "ready" | "busy" | "paused" | "logged_out"
  login_at       DateTime
  updated_at     DateTime

  UNIQUE (instance_id, tenant_id)
}
```

Escrito e atualizado **exclusivamente pelo mcp-server-plughub** via tools Agent Runtime. O `agent-registry` expõe as instâncias apenas para consulta (`GET /v1/instances`).

---

### `Skill`

```
Skill {
  skill_id    String   PK — formato: skill_{name}_v{n}
  tenant_id   String
  name        String
  version     String
  description String
  definition  Json     — SkillSchema serializado completo (inclui flow_definition)
  status      String   — "active" | "deprecated"
  created_at  DateTime
  updated_at  DateTime

  UNIQUE (skill_id, tenant_id)
}
```

---

## ClickHouse — rules-engine

O ClickHouse armazena o log de auditoria de escalações e os dados históricos de sessão usados em dry-run.

### `escalation_audit` (tabela)

Registra toda escalação disparada pelo Rules Engine (modo `active`), bem como os disparos de shadow mode.

| Coluna | Tipo | Descrição |
|---|---|---|
| `session_id` | String | Sessão escalada |
| `tenant_id` | String | Tenant |
| `rule_id` | String | Regra que disparou |
| `rule_name` | String | Nome da regra |
| `target_pool` | String | Pool de destino |
| `shadow_mode` | Boolean | `true` = shadow (não escalou de fato) |
| `triggered_at` | DateTime | Timestamp do disparo |
| `sentiment_score` | Float64 | Score no momento do disparo |
| `intent_confidence` | Float64 | Confiança da intenção |
| `turn_count` | Int32 | Turnos sem resolução |
| `elapsed_ms` | Int64 | Tempo total da sessão |

Acessado pelo Rules Engine para:
- Dry-run histórico — simular regra contra janela de sessões passadas
- Relatório de escalações ativas (`GET /api/v1/rules/{id}/report`)

---

## Matriz de Acesso por Módulo

Legenda: **E** = Escrita · **L** = Leitura · **—** = Sem acesso

### Redis

| Chave (padrão) | mcp-server | skill-flow | routing-engine | ai-gateway | rules-engine | channel-gw |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| `{t}:agent:instance:{id}` | **E** | — | L | — | — | — |
| `{t}:agent:token:{token}` | **E** | — | — | — | — | — |
| `{t}:pool:{id}:available` | **E** | — | L | — | — | — |
| `{t}:agent:instance:{id}:conversations` | **E** | — | — | — | — | — |
| `{t}:insight:{conv_id}:{item_id}` | **E** | — | L | — | — | — |
| `{t}:insight:h:{cust_id}:{item_id}` | — | — | L | — | — | — |
| `{t}:pending:{cust_id}:{item_id}` | — | — | L | — | — | — |
| `{t}:pipeline:{session_id}` | — | **E** | — | — | — | — |
| `{t}:pipeline:{session_id}:running` | — | **E** | — | — | — | — |
| `{t}:pipeline:{session_id}:job:{step_id}` | — | **E** | — | — | — | — |
| `{t}:instance:{instance_id}` | — | — | **E** | — | — | — |
| `{t}:pool:{id}:instances` | — | — | **E** | — | — | — |
| `{t}:pool_config:{pool_id}` | — | — | **E** | — | — | — |
| `{t}:pools` | — | — | **E** | — | — | — |
| `{t}:pool:{id}:queue` | — | — | **E** | — | — | — |
| `{t}:queue_contact:{session_id}` | — | — | **E** | — | — | — |
| `session_instance:{session_id}` | — | — | **E** | — | — | — |
| `{t}:routing:instance:{id}:meta` | — | — | **E** | — | — | — |
| `{t}:routing:instance:{id}:conversations` | — | — | **E** | — | — | — |
| `{t}:session:{conv_id}:context` | — | — | **E** | L | — | — |
| `{t}:session:{sid}:turn:{tid}:params` | — | — | — | **E** | L | — |
| `{t}:cache:{hash}` | — | — | — | **E** | — | — |
| `{t}:ratelimit:{agent_type}:{window}` | — | — | — | **E** | — | — |
| `rules:{t}:active` | — | — | — | — | **E** | — |

### PostgreSQL e ClickHouse

| Storage | Tabela / Coleção | Escrita | Leitura |
|---|---|---|---|
| PostgreSQL | `Pool` | agent-registry | routing-engine (via Kafka cache) |
| PostgreSQL | `AgentType` | agent-registry | routing-engine, mcp-server (via Kafka cache) |
| PostgreSQL | `AgentInstance` | mcp-server-plughub | agent-registry (exposição via API) |
| PostgreSQL | `Skill` | agent-registry | mcp-server, skill-flow (via referência) |
| ClickHouse | `escalation_audit` | rules-engine | rules-engine (dry-run, relatórios) |
