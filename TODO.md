# TODO — PlugHub Itens Pendentes

> Itens genuinamente não implementados. Histórico de implementações concluídas em `CHANGELOG.md`.

---

## Webhook pools — eliminar capacidade fictícia (default 500)

Decisão 2026-06-03 (discussão da admissão híbrida, `docs/arcos/queue-attended-model.md`):
pool webhook não pré-instancia nada (slots de skill-flow são lógicos, execução on-demand),
então `max_concurrent_sessions = 500` é capacidade fictícia — o recurso real que limita
sessões webhook é a **admissão** (reserva ou shared), igual a todo pool.

- Remover o `default(500)` do schema de deploy webhook.
- `max_concurrent_sessions` em webhook vira **throttle opcional de downstream**
  (backpressure p/ sistemas frágeis, ex. ERP): `available = max − busy` só quando
  configurado; ausente → admissão é o único teto.
- Ajustar `registry.py` (routing) e Monitor (exibição de capacidade webhook).

> **Ressalva**: re-validar esta lógica quando retomarmos — conferir impactos no Arc 19
> (alocação webhook, Bootstrap, Monitor/Pools que exibe capacidade configurada) antes
> de implementar. Mudança de comportamento; não misturar com o smoke da Fase B.

---

## Delegate v2 — itens restantes (pós-correção do ciclo de portabilidade)

Modelo corrigido e backend verde em [`docs/arcos/delegate-workflow-io.md`](docs/arcos/delegate-workflow-io.md)
(delegate sempre roda o alvo como segmento conference do chamador; A-new fecha como webchat;
`context_set` registrado; specialist de B adia instantâneo). Restam:

- **Fase C — heurística de canal na UI (`platform-ui` `ListaTab.tsx`)**: classificar a sessão
  por `channel_type` real, não pela presença de step `delegate`/`suspend`. Hoje uma sessão
  webchat que usa `delegate` é renderizada como workflow/webhook. Badge de status deve derivar
  de participantes vivos (sessão com specialist ativo lê `active`, não `suspended`).
- **Fase D — timeout scanner do delegate**: quando B fica `suspended`
  (`awaiting_customer_inbound`) e o cliente nunca reconecta, a `pending_workflow` key fica
  pendente para sempre. Implementar scanner que, ao estourar o timeout final, dispara
  `workflow_resume` com `decision=timeout` → B `on_timeout` → fecha como failed/timeout.
- **Fase E — Workflow Execution Trace (step-level)** ✅ (E.1/E.2/E.3 + transcript):
  step timeline já renderiza; `step_io` com `decision`/`payload`/`child_session_id` por step
  (E.1); `resumed_by` por step (E.3); duration webhook = tempo decorrido total (E.2);
  transcript do specialist via clique no nó de agente (já existia). Design em
  `docs/arcos/delegate-workflow-io.md` § Fase E.
  - **E.4 diferido (sem dado no demo)**: (a) **MCP audit** por step — `skill-flow-service`
    chama o mcp-server via cliente cru, não pelo `McpInterceptor`, então os `invoke` não
    geram `mcp.audit`; construir quando a execução passar pelo interceptor. (b)
    **agent_events** (Arc 12) — agentes de portabilidade não emitem. (c) snapshot de
    ContextStore com evolução entre suspends (hoje só o estado atual no strip Input context).
    (d) duration "corridas vs úteis" (business_hours) lado a lado.

## Relatórios analíticos — Agentes e Pools

Avaliação + proposta em [`docs/arcos/analytics-reports-redesign.md`](docs/arcos/analytics-reports-redesign.md).
Hoje o Analytics/Agents mistura agente×pool e não separa humano×IA.

- **Fase 1 — relatório de agentes**: humano por usuário×pool (lookup login), IA por
  flow_id(skill)×pool; abas distintas; excluir webhook; daily trend de segments; link→Quality.
  (`reports_query` + `AnaliseAgentesPage`.) `flow_id` no segments ✅.
