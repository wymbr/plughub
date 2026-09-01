# ContextStore — Guia do Modelo de Contexto

> Última atualização: 2026-07-22 · Estado: pós-Journey J5a (escrita imperativa journey-aware)

> **Substitui:** `contact_context` / `context_package` (legado)
> **Fonte de verdade técnica:** `packages/sdk/src/context-store.ts` (roteamento + TTL) ·
> `packages/mcp-server-plughub/src/tools/journey.ts` (`writeContextTag`) · `CLAUDE.md § ContextStore`

---

## O que é (e o que NÃO é)

O ContextStore é o repositório de **estado observável e endereçável** de um contato/processo: hashes
Redis onde componentes leem e escrevem campos **tipados**, com confiança e origem. Acesso é **por chave**
(`@ctx.caller.cpf`), determinístico — **não** é busca semântica. Para "alimentar e perguntar depois" sobre
texto não-estruturado existe outra ferramenta (o knowledge base `mcp-server-knowledge` / `insight.historico.*`).

Duas camadas, com papéis distintos:

- **Mecânica = plataforma (fixa).** Roteamento por namespace, TTL, resolução da raiz da journey, migração
  no merge e um conjunto de tags que a plataforma popula sozinha (`session.pool.*`, `session.sentimento.*`,
  `core.contact.root_session_id`, …). O desenvolvedor não controla isso.
- **Semântica = contrato do desenvolvedor.** Quais tags de negócio existem, o que significam, quem escreve e
  quem lê — acordo prévio entre o flow produtor e o consumidor. Sem esse contrato a tag não existe para o leitor.

Diferente do `pipeline_state` (estado de execução efêmero de UM flow), o ContextStore atravessa agentes,
sessões (via journey) e é visível ao Agent Assist e às tools de supervisão.

---

## Os três hashes e seus TTLs

O **prefixo da tag decide DUAS coisas**: em qual hash ela vive e qual o TTL. Roteamento em
`sdk/src/context-store.ts` (`LONG_TTL_PREFIXES`, `JOURNEY_TTL_PREFIX`) e, para escrita imperativa via MCP,
`journey.ts` (`writeContextTag`).

| Hash Redis | Escopo | TTL | Namespaces que moram aqui |
|---|---|---|---|
| `{t}:ctx:{sessionId}` | uma **sessão** (contato) | **4h** (renovado a cada escrita) | `caller.*`, `account.*`, `session.*`, `segment.{segId}.*`, `insight.conversa.*`, `sla.*`, `queue.*`, `workflow.*`, `approval.*` |
| `{t}:ctx:journey:{root}` | o **processo** (N sessões da journey) | **30 dias** | `journey.*` |
| `{t}:ctx:customer:{customerId}` | o **cliente** (longo prazo) | **90 dias** | `insight.historico.*`, `pricing.*` |

Notas de TTL:

- **`journey.*`** precisa de TTL de processo (30d), não de sessão — senão evaporaria entre dois contatos da
  mesma journey. A raiz canônica (`{root}`) é resolvida por union-find (proveniência `core.contact.root_session_id`
  → floresta de aliases), a mesma via do bridge e do `journey_merge`.
- **`session.pool.*`** é escrito com **TTL 24h NX** (estende a chave da sessão), pelo Routing Engine.
- **`insight.historico.*` / `pricing.*`** só roteiam para o hash do cliente **quando há `customerId`**; sem ele,
  degradam para o hash da sessão.
- Override por-tag: `ContextEntry.ttl_override_s` (raro).

---

## Modelo de dados — `ContextEntry`

Cada campo é um `ContextEntry` serializado em JSON:

```typescript
interface ContextEntry {
  value:      unknown                        // string | number | boolean | object
  confidence: number                         // 0.0–1.0
  source:     string                         // origem do dado
  visibility: "agents_only" | "all"          // default agents_only
  updated_at: string                         // ISO-8601 (gerado na escrita)
  ttl_override_s?: number                    // opcional
}
```

