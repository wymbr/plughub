# Módulo: Contatos

> Rota UI: `/contacts` | Roles: operator, supervisor, admin, business

## O que é

O módulo Contatos é o hub central de visibilidade do contact center. Unifica em uma única tela a listagem de contatos ativos e históricos, o monitor em tempo real por pool, e a análise agregada de métricas.

## Abas

### Lista

Tabela de contatos (ativos e finalizados) com filtros por canal, pool, agente e período. Colunas: identificador do cliente, canal, pool, agente atribuído, status, duração, sentimento médio.

Fonte: `analytics-api` → ClickHouse `sessions FINAL`.

### Monitor

Visualização em tempo real dos contatos ativos organizados por pool. Duas visualizações disponíveis:

- **Cards por pool**: heatmap de sentimento por pool, ordenados do pior para o melhor sentimento
- **Lista de sessões ativas**: tabela com wait time, SLA urgency e sinalização de "próximo sugerido"

Drill-down disponível: pool → sessões ativas → transcrição ao vivo com SSE.

Fonte: `analytics-api` → Redis snapshots (SSE `/dashboard/operational`, poll 5s) + `session:{id}:stream` (XREAD bloqueante via `/sessions/{id}/stream`).

### Análise

Métricas agregadas do conjunto filtrado: volume por canal, handle time médio, score de qualidade médio, distribuição por outcome. Gráficos de timeseries com interval picker.

Fonte: `analytics-api` → ClickHouse + endpoints `/reports/sessions`, `/reports/agents`, `/reports/timeseries/*`.

## Gate ABAC

| Campo | Efeito |
|---|---|
| `contacts.operacao` | Exibe as abas Monitor e Agent Assist |
| `contacts.visualizar` | Exibe a aba Análise |

Usuários `business` com `operacao: none` veem apenas a aba Lista.

## Pacotes envolvidos

| Pacote | Responsabilidade |
|---|---|
| `analytics-api` | Consumer Kafka→ClickHouse, SSE de snapshots, endpoints de reports e sessions |
| `channel-gateway` | Produz `conversations.inbound`, assina Redis pub/sub para WS delivery |
| `routing-engine` | Produz snapshots de pool no Redis a cada evento de roteamento |
| `mcp-server-plughub` | Tool `supervisor_state` lê ContextStore e retorna `context_snapshot` |
| `platform-ui` | `modules/contacts/` — ContactsPage + ListaTab + MonitorTab + AnaliseTab |

## Eventos Kafka relevantes

- `conversations.inbound` — nova sessão inbound normalizada pelo Channel Gateway
- `conversations.routed` / `conversations.queued` — alocação ou enfileiramento pelo Routing Engine
- `conversations.session_opened` / `conversations.session_closed` — lifecycle da sessão
- `sentiment.updated` — AI Gateway publica score de sentimento após cada turno LLM
- `conversations.participants` — participantes que entram/saem de sessões (analytics de participação)

## Referências

- ADR: `docs/adr/adr-contact-segments.md`
- Guide: `docs/guias/conferencia-e-historico.md` (em `docs/sections/`)
- Backend: `packages/analytics-api/`, `packages/channel-gateway/`
- Frontend: `packages/platform-ui/src/modules/contacts/`