- **Fase 1b — tempo logado/disponibilidade ✅** (2026-06-02): tabela `agent_login_intervals`
  + máquina de estados no consumer (reusa agent_ready/agent_login → abre, agent_logout → fecha;
  Redis `{tenant}:login:{instance}`); endpoint `agent-availability` reescrito por instance_id
  (logged_ms/available_ms/user_login) + donut de motivos no `AgentsTab`. Ver `CHANGELOG.md`.
  Derivados ✅: **ocupação** (busy dos segments ÷ disponível) e **gestão de motivos de pausa**
  (i18n) — ambos concluídos 2026-06-02, ver `CHANGELOG.md`.
- **Timeline do agente — presença por pool ✅** (2026-06-02): tabela `agent_pool_intervals`
  (diff de `pools[]` no consumer) + endpoint `/reports/agent-timeline` + componente
  `AgentTimeline` (swimlanes: Total + faixa por pool, overlay de pausas) com drill-down da
  tabela de Disponibilidade. Ver `CHANGELOG.md`. Precisão por pool é aproximada (atribui o
  intervalo inteiro a cada pool tocado); sub-intervalos exatos por pool = refinamento futuro.
- **Pausa — persistência através de reconnect ✅** (2026-06-02): key durável
  `{tenant}:agent_paused:{instanceId}` (pause grava TTL 16h, resume deleta); `registerHumanAgent`
  e o heartbeat passam a carregar `status=paused` quando a key existe → o routing mantém
  `state=paused` (alocação exige `state=="ready"`, linha 161/652 do registry) → agente continua
  excluído sem cirurgia em sets; novo `GET /api/agent-state` + a UI lê ao montar (botão reflete
  a realidade). TTL por motivo (`max_minutes`) + logout explícito limpa a key (`POST
  /api/agent-clear-pause`). Órfã resolvida: no `agent_logout` o analytics fecha a pausa aberta
  **só** quando a key durável está ausente (= logout explícito), distinguindo de navegação. Ver `CHANGELOG.md`.
- **Pausas — gestão de motivos ✅/decidido** (2026-06-02): a pausa é do **agente** (remove de
  todos os pools), então motivo **por pool é semanticamente errado** — lista global é o correto.
  Config UI de cadastro descartada por overkill (Config API `pause_reasons` semeável + fallback de
  defaults já cobre); seletor de motivo já existe (`PauseReasonModal`). Único ajuste feito:
  **i18n** dos motivos default + textos do modal (seguiam fixos em pt-BR) → namespace `agentAssist`
  seção `pause` (en + pt-BR). Labels do Config API permanecem como configurados pelo tenant.
- **Fase 2 — relatório de Pools/Infra (novo)**: pool×canal×**endpoint**×tempo — volumetria,
  fila (espera/tamanho/abandono/disponíveis), concorrência vs capacidade (headroom), SLA.
  **Spec/ADR registrado** em [`docs/arcos/pools-infra-report.md`](docs/arcos/pools-infra-report.md)
  — decisões fechadas: (a) concorrência via contadores no Routing Engine (pool + total, Redis,
  carry-over no fechamento do bucket; `peak_total` ≠ soma dos max por pool); (b) capacidade =
  configurada no pricing (snapshot reservado à Fila); (c) volume com dimensão `endpoint`=DNIS
  (Arc 19). Pendente: Routing Engine (contadores+flush), 3 endpoints `/reports/pools/*`,
  pricing expõe capacidade, aba `Analytics/Pools`.
  **Atualização 2026-06-03**: Fila/SLA reescritos sobre segments `role='queue'` + demanda
  reprimida no Volume (queue-attended-model Fase D ✅, ver `CHANGELOG.md`).
  **Atualização 2026-06-04**: dívida `sessions.sla_target_ms` resolvida ✅ (ver
  `CHANGELOG.md`) — aba SLA popula a partir dos contatos novos; sessões históricas
  permanecem NULL (valor nunca foi persistido, irrecuperável).
