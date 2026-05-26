# Arc 11 — Console: Superfície de Orquestração Humana

> Última atualização: 2026-05-25 · Estado: Arc 16

O Arc 11 eleva o Console de uma interface de atendimento para uma **superfície de orquestração** — onde o operador humano dirige, delega e monitora agentes AI como coparticipantes de primeira classe, com a mesma simetria que o modelo de sessão já suporta nos dados.

---

## Visão e Princípio

O modelo de sessão do PlugHub já trata AI e humanos simetricamente: ambos têm `role`, `participant_id`, `segment`, ciclo de vida `agent_done`, e pertencem a pools. O que falta é a **superfície de orquestração** refletir essa simetria na UI.

**Modelo mental:**
- **Console** = orquestrador comandado pelo humano (decide ritmo, delega, intervém)
- **Skill-Flow nativo** = orquestrador automatizado (executa, suspende, escalona)
- **Ambos usam as mesmas primitivas**: pools, segments, A2A task/assist, ContextStore

O humano e o Skill-Flow não competem — complementam. O Console é o ponto de entrada quando o humano está no controle; o Skill-Flow assume quando o fluxo está definido. O mesmo agente AI pode ser invocado por qualquer um dos dois.

---

## Funcionalidades

### F1 — Cartões de Participantes AI em Tempo Real

**Onde:** painel lateral direito do Console (coluna de participantes), ao lado dos cartões de agentes humanos.

**Comportamento:**
- Cada instância AI ativa na sessão aparece como um cartão de participante com: nome do agent_type (`agente_retencao_v1`), role (`primary` / `specialist` / `supervisor`), step atual do Skill-Flow (ex: "reason: classificando intent"), tempo no segmento, status visual (thinking / waiting / done / error).
- O status é derivado do `pipeline_state` em Redis — o Console lê via `GET /v1/supervisor-state` já existente, adicionando polling leve (3s interval) ou WebSocket se disponível.
- Cards de AI têm visual distinto dos humanos (ícone, badge de role) mas seguem o mesmo layout de cartão — simetria visual intencional.
- Ao clicar no cartão: drawer lateral com últimas 5 mensagens do agente na sessão + step atual + botão "Encerrar segmento" (ativa `terminate_self` via @mention).

**Backend necessário:** sem mudança. `supervisor_state` já retorna `participants[]` com `agent_type`, `role`, `segment_id`. Adicionar `current_step` e `step_status` ao payload.

**Dados do step atual:**
```
GET /v1/supervisor-state → participants[].ai_state {
  current_step:   string          // step_id do Skill-Flow
  step_type:      string          // "reason" | "invoke" | "task" | ...
  step_status:    "running" | "waiting" | "done" | "error"
  waiting_for?:   string          // "menu" | "receive" | "collect" | "approval"
  since_ms:       number          // tempo no step atual
}
```

---

### F2 — Botão "Adicionar Especialista"

**Onde:** ActionBar do Console (ao lado de "Iniciar Processo").

**Comportamento:**
- Abre dropdown listando agentes disponíveis nos `mentionable_pools` configurados no pool atual.
- Cada item mostra: nome do agente, descrição curta, role que assumirá (`specialist`).
- Ao selecionar: modal opcional "Contexto para o especialista" (campo texto livre) → chama `POST /v1/sessions/{id}/task` com `mode: "assist"` e o contexto como mensagem de entrada.
- O agente AI entra na sessão imediatamente; seu cartão aparece no painel (F1).

**Backend necessário:**
- Endpoint `GET /v1/pools/{pool_id}/mentionable-agents` — retorna lista de `{ agent_type_id, description, role }` filtrada por `mentionable_pools` do pool atual. Novo endpoint simples no agent-registry.
- `POST /v1/sessions/{id}/task` — já existe na lógica de A2A; expor como endpoint REST direto no mcp-server-plughub (hoje só acessível via MCP tool `session_task`).

**Alternativa de implementação curta prazo:** reutilizar o `@mention` protocol — o botão simplesmente injeta `@{agent_type_id}` no campo de texto do operador e submete. Mesma primitiva, sem novo endpoint. Recomendado para a Fase A.

---

### F3 — Ação "Delegar Tarefa"

**Onde:** context menu ao selecionar uma ou mais mensagens na transcrição, ou botão fixo no ActionBar quando há texto selecionado.

**Comportamento:**
- Ao ativar: drawer "Delegar tarefa" com: lista de agentes disponíveis (`mentionable_pools`), campo de instrução editável (pré-populado com a mensagem selecionada como contexto), opção de visibilidade da delegação (`agents_only` para que o cliente não veja).
- Submete criando um `task step` com `mode: "assist"`, com o contexto construído a partir da seleção.
- O agente AI trabalha em paralelo; o humano continua atendendo.
- Quando o AI termina (`agent_done`), um card "Resultado de delegação" aparece no painel do operador (não visível ao cliente se `agents_only`) com o outcome e mensagem gerada.

