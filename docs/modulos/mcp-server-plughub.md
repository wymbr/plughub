# Módulo: mcp-server-plughub

> Pacote: `mcp-server-plughub` (serviço)
> Runtime: Node 20+, TypeScript
> Transporte: SSE sobre HTTP, porta 3100
> Spec de referência: seções 3.2a, 3.2b, 4.2, 4.5, 9.4, 9.5

## O que é

O `mcp-server-plughub` é o MCP Server da própria plataforma PlugHub. É o ponto de integração entre dois mundos distintos:

- **Sistemas externos (BPM)** — usam as tools BPM para iniciar, consultar e encerrar conversas
- **Agentes durante atendimento** — usam as tools Agent Runtime para gerenciar seu ciclo de vida e registrar dados na sessão

Ele não implementa lógica de negócio. Recebe chamadas de tool, valida inputs com Zod, roteia para os componentes internos corretos (Redis, Kafka, Agent Registry) e retorna o resultado. Toda lógica de decisão vive no `routing-engine`, `rules-engine` e nos próprios agentes.

---

## Invariantes

- Nunca implementar lógica de negócio — apenas receber e rotear
- Toda tool valida input com Zod antes de qualquer operação
- Toda tool autentica via JWT no header `Authorization`
- `session_id` é obrigatório em todas as tools de Agent Runtime
- `tenant_id` é inferido do JWT — nunca do corpo da requisição

---

## Transporte e Configuração

```
Protocolo:  SSE (Server-Sent Events) sobre HTTP
Endpoint:   http://{host}:3100/sse
Consumo:    múltiplos consumidores simultâneos (não usa stdio)
```

A escolha de SSE em vez de stdio é intencional: o servidor precisa atender múltiplos agentes e sistemas externos em paralelo. stdio é adequado apenas para processos 1:1.

### Dependências de infraestrutura

| Dependência | Uso |
|---|---|
| Redis | Estado de instâncias de agente, fila de pools, insights de sessão |
| Kafka | Eventos de ciclo de vida (`agent.lifecycle`), conclusão de conversa (`conversations.events`) |
| Agent Registry | Validação de `agent_type_id` no `agent_login` |

### Dependências de código

```
@plughub/schemas       ← contratos de dados (AgentDonePayloadSchema, OutcomeSchema, IssueSchema)
@modelcontextprotocol/sdk ← SDK oficial Anthropic MCP
zod                    ← validação de input
```

---

## Grupos de Tools

O servidor expõe 18 tools organizadas em quatro grupos com consumidores distintos.

### Grupo 1: BPM (4 tools)

Consumidas por sistemas externos (orquestradores BPM, sistemas de negócio) para gerenciar o ciclo de vida de conversas de fora da plataforma.

| Tool | O que faz | Spec |
|---|---|---|
| `conversation_start` | Inicia um atendimento. Retorna `session_id`. Publica em `conversations.inbound`. | 9.4 |
| `conversation_status` | Retorna estado atual: status, agente alocado, sentiment, SLA. | 9.4 |
| `conversation_end` | Encerramento forçado (timeout, cancelamento, erro de sistema). | 9.4 |
| `rule_dry_run` | Simula uma regra do Rules Engine contra histórico de conversas. | 3.2b |

#### `conversation_start` — schema de input

```typescript
{
  channel:      "chat" | "whatsapp" | "sms" | "voice" | "email" | "webrtc"
  customer_id:  string (UUID)
  tenant_id:    string
  intent?:      string          // intent detectado pelo Channel Layer
  process_context?: {
    process_id?:       string
    process_instance?: string
    status?:           string
    payload?:          Record<string, unknown>
  }
}
```

#### `conversation_end` — motivos válidos

```
timeout         | Conversa excedeu SLA sem resposta
cancelled       | Cancelamento explícito pelo sistema
system_error    | Falha de infraestrutura
bpm_terminated  | Encerramento determinado pelo workflow BPM
```

#### `rule_dry_run` — schema de input

