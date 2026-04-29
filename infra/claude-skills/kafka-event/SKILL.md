---
name: kafka-event
description: "Criar novo evento/tópico Kafka na plataforma PlugHub. Use quando o usuário pedir para publicar um novo evento, criar um consumidor, ou documentar um tópico."
license: Proprietary. LICENSE.txt has complete terms
---

# Skill: Novo Evento Kafka

## Quando usar

Use este skill sempre que o usuário pedir para:
- Criar um novo evento Kafka
- Registrar um novo tópico
- Adicionar schema de evento em Zod
- Documentar um fluxo de pub/sub
- Implementar um produtor ou consumidor

Exemplos de trigger:
- "Crie um evento para quando um workflow é concluído"
- "Adicione um novo tópico para notificações"
- "Documente o fluxo de `approval.requested`"

## Contrato de conformidade — OBRIGATÓRIO

Todo evento Kafka **DEVE** ter estes três campos base:

```typescript
event_id:   z.string().uuid()   // UUID único gerado pelo producer
tenant_id:  z.string()          // isolamento multi-tenant
timestamp:  z.string()          // ISO-8601 do evento
```

Exemplo mínimo válido:
```typescript
const MyEventSchema = z.object({
  event_id:   z.string().uuid(),
  tenant_id:  z.string(),
  timestamp:  z.string(),
  // seus campos específicos aqui
})
```

**Violação desta regra → rejeição no code review.**

## Passo a passo

### 1. Definir schema Zod em packages/schemas/src/

**Locais válidos para novos eventos:**

| Tipo de evento | Arquivo | Descrição |
|---|---|---|
| Conversas/routing | `kafka.ts` | Eventos de `conversations.*` |
| Ciclo de vida de agente | `kafka.ts` | Eventos de `agent.*` |
| Regras/escalação | `kafka.ts` | Eventos de `rules.*` |
| Uso/metering | `usage.ts` | Eventos de `usage.*` |
| Workflows/orquestração | `workflow.ts` | Eventos de `workflow.*` |
| Coleta de dados outbound | `workflow.ts` | Eventos de `collect.*` |
| Configuração | `config.ts` | Eventos de `config.*` |

Se nenhum desses arquivos se encaixa, **pergunte antes de criar novo arquivo**.

**Padrão de nomenclatura:**
- Nome do schema (Zod): `PascalCase` + `Schema` sufixo → `ApprovalRequestedEventSchema`
- Nome do tópico (Kafka): `kebab.case` com dots → `approval.requested`
- Nome do tipo TypeScript (export): `PascalCase` sem sufixo → `ApprovalRequestedEvent`
- Campo `event_type` no schema: snake_case string literal → `"approval_requested"`

**Código exemplo — evento de aprovação**

```typescript
// em packages/schemas/src/workflow.ts

// Schema Zod
export const ApprovalRequestedEventSchema = z.object({
  event_id:           z.string().uuid(),
  tenant_id:          z.string(),
  timestamp:          z.string().datetime(),
  
  // Campos específicos do evento
  workflow_id:        z.string(),
  instance_id:        z.string().uuid(),
  step_id:            z.string(),
  approver_role:      z.enum(['supervisor', 'manager']),
  request_reason:     z.string(),
  expires_at:         z.string().datetime(),
  
  event_type:         z.literal('approval_requested'),
})

export type ApprovalRequestedEvent = z.infer<typeof ApprovalRequestedEventSchema>
```

### 2. Exportar do index.ts

Arquivo: `packages/schemas/src/index.ts`

Adicionar ao final do arquivo (na seção correspondente):

```typescript
// ── Workflow Events ──────────────────────────────
export {
  ApprovalRequestedEventSchema,
  // ... outros eventos de workflow
} from './workflow'

export type {
  ApprovalRequestedEvent,
  // ... outras types de workflow
} from './workflow'
```

**Regra importante:** NUNCA use `export * from './file'`; sempre nomeie explicitamente.

### 3. Documentar em docs/kafka-eventos.md

Localização: `plughub/docs/kafka-eventos.md`

Adicionar uma nova seção para o tópico. Padrão de seção:

