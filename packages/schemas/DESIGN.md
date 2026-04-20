# schemas — Design v2

Fonte da verdade: `plughub_spec_v1.docx` (v1.2)

## Estrutura de arquivos

```
src/
  common.ts          ← primitivos base (sem dependências locais)
  audit.ts           ← AuditPolicy, DataCategory, MaskingRule
  message.ts         ← Message, MessageContent, MessageVisibility
  session.ts         ← Session, Participant, SessionContext, CustomerIdentity, SentimentEntry
  stream.ts          ← StreamEvent, StreamEventType, Author
  channel-events.ts  ← InboundEvent, OutboundEvent, GatewayHeartbeat, ChannelCapabilities
  routing.ts         ← AssignmentTicket, AgentState, QueueEntry
  ai-gateway.ts      ← ModelConfig, ModelEntry, AIInferInput, AIInferOutput
  agent-registry.ts  ← AgentType, Pool, Skill, GatewayConfig (reescrito)
  skill.ts           ← FlowStep types (atualizado: TaskStep.mode, menu timeout)

  # Legado (mantido até substituição dos consumidores)
  context-package.ts ← @deprecated — será removido após migração
```

## Dependências entre arquivos

```
common.ts
  └─ audit.ts
  └─ message.ts
       └─ session.ts
            └─ stream.ts
  └─ channel-events.ts
  └─ routing.ts
  └─ ai-gateway.ts (← audit.ts)
  └─ agent-registry.ts (← common.ts, skill.ts)
```

## Decisões

- `session_id` format: `sess_{YYYYMMDD}T{HHMMSS}_{ulid}` — validado por regex
- Sentimento: score numérico apenas, label calculado na leitura com faixas configuráveis por tenant
- `MessageVisibility`: `"all" | "agents_only" | string[]` — lista é modalidade distinta
- `TaskStep.mode`: `"assist" | "transfer"` obrigatório
- `MenuStep.timeout_s`: `0`=imediato, `>0`=N segundos, `-1`=indefinido
- `AIInferInput.model_config.models`: lista ordenada de fallback definida pelo caller
