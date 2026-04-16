# PlugHub — Documentação Técnica

> Spec de referência: v24.0 · Última atualização: 2026-04-16

---

## Documentos transversais

| Arquivo | Conteúdo | Status |
|---|---|---|
| [visao-geral.md](visao-geral.md) | Arquitetura geral, invariantes, fluxo de uma conversa, contrato de execução, persistência, Kafka, convenções | ✅ |
| [modelos-de-dados.md](modelos-de-dados.md) | Schemas de cada estrutura por camada de persistência + matriz de acesso por módulo | ✅ |
| [kafka-eventos.md](kafka-eventos.md) | Tópicos Kafka, schemas completos de eventos, produtores e consumidores | ✅ |

---

## Camadas arquiteturais (`layers/`)

Mapeamento conceitual das 9 camadas da plataforma para os módulos do repositório.

| Arquivo | Camada | Módulos | Status |
|---|---|---|---|
| [layers/01-channel-layer.md](layers/01-channel-layer.md) | Channel Layer | `channel-gateway` | ✅ |
| [layers/02-gateway-layer.md](layers/02-gateway-layer.md) | Gateway Layer | `channel-gateway`, componentes de voz¹, `ai-gateway` | ✅ |
| [layers/03-message-bus.md](layers/03-message-bus.md) | Message Bus | Kafka (`docker-compose.infra.yml`) | ✅ |
| [layers/04-orchestration-layer.md](layers/04-orchestration-layer.md) | Orchestration Layer | `routing-engine`, `rules-engine`, `skill-flow-engine`, `ai-gateway` | ✅ |
| [layers/05-agent-layer.md](layers/05-agent-layer.md) | Agent Layer | `sdk`, agentes externos, `notification-agent`, `agent-assist` | ✅ |
| [layers/06-mcp-layer.md](layers/06-mcp-layer.md) | MCP Layer | `mcp-server-plughub`, domain MCP Servers² | ✅ |
| [layers/07-data-layer.md](layers/07-data-layer.md) | Data Layer | Redis, PostgreSQL, ClickHouse, Object Storage | ✅ |
| [layers/08-mlops-layer.md](layers/08-mlops-layer.md) | MLOps Layer | fora do repositório no Horizonte 1 | ✅ |
| [layers/09-observability-layer.md](layers/09-observability-layer.md) | Observability Layer | ferramentas externas | ✅ |

> ¹ Voice Gateway e STT Router são desenvolvidos fora deste repositório no Horizonte 1.
> ² Domain MCP Servers (`mcp-server-crm`, `mcp-server-telco`, etc.) são operados pelo tenant.

---

## Módulos (`modulos/`)

Um arquivo por pacote do monorepo. Cobre funcionamento interno, contratos, persistência, eventos e relação com outros módulos.

| Arquivo | Pacote | Runtime | Status |
|---|---|---|---|
| [modulos/schemas.md](modulos/schemas.md) | `@plughub/schemas` | Node 20+ | ✅ |
| [modulos/sdk.md](modulos/sdk.md) | `@plughub/sdk` + `plughub-sdk` (Python) | Node 20+ / Python 3.11+ | ✅ |
| [modulos/mcp-server-plughub.md](modulos/mcp-server-plughub.md) | `mcp-server-plughub` | Node 20+ | ✅ |
| [modulos/skill-flow-engine.md](modulos/skill-flow-engine.md) | `@plughub/skill-flow` | Node 20+ | ✅ |
| [modulos/ai-gateway.md](modulos/ai-gateway.md) | `ai-gateway` | Python 3.11+ | ✅ |
| [modulos/agent-registry.md](modulos/agent-registry.md) | `agent-registry` | Node 20+ | ✅ |
| [modulos/routing-engine.md](modulos/routing-engine.md) | `routing-engine` | Python 3.11+ | ✅ |
| [modulos/rules-engine.md](modulos/rules-engine.md) | `rules-engine` | Python 3.11+ | ✅ |
| [modulos/channel-gateway.md](modulos/channel-gateway.md) | `channel-gateway` | Python 3.11+ | ✅ |
| [modulos/notification-agent.md](modulos/notification-agent.md) | `notification-agent` | Python 3.11+ (GitAgent) | ✅ |
| [modulos/agent-assist.md](modulos/agent-assist.md) | `agent-assist` | cliente de mcp-server-plughub | ✅ |
| [modulos/evaluation.md](modulos/evaluation.md) | `evaluation` *(Horizonte 2)* | a definir | 📋 stub |

---

## Guias temáticos (`guias/`)

Documentação de workflows e padrões que cruzam múltiplos módulos.

| Arquivo | Assunto | Módulos envolvidos | Status |
|---|---|---|---|
| [guias/gitagent.md](guias/gitagent.md) | GitAgent — ciclo de vida completo: estrutura do repositório, artefatos, certificação, regeneração, registro e deploy | `sdk`, `schemas`, `skill-flow-engine`, `agent-registry` | ✅ |
| [guias/changelog-2026-04-15.md](guias/changelog-2026-04-15.md) | Changelog 2026-04-15 — ferramentas MCP BPM/supervisor, fila de agentes humanos, Queue Agent Pattern, pool_id via URL | `mcp-server`, `routing-engine`, `ai-gateway`, `agent-assist-ui`, `orchestrator-bridge`, `schemas`, `agent-registry`, `skill-flow-engine`, `channel-gateway` | ✅ |
| [guias/changelog-2026-04-16.md](guias/changelog-2026-04-16.md) | Changelog 2026-04-16 — integração end-to-end framework external-mcp (spec 4.6k): roteamento, heartbeats, wait_for_message, pool_config TTL | `mcp-server-plughub`, `routing-engine`, `agent-registry` | ✅ |

---

## Grafo de dependências entre módulos

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

---

## Legenda

| Símbolo | Significado |
|---|---|
| ✅ | Documento completo e revisado |
| 📋 stub | Estrutura criada, conteúdo a ser preenchido |

---

## Referência histórica

A spec técnica original e as seções extraídas para consulta permanecem em `sections/` como referência. A documentação viva é esta pasta.

| Arquivo | Conteúdo |
|---|---|
| `sections/spec_completa.md` | Spec técnica v24.0 completa em markdown |
| `sections/INDEX.md` | Índice das seções extraídas da spec |
| `spec_omnichannel_tecnico_v24.docx` | Documento original (fonte histórica) |
