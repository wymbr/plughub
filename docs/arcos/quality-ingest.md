# Quality Ingest — leitor de histórico plugável (interno ↔ externo) para avaliação

> Estado: **design fechado, implementação pendente** (R13a–R13d). Última atualização: 2026-06-24.
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

## 4. Stream durável = consumer Y (produtor puro)

O módulo **não toca store**. Um **consumer interno** (gated `source=external_import`) reconstrói
`session_stream_events` (PG) a partir dos eventos canônicos — append por `message_sent` (idempotente por
`event_id`), `delta_ms` recalculado no `contact_closed`, `original_content=null` — **compartilhando a
construção de linha** com o Persister vivo (sem drift). Hydrator/Replayer leem transparente → ReplayContext
igual ao interno.

## 5. Masking

PII deixa de existir **fora** do PlugHub: o contrato exige `content` já mascarado + `masked=true` +
`masked_categories`. Como a responsabilidade LGPD recai no **armazenamento**, o módulo roda uma **passada-rede**
com as `MaskingRule` no ingest. `original_content=null` (revisão cega por construção).

## 6. Mapa de identidade/pool/versão por `source` (Config API)

Por `source` (ex. `ccaas:genesys`): `external_agent_id → user_id` (humano) / `agent_id`+`skill_id`+
`deploy_version` (IA); `external_pool → pool_id` interno. Não se repete por evento. Sem versão → fallback
ADR `(campaign, pool, skill)`.

## 7. Exportador interno (leitor de histórico)

Lê contatos do histórico da plataforma (ClickHouse/`session_stream_events`) e **emite os mesmos eventos
`ingestion_event_v1`** pela interface — **mesma porta do externo**, sem código de avaliação divergente.
**Preferência (decisão): reusa o `pool_id` original** do contato (consequências — mistura com tráfego vivo,
campanha amostrando ambos — **a avaliar depois**; alternativa = pool de revisão dedicado).

## 8. Fatiamento

- **R13a-1** — schemas `ingestion_event_v1` (família de eventos externos) em `@plughub/schemas` + validação. *(unit)*
- **R13a-2** — `packages/quality-ingest/`: endpoint aberto de eventos + masking net + mapeamento→emissão de
  eventos canônicos. *(smoke: fixture de eventos de 1+ contatos → ClickHouse populado + sampling dispara sob campanha)*
- **R13b** — consumer Y (`session_stream_events` from-events, gated `source=external_import`).
- **R13c** — mapa de identidade/pool/versão por `source` (Config API).
- **R13d** — exportador interno (histórico → eventos → mesma interface) — fecha a reavaliação interna.

## 9. Concerns / em aberto

- **Reuso do pool original na reavaliação interna** — preferência registrada; avaliar depois a mistura
  tráfego-vivo × reavaliação histórica no mesmo pool (campanha amostrando os dois; possível dupla contagem).
- **Tier-2 indisponível p/ externo** — assumido (sem `mcp.audit`/`pipeline_state`).
- **Completude por contato** — depende do remetente enviar `contact.closed`; contatos parciais não avaliam.
- **Pool deve existir** — `pool_id` (interno ou mapeado de external) precisa existir como pool p/ campanhas
  mirarem e p/ scoping de analytics.
