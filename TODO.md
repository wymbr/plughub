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
  1. **`$.segment_id` no `interpolate.ts`** (1 linha em `resolveJsonPathRef`: `segment_id: ctx.segmentId`
     no evalContext) — quick win; destrava sinal de segmento "sobre si mesmo" genérico via skill.
  2. **F11 — survey diferida + grão journey E2E**: resolver `session_at` da sessão original via
     **enrichment no consumer** (`parse_session_signal_event` quando `captured_at ≠ session_at`, lookup
     `analytics.sessions.opened_at` por `origin_session_id`); workflow agendado disparando `survey_record`
     dias depois; grão `journey` ponta-a-ponta. Schema/tool já comportam (sem migração).
  3. **`quality_criteria` cross-form**: alinhar dimensões equivalentes entre formulários (por
     `dimension_id`/label) — hoje compara só dentro do mesmo form (guard na UI).
  4. **Validações E2E reais F5/F7 + limpeza de fixtures sintéticos**: agora que há fluxos reais
     (NPS humano via Agent Assist, survey via webchat), substituir os fixtures de
     `evaluation_dimension_scores` (F8) e `segments.escalation_reason` (F7) por dado E2E; rodar F5/F7
     ponta-a-ponta (multi-humano / escalação real).
  5. **DROP da coluna `segments.nps_score`** (polish): após confirmar `session_signal` como fonte única
     — `ALTER TABLE segments DROP COLUMN nps_score` + remover do DDL/cols/row-builder/parser.
  6. **Débitos de teste pré-existentes**: corrigir as 6 falhas `TestQueryAgentAvailabilityReport`
     (assinatura `query_agent_availability`) e as 3 de `resolve.test.ts` (BLPOP/mention mocks).
  **Nota técnica F10.3 — contexto de atribuição para `survey_record(grain=segment)` (recon 2026-06-10):**
  o que o skill já tem vs. o que falta para chamar `survey_record` com atribuição:
  · `session_id` — **disponível** à YAML como built-in `$.session_id` (`interpolate.ts` `resolveJsonPathRef`,
  junto de `tenant_id`/`customer_id`/`instance_id`). Logo `grain=session|workflow|journey` é direto.
  · `segment_id` do PRÓPRIO agente — o bridge **já passa** no `/execute` (`activate_native_agent`
  `payload["segment_id"]`, main.py ~465) → `StepContext.segmentId`; usado em `@segment.*` e escritas
  `scope: segment`. **MAS não está exposto como built-in `$.segment_id`** — falta 1 linha em
  `resolveJsonPathRef` (`segment_id: ctx.segmentId` no evalContext) para o skill lê-lo e passar à tool.
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

## Fila de sistema — tier gratuito *(arco ATIVO — spec em discussão→fechada 2026-06-05)*

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

---

## Record/Replay Harness — gravação/replay em todas as costuras *(proposta — não implementado)*

Visão + spec em [`docs/product/record-replay-harness-spec.md`](docs/product/record-replay-harness-spec.md). Generaliza o Session Replayer (que hoje replaya só o stream da sessão, para avaliação) num harness "VCR" em todas as costuras (channel-gateway, AI Gateway, MCP, Kafka) — cada costura como **driver** (injeta inputs gravados) ou **mock** (devolve outputs gravados), com timings.

**Base que já existe**: `session-replayer` (persister/hydrator/replayer/comparator), `ComparisonReport` (Jaccard + deltas), `delta_ms`/`speed_factor`, Kafka como log, harness `e2e-tests`. **A construir**: captura full-fidelity de payload em MCP/AI Gateway (hoje `mcp.audit` é só metadado), clock/seed injetável (determinismo), harness multi-costura, gravação seletiva (golden/amostrada/on-demand) com masking, e o **gate de promoção** consumindo o `ComparisonReport` como critério objetivo. Aplicações: regressão determinística, repro de bug, simulação de carga, datasets de avaliação.

---

---