```markdown
## `approval.requested`

**Propósito**: Solicitar aprovação de um passo suspenso do workflow de um supervisor ou manager.

**Produtores**:
- `skill-flow-worker` — após executar um `suspend` step com `reason: "approval"`

**Consumidores**:
- `mcp-server-plughub` — fornece MCP tool `approval_request_list` para supervisores
- `analytics-api` — indexa em `workflow_events` para relatórios

**Grupo de consumo**: `approval-processor`

### Schema — ApprovalRequestedEvent

```json
{
  "event_id":       "uuid",
  "tenant_id":      "string",
  "timestamp":      "ISO-8601",
  "workflow_id":    "string",
  "instance_id":    "uuid",
  "step_id":        "string",
  "approver_role":  "supervisor | manager",
  "request_reason": "string",
  "expires_at":     "ISO-8601"
}
```

**Campo descritivo**:

| Campo | Tipo | Descrição |
|---|---|---|
| `event_id` | UUID | ID único do evento |
| `tenant_id` | string | Isolamento multi-tenant |
| `timestamp` | ISO-8601 | Quando a aprovação foi solicitada |
| `workflow_id` | string | ID do fluxo (e.g., `fluxo_financeiro_v1`) |
| `instance_id` | UUID | ID da instância de workflow |
| `step_id` | string | ID do passo suspenso (e.g., `aguardar_aprovacao`) |
| `approver_role` | enum | Função do aprovador |
| `request_reason` | string | Razão legível para humano |
| `expires_at` | ISO-8601 | Quando a aprovação expira |
```

### 4. Registrar tópico em docker-compose

**Arquivo:** `docker-compose.test.yml`, `docker-compose.demo.yml`

Localizar seção `kafka-init` e adicionar ao script:

```yaml
kafka-init:
  image: confluentinc/cp-kafka:7.0.0
  ...
  environment:
    ...
    KAFKA_TOPICS: |
      conversations.inbound:3:1
      conversations.routed:3:1
      approval.requested:3:1
```

Format: `{topic_name}:{partitions}:{replication_factor}`

**Configuração padrão para PlugHub:**
- `partitions: 3` — paralelismo padrão (3 = bom para 3-9 workers)
- `replication_factor: 1` — em dev/test; produção usa 3

Se usar valores diferentes (ex: alto volume → 12 partições), documentar a razão.

### 5. Implementar produtor (exemplo TypeScript)

Arquivo apropriado (ex: `packages/skill-flow-worker/src/emitter.ts`)

```typescript
import { Kafka } from 'kafkajs'
import { ApprovalRequestedEventSchema } from '@plughub/schemas'

const kafka = new Kafka({
  brokers: process.env.PLUGHUB_KAFKA_BROKERS?.split(',') ?? ['localhost:9092'],
  clientId: 'skill-flow-worker',
})

const producer = kafka.producer()

export async function emitApprovalRequested(
  tenantId: string,
  workflowId: string,
  instanceId: string,
  stepId: string,
  approverRole: 'supervisor' | 'manager',
  requestReason: string,
  expiresAt: Date
): Promise<void> {
  const event = ApprovalRequestedEventSchema.parse({
    event_id:       crypto.randomUUID(),
    tenant_id:      tenantId,
    timestamp:      new Date().toISOString(),
    workflow_id:    workflowId,
    instance_id:    instanceId,
    step_id:        stepId,
    approver_role:  approverRole,
    request_reason: requestReason,
    expires_at:     expiresAt.toISOString(),
  })

  await producer.send({
    topic: 'approval.requested',
    messages: [
      {
        key: instanceId,  // particionado por instance para ordem
        value: JSON.stringify(event),
      }
    ]
  })
}
```

**Padrão obrigatório:**
- Use `crypto.randomUUID()` para `event_id`
- Use `.toISOString()` para timestamps
- Valide com Zod antes de enviar
- Publique com `key = instance_id` ou `session_id` para **garantir ordem** dentro da partição
- Fire-and-forget ou trate erro com log + retry — nunca lance em handler síncrono

### 6. Implementar consumidor (exemplo)

Padrão do `analytics-api` (Python + asyncio):

