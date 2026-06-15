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
- ~~**Sessões sem `pool_id` no relatório de fila**~~ ✅ (2026-06-05): origem
  identificada — sessões nunca roteadas nem enfileiradas (pool vazio E sem
  segmento de fila; ex. webchat que conecta e não engaja). Sem semântica de
  fila → filtradas do `/reports/pools/queue` (`WHERE pool_id != ''` no
  per-session, com justificativa em comentário); o volume delas segue no
  Volume report.
- ~~**i18n quebrado no dropdown de pools do Console**~~ ✅ (2026-06-05): a chave
  `header.comboPools` interpola `{{pools}}` mas o cabeçalho do dropdown passava
  `{ count }` → literal `POOLS ({{POOLS}})`. Fix no `Header.tsx` (passa
  `pools: "ativo/total"`, mesmo formato do botão).
- ~~**Env do Config API no routing**~~ ✅ (2026-06-05): faltava
  `PLUGHUB_CONFIG_API_URL` no compose — o RoutingConfigCache tentava
  `localhost:3600` no boot e caía nos defaults hard-coded (custom de
  mensagens/limites do tenant não chegava até um config.changed). Adicionado
  `http://config-api:3600`.
- **Reformulação Analytics/Agents — Bancada de comparação 360° (novo)**: reescreve a aba como
  bancada de comparação (média dos agentes × indivíduos), unificando quantitativo + qualitativo
  (Arc 6) + voz do cliente (NPS/pesquisa) + voz do agente (wrap-up) na mesma entidade `agent_key`.
  **Spec/ADR** em [`docs/arcos/analytics-agents-workbench.md`](docs/arcos/analytics-agents-workbench.md)
  — decisões fechadas: média aritmética rotulada "média dos agentes" + N; comparabilidade por
  domínio de métrica (desabilita no seletor); camada `session_signal` (NPS/wrap-up/pesquisa via
  Arc 12 + journey, `session_at`×`captured_at`, normalização por pool); detalhe type-aware;
  cruzamento das vantagens (concordância/quadrante) + calibração do avaliador (Arc 13).
  **Recon 2026-06-07 (§13 do spec)** — premissas validadas no código + decisões travadas:
  · `evaluation_results` **sem** atribuição a agente → exige join `→ segments` por `session_id` (F2);
  · hooks NPS/wrap-up **não** emitem `agent_event` (dado preso no ContextStore); `session_signal` inexistente (F5);
  · outcome humano é **placeholder** (não 0%) — fonte real = `session.wrapup.classificacao`;
  · **decisão**: `complete` de todo agente devolve outcome **dinâmico**; `primary` humano **propaga** o do wrap-up;
  · domínio `pending≡suspended`, `transfer≡escalate` (sem valor novo) — mapa wrap-up: resolvido→resolved, escalado→escalated, cancelado→abandoned, pendente→suspended;
  · contrato do segmento (lido igual humano/IA): `outcome` + `close_reason` (enum, iniciativa) + `handoff_reason` (livre, escalação) + `issue_status` (rótulo curto); texto livre rico no detalhe sob demanda (LGPD).
  **Fases**: **F1 espinha (outcome real) ✅ 2026-06-07** (ver `CHANGELOG.md` — inclui correção da
  causa-raiz: notify nunca implementara `context_tags`, destravando também o NPS) → **F2 join
  qualidade ✅ 2026-06-07** (atribuição validada E2E; pipeline de avaliação religado — ver
  CHANGELOG; pendências test-grade: ReplayContext sem session_meta e sem associação campanha/form
  → arco da visão final) → **F3 endpoint `/reports/agents/compare` ✅ 2026-06-07** (5 lentes v1;
  média aritmética c/ gap; validado com dado real — ver CHANGELOG) → **F4 UI bancada ✅ 2026-06-09**
  (AgentsBenchPage; subfases F4.1–F4.5 no CHANGELOG; pendentes na UI: nps/wrapup→F5, quality_criteria;
  refinamento: pool-average agregado via pseudo-entidade `pool:`) → **F5 NPS+wrap-up (grão segmento)
  ✅ 2026-06-09** (derivado de segments, NÃO session_signal; refator per-segmento no bridge — ver
  CHANGELOG + conference-mechanics §Mudança 7; session_signal p/ grãos contato/jornada fica futuro)
  → **F6 cruzamentos ✅ 2026-06-09** (endpoint `/reports/agents/cross` + view Cross-cut: concordância
  + quadrante) → **F8 quality_criteria ✅ 2026-06-09** (lente por dimensão + heatmap + radar) →
  **F9 pool-average `pool:` ✅ 2026-06-09** (pseudo-entidade) → **F7 motivo de escalação ✅ 2026-06-09**
  (taxonomia configurável + lente empilhada) → **F10 `session_signal` (grão contato/jornada — em curso)**:
  **F10.1 camada de dados ✅ 2026-06-10** (tabela `session_signal`; ingest inicial via dual-write de
  `agent_event` — substituído na F10.2a) → **F10.2a tool `survey_record` + tópico `session.signals` ✅
  2026-06-10** (store unificado: TODOS os grãos `segment|session|workflow|journey` gravados
  explicitamente via tool MCP dedicada; `segment` com `segment_id`+`agent_key`; dual-write de
  `agent_event` retirado, contrato Arc 12 intacto; validado E2E — ver CHANGELOG) → **F10.2b.1 esqueleto
  trigger→record ✅ 2026-06-10** (fluxo primário dispara sub-workflow `skill_survey_v1` via
  `workflow_trigger` passando `origin_session_id`; `survey_record` tenant-explícito; validado E2E.
  **Destravou 4 fixes de plataforma**: input array no `StepInputValueSchema`; resolução webhook
  `skill_id`→pool no routing — nunca existira, funcionava por acaso com 1 pool webhook —
  via `webhook_skill_id`; `skill_id` no `ConversationInboundEvent`; demo exige `INCRBY` na quota
  `max_concurrent_sessions`. Ver CHANGELOG) → **F10.2b.2 coleta real de NPS via delegate (inbound_only)
  ✅ 2026-06-10** (skills `agente_survey_nps_v1`+`agente_survey_reconnect_v1`; pools `survey_collector_ia`+
  `survey_reconnect_ia`; reconexão webchat via `pending_workflow_get`; fix de plataforma: recursão de
  arrays no `interpolate.ts`; validado E2E real com NPS=8 — ver CHANGELOG) → **F10.3a exposição do NPS
  de sessão na bancada ✅ 2026-06-10** (lente `session_nps`: `session_signal` grain=session ⋈ atribuição
  por session_id → NPS de sessão dos contatos do agente, cruzamento §8; seção "Voz do cliente" no
  detalhe type-aware: NPS agente × NPS sessão; i18n; teste passa; endpoint 200 — ver CHANGELOG. Não toca
  F5) → **F10.3b cutover F5 ✅ 2026-06-10** (caminho B unificado: `agente_nps_v1` chama
  `survey_record(grain=segment)`; bridge escreve `session.surveyed_segment_id`/`agent_key` via `@ctx`;
  `_compare_nps_lens` migra para `session_signal` (join segments p/ metadata) → lentes `nps`+`session_nps`
  leem a mesma tabela, **acaba a duplicação**. **Cutover final**: validado E2E o write do hook (fluxo
  humano real → `survey_record grain:segment, nps=8`); legado removido (bridge não escreve mais
  `segments.nps_score`; `_apply_nps_to_segment` deletado). Coluna `nps_score` vestigial (DROP opcional).
  **Fatia F10 concluída.** Ver CHANGELOG). **F11 futura**: survey
  **diferida** (`captured_at ≠ session_at`, `session_at` da origem via enrichment) + grão **journey**
  ponta-a-ponta. Vocabulário: `journey`=grão (relacionamento multi-sessão), não a entidade eliminada.
  Detalhe em §13/§14 do spec. Débito pré-existente notado na F1:
  3 falhas em `resolve.test.ts` (BLPOP/mention mocks — não relacionadas). Débito notado na F10.3a:
  6 falhas em `test_reports.py::TestQueryAgentAvailabilityReport` (`query_agent_availability() missing
  positional arg 'tenant_id'` — descasamento assinatura×teste, pré-existente, não relacionado à bancada).
  **▶ PRÓXIMA SESSÃO (planejada 2026-06-10) — FECHAR A BANCADA (follow-ups A), ordem sugerida:**
  1. ✅ (2026-06-11) **`$.segment_id` no `interpolate.ts`** — `segment_id: ctx.segmentId` no evalContext
     de `resolveJsonPathRef`; teste em `invoke.test.ts`. Skill lê `$.segment_id` p/ `survey_record(grain=segment)`
     "sobre si mesmo". Ver CHANGELOG 2026-06-11.
  2. **F11.1** ✅ (2026-06-11) — enrichment de `session_at`: consumer resolve `analytics.sessions.opened_at`
     da origem (por `origin_session_id`) e sobrescreve `session_at` no ramo `session.signals`; fallback
     `captured_at`. `AnalyticsStore.lookup_session_opened_at` + `consumer._enrich_signal_session_at` (cache).
     Grão `journey` já aceito. **F11.2 (validação)**: diferido **simulado via curl/seed** (decisão do
     usuário) — publicar `session.signals`/`survey_record` com origem de `opened_at` anterior + grão journey,
     conferir `session_at = opened_at`. Workflow agendado real (dias depois) fica futuro. Ver CHANGELOG.
  3. ✅ (2026-06-11) **quality cross-form — re-escopado**: merge de dimensões cross-form **descartado**
     (inventa equivalência inexistente). Regra de comparabilidade: cross-agente exige mesmo form;
     cross-form só p/ um único agente. `_compare_quality_lens` expõe `summary.form_ids`; UI da lente
     `quality` faz guard/ressalva. `quality_criteria` segue same-form. **Futuro**: catálogo canônico de
     dimensões (única base rigorosa p/ comparar dimensões entre forms) → arco próprio. Ver CHANGELOG.
  4. **Validações E2E reais F5/F7 + limpeza de fixtures** — EM ANDAMENTO (2026-06-11):
     - **F7** ✅ **VALIDADO E2E REAL (2026-06-12, ver CHANGELOG)**: contato real
       `sac_ia`→escala→`retencao_humano`→wrap-up escalado+motivo (conduzido via webchat+Console no
       navegador). `plughub_demo.segments` da sessão: 1 linha IA (`flow_id=skill_atendimento_sac_v1`,
       `outcome=escalated_human`, `escalation_reason=specialist_needed`) + 1 linha humana
       (`agent_type=human`, `outcome=escalated`, `escalation_reason=retention`). Wiring confirmado ponta
       a ponta (IA via `pipeline_state.results.escalation_reason`; humano via menu do wrap-up→`seg_signal`
       →bridge). Nota de execução: menu `list`/`button` exige **eventos de mouse completos** p/ submeter
       a seleção (um `.click()` JS puro não dispara o handler).
     - **F5** inline (grão segmento) — ✅ **CONCLUÍDO E VALIDADO E2E (2026-06-12)**. O NPS/wrap-up inline
       é **1 por contato, no segmento humano final** — "2 NPS inline num contato" é estrutural (não existe).
       O **transfer funcional** (ver CHANGELOG "Console Transfer + G7") destravou e **validou** a atribuição
       per-segmento real: contato com 2 segmentos humanos (`operator@…` `transferred` em `retencao_humano` →
       `admin@…` `resolved` em `humanoxxx`), e o sinal `session_signal grain=segment metric=nps=10`
       corretamente chaveado ao `segment_id`/`agent_key` do segmento **final** (admin), não ao transferido.
       Caminho de escrita do NPS confirmado saudável (`survey_record grain=segment`; o "não gravava" em
       automação era artefato de `.click()` JS no webchat, não regressão).
       **Reclassificação (decisão 2026-06-12)**: a riqueza "**N sinais por agente/segmento**" **NÃO é inline**
       — é o **modelo de pesquisa multi-grão OUTBOUND** (`session_signal` grãos `journey | session | segment`,
       até 3 grãos por fluxo, configurável: avaliar a journey, cada contato e cada segmento). Base parcial na
       F10.2b (`survey_collector_ia`/`survey_reconnect_ia`). Falta o **planejamento da orquestração** (quando/
       como cada grão dispara, surveys diferidas `captured_at≠session_at`) → vira **F11 / arco de pesquisa
       multi-grão** (evaluation), separado do G7 (ciclo de vida). Ver `docs/arcos/g7-segment-contact-decoupling.md` §5.
     - **NPS render (cosmético, diferido)**: a mensagem do `menu`/`notify` passou a ser exibida no
       transcript como "structured content" (texto dentro do envelope) em vez de texto puro; o **dado do
       NPS é gravado normalmente**. Revisar o emit do `menu`/`notify` + render no transcript depois.
     - **F8** ⏸ **ADIADO**: `evaluation_dimension_scores` segue com fixture (seed de `evaluation_results`).
       O avaliador `agente_avaliacao_v1` não roda no demo (test-grade, sem associação form/campanha) —
       consertar o pipeline de avaliação é **arco próprio**. Fixture documentado até lá.
  5. ✅ (2026-06-11) **DROP `segments.nps_score`**: leitor esquecido no `query_agents_cross` (F6)
     migrado p/ `session_signal` (grain=segment); removido de DDL/cols/row-builder/parser (analytics) e
     do bridge (`_publish_participant_event`/republish, vestigial). DROP idempotente
     (`_DDL_SEGMENTS_DROP_NPS`) auto-aplica no startup do analytics-api — sem passo manual. Testes do
     cross atualizados (seg→nps→eval). Ver CHANGELOG.
  6. ✅ (2026-06-11) **Débitos de teste pré-existentes**: ambos eram drift teste×impl (produção OK).
     `TestQueryAgentAvailabilityReport` (6) — além da assinatura `(client, database, tenant_id, …)`, o
     mock estava obsoleto: a fn foi reescrita na Fase 1b (4 queries login/pause/reason/busy, não 3); o
     mock com 3 resultados esgotava o `side_effect` → `StopIteration` no `to_thread` **travava o pytest**.
     Testes reescritos pro modelo novo. `resolve.test.ts` (3) — modelo multi-instância: result key com
     `instanceId` + `hdel` no hash `menu:waiting` (testes usavam key plana + `del`). Só testes. Ver
     CHANGELOG. **Follow-ups A (1–6) COMPLETOS.**

  **✅ BUG corrigido (2026-06-11) — contato vazava p/ todos os agentes do mesmo pool no Console:**
  Causa-raiz: `conversation.assigned` publicado no canal do POOL `pool:events:{poolId}`; o WS handler
  aceitava qualquer assignment sem filtrar o `instance_id` alvo → fan-out pro pool (regressão do modelo
  por-usuário C1 sobre o canal por pool legado). **Fix**: conexão calcula `expectedInstanceId =
  "human-${userId}"` e descarta `conversation.assigned` de outro alvo, nos dois caminhos (pub/sub ao vivo
  + reentrega de `pool:pending_assignment`). Helper puro `lib/assignment-filter.ts` (`shouldDropAssignment`)
  + teste. Backward-compat (userId/target vazio → não filtra). Rebuild `mcp-server-plughub`. Ver CHANGELOG
  + `conference-mechanics.md` § Histórico.
  **Pendências relacionadas (abertas)**: (a) `pool:pending_assignment:{poolId}` é UMA chave por pool
  (last-write wins) → chave por-instância é melhoria futura (liga à fila pull/inbox).

  **✅ Transfer "No destinations available" RESOLVIDO (2026-06-11) — eram 3 camadas:**
  (8.1) contrato — `escalation_pools` no `SupervisorConfigSchema` (registry parava de descartar no write);
  (8.2) config — seed do campo no `retencao_humano` (YAML→registry); (8.3) **endpoint** — a rota REST
  `GET /api/supervisor_capabilities/:sessionId` (server.ts) era um **stub vazio**; passou a resolver
  pool do session meta e ler `escalation_pools` da registry. **Validado E2E**: combo lista sac/reembolso/
  portabilidade. Ver CHANGELOG. **Pool Config Surface** (editar esses campos na UI) segue como F2-pools.
  **Iniciativa maior (decidida pelo usuário)**: o YAML é seed-a-eliminar; TODO config de pool deve ser
  editável na tela `config/resources/pool` (registry-backed), pra provisionamento sair 100% da config.
  **Inventário-fonte + plano**: `docs/arcos/pool-config-surface.md`. Gap principal (não na UI hoje):
  `hooks` (wrap-up/NPS/post), `supervisor_config` (escalation_pools/intent_map), `mentionable_pools`,
  `deploy` (skill+concorrência IA), `evaluation`, `agent_kind`, `session_reservation`,
  `max_concurrent_sessions`, `agent_groups`, `webhook_skill_id`. Fases no doc.

  **▶ ESCOPO: Config Consolidation (estratégia HÍBRIDA, 2026-06-11)** — plano completo em
  `docs/arcos/config-consolidation.md` §8. Os invariantes "Configuration — Single Source" no CLAUDE.md
  são **permanentes**; este escopo é o burn-down das violações herdadas até o guard ficar limpo.
  - [x] **F0.1** ✅ Invariantes de config (permanentes) no CLAUDE.md, seção "Configuration — Single Source"
  - [x] **F0.2** ✅ Guard-rail: `infra/check_config_invariants.py` (allowlist de 4 violações conhecidas;
        falha se surgir nova; avisa quando uma é corrigida). Roda via `python3` ou container:
        `docker run --rm -v "$PWD":/repo -w /repo python:3.11-slim python infra/check_config_invariants.py`
  - [x] **F1.1a** ✅ (2026-06-11) `seed.py` não escreve mais Redis: removidos `seed_redis()` + helper
        `RedisConn` (redundante — routing-engine popula `pool_config:{id}` e `{tenant}:pools` via
        `registry.changed`→`save_pool_config`). Guard: `seed_redis_write` saiu do allowlist.
  - [x] **F1.1b** ✅ (2026-06-11) `seed.py` aposentado: `channel_endpoints` migrados p/ YAML +
        `RegistrySyncer._sync_channel_endpoints` (corrige `label`→`display_name`); agent_types eram mortos
        (entidade removida); serviço `demo-seed` removido do compose; seed.py vira stub. Guard zerado
        (`pools_double_source` resolvido). **FASE 1 COMPLETA — guard 0/0.** Ver CHANGELOG.
  - [x] **F1.2** ✅ (2026-06-11) Precedência env×config (rigoroso, config-api vence):
        `attachment_expiry` — channel-gateway lê `{tenant}:config:webchat:attachment_expiry_days` do
        config-api (helper `resolve_attachment_expiry_days`, 4 adapters) + env removido + teste.
        `instance_ttl` — env removido (routing-engine usa default 30s da spec; tunable→config-api se preciso).
        Guard 3→1 (detecção por assignment ativo). Ver CHANGELOG.
  - [ ] **F2** Migração por domínio (read-path-first): pools (UI, `pool-config-surface.md`) → TTLs → hooks → masking → ABAC/users → evaluation/pricing → defaults hardcoded
    - **F2-pool (UI de pool)** — fatiado por grupo de campos. Decisões de modelagem 2026-06-12 em
      `pool-config-surface.md` § Decisões: combos referenciam **pool_id** (não skill, estável a versões);
      Transfer ≠ @mention (listas separadas); `max_concurrent_sessions` e `webhook_skill_id` ficam fora
      do drawer (este último é config de canal); `supervisor_config.enabled` não exposto.
      - [x] **F2.A** ✅ (2026-06-12) Hooks (on_human_start/on_human_end/post_human) — `HookListEditor` no
            PoolsPage + tipos + i18n. Backend já persistia. Ver CHANGELOG.
      - [x] **F2.B** ✅ (2026-06-12) Transfer (`escalation_pools`, merge-safe em supervisor_config) +
            @mention (`mentionable_pools`, lista alias→pool) — `PoolListEditor`/`MentionListEditor`, seções
            separadas no drawer. Backend já persistia. Ver CHANGELOG.
      - [x] **F2.C** ✅ (2026-06-12) Tipo & Capacidade: `agent_kind` (Select inferido/human/ai) +
            `session_reservation`; aviso queue⇒human; `registry.ts` propaga 422 (Σ≤C) ao banner.
            `max_concurrent_sessions`/`webhook_skill_id` fora por decisão. Ver CHANGELOG.
      - [x] **F2.D** ✅ (2026-06-12) **DISSOLVIDA por fonte única** — nada a expor no pool. `evaluation`/
            `evaluation_template_id` são donos de **Quality/Campaigns** (evaluation-api; o `pool.evaluation`
            do rules-engine é caminho legado/dormente — `on_pool_config` nunca é chamado). `agent_groups`
            é dono do **módulo Groups + JWT** (`supervised_groups[]`, Arc 9). Expor qualquer um violaria o
            invariante de fonte única. Ver `pool-config-surface.md` § Decisões/Gap.
            *Cleanup futuro (opcional): remover o caminho dormente `evaluation_sampler`/`on_pool_config`
            do rules-engine, ou religá-lo só se a campanha não cobrir.*
      - [x] **F2.E** ✅ (2026-06-12) Deploy — **RESOLVIDO (decisão: nada no pool)**. Investigação confirmou
            consumo ponta a ponta: `PUT /slots/next`→`promote` (next→current) publica `registry.changed` →
            orchestrator-bridge `bootstrap.request_refresh()` → `_build_desired_from_deploy` lê
            `deployed_skill_id`/`deployed_max_concurrent_sessions` do `GET /v1/pools` e provisiona instâncias.
            Dono = tela Fluxo→Deploy; por fonte única, não se duplica no drawer de pool. **F2-pool COMPLETA.**
    - **F2-TTL (TTLs/timeouts env×config)** — §8 item 2.
      - [x] **ws_auth_timeout** ✅ (2026-06-12) `resolve_ws_auth_timeout_s` lê `webchat.auth_timeout_s` do
            config-api; webchat + webrtc (foldado, sem a constante `_AUTH_TIMEOUT_S` hardcoded) usam o
            resolver; env `PLUGHUB_WS_AUTH_TIMEOUT_S` removido; guard ganhou `env_dup_ws_auth_timeout`
            (0/0). Ver CHANGELOG.
      - [x] **Item 7 (cat. C)** ✅ (2026-06-12): 7a `VITE_DEFAULT_POOL` era env morto → removido. 7b
            `EVALUATOR_POOL`+`REPLAY_SPEED_FACTOR` → config-api `evaluation` (session-replayer lê via HTTP;
            consertados de passagem: CONFIG_API_URL 3500→3600 + ausente no compose, `?tenant_id=` faltando,
            default errado `avaliador_qualidade`). `PLUGHUB_ANALYTICS_OPEN_ACCESS` fica (flag de demo).
            `webrtc._AUTH_TIMEOUT_S` é só default (ok). **Categoria C fechada.** Ver CHANGELOG.
      - [x] **Item 5 (ABAC/users)** ✅ (2026-06-12): `modules.yaml` = catálogo (auth-api carrega no startup);
            `seed_auth.py` provisiona users via API. Bug: `module_config` do seed drifted do catálogo
            (módulo `analytics` inexistente, `relatorio` vs `report`, `billing.view` vs `visualizar`) → 422
            → demo users sem ABAC. Realinhado ao `modules.yaml`; `set_module_config` falha em 422. Ver CHANGELOG.
      - [ ] **Item 6** seeds `seed_evaluation`/`seed_pricing` → bootstrap idempotente via API (liga à Fase 3).
            **Estacionado (2026-06-12): atacar junto da revisão dos módulos evaluation/pricing.**

  **▶ ARCO: Config HTTP Propagation** (aberto 2026-06-12) — `docs/arcos/config-http-propagation.md`.
  Achado durante o masking: o padrão "config-api vence via leitura direta do Redis" **nunca funcionou**
  (chave `{tenant}:config:...` nunca escrita; cache `plughub:cfg:...` é TTL). **F1.2 e F2-TTL eram
  latentes** (sempre default) — consertados pela Fase 1. Padrão-alvo = HTTP-backed cache
  (Session/RoutingConfigCache).
  - [x] **Fase 1** ✅ (2026-06-12) channel-gateway `WebchatConfigCache` (HTTP + config.changed); resolvers
        leem do cache; `config_api_url`/`PLUGHUB_CONFIG_API_URL`; testes reescritos. Conserta F1.2+F2-TTL.
  - [x] **Fase 2** ✅ (2026-06-12) mcp-server masking via HTTP (`GET /config/masking`) + seed
        `masking.context_rules` global + aposentado JSON órfão e `saveContextMaskingConfig` dead-code.
        Fecha o item 4 "masking" do §8. Ver CHANGELOG.
  - [x] **Fase 3** ✅ (2026-06-12) **ARCO COMPLETO**. 3b: `authorized_roles` migrado para HTTP
        (`loadAccessPolicy` + cache; `saveAccessPolicy` dead-code removido). 3c: creds
        `{tenant}:config:sms|whatsapp|voice:*` + `webchat:jwt_secret` são **secrets exemptos** (sem writer;
        env-first; documentado). 3a: guard `config_cache_direct_read` (falha em leitura direta de
        `plughub:cfg:*` fora do config-api; 0 ofensores). Ver CHANGELOG.
  - [ ] **F3** Bootstrap idempotente único (substitui `infra/seed/*.py` + YAML-fonte; só via APIs)
  - [ ] **F4** Política de env vars (segurança) — inventário final
  - **Transfer (8.1/8.2)** acima é a primeira fatia concreta da F2-pools (escalation_pools).
  **Nota técnica F10.3 — contexto de atribuição para `survey_record(grain=segment)` (recon 2026-06-10):**
  o que o skill já tem vs. o que falta para chamar `survey_record` com atribuição:
  · `session_id` — **disponível** à YAML como built-in `$.session_id` (`interpolate.ts` `resolveJsonPathRef`,
  junto de `tenant_id`/`customer_id`/`instance_id`). Logo `grain=session|workflow|journey` é direto.
  · `segment_id` do PRÓPRIO agente — o bridge **já passa** no `/execute` (`activate_native_agent`
  `payload["segment_id"]`, main.py ~465) → `StepContext.segmentId`; usado em `@segment.*` e escritas
  `scope: segment`. **Exposto como built-in `$.segment_id`** ✅ (2026-06-11, follow-ups A item 1) —
  `resolveJsonPathRef` (`segment_id: ctx.segmentId` no evalContext); o skill já lê e passa à tool.
  · segmento de OUTRO agente (caso NPS-sobre-o-humano no `on_human_end`): o `segment_id`/`agent_key` do
  ALVO vivem no `hook_conf` (5º campo) — no **bridge**, não no ctx do agente de pesquisa. Cutover precisa
  o bridge **injetar no ctx** (ex.: `session.surveyed_segment_id` + `session.surveyed_agent_key`) antes
  de disparar a pesquisa, OU passar via metadata do trigger. Esse é o real trabalho de atribuição do
  cutover; sinal de segmento "sobre si mesmo" só precisa do `$.segment_id` exposto.
  ContextStore NÃO guarda registro dos segment_ids do contato — só namespace por segmento
  `segment.{segmentId}.*` (precisa saber o id) e `session.*_participant_id` (participant, não segment).
  **Pipeline de avaliação (descoberto na F2, 2026-06-07)**: a cadeia Arc 3/6 estava DORMENTE —
  `conversations.session_closed` sem produtor (adicionado ao bridge), persister sem self-healing de
  schema, `EVALUATOR_POOL` apontando p/ pool inexistente, consumer do routing filtrando `event` em
  vez de `event_type`, `SKILL_FLOW_SERVICE_URL` ausente no compose, flow do avaliador sem mount no
  container, e **avaliador sem identidade** (session_token/participant_id nunca injetados).
  Test-grade: `agente_avaliacao_v1` ganhou step `agent_login` inicial (opção A — token próprio).
  **Visão final (decisão 2026-06-07)**: o avaliador deve poder rodar a qualquer momento; na versão
  definitiva é disparado pelo **calendário** na data/hora da agenda da campanha do módulo quality
  (campo `schedule` JSONB já existe em `evaluation.campaigns`), recebendo como parâmetro o
  `session_id` a avaliar — substituindo o gatilho incondicional do Persister por
  agendamento+amostragem da campanha. Vira arco próprio quando priorizado.
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
      Pendente derivado → **Fase 1b** (availability/pauses vazio no humano). **Correção 2026-06-07**:
      "outcome humano = 0%" era premissa errada — o segmento humano **grava** outcome, mas é
      **placeholder** (Console hardcoda `resolved`/`abandoned`; ClickHouse: 24 resolved / 12 abandoned
      / 19 NULL em 55 segs, `issue_status` 0/55). Disposição real em `session.wrapup.classificacao`
      (ContextStore). Tratamento → Fase F1 da bancada (`docs/arcos/analytics-agents-workbench.md` §13).
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

