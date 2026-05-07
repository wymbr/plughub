# TODO — PlugHub Itens Pendentes

> Itens genuinamente não implementados. Histórico de implementações concluídas em `CHANGELOG.md`.

---

## platform-ui — Próximas features planejadas

### #30 — Contacts: 2 níveis de aba (Contacts / Agents) com filtros revisados

Reestruturar ContactsPage em nível superior Contacts | Agents, cada um com sub-abas
List · Monitor · Analysis e filtros independentes revisados.
- Filtro atual de Contacts tem campos inadequados por contexto — precisa ser redefinido
- Aba Agents: List = tabela de agentes com métricas consolidadas (TMA, volume, taxa resolução)
  + drill-down para lista de contatos/segmentos onde o agente atuou
- Decisões pendentes: conjunto exato de filtros por nível, URL scheme (?section=contacts&tab=list)
- Ver Task #30 para especificação completa

### #31 — Flow/Deploy: ciclo de vida de versões com agendamento e rollback

Tela de deploy de skills em pools com modelo de 3 slots (anterior / corrente / próxima).
- Cada slot: skill_id + JSON de configuração
- Deploy: anterior←corrente←próxima, próxima←livre
- Rollback: corrente←anterior (o que era corrente: decisão pendente)
- Agendamento via workflow-api (skill_scheduled_deploy_v1); Calendar API para validação de horário
- Ver Task #31 para especificação completa

---

## platform-ui — Backend pendente para features de config (2026-05-06)

### Sentimento — _classify() dinâmico

`sentiment_emitter.py._classify()` usa if/elif hardcoded. Para que `SentimentBandsEditor` funcione em runtime, o AI Gateway precisa ler `sentiment.bands` do Config API e classificar usando as faixas configuradas. Refatoração: passar `bands: list[dict]` como parâmetro ou ler do Config API na inicialização com hot-reload via `config.changed` Kafka.

### Sessão — orchestrator-bridge ler TTLs do Config API

`orchestrator-bridge/main.py` tem ~20 literais `14400` hardcoded (TTL de sessão Redis). O namespace `session` no Config API (`channel_gateway_ttl_s`) existe mas não é consumido pelo bridge. Migrar para leitura dinâmica similar ao `RoutingConfigCache`. Idem para `session_replayer` (`HYDRATION_TTL_SECONDS`, `REPLAY_CONTEXT_TTL`) e `conversation-writer` (`transcript_ttl_seconds`).

### Config API seed — novos namespaces/chaves

- Renomear `masking` → `audit_policy` no seed (manter `masking` como alias de compatibilidade).
- Adicionar chaves ao namespace `session`: `orchestrator_session_ttl_s` (14400), `transcript_ttl_s` (14400), `replayer_hydration_ttl_s` (3600), `replay_context_ttl_s` (3600), `pool_config_ttl_s` (3600), `sentiment_live_ttl_s` (300).
- Adicionar `analytics_consumer` como namespace (renomear de `consumer`).

### Pool Registry — routing_expression field

Verificar se `agent-registry` (Prisma schema + API endpoints) aceita e persiste o campo `routing_expression` nos modelos `Pool`/`CreatePool`/`UpdatePool`. O frontend já envia o campo; o backend precisa estar preparado.

### Quotas — refatoração

`quota.llm_tokens_daily` e `quota.messages_daily` devem migrar para o namespace `ai_gateway` como limites por conta API (não por tenant). O namespace `quota` simplifica para apenas `max_concurrent_sessions`.

---

## Usage Metering — Channel Gateway adapters

- **whatsapp_conversations, voice_minutes, sms_segments, email_messages** *(deferred)*: funções em `usage_emitter.py` implementadas, mas os adapters de canal ainda não as chamam. Será wired quando cada adapter for criado (WhatsApp, WebRTC/Voice, SMS, Email).

---

## Pricing Module

- **Integração metering × pricing** *(deferred)*: módulo que lê contadores de `usage.events` no Redis/ClickHouse, aplica planos configurados no Config API e escreve `{tenant}:quota:limit:*` no Redis. Metering registra mas pricing não consome.

