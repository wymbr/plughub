# Censo de cadastro do ContextStore — a lista da D9 por análise estática

> ⚠️ **Os nomes deste relatório são os de 2026-08-30 e NÃO foram reescritos.** A CNS-11
> (2026-09-01) migrou os 35 nomes da plataforma para o root `core.*` — `session.pool.id`
> virou `core.pool.id`, `session.close_origin` virou `core.contact.close_origin`, e assim
> por diante. Um censo é uma **medição datada**: reescrevê-lo faria o documento afirmar
> que mediu algo que não existia quando ele rodou. A tabela de-para vive em
> [`contextstore-core-namespace-spec.md`](contextstore-core-namespace-spec.md) §4.

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

## ✅ A FATIA 1 foi executada em 2026-08-30 — o que este documento passa a ser

Os **37** que este censo listou **foram cadastrados**, menos quatro deixados de fora
de propósito (abaixo). O mapa saiu de **75 canônicas / 53 aliases** para
**94 / 82**, e o número que a V4 espera caiu de **37 para 4**.

Ficaram FORA, e cada um tem dono:

| campo | por quê | onde a decisão mora |
|---|---|---|
| `session.preview` | o valor **é uma spec de mascaramento** (`{"numero_cartao": "last_4"}`), não um dado. Declará-lo como `texto` diria que a política é conteúdo | decisão aberta **#6** do ADR |
| `session.reviewer_id` | identidade de **usuário da plataforma**; nenhuma das 5 classes LGPD foi pensada para não-cliente. É lacuna do CATÁLOGO, e a ordem é catálogo antes do mapa | decisão aberta **#5** do ADR |
| `session.journey_demo_ping` | eco de **demo** (`skill_journey_demo_v1`) | override do tenant, nunca o seed |
| `session.journey_echo` | eco de **demo** (`skill_survey_outbound_v1`, do trio que nenhum pool deploya) | override do tenant, nunca o seed |

Os dois últimos são a única decisão de método nova desta fatia: `DEFAULT_CONTEXT_MAP`
é o **seed da PLATAFORMA**, e escrever nele instrumentação de demo colocaria detrito
no default de todo tenant. Que a auditoria siga acusando os dois é o comportamento
CERTO — eles são mesmo não declarados, no sentido que importa.

> ⚠️ **Um achado do próprio instrumento, e ele é da família *"teste que não pode
> reprovar"*.** Ao conferir o resultado, o censo publicou **80 aliases** contra os
> **82** do oráculo da TS, e mostrou `session.surveyed_*` como NÃO DECLARADO quando
> já eram alias. Causa: o parser do mapa era **line-based** e um `legado` com dois
> aliases quebra naturalmente em duas linhas — ele lia a primeira e descartava o
> resto **em silêncio**. Sub-contagem erra para o lado do trabalho a mais, o que a
> torna *simpática* e não menos falsa: ela inventaria pendência que não existe.
> Corrigido para juntar a folha por **saldo de chaves**. A conferência que pegou
> isso foi comparar o instrumento com o **oráculo**, que é outra implementação —
> um número sozinho não teria denunciado nada.

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
| **prosa** (LLM ou humano) | **8** | qual tipo — e é escolha, não bloqueio |
| política guardada como dado (`session.preview`) | 1 | onde isso mora, se é que mora no ctx |
| identidade de **usuário da plataforma** (`session.reviewer_id`) | 1 | nenhuma das 5 classes LGPD foi pensada para não-cliente |

> ⛔ **CORRIGIDO no mesmo dia.** A versão original desta seção dizia que os 8 de prosa
> *"pedem uma capacidade que o catálogo não tem"* e promovia a **D9.5** a pré-requisito.
> **Falso**, e conferido: `ContextMapFieldSchema.tipo` é `z.string()` validado contra o
> catálogo, então o mapa aceita qualquer um dos 13 tipos — `opaque` inclusive. **Todo campo
> tem onde ser cadastrado.** O que nenhum tipo entrega é granularidade *dentro* da prosa
> (mostrar o útil e esconder o CPF citado), e isso é limitação de UTILIDADE, não de
> segurança. A D9.5 foi **depreciada pela própria D9** — ver o ADR.
>
> Duas medições que a corrigem: **(a)** dos 8, **6 têm ZERO dado** no store vivo e os 2 que
> têm são **template autorado com um buraco tipado**
> (`R$ {{@ctx.session.limite_solicitado}}`), não prosa — a sensibilidade deles é DERIVÁVEL
> do buraco; **(b)** `session.summary` é lido através da porta de masking
> (`DialogFormRenderer.tsx:232`), então mascarar por default **apaga a tela de aprovação**.

O que fica verdadeiro é mais estreito: declarar tipo num campo de prosa é uma **aposta**,
não uma descrição. Isso muda o que a tela de cadastro deve DIZER a quem escolhe —
afordância, não mecanismo.

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
3. **D9.5 DEPRECIADA pela própria D9** *(corrigido no mesmo dia)*. Ela invocava um
   mecanismo inexistente — `formato.detect_pattern` tem **zero consumidores**, e o motor
   real roda num sítio só, sobre mensagens do stream, nunca sobre valor de ctx. **A
   migração não tem bloqueio.**
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