## Governança de Capacidade — contratado como fonte única *(✅ ARCO CONCLUÍDO 2026-06-05)*

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
**Item 1 ✅** (2026-06-04, ver `CHANGELOG.md`): quota sync no pricing-api —
mutações de resources gravam `{t}:quota:max_concurrent_sessions` (C = ai+human,
base + reservas ativas); `sync_all` no boot; o gate já existente da admissão
híbrida (`shared = C − Σ reservas`) passa a armar de verdade. `pricing.md`
§ Quota Side Effects corrigido (descrevia integração inexistente).
**Item 3a ✅** (2026-06-04, ver `CHANGELOG.md`): agent-registry valida
`Σ session_reservation ≤ C` no POST/PUT de pool (422 só em aumentos; reduções
sempre passam; sem C → fail-open) + `GET /v1/pools/capacity/conformance`
(conformidade derivada, revalidação implícita on contract-change).
**Item 4 ✅** (2026-06-04, ver `CHANGELOG.md`): aba Capacidade na BillingPage —
contratado × alocado × saldo + reservado/shared com alertas de não-conformidade
(reservas > C; alocado > C). Restam: 3b (Σ dos deploys ≤ C), 2 (gates por
tipo), 5 (aba Analytics contratado-cêntrica) e 6 (demo coerente).
**Item 3b ✅** (2026-06-04, ver `CHANGELOG.md`): Σ declarada nos deploys ≤ C
validada no PUT slots/next + promote (rollback isento; reduções passam;
helper `lib/capacity.ts` compartilhado com 3a).
**Itens 5+6 ✅** (2026-06-04, ver `CHANGELOG.md`): aba Capacidade
contratado-cêntrica (KPI Alocado como diagnóstico) + `pricing-seed` do demo
(ai 300 + human 10 → C=310, não-destrutivo). Resta do arco: 2 (gates por
tipo) e 7 (UX do available).
**Item 2 / Etapa 1 ✅** (2026-06-05, ver `CHANGELOG.md`): `agent_kind` ponta a
ponta (schemas+Prisma+backfill+rotas+routing+YAML) + quotas por tipo
(`{t}:quota:capacity:{ai_agent|human_agent}`) + decisões de tipagem fechadas
no spec (queue_config⇒human; fila atendida=ai cobrável; tier grátis = fila de
sistema, arco futuro).
**Item 2 / Etapa 2 ✅** (2026-06-05, ver `CHANGELOG.md`) — **item 2 completo**:
gate humano (logins concorrentes ≤ C_human + kind do pool no registerHumanAgent,
`login_denied` com toast no Console), gate IA (sessões em pools ai ≤ C_ai na
admissão, cause `quota` → demanda reprimida), recurso×kind (deploy em pool
human → 422; login humano em pool ai → negado). **Resta do arco: só o item 7**
(UX do available físico × admissível).
**Item 7 — design fechado 2026-06-05** (ver § 7 do spec): dois números
(físico/admissível ⊕), organização Reservados × Compartilhado × Fila gratuita
com donuts ("total e como está sendo consumido") + tiles do pipeline; HASH
`{t}:admission:shared_pools` para atribuição exata do shared. Execução:
**7a ✅** (2026-06-05, ver `CHANGELOG.md`): HASH shared_pools (atribuição exata)
+ agregador no /v1/operational/pools (admissible, regimes, tiers, summary) +
Monitor/Pools com tiles/donuts/seções + tiles no Monitor/Sessions.
**7b ✅** (2026-06-05, ver `CHANGELOG.md`): sampler amostra admissão →
admitted_peak + linhas __reserved__/__shared__/__buffer__ → bloco admission
no occupancy → aba Capacidade com "Admissão no tempo" e "Sala de espera
gratuita no tempo". **ITEM 7 COMPLETO — ARCO CONCLUÍDO.** Verificações na
validação do 7b: segmento sintético no detalhe de Sessions; nenhum `system`
em Analytics/Agents.

