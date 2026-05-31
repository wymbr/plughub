# TODO — PlugHub Itens Pendentes

> Itens genuinamente não implementados. Histórico de implementações concluídas em `CHANGELOG.md`.

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

