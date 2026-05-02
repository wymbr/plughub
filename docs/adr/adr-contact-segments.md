# ADR: ContactSegment — Modelo de Segmentos de Atendimento e Taxonomia Unificada de Eventos

**Status:** Proposto (design aprovado, implementação pendente Arc 5)  
**Data:** 2026-04-27  
**Componentes:** `packages/schemas`, `packages/analytics-api`, `packages/skill-flow-engine`, `packages/orchestrator-bridge`

---

## Contexto

### O problema dos múltiplos segmentos

O modelo original do PlugHub trata o `session_id` como unidade atômica de atendimento:
um contato, um agente, um ciclo de avaliação. Na prática, isso é uma simplificação incorreta.

Contatos reais frequentemente têm a estrutura:

```
sessão (session_id)
  ├── segmento 1: agente IA triagem        (resolvido)
  ├── segmento 2: agente humano retenção   (escalado)
  │     └── segmento 2a: especialista faturamento (conferência)
  ├── segmento 3: agente humano supervisor (continuação)
  └── segmento 4: agente finalização IA   (NPS + encerramento)
```

Tratar a saída do último humano como fim de contato (`agent_done` ≡ `contact_close`) é
conceitualmente errado. Implica:

- Toda avaliação de qualidade é coletada contra o contato inteiro, sem granularidade por
  agente ou fase
- SLA é calculado contra o contato inteiro, ocultando degradações em segmentos específicos
- Não é possível distinguir a experiência do cliente com o agente A da experiência com o B
- O relatório "tempo médio de atendimento por agente" é distorcido por handoffs

### O problema da N-plicação de eventos

O mesmo fato semântico — "agente X começou a atender o cliente Y na sessão Z" — é
representado hoje em cinco lugares distintos sob cinco nomes diferentes:

| Onde | Nome | Gatilho |
|------|------|---------|
| Kafka `conversations.routed` | `conversations.routed` | Routing Engine aloca |
| Redis pub/sub `pool:events:{poolId}` | `conversation.assigned` | Agent Assist UI |
| Redis Stream `session:{id}:stream` | `participant_joined` | Core |
| Redis hash `{tenant}:agent:instance:{id}` | status=`busy` | Bridge |
| PostgreSQL `sentiment_timeline` | (início implícito) | analytics-api |

Quando o agente termina:

| Onde | Nome | Gatilho |
|------|------|---------|
| Kafka `agent.lifecycle` | `agent.done` | Routing Engine |
| Kafka `conversations.events` | `contact_closed` | Bridge |
| Redis Stream | `participant_left` | Core |
| Redis hash | status=`ready` | Bridge |
| ClickHouse `sessions` | `closed_at` | analytics consumer |

Cinco representações de entrada, cinco de saída. Manutenção duplicada, possibilidade de
divergência, ausência de `segment_id` em todas elas.

### O problema da avaliação única

Há dois ângulos distintos e legítimos de avaliação de qualidade:

**Perspectiva do agente (atendente):** O agente cumpriu seu SLA? Seguiu o script? Usou
as ferramentas corretas? Escalonou no momento certo? Resolveu sem escaladas desnecessárias?
→ Destinatário: supervisão, QA, treinamento.

**Perspectiva do cliente (atendido):** O problema foi resolvido? O tempo de espera foi
aceitável? Precisou repetir informações? Qual o esforço percebido?
→ Destinatário: NPS/CSAT/CES, produto, customer success.

O modelo atual mistura ou ignora esses ângulos. O `agente_avaliacao_v1` avalia o contato
inteiro do ângulo do agente. O NPS é coletado (opcionalmente) no final via `agente_finalizacao_v1`,
mas não está vinculado a segmentos específicos.

---

## Decisão

### 1. ContactSegment como entidade de primeira classe

Um `ContactSegment` representa uma unidade de atendimento dentro de um contato. Todo agente
que atende um cliente cria (ou é associado a) um segmento.

```typescript
ContactSegment {
  segment_id:        string    // UUID gerado pelo Core/Bridge na alocação
  session_id:        string    // FK para a sessão pai
  tenant_id:         string

  // Topologia
  parent_segment_id: string | null  // null = segmento primário; preenchido = conferência/paralelo
  sequence_index:    number         // ordem entre segmentos primários (0, 1, 2…)

  // Quem atendeu
  pool_id:           string
  agent_type_id:     string
  instance_id:       string
  participant_id:    string    // ID no stream da sessão

  // Tempo
  started_at:        string    // ISO-8601
  ended_at:          string | null
  duration_ms:       number | null

  // Resultado
  outcome:           "resolved" | "escalated" | "transferred" | "abandoned" | "timeout" | null
  close_reason:      string | null
  handoff_reason:    string | null
  issue_status:      string | null
}
```

