# Quality Ingest — leitor de histórico plugável (interno ↔ externo) para avaliação

> Estado: **R13a–R13d implementados (arco completo)**. Última atualização: 2026-06-25.
> Complementa `docs/arcos/arc-evaluation-metrics-methodology.md` §IV.5–IV.7 (aterra e revisa o
> contrato A2 → **interface de eventos**) e `docs/arcos/arc6-evaluation.md` (plataforma de avaliação).

## 1. Objetivo

Tornar o **leitor de histórico de contatos plugável**: hoje o fluxo de avaliação consome o histórico
**interno** da plataforma; o mesmo fluxo deve poder consumir **históricos externos** (CCaaS de terceiros),
**reaproveitando todo o resto** (sampling → ReplayContext → avaliador IA → revisor IA → analytics). Caso
concreto inicial: **reavaliar contatos históricos da própria plataforma** para alimentar o avaliador/revisor.

Escopo honesto — **grau-transcript**: tier-1 qualitativo + `session_metric.*` deriváveis só do transcript
(timing/composição/silêncio, se houver `ts` + `author_role`). **Tier-2 de IA** (tool correctness,
faithfulness-vs-ferramenta, policy-por-trajetória) e métricas de step/custo ficam **indisponíveis** para
dados sem `mcp.audit`/`pipeline_state` (sempre o caso externo).

## 2. Arquitetura — módulo anti-corrupção (`quality-ingest`)

Espectro de importação: de "eventos básicos" a "inserção direta na base". Escolha = **meio-termo seguro**:
um módulo (`packages/quality-ingest/`) que **expõe uma interface de eventos própria** e a **mapeia para a
interface interna de eventos canônicos**. O importador externo (e o exportador interno) **nunca tocam a
infra de eventos** (Kafka/tópicos/schemas internos) nem os stores — só conhecem o **contrato** do módulo.
O módulo é o **único tradutor** (isolamento nos dois sentidos).

```
[importador externo / exportador interno]
        │  (ingestion_event_v1 — interface ABERTA do módulo)
        ▼
   quality-ingest  ── valida → masking net → MAPEIA 1:1 → emite eventos canônicos internos
        │
        ▼
 [conversations.events / conversations.participants / agent.lifecycle]  ← infra interna, NÃO exposta
        │
        ▼
 consumers existentes → ClickHouse (messages/segments/sessions) + (contact_closed) dispara sampling
        │
        ▼
 fluxo de avaliação por CAMPANHA → ReplayContext → avaliador IA → revisor IA → analytics
```

## 3. Interface = stream de eventos (não documento de lote)

O módulo recebe **vários eventos externos em sequência**, possivelmente **intercalados de vários contatos**,
correlacionados por **`external_contact_id`** (→ `session_id` determinístico). O "lote" é só como o remetente
organiza o envio — **invisível ao módulo**, que traduz **por evento**. Família `ingestion_event_v1`:

| Evento externo | Obrigatórios | → evento canônico interno |
|---|---|---|
| `contact.opened` | `external_contact_id`, `source`, `channel`, `opened_at` | `conversations.events` `contact_open` |
| `participant.joined` | `external_contact_id`, `segment_ref`, `external_agent_id`, `agent_kind`, **`pool_id`**, `started_at` | `conversations.participants` `participant_joined` |
| `message.sent` | `external_contact_id`, `ts`, `author_role`, `content`, `masked` | `conversations.events` `message_sent` |
| `participant.left` | `external_contact_id`, `segment_ref`, `ended_at`, `outcome?` | `agent.lifecycle` `agent_done` + `participant_left` |
| `contact.closed` | `external_contact_id`, `outcome`, `closed_at` | `conversations.events` `contact_closed` → **dispara sampling** |

Opcionais por evento: msg (`author_id`, `segment_ref`, `content_type` def `text`, `visibility` def `all`,
`masked_categories`); segment (`skill_id`, `deploy_version` — só IA, habilita analytics/cota por versão;
`role` def `primary`); contato (`close_reason`, `medium`, `customer_ref`). Métricas pré-computadas e
`tool_trace` opcionais (tool_trace ⇒ tier-2, só interno).

**Pool é a unidade (decisão).** Como **campanha sempre coleta de um pool**, os eventos carimbam **`pool_id`**
(não `campaign_id`): a ingestão fica **desacoplada** da avaliação. Qualquer campanha que mira o pool amostra
os contatos daquele pool — internos **ou** importados — sem caminho especial. Permite campanhas que avaliam
pools com dados internos e pools com dados importados, uniformemente.