```typescript
{
  tenant_id:    string
  rule: {
    name:        string
    expression:  Record<string, unknown>   // mesma estrutura de regra do Rules Engine
    target_pool: string
  }
  history_window_days: number (1–90, default: 30)
}
```

Retorna: total de conversas analisadas, quantas teriam disparado a regra, taxa de disparo e amostra de triggers com timestamps. Ver seção 3.2b da spec para detalhes de shadow mode e canary release.

---

### Grupo 2: Agent Runtime (8 tools)

Consumidas pelos agentes durante o atendimento. São o contrato operacional entre o agente e a plataforma. Toda chamada exige `session_token` (JWT emitido no `agent_login`).

#### Ciclo de vida completo de uma instância

```
agent_login     → emite session_token, registra instância no Redis (estado: logged_in)
     ↓
agent_ready     → coloca nos pools declarados, disponível para alocação (estado: ready)
     ↓
agent_busy      → registra conversa ativa, incrementa current_sessions (estado: busy)
     ↓             se atingiu max_concurrent_sessions → remove dos pools temporariamente
agent_done      → decrementa current_sessions, publica em conversations.events
     ↓             se current_sessions == 0 → volta para ready
     ↓             se state == "draining" && current_sessions == 0 → logged_out + cleanup
agent_pause     → remove dos pools sem interromper sessões ativas (estado: paused)
     ↓             retorna ao ciclo via agent_ready
agent_logout    → drain: para novas alocações. Se sem sessões: logged_out imediato.
                  Se com sessões ativas: estado "draining" até último agent_done.
```

Além do ciclo principal, `agent_heartbeat` deve ser chamado a cada ~10s quando `ready` ou `busy`. A ausência de heartbeat por 30s é detectada pelo Routing Engine como crash da instância.

#### `agent_login` — schema de input

```typescript
{
  agent_type_id: string    // validado no Agent Registry
  instance_id:   string    // identificador único desta instância
  tenant_id:     string    // único campo onde tenant_id vem do body
}
```

Retorno: `{ session_token, token_expires_at, instance_id }`. O `session_token` é um JWT usado em todas as chamadas subsequentes. O `tenant_id` é inferido do token nas demais tools.

#### Estado da instância no Redis

```
Chave: {tenant_id}:agent:instance:{instance_id}   (Hash)

Campos:
  state:                   logged_in | ready | busy | paused | draining
  agent_type_id:           string
  current_sessions:        number   ← incrementado em agent_busy, decrementado em agent_done
  max_concurrent_sessions: number   ← copiado do Agent Registry no login
  pools:                   JSON (string[])
  logged_in_at:            ISO datetime
```

Índice adicional:
```
Chave: {tenant_id}:agent:token:{session_token}   → instance_id
Chave: {tenant_id}:agent:conversations:{instance_id}  (Set) → UUIDs de conversas ativas
```

#### `agent_done` — contrato de conclusão (spec 4.2)

É a tool mais crítica do contrato de execução. Sinaliza o encerramento de um atendimento.

```typescript
{
  session_token:      string
  conversation_id:    string (UUID)
  outcome:            "resolved" | "escalated_human" | "transferred_agent" | "callback"
  issue_status:       IssueSchema[]   // OBRIGATÓRIO, mínimo 1 item
  handoff_reason?:    string          // OBRIGATÓRIO quando outcome !== "resolved"
  resolution_summary?: string
  completed_at?:      ISO datetime    // default: agora
}
```

**Regras de validação via `AgentDonePayloadSchema` (`@plughub/schemas`):**

- `issue_status` nunca pode estar vazio — sem exceções
- `handoff_reason` é obrigatório quando `outcome` é `escalated_human` ou `transferred_agent`
- A validação usa `.refine()` do Zod — é executada após validação de campos individuais

**O que `agent_done` faz no Redis e Kafka:**

1. Decrementa `current_sessions` atomicamente via `HINCRBY`
2. Remove `conversation_id` do Set de conversas ativas da instância
3. Transição de estado pós-conclusão:
   - `current_sessions == 0` e estado não `paused` → `ready`
   - estado `draining` e `current_sessions == 0` → deleta instância e token do Redis