---

## Arc 8 — Relatório de Disponibilidade e Pausas de Agentes

**Frontend já implementado:** `AgentReportsPage.tsx` e `PauseReasonModal.tsx` ✅ — aguardando backend.

**Backend pendente:**

1. **`agent_pause` schema** — adicionar `reason_id: string` e `reason_label: string` ao `AgentPauseEventSchema` em `packages/schemas/src/platform-events.ts`.

2. **Config API — motivos de pausa** — seed de namespace `agent_activity` com chave `pause_reasons` (lista de `{ id, label, requires_note: bool }`). Motivos padrão: intervalo, almoço, treinamento, reunião, outro. Override por pool via chave `pause_reasons:{pool_id}`.

3. **orchestrator-bridge — publicar `agent_pause`/`agent_ready` com motivo** — `PUT /api/agent-pause/:instanceId { reason_id, reason_label, note? }` publica no tópico `agent.lifecycle`. `PUT /api/agent-resume/:instanceId` publica `agent_ready`.

4. **analytics-api — consumir `agent_pause` e `agent_ready`** — `parse_agent_lifecycle` estendido para processar pausas → tabela `agent_pause_intervals` (ClickHouse, `ReplacingMergeTree`). Schema: `interval_id, tenant_id, instance_id, agent_type_id, pool_id, reason_id, reason_label, note, paused_at, resumed_at, duration_ms`.

5. **analytics-api — `GET /reports/agent-availability`** — agrega `agent_pause_intervals FINAL` por `(agent_type_id, pool_id, period_date)`. Campos: `total_pause_duration_ms`, `pause_count`, breakdown por `reason_id`. Pool scoping via `optional_pool_principal`.

---

## mcp-server-plughub — writeStreamEntry centralizado (Task #173)

Refatoração estrutural para eliminar os múltiplos caminhos de XADD direto no stream Redis. Hoje existem quatro pontos de escrita com convenções inconsistentes: `message_send` (session.ts), `notification_send` (bpm.ts), `agent_done` (session.ts), `mention_dispatcher` (bpm.ts). Cada um grava campos com formatos diferentes (`author` como JSON object vs. campos flat, `segment_id` ausente ou inconsistente), causando bugs de visibilidade e fallbacks no `_parse_entry` do analytics-api.

**Solução**: função única `writeStreamEntry()` com validação Zod obrigatória em compile-time e runtime antes de cada XADD. Campos obrigatórios: `type`, `author_id`, `author_role`, `visibility`, `content`, `timestamp`. `segment_id` opcional mas sempre presente como campo consistente. Falha ruidosa se campo ausente — nenhum entry incompleto entra no stream.

---

## Language Cleanup — Portuguese identifiers in code (2026-05-07)

Rule added to CLAUDE.md. Fase 1 ✅ complete — see CHANGELOG 2026-05-07.

### Fase 2 — ABAC field names (requires DB migration + modules.yaml update)

| Current | Replace with | Files |
|---|---|---|
| `field: 'mascaramento'` | `field: 'masking'` | modules.yaml + all JWT validation code |
| `field: 'relatorio'` | `field: 'report'` | modules.yaml + all JWT validation code |
| `field: 'recursos'` | `field: 'resources'` | modules.yaml + all JWT validation code |

Migration: `UPDATE auth.module_registry SET ... WHERE ...` + auth-api reseed on startup handles it if modules.yaml is the source of truth.

### Not in scope (intentional)

`agente_*`, `skill_*`, `pool_id` values like `retencao_humano`, `postura_atendimento` — these are business-domain entity IDs configured by the tenant; they are data, not code identifiers.

---

## Channel Endpoints — channel-gateway integration (pending)

Layers 1 (agent-registry), 3 (platform-ui), 4 (schemas) ✅ complete — see CHANGELOG 2026-05-07.

**Layer 2 — channel-gateway** *(deferred)*: Replace hardcoded pool lookup with HTTP call to agent-registry `GET /v1/channel-endpoints?channel={ch}&identifier={id}`. Cache with short TTL (~30s) to avoid hot-path latency. Also run `prisma migrate dev --name add_channel_endpoint` in agent-registry when network is available.

