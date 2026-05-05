# Módulo: Configuração → Relatórios de Agentes

> Rota UI: `/config/agent-reports` | Roles: supervisor, admin

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

## Fluxo de dados (pendente em parte)

**Implementado (UI e analytics)**:
- `AgentReportsPage.tsx` com duas abas (Disponibilidade e Pausas)
- `PauseReasonModal.tsx` no Agent Assist
- Config API seed com motivos padrão

**Pendente (backend)**:
- `agent_pause` event no orchestrator-bridge com `reason_id/reason_label` ao pausar via `PUT /api/agent-pause/:instanceId`
- Consumer no analytics-api para `agent_pause` / `agent_ready` → tabela `agent_pause_intervals`
- Endpoint `GET /reports/agent-availability`

Enquanto o backend não estiver completo, a aba Disponibilidade mostra dados de exemplo ou retorna 404 graciosamente.

**Dados disponíveis via endpoints existentes** (complementares):
- `GET /reports/participation` — volumetria de atendimento (total_sessions, duration_ms por agente/pool)
- `GET /reports/agents/performance` — avg_duration_ms, total_sessions, escalation_rate, handoff_rate
- `GET /reports/agent-performance/daily` — MV-backed, série diária de performance

## Relação com outros módulos

| Módulo | Relação |
|---|---|
| **Agent Assist** | PauseReasonModal (implementado) dispara `PUT /api/agent-pause` — dados alimentam Disponibilidade |
| **Contatos / Análise** | AnaliseTab tem handle time médio e volume — Relatórios de Agentes adiciona granularidade por agente individual |
| **AgentFlow / Report** | performance de agentes IA — este módulo foca em agentes humanos |

## APIs envolvidas

| Endpoint | Status | Descrição |
|---|---|---|
| `GET /reports/agent-availability` | ⏳ pendente | Disponibilidade e pausas agregadas por agente/dia |
| `GET /reports/agents/performance` | ✅ | Performance agregada (escalation_rate, avg_duration_ms) |
| `GET /reports/participation` | ✅ | Volumetria por participante/pool |
| `GET /config/agent_activity/pause_reasons` | ✅ | Motivos de pausa configurados |
| `PUT /api/agent-pause/:instanceId` | ⏳ pendente | Registra pausa com motivo no orchestrator-bridge |

## Pacotes envolvidos

| Pacote | Responsabilidade |
|---|---|
| `analytics-api` | Endpoints de reports existentes; tabela `agent_pause_intervals` (pendente DDL + consumer) |
| `orchestrator-bridge` | Publica `agent_pause`/`agent_ready` com reason_id (pendente implementação) |
| `config-api` | Motivos de pausa configuráveis (namespace `agent_activity`) |
| `platform-ui` | `modules/config/AgentReportsPage.tsx`, `modules/agent-assist/components/PauseReasonModal.tsx` |

## Referências

- Frontend: `packages/platform-ui/src/modules/config/AgentReportsPage.tsx`
- PauseReasonModal: `packages/platform-ui/src/modules/agent-assist/components/PauseReasonModal.tsx`
- Pendências: `CLAUDE.md` seção "Arc 8 — Relatório de Disponibilidade e Pausas de Agentes"
