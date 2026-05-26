# Módulo: agent-registry (@plughub/agent-registry)

> Última atualização: 2026-05-25 · Estado: Arc 16

> Pacote: `agent-registry` (serviço)
> Runtime: TypeScript / Node 20+ · Express · PostgreSQL · Prisma
> Spec de referência: seções 4.5, 4.7

## O que é

O `agent-registry` é a **API administrativa da plataforma** — o catálogo central de tudo que pode ser alocado: pools de atendimento, tipos de agente, skills e instâncias ativas. Ele não participa do fluxo de atendimento em si; é o sistema de registro consultado pelo `routing-engine` para saber o que existe e pelo `mcp-server-plughub` para gerenciar o ciclo de vida das instâncias.

---

## Invariantes

- `tenant_id` é sempre extraído do JWT — nunca aceito no body da requisição
- IDs de recursos são **imutáveis** após criação — para evoluir um agente, cria-se uma nova versão (`_v2`, `_v3`, etc.)
- Pools **nunca são deletados** — apenas desativados (`status: inactive`)
- `pool_id` deve existir antes de criar um `AgentType` que o referencie
- `skill_id` deve existir antes de ser referenciado por um `AgentType`

---

## Estrutura do Pacote

```
agent-registry/
  src/
    routes/
      pools.ts         ← CRUD de pools
      agent-types.ts   ← CRUD de tipos de agente + canary deployment
      skills.ts        ← CRUD de skills
      instances.ts     ← consulta de instâncias ativas
    middleware/
      auth.ts          ← extração e validação do JWT
    app.ts             ← Express app, montagem das rotas
  prisma/
    schema.prisma      ← modelos de dados (Pool, AgentType, AgentInstance, Skill)
```

---

## Modelos de Dados (Prisma)

### `Pool`

```prisma
Pool {
  id                     String   // pool_id — snake_case, sem versão (ex: retencao_humano)
  tenant_id              String
  description            String?
  channel_types          String[] // Channel enum values
  sla_target_ms          Int
  status                 String   // "active" | "inactive"
  routing_expr           Json?    // RoutingExpression serializado
  supervisor_cfg         Json?    // SupervisorConfig serializado
  mentionable_pools      Json?    // { pool_id: label } — domínio de @mention
  mentionable_journeys   Json?    // { skill_id: label } — journeys que o pool pode iniciar
  inbound_journey_resume Boolean  @default(false)  // Arc 16 Fase E — flag informacional
  created_at             DateTime
  updated_at             DateTime

  @@unique([id, tenant_id])
}
```

> **`mentionable_pools`** define o domínio fechado de `@mention` do pool (apenas `role: primary`/`human` podem emitir menções). **`mentionable_journeys`** lista as journeys (`skill_id`) que um agente do pool pode iniciar via `@journey:<skill_id>` ou pelo botão "Iniciar Processo" do Console. **`inbound_journey_resume`** (Arc 16 Fase E) é uma flag informacional para o autor da skill — o Routing Engine não a lê.

### `AgentType`

```prisma
AgentType {
  id                      String    // agent_type_id — formato: {nome}_v{n}
  tenant_id               String
  framework               String
  execution_model         String    // "stateless" | "stateful"
  role                    String    // "executor" | "orchestrator"
  max_concurrent_sessions Int
  permissions             String[]  // "mcp-server-nome:tool_name"
  capabilities            Json
  media_capabilities      String[]  @default([])  // Arc 15 — ["video","voice","text"]
  agent_classification    Json?
  prompt_id               String?
  traffic_weight          Float?    // canary: 0.10 → 0.20 → 0.50 → 1.00
  status                  String    // "active" | "archived"
  created_at              DateTime
  updated_at              DateTime

  pools   AgentTypePool[]   // junction table
  skills  AgentTypeSkill[]  // junction table

  @@unique([id, tenant_id])
}
```

> **`media_capabilities`** (Arc 15 — canal WebRTC) declara os mediums que o agente suporta. A ordem implica preferência; lista vazia significa text-only. Usado pelo `negotiate_medium()` do Channel Gateway para escolher video → voice → text.

### `AgentInstance`

```prisma
AgentInstance {
  instance_id  String    // UUID gerado pelo mcp-server no agent_login
  tenant_id    String
  agent_type_id String
  pool_id      String
  session_id   String?   // preenchido em agent_busy
  status       String    // "ready" | "busy" | "paused" | "logged_out"
  login_at     DateTime
  updated_at   DateTime

  @@unique([instance_id, tenant_id])
}
```