**Topologia sequencial:** Quando o agente A faz handoff para o agente B, o segmento de B
recebe `sequence_index = segmento_A.sequence_index + 1` e `parent_segment_id = null`.

**Topologia paralela (conferência):** Quando o agente A convida o especialista B,
o segmento de B recebe `parent_segment_id = segmento_A.segment_id` e `sequence_index = 0`.

```
sessão
  segmento[0]   (primário, sequence_index=0, parent=null)
    segmento[0.0]  (conferência, sequence_index=0, parent=seg[0])
    segmento[0.1]  (conferência, sequence_index=1, parent=seg[0])
  segmento[1]   (primário após handoff, sequence_index=1, parent=null)
  segmento[2]   (hook on_human_end, sequence_index=2, parent=null)
```

### 2. Eventos canônicos: `participant.joined` / `participant.left`

O Redis Stream `session:{id}:stream` já tem `participant_joined` e `participant_left`.
Esses eventos são a fonte de verdade para a existência de segmentos.

**Novo campo obrigatório em ambos:** `segment_id`.

O `segment_id` é gerado pelo Bridge no momento do `process_routed` (para agentes IA) e do
`activate_human_agent` (para agentes humanos) e escrito no evento `participant_joined`.
O mesmo `segment_id` é incluído no `participant_left` correspondente.

### 3. Tópico Kafka: `conversations.participants`

Introduzido para espelhar os eventos de participação do Redis Stream para o ecossistema
de analytics sem modificar o hot path.

**Schema:**

```typescript
ConversationParticipantEvent {
  event_type:      "participant.joined" | "participant.left"
  event_id:        string       // XADD entry ID do stream Redis
  session_id:      string
  tenant_id:       string
  segment_id:      string       // novo campo obrigatório
  participant_id:  string
  participant_role: "primary" | "specialist" | "supervisor" | "evaluator" | "reviewer"
  agent_type_id:   string | null
  instance_id:     string | null
  pool_id:         string | null
  channel:         string | null
  timestamp:       string       // ISO-8601

  // Apenas em participant.left
  outcome?:        string
  duration_ms?:    number
  handoff_reason?: string
  issue_status?:   string
  close_reason?:   string
}
```

**Producer:** Bridge (orquestrator-bridge) — fire-and-forget, fora do hot path.  
**Consumer:** analytics-api → ClickHouse `segments` table.

Esse tópico **não substitui** os tópicos existentes (`conversations.routed`, `agent.lifecycle`).
Ele é o ponto de entrada para o modelo de segmentos. A consolidação dos tópicos legados
é trabalho futuro (ver Consequências).

### 4. Arquitetura hot/cold — separação de responsabilidades

O modelo dual garante performance onde é crítico e observabilidade onde é necessária:

```
Hot path (sub-5ms)          Cold path (segundos a minutos)
─────────────────────       ──────────────────────────────
Redis Stream                Kafka topics
  participant_joined  ───►    conversations.participants
  participant_left    ───►    conversations.participants
  message             ───►    conversations.message_sent
  flow_step_completed ───►    (futuro: conversations.flow_steps)

Redis hash (estado vivo)    ClickHouse (série temporal)
  instance status     ───►    segments, session_timeline
  pool snapshot       ───►    queue_events
  sentiment_live      ───►    sentiment_events
```

**Regra:** O Routing Engine, o Skill Flow Engine e o Agent Assist UI lêem **exclusivamente**
do Redis. Nunca bloqueiam em Kafka ou ClickHouse.

**Bridge como stream bridge:** O orchestrator-bridge é o único componente que lê o Redis
Stream e publica no Kafka (fire-and-forget, sem bloqueio do stream consumer principal).

### 5. `session_timeline` — série temporal vinculada a segmentos

```sql
CREATE TABLE analytics.session_timeline
(
    event_id       String,
    tenant_id      String,
    session_id     String,
    segment_id     String,          -- nullable até enrichment
    event_type     String,
    actor_id       String,          -- participant_id, instance_id, "platform"
    actor_role     String,
    payload        String,          -- JSON compactado
    timestamp      DateTime64(3),
    ingested_at    DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY (tenant_id, session_id, timestamp, event_id)
PARTITION BY toYYYYMM(timestamp);
```

