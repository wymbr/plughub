# Layer 7 — Data Layer

> Spec de referência: v24.0 seções 2.3, 5.1–5.4, 13.4
> Responsabilidade: persistência de toda a plataforma — estado efêmero de sessão, registro de configuração, auditoria e analytics, armazenamento de mídia
> Implementado por: Redis Cluster, PostgreSQL + pgvector, ClickHouse, Object Storage (S3/GCS)

---

## Visão geral

A Data Layer divide a persistência em quatro tecnologias com naturezas e casos de uso distintos. Nenhuma delas é genérica — cada uma foi escolhida para o tipo de dado que domina seu uso.

| Tecnologia | Natureza | Uso principal |
|---|---|---|
| **Redis Cluster** | Efêmero, em memória | Estado de sessão em tempo real, filas, heartbeats, coordenação cross-site |
| **PostgreSQL + pgvector** | Relacional + vetorial | Registro de configuração, perfis, histórico de conversas, base de conhecimento |
| **ClickHouse** | Colunar, append-only | Analytics operacional, audit log de ações MCP, métricas de agentes |
| **Object Storage** | Blob, lifecycle policies | Áudio de ligações, datasets de fine-tuning, versões de modelos STT |

Para os schemas detalhados de cada chave Redis, tabela PostgreSQL e tabela ClickHouse, ver **[modelos-de-dados.md](../modelos-de-dados.md)**.

---

## Redis Cluster

Acesso em microssegundos. Operações atômicas (`DECR` para reserva de recursos escassos sem lock). Backbone de coordenação cross-site.

**Casos de uso:**

| Dado | TTL | Dono |
|---|---|---|
| Estado de sessão do agente (`agent:{instance_id}` HASH) | 30s (renovado por heartbeat) | mcp-server-plughub |
| Pipeline state (`{tenant_id}:pipeline:{session_id}`) | 24h | skill-flow-engine |
| Session context do Routing Engine | 4h | routing-engine |
| Estado de sessão AI (`session:{session_id}:ai`) | 24h | ai-gateway |
| Fila de pool por prioridade (`pool:{pool_id}:queue` ZSET) | Sem TTL | routing-engine |
| Regras ativas (`rules:active`) | Sem TTL | rules-engine |
| Insight da conversa (`insight.conversa.*`) | Até `contact_closed` | mcp-server-plughub |
| Pending Delivery Store (`outbound.*`) | Até entrega ou expiração | notification-agent (via MCP) |

**Multi-site:** 7 nodes (3 Site A + 3 Site B + 1 árbitro). Quorum de 4/7 para confirmar escrita. Sem split brain. Reservas atômicas via `DECR` — sem eleição de líder.

**Acesso Redis direto:** apenas `routing-engine` e `skill-flow-engine`. Outros módulos acessam via MCP tools autorizadas.

---

## PostgreSQL + pgvector

Estado persistente de configuração e histórico de longo prazo. TimescaleDB para queries temporais. pgvector para base de conhecimento vetorial.

**Tabelas principais (agent-registry):**

| Tabela | Conteúdo |
|---|---|
| `Pool` | Configuração de pools (channel_types, SLA, supervisor_config) |
| `AgentType` | Tipos de agente com flow JSON, canary weights, version_policy |
| `AgentTypePool` | Associação many-to-many entre AgentType e Pool |
| `AgentInstance` | Instâncias ativas com status e metadata |
| `Skill` | Skills registradas com input/output schema |

**Acesso:** somente via `agent-registry` (API REST). Outros módulos não acessam PostgreSQL diretamente.

---

## ClickHouse

Colunar, append-only por design. Queries sobre bilhões de linhas em segundos. Audit log e analytics operacional.

**Tabelas principais:**

| Tabela | Conteúdo | Producer |
|---|---|---|
| `escalation_audit` | Histórico de escalações com motivo, agente origem/destino, timestamp | rules-engine |
| `mcp_audit` | Toda chamada MCP interceptada — tool, agente, resultado, latência | PlugHubAdapter / proxy sidecar |
| `conversations_analytics` | Eventos de `conversations.events` materializados para queries | Kafka consumer |
| `agent_quality_scores` | Scores calculados pelo Evaluation Agent por atendimento | Evaluation Agent (Horizonte 2) |

**Alimentação:** Kafka consumer materializa eventos em tabelas analíticas — cada evento vira uma linha com schema fixo. Kafka Connect exporta incrementalmente para Snowflake/BigQuery/S3 Parquet.

---

## Object Storage (S3/GCS)

Blob storage com lifecycle policies por idade para controle de custo.

| Conteúdo | Retenção | Uso |
|---|---|---|
| Áudio de ligações (SIP/WebRTC) | 30 dias (LGPD) | Auditoria, dataset de fine-tuning |
| Datasets de fine-tuning STT | Lifecycle por uso | Ray Train (MLOps Layer) |
| Versões de modelos STT | Lifecycle por versão | Model Registry (MLOps Layer) |
| Mídia de WhatsApp (URLs expiram em ~5min) | 30 dias | Contexto de conversa |

---

## Modelo analítico em três camadas (seção 13.4)

Os dados distribuídos entre Redis, Kafka e PostgreSQL não são adequados para queries analíticas diretamente. O modelo de três camadas resolve isso:

**Camada 1 — Persistência analítica:** Kafka consumer materializa eventos → ClickHouse. Kafka Connect exporta → warehouse externo. O momento crítico é a serialização da sessão Redis no `contact_closed` — único ponto onde estado efêmero se torna persistente.

**Camada 2 — Modelos first-party (dbt):** Conversational Analytics, Agent Performance, Operacional, Valor de Negócio, Knowledge Base Analytics. Entregues como views pré-calculadas sem que cada tenant precise construir do zero.

**Camada 3 — Data Mining (Horizonte 2):** clustering de intents, padrões de escalação, jornada do cliente, anomalia em tempo real. Requer volume de dados que não existe no lançamento.

---

## Considerações operacionais

**`insight.historico.*` persiste via Kafka, nunca por escrita direta em PostgreSQL.** O fluxo é: `insight_register` publica `insight.registered` em `conversations.events`; consumer promove `insight.conversa.*` → `insight.historico.*` no `contact_closed`. A fronteira de persistência é o contato, não a sessão do agente.

**Degradação Redis:** se Redis fica indisponível por < 30s, Rules Engine e Escalation Engine operam com último estado em cache local. Por > 30s, cada site opera de forma autônoma sem balanceamento cross-site.

**Isolação por tenant:** Redis via keyspace por `tenant_id`; Kafka via tópicos prefixados; PostgreSQL e ClickHouse via coluna `tenant_id` em todas as queries (modelo C). Modelo A tem infraestrutura completamente separada por tenant.

**SLA Redis:** 99,99% — 7 nodes com quorum 4/7. Operações críticas (reserva de especialistas, transições de pipeline) são atômicas.

---

## Referência spec

- Seção 2.3 — Stack de Dados
- Seção 5.1–5.3 — Arquitetura Multi-Site e Degradação Graciosa
- Seção 5.4 — Retenção Kafka por Tópico
- Seção 13.4 — Modelo Analítico em Três Camadas
- [modelos-de-dados.md](../modelos-de-dados.md) — schemas completos por módulo