**Completude/ordem**: `contact.closed` é o gatilho de finalização (sampling + `delta_ms` recalculado no
consumer Y). Contato sem `closed` nunca finaliza → não avaliado (dado incompleto fora, de propósito).
**Idempotência**: `event_id` estável por evento (remetente fornece ou módulo deriva de `external_contact_id`
+índice) → re-envio não duplica (consumers ReplacingMergeTree/`ON CONFLICT`).

## 4. Stream durável = consumer Y (produtor puro) ✅ R13b

O módulo **não toca store**. Um **consumer interno** (gated `source=external_import`) reconstrói
`session_stream_events` (PG) a partir dos eventos canônicos — append por `message_sent` (idempotente por
`event_id`), `delta_ms` recalculado no `contact_closed`, `original_content=null` — **compartilhando a
construção de linha** com o Persister vivo (sem drift). Hydrator/Replayer leem transparente → ReplayContext
igual ao interno.

**Implementado** (`packages/session-replayer/src/session_replayer/import_stream_consumer.py`): `ImportStreamConsumer`
(grupo `session-replayer-import`, `auto_offset_reset=earliest`) consome `conversations.events` +
`conversations.participants`, mapeia 1:1 ao vocabulário de stream interno (`contact_open→session_opened`,
`message_sent→message`, `contact_closed→session_closed`, `participant_joined/left`) e grava via
`StreamPersister.insert_records` — o **mesmo escritor** do Persister vivo (refatorado p/ expor
`insert_records()` + `recompute_deltas()`; `persist()` passou a usá-los → zero drift). `delta_ms` finalizado
por `recompute_deltas` (janela `LAG` por timestamp) no fechamento — **ordem-independente** (eventos atrasados/
fora de ordem em partições distintas continuam corretos; o Replayer também recomputa na leitura).
`author.role`: `customer` preservado, `agent→primary`, `system→system` (rótulo de autor grosso p/ grau-transcript;
a granularidade por-segmento vive em `analytics.segments`). Linhas de importação têm `original_content=null`
(cego por construção). Convive com o Persister vivo: p/ sessões importadas o Persister lê o stream Redis
inexistente → no-op; o consumer Y preenche as linhas (idempotente por `event_id`). **Limitação:** o Replayer
lê `session:{id}:meta`/`:participants`/`:sentiment` do Redis (ausentes p/ importados) → `session_meta`/
`participants`/`sentiment` caem em default; os **events** (transcript), que são o núcleo da avaliação, ficam
completos.

## 5. Masking

PII deixa de existir **fora** do PlugHub: o contrato exige `content` já mascarado + `masked=true` +
`masked_categories`. Como a responsabilidade LGPD recai no **armazenamento**, o módulo roda uma **passada-rede**
com as `MaskingRule` no ingest. `original_content=null` (revisão cega por construção).

## 6. Mapa de identidade/pool/versão por `source` (Config API) ✅ R13c

Por `source` (ex. `ccaas:genesys`): `external_agent_id → user_id` (humano) / `skill_id`+`deploy_version` (IA);
`external_pool → pool_id` interno. Não se repete por evento (configurado uma vez por source).

**Implementado**: namespace `quality_ingest`, key **`source_map`** (Config API, seed-if-absent default `{}`) —
um JSON keyed por `source`: `{ "<source>": { "pools": {ext→int}, "agents": {ext_agent_id→{kind, user_id |
skill_id+deploy_version}} } }`. O `quality-ingest` lê via `SourceMapClient` (`GET /config/quality_ingest/
source_map?tenant_id=`, cache TTL 60s por tenant, **degradação graciosa** → `{}`), resolvendo override de
tenant → default global. A tradução roda **no mapper, ANTES de emitir os eventos canônicos** (anti-corrupção:
analytics, sampling e consumer Y só veem identidades INTERNAS): `pool_id` traduzido (carimbado também no
`contact_closed`/`session_closed` via segmento primário), humano → `user_id`, IA → `flow_id`(=skill_id)+
`deploy_version`. **Pass-through** quando source/pool/agent não mapeados (comportamento R13a-2). Editável por
tenant. Sem versão → fallback ADR `(campaign, pool, skill)` no sampling (inalterado).

## 7. Exportador interno (leitor de histórico)