**Backend necessário:** mesma primitiva do F2 (`session_task`). A diferença é a origem do contexto (mensagem selecionada vs input livre).

---

### F4 — Tab de Orquestração

**Onde:** quinto tab no painel direito do Console (após Estado, Capacidades, Contexto, Histórico). Aparece apenas quando há um Skill-Flow ativo na sessão.

**Conteúdo:**
- **Grafo de steps simplificado** (ou lista linear se grafo for complexo): passos executados (✓), passo atual (⟳), próximos passos previstos (○), passos falhados (✗).
- **Linha do tempo de execução**: cada step com timestamp e duração.
- **Ações de intervenção disponíveis** (se o agente for `specialist` e o operador for `primary` ou `supervisor`):
  - "Injetar contexto antes do próximo step" — escreve uma tag no ContextStore que o step seguinte lerá.
  - "Pular para step X" — força `pipeline_state.current_step` via endpoint de admin (decisão: habilitar apenas para `supervisor` com ABAC `agent_assist.operacao`).
  - "Encerrar fluxo com outcome" — dispara `complete` step via `POST /v1/sessions/{id}/force-complete`.
- **Dados de pipeline_state**: expõe campos selecionados (sem dados mascarados) em formato legível.

**Backend necessário:**
- `GET /v1/sessions/{id}/pipeline-state` — já existe implicitamente em `supervisor_state`; extrair como endpoint dedicado com campo `steps_history[]`.
- `POST /v1/sessions/{id}/inject-context` — wrapper sobre `ContextStore.set()` com validação de permissão ABAC.
- `POST /v1/sessions/{id}/force-complete` — endpoint de intervenção; apenas `supervisor` via ABAC.

---

## Modelo de Permissões

| Ação | Role mínima | ABAC |
|---|---|---|
| Ver cartões AI (F1) | operator | `agent_assist.operacao` |
| Adicionar especialista (F2) | operator | `agent_assist.operacao` |
| Delegar tarefa (F3) | operator | `agent_assist.operacao` |
| Ver tab Orquestração (F4) | operator | `agent_assist.operacao` |
| Injetar contexto (F4) | supervisor | `agent_assist.operacao` |
| Pular step / force-complete (F4) | supervisor | `agent_assist.operacao` + scope |

---

## Arquitetura de Dados

### Fluxo de estado dos cartões AI (F1)

```
Redis pipeline_state → supervisor_state endpoint
  → Console polling 3s
  → CartãoAI.step_status
```

O Console não lê Redis diretamente — passa sempre por `supervisor_state` no mcp-server-plughub, mantendo o invariante de acesso exclusivo via MCP/REST.

### Fluxo de delegação (F2 / F3)

```
Operador aciona F2 ou F3
  → POST /v1/sessions/{id}/task (mode: assist)
  → Routing Engine aloca instância AI
  → AI entra como participant specialist
  → Cartão aparece em F1
  → AI executa Skill-Flow
  → agent_done → card "Resultado" aparece no Console
```

---

## Fases de Implementação

### Fase A — Visibilidade dos agentes AI (F1)

Escopo: cartões de participantes AI em tempo real no painel lateral.

**Backend:**
1. Estender `supervisor_state` response: adicionar `ai_state { current_step, step_type, step_status, waiting_for, since_ms }` por participante AI.
2. `pipeline_state` já está em Redis — adicionar leitura no handler de `supervisor_state`.

**Frontend:**
1. `AiParticipantCard` component — reutiliza estrutura visual dos cartões humanos com badge de role.
2. Polling 3s em `useSessionParticipants()` hook existente (ou novo hook dedicado).
3. Drawer de detalhes ao clicar no cartão.

Dependências: `supervisor_state` endpoint existente; `pipeline_state` em Redis.

---

### Fase B — Adicionar Especialista (F2)

Escopo: botão "Adicionar Especialista" no ActionBar.

**Backend:**
1. `GET /v1/pools/{pool_id}/mentionable-agents` no agent-registry.
2. Expor `session_task` MCP tool como endpoint REST `POST /v1/sessions/{id}/task` (ou usar @mention como atalho na Fase B, endpoint REST na Fase C).

**Frontend:**
1. Dropdown com agentes disponíveis (busca `mentionable_pools` do pool da sessão).
2. Modal de contexto opcional.
3. Chamada de API + feedback visual (cartão aparece via F1).

Dependências: Fase A concluída (cartão aparece imediatamente após invocação).

---

### Fase C — Delegar Tarefa (F3)