**Escala de confiança** (usada por `confidence_gte` e pela política de coleta):

| Faixa | Significado | Comportamento |
|---|---|---|
| 0.9–1.0 | Confirmado explicitamente | Usar sem confirmar |
| 0.7–0.9 | Inferido com alta certeza | Usar sem confirmar |
| 0.4–0.7 | Incerto | Confirmar se `force_confirmation` |
| 0.0–0.4 | Desconhecido | Coletar novamente |

**Estratégias de merge** (na escrita via `context_tags`/SDK): `overwrite` (sempre substitui),
`highest_confidence` (só substitui se a nova confiança for maior — default), `append` (acumula em array).

---

## Catálogo de chaves

Legenda de origem: **P** = a plataforma popula automaticamente · **D** = o desenvolvedor escreve no skill/tool.
Todas no hash de **sessão** salvo indicação de journey/cliente.

### `caller.*` — identidade de quem contata (sessão · 4h)

Escrito majoritariamente por **D** via `context_tags` de tools de CRM/customer (o `McpInterceptor` chama o
`ContextAccumulator`) e por reason steps.

| Tag | Origem | Uso |
|---|---|---|
| `caller.customer_id` | D/Operador | id interno do cliente (não-PII). Operador pode escrever (allowlist `inject-context`); carimbado em `sessions.customer_id` no fechamento. Chave do histórico 360. |
| `caller.cpf` | D | identificador fiscal (PII, mascarável) |
| `caller.nome` | D | nome do cliente |
| `caller.account_id` | D | conta associada |
| `caller.telefone` / `caller.email` | D | canais de contato |
| `caller.motivo_contato` | D | motivo (lido por copilot/roteamento) |
| `caller.intencao_primaria` | D | intenção detectada |
| `caller.sentimento_atual` | D | sentimento extraído da conversa (≠ `session.sentimento.*` da plataforma) |

### `account.*` — dados da conta em discussão (sessão · 4h)

Escrito por **D** via `context_tags.outputs` de tools de billing/CRM.

| Tag | Origem | Uso |
|---|---|---|
| `account.plano_atual` | D | plano corrente |
| `account.status` | D | status da conta (ativo/…) |
| `account.holder_*` | D | dados do titular (convenção) |

### `session.*` — estado do contato atual (sessão · 4h)

Subgrupos **de plataforma** (P):

| Tag | Origem | Escritor | Uso |
|---|---|---|---|
| `core.pool.id` / `.channels` / `.mentionable_pools` / `.agent_groups` / `.max_reply_time_ms` / `.llm_account_ids` | P | Routing Engine `_write_pool_context` (conf 1.0, `routing_engine`, TTL 24h NX) | pool alocado e sua config para o agente |
| `core.queue.position` / `.eta_ms` | P | Routing Engine (efêmero) | posição/ETA na fila |
| `core.sentiment.current` / `.category` | P | AI Gateway `sentiment_emitter` (conf 0.80) | score −1..1 e categoria satisfied/neutral/frustrated/angry |
| `core.copilot.suggested_reply` / `.risk_flags` / `.recommended_actions` / `.last_analysis` | P | AI Gateway `copilot_emitter` | assistência ao agente humano |
| `core.contact.root_session_id` | P | channel-gateway (webhook) | raiz de proveniência da journey (base do union-find) |
| `core.workflow.origin_session_id` / `core.contact.spawn_reason` | P | channel-gateway / bridge | 1 salto de proveniência + por que a sessão nasceu |
| `core.contact.close_origin` | P | bridge (pré-hook) | origem do fechamento (lido por hooks NPS/wrap-up) |
| `core.contact.customer_participant_id` | P | bridge (pré-hook) | participant do cliente (visibilidade de NPS) |
| `core.contact.human_agent_participant_id` | P | bridge (pré-hook) | participant do agente humano (visibilidade de wrap-up) |
| `core.contact.last_primary_segment_id` / `.last_primary_agent_key` | P | bridge | último segmento/agente primário (alvo de survey) |
| `core.survey.segment_id` / `.surveyed_agent_key` | P | bridge | segmento/agente pesquisado |
| `core.process.outcome` / `session.contact_outcome` | P | bridge / payload do hook de close | desfecho do processo/contato (o runner de survey decide "ciclo fechado") |
| `core.workflow.resume_token` | P | channel-gateway | token de resume (Arc 19 suspend/resume) |
| `session.approval_threshold` / `.deploy_version` / `.approval_segment_id` | P | channel-gateway | contexto de passo de aprovação |

