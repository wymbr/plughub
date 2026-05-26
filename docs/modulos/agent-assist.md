# Módulo: Agent Assist

> Última atualização: 2026-05-25 · Estado: Arc 16

> Rota UI: `/agent-assist` | Roles: operator, supervisor, admin

## O que é

O Agent Assist é a interface principal do agente humano durante o atendimento — e, a partir do Arc 11, a **superfície de orquestração** (Console) do PlugHub. Exibe a fila de contatos ativos (por pool), a transcrição em tempo real da conversa via WebSocket, sugestões do co-pilot de IA, dados de contexto do cliente (ContextStore) e histórico de contatos anteriores. Permite que o agente responda, pause, escale, transfira e encerre a sessão — e também dirija, delegue e monitore agentes IA como coparticipantes de primeira classe (Arc 11).

## Especialistas por pool — o diferencial de produtividade

O que diferencia o Agent Assist de outras interfaces de atendimento não está nos campos e botões — está no modelo de colaboração entre o agente humano e os agentes IA disponíveis para ele.

Cada pool declara um conjunto próprio de especialistas via `mentionable_pools`. Um agente da equipe de retenção tem acesso a um especialista de cobrança, um consultor jurídico IA e um co-pilot treinado em scripts de retenção. Um agente de suporte técnico tem especialistas diferentes. A configuração é por pool, não global — cada equipe tem exatamente o conjunto de colaboradores adequado ao seu contexto.

O acionamento usa a sintaxe `@alias [comando ou contexto]` diretamente no campo de mensagem:

```
@billing conta=@ctx.caller.account_id motivo=@ctx.caller.motivo_contato
@juridico analise o contrato de prestação de serviço
@copilot o cliente está insistindo em cancelar, sugira alternativas
```

O especialista convocado entra na sessão como participante real — não é uma sugestão em barra lateral. Pode conversar com o agente humano internamente (`agents_only`), pode falar diretamente com o cliente (`all`), ou pode operar em background processando dados enquanto o agente humano continua o atendimento.

Além do acionamento manual, os Pool Lifecycle Hooks disparam especialistas automaticamente:

| Momento | Hook | Exemplo de uso |
|---|---|---|
| Agente entra na sessão | `on_human_start` | Co-pilot ativado automaticamente |
| Agente encerra o atendimento | `on_human_end` | NPS + wrap-up IA disparam sem intervenção |
| Pós-processamento | `post_human` | Sumarização, tagging, registro de CRM |

Tudo isso é configurado com o mesmo Skill Flow YAML que define o comportamento do agente IA. Não há ferramenta separada de "copilot" para instalar ou licenciar.

## Layout

```
┌─────────────────────────────────────────┐
│  Header (agente, pool, sessão, SLA, WS) │
├──────────────────┬──────────────────────┤
│  ChatArea (60%)  │  RightPanel (40%)    │
├──────────────────┴──────────────────────┤
│  AgentInput + CloseModal                │
└─────────────────────────────────────────┘
```

### ContactList

Painel lateral com cards de contatos ativos, ordenados por prioridade: SLA urgency, tempo de espera, sentimento. Clicar num card troca a sessão ativa no ChatArea. Um único WebSocket persistente serve todos os contatos simultaneamente.

### ChatArea

Transcrição em tempo real via WebSocket (`agent-ws`). Tipos de mensagem:

- **`all`** — mensagens normais (bubble branca/cinza por autor)
- **`agents_only`** — notas internas: fundo âmbar + badge "Interno", posicionadas à esquerda
- **`interaction.request`** (menus) — renderizados como `MenuCard`:
  - Modo observação: disabled (padrão)
  - Modo substituição: interativo (ativado via botão 🔄)

### RightPanel — 5 abas

| Aba | Conteúdo | Fonte |
|---|---|---|
| **Estado** | Score de sentimento (linha temporal + trend), intenção detectada, flags de risco, SLA + `AiParticipantCard` (cartões de participantes IA em tempo real, Arc 11 Fase A) | ContextStore + `supervisor_state` |
| **Capacidades** | Seção Co-pilot (sugestão de resposta, flags de risco, ações recomendadas) + agentes sugeridos para escalada | `GET /api/copilot_state/:sessionId` + `supervisor_state` |
| **Contexto** | ContextSnapshotCard (teal) com campos agrupados por namespace (`caller.*`, `session.*`, `account.*`) | `supervisor_state` MCP tool → ContextStore |
| **Histórico** | Últimos 20 contatos fechados do cliente identificado | `GET /analytics/sessions/customer/{id}` |
| **Orquestração** | Agentes IA ativos + linha do tempo de transições do Skill-Flow + ações de intervenção (Arc 11 Fase D) — **visível apenas para roles `supervisor`/`admin`** | `GET /api/supervisor_state` (`ai_participants` + `pipeline_transitions`) |

