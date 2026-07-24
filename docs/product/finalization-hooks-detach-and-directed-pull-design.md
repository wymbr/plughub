# Design — Detach de hooks de finalização + Pull direcionado + ACW

**Status:** Desenho fechado (2026-07-23), implementação iniciada (Camada A).
**Componentes:** `packages/schemas` (`PoolHooks`), `packages/orchestrator-bridge` (barrier de fecho + disparo),
`packages/routing-engine` (pull direcionado + gate de ACW), `packages/channel-gateway` (handle_collect/trigger),
`packages/platform-ui` (`PullInboxPanel`), skills de survey/wrap-up.
**Relacionado:** [`docs/arcos/session-conference-lifecycle.md`](../arcos/session-conference-lifecycle.md) (G1, G7),
[`docs/guias/pool-hooks.md`](../guias/pool-hooks.md), [`docs/guias/conference-mechanics.md`](../guias/conference-mechanics.md),
[`docs/adr/adr-human-approval-workflow-step.md`](../adr/adr-human-approval-workflow-step.md) (pull inbox), Journey J1–J5.

---

## Motivação

Hoje o survey de fim de contato tem **duas formas de coleta**: (A) **delegate** legado (`skill_survey_v1` +
`agente_survey_nps_v1` no pool `survey_collector_ia`, F10.2b) e (B) **collect** (J4c, `skill_survey_outbound_v1`
+ `skill_survey_runner_v1`). A Forma A existe por um motivo estrutural: **hooks de finalização não podem
suspender/collect** — o bridge segura o `_trigger_contact_close()` (contador `hook_pending`) até os hook agents
concluírem, e trata `suspended` como `agent_done` → fecha o contato antes de a coleta renderizar. Por isso o NPS
inline (`agente_nps_v1`) usa menu inline, e a coleta assíncrona virou workflow separado.

A razão real de **segurar o contato** é **atribuição**: fazer a ação de finalização atribuível ao segmento do
atendente. Mas a atribuição fina já é feita por **referência carregada** (`surveyed_segment_id`/`agent_key`
carimbados no ContextStore pré-hook; `survey_record(grain=segment, …)` chaveia por eles), **não** por o survey
ser fisicamente um segmento da conferência. O que segura o contato de fato é só a **infra viva** (WS do cliente,
contexto). **Com a Journey de volta (J1–J3, `root_session_id`),** a associação migra de "mesmo segmento da
conferência" para "membro da mesma journey + referência de segmento no payload".

## Objetivo

Reduzir de **três** mecanismos de coleta (inline, delegate, collect) para **dois** com fronteira limpa:
- **inline** — só o caso síncrono "pega o cliente enquanto o WS está vivo" (NPS de fim de contato presente).
- **collect** — todo o resto (outbound, reconnect, journey N3, assíncrono), sempre em sessão perfil `workflow`.

E **aposentar a Forma A (delegate)**. Bônus: fecha follow-ups já mapeados —
- **G1** (AHT inflado pelo tempo de wrap-up): destacar congela as estatísticas na saída do cliente.
- **G7** (`on_human_end` desacoplado só no caso `agent_transfer`): generaliza o desacoplamento a todo hook de
  finalização.
- Some o follow-up de pool-scoping do survey delegate (item 2 do arco de Segurança): sem caminho delegate, tudo
  passa por `handle_collect`, que já resolve o pool.

## Invariante preservada — pool é a unidade endereçável

O "ramal" (endereçar um recurso específico) **não** vira um alvo de roteamento. O trabalho direcionado é sempre
um **work item que mora num pool (fila)** com um **filtro de elegibilidade de claim** (`assigned_to`) e
**fallback pro pool** por lease. O pool continua sendo a unidade endereçável; a reserva é preferência com
validade. Paralelo PABX exato: **fila = pool + dispatch**; **ramal = pull item com `assigned_to` + overflow para
a fila** ("toque no ramal, transborda pra fila"). Nunca se endereça um recurso nu (o que reintroduziria
fragilidade de disponibilidade/ponto único que o modelo de pool fecha).

Esse "pull direcionado" é o **embrião** de transfer-to-agent (transferência a um atendente específico) — que no
futuro reusa o mesmo mecanismo (inbox pull do recurso + fallback), sem quebrar o invariante.

---

## Modelo

### `dispatch: inline | detached` (por hook, Camada A)

Atributo no `PoolHookEntry`. Default `inline` (retrocompat). `detached` só em hooks de FINALIZAÇÃO
(`on_human_end` / `on_contact_end` / `on_process_end`) — guard de parse rejeita em `on_human_start`.

- `inline` → bridge segura o fecho (`hook_pending`), roda na conferência viva. Reservado ao caso que precisa do
  WS do cliente.
