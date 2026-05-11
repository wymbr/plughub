# Arc 10 — Journey: Multi-Session Service Automation

## Motivação

A plataforma executa automação de serviços via skill-flow (agentes + workflow steps), mas toda a monitoração e relatórios são centrados na **sessão** — uma unidade de interação única. Serviços reais de atendimento frequentemente requerem múltiplos contatos ao longo do tempo: uma portabilidade leva 3 dias, um reembolso pode exigir análise e retorno, uma reclamação formal tem etapas burocráticas com prazos.

O **Journey** é a unidade de serviço que transcende a sessão. Ele agrupa todos os contatos (`session_id[]`) envolvidos na resolução de um mesmo processo, mantém o estado do workflow que o governa, e permite KPIs de serviço (não apenas de sessão): duração end-to-end, taxa de resolução por processo, número de contatos necessários, SLA do processo inteiro.

---

## Conceitos

### Journey vs WorkflowInstance

| Conceito | Nível | Proprietário | Lifecycle |
|---|---|---|---|
| **Journey** | Negócio — "o serviço prestado ao cliente" | journey-api (ou workflow-api) | `active → suspended → completed / failed / cancelled` |
| **WorkflowInstance** | Técnico — "a execução do skill-flow" | workflow-api | `running → suspended → completed / failed` |

Um Journey pode sobreviver à falha e recriação de um WorkflowInstance (retry com novo instance_id). A relação é 1 Journey → N WorkflowInstances ao longo do tempo (geralmente 1, mas não obrigatoriamente).

### Modelo de dados

```
Journey
  journey_id            UUID PK
  tenant_id             string
  skill_id              string            — qual skill-flow define o processo
  workflow_instance_id  UUID nullable FK  — instância ativa (atualizado on trigger)
  customer_id           string nullable   — identificador do cliente (caller.*)
  origin_session_id     string            — primeira sessão que iniciou a jornada
  status                enum              — active | suspended | completed | failed | cancelled
  metadata              JSONB nullable    — dados passados no journey_start
  created_at            timestamptz
  updated_at            timestamptz
  completed_at          timestamptz nullable

Session  (campo adicionado)
  journey_id            UUID nullable FK  — null = sessão standalone (comportamento atual)
```

### Hierarquia de observabilidade

```
Journey
  └── Contato 1  (Session — origin_session_id)
        └── Segmento primary    (ContactSegment)
        └── Segmento specialist (ContactSegment)
  └── Contato 2  (Session — vinculada via collect ou recontato)
        └── Segmento primary
  └── Contato N  ...
```

Sessões sem `journey_id` continuam funcionando exatamente como hoje. O Journey é um nível adicional acima, não uma refatoração do modelo existente.

---

## Formas de iniciar um Journey

### 1. MCP tool `journey_start` (forma primária)

Tool exposta no mcp-server-plughub, grupo `journey`:

```typescript
journey_start({
  skill_id:   string,           // qual skill-flow governa o processo
  session_id: string,           // sessão atual — vira origin_session_id
  metadata?:  Record<string, unknown>,  // contexto adicional passado ao workflow
}): { journey_id: string; workflow_instance_id: string }
```

Efeitos em sequência:
1. Cria registro `Journey` (status `active`, `origin_session_id = session_id`)
2. Chama `POST /v1/workflow/trigger` com `{ skill_id, origin_session_id, journey_id, metadata }`
3. Atualiza `Journey.workflow_instance_id` com o id retornado
4. Publica `journey.events` → `journey_started` no Kafka
5. Retorna `{ journey_id, workflow_instance_id }`

A tool é interceptada pelo McpInterceptor (auditoria automática). Agentes AI a chamam diretamente como qualquer outra tool MCP. Agentes humanos a acionam via UI (ver seção Console).

### 2. `@mention` protocol — acionamento por agente primário

Extensão do protocolo `@mention` existente. Um agente primário (IA ou humano) emite:

```
@journey:<skill_id>
```

O Core recebe, resolve `mentionable_journeys` na config do pool (análogo a `mentionable_pools`), e internamente chama `journey_start` com a session_id atual. O resultado é emitido como mensagem `agents_only` confirmando a criação da jornada.

Configuração no pool YAML:
```yaml
mentionable_journeys:
  - skill_id: skill_portabilidade_telco_v2
    label: "Portabilidade"
  - skill_id: skill_reembolso_v1
    label: "Reembolso"
```

### 3. Acionamento automático em skill-flow

Flag `creates_journey: true` no YAML da skill. Quando o skill-flow-engine inicia a execução, o primeiro passo implicitamente chama `journey_start` antes de processar qualquer step:

```yaml
skill_id: skill_portabilidade_telco_v2
creates_journey: true
steps:
  - type: task
    ...
```

Isso cobre o caso em que o próprio fluxo de automação sabe que é um processo multi-sessão, sem precisar de intervenção do agente.

### 4. Acionamento via console do agente humano

Botão "Iniciar Processo" no `ActionBar` do AgentAssistPage. Abre um selector de skill-flows disponíveis (filtradas por `mentionable_journeys` do pool). Ao confirmar, chama `journey_start` via MCP tool (mesmo mecanismo — não é um endpoint dedicado de UI).

---

## Vinculação de sessões subsequentes

Sessões criadas após o início da jornada precisam carregar o mesmo `journey_id`. Dois mecanismos:

### Via `collect` step

