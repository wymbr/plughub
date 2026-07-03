# Resolvedor de Identidade — Fase A · Plano de Implementação

> **Companion de** `identity-resolver-nivel-b-spec.md` (spec travada) e `delegate-contrato-por-pool-spec.md`.
> **Objetivo da Fase A (§12 da spec):** cadastro mínimo interno de cliente (sem CRM) + duas buscas +
> `delegate` resolve/provisiona `customer_id` e registra pendência sob o cliente + retomada cross-canal.
> **Motivação de curto prazo:** corrigir o erro `contact_id`-como-`customer_id` e dar uma identidade estável
> sobre a qual o histórico/busca (arco H) e a retomada de workflow passam a valer.
> **Status:** plano aprovado (2026-07-02). Fatiado para revisão incremental.

---

## 0. Decisões travadas (aprovadas)

1. **Co-localizar o módulo `identity/` no channel-gateway** na Fase A (reusa o router webhook, Redis, PG e o
   `_open_child_session` do prior art), desenhado como **módulo coeso** para poder virar serviço próprio
   depois. Não criar serviço novo agora.
2. **Redis-only primeiro** (Slice 1) — destrava a retomada cross-canal demoável sem schema; PG durável vem
   no Slice 2.
3. **Grau "origem/fraca" no v1** — a validação de âncora forte (`identity_verify`) é da retaguarda do tenant
   via MCP de domínio, que não existe no demo. A Fase A trata a âncora como origem (deriva/provisiona
   `customer_id`, sem verificação forte). `identity_verify` real fica para quando houver MCP de domínio.

## 1. Prior art (reuso — não é greenfield)

| Peça | Onde | Papel na Fase A |
|---|---|---|
| `workflow_resume(resume_token)` + `POST /v1/channels/webhook/resume/{token}` + `{t}:resume_tokens` | mcp-server-plughub `tools/workflow.ts`; channel-gateway `adapters/webhook.py` | **Inalterado** — destravamento de (a). |
| `_open_child_session` (delegate I/O) | `webhook.py` | Estendido: resolve/provisiona `customer_id`; grava pendência sob o cliente (dual-write com a chave legada). |
| `{t}:pending_workflow:{contact_id}` (claro, 1/handle) + `get_pending_workflow` | `webhook.py` | **Semente** de Lookup 2; generalizado para `{t}:pending_by_customer:{customer_id}` (HASH). |
| `pending_workflow_get(contact_identifier)` | `tools/workflow.ts` | Estendida com `anchors[]` (mantém `contact_identifier` legado). |
| `{t}:insight:h:{customer_id}:*` | insights | Confirma `customer_id` como identidade cross-contato. |
| `agente_portabilidade_intake_v1` | skills | Fluxo de intake demo que já chama `pending_workflow_get`. |

## 2. Fatiamento

### Slice 1 — Índice + pendência por cliente (Redis-only) ✅ (2026-07-02) ← DETALHADO em §3
> **Concluído e validado** (16 unit tests + smoke `test_identity_resolver_slice1.sh`, verde). Correção de
> invariante aplicada: **salt é segredo → env** (`PLUGHUB_IDENTITY_SALT`), namespace `identity` do config-api
> guarda só tuning (TTLs, `system_trust`). Limitação registrada: identidade progressiva (anexar âncora nova a
> cliente existente em match parcial) fica para a Fase B.
Helpers normalização+hash; Lookup 1 (`{t}:identity:{kind}:{hash}`→`customer_id`); Lookup 2
(`{t}:pending_by_customer:{customer_id}` HASH); prospect efêmero; namespace Config `identity`;
`_open_child_session` dual-write; tools `customer_resolve` (nova) + `pending_workflow_get(anchors)`;
HTTP `POST …/identity/resolve` + `GET …/pending/by-customer/{id}` (legado → wrapper).
**Entrega:** resolver âncora→`customer_id`; reconectar por outra âncora do mesmo cliente acha a pendência.
Retomada cross-canal demoável só com Redis.

### Slice 2 — Durabilidade (schema PG `identity`) + promoção + fallback ✅ (2026-07-02)
Schema `identity` (`customers`, `customer_secondary_keys`, `customer_external_refs`, `customer_merges`),
separado de `auth`. Promoção efêmero→PG por gatilho concreto (id estável reusado). Lookup 1: miss Redis→PG.
Regra de migração: raw asyncpg `CREATE ... IF NOT EXISTS` (não Prisma) — **nunca** `db push --accept-data-loss`.
> **Concluído e validado** (19 unit tests + smoke `test_identity_resolver_slice2.sh`, verde). Reusa o pool
> asyncpg que o channel-gateway já cria (attachments); `ensure_schema` no startup; promoção disparada no
> `write_pending`. Fallback devolve `matched_by="durable"` e reidrata o índice Redis. `external_refs`/`merges`
> criados (DDL) mas populados só na Fase B.

