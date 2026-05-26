# Arc 10 — Journey: Multi-Session Service Automation

> Última atualização: 2026-05-25 · Estado: Arc 16

## Motivação

A plataforma executa automação de serviços via skill-flow (agentes + workflow steps), mas toda a monitoração e relatórios são centrados na **sessão** — uma unidade de interação única. Serviços reais de atendimento frequentemente requerem múltiplos contatos ao longo do tempo: uma portabilidade leva 3 dias, um reembolso pode exigir análise e retorno, uma reclamação formal tem etapas burocráticas com prazos.

O **Journey** é a unidade de serviço que transcende a sessão. Ele agrupa todos os contatos (`session_id[]`) envolvidos na resolução de um mesmo processo, mantém o estado do workflow que o governa, e permite KPIs de serviço (não apenas de sessão): duração end-to-end, taxa de resolução por processo, número de contatos necessários, SLA do processo inteiro.

---

## Conceitos

### Journey vs WorkflowInstance

| Conceito | Nível | Proprietário | Lifecycle |
|---|---|---|---|
| **Journey** | Negócio — "o serviço prestado ao cliente" | workflow-api | `active → suspended → completed / failed / cancelled / merged` |
| **WorkflowInstance** | Técnico — "a execução do skill-flow" | workflow-api | `running → suspended → completed / failed` |

Um Journey pode sobreviver à falha e recriação de um WorkflowInstance (retry com novo instance_id). A relação é 1 Journey → N WorkflowInstances ao longo do tempo (geralmente 1, mas não obrigatoriamente).

### Modelo de dados

**Tabela `workflow.journeys` (PostgreSQL — workflow-api):**

```
journey_id              UUID PK
tenant_id               string
skill_id                string            — qual skill-flow define o processo
workflow_instance_id    UUID nullable FK  — instância ativa (atualizado on trigger)
pool_id                 string nullable   — pool de origem; estável entre upgrades de skill; usado para filtro e access control
customer_id             string nullable   — identificador do cliente
origin_session_id       string            — sessão que iniciou a jornada (imutável)
status                  enum              — active|suspended|completed|failed|cancelled|merged
failure_reason          text nullable     — preenchido quando status=failed: collect_timeout | suspend_timeout | workflow_error | ...
merged_into_journey_id  UUID nullable     — preenchido quando status = merged
split_from_journey_id   UUID nullable     — preenchido quando originado de split
metadata                JSONB nullable    — dados passados no journey_start
session_snapshot        JSONB nullable    — cache desnormalizado para display (atualizado via Kafka, não é fonte de verdade)
created_at              timestamptz
updated_at              timestamptz
completed_at            timestamptz nullable
```

**Tabela `workflow.journey_sessions` (junction table — PostgreSQL — workflow-api):**

Substituição do array `session_ids[]` por uma tabela de junção com FK real e metadados por sessão:

```
journey_id      UUID FK → journeys(journey_id)
session_id      UUID FK → sessions(id)   — FK cross-schema (via DB link ou schema compartilhado)
role            text    — collect | recontact (origem está em origin_session_id do journey)
channel         text    — canal usado nessa sessão (whatsapp, webchat, voice, ...)
sequence_idx    int     — ordem de vinculação (0-based)
outcome         text    — resolved | escalated | abandoned | failed | ...
linked_at       timestamptz
PRIMARY KEY (journey_id, session_id)
```

**Decisão de modelagem (2026-05-21):** o campo `session_ids UUID[]` não é implementado. A junction table oferece FK real, queries eficientes nos dois sentidos (`WHERE journey_id = ?` e `WHERE session_id = ?`), e metadados ricos por sessão. O campo `session_snapshot JSONB` na tabela `journeys` serve como cache de display — preenchido pelo consumer de `journey.events` Kafka, nunca é fonte de verdade.

**Quem escreve em `journey_sessions` (decisão 2026-05-21):** o `workflow-api` consome `collect.events` Kafka diretamente e faz o INSERT na junction table quando a sessão collect é criada. O Core continua escrevendo `sessions.journey_id` (campo simples na tabela de sessões). Nenhum REST cross-service entre Core e workflow-api para esse dado.

**Tabela `workflow.collect_instances` (campo adicionado):**
```
journey_id              UUID nullable FK  — propaga journey para sessões collect
```

**Tabela `sessions` (campo adicionado — agent-registry / Core):**
```
journey_id              UUID nullable     — null = sessão standalone (sem mudança no comportamento atual)
```

### Hierarquia de observabilidade

