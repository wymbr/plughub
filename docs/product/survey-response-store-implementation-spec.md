# Survey Response Store — Implementation Spec (contrato do endpoint + wiring)

**Status:** ✅ Implementado e validado (2026-07-23) — 4 frentes + `smoke_survey_response_store.sh` verde
(verbatim capturado, persist-first, idempotência 201→200). Ver CHANGELOG. Aberto: endpoint de LEITURA de S8
+ áudio/transcript (S9).
**Decisão-mãe:** [`docs/adr/adr-survey-response-store.md`](../adr/adr-survey-response-store.md) (Aceito) —
Opção A: schema PG `survey` dedicado na **evaluation-api**, escopo mínimo `survey_instance`+`survey_response`.
**Resolve:** item aberto #3 do ADR (contrato do endpoint, idempotência, ordem persist↔emit).

> Todos os pontos de código abaixo foram verificados contra a base em 2026-07-23. Números de linha são
> âncoras de partida — reconferir no checkout.

---

## 1. Escopo

Construir a **linha operacional por-resposta** que S8 (navegador) e S9 (analista de verbatim) exigem e que
hoje **não existe** (verbatim é descartado). Três peças:

1. Schema PG `survey` na evaluation-api (2 tabelas) + `ensure_schema` no boot.
2. Endpoint `POST /v1/evaluation/survey/responses` (persiste instância + resposta, idempotente).
3. Wiring nos **dois** produtores para persistir **antes** de emitir `session.signals`:
   `survey_record` (mcp-server) e `survey_web.submit` (channel-gateway) — este último **deixa de descartar**
   o texto aberto.

`session_signal` (ClickHouse) **não muda** — segue sendo só a projeção analítica numérica. Verbatim/áudio
**nunca** vão para o ledger analítico.

---

## 2. DDL — schema `survey` (evaluation-api)

Segue a convenção da evaluation-api: **DDL aditivo idempotente** num módulo próprio, aplicado no boot (espelha
`ensure_schema(pool)` / `_DDL` em `packages/evaluation-api/src/plughub_evaluation_api/db.py:624` + chamada em
`main.startup()`). IDs TEXT com prefixo (`_new_id(prefix)`); JSONB via `$k::jsonb` + `json.dumps`.

```sql
CREATE SCHEMA IF NOT EXISTS survey;

-- Uma ocorrência de pesquisa (chave de religação + atribuição + escopo LGPD)
CREATE TABLE IF NOT EXISTS survey.survey_instance (
  instance_id        TEXT PRIMARY KEY,            -- svi_<uuid4hex>
  tenant_id          TEXT NOT NULL,
  idempotency_key    TEXT NOT NULL,               -- web = token; record = hash composto (§4)
  survey_id          TEXT,                        -- = form_id do DialogForm (pode ser '')
  origin_session_id  TEXT,                        -- a sessão/contato pesquisado
  grain              TEXT NOT NULL,               -- session | segment | workflow | journey
  segment_id         TEXT,                        -- quando grain=segment
  agent_key          TEXT,                        -- atribuição (user_id | flow_id)
  pool_id            TEXT,
  customer_key       TEXT,                        -- JOIN com a base de cliente (§7.3)
  channel            TEXT,
  survey_session_id  TEXT,                        -- sessão-filho de survey (Arc 19), se houver
  status             TEXT NOT NULL DEFAULT 'responded',  -- pending|sent|responded|expired|skipped
  session_at         TIMESTAMPTZ,                 -- fallback = captured_at
  responded_at       TIMESTAMPTZ,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_survey_instance_idem UNIQUE (tenant_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_survey_instance_customer
  ON survey.survey_instance (tenant_id, customer_key);
CREATE INDEX IF NOT EXISTS ix_survey_instance_origin
  ON survey.survey_instance (tenant_id, origin_session_id);

-- A resposta (fonte operacional da verdade; 1:1 com instance no v1)
CREATE TABLE IF NOT EXISTS survey.survey_response (
  response_id      TEXT PRIMARY KEY,              -- svr_<uuid4hex>
  instance_id      TEXT NOT NULL
                     REFERENCES survey.survey_instance(instance_id) ON DELETE CASCADE,
  tenant_id        TEXT NOT NULL,                 -- denormalizado p/ scoping de leitura
  signals          JSONB NOT NULL DEFAULT '[]',   -- espelho numérico: [{metric,value,value_label?}]
  open_text        TEXT,                          -- VERBATIM (LGPD: acesso controlado)
  verbatims        JSONB NOT NULL DEFAULT '[]',   -- [{question_id, text}] — texto aberto por-pergunta
  audio_ref        TEXT,                          -- forward-looking: artefato do attachment_store
  transcript_ref   TEXT,                          -- forward-looking: transcrição STT
  response_channel TEXT,
  responded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_survey_response_instance UNIQUE (instance_id)  -- idempotência 1:1
);
CREATE INDEX IF NOT EXISTS ix_survey_response_tenant
  ON survey.survey_response (tenant_id, responded_at);
```

