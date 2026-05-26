# PlugHub — Portal de Conhecimento

> Última atualização: 2026-05-25 · Estado da plataforma: Arc 16

Ponto de entrada único de toda a documentação do PlugHub. Cada seção mapeia um público e um nível de detalhe.

> **Revisão de documentação:** a avaliação completa do acervo (123 arquivos) está em [`revisao-documentacao-2026-05.md`](revisao-documentacao-2026-05.md). Documentos com drift conhecido estão marcados abaixo.

---

## Navegação rápida

| Quero entender... | Vá para |
|---|---|
| Visão técnica completa da plataforma | [visao-geral.md](visao-geral.md) |
| O que é o PlugHub e para quem serve | [product/overview.md](product/overview.md) |
| Como a plataforma compete no mercado | [product/competitive-analysis.md](product/competitive-analysis.md) |
| O que cada tela da UI faz | [Módulos funcionais](#módulos-funcionais-modulos) |
| Como um Arc/feature funciona internamente | [Arcos de implementação](#arcos-de-implementação-arcos) |
| Como um pacote funciona internamente | [Pacotes técnicos](#pacotes-técnicos-pacotes) |
| Como implementar um padrão (masking, mention, etc.) | [Guias temáticos](#guias-temáticos-guias) |
| Por que uma decisão arquitetural foi tomada | [ADRs](#adrs-adr) |
| Eventos Kafka e schemas | [kafka-eventos.md](kafka-eventos.md) |
| Modelos de dados e persistência | [modelos-de-dados.md](modelos-de-dados.md) |
| A arquitetura em camadas conceitual | [Camadas arquiteturais](#camadas-arquiteturais-layers) |

---

## Documentos transversais (raiz)

| Arquivo | Conteúdo |
|---|---|
| [visao-geral.md](visao-geral.md) | **Visão técnica consolidada** — percorre toda a plataforma com links para os docs detalhados |
| [modelos-de-dados.md](modelos-de-dados.md) | Schemas por camada de persistência + matriz de acesso por módulo |
| [kafka-eventos.md](kafka-eventos.md) | Tópicos Kafka, schemas de eventos, produtores e consumidores |
| [plughub_analise_competitiva_2026.md](plughub_analise_competitiva_2026.md) | Análise competitiva detalhada (abr/2026) |
| [revisao-documentacao-2026-05.md](revisao-documentacao-2026-05.md) | Relatório de avaliação do acervo de documentação |

---

## Produto (`product/`)

Documentação voltada ao público comercial, de gestão e a novos usuários.

| Arquivo | Conteúdo |
|---|---|
| [product/overview.md](product/overview.md) | O que é o PlugHub, proposta de valor, arquitetura em uma página |
| [product/target-audience.md](product/target-audience.md) | Perfis de público-alvo: gestores, supervisores, integradores, desenvolvedores |
| [product/value-proposition.md](product/value-proposition.md) | Diferenciais por papel, benefícios mensuráveis, casos de uso |
| [product/competitive-analysis.md](product/competitive-analysis.md) | Comparação com Gemini Enterprise, Agentforce, Genesys, NICE/Cognigy, Five9, Talkdesk, LangGraph, CrewAI, n8n |

---

## Módulos funcionais (`modulos/`)

Um arquivo por módulo da UI. Cobre o que cada módulo faz, suas abas, gates de acesso ABAC, APIs envolvidas e os pacotes de backend que o sustentam.

### Atendimento

| Arquivo | Rota UI | Roles | Descrição |
|---|---|---|---|
| [modulos/contatos.md](modulos/contatos.md) | `/contacts` | operator+ | Lista de contatos, aba Agentes, Monitor em tempo real, Análise |
| [modulos/agent-assist.md](modulos/agent-assist.md) | `/agent-assist` | operator+ | Console do agente humano: chat, RightPanel, orquestração de agentes IA |

### Automação

| Arquivo | Rota UI | Roles | Descrição |
|---|---|---|---|
| [modulos/workflow.md](modulos/workflow.md) | `/workflow/*` | operator+ | Editor de workflows, Monitor de instâncias, Calendar, Report |
| [modulos/agentflow.md](modulos/agentflow.md) | `/agent-flow/*` | admin+ | Editor YAML de SkillFlows, Monitor, Deploy lifecycle, @mention, Pool Hooks |
| [modulos/processos.md](modulos/processos.md) | `/agent-flow/processos` | operator+ | Jornadas (Arc 10/16) e Instâncias de workflow — monitoramento multi-sessão |

### Qualidade

| Arquivo | Rota UI | Roles | Descrição |
|---|---|---|---|
| [modulos/avaliacao.md](modulos/avaliacao.md) | `/evaluation/*` | operator+ | Formulários, campanhas, avaliação IA + RAG, contestação/revisão, calibração, curadoria |

### Configuração

| Arquivo | Rota UI | Roles | Descrição |
|---|---|---|---|
| [modulos/configuracao-recursos.md](modulos/configuracao-recursos.md) | `/config/resources` | admin | Pools, Agent Types, Skills, Instâncias, Canais, Agentes Humanos |
| [modulos/configuracao-plataforma.md](modulos/configuracao-plataforma.md) | `/config/platform` | admin | Namespaces de configuração via Config API |
| [modulos/mascaramento.md](modulos/mascaramento.md) | `/config/masking` | admin | Regras de mascaramento de dados sensíveis, audit capture, retenção |
| [modulos/controle-acesso.md](modulos/controle-acesso.md) | `/config/access` | admin | Usuários RBAC + ABAC, JWT, module_config |
| [modulos/grupos.md](modulos/grupos.md) | `/config/groups` | admin | Agent Groups, supervisores por turno, escopo de supervisor no JWT (Arc 9) |
| [modulos/dashboards.md](modulos/dashboards.md) | `/dashboards` | admin | DisplayTool registry, tipos de card, ENDPOINT_CATALOG, FilterBar |
| [modulos/faturamento.md](modulos/faturamento.md) | `/config/billing` | admin, business | Faturamento por capacidade: base + reserve pools |
| [modulos/relatorios-agentes.md](modulos/relatorios-agentes.md) | `/contacts/reports/agents` | supervisor+ | Disponibilidade e pausas de agentes humanos |

---

## Arcos de implementação (`arcos/`)

Documentação técnica detalhada por Arc ou componente: implementação, contratos internos, schemas de banco, eventos Kafka e decisões de design.

### Arcos de feature

| Arquivo | Conteúdo |
|---|---|
| [arcos/arc4-workflow.md](arcos/arc4-workflow.md) | Workflow, Calendar, Collect, Webhooks, Skill Deploy lifecycle |
| [arcos/arc5-segments.md](arcos/arc5-segments.md) | ContactSegment analytics, ClickHouse tables, endpoints de relatório |
| [arcos/arc6-evaluation.md](arcos/arc6-evaluation.md) | Quality Evaluation Platform (Forms, Campaigns, Contestação, RAG) |
| [arcos/arc6-phase2-observability.md](arcos/arc6-phase2-observability.md) | Observabilidade de mudanças e comparação por deploy epoch |
| [arcos/arc7-auth.md](arcos/arc7-auth.md) | Auth, RBAC, ABAC, performance routing, JWT |
| [arcos/arc8-agent-availability.md](arcos/arc8-agent-availability.md) | Disponibilidade e pausas de agentes humanos, pipeline ClickHouse |
| [arcos/arc9-agent-groups.md](arcos/arc9-agent-groups.md) | Agent Groups, Supervisor Scope, shift resolution, JWT claims |
| [arcos/arc10-journey.md](arcos/arc10-journey.md) | Journey multi-sessão, fases A–F, Kafka journey.events |
| [arcos/arc11-console-orchestration.md](arcos/arc11-console-orchestration.md) | Console como superfície de orquestração humana (fases A–D) |
| [arcos/arc11-phase2-console-redesign.md](arcos/arc11-phase2-console-redesign.md) | Redesign do Console (fases A–E) |
| [arcos/arc12-agent-business-events.md](arcos/arc12-agent-business-events.md) | Agent Business Events — tool `agent_event`, KPIs de negócio |
| [arcos/arc13-review-contestation.md](arcos/arc13-review-contestation.md) | Evaluation Review, Contestation & Calibration (fases A–H) |
| [arcos/arc14-posatt-independent-segments.md](arcos/arc14-posatt-independent-segments.md) | Segmentos independentes de pós-atendimento |
| [arcos/arc15-webrtc.md](arcos/arc15-webrtc.md) | Canal WebRTC com SFU enterprise (fases A–F) |
| [arcos/arc16-flow-orchestration.md](arcos/arc16-flow-orchestration.md) | Orquestração de processos em três camadas, channel capability negotiation |
| [arcos/audit-lgpd.md](arcos/audit-lgpd.md) | Audit LGPD — módulo ABAC `audit`, acesso DPO/compliance |

### Componentes e subsistemas

| Arquivo | Conteúdo |
|---|---|
| [arcos/instance-bootstrap.md](arcos/instance-bootstrap.md) | Reconciliação Kubernetes-style, RegistrySyncer, hot-reload |
| [arcos/platform-ui.md](arcos/platform-ui.md) | Frontend Architecture — design system, nav groups, ABAC, i18n |
| [arcos/ai-gateway.md](arcos/ai-gateway.md) | AI Gateway — multi-account rotation, AccountSelector, copilot |
| [arcos/usage-metering.md](arcos/usage-metering.md) | Usage Metering — dimensões, Redis quota, cycle reset |
| [arcos/pricing.md](arcos/pricing.md) | Pricing — faturamento por capacidade, base + reserve pools |
| [arcos/session-replayer.md](arcos/session-replayer.md) | Session Replayer — ensure-before-read, Hydrator, ReplayContext |
| [arcos/session-conference-lifecycle.md](arcos/session-conference-lifecycle.md) | Ciclo de vida de conferência — modelo de 3 camadas |
| [arcos/dashboard.md](arcos/dashboard.md) | Dashboard — DisplayTool registry, ENDPOINT_CATALOG, cards |
| [arcos/channel-gateway-multi-channel.md](arcos/channel-gateway-multi-channel.md) | Channel Gateway multi-canal — WhatsApp, SMS, Email, Voice |
| [arcos/evaluation-agents.md](arcos/evaluation-agents.md) | Agentes de avaliação — design de fluxos |

### Propostas e relatórios pontuais

| Arquivo | Conteúdo |
|---|---|
| [arcos/dialer-compliance-invariants.md](arcos/dialer-compliance-invariants.md) | **Proposta** — invariantes do compliance guard de discador (não implementado) |
| [arcos/journey-analytics.md](arcos/journey-analytics.md) | **Obsoleto** — proposta analítica superada pelo modelo real do Arc 10 |
| [arcos/task-30-contacts-restructure.md](arcos/task-30-contacts-restructure.md) | **Histórico** — design de reestruturação de nav (Task #30) |
| [arcos/design-system-audit.md](arcos/design-system-audit.md) | Relatório pontual — auditoria do design system (2026-05-18) |
| [arcos/accessibility-audit.md](arcos/accessibility-audit.md) | Relatório pontual — auditoria de acessibilidade WCAG 2.1 AA (2026-05-18) |

---

## Pacotes técnicos (`pacotes/`)

Um arquivo por pacote do monorepo: funcionamento interno, contratos, persistência e eventos.

| Arquivo | Pacote | Runtime |
|---|---|---|
| [pacotes/schemas.md](pacotes/schemas.md) | `@plughub/schemas` | Node 20+ |
| [pacotes/sdk.md](pacotes/sdk.md) | `@plughub/sdk` + `plughub-sdk` (Python) | Node / Python |
| [pacotes/mcp-server-plughub.md](pacotes/mcp-server-plughub.md) | `mcp-server-plughub` | Node 20+ |
| [pacotes/skill-flow-engine.md](pacotes/skill-flow-engine.md) | `@plughub/skill-flow` | Node 20+ |
| [pacotes/ai-gateway.md](pacotes/ai-gateway.md) | `ai-gateway` | Python 3.11+ |
| [pacotes/agent-registry.md](pacotes/agent-registry.md) | `agent-registry` | Node 20+ |
| [pacotes/routing-engine.md](pacotes/routing-engine.md) | `routing-engine` | Python 3.11+ |
| [pacotes/rules-engine.md](pacotes/rules-engine.md) | `rules-engine` | Python 3.11+ |
| [pacotes/channel-gateway.md](pacotes/channel-gateway.md) | `channel-gateway` | Python 3.11+ |
| [pacotes/channel-gateway-webchat.md](pacotes/channel-gateway-webchat.md) | `channel-gateway` (WebChat) | Python 3.11+ |
| [pacotes/auth-api.md](pacotes/auth-api.md) | `auth-api` | Python 3.11+ |
| [pacotes/platform-ui.md](pacotes/platform-ui.md) | `platform-ui` | React 18 + TypeScript |
| [pacotes/evaluation-agent.md](pacotes/evaluation-agent.md) | `evaluation-agent` | Python 3.11+ |
| [pacotes/clickhouse-consumer.md](pacotes/clickhouse-consumer.md) | `analytics-api` consumer | Python 3.11+ |
| [pacotes/conversation-writer.md](pacotes/conversation-writer.md) | stream persister | Python 3.11+ |

> **Pacotes sem doc própria em `pacotes/`:** `calendar-api`, `workflow-api`, `skill-flow-worker`, `pricing-api`, `evaluation-api`, `mcp-server-knowledge`, `analytics-api`, `orchestrator-bridge`. Cobertos parcialmente pelos docs de Arc correspondentes (`arcos/arc4-workflow.md`, `arcos/arc6-evaluation.md`, `arcos/pricing.md`). Criar entradas formais é uma ação de remediação pendente.

---

## Guias temáticos (`guias/`)

Padrões e workflows que cruzam múltiplos pacotes.

| Arquivo | Assunto |
|---|---|
| [guias/context-store.md](guias/context-store.md) | ContextStore — Redis hash por sessão, `@ctx.*`, `context_tags`, namespaces |
| [guias/context-store-taxonomy.md](guias/context-store-taxonomy.md) | Taxonomia de namespaces de contexto |
| [guias/context-masking-rules.md](guias/context-masking-rules.md) | Mascaramento dinâmico do ContextStore (`ContextMaskingRule`) |
| [guias/masked-input.md](guias/masked-input.md) | Masked Input — captura segura: `begin_transaction`, `@masked.*` |
| [guias/mention-protocol.md](guias/mention-protocol.md) | @mention — endereçamento de participantes, `mentionable_pools` |
| [guias/conference-mechanics.md](guias/conference-mechanics.md) | Mecanismo de conferência — Redis keys, eventos, posatt, teardown |
| [guias/pool-hooks.md](guias/pool-hooks.md) | Pool Lifecycle Hooks — `on_human_start`, `on_human_end`, `post_human` |
| [guias/orchestrator-working-memory.md](guias/orchestrator-working-memory.md) | Working memory para orquestradores em loop |
| [guias/abac-permission-system.md](guias/abac-permission-system.md) | Sistema ABAC — `makePermissions()`, `modules.yaml`, scope por pool |
| [guias/gitagent.md](guias/gitagent.md) | GitAgent — artefatos, certificação, regeneração, deploy |
| [guias/webhook-patterns.md](guias/webhook-patterns.md) | Webhooks — padrões trigger e resume; comportamento do step `collect` |
| [guias/timeouts-e-deteccao-de-falhas.md](guias/timeouts-e-deteccao-de-falhas.md) | Timeouts, CrashDetector, heartbeat, TTLs por componente |
| [guias/conferencia-agente-ia-mapeamento.md](guias/conferencia-agente-ia-mapeamento.md) | **Obsoleto** — mapeamento de gaps já implementados |

---

## ADRs (`adr/`)

Decisões arquiteturais com contexto, opções consideradas e consequências.

| Arquivo | Decisão |
|---|---|
| [adr/adr-ai-gateway-separation.md](adr/adr-ai-gateway-separation.md) | Separação do AI Gateway como serviço stateless |
| [adr/adr-contact-segments.md](adr/adr-contact-segments.md) | ContactSegment como entidade analítica de participação |
| [adr/adr-instance-bootstrap.md](adr/adr-instance-bootstrap.md) | Instance Bootstrap — reconciliação controlada (Kubernetes-style) |
| [adr/adr-message-masking.md](adr/adr-message-masking.md) | Mascaramento de mensagens com tokenização e partial display |
| [adr/adr-session-replayer.md](adr/adr-session-replayer.md) | Session Replayer — ensure-before-read com Hydrator opcional |
| [adr/adr-webchat-channel.md](adr/adr-webchat-channel.md) | WebChat — hybrid stream model, WebSocket tipado, upload dois estágios |

---

## Camadas arquiteturais (`layers/`)

Mapeamento conceitual das 9 camadas da plataforma para os pacotes do monorepo.

| Arquivo | Camada | Pacotes |
|---|---|---|
| [layers/01-channel-layer.md](layers/01-channel-layer.md) | Channel Layer | `channel-gateway` |
| [layers/02-gateway-layer.md](layers/02-gateway-layer.md) | Gateway Layer | `channel-gateway`, `ai-gateway` |
| [layers/03-message-bus.md](layers/03-message-bus.md) | Message Bus | Kafka |
| [layers/04-orchestration-layer.md](layers/04-orchestration-layer.md) | Orchestration Layer | `routing-engine`, `rules-engine`, `skill-flow-engine` |
| [layers/05-agent-layer.md](layers/05-agent-layer.md) | Agent Layer | `sdk`, agentes externos |
| [layers/06-mcp-layer.md](layers/06-mcp-layer.md) | MCP Layer | `mcp-server-plughub`, `mcp-server-knowledge`, domain MCP Servers |
| [layers/07-data-layer.md](layers/07-data-layer.md) | Data Layer | Redis, PostgreSQL, ClickHouse, Object Storage |
| [layers/08-mlops-layer.md](layers/08-mlops-layer.md) | MLOps Layer | fora do repositório (Horizonte 1) |
| [layers/09-observability-layer.md](layers/09-observability-layer.md) | Observability Layer | ferramentas externas |

---

## Padrões de desenvolvimento (`standards/`)

| Arquivo | Conteúdo |
|---|---|
| [standards/frontend-architecture.md](standards/frontend-architecture.md) | Design system, módulos, componentes, anti-padrões, i18n, autenticação |

---

## Referência histórica (`sections/`)

Seções extraídas da especificação técnica v24.0 original. Mantidas apenas para consulta — a documentação viva está nas seções acima.

| Arquivo | Conteúdo |
|---|---|
| [sections/spec_completa.md](sections/spec_completa.md) | Spec técnica v24.0 completa em markdown |
| [sections/INDEX.md](sections/INDEX.md) | Índice das seções extraídas |
| [sections/conferencia-e-historico.md](sections/conferencia-e-historico.md) | Rascunho v25.0 — conferência unificada (superado por `guias/conference-mechanics.md`) |
| [sections/3.2-rules-engine.md](sections/3.2-rules-engine.md) | Seção 3.2 — Rules Engine |
| [sections/3.3-routing-engine.md](sections/3.3-routing-engine.md) | Seção 3.3 — Routing Engine |
| [sections/3.4-context-package.md](sections/3.4-context-package.md) | Seção 3.4 — Context Package |
| [sections/4.2-contrato-execucao.md](sections/4.2-contrato-execucao.md) | Seção 4.2 — Contrato de Execução |
| [sections/4.5-agent-registry.md](sections/4.5-agent-registry.md) | Seção 4.5 — Agent Registry |
| [sections/4.6-sdk.md](sections/4.6-sdk.md) | Seção 4.6 — SDK |
| [sections/4.7-skill-registry.md](sections/4.7-skill-registry.md) | Seção 4.7 — Skill Registry |
| [sections/9.4-agent-runtime-tools.md](sections/9.4-agent-runtime-tools.md) | Seção 9.4 — Agent Runtime Tools |
| [sections/9.5-a2a-protocol.md](sections/9.5-a2a-protocol.md) | Seção 9.5 — A2A Protocol |
| [sections/10-evaluation.md](sections/10-evaluation.md) | Seção 10 — Evaluation |
| [sections/14-multi-tenant.md](sections/14-multi-tenant.md) | Seção 14 — Multi-tenant |

---

## Deprecated (`deprecated/`)

Arquivos supersedidos mantidos apenas como referência histórica. Não consulte para implementação.

| Arquivo | Motivo de deprecação |
|---|---|
| [deprecated/modulos/agent-assist-piloto.md](deprecated/modulos/agent-assist-piloto.md) | Design do piloto — substituído pelo Agent Assist atual |
| [deprecated/modulos/dashboard-piloto.md](deprecated/modulos/dashboard-piloto.md) | Dashboard do piloto — substituído pelo módulo Dashboards atual |
| [deprecated/modulos/evaluation.md](deprecated/modulos/evaluation.md) | Stub inicial — substituído por [modulos/avaliacao.md](modulos/avaliacao.md) |
| [deprecated/standards/operator-console-migration.md](deprecated/standards/operator-console-migration.md) | Plano de migração — `operator-console` removido (migração concluída) |
| [deprecated/sections/visao_negocial.md](deprecated/sections/visao_negocial.md) | Substituído por [product/overview.md](product/overview.md) |
| [deprecated/sections/visao_negocial_v24.md](deprecated/sections/visao_negocial_v24.md) | Substituído por [product/](product/overview.md) |
| [pacotes/notification-agent.md](pacotes/notification-agent.md) | Pacote nunca implementado — `notify` depreciado no Arc 16 |
| [guias/changelog-2026-04-15.md](guias/changelog-2026-04-15.md) | Changelog histórico pré-`CHANGELOG.md` |
| [guias/changelog-2026-04-16.md](guias/changelog-2026-04-16.md) | Changelog histórico pré-`CHANGELOG.md` |
| [guias/changelog-2026-04-16b.md](guias/changelog-2026-04-16b.md) | Changelog histórico pré-`CHANGELOG.md` |
| [guias/changelog-2026-04-29.md](guias/changelog-2026-04-29.md) | Changelog histórico pré-`CHANGELOG.md` |
