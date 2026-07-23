# ADR: Store operacional por-resposta de survey — schema PG dedicado × estender a dialog-api

**Status:** Aceito (2026-07-23) — desbloqueia S8/S9 do módulo Customer Surveys. Pré-implementação (nenhum
código ainda; é o gate arquitetural antes de codar S8).
**Data:** 2026-07-23
**Componentes:** `packages/evaluation-api` (host proposto do store), `packages/mcp-server-plughub`
(`survey_record` passa a persistir), `packages/channel-gateway` (`survey_web.submit` para de descartar
verbatim), `packages/dialog-api` (inalterada — segue só definições), `packages/analytics-api`
(`session_signal` inalterada).
**Relacionado:** [`docs/arcos/customer-surveys.md`](../arcos/customer-surveys.md) §7.2/§10b/§10c/§12.1,
[`docs/adr/adr-survey-form-scoring-composition.md`](adr-survey-form-scoring-composition.md) (D8 — forms na
dialog-api), [`docs/arcos/outbound.md`](../arcos/outbound.md) (`contact_eligibility_check` genérico que
supersede a quarentena de survey).

---

## Contexto

As fases **S8** (navegador de respostas com verbatim/áudio) e **S9** (agente IA analista de verbatims) do
módulo Customer Surveys pressupõem uma **resposta individual durável** — a linha operacional que o §7.2 do
spec chamava de `survey_response`. Antes de tocar S8, é preciso decidir **onde** essa linha vive, porque o
resto do módulo foi entregue **por substituição** (dialog-api, `contact_eligibility_check`, `session_signal`
genéricos) e o §7.2 original — que hospedava tudo num schema PG `survey` na evaluation-api — ficou
parcialmente obsoleto. Este ADR isola essa decisão de store para não arrastá-la implicitamente no primeiro
commit de S8.

### O que existe hoje (levantamento 2026-07-23)

- **Nenhuma linha por-resposta durável em lugar nenhum.** Não há schema PG `survey`, nem tabela
  `survey_response`/`survey_instance`. O único rastro durável de uma submissão é o **fato analítico**
  `analytics.session_signal` (ClickHouse) mais um **snapshot transiente em Redis**.
- **dialog-api** persiste **só definições**: schema `dialog`, tabela `dialog.forms` (DialogForm versionado,
  draft/published). Serviço deliberadamente **read-open e sem-PII** (comentário de cabeçalho do `router.py`).
  Sem conceito de resposta/instância/submissão.
- **`survey_web.submit`** (channel-gateway) emite `session.signals` (Kafka) e reescreve o registro Redis
  `survey_web:token:{token}` (TTL 7d, vira anti-replay). **Verbatim é descartado**: o loop só considera
  `question` com `capture.metric` cujo valor faz `float()` — resposta não-numérica (open_text) é pulada em
  silêncio. Nada persiste texto aberto.
- **`survey_record`** (mcp-server) e o `SurveySignalSchema` só carregam `{metric, value, value_label?,
  scale?}` — `value_label` é rótulo ≤64 chars, **não** texto livre. Emite `session.signals` e não escreve
  nada operacional.
- **`session_signal`** (ClickHouse) é `ReplacingMergeTree` **1 linha por métrica**, numérico, TTL 2 anos,
  sem coluna de verbatim. É agregado analítico, não resposta operacional.
- **Áudio:** não há **nenhuma** ligação resposta-de-survey ↔ artefato de áudio/transcrição. Existe um
  `attachment_store` (webchat, tabela `session_attachments`) e adapters de voz/STT, mas nada wirado a survey.
- **Precedente:** a única tabela `*_responses` do repo é `evaluation.criterion_responses` (evaluation-api) —
  dado de avaliação de qualidade, com postura ABAC/LGPD já estabelecida.

### Por que o ClickHouse não basta (e por que a decisão é real)

`session_signal` é o destino **analítico** certo e permanece. Mas é a forma errada para S8/S9: é
1-linha-por-métrica (não por-resposta), numérico (sem texto aberto), deduplicado por `metric`, TTL 2a, e
**sem controle de acesso por-campo** — enquanto verbatim e áudio são exatamente o dado **LGPD-controlado**
que o §10b/§10c dizem "acesso controlado, nunca replicado em massa ao ledger analítico". Guardar verbatim
como `value_label` é inviável (≤64 chars, dedup por métrica) e violaria a separação analítico×operacional.
**Logo: um store operacional por-resposta precisa ser construído de qualquer forma** — a decisão é só
**onde** ele mora e **qual o escopo mínimo**.

---

## Opções consideradas

### Opção A — schema PG `survey` dedicado (na evaluation-api)

Criar o store operacional por-resposta como schema PG próprio, hospedado na **evaluation-api** (namespace
`survey`, como o §7.2 já previa), reusando a postura ABAC/LGPD e o precedente `criterion_responses`.
**Escopo podado** ao que S8/S9 realmente exigem: `survey_instance` + `survey_response` (com `open_text` e
`audio_ref`). As demais tabelas do §7.2 original **não entram** — `survey_question`/`survey_definition` já
são DialogForm na dialog-api (ADR scoring, D8), e `survey_quarantine`/`_policy` já foram supersedidas pelo
`contact_log`/`contact_policy` genéricos da mailing-api.

### Opção B — estender a dialog-api para guardar respostas

Adicionar a família de tabelas de resposta ao PG da dialog-api (que já tem `dialog.forms`).

**Rejeitada.** A dialog-api é por design **read-open e sem-PII** (guarda template, não dado de cliente);
enfiar `open_text`/áudio ali introduz uma família de tabelas com PII e um regime de acesso que o serviço
explicitamente evita — quebraria sua postura de segurança. Colocar definição (conteúdo do tenant, público)
e resposta (PII do cliente, LGPD) no mesmo serviço colapsa duas costuras que hoje estão limpas.

