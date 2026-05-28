# Arc 19 — Modelo Unificado de Sessão: Workflow como Canal Webhook

> Criado: 2026-05-28 · Estado: Especificação v2 — revisado 2026-05-28

## Premissa

A plataforma tem dois modelos paralelos de execução que deveriam ser um só:

- **Contato** — inbound via channel-gateway (webchat, WA, voz…) → session_id → routing engine → pool → agente
- **Workflow** — trigger via workflow-api → WorkflowInstance → skill-flow-worker → executa steps

O Arc 19 elimina essa dualidade. Workflow passa a ser um **canal `webhook`** na channel-gateway, exatamente como voz ou WhatsApp. Cada skill registrada num pool webhook é um "endpoint" (análogo a um DIN de voz ou número WA). O trigger cria uma sessão normal, o routing engine aloca uma instância de skill-flow do pool, e o `session_id` é o identificador único e persistente de toda a execução — incluindo através de múltiplos ciclos de suspend/resume.

---

## Mapeamento de Conceitos

| Canal de voz/WA | Canal webhook (workflow) |
|---|---|
| DIN / número WA | `skill_id` como endpoint URL |
| `POST /webhook/whatsapp/{account}` | `POST /v1/channels/webhook/{skill_id}` |
| Sessão criada pelo adapter | Sessão criada pelo WebhookAdapter |
| Routing engine → pool | Routing engine → pool webhook |
| Agente AI/humano atende | Instância skill-flow "atende" |
| Canal entrega mensagens ao cliente | Canal entrega notificações/collects |
| `session_closed` quando desliga | `session_closed` quando `complete()` |

---

## Modelo de Sessão com Status `suspended`

### Status domain (extensão)

```
active      — sessão em execução (agente alocado)
suspended   — sessão pausada aguardando sinal externo (sem agente alocado)
closed      — sessão encerrada normalmente
abandoned   — nenhum agente atendeu antes do encerramento
```

### Ciclo de vida completo

```
trigger recebido pelo WebhookAdapter
  → session_opened  (status: active)
  → routing engine aloca instância do pool webhook
  → segmento aberto (segment_id A)

    step executa → step executa → suspend()
      → resume_token gerado
      → EXPIRE session:{id}:stream  +buffer
      → EXPIRE {tenant}:ctx:{session_id}  +buffer
      → agent_done (segment_id A fecha)
      → status: suspended

    [horas / dias depois]

    resume signal chega via WebhookAdapter (resume_token → session_id)
      → status: active
      → routing engine aloca nova instância do pool webhook
      → segmento aberto (segment_id B)

    step executa → step executa → complete()
      → agent_done (segment_id B fecha)
      → session_closed  (status: closed)
```

### Invariante fundamental

O `session_id` nunca muda. Cada suspend/resume abre e fecha um `segment_id` diferente — o segmento é a janela de execução ativa. A sessão é o thread que persiste por toda a vida do processo.

---

## WebhookAdapter — Canal `webhook`

Novo adapter em `channel-gateway/adapters/webhook.py`, seguindo o mesmo padrão de `webchat.py`, `whatsapp.py`, etc.

### Endpoints

```
POST /v1/channels/webhook/{skill_id}
  Corpo: { tenant_id, trigger_type, metadata?, customer_id? }
  Cria sessão, publica conversations.inbound, retorna session_id

POST /v1/channels/webhook/resume/{resume_token}
  Resolve resume_token → session_id (Redis hash resume_tokens:{tenant})
  Publica evento de resume na sessão, acorda a sessão suspensa
  Retorna session_id

GET /v1/channels/webhook/{session_id}/status
  Retorna status atual da sessão (active|suspended|closed)
```

### resume_token lookup

```
Redis hash: {tenant_id}:resume_tokens
  campo: {resume_token}
  valor: {session_id}:{step_id}:{expires_at}
TTL: mesmo que a sessão
Gravado pelo executor do step suspend() antes de suspender
```

### Mapeamento de trigger_types existentes

