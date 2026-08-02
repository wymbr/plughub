# TODO — PlugHub Itens Pendentes

> Itens genuinamente não implementados. Histórico de implementações concluídas em `CHANGELOG.md`.

---

## I5 — encerramento de trabalho author-bound *(núcleo A+B ✅ 2026-07-30; resta o relatório)*

Fase final da ADR [`adr-internal-work-queue-author-bound`](docs/adr/adr-internal-work-queue-author-bound.md).
**Núcleo A+B entregue** (ver CHANGELOG): ledger `{t}:work_task:{session}`, `Router.work_task_expire`
+ `POST /v1/work_queue/expire`, expire em todo resume, gatilho de supervisor no BFF, TTL do JSON da
fila alinhado ao prazo, três `close_reason` distintos. Smoke `infra/test/smoke_acw_expire.sh`.

### Falta

- **Relatório de pendências por agente** — **desenho fechado 2026-07-30** (ADR § D7b); **fatia 1
  entregue 2026-07-30**, fatias 2 e 3 pendentes. A lacuna 5 deixou de bloquear: o **ledger
  `{t}:work_task:{session}` da I5 é o índice de
  pendência por construção** (nasce no despacho, morre no resume; o claim NÃO o apaga → cobre as duas
  formas com uma linha só) e carrega `assigned_to`.

  | Fatia | Entrega | Estado |
  |---|---|---|
  | 1 | **Pendências agora** — `GET /api/work_queue/pending` no BFF (`SCAN` do ledger + cruzamento ZSET/lease/`dispatch_mode`) + **Monitor › Pendências** (`/monitor/work-items`), agrupável por agente ou pool, com o encerramento pelo supervisor ligado | ✅ **2026-07-30** (smoke 11/11; ver CHANGELOG) |
  | 2 | **Histórico do caso reivindicado** — `GET /reports/wrapup-summary` + **Analítico › Histórico de Wrap-up** (`/analise/wrapup`), agregado por agente/pool com `unfilled_rate` | ✅ **2026-07-30** (sonda 7/7; ver CHANGELOG) |
  | 3 | **Histórico do nunca-reivindicado** — evento `work_item.expired` → ClickHouse. **Gated:** só se a fatia 1 mostrar volume. Nas medições da Camada F quase toda expiração foi de item reivindicado | **não construir sem medir** |

  **Primeira medição do gate (2026-07-30, sonda da fatia 2):** 9 wrap-ups no período — 7 submetidos,
  2 vencidos, `unfilled_rate` **22,2%**. Os 2 vencidos são **reivindicados** (têm segmento, senão não
  apareceriam nesta contagem), o que reforça o achado da Camada F e mantém a fatia 3 fora de escopo.
  O que ainda não se mediu é o **nunca reivindicado** — que por construção não aparece aqui; esse
  número só sai olhando o Monitor › Pendências dentro da janela de 25 h.

  **Escopo da fatia 1 — só wrap-up, e por quê.** O ledger é genérico (`_write_work_task`
  é incondicional nos DOIS handlers de delegate, e o próprio docstring assume pool push),
  então ele indexa também aprovação e delegate a especialista IA. A tela corta pelo sufixo
  `-int`, que a **D6 tornou garantia por construção** (o registry rejeita criação manual com
  ele). O critério não é arbitrário: aprovação é **pooled** e tem transbordo por
  `fallback_to_pool_after_s` — ninguém fica preso nela; wrap-up é **author-bound** e sem
  transbordo, que é a razão de a D4 pedir o relatório. `?all=1` derruba o filtro para
  diagnóstico.

  **Quatro estados, e o quarto é o achado.** `unclaimed` (no ZSET) · `claimed` (lease) ·
  `not_queued` (pool push) · **`orphaned`** — pool *pull*, fora do ZSET e **sem lease**, isto
  é: a lease venceu e nada devolveu o item à fila. É a **lacuna 2** (não há reaper de
  `claim_lease`), que a Camada F deixou sem instrumento. Colapsá-lo em `not_queued` o
  esconderia atrás de um valor plausível. Há ainda `unknown` para pool sem `pool_config` no
  cache — ausência de infra não é presumida como "push". Se `orphaned` aparecer com volume, a
  discussão do reaper passa a ter número.

  **Não criar segmento sintético** para o item nunca reivindicado: nenhum valor de `duration_ms` é
  honesto ali (`0` dilui o ACW que a E2f fez existir; a janela de pendência vira tempo de trabalho;
  `NULL` queima a assinatura que achou os 87 órfãos). Segmento = participação; pendência = item de
  trabalho, com dono/prazo/tempo parado que segmento não comporta. Discussão completa no ADR § D7b.

  **Achados da fatia 1 — dois limites que a tela declara em vez de esconder:**

  1. **O relatório é uma JANELA de ~25 h, não um acumulado.** O ledger nasce com
     `ex = timeout_hours*3600 + 3600` (`webhook.py:1012` e `:1787`) — 25 h no wrap-up default.
     No caminho normal a linha morre antes disso, no resume (o `handle_resume` apaga o ledger),
     e o buffer de +1 h existe justamente para o TTL não ganhar do timeout scanner. **Mas se o
     scanner não passar** (serviço fora, ou o intervalo de 60 s), a pendência **desaparece da
     tela sem deixar registro nenhum** — nem em `segments`, porque item nunca reivindicado não
     tem segmento. Consequência para o gate da fatia 3: "medir antes de construir" significa
     *olhar a tela e anotar*, não *deixar acumular*; nada acumula.
  2. **O nome do agente depende de um grant que o público da tela pode não ter.** `assigned_to`
     é `user_id` (derivado de `human-{uid}`, `main.py:1517`), e `/auth/users` exige ABAC
     `config.usuarios` (strict, sem bypass de admin) — que o supervisor típico não tem. A tela
     degrada para o `user_id` cru **exibindo o motivo**, em vez de mostrar UUID sem explicação.
     A alternativa Redis (`{t}:instance:human-{uid}` → `user_login`) foi **descartada**: aquela
     chave é heartbeat de 30 s e some no logout, ou seja, falharia exatamente na linha mais
     interessante — a pendência de quem já saiu. Conserto real (se incomodar): ou um endpoint
     de diretório mínimo com grant próprio, ou carimbar `user_login` no ledger no despacho
     (mudança de produtor, só vale para itens novos).
- ~~**Bloco C da sonda de prosa**~~ ✅ **2026-07-31** — exercitado (4/4, resolvido E não-resolvido).
  Ver CHANGELOG: a sonda tinha **dois defeitos que a impediam de reprovar**, corrigidos antes da
  medição.
- ~~**Cenários `claimed` e `orphaned` do relatório**~~ ✅ **2026-07-31** — rodados com
  `INSTANCE=human-<user_id>` de agente logado: **14/14**. `claimed` (fora do ZSET, com lease,
  `claimed_by` correto) e `orphaned` (lease apagada sem re-enfileirar) foram **vistos acontecer**. O
  estado `orphaned` deixa de ser instrumento não calibrado e passa a valer como medida da **lacuna 2**.
- **Validação ao vivo do gatilho de prazo.** O smoke exercita o gatilho de supervisor; o de prazo
  depende do scanner de 60 s. ✅ o prazo virou config do pool (`PoolHookEntry.context.acw_timeout_hours`
  → `@ctx.hook.acw_timeout_hours`), então encurtá-lo para medir na Camada F é edição de pool via PUT,
  sem tocar em skill nem em slot.
- **Cenário reivindicado no smoke** — só roda com `INSTANCE=human-<user_id>` de um agente logado.

### Lacunas do levantamento que seguem abertas

| # | Lacuna | Evidência |
|---|---|---|
| 2 | **Não há reaper de `claim_lease`** | nenhum poller varre `*:pool:*:claim:*`; a lease expira passivamente. Defeito da família pull inteira (aprovação também), não do wrap-up. ✅ o docstring que **afirmava** existir heartbeat + auto-release foi corrigido (`registry.py:82`). A Camada F **não** o mediu (a sonda observou a chave de outra sessão); **agora há instrumento**: o estado `orphaned` do relatório de pendências é exatamente esta condição — item de pool pull fora do ZSET e sem lease. ✅ **instrumento CALIBRADO em 2026-07-31** (smoke 14/14 — a condição foi vista acontecer, não só o classificador lido). Decidir o reaper só depois de ver o volume **em uso normal**, que ainda não se mediu |
| 3 | **O TTL de fila existente nunca alcança fila pull** | `routing-engine/main.py:1253` pula `dispatch_mode=pull` **antes** da varredura de `max_wait_exceeded` — `queue_config.max_wait_s` não se aplica. O prazo do item hoje vem do `timeout_hours` do delegate, não da fila |
| 4 | **Nenhuma ação de terceiro encerra item de tarefa** | ✅ resolvido para a fila pull (`/api/work_queue/expire/:sessionId`). Seguem inertes: `/v1/workflow/instances/:id/cancel` = **410 hard**; `POST /api/force-complete` só reescreve uma chave Redis (sem evento, sem fila, sem vaga) |
| 5 | ~~**A fila pull não é consultável pelo analytics**~~ | ✅ **resolvido para a pergunta operacional (2026-07-30)**: `GET /api/work_queue/pending` varre o ledger `{t}:work_task:*` e cobre as duas formas de pendência com uma linha só (o claim não apaga o ledger). Segue sem evento/tabela espelho — o histórico do **nunca-reivindicado** continua sem fonte (fatia 3, gated) |
| 6 | **`close_reason` de segmento não tem enum** | `contact-segment.ts:83` é `z.string()` livre; `task_submitted`/`session_teardown`/`acw_expired`/`acw_supervisor_closed` são literais no publish do bridge. O enum fechado (`CloseReasonSchema`, `common.ts:44-56`) é o de SESSÃO — domínio diferente. Hoje quem enumera o domínio o descobre por arqueologia |

### Timeouts ainda constantes no caminho da I5 *(arco de consolidação de config)*

Auditoria do caminho todo (2026-07-30). `claim_lease_s` já é config (`routing`); o
`delegate.timeout_hours` é dado de autoria e agora aceita ref. Restam três, todos com casa natural
no namespace `session` (cujos seeds já dizem "currently hardcoded — migrating"):

| Onde | Valor | Chave candidata |
|---|---|---|
| `add_queued_contact(ttl=14_400)` — routing-engine `registry.py` | 4 h | `routing.queue_contact_ttl_s` |
| Buffer `+3600` no TTL do item — channel-gateway `webhook.py` **e** registry (duplicado) | 1 h | `session.work_item_ttl_buffer_s` |
| `run_timeout_scanner(interval_s=60)` — chamado sem argumento em `main.py:374` | 60 s | `session.timeout_scan_interval_s` |

O terceiro é o que mais importa: **é política, não infra** — define a granularidade de toda
expiração da plataforma, e hoje ninguém pode afrouxá-la ou apertá-la sem rebuild.

### Dívida de segurança encostada nesta fatia

`mcp-server-plughub` **não recebe `PLUGHUB_JWT_SECRET`** no compose da demo, então
`verifyJwtPayload` cai no fallback de desenvolvimento (decodifica sem verificar assinatura) — vale
para TODAS as rotas de UI do BFF, incluindo o novo gate `supervisor|admin`. Não foi wirado aqui de
propósito: ligar o segredo muda o comportamento de autenticação do BFF inteiro e merece fatia
própria, não um efeito colateral da I5. Enquanto isso, o gate é de intenção, não de autenticação.

---

## Wrap-up unificado — resíduos após a Phase 2 ✅ *(arco fechado 2026-07-27, ver CHANGELOG)*

