# ADR: Session Replayer — Avaliação de Qualidade Pós-Sessão

**Status:** Implementado  
**Data:** 2026-04-20  
**Componentes:** `packages/session-replayer`, `@plughub/schemas`, `mcp-server-plughub`

---

## Contexto

O PlugHub precisa de um mecanismo de avaliação de qualidade pós-sessão que:
- Use o mesmo stream canônico já existente (não duplica infraestrutura)
- Respeite o timing original dos eventos para avaliação fiel de SLA e latência
- Funcione mesmo após o TTL do Redis expirar (sessões avaliadas horas ou dias depois)
- Escale horizontalmente sem bottlenecks estruturais
- Separe a carga de avaliação da carga operacional (ver ADR: ai-gateway-separation)

## Decisão

### Padrão ensure-before-read com Hydrator opcional

O Replayer **sempre lê do Redis** — caminho único, código simples.
Um módulo separado (Stream Hydrator) garante que o Redis está populado antes do Replayer rodar.
O Replayer não sabe se os dados vieram do Redis original (hot) ou do PostgreSQL reconstruído (cold).

```
conversations.session_closed
        │
        ├──→ Stream Persister (PostgreSQL)     ← persiste imediatamente, independente de avaliação
        │
        └──→ evaluation.requested (Kafka)
                    │
                    ▼
             Stream Hydrator
             Redis hit?  → passa direto            (hot path: < 1ms)
             Redis miss? → lê PostgreSQL → Redis    (cold path: reconstituição transparente)
                    │
                    ▼
                Replayer
             (sempre lê Redis — caminho único)
                    │
                    ▼
             ReplayContext escrito em Redis
             {tenant_id}:replay:{session_id}:context   TTL: 1h
                    │
                    ▼
             Evaluator agent (pool avaliador_qualidade)
             evaluation_context_get → evaluation_submit
                    │
                    ▼
             evaluation.events (Kafka) → consumer persiste no PostgreSQL
```

### Timing fiel com speed_factor

Cada `ReplayEvent` carrega `delta_ms` — delta em milissegundos desde o evento anterior,
calculado na hora da persistência (Persister) ou na leitura do stream (Replayer).

O Replayer usa `speed_factor` para escalar o timing:
- `1.0` = real-time (avalia latência do agente fielmente)
- `10.0` = 10x mais rápido (default para batch de avaliação)

Sem timing fiel, o evaluator não consegue julgar se o agente respondeu dentro do SLA
ou se a escalação foi tardia.

## Componentes implementados

### `@plughub/schemas/evaluation.ts`

| Schema | Descrição |
|--------|-----------|
| `EvaluationDimensionSchema` | Dimensão individual de qualidade (score 0–10, peso, notas) |
| `EvaluationResultSchema` | Resultado completo: composite_score, dimensões, summary, highlights, compliance_flags |
| `ReplayEventSchema` | Evento do stream reconstituído com `original_content` e `delta_ms` |
| `ReplayContextSchema` | Pacote completo entregue ao evaluator: events + sentiment + participants + meta |
| `EvaluationRequestSchema` | Evento publicado em `evaluation.events` para iniciar avaliação |
| `ComparisonReportSchema` | Comparação turn-a-turn produção vs replay (schema definido, implementação pendente) |

### `packages/session-replayer/` (Python)

| Módulo | Responsabilidade |
|--------|-----------------|
| `stream_persister.py` | Lê `session:{id}:stream` do Redis → persiste em `session_stream_events` (PostgreSQL) |
| `stream_hydrator.py` | `ensure(session_id)` — Redis hit: no-op; Redis miss: lê PG → reconstrói Redis com TTL 1h |
| `replayer.py` | Lê stream do Redis, calcula `delta_ms`, lê metadata complementar, escreve `ReplayContext` |
| `consumer.py` | Dois Kafka consumers: persister (session_closed) + replayer (evaluation.requested) |
| `models.py` | Pydantic models espelhando os schemas TypeScript |

### `mcp-server-plughub/tools/evaluation.ts` (novas tools)

| Tool | Descrição |
|------|-----------|
| `evaluation_context_get` | Lê `ReplayContext` do Redis; requer role `evaluator` ou `reviewer` |
| `evaluation_submit` | Submete `EvaluationResult` → publica em `evaluation.events`; reduz TTL do contexto |

### Redis keys

| Key | Conteúdo | TTL |
|-----|----------|-----|
| `session:{id}:stream` | Stream canônico (Redis Streams) | 4h (sessão) |
| `{tenant_id}:replay:{session_id}:context` | `ReplayContext` JSON | 1h |

### PostgreSQL

| Tabela | Conteúdo |
|--------|----------|
| `session_stream_events` | Todos os eventos do stream com `original_content`, `delta_ms`, índices por `session_id` e `timestamp` |

## Separação de carga operacional vs avaliação

Ver ADR: `adr-ai-gateway-separation.md`.
O pool `avaliador_qualidade` aponta para `ai-gateway-evaluation` (deployment separado).
Avaliações não consomem budget de rate limit do AI Gateway operacional.

## Scaling

- Kafka consumer groups: adicionar instâncias do Replayer = adicionar paralelismo (1 instância por partição)
- Stream Hydrator: idempotente — múltiplas instâncias podem tentar hidratar a mesma sessão sem conflito (Redis SET é atômica)
- Stateless: nenhum estado em memória — todas as instâncias são equivalentes

## Validado

- **E2E scenario 09** (11 assertions): pipeline completo validado — session_closed stream populado → ReplayContext escrito no Redis → `evaluation_context_get` retorna ReplayContext com `replay_id` correto → `evaluation_submit` publica `EvaluationResult` em `evaluation.events` → evaluator back to ready. Todos 11 assertions passando.
- **Dockerfile**: `packages/session-replayer/Dockerfile` implementado (Python 3.11-slim). `DATABASE_URL` deve apontar para `plughub_demo` (nome do database no demo environment).

## Pendente (próxima iteração)

- **Comparison mode**: `EvaluationRequest.comparison_mode: true` → captura respostas de produção e gera `ComparisonReport` (schema definido, comparator não implementado)
- **Masking config UI** no Agent Registry para configurar `evaluator_pool` por tenant