---

## Fila de sistema — tier gratuito *(✅ ARCO CONCLUÍDO 2026-06-05)*

**Spec/ADR**: [`docs/arcos/system-queue.md`](docs/arcos/system-queue.md).
Recon 2026-06-05 (a armadilha de sempre, na direção boa): a fila muda está
**majoritariamente viva** — ledger ZSET, aviso de espera ao cliente (mantido no
render v2), drain-on-ready, `queue_max_wait_default_s`, evento `queued` →
analytics. O arco real é bem menor que o esboço supunha. **Decisões fechadas**:
(1) isenção de C libera os buckets de admissão no enqueue mudo (re-admissão
natural no drain; C cheio → re-enfileira); (2 revisada) teto TOTAL do tenant —
`max_queue_total` no Config API + SET `{t}:queue:unadmitted` (SCARD = ocupação;
sem teto por pool; vizinho barulhento = refinamento futuro), estouro = outage
causa NOVA `queue_full`; (2b) **overflow**: C esgotado em pool humano cai na
fila muda gratuita em vez de rejeitar na porta (rejeita só com fila cheia);
(3 superada na implementação) saídas da fila muda viram SEGMENTOS SINTÉTICOS
`role=queue` (handoff/abandoned) — zero tópicos novos, zero dual-source, o
relatório Fase D conta fila muda sem mudar; (4) resta só tier da fila por pool;
(5) updates de posição = v2 opcional.
**Fase A ✅** (2026-06-05, ver `CHANGELOG.md`): isenção de C + overflow +
proteções (queue_max_total, max_wait por canal, queue_full) + segmentos
sintéticos + backstops + fixes da validação (headroom nos drains, dedupe do
aviso, release imediato no contact_closed). **Fase B ✅** (2026-06-05): causa
queue_full na demanda reprimida + tier da fila (Atendida/Sistema) na aba Fila.
**ARCO CONCLUÍDO** — item 7 do capacity-governance destravado.