```
Journey
  └── Contato 1  (Session — origin_session_id)
        └── Segmento primary    (ContactSegment)
        └── Segmento specialist (ContactSegment)
  └── Contato 2  (Session — vinculada via collect ou manualmente)
        └── Segmento primary
  └── Contato N  ...
```

Sessões sem `journey_id` continuam funcionando exatamente como hoje. O Journey é um nível adicional acima, não uma refatoração do modelo existente.

---

## Modelo de Interseção de Sessões

Uma sessão pode ter **dois relacionamentos simultâneos e independentes** com journeys:

| Relacionamento | Campo | Quem escreve | Semântica |
|---|---|---|---|
| Sessão collect de uma journey | `journey_sessions(journey_id, session_id)` + `sessions.journey_id = Journey_A` | Core (via `collect.events` Kafka) insere na junction table e escreve `sessions.journey_id` | Sessão criada pelo `collect` step *pertence* à journey |
| Sessão de origem de outra journey | `journey_b.origin_session_id = session_id` | `journey_start` / MCP tool | Sessão foi o *ponto de início* da journey B |

**Regra fundamental:** `journey_start` e `db_create_journey` **nunca escrevem** `sessions.journey_id`. Apenas o Core escreve esse campo, exclusivamente para sessões criadas por `collect` steps.

**Exemplo canônico — uma sessão S3 como ponto de interseção:**

```
S1 → origin de Journey A (produto X)
S2 → collect de Journey A  (sessions.journey_id = A)
S3 → collect de Journey A  (sessions.journey_id = A)
   + origin de Journey B  (journey_b.origin_session_id = S3)   ← interseção
S4 → collect de Journey B  (sessions.journey_id = B)
```

Journey A e Journey B são completamente independentes — a interseção é capturada apenas pelo `origin_session_id` comum. Nenhuma relação de hierarquia ou dependência entre elas.

**Query "todas sessões de uma journey":**
```sql
SELECT session_id FROM workflow.journey_sessions WHERE journey_id = :journey_id
UNION
SELECT origin_session_id FROM workflow.journeys WHERE journey_id = :journey_id
```

---

## Formas de iniciar um Journey

### 1. MCP tool `journey_start` (forma primária)

Tool exposta em mcp-server-plughub, grupo `journey`:

```typescript
journey_start({
  skill_id:   string,
  session_id: string,           // vira origin_session_id (imutável)
  metadata?:  Record<string, unknown>,
}): { journey_id: string; workflow_instance_id: string }
```

Efeitos em sequência:
1. Cria registro `Journey` (status `active`, `origin_session_id = session_id`)
2. Chama `POST /v1/workflow/trigger` com `{ skill_id, origin_session_id, journey_id, metadata }`
3. Atualiza `Journey.workflow_instance_id`
4. Publica `journey.events` → `journey_started` no Kafka

A tool é interceptada pelo McpInterceptor (auditoria automática). **Nunca é chamada via REST direto da UI** — sempre via MCP.

### 2. Acionamento automático em skill-flow

Flag `creates_journey: true` no YAML da skill. O `skill-flow-worker` verifica a flag antes de iniciar a execução (`engine.run()`) e cria a Journey automaticamente. A falha na criação é **não-fatal** — o workflow prossegue mesmo se a criação da Journey falhar.

```yaml
skill_id: skill_portabilidade_telco_v2
creates_journey: true
steps:
  - type: task
    ...
```

### 3. Via `POST /v1/journeys/from-instance/{instance_id}`

Endpoint no workflow-api que cria uma Journey a partir de uma WorkflowInstance existente. Idempotente: se a instância já tem `journey_id`, retorna a journey existente.

### 4. Acionamento via console do agente humano

Botão "🗺️ Iniciar Processo" no `ActionBar` do `AgentAssistPage`. Dropdown filtrado por `pool.mentionable_journeys`. Ao confirmar, chama `POST /v1/journeys` com `{ tenant_id, skill_id, session_id }`.

Configuração no pool YAML:
```yaml
mentionable_journeys:
  - skill_portabilidade_telco_v2
  - skill_reembolso_v1
```

### 5. `@mention` protocol

Extensão do `@mention`: um agente primário (humano) digita `@journey:<skill_id>` no campo de texto. O Core resolve `mentionable_journeys` do pool e chama `journey_start` internamente. Sem UI especial — mesmo protocolo do `@mention` existente.

---

## Vinculação de sessões subsequentes

### Via `collect` step (automática)

