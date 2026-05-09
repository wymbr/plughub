# CHANGELOG — PlugHub Implementações Concluídas

---

## Pool Registry routing_expression — verificado como já implementado (2026-05-09)

Code review confirmou que `routing_expression` está completamente suportado:
- `PoolRegistrationSchema` (schemas) — campo `routing_expression: RoutingExpressionSchema.optional()`
- Prisma schema — `routing_expression Json?`
- `validators/pool.ts` — `CreatePoolSchema`/`UpdatePoolSchema` herdam via extend/partial
- `routes/pools.ts` — create e update ambos persistem o campo corretamente

TODO.md: seção "Pool Registry — routing_expression field" removida.

---

## Task #41 — Remover category do AI Gateway + schemas (2026-05-09)

O AI Gateway é producer puro de dados de sentimento — não deve classificar scores em categorias. As faixas (satisfied/neutral/frustrated/angry) são configuráveis por tenant via Config API; qualquer classificação feita com valores hardcoded seria incorreta após mudança de configuração. A responsabilidade de classificar pertence ao consumer (analytics-api), que tem acesso às faixas corretas.

**Alterações:**

`packages/schemas/src/platform-events.ts`:
- `SentimentUpdatedEventSchema` — removido campo `category`. Comentário documenta explicitamente que a classificação é responsabilidade do consumer.

`packages/ai-gateway/src/plughub_ai_gateway/`:
- `sentiment_config.py` — deletado (classificação dinâmica não pertence ao AI Gateway).
- `sentiment_emitter.py` — removidos `_classify()`, import `sentiment_config`, campo `category` do payload Kafka, contagens por categoria (`satisfied/neutral/frustrated/angry`) do hash `sentiment_live`. Hash agora contém apenas `avg_score`, `score_total`, `count`, `last_session_id`, `updated_at`.
- `config.py` — removido `config_api_url` (não mais necessário).
- `main.py` — removidos import `sentiment_config`, `reload()` no startup, `refresh_loop()` background task e cancel no teardown.

TODO.md: seção "Sentimento — _classify() dinâmico" já havia sido removida na sessão anterior.

---

## Scheduled Deploy gap — verificado como já implementado (2026-05-09)

Code review revelou que o TODO "Skill Flow — Scheduled Deploy gap" já estava resolvido. Items confirmados como presentes:

- `_resolve_flow_definition()` em `workflow-api/router.py` (linhas 125–153): busca o skill pelo `flow_id` no agent-registry quando `metadata` não contém `flow_definition`, e injeta automaticamente antes de salvar a instância.
- Chamado em `trigger_workflow` via `resolved_metadata = await _resolve_flow_definition(...)`.
- `agent_registry_url` configurado em `workflow-api/config.py`.
- `skill_scheduled_deploy_v1.yaml` presente em `skill-flow-engine/skills/`.
- `scheduleSkillDeploy()` removido da UI no Task #33 (seção de agendamento foi retirada do `AgentFlowDeployPage`).

TODO.md: seção "Skill Flow — Scheduled Deploy gap" removida.

---

## Session TTLs dinâmicos via Config API (2026-05-09)

Eliminados ~25 literais `14400` hardcoded de 3 componentes. Todos os TTLs de sessão Redis agora lidos do namespace `session` do Config API no startup, com fallback silencioso aos valores default (14400s / 3600s).

**Arquivos modificados:**

`packages/orchestrator-bridge/`:
- `session_config.py` (novo) — `SessionConfigCache`, mesmo padrão do `RoutingConfigCache`. Singleton `session_config`. Defaults espelham `seed.py`.
- `main.py` — importa `session_config`; adiciona `CONFIG_API_URL` env var; função `_stl()` retorna TTL dinamicamente; substitui todos os literais `14400` por `_stl()`; carrega Config API no startup; `_handle_config_changed` agora aceita `http` e invalida + recarrega ao receber `namespace=session`.

`packages/conversation-writer/`:
- `config.py` — adiciona `config_api_url: str = "http://localhost:3500"`.
- `main.py` — função `_fetch_session_ttl()` busca `transcript_ttl_s` via `urllib.request` no startup; `RedisBuffer` criado com TTL dinâmico.

`packages/session-replayer/`:
- `stream_hydrator.py` — `StreamHydrator.__init__` recebe `ttl: int = HYDRATION_TTL_SECONDS`; usa `self._ttl` internamente.
- `replayer.py` — `Replayer.__init__` recebe `context_ttl: int = REPLAY_CONTEXT_TTL`; usa `self._context_ttl` no `redis.set(ex=...)`.
- `consumer.py` — `SessionReplayerConsumer` adiciona `CONFIG_API_URL` env var; `start()` busca `replayer_hydration_ttl_s` e `replay_context_ttl_s` do Config API antes de iniciar consumers; passa TTLs ao construir `StreamHydrator` e `Replayer`.

TODO.md: item "orchestrator-bridge TTLs dinâmicos" removido.

---

## Arc 8 — Disponibilidade e Pausas de Agentes — backend completo (verificado 2026-05-09)

Verificação de code review revelou que todo o backend Arc 8 já estava implementado. TODO.md estava desatualizado. Itens confirmados como presentes:

- `AgentPauseEventSchema` em `platform-events.ts` — campos `reason_id`, `reason_label`, `note` presentes.
- Config API seed `agent_activity.pause_reasons` — já presente em `seed.py`.
- `PUT /api/agent-pause` e `PUT /api/agent-resume` em `mcp-server-plughub/src/server.ts` (linhas 944–1050) — publicam `agent_pause`/`agent_ready` em `agent.lifecycle` com `reason_id`/`reason_label`.
- `parse_agent_lifecycle` em `analytics-api/models.py` — processa `agent_pause` (action=open) e `agent_ready` (action=close_check).
- Tabela `agent_pause_intervals` (ClickHouse `ReplacingMergeTree`) em `clickhouse.py`.
- `upsert_agent_pause_interval` e Redis state machine no consumer (`consumer.py`).
- `GET /reports/agent-availability` em `reports.py` + `query_agent_availability` em `reports_query.py`.
- Testes unitários em `tests/test_reports.py`.