| Origem atual | Novo caminho |
|---|---|
| `POST /v1/workflow/trigger` (API) | `POST /v1/channels/webhook/{skill_id}` com `trigger_type: api` |
| Webhook externo | `POST /v1/channels/webhook/{skill_id}` com `trigger_type: webhook` |
| `creates_journey: true` no YAML | Skill auto-trigger na alocação, `trigger_type: yaml_auto` |
| `task` step (A2A) | Trigger interno do routing engine, `trigger_type: task` |
| Scheduler | Trigger via webhook adapter, `trigger_type: scheduled` |

---

## Pool Webhook

### Configuração

```yaml
- pool_id: portabilidade_processo
  channel_types: [webhook]          # novo channel type
  skill_id: skill_portabilidade_demo_v1   # endpoint — o "DIN" do pool
  sla_target_ms: 172800000          # 48h
  max_concurrent_sessions: 50       # capacidade de execução paralela
```

O campo `skill_id` no pool é o link entre o endpoint webhook e o skill que o pool executa. Um pool webhook tem exatamente um skill (o processo de negócio que ele gerencia).

### Alocação

Routing engine trata pools webhook como qualquer outro pool. A diferença é que em vez de alocar um agente AI/humano, aloca uma **instância de skill-flow**. A capacidade é controlada por `max_concurrent_sessions`.

Quando uma sessão suspende: `agent_done` → instância volta ao pool (`agent_ready`). Quando resume: nova alocação normal.

---

## TTL e Preservação de Estado

O `suspend()` executor estende os TTLs de todas as chaves Redis da sessão:

```python
ttl_seconds = step.timeout_hours * 3600 + 3600  # +1h de buffer

await redis.expire(f"session:{session_id}:stream",  ttl_seconds)
await redis.expire(f"{tenant}:ctx:{session_id}",     ttl_seconds)
await redis.expire(f"{tenant}:pipeline:{session_id}", ttl_seconds)
await redis.expire(f"{tenant}:resume_tokens",         ttl_seconds)   # NX
```

O `pipeline_state` (já persistido a cada step) sobrevive naturalmente — a sessão não fecha, o Redis key não expira.

---

## O que é Eliminado

| Componente | Destino |
|---|---|
| `workflow-api` lifecycle endpoints (`/trigger`, `/resume`, `/complete`, etc.) | Substituídos pelo WebhookAdapter + session lifecycle normal |
| `WorkflowInstance` entidade separada | Vira `Session` com `session_type: workflow` |
| `skill-flow-worker` Kafka consumer | Subsumido pelo orchestrator-bridge alocando instâncias skill-flow de pools webhook |
| `workflow.events` Kafka topic | Substituído por `conversations.session_*` + eventos de segmento existentes |
| `Journey` entidade (Arc 10/16/17) | Eliminada conforme decisão já registrada — rastreabilidade via `parent_session_id` |
| Monitor/Processes página separada | Filtro `channel_type: webhook` no Monitor/Sessions |
| Analytics/Processes página separada | Filtro `channel_type: webhook` no Analytics/Sessions |

### O que sobrevive do workflow-api

- `calendar-api` (port 3700) — puro engine de calendário, independente
- Lógica de `business_hours` no executor suspend() — migra para skill-flow-engine direto

---

## O que é Unificado

### Monitor e Analytics

Monitor/Processes e Analytics/Processes são eliminadas. Workflows aparecem nas mesmas páginas de Sessions com filtro `channel_type: webhook`. Ver modelos detalhados nas seções **Monitor — Modelo Final** e **Analytics — Modelo Final** abaixo.

### ContextStore

`{tenant}:ctx:{session_id}` para tudo. Sem namespace `journey.*` separado. Um workflow invocado por `task` step de outro workflow usa o `session_id` do workflow pai como `parent_session_id` — hierarquia visível no trace sem FK especial.

### analytics.segments

Já tem `session_id`, `pool_id`, `agent_type_id`, `started_at`, `ended_at`. Não muda nada. Workflows aparecem como segmentos com `pool_id = portabilidade_processo`. `analytics.session_timeline` captura tudo via stream — inclusive os períodos `suspended` entre segmentos.

