# PlugHub — Visão Geral da Plataforma

> Versão de referência: spec v24.0 (Março 2026)
> Última atualização desta documentação: 2026-03-31

## O que é o PlugHub

O PlugHub é um **AI/Human Agent Hub** — uma camada de controle centralizada que conecta, orquestra, governa e avalia agentes de IA e humanos, de qualquer origem, sobre um único núcleo operacional. Não é um agente nem um framework: é a infraestrutura que torna a entrega de serviço possível com qualidade mensurável e sem lock-in.

A plataforma opera em quatro dimensões complementares:

**Acesso omnichannel.** Abstrai e normaliza todos os canais de comunicação com o cliente — WhatsApp, SMS, Chat Web/App, Email, Voz e WebRTC — entregando ao interior um envelope de evento uniforme independentemente do canal de origem. Nenhum agente precisa conhecer o protocolo do canal; a plataforma cuida da janela de 24h do WhatsApp, do threading de email, do streaming de voz via STT e da coleta sequencial de menus em canais sem suporte nativo.

**Orquestração de agentes.** Gerencia o ciclo de vida de agentes humanos e IA — de qualquer framework — decide quem atende cada conversa, executa Skill Flows declarativos, monitora parâmetros de sessão em tempo real e aciona escalações automáticas. Agentes nativos via SDK, agentes externos (LangGraph, CrewAI, Azure AI Agents) via proxy sidecar e agentes humanos via interface assistida coexistem no mesmo pool com o mesmo contrato de execução.

**Integração com sistemas e processos externos.** Via MCP, a plataforma se conecta bidirecionalmente ao ecossistema do tenant: BPMs e orquestradores externos (Camunda, Pega, IBM BPM) acionam flows da plataforma e recebem outcomes de volta, sem precisar recriar o processo interno; agentes acessam sistemas de negócio (CRM, ERP, cobrança) exclusivamente via domain MCP Servers, com autorização granular e auditoria em cada chamada.

**Avaliação e qualidade nativas.** Cada atendimento é avaliado por um Agente Avaliador com templates configuráveis por pool — os mesmos critérios aplicados a agentes de IA de qualquer framework e a agentes humanos. Qualidade não é um add-on analítico: é parte da operação desde o primeiro dia, sobre os dados que a plataforma já produz.

---

## Princípios Arquiteturais

**Event-driven first.** Toda comunicação entre componentes passa pelo Kafka. Nenhum componente chama outro diretamente de forma síncrona, exceto onde latência é crítica.

**Stateless por padrão.** Agentes IA, gateways e roteadores são stateless por padrão. Estado vive no Redis e no Kafka, não nos processos. Agentes que mantêm estado interno entre turnos (ex: LangGraph com state graph persistido na instância) declaram `execution_model: stateful` no Agent Registry — o Routing Engine garante afinidade de sessão para esses agentes, roteando sempre para a mesma instância durante toda a conversa. Agentes `stateless` são intercambiáveis: qualquer instância atende, e o `context_package` completo é entregue a cada turno para reconstrução de contexto.

**Degradação graciosa.** Cada componente tem comportamento definido em caso de falha dos seus dependentes. Não há falha catastrófica.

**Canal-aware.** O contexto do canal trafega com cada evento. Nenhum componente ignora as restrições físicas do canal de origem.

**Menor privilégio.** Agentes IA acessam sistemas de negócio exclusivamente via MCP Servers, com autorização granular por tipo de agente.

**Observabilidade nativa.** Toda decisão de agente, handoff e ação em sistema de negócio é rastreável por design.

---

## Invariantes — nunca violar

| Invariante | Consequência se violado |
|---|---|
| AI Gateway é stateless — processa um turno por chamada LLM | Inconsistência de parâmetros de sessão entre turnos |
| Routing Engine é o único árbitro de alocação | Conversas roteadas fora do audit log |
| MCP é o único protocolo de integração entre componentes internos | Chamadas diretas REST sem autorização nem audit |
| `pipeline_state` persiste no Redis a cada transição de step | Perda de estado em falha do orquestrador |
| Contrato do agente: `agent_login` → `agent_ready` → `agent_busy` → `agent_done` | Instâncias fantasma no pool, conversas abertas indefinidamente |
| `agent_done` requer `handoff_reason` quando `outcome !== "resolved"` | Analytics e escalações sem contexto de motivo |
| `issue_status` é sempre obrigatório e nunca vazio no `agent_done` | Falha no Agent Quality Score, relatórios de qualidade sem dados |
| Agentes nunca acessam sistemas backend diretamente — apenas via MCP Servers autorizados | Ações não auditadas, sem controle de permissão por tenant |
| Todas as chamadas MCP de domínio são interceptadas (PlugHubAdapter ou proxy sidecar) | Chamadas a domain MCP Servers sem validação de permissões |
| `insight.historico.*` persiste via Kafka, nunca por escrita direta no PostgreSQL | Dados de insight sem consumer de consolidação, granularidade de persistência violada |