4. Publica em `conversations.events`: evento `conversation_completed`
5. Publica em `agent.lifecycle`: evento `agent_done`

#### `insight_register` — spec 3.4a

Registra insights gerados pelo agente durante a conversa atual.

```typescript
{
  session_token:   string
  conversation_id: string (UUID)
  category:        string   // DEVE começar com "insight.conversa."
  content:         Record<string, unknown>
  priority:        number (0–100)
  expires_at?:     ISO datetime   // default: TTL da sessão (4h)
}
```

**Restrição crítica de categoria:**

Agentes só podem registrar categorias `insight.conversa.*`. Categorias `insight.historico.*` são geradas por consumers Kafka externos após o fechamento do contato — nunca pelos agentes diretamente. Tentativas de registrar `insight.historico.*` são rejeitadas com `category_forbidden`.

**Persitência:** o insight é gravado no Redis com TTL calculado a partir de `expires_at` (ou 4h padrão). Ao fechar o contato, um consumer Kafka promove os insights de `insight.conversa.*` para `insight.historico.*` no PostgreSQL.

```
Chave Redis: {tenant_id}:insight:{conversation_id}:{item_id}   (String JSON)
```

#### `agent_delegate` — spec 9.5

Delega uma subtarefa a outro agente em modo A2A (agent-to-agent). Usada principalmente pelo agente orquestrador nos steps do tipo `task` do Skill Flow.

```typescript
{
  session_token:   string
  session_id:      string (UUID)
  target_skill:    string   // skill_id que o agente delegado deve implementar
  payload: {
    customer_id:      string
    pipeline_step:    string    // id do step no flow
    pipeline_context: Record<string, unknown>  // pipeline_state.results
  }
  delegation_mode: "silent"   // agente delegado não interage diretamente com o cliente
}
```

O `pipeline_context` contém apenas `pipeline_state.results` — o agente delegado recebe os dados de negócio necessários sem acesso à estrutura interna do flow (transições, retry_counters, error_context).

**Resultado via fire-and-poll:**

O `agent_delegate` retorna imediatamente um `job_id`. O orquestrador faz polling até obter o resultado:

```typescript
// Retorno imediato
{ job_id: string, status: "pending" }

// Após conclusão (polling)
{ job_id: string, status: "completed", outcome: "resolved", result: {...} }
```

---

### Grupo 3: Evaluation (4 tools)

Consumidas exclusivamente pelo agente de avaliação (`agente_avaliacao_v1`) durante
a execução do SkillFlow. Encapsulam acesso a PostgreSQL, proxy sidecar e Kafka
para que o flow permaneça puramente declarativo.

| Tool | O que faz |
|---|---|
| `transcript_get` | Busca mensagens do transcript no PostgreSQL por `transcript_id` |
| `evaluation_context_resolve` | Resolve declarações `requires_context` da evaluation skill via proxy sidecar e monta `agent_context_queue` |
| `evaluation_agent_context_next` | Pop-and-accumulate: desempilha o próximo agente especialista da fila e mescla o resultado anterior no acumulador |
| `evaluation_publish` | Calcula scores deterministicamente e publica `evaluation.completed` no Kafka |

#### `transcript_get` — schema de input

```typescript
{
  transcript_id: string (UUID)
}
```

Retorna lista de mensagens ordenadas por `position`:

```typescript
Array<{
  author_type: "customer" | "agent_human" | "agent_ai" | "system"
  content_text: string
  timestamp:    ISO datetime
  position:     number
}>
```

Retorna lista vazia (não erro) se `transcript_id` não existir ou transcript
ainda não foi persistido. O step `reason` recebe lista vazia e o LLM indica
na justificativa que o transcript não estava disponível.

---

#### `evaluation_context_resolve` — schema de input