**TMA para sessions webhook**: calculado como `SUM(segment.duration_ms)` por `session_id` — não como `session.ended_at - session.started_at`. Isso exclui o tempo suspenso (que pode ser horas ou dias) do tempo real de execução.

---

## Segregação Workflow vs. Agente

O Arc 19 formaliza a distinção entre workflows e agentes como dois perfis de skill com conjuntos de steps disjuntos. O critério é **consciência de canal**: workflows são channel-agnostic (orquestram o quê), agentes são channel-aware (executam o como).

### Perfil `workflow` — `channel_type: webhook`

Steps disponíveis:

| Step | Papel |
|---|---|
| `task` | Delega para agente ou sub-workflow via routing engine |
| `choice` | Branching condicional sobre pipeline_state |
| `catch` | Retry e fallback antes de escalação |
| `escalate` | Roteamento para pool via rules engine |
| `complete` | Encerra com outcome definido |
| `invoke` | Chama MCP tool diretamente |
| `reason` | Invoca AI Gateway com output_schema |
| `suspend` | Suspende sessão até sinal externo (TTL estendido no Redis) |
| `collect` | Cria sessão-filho de contato e suspende até ela fechar |
| `receive` | Suspende aguardando próximo evento externo (sem prompt ao canal) |

Steps proibidos: `menu`, `notify`, `begin_transaction`, `end_transaction`.

Workflows não conhecem o canal do cliente. Se precisam enviar uma mensagem, delegam para um agente via `task`. Se precisam coletar dados, usam `collect` (que cria uma sessão-filho atendida por um agente channel-aware).

### Perfil `agent` — qualquer `channel_type` exceto `webhook`

Steps disponíveis:

| Step | Papel |
|---|---|
| `task` | Delega para especialista ou sub-workflow |
| `choice` | Branching condicional |
| `catch` | Retry e fallback |
| `escalate` | Roteamento para pool |
| `complete` | Encerra segmento |
| `invoke` | Chama MCP tool |
| `reason` | Invoca AI Gateway |
| `notify` | Envia mensagem ao cliente via canal |
| `menu` | Captura input do cliente (text / button / list / form) |
| `begin_transaction` / `end_transaction` | Bloco atômico de input mascarado |
| `receive` | Suspende aguardando próxima mensagem do canal |

Steps proibidos: `suspend`, `collect`.

Agentes completam segmentos. Só sessões (workflows) suspendem.

### Validação

O engine valida o perfil no parse da skill YAML — erro em configuração, não em runtime. Pool `channel_type: webhook` só aceita skills com perfil `workflow`. Pools com outros channel_types só aceitam skills com perfil `agent`.

---

## Collect Step no Novo Modelo

O `collect` step é exclusivo de workflows. Cria uma sessão-filho de contato (channel-aware) sem especificar o canal diretamente — o canal é negociado via capabilities (Arc 16):

```
sessão workflow (channel_type: webhook)
  └── parent_session_id: null

    collect step → cria sessão de contato (channel_type: negociado)
      └── parent_session_id: {workflow_session_id}
          channel_type: webchat | whatsapp | voice | ...
          (atendida por agente com perfil agent)
```

O workflow suspende durante o `collect`. Quando a sessão-filho fecha, o webhook adapter recebe o sinal de resume e retoma a sessão workflow pai com o resultado da coleta no `pipeline_state`.

O workflow nunca conhece qual canal foi usado — só recebe o resultado estruturado. O agente alocado para a sessão-filho é o responsável pelo I/O com o cliente.

---

## Session Trace — Observabilidade de Execução Webhook

O trace de execução step-a-step de sessions webhook é absorvido do Arc 18 com fonte de dados e superfície de UI adaptadas ao modelo Arc 19.

### Fonte de dados

`pipeline_state.transitions[]` é gravado pelo `PipelineStateManager.addTransition()` em cada transição de step — isso não muda. O que muda é onde o dado vive e como é preservado para histórico:

