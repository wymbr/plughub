# Módulo: evaluation

> Responsabilidade: plataforma completa de avaliação de qualidade de atendimentos — avaliação automática por IA, revisão, contestação, aprovação humana, feedback loop e relatórios.

---

## Visão geral

O módulo de avaliação cobre o ciclo completo de qualidade: avaliação automática de cada atendimento encerrado → revisão por IA → contestação pelo agente → aprovação pelo supervisor → feedback loop para calibração → relatórios de qualidade.

O mesmo template de avaliação (evaluation skill) é aplicado a agentes de IA de qualquer framework e a agentes humanos — mesma escala, mesmos critérios, comparação direta entre pools.

---

## Componentes

| Componente | Status | Referência |
|---|---|---|
| **Evaluation Agent** (SkillFlow nativo) | ✅ Especificado — Piloto | [evaluation-agent.md](evaluation-agent.md) |
| **Conversation Writer** | ✅ Especificado — Piloto | [conversation-writer.md](conversation-writer.md) |
| Reviewer Agent | Horizonte 2 | stub abaixo |
| Contestation Engine | Horizonte 2 | stub abaixo |
| Approval Engine | Horizonte 2 | stub abaixo |
| Feedback Loop | Horizonte 2 | stub abaixo |
| Reporting / Dashboard | Piloto — escopo a definir | — |

---

## Evaluation Agent — resumo

Agente nativo de orquestração implementado como Skill Flow YAML genérico
(`agente_avaliacao_v1`). Executado pelo Routing Engine via `SkillFlowEngine.run()`
ao receber `evaluation.requested`. Não é um serviço dedicado — usa a mesma
infraestrutura de execução de todos os agentes orquestradores da plataforma.

**Orquestração:** busca transcript → resolve dados externos declarados na skill
(`evaluation_context_resolve`) → invoca AI Gateway para preenchimento dos itens
→ publica `evaluation.completed` com scores calculados deterministicamente.

**Modelo de scoring:** média ponderada com pesos inteiros relativos, escala 0–10
por item. Produz `base_score` (seção mandatory, sempre comparável) e
`context_scores` independentes por seção contextual disparada.

**Dois YAMLs distintos:** o SkillFlow `agente_avaliacao_v1` é genérico e não
muda por pool. A evaluation skill `eval_{pool}_{dominio}_v{n}` declara o formulário
por pool — é o único arquivo alterado quando critérios de avaliação mudam.

**Amostragem:** o Rules Engine decide se um `contact_closed` gera avaliação,
mantendo contadores por sessão de agente no Redis com algoritmo de cota.

→ Spec completa: [evaluation-agent.md](evaluation-agent.md)

---

## Conversation Writer — resumo

Consumer Kafka que agrega eventos de mensagem de `conversations.inbound` e
`conversations.outbound` por contato, persiste o transcript em PostgreSQL
ao receber `contact_closed` e publica `transcript.created` para o
Evaluation Trigger.

→ Spec completa: [conversation-writer.md](conversation-writer.md)

## Evaluation Skills (templates de avaliação)

Declaradas em YAML por pool, versionadas no Skill Registry com o padrão
`eval_{pool}_{dominio}_v{n}`. Seções organizadas em hierarquia
section → subsection → item. Pesos inteiros relativos — sem exigência de
soma fixa. Itens com `applies_to: human | ai | all` e seções com
`applies_when` (intent, flags, agent_type) permitem formulários genéricos
instanciados dinamicamente por contato.

<!-- Horizonte 2: interface de configuração visual para analistas de qualidade,
sem necessidade de editar YAML diretamente. Workflow de aprovação de nova versão
de skill antes de ativação em produção. -->

## Fluxo completo de qualidade

### Piloto (implementado)

```
contact_closed
  → Conversation Writer      →  transcript.created
  → Rules Engine (sampling)  →  evaluation.requested  (se cota não atingida)
  → Routing Engine           →  SkillFlowEngine.run(agente_avaliacao_v1)
       ├── invoke transcript_get
       ├── invoke evaluation_context_resolve
       ├── reason evaluation_rubric_v1
       └── invoke evaluation_publish  →  evaluation.completed
  → ClickHouse consumer      →  evaluation_scores + evaluation_items
```