### Opção C — só ClickHouse (nenhum store operacional)

Guardar verbatim como sinal extra em `session_signal`.

**Rejeitada.** Detalhado acima: forma errada (por-métrica, numérico, dedup, sem controle de acesso). Não
suporta S8 (lista por-resposta) nem o regime LGPD do verbatim/áudio.

---

## Decisão (recomendada)

**Opção A, escopo mínimo, na evaluation-api.** Duas tabelas no schema `survey`:

```
survey_instance   -- uma ocorrência de pesquisa (chave de religação + atribuição + LGPD scope)
  instance_id (PK), tenant_id, survey_id (= form_id do DialogForm)
  origin_session_id, grain, segment_id?, agent_key?, pool_id, customer_key
  channel, status (pending|sent|responded|expired|skipped)
  survey_session_id?          -- a sessão-filho de survey (Arc 19), quando houver
  session_at, sent_at, responded_at

survey_response   -- a resposta (fonte operacional da verdade)
  response_id (PK), instance_id (FK)
  signals            -- jsonb[]: { metric, value, value_label }  (espelho do que virou session_signal)
  open_text?         -- VERBATIM (LGPD: acesso controlado, nunca em massa ao ledger analítico)
  audio_ref?         -- ligação ao artefato do attachment_store + transcript_ref? (forward-looking)
  response_channel, responded_at
```

**Fica de fora** (por já existir alhures — evita duplicar fonte): `survey_question`/`survey_definition`
(→ DialogForm na dialog-api), `survey_quarantine`/`_policy` (→ `contact_log`/`contact_policy` na
mailing-api). O §7.2 do spec fica **podado** a estas duas tabelas.

**Host = evaluation-api** porque (1) o §7.2 já a nomeava, (2) já tem o motor de amostragem, a postura
ABAC/LGPD e o precedente `criterion_responses`, e (3) mantém a dialog-api limpa (definição≠resposta). O
spec já ressalva a graduação para um `survey-api` dedicado se o domínio crescer — sem migração de schema,
só de host.

### Caminho de escrita canônico (fecha o descarte de verbatim)

A regra do §7 — *resposta capturada → grava `survey_response` (PG) → emite `survey_record` →
`session.signals`* — passa a valer de fato:

- **`survey_record`** (mcp-server) ganha um passo de persistência: antes de emitir `session.signals`,
  faz `POST` na evaluation-api para gravar `survey_instance`/`survey_response` (idempotente por
  `origin_session_id`+`grain`+`instance`). Sinais numéricos seguem para `session_signal` como hoje.
- **`survey_web.submit`** (channel-gateway) para de **descartar** as respostas não-numéricas: `open_text`
  vai para `survey_response.open_text` (operacional/LGPD), **não** para `session_signal`. Unifica o caminho
  de escrita — hoje o submit web emite direto, divergindo do `survey_record`.
- **`session_signal` permanece inalterada** (numérico, analítico). Verbatim e áudio **nunca** entram no
  ledger analítico — só na resposta operacional, com acesso controlado.

---

## Invariantes preservadas

- **Analítico × operacional separados:** `session_signal` (ClickHouse) segue sendo a verdade analítica
  agregada; `survey_response` (PG) é a verdade operacional por-resposta. Um caminho de escrita canônico
  (`survey_record`), dois destinos — exatamente o §7 do spec.
- **Single-source por domínio:** definição de form na dialog-api; elegibilidade/fadiga na mailing-api;
  **resposta** na evaluation-api. Nenhuma tabela duplicada entre stores.
- **dialog-api continua sem-PII e read-open** — verbatim não a contamina.
- **Runner continua burro** — quem persiste é o domínio (`survey_record`/submit), não o step de flow.
- **LGPD:** verbatim/áudio ficam num store com controle de acesso por-papel (herda a postura da
  evaluation-api), nunca replicados em massa ao ClickHouse.

## Consequências

- Desbloqueia **S8** (navegador lê `survey_response`/`survey_instance` por-resposta, com verbatim e link
  de áudio) e **S9** (analista IA consome a resposta com `open_text` para classificar sentiment/tema/
  urgência e endereçar).
- Fecha o **bug silencioso** de hoje: respostas de texto aberto no survey web são perdidas.
- Custo contido: duas tabelas + um endpoint de persistência na evaluation-api + o wiring de escrita em dois
  produtores (`survey_record`, `survey_web.submit`). Sem serviço novo, sem migração de dado existente
  (não há dado por-resposta a migrar).
- `customer_key` como coluna-junção segue o §7.3 (precedência `caller.customer_id` → `contact_identifier`),
  forward-compatível com o cadastro de cliente futuro sem migração.

## Decisões em aberto

1. **Áudio/transcrição** (`audio_ref`/`transcript_ref`): a coluna entra agora (nullable), mas o wiring ao
   `attachment_store` + STT dos canais de voz é trabalho de S8/S9, não deste ADR. Retenção do artefato de
   áudio herda a política de uploads do Channel Gateway (LGPD, por-tenant).
2. **Graduação para `survey-api` dedicado:** adiada até o domínio crescer (gatilho: volume de escrita ou
   necessidade de lifecycle/retention próprio de survey). Sem migração de schema — só de host.
3. **Idempotência da persistência** no `survey_record` vs. o emit de `session.signals` (ordem, retry,
   parcial): **RESOLVIDO** — contrato do endpoint, DDL, chave de idempotência e ordem persist-first em
   [`docs/product/survey-response-store-implementation-spec.md`](../product/survey-response-store-implementation-spec.md)
   (preparado 2026-07-23, turnkey para o 1º corte de S8).
