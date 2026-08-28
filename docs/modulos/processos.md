# Módulo: Processos

> ⚠️ **PÁGINA REMOVIDA em 2026-08-28** (F0 do [ADR de relatórios](../adr/adr-relatorios-duas-superficies-e-lentes.md)).
> A rota `/flow/processos` estava **fora do menu** (alcançável só por URL), contradizia a **D2** de
> [`adr-historico-unificado-duas-visoes`](../adr/adr-historico-unificado-duas-visoes.md) — *processo é
> pivô, nunca navegação livre* — e sua aba default agregava sobre `workflow_events`, **vazia por
> construção** desde a deprecação do workflow-api (Arc 19 fase D). O processo vive hoje como **nível 2**
> de `/analise/sessions?journey=…`. Este documento fica como registro histórico.

> Última atualização: 2026-05-25 · Estado: Arc 16

> Rota UI: `/agent-flow/processos` | Roles: operator+ | ABAC: `skill_flows.operacao`

## O que é

O módulo Processos é a visão de monitoramento de automação de longa duração. Unifica duas perspectivas complementares em duas abas:

- **Jornadas**: monitoramento de Journeys (Arc 10) — processos multi-sessão que transcendem um único contato
- **Instâncias**: monitoramento de instâncias de Workflow (Arc 4) — execuções de fluxos automáticos

No Arc 16, a Journey é formalizada como **superfície pública** de orquestração de processos de negócio: o `workflow-api` expõe `GET /v1/journeys?pool_id=...&status=suspended` e `POST /v1/journeys/{id}/resume` (encapsulando o `resume_token` interno), viabilizando o padrão de poller workflow (Tier 1 que monitora e retoma Journeys de outros processos). As MCP tools `journey_list_suspended`, `journey_resume` e `journey_check_pending` dão o mesmo acesso a agentes, sob a permissão ABAC `workflows.journey.*`.

## Aba Jornadas

A Journey é a unidade de serviço acima da sessão — agrupa todos os contatos de um mesmo processo de atendimento. Uma jornada pode ter múltiplas sessões associadas ao longo de dias ou semanas.

### KPI Strip

Exibe métricas agregadas por `skill_id` (tipo de jornada):

| KPI | Origem |
|---|---|
| Jornadas ativas | `GET /reports/journeys` — status `active` |
| Taxa de resolução | `(completed / total) × 100` |
| Duração mediana | p50 de `ended_at - started_at` |

### Lista de jornadas

Tabela com colunas: `journey_id`, `skill_id`, `customer_id`, `status`, `sessions vinculadas`, `criada em`, `última atualização`. Suporta filtro por status e skill.

### Painel de detalhe

Drawer lateral ao clicar numa jornada:
- Timeline de sessões vinculadas (`session_id`, `outcome`, `started_at`, `ended_at`)
- Status atual e histórico de eventos (`journey.events`)
- Botão **Merge** para unificar jornadas duplicadas (`journey_merge` MCP tool)
- Botão **✂️ Separar em nova jornada** (Fase F) — abre drawer com checklist de sessões collect para extrair para uma nova journey independente (`journey_split` MCP tool). Disponível apenas para jornadas `active` ou `suspended`.

## Aba Instâncias

Visualização das instâncias de Workflow criadas via `workflow-api`. Colunas: `instance_id`, `skill_id`, `status`, `origin_session_id`, `criada em`, `timeout em`. Filtro por status (`active`, `suspended`, `completed`, `failed`, `cancelled`).

## Status de jornada

| Status | Descrição |
|---|---|
| `active` | Em andamento — pelo menos uma sessão ativa |
| `suspended` | Aguardando retorno (collect, timer, approval) |
| `completed` | Concluída com sucesso |
| `failed` | Encerrada com falha |
| `cancelled` | Cancelada manualmente |

## Como uma jornada é iniciada

| Forma | Mecanismo |
|---|---|
| MCP tool manual | `journey_start(skill_id, session_id)` — chamado por agente IA ou humano |
| @mention | `@journey:<skill_id>` no campo de mensagem do Agent Assist |
| YAML automático | `creates_journey: true` na skill — cria jornada no primeiro step |
| Botão na UI | "Iniciar Processo" no ActionBar do Agent Assist (dropdown `mentionable_journeys`) |

## APIs

| Endpoint | Descrição |
|---|---|
| `GET /v1/journeys` | Lista jornadas do tenant (filtros: status, skill_id, customer_id) |
| `GET /v1/journeys/:id` | Detalhes de uma jornada |
| `POST /v1/journeys` | Cria jornada (via `journey_start` MCP tool) |
| `POST /v1/journeys/from-instance/:id` | Cria jornada a partir de instância de workflow |
| `POST /v1/journeys/:id/link` | Vincula sessão a uma jornada (`journey_link_session`) |
| `POST /v1/journeys/merge` | Unifica duas jornadas (`journey_merge`) |
| `GET /v1/journeys/:id/collect-sessions` | Lista session IDs collect elegíveis para split |
| `POST /v1/journeys/:id/split` | Extrai sessões para nova jornada (`journey_split`) |
| `GET /reports/journeys` | Relatório de jornadas com KPIs por skill_id |

## Kafka

| Tópico | Eventos |
|---|---|
| `journey.events` | `journey_started`, `journey_session_linked`, `journey_suspended`, `journey_resumed`, `journey_completed`, `journey_failed`, `journey_cancelled`, `journey_merged`, `journey_split` |

## Referências

- Arc técnico: [`docs/arcos/arc10-journey.md`](../arcos/arc10-journey.md)
- Arc técnico Workflow: [`docs/arcos/arc4-workflow.md`](../arcos/arc4-workflow.md)
- Agent Assist (Iniciar Processo): [`docs/modulos/agent-assist.md`](agent-assist.md)