### Slice 3 — Campos no `delegate` + política + `session_resumed`
`customer_resumable?`/`resume_policy?` no `delegate` (e `collect`) em `schemas/src/skill.ts`; propagação pelo
engine até o callback `persistDelegate` (**risco: verificar propagação de campos novos**); `offer`/`auto`;
`session_resumed` com `resume_origin: same_channel|token|identity`; guardrail de perfil.
`task.target {skill_id}→{pool}` (delegate-spec §7) é decisão relacionada mas **fora** da Fase A.

### Slice 4 — Ponte de volta ao histórico (`caller.customer_id = nativo`) ✅ (2026-07-02)
Formalmente Fase B (§13.8-5), puxado para o fim da Fase A: o `customer_id` da sessão (analytics) passa a
refletir o **nativo** → conserta o erro `contact_id` e reconecta H1/H2/H3.
> **Concluído.** Backend: o **bridge** (`_close_contact_layer`) usa `_resolve_close_customer_id` para ler
> `caller.customer_id` (nativo) do ContextStore e **sobrescrever** o `customer_id` na linha de fechamento
> autoritativa (`sessions.customer_id`), fallback `contact_id`. Frontend: `AgentAssistPage` chaveia a
> `HistoricoTab` pelo `caller.customer_id` do snapshot (fallback `contactId`). Validado por 6 unit tests do
> helper (`test_close_customer_id.py`). **Escopo:** a plataforma **propaga** o nativo quando resolvido; quem
> **escreve** `caller.customer_id` (intake chamando `customer_resolve`, ou o step CRM `resolve` gravando o
> nativo em vez do id de CRM) é wiring de fluxo — Fase B. Sem CRM no demo, a validação end-to-end no browser
> depende desse wiring.

**Fase A completa** (Slices 1–4). **Wiring do intake ✅ (2026-07-03):** `agente_portabilidade_intake_v1`
chama `customer_resolve` (âncoras `numero_atual`+`contact_identifier`) e grava `caller.customer_id` nativo
via `context_set` antes da ramificação de pendência — o Slice 4 agora tem o nativo para propagar. Validado
no demo (webchat): dois intakes com o mesmo número fecham sob o mesmo `cus_…`. Nota de deploy: pool migrado
a slot exigiu `set-next`+`promote` (edição de YAML+restart não re-snapshota o `current`). Falta a Fase B
(identidade progressiva, `external_refs`, merge, e o wiring do step CRM `resolve` quando houver retaguarda).

## 3. Slice 1 — detalhamento

### 3.1 Chaves Redis

```
{t}:identity:{kind}:{value_hash}     → customer_id        kind ∈ phone|email|cpf|princ|dev
{t}:identity:ext:{system}:{ext_id}   → customer_id        (cross-ref externo; fase B usa mais)
{t}:customer:prospect:{customer_id}  → JSON {status, created_at, secondary_kinds[]}   (TTL deslizante)
{t}:pending_by_customer:{customer_id}→ HASH field={session_id} value=PendingEntry     (TTL = max pendências)

value_hash = hex(sha256(tenant_salt + valor_normalizado))
tenant_salt ← Config API {t}:config:identity:salt   (Slice 1 lê; ausência → salt default por env, avisando)
```

Normalização: phone→E.164 (dígitos + `+`); email→trim+lowercase; cpf→só dígitos; princ→`sub` do JWT.

### 3.2 Módulo `identity/` (channel-gateway)

`packages/channel-gateway/src/plughub_channel_gateway/identity/` — coeso, movível:
- `normalize.py` — normalização por `kind` + `hash_anchor(tenant_salt, kind, value)`.
- `index.py` — `IdentityIndex(redis, config_client)`:
  - `resolve_or_provision(tenant_id, anchors, provision=True) -> CustomerRef{customer_id,status,matched_by,confidence}`
    Lookup 1: hasheia âncoras → GET índice. 0 e `provision` → cria prospect efêmero (uuid `cus_…`) + indexa as âncoras. >1 candidato → desambiguação por confiança (`princ`/`ext` > `cpf`/`email` > `phone`); colisão real (mesma confiança, ids diferentes) → `matched_by="ambiguous"` (o fluxo decide `ask`).
  - `write_pending(tenant_id, customer_id, entry, ttl_s)` — HSET + EXPIRE.
  - `find_pending(tenant_id, customer_id) -> list[PendingEntry]` — HGETALL; limpa entradas cujo `resume_token` não está mais em `{t}:resume_tokens` (stale).
  - `consume_pending(tenant_id, customer_id, session_id)` — HDEL idempotente.