```typescript
{
  skill_id:        string              // evaluation skill a carregar do Skill Registry
  context_package: Record<string,any>  // snapshot do contato — filtra seções via applies_when
  template_vars: {
    evaluation_id: string
    agent: {
      agent_id:   string
      agent_type: string
      pool_id:    string
    }
    contact: {
      contact_id: string
      channel:    string
    }
    context: {
      intent:  string | null
      flags:   string[]
      // demais campos do context_package
    }
  }
}
```

**Output:**

```typescript
{
  external_context:    Record<string, any>  // resultados de requires_context
  agent_context_queue: Array<{
    skill_id:   string   // skill do agente especialista
    output_key: string   // chave no acumulador onde seu resultado será armazenado
  }>
  // todas as seções ativas com requires_agent, em ordem de declaração no formulário
  // (vazia se nenhuma seção ativa declara requires_agent)
}
```

**Comportamento interno:**

1. Carrega a evaluation skill do Skill Registry por `skill_id`
2. Filtra seções ativas via `applies_when` usando `context_package`
3. Para cada seção ativa com `requires_context`:
   - Resolve templates `{{ key }}` nos `input` usando `template_vars`
   - Templates de seções anteriores já resolvidos também ficam disponíveis
   - Chama as tools declaradas em paralelo via proxy sidecar (localhost:7422)
   - Seções processadas em sequência (output de uma disponível para a próxima)
4. Coleta **todas** as seções ativas com `requires_agent` e monta `agent_context_queue`
5. Falhas individuais de tool são logadas e o `output_key` correspondente é omitido

Retorna `agent_context_queue: []` e `external_context: {}` se nenhuma seção
ativa declara `requires_context` ou `requires_agent`.

---

#### `evaluation_agent_context_next` — schema de input

```typescript
{
  queue:               Array<{ skill_id: string; output_key: string }>
  // fila atual — agent_context_queue na primeira chamada, remaining nas seguintes
  task_result?:        unknown
  // resultado do step task anterior (ausente na primeira chamada)
  current_output_key?: string
  // output_key do agente que acabou de executar
  accumulated?:        Record<string, any>
  // acumulador com resultados de todos os agentes já executados
}
```

**Output:**

```typescript
{
  has_next:           boolean   // true → há próximo agente para executar
  current_skill_id:   string    // skill_id a despachar (ou "" se has_next=false)
  current_output_key: string    // chave para armazenar resultado no acumulador
  remaining:          Array<{ skill_id: string; output_key: string }>
  // fila após desempilhar — passada ao invoke da próxima iteração
  accumulated:        Record<string, any>
  // contexto acumulado de todos os agentes executados até agora
  // disponível como agent_context no step evaluate
}
```

**Comportamento:** mescla `task_result` (resultado do agente anterior) no
acumulador usando `current_output_key`, depois desempilha o próximo item da fila.
Chamada com fila vazia retorna `has_next: false` sem alterar o acumulador.

---

#### `evaluation_publish` — schema de input

```typescript
{
  evaluation_id:        string (UUID)
  tenant_id:            string
  contact_id:           string (UUID)
  agent_id:             string
  agent_type:           "human" | "ai"
  pool_id:              string
  skill_id:             string
  triggered_by:         string
  llm_items: Array<{
    item_id:       string
    section_id:    string
    subsection_id: string
    value:         number   // 0–10
    justification: string
  }>
  overall_observation?: string
  context_package:      ContextPackage   // para filtrar items_excluded
}
```

**Comportamento interno:**

1. Carrega a evaluation skill por `skill_id`
2. Filtra seções e itens conforme `applies_when` e `applies_to` do `context_package`
3. Calcula scores deterministicamente (média ponderada bottom-up):
   ```
   subsection_score = Σ(item_value × item_weight) / Σ(item_weight)
   section_score    = Σ(subsection_score × subsection_weight) / Σ(subsection_weight)
   ```
4. Separa `base_score` (seção mandatory) dos `context_scores` (demais seções)
5. Constrói payload `evaluation.completed` com `items_excluded`
6. Publica em `evaluation.results` via Kafka

---

### Grupo 4: Supervisor (3 tools)

Consumidas pelo Agent Assist quando agente humano está em atendimento. Disponíveis apenas em pools com `supervisor_config.enabled: true`.

