# Layer 5 — Agent Layer

> Spec de referência: v24.0 seções 4.2, 4.6, 4.7, 8.3, 3.2a
> Responsabilidade: execução dos atendimentos — agentes IA especializados, agentes externos, agentes humanos assistidos pelo Agent Assist
> Implementado por: `sdk`, GitAgents, agentes externos (LangGraph, CrewAI), interface de agente humano

---

## Visão geral

A Agent Layer é onde o atendimento acontece. Ela é composta por todos os agentes — IA e humanos — que executam as conversas alocadas pelo Routing Engine.

A plataforma não impõe uma implementação específica de agente. Qualquer sistema que respeite o contrato de execução (seção 4.2) pode participar: agentes nativos via SDK, agentes externos LangGraph/CrewAI via proxy sidecar, e agentes humanos via interface de atendimento assistida pelo Agent Assist.

Todos os agentes, independentemente da origem, **acessam sistemas de negócio exclusivamente via MCP Layer** — nunca diretamente.

---

## Tipos de agente

### Agentes IA nativos (GitAgents)

Agentes versionados em repositório Git com estrutura padronizada. Lógica declarada em flow YAML convertido para JSON no registro. Usam o SDK (`@plughub/sdk` / `plughub-sdk` Python) com `PlugHubAdapter` em-processo para interceptação MCP.

Agentes nativos da plataforma que seguem o mesmo padrão:
- **Orchestrator** — executa Skill Flows declarativos
- **Notification Agent** — detecta e entrega pendências `outbound.*`
- **Evaluation Agent** (Horizonte 2) — avalia qualidade de atendimentos
- **Reviewer Agent** (Horizonte 2) — revisa avaliações sinalizadas

### Agentes IA externos

Agentes construídos com frameworks externos (LangGraph, CrewAI, etc.) que se integram via `plughub-sdk proxy` — sidecar em `localhost:7422` que intercepta todas as chamadas MCP para validação de permissões e auditoria. Overhead: < 1ms por chamada MCP.

### Agentes humanos

Atendentes humanos operando via interface de atendimento. Assistidos pelo **Agent Assist** — painel com estado da conversa, insights e capacidades disponíveis (seção 3.2a). Podem acionar agentes IA em conferência ou background via `agent_join_conference`.

---

## Contrato de execução (seção 4.2)

Todo agente, independentemente do tipo, segue o mesmo ciclo de vida:

```
agent_login → agent_ready → agent_busy → agent_done
```

O `agent_done` exige:
- `outcome`: `resolved` | `escalated_human` | `transferred_agent` | `callback`
- `issue_status`: sempre obrigatório, nunca vazio
- `handoff_reason`: obrigatório quando `outcome !== "resolved"`

---

## Interfaces

**Entrada (agente recebe):**
- `context_package` no `agent_login` — inclui `conversation_history`, `session_id`, `tenant_id`, `pipeline_state` parcial, insights carregados
- Mensagens do cliente via canal (normalizado pelo Channel Gateway)
- Mensagens de outros agentes (em conferência)

**Saída (agente produz):**
- Respostas ao cliente via `conversations.outbound` (Kafka) — entregues pelo Channel Gateway
- Chamadas a MCP tools (via SDK + PlugHubAdapter ou proxy sidecar)
- `agent_done` — sinaliza conclusão, publica em `conversations.events`

**Acesso a sistemas:**
- **Somente via MCP Layer** — agentes nunca acessam backends diretamente
- Toda chamada MCP é interceptada para validação de permissões e auditoria

---

## Fluxo de dados

```
Routing Engine aloca agente → conversations.routed
↓ Agente recebe context_package via agent_login
↓ Agente processa turno:
    → chama AI Gateway para raciocínio
    → chama MCP tools para ações de negócio
    → responde ao cliente
↓ Ciclo se repete até resolução
↓ agent_done → conversations.events (Kafka)
   ↓ Routing Engine fecha alocação
   ↓ Evaluation Agent avalia (Horizonte 2)
```

**Conferência (Agent Assist + agente IA):**
```
Agente humano em atendimento
↓ Agent Assist sugere agente IA (supervisor_capabilities)
↓ Humano autoriza → agent_join_conference
↓ Agente IA entra com context_package do histórico
↓ Agente IA e humano atendem simultaneamente
↓ Agente IA encerra via agent_done (outcome próprio)
↓ Humano continua atendimento principal
```

---

## Considerações operacionais

**Pools:** agentes são alocados em pools com configuração declarada no Agent Registry (`channel_types`, `max_concurrent`, `sla_target_ms`, `supervisor_config`). Um agente pode participar de múltiplos pools.

**Canary deployment:** novos agentes entram com `traffic_weight: 0.10`, sobem gradualmente (0.10 → 0.20 → 0.50 → 1.00). Rollback por convenção `{base}_v{n-1}`.

**Tipo arquitetural:** todo agente é declarado como `inbound`, `outbound` ou `notification` no Agent Registry. Determina quais pools pode integrar e quais sinais de conclusão são válidos.

**Isolação de MCP:** `PlugHubAdapter` (em-processo, nativo) ou proxy sidecar (`localhost:7422`, externo). Nenhuma chamada MCP chega ao domain MCP Server sem validação de permissão e registro de auditoria.

**Auto-scaling:** KEDA escala o pool de agentes IA com base em consumer lag dos tópicos Kafka. Dimensionado por demanda real, não por CPU.

---

## Referência spec

- Seção 4.2 — Contrato de Execução do Agente
- Seção 4.6a–4.6j — SDK (PlugHubAdapter, proxy sidecar)
- Seção 4.7 — Skill Registry (GitAgent, flow YAML)
- Seção 8.3 — Notification Agent
- Seção 3.2a — Agent Assist / Supervisor
- [modulos/sdk.md](../modulos/sdk.md)
- [modulos/notification-agent.md](../modulos/notification-agent.md)
- [modulos/agent-assist.md](../modulos/agent-assist.md)
