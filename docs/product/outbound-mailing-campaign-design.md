# Outbound — Mailing, Campaign & Contact Governance (design)

> **Status:** Proposto (design fechado em discussão 2026-07-21). Pré-código. Não implementado.
> **Norte:** substrato de **outbound genérico** — `mailing` (audiência) + `campaign` (orquestração) +
> **governança de contato** (elegibilidade agnóstica). **Survey é o primeiro consumidor, não o dono.**
> É a base natural da **Scheduler Fase 4** (cadência) e do outbound do **Customer Surveys** (§19).
> Relacionados: [`scheduler-agenda-spec.md`](scheduler-agenda-spec.md), [`../arcos/customer-surveys.md`](../arcos/customer-surveys.md),
> resolvedor de identidade (Fase A/B), Pool lifecycle hooks, Operational Visibility (§3.3c).

---

## 1. Conceito

A decisão de **contatar alguém ativamente** é um pipeline de portões, cada um reusando um subsistema que já
existe, cada um aplicado **só quando configurado** (graceful). O substrato é genérico (serve survey, promo,
cobrança, lembrete); o **core permanece genérico** — a lógica de domínio (o que inserir, como contatar, como
decidir preferência) mora em **skills** e no **motor de contato**, nunca no core.

Três coisas de primeira classe, na mesma linha "config × runtime" do resto da plataforma:

- **Mailing** = a audiência (lista de pessoas + canais + **contexto opaco**), reutilizável, alimentada
  dinamicamente por skills (hooks de lifecycle) ou por um importador de arquivos.
- **Campaign** = **como** o mailing é usado (pool outbound + cadência + canal + governança + fatia).
- **Contact governance** = motor **agnóstico** de elegibilidade/fadiga (fato × regra × decisão), do qual o
  survey é só um chamador com janela própria.

---

## 2. Invariantes de modelagem

- **Metadado da entrada é OPACO à plataforma** — blob JSON, contrato **produtor↔consumidor** entre o skill que
  insere e o skill outbound que consome (igual ao payload da agenda / `context` do collect / DialogForm). O
  primitivo de mailing continua genérico; a *instância* é tipada pelo contrato → compartilhamento **oportunista**
  (`campaign.selection` fatia por metadado), não universal.
- **Membership × suppression** — `mailing_entries` (quem está na lista) é distinto de `campaign_deliveries`
  (quem *esta campanha* já contatou). Mailing compartilhado não se atropela; consumo é per-campanha.
- **A entrada é `(pessoa, contexto)`** — não "a pessoa". Isso torna o ciclo de vida auto-contido (candidato →
  contatado → respondido/expirado) sem afetar outras entradas da mesma pessoa nem outras campanhas.
- **Precedência de decisão:** supressão dura (opt-out/unsubscribe) → **regra de campanha** → preferência soft.
  Regra de campanha vence preferência (canal/horário preferidos apenas reordenam o que a campanha permite);
  **opt-out é veto absoluto** (LGPD), com bypass só se a campanha for explicitamente marcada `transactional`.
- **Core genérico** — inserção e contato são skills; a plataforma só carrega blobs opacos e expõe tools.

---

## 3. Schema (PG, schema `outbound`)

