# TODO — PlugHub Itens Pendentes

> Itens genuinamente não implementados. Histórico de implementações concluídas em `CHANGELOG.md`.

---

---

## platform-ui — Próximas features planejadas

### #30 — ✅ Contacts & Nav Restructure — implementado (2026-05-08)

Ver CHANGELOG 2026-05-08 e `docs/modules/task-30-contacts-restructure.md`.

**Pendências futuras decorrentes:**
- `AgentsPage` / Lista sub-tab: requer backend Arc 8 (`GET /reports/agent-performance/daily`)
- `AnaliseProcessosPage`: requer endpoint analytics-api para métricas de workflow
- `AnaliseQualidadePage`: requer `GET /reports/evaluations/summary` da evaluation-api
- `FlowMonitorPage`: select de pools hardcoded (sem opções dinâmicas) — melhorar com fetch de pools disponíveis
- `EventsPage`: requer endpoint `GET /reports/events` no analytics-api (pode ainda não existir)

### #31 / #33 — ✅ Flow/Deploy: modelo pool-centric 3 slots — implementado (2026-05-08)

Ver CHANGELOG 2026-05-08 (Tasks #31 e #33).

**Pendências futuras decorrentes:**
- `AgentFlowDeployPage`: role-based access control (developer vs operator) — atualmente todos os usuários veem o botão "Editar slot"; ABAC não está aplicado ainda
- `skill_scheduled_deploy_v1` precisa existir como skill no registry para o agendamento funcionar (legado do #31 — seção de agendamento foi removida no #33)

---

## platform-ui — Backend pendente para features de config (2026-05-06)

### Quotas — refatoração

`quota.llm_tokens_daily` e `quota.messages_daily` devem migrar para o namespace `ai_gateway` como limites por conta API (não por tenant). O namespace `quota` simplifica para apenas `max_concurrent_sessions`.

---

## Usage Metering — Channel Gateway adapters

- **whatsapp_conversations, voice_minutes, sms_segments, email_messages** *(deferred)*: funções em `usage_emitter.py` implementadas, mas os adapters de canal ainda não as chamam. Será wired quando cada adapter for criado (WhatsApp, WebRTC/Voice, SMS, Email).

---

## Pricing Module

- **Integração metering × pricing** *(deferred)*: módulo que lê contadores de `usage.events` no Redis/ClickHouse, aplica planos configurados no Config API e escreve `{tenant}:quota:limit:*` no Redis. Metering registra mas pricing não consome.

---

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


## Skill Flow Editor — Folder Organization (new feature)

O `SkillFlowsPage` exibe lista plana. Não há pastas/grupos. Melhoria desejada: agrupamento visual por `classification.type` (orchestrator/vertical/horizontal) ou por pasta livre configurável no `skill_id` (ex: `skill_sac_*/skill_retencao_*` agrupados). Separação visual entre "skills de workflow" e "agents operacionais" simplificaria a navegação em registries com muitos skills.

**Escopo sugerido:** filtro/toggle por `classification.type` na sidebar do editor (sem filesystem de pastas — apenas agrupamento visual).

---

## CLAUDE.md — Otimização (Fases 2 e 3)

**Fase 2** *(blocked by Fase 1)*: Mover Arc 6, Arc 4, Arc 7, ABAC e ContextStore para arquivos em `docs/modules/`. Manter no CLAUDE.md apenas resumo de 15–20 linhas por módulo com link para o arquivo completo.

**Fase 3** *(blocked by Fase 2)*: Mover seções menores (WebChat Channel, Instance Bootstrap, Pool Lifecycle Hooks, Session Replayer, Usage Metering, Pricing Module) para `docs/modules/`. Revisão final para target ≤ 800 linhas no CLAUDE.md.