| Estado da session | Fonte do trace |
|---|---|
| `active` / `suspended` | Redis `{tenant}:pipeline:{session_id}` (TTL estendido pelo suspend()) |
| `closed` (recente) | Redis (ainda dentro do TTL pós-close) |
| `closed` (histórico) | ClickHouse `analytics.session_traces` |

**Persistência em `complete()`**: ao fechar uma session webhook, o executor emite um evento Kafka `session_trace_completed` com o array `transitions[]` completo. O consumer em `analytics-api` persiste no ClickHouse. Sem PostgreSQL envolvido.

```
complete() executor
  → XADD session:{id}:stream  session_closed
  → Kafka: session_trace_completed { session_id, transitions[], outputs, trigger_type }
    → analytics-api consumer → ClickHouse analytics.session_traces
```

### Endpoint

`GET /v1/sessions/{id}/trace` — novo endpoint no Core ou analytics-api (não no workflow-api).

Lógica: lê Redis primeiro (sessions ativas/suspensas). Para sessões fechadas com trace não mais no Redis, lê `analytics.session_traces`. Enriquece cada step com `step_type` e `step_label` do `flow_definition` (presente no ContextStore durante execução, ou no pool config).

Resposta: `SessionTrace` (renomeado de `InstanceTrace` do Arc 18):

```typescript
interface SessionTrace {
  session_id:        string
  skill_id:          string
  status:            "active" | "suspended" | "closed" | "abandoned"
  trigger_type:      "api" | "webhook" | "task" | "scheduled" | "yaml_auto"
  origin_session_id: string | null    // session que invocou via task step
  pool_id:           string
  contact_context:   Record<string, unknown>   // inputs do trigger
  outputs:           Record<string, unknown>   // outputs acumulados (output_as)
  transitions:       StepTransition[]
  current_step:      string | null
  outcome:           string | null
  created_at:        string
  suspended_at:      string | null
  closed_at:         string | null
  duration_ms:       number | null             // SUM(segment.duration_ms)
}

interface StepTransition {
  step_id:           string
  step_type:         string
  step_label:        string
  timestamp:         string
  reason:            "completed" | "suspended" | "resumed" | "on_failure" | "skipped" | "escalated"
  next_step:         string | null
  suspend_reason?:   "approval" | "input" | "webhook" | "timer"
  resume_decision?:  "approved" | "rejected" | "input" | "timeout"
  resume_timestamp?: string
  collect?: {
    status:          "pending" | "responded" | "expired"
    channel?:        string
    send_at?:        string
    expires_at?:     string
    responded_at?:   string
    child_session_id?: string    // link para a sessão-filho via parent_session_id
  }
}
```

`trigger_type` é derivado dos metadados da session — sem campo novo na tabela:
- `task` step A2A → `trigger_type: task`
- scheduler → `trigger_type: scheduled`
- `POST /v1/channels/webhook/{skill_id}` sem `origin_session_id` → `trigger_type: api`
- request com metadado `webhook_source` → `trigger_type: webhook`
- `creates_journey: true` no YAML → `trigger_type: yaml_auto`

`duration_ms` para sessions webhook = `SUM(segment.duration_ms)` — exclui tempo suspenso.

### UI — Aba Trace no detalhe de session

Em `Analytics/Sessions`, ao abrir uma session com `channel_type: webhook`, o detalhe exibe uma aba **Trace** além das abas de segmentos e transcript.

A aba Trace contém quatro seções (mesmo design do Arc 18 `ProcessDetailPage`):