```
outbound.mailings                 -- a lista (audiência), reutilizável
  id                uuid pk
  tenant_id         text
  name, description text
  dedup_policy      text     -- 'customer' | 'customer_context' | 'none'
  metadata_contract text     -- rótulo do contrato produtor↔consumidor (ex. 'survey_context_v1'); doc, não enforce
  entry_ttl_seconds int null  -- retenção padrão das entradas (null = persistente)
  created_at, updated_at

outbound.mailing_entries          -- pessoa + canais + CONTEXTO (a unidade)
  id            uuid pk
  mailing_id    uuid fk
  tenant_id     text
  customer_id   text null    -- nativo (resolvedor); null = contato cru não resolvido (import tenta e cai p/ null)
  contacts      jsonb        -- endereços por canal: {whatsapp, email, sms, voice, ...}
  metadata      jsonb        -- CONTEXTO OPACO (contrato produtor↔consumidor)
  dedup_key     text         -- derivado da dedup_policy do mailing
  source        text         -- procedência: 'skill:{skill_id}' | 'import:{import_id}'
  status        text         -- 'active' | 'expired' | 'unsubscribed' | 'invalid'  (lifecycle GLOBAL da entrada)
  added_at, expires_at null, updated_at
  UNIQUE (mailing_id, dedup_key)  -- quando dedup_policy != 'none'

outbound.campaigns                -- COMO o mailing é usado (orquestrador fino)
  id                  uuid pk
  tenant_id           text
  name                text
  mailing_id          uuid fk     -- a audiência
  pool_id             text        -- pool webhook outbound cujo skill contata (S4: endereça POOL)
  selection           jsonb null  -- predicado sobre entry.metadata (fatia o mailing compartilhado)
  channel_policy      jsonb       -- ordem de canal, possessed-only?, restrições por canal
  contact_calendar_id text null   -- (d) janela de contato via calendar-api (is_open)
  contact_policy_id   uuid null   -- (c) governança agnóstica; null = herda default do tenant
  transactional       bool        -- se true, pode furar opt-out (notificação legal/obrigatória)
  batch_size          int         -- teto de entradas drenadas por execução
  pacing              jsonb        -- (d) {max_concurrent, rate}; drena ≤ capacidade disponível do pool
  retry               jsonb        -- (b) {max_attempts, backoff}
  agenda_id           uuid null    -- cadência: a Agenda (scheduler) que dispara este pool c/ payload {campaign_id}
  status              text         -- 'active' | 'paused' | 'completed' | 'archived'
  created_at, updated_at

outbound.campaign_deliveries      -- estado POR-CAMPANHA por entrada (suppression + claim + drill)
  id               uuid pk
  campaign_id      uuid fk
  mailing_entry_id uuid fk
  tenant_id        text
  claimed_at       timestamptz  -- claim atômico (FOR UPDATE SKIP LOCKED) — evita drain duplo
  contacted_at     null
  session_id       text null    -- sessão outbound criada (drill-through)
  root_session_id  text null
  result           text         -- 'pending'|'contacted'|'responded'|'failed'|'skipped_ineligible'|'suppressed'
  attempts         int default 0
  UNIQUE (campaign_id, mailing_entry_id)  -- 1 entrega por campanha por entrada (idempotência)

-- ── Governança de contato (agnóstica; survey é só um chamador) ──────────────
outbound.contact_log              -- FATO (universal): todo contato outbound sai daqui
  id, tenant_id, customer_id, channel, campaign_id, contacted_at, result
  -- índices (customer_id, contacted_at) e (customer_id, channel, contacted_at) p/ os caps

outbound.contact_policy           -- REGRA (em camadas): default do tenant + override por campanha
  id, tenant_id
  scope            text     -- 'tenant' | 'campaign'
  scope_id         text null -- null (global) | campaign_id
  frequency_caps   jsonb    -- [{window:'24h', max:1, per_channel:true}, {window:'7d', max:3}]
  quarantine_after interval -- não recontatar por X após um contato
  channel_caps     jsonb    -- limites por canal
  -- opt-out GLOBAL vive no CADASTRO DO CLIENTE (do_not_contact por canal/total), não aqui.
  -- sair de UMA lista = entry.status='unsubscribed'; opt-out de verdade = atributo do cliente.
```

---

## 4. Contratos de tool (MCP)

```
mailing_add(mailing_id, customer_id, contacts?, metadata, dedup_key?, ttl_seconds?)
    -> { entry_id, deduped }
    # produtores: (1) skill em hook de lifecycle; (2) importador de arquivo (anti-corrupção).
    # upsert por (mailing_id, dedup_key); auditado via McpInterceptor.

mailing_unsubscribe(customer_id | anchor, channel?='all')
    # supressão. mailing-scoped -> entry.status='unsubscribed'; global -> atributo do cliente (do_not_contact).

contact_eligibility_check(customer_id, channel, campaign_id, at=now)
    -> { allowed, reason, retry_after? }
    # motor AGNÓSTICO: consulta cadastro (opt-out/preferência) + contact_log×policy (fadiga) + calendar (janela).
    # aplica cada regra só se configurada. **survey_eligibility_check NÃO existe** (decisão 2026-07-21):
    # o survey chama contact_eligibility_check DIRETO — o genérico SUBSTITUI, não é wrapper.

# REUSO (já existe — grupo `operational`): pool_status_get / queue_context_get / system_availability_check
#   -> { available, queue_length, sla_target_ms, ... } do snapshot do routing-engine.
#   Mesma chamada serve "iniciar contato?" (capacidade) e "posição na fila" pro cliente em espera.
```

