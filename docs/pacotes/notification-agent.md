# Módulo: notification-agent

> Spec de referência: v24.0 seção 8.3
> Responsabilidade: detectar pendências de entrega (`outbound.*`), oferecer ao cliente no momento certo e acionar o agente destino quando aceito
> Tipo arquitetural: `notification` — distinto de `inbound` e `outbound`
> Repositório interno: `plughub/agent-notification`

---

## Visão geral

O Notification Agent é um agente de IA nativo que segue o mesmo padrão GitAgent de qualquer outro agente da plataforma — mesmo contrato de execução (seção 4.2), mesmo ciclo de vida via `mcp-server-plughub`, mesma avaliação pelo Agent Quality Score. Não há lógica hardcoded na plataforma: o flow é declarado em YAML e o operador pode customizá-lo via override de skill no tenant sem tocar no código.

Responsabilidade única: detectar pendências `outbound.*` no Pending Delivery Store, oferecê-las ao cliente no momento adequado e, se aceitas, acionar o agente destino via Routing Engine.

O Notification Agent **não** confirma a entrega de uma pendência — isso é responsabilidade do agente destino ao encerrar a tratativa com sucesso.

---

## Contrato de entrada

O Notification Agent é acionado pelo Skill Flow Engine via step `task` com uma skill de notificação. Não é um consumidor Kafka independente — segue o ciclo de vida padrão de qualquer agente alocado pelo Routing Engine.

**Eventos que disparam o acionamento** (configurados no flow do Orquestrador):

| Gatilho | Condição | Comportamento |
|---|---|---|
| Início de conversa | Sempre, qualquer canal inbound | Consulta Pending Delivery Store, filtra por canal adequado, oferece pendência de maior prioridade antes de encaminhar ao atendimento principal |
| Fila de espera | Cliente aguarda agente humano além de threshold configurável (ex: 30s) | Aproveita janela de espera para entrega oportunista |
| Slots configuráveis | Pontos declarados pelo operador no flow | Após resolução, após handoff, durante pausa natural |

**Leitura de insights no início do contato:**

O Routing Engine inclui na sessão itens ativos do cliente. O Notification Agent filtra pelo prefixo `outbound.*` — não lê `insight.*` nem itens de outros prefixos.

---

## Contrato de saída

**MCP tools chamadas** (via `mcp-server-plughub`):

| Tool | Quando | O que faz |
|---|---|---|
| `pending_delivery_list` | Início do atendimento | Lista pendências `outbound.*` do cliente, filtradas por canal adequado |
| `pending_delivery_offer` | Após identificar pendência adequada | Apresenta a oferta ao cliente no canal correto |
| `agent_done` | Ao concluir | Sinaliza `outcome: resolved` (pendência aceita e agente destino acionado) ou `outcome: transferred_agent` (cliente recusou, devolvendo ao fluxo principal) |

**Aciona Routing Engine** para alocar o agente destino quando o cliente aceita a pendência.

**Publica em `conversations.events`** via `agent_done` — o ciclo de vida padrão.

---

## Fluxo de entrega

```
1. agent_login → agent_ready
2. Consulta Pending Delivery Store via pending_delivery_list
   → filtra por canal atual do cliente (WhatsApp, SMS, chat, etc.)
   → ordena por prioridade
3. Sem pendência adequada → agent_done (outcome: resolved, sem oferta)
4. Com pendência:
   a. Verifica canal atual vs canal adequado da pendência
   b. Canal adequado → oferta direta ao cliente
   c. Canal inadequado → menciona existência de pendências em outros canais
      (sem detalhar conteúdo)
5. Cliente aceita → Routing Engine aloca agente destino
   → agent_done (outcome: transferred_agent, handoff_reason: pending_accepted)
6. Cliente recusa → agent_done (outcome: resolved, pendência permanece ativa)
7. Timeout sem resposta → agent_done (outcome: resolved)
```

---

## Fluxo especial: fila de espera com pendência

Quando o cliente aguarda agente humano e o threshold é atingido:

```
Cliente em fila (ex: fila_suporte, posição 3)
↓ threshold atingido → Orquestrador aciona Notification Agent
↓ pendência disponível e canal adequado → oferta ao cliente
↓ cliente aceita
↓ Pending Delivery Store registra queue_hold:
  { fila_original, posição, contexto, prazo: SLA_restante }
↓ cliente sai da fila → agente destino assume a tratativa
↓ tratativa encerra → agente destino consulta interesse em retomar fila

  Sim → Routing Engine retoma queue_hold:
        fila original com contexto preservado
        SLA calculado a partir do tempo original de entrada

  Não → queue_hold: desistência / motivo: resolvido_por_outro_canal
        SLA da fila original não é afetado

  Timeout → desistência automática: timeout_queue_hold
```

O Notification Agent gerencia o ciclo da pendência apenas **até `aceita`** — não confirma entrega. A confirmação (`pendente → entregue`) acontece quando o agente destino encerra a tratativa com sucesso.

---

## Comportamento por canal

| Situação | Comportamento |
|---|---|
| Canal atual é adequado para a pendência | Oferta direta ao cliente |
| Canal atual não é adequado | Menciona existência de pendências em outros canais sem revelar conteúdo |
| Múltiplas pendências | Oferece a de maior prioridade; demais permanecem no Pending Delivery Store |

---

## Execução batch (sem cliente ativo)

Quando acionado por processo agendado via `process_context`, o `channel` é `"batch"` e `conversation_history` está vazio. O flow é idêntico — os mesmos step types, o mesmo `pipeline_state` no Redis. A diferença é semântica:

- Steps `notify` entregam ao canal declarado no `process_context` (email, webhook, sistema externo), não ao canal do cliente.
- `agent_done` com `outcome: resolved` significa execução concluída com sucesso, não cliente satisfeito.
- O scheduler externo recebe o resultado via Kafka (`conversations.events`) ou callback declarado no `process_context`.

---

## Persistência

O Notification Agent não escreve diretamente em Redis ou PostgreSQL. Toda persistência ocorre via MCP tools autorizadas.

| O que persiste | Onde | Quem escreve | Referência |
|---|---|---|---|
| Estado da pendência (`outbound.*`) | Pending Delivery Store (Redis) | `pending_delivery_offer` (MCP tool) | modelos-de-dados.md |
| `queue_hold` | Redis (key temporária por sessão) | Routing Engine | modelos-de-dados.md |
| Eventos de ciclo de vida | `conversations.events` (Kafka) | `agent_done` via mcp-server-plughub | kafka-eventos.md |

---

## Relações com outros módulos

| Módulo | Relação |
|---|---|
| `skill-flow-engine` | Aciona via step `notify` ou step `task` com skill de notificação; recebe `agent_done` para continuar o flow |
| `mcp-server-plughub` | Único canal de acesso a tools (`pending_delivery_list`, `pending_delivery_offer`, `agent_done`) |
| `routing-engine` | Aloca o Notification Agent; recebe demanda de alocação do agente destino quando pendência é aceita |
| `channel-gateway` | Entrega física da mensagem de oferta ao cliente no canal correto |
| `agent-registry` | Registra o tipo `notification` com repositório `plughub/agent-notification` |

---

## Customização por tenant

O operador pode customizar o Notification Agent via override de skill no tenant sem tocar no código da plataforma (seção 4.7). Parâmetros customizáveis:

- Categorias de `outbound.*` que o agente processa
- Canais habilitados para oferta
- Templates de mensagem por categoria e canal
- Threshold de espera em fila para acionamento oportunista
- Slots declarados no flow para entrega durante pausa natural

---

## Referência spec

- Seção 8.3 — Notification Agent: Entrega no Momento Certo
- Seção 8.4 — Circuit Breaker do Outbound
- Seção 4.2 — Contrato de Execução do Agente
- Seção 4.7 — Skill Registry e override por tenant
- Seção 2201 — Agentes nativos como GitAgents
