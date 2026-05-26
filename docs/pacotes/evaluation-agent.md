# Módulo: Evaluation Agent

> Última atualização: 2026-05-25 · Estado: Arc 16

> **Responsabilidade:** avaliar a qualidade de uma sessão encerrada aplicando o
> formulário (`EvaluationForm`) da campanha, e submeter o resultado à `evaluation-api`.

---

## Visão geral

O Evaluation Agent é um **agente nativo de orquestração** implementado como um
Skill Flow YAML genérico (`agente_avaliacao_v1`). Não é um serviço Python
dedicado — é executado pelo `SkillFlowEngine` como qualquer outro agente
orquestrador da plataforma.

O ciclo de avaliação **não** depende mais de um agregador de transcript próprio.
Ele se apoia em três componentes dedicados, introduzidos a partir do Arc 6:

| Componente | Papel |
|---|---|
| `evaluation-api` (porta 3400) | Ciclo de vida de formulários, campanhas, instâncias, resultados e contestações |
| Session Replayer / Hydrator | Reconstrói o stream canônico da sessão como `ReplayContext` |
| `mcp-server-knowledge` (porta 3401) | Base de conhecimento vetorial (pgvector) para RAG |

---

## Como uma avaliação é criada — sampling engine

Não há mais um "Rules Engine de amostragem" nem um "Evaluation Trigger". O
**sampling engine** vive dentro da `evaluation-api`: ele consome eventos
`conversations.session_closed`, avalia as `SamplingRules` de cada campanha ativa
e **cria automaticamente uma `EvaluationInstance`** quando a sessão é selecionada.

`SamplingRules` suporta `mode: all | percentage | fixed`, filtros por
`agent_type_ids`, `pool_ids`, `channels`, `outcome_filter`, `min_duration_s` e
prioridades por campo.

A criação da instância dispara `evaluation.requested` → o Session Replayer
constrói o `ReplayContext` → o agente avaliador é acionado.

---

## Pipeline de avaliação

```
conversations.session_closed
    │
    ▼
sampling engine (evaluation-api)  → cria EvaluationInstance
    │
    ▼
evaluation.requested  (Kafka)
    │
    ▼
Hydrator → Replayer → ReplayContext  ({tenant}:replay:{session_id}:context, TTL 1h)
    │      (stream canônico + evaluation_form + campaign_context + knowledge_snippets top-5)
    ▼
agente_avaliacao_v1 (SkillFlowEngine)
    │  evaluation_context_get → reason (LLM) → evaluation_submit
    ▼
evaluation.events  (Kafka)  →  analytics-api  →  ClickHouse
```

→ Ver [`docs/arcos/session-replayer.md`](../arcos/session-replayer.md).

---

## Tópico Kafka

| Tópico | Produzido por | Consumido por |
|---|---|---|
| `evaluation.events` | `evaluation-api` (submit + review + contestação + lock + finalize) | `analytics-api` → ClickHouse |

A `evaluation-api` é a única produtora de `evaluation.events`. Os tipos de evento
incluem `submitted`, `reviewed`, `contested`, `locked` e — a partir do Arc 13 —
`evaluation_finalized` (única fonte de verdade para relatórios de qualidade).

---

## MCP tools do agente avaliador

O `mcp-server-plughub` expõe as tools usadas pelo `agente_avaliacao_v1`:

| Tool | Função |
|---|---|
| `evaluation_context_get` | Retorna o `ReplayContext` enriquecido: stream events, `evaluation_form`, `campaign_context`, `knowledge_snippets` (top-5) |
| `evaluation_submit` | Submete `criterion_responses[]` + `overall_score` + `compliance_flags` à `evaluation-api` |
| `evaluation_lock` | Congela um resultado (idempotente — 409 = já locked) |

RAG é feito via `mcp-server-knowledge` (tools `knowledge_search`, `knowledge_upsert`,
`knowledge_delete`) sobre a tabela `knowledge_snippets` em pgvector.

---

## agente_avaliacao_v1 — v3.0 (Arc 13)

`packages/skill-flow-engine/skills/agente_avaliacao_v1.yaml`

O YAML do agente avaliador foi reescrito pelo Arc 13 (v3.0). Mudanças principais
em relação às versões anteriores:

- **`dimension_threads[]`** — cada dimensão avaliada produz um thread com
  `evidence_entries[]` **obrigatório**: o agente precisa registrar evidência
  textual por dimensão, não apenas uma justificativa solta.
