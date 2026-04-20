# Módulo: Conversation Writer

> Spec de referência: v24.0 seção 14
> Responsabilidade: agregar os eventos de mensagem de um contato,
> construir o transcript completo e persistir ao receber `contact_closed`.
> Pré-requisito do Evaluation Agent — sem transcript não há avaliação.

---

## Visão geral

O Conversation Writer é um consumer Kafka de responsabilidade única.
Não tem lógica de negócio. Não interpreta conteúdo. Não toma decisões.
Lê eventos de mensagem, acumula por contato em Redis e persiste em
PostgreSQL quando o contato é encerrado.

**Por que Kafka é o ponto correto:** os tópicos `conversations.inbound` e
`conversations.outbound` já contêm todos os eventos de mensagem normalizados
de todos os canais. O Channel Gateway faz a normalização antes de publicar —
o Conversation Writer recebe sempre o mesmo envelope independente do canal.

---

## Tópicos Kafka consumidos

| Tópico | Conteúdo |
|---|---|
| `conversations.inbound` | Mensagens recebidas do cliente (qualquer canal) |
| `conversations.outbound` | Mensagens enviadas ao cliente (agente humano, agente IA, sistema) |
| `conversations.events` | Eventos de ciclo de vida — consome apenas `contact_closed` |

---

## Fluxo de processamento

```
conversations.inbound  ──┐
                          ├──▶ acumula em Redis (key: transcript:{contact_id})
conversations.outbound ──┘         TTL: duração máxima configurável do contato

conversations.events (contact_closed)
  ↓
  1. lê todas as mensagens acumuladas em Redis para o contact_id
  2. ordena por timestamp
  3. persiste transcript em PostgreSQL
  4. deleta chave Redis
  5. publica transcript.created em evaluation.events (para o Evaluation Trigger)
```

O Redis serve apenas como buffer temporário durante o atendimento.
A fonte de verdade é o PostgreSQL após o `contact_closed`.

---

## Envelope de mensagem (entrada)

Formato normalizado publicado pelo Channel Gateway em
`conversations.inbound` e `conversations.outbound`.

```json
{
  "message_id": "uuid",
  "contact_id": "uuid",
  "session_id": "uuid",
  "timestamp": "2026-04-06T14:00:00Z",
  "direction": "inbound | outbound",
  "author": {
    "type": "customer | agent_human | agent_ai | system",
    "id": "uuid | null",
    "display_name": "string | null"
  },
  "content": {
    "type": "text | menu_result | system_event",
    "text": "string | null",
    "payload": "object | null"
  },
  "context_snapshot": {
    "intent": "string | null",
    "sentiment_score": "float | null",
    "turn_number": "integer"
  }
}
```

### `context_snapshot`

Snapshot dos parâmetros de sessão no momento da mensagem, gravados
pelo AI Gateway no Redis. Incluído pelo Channel Gateway ao publicar.
Permite que o transcript carregue a evolução de intent e sentimento
turno a turno — usado pelo Evaluation Agent e pelo dashboard de
drill-down de conversa.

---

## Acumulação em Redis

```
key:   transcript:{contact_id}
type:  list (RPUSH a cada mensagem)
TTL:   contato_max_duration_seconds (configurável, default: 4h)
```

O TTL é uma proteção contra contatos que nunca recebem `contact_closed`
(falha de infraestrutura, agente desconectado). Após o TTL, a chave
expira e o transcript parcial é perdido — aceitável como edge case
no piloto, tratável via reprocessamento manual com o trigger CLI.

---

## Persistência em PostgreSQL

### Tabela `transcripts`

```sql
CREATE TABLE transcripts (
  transcript_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  contact_id      UUID NOT NULL UNIQUE,
  pool_id         VARCHAR NOT NULL,
  agent_id        UUID NOT NULL,
  agent_type      VARCHAR NOT NULL,   -- 'human' | 'ai'
  started_at      TIMESTAMPTZ NOT NULL,
  ended_at        TIMESTAMPTZ NOT NULL,
  outcome         VARCHAR NOT NULL,
  turn_count      INTEGER NOT NULL,
  created_at      TIMESTAMPTZ DEFAULT now()
);
```

