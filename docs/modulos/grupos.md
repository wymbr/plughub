# Módulo: Grupos de Agentes

> Última atualização: 2026-05-25 · Estado: Arc 16

> Rota UI: `/config/groups` | Roles: admin | ABAC: `config.users`

## O que é

O módulo de Grupos de Agentes permite organizar agentes humanos em grupos para fins de gestão e supervisão. É uma entidade ortogonal ao Pool — enquanto o Pool define roteamento, o Grupo define a estrutura organizacional (org chart). Um agente pode pertencer a múltiplos grupos simultaneamente.

## Conceito: Group vs Pool

| Entidade | Propósito | Define |
|---|---|---|
| **Pool** | Roteamento de contatos | Quem atende qual tipo de contato |
| **AgentGroup** | Organização e supervisão | Quem supervisiona quem, em quais turnos |

## Layout

A `GroupsPage` exibe uma lista de grupos com um drawer lateral que abre ao selecionar um grupo. O drawer tem 4 abas:

| Aba | Conteúdo |
|---|---|
| **Info** | Nome, descrição, metadados do grupo |
| **Members** | Agentes humanos e agentes IA membros (`agent_type_id`, flag `is_human`) |
| **Supervisors** | Usuários com papel de supervisor neste grupo |
| **Shifts** | Turnos de supervisão: `days_of_week[]`, `time_start`, `time_end`, `timezone` |

## Tabelas de banco (schema `auth`)

| Tabela | Conteúdo |
|---|---|
| `agent_groups` | Entidade principal: `group_id`, `tenant_id`, `name`, `description` |
| `agent_group_members` | Membros: `group_id`, `agent_type_id`, `is_human` |
| `agent_group_users` | Usuários associados ao grupo |
| `agent_group_supervisors` | Usuários com papel supervisor no grupo |
| `agent_group_shifts` | Turnos: `days_of_week[]` (DOW: 0=Dom), `time_start TIME`, `time_end TIME`, `timezone` |

## Impacto no JWT

O Auth API resolve o escopo do supervisor no momento do login via `resolve_supervisor_scope()`. O JWT passa a carregar três claims adicionais:

| Claim | Tipo | Significado |
|---|---|---|
| `supervised_groups[]` | string[] | IDs dos grupos sob supervisão no turno ativo |
| `supervised_agent_types[]` | string[] | agent_type_ids dos membros dos grupos supervisionados |
| `supervised_user_ids[]` | string[] | user_ids dos usuários nos grupos supervisionados |

Regras especiais:
- `admin` → `([], [], [])` — sem restrição (arrays vazios = acesso total)
- Supervisor sem turno ativo → `supervised_agent_types: ["__no_active_shift__"]` — sentinel que impede interpretação de array vazio como "sem restrição"

## Gate ABAC

| Campo | Efeito |
|---|---|
| `config.users` | Acesso à página de grupos (mesma gate que gestão de usuários) |

## APIs

| Endpoint | Descrição |
|---|---|
| `GET /v1/groups` | Lista grupos do tenant |
| `POST /v1/groups` | Cria grupo |
| `GET /v1/groups/:id` | Detalhes do grupo |
| `PUT /v1/groups/:id` | Atualiza grupo |
| `DELETE /v1/groups/:id` | Remove grupo |
| `POST /v1/groups/:id/members` | Adiciona membro |
| `DELETE /v1/groups/:id/members/:agent_type_id` | Remove membro |
| `POST /v1/groups/:id/supervisors` | Adiciona supervisor |
| `DELETE /v1/groups/:id/supervisors/:user_id` | Remove supervisor |
| `POST /v1/groups/:id/shifts` | Adiciona turno |
| `PUT /v1/groups/:id/shifts/:shift_id` | Atualiza turno |
| `DELETE /v1/groups/:id/shifts/:shift_id` | Remove turno |

Todas as rotas usam `X-Admin-Token` ou `Bearer JWT` com role admin.

## Efeito nos relatórios de analytics

O `analytics-api` filtra todos os relatórios de agentes pelo escopo do supervisor:
- `supervised_agent_types: null` → sem filtro (admin)
- `supervised_agent_types: ["__no_active_shift__"]` → resultado vazio
- `supervised_agent_types: [...]` → WHERE `agent_type_id IN (...)` nas queries

As 5 rotas de relatório afetadas: segments, performance, availability, sessions, daily performance.

## Referências

- Arc técnico: [`docs/arcos/arc9-agent-groups.md`](../arcos/arc9-agent-groups.md)
- Auth API: [`docs/pacotes/auth-api.md`](../pacotes/auth-api.md)
- Controle de Acesso: [`docs/modulos/controle-acesso.md`](controle-acesso.md)