`CustomerRef.matched_by ∈ {existing, provisioned, ambiguous}`.

### 3.3 `_open_child_session` (dual-write, atrás de flag `IDENTITY_RESOLVER_ENABLED`)

Após o bloco atual que escreve `{t}:pending_workflow:{contact_id}`:
1. Montar `anchors` a partir do `context` (`contact_identifier` + quaisquer `phone/email/cpf` presentes).
2. `ref = IdentityIndex.resolve_or_provision(tenant, anchors, provision=True)`.
3. `write_pending(tenant, ref.customer_id, PendingEntry{session_id=origin_session_id, resume_token, pool, skill_id, suspended_at, expires_at, policy, intent?, context_preview?})`.
A chave legada continua sendo escrita (compat) até validação (Slice 2/migração desliga).

### 3.4 HTTP (channel-gateway, router webhook)

```
NOVO  POST /v1/channels/webhook/identity/resolve
  body { tenant_id, anchors:[{kind,value}], provision? }   # PII só no loopback; hash server-side
  resp { customer_id, status, matched_by, confidence }

NOVO  GET  /v1/channels/webhook/pending/by-customer/{customer_id}?tenant_id=…
  resp { found, count, pendings:[PendingEntry(sem PII crua)] }

LEGADO (mantido, vira wrapper) GET /v1/channels/webhook/pending/{contact_identifier}?tenant_id=…
  → resolve_or_provision([âncora inferida], provision=False) → find_pending → 1º pendente (forma antiga).
```

### 3.5 MCP tools (mcp-server-plughub `tools/workflow.ts`)

```
customer_resolve (NOVA) { tenant_id, anchors:[{kind,value}], provision? }
  → { customer_id, status, matched_by, confidence }     # POST …/identity/resolve

pending_workflow_get (ESTENDIDA, backward-compatible)
  { tenant_id, anchors?:[{kind,value}], contact_identifier?, provision? }
  → { customer_id, found, count, pendings:[…] }          # anchors → by-customer; contact_identifier → wrapper legado

workflow_resume — INALTERADA.
```

### 3.6 Config API — namespace `identity` (seed-if-absent)

`salt` (obrigatório; seed gera 1 por tenant), `prospect_ttl_s` (default 30d deslizante),
`resolution_index_ttl_s` (default 30d), `system_trust` (default `{}`). Seed em `config-api/seed.py`.

### 3.7 Testes

- **Unit (Python, channel-gateway):** normalização por kind; hash determinístico + salt; `resolve_or_provision`
  (0→prospect+index; 1→existing; >1→confiança/ambiguous); write/find/consume (stale cleanup via resume_tokens).
- **Unit (TS, mcp-server):** `customer_resolve` e `pending_workflow_get(anchors)` montam o request certo;
  `contact_identifier` legado ainda funciona.
- **Smoke host** `infra/test/test_identity_resolver_slice1.sh`: seed salt no config-api; POST resolve com
  âncora nova → `provisioned`+`customer_id`; POST resolve **outra âncora** declarando o mesmo cliente
  (via 2ª âncora no mesmo resolve) → mesmo `customer_id`; simular pendência (delegate/`_open_child_session`
  ou HSET direto) → GET by-customer acha; reconectar por âncora diferente que resolve ao mesmo cliente → acha
  a pendência (prova cross-canal); inspeção: nenhuma chave `{t}:identity:*` contém PII em claro.

### 3.8 Riscos do Slice 1

- **Desambiguação/colisão** de Lookup 1 — v1: confiança fixa por kind + `ambiguous` devolvido ao fluxo.
- **Salt/rotação** — rotação invalida o índice; Slice 1 só lê; rebuild fica para PG (Slice 2).
- **Dual-write/idempotência** — pendência sob cliente + legada ao mesmo tempo; consume idempotente.
- **Anchor a partir do `context`** — hoje `_open_child_session` só tinha `contact_identifier`; mapear
  phone/email/cpf do context sem quebrar quem passa só o identifier.

## 4. Fora de escopo (Fase A)
`identity_verify` real (retaguarda), merge de clientes (Slice C da spec), device id anônimo (fase D),
`task.target→pool`, e a UI de qualquer disso. `workflow_resume` não muda.
