# Outbound — Fase 1 (Substrato Mínimo) — Implementation Spec

> **Status:** Pré-código (spec de implementação). Escopo aprovado: **Fase 1 backend e2e, sem UI**.
> **Design de referência (fechado):** [`outbound-mailing-campaign-design.md`](outbound-mailing-campaign-design.md).
> **Contexto:** o módulo Outbound é a **Fase 4 (opcional) do arco Scheduler**. Esta é a 1ª das 5 sub-fases
> internas do módulo. Norte: substrato genérico (`mailing` + `campaign` + governança); **survey é consumidor,
> não dono**. O scheduler é a cadência com **diff zero** (a agenda já entrega `payload` como `context` ao pool
> webhook — ver `packages/scheduler-api/src/plughub_scheduler_api/dispatcher.py`).

---

## 0. Objetivo e critério de pronto

**Objetivo:** provar a abstração ponta-a-ponta — uma **agenda** dispara um **pool webhook** cujo **skill drena**
um lote de um **mailing**, e **contata** (via `collect`) cada entrada usando o alvo lido do `entry.metadata`,
gravando o resultado por-campanha. **Zero diff no scheduler.**

**Pronto quando** o smoke `infra/test/smoke_outbound_fase1.sh` passa:
1. Seed de um mailing + N entries + uma campaign (via REST do `mailing-api`).
2. `fire-now` da agenda (scheduler) → cria sessão no pool `outbound_demo`.
3. O skill drena o lote (claim atômico — **sem drain duplo** em re-disparo), faz `collect` por entrada.
4. `campaign_deliveries` tem 1 linha por entrada com `result` ∈ {contacted, responded, failed} + `session_id`.
5. Re-disparo imediato **não re-drena** entradas já claimadas (idempotência por `UNIQUE(campaign_id, entry)`).

**Fora de escopo nesta fase** (sub-fases seguintes): `contact_log`/`contact_policy`/`contact_eligibility_check`
(Fase 2 — e este `contact_eligibility_check` **substitui** o `survey_eligibility_check`; não haverá tool de
elegibilidade específica de survey. Decisão 2026-07-21; sequência: outbound primeiro, survey depois);
janela de contato/calendar + pacing por `pool_status_get` + preferência do cadastro (Fase 3);
importador de arquivos (Fase 4); survey outbound e2e + `journey_complete` (Fase 5); **UI** (fatia 1b — a
invariante "every config field is UI-editable" fica registrada como dívida da fatia 1b).

---

## 1. Decisões desta fase (tomadas)

- **Drain é agent-driven:** o skill chama a tool MCP `campaign_drain`; nenhum componente escreve no DB de config
  fora da API (invariante "agentes só via MCP" + "provisioning only via official API"). Pacing = a agenda
  recorrente (cada tick drena ≤ `batch_size`; capacidade real do pool entra na Fase 3).
- **`metadata_contract` é rótulo doc-only** (o design diz "doc, não enforce"). O `entry.metadata` é **blob opaco**
  (contrato produtor↔consumidor entre quem faz `mailing_add` e o skill outbound).
- **Claim per-campanha:** `campaign_deliveries UNIQUE(campaign_id, mailing_entry_id)` — 1 entrega por campanha por
  entrada. Drain compartilhado entre 2 campanhas do mesmo mailing = **residual adiado** (não ocorre no demo).
- **Ciclo da entrega:** o `drain` **claima** (cria `campaign_delivery` com `result='claimed'` + `claimed_at`); após
  o `collect`, o skill chama `campaign_delivery_result` para setar `contacted|responded|failed` + `session_id`.
  Degradação nunca silenciosa: `collect` sem resposta → `failed` com motivo, não sumiço.
- **Store canônico:** o domínio `outbound` ganha **um** store (`mailing-api`, schema PG `outbound`) — consistente
  com "one source per domain". Sem duplicação com scheduler (cadência) ou agent-registry (pool/skill).
- **Porta:** `mailing-api` = **3660** (sibling do scheduler 3650). Ledger/execução da sessão vive no ciclo da
  sessão (drill-through por `session_id`), nunca espelhado aqui.

**Residuais adiados** (decidir quando surgirem): dedup_key exato por `dedup_policy`; 2 campanhas drenando o mesmo
mailing compartilhado; versionamento do `metadata_contract`; TTL/expiração de entradas.

---

## 2. Schema PG (schema `outbound`) — Fase 1

Padrão `scheduler-api/db.py`: asyncpg raw, `ensure_schema` idempotente, JSONB cujos shapes são o contrato Zod em
`@plughub/schemas/outbound.ts` (validado no ingest por Pydantic no `router.py`). **Fase 1 cria 4 tabelas**
(`contact_log`/`contact_policy` são Fase 2).

