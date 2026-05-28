# Arc 19 — Modelo Unificado de Sessão: Workflow como Canal Webhook

> Criado: 2026-05-28 · Estado: Especificação v1

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

### Monitor

```
Sessions (todas)
  ├── channel_type: webchat
  ├── channel_type: whatsapp
  ├── channel_type: voice
  ├── channel_type: webhook     ← processos de negócio
  └── channel_type: webrtc

Status: active | suspended | closed | abandoned
```

Sessões `suspended` aparecem no Monitor como processos em espera — visibilidade que hoje não existe para workflows.

### Analytics

`analytics.segments` já tem `session_id`, `pool_id`, `agent_type_id`, `started_at`, `ended_at`. Não muda nada. Workflows aparecem como segmentos com `pool_id = portabilidade_processo`.

`analytics.session_timeline` captura tudo via stream — inclusive os períodos `suspended` entre segmentos.

### ContextStore

`{tenant}:ctx:{session_id}` para tudo. Sem namespace `journey.*` separado. Um workflow invocado por `task` step de outro workflow usa o `session_id` do workflow pai como `parent_session_id` — hierarquia visível no trace sem FK especial.

---

## Collect Step no Novo Modelo

O `collect` step dispara um contato de saída via channel-gateway (exatamente como hoje), mas o link entre a sessão-filho e a sessão-pai workflow é via `parent_session_id`:

```
sessão workflow (session_type: workflow)
  └── parent_session_id: null

    collect step → cria sessão de contato (session_type: contact)
      └── parent_session_id: {workflow_session_id}
          channel_type: webchat | whatsapp | ...
```

Quando o cliente responde, a sessão-filho fecha e o webhook de `collect` resume a sessão workflow pai.

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

### Fase F — Eliminação Journey + cleanup
- Remover entidade Journey conforme já especificado
- Remover `workflow.events` topic (substituído por eventos de sessão)
- Remover `skill-flow-worker` package

---

## Decisões em Aberto

1. **`session_type`** campo na tabela `sessions`: necessário para filtrar contact vs workflow em queries SQL, ou suficiente filtrar por `channel_type = webhook`? Preferência: `channel_type` é suficiente — evita campo redundante.

2. **`parent_session_id`**: deve ficar na tabela `sessions` ou só no ContextStore? Recomendação: tabela, para joins analíticos diretos.

3. **Migração de instâncias ativas**: instâncias de workflow em execução no momento da migração precisam de um bridge — `workflow-api` serve ambos os formatos por um período de transição.

4. **Capacidade do pool webhook**: `max_concurrent_sessions` controla paralelismo. Skill-flow instances são stateless (estado no Redis), então o limite é de CPU/memória, não de conexões persistentes. Valor default razoável: 100.

---

## Relação com Decisões Existentes

Este Arc implementa diretamente as duas entradas já registradas em `CLAUDE.md § Pending`:

- **Journey — Eliminação planejada**: Fase F deste Arc é o momento da eliminação.
- **Modelo Unificado de Contexto**: Este Arc é a implementação completa — `session_id` universal, `session_type` via `channel_type`, ContextStore unificado.