TODO.md: seção Arc 8 removida (todos os itens concluídos).

---

## Config API Seed — novos namespaces e chaves de sessão (2026-05-09)

**Arquivo:** `packages/config-api/src/plughub_config_api/seed.py`

**O que mudou:**

Namespace `session` — 6 chaves TTL adicionadas para centralizar todos os TTLs de Redis em um único namespace, eliminando literais hardcoded nos componentes:
- `orchestrator_session_ttl_s` (14400) — bridge session state
- `transcript_ttl_s` (14400) — conversation-writer
- `replayer_hydration_ttl_s` (3600) — Hydrator (session_replayer)
- `replay_context_ttl_s` (3600) — ReplayContext hash
- `pool_config_ttl_s` (3600) — PoolConfigCache no orchestrator-bridge
- `sentiment_live_ttl_s` (300) — sentiment_live hash (duplicado de `sentiment.live_ttl_s` para o bridge ler tudo de um namespace)

Namespace `audit_policy` — substitui `masking` como namespace primário para políticas de mascaramento/auditoria (LGPD). Chaves: `authorized_roles`, `default_retention_days`, `capture_input_default`, `capture_output_default`. Entradas `masking.*` mantidas como aliases deprecados com prefixo `[DEPRECATED]` na descrição.

Namespace `analytics_consumer` — substitui `consumer` como namespace primário para configuração do consumidor Kafka da analytics-api. Entradas `consumer.*` mantidas como aliases deprecados.

Docstring do arquivo atualizado para listar namespaces ativos vs aliases deprecados.

---

## Bugfix — Routing Engine: sessões órfãs na fila ao desconectar cliente (2026-05-08)

**Problema:** Quando o cliente WebSocket desconectava (refresh, fechar aba, queda de rede), a sessão permanecia no ZSET `{tenant}:pool:{pool_id}:queue` indefinidamente — exibida na tela operacional de pools como sessão ativa, gerando falso-positivo de SLA excedido.

**Root cause:** O Channel Gateway já publicava `ContactClosedEvent` em `conversations.events` corretamente. O Routing Engine não consumia esse tópico e nunca gravava a chave `session:{id}:closed` no Redis — que é o marcador que o `_drain_queue_for_agent` usa para pular sessões encerradas.

**Solução (apenas routing-engine):**

- ✅ `config.py` — adicionado `kafka_topic_events: str = "conversations.events"`
- ✅ `kafka_listener.py` — novo `SessionClosedEventHandler`:
  - Recebe `event_type="contact_closed"`, grava `session:{id}:closed = reason` (TTL 7d)
  - Lê `{tenant}:queue_contact:{session_id}` para descobrir `pool_id`, remove do ZSET e deleta o JSON
- ✅ `kafka_listener.py` — `run_listeners()` e `_dispatch()` atualizados para aceitar e rotear `kafka_topic_events`
- ✅ `main.py` — `run_listeners` chamado com `kafka_topic_events = settings.kafka_topic_events`
- ✅ `main.py` — `_periodic_queue_drain` agora verifica `session:{id}:closed` antes de re-rotear (defesa em profundidade)

**Componentes alterados:** `routing-engine` apenas. Channel Gateway, Core e orchestrator-bridge sem alteração.

---

## Task #38 — Calendar Arc: modelo dois níveis + feriados recorrentes + detecção de conflitos (2026-05-08)

**Problema:** O calendário era um template compartilhado por referência, o que significava que exceções de um pool (manutenção, etc.) vazavam para outros pools que usassem o mesmo calendário. Feriados não podiam ser marcados como recorrentes anuais. Datas sobrepostas entre conjuntos de feriados eram silenciosamente resolvidas pelo último registro.

**Solução:** Modelo dois níveis onde (1) o calendário define a política compartilhada e (2) a associação `pool ↔ calendário` carrega exceções específicas do pool com prioridade 1 no motor.

**calendar-api — db.py:**
- ✅ `_DDL_ASSOCIATIONS` — coluna `exceptions JSONB NOT NULL DEFAULT '[]'` na tabela `calendar_associations`
- ✅ `_DDL_ASSOC_ADD_EXCEPTIONS` — migration idempotente `ADD COLUMN IF NOT EXISTS`
- ✅ `ensure_schema` — executa a migration idempotente
- ✅ `_row_to_assoc` — inclui `exceptions` no retorno
- ✅ `db_create_association` — persiste `exceptions`
- ✅ `db_update_association` — PATCH de `exceptions` de uma associação existente
- ✅ `db_upsert_pool_association` — upsert `ON CONFLICT DO UPDATE SET exceptions = EXCLUDED.exceptions`
- ✅ `db_delete_associations_for_entity` — remove todas as associações de uma entidade
- ✅ `db_get_associations_for_engine` — merge de exceções: `assoc.exceptions` (prioridade) + `cal.exceptions` (deduplica por date)

**calendar-api — router.py:**
- ✅ `AssociationCreate` — aceita `exceptions: list[dict] = []`
- ✅ `AssociationUpdate` — Pydantic model para PATCH
- ✅ `AssociationUpsert` — Pydantic model para PUT /upsert
- ✅ `PATCH /v1/associations/{id}` — atualiza exceções
- ✅ `PUT /v1/associations/upsert` — idempotent upsert (limpa associações antigas, cria nova com exceções)
- ✅ `DELETE /v1/associations/entity` — remove todas associações de uma entidade

**agent-registry:**
- ✅ `prisma/schema.prisma` — `calendar_id String?` no model Pool
- ✅ `prisma/migrations/20260508220000_add_pool_calendar_id/migration.sql`
- ✅ `src/routes/pools.ts` — persiste `calendar_id` no create + update
- ✅ `packages/schemas/src/agent-registry.ts` — `calendar_id: z.string().uuid().optional()` em `PoolRegistrationSchema`

