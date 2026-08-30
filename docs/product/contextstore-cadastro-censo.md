# Censo de cadastro do ContextStore — a lista da D9 por análise estática

> **Medido em 2026-08-30**, sobre `packages/skill-flow-engine/skills/` (42 YAML) e os
> escritores de código de plataforma. É a fase de **análise estática** que a
> [D9](../adr/adr-contextstore-allowlist.md) prometeu no lugar do *loop-until-dry* da V4
> antiga.
>
> **Instrumento versionado e re-executável:**
> ```bash
> python3 infra/test/censo_contextstore_cadastro.py
> ```
> É **instrumento, não gate** — não há proposição que ele possa reprovar, e por isso não
> tem veredicto. Mas o único número dele que sustenta uma afirmação (`DINAMICOS: 0`) foi
> conferido por **mutação**: nome composto em runtime leva o contador a 1 e nomeia o
> sítio. Sem isso, o zero seria indistinguível de um detector morto.

---

## Veredicto

**A premissa da D9.2 sobrevive: ZERO nomes dinâmicos.** Em 42 skills e em todos os
escritores de código medidos, nenhum nome de tag é composto a partir de valor de
runtime — os dois casos de composição são `segment.{segId}.<folha literal>`, que é a
**família** que a D9.4 já prevê, com a folha literal.

**Mas a enumerabilidade não é obtenível caminhando a árvore do YAML**, e essa
diferença muda o desenho do portão. A D9 supunha DUAS superfícies de autoria. São
**SEIS**, e quatro delas são invisíveis a um analisador estrutural — um portão de
publish escrito da forma óbvia passaria por elas em silêncio, que é fail-open por
**invisibilidade**, não por decisão.

---

## As seis superfícies de escrita

| # | superfície | onde o nome vive | visível a um walker de YAML? |
|---|---|---|---|
| 1 | `context_tags.outputs` | `tag:` da anotação (+ prefixo `segment.` se `scope: segment`) | **sim** |
| 2 | `delegate.context` / `collect.context` | chave do mapa — **o gateway PREFIXA `session.`** | parcial (o nome final não está no arquivo) |
| 3 | `mention_commands.set_context` | chave do mapa | **sim** |
| 4 | `context_json` | **string JSON dentro do YAML** | **não** |
| 5 | `invoke tool: context_set` / `context_write` | `input.tag`, um campo de step como outro qualquer | **não** (exige conhecer a tool) |
| 6 | literal em código de plataforma | `hset(..., "session.x", ...)` | n/a — é a metade de seed |

E há a passagem que **nenhum portão de publish alcança**:

> **7. O corpo HTTP do webhook.** `POST /v1/channels/webhook/pool/{id}` recebe um objeto
> `context` e escreve **cada chave verbatim, sem prefixo** (`webhook.py:630`). É por aí
> que existem as duas únicas tags **sem namespace nenhum** — `campaign_id` e
> `target_pool` —, lidas por quatro skills. A rota é anônima por construção
> (§ *allowlist de sete prefixos* do `CLAUDE.md`).

---

## Os números

| medição | valor |
|---|---|
| nomes **escritos** (união das 6 superfícies) | **91** |
| — autorados pelo tenant (YAML) | 61 |
| — escritos por código de plataforma | 35 |
| já cobertos pelo `DEFAULT_CONTEXT_MAP` vigente | **54** |
| **NÃO declarados** — o trabalho de migração | **37** |
| lidos (`@ctx.`) sem escritor conhecido | 21 |
| **nomes dinâmicos** | **0** |

Mapa vigente de referência: **75 canônicas, 53 aliases**.

**Validação cruzada:** a auditoria ao vivo (4 fluxos de smoke, 2026-08-30) acusou 7
`unknown` — `session.title`, `session.summary`, `session.preview`, `session.status`,
`session.approval_threshold`, `session.root_session_id` e `approval.summary`. **Os
sete estão nesta lista.** O censo é superconjunto estrito do que o tráfego achou, e
por um fator de ~5 — que é exatamente o argumento da D9.2 contra descobrir por
observação.

---

## Quantos ficam sem tipo óbvio

Este é o número que dimensiona a decisão, e ele é bem menor que 37:

| classe | n | o que decidir |
|---|---|---|
| identificador, enum ou controle interno | **27** | nada — é `texto`, sem política |
| **conteúdo LIVRE** | **8** | ⚠️ **nenhum tipo do catálogo serve** |
| política guardada como dado (`session.preview`) | 1 | onde isso mora, se é que mora no ctx |
| identidade de **usuário da plataforma** (`session.reviewer_id`) | 1 | nenhuma das 5 classes LGPD foi pensada para não-cliente |

**São 10 decisões reais, e 8 delas pedem uma capacidade que o catálogo não tem** — a
marcação de *conteúdo livre* que a **D9.5** nomeia. Os oito:
`approval.summary` · `session.summary` · `session.parecer` · `session.resultado` ·
`session.pergunta_coleta` · `session.copilot.sugestao_resposta` ·
`session.copilot.acoes_recomendadas` · `session.copilot.flags_risco`.

Todos carregam prosa gerada por LLM ou redigida por humano, sobre uma conversa —
podem trazer qualquer PII. Tipá-los `texto` seria **claro por declaração**, que é pior
que claro por omissão: parece decidido.

> ⚠️ **Precedente medido, não introduzido aqui:** os gêmeos `journey.parecer` e
> `journey.resultado` **já estão no mapa como `texto`**. A lacuna é anterior a este
> censo; o censo apenas a conta.

---

## Duas reduções que a lista permite