### `Skill`

```prisma
Skill {
  skill_id      String
  tenant_id     String
  name          String
  version       String
  description   String
  definition    Json     // SkillSchema serializado completo
  status        String   // "active" | "deprecated"
  deploy_status String   @default("draft")  // "draft" | "published"
  created_at    DateTime
  updated_at    DateTime

  @@unique([skill_id, tenant_id])
}
```

### Skill Deploy Lifecycle

`deploy_status` separa a edição de uma skill da sua publicação:

- `PUT /v1/skills/:id` — sempre cria novas skills com `deploy_status: "draft"` e **nunca** modifica o `deploy_status` em atualizações.
- `POST /v1/skills/:id/deploy` — única ação que define `deploy_status: "published"`; grava um registro na tabela `skill_deployments` e publica `registry.changed`.

A tabela `skill_deployments` mantém o histórico de publicações (deploy events), usada como âncora temporal por relatórios de qualidade (Arc 6 Fase 2).

---

## Rotas — Pools (`/v1/pools`)

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/v1/pools` | Criar pool |
| `GET` | `/v1/pools` | Listar pools do tenant |
| `GET` | `/v1/pools/:id` | Consultar pool |
| `PUT` | `/v1/pools/:id` | Atualizar pool (inclusive status) |
| `GET` | `/v1/pools/:poolId/mentionable-agents` | Lista os tipos de agente ativos dos pools em `mentionable_pools` (Arc 11) |

> Não existe `DELETE /v1/pools/:id`. Para desativar: `PUT /v1/pools/:id` com `{ "status": "inactive" }`.

> `GET /v1/pools/:poolId/mentionable-agents` lê o `mentionable_pools` do pool, resolve os pools listados e retorna os `AgentType` ativos deles. Usado pelo Console (Arc 11 — "Adicionar Especialista") para oferecer a lista de especialistas invocáveis via A2A `assist`.

---

## Rotas — Agent Types (`/v1/agent-types`)

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/v1/agent-types` | Registrar novo tipo de agente |
| `GET` | `/v1/agent-types` | Listar tipos do tenant |
| `GET` | `/v1/agent-types/:id` | Consultar tipo de agente |
| `PATCH` | `/v1/agent-types/:id/canary` | Avançar peso de canary |
| `DELETE` | `/v1/agent-types/:id/canary` | Rollback de canary |

### Criação (`POST /v1/agent-types`)

Validações em sequência antes de inserir:

1. **Pools existem**: cada `pool_id` em `pools[]` deve existir para o tenant — retorna 400 com lista de pools não encontrados
2. **Skills existem**: cada `skill_id` em `skills[]` deve existir para o tenant — retorna 400 com lista de skills não encontradas
3. **Sem duplicata**: `agent_type_id` não pode existir para o tenant — retorna 409

### Canary Deployment

O canary permite introduzir uma nova versão de agente gradualmente, controlando a fração de sessões que ela recebe.

**Progressão de pesos** — `_nextCanaryWeight()`:

```
0.00 (sem canary) → 0.10 → 0.20 → 0.50 → 1.00
```

Cada chamada a `PATCH /v1/agent-types/:id/canary` avança para o próximo peso na sequência. Quando `traffic_weight` atinge `1.00`, o agente assume integralmente.

**Rollback** — `DELETE /v1/agent-types/:id/canary`:

```
1. Arquiva a versão atual (status: "archived")
2. Identifica a versão anterior por convenção: {base}_v{n-1}
   Ex: agente_retencao_v3 → agente_retencao_v2
3. Restaura a versão anterior (status: "active", traffic_weight: null)
```

> O rollback assume a convenção de nomenclatura `{nome}_v{n}`. Se a versão anterior não existir ou já estiver arquivada, retorna 404.

---

## Rotas — Skills (`/v1/skills`)

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/v1/skills` | Registrar skill |
| `GET` | `/v1/skills` | Listar skills do tenant |
| `GET` | `/v1/skills/:id` | Consultar skill |
| `PUT` | `/v1/skills/:id` | Atualizar skill — sempre `deploy_status: "draft"` em novas; nunca altera o `deploy_status` em updates |
| `POST` | `/v1/skills/:id/deploy` | Publica a skill (`deploy_status: "published"`) + registra em `skill_deployments` + publica `registry.changed` |

A skill armazena a definição completa (`SkillSchema`) no campo `definition` (JSON). O `skill_id` segue o formato `skill_{name}_v{n}`.

---

## Rotas — Instances (`/v1/instances`)

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/v1/instances` | Listar instâncias ativas do tenant |
| `GET` | `/v1/instances?pool_id=X` | Instâncias de um pool específico |
| `GET` | `/v1/instances?status=ready` | Filtrar por status |