**platform-ui — CalendarsPage.tsx:**
- ✅ `HolidaysEditor` — checkbox "Repete todo ano": salva `MM-DD` em vez de `YYYY-MM-DD`; badge ↺ na lista de feriados recorrentes
- ✅ `conflictDates` — `useMemo` detecta datas sobrepostas entre conjuntos selecionados
- ✅ Warning ⚠️ inline no seletor de conjuntos quando há datas duplicadas

**platform-ui — PoolsPage.tsx:**
- ✅ `PoolExceptionsEditor` — componente inline para gerenciar exceções do pool (fecha o dia todo ou horário especial)
- ✅ `calExceptions` state — carregado assincronamente da associação existente ao abrir o drawer
- ✅ `loadPoolAssociation` — busca associações do pool em `GET /v1/associations`
- ✅ `handleSubmit` — após salvar o pool no agent-registry, faz PUT `/v1/associations/upsert` (com calendar_id + exceptions) ou DELETE `/v1/associations/entity` (se sem calendário)
- ✅ Seção "Exceções deste Pool" aparece no drawer somente quando `calendar_id` está definido

**i18n:**
- ✅ `en/calendars.json` — `holidaySet.recurringLabel`, `calendar.holidayConflict`, `calendar.holidayConflictHint`
- ✅ `pt-BR/calendars.json` — traduções correspondentes

---

## Task #37 — Config/Channels: hierarquia Conta → Endpoints (2026-05-08)

**Problema:** GatewayConfig (credenciais) e ChannelEndpoint (endereços → pools) eram entidades paralelas sem relação explícita, listadas em sub-tabs separadas.

**Solução:** FK nullable `gateway_config_id` no ChannelEndpoint + UI hierárquica onde cada conta (GatewayConfig) é um card pai com seus endpoints filhos abaixo.

**Backend — agent-registry:**
- ✅ `prisma/schema.prisma` — `gateway_config_id String?` em ChannelEndpoint + relação bidirecional com GatewayConfig
- ✅ `prisma/migrations/20260508200000_channel_endpoint_gateway_config_fk/migration.sql` — ADD COLUMN + índice
- ✅ `src/types/channel-endpoint.ts` — `gateway_config_id: string | null` no type shim + `updateMany` adicionado
- ✅ `src/routes/channel-endpoints.ts` — aceita `gateway_config_id` em POST + PUT; filtro em GET; webhook adicionado a VALID_CHANNELS

**Frontend — platform-ui:**
- ✅ `src/types/index.ts` — `gateway_config_id: string | null` em ChannelEndpoint; `gateway_config_id?` em Create/UpdateChannelEndpointInput
- ✅ `src/api/registry.ts` — `listChannelEndpoints` aceita filtro opcional `gatewayConfigId`
- ✅ `modules/config-channels/ChannelAccountCard.tsx` — novo componente card pai: mostra credenciais + tabela de endpoints filhos; formulários inline para criar/editar/deletar endpoints dentro do card
- ✅ `modules/config-channels/index.tsx` — reestruturado: ChannelPanel usa ChannelAccountCard como hierarquia principal; webhook mantém ChannelEndpointList standalone; webchat mantém sub-tab "Runtime Settings"

---

## Task #36 — Config/Channels: consolidação GatewayConfig (2026-05-08)

**Problema resolvido:** GatewayConfig (credenciais de API) estava em Resources/Channels enquanto ChannelEndpoints (roteamento) estava em Config/Channels — duas telas separadas para configurar o mesmo canal.

**Solução:** Config/Channels virou o ponto único de configuração de canal. Resources ficou apenas com Pools + Skills.

**Frontend — platform-ui:**
- ✅ `modules/config-channels/channel-meta.ts` — `CHANNEL_META` extraído para arquivo compartilhado (8 canais: whatsapp, webchat, voice, email, sms, instagram, telegram, webrtc, webhook)
- ✅ `modules/config-channels/GatewayConfigPanel.tsx` — CRUD de GatewayConfig escopado por canal; sub-tabs expandíveis inline; suporte a create/edit/delete com masking de credenciais
- ✅ `modules/config-channels/index.tsx` — adicionada sub-tab "Credentials" para todos os canais que possuem campos de API; sub-tabs: Endpoints | Credentials | Settings (Settings apenas webchat/webhook)
- ✅ `modules/config-recursos/index.tsx` — aba "Channels" removida; Resources agora tem apenas Pools + Skills
- ✅ `modules/config-recursos/ChannelsPage.tsx` — marcado `@deprecated`; arquivo mantido para referência

**Estrutura final de Config/Channels por canal:**
- Endpoints: mapeamento identifier → pool (ChannelEndpointList, existente)
- Credentials: API keys/tokens/secrets (GatewayConfigPanel, novo)
- Settings: configuração de comportamento runtime via Config API (webchat: auth_timeout, attachments; webhook: path)

---

## Task #34 — Service/Pools: monitoração operacional de pools (2026-05-08)

**Backend — agent-registry:**
- ✅ `src/infra/redis.ts` — Redis client (ioredis) com opKeys: poolSnapshot, poolQueue, poolInstances
- ✅ `src/routes/operational.ts` — `GET /v1/operational/pools` + `GET /v1/operational/pools/:id/queue`
  - Fallback automático: snapshot Redis (Routing Engine) → live counts (:instances SCARD + :queue ZCARD)
  - Pool config (DB) + estado operacional (Redis) combinados por pool_id
  - Queue drill-down: posição, session_id, aguardando, SLA excedido, espera estimada restante
- ✅ `src/app.ts` — operationalRouter montado em `/v1/operational`
- ✅ `package.json` — ioredis adicionado
- ✅ `config.ts` — redis_url adicionado
- ✅ `docker-compose.demo.yml` — REDIS_URL + depends_on redis adicionados ao agent-registry