Estado de **negócio/fluxo** (D) — vivem na sessão, escritos via `context_set`/`context_tags`/reason outputs.
Exemplos concretos: `session.numero_pedido`, `session.motivo_reembolso`, `session.numero_atual`,
`session.operadora_destino`, `session.confirmation_channel`, `session.contact_identifier`,
`session.customer_present`, `session.resume_origin`, `core.workflow.delegate_resume_token`,
`core.workflow.dialog_form_id`, `core.workflow.review_decision`/`current_round`/`max_rounds`/`reviewer_type`/`result_id`,
`session.escalar_solicitado`, `session.historico_mensagens`/`historico_resumo`, e as `session.survey_*` /
`session.campaign_id` / `session.delivery_id` dos flows de outbound/survey.

### `segment.{segId}.*` — isolado por participação (sessão · 4h)

O prefixo `segment.{segmentId}.` isola escritas de agentes paralelos numa conferência (não colidem). Em
inputs/visibilidade, `@segment.x` é resolvido pelo engine para `segment.{segId}.x` do agente em execução.

| Tag | Origem | Uso |
|---|---|---|
| `segment.{segId}.inviter_participant_id` | P (bridge) | quem convidou este especialista |
| `segment.{segId}.served_human_participant_id` | P (bridge) | qual humano este hook de wrap-up serve (fato **por-segmento** — nunca promover a session-global) |
| `segment.{segId}.<qualquer>` | D | espaço de trabalho isolado do agente (`scope: segment`) |

### `journey.*` — contexto compartilhado do PROCESSO (journey · 30d)

Vive em `{t}:ctx:journey:{raiz canônica}`, **compartilhado por todas as sessões da journey**. É o único
namespace cross-sessão e durável. Escrito por **D** (skills via `context_tags`/`context_set` com prefixo
`journey.`) e migrado na união (`journey_merge` → `migrateJourneyContext`, a raiz canônica vence). Lido por
`@ctx.journey.*` e pelo AI Gateway (`_build_journey_context_block`, ignora confidence < 0.3).

Exemplos: `journey.pedido_id`, `journey.numero_pedido`, `journey.operadora_destino`,
`journey.origin_process_session`. Regra: promova para `journey.*` **só** o punhado de fatos que um contato
**futuro/distinto** vai precisar ler — não o estado transiente do turno.

### `insight.*`

| Tag | Hash | TTL | Origem | Uso |
|---|---|---|---|---|
| `insight.conversa.*` | sessão | 4h | D (tool `insight_register`) | memória gerada na sessão atual; expira no fechamento. `insight_register` **rejeita** `insight.historico.*`. |
| `insight.historico.*` | **cliente** | **90d** | P/externo (jobs de memória) | memória de longo prazo do cliente; **agentes não escrevem** |

### `pricing.*` — hash do cliente (90d)

Namespace declarado, roteado ao hash do cliente. Populado pela integração de billing (P) — sem tags concretas
neste monorepo.

### Namespaces menores