```
┌─ ORIGEM ──────────────────────────────────────────────────┐
│  trigger_type badge · link para origin_session_id se task  │
└────────────────────────────────────────────────────────────┘
┌─ PARÂMETROS ──────────────────────────────────────────────┐
│  Entrada (contact_context)  │  Saída (outputs acumulados)  │
└────────────────────────────────────────────────────────────┘
┌─ EXECUÇÃO ─────────────────────────────────────────────────┐
│  ProcessStepTimeline — um item por transição               │
│                                                            │
│  ◉ verificar_elegibilidade  [task]       ✓ completed        │
│  ◉ solicitar_operadora      [suspend]    ⏸ suspended        │
│      Motivo: aprovação · Retomado: ... · Decisão: aprovado  │
│  ◉ aguardar_confirmacao     [collect]    ✓ completed        │
│      ┌─ Canal: whatsapp · Status: responded ──────────┐    │
│      │ Enviado: ...  Respondido: ...                  │    │
│      │ ↗ Ver sessão de resposta …abc123               │    │
│      └────────────────────────────────────────────────┘    │
│  ◉ finalizar               [complete]   ✓ completed        │
└────────────────────────────────────────────────────────────┘
```

**Collect cards** mostram `child_session_id` como link navegável para a sessão-filho em `Analytics/Sessions` — rastreabilidade completa entre workflow e contatos gerados.

**Polling**: para sessions `active` ou `suspended`, o hook `useSessionTrace` poleia a cada 5 segundos.

### Novos arquivos

| Componente | Arquivo | Descrição |
|---|---|---|
| Backend | `analytics-api/session_trace_consumer.py` | Consome `session_trace_completed`, persiste ClickHouse |
| Backend | `analytics-api/session_trace_endpoint.py` | `GET /v1/sessions/{id}/trace` |
| Backend | ClickHouse DDL | `analytics.session_traces` (`session_id`, `skill_id`, `transitions JSON`, `outputs JSON`, `trigger_type`, `closed_at`) |
| Frontend | `SessionTraceTab.tsx` | Aba Trace no session detail |
| Frontend | `ProcessStepTimeline.tsx` | Componente de timeline (portado do Arc 18 spec) |
| Frontend | `hooks.ts` | `useSessionTrace(sessionId, pollMs?)` |
| i18n | `contacts.json` (en + pt-BR) | Chaves `trace.*` (portadas do Arc 18 spec) |

---

## Monitor — Modelo Final

O Monitor é a superfície **operacional** — informa o estado recente para tomada de decisão imediata. Cobre desde o snapshot de agora até as últimas 24h como contexto. Os dados vêm de fontes mistas: Redis para snapshots, ClickHouse para tendências recentes.

Filtro de período disponível em todas as abas: `now` | `last_hour` | `last_24h` | `today`.

---

### Monitor / Sessions

**Filtros**: `channel_type` (all | webchat | whatsapp | voice | webrtc | webhook) · `period` · `pool`

**Métricas**:

| Métrica | Descrição |
|---|---|
| Total | Total de sessões no período |
| In Progress | Sessões com status `active` |
| Suspended | Sessões com status `suspended` (snapshot + total no período) |
| Resolved | Fechadas com `close_reason: flow_complete` (outcome success) |
| Escalated | Fechadas com `close_reason: agent_transfer` |
| Failure | Fechadas com `close_reason: system_error` |
| Timeout | Fechadas com `close_reason: session_timeout` ou `max_wait_exceeded` |
| Cancelled | Fechadas com `close_reason: customer_abandon` / `customer_hangup` / `customer_disconnect` |
| TMA | Tempo médio de atendimento — para webhook: `AVG(SUM(segment.duration_ms))` por session |

Sessões `suspended` têm badge visual distinto na lista — representam processos aguardando sinal externo.

Monitor/Processes é eliminada. Workflows aparecem aqui com `channel_type: webhook`.

---

### Monitor / Pools

**Filtros**: `period`

**Por pool**:

**Queue**
- Sessões em fila agora

**Resources**

| Métrica | Humano/AI | Webhook |
|---|---|---|
| Total | Instâncias configuradas | `max_concurrent_sessions` |
| Available | `agent_ready` | slots livres |
| Busy | `agent_busy` | instâncias ativas |
| Paused | Agentes em pausa + motivo | N/A |
| Máximo no período | Pico de busy no intervalo selecionado | Pico de instâncias ativas |

---

### Monitor / Agents

**Filtros**: `period` · `pools`

Aplica-se a agentes humanos e AI. Skill-flow instances (pools webhook) não aparecem aqui — cobertos pelo Monitor/Pools.