Escopo: context menu + drawer de delegação sobre mensagens selecionadas.

**Frontend:**
1. Seleção de mensagens na transcrição (shift-click ou checkbox).
2. Drawer "Delegar tarefa" com agentes + instrução editável + visibilidade.
3. Card "Resultado de delegação" quando `agent_done` chega via WebSocket.

**Backend:** mesmo endpoint de Fase B.

Dependências: Fase B concluída.

---

### Fase D — Tab de Orquestração (F4)

Escopo: tab de orquestração com grafo/lista de steps e ações de intervenção.

**Backend:**
1. `GET /v1/sessions/{id}/pipeline-state` com `steps_history[]`.
2. `POST /v1/sessions/{id}/inject-context` (ABAC supervisor).
3. `POST /v1/sessions/{id}/force-complete` (ABAC supervisor).

**Frontend:**
1. `OrchestrationTab` com visualização de steps.
2. Botões de intervenção condicionais ao role do usuário.

Dependências: Fases A–C concluídas; `pipeline_state` schema estável.

---

## Dependências de Outros Arcs

| Dependência | Arc | Status |
|---|---|---|
| `supervisor_state` endpoint | mcp-server-plughub | Existente |
| `pipeline_state` em Redis | Skill-Flow Engine | Existente |
| `mentionable_pools` no pool config | Agent Registry | Existente |
| `@mention` protocol | Arc (doc: mention-protocol.md) | Existente |
| A2A `task step` / `assist mode` | mcp-server-plughub | Existente |
| `agent_done` via WebSocket | Core + Channel Gateway | Existente |
| Journey `journey_start` MCP tool | Arc 10 | Existente |
| ABAC `agent_assist` module | Arc 7 | Existente |

---

## Status de Implementação (2026-05-13)

| Fase | Status | Notas de implementação |
|---|---|---|
| **A — F1 Cartões AI** | ✅ Concluída | `AiParticipantCard` + EstadoTab + polling 3s + drawer |
| **B — F2 Adicionar Especialista** | ✅ Concluída | `GET /v1/pools/:id/mentionable-agents` + `AdicionarEspecialistaButton` |
| **C — F3 Delegar Tarefa** | ✅ Concluída | `DelegarTarefaDrawer` + hover checkboxes + `DelegarButton` com badge |
| **D — F4 Tab Orquestração** | ✅ Concluída | `OrchestrationTab` + `inject-context` + `force-complete` REST endpoints |

### Implementação Fase C (detalhes)

- `MessageBubble`: prop `onToggleSelection` activa wrapper `group` com hover-checkbox laranja (opacity-0 → opacity-100 no hover, `bg-orange-500` quando selecionado).
- `ChatArea`: toolbar "📋 Contexto" exibida quando `selectedMessageIds.size > 0`.
- `ActionBar`: `DelegarButton` com badge numérico posicionado como `absolute -top-1.5 -right-1.5`.
- `DelegarTarefaDrawer`: 3 seções — agent picker (`MentionableAgent[]`), instruction textarea, visibility radio. `prefilledContext` concatena textos com `\n---\n`.
- `AgentAssistPage`: `handleDelegate` → `handleSend(@{id} {instruction})` → toast + `setSelectedMessageIds(new Set())` + `setShowDelegarDrawer(false)`.

### Implementação Fase D (detalhes)

- `server.ts` REST: `GET /api/supervisor_state` clona a lógica de `supervisor.ts` MCP tool para `ai_participants` + `pipeline_transitions`. Dois novos endpoints: `POST /api/inject-context` (HSET no Redis ContextStore) + `POST /api/force-complete` (GET+SET do pipeline JSON no Redis).
- `useSupervisorState`: retorna `{ state, refresh }` — `refresh` é o `fetchState` callback exposto para on-demand re-poll após intervenções de supervisor.
- `OrchestrationTab`: inline components `InjectContextForm` (form POST com feedback) + `ForceCompleteConfirm` (2-step confirm). Step type icons inferred from `to_step` name via regex.
- Tab "Orq." visível apenas quando `session.role === "supervisor" || "admin"` — gateado no `AgentAssistPage` tab bar render.

**Nenhuma nova primitiva de backend precisa ser inventada.** O Arc 11 é majoritariamente de UI — usa infraestrutura já construída em Arcs anteriores.

---

## Métricas de Sucesso

- Tempo médio de invocação de especialista AI via Console < 10s (hoje requer @mention manual).
- Visibilidade do status do agente AI durante a sessão: 100% das sessões com AI participante.
- Taxa de delegação bem-sucedida (outcome != error): monitorado via `agent_done.outcome` em `analytics.segments`.
- Intervenções de supervisor via Tab Orquestração: monitorado via `mcp.audit` (inject-context, force-complete).
