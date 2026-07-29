# Avaliação e Proposta — Relatórios de Agentes e Pools

> Criado: 2026-05-31 · Status: **proposta, pendente de implementação**
> Contexto: a página Analytics/Agents hoje mistura conceitos de agente e de pool e
> não separa humano de IA. Esta proposta separa em **dois relatórios** com propósitos
> distintos: (A) **avaliação de agentes** e (B) **dimensionamento de pools/infra**.

## 1. Estado atual (Analytics/Agents) — avaliação

Funciona: o endpoint `/reports/agents/performance` carrega (após o fix do prefixo
`/analytics`→`/reports`) e agrega por `(agent_type_id, pool_id, role)`.

Problemas observados:

- **B1 — Não separa Humano × IA.** As abas "Human Agents" / "AI Agents" compartilham o
  mesmo `perfRows`/`dailyRows` sem filtro de `role`/tipo. Resultado: KPIs idênticos nas
  duas abas; a aba IA lista agent types **humanos** (`human agent retencao humano`), e a
  aba Humano soma IA.
- **B2 — Mistura agente × pool.** O `GROUP BY (agent_type, pool, role)` faz o relatório
  "de agentes" exibir pool como dimensão e duplicar linhas por role (ex.: `agente
  confirmacao portabilidade` aparece 2×: role primary e specialist). Pool deveria ser
  **filtro**, não dimensão, num relatório de agentes.
- **B3 — Agente humano logado não aparece.** A seção "Availability & Pauses" (Arc 8) está
  vazia: só mostraria intervalos de pausa, não o **tempo logado / disponível** nem a
  volumetria do humano. Um humano que atendeu mas não pausou some da avaliação.
- **B4 — Daily Trend vazio.** `/reports/agent-performance/daily` (MV
  `mv_agent_performance_daily`) não retorna dado — investigar população da MV
  (AggregatingMergeTree POPULATE só captura inserts pós-criação; a MV não tem `role`).
- **B5 — Workflows webhook poluem a avaliação.** `agente portabilidade processo`
  (workflow) entra na lista de "agentes" com AHT `—` (duration de segmento webhook) e
  resolution 4.9% (a maioria suspende). Um workflow não é um "agente" a avaliar com as
  mesmas métricas — deveria sair da avaliação de agentes (ou virar categoria própria).

## 2. Princípio — dois relatórios, dois propósitos

| Relatório | Pergunta que responde | Dimensão primária |
|---|---|---|
| **A. Avaliação de Agentes** | "Como cada agente (humano/IA) está performando?" | agente (agent_type_id / usuário humano), separado por tipo |
| **B. Dimensionamento de Pools** | "Preciso de mais recurso em qual pool/canal e quando?" | pool × canal × tempo |

Pool é **filtro** no relatório A; é **dimensão** no relatório B. Agente é dimensão no A;
é (no máximo) detalhe no B.

## 3. Relatório A — Avaliação de Agentes

### Identidade do agente (verificado no código — 2026-05-31)

**`agent_type_id` está deprecated.** A associação **pool ↔ skill** é feita no **deploy do
flow** (`POST /v1/skills/:id/deploy` com `pool_ids` → tabela `skill_deployments`), não pelo
agent_type. O agent_type sobrevive apenas como **plumbing legado** (registry YAML +
`instance_bootstrap` que cria instâncias `{agent_type_id}-{n}` + coluna `agent_type_id` no
`segments`). As identidades **reais** de avaliação são **skill_flow** (IA) e **usuário** (humano).

Campos disponíveis em `segments` hoje: `participant_id`, `instance_id`, `agent_type_id`,
`agent_type` (`human|native|external`), `role`, `pool_id`. **Não há** `skill_flow_id` (flow
deployado) nem `user_login` — esse é o gap a fechar.

- **IA** — a unidade de avaliação é o **skill_flow deployado** no pool, não o `agent_type_id`
  (que é artefato legado). O flow está em `pipeline_state.flow_id` / `workflow_events.flow_id`,
  **não** em `segments`. ⇒ avaliar IA por skill **exige adicionar `flow_id` ao `segments`**
  (via participant event do bridge). Interino: `agent_type_id` é proxy ~1:1 do skill no demo.