**Eventos que alimentam session_timeline:**

| Fonte Kafka | event_type |
|-------------|-----------|
| `conversations.participants` | `participant.joined`, `participant.left` |
| `conversations.message_sent` | `message.sent` |
| `sentiment.updated` | `sentiment.updated` |
| `mcp.audit` | `mcp.tool_call` |
| `workflow.events` | `workflow.suspended`, `workflow.resumed` |
| `collect.events` | `collect.requested`, `collect.responded` |

**Views materializadas úteis:**

```sql
-- Resumo por segmento
CREATE MATERIALIZED VIEW analytics.segment_summary ...
SELECT
    segment_id,
    session_id,
    tenant_id,
    min(timestamp) AS started_at,
    max(timestamp) AS ended_at,
    dateDiff('millisecond', min(timestamp), max(timestamp)) AS duration_ms,
    countIf(event_type = 'message.sent') AS message_count,
    avg(JSONExtractFloat(payload, 'score')) AS avg_sentiment,
    min(JSONExtractFloat(payload, 'score')) AS min_sentiment,
    argMin(timestamp, JSONExtractFloat(payload, 'score')) AS worst_sentiment_at
FROM analytics.session_timeline
WHERE event_type IN ('message.sent', 'sentiment.updated')
GROUP BY segment_id, session_id, tenant_id;

-- Performance de agentes
CREATE MATERIALIZED VIEW analytics.agent_performance ...
SELECT
    tenant_id,
    actor_id AS instance_id,
    toStartOfHour(timestamp) AS hour,
    countIf(event_type = 'participant.joined') AS sessions_started,
    countIf(event_type = 'participant.left') AS sessions_ended,
    avgIf(
        JSONExtractUInt(payload, 'duration_ms'),
        event_type = 'participant.left'
    ) AS avg_handle_time_ms,
    countIf(
        event_type = 'participant.left'
        AND JSONExtractString(payload, 'outcome') = 'resolved'
    ) AS resolved_count
FROM analytics.session_timeline
GROUP BY tenant_id, instance_id, hour;
```

### 6. Enrichment de segment_id — estratégia post-hoc

Alguns produtores de eventos (ex.: `sentiment_emitter`, `mcp.audit`) não conhecem o
`segment_id` no momento de publicação — eles conhecem apenas `session_id` e `timestamp`.

**Estratégia:** Ao ingerir no ClickHouse, o analytics consumer faz o enrichment por
sobreposição de timestamp:

```python
async def enrich_segment_id(session_id: str, timestamp: datetime) -> str | None:
    """
    Retorna o segment_id do segmento ativo no instante `timestamp`.
    Usa a tabela segments já ingerida para lookup O(log n).
    """
    result = await ch.query(
        """
        SELECT segment_id FROM analytics.segments
        WHERE session_id = %(session_id)s
          AND started_at <= %(ts)s
          AND (ended_at IS NULL OR ended_at >= %(ts)s)
        ORDER BY started_at DESC
        LIMIT 1
        """,
        {"session_id": session_id, "ts": timestamp}
    )
    return result[0]["segment_id"] if result else None
```

Vantagem: **nenhum produtor precisa ser modificado** para vincular seus eventos a segmentos.
O enrichment acontece na camada de analytics, de forma assíncrona.

Limitação: eventos anteriores à existência da tabela `segments` não podem ser enriquecidos
retroativamente. Migração de dados históricos requer estratégia separada.

### 7. Modelo dual de avaliação

Dois artefatos de avaliação distintos por segmento, com escopos, gatilhos e destinatários
separados:

**EvaluationResult (perspectiva do agente — QA)**

```typescript
{
  evaluation_id: string
  segment_id:    string          // granularidade = segmento
  session_id:    string
  agent_type_id: string
  instance_id:   string

  dimensions: {
    script_compliance:  number   // 0-1
    tool_usage:         number   // 0-1
    escalation_timing:  number   // 0-1
    response_quality:   number   // 0-1
    resolution_rate:    number   // 0-1
  }
  weighted_score:       number
  evaluator_agent_id:   string
  evaluated_at:         string
  notes:                string[]
}
```

Gatilho: `agente_avaliacao_v1` ativado via hook `on_human_end` ou por scheduler pós-sessão.
Destinatário: supervisão, QA, relatórios de treinamento.