**Notas de modelagem**
- `signals` guarda uma **cópia denormalizada** do que virou `session_signal` — o navegador (S8) lê a resposta
  inteira do PG sem cruzar o ClickHouse.
- `open_text` + `verbatims`: `open_text` para o caso simples (1 campo aberto); `verbatims[]` quando há N
  perguntas abertas. Ambos LGPD-controlados na leitura.
- **Precedente de tabela-filha de resposta:** `evaluation.criterion_responses` (db.py:179 DDL, `create_criterion_responses`
  db.py:1717) — mesmo padrão de FK+CASCADE, colunas de valor, `text_value` para aberto.

---

## 3. Endpoint

```
POST /v1/evaluation/survey/responses
```

- **Prefixo** `/v1/evaluation/...` (convenção da evaluation-api; o schema PG `survey` é ortogonal à URL).
- **Auth:** `_require_service` (header `X-Service-Token`) — ambos os chamadores são backend/agente
  (mcp-server, channel-gateway), mesma postura do `POST /v1/evaluation/ingest` (router.py:1326). Token vazio =
  no-op no demo (convenção existente). Se um dia a UI postar, trocar por `_require_service_or_eval_write`.
- **Tenant:** no **corpo** (`tenant_id`), convenção da evaluation-api (não header — o `x-tenant-id` que o
  cliente MCP manda é ignorado aqui; o corpo é canônico).
- **201** na criação; **200** em replay idempotente (`created:false`).

### Request

```jsonc
{
  "tenant_id": "tnt_demo",              // obrigatório (corpo é canônico)
  "idempotency_key": "…",               // obrigatório — web=token; record=hash composto (§4)
  "survey_id": "dialog_nps_buttons",    // form_id do DialogForm (ou "")
  "origin_session_id": "…",
  "grain": "session",                   // session|segment|workflow|journey
  "segment_id": null,
  "agent_key": "",
  "pool_id": "",
  "customer_key": "",                   // nullable; §7.3
  "channel": "web",
  "survey_session_id": null,
  "session_at": null,                   // opcional; fallback = captured_at
  "signals": [ { "metric": "nps", "value": 9, "value_label": "promotor" } ],
  "open_text": null,
  "verbatims": [ { "question_id": "q_motivo", "text": "atendimento rápido" } ],
  "audio_ref": null,
  "response_channel": "web",
  "captured_at": "2026-07-23T12:00:00Z"
}
```

### Response

```jsonc
// 201 (criado) | 200 (replay idempotente)
{ "instance_id": "svi_ab12…", "response_id": "svr_cd34…", "created": true }
```

### Semântica

1. **Upsert da instância** por `(tenant_id, idempotency_key)`: `INSERT … ON CONFLICT (tenant_id,
   idempotency_key) DO NOTHING`; se conflitou, `SELECT` a existente. (Espelha o `ON CONFLICT DO NOTHING` dos
   snapshots imutáveis — `publish_form` db.py:857.)
