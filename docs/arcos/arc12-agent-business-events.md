# Arc 12 — Agent Business Events

> Última atualização: 2026-05-25 · Estado: Arc 16 · Status: Fases A–E concluídas (2026-05-14)

Permite que agentes (AI e humanos) publiquem KPIs de negócio estruturados durante sessões, via MCP tool `agent_event`. Os eventos são armazenados no ClickHouse e integrados ao módulo de análise de qualidade como métricas sobrepostas.

---

## Visão Geral

O `agent_event` é o mecanismo de telemetria de negócio do PlugHub. Em vez de capturar apenas métricas de qualidade de atendimento (resolução, escalação, avaliação), os agentes podem emitir KPIs específicos do domínio — NPS coletado, valor de contrato capturado, produto identificado, score de propensão calculado, etc.

**Hierarquia de categoria**: dot notation em até 4 níveis — `pool_id.skill_id.metric_key` (ex: `retencao_humano.skill_portabilidade_v1.nps`). O primeiro segmento é obrigatoriamente o `pool_id` da sessão — namespace isolation por pool.

**Contexto automático**: o agente só passa `category`, `value` e `tags?`. O resto — `tenant_id`, `agent_type_id`, `skill_id`, `pool_id`, `journey_id`, `session_id` — é resolvido automaticamente do `session_token` pelo McpInterceptor.

> ### ⚠️ Desambiguação de nomes
>
> Três nomes quase idênticos, para **duas** coisas diferentes:
>
> | Nome | O que é |
> |---|---|
> | tool `agent_event` (singular) | **este arco** — porta única do eixo de marcação |
> | tabela `agent_business_events` | **este arco** — onde a marcação é gravada |
> | rotas `/reports/agent-events/*` | **este arco** — leem `agent_business_events` |
> | ~~tabela `agent_events`~~ | **NÃO é este arco** — lifecycle derivado (`routed`/`agent_done`), descontinuada em 2026-07-28 por duplicar `segments`; **dropada em 2026-07-29** (fatia 2) |
>
> A colisão já induziu erro na documentação do próprio projeto. Regra para decidir:
> **o eixo de marcação tem porta única — a tool `agent_event`.** O que não passa por ela
> é substrato derivado (`sessions`/`segments`/`messages`), não marcação.

---

## Fase A — MCP Tool + Infraestrutura ClickHouse

### MCP tool `agent_event`

**`packages/mcp-server-plughub/tools/agent_event.ts`**

```typescript
// Assinatura pública (o agente vê só isso)
agent_event(category: string, value: number, tags?: Record<string, string>)
```

**Restrições de governança** (aplicadas pelo McpInterceptor):
- `category[0]` deve ser o `pool_id` da sessão — bloqueia namespace pollution cross-pool
- Tags com PII keywords bloqueadas: `cpf`, `phone`, `email`, `token`, `senha`, `password`, `document`, `cnpj`
- Máximo 10 tags por evento; chave e valor limitados a 64 chars
- Rate limit: 50 eventos por sessão (configurável via Config API `agent_events.rate_limit`)
- Todo call auditado em `mcp.audit` — não é possível opt out

**Kafka topic**: `agent.events` — `AgentBusinessEventSchema` do `@plughub/schemas`

### ClickHouse — `analytics.agent_business_events`

```sql
CREATE TABLE analytics.agent_business_events (
    event_id        String,
    tenant_id       String,
    session_id      String,
    agent_type_id   String,
    skill_id        Nullable(String),
    pool_id         String,
    journey_id      Nullable(String),
    category        String,
    category_l1     String,   -- pool_id
    category_l2     Nullable(String),
    category_l3     Nullable(String),
    category_l4     Nullable(String),
    value           Float64,
    tags            Map(String, String),
    recorded_at     DateTime64(3, 'UTC')
) ENGINE = MergeTree()
ORDER BY (tenant_id, category, recorded_at)
TTL recorded_at + INTERVAL 2 YEAR;
```

Os campos `category_l1..l4` são pré-decompostos no consumer para evitar `splitByChar` em queries de análise.

### analytics-api — consumer.py

- `parse_agent_business_event()`: parser do tópico `agent.events` → linha para `agent_business_events`
- `_DDL_AGENT_BUSINESS_EVENTS`: DDL da tabela
- `insert_agent_business_event()`: método de inserção assíncrona em `AnalyticsStore`

---

## Fase B — Analytics Endpoints

Três endpoints em `analytics-api/reports.py`:

### `GET /reports/agent-events/series`

Série temporal (diária ou semanal) de `AVG(value)` para uma categoria.

**Params**: `tenant_id`, `category`, `from`, `to`, `pool_id?`, `granularity` (day|week)

