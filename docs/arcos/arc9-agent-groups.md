# Arc 9 — Agent Groups & Supervisor Scope

> Última atualização: 2026-05-25 · Estado: Arc 16 · Status: Arc 9 implementado

> **Arc 9 implementado** — `GroupsPage` (`/config/groups`), `groups_router.py` (CRUD de grupos + sub-recursos), claims JWT (`supervised_groups`, `supervised_agent_types`, `supervised_user_ids`) e os filtros de escopo em analytics-api estão em produção. Supervisor com grupos ativos mas sem nenhum membro recebe o sentinela `["__no_active_shift__"]` em `supervised_agent_types` — isso impede que a lista vazia seja interpretada como "sem restrição" (que é a semântica para admin). Este documento descreve o desenho original; o estado vigente está consolidado no CLAUDE.md § Arc 9.

## Problema

Pool é a unidade de **roteamento** de contatos. Equipe é a unidade de **gestão de pessoas**. São conceitos ortogonais: um agente pode atender qualquer pool mas pertence a uma equipe; um supervisor precisa enxergar somente os agentes do seu grupo, independente de qual pool eles estão atendendo no momento.

O mecanismo existente (`accessible_pools[]` no JWT) filtra analytics por pool, não por equipe. Um pool pode conter agentes de turnos diferentes supervisionados por pessoas diferentes — `accessible_pools[]` não resolve isso.

Adicionalmente, call centers operam em turnos (manhã, tarde, noite). A mesma infraestrutura (pools, filas, troncos) é compartilhada, mas cada turno tem um supervisor e um grupo de agentes distinto. A restrição de visibilidade precisa ser resolvida por grupo de gestão + turno ativo, não por pool.

---

## Entidades

### `AgentGroup`

Unidade de gestão de pessoas. Independente de pool.

```
AgentGroup
  group_id        UUID PK
  tenant_id       TEXT NOT NULL
  name            TEXT NOT NULL        ex: "Equipe SAC Manhã"
  description     TEXT
  created_at      TIMESTAMPTZ
  updated_at      TIMESTAMPTZ
```

### `AgentGroupMember`

Agentes (AI ou humanos por agent_type) pertencentes ao grupo. Define o escopo de visibilidade do supervisor.

```
AgentGroupMember
  group_id        UUID FK → agent_groups
  agent_type_id   TEXT NOT NULL        ex: "agente_sac_v1"
  is_human        BOOLEAN DEFAULT false
  PRIMARY KEY (group_id, agent_type_id)
```

### `AgentGroupUser`

Usuários humanos (agentes humanos) membros do grupo.

```
AgentGroupUser
  group_id        UUID FK → agent_groups
  user_id         UUID FK → auth.users
  PRIMARY KEY (group_id, user_id)
```

### `AgentGroupSupervisor`

Supervisores atribuídos ao grupo (sem restrição de turno).

```
AgentGroupSupervisor
  group_id        UUID FK → agent_groups
  user_id         UUID FK → auth.users
  PRIMARY KEY (group_id, user_id)
```

### `AgentGroupShift` (opcional)

Restrição temporal: define qual supervisor está ativo em qual janela de tempo.
Quando não configurado para um grupo, o supervisor é sempre ativo naquele grupo.

```
AgentGroupShift
  shift_id            UUID PK
  group_id            UUID FK → agent_groups
  supervisor_user_id  UUID FK → auth.users
  days_of_week        INT[]    [0=Dom, 1=Seg … 6=Sáb]
  time_start          TIME NOT NULL
  time_end            TIME NOT NULL
  timezone            TEXT NOT NULL DEFAULT 'UTC'
  active              BOOLEAN DEFAULT true
```

**Exemplo:**
```
Equipe SAC → supervisor_a, seg-sex 06:00-14:00, America/Sao_Paulo
Equipe SAC → supervisor_b, seg-sex 14:00-22:00, America/Sao_Paulo
```

---

## Resolução no Login (auth-api)

Executada em `/auth/login` e `/auth/refresh`:

```
1. Buscar todos os grupos onde user_id é supervisor
2. Para cada grupo:
   a. Se grupo tem shifts configurados para este user:
      → filtrar shifts com day_of_week == hoje E time_start ≤ agora ≤ time_end (no timezone do shift)
      → incluir grupo só se há shift ativo agora
   b. Se grupo NÃO tem shifts configurados:
      → incluir grupo sempre
3. Expandir grupos ativos → coletar agent_type_ids (AgentGroupMember)
4. Expandir grupos ativos → coletar user_ids (AgentGroupUser)
5. Embutir no JWT como claims supervisionados
```

**Regra:** admin (`role=admin`) sempre recebe `supervised_groups: []` (vazio = sem restrição).

---

## JWT — Novos Claims

```json
{
  "user_id": "uuid-supervisor",
  "role": "supervisor",
  "tenant_id": "tenant_demo",

  "supervised_groups":      ["grp_abc123", "grp_def456"],
  "supervised_agent_types": ["agente_sac_v1", "agente_retencao_v1"],
  "supervised_user_ids":    ["uuid-humano-1", "uuid-humano-2"],

  "accessible_pools": []
}
```