2. **Insert da resposta** por `instance_id`: `INSERT … ON CONFLICT (instance_id) DO NOTHING`.
3. `created = true` só se a resposta foi de fato inserida; senão `created:false` + 200.
4. Tudo numa **transação** (`async with conn.transaction()`), padrão asyncpg `$1..$n`, JSONB `$k::jsonb`.
5. `session_at` ausente → usa `captured_at`. `status` default `responded` (v1 é captura imediata; o estado
   `pending/sent` fica para quando o outbound criar a instância antes da resposta).

**Idempotência é não-fatal por design:** replay retorna 200, nunca 409 — porque o chamador persiste **antes**
de emitir o Kafka, e um retry após falha parcial não pode quebrar. (Diferente do 409 do rubric create, que é
uma escrita de usuário.)

---

## 4. Chave de idempotência

O que torna uma resposta única depende do veículo:

| Veículo | `idempotency_key` | Razão |
|---|---|---|
| **survey_web.submit** | o **token** (`survey_web:token:{token}`, já single-use) | 1 token = 1 submissão; anti-replay natural |
| **survey_record** (conferência/inline) | hash de `(tenant_id, origin_session_id, grain, segment_id\|'', survey_id\|'', captured_bucket)` | não há token; a chave composta captura "uma pesquisa por (sessão×grão×instrumento)" |

`captured_bucket` = `captured_at` truncado (ex.: ao minuto) só para tolerar retry imediato sem colar duas
pesquisas legítimas distantes no tempo. Definir a granularidade no corte (sugestão: minuto).

---

## 5. Wiring — Produtor 1: `survey_record` (mcp-server)

Arquivo `packages/mcp-server-plughub/src/tools/survey.ts`.

- **Ponto de inserção:** imediatamente **antes** de `await kafka.publish("session.signals", event)`
  (survey.ts:**329**), depois de `finalSignals` resolvido (288–310). Persistir → se falhar, `return mcpError(…)`
  **sem** emitir (espelha o catch `publish_failed` em 335–341).
- **Cliente HTTP:** mirror do `contact_eligibility_check` (tools/outbound.ts:264) via o helper `postJson(url,
  tenantId, body)` (outbound.ts:118) — `fetch` POST, header `x-tenant-id`, `!ok → mcpError`, throw →
  `mcpError("network_error", …)` (o `isError` roteia o `invoke` para `on_failure`).
- **URL:** adicionar `evaluationApiUrl` ao `SurveyDeps` (survey.ts:40) e wirar em `server.ts` (`surveyDeps`,
  ~137–142) como `process.env["EVALUATION_API_URL"] ?? "http://localhost:3400"` (o env `EVALUATION_API_URL` já
  é o estabelecido — usado em tools/evaluation.ts:1424).
- **Campos:** o handler tem `origin_session_id, grain, segment_id, agent_key, pool_id, signals[], form_id,
  tenant_id, survey_session_id, captured_at`. **Faltam** `customer_key` e `session_at` no
  `SurveyRecordInputSchema` — no v1 vão como `null`/`""` (nullable no schema). Se o join por cliente por esse
  veículo virar requisito, adicionar `customer_key` opcional ao schema depois (fora deste corte).
- **`idempotency_key`:** o handler computa o hash composto (§4).

---

## 6. Wiring — Produtor 2: `survey_web.submit` (channel-gateway)

Arquivo `packages/channel-gateway/src/plughub_channel_gateway/survey_web.py`.

- **Captura do verbatim (o fix central):** hoje o loop 590–603 faz `float(val)` e o `except: continue`
  (**601–602**) **descarta** a resposta não-numérica ("open_text → verbatim, não signal"). Trocar o `continue`
  por **acumular** `{question_id, text}` numa lista `verbatims` (e/ou `open_text` quando houver um único campo
  aberto). Os `signals` numéricos seguem como hoje.
- **Ponto de inserção:** persistir na evaluation-api **antes** de `await self._producer.send(...)`
  (survey_web.py:**618**). Falha na persistência → não emitir + erro na submissão (o token continua `open`, o
  cliente pode reenviar; ver idempotência).