---

## G7 — Decoupling segment-end × contact-close *(arco aberto)*

Spec em [`docs/arcos/g7-segment-contact-decoupling.md`](docs/arcos/g7-segment-contact-decoupling.md).
`on_human_end` está acoplado a `_trigger_contact_close` (conflação camadas 1/3). Entregue:
Fase 0 (classificador `_has_continuation`) + branch `agent_transfer` (transfer funcional — Mudança 9) +
**Slice A ✅** (wrap-up multi-humano: identidade de participante por-segmento — Mudança 10 + ADR
`adr-participant-identity-single-source`; resolve o gap (2) menu-routing do sub-arco multi-humano).
**Slice B ✅** (wrap-up no transfer — hook type `segment_wrapup`, fim-de-segmento sem armar close; Mudança 11).
**Fase 3 ✅ COMPLETA** (2026-06-13): **3a** (close governado por `_has_continuation` + marcador
`session:closed` condicional, parity-preserving single+transfer), **3b-i** (`on_contact_end` no schema +
cutover YAML + `infra/migrations/g7_nps_to_on_contact_end.py` p/ pools de DB + dispatch no bridge nos 4
sites; sem `arm_close` — wrap-up via `on_human_end` e NPS via `on_contact_end`, ambos armam `posatt`;
completion handler genérico inalterado), **3b-ii** (editor de `on_contact_end` na UI de Pools + i18n;
fecha o invariante UI-editable). Invariante de posse: dono = `primary` corrente, posse só move via
`transfer`; `task`/`assist`/`delegate` são `specialist` que volta ao chamador. Validado E2E (single ×2 +
transfer A→B com NPS só em B, incl. pool migrado `humanoxxx`). Mudança 12/13 + g7 §10.

