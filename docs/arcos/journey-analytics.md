# Journey Analytics — Modelo de Dados e Drill-down

> ⚠️ OBSOLETO — esta proposta de modelagem analítica (4 níveis journey→contato→sessão→turno) NÃO foi adotada. O Arc 10 implementado usa journey→session→segment com o tópico journey.events e a tabela ClickHouse journey_events. Ver arc10-journey.md. Documento mantido apenas como referência histórica.

> Status: **Proposta de modelagem analítica — validar com time de Analytics e com Arc 10 (Journey)**
> Último update: Maio 2026
> Escopo: tabelas ClickHouse, endpoints de relatório, KPIs e drill-down nos quatro níveis hierárquicos

---

## Objetivo

Especificar o modelo analítico para Journey de forma que **todas as equipes (Dashboard #35, Relatórios, Avaliação, módulo Processos) usem o mesmo schema e a mesma agregação**. O risco a evitar: "TTR por jornada" responder valores diferentes em dashboards diferentes porque cada um implementou sua própria agregação.

Documento complementar a [`arcos/arc10-journey.md`](arc10-journey.md), que define o modelo operacional da Journey (fases, eventos Kafka, lifecycle). Aqui o foco é exclusivamente a camada analítica.

---

## Hierarquia conceitual

```
Journey                  (journey_id)            — processo de atendimento completo
  └─ Contato             (contact_id)            — interação atômica (chamada, chat, e-mail enviado)
       └─ Sessão         (session_id)            — janela de presença de participantes
            └─ Turno     (turn_id)               — mensagem ou ação individual
```

Os quatro níveis são **dimensões de drill-down e roll-up** consistentes em todos os relatórios:

- **Jornada** agrupa todos os contatos de um processo (cobrança, onboarding, retenção)
- **Contato** é uma interação que começa e termina em algum canal — pode ter múltiplas sessões em sequência (caso típico: cliente liga, fica em fila, sessão 1 com bot, transferência para humano, sessão 2)
- **Sessão** é a janela onde um conjunto de participantes (cliente, humano, IA, especialistas) coexiste em conferência
- **Turno** é uma mensagem ou ação individual de um participante

---

## Princípios de modelagem

### 1. Late-binding suportado

Um novo contato pode ser vinculado a uma journey existente **a qualquer momento** — inclusive retroativamente. Quando isso ocorre, métricas de jornada são recalculadas, e o evento `journey.contact_attached` é emitido em Kafka para os consumers atualizarem agregações.

### 2. Replay determinístico

Toda métrica de jornada pode ser recalculada a partir dos eventos brutos em `journey.events` topic. Se uma reclassificação ocorre (ex.: contato originalmente vinculado a jornada A foi corrigido para jornada B), o replay garante consistência sem perda de histórico.

### 3. Granularidade preservada até o turno

Métricas agregadas no nível de jornada **sempre são drillable** até o turno individual. Não há agregações lossy — toda agregação é uma soma sobre eventos atômicos preservados em ClickHouse.

### 4. Multi-tenant por design

Toda tabela tem `tenant_id` como primeira coluna do `ORDER BY` (sharding key). Queries cross-tenant são impossíveis por design — não há agregação que cruze tenants.

---

## Tabelas ClickHouse

### `journey_events`

Tabela principal de eventos atômicos da jornada. Fonte da verdade.

```sql
CREATE TABLE journey_events (
    tenant_id          LowCardinality(String),
    journey_id         UUID,
    contact_id         Nullable(UUID),
    session_id         Nullable(UUID),
    turn_id            Nullable(UUID),
    event_type         LowCardinality(String),  -- journey.started, contact.attached, contact.resolved, session.opened, session.closed, turn.created
    event_ts           DateTime64(3),
    journey_type       LowCardinality(String),  -- cobrança, onboarding, retenção, suporte, etc.
    channel            LowCardinality(String),  -- whatsapp, webchat, voz_sip, voz_webrtc, sms, email, instagram, telegram
    pool_id            Nullable(String),
    participant_role   LowCardinality(String),  -- cliente, humano, ia_orquestrador, especialista, supervisor
    participant_id     Nullable(String),
    outcome            Nullable(String),        -- resolved, escalated_human, transferred_agent, callback
    metadata           JSON                     -- campos específicos do evento_type
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(event_ts)
ORDER BY (tenant_id, journey_id, event_ts, journey_type)
TTL event_ts + INTERVAL 7 YEAR;
```

### `journey_summary` (Materialized View)

Agregado denormalizado por jornada — usado em dashboards de alto nível.

```sql
CREATE MATERIALIZED VIEW journey_summary
ENGINE = AggregatingMergeTree()
PARTITION BY toYYYYMM(journey_started_at)
ORDER BY (tenant_id, journey_id)
AS SELECT
    tenant_id,
    journey_id,
    anyState(journey_type)              AS journey_type,
    minState(event_ts)                  AS journey_started_at,
    maxIfState(event_ts, event_type = 'journey.closed') AS journey_closed_at,
    countIfState(event_type = 'contact.attached')        AS contact_count,
    countIfState(event_type = 'session.opened')          AS session_count,
    countIfState(event_type = 'turn.created')            AS turn_count,
    uniqState(channel)                  AS channels_used,
    uniqState(participant_id)           AS participants_unique,
    anyLastState(outcome)               AS final_outcome
FROM journey_events
GROUP BY tenant_id, journey_id;
```

### `contact_summary` (Materialized View)

Agregado por contato — segundo nível de drill-down.

```sql
CREATE MATERIALIZED VIEW contact_summary
ENGINE = AggregatingMergeTree()
PARTITION BY toYYYYMM(contact_started_at)
ORDER BY (tenant_id, journey_id, contact_id)
AS SELECT
    tenant_id,
    journey_id,
    contact_id,
    anyState(channel)                   AS channel,
    minState(event_ts)                  AS contact_started_at,
    maxIfState(event_ts, event_type = 'contact.resolved') AS contact_resolved_at,
    countIfState(event_type = 'session.opened')   AS session_count,
    countIfState(event_type = 'turn.created')     AS turn_count,
    anyLastState(outcome)               AS outcome
FROM journey_events
WHERE contact_id IS NOT NULL
GROUP BY tenant_id, journey_id, contact_id;
```

### `session_summary` (Materialized View)

Agregado por sessão. Já existe parcialmente em outros pacotes (Arc 5 — ContactSegment); alinhamento necessário.

Convergência sugerida: ContactSegment passa a referenciar `journey_id` como dimensão obrigatória; queries de sessão respondem com filtro por jornada quando aplicável.

---

## KPIs padrão por nível

| KPI | Nível Jornada | Nível Contato | Nível Sessão | Nível Turno |
|---|---|---|---|---|
| **TTR** (Time to Resolve) | journey_closed - journey_started | contact_resolved - contact_started | session_closed - session_opened | N/A |
| **Touches** (contagem) | contact_count na jornada | session_count no contato | turn_count na sessão | N/A |
| **Channel mix** | uniq(channels_used) | channel (1 valor) | channel (1 valor) | channel (1 valor) |
| **Participantes únicos** | uniq(participant_id) | uniq(participant_id) | uniq(participant_id) | participant_id (1 valor) |
| **Custo total** | sum(custo) sobre turnos+sessões+contatos | sum(custo) sobre sessões+turnos | sum(custo) sobre turnos | custo do turno |
| **Sentimento médio** | avg(sentiment) sobre todos turnos do cliente | idem por contato | idem por sessão | sentiment do turno (se mensagem do cliente) |
| **First Contact Resolution (FCR)** | 1 se contact_count = 1 e outcome = resolved | N/A | N/A | N/A |
| **Resolution rate** | % de jornadas com final_outcome = resolved | % de contatos com outcome = resolved | N/A | N/A |
| **AHT** (Average Handling Time) | N/A (não-aplicável a jornada) | tempo ativo de participantes humanos | tempo de sessão | N/A |
| **Multi-channel ratio** | % de jornadas com ≥ 2 channels | N/A | N/A | N/A |

---

## Endpoints de relatório

Convenção: todos retornam `Content-Type: application/json` e suportam os filtros padrão `?from=<ts>&to=<ts>&tenant_id=<id>&journey_type=<type>&pool_id=<id>`.

| Endpoint | Nível | Retorna |
|---|---|---|
| `GET /reports/journeys` | Jornada | Lista paginada de jornadas + KPIs sumarizados |
| `GET /reports/journeys/:journey_id` | Jornada | Detalhe completo da jornada com lista de contatos |
| `GET /reports/journeys/:journey_id/contacts` | Contato | Lista de contatos da jornada com KPIs |
| `GET /reports/journeys/:journey_id/contacts/:contact_id/sessions` | Sessão | Sessões do contato |
| `GET /reports/journeys/:journey_id/contacts/:contact_id/sessions/:session_id/turns` | Turno | Turnos da sessão (replay disponível) |
| `GET /reports/kpis/journeys/aggregated` | Jornada | KPIs agregados por journey_type / pool / channel mix |
| `GET /reports/funnel/:journey_type` | Jornada | Funil de resolução por fase configurada no Arc 10 |

Drill-down: cada endpoint retorna links HATEOAS para os níveis abaixo. `_links.contacts.href`, `_links.sessions.href`, etc.

---

## Late-binding e replay — operação

### Late-binding

Cenário típico: cliente liga (gera contato C1, vinculado a jornada J1 por inferência); três dias depois, o cliente chama de novo (C2). O classificador da plataforma detecta que C2 é continuação de J1 e emite `journey.contact_attached(journey_id=J1, contact_id=C2)`.

Consequências analíticas:
- `journey_events` recebe o evento — automaticamente atualiza `journey_summary` via materialized view
- `contact_summary` para C2 passa a ter `journey_id = J1` (não null)
- KPIs de J1 recalculam: `contact_count++`, `journey_closed_at` recalculado, `final_outcome` revisado

### Replay

Operação `POST /admin/reports/replay` aciona reprocessamento de uma janela. Caso típico: bug em agregação corrigido; precisa recalcular agregações dos últimos 30 dias.

Processo:
1. Drop dos materialized views afetados
2. Recriação a partir de `journey_events` (fonte da verdade)
3. Validação automática: comparar contagens pré/pós-replay

Tempo estimado: ~30min para 30 dias de dados em tenant médio (10k jornadas/dia).

---

## Reclassificação retroativa

Cenário: jornada classificada inicialmente como "suporte técnico" foi corrigida para "retenção" após análise da equipe de qualidade.

Operação: `PATCH /journeys/:journey_id { journey_type: "retenção" }` emite evento `journey.reclassified` que:
- Atualiza `journey_events` com novo `journey_type` em todos os eventos da jornada
- Trigger de rebuild da agregação `journey_summary` para a jornada afetada
- Notifica dashboards via WebSocket de reload de filtros se aplicável

Audit: evento `journey.reclassified` persiste em `audit_events` com operador, motivo, valor anterior e valor novo. Retenção 7 anos.

---

## Posicionamento competitivo

| Plataforma | Modelo equivalente | Gap |
|---|---|---|
| Salesforce Service Cloud | `Case` como entidade CRM | Não é primitive de roteamento; analytics fragmentada entre Service Cloud Reports e Tableau CRM |
| Genesys Pointillist | Journey Analytics standalone | Sem amarração ao roteador; ingestão batch (não real-time); produto separado |
| Adobe Customer Journey Analytics | Pure-play analytics | Sem amarração operacional; sem real-time |
| NICE Enlighten XM | Experience Management | Parcial — métricas de experiência, não de jornada operacional completa |
| Pega Customer Decision Hub | Case management BPM | Forte em decisão; analytics enterprise mas separada do contact center |

**Diferencial PlugHub:** Journey como entidade operacional + analítica nativa, com drill-down até turno, sem dependência de produto externo. Combina o que Pointillist faz (analytics de jornada) com o que Service Cloud faz (case management) num único primitive.

---

## Itens em aberto para validação

- [ ] Confirmar com time Arc 10 que `journey.events` Kafka topic já tem todos os event_types listados (ou priorizar quais faltam)
- [ ] Validar com time de Analytics o particionamento mensal vs. semanal — campanhas outbound de alto volume podem exigir partitions mais granulares
- [ ] Decidir se `session_summary` é nova tabela ou se ContactSegment (Arc 5) absorve a função com `journey_id` adicionado
- [ ] Confirmar TTL de 7 anos — alguns setores (financeiro BR, telco) exigem 10
- [ ] Definir quais KPIs entram no dashboard padrão (Dashboard #35) na primeira versão — proposta: TTR, Resolution rate, Multi-channel ratio, Touches, FCR
- [ ] Validar HATEOAS pattern com time de Frontend — alternativa: client-side path building com schema OpenAPI

---

## Referências cruzadas

- [`arcos/arc10-journey.md`](arc10-journey.md) — modelo operacional da Journey
- [`arcos/arc5-segments.md`](arc5-segments.md) — ContactSegment analytics (precedente do `session_summary`)
- [`arcos/dashboard.md`](dashboard.md) — Dashboard #35, ENDPOINT_CATALOG (onde os endpoints acima são registrados)
- [`kafka-eventos.md`](../kafka-eventos.md) — `journey.events` topic schema
- [`modelos-de-dados.md`](../modelos-de-dados.md) — schema ClickHouse geral
- [`product/value-proposition.md`](../product/value-proposition.md) — Diferencial 6 (Journey lifecycle-centric)
