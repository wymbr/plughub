# Outbound — Mailing + Campaign + Delivery (arco)

> **Status:** Fase 1 **✅ validada E2E** (`smoke_outbound_fase1.sh`); Fase 2 (governança) **✅ validada via API**
> (`smoke_outbound_fase2.sh`) — 2026-07-21. Fase 2b (fiar no skill) + Fases 3–5 pendentes.
> Design fechado: [`../product/outbound-mailing-campaign-design.md`](../product/outbound-mailing-campaign-design.md).
> Spec de implementação da Fase 1: [`../product/outbound-fase1-implementation-spec.md`](../product/outbound-fase1-implementation-spec.md).
> Contexto: o módulo Outbound é a **Fase 4 (opcional) do arco Scheduler** (cadência com diff zero).

Substrato **genérico** de contato ativo: `mailing` (audiência reutilizável) + `campaign` (orquestrador fino) +
governança de contato. **Survey é o primeiro consumidor (S11 agendado), não o dono.** Invariantes de modelagem:
metadado da entrada é **opaco** (contrato produtor↔consumidor), **membership (`mailing_entries`) ≠ suppression
(`campaign_deliveries`)**, a entrada é `(pessoa, contexto)`, e a unidade endereçável é o **POOL** (S4).

## Componentes

| Peça | Papel |
|---|---|
| **`mailing-api`** (Python FastAPI + asyncpg, porta **3660**) | Store canônico do domínio `outbound` (schema PG `outbound`). CRUD de mailings/campaigns, `mailing_add` (upsert por `dedup_key`), **drain** (claim atômico) e resultado de entrega. |
| **schema PG `outbound`** | `mailings`, `mailing_entries`, `campaigns`, `campaign_deliveries` (Fase 1). `contact_log`/`contact_policy` = Fase 2. |
| **tools MCP `outbound`** (`mcp-server-plughub/tools/outbound.ts`) | `mailing_add`, `campaign_drain`, `campaign_delivery_result` — wrappers finos do mailing-api, `isError` em não-2xx (degradação não silenciosa), auditados via McpInterceptor. |
| **`@plughub/schemas/outbound.ts`** | Contratos Zod (fonte canônica dos blobs JSONB + payloads REST). |
| **pool `outbound_demo` + `skill_outbound_demo_v1`** | Demo perfil workflow: drena → `loop` → `campaign_delivery_result`. Prova o substrato com diff zero no scheduler. |

## Fluxo (Fase 1)

Uma **Agenda** (scheduler) dispara `POST /v1/channels/webhook/pool/outbound_demo` com `payload {campaign_id}`; o
dispatcher entrega o payload como `context` → `@ctx.campaign_id` (mesmo caminho do promote agendado da Scheduler
Fase 2). O skill:

1. `campaign_drain(campaign_id)` → o mailing-api roda o `SELECT … FOR UPDATE SKIP LOCKED` sobre entradas elegíveis
   (active, não expiradas, casando `selection`, ainda não entregues por esta campanha **ou** falha retryável) e
   **claima** cada uma inserindo/atualizando `campaign_deliveries` (`result='claimed'`). Devolve o lote.
2. `loop` sobre `drained` → por entrada, `campaign_delivery_result` (o demo marca `contacted`; um consumidor real
   faria o `collect` antes e marcaria `responded`/`failed`).
3. `complete`.

**Pacing** = a agenda recorrente (cada tick drena ≤ `batch_size`). **Idempotência**: `UNIQUE(campaign_id,
mailing_entry_id)` + `ON CONFLICT DO UPDATE WHERE result='failed' AND attempts<max` → re-disparo não re-drena
entradas já entregues; só re-tenta falhas retryáveis.

> **Achado na validação (fix de engine):** `invoke` dentro de `loop` só rodava a 1ª iteração — o `loop` limpava o
> sentinel de idempotência `:__notified__` (notify) a cada volta mas **não** o `:__invoked__` (invoke), então o
> invoke retornava o resultado cacheado ("completed") sem re-chamar a tool. Corrigido em `skill-flow-engine/steps/
> loop.ts` (clear simétrico). **Ops:** recriar o `skill-flow-service` exige recriar o `orchestrator-bridge` em
> seguida (o bridge mantém conexão viva ao container antigo → `Cannot connect to host skill-flow-service:3460`).

## Drain / claim / retry (semântica)

- **Claim atômico**: `FOR UPDATE OF e SKIP LOCKED` no `SELECT`; `INSERT … ON CONFLICT DO NOTHING/UPDATE` serializa
  drains concorrentes (nunca dois claims da mesma entrada).
- **Retry por campanha**: `campaign.retry.max_attempts` + `delivery.attempts`. `campaign_delivery_result('failed')`
  incrementa `attempts`; o drain re-elege a falha enquanto `attempts < max_attempts` (via `ON CONFLICT DO UPDATE`
  reabrindo a entrega para `claimed`).