### Horizonte 2 (stub)

```
evaluation.completed
  → Reviewer Agent       — identifica inconsistências, classifica para
                           aprovação automática ou revisão humana
  → Contestation Engine  — workflow de contestação pelo agente avaliado
  → Approval Engine      — fila para supervisor com transcrição +
                           avaliação + justificativa + histórico
  → Feedback Loop        — aprovações/ajustes retroalimentam calibração
                           do Reviewer Agent
```

## Workflow de contestação — Horizonte 2

<!-- Como o agente avaliado pode contestar uma avaliação. Prazo, evidências,
quem arbitra. Como a contestação afeta o score e o histórico. -->

## Aprovação humana — Horizonte 2

<!-- Interface do supervisor: o que recebe (transcrição original, avaliação,
justificativa do Reviewer, histórico do agente avaliado). Ações disponíveis:
aprovar, ajustar notas, rejeitar avaliação. Impacto de cada ação no feedback loop. -->

## Feedback loop e calibração — Horizonte 2

<!-- Como aprovações e ajustes humanos retroalimentam o Reviewer Agent ao longo
do tempo. Métricas de calibração: taxa de aprovação automática, taxa de ajuste
por supervisor, drift de critério ao longo do tempo. -->

## Persistência

### Piloto
| Dado | Onde | Quem escreve |
|---|---|---|
| Transcript (mensagens) | PostgreSQL: `transcripts`, `transcript_messages` | Conversation Writer |
| Scores por seção | ClickHouse: `evaluation_scores` | ClickHouse consumer |
| Itens com justificativas | ClickHouse: `evaluation_items` | ClickHouse consumer |

### Horizonte 2
<!-- Estado de workflow de contestação/aprovação: Redis com TTL.
Métricas de calibração do Reviewer Agent: ClickHouse.
Configuração de evaluation skills por pool: PostgreSQL via Skill Registry. -->

## Relatórios

### Piloto
- base_score médio por agente por pool (últimos N dias)
- context_scores por agente por seção — apenas quando n ≥ 5
- Drill-down: seção → sub-seção → item → justificativa → transcript

### Horizonte 2
<!-- - Drift de qualidade — agentes que pioraram em N dias
- Outliers positivos e negativos por tipo de atendimento
- Comparação IA vs humano por critério de avaliação
- Taxa de contestação e resultado
- Calibração do Reviewer ao longo do tempo
- Eficácia por critério de template (quais itens mais discriminam qualidade) -->

## Relações com outros módulos

| Módulo | Relação |
|---|---|
| `channel-gateway` | Produz eventos de mensagem consumidos pelo Conversation Writer |
| `conversations.events` (Kafka) | Fonte do `contact_closed` consumido pelo Rules Engine |
| `rules-engine` | Decide amostragem por sessão de agente, publica `evaluation.requested` |
| `routing-engine` | Consome `evaluation.requested`, executa o agente de avaliação via SkillFlowEngine |
| `skill-flow-engine` | Executa o flow `agente_avaliacao_v1` |
| `mcp-server-plughub` | Tools `transcript_get`, `evaluation_context_resolve`, `evaluation_publish` |
| `ai-gateway` | Invocado via step `reason` para preenchimento de itens |
| `skill-registry` | Fonte das evaluation skills carregadas por `skill_id` |
| `agent-registry` | Fonte da `sampling_rate` e `skill_id_template` por pool |
| `agent-assist` | Supervisor acessa avaliações e drill-down de transcript (Horizonte 2) |
| `ClickHouse` | Persistência de scores e itens para dashboard e relatórios |

## Referência spec

- Seção 10.2 — Evaluation Agent
- Seção 10.3 — Reviewer Agent (Horizonte 2)
- Seção 13 — Analytics e Observabilidade (Agent Performance)
- Seção 14 — Conversation Writer e pré-requisitos do Evaluation Agent
