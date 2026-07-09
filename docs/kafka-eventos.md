# PlugHub — Tópicos Kafka e Schemas de Eventos

> Última atualização: 2026-07-09 · Estado: Arc 19 (modelo unificado de sessão)
> Broker padrão: `localhost:9092` (configurável via `PLUGHUB_KAFKA_BROKERS`)
> Formato: JSON — `value_serializer = json.dumps().encode("utf-8")`
> Chave de partição: `session_id` quando disponível (garante ordem por sessão)
>
> **Modelo unificado (Arc 19).** Workflow deixou de ser uma entidade/lifecycle próprio e virou o canal
> `webhook` da channel-gateway: o `session_id` é o identificador persistente por toda a execução (inclusive
> ciclos de suspend/resume), o status `suspended` foi adicionado ao domínio de sessão, e o skill-flow roda
> como **agente nativo via `orchestrator-bridge`**. Consequências para este documento:
> - **`journey.events` (9 tipos, Arc 10) — REMOVIDO** no Arc 19 Fase F (a tabela ClickHouse `journey_events`
>   não existe mais). O agrupamento multi-sessão volta como **lente + alias** (topic `journey.merges`, 1 tipo,
>   design/pré-código — ver Journey retorno).
> - **`workflow.events` / `collect.events` — legado do `workflow-api`.** Os tópicos, o serviço `workflow-api`,
>   o consumer `skill-flow-worker` e as tabelas `workflow_events`/`collect_events` **continuam fisicamente
>   presentes e ativos** (compat), mas NÃO fazem parte do caminho unificado — no modelo Arc 19 o workflow
>   executa como canal `webhook` via `orchestrator-bridge`. Tratar como caminho legado, não como o contrato atual.
> - Rastreabilidade multi-sessão usa `origin_session_id` (1 salto, Arc 19) e — futuramente — `root_session_id`
>   (raiz transitiva, Journey J1).

---

## Cobertura de Schemas Zod

Todo evento Kafka que cruza fronteira de pacote tem um schema Zod correspondente em
`@plughub/schemas` — nenhum evento cross-package trafega sem contrato validado. A tabela
abaixo mapeia os principais tópicos aos seus schemas e arquivos:

| Tópico | Schema Zod | Arquivo |
|---|---|---|
| `rules.escalation.events` | `RulesEscalationEventSchema` | `rules-events.ts` |
| `registry.changed` | `RegistryChangedEventSchema` | `platform-events.ts` |
| `config.changed` | `ConfigChangedEventSchema` | `platform-events.ts` |
| `sentiment.updated` | `SentimentUpdatedEventSchema` | `platform-events.ts` |
| `queue.position_updated` | `QueuePositionUpdatedEventSchema` | `platform-events.ts` |
| `conversations.routed` / `conversations.queued` | `ConversationRoutedEventSchema` | `platform-events.ts` |
| `agent.lifecycle` | `AgentLifecycleEventSchema` | `platform-events.ts` |
| `workflow.events` | `WorkflowEventSchema` | `workflow.ts` |
| `collect.events` | `CollectEventSchema` | `workflow.ts` |
| ~~`journey.events`~~ | ~~`JourneyEventSchema` / `journey.ts`~~ | **Removido (Arc 19 Fase F)** — tabela `journey_events` não existe mais |
| `journey.merges` *(Journey J3 ✅)* | `JourneyMergedEventSchema` | `journey-merges.ts` |
| `usage.events` | `UsageEventSchema` | `usage.ts` |
| `conversations.participants` | `ConversationParticipantEventSchema` | `contact-segment.ts` |
| `mcp.audit` | `AuditRecordSchema` | `audit.ts` |
| `evaluation.events` | `EvaluationEventSchema` | `evaluation.ts` |

---

## Sumário de Tópicos

