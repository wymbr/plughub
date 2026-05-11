# TODO — PlugHub Itens Pendentes

> Itens genuinamente não implementados. Histórico de implementações concluídas em `CHANGELOG.md`.

---

## platform-ui — Próximas features planejadas

### #30 — ✅ Contacts & Nav Restructure — implementado (2026-05-08)
### Backend-dependent Pages — ✅ implementado (2026-05-09)

Ver CHANGELOG 2026-05-09.

**Pendências futuras decorrentes do #30 — concluídas.**

---

### #31 / #33 — ✅ Flow/Deploy: modelo pool-centric 3 slots — implementado (2026-05-08)

Ver CHANGELOG 2026-05-08 (Tasks #31 e #33).

**Pendências futuras decorrentes:**
- `skill_scheduled_deploy_v1` precisa existir como skill no registry para o agendamento funcionar

---

## Dashboard #35 — ✅ Sistema de Cards Genéricos — implementado (2026-05-09)

Ver CHANGELOG 2026-05-09 (Parts 1–4). Spec completa em `docs/modules/dashboard.md`.

---

## Usage Metering — Channel Gateway adapters

- **whatsapp_conversations, voice_minutes, sms_segments, email_messages** *(deferred)*: funções em `usage_emitter.py` implementadas, mas os adapters de canal ainda não as chamam. Será wired quando cada adapter for criado (WhatsApp, WebRTC/Voice, SMS, Email).

---

## Pricing Module

- **Integração metering × pricing** *(deferred)*: módulo que lê contadores de `usage.events` no Redis/ClickHouse, aplica planos configurados no Config API e escreve `{tenant}:quota:limit:*` no Redis. Metering registra mas pricing não consome.

---

## Channel Endpoints — channel-gateway integration (pending)

Layers 1 (agent-registry), 3 (platform-ui), 4 (schemas) ✅ complete — see CHANGELOG 2026-05-07.

**Layer 2 — channel-gateway** *(deferred)*: Replace hardcoded pool lookup with HTTP call to agent-registry `GET /v1/channel-endpoints?channel={ch}&identifier={id}`. Cache with short TTL (~30s) to avoid hot-path latency. Also run `prisma migrate dev --name add_channel_endpoint` in agent-registry when network is available.

---

## Skill Flow Editor — Folder Organization (new feature)

O `SkillFlowsPage` exibe lista plana. Não há pastas/grupos. Melhoria desejada: agrupamento visual por `classification.type` (orchestrator/vertical/horizontal) ou por pasta livre configurável no `skill_id`. Separação visual entre "skills de workflow" e "agents operacionais" simplificaria a navegação em registries com muitos skills.

**Escopo sugerido:** filtro/toggle por `classification.type` na sidebar do editor (sem filesystem de pastas — apenas agrupamento visual).

---

## Arc 9 — Agent Groups & Supervisor Scope *(implementado — 2026-05-11)*

Implementação completa: auth-api (tabelas + CRUD REST + shift resolution + JWT claims), analytics-api (5 report endpoints filtrados), platform-ui (Config/Groups page + Monitor/Console scope filtering via `supervised_agent_types` e `accessiblePools`). Ver CHANGELOG.

**Spec completa:** [`docs/modules/arc9-agent-groups.md`](docs/modules/arc9-agent-groups.md)

---

## Masking — Channel-Aware Display Architecture

**Bloco 1 (concluído 2026-05-11)**: correções de segurança — logs de `menu_submit` redactados, fallback Kafka redacta `input_snapshot`/`output_snapshot`, `masked_input_fields` populado no `AuditRecord`. Ver CHANGELOG.

**Bloco 2 (concluído 2026-05-11)**: `MaskedToken` component + `renderWithTokens` + `useMaskingDisplayRules` + migração `SessionTranscript` (remove `maskSensitiveContent`) + `MaskingPage` seção 5 (display rules por categoria × canal). Ver CHANGELOG.

**Bloco 3 — Channel Gateway TTS** *(deferred até implementação de voz)*: quando qualquer adapter de voz/TTS for criado, deve consultar `rule.{category}.display_voice` no namespace `masking` do Config API antes de passar texto ao sintetizador. Comportamentos: `silence` (pula o valor), `beep` (tom de beep), `speak_placeholder` (fala "valor mascarado"). Aplica-se a qualquer canal que gere áudio — não só voice/webrtc. Não implementar antes de definir qual engine TTS será usada.

---

## Audit Profile — LGPD Compliance Role

Perfil dedicado ao DPO/Compliance para auditoria de dados pessoais. Decisão técnica: implementar como módulo ABAC `audit` separado (não uma role fixa) para manter ortogonalidade com `operator`/`supervisor`/`admin`.

**Escopo do módulo ABAC `audit`:**
- `sessions` — leitura de `original_content` (dados desmascarados) via endpoint separado com log de acesso próprio
- `mcp_calls` — leitura de `input_snapshot`/`output_snapshot` com `masked_input_fields` no `mcp.audit` ClickHouse
- `user_access` — leitura de logs de autenticação e refresh token rotation
- `data_requests` — CRUD de SARs (Subject Access Requests) e erasure requests
- `config_snapshot` — leitura somente de configurações ativas (masking rules, retention policies)

**Para implementar:**
1. Adicionar `audit` ao `infra/modules.yaml` e ao `PermissionChecker`
2. Endpoint `GET /v1/audit/sessions/{id}/original-content` (auth-api ou novo audit-api) com log de acesso obrigatório
3. Endpoint `GET /v1/audit/mcp-calls` consumindo ClickHouse `mcp_audit` com filtro por `masked_input_fields IS NOT NULL`
4. Pipeline SAR/erasure: pseudonimização em `sessions_stream` + anonimização em analytics ClickHouse
5. Platform-UI: página `AuditPage` em novo grupo de nav (somente role com módulo `audit`)

---

## Arc 10 — Journey: Multi-Session Service Automation *(não implementado)*

Spec completa em [`docs/modules/arc10-journey.md`](docs/modules/arc10-journey.md).

**Fase A — Backend foundation** *(pré-requisito de tudo)*
- Schema `journeys` table em `workflow-api` (ou novo `journey-api`)
- Campo `journey_id` nullable em `sessions` (PostgreSQL + ClickHouse `session_timeline`)
- MCP tools `journey_start` + `journey_link_session` em `mcp-server-plughub` (grupo `journey`)
- `JourneyEventSchema` em `@plughub/schemas`
- Kafka topic `journey.events` + consumer em analytics-api → `analytics.journeys` ClickHouse
- `workflow-api`: aceitar `journey_id` no trigger payload; publicar eventos de ciclo de vida

**Fase B — Vinculação automática via collect**
- Channel Gateway: receber `journey_id` do `collect.events` Kafka e taguear sessão criada
- Flag `creates_journey: true` no skill YAML + skill-flow-engine chamando `journey_start` no primeiro step

**Fase C — Monitor (ProcessosPage)**
- Journey list + detail panel + drill-down → sessions existentes → transcript
- Journey KPIs em Analytics: duração mediana, taxa resolução, contatos médios por skill_id

**Fase D — Console (AgentAssistPage)**
- HistoricoTab: seção "Processos em aberto" para o customer_id atual
- ActionBar: botão "Iniciar Processo" com selector (filtrado por `mentionable_journeys` do pool)
- `@mention` protocol: extensão `@journey:<skill_id>`

**Fase E — Relatórios consolidados**
- `GET /reports/journeys` em analytics-api com filtros por skill_id, status, período
- Dashboard cards de jornada (usando sistema de cards genéricos existente)

**Decisões pendentes antes da Fase A:**
- Journey entity em `workflow-api` ou novo `journey-api` separado?
- `mentionable_journeys` configurado no pool YAML ou no skill YAML?

---

## CLAUDE.md — Otimização (Fase 3)

**Fase 3**: Revisão final para confirmar CLAUDE.md ≤ 800 linhas. Mover seções remanescentes se necessário. Fase 2 concluída — ver CHANGELOG 2026-05-09.
