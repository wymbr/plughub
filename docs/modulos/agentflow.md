# Módulo: AgentFlow

> Última atualização: 2026-05-25 · Estado: Arc 16

> Rota UI: `/agent-flow/*` | Roles: admin, developer (operação); business (relatório)

## O que é

O módulo AgentFlow é o ambiente de desenvolvimento e operação de agentes IA nativos da plataforma. Permite criar, editar, publicar e monitorar Skill Flows — os programas declarativos em YAML que definem como os agentes IA atendem, raciocinam, escalam e encerram sessões.

## Abas / Rotas

| Rota | Arquivo | Descrição |
|---|---|---|
| `/agent-flow/editor` | `AgentFlowEditorPage.tsx` | Monaco YAML editor + deploy lifecycle |
| `/agent-flow/monitor` | `AgentFlowMonitorPage.tsx` | Instâncias de agentes ativos em tempo real |
| `/agent-flow/deploy` | `AgentFlowDeployPage.tsx` | Histórico de deploys, rollback, deploy agendado, monitor de handoff |
| `/agent-flow/report` | `AgentFlowReportPage.tsx` | Analytics de sessões por agente/pool (analytics-api) |

Redirect legado: `/skill-flows` → `/agent-flow/editor`.

## Gate ABAC

| Campo | Efeito |
|---|---|
| `skill_flows.operacao` | Exibe Editor, Monitor e Deploy |
| `skill_flows.visualizar` | Exibe Report |
| `skill_flows.editar` | Habilita ações de save/delete no Editor |

## Editor (AgentFlowEditorPage)

Monaco YAML editor (`vs-dark` theme) para definições de Skill Flow. Funcionalidades:
- Sidebar de skills com busca por `skill_id/name`, cor por classification (orchestrator=violet, vertical=cyan, horizontal=yellow)
- Indicador `●` de modificações não salvas
- Live YAML validation (parse errors na status bar)
- Conversão JSON ↔ YAML automática (skills são armazenadas como JSON no banco)
- `PUT /v1/skills/:id` para salvar; HTTP 422 mostra erros de schema
- `⌘S` keyboard shortcut
- Auto-refresh da lista de skills a cada 30 s
- Template blank para nova skill com exemplo de estrutura

## Deploy Lifecycle

### Dois estágios: draft → published

Toda edição salva cria um `draft`. O deploy publica para pools específicos.

```
PUT /v1/skills/:id   →  deploy_status = "draft"  (save)
POST /v1/skills/:id/deploy  →  deploy_status = "published"  (deploy)
```

### AgentFlowDeployPage

- **Histórico de deploys**: lista de `SkillDeployment` com data, pools, `deployed_by`; badge "rollback" em deploys originados por rollback
- **Rollback**: botão ↩ no histórico restaura `yaml_snapshot` do deploy anterior + re-deploy nos mesmos pools (dois estágios com confirmação)
- **Deploy agendado**: seletor `datetime-local` + botão "⏰ Agendar" cria instância de `skill_scheduled_deploy_v1` no workflow-api; listagem de deploys pendentes com botão "✕ Cancelar"
- **Monitor de handoff**: KPI card de sessões na versão anterior (verde=0, âmbar>0); barra de convergência animada; polling a cada 10 s via `GET /v1/skills/:id/handoff-status`

## Monitor (AgentFlowMonitorPage)

Instâncias de agentes IA ativos (`status: ready / busy / paused / draining`) com filtros dinâmicos por pool. Auto-refresh a cada 15 s.

**Fonte**: `GET /v1/instances?framework=plughub-native&status=` (agent-registry porta 3300).

## Skill Flow — step types

14 tipos de step declarativos em YAML:

| Tipo | Faz |
|---|---|
| `task` | Delega a outro agente via A2A (`assist` = conferência paralela; `transfer` = handoff completo) |
| `choice` | Branching condicional via `@ctx.*` e `pipeline_state` |
| `catch` | Retry e fallback antes de escalada |
| `escalate` | Roteia para pool via Rules Engine |
| `complete` | Encerra com outcome definido |
| `invoke` | Chama MCP tool diretamente |
| `reason` | Invoca AI Gateway com `output_schema` |
| `notify` | **Depreciado (Arc 16)** — envia mensagem ao cliente; usar `invoke: notification_send`. O sub-campo `notify` em `suspend` permanece válido por atomicidade |
| `menu` | Captura input do cliente e suspende até resposta |
| `suspend` | Suspende até sinal externo (aprovação, input, webhook, timer) |
| `collect` | Contata target via canal, aguarda resposta, suspende até reply ou expiração. No Arc 16 aceita `requires: [text\|audio\|video\|file_upload\|masked_input\|rich_menu]` em vez de `channel` explícito — o Channel Gateway seleciona o canal por matriz de capacidades |
| `resolve` | Coleta de contexto inline (gap check → CRM → LLM question → BLPOP → LLM extract) |
| `begin_transaction` / `end_transaction` | Delimitam bloco atômico de Masked Input — coleta-validação-ação em memória; `@masked.*` nunca escrito em Redis/stream/logs |
| `receive` | Suspende aguardando a próxima mensagem do stream de qualquer participante (sem prompt enviado ao canal) — BLPOP em `receive:result:{sid}:{iid}` |

