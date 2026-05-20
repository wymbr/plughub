# TODO — PlugHub Itens Pendentes

> Itens genuinamente não implementados. Histórico de implementações concluídas em `CHANGELOG.md`.

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

## Analytics/Agents — Expansão do Relatório de Agentes *(a definir)*

A página `/analise/agents` exibe hoje apenas dados de **pausa** de agentes humanos (Arc 8 — `agent_pause_intervals`). Precisa-se definir quais informações adicionais incluir, aproveitando dados já disponíveis no ClickHouse:

**Candidatos para agentes humanos** (fonte: `segments`, `agent_events`, `agent_pause_intervals`):
- Sessões atendidas no período, AHT médio, taxa de resolução, taxa de escalação
- Disponibilidade online vs pausa (% do turno em pausa, por motivo)
- Distribuição por pool e canal
- Endpoint já existente: `GET /reports/agents/performance` (Arc 5, `mv_agent_performance_daily`)

**Candidatos para agentes IA** (fonte: `segments`, `agent_business_events`, `evaluation_results`):
- Volume de sessões, AHT médio, outcomes por skill version
- KPIs de negócio emitidos via `agent_event` tool (Arc 12)
- Score médio de avaliação por deploy epoch (Arc 6 Fase 2)

**Decisão pendente**: quais métricas priorizar, separação em abas (Humanos / IA / Comparativo), filtros necessários (pool, agent_type_id, período, deploy epoch).

---

## Audit LGPD — Fases Pendentes

Fase 1 concluída — ver CHANGELOG 2026-05-14 e `docs/arcos/audit-lgpd.md`.

- **Fase 2** — `original_content` desmascarado: endpoint de resolução de tokens em Core → analytics-api expõe conteúdo original ao DPO. Requer endpoint batch de resolução de tokens no Core.
- **Fase 3** — `user_access` logs: topic Kafka `user_access.events` em auth-api + tabela ClickHouse + tab ativo em AuditPage.
- **Fase 4** — SAR/Erasure pipeline: CRUD de Subject Access Requests + pseudonimização em `sessions_stream` + anonimização ClickHouse (TTL/partition replacement).
- **Fase 5** — `config_snapshot`: leitura read-only do namespace `masking` do Config API para verificação DPO.

---

---