---

## Camadas da Arquitetura

A plataforma é organizada em 9 camadas com responsabilidades distintas. Veja `docs/layers/` para a documentação detalhada de cada uma.

| # | Camada | Responsabilidade | Módulos no repositório |
|---|---|---|---|
| 1 | [Channel Layer](layers/01-channel-layer.md) | Abstração e normalização de canais: WhatsApp, SMS, Chat Web/App, Email, Voz | `channel-gateway` |
| 2 | [Gateway Layer](layers/02-gateway-layer.md) | Voice Gateway, STT Router, Channel Normalizer | `channel-gateway` (normalização), componentes de voz¹ |
| 3 | [Message Bus](layers/03-message-bus.md) | Apache Kafka — backbone de eventos assíncrono desacoplado | infraestrutura (`docker-compose.infra.yml`) |
| 4 | [Orchestration Layer](layers/04-orchestration-layer.md) | Routing Engine, Motor de Regras, Escalation Engine, AI Gateway | `routing-engine`, `rules-engine`, `skill-flow-engine`, `ai-gateway` |
| 5 | [Agent Layer](layers/05-agent-layer.md) | Pool de agentes IA especializados + interface de agentes humanos | `sdk`, agentes externos |
| 6 | [MCP Layer](layers/06-mcp-layer.md) | MCP Servers por domínio com autorização granular | `mcp-server-plughub`, domain MCP Servers² |
| 7 | [Data Layer](layers/07-data-layer.md) | Redis, PostgreSQL+pgvector, ClickHouse, Object Storage | infraestrutura |
| 8 | [MLOps Layer](layers/08-mlops-layer.md) | Fine-tuning STT, retraining de agentes, Model Registry | fora do repositório no Horizonte 1 |
| 9 | [Observability Layer](layers/09-observability-layer.md) | LangSmith/Langfuse, Prometheus, OpenTelemetry, Superset, dbt | ferramentas externas |

> ¹ Componentes de voz (Voice Gateway, STT Router) são desenvolvidos fora deste repositório no Horizonte 1.
> ² Domain MCP Servers (`mcp-server-crm`, `mcp-server-telco`, etc.) são operados pelo tenant e não fazem parte deste repositório.

---

## Pacotes do Repositório

O repositório é organizado como monorepo em `packages/`. Cada pacote tem responsabilidade única e dependências explicitamente declaradas.

| Pacote | Nome npm/pip | Runtime | Documentação |
|---|---|---|---|
| `schemas` | `@plughub/schemas` | Node 20+ | [modulos/schemas.md](modulos/schemas.md) |
| `sdk` | `@plughub/sdk` (TS) + `plughub-sdk` (Python) | Node 20+ / Python 3.11+ | [modulos/sdk.md](modulos/sdk.md) |
| `mcp-server-plughub` | — (serviço) | Node 20+ | [modulos/mcp-server-plughub.md](modulos/mcp-server-plughub.md) |
| `skill-flow-engine` | `@plughub/skill-flow` | Node 20+ | [modulos/skill-flow-engine.md](modulos/skill-flow-engine.md) |
| `ai-gateway` | — (serviço) | Python 3.11+ | [modulos/ai-gateway.md](modulos/ai-gateway.md) |
| `agent-registry` | — (serviço) | Node 20+ | [modulos/agent-registry.md](modulos/agent-registry.md) |
| `routing-engine` | — (serviço) | Python 3.11+ | [modulos/routing-engine.md](modulos/routing-engine.md) |
| `rules-engine` | — (serviço) | Python 3.11+ | [modulos/rules-engine.md](modulos/rules-engine.md) |
| `channel-gateway` | — (serviço) | Python 3.11+ | [modulos/channel-gateway.md](modulos/channel-gateway.md) |

### Grafo de dependências

```
schemas         ← base — nenhuma dependência interna
sdk             ← schemas
mcp-server      ← schemas
skill-flow      ← schemas, mcp-server
ai-gateway      ← schemas
agent-registry  ← schemas
routing-engine  ← schemas, agent-registry
rules-engine    ← schemas, routing-engine
channel-gateway ← schemas
```

