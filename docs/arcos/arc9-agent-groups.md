# Arc 9 — Agent Groups & Supervisor Scope

> Última atualização: 2026-07-02 · Estado: Arc 19 · Status: Arc 9 implementado (Members/Shifts removidos)

> **Arc 9 implementado** — `GroupsPage` (`/config/groups`), `groups_router.py` (CRUD de grupos + sub-recursos), claims JWT (`supervised_groups`, `supervised_user_ids`) e os filtros de escopo em analytics-api estão em produção. Este documento descreve o desenho original; o estado vigente está consolidado no CLAUDE.md § Arc 9.
>
> **⚠️ Remoção (2026-07-02) — sub-recursos "Agents" (`AgentGroupMember`) e "Shifts" (`AgentGroupShift`) removidos.**
> Motivo: (1) `AgentGroupMember.is_human` era uma segunda fonte de verdade não-validada para humano/IA,
> digitada como texto livre na UI (`agent_type_id`) sem referência à entidade real — `Pool.agent_kind` já é a
> fonte canônica (Config > Resources > Pools). (2) Turnos diferentes (`AgentGroupShift`) agora são modelados
> como **grupos diferentes**, não como janelas de horário por membro dentro de um único grupo — simplifica o
> modelo sem perder a capacidade (basta criar "Equipe SAC Manhã" e "Equipe SAC Tarde" como grupos distintos).
> (3) A associação grupo↔usuário já é editável direto no formulário do usuário em `Configuration > Access`
> (seção "Group association"), então a aba Members do Group não precisa de referência cruzada. Ver §
> "Remoção de Members/Shifts" abaixo para o estado atual e o impacto em `supervised_agent_types`. As seções
> a seguir (Entidades, JWT, Impacto por Serviço) descrevem o desenho **original** — mantidas como histórico,
> não refletem mais o código.

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

---

## Remoção de Members/Shifts (2026-07-02) — estado atual

**Entidades removidas do código** (tabelas físicas podem seguir existindo em bancos antigos — não houve
`DROP TABLE`, só `ensure_schema()` parou de criá-las/lê-las/escrevê-las):
- `AgentGroupMember` (`agent_group_members`) — endpoints `POST/DELETE /v1/groups/{id}/members` removidos;
  `db.add_group_member`/`remove_group_member`/`list_group_members` removidos.
- `AgentGroupShift` (`agent_group_shifts`) — endpoints `GET/POST/PUT/DELETE /v1/groups/{id}/shifts*`
  removidos; `db.create_group_shift`/`update_group_shift`/`delete_group_shift`/`list_group_shifts`
  removidos.

**`resolve_supervisor_scope()`** simplificado — agora `(user_id, role) → (supervised_groups, supervised_user_ids)`
(era uma tupla de 3, `supervised_agent_types` incluído). Sem expansão de `agent_type_id`, sem gate de janela
de horário — grupo supervisionado é sempre ativo enquanto o usuário for supervisor dele.

**JWT** — claim `supervised_agent_types` deixou de ser emitido. `analytics-api` (`pool_auth.py`) já lia esse
claim via `payload.get("supervised_agent_types", [])` com fallback `[]` → segue funcionando sem mudança,
tratando a ausência como "sem restrição" (mesma semântica de admin). `_apply_agent_scope`/
`_agent_scope_session_join` em `reports_query.py` continuam existindo no código (não removidos) mas nunca
mais recebem uma lista não-vazia — viram no-op permanente. `accessible_pools` (Arc 7) continua aplicando
seu próprio filtro por pool nos mesmos endpoints, em paralelo — a camada de escopo por pool não foi afetada.

**GroupsPage** (`platform-ui`) — drawer com 3 abas apenas: Info, Members (`agent_group_users`, humanos),
Owners (`agent_group_supervisors`). `member_count` na listagem agora conta `agent_group_users` (era
`agent_group_members`).

**Consequência prática:** `supervised_agent_types` não filtra mais nada — supervisores enxergam todos os
agent_types dentro dos pools que já têm acesso via `accessible_pools`. Quem precisar restringir visibilidade
por turno/horário deve criar grupos separados (ex.: "Equipe SAC Manhã" / "Equipe SAC Tarde") em vez de
configurar shifts dentro de um único grupo.

---

## Associação Group ↔ User pela tela do usuário (2026-06-28)

Além da associação pelo lado do grupo (`GroupsPage`), a `AccessPage` (`/config/access`) tem uma seção
**Grupos** no modal do usuário com colunas **Membro** (→ `agent_group_users`) e **Owner** (→
`agent_group_supervisors`). Como não existe rota reversa "grupos do usuário", o modal sonda o detalhe
de cada grupo (`GET /v1/groups/{id}`) para descobrir o estado atual; o diff é aplicado no Save via os
mesmos endpoints de sub-recurso do `groups_router`. Escopo de owner é denormalizado no JWT no
login/refresh — mudança de grupo só reflete no próximo token, e a UI avisa isso.

---

## Escopo de visibilidade — Pool × Group é interseção de filtros independentes, não correlação

**Único consumidor real de `supervised_user_ids` hoje: `evaluation-api`** (`_compute_result_scope` /
`list_results`), para restringir **de quem** um supervisor pode ver resultados de avaliação de
qualidade. A query aplica dois `WHERE` com `AND`:

```sql
WHERE r.tenant_id = $1
  AND r.evaluated_user_id = ANY($evaluated_user_ids)   -- QUEM (via grupo supervisionado)
  AND c.pool_id = ANY($accessible_pools)                -- QUAL POOL (Arc 7)
```

**Isto É uma interseção de linhas — mas NÃO é uma correlação pool↔grupo.** Os dois filtros são
resolvidos de forma totalmente independente e não se conhecem:

- `evaluated_user_ids` = achatamento de **todos os `user_ids`** de **todos os grupos** onde o supervisor
  é `owner` (`agent_group_supervisors` → expande `agent_group_users`). Não carrega "este usuário está
  aqui por causa do grupo X" — é só uma lista flat de pessoas supervisionadas.
- `accessible_pools` filtra pela coluna `pool_id` da campanha/sessão, sem nenhuma referência a Group.
- **`AgentGroup` não tem (nem nunca teve) um campo de Pool** — é órfão de pool por desenho (ver
  "Grupos vs Pools — Tabela Comparativa" acima: "um agente pode estar em grupo A e atender pool X, Y,
  Z"). Não existe no schema um jeito de dizer "esta avaliação/sessão pertence ao grupo g_a".

**Consequência**: se um supervisor é `owner` de **múltiplos grupos** (ex.: `g_a` e `g_b`) e tem
`accessible_pools` com mais de um pool, ele enxerga o usuário supervisionado por **inteiro** dentro de
todos os pools a que tem acesso — não há como restringir "só a parte do trabalho desse usuário que caiu
sob o grupo g_a especificamente". Group escopa **quem** é visível; Pool escopa **onde**; não existe hoje
um terceiro filtro que amarre os dois por item de trabalho.

**Por que isto não é uma falha de segurança**: `accessible_pools` é a fronteira dura, **sempre aplicada
independente de role** — inclusive para admin (`role=admin` zera o filtro de `evaluated_user_ids`, mas
`accessible_pools` continua restringindo se setado). Group nunca amplia visibilidade além do que o pool
já permite; ele só restringe **quem**, dentro da fronteira que o pool já define. O gap de granularidade
(grupo não sub-filtra dentro do pool) só vira relevante se Group um dia passar a ter significado
por-pool — hoje ele é deliberadamente órfão de pool, então a imprecisão é esperada, não um bug.