| Namespace | Hash | Origem | Nota |
|---|---|---|---|
| `sla.*` | sessão | P | ex. `sla.breach_imminent` (declarado; pouco populado) |
| `queue.*` | sessão | P | na prática emitido como `session.queue.*` |
| `workflow.*` | sessão | D | declarado; estado de workflow |
| `approval.*` | sessão | D | ex. `approval.summary` (gate de promoção) |
| `@ctx.__gaps__` | — | P (computado) | **não persistido**: `GapsReport` de `required_context` (`missing`/`low_confidence`) |

> **Fora do ContextStore:** o opt-out global de contato (`do_not_contact {all?, channels?}`) **não** é tag de
> `:ctx:` — vive no cadastro de identidade (`identity.customers.attributes`), lido via channel-gateway. O índice
> de identidade (`{t}:identity:*`) também é chave própria, não `:ctx:`.

---

## Leitura

**`@ctx.<tag>` em inputs de `reason`/`invoke`** — resolve `ContextEntry.value`; ausente → `""` (nunca lança):

```yaml
input:
  nome_cliente: "@ctx.caller.nome"
  sentimento:   "@ctx.core.sentiment.category"
  pedido:       "@ctx.journey.pedido_id"        # lê do hash do processo
```

**Interpolação em `menu`/`notify`:** `"Olá, {{@ctx.caller.nome}}!"`

**`choice` sem LLM** (operadores: `exists`, `not_exists`, `eq`, `ne`, `gt`/`gte`, `lt`/`lte`, `confidence_gte`):

```yaml
- id: verificar
  type: choice
  conditions:
    - field: "@ctx.caller.cpf"
      operator: confidence_gte
      value: 0.8
      next: prosseguir     # já confirmado — não pedir de novo
  default: coletar_cpf
```

---

## Escrita

**`context_tags.outputs` em `reason`/`invoke`** (fire-and-forget; roteia `journey.*` sozinho para o hash do
processo):

```yaml
context_tags:
  outputs:
    cpf:        { tag: caller.cpf,        confidence: 0.85, merge: highest_confidence }
    pedido_id:  { tag: journey.pedido_id, confidence: 1.0,  merge: overwrite }   # → hash da journey
```

**`context_tags.inputs`** — leitura declarativa (injeta `@ctx.<tag>` no input antes do LLM/MCP).

**Imperativa via tool `context_set`** (steps `invoke`) e **supervisor `/api/inject-context`** — ambas passam
pelo helper único **`writeContextTag`** (J5a): `journey.*` → hash do processo (raiz canônica, 30d), demais →
hash da sessão. Nunca use `redis` direto:

```yaml
- id: gravar_pedido
  type: invoke
  target: { mcp_server: mcp-server-plughub, tool: context_set }
  input:
    session_id: "$.session_id"
    tenant_id:  "$.tenant_id"
    tag:        "journey.pedido_id"     # roteia para {t}:ctx:journey:{raiz}
    value:      "$.pipeline_state.pedido.id"
```

---

## Quando usar qual (regra de decisão)

| A pergunta | Onde colocar |
|---|---|
| Uma sessão **diferente** (agora ou depois) vai precisar deste fato? | `journey.*` (processo · 30d) |
| É só do turno/contato atual? | `session.*` (sessão · 4h) |
| É da fatia de um agente específico numa conferência? | `segment.{segId}.*` |
| É memória de longo prazo do **cliente**, independente do processo? | `insight.historico.*` (cliente · 90d) |
| É output interno de um step deste flow? | **não** é contexto — use `$.pipeline_state.*` |

Princípios:

- **Nada sincroniza `session.*` → `journey.*` automaticamente.** O hash da journey é um subconjunto **curado e
  explícito** — você *promove* um fato para lá de propósito. Por isso ele fica pequeno.
- **O produtor escreve; o consumidor lê por `@ctx`** — nunca alcance `$.pipeline_state.<outro_agente>` (frágil).
- **Fonte única**: se está em `journey.*`, não duplique em `session.*`.
- **`merge: highest_confidence`** para dados persistentes (não deixe a extração da conversa sobrescrever o CRM).
- **Nunca derive identidade de participante para um campo de escopo mais largo** — um fato por-segmento
  (`segment.{segId}.served_human_participant_id`) NÃO pode virar session-global (quebra em multi-humano).

