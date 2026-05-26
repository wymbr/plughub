# Módulo: ClickHouse Consumer (analytics-api)

> Última atualização: 2026-05-25 · Estado: Arc 16

> **Responsabilidade:** consumir múltiplos tópicos Kafka de eventos analíticos e
> persistir as linhas resultantes nas tabelas ClickHouse da `analytics-api`.

---

## Visão geral

O ClickHouse consumer é o pipeline de ingestão da `analytics-api`. Não é um
serviço isolado: o consumer (`consumer.py`) roda dentro da `analytics-api`, que
também expõe os endpoints REST `/reports/*` lidos pela `platform-ui`.

> **Importante:** a `analytics-api` **não** é o único componente que escreve no
> ClickHouse, e a `platform-ui` **não** lê o ClickHouse diretamente. A UI consome
> exclusivamente os endpoints `/reports/*` da `analytics-api`, que executam as
> queries ClickHouse server-side (com pool scoping e scope de supervisão aplicados).

O consumer tem responsabilidade única de ingestão: para cada tópico há um parser
em `_PARSERS` que transforma o evento Kafka em uma ou mais linhas de tabela; o
`_write_row` despacha cada linha para o método de `store` correspondente.

---

## Tópicos consumidos

A `analytics-api` consome um conjunto amplo de tópicos analíticos:

| Tópico | Tabela(s) ClickHouse alimentada(s) |
|---|---|
| `evaluation.events` | `evaluation_results`, `evaluation_events` |
| `conversations.participants` | `analytics.segments`, `session_timeline` |
| `journey.events` | `journey_events` |
| `collect.events` | tabela de eventos de collect |
| `calibration.events` | `calibration_events` |
| `mcp.audit` | `mcp_audit_log` (+ `session_timeline`) |
| `agent.events` | `analytics.agent_business_events` |
| `evaluation.requested` / `conversations.session_closed` | `session_events` (via Stream Persister) |
| `deploy_events` (de `registry.changed`) | `analytics.deploy_events` |

A lista exata de tópicos vive em `_TOPICS` no `consumer.py`. Cada novo tópico
analítico adiciona uma entrada em `_TOPICS` e um parser em `_PARSERS`.

---

## Fluxo de processamento

```
evento Kafka (qualquer tópico de _TOPICS)
  ↓
_PARSERS[topic](msg)  →  lista de rows { "table": <nome>, ... }
  ↓
para cada row:
  _write_row(row)  →  store.<método de upsert/insert da tabela>
  ↓
Commit offset Kafka
```

Alguns parsers fazem **dual-write**: por exemplo, `parse_mcp_audit_event()`
retorna duas linhas — uma para `session_timeline` e outra para `mcp_audit_log`.
`parse_evaluation_event()` retorna uma linha de estado (`evaluation_results`) e
uma linha de log (`evaluation_events`).

---

## Tabelas de avaliação (Arc 6)

As tabelas atuais de avaliação são `evaluation_results` e `evaluation_events`
— **não** existem mais `evaluation_scores`/`evaluation_items`.

```sql
-- Estado atual de cada resultado (ReplacingMergeTree — último eval_status vence)
CREATE TABLE analytics.evaluation_results (
    result_id        String,
    instance_id      String,
    session_id       String,
    tenant_id        String,
    evaluator_id     String,
    form_id          String,
    campaign_id      Nullable(String),
    overall_score    Float64,
    eval_status      String,
    locked           UInt8,
    compliance_flags Array(String),
    timestamp        DateTime,
    ingested_at      DateTime DEFAULT now()
) ENGINE = ReplacingMergeTree(ingested_at)
  ORDER BY (tenant_id, result_id);

-- Log append-only de eventos (submitted/reviewed/contested/locked)
CREATE TABLE analytics.evaluation_events (
    event_id      String,
    result_id     String,
    session_id    String,
    tenant_id     String,
    event_type    String,
    actor_id      String,
    eval_status   String,
    overall_score Nullable(Float64),
    timestamp     DateTime,
    ingested_at   DateTime DEFAULT now()
) ENGINE = MergeTree()
  ORDER BY (tenant_id, result_id, timestamp);
```

→ Ver [`docs/arcos/arc6-evaluation.md`](../arcos/arc6-evaluation.md) para o modelo
completo de tabelas (segmentos, calibração, journey, deploy events, agent business
events).

---

## O que o consumer não faz

- Não calcula nem transforma métricas de negócio — recebe valores já calculados
- Não valida regras de domínio — confia nos produtores dos eventos
- Não expõe API — os endpoints `/reports/*` são da `analytics-api` (mesmo processo)
- O consumer em si não escreve em PostgreSQL ou Redis — apenas ClickHouse

---

## Relações com outros módulos

| Módulo | Relação |
|---|---|
| `evaluation-api` | Produtor de `evaluation.events` e `calibration.events` |
| `orchestrator-bridge` | Produtor de `conversations.participants` |
| `workflow-api` | Produtor de `journey.events` e `collect.events` |
| `McpInterceptor` / proxy sidecar | Produtor de `mcp.audit` |
| `Agent Registry` | Produtor de `registry.changed` (origem de `deploy_events`) |
| `ClickHouse` | Destino — dezenas de tabelas analíticas |
| `platform-ui` | Consome os endpoints `/reports/*` da `analytics-api` — nunca o ClickHouse direto |