**Por agente**:
- Status: `active` | `paused` (+ motivo) | `logoff`
- Login time
- Pause time
- TMA
- Wrap-up time

**Totais no período**:
- Max Sessions configurado
- Busy (sessões simultâneas agora)
- Available
- Máximo de atendimentos no período
- Máximo de sessões simultâneas no período

---

### Monitor / Events

**Filtros**: `period` · `pool` · `category` (regex sobre dot-notation)

Eventos de negócio emitidos por agentes via `agent_event` (Arc 12). Nenhum evento interno de plataforma.

**Cards por tipo de evento**:
- `total` — contador de ocorrências no período
- `distribution` — `(total de cada valor) / total de eventos` por chave
- `average` — `(soma dos valores) / total de eventos`

**Formato do evento**:
```
category: {pool_id}.{skill_id}.{metric_key}   # filtrável via regex
  key: nome da dimensão
    value: valor do evento
    type:
      volume      → counter puro
      distribution → breakdown por valor [key1, key2, ...]
      average     → valor numérico para média
```

---

## Analytics — Modelo Final

O Analytics é a superfície **histórica** — análise retrospectiva com período livre, drill-down profundo e dados do ClickHouse. Não tem restrição de janela temporal.

---

### Analytics / Sessions

**Filtros**: `channel_type` · `period` · `pool` · `ANI` · `DNIS` · `agent_id`

- ANI/DNIS para sessions webhook: ANI = `customer_id`, DNIS = `skill_id` (o "DIN" do endpoint)

**Hierarquia**: lista de sessions → lista de segments → detalhe do segment

Analytics/Processes é eliminada. Workflows aparecem aqui com `channel_type: webhook`.

---

### Analytics / Pools

**Filtros**: `period` · `pools`

Análise de capacidade ao longo do tempo com granularidade configurável (hora / dia / mês).

**Queue**: máximo de sessões em fila por intervalo — gráfico + tabela com os pontos

**Resources ao longo do tempo**:
- Busy máximo por intervalo (gráfico + tabela)
- Total de recursos logados / capacidade configurada ao longo do tempo
  - Pools humanos/AI: total logado
  - Pools webhook: `max_concurrent_sessions` como linha de referência (capacidade)
- Available ao longo do tempo
- Busy ao longo do tempo

---

### Analytics / Agents

**Filtros**: `period` · `pools` · `channel`

Aplica-se a agentes humanos e AI. Skill-flow instances referenciadas via pool.

**Por agente — consolidado no período**:

| Métrica | Agregação |
|---|---|
| Login time | Total · Máximo · Média |
| Pause time | Total · Máximo · Média |
| TMA | Média |
| Wrap-up time | Total · Máximo · Média |
| Sessions atendidas | Total |
| Busy ao longo do tempo | Série temporal |
| Máximo de atendimentos por intervalo | Série temporal |
| Sessões simultâneas ao longo do tempo | Série temporal |

**Drill-down**: lista de segments que geraram os dados consolidados

---

### Analytics / Events

**Filtros**: `period` · `pool` · `channel` · `category` (regex sobre dot-notation)

Eventos de negócio do Arc 12. Nenhum evento interno de plataforma.

**Visualização**: dados brutos para construção de gráficos ao longo do tempo, com granularidade configurável.

**Gráficos por tipo**:
- `total` — série temporal de contagem de eventos
- `distribution` — série temporal de breakdown por valor
- `average` — série temporal de média de valor numérico

**Lista de categories** com valores, tipo e histórico.

**Drill-down**: lista de segments onde os eventos foram gerados — ponte Arc 12 → Arc 5 via `analytics.agent_business_events.session_id → analytics.segments.session_id`.

---

## Fases de Implementação

### Fase A — Canal webhook + adapter
- `channel-gateway`: `adapters/webhook.py` com endpoints trigger/resume/status
- `@plughub/schemas`: `channel_type` enum adiciona `"webhook"`
- `routing-engine`: tratar pools `channel_type: webhook` — aloca instâncias skill-flow

