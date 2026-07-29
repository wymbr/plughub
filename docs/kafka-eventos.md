# PlugHub — Tópicos Kafka e Schemas de Eventos

> Última atualização: 2026-07-27 · Estado: Arc 19 (modelo unificado de sessão)
>
> **Saneamento 2026-07-27** — este documento foi reconciliado contra o CÓDIGO (varredura de todo
> `producer.send`/`kafka.publish`/`AIOKafkaConsumer` em `packages/`). Achados corrigidos: o tópico
> **`conversations.events` — o mais movimentado da plataforma — estava listado como "nome obsoleto que não
> existe mais"**; cinco tópicos documentados **não existem no código** (`conversations.session_opened`,
> `conversations.message_sent`, `conversations.abandoned`, `rules.session_tagged`, `gateway.heartbeat`); e três
> são **órfãos** (publicados sem nenhum consumidor: `agent.done`, `rules.escalation.events`,
> `rules.shadow.events`). Cada caso está marcado na tabela e na seção do tópico. Regra desta doc daqui em
> diante: **um tópico só entra se houver produtor ou consumidor no código** — evento dentro de um tópico
> (`agent_done` em `agent.lifecycle`, `message_sent` em `conversations.events`) **não é tópico**.
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
| `session.signals` | `SessionSignalEventSchema` | `survey.ts` |
| `conversations.events` | `ContactOpenEvent` / `ContactClosedEvent` (channel-gateway `models.py`) + parsers do analytics | — sem schema Zod único (dívida: é o tópico central e o de contrato mais frouxo) |

---

## Sumário de Tópicos