Lê contatos do histórico da plataforma (ClickHouse/`session_stream_events`) e **emite os mesmos eventos
`ingestion_event_v1`** pela interface — **mesma porta do externo**, sem código de avaliação divergente.
**Preferência (decisão): reusa o `pool_id` original** do contato (consequências — mistura com tráfego vivo,
campanha amostrando ambos — **a avaliar depois**; alternativa = pool de revisão dedicado).

**Implementado ✅ R13d** (`packages/quality-export/`, FastAPI porta 3852, deps só `httpx`): leitor
**ClickHouse-only** — a tabela `messages` já tem o transcript mascarado (`original_content` nunca sai), e
`segments`/`sessions` têm os metadados. `POST /v1/export/sessions {tenant_id, session_ids, source?}` lê
`sessions`+`segments`+`messages` (`FINAL`), reconstrói `ingestion_event_v1` (o **inverso** do mapper:
`sessions→contact.opened/closed`, `segments→participant.joined/left` filtrando `primary`/`specialist`,
`messages→message.sent` com `author_role` revertido `customer`/`agent`/`system`) e faz **POST na mesma porta**
do quality-ingest. `external_contact_id = session_id` original → o mapper deriva um **novo** `session_id` de
reavaliação (sem colisão com o original). Pool original reusado: `source="internal:reeval"` sem `source_map`
→ pass-through. **Pool de revisão dedicado sai de graça do R13c**: basta cadastrar um `source_map` para
`internal:reeval` mapeando os pools originais → o pool de revisão (sem mecanismo novo). É um **cliente do
contrato** (não toca infra de eventos interna; só lê histórico e re-emite). Sessão sem `closed_at` → não
exporta (incompleta). *(unit: 9 testes do builder puro + round-trip pelo mapper do quality-ingest; smoke
`infra/test/smoke_quality_export.sh` — import → export → re-eval com pool/transcript originais + sampling)*

**Concern (registrado §9) — ✅ RESOLVIDO (2026-06-25):** a reavaliação criava linhas de analytics novas
(session_id de reavaliação) sob o **pool original**, misturando com tráfego vivo. Resolvido pelo isolamento
por `origin` (ADR `adr-quality-substrate-isolation`): substrato carimba `origin=reeval`, report layer e
sampling filtram `live` por default. Reuso de pool tornou-se inócuo; `source_map`→pool dedicado segue como
opção, não requisito.

## 8. Fatiamento

- **R13a-1 ✅** — schemas `ingestion_event_v1` (família de eventos externos) em `@plughub/schemas` + validação.
  *(unit: 39 testes vitest + typecheck)*
- **R13a-2 ✅** — `packages/quality-ingest/`: endpoint aberto de eventos + masking net + mapeamento→emissão de
  eventos canônicos. *(unit: 23 testes pytest; smoke `infra/test/smoke_quality_ingest.sh` — fixture de 1 contato
  → ClickHouse sessions/messages/segments + sampling dispara sob campanha)*
- **R13b ✅** — consumer Y (`session_stream_events` from-events, gated `source=external_import`).
  *(unit: 10 testes do mapper/handler em `session-replayer/tests/test_import_stream_consumer.py`; smoke estendido
  `smoke_quality_ingest.sh` §4b — `session_stream_events` populado p/ a sessão importada, `original_content` NULL)*
- **R13c ✅** — mapa de identidade/pool/versão por `source` (Config API namespace `quality_ingest.source_map`).
  *(unit: casos de tradução pool/humano/IA/pass-through em `quality-ingest/tests/test_mapper.py`; smoke
  `smoke_quality_ingest_sourcemap.sh` — PUT do map, ids externos → internos em `analytics.segments` + sampling)*
- **R13d ✅** — exportador interno (`packages/quality-export/`, ClickHouse-only → `ingestion_event_v1` pela
  mesma porta) — fecha a reavaliação interna. *(unit: 9 testes + round-trip; smoke `smoke_quality_export.sh`)*

## 8.1 Implementação R13a (entregue)

**R13a-1** — `packages/schemas/src/ingestion-event.ts`: união discriminada por `event_type`, vocabulário
próprio do módulo (`agent_kind`, `author_role`, `content_type`, `visibility`, `role`) — **decoupled** dos
enums internos (`ChannelSchema`/`SegmentOutcome`/`DataCategory`); `channel`/`outcome`/`pool_id`/
`masked_categories` ficam strings livres (mapeadas a jusante). `tenant_id` **não** vai no corpo do evento
(resolvido do header no ingest). `deriveIngestionEventId` p/ idempotência.