**Sub-arco multi-humano** (modelo **peer / Teams-like, kind-agnostic** — invariante revisada g7 §10/§11;
bloqueante p/ Fase 1). Raiz do §8.1 = identidade de participante em campo de escopo-sessão. Anchor de
ciclo de vida = **último agente com I/O ao cliente** sai (humano ou IA); `primary`/posse = papel
(analytics + NPS), não âncora; sem sucessão/owner-lifeline. Fatias:
- **Slice 1 ✅** (2026-06-13, +1b) — identidade por-participante no close (Console envia `instance_id`;
  mcp-server usa `body`; bridge lê pool/agent_type por-instance via `participant_meta`). Cada humano
  encerra seu segmento, com seu pool; contato fecha quando o último sai, em qualquer ordem.
- **Slice 2′ ✅** (2026-06-13) — wrap-up por peer humano: `other_human_active` dispara `segment_wrapup`
  para o humano que sai (incl. não-último). É a **Fase 1**. Limitação: `human_seg` keyed por pool (2
  humanos no mesmo pool colidem); customer-disconnect multi-humano → Slice 4′. Mudança 14 / g7 §11.
- **Slice 3 ✅** (2026-06-13) — fan-out msg humano↔humano (gap 1): ramo normal do agent-WS publica em
  `agent:events` + self-skip no forward. Mudança 15 / g7 §11. (Polish: atribuição-por-nome do remetente.)
- **Slice 4′ Item 1 ✅** (2026-06-13) — bridge desfaz `session:closed` em `other_human_active` (mcp-server
  segue setando síncrono; fecha o vazamento do §4).
- **Slice 4′ Item 2 — wrap-up por peer no customer-disconnect multi-humano (RETOMADO 2026-06-14, fatiado).**
  Investigação reconfirmou o nó frágil (path customer-disconnect lê 1 pool do `meta`; `segment_wrapup` por
  humano de um único evento esbarra em `hook_pending` SET + `posatt` não-armado → exige contabilidade
  aditiva). Decisão: colisão "mesmo pool" é operacionalmente inexistente (1 agente/pool); fan-out endereça
  por `instance_id`. Ver g7 §11 + `conference-mechanics.md` § Mudança 17.
  - **Fatia 1 ✅** (2026-06-14) — `human_seg:{pool}`→`{instance_id}` (dual-write + param `human_instance_id`
    em `fire_pool_hooks`; threading em 10 call-sites). Parity-preserving; validado E2E single + multi-humano
    pools distintos (`fallback=False`, zero cross-attribution). Levanta a limitação "mesmo pool" da
    Mudança 14/Slice 2′. Ver CHANGELOG.
  - **Fatia 2a ✅** (2026-06-14) — idempotência do close do agente: gate `SREM human_agents` atômico no
    topo do branch (`removed==0`→no-op), `SREM` redundante removido. Mata o double-processing (segmento
    fantasma + wrap-up duplicado). Validado E2E (não-regressão multi-humano agent_done; sem fantasma).
    Log `Duplicate/late agent close ignored`. Ver CHANGELOG + `conference-mechanics.md` § Mudança 18.
  - **Fatia 2b/3 ✅ (lado bridge) — fan-out implementado e correto; E2E bloqueado por gap-2** (2026-06-14):
    contador `contact_close_pending` + `close_arming:{conf}` + guarda no `_destroy_conference` + DECR/teardown
    na conclusão do `segment_wrapup`; customer_disconnect dispara `segment_wrapup(arm_contact_close)` por peer;
    `human_seg:{instance}` escrito no loop `customer_side`; `_contact_close_timeout_guard`. **Entrega/atribuição
    validadas** (2 human_seg WRITE, READs fallback=False, cada menu ao seu console). **NÃO fecha E2E** →
    gap-2 abaixo. Ver CHANGELOG + `conference-mechanics.md` § Mudança 19.
  - **Fatia 4 ✅ (2026-06-15)** — cleanup: logs `G7 Item1 human_seg` (READ + 2× WRITE) rebaixados a `debug`.
    Espelho `human_seg:{pool}` **mantido por decisão** — é fallback defensivo barato no `fire_pool_hooks`
    (~linha 1002, p/ sessões in-flight durante deploy); remover teria valor marginal num path frágil (close).