| Tópico | Produtores | Consumidores | Propósito |
|---|---|---|---|
| [`conversations.inbound`](#conversationsinbound) | channel-gateway (todos os adapters + webhook), routing-engine (CrashDetector, re-rota), orchestrator-bridge | routing-engine, orchestrator-bridge, conversation-writer, analytics-api | Eventos inbound normalizados para roteamento |
| [`conversations.events`](#conversationsevents) | channel-gateway, orchestrator-bridge, mcp-server-plughub, quality-ingest | orchestrator-bridge, routing-engine, rules-engine, conversation-writer, session-replayer, analytics-api | **Tópico central do ciclo de vida do contato** — `contact_open`, `contact_closed`, `message_sent`, `session_suspended`, `conference_agent_completed`, `insight.registered` |
| [`conversations.outbound`](#conversationsoutbound) | routing-engine, orchestrator-bridge, mcp-server-plughub | channel-gateway (`outbound_consumer`), conversation-writer | Entrega ao cliente (mensagem, `session.closed` do WS) |
| [`conversations.routed`](#conversationsrouted) | routing-engine | orchestrator-bridge, analytics-api | Decisão de roteamento com alocação |
| [`conversations.queued`](#conversationsqueued) | routing-engine | orchestrator-bridge, analytics-api | Contatos não alocados (pool saturado) |
| [`conversations.session_closed`](#conversationssession_closed) | orchestrator-bridge, quality-ingest | session-replayer, evaluation-api | Fechamento de sessão — dispara Replayer e amostragem de avaliação |
| [`conversations.participants`](#conversationsparticipants) | orchestrator-bridge, mcp-server-plughub (`segment_outcome_record`), evaluation-api, quality-ingest | analytics-api → `analytics.segments`, session-replayer, evaluation-api | Participação de agente em segmento |
| [`agent.lifecycle`](#agentlifecycle) | mcp-server-plughub, orchestrator-bridge, routing-engine (CrashDetector), quality-ingest | routing-engine, rules-engine, analytics-api | Ciclo de vida de instâncias — inclui o evento **`agent_done`** |
| [`agent.registry.events`](#agentregistryevents) | agent-registry | routing-engine | Cache de pools/agent types no routing |
| [`registry.changed`](#registrychanged) | agent-registry | orchestrator-bridge | Registro e atualização de pools, tipos e skills (hot-reload) |
| [`config.changed`](#configchanged) | config-api | orchestrator-bridge, routing-engine, channel-gateway | Mudança de configuração de namespace |
| [`queue.position_updated`](#queueposition_updated) | routing-engine | analytics-api | Posição do contato na fila |
| [`pool.occupancy`](#pooloccupancy) | routing-engine | analytics-api | Amostragem periódica de ocupação de pool |
| [`mcp.audit`](#mcpaudit) | SDK `McpInterceptor` / proxy sidecar | analytics-api, LGPD | Audit trail de chamadas MCP |
| [`sentiment.updated`](#sentimentupdated) | ai-gateway | analytics-api | Atualização de sentimento da sessão |
| [`evaluation.events`](#evaluationevents) | evaluation-api, rules-engine (sampler), mcp-server-plughub, session-replayer | routing-engine, session-replayer, evaluation-api, analytics-api | Ciclo de avaliação de qualidade (Arc 6) |
| [`evaluation.results`](#evaluationresults) | mcp-server-plughub | clickhouse-consumer | Resultado de avaliação (trilha separada de `evaluation.events`) |
| [`calibration.events`](#calibrationevents) | evaluation-api | analytics-api → ClickHouse | Calibração de avaliadores (Arc 13) |
| [`workflow.events`](#workflowevents) *(legado workflow-api)* | workflow-api | skill-flow-worker, evaluation-api, analytics-api | Lifecycle de `WorkflowInstance` — **caminho legado**, fora do modelo unificado (Arc 19) |
| [`collect.events`](#collectevents) *(legado workflow-api)* | workflow-api | channel-gateway, analytics-api | Step `collect` — contato outbound assíncrono (caminho legado) |
| [`session.signals`](#sessionsignals) | mcp-server-plughub (`survey_record`), channel-gateway (`survey_web`), evaluation-api | analytics-api → ClickHouse | Voz do cliente/agente (CSAT/NPS/CES/PMF/FCR) — grão contato/segmento/jornada |
| [`journey.merges`](#journeymerges) *(Journey J3 ✅)* | mcp-server-plughub (`journey_merge`) | analytics-api → ClickHouse `journey_aliases` | Aresta de merge de journey (novo→antigo); **1 tipo** — distinto do `journey.events` (9 tipos) removido no Arc 19 |
| [`usage.events`](#usageevents) | channel-gateway, ai-gateway, mcp-server-plughub | usage-aggregator, analytics-api | Metering por dimensão de consumo |
| [`usage.cycle_reset`](#usagecycle_reset) | **nenhum** | usage-aggregator | Reset de ciclo — **consumidor sem produtor** |
| [`agent.events`](#agentevents) | mcp-server-plughub (tool `agent_event`) | analytics-api → ClickHouse | KPIs de negócio publicados por agentes (Arc 12) |
| [`events.dead_letter`](#eventsdead_letter) | orchestrator-bridge, analytics-api | **nenhum** (sink) | Eventos não processáveis (DLQ write-only) |
| [`agent.done`](#agentdone) ⚠️ órfão | mcp-server-plughub | **nenhum** | Publicado no vácuo — ver seção |
| [`rules.escalation.events`](#rulesescalationevents) ⚠️ órfão | rules-engine | **nenhum** | Escalações do modo ativo — ver seção |
| [`rules.shadow.events`](#rulesshadowevents) ⚠️ órfão | rules-engine | **nenhum** | Disparos em shadow mode — ver seção |

> **Tópicos que NÃO existem** (verificado no código em 2026-07-27 — zero produtores e zero consumidores):
> `conversations.session_opened`, `conversations.message_sent`, `conversations.abandoned`,
> `rules.session_tagged`, `gateway.heartbeat`. Os três primeiros confundem **evento com tópico**:
> `message_sent` é um `event_type` dentro de `conversations.events`, e abandono é um `close_reason` do
> `contact_closed`. `gateway.heartbeat` é o evento `agent_heartbeat` dentro de `agent.lifecycle`.
> **`journey.events`** (9 tipos, `JourneyEventSchema`) foi **removido no Arc 19 Fase F** junto da entidade
> Journey e da tabela ClickHouse `journey_events`; não confundir com `journey.merges` (1 tipo, implementado).
>
> **Órfãos** (produzem sem ninguém consumir, ou consomem sem ninguém produzir): `agent.done`,
> `rules.escalation.events`, `rules.shadow.events`, `usage.cycle_reset`. Cada um tem uma nota na sua seção;
> os acionáveis estão registrados no `TODO.md`.

---

## Matriz módulo × tópico

> **Varredura de 2026-07-28.** Complementa o Sumário acima invertendo o eixo: lá o índice é o tópico, aqui é o
> **módulo**. Serve para responder "de que eventos este serviço depende?" ao portar, escalar ou depurar um
> pacote. Verificada contra o código (`producer.send`/`send_and_wait`/`kafka.publish` e
> `AIOKafkaConsumer`/`consumer.subscribe` em `packages/`), com `node_modules` e `.venv` excluídos.
>
> **P** = produz · **C** = consome (com o `group_id` entre parênteses). Onde a varredura não encontrou
> evidência, está escrito "nenhum" — não "provavelmente nenhum".

### Núcleo de sessão e roteamento

| Módulo | Produz | Consome (`group_id`) |
|---|---|---|
| `mcp-server-plughub` (Core) | `agent.lifecycle`, `conversations.inbound`, `conversations.events`, `conversations.outbound`, `conversations.participants`, `conversations.channel_change` ⚠, `agent.events`, `evaluation.events`, `evaluation.results`, `session.signals`, `journey.merges`, `usage.events`, `audit.mcp_calls` ⚠ | **nenhum** — é produtor puro |
| `routing-engine` | `conversations.routed`, `conversations.queued`, `conversations.events`, `conversations.participants`, `conversations.outbound`, `conversations.inbound` (re-rota), `queue.position_updated`, `pool.occupancy`, `agent.lifecycle` (`agent_crash`) | `conversations.inbound` (`routing-engine`) · `agent.lifecycle` + `agent.registry.events` + `config.changed` + `conversations.events` (`routing-engine-listener`) · `evaluation.events` (`routing-engine-evaluation`) |
| `orchestrator-bridge` | `conversations.inbound`, `conversations.outbound`, `conversations.events`, `conversations.session_closed`, `conversations.participants`, `agent.lifecycle`, `events.dead_letter` | `conversations.routed`, `conversations.queued`, `conversations.inbound`, `conversations.events`, `registry.changed`, `config.changed` (`orchestrator-bridge`) |
| `channel-gateway` | `conversations.inbound`, `conversations.events`, `usage.events`, `session.signals` | `conversations.outbound` (`channel-gateway-webchat`) · `collect.events` (`…-collect`) · `config.changed` (`…-config`) |
| `rules-engine` | `rules.escalation.events`, `rules.shadow.events`, `evaluation.events` | `conversations.events`, `agent.lifecycle` (`rules-engine-sampling`) |
| `conversation-writer` | `evaluation.events` (`transcript.created`) ⚠ | `conversations.inbound`, `conversations.outbound`, `conversations.events` (`conversation-writer`) |

### Registro, configuração e fluxo

| Módulo | Produz | Consome (`group_id`) |
|---|---|---|
| `agent-registry` | `registry.changed`, `agent.registry.events` | nenhum |
| `config-api` | `config.changed` | nenhum |
| `ai-gateway` | `sentiment.updated`, `usage.events` | nenhum |
| `skill-flow-engine` | nenhum | nenhum — não importa `kafkajs`; é biblioteca de interpretação |
| `sdk` (`McpInterceptor` / proxy) | `mcp.audit` | nenhum |
| `workflow-api` *(legado)* | `workflow.events`, `collect.events` | nenhum |
| `skill-flow-worker` *(legado)* | `events.dead_letter` | `workflow.events` (`skill-flow-worker`) |

### Qualidade e analítica

| Módulo | Produz | Consome (`group_id`) |
|---|---|---|
| `analytics-api` | `events.dead_letter` (DLQ) | 18 tópicos num consumer só (`analytics-api`): `conversations.inbound`, `conversations.routed`, `conversations.queued`, `conversations.events`, `conversations.participants`, `agent.lifecycle`, `agent.events`, `usage.events`, `sentiment.updated`, `queue.position_updated`, `pool.occupancy`, `workflow.events`, `collect.events`, `evaluation.events`, `calibration.events`, `session.signals`, `journey.merges`, `mcp.audit` |
| `evaluation-api` | `evaluation.events`, `calibration.events`, `conversations.participants`, `session.signals` | `workflow.events` (`…-workflow-consumer`) · `conversations.participants` (`…-participants-consumer`) · `conversations.session_closed` (`…-sampling-consumer`) · `evaluation.events` (`…-ingest-consumer`) |
| `session-replayer` | **nenhum** ⚠ (o `AIOKafkaProducer` é criado mas nunca usado) | `conversations.session_closed` (`session-replayer-persister`) · `evaluation.events` (`session-replayer-replayer`) · `conversations.events` + `conversations.participants` (`session-replayer-import`) |
| `quality-ingest` | `conversations.events`, `conversations.participants`, `agent.lifecycle`, `conversations.session_closed` | nenhum — produtor puro por design |
| `clickhouse-consumer` | nenhum | `evaluation.results` (`clickhouse-consumer`) |
| `usage-aggregator` | nenhum | `usage.events` (`usage-aggregator`) |

### Serviços sem Kafka (REST puro)

`auth-api`, `pricing-api`, `dialog-api`, `scheduler-api`, `mailing-api`, `quality-export`, `dashboard/api`,
`platform-ui`, `schemas`.

> Vale notar como fato de arquitetura: os três serviços mais novos de domínio (`dialog-api`, `scheduler-api`,
> `mailing-api`) são **REST puro**. Integram-se ao barramento indiretamente — via tools MCP chamadas por
> agentes, ou via o webhook de canal — em vez de publicar eventos próprios.

### Achados da varredura de 2026-07-28

Seis divergências encontradas **depois** do saneamento de 27/07. Nenhuma é grave; todas são deriva silenciosa
do tipo que só aparece em varredura de código.

| # | Achado | Situação |
|---|---|---|
| 1 | **`conversations.channel_change`** é produzido (`mcp-server-plughub/src/tools/session.ts:812`) e não estava documentado | Tópico real, sem seção. Consumidor não localizado — candidato a órfão. |
| 2 | **`audit.mcp_calls`** é produzido (`mcp-server-plughub/src/tools/external-agent.ts:200`) e não estava documentado | Suspeita de deriva: o tópico canônico de auditoria é `mcp.audit`. Dois nomes para a mesma coisa é exatamente o defeito que o saneamento removeu em outros pontos. **Verificar antes de documentar.** |
| 3 | **`conversation-writer` produz `evaluation.events`** (`writer.py:196`, evento `transcript.created`) | Produtor legítimo e ausente da lista da seção `evaluation.events`. |
| 4 | **`session-replayer` não produz nada** — cria o producer e nunca chama `send` | A seção `evaluation.events` o lista como produtor. Corrigir a lista (o comentário em `consumer.py:256` confirma que a publicação de `evaluation.requested` foi retirada). |
| 5 | **`calendar.events`** está declarado em `calendar-api/config.py:30` e o docstring do `main.py` promete publicá-lo — **não há produtor** | Config morta. Remover a declaração ou implementar. |
| 6 | **`journey.events`** ainda está declarado em `workflow-api/config.py:30` (`journey_topic`) | Resíduo do Arc 19 Fase F. O tópico não existe mais; a config sim. |

> **Nenhum módulo central de constantes de tópicos existe** — os nomes são literais inline em cada call site,
> com poucas constantes locais de arquivo. É a causa raiz das derivas 1, 2, 5 e 6: um `topics.ts`/`topics.py`
> compartilhado tornaria um tópico novo ou morto visível no diff. Registrado como dívida.

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

## `conversations.events`

> **O tópico mais movimentado da plataforma** — e o que este documento afirmava não existir até 2026-07-27.
> Cinco pacotes produzem, seis consomem. Todo o ciclo de vida do CONTATO passa por aqui.

**Propósito**: Ciclo de vida do contato e das mensagens. Não confundir com `conversations.inbound` (entrada
para roteamento) nem com o canonical stream (`session:{id}:stream`, que é Redis, não Kafka).

**Produtores**: `channel-gateway` (adapters webchat/email/sms/whatsapp), `orchestrator-bridge`,
`mcp-server-plughub` (server + tools runtime/bpm/session), `quality-ingest` (importação externa)

**Consumidores**: `orchestrator-bridge` (`process_contact_event` — hooks de finalização, close do contato),
`routing-engine` (remove sessão órfã da fila), `rules-engine`, `conversation-writer`, `session-replayer`
(reconstrução do stream para importados), `analytics-api` (→ `sessions` + `messages`)

### `event_type` que trafegam

| `event_type` | Produtor típico | Consumido por |
|---|---|---|
| `contact_open` | channel-gateway (adapters), mcp-server (`bpm`), quality-ingest | analytics-api (`sessions`) |
| `contact_closed` | orchestrator-bridge (versão enriquecida: `close_reason`, `outcome`, `root_session_id`), channel-gateway, mcp-server (`agent_closed`/`agent_transfer`/`agent_disconnect`) | analytics-api, orchestrator-bridge, routing-engine |
| `message_sent` | mcp-server-plughub, quality-ingest | analytics-api (`messages`) |
| `session_suspended` | orchestrator-bridge | analytics-api |
| `conference_agent_completed` | mcp-server-plughub (`runtime`) | orchestrator-bridge (contagem de hooks) — **não materializado no analytics** |
| `insight.registered` | mcp-server-plughub (`runtime`) | — **não materializado no analytics** |

> **Cuidado com o wire.** O valor `reason: "agent_done"` dentro de `contact_closed` é um terceiro sentido de
> "agent done" (≠ tópico `agent.done`, ≠ evento `agent_done` do `agent.lifecycle`) e há código que depende
> literalmente dessa string — a Arc 14 re-entry guard entre eles. Não renomear.

---

## `conversations.participants`

**Propósito**: Evento de participação de agente em um segmento de contato — base do modelo analítico de `ContactSegment` (Arc 5).

**Produtor**: `orchestrator-bridge`

**Consumidores**: `analytics-api` → tabela ClickHouse `analytics.segments`

**Schema Zod**: `ConversationParticipantEventSchema` (`contact-segment.ts`)

---

## `conversations.session_closed`

**Propósito**: Fechamento da sessão — dispara o pipeline de Session Replayer e a amostragem de avaliação.

**Produtores**: `orchestrator-bridge` (`_close_contact_layer`), `quality-ingest` (histórico importado)

**Consumidores**: `session-replayer`, `evaluation-api` (sampling)

Carrega `close_reason` (domínio no CLAUDE.md). **Nota histórica**: há comentário no bridge dizendo que este
tópico "nunca teve produtor" — era verdade quando escrito; hoje tem dois.

> `conversations.session_opened` **não existe** (zero produtores/consumidores). A abertura é o
> `contact_open` dentro de `conversations.events`.

---

## ~~`agent.done`~~ — removido (2026-07-27)

> **Não existe mais.** Era publicado pelo `mcp-server-plughub` (`tools/runtime.ts`, `tools/session.ts`) e
> **nenhum serviço o consumia**. O comentário no código dizia "consumido por Rules Engine e Analytics" — falso:
> o rules-engine assina apenas `conversations.events` + `agent.lifecycle`, e a lista de tópicos do analytics-api
> não o incluía. A MESMA função publicava `agent.done` e, 64 linhas depois, `agent.lifecycle` com
> `event: "agent_done"` — publicação dupla, sendo que só a segunda tem consumidor. Um teste unitário cobria a
> publicação órfã, mascarando a ausência de consumo; foi reescrito para cobrir as duas vias reais.
>
> **O contrato de conclusão de atendimento é o evento `agent_done` dentro de `agent.lifecycle`** (ver seção
> abaixo) — é ele que dispara `remove_conversation` no routing-engine e alimenta o analytics. O `outcome`
> também viaja no `contact_closed` de `conversations.events`.
>
> **Efeito colateral registrado:** `issue_status` só era publicado no tópico órfão. Ele continua sendo exigido
> e validado na entrada do `agent_done` (invariante do CLAUDE.md), mas não trafega mais em nenhum tópico —
> ninguém o consumia. Se for preciso no analytics, o lugar natural é o `contact_closed`.

Regras do payload (espelham o refinement de `AgentDoneSchema`, válidas para o evento no `agent.lifecycle`):
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

## `rules.escalation.events` ⚠️ telemetria sem consumidor

> **A escalação NÃO depende deste tópico.** O efeito real é uma chamada HTTP: `escalator.py` →
> `POST {mcp_server_url}/tools/conversation_escalate`, e só DEPOIS o evento Kafka é publicado
> (`escalator.py:79` e `:91`). O tópico é **telemetria**, não mecanismo — e não tem consumidor: nenhum
> relatório registra escalações disparadas.
>
> A documentação anterior (e o CLAUDE.md) davam o `routing-engine` como consumidor. Ele não assina o tópico;
> a re-rota acontece pelo caminho HTTP → `conversation_escalate`. Decisão pendente no `TODO.md`: ligar um
> consumidor (para haver relatório de escalações) ou remover a publicação.

**Propósito**: Telemetria de cada escalação disparada pelo Rules Engine em modo `active` (a AÇÃO é o
`conversation_escalate` via HTTP).

**Produtor**: `rules-engine` (Escalator, após a chamada HTTP)

**Consumidor**: **nenhum**

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

## `rules.shadow.events` ⚠️ telemetria sem consumidor

> **Sem consumidor.** O rules-engine publica; o analytics-api (dado como consumidor) não assina o tópico. O
> shadow mode existe para **medir** o que uma regra faria antes de ativá-la — sem consumidor, o único registro
> é o `logger.info("[SHADOW] Rule %s would escalate…")`. A capacidade está pela metade: o dado é produzido e
> descartado.

**Propósito**: Registra disparos de regras em modo `shadow` — o que teria acontecido, sem ação real.

**Produtor**: `rules-engine` (Escalator)

**Consumidor**: **nenhum**

Schema idêntico a `rules.escalation.events`, com `shadow_mode: true`.

> `rules.session_tagged` **não existe** — zero ocorrências no código (nem produtor, nem consumidor, nem
> schema). Estava documentado como se fosse um tópico ativo.

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

## `agent.registry.events`

**Propósito**: Cache de pools e agent types no Routing Engine. Distinto de `registry.changed` (consumido pelo
orchestrator-bridge para reconciliação de instâncias) — são dois tópicos vivos, com destinos diferentes.

**Produtor**: `agent-registry`

**Consumidor**: `routing-engine` (kafka_listener)

> `gateway.heartbeat` **não existe** como tópico. O hard filter de heartbeat do routing opera sobre o evento
> `agent_heartbeat` dentro de `agent.lifecycle` (TTL da chave de instância no Redis).

---

## `queue.position_updated`

**Propósito**: Posição do contato na fila de espera. Publicado **após** o enqueue (antes disso a fila não
contém a própria sessão e a posição sai 0 — ver CHANGELOG 2026-07-27).

**Produtor**: `routing-engine` (`main._publish_queue_position`, pós-`add_queued_contact`)

**Consumidor**: `analytics-api` → `queue_events`

> **Nenhum canal consome.** A documentação anterior dava o `channel-gateway` como consumidor "para informar o
> cliente" — isso nunca foi implementado: o gateway não assina o tópico. Informar posição/ETA ao cliente é
> feature em aberto (`TODO.md`).

**Schema Zod**: `QueuePositionUpdatedEventSchema` (`platform-events.ts`) — carrega `queue_position` (posição do
contato) **e** `queue_length` (tamanho da fila); a tabela persiste só a posição.

---

## `pool.occupancy`

**Propósito**: Amostragem periódica de ocupação de pool (o `_occupancy_sampler` do routing-engine).

**Produtor**: `routing-engine`

**Consumidor**: `analytics-api`

---

## `conversations.outbound`

**Propósito**: Entrega ao cliente — mensagem de sistema (aviso de fila), render de menu e o `session.closed`
que fecha o WebSocket do cliente.

**Produtores**: `routing-engine`, `orchestrator-bridge`, `mcp-server-plughub`

**Consumidores**: `channel-gateway` (`outbound_consumer`), `conversation-writer`

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

## ~~`usage.cycle_reset`~~ — removido (2026-07-27)

> **Não existe mais.** O `usage-aggregator` assinava este tópico, mas **nada nunca o publicou**; o consumo foi
> removido. O reset de ciclo é HTTP: `POST /admin/cycle-reset` (mesma classe `CycleResetter`). O schema
> permanece em `usage.ts` caso o caminho por evento seja desejado no futuro — nesse caso, o que falta é o
> **produtor**.

---

## `evaluation.results`

**Propósito**: Resultado de avaliação numa trilha separada de `evaluation.events` — consumida pelo
`clickhouse-consumer`.

**Produtor**: `mcp-server-plughub` (`tools/evaluation.ts`) · **Consumidor**: `clickhouse-consumer`

---

## `events.dead_letter`

**Propósito**: Dead letter queue — eventos que não puderam ser processados pelos consumidores.

**Produtores**: `orchestrator-bridge`, `analytics-api`

**Consumidores**: **nenhum** no repo — sink write-only, inspecionado por ferramentas de ops.

---

## Fluxo de Eventos — Atendimento Padrão

```
1. channel-gateway    → conversations.inbound             (nova mensagem do cliente)
2. channel-gateway    → conversations.events (contact_open)
3. routing-engine     → conversations.routed              (alocação bem-sucedida)
   ou routing-engine  → conversations.queued              (pool saturado)
                      → queue.position_updated            (após o enqueue)
4. mcp-server         → agent.lifecycle (agent_login)
5. mcp-server         → agent.lifecycle (agent_ready)
6. routing-engine     → agent.lifecycle (agent_busy)      ← via kafka_listener
   [atendimento em curso — ai-gateway → sentiment.updated; mcp-server → conversations.events (message_sent)]
7. mcp-server/bridge  → conversations.events (contact_closed)
8. orchestrator-bridge→ agent.lifecycle (agent_done)      ← libera a vaga no routing
9. orchestrator-bridge→ conversations.session_closed      ← dispara Replayer + sampling de avaliação
```

> Corrigido em 2026-07-27: o fluxo antigo citava `conversations.session_opened` (inexistente) e o tópico
> `agent.done` (órfão). A conclusão de atendimento que tem efeito é o **evento** `agent_done` dentro de
> `agent.lifecycle`.

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