**Response**:
```json
{
  "data": [{ "date": "2026-05-01", "avg_value": 8.2, "count": 145 }],
  "deploy_markers": [{ "date": "2026-05-07", "skill_id": "...", "notes": "..." }]
}
```

Deploy markers reutilizam `analytics.deploy_events` do Arc 6 Fase 2 — mesma fonte de âncoras.

### `GET /reports/agent-events/summary`

Estatísticas agregadas no período: `total`, `avg`, `min`, `max`, `count`.

**Params**: `tenant_id`, `category`, `from`, `to`, `pool_id?`

**Response**:
```json
{
  "data": { "category": "...", "total": 1203.5, "avg": 8.3, "min": 1.0, "max": 10.0, "count": 145 }
}
```

### `GET /reports/agent-events/categories`

Lista todas as categorias distintas registradas pelo tenant, com contagem de eventos.

**Params**: `tenant_id`

**Response**:
```json
{
  "data": [
    { "category": "retencao_humano.skill_portabilidade_v1.nps", "count": 1203 },
    { "category": "retencao_humano.skill_portabilidade_v1.valor_contrato", "count": 892 }
  ]
}
```

---

## Fase C — Integração com Análise de Qualidade

Os endpoints de Arc 6 Fase 2 passam a aceitar `metrics[]=agent_event:{category}` como parâmetro opcional.

### `GET /reports/quality-comparison`

Aceita `metrics[]=agent_event:retencao_humano.skill_portabilidade_v1.nps` (repetível).

Para cada `agent_event:{category}` na lista, `_fetch_agent_event_slice()` executa query paralela em `agent_business_events` e adiciona o resultado ao dict `metrics` da fatia retornada.

`_compute_delta()` foi refatorado para ser key-agnostic — funciona com qualquer chave de métrica, não só as 4 base.

### `GET /reports/quality-timeseries`

Aceita `metrics[]` da mesma forma. `_fetch_agent_event_timeseries()` retorna pontos diários de `AVG(value)` que são mesclados na série temporal retornada.

### `GET /reports/quality-metrics`

Endpoint single-slice do `ComparisonGroupBuilder`. Aceita os mesmos `metrics[]` — chama `_fetch_quality_slice()` com a lista expandida.

---

## Fase D — Dashboard Cards

### catalog.ts — 2 novas entradas

**`agent-event-timeseries`** (endpoint `/reports/display/agent-event-timeseries`):
- `compatible_tools: ['line_chart', 'bar_chart']`, `default_tool: 'line_chart'`
- `configurable_params`: `category` (required, com `options_from: '/reports/agent-events/categories'`) + `pool_id` (optional)

**`agent-event-summary`** (endpoint `/reports/display/agent-event-summary`):
- `compatible_tools: ['metric_card', 'table']`, `default_tool: 'metric_card'`
- `configurable_params`: `category` (required, com `options_from`) + `pool_id` (optional)

### ConfigurableParam.options_from

Nova propriedade em `EndpointDescriptor.ConfigurableParam` (`catalog.ts`):

```typescript
interface ConfigurableParam {
  key:          string
  label:        string
  placeholder:  string
  optional:     boolean
  options_from?: string  // URL; quando presente, AddCardModal renderiza <select>
}
```

### AddCardModal.tsx — seletor dinâmico

`StepConfigure` ganhou um `useEffect` que, para cada param com `options_from`, faz `fetch(url + '?tenant_id=...')` e popula `paramOptions[param.key]` com os valores de `data[].category`.

No loop de params: quando `paramOptions[param.key]` existe (array carregado ou vazio), renderiza `<select>` em vez de `<input type="text">`. O `<select>` tem opção vazia para params opcionais ou placeholder desabilitado para obrigatórios. Loading state mostra `<input placeholder="Carregando opções…">` enquanto fetch não retornou.

---

## Fase E — Integração com Módulo de Análise (Páginas Qualidade/Comparação)

### MetricSelector.tsx (novo componente compartilhado)

**`packages/platform-ui/src/modules/analise/MetricSelector.tsx`**

Exportações:

| Export | Tipo | Descrição |
|--------|------|-----------|
| `MetricDef` | interface | `{ key: string, label, format, higherIsBetter, color? }` |
| `BASE_METRIC_DEFS` | `MetricDef[]` | 4 métricas base (evaluation_score, resolution_rate, escalation_rate, aht_ms) |
| `BASE_METRIC_KEYS` | `string[]` | Keys das 4 base |
| `makeAgentEventDef(cat, idx)` | função | Cria `MetricDef` para `agent_event:{category}` |
| `buildMetricDefs(selected)` | função | Filtra base + cria defs para agent_event entries |
| `MetricSelector` | componente | Pills toggles + picker "+ Evento" |