```sql
CREATE SCHEMA IF NOT EXISTS outbound;

-- A lista (audiência), reutilizável
CREATE TABLE IF NOT EXISTS outbound.mailings (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         TEXT        NOT NULL,
    name              TEXT        NOT NULL,
    description       TEXT,
    dedup_policy      TEXT        NOT NULL DEFAULT 'customer_context'
                                  CHECK (dedup_policy IN ('customer','customer_context','none')),
    metadata_contract TEXT,       -- rótulo doc-only do contrato produtor↔consumidor
    entry_ttl_seconds INTEGER,    -- null = persistente
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mailings_tenant
    ON outbound.mailings (tenant_id, created_at DESC);

-- pessoa + canais + CONTEXTO (a unidade); (pessoa, contexto), não "a pessoa"
CREATE TABLE IF NOT EXISTS outbound.mailing_entries (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    mailing_id    UUID        NOT NULL REFERENCES outbound.mailings(id) ON DELETE CASCADE,
    tenant_id     TEXT        NOT NULL,
    customer_id   TEXT,                     -- nativo (resolvedor); null = cru não resolvido
    contacts      JSONB       NOT NULL DEFAULT '{}',  -- {whatsapp,email,sms,voice,...}
    metadata      JSONB       NOT NULL DEFAULT '{}',  -- CONTEXTO OPACO
    dedup_key     TEXT        NOT NULL,     -- derivado da dedup_policy (ver §5)
    source        TEXT,                     -- 'skill:{skill_id}' | 'import:{import_id}'
    status        TEXT        NOT NULL DEFAULT 'active'
                              CHECK (status IN ('active','expired','unsubscribed','invalid')),
    added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (mailing_id, dedup_key)          -- upsert por dedup_key
);
CREATE INDEX IF NOT EXISTS idx_entries_drain
    ON outbound.mailing_entries (mailing_id, status, added_at);

-- COMO o mailing é usado (orquestrador fino)
CREATE TABLE IF NOT EXISTS outbound.campaigns (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           TEXT        NOT NULL,
    name                TEXT        NOT NULL,
    mailing_id          UUID        NOT NULL REFERENCES outbound.mailings(id),
    pool_id             TEXT        NOT NULL,   -- pool webhook outbound (S4: endereça POOL)
    selection           JSONB,                 -- predicado sobre entry.metadata (fatia)
    channel_policy      JSONB       NOT NULL DEFAULT '{}',
    contact_calendar_id TEXT,                  -- Fase 3
    contact_policy_id   UUID,                  -- Fase 2
    transactional       BOOLEAN     NOT NULL DEFAULT false,
    batch_size          INTEGER     NOT NULL DEFAULT 50,
    pacing              JSONB       NOT NULL DEFAULT '{}',   -- Fase 3
    retry               JSONB       NOT NULL DEFAULT '{}',   -- {max_attempts,backoff}
    agenda_id           UUID,                  -- cadência (scheduler) — referência
    status              TEXT        NOT NULL DEFAULT 'active'
                                    CHECK (status IN ('active','paused','completed','archived')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_campaigns_tenant
    ON outbound.campaigns (tenant_id, created_at DESC);

-- estado POR-CAMPANHA por entrada (suppression + claim + drill)
CREATE TABLE IF NOT EXISTS outbound.campaign_deliveries (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id      UUID        NOT NULL REFERENCES outbound.campaigns(id) ON DELETE CASCADE,
    mailing_entry_id UUID        NOT NULL REFERENCES outbound.mailing_entries(id) ON DELETE CASCADE,
    tenant_id        TEXT        NOT NULL,
    claimed_at       TIMESTAMPTZ,
    contacted_at     TIMESTAMPTZ,
    session_id       TEXT,
    root_session_id  TEXT,
    result           TEXT        NOT NULL DEFAULT 'claimed'
                                 CHECK (result IN ('claimed','pending','contacted','responded',
                                                   'failed','skipped_ineligible','suppressed')),
    attempts         INTEGER     NOT NULL DEFAULT 0,
    error            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (campaign_id, mailing_entry_id)     -- idempotência do claim
);
CREATE INDEX IF NOT EXISTS idx_deliveries_campaign
    ON outbound.campaign_deliveries (campaign_id, created_at DESC);
```

---

## 3. Contratos Zod — `@plughub/schemas/src/outbound.ts` (novo)