- **Queue-attended-model — residuais pós Fase E** (2026-06-03, ver spec): (a) **render v2
  webchat** para message.text de sistema — fila muda e rejeição outage fecham sem mensagem
  (webchat não implementa `deliver_text`; caminho do flow cobre só fila atendida);
  (b) limpar `queue_config`/`session_reservation` via PUT (Zod rejeita null — hoje SQL);
  (c) cenários fila muda e drop sem pool_id não exercitados em teste.
- **Reformulação Analytics/Agents — Bancada de comparação 360° (novo)**: reescreve a aba como
  bancada de comparação (média dos agentes × indivíduos), unificando quantitativo + qualitativo
  (Arc 6) + voz do cliente (NPS/pesquisa) + voz do agente (wrap-up) na mesma entidade `agent_key`.
  **Spec/ADR** em [`docs/arcos/analytics-agents-workbench.md`](docs/arcos/analytics-agents-workbench.md)
  — decisões fechadas: média aritmética rotulada "média dos agentes" + N; comparabilidade por
  domínio de métrica (desabilita no seletor); camada `session_signal` (NPS/wrap-up/pesquisa via
  Arc 12 + journey, `session_at`×`captured_at`, normalização por pool); detalhe type-aware;
  cruzamento das vantagens (concordância/quadrante) + calibração do avaliador (Arc 13).
  Pré-requisitos: outcome humano no segment; join avaliação→agente. Ordem em §12 do spec.