### Router — corrida de sobre-alocação de instância (concorrência) *(arco próprio, root-caused 2026-06-14)*
**É a causa-raiz REAL do bloqueio E2E da Fatia 2b/3** (o "gap-2 de menu" era sintoma). Cadeia confirmada:
(1) instâncias AI são **single-occupancy** — bootstrap cria N instâncias `max_concurrent=1` a partir de
`max_concurrent_sessions` (`instance_bootstrap.py:1008-1036`); (2) o consumer do routing processa inbound
**concorrente** (`main.py:149` `asyncio.create_task(_process_message)` por msg, sem serialização);
(3) `get_ready_instances`(`registry.py:161`)→`mark_busy`(`registry.py:639` `current_sessions += 1`) é
**não-atômico**, sem claim. → Dois inbound paralelos (ex.: fan-out de wrap-up) leem a mesma instância com
`current_sessions=0`, ambos a escolhem, ambos `mark_busy` `0→1` (**lost update**) → 2 sessões na MESMA
instância single-occupancy. **Visível** quando são 2 segmentos da MESMA sessão (chave de menu
`{sid}:{instanceId}` colide → inputs cruzam, menus expiram); **latente** p/ sessões distintas (só
desbalanceia carga / estoura capacidade em silêncio). **Afeta todos os pools sob concorrência.**
**Fix primário = alocação atômica** (claim que rejeita sobre-capacidade e re-seleciona). **Modelo escolhido
(decisão 2026-06-14): semáforo de contagem por-instância via SET de occupant_ids + Lua atômico** —
`claim`=SADD-se-SCARD<max, `release`=SREM, `current_sessions`=SCARD. Atômico **e idempotente** (occupant
repetido = no-op → cobre redelivery de agent_done). Por que não as alternativas: contador INCR/DECR no JSON
não é idempotente (double agent_done sub-conta); mutex grosso por-pool serializa o select+score lento e tem
fragilidade de TTL (expira no meio do trabalho → corrida volta; precisaria Redlock/fencing). A consulta
(`get_ready_instances`) é read-only e o `decide()` pontua TODOS os candidatos antes de escolher → não dá pra
"marcar na consulta"; a marca vem **depois**, atômica, com re-seleção do perdedor (otimista/CAS).
Fatias:
- **Fatia A — primitivas atômicas ✅ VALIDADA** (2026-06-14; `test_instance_semaphore.py` 5/5 contra Redis real,
  incl. 25 claims concorrentes em max=1 → 1 vencedor): `registry.py`
  ganhou `_instance_sessions_key`, Lua `_CLAIM_INSTANCE_LUA`/`_RELEASE_INSTANCE_LUA` e métodos
  `claim_instance`/`release_instance`/`instance_session_count`. **Aditiva** — nada chama ainda (zero mudança
  de comportamento). Teste de integração `tests/test_instance_semaphore.py` (Redis real, skippable): N claims
  concorrentes em max=1 → 1 vencedor; idempotência claim/release; teto multi-capacidade; claim×release sem
  lost update. **Gate**: `REDIS_URL=redis://localhost:6379 pytest test_instance_semaphore.py` verde.
- **Fatia B — wiring no `decide()` ✅** (2026-06-14): `route()` coleta candidatos pontuados → claim em cascata
  com re-seleção do perdedor (`-1`→próximo best); `_try_affinity` faz claim da instância de afinidade. occupant
  composto `"{session_id}::{conference_id}"` (confs da mesma sessão não dividem vaga). `mark_busy` sincroniza
  `current_sessions` do `SCARD` (não incrementa). **Absorveu a Fatia C**: `remove_conversation` usa
  `release_instance` (release por prefixo de sessão). Validado: suíte verde + 2 testes de re-seleção. Ver CHANGELOG.
- **Fatia C — release ✅ (foldada na B)**: `remove_conversation`→`release_instance` por prefixo. Resíduo opcional:
  `get_ready_instances`/snapshots passarem a ler `SCARD` direto (hoje leem o JSON mantido em sincronia pelo
  claim/release — funciona como hint; o claim é o gate atômico). Baixa prioridade.
- **Fatia D — gate E2E ✅ (2026-06-15)**: re-seleção + **instâncias distintas** provadas E2E (`router.claim ...
  claim=-1 — re-selecting`; `wrapup_ia-002`/`-018`), zero sobre-alocação. "Os dois wrap-ups completam"
  **validado** após a Camada 3 (Fatias A/A2; 2 runs verdes, `pushed=true` nos dois). **Arco do router
  concluído.** Residual opcional (baixa prioridade): "2 contatos simultâneos no mesmo pool → spread" não
  exercitado isoladamente.

### Camada 3 — isolamento de `pipeline_state`/lock por conferência ✅ *(resolvido 2026-06-15 — ver g7 §11 Item 2, conference-mechanics § Mudança 21)*
**Fechada.** Diagnóstico da Mudança 20 estava **errado para HEAD**: o bridge já sufixava `pipeline_session_id`
por `--seg--{segment_id}` (a evidência `5ea8dfae` era **build stale**). Bloqueios reais corrigidos:
**Fatia A** (chave de pipeline endurecida em `activate_native_agent`: `segment_id or instance_id or uuid`,
nunca `session_id` cru; fecha branch `--conf--` + YAML-fallback) e **Fatia A2** (isenção de hook no dedup
`conference:specialist:{pool_id}` que colapsava os 2 wrap-ups do mesmo pool numa corrida). Validado E2E 2×.
- **Follow-up ✅ RESOLVIDO (2026-06-15, Fatia 1 — hook-pool por segmento)**: `on_human_end`/`on_contact_end` do
  último/âncora passam a resolver o pool de `participant_meta:{instância que fecha}` (fallback `meta`), nos
  **dois** close paths (`agent_closed` `_pool_id_hooks` + `customer_disconnect` `_cs_pool_id`; cobre o
  **deferred** via stash). Validado E2E (admin último → `origin_pool=retencao_humano`; pré-fix `humanoxxx`).
  Ver CHANGELOG 2026-06-15 + conference-mechanics § Mudança 22.
- **Gaps remanescentes do modelo de hooks (follow-ups, baixa prioridade)**: (2) survey **customer-side
  por-segmento** (grão=segment NPS) não dispara p/ peers no fan-out — `segment_wrapup` reusa a lista
  `on_human_end` mas filtra `side=agent` (`main.py` ~938), então surveys customer-side só saem na âncora/
  primário; (4) binding **grão↔boundary** (skill em "contact ends" gravar `grain=session`) é convenção, não
  contrato; disparo **grão=journey** não plumbado (sem boundary de fim-de-journey) → F11. Convergir
  `on_human_end`(último)+`segment_wrapup`(peers) num mecanismo único de wrap-up por-segmento = higiene opcional.
- **Hardening opcional (gap-2 menu)**: chave de menu por `segmentId` como defesa-em-profundidade p/ pools com
  `max_concurrent>1` legítimo + 2 segmentos da mesma sessão. **Desnecessário** após a alocação atômica
  (concorrentes vão para instâncias distintas) + Fatia A (pipelines distintos). Encerrado salvo regressão.

### Latência do `@mention` de humano — RESOLVIDO (não é bug, 2026-06-15)
**Conclusão**: não havia latência anômala. O `@mention` de um pool cujo **agente humano ainda não logou** cai
na fila do contato (nenhuma instância `ready` no pool) e é entregue assim que o operador **faz login**
(`agent_ready` → `Queue drain: ... became ready`). Comportamento **esperado**. Confirmado: com o operador
logado, `@mention` → `Routed → human-…` em **~33 ms** (sem fila); sem login, aguarda na fila até o login. Os
"alguns segundos / nem sempre" eram o tempo até o operador (`humanoxxx`) logar — o que estava logado/servindo
era o `retencao_humano` (admin), daí a impressão de "coincidir com a escalação". **Sem ação.** (Mecanismo de
referência: convite a pool sem agente ready → `Contact persisted to queue` → drain no `agent_ready`; eventual
melhoria de UX — sinalizar no Console "convidando, aguardando login do agente" — é cosmética, não bug.)

**Sub-arco multi-humano: Slices 1/2′/3/4′ ✅; Item 2 ✅ (Camada 3, 2026-06-15).** Restam só os arcos próprios
abaixo (unificação de contabilidade; queda involuntária de humano) + o follow-up `_cs_pool_id` acima.