**Polish (não bloqueia):** latência do auto-atendimento (~2-3s do poll da inbox) → instantâneo bombando o
`refreshSignal` do `PullInboxPanel` no `conversation.assigned`. **Agora é seguro**: antes da Phase 2 o claim
instantâneo AUMENTARIA a chance de chegar antes do release (`-1` → cai na inbox); com o hold, as duas ordens
são cobertas. E: UI para a config de `dispatch` inline/detached do hook (hoje só YAML — invariante "config
UI-editável" pendente para hooks de pool).

**Camada E2 restante:** ~~**E2f**~~ ✅ (2026-07-29) · ~~**Camada F**~~ ✅ **2026-07-30** (F1 atribuição,
F2 G1 no relatório, F3 pull direcionado 5/5, F4 expiração — ver CHANGELOG). **Arco A–F completo.**
Resíduo da F: a **lease** não foi medida (a sonda observou a chave de outra sessão) — a lacuna 2
segue como estava, e o que ficou provado é que o **prazo** devolve a vaga.
*(E2e — produtor do marker `acw_pending` — **saiu de escopo** com a remoção da Camada C, 2026-07-29.)*

**Cleanup:** ~~`infra/test/smoke_acw_gate.sh` órfão~~ — não existia mais (item stale) · ~~`acw_gate` como config
sem leitor~~ ✅ **removido ponta a ponta (2026-07-29, ver CHANGELOG)**: schemas, Prisma (migration
`20260729000000_drop_pool_acw_gate`), `pools.ts`, routing-engine, platform-ui (tipo, `PoolsPage`, i18n en+pt-BR)
e as 4 superfícies de doc. **Não reviver o enum** — um gate de ACW futuro se desenha sobre a VAGA.

---

## Wrap-up como fonte de dados — arco de 4 fatias *(discussão 2026-07-29, fatia 1 em curso)*

> **Origem:** a E2f começou como "tirar a sessão de wrap-up da contagem de TMA" e a discussão a
> reenquadrou. A sessão de wrap-up não é ruído a excluir — é **fonte de dados** (serviços
> executados, FCR, motivo), cruzável com Evaluation. Isso muda a ordem: garantir que o dado seja
> gravado de forma consultável vem ANTES de construir relatório, senão os primeiros meses de
> histórico se perdem.

**Achado que motiva o arco:** o `segment_outcome_record` (`tools/segment.ts:67-75`) tem contrato
**fixo de 4 campos** (`classificacao`/`resumo`/`escalation_reason`/`proximos_passos`) e tudo
desemboca em `outcome`/`issue_status`/`handoff_reason` (texto livre concatenado). O DialogForm, ao
contrário, é genérico: dá para acrescentar "serviço executado" no editor hoje — e a resposta
**some sem log**, porque o skill não passa e a tool não aceita. Formulário genérico × tool de
contrato fixo = funil que descarta em silêncio.

> **✅ A perna do descarte foi CONSERTADA em 2026-07-30** (ver CHANGELOG): `resumo` e
> `proximos_passos` agora têm colunas próprias (`segments.wrapup_summary` /
> `wrapup_next_steps`) e são gravados em TODA disposição. O que **permanece** deste arco é o
> outro lado do funil: campo NOVO acrescentado no editor do DialogForm segue sem chegar à
> tool (contrato fixo de 4 campos) — é a fatia 3.

**Evidência ao vivo (F1, 2026-07-30)** — o funil é mais estreito do que "campo novo no editor":
descartava campo que o formulário JÁ TINHA. Wrap-up submetido com `resumo="zxzxzx"` e
`proximos_passos="wwww"`; o segmento da origem gravou

```
outcome: resolved   issue_status: resolvido   handoff_reason: NULL
```

porque a tool só montava `handoff_reason` quando `outcome !== "resolved"`. Num atendimento
**resolvido** — o caso mais comum — o resumo que o atendente escreveu não ia a lugar nenhum, e a
tela não dava nenhum sinal disso. O `issue_status` (classificação crua, em português) é o campo
que prova a atribuição por referência: nada mais no sistema o escreve.

**Conserto (2026-07-30):** colunas próprias `wrapup_summary`/`wrapup_next_steps`, escritas em toda
disposição pelos DOIS produtores (destacado e inline). **`handoff_reason` ficou intacto de
propósito** — ele define `handoff_rate` (`countIf(handoff_reason != '') / count()`), e escrever o
resumo ali levaria a taxa de repasse a ~100%: trocaria perda silenciosa por métrica que muda de
sentido sem avisar. Prosa também não caberia em `agent_business_events` (D2: `value` é numérico,
nominal vive na categoria). Sonda `infra/test/check_wrapup_prose_persisted.sh`.

**~~Resíduo~~ — era STALE, medido e derrubado em 2026-07-31.** A nota dizia que o caminho
**inline** (`_apply_wrapup_to_segment`) só conhece `wrapup_resumo`, e que portanto
`wrapup_next_steps` só seria preenchido pelo destacado. A sonda mostrou o contrário: os dois
atendimentos (um `resolvido`, um `escalado`, ambos pelo hook `dispatch: inline`) gravaram
`wrapup_next_steps`. Um campo que aquela função **não recebe na assinatura** não poderia estar ali —
logo o produtor foi o `segment_outcome_record`.

**Causa da defasagem:** a Phase 3 (wrap-up unificado) aposentou o inline antigo, e o inline de hoje é
**auto-atendimento sobre a mesma máquina destacada** — mesmo `skill_wrapup_detached_v1`, mesma tool.
`_apply_wrapup_to_segment` (`main.py:3010`, acionado em `process_routed` por
`pipeline_state.results.wrapup_classificacao`) servia o especialista de conferência `wrapup_ia`, que
saiu do `tenant_demo.yaml:445`. O único emissor daquela chave é `agente_wrapup_v1.yaml`, que **nenhum
pool deploya** (`grep` em `infra/` só acha o comentário de remoção).

**Consequência a tratar:** `_apply_wrapup_to_segment` e `agente_wrapup_v1.yaml` são candidatos a
**código morto** — sem produtor vivo. Não remover sem confirmar que nenhum tenant fora do demo
deploya o skill; enquanto ficarem, ensinam um modelo que não é mais o corrente (foi exatamente o que
produziu esta nota errada).

### Fatias

| # | Entrega | Estado |
|---|---|---|
| 1 | **E2f** — atributo `purpose: contact\|internal` no pool + filtros no analytics | ✅ 2026-07-29 (resíduo: TMA por agente sobre `segments`) |
| 2 | **Arc 12 `segment_id`** em `agent_business_events` (plano A+C já decidido, seção própria) | pendente — **pré-requisito das fatias 3 e 4** |
| 3 | **Capture de wrap-up** — tipo de captura nominal no editor + roteamento no `segment_outcome_record` | pendente (ADR) |
| 4 | **Relatório de wrap-up** — cai sobre `/reports/agent-events/*` (série/summary/categorias já existem) | pendente |

### Decisões fechadas na discussão

**D1 — o sink roteia por QUEM RESPONDE, não por que métrica é.** O `DialogCapture` já foi desenhado
assim (`dialog.ts:109-112`: *"echoed back to the domain… the domain routes it to its sink"*).

| Captura | Quem responde | Sink |
|---|---|---|
| CSAT/NPS/CES de survey | o **cliente** | `session_signal` → Voz do Cliente (máquina de `dimension`) |
| FCR, serviço, motivo do wrap-up | o **atendente** | `agent_business_events` (Arc 12) |

Violar isso faz a superfície "Voz do Cliente" exibir declaração de atendente como se fosse do
cliente — e contamina a série histórica, que é irreversível. *(Correção registrada: a ideia inicial
de pôr FCR no catálogo de instrumentos do editor pegava o mecanismo certo e o sink errado — o
catálogo desemboca em `session_signal`.)*

**D2 — dentro do `agent_event`, pontuável × nominal é só onde o dado mora.** `value` é
`z.number().finite()` (`agent-events.ts:92`) e o relatório **não agrupa por tag**
(`VALID_GROUP_BY = {category, skill_id, pool_id, agent_type_id}`, `reports_query.py:5684`) — tag
seria gravada e invisível. Logo:

- **pontuável** (FCR): categoria fixa + `value` numérico → `avg_value` do summary **é** a taxa.
- **nominal** (serviço, motivo): folha na **categoria** (`l4`, a regex aceita 2–5 segmentos e a
  convenção usa 3) + `value: 1` → `count` por categoria. Multi-select = N eventos.

**D3 — o roteamento mora na TOOL, não no YAML do skill.** Se o skill passar campo a campo, cada
pergunta nova no editor vira edição de skill + `set-next` + `promote`, e o formulário deixa de
dirigir — que era o ponto. Precedente: `survey_record` compõe server-side (D9 do ADR de scoring).
Corolário de governança: a folha nominal deve vir do **`options[].value` do DialogForm**, que é a
lista controlada, versionada e UI-editável. Só a tool tem como derivá-la. Sem isso a regex valida
só o formato e `troca_titularidade` × `troca_de_titularidade` viram duas séries que nunca
reconciliam.

**Brinde de D1+D2:** FCR passa a ter três fontes independentes — **declarado** (agente, wrap-up),
**percebido** (cliente, survey) e **observado** (voltou na janela? `root_session_id` da Journey, já
existe). A divergência entre elas é indicador de qualidade melhor que qualquer uma isolada, e cruza
com Evaluation pelo mesmo `segment_id`.

### ⚠️ Questão ABERTA — serviços executados por múltiplos agentes *(marcada 2026-07-29, discutir)*

Num atendimento orquestrado, **vários serviços** são executados e **especialistas** (IA ou humanos)
executam parte deles. Como consolidar isso num wrap-up?

**Posição preliminar (a validar na discussão):** não se consolida *dentro* do wrap-up. Serviço
executado é fato de **(segmento, momento)** — quem executou sabe, e sabe na hora. O humano no fim
não sabe o que o especialista de IA fez três passos atrás; pedir que re-declare é lossy por
construção e duplica um fato que já existe. É o invariante do CLAUDE.md (*nunca guardar fato de
escopo estreito em campo de escopo largo — derivar onde o escopo é conhecido*).

Consequência: cada agente emite `agent_event` **no seu próprio segmento**, e "serviços do contato"
é a **união sobre os segmentos da sessão** — uma query na leitura, não um campo de formulário. O
wrap-up fica com o que só o humano sabe no fim (disposição, FCR declarado, resumo).

Isso **eleva a fatia 2**: sem `segment_id` no `agent_business_events`, as marcações de todos os
agentes caem na mesma sessão sem dizer quem executou o quê. O item do Arc 12 deixa de ser só
"destrava o cruzamento com Evaluation" e vira pré-requisito da própria contabilização de serviços.

**Desdobramento de UI a discutir:** se os serviços já estão marcados, o formulário de wrap-up pode
**exibi-los** (o briefing já carrega contexto da origem) para o humano confirmar/complementar, em
vez de digitar do zero.

---

## ~~`close_reason` do contato só é persistido se o wrap-up for submetido~~ ✅ *(2026-07-30, ver CHANGELOG)*

Resolvido pela opção **(a)** — `close_reason` viaja no `participant_left` do fechamento canônico,
nos dois produtores. Validado com dois atendimentos na mesma janela, um COM e outro SEM wrap-up,
ambos gravando `agent_hangup`. Sonda: `infra/test/check_close_reason_persisted.sh`.

**Resíduo (não bloqueia):** o `_TRANSPORT_TO_CLOSE_REASON` cobre 6 transportes; qualquer outro
agora produz `close_reason` ausente **com WARNING** em vez de um `agent_hangup` inventado. Se o
WARNING aparecer em produção, completar o mapa — a sonda tem uma asserção que o varre nos logs.

---

## Capacidade, licenças e isolamento entre pools *(desenho FECHADO 2026-07-31 — implementação não iniciada)*

> Começou como "recontagem de recursos" e a pergunta *"a alocação usa os mesmos números errados?"*
> abriu **três** problemas distintos. O desenho dos três está fechado em documento próprio; aqui fica
> só o estado, a evidência medida e a ordem.
>
> · **Desenho de relatório:** [`docs/product/shared-capacity-pool-as-tag-design.md`](docs/product/shared-capacity-pool-as-tag-design.md)
> · **ADR de licenciamento:** [`docs/adr/adr-agent-licensing-and-pool-isolation.md`](docs/adr/adr-agent-licensing-and-pool-isolation.md)

| | Problema | Severidade | Onde |
|---|---|---|---|
| **A** | relatório mente: `available` por pool ignora consumo dos irmãos; KPI soma recurso compartilhado | média — corrompe **afirmação ao cliente** (`queue_context_get`, `system_availability_check`), não alocação | doc de produto |
| **B** | teto de licença mistura moedas (`C = ai + human`) e gateia sessão humana; mede em sessões o que foi contratado em instâncias | **alta — rejeita contato real com capacidade ociosa** (`shared_full` → outage) | ADR §1.3 |
| **C** | piso/teto por pool, licenças materializadas, cerimônia de deploy | — é **capacidade nova**, não conserto | ADR D4/D10/§10 |

**Sintoma que iniciou tudo:** agente `max_concurrent 3` logado em `retencao_humano` +
`retencao_humano-int`; durante um wrap-up o pool PAI seguia exibindo `0 busy / 3 available / 3 total`,
e o KPI somava `Available 9` para 3 pools que compartilham o mesmo humano de 3 vagas.

**Enquadramento:** não é "capacidade duplicada" — capacidade compartilhada está **certa** (o agente
atende contato de qualquer pool em que está logado). O defeito é o **consumo não propagado**: vaga
tomada por um pool não desconta nos irmãos. `active_count` é contador **por pool**
(`get_busy_count`, `registry.py:1487`); a ocupação real vive no semáforo do RECURSO
(`{t}:instance:{iid}:sessions`), que nenhum snapshot lê. Viola o invariante do `CLAUDE.md`
*"capacidade é do RECURSO e não fragmenta por pool"*.

**Por que apareceu agora:** o espelho `-int` (ADR author-bound, D2) tornou **todo** humano multi-pool
por construção. Antes era exceção.

**Achado que ordena o trabalho:** o defeito **A é do lado HUMANO** (a linha de base medida é 1 humano
em 3 pools; o `-int` também) e o **B/C são de IA** (humano não tem licença por sessão — ADR D2). Os
dois arcos quase não se sobrepõem: dá para fazer os dois sem retrabalho, desde que o relatório **de
IA** (que lê os conjuntos de licença) venha depois das licenças existirem.

### Linha de base medida (2026-07-31) — 3 números para o mesmo fato, e um gatilho que não existe

Medição ao vivo com **1 humano logado** (`max_concurrent 3`) e **1 vaga ocupada** no semáforo
(resíduo do cenário E do `smoke_work_task_pending.sh` — a lacuna 2 acontecendo):

| Superfície | Diz | Verdade |
|---|---|---|
| `retencao_humano` snapshot | `available 3, busy 0` | 2 |
| `retencao_humano-int` snapshot | `available 3, busy 0` | 2 |
| Soma na tela | **6** | **2** |

O registro da instância declara `pools: ["retencao_humano","aprovacao_deploy"]` e o espelho `-int` é
auto-provisionado por cima ⇒ **3 pools para um humano de 3 vagas**. É exatamente a origem do
`Available 9` relatado no topo desta seção.

**Achado 1 — são TRÊS representações do mesmo fato, e a que o snapshot lê é a que já divergiu.**

| Representação | Onde | Valor medido |
|---|---|---|
| contador por POOL | `get_busy_count` → `busy` do snapshot | **0** ❌ |
| contador por INSTÂNCIA | `current_sessions` no registro da instância | 1 ✅ |
| SET de ocupantes | `SCARD {t}:instance:{iid}:sessions` | 1 ✅ |

Consequência de desenho: derivar do **SET**, não adotar `current_sessions`. O contador da instância
está certo *hoje*, mas é da mesma família do contador por pool — número paralelo que pode derivar.
Trocar um contador por outro não fecha a classe de defeito; só muda qual deles vai mentir depois.

**Achado 2 — o conjunto de gatilhos do desenho é insuficiente.** A proposta acima diz "trocar remendo
por recálculo **nos mesmos gatilhos que hoje remendam**". Mas o **claim/release de item de fila pull
não é um desses gatilhos**: não remenda nem recalcula — `write_pool_snapshot` tem **um único call site
no router** (`router.py:215`, dentro de `route()`), e `work_task_claim`/`work_task_release` não passam
por lá. Os dois snapshots medidos são de 11:29, **anteriores** ao claim das 11:34; o `formfill_demo`
prova o simétrico, anunciando `queue_length: 1` de uma fila já esvaziada. A lista de gatilhos precisa
**ganhar** claim e release — e, como a I5 tornou todo humano multi-pool por construção, essa via
deixou de ser exceção.

**Achado 3 — o TTL amplifica o achado 2 em 30×.** O snapshot vive **3600 s**, não 120 s: o código
sempre usou o default `snapshot_ttl=3600`, e três docs diziam 120 s (`registry.py:132`, o docstring de
`write_pool_snapshot`, e `CLAUDE.md` § Operational Visibility) — corrigidos em 2026-07-31 após medir
`TTL 2958` numa chave escrita 11 min antes. Com 120 s um snapshot obsoleto se auto-curaria expirando;
com 1 h ele **persiste**, e a tela mostra capacidade de uma hora atrás.

**Detalhe lateral que reforça "pool é TAG":** `formfill_demo` tem `total_instances 0` e ainda assim
teve item reivindicado por esse humano. Aqui é artefato do smoke (passa `instance_id` explícito), mas
vale o registro: nada no caminho do claim exigiu membership.

### Prioridade — medida, não presumida

- **A não gateia roteamento**: `admission.py` não lê `available`; o árbitro é o semáforo via
  `claim_instance`. Sem super-alocação. Mas alimenta `queue_context_get` / `pool_status_get` /
  `system_availability_check` (`tools/operational.ts`), que os Skill Flows usam para *oferecer troca de
  canal e informar tempo de espera* — **ao cliente**. Corrompe afirmação, não alocação.
- **B gateia**: é o único item que hoje **rejeita contato real**. 10 licenças humanas
  (`max_concurrent 3`) + 10 de IA ⇒ `C = 20` gasto em sessões, quando só os humanos serviriam 30.

### Decisão de execução (2026-07-31): ir para o modelo novo, sem trabalho de preservação

Não há compromisso assumido nem necessidade de compatibilidade retroativa. Quase tudo que estava
planejado como incremental existia **só** para proteger compatibilidade — logo é custo puro. Some:
a migração `floor = ceiling = session_reservation`; a separação L2/L3 (materializar **é** o conserto de
unidade); a redução gradual do D5 aritmético.

**Ordem (revisada após a medição de 21:20):**

1. **Tag de pool no membro do semáforo** — pré-requisito da reconstrução de licença (ADR §8/§10.3),
   sem mudança de comportamento. Invariante: release é por **prefixo** `{session_id}::`, então o pool
   entra como **sufixo**; regra unificadora = *pool é sempre o 3º campo `::`*, o que preserva também o
   parse de expiração do hold.
   **✅ CONCLUÍDA E VALIDADA (2026-08-02)** — ver `CHANGELOG.md`. As-built em `registry.py`: `_occupant_id`
   ganhou `pool_id`; `_CLAIM_INSTANCE_LUA` troca `SISMEMBER` exato por hit de **prefixo de identidade**
   `{session}::{conf}::` (+ o membro legado de 2 campos, para a janela de migração) e faz **RE-TAG**
   (SREM+SADD) em hit com pool diferente; `_SWAP_TO_HOLD_LUA` monta o membro do hold **no Lua**,
   herdando a tag do occupant removido (nenhum `pool_id` atravessa `remove_conversation`); parse único
   em `occupant_pool(member)` → `None` para untagged. Call sites: `router.py` ×3 (`route`,
   `_try_affinity`, `work_task_claim`). Verificação: 10 testes novos em `test_instance_semaphore.py`
   (24 no arquivo, 44 na suíte, 0 skipped) + prova de reprovação por **mutação** (os testes F1 não podem nascer
   vermelhos contra o código antigo — lá o `pool_id` nem existe): idempotência ← `SISMEMBER` exato
   derruba o re-tag com `SCARD 2`; tag **depois** do timestamp no hold derruba 5, dois deles
   **pré-existentes**, pelo modo perigoso (`exp = nil` → hold vivo tido por expirado → push rouba a
   vaga do wrap-up).
2. **Relatório (A)** — recompute + fan-out por recurso + rollup **por tipo de licença**; bootstrap
   deixa de afirmar capacidade. É o defeito **efetivamente observado**, e a medição mostrou que o
   bootstrap é hoje o escritor principal do snapshot.
   **✅ F2 CONCLUÍDA (2026-08-02)** — ver `CHANGELOG.md`. `available`/`busy` derivados do semáforo do
   recurso (`_RECOMPUTE_POOL_OCCUPANCY_LUA` → `compute_pool_occupancy`); `busy_elsewhere`+`untagged`+
   `model` no snapshot; fan-out `pools(instance) ∪ {pool_id}` em `mark_busy`/`remove_conversation`/
   `release_session_from_pool`; `active_count` **removido ponta a ponta** (chave, helper,
   `get_busy_count`, INCR/DECR/clamp e o patch `available += 1`) com **todos** os leitores
   reapontados — sampler do routing, `/v1/operational/pools` (`active_sessions` agora `null` quando
   desconhecido, não 0), bootstrap e `PoolsPage`. Teste `test_shared_capacity_snapshot.py` **nasceu
   vermelho** contra o código anterior (`3/3/3` onde afirma `2/2/2`).
   **Antecipado de F3 por necessidade:** o bootstrap parou de ler `active_count` (viraria leitor de
   chave sem escritor, devolvendo o 0 plausível) — publica `model: "bootstrap_placeholder"`, ocupação
   por `Σ SCARD`, e **omite** `busy`/`busy_elsewhere`/`untagged` em vez de zerá-los.
   **F3a ✅ CONCLUÍDA (2026-08-02)** — `work_task_release`/`work_task_expire` recomputam com fan-out
   (`work_task_claim` já entrava de carona no `mark_busy`; **F3b, o bootstrap placeholder, foi feita
   na fatia 2** — ver acima). Teste `test_pull_release_snapshot.py` nasceu vermelho: depois da
   liberação o semáforo estava certo e o snapshot seguia afirmando a vaga consumida, em todas as
   linhas do recurso. Prova por mutação em `infra/test/mutation_occupancy_peak.sh` (M1) — que de
   quebra pegou um 4º teste que **não podia reprovar** (`queue_length` tem 2 escritores) e o
   eliminou.
   **F4a ✅ + F5b ✅ CONCLUÍDAS (2026-08-02)** — ver `CHANGELOG.md`. Rollup
   `{t}:capacity:snapshot` sobre instâncias DISTINTAS, por tipo de licença (sem escalar no topo;
   balde `unknown` para config contraditória; `pools_available` por **(tipo, canal)**);
   `system_availability_check` lê o rollup e devolve `available_by_kind`, com `null` +
   `capacity_unknown` quando ausente — nunca voltando a somar as linhas. F5b: o *live fallback* de
   `pool_status_get` devolve `available: null` + `status: "unknown"` em vez de
   `SCARD(pool:instances)`.
   **F4b ✅ CONCLUÍDA (2026-08-02)** — `/v1/operational/pools` repassa o rollup em `summary.capacity`
   (sem reimplementar a agregação); `MonitorTab`/`PoolsPage` mostram um cartão POR TIPO. Achados:
   o `Online` somava `total_instances` e tinha o mesmo defeito (não estava no desenho);
   `by_channel` é PROJEÇÃO, não partição (Σ entre canais excede o total do tipo, por construção —
   fixado por teste); e **escopo exigiu recompute, não recorte** — a 1ª versão devolvia "—" a quem
   tivesse `accessible_pools`, e a tela mostrou que isso mata o KPI para praticamente todo mundo
   (usuário escopado é a norma). `compute_tenant_capacity(only_pools=…)` + `GET /v1/capacity?pools=`
   no engine, chamado pelo agent-registry com cache 5 s; a regra de dedução segue num lugar só.
   Sem teste de componente na UI: verificação visual + endpoint conferido ao vivo.
   **F4c ✅ CONCLUÍDA (2026-08-02)** — `__total__.provisioned_capacity` passou à capacidade
   deduplicada e entraram linhas `__capacity_{kind}__` (linha do pool inalterada, está certa).
   Série real: `human` 3 / `ai` 353 / `__total__` 356 (era 362). Janela de arranque (1–2 min pós
   restart) publica o `Σ` inflado com log **e marcador na série**: minuto sem linhas `__capacity_*`
   ⇒ `__total__` não confiável. Ocupação por tipo segue AMOSTRADA (é `max` de somas; P2).
   **Varredura da F5b ✅** — nenhum skill YAML chama `pool_status_get`/`system_availability_check`/
   `queue_context_get`; a mudança de contrato é inerte hoje.
   **Falta:** **F5** (limpeza: `get_available_count` sem chamador, remoção do §3.1 do
   `AnalisePoolsPage` — gráfico + `q_series` + produtor `main.py:988`).

   **Pré-requisito da F3 (registrado 2026-08-02, decisão de escopo):** `refresh_snapshots_for_instance`
   só reescreve pool que **já tem snapshot** — sem um, não há de onde tirar `sla_target_ms`/
   `channel_types`, e inventá-los publicaria config falsa num registro que o
   `system_availability_check` usa para decidir oferta de canal **ao cliente**. Hoje é inofensivo
   porque o bootstrap cria a primeira linha de todo pool configurado a cada 15 s (e o `route()` cria
   a de qualquer pool que receba contato). Mas **é uma dependência silenciosa de um serviço no
   outro**, e a F3 mexe justamente no bootstrap: se ele deixar de escrever, pool sem contato fica
   sem linha nenhuma e some do feed. Saída já identificada e barata: `refresh_pool_snapshot` cair em
   `{t}:pool_config:{p}` (cache do próprio routing-engine, alimentado pelo `kafka_listener` — fonte
   autoritativa, não invenção), o que de quebra elimina a heurística do "só se já existe". **Fechar
   isto ANTES de alterar o bootstrap**, não depois.
   *Medido 2026-08-02 (rebaixa a urgência, não elimina a dívida):* a suspeita era que o espelho
   `retencao_humano-int` — que **não existe em `public.pools`** — ficasse sem linha e o fan-out
   nunca o alcançasse. **Não se confirma:** o bootstrap escreve `bootstrap_placeholder` para ele
   também, e no login do humano as duas linhas viraram `resource_semaphore` no mesmo instante
   (`…436019` e `…441911`, 5 ms de diferença — o fan-out percorrendo `pools(instance)`). Ou seja, a
   dependência **está satisfeita hoje**; o que permanece é ela ser silenciosa.
3. **Desmontagem do modelo errado (B)** — para de gatear sessão humana, para de somar as moedas. É
   **remoção**.
   **✅ CONCLUÍDA (2026-08-02)** — ver `CHANGELOG.md`. Escopo pela decisão acima: saíram
   `_shared_limit`/`_sum_reservations`, o SET `{t}:admission:shared`, os `…:reserved:{pool}`, o
   `member_key`, a leitura de `session_reservation` pela admissão e o `pool_registry` do construtor
   do `AdmissionController`. Sobrou `kind:ai ≤ C_ai`, e `AdmissionDecision.cause` tem um valor só
   (`quota`). Instrumentação REAPONTADA: HASH `shared_pools`→`ai_pools`, linhas `__shared__`/
   `__reserved__`→`__admitted_ai__`, e os consumidores foram atrás (analytics `ai_series`,
   `/v1/operational/pools` `summary.ai.by_pool` + `admission_scope: licensed|unlicensed`, Monitor/
   Pools/Analytics + i18n en/pt-BR).
   **Achado durante a remoção — dois ramos ficaram sem entrada possível e saíram junto:**
   `_try_overflow_enqueue` (acomodava na fila muda o contato HUMANO recusado por `C`; humano deixou
   de ser recusado, e a própria função já recusava IA de propósito) e, com ele, o parâmetro
   `force_mute` de `_persist_queued_contact`, `mute_queue.buffer_usage` e a causa `queue_full`, que
   ficou sem produtor. A fila muda continua viva pelo outro motivo — pool sem `queue_config`.
   **Teste vermelho:** `test_admission_licensing.py`. O cenário central monta o pote **configurado e
   esgotado** do jeito que o código antigo o lia e admite um contato HUMANO — contra o código
   anterior reprova com `cause="shared_full"`. Cada cenário traz **controle positivo** no mesmo
   fixture (IA além de `C_ai` É recusada), porque a afirmação "humano é admitido" passaria sozinha
   num ambiente sem limite nenhum — o verde vazio que o brief adverte.
   **Dívida aberta:** a coluna `Pool.session_reservation` (Prisma), o endpoint que a aceita e a
   validação `Σ session_reservation ≤ C` (`routes/pools.ts`, item 3a) continuam existindo e não
   governam mais nada. O **input saiu da tela** (config → Recursos → Pools) — campo inerte oferecido
   ao tenant é pior que campo ausente —, mas o drop da coluna é migração à parte. Ver § abaixo.
4. **Licenças materializadas (C)** — **ADIADO por medição** (Q2 = zero usuários de reserva). Desenho
   pronto na ADR; entra quando houver demanda de produto.
5. **Costura `acquire`/`release`** — arco separado, ver abaixo.

*A ordem 2↔3 inverteu em relação ao esboço inicial: eu havia posto B primeiro por supor que era o único
com dano ativo. A medição mostra B vivo mas de magnitude pequena na configuração atual, enquanto A está
reproduzido e é o que alimenta a afirmação ao cliente.*

**Cautela:** `claim_instance` é o código de maior consequência da plataforma e o co-commit mexe no Lua
dele. `test_instance_semaphore.py` já tem testes de concorrência e teto — **estender**, nunca
contornar. Teste que fique verde sem exercitar o caminho novo remove a única rede que existe ali.

### Decisão tomada (2026-08-02) — o que sobra dos baldes de `admission.py`

> Era "Pendente de decisão ANTES da fatia 3". **Decidido: sai o POTE MISTO, fica `kind:ai`.**

**O que se mediu antes de decidir:**

| Fato | Onde | Consequência |
|---|---|---|
| `shared_limit = max_concurrent_sessions − Σ reservas`, e `max_concurrent_sessions` = **370 = 360 IA + 10 humanos** | `admission._shared_limit` | o pote É a soma das moedas — a mesma falácia de aditividade que a F4 recusou no topo (`available_by_kind`, sem escalar). Não é re-escopável: separadas as moedas, o 370 não tem referente |
| humano já é barrado no **login**: `instâncias human-* ≥ C_human` ⇒ `human_capacity_exhausted` | `mcp-server/server.ts:369` | o balde de sessão é gate **duplo** e na **unidade errada** (licença humana é por login, D2) |
| `kind:ai ≤ C_ai` (360) | `admission.admit`, gate por tipo | **única** moeda correta, e hoje o **único** teto de IA — licenças materializadas (C) estão adiadas |
| `session_reservation`: **zero** pools (Q2) | medição 2026-07-31 | fatia de SESSÃO do mesmo pote misto, e fragmenta um recurso compartilhado (contraria o invariante) |
| fila muda existe por **pool sem `queue_config`**, não só por `C` esgotado | `_persist_queued_contact` | sobrevive inteira; só o gatilho "overflow por C" depende do pote |

**Decisão A — escopo.** Um gate só: `kind:ai ≤ C_ai`. Saem `_shared_limit`, `_sum_reservations`, o
SET `{t}:admission:shared`, os SETs `{t}:admission:reserved:{pool}`, o `member_key` e a leitura de
`session_reservation` pela admissão. `AdmissionDecision.cause` fica com **um** valor: `quota`.

*Por que não a alternativa "tirar só o ramo humano do shared":* o shared seguiria gateando pools de
IA em paralelo ao `kind:ai` — dois portões com intenção sobreposta, que é exatamente como se chegou
aqui. *Por que não tirar o `kind:ai` junto:* a premissa disso — licença cobrada na **aquisição** da
instância — ainda não é verdade (fatia 4, adiada por medição); removê-lo agora deixa IA sem teto.

**Decisão B — instrumentação (itens 7a/7b) é REAPONTADA, não mantida.** O balde que sobra é o de IA,
então é ele que a série e o Monitor passam a medir:

| Antes | Depois | Razão |
|---|---|---|
| linha `__shared__` (used vs `shared_limit`) | linha **`__admitted_ai__`** (used vs `C_ai`) | publicar `__shared__` com o limite antigo seria número plausível descrevendo um portão que não existe |
| linha `__reserved__` | **removida** | sem baldes reservados, a linha não tem referente |
| linha `__buffer__` | **mantida** | fila muda tem razão própria |
| HASH `{t}:admission:shared_pools` | **`{t}:admission:ai_pools`** | o nome é o que mantém o modelo em circulação (a mesma lição de `get_available_count`) |

`max_concurrent_sessions` **não** é removida: segue sendo o número de PROVISIONAMENTO que
`capacity.ts/deployViolation` cobra (Σ declarada nos slots ≤ C). Mistura moedas ali também — mas
isso é o defeito **C**, de outra fatia, e trocar o gate de provisionamento aqui seria construir a
fatia 4 no meio da 3.

**Descontinuidade na série (marcar a data no eixo):** `admission.shared_series` deixa de existir e
`admission.ai_series` começa em 2026-08-02. Não é renomeação — o denominador mudou de `370 − Σ
reservas` para `C_ai = 360`, e o numerador deixou de contar sessões humanas.

### Resultados medidos (2026-07-31 21:20, `infra/test/measure_capacity_licensing_baseline.sh tenant_demo`)

| Pergunta | Resposta | Consequência |
|---|---|---|
| **Q1** — IA roda > 1 sessão por instância? | **NÃO.** O bootstrap (`instance_bootstrap.py:1054-1072`) usa "Concurrent sessions: N" como **número de instâncias**, cada uma com `max_concurrent=1`. Para IA, **instância == sessão** | defeito de unidade **LATENTE** — só morderia com instância de IA multi-vaga, que o bootstrap nunca cria |
| **Q2** — alguém usa `session_reservation`? | **Zero** pools | piso/teto (**C**) é **FEATURE**, não conserto → sai do escopo imediato |
| **Q3** — existe `C` contratada? | **SIM**: `capacity:ai_agent=360`, `capacity:human_agent=10`, `max_concurrent_sessions=**370**` (misto) | **B está VIVO** neste ambiente |
| **Q4** — sessão humana no balde compartilhado? | não no instante medido (3 entradas, todas IA) | inconclusivo p/ B-2; ver vazamento abaixo |

**Magnitude de B aqui:** 10 licenças humanas rendem 30 sessões servíveis (`max_concurrent 3` cada) mas
contribuem 10 ao pote. Pote 370 × capacidade real ~390 → sub-admissão de ~5%. Pequena **porque `C` está
generosa**; morde quando o dimensionamento é apertado ou a carga é humana.

**Defeito A reproduzido (forma "deriva"):** `formfill_demo` tem `available 0 · busy 1 ·
total_instances 0 · active_count 1 · fila 0`, **sem nenhuma instância e sem ocupação em lugar algum**.
`busy > total_instances` é impossível por construção. A forma *não-aditiva* de A precisa de humano
logado em ≥2 pools com sessão ativa — a evidência dela segue sendo a linha de base das 11:29 acima.

**Quem escreve o snapshot num sistema ocioso é o BOOTSTRAP**, não o routing-engine: quase toda linha
tem a assinatura `available == total_instances`, `busy 0`, e duas expiraram durante a leitura (TTL 60 s
do bootstrap, não 3600 s do routing). Confirma empiricamente que a segunda implementação da fórmula
(`instance_bootstrap._refresh_pool_snapshots`) é hoje a **principal** — e reforça a decisão de fazê-la
parar de afirmar capacidade (escrever `null` + `model: bootstrap_placeholder`).

### Itens novos e independentes deste arco, achados na medição

- **Vazamento de admissão** — 3 sessões presas em `tenant_demo:admission:shared` (todas `kind:ai`,
  pool `survey_journey_wf`) com zero instâncias ocupadas. O reconciler não as liberou. Com `C`
  configurada, são 3 unidades de capacidade permanentemente subtraídas. *Corrige em parte a afirmação
  "a admissão é correta por construção": o mecanismo é SET idempotente, mas a liberação depende do
  marcador `closed` + reconciler, e aqui não aconteceu.*
- **Pools fantasma** — `formfill_demo`, `ramal_test`, `survey_journey_wf` (resíduo de smoke com estado
  vivo); **`webhook_skill_id`** é um pool com 3 instâncias, ou seja **o nome de um campo virou id de
  pool** (bug de seed/provisionamento); **`retencao_humano-int`** tem snapshot no Redis e **não existe**
  em `public.pools` — o espelho vive só em runtime e é **invisível a validação em tempo de config**
  (reforça a amarra §9.1 da ADR: pool interno resolve licenciamento no pai, porque não há onde
  configurá-lo).
- **`fila_humano` está com `agent_kind = ai`** — pelo nome deveria ser humano; muda comportamento de
  licenciamento e de hook.
- **Dois testes de `test_human_instance_identity.py` afirmam um contrato REVOGADO** (achado ao rodar a
  suíte da fatia 1, 2026-08-02; **anteriores a ela** — nada do diff toca esse caminho).
  `test_agent_ready_is_authoritative_partial_logout_shrinks` e `test_agent_ready_login_grows_membership`
  exigem que o `agent_ready` seja autoritativo sobre `pools` (encolher no logout parcial, crescer no
  login). O `kafka_listener.py:434-464` faz o **oposto desde 2026-07-28**, de propósito: `pools =
  existing_pools`, evento ignorado — o mcp-server escreve a membership atomicamente ANTES de publicar, e
  o Console abre uma conexão por pool (N `agent_ready` sem ordem garantida entre partições; era assim
  que a membership colapsava, `before=['formfill_demo'] after=['retencao_humano']`). Suspeitar do
  **teste**, não do código: o argumento do código é verificável (ordem de entrega) e o do teste não.
  **✅ RESOLVIDO (2026-08-02): asserções INVERTIDAS.** Viraram
  `test_agent_ready_never_shrinks_membership` e `test_agent_ready_never_grows_membership_either` —
  afirmam o contrato vigente (o registro manda, o evento nunca) e passam. O simétrico do
  crescimento foi acrescentado de propósito: aceitar só a adição ("é aditivo, não perde nada")
  devolveria ao consumidor a autoridade de membership que ele perdeu, e um teste só do
  encolhimento deixaria essa porta aberta. Suíte do routing-engine: 188/188.

### Pico de ocupação VERDADEIRO — event-driven *(**COMPLETO** 2026-08-02: F3a ✅ P1 ✅ P2 ✅ P3 ✅ — ver `CHANGELOG.md`)*

> **P3 as-built:** `bucket=15min` em `/reports/pools/occupancy` (só nele). No caminho, dois defeitos:
> as linhas `__capacity_{kind}__` da F4c entravam em `series`/`by_pool` **como pools** (exclusão era
> por lista literal; virou `NOT startsWith(pool_id, '__')`), e `meta.bucket` ecoava o parâmetro cru
> em vez do aplicado. **Cuidado ao editar `reports_query.py`:** a linha de validação de bucket é
> idêntica em 3 funções — âncora de edição sem contexto pega a errada (aconteceu, e o teste de
> `meta.bucket` foi quem pegou).

> **P2 as-built:** ZSET `{t}:occupancy` (fonte, `ZREM` em zero) + contador
> `{t}:occupancy:total` (atalho O(1)), delta tirado de `ZSCORE` antes/depois num Lua; ganchos DENTRO
> de `claim_instance`/`release_instance`/`swap_to_hold`; `reconcile_tenant_occupancy` 1×/min no
> flusher, corrigindo para a fonte e LOGANDO. **A reconciliação é a condição de existência do
> contador** — sem ela ele é o `active_count` de novo, e deve sair junto. Não clampa negativo (é a
> única evidência de caminho de vaga fora dos ganchos). O `__total__` passou a vir do watermark
> (`{t}:pool:__total__:peak:{minuto}`, sem chave-irmã de capacidade — a capacidade deduplicada é
> montada pelo flusher a partir dos baldes por tipo, F4c); a amostra sobrevive só como fallback, e
> ela se anuncia no log.

> Nasceu de uma pergunta sobre o relatório de dimensionamento: *"o pico por pool existe?"*. Existe —
> `analytics.pool_occupancy_peaks` (grão 1 min) ← Kafka `pool.occupancy` ← `_occupancy_sampler`, lido
> por `GET /reports/pools/occupancy?bucket=hour|day` e exibido em Analytics › Pools › Capacidade.
> **Mas é pico AMOSTRADO a 5 s, e amostrar por relógio é o método errado por construção:** pico é o
> máximo de uma função escada, e qualquer intervalo de amostra pode cair inteiro entre duas subidas.
> Não é questão de escolher um intervalo menor. A alocação/liberação é o instante em que o valor muda.

**Grandeza decidida: VAGA CONSUMIDA** (o que `busy`/`available` já publicam), wrap-up incluído. O
agente em wrap-up não recebe o próximo contato, logo está ocupado por qualquer definição operacional;
e licença de humano é **por login**, então o hold não gasta licença — as duas grandezas não colidem.
Nada a construir: o hold já herda a tag do ocupante (F1) e já entra em `used_here`.

**Regra de gravação (fechada):**

- `max` sobe **só na ALOCAÇÃO** — liberação nunca cria máximo novo.
- **Carga carregada** (bucket que começa alto e só desce, ou sem transição alguma) entra por SEED:
  `max(bucket novo) := ocupação corrente` na virada.
- O seed por virada tem um buraco de **relógio** (flusher acorda em 00:00.4, ocupação caiu em 00:00.1
  → perde 300 ms de pico, justo a classe que motivou sair da amostragem). Seguro barato e **coerente
  com a regra**: na liberação, semear o bucket com o valor de ANTES *se ainda não semeado* — não é
  "gravar max na liberação", é o mesmo seed disparado por EVENTO em vez de por relógio.
- **Ocupação corrente** não precisa ser mantida para POOL (o recompute em Lua já devolve `used_here`
  fresco a cada transição). Só o tenant precisa (ver P2).

> **Invariante de implementação — o bump mora na costura de ALOCAÇÃO (`mark_busy`, sobre o `used_here`
> que o recompute devolveu), NUNCA dentro de `write_pool_snapshot`.** Se "quem escreve snapshot também
> sobe o pico", a F3a passa a bumpar em liberações e o pico volta a ser *amostrado nos instantes em que
> alguém escreve snapshot* — numericamente inofensivo hoje, e a semântica escorrega de volta para
> amostragem **sem nada ficar vermelho**.

**Cobertura verificada (2026-08-02):** `claim_instance` tem **3 sítios, todos em `router.py`**
(`_allocate` 327, `_try_affinity` 500, `work_task_claim` 655) e **cada um chama `mark_busy` logo
depois** → a cobertura de subida já é 100% hoje. `release_instance`: `remove_conversation` (coberto) +
`work_task_release`/`work_task_expire` (F3a, descobertos).

**Consequência: a F3a NÃO é pré-requisito do pico** (afirmação anterior RETIFICADA — ela valia para um
modelo descartado, em que a liberação gravava max). F3a mexe em ocupação corrente e frescor de
snapshot; o flusher lê a ocupação pelo recompute (fresco do Redis), então nem o seed se contamina com
snapshot velho. F3a tem valor próprio e independente.

| # | Fatia | Nota |
|---|---|---|
| **F3a** ✅ | `work_task_release`/`work_task_expire` chamam `refresh_snapshots_for_instance` | 2 sítios; sem decisão pendente. *(F3b — bootstrap placeholder — JÁ FEITA na fatia 2.)* As-built: refresh depois do requeue (ordem **não** load-bearing — `add_queued_contact` já patcheia `queue_length` in-place; a afirmação contrária foi retificada) e **também** no expire de item nunca reivindicado (só a fila encolheu), com `extra_pools=[pool_id]` porque sem `instance_id` o fan-out não alcança pool nenhum |
| **P1** ✅ | watermark por pool: bump na alocação + seed na virada + seed por evento na liberação; `_occupancy_sampler` deixa de amostrar e vira **flusher** | `{t}:pool:{p}:peak:{minuto}`, TTL 2 h. **Zero mudança de schema, tópico, endpoint ou UI** — publica no mesmo `pool.occupancy`. As-built: primitivo único `record_pool_peak` (Lua max-write, atômico — GET+SET em Python perderia o maior entre dois claims concorrentes); `refresh_snapshots_for_instance` passou a **devolver** `{pool: occ}` (dado, não gatilho) e o bump vive só em `mark_busy`; o seed da liberação mora em `release_instance` (porta única de saída da vaga: `remove_conversation` + os dois do pull), com atalho por `EXISTS` — bucket já gravado é ≥ a ocupação corrente, então o `max` seria no-op |
| **P2** ✅ | `__total__` do tenant exato | `max` de SOMAS ≠ soma de `max`, então watermark por pool não o produz. ZSET `{t}:occupancy` `instance → ocupação` (o Lua de claim/release já devolve o novo SCARD; ZREM em zero ⇒ cardinalidade O(ocupadas)) + `INCRBY delta` O(1) no caminho quente + **reconciliação barulhenta 1×/min** no flusher contra a soma do ZSET. *Diferença para o `active_count`: aquele não era pecado por ser contador, e sim por ser de escopo errado, sem fonte contra a qual conferir e sem ninguém conferindo.* |
| **P3** ✅ | `bucket=15min` | Leitura pura (`toStartOfInterval(minute, INTERVAL 15 MINUTE)` + o valor no `pattern="^(hour\|day)$"`). Retroativo, a qualquer momento — o grão de 1 min já retém a informação |

**Verificação as-built (2026-08-02):** primitivos por `test_pool_occupancy_peak.py` + prova por
MUTAÇÃO (`infra/test/mutation_occupancy_peak.sh`, 6 mutações — cada peça tem quem a derrube); o LAÇO
por `infra/test/smoke_occupancy_peak_flusher.sh` (4 portões, ponta a ponta até o ClickHouse). Duas
lições do harness ficaram registradas: **exit ≠ 0 não é vermelho** (veredicto de 3 estados; `local`
zera `$?`; `build --no-cache` apaga o pytest ad-hoc) e **portão que não discrimina não é portão** —
o "linhas chegando" não separa seed-da-virada de seed-ausente, daí o portão sobre o log.

**Validação cruzada (a resposta para "como eu saberia que está certo"):** derivar o pico
retroativamente de `analytics.segments` no ClickHouse, desdobrando `started_at`/`ended_at` em ±1 e
tomando o máximo da soma corrida. Derivação independente, sem escritor novo, em qualquer
granularidade. Deve bater com o watermark **a menos dos holds de wrap-up e de vagas órfãs** — que é
exatamente a diferença entre "vaga consumida" e "contato sendo servido".

**Descontinuidade na série histórica (2026-08-02):** a fonte do sampler mudou de `active_count` para
`used_here`. Não é troca cosmética — o contador **derivava para cima** (ficava preso alto quando
faltava `agent_done`; o clamp de negativo mascarava o simétrico), então `peak_concurrency` histórico
tende a estar **superestimado**. O valor novo herda outra coisa: vaga órfã conta como ocupação até o
reap passar — mas agora ela também aparece em `available`, ou seja, deixou de ser invisível. Tamanho do
degrau não medido. **Se a série virar base de decisão de dimensionamento, marcar a data no eixo.**

**Limitação que permanece em qualquer desenho:** o `peak_concurrency` responde *"quantas vagas no
máximo"*, nunca *"ocupação média"* — o registro por minuto já é máximo, e média de máximos não é média
de ocupação. Média exigiria soma+contagem de amostras por minuto (campo novo, não pedido).

**Achados na série real (2026-08-02) — 1 FECHADO no P1, 2 e 3 seguem abertos:**

> **Achado 1 ✅ FECHADO no P1.** A capacidade passou a ser gravada na chave-irmã
> `{t}:pool:{p}:peakcap:{minuto}`, no MESMO instante do pico e só quando o pico avança — é a
> capacidade *daquele* instante, por construção. Ausente → o flusher degrada para a leitura ao vivo
> **e loga** o viés, para a linha enviesada não se confundir com medida boa. Testes:
> `test_capacity_is_captured_at_the_peak_instant` (a capacidade cresce DEPOIS do pico; a linha do
> minuto tem de manter a antiga) + a asserção `peak <= capacity`, que é a assinatura exata do
> defeito. O registro original fica abaixo — o diagnóstico é o que ensina, não o `✅`.

1. **`provisioned_capacity` é FLASHADA no flush, não amostrada junto com o pico.** `_flush_occupancy`
   chama `_pool_capacity` na virada do minuto, enquanto `peak_concurrency` veio do minuto que passou.
   Consequência observada: `16:38 cap_smoke_a peak 1 / provisioned 0` — `peak > capacity`, impossível
   por construção, e a MESMA assinatura registrada como "defeito A reproduzido" no `formfill_demo`
   (`busy 1 / total_instances 0`). Ali a causa era o contador; aqui é *skew temporal* entre duas
   grandezas do mesmo registro. **No P1, capturar a capacidade no mesmo instante do watermark** (ou
   registrar explicitamente que são de instantes diferentes) — senão `headroom` e `utilization`, que
   a UI deriva das duas, ficam com denominador de outro momento.
2. **O defeito C aparece na SÉRIE, não só na tela.** `16:39 retencao_humano provisioned 3` +
   `retencao_humano-int provisioned 3` = 6 para **um** recurso de 3 vagas. O rollup por recurso
   distinto (F4) precisa alcançar `pool_occupancy_peaks` também, senão o histórico de capacidade
   segue inflado mesmo depois que a tela for corrigida.
3. **`__total__` está correto e é a prova viva de que `max` de somas ≠ soma de `max`:** no mesmo
   minuto 16:39, quatro pools registraram pico 1 (`nps_ia`, `retencao_humano`, `retencao_humano-int`,
   `sac_ia`) e o `__total__` foi **2** — porque os picos ocorreram em instantes diferentes e no máximo
   dois coexistiram. É exatamente por isso que o P2 não pode derivar o total dos watermarks por pool.

### Medições que decidiram o escopo — ⚠️ **Q1/Q2 foram tiradas do BANCO ERRADO** (achado 2026-08-02)

> **O agent-registry vive em `plughub_registry`, não em `plughub_demo`.** Ele roda
> `prisma db push --accept-data-loss` no boot, que dropa tabelas do `public` fora do seu Prisma —
> por isso foi isolado num banco próprio (`docker-compose.demo.yml:551-555`). O que sobrou em
> `plughub_demo.public.pools` é um **FÓSSIL** daquela época: mesmas colunas, dados congelados,
> nenhum serviço escrevendo.
>
> `infra/test/measure_capacity_licensing_baseline.sh` usava `PGDB="plughub_demo"`. Logo **Q1 e Q2
> mediram o fóssil.** É o modo de falha mais difícil de ver: a tabela existe, as colunas existem,
> as linhas são plausíveis — nada fica vazio e nada erra. Descoberto por acidente, ao conferir se o
> `DROP COLUMN` da fatia 3 tinha pegado: Prisma dizia "aplicada e em sincronia" e o `psql` dizia que
> a coluna estava lá. As duas afirmações eram verdadeiras, sobre bancos diferentes.
>
> **Corrigido no script** (`PGDB=plughub_registry`) + portão **Q-1**, que aborta se o banco não tiver
> `_prisma_migrations` — o único discriminador, já que a estrutura é idêntica nos dois.

**O que isso invalida, e o que não:**

| | Fonte | Situação |
|---|---|---|
| **Q1** — IA roda > 1 sessão/instância? | fóssil **+** leitura de `instance_bootstrap.py:1054-1072` | a perna de CÓDIGO sustenta a conclusão; a de dado precisa refazer |
| **Q2** — alguém usa `session_reservation`? | **só** o fóssil | **decidiu adiar a fatia 4**, e a coluna já foi dropada — não é mais re-mensurável no banco vivo |
| Q3/Q4 — quota, baldes | Redis | intactas (o Redis nunca esteve em questão) |

**Sobre Q2, sendo honesto sobre o que sobrou de evidência:** `infra/registry/*.yaml` não declara
`session_reservation` em nenhum pool, então o seed nunca provisionou reserva; e o fóssil também
mostrava zero. As duas evidências apontam para a mesma conclusão — mas nenhuma delas é o banco vivo
no momento da decisão, e essa medição não volta. **O método estava errado mesmo com o resultado
provavelmente certo**, e é o método que decide o próximo caso.

**Refazer antes de reabrir a fatia 4** (agora aponta para o banco certo e aborta se não for):

```bash
bash infra/test/measure_capacity_licensing_baseline.sh tenant_demo
```

**Resíduo a resolver:** o fóssil `plughub_demo.public.pools` continua lá e vai enganar a próxima
medição do mesmo jeito. Ou dropar a tabela, ou renomeá-la para `pools__fossil_pre_registry_split`
— o nome é a única defesa que sobrevive a quem não leu esta seção.

**Alternativas descartadas** (detalhe nos dois documentos): reservar vagas de sessão por pool
(fragmenta o recurso — contraria o invariante); só piso sem teto (sem teto não há limite a impor);
empréstimo do piso ocioso (garantia que exige espera não é garantia); baixar o TTL do snapshot (cura
por expiração); métrica única de "degradação" (valor plausível que esconde privação, espera e
atribuição).

---

## `bootstrap_placeholder` publica capacidade ZERO para pool HUMANO ✅ *(2026-08-02)*

> **CONFIRMADO e CORRIGIDO** — ver `CHANGELOG.md`. A dúvida "o humano estava pausado?" foi
> respondida pelo registro da instância: `"status": "ready"`, `"max_concurrent": 3`,
> `"pools": ["retencao_humano","aprovacao_deploy","retencao_humano-int"]`. Os três pools que
> anunciavam `TOTAL 0 / AVAIL 0` alcançavam 3 vagas. O zero era falso.
>
> Saída aplicada: **(a) omitir**. O bootstrap detecta membro no Redis fora de `self._registered` e
> deixa `available`/`total_instances` de fora, com `capacity_unknown: "unmanaged_members"`.
> Consumidores atrás: `/v1/operational/pools` (`available: null` → `op_status: "unknown"`, em vez de
> `undefined > 0` virar `"empty"`), `PoolsPage` ("—" com o motivo no tooltip) e
> `compute_tenant_capacity` (ausência não conta como porta aberta em `pools_available`).
>
> Registro original abaixo.

---


**Medido**, na linha de base de 23:00 (`measure_capacity_licensing_baseline.sh tenant_demo`): três
pools com **`READY 1` e `TOTAL 0 / AVAIL 0`** — `retencao_humano`, `retencao_humano-int`,
`aprovacao_deploy`. São exatamente os três pools do humano logado. Todas as linhas estavam em
`model: bootstrap_placeholder`.

**Causa (lida no código, não inferida do número).**
`instance_bootstrap._refresh_pool_snapshots` (`:643`) soma `total_capacity` iterando
**`self._registered`** — o conjunto de instâncias que o BOOTSTRAP gerencia. E o `CLAUDE.md` é
explícito: *"Human agents NOT managed by Bootstrap"*. Logo, para um pool cujos membros são humanos,
o laço não encontra nada e publica `total_capacity = 0`, `available = max(0, 0 − 0) = 0`.

**Por que isso é da família que este arco persegue.** Não é "desconhecido": é uma **afirmação de que
não há capacidade** num pool que tem humano pronto para atender. A F3b tomou o cuidado de fazer o
bootstrap **omitir** `busy`/`busy_elsewhere`/`untagged` em vez de zerá-los — mas `available` e
`total_instances` continuaram sendo calculados a partir de um conjunto que exclui humanos por
construção. O cuidado foi aplicado a três campos e não ao quarto.

**Onde morde.** `pool_status_get` e `system_availability_check` leem o snapshot para os Skill Flows
decidirem *o que oferecer ao cliente* (troca de canal, tempo de espera). A F5b consertou o *live
fallback* desses tools (devolve `available: null` + `status: "unknown"` quando não há snapshot) — mas
esta linha **não é fallback**: é snapshot escrito, e é lida como autoridade.

**Janela em que aparece.** O routing-engine escreve `resource_semaphore` (TTL 3600 s) nas transições,
inclusive no login do humano. Passada 1 h sem transição naquele pool, a linha expira e o bootstrap
(NX, TTL 60 s) a substitui pela versão com zero. Ou seja: **pool humano ocioso por mais de uma hora
passa a se anunciar sem capacidade.** É por isso que só apareceu agora — as medições anteriores
foram feitas logo após tráfego.

**Não fechar sem decidir a forma**, porque as duas saídas boas não são equivalentes:
(a) **omitir** `available`/`total_instances` quando o pool tem membro que o bootstrap não gerencia —
coerente com a F3b, e transfere a decisão para quem lê (que já sabe tratar ausência desde a F5b);
(b) **derivar do Redis** (`ready ∪ busy` + `max_concurrent` do registro da instância), o que faria o
bootstrap medir certo — mas seria uma TERCEIRA implementação da fórmula, e o arco passou a fatia 2
inteira reduzindo isso a uma.

Inclinação: **(a)**. O bootstrap existe para reconciliar instâncias, não para medir capacidade;
publicar menos é o conserto barato e não cria mais um lugar onde a fórmula possa divergir.

**Verificar antes de consertar:** confirmar que o humano não estava `paused` (o laço pula pausados
em `:648`, e aí o zero seria legítimo). `redis-cli SMEMBERS tenant_demo:pool:retencao_humano:instances`
e o `status` no registro da instância dizem.

---

## Varrer o `REDIS_URL` de leitura única nos DEMAIS pacotes *(aberto — 2026-08-02)*

**Resolvido no `routing-engine`, e medido:** `test_instance_semaphore.py` (24) e
`test_human_instance_identity.py` (11) pulavam inteiros no container por lerem só `REDIS_URL` (ver
CHANGELOG). Com o dual-read a suíte foi de `171 passed, 35 skipped` para **`207 passed, 0 skipped`**
— os 35 rodaram e **passaram**. Ou seja: o código das fatias 1 e 2 se sustenta sob a suíte que se
alegou tê-lo validado; o que faltava era a suíte rodar onde o serviço roda. Não é o mesmo que dizer
que ela rodou durante aquelas validações — isso segue sem dado —, mas não há vermelho latente, que
era o risco material.

**Varredura estática feita (2026-08-02).** No repositório inteiro existe **um único** outro arquivo
de teste com o padrão: `orchestrator-bridge/.../tests/test_restore_instance_patch.py:35` —
`os.environ.get("REDIS_URL", "redis://redis:6379")`. Ele provavelmente NÃO sofre do defeito, e o
motivo importa: o default é o **hostname do compose**, não `localhost`, e o serviço dele
(`orchestrator-bridge/main.py:76`) também lê `REDIS_URL` cru. Teste e serviço leem a mesma variável.

**Confirmar com o dado, não com a leitura** (é barato, e "provavelmente" não é medição):

```bash
docker compose -f docker-compose.demo.yml exec -T orchestrator-bridge sh -lc \
  'pip install -q pytest pytest-asyncio && cd /app/packages/orchestrator-bridge && \
   python -m pytest -p no:cacheprovider -q src -rs 2>&1 | tail -20'
#   skip com razão "Redis indisponível" ⇒ mesmo defeito, outro endereço.
```

### A raiz: os serviços discordam do NOME da variável

| Serviço | Lê | Onde |
|---|---|---|
| routing-engine | `PLUGHUB_REDIS_URL` | `config.py` (prefixo `PLUGHUB_` do pydantic-settings) |
| orchestrator-bridge | `REDIS_URL` | `main.py:76` |
| session-replayer | `REDIS_URL` | `consumer.py:119` |
| usage-aggregator | `REDIS_URL` | `main.py:30` |

É por isso que o dual-read nos testes existe: ele contorna uma inconsistência de **wiring**, que o
`CLAUDE.md` § Configuration trata como domínio próprio (*"env only for secrets and wiring"* — mas
wiring com dois nomes para a mesma coisa é wiring quebrado). Enquanto os nomes divergirem, todo teste
de integração novo precisa lembrar do dual-read, e esquecer sai VERDE.

**Duas saídas, e a segunda é a que fecha a classe:** (a) manter o dual-read + guarda por pacote —
barato, mas é remendo replicado; (b) unificar o nome da variável nos serviços (provavelmente para
`PLUGHUB_REDIS_URL`, coerente com o prefixo já adotado), o que torna o dual-read desnecessário e a
guarda um detector de resíduo. (b) mexe em compose e em 3 serviços; não é urgente, mas é o conserto.

**Se a resolução migrar para um `conftest.py` compartilhado**, a guarda se declara inútil por
construção (ela checa o denominador antes de afirmar "zero infratores") — adaptar o alvo dela, nunca
apagá-la.

---

## Drop de `Pool.session_reservation` — resíduo da fatia 3 ✅ *(2026-08-02)*

**Concluído** nos três passos previstos — ver `CHANGELOG.md`. O campo não governava nada desde a
fatia 3, e a validação `Σ ≤ C` era a última coisa no sistema a **afirmar** que reservas de sessão
existem: quem lesse o código por ela reconstruiria o modelo removido.

Saíram, nesta ordem (cada passo verificável sozinho, o DROP por último por ser o único
irreversível): validação item 3a (`_reservationViolation`/`_reservedTotal`) + aceitação no
POST/PUT + endpoint `GET /v1/pools/capacity/conformance` → campo fora de
`PoolRegistrationSchema`, `PoolConfig` (routing-engine), tipos da UI, `kafka_listener` e da
allowlist `MANAGED` do `instance_bootstrap` → migração
`20260802000000_drop_pool_session_reservation`.

**Consumidor que a remoção arrastou:** a aba Capacidade do Billing exibia
`reservado`/`compartilhado` + tabela "Pools com reserva" + selo conforme/não-conforme, tudo
derivado do endpoint removido. Um selo de conformidade sobre uma regra que nenhum caminho de
execução aplica é afirmação ao operador, plausível e falsa. Sobrou `contratado × alocado × saldo`,
que **é** imposto (`deployViolation`, 422 no PUT de slot).

> **Aguda depois desta remoção:** `C` = `max_concurrent_sessions` continua somando licença humana
> com licença de IA — agora só como teto de PROVISIONAMENTO (`lib/capacity.ts`). Como teto de
> admissão morreu; como teto de provisionamento mistura as moedas do mesmo jeito. É o **defeito C**
> do arco, ainda aberto, e a fatia 4 (licenças materializadas) é onde ele fecha.

**Deploy:** migração é arquivo NOVO ⇒ `docker compose build --no-cache agent-registry` (dívida
conhecida, § abaixo). Um `build` normal deixa o serviço reportando "N migrations" e "No pending
migrations", o que parece sucesso.

---

## Costura única de aquisição (`acquire`/`release`) *(arco separado, adiado — 2026-07-31)*

O **árbitro** já é único: `claim_instance`, Lua atômica, mesmo semáforo para push e pull. O que está
duplicado é o **entorno**: push faz `selecionar → pontuar → claim → mark_busy → snapshot → publish
routed`; pull faz `gate → ZREM → claim → mark_busy → lease → publish routed`. Mesma sequência, duas
implementações — e as divergências são onde moram os defeitos deste arco: o pull **não escreve
snapshot**, **não checa admissão** nem **pertencimento ao pool** (o `formfill_demo` teve item
reivindicado com `total_instances 0`), e a liberação tem três caminhos (`remove_conversation`,
`release_instance`, o release condicional do `work_task_expire`).

Alvo: um par `acquire(recurso, sessão, conferência, pool, motivo)` / `release(...)` que possua claim +
sincronia do espelho + tag + fan-out de snapshot + lease + publish, compondo os **três portões**
(licença, admissão, semáforo) com uma taxonomia de falha só. Push e pull passariam a diferir apenas em
**quem escolhe o recurso** — algoritmo de score num caso, um humano no outro. Pull é "o humano é o
scorer"; tudo depois é idêntico.

**Não unificar:** admissão responde *"este contato entra no sistema"*, alocação responde *"qual recurso
o atende"* — donos diferentes, colapsá-las é o erro simétrico. Exceções declaradas (throttle de pool
webhook, canal como hard filter) viram parâmetro explícito, não caminho paralelo.