| Tópico | Produtores | Consumidores | Propósito |
|---|---|---|---|
| [`conversations.inbound`](#conversationsinbound) | channel-gateway, routing-engine (CrashDetector) | core, routing-engine | Eventos inbound normalizados para roteamento |
| [`conversations.routed`](#conversationsrouted) | routing-engine | core, rules-engine | Decisão de roteamento com alocação |
| [`conversations.queued`](#conversationsqueued) | routing-engine | rules-engine | Contatos não alocados (pool saturado) |
| [`conversations.abandoned`](#conversationsabandoned) | routing-engine | core, rules-engine | Contato abandonado antes de alocação |
| [`conversations.session_opened` / `conversations.session_closed`](#conversationssession_opened--conversationssession_closed) | core | analytics-api, LGPD | Abertura e fechamento de sessão |
| [`conversations.message_sent`](#conversationsmessage_sent) | core | analytics-api | Mensagem entregue na sessão |
| [`conversations.participants`](#conversationsparticipants) | orchestrator-bridge | analytics-api → ClickHouse | Participação de agente em segmento |
| [`agent.done`](#agentdone) | routing-engine | rules-engine, analytics-api | Conclusão de atendimento por um agente |
| [`agent.lifecycle`](#agentlifecycle) | mcp-server-plughub, routing-engine (CrashDetector) | routing-engine | Transições de ciclo de vida de instâncias |
| [`rules.escalation.events`](#rulesescalationevents) | rules-engine | routing-engine | Escalações disparadas (modo ativo) |
| [`rules.shadow.events`](#rulesshadowevents) | rules-engine | analytics-api | Disparos em shadow mode (sem ação real) |
| [`rules.session_tagged`](#rulessession_tagged) | rules-engine | analytics-api | Tags aplicadas à sessão pelo Rules Engine |
| [`registry.changed`](#registrychanged) | agent-registry | routing-engine, core, orchestrator-bridge | Registro e atualização de pools, tipos e skills |
| [`config.changed`](#configchanged) | config-api | orchestrator-bridge, routing-engine | Mudança de configuração de namespace |
| [`gateway.heartbeat`](#gatewayheartbeat) | channel-gateway | routing-engine | Heartbeat dos gateways de canal |
| [`queue.position_updated`](#queueposition_updated) | routing-engine | channel-gateway, analytics-api | Posição do contato na fila |
| [`mcp.audit`](#mcpaudit) | McpInterceptor / proxy sidecar | analytics-api, LGPD | Audit trail de chamadas MCP |
| [`sentiment.updated`](#sentimentupdated) | ai-gateway | analytics-api | Atualização de sentimento da sessão |
| [`evaluation.events`](#evaluationevents) | evaluation-api | analytics-api → ClickHouse | Eventos do ciclo de avaliação de qualidade |
| [`calibration.events`](#calibrationevents) | evaluation-api | analytics-api → ClickHouse | Eventos de calibração de avaliadores (Arc 13) |
| [`workflow.events`](#workflowevents) *(legado workflow-api)* | workflow-api | skill-flow-worker, analytics-api | Lifecycle de `WorkflowInstance` — **caminho legado**, fora do modelo unificado (Arc 19) |
| [`collect.events`](#collectevents) *(legado workflow-api)* | workflow-api | channel-gateway, analytics-api | Step `collect` — contato outbound assíncrono (caminho legado do workflow-api) |
| [`session.signals`](#sessionsignals) | mcp-server-plughub (`survey_record`) | analytics-api → ClickHouse | Voz do cliente/agente (CSAT/NPS/CES…) — grão contato/segmento/jornada |
| [`journey.merges`](#journeymerges) *(Journey J3 ✅)* | mcp-server-plughub (`journey_merge`) | analytics-api → ClickHouse `journey_aliases` | Aresta de merge de journey (novo→antigo); **1 tipo** — distinto do `journey.events` (9 tipos) removido no Arc 19. Ver `docs/product/journey-3-niveis-implementation-spec.md` |
| [`usage.events`](#usageevents) | core, ai-gateway, channel-gateway | usage-aggregator | Metering por dimensão de consumo |
| [`agent.events`](#agentevents) | mcp-server-plughub (tool `agent_event`) | analytics-api → ClickHouse | KPIs de negócio publicados por agentes (Arc 12) |
| [`events.dead_letter`](#eventsdead_letter) | skill-flow-worker, analytics-api, orchestrator-bridge | ops / monitoring | Eventos não processáveis (dead letter) |

> **Nomes obsoletos / removidos.** Versões anteriores deste documento citavam `conversations.events`
> (substituído por `conversations.session_opened` / `conversations.session_closed` +
> `agent.done`) e `agent.registry.events` (substituído por `registry.changed`). Esses
> nomes não existem mais — use os tópicos atuais da tabela acima. **`journey.events`** (9 tipos,
> `JourneyEventSchema`) foi **removido no Arc 19 Fase F** junto da entidade Journey e da tabela
> ClickHouse `journey_events`; não confundir com `journey.merges` (topic novo de 1 tipo, ainda em design).

---

## `conversations.inbound`

**Propósito**: Ponto de entrada de toda conversa na plataforma. O Routing Engine consome este tópico como único árbitro de alocação.

**Produtores**:
- `channel-gateway` — toda mensagem inbound de canal (WhatsApp, SMS, webchat, email, webrtc), incluindo `MenuSubmitEvent`
- `routing-engine` (CrashDetector) — reencaminhamento de conversas órfãs após crash de instância

**Consumidores**:
- `core` — abertura/atualização da sessão
- `routing-engine` — consome como tópico principal; processa cada evento como `ConversationInboundEvent`

### Schema — ConversationInboundEvent

```json
{
  "session_id":      "uuid",
  "tenant_id":       "string",
  "customer_id":     "string",
  "channel":         "whatsapp | webchat | voice | email | sms | instagram | telegram | webrtc",
  "intent":          "string | null",
  "confidence":      0.0,
  "customer_profile": {
    "tier":           "platinum | gold | standard",
    "churn_risk":     0.0,
    "ltv":            null,
    "business_score": 0.0,
    "risk_flag":      false
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

---

## `conversations.routed`

**Propósito**: Resultado da decisão de roteamento para conversas alocadas com sucesso.

**Produtor**: `routing-engine` — publicado após `router.route()` retornar `allocated: true`

**Consumidores**:
- `core` — recebe a alocação para iniciar o handoff ao agente
- `rules-engine` — avaliação pós-roteamento

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
    "queued":          false,
    "routed_at":       "ISO datetime"
  },
  "routed_at": "ISO datetime"
}
```

---

## `conversations.queued`

**Propósito**: Notifica que um contato não pôde ser alocado (pool saturado ou sem instâncias disponíveis) e foi colocado na fila.

**Produtor**: `routing-engine` — publicado após `router.route()` retornar `allocated: false`

**Consumidores**: `rules-engine`

Mesmo schema de `ConversationRoutedEvent`, com `result.allocated: false` e `result.queued: true`. O contato é armazenado no Redis Sorted Set `{tenant_id}:pool:{pool_id}:queue` (score = `queued_at_ms`) e no Redis String `{tenant_id}:queue_contact:{session_id}`.

---

## `conversations.abandoned`

**Propósito**: Sinaliza que um contato deixou a plataforma antes de ser alocado a um agente (`close_reason` da família `customer_abandon` / `max_wait_exceeded` / `no_resource`).

**Produtor**: `routing-engine`

**Consumidores**: `core` (fecha a sessão como `abandoned`), `rules-engine`

---

## `conversations.session_opened` / `conversations.session_closed`

**Propósito**: Eventos de ciclo de vida da sessão — a única exceção à regra de XADD via `writeStreamEntry()` (o Core os escreve diretamente no `server.ts`).

**Produtor**: `core`

**Consumidores**: `analytics-api`, pipeline LGPD

O `session_closed` carrega `close_reason` (do domínio definido no CLAUDE.md) e dispara, entre outros, o pipeline de Session Replayer para avaliação de qualidade.

---

## `conversations.message_sent`

**Propósito**: Registra cada mensagem entregue na sessão para fins analíticos (não substitui o canonical stream).

**Produtor**: `core`

**Consumidores**: `analytics-api`

---

## `conversations.participants`

**Propósito**: Evento de participação de agente em um segmento de contato — base do modelo analítico de `ContactSegment` (Arc 5).

**Produtor**: `orchestrator-bridge`

**Consumidores**: `analytics-api` → tabela ClickHouse `analytics.segments`

**Schema Zod**: `ConversationParticipantEventSchema` (`contact-segment.ts`)

---

## `agent.done`

**Propósito**: Conclusão de atendimento por um agente. Substitui o antigo `conversations.events` / `conversation_completed`.

**Produtor**: `routing-engine` (o orchestrator-bridge publica em nome de agentes nativos e de fallback YAML)

**Consumidores**: `rules-engine`, `analytics-api`

Regras (espelham o refinement de `AgentDoneSchema`):
- `outcome != "resolved"` → `handoff_reason` é obrigatório
- `issue_status` é sempre obrigatório e nunca vazio

```json
{
  "event":            "agent_done",
  "tenant_id":        "string",
  "instance_id":      "uuid",
  "conversation_id":  "uuid",
  "outcome":          "resolved | escalated | abandoned | transferred",
  "issue_status":     [ { "issue_id": "string", "description": "string", "resolved": true } ],
  "handoff_reason":   "string | null",
  "timestamp":        "ISO datetime"
}
```

---

## `agent.lifecycle`

**Propósito**: Transições de ciclo de vida de instâncias de agente. Canal principal de comunicação entre `mcp-server-plughub` e `routing-engine`.

**Produtores**:
- `mcp-server-plughub` — eventos normais de ciclo de vida (`agent_login`, `agent_ready`, `agent_busy`, `agent_pause`, `agent_logout`, `agent_heartbeat`); inclui `agent_pause`/`agent_ready` com `reason_id`/`reason_label` (Arc 8)
- `routing-engine` (CrashDetector) — evento `agent_crash` ao detectar instância sem heartbeat

**Consumidores**: `routing-engine` (kafka_listener) — atualiza Redis `{tenant_id}:instance:{instance_id}` (TTL 30s) e gerencia sets de pool

**Schema Zod**: `AgentLifecycleEventSchema` (`platform-events.ts`)

Eventos: `agent_login`, `agent_ready`, `agent_busy`, `agent_done`, `agent_pause`, `agent_logout`, `agent_heartbeat`, `agent_crash`. O `agent_heartbeat` deve ser enviado a cada ~10s em estado `ready`/`busy`; a ausência por 30s é detectada como crash.

---

## `rules.escalation.events`

**Propósito**: Registra cada escalação efetivamente disparada pelo Rules Engine (modo `active`).

**Produtor**: `rules-engine` (Escalator)

**Consumidor**: `routing-engine` (recebe o novo pool de destino)

**Schema Zod**: `RulesEscalationEventSchema` (`rules-events.ts`)

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
    "turn_count":        5,
    "elapsed_ms":        12000,
    "sentiment_score":   -0.8,
    "intent_confidence": 0.4,
    "flags":             ["high_frustration"]
  }
}
```

---

## `rules.shadow.events`

**Propósito**: Registra disparos de regras em modo `shadow` — o que teria acontecido, sem ação real. Usado para validar novas regras antes de ativá-las.

**Produtor**: `rules-engine` (Escalator)

**Consumidor**: `analytics-api`

Schema idêntico a `rules.escalation.events`, com `shadow_mode: true`.

---

## `rules.session_tagged`

**Propósito**: Registra tags aplicadas a uma sessão pelo Rules Engine (classificação derivada de regras).

**Produtor**: `rules-engine`

**Consumidor**: `analytics-api`

---

## `registry.changed`

**Propósito**: Notifica mudanças de configuração do Agent Registry — pools, agent types e skills. Substitui o antigo `agent.registry.events`. Habilita o **hot-reload de skills** (PUT no skill-flow-engine → `registry.changed` → invalidação de cache).

**Produtor**: `agent-registry`

**Consumidores**: `routing-engine` (cache de pool configs), `core`, `orchestrator-bridge` (reconciliação de instâncias)

**Schema Zod**: `RegistryChangedEventSchema` (`platform-events.ts`)

---

## `config.changed`

**Propósito**: Notifica mudança em um namespace do Config API (`ai_gateway`, `pricing`, `masking`, `agent_activity`, etc.).

**Produtor**: `config-api`

**Consumidores**: `orchestrator-bridge`, `routing-engine`

**Schema Zod**: `ConfigChangedEventSchema` (`platform-events.ts`)

---

## `gateway.heartbeat`

**Propósito**: Heartbeat dos gateways de canal. O Routing Engine usa esse sinal como hard filter — agentes em gateways sem heartbeat há mais de 90s são excluídos do roteamento.

**Produtor**: `channel-gateway`

**Consumidor**: `routing-engine`

---

## `queue.position_updated`

**Propósito**: Publica a posição atualizada de um contato na fila de espera, para feedback ao cliente e analytics.

**Produtor**: `routing-engine`

**Consumidores**: `channel-gateway`, `analytics-api`

**Schema Zod**: `QueuePositionUpdatedEventSchema` (`platform-events.ts`)

---

## `mcp.audit`

**Propósito**: Audit trail de toda chamada MCP interceptada. A política de auditoria é definida por tool — o caller não pode optar por sair (LGPD).

**Produtores**: `McpInterceptor` (in-process, agentes nativos), proxy sidecar (`localhost:7422`, agentes externos)

**Consumidores**: `analytics-api`, pipeline LGPD

**Schema Zod**: `AuditRecordSchema` (`audit.ts`)

O `AuditRecord` inclui `server_name`, `tool_name`, `allowed`, `injection_detected`, `duration_ms` e `source` (`in_process` | `proxy_sidecar`).

---

## `sentiment.updated`

**Propósito**: Atualização do sentimento da sessão calculado pelo AI Gateway.

**Produtor**: `ai-gateway`

**Consumidor**: `analytics-api`

**Schema Zod**: `SentimentUpdatedEventSchema` (`platform-events.ts`)

---

## `evaluation.events`

**Propósito**: Eventos do ciclo de avaliação de qualidade (Arc 6) — criação de instância, submissão de resultado, revisão, contestação, finalização.

**Produtor**: `evaluation-api`

**Consumidor**: `analytics-api` → tabelas ClickHouse `evaluation_results` e `evaluation_events`

**Schema Zod**: `EvaluationEventSchema` (`evaluation.ts`)

---

## `calibration.events`

**Propósito**: Eventos de calibração de avaliadores (Arc 13) — `calibration_reviewed` e `calibration_note_published`. Alimenta o Calibration Dashboard.

**Produtor**: `evaluation-api`

**Consumidor**: `analytics-api` → tabela ClickHouse `calibration_events`

---

## `workflow.events`

> **Legado (`workflow-api`).** No modelo unificado (Arc 19) o workflow é o canal `webhook` da channel-gateway
> e o skill-flow roda como agente nativo via `orchestrator-bridge` — não há mais `WorkflowInstance` como
> entidade de primeira classe no caminho novo. Este tópico, o `workflow-api`, o consumer `skill-flow-worker`
> e a tabela ClickHouse `workflow_events` **permanecem fisicamente presentes e ativos** por compatibilidade,
> mas descrevem o caminho antigo, não o contrato atual.

**Propósito**: Lifecycle de `WorkflowInstance` (Arc 4) — `trigger`, `suspend`, `resume`, `complete`, `fail`, `cancel`, `timed_out`.

**Produtor**: `workflow-api`

**Consumidores**: `skill-flow-worker` (executa SkillFlow para instâncias de workflow — ainda subscrito ao
tópico), `analytics-api` → tabela ClickHouse `workflow_events`

**Schema Zod**: `WorkflowEventSchema` (`workflow.ts`)

---

## `collect.events`

> **Legado (`workflow-api`).** Mesmo enquadramento de `workflow.events`: pertence ao caminho antigo do
> `workflow-api`. No modelo unificado (Arc 19) o `collect` é step exclusivo de workflows executados como
> canal `webhook`. Tópico e tabela ClickHouse `collect_events` permanecem ativos por compatibilidade.

**Propósito**: Eventos do step `collect` — contato outbound assíncrono multicanal que suspende o workflow até a resposta do cliente.

**Produtor**: `workflow-api`

**Consumidores**: `channel-gateway` (filtra `collect.requested` para despachar o contato), `analytics-api` → tabela ClickHouse `collect_events`

**Schema Zod**: `CollectEventSchema` (`workflow.ts`)

O despacho usa a matriz de capacidades de canal (`requires: [text|audio|video|file_upload|masked_input|rich_menu]`) quando o canal não é explícito.

---

## `journey.events` — **REMOVIDO (Arc 19 Fase F)**

> Status: **removido.** O tópico `journey.events` (9 tipos), o `JourneyEventSchema`/`journey.ts` e a tabela
> ClickHouse `journey_events` foram **eliminados no Arc 19 Fase F** junto com a entidade Journey (a dualidade
> contact/workflow foi colapsada no modelo unificado). Nada produz nem consome este tópico.

O agrupamento multi-sessão retorna como **lente + camada mínima de alias**, não como entidade:
- **`origin_session_id`** (Arc 19) — 1 salto de proveniência, já em produção (`sessions.origin_session_id`).
- **`root_session_id`** (Journey J1, pré-código) — raiz transitiva que agrupa a árvore de proveniência.
- **`journey.merges`** (topic novo de **1 tipo**, Journey J3 ✅) — arestas de merge (novo→antigo); ver seção abaixo.

Ver `docs/product/journey-3-niveis-implementation-spec.md` e a seção Journey (retorno) do `TODO.md`.

---

## `journey.merges`

> Status: **Implementado** (Journey J3). Topic de **1 tipo** (`journey_merged`) — NÃO confundir com o
> `journey.events` de 9 tipos removido no Arc 19.

**Propósito**: Aresta de merge/alias entre duas journeys — liga a raiz mais NOVA (`source_root`, absorvida) à
mais ANTIGA (`canonical_root`, sobrevivente). Ordem novo→antigo ⇒ floresta sem ciclo. NUNCA reescreve o
`root_session_id` das sessões — só grava a aresta; a resolução canônica (union-find) roda no read layer da
analytics-api.

**Produtor**: `mcp-server-plughub` (tool `journey_merge`, auditada pelo McpInterceptor)

**Consumidor**: `analytics-api` → tabela ClickHouse `journey_aliases` (`ReplacingMergeTree` por
`(tenant_id, source_root)`); a lente `/reports/journeys` agrupa pela raiz canônica e o drill
`/reports/sessions?root_session_id=` expande o canônico para o conjunto de raízes-membro.

**Schema Zod**: `JourneyMergedEventSchema` (`journey-merges.ts`)

```json
{
  "event_id":       "uuid",
  "tenant_id":      "string",
  "source_root":    "session_id (raiz nova, absorvida)",
  "canonical_root": "session_id (raiz antiga, sobrevivente)",
  "merged_at":      "ISO datetime",
  "actor":          "string (skill_id / instance_id / participant)"
}
```

---

## `session.signals`

**Propósito**: Sinais de "voz do cliente/agente" — resultado de pesquisas de satisfação (CSAT/NPS/CES/PMF/FCR)
e afins. Store único de sinais na bancada analítica (F10), com grão contato/segmento e — quando aplicável —
jornada.

**Produtor**: `mcp-server-plughub` (tool `survey_record`)

**Consumidor**: `analytics-api` → tabela ClickHouse `session_signal`

**Schema Zod**: `SessionSignalEventSchema` (`survey.ts`)

O evento carrega `origin_session_id`/`journey_id` (religa à sessão original em surveys diferidos) e é
bucketizado por `session_at` (regra de ouro da bancada — timing do ato × diferido não altera o bucket).

---

## `usage.events`

**Propósito**: Metering por dimensão de consumo. Metering ≠ pricing — registros de uso não carregam preço.

**Produtores**: `core` (`sessions`, `messages`), `ai-gateway` (`llm_tokens_input`/`llm_tokens_output`), `channel-gateway` (`webchat_attachments`)

**Consumidor**: `usage-aggregator`

**Schema Zod**: `UsageEventSchema` (`usage.ts`) — `event_id`, `tenant_id`, `session_id`, `dimension`, `quantity`, `source_component`, `metadata`

---

## `agent.events`

> Status: **Implementado** (Arc 12).

**Propósito**: KPIs de negócio estruturados publicados por agentes (AI e humanos) via MCP tool `agent_event(category, value, tags?)` durante sessões.

**Produtor**: `mcp-server-plughub` (tool `agent_event`, interceptada e auditada)

**Consumidor**: `analytics-api` → tabela ClickHouse `analytics.agent_business_events`

O `category` é hierárquico em dot notation (`pool_id.skill_id.metric_key`); o primeiro segmento deve ser o `pool_id` da sessão (namespace isolation). Contexto de sessão é resolvido automaticamente do `session_token`.

---

## `events.dead_letter`

**Propósito**: Dead letter queue — eventos que não puderam ser processados pelos consumidores.

**Produtores**: `skill-flow-worker`, `analytics-api`, `orchestrator-bridge`

**Consumidores**: ferramentas de ops e monitoramento

---

## Fluxo de Eventos — Atendimento Padrão

```
1. channel-gateway    → conversations.inbound           (nova mensagem do cliente)
2. core               → conversations.session_opened
3. routing-engine     → conversations.routed            (alocação bem-sucedida)
   ou routing-engine  → conversations.queued            (pool saturado)
4. mcp-server         → agent.lifecycle (agent_login)
5. mcp-server         → agent.lifecycle (agent_ready)
6. routing-engine     → agent.lifecycle (agent_busy)    ← via kafka_listener
   [atendimento em curso — ai-gateway → sentiment.updated]
7. routing-engine     → agent.done
8. core               → conversations.session_closed
```

## Fluxo de Eventos — Crash de Instância

```
1. [heartbeat ausente por 30s — TTL do Redis expirou]
2. routing-engine     → agent.lifecycle (agent_crash)
3. routing-engine     → conversations.inbound           (requeue de cada conversa órfã)
4. routing-engine     → conversations.routed            (nova alocação)
```

## Fluxo de Eventos — Escalação por Regra

```
1. [regra dispara: sentiment_score < -0.7 por 3 turnos]
2. rules-engine       → rules.escalation.events         (modo active)
   ou rules-engine    → rules.shadow.events             (modo shadow)
3. routing-engine     ← rules.escalation.events         (novo pool de destino)
```

## Fluxo de Eventos — Workflow como canal `webhook` (Arc 19, modelo unificado)

```
1. [trigger externo no endpoint webhook]
   channel-gateway    → conversations.inbound           (WebhookAdapter cria a sessão)
2. core               → conversations.session_opened
3. routing-engine     → conversations.routed            (aloca instância skill-flow do pool webhook)
4. [step suspend: sessão persiste com status `suspended` e TTL estendido no Redis]
   orchestrator-bridge→ agent.lifecycle (agent_ready)   (segmento fecha, instância volta ao pool)
5. [resume via POST .../webhook/resume/{token}]
   channel-gateway    → conversations.inbound           (nova alocação → novo segmento)
6. orchestrator-bridge→ agent.done                       (fluxo conclui)
7. core               → conversations.session_closed
```

> O `session_id` é o identificador persistente por toda a execução — inclusive múltiplos ciclos de
> suspend/resume. Não há mais `journey.events`; a proveniência entre sessões usa `origin_session_id`.

> **Nota (legado).** O caminho antigo do `workflow-api` (`workflow.events` → `skill-flow-worker`,
> `collect.events`) ainda existe fisicamente e emite os fluxos descritos nas seções acima, mas não é o
> caminho do modelo unificado.