**Regras de dependência que nunca devem ser violadas:**
- `schemas` nunca depende de nenhum outro pacote
- Pacotes TypeScript nunca dependem de `ai-gateway` (Python)
- Nunca criar dependências circulares
- Nunca redefinir localmente tipos já presentes em `@plughub/schemas`

---

## Fluxo de uma Conversa

O diagrama abaixo descreve o ciclo de vida completo de uma conversa, desde a entrada no canal até o encerramento.

```
Canal (WhatsApp / SMS / Chat / Voz / Email)
         │
         ▼
  Channel Gateway                  ← normaliza evento inbound
  publica em conversations.inbound
         │
         ▼
  Routing Engine                   ← decide quem atende (150ms timeout)
  aloca agente do pool adequado
  cria pipeline_state no Redis
         │
         ├─── agente IA ───────────────────────────────────────────┐
         │    (stateless ou stateful)                              │
         │    ↓                                                    │
         │    agent_login → agent_ready → agent_busy              │
         │    ↓                                                    │
         │    Executa via SDK / PlugHubAdapter                     │
         │    Chama domain MCP Servers (interceptados)             │
         │    AI Gateway para raciocínio LLM                       │
         │    ↓                                                    │
         │    agent_done (outcome + issue_status)                  │
         │                                                         │
         └─── agente humano ───────────────────────────────────────┘
              Agent Assist UI
              Supervisor Agent (via mcp-server-plughub)
         │
         ▼
  conversations.events (Kafka)     ← conversation_completed publicado
         │
         ▼
  Rules Engine                     ← avalia regras do tenant
  (pode acionar Escalation Engine antes do encerramento)
         │
         ▼
  Evaluation Agent                 ← Agent Quality Score
  Kafka consumer                   ← promove insight.conversa.* → insight.historico.*
```

---

## Contrato de Execução do Agente

Todo agente — independente de framework, linguagem ou ser humano ou IA — deve aderir ao contrato de execução para participar de um pool. O contrato completo está documentado em [modulos/mcp-server-plughub.md](modulos/mcp-server-plughub.md).

**Resumo do contrato:**

1. Recebe `context_package` com `channel_context`, `customer_data`, `conversation_history` e `process_context`
2. Declara `agent_type_id` com `execution_model` (`stateless` ou `stateful`)
3. Acessa sistemas de negócio exclusivamente via MCP Servers autorizados
4. Encerra com `agent_done` informando `outcome`, `issue_status` (obrigatório) e `handoff_reason` (obrigatório quando `outcome !== "resolved"`)

**Estados válidos de `outcome`:**

| Outcome | Quando usar |
|---|---|
| `resolved` | Conversa encerrada com sucesso pelo agente |
| `escalated_human` | Transferência para pool humano |
| `transferred_agent` | Transferência para outro agente IA |
| `callback` | Retorno agendado (exclusivo para agentes outbound) |

---

## Interceptação de Chamadas MCP (Modelo Híbrido de Proxy)

Domain MCP Servers (`mcp-server-crm`, `mcp-server-telco`, etc.) são operados pelo tenant. Todas as chamadas a eles são interceptadas para validação de permissões e auditoria, independente do tipo de agente:

| Tipo de agente | Mecanismo de interceptação | Salto de rede |
|---|---|---|
| Agente nativo (usa SDK) | PlugHubAdapter in-process | Nenhum |
| Agente externo (LangGraph, CrewAI) | `plughub-sdk proxy` sidecar em localhost:7422 | Somente loopback |
| GitAgent (output de `regenerate`) | PlugHubAdapter in-process (código gerado) | Nenhum |

O proxy sidecar valida `permissions[]` do JWT da sessão localmente (~0,1ms) e grava eventos de auditoria assincronamente para um buffer local drenado por thread de background para o Kafka. Overhead total por chamada MCP: **< 1ms**.

---

## Topologia de Persistência

| Banco | Uso principal | Responsáveis por escrever |
|---|---|---|
| **Redis Cluster** | Estado de conversa em tempo real, instâncias de agente, filas de pool, heartbeats, `pipeline_state`, locks de execução | `mcp-server-plughub`, `routing-engine`, `skill-flow-engine`, `ai-gateway` |
| **PostgreSQL + pgvector** | Registro de tipos de agente, pools, skills, histórico de conversas, base de conhecimento vetorial | `agent-registry` |
| **ClickHouse** | Analytics operacional, audit log de chamadas MCP, métricas de qualidade de agentes | consumers Kafka, `rules-engine` |
| **Object Storage (S3/GCS)** | Áudio de ligações, datasets de fine-tuning, versões de modelos STT | componentes de voz, MLOps |