O `collect` step popula `collect_instances.journey_id`. Quando o `respond_collect` recebe a resposta (`session_id` presente), `workflow-api/router.py` emite o evento `journey_session_linked` com metadados de progresso.

**Evento `journey_session_linked` — campos:**

```
journey_id           — journey vinculada
session_id           — sessão que chegou
skill_id             — skill que governa
current_step?        — step do workflow no momento do vínculo
session_outcome?     — outcome da sessão (resolved/escalated/abandoned/...)
session_started_at?  — ISO timestamp de abertura da sessão
session_ended_at?    — ISO timestamp de encerramento da sessão
```

Esses campos criam uma **linha do tempo auditável** da evolução do workflow através dos contatos, armazenada em `journey_events` no ClickHouse.

### Via MCP tool `journey_link_session` (manual)

```typescript
journey_link_session({
  journey_id:          string,
  session_id:          string,
  current_step?:       string,
  session_outcome?:    string,
  session_started_at?: string,  // ISO8601
  session_ended_at?:   string,  // ISO8601
}): void
```

Correlação automática de recontatos espontâneos (cliente recontatando sem `collect`) é fase posterior.

---

## Kafka — `journey.events`

| Topic | Producer | Consumer(s) |
|---|---|---|
| `journey.events` | workflow-api | analytics-api → ClickHouse |

**9 tipos de evento:**

```
journey_started         — journey criada, origin_session_id definida
journey_session_linked  — nova sessão vinculada (+ metadados de progresso)
journey_suspended       — workflow suspendeu (aguardando input/timer)
journey_resumed         — workflow retomado
journey_completed       — processo concluído com sucesso
journey_failed          — processo falhou
journey_cancelled       — cancelado por agente ou timeout
journey_merged          — journey secundária absorvida por primária
journey_split           — sessões collect extraídas para nova journey (Fase F)
```

Schema Zod: `JourneyEventSchema` em `@plughub/schemas/src/journey.ts`.

---

## Analytics — ClickHouse

**Tabela `analytics.journey_events`** (ReplacingMergeTree ORDER BY `(tenant_id, event_id)`):

Append-only audit log de todos os eventos. Estado atual da journey é reconstruído via `argMax`:

```sql
SELECT journey_id, argMax(status, event_time) AS latest_status
FROM analytics.journey_events FINAL
WHERE tenant_id = :tenant_id
GROUP BY journey_id
```

**Colunas principais:**

| Campo | Tipo | Descrição |
|---|---|---|
| `event_id` | String | UUID do evento |
| `tenant_id` | String | |
| `event_type` | String | Um dos 8 tipos acima |
| `journey_id` | String | |
| `skill_id` | String | |
| `workflow_instance_id` | String nullable | |
| `customer_id` | String nullable | |
| `session_id` | String nullable | Sessão associada ao evento |
| `origin_session_id` | String nullable | |
| `status` | String | Status da journey no momento do evento |
| `current_step` | String nullable | Passo do workflow (em `journey_session_linked`) |
| `session_outcome` | String nullable | Outcome da sessão vinculada |
| `session_started_at` | DateTime64 nullable | |
| `session_ended_at` | DateTime64 nullable | |
| `metadata_json` | String | Payload original serializado |
| `event_time` | DateTime64(3) | Timestamp do evento |
| `ingest_time` | DateTime64(3) | Timestamp de ingestão |

**KPIs derivados via queries `argMax`:**

- **Taxa de resolução por processo**: `countIf(latest_status = 'completed') / count()` por `skill_id` (denominador = jornadas terminais)
- **Duração mediana end-to-end**: `median(max(event_time) - min(event_time))` por `journey_id`, agrupado por `skill_id`
- **Funnel de status**: `argMax(status, event_time)` distribuído por categoria
- **Linha do tempo de progresso**: `session_outcome` + `current_step` por `session_id` ordenados por `event_time`

**Endpoint REST:** `GET /reports/journeys` — aceita `tenant_id`, `from_dt`, `to_dt`, `skill_id`, `status`, `customer_id`. Retorna dados + KPIs + meta.

---

## Frontend

### ProcessosPage (Monitor → `/workflow/monitor`)

Dois tabs: **Jornadas** e **Instâncias** (instâncias existentes preservadas).

**Tab Jornadas:**
- KPI strip: total ativas, taxa de resolução, duração mediana
- Lista de jornadas com colunas `skill_id`, `customer_id`, `status`, `created_at`
- Painel de detalhe lateral com sessões vinculadas e botão "⛓ Unir jornadas" (sticky footer, visível para `active`/`suspended`)