- **`dedup_key`**: `customer` → `cust:{customer_id}`; `customer_context` → `ctx:{sha256(customer_id+metadata)}`;
  `none` → sempre distinto. `mailing_add` faz upsert por `(mailing_id, dedup_key)`.

## Fase 2 — governança de contato (fato × regra × decisão)

Motor **agnóstico** de fadiga. Três peças no schema `outbound` + duas tools:

- **`contact_log`** (fato): todo contato outbound é registrado — `customer_id`, `channel`, `campaign_id`,
  `contacted_at`, `result`. Índices por `(tenant, customer, contacted_at)` e `(tenant, customer, channel,
  contacted_at)`.
- **`contact_policy`** (regra, em camadas): `scope` `tenant`|`campaign` (UNIQUE por `(tenant, scope, scope_id)`);
  `frequency_caps` (`[{window, max, per_channel}]`), `quarantine_after`, `channel_caps` (`{channel:{window,max}}`).
  Janela = `"24h"|"7d"|"60m"|"30s"` ou inteiro em segundos. Efetiva = policy da campanha se existir, senão a do
  tenant, senão **sem regra** (allowed).
- **`contact_eligibility_check(customer_id, channel, campaign_id?, claim=true, at?)`** (decisão): avalia a policy
  efetiva contra o `contact_log` (quarentena → frequency_caps → channel_caps; qualquer cap barra). Se permitido e
  `claim=true`, **grava o fato** na mesma transação (a janela começa no envio). Retorna `{allowed, reason,
  retry_after, claimed}`. `reason` sempre nomeia a regra que barrou (nunca silencioso).
- **`mailing_unsubscribe(customer_id, mailing_id?, channel?)`**: supressão mailing-scoped → `entry.status=
  'unsubscribed'` (o drain já exclui não-`active`). `mailing_id` omisso = todas as listas do cliente.

Tools MCP (`tools/outbound.ts`): `contact_eligibility_check`, `mailing_unsubscribe` — wrappers finos, `isError` em
não-2xx, auditados. Endpoints REST: `POST/GET/PATCH/DELETE /v1/contact-policies`, `POST /v1/contact/eligibility`,
`POST /v1/unsubscribe`. Validação (Fase 2, decisão): **motor via API** (`smoke_outbound_fase2.sh` ✅ — 2 checks no
mesmo cliente/janela → 2º negado por `frequency_cap` + `retry_after`; unsubscribe → drain exclui). Fiar no skill
demo = fatia 2b. *(Achados na validação: `$n` não-referenciado no count por-ramo — Postgres "could not determine
data type"; e `datetime` faltando no import do `router.py` sob `from __future__ import annotations`.)*

**Fora da Fase 2** (Fase 3): opt-out **global** (`do_not_contact` no cadastro do cliente), janela de calendário
(`is_open`), preferência soft (canal/horário), pacing por `pool_status_get`.

## Consumidor: Customer Surveys (S11) — decisões de fronteira

O survey (a implementar **depois** do outbound) é cliente do **caminho batelado/agendado** (NPS/PMF relacional).
O retorno por-resposta (§19 de `customer-surveys.md`) usa **pull inbox**, não mailing/campaign. Decisões fechadas
(2026-07-21): **`contact_eligibility_check` (Fase 2) SUBSTITUI `survey_eligibility_check`** — motor de
elegibilidade único e genérico, sem tool/ledger de survey; a quarentena por tipo de instrumento vira
`contact_policy`. A pertença à journey de um survey batelado (via `journey_merge` a partir de
`metadata.origin_session_id`) é concern de Fase 5.

## Invariantes

- **One source per domain** — `outbound` tem um store (`mailing-api`); sem duplicar scheduler/agent-registry.
- **Agentes só via MCP** — o skill drena via `campaign_drain`, nunca SQL direto.
- **POOL é a unidade endereçável** — `campaign.pool_id` / `agenda.target_pool_id`; nunca skill.
- **Degradação nunca silenciosa** — tools retornam `isError`; `collect` falho → `failed` com motivo.
- **UI-editable** ⚠️ dívida: mailing/campaign são config de tenant → exigem tela (fatia 1b; Fase 1 é backend).

## Pendente (próximas fases)

- **Fase 2b** — fiar `contact_eligibility_check` no skill outbound (gate por entrada no loop: `contacted` ×
  `skipped_ineligible`) + smoke cross-campanha.
- **Fase 3** — portões: janela de contato (calendar `is_open`), capacidade/pacing (`pool_status_get`), preferência
  do cadastro; `channel_policy` possessed-only.
- **Fase 4** — importador anti-corrupção (CSV/xlsx → `mailing_add`).
- **Fase 5** — survey outbound e2e (`journey_complete` no `complete` do processo; `survey_record`→`session_signal`).
- **UI (fatia 1b)** — telas de mailings/campaigns/deliveries no platform-ui.
