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

---

## Dashboard #35 — ✅ Sistema de Cards Genéricos — implementado (2026-05-09)

Ver CHANGELOG 2026-05-09 (Parts 1–4). Spec completa em `docs/modules/dashboard.md`.

**Pendências futuras decorrentes:**
- `FlowMonitorPage`: select de pools hardcoded — substituir por fetch dinâmico de `GET /v1/pools` no agent-registry

---

## Usage Metering — Channel Gateway adapters

- **whatsapp_conversations, voice_minutes, sms_segments, email_messages** *(deferred)*: funções em `usage_emitter.py` implementadas, mas os adapters de canal ainda não as chamam. Será wired quando cada adapter for criado (WhatsApp, WebRTC/Voice, SMS, Email).

---

## Pricing Module

- **Integração metering × pricing** *(deferred)*: módulo que lê contadores de `usage.events` no Redis/ClickHouse, aplica planos configurados no Config API e escreve `{tenant}:quota:limit:*` no Redis. Metering registra mas pricing não consome.

---

---

---

---

## Channel Endpoints — channel-gateway integration (pending)

Layers 1 (agent-registry), 3 (platform-ui), 4 (schemas) ✅ complete — see CHANGELOG 2026-05-07.

**Layer 2 — channel-gateway** *(deferred)*: Replace hardcoded pool lookup with HTTP call to agent-registry `GET /v1/channel-endpoints?channel={ch}&identifier={id}`. Cache with short TTL (~30s) to avoid hot-path latency. Also run `prisma migrate dev --name add_channel_endpoint` in agent-registry when network is available.

---


## Skill Flow Editor — Folder Organization (new feature)

O `SkillFlowsPage` exibe lista plana. Não há pastas/grupos. Melhoria desejada: agrupamento visual por `classification.type` (orchestrator/vertical/horizontal) ou por pasta livre configurável no `skill_id` (ex: `skill_sac_*/skill_retencao_*` agrupados). Separação visual entre "skills de workflow" e "agents operacionais" simplificaria a navegação em registries com muitos skills.

**Escopo sugerido:** filtro/toggle por `classification.type` na sidebar do editor (sem filesystem de pastas — apenas agrupamento visual).

---

## CLAUDE.md — Otimização (Fase 3)

**Fase 3**: Revisão final para confirmar CLAUDE.md ≤ 800 linhas. Mover seções remanescentes se necessário. Fase 2 concluída — ver CHANGELOG 2026-05-09.