O Supervisor não é um agente — não tem ciclo de vida. Lê o estado já disponível no Redis (gravado pelo AI Gateway a cada turno) sem fazer cálculo adicional.

| Tool | O que faz | Spec |
|---|---|---|
| `supervisor_state` | Retorna sentiment atual, trajetória, intent, flags, SLA, contexto histórico do cliente | 3.2a |
| `supervisor_capabilities` | Retorna capacidades disponíveis filtradas por intent atual (agentes IA, pools de escalação) | 3.2a |
| `agent_join_conference` | Convida agente IA para conferência com agente humano e cliente | 3.2a |

#### `supervisor_state` — estrutura de resposta

```typescript
{
  session_id: string
  sentiment: {
    current:    number       // -1 a 1
    trajectory: number[]     // por turno
    trend:      "improving" | "stable" | "declining"
    alert:      boolean      // true quando abaixo do threshold configurado no pool
  }
  intent: {
    current:    string | null
    confidence: number | null
    history:    string[]
  }
  flags:        string[]     // ex: ["churn_signal", "high_value", "policy_limit_hit"]
  sla: {
    elapsed_ms:      number
    target_ms:       number
    urgency:         number
    breach_imminent: boolean
  }
  turn_count:   number
  snapshot_at:  ISO datetime
  is_stale:     boolean      // true se snapshot > 30s
  customer_context: {
    history_window_days:   number
    historical_insights:   InsightItem[]   // insight.historico.*
    conversation_insights: InsightItem[]   // insight.conversa.* da sessão atual
  }
}
```

#### `agent_join_conference` — modos de interação

```typescript
{
  session_id:    string (UUID)
  agent_type_id: string
  interaction_model: "background" | "conference"
  // background:   agente IA assiste sem participar diretamente
  // conference:   agente IA participa visivelmente na conversa
  channel_identity?: {
    text:          string    // nome exibido no chat
    voice_profile: string    // perfil de voz para canal de voz
  }
}
```

---

## Fluxo de Autenticação

Todas as tools de Agent Runtime e Supervisor usam JWT. O fluxo:

1. `agent_login` recebe `tenant_id`, `agent_type_id` e `instance_id` sem JWT
2. Valida `agent_type_id` no Agent Registry (HTTP para `agent-registry`)
3. Emite `session_token` JWT com `{ tenant_id, agent_type_id, instance_id }` no payload
4. Todas as calls subsequentes incluem `session_token` no body
5. O servidor verifica e decodifica o JWT — `tenant_id` é sempre extraído do token, nunca do body

**Chave de índice:** `{tenant_id}:agent:token:{session_token}` → `instance_id` no Redis, com mesmo TTL do JWT.

---

## Eventos Kafka Publicados

| Tool | Tópico | Evento |
|---|---|---|
| `agent_login` | `agent.lifecycle` | `agent_login` |
| `agent_ready` | `agent.lifecycle` | `agent_ready` |
| `agent_busy` | `agent.lifecycle` | `agent_busy` |
| `agent_done` | `agent.lifecycle` | `agent_done` |
| `agent_done` | `conversations.events` | `conversation_completed` |
| `agent_pause` | `agent.lifecycle` | `agent_pause` |
| `agent_logout` | `agent.lifecycle` | `agent_logout` |
| `agent_heartbeat` | `agent.lifecycle` | `agent_heartbeat` |

#### Schema do evento `conversation_completed` (conversations.events)

```typescript
{
  event:          "conversation_completed"
  tenant_id:      string
  instance_id:    string
  conversation_id: string
  outcome:        "resolved" | "escalated_human" | "transferred_agent" | "callback"
  issue_status:   IssueSchema[]
  handoff_reason: string | undefined
  completed_at:   ISO datetime
  timestamp:      ISO datetime
}
```

#### Schema do evento `agent_busy` (agent.lifecycle)

```typescript
{
  event:            "agent_busy"
  tenant_id:        string
  instance_id:      string
  conversation_id:  string
  current_sessions: number
  timestamp:        ISO datetime
}
```