### Tabela `transcript_messages`

```sql
CREATE TABLE transcript_messages (
  id              BIGSERIAL PRIMARY KEY,
  transcript_id   UUID NOT NULL REFERENCES transcripts(transcript_id),
  message_id      UUID NOT NULL,
  turn_number     INTEGER NOT NULL,
  timestamp       TIMESTAMPTZ NOT NULL,
  direction       VARCHAR NOT NULL,   -- 'inbound' | 'outbound'
  author_type     VARCHAR NOT NULL,   -- 'customer' | 'agent_human' | 'agent_ai' | 'system'
  author_id       UUID,
  display_name    VARCHAR,
  content_type    VARCHAR NOT NULL,   -- 'text' | 'menu_result' | 'system_event'
  content_text    TEXT,
  content_payload JSONB,
  intent          VARCHAR,            -- context_snapshot.intent
  sentiment_score FLOAT,              -- context_snapshot.sentiment_score
  INDEX (transcript_id, turn_number)
);
```

---

## Evento publicado: `transcript.created`

Publicado em `evaluation.events` após persistência bem-sucedida.
O Evaluation Trigger consome este evento para montar o `evaluation.requested`
— não precisa ler o `contact_closed` diretamente.

```json
{
  "event_type": "transcript.created",
  "transcript_id": "uuid",
  "contact_id": "uuid",
  "agent_id": "uuid",
  "agent_type": "human | ai",
  "pool_id": "retencao_humano",
  "outcome": "resolved | escalated | abandoned",
  "turn_count": 12,
  "started_at": "2026-04-06T13:45:00Z",
  "ended_at": "2026-04-06T14:00:00Z",
  "created_at": "2026-04-06T14:00:03Z"
}
```

O Evaluation Trigger lê este evento, consulta o context_package no Redis
(ainda disponível por alguns segundos após o `contact_closed`), resolve
o `skill_id` pelo `pool_id` no Agent Registry e publica `evaluation.requested`.

---

## Fluxo completo: do contact_closed à avaliação

```
contact_closed (conversations.events)
  │
  ├──▶ Conversation Writer
  │      lê Redis → ordena → persiste PostgreSQL
  │      publica transcript.created (evaluation.events)
  │
  └──▶ Evaluation Trigger (consumer de evaluation.events)
         lê transcript.created
         lê context_package do Redis (contact_id)
         resolve skill_id pelo pool_id (Agent Registry)
         publica evaluation.requested (evaluation.events)
              │
              └──▶ Evaluation Agent
                     processa evaluation.requested
                     publica evaluation.completed (evaluation.results)
                          │
                          └──▶ ClickHouse consumer
                                 persiste evaluation_scores
                                 persiste evaluation_items
```

---

## Configuração

```yaml
conversation_writer:
  redis:
    transcript_ttl_seconds: 14400    # 4h — TTL do buffer de mensagens
  postgres:
    schema: plughub
  kafka:
    consumer_group: conversation-writer
    topics:
      - conversations.inbound
      - conversations.outbound
      - conversations.events
    publish_topic: evaluation.events
```

---

## O que o Conversation Writer não faz

- Não interpreta conteúdo das mensagens
- Não calcula métricas (sentimento, intent) — lê do context_snapshot
- Não decide se um contato deve ser avaliado
- Não escreve no ClickHouse
- Não notifica agentes ou supervisores
- Não tem API REST — é exclusivamente um consumer/producer Kafka

---

## Relações com outros módulos

| Módulo | Relação |
|---|---|
| `channel-gateway` | Produz os eventos de mensagem normalizados consumidos |
| `conversations.events` (Kafka) | Fonte do `contact_closed` que dispara a persistência |
| `ai-gateway` | Produtor do `context_snapshot` incluído nas mensagens |
| `evaluation.events` (Kafka) | Destino do `transcript.created` |
| `Evaluation Trigger` | Consumer do `transcript.created` |
| `Evaluation Agent` | Lê o transcript via `transcript_id` |
| `Dashboard` | Lê `transcript_messages` para drill-down de conversa individual |