**R13a-2** — `packages/quality-ingest/` (Python FastAPI, porta 3850, env `PLUGHUB_QUALITY_INGEST_`,
**produtor puro** — sem store/consumo). Módulos: `events.py` (mirror Pydantic do contrato), `masking.py`
(net-pass = port de `DEFAULT_MASKING_RULES`: cpf/credit_card/phone/email), `identity.py` (`session_id`
válido p/ `SessionIdSchema` + `segment_id`/`participant_id` via uuid5 — determinísticos = idempotência),
`mapper.py` (o tradutor único), `emitter.py` (aiokafka), `router.py` (`POST /v1/ingest/events`), `main.py`.

Mapa wire-format (validado contra os produtores vivos do orchestrator-bridge):

| Evento externo | Tópico canônico | Notas de wire |
|---|---|---|
| `contact.opened`    | `conversations.events` `contact_open`   | sessions: channel, customer_id, opened_at |
| `participant.joined`| `conversations.participants`            | campo **`type:"participant_joined"`** (underscore, não dotted); `agent_type` ai/human; AI→`flow_id`/`deploy_version`, humano→`user_id`/`user_login`; consumido p/ segments **e** acumulador de sampling (evaluation-api) |
| `message.sent`      | `conversations.events` `message_sent`   | masking net-pass aplicado aqui |
| `participant.left`  | `conversations.participants` + `agent.lifecycle` `agent_done` | segment fecha (`duration_ms`/`outcome`) + libera recurso |
| `contact.closed`    | `conversations.events` `contact_closed` **+** `conversations.session_closed` | `session_closed` **dispara o sampling**; ambos carimbam `pool_id` do segmento primário |

Invariantes da implementação: (1) toda emissão leva `source:"external_import"` (gate do R13b; ignorado
pelos consumers vivos; **nunca** `channel_gateway`, que o parser do analytics descarta); (2) emissões são
ordenadas por fase no batch inteiro → todo `participant_joined` precede qualquer `session_closed`
(o sampling por segmento depende do acumulador estar preenchido; há fallback por sessão se houver corrida).

## 9. Concerns / em aberto

- **ReplayContext: transcript ✅ R13b; meta/participants/sentiment ainda em default** — o consumer Y reconstrói
  `session_stream_events` p/ importados, então o ReplayContext.**events** (transcript) fica completo e o contato
  é avaliável (não mais `scheduled` sem contexto). **Residual:** o Replayer lê `session:{id}:meta`/`:participants`/
  `:sentiment` do Redis, ausentes p/ importados → esses campos caem em default (channel `webchat`, listas vazias).
  Reavaliar: hidratar também esses do PG/dos eventos se a avaliação passar a depender deles (hoje o avaliador
  opera sobre o transcript).
- **Correlação por-requisição (in-memory) no R13a-2** — o mapper do quality-ingest correlaciona por
  `external_contact_id` apenas **dentro de um POST**. O R13b **não** muda isso: o consumer Y é
  ordem-independente/idempotente (mensagens em POSTs separados ainda entram no stream certo), mas o `pool_id`
  carimbado no `contact.closed`/`session_closed` pelo quality-ingest **degrada** p/ `""` se o `participant.joined`
  veio noutro request (o sampling por pool então não casa). Continua aberto: durabilizar o estado por-contato do
  quality-ingest (ex.: Redis/PG chaveado por `session_id` determinístico) — ou exigir contato completo por POST.
- **Reuso do pool original na reavaliação interna (R13d em efeito)** — ✅ **RESOLVIDO (2026-06-25)** pelo
  arco de isolamento por `origin` (ADR `adr-quality-substrate-isolation`, ver CHANGELOG): a reavaliação carimba
  `origin=reeval` no substrato (derivado do `source=internal:reeval`); o report layer filtra `origin='live'`
  por default (produção não conta a reavaliação, mesmo no pool reusado) e o sampling tem filtro opcional de
  `origin` por campanha (produção mira `live`; reavaliação seta `reeval`) → sem dupla contagem nem cross-fire.
  O reuso de `pool_id` virou **inócuo** (o eixo de isolamento é a origem, não o pool). `source_map`→pool
  dedicado (R13c) continua disponível como opção, não mais necessário.
- **Tier-2 indisponível p/ externo** — assumido (sem `mcp.audit`/`pipeline_state`).
- **Completude por contato** — depende do remetente enviar `contact.closed`; contatos parciais não avaliam.
- **Pool deve existir** — `pool_id` (interno ou mapeado de external) precisa existir como pool p/ campanhas
  mirarem e p/ scoping de analytics.
