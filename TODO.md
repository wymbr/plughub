# TODO — PlugHub Itens Pendentes

> Itens genuinamente não implementados. Histórico de implementações concluídas em `CHANGELOG.md`.

---

## Arc 19 — Modelo Unificado de Sessão: Workflow como Canal Webhook

Spec em [`docs/arcos/arc19-unified-session-model.md`](docs/arcos/arc19-unified-session-model.md). Elimina a dualidade contact/workflow tratando workflows como canal `webhook` na channel-gateway.

- **Fase A** — WebhookAdapter (`channel-gateway/adapters/webhook.py`): endpoints trigger/resume/status + `channel_type: webhook` no schema + routing engine reconhece pools webhook
- **Fase B** — Status `suspended` no domain de sessão + TTL extension no executor suspend() + hash Redis `resume_tokens`
- **Fase C** — orchestrator-bridge: skill-flow como agente nativo de pool webhook; eliminar `skill-flow-worker`
- **Fase D** — workflow-api deprecation: redirect `/v1/workflow/trigger` → webhook adapter; manter `/v1/workflow/instances` read-only
- **Fase E** — Monitor e Analytics unificados: filtro `channel_type` substitui páginas separadas; badge para sessões `suspended`
- **Fase F** — Eliminação Journey (Arc 10/16/17 → CHANGELOG) + remoção `workflow.events` topic + cleanup `skill-flow-worker` package

**Pré-requisito**: não implementar até a eliminação do Journey (Fase F) estar pronta para execução.

---

## Arc 18 — Workflow Execution Trace *(DEPRECATED pelo Arc 19)*

A spec original em [`docs/arcos/arc18-workflow-execution-trace.md`](docs/arcos/arc18-workflow-execution-trace.md) está superseded pelo Arc 19.

**Por que deprecated**: todas as superfícies de Arc 18 dependem de entidades eliminadas pelo Arc 19 — `workflow-api` (deprecado Fase D), `Analytics/Processes` (eliminado, merge em Analytics/Sessions), `Analytics/Journeys` (eliminado com Journey na Fase F), rotas `/analytics/processes/:instanceId` e `/analytics/journeys/:journeyId` (desaparecem).

**O que sobrevive do conceito**: step-level trace ainda tem valor. No modelo Arc 19 passa a ser uma aba "Trace" no detalhe de session em `Analytics/Sessions` para sessions com `channel_type: webhook`. Fonte de dados: Redis `pipeline_state.transitions[]` (sessões ativas/suspensas) com fallback para stream persistido (sessões fechadas). Implementar como parte da **Arc 19 Fase E**.

**Fase E deferred** (ClickHouse step analytics — `workflow_step_events` table + heatmap) ainda é válida conceitualmente, mas a implementação muda: eventos de step de sessions webhook, não de workflow_instances.

---

## Arc 17 — JourneyType Governance *(COMPLETO)*

Todas as tarefas #298–#301 implementadas. Ver CHANGELOG e [`docs/arcos/arc17-journey-types.md`](docs/arcos/arc17-journey-types.md).

---

## Arc 16 — Three-Tier Business Process Orchestration *(COMPLETO)*

Todas as fases A–E implementadas. Ver CHANGELOG e [`docs/arcos/arc16-flow-orchestration.md`](docs/arcos/arc16-flow-orchestration.md).

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

