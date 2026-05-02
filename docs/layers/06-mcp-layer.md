# Layer 6 — MCP Layer

> Spec de referência: v24.0 seções 9.4, 9.5, 4.6 (spec 4.6k strategy section 11)
> Responsabilidade: protocolo único de integração — expõe ferramentas de negócio aos agentes com autorização granular e auditoria em todas as chamadas
> Implementado por: `mcp-server-plughub` (Agent Runtime e BPM tools), domain MCP Servers operados pelo tenant

---

## Visão geral

MCP é o único protocolo de integração da plataforma. Nenhum componente acessa sistemas de negócio diretamente — todo acesso ocorre via MCP tools autorizadas.

A MCP Layer tem dois grupos distintos:

**`mcp-server-plughub`** — Agent Runtime tools (ciclo de vida do agente, insight, Supervisor) e BPM tools. Operado pela plataforma.

**Domain MCP Servers** (`mcp-server-crm`, `mcp-server-telco`, etc.) — ferramentas de domínio de negócio específicas do tenant. Operados pelo tenant.

Toda chamada a um domain MCP Server é **interceptada** para validação de permissões e auditoria — independentemente de como o agente está integrado.

---

## Modelo de interceptação (spec 4.6k, strategy section 11)

| Tipo de agente | Mecanismo de interceptação | Hop de rede |
|---|---|---|
| Agente nativo (usa SDK) | `PlugHubAdapter` em-processo | Nenhum |
| Agente externo (LangGraph, CrewAI) | `plughub-sdk proxy` sidecar em `localhost:7422` | Loopback apenas |
| GitAgent (output de `regenerate`) | `PlugHubAdapter` em-processo (código gerado) | Nenhum |

O proxy sidecar valida `permissions[]` do JWT do `session_token` localmente (sem chamada de rede, ~0,1ms) e registra eventos de auditoria de forma assíncrona em buffer local drenado para Kafka. **Overhead total por chamada MCP: < 1ms.** Viável em deployments SaaS multi-site.

---

## Componentes

### `mcp-server-plughub` (TypeScript, Node 20+)

MCP Server da plataforma. Não contém lógica de negócio — apenas expõe ferramentas e delega ao Routing Engine, Rules Engine e persistência.

**Agent Runtime tools** (contrato de execução do agente):

| Tool | Descrição |
|---|---|
| `agent_login` | Inicia sessão do agente, recebe `context_package` |
| `agent_ready` | Sinaliza disponibilidade para receber conversas |
| `agent_done` | Conclui atendimento com `outcome`, `issue_status`, `handoff_reason` |
| `insight_register` | Registra fato objetivo identificado durante a conversa em `insight.conversa.*` |

**Supervisor tools** (Agent Assist):

| Tool | Descrição |
|---|---|
| `supervisor_state` | Estado atual da conversa (sentiment, intent, flags, SLA, insights) |
| `supervisor_capabilities` | Capacidades disponíveis filtradas pelo intent e pool |
| `agent_join_conference` | Aciona agente IA como participante de conferência |

**BPM tools** (Skill Flow `invoke` step):

- Tools para controle de fluxo, timers, callbacks e integração com processos BPM externos

### Domain MCP Servers (tenant)

Exemplos: `mcp-server-crm`, `mcp-server-telco`, `mcp-server-cobranca`. Operados e mantidos pelo tenant. A plataforma não conhece a implementação interna — apenas intercepta e valida as chamadas.

Cada domain MCP Server declara suas tools com `permissions[]` que são validados pelo interceptador antes de deixar a chamada chegar ao servidor.

---

## Interfaces

**Entrada:**
- Chamadas MCP de agentes (via PlugHubAdapter ou proxy sidecar)
- Chamadas MCP do Skill Flow Engine (step `invoke`)

**Saída:**
- Respostas das tools ao agente ou ao Skill Flow
- Eventos de auditoria em Kafka (via buffer assíncrono do interceptador)
- Escritas em Redis (via Agent Runtime tools: `agent_done`, `insight_register`)

**Permissões:**
- Declaradas no JWT do `session_token` como `permissions[]`
- Validadas localmente pelo PlugHubAdapter ou proxy sidecar — sem chamada de rede
- Sem permissão: a chamada é bloqueada e auditada; o agente recebe erro

---

## Fluxo de dados

**Agente nativo chama tool:**
```
Agente → PlugHubAdapter (em-processo)
↓ valida permissions[] do session_token (local, ~0.1ms)
↓ registra evento de auditoria (assíncrono, buffer → Kafka)
↓ encaminha para domain MCP Server
↓ retorna resultado ao agente
```

**Agente externo chama tool:**
```
Agente externo → localhost:7422 (proxy sidecar)
↓ valida permissions[] do session_token (local, ~0.1ms)
↓ registra evento de auditoria (assíncrono, buffer → Kafka)
↓ encaminha para domain MCP Server (rede interna)
↓ retorna resultado ao agente externo
```

**Skill Flow Engine (step `invoke`):**
```
Skill Flow → mcp-server-plughub (tool BPM)
↓ executa ação declarada no step
↓ retorna resultado → pipeline_state atualizado
```

---

## Considerações operacionais

**Agentes nunca acessam backends diretamente.** Esta é uma invariante da plataforma. Qualquer chamada a um sistema de negócio sem passar pela MCP Layer viola o modelo de segurança e auditoria.

**`mcp-server-plughub` não tem lógica de negócio.** Apenas expõe ferramentas. Lógica de roteamento fica no Routing Engine; lógica de escalação no Rules Engine.

**Auditoria completa:** toda chamada MCP interceptada gera um evento de auditoria, mesmo as que são bloqueadas por falta de permissão. O `audit_id` está sempre presente no retorno de erro.

**Circuit breaker:** domain MCP Servers com falhas acumuladas têm o circuit breaker local aberto. O Agent Assist exibe o estado do circuit breaker para ferramentas sugeridas — o agente humano não tenta acionar uma tool com circuit breaker aberto.

**Export `*` proibido:** todos os pacotes TypeScript usam exports nomeados explícitos — nunca `export *`.

---

## Referência spec

- Seção 9.4 — Agent Runtime tools e Supervisor tools
- Seção 9.5 — Protocolo A2A
- Seção 4.6 (spec 4.6k, strategy section 11) — MCP interception (hybrid proxy model)
- [modulos/mcp-server-plughub.md](../modulos/mcp-server-plughub.md)
- [modulos/sdk.md](../modulos/sdk.md) — PlugHubAdapter e proxy sidecar
