# Módulo: Configuração → Relatórios de Agentes

> Última atualização: 2026-05-25 · Estado: Arc 16

> Rota UI: `/contacts/reports/agents` | Roles: supervisor, admin, business

## O que é

O módulo Relatórios de Agentes exibe métricas operacionais de produtividade e disponibilidade dos agentes humanos: tempo em atendimento, taxa de pausa por motivo, taxa de escalonamento e transferência. Complementa o módulo Contatos (que foca nas sessões) com visão centrada no agente.

## Abas

### Disponibilidade

Tabela pivot agente × data com intensidade de cor âmbar proporcional ao tempo de pausa (ms / 4h por célula). Filtros: período, pool_id, agent_type_id.

**Fonte**: `GET /reports/agent-availability` (analytics-api → ClickHouse `agent_pause_intervals FINAL`).

Métricas por agente por dia:
- `total_pause_duration_ms` — soma de todas as pausas
- `pause_count` — número de pausas
- Breakdown por `reason_id` com duração acumulada

### Pausas

Tabela flat de `reason_breakdown` com paginação e exportação CSV. Colunas: agente, pool, motivo de pausa, nota (quando `requires_note: true`), duração, período.

**Fonte**: mesma que Disponibilidade, filtrada por motivo e agente específico.

## PauseReasonModal (Agent Assist)

O modal que os agentes usam para registrar o motivo da pausa é configurado via Config API (namespace `agent_activity`, key `pause_reasons`):

```json
[
  { "id": "intervalo",   "label": "Intervalo",   "requires_note": false },
  { "id": "almoco",      "label": "Almoço",      "requires_note": false },
  { "id": "treinamento", "label": "Treinamento", "requires_note": false },
  { "id": "reuniao",     "label": "Reunião",     "requires_note": true  },
  { "id": "outro",       "label": "Outro",       "requires_note": true  }
]
```

Override por pool via chave `pause_reasons:{pool_id}`.

## Fluxo de dados (Arc 8 — totalmente implementado)

O pipeline de rastreamento de pausas do Arc 8 está completo de ponta a ponta:

```
PUT /api/agent-pause  (mcp-server-plughub)
  → atualiza estado Redis: agent_ready → agent_paused
  → publica agent_pause em agent.lifecycle (Kafka) com reason_id/reason_label

PUT /api/agent-resume  (mcp-server-plughub)
  → atualiza estado Redis: agent_paused → agent_ready
  → publica agent_ready em agent.lifecycle

analytics-api — consumer agent.lifecycle:
  agent_pause  → INSERT em agent_pause_intervals (resumed_at = null)
  agent_ready  → UPDATE da linha via ReplacingMergeTree (resumed_at + duration_ms)

GET /reports/agent-availability  (analytics-api)
  → agrega agent_pause_intervals FINAL por agente/dia
```

Os endpoints de pausa **`PUT /api/agent-pause` e `PUT /api/agent-resume` ficam no `mcp-server-plughub`** — não no orchestrator-bridge. Ambos seguem o agent contract lifecycle. A pausa é hard filter no Routing Engine: agentes pausados são excluídos da alocação imediatamente.

**Dados complementares via outros endpoints**:
- `GET /reports/participation` — volumetria de atendimento (total_sessions, duration_ms por agente/pool)
- `GET /reports/agents/performance` — avg_duration_ms, total_sessions, escalation_rate, handoff_rate
- `GET /reports/agent-performance/daily` — MV-backed, série diária de performance

## Relação com outros módulos

| Módulo | Relação |
|---|---|
| **Agent Assist** | PauseReasonModal dispara `PUT /api/agent-pause` — dados alimentam Disponibilidade |
| **Contatos / Análise** | AnaliseTab tem handle time médio e volume — Relatórios de Agentes adiciona granularidade por agente individual |
| **AgentFlow / Report** | performance de agentes IA — este módulo foca em agentes humanos |

## APIs envolvidas

| Endpoint | Descrição |
|---|---|
| `GET /reports/agent-availability` | Disponibilidade e pausas agregadas por agente/dia (analytics-api) |
| `GET /reports/agents/performance` | Performance agregada (escalation_rate, avg_duration_ms) |
| `GET /reports/participation` | Volumetria por participante/pool |
| `GET /config/agent_activity/pause_reasons` | Motivos de pausa configurados |
| `PUT /api/agent-pause` | Registra pausa com motivo (mcp-server-plughub) |
| `PUT /api/agent-resume` | Retoma agente pausado (mcp-server-plughub) |

## Pacotes envolvidos

| Pacote | Responsabilidade |
|---|---|
| `analytics-api` | Consumer `agent.lifecycle` → ClickHouse `agent_pause_intervals`; endpoint `GET /reports/agent-availability` |
| `mcp-server-plughub` | Endpoints `PUT /api/agent-pause` / `PUT /api/agent-resume`; publica `agent_pause`/`agent_ready` com `reason_id` |
| `config-api` | Motivos de pausa configuráveis (namespace `agent_activity`) |
| `platform-ui` | `modules/config/AgentReportsPage.tsx`, `modules/agent-assist/components/PauseReasonModal.tsx` |

## Referências

- Arc técnico: [`../arcos/arc8-agent-availability.md`](../arcos/arc8-agent-availability.md)
- Frontend: `packages/platform-ui/src/modules/config/AgentReportsPage.tsx`
- PauseReasonModal: `packages/platform-ui/src/modules/agent-assist/components/PauseReasonModal.tsx`