**(a) A família `survey_*` × `surveyed_*` é UM fato com duas grafias.** Medido: o
bridge escreve `session.surveyed_segment_id`/`surveyed_agent_key` no `on_human_end`
(sessão de ORIGEM, `main.py:2239`); o gateway escreve
`session.survey_segment_id`/`survey_agent_key` no `collect_engage` (sessão da
PESQUISA, `webhook.py:2231`). Mesmo fato — *qual segmento/agente está sendo
pesquisado* —, sessões diferentes. Viram **aliases da mesma canônica**, exatamente
como `caller.cpf` × `session.cpf`.

**(b) `session.parecer`/`session.resultado` espelham `journey.parecer`/`journey.resultado`.**
Não são aliases (o escopo difere, e o escopo é o primeiro segmento por decisão da D2),
mas o **tipo é o mesmo** e a decisão é uma só.

---

## O que isto muda na D9

1. **D9.2 — confirmada, com emenda de método.** A população é estaticamente
   enumerável, mas o extrator **não é um walker de YAML**: precisa conhecer
   `context_json` (string), `input.tag` de `context_set`/`context_write` e a
   composição `session.{key}` que o gateway faz. Escrever o portão sem isso dá verde
   com quatro superfícies passando por baixo.
2. **D9.3 — a partição são TRÊS origens, não duas.** Plataforma (seed) × tenant
   (config) × **chamador** (corpo do webhook). A terceira não tem portão possível no
   publish; só resta a postura de runtime da D9.1 — grava, resolve restritivo e loga.
3. **D9.5 sobe de nota de rodapé a pré-requisito.** É a única das quatro decisões
   abertas que **bloqueia** a migração: 8 dos 37 não podem ser cadastrados sem ela.
4. **A D9.8 ganha evidência.** As duas famílias `survey_*`/`surveyed_*` que ela citava
   como sintoma estão medidas, e o cadastro as colapsa.

---

## Anexo — os 37 não declarados

| nome | origem | superfície |
|---|---|---|
| `approval.summary` | tenant | `invoke context_set` |
| `session.approval_threshold` | tenant | `delegate.context` |
| `session.copilot.acoes_recomendadas` | plataforma | `copilot_emitter.py` |
| `session.copilot.flags_risco` | plataforma | `copilot_emitter.py` |
| `session.copilot.mode` | tenant | `mention.set_context` |
| `session.copilot.sugestao_resposta` | plataforma | `copilot_emitter.py` |
| `session.copilot.ultima_analise` | plataforma | `copilot_emitter.py` |
| `session.current_round` | tenant | `invoke context_write` |
| `session.customer_present` | tenant | `delegate.context` |
| `session.journey_demo_ping` | tenant | `invoke context_set` |
| `session.journey_echo` | tenant | `invoke context_set` |
| `session.last_primary_agent_key` | plataforma | `main.py` |
| `session.last_primary_segment_id` | plataforma | `main.py` |
| `session.parecer` | tenant | `delegate.context` |
| `session.pergunta_coleta` | tenant | `context_tags.outputs` |
| `session.pool.agent_groups` | plataforma | `main.py` |
| `session.preview` | tenant | `delegate.context` |
| `session.process_outcome` | plataforma | `main.py` |
| `session.resultado` | tenant | `delegate.context` |
| `session.resume_origin` | tenant | `delegate.context` |
| `session.review_decision` | plataforma | `router.py` (evaluation-api) |
| `session.reviewer_id` | plataforma | `router.py` (evaluation-api) |
| `session.root_session_id` | plataforma | `server.ts` · `webhook.py` |
| `session.round_echoed` | plataforma | `router.py` (evaluation-api) |
| `session.spawn_reason` | plataforma | `webhook.py` |
| `session.status` | tenant | `delegate.context` |
| `session.summary` | tenant | `delegate.context` |
| `session.survey_agent_key` | ambos | `webhook.py` · `context_json` |
| `session.survey_customer_key` | tenant | `context_json` |
| `session.survey_form_id` | tenant | `context_json` |
| `session.survey_grain` | ambos | `webhook.py` · `context_json` |
| `session.survey_origin` | tenant | `context_json` |
| `session.survey_origin_pool` | tenant | `context_json` |
| `session.survey_pool_id` | plataforma | `webhook.py` |
| `session.survey_segment_id` | ambos | `webhook.py` · `context_json` |
| `session.survey_target_id` | plataforma | `webhook.py` |
| `session.title` | tenant | `delegate.context` |

## Anexo — os 21 lidos sem escritor conhecido

Oito **já têm canônica** no mapa (`hook.*` ×3, `journey.numero_pedido`,
`session.deploy_notes`, `session.deployed_by`, `session.skill_id`,
`session.sentimento.categoria`) — leitura de campo declarado cujo produtor está fora
das superfícies varridas, ou dead read.

Treze **não**: `campaign_id` · `target_pool` · `session.contact_outcome` ·
`session.historico_mensagens` · `session.historico_resumo` ·
`session.jornada_registrada` · `session.max_rounds` · `session.pool_ids` ·
`session.result_id` · `session.reviewer_type` · `session.wrap_up_auto_attend` ·
mais os dois truncados `hook` e `journey` (prefixo lido sem folha — artefato de
interpolação, não tag).

⚠️ **Ler-sem-escritor não é o mesmo defeito que escrever-sem-cadastro**, e a D9 só
gateia o segundo. Cada um destes é *ou* um produtor fora das seis superfícies *ou* um
dead read — e distinguir os dois é trabalho de outra passada, não desta.