```python
# em packages/analytics-api/src/plughub_analytics_api/consumer.py

from aiokafka import AIOKafkaConsumer
import json
from plughub_schemas import ApprovalRequestedEventSchema

async def consume_approval_requested():
    consumer = AIOKafkaConsumer(
        'approval.requested',
        bootstrap_servers=['localhost:9092'],
        group_id='approval-processor',
        value_deserializer=lambda m: json.loads(m.decode('utf-8'))
    )
    
    async with consumer:
        async for message in consumer:
            try:
                event = ApprovalRequestedEventSchema.parse_obj(message.value)
                await process_approval_request(event)
                await consumer.commit()  # commit após sucesso
            except Exception as e:
                logger.error(f"Failed to process approval event: {e}")
                # NÃO commit — será retentado
```

**Padrões críticos:**
- Use `group_id` único por consumidor lógico (ex: `approval-processor`)
- `commit()` apenas após sucesso
- Trate erros sem perder eventos — log + requeue automático
- Valide schema com Zod/Pydantic antes de processar

## Exemplo completo: evento `approval.completed`

### Schema em Zod (workflow.ts)

```typescript
export const ApprovalCompletedEventSchema = z.object({
  event_id:         z.string().uuid(),
  tenant_id:        z.string(),
  timestamp:        z.string().datetime(),
  
  workflow_id:      z.string(),
  instance_id:      z.string().uuid(),
  step_id:          z.string(),
  decision:         z.enum(['approved', 'rejected']),
  approver_id:      z.string(),
  reason:           z.string().optional(),
  approved_at:      z.string().datetime(),
})

export type ApprovalCompletedEvent = z.infer<typeof ApprovalCompletedEventSchema>
```

### Exportar em index.ts

```typescript
export {
  ApprovalCompletedEventSchema,
} from './workflow'

export type {
  ApprovalCompletedEvent,
} from './workflow'
```

### Documentar em kafka-eventos.md

```markdown
## `approval.completed`

**Propósito**: Notificar que uma aprovação foi concluída (aprovado ou rejeitado).

**Produtores**:
- `mcp-server-plughub` — após receber `POST /approval_submit` de supervisor

**Consumidores**:
- `skill-flow-worker` — resume workflow com decisão via `engine.run(resumeContext)`
- `analytics-api` — indexa para auditoria e relatórios

**Grupo de consumo**: `approval-completer`

### Schema — ApprovalCompletedEvent

```json
{
  "event_id":    "uuid",
  "tenant_id":   "string",
  "timestamp":   "ISO-8601",
  "workflow_id": "string",
  "instance_id": "uuid",
  "step_id":     "string",
  "decision":    "approved | rejected",
  "approver_id": "string",
  "reason":      "string | null",
  "approved_at": "ISO-8601"
}
```

| Campo | Tipo | Descrição |
|---|---|---|
| `event_id` | UUID | ID único do evento |
| `decision` | enum | Resultado da aprovação |
| `approver_id` | string | ID do supervisor que aprovou/rejeitou |
| `reason` | string | Motivo opcional (ex: "esperando informação do cliente") |
```

### Produtor (mcp-server-plughub)

```typescript
export async function emitApprovalCompleted(
  tenantId: string,
  workflowId: string,
  instanceId: string,
  stepId: string,
  decision: 'approved' | 'rejected',
  approverId: string,
  reason?: string
): Promise<void> {
  const event = ApprovalCompletedEventSchema.parse({
    event_id:    crypto.randomUUID(),
    tenant_id:   tenantId,
    timestamp:   new Date().toISOString(),
    workflow_id: workflowId,
    instance_id: instanceId,
    step_id:     stepId,
    decision:    decision,
    approver_id: approverId,
    reason:      reason ?? null,
    approved_at: new Date().toISOString(),
  })

  await producer.send({
    topic: 'approval.completed',
    messages: [{
      key: instanceId,
      value: JSON.stringify(event)
    }]
  })
}
```

## Mapa de tópicos atuais

| Tópico | Produtores | Consumidores | Stage |
|---|---|---|---|
| `conversations.inbound` | channel-gateway | routing-engine | V2 |
| `conversations.routed` | routing-engine | mcp-server, skill-flow | V2 |
| `conversations.queued` | routing-engine | analytics | V2 |
| `conversations.events` | mcp-server-plughub | insight-consumer | V2 |
| `agent.lifecycle` | mcp-server-plughub | routing-engine | V2 |
| `agent.registry.events` | agent-registry | routing-engine | V2 |
| `rules.escalation.events` | rules-engine | audit | V2 |
| `usage.events` | core, ai-gateway, channel-gateway | usage-aggregator | Arc 2 |
| `sentiment.updated` | ai-gateway | analytics-api | Arc 3 |
| `conversations.participants` | orchestrator-bridge | analytics-api | Arc 3 |
| `workflow.events` | workflow-api | skill-flow-worker | Arc 4 |
| `collect.events` | workflow-api | analytics-api | Arc 4 |
| `config.changed` | config-api | orchestrator-bridge, routing-engine | Arc 4 |