Ver [modelos-de-dados.md](modelos-de-dados.md) para schemas completos e matriz de acesso.

---

## Tópicos Kafka Principais

| Tópico | Conteúdo |
|---|---|
| `conversations.inbound` | Eventos de entrada normalizados de todos os canais |
| `conversations.routed` | Eventos de alocação de agente pelo Routing Engine |
| `conversations.queued` | Conversas aguardando agente disponível |
| `conversations.events` | Eventos de ciclo de vida: handoffs, escalações, conclusões, insights |
| `agent.lifecycle` | Estados das instâncias de agente: login, ready, busy, done, logout |
| `agent.registry.events` | Alterações de configuração no Agent Registry |
| `rules.escalation.events` | Escalações acionadas pelo Rules Engine |
| `rules.shadow.events` | Registros de shadow mode para análise antes de ativação de regras |

Ver [kafka-eventos.md](kafka-eventos.md) para schemas completos de cada evento.

---

## Skill Flow — Orquestração Declarativa

Agentes com `role: orchestrator` executam pipelines de coordenação declarados no campo `flow` de uma skill de orquestração. O flow combina 9 tipos de step, cada um com semântica e mecanismo de execução próprios.

| Type | O que faz | Mecanismo |
|---|---|---|
| `task` | Delega subtarefa a agente com a skill via A2A | Routing Engine via `agent_delegate` |
| `choice` | Ramificação condicional via JSONPath | Avaliado pelo engine localmente |
| `catch` | Retry e fallback antes de escalação | Avaliado pelo engine localmente |
| `escalate` | Roteia para pool via Rules Engine | Rules Engine |
| `complete` | Encerra pipeline com outcome definido | `agent_done` |
| `invoke` | Chama tool MCP diretamente | MCP Server |
| `reason` | Invoca AI Gateway com output_schema | AI Gateway |
| `notify` | Envia mensagem ao cliente (unidirecional) | Notification Agent |
| `menu` | Captura input do cliente e suspende até resposta | Channel Gateway via Notification Agent |

Documentação completa em [modulos/skill-flow-engine.md](modulos/skill-flow-engine.md).

---

## Convenções de Nomenclatura

```
skill_id:       skill_{name}_v{n}      →  skill_portabilidade_telco_v2
agent_type_id:  {name}_v{n}            →  agente_retencao_v1
pool_id:        snake_case sem versão  →  retencao_humano
mcp_server:     mcp-server-{name}      →  mcp-server-crm
tool:           snake_case             →  customer_get
insight:        insight.historico.*   →  memória de longo prazo do cliente
                insight.conversa.*    →  gerado na sessão atual, expira no fechamento
outbound:       outbound.*            →  entregas pendentes para Notification Agent
```

---

## CLI do SDK

```bash
plughub-sdk certify            # valida contrato de execução do agente
plughub-sdk verify-portability # verifica isolamento de dependências
plughub-sdk regenerate         # regenera agente proprietário como nativo
plughub-sdk skill-extract      # extrai skill a partir de agente existente
```

---

## O que nunca fazer

- Criar componente que roteia conversas sem passar pelo Routing Engine
- Acessar o Redis diretamente de fora do `routing-engine` ou do `skill-flow-engine`
- Redefinir tipos de `@plughub/schemas` localmente em outro pacote
- Adicionar lógica de negócio ao `mcp-server-plughub` — ele só expõe tools
- Criar dependência de `ai-gateway` em pacotes TypeScript — somente Python o consome
- Usar `export *` em pacotes — sempre exports nomeados explícitos
- Implementar lógica de renderização canal-específica fora do `channel-gateway`
- Colocar validação de negócio dentro do step `menu` — validação pertence a steps subsequentes

---

## Referência de Documentação

| Documento | Conteúdo |
|---|---|
| [visao-geral.md](visao-geral.md) | Este arquivo |
| [modelos-de-dados.md](modelos-de-dados.md) | Schemas de persistência e matriz de acesso por módulo |
| [kafka-eventos.md](kafka-eventos.md) | Tópicos Kafka, schemas de eventos, produtores e consumidores |
| [layers/](layers/) | Documentação das 9 camadas arquiteturais |
| [modulos/](modulos/) | Documentação detalhada de cada um dos 9 pacotes |
| [sections/spec_completa.md](sections/spec_completa.md) | Spec técnica v24.0 completa (fonte histórica) |