- **Humano** — `instance_id`/`participant_id` = **`human-{userId}`** (server.ts:282: instância
  por usuário, compartilhada entre pools). ⇒ avaliação **por pessoa** = agrupar por
  `participant_id` + `pool_id`. O `agent_type_id` humano (`human_agent_{poolId}`, server.ts:342)
  é sintético/legado — **não usar** como identidade (é a causa do `human agent retencao humano`
  aparecer como "agent type" e vazar na aba IA).

Gaps de identidade:
- **`flow_id` (skill) no `segments` ✅ (implementado 2026-05-31)** — coluna `flow_id` no
  `segments` (+ migração `ADD COLUMN IF NOT EXISTS`); o bridge emite `flow_id` (de
  `agent_result.pipeline_state.flow_id`) no `participant_left` (process_routed + resume);
  parser/insert do consumer atualizados. IA passa a ser avaliável pela **skill deployada**.
  *(Vale só para segmentos novos; humanos têm `flow_id=''`.)*
- **`user_login` amigável** — `participant_id` é `human-{userId}` (UUID); lookup no auth-api
  (`user_id → login/email`) para exibir nome. Hoje só o UUID no dado.

Dimensão do relatório: **humano → usuário (`participant_id`) × `pool_id`** (resolver
user_id→login); **IA → skill_flow × `pool_id`** (requer `flow_id` no segmento; interino:
`agent_type_id`). **Separar humano de IA** por `agent_type` (`human` vs `native`).
**Excluir** pools webhook (workflows) — ou categoria "Workflows" à parte. **Não usar
`agent_type_id` como eixo de avaliação** (deprecated).

Métricas e fonte (ClickHouse):

- **Volumetria** — nº de atendimentos (`segments` por agente), sessões distintas,
  atendimentos/dia (série temporal). Fonte: `segments`, `mv_agent_performance_daily`.
- **Tempo de atendimento** — AHT (`avg(segment.duration_ms)`), tempo total. Fonte:
  `segments`. *(Para humano, duration de segmento = tempo real de atendimento.)*
- **Disponibilidade (humano)** — **pausa** por motivo (`agent_pause_intervals` ✅ existe).
  ⚠️ **Tempo logado e % ocupação NÃO existem**: o consumer **descarta** `agent_login`/
  `agent_logout` (models.py:392) — só pausas e `agent_done` são persistidos. Construir =
  mini-fase 1b: tabela `agent_login_intervals` (login→logout, espelhando
  `agent_pause_intervals`) + handler no consumer + endpoint. Eventos já são publicados pelo
  mcp-server (`agent_login`/`agent_logout`), só não são consumidos.
- **Qualidade** — `resolution_rate`, `escalation_rate`, `handoff_rate`
  (`segments.outcome`); **score de avaliação** (`evaluation_results`, Arc 6); **sentimento**
  médio das sessões do agente (`sentiment_events`).
- **Performance score** (Arc 7d) — `resolution_rate × (1 − escalation_rate)`.

Correções: filtro por tipo (B1); pool vira filtro (B2); incluir tempo logado mesmo sem
pausa (B3); popular daily trend (B4); segregar workflows (B5).

## 4. Relatório B — Dimensionamento de Pools / Infra *(novo — não existe hoje)*

Dimensão: `pool × channel × bucket de tempo`. Objetivo: dimensionar recurso por tráfego.
Hoje só existe `Monitor/Pools` (snapshot **live**); não há analítico **histórico**.

Métricas e fonte:

- **Volumetria de contatos** — sessões por pool×canal×período (chegada =
  `sessions.opened_at`). Fonte: `sessions`.
- **Tráfego ao longo do tempo** — série temporal de chegadas por canal/pool (distribuição
  horária, picos, dia da semana). Fonte: `sessions` (já há `/reports/timeseries/volume`,
  estender por pool/canal).