**`MetricSelector` component**:
- Pills para as 4 métricas base (toggle on/off)
- Pills removíveis para `agent_event:*` já selecionados
- Botão "+ Evento" abre dropdown lazy-loaded com `GET /reports/agent-events/categories`
- Fechamento por clique fora via `useRef` + `useEffect`
- Props: `selected: string[]`, `onChange: (metrics: string[]) => void`, `tenantId: string`

**Cores dos agent_event overlays**: `['#7C3AED', '#0891B2', '#BE185D', '#15803D', '#B45309', '#6D28D9']`

### AnaliseComparacaoPage.tsx

- Removidas definições inline de `MetricDef`, `BASE_METRIC_DEFS`, `buildMetricDefs`; agora importadas de `./MetricSelector`
- `SliceMetrics.metrics: Record<string, number | null>` (era `{ evaluation_score, resolution_rate, escalation_rate, aht_ms }`)
- Estado `selectedMetrics: string[]` (default: `BASE_METRIC_KEYS`), derivado `metricDefs = buildMetricDefs(selectedMetrics)`
- `fetchSlice()` append `metrics[]=...` para cada `agent_event:*` em `selectedMetrics`
- `<MetricSelector>` adicionado na seção de filtros globais
- `<GroupedBarChart>` e `<MetricTable>` recebem `metricDefs: MetricDef[]` e iteram dinamicamente

**`metricToChartValue(key, raw)`**: chave-aware — `agent_event:*` usa raw direto; `aht_ms` divide por 60000; outros ×100.

### AnaliseQualidadePage.tsx

**TimeseriesView**:
- `selectedMetrics: string[]` (default: `['evaluation_score']` — apenas score inicial)
- `metricDefs = buildMetricDefs(selectedMetrics)`
- `load()` append `metrics[]` para agent_event keys; `selectedMetrics` em deps
- `chartData`: objeto com `score` (evaluation_score ×100) + chaves `agent_event:*` raw
- `YAxis` sem domain fixo (auto-escala para acomodar valores arbitrários)
- `<Legend>` adicionado
- `<Line dataKey="score">` para evaluation_score (quando selecionado)
- `{metricDefs.filter(...agent_event).map(d => <Line dataKey={d.key}>)}` — dinâmico
- Tooltip dinâmico com label da `MetricDef` e formatação correta por tipo

**ComparisonView**:
- `selectedMetrics: string[]` (default: `BASE_METRIC_KEYS`)
- `metricDefs = buildMetricDefs(selectedMetrics)`
- `run()` append `metrics[]`; `selectedMetrics` em `useCallback` deps
- `<MetricSelector>` após os formulários de slice
- `metricDefs.map(def => <MetricComparisonRow>)` — substitui as 4 chamadas hardcoded

**Tratamento de `aht_ms` no ComparisonView**: `toDisplay(v)` divide por 60000 antes de passar ao componente; `formatter` inverte (multiplica por 60000) antes de chamar `def.format()`, que espera ms.

---

## Integração Kafka

| Topic | Producer | Consumer |
|-------|----------|----------|
| `agent.events` | mcp-server-plughub (McpInterceptor) | analytics-api |
| `mcp.audit` | McpInterceptor | Analytics, LGPD |

O `agent_event` tool passa pelo `McpInterceptor` como qualquer outra tool — auditado automaticamente.

---

## Governança e Rate Limiting

| Restrição | Valor default | Config API key |
|-----------|--------------|----------------|
| Rate limit por sessão | 50 eventos | `agent_events.rate_limit` |
| Máximo de tags | 10 | hardcoded |
| Tamanho máximo de chave/valor de tag | 64 chars | hardcoded |
| TTL dos dados no ClickHouse | 2 anos | DDL (`TTL recorded_at + INTERVAL 2 YEAR`) |

PII keywords bloqueadas nas tags (lista não exaustiva): `cpf`, `phone`, `email`, `token`, `senha`, `password`, `document`, `cnpj`. Qualquer tag que contenha essas palavras como substring no nome da chave é rejeitada pelo McpInterceptor.

---

## Exemplo de Uso

```typescript
// Em um skill YAML — step invoke
- id: collect_nps
  type: invoke
  tool: agent_event
  inputs:
    category: "retencao_humano.skill_portabilidade_v1.nps"
    value: "@ctx.session.nps_collected"
    tags:
      channel: "@ctx.session.channel"
      product: "portabilidade"
```

```typescript
// Em um agente SDK nativo
await mcp.call('agent_event', {
  category: 'retencao_humano.skill_portabilidade_v1.valor_contrato',
  value: 299.90,
  tags: { plan: 'gold' }
})
```

O contexto de sessão (tenant_id, pool_id, etc.) é injetado automaticamente — o agente não precisa passá-los.