Hooks: `useJourneys(filters)`, `useJourney(journeyId)` em `platform-ui/src/modules/agent-flow/hooks.ts`.

### AgentAssistPage — HistoricoTab

Seção "Processos em aberto" no topo da aba Histórico: jornadas `active` e `suspended` do `customer_id` atual. Badges coloridos por status. Renderizada antes de "Contatos anteriores".

### AgentAssistPage — ActionBar

Botão "🗺️ Processo ▾" visível quando `pool.mentionable_journeys.length > 0`. Dropdown com label humanizado (remove prefixo `skill_` e sufixo `_vN`). Ao confirmar, chama `POST /v1/journeys`.

### Dashboard — Cards de Jornada (Phase E)

4 cards registrados no Display Tool Registry (`catalog.ts`), todos filtrável por `skill_id`:

| Card ID | Tool | Tamanho | Descrição |
|---|---|---|---|
| `journey-active-count` | `metric_card` | 3×2 | Jornadas ativas com tendência vs período anterior |
| `journey-resolution-rate` | `bar_chart` | 6×4 | Taxa de resolução % por `skill_id` (jornadas terminais) |
| `journey-funnel` | `donut` | 4×3 | Distribuição por status |
| `journey-median-duration` | `bar_chart` | 6×4 | Duração mediana em minutos por `skill_id` |

Endpoints: `GET /reports/display/journey-*` em `analytics-api/display.py`. Queries usam `argMax(status, event_time)` sobre `journey_events FINAL`.

---

## MCP tools

Todas em `mcp-server-plughub/src/tools/journey.ts`, grupo `journey`:

| Tool | Descrição |
|---|---|
| `journey_start(skill_id, session_id, metadata?)` | Cria journey + dispara workflow |
| `journey_link_session(journey_id, session_id, ...)` | Vincula sessão manualmente com metadados de progresso |
| `journey_merge(primary_id, secondary_id)` | Une dois journeys; secundário → `status: merged` |

---

## Workflow API — Endpoints

Prefixo `/v1/journeys` (workflow-api, porta 3800):

| Método | Path | Descrição |
|---|---|---|
| `POST` | `/v1/journeys` | Cria journey (usado pelo Console) |
| `GET` | `/v1/journeys/{id}` | Detalhe |
| `GET` | `/v1/journeys` | Lista com filtros |
| `POST` | `/v1/journeys/{id}/link-session` | Vincula sessão |
| `POST` | `/v1/journeys/merge` | Une dois journeys |
| `POST` | `/v1/journeys/from-instance/{instance_id}` | Cria journey a partir de instância existente |

---

## Invariants

- **`journey_id` nunca é igual a `workflow_instance_id`** — entidades distintas com ciclos de vida independentes
- **`journey_start` é sempre chamado via MCP tool** — nunca REST direto da UI; garante auditoria pelo McpInterceptor
- **`origin_session_id` é imutável** após criação da Journey
- **`sessions.journey_id` é escrito apenas para sessões collect** — `journey_start` nunca toca `sessions.journey_id`
- **Múltiplas journeys podem compartilhar `origin_session_id`** — cada journey é independente; sem hierarquia entre elas
- **Uma Journey ativa tem no máximo um WorkflowInstance ativo** — instances múltiplos são sequenciais (retry), nunca paralelos
- **Journey com `status: merged` é somente leitura** — nenhuma operação pode ter uma journey merged como alvo primário
- **`journey_merge` é irreversível** — não existe unmerge; use split se necessário
- **Falha em `creates_journey` é não-fatal** — o workflow continua mesmo sem journey criada
- **Sessões standalone não são afetadas** — `journey_id` é sempre nullable em sessions

---

## Integração com o Arc 16 — Three-Tier Business Process Orchestration

O Arc 16 estende o modelo de Journey para servir como superfície pública de orquestração de processos de negócio:

- **`pool_id` na Journey**: a tabela `workflow.journeys` ganhou a coluna `pool_id` (persistida na criação), usada para filtro e access control. `GET /v1/journeys` aceita `pool_id` como filtro — habilita o padrão de poller workflow (Tier 1 que monitora e retoma journeys de outros processos).
- **Namespace `@ctx.journey.*`**: novo Redis hash `{tenantId}:ctx:journey:{journeyId}` compartilhado entre todas as sessões da Journey (TTL 30 dias). Resolve o problema de dados coletados em sessões `collect` serem invisíveis ao Business Workflow. `context_tags.outputs` com prefixo `journey.*` redireciona a escrita para esse hash.
- **MCP tools do Tier 1 poller**: `journey_list_suspended(pool_id, skill_id?, limit?)` e `journey_resume(journey_id, context?, decision)` encapsulam o `resume_token` interno — auditadas via McpInterceptor; permissão ABAC `workflows.journey.read`/`journey.resume`.
- **`journey_check_pending(customer_id, channel?, limit?)`**: descobre journeys com `collect` pendente para um cliente (Inbound Journey Resume, Arc 16 Fase E).
- **Endpoints públicos**: `GET /v1/journeys?pool_id=...&status=suspended` e `POST /v1/journeys/{id}/resume` (callers passam apenas `context` + `decision`).

→ Ver [`docs/arcos/arc16-flow-orchestration.md`](arc16-flow-orchestration.md).

---

## Fase F — Split de jornadas *(implementado)*

Extrai um subconjunto de sessões de uma journey para uma nova journey independente. Caso de uso típico: durante a execução, descobre-se que algumas sessões pertencem a um processo diferente do que foi originalmente iniciado.

### Decisões resolvidas

| Decisão | Resolução |
|---|---|
| `workflow_instance_id` após split | Permanece na journey **original**. O workflow é o motor do processo original; as sessões são realocadas, não o processo. |
| Nova journey recebe workflow? | Inicia com `workflow_instance_id = null`, status `active`. Parâmetro opcional `skill_id` dispara novo workflow imediatamente. Default: sem workflow. |
| Restrições sobre `origin_session_id` | Ver seção Restrições abaixo. |

### MCP tool `journey_split`

```typescript
journey_split({
  journey_id:   string,          // journey origem
  session_ids:  string[],        // sessões collect a extrair (mín. 1)
  skill_id?:    string,          // se passado, dispara novo workflow para a nova journey
  metadata?:    Record<string, unknown>,
}): {
  new_journey_id:          string,
  new_workflow_instance_id: string | null,  // null se skill_id não passado
}
```

**Efeitos em sequência:**
1. Valida restrições (ver abaixo) — retorna `400` se violado
2. Cria nova journey com:
   - `origin_session_id` = primeira sessão do conjunto (ordenada por `started_at`)
   - `split_from_journey_id` = `journey_id` (campo novo, nullable UUID)
   - `workflow_instance_id = null`, status `active`
3. Para cada `session_id` em `session_ids[]`: atualiza `sessions.journey_id` = `new_journey_id`
4. Se `skill_id` passado: chama `POST /v1/workflow/trigger` e atualiza `new_journey.workflow_instance_id`
5. Publica `journey_split` no Kafka `journey.events` (source + destination)
6. Interceptado pelo McpInterceptor (auditoria automática)

### Restrições

- **Somente sessões collect**: `session_ids[]` deve conter apenas sessões com `sessions.journey_id = source_journey_id`. Sessões standalone ou de outra journey retornam `400`.
- **`origin_session_id` protegido**: se `session_ids[]` incluir o `origin_session_id` da journey origem, retorna `400 origin_session_cannot_be_split`.
- **Journey merged é somente leitura**: se `source_journey.status = merged`, retorna `409`.
- **Mínimo 1 sessão**: `session_ids[]` vazio retorna `400`.

### Novo campo no modelo de dados

```
split_from_journey_id   UUID nullable   — preenchido na journey derivada de um split
```

Adicionado à tabela `workflow.journeys` e ao evento `journey_started` quando originado de split.

### Evento Kafka — `journey_split`

Nono tipo adicionado ao `journey.events`:

```
journey_split     — source_journey_id, new_journey_id, session_ids[], session_count
```

Schema Zod: campo `journey_split` adicionado ao `JourneyEventSchema` em `@plughub/schemas/src/journey.ts`.

### UI

Painel de sessões da journey no Monitor (tab Jornadas → detalhe lateral):
- Modo de seleção múltipla de sessões collect com checkboxes
- Botão "Separar em nova jornada" (sticky footer) — ativo quando ≥ 1 sessão selecionada e nenhuma é a `origin_session_id`
- Drawer de confirmação com campo opcional de `skill_id` (dropdown filtrado por `pool.mentionable_journeys` do tenant) e preview das sessões a extrair
- Após confirmação: navega para a nova journey no Monitor

### Invariants adicionais

- **`journey_split` é irreversível** — use `journey_merge` se necessário reagrupar
- **`split_from_journey_id` é imutável** após criação da journey derivada
- **`origin_session_id` da journey origem nunca muda** — o split nunca toca esse campo