- **Comportamento de fila** — tempo de espera (`estimated_wait_ms`/`sessions.wait_time_ms`,
  p50/p95), tamanho de fila ao longo do tempo (`queue_position`), **taxa de abandono**
  (`queue_events.event_type='abandoned'`), `available_agents` no tempo. Fonte:
  `queue_events`, `sessions`.
- **Ocupação / concorrência** — sessões **simultâneas** por pool×canal (sobreposição de
  `participation_intervals`/`segments`) → pico de concorrência. Fonte:
  `participation_intervals`, `segments`.
- **SLA attainment** — % de contatos dentro do `sla_target` do pool (`wait_time` vs target).
- **Utilização de capacidade** — concorrência pico ÷ capacidade configurada (instâncias ×
  `max_concurrent`, ou `max_concurrent_sessions` para webhook) → **headroom** para
  dimensionamento. Fonte: `participation_intervals` + snapshot de capacidade.

Gap de endpoint: nenhum dos acima é exposto analiticamente. Endpoints novos sugeridos:
`/reports/pools/volume`, `/reports/pools/queue`, `/reports/pools/occupancy` (ou um único
`/reports/pools/timeseries` com `metric=`), todos com `pool_id?`/`channel?`/`bucket=hour|day`.

## 5. Decisões (2026-05-31)

- **Quality**: o relatório de agentes é o **quantitativo**; o qualitativo fica no
  `analytics/quality` (a definir). Por ora, apenas **link** do agente → Quality, sem
  duplicar score aqui.
- **Workflows webhook**: **fora** do relatório de agentes (não são agentes a avaliar).
  Excluir pools com `channel_types=[webhook]` da query.
- **Identidade**: IA = `flow_id` (skill) × pool ✅ (flow_id já no segments); humano =
  `participant_id` (usuário, `human-{userId}`) × pool, com lookup `user_id → login` no auth.
- **`flow_id` no segments**: ✅ implementado.

## 6. Plano de atividades

- **Fase 1 — relatório de agentes** (`reports_query` + `AnaliseAgentesPage`): separar
  humano/IA por `agent_type`; humano por usuário×pool (lookup login), IA por `flow_id`×pool;
  excluir webhook; daily trend de `segments` por dia (não a MV); abas com dados distintos;
  link → Quality. *(Sem tempo logado/ocupação até a Fase 1b.)*
- **Fase 1b — tempo logado / ocupação (dado novo)**: tabela `agent_login_intervals`
  (login→logout) + handler no consumer p/ `agent_login`/`agent_logout` (espelha o de pausas)
  + endpoint; ocupação = tempo atendendo ÷ logado. Habilita os KPIs/colunas de disponibilidade.
- **Fase 2 — relatório de Pools/Infra** (novo): endpoints `/reports/pools/{volume,queue,occupancy}`
  a partir de `queue_events`/`sessions`/`participation_intervals` + aba Analytics/Pools
  (séries temporais + SLA/headroom).
- **Fase 3 — migrar provisionamento do demo para Config + Deploy** (elimina YAML): hoje o
  ambiente é montado por `infra/registry/*.yaml` + RegistrySyncer (pools + agent_types + skills)
  e PUT de skills. Alvo: criar pools/skills via Config API + UI (`config-recursos/PoolsPage`)
  e associar skill↔pool via **deploy** (`POST /v1/skills/:id/deploy` com `pool_ids`),
  aposentando os arquivos e o `agent_type` legado (humano/webhook). Refactor de
  provisionamento — separado dos relatórios, mas relacionado à depreciação do agent_type.

## 7. KPIs já disponíveis no ClickHouse (inventário)

`sessions` (volume, wait/handle time, outcome, channel, pool), `segments` (participação por
agente: role, duração, outcome, handoff), `queue_events` (fila: posição, espera estimada,
agentes disponíveis, abandono), `agent_pause_intervals`
(Arc 8), `participation_intervals` (janelas de participação — concorrência), `sentiment_events`,
`evaluation_results`/`evaluation_events` (Arc 6), `usage_events` (metering por dimensão),
`agent_business_events` (Arc 12), `mv_agent_performance_daily`, `mv_segment_summary`.