- **Fase 3 — migrar provisionamento do demo para Config + Deploy** (elimina YAML/agent_type):
  - **3b / 3a / 3c / 3d-parcial — concluídas** — ver `CHANGELOG.md` (2026-05-31, 2026-06-01)
    e `docs/arcos/instance-bootstrap.md`. Pools IA migrados; `mention_commands` via embed no
    flow; slots vêm do `deploy:` de cada pool (boot limpo OK); agent_types IA aposentados do
    YAML (só o human resta, prune limpa o registry); reconcile deploy-only; hack
    `_applyMaxConcurrentSessions` e builder legado `_build_desired_state` removidos.
  - **Fase C — rename em massa DESCARTADO** (1198 ocorrências/136 arquivos, semanticamente
    errado p/ humano); `agent_type_id` permanece como carrier. Re-escopada em C1/C1b/C2/C3:
    - **C1 ✅** (2026-06-01): identidade do agente humano por `user_id`/`user_login` (login)
      nos segments — threading platform-ui→mcp-server→routing-engine→bridge→analytics; colunas
      no ClickHouse; exibição na lista e detalhe de Analytics/Sessions. Ver `CHANGELOG.md`.
    - **C1b-A ✅** (2026-06-01): Analytics/**Agents** — `_fetch_agent_performance` agrupa humano
      por `user_id` (display `user_login`), IA por `flow_id`; abas Human/AI com tabela de
      performance própria e KPIs filtrados. Ver `CHANGELOG.md`.
    - **C1b-B ✅** (2026-06-02): daily trend por identidade — `_fetch_agent_performance_daily`
      reescrito para ler `segments` direto (humano por `user_id`, IA por `flow_id`), sem
      depender da MV `mv_agent_performance_daily` (que colapsa humano por `agent_type_id`);
      `AnaliseAgentesPage` filtra `tabDailyRows` por `agent_type` por aba. Fix colateral: stroke do
      TrendChart usava `var(--color-*)` inexistente → linhas invisíveis (bug pré-existente mascarado
      enquanto o endpoint daily não trazia dado) → trocado por hex dos tokens. Ver `CHANGELOG.md`.
      Pendente derivado → **Fase 1b** (availability/pauses vazio no humano; outcome humano = 0%).
    - **C2/C3/C4 ✅** (2026-06-01): entidade `AgentType` **REMOVIDA** (tabelas `agent_types` +
      `agent_type_pools` dropadas via `prisma db push`). As UIs de CRUD eram código morto (não
      roteadas) → deletadas sem migração. mentionable-agents/delegation/agent_login repontados
      p/ deploy slots/skills. Ver `CHANGELOG.md`.
    - **Cleanup residual** (inofensivo, dead code — varrer quando der): `_sync_agent_type`/
      `_prune_agent_types` (registry_syncer.py, sem chamador); Path A `elif framework=="human"`
      (main.py, inalcançável); `AgentTypeSchema` (@plughub/schemas) + `validators/agent-type.ts`
      órfão. Testes do agent-registry que referenciavam agent_type foram deletados; revisar a
      suíte se reativar CI.

---

## Copilot @mention standby — corrida na chave `menu:result` session-scoped

**Bug pré-existente, independente do deploy-driven** (descoberto ao migrar `copilot_sac`,
mas reproduz igual no caminho legado por `agent_type`). O step `aguardar` do
`skill_copilot_sac_v1` é um `menu` `agents_only` com `timeout_s: -1` (standby até receber
`@copilot ativa`). O engine suporta `-1` (`menu.ts` linha 84 → BLPOP timeout 0 = infinito),
mas o BLPOP observa chaves **session-scoped** (`menu:result:{sessionId}` e
`session:closed:{sessionId}`, `menu.ts` linhas 172-173). Num conference com agente humano
*primary* ativo, cada mensagem do humano (incluindo o próprio texto `@copilot ...`) cai em
`menu:result:{sessionId}` e estoura o standby do copilot na entrada → segmento de **0s** →
`agent_done` apaga o `specialist_key` → a próxima mention cai em "specialist not active →
new invite" (re-convite em loop, nunca despacha o comando).

Sintoma confirmado: segmentos `skill copilot sac` de 0s em Analytics/Sessions; log do bridge
repetindo `mention_routing: specialist pool=copilot_sac not active ... new invite`.

A migração deploy-driven do copilot está OK (provisionamento, síntese, `specialist_key` com
`skill=skill_copilot_sac_v1`, e `mention_commands` round-trip pela agent-registry — todos
validados). O que falta é o standby segurar.

Opções de fix (a decidir):
- **Chave de standby instance-scoped para specialist**: o `aguardar` do copilot deveria
  bloquear numa chave `menu:result:{sessionId}:{instanceId}` (ou dedicada ao specialist),
  isolando-o do tráfego do *primary*. O `dispatch_mention_command` LPUSH já mira
  `menu:result:{sessionId}` — precisaria mirar a chave instance-scoped do specialist ativo.
- **Trocar o standby de `menu` por `receive`**: o step `receive` já bloqueia em chave própria
  (`receive:result:{sid}:{iid}`) e suporta `-1` nativo; reescrever o `aguardar` como `receive`
  + adaptar o dispatch. Mais alinhado ao propósito (escutar sinal, não renderizar menu).

Não bloqueia a migração IA — `nps`/`wrapup` (menu com timeout positivo, visibility do humano)
e `echo` (step `receive`) não sofrem a corrida.

---

## Scheduler central de timers *(diferido — ADR aceito)*

Consolidar os timers espalhados (timeout de suspend/delegate no channel-gateway,
`_hook_timeout_guard` no bridge, timeout de `collect`) num módulo único de scheduling:
sorted-set de deadlines (`ZADD`/`ZRANGEBYSCORE`) + poller único + evento `timer.fired`
com os donos reagindo; calendar-api permanece o engine de prazo (calcula o *quando*, não
dispara). Primeiro corte funcional já existe (`run_timeout_scanner` no channel-gateway).
Decisão e mecanismo em [`docs/adr/adr-timer-scheduler.md`](docs/adr/adr-timer-scheduler.md).

---

## Skill hot-reload via YAML em disco sem restart *(deferred — dev/demo only)*

**Fluxo editor → deploy já funciona**: `POST /v1/skills/:id/deploy` → `publishRegistryChanged` → bridge invalida `_skill_flow_cache` → próxima execução busca conteúdo atualizado do agent-registry. Nenhuma mudança necessária para este caminho.

**Gap**: edição direta de arquivo YAML em disco (dev/demo) ainda requer `restart orchestrator-bridge` para o RegistrySyncer re-ler e fazer PUT para o agent-registry. A solução correta é um endpoint `POST /admin/skills/sync` (ou handler de `registry.changed` com `source: disk`) no bridge — chama `RegistrySyncer._sync_skills()` → PUT → `registry.changed` → cache invalidado. Deve ser acionado pelo processo de deploy YAML (CI/CD, script), não pelo editor.

---

## Arc 19 — Modelo Unificado de Sessão: Workflow como Canal Webhook

Spec em [`docs/arcos/arc19-unified-session-model.md`](docs/arcos/arc19-unified-session-model.md). Elimina a dualidade contact/workflow tratando workflows como canal `webhook` na channel-gateway.

- **Fase A** ✅ — WebhookAdapter + `channel_type: webhook` + routing engine (2026-05-28)
- **Fase B** ✅ — Status `suspended` + TTL extension + hash Redis `resume_tokens` + stream events (2026-05-28)
- **Fase C** ✅ — orchestrator-bridge: `persistSuspendWebhook` wired in skill-flow-service; `_handle_webhook_session_resumed`; `process_inbound` http param (2026-05-28)
- **Fase D** ✅ — workflow-api: proxy trigger/resume → channel-gateway; 410 Gone para persist-suspend/complete/fail/cancel/collect; `business_hours` + `calendar_id` em `persistSuspendWebhook` (2026-05-28)
- **Fase E** ✅ — Monitor e Analytics unificados: filtro `channel_type`/`webhook` badge/`suspended` badge; Events tab (Arc 12); status filter analytics end-to-end (2026-05-28)
- **Fase F** ✅ — Eliminação Journey (Arc 10/16/17 → CHANGELOG); platform-ui limpa; Arcs 10/16/17 retired (2026-05-28)

**Arc 19 completo.** Cleanup residual (infra): remover `workflow.events` topic do Kafka e arquivar o package `skill-flow-worker`.

---

## Arc 18 — Workflow Execution Trace *(DEPRECATED pelo Arc 19)*

A spec original em [`docs/arcos/arc18-workflow-execution-trace.md`](docs/arcos/arc18-workflow-execution-trace.md) está superseded pelo Arc 19.

**Por que deprecated**: todas as superfícies de Arc 18 dependem de entidades eliminadas pelo Arc 19 — `workflow-api` (deprecado Fase D), `Analytics/Processes` (eliminado, merge em Analytics/Sessions), `Analytics/Journeys` (eliminado com Journey na Fase F), rotas `/analytics/processes/:instanceId` e `/analytics/journeys/:journeyId` (desaparecem).

**O que sobrevive do conceito**: conforme documentado em `docs/arcos/arc19-unified-session-model.md` §Analytics/Sessions, a hierarquia correta é **lista de sessions → lista de segments → detalhe do segment**. Workflows webhook aparecem em Analytics/Sessions com `channel_type: webhook`; cada suspend/resume cria um segmento distinto; o padrão de navegação é idêntico ao de sessões normais (webchat, voice). Não há Trace tab separada — o usuário navega pelos segmentos da sessão webhook da mesma forma que navega pelos segmentos de qualquer outra sessão.

**Pendência real (prioridade demo):** Analytics/Sessions → ao clicar em uma sessão webhook com múltiplos segmentos, a UI deve mostrar a **lista de segmentos** antes de ir para o detalhe, e cada segmento na lista deve indicar o contexto do ciclo (ex: "Execução 1 — suspenso" / "Execução 2 — concluído"). Verificar se a navegação atual pula a lista de segmentos para sessões com um único segmento (comportamento correto para sessões normais) e mostrar corretamente a lista quando há múltiplos segmentos (caso webhook com suspend/resume).

---

## Step `delegate` + MCP tool `workflow_resume` ✅

Padrão implementado completo. Componentes entregues:

- `skill-flow-engine/src/steps/delegate.ts` — executor do step
- `skill-flow-engine/src/engine.ts` — `persistDelegate` em `SkillFlowEngineConfig` + wiring em `_buildContext`
- `mcp-server-plughub/src/tools/workflow.ts` — MCP tool `workflow_resume`
- `channel-gateway/adapters/webhook.py` — `handle_delegate` (cria sessão-filho + ContextStore)
- `channel-gateway/main.py` — `POST /v1/channels/webhook/delegate` (antes de `/{skill_id}`)
- `e2e-tests/services/skill-flow-service/src/index.ts` — `persistDelegateFn` + `CHANNEL_GATEWAY_URL`
- `docker-compose.demo.yml` — `CHANNEL_GATEWAY_URL` + `CALENDAR_API_URL` no skill-flow-service
- `skill_portabilidade_demo_v1.yaml` v2.0 — usa `delegate` (sem notify/collect no workflow)
- `agente_confirmacao_portabilidade_v1.yaml` — agente de I/O de confirmação
- `infra/registry/tenant_demo.yaml` — pool `portabilidade_confirmacao`

---

## Webhook workflow trace — segmentos históricos sem origin_session_id *(deferred)*

A migração ClickHouse `_DDL_SESSIONS_MIGRATE_ORIGIN` adiciona a coluna `origin_session_id` à tabela `sessions`, mas sessões webhook criadas antes da migração têm o campo NULL. O `WorkflowTraceList` não vai exibir o segmento de entrada (intake) para essas sessões. Apenas sessões criadas após a migração terão o link correto.

Não requer ação — os dados históricos permanecem corretos para análise; apenas o link de rastreabilidade cross-session ficará ausente para sessões antigas.

---

## Usage Metering — Channel Gateway Adapters *(deferred)*

Funções em `usage_emitter.py` implementadas, mas os adapters de canal ainda não as chamam. Será wired quando cada adapter for criado:

- `whatsapp_conversations` — adapter WhatsApp
- `voice_minutes` — adapter WebRTC/Voice
- `sms_segments` — adapter SMS
- `email_messages` — adapter Email

---

## Pricing Module — Integração metering × pricing *(deferred)*

Módulo que lê contadores de `usage.events` no Redis/ClickHouse, aplica planos configurados no Config API e escreve `{tenant}:quota:limit:*` no Redis. Metering registra mas pricing não consome ainda.

---

## Masking — Bloco 3: Channel Gateway TTS *(deferred até implementação de voz)*

Quando qualquer adapter de voz/TTS for criado, deve consultar `rule.{category}.display_voice` no namespace `masking` do Config API antes de passar texto ao sintetizador. Comportamentos: `silence` (pula o valor), `beep` (tom de beep), `speak_placeholder` (fala "valor mascarado"). Não implementar antes de definir qual engine TTS será usada.

---

## Audit LGPD — Fases Pendentes

Fase 1 concluída — ver CHANGELOG 2026-05-14 e `docs/arcos/audit-lgpd.md`.

- **Fase 2** — `original_content` desmascarado: endpoint de resolução de tokens em Core → analytics-api expõe conteúdo original ao DPO. Requer endpoint batch de resolução de tokens no Core.
- **Fase 3** — `user_access` logs: topic Kafka `user_access.events` em auth-api + tabela ClickHouse + tab ativo em AuditPage.
- **Fase 4** — SAR/Erasure pipeline: CRUD de Subject Access Requests + pseudonimização em `sessions_stream` + anonimização ClickHouse (TTL/partition replacement).
- **Fase 5** — `config_snapshot`: leitura read-only do namespace `masking` do Config API para verificação DPO.

---

---