Adiado por decisão (2026-07-31): não há defeito visível ao usuário aqui, e separar mantém a validação
de cada arco capaz de ficar vermelha sozinha. Depende das fatias 1–3 acima.

---

## `available > total` — **ENCERRADO por remoção da causa** *(2026-08-02; a investigação abaixo já não é executável)*

**Reescrito em 2026-08-02.** A versão anterior mandava caçar dois `WARNING` — `active_count de
pool=… foi a NEGATIVO` e `available (…) passaria de total_instances (…)`. **Nenhum dos dois existe
mais**, e não porque pararam de disparar: a fatia 2 removeu `active_count` ponta a ponta (chave,
helper, `get_busy_count`, INCR/DECR/clamp e o patch `available += 1`), e com ele foram embora o
contador que ia a negativo e o teto que o segurava. Seguir a instrução antiga produziria "zero
ocorrências" — o verde vazio que este arco inteiro combate. Encerrar a caça é o resultado correto;
deixá-la de pé seria manter um portão que só pode passar.

**Por que a classe fechou por construção, e não por vigilância.** Os dois escritores do snapshot
calculam `available` e `total_instances` da MESMA grandeza:

| Escritor | `total_instances` | `available` |
|---|---|---|
| `registry.write_pool_snapshot` (`model: resource_semaphore`) | `total_capacity` = Σ `max_concurrent` sobre `ready ∪ busy` | `max(0, total_capacity − used_global)` |
| `instance_bootstrap._refresh_pool_snapshots` (`model: bootstrap_placeholder`, NX/60 s) | idem, por Σ `max_concurrent` | `max(0, total_capacity − Σ SCARD)` |

`available = max(0, T − u)` com `total_instances = T` e `u ≥ 0` ⇒ `available ≤ total_instances` é
**aritmética**, não invariante defendida por um `if`. O `4/3` de 2026-07-30 só era possível enquanto
as duas grandezas vinham de fontes diferentes (uma de capacidade, a outra de um contador paralelo por
pool). *Lição que fica: o teto era um remendo correto sobre um modelo errado — apagar a causa dispensou
o remendo, e o remendo é que estava sendo monitorado.*

**O que resta como risco real:** um TERCEIRO escritor da chave `{t}:pool:{p}:snapshot`, que não
passe por nenhuma das duas fórmulas. É verificável em segundos no DADO (não no log), e o
discriminador é o campo `model` — que existe exatamente para isto:

```bash
# 1. Modelos em circulação. Só devem aparecer `resource_semaphore` e `bootstrap_placeholder`.
#    Qualquer outro valor — ou snapshot SEM `model` — é escritor não catalogado.
docker compose -f docker-compose.demo.yml exec -T redis sh -lc \
  'for k in $(redis-cli --scan --pattern "*:pool:*:snapshot"); do redis-cli get "$k"; done' \
  | python3 -c 'import sys,json,collections;
c=collections.Counter(json.loads(l).get("model","<SEM model>") for l in sys.stdin if l.strip());
print(c)'

# 2. A desigualdade, medida onde ela vive. Saída VAZIA é a aprovação.
docker compose -f docker-compose.demo.yml exec -T redis sh -lc \
  'for k in $(redis-cli --scan --pattern "*:pool:*:snapshot"); do redis-cli get "$k"; done' \
  | python3 -c 'import sys,json
for l in sys.stdin:
    if not l.strip(): continue
    d=json.loads(l)
    a,t=d.get("available"),d.get("total_instances")
    if a is not None and t is not None and a>t: print(d["pool_id"], a, t, d.get("model"))'
```

Se a query 2 devolver linha, a informação que importa já vem junto: o `model` diz **qual** escritor
mentiu, e a lista completa de escritores da chave está no CHANGELOG da entrada de 2026-07-30. Se
devolver linha com `model` ausente, é escritor novo — e o lugar de consertar é lá, não num teto.

**Não reintroduzir**, e agora são quatro, todas pelo mesmo motivo (código que ensina um modelo
errado custa mais que o espaço que ocupa):

| Símbolo | O que ensinava de errado |
|---|---|
| `patch_pool_snapshot_available` | que `available` era incrementado por fora, e não derivado |
| `get_total_instances_count` | que `total_instances` era contagem de agentes, não capacidade |
| `get_available_count` (F5) | que `SCARD(pool:instances)` — pertencimento — era vaga livre |
| `get_busy_count` / `{t}:pool:{p}:active_count` (fatia 2) | que ocupação é fato do POOL, quando é do RECURSO |

---

## Auditar `duration_ms` × `handle_time_ms` no analytics *(follow-up do fix de 2026-07-29)*

`sessions` tem `handle_time_ms`; `segments` tem `duration_ms`. O
`/reports/timeseries/handle_time` pedia `duration_ms` sobre `sessions` e falhava desde
sempre, mudo (ver CHANGELOG). **Só aquela função foi corrigida.**

Falta varrer o analytics-api atrás do mesmo engano — qualquer `duration_ms` referenciado
contra `sessions` (ou `handle_time_ms` contra `segments`). O sintoma é sempre o mesmo:
endpoint que devolve vazio com `error: "data_unavailable"` e UI que renderiza gráfico em
branco, sem erro visível.

**Como varrer com proveito:** não basta grep — a coluna certa depende da tabela no `FROM`,
que às vezes é aliasada. Um teste que rode cada query contra o schema real (ou um
`DESCRIBE` comparado com as colunas citadas) acha mais que leitura. Vale considerar
transformar o `except` genérico desses wrappers em log de ERROR com o texto da exceção:
`UNKNOWN_IDENTIFIER` teria denunciado isto no primeiro boot.

---

## `docker compose build` não pega arquivo NOVO — só `--no-cache` *(achado 2026-07-29, causa não investigada)*

Reproduzido **duas vezes na mesma sessão**, em serviços diferentes:

| Arquivo novo | Serviço | Sintoma |
|---|---|---|
| `prisma/migrations/20260729000000_drop_pool_acw_gate/` | agent-registry | boot dizia "28 migrations found" (havia 29 no disco); `migrate deploy` reportava "No pending migrations" |
| `pools_client.py` + `tests/test_pools_client.py` + migration `pool_purpose` | analytics-api, agent-registry | `pytest` → "file or directory not found"; boot → "29 migrations" |

Nos dois casos `build --no-cache <svc>` resolveu na hora. **Edição de arquivo EXISTENTE
entra normalmente** — o problema é só com arquivo/diretório novo, o que aponta para
invalidação de layer de `COPY` (`.dockerignore`, padrão fixo no Dockerfile, ou cache do
BuildKit).

**Por que investigar em vez de sempre usar `--no-cache`:** nas duas vezes o sintoma foi
barulhento por sorte — o pytest reclamou do arquivo ausente e o Prisma contou as migrations.
Um arquivo novo cuja ausência é **silenciosa** (um consumer que simplesmente não roda, um
filtro que não aplica, um cliente que degrada para vazio) não produziria mensagem nenhuma —
só um comportamento que não muda. É o padrão que a § Postura de Engenharia nomeia, na
camada de build.

**Primeiro passo:** comparar `.dockerignore` com o `COPY` do Dockerfile do agent-registry e
do analytics-api; conferir se o build usa BuildKit com cache montado.

---

## Segmento humano do wrap-up NUNCA fecha — produtor de `agent_done` faltando *(achado 2026-07-29)*

> **Candidato forte ao "produtor faltante" da seção seguinte.** Os dois itens são
> provavelmente o mesmo defeito visto de ângulos diferentes.

Ao investigar o resíduo "TMA por agente" da E2f, a query mostrou que **toda** sessão de
wrap-up destacado tem dois segmentos, e o do humano nunca encerra:

| Segmento | `pool_id` | `ended_at` | `duration_ms` | `outcome` |
|---|---|---|---|---|
| workflow (`agent_type: native`) | `wrapup_detached_ia` | ok | 23–48 ms | ok |
| **humano** (`agent_type: human`) | `formfill_demo` | **NULL** | **NULL** | **NULL** |

Segmentos abertos desde 2026-07-28 (portanto **depois** da Phase 2, que já havia tocado o
sintoma correlato "vaga do claimante devolvida só por efeito colateral"). O humano
reivindica o item, preenche o form, submete via `workflow_resume` — e o `participant_left`
do segmento DELE nunca é publicado. O `segment_outcome_record` grava no segmento da
**origem**, por referência; o segmento do próprio wrap-up fica órfão.

**Três consequências, em ordem de importância:**

1. **O tempo de ACW não existe como número em lugar nenhum.** Nem para excluir do TMA de
   atendimento, nem para reportar como métrica própria — que era a promessa inteira da
   "segregação, não supressão" da E2f. *(Correção de registro: a afirmação "o TMA do pool
   de wrap-up É o tempo de ACW" foi feita sem verificação e é falsa neste wiring — os
   23–48 ms de `wrapup_detached_ia` são o runtime do workflow.)*
2. **A vaga fica pendurada** até o reap passar — a origem que a seção seguinte procurava.
3. **Segmentos permanentemente abertos** em `segments`. Não poluem
   `mv_agent_performance_daily` (`WHERE ended_at IS NOT NULL`), mas poluem qualquer leitura
   de "participação em aberto".

