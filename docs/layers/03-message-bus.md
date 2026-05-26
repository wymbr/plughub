# Layer 3 — Message Bus

> Última atualização: 2026-05-25 · Estado: Arc 16
> Spec de referência: v24.0 seções 2.4, 5.4
> Responsabilidade: backbone de eventos assíncrono — desacopla produtores de consumidores, garante entrega e persistência de eventos da plataforma
> Implementado por: Apache Kafka (`docker-compose.infra.yml`)

---

## Visão geral

O Message Bus é o eixo de comunicação assíncrona da plataforma. Toda troca de informação entre componentes que não exige resposta síncrona passa pelo Kafka. Isso garante que nenhum componente depende da disponibilidade imediata de outro — o produtor publica e segue; o consumidor processa no seu próprio ritmo.

O princípio central da arquitetura é **event-driven first**: nenhum componente chama outro diretamente de forma síncrona, exceto onde a latência é crítica (AI Gateway, MCP tools em tempo real de atendimento).

Para a documentação completa dos tópicos, schemas de eventos, produtores, consumidores e consumer groups, ver **[kafka-eventos.md](../kafka-eventos.md)**.

---

## Componentes

| Componente | Responsabilidade |
|---|---|
| **Kafka Cluster** | 3 brokers por site, replicação cross-site via MirrorMaker 2 |
| **Kafka MirrorMaker 2** | Replicação de tópicos entre sites A e B para resiliência multi-site |
| **Kafka Connect** | Exportação incremental para data warehouse externo (Snowflake, BigQuery) ou data lake (S3 Parquet) |
| **KEDA** | Auto-scaling do Agent Pool baseado em consumer lag dos tópicos Kafka |

---

## Tópicos

| Tópico | Produtores | Consumidores |
|---|---|---|
| `conversations.inbound` | channel-gateway | Core, routing-engine |
| `conversations.routed` | routing-engine | Core, rules-engine |
| `conversations.queued` | routing-engine | rules-engine |
| `conversations.abandoned` | routing-engine | Core, rules-engine |
| `conversations.session_opened/closed` | Core | analytics, LGPD |
| `conversations.message_sent` | Core | analytics |
| `conversations.participants` | orchestrator-bridge | analytics-api → ClickHouse |
| `rules.escalation.events` | rules-engine | routing-engine |
| `rules.shadow.events` | rules-engine | analytics |
| `rules.session_tagged` | rules-engine | analytics |
| `registry.changed` | agent-registry | routing-engine, Core, orchestrator-bridge |
| `config.changed` | Config API | orchestrator-bridge, routing-engine |
| `gateway.heartbeat` | channel-gateway | routing-engine |
| `agent.lifecycle` | mcp-server-plughub | routing-engine, analytics |
| `agent.done` | routing-engine | rules-engine, analytics |
| `queue.position_updated` | routing-engine | channel-gateway, analytics |
| `mcp.audit` | McpInterceptor / proxy sidecar | analytics, LGPD |
| `sentiment.updated` | ai-gateway | analytics-api |
| `evaluation.events` | evaluation-api | analytics-api → ClickHouse |
| `calibration.events` | evaluation-api | analytics-api → ClickHouse |
| `workflow.events` | workflow-api | skill-flow-worker |
| `collect.events` | workflow-api | analytics-api, channel-gateway |
| `journey.events` | workflow-api | analytics-api → ClickHouse |
| `agent.events` | agentes (via `agent_event` MCP tool) | analytics-api → ClickHouse |
| `usage.events` | Core, ai-gateway, channel-gateway | usage-aggregator |
| `events.dead_letter` | skill-flow-worker, analytics-api, orchestrator-bridge | ops/monitoring |

Ver `kafka-eventos.md` para schemas completos e consumer groups. Todos os eventos cross-package têm schemas Zod em `@plughub/schemas`.

---

## Interfaces

**Produção:** todos os componentes publicam via cliente Kafka (aiokafka no Python, KafkaJS no TypeScript).

**Consumo:** cada componente declara seu consumer group — garante que cada evento seja processado exatamente uma vez por grupo, independentemente do número de instâncias do consumidor.

**Exportação analítica:** Kafka Connect materializa eventos em ClickHouse (analytics operacional) e opcionalmente em Snowflake/BigQuery/S3 Parquet para tenants com infra própria.

---

## Fluxo de dados

```
Componente produtor publica evento no tópico
↓
Kafka replica entre brokers (replicação intra-site)
↓
Kafka MirrorMaker 2 replica para o site secundário
↓
Consumer group do componente consumidor processa
↓
Kafka Connect materializa em ClickHouse / warehouse externo (assíncrono)
```

**Auto-scaling via KEDA:**
```
Consumer lag no tópico conversations.inbound ou conversations.routed
↓ KEDA detecta lag acima do threshold
↓ escala réplicas do Agent Pool horizontalmente
↓ lag reduz → KEDA escala para baixo
```

---

## Considerações operacionais

**Retenção por tópico** (política de compliance + custo):

| Dado | Retenção |
|---|---|
| Transcrições de áudio | 30 dias (LGPD) |
| Eventos de decisão de agente | 1 ano (auditoria) |
| Estado efêmero de conversa | 7 dias |
| Audit log de ações em sistemas | 5 anos (compliance) |

**Multi-site:** clusters Kafka independentes por site. MirrorMaker 2 garante replicação cross-site. Se um site cai, o outro opera com seu próprio cluster — sem dependência do site falho para processar novos eventos.

**SLA Kafka:** 99,95% — 3 brokers por site com replicação cross-site. Degradação graciosa: se Kafka fica indisponível, componentes que dependem dele param de publicar mas não perdem o estado de sessão (Redis persiste independentemente).

**Isolação por tenant (modelo C — stack compartilhada):** tópicos prefixados por `tenant_id`. Isolação lógica — não há clusters por tenant no modelo C.

**Isolação por tenant (modelo A — instância dedicada):** cluster Kafka próprio por tenant ou tópicos isolados em Kafka gerenciado externo.

---

## Referência spec

- Seção 2.4 — Tópicos Kafka
- Seção 5.3 — Degradação Graciosa
- Seção 5.4 — Kafka: Retenção por Tópico
- Seção 5.5 — SLA Kafka
- Seção 13.4 — Modelo Analítico em Três Camadas (Kafka Connect → ClickHouse)
- [kafka-eventos.md](../kafka-eventos.md) — schemas completos de todos os tópicos