As instâncias são criadas e atualizadas pelo `mcp-server-plughub` via `agent_login`, `agent_ready`, `agent_busy`, `agent_done` — nunca pelo próprio `agent-registry`. Este módulo apenas as expõe para consulta.

---

## Ciclo de Vida de uma Instância

```
agent_login  → cria AgentInstance (status: "ready")
               via mcp-server-plughub

agent_busy   → atualiza status: "busy", preenche session_id
               via mcp-server-plughub (no handoff)

agent_done   → atualiza status: "ready" (ou remove, se logout)
               via mcp-server-plughub

agent_logout → atualiza status: "logged_out"
               via mcp-server-plughub
```

---

## Cross-validações

O `agent-registry` é o guardião da consistência referencial entre entidades:

| Operação | Validação | Erro |
|---|---|---|
| Criar AgentType | Todos os `pool_id` em `pools[]` devem existir | 400 — lista de pools inválidos |
| Criar AgentType | Todos os `skill_id` em `skills[]` devem existir | 400 — lista de skills inválidas |
| Criar AgentType | `agent_type_id` não pode existir para o tenant | 409 — conflito |
| Rollback canary | Versão `{base}_v{n-1}` deve existir e estar arquivada ou ativa | 404 |

---

## Autenticação

Todas as rotas exigem JWT. O middleware `auth.ts` extrai e valida o token e popula `req.tenant_id`. O `tenant_id` nunca vem do body — sempre do token.

---

## Integração Kafka

O `agent-registry` publica eventos no tópico `registry.changed` após operações de escrita em pools e ao publicar uma skill. Isso permite que `routing-engine`, `Core` e `orchestrator-bridge` mantenham seus caches sincronizados sem acesso direto ao PostgreSQL.

### Eventos publicados

| Operação | Evento |
|---|---|
| `POST /v1/pools` | `pool.registered` |
| `PUT /v1/pools/:pool_id` | `pool.updated` |
| `POST /v1/skills/:id/deploy` | `registry.changed` (skill publicada) |

**Schema:**

```json
{
  "event":     "pool.registered | pool.updated",
  "tenant_id": "string",
  "pool": {
    "pool_id":            "string",
    "channel_types":      ["chat"],
    "sla_target_ms":      300000,
    "routing_expression": { ... },
    "supervisor_config":  null
  }
}
```

**Comportamento:** publicação é fire-and-forget — erros de Kafka são logados mas não impedem a resposta HTTP. A operação de persistência no PostgreSQL não é afetada por falhas de Kafka.

**Efeito no routing-engine:** ao receber `pool.registered` ou `pool.updated`, o `kafka_listener` chama `save_pool_config()` que atualiza `{tenant_id}:pool_config:{pool_id}` no Redis com TTL 24h. Sem este evento, o routing engine não enxerga o pool e trata todos os contatos como sem agente disponível.

---

## Dependências

```
agent-registry
  ├── schemas      ← tipos de domínio (AgentTypeRegistration, PoolRegistration, etc.)
  ├── PostgreSQL   ← persistência principal (via Prisma)
  ├── Prisma ORM   ← acesso ao banco
  ├── KafkaJS      ← publicação de eventos de configuração
  └── Express      ← framework HTTP
```

---

## Relação com Outros Módulos

```
agent-registry ← consultado por
  ↑ routing-engine     (lista pools e tipos disponíveis para alocação)
  ↑ rules-engine       (verifica capacidades e configuração de supervisão)
  ↑ mcp-server-plughub (cria/atualiza AgentInstances via agent_login/done)
  ↑ sdk (certify)      (valida manifesto contra tipos registrados)

agent-registry → persiste em
  → PostgreSQL (pools, agent-types, skills, instances)

agent-registry → publica em
  → Kafka: registry.changed (pool.registered, pool.updated, skill publicada)
     ← consumido por routing-engine, Core e orchestrator-bridge para sincronizar caches
```