- **`idempotency_key` = token** (já single-use, guard `already_submitted` em 585–586).
- **Cliente HTTP:** `httpx` já está importado e em uso (fetch do dialog-api em `create`, 545–552). **Nova
  config:** adicionar `evaluation_api_url` (+ `evaluation_service_token` opcional) em `config.py`, threa-lo no
  `SurveyWebService.__init__` (495) via `main.py` (131–138); header `X-Service-Token`. Mirror do POST do
  dialog-api já existente.
- **Campos ausentes:** `pool_id`, `segment_id`, `agent_key`, `survey_session_id` **não** estão no registro
  Redis (hoje emitidos como `""`/`None`). Vão `null` para o store. Se S8 precisar de atribuição por esse
  veículo, adicioná-los ao registro no `create` (survey_web.py:555) — trabalho separado.
- **Verbatim NÃO vai para `session.signals`** — só numérico continua no evento Kafka; o texto aberto vai só
  para o `survey_response` (operacional/LGPD).

---

## 7. Ordem persist↔emit (invariante)

Nos **dois** produtores: **persistir no PG primeiro, emitir `session.signals` depois** — igual ao
`_ingest_core` (router.py:1440: `create_result` → … → Kafka). Justificativa: se a emissão numérica ao
analytics vazasse antes de a resposta operacional existir, o navegador (S8) veria um sinal agregado sem a
resposta correspondente (o mesmo "dado plausível esconde bug" da postura de engenharia). Persist-first + replay
idempotente não-fatal fecha isso.

---

## 8. LGPD

- `open_text`/`verbatims`/`audio_ref` são o dado **controlado**: a **leitura** (endpoint de S8, a definir junto
  com a UI) exige o campo ABAC de auditoria/qualidade apropriado; **nunca** replicados em massa ao ClickHouse.
- Retenção do artefato de áudio (quando S9 wirar) herda a política de uploads do Channel Gateway
  (`attachment_store` / `session_attachments`), por-tenant.

---

## 9. Follow-ups (não bloqueiam o 1º corte)

1. **Endpoint de leitura de S8** (`GET /v1/evaluation/survey/responses?…` por tipo/pool/período + detalhe com
   verbatim) — desenhar com a UI `/analise/surveys`. Auth de leitura = ABAC (não service token).
2. **Áudio/transcript**: wiring `audio_ref`→`attachment_store` + STT — trabalho de S9.
3. **`customer_key` no `survey_record`**: adicionar ao `SurveyRecordInputSchema` se o join por cliente pela
   conferência virar requisito.
4. **Estado `pending/sent`**: quando o outbound criar a instância antes da resposta (survey diferido), o
   endpoint ganha um modo "criar instância sem resposta"; hoje v1 é captura imediata (`responded`).
5. **Smoke** `infra/test/smoke_survey_response_store.sh`: cria form → survey web token → submit com
   `{nps:9, motivo:"texto"}` → assert `survey_response` gravado com `verbatims` **e** `session_signal(nps)`
   presente; reenvio do mesmo token → `created:false`, sem linha duplicada.

---

## 10. Checklist de build (ordem sugerida)

1. `survey` schema DDL + `ensure_schema` no boot da evaluation-api (+ funções `upsert_survey_instance` /
   `insert_survey_response`).
2. Endpoint `POST /v1/evaluation/survey/responses` (`_require_service`, tenant no corpo, 201/200, transação).
3. `survey_record`: `evaluationApiUrl` em `SurveyDeps` + persist-before-emit + hash de idempotência.
4. `survey_web.submit`: capturar verbatim (fix do `continue`) + persist-before-emit + config
   `evaluation_api_url`.
5. Smoke §9.5.
6. **Build:** evaluation-api (Python) e mcp-server (TS) **bakeiam** o source → `build <svc> && up -d
   --force-recreate <svc>`; channel-gateway idem. (survey.ts muda o `SurveyDeps` → rebuild do mcp-server.)