`supervised_agent_types` e `supervised_user_ids` são **denormalizados** no JWT — evita N+1 em cada query de analytics. Vazio = sem restrição (admin ou supervisor sem grupos configurados).

---

## Impacto por Serviço

### auth-api

**Tabelas novas** (schema `auth`):
- `agent_groups`
- `agent_group_members`
- `agent_group_users`
- `agent_group_supervisors`
- `agent_group_shifts`

**Endpoints CRUD** (autenticados com `X-Admin-Token`):
```
GET    /v1/groups
POST   /v1/groups
GET    /v1/groups/{id}
PUT    /v1/groups/{id}
DELETE /v1/groups/{id}

POST   /v1/groups/{id}/members          body: { agent_type_id, is_human }
DELETE /v1/groups/{id}/members/{agent_type_id}

POST   /v1/groups/{id}/users            body: { user_id }
DELETE /v1/groups/{id}/users/{user_id}

POST   /v1/groups/{id}/supervisors      body: { user_id }
DELETE /v1/groups/{id}/supervisors/{user_id}

POST   /v1/groups/{id}/shifts
PUT    /v1/groups/{id}/shifts/{shift_id}
DELETE /v1/groups/{id}/shifts/{shift_id}
```

**Lógica de login**: adicionar resolução de grupos supervisionados antes de assinar JWT.

---

### analytics-api

Quando `supervised_agent_types` não-vazio no JWT, todas as queries de sessões adicionam filtro via JOIN pré-agregado (compatível ClickHouse 23.8):

```sql
-- Aplicado em _fetch_sessions() quando supervised_agent_types presente:
LEFT JOIN (
    SELECT DISTINCT session_id
    FROM {db}.segments FINAL
    WHERE tenant_id = {tenant_id}
      AND agent_type_id IN ({supervised_agent_types})
) AS _scope ON _scope.session_id = s.session_id
-- WHERE: adicionar AND _scope.session_id IS NOT NULL
```

**Queries afetadas:**
- `GET /reports/sessions` — filtra sessões por agentes do grupo
- `GET /reports/segments` — filtra segmentos
- `GET /reports/agents/performance` — filtra por `agent_type_id IN supervised_agent_types`
- `GET /reports/agent-performance/daily` — idem
- `GET /reports/agent-availability` — idem

**Queries NÃO afetadas** (infra compartilhada, supervisor vê tudo):
- Pool snapshots (`pool_status_get`)
- Métricas de fila

---

### platform-ui / Monitor

Filtragem é **transparente** — feita no backend via JWT claims. O supervisor faz login e automaticamente enxerga apenas o seu escopo.

**Comportamento específico:**
- **Monitor/Sessions**: só sessões com agentes do grupo aparecem
- **Monitor/Agents**: só agentes do grupo listados
- **Monitor/Pools**: pools visíveis por inteiro (infra compartilhada), mas contagens de agentes refletem só os do grupo
- **Console (modo supervisor)**: botão de join desabilitado para sessões fora do escopo do JWT

---

### platform-ui / Config/Groups (nova página)

```
Configurations
  ...
  Groups                    ← nova entrada
```

**UI da página:**
- Lista de grupos com nome, nº de agentes, nº de supervisores
- Drawer para criar/editar grupo:
  - Nome e descrição
  - Tab "Membros": adicionar/remover agent_type_ids (AI) + user_ids (humanos)
  - Tab "Supervisores": adicionar/remover user_ids com role supervisor
  - Tab "Turnos": lista de shifts com dia/hora/supervisor; criar/editar/remover
- Sem exclusão em cascade — remover grupo não afeta agentes ou usuários

---

## Grupos vs Pools — Tabela Comparativa

| Dimensão | AgentGroup | Pool |
|---|---|---|
| Propósito | Gestão de pessoas | Roteamento de contatos |
| Quem define | Admin (RH/operações) | Admin (arquitetura de atendimento) |
| Muda quando | Turnos, reorganizações | Mudanças de skill/canal |
| Afeta | Visibilidade do supervisor | Destino dos contatos |
| Ortogonal? | Sim — um agente pode estar em grupo A e atender pool X, Y, Z | Sim |

---

## Convenções de ID

```
group_id: UUID (gerado pelo banco)
Sem slug — grupos são entidades de gestão sem semântica de routing
```

---

## Escopo de Implementação

| Componente | Esforço estimado |
|---|---|
| auth-api: tabelas + CRUD REST | Médio |
| auth-api: resolução de shift no login/refresh | Pequeno |
| analytics-api: filtro adicional nas 5 queries afetadas | Pequeno |
| platform-ui: Config/Groups (lista + drawer + tabs) | Médio |
| platform-ui: Monitor scope enforcement (transparente) | Mínimo |

**Dependências:** Arc 7 (auth-api, JWT structure) deve estar estável antes de implementar.

**O que NÃO muda:** core de roteamento, estrutura de sessão, pools, Kafka topics, Redis keys.