---

## Skill Flow — Deploy por Slots de Versão (anterior / corrente / próxima)

O modelo atual de deploy (`skill_deployments`) é um histórico de deploys arbitrário onde o operador escolhe manualmente para qual snapshot fazer rollback. Isso exige conhecimento técnico do operador e abre espaço para erros de configuração.

**Modelo desejado:** cada skill tem 3 slots nomeados pelo desenvolvedor:

| Slot | Papel |
|---|---|
| `anterior` | Versão de fallback seguro — destino do rollback automático |
| `corrente` | Versão atualmente em produção |
| `próxima` | Candidata ao próximo deploy |

Cada slot carrega: YAML do flow + configuração JSON específica dessa versão.

**Fluxo de deploy:** promove `próxima` → `corrente`; `corrente` desloca automaticamente para `anterior`.
**Fluxo de rollback:** promove `anterior` → `corrente`; nenhuma escolha manual pelo operador.

O operador executa a intenção que o desenvolvedor pré-definiu — não toma decisão técnica sobre qual versão ou configuração usar.

**Impacto no backend:** novo model `skill_version_slots` em agent-registry com campos `slot` (anterior/corrente/proxima), `skill_id`, `yaml_snapshot`, `config_json`, `pool_ids`, `set_at`, `set_by`. Endpoints `PUT /v1/skills/:id/slots/:slot` (desenvolvedor) e `POST /v1/skills/:id/promote` (trigger de deploy: próxima→corrente) e `POST /v1/skills/:id/rollback` (anterior→corrente).

**Impacto no frontend:** `AgentFlowDeployPage` ganha painel de 3 colunas (anterior/corrente/próxima) onde o desenvolvedor edita cada slot. Operador vê apenas os botões "Promover" e "Rollback" com confirmação.

---

## Skill Flow Editor — Folder Organization (new feature)

O `SkillFlowsPage` exibe lista plana. Não há pastas/grupos. Melhoria desejada: agrupamento visual por `classification.type` (orchestrator/vertical/horizontal) ou por pasta livre configurável no `skill_id` (ex: `skill_sac_*/skill_retencao_*` agrupados). Separação visual entre "skills de workflow" e "agents operacionais" simplificaria a navegação em registries com muitos skills.

**Escopo sugerido:** filtro/toggle por `classification.type` na sidebar do editor (sem filesystem de pastas — apenas agrupamento visual).

---

## Skill Flow — Scheduled Deploy gap (2026-05-07)

`scheduleSkillDeploy()` em `AgentFlowDeployPage.tsx` envia `flow_id: 'skill_scheduled_deploy_v1'` mas **não inclui `flow_definition` em `metadata`**. O worker (`engine-runner.ts`) falha com `"Missing flow_definition in metadata"`.

**Solução A (recomendada):** o endpoint `POST /v1/workflow/trigger` no workflow-api busca a skill pelo `flow_id` no agent-registry e injeta `flow` (entry + steps) em `metadata.flow_definition` antes de salvar a instância. Assim o caller não precisa resolver o YAML.

**Solução B:** `scheduleSkillDeploy` busca `GET /v1/skills/skill_scheduled_deploy_v1` antes de chamar trigger e inclui `metadata: { flow_definition: skill.flow }`.

Solução A é preferível — elimina o acoplamento ao caller e garante que qualquer trigger (webhook, evaluation-api, etc.) funcione corretamente.

---

## CLAUDE.md — Otimização (Fases 2 e 3)

**Fase 2** *(blocked by Fase 1)*: Mover Arc 6, Arc 4, Arc 7, ABAC e ContextStore para arquivos em `docs/modules/`. Manter no CLAUDE.md apenas resumo de 15–20 linhas por módulo com link para o arquivo completo.

**Fase 3** *(blocked by Fase 2)*: Mover seções menores (WebChat Channel, Instance Bootstrap, Pool Lifecycle Hooks, Session Replayer, Usage Metering, Pricing Module) para `docs/modules/`. Revisão final para target ≤ 800 linhas no CLAUDE.md.