**Resíduo da E2f fica SUSPENSO por causa disto:** filtrar a lente `sessions_aht` não faz
sentido enquanto a duração é nula — filtraria zeros. Reabrir depois do fix, e aí valendo o
achado de que **filtro por `segments.pool_id` não serve**: o segmento humano carrega
`formfill_demo` (pool `contact`), não o pool interno. O filtro correto é por SESSÃO
(subquery em `sessions`), e a `mv_agent_performance_daily` — chaveada por `pool_id`, sem
`session_id` — não tem conserto por leitura.

**Decisão de configuração pendente (anterior ao código):** o claim do wrap-up deveria ter
**pool próprio** (`wrapup_claim`, `purpose: internal`) em vez de reusar o `formfill_demo`
(`skill_wrapup_detached_v1.yaml:32`, "reusa o pool pull do demo R0"). Com pool próprio, o
tempo de ACW nasce legível por pool, o filtro por `segments.pool_id` volta a servir e a MV
também. Reusar um pool de demo foi conveniência da fatia R0, não desenho.

### Mapeamento concluído (2026-07-29) — não é bug do wrap-up, é lacuna da família pull

**Não há caminho de referência a copiar: a APROVAÇÃO tem o mesmo defeito.** Aprovação,
`skill_formfill_demo_v1` e wrap-up usam o mesmo `delegate`+`pool` → `handle_delegate`
(inbound roteado, **não** `handle_delegate_conference`). Nos três o segmento humano abre e
nunca fecha.

**O produtor canônico e por que ele não é acionado.** `participant_left` de `agent_type=human`
tem só **2 produtores**, ambos em `process_contact_event` (bridge `main.py:6175` e `:5601`), e
**ambos exigem um `contact_closed` em `conversations.events`**. No atendimento normal quem o
publica é o `/api/agent_done` chamado pelo botão "Encerrar" do Console
(`AgentAssistPage.tsx:328` → `server.ts:1887`). Na UI de form-fill **esse botão não existe**
(só "Return to queue") — corretamente, porque item de tarefa não é contato. O `agent_done` que
o caminho pull publica (`main.py:7342-7387`) vai para `agent.lifecycle`, que só devolve a
**vaga**; o analytics não o consome.

**Triplo trinco no caminho A** — mesmo consertando um, os outros seguram: (a) ninguém publica
`contact_closed(agent_closed)`; (b) `_destroy_conference` (`main.py:2483-2497`) **deleta**
`human_agent`/`human_agents` sem emitir nada; (c) a única varredura de participantes existente
(`:5493-5619`) está atrás de `not _ccf_already`, e `_close_contact_layer` seta esse flag 190
linhas antes de publicar (`:2144-2149`) — o ramo "customer_side" documentado é, na prática,
inalcançável a partir dali.

**Vazamento gêmeo:** `work_task_release` (`routing/router.py:714-741`) devolve item, lease e
vaga — e também não emite `participant_left`. Idem o `on_timeout` do delegate.

> **H2 ✅ + H1 ✅ (2026-07-29, ver CHANGELOG).** A medição refinada mostrou **0 vazamentos
> no caminho canônico** (`retencao_humano`) — o produtor canônico funciona; o defeito era
> exclusivo da família pull.
>
> **Falta:**
> - **Resíduo da E2f, agora REAL** — com duração preenchida, o segmento de wrap-up entra
>   na lente `sessions_aht`. Filtrar por `segments.pool_id` **não serve** (o segmento
>   carrega `formfill_demo`, pool `contact`). Fazer o **pool próprio** abaixo torna o
>   filtro trivial e conserta a MV junto; sem ele, exige subquery por sessão.
> - ~~**Resíduo da E2f**~~ + ~~**Pool próprio para o claim**~~ ✅ **RESOLVIDOS (2026-07-30, ver
>   CHANGELOG)** pela ADR [`adr-internal-work-queue-author-bound`](docs/adr/adr-internal-work-queue-author-bound.md),
>   fases **I1–I4**. O segmento humano do wrap-up passou a nascer em `{pool}-int`
>   (`purpose: internal`) e o `_apply_contact_scope` já existente o cobre — o resíduo do TMA por
>   agente desapareceu **sem filtro novo**, e o ACW ficou legível **por pool de origem**.
>   **Falta a fase I5** (sem transbordo + supervisor pode encerrar + TTL `acw_expired` +
>   relatório de pendências por agente). Sem ela um wrap-up que ninguém preenche fica pendurado
>   para sempre — os 87 órfãos em outra roupa. *(Achado a checar junto: o segmento de wrap-up
>   SUBMETIDO fechou com `outcome = NULL`, que é o valor que a D5 reserva para "ninguém
>   preencheu" — o que os separa tem de ser o `close_reason`.)*
> - **Backfill dos 87 já abertos** — decisão: não curar dado de teste (o grosso do
>   `formfill_demo` é lixo de teste abortado); os 9 da aprovação são uso real e ficam como
>   ruído. Alternativa a fingir fechamento: `DELETE` no ClickHouse.
> - **As 3 divergências de doc** listadas abaixo.
> - ~~**Validar H1 ao vivo**~~ ✅ **(2026-07-30)** — atendimento real: o segmento de wrap-up
>   fechou com `close_reason=task_submitted` e `duration_ms=89 483`. A corrida não ocorreu, e
>   o **tempo de ACW passou a existir como número** (contato: 11 656 ms no `retencao_humano`;
>   ACW: 89 483 ms no `retencao_humano-int` — 7,7× o atendimento, a distorção que G1 nomeia).

**Conserto proposto (duas camadas, complementares):**
- **H2 (estrutural, primeiro):** varrer `session:{id}:human_agents` emitindo `participant_left`
  **antes** do delete em `_destroy_conference` (`main.py:2483-2497`), espelhando o loop de
  `:5493-5619`. Cobre submetido, devolvido e expirado. Fecha com `outcome` genérico.
- **H1 (por cima):** em `_handle_webhook_session_resumed`, ao lado do `agent_done` de lifecycle
  (`main.py:7342`), emitir o `participant_left` humano lendo o mesmo trio que o produtor
  canônico usa (`participant_joined_at:{inst}`, `segment:{inst}`, `participant_meta:{inst}`,
  todos escritos por `activate_human_agent` em `:892-933` e vivos nesse ponto). **Só o resume
  conhece o `outcome`** — é isto que produz o tempo de ACW como número.
- **H3 descartada:** fazer o Console chamar `/api/agent_done` após o submit — publicaria
  `contact_closed(agent_closed)` e dispararia `on_human_end` no pool do claim, correndo contra
  o `_close_contact_layer` do próprio resume.

**Evidência que dimensiona antes de codar:** listar segmentos humanos com `ended_at IS NULL`
agrupados por pool. Se aparecerem wrap-ups **nunca submetidos** (devolvidos/expirados), H1
sozinha é insuficiente — previsão do código é que ambos vazem.

**Divergências doc × código achadas no mapeamento (corrigir junto):**
0. ✅ **CORRIGIDA (2026-07-30)** — `CLAUDE.md` § Configuration dizia "Skills seguem upsert (são
   código, não config de tenant)". **Falso desde 2026-07-13**: skills são seed-if-absent
   (`registry_syncer.py` §46-53). Consequência que custou um ciclo inteiro de validação: editar o
   YAML de um skill já semeado é **no-op**, reiniciar o bridge não publica nada (só loga o DRIFT),
   e o modo de falha é **sucesso pelo caminho antigo**.
4. `tenant_demo.yaml:123` comentava "dispatch: detached ✅" enquanto a entrada declarava
   `inline` — corrigido em 2026-07-30 junto com a I3.
5. `PresenceSidebar.tsx` não é renderizado por ninguém — 5º órfão, além dos 4 já listados em
   § "Eventos — três superfícies para duas ideias".
1. `docs/arcos/session-conference-lifecycle.md:305-311` diz que o segmento fecha por "agent_done
   OU heartbeat TTL expirado". A perna de heartbeat (`server.ts:3371-3388`) é **gated em
   `sismember human_agents`** — SET que `_destroy_conference` já apagou. A rede não fecha.
2. `docs/adr/adr-wrapup-detached-pull.md:25,143` diz que `segment_outcome_record` "re-publica
   participant_left p/ o analytics" — verdade **só para o segmento da ORIGEM**. O ADR nunca
   menciona que a sessão de wrap-up gera um segmento humano próprio; o desenho não previu quem
   o fecharia.
3. Comentário em `main.py:2204` descreve um caminho `customer_side` que a própria função
   neutraliza (ver trinco (c)).

---

## Vaga só é liberada no `agent_done` — reap é rede, não conserto da origem *(2026-07-28)*

O reap de ocupantes órfãos está **implementado e validado** (ver CHANGELOG): ocupante cuja sessão tem
`session:{sid}:closed` sai do semáforo, nos dois sites onde a lotação pode ser mentira
(`get_ready_instances` e `claim_instance`), com cooldown de 60 s por instância.

**O que continua aberto é a origem.** `release_instance` só é chamado no `agent_done`. Todo caminho de
morte de sessão que não passa por ele segue vazando vaga até o próximo reap — o reap repara *depois*,
não impede. Assimetria que denuncia a premissa: o **hold** de wrap-up tem expiração passiva porque o
desenho previu "wrap-up que nunca chega"; o ocupante real não tem equivalente porque se presumiu que
todo claim termina em `agent_done`.

**Instrumento de decisão:** o `warning` de `reap:`. Ele existe para MEDIR, não só para consertar.

- Se aparecer **raro** (só após crash/restart do bridge) → a rede basta, não mexer.
- Se aparecer **em uso normal** → existe um produtor de `agent_done` faltando. Caçá-lo é melhor que
  seguir reparando: cada linha de `reap:` nomeia o `session_id`, e o `session:{sid}:closed` guarda o
  `reason` (7 d de TTL) — dá para agrupar por motivo de fechamento e achar qual caminho não publica.

Só depois dessa medição decidir se cabe fechar a origem (publicar `agent_done` também nos caminhos de
morte abrupta) ou aceitar a rede como suficiente.

---

## `role` nunca é escrito no hash de participante *(resíduo da F5 de identidade por-pool, 2026-07-28)*

> **Fatia A ✅ (2026-07-28, ver CHANGELOG).** A investigação mostrou que o nome `role` cobria **dois
> fatos de escopos diferentes**, e que por isso não existia um único produtor a escrever:
>
> | | **Fato A** — propósito do agente | **Fato B** — papel de participação |
> |---|---|---|
> | Valores | `executor` / `orchestrator` / `evaluator` | `primary` / `specialist` / `supervisor` |
> | Escopo | o ARTEFATO (skill), estável | (participante, sessão) |
> | Consumidores | `evaluation_context_get`, `evaluation_submit` | `message_send`, `session_context_get` |
>
> **Fato A está fechado**: campo `agent_role` no skill (registry), carimbado pelo `agent_login` no hash
> da instância — que é o escopo CERTO para ele, porque o propósito é constante por toda a vida da
> instância. **Fato B segue aberto** e é o que resta desta entrada: NÃO cabe naquele hash (a mesma
> instância atende `max_concurrent_sessions` sessões e é `primary` numa e `specialist` noutra ao mesmo
> tempo — guardá-lo ali colapsa multi-sessão, invariante do CLAUDE.md).

Dois sites ainda LEEM `role` de `{tenant}:agent:instance:{participant_id}` — `session_context_get` e
`message_send` — e **nenhum produtor escreve o campo**. Ambos caem no default.

Consequências vivas:

- A tool MCP `message_send` **não roteia @mention nenhuma**: o gate da F5 exige leitura positiva
  (falha fechada, de propósito). Correto por ora — o Console usa o WS, que conhece o agente pela
  conexão — mas é capacidade desligada por falta de produtor, não por decisão. Fechar quando/se
  existir agente humano via SDK.
- O mesmo default decide **mascaramento** (`session.ts`: `role === "customer" || role === "primary"`
  → mascara) e carimba `author_role` no stream. Como nunca é lido de fato, toda mensagem via
  `message_send` é mascarada e sai como `primary`. Blast radius maior que o do @mention; mesmo
  produtor ausente.

**Fatia B — desenho decidido, não implementado.** Store por participante
`session:{id}:participant:{participant_id}` (hash), escrito pelo bridge no join, generalizando o
`session:{id}:ai_participant:{instance_id}` atual (que hoje cobre só IA nativa e é chaveado por
instance_id). Pré-requisitos levantados na investigação:

1. **Unificar a convenção de `participant_id`** — o bridge publica `participant_id=native_instance_id`
   no Kafka (`main.py:3622`) mas entrega `uuid4()` ao especialista de conferência (`main.py:2863`, nunca
   persistido). Duas identidades para o mesmo participante; nenhum store conserta isso antes.
2. **Produzir o vocabulário** — `_part_role = "specialist" if conference_id else "primary"`
   (`main.py:3489`) é a ÚNICA decisão de papel no sistema; os outros 11 call sites de
   `_publish_participant_event` passam literais. `supervisor` nunca é emitido por caminho nenhum.
3. **`session:{id}:participants` é chave órfã** — o `ParticipantSchema`
   (`schemas/src/session.ts:77-88`) já tem a forma exata, é lido por `session_context_get:182` e pelo
   replayer (`replayer.py:303`), e **não tem writer**. Hoje `session_context_get` sempre devolve
   `participants: []` e todo `ReplayContext.participants` vem vazio.
4. `e2e-tests/scenarios/10_masking.ts:235` só passa porque semeia `role` à mão no Redis — o teste
   documenta a ausência do produtor, não a presença.

Correlatos do mesmo arco (fechado — ADR
[`adr-human-agent-pool-scoped-identity`](docs/adr/adr-human-agent-pool-scoped-identity.md)):
`crash_detector.py:144` ainda usa `meta.pools[0]` (mitigado por pular `human-*` em `:98`; o docstring
de `update_instance_meta` agora avisa que o meta é cache, não constante) · **testes de estabilidade
multi-pool** seguem inexistentes, embora a F5 os previsse.

---

## analytics-api — 23 testes vermelhos há tempo *(achado ao rodar a suíte, 2026-07-28)*

Apareceram ao validar a descontinuação do `agent_events`. **Nenhum tem relação com essa
mudança** — os 260 de `test_reports.py`+`test_consumer.py` passam. São dois defeitos
independentes, ambos anteriores e ambos do tipo "teste que não pode reprovar".

### (a) 14 testes de RBAC neutralizados por um MagicMock ✅ CORRIGIDO (2026-07-28)

`test_admin.py::TestRequirePrincipal` (8) e `test_dashboard.py::TestDashboardRBAC` (6)
recebiam `Principal(sub="open_access")` e 200 onde esperavam 401/403.

**Não era o ambiente** (a variável só existe no `docker-compose.demo.yml:921`; `env -u` não
mudava nada). A causa era o próprio fixture:

```python
settings = MagicMock()
settings.admin_jwt_secret = SECRET        # ← só isto era fixado
```

Um `MagicMock` auto-cria qualquer atributo e o devolve **truthy**. Quando o guard
`if settings.analytics_open_access:` entrou em `require_principal` (`auth.py:72`), o mock
passou a respondê-lo como verdadeiro, e os 14 testes silenciosamente trocaram de caminho:
deixaram de exercitar autenticação e passaram a validar o atalho de open-access.

**Corrigido** com `settings.analytics_open_access = False` nos dois fixtures.

**A lição, que é maior que o conserto:** eram exatamente os testes que provam que o
analytics exige token e bloqueia cross-tenant, e ficaram incapazes de reprovar sem que nada
acusasse. É a terceira ocorrência do mesmo padrão nesta sessão — depois do
`evaluation_context_get` (o `if (role && …)` que curto-circuitava na string vazia) e do 502
mudo do ai-gateway (`lastResort` do logging). Todos: um default plausível ocupando o lugar
de uma verificação.

**Regra a adotar:** mock de config precisa fixar TODO atributo booleano que o código sob
teste lê — o default de um mock nunca é "desligado". Um `MagicMock(spec=Settings)` não
resolveria (spec valida nomes, não valores); o que resolveria é o mock ser um `Settings`
real com overrides.

### (b) 9 testes de `_fetch_customer_history` — drift desde a Journey J1 ✅ CORRIGIDO (2026-07-29)

`test_sessions.py::TestFetchCustomerHistory` (7) e `TestCustomerHistoryEndpoint` (2):
`ValueError: not enough values to unpack (expected 9, got 8)`.

A query em `sessions.py:241` passou a selecionar `root_session_id` (Journey J1) e os
fixtures continuavam com 8 colunas. **Corrigido:** campo acrescentado aos `_row()`/
`_ch_row()`, com default = o próprio `session_id` (J1: contato avulso é sua própria raiz,
auto-mint = self).

**O que faltava não era a coluna, era a asserção.** Não havia uma única menção a
`root_session_id` no arquivo — por isso o drift passou. Foram adicionados 3 asserts que
cobrem o campo (raiz = self, raiz ≠ self ⇒ membro de processo, e o campo na resposta do
endpoint). Sem eles o próximo `SELECT` novo repete a história.

### (c) Testes que travam

`TestDashboardRBAC` pendurou em duas execuções (interrompido com Ctrl+C). Instancia
`TestClient` com app real; suspeita de request sem timeout. Não investigado.

---

## Eventos — três superfícies para duas ideias *(desenho fechado 2026-07-28, não implementado)*

Levantamento do platform-ui achou **três** telas de "Eventos", duas delas cópia literal
uma da outra:

| # | Onde | Conteúdo | Fonte |
|---|---|---|---|
| 1 | Monitor › Sessões → toggle "Eventos" (`MonitorTab.tsx:780` `EventsView`) | **agregado** (categoria, count, sum, avg, first/last seen) | `/reports/agent-events/summary` → `agent_business_events` (Arc 12) |
| 2 | Monitor › Eventos (`Sidebar.tsx:70` → `/contacts/events`) | lista crua | `/reports/events` |
| 3 | Analítico › Eventos (`Sidebar.tsx:123` → `/analise/events`) | lista crua — **mesmo componente do #2** | `/reports/events` |

#2 e #3 montam o MESMO `EventsPage` (`routes.tsx:78` e `:111`); só o grant ABAC difere
(`contacts.operacao` × `contacts.visualizar`).

**Decisão (2026-07-28):** o #1 já É o dash consolidado que Monitor deveria ter — está só no
lugar errado, escondido como toggle. Rearranjo:

- **Monitor › Eventos** passa a renderizar o agregado (conteúdo do #1) — vira dash com
  entrada própria de menu.
- **O toggle dentro de Sessões sai** (`MonitorScope` volta a `sessions | processes`).
- **Analítico › Eventos** fica com a lista crua, sozinha.

Espelha o padrão do produto: Monitor = estado agregado ao vivo; Analytics = detalhe
retrospectivo — a mesma relação que Monitor › Sessões tem com Analítico › Sessões.

**Defeito a corrigir junto:** `EventsView` envia `period=24h` (`MonitorTab.tsx:794`), mas
`get_agent_events_summary` (`reports.py:1431`) só aceita `from_dt`/`to_dt` — o param é
ignorado, a janela real é o default de 7 dias, e o título i18n diz "últimas 24h". Número
que mente.

**Órfãos achados no mesmo levantamento** (não tratados): `AnaliseComparacaoPage` não tem
rota (arrasta `MetricSelector` junto); `ContactsPage` não é importado no router;
`/reports/agent-events/series` não tem nenhum chamador; chave i18n `nav.service.events`
sem item de nav.

---

## Arc 12 — `segment_id` em `agent_business_events` *(investigado 2026-07-28, não implementado)*

**Lacuna:** a marcação emitida pela tool `agent_event` é atribuída à SESSÃO, não ao
segmento. Numa sessão com vários participantes (primary + especialista, humano + hook de
wrap-up) não dá para saber **qual** emitiu o KPI; `agent_type_id` é a granularidade mais
fina hoje, e ela agrega todos os agentes daquele tipo.

**Achado que simplifica:** o `instance_id` já é decodificado do JWT dentro da tool
(`mcp-server-plughub/src/tools/agent-events.ts:117`) e **descartado** — nunca entra no
evento publicado (`:189-205`). É exatamente a chave que falta.

**Precedente:** `survey_record` **não resolve** o `segment_id` — ele o **recebe**. O skill
passa `$.segment_id`, built-in que o engine já tem em memória
(`skill-flow-engine/src/interpolate.ts:279`), cujo comentário diz que existe "para um skill
passar seu PRÓPRIO segmento ao `survey_record(grain=segment)`". Ver
`skills/agente_nps_v1.yaml:121`.

**Plano decidido — A + C:**
- **A (custo zero):** `segment_id` opcional em `AgentEventInputSchema`
  (`packages/schemas/src/agent-events.ts:111`); o YAML passa `$.segment_id`. Cobre o caso
  comum sem I/O nova.
- **C (rede):** publicar o `instance_id` no evento e enriquecer no consumer via
  `SegmentEnricher.lookup_by_instance` (`segment_enricher.py:63`), adicionando
  `"agent.events"` a `_ENRICHED_TOPICS` (`consumer.py:302`). É o que `mcp.audit` já faz.
  Cobre humanos e replays de DLQ sem penalizar o caminho quente.
- **B descartado** (1 GET extra em `session:{id}:segment:{instance_id}`) — redundante com C.

**Schema:** 1 ALTER aditivo no padrão de `_DDL_SENTIMENT_EVENTS_MIGRATE_SEGMENT`
(`clickhouse.py:238`) + entrada em `_MIGRATIONS` + 3 edições posicionais acopladas
(`_AGENT_BUSINESS_EVENT_COLS` em `clickhouse.py:1588`, `_agent_business_event_row` em
`:2281`, `parse_agent_business_event` em `models.py:1036`). Nenhum consumidor quebra (todos
usam SELECT com colunas explícitas).

**Ganho imediato:** `"segment_id"` no `VALID_GROUP_BY` (`reports_query.py:5684`) é UMA linha
e habilita "KPI de negócio por participante".

**NÃO mexer no ORDER BY:** o ClickHouse só permite acrescentar ao fim, e depois de
`emitted_at` (alta cardinalidade) o `segment_id` não poda nada. Pôr antes exigiria recriar
a tabela — não paga, com TTL de 2 anos.

---

## ~~`agent_events` — fatia 2: DROP da tabela~~ ✅ *(2026-07-29, ver CHANGELOG)*

Resíduo: `scripts/commit-agent-events.sh` é um script de commit one-shot da fatia 1 e
ficou no repo — candidato a `git rm` na próxima passada de limpeza.

---

## ~~`agent_done` de crash-recovery é descartado pelo analytics~~ — RESOLVIDO por remoção *(2026-07-28)*

> **Fechado sem corrigir o campo.** A investigação mostrou que (a) não eram 2 caminhos e
> sim **9** — todo `agent_done` do bridge era descartado, não só o de recuperação — e (b)
> nenhuma métrica de produto lia `agent_events`, então o dilema sobre TMA que travava a
> decisão não existia. A tabela era substrato derivado duplicando `segments` e foi
> descontinuada (fatia 1 acima). O texto original fica abaixo como registro do raciocínio.

<details>
<summary>Registro original</summary>

### `agent_done` de crash-recovery é descartado pelo analytics *(achado 2026-07-27 na F4, não corrigido)*

Dois caminhos de recuperação no bridge publicam `agent_done` em `agent.lifecycle` com **`conversation_id`**:
`process_contact_event` (contact_closed com `ai_completing` expirado) e `_cleanup_stale_completing_at_startup`.
Mas `parse_agent_lifecycle` (analytics-api `models.py`) exige **`session_id`** para o `agent_done` e devolve
`None` sem ele — então **essas linhas nunca chegam em `agent_events`**. O consumidor do routing-engine funciona
(usa só `conversation_id`/`pools`), então a capacidade é liberada corretamente; o que falta é só o registro
analítico.

Descoberto ao remover o `agent_type_id` desses eventos na F4: fui checar quem consumia o campo e a resposta foi
"ninguém, porque o evento inteiro é descartado".

**Por que não foi corrigido junto:** não é do arco de identidade, e a correção não é óbvia. Renomear para
`session_id` faria aparecerem linhas novas em `agent_events` para contatos recuperados de crash — com
`outcome`/`handle_time_ms` ausentes e um `timestamp` que é o da recuperação, não o do fim real do atendimento.
Isso mexe em TMA e taxa de resolução. Antes de corrigir é preciso decidir **o que essas linhas devem
significar** (evento de recuperação distinto? `outcome` sintético? excluir do TMA?) — decisão de produto sobre
métrica, não conserto de campo.

**Nota transversal:** o descarte é silencioso (`return None` sem log), o que é o padrão que a § *Postura de
Engenharia* do CLAUDE.md nomeia. Independente da decisão acima, o parser deveria **logar** o motivo do skip —
foi só por acaso que isso apareceu.

</details>

**Epílogo (2026-07-28).** O log de descarte chegou a ser adicionado e durou uma hora: ele
foi o instrumento que revelou os 9 produtores, e saiu junto com o ramo que ele instrumentava.
A lição que fica é a da nota transversal, não a do campo: *o `return None` mudo escondeu por
meses uma tabela inteira sem produtor válido, e só apareceu por acaso.*

---

## Posição na fila — resíduos após o fix do `queue.position_updated` ✅ *(2026-07-27, ver CHANGELOG)*

O evento voltou a ser publicado e `queue_position`/`estimated_wait_ms` são corretos. O que ficou:

- **Nenhum canal consome o evento.** O comentário do código promete "channel-gateway (to inform customer)", mas
  o channel-gateway só assina `collect.events` — **mostrar a posição ao cliente nunca foi implementado**. É
  feature, não regressão: exige consumidor no gateway + render por canal (webchat WS; voz = prompt falado).
- **Ruído do drain na tabela.** O drain periódico re-enfileira o mesmo contato a cada ~5 s e cada ciclo grava um
  par `queued`+`position_updated` (10 linhas para 1 contato em 45 s). Ou o publish passa a ser condicionado a
  MUDANÇA de posição, ou a série é agregada na leitura. Decidir antes que a tabela vire lixo em produção.
- **`available_agents` é enganoso**: conta instâncias no set `ready` (SCARD), não vagas livres — um agente
  lotado ainda aparece como "disponível". Renomear para `ready_instances` ou passar a contar capacidade real.
- **`queue_length` não é persistido**: o payload leva, a tabela `queue_events` não tem a coluna. Se o tamanho da
  fila no instante interessa ao relatório, é `ALTER TABLE … ADD COLUMN queue_length Nullable(Int32)` + a linha no
  `CREATE TABLE` do `clickhouse.py`.

---

## Journey (retorno) — modelo de 3 níveis *(design fechado 2026-07-08, pré-código)*

**Contexto:** o modelo de 3 níveis (N3 negocial `workflow` / N2 acesso a canais / N1 I/O — perfis `agent`) faz
voltar a necessidade de amarrar vários contatos a um processo de longa duração. A entidade `Journey` (Arc 10) foi
removida no Arc 19 Fase F (dualidade contact/workflow; "rastreabilidade via `parent_session_id`, sem entidade").
O retorno é **como lente + camada mínima de alias**, não como entidade.

**Decisão (D1.5):** journey = componente conexa de sessões sob (proveniência ∪ alias), identificada pela **raiz
canônica** valorada em `session_id`. Descartado D1 puro (não resolve cenário 2-unify nem 3-inbound — proveniência
é imutável) e D2 (entidade — reintroduz o que o Arc 19 removeu). Insight: sem merge, `journey_id=session_id` é só
`origin_session_id` replicado; o merge/alias é a única coisa que a derivação por proveniência não expressa.

**Invariantes:**
- `root_session_id` imutável, **nunca null** (param propagado no `delegate`/`collect`/`task` = do chamador; senão
  auto-mint = `self`). Propagação é de plataforma (injetada como o `origin_session_id`), não campo de fluxo.
- Fonte de verdade = `root_session_id` + `journey_aliases`; `sessions.journey_id` = **cache** eventualmente
  consistente (refresh no merge; reads não dependem dele em v1 — resolve por union-find).
- Merge sempre **novo→antigo** (ordem total por `started_at`,`session_id`) ⇒ floresta sem ciclo, sem cycle-guard.
- `journey.merges` = topic de **1 tipo**; proibido reviver entidade/lifecycle/merge-split/`journey.events` (9 tipos).
- Mantém `origin_session_id` (1 salto, desenha o `SessionTrace`) **E** `root_session_id` (raiz transitiva, agrupa).

**Fases:**

| Fase | Entrega | Depende de |
|---|---|---|
| J1 ✅ (2026-07-09, ver CHANGELOG) | `root_session_id` (schemas + CH + nascimento + propagação automática); `journey_id` cache=root no open. Cenários 1 e 2-com-journey. Persistência da raiz via **enrichment central no consumer** (lê ContextStore autoritativo — não repete root em cada evento nem toca routing-engine). Validado E2E (`infra/test/smoke_journey_root.sh`, transitividade W3 origin=W2/root=W1). | — |
| J2 ✅ (2026-07-09, ver CHANGELOG) | `/reports/journeys` (proveniência-only) + filtro `root_session_id` no `/reports/sessions` (drill) + Vista Processos (`AnaliseJourneysPage`, repurpose de `/analise/processos`) + drill 3 níveis + toggle "significativa". Só Analytics (Monitor fica p/ depois). | J1 |
| J3 ✅ (2026-07-09, ver CHANGELOG) | `journey_merge` tool + `journey.merges` + `journey_aliases` + union-find (resolução na leitura via `transform()`; cache `journey_id` **diferido**, não refresh — reads por union-find) + `PendingEntry.root_session_id`. Cenário 2-unify validado E2E; cenário 3 = pipeline pronto, falta o skill disparar a tool. | J1, J2 |
| J4a ✅ (2026-07-10, ver CHANGELOG) | Leitura N3: `session_signal` grain=`journey` + métricas de processo (`business_outcome`, `business_duration_ms`, `signal_count`, `nps_avg`/`csat_avg`/`ces_avg`) no `/reports/journeys` + colunas Outcome/NPS na Vista Processos. | J2 |
| J4b ✅ (2026-07-10, ver CHANGELOG) | Hook **genérico** `on_process_end` (dispara em desfecho terminal, carimba `session.process_outcome`; mecanismo igual aos outros hooks, survey é 1 consumidor). Agente `skill_journey_survey_v1` cria survey OUTBOUND (`survey_link_create`, form `dialog_nps_buttons`) grain=journey keyed na raiz. Validado E2E via trigger slug→pool (`/channel/webhook/{slug}`). | J4a |
| **J4c ✅** (2026-07-13, validado E2E — spec `docs/product/journey-j4c-survey-collect-spec.md`, ADR `adr-outbound-survey-as-collect-contact.md`) | **Survey outbound = contato via `collect` (Arc 19 suspend/resume), não sinal solto.** Modelo 3 camadas: **N3** (workflow de survey, **channel-agnostic**, faz `collect`+suspende) → **N2** (handler `persistCollect` = resolvedor de canal **único e cego ao processo**: alcançabilidade via Resolvedor de Identidade + `channel_policy` declarativo de N3 + consentimento/política como slots plugáveis) → **N1** (sessão-filho **roteada** a um pool de survey, herda `root`→membro da journey). **Opção A + criação LAZY (decidida 2026-07-10):** separa o assíncrono (esperar o cliente) do síncrono (o survey). **(1)** `collect` = convite: N2 **entrega o link + guarda pending, suspende — zero sessão/recurso/metering** até o clique (sem clique→timeout→nada alocado). **(2)** clique com token válido = **inbound PADRÃO** (cliente presente), roteado ao pool de survey → Routing admite (cota + `max_concurrent_sessions`) + Core metera — **limites só no engajamento real**; `dialog_runner` (agente único, DialogForm por config) renderiza **ao vivo** (síncrono → `menu` funciona, e o princípio "agente único interpreta o form" sobrevive). **(3)** fim do survey → `session_closed` + sinal grain=journey no close + `collect.responded`→resume N3 (collect resolve **no fim**). Resolve a regra de perfil (`menu`≠`suspend` no mesmo skill) e o custo de capacidade do assíncrono. "delega"≠step `delegate()` (é inbound, sessão própria). **Segmentação/billing por pool** (sem canal-classe novo, sem carve-out — capacity-based; `max_concurrent_sessions` = botão de volume). Trabalho central: **wirar `persistCollect`** (hoje só `persistDelegate`; `collect` cai em wall-clock). `survey_link_create` = legado/anônimo. **Invariantes:** N3 nunca nomeia canal (só `channel_policy`); N2 nunca ramifica por `skill_id`/`campaign_id` (guard de CI estilo `check_config_invariants.py`); escolha de canal = concern reutilizável. Fatias J4c-1..5. Demo = web+mock; SMS/e-mail/consent/policy = slots futuros por config. | J4b |
| J5a ✅ (2026-07-14, ver CHANGELOG) | `@ctx.journey.*` **vivo** (bridge resolve a raiz canônica → `journey_id` no `/execute` → `journeyId` no engine; TTL próprio de 30d) + **merge acíclico por construção** (aresta raiz→raiz via mapa de aliases no Redis; idade vem do stream canônico, não do `meta` que só o webchat escreve) + 12 testes do `journey_merge`. Validado E2E com escritor e leitor em sessões diferentes da mesma journey, com controle negativo. **J5a-2 ✅ (2026-07-22, ver CHANGELOG):** fechada a **escrita IMPERATIVA** — `context_set` (skill-flow) e `/api/inject-context` (supervisor) gravavam raw no hash da sessão; agora roteiam pelo helper único `writeContextTag` (`journey.*` → hash do processo/raiz canônica, TTL 30d; reusa `resolveJourneyRoot`, sem dep de `@plughub/sdk`). Smoke `smoke_journey_context.sh`. | J3, J4 |
| J5b ✅ (2026-07-14) | i18n dos **enums** na Vista Processos. `status`/`outcome`/`business_outcome`/`channels` chegavam crus da analytics-api e eram renderizados assim (o operador via inglês técnico em pt-BR); a moldura já passava por `t()`, faltavam os **valores**. Reusa `sessions.status.*` (já existia no namespace) e adiciona `enums.outcome.*` + `enums.channel.*` (en+pt-BR) — não duplica dicionário. `defaultValue: <valor cru>` em todos: enum novo no backend degrada para o valor cru em vez de quebrar a tela. `t` passa por **parâmetro** nos helpers (a regra proíbe `useTranslation` fora de componente). `title` guarda o valor cru para debug. | J5a |
| — (app-wide, fora do Journey) | **Guard de rota ABAC**: nenhuma página de `analise/` tem gate próprio — só o Sidebar. Deep-link contorna a UI (o dado segue filtrado por `accessible_pools` no backend). Consertar só a de Journeys seria cosmético; é um item do app. | — |

### Journey — 3 itens pendentes: natureza + mini-plano (levantamento 2026-07-23)

Cruzados contra o código. **São três naturezas distintas** — só o Item 1 é entrega de valor acionável.

**Item 1 — sinal N3 no drill da Vista Processos ✅ ENTREGUE (Fatias 1+2, 2026-07-23 — ver CHANGELOG).**
Painel **PROCESS SIGNAL** no cabeçalho do L2 (desfecho+provisório, duração, NPS/CSAT/CES, `signal_count`);
`csat_avg`/`ces_avg` agora renderizados. Fatia 1 = UI-only (`selectedJourney` no `AnaliseJourneysPage` →
prop). Fatia 2 = filtro `root_session_id` no `/reports/journeys` (resolve canônico, ignora janela+significant)
+ rebusca no `JourneySessions` para deep-link. Validado (clique + deep-link). *Limitação:* fetch direcionado
varre `sessions` por lista de roots-membros — medir se houver journeys enormes sob merge.

**Item 2 — cache `sessions.journey_id` diferido** *(otimização adiada por decisão, não é bug)*. A coluna
existe (escrita = raiz no nascimento) mas **não é refrescada no merge**; reads resolvem por union-find sobre
`journey_aliases` (`_journey_resolved_map`). "Ativar" = refrescar `journey_id` no consumer de merge para
`GROUP BY journey_id` direto. Custo atual baixo (tabela de aliases minúscula, 1 hop pré-resolvido), correção
intacta (cache nunca é lido como verdade). **Só sob pressão de latência/volume medida.**

**Item 3 — guard de rota ABAC** *(dívida app-wide, defesa-em-profundidade/UX, NÃO vazamento)*. Rotas
`analise/*` (`routes.tsx`) sem wrapper — só o `Sidebar` esconde o nav; deep-link renderiza o chrome. O dado
**segue filtrado** por `accessible_pools` no backend (`_apply_pool_scope`), então não vaza. Modelo de correção
já existe no repo: `RequireEvalAccess` (guard por-rota das telas de Avaliação, hoje hard-coded a
`module='evaluation'`) — generalizar (prop `module`) ou criar `RequireAbac` irmão e envolver `analise/*`.
**App-wide** (analise/monitor/config são todos nav-only) — melhor numa passada dedicada, não enxertado no
Journey.

### Journey — Árvore de proveniência (T1–T6) ✅ COMPLETA (2026-07-14/15)

Toda a árvore de proveniência entregue e validada — movida para `CHANGELOG.md` (entradas **"Journey T1–T5"**
e **"Journey T6"**): T1 persistir `origin_session_id` · T2 desfecho = raiz (+ provisório) · T3 `journey:
inherit|new` · T4 `spawn_reason` · T5 UI em árvore + prefixo `PRC-` · T6 rastro forense bidirecional
(`GET /reports/sessions/{id}/trace` + `TraceDrawer`). Bug colateral fechado no caminho: `/reports/sessions`
nunca rodava a query principal (alias-shadowing → fallback mudo pelo tier 3). Design/decisões e não-objetivos
na spec `docs/product/journey-provenance-tree-spec.md` (§9). ⚠️ T2 mudou números já exibidos (desfecho passou
a ser o da raiz) — correção, quebra comparação com prints anteriores.

---

## Deploy de skills — cleanup de campos órfãos *(follow-up do redesenho D1–D4, 2026-07-13)*

Depois do modelo novo de deploy ("uma definição editável + cópia imutável no slot"), ficaram órfãos:
dropar `flow_draft` e `deploy_status` do schema Prisma (agent-registry) e remover o endpoint
`POST /v1/skills/:id/deploy`. Deixados para depois de o modelo novo rodar; histórico completo do
redesenho no `CHANGELOG.md`.

---

## Analytics — revisar workarounds pré-`row_version` *(resíduo do fix de 2026-07-13)*

Com `sessions` já em `ReplacingMergeTree(row_version)`, revisar (e provavelmente remover) os workarounds
de `COALESCE` / `channel=""` no analytics-api que existiam **só** para mitigar a corrida entre tópicos.
Histórico do bug e do fix no `CHANGELOG.md`.

---

## Tópicos Kafka órfãos — achados do saneamento do doc *(2026-07-27, doc ✅ saneado)*

O saneamento de `docs/kafka-eventos.md` (✅ feito, ver CHANGELOG) reconciliou a doc contra o código e expôs
**quatro defeitos reais** — nenhum é de documentação:

> **Propósito declarado (2026-07-27, decisão do dono do produto):** estes eventos são **negociais, de
> MEDIÇÃO** — contam ocorrências nos fluxos de agentes gerados nos skills, para análise e comparação
> posterior. Não são mecanismo (a ação já acontece por outra via) e **não devem ser removidos**: estão
> incompletos, não mortos. Isso muda a pergunta de "remover ou ligar consumidor" para **"onde essa medição
> deve aterrissar"**.
>
> **Substrato que já existe (avaliar ANTES de criar consumidor/tabela novos):** o **Arc 12** faz exatamente
> isso — `agent.events` → ClickHouse `analytics.agent_business_events`, com `category` hierárquico
> (`pool_id.skill_id.metric_key`, decomposto em `category_l1..l4`), endpoints
> `/reports/agent-events/{series,summary,categories}` e integração com a lente de deploy do Arc 6 Fase 2
> (`metrics[]=agent_event:{category}` — "esta versão do skill mudou a taxa de ocorrência?"). Se a medição de
> regras entrar por aí, ganha série temporal, drill e comparação por versão **sem infra nova**.

1. **`rules.escalation.events`** — telemetria de escalação disparada (modo `active`), sem consumidor. (NÃO é a
   via da escalação — correção de um diagnóstico meu errado: `escalator.py:79` chama
   `POST /tools/conversation_escalate` e só depois publica o evento, `:91`.) Falta o destino de medição.
2. **`rules.shadow.events`** — o shadow mode existe para MEDIR o que uma regra faria antes de ativá-la; hoje o
   único registro é um `logger.info`. É o caso em que a medição É a feature.

**Opções para os dois** (mesma decisão): (a) o rules-engine passa a emitir `agent_event` com categoria
(`{pool}.{skill}.rule_escalation` / `.rule_shadow`) e os tópicos `rules.*` são aposentados — reuso máximo;
(b) consumidor dedicado no analytics com tabela própria (mais fiel ao schema atual, mais infra); (c) manter
publicando e aterrissar depois. **Correção pendente no CLAUDE.md** em qualquer caso: a tabela de tópicos lista
`rules.escalation.events` → consumidor `Routing Engine`, o que nunca foi verdade.
3. **`agent.done`** — ✅ **REMOVIDO (2026-07-27, ver CHANGELOG).** Publicação órfã + dupla no mcp-server; teste
   reescrito para cobrir as vias reais. Resíduo: `issue_status` não trafega mais em nenhum tópico (só era
   publicado no órfão; segue validado na entrada). Se o analytics precisar dele, adicionar ao `contact_closed`.
4. **`usage.cycle_reset`** — ✅ **REMOVIDO (2026-07-27, ver CHANGELOG).** Consumo morto no usage-aggregator; o
   reset segue pelo `POST /admin/cycle-reset` (mesma classe). O schema fica em `usage.ts` — se o caminho por
   evento for desejado, falta o PRODUTOR.

Também corrigido na doc (era erro de documentação, não de código): `conversations.events` — o tópico mais
movimentado da plataforma — estava listado como "nome obsoleto que não existe mais"; e cinco tópicos
documentados **não existem** (`conversations.session_opened`, `conversations.message_sent`,
`conversations.abandoned`, `rules.session_tagged`, `gateway.heartbeat` — os três primeiros confundiam evento
com tópico).

**Dívida de contrato:** `conversations.events` não tem schema Zod único, sendo o tópico central e o de maior
fan-in (5 produtores × 6 consumidores). Contraria o princípio "todo evento cross-package tem contrato
validado" registrado no próprio doc.

**Correção pendente no CLAUDE.md**: a tabela de Kafka topics lista `rules.escalation.events` → consumidor
`Routing Engine` e `agent.done` → `Rules Engine, Analytics`. Ambas falsas — atualizar junto com a decisão (1).

**Método:** cross-check contra `packages/analytics-api/src/plughub_analytics_api/clickhouse.py` (DDLs reais) e
`CLAUDE.md § Kafka Topics` (que já está correto e serve de gabarito). Baixo risco, alta clareza — chore de doc.

---

## Resolvedor de Identidade — próximos passos (Fase A ✅ Slices 1–4; falta Slice 3 + Fase B) *(2026-07-02)*

**Estado:** Fase A completa e validada (ver `CHANGELOG.md` § Slices 1/2/4 e `docs/product/identity-resolver-fase-a-plano.md`). Cadastro mínimo interno sem CRM: índice Redis + durabilidade PG (`schema identity`) + retomada cross-canal + `sessions.customer_id` = nativo no fechamento (conserta `contact_id`-como-`customer_id`, reconecta H1/H2/H3).

**Próximo (recomendado — desbloqueia o valor no demo):**
- **Wiring do intake para escrever `caller.customer_id` NATIVO ✅ (2026-07-03, CHANGELOG).** `agente_portabilidade_intake_v1` chama `customer_resolve` (âncoras `numero_atual`+`contact_identifier`, kind detectado por choice `contains "@"`) e grava `caller.customer_id` via `context_set` **pré-ramificação** (não `context_tags.outputs` — `context_set` é o caminho já provado no runtime nativo do bridge e é a tag exata que `_resolve_close_customer_id` lê). Validado no demo: 2 intakes, mesmo número → mesmo `cus_…` em `sessions.customer_id`. Deploy exigiu `set-next`+`promote` (pool migrado a `PoolSkillSlot`; YAML+restart republica `skill.flow` mas não re-snapshota o `current`).
- **Slice 3** — campos `customer_resumable`/`resume_policy` no step `delegate` (schema `skills.ts` + propagação no engine até o callback `persistDelegate` — **verificar** se o engine repassa campos novos) + `session_resumed` com `resume_origin: same_channel|token|identity`. Ver plano §2 Slice 3 + spec §6/§11.
- **Fase B** — identidade progressiva (anexar âncora nova a cliente existente em match parcial — hoje retorna o existente sem indexar as novas), `external_refs` (CRM id → `external_refs`, não como chave), merge de clientes. Spec §5/§12.
- **Consolidar `caller.customer_id = nativo` no step CRM `resolve`** (`agente_contexto_ia_v1.yaml`): hoje o `buscar_crm` grava `caller.customer_id` com o id do CRM; no modelo novo o nativo é a chave e o CRM vai p/ `external_refs`. Spec §13.8-5 / §3 nota de migração.

**Candidato Fase B/C — gate de validação p/ steps sensíveis + OTP de posse de canal (proposta 2026-07-02, REVISADA 2026-07-03):** liberar sequências **sensíveis** só com validação da identidade/posse que entrou em contato. Duas classes de verificação, decisão consciente:

- **Posse de canal (NOVO — plataforma PODE ser autoridade):** OTP interno (plataforma gera+envia+valida) prova que quem está na conversa **controla o handle agora** → eleva a âncora `phone`/`email` de fraca→verificada. Isto **NÃO** é autoridade de identidade-de-registro; é autoridade de posse de canal (a plataforma é dona dos canais). Gate para ações **não-sensíveis / baixo-médio risco** (retomar carrinho, ver histórico, confirmar dado cadastral) e é o que torna `resume_policy: auto` seguro (vs foot-gun).
- **Identidade-de-registro / credencial / KYC / pagamento (INALTERADO — só retaguarda):** continua **sempre** delegada ao tenant via `identity_verify` MCP; a plataforma relaya e guarda só o veredito. Princípio 7 preservado *neste eixo*.

**Correção de posição:** a proposta original (2026-07-02) proibia OTP próprio da plataforma ("só se emitido pela retaguarda"). Revisão: permitir OTP de **posse de canal** exige **emenda explícita ao princípio 7 e §4.4** — hoje a spec reserva TODA elevação de `confidence`/`verified` ao backend (§ linha 105: "confidence reflete o veredito do backend, não um palpite nosso"). Emenda = separar as duas classes acima; **fazer a emenda antes do código**.

**Não-negociável de modelagem — classe na DADO, não só na prosa:** `confidence` escalar único colapsa semânticas de confiança não-intercambiáveis (0.95-OTP ≠ 0.95-CRM). Adicionar `verification_method`/`verification_class ∈ {channel_otp, backend_identity, none}` ao lado de `verified_at` na `customer_secondary_keys` (colunas já existem: `confidence`, `verified_at`). Consumidores gateiam pela classe certa: `auto`-resume → `channel_otp` recente; ação sensível → `backend_identity`. Veredito escopado a `(customer_id, kind, value_hash)`, nunca ao handle global.

**Precisões:** (a) OTP mata **spoof**, não a **ambiguidade de handle compartilhado** (`matched_by="ambiguous"` ainda precisa de discriminador — pessoa escolhe conta / backend desambigua); não é primitiva de merge. (b) "Nunca guardar o código" tem asterisco: o **desafio** gerado vive efêmero server-side `{t}:otp:{challenge_id}` (hasheado, TTL, uso único, bound a session+customer_id) p/ comparar; a resposta digitada do cliente é `@masked.*` (comparada e descartada); só o veredito persiste. O desafio **não** usa o namespace `@masked.*`. (c) Primitiva = **tools MCP** `otp_challenge`/`otp_verify` via `invoke` (não novo step-type). Composição: `invoke otp_challenge` → `menu masked:true` (coleta código) → `invoke otp_verify(@masked.code)` → `choice` no veredito. (d) **Degradação graciosa** obrigatória (código errado/expirado/max-tentativas → modo baixa-confiança ou escala; nunca hard-block). (e) Entrega pelos adapters de canal existentes; créditos/provedor (SMS/WA template) = integração/custo do tenant; anti-enumeração (só OTP p/ handle que o cliente forneceu no contato que ele iniciou — nunca "esse número tem conta aqui?") + consentimento no envio proativo.

**Fronteira (clarificação 2026-07-03):** OTP é **fator componível / step-up**, nunca o autenticador final. A plataforma provê a primitiva + o veredito-com-classe; **o nível de segurança é definido pelo fluxo do tenant** (regra de negócio, não modelada aqui). Não-sensível: fluxo pode aceitar `channel_otp` só. Sensível/regulado: fluxo **encadeia** OTP (posse) → `identity_verify` retaguarda (identidade-de-registro/KYC) — a plataforma nunca vira autenticador final. `resume_policy: auto` em `channel_otp` é default opt-in do fluxo, não mandato. Requisito que isso impõe: `verification_class` no dado (a primitiva é neutra; a classe dá ao fluxo o poder de compor a barra "posse E/OU identidade").

**Sequência:** o wiring de intake (gargalo) está ✅. OTP é independente do Slice 3 mas complementar — Slice 3 define o campo `resume_policy`, OTP dá a prova que deixa `auto` disparar com segurança. Config no namespace `identity` (tamanho, TTL, máx-tentativas, rate-limit). **Próximo artefato:** mini-spec de `otp_challenge`/`otp_verify` (contrato das tools, chaves Redis, config, fluxo anti-enumeração, emenda ao princípio 7/§4.4) — criticar antes de codar. Ver spec §4.4 (dois momentos), §5, §6/§8 (gate no delegate), princípio 7.

**Dívida colateral ✅ (2026-07-08):** os 2 testes pré-existentes de `test_webhook_bridge.py` (drift anterior, sem
relação com identidade) foram corrigidos — `test_resume_publishes_agent_ready_and_agent_done` usa `AsyncMock` no
`producer.send` (awaitable p/ o `create_task`); `test_process_inbound_does_not_call_resume_handler_for_customer_msg`
deixa o `process_inbound` correr contra o `mock_redis` (a função `forward_inbound_to_active_agent` não existe mais),
com `get`/`hgetall` configurados p/ pular o retry-loop e não vazar coroutine. 17/17 verdes. Ver `CHANGELOG.md`.

---

## OTP produção + primitivo de diálogo genérico (survey + OTP) — resíduos *(ADR ainda Proposto; primitivo v1 + Fatias 1/2 ✅, ver CHANGELOG)*

OTP Fase B é um **MVP tool-based** (identidade progressiva + `verification_class` + `OtpService` + gate `possessed`);
o dialog-primitive v1 (`dialog-api`, `skill_dialog_runner_v1`, `form_get`, editor `/config/dialog-forms`) está entregue
e adotado por OTP, NPS e survey multi-pergunta. ADRs: `docs/adr/adr-otp-workflow-and-dialog-primitive.md` (**Proposto**),
`docs/adr/adr-identity-channel-possession.md`; spec: `docs/product/dialog-primitive-and-runner-design.md`.
**Inegociável (invariante):** o código do OTP nunca passa pela mão de um agente — gerar/enviar/verificar ficam no `OtpService`/channel-gateway.

**OTP — produção (ADR não implementado)**
- **D1 — OTP como workflow negocial + especialista de canal** (`delegate-workflow-io`, Arc 19) segue **só desenhado**: workflow channel-abstract exposto como step-up reusável (`{verified}`) + especialista Tier-3 dono do canal. Hoje é tool-based no intake. Item 6 (OTP como step-up genérico) depende disto.
- **Item 1 — entrega real** (SMS/e-mail, envio por canal ≠ sessão = posse forte) **adiado até termos canais**; vira o `collect` do especialista.
- **Trilha B / D3 — tela de OTP em Configurations**: tuning numérico (TTL, tentativas, rate-limit, canais de posse) é **env-only**; falta namespace `identity`/`otp` no config-api + bindings (`form_id` dos prompts, `template_id` de entrega).
- **Trilha C — segurança**: auditoria de challenge/verify (Kafka/`mcp.audit`, item 5); **lockout crescente** (item 7); **testes de unidade** do adapter/endpoints (item 8).
- **Trilha A** — textos/i18n dos prompts de OTP (item 3) *(verificar: o retry na mesma superfície já saiu em 2026-07-07)*.
- **D2** — atualizar o spec de survey (§17/§19) para consumir o primitivo de diálogo *(verificar se já feito)*.

**Limitações declaradas do primitivo (aceitas, sem fix)**
- **Hooks de fim-de-contato não podem delegar** — `suspend` = hook concluído → o contato fecha antes de renderizar. Por isso o NPS ativo (`agente_nps_v1`, `on_contact_end`) roda **inline** (form_get + menu dinâmico), não via runner. Runner só serve chamadores que podem suspender.
- **Delegate de nível único** — aninhar o runner dentro do collector colide em `session.delegate_resume_token` (rejeitado).
- **`channel_policy: elect` adiado (decisão C, 2026-07-08)** — eleição de canal hoje é uma `question` do form lida pelo workflow; o `elect` de 1ª classe conflita com a segregação de perfil (reach/`collect` é exclusivo de `workflow`, runner é `agent`). Reabrir quando houver fluxo que exija o runner **ele mesmo** re-despachar cross-canal (aí decidir A escopado vs B pleno).
- **Binding do form no runner é contexto de delegate** (`@ctx.session.dialog_form_id`), não `$.config` — o hook `$.config` existe, mas a migração para deploy-por-slot só foi feita no `skill_survey_multi_v1` *(verificar se o runner/OTP ainda dependem do ctx)*.

**Config params por deploy**
- Skill parametrizado **exige deploy por slot** com `config_json.form_id` (`set-next` + `promote`); sem isso o `form_get` falha em runtime.
- **Typo de `source` não é tratado no deploy** — o lint no publish (`configParamSourceWarnings`, agent-registry) é apenas **avisador, não-bloqueante**.
- Worker legado `skill-flow-worker` fora de escopo (Arc 19 o deprecou).

**Editor de dialog-forms `/config/dialog-forms` — 2ª passada**
- Reordenar nós por **drag** (hoje setas ↑↓); **edição de locale lado-a-lado** + progresso de tradução estável; **preview** do que o cliente vê.
- **Auth no write** — hoje **aberto**, sem gate ABAC `config.*`.
- Validação client-side com mensagens (form_id slug, `output_key` único, `dimension_id` snake_case); confirmação ao descartar rascunho (dirty/blocker); `interaction=form` com múltiplos `fields`.

**Survey / scoring**
- `survey_question` **reutilizável** — fora do 1º corte, ainda pendente.
- **Entrega do link web**: falta só o **operacional** (tenant apontar `survey.link_delivery.webhook.url` pro gateway SMS/e-mail dele + `PLUGHUB_SURVEY_LINK_WEBHOOK_TOKEN`); `SmtpProvider` nativo é opção futura; **UI dedicada** para `link_delivery` é follow-up (hoje só config genérica). §9.2/§19 de customer-surveys.

**Guard de teardown-hook (Tarefa #17) — endurecer**
- O guard atual (`_validate_teardown_hooks`/`_load_skill_steps` no `registry_syncer.py`) é **read-only, fail-open**: só loga ERROR. O desenho pede **rejeitar no deploy/sync** (agent-registry/RegistrySyncer) quando o flow de um skill deployado em pool-alvo de `PoolHooks.on_contact_end/on_human_end/post_human` contiver step que suspende — reusando a varredura do `_computeFlowModel` **estendida com `delegate`** (hoje `_computeFlowModel` só olha `suspend`/`collect`). Alternativa descartada por ser menos robusta: flag declarado `classification.execution_context`.

---

## evaluation-api — 10 testes de `test_router.py` quebrados por drift de ambiente *(achado ao vivo, 2026-07-02)*

Encontrado ao validar o fix de self-view (ver `CHANGELOG.md` § "evaluation-api — bug self-view..."): rodando
a suíte local (`pytest`, Python 3.12.3, `pytest-9.1.1`) — ambiente mais novo que o usado da última vez que os
`.pyc` cacheados foram gerados (Python 3.10) — **10 de 83 testes falham**, todos em `test_router.py`, **nenhum
relacionado à mudança de self-view** (confirmado por leitura: os testes que tocam `_compute_result_scope`/
`list_results` — seção T10-C de `test_available_actions.py` + o novo teste de regressão — passam 100%).
Três causas raiz distintas, todas pré-existentes:

1. **`AsyncMock.keys() returned a non-iterable` (7 casos)** — `_row()` em `db.py` faz `dict(record)` sobre o
   retorno de `fetchrow()` de um `MagicMock()` fake; a versão mais nova da lib `mock` (stdlib do Python 3.12)
   trata isso diferente. Afeta `TestIngest` (4 casos, via `_db.set_contestation_state`) e `TestResults` (
   `test_list_results` via `_db.get_campaign` — chamada pré-existente no handler pra montar
   `available_actions`, nunca mockada pelo teste; `test_lock_result` via `_db.lock_result`).
2. **`422` em vez de `200`/`400` (2 casos)** — `test_review_result`, `test_review_invalid_outcome`: o schema
   de validação do endpoint `/review` evoluiu desde que os testes foram escritos.
3. **`'State' object has no attribute 'redis'` (2 casos)** — `TestContestations::test_create_contestation` e
   `test_cannot_contest_locked_result`: o endpoint legado de contestação (`/v1/evaluation/contestations`)
   passou a exigir `request.app.state.redis`; a fixture `_app_with_mocks` não seta isso.

**Não bloqueia** nenhum trabalho corrente — documentado aqui só pra não perder o achado. Corrigir exige (1)
atualizar `_row`/os testes pra funcionar com o mock mais novo (ou fixar versão de `mock`/pytest do projeto),
(2) alinhar `test_review_*` ao schema atual do endpoint, (3) a fixture `_app_with_mocks` setar `state.redis`
(`AsyncMock()`) por padrão.

---

## Flow — step de expressão sandboxed (NÃO eval cru) *(decisão de design, 2026-06-28)*

**Necessidade**: valores computados / lógica mais rica em flows (ex.: o loop p/ ler o form JSON de pesquisa de
satisfação; condições derivadas além de JSONPath em `choice`). **Ideia descartada**: um step que roda
**JavaScript livre (`eval`)** com acesso ao ContextStore — quebra invariantes (Redis só via routing/skill-flow,
MCP audit, masking/LGPD, isolamento de tenant) e abre RCE/exfiltração/loop infinito.

**Recomendado**: **step de expressão sandboxed, read-only**:
- avaliador de expressão **restrito** (estilo CEL/jsonlogic), **puro e determinístico**, **sem I/O nem rede**,
  com limite de CPU/tempo; lê `@ctx.*` (respeitando escopo/visibility), **não** escreve direto no Redis;
- saída tipada gravada via os mecanismos já existentes (`context_tags`/output), nunca acesso bruto ao store;
- cobre a maioria dos "flows complexos" sem o buraco de segurança do eval.
- **Casos específicos já têm caminho seguro**: pesquisa de satisfação → form JSON interpreter + menu dinâmico
  (decisão B do ADR de surveys); lógica que não cabe em expressão → step `reason` (AI Gateway + `output_schema`).
- **Código de verdade** (Turing-completo) só no **SDK/agente nativo** (runtime controlado, já auditado), nunca
  como step de flow.

Invariante a preservar: nenhum step de flow executa código arbitrário do tenant com acesso ao runtime interno.
*(discussão; sem implementação)*

---

## Agent Principal — identidade de máquina p/ agentes IA *(spec, 2026-06-28)*

Identidade de máquina (`subject_type:"agent"`) p/ agentes nativos e externos se autenticarem, distinta das
roles humanas; capability vem do `agent_type` (registry), auth-api só emite/rota credencial; audit por
`principal_id`. Nativo = auto-provisionado, **sem UI**; externo = cadastro + secret (API/CLI; UI enxuta na F3).
Fases F1–F4. **Spec**: `docs/product/agent-principal-identity-spec.md`. *(discussão; não implementado)*

---

## Dashboards — cobertura de catálogo *(spec, 2026-06-28)*

O sistema composável (estilo Grafana) **já existe** (Dashboard #35/Arc 16: DisplayTool registry, grid,
Add Card 3-passos, runtime filters, `/reports/display/*`). Fases (spec): **F1 cobertura** — expor no
`ENDPOINT_CATALOG` os relatórios ausentes (segmentos/complexidade, disponibilidade, Fila/SLA, Pools/Infra,
qualidade/calibração, surveys, performance diária) via o contrato existente; **F2 consumo no Home** — `HomePage`
renderiza o dashboard do usuário (destravar p/ todas as roles; builder segue em Config/admin); **F3 allowlist +
starter por role** (`role_catalog:{role}` no Config API: admin define componentes liberados + layout starter;
reconcile no load); **F4 picker do usuário** (escolhe/arruma dentro da allowlist; layout pessoal já existe).
Escopo de dados sempre via ABAC/`accessible_pools`/`supervised_*` no endpoint. **Decisão: NÃO** construir
datasource/query-builder genérico (dado interno); novos tools (heatmap/gauge/leaderboard) só sob demanda.
**Spec**: `docs/product/dashboard-catalog-coverage-spec.md`. *(discussão; não implementado)*

---

## Isolamento do substrato por `origin` — Fase 2 (adiada) *(arco completo 2026-06-25; histórico no CHANGELOG)*

**Fase 2 — ADIADA por decisão (2026-06-25), não enterrada.** Conteúdo: partição CH
`PARTITION BY (toYYYYMM(date), origin)` em tabelas novas/migração versionada (lifecycle/LGPD; **não**
in-place — CH não altera partition key in-place); campo `pool.origin_class: production|import|review`
(default production), **ortogonal a `agent_kind`**, como atalho/validador p/ pools dedicados + eixo de
agrupamento na UI.

**Por que adiar:** a fase 2 é **governança/lifecycle, não correção**. A separação dos dados (o problema
real) já está garantida pelo **filtro de leitura default `live`** (passo 4) + sampling (passo 5); a partição
não muda nada disso. Hoje não há importação externa real e a reavaliação é de volume mínimo → custo/benefício
não fecha.

**Gatilho que reativa (vira necessária, não opcional):** entrada de **importação externa real com obrigação
de retenção/erasure própria** (LGPD — dado de terceiro com prazo distinto, ou direito ao esquecimento que
precise expurgar **só** o `import`/`reeval`). Nesse cenário o filtro de leitura não basta: precisa da
separação **física** para `DROP PARTITION` barato/limpo (a alternativa, `ALTER … DELETE`/mutation, é pesada
e não-particionada). Enquanto esse requisito não existir, fica como backlog.

---

## G-PROBE — Auth ABAC/serviço nos endpoints do Quality (evaluation-api)

**Fase 1 ✅ (config humana, 2026-06-25):** mutações de forms/campaigns/rubric gateadas por
`_require_evaluation_field` (grant-first, deny em config vazio; forms/campaigns→`formularios`,
rubric→`gerir_rubrica`, read_write). Route guard `RequireEvalAccess` em todas as rotas de evaluation
(espelha o nav strict, sem bypass). Bearer JWT (de `session.accessToken`) nas mutações + hooks de lista
no platform-ui. Detalhe em `CHANGELOG.md`.

**Listas abertas (decisão fase 1):** `list_forms/campaigns/rubric` ficaram **sem gate** — são read
compartilhado (Avaliações/Calibração/Curadoria/Reports mapeiam id→nome com `report`/`revisar`/`curar`,
não `formularios`; gateá-las quebraria essas telas). GET-by-id/resolve/effective também abertos
(runtime: session-replayer lê `forms/{id}`, mcp-server lê `rubric-templates/effective`).

**Fase 2 — slice backend ✅ (2026-06-26); wiring + UI PENDENTES.** Decisões da sessão: gate de serviço
**strict** (sem fallback admin-token); UI usa **Bearer+ABAC** (sem segredo no frontend); slice backend-first.

- ✅ **`_require_service`** (strict `X-Service-Token`, `config.service_token` env
  `PLUGHUB_EVALUATION_SERVICE_TOKEN`, vazio = no-op/demo) em: `ingest`, `claim_instance`,
  `expire/skip/mark-error`, `dispatch_scan`, `submit_pre_review`, `submit_ai_review`,
  `publish_calibration_note`.
- ✅ **`_require_service_or_eval_write`** (serviço OU Bearer+ABAC `formularios:rw`) nas ações de ops
  disparáveis pela UI: `dispatch_campaign`, `backfill`, `seed/flush-synthetic`, `sampling-rules` CUD.
- ✅ **`_require_any_evaluation`** (any-of, degradação graciosa) nas LEITURAS de lista: forms, campaigns,
  rubric-templates, instances, contestations, calibration-notes, sampling-rules.
- ✅ Testes `tests/test_gprobe_phase2.py` (funções puras). Ver CHANGELOG.

**Slice caller-wiring ✅ (2026-06-26):**
- ✅ **Provisionado** `PLUGHUB_EVALUATION_SERVICE_TOKEN` no `docker-compose.demo.yml` (evaluation-api +
  mcp-server-plughub; valor demo `changeme_eval_service_token_demo`). Gates de serviço agora ENFORCED no demo.
- ✅ **mcp-server** `evaluation_pre_review_submit` envia `X-Service-Token` (env; `EVALUATION_API_URL` também
  provisionado p/ o container). Único caller HTTP backend de endpoint service-gated (o avaliador real publica
  por Kafka, não por HTTP `/ingest`; os scanners chamam a função direto, não o endpoint).
- ✅ **UI bridge**: `seed/flush/dispatch` da `CampaignsPage` passam o Bearer do operador (`session.accessToken`)
  → `_require_service_or_eval_write` aceita via ABAC. Input de admin-token vira vestigial (remoção = cleanup UI).
- ✅ **Smoke** `infra/test/smoke_gprobe_service_auth.sh` valida os 3 gates (service strict / dual / any-of).

**Follow-ups restantes:**
- ⏳ **Repair dos ~15 e2e legados de eval** (`test_t7a/t9*/t10*/t12/t13/t14/t15/t17/r1/r6/t7b2`): **já vermelhos
  pela Fase 1** (criam form/campanha SEM Bearer; `create_form/create_campaign` exigem `formularios:rw`) —
  precisam de (a) Bearer mintado p/ o setup E (b) `X-Service-Token` nos calls G-PROBE-gated (ingest/dispatch/
  scan/backfill/ai-review/skip/mark-error/sampling-rules). Dívida pré-existente da Fase 1; smoke dedicado cobre
  o G-PROBE no intervalo.
- ✅ **Cleanup UI** (2026-06-26): input de admin-token removido da `CampaignsPage` (state/input/props +
  i18n `campaigns.sidebar.adminTokenPlaceholder` en/pt); `saveCurationSamplingRules`/`useCurationSamplingRules`
  passam o Bearer do operador. Bearer explícito nos consumidores de lista que faltavam (`useInstances`,
  `useContestations`, `useCurationSamplingRules`); forms/campaigns/rubric/results/curations já tinham. Ver CHANGELOG.

**Pendente — admin-token boxes platform-wide → Bearer+ABAC (FORA do escopo G-PROBE, não bloqueia):**
G-PROBE cobriu só o módulo Quality (evaluation-api). O MESMO anti-padrão (caixa de texto de admin-token na UI,
em vez de autorizar pelo JWT do operador + ABAC) persiste em outras telas, cada uma gateando um serviço
diferente pelo seu admin-token. Migrar cada uma é um "mini-G-PROBE" por serviço (gatear endpoints em
Bearer+ABAC + remover a caixa). Inventário:
- ✅ **`config/access` (`AccessPage`) + `config/groups` (`GroupsPage`) → auth-api** (`config.usuarios`) — slice
  CONCLUÍDO (2026-06-26): gate strict Bearer+ABAC na auth-api (router + groups_router), seed_auth minta Bearer
  de bootstrap, UI usa session Bearer (listas carregam no login — conserta o bug reportado). Smoke
  `smoke_config_usuarios_auth.sh`. Ver CHANGELOG. *(Follow-up: `auth-api/tests/test_router.py` em X-Admin-Token
  → refresh; envs `*_AUTH_ADMIN_TOKEN` vestigiais → cleanup.)*
- ✅ `config/platform` (`ConfigPlataformaPage`) + `config/masking` (`MaskingPage`) → **config-api** — slice
  CONCLUÍDO (2026-06-26): gate DUAL (admin-token OU Bearer+ABAC mapeado por namespace; default→`plataforma`,
  masking/audit_policy→`masking`); `putConfig/deleteConfig` com Bearer opcional; caixas removidas das 2 telas.
  Smoke `smoke_config_write_auth.sh`. Demais telas de config (Channels/Billing/Dashboards) seguem em admin-token
  (dual cobre) até suas fatias. Ver CHANGELOG.
- ✅ `config/resources → Skills` (`SkillsPage`, `competencySkills`) → **config-api** (NÃO era agent-registry —
  escreve namespace `competency_skills` via `putConfig`, mapeia ao default `config.plataforma`). Slice UI-only
  CONCLUÍDA (2026-06-26): caixa removida, escritas via Bearer; backend já coberto pelo gate dual da config-api.
- ✅ **agent-registry — gate dual nas mutações de config** (2026-06-26): middleware `requireResourceWrite`
  (Express, verificação HS256 em stdlib `crypto`) nos routers **pools/skills/channels/channel-endpoints** —
  GET aberto; mutação exige **X-Service-Token** (callers internos) OU **Bearer+ABAC `config.resources`** (UI).
  Callers internos wirados: RegistrySyncer (`registry_syncer.py`) + `skill_deploy` (`deploy.ts`) mandam
  `x-service-token`. UI: `registry.ts` manda Bearer via novo `auth/token-store.ts` (holder de módulo espelhado
  pelo AuthContext) → caixa da `SkillsPage` removida. Provisionado `PLUGHUB_JWT_SECRET` +
  `AGENT_REGISTRY_SERVICE_TOKEN` (agent-registry + orchestrator-bridge + mcp-server). Smoke
  `smoke_agent_registry_write_auth.sh`. Ver CHANGELOG.
  - **Residual (fora desta fatia, FORA do gate de propósito):** `pool-slots` (promote/rollback do Fluxo→Deploy,
    cadeia via mcp-server), `instances`/`operational` (runtime: bootstrap/heartbeat). Gatear esses = fatia
    própria (wirar a cadeia de deploy + bootstrap). Ferramentas CLI de import (`sdk/cli/import.ts`,
    `gitagent/import.ts`) mutam `/v1/skills` sem token — dev/CI, não-runtime; passar `x-service-token` se forem
    usadas contra registry gateado.
- ✅ `config/channels` (`WebChatConfigPage` + `WebhookConfigPage`) → **config-api** `config.canais` — slice
  CONCLUÍDO (2026-06-26): backend já dual; add `webhook`→`canais` no mapa; caixas removidas, escritas via Bearer.
  Smoke estendido (§4). Ver CHANGELOG.
- ✅ `config/billing` (`BillingPage`) → **pricing-api** (NÃO era config-api — usa `/v1/pricing/*`) — slice
  CONCLUÍDO (2026-06-26): gate DUAL na pricing-api (admin-token OU Bearer+ABAC **`config.plataforma`** — decisão:
  reusa config.plataforma, sem campo billing novo; o módulo `billing` só tem `visualizar`/read). `jwt_secret` +
  `PLUGHUB_PRICING_JWT_SECRET`. Caixa removida; reserve activate/deactivate via Bearer. Smoke
  `smoke_pricing_write_auth.sh`. Ver CHANGELOG.
- ✅ `config/dashboards` (`DashboardsPage`) → **config-api** namespace `dashboards` (→ default `config.plataforma`)
  — slice UI-only CONCLUÍDA (2026-06-26): `dashboard-hooks` (configGet/Put/Delete/List) mandam Bearer via
  token-store; caixa de admin-token (+ localStorage `plughub_admin_token`) removida. Backend já coberto pelo gate
  dual da config-api. Ver CHANGELOG.
- ✅ `evaluation/knowledge` (`KnowledgePage`) — **fatia de wiring CONCLUÍDA (2026-06-26)**. Recon confirmou que a
  página estava **morta**: `/v1/knowledge/*` não existia em lugar nenhum (proxy ia p/ eval-api:3400 sem rotas;
  mcp-server-knowledge só tinha `/admin/*` + MCP tools). Construído o **surface REST** na mcp-server-knowledge
  (`routes/knowledge.ts`: GET `/v1/knowledge/search`, POST/DELETE `/v1/knowledge/snippets`, reusando `db.ts`),
  gate DUAL (`require-knowledge-access.ts`: X-Service-Token OU Bearer+ABAC `evaluation.gerir_rubrica`, read p/
  search / read_write p/ snippets). Proxy Vite `^/v1/knowledge` → **3401**. Publish de CalibrationNote da
  evaluation-api passa `X-Service-Token` (conserta o KB vetorial do Arc 13, que silenciava em 404). UI usa Bearer
  (token-store) e perde a caixa. Smoke `smoke_knowledge_rest_auth.sh`. Ver CHANGELOG.
- ✅ `Avaliações` filters (`AvaliacoesPage`) — caixa de admin-token removida (2026-06-26); a adjudicação Arc6
  **legada** usa o Bearer do operador (`adjudicateContestation` → `bearerHeaders`). *Resíduo:* a **retirada
  física** do endpoint/UI `adjudicate` segue junto da limpeza do motor Arc6 legado (não bloqueia).
Decisão (2026-06-26): sequenciável por serviço; auth-api foi a 1ª fatia (strict, decisão da sessão). Inventário
completo das telas com caixa de admin-token: access, groups (✅ auth-api), platform, masking (config-api),
resources/skills (agent-registry), knowledge (mcp-server-knowledge), avaliações/adjudicate (evaluation-api legado).

**Rot pré-existente (separado do G-PROBE, não bloqueia):** `evaluation-api/tests/test_router.py` tem
11 testes quebrados **independentes do gate** (classes TestInstances/Ingest/Results/Contestations):
mocks não cobrem `set_contestation_state`/`get_campaign`/`lock_result` (chamadas novas Arc 13),
`app.state.redis` ausente no app de teste, payload de review desatualizado (422), `expire_instance`
sem `x-admin-token` (container tem `admin_token` setado). Atualizar os mocks ao contrato evoluído.

---

## Webhook pools — throttle de downstream: enforcement no routing *(deferred)*

Re-validação 2026-06-04 (ver `CHANGELOG.md`): o default 500 **já não existia** no código
(schema `.optional()`, registry grava null); a premissa "nada é pré-instanciado" ficou
stale pós Arc 19 Fase C — capacidade real de webhook = slots de instância do deploy
(Bootstrap) + admissão híbrida. O `max_concurrent_sessions` pool-level era display-only
no Monitor (capacidade fictícia) — coerência aplicada: removido do YAML demo, comments
schema/registry revisados ("throttle opcional de downstream").

**Deferred**: enforcement real do throttle no routing quando configurado
(`active_count ≥ max` → enfileira; backpressure p/ downstream frágil, ex. ERP).
Implementar quando houver caso de uso real.

---

## Delegate v2 — itens restantes (pós-correção do ciclo de portabilidade)

Modelo corrigido e backend verde em [`docs/arcos/delegate-workflow-io.md`](docs/arcos/delegate-workflow-io.md)
(delegate sempre roda o alvo como segmento conference do chamador; A-new fecha como webchat;
`context_set` registrado; specialist de B adia instantâneo). Restam:

- **Fase C — heurística de canal na UI ✅** (já implementada — TODO estava
  desatualizado): `ListaTab.tsx` classifica pelo `channel_type` real (canal decide
  WorkflowTraceList vs SegmentList) e o badge "suspended" é restrito a `channel ===
  'webhook'` (webchat em delegate-wait lê live). Nota residual no código: contador
  de participantes vivos exigiria suporte de backend — channel é o proxy aceito.
- **Fase D — timeout scanner do delegate ✅** (já implementado — TODO estava
  desatualizado; ver `delegate-workflow-io.md` § Fase D): `run_timeout_scanner` em
  `channel-gateway/adapters/webhook.py` (lifespan, 60s) expira `resume_tokens`
  vencidos via `handle_resume(decision="timeout")` → `on_timeout` do step; cobre
  suspend e delegate; `pending_workflow` stale auto-limpa no próximo reconnect.
- **Fase E — Workflow Execution Trace (step-level)** ✅ (E.1/E.2/E.3 + transcript):
  step timeline já renderiza; `step_io` com `decision`/`payload`/`child_session_id` por step
  (E.1); `resumed_by` por step (E.3); duration webhook = tempo decorrido total (E.2);
  transcript do specialist via clique no nó de agente (já existia). Design em
  `docs/arcos/delegate-workflow-io.md` § Fase E.
  - **E.4 diferido (sem dado no demo)**: (a) **MCP audit** por step — `skill-flow-service`
    chama o mcp-server via cliente cru, não pelo `McpInterceptor`, então os `invoke` não
    geram `mcp.audit`; construir quando a execução passar pelo interceptor. (b)
    **agent_business_events** (Arc 12, via tool `agent_event`) — agentes de portabilidade
    não emitem. *(Não confundir com a tabela `agent_events`, descontinuada em 2026-07-28 —
    eram nomes quase idênticos para eixos diferentes.)* (c) snapshot de
    ContextStore com evolução entre suspends (hoje só o estado atual no strip Input context).
    (d) duration "corridas vs úteis" (business_hours) lado a lado.

## Pricing → quota Redis não existe: o gate de admissão não arma *(achado 2026-06-04)*

As chaves `{t}:quota:*` lidas pelo `assertQuota` estão documentadas em `docs/arcos/pricing.md` e no
CLAUDE.md, mas o **pricing-api não tem código Redis** (`keys 'tenant_demo:quota:*'` volta vazio depois
de um POST de resources). Consequência: o teto contratado é só **analítico** (denominador do
occupancy) — o gate de admissão **nunca arma**. No demo isso obriga `INCRBY` manual na quota
`max_concurrent_sessions`.

Correção: escrever as chaves no upsert de resources (ou na ativação de plano) e corrigir
`docs/arcos/pricing.md`, que hoje descreve um comportamento que não existe.

---

## Relatórios analíticos — Agentes e Pools *(só o que resta aberto; histórico no CHANGELOG)*

Arco de relatórios (agentes + pools/infra) e Bancada de comparação 360° por `agent_key`. Specs:
[`analytics-reports-redesign.md`](docs/arcos/analytics-reports-redesign.md) · [`pools-infra-report.md`](docs/arcos/pools-infra-report.md) ·
[`analytics-agents-workbench.md`](docs/arcos/analytics-agents-workbench.md) · [`config-consolidation.md`](docs/arcos/config-consolidation.md) ·
[`config-http-propagation.md`](docs/arcos/config-http-propagation.md).

### Dívidas e limitações declaradas

- **`sessions.sla_target_ms` histórico**: sessões antigas permanecem NULL (valor nunca persistido,
  irrecuperável); a aba SLA só popula com contatos novos.
- **`AgentTimeline` — precisão por pool é aproximada**: atribui o intervalo inteiro a cada pool
  tocado; sub-intervalos exatos por pool = refinamento futuro.
- **`farewell_text` só renderiza no webchat**: voice/whatsapp não renderizam (voice = TTS futuro).
- **Quality ainda em fixture (F8 ⏸ adiado)**: `evaluation_dimension_scores` vem de seed de
  `evaluation_results`; `agente_avaliacao_v1` não roda no demo (test-grade, sem associação
  form/campanha). Pendências test-grade da F2: ReplayContext sem `session_meta` e sem associação
  campanha/form. Consertar o pipeline de avaliação = arco próprio.
- **`pool:pending_assignment:{poolId}` é UMA chave por pool** (last-write wins) → chave
  por-instância é melhoria futura (liga à fila pull/inbox).
- **NPS render (cosmético, diferido)**: a mensagem de `menu`/`notify` aparece no transcript como
  "structured content" em vez de texto puro (o dado do NPS grava normalmente) — revisar emit + render.
- **Cenários sem teste** (queue-attended-model): "fila muda" e "drop sem `pool_id`".
- **(verificar)** "Fase 1 — relatório de agentes" nunca foi marcada ✅ (parece absorvida por
  C1/C1b-A/C1b-B + Bancada); idem "Fase 3 · 3d-**parcial**" do provisionamento — conferir o que ficou fora.

### Trabalho futuro planejado

- **F11 — pesquisa multi-grão / surveys diferidas** (arco de evaluation, separado do G7): falta o
  **planejamento da orquestração** — quando/como cada grão (`journey | session | segment`, até 3 por
  fluxo) dispara, e surveys diferidas (`captured_at ≠ session_at`). Base parcial na F10.2b
  (`survey_collector_ia` / `survey_reconnect_ia`). Ver workbench §13/§14 e
  `g7-segment-contact-decoupling.md` §5.
  - **F11.2 (validação)** diferida: simular via curl/seed (publicar `session.signals`/`survey_record`
    com origem de `opened_at` anterior + grão `journey` e conferir `session_at = opened_at`);
    workflow agendado real (dias depois) fica futuro.
- **Catálogo canônico de dimensões de qualidade** (arco próprio): única base rigorosa p/ comparar
  dimensões entre forms. Hoje cross-agente exige mesmo form e cross-form só vale p/ um agente
  (`_compare_quality_lens` expõe `summary.form_ids`; a UI faz o guard).
- **Avaliador dirigido por calendário/campanha** (arco próprio, decisão 2026-06-07): disparar pelo
  `schedule` (JSONB de `evaluation.campaigns`) passando o `session_id`, substituindo o gatilho
  incondicional do Persister.
- **Residuais opcionais do relatório de Pools/Infra** (spec § Pendente): sub-aba Visão geral,
  heatmap hora×dia, SETs de `session_id`, overlay de capacidade licenciada v2.

### Config Consolidation / HTTP Propagation — o que falta

- [ ] **F2** migração por domínio: faltam **hooks**, **evaluation/pricing** e **defaults hardcoded**
      (pools, TTLs, masking e ABAC/users ✅).
  - [ ] **Item 6** — seeds `seed_evaluation`/`seed_pricing` → bootstrap idempotente via API.
        **Estacionado (2026-06-12)**: atacar junto da revisão dos módulos evaluation/pricing.
- [ ] **F3** bootstrap idempotente único (substitui `infra/seed/*.py` + YAML-fonte, só via APIs).
      Arquitetural, sem bug vivo, baixa urgência (`config-consolidation.md` §9).
- [ ] **F4** política de env vars (segurança) — inventário final.
- *Cleanup opcional*: remover o caminho dormente `evaluation_sampler`/`on_pool_config` do
  rules-engine (`on_pool_config` nunca é chamado) — ou religá-lo se a campanha não cobrir.
- *Dead code a varrer*: `_sync_agent_type`/`_prune_agent_types` (`registry_syncer.py`, sem chamador);
  Path A `elif framework == "human"` (main.py, inalcançável); `AgentTypeSchema` (@plughub/schemas) +
  `validators/agent-type.ts` órfão. Testes do agent-registry com agent_type foram deletados — revisar
  a suíte se reativar CI.

---

## G7 — Decoupling segment-end × contact-close *(fases entregues; restam follow-ups + 2 arcos próprios)*

Spec em [`g7-segment-contact-decoupling.md`](docs/arcos/g7-segment-contact-decoupling.md) (§10/§11) +
`conference-mechanics.md`. Fases 0/3, Slices A/B, sub-arco multi-humano (Slices 1/2′/3/4′), arco do
router (alocação atômica) e Camada 3 estão entregues e validados E2E — histórico no CHANGELOG. Resta:

### Follow-ups do modelo de hooks *(baixa prioridade)*

- **Gap (2) — survey customer-side por-segmento não chega aos peers**: `segment_wrapup` reusa a lista
  de `on_human_end` mas filtra `side=agent` (`main.py` ~938) → surveys customer-side (grão=segment,
  NPS) só saem na âncora/primário.
- **Gap (4) — binding grão↔boundary é convenção, não contrato** (skill em "contact ends" gravar
  `grain=session`); disparo com **grão=journey** não está plumbado (não há boundary de fim-de-journey) → F11.
- **Higiene opcional**: convergir `on_human_end` (último) + `segment_wrapup` (peers) num mecanismo
  único de wrap-up por-segmento.
- **Polish (Slice 3)**: atribuição-por-nome do remetente no fan-out humano↔humano.
- **UX cosmético**: sinalizar no Console "convidando, aguardando login do agente" quando o `@mention`
  vai p/ pool sem instância `ready` (não é bug — fila + drain no `agent_ready`, conclusão 2026-06-15).

### Router — alocação atômica *(arco concluído; só residuais opcionais)*

- `get_ready_instances`/snapshots poderiam ler `SCARD` direto (hoje leem o JSON sincronizado pelo
  claim/release — funciona como hint; o claim é o gate atômico). Baixa prioridade.
- Cenário "2 contatos simultâneos no mesmo pool → spread" não exercitado isoladamente.
- Hardening da chave de menu por `segmentId` julgado **desnecessário** após a alocação atômica +
  Camada 3 Fatia A — reabrir só se houver regressão.

### Unificação de contabilidade de agente (kind-agnostic) *(arco próprio — DIFERIDO)*

Anchor "último agente customer-facing" é aproximado por 4 chaves de papéis distintos: `human_agent`
(flag, ~10 sites, hot path de entrega) · `human_agents` (SET, ~10: remaining/restore/participant_left/
fan-out) · `ai_agents` (SET, ~8: restore no close) · `active_ai_specialists` (SET, ~7: defer G2).
Alvo: HASH único `session:{id}:agents → {kind, role, customer_facing, running}`.
- **Decisão (2026-06-13, reafirmada 2026-06-15)**: fazer **oportunisticamente** — só quando um bug
  concreto justificar ou encostado em feature que já toque essas chaves. Refactor puro-interno,
  gateável só por paridade, raio cross-package (mcp-server supervisor/bpm/evaluation), no path mais
  frágil (close).
- Único incremento baixo-risco se encostar no path de entrega: derivar `human_agent` de
  `SCARD(human_agents) > 0` — atenção à aresta (flag setada mesmo com `instance_id` vazio em
  `activate_human_agent`; não é 1:1).

### Detecção de queda involuntária de humano *(Slices 1/2 ✅ — verificar se o alvo está coberto)*

- **(verificar)** Slices 1 (ws.close + grace → `contact_closed(agent_disconnect)`; re-rota ao
  `_ha_pool` quando `remaining<=0`) e 2 (pong-tracking `ws.ping` + `terminate` em 30s) estão ✅ e o
  texto declara "arco heartbeat completo", mas o fechamento do sub-arco multi-humano ainda listava
  este arco como restante — conferir o alvo "posse re-estabelecida por alocação" no caso `remaining>0`.

---

## Frente 3 — Revisão de config / eliminar seeds *(em curso)*

Meta: produção sem seeds re-aplicados — DB é fonte de verdade; setup inicial de DB versionado.
- **Fase 1 ✅ (2026-06-15)** — **seed-if-absent / DB-owned** no `RegistrySyncer` (`registry_syncer.py`): no 409,
  não sobrescreve pool config nem deploy-slot (capacidade); edições de UI sobrevivem a rebuild. Env
  `REGISTRY_SYNC_RECONCILE=true` = reconcile legado (YAML vence) p/ dev. Skills seguem upsert (código). Curou o
  sintoma "Transfer/`escalation_pools` some a cada build". Ver CHANGELOG 2026-06-15 + CLAUDE.md § Configuration.
- **Fase 2 — correção ✅ / arquitetura DIFERIDA (auditoria 2026-06-15)**: a auditoria por store mostrou que
  **todos já são seed-if-absent** (pools via Fase 1; config-api `overwrite=False`; pricing/evaluation checam
  existência; users 409; catálogo ABAC e skills re-aplicam de propósito = código). Ou seja, **não há bug
  pendente** — a "config some no rebuild" está resolvida. O que sobra é só o **sonho arquitetural** (converter
  seeds/YAML em **migração versionada if-absent**, modelo `initdb/01_platform_config.sql`, aposentando
  `infra/seed/*.py` + YAML de registry, store por store) — **baixa urgência**, burn-down gradual sem retrabalho.
  Resíduo opcional: `set_module_config` do `seed_auth` if-absent (demo-users). Ver `docs/arcos/config-
  consolidation.md` §9.
- **Doc** ✅ — `docs/arcos/config-consolidation.md` existe; atualizado com a auditoria + precedência seed-if-
  absent (§9). Referências de `CLAUDE.md`/`registry_syncer.py` resolvem.

---

## Hardening de Auth — postura de sessão do Console *(proposta — não é bug)*

Hoje (Arc 7, por design): `access_token` em memória; `refresh_token` em `localStorage('plughub_refresh_token')`
→ **silent re-auth** no mount (`POST /auth/refresh`). Reabrir a URL após fechar a aba entra logado sem
credencial — esperado, mas é um trade-off UX×segurança. Levers de endurecimento (cada um é arco próprio,
escolher conforme exigência de segurança para um console que vê PII):
- **refresh_token em cookie httpOnly** (em vez de `localStorage`) → mitiga exfiltração por XSS. Maior
  mudança (auth-api seta cookie; CORS/SameSite; CSRF token).
- **Idle/inactivity timeout** — não existe hoje; sessão dura enquanto o refresh_token for válido. Adicionar
  expiração por inatividade no Console + invalidação no auth-api.
- **TTL do refresh_token** — encurtar no auth-api (hoje rotaciona indefinidamente enquanto usado).
- **"Fechar aba = deslogar"** — trocar `localStorage` por `sessionStorage` (morre com a aba); custo de
  conforto (reloga a cada nova aba).
Decisão de produto/segurança pendente: qual combinação aplicar. Sem isso, manter o comportamento atual.

---

## Customer Surveys — estado as-built das fases S1–S11 *(levantamento 2026-07-23)*

> Cruzamento do plano §12 de [`docs/arcos/customer-surveys.md`](docs/arcos/customer-surveys.md) contra o
> **código real** (o F11 abaixo dizia "nenhuma fase iniciada" em 2026-07-02 — **desatualizado**). Tabela
> as-built + evidências + próximos passos completos em **`customer-surveys.md` §12.1**. Achado central:
> várias fases estão **feitas-por-substituição** (dialog-api, `contact_eligibility_check`, `session_signal`
> genéricos cobrem o que o spec pedia como entidades dedicadas de survey).

**Feito / feito-por-substituição (não é trabalho pendente):** S2 (runner genérico + DialogForm), S3 (gatilho
lê outcome), S4 (quarentena → `contact_eligibility_check` genérico), S5 (web + link → `session.signals`).

**Pendente — eixo "fechar parciais primeiro" (decidido 2026-07-23):**

1. **S1 — ✅ FEITO (2026-07-27, ver CHANGELOG).** Catálogo único `survey_catalog.py` + roll-up por instrumento.
   **Resíduos:**
   - **Nenhum produtor emite CES/PMF/FCR** — nenhum DialogForm (`infra/test/seed_dialog_*`) nem skill de survey
     os captura. A normalização está pronta e sem dado: falta um form de seed com dimensions CES/PMF/FCR para
     um E2E de verdade (e para o S6/S8 mostrarem algo além de NPS/CSAT).
   - **UI ignora `value_label`** — `SignalChips` (`AnaliseSurveysPage.tsx`) renderiza só `metric` + número, até
     para NPS ("nps 9" em vez de "Promotor"); `CustomerVoicePage` tem um ternário vazio (`rollup === 'avg' ? ''
     : ''`) onde deveria sufixar `%` para `pct`/`nps_index`. Fatia C do S1.
   - **Rótulos mistos** — CES/PMF/FCR em inglês (spec), NPS/CSAT em pt-BR (histórico gravado). Unificar exige
     decidir migração do histórico + i18n na UI.
2. **S7 (refinos do editor `/config/dialog-forms`):** biblioteca `survey_question` reutilizável, ABAC no
   write (hoje só `X-Admin-Token`), drag reorder, locale lado-a-lado + preview.
3. **S6 (fechar):** view consolidada "Visão do cliente" (cross-cut multi-métrica + divergências §8/§10)
   sobre a base que a lente `customer_voice` já expõe (Customer Voice Fatia 1 = só grão×instrumento + SLA).
4. **Higiene S2:** deployar o trio renomeado (`skill_survey_runner_v1`/`outbound`/`trigger`) como pools no
   `infra/registry/tenant_demo.yaml` — o registry ainda roda o conjunto antigo.
5. **Store per-response** (gargalo que travava S8/S9) — ✅ **FEITO E VALIDADO (2026-07-23, ver CHANGELOG).**
   Schema PG `survey` + endpoint idempotente (evaluation-api); `survey_record` persist-first (mcp-server);
   `survey_web.submit` **captura verbatim** + persist-first (channel-gateway). Smoke
   `smoke_survey_response_store.sh` verde. **Desbloqueia S8.** ADR aceito:
   [`docs/adr/adr-survey-response-store.md`](docs/adr/adr-survey-response-store.md). **Opção A** decidida:
   schema PG `survey` dedicado, escopo mínimo `survey_instance`+`survey_response` (com `open_text`/`audio_ref`),
   host = **evaluation-api**; poda o §7.2 (definições→dialog-api, quarentena→mailing-api). Caminho de escrita:
   `survey_record` persiste antes de emitir + `survey_web.submit` para de descartar verbatim. **Contrato de
   implementação PRONTO** (DDL das 2 tabelas, endpoint `POST /v1/evaluation/survey/responses`, idempotência,
   ordem persist-first, wiring com linha/símbolo exatos, checklist de build):
   [`docs/product/survey-response-store-implementation-spec.md`](docs/product/survey-response-store-implementation-spec.md).
   **Falta só codar.** Abertos: endpoint de leitura de S8, áudio/transcript (S9).
6. **Valor novo (loop captura→leitura→ação):** **S8** (navegador de respostas `/analise/surveys` + verbatim)
   ✅ **FEITO (2026-07-23, ver CHANGELOG).** Restante: **S9** (`agente_survey_analyst_v1` — classifica verbatim +
   áudio/transcript via `attachment_store`) → **S10** (retorno outbound + caixa de ações) → **S11** (NPS/PMF
   relacional agendado). Refino de S8: endpoint de LEITURA já existe; falta só export CSV (opcional) e o
   guard de rota ABAC (Item 3 app-wide).

---

## Arco de Segurança — Pool-scoping em relatórios (ABAC no DADO) *(achado 2026-07-23; Fase A preparada)*

**Problema (levantado pelo usuário, confirmado em código).** O modelo pretende que relatórios/monitores
respeitem o **domínio de pools** do usuário (Arc 7c: `accessible_pools` = filtro de linha; ABAC + grupos).
Hoje isso está **inerte** em toda a superfície de Analytics.

- **Causa raiz (app-wide):** a **platform-ui não envia `Authorization: Bearer`** nas chamadas de `/reports/*`
  e `/v1/evaluation/*` — as páginas de `/analise` usam `fetch(url)` cru; o proxy do Vite é pass-through
  (`vite.config.ts` `^/reports` e `^/v1/evaluation`, só `changeOrigin`). Sem token, o `optional_pool_principal`
  (analytics-api `pool_auth.py`) e o `_decode_jwt_optional` (evaluation-api) resolvem `accessible_pools=None`
  = **irrestrito** ("unauthenticated → all pools", documentado). Ou seja, o filtro por pool é **no-op**: qualquer
  usuário vê **todos os pools**. Vale para journeys, sessions, survey, etc. Postura de demo — mas fura o modelo.
- **Fix camada de dado:** a UI passa a anexar o `bearer()` (existe em `api/registry.ts`, lê o token em memória)
  nas chamadas de relatório — ou um gateway injeta o header. Necessário para QUALQUER scoping de Analytics
  funcionar. Distinto do **Item 3 (guard de rota ABAC)** da seção Journey: aquele protege o *chrome* da página;
  este protege o *dado*. Os dois juntos = enforcement real (rota + linha).

**Gaps ESPECÍFICOS do survey (S8) — só mordem quando o token for enviado:**
1. **`survey_instance.pool_id` não é populado na escrita.** Veículo web (`survey_web.submit`, channel-gateway):
   `pool_id` sai **sempre vazio** (o token congelado não carrega o pool da sessão pesquisada). `survey_record`
   (mcp-server): `pool_id` é input **opcional** → vazio quando omitido. **Decisão de produto**: a resposta deve
   ser atribuída ao **pool da sessão/segmento PESQUISADO** (resolver na escrita — web: do `origin_session_id`
   no `survey_link_create`/persist; record: exigir/derivar). Sem isso o scoping não tem em que se ancorar.
2. **Sem escape hatch de pool vazio** em `db.list_survey_responses` (`i.pool_id IN (...)`), ao contrário da
   analytics-api que usa `(s.pool_id IN (...) OR s.pool_id = '')` de propósito. Com o token ativo + pool vazio,
   um supervisor restrito veria **zero** respostas web (inverte "vê tudo"→"vê nada"). Decidir a política de
   pool vazio junto com o fix (1).
- **LGPD reforça a prioridade:** o verbatim é texto aberto do cliente (dado controlado); ler verbatim de pools
  fora do escopo é vazamento cross-pool, não só cosmético.
- **Referência do padrão correto:** evaluation-api `list_results` + `_compute_result_scope` (row-scope por
  role+grupo+pool, trata self-ownership) — mas **também** depende do token que a UI não manda.

**Fases:**

| Fase | Entrega | Depende |
|---|---|---|
| **A — propagar o token na UI** | ✅ **Completa (2026-07-23):** helper `apiFetch` + **8 arquivos de `analise/`** + **varredura dos demais consumidores** (18 call sites `/reports` em 15 arquivos: `contacts/*`, `contacts/tabs/*` [Monitor/Analise/Agents/AgentTimeline/Lista], `agent-reports/`, `agent-flow/*`, `service/SessionTranscript`, `billing/`, `campaigns/`, `analise/CustomerVoicePage` instruments). Único `fetch` cru remanescente a `/reports` = `api/evaluation-hooks.ts:515` (POST flush-synthetic, já anexa `bearerHeaders`). | — |
| **B — `pool_id` na escrita do survey** | 🟢 **Feito p/ web + NPS inline + J4c collect + multi (2026-07-23):** veículo web plumba `pool_id` (`survey_link_create`→token→`submit`); outbound 5b carimba `origin_pool` na metadata→dispatcher→worker; `agente_nps_v1`/`skill_survey_multi_v1` usam `@ctx.session.pool.id` (origem = self); **J4c** — `handle_collect` resolve o pool do alvo e semeia `session.survey_pool_id` no engage, `skill_survey_runner_v1` o carimba. Smokes: `smoke_outbound_fase5b.sh` + pytest `test_collect_pool_scoping.py`. **Resta 1 seam:** `skill_survey_v1` (survey_processo_ia, F10.2b delegate) grava de `@ctx.session.origin_session_id` sem passar pelo collect → semear o pool no `handle_trigger` (do `origin_session_id`). Até lá pool vazio = admin-only (decisão C). | — |
| **C — política de pool vazio** | ✅ **DECIDIDA strict (2026-07-23): pool vazio = só irrestrito/admin vê.** Sem escape hatch — respeita o domínio (resposta sem pool não pertence a nenhum domínio; over-expor a todos seria mais inseguro que sub-expor). É o comportamento ATUAL da query (`pool_id IN (domain)` já exclui vazio p/ restrito), **sem código**. O "restrito vê zero survey web" é sintoma de B (pool vazio na escrita), não de C. | — |
| **D — endpoints operacionais + `/reports/*` sem scoping** | ✅ **COMPLETA (2026-07-23):** `/v1/operational/pools` (agent-registry) + Monitor SSE `/dashboard/{operational,sentiment,pool-sla}` (token por query param) + auditoria `/reports/*`: `contact-insights` ESCOPADO (subquery a segments); demais não-escopados por decisão fundamentada (`usage`/`campaigns` não pool-atribuídos; `workflows` metadado de processo; `evaluations*` gateados por ABAC evaluation; `quality` unscoped por construção; `instruments` catálogo). Follow-up de posture: JWT em URL do SSE → cookie/ticket em prod. Ver CHANGELOG "Fase D COMPLETA". | A |
| **E — filtro de pool = combo do DOMÍNIO (não texto)** | ✅ **Completa (2026-07-23):** survey usa `PoolMultiSelect` (multi, `pool_ids[]` + reinterseção no backend); **agentes/contatos** usam o novo `PoolDomainSelect` (single) — `AnaliseAgentesPage`/`AnaliseContatosPage` trocaram o texto livre por combo do domínio (`listPools ∩ accessiblePools`). Single (não multi) por decisão: `ContactFilters.poolId` é singular e compartilhado (blast radius) e a segurança já é backend (`optional_pool_principal`). i18n `agentReports.filters.allPools`. | A |

Enforcement completo = **rota** (Item 3 do Journey — guard ABAC de `/analise/*`) + **dado** (este arco).
Ver `docs/arcos/arc7-auth.md` (ABAC/accessible_pools) e `docs/arcos/customer-surveys.md` §7.3.

### Fase A — preparada (turnkey)

**Decisão:** helper explícito `apiFetch` (consistente com o `bearer()` já existente em `api/registry.ts`), NÃO
monkey-patch do `window.fetch`. Motivo: a base já faz merge explícito de header (`bearer()`), sem interceptor
global; um patch global tem efeito colateral em chamadas que não devem levar token (auth/refresh, CDNs). O
custo do explícito (migrar call sites) é aceitável e a segurança do backend **já enforça** quando o token chega
(o gate é permissivo só na ausência) — logo A é **puramente frontend**.

1. **Novo helper** `packages/platform-ui/src/api/apiFetch.ts`:
   ```ts
   import { getAccessToken } from '@/auth/token-store'
   /** fetch que anexa Authorization: Bearer do token em memória (se houver e não já setado).
    *  Usar em TODA chamada de relatório (/reports, /v1/evaluation, /analytics). */
   export function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
     const t = getAccessToken()
     const headers = new Headers(init.headers)
     if (t && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${t}`)
     return fetch(input, { ...init, headers })
   }
   ```
2. **Migrar os call sites** de `fetch(` → `apiFetch(` nas chamadas de `/reports/*` e `/v1/evaluation/*`.
   Superfície confirmada (8) em `src/modules/analise/`: `AnaliseSurveysPage`, `AnaliseJourneysPage`,
   `CustomerVoicePage`, `AnalisePoolsPage`, `AnaliseAgentesPage`, `AgentsBenchPage`, `MetricSelector`,
   `AnaliseComparacaoPage` — **+ varrer `src/modules/monitor/`** e demais consumidores de `/reports`
   (grep `fetch\(['"\`]/(reports|v1/evaluation|analytics)`). Só GET de relatório; não tocar chamadas de auth.
3. **Backend: zero mudança em A** — `optional_pool_principal` (analytics-api) e `_decode_jwt_optional`
   (evaluation-api) já leem o `Authorization` e aplicam `accessible_pools`. **Exceção:** para o survey,
   entregar a **Fase C junto** (escape hatch), senão o admin segue vendo tudo (accessible_pools vazio→None) mas
   um supervisor restrito perde as respostas web (pool vazio).
4. **Verificação:** logar com um usuário **restrito** (accessible_pools não-vazio, sem admin) → só vê linhas
   dos seus pools em `/analise/*`; admin (accessible_pools vazio→None) → vê tudo. Cobre journeys + survey.
   Guard futuro (opcional): lint/grep que falha em `fetch('/reports`|`fetch('/v1/evaluation` cru (fora do
   `apiFetch`), p/ não reintroduzir call site sem token.

**Consequência aceita (decisão C strict):** com o token fluindo (A) + a decisão strict (C), um usuário
**restrito** vê **zero** respostas web hoje (todas com pool vazio) — é correto (não pertencem ao domínio dele),
não um bug. **Admin não é afetado** (domínio vazio→None→vê tudo). A completude vem de **B** (carimbar o pool
da sessão pesquisada na escrita), que faz as respostas web aparecerem para o supervisor do pool certo.
**Próximo passo natural do arco: B.** (Validação E2E do A/C/E ✅ 2026-07-23 — admin restrito a 2 pools passou a
ver só o pool do domínio; ver CHANGELOG.)

### Fase B — 🟢 web + NPS inline feitos (2026-07-23); falta J4c runner/workflow

**Entregue (ver CHANGELOG § "Segurança — Pool-scoping: Fase B"):** veículo web plumba `pool_id`
(`survey_link_create`→`create` congela no token→`submit` carimba persist + `session.signals`); outbound 5b
carimba `origin_pool` na metadata→dispatcher (`session.survey_origin_pool`)→worker; `agente_nps_v1` passa
`@ctx.session.pool.id`. Smoke `smoke_outbound_fase5b.sh` prova pool não-vazio + controle negativo.

**J4c collect-based ✅ (2026-07-23):** `handle_collect` resolve o pool do alvo (`signal_target_id`) do ctx
(`session.pool.id`), congela em `pending.signal_pool_id`; `handle_collect_engage` semeia `session.survey_pool_id`;
`skill_survey_runner_v1` passa `pool_id: "@ctx.session.survey_pool_id"`. `skill_survey_multi_v1` pesquisa a
própria sessão → `@ctx.session.pool.id`. Pytest `test_collect_pool_scoping.py`.

**Resta `skill_survey_v1` (F10.2b delegate, survey_processo_ia):** grava via `survey_record` de
`@ctx.session.origin_session_id`, mas NÃO passa pelo `handle_collect` (é delegate, não collect). Para carimbar o
pool: no `handle_trigger` (webhook.py), quando `origin_session_id` vier no `workflow_trigger`, ler o
`session.pool.id` do ctx da origem e semear `session.survey_pool_id` na sessão do workflow → `skill_survey_v1`
passa `pool_id: "@ctx.session.survey_pool_id"`. Mudança genérica no trigger (afeta todo trigger com origin) —
avaliar custo/benefício. Até lá, pool vazio = admin-only (decisão C), correto e sem crash.

**Objetivo (histórico):** `survey_instance.pool_id` deixa de nascer vazio — carimbar o **pool da sessão/segmento
PESQUISADO**, para a resposta ter domínio e o supervisor do pool certo a ver.

**Decisão de produto:** o pool da resposta = o pool da **sessão de origem** (`origin_session_id`), não o pool
do dispatcher/runner de survey. É o atendimento que gerou a pesquisa que define o domínio.

**Dois veículos (investigar a origem do pool em cada um):**
1. **Web** (`survey_web`, channel-gateway): o token (`survey_web:token`) tem `origin_session_id`+`grain` mas
   **não** o pool. Duas opções a decidir: **(a)** `survey_link_create` (mcp-server `tools/survey.ts`) passa o
   `pool_id` do contexto do chamador (o hook/skill que cria o link roda numa sessão COM pool — `session.pool.id`
   no ContextStore) → congela no token → persiste; **(b)** resolver no persist a partir do `origin_session_id`
   (lookup do pool da sessão — analytics-api `sessions.pool_id` OU ContextStore `session.pool.id`). (a) é mais
   barato (sem lookup) e o pool já está no contexto de quem dispara; preferir (a), (b) como fallback.
2. **Conferência/inline** (`survey_record`, mcp-server): `pool_id` é input **opcional**. O runner/inline
   (`agente_nps_v1`, `skill_survey_runner_v1`) roda na sessão pesquisada → tem `session.pool.id` no contexto →
   passar via `$.pipeline_state`/`@ctx`. Verificar se o skill já resolve o pool e só não o passa.

**Escopo mínimo:** carimbar o pool na escrita (web + record) + demo/smoke que prova a resposta nascendo com o
pool real (não vazio) e o usuário restrito daquele pool passando a vê-la. **Não** precisa migração de dado
antigo (pool vazio legado = admin-only, decisão C). **Entry points:** `channel-gateway/survey_web.py` (create/
submit + token record), `mcp-server/tools/survey.ts` (`survey_link_create`/`survey_record`), ContextStore
`session.pool.id` (escrito pela Routing Engine no `_write_pool_context`). Ver ADR `adr-survey-response-store.md`
(o `pool_id` já existe no schema; falta a origem na escrita) e `customer-surveys.md` §7.3.

### Fase E — filtro de pool = combo do domínio ✅ (2026-07-23)

**Concluída:** survey → `PoolMultiSelect` (multi, `pool_ids[]`); agentes/contatos → `PoolDomainSelect` (single,
`components/ui/PoolDomainSelect.tsx`) em `AnaliseAgentesPage`/`AnaliseContatosPage`. Single por decisão
(`ContactFilters.poolId` singular/compartilhado; segurança já no backend). Ver CHANGELOG "Fase E (combo do
domínio em agentes/contatos)". Notas de design abaixo (mantidas p/ referência).

**Confirmado (2026-07-23):** o domínio do usuário = bloco **"Accessible Pools"** em Configuration > Access
(`AccessPage.tsx` → `user.accessible_pools` na auth-api → claim `accessible_pools` no JWT; **vazio = todos**).
A sessão **já expõe** isso no client: `useAuth().session.accessiblePools` (`AuthContext`, `[]` = todos).

**Problema:** o filtro de pool nas telas de Analytics é **caixa de texto** — `AnaliseSurveysPage.tsx:233` (a
nova), `AnaliseAgentesPage.tsx:376`, `AnaliseContatosPage.tsx:107`. Deveria ser um **combo multi-select do
domínio**. (`AnaliseJourneysPage`/`CustomerVoicePage` não têm filtro de pool.)

**Design:**
1. **Fonte das opções (client):** `registryApi.listPools(tenantId)` (`api/registry.ts`, já normaliza `items`)
   **∩ `session.accessiblePools`** — se `accessiblePools` vazio (admin) → lista cheia. Assim o combo mostra
   só o que o usuário pode ver (o filtro nunca oferece pool fora do domínio). Referência de `<select>`
   populado por `listPools`: `AnaliseProcessosPage.tsx` (fetch L104-108 + select L151-157) — copiar, mas
   **multi-select** (checkbox-list, como o de `AccessPage.tsx` L430-478, o único multi-select do app; não há
   componente compartilhado — extrair um `PoolMultiSelect` reusável é oportuno).
2. **Backend aceita lista:** `GET /v1/evaluation/survey/responses` troca `pool_id: str` por `pool_ids`
   (repetido ou CSV); `db.list_survey_responses` já filtra `i.pool_id IN (...)` — passar a lista do filtro
   **interseccionada com `accessible_pools`** (o filtro é subconjunto do domínio; a fronteira dura continua no
   scoping da Fase A/C). Vazio no filtro = todo o domínio (não todos os pools).
3. **Invariante:** filtro (subconjunto escolhido) ≠ scoping (domínio permitido). O combo só oferece o domínio;
   o backend **sempre** reintersecta com `accessible_pools` (nunca confia só na UI).
4. Aplicar o mesmo `PoolMultiSelect` às outras telas de texto (agentes, contatos) na varredura.

---

## Detach de hooks de finalização + Pull direcionado + ACW *(desenho fechado 2026-07-23; Camada A iniciada)*

Unifica a coleta de finalização (survey/wrap-up) e aposenta a **Forma A (delegate `skill_survey_v1`)**. Hooks de
finalização não podem suspender/collect (o bridge trata `suspended` como concluído → fecha o contato cedo). A
razão de segurar o contato é **atribuição** — que a Journey (`root_session_id`) + referência de segmento no
payload resolvem sem segurar. Reduz de 3 mecanismos (inline/delegate/collect) para 2 (inline síncrono / collect
assíncrono). Fecha **G1** (AHT inflado por wrap-up) e generaliza **G7** (desacoplamento de `on_human_end`).

**Invariante preservado (PABX):** o "ramal" (direcionar a um recurso) NÃO vira alvo de roteamento — é um work
item que mora num **pool** (fila) com filtro de claim `assigned_to` + **fallback pro pool** por lease. Fila =
pool+dispatch; ramal = pull item direcionado + overflow. Embrião de transfer-to-agent, sem quebrar o invariante.

**Camadas:**
- **A — fundação ✅ (iniciada):** `dispatch: inline|detached` no `PoolHookEntry` (`@plughub/schemas`), default
  `inline`; guard de parse rejeita `detached` em `on_human_start` (não-finalização). Rebuild: agent-registry +
  skill-flow-service + mcp-server (validam skills/pools).
- **B — pull direcionado ✅ (2026-07-24, smoke 5/5):** `assigned_to` + `fallback_to_pool_after_s` +
  `assigned_at_ms` no work item + claim-eligibility em `Router.work_task_claim` (reusa `dispatch_mode: pull`/
  `work_queue`/`PullInboxPanel`). Wrap-up como consumidor = Camada E (não wirado aqui). Smoke
  `infra/test/smoke_directed_pull.sh`.
- **~~C — ACW~~ REVERTIDA (Phase 0) e REMOVIDA (2026-07-29):** entregue em 2026-07-24 (`acw_gate: none|soft|hard`
  + marker `:acw_pending` + regra em `get_ready_instances` + UI + smoke 3/3) e desfeita por operar na **unidade
  errada** — bloqueava a instância inteira (não a vaga) e reservava no dispatch (não no claim). A Phase 0 tirou
  enforcement/marker/smoke; a coluna e todo o plumbing saíram em 2026-07-29 (migration
  `20260729000000_drop_pool_acw_gate`). Capacidade de wrap-up = 1 vaga pelo `claim_instance`, nos dois modos.
  **E2e (produtor do marker) sai de escopo junto.**
- **D — bridge ✅ (2026-07-24, smoke 2/2):** `_fire_detached_hook` (workflow webhook fire-and-forget
  `POST {CHANNEL_GATEWAY_URL}/v1/channels/webhook/pool/{id}`, `origin_session_id`+`journey:inherit`+ref de segmento
  no `context`); `_entry_will_dispatch` exclui detached do barrier (`hook_pending`/`posatt`); auto-close
  `_trigger_contact_close` na leva 100% detached de finalização (fecha G1); guardas `_has_customer_hooks` (IA-primário
  + humano) excluem detached; env `CHANNEL_GATEWAY_URL`. **conference-mechanics.md § Histórico → Mudança 25 ✅.**
  Limitações registradas: `post_human`+detached e `segment_wrapup` fanout detached → Camada E. Smoke
  `infra/test/smoke_detached_hook.sh`.
- **E1 — Forma A aposentada ✅ (2026-07-24):** pools `survey_processo_ia`/`survey_collector_ia`/`survey_reconnect_ia`
  + skills `skill_survey_v1`/`skill_survey_nps_v1`/`skill_survey_reconnect_v1` estavam **inertes** (sem hook/trigger
  vivo); removidos do YAML + arquivos. Coleta de survey = NPS inline + J4c collect. *(DB rodando persiste inerte;
  purge opcional via PRUNE — sem DELETE de pool na API.)*
- **Renderer R0 ✅ (2026-07-24, pré-requisito do Path α):** `DialogFormRenderer.tsx` (núcleo genérico) entregue e
  validado — ver CHANGELOG "Renderer genérico de collect-form no Console — R0". Superfície estável que a E2
  consome: claim de workflow suspensa (`session.dialog_form_id`+resume token) → briefing (`session.briefing_session_id`)
  + DialogForm → `workflow_resume` com `payload.answers`. Falta só o conteúdo/plumbing da E2 (abaixo).
- **E2 — wrap-up humano → `detached` (pendente):** `agente_wrapup_v1`/`wrapup_ia` (inline hoje) vira item de pull
  inbox `assigned_to` o humano (fecha G1 do humano). Plumbar `assigned_to` webhook trigger→routing; `wrapup_ia`→
  `dispatch_mode: pull`; skill de wrap-up como workflow pull (DialogForm no claim); gravação do outcome por
  referência (`surveyed_segment_id`); **produtor do marker `acw_pending`** (setar no dispatch detached de pool
  `hard`, limpar na resolução); briefing. NPS síncrono presente fica `inline`. Fecha as limitações da Camada D
  (post_human+detached, segment_wrapup fanout). **Desenho FECHADO** → ADR
  [`docs/adr/adr-wrapup-detached-pull.md`](docs/adr/adr-wrapup-detached-pull.md). **Decisão (2026-07-24): Path α,
  renderer-first** — o renderer é o **tratamento genérico de collect-form no Console** (não "renderer de
  aprovação"; reenquadramento 2026-07-24, ADR §2.1): renderiza o DialogForm de qualquer `collect`/`delegate`
  reivindicado no inbox pull + submit via `workflow_resume`; serve aprovação + wrap-up + survey-no-Console **sem
  skill por caso** (o wrap-up deixa de ter skill próprio). Construir ANTES (arco/sessão dedicado; kickoff do
  núcleo R0 em `docs/product/approval-renderer-kickoff.md`); wrap-up-α por cima. Path β (skill agente menu) **NÃO
  viável no pull-standalone** (humano reivindica → vira primário, sem IA p/ renderizar; só o Console renderiza).
  Comuns aos dois
  (não se perdem na troca): **E2a** (DialogForm
  `dialog_wrapup_v1` + skill) · **E2b** (tool `segment_outcome_record`) · **E2c** (plumbing `assigned_to` no
  `ConversationInboundEvent`) · **E2d** (dispatch pull sintético no bridge) · **E2e** (`acw_pending` set/clear) ·
  **E2f** (analytics: sessão de wrap-up fora da contagem de contato/TMA — **ponto de atenção**) · **E2g** (config
  `wrapup_ia`→pull + smoke E2E).
- **F — validação:** G1 (AHT), atribuição de segmento no relatório, smoke wrap-up na pull inbox (claim direcionado
  + fallback), pool-scoping do survey sem delegate.

Design fechado: [`docs/product/finalization-hooks-detach-and-directed-pull-design.md`](docs/product/finalization-hooks-detach-and-directed-pull-design.md).

### Camada B — pull direcionado ("ramal") — ✅ (2026-07-24, smoke 5/5; ver CHANGELOG)

> **As-built (2026-07-24):** entregue conforme o kickoff abaixo. Toques do que ficou:
> - **Item = dict `contact_data`** (JSON em `{t}:queue_contact:{sid}`) — sem novo schema Zod; campos `assigned_to`/
>   `fallback_to_pool_after_s`/`assigned_at_ms` tipados em `QueuedContact` (routing `models.py`) e na interface
>   `QueueContact` (TS: `lib/work-queue.ts` + `PullInboxPanel`).
> - **Âncora da janela = `assigned_at_ms`**, auto-carimbada no 1º `add_queued_contact` (registry) e **preservada
>   no re-enqueue** (contact_data re-passado verbatim) — a janela conta desde a atribuição, não reinicia a cada
>   requeue. Fallback p/ `queued_at_ms` se ausente.
> - **Gate em `Router.work_task_claim`** (antes do `ZREM`): reservado só é claimable pelo dono OU após transbordo
>   (idade ≥ `fallback_to_pool_after_s`; ausente = permanente). `reason: reserved_to_other`, **logado** (degradação
>   nunca silenciosa). Sem I/O extra — âncora já no pacote lido no passo 2.
> - **Claimant** = `claimant_user_id` explícito (opcional, plumbado em http_api/tools/server) OU derivado de
>   `instance_id` (`human-{userId}`). Retrocompat: sem `assigned_to` = fila compartilhada (comportamento atual).
> - **Inbox:** `PullInboxPanel` esconde reservados-a-outro (até transbordo), rotula "reservado a você"/"transbordado",
>   ordena reservados-a-mim primeiro; i18n `pullInbox.{reservedToYou,overflow}` + `claimReason.reserved_to_other`.
> - **Sem reaper de lease** (o transbordo é por idade do item, não expiração de lease — o kickoff antecipava lease;
>   o modelo real dispensa). Smoke `infra/test/smoke_directed_pull.sh` (userB barrado na janela; dono sempre;
>   userB após transbordo; reserva permanente nunca transborda).
> - **Validado (2026-07-24):** build dos 3 serviços OK + smoke `smoke_directed_pull.sh` 5/5. **Não wirado:**
>   wrap-up como consumidor = Camada E.

**Objetivo:** um work item da fila pull pode ser **reservado** a um recurso específico (`assigned_to`), com
**transbordo pro pool** por lease. Fila = pool; ramal = item direcionado + overflow. Invariante: `assigned_to` é
elegibilidade de claim sobre trabalho *pooled* — **nunca** alvo de roteamento que bypassa o pool.

**Pré-investigação (abrir a sessão lendo isto):** onde vive o work item e o claim hoje —
- Routing Engine: `dispatch_mode: pull` (claim atômico `ZREM`, lease+auto-release). Achar a estrutura do item na
  fila e o handler de claim (`work_queue_claim`?). Ver `packages/routing-engine`.
- Tools MCP `work_queue_*` (mcp-server-plughub) — o preview/claim que a UI consome.
- `PullInboxPanel` (platform-ui) — como lista/filtra os itens.
- ADR `docs/adr/adr-human-approval-workflow-step.md` (a aprovação já é o 1º uso do pull; reusar o mesmo item).

**Sub-etapas:**
1. **Schema do work item:** `assigned_to?: string` (user_id preferido) + `fallback_to_pool_after_s?: number`
   (default: sem reserva). Onde o item é modelado (schemas / routing). Retrocompat: ausência = fila compartilhada
   (comportamento atual).
2. **Claim-eligibility no Routing Engine:** ao reivindicar, um item com `assigned_to` só é elegível se
   `claimant.user_id == assigned_to` **OU** a idade do item ≥ `fallback_to_pool_after_s` (aí vira claimable por
   qualquer um do pool/grupo). Sem `assigned_to` = elegível a todos (hoje). Cuidar do hot path (barato, sem
   query extra — a idade já está no ZSET score).
3. **Fallback por lease:** o transbordo é do **direcionamento**, não do item (o item continua na fila; só deixa
   de ser exclusivo). Nada de mover de fila.
4. **Tools MCP `work_queue_*`:** expor `assigned_to`/estado ("reservado a você" × "transbordado") no preview.
5. **`PullInboxPanel`:** mostrar itens reservados ao usuário + rótulo de transbordo; ordenar reservados primeiro.
6. **Smoke:** enfileira item com `assigned_to=userA` + `fallback` curto → userB NÃO vê antes do fallback; após,
   userB vê; userA vê sempre. `infra/test/smoke_directed_pull.sh`.

**Não fazer nesta camada:** o wrap-up ainda não é wirado como consumidor (isso é a Camada E, depois de a B e a D
existirem); aqui só o primitivo genérico de pull direcionado. E **nunca** transformar `assigned_to` em alvo de
roteamento (bypass do pool) — é filtro de claim com fallback.

---

## Histórico de contatos do cliente — backlog pós-H5

> O arco Customer History está **completo no v1** (H1–H5 + C1a/C1b ✅ — ver `CHANGELOG.md` e
> `docs/arcos/customer-contact-history.md` §9). Resta:
- **Busca full-text `GIN(tsvector)` (escala)** *(adiado no H5)* — a busca de mensagens (H2) usa hoje
  ClickHouse substring (`positionCaseInsensitiveUTF8`), suficiente no volume atual. Para escala, migrar
  para full-text tokenizado real (índice `GIN(tsvector)` no Postgres `session_stream_events`, ou skip-index
  ClickHouse). É otimização, não correção — a busca funciona. Gatilho: latência/volume medidos.
- **H4-survey** *(bloqueado)* — origem+resultado do survey no **briefing de retorno** (`customer-surveys.md`
  §19), que ainda não existe.

---

## Scheduler / Outbound — resíduos *(arco Scheduler 1–3 ✅ e arco Outbound 1–5 ✅; histórico no CHANGELOG)*

- **Fase 3b do Outbound — ⚠️ a validar:** opt-out global `do_not_contact` no cadastro (identity), veto de
  maior precedência no eligibility salvo `transactional`; `mailing_unsubscribe scope=global` escreve o
  atributo. O smoke `infra/test/smoke_outbound_fase3b.sh` está escrito mas **não foi validado**.
- **Refinamentos do Outbound 5b (backlog):** `responded` por-delivery (submit → `campaign_delivery_result`);
  skill de processo que **auto-alimenta a mailing** no `complete` (journey_complete real — hoje é seed direto).

### Migração dos timers legados *(follow-up — antigo "Scheduler central de timers")*

Consolidar os timers espalhados (timeout de suspend/delegate no channel-gateway,
`_hook_timeout_guard` no bridge, timeout de `collect`) no substrato do scheduler-api:
sorted-set de deadlines (`ZADD`/`ZRANGEBYSCORE`) + poller único + evento `timer.fired`
com os donos reagindo; calendar-api permanece o engine de prazo (calcula o *quando*, não
dispara). Primeiro corte funcional já existe (`run_timeout_scanner` no channel-gateway).
Decisão e mecanismo em [`docs/adr/adr-timer-scheduler.md`](docs/adr/adr-timer-scheduler.md).

---

## Agent-registry — unificar binding skill↔pool (2→1) *(proposta — concern do registry)*

Origem: discussão do doc de avaliação (`docs/arcos/arc-evaluation-metrics-methodology.md` §IV.3),
scoped-out de lá por ser refactor do agent-registry, não de avaliação.

**Achado (revisado 2026-07-20):** a associação skill↔pool aparecia em **três** lugares no `schema.prisma`, mas
o `SkillVersionSlot.pool_ids` (3-slot POR skill) **já foi aposentado** (Skill Versioning Fase E, 2026-06-24 — o
modelo virou pool-cêntrico; `db push` dropou `skill_version_slots`). Hoje sobram **dois**: `PoolSkillSlot`
(slot do pool — binding vivo, autoritativo) e `SkillDeployment.pool_ids` (histórico de deploy). Risco de
divergência entre eles.

**Alvo**: `PoolSkillSlot` como relação **autoritativa** do binding atual + o histórico como **append-log** das
mudanças de slot (o `SkillDeployment` deixaria de precisar do próprio `pool_ids`, derivável do contexto).
**Pré-trabalho**: auditar os readers de `pool_ids` (routing/alocação no caminho quente, RegistrySyncer, lente
deploy do Arc 6 Fase 2, `GET /v1/pools/:id/deployments`) antes de dropar o campo. Escopo menor do que o "3→1"
original sugeria.

---

## Skill hot-reload via YAML em disco sem restart *(deferred — dev/demo only)*

**Fluxo editor → deploy já funciona**: `POST /v1/skills/:id/deploy` → `publishRegistryChanged` → bridge invalida `_skill_flow_cache` → próxima execução busca conteúdo atualizado do agent-registry. Nenhuma mudança necessária para este caminho.

**Gap**: edição direta de arquivo YAML em disco (dev/demo) ainda requer `restart orchestrator-bridge` para o RegistrySyncer re-ler e fazer PUT para o agent-registry. A solução correta é um endpoint `POST /admin/skills/sync` (ou handler de `registry.changed` com `source: disk`) no bridge — chama `RegistrySyncer._sync_skills()` → PUT → `registry.changed` → cache invalidado. Deve ser acionado pelo processo de deploy YAML (CI/CD, script), não pelo editor.

---

## Arc 19 — cleanup residual de infra *(arco concluído 2026-05-28; histórico no CHANGELOG)*

Remover o tópico `workflow.events` do Kafka e arquivar o package `skill-flow-worker`.

---

## Usage Metering — Channel Gateway Adapters *(deferred)*

Funções em `usage_emitter.py` implementadas, mas os adapters de canal ainda não as chamam. Será wired quando cada adapter for criado:

- `whatsapp_conversations` — adapter WhatsApp
- `voice_minutes` — adapter WebRTC/Voice
- `sms_segments` — adapter SMS
- `email_messages` — adapter Email

---

## Pricing Module — Integração metering × pricing *(deferred)*

Módulo que lê contadores de `usage.events` no Redis/ClickHouse, aplica planos configurados no Config API e escreve `{tenant}:quota:limit:*` no Redis. Metering registra mas pricing não consome ainda.

---

## Masking — Bloco 3: Channel Gateway TTS *(deferred até implementação de voz)*

Quando qualquer adapter de voz/TTS for criado, deve consultar `rule.{category}.display_voice` no namespace `masking` do Config API antes de passar texto ao sintetizador. Comportamentos: `silence` (pula o valor), `beep` (tom de beep), `speak_placeholder` (fala "valor mascarado"). Não implementar antes de definir qual engine TTS será usada.

---

## Audit LGPD — Fases Pendentes

Fase 1 concluída — ver CHANGELOG 2026-05-14 e `docs/arcos/audit-lgpd.md`.

- **Fase 2** — `original_content` desmascarado: endpoint de resolução de tokens em Core → analytics-api expõe conteúdo original ao DPO. Requer endpoint batch de resolução de tokens no Core.
- **Fase 3** — `user_access` logs: topic Kafka `user_access.events` em auth-api + tabela ClickHouse + tab ativo em AuditPage.
- **Fase 4** — SAR/Erasure pipeline: CRUD de Subject Access Requests + pseudonimização em `sessions_stream` + anonimização ClickHouse (TTL/partition replacement).
- **Fase 5** — `config_snapshot`: leitura read-only do namespace `masking` do Config API para verificação DPO.

---

## Business in Any Media — processo channel-abstract + framework de loja *(proposta — não implementado)*

Reposicionamento process-centric ("nunca perca um negócio por causa de canal") + framework de comércio conversacional sobre o modelo de 3 níveis (a = fluxo negocial channel-abstract; b = acesso a canais; c = agente de I/O). Especificações em `docs/product/`:

- **Arquitetura-alvo (3 níveis)** — [`docs/product/business-in-any-media-arquitetura-alvo.md`](docs/product/business-in-any-media-arquitetura-alvo.md) + diagrama `business-in-any-media-3-niveis.svg`. Define as 3 camadas, contratos, e o que falta construir no nível (b).
- **Resolvedor de identidade + cadastro (nível b)** — [`docs/product/identity-resolver-nivel-b-spec.md`](docs/product/identity-resolver-nivel-b-spec.md) + sequência `identity-resolver-sequencia.mermaid`. Generaliza o `pending_workflow` existente: cadastro nativo (`customer_id` canônico, dois andares Redis/PG), índice multi-âncora hasheado, retomada cross-canal. Governança: plataforma não é autoridade de identidade/pagamento; só chaves mascaradas; uso interno.
- **Contrato delegate por pool (a→b)** — [`docs/product/delegate-contrato-por-pool-spec.md`](docs/product/delegate-contrato-por-pool-spec.md). Delegação por pool (não skill); decidido alinhar `task.target` a pool; 1 skill publicada por pool; gate de identificação como lógica de fluxo (não campo de schema).
- **Commerce-cards (nível c)** — [`docs/product/commerce-cards-nivel-c-spec.md`](docs/product/commerce-cards-nivel-c-spec.md). `component` tipado em `notify`/`menu` (product_card/carousel/cart/checkout/order_status), render nativo por canal; checkout com masked input + repasse ao PSP; novas ChannelCapability `rich_card`/`carousel`.
- **Fluxo de intake (nível c)** — [`docs/product/intake-flow-nivel-c-spec.md`](docs/product/intake-flow-nivel-c-spec.md). Generaliza o `agente_portabilidade_intake_v1`: resolve identidade (origem do canal) → checa pendência → oferta de retomada → roteia intenção; gate de identificação flow-wired.

Descritivo técnico-funcional consolidado (com a seção de roadmap §20.7): [`docs/product/plughub-descritivo-tecnico-funcional.md`](docs/product/plughub-descritivo-tecnico-funcional.md) (+ `.html` print-ready) — **manter atualizado conforme cada item for implementado**.

**Base que já existe** (não confundir com o gap): workflow + canais + suspend/resume + retomada via `pending_workflow` + masking. **A construir**: cadastro de identidade completo, commerce-cards, gate, e o nível (b) como camada de primeira classe.

---

## Fila de trabalho humano / dispatch pull + inbox no Console — resíduos pós-v1 *(v1 concluído 2026-07-17; histórico no CHANGELOG)*

**Resta (A6 — pós-v1, ADR §6 `adr-human-approval-workflow-step.md`):** quatro-olhos (2 aprovadores);
reatribuição por supervisor (= conferência padrão); notificações/SLA na inbox; **rework rate**
(Bancada/Arc 6); **auto-aprovação** (pool IA). **Não-objetivos v1 (adiados por decisão):** omnichannel/
Modo B (D6); weight-ordering (F6); **promote real** (invoke de deploy no `efetuar_promocao`, hoje
`complete`). **Follow-ups menores (CHANGELOG A5):** Context/History trazendo a journey do workflow por
`root_session_id` (aprovação raramente tem `customer_id`); gate de servibilidade do pool de aprovação
pelo ABAC `approvals` (fechar o claim genérico); refresh imediato do inbox pós-release.

**Diferido desde a F1.3** (spec "sem sweep dedicado"): renovação da lease de claim por heartbeat +
sweeper de "conectado-mas-ocioso". Hoje o auto-release do pull é emergente (desconexão → bridge
re-roteia → `route()` parqueia e limpa a lease); a inbox sinaliza melhor que um sweep.

## Frente 2 — Avaliação campaign-driven — resíduos *(pipeline S1/S2.1/S2.Q1/S2.2 ✅ e lente `deploy` P2+P3 ✅; histórico no CHANGELOG)*

Avaliação é **sempre dirigida por campanha** (janela = `evaluation_calendar_id`, throttle = `avaliacao_ia.max_concurrent_sessions`).
Pipeline validado E2E com avaliador real (2026-06-17) e lente `deploy` ancorada no pool (2026-06-20).
Specs: `docs/product/arc6-phase2-deploy-observability-spec.md`, `docs/product/calendar-consolidation-and-trigger.md`.

**Diferidos por decisão do usuário (reabrir só se observabilidade por deploy/versão virar requisito)**
- **P4 (núcleo §4.1/D4)** — série por **epoch/versão**: eixo X = versões do pool (`[deploy N, deploy N+1)`), ponto = qualidade média da versão, N por versão. Hoje o eixo é tempo + `deploy_markers` (leitura de "v1 vs v2" ainda manual). Seed: `infra/test/seed_deploy_lens_demo.sh`.
- **Ruído herdado do board na lente `deploy` (§4.5/D3)** — média/multi-seleção fazem pouco sentido numa lente de versões; avaliar remover/ocultar e focar single-skill quando o epoch entrar.
- **Markers exigem `flow_id == skill_id` (§8)** — no demo `sac_ia` (agent_type_id) ≠ `skill_atendimento_sac_v1`; só alinha quando o `flow_id` carrega o skill_id real *(verificar se o re-ancoramento por pool do P3 já tornou isso irrelevante)*.
- **Capacidades perdidas com a remoção das abas Trend/Comparison** (não existem no bench): significância estatística (N<30), comparação de **períodos arbitrários A vs B**, overlay multi-métrica. Se voltarem, entram como modo "comparar fatias/deploy" no bench.
- `TimeseriesView`/`ComparisonView` continuam no repo como **código morto** (não removidos no cleanup).

**Nits do bench (diferidos, não fechados)**
- **Quality score geral diluído** — KPI "Quality score 0.00 (N evals)" do drill-down e a curva da lente `quality` saem baixos/zero enquanto o radar de dimensões está correto. Hipótese original (zero-fill por sessão) **refutada** por leitura de `analytics-api/reports_query.py`. Achado real não confirmado como causa: `_compare_quality_lens` filtra a janela pelo `timestamp` da avaliação, enquanto `_fetch_agents_cross` filtra por `attr.session_started_at` — mas a mesma divergência existe em `_compare_quality_criteria_lens` (que está correto). **Requer reprodução ao vivo com dado real** (range + Quality/N evals/Sessions do drill-down vs. a linha do mesmo agente na tabela) antes de qualquer fix.
- **Janela/período** — confirmar se KPI, lente e tabela de dimensão usam períodos diferentes no mesmo request (não confirmado); considerar default próprio do bench (hoje reusa `DEFAULT_FILTERS` de `contacts/types.ts`, 7 dias, alinhado com `_default_from`/`_default_to`).
- **NPS por agente parece alto** (pequeno).

**Contrato de avaliação / robustez**
- **Unificação do contrato prompt×schema (desenhada, não implementada)** — prompt `evaluation_rubric_v3` é fixo e deveria derivar do `EvaluationForm`; `_format_schema` do ai-gateway é **lossy** (descarta `items`/`properties`/`description`/`nullable`; `OutputFieldSchema` nem os modela); alvo = YAML `output_schema` ≡ Zod do `evaluation_submit`, permitindo **remover os shims de compat**. *(O nit específico da perda de `justification`/evidência foi fechado no T9-C.fix2.)*
- **Sessão sem dados** — avaliar sessão "magra" ainda falha duro no `evaluation_submit` (`overall_score=null` × `composite_score: number` obrigatório). Contrato escolhido: avaliador detecta sessão sem conteúdo e marca a instance `skipped`/`error` com motivo, **sem** chamar submit; pode exigir `skipped` no enum (hoje só `error`).

**Pipeline / superfícies faltantes**
- **S2.3** — dispatcher automático drenando instances `scheduled` das campanhas com `evaluation_calendar_id` aberto (calendar-api `is_open`), respeitando a capacidade do pool avaliador *(verificar sobreposição com o dispatcher windowed T15 já existente)*.
- **Surface de instances `scheduled`** — hoje Avaliações mostra só resultados; operador não tem visão da fila agendada.
- **CampaignsPage** — sem editar/deletar campanha (só create + pause/resume), embora a API já tenha `CampaignUpdate`/PUT.
- **i18n** — chaves `campaigns.seedSynthetic*` (en/pt-BR) nunca adicionadas; e rebuild do `platform-ui` para as chaves Arc 13 (`contest.*`/`review.*`) entrarem em produção *(verificar se já rebuildado)*.
- **Curation/Calibration (Arc 13 Fase H)** — telas existem mas nunca validadas com dado real; exercitar o **Fluxo 2** (curadoria → `calibration_signal` → CalibrationNote → KB), que só rodou via seeder.
- **Fila de revisão do supervisor** ("Awaiting my action", depende de `available_actions`) — confirmar se existe.

**Auth / limpeza**
- **G-PROBE, perna agente/sistema** — `submit_pre_review`, `seed/flush-synthetic`, `create/update/delete_sampling_rule`, `publish_calibration_note` seguem **header-only** (`X-Tenant-ID`/`X-User-ID`). Decisão 2026-07-01: **não** usar credencial de serviço ad-hoc; gatear por `principal_id` do **Agent Principal** (F1–F4) quando existir. Perna humana `curar` ✅ resolvida. Ver seção `## G-PROBE` própria neste arquivo.
- **G-S2.4 aposentado (decisão 2026-06-25)** — resta o *follow-up opcional* de **remoção física da cola morta**: consumer reativo `workflow.events` na evaluation-api, coluna/seletor `review_workflow_skill_id`, skills `skill_revisao_*`/`agente_revisor_v1` e o cenário e2e 28. Slice próprio (raio de teste no 28).

**Achados pré-existentes (não causados pela F1.0)**
- **A — specialist-return (pré-requisito/núcleo da F4)**: conference specialist que termina com `escalate` re-roteia o CONTATO em vez de **voltar ao chamador** (ex.: `agente_auth_form_v1.yaml` → `retencao_humano` → fila, com mensagem de fila espúria). Fix preferido: **engine** — flow em modo conference specialist trata `escalate`/`complete` como retorno-ao-chamador devolvendo outcome. Sub-arco próprio.
- **B — multi-sessão humana no push**: humano servindo entra `state="busy"` e `get_ready_instances` exige `state=="ready"` → mesmo sob capacidade (`max_concurrent=3`, vindo da URL do WS do Console — `mcp-server` server.ts:2147 — não do `auth`) não recebe 2º contato via push. Pull (F1) endereça; decisão pendente: o push também deveria manter `ready` enquanto sob capacidade? Medir ao vivo antes de atacar.

---

## Record/Replay Harness — gravação/replay em todas as costuras *(proposta — não implementado)*

Visão + spec em [`docs/product/record-replay-harness-spec.md`](docs/product/record-replay-harness-spec.md). Generaliza o Session Replayer (que hoje replaya só o stream da sessão, para avaliação) num harness "VCR" em todas as costuras (channel-gateway, AI Gateway, MCP, Kafka) — cada costura como **driver** (injeta inputs gravados) ou **mock** (devolve outputs gravados), com timings.

**Base que já existe**: `session-replayer` (persister/hydrator/replayer/comparator), `ComparisonReport` (Jaccard + deltas), `delta_ms`/`speed_factor`, Kafka como log, harness `e2e-tests`. **A construir**: captura full-fidelity de payload em MCP/AI Gateway (hoje `mcp.audit` é só metadado), clock/seed injetável (determinismo), harness multi-costura, gravação seletiva (golden/amostrada/on-demand) com masking, e o **gate de promoção** consumindo o `ComparisonReport` como critério objetivo. Aplicações: regressão determinística, repro de bug, simulação de carga, datasets de avaliação.

---

---