### Unificação de contabilidade de agente (kind-agnostic) *(arco próprio, proposta — diferido)*
Anchor "último agente customer-facing" hoje é aproximado por **4 chaves** com papéis distintos:
`human_agent` (flag → entrega inbound/guard), `human_agents` (SET → remaining/close/restore/participant_left),
`ai_agents` (SET → restore + leitura supervisor/bpm), `active_ai_specialists` (SET → defer/continuação).
Três dimensões misturadas: anexação × kind (entrega/restore/wrap-up) × estado (rodando). Alvo: HASH único
`session:{id}:agents → {kind, role, customer_facing, running}` do qual as 4 respostas são derivadas.
**Investigação 2026-06-13 — DIFERIDO**: é refactor **puro-interno** (não corrige bug; o modelo de 2 sets+defer
já aproxima o anchor), toca o caminho **mais frágil** (close) + consumidores cross-package (mcp-server
supervisor/bpm/server) e só é gateável por **paridade**. Alto custo/risco, payoff diferido (manutenibilidade).
**Decisão**: fazer **oportunística** (quando um bug concreto justificar ou encostada em feature que já toque
essas chaves), não como refactor standalone. Heartbeat priorizado antes (valor real).
**Re-avaliação 2026-06-15 (pós Camada 3 + fan-out) — MANTÉM DIFERIDO**: as entregas recentes adicionaram
bookkeeping paralelo (`human_seg:{instance}`, `hook_conf`, `posatt`, `contact_close_pending`, `close_arming`)
**por cima** das 4 chaves, sem reestruturá-las; e os bugs corrigidos estavam no `session:meta` (last-writer) e
no dedup por `pool_id`, **não** nas 4 chaves de contabilidade (que seguem aproximando o anchor corretamente).
Nenhum dos 2 gatilhos foi atingido. Argumento contra reforçado: close path recém-estabilizado (2 runs verdes),
refactor gateável só por paridade com raio cross-package (mcp-server supervisor/bpm/evaluation). Mapa atual:
`human_agent` flag (~10 sites, hot path entrega) · `human_agents` SET (~10: remaining/restore/participant_left/
fan-out) · `ai_agents` SET (~8: restore no close) · `active_ai_specialists` SET (~7: defer G2). Único
incremento baixo-risco se encostar no path de entrega: derivar `human_agent` de `SCARD(human_agents)>0` — mas
há aresta (flag setada mesmo com `instance_id` vazio em `activate_human_agent` → não é 1:1). Manter HASH único
oportunístico.

### Detecção de queda involuntária de humano *(arco próprio)*
Humano que cai (disconnect/crash) deixava o contato órfão (gap G4). Alvo: drop → re-rota ao pool do dono
(posse re-estabelecida por alocação, não promoção); contato vivo sob os agentes customer-facing restantes.
- **Slice 1 ✅** (2026-06-13) — detecção via `ws.close`+grace (mcp-server publica
  `contact_closed(agent_disconnect)` p/ sessões onde o humano ainda está em `human_agents`) + bridge:
  `remaining>0` sem peer wrap-up; `remaining<=0` re-rota `conversations.inbound` ao `_ha_pool`. Mudança 16.
- **Slice 2 ✅** (2026-06-13, hardening) — pong-tracking: ping de PROTOCOLO (`ws.ping`, auto-respondido
  pelo browser) + evento `pong` reseta `isAlive`; sem pong num ciclo de 30s → `ws.terminate()` dispara
  `ws.close` → grace → `agent_disconnect` (Slice 1). Fecha o "drop sujo" (meia-conexão que não emite
  `close`). Mudança 16 (adendo). **Arco heartbeat completo.**

---

## Frente 3 — Revisão de config / eliminar seeds *(em curso)*

Meta: produção sem seeds re-aplicados — DB é fonte de verdade; setup inicial de DB versionado.
- **Fase 1 ✅ (2026-06-15)** — **seed-if-absent / DB-owned** no `RegistrySyncer` (`registry_syncer.py`): no 409,
  não sobrescreve pool config nem deploy-slot (capacidade); edições de UI sobrevivem a rebuild. Env
  `REGISTRY_SYNC_RECONCILE=true` = reconcile legado (YAML vence) p/ dev. Skills seguem upsert (código). Curou o
  sintoma "Transfer/`escalation_pools` some a cada build". Ver CHANGELOG 2026-06-15 + CLAUDE.md § Configuration.
- **Fase 2 — correção ✅ / arquitetura DIFERIDA (auditoria 2026-06-15)**: a auditoria por store mostrou que
  **todos já são seed-if-absent** (pools via Fase 1; config-api `overwrite=False`; pricing/evaluation checam
  existência; users 409; catálogo ABAC e skills re-aplicam de propósito = código). Ou seja, **não há bug
  pendente** — a "config some no rebuild" está resolvida. O que sobra é só o **sonho arquitetural** (converter
  seeds/YAML em **migração versionada if-absent**, modelo `initdb/01_platform_config.sql`, aposentando
  `infra/seed/*.py` + YAML de registry, store por store) — **baixa urgência**, burn-down gradual sem retrabalho.
  Resíduo opcional: `set_module_config` do `seed_auth` if-absent (demo-users). Ver `docs/arcos/config-
  consolidation.md` §9.
- **Doc** ✅ — `docs/arcos/config-consolidation.md` existe; atualizado com a auditoria + precedência seed-if-
  absent (§9). Referências de `CLAUDE.md`/`registry_syncer.py` resolvem.

---

## Hardening de Auth — postura de sessão do Console *(proposta — não é bug)*

Hoje (Arc 7, por design): `access_token` em memória; `refresh_token` em `localStorage('plughub_refresh_token')`
→ **silent re-auth** no mount (`POST /auth/refresh`). Reabrir a URL após fechar a aba entra logado sem
credencial — esperado, mas é um trade-off UX×segurança. Levers de endurecimento (cada um é arco próprio,
escolher conforme exigência de segurança para um console que vê PII):
- **refresh_token em cookie httpOnly** (em vez de `localStorage`) → mitiga exfiltração por XSS. Maior
  mudança (auth-api seta cookie; CORS/SameSite; CSRF token).
- **Idle/inactivity timeout** — não existe hoje; sessão dura enquanto o refresh_token for válido. Adicionar
  expiração por inatividade no Console + invalidação no auth-api.
- **TTL do refresh_token** — encurtar no auth-api (hoje rotaciona indefinidamente enquanto usado).
- **"Fechar aba = deslogar"** — trocar `localStorage` por `sessionStorage` (morre com a aba); custo de
  conforto (reloga a cada nova aba).
Decisão de produto/segurança pendente: qual combinação aplicar. Sem isso, manter o comportamento atual.

---

## F11 — Pesquisa multi-grão outbound *(planejamento)*

A avaliação do cliente é **outbound** e pode rodar em **até 3 grãos** (`session_signal`: `journey | session |
segment`), configurável por fluxo: avaliar a journey inteira, cada contato, e cada segmento em cada contato.
Base parcial na F10.2b (`survey_collector_ia`/`survey_reconnect_ia` + `survey_record grain=segment`). **Falta o
planejamento da orquestração**: quando/como cada grão dispara (1 ao fim do contato/journey + N por segmento,
diferidas, `captured_at≠session_at`). Arco de **evaluation**, separado do G7 (ciclo de vida). O F5 inline (grão
segmento) está ✅ concluído; a riqueza "N sinais por agente" mora aqui, não no inline. Ver
`docs/arcos/g7-segment-contact-decoupling.md` §5.

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

## Business in Any Media — processo channel-abstract + framework de loja *(proposta — não implementado)*

Reposicionamento process-centric ("nunca perca um negócio por causa de canal") + framework de comércio conversacional sobre o modelo de 3 níveis (a = fluxo negocial channel-abstract; b = acesso a canais; c = agente de I/O). Especificações em `docs/product/`:

- **Arquitetura-alvo (3 níveis)** — [`docs/product/business-in-any-media-arquitetura-alvo.md`](docs/product/business-in-any-media-arquitetura-alvo.md) + diagrama `business-in-any-media-3-niveis.svg`. Define as 3 camadas, contratos, e o que falta construir no nível (b).
- **Resolvedor de identidade + cadastro (nível b)** — [`docs/product/identity-resolver-nivel-b-spec.md`](docs/product/identity-resolver-nivel-b-spec.md) + sequência `identity-resolver-sequencia.mermaid`. Generaliza o `pending_workflow` existente: cadastro nativo (`customer_id` canônico, dois andares Redis/PG), índice multi-âncora hasheado, retomada cross-canal. Governança: plataforma não é autoridade de identidade/pagamento; só chaves mascaradas; uso interno.
- **Contrato delegate por pool (a→b)** — [`docs/product/delegate-contrato-por-pool-spec.md`](docs/product/delegate-contrato-por-pool-spec.md). Delegação por pool (não skill); decidido alinhar `task.target` a pool; 1 skill publicada por pool; gate de identificação como lógica de fluxo (não campo de schema).
- **Commerce-cards (nível c)** — [`docs/product/commerce-cards-nivel-c-spec.md`](docs/product/commerce-cards-nivel-c-spec.md). `component` tipado em `notify`/`menu` (product_card/carousel/cart/checkout/order_status), render nativo por canal; checkout com masked input + repasse ao PSP; novas ChannelCapability `rich_card`/`carousel`.
- **Fluxo de intake (nível c)** — [`docs/product/intake-flow-nivel-c-spec.md`](docs/product/intake-flow-nivel-c-spec.md). Generaliza o `agente_portabilidade_intake_v1`: resolve identidade (origem do canal) → checa pendência → oferta de retomada → roteia intenção; gate de identificação flow-wired.