- **Critérios `auto_computed`** — critérios objetivos (AHT, SLA, contagem de
  transferências) são pulados pelo LLM e preenchidos deterministicamente por
  `SessionMetricsExtractor` + `fill_auto_computed_criteria()` / `compute_auto_criterion_score()`.
- **`calibration_notes`** — notas de calibração publicadas no namespace de
  conhecimento são injetadas no contexto do LLM, fechando o loop de feedback.
- **`evaluated_agent_type`** — roteia o resultado: agente humano → fluxo de
  contestação; agente AI → `evaluation_finalized` imediato + curadoria amostral.

Fluxo resumido:

```
carregar_contexto (evaluation_context_get)
  → ReplayContext: stream, evaluation_form, campaign_context, knowledge_snippets, calibration_notes

avaliar_criterios (reason LLM):
  - para cada critério não-auto_computed do formulário:
      score / pass_fail / N/A + dimension_thread com evidence_entries[]
  - critérios auto_computed pulados (preenchidos pelo SessionMetricsExtractor)
  - knowledge_snippets + calibration_notes como contexto normativo
  - detecta compliance_flags

submeter_resultado (evaluation_submit):
  - criterion_responses[] + dimension_threads[] + overall_score
  - eval_status: "submitted"
```

---

## Arc 13 — Review, Contestation & Calibration

O Arc 13 fechou o ciclo de qualidade com dois agentes adicionais e dois fluxos
separados pelo tipo de agente avaliado:

| Agente | YAML | Papel |
|---|---|---|
| `agente_pre_revisor_v1` | `agente_pre_revisor_v1.yaml` v1.0 | Gate de qualidade pré-publicação — revisa o resultado do avaliador antes de publicá-lo ao agente humano; pode emitir `calibration_signal` |
| `agente_revisor_v1` | `agente_revisor_v1.yaml` v1.0 | Árbitro pós-contestação — decide `upheld`/`revised` por dimensão contestada |

- **Fluxo 1 (agente humano avaliado):** AI Evaluator → (se campanha tem
  pre-review) AI Reviewer → publicação → contestação por dimensão → Human
  Reviewer (decisão final) → `evaluation_finalized`.
- **Fluxo 2 (agente AI avaliado):** AI Evaluator → `evaluation_finalized`
  imediato; curadoria amostral paralela por regras configuráveis → curador
  humano → `CalibrationNote` publicada no namespace de conhecimento → feedback
  ao avaliador via RAG.

O ciclo de revisão/contestação é dirigido por um Skill Flow (`skill_revisao_treplica_v1`)
como motor de estado — o YAML é o único dono da lógica de quantos rounds existem.

→ Ver [`docs/arcos/arc13-review-contestation.md`](../arcos/arc13-review-contestation.md)
e [`docs/arcos/arc6-evaluation.md`](../arcos/arc6-evaluation.md).

---

## ClickHouse — modelo de dados

O consumer da `analytics-api` persiste `evaluation.events` em duas tabelas:
`evaluation_results` (ReplacingMergeTree — estado atual) e `evaluation_events`
(MergeTree — log append-only). O Arc 13 adiciona `calibration_events`.

→ DDL completo em [`docs/pacotes/clickhouse-consumer.md`](clickhouse-consumer.md)
e [`docs/arcos/arc6-evaluation.md`](../arcos/arc6-evaluation.md).

Os relatórios de qualidade são expostos pela `analytics-api` em
`GET /reports/evaluations`, `GET /reports/evaluations/summary` e
`GET /reports/evaluator-calibration` — lidos pela `platform-ui`.

---

## Relações com outros módulos

| Módulo | Relação |
|---|---|
| `evaluation-api` | Sampling engine cria instâncias; recebe `evaluation_submit`; produz `evaluation.events` |
| `Session Replayer` / `Hydrator` | Reconstroem o stream canônico como `ReplayContext` |
| `SkillFlowEngine` | Executa `agente_avaliacao_v1` e os agentes de revisão |
| `mcp-server-plughub` | Fornece `evaluation_context_get`, `evaluation_submit`, `evaluation_lock` |
| `mcp-server-knowledge` | Base de conhecimento vetorial (RAG) para os agentes avaliadores |
| `AI Gateway` | Invocado via step `reason` para preenchimento dos critérios |
| `analytics-api` | Consome `evaluation.events`, persiste no ClickHouse, expõe `/reports/*` |
| `workflow-api` | Motor de estado do ciclo de revisão/contestação (Arc 13) |
