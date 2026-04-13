# PlugHub — Tópicos Kafka e Schemas de Eventos

> Broker padrão: `localhost:9092` (configurável via `PLUGHUB_KAFKA_BROKERS`)
> Formato: JSON — `value_serializer = json.dumps().encode("utf-8")`
> Chave de partição: `session_id` quando disponível (garante ordem por sessão)

---

## Sumário de Tópicos

| Tópico | Produtores | Consumidores | Propósito |
|---|---|---|---|
| [`conversations.inbound`](#conversationsinbound) | channel-gateway, crash-detector | routing-engine | Eventos inbound normalizados para roteamento |
| [`conversations.routed`](#conversationsrouted) | routing-engine | mcp-server, skill-flow | Decisão de roteamento com alocação |
| [`conversations.queued`](#conversationsqueued) | routing-engine | routing-engine | Contatos não alocados (pool saturado) |
| [`conversations.events`](#conversationsevents) | mcp-server-plughub | insight-consumer, analytics | Conclusão de atendimento |
| [`agent.lifecycle`](#agentlifecycle) | mcp-server-plughub, crash-detector | routing-engine (kafka_listener) | Transições de ciclo de vida de instâncias |
| [`agent.registry.events`](#agentregistryevents) | agent-registry | routing-engine (kafka_listener) | Registro e atualização de pools e tipos |
| [`rules.escalation.events`](#rulesescalationevents) | rules-engine | audit, monitoring | Escalações disparadas (modo ativo) |
| [`rules.shadow.events`](#rulesshadowevents) | rules-engine | monitoring | Disparos em shadow mode (sem ação real) |

---

## `conversations.inbound`

**Propósito**: Ponto de entrada de toda conversa na plataforma. O Routing Engine consome este tópico como único árbitro de alocação.

**Produtores**:
- `channel-gateway` — toda mensagem inbound de canal (WhatsApp, SMS, web chat, email, voice), incluindo `MenuSubmitEvent`
- `routing-engine` (CrashDetector) — reencaminhamento de conversas órfãs após crash de instância
- `mcp-server-plughub` (BPM `conversation_start`) — início explícito via API

**Consumidores**:
- `routing-engine` — consome como tópico principal; processa cada evento como `ConversationInboundEvent`

**Grupo de consumo**: `routing-engine`

### Schema — ConversationInboundEvent

```json
{
  "session_id":      "uuid",
  "tenant_id":       "string",
  "customer_id":     "string",
  "channel":         "chat | whatsapp | sms | voice | email | webrtc",
  "intent":          "string | null",
  "confidence":      0.0,
  "customer_profile": {
    "tier":           "platinum | gold | standard",
    "churn_risk":     0.0,
    "ltv":            null,
    "business_score": 0.0,
    "risk_flag":      false
  },
  "process_context": {
    "process_id":       "string | null",
    "process_instance": "string | null",
    "status":           "string | null",
    "payload":          { }
  },
  "requirements":    { "competency_name": 1 },
  "started_at":      "ISO datetime",
  "elapsed_ms":      0,
  "timestamp":       "ISO datetime"
}
```

### Variante — MenuSubmitEvent (canal → plataforma)

Publicado pelo channel-gateway após coleta completa de um step `menu`. Indistinguível de um evento inbound regular do ponto de vista do Routing Engine.

```json
{
  "session_id":   "uuid",
  "tenant_id":    "string",
  "channel":      "whatsapp | sms | webchat | email",
  "interaction":  "text | button | list | checklist | form",
  "result":       "string | string[] | object",
  "timestamp":    "ISO datetime"
}
```

### Variante — Requeue (crash recovery)

Publicado pelo CrashDetector para re-rotear conversas órfãs. Usa `channel: "chat"` e `elapsed_ms: 0` como valores padrão.

```json
{
  "session_id":  "uuid",
  "tenant_id":   "string",
  "customer_id": "",
  "channel":     "chat",
  "started_at":  "ISO datetime",
  "elapsed_ms":  0
}
```

---

## `conversations.routed`

**Propósito**: Resultado da decisão de roteamento para conversas alocadas com sucesso.

**Produtor**: `routing-engine` — publicado após `router.route()` retornar `allocated: true`

**Consumidores**:
- `mcp-server-plughub` — recebe a alocação para iniciar o handoff ao agente
- `skill-flow-engine` — confirma o agente selecionado para pipelines de orquestração

### Schema — ConversationRoutedEvent

```json
{
  "session_id": "uuid",
  "tenant_id":  "string",
  "result": {
    "session_id":      "uuid",
    "tenant_id":       "string",
    "allocated":       true,
    "instance_id":     "uuid",
    "agent_type_id":   "string",
    "pool_id":         "string",
    "resource_score":  0.0,
    "priority_score":  0.0,
    "routing_mode":    "autonomous | hybrid | supervised",
    "cross_site":      false,
    "allocated_site":  "string | null",
    "queued":          false,
    "queue_eta_ms":    null,
    "routed_at":       "ISO datetime"
  },
  "routed_at": "ISO datetime"
}
```

**`routing_mode`** — determina o nível de supervisão humana da sessão:

| Modo | Condição | Ação |
|---|---|---|
| `autonomous` | `confidence >= 0.85` e `risk_flag == false` | AI opera sem supervisão |
| `hybrid` | `0.60 <= confidence < 0.85` | Re-avaliação a cada 5 turnos |
| `supervised` | `confidence < 0.60` ou `risk_flag == true` | Re-avaliação a cada turno |

---

## `conversations.queued`

**Propósito**: Notifica que um contato não pôde ser alocado (pool saturado ou sem instâncias disponíveis) e foi colocado na fila.

**Produtor**: `routing-engine` — publicado após `router.route()` retornar `allocated: false`

**Consumidores**:
- `routing-engine` — reprocessa quando uma instância fica disponível (Cenário 2: recurso disponível → `router.dequeue()`)

### Schema

Mesmo schema de `ConversationRoutedEvent`, mas com `result.allocated: false` e `result.queued: true`:

```json
{
  "session_id": "uuid",
  "tenant_id":  "string",
  "result": {
    "session_id":    "uuid",
    "tenant_id":     "string",
    "allocated":     false,
    "queued":        true,
    "routing_mode":  "supervised",
    "routed_at":     "ISO datetime"
  },
  "routed_at": "ISO datetime"
}
```

O contato é armazenado no Redis Sorted Set `{tenant_id}:pool:{pool_id}:queue` (score = `queued_at_ms`) e no Redis String `{tenant_id}:queue_contact:{session_id}` para re-priorização e dequeue.

---

## `conversations.events`

**Propósito**: Eventos de ciclo de vida de atendimento. Principal tópico de integração para sistemas externos, auditoria e processamento de insights.

**Produtor**: `mcp-server-plughub` — publicado após `agent_done`

**Consumidores**:
- Insight consumer (plataforma) — promove `insight.conversa.*` → `insight.historico.*` no fechamento do contato
- Sistemas de analytics e auditoria externos

> **Invariante** (`CLAUDE.md`): `insight.historico.*` persiste **via Kafka, nunca via escrita direta em PostgreSQL**. O consumer processa `insight.conversa.*` → `insight.historico.*` no fechamento do contato (`contact_closed`). O limite de persistência é o contato, não a sessão.

### Schema — `conversation_completed`

```json
{
  "event":           "conversation_completed",
  "tenant_id":       "string",
  "instance_id":     "uuid",
  "conversation_id": "uuid",
  "outcome":         "resolved | escalated | abandoned | transferred",
  "issue_status": [
    {
      "issue_id":    "string",
      "description": "string",
      "resolved":    true
    }
  ],
  "handoff_reason":  "string | null",
  "completed_at":    "ISO datetime",
  "timestamp":       "ISO datetime"
}
```

Regras de `outcome` e `handoff_reason` (espelham o Zod refinement de `AgentDoneSchema`):
- `outcome != "resolved"` → `handoff_reason` é obrigatório
- `issue_status` tem mínimo de 1 item — nunca vazio

---

## `agent.lifecycle`

**Propósito**: Transições de ciclo de vida de instâncias de agente. Canal principal de comunicação entre `mcp-server-plughub` e `routing-engine`.

**Produtores**:
- `mcp-server-plughub` — para todos os eventos normais de ciclo de vida
- `routing-engine` (CrashDetector) — evento `agent_crash` ao detectar instância sem heartbeat

**Consumidores**:
- `routing-engine` (kafka_listener) — atualiza Redis `{tenant_id}:instance:{instance_id}` (TTL 30s) e gerencia sets de pool

**Grupo de consumo**: `routing-engine-listener`

### Schemas por evento

#### `agent_login`
```json
{
  "event":         "agent_login",
  "tenant_id":     "string",
  "agent_type_id": "string",
  "instance_id":   "uuid",
  "timestamp":     "ISO datetime"
}
```

#### `agent_ready`
```json
{
  "event":                    "agent_ready",
  "tenant_id":                "string",
  "instance_id":              "uuid",
  "agent_type_id":            "string",
  "pools":                    ["pool_id"],
  "execution_model":          "stateless | stateful",
  "max_concurrent_sessions":  1,
  "current_sessions":         0,
  "status":                   "ready",
  "timestamp":                "ISO datetime"
}
```
> **Efeito no routing-engine**: atualiza Redis com TTL 30s + popula `{tenant_id}:pool:{pool_id}:instances` + atualiza metadata sem TTL.

#### `agent_busy`
```json
{
  "event":            "agent_busy",
  "tenant_id":        "string",
  "instance_id":      "uuid",
  "conversation_id":  "uuid",
  "current_sessions": 1,
  "timestamp":        "ISO datetime"
}
```
> **Efeito no routing-engine**: renova TTL 30s + incrementa `current_sessions` + adiciona `conversation_id` ao SET de conversas ativas.

#### `agent_done`
```json
{
  "event":            "agent_done",
  "tenant_id":        "string",
  "instance_id":      "uuid",
  "conversation_id":  "uuid",
  "current_sessions": 0,
  "timestamp":        "ISO datetime"
}
```
> **Efeito no routing-engine**: remove `conversation_id` do SET de conversas ativas.

#### `agent_pause`
```json
{
  "event":       "agent_pause",
  "tenant_id":   "string",
  "instance_id": "uuid",
  "timestamp":   "ISO datetime"
}
```
> **Efeito no routing-engine**: remove instância de todos os pool sets (para de receber novas conversas).

#### `agent_logout`
```json
{
  "event":           "agent_logout",
  "tenant_id":       "string",
  "instance_id":     "uuid",
  "state":           "logged_out | draining",
  "active_sessions": 0,
  "timestamp":       "ISO datetime"
}
```

#### `agent_heartbeat`
```json
{
  "event":       "agent_heartbeat",
  "tenant_id":   "string",
  "instance_id": "uuid",
  "status":      "ready | busy",
  "timestamp":   "ISO datetime"
}
```
> Deve ser enviado a cada ~10s em estado `ready` ou `busy`. A ausência por 30s é detectada pelo CrashDetector como crash.

#### `agent_crash` (produzido pelo CrashDetector)
```json
{
  "event":                       "agent_crash",
  "tenant_id":                   "string",
  "instance_id":                 "uuid",
  "agent_type_id":               "string",
  "recovered_conversation_ids":  ["uuid"],
  "timestamp":                   "ISO datetime"
}
```
> Publicado pelo routing-engine após detectar ausência de heartbeat. Cada `conversation_id` em `recovered_conversation_ids` foi re-publicado em `conversations.inbound`.

---

## `agent.registry.events`

**Propósito**: Notifica mudanças de configuração do Agent Registry. O Routing Engine consome este tópico para manter seu cache Redis de pools sempre atualizado, sem acesso direto ao PostgreSQL.

**Produtor**: `agent-registry` — após criação ou atualização de Pool ou AgentType

**Consumidores**:
- `routing-engine` (kafka_listener) — atualiza `{tenant_id}:pool_config:{pool_id}` no Redis (TTL 5min)

**Grupo de consumo**: `routing-engine-listener`

### Schema — `pool.registered` / `pool.updated`

```json
{
  "event":     "pool.registered | pool.updated",
  "tenant_id": "string",
  "pool": {
    "pool_id":        "string",
    "channel_types":  ["chat", "whatsapp"],
    "sla_target_ms":  3000,
    "routing_expression": {
      "weight_sla":      1.0,
      "weight_wait":     0.8,
      "weight_tier":     0.6,
      "weight_churn":    0.9,
      "weight_business": 0.4
    },
    "supervisor_config": null
  }
}
```

> **Efeito no routing-engine**: `save_pool_config()` → atualiza `{tenant_id}:pool_config:{pool_id}` + adiciona `pool_id` ao set `{tenant_id}:pools`.

### Schema — `agent_type.registered`

```json
{
  "event":       "agent_type.registered",
  "tenant_id":   "string",
  "agent_type": {
    "agent_type_id":          "string",
    "execution_model":        "stateless | stateful",
    "max_concurrent_sessions": 1,
    "pools":                  ["pool_id"],
    "capabilities":           { }
  }
}
```

---

## `rules.escalation.events`

**Propósito**: Registra cada escalação efetivamente disparada pelo Rules Engine (modo `active`). Usado para auditoria e monitoramento operacional.

**Produtor**: `rules-engine` (Escalator) — somente em modo `active` com `target_pool` definido

**Consumidores**: sistemas de auditoria, dashboards operacionais

### Schema — EscalationTrigger

```json
{
  "session_id":   "uuid",
  "tenant_id":    "string",
  "rule_id":      "string",
  "rule_name":    "string",
  "target_pool":  "pool_id",
  "shadow_mode":  false,
  "triggered_at": "ISO datetime",
  "context": {
    "session_id":         "uuid",
    "tenant_id":          "string",
    "turn_count":         5,
    "elapsed_ms":         12000,
    "sentiment_score":    -0.8,
    "intent_confidence":  0.4,
    "flags":              ["high_frustration"],
    "sentiment_history":  [-0.2, -0.5, -0.8]
  }
}
```

---

## `rules.shadow.events`

**Propósito**: Registra disparos de regras em modo `shadow` — o que teria acontecido, sem ação real. Usado para validar novas regras antes de ativá-las.

**Produtor**: `rules-engine` (Escalator) — somente em modo `shadow`

**Consumidores**: ferramentas de análise de regras, dry-run histórico

### Schema

Idêntico a `rules.escalation.events`, mas com `shadow_mode: true`.

```json
{
  "session_id":   "uuid",
  "tenant_id":    "string",
  "rule_id":      "string",
  "rule_name":    "string",
  "target_pool":  "pool_id",
  "shadow_mode":  true,
  "triggered_at": "ISO datetime",
  "context":      { ... }
}
```

---

## Fluxo de Eventos — Atendimento Padrão

```
1. channel-gateway       → conversations.inbound        (nova mensagem do cliente)
2. routing-engine        → conversations.routed         (alocação bem-sucedida)
   ou routing-engine     → conversations.queued         (pool saturado)

3. mcp-server            → agent.lifecycle (agent_login)
4. mcp-server            → agent.lifecycle (agent_ready)
5. routing-engine        → agent.lifecycle (agent_busy)  ← via kafka_listener
   [atendimento em curso]
6. mcp-server            → agent.lifecycle (agent_done)
7. mcp-server            → conversations.events         (conversation_completed)
8. insight-consumer      ← conversations.events         (promove insight.conversa.* → insight.historico.*)
```

## Fluxo de Eventos — Crash de Instância

```
1. [heartbeat ausente por 30s — TTL do Redis expirou]
2. routing-engine        → agent.lifecycle (agent_crash)
3. routing-engine        → conversations.inbound        (requeue de cada conversa órfã)
4. routing-engine        → conversations.routed         (nova alocação)
```

## Fluxo de Eventos — Escalação por Regra

```
1. rules-engine          ← Redis pub/sub session:{id}:ai  (parâmetros do turno)
2. [regra dispara: sentiment_score < -0.7 por 3 turnos]
3. rules-engine          → rules.escalation.events      (modo active)
   ou rules-engine       → rules.shadow.events          (modo shadow)
4. rules-engine          → mcp-server conversation_escalate  (modo active apenas)
5. routing-engine        → conversations.routed         (novo pool de destino)
```