Descritivo técnico-funcional consolidado (com a seção de roadmap §20.7): [`docs/product/plughub-descritivo-tecnico-funcional.md`](docs/product/plughub-descritivo-tecnico-funcional.md) (+ `.html` print-ready) — **manter atualizado conforme cada item for implementado**.

**Base que já existe** (não confundir com o gap): workflow + canais + suspend/resume + retomada via `pending_workflow` + masking. **A construir**: cadastro de identidade completo, commerce-cards, gate, e o nível (b) como camada de primeira classe.

---

## Fila de trabalho humano / dispatch pull + inbox no Console *(proposta — não implementado)*

Modo de despacho **pull** genérico no Routing Engine (operador puxa da fila) + inbox no Console, tendo a **fila de aprovação** como primeira especialização (revisão de processo montado por IA num passo anterior). Especificações em `docs/product/`:

- **Dispatch pull genérico** — [`docs/product/routing-pull-dispatch-spec.md`](docs/product/routing-pull-dispatch-spec.md). `dispatch_mode: push|pull` no `PoolConfig` (único toque de schema); reusa o sorted set de fila; claim atômico via `ZREM` (alocação concedida pelo routing — invariante preservada); lease TTL + auto-release event-driven (crash_detector); release re-enfileira pelos critérios do routing; ordenação por peso da fila + tags `session.queue.*` no ContextStore; respeita `max_concurrent_sessions`.
- **Fila de aprovação (especialização)** — [`docs/product/human-work-queue-aprovacao-spec.md`](docs/product/human-work-queue-aprovacao-spec.md). Item = sessão de workflow suspensa (delegate ao pool pull); pacote (form padrão + extensão + `decisions`); decisão volta pelo **retorno do delegate** (`output_as: step.id` já existe — sem schema novo); workflow principal roteia (`choice`); edição auditada.
- **Inbox no Console (UI)** — [`docs/product/pull-inbox-console-ui-spec.md`](docs/product/pull-inbox-console-ui-spec.md). Integrada ao atendimento (rail de filas piscando → lista → preview no centro → "Pull" na action bar); cor por SLA (verde/amarelo/vermelho); notificação via ciclo do heartbeat; gating de capacidade.

Liga com o **gate de promoção** homologação→produção (descritivo §20.1): promover vira um workflow com passo de aprovação.

**Status (2026-06-15):** plano consolidado em `docs/product/frente1-dispatch-pull-aprovacao-plano-consolidado.md`
(módulos + task list + esforço; decisões D1–D3 resolvidas). Sub-fatiamento da F1 (pull core) confirmado:
F1.0 (plumbing `dispatch_mode`) → F1.1 (branch `route()`) → F1.2 (claim atômico) → F1.3 (lease).
- **F1.0 ✅ (2026-06-15)** — `dispatch_mode: push|pull` (default push) ponta a ponta: `@plughub/schemas`
  `PoolRegistrationSchema`, agent-registry (coluna Prisma + migração + POST/PUT), routing `PoolConfig` +
  `kafka_listener`, **UI select** na PoolsPage (+ i18n). Aditivo. Validado (`teste_demo` → `dispatch_mode=pull`).
- **F1.1 ✅ (2026-06-15)** — branch no `route()` (pool pull → parqueia, pula `_allocate`, reusa caminho queued)
  + `_drain_queue_for_agent` e `_periodic_queue_drain` pulam pools pull. Validado: push byte-parity; pull
  parqueia (`Contact persisted to queue pool=teste_demo`) sem `Routed`/drain.
- **F1.2 ✅ (2026-06-15)** — claim atômico no Router: `work_task_claim` (`ZREM` 1-vencedor + `claim_instance`
  no semáforo do recurso + rollback se −1 + `mark_busy` + lease + publica `conversations.routed` → reusa
  bridge/Console) e `work_task_release` (lease off + `release_instance` + re-enfileira). Registry:
  `atomic_claim_dequeue`, `write/delete_claim_lease`. Testes `test_work_queue_claim.py` 5/5 + suíte 96 verde.
  Invocação (tool mcp-server) é F2.
- **F1.3 ✅ (2026-06-15)** — `claim_lease_s` no config-api (ns `routing`, 180) + `routing_config` + `Router`
  lê dele; branch pull do `route()` **deleta a claim lease** no re-parque. **Correção do desenho**: o
  crash_detector **pula humanos** → o auto-release de pull (humano) é **emergente**: desconexão (mcp-server WS
  lifecycle / arco "queda involuntária") → bridge re-roteia → `route()` parqueia (F1.1) + limpa lease → contato
  volta claimável + vaga liberada por `agent_done`/`release_instance`. **Diferido** (spec "sem sweep dedicado"):
  renovação da lease por heartbeat + sweeper de "conectado-mas-ocioso" (a inbox da F2 sinaliza melhor). Testes
  6/6 + suíte 96 verde. **Pull core (F1.0–F1.3) COMPLETO.**
- **F2 (próxima)** — tools mcp-server (`work_queue_list`/`claim`/`release`) + **API HTTP no routing** (o engine
  não tem hoje) + **inbox no Console** (3-zonas, preview→Pull, cor SLA, capacidade). É onde o pull vira usável
  ponta-a-ponta (E2E pela UI) + valida o auto-release completo.

**Achados pré-existentes (registrados durante a F1.0 — NÃO causados por ela; F1.0 é inerte):**
- **A — specialist-return (pré-requisito da F4)**: um conference specialist (ex.: `auth_form_ia` via @mention)
  que termina com `escalate` re-roteia o CONTATO em vez de **voltar ao chamador**. O `agente_auth_form_v1.yaml`
  escala nos dois caminhos (sucesso/falha) → invocado como specialist (admin servindo), escala pro
  `retencao_humano` → fila → drena de volta (sintoma: mensagem de fila espúria). Modelo-alvo (definição do
  usuário): invite/task **sempre voltam ao chamador**. Fix preferido: **engine** — flow em modo conference
  specialist trata `escalate`/`complete` como **retorno-ao-chamador** (devolve outcome), não re-roteia o contato.
  É o **núcleo da F4** (aprovação = specialist que devolve outcome). Sub-arco próprio.
- **B — multi-sessão humana no push (ligado ao pull)**: humano servindo entra `state="busy"`;
  `get_ready_instances` exige `state=="ready"` → mesmo com vaga (`max_concurrent=3`; a cap humana vem da URL do
  WS do Console — `mcp-server` server.ts:2147 default 3 — não do `auth`), um humano em atendimento não recebe 2º
  contato concorrente via **push** → vai pra fila. É o gap que o **pull (F1)** endereça (o humano puxa o
  próximo). Decisão de modelo: o push também deveria oferecer (manter `ready` enquanto sob capacidade)? Medir ao
  vivo (`state`/`current_sessions`/`max_concurrent` da instância) se for atacar.

---

## Record/Replay Harness — gravação/replay em todas as costuras *(proposta — não implementado)*

Visão + spec em [`docs/product/record-replay-harness-spec.md`](docs/product/record-replay-harness-spec.md). Generaliza o Session Replayer (que hoje replaya só o stream da sessão, para avaliação) num harness "VCR" em todas as costuras (channel-gateway, AI Gateway, MCP, Kafka) — cada costura como **driver** (injeta inputs gravados) ou **mock** (devolve outputs gravados), com timings.

**Base que já existe**: `session-replayer` (persister/hydrator/replayer/comparator), `ComparisonReport` (Jaccard + deltas), `delta_ms`/`speed_factor`, Kafka como log, harness `e2e-tests`. **A construir**: captura full-fidelity de payload em MCP/AI Gateway (hoje `mcp.audit` é só metadado), clock/seed injetável (determinismo), harness multi-costura, gravação seletiva (golden/amostrada/on-demand) com masking, e o **gate de promoção** consumindo o `ComparisonReport` como critério objetivo. Aplicações: regressão determinística, repro de bug, simulação de carga, datasets de avaliação.

---

---