---

## 5. Drenagem (drain) + back-pressure

O skill outbound (acionado pela agenda) drena um lote e claima:

```sql
SELECT e.* FROM outbound.mailing_entries e
WHERE e.mailing_id = :campaign_mailing AND e.tenant_id = :t
  AND e.status = 'active'
  AND (e.expires_at IS NULL OR e.expires_at > now())
  AND (:selection over e.metadata)                    -- fatia da campanha
  AND NOT EXISTS (SELECT 1 FROM outbound.campaign_deliveries d
                  WHERE d.campaign_id = :camp AND d.mailing_entry_id = e.id
                    AND (d.result <> 'failed' OR d.attempts >= :max_attempts))  -- (b) retry por campanha
ORDER BY e.added_at
LIMIT LEAST(:batch_size, :available_capacity)         -- (d) pacing pela capacidade do pool
FOR UPDATE SKIP LOCKED;
```

**A agenda recorrente É o mecanismo de pacing:** cada tick drena só o que a capacidade do pool permite
(`available_capacity` de `pool_status_get`); o resto espera o próximo tick. Sem escalonador de recursos separado.

---

## 6. Pipeline de portões (a decisão de contatar) — (c) + (d)

Cada portão reusa um subsistema, aplicado **só quando configurado**. Portões baratos/set-based rodam no SQL do
drain; os dependentes de tempo/contexto rodam **por-entrada no skill** (via `contact_eligibility_check`) no envio.

| Restrição | Portão | Reuso | Onde roda |
|---|---|---|---|
| Período de execução | campanha ativa **e** dentro da vigência | `agenda.validity` (scheduler) | drain |
| Períodos de contato | "agora é hora permitida?" | **calendar-api** `is_open` sobre `contact_calendar_id` | envio |
| Canal | qual canal + ordem + fallback | `channel_policy` + `entry.contacts` + resolvedor (`possessed`) | envio |
| Recursos disponíveis | não floodar | **operational** `pool_status_get` (`available`/`queue_length`) | drain+envio |
| Eligibility/quarentena | frequência/fadiga | motor `contact_eligibility_check` (contact_log×policy) | envio |
| Preferência do contatado | canal/janela preferidos, do-not-contact | **cadastro de cliente** (resolvedor Fase B) | envio |

**Precedência (decisão fechada):** supressão dura (opt-out/unsubscribe) veta absoluto (salvo `campaign.transactional`);
depois a **regra de campanha** vence; a **preferência soft** só reordena/influencia dentro do que a campanha permite.

---

## 7. Como o Survey pluga (consumidor, não dono)

Survey tem **política própria** (form DialogForm, `grain`, janela de quarentena, instrumento CSAT/NPS/…).

- **Inline** → hook `on_contact_end` dispara direto (caminho NPS que já existe), **sem mailing**.
- **Outbound** → (1) um skill em hook de lifecycle chama `mailing_add` com
  `metadata = {grain, grain_instance_id, origin_session_id, outcome, verbatim?, survey_form_id}`; (2) uma campanha
  referencia esse mailing + pool outbound + agenda; (3) o scheduler dispara o pool, o skill drena, monta o
  briefing **do `entry.metadata`**, passa pelos portões, contata (collect/pull), roda o form, `survey_record` →
  `session_signal` no `metadata.grain`, marca `delivery.responded` + grava `contact_log`.
- **`journey_complete` que faltava:** o próprio skill do processo chama `mailing_add` no seu passo `complete` —
  a journey se auto-reporta "terminei", sem evento novo no core.