**CustomerFeedback (perspectiva do cliente — CX)**

```typescript
{
  feedback_id:  string
  segment_id:   string | null    // null = feedback sobre o contato inteiro
  session_id:   string
  customer_id:  string

  nps_score:    number | null    // -1 a 10 (NPS) ou null se não coletado
  csat_score:   number | null    // 1 a 5 (CSAT)
  ces_score:    number | null    // 1 a 7 (Customer Effort Score)
  verbatim:     string | null    // resposta livre do cliente
  collected_at: string
  channel:      string
}
```

Gatilho: `agente_finalizacao_v1` ativado via hook `on_human_end` coleta NPS/CSAT.
Destinatário: NPS tracker, produto, customer success.

Os dois modelos são independentes. Um segmento pode ter avaliação do agente sem feedback
do cliente (ex.: agente IA) e vice-versa.

---

## Consequências

### Positivas

- **Granularidade completa**: SLA, quality score e sentiment calculados por segmento,
  não por contato
- **Rastreabilidade**: qualquer evento (sentimento, tool call, step de flow) pode ser
  associado ao agente que estava atendendo naquele momento
- **Separação clara de avaliação**: QA de agente ≠ satisfação do cliente
- **Nenhuma mudança no hot path**: Redis Stream, Routing Engine e Skill Flow Engine
  não são modificados
- **Retrocompatibilidade**: tópicos Kafka existentes continuam funcionando; `conversations.participants`
  é adição, não substituição

### Negativas / Riscos

- **Dois sistemas de evento em paralelo**: tópicos legados e `conversations.participants`
  coexistem durante a transição, aumentando complexidade de manutenção temporariamente
- **Latência de enrichment**: segment_id no session_timeline chega com delay (~segundos)
  após o evento real, porque depende que `segments` já tenha sido populado pelo consumer
  de `conversations.participants`
- **Window de inconsistência**: eventos de sentimento que chegam antes do `participant_joined`
  correspondente ficam temporariamente sem segment_id. Resolução: retry de enrichment
  com backoff de 5s
- **Migração de dados históricos**: sessões anteriores à implementação não têm `segment_id`.
  Decisão: aceitar o gap; não migrar retroativamente sem análise de custo-benefício

### Trabalho futuro

- Consolidar tópicos legados (`agent.lifecycle`, `conversations.routed`) no modelo de
  `conversations.participants` quando todos os consumidores estiverem migrados
- Implementar `segment_id` como campo de primeira classe no `pipeline_state` do Skill Flow
- Adicionar drill-down de segmento no Operator Console (timeline por segmento dentro de
  uma sessão)
- CustomerFeedback API: endpoint para receber scores de canais externos (pesquisa por email,
  IVR pós-chamada, etc.)

---

## Alternativas consideradas

### Alternativa A: Manter session como unidade atômica

Simples, sem mudança de schema. Rejeitada porque não suporta SLA por segmento, avaliação
por agente ou análise de handoff. O custo de não fazer é crescente: cada novo relatório
"por agente" requer hacks em cima de dados agregados errados.

### Alternativa B: Criar tabela `contact_segments` no PostgreSQL (Core)

Persistir segmentos no PostgreSQL transacional do Core durante o atendimento.
Rejeitada porque adicionaria latência síncrona no hot path (toda alocação esperaria
um INSERT), violando o invariante de que o Routing Engine deve responder em sub-5ms.

### Alternativa C: Enriquecer todos os produtores com `segment_id`

Modificar `sentiment_emitter`, `mcp.audit`, etc. para conhecerem o `segment_id` atual.
Rejeitada porque requer coordenação em múltiplos pacotes (AI Gateway, MCP Server,
Channel Gateway) e acoplamento entre componentes que hoje são independentes. O enrichment
post-hoc na camada de analytics resolve o mesmo problema sem acoplamento.

---

## Referências

- `CLAUDE.md § Pool Lifecycle Hooks` — Fase A e Fase B
- `CLAUDE.md § Unified Session Model` — modelo de participantes e roles
- `docs/guias/pool-hooks.md` — guia de implementação de hooks (Fase A + B)
- `docs/kafka-eventos.md` — tópicos Kafka existentes
- `packages/schemas/src/workflow.ts` — `ContactSegment` (a implementar)
- `packages/analytics-api/src/plughub_analytics_api/clickhouse.py` — schemas ClickHouse existentes
