# Módulo: Conversation Writer / Stream Persister

> Última atualização: 2026-05-25 · Estado: Arc 16

> **Responsabilidade:** persistir o stream canônico de uma sessão em PostgreSQL ao
> receber `session_closed`, garantindo um snapshot estável para o pipeline de avaliação.
> Pré-requisito do Session Replayer e do Evaluation Agent.

---

## Visão geral

O modelo de transcript de PlugHub não é mais um agregador de mensagens. A fonte
única de verdade de tudo que acontece numa sessão é o **stream canônico**
`session:{id}:stream` (Redis) — todos os eventos de sessão (mensagens, routing,
eventos de ciclo de vida) são gravados ali via `writeStreamEntry()`.

O componente responsável por persistir esse stream é o **Stream Persister**,
implementado dentro da `analytics-api`. Não é um serviço Python dedicado nem um
agregador de tópicos `conversations.inbound`/`conversations.outbound`. Ele é um
consumer Kafka de responsabilidade única: ao receber `session_closed`, lê o
stream canônico do Redis e o persiste em PostgreSQL.

> **Por que o stream e não os tópicos inbound/outbound:** o `session:{id}:stream`
> já contém todos os eventos normalizados, com `event_id`, `segment_id`,
> `author_id`/`author_role` flat e validação Zod garantida por `writeStreamEntry()`.
> Não há necessidade de re-agregar mensagens de tópicos separados — o stream é
> a representação canônica completa.

---

## Pipeline de persistência e avaliação

```
session_closed (conversations.session_closed)
    │
    ▼
Stream Persister (analytics-api)
    │  lê session:{id}:stream (Redis)
    │  escreve em analytics.session_events (PostgreSQL), event_order index
    │  marca sessions.stream_persisted = true
    │
    ▼
evaluation.requested  ←──── Kafka (sampling engine da evaluation-api)
    │
    ▼
Hydrator
    │  Redis hit  → no-op (stream ainda em cache)
    │  Redis miss → lê PostgreSQL → reescreve no Redis (TTL 1h)
    │
    ▼
Replayer  →  ReplayContext em {tenant}:replay:{session_id}:context (TTL 1h)
    │
    ▼
Evaluation Agent (agente_avaliacao_v1)
```

O Redis stream pode expirar após o passo de persistência — o PostgreSQL passa a
ser a fonte autoritativa. O Hydrator implementa o padrão "ensure-before-read":
o Replayer sempre lê do Redis e o Hydrator garante que os dados estejam lá.

→ Ver [`docs/arcos/session-replayer.md`](../arcos/session-replayer.md).

---

## Tópicos Kafka consumidos

| Tópico | Conteúdo |
|---|---|
| `conversations.session_opened` | Abertura de sessão — registra metadados |
| `conversations.session_closed` | Encerramento — dispara a persistência do stream |

---

## Persistência em PostgreSQL

### Tabela `analytics.session_events`

Uma linha por evento do stream canônico, na ordem original.

```sql
CREATE TABLE analytics.session_events (
  id            BIGSERIAL PRIMARY KEY,
  session_id    TEXT NOT NULL,
  tenant_id     TEXT NOT NULL,
  event_order   INTEGER NOT NULL,    -- posição no stream
  event_id      TEXT NOT NULL,
  event_type    TEXT NOT NULL,       -- 'message' | 'routing.assigned' | ...
  segment_id    TEXT,                -- segmento (Arc 5 ContactSegment)
  author_id     TEXT,
  author_role   TEXT,                -- 'customer' | 'primary' | 'specialist' | ...
  visibility    TEXT,                -- 'all' | 'agents_only' | array de participant_ids
  content       JSONB,
  delta_ms      INTEGER,             -- ms desde o evento anterior
  timestamp     TIMESTAMPTZ NOT NULL
);
CREATE INDEX ON analytics.session_events (session_id, event_order);
```

A coluna `delta_ms` preserva os intervalos originais entre eventos — usada pelo
Replayer para reproduzir o timing da sessão com `speed_factor`.

---

## Modelo de segmentos (Arc 5 — ContactSegment)

Uma sessão é uma conferência: vários participantes (primário, especialistas,
supervisores) operam em janelas próprias. Cada participante tem um `ContactSegment`
com `segment_id`, `parent_segment_id`, `sequence_index`, `outcome`, `close_reason`.

Os eventos do stream carregam `segment_id` flat — a persistência preserva esse
campo para que o Replayer e os relatórios de segmento (`analytics.segments`)
consigam reconstruir a topologia da conferência. O transcript de uma sessão
não é mais "um transcript por contato" — é o conjunto de eventos do stream,
particionável por segmento.

→ Ver [`docs/arcos/arc5-segments.md`](../arcos/arc5-segments.md).

---

## O que o Stream Persister não faz

- Não interpreta o conteúdo das mensagens
- Não calcula métricas (sentimento, intent)
- Não decide se uma sessão deve ser avaliada — isso é da `evaluation-api` (sampling engine)
- Não agrega mensagens de tópicos `conversations.inbound`/`outbound` — lê o stream canônico
- Não expõe API REST — a `analytics-api` que o hospeda expõe os endpoints `/reports/*`

---

## Relações com outros módulos

| Módulo | Relação |
|---|---|
| `Core` | Escreve no stream canônico via `writeStreamEntry()`; publica `session_closed` |
| `conversations.session_closed` (Kafka) | Fonte do gatilho de persistência |
| `Hydrator` | Reidrata o stream do PostgreSQL para o Redis quando o cache expira |
| `Session Replayer` | Reconstrói a sessão a partir do stream para o pipeline de avaliação |
| `evaluation-api` | Sampling engine cria instâncias e dispara `evaluation.requested` |
| `Evaluation Agent` | Consome o `ReplayContext` produzido pelo Replayer |