**Frontend — platform-ui:**
- ✅ `modules/contacts/PoolsPage.tsx` — nova página `/contacts/pools`:
  - Summary pills: agentes disponíveis, contatos em fila, pools com fila, total
  - Tabela de pools: status, disponíveis, fila, espera est., SLA, canais, snapshot age
  - Drill-down inline por pool: lista de sessões em fila com posição + wait + SLA excedido
  - Auto-refresh 15s, filtros por pool (select dinâmico) e status
  - `PoolStatusCard` exportado para reutilização em dashboard (Task #35)
- ✅ `modules/contacts/AgentsPage.tsx` — redesign: grid de cards → tabela compacta; tab "Lista" removida
- ✅ `shell/Sidebar.tsx` — item "Pools" adicionado em Service
- ✅ `app/routes.tsx` — rota `/contacts/pools` adicionada
- ✅ i18n pt-BR + en — chave `nav.service.pools` adicionada

---

## Task #33 — Deploy Pool-Centric: redesign completo (2026-05-08)

**Conceito corrigido:** deploy é uma operação de *pool*, não de skill. O usuário escolhe um pool e atribui um skill-flow a cada slot.

**Backend — agent-registry:**
- ✅ `prisma/migrations/20260508120000_add_pool_skill_slots/migration.sql` — tabela `pool_skill_slots` (pool_id + tenant_id + slot como chave; FK para pools com CASCADE; índice pool+tenant)
- ✅ `prisma/schema.prisma` — modelo `PoolSkillSlot` + relação `skill_slots` em `Pool`
- ✅ `src/routes/pool-slots.ts` — 4 endpoints montados em `/v1/pools/:pool_id`:
  - `GET /slots` — retorna todos os 3 slots do pool
  - `PUT /slots/next` — único slot editável; 403 para previous/current; auto-fetches yaml_snapshot
  - `POST /promote` — next→current, current→previous, next cleared (transaction)
  - `POST /rollback` — previous→current, previous cleared (transaction)
- ✅ `src/app.ts` — `poolSlotsRouter` montado em `/v1/pools/:pool_id` com `mergeParams: true`

**Frontend — platform-ui:**
- ✅ `AgentFlowDeployPage.tsx` — reescrita pool-centric:
  - Lista de pools no painel esquerdo com filtro
  - 3 cards por pool: Anterior (cinza) / Corrente (verde) / Próxima (azul)
  - Somente "Próxima" tem botão Editar; Corrente/Anterior mostram "🔒 somente leitura"
  - `NextSlotEditor`: skill dropdown + `ConfigForm` derivado de `skill.interface_schema`
  - "Copiar do Corrente": intersection merge — campos que existem em ambos são copiados; novos campos (só no novo schema) recebem badge "novo"; campos removidos são ignorados
  - Rollback usa `config_json` salvo sem revalidação (operação de emergência)
  - `ConfirmModal` para Promover e Rollback
  - Headers `x-tenant-id` em todos os fetches

---

## Full-Stack — 3-Slot Deploy Lifecycle (2026-05-08)

### Task #31 — Flow/Deploy: modelo de 3 slots (anterior / corrente / próxima)

**Backend — agent-registry:**
- ✅ `prisma/migrations/20260508000000_add_skill_version_slots/migration.sql` — tabela `skill_version_slots` + enum `SkillSlot` (previous/current/next)
- ✅ `prisma/schema.prisma` — modelo `SkillVersionSlot` + enum `SkillSlot` + relação `version_slots` em `Skill`
- ✅ `src/routes/skill-slots.ts` — 4 endpoints: `GET /slots`, `PUT /slots/:slot`, `POST /promote`, `POST /rollback`
- ✅ `src/app.ts` — `skillSlotsRouter` montado em `/v1/skills/:skill_id` com `mergeParams: true`

**Backend — workflow-api:**
- ✅ `config.py` — adicionado `agent_registry_url` (default `http://localhost:3300`)
- ✅ `router.py` — `_resolve_flow_definition()`: injeta `flow_definition` de agent-registry no metadata quando omitido em `POST /v1/workflow/trigger` para skill_*_v{n}
- ✅ `docker-compose.demo.yml`, `docker-compose.full.yml`, `docker-compose.arc4.yml` — `PLUGHUB_WORKFLOW_AGENT_REGISTRY_URL` adicionado

**Frontend — platform-ui:**
- ✅ `AgentFlowDeployPage.tsx` — reescrita completa; substituiu histórico-as-rollback por modelo de slots:
  - Painel de 3 colunas: Anterior / Corrente / Próxima
  - Desenvolvedor: botão "Editar" por slot (yaml_snapshot + config_json + pool_ids)
  - Operador: "Promover" (Próxima→Corrente, Corrente→Anterior) e "Rollback" (Anterior→Corrente) com confirmação
  - Após promote/rollback: chama `POST /deploy` para registrar em `skill_deployments`
  - Histórico de deploys colapsável
  - Deploys agendados colapsável (agendamento via workflow-api + cancelamento)
  - Monitor de handoff gradual colapsável (polling 15s)

---

## platform-ui — Contacts & Nav Restructure (2026-05-08)

### Task #30 — Grupos Atendimento + Análise + Flow expandido

**Novas páginas criadas:**
- ✅ `SessionsPage.tsx` (`/contacts/sessions`) — lista unificada de sessões (inbound + outbound), com filtros revisados: tipo (inbound/outbound), status (active/closed/abandoned), session_id, canal, pool, agent
- ✅ `AgentsPage.tsx` (`/contacts/agents`) — sub-abas Monitor (instâncias ao vivo, polling 15s) e Lista (placeholder Arc 8)
- ✅ `EventsPage.tsx` (`/contacts/events`) — stream plano de eventos com filtros: período, session_id (busca exata), canal, pool, tipo de evento
- ✅ `FlowMonitorPage.tsx` (`/flow/monitor`) — pool cards em tempo real (extrai scope "sessões" do MonitorTab)
- ✅ `ProcessosPage.tsx` (`/flow/processos`) — workflow instances com filtro de status, painel de detalhe, link para sessão de origem em `/contacts/sessions`
- ✅ `AnaliseContatosPage.tsx` (`/analise/contatos`) — seletor de período (Dia/Semana/Mês/Ano) + custom range; wraps AnaliseTab
- ✅ `AnaliseAgentesPage.tsx` (`/analise/agentes`) — filtros próprios; wraps AgentsTab (heatmap de disponibilidade + pausas)
- ✅ `AnaliseProcessosPage.tsx` (`/analise/processos`) — placeholder (backend analytics pendente)
- ✅ `AnaliseQualidadePage.tsx` (`/analise/qualidade`) — placeholder (backend evaluation summary pendente)

**Sidebar.tsx atualizado:**
- ✅ Grupo `Atendimento` (📞): Sessions · Agents · Events · Agent Assist
- ✅ Grupo `Fluxo` (🔄): Editor · Deploy · Monitor · Processos (2 novos sub-itens)
- ✅ Grupo `Análise` (📊) novo: Contatos · Agentes · Processos · Qualidade
- ✅ ABAC bypass para admin/supervisor/developer aplicado também nos itens de grupo

**routes.tsx atualizado:**
- ✅ Novas rotas: `/contacts/sessions`, `/contacts/agents`, `/contacts/events`, `/flow/monitor`, `/flow/processos`, `/analise/*`
- ✅ Redirects legados: `/contacts` → `/contacts/sessions`, `/monitor` → `/flow/monitor`, `agent-flow/monitor` → `/flow/monitor`, `config/agent-reports` → `/analise/agentes`, `reports` → `/analise/contatos`
- ✅ ContactsPage (`/contacts`) substituída — redirecionada para `/contacts/sessions`

**i18n:**
- ✅ `shell.json` (pt-BR + en): chaves `nav.service.*`, `nav.flow.monitor`, `nav.flow.processos`, `nav.analise.*` adicionadas

**Documentação:**
- ✅ `docs/modules/task-30-contacts-restructure.md` — especificação completa do redesign com decisões de design, filtros, colunas, ABAC e componentes

> Histórico de itens implementados removidos do `## Pending` do CLAUDE.md para reduzir noise no contexto.
> Itens pendentes: ver `TODO.md`.

---

## platform-ui — UI fixes + Skills/Pools/Config refactor (2026-05-07)

### Contacts (#23)
- ✅ Tab bar separada do título (border-b não mais aparecia sob "Contatos")
- ✅ Normalização de URL stale (`?tab=lista` → `?tab=report` via useEffect)
- ✅ Tab "Report" renomeada para "List" (`tabs.list`); i18n já tinha a chave
- ✅ Admin/supervisor/developer bypassam ABAC — veem todas as abas (List · Monitor · Analysis · Agents)

### Calendars (#24)
- ✅ Botão "New" adicionado nas seções Calendar e Holiday Sets
- ✅ Aba "Associations" removida (TabBar + AssociationsTab + código relacionado removidos)
- ✅ Tab state tipado com union `CalTab = 'calendars' | 'holiday-sets'`

### Flow/Editor (#25)
- ✅ Botão "New" removido (skills vêm do registry YAML, não são criadas pela UI)
- ✅ `NewSkillForm`, `AgentTypeConfig`, `FRAMEWORKS`, `ROLES`, `handleNewConfirm` removidos
- ✅ `isNew`, `pendingAgentType` removidos do estado

### Config/Platform — Masking + Namespaces (#26, #27)
- ✅ `MaskingPage.tsx`: namespace `masking` → `audit_policy`; chaves `capture_input`, `capture_output`, `token_retention_days`
- ✅ `NamespaceEditor`: namespaces `masking` e `audit_policy` removidos (têm UI própria)
- ✅ Namespaces `routing` + `session` unificados em grupo "Roteamento & Timeouts" com section headers
- ✅ Namespace `expurgo` adicionado (`voice_recording_days`, `attachment_days`)
- ✅ `NamespacePanel` sub-componente extraído; suporte a grupos (`namespaceIds[]`)
- ✅ `useMultiNamespace` hook adicionado em `config-hooks.ts`

### Resources/Skills (#28)
- ✅ `SkillsPage.tsx` reescrito: CRUD de competency skills no namespace `competency_skills` da Config API
- ✅ Cada entry: `key` (snake_case) + `value.domain` (0-9)
- ✅ Visual domain bar (10 pips) + slider inline para add/edit
- ✅ Admin token field para operações de escrita

### Resources/Pools (#29)
- ✅ `PoolsPage.tsx` reescrito: form de Modal para Drawer (slide-in direita, Escape para fechar)
- ✅ `routing_weights` substitui `routing_expression` na UI: Fixos (per-skill 0-9) + Dinâmicos (5 fatores 0-9)
- ✅ Competency skills carregadas de `/config/competency_skills` para o seletor de Fixos
- ✅ `routing_skills[]` derivado automaticamente dos Fixos com peso > 0 no save
- ✅ `types/index.ts`: `RoutingWeights`, `RoutingWeightsDinamicos`, `ROUTING_WEIGHTS_DEFAULTS` adicionados

### Bugfix
- ✅ `SkillFlowsPage.tsx` linha 408: referência residual a `isNew` removida

---

## platform-ui — Language cleanup + ChannelEndpoint (2026-05-07)

### Language rule — English identifiers in code

- ✅ **CLAUDE.md**: added "Language Rule — English in code, Portuguese only in display" to Naming Conventions section with examples.
- ✅ **routes.tsx**: `/config/canais` → `/config/channels`, `/config/recursos` → `/config/resources`, `/evaluation/avaliacoes` → `/evaluation/evaluations`. Legacy routes kept as `<Navigate>` redirects. Tab query params: `?tab=relatorio`→`?tab=report`, `?tab=analise`→`?tab=analysis`.
- ✅ **Sidebar.tsx**: all navKeys renamed to English (`service`, `flow`, `quality`, `config`); all hrefs updated to English paths; all i18n key calls updated (`t('nav.service')`, `t('nav.channels')`, `t('nav.resources')`, etc.); ABAC field references updated: `mascaramento`→`masking`, `relatorio`→`report`, `recursos`→`resources`.
- ✅ **shell.json (pt-BR + en)**: all i18n key names renamed to English while preserving translated values.
- ✅ **i18n/index.ts**: namespace `atendimento` → `service`; import variables renamed accordingly.
- ✅ **ContactsPage.tsx**: `ContactTab` type `'relatorio'|'analise'` → `'report'|'analysis'`; all tab references updated.
- ✅ **agent-assist types.ts**: `ultima_analise` → `last_analysis`.
- ✅ **useCopilotState.ts + CapacidadesTab.tsx**: updated to use `last_analysis`.
- ✅ **atendimento/MonitorPage.tsx**: `useTranslation('atendimento')` → `useTranslation('service')`.
- ✅ **config-channels/**: new module at English path; `index.tsx` + `WebChatConfigPage.tsx` created (logic same as config-canais version).

### Channel Endpoints — ChannelEndpoint entity

- ✅ **`@plughub/schemas`**: `ChannelEndpointChannelSchema`, `ChannelEndpointSchema`, `CreateChannelEndpointSchema`, `UpdateChannelEndpointSchema`, `ChannelEndpointQuerySchema` added to `packages/schemas/src/channel-endpoint.ts` and exported from `index.ts`.
- ✅ **`agent-registry` Prisma schema**: `ChannelEndpoint` model added with `@@unique([tenant_id, channel, identifier])`.
- ✅ **`agent-registry` routes**: `src/routes/channel-endpoints.ts` — full CRUD (GET list with `?channel=` filter, GET/:id, POST, PUT/:id with channel+identifier immutable, DELETE/:id). 409 on duplicate identifier. `publishRegistryChanged` on every write. Registered at `/v1/channel-endpoints` in `app.ts`.
- ✅ **`agent-registry` type shim**: `src/types/channel-endpoint.ts` — `ChannelEndpointRow` + `ChannelEndpointDelegate` for pre-generate Prisma workaround.
- ✅ **`platform-ui` types**: `ChannelEndpoint`, `ChannelEndpointChannel`, `CreateChannelEndpointInput`, `UpdateChannelEndpointInput` added to `types/index.ts`.
- ✅ **`platform-ui` api/registry.ts**: `listChannelEndpoints`, `createChannelEndpoint`, `updateChannelEndpoint`, `deleteChannelEndpoint` added.
- ✅ **`platform-ui` ChannelEndpointList.tsx**: new component — inline create/edit form, pool dropdown, delete with confirm; `IDENTIFIER_HINT` and `IDENTIFIER_PLACEHOLDER` maps per channel type.
- ✅ **`platform-ui` config-channels/index.tsx**: restructured with two sub-tabs per channel — "Endpoints" (`ChannelEndpointList`) + "General Settings" (`WebChatConfigPage` for webchat, coming soon for others).

**Manual steps required (WSL terminal):**
- `cd packages/agent-registry && npx prisma migrate dev --name add_channel_endpoint`
- `git mv packages/platform-ui/src/modules/config-canais packages/platform-ui/src/modules/config-channels` (then delete if config-channels already works)
- `git mv packages/platform-ui/src/modules/atendimento packages/platform-ui/src/modules/service` + update all imports referencing `@/modules/atendimento/`

---

## platform-ui — Config/Recursos + Config/Plataforma + Config/Canais (2026-05-06)

### Configuração / Recursos

- ✅ **Pool form — routing_expression weights**: 5 pesos dinâmicos de prioridade (`weight_sla=1.0`, `weight_wait=0.8`, `weight_tier=0.6`, `weight_churn=0.9`, `weight_business=0.4`) adicionados ao form de pool. Grid 2×3, inputs numéricos com step=0.1, botão "Restaurar padrões". Payload só envia `routing_expression` quando difere dos defaults. Coluna "Prioridade" na tabela mostra SLA+Churn com tooltip completo.
- ✅ **Pool form — calendar_id + routing_skills**: dropdown de template de calendário (calendar-api) e checkboxes de competency skills (Config API namespace `routing`) integrados ao form.
- ✅ **Types**: `RoutingExpression`, `ROUTING_EXPRESSION_DEFAULTS` adicionados a `types/index.ts`; `Pool`, `CreatePoolInput`, `UpdatePoolInput` atualizados.
- ✅ **Remover tab AgentTypes**: tab AgentTypes removida de `config-recursos/index.tsx` (Pools · Skills · Channels apenas). AgentType é criado pelo editor de SkillFlow ao criar novo skill.
- ✅ **SkillFlowsPage — inline AgentType**: `NewSkillForm` substitui `NewSkillPrompt`; ao criar skill, formulário completo de AgentType (framework, execution_model, role, max_concurrent, pools) é preenchido inline; `agent_type_id = skill_id`; POST /v1/agent-types disparado após skill salvo.

### Configuração / Plataforma

- ✅ **NamespaceEditor reestruturado**: 8 namespaces → 5 ativos. Removidos: `sentiment` (UI dedicada), `dashboard` (migrado ao módulo Dashboards), `webchat` (migrado a Configuração/Canais). Renomeados labels: `consumer` → "Consumer Analytics", `masking` mantido como legado com label "Mascaramento (legado)" + novo `audit_policy`. Entrada `routing` com descrição atualizada (sem score_weights — agora por pool).
- ✅ **SentimentBandsEditor**: novo componente dedicado para edição de faixas de sentimento com níveis numéricos (1=pior, N=melhor). Validação de contiguidade e cobertura [-1.0, 1.0]. Barra visual de cor vermelho→verde. Suporte a 2–6 faixas configuráveis. Salva `sentiment.bands` no Config API sem texto (i18n resolve labels). Nova tab "💬 Sentimento" no ConfigPlataformaPage.
- ✅ **RoutingSkillsManager**: CRUD de competency skills (key + domain range) no namespace `routing`. Tab "🎯 Roteamento" no ConfigPlataformaPage.
- ✅ **ConfigPlataformaPage tabs**: ⚙️ Configuração | 💬 Sentimento | 📅 Calendários | 🎯 Roteamento.

### Configuração / Canais (novo módulo)

- ✅ **config-canais/index.tsx**: seção `/config/canais` com tabs por canal. WebChat ativo; WhatsApp/Voice/Email/SMS marcados "em breve".
- ✅ **WebChatConfigPage.tsx**: configuração do canal WebChat em 3 grupos — autenticação WS (`auth_timeout_s`), política de attachments (`attachment_expiry_days`, `upload_limits_mb` por MIME type), info sobre JWT secret. Lê/escreve namespace `webchat` no Config API. Admin token inline.
- ✅ **Rota + Sidebar**: `/config/canais` registrado em `routes.tsx`; item "📡 Canais" na sidebar entre Plataforma e Calendários. Chave i18n `nav.canais` em pt-BR e en.

---

## platform-ui — Sidebar / Navigation refactor (2026-05-06)

- ✅ **Fix eco no histórico de segmento (Atendimento/Contatos)**: `mcp-server-plughub/src/server.ts` — substituído `redis.xadd()` direto por `writeStreamEntry()` no handler `menu_submit` (respeita invariante arquitetural). O `xadd` direto não populava os campos flat `author_id`/`author_role` que `_parse_entry()` do analytics-api exige, causando eco/duplicação na exibição das conversas do segmento no histórico do contato. Adicionado `import { writeStreamEntry }` do `lib/write-stream-entry`. Verificado e funcionando na docker-demo.
- ✅ **Scripts linux**: permissões de execução corrigidas em `check-infra.sh`, `seed-demo.sh`, `set-env.sh`, `setup.sh`.

---

## Context-Aware / ContextStore

- ✅ **Fase 2 — Co-pilot**: `copilot_emitter.py` (AI Gateway) — `analyze_for_copilot()` fire-and-forget; endpoint `POST /v1/copilot/analyze`; `GET /copilot_state/:sessionId` no mcp-server-plughub; `useCopilotState` hook; `CapacidadesTab.tsx` com `CopilotSection`; 28/28 testes.
- ✅ **Fase 3 — Step `resolve` nativo**: Executor completo de 5 fases (gap check → CRM → LLM question → BLPOP → LLM extract); schemas em `@plughub/schemas`; 15 unit tests.

---

## Arc 5 — ContactSegment (v2)

- ✅ **Enrichment post-hoc de `segment_id`**: `SegmentEnricher` em `analytics-api/segment_enricher.py` — lookup chain LRU → Redis → ClickHouse FINAL; `_ENRICHED_TOPICS = {"sentiment.updated", "mcp.audit"}`; 27/27 testes em `test_segment_enricher.py`. Total analytics-api: 226/226.
- ✅ **Materialized views ClickHouse**: `mv_agent_performance_daily` (AggregatingMergeTree, POPULATE) + `v_agent_performance` — `GET /reports/agent-performance/daily`; `mv_segment_summary` + `v_segment_summary` — `GET /reports/sessions/complexity`. Pool scoping em ambos.

---

## Skill Deploy Lifecycle (Phase 2)

- ✅ **Deploy agendado**: `skill_scheduled_deploy_v1.yaml` com `reason: timer` suspend; MCP tool `skill_deploy` em `mcp-server-plughub`; `scheduled_at` em `PersistSuspendRequest`; `GET /v1/skills/:id/deployments/scheduled`; UI `AgentFlowDeployPage.tsx` com agendamento e cancelamento.
- ✅ **Graceful handoff monitor**: `GET /v1/skills/:id/handoff-status` — detecta deploy recente, consulta analytics-api por sessões ativas antes de `deployed_at`. UI com polling a cada 10s.
- ✅ **Automated rollback button**: botão "↩ Rollback" no histórico; two-step (PUT yaml_snapshot + POST re-deploy); `RollbackConfirmModal`; badge "rollback" em entradas originadas por rollback.

---

## Config API / Routing Engine

- ✅ **Consumo de `config.changed` namespace `routing`**: `RoutingConfigCache` em `routing_config.py` — fetch no startup, invalida/recarrega via `ConfigChangedHandler`. `performance_score_weight` dinâmico com fallback para env var. 15/15 testes em `test_routing_config.py`.

---

## Arc 8 — Frontend

- ✅ **platform-ui — AgentReportsPage**: `/config/agent-reports` (supervisor, admin) — duas sub-abas: "Disponibilidade" (pivot agent × data, células âmbar por intensidade) e "Pausas" (tabela flat de reason_breakdown + exportação CSV). `useAvailability` hook.
- ✅ **platform-ui — PauseReasonModal**: modal intercepta "Pausar"; busca motivos do Config API com fallback para DEFAULT_REASONS; `requires_note: true` exibe textarea obrigatória; chama best-effort `PUT /api/agent-pause`.

---

## Arc 2 — Fechamento

- ✅ E2E scenario 12: webchat auth flow + media upload end-to-end
- ✅ Usage Metering no Channel Gateway (voice_minutes, whatsapp_conversations, sms_segments)
- ✅ WebChat reconexão fase 2: stream TTL expirado + jwt_secret por tenant
- ✅ AttachmentStore fase 2: S3/MinIO
- ✅ Magic bytes validation no upload
- ✅ Pricing Module v1: planos, tarifas, ciclo de billing

---

## Arc 3 — Analytics, Dashboard Operacional e Relatórios

Todos os 12 itens implementados:

1. ✅ AI Gateway — `sentiment_emitter.py`: `emit_sentiment_updated` (Kafka) + `update_sentiment_live` (Redis). 41 assertions.
2. ✅ analytics-api — consumer + ClickHouse (6 tabelas ReplacingMergeTree, 8 tópicos, commit manual). 30 assertions.
3. ✅ analytics-api — dashboard endpoints: `GET /dashboard/operational` (SSE), `/metrics`, `/sentiment`. 18 assertions.
4. ✅ analytics-api — reports + BI export: sessions/agents/quality/usage com paginação e CSV. 26 assertions.
5. ✅ analytics-api — camada admin consolidada: `/admin/consolidated`, RBAC JWT, cross-tenant. 21 assertions.
6. ✅ operator-console fase 1 — heatmap + métricas realtime: SSE, PoolTile, MetricsPanel. 157 kB JS gzip 50 kB.
7. ✅ operator-console fase 2 — drill-down: sessions ativas → transcrição ao vivo (SSE + ClickHouse fallback). 61 assertions.
8. ✅ operator-console fase 3 — intervenção ativa: supervisor join/message/leave via REST. 173 kB JS gzip 54 kB.
9. ✅ Metabase setup: `docker-compose.infra.yml`, driver ClickHouse, Row Policies por tenant, 5 questions + dashboard.
10. ✅ Config Management Module: `packages/config-api/`, PostgreSQL, Redis cache 60s, 9 namespaces seedados. 27 assertions.
11. ✅ Timeseries endpoints: `/reports/timeseries/volume`, `/handle_time`, `/score`. Bucketing dinâmico. Pool scoping.
12. ✅ Dashboard drag-and-drop + templates: `DashboardsPage.tsx`, `TimeseriesChart`, react-grid-layout, Config API namespace `dashboards`.

---

## Arc 5 — ContactSegment v1

- ✅ Schemas em `@plughub/schemas/src/contact-segment.ts`: `ContactSegmentSchema`, `ConversationParticipantEventSchema`
- ✅ orchestrator-bridge: `_publish_participant_event` com `segment_id`, `sequence_index`, `parent_segment_id`, `outcome`
- ✅ analytics-api: tabelas `segments` + `session_timeline` (ClickHouse); endpoints `/reports/segments`, `/reports/agents/performance`, `/reports/agent-performance/daily`, `/reports/sessions/complexity`
- ✅ E2E scenario 23: Parts A/B/C — 11 assertions (`--segments` flag)

---

## AI Gateway — Multi-account Rotation

- ✅ `AccountSelector` em `account_selector.py`: Redis-backed, stateless, scoring por (rpm_used/rpm_limit × 0.7) + (tpm_used/tpm_limit × 0.3)
- ✅ Configuração multi-chave: `PLUGHUB_ANTHROPIC_KEYS` (vírgula) + `PLUGHUB_OPENAI_KEYS` como fallback
- ✅ `_call_with_fallback`: rotação automática em 429/529 → `mark_throttled` → próxima conta → cross-provider fallback
- ✅ Isolamento de workloads: profiles `realtime` (Sonnet → gpt-4o), `balanced` (Haiku → gpt-4o-mini), `evaluation` (Haiku isolado)
- ✅ `OpenAIProvider` com graceful degradation se SDK ausente; tool format conversion; role mapping
- ✅ Config API namespace `ai_gateway`: `account_rotation_enabled`, `throttle_retry_after_s`, `utilization_rpm_weight`, `evaluation_model`, `evaluation_max_tokens`
- ✅ 29 assertions em `test_account_selector.py`
- ✅ E2E scenario 26: throttle → fallback → recovery (`--fallback` flag)

---

## Arc 6 v2 — Permissões 2D + Workflow Motor

- ✅ `evaluation_permissions` substituído por ABAC (`module_config.evaluation.revisar` / `contestar`)
- ✅ `_check_abac_permission` em `router.py`; graceful degradation para tokens legacy
- ✅ Campos de workflow em `evaluation_results`: `workflow_instance_id`, `resume_token`, `action_required`, `current_round`, `deadline_at`, `lock_reason`
- ✅ Anti-replay de `round` em review/contestation endpoints
- ✅ Consumer `workflow.events` no evaluation-api
- ✅ `skill_revisao_simples_v1.yaml` e `skill_revisao_treplica_v1.yaml`
- ✅ MCP tool `evaluation_lock` (idempotente)
- ✅ E2E scenarios 27/28 (11 + 11 assertions)

---

## Calendar API — Fase 4: Múltiplos Intervalos de Tempo por Dia

- ✅ **Engine**: já suportava múltiplos slots desde a implementação inicial (estrutura `{day, open, slots: [{open, close}]}`); nenhuma mudança necessária no backend
- ✅ **5 novos testes de engine** (`test_engine.py` — total: 46/46):
  - `test_between_slots_returns_start_of_next_slot` — 12:30 durante pausa → retorna 13:00 mesmo dia
  - `test_within_first_slot_returns_current_time` — 10:00 dentro do primeiro slot → já aberto
  - `test_after_all_slots_returns_next_day_open` — 17:30 após último slot → 08:00 dia seguinte
  - `test_is_open_false_during_gap` — gap entre slots retorna `False`; início de slot retorna `True`
  - `test_crosses_gap_between_slots` — `add_business_duration` 11:00 + 3h cruza pausa (12–13) → 15:00
- ✅ **CalendarsPage.tsx** — corrigido bug de formato de dados (UI enviava `{day_of_week, start_time, end_time}` mas o engine lê `{day, open, slots: [{open, close}]}`); tipos `TimeInterval` + `WeeklyDaySchedule` agora corretos
- ✅ **WeeklyEditor** — reescrito para suportar N intervalos por dia: cada slot é uma linha; botão `× ` remove um intervalo; botão `+ intervalo` adiciona (máx 4); fallback para `08:00–18:00` ao remover último slot
- ✅ **`scheduleLabel`** — exibe `Segunda(2)` quando o dia tem múltiplos slots