O `collect` step já usa `collect_token` para correlacionar resposta ao workflow. O Channel Gateway que cria a sessão do contato coletado recebe o `workflow_instance_id` no payload do Kafka `collect.events`. A journey-api (ou workflow-api) resolve `journey_id` a partir de `workflow_instance_id` e tagueia a sessão criada.

### Via recontato identificado (fase posterior)

Quando um cliente recontatá proativamente (nova chamada, novo chat), o agente humano ou AI pode vincular manualmente via MCP tool `journey_link_session(journey_id, session_id)`. Automatização desta correlação (via `customer_id` + journeys ativos) é fase posterior.

---

## Kafka Topics

| Topic | Producer | Consumer(s) |
|---|---|---|
| `journey.events` | journey-api / mcp-server | analytics-api → ClickHouse, Core |

### Tipos de evento `journey.events`

```
journey_started    — journey criada, origin_session_id definida
journey_session_linked  — nova sessão vinculada à journey
journey_suspended  — workflow suspendeu (aguardando input/timer)
journey_resumed    — workflow retomado
journey_completed  — processo concluído com sucesso
journey_failed     — processo falhou
journey_cancelled  — cancelado por agente ou timeout
```

---

## Analytics — KPIs de Jornada

Tabela ClickHouse `analytics.journeys` (ReplacingMergeTree):

| Campo | Tipo | Descrição |
|---|---|---|
| `journey_id` | String | PK |
| `tenant_id` | String | |
| `skill_id` | String | Processo executado |
| `customer_id` | String | |
| `origin_session_id` | String | |
| `status` | String | |
| `session_count` | UInt16 | Contatos realizados |
| `duration_ms` | Int64 | `completed_at - created_at` |
| `created_at` | DateTime | |
| `completed_at` | DateTime nullable | |

KPIs derivados:
- **Taxa de resolução por processo**: `completed / total` por `skill_id`
- **Duração mediana end-to-end**: percentil de `duration_ms` por `skill_id`
- **Contatos médios por resolução**: avg `session_count` onde `status = completed`
- **Taxa de recontato**: journeys com `session_count > 1`
- **SLA compliance**: `duration_ms < sla_target_ms` configurado na skill

---

## Frontend

### ProcessosPage (Monitor)

Nível Journey adicionado acima dos contatos existentes. Drill-down:

```
Journey list  →  [selecionar]  →  Journey detail
                                    ├── Workflow timeline (suspensions, resumes)
                                    └── Sessions panel
                                          └── [selecionar sessão]  →  SessionTranscript (já existe)
```

Colunas da journey list: `skill_id`, `customer_id`, `status`, `session_count`, `duration`, `created_at`.

### AgentAssistPage — HistoricoTab

Nova seção "Processos em aberto" no topo da aba Histórico: lista de journeys ativos para o `customer_id` atual (identificado via `@ctx.caller.*`). Colunas: processo (skill_id label), iniciado há X, status, N contatos. Clique abre o journey detail em modal ou navega para o Monitor.

### AgentAssistPage — ActionBar

Botão "Iniciar Processo" (visível quando pool tem `mentionable_journeys` configurados). Abre dropdown/modal de seleção de processo → confirma → chama `journey_start` MCP via WS.

---

## Fases de Implementação

### Fase A — Backend foundation
- Schema `journeys` table (workflow-api ou novo journey-api)
- Campo `journey_id` nullable em `sessions` (PostgreSQL + ClickHouse)
- MCP tool `journey_start` + `journey_link_session` em mcp-server-plughub
- Kafka topic `journey.events` + `JourneyEventSchema` em `@plughub/schemas`
- `workflow-api`: aceitar `journey_id` no trigger payload; publicar eventos

### Fase B — Vinculação automática via collect
- Channel Gateway: receber `journey_id` do `collect.events` Kafka e taguear sessão criada
- analytics-api: consumer `journey.events` → upsert `analytics.journeys`

### Fase C — Monitor (ProcessosPage)
- Journey list + detail + drill-down para sessions existentes
- Journey KPIs no painel de relatórios (Analytics tab)

### Fase D — Console (AgentAssistPage)
- HistoricoTab: seção "Processos em aberto"
- ActionBar: botão "Iniciar Processo" + selector
- `@mention` protocol: extensão `@journey:<skill_id>`

### Fase E — Relatórios consolidados
- `GET /reports/journeys` endpoint em analytics-api
- KPIs end-to-end por skill_id, duração mediana, taxa resolução
- Dashboard cards de jornada

---

## Invariants

- **`journey_id` nunca é igual a `workflow_instance_id`** — são entidades distintas com ciclos de vida independentes
- **Sessões standalone continuam funcionando sem mudança** — `journey_id` é sempre nullable em sessions
- **`journey_start` é sempre chamado via MCP tool** — nunca REST direto da UI; garante auditoria pelo McpInterceptor
- **Uma Journey ativa tem no máximo um WorkflowInstance ativo** — múltiplos instances são sequenciais (retry), nunca paralelos
- **`origin_session_id` é imutável** após criação da Journey

---

## Decisões em aberto

- **Onde vive a Journey entity?** Opção A: tabela nova em `workflow-api` (co-localiza com WorkflowInstance). Opção B: novo `journey-api` separado (mais limpo mas mais infra). Recomendação: Fase A usa `workflow-api`, migra para serviço próprio se complexidade justificar.
- **`mentionable_journeys` no pool YAML ou no skill YAML?** Pool YAML define quais processos estão disponíveis para aquele pool de atendimento — faz mais sentido por pool.
- **Correlação automática de recontatos** (cliente recontatando sem `collect`): fora do escopo inicial, requer lógica de identidade de cliente.