Espelha `scheduler.ts` (named exports; nunca `export *`; index reexporta). São a fonte canônica dos blobs JSONB e
dos payloads REST. Pydantic no `mailing-api` valida contra o mesmo shape.

```ts
// Entidades
MailingSchema             { id, tenant_id, name, description?, dedup_policy, metadata_contract?,
                            entry_ttl_seconds?, created_at, updated_at }
DedupPolicySchema         z.enum(["customer","customer_context","none"])
MailingEntrySchema        { id, mailing_id, tenant_id, customer_id|null, contacts: record,
                            metadata: record, dedup_key, source?, status, added_at, expires_at|null, updated_at }
EntryStatusSchema         z.enum(["active","expired","unsubscribed","invalid"])
CampaignSchema            { id, tenant_id, name, mailing_id, pool_id, selection?|null, channel_policy,
                            contact_calendar_id?|null, contact_policy_id?|null, transactional, batch_size,
                            pacing, retry, agenda_id?|null, status, created_at, updated_at }
CampaignStatusSchema      z.enum(["active","paused","completed","archived"])
CampaignDeliverySchema    { id, campaign_id, mailing_entry_id, tenant_id, claimed_at|null, contacted_at|null,
                            session_id|null, root_session_id|null, result, attempts, error|null,
                            created_at, updated_at }
DeliveryResultSchema      z.enum(["claimed","pending","contacted","responded","failed",
                                  "skipped_ineligible","suppressed"])

// Inputs REST (server owns id/status-derivation/timestamps)
CreateMailingSchema       { name, description?, dedup_policy?, metadata_contract?, entry_ttl_seconds? }
UpdateMailingSchema       (todos opcionais)
AddEntrySchema            { customer_id?, contacts?, metadata, dedup_key?, ttl_seconds? }  // backing do mailing_add
CreateCampaignSchema      { name, mailing_id, pool_id, selection?, channel_policy?, transactional?,
                            batch_size?, retry?, agenda_id? }
UpdateCampaignSchema      (todos opcionais)
DrainRequestSchema        { limit? }                     // opcional; default = campaign.batch_size
DrainedEntrySchema        { delivery_id, entry_id, customer_id|null, contacts, metadata }
DrainResponseSchema       { campaign_id, drained: DrainedEntrySchema[] }
DeliveryResultInputSchema { result, session_id?, root_session_id?, error? }
```

---

## 4. `mailing-api` — REST (porta 3660)

Estrutura idêntica ao `scheduler-api`: `config.py`, `db.py`, `router.py`, `main.py`, `Dockerfile`,
`pyproject.toml`. Header `X-Tenant-ID` obrigatório. `ensure_schema` no startup.

```
# Mailings
GET    /v1/mailings                      -> [Mailing]
POST   /v1/mailings                      (CreateMailing)            -> Mailing
GET    /v1/mailings/{id}                 -> Mailing
PATCH  /v1/mailings/{id}                 (UpdateMailing)            -> Mailing
DELETE /v1/mailings/{id}                 -> 204

# Entries (backing do mailing_add — upsert por dedup_key)
POST   /v1/mailings/{id}/entries         (AddEntry)                 -> { entry_id, deduped }
GET    /v1/mailings/{id}/entries?status= -> [MailingEntry]

# Campaigns
GET    /v1/campaigns                     -> [Campaign]
POST   /v1/campaigns                     (CreateCampaign)           -> Campaign
GET    /v1/campaigns/{id}                -> Campaign
PATCH  /v1/campaigns/{id}                (UpdateCampaign)           -> Campaign

# Drain (claim atômico) + resultado por entrega
POST   /v1/campaigns/{id}/drain          (DrainRequest)             -> DrainResponse
POST   /v1/deliveries/{id}/result        (DeliveryResultInput)      -> CampaignDelivery
GET    /v1/campaigns/{id}/deliveries     -> [CampaignDelivery]      # drill/smoke
```

`GET /health` para o compose healthcheck.

---

## 5. Drain + claim (SQL) — o coração da Fase 1

`POST /v1/campaigns/{id}/drain` roda numa transação: seleciona entradas elegíveis **ainda não entregues por esta
campanha**, com `FOR UPDATE SKIP LOCKED` (evita drain duplo concorrente), e **claima** inserindo
`campaign_deliveries` (`result='claimed'`, `claimed_at=now()`). Devolve `delivery_id` + `entry` para o skill.