- `detached` → bridge NÃO segura: dispara via `workflow_trigger` (fire-and-forget) com `origin_session_id`
  (herda `root_session_id` → membro da journey) + referência de segmento no contexto, e fecha o contato na hora.

### Fluxo do wrap-up destacado

1. Humano fecha o segmento (`agent_done`). Hook `on_human_end` com `dispatch: detached`.
2. Bridge dispara o wrap-up como **work item na fila pull** (não segura o contato) com: `assigned_to = user_id`
   do atendente, `segment_id`/`agent_key`/`origin_session_id`, briefing (transcrição/verbatim/copilot da origem),
   `lease`/SLA + `fallback_to_pool_after`.
3. Contato fecha na hora (estatísticas congeladas → G1).
4. O item aparece na `PullInboxPanel` **só do atendente** (`assigned_to` casa). Claim atômico → DialogForm de
   disposição → submete → gravado contra o segmento pela via de atribuição carregada.
5. `acw_gate` decide o efeito na disponibilidade (ver Camada C).
6. Sem claim no lease → transborda pro grupo/pool (team-claimable). Sem órfão.

### ACW = regra de elegibilidade de roteamento (não "segurar o contato")

O ACW deixa de ser "contato aberto segurando o atendente" e vira uma regra sobre a disponibilidade:
`acw_gate: none | soft | hard` (por pool).
- `none` — não bloqueia; wrap-up é backlog no inbox.
- `soft` — atendente segue disponível; supervisor vê pendências.
- `hard` — Routing Engine **não roteia novo contato** enquanto houver wrap-up pendente daquele `user_id`
  (`agent_ready` efetivamente gated). O ACW bloqueante clássico é preservado, mas enforçado no roteamento — o
  contato fecha honesto; o que "prende" o atendente é o backlog dele.

---

## Camadas de implementação

- **Camada A — fundação (iniciada):** `dispatch: inline|detached` no `PoolHooks` (schema `@plughub/schemas`),
  default `inline`, + guard de parse (rejeita `detached` em `on_human_start`). Rebuild de todo serviço que valida
  skills/pools (agent-registry) + engine + mcp-server.
- **Camada B — pull direcionado ("ramal"):** work item com `assigned_to` (recurso preferido) +
  `fallback_to_pool_after` (transbordo); claim-eligibility no Routing Engine (`work_queue`/`dispatch_mode: pull`).
  Wrap-up = 1º consumidor. **Fronteira:** `assigned_to` é elegibilidade de claim sobre trabalho *pooled*, com
  fallback — nunca alvo de roteamento que bypassa o pool.
- **Camada C — ACW:** `acw_gate: none|soft|hard` como regra de `agent_ready` no Routing Engine.
- **Camada D — bridge:** honrar `detached` (não incrementa `hook_pending`; `workflow_trigger(origin + segment
  ctx)`; fecha o contato). **Atualizar `docs/guias/conference-mechanics.md` § Histórico** (regra da casa para
  qualquer mudança no mecanismo de conferência).
- **Camada E — migração:** `agente_wrapup_v1` e o survey → `detached`; NPS síncrono continua `inline`. Aposentar
  `skill_survey_v1` + `agente_survey_nps_v1` + pool `survey_collector_ia` (Forma A).
- **Camada F — validação:** G1 (AHT deixa de inflar); atribuição de segmento no relatório; smoke do wrap-up na
  pull inbox (claim direcionado + fallback); pool-scoping do survey sem caminho delegate.

## Decisões fechadas (2026-07-23)

1. **Wrap-up destacável, mas config** — não impor. `inline` = ACW bloqueante clássico; `detached` = tarefa
   assíncrona na pull inbox.
2. **Claim direcionado com fallback** — o wrap-up é pessoal (autoria de quem atendeu); `assigned_to = user_id`,
   com transbordo team-claimable por lease. Não é fila compartilhada pura.
3. **`acw_gate` configurável, default `none` no `detached`** — o ponto do detach é não segurar; quem quer ACW
   bloqueante usa `hard` (ou mantém `inline`).
4. **Pull direcionado como primitivo geral** (não campo específico de wrap-up) — forward-compatível com
   transfer-to-agent. Invariante do pool preservado.
5. **Hooks não-finalização (`on_human_start`) ficam como estão** — não têm existência independente da sessão que
   os invocou; `detached` proibido no parse.

## Não-objetivos

- Endereçamento-de-recurso como alvo de roteamento (bypass do pool). Fora — o "ramal" é reserva-com-overflow.
- Transfer-to-agent — só se registra que a Camada B é o caminho; não entra neste arco.
- Mudar o NPS síncrono presente — continua `inline` (restrição física do WS do cliente).
