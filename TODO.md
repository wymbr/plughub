# TODO — PlugHub Itens Pendentes

> Itens genuinamente não implementados. Histórico de implementações concluídas em `CHANGELOG.md`.

---

## Arc 18 — Workflow Execution Trace

Spec em [`docs/arcos/arc18-workflow-execution-trace.md`](docs/arcos/arc18-workflow-execution-trace.md). Três fases sequenciais:

Navegação hierárquica em páginas dedicadas (não painéis laterais). Spec em [`docs/arcos/arc18-workflow-execution-trace.md`](docs/arcos/arc18-workflow-execution-trace.md).

- **Fase A** — `GET /v1/workflow/instances/{id}/trace` no workflow-api: lê `transitions[]` do JSONB (Redis first para ativas, fallback DB), enriquece com `step_type`/`step_label` do `flow_definition`, junta `collect_instances` (channel, status, session_id). Deriva `trigger_type` da origem — sem migration.
- **Fase B.1** — `AnalyticsProcessesPage` (lista de instâncias): adicionar colunas `started_at`, `ended_at`, `duration`; clicar na linha navega para `/analytics/processes/:instanceId`.
- **Fase B.2** — `AnalyticsJourneysPage` (lista de jornadas): idem com `completed_at` e duração; clicar navega para `/analytics/journeys/:journeyId`.
- **Fase C** — `ProcessDetailPage` (`/analytics/processes/:instanceId`): 4 seções — Origin (trigger_type + links), Parameters (inputs ↔ outputs), `ProcessStepTimeline`, hook `useInstanceTrace`. Reutilizado de ambas as rotas.
- **Fase D** — `JourneyDetailPage` (`/analytics/journeys/:journeyId`): timeline de eventos da Journey + lista de processos enriquecida com link para `ProcessDetailPage`.
- **Fase E** *(deferred)* — Analytics por step: `workflow_step_events` ClickHouse + consumer Kafka + heatmap na SummaryTab.

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

