---
name: data-engineering
description: Método de dados do PlugHub — ClickHouse, Kafka e fatos derivados. Use ao criar ou alterar tabela, coluna, DDL ou migração ClickHouse (ReplacingMergeTree, row_version, ORDER BY, materialized view, _MIGRATIONS em analytics-api/clickhouse.py); ao escrever query de relatório (FINAL, any(), alias, agregação); ao criar ou mexer em produtor ou consumidor Kafka (publish, key, partição, tópico novo, schema Zod de evento); ao gerar id determinístico (uuid5); ao carimbar fato em segmento/sessão/journey; ao consultar ClickHouse ao vivo; e ao investigar linha ausente, duplicada ou sobrescrita em analytics.
---

# data-engineering — a linha que sobrevive é a que você decidiu

Em ClickHouse + Kafka quase todo defeito deste repositório foi **silencioso**: uma linha
substituída, um evento fora de ordem, uma espera que deixou de existir. Nada fica vermelho;
o número só fica plausível e errado. O porquê de cada regra, com medição, está em
[`references/casos-medidos.md`](references/casos-medidos.md).

## 1. Tabela ClickHouse nova ou alterada

1. **Engine: `ReplacingMergeTree(row_version)`**, com
   `row_version DateTime64(3, 'UTC') DEFAULT coalesce(<fim>, <início>)`. A versão é do
   **EVENTO**, nunca da inserção, e na resolução do fenômeno (ms, não segundo). Sem coluna de
   versão, ou com `ingested_at` em segundo, o fechamento perde para a abertura (§1).
2. **`ORDER BY` É a identidade da linha.** Começa por `tenant_id` e tem de conter o
   discriminador do fato que se quer contar. Pergunte: *quais dois fatos legítimos colidem
   nesta chave?* (`participation_intervals` colide dois segmentos do mesmo participante no
   resume — por isso a testemunha por-segmento é `segments`.)
3. **RMT substitui a LINHA INTEIRA.** Todo writer manda a linha completa ou é reidratado antes
   de escrever. Coluna que só um writer conhece é apagada pelo próximo.
4. **Migração**: `ALTER TABLE {db}.<t> ADD COLUMN IF NOT EXISTS …`, idempotente, acrescentada
   ao fim de `_MIGRATIONS` em `packages/analytics-api/src/plughub_analytics_api/clickhouse.py`,
   com comentário de uma linha dizendo o arco. **Trocar a engine de tabela existente** (ex.: para
   `ReplacingMergeTree(row_version)`) não é `ALTER`: use `_migrate_row_version` no mesmo arquivo,
   que reconstrói idempotente e recria as views dependentes. Ordem de DDL importa (MV depois da tabela) e só
   instalação limpa a prova — ver skill `testing-pattern` § Rodar.
5. **Carimbo que se perde na saída é forward-only**: não há migração que o recupere. Declare o
   corte da série em data (padrão: `sla_source.SEGMENT_SLA_EPOCH`) em vez de fallback para
   outra fonte, que misturaria duas fontes num número sem dizer qual respondeu. Linha que
   deveria ter carimbo e não tem vira **contador**, nunca silêncio.
6. **Comentário de DDL ou docstring que promete ordem/unicidade** ("left é sempre inserido
   depois de joined") só vale se houver mecanismo. Sem mecanismo, corrija o comentário — ele
   já custou um defeito duas vezes.

## 2. Query de relatório

- **Leitura de RMT usa `FINAL`** (convenção dominante: `reports_query.py`, `sessions.py`).
  Antes do merge há duplicatas, e `count()` sem `FINAL` conta versões, não fatos.
- **Alias de agregado NUNCA repete nome de coluna real**: `any(pool_id) AS pool_id` derruba a
  query inteira (code 184). Sufixe `_ref` e renomeie na camada Python (§4).
- **O `except` do wrapper loga o texto da exceção** — `data: []` por erro é indistinguível de
  "não há dado".
- **Nunca somar `segments.duration_ms` para obter tempo de sessão**: segmentos se sobrepõem
  (mention, conferência, hooks). `elapsed_time_ms` (wall-clock) e `agent_time_ms`
  (Σ segmentos `primary`/`specialist`, sem `system`) são grandezas diferentes e não se comparam.
- **Escopo do fato decide a tabela**: segmento (janela de um participante) · sessão (um
  acesso) · journey (derivada, nunca entidade). SLA é do segmento de espera; o pool da sessão é
  o de ENTRADA, o do segmento é quem ATENDE — filtrar por "pool" sem dizer qual mente.

## 3. Produtor e consumidor Kafka

1. **Todo publish de evento que descreve um objeto com ciclo de vida leva `key`** = id desse
   objeto (`session_id`, `segment_id`…). Sem chave não há ordem entre abre/fecha (§2). Modelo:
   publish de `conversations.participants` em `orchestrator-bridge/main.py`.
2. **Tópico cross-package tem schema Zod em `@plughub/schemas`** e linha nas duas tabelas do
   `CLAUDE.md` (*Kafka Topics* e *Kafka Event Schemas*). Nunca redefinir o tipo localmente.
3. **Consumidor é idempotente** (at-least-once): reprocessar o mesmo evento produz a mesma
   linha — o que exige id determinístico e `row_version` do evento.
4. **Fire-and-forget loga a falha** com o que se perdeu. Degradação muda é o modo de falha
   padrão aqui.
5. `insight.historico.*` persiste **via Kafka**, nunca escrita direta em PostgreSQL.

## 4. Id derivado (`uuid5`)

- A chave descreve o **FENÔMENO**, não o contêiner: `uuid5(tenant, session_id)` identifica a
  sessão, não a passagem pela fila, e a segunda passagem apagou a primeira (§3).
- **Escolha o discriminador pelo CICLO DE VIDA** (um valor que nasce e morre com o fenômeno,
  como `first_queued_ms`: NX na entrada, DELETE na saída), nunca por um fato do call site
  (`pool_id or ""` dá dois ids para uma passagem).
- Teste obrigatório: **dois fenômenos na mesma sessão geram dois ids**, e a mesma emissão
  repetida gera o mesmo id.

## 5. Valor ausente

- **`None` = não medido.** Nunca `?? 0`, `or 0`, default `"stable"`: ausência convertida em
  medição desarma a guarda do consumidor sem rastro.
- Guarda sobre valor decodificado: `if not x`, nunca `is None` — os decoders devolvem `""`.
- Produtor novo: teste que **não** registra o não-fato, com testemunha de presença ao lado.

## 6. Consultar ao vivo

```bash
docker exec plughub-demo-clickhouse-1 clickhouse-client -d plughub_demo -q "SELECT … FROM segments FINAL WHERE tenant_id = '…' LIMIT 20"
```

- O banco é **`plughub_demo`**; o prefixo `analytics.` dos docs **não existe** ao vivo (§5).
- **Nunca `2>/dev/null`** numa consulta de diagnóstico: `UNKNOWN_DATABASE` vira saída vazia
  que parece "não há linha".
- `session_timeline` está vazia neste deploy; o stream durável é `session_stream_events` no
  **Postgres** (`event_type`/`payload`).
- Para contar um fenômeno, conte também a população que **não** deveria ter linha.