A janela de quarentena do survey é um `contact_policy` no escopo daquela campanha — não um campo especial.
O motor de elegibilidade é **um só e genérico**: **não haverá `survey_eligibility_check`** (decisão 2026-07-21).
Quando o módulo de survey for implementado (após o outbound), ele consome `contact_eligibility_check` direto; a
"quarentena por cliente × tipo de instrumento" (`customer-surveys.md` §6) vira uma `contact_policy` com `scope`
por-campanha/tipo — o ledger de fadiga passa a ser o `contact_log` genérico, não um `survey_quarantine` próprio.

---

## 8. Plataforma / reuso

- Schema PG `outbound`; serviço `mailing-api` (CRUD mailings/campaigns + drain + importador), padrão
  `scheduler-api`/`dialog-api`. Tools MCP `mailing_add`/`mailing_unsubscribe`/`contact_eligibility_check`.
- **Reusa:** scheduler (cadência + back-pressure) · calendar-api (janela de contato) · resolvedor de identidade
  (customer + anchors→contacts + preferência/opt-out) · collect/pull (entrega) · session_signal (resposta) ·
  Pool lifecycle hooks (alimentação) · Operational Visibility `pool_status_get` (capacidade + fila).
- **Importador** = adaptador anti-corrupção (lê CSV/xlsx/origem → normaliza → `mailing_add`), padrão quality-ingest.

---

## 9. Decisões — FECHADAS (2026-07-21)

- **(a) Import sem customer resolvido:** o importador **tenta resolver**; falhou → `customer_id=null` com contato
  cru; resolução retentável depois.
- **(b) Retry:** por campanha (`campaign.retry {max_attempts, backoff}` + `delivery.attempts`).
- **(c) Eligibility/quarentena agnóstica:** motor de contato genérico (fato `contact_log` × regra `contact_policy`
  × decisão `contact_eligibility_check`); survey é um chamador com janela própria. Fadiga é cross-campanha.
- **(e) `contact_eligibility_check` SUBSTITUI `survey_eligibility_check` (2026-07-21):** o motor de elegibilidade é
  único e genérico; **não haverá tool/ledger de elegibilidade específico de survey**. O survey (a implementar
  **depois** do outbound) é um chamador do motor genérico — sua quarentena por tipo de instrumento vira
  `contact_policy` de escopo próprio, e o estado de fadiga vive no `contact_log`, não num `survey_quarantine`.
  Reconciliação a executar na **Fase 2** (governança de contato). Sequência fixada: **outbound primeiro, survey
  depois.**
- **(d) Restrições da campanha = pipeline de portões**, cada um reuso, cada um "aplica se configurado":
  período (agenda), janela de contato (calendar), canal (channel_policy+resolver), recursos (`pool_status_get`),
  fadiga (motor c), preferência (cadastro). **Precedência:** opt-out > regra de campanha > preferência soft.
- **(2) Fonte de capacidade:** `pool_status_get` (snapshot do routing-engine, 120s) + back-pressure da agenda;
  query ao vivo no routing = refino. A chamada é reutilizável (posição de fila ao cliente em espera).

**Residuais (decidir na implementação):** dedup_key exato por `dedup_policy`; claim quando 2 campanhas drenam a
mesma entrada de um mailing compartilhado (entrada dona de 1 campanha vs claim per-campanha); shape do
`metadata_contract` (versionado); política de limpeza/expiração de entradas (TTL vs retenção externa).

---

## 10. Fases sugeridas (incremental)

1. **Substrato mínimo:** schema `outbound` + `mailing-api` (CRUD + `mailing_add` + drain) + pool/skill outbound
   demo que drena e faz `collect` a um alvo lido de `entry.metadata`. Agenda dispara. Prova a abstração (diff
   zero no scheduler).
2. **Governança de contato:** `contact_log` + `contact_policy` + `contact_eligibility_check` (portões c).
3. **Portões (d):** calendar (janela), `pool_status_get` (capacidade/pacing), preferência do cadastro.
4. **Importador** de arquivos (anti-corrupção).
5. **Survey outbound de ponta a ponta:** skill de hook alimenta o mailing (inclui `journey_complete` no
   `complete` do processo); campanha + agenda; `survey_record`→`session_signal`. Liga ao Customer Surveys §19.