```sql
WITH candidatas AS (
  SELECT e.*
  FROM outbound.mailing_entries e
  JOIN outbound.campaigns c ON c.id = $1 AND c.tenant_id = $2
  WHERE e.mailing_id = c.mailing_id
    AND e.tenant_id  = $2
    AND e.status = 'active'
    AND (e.expires_at IS NULL OR e.expires_at > now())
    -- selection (fatia por metadata) aplicada no app quando presente (Fase 1: opcional)
    AND NOT EXISTS (
      SELECT 1 FROM outbound.campaign_deliveries d
      WHERE d.campaign_id = c.id AND d.mailing_entry_id = e.id
        AND (d.result <> 'failed' OR d.attempts >= COALESCE((c.retry->>'max_attempts')::int, 1))
    )
  ORDER BY e.added_at
  LIMIT LEAST($3, (SELECT batch_size FROM outbound.campaigns WHERE id = $1))
  FOR UPDATE OF e SKIP LOCKED
)
INSERT INTO outbound.campaign_deliveries (campaign_id, mailing_entry_id, tenant_id, claimed_at, result)
SELECT $1, cand.id, $2, now(), 'claimed' FROM candidatas cand
ON CONFLICT (campaign_id, mailing_entry_id) DO NOTHING
RETURNING id AS delivery_id, mailing_entry_id;
```

Depois faz `SELECT` das entries claimadas para montar `DrainResponse.drained[]`
(`{delivery_id, entry_id, customer_id, contacts, metadata}`). **`dedup_key`** (§2): `customer` → `customer_id`;
`customer_context` → `hash(customer_id + metadata canônico)`; `none` → `gen_random_uuid()::text` (sempre distinto).

---

## 6. Tools MCP — `packages/mcp-server-plughub/src/tools/outbound.ts` (novo)

Padrão `tools/deploy.ts`: `server.tool(...)`, input Zod local, `x-tenant-id`, `AGENT_REGISTRY_SERVICE_TOKEN`-style
service cred se aplicável, **`isError` em qualquer não-2xx** (degradação nunca silenciosa → `on_failure` do step),
auditado pelo `McpInterceptor` (invariante — sem opt-out). `deps = { mailingApiUrl, tenantId }`.

```
mailing_add(mailing_id, metadata, customer_id?, contacts?, dedup_key?, ttl_seconds?, tenant_id?)
    -> { entry_id, deduped }                 # wrap POST /v1/mailings/{id}/entries
campaign_drain(campaign_id, limit?, tenant_id?)
    -> { campaign_id, drained: [...] }       # wrap POST /v1/campaigns/{id}/drain
campaign_delivery_result(delivery_id, result, session_id?, root_session_id?, error?, tenant_id?)
    -> CampaignDelivery                      # wrap POST /v1/deliveries/{id}/result
```

Registrar `registerOutboundTools(server, deps)` no bootstrap do mcp-server (onde `registerDeployTools` é chamado)
+ `MAILING_API_URL` no env do serviço. Grupo lógico `outbound` (para futura filtragem de permissões).

---

## 7. Demo — pool + skill + seed

**Pool `outbound_demo`** (YAML em `infra/registry/`): `channel_types: [webhook]`, perfil **workflow**, slot com
`skill_outbound_demo_v1`.

**`skill_outbound_demo_v1.yaml`** (perfil workflow — steps permitidos: task/choice/catch/escalate/complete/
invoke/reason/suspend/collect/receive; **proibido** menu/notify). Fluxo:

```
entry: drenar
- drenar (invoke campaign_drain, campaign_id: "$.config.campaign_id" | "@ctx.campaign_id")
        output_as: lote ; on_success: percorrer ; on_failure: encerrar
- percorrer (loop over "$.pipeline_state.lote.drained", item_as: alvo, body: contatar,
             collect: r, results_as: resultados, on_complete: encerrar)
- contatar (collect requires:[text], target lido de "$.pipeline_state.alvo.metadata.<canal/endereço>",
            message de "$.pipeline_state.alvo.metadata.mensagem"; on_success: marcar_ok, on_failure: marcar_fail,
            on_timeout: marcar_fail)
- marcar_ok (invoke campaign_delivery_result: delivery_id "$.pipeline_state.alvo.delivery_id",
             result "responded"/"contacted", session_id "$.session_id") -> percorrer
- marcar_fail (invoke campaign_delivery_result: result "failed", error ...) -> percorrer
- encerrar (complete outcome: resolved)
```

`campaign_id` entra por **payload da agenda** → `context` do webhook → resolvido no flow. Definir via `config_param`
(padrão `skill_survey_multi_v1`) **ou** direto do `context`. Decisão de implementação: `config_param campaign_id`
não cabe (é por-disparo, não por-deploy) → ler do **payload/context** da agenda (`@ctx.campaign_id`).

