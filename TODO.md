# TODO — PlugHub Itens Pendentes

> Itens genuinamente não implementados. Histórico de implementações concluídas em `CHANGELOG.md`.

---

## skill_scheduled_deploy_v1 — Registro no Registry

Skill `skill_scheduled_deploy_v1` precisa existir como YAML no `infra/registry/skills/` para que o agendamento de deploys funcione em produção. Criada referência no código (Arc 4 / Arc 31), mas o arquivo YAML de definição não foi criado.

---

## Channel Endpoints — Layer 2 channel-gateway *(deferred)*

Layers 1 (agent-registry), 3 (platform-ui) e 4 (schemas) completos — ver CHANGELOG 2026-05-07.

**Layer 2 — channel-gateway**: substituir lookup hardcoded por chamada HTTP a `GET /v1/channel-endpoints?channel={ch}&identifier={id}` no agent-registry. Cache com TTL curto (~30s) para evitar latência no hot-path. Também rodar `prisma migrate dev --name add_channel_endpoint` no agent-registry quando rede disponível.

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


## Arc 13 — Evaluation Review & Contestation UX *(em especificação)*

Spec completo em `docs/arcos/arc13-review-contestation.md`.

Decisões pendentes antes de implementar:
- Dimensões explícitas no formulário (`dimension_id` em `EvaluationCriterion`) ou inferidas automaticamente dos critérios?
- Tréplica: agente avaliado pode responder à revisão do revisor? (define `max_rounds` em `ContestationPolicy`)
- `agente_revisor_v1` acessa conversa original via `ReplayContext` ou apenas `ContestationThread[]`?

Fases planejadas: A (data model + endpoints) → B (evaluator agent spec + evidence) → C (reviewer agent spec) → D (human review UX) → E (campaign SLA config UI).

---

## Arc 10 — Fase F: Split de Jornadas *(fase futura — decisões em aberto)*

MCP tool `journey_split(journey_id, session_ids[])` — extrai sessões para nova journey.

Decisões pendentes antes de implementar:
- Destino do `workflow_instance_id` após o split
- Nova journey recebe workflow existente ou inicia sem vínculo
- Restrição sobre `origin_session_id` da journey original após extração
