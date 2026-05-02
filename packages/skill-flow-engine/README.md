# @plughub/skill-flow-engine

Interpretador de Skill Flow da **PlugHub Platform**.
Lê o campo `flow` de uma skill de orquestração e executa o grafo declarativo de steps.

## Uso

```typescript
import { SkillFlowEngine } from "@plughub/skill-flow-engine"
import Redis               from "ioredis"

const engine = new SkillFlowEngine({
  redis:         new Redis(process.env.REDIS_URL),
  mcpCall:       (tool, input, server) => mcpClient.call(tool, input, server),
  aiGatewayCall: (payload) => aiGateway.reason(payload),
})

const { outcome, pipeline_state } = await engine.run({
  sessionId:      session.id,
  customerId:     session.customer_id,
  skillId:        "skill_onboarding_finserv_v1",
  flow:           skill.flow,
  sessionContext: session.context,
})
```

## Retomada automática

Se existe `pipeline_state` ativo no Redis para a sessão, o engine retoma
do step corrente em vez de reiniciar do `entry`. Falhas do orquestrador
— timeout, crash, redeploy — são transparentes para o cliente.

## Steps suportados (8 tipos)

| Type | O que faz |
|---|---|
| `task` | Delega para agente com skill via A2A (fire-and-poll) |
| `choice` | Ramificação condicional por JSONPath |
| `catch` | Retry e fallback antes de escalar |
| `escalate` | Deriva para pool via Rules Engine |
| `complete` | Encerra o pipeline |
| `invoke` | Chama tool MCP diretamente |
| `reason` | Invoca AI Gateway com output_schema |
| `notify` | Envia mensagem ao cliente |

## Spec de referência

- 4.7  — schema do Skill Flow e campos de cada step
- 9.5i — execução de steps task, escalate, catch e retomada