A aba Contexto detecta automaticamente o formato disponível:
- **`context_snapshot` presente** → `ContextSnapshotCard` (teal) com campos agrupados por namespace, badge 🔒 para entradas `agents_only`
- **Apenas `contact_context`** → `ContactContextCard` (emerald) — fallback legado

A aba **Orquestração** só é renderizada quando `session.role === "supervisor" || "admin"` (gate no tab bar do `AgentAssistPage`).

## Console como superfície de orquestração (Arc 11)

O Arc 11 eleva o Agent Assist de interface de atendimento para superfície de orquestração: o operador humano e o Skill-Flow nativo usam as mesmas primitivas (pools, segments, A2A `task`/`assist`, ContextStore) — o Console é o ponto de entrada quando o humano está no controle.

### Fase A — Cartões de participantes IA (`AiParticipantCard`)

Cada instância IA ativa na sessão aparece como cartão no **EstadoTab**, ao lado dos cartões humanos: agent_type, role, step atual do Skill-Flow, `step_status` (running/waiting/done/error), `waiting_for` e tempo no segmento. Polling de 3 s sobre `supervisor_state` (`ai_participants[]` com `AiState { current_step, step_type, step_status, waiting_for, since_ms }`). A bridge escreve `session:{id}:ai_participant:{instance_id}` (TTL 4h). Clicar no cartão abre drawer com as últimas 5 mensagens do agente + botão "Encerrar segmento" → `@{instance_id} terminate_self`.

### Fase B — Adicionar Especialista

Botão `AdicionarEspecialistaButton` no ActionBar: dropdown 2-step (escolha de agente + textarea de contexto). Lista os agentes dos `mentionable_pools` do pool atual via `GET /v1/pools/:poolId/mentionable-agents` (agent-registry) — hook `useMentionableAgents(poolId)`. Ao confirmar, envia `@{agent_type_id} {context}` pelo WebSocket; o especialista entra como `specialist` e seu cartão aparece via Fase A.

### Fase C — Delegar Tarefa

Seleção de mensagens no transcript: hover-checkbox em `MessageBubble`, toolbar de contagem no `ChatArea`. O `DelegarButton` (com badge numérico) no ActionBar abre o `DelegarTarefaDrawer` — agent picker, instrução pré-preenchida com as mensagens selecionadas, e radio de visibilidade (`agents_only` para que o cliente não veja). Submete via `handleSend(@{id} {instruction})`. Quando o agente IA termina (`agent_done`), um card de resultado aparece no painel.

### Fase D — Tab de Orquestração

Quinta aba do RightPanel (`OrchestrationTab`), gateada por `supervisor`/`admin`. Lista os agentes IA da sessão, a linha do tempo de `pipeline_transitions` do Skill-Flow, e ações de intervenção: `InjectContextForm` → `POST /api/inject-context/:sessionId` (HSET no ContextStore Redis) e `ForceCompleteConfirm` → `POST /api/force-complete/:sessionId`. O hook `useSupervisorState` retorna `{ state, refresh }` — `refresh` permite re-poll on-demand após intervenções.

## Botão "Iniciar Processo" (Journey — Arc 10)

O ActionBar inclui o botão "Iniciar Processo": um dropdown filtrado por `pool.mentionable_journeys` que cria uma Journey (`journey_start` MCP tool) a partir da sessão atual. Vincula o atendimento ao módulo Processos (`/agent-flow/processos`). O `HistoricoTab` exibe ainda a seção "Processos em aberto" do `customer_id`.

## WebRTCOverlay — sessões de vídeo/voz (Arc 15)

Para sessões com `channel === "webrtc"`, o `WebRTCOverlay` é renderizado antes do `ParticipantFilterBar`: grid de vídeo 2-up, waveform animado ou nenhum overlay conforme o medium negociado (`video`/`voice`/`text`). Hook `useWebRTCSession()` faz a conexão LiveKit (publish de tracks locais, controles de mic/câmera). Supervisores entram como observadores via `WebRTCSupervisorView` (sem publicar tracks).

## Gate ABAC

| Campo | Efeito |
|---|---|
| `contacts.operacao` | Exibe o item "Agent Assist" na sidebar (grupo Atendimento) |
| `agent_assist.atender` | Pode atender contatos como primary agent |
| `agent_assist.supervisionar` | Pode entrar como supervisor numa sessão ativa |

## Fluxo de sessão

1. UI conecta ao WebSocket com `pool_id` → mcp-server-plughub registra canal
2. `conversation.assigned` chega por `pool:events:{poolId}` → UI exibe contato e carrega histórico
3. Mensagens chegam via `message.text` WS events → adicionadas ao estado `messages[]`
4. Co-pilot analisa cada mensagem do cliente fire-and-forget (`analyze_for_copilot`) → `copilot.updated` via Redis pub/sub → CapacidadesTab atualiza
5. Agente encerra → `POST /api/agent_done/{sessionId}` (issue_status + outcome + handoff_reason) → volta ao lobby
6. `session.closed` por disconnect do cliente → contato removido automaticamente — wrap-up e NPS disparam via Pool Lifecycle Hooks (`on_human_end` → `agente_nps_v1` + `agente_wrapup_v1`) sem abrir CloseModal

