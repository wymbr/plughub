# Layer 9 — Observability Layer

> Spec de referência: v24.0 seção 13 (Analytics e Observabilidade)
> Responsabilidade: visibilidade completa da plataforma — métricas técnicas, rastreabilidade de decisões IA, qualidade contínua, dashboards operacionais
> Implementado por: ferramentas externas (não são módulos do monorepo)

---

## Visão geral

A Observability Layer não é um módulo da plataforma — é a camada de ferramentas externas que consomem os dados que a plataforma produz naturalmente durante a operação. Nenhuma instrumentação especial é necessária nos agentes: o AI Gateway, o MCP audit log e o Kafka fornecem todos os sinais.

A observabilidade é organizada em três domínios complementares:

| Domínio | Ferramentas | O que responde |
|---|---|---|
| **Técnico** | Prometheus + Grafana + OpenTelemetry + Jaeger | Latência, erros, throughput, lag Kafka, Redis, MCP |
| **Comportamento IA** | LangSmith / Langfuse + Prometheus custom | Decisões de agente, tokens, custo, tool usage, escalações |
| **Qualidade contínua** | Eval Pipeline + ClickHouse + Superset | Evals assíncronos em produção, curadoria, regressões |

---

## Componentes

### Métricas técnicas

| Ferramenta | Uso |
|---|---|
| **Prometheus** | Coleta métricas de todos os componentes — latência, throughput, taxa de erro, estado de circuit breakers por instância e site, consumer lag Kafka, uso de Redis |
| **Grafana** | Dashboards operacionais sobre métricas Prometheus |
| **OpenTelemetry** | Traces distribuídos — correlação entre componentes de uma mesma conversa |
| **Jaeger** | Armazenamento e visualização de traces OpenTelemetry |

**Circuit breaker visibility:** Prometheus coleta o estado por instância e agrega por site e sistema externo. Perguntas operáveis: *quantas instâncias do site RJ estão com circuit breaker aberto para o CRM? O problema é parcial ou total?*

### Rastreabilidade de IA

| Ferramenta | Uso |
|---|---|
| **LangSmith / Langfuse** | Rastreabilidade de cada chamada LLM: prompt, resposta, tokens, latência, custo, tool usage, cadeia de decisão do agente |
| **Prometheus custom** | Métricas de comportamento IA: escalation_rate, sentiment_delta, resolution_rate, cost_per_conversation |

**Propagação de trace:** o SDK propaga o `session_id` da plataforma como trace ID raiz para o sistema de observabilidade do agente. Correlação entre eventos da plataforma e traces internos é automática — agentes externos não perdem sua observabilidade ao integrar com a plataforma.

**Adaptadores disponíveis no SDK:** OpenTelemetry, LangSmith, Langfuse, Datadog. Interface `TelemetryAdapter` para sistemas proprietários.

**Agentes de plataforma** (Orchestrator, Notification Agent) são auditados como sequências de raciocínio — múltiplas chamadas LLM, múltiplas tools, cadeia de decisão rastreável no LangSmith/Langfuse.

### Qualidade contínua e analytics

| Ferramenta | Uso |
|---|---|
| **ClickHouse** | Audit log de ações MCP, eventos de conversa materializados, scores de qualidade de agente |
| **Apache Superset** | Dashboards operacionais e exploração ad-hoc conectados diretamente ao ClickHouse |
| **dbt** | Transformações analíticas — marts de performance, jornada do cliente, KPIs de resolução, Knowledge Base Analytics |
| **Kafka Connect** | Exportação incremental para Snowflake/BigQuery/S3 Parquet (tenants com infra própria) |

---

## Sinais por domínio

**Canary monitoring (Agent Registry):** métricas monitoradas em promoção de versão de agente: `success_rate`, `escalation_rate`, `sentiment_delta`, `cost_per_conversation`. Rollback imediato disponível via Agent Registry.

**Shadow mode (Rules Engine):** regras em shadow mode avaliam e registram em `rules.shadow.events` o que fariam sem acionar o Escalation Engine — primeiras 24–48h após ativação de nova regra.

**Alertas por severidade:**

| Nível | Exemplos | Ação |
|---|---|---|
| CRITICAL | Redis indisponível, Kafka particionado, AI Gateway em cascata | PagerDuty / on-call imediato |
| WARNING | Taxa de escalação acima do threshold, WER STT degradando, circuit breaker abrindo | Slack / análise em horário comercial |
| INFO | Dashboard apenas | Novo agente promovido, canário avançou, fine-tuning STT concluído |

---

## Interfaces

**Produção de dados pela plataforma:**
- `conversations.events` (Kafka) → Kafka consumer → ClickHouse
- AI Gateway → LangSmith/Langfuse (via SDK TelemetryAdapter)
- Todos os componentes → Prometheus (endpoints `/metrics`)
- PlugHubAdapter / proxy sidecar → audit log MCP → Kafka → ClickHouse
- STT Router → métricas WER por tenant → Prometheus

**Consumo externo:**
- Grafana consome Prometheus
- Superset consome ClickHouse
- Jaeger armazena traces OpenTelemetry
- dbt transforma dados no ClickHouse/warehouse

---

## Considerações operacionais

**Refresh de dashboards:** 30 segundos resolve 99% dos casos operacionais. Dashboards sub-segundo não estão no roadmap de nenhum horizonte — complexidade alta, valor operacional não validado.

**Sem instrumentação adicional nos agentes:** o AI Gateway e o audit log MCP fornecem todos os sinais de comportamento IA. Agentes não precisam de SDK de observabilidade próprio para serem rastreados — o trace ID propagado pelo `session_id` garante correlação automática.

**Knowledge Base Analytics** (via dbt): auditoria completa de consultas a `mcp-server-knowledge` — cobertura de intent, eficácia de artigo, artigos órfãos, drift de relevância, cadeia de consulta, correlação com sentiment. Output é uma fila de trabalho para curadoria, não apenas relatório.

**Dados de qualidade:** os mesmos dados do audit log MCP e do ClickHouse alimentam o Evaluation Agent (Horizonte 2) — sem nova infraestrutura necessária.

---

## Referência spec

- Seção 13 — Analytics e Observabilidade
- Seção 13.4 — Modelo Analítico em Três Camadas
- Seção 2.2 — Frameworks (LangSmith, Langfuse, Prometheus, OpenTelemetry)
- Seção 5.5 — SLAs por Componente
- Seção 1905 — Propagação de trace ID via SDK