## Padrão de Zod — tipos compostos

Para eventos com estruturas complexas, use discriminated unions:

```typescript
const BaseEventFields = z.object({
  event_id:  z.string().uuid(),
  tenant_id: z.string(),
  timestamp: z.string().datetime(),
})

export const WorkflowEventSchema = z.discriminatedUnion('event_type', [
  BaseEventFields.extend({
    event_type: z.literal('workflow_started'),
    workflow_id: z.string(),
    instance_id: z.string().uuid(),
  }),
  BaseEventFields.extend({
    event_type: z.literal('workflow_completed'),
    workflow_id: z.string(),
    instance_id: z.string().uuid(),
    outcome: z.enum(['resolved', 'escalated']),
  }),
])

export type WorkflowEvent = z.infer<typeof WorkflowEventSchema>
```

## Checklist antes de declarar pronto

- [ ] Schema tem `event_id` (UUID), `tenant_id` (string), `timestamp` (ISO-8601)
- [ ] Schema está em arquivo correto (`kafka.ts`, `workflow.ts`, etc.)
- [ ] Tipo TypeScript (type alias) exportado
- [ ] Entrada em `packages/schemas/src/index.ts` com nome explícito
- [ ] Tópico documentado em `docs/kafka-eventos.md` com seção completa
- [ ] Tópico registrado em `docker-compose.test.yml` e `docker-compose.demo.yml`
- [ ] Produtor valida com Zod antes de publicar
- [ ] Produtor usa UUID para `event_id` e ISO-8601 para timestamps
- [ ] Consumidor faz `commit()` apenas após sucesso
- [ ] Consumidor usa `group_id` único (não reutilizado)
- [ ] Nenhum evento vazando para `stream` (apenas através de Kafka)
- [ ] Partição = `instance_id` ou `session_id` para garantir ordem

## Erros comuns

| Erro | Causa | Solução |
|---|---|---|
| Evento chega fora de ordem | Chave de partição errada (random UUID) | Use `key = instance_id` ou `session_id` |
| Eventos duplicados | Commit antes de processar | Commit APÓS processar com sucesso |
| Schema validation falha no consumer | Tipo mismatch (int vs string) | Validar tipo em produtor com Zod |
| Tópico não existe em docker-compose | Não registrado na seção `KAFKA_TOPICS` | Adicionar ao script `kafka-init` |
| `event_type` string vs enum mismatch | Zod parse faz hardcheck | Usar `z.literal('exact_string')` |
| Timestamp sem timezone | `.toISOString()` retorna UTC | Sempre usar `.toISOString()` (não `.toString()`) |

## Anti-padrões

❌ **Gerar UUID aleatório para chave de partição**
  - ✅ Usar `key = instance_id` ou `session_id` para ordem garantida

❌ **Não validar schema no produtor**
  ```typescript
  // ERRADO:
  await producer.send({ topic: 'x', messages: [{ value: JSON.stringify(obj) }] })
  // CERTO:
  const event = EventSchema.parse(obj)
  await producer.send({ topic: 'x', messages: [{ value: JSON.stringify(event) }] })
  ```

❌ **Fazer commit antes de processar**
  ```typescript
  // ERRADO:
  await consumer.commit()
  await process(message)
  // CERTO:
  await process(message)
  await consumer.commit()
  ```

❌ **Escrever timestamp com `.toString()`**
  - ✅ Sempre `.toISOString()` para formato consistente

❌ **Omitir event_id, tenant_id, ou timestamp**
  - ✅ Esses três campos são OBRIGATÓRIOS em TODOS os eventos

❌ **Criar novo arquivo de schema para um único evento**
  - ✅ Agrupar por categoria (`workflow.ts`, `usage.ts`, etc.)