---

## Padrão sugerido — briefing de journey para quem entra no meio

**Objetivo:** dar a um agente (IA ou humano) que assume o atendimento em **qualquer ponto** do processo o fio
do que já aconteceu, sem reconstruir nada. A tentação é fazer cada step "tracear" tudo em `journey.*`; **evite**
— isso incha um hash durável de 30d, transforma-o num log paralelo e duplica o que o stream/segments/`mcp.audit`
já registram (fonte única). `journey.*` é para **estado do processo**; trace/histórico é o **substrato**.

Forma recomendada:

- **Estado curado no `journey.*`** — padronize um punhado de chaves de alto valor, não um dump por-step:
  - `journey.objetivo` — o que o processo quer alcançar (escrito por N3 no início).
  - `journey.etapa_atual` — onde está agora (atualizado nas transições de etapa).
  - `journey.milestones` — append **só nas fronteiras** (fim de segmento / mudança de etapa), com teto — nunca a
    cada step.
  - `journey.resumo` — opcional; **um** campo de texto legível mantido por um step `reason` que **condensa**
    (não um append cru).
- **Histórico completo vem do substrato** — o "o que aconteceu passo-a-passo" já é `session:{id}:stream` +
  `ContactSegment` + analytics (ordenado, mascarado, fonte única). A entrada de um humano já é o briefing de
  `on_human_start`/copilot; o ganho é esse briefing **costurar o cabeçalho `journey.*` + o drill de histórico
  existente**, em vez de um trace novo por step.
- **Padronização por convenção, não por step** — o conjunto pequeno de chaves-padrão acima + escrita em
  milestone (via hook de fim de segmento) dá uniformidade sem o ruído (e o custo de prompt) de um trace integral.

Em uma linha: guarde no `journey.*` **o que o processo quer e onde está**; deixe **o que aconteceu** com o
stream/segments; faça o briefing de quem entra unir os dois.

---

## Referências de código

| Componente | Arquivo | Responsabilidade |
|---|---|---|
| SDK | `packages/sdk/src/context-store.ts` | `ContextStore` — roteamento hash/TTL, `set`/`get`/`getByPrefix` |
| SDK | `packages/sdk/src/context-accumulator.ts` | extração automática de MCP calls |
| MCP | `packages/mcp-server-plughub/src/tools/journey.ts` | `writeContextTag`, `resolveJourneyRoot`, `journeyCtxKey`, `migrateJourneyContext` |
| MCP | `packages/mcp-server-plughub/src/tools/session.ts` | tool `context_set` |
| MCP | `packages/mcp-server-plughub/src/server.ts` | `POST /api/inject-context` |
| Engine | `packages/skill-flow-engine/src/interpolate.ts` | resolução `@ctx.*` / `@ctx.journey.*` / `@segment.*` |
| Engine | `packages/skill-flow-engine/src/context-accumulator-util.ts` | escrita de `context_tags` (roteia `journey.`) |
| Routing | `packages/routing-engine/…/main.py` | `_write_pool_context` (`session.pool.*`, `session.queue.*`) |
| AI Gateway | `packages/ai-gateway/…/sentiment_emitter.py`, `copilot_emitter.py` | `session.sentimento.*`, `session.copilot.*` |
| Bridge | `packages/orchestrator-bridge/…/main.py` | tags de pré-hook/close/journey (`core.contact.close_origin`, `*_participant_id`, `root_session_id`) |
| Schema | `packages/schemas/src/context-store.ts` | `ContextEntry`, regex de tag, `required_context`, `__gaps__` |
| Smoke | `infra/test/smoke_journey_context.sh` | roteamento journey da escrita imperativa (J5a) |