---

## Tratamento de Erros

Erros são retornados como MCP error response (`isError: true`) — nunca como exceção não tratada.

| Código | Causa |
|---|---|
| `validation_error` | Input falhou na validação Zod (com detalhe de campo e mensagem) |
| `invalid_token` | JWT inválido ou expirado |
| `agent_type_not_found` | `agent_type_id` não existe no tenant no Agent Registry |
| `instance_not_found` | Chave Redis da instância não existe (expirou ou nunca foi criada) |
| `invalid_state` | Transição de estado inválida (ex: `agent_ready` quando estado é `busy`) |
| `category_forbidden` | Tentativa de registrar `insight.historico.*` via `insight_register` |
| `internal_error` | Erro não classificado (mensagem incluída) |

---

## Chaves Redis — Prefixos e Estruturas

Todas as chaves são prefixadas com `{tenant_id}:` para isolamento multi-tenant.

```
{tenant_id}:agent:instance:{instance_id}         Hash  → estado da instância
{tenant_id}:agent:token:{session_token}          String → instance_id (índice token→instância)
{tenant_id}:agent:conversations:{instance_id}    Set   → UUIDs de conversas ativas
{tenant_id}:pool:available:{pool_id}             Set   → instance_ids disponíveis no pool
{tenant_id}:insight:{conversation_id}:{item_id}  String (JSON) → InsightItem
```

---

## Status de Implementação

| Tool | Status |
|---|---|
| `agent_login` | ✅ Implementado |
| `agent_ready` | ✅ Implementado |
| `agent_busy` | ✅ Implementado |
| `agent_done` | ✅ Implementado |
| `agent_pause` | ✅ Implementado |
| `agent_logout` | ✅ Implementado (drain) |
| `agent_heartbeat` | ✅ Implementado |
| `insight_register` | ✅ Implementado |
| `agent_delegate` | ⚠️ Schema definido, integração com Routing Engine pendente |
| `conversation_start` | ⚠️ Stub — publicação Kafka e alocação via Routing Engine pendentes |
| `conversation_status` | ⚠️ Stub — leitura do Redis pendente |
| `conversation_end` | ⚠️ Stub — publicação Kafka e notificação ao agente pendentes |
| `rule_dry_run` | ⚠️ Stub — consulta ClickHouse pendente |
| `transcript_get` | ⚠️ Schema definido, implementação pendente |
| `evaluation_context_resolve` | ⚠️ Schema definido, implementação pendente |
| `evaluation_agent_context_next` | ⚠️ Schema definido, implementação pendente |
| `evaluation_publish` | ⚠️ Schema definido, implementação pendente |
| `supervisor_state` | ⚠️ Stub — leitura do Redis pendente |
| `supervisor_capabilities` | ⚠️ Stub — leitura do `intent_capability_map` do pool pendente |
| `agent_join_conference` | ⚠️ Stub — publicação `conference.joined` no Kafka pendente |

---

## Relação com Outros Módulos

```
mcp-server-plughub
  ├── consome → @plughub/schemas    (contratos de dados)
  ├── consome → agent-registry      (validação de agent_type_id no login)
  ├── consome → skill-registry      (evaluation_context_resolve + evaluation_publish carregam skill YAML)
  ├── consome → PostgreSQL          (transcript_get — tabela transcript_messages)
  ├── chama   → proxy sidecar       (evaluation_context_resolve → domain MCP servers)
  ├── escreve → Redis               (estado de instâncias, insights, pool queues)
  ├── publica → Kafka               (agent.lifecycle, conversations.events, evaluation.results)
  └── é consumido por:
        ├── agentes nativos via SDK (@plughub/sdk)
        ├── agentes externos via proxy sidecar (plughub-sdk proxy)
        ├── skill-flow-engine       (agent_delegate, agent_done, transcript_get,
        │                            evaluation_context_resolve, evaluation_publish)
        └── sistemas BPM externos  (conversation_start/status/end, rule_dry_run)
```
