# Módulo: ClickHouse Consumer (Piloto)

> Responsabilidade: consumir `evaluation.completed` de `evaluation.results`
> e persistir scores e itens nas tabelas ClickHouse do piloto.
> Pré-requisito do Dashboard.

---

## Visão geral

Consumer Kafka de responsabilidade única. Não tem lógica de negócio.
Lê `evaluation.completed`, extrai as entradas de scores e itens,
e escreve nas duas tabelas ClickHouse definidas no Evaluation Agent.

É o único componente da plataforma que escreve no ClickHouse.
O Dashboard lê diretamente do ClickHouse — sem API intermediária.

---

## Tópico consumido

| Tópico | Conteúdo |
|---|---|
| `evaluation.results` | Eventos `evaluation.completed` publicados pelo Evaluation Agent |

---

## Fluxo de processamento

```
evaluation.completed (evaluation.results)
  ↓
  Para cada entrada em scores[]:
    INSERT INTO evaluation_scores (...)
    Para cada subsection em subsections[]:
      Para cada item em items[]:
        INSERT INTO evaluation_items (...)
  ↓
  Commit offset Kafka
```

A escrita é feita em batch por evento — todas as linhas de um
`evaluation.completed` são inseridas numa única transação antes do
commit do offset. Se a inserção falhar, o evento é reprocessado.

---

## Mapeamento evaluation.completed → tabelas

### `evaluation_scores`

Uma linha por entrada em `scores[]`:

```python
for score in event["scores"]:
    INSERT INTO evaluation_scores VALUES (
        evaluation_id   = event["evaluation_id"],
        contact_id      = event["contact_id"],
        agent_id        = event["agent_id"],
        agent_type      = event["agent_type"],
        pool_id         = event["pool_id"],
        skill_id        = event["skill_id"],
        section_id      = score["section_id"],
        score_type      = score["score_type"],       -- 'base_score' | 'context_score'
        score           = score["score"],
        triggered_by_key = score.get("triggered_by", {}).get key,
        triggered_by_val = score.get("triggered_by", {}).get value,
        evaluated_at    = event["evaluated_at"],
        triggered_by_src = event["triggered_by"]
    )
```

### `evaluation_items`

Uma linha por item em cada sub-seção de cada seção:

```python
for score in event["scores"]:
    for subsection in score["subsections"]:
        for item in subsection["items"]:
            INSERT INTO evaluation_items VALUES (
                evaluation_id = event["evaluation_id"],
                contact_id    = event["contact_id"],
                agent_id      = event["agent_id"],
                pool_id       = event["pool_id"],
                section_id    = score["section_id"],
                subsection_id = subsection["subsection_id"],
                item_id       = item["item_id"],
                value         = item["value"],
                weight        = item["weight"],
                justification = item["justification"],
                evaluated_at  = event["evaluated_at"]
            )
```

---

## DDL das tabelas ClickHouse

```sql
-- Scores por seção: agregações rápidas para dashboard
CREATE TABLE IF NOT EXISTS evaluation_scores (
    evaluation_id     UUID,
    contact_id        UUID,
    agent_id          UUID,
    agent_type        Enum8('human' = 1, 'ai' = 2),
    pool_id           String,
    skill_id          String,
    section_id        String,
    score_type        Enum8('base_score' = 1, 'context_score' = 2),
    score             Float32,
    triggered_by_key  Nullable(String),
    triggered_by_val  Nullable(String),
    evaluated_at      DateTime,
    triggered_by_src  String
) ENGINE = MergeTree()
  PARTITION BY toYYYYMM(evaluated_at)
  ORDER BY (pool_id, agent_id, section_id, evaluated_at);

-- Itens individuais: drill-down até justificativa
CREATE TABLE IF NOT EXISTS evaluation_items (
    evaluation_id   UUID,
    contact_id      UUID,
    agent_id        UUID,
    pool_id         String,
    section_id      String,
    subsection_id   String,
    item_id         String,
    value           UInt8,
    weight          UInt8,
    justification   String,
    evaluated_at    DateTime
) ENGINE = MergeTree()
  PARTITION BY toYYYYMM(evaluated_at)
  ORDER BY (pool_id, agent_id, section_id, item_id, evaluated_at);
```

O DDL é executado na inicialização do consumer com `CREATE TABLE IF NOT EXISTS`
— sem migrations separadas no piloto.

---

## Configuração

```yaml
clickhouse_consumer:
  kafka:
    consumer_group: clickhouse-consumer
    topic: evaluation.results
    auto_offset_reset: earliest
  clickhouse:
    host: localhost
    port: 8123
    database: plughub
    batch_timeout_ms: 500     # flush parcial se não acumular batch completo
    batch_size: 100           # flush quando atingir N eventos
```

---

## O que o consumer não faz

- Não calcula nem transforma scores — recebe valores já calculados
- Não valida o conteúdo da avaliação — confia no Evaluation Agent
- Não expõe API — o Dashboard lê diretamente do ClickHouse
- Não escreve em PostgreSQL ou Redis

---

## Relações com outros módulos

| Módulo | Relação |
|---|---|
| `evaluation.results` (Kafka) | Fonte — consome `evaluation.completed` |
| `Evaluation Agent` | Produtor do evento consumido |
| `ClickHouse` | Destino — escreve `evaluation_scores` e `evaluation_items` |
| `Dashboard` | Leitor — consome as tabelas via queries diretas |
