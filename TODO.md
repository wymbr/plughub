# TODO — PlugHub Itens Pendentes

> Itens genuinamente não implementados. Histórico de implementações concluídas em `CHANGELOG.md`.

---

## Webhook pools — throttle de downstream: enforcement no routing *(deferred)*

Re-validação 2026-06-04 (ver `CHANGELOG.md`): o default 500 **já não existia** no código
(schema `.optional()`, registry grava null); a premissa "nada é pré-instanciado" ficou
stale pós Arc 19 Fase C — capacidade real de webhook = slots de instância do deploy
(Bootstrap) + admissão híbrida. O `max_concurrent_sessions` pool-level era display-only
no Monitor (capacidade fictícia) — coerência aplicada: removido do YAML demo, comments
schema/registry revisados ("throttle opcional de downstream").

**Deferred**: enforcement real do throttle no routing quando configurado
(`active_count ≥ max` → enfileira; backpressure p/ downstream frágil, ex. ERP).
Implementar quando houver caso de uso real.

---

## Delegate v2 — itens restantes (pós-correção do ciclo de portabilidade)

Modelo corrigido e backend verde em [`docs/arcos/delegate-workflow-io.md`](docs/arcos/delegate-workflow-io.md)
(delegate sempre roda o alvo como segmento conference do chamador; A-new fecha como webchat;
`context_set` registrado; specialist de B adia instantâneo). Restam:

- **Fase C — heurística de canal na UI ✅** (já implementada — TODO estava
  desatualizado): `ListaTab.tsx` classifica pelo `channel_type` real (canal decide
  WorkflowTraceList vs SegmentList) e o badge "suspended" é restrito a `channel ===
  'webhook'` (webchat em delegate-wait lê live). Nota residual no código: contador
  de participantes vivos exigiria suporte de backend — channel é o proxy aceito.
- **Fase D — timeout scanner do delegate ✅** (já implementado — TODO estava
  desatualizado; ver `delegate-workflow-io.md` § Fase D): `run_timeout_scanner` em
  `channel-gateway/adapters/webhook.py` (lifespan, 60s) expira `resume_tokens`
  vencidos via `handle_resume(decision="timeout")` → `on_timeout` do step; cobre
  suspend e delegate; `pending_workflow` stale auto-limpa no próximo reconnect.
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
- **Fase 2 — relatório de Pools/Infra ✅ concluída** (2026-06-04): pool×canal×**endpoint**×tempo
  — volumetria, fila (espera/tamanho/abandono/disponíveis), concorrência vs capacidade
  (headroom), SLA. Spec/ADR em [`docs/arcos/pools-infra-report.md`](docs/arcos/pools-infra-report.md).
  **Atualização 2026-06-03**: Fila/SLA reescritos sobre segments `role='queue'` + demanda
  reprimida no Volume (queue-attended-model Fase D ✅, ver `CHANGELOG.md`).
  **Atualização 2026-06-04**: dívida `sessions.sla_target_ms` resolvida ✅ (ver
  `CHANGELOG.md`) — aba SLA popula a partir dos contatos novos; sessões históricas
  permanecem NULL (valor nunca foi persistido, irrecuperável).
  **Fechamento 2026-06-04 ✅** (ver `CHANGELOG.md`): recon confirmou (TODO atrás do
  código de novo) que sampler/consumer/endpoints/aba já existiam; decisões: (a)
  occupancy **sampler** basta (carry-over implícito, `peak_total` instantâneo —
  contadores event-driven descartados); (b) teto do **total** = configurada no pricing
  (novo `GET /v1/pricing/capacity/{tenant_id}`, `capacity_source` no occupancy,
  fallback gracioso), per-pool segue provisionada; (c) time-series de capacidade na
  aba Capacidade ✅ (Arc 19). Residuais opcionais no spec (§ Pendente→Concluído):
  sub-aba Visão geral, heatmap hora×dia, SETs de session_id, overlay licenciada v2.
  **Dívida descoberta na validação (2026-06-04)**: a integração pricing→quota Redis
  (`{t}:quota:*` lidas pelo `assertQuota`) está documentada em `docs/arcos/pricing.md`
  e no CLAUDE.md mas **não existe no pricing-api** (zero código Redis; verificado:
  `keys 'tenant_demo:quota:*'` vazio após POST de resources). O teto contratado hoje
  é só analítico (denominador do occupancy); o gate de admissão por quota não arma.
  Implementar a escrita das quotas no upsert de resources (ou na ativação de plano)
  e corrigir `pricing.md` enquanto isso.
- **Queue-attended-model — residuais pós Fase E** (2026-06-03, ver spec): (a) ~~render v2
  webchat~~ ✅ (2026-06-04, ver `CHANGELOG.md`) — `deliver_text` entrega mensagens de
  sistema via WS e `deliver_session_closed` renderiza `farewell_text` antes do close;
  validado no cenário outage (`reservation_full`). Canais voice/whatsapp ainda não
  renderizam `farewell_text` (voice = TTS futuro);
  (b) ~~limpar `queue_config`/`session_reservation` via PUT~~ ✅ (2026-06-04, ver
  `CHANGELOG.md` — `.nullable()` nos campos de pool + `DbNull` no registry + UI);
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

## Governança de Capacidade — contratado como fonte única *(novo, 2026-06-04)*

Nasce da validação do fechamento Fase 2 Pools: contratado não governa config nem
runtime (Σ reservas pode exceder C / shared negativo; quota Redis documentada mas
inexistente; demo deploya 295 vs 25 contratados sem alerta). **Modelo fechado** em
[`docs/arcos/capacity-governance.md`](docs/arcos/capacity-governance.md): C
(pricing) é fonte única; **recursos criados no momento do uso** → gate primário na
criação (instância IA on-demand, humano = concorrentes logados) contra o C vigente;
declaração no flow/deploy validada no deploy; Σ reservas ≤ C e shared ≥ 0 (zero ok,
negativo nunca); redução de C sempre aceita com revalidação + alerta de
não-conformidade (nunca bloqueia); P (alocado) vira medidor de consumo do contrato
(UI: C × alocado × saldo). Absorve a dívida pricing→quota Redis registrada na
Fase 2. Pendente de implementação: ver § Pendente do spec.

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

**Pendência real ✅** (constatada já implementada em 2026-06-04 — Fase E do delegate
entregou): `WorkflowTraceList` renderiza a lista ordenada de segmentos da sessão
webhook com numeração de ciclo, badge de tipo (intake/execução/specialist), status
por nó (live/outcome/closed), pool+timing e contadores de execuções/suspensões; a
navegação por canal real (Fase C do delegate) garante que sessão webhook sempre
passa pela lista antes do detalhe.

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