## Padrão orquestrador + especialistas reutilizáveis

Um Skill Flow eficiente raramente contém toda a lógica de atendimento num único YAML. O padrão recomendado é **fatorar em orquestrador + especialistas** via step `task`:

```yaml
# agente_triage_ia_v1.yaml — orquestrador
- id: consultar_billing
  type: task
  target:
    pool_id: billing_especialista
  mode: assist           # especialista entra na conferência; cliente não percebe
  execution_mode: sync
  on_success: continuar_atendimento
  on_failure: escalar
```

O especialista `billing_especialista` não sabe quem o convocou — pode ser o orquestrador IA via `task` step ou um operador humano via `@billing`. O Skill Flow é idêntico nos dois casos.

**Por que isso importa na prática:**

| Situação | Sem fatoração | Com fatoração |
|---|---|---|
| Atualizar regra de cobrança | Editar N agentes que contêm a lógica | Editar `billing_especialista` uma vez; todos os chamadores recebem via hot-reload |
| Testar novo script de retenção | Testar em cada agente separadamente | Testar o especialista uma vez; cobre invocação IA e invocação humana |
| Migrar de semi-automático para automático | Reescrever o agente-IA para incluir lógica do operador | Substituir `@mention` manual por step `task` no orquestrador; especialista não muda |
| Sessão híbrida (IA + humano) | Comportamento diverge entre robô e operador | Mesmo especialista, comportamento padronizado |

A fatoração também define a **trajetória natural de maturação** de um contact center: começa com operadores usando `@mention` para acionar especialistas manualmente; à medida que os fluxos ficam previsíveis, o orquestrador IA passa a acionar os mesmos especialistas via `task` step, sem que o especialista precise ser reescrito.

## @mention — comandos em sessões de conferência

Agentes especialistas (co-pilot, billing) podem receber comandos via `@alias` do agente humano primary. Configurado em `mentionable_pools` do pool de origem:

```yaml
pools:
  - id: retencao_humano
    mentionable_pools:
      copilot:  copilot_retencao
      billing:  billing_especialista
```

Ações disponíveis em `mention_commands` do YAML: `set_context`, `trigger_step`, `terminate_self`.

## Journey — `creates_journey` e `mentionable_journeys` (Arc 10/16)

Uma skill pode declarar `creates_journey: true` no YAML — o skill-flow-worker cria automaticamente uma Journey no primeiro step, agrupando as sessões do processo. O pool pode declarar `mentionable_journeys` (análogo a `mentionable_pools`): aliases de `@journey:<skill_id>` que o agente primary usa para iniciar processos, e que alimentam o dropdown "Iniciar Processo" no Console. Ver módulo Processos.

## Pool Lifecycle Hooks

Permite que pools humanos declarem agentes que são ativados automaticamente em pontos do ciclo de atendimento:

```yaml
hooks:
  on_human_start: []          # vazio: co-pilot ativado via @mention
  on_human_end:
    - pool: wrapup_ia         # wrap-up obrigatório
    - pool: nps_ia            # pesquisa isolada ao cliente
  post_human: []
```

Triggers: `on_human_start` (humano entra), `on_human_end` (humano chama `agent_done`, último humano sai), `post_human` (após on_human_end concluído, antes de fechar WebSocket do cliente).

## Hot-reload de skills

Atualizar um YAML propaga para agentes em execução sem restart:

```
PUT /v1/skills/{id}  →  registry.changed (Kafka)  →  cache invalidado  →  próxima ativação usa versão nova
```

## Pacotes envolvidos

| Pacote | Responsabilidade |
|---|---|
| `agent-registry` | CRUD de skills, pools, agent_types; deploy lifecycle (`/v1/skills/:id/deploy`); hot-reload via `registry.changed` |
| `skill-flow-engine` | Interpretador do YAML — executor de steps, ContextStore, interpolação `@ctx.*`, `@segment.*` |
| `orchestrator-bridge` | Reconciliador de instâncias Redis vs. agent-registry; dispatch de pool hooks; publica `conversations.participants` |
| `ai-gateway` | Invocado pelos steps `reason` (Anthropic/OpenAI multi-account com fallback) |
| `platform-ui` | `modules/agent-flow/` — 4 páginas (Editor, Monitor, Deploy, Report) |

## Kafka topics

| Tópico | Produtor | Consumidor |
|---|---|---|
| `registry.changed` | agent-registry (PUT/DELETE skills) | orchestrator-bridge (cache invalidation + reconciliação) |
| `conversations.participants` | orchestrator-bridge | analytics-api → ClickHouse `segments` + `participation_intervals` |

## Referências

- Schemas: `packages/schemas/src/skill.ts`, `packages/schemas/src/agent-registry.ts`
- Skills YAML: `packages/skill-flow-engine/skills/`
- Backend: `packages/agent-registry/`, `packages/skill-flow-engine/`, `packages/orchestrator-bridge/`
- Frontend: `packages/platform-ui/src/modules/agent-flow/`
- ADR Deploy: `docs/adr/` (skill deploy lifecycle)