### Fase B — Status `suspended` no modelo de sessão
- `@plughub/schemas`: adicionar `"suspended"` ao SessionStatus
- Core: publicar `session_suspended` / `session_resumed` no stream
- Redis: TTL extension no suspend executor
- `resume_tokens` hash no executor suspend

### Fase C — orchestrator-bridge: skill-flow como agente nativo de pool webhook
- `instance_bootstrap.py`: reconhecer `channel_type: webhook` → criar instâncias skill-flow
- `process_routed()`: para pools webhook, iniciar skill-flow engine diretamente
- Eliminar `skill-flow-worker` Kafka consumer

### Fase D — workflow-api deprecation
- Redirecionar `/v1/workflow/trigger` → webhook adapter (compatibilidade)
- Migrar lógica `business_hours` para skill-flow-engine
- Manter `/v1/workflow/instances` como read-only (analytics histórica)

### Fase E — Monitor e Analytics unificados
- Remover páginas separadas Monitor/Processes e Analytics/Processes
- Adicionar filtro `channel_type` ao Monitor/Sessions e Analytics/Sessions
- Badge visual para sessões `suspended` no Monitor
- Monitor/Sessions: métricas Suspended, Failure, Timeout, Cancelled com mapeamento para `close_reason`
- Monitor/Pools: diferenciação entre pools humanos/AI (logados) e webhook (capacidade configurada)
- Analytics/Sessions: filtros ANI/DNIS com mapeamento por `channel_type`
- Analytics/Agents: série temporal + drill-down para segments
- Monitor/Events e Analytics/Events: visualização Arc 12 com filtro regex de category

### Fase F — Eliminação Journey + cleanup
- Remover entidade Journey conforme já especificado
- Remover `workflow.events` topic (substituído por eventos de sessão)
- Remover `skill-flow-worker` package

---

## Decisões em Aberto

1. **`session_type`** — ~~necessário campo separado?~~ **Fechado**: `channel_type: webhook` é suficiente para filtrar contact vs. workflow em queries SQL. Sem campo redundante.

2. **`parent_session_id`** — **Fechado**: fica na tabela `sessions` (não só no ContextStore) para joins analíticos diretos.

3. **Migração de instâncias ativas**: instâncias de workflow em execução no momento da migração precisam de um bridge — `workflow-api` serve ambos os formatos por um período de transição. Instâncias ativas no momento da migração continuam no modelo antigo até `complete()` ou timeout.

4. **Capacidade do pool webhook**: `max_concurrent_sessions` controla paralelismo. Skill-flow instances são stateless (estado no Redis), então o limite é de CPU/memória, não de conexões persistentes. Valor default razoável: 100.

5. **Validação de perfil workflow/agent no engine**: a validação dos step types proibidos por perfil pode ocorrer em (a) parse do YAML no deploy, (b) parse no engine ao iniciar execução, ou (c) ambos. Recomendação: ambos — erro de deploy para YAML inválido, guard no engine como safety net.

6. **TMA no Monitor/Sessions para `channel_type` misto**: quando o filtro inclui todos os canais, o TMA precisa usar lógica diferente por tipo — `SUM(segment.duration_ms)` para webhook, `session.ended_at - session.started_at` para contatos. A query de Monitor deve bifurcar por `channel_type` ou usar sempre a lógica de segments (mais consistente).

7. **`collect` sem canal explícito**: o `collect` step passa a usar capabilities negotiation (Arc 16) em vez de especificar `channel` diretamente. Requer que o pool de destino declare `channel_capability_requirements`. Para pools que ainda especificam canal explícito, manter suporte por compatibilidade até remoção do campo.

---

## Relação com Decisões Existentes

Este Arc implementa diretamente as duas entradas já registradas em `CLAUDE.md § Pending`:

- **Journey — Eliminação planejada**: Fase F deste Arc é o momento da eliminação.
- **Modelo Unificado de Contexto**: Este Arc é a implementação completa — `session_id` universal, `session_type` via `channel_type`, ContextStore unificado.