**Seed `infra/test/seed_outbound_demo.sh`** (host → `docker compose exec -T`): cria mailing, 2–3 entries
(`metadata` com alvo+mensagem), campaign apontando `outbound_demo` + `mailing_id`.

---

## 8. Agenda (diff zero) + smoke

Cria uma agenda no `scheduler-api` (`target_pool_id: outbound_demo`, `payload: {campaign_id: <id>}`). Nenhuma
linha do scheduler muda — o `dispatcher` já injeta `payload` como `context`.

**`infra/test/smoke_outbound_fase1.sh`:**
1. `seed_outbound_demo.sh` → captura `campaign_id`.
2. Cria agenda (`once`, `fire_at` no passado curto) **ou** usa `POST /v1/agendas/{id}/fire` (fire-now).
3. Poll: `GET /v1/campaigns/{id}/deliveries` → asserta N deliveries, `session_id` presente, `result` final.
4. Dispara `fire` de novo → asserta **contagem de deliveries inalterada** (sem re-drain).
5. (Opcional) confere sessões criadas no analytics/monitor por `session_id`.

---

## 9. Ordem de build/run (eu edito, você roda)

Regras: source **bakeia** → `build <svc> && up -d --force-recreate <svc>`. YAML de skill = mount ro → `restart`.
`@plughub/schemas` é dependência TS de `mcp-server-plughub` (e agent-registry) → **rebuildar schemas antes** dos
consumidores TS.

1. **schemas**: escrever `outbound.ts` + index → `build` do pacote schemas → `build mcp-server-plughub`.
2. **mailing-api**: novo serviço + entrada no `docker-compose.demo.yml` → `build mailing-api && up -d mailing-api`
   (roda `ensure_schema` no boot). DB `plughub_demo` (user `plughub`).
3. **mcp-server**: `tools/outbound.ts` + registro + env `MAILING_API_URL=http://mailing-api:3660` →
   `build mcp-server-plughub && up -d --force-recreate mcp-server-plughub`.
4. **Demo**: pool YAML (RegistrySyncer semeia via API — seed-if-absent) + skill YAML (mount ro → `restart` do
   serviço que carrega skills). Deploy do slot: pool novo → RegistrySyncer publica `skill.flow`; se migrado a
   `PoolSkillSlot`, exige `set-next` + `promote`.
5. **Seed + agenda + smoke**: `seed_outbound_demo.sh` → cria agenda → `smoke_outbound_fase1.sh`.

**Testes:** `pytest` ad-hoc no mailing-api (`exec mailing-api pip install -q pytest pytest-asyncio`) para o drain
(claim único sob concorrência, dedup upsert, retry-gated re-drain). Testes TS do mcp-server para os wrappers
(isError em não-2xx).

---

## 10. Impacto em invariantes (checagem)

- **One source per domain** ✅ — `outbound` ganha um store (`mailing-api`); sem duplicar scheduler/agent-registry.
- **Agentes só via MCP** ✅ — skill drena via `campaign_drain`, nunca SQL direto.
- **Provisioning only via official API** ✅ — seed escreve via REST do `mailing-api`.
- **POOL é a unidade endereçável** ✅ — campaign aponta `pool_id`; agenda aponta `target_pool_id`.
- **Degradação nunca silenciosa** ✅ — tools retornam `isError`; `collect` falho → `failed` com motivo.
- **Config UI-editable** ⚠️ — mailing/campaign são config de tenant; **UI adiada** para fatia 1b (dívida registrada
  no TODO). Fase 1 é backend-provável.
- **English no código / PT em spec/dados** ✅.

---

## 11. Arquivos (a criar/tocar)

```
packages/schemas/src/outbound.ts                         (novo)
packages/schemas/src/index.ts                            (export)
packages/mailing-api/**                                  (novo pacote: config/db/router/main + Dockerfile + pyproject)
docker-compose.demo.yml                                  (serviço mailing-api + env MAILING_API_URL no mcp-server)
packages/mcp-server-plughub/src/tools/outbound.ts        (novo)
packages/mcp-server-plughub/src/<bootstrap>.ts           (registro + deps)
infra/registry/pools.yaml (ou equivalente)               (pool outbound_demo)
packages/skill-flow-engine/skills/skill_outbound_demo_v1.yaml   (novo)
infra/test/seed_outbound_demo.sh                         (novo)
infra/test/smoke_outbound_fase1.sh                       (novo)
docs/arcos/outbound.md                                   (novo — arco)
CLAUDE.md                                                 (resumo ~15 linhas)
CHANGELOG.md                                             (entrada Fase 1)
TODO.md                                                  (Fase 1 ✅ / Fases 2–5 pendentes)
```