## PauseReasonModal

Intercepta o botão "Pausar". Busca motivos de `GET /config/agent_activity/pause_reasons` com fallback para lista padrão (intervalo, almoço, treinamento, reunião, outro). Motivos com `requires_note: true` exibem textarea obrigatória (≥ 3 chars). Confirmar envia `PUT /api/agent-pause/:instanceId` (best-effort — graceful degradation).

## CloseModal

Aparece **somente** em encerramento manual explícito pelo agente. Campos:
- `issue_status` (texto livre — descrição do problema)
- `outcome` (resolved / escalated / transferred / abandoned)
- `handoff_reason` (obrigatório quando `outcome !== "resolved"`)

**Não aparece** em `session.closed` por disconnect do cliente — o encerramento é tratado server-side.

## Modo Substituição de Menu

Botão "🔄 Substituir" na ActionBar ativa modo substituição. Menus `interaction.request` ficam interativos (button/list/checklist/form/text). O clique chama `POST /api/menu_submit/:sessionId` → XADD `interaction_result` no stream Redis → Skill Flow Engine retoma o suspend step. Auto-disable após submit bem-sucedido. Roteamento por `participant_id` via ContextStore (`session.human_agent_participant_id`).

## Co-pilot (Aba Capacidades)

O AI Gateway analisa cada mensagem do cliente em background usando `claude-haiku-4-5-20251001` (isolado de tráfego realtime). Escreve 4 tags no ContextStore:

| Tag | Conteúdo |
|---|---|
| `session.copilot.sugestao_resposta` | Sugestão de resposta personalizada |
| `session.copilot.flags_risco` | Badges de risco (sentimento, intenção) |
| `session.copilot.acoes_recomendadas` | Ações recomendadas pelo contexto |
| `session.copilot.ultima_analise` | Timestamp da última análise |

O hook `useCopilotState` re-busca ao receber `copilot.updated` via Redis pub/sub.

## Pacotes envolvidos

| Pacote | Responsabilidade |
|---|---|
| `mcp-server-plughub` | WebSocket server, MCP tools (`supervisor_state`, `copilot_state`, `menu_submit`, `agent_done`), pub/sub de eventos |
| `ai-gateway` | `copilot_emitter.py` — análise fire-and-forget para sugestões + `POST /v1/copilot/analyze` |
| `analytics-api` | `GET /sessions/customer/{id}` — histórico de contatos fechados |
| `platform-ui` | `modules/agent-assist/` — AgentAssistPage + componentes (Header, ChatArea, AgentInput, CloseModal, RightPanel, ContactList) |

## APIs

| Endpoint | Descrição |
|---|---|
| WS `agent-ws` (porta 3100) | Canal persistente com auto-reconexão (3 s delay) |
| `pool:events:{poolId}` | Redis pub/sub — assignments e copilot.updated |
| `POST /api/agent_done/{sessionId}` | Encerra sessão com issue_status, outcome, handoff_reason |
| `GET /api/supervisor_state/{sessionId}` | Dados do ContextStore para RightPanel + Aba Estado |
| `GET /api/copilot_state/{sessionId}` | Sugestões do co-pilot para Aba Capacidades |
| `POST /api/menu_submit/{sessionId}` | Submete resposta de menu interativo |
| `PUT /api/agent-pause/:instanceId` | Registra pausa com motivo (best-effort) |

## Relação com outros módulos

| Módulo | Relação |
|---|---|
| **Contatos** | O Monitor do módulo Contatos exibe as mesmas sessões ativas que o Agent Assist atende, com drill-down de transcrição ao vivo |
| **Avaliação** | Sessões atendidas no Agent Assist são amostradas pelas campanhas de avaliação |
| **Relatórios de Agentes** | Tempo de atendimento, taxas de pausa e escalonamento são reportados na aba Disponibilidade |

## Referências

- Frontend: `packages/platform-ui/src/modules/agent-assist/`
- WS server: `packages/mcp-server-plughub/src/server.ts`
- Co-pilot: `packages/ai-gateway/src/plughub_ai_gateway/copilot_emitter.py`
- Pool Hooks: `packages/orchestrator-bridge/src/plughub_orchestrator_bridge/main.py` (funções `fire_pool_hooks`, `_write_pre_hook_context`)
- Arc 11 (Console / orquestração): `docs/arcos/arc11-console-orchestration.md`
- Arc 15 (WebRTC): `docs/arcos/arc15-webrtc.md`
- Guia operacional: `docs/sections/conferencia-e-historico.md`
