# @plughub/schemas

Schemas Zod para a **PlugHub Platform** — fonte de verdade para todos os contratos de dados da plataforma.

Qualquer componente que produz ou consome esses objetos deve importar daqui. Nunca redefina localmente.

## Instalação

```bash
npm install @plughub/schemas
# ou
pnpm add @plughub/schemas
```

Dependência: `zod >= 3.23`

## Uso

```typescript
import {
  ContextPackageSchema,
  AgentDoneSchema,
  SkillSchema,
  AgentTypeRegistrationSchema,
  PipelineStateSchema,
} from "@plughub/schemas"

// Validar um context_package recebido do Redis
const pkg = ContextPackageSchema.parse(rawJson)

// Validar um agent_done antes de publicar no Kafka
const done = AgentDoneSchema.parse(payload)

// Registrar uma skill
const skill = SkillSchema.parse(skillConfig)
```

## Schemas disponíveis

| Schema | Fonte | Descrição |
|---|---|---|
| `ContextPackageSchema` | spec 3.4 | Estado da sessão entregue ao agente |
| `AgentDoneSchema` | spec 4.2 | Sinal de conclusão do agente |
| `SessionItemSchema` | spec 3.4a | Insight ou pending delivery |
| `SkillSchema` | spec 4.7 | Skill com ou sem flow de orquestração |
| `SkillFlowSchema` | spec 4.7 | Flow declarativo de um orquestrador |
| `FlowStepSchema` | spec 4.7 | Step discriminado (8 tipos) |
| `PoolRegistrationSchema` | spec 4.5 | Registro de pool |
| `AgentTypeRegistrationSchema` | spec 4.5 | Registro de tipo de agente |
| `PipelineStateSchema` | spec 4.7/9.5i | Estado do pipeline no Redis |

## Rodar os testes

```bash
npm install
npx vitest run
```

## Tipos exportados

Todos os schemas exportam o tipo inferido correspondente:

```typescript
import type {
  ContextPackage,
  AgentDone,
  SessionItem,
  Skill,
  SkillFlow,
  FlowStep,
  PoolRegistration,
  AgentTypeRegistration,
  PipelineState,
  Channel,
  Outcome,
  InsightConfidence,
  AgentRole,
  AgentFramework,
} from "@plughub/schemas"
```

## Validações de runtime não cobertas pelos schemas

As seguintes validações dependem de estado externo e são responsabilidade da API administrativa, não dos schemas Zod:

- `skill_id` em `AgentTypeRegistration.skills` deve existir no Skill Registry
- `pool_id` em `AgentTypeRegistration.pools` deve existir no Agent Registry
- `mcp_server` em `SkillTool` deve estar registrado no tenant
- `evaluation_template_id` deve existir no template store

## Convenções de nomenclatura

```
skill_id:       skill_{nome}_v{n}     →  skill_portabilidade_telco_v2
agent_type_id:  {nome}_v{n}           →  agente_retencao_v1
pool_id:        snake_case sem versão →  retencao_humano
insight:        insight.historico.*   →  memória de longo prazo
                insight.conversa.*    →  gerada na sessão atual
outbound:       outbound.*            →  pending deliveries
```
