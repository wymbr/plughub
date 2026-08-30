# TODO — PlugHub Itens Pendentes


## ✅ SETE decisões do dono — sessão de 2026-08-30 *(registro ÚNICO; não duplicar)*

> Levantamento das decisões pendentes, percorridas em dois grupos. **Este é o único lugar
> onde elas moram**: os itens originais abaixo apontam para cá em vez de repetirem, porque
> duas cópias de um plano divergem e este repositório já pagou por isso.

**Achado de método, antes das decisões.** Eu listei sete e **duas não eram decisão** — a
medição as dissolveu antes de chegarem à mesa, porque eu as havia levantado lendo **títulos**:

| listado como aberto | estado real |
|---|---|
| chip de PROCESSO p/ usuário escopado | **decidido, implementado e provado em 2026-08-26** (caminho (a); gate verde, metade visual 4/4). Só o TÍTULO da seção dizia "decisão aberta" |
| `AnaliseTab.tsx` volta ou morre | **arquivo apagado** em `10bde79`; a feature (métricas agregadas do conjunto filtrado) foi absorvida pelas cinco lentes da F2 |

É o item 1 da § *"Erros de método"* acontecendo comigo: **item antigo mente sobre o próprio
estado**, e o TÍTULO mente para mais gente que o corpo (item 5 da mesma seção).

### Grupo 1 — o que bloqueava a V4

| # | decisão | onde |
|---|---|---|
| 1 | tipo **`card_expiry`** (`operator: last_2` · `lgpd: financeiro` · `declared_only` · `display "##/##"`) | `audit.ts` + `seed.py` |
| 2 | tipo **`linha_em_servico`** (`by_role: {}` · `lgpd: pessoal` · `declared_only`) | idem |
| 3 | **nenhuma** declaração de `cpf_titular`; a folha vira `cartao.cpf` com o alias | `context-map.ts` + `seed.py` |

**#1 — por que tipo novo e não reuso.** O campo já tinha POLÍTICA VIVA (duas regras, `last_2`)
e nenhum dos 11 tipos casava máscara **e** classe. Não é `credit_card` (`last_4` sobre `1226`
devolve tudo — o argumento da T6 para o CVV) nem `cpf` (a classe difere). O tipo **declara** a
política que já valia; zero mudança de comportamento. `last_2` sobre `12/26` → `***26`: aparece
o ano, some o mês.
⚠️ **É tipo de LEITURA.** Ele mascara algo, logo passa no portão da T5 e pode ser escrito num
`masked:` — e isso quebraria o pacote de aprovação **em silêncio** (D4: masked nunca entra em
`pipeline_state`, e é de lá que `skill_limite_entrada_v1.yaml:475` lê para escrever a tag). O
form declara `masked` só no `cvv`, e isso é desenho, não esquecimento.

**#2 — a decisão inverteu o diagnóstico da V3, e a evidência que eu trouxe estava fraca.**
Apresentei o `_LEGACY_PREVIEW_SPEC` (`webhook.py:2298`, `numero_atual → last_4`) como prova de
que a plataforma já protegia o campo numa borda irmã. **Não prova**: aquele preview vai ao
**cliente**, na retomada cross-canal, e ali mascarar é **anti-enumeração** — não confirmar dado
a quem ainda não provou posse. Outra audiência, outra finalidade; as duas bordas podem divergir
com razão.
Medido ao decidir: a máscara do ContextStore vale em **duas portas apenas** — o endpoint da
Console (`server.ts:1536`) e `maskContextForPersistence` (persistência F5 + tool
`supervisor_state`). A interpolação `@ctx.` do engine lê o hash **cru**, então nenhum fluxo é
afetado por regra nenhuma.
Decisão: o número **é o objeto do atendimento**, não dado de cadastro — segue em claro, agora
**declarado** como tal. O telefone de CADASTRO (`session.cliente.telefone`) continua protegido.
A §1.1 do ADR segue valendo: o defeito nunca foi o valor visível, foi o valor visível **porque
ninguém decidiu**.

**#3 — o princípio do dono, e o caso concreto que dissolveu.** Princípio: *"declara-se `cpf`,
genérico; se for preciso discriminar, o cadastro aponta para o canônico"*. **A medição o
reforça:** o casador de regra **não tem glob de meio** (`context-masking.ts:80-160` aceita
exato, `*.sufixo`, `prefixo.*`, `*`), então `*cpf*` seria regra **inerte**, sem nada ficar
vermelho. Logo *"CPF protegido independente de qual CPF"* só existe com a tag terminando em
`.cpf` — o discriminador no segmento de **domínio**. Princípio e mecanismo coincidem.
**E o campo é fóssil:** `session.cpf_titular` **não tem produtor** — o campo de tela saiu do
formulário, substituído por `vencimento_cartao` (`skill_limite_processo_v1.yaml:88-91`; o form
vivo tem 4 campos e nenhum é ele), e `skill_limite_entrada_v1.yaml:475` escreve `session.cpf`,
que `*.cpf` já cobre. **A "divergência nos dois sentidos" não são duas políticas: é uma política
em dois momentos** — o `__global__` é mais velho que a mudança do formulário, e
`vencimento_cartao` é o *sucessor* de `cpf_titular`. `overwrite_would_drop = 1` está contando
uma regra morta.
O alias FICA no mapa para que o **contador** prove o fóssil extinto, em vez de nós afirmarmos.

### Grupo 2

| # | decisão | consequência |
|---|---|---|
| 4 | **congelar** o mínimo declarado dos presets `developer`/`business` | zero código; o gatilho é o primeiro portador real |
| 5 | **remover** `platform_permissions` + `permission_templates` + `apply` + as 2 funções mortas | template segue como preset de `module_config` (o uso que a UI já faz) |
| 6 | ✅ **FEITA em 2026-08-30** — peça 1 da (d), sem se comprometer com ela | escopo de conteúdo fechado nas 4 rotas; a (d) segue adiada, agora com o pré-requisito INTEIRO pronto |
| 7 | ordem: grupo 1 → registrar → provar fóssil → **V4** → resto | — |

**#4 — medido ao vivo (`auth.users`, base `plughub_demo`, 2026-08-30):** supervisor 2 · admin 2
· operator 2 · **developer 1 — e é o `admin@`** · **business 0**. Seis usuários, todos fixtures.
Isso dá CAUSA ao que o item registrava como sintoma (*"não têm baseline medível"*): **não há
população**. O preset de `developer` é **inerte por construção** (seu único portador já é admin,
que concede mais) e o de `business` se aplica a ninguém. Desenhar persona contra zero população
é o erro que as colunas ANI/DNIS custaram.

**#5 — ✅ ENTREGUE em 2026-08-30** (detalhe no `CHANGELOG.md`). Medido ao vivo:
`auth.platform_permissions` **0 linhas** · `auth.permission_templates` **0 linhas** · **zero
consumidores de produção** das rotas (nem UI, nem serviço). **São os testes que o faziam
parecer vivo** — 20 asserções, e eram as únicas. Suíte auth-api **83 → 63**, zero falhas dos
dois lados.

⚠️ **Correção de uma medição minha.** Eu escrevi aqui *"laço morto fechado — `user_can()` sem
nenhum chamador"*. **A função não se chama `user_can`** (é `resolve_permissions`), e ela TEM
chamador: o endpoint `GET /permissions/resolve`. O grep voltou vazio pelo **nome errado** — é
*"um teste que não pode reprovar"* aplicado a uma medição, com o vazio parecendo resposta. A
caracterização correta é **endpoints sem consumidor de produção sobre tabela vazia**, que já
bastava; só `get_accessible_pools_for_module` estava literalmente sem chamador. A UI documenta outra semântica em
`AccessPage.tsx:37-44` (snapshot que PRÉ-PREENCHE o form, *"sem vínculo vivo nem propagação"*).
Seguro **agora justamente porque há 0 linhas**; no dia em que alguém chamar `apply`, remover vira
migração. A baseline de teste do auth-api **cai junto** — correto, não regressão.
Consequência aceita: sobram **dois** mecanismos de preset escrevendo `module_config` —
`role_defaults` (servidor, automático no create) e o template (cliente, cópia manual). Não
competem (gatilhos diferentes), mas só o primeiro é o default.

**#6 — o que mudou sem ninguém trabalhar nela:** em 2026-08-29 o eixo de cobertura de credencial
por rota fechou (18 gateadas, 1 isenta nomeada), o que é **metade da peça 1** da (d). As peças 1
e 2 são o mesmo trabalho com ou sem a (d); a peça 3 (filtro de relevância) só existe **se** as
listas abrirem. Fechar o escopo é o pré-requisito que a própria (d) exige primeiro, então
fazê-lo não compromete com ela.

### Três regras que saíram destas decisões — valem além das folhas que as originaram

1. **Finalidade é dimensão do TIPO, nunca exceção de regra.** Campo cuja finalidade dispensa
   máscara ganha tipo próprio, **nomeado pela finalidade** (`linha_em_servico`, jamais
   `phone_open`), com a **classe LGPD preservada** — o que se declara vazio é a máscara, nunca a
   classe. Mapa e regra dando respostas diferentes é a duplicação que este arco existe para
   matar, e a permissiva venceria.
2. **O discriminador mora no segmento de DOMÍNIO, nunca no nome da folha.** Medido: sem glob de
   meio, `escopo.dominio.campo` é a única forma de um glob genérico alcançar a família inteira.
   Critério para toda folha nova do mapa.
3. **Preset e template não se decidem contra zero população.** O gatilho é o primeiro portador
   real, e ele fica ESCRITO no item — não é adiamento, é a condição que torna a decisão legível.

### Estado da implementação (2026-08-30)

**Passo 1 ✅ — código na árvore.** `audit.ts` (+2 tipos), `context-map.ts` (folha `cartao.cpf`
renomeada, `cartao.vencimento` nova, `portabilidade.numero_atual` retipada, mais os DOIS blocos
de cabeçalho que passariam a mentir), `seed.py` (as três cópias espelhadas).
Oráculos verdes: catálogo `declared=13 · órfãos=[] · categorias_sem_tipo=[]`; mapa
`declared=75 · aliases=40`, as quatro listas vazias. `typeMasksSomething`: `card_expiry=true`
(elegível a `masked:` — mitigado por comentário), `linha_em_servico=false` (inelegível, como o
`texto`). `DEFAULT_MASKING_RULES` segue em **4** — nenhum tipo novo é detectável.

**Passo 3 ✅ — o store vivo foi reaplicado** (detalhe no `CHANGELOG.md`, § D8). O fóssil
`cpf_titular` foi **provado sem produtor** onde ele poderia existir, com testemunha positiva ao
lado do zero: `skills.flow`/`flow_draft` **0** (sucessor em 2 de 44) e
`pool_skill_slots.yaml_snapshot` — *o que EXECUTA* — **0** (sucessor em 4 de 39). A imagem do
container estava atrás do repo (ramo A da própria D7) e foi reconstruída **antes** do
`--overwrite`; depois, restart do config-api.

| | antes | depois (servido pela API) |
|---|---|---|
| `masking.types` | 11 | **13** |
| `masking.context_map` | 74 canônicas / 39 aliases | **75 / 40** |
| `session.cartao` | `numero, cpf_titular, limite_*` | `numero, **cpf**, **vencimento**, limite_*` |
| `numero_atual` | `phone` | **`linha_em_servico`** |
| `context_rules` (`__global__`) | 14 | **23**, sem `cpf_titular` |

Gate `probe_context_map_audit.sh`: **16 asserções verdes**, com o ramo F **invertido** (a lacuna
deliberada virou lacuna FECHADA) e provado por mutação — verde → vermelho → verde.

⚠️ **Duas medições ficaram INCONCLUSIVAS e não viram negativas:** ContextStore vivo com **0
hashes `ctx`** (TTL 4 h, sem contato recente) e store durável da F5 com 221 linhas, **nenhuma do
fluxo de limite**. Não pesam na decisão — o eixo que importa é o PRODUTOR, e a F5 persiste já
mascarado —, mas ficam ditas para que ninguém as leia como prova.

⚠️ **Resíduo nomeado:** o `tenant_demo` tem override próprio de `context_rules` (o seed só escreve
`__global__`) e difere do global por **exatamente uma regra em cada direção** — tem
`session.cpf_titular`, não tem a exata `session.vencimento_cartao`. Mesmo retrato pré-troca, agora
do lado do tenant. **Sem lacuna de comportamento** (o glob `*.vencimento_cartao` está nos dois);
limpá-lo é ato sobre config de TENANT, não sobre default de plataforma.

### Correções de estado que a sessão produziu

Além das duas dissolvidas no topo:

- 🟢 **`mcp-server-plughub` COMPILA.** O achado 3 da § *"Cobertura de credencial por ROTA"*
  (*"não compila / `TS2345` em `usage-emitter.ts`"*) está **vencido**: o conserto
  (`segment_id: null`) está na árvore em `usage-emitter.ts:82`, o container **bate com o repo**
  (sem bind mount) e `npx tsc --noEmit` → **`TSC_OK`**, medido em 2026-08-30.
- 🟡 **A cauda de papel no backend tem 8 sítios a mais.** O censo do passo 8 do ABAC TOTAL lista
  **4** (channel-gateway, evaluation-api ×2, auth-api) e o arco está marcado *"1–8 COMPLETO"*.
  Medido: `mcp-server-plughub/src/server.ts` tem **8** portões de papel
  (`:1441, :1750, :1841, :2200, :2674, :2756, :2827, :2858`) — um serviço inteiro não varrido.
  A direção é segura (menu mais estrito que backend), mas alguém vai ler o ✅ como *"papel saiu
  do backend"*. É a regra da casa outra vez: **um censo desenhado para um eixo não prova nada
  sobre o eixo vizinho** — aqui, o mesmo eixo com um serviço fora da varredura.

## 🧭 Censo de cadastro da D9 — ✅ FEITO *(2026-08-30)* e resíduos de ambiente

### O resultado: [`docs/product/contextstore-cadastro-censo.md`](docs/product/contextstore-cadastro-censo.md)

**A premissa da D9.2 sobrevive — ZERO nomes dinâmicos** em 42 skills e em todos os escritores
de plataforma. Os dois casos de composição são `segment.{segId}.<folha literal>`, a FAMÍLIA
que a D9.4 já prevê.

**Mas ela é verdadeira por um motivo diferente do que a D9 supôs.** A D9 contava DUAS
superfícies de autoria; são **SEIS**, e quatro são invisíveis a um caminhador de árvore de
YAML — `context_json` é uma **string JSON**, e `context_set`/`context_write` guardam o nome em
`input.tag`, um campo de step como outro qualquer. Um portão de publish escrito da forma óbvia
ficaria **verde com quatro superfícies passando por baixo**: fail-open por INVISIBILIDADE, não
por decisão. É a família do *valor plausível*, do lado do instrumento.

E há a **terceira origem** que a D9.3 não previa: o **corpo HTTP do webhook**
(`webhook.py:630` escreve cada chave verbatim, sem prefixo) — é assim que existem as duas
únicas tags sem namespace nenhum, `campaign_id` e `target_pool`. Nenhum portão de publish a
alcança; sobra a postura de runtime da D9.1.

**Os números:** 91 nomes escritos (61 tenant · 35 plataforma) · 54 já cobertos ·
**37 a cadastrar** · 21 lidos sem escritor · **0 dinâmicos**.

**O número que dimensiona a decisão não é 37, é 10** — 27 dos 37 são identificador/enum e
viram `texto` sem política; as outras 10 pedem uma escolha de tipo.

> ⛔ **CORRIGIDO no mesmo dia.** Escrevi aqui que **8 dos 10** *"pedem uma capacidade que o
> catálogo não tem"* e que a **D9.5 subia a PRÉ-REQUISITO**. Falso: `ContextMapFieldSchema.tipo`
> é `z.string()` validado contra o catálogo ⇒ **todo campo tem onde ser cadastrado**, `opaque`
> inclusive. A D9.5 foi **depreciada pela própria D9** (o mecanismo que ela invocava não
> existe: `detect_pattern` tem zero consumidores). **A migração não tem bloqueio.**

**Validação cruzada:** os 7 `unknown` que a auditoria ao vivo acusou estão todos nesta lista.
O censo é superconjunto estrito do que o tráfego achou, por um fator de ~5 — que é o próprio
argumento da D9.2 contra descobrir por observação.

> ⚠️ **O aviso de sobre-coleta continua valendo, mas mudou de forma.** Este censo é
> ESTRUTURAL (caminha o YAML e conhece cada superfície), não `grep` — por isso não acusou
> tópico Kafka nem fixture. O que ele ainda não separa é *ler sem escritor* de *dead read*:
> os 21 da segunda lista são uma pergunta em aberto, não um defeito medido.

**Próximo passo, se a D9 for adiante:** escrever o extrator — não há decisão pendente antes
dele. Ele tem de nascer conhecendo as seis superfícies; o `cadastro2.py` do scratchpad (hoje
versionado em `infra/test/censo_contextstore_cadastro.py`) é o protótipo, e as três correções
que ele precisou (string JSON, `input.tag`, prefixo composto no gateway) são o teste que ele
tem de passar.

### Resíduos de AMBIENTE — não viajam no commit

- **`mcp-server-plughub` não foi reconstruído.** O `DEFAULT_CONTEXT_MAP` embutido nele está
  **duas versões atrás** (11 tipos, 74/39). Comportamento vivo está correto — ele busca o mapa
  do config-api com cache de 60 s —, mas o **fallback** só é exercido se o config-api ficar
  inalcançável, e aí serviria mapa velho. Entra no próximo build.
- **`auth-api` não foi reconstruído**: o container ainda roda o subsistema `platform_permissions`
  que este commit removeu do código.
- **O store vivo DESTE demo já está reaplicado** (13 tipos, 75/53 depois dos aliases). Em
  qualquer outro ambiente o código não basta: exige `--overwrite` das três chaves de `masking`
  **mais restart do config-api** — o cache é em processo.
- **`tenant_demo` mantém a regra fóssil `session.cpf_titular`** no override próprio (o seed só
  escreve `__global__`). Difere do global por uma regra em cada direção. **Sem lacuna de
  comportamento** — o glob `*.vencimento_cartao` está nos dois. Limpá-lo é ato sobre config de
  TENANT, não default de plataforma.

### Estado da auditoria, para não ser remedido do zero

Série cortada e retomada em 2026-08-30 sob o mapa novo, com cache quente:
**alias 18 · canonical 4 · unknown 7 · dynamic 0 · overflow 0**, sobre quatro fluxos
(`smoke_limite_tres_acessos`, `smoke_journey_context`, `smoke_wrapup_arc12_capture`,
`smoke_detached_hook`). Os 7 `unknown` são `session.title`, `session.root_session_id`,
`session.summary`, `session.status`, `session.preview`, `session.approval_threshold` e
`approval.summary` — e **não devem ser decididos um a um**: a D9 os dissolve.


## ✅ Chamadores internos da analytics-api — FECHADO em 2026-08-30 *(detalhe no `CHANGELOG.md`)*

> A seção abaixo é o DIAGNÓSTICO original, mantido porque a medição vale. O conserto veio no
> mesmo dia e cresceu: não era uma rota, eram **quatro chamadores**, e a investigação
> desenterrou um defeito mais antigo — o `handoff-status` do agent-registry **nunca funcionou**
> neste deploy (`ANALYTICS_API_URL` ausente ⇒ `fetch failed` ⇒ `catch {}` ⇒ `active_sessions: 0`;
> a resposta certa era 24). Gate: `infra/test/probe_internal_service_callers.sh`.
>
> **Aberto, com gatilho declarado:** o 4º chamador (`mcp-server` → `/v1/audit/mcp-calls`)
> **não** foi atendido, de propósito — aquela rota tem portão LGPD próprio e a fonte está
> vazia (`session_timeline` = 0 linhas, medido). Quando ela ganhar produtor, decidir se a
> leitura da própria plataforma é acesso auditado (instinto: é, e deve ser GRAVADA, nunca
> isenta). O ramo G do probe guarda a regressão de dar chave mestra ao serviço.

Achado de lambuja ao fechar o escopo de conteúdo; **independente daquela mudança**, e anterior a ela.

**Os dois lados, medidos:**

- **O chamador não manda credencial.** `evaluation-api/router.py:2221` faz
  `client.get(f"{analytics_api_url}/v1/transcript/sessions/{session_id}", params=params)` —
  nenhum header, nenhum token de serviço.
- **A rota recusa sem credencial.** Desde 2026-08-27 `optional_pool_principal` responde **401**
  quando não há `Authorization` e `analytics_open_access` está desligado. Ele **está**: default
  `False` em `config.py:75` e **zero ocorrências** da env em `infra/` (compose, `.env*`, scripts).
  Confirmado ao vivo: `curl` sem header → `401 {"detail":"auth_required"}`.

**Consequência:** `resp.status_code >= 400` → `HTTPException(502, "transcript service error (401)")`.
Quem abre a transcrição pela tela de Qualidade recebe 502.

**Por que não consertei junto.** As duas saídas óbvias decidem política, não fiação:

| saída | o que decide |
|---|---|
| encaminhar o token do usuário | o escopo de POOL passa a valer sobre uma leitura que a evaluation-api já gateia por `module_config.evaluation.*` + `pool_id` — dois eixos, e alguém tem de dizer qual manda |
| token de serviço / header interno | a evaluation-api vira a autoridade única daquela leitura, e o analytics confia na delegação |

O ADR da transcrição diz **explicitamente** que o gate de PAPEL fica na evaluation-api, *"NOT here"*
— o que favorece a segunda. Mas isso é decisão de quem desenhou a delegação, não conserto mecânico.

⚠️ **O tell de método:** o defeito nasceu de uma rota chamada `optional_pool_principal` que deixou
de ser opcional em 2026-08-27. O nome ainda diz "optional", e o chamador que dependia disso não
tinha teste atravessando a fronteira — é o mesmo modo de falha do `probe_audit_surface`: *o
veredicto foi coberto, a travessia não*.

## 🟡 Pré-requisito da V4 — passo 1 ✅; o resto foi SUPERSEDIDO pela D9 *(2026-08-30)*

> ⚠️ **Leia primeiro:** este bloco descreve o trabalho sob o modelo ANTIGO (mapa de nomes como
> allowlist de leitura). A **D9** do `adr-contextstore-allowlist.md` o supersede — o ContextStore
> passa a ser **cadastro**, com portão no **publish**. Os aliases do passo 1 continuam válidos e
> viram **instrumento de migração**; os sete do passo 2 deixam de ser pergunta. O que fica desta
> seção é a MEDIÇÃO, que é o que motivou a emenda.

**A auditoria da V3 acordou.** Até hoje ela nunca gravara nada — não por defeito: o mcp-server já
tinha a instrumentação (imagem de 2026-08-29 23:29, `observeContextTags` em `src` e `dist`) e a
auditoria **não expira** (nenhum `EXPIRE`; acumula para sempre). Faltava **tráfego**. O
`unknown: []` que se lia era `0` sobre **zero leituras** — a armadilha que o próprio ADR nomeia
(*"zero sobre zero é serviço parado"*), e autorizar a inversão não-reversível com esse número seria
o pior uso possível dela.

Quatro fluxos rodados (`smoke_limite_tres_acessos`, `smoke_journey_context`,
`smoke_wrapup_arc12_capture`, `smoke_detached_hook`) produziram o primeiro balde real:
**alias 12 · canonical 4 · unknown 13 · dynamic 0 · overflow 0**.

### Confirmações que o tráfego trouxe

- **`session.vencimento_cartao` → alias de `session.cartao.vencimento`** — a D8.3 provada com dado
  vivo, não com oráculo.
- **`session.cpf_titular` NÃO apareceu** — o fóssil confirmado morto com tráfego real, o que fecha
  a última dúvida sobre o `--overwrite` do passo 3.

### 🔴 O bloqueio: dos 13 `unknown`, SEIS já têm canônica no mapa — sem `legado`

| tag REAL escrita | canônica declarada no mapa | estado |
|---|---|---|
| `session.dialog_form_id` | `session.workflow.dialog_form_id` | canônica **sem produtor**; a real cai em `unknown` |
| `session.decisions` | `session.workflow.decisions` | idem |
| `session.origin_session_id` | declarada (`:230`) | idem |
| `session.briefing_session_id` | declarada (`:231`) | idem |
| `session.surveyed_agent_key` | declarada (`:253`) | idem |
| `session.surveyed_segment_id` | declarada (`:254`) | idem |

As outras **sete** são genuinamente não declaradas: `session.root_session_id`, `session.title`,
`session.summary`, `session.status`, `session.preview`, `session.approval_threshold` e
**`approval.summary`** — este último de um namespace `approval.*` que o mapa não conhece.

**Por que isto BLOQUEIA a V4, e não é detalhe de completude:** inverter agora negaria
`session.dialog_form_id` e `session.decisions` — que são **exatamente** os campos que o seed do
config-api avisa por escrito ("*NÃO acrescentar um catch-all `session.*`: derrubaria
`session.dialog_form_id`/`session.decisions` e a tela de aprovação deixaria de renderizar em
silêncio*"). A V4 chegaria ao mesmo defeito por outro caminho.

**E há um fato maior por trás:** das **75 canônicas declaradas**, o tráfego observou **4**
(`session.pool.id`, `session.pool.channels`, `session.queue.position`, `session.queue.eta_ms`). O
resto ou é alcançado por **alias** (12 grafias legadas vivas) ou **não tem produtor**. O mapa da V3
declarou nomes canônicos aspiracionais — o estado-alvo da migração — enquanto a grafia que o código
realmente escreve ficou de fora em seis casos. **Isso não é erro do censo:** o censo mediu leituras
`@ctx.` e declarações `tag:`, e essas seis são escritas por caminhos que nenhuma das duas varreduras
alcançava do mesmo jeito.

### O trabalho, na ordem

1. ✅ **Aliases acrescentados — 13, não 6** (2026-08-30). O escopo cresceu ao medir: em vez de tratar
   só o que a auditoria observara, derivei do **CENSO DE PRODUTORES** (`ctx_writes` do
   channel-gateway + escritas do bridge + `tag:` dos YAML) e casei contra as canônicas do mapa. Deu
   **12 casamentos 1:1** mais **1 ambiguidade**, resolvida por medição: `session.dialog_form_id`
   tinha duas canônicas candidatas (`workflow` × `hook`), e `webhook.py:2208` o escreve no caminho de
   **collect engage** — o do hook já tinha alias próprio (`hook.dialog_form_id`).
   Sete dos treze **ainda não tinham sido observados**: seriam descobertos um a um, rodada a rodada.
   ⚠️ **O censo SOBRE-COLETA e não é fonte de verdade.** Ele acusou 50 "sem canônica", e boa parte é
   ruído do meu próprio regex: `session.signals` e `journey.merges` são **tópicos Kafka**,
   `session.closed` é nome de evento em 17 arquivos, `session.a`/`b`/`foo`/`x` são fixtures. O censo
   gera HIPÓTESE; quem dá FATO é a auditoria, que lê chaves de hash reais.
   Mapa: **75 canônicas / 40 → 53 aliases**, oráculo com as quatro listas vazias, gate
   `probe_context_map_audit.sh` em 16 verdes.
2. ~~Decidir as SETE restantes~~ — **NÃO DECIDIR.** A **D9** (proposta pelo dono em 2026-08-30, ver
   o ADR) muda o modelo: o ContextStore vira **cadastro**, e sob ele estes sete não existem como
   pergunta. O que os tornava pergunta era a allowlist ser uma enumeração de NOMES que o tenant
   autora — e é justamente isso que a emenda supersede.
3. **Continuar acumulando tráfego** até N rodadas seguidas não acrescentarem nada ao `unknown` — a
   forma é *loop-until-dry*, não "rodei uma vez". Quatro fluxos cobrem uma fração dos 44 skills.
4. Só então a V4.

### ⚠️ Ler a auditoria depois de mexer no mapa exige DOIS cuidados, não um

Medido ao vivo, e o segundo me pegou:

- **O contador não expira.** Entradas gravadas sob um mapa anterior ficam para sempre, e ler a lista
  sem cortar a série mistura classificações de dois mapas num número só — o defeito que a D14 recusou
  com a época do SLA. **Corte a série** (`DEL {t}:ctx_audit:*`) depois de qualquer mudança de mapa,
  preservando o dump antes.
- **O mapa tem cache de 60 s no mcp-server.** Cortei a série e rodei tráfego imediatamente: o fluxo
  do limite executou com o mapa ANTIGO em cache e gravou `session.dialog_form_id` e
  `session.decisions` como `unknown` — campos que **já estavam declarados**. Uma leitura ingênua
  teria concluído que o alias não funcionou. Provado ao contrário rodando o mesmo fluxo com o cache
  quente: os dois passaram a `alias`, resolvendo para `session.workflow.*`.

**Regra: cortar a série E esperar o cache virar. Cortar sozinho produz uma lista que acusa campo
declarado** — e a V4 negaria exatamente esse campo.

### Estado medido depois do passo 1 (série limpa, cache quente)

`alias 18 · canonical 4 · unknown 7 · dynamic 0 · overflow 0` sobre os mesmos quatro fluxos.

⚠️ `overflow = 0` hoje — o balde tem teto, e o aviso do ADR (*"`unknown` só vale enquanto
`overflow == 0`"*) segue válido. Reconferir a cada medição.

⚠️ **A auditoria observa a LEITURA.** Campo escrito por um dos 12 `HSET` diretos e nunca lido é
invisível a ela. Para W (escrita) e P (persistência) o número não é suficiente — é para R-humano.

## Achados da V3 do arco ALLOWLIST (2026-08-29) — fora do escopo da fase

### ✅ `session.numero_atual` — DECIDIDO em 2026-08-30 (e ao contrário do previsto aqui)

> O campo é o **objeto do atendimento**, não dado de cadastro: segue em CLARO, agora
> **declarado** (`tipo: "linha_em_servico"`) em vez de por omissão. O telefone de CADASTRO
> (`session.cliente.telefone`) continua protegido. Raciocínio — e a evidência minha que se
> mostrou fraca (`_LEGACY_PREVIEW_SPEC` serve ao CLIENTE, não ao operador) — na § *"SETE decisões do dono — sessão de 2026-08-30"*, #2.
> O texto abaixo fica como registro do que foi medido.

Medido e **reproduzido no caminho real** (`POST /internal/context-snapshot` devolveu
`"value":"11987654321"` ao lado de `caller.cpf` mascarado como `***00`).

- **Produtor:** `agente_portabilidade_intake_v1.yaml:445` (`context_set`, `confidence: 1.0`) — é a
  linha atual do cliente no fluxo de portabilidade.
- **Por que escapa:** não casa **nenhuma** das 23 regras do `tenant_demo`. `*.telefone` exige o
  sufixo `.telefone`; `caller.*`/`account.*` não alcançam `session.*`; e `session.*` **não pode** ter
  catch-all (derrubaria a tela de aprovação — o seed avisa por escrito). Cai no
  `default_unmatched_operator: "plain"`.
- **Exposição × dano:** exposição REAL; **dano medido hoje zero** — nenhum hash vivo do ContextStore
  contém o campo (a varredura achou 5 campos distintos em 11 hashes, todos do routing-engine).
- **Conserto durável:** a V4. O mapa da V3 já o declara `session.portabilidade.numero_atual` com
  `tipo: phone`.
- **Paliativo (decisão de PRODUTO, não fiz):** uma regra exata
  `{"pattern": "session.numero_atual", "role": "operator", "type": "last_4"}`. Não apliquei porque
  mascarar a linha em portabilidade pode atrapalhar o próprio atendimento que a coleta serve — quem
  decide isso é quem conhece o fluxo, não o gate.

### 🟢 `masking.context_rules` do `__global__` atrás do seed — DECIDIDO em 2026-08-30

> **Não se declara `cpf_titular`** (decisão do dono: declara-se o genérico; o discriminador vai
> para o segmento de DOMÍNIO). E o caso concreto dissolveu — medido: `session.cpf_titular` **não
> tem produtor**, logo o `overwrite_would_drop = 1` conta uma regra MORTA e a divergência é *uma
> política em dois momentos*, não duas políticas. Falta só provar o fóssil fora do código antes do
> `--overwrite`. Ver a § *"SETE decisões do dono — sessão de 2026-08-30"*, #3.

Medido: `__global__` tem **14 regras**, `tenant_demo` tem **23**, o `seed.py` tem **23**. Os globs de
sufixo (`*.cpf`, `*.numero_cartao`, …) — que são o conserto de 2026-08-26 para o PII que cai em
`session.*` — existem **só no override do tenant**. Um tenant novo herda a política pré-conserto.

Dano hoje **zero** (só existe um tenant real). Mas **não basta reaplicar o seed**: a divergência é
nos DOIS sentidos — `session.cpf_titular` existe vivo e não no seed; `session.vencimento_cartao`
existe no seed e não vivo. Um `--overwrite` cego **regride** o `cpf_titular`.

> **Atualizado 2026-08-29 — a D7 foi entregue, e esta divergência agora APARECE.** Todo
> `plughub-config-seed` a nomeia nas duas direções e avisa quantos itens um `--overwrite`
> descartaria (hoje: 1). Medido na ocasião: **77 keys, 76 iguais, esta é a única divergente**.
> O que sobra aqui **não é instrumento, é DECISÃO de política** — e ela tem um caminho barato:
> acrescentar `session.cpf_titular` à declaração do `seed.py` faz `overwrite_would_drop` ir a zero,
> e aí a reaplicação passa a ser puro acréscimo. Não fiz porque promover uma regra de mascaramento
> a **default de plataforma** é escolha de produto (ela pode ser deliberadamente só do
> `tenant_demo`), e o instrumento que acabou de ser construído existe justamente para essa escolha
> ser tomada com o número na mão, não deduzida.

### ✅ `session.vencimento_cartao` — RESOLVIDO em 2026-08-30 (tipo `card_expiry`)

> A lacuna era do CATÁLOGO e foi fechada lá: `card_expiry` (`operator: last_2`, `lgpd:
> financeiro`, `declared_only`), declarando a política que já valia. Só então o campo entrou no
> mapa, como `session.cartao.vencimento` — **a ordem é o critério**. Ver a § *"SETE decisões do dono — sessão de 2026-08-30"*, #1.

Campo escrito e mascarado por regra (`last_2`), deixado **fora do mapa de propósito**: nenhum tipo do
catálogo serve. `credit_card` é `last_4`, e num `MM/AA` isso mostra quase tudo — o mesmo argumento
pelo qual a T6 recusou reusar `credit_card` para o CVV.

Declarar um tipo aproximado escreveria no mapa uma política que ninguém decidiu, e a V4 a aplicaria.
Fechar a lacuna é decisão do CATÁLOGO (um tipo com `by_role.operator: "last_2"` e
`lgpd: "financeiro"`), e **é pré-requisito da V4** — enquanto faltar, o campo conta como
não-declarado e a auditoria (corretamente) não autoriza a inversão.

### 🟡 Quatro regras de masking apontam para campos que código nenhum escreve

`caller.cnpj`, `account.numero_contrato`, `account.valor_fatura`, `account.limite_credito` não
aparecem em nenhuma das duas varreduras (leitura `@ctx.` nem escrita `tag:`). São regras sem
produtor — inócuas, mas engordam a lista que a tela de Masking exibe e sugerem cobertura que não
existe. Antes de remover, conferir se algum `delegate.context` de tenant as alimenta (a varredura só
alcança o código, não o conteúdo autorado).

### 🟡 `DEFAULT_OPERATOR_NAMESPACES` ainda contém `service`, que não concede nada

Medido em 2026-08-29: `service.*` tem **zero produtores** em `packages/` e zero ocorrências no
ContextStore vivo, mas está no default da plataforma (`server.ts`,
`DEFAULT_OPERATOR_NAMESPACES = ["service", "session"]`). O efeito é **inerte** — concede visibilidade
a um namespace onde nada é escrito —, então não há urgência e nada vaza.

**Não removi de propósito:** é um default de VISIBILIDADE, e mexer nele é mudança de comportamento
de segurança que merece decisão explícita, não um efeito colateral de um arco de UI. O docstring do
schema já registra a medição, e o seletor da D6 impede que alguém o *escolha* daqui em diante.

Junto: `docs/guias/context-store-taxonomy.md` declara SETE namespaces, dois dos quais
(`service.*` — com cinco campos nominais — e `history.*`) não existem. O ADR já marca o guia como
desatualizado; agora está medido. Se o guia for revisado, esses dois são o começo.

### 🟡 `config-api` cacheia em processo — re-semear não basta

`plughub-config-seed --only <ns>.<key> --overwrite` grava no Postgres, mas
`GET /config/<ns>?tenant_id=` continua servindo o valor antigo até o serviço **reiniciar**. Custou um
diagnóstico inteiro no rumo errado nesta sessão (a mutação "não aplicava", quando na verdade não era
servida). Ou o seed passa a invalidar, ou o procedimento de reaplicação inclui o restart — hoje não
inclui, e nada avisa.

### 🟡 `$?` através de `wsl.exe -- bash -lc '…'` a partir do Git Bash devolve status errado

O `$?` é expandido cedo (pelo Git Bash), então um gate que imprime `FALHA (2)` "sai 0". Não é
defeito do gate; é o instrumento. Aferir sempre com `&& / ||`, nunca com `$?`, nessa combinação.
Vale para qualquer probe rodado a partir do Windows.


## 🟡 ABAC TOTAL — escopo FECHADO pelo dono (2026-08-27), passo 1 entregue

Decisão: **eliminar a permissão por papel; tudo sob ABAC.** As seis perguntas foram fechadas:

| # | decisão do dono |
|---|---|
| 1 | supervisor administra **Access, Groups, Calendars, Schedules** |
| 2 | supervisor **não** alcança Fluxo |
| 3 | supervisor **mantém** Monitor (é o trabalho dele; o grant é escopado por pool) |
| 4 | supervisor **não** vê Faturamento — o `billing.visualizar` dele era grant de teste, sai |
| 5 | **tudo módulo independente**, e **papel vira preset de seed**, não portão. Default alterável só pelo seed, não pela tela |
| 6 | `module_config` vazio = **não pode nada** (mesma lógica de `accessible_pools`); a porta larga é o claim `unrestricted`, declarado |

A decisão 5 é a que muda a economia do arco: com o usuário **nascendo com um preset**, granularidade
deixa de custar trabalho a quem concede — o que era o único argumento contra o split por persona.
Duas propriedades vêm junto, e são desejadas: **papel vira certidão de nascimento, não política viva**
(editar o template não muda quem já existe — é o mesmo *seed-if-absent* do resto da casa), e o
template é **provisionamento**, então mantê-lo fora da tela não fere *"todo campo de config é
UI-editável"*, que vale para o `module_config` de cada pessoa.

### Ordem de execução — inegociável, e a matriz mede a prontidão entre os passos

| # | passo | estado |
|---|---|---|
| 1 | Split `config.users` → `users` + `permissions` | ✅ **2026-08-27** (ver `CHANGELOG.md`) |
| 2 | Campos `config.calendars` / `config.dialog_forms` / `config.dashboards`; `nav.channels` → `config.channels` | ✅ **2026-08-27** (ver `CHANGELOG.md`) |
| 3 | **Papel → preset de seed**, aplicado em `create_user` | ✅ **2026-08-27** (ver `CHANGELOG.md`) |
| 4 | Grants: supervisor recebe os campos das decisões 1 e 3; saiu `billing.visualizar` | ✅ **2026-08-27** (ver `CHANGELOG.md`) |
| 5 | Portão único grant-first + regra em `nav.billing` | ✅ **2026-08-27** — e **absorveu 6 e 7** (ver abaixo) |
| 6 | Caem os **dois** bypasses juntos; `unrestricted` vira a única porta larga | ✅ **2026-08-27** (no mesmo commit do 5) |
| 7 | Apagar os 7 `roles:` do `Sidebar.tsx` | ✅ **2026-08-27** (no mesmo commit do 5) |
| 8 | Cauda de papel no **backend**: 4 sítios | ✅ **2026-08-27** — arco 1–8 COMPLETO |

⚠️ **O passo 6 depende do 3, e isto foi medido, não suposto.** `create_user`
(`packages/auth-api/.../db.py:249`) grava `roles`, `accessible_pools`, `unrestricted` e
`max_concurrent_sessions` — **e não grava `module_config`**. Ou seja, *todo usuário criado pela tela
nasce com config vazio*, dentro da degradação. Inverter o 6 antes do 3 faria cada usuário novo
**nascer cego**, e quem o criou leria isso como "a tela de Acesso quebrou".

> **Por que 5, 6 e 7 saíram juntos** *(fechado em 2026-08-27)*: eram separáveis no plano e não
> na prática. Os `roles:` do passo 7 decidiam **antes** da ABAC, então enquanto existissem os
> grants do passo 4 ficavam **inertes** — medido: 11 grants do supervisor que ele não via. E o
> passo 6 caiu junto porque o ramo não-estrito de `passesAbacRule` foi removido **inteiro** em
> vez de cada regra ganhar `strict: true`: com a flag, a próxima entrada de menu escrita sem ela
> reabriria os dois bypasses em silêncio.

⚠️ **Não remover bypass antes de conceder** — mas **o alvo NÃO é zero**. *(Corrigido em
2026-08-27: esta linha, e a mensagem do próprio `q_nav_gates_matrix.py`, diziam "tem de ir a 0".
Medido: depois de conceder tudo o que o dono decidiu, o supervisor foi de 17 para **8**, e os 8
são exatamente os que ele **não** deve alcançar. Exigir zero teria pedido para conceder o que se
decidiu negar.)*

O critério certo é **item a item**: para cada um que sobra no placar, é acesso a dar (conceda
antes do `strict`) ou acesso a tirar (o `strict` é o que o remove, e a remoção é o efeito
pretendido)? Os 8 do supervisor hoje — `nav.flow.editor` e `nav.flow.deploy` (decisão 2) mais
`nav.dashboards`, `nav.resources`, `nav.platform`, `nav.channels`, `nav.masking` e
`nav.dialogForms` (decisão 1) — são **todos a tirar**.

### 🔴 O editor de Fluxo repete a divergência do `nav.channels` (achado do passo 4)

| lado | campo exigido |
|---|---|
| menu (`nav.flow.editor`, `nav.flow.deploy`) | `skill_flows.operacao` |
| backend (`PUT /v1/skills/:id`, `require-resource-write.ts:62`) | `config.resources` |

Quem receber `skill_flows.operacao` vê o editor e **não salva**; quem tiver `config.resources`
salva e **não vê** o item. Mesma família do defeito que o passo 2 fechou, em outro serviço — e o
catálogo ainda tem `skill_flows.editar` (*"Criar e editar skill flows"*), um campo que existe e
não é o que o backend consulta.

**Qual lado está certo é decisão**, não conserto óbvio: `config.resources` significa "CRUD de
pools/skills/instâncias" e `skill_flows.editar` significa "autorar fluxos" — capacidades
diferentes que hoje colidem no mesmo endpoint.

⚠️ O `probe_nav_backend_field_agreement.sh` **não pega isto**: cobre só os namespaces do
config-api, e `resources` está declarado em `NAO_CONFIG_API`. Estender a cobertura ao
agent-registry é parte do encaminhamento.

**Medido junto:** o agent-registry **não gateia leitura** — `GET /v1/skills` e `GET /v1/pools`
respondem **200 anônimo**. Escritas são gateadas (401 sem token, 403 sem o campo). É divulgação
de config, não de dado pessoal, mas é superfície.

### ✅ Presets de `developer` e `business` — DECIDIDO em 2026-08-30 (congelar o mínimo)

> Medido ao vivo: **`developer` tem 1 portador — o próprio `admin@`** — e **`business` tem 0**.
> Não há baseline porque **não há população**: o preset de `developer` é inerte por construção e
> o de `business` se aplica a ninguém. Congela-se o mínimo já declarado; o gatilho para desenhar
> de verdade é o primeiro portador REAL. Ver a § *"SETE decisões do dono — sessão de 2026-08-30"*, #4.

`admin`, `supervisor` e `operator` tiveram os presets **levantados** do `seed_auth.py` — é o que
eles têm hoje, movido para a declaração. `developer` e `business` **não têm baseline medível**:
o que alcançam hoje vem da degradação de config vazio, que é o que o passo 6 remove. Copiar *"o
que eles veem hoje"* seria copiar o bypass para dentro da declaração.

Declarados minimamente em `infra/modules.yaml`:

| papel | preset atual |
|---|---|
| `developer` | `skill_flows.{operacao,editar,visualizar}` + `contacts.visualizar` |
| `business` | `contacts.visualizar`, `evaluation.report`, `billing.visualizar`, `skill_flows.visualizar`, `workflows.visualizar`, `campaigns.visualizar` |

**A decisão fica melhor depois do passo 5**, quando a matriz mostrar exatamente o que cada preset
faz aparecer no menu.

### ✅ Subsistema de templates de permissão MORTO — DECIDIDO em 2026-08-30 (remover)

> Confirmado ao vivo (0 linhas nas duas tabelas) e o grafo é pior que *"ninguém lê"*: é **laço
> morto fechado** — `user_can()` sem nenhum chamador, `get_accessible_pools_for_module()` só em
> teste, `apply_template()` escrevendo numa tabela que ninguém consulta. **São os testes que o
> fazem parecer vivo.** Remover as duas tabelas + o `apply` + as duas funções; o template segue
> como preset de `module_config`. Ver a § *"SETE decisões do dono — sessão de 2026-08-30"*, #5.

Medido: `auth.permission_templates` e `auth.platform_permissions` têm **0 linhas**, e **nenhum
código lê `platform_permissions` para decidir** — a única menção fora do próprio CRUD é um
docstring. O `POST /templates/{id}/apply` materializa permissões numa tabela que ninguém
consulta, enquanto a decisão real vive em `auth.users.module_config`.

Agrava: a **UI usa o mesmo objeto com outra semântica** — o seletor de template pré-preenche o
formulário a partir de `template.module_config` (cópia no cliente), sem passar pelo `apply`. Ou
seja, um objeto com duas leituras e uma delas escreve em tabela morta.

Encaminhamento (não decidido): remover as duas tabelas e o `apply`, mantendo o template como
preset de `module_config` — que é o uso que a UI já faz e o único com consumidor.

### ✅ Serviços de config SEM portão — FECHADO (2026-08-27)

`calendar-api` e `dialog-api` aceitavam **escrita não autenticada** em config de produção
(`POST /v1/calendars` sem credencial → **201**; criar **e publicar** DialogForm anônimo → **200**
nos dois). Fechado: portão **dual** (admin-token de sistema **OU** Bearer + ABAC) sobre as 12
rotas de escrita do calendar e as 3 do dialog. Ver `CHANGELOG.md` e
[`docs/arcos/arc7-auth.md`](docs/arcos/arc7-auth.md) § Portão dos serviços de config.

**A decisão que travava era ONDE mora o verificador**, e a medição a resolveu: não eram cinco
implementações de *"verificar JWT + ler `module_config`"* — eram **seis** (faltava a
`pricing-api`), e elas **já haviam divergido**, em seis pontos. Isso tirou "extrair" do campo do
gosto. O dono escolheu o pacote canônico **só para os dois novos**, sem migrar os seis.

**LEITURA FICOU ABERTA nos dois, e não é lacuna** — `/v1/engine/*` (workflow-api,
scheduler-api, mailing-api) e os `GET` do dialog (`form_get` do mcp-server, survey web) são
chamadores de runtime sem credencial. Um portão que fechasse a leitura passaria no teste de
segurança e quebraria o produto em silêncio; por isso as três testemunhas S5/S9/S10 do gate.

Gates: `infra/test/probe_config_service_write_gate.sh` (10 cenários, com contraprova positiva
**e** negativa) e `infra/test/probe_authz_single_verifier.sh`.

### ✅ Migrar os SEIS verificadores para `packages/py-authz` — CONCLUÍDO (2026-08-28)

> **Arco completo, passos 0–6.** Linha de base do `probe_authz_single_verifier.sh`:
> **7 arquivos em 6 serviços → 1**, e esse 1 é o **emissor** (`auth-api/jwt_utils.py`),
> que fica por decisão (D4). História em `CHANGELOG.md` de 2026-08-28. **A linha de base
> não deve ir a zero** — se um dia for, alguém migrou o emissor sem decidir isso.
>
> Fica aqui só o que continua ABERTO e nasceu do arco:

**1. O ramo 3 do `scope_id` é HERDADO, não decidido** *(passo 6)*. Em
`plughub_authz.abac_can`, `scope` não-vazio + `scope_id is None` → **passa**. Veio da
`evaluation-api`, onde o `pool_id` sai de `campaign.pool_id` e `None` significa *"a
campanha não é escopada a pool"*, não *"esqueci de passar"* — sob essa leitura, passar é
correto. A leitura oposta (um usuário escopado só toca recurso escopado) é defensável.
**Decidir junto do passo 3 de `accessible_pools`**: é o mesmo tipo de pergunta, e o
inventário que aquele passo exige responde os dois.

**2. Eixo VIZINHO medido e LIMPO — não virou gate** *(passo 4)*. O C1 conta quem lê
`module_config` **e** decodifica JWT; ao lado existe *"quem decodifica um JWT, para
qualquer fim"*. A `analytics-api` tem duas casas nesse eixo — `pool_auth.py` (escopo) e
`auth.py` (identidade, `admin_jwt_secret`) — que **ficam locais**: precisam distinguir
*expirado × inválido* na mensagem do 401, e `verify_user_jwt` colapsa os dois em `None`.
Medidos os 9 decodificadores do repo: todos com `algorithms=["HS256"]`; o único
`verify_signature: False` é a espiada documentada do `webchat.py:439`. **Se aparecer o
segundo `verify_signature: False`, é a hora de construir o C5** — hoje ele teria lista de
exceção de um item, e exceção que envelhece é o defeito que ela deveria pegar.

**3. Armadilha latente no C1** *(passo 4)*: o filtro `^[^#]*` exclui comentário `#`, **não
docstring**. Um arquivo migrado que descreva em prosa o `decode` que perdeu volta a casar;
hoje não vira falso positivo só porque o primeiro filtro exige `module_config`, que sai
junto. Registrado no próprio probe. Trocar `grep` por AST quando o falso positivo aparecer,
não antes.

**4. `probe_resume_approver_authz.sh` é VERDE PARCIAL por desenho** — a metade
comportamental (S2/S3) precisa de uma aprovação SUSPENSA e não roda sozinha. A suíte nova
do passo 3 (`test_approver_principal_authz.py`) cobre a mesma metade em unit, mas o
caminho HTTP real continua sem gate re-executável.

**5. Lição de método do arco, para o próximo que mover uma fronteira de autorização:**
em **cinco dos sete passos** os testes que cercavam a fronteira estavam para trás, e em
três deles quem revelou foi a **bateria de mutação**, não a suíte. Ao mexer num portão,
o primeiro passo é medir o que o cerca — e o vermelho de um controle POSITIVO parece
proteção, que é justamente o que se queria ver.

### 🟡 `/dashboard/metrics` devolve agregado TENANT-WIDE a chamador escopado (2026-08-28)

Declarado, não silencioso: a resposta carrega `pool_scope_applied: false` e o serviço loga quando
quem lê tem `accessible_pools` restrito. Não foi escopado no mesmo passo porque `get_metrics_24h`
agrega quatro blocos e **`usage_events` não tem coluna de pool**: filtrar só os que dão (sessions,
segments) publicaria um painel meio-escopado — sessões de um domínio ao lado de uso do tenant
inteiro, números que não fecham entre si e nada dizendo por quê. Escopar de verdade exige decidir o
que fazer com `usage`/`sentiment`: omitir o bloco (a tela perde a faixa) ou marcá-lo por bloco.
**Enquanto não for decidido, a limitação é lida na resposta, não adivinhada.**

### 🟡 Cobertura declarada da varredura de credencial no browser (2026-08-28)

`infra/test/_ui_raw_analytics_calls.py` só decide `fetch` com URL **literal**. As **15** chamadas
`fetch(url)` (URL montada noutro lugar) saem como `UNDECIDABLE` e o probe imprime a contagem — foi
exatamente por esse buraco que `dashboard/CardRenderer.tsx` escapou e a Home parou com o gate verde.
Fechar de vez pede a regra inversa: **proibir `fetch` cru no platform-ui**, com allowlist declarada
dos wrappers de credencial (`apiFetch`, `api/auth.ts`, `modules/*/api.ts`) — manifesto, não glob,
pela mesma razão do `run_gates.sh`. É lint, não runtime; cabe num passo próprio.

### 🟡 101 classes `*-gray-N` INERTES no platform-ui (medido 2026-08-28)

`tailwind.config.ts:23` redefine o token `gray` como cor **CHAPADA** (`#6B7280`), o que
substitui a escala inteira do Tailwind. Medição contra o bundle servido:

```
utilitárias (bg|text|border)-gray-N no CSS construído:  NENHUMA
usos no fonte (packages/platform-ui/src):              101
```

O próprio config avisa (*"colide com a escala built-in, prefira 'muted'"*) — e 101 lugares
foram escritos assim mesmo. **Promessa sem mecanismo**, a mesma família do docstring de
`channel-gateway/auth.py` e do DDL de `participation_intervals`.

**O modo de falha não é uniforme, e é isso que o torna caro.** Quase todas as 101 só perdem
um tom de cinza e ninguém nota. Mas onde a classe carrega o FUNDO de um elemento com
`text-white`, o resultado é branco no branco — **elemento invisível com a área de clique
intacta**. Foi o que aconteceu com o *Save draft* do editor de DialogForms: o operador
clicou no que parecia espaço vazio e recebeu um erro de gravação. Corrigido pontualmente
(`bg-dark`).

**A categoria perigosa foi varrida e está fechada** (2026-08-28): das **90** classes que
restam, **22** carregam fundo e **NENHUMA** aparece junto de `text-white` — o único caso
era o *Save draft*. O resto perde tom de cinza, não visibilidade. Registrar isso importa
porque a próxima leitura desta entrada não precisa refazer a medição para saber que não há
outro botão invisível esperando.

**O que impede a volta não é a varredura, é o mecanismo:** um check de build (ou lint) que
recuse `*-gray-N`, já que nenhuma delas funciona. Sem ele, a próxima linha escrita com
`bg-gray-700` entra igual. A varredura em si é decisão por call site (cada um precisa de
escolha de token: `muted`, `muted-light`, `surface-alt`, `border`, `dark`).

### ✅ `dialog-api` não tem rota DELETE (achado colateral, 2026-08-27) — RESOLVIDO 2026-08-28

Rotas medidas: `POST ""`, `GET ""`, `GET /{id}`, `PUT /{id}`, `POST /{id}/publish` — e nada
mais. Consequência prática: **todo form criado é permanente**, inclusive os que instrumentos
criam. O `probe_config_service_write_gate.sh` contorna apagando pelo Postgres, e diz por que no
comentário; um probe que suja o ambiente que mede acaba medindo a própria sujeira. Não é
urgente, mas enquanto não existir, qualquer script que crie form tem de limpar pelo banco.

**As sete decisões estão fechadas** em [`docs/adr/adr-dialog-form-deletion.md`](docs/adr/adr-dialog-form-deletion.md)
(arquivamento reversível; purga real só do nunca-publicado). O que a medição desfez, e que vale
para além desta rota: **soft-delete responde "dá para recuperar?" (armazenamento) e NÃO responde
"o contato em andamento cai?" (leitura)** — são dois eixos, e escolher soft-delete mantendo `404`
na resolução quebraria os **seis** leitores exatamente como um hard delete, só que com um backup
que ninguém consulta. Daí D1: o catálogo fecha, `GET /{form_id}` continua servindo (com
`deleted_at`). Dois dos seis leitores leem no **fim** do diálogo (`survey_record`,
`segment_outcome_record`) e um lê **história encerrada** (`WebhookSegmentDetail`), cujo dano não
teria janela. Achado de tabela: `seed_dialog.published_version()` trata `404` como AUSENTE, então
com a leitura fechada **todo boot ressuscitaria o form apagado**.

Fases F1 (dialog-api) → F2 (UI) → F3 (`survey_link_create` recusa) → F4 (seed) → F5 (gate
`probe_dialog_form_delete.sh`, cuja testemunha negativa é `GET ?status=published` de arquivado
devolver **200 com `deleted_at`** — um probe que só cheque "sumiu da lista" fica verde num hard
delete). **Todas entregues em 2026-08-28; ver `CHANGELOG.md`.**

**Resíduo aceito, não dívida:** a limpeza do `probe_config_service_write_gate.sh` continua pelo
Postgres — ela publica o form que cria, e publicado só arquiva. Se incomodar, a saída é o probe
parar de publicar (nunca-publicado é purgado pela própria rota), não reabrir a decisão.

### 🟡 Layout pessoal de dashboard exige permissão de plataforma (achado do passo 2)

O namespace `dashboards` do config-api mistura **template do tenant** (`template:*`,
`role_catalog:*`) com **preferência pessoal** (`layout:{tenant}:{user}`). Como o gate é por
namespace, salvar o próprio Home pede permissão de administração. Medido em 2026-08-27:

```
operator     PUT dashboards/<layout>  ->  403
supervisor   PUT dashboards/<layout>  ->  403
admin        PUT dashboards/<layout>  ->  passa
```

**Renomear o campo não conserta** — `config.dashboards` só muda o nome do campo errado. É a
regra de escopo outra vez: preferência pessoal não é config de plataforma. O `user_id` está
**dentro da chave** (`layout:{tenantId}:{userId}`), e o config-api já lê o `sub` do JWT, então a
saída é uma **regra de posse** (a chave é minha ⇒ posso escrever), não um campo novo.

### ✅ Os 2 sítios de papel que sobravam — RESPONDIDOS pelo dono (2026-08-27)

| pergunta | resposta |
|---|---|
| O supervisor deve decidir aprovações? | **Sim, as do módulo de Quality** — que são REST próprio (`contestation_router`, gate `evaluation.revisar`) e não passam pelo `/resume`. Ele já as tem; **não** recebeu `approvals.decide` |
| Os 22 pools do admin são deliberados? | **Não** — resíduo de teste do demo. *"Na prática todos os pools são criados dinamicamente."* `admin@` virou `accessible_pools: []` + `unrestricted: true`, declarado |

Implementado; ver `CHANGELOG.md`. Fica registrado o que a segunda resposta implica: **o seed
declara escopo apenas para quem o declara na entrada** — o `supervisor@` tem `accessible_pools`
montado à mão como caso de teste, e um default aplicado a todos o apagaria em silêncio.

### Cauda de papel no backend (passo 8) — medida ⚠️ SUBCONTADA (corrigido 2026-08-30)

> **São 4 aqui e mais 8 em `mcp-server-plughub`** (`src/server.ts:1441, 1750, 1841, 2200, 2674,
> 2756, 2827, 2858`) — um serviço inteiro fora da varredura, com o arco marcado *"1–8 COMPLETO"*.
> A direção é segura (menu mais estrito que backend), mas alguém vai ler o ✅ como *"papel saiu do
> backend"*. Regra da casa outra vez: **um censo desenhado para um eixo não prova nada sobre o
> eixo vizinho** — aqui, o MESMO eixo com um serviço não varrido.

Eliminar o papel do menu **não** o elimina do produto. Restam decisões por papel em:

- `packages/channel-gateway/.../main.py:1557` — `is_elevated = ("admin" in roles) or ("supervisor" in roles)`
- `packages/evaluation-api/.../router.py:247` e `:462` — `if "admin" in roles`
- `auth-api` — `resolve_supervisor_scope` (admin → sem restrição)

A direção é segura (menu mais estrito que backend), mas o arco não termina no `Sidebar.tsx`.

### Estado da matriz (regenerar com `python3 infra/test/q_nav_gates_matrix.py`)

- 37 itens com regra ABAC, **26 não-estritos** (sujeitos ao bypass de papel)
- **7 portões de papel** no fonte: 5 cabeçalhos de grupo + `nav.home` + o item `nav.billing`
  *(o §1 do script conta 8 — um deles está dentro de um comentário; corrigir junto com o passo 5)*
- placar do bypass: admin **0** · operator **0** · probe **0** · **supervisor 17**
- `probe@plughub.local` aparece `legado` em **20 itens** — é o segundo bypass, o silencioso

### Encaminhamento das 7 entradas sem regra (passo 5)

| entrada | encaminhamento |
|---|---|
| `nav.monitor`, `nav.flow`, `nav.quality`, `nav.analise`, `nav.config` | cabeçalhos: *grupo visível ⟺ ao menos um filho visível* — derivação, não campo novo |
| `nav.home` | sem portão |
| `nav.billing` | `billing.visualizar` — o módulo já existe |

### Princípio que saiu do passo 1, e vale para os próximos

**Módulo é a unidade que você concede ou nega INTEIRA a uma pessoa; campo é para quando o trabalho da
mesma pessoa se divide.** O corolário medido: um campo cujo rótulo tem **"e"** (*"Gestão de usuários
**e** permissões"*) provavelmente são dois fatos — e se um deles concede capacidade, o campo é uma
chave-mestra até que se prove o contrário.

## Gates — o que o runner ainda NÃO cobre (2026-08-27)

`infra/test/run_gates.sh` cobre **21 gates AUTO**. Quatro ficam de fora por natureza, e a lista existe
para que a cobertura não pareça maior do que é:

| gate | requisito |
|---|---|
| `gate_pause_capacity.sh` | agente humano LOGADO no Console (a instância nasce no login WS, `registerHumanAgent`) — forjar a chave em Redis testaria a forja, não o produto |
| `gate_queue_segment_not_born_without_flow.sh` | `T0` colhido **antes** do fluxo |
| `gate_sentiment_engine_half.sh` | `T0` colhido antes do contato, e um contato que ENFILEIRE |
| `probe_release_reclaim_race.sh` | `<pool_id> <instance_id>` de uma instância viva |

**Como fechar, se valer a pena:** os dois de `T0` viram AUTO se o próprio gate produzir o fluxo que
mede (hoje ele só o observa). Os outros dois exigem um ator — humano logado, instância viva — e o
caminho honesto é um harness que os produza, não um forjador de chave em Redis.

⚠️ **Não transformar em AUTO baixando a barra**: um gate que roda sempre e nunca exerce o caminho é a
família *"teste que não pode reprovar"*, e sai pior do que a lista acima.

**Fora do manifesto por decisão:** os outros ~190 scripts de `infra/test/` são seeds, medições (`q_*`)
e smokes de arco. Entram se e quando tiverem veredicto falseável — o critério é COMPORTAMENTO, não
prefixo.

**Ainda não coberto por runner nenhum:** as suítes de pytest (analytics 637 · auth ~~64~~ **63**, medido 2026-08-30 · channel-gateway
676 · evaluation-api 211), das quais 4 testes estão vermelhos e registrados em seção própria. E o
`auth-api` não tem nem pytest nem os testes na imagem.


## Ambientes componíveis — PISO × PACOTES DE CONTEÚDO *(direção, decidida 2026-08-27)*

**O modelo não é "demo × dev".** É: **partindo de vazio, um ambiente é uma composição de pacotes de
conteúdo sobre um piso comum.** Demo é um pacote; CI, carga e o que aparecer são outros. Decisão do
dono; abaixo a medição que a sustenta e o que ela exige.

### Os dois eixos

| eixo | o que é | leva `profiles:`? |
|---|---|---|
| **PISO (bootstrap)** | o que a plataforma precisa para funcionar, ponto: `config-seed` (defaults globais), `kafka-init`, `minio-init`, o admin do boot do auth-api, e as DialogForms **de plataforma** | **nunca** |
| **PACOTE (conteúdo)** | material com escopo de tenant: registry de pools/skills, usuários, formulários, campanhas, recursos de pricing | sim — aditivos e múltiplos |

Sem essa separação, cada ambiente novo re-litiga o que é o piso. É exatamente onde o `dialog-seed`
está hoje: um job só, misturando os dois.

### Classificação medida dos jobs de seed

| serviço | o que escreve | escopo | veredicto |
|---|---|---|---|
| `config-seed` | `platform_config`: sentiment, routing, **session TTLs**, audit_policy/masking, ai_gateway, evaluation, dashboards… | **global, sem `TENANT_ID`** — o docstring diz *"tenant-specific overrides are never set by the seed"* | **PISO** |
| `pricing-seed` | recursos contratados `ai_agent×300 + human_agent×10` | `tenant_demo` | PACOTE |
| `auth-seed` | usuários `supervisor@` e `operator@` + `module_config` (o admin já vem do boot do auth-api) | `tenant_demo` | PACOTE |
| `eval-seed` | formulário "SAC Padrão" + campanha "Demo SAC" | `tenant_demo` | PACOTE |
| `dialog-seed` | 10 DialogForms de `infra/dialog/*.json` | `tenant_demo` | **PACOTE** *(era "MISTO" — corrigido, ver abaixo)* |

`kafka-init` e `minio-init` são piso. `plughub-demo` na linha 235 **não é serviço**: é o `name:` do
projeto — ou seja, o `COMPOSE_PROJECT_NAME` já está fixado ali, e trocá-lo por ambiente é uma linha.

### ⚠️ `dialog-seed` NÃO é misto — a tabela anterior estava errada (corrigido 2026-08-27)

A versão anterior desta seção classificava metade dos forms como **piso**, derivando isso do
comentário do compose (*"um banco NOVO sobe sem nenhum DialogForm e os consumidores degradam em
silêncio: NPS não aparece, wrap-up abre vazio"*). **Medido: essa degradação só acontece porque o
`tenant_demo` fia aqueles hooks.** Num ambiente sem o pacote de demo não há hook, logo não há falha.

O que a medição diz, consumidor a consumidor:

| forma | consumidor real |
|---|---|
| `dialog_wrapup_v1` | `tenant_demo.yaml:407` (config de hook) |
| `dialog_nps_buttons` | `agente_nps_v1.yaml:64` |
| `dialog_otp_possession` | `skill_limite_entrada_v1`, `agente_portabilidade_intake_v1` |
| `dialog_promocao_deploy` | `skill_gate_promocao_v1` |
| `dialog_limite_aprovacao` · `dialog_limite_solicitacao` | skills do fluxo de limite |
| `dialog_formfill_demo` | `skill_formfill_demo_v1` |
| `dialog_survey_multi_v1` | `PoolSkillSlot.config_json` de `survey_multi_ia` |
| `dialog_wrapup_arc12_v1` | **só** `smoke_wrapup_arc12_capture.sh` — fixture de teste |
| `dialog_nps_v1` | **NINGUÉM** — superseded por `dialog_nps_buttons`. Form morto. |

**Nenhum id de form está cravado em código de produto** (o único `dialog_*` em platform-ui /
analytics-api / mcp-server é o nome do campo `dialog_form_id` e chaves de erro). Todo consumidor é
skill YAML, registry de tenant, ou config de slot.

**Conclusão:** os 10 são **conteúdo de tenant**, e o piso do diálogo é o *serviço* `dialog-api`, não
qualquer form. Não há divisão piso × pacote a fazer aqui — o que existe é um pacote só, hoje implícito
(`TENANT_ID: tenant_demo` cravado no job). O passo com evidência é **parametrizar**, não separar.

*Lição de método: eu derivei uma classificação de um COMENTÁRIO em vez de dos consumidores, e ela
sobreviveu a um commit. Comentário descreve o que era verdade quando foi escrito; consumidor é
medível agora.*

### O que JÁ está parametrizado (medido — o trabalho é menor do que parece)

- `REGISTRY_CONFIG_DIR: /registry` e `SKILLS_DIR: /skills` — env + mount, com ramo próprio para "não
  setado" (`registry_syncer.py:241`). O `RegistrySyncer` roda dentro do `orchestrator-bridge` e está
  fora do alcance de profile, mas **não precisa dele**: a alavanca é o diretório.
- `DIALOG_FORMS_DIR` idem.
- `${VAR:-default}` já é idioma do compose (**7 ocorrências** — `REGISTRY_SYNC_RECONCILE`,
  `ALLOW_LIVE_FLOW_FALLBACK`, `PLUGHUB_LLM_BOOT_PROBE`, `E2E_EXTRA_ARGS`, chaves de LLM). Não é
  mecanismo novo.
- **Idempotência (seed-if-absent) já é disciplina da casa** em `config-seed`, `dialog-seed`,
  `RegistrySyncer` e `pricing-seed`. É justamente a propriedade que permite EMPILHAR pacotes — a parte
  difícil está feita.

⚠️ *Correção de medição:* numa primeira passagem eu reportei **zero** interpolações no compose. Falso —
o `grep` teve o `\$` mastigado na passagem para o WSL e devolveu 0. Valor plausível, instrumento
quebrado; a família de sempre.

### Falta pouco

1. Separar piso de conteúdo **dentro** de cada seed job (o `dialog-seed` é o caso claro).
2. Parametrizar `TENANT_ID`, hoje cravado em cada job de seed.
3. `profiles:` nos jobs de conteúdo.

### 🔴 A regra a escrever ANTES do segundo pacote

**Seed-if-absent torna pacotes não-composáveis quando se SOBREPÕEM — e em silêncio.** Se o pacote A
semeia `pool_x` e o B quer um `pool_x` diferente, o B perde e ninguém fica sabendo.

> **Pacotes disjuntos por construção, ou colisão BARULHENTA.**

Custa nada agora e é cara depois. O precedente certo já existe: o `RegistrySyncer` **loga DRIFT** em
vez de sobrescrever calado.

### Duas consequências aceitas

- **`down -v` vira a porta de entrada**, não evento raro: "montar qualquer ambiente a partir de vazio"
  só é verdade a partir de volume limpo (o profile controla o que é ESCRITO, não o que ESTÁ lá). Isso é
  bom — é o teste de instalação limpa que o `CLAUDE.md` cobra e que ninguém roda.
- **Hoje `infra/registry/` tem UM arquivo (`tenant_demo.yaml`)**, então o ambiente sem pacote de demo é
  uma plataforma **sem pool nenhum**. Isso é a instalação limpa, não um ambiente de desenvolvimento —
  se houver necessidade de um "dev" próprio, ele é um PACOTE a criar, não a ausência do demo.
- O `agent-assist-ui` **sai desta conversa**: sob este modelo ele não é questão de ambiente, é
  superfície de produto duplicada. Eixo diferente, decisão independente (ver seção própria).


## ✅ `/sessions/customer/*` — FECHADO em 2026-08-27 (detalhe no `CHANGELOG.md`)

Portão `optional_pool_principal` + `_session_scope_clause`; gate
`infra/test/probe_customer_history_authz.sh` (irrestrito 4 · escopado 0). O diagnóstico abaixo fica
como registro do que era e de como foi medido.

### Resíduos abertos deste arco

- ✅ **`customer_history` e `..._search` devolvem 502 em falha de query** (decidido 2026-08-27). A UI já
  tinha o estado de erro desenhado e traduzido — era código morto. **Continua aberto o terceiro:**
  `list_active_sessions` (`sessions.py:88`) tem o mesmo padrão e ficou de fora de propósito — alimenta
  o Monitor, que faz polling, e erro a cada ciclo numa falha transitória ensina a ignorar a caixa.
  Decisão própria: tolerar N falhas seguidas antes de acusar.
- ⚠️ **`workflow_trace` / legacy reverse lookup nunca devolveu linha** (mesma forma inválida, desde
  sempre, `except` logando em DEBUG). Corrigido o SQL; **não medido** se o caminho primário
  (`delegate_child_ids`) já cobria tudo — se cobria, o achado é inócuo; se não, houve sessão-filha
  perdida em `workflow_trace` esse tempo todo.
- ⚠️ **`probe_ui_credential_coverage.sh` cobre 4 chamadas**, não todas. Fora: `/dashboard/*` (o
  contrato está coberto por pytest), e os outros ~47 `fetch(` de `evaluation-hooks.ts`, que apontam
  para a evaluation-api e não foram medidos.

<!-- registro original -->
## 🔴 `/sessions/customer/*` — histórico do cliente SEM autenticação e SEM escopo (medido 2026-08-27)

Achado depois de fechar os três furos de autorização; **nenhum deles cobria este**. `sessions.py` tem
**zero** ocorrências de `pool_principal`, e não há dependência global no `include_router`.

**Instrumento:** token **inválido** — é ele que discrimina, porque o portão recusa mesmo com
`analytics_open_access` ligado, enquanto "sem header" devolve 200 pelos dois motivos possíveis e não
distingue nada. Foi assim que a primeira medição (`sem token` → 200 nos três) saiu inconclusiva.

| endpoint | com `Bearer lixo.lixo.lixo` | |
|---|---|---|
| `GET /sessions/customer/{id}` | **200** | sem portão |
| `GET /sessions/customer/{id}/search` | **200** | sem portão |
| `GET /reports/pools/queue` (controle) | 401 | com portão |

O dado é histórico de contato **chaveado por `customer_id`** — lista e busca. Os trechos são
masked-by-construction (vêm de `messages` no ClickHouse), mas a associação identidade↔contatos é o
próprio dado pessoal, e **não há `audit_access_log`** em nenhum dos dois. O módulo `audit` existe
exatamente para esta classe e **recusa alto** por padrão (`_check_audit_access`); aqui não é aplicado.

⚠️ **Correção de uma afirmação minha do mesmo dia:** eu disse que endurecer o demo
(`PLUGHUB_ANALYTICS_OPEN_ACCESS: "false"`) dependia de decidir sobre o `agent-assist-ui`. **Falso** — a
única chamada que aquele app faz ao analytics é justamente este endpoint, que não consulta a flag. A
ordem verdadeira é a inversa: **fechar `/sessions/customer/*` é que quebra o `agent-assist-ui`**, logo
o app é pré-requisito DESTE item, não do endurecimento.

Pendente: decidir o portão (`optional_pool_principal` escopando por pool das sessões, ou o gate de
`audit` com trilha) e **contar os consumidores antes**, como no passo 2.


## ✅ `agent-assist-ui` — APOSENTADO em 2026-08-27 (decisão do dono; detalhe no `CHANGELOG.md`)

Ativos de demo em `infra/demo/web/`, servidos pelo `demo-assets` na mesma porta 5173.

**Resíduo aberto:** eu nunca identifiquei a origem do `curl` periódico que batia no 5173. O único
script que apontava para a porta era o `probe_duration_definitions.sh` (repontado). Se algo
automatizado ainda bater lá esperando o proxy `/analytics`, agora recebe **404** — barulhento, que é o
comportamento desejado, mas vale ficar atento nos próximos dias.

**Não reescritos de propósito:** as ~40 menções em `docs/product/passagem-*.md`, changelogs antigos e
`docs/deprecated/`. São registro histórico.

<!-- registro original -->
## `agent-assist-ui` — recomendação: APOSENTAR o app, PRESERVAR o `webchat-test.html` (2026-08-27)

Decisão do dono; abaixo a medição que a sustenta. São **duas coisas num pacote só**, e só uma é legado.

**O app React (22 arquivos) — evidência para aposentar:**

| eixo | medido |
|---|---|
| duplicação | as 4 abas (`Estado`/`Capacidades`/`Contexto`/`Historico`) existem **1:1** em `packages/platform-ui/src/modules/agent-assist/components/tabs/` |
| acoplamento | **nenhum**: as ~20 referências fora do pacote são **comentário** (`platform-ui/Dockerfile:65`, `mcp-server/server.ts:1262`, `mock-agent-ws.ts:13`, `channel-gateway/session_registry.py:130`) ou **launcher** (`ecosystem.config.js`, `scripts/linux/setup.sh`, os composes) |
| uso | zero user-agent de navegador nos logs do container; só `curl` |
| arquitetura | viola a invariante explícita do `CLAUDE.md`: *"Never create a new `packages/my-ui/` standalone frontend app — add a module to platform-ui"* |
| custo já pago | trabalho dobrado **duas vezes** (conserto do sentimento; remoção da barra de SLA morta na D14.1) |
| segurança | **sem `login`/`Bearer`/`AuthContext`** em nenhum dos 22 arquivos |

**O que NÃO pode ir junto:** `packages/agent-assist-ui/webchat-test.html` — o simulador de cliente do
demo (citado 2× no `docker-compose.demo.yml`). São **533 linhas com zero `src=`/`href=`/`import`
externo**: autocontido, move para qualquer serviço de estático.

**Custo de aposentar:** mover o HTML · repontar `infra/test/probe_duration_definitions.sh:21-22`
(usa o 5173 **só como proxy de analytics**: `API="$UI/analytics"`) · remover o serviço de
`docker-compose.demo.yml` e `docker-compose.full.yml`, de `ecosystem.config.js` e de
`scripts/linux/setup.sh` · atualizar as URLs de console nos comentários dos composes e em
`scripts/linux/seed-demo.sh:164`.

⚠️ **O que a medição NÃO respondeu, e só o dono responde:** se alguém de fato abre a tela. Ausência de
navegador na janela de log é evidência, não prova. Também não identifiquei a origem do `curl` periódico
que bate no 5173 a cada ~5 min — o único script que aponta para a porta é o probe acima, e ele não é
periódico.


## ✅ Os quatro vermelhos — FECHADOS em 2026-08-27 (detalhe no `CHANGELOG.md`)

Todos eram instrumento herdando a condição que deveria declarar, nenhum era regressão de produto.
Baselines: evaluation-api 214 · channel-gateway 677.

**O que continua aberto desta seção:** nada roda estas suítes fora deste desvio. São **1 594 testes**
(analytics 639 · auth ~~64~~ **63** · channel-gateway 677 · evaluation 214) que só existem quando alguém lembra — ⚠️ **O número do auth está velho: medido em 2026-08-30, eram 83, e ficaram 63 depois da remoção do `platform_permissions`.**
e `auth-api`/`evaluation-api` não têm pytest na imagem, `auth-api` não tem nem os testes. O `run_gates`
cobre os gates de shell; as suítes de pytest continuam sem runner.

<!-- registro original -->
## 🔴 Quatro testes vermelhos que ninguém estava vendo (medido 2026-08-27)

As suítes de `evaluation-api` e `channel-gateway` **não são rodadas de rotina** — só foram executadas
porque o passo 2 tocou os dois pacotes. Estavam com 4 falhas, todas **anteriores** à mudança (provado
copiando o arquivo de `HEAD` por cima dentro do container: falhas idênticas).

- `evaluation-api` — `TestConfigAbacGate::test_create_form_{no_token_401,without_grant_403,readonly_grant_403}`,
  todos com `ResponseValidationError`. São **testes de portão ABAC**: a família que menos pode ficar
  vermelha sem alguém saber, porque o que eles guardam é autorização.
- `channel-gateway` — `test_webhook_adapter::test_handle_resume_publishes_kafka_event`.

**A dívida não é só consertar os quatro.** É que nada roda estas suítes: 887 testes (676 + 211) que só
existem quando alguém lembra. Irmão do item *"nenhum runner invoca os gates"* — e da mesma família de
*"um ambiente que só sobe porque já subiu antes não está sendo verificado"*.

⚠️ **Agravante medido:** `auth-api` e `evaluation-api` **não têm pytest na imagem**, e o `auth-api`
**não tem os testes na imagem** (só `src/`) — a suíte dele só roda por `docker cp`. Das cinco baselines
que este projeto cita, quatro dependem de estado herdado do container.


> Itens genuinamente não implementados. Histórico de implementações concluídas em `CHANGELOG.md`.
>
> **Poda:** seções fechadas saem daqui por `infra/scripts/prune_todo_closed.py` (dry-run por
> defeito) — o CHANGELOG é a casa do concluído, e duas casas para a mesma informação é o
> defeito que este projeto evita em toda parte.

---

## 🧭 Ordem de trabalho PROPOSTA — SLA/ledger → destravar F4 → visão 2 de processos *(2026-08-25)*

> **Status: PROPOSTA, não decidida.** Nasceu da pergunta do dono *"quando começar o arco de
> processos — depois de terminar a lista?"*. Fica aqui e **em lugar nenhum mais**: a passagem
> aponta para esta seção em vez de repeti-la, porque duas cópias de um plano divergem, e este
> repositório já pagou por isso mais de uma vez.

**A premissa que a pergunta trazia não se sustenta, e é o achado principal:** *"terminar a lista
antes"* acopla coisas que não se tocam. Os resíduos abertos da D14.1 (TTL do `pool_config`,
`claimed_via`, chaves órfãs do namespace `routing`, cópias do `480_000`) são de **roteamento e
config**; o F4 é bloqueado por **três outras coisas**, todas declaradas na § *"Ler um processo = ver
seus CONTATOS em sequência"*. Nenhum item da primeira lista destrava a segunda.

**E não há regressão pressionando** *(conferido por grep em 2026-08-25, não lembrado)*: a Vista
Processos **existe** — `AnaliseJourneysPage.tsx`, montada como nível de `/analise/sessions`
(`SessionsPage.tsx:205`), com `/analise/processos` como redirect (`routes.tsx:130`). O F4 é
**melhoria** (toggle árvore↔cronologia, internas dobradas, pivô explícito), não conserto de buraco.
Isso rebaixa a urgência dele, e vale registrar contra o impulso de tratá-lo como emergência.

### Critério da ordem: IRREVERSIBILIDADE, não tamanho nem pedido

1. ~~**TTL de `{t}:pool_config:{p}` primeiro**~~ — ✅ **FEITO em 2026-08-25** (ver `CHANGELOG.md`).
   **A ordem se conferiu, e o passo 1 saiu diferente do que a proposta previa — nas duas pontas.**

   **A medição REBAIXOU o argumento de entrada.** `sla_unstamped = 0`, mas sobre `elig = 1` em 72
   esperas ⇒ **INCONCLUSIVO, não latente**: o zero mede a ausência de população depois da época, não
   a ausência do defeito. ⚠️ **A regra escrita abaixo (*"`sla_unstamped = 0` rebaixa o item 1 na
   hora"*) estava incompleta** — ela só vale com população pós-época, e sem essa cláusula teria
   mandado rebaixar por um gate vazio, que é o defeito do `discord = 0`. Corrigida aqui, e a lição
   é geral: **um critério de decisão escrito em cima de um contador precisa dizer qual população
   torna aquele contador legível.**

   **E o argumento REAL era outro, maior.** Quem respondeu foi parar o serviço: o bridge renova a
   chave a cada 15 s, logo ela só expira se ele ficar fora do ar por mais que o TTL — e nesse
   estado `get_candidate_pools` devolve **vazio** e todo contato é enfileirado (incidente de
   `changelog-2026-04-16`). O ledger de SLA era sintoma colateral, não a consequência principal.

   **Achado que ninguém tinha:** o namespace `session` **nunca foi lido** pelo bridge — três causas
   empilhadas (env ausente no compose, porta 3500 em vez de 3600, GET sem `tenant_id`), seis chaves
   de TTL editáveis na tela e inertes. Consertar só uma não moveria nada.

2. ~~**Destravar o F4 — medição + decisão, não código.**~~ ✅ **FEITO em 2026-08-25 — e os dois
   bloqueios declarados fecharam, revelando um TERCEIRO que ninguém tinha.**
   - ✅ **`spawn_reason='collect'` TEM produtor** (`collect` 0 → 1; gate
     `infra/test/probe_spawn_reason_collect.sh`, 8/0). O zero era ausência de população.
     ⚠️ **A medição como estava planejada teria medido zero:** *"rodar o cenário até parquear"* não
     produz amostra — o `collect` é lazy e o rótulo nasce no **engajamento**, não no parqueamento. Foi
     preciso dar o clique, e a entrega do link não existe. **Lição de método: antes de rodar um
     cenário para popular um campo, leia QUEM escreve o campo** — "rodar o fluxo que o menciona" e
     "atingir a linha que o escreve" não são a mesma coisa, e o zero das duas é idêntico.
   - ✅ **`delegate = 0` está explicado — é DESENHO**, não produtor mudo (`delegate()` sempre roda como
     conferência, reusando o `session_id` do pai). Detalhe na § do arco. **Não re-medir.**
   - ✅ **Texto decidido pelo dono: ACESSOS DO CLIENTE**, e são **N** (abertura + N consultas de
     status + resposta), não 2.
   - ~~🔴 **Bloqueio 3: os acessos intermediários não são membros do processo.**~~ ✅ **FECHADO no
     mesmo dia (passo 2b).** `avaliar_pendencia(found)` → `unificar_journey` →
     `avaliar_politica_retomada`: pertença antes da política, um site só. Gate
     `infra/test/probe_journey_merge_status_access.sh` **vermelho→verde** (✅2❌3 → ✅6❌0), com
     **duas** consultas no gate porque a decisão era sobre N, não sobre uma. Ver `CHANGELOG.md`.

   **Construir o F4 antes disso repete a armadilha que a F3 JÁ PAGOU:** as colunas ANI/DNIS foram
   construídas e depois removidas por não terem população que as exercitasse.

3. ~~**F4 junto dos resíduos da F3**~~ — ✅ **FEITO em 2026-08-25** (ver `CHANGELOG.md`). Os três
   resíduos e a visão 2 fecharam na mesma passada, e a aposta de que eram a mesma superfície se
   confirmou: o filtro por direção **destravou** o F4 em vez de competir com ele, porque foi ele
   que forçou a direção a ter um predicado único — e é esse predicado que separa as duas classes
   de linha da visão 2.

   **O que a fase revelou, e não era item dela:**
   - **Eram QUATRO telas linkando com `session_id`, não três.** A quarta
     (`ProcessosPage.tsx:604`) usava o caminho legado e estava quebrada em três camadas
     independentes. O grep da passagem procurou `analise/sessions?` e não alcançava
     `/contacts/sessions?sessionId=`.
   - **A direção estava a um passo de virar duas verdades.** Ver ADR §F4 item 1.
   - **`journeyLabel` já eram duas**, com a prosa de uma afirmando que não.
   - 🆕 **`AnaliseTab.tsx` ficou sem NENHUM consumidor** — ver Backlog.

### O que mudaria esta ordem

- **Inverter 1 e 2** é defensável se o negócio estiver pedindo o processo — o custo é o ledger seguir
  furando enquanto isso, e ele é mensurável (`sla_unstamped`). **Inverter 2 e 3 não é**: é construir a
  tela antes de saber se a classe de linha tem população.
- ~~`sla_unstamped = 0` medido no passo 1 rebaixa o item 1 na hora~~ — **regra corrigida ao ser
  aplicada (2026-08-25)**: `0` só é evidência de latência **se houver população pós-época**
  (`sla_eligible > 0`). Veio `0` sobre `elig = 1` ⇒ inconclusivo, e o item foi decidido por outra
  via (o mecanismo, medido parando o serviço). Ver o passo 1 acima.
- Amostra de `collect` aparecendo no passo 2 **fecha** o bloqueio principal do F4 e o torna a fatia
  óbvia seguinte.

### 🆕 Aberto pela fase 3 (2026-08-25)

- ✅ **`AnaliseTab.tsx` — RESOLVIDO por remoção (medido 2026-08-30).** O arquivo foi apagado em
  `10bde79` e a feature (métricas agregadas do conjunto filtrado) foi absorvida pelas **cinco
  lentes da F2**, sobre o mesmo predicado da lista. Não há decisão pendente. Registro original:
  **`AnaliseTab.tsx` perdeu o último consumidor.** Ela era usada só por `ContactsPage` e
  `AnaliseContatosPage`, as duas removidas — e **já estava inalcançável antes disso**, porque
  nenhuma das duas tinha rota desde a F3.3. Não foi apagada junto de propósito: é uma FEATURE
  (métricas agregadas do conjunto filtrado de contatos), e decidir se ela volta — como aba de
  `/analise/sessions`, ou não volta — é do dono, não consequência de uma limpeza. **Enquanto
  não se decide, é código morto que compila.** Mesma classe do trio de skills do item 13.
- **O gate novo (`probe_f4_direction_and_classes.sh`) não é chamado por runner nenhum** —
  mesma dívida de quase todo `infra/test/`, agora com mais um.
- **O ramo "não classificadas" existe sem população** (0 em 115). Está certo que exista; mas
  ele **nunca foi exercido**, e um ramo nunca exercido é uma promessa, não uma verificação.
  Para exercê-lo bastaria um `spawn_reason` novo — o que é exatamente o dia em que ele
  importa.
- **O dobramento (D11) foi exercido com 2 internas** no processo de referência. Processo com
  maquinaria profunda (N níveis de `trigger` aninhado) **não foi visto** — a árvore agrupa
  pelo acesso ancestral mais próximo, e isso só tem uma forma de errar que a amostra atual não
  distingue.
- 🔴 **O chip da lista e o cabeçalho do processo contam coisas diferentes, e o operador clica
  num e encontra o outro.** Medido na tela em 2026-08-25: chip `PRC-8c47326d · 5` → cabeçalho
  `3 customer accesses · 2 internal steps`. **Os dois estão certos** — o chip conta SESSÕES do
  processo (`journey_session_count`, predicado de contato) e o cabeçalho conta ACESSOS DO CLIENTE
  (D4, que a F4 introduziu). É a mesma família do *"cabeçalho diz 3, tabela mostra 4"* que o D11
  fechou um nível abaixo, ressurgindo um nível acima — e desta vez foi a F4 que a criou, ao dar
  ao cabeçalho um domínio que o chip não tem. Três saídas, decisão do dono: **(i)** o chip passa
  a contar acessos (fica consistente, mas some a informação de tamanho do processo); **(ii)** o
  chip ganha os dois números (`· 3 + 2`); **(iii)** só o rótulo muda (`{{count}} sessões`, não
  `contacts`) — o mais barato e o que menos promete. ⚠️ A chave i18n hoje diz
  *"Process with {{count}} contacts"*, que é a leitura MENOS defensável das três.
- 🆕 **`GET /config/dashboards?tenant_id=tenant_demo` responde 404 repetidamente** (visto no
  console do browser, 4+ ocorrências por carga de página). Não foi investigado — pode ser rota
  removida com chamador vivo, ou proxy faltando, que é o defeito que a H1 do arco de histórico
  já pagou uma vez.
- 🆕 **Do drill de uma sessão-membro não há caminho de volta ao PROCESSO.** ✅ **FECHADO no mesmo
  dia** (ver `CHANGELOG.md`): selo `PRC-… · N` no breadcrumb, alimentado pelo mesmo lookup que
  resolve o canal. O registro fica porque a CAUSA vale: o único pivô era o chip, e o chip mora
  na lista — quem chega por deep-link nunca passa por ela. Achado ao ver a
  tela: o operador abriu `?session_id=` (um dos deep-links que esta mesma fatia consertou),
  viu o `Workflow trace` de uma sessão interna e concluiu que *"os dois contatos não aparecem
  em lugar nenhum"* — estavam a um clique de distância, na visão 2. O único pivô para o
  processo é o chip, e ele mora na LISTA; quem chega por link direto nunca passa por ela. O
  conserto óbvio (um selo `PRC-…` no breadcrumb do drill quando a sessão tem processo com
  N > 1) é barato, mas é decisão de navegação: hoje o drill não pede a journey da sessão.
- ~~🆕 **A lente "Cronologia" agrupa por acesso, e por isso NÃO está em ordem cronológica
  estrita**~~ — ✅ **FECHADO no mesmo dia, pela saída (ii)** (ordem global estrita, internas
  soltas no eixo). Ver a emenda ao D6 no ADR. **A pergunta que fechou não foi a que abriu:** o
  registro dizia *"a cronologia contradiz o próprio rótulo"*, e a medição mostrou algo pior —
  com as internas dobradas (o default) **as duas lentes produziam exatamente as mesmas linhas**,
  e a única diferença era a indentação de um neto. Foi o dono quem notou, perguntando se o
  toggle *"por enquanto não faz nada"*. **Um controle que não muda nada no caso comum é
  indistinguível de um quebrado** — e essa é a família do seletor que a própria F3 removeu.

**Estado após 2026-08-25 (segunda sessão do dia): passos 1, 2 e 2b FECHADOS.** O passo 2 descobriu
um bloqueio de pertença (acessos de consulta fora do processo) e o **2b o fechou no mesmo dia** —
era a metade que faltou da F1, não fatia nova.

**Estado após a terceira sessão de 2026-08-25: os quatro passos (1, 2, 2b, 3) estão FECHADOS, e a
ordem acabou.** Ela se conferiu três vezes e se corrigiu duas — e a terceira correção é a que vale
guardar: **o passo 3 parecia ser "duas coisas na mesma superfície, por economia", e era uma só**.
O filtro por direção não foi contrabandeado ao lado do F4; foi ele que obrigou a direção a ter um
predicado único, e é esse predicado que separa acesso de etapa interna na visão 2. A economia
prevista era de passada; a real foi de MODELO.

**O que resta desta linha de trabalho:** a **F5** (`ContextStorePersister`, fase própria, desenho
fechado no ADR §3) e a **lente C** (faixas por personagem, destino registrado). Nenhuma das duas
tem bloqueio declarado, e nenhuma é continuação óbvia — a próxima fatia volta a ser escolha do
dono, não posição numa fila.

---

## ⚠️ Direção REVERTIDA em 2026-08-18 — leia antes de pegar qualquer item daqui

A decisão de direção do n8n de 2026-08-17 — alvo *"todo skill associado a um pool passa a ser autorado
no n8n; o editor de fluxo local sai por completo"* — foi **ABORTADA**. Justificativa, reclassificação
item a item e o que sobrevive vivem num artefato só:
[`docs/product/n8n-arco-abortado-2026-08-18.md`](docs/product/n8n-arco-abortado-2026-08-18.md).

**No lugar dela:** **A2A server binding** ([`adr-a2a-server-binding.md`](docs/adr/adr-a2a-server-binding.md))
para abrir a fronteira por protocolo padrão — uma implementação, N consumidores — e **editor gráfico
próprio**, alavancado por *execução observável* (rodar o fluxo e ver o `pipeline_state` passo a passo),
não por canvas. A direção *"config + interpretador genérico"* sobrevive inteira e **não dependia do n8n**.

**O que a reversão faz com os baldes da triagem:**

| Balde | Estado agora |
|---|---|
| **14 `Congela`** | **DESCONGELAM** — o gate da fase 3 que os prendia não existe mais. Voltam à triagem normal, sem prioridade herdada |
| **9 `Escopo reduzido`** | ✅ **REEXAMINADOS em 2026-08-26** — veredicto item a item na seção *"Reexame dos 9 em Escopo reduzido"* logo abaixo |
| **4 `Aborta`** | **SEGUEM ABORTADOS**, por mérito próprio — a própria triagem registra que nenhum caiu por *"o n8n cobre"* |
| **28 `Segue`** | inalterados |

> [`n8n-triagem-2026-08-17.md`](docs/product/n8n-triagem-2026-08-17.md) vira **insumo histórico**: a
> evidência por item (arquivo:linha) continua válida; os baldes e as âncoras de fase, não. **Não usar
> como filtro vivo.** O mesmo vale para `n8n-interop-boundaries-and-seams.md` e `n8n-plano-execucao.md`.

> ⚠️ **A decisão que a reversão NÃO toma:** a costura C (n8n como *domain MCP server* governado) não foi
> abortada — aponta na direção contrária, não toca autoria, e é a de maior retorno declarado. Está
> desacoplada, sem dono, à espera de decisão explícita. Ver §7 do documento de reversão.

---

## Reexame dos 9 em `Escopo reduzido` — 2026-08-26

Critério: o corte foi feito com o fundamento *"esta parte vira template n8n"*. Onde o corte tinha
**outro** fundamento (código morto, YAML que nenhum pool deploya, stub 410), ele sobrevive sozinho.

| Item | O que ia ser cortado | Veredicto |
|---|---|---|
| **2** — `workflow-api`, a PORTA | o motor de lifecycle (= item 5), mantendo as duas rotas de trigger | **corte mantido.** A parte que fica produz fronteira (única escritora de `workflow.instances`; a `/trigger` cria sessão) e tem 11 leitores na UI. Só a âncora de fase morre |
| **5** — o MOTOR + legado por token | 5 stubs 410, `WebhooksTab`, rota órfã `/workflow/calendar` | **corte mantido** — stubs mortos cujo único chamador (`skill-flow-worker`) segue abortado. Ressalva: a *subida de prioridade* da reescrita de `webhook-patterns.md` §Padrão 1 era n8n; volta pela porta do A2A, prioridade média |
| **9** — avaliação campaign-driven | a cola do motor de revisão por workflow (G-S2.4) | **corte mantido e AMPLIADO** — ver § própria abaixo. A parte que FICA (lentes + G-PROBE) perdeu o consumidor nomeado (o item 7 morreu com a reversão) e precisa de dono novo |
| **12** — Business in Any Media | nível (a) *fluxo negocial channel-abstract*, contrato delegate-por-pool, intake-flow | **corte REVERTIDO** — a razão era literalmente *"autoria, que vira template n8n"*, e a autoria fica em casa. Rejulgar, não restaurar: a pergunta nova é *quanto disso vira config + interpretador genérico*. Nota: classificado sem código, porque são 5 specs e zero implementação |
| **15** — OTP + primitivo de diálogo | absorvia as *"limitações declaradas"* no item 6 (interpretador como serviço de código) | **absorção mantida, DONO NOVO** — o item 6 não morreu, mudou para a frente editor/config+runner e *"ganha, não perde"* com a reversão |
| **16** — guard de teardown-hook | o **mecanismo** (varrer steps de skill YAML via `SKILLS_DIR`), porque *"sem esse bloco não há o que varrer"* | **corte REVERTIDO** — o bloco não morre mais, então o mecanismo tem sobre o que operar. A garantia nunca esteve em jogo (é governança de contato). A rejulgar: se a migração parse-time → boundary-time vale por si |
| **17** — Customer Surveys S1–S11 | S2 absorvido no item 6; S7 (editor de DialogForm) sobrevive e ganha | **parcial** — S7 sobe (confirmado). Se o **S2** volta a ter dono próprio ou segue absorvido é **decisão a tomar, não herdada** |
| **20** — `agente_wrapup_v1.yaml` × `_apply_wrapup_to_segment` | o YAML (nenhum pool o deploya), preservando a função viva | **corte mantido** — mesma classe do trio do item 13. Segue valendo: extrair helper compartilhado com `segment_outcome_record`, hoje duplicado por admissão própria |
| **55** — Delegate v2, E.4 | E.4(a) *MCP audit por step* mudava de dono para o mapeador (item 7) | **reatribuir** — o item 7 deixou de existir, mas (a) não morre: a causa dele é o `mcpCall` fora do interceptor. Novo dono = ADR de borda única MCP, fase B2 |

**Placar:** 4 cortes mantidos (2, 5, 20 e 9-ampliado) · 2 revertidos (12, 16) · 3 reatribuídos (15, 17, 55).

### Achado adjacente — dois dos 4 `Aborta` precisam do mesmo reexame

O doc da reversão (§4.6) afirma que os 4 abortados *"seguem abortados por mérito próprio"*. Confere para
o **4** (`skill-flow-worker`: 4 de 5 saídas HTTP em 410) e o **13** (trio de YAML sem pool). **Não confere**
para o **10** (step de expressão sandboxed) e o **11** (hot-reload de YAML em disco): os dois foram
abortados com a razão *"o corpo do fluxo morre"* / *"não sobra o que recarregar"* — que é a definição
estrita do balde (*"depende do editor de fluxo que morre"*) e caiu junto. **D1: rejulgar 10 e 11.**

### Item 9 — a cola do G-S2.4 é maior do que a linha registrava (medido 2026-08-26)

O `TODO.md` descrevia 4 peças (consumer, coluna, skills, cenário 28). A medição achou **três acoplamentos
não registrados**, um deles com tela viva do outro lado.

| Achado | Evidência | Consequência |
|---|---|---|
| **O campo morto gateia uma tela VIVA do Arc 13** | `CampaignsPage.tsx:1359` — `CurationSamplingRulesDetailPanel` só renderiza se `review_workflow_skill_id === 'skill_revisao_treplica_v1'` | dropar a coluna esconde a curadoria. **A1 precede A4** |
| **…e o painel nunca renderiza hoje** | `select review_workflow_skill_id from evaluation.campaigns` → **1 linha**, e é `skill_revisao_simples_v1`. **Zero** campanhas com `treplica` | A1 fica barato (sem migração de dado) mas ganha metade nova: destravar é mudança de comportamento. ⚠️ *Suspeita, não provada:* explicaria o item *"Curation/Calibration — telas nunca validadas com dado real"* desta mesma seção |
| **O caminho VIVO lê estado que só a cola morta escreve** | `router.py:2285` e `:2404` fazem `if result.get("resume_token"): await _resume_workflow(...)`; e `:2276-2282` grava `session.review_decision` no ctx *"so the suspended workflow YAML choice step can branch on it"* | removida a cola, viram ramos permanentemente falsos — *promessa sem produtor*. Saem junto (**A3**) |
| **`skill_revisao_simples_v1` não existe em lugar nenhum** | sem arquivo em `skill-flow-engine/skills/`; sem linha no registry (`select … like 'skill_revisao%'` → só `treplica` e `skill_revisao_v1`). Mas `seed_evaluation.py:198` o grava em toda campanha e o cenário 28 dispara por ele | **referência pendurada** = defeito próprio (**A0**), fechado de graça pelo A4 |
| **Armadilha de nome: arquivo ≠ id** | `agente_revisor_v1.yaml:80` declara `id: skill_revisao_v1` | **A5 é por id, com o arquivo mapeado.** Apagar por nome de arquivo não revela o que sai do DB, e vice-versa |
| **O cenário 28 não passa** | `--only 28` estoura o timeout de 60 s, zero asserções. É opt-in (`--workflow-review`), fora da suíte default 01–18 | acaba o argumento *"mantido por compat com o 28 — raio de teste no 28"*: **não há raio de teste**. A6 = apagar |

**As 3 skills têm destinos DIFERENTES** — a linha antiga as agrupava como `skill_revisao_*`/`agente_revisor_v1`,
que é a armadilha do item 20 (triar por pacote, não por função): `agente_revisor_v1` é inerte, mas o MCP tool
`evaluation_review_submit` (`evaluation.ts:1559`) é **Arc 13 Fase D, vivo**, e o cita no docstring;
`agente_pre_revisor_v1` se autodeclara legado (`:97-99`), mas o endpoint `submit_pre_review` está **vivo** e é
item aberto do G-PROBE; `skill_revisao_treplica_v1` é inerte e é o valor que gateia o painel.

#### Tarefas — item 9

| # | Tarefa | Módulo | Estado |
|---|---|---|---|
| A0 | Seed grava `review_workflow_skill_id` inexistente (`seed_evaluation.py:198`) | infra/seed | defeito; fecha com A4 |
| A1 | Trocar o discriminador do `CurationSamplingRulesDetailPanel` por flag própria de campanha; decidir se o painel deve ser alcançável | platform-ui | **bloqueia A4** |
| A2 | Remover consumer `workflow.events` (`main.py:39-129`, wiring `:579-580`/`:624-625`) + `update_result_workflow_state` (`db.py:2042`) | evaluation-api | pronto |
| A3 | Remover vestígios no caminho vivo: `_resume_workflow` (`router.py:2285`/`:2404`) + `_write_ctx` (`:2276-2282`) | evaluation-api | pronto |
| A4 | Coluna `review_workflow_skill_id`: DDL `db.py:239` · API `router.py:794/825/889` · seed `:198` · seletor UI + i18n | evaluation-api, platform-ui | depende de A1 |
| A5 | Classificar as skills de revisão **por id** (filtro `skill_%revisao%` — a query de 08-26 não cobriu `skill_pre_revisao_v1`) | skill-flow-engine | pronto |
| A6 | Apagar o cenário 28 | e2e-tests | decidido |
| A7 | Redefinir o consumidor da parte que fica (lentes de qualidade + G-PROBE) | doc | — |

#### Tarefas — demais itens do reexame

| # | Tarefa |
|---|---|
| B1 | **Item 12** — rejulgar nível (a), contrato delegate-por-pool e intake-flow sob a pergunta *"quanto vira config + interpretador genérico"* |
| B2 | **Item 16** — decidir se a migração parse-time → boundary-time do guard de perfil vale por si |
| C1 | **Item 15** — reatribuir ao editor/config+runner; verificar se o plumbing `$.config` (✅ 2026-07-08) já fechou parte da *"limitação declarada"* |
| C2 | **Item 17** — decidir o enquadramento do S2 (dono próprio × absorvido) |
| C3 | **Item 55** — reatribuir E.4(a) ao ADR de borda única MCP, fase B2 |
| C4 | **Item 17** — remediar a evidência do `value_label` (a triagem citou arquivo errado) antes de entrar em plano |
| D1 | Rejulgar os itens **10** e **11**, abortados com premissa que caiu |

---

## Resíduos resgatados da poda de 2026-08-26

> **Por que esta seção existe.** A poda de 11 seções fechadas deleta por seção INTEIRA, e 8 delas
> tinham pendência viva enterrada no corpo (a rodada de 2026-08-03 teve 3 de 7 — a armadilha piorou,
> não melhorou). Estes itens foram colhidos à mão ANTES da remoção. **Não foram resgatados** os
> resíduos que já tinham casa em outra seção aberta — duplicar seria dar duas casas à mesma
> informação, que é o defeito que a poda existe para evitar: a rota anônima já vive em § *N3*; os 3
> timeouts e o portão de deriva do seed foram movidos para dentro da § *I5*, cujo cabeçalho já os
> nomeava; a fase I5 citada como "faltante" no wrap-up **foi fechada depois** (CHANGELOG 2026-08-07).

**R1 — `instance_bootstrap` desfaz a pausa de agente IA em 15 s.** *(de "Capacidade × PAUSA")*
Para agente de **IA** a pausa passou a agir, mas o `agent_ready` da reconciliação do bootstrap (15 s)
a desfaz. Não é regressão (antes não agia nada); o conserto é no `instance_bootstrap`, que precisa
parar de republicar `ready` para instância com pausa declarada.

**R2 — `_sessions_meta` não tem marcador para o escopo de POOL.** *(de "Volume de sessões inexplicado")*
O `meta` publica `window_applied` e `internal_pools_known`, e **nenhum** para o escopo de pool.
Conserto na mesma forma da casa: marcador (`pool_scope_applied` / nº de pools) em `_sessions_meta` e
leitura honesta na UI. ⚠️ Registrado como pendente pelo próprio CHANGELOG, que diz que o defeito
"fica registrado no `TODO.md`" — apagar sem resgatar quebraria essa referência.

**R3 — carimbo de DNIS não entrou.** *(de "Webhook — registro único")*
Era candidato natural naquele arco (a tela passou a exibir o endereço), mas é mudança de backend +
dado analítico, com risco próprio. Fatia separada. O CHANGELOG confirma que segue a fazer.

**R4 — cenários e2e discam `flow_id` sem linha e tomam 404.** *(de "Webhook — registro único")*
Afetados: `03_resume_after_failure`, `13_workflow_automation`, `14_collect_step`,
`18_workflow_worker_chain`, `28_evaluation_workflow_cycle`, todos via o proxy `/v1/workflow/trigger`
da workflow-api. **Eles já não funcionavam**: nenhum pool declara aqueles skills, o router os
rejeitava e a sessão morria enfileirada — a mudança troca "201 + sessão que não vai a lugar nenhum"
por "404". Conserto: migrar para endereço por **pool** (preferível) ou semear linhas de teste.
**Cuidado ao consertar:** se a suíte passou a sessão inteira com 201 sobre sessão morta, o verde dela
já não significava o que parecia; conferir o que cada cenário realmente assere antes de só trocar a URL.
› **É a tarefa 2 da passagem de 2026-08-26** (13/14/18 vermelhos no `--demo`). O `28` sai por decisão
própria (tarefa **A6** do reexame). ⚠️ Endereçar por **pool**, nunca por skill — invariante do `CLAUDE.md`.

**R5 — dois pontos do arco de requeue nunca medidos.** *(de "Guard de teardown-hook")*
(a) se um segundo agente logado no mesmo pool consegue de fato puxar o item duplicado — exige dois
logins; (b) se o wrap-up `-int` perde o `assigned_to`. Este segue **DEDUZIDO** do evento de seis
campos: em `formfill_demo` o campo nasceria vazio de qualquer forma, então o `null` observado não é
evidência sobre item author-bound.

**R6 — rótulo de formulário não sobrevive à edição (snapshot pendente).** *(de "Linha do tempo única")*
O formulário é MUTÁVEL e a resposta é HISTÓRICA: a via barata da S4 não recupera o rótulo CERTO de
uma pergunta que foi editada. **Fechar de verdade exige snapshot do form na gravação** (como o link
de survey já faz no `create`). **Gatilho para reabrir:** primeira edição real de um `dialog_*` que já
tenha respostas gravadas.

**R7 — docstring obsoleto + medição do `reap:` nunca feita.** *(de "Vaga só é liberada no `agent_done`")*
(a) o docstring de `registry.py` §938 ainda repete a afirmação antiga *"a vaga só é liberada no
`agent_done`"* — comentário obsoleto que contradiz o código; (b) a medição de frequência do
`warning 'reap:'` que aquele item pedia **nunca foi feita**, e não se responde por leitura — precisa
de log de produção. ⚠️ Sem cobertura nenhuma no CHANGELOG: se não fosse resgatado, sumia.

**R8 — `participant_id`: pré-requisitos 1 e 2 seguem abertos.** *(de "`role` no hash de participante")*
1. **Unificar a convenção de `participant_id`** — o bridge publica `participant_id=native_instance_id`
   no Kafka (`main.py:3622`) mas entrega `uuid4()` ao especialista de conferência (`main.py:2863`,
   nunca persistido). Duas identidades para o mesmo participante; nenhum store conserta isso antes.
2. **Produzir o vocabulário** — `_part_role = "specialist" if conference_id else "primary"`
   (`main.py:3489`) é a ÚNICA decisão de papel no sistema; os outros 11 call sites de
   `_publish_participant_event` passam literais. `supervisor` nunca é emitido por caminho nenhum.
   > **Consequência OBSERVÁVEL:** numa sessão-filha de `delegate` o roster traz **DOIS participantes
   > `primary`** — o agente nativo do workflow e o humano que reivindicou o item. Não é defeito do
   > roster: é este pré-requisito. A regra binária (`conference_id` ou não) responde `primary` para os
   > dois, e qualquer consumidor que assuma *um* primary por sessão vai errar.

Correlato do mesmo arco: `crash_detector.py:144` ainda usa `meta.pools[0]` (mitigado por pular
`human-*` em `:98`) · **testes de estabilidade multi-pool** seguem inexistentes, embora a F5 os previsse.

---

## ⚠️ Erros de método que se repetem — ler antes de atacar qualquer item

Esta seção **não descreve trabalho pendente**. Descreve como se erra aqui, e ficou depois que
uma sessão inteira (2026-08-03) mostrou que o mesmo padrão custa mais que os defeitos.

### 1. Item antigo mente sobre o próprio estado — MEÇA antes de executar

Dos 5 itens atacados em 2026-08-03, **três estavam stale** e **dois eram maiores** do que o
registro dizia:

| Item | O que o TODO dizia | O que a medição achou |
|---|---|---|
| F5 (limpeza de capacidade) | 4 peças pendentes | 3 já feitas **no mesmo dia** em que a nota foi escrita; a peça real (`position_updated` lendo `available_agents`) não estava na lista |
| Pricing → quota Redis | "pricing-api não tem código Redis" | existe desde 2026-06-04; as chaves estavam vivas (`370/360/10`) |
| JWT do BFF | assinatura não verificada | **e** `exp` não verificado — token expirado valia para sempre |
| 87 segmentos órfãos | 87, "os 9 da aprovação são ruído" | eram **107**, e os da aprovação tinham subido para **17** — deixaram de ser ruído e viraram evidência de defeito aberto |

**Regra:** antes de executar, meça o que o item afirma — inclusive os NÚMEROS que ele cita. Em
dois casos a medição mudou a decisão, não só o escopo.

### 2. Hipótese plausível é a que engana — escreva-a COMO hipótese

Duas ficam registradas porque o custo de cada uma foi real e o formato foi o que salvou:

- **`PLUGHUB_AUTH_JWT_SECRET` na analytics-api.** O TODO supunha que os 2 vermelhos de
  `TestPoolPrincipalAuth` vinham da variável ausente no container, e mandava *"confirmar antes
  de tratar como defeito de código"*. A variável **estava definida**. A causa era
  `_with_internal_mirrors` (ADR author-bound D2), que passou a derivar o espelho `-int`. A
  hipótese era plausível, batia com o sintoma (JWT/pool-scoping) e teria levado a mexer no
  compose. *O que salvou foi ela estar escrita como hipótese, com o "confirmar antes" junto.*

- **Limpar antes de medir, nos órfãos.** O TODO propunha `DELETE` direto. Medindo primeiro
  apareceram 9 órfãos PÓS-fix — e a limpeza teria apagado a única evidência de que a lacuna 2
  (sem reaper de `claim_lease`) acontece de fato.

### 3. Verde acidental custa mais que vermelho

Em 4 classes de teste, o conserto do vermelho revelou irmãos VERDES pela mesma causa —
ocupando o lugar da cobertura que faltava. Ao consertar um teste, olhe a **classe inteira**.

### 4. Prever o número antes de rodar

Quatro previsões erradas em 2026-08-03, três delas produzindo resultados **plausíveis**
(`7 passed / 22 failed` por rootdir errado; `9 failed / 13 passed` de uma mutação que não
mutou nada). Sem a previsão escrita, cada uma teria virado diagnóstico em vez de erro.

### 5. O TÍTULO é o que mente para mais gente *(acrescentado 2026-08-03, 2ª sessão)*

Três seções foram achadas com **corpo mantido e cabeçalho não**, na mesma varredura:

| Seção | Título dizia | Corpo dizia |
|---|---|---|
| Capacidade | "implementação não iniciada" | F1, F2, F3a, F4a/b/c, F5, F5b ✅ |
| I5 | "resta o relatório" | relatório = fatias 1 e 2, ambas ✅ |

**Quarta ocorrência, 2026-08-07 — a variante que erra para MAIS.** O I5 dizia *"seguem abertas
2/3/4/6"* com a 3 e a 6 fechadas (a 3 na própria tabela da seção; a 6 pela Fase E), e o corpo dizia
*"Próximo: Fase E"* com E e F concluídas. As três da tabela acima subestimavam o feito e faziam
pular a seção; esta **superestimava o pendente** e faz gastar a sessão re-medindo o que já foi
medido. A regra não muda de forma: *o cabeçalho entra no mesmo commit que fecha a fatia* — inclusive
quando o que fecha é uma FASE citada num parágrafo de "próximo passo".
| Resolvedor de Identidade | "falta Slice 3 + Fase B" | Slice 3 ✅ e Fase B ✅ há um mês |

Atualizar item a item e deixar o título é o modo mais comum de a seção envelhecer, e o mais caro:
**o título é a única linha que se lê no índice**. Quem varre o TODO para planejar nunca chega ao
parágrafo que corrige. Ao fechar uma fatia, o cabeçalho da seção entra no mesmo commit.

### 6. Um portão nunca julga o ALVO quando a falha foi na PRÓPRIA montagem *(2026-08-03)*

O `smoke_approval_segment_closes.sh` v1 mandou o resume sem `tenant_id`, levou 422 — e o passo
seguinte, mesmo tendo marcado o submit como INCONCLUSIVO, **seguiu julgando** e concluiu
*"❌ DEFEITO REAL na aprovação"*. O segmento estava aberto porque ninguém submetera. O vermelho era
convincente e tinha 17 órfãos "confirmando"; levado adiante, custaria a sessão consertando código
correto. **Pré-condição falha ⇒ INCONCLUSIVO e o teste PARA.** Portão que aponta o lugar errado é
pior que portão nenhum — este manda alguém trabalhar.

*Placar do dia, que é o argumento inteiro:* **7 defeitos de instrumento × 1 achado de código real.*
Seis falharam alto e custaram minutos. O sétimo falhou plausível e quase custou a sessão.

### 7. Como um dublê mente *(destilado dos 48 vermelhos de 2026-08-03; seção de origem podada)*

Nenhum dos 48 era defeito de produção, e a causa se repetiu em famílias. As duas que não têm outra
casa permanente ficam aqui, porque valem para a **próxima** suíte que alguém escrever:

- **Dublê de store responde por CHAVE** — nunca com valor único para todas elas, nunca por ordem de
  chamada (`side_effect` posicional). Valor único faz o teste ler o dado do vizinho; ordem faz o
  teste depender de um detalhe que o código pode reorganizar sem quebrar nada. Melhor ainda:
  **derivar o dublê por introspecção** do objeto real, para ele responder à ESTRUTURA e não a uma
  serialização congelada.
- **A alavanca do teste tem de ser a fonte que o CÓDIGO lê** — mexer em `Settings` quando o adapter
  resolve pelo cache do config-api testa uma alavanca desconectada (invariante *"config-api vence"*).
  O teste fica verde porque não alcança o caminho, não porque o caminho está certo.

Detalhe completo das 5 famílias no `CHANGELOG.md` § *"Zero suítes vermelhas: 48 falhas em 6 pacotes → 0"*.

### 8. Um número que PARECE resposta *(2026-08-05 — cinco casos num dia)*

Placar do dia: **5 defeitos de instrumento × 1 achado de código real**, e os cinco tinham a mesma
forma — um número foi produzido, foi lido, e não significava o que parecia. Nenhum falhou alto.

| O que apareceu | O que significava de verdade |
|---|---|
| `238 deselected in 0.37s` | **zero** testes selecionados; `-k` não casou nada, exit 0 |
| `2 passed` (duas vezes) | container ainda com o código VELHO — o arquivo de teste mora na imagem |
| `INCONCLUSIVO` sobre saída correta | o veredicto do probe só sabia REPROVAR (ver abaixo) |
| `grep -c` = 0, duas vezes | `--since` com timestamp sem fuso: janela de log 3 h no FUTURO |
| `presence_at_reclaim = 1` | proxy lido no instante errado — o guard avalia DEPOIS |

**A regra que emergiu, e que funcionou toda vez que foi aplicada:**

> **Um contador de AUSÊNCIA precisa de um contador-TESTEMUNHA de presença ao lado.**

Sem a testemunha, `0` significa *"não aconteceu"* **ou** *"não foi exercitado"*, e não há como
distinguir. Os três pares que funcionaram: `Return to queue` ao lado de `Skipping duplicate`;
`Pool (registered|already exists)` ao lado de `HTTP 401`; roster com participante ao lado de
`ctx_participants`. **O único gate que pegou seu próprio erro de primeira** foi o que já exigia a
testemunha antes de aceitar um verde.

**A pergunta que teria evitado três erros seguidos:** *qual linha de código precisa executar para
este número mudar, e o meu experimento a executa?* Duas vezes propus um gate que media o **produtor**
quando a mudança estava no **consumidor** — e a segunda vez foi na mesma mensagem em que eu advertia
contra a primeira.

**Corolário do §3 (verde acidental), na direção oposta:** um veredicto de três estados pode estar
errado no ramo que **aprova**, e isso não chama atenção porque o estado inútil (`INCONCLUSIVO`)
parece prudência. O probe de janela contava ausências nas duas pontas e exigia "nenhum dos novos
presente" para dar verde — impossível quando a janela passa de metade da fila, porque as duas pontas
se **sobrepõem**. Ele sabia reprovar e não sabia aprovar. **Provar que o veredicto APROVA exige uma
execução esperada-verde**; só a esperada-vermelha não basta.

**Controle negativo por ritual manual não é validação.** Reverter → rebuildar → rodar → restaurar
falhou **duas vezes**, das duas por pular o rebuild. Substituído por um teste **diferencial** (lê o
mesmo dado pelas duas semânticas e afirma que DIVERGEM), que roda em todo build e ainda cobre o caso
que o ritual nunca cobriria: fixture encolhida para dentro da janela faz as duas leituras
coincidirem, os testes de sentido seguem verdes e param de discriminar — em silêncio.

### 9. O pente vale mais que o plano *(2026-08-05)*

Sete achados antigos foram auditados contra o código que executa. **Três já estavam consertados** —
resolvidos de passagem por arcos vizinhos, sem ninguém marcar. Um deles (`§1b`) estava completo
inclusive no bundle servido.

Isso põe número no §1 e no §5: **o custo não é o item ficar aberto, é o registro AFIRMAR que está
aberto**, porque quem planeja em cima dele planeja trabalho que não existe. Antes de abrir uma frente
grande, passar o pente nos achados da faixa de datas correspondente — a taxa foi de 3 em 7, e cada
verificação custou minutos.

*Corolário achado no mesmo dia:* dois itens sem nenhuma referência cruzada podem estar em cadeia. O
**§101** (401 no registry) travava a suíte e2e, que era o único caminho vivo do leitor de `role` do
**§1055**. Um defeito de autenticação a três saltos era o que impedia de validar outro. **Ao fechar
um item, perguntar o que ele DESBLOQUEIA.**

---

## 📂 TEMA · Direção e frentes abertas

## Frentes abertas pela reversão *(2026-08-18)*

### A2A server binding — abrir a fronteira por protocolo padrão

Uma implementação, N consumidores (LangGraph, CrewAI, orquestrador do cliente — e o n8n entre eles).
Decisão em [`adr-a2a-server-binding.md`](docs/adr/adr-a2a-server-binding.md), fases **A0→A6**.

**Duas emendas de 2026-08-18:** o **D11** (graça de espera do caller) é substituído por
[`adr-pool-no-resource-policy.md`](docs/adr/adr-pool-no-resource-policy.md) — vira config de pool
(`on_no_resource`) e o binding só traduz para `503`/`TASK_STATE_REJECTED`; e o **D9** (cota por
`a2a_client`) sai do v1 — a contenção é `allowed_pools` + pool dedicado + `on_no_resource: reject`.

**Pools humanos seguem FORA de escopo** (§8 do ADR), condicionados a *demanda comercial nomeada*. É
provavelmente o produto mais diferenciado que existe aqui, e exige **A5 (streaming) como pré-requisito**,
porque o A2A é blocking por padrão e uma fila humana de minutos só é conforme com a linha aberta.

### Editor de fluxo próprio — a alavanca é execução observável, não o canvas

Canvas é commodity (React Flow/xyflow) e a parte difícil já existe (`validateFlow`: adjacência fechada +
guarda de ciclo). O que falta é **rodar e ver**: executar o fluxo com dados de teste, mostrar o
`pipeline_state` passo a passo, e **replayar uma sessão real dentro do canvas** — substrato que o
Session Replayer e o `pipeline_state` persistido a cada transição já dão, e que o n8n não tem.

**Duas frentes já existentes reduzem o escopo desta** e valem por si: **N1** (config + interpretador
genérico) e **N4** (editor de DialogForm).

**Risco herdado, que a reversão não apaga:** o diagnóstico original estava certo — editor sem
investimento apodrece. Se passarem dois trimestres sem commit em `agent-flow`, esta frente virou o
*"fallback sem investimento"* que o documento abortado proibia com razão.

### Capacidade de IA — `reserved` × `shared`

ADR: [`adr-pool-capacity-reserved-shared.md`](docs/adr/adr-pool-capacity-reserved-shared.md) ·
Handoff: [`capacidade-reserved-shared-kickoff.md`](docs/product/capacidade-reserved-shared-kickoff.md).

Medido em 2026-08-18 (`tenant_demo`): **Σ declarada = 329** em 30 pools de IA; **as três quotas `nil`**
⇒ admissão, provisionamento e login **todos fail-open**. É construção, não conserto.

⚠️ **A ordem inverte e parece engano:** a **P1** (`deployViolation` contra `C_ai`, uma linha em
`capacity.ts:27`) vem **antes** da **P0** (ligar o pricing). Hoje é inócua; depois do P0 é mudança de
comportamento em produção. A janela para consertar o defeito C fecha no instante em que as quotas
existirem.

---

## Itens do levantamento de n8n que SOBREVIVEM à reversão *(2026-08-18)*

> O arco de interop foi abortado — ver [`n8n-arco-abortado-2026-08-18.md`](docs/product/n8n-arco-abortado-2026-08-18.md).
> **Estes itens ficam**, porque entraram na fila do n8n sem serem sobre n8n. Matá-los por associação de
> nome repetiria o erro que a própria §10.4 do documento abortado corrigiu: *"nome de pacote não era
> unidade de decisão"*.
>
> **Prioridade preservada, justificativa trocada.** Onde o texto abaixo ainda argumenta *"com n8n do
> outro lado"*, leia *"com qualquer consumidor externo do outro lado"* — o A2A põe um lá do mesmo jeito.

**Morreu junto com o arco** *(registrado aqui para não ser reaberto por engano)*: o alvo de autoria no
n8n; a morte do editor `agent-flow`; a costura B (absorvida pelo principal externo do A2A, fase A2); a
frente N2 (mapeadores `flow_definition`/`pipeline_state` ← n8n); o gate de latência de turno; a fachada
OpenAI no ai-gateway como *requisito*; e `skill-extract` tendo o JSON do n8n como alvo.

**Guarda que continua load-bearing, com ou sem n8n:** a pressão para empurrar control-flow para dentro
do DialogForm existe de qualquer forma — e cresce à medida que a plataforma migra de *"lógica em YAML"*
para *"config + interpretador genérico"*. `adr-dialog-conditional-skip-logic.md` (guarda declarativa
`ask_when`, *não* control-flow) precisa ser defendido: se ceder, o editor de fluxo é reconstruído dentro
do editor de formulário, com uma linguagem pior.

### Promover `skill-flow-service` a pacote de primeira classe

Achado de due diligence, independente de qualquer decisão de interop: **o runtime de produção dos skills
mora num pacote de testes.**

O runtime de produção dos skills conversacionais é `packages/e2e-tests/services/skill-flow-service/`
— cujo cabeçalho diz *"Thin HTTP wrapper … for E2E testing"* e que é dependência `service_healthy`
do orchestrator-bridge, do mcp-server e da evaluation-api. Complementa o achado da seção seguinte
(que já identifica esse `mcpCall` como o caminho nativo sem interceptação).

| Achado | Evidência |
|---|---|
| O `skill-flow-worker` está morto para MCP, e o código admite | `orchestrator-bridge/main.py:638-639` |
| Ele ainda consome `workflow.events` (produtor vivo em `workflow-api/kafka_emitter.py`), mas posta em `/mcp`, rota que não existe | `engine-runner.ts:131` × mcp-server só expõe `/sse` e `/messages` |
| Mapa de servidores MCP hardcoded com 2 entradas + **fallback silencioso** | `skill-flow-service/src/index.ts:35-38`, `:142-144` — servidor desconhecido vai ao mcp-server-plughub em vez de falhar |
| `agente_contexto_ia_v1.yaml:96` aponta para `mcp-server-crm`, que **não existe** | O erro que aparece é "tool desconhecida", não "servidor não configurado" |
| A tool `invoke` resolve endereço por env `MCP_SERVER_{NOME}_URL`, **não definida em lugar nenhum** | `tools/external-agent.ts:149-159` |
| `tools/list` **não é chamado em nenhum ponto do repositório** | Zero discovery; nomes de tool sempre hardcoded |

Escopo: mover para `packages/skill-flow-service/`, substituir a convenção de env pelo catálogo
`mcp_servers` no config-api, implementar `tools/list` (+ snapshot no slot como detector de drift de
contrato), remover o fallback mudo, e decidir o destino do `skill-flow-worker`.

*Ressalva: "404" e "env ausente" são conclusões estáticas (ausência de rota e de env em todos os
compose), não observadas em execução.*

### Frentes NOVAS que o levantamento de 2026-08-17 descobriu

Quatro foram criadas; **três sobrevivem** à reversão, com justificativa própria. Nenhuma existia como
item no `TODO.md` nem no `CLAUDE.md` § Pending — foram **descobertas ao levantar o terreno**, não
inventadas pelo alvo, e é por isso que sobrevivem a ele.

#### N1 — Promover o interpretador genérico a serviço de código *(agora: redutor de escopo do editor)*

O interpretador genérico é hoje **ele mesmo um skill em YAML**: `skill_dialog_runner_v1.yaml`, 119 linhas,
`steps:` de `:49` a `:118`, **5 steps** (`carregar_form` → `coletar` → `retornar` → `retornar_falha` →
`finalizar`) e **2 tools MCP apenas** (`form_get` `:58`, `workflow_resume` `:96`/`:108`). O trabalho real
dele é repassar o render do form ao canal e devolver o escalar cru — é código, não autoria.

**Justificativa após a reversão:** deixou de ser *"não tem onde morar com o YAML morto"* e virou o
oposto — **quanto mais skill vira config sobre interpretador genérico, menos superfície o editor
gráfico precisa cobrir.** De bloqueante da fase 5 a redutor de escopo do editor próprio. **Arrasta a
superfície inteira do dialog primitive:** hooks de finalização, NPS inline, wrap-up, survey
(chat/inline/web/Console) e OTP.

**Escopo:** serviço de primeira classe que (a) resolve o `DialogForm` publicado, (b) renderiza na superfície
certa e (c) devolve `payload = { value: <escalar> }` — o contrato uniforme já as-built. As *"limitações
declaradas"* de hoje são propriedades do interpretador **em YAML** e devem sumir junto: hook que não pode
delegar, delegate de nível único (`session.delegate_resume_token`), binding do form por `@ctx` em vez de
`$.config`.

**Achado a resolver no caminho:** o avaliador de `ask_when` está **triplicado** — canônico em
`packages/schemas/src/dialog.ts:423`, espelho JS em `channel-gateway/…/survey_web.py:386` e terceiro
espelho em `platform-ui/…/DialogFormRenderer.tsx:400` (o comentário em `:75` se declara *"mirror of
`evaluateAskWhen`"*). O ADR previu dois. Três implementações do mesmo veredicto divergem.

**Pré-requisito:** a 2ª passada do editor de DialogForm (N4) estável — é a superfície que passa a autorar o
conteúdo.

#### ~~N2 — Mapeadores de `flow_definition` e `pipeline_state`~~ *(MORREU com a reversão)*

Existia porque, com 100% dos skills autorados no n8n, a avaliação de IA degradaria **em bloco** para
grau-transcript. Com a autoria ficando em casa, **nada degrada** — `flow_definition` e `pipeline_state`
continuam sendo produzidos pelo engine local. A frente inteira some.

**Dois resíduos que NÃO somem junto** (eram dependências dela e valem por si):

- **`sequence_index` é calculado e nunca persistido** — `orchestrator-bridge/main.py:915`; o
  `participant_left` grava `0` e o `ReplacingMergeTree` substitui a linha do join, quebrando **5
  `argMax`** em `reports_query.py:2183-2209`. Defeito de atribuição por segmento, independente de tudo.
- **`evaluation.ts:1131-1155` degrada em silêncio** — lê `flow_id` de `context["pipeline_state"]`, faz
  `fetch ${agentRegistryUrl}/v1/skills/{flow_id}` e monta `{ skill_id, version, flow: sk["flow"] }`
  (`:1183`). **Com a coluna `flow` vazia o campo some sem erro.** É o modo de falha que a § Postura de
  Engenharia manda caçar, e continua aberto com o engine local.
- O **Record/Replay Harness** perde a justificativa *"é o único que pega a tier-2 apagando"*, mas
  sobrevive com a própria: gate de promoção.

#### N3 — Fechar a rota anônima `POST /v1/channels/webhook/pool/{pool_id}` *(segue, e SOBE de prioridade)*

**Não é item de n8n — é achado de segurança.** Sobrevive intacto, e o A2A o torna mais urgente, não menos:
o D7 do [`adr-a2a-server-binding.md`](docs/adr/adr-a2a-server-binding.md) exige `tenant_id` vindo
**exclusivamente da credencial**, e sem isso o binding publica um disparador anônimo de pools que promovem
deploy e contatam clientes.

Era natural supor que o arco *"Autenticação de endpoint webhook"* a cobrisse —
**não cobre**: aquele arco fecha a porta **por identificador** (`auth_required` por `ChannelEndpoint`), e a
rota **por pool** fica de fora por construção, porque não passa pelo registro de endpoint e por isso não tem
onde pendurar credencial.

`channel-gateway/…/main.py:1011-1048`: a assinatura recebe só `(pool_id: str, request: Request)` — zero
`Depends`, zero `_require_*`, e o arquivo não tem middleware de auth global. Pior, o **`tenant_id` vem do
corpo** (`:1037`, `body.get("tenant_id") or settings.tenant_id`), logo é cross-tenant por construção assim
que a superfície for publicada. Com n8n do outro lado, todo pool webhook do tenant — inclusive os que
promovem deploy e contatam clientes — vira disparável por qualquer um.

**Vai junto, mesma fase:** *"Porta externa de resume × posse do item de pull"* (a porta pública passa
`approver=None` por construção, então o gate de `channel-gateway/…/adapters/webhook.py:1163` — `if approver
is not None and claim_instance_id:` — nunca roda) e *"`source` do resume asserido pelo cliente"* (chamador
externo escolhe a causa terminal gravada).

#### N4 — Revisão do editor de diálogos *(segue, e GANHA importância)*

**Âncora quebrada:** `CLAUDE.md:1226`, `CHANGELOG.md:9708`, `CHANGELOG.md:9843` e o kickoff de triagem
`:211` apontam para uma seção *"Revisão do editor de diálogos"* neste arquivo. Ela **nunca existiu** — o
conteúdo está espalhado em duas subseções de outras seções (`TODO.md` § OTP/primitivo, *"2ª passada"*; e o
S7 do § Customer Surveys). Quatro documentos citando um alvo inexistente.

**Por que sobe de nit a caminho crítico:** o editor de DialogForm (`/config/dialog-forms`) é a superfície
de autoria de **conteúdo conversacional**, e ela é ortogonal ao editor de fluxo — as duas convivem. Com a
direção *"config + interpretador genérico"* (N1) andando, é esta tela que absorve cada skill que deixa de
ser fluxo e vira config. Ela cresce em uso independentemente do que aconteça com o editor de fluxo.

**Escopo consolidado** (união das duas listas espalhadas, sem duplicar):
- **Segurança — sobe a requisito:** o **write hoje não tem gate ABAC**, só `X-Admin-Token`.
- Drag reorder (hoje setas ↑↓); nós colapsáveis; agrupar validação/retry/opções; campo `retry.reprompt`.
- Locale lado-a-lado com progresso de tradução estável; **preview do que o cliente vê**.
- Validação client-side: slug de `form_id`, `output_key` único, `dimension_id` em snake_case.
- Confirmação de descarte; `interaction=form` com múltiplos `fields`.
- Biblioteca de `survey_question` reutilizável (vinha do S7).
- **Guarda a defender, não a construir:** `ask_when` é skip-logic declarativa e o limite é esse. Se o editor
  ganhar `next` condicional, laço ou variável, o editor de fluxo é reconstruído dentro do editor de
  formulário, com uma linguagem pior. Ver `docs/adr/adr-dialog-conditional-skip-logic.md` (**Aceito +
  implementado**, não proposto) — de suas 3 decisões em aberto, só **uma** segue aberta de verdade
  (`checklist` multi-valor como `field`).

---

### Defeitos colaterais achados no levantamento *(independentes do n8n)*

- **`llm_tokens_*` não é emitido no caminho principal.** `emit_llm_tokens` tem um único call site,
  `InferenceEngine.infer()` (`ai-gateway/inference.py:149-161`) = `POST /inference`. O **`/v1/reason`**,
  que é o step `reason` dos skill flows, **não emite**; `/v1/turn` também não. Verificar se alguma
  cota ou relatório de tokens está sendo lido como se tivesse dado.
- **O hint de backfill mente.** A UI de campanha de qualidade diz *"Past start = reprocesses history
  (backfill)"*, mas o backfill é endpoint manual (`POST /v1/evaluation/campaigns/{id}/backfill`,
  `router.py:1151-1186`) e **a UI nunca o chama** — grep por `backfill` em `modules/evaluation/`
  devolve só a própria string do hint (`CampaignsPage.tsx:600`). O operador põe data no passado e
  nada acontece, sem sinal.
- **Campo morto homônimo.** JSONB `schedule` com `window_start`/`window_end`/`days_of_week` em
  `evaluation-api/db.py:73`, sem nenhum leitor. Resíduo de desenho substituído pela calendar-api — e
  quem procurar "janela" acha esse antes do `period_start`/`period_end` verdadeiro.
- **`MCP_PROXY_URL` aponta para serviço inexistente.** `tools/evaluation.ts:803` faz `fetch` em
  `localhost:7422`; não há proxy em nenhum compose; o `catch` só emite `console.warn`.
- **DECR de `hook_pending` não inspeciona outcome** (`orchestrator-bridge/main.py:4952`); o
  tratamento de `suspended` é guardado por `not conference_id` (`:4663`, `:4784`). Não morde hoje,
  mas é borda desguardada.
- **Não foi localizado o guard do engine** que rejeitaria `delegate` fora de sessão webhook — o
  comportamento se apoia em comentário e wiring.
- **`inline` tem dois significados** em `PoolHookEntry` conforme o `side`: conferência quando
  `customer`, máquina destacada quando `agent`.
- **`ai-gateway` não tem shim OpenAI** (`/v1/chat/completions`), mas `providers/openai_provider.py`
  já converte o formato interno *para* Chat Completions na saída — a fachada de entrada é largamente
  reverter isso, e é o que permite ao AI Agent node do n8n usar o gateway sem perder rotação
  multi-conta e fallback.

**Acrescentados pela triagem de 2026-08-17:**

- **Avaliador de `ask_when` triplicado** — canônico `evaluateAskWhen` em
  `packages/schemas/src/dialog.ts:423`; espelho JS em `channel-gateway/…/survey_web.py:386`; **terceiro**
  espelho em `platform-ui/…/DialogFormRenderer.tsx:400`, cujo comentário em `:75` se declara *"mirror of
  `evaluateAskWhen`"*. O ADR previu dois. Três implementações do mesmo veredicto divergem — mesmo modo de
  falha da assimetria de permissão entre bordas.
- **`masked_input_fields` é um contador de ausência sem testemunha.** Existe em
  `analytics-api/…/audit.py` e é filtro do endpoint de auditoria LGPD, mas **não tem escritor** — sempre
  `[]`. Para o DPO, *"nenhum campo mascarado nesta sessão"* e *"ninguém nunca escreveu"* são
  indistinguíveis.
- **`EventsView` pede `period=24h` a um endpoint que só aceita `from_dt`/`to_dt`**
  (`MonitorTab.tsx:794` × `reports.py:1431`): a janela real é de 7 dias e o i18n diz *"últimas 24h"*.
  Número plausível escondendo bug.
- **`spawn_reason` tem zero amostras de `collect`/`delegate`** no demo (só `NULL` 349 e `trigger` 71) — e é
  dele que a visão 2 do histórico deriva a direção do acesso. Medir antes de renderizar, senão a tela nasce
  plana e a planura parece resposta.
- **Índice de ADR desatualizado no `CLAUDE.md`** (corrigido 2026-08-17): `adr-dialog-conditional-skip-logic.md`
  constava como *proposto* por mais de um mês, estando **Aceito + implementado desde 2026-07-08** e validado
  ao vivo. Doc que descreve config não é a config — três documentos já discordaram do código nesta mesma
  investigação.
- **`TODO.md:3698` (`### Config Consolidation / HTTP Propagation`) é filha de
  `## Relatórios analíticos — Agentes e Pools`** — título do pai sem relação nenhuma com o conteúdo. Quem
  procurar consolidação de config pelo índice não acha.

---

## 📂 TEMA · Qualidade e Avaliação

## `sequence_index` apagado pelo `participant_left` — atribuição de agente em qualidade está em risco *(achado 2026-08-10)*

**Causa raiz, localizada e única.** O `sequence_index` é atribuído no `participant_joined` (contador Redis
`INCR session:{sid}:segment_seq`) mas **não é persistido junto com o `segment_id`**
(`orchestrator-bridge/main.py:918-922` grava só o segment_id). No `participant_left` ele é reconstruído
como `0` (`main.py:6759`) ou omitido (default `0`, `main.py:3030`) — e como `analytics.segments` é
`ReplacingMergeTree`, **a linha do left substitui a do join e apaga o índice**. Atinge **todo segmento
humano e todo especialista**; os nativos escapam por acidente (join e left no mesmo escopo léxico).

Medido: sessão `5553c72a` saiu `0, 0, 2` — o `1` do segmento humano existiu no join e foi sobrescrito.
Verificável com `SELECT sequence_index, ingested_at FROM segments WHERE session_id=… ` **sem `FINAL`**,
que mostra as duas versões.

⚠️ **A consequência mais séria não é a ordenação — é ATRIBUIÇÃO.** `reports_query.py:2183-2209`
(`_session_agent_attribution_sql`) usa **cinco** `argMax(…, sequence_index)` para decidir *qual agente é
atribuído à sessão* nas lentes **quality**, **quality_criteria**, **deploy** e **session_nps**. Com
empates em `0` o `argMax` é **não-determinístico** — e as cinco colunas podem vir de **linhas
diferentes** (agent_key de um segmento, pool_id de outro). Na sessão medida, atribuiria ao agente nativo
(seq 2) e não ao humano que efetivamente atendeu. **Nota de honestidade:** o impacto real em números de
qualidade **não foi medido**, só derivado do código.

⚠️ **Inventário de consumidores REVISADO 2026-08-10 (a v1 desta nota errava dois dos quatro).** Cada um
tem de ser julgado por *como lê*, não por *se cita o campo*:

| Consumidor | Como lê | Hoje | Depois do conserto |
|---|---|---|---|
| `_session_agent_attribution_sql` (5 `argMax`) | `segments FINAL` | empate **só quando o máximo empata** | determinístico |
| `quality-export/exporter.py:181` | `segments FINAL`, `ORDER BY seq, started_at` | correto **por acidente** | **REGRIDE** — ver abaixo |
| `mv_segment_summary` → `handoff_count` | **MV sobre INSERTs**, `maxState` | **correto** (vê a linha do join) | inalterado |
| `SegmentList.tsx:146` (badge) | exibição | badge some | badge correto |

- **`handoff_count` NÃO é afetado.** A MV (`clickhouse.py:922-940`) é gatilho de INSERT sobre `segments`,
  **sem `FINAL`**: ela agrega as DUAS versões da linha e `maxState` preserva o índice do join. O defeito
  vive só onde se lê o estado mesclado. *(A v1 listava a MV como afetada — leitura por citação do campo,
  não pelo mecanismo.)*
- **O empate do `argMax` é mais estreito do que a v1 dizia, e por isso mais fácil de reproduzir errado.**
  `argMax` só é não-determinístico quando o **máximo** empata. Na sessão medida (`0, 0, 2`) o máximo é
  único → a atribuição já sai determinística, no agente nativo. O empate real é a sessão com **dois ou
  mais primários humanos** (transferência): ambos gravam 0, o máximo empata, e as 5 colunas podem vir de
  linhas diferentes. Um teste que use uma sessão com resume nativo no fim **não pega** o empate.
- ⚠️ **O exporter REGRIDE com o conserto, e por isso saiu na mesma fatia.** `ORDER BY sequence_index ASC,
  started_at ASC`: enquanto tudo empata em 0, a cláusula degenera em cronológica e o export sai certo.
  Com o índice correto, um especialista de conferência (fora do contador ⇒ 0) entrando tarde passa a
  ordenar **antes** de um primário de handoff (1+). Consertar o produtor sem rechavear o consumidor
  **quebraria** o export — caso literal de "o conserto move o número". Rechaveado para `started_at ASC,
  segment_id ASC`.

**A cadeia a jusante já está correta — não precisa de conserto** *(verificado 2026-08-10)*. O registro
`human_seg:{pool}` é gravado **no `participant_left`** (`main.py:1613`); `_seed_segment_signal:3321` lê
`record["sequence_index"]` e `_republish_segment_from_signal:3362` lê do acumulador. Os três propagam
fielmente — **herdam o `0` do left**. Logo o conserto é num ponto só de origem, e a cadeia se corrige
sozinha. *(Isto corrige a v1 desta nota, que listava acumulador e `segment.ts` como sites a alterar.)*

**Conserto, simétrico ao que já se faz com `segment_id`:**

1. **No join** (`main.py:900-930`): `_seq_idx` é calculado em `:915` e **nunca persistido**, enquanto
   `_seg_id` é gravado em `:918-922`. Persistir o índice ao lado — chave paralela
   `session:{sid}:segment_seq_idx:{instance_id}` (mesmo TTL 14400) **ou** um campo a mais no
   `participant_meta:{instance_id}` (`:934-944`), que já se declara *"fonte por-participante para o path
   de close"*. Preferir o `participant_meta`: reusa chave existente e é `get`, não `getdel`, então
   sobrevive a um left republicado.
2. **Nos left sites** que hoje mandam `0`: recuperar junto com o `segment_id`, no mesmo bloco `getdel`
   que já existe (`main.py:2830`, `4531`, `5641`, `6165`, e os demais em `6854`/`8182`).

⚠️ **Um segundo left do MESMO segmento tem de continuar funcionando.** O `segment_id` sobrevive porque os
sites guardam fallback em escopo (`_left_seg_id = _part_seg_id`); o índice precisa do mesmo cuidado, ou o
republish volta a escrever 0 e o `ReplacingMergeTree` desfaz o conserto — exatamente o bug de novo, por
outro caminho.

**Como validar (e o e2e 23 NÃO serve — ver acima):** rodar `smoke_formfill_renderer.sh` + claim/submit no
Console e conferir que o segmento humano sai com `sequence_index = 1`, não `0`. A prova de hoje foi
aritmética (`0, ?, 2` com o meio em zero ⇒ o `1` foi consumido e perdido); depois do conserto a sequência
tem de ler `0, 1, 2`.

⚠️ **Não conserta a ordenação, e não deve prometer isso.** Mesmo corrigido, `queue`, sintéticos e
especialistas ficam fora do contador **por decisão** — o campo é *"ordem entre primários não-sintéticos"*,
nunca ordenação total. Ordenar por `started_at`.

### Decisão de fatia — tomada 2026-08-10, com o critério explícito

A pergunta era *"os consumidores que usam o índice como chave de ordem entram na mesma fatia ou viram
fatia própria?"*. O critério que decide **não é acoplamento, é neutralidade**: entra na fatia do conserto
o consumidor que o conserto **move**; vira fatia própria o que ele deixa parado.

- **Verificação pedida, feita:** `_session_agent_attribution_sql` **filtra sim** —
  `WHERE role = 'primary' AND agent_type != 'system'` (`reports_query.py:2204-2205`). E o escopo do filtro
  **coincide exatamente** com o escopo do contador: os três únicos sites de `INCR segment_seq`
  (`main.py:914` humano, `:4148` nativo não-conferência, `:8045` resume) publicam todos
  `role='primary'` com `agent_type` ∈ {`human`,`native`}; `queue` (`:5418`) e especialista (`:4136`)
  nunca incrementam e são excluídos pelo `role`. **Logo o conserto TORNA o índice único dentro do
  conjunto filtrado, e o empate desaparece** — a hipótese *"empata mesmo depois do conserto"* está
  **refutada**, e o rechaveamento da atribuição não é pré-requisito.
- **Na fatia do conserto:** o **exporter**, porque o conserto o quebra (acima). Não é zelo, é evitar
  introduzir um defeito.
- **Fatia própria:** o rechaveamento dos 5 `argMax` para `started_at`. Depois do conserto os dois
  critérios **concordam** (índice e `started_at` derivam do mesmo evento de join), então a troca é
  provadamente neutra — e é justamente por ser neutra que ela merece medição própria: se o número mudar,
  mudou por outro motivo. Continua valendo fazê-la, por dois motivos que o conserto não remove:
  o índice não é ordenação total, e o TTL de 4 h do contador o reinicia em 0 numa sessão longa
  (`started_at` não tem essa borda).

### ⚠️ Achado adjacente — a REGRA de atribuição erra em suspend/resume, e nem o conserto nem o rechave a corrigem

`_session_agent_attribution_sql` atribui a sessão ao **último** primário não-sintético. Na sessão
`5553c72a` esse é o segmento nativo de **13 ms** que apenas processou o resume — não o humano que passou
30 s preenchendo o formulário. Depois do conserto isso fica **determinísticamente errado**, que é pior de
notar do que aleatoriamente errado. Vale para toda sessão do padrão delegate→humano→resume: a lente de
qualidade credita a máquina pelo trabalho do humano.

Não é bug do índice — é a regra "último primário" encontrando um caminho de execução que ela não previu.
O conserto certo depende da **transição como primeira classe** (D4 do ADR): com a lacuna nomeada, dá para
atribuir ao segmento que fez o trabalho em vez do que fechou a porta. **Registrar como entrada do arco de
workflow, não deste conserto.**

**Dívidas adjacentes que apareceram junto:**
- **O e2e 23 não pode pegar isto** — `23_contact_segments.ts:532-552` publica join **e** left já com o
  índice correto, então nunca exercita o caminho onde o left zera. Teste que passa por não tocar o
  defeito.
- **`ConversationParticipantEventSchema` não declara `sequence_index`** (`contact-segment.ts:108-139`) —
  o campo trafega sem validação em ponto nenhum do pipeline.
- **Contradição de doc, precisa de decisão humana:** `arc5-segments.md:23,106` diz escopo **por pool com
  reset**; `adr-contact-segments.md` e `CLAUDE.md:852` dizem **por sessão sem reset**. O código segue o
  segundo.
- **TTL do contador** (`session:{sid}:segment_seq`, 14400 s) — sessão com >4 h entre dois joins reinicia
  em 0. Segunda fonte de duplicata, plausível e **não medida**.

---

## Cenário e2e 28 falha por config, não por lógica — e o achado reforça a Fase 4 *(medido 2026-08-11)*

Rodando `E2E_EXTRA_ARGS=--workflow-review`, o 28 estoura 60 s. **Não é regressão do arco de workflow** — a
evidência é que a `evaluation-api` nunca chega a tentar o resume (nenhuma linha nos logs dela; os
`webhook resume` do channel-gateway são todos da corrida do gate, horas antes). O cenário morre antes.

**Causa raiz, com a linha:** `POST /v1/workflow/trigger` devolve **502 Bad Gateway**. Essa rota é proxy
para `channel-gateway/v1/channels/webhook/{flow_id}` e **repropaga o status do gateway** — 502 é o código
reservado a *inalcançável*. E o serviço `workflow-api` **não declara `PLUGHUB_WORKFLOW_CHANNEL_GATEWAY_URL`
no `docker-compose.demo.yml`** (envs em `:1356-1363`): cai no default `http://localhost:8010`, que dentro do
próprio container é ELE MESMO. Conexão recusada, 502, sempre.

⚠️ **Mesma família do defeito que a Fase 4a evitou por um triz na `evaluation-api`** — default `localhost`
dentro de container, degradando em silêncio. Lá o env foi adicionado junto com o repointe; aqui nunca foi.

**Defeito secundário, visível no mesmo log:** o polling do 28 chama `GET /v1/workflow/instances/` (com
barra final) → FastAPI responde **307** → o cliente refaz em `/v1/workflow/instances` **sem os parâmetros**
→ **422**, em laço até o timeout. A barra final custa a query string no redirect.

**Por que isto REFORÇA a Fase 4, em vez de bloqueá-la:** o trigger legado está quebrado no demo e ninguém
percebeu, porque o cenário 28 é o **único** gatilho dele — e o motor de review por workflow que o 28
exercita já está classificado como legado inerte (`CLAUDE.md` § Arc 6). É evidência de que *nada vivo
depende dessa rota funcionar*. **Não consertar a config**: a 4d remove a rota. Consertar só faria o
inventário de chamadores voltar a incluir um caminho que estamos removendo.

**O que fica pendente de verdade:** a Fase 4a segue com **fiação provada e comportamento não provado** — o
28 não consegue exercitá-la, porque quebra antes. O gate honesto da 4a precisa ser outro: um teste que
dispare contestação→revisão sem passar pelo trigger legado.

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
- **G-S2.4 aposentado (decisão 2026-06-25) — remoção física DECIDIDA em 2026-08-26** (reexame do item 9; tarefas A0–A7 na seção *"Reexame dos 9 em Escopo reduzido"*). ⚠️ **Esta linha subestimava o escopo em três pontos, todos medidos em 2026-08-26:** (1) o campo morto **gateia uma tela viva do Arc 13** (`CampaignsPage.tsx:1359`), que por isso **nunca renderiza** — zero campanhas com `skill_revisao_treplica_v1`; (2) o caminho **vivo** lê `resume_token`, que só a cola morta escreve (`router.py:2285`/`:2404`, mais o `_write_ctx` em `:2276-2282`); (3) as 3 skills têm **destinos diferentes** — `evaluation_review_submit` e `submit_pre_review` são Arc 13 **vivos** —, e `agente_revisor_v1.yaml` declara `id: skill_revisao_v1` (arquivo ≠ id). O *"raio de teste no 28"* **não existe**: o cenário é opt-in, fora da suíte default, e estoura o timeout sozinho.

**Achados pré-existentes (não causados pela F1.0)**
- **A — specialist-return (pré-requisito/núcleo da F4)**: conference specialist que termina com `escalate` re-roteia o CONTATO em vez de **voltar ao chamador** (ex.: `agente_auth_form_v1.yaml` → `retencao_humano` → fila, com mensagem de fila espúria). Fix preferido: **engine** — flow em modo conference specialist trata `escalate`/`complete` como retorno-ao-chamador devolvendo outcome. Sub-arco próprio.
- **B — multi-sessão humana no push**: humano servindo entra `state="busy"` e `get_ready_instances` exige `state=="ready"` → mesmo sob capacidade (`max_concurrent=3`, vindo da URL do WS do Console — `mcp-server` server.ts:2147 — não do `auth`) não recebe 2º contato via push. Pull (F1) endereça; decisão pendente: o push também deveria manter `ready` enquanto sob capacidade? Medir ao vivo antes de atacar.

---

## 📂 TEMA · Sessão, Segmento e Journey

## `session:{id}:meta` — o problema não é "quatro escritores", é uma partição de propriedade não declarada *(achado 2026-08-19; MEDIDO 2026-08-20; **fatia A FECHADA 2026-08-22**)*

> ✅ **Fatia A entregue em 2026-08-22** — partição declarada e MEDIDA, helper
> `session_meta_merge` (3 modos, `EVAL` único, `soft` para backfill), regra do MAIOR TTL nos três
> sites do bridge, gate `probe_meta_ttl_bridge_off.sh` com `SELFTEST`. Ver `CHANGELOG.md` e
> [`docs/guias/session-meta-ownership.md`](docs/guias/session-meta-ownership.md).
>
> **O defeito que a fatia A consertou não estava nesta lista** — ele apareceu ao medir: o bridge
> reescrevia com `_stl()` (4 h) o meta que a porta webhook escreve com 86 400 s (24 h), e a workflow
> descrita fica suspensa por `timeout_hours*3600+3600` (48 h). Contagem antes do conserto:
> **7 tokens de resume condenados, 0 cobertos**. A recusa `tenant_unknown` do arco P2 estava sendo
> desfeita a jusante pelo prazo.
>
> **Abertos desta seção (rejulgados 2026-08-22):**
> · **B — recusar campo alheio.** O helper hoje LOGA `[session_meta] DONO VIOLADO` e não recusa.
>   Recusar só no bridge enquanto webchat/webrtc/webhook/`bpm.ts` gravam `SETEX` cego por fora
>   fecharia o caminho certo e deixaria os errados abertos. Exige o helper também em TypeScript e
>   compartilhado entre os dois pacotes Python — ou um gate que compare as listas de campos.
> · **C — `pool_id` carrega DOIS fatos.** Pool de ENTRADA (`webchat.py:210`, `webrtc.py:484`) × pool
>   que ATENDE (bridge; é a acepção que `webhook.py:1330` lê no resume). Separar em `entry_pool_id`;
>   família de `elapsed_time_ms` × `agent_time_ms` (D9). Muda contrato de vários leitores.
>   **Consumidor novo, achado em 2026-08-26:** `server.ts:1421` resolve por aqui o pool cuja
>   `context_visibility` governa a aba Contexto do Console — logo, num contato TRANSFERIDO a
>   política aplicada é a do pool de ORIGEM, e configurar o pool que atende não tem efeito. Some
>   à lista de leitores a migrar; é o primeiro em que a acepção errada tem consequência de
>   **exposição de PII**, não só de relatório. Ver TEMA · Segurança § `Context Store Visibility`.
> · **Suspeita adjacente, NÃO medida:** `session:{id}:wf_agent` (escrito ao lado do site W6) nasce
>   com o mesmo `_stl()` = 4 h e descreve a mesma workflow de até 48 h. Mesmo mecanismo; medir antes
>   de consertar.
> · **Sub-item TTL 24 h → 4 h da passagem de 08-21: RESOLVIDO ao contrário do que se supunha.** A
>   dúvida era se havia truncamento; há, e o conserto **não** foi baixar o teto — foi parar de
>   encurtar. Não uniformizar TTL aqui.

Medido: **seis** escritores de produção, não quatro — e um dos quatro citados **não existe**
(`delegate`/`collect` **não** gravam a chave; `handle_delegate`/`handle_collect` só escrevem
ContextStore. O comentário `webhook.py:570` e o CHANGELOG afirmam que gravam — o achado herdou o
erro). São: webchat (`webchat.py:196`), webrtc (`webrtc.py:473`), trigger de webhook
(`webhook.py:587`), `conversation_start` (`bpm.ts:207`) e **três** sites no bridge com **três
semânticas diferentes** — merge-só-se-existir (`main.py:960`), `SET NX` (`:4501`), merge-ou-cria
(`:4561`). Não há schema Zod/Pydantic nem helper: **a chave é interpolada à mão em ~50 sites**, e os
dois `SessionMeta` que existem no repo descrevem outra coisa (o `ReplayContext`) com um conjunto de
campos quase disjunto — o nome já estava tomado, o que ajuda a explicar por que ninguém viu.

**A partição implícita está CERTA; o que falta é ela ser declarada e respeitada.** Canal/trigger são
donos de `tenant_id`/`channel`/`contact_id`/`customer_id`/`started_at`; o bridge é dono de
`pool_id`/`instance_id`/`agent_type_id`/`user_login`. Isso está escrito em **um** comentário no
repositório inteiro (`webhook.py:584-586`, que se abstém de `pool_id` de propósito) — e é violado:
webchat/webrtc escrevem `pool_id`, `activate_human_agent` o sobrescreve last-writer-wins, e o merge
do bridge escreve `tenant_id`.

**Três defeitos que a medição destapou (nenhum é o padrão, são consequências dele):**

1. **`ai-gateway/session.py:140` faz `HGET` numa chave que todos os seis escritores gravam como
   string JSON** — `WRONGTYPE`, não ausência, com a inversão perfeita: **só funciona quando o meta NÃO
   existe** (HGET em chave ausente devolve `None` → `pool_id="unknown"` → segue). Com o meta presente,
   a exceção é engolida em `:162-163` e leva junto as **três** emissões de sentimento. O comentário
   `:137-138` descreve a chave como hash e chama o caminho de "normal".
   ⚠️ **DORMENTE — mas o texto abaixo desta linha, escrito em 08-20, estava ERRADO em três pontos, e
   a medição de 2026-08-22 os desfez um a um.** Ver § "Nenhum step `reason` funciona no demo" e a
   § Sentiment Tracking do `CLAUDE.md`, ambas reescritas. Resumo do que mudou:
   › *"a trilha só é alcançada por `/v1/inference`"* — **a rota não existe**: é `POST /inference`
     (`main.py:286`), sem chamador nenhum no repositório. E **`/v1/reason` TAMBÉM chama**
     `update_partial_params` (`main.py:357`), então a trilha É percorrida, 124 vezes.
   › *"pipeline sem fonte"* — a fonte existe e é exercitada; ela morre ANTES, no 401 do provedor.
   › *"o `HGET` é o segundo problema"* — continua verdade, mas por outro motivo: ele é inalcançável
     porque o handler levanta antes da linha 357, não porque o endpoint não tem tráfego.
   O que **sobrevive** de 08-20: consertar o `HGET` às cegas teria dado um "fix" verde sem mudar nada.
   Isso segue valendo, e agora com causa nomeada.
2. ✅ **RESOLVIDO 2026-08-21** — o guard que se auto-anulava em `analytics-api/supervisor.py`.
   Contado antes: **8 metas vivos, 8 com `tenant_id`, 0 sem** ⇒ real no código, **sem alvo** nesta
   população; a palavra "exposição" não se sustentou, e o conserto foi fail-closed assim mesmo (0 em 8
   é evidência fraca de "nunca"). *Alcançabilidade DEMONSTRADA*, não inferida: o gate deu
   `P1 = HTTP 200` contra a imagem antiga. Ver `CHANGELOG.md` § "`session:{id}:meta` — o tenant deixa
   de ter fallback" e `infra/test/gate_supervisor_tenant_guard.sh`.
3. ✅ **RESOLVIDO 2026-08-21** — os três sites do `server.ts` (`session_transfer` que escreve;
   `supervisor_capabilities` e `copilot_state` que leem com o tenant como prefixo de chave). Trocados
   por um resolvedor único (`resolveSessionTenant`) que devolve `null` **mais o motivo**, nunca um
   default. Escrita recusa com 409; leituras devolvem vazio + `tenant_unknown`.

**TTL — truncamento silencioso 24 h → 4 h.** O trigger grava `SETEX 86_400` (`webhook.py:596`); na
alocação o merge do bridge relê e regrava com `_stl()` = 14_400 (`main.py:4571`). Ninguém compara TTL
antes de sobrescrever, e **nenhum escritor desta chave faz `TTL` antes de escrever** — o tratamento
de `-1 = ausência` existe no repo, mas para o hash do ContextStore (`webhook.py:1079-1084`), e nunca
chegou aqui. Cada merge também reprorroga a janela cheia, então sessão com muitas ativações nunca
expira por TTL.

**ESTADO 2026-08-21: sobra a PARTIÇÃO — os dois defeitos-consequência caíram.** Medido no mesmo
probe: **8/8 metas carregam `pool_id`**, que pela partição implícita é do bridge. A violação é a
regra, não a exceção — e é isso que o helper abaixo tem de fechar. O TTL também foi medido: **8/8 na
faixa ≤ 4 h, 0 acima**, mas isso *não prova* truncamento sozinho (sessão de webchat nasce com 4 h); só
uma sessão de TRIGGER que passou por alocação prova, e comparar exige saber a origem, não só o TTL.

**Conserto: nenhum dos seis pode virar dono único**, por razão estrutural — os canais só cobrem
webchat/webrtc; o bridge chega tarde (a janela pré-alocação é onde o escalate morria, `webhook.py:581-582`);
o trigger se abstém de `pool_id` por desenho. O conserto é **declarar a partição**: helper
`session_meta_merge(..., owner)` por linguagem que (i) rejeite campo de outro dono, (ii) preserve o
**maior** TTL (tratando `-1` como ausência), (iii) tenha semântica única de criação; mais um schema
declarativo com nome que não colida com o `SessionMeta` do ReplayContext. Os três defeitos acima são
independentes e podem cair antes.

---

## ~~Segmento que nunca fecha~~ — CONSERTADO 2026-08-18; sobraram DOIS resíduos

> **A causa raiz foi encontrada e corrigida** (`CHANGELOG.md` 2026-08-18): `conversations.participants`
> era publicado **sem chave** num tópico de 3 partições, o par joined/left do mesmo segmento invertia,
> e o `joined` inserido depois vencia a dedup nas duas tabelas. Conserto em duas partes
> (`key=session_id` + `ReplacingMergeTree(row_version)`), gate 3/3 na forma que antes era moeda.
>
> **Resíduo 1 — ~~o passado não foi reparado~~ REVISTO 2026-08-21: ele FOI reparado, nos dois papéis que
> importavam.** A contagem `primary` 5 · `queue` 2 · `specialist` 2 está **stale**. Re-medido em
> sessão fechada: **`primary` 0 · `specialist` 0 · `queue` 4** (e os 4 de fila são todos POSTERIORES ao
> fix, logo não são passado — são produção nova, ver a seção do diagnóstico refutado abaixo).
> O `DEFAULT` do `row_version` reparou onde as duas linhas ainda coexistiam, e aparentemente isso cobriu
> a população de `primary`/`specialist`. **A decisão sobre reprocessar deixa de ser urgente** — não há
> passado de `primary`/`specialist` a reparar, e o de fila não se conserta por reprocessamento (o evento
> de saída nunca foi produzido). Se algum dia voltar à mesa, segue valendo que reprocessar mexe em
> substrato de qualidade e pede o discriminador `origin` no lugar certo.
>
> **Resíduo 2 — `queue_config.skill_id` é decorativo** (defeito SEPARADO, não era a causa do segmento
> aberto). Ver a seção própria abaixo.
>
> O material abaixo é o histórico da investigação. Mantido só até o resíduo 1 ser decidido.

## Volume de sessões inexplicado — +167 contatos numa execução de e2e *(observado 2026-08-14, não medido)*

**Observação, não diagnóstico.** Duas leituras de `/analise/sessions` com **a mesma janela**
(07/08→14/08), **mesmo escopo** (`contacts`, toggle desligado) e **o mesmo build**, separadas por uma
execução de e2e:

| | `meta.total_contacts` |
|---|---|
| antes do e2e | **118** (3 páginas) |
| depois do e2e | **285** (6 páginas) |

O e2e deveria criar **uma** sessão com 5 segmentos — no máximo ~10 sessões somando as internas.
Apareceram **+167 contatos**. Ou a suíte rodou muito mais do que um cenário, ou algo produz contato
sozinho. Não há dado para escolher, e as duas explicações têm consequências opostas (uma é operação
normal, a outra contamina toda contagem, TMA e atribuição por pool do ambiente de demo).

**Primeiro corte** — quem são, por minuto de abertura e por pool/canal:

```
SELECT toStartOfMinute(opened_at) AS m, pool_id, channel, count()
FROM plughub_demo.sessions FINAL
WHERE tenant_id='tenant_demo' AND opened_at >= '2026-08-14 18:00:00'
GROUP BY m, pool_id, channel ORDER BY m DESC LIMIT 40
```

Se concentrarem num punhado de minutos e num pool de teste, é a suíte e o item morre. Se estiverem
espalhados no tempo, há produtor ativo — e aí o alvo é *quem* publica `conversations.inbound` sem
contato real. ⚠️ Conferir também `origin`: seed que escreve `origin='live'` já é achado conhecido
(`telas-design` §5) e inflaria exatamente esta contagem.

---

## ~~Segmento que nunca fecha — por SUPERAÇÃO~~ — DIAGNÓSTICO REFUTADO por medição *(2026-08-21)*

> ⚠️ **A "superação" não é a causa, e não há UM defeito aqui — há TRÊS.** Medição completa, com
> transcrição e log de produção, em
> [`docs/product/fila-janela-de-espera-2026-08-21.md`](docs/product/fila-janela-de-espera-2026-08-21.md).
>
> **Contagem de abertos em sessão FECHADA** (o escopo que julga — aberto em sessão viva é normal, e foi
> esse discriminador que faltou na medição de 14/08):
>
> | Papel | 2026-08-14 | 2026-08-21 |
> |---|---|---|
> | `primary` | 5 | **0** |
> | `specialist` | 2 | **0** |
> | `queue` | 2 | **4** — e **4 de 4 posteriores a 2026-08-18** |
>
> `primary`/`specialist` **caíram com o fix de 18/08** (chave no Kafka + `row_version`), que fechou a
> produção e reparou o passado desses dois papéis. O que sobra é específico de fila, é vivo, e são três
> mecanismos distintos:
>
> 1. **Re-entrância** — `activate_queue_agent` roda DUAS vezes para a mesma sessão (medido: dois
>    `marker SET` a 32 ms um do outro). A 2ª bate em `Skill already running`, retorna, e **apaga o
>    marcador que a 1ª escreveu**; a 1ª fica bloqueada esperando sinal que depende da chave apagada.
>    Dois defeitos de ORDEM: o `participant_joined` é publicado **antes** do guard (guard depois do
>    efeito não previne, aborta), e o `DELETE` **não confere posse**. Gatilho provável: escalate/transfer
>    chegando para sessão já enfileirada.
> 2. **Ativação única que nunca retorna** — a causa DOMINANTE. `marker SET` + `Activating` e depois
>    silêncio, sem `Skill already running` e sem `DELETE`. Contador: 2 ocorrências do guard em 6 h para
>    4 segmentos abertos ⇒ a re-entrância explica no máximo metade. **Causa não identificada.**
> 3. **O INVERSO — sessão que não fecha.** ✅ **MEDIDO EM VOLUME 2026-08-21** — e a descrição original
>    estava certa no conteúdo e **errada na evidência**. Detalhe completo em
>    [`conference-mechanics.md` § Problema 36](../guias/conference-mechanics.md). Três correções ao que
>    esta linha dizia:
>    · **`active` + `abandoned` não existe como linha.** O par é montado na LEITURA — `active` vem de
>      `!row.closed_at` no frontend (`ListaTab.tsx:294`, **não** lê `status`) e `abandoned` vem do
>      outcome do **segmento** pelo fallback `COALESCE(NULLIF(s.outcome,''), _seg_out.outcome_v)`
>      (`reports_query.py:895`). A linha da sessão está inteiramente nula. ⚠️ Procurar
>      `status='active' AND outcome='abandoned'` devolve **zero fabricado pelo recorte**; o instrumento
>      é `closed_at IS NULL`.
>    · **Volume: 5 nunca fechadas contra 10 fechadas**, entre as que têm segmento de fila `abandoned`
>      (população 522). É intermitência de ~⅓, não produtor ausente — e os 10 são a testemunha
>      obrigatória de qualquer conserto.
>    · **O caminho do reload não é este defeito.** Ver a seção nova abaixo.
>
> **E o maior de todos não é nenhum dos três: é o segmento que NÃO NASCE** — ver a seção
> *"A janela de espera não tem produtor"* abaixo.
>
> **Conserto do (1) tem forma conhecida:** `segment_id` determinístico derivado do `session_id` (hoje
> `uuid.uuid4()` por invocação, `main.py:5924`) faz invocação repetida produzir a MESMA linha e o
> `ReplacingMergeTree` deduplicar — sem guard novo. Mais `SET NX` no marcador e posse no `DELETE`.
>
> **Gate possível:** os casos são reproduzíveis pelo **e2e** (`e2e-inbound-*`,
> `e2e-conference-specialist-*`), não por teste manual. Testemunha obrigatória: os 47 segmentos de fila
> que fecham têm de continuar fechando.
>
> O material abaixo é o histórico da investigação de 14/08. Mantido porque a cadeia medida ali continua
> válida como observação — só a conclusão ("superação") não se sustenta.

Sintoma na tela: um contato **encerrado** exibe segmento com `live` + `join`, e o cabeçalho diz
`1 active`. A UI está honesta — `SegmentList.tsx:96` deriva `live` de `ended_at === null`.

**Impacto, e por que não é cosmético:** `agent_time_ms` filtra `duration_ms IS NOT NULL`, então
segmento que nunca fecha fica **fora** do tempo de agente; o `join` oferece entrar numa conferência
já destruída; e o contador de ativos mente. Se a espera em fila deve contar como tempo de agente, ela
está sumindo dos agregados; se não deve, então `role='queue'` não deveria ser segmento de agente. As
duas leituras não podem estar certas ao mesmo tempo — decidir isso faz parte do conserto.

**Cadeia medida (`tenant_demo`, sessão `61dd213c…`):**

| Passo | Resultado |
|---|---|
| escopo — segmentos abertos em sessão FECHADA, por papel | `primary` 5/597 · `queue` 2/11 · `specialist` 2/68 ⇒ **9 em 676 (1,3%)** |
| `queue` no tenant inteiro | **14 fechados**, 2 abertos ⇒ o caminho normal FUNCIONA |
| os 9, nomeados | **9 sessões distintas**, `close_reason` variado (`flow_complete` ×6, `agent_hangup` ×2, `customer_abandon` ×1), 2 canais, 6 skills, 5 dias |
| `segments` **sem `FINAL`** | 1 versão por `segment_id` ⇒ **inconclusivo** (merge pode ter comido a anterior) |
| `participation_intervals` | fila com `left_at = ∅` ⇒ **o evento nunca foi publicado** |

**Descartado — não redescobrir:** (a) *"a fila nunca fecha"* — fecha em 14 de 16; (b) *"é específico
do papel `queue`"* — `primary` e `specialist` também têm casos; (c) *"corrida de ordenação entre
tópicos"* — `segments` é escrita pelo par `participant_joined`/`participant_left` do **mesmo** tópico
(`clickhouse.py:376`), não por dois; (d) *"sobrescrita RMT"* — não sustentada: se a linha nunca
recebeu o rewrite E o evento também falta em `participation_intervals`, o fato não existiu.

**Diagnóstico:** o participante de fila nasce da SAÍDA do agente anterior (`sac_ia` sai
`16:09:28.912`, fila entra `16:09:28.965`) e desaparece quando o humano assume (`16:09:41`) — some
por **superação**, não por término negociado, e esse caminho não publica `participant_left`.

**Onde mexer:** produtor do `conversations.participants` (orchestrator-bridge / routing na alocação
que tira o contato da fila). ⚠️ Toca mecânica de conferência ⇒ pelo `CLAUDE.md`, exige atualizar
`docs/guias/conference-mechanics.md` § Histórico de Problemas e Correções **antes** de considerar
concluído. Gate precisa de testemunha: os 14 que fecham têm de continuar fechando.

---

## "Abandono" tem DOIS vocabulários, e eles discordam no caso mais comum *(medido 2026-08-21)*

> Reproduzido ao vivo duas vezes: cliente escalado do `sac_ia` para a fila **recarrega a página**
> enquanto espera. Sessões `e6056b6b…` (11 s) e `11c288a9…` (24 s).

A sessão **fecha corretamente** — `outcome='escalated_human'`, `close_reason='customer_disconnect'` —
e tem **1 segmento só**, o do `sac_ia`. O segmento de fila nunca nasce (é a D12, seção abaixo). O
efeito novo não é a espera não medida; é o **abandono não contado**:

| Superfície | Define abandono como | Este caso |
|---|---|---|
| Lista de contatos (`ListaTab.tsx:276`) | `close_reason ∈ {customer_abandon, no_resource, max_wait_exceeded, **customer_disconnect**, customer_hangup, session_timeout}` | **exibe "abandoned"** (confirmado na tela) |
| Relatório Fila/SLA (`reports_query.py:5762`) | entra com `q_count > 0`, conta com `q_outcome='abandoned'` | **invisível**: fora do numerador **e** do denominador |

Sem segmento de fila o contato não é "enfileirado" para o relatório que existe para medir fila — sai
dos dois lados da fração ao mesmo tempo, então nem a **taxa** de abandono acusa. Há **13** sessões com
`customer_disconnect` na população de 522.

**Decisão de produto pendente, e ela vem antes do código:** *cair enquanto espera é abandono?* A lista
diz que sim, o relatório de fila diz que não existe. As duas não podem estar certas. Enquanto a
pergunta não for respondida, unificar o vocabulário é escolher em silêncio.

**Ordem:** este item **depende** do produtor da janela de espera (D12) — sem segmento de fila não há
onde pendurar a contagem. Consertar só a leitura faria as duas telas concordarem sobre um contato que
continua sem registro nenhum de espera.

**Achado de domínio, de brinde e independente:** `close_reason` declara `session_timeout`,
`no_resource` e `system_error`, e os **três têm zero ocorrência** em 522 linhas (promessa sem
produtor — a mesma família de `session:{id}:sentiment`). Na direção oposta, `agent_closed` aparece
**14 vezes** e não está no domínio do `CLAUDE.md`.

---

## A janela de espera não tem produtor — e o segmento `role='queue'` nunca foi ela *(medido 2026-08-21; **D12** do ADR)*

**Contato real, manual, no WebChat** (`81d194ad-…-ce81b30e8343`): o segmento de IA fecha às
`18:14:46.926` com `escalated_human`, o humano entra às `18:15:08.276`. **21,35 s de espera, zero
registro** — nem segmento, nem `session_transitions`, nem nada. O pool tem `SLA (ms) = 300000`
configurado na tela; nada mede contra ele.

E não é vazamento: existem **52** segmentos `role='queue'` no tenant. É **ausência seletiva** — a fila
nasce em alguns caminhos e não no que um contato real percorre.

**O achado que reordena:** o segmento `role='queue'` **não é a janela de espera** — é o segmento de
trabalho do agente de fila. A transcrição de `sess-e2e-2920b0d1-…` mostra conversa real dentro da janela
(*"você está na fila… pode enviar mensagens"* → cliente responde → agente responde), e a duração de 6 s
é a do **flow**, não a do tempo aguardado (`_q_joined_at` é carimbado antes de `activate_native_agent`;
o fim é o retorno dessa chamada). **Refuta a linha *(espera)* da D9** do ADR, que atribuía a espera ao
`duration_ms` de `role='queue'`.

**Logo a janela de espera nunca existiu como fato** — nem no caso atendido (onde o segmento mede outra
coisa) nem no mudo (onde não há segmento nenhum).

**Decidido (D12):** a espera é fato de **ROTEAMENTO**. Quem tem o fato é o routing-engine, que já loga
as duas bordas (`Queued session=… — no agents available` / `Contact persisted to queue` na entrada;
`Queue cleanup: removed … reason=…` na saída). O bridge só sabe da espera quando decide entreter.

**Veículo = segmento, não tabela nova.** `session_transitions` **não comporta**: medido, é livro-razão de
suspend/resume com token (`resume_token`, `step_id`, `suspend_reason`, `resume_expires_at`), sem
`from_state`/`to_state` e sem enum de estado — *o nome é mais largo que o conteúdo*, mesma família da
colisão `SessionMeta` × `ReplayContext`. Alargá-la deixaria a maioria das colunas nula por linha; tabela
nova violaria *"nunca inventar o 3º mecanismo"*.

**Emenda junto (D12):** o agente de fila passa a ser `specialist`. ⚠️ **Efeito colateral a declarar:**
`agent_time_ms` filtra `role IN ('primary','specialist')`, então a reclassificação **move o tempo do
agente de fila para dentro do tempo de agente** e muda TMA/AHT do ambiente. Defensável (é trabalho de
IA), mas mudança de número sem pedido é *valor plausível* — entra declarada ou não entra.

⏳ **Aberto:** `retencao_humano` é **push** (confirmado na tela), então os 21,35 s são fila de verdade.
Mas em pool de **pull** existe uma segunda espera — "agente disponível que ainda não reivindicou" — e
para SLA as duas contam. Nenhuma tem registro hoje.

### ⚠️ Correção de 2026-08-28 — o produtor JÁ EXISTE para o tier MUDO; a D12 é GENERALIZAÇÃO

A frase acima (*"a janela de espera nunca existiu como fato… nem no mudo"*) está **errada na segunda
metade**. Lido no código:

| Peça | Onde já está |
|---|---|
| emissor do segmento de espera | `mute_queue.resolve_mute_exit` (`mute_queue.py:99-164`), no **routing-engine** |
| forma | `conversations.participants`, `role='queue'`, `agent_type='system'`, `participant_id="system-queue"` |
| início da espera | `first_queued_key` — **NX**, TTL 7 d |
| desfechos | `handoff` / `abandoned`, + `max_wait_exceeded` pelo `_emit_queue_timeout` |
| **carimbo de entrada nos DOIS tiers** | `add_queued_contact` escreve a MESMA chave, mesmo NX, mesmo TTL (`registry.py:2618-2633`) |

⇒ **Da fila atendida falta só o fato de SAÍDA.** O de entrada já é durável e sobrevive ao
re-enfileiramento. `resolve_mute_exit` abre com `SREM(unadmitted)` e **retorna `False`** para quem não
é fila muda (`:116-118`) — é chamado nos 4 pontos de saída e simplesmente não faz nada no tier
atendido. Cobertura parcial sem sintoma próprio: some no mesmo silêncio da espera não medida.

**Dois defeitos latentes no emissor que existe** (os dois consertados de graça pela D12):

1. `segment_id = str(uuid.uuid4())` (`:142`) — idêntico ao `:5924` do bridge. Protegido hoje só por o
   `SREM` ser one-shot; id determinístico remove a dependência do acidente.
2. `producer.send(...)` **sem `key=`** (`:137`), em tópico de 3 partições — o defeito mais caro já
   registrado neste repo. Não morde **hoje** só porque o evento é único (não há `joined` para vencer);
   a proteção é acidental, e some no instante em que alguém acrescentar o `participant_joined`.
   O bridge já usa `key=session_id` (`main.py:3513`). ⚠️ **Conserto independente da D12 — não adiar.**

   > ⚠️ **Emendado por medição (2026-08-21, P3) — e a emenda foi CORRIGIDA no mesmo dia.**
   >
   > **O que se mediu:** um contato que escala produziu **TRÊS** `participant_left` para a mesma
   > sessão (`handoff 0 ms` no `sac_ia`, `handoff 0 ms` no `retencao_humano`, `abandoned 8 273 ms`),
   > todos com o **mesmo** `segment_id` determinístico. Sem `participant_joined` nenhum: quem vence a
   > dedup do `ReplacingMergeTree` é quem chega por último, e isso só é determinístico dentro de uma
   > partição.
   >
   > ⚠️ **Mas duas daquelas três eram as FANTASMAS do portão morto.** Com o portão corrigido, uma
   > passagem pela fila emite **uma** vez (a 1ª emissão apaga o carimbo), e aquele caso específico
   > deixou de ser reproduzível. *Escrevi a refutação apoiado em evidência que o próprio fix removeu —
   > registrado aqui em vez de silenciosamente reescrito.*
   >
   > **O que sobrevive, e por quê:** *"o evento é único"* segue falso, agora por um caminho legítimo —
   > uma sessão que passa por **DUAS** filas (espera no pool A, handoff, transferência, espera no pool
   > B) recebe dois carimbos e emite duas vezes. A chave continua exigida para ordenar contra os
   > **demais** eventos da mesma sessão, que é o que a leitura de topologia assume.
   >
   > ~~🔎 **Resíduo NOVO que isto expõe:** `queue_wait_segment_id` é `uuid5(tenant, session_id)`…~~
   > ✅ **FECHADO 2026-08-24** — ver `CHANGELOG.md` § *"O segmento de espera passou a discriminar a
   > PASSAGEM"*. Duas correções ao que estava escrito aqui, ambas por medição:
   >
   > · **deixou de ser dedutivo.** O caso foi produzido (contato real `9403a14b…`): espera de
   >   24 118 ms em `retencao_humano` + 85 009 ms em `especialista_onboarding`, **duas emissões, uma
   >   linha**, id gravado idêntico ao `uuid5` previsto. Não era risco latente — era **perda de dado**
   >   acontecendo, e irrecuperável (o carimbo da passagem perdida é apagado na saída).
   > · **o conserto proposto aqui estava errado.** *"Incluir o `pool_id` de destino"* não serve: o
   >   pool é fato do CALL SITE (`main.py:286` emite com `event.pool_id or ""`), então duas saídas da
   >   mesma passagem por sites diferentes dariam dois ids para uma passagem — matando a idempotência
   >   que a D12 comprou. E não separa duas esperas no mesmo pool. O discriminador é o
   >   **`first_queued_ms`**, que já *significa* "esta passagem" (NX na entrada, DELETE na saída).

### A ordem de implementação é FORÇADA por um `anyIf`, não por preferência

`reports_query.py:5754`: `q_outcome = anyIf(outcome, role='queue')`, agregado **por sessão**. Com dois
segmentos `role='queue'` na mesma sessão (o da espera + o do agente, enquanto o bridge não migrar), o
`abandoned` do relatório Fila/SLA vira **escolha arbitrária entre duas linhas** — não dobra, **sorteia**.
`q_count > 0` e `maxIf(duration_ms)` toleram a coexistência; `anyIf` não.

⇒ **As duas emendas da D12 não são deployáveis em separado.** Ou landam juntas, ou o relatório passa a
mentir de forma não-determinística na janela entre elas — que é pior que o defeito atual, porque é
irreprodutível. *(O inverso também não serve: migrar o bridge primeiro esvazia o relatório, porque não
sobra nenhum `role='queue'`.)*

**Fases propostas:**

| # | O quê | Verde é |
|---|---|---|
| P0 ✅ | baseline rodada 2026-08-28 | **inalterada desde 08-21**: 469 `closed`/sem-fila · 27 `suspended` · **10** `closed`+ab · 8 `active` · **5** `never_closed`+ab · 3 nulas. População 522. Segmentos `queue`: 41 sessões com 1, 4 com 2, 1 com 3 (= 52) |
| P1 ✅ | `key=session_id` no emissor do routing (`mute_queue.py`) — isolado | escrito 2026-08-28; **não** era isolado nem cosmético: ver refutação acima |
| P2 ✅ | ver "as-built" abaixo | construído e validado 2026-08-21 |
| P3 ✅ | build + preflight + gate + testemunha | **fechado 2026-08-21**: coorte `role='queue'` 3 → 1 · `closed`+ab 10 → **11** · `never_closed`+ab **imóvel em 5** (previsto) · gate `test_queue_wait_segment.py` 4/4 com falseabilidade conferida |

#### P2 as-built (escrito 2026-08-28 · **construído, medido e corrigido 2026-08-21**)

> ⚠️ **O portão descrito abaixo nasceu MORTO, e o P3 só o encontrou porque mediu a população que NÃO
> devia ter linha.** O código dizia `if raw is None`, e `_decode` devolve `""` para chave ausente
> (`mute_queue.py:60-63`), nunca `None` — logo o `return False` jamais disparava e **todo contato
> roteado direto emitia `role='queue' outcome='handoff' duration_ms=0`**. Espera fantasma, exibida no
> drill como a *primeira participação do contato*. Corrigido para `if not raw`; coorte medida **3 → 1**
> linha de fila, com o caso legítimo (47 327 ms) intacto e taxa da fantasma antes do fix de **2 em 2**.
> A frase *"sem carimbo não emite nada"* abaixo era a **intenção**, não o comportamento.
>
> Duas lições que valem além deste item: (a) a fantasma era **invisível na query canônica do 36.2**
> (lá o predicado é `outcome='abandoned'`; a fantasma é `handoff`) — testemunha certa para o defeito
> que ela mede, cega para o que o conserto criou; (b) é a **Mudança 35 outra vez** (`""` do lado
> errado de uma guarda), quatro dias depois e no mesmo mecanismo ⇒ virou invariante de método no
> `CLAUDE.md` § Postura de Engenharia.

O desenho mudou ao implementar, e para melhor: **não foi preciso extrair função nem espalhar chamadas
novas**. `resolve_mute_exit` já estava plugado em TODAS as saídas de fila — só se recusava a agir no
tier atendido. A mudança foi **remover a recusa**.

**routing-engine**
- `mute_queue.resolve_mute_exit` → **`resolve_queue_exit`** (renomeada: o conteúdo ficou mais largo que
  o nome). O `SREM(unadmitted)` continua, mas virou **bookkeeping**, não portão; quem decide é o
  `first_queued_ms`. **Sem carimbo não emite nada** — ausência honesta, nunca `duration_ms` fabricado.
- `queue_wait_segment_id()` — `uuid5(NAMESPACE_URL, "plughub:queue-wait:{tenant}:{sid}")`.
- `key=session_id` no publish (P1).
- Call sites migrados: `main.py` (route/admitida · `_emit_queue_timeout` · drain periódico) +
  `kafka_listener.py` (drain com marker closed).
- **DOIS pontos de saída que nenhum dos 4 cobria, agora cobertos:**
  · `SessionClosedEventHandler` (contact_closed) → `abandoned` — **é o caminho do Problema 36.3**, o
    cliente que cai NA FILA; o handler não tinha producer, passou a receber `kafka_producer` e loga em
    WARNING se vier ausente;
  · drain com agente disponível → `handoff` — não era coberto pelo resolve do `route()`, porque com
    agente de fila ativo o contato é **sinalizado** (LPUSH) em vez de re-publicado no inbound, e o
    roteamento não roda de novo.
- Emitir duas vezes é **inócuo por construção**: o `first_queued_ms` é apagado na 1ª, e o id
  determinístico faria a 2ª ser a MESMA linha.

**orchestrator-bridge** (`activate_queue_agent`) — as três juntas, por causa do `anyIf`:
`role='queue'`→**`'specialist'`** · `pool_id`→**`_flow_pool_id`** (D10) · `segment_id` **determinístico**
(`uuid5`, namespace `queue-agent`, distinto do `queue-wait`).

~~**Não tocado, de propósito:** o emissor próprio do `_emit_queue_timeout` (passo 3). Unificá-lo agora
produziria emissão dupla; a lacuna (`max_wait` na fila atendida) está nomeada no docstring e aqui.~~
✅ **UNIFICADO em 2026-08-24 (fatia B).** E a premissa do "de propósito" **tinha expirado**: unificar
não produz emissão dupla, porque o segmento que o bridge fecha no tier atendido é `role='specialist'`
desde a própria D12 (`orchestrator-bridge/main.py:6007`) — o aviso descrevia o estado anterior à
reclassificação e sobreviveu a ela. Ver `CHANGELOG.md` 2026-08-24.

~~**Falta:** `docs/arcos/system-queue.md:134` cita o nome antigo · build dos dois serviços · gate.~~
**✅ Tudo pago em 2026-08-21** — `system-queue.md` item 4 corrigido (com nota da mudança de escopo),
build + `up -d` dos dois serviços, preflight de símbolo (`True True` / `1`), gate manual em 3 contatos
reais e gate re-executável `packages/routing-engine/src/plughub_routing/tests/test_queue_wait_segment.py`
(4 testes, Redis real, skip explícito lendo as DUAS variáveis). Registro: `CHANGELOG.md` +
`conference-mechanics.md` § **Mudança 37**.

**Ainda aberto neste arco** (nenhum é bloqueio do que foi entregue):
- ~~**Unificar o emissor do `_emit_queue_timeout`**~~ ✅ **2026-08-24 (fatia B).** `resolve_queue_exit`
  ganhou **um** parâmetro (`close_reason`) e o ramo `else` inteiro saiu; com ele saem os três defeitos
  de uma vez (`uuid4()`, publish sem `key=`, e a cobertura só do tier MUDO). Gate: 4 testes novos em
  `test_queue_wait_segment.py` (14 passed).
  ⚠️ **Medido, e as duas grandezas ficam separadas:** exposição prospectiva ALTA (o `retencao_humano`
  tem teto 1800 s e virou fila ATENDIDA em 08-24 — o próximo timeout cairia no buraco), dano
  histórico **ZERO** (1 timeout na vida do tenant, em 08-18, no tier mudo, registrado certo). A
  população **não contém** o caso consertado ⇒ medição em runtime é **inconclusiva** como gate.
  ~~⚠️ **Não observado:** o teste do tier atendido não foi visto vermelho pelo motivo certo — reprovou
  por defeito do harness. Fechar custa um `git stash` + build.~~
  ✅ **OBSERVADO em 2026-08-24.** Mutação nomeada e reversível em `_emit_queue_timeout` (a chamada a
  `resolve_queue_exit` re-gatilhada por `queue:agent_active`, reencenando o ramo `else`) → **1 failed,
  3 passed**, e o vermelho veio com a mensagem que o teste declara: *"fila ATENDIDA fechada por
  max_wait produziu **0** segmentos de espera"*. Previsão escrita antes de rodar (mute VERDE ·
  attended VERMELHO · sem-carimbo VERDE · guarda AST VERDE ⇒ **1 de 4**): **acertada nos quatro**.
  Revertido; `ORIG` no preflight e **14 passed**.
  ⚠️ **`git stash` teria sido o instrumento ERRADO, e o erro seria mudo:** com a fatia B já commitada
  o stash é no-op e o teste segue verde — e *verde por ausência de mudança* é indistinguível de
  *teste que não pode reprovar*, que é exatamente a proposição sob exame. Por isso o preflight não é
  `grep` de texto e sim `inspect.getsource` da função **carregada**: "o build não pegou" e "o teste é
  inútil" produzem os dois um verde e são conclusões opostas.
  ⚠️ **Reconciliação doc×código:** o comentário em `test_queue_wait_segment.py:328-333` afirmava *"é o
  único que estava VERMELHO antes desta fatia"* — a **afirmação era verdadeira, o status epistêmico
  não**: era dedução do autor, escrita depois do conserto, e o vermelho que ele de fato viu foi o do
  harness (`_FakeProducer` do fixture exigindo `key=` em todos os tópicos → `TypeError` engolido →
  `AttributeError`, documentado em `_run_timeout:374-380`). Agora é observação, com citação.
  🟢 **Subproduto:** o defeito original era mudo, e a fatia B **deixou testemunha** — `main.py:711`
  loga `wait_segment=%s`, que sob a mutação saiu `wait_segment=False` ao lado de `queue_agent=True`.
  Antes da fatia a variável não existia. É a § *Degradação NUNCA é silenciosa* honrada de graça.
  ⏳ **Encostado e não pago:** aquele caminho publica em outbound e `conversations.events` **sem
  `key=`**. Tópicos diferentes, fatia diferente.
- **O bridge tem a MESMA colisão** (`main.py:5963`, namespace `queue-agent`, `uuid5(tenant, session)`
  sem discriminador). Confirmado na medição de 08-24: o id gravado no `specialist fila_humano` bate
  com o `uuid5` previsto. **Hoje inalcançável** — exige DOIS pools com fila atendida, e só o
  `retencao_humano` tem endereço. Fatia própria, e ela tem uma pergunta que o routing não tinha: o
  discriminador do agente de fila **não pode ser wall-clock** (`_q_joined_at`), senão duas emissões da
  mesma ativação viram duas linhas e a idempotência morre. Achar o fato equivalente ao
  `first_queued_ms` é o trabalho.
- **O relatório Fila/SLA colapsa a sessão numa linha de fila** — `reports_query.py:5752`:
  `anyIf(pool_id, role='queue')`, `anyIf(outcome, …)`, `maxIf(duration_ms, …)`. Consequência viva:
  a segunda linha que o conserto de 08-24 passou a gravar **é descartada na leitura** (a tela mostra
  80 980 ms de 124 771 medidos), e onde as duas discordam o `anyIf` **sorteia**. Medido: 2 de 5
  sessões multi-linha já discordam hoje (`049167a2…` em outcome, `dce98532…` em pool) — era resíduo
  congelado, agora cresce com o tráfego. ⚠️ **O conserto não é `sum()`** — somar duas esperas contra
  um alvo é exatamente o que a D14 recusa. É a D14, e ela depende da D14.1.
  Probe: `infra/test/q_queue_multirow_impact.sh`.
- **`duration_ms` do segmento humano diverge da janela dos próprios carimbos.** Duas medições
  independentes: `26 448 ms` para 11:44:50→11:46:10 (80 s) e `25 519 ms` para 13:22:55→13:23:55
  (60 s). Duas ocorrências não é ruído. Não investigado — pode ser wrap-up fora da conta (o que seria
  correto e mal rotulado) ou carimbo de fim errado. Backlog próprio, fora do arco de fila.
- ~~**A linha `pool_id → _flow_pool_id` (D10) é no-op**~~ ✅ **PROVADA 2026-08-24**, e a lição é sobre
  atribuição: ela nunca dependeu do defeito 2 (esta linha dizia que sim — **falso**), e sim de haver um
  Queue pool ENDEREÇADO, que é ato de configuração. Medida com `queue_config.pool_id = fila_humano` num
  contato real (`…13315bc9968c`): `specialist | fila_humano` × `queue | retencao_humano`. Os ~176 s da
  IA de fila entram no TMA do `fila_humano` ⇒ a inflação de `+4,8 %` no `retencao_humano` **não ocorre**
  quando há pool de fila. Sem endereço (config atual do demo, por decisão do operador) não há para onde
  atribuir, e isso deixou de ser defeito: é o cenário 1, com fila muda honesta.
- **O efeito colateral de TMA ainda não foi observado em dado novo.** O `+4,8 %` em `retencao_humano`
  é recomputação hipotética sobre histórico; **não é retroativo** (linhas antigas mantêm
  `role='queue'`), então o número só deriva com tráfego novo. Nada a fazer — só não confundir a
  previsão com medição quando alguém reabrir o relatório.
- **36.2 segue sem causa.** Confirmado nesta rodada pela imobilidade prevista (5 → 5).

### SLA está no grão errado — é do SEGMENTO, não da sessão *(D14, 2026-08-28)*

Levantado por argumento de domínio (*"não conheço SLA por sessão; o normal é do segmento, senão soma-se
coisa diferente sem utilidade prática"*) e confirmado por leitura:

- `sla_target_ms` é **coluna de `sessions`** (`clickhouse.py:114`), populada por `parse_routed`;
  `segments` **não tem** a coluna.
- Os **três** leitores de SLA do repositório leem da sessão: `query.py:240`
  (`wait_time_ms <= sla_target_ms`), `reports_query.py:3802` (overlay) e `:5743` (Fila/SLA).

**Consequência concreta:** uma sessão carrega **um** alvo. Contato que espera 30 s por `retencao_humano`
(alvo 300 s), é transferido e espera 120 s por outro pool (alvo 60 s) só registra um dos dois — a
violação da segunda espera é **invisível**, e a média mistura populações não comparáveis.

**Destravado pela D12:** enquanto a espera não tinha registro por segmento, não havia onde pôr o alvo.
Agora há.

~~⚠️ **Mas ainda BLOQUEADO por um resíduo do próprio produtor (achado 2026-08-21)**~~
✅ **DESBLOQUEADO 2026-08-24.** O id do segmento de espera discrimina a passagem (`first_queued_ms` no
namespace do `uuid5`), e o caso de motivação **existe medido**, não mais suposto: contato
`27651d1b-…-dc9a3d1a0c0c` com 43 791 ms em `retencao_humano` + 80 980 ms em `especialista_onboarding`,
**duas linhas** no ledger. Ver `CHANGELOG.md` de 2026-08-24.

⚠️ **O que a D14 herda, e que só apareceu ao consertar o produtor:** o ledger agora tem os dois fatos,
mas `reports_query.py:5740-5760` **colapsa a sessão numa linha** antes de qualquer leitura de SLA —
`anyIf(pool_id, role='queue')`, `anyIf(outcome, …)`, `maxIf(duration_ms, …)`. Migrar `sla_target_ms`
para o segmento **não basta**: enquanto o colapso existir, o alvo por-segmento é comparado contra um
`wait_ms` que é o `max` de esperas diferentes, e o `pool_id` da linha é sorteado entre os dois. ⇒ **a
D14 é, em ordem: (i) parar de colapsar — uma linha por segmento de espera; (ii) `sla_target_ms` no
segmento; (iii) migrar os três leitores.** E (i) depende de (D14.1), porque não se decide a granulação
da comparação sem decidir o que o alvo mede.

✅ **(i) ENTREGUE em 2026-08-24** (`CHANGELOG.md`; gate `infra/test/gate_queue_report_per_wait.sh`,
VERDE). `_per_session` → `_per_wait`; `contacts`/`queued` em SESSÕES, `waits` (nova) e o resto em
PASSAGENS. Medido: 71 esperas em 59 sessões ⇒ **12 descartadas** (17%), **3 sessões** com pool ou
desfecho divergente onde o `anyIf` sorteava. Três exclusões de aderência entraram junto — só espera
**concluída, com alvo e não-abandonada** é julgável —, e `retencao_humano` saiu de **0,913** para
**0,6364**.

🔵 **(ii) ganhou EVIDÊNCIA NUMÉRICA, e ela veio da (i).** Decomposição medida do `retencao_humano`:
**48 esperas = 5 abertas + 10 SEM ALVO + 33 julgáveis**. As 10 pertencem a sessões cujo
`sessions.sla_target_ms` é 0/NULL — **enquanto o pool tem alvo configurado (300 000 ms) e a espera
aconteceu naquele pool**. 23% das esperas concluídas daquele pool são injulgáveis por o alvo estar
guardado na entidade errada. O argumento de domínio virou contagem; a (ii) deixou de precisar de fé.

**NÃO somar as duas esperas.** É a tentação óbvia do `maxIf`, e é o erro que a própria D14 nomeia:
somar esperas contra alvos diferentes dá número sem uso prático.
**Não medido:** a população atual não tem caso de duas filas, então o defeito é dedutivo, não
observado.

✅ **(ii) ENTREGUE em 2026-08-24** (`CHANGELOG.md`; `conference-mechanics.md` § Mudança 41). Coluna
`analytics.segments.sla_target_ms`, carimbada por `mute_queue.resolve_queue_exit` a partir do
`{t}:pool_config:{p}`. **As duas decisões do dono, tomadas antes de codar:**
  1. **pool de IA TEM alvo** — *espera é espera*, sem ramo por `agent_kind`. Fecha a sub-pergunta da
     D14.1 (§ abaixo), aberta desde 08-24. Gravado como teste (`test_ai_pool_also_carries_a_target`)
     para não ser reaberta por engano.
  2. **copiado no fechamento**, não resolvido na leitura — só a cópia guarda "o alvo do dia".
Gates: `test_queue_wait_sla_stamp.py` (6, routing-engine) + `test_segment_sla_column.py` (9) + 2 no
`test_consumer.py`. Falseabilidade por **mutação** em ambos os lados (produtor: `2 failed, 283`;
allowlist do parser: `2 failed, 593`). E2E em tráfego real: espera de 10 065 ms com
`sla_target_ms=300 000`, contra 5 esperas anteriores em `\N`.

⚠️ **É forward-only.** Linha antiga fica `NULL` e **não há migração possível** — o `first_queued_ms`
que daria o alvo é consumido na saída da fila. As 10 esperas injulgáveis medidas acima continuam
injulgáveis; o conserto vale para tráfego novo.

⚠️ **Os números de conformidade NÃO mudaram com a (ii)** — e isso é o esperado, não sintoma. Os três
leitores seguem lendo `sessions.sla_target_ms` (que a partir daqui é **projeção**, nunca fonte de
cálculo). É na **(iii)** que os números se movem: contar antes, por pool, e declarar.

✅ **(iii) ENTREGUE em 2026-08-25 — ARCO D14 (i→ii→iii) COMPLETO** (`CHANGELOG.md`). Os três leitores
passaram ao alvo do segmento. **Decisão do dono: saída (b), corte da série em data declarada** — a (a)
(fallback à sessão) preservaria a série misturando duas fontes num mesmo número, sem dizer qual
respondeu em cada linha.

**Contado ANTES de trocar** (`infra/test/q_sla_source_delta.py`): 51 elegíveis a **70,6%** → **1**
elegível; `retencao_humano` 34 a **64,7%** → 1. Encolher é o esperado; sem a contagem, viraria "o
relatório quebrou".

⚠️ **O probe errou na 1ª rodada e a lição é de método:** reescreveu o `_sla_eligible` de memória e
excluiu a espera ABANDONADA do denominador — o predicado real não a exclui (só o `within_sla`). Saía
95,7% onde a tela mostra 0,6364. Falseável, ramificado, honesto — e medindo a proposição **adjacente**.
Predicado de relatório se COPIA, não se reescreve.

**A época (`sla_source.SEGMENT_SLA_EPOCH`) não é o que exclui a linha antiga** — o `> 0` já excluiria.
Ela separa duas ausências idênticas na aparência: pré-produtor ("não medíamos") × pós-deploy sem alvo
(**`pool_config` expirado**, que é a opção "TTL" desta lista). A segunda virou contador
(`sla_unstamped` no `by_pool`) em vez de silêncio.

Gates: `test_sla_reads_the_segment.py` (12, asserta sobre o SQL EXECUTADO, não sobre o fonte) +
`infra/test/gate_sla_segment_target.sh`, que **insere** a sessão de duas esperas com alvos distintos —
população que o ambiente **não tem** (`discord = 0`), logo um teste de concordância passaria idêntico
sobre o código velho. Mutação reprovou os dois lados com os nomes previstos. Suíte **607 passed**.

#### ✅ D14.1 DECIDIDA (2026-08-24): `sla_target_ms` := **alvo de ESPERA em fila**

Decisão do dono do produto, com o inventário abaixo como evidência: *"é o tempo de espera alvo na
fila que leva ao SLA desejado numa fila de espera humana"*.

**Não foi escolha entre duas leituras — é o que o código já faz.** A medição derrubou a premissa
desta própria seção (ver § *"O que a medição corrigiu"*): a metade "atendimento total" do inventário
é **fantasma**, e o campo é consumido em comportamento **só como espera**.

✅ **Sub-pergunta DECIDIDA em 2026-08-24, junto com a (ii): "alvo de espera" vale para QUALQUER
fila** — o rótulo perde o "humana". O dono dissera *"fila humana"* e o mecanismo não distinguia; dos
63 segmentos `role='queue'` medidos, **19 estavam em pools de IA**. A alternativa (pool de IA carrega
`null`) foi recusada: o que torna uma espera injulgável passa a ser a CONFIG do pool, nunca o tipo do
agente. Gravada como teste no produtor, porque decisão que só vive em prosa é reaberta por engano.

##### O que a medição corrigiu nesta seção

⚠️ **A frase original — *"Consumidores: todos comparam com ESPERA"* — estava ERRADA por omissão**,
e listava só os 3 leitores de analytics. São **13 sites em dois campos**, e o lado "espera" inclui
**quatro que decidem comportamento**, que esta seção nunca mencionou:

| lê como | onde |
|---|---|
| atendimento total *(**fantasma** — ver abaixo)* | `schemas/agent-registry.ts:390` (contrato) · `configRecursos.json:29` (rótulo, 2 locales) · `supervisor.ts:202` · `ContactList.tsx:60,149` · `agent-assist-ui` ×3 |
| espera — **relatório** | `query.py:240` · `reports_query.py:3803` · `:5827` |
| espera — **comportamento** | `scorer.py:177` (aging + breach do ZSET) · `decide.py:287` (`sla_urgency > 1.0 → inf`) · `saturated.py:92/109/126` (ETA, `>2.0` → redirect + oncall) · `main.py:1055` (`avg_handle_ms = sla×0.7` → ETA publicada **ao cliente**) |

⚠️ **O lado "atendimento total" NÃO TEM CONSUMIDOR VIVO.** Medido: o `supervisor_state` tem duas
implementações e quem alimenta a tela é o endpoint HTTP (`useSupervisorState.ts:30` →
`server.ts:1617`), que devolve `sla{elapsed_ms:0, target_ms:480_000, percentage:0,
breach_imminent:false}` — **constantes, não cálculo**. Logo o campo nunca chegou àquela leitura.
Bug próprio, fora do escopo da D14.1 — ver § *Analytics e UI*.

- **Default do próprio formulário**: `30000` ms (`PoolsPage.tsx:603,755`) — 30 s só é alvo de espera.
  O código contradiz o rótulo até no valor que escreve.
- ⚠️ **E há um SEGUNDO default, em código.** `480_000` vive em `kafka_listener.py:218` ·
  `registry.py:3133` · `supervisor.ts:74`. **Um campo com dois defaults não tem default** — quem lê
  a tela e quem lê o runtime discordam quando a config está ausente.
  ⚠️ **Não é vazamento, é coincidência** *(corrigido na mesma sessão)*: os 2 pools em 8 min
  (`demo_ia`, `sac_ia`) **declaram** o valor no YAML de seed (`tenant_demo.yaml:159,166`). A
  primeira versão desta linha dizia *"o default do runtime vazou para o store"* — afirmação não
  medida.

##### ⚠️ A intenção que o dono descreveu para os valores ≥ 1 h já tem campo próprio

O dono lê os 18 valores altos como *"tempo máximo de espera por recurso de IA… o tempo que o canal
suporta ficar em mudo antes de cair"*. **Isso é `max_wait_s`/`channel_max_wait_s`, não
`sla_target_ms`** — e a diferença é operacional, não terminológica:

| | `sla_target_ms` | `max_wait_s` · `queue_max_wait_by_channel` |
|---|---|---|
| natureza | **alvo** (soft) | **teto** (hard) |
| ao ser ultrapassado | aging cresce até ele, `breach_bonus` acelera depois — o contato **sobe na fila** | o contato é **encerrado** (`max_wait_exceeded`) |
| encerra? | **nunca** | é o único que encerra |
| mudo por canal | não | sim (`voice 300 · webrtc 300 · webchat 1800 · whatsapp 14400`; **`0` é VETO**) |

⇒ `limite_entrega` com 7 dias **não segura ninguém por 7 dias**; quem seguraria é o `max_wait_s`,
que aquele pool não declara. O valor alto só torna o aging inerte. Licença de IA também não passa
por aqui — a admissão tem portão próprio (`{t}:admission:kind:ai`, `cause="quota"` na porta).

**Já morde:** `aprovacao_deploy` está com **86 400 000 ms (24 h)** — coerente com o rótulo, absurdo como
espera ⇒ **aquele pool não pode violar SLA** e a conformidade dele é 100% por construção (verde que não
pode ficar vermelho). `retencao_humano` está com 300 000 ms, configurado como espera. **Duas intenções
no mesmo campo, em pools diferentes.**

**⚠️ Contado (36 pools, 2026-08-28): é METADE do parque, não um outlier.** 18 pools entre 15 s e 10 min
(alvo de espera, plausível) × **18 pools com ≥ 1 hora** (prazo de processo, impossível violar como
espera): 5 em 1 h, 9 em 24 h, 3 em 48 h e **um em 7 DIAS** (`limite_entrega`). O default de 30 s é usado
por **2** pools ⇒ o parque foi configurado à mão, seguindo o rótulo.
E significa que **todo número agregado de SLA hoje mistura duas populações incomparáveis**.

~~**A divisão coincide com o TIPO de pool** … **O discriminador é o mesmo da D13** (contato × interno),
o que torna a correção tratável sem adivinhar intenção pool a pool.~~
❌ **REFUTADO por medição (2026-08-24, `infra/test/q_sla_target_inventory.py`).** `purpose` separa
**2 de 18**: dezesseis pools da faixa ≥1 h são `purpose=contact`. O discriminador real é **como o
pool é ENTRADO**, e ele não é um campo só:

| entrada | n | o que o alvo significa lá |
|---|---|---|
| contato espera atendimento (`push` + canal de cliente) | 18 | **espera na fila** — o único caso em que o campo é o que a D14.1 decidiu |
| trigger de workflow (`webhook` em `channel_types`) | 12 | prazo do processo |
| item de trabalho (`dispatch=pull`) | 4 | prazo do item |
| I/O de `delegate`/`collect` | 2 | prazo de resposta do **cliente** |

⚠️ **Os 2 últimos não têm discriminador no registro do pool.** `limite_retorno` e
`portabilidade_confirmacao` são `push` + canal de cliente, iguais aos 18 — o que os separa é serem
alvo de `delegate` (`skill_limite_entrada_v1:378`, `skill_limite_entrega_v1:70` via
`channel_policy`, `agente_confirmacao_portabilidade_v1:5`), fato do **skill que chama**, não do pool.
Só o próprio VALOR os distingue hoje, o que é circular para migração automática. Qualquer migração
em massa precisa tratá-los à mão ou introduzir o discriminador que falta.

⚠️ **E a definição do dono estreita mais: são 2 pools, não 18.** *"fila de espera humana"* — dos 6
pools humanos, só **`retencao_humano`** (5 min) e **`especialista_onboarding`** (10 min) são fila de
espera (`push`); os outros 4 são `pull`, que é item de trabalho. Os 18 da faixa de espera são **16 de
IA**. Consistência interna que vale registrar: os 4 humanos `pull` já estão em valores de
prazo-de-processo (1d/2d) — **o parque foi configurado seguindo uma distinção que o código não faz.**

**Três SLAs no domínio, um campo e meio:** espera (fila, grão de segmento) · atendimento total (o que o
rótulo promete) · tempo máx. de resposta por mensagem (**já tem campo próprio** — *Max. reply time* —, e
isso é a evidência de que a separação é natural).

##### O aging inerte: mecanismo VIVO, dano medido NULO *(2026-08-24, `q_sla_band_wait_witness.sh`)*

Com alvo de 24 h, `sla_ratio` após 10 min de espera é 0,0069 ⇒ o aging vale ~0,7% do fator e o
`breach_bonus` é **zero para sempre**; o ramo de prioridade máxima absoluta (`sla_urgency > 1.0 →
inf`) é inalcançável em qualquer horizonte prático. Previu-se que isso fosse **latente** (nenhuma
espera em pool da faixa ≥1 h). **Previsão ERRADA:** 16 das 63 esperas estão lá — `formfill_demo_ia`
9 · `limite_processo` 5 · `aprovacao_credito` 1 · `wrapup_detached_ia` 1.

Mas o dano é outro fato, e ele é **nulo**: as esperas nesses pools são de **5 a 14 segundos**, e
quem espera 8 s não precisa de aging. O `sla_ratio` real ficou entre 0,00005 e 0,0017.

⚠️ **Lição de método — o instrumento não sabia responder o que importa.** Os três ramos eram
VIVO / LATENTE / INCONCLUSIVO, e a verdade não é nenhum: *"contato esperou aqui"* e *"a espera foi
longa o bastante para o aging importar"* são **dois fatos**, e o probe colapsou os dois num ramo só.
Só o segundo é dano. Irmão de *"um contador de ausência precisa de contador-testemunha"*: um
predicado que responde pergunta adjacente passa por resposta.

Único caso que chega perto: **`aprovacao_credito`, 4,9 min contra alvo de 2 dias**. Com alvo de
espera (5 min) aquele contato estaria em `sla_ratio ≈ 0,99` — aging máximo, quase breach. Havia
**um item só** na fila, então não existia ordem a inverter. Mecanismo exercido, dano zero por sorte
de população — não por proteção.

##### Composição dos 63 segmentos de espera, pela definição decidida

| classe | n | pertence ao Fila/SLA? |
|---|---|---|
| fila humana `push` (`retencao_humano` 41 · `especialista_onboarding` 2) | **43** | sim |
| espera em pool de **IA** (6 pools) | **19** | ⏳ sub-pergunta acima |
| item de fila `pull` (`aprovacao_credito`) | **1** | não — é prazo de item |

⇒ o relatório Fila/SLA mistura hoje 43 que pertencem com 20 que não: **32% de contaminação**, e é
isso que a **D14-i** encontra ao parar de colapsar. Testemunha de presença: 63 segmentos, 9 pools,
56 sessões — o instrumento estava medindo.

**Resíduos que a medição jogou fora, registrados sem perseguir:** `sac_ia` tem **2 esperas de 0 ms**
(a espera fantasma da Mudança 37, que consta como corrigida — ou é dado pré-correção, ou a correção
não pegou tudo) e `retencao_humano` mantém os **5 segmentos abertos** já conhecidos.

~~⚠️ **Decidir o que `sla_target_ms` É vem ANTES de migrar os leitores para o segmento**… Saídas:
renomear o rótulo … ou **partir em dois campos**…~~
✅ **Decidido 2026-08-24 (acima): := alvo de ESPERA em fila.** E a saída escolhida é a barata, por
um motivo que só a medição deu: **"partir em dois campos" não se justifica**, porque o segundo campo
não teria consumidor — o lado "atendimento total" é fantasma (`server.ts:1628` devolve constantes).
Não se cria campo para alimentar tela que não lê.

**Trabalho que a decisão gera, em ordem, e nada dele é a D14 ainda:**

1. ✅ **Contrato — FEITO** *(conferido por grep em 2026-08-25; esta linha dizia "pendente")*.
   `schemas/src/agent-registry.ts:322-328` já traz o comentário corrigido, incluindo a nota de que a
   afirmação anterior (*"mede o atendimento como um todo"*) **nunca teve consumidor** e o aviso de
   não reintroduzir a leitura sem criar o campo que a suporte. O campo irmão (`:416`) declara a
   ortogonalidade. **Item era eco de plano, não estado.**
2. ✅ **Rótulo — FEITO** *(conferido por grep em 2026-08-25)*. `configRecursos.json:22` diz *"Alvo de
   espera em fila (ms)"* e o `slaHint` (`:29`) já separa alvo × teto por extenso. ⏳ **Não conferido:
   o locale `en`** — o item pedia os DOIS, e só o `pt-BR` foi medido.
3. ✅ **Default nomeado e barulhento (2026-08-24)** — não eram dois, são **sete** sites. Feito:
   `SLA_TARGET_MS_FALLBACK` em `routing-engine/models.py`, citado por `kafka_listener.py` e
   `registry.py`, os dois agora **logando** quando o fallback dispara (o campo é obrigatório no
   contrato Zod, então o ramo só existe para evento malformado — e fabricar alvo de espera em
   silêncio alimenta aging, breach, ETA ao cliente e aderência de SLA). `supervisor.ts` ficou
   comentado, não alterado: é contrato de tool MCP com consumidores não mapeados.
   ⏳ **Restam cópias — mas a contagem "3" estava ERRADA nos dois sentidos** *(medido por grep em
   2026-08-25)*:
   · `orchestrator-bridge/instance_bootstrap.py:709` — ✅ **existe**, é o placeholder do bootstrap e é
     a única cópia legítima em aberto;
   · `mcp-server/tools/bpm.ts:281` — ❌ **NÃO EXISTE**: o arquivo não tem nenhuma ocorrência de
     `sla_target_ms`. Ou já saiu, ou a referência nasceu errada;
   · `config-api/seed.py:158` — ⚠️ **não é cópia do default**: é o VALOR semeado da chave
     `sla_default_ms`, que é o órfão sem leitor logo abaixo. Contá-la aqui dobrava o mesmo item em
     duas listas;
   · 🆕 `orchestrator-bridge/registry_syncer.py:19` — **quarta ocorrência, que a lista não tinha**
     (exemplo de YAML em docstring). Inócua, registrada para a contagem fechar.
   🔴 **E um achado que não é duplicação: `sla_default_ms` NÃO TEM LEITOR.**
   `routing_config.get("sla_default_ms")` não aparece fora dos testes — a chave é semeada
   (`config-api/seed.py:157`), cacheada (`routing_config.py:47`), emitida
   (`kafka_emitter.py:11`), testada, e **exibida ao operador** na tela do namespace `routing`
   (`ROTEIRO_TESTES.md:209`). Editar não muda comportamento nenhum. Botão que promete efeito e
   não tem é pior que default duplicado. ⚠️ Remover exige cuidado: o
   `smoke_config_routing_orphan_keys.sh:118` a usa como **canário** de "namespace intacto".
4. **Os 34 pools que não são fila de espera** — ⏳ decisão pendente, mas a opção que estava escrita
   aqui **caiu por medição (2026-08-24)**.

   ~~a opção que a medição sugere é `null` onde não é fila… faz os 4 sites caírem no ramo sem alvo~~
   ❌ **Não existe "ramo sem alvo".** `PoolConfig.sla_target_ms` é `int`, **não** `int | None`
   (`models.py:244`), e o `kafka_listener:231` converte ausência em `SLA_TARGET_MS_FALLBACK` antes
   de construir o modelo. Nenhum dos consumidores jamais vê `None` — logo **"anular o campo" não é
   expressável hoje**, e os dois valores candidatos significam outra coisa:

   | valor escrito | o que o runtime faz |
   |---|---|
   | `null` / ausente | vira **480 000 ms** (`kafka_listener:231`, com WARNING). "Sem alvo" vira "8 minutos" — silencioso exceto pelo log |
   | `0` | **pior, e em quatro lugares**: `scorer.py:177` `max(0,1)=1` ⇒ `sla_ratio` = elapsed em ms ⇒ aging no teto e `breach_bonus` sem limite · `decide.py:287` ⇒ `sla_urgency > 1.0` **sempre** ⇒ prioridade absoluta · `saturated.py:74` voz ⇒ `>2.0` ⇒ **redirect p/ site secundário + oncall** a cada espera, e `int(0×1.5)=0` ⇒ ETA 0 · `main.py:1075` ⇒ `avg_handle_ms=0` ⇒ **ETA 0 ms publicada AO CLIENTE** |

   ~~🔴 **Achado colateral — os dois escritores do campo DISCORDAM sobre o `0`.**~~
   ✅ **FECHADO 2026-08-24 — e não eram dois escritores, eram QUATRO sites.** A conta original
   (`kafka_listener:231` preservando `0` × `registry.py:3149` com truthiness) estava certa e
   **incompleta**; ao medir por `grep` de derivação (não de menção) apareceram mais dois, ambos
   mudos e ambos em `main.py`:

   | site | fonte | ausente → | `0` → |
   |---|---|---|---|
   | `kafka_listener` | evento `pool.registered` | fallback, com log | **preservava `0`** |
   | `registry.refresh_pool_snapshot` | snapshot / pool_config | fallback, com log | fallback (truthiness) |
   | `main._pool_sla_target` | cache `pool_config` | `None`, **mudo** | preservava `0` |
   | `main._queue_position_and_eta` | cache `pool_config` | **`0`**, **mudo** | preservava `0` |

   🔴 **A última linha era o defeito ATIVO, e não precisava de `0` configurado para morder.**
   `avg_handle_ms = int(0 × 0.7) = 0` ⇒ **ETA de `0 ms` publicada AO CLIENTE**, e ausência ali não é
   evento malformado: `{t}:pool_config:{p}` **tem TTL**, logo o gatilho é o RELÓGIO. O `0` era portão
   fechado; este não era portão nenhum.

   Conserto: predicado único `models.resolve_sla_target_ms` (`int | None`, nunca `0`, log nomeando
   QUAL violação), com o `SLA_TARGET_MS_FALLBACK` aplicado **no call site** dos dois que precisam de
   `int` — a fabricação aparece onde acontece em vez de ficar escondida no predicado. Contrato
   corrigido junto: `QueuePositionUpdatedEventSchema.estimated_wait_ms`/`sla_target_ms` eram
   `nonnegative()` **obrigatórios**, o que tornava *"não sei"* inexpressável e legitimava `0` —
   enquanto o schema irmão `RoutingResultEventSchema:163` já declarava o MESMO campo como
   `positive().nullable().optional()`. Gate: `test_sla_target_predicate.py` (**23 passed**).

   ⚠️ **O que dava valor ao gate era a linha do `0`**, e por um motivo de método: um teste de
   *"todos concordam"* só julga se a população contiver o caso em que eles DISCORDAVAM. Sem ela a
   mesma tabela passaria idêntica sobre o código velho.

   🟢 **Achado de graça — a "fonte única" já tinha dois consumidores discordando.**
   `_write_queue_context:1120` guardava (`if avg_handle_ms > 0`) e omitia a tag; `_publish_queue_position`
   publicava `estimated_wait_ms: 0` sem guarda. Uma função só não faz fonte única quando o valor de
   "não sei" é indistinguível de um valor medido: **a unicidade estava no cálculo, não no vocabulário.**
   E nenhuma das duas funções de `main.py` tinha teste algum — a ETA que fala com o cliente estava sem
   cobertura.

   **As opções reais do passo 4, então:**
   (a) tornar o campo **Optional de ponta a ponta** — Zod, `PoolConfig`, e ramo `None` explícito nos
   sete consumidores. É o único que torna *"não medimos"* distinguível, e custa o que custa.
   (b) **não anular nada e mudar quem LÊ** — os 34 pools mantêm o número; o Fila/SLA passa a separar
   as populações, que é o que a **D14-i** faz de qualquer forma. Custo marginal ≈ zero.
   (c) migrar os valores altos para `max_wait_s` — é o passo 5, e não anula nada.
   ✅ **DECIDIDO (b) em 2026-08-24, pelo dono do produto.** Nada é anulado; muda **quem lê**. O passo
   4 portanto **não gera trabalho próprio** — é absorvido pela **D14-i**, que separa as populações no
   relatório. Consequências que a decisão fixa:

   - os 34 pools mantêm o valor que têm; nenhuma migração em massa, e o problema dos 2 pools de
     `delegate` I/O sem discriminador (`limite_retorno`, `portabilidade_confirmacao`) **deixa de ser
     bloqueio** — não há migração automática a fazer;
   - **nenhum comportamento de roteamento muda.** Aging, breach, ETA ao cliente e as ações de
     saturação continuam lendo exatamente o que liam. Isso é o valor da opção: a correção fica no
     relatório, que é onde o defeito é;
   - a opção (a) fica registrada, **não descartada** — reabre se alguém precisar da distinção no
     COMPORTAMENTO (ex.: pool de processo que não deveria participar de aging nenhum). Hoje não há
     esse requisito;
   - ~~🔴 **a divergência de escritores sobre o `0` NÃO é fechada por (b)** e continua aberta como
     fatia própria.~~ ✅ **Fechada em 2026-08-24** — ver o bloco acima. A frase *"hoje ninguém
     escreve `0`, então não morde"* estava **certa sobre o `0` e errada sobre a fatia**: o site que
     mordia era o da ETA, e ele não precisava de `0` na config — bastava a chave de cache expirar.
##### Achados adjacentes da fatia do predicado (2026-08-24) — registrados, NÃO perseguidos

✅ **1. O TTL de `{t}:pool_config:{p}`** — **FECHADO em 2026-08-25** (fonte única no Config API,
namespace `session`, 86 400). Ver `CHANGELOG.md` e `docs/arcos/instance-bootstrap.md` § TTL.

Duas correções ao diagnóstico que estava escrito aqui, e a segunda mudou o desenho:

- **Não era o reconcile de 5 min que fazia o bridge vencer — é o `_heartbeat_tick`, a cada 15 s.**
  O 86 400 não perdia uma corrida no boot: era sobrescrito quinze segundos por vez, indefinidamente.
  Provado parando o serviço (TTL decai 3587 → 3546 → 3462 e não reseta; religado, volta a 3594).
- **A consequência principal não é o cache do routing-engine — é o apagão.** Se a chave expira,
  `get_candidate_pools` devolve vazio e TODO contato é enfileirado. E a expiração exige o bridge
  fora do ar por mais que o TTL, o que torna o defeito **condicional**, não corrente: o item entrou
  na fila como *"perde dado agora"* e isso não se sustentou.

⚠️ **Achado que este item não continha, e é maior que ele:** o `SessionConfigCache` do bridge
**nunca leu** o Config API — `CONFIG_API_URL` ausente do compose (todos os outros serviços a têm),
default hardcoded em `localhost:3500` (porta da analytics-api) e GET sem `?tenant_id=`. Três causas
empilhadas, cada uma suficiente, todas degradando para "usa o default". As seis chaves de TTL do
namespace `session` eram editáveis na tela e **inertes**. Conferido depois: os valores do store são
idênticos aos defaults, então ligar a leitura não mudou mais nada.

🆕 **2. `estimated_wait_factor` (0.7) e `congestion_sla_factor` (1.5) são o 2º e o 3º órfão do
namespace `routing`.** Semeados (`config-api/seed.py:163,170`), cacheados (`routing_config.py:59-60`),
testados — e **sem leitor**: `main.py` fixa `* 0.7` em código e `saturated.py` fixa `* 1.5`. Editar na
tela não muda comportamento nenhum. Mesma família do `sla_default_ms` já registrado no passo 3 acima,
que agora são **três** botões que prometem efeito e não têm.

🆕 **2b. `wrapup_hold_ttl_s` é o caso INVERSO — leitor sem chave** (medido 2026-08-25).
`kafka_listener.py:411` faz `routing_config.get("wrapup_hold_ttl_s", 90)`, mas a chave **não está**
no `_DEFAULTS` do `routing_config.py` **nem no seed**. Resultado: resolve sempre para o `90` inline,
e o operador não a vê na tela do namespace `routing`.

Vale registrar porque é a metade que a heurística dos órfãos não pega — procura-se chave sem leitor,
e esta é leitor sem chave. As duas violam a mesma invariante por lados opostos: *"todo campo de
config tem superfície na tela"* (§ Configuration) e o contrato escrito no próprio
`routing_config.py:21` (*"ou ela existe no seed E tem leitor, ou não entra"*). Conserto: semear a
chave, ou assumir o 90 como constante de código e tirar o `routing_config.get`. **Decidir qual
antes de codar** — semear sem necessidade cria o 4º botão inútil.

🆕 **2c. `queue_default_agent_type_id` e `queue_default_skill_id` continuam no config store**
(medido 2026-08-25 em `GET /config/session?tenant_id=tenant_demo`, ambas `""`). Saíram do código e
do seed em 2026-08-24 (defeito 2), mas o store é DB-owned: remover do seed não remove a linha. É a
pendência herdada *"resíduos do defeito 2"*, agora com evidência. Conserto é um DELETE pela API
oficial, não edição de seed.

🔴 **3. `test_expire_returns_the_slot_even_after_the_lease_expired` está VERMELHO, e é anterior a esta
fatia** (medido: nada em `router.py` foi tocado). Asserta `claimed_via == "semaphore"`, e o código
devolve `"record"` — porque a emenda **D6** (2026-08-04) inseriu o registro durável de posse
**ANTES** do semáforo (`router.py:1140`, que o diz literalmente). O teste não foi atualizado.
⚠️ **Não é discrepância cosmética.** O comentário em `router.py:1158` declara que a linha do
`semaphore` é *"a MEDIDA da lacuna 2"* — item reivindicado, sem lease, vaga presa. Com o `record`
respondendo primeiro, **esse ramo ficou inalcançável neste cenário**, e o instrumento da lacuna 2
parou de ser exercido sem que nada além deste vermelho o dissesse. Casa com a lacuna que a **Camada F**
do arco de detach já declarava (*"a lease não foi medida"*).
⏳ **Consertar não é trocar a string para `"record"`** — é decidir onde vive a medida da lacuna 2
depois que o registro durável passou a responder antes dela.

5. **Onde os valores altos deviam estar** — se expressam teto, o campo é `queue_config.max_wait_s`
   (ver tabela alvo × teto acima). Migração à mão: os 2 pools de `delegate` I/O não têm
   discriminador no registro.

### Decisão de nome: **`queue` := espera** (tomada 2026-08-28, com inventário como evidência)

Reconfirma a emenda 2 da D12 (agente de fila → `specialist`), que a passagem de 08-29 tinha reaberto
propondo um papel novo `wait`. O que decidiu foi o **inventário dos 52 segmentos** — `role='queue'`
já carrega DOIS significados hoje, e cada caminho herda um lado pronto:

- **`queue` := espera** ⇒ os **19** segmentos do routing (mute + timeout) **já estão corretos**, zero
  migração; o Fila/SLA não muda de query e passa a admitir a fila atendida; e o routing vira produtor
  único, com o padrão de **evento único** que torna segmento de fila aberto **estruturalmente
  impossível** (os 5 abertos são todos do bridge; os 19 do routing têm zero).
- **`wait` novo** ⇒ preservaria TMA/AHT, mas migraria os 19, mexeria no Fila/SLA, deixaria
  `role='queue'` significando "trabalho do agente" contra o próprio nome, e manteria os 5 abertos.

⚠️ **Efeito colateral MEDIDO (2026-08-28), não estimado — vai declarado no CHANGELOG.**
**A medição certa é POR POOL, não por tenant** — TMA é lido por pool, e a primeira versão deste item
declarava a média do tenant (+3,44%, 188 967 → 195 464 ms sobre 507 sessões), que não corresponde a
tela nenhuma. Por pool:

| Pool | sessões | `agent_time_ms` hoje | depois | Δ |
|---|---|---|---|---|
| `retencao_humano` | 146 | 456 083 ms | 477 968 ms | **+4,8%** |
| *(pool vazio)* | 1 | 0 | 9 ms | ruído |

**Nenhum outro pool muda.** Toca **27 sessões**, das quais **11 hoje reportam tempo-agente ZERO** e
passarão a reportar — os casos em que a IA de fila trabalhou e nada contou. Os 5 segmentos abertos
ficam de fora pelo filtro `duration_ms IS NOT NULL`.

### ⚠️ Requisito que a pergunta "TMA não é por pool?" revelou — a D10 entra no MESMO pacote

O segmento do agente de fila carrega hoje o pool de **DESTINO** (`main.py:5919-5923`) — a sobrecarga
que a própria D10 nomeia. Logo a reclassificação ingênua joga o tempo da IA de fila **dentro do TMA do
pool humano**, concentrado em quem usa fila atendida. O conserto é a D10 (pool do segmento = quem
ATENDE ⇒ o segmento do agente passa a carregar `_flow_pool_id`), e ela **tem de entrar junto**, senão
o arco publica uma inflação que ele mesmo sabe como evitar.

⚠️ **E a D10-attribution é NO-OP neste ambiente até o defeito 2 ser consertado.** Com
`queue_config = {"skill_id": "skill_fila_v1"}` **sem `pool_id`**, `_flow_pool_id = "" or pool_id`
(`main.py:5841`) resolve para o **próprio `retencao_humano`** — não existe pool de fila separado para
onde mandar o tempo. **Ordem imposta:** defeito 2 (tenant default suprimido) **antes** da atribuição
da D10 ter destino. Enquanto isso, o +4,8% no `retencao_humano` é o número a declarar.

> ⚠️ **Armadilha de medição paga nesta rodada, e ela quase publicou o número errado.** A 1ª versão da
> query deu *"16 afetadas, +1,19%"*. As duas estavam erradas: `sumIf` sobre coluna **`Nullable`**
> devolve **NULL** (não 0) quando nenhuma linha casa, então (a) sessão cujo ÚNICO tempo é o segmento
> de fila tinha `hoje = NULL`, e `depois > hoje` virava NULL — o `countIf` **pulava** exatamente a
> população mais afetada; e (b) `avg` ignora NULL, então as duas médias saíram sobre **denominadores
> diferentes**. Conserto: `coalesce(sumIf(...), 0)`. A confirmação não foi argumento, foi aritmética —
> `16 + 11 (só-fila) = 27`, e o delta bateu com o cálculo à mão feito antes. *Irmão do
> `clickhouse-agregado-vazio-devolve-default`, na versão inversa: o vazio não devolve default, devolve
> NULL, e move o denominador em silêncio.*

⚠️ **PREVISÃO OBRIGATÓRIA do P3, escrita antes de rodar:** o P2 conserta a **duplicação** (5 sessões) e
**não deve mover** o `never_closed = 5` nem os 4 segmentos abertos em sessão fechada. Medido
2026-08-28: a interseção entre "tem fila duplicada" e "sessão nunca fechou" é **1**, não 5 — o número
igual era coincidência, e 4 das 5 sessões que não fecham têm **um único** segmento de fila, **já
fechado**. Sem essa previsão no papel, o P3 verde-mas-imóvel será lido como *"não aplicou"*. Tabela
completa em [`conference-mechanics.md` § Problema 36.2](../guias/conference-mechanics.md).

⚠️ **O que o P2 NÃO conserta — três populações distintas, agora separadas pelo contador:**
(a) duplicação (5 sessões, 4 delas fechando normalmente) — **é o que o P2 fecha**;
(b) sessão que nunca fecha com fila abandonada (5) — independente, causa não identificada;
(c) segmento de fila aberto (5 segmentos, **4 em sessão FECHADA** = Problema 34) — inverso de (b),
não variante. Renomear para `specialist` não fecha nenhum: viram `specialist` abertos.

---

## ~~O tenant default de fila é suprimido pelo `skill_id` legado~~ ✅ **RESOLVIDO 2026-08-24**

> ✅ **Fechado, e o defeito era maior do que este título.** Medindo, a supressão do default era um
> sintoma: o problema é que `queue_config` carrega **três fatos de escopos diferentes** (endereço ×
> política × endereço legado morto) e **quatro** call sites perguntavam *"há quem atenda?"* testando a
> presença do OBJETO. Conserto: tier decidido por ENDEREÇO (`queue_address`, predicado único), política
> de espera do pool ortogonal ao tier, default de tenant **removido** (vocabulário pré-slot, zero
> usuários, promessa falsa na tela) e `queue_pool_id or pool_id` trocado por recusa alta. Consequência
> viva que isto fechou: **licença de IA retida durante espera que ninguém atende**. Validado em contato
> real (`a7275f7f…`): `unadmitted` ganhou a sessão e `admission:kind:ai` a perdeu. Detalhe, medições e
> os dois erros de previsão no `CHANGELOG.md` de 2026-08-24. Probe: `infra/test/q_queue_config_inventory.py`.
>
> **Das três frentes listadas abaixo, as três foram feitas** — config-time (aviso na tela ao endereçar
> pool sem slot promovido), semântica (gatilho é "não há endereço", não "objeto vazio") e fallback
> (recusa alta). O texto original fica como registro do diagnóstico.

Estado do pool no agent-registry (não no YAML): `"queue_config": {"skill_id": "skill_fila_v1"}`, **sem
`pool_id`**. A tela mostra `Queue pool: — Tenant default —` e o texto de ajuda diz *"Empty = tenant
default"*. **É falso nesta configuração.**

| Linha | O que acontece |
|---|---|
| `main.py:5735` | `if not queue_cfg:` → o objeto é **truthy** (tem `skill_id`) ⇒ **ramo do tenant default PULADO** |
| `:5788-5789` | `agent_type_id = explicit_skill_id` = `skill_fila_v1` |
| `:5841` | `_flow_pool_id = "" or pool_id` = **`retencao_humano`** |
| `:5845-5855` | `resolve_flow_for_agent` → `None` → ERROR, retorno cedo, nada criado |

Log casando linha a linha: `Agente de fila NÃO ativado: destino=retencao_humano fila=retencao_humano
agent=skill_fila_v1`.

**O `skill_id` legado não é inerte.** A tela o descreve como *"preserved, but it does not resolve the
deploy"* — verdade pela metade: ele não resolve o deploy **e** bloqueia o default. Agrava:
`PoolsPage.tsx:927` envia `queue_config` sempre que **qualquer** dos dois campos está preenchido, então
limpar o Queue pool na UI **não** esvazia o objeto enquanto o legado estiver lá.

Duas famílias conhecidas num defeito só: *"preservado" lido como inerte* (irmão de *"campo morto pode
gatear tela viva"*) e **valor plausível** — `queue_pool_id or pool_id` transforma config ausente num alvo
que parece razoável e nunca pode funcionar, convertendo lacuna de config em erro de runtime num log que
ninguém lê, com o contato esperando mudo.

**Três frentes independentes:**

- **config-time:** a tela do pool sabe se o queue pool escolhido tem slot promovido — deve **recusar ou
  avisar**, não deixar salvar tratamento de fila que não roda;
- **semântica:** o gatilho do tenant default deve ser *"não há pool de fila resolvível"*, não *"o objeto
  `queue_config` é vazio"*;
- **fallback:** `queue_pool_id or pool_id` deve **recusar alto**, não adivinhar.

---

## Papel de participante tem DOIS vocabulários declarados, e o masking usa o estreito *(medido 2026-08-21)*

| Schema | Valores | Quem usa |
|---|---|---|
| `ParticipantRoleSchema` (`schemas/src/common.ts:81`) | 5 — **sem `queue`** | `SessionParticipant.role` **e `authorized_roles` do masking** (`audit.ts:247`) |
| literal inline em `ContactSegmentSchema.role` (`contact-segment.ts:62`) e `participant_role` (`:115`) | 6 — **com `queue`** | segmento e evento Kafka |

O segundo **não referencia** o primeiro: repete a lista à mão. Divergência de um valor não é descuido
isolado — é o resultado esperado de duas fontes para o mesmo vocabulário. Há ainda um **terceiro**
vocabulário adjacente: `author_role` da mensagem, que na transcrição de `sess-e2e-2920b0d1-…` exibe
`PRIMARY` para o participante cujo segmento é `queue`.

**Onde morde:** `authorized_roles` é `z.array(ParticipantRoleSchema)`, default `["evaluator","reviewer"]`.
Logo o papel `queue` **não pode nem ser expresso** numa política de masking — nem para autorizar, nem
para negar. E o agente de fila é justamente quem conversa com o cliente (é o único step `reason` sobre
fala de cliente no repositório).

**Supervisor, o furo simétrico:** está nos dois enums e **nenhum caminho o emite** (a D9.1 do ADR já
registra). `_part_role = "specialist" if conference_id else "primary"` (`main.py:4503`) é a única decisão
de papel do sistema, e é binária. Consequência que a discussão de 2026-08-21 acrescenta: **o supervisor
hoje lê a sessão SEM ser participante** — `supervisor_state`, `supervisor_capabilities`, `copilot_state`,
`/api/inject-context` são tools/endpoints, não entrada na conferência. Logo o acesso **não aparece no
roster** (não é auditável como participação) e **não há evento de entrada para anunciar** — a exigência
de *"o Console deve avisar que um supervisor entrou"* só é implementável depois que o supervisor virar
participante de verdade.

Encadeia com o pré-requisito 2 do **R8** (*"produzir o vocabulário"*).

---

## Ler um processo = ver seus CONTATOS em sequência, num lugar só *(ADR fechado 2026-08-12; **F0 ✅ + F1 ✅ + F1b ✅ + F2 ✅ + F3 ✅** — restam F4, F5)*

> ⚠️ **Cabeçalho corrigido em 2026-08-14.** Dizia *"nada implementado"*, e F0 (`774b257`) e F1
> (`43ab761`) estavam commitadas desde 12-13/08, sem entrada no `CHANGELOG` e sem nada aqui. A sessão
> de 14/08 mediu antes de escrever: gate do `collect` em vigor no container, slot `current` do
> `limite_entrega` executando `type: collect`, gates `5/0` e `18/0`, e o merge provado por
> `journey_aliases`. As-built e os quatro achados no `CHANGELOG.md`; plano das fases restantes em
> [`docs/product/historico-unificado-plano-execucao.md`](docs/product/historico-unificado-plano-execucao.md).
>
> **Decisão aberta #1 FECHADA por ausência:** o `collect` é lazy — sem clique não nasce sessão, logo
> um `collect` expirado não conta como contato porque **não existe**. Em troca, "a perna do output
> como sessão" passou a ser **condicional ao engajamento**, o que o ADR não previa.

> **Desenho fechado em [`docs/adr/adr-historico-unificado-duas-visoes.md`](docs/adr/adr-historico-unificado-duas-visoes.md).**
> Kickoff de F0: [`docs/product/historico-unificado-kickoff.md`](docs/product/historico-unificado-kickoff.md).
> Handoff de origem: `docs/product/journey-contatos-em-sequencia-handoff.md`. O detalhe (D1–D12,
> achados medidos, o que foi rejeitado) vive no ADR; aqui fica só o que está pendente e por quê.

**O que já funciona** (medido 2026-08-12, não reinvestigar): `/reports/journeys?root_session_id=…`
devolve `session_count: 3`; `/reports/sessions?root_session_id=…` devolve as três encadeadas por
`origin_session_id`; a Vista Processos renderiza `ROOT WebChat → análise → entrega`. O agrupamento
por PROVENIÊNCIA está correto ponta a ponta — **não há defeito de journey a consertar.**

**O achado que reordenou tudo.** O pedido virou quatro perguntas — direção do acesso, prova de saída,
perna do workflow, output com confirmação — e nas quatro **o fato já está persistido sem superfície**.
Uma delas está persistida e **desligada por um gate assimétrico**: `handle_collect` não honra
`customer_resumable`/`resume_policy`, embora o schema tenha os campos e o engine os envie (registrado
em `skill_limite_entrega_v1.yaml:41-42`, que por isso parqueia com `delegate`). Fechar esse gate dá,
de uma vez: output-com-confirmação, a perna do output **como sessão**, a direção **outbound**
(`spawn_reason='collect'`) e pertença ao processo **por proveniência**.

Consequência: a pertença se reparte, e só metade precisa de merge.

| Caso | Como entra no processo |
|---|---|
| **Output ativo** — nós avisamos o cliente | `collect` → proveniência, automático (F0) |
| **Acesso espontâneo** — o cliente volta por conta (acesso 2) | `journey_merge` (F1) |

**Fases — ordem revisada em 2026-08-14 (F2 subiu na frente de F1b):**

F2 é a menor, é backend puro sem consumidor a quebrar, e é a **única** que F4 não contorna; F1b só
destrava um filtro de F3 e custa o inventário de ~40 leitores. Não há dependência entre as duas, então
trocar a ordem custa zero.

- ~~**F0 · conserto do gate do `collect`**~~ ✅ **2026-08-14** (código de 12/08). `handle_collect`
  honra `customer_resumable`/`resume_policy` (`webhook.py:1956-2010`); `parquear_resultado` é
  `collect` com `resume_policy: auto` e 168h, promovido no slot `current` do `limite_entrega`.
- ~~**F1 · `journey_merge` no intake**~~ ✅ **2026-08-14** (`skill_limite_entrada_v1.yaml:362-372`,
  step `unificar_journey` no ramo `policy == "auto"`). Provado por aresta ativa em `journey_aliases`.
  **Falta só o intake de PORTABILIDADE** (`agente_portabilidade_intake_v1.yaml`: zero ocorrências de
  `journey_merge`; vai de `avaliar_politica_retomada` direto a `retomar_processo`) — fatia pequena,
  **fora do caminho crítico**. ~~+ endereço de entrada (`endpoint_id`)~~ **REMOVIDO 2026-08-12 — ver F1b.**
- ~~**F1b** · `entrou por`: first-write-wins em `sessions.pool_id`~~ ✅ **2026-08-14**. Critério = menor
  `timestamp` (não ordem de chegada: `inbound`/`routed`/`queued` são tópicos distintos). Fonte única —
  o fallback `_pool` de `_fetch_sessions` foi removido; `_fetch_pools_queue` teve a precedência
  invertida para o segmento de fila (**já estava errado** em 6 de 15 sessões, antes desta fase). ABAC
  **precisou de conserto**, não de conferência: 52 dos 67 contatos sairiam do escopo de 2 usuários
  reais → `_session_scope_clause` (predicado único, união entrou ∪ sem-pool ∪ participou, +9/−0).
  Gates `probe_entry_pool_base.sh` + `probe_entry_pool_fww.sh` (7/0). As-built no CHANGELOG.
- ~~**F2** · `root_session_id` em `/reports/segments`, **com isenção da janela de data**~~ ✅
  **2026-08-14**. Subconsulta em `sessions` (a coluna não existe em `segments`) com o mesmo union-find
  de `/reports/journeys`; `meta.window_applied` marca o ramo isento. Gate
  `infra/test/probe_segments_journey_window.sh` (6/0, diferencial de 4 leituras com janela absurda).
  **Achado 6 medido e descartado**: 723 segmentos com pool, **0** com `pool_id` vazio — o defeito
  derivado do código não tem amostra; `_apply_pool_scope` **não** foi tocado.
  ~~**Dívida aberta junto:** `/reports/journeys` … sem o marcador `window_applied`~~ ✅ **2026-08-14**
  — e ao medir apareceu um **defeito vivo maior ao lado**: `/reports/sessions?root_session_id=`
  **não** tinha a isenção que o CHANGELOG da F2 lhe atribuía (só `origin_session_id` a tinha), então
  o drill de um processo fora da janela devolvia **0** sessões, não "menos". Os dois consertados +
  marcador nos dois endpoints. Gate `infra/test/probe_journeys_window_applied.sh` (7/0).
  **Resíduo declarado:** `session_id` + janela que o exclui segue devolvendo 0, e pela mesma lógica
  ("pedir UM não é listar") também deveria ser isento. Não foi mudado junto porque o
  `probe_segments_journey_window` da F2 usa exatamente esse comportamento como **testemunha** de que
  a janela funciona — mudá-lo derrubaria o discriminador de outro gate. Item próprio: decidir a
  isenção e trocar a testemunha daquele probe na mesma fatia, nunca só a primeira metade.
- ~~**F3** · visão 1 (contatos + chip de processo + direção)~~ ✅ **2026-08-14**. As-built no
  `CHANGELOG.md`; gates `probe_f3_contact_list_contract.sh` (4 ramos) e
  `probe_i18n_contacts_parity.sh` (692 chaves). **Três fatias de BACKEND que o kickoff não previa**
  (`entry_pool_id`; `journey_id`+`journey_session_count`; `attended_pool_ids`) — nenhuma é
  implementável no front, e as duas últimas são pós-passes justamente para não encostar na query
  principal. Saíram: ANI/DNIS (colunas, filtros, i18n, tipos), `AnaliseProcessosPage.tsx`,
  `OriginSelector.tsx`, «Processos» do menu, e o seletor Inbound/Outbound que não filtrava nada.
  **Resíduos abertos por esta fase**, cada um item próprio:
  - **filtro por direção** — o seletor removido era falso; um de verdade é parâmetro novo sobre
    `spawn_reason` em `/reports/sessions`. Não foi contrabandeado na F3.
  - **`/analise/sessions?session_id=…` não é honrado.** Três telas linkam para lá (`WorkItemsPage`,
    `DeliveriesTab`, `SchedulesMonitorPage`) e a página ignora o parâmetro — abre a lista sem o
    recorte. Defeito **anterior** à F3, agora fácil (a página já lê `useSearchParams` para o chip).
  - **medição que ficou por rodar** (não bloqueia a F3): divergência `sessions.pool_id` × pool do
    1º segmento, **partida antes/depois** do deploy da F1b em 14/08. A tela já provou que o ingest
    novo está certo (`sac ia → nps ia` numa sessão que antes exibia entrada `retencao humano`), mas
    falta o número que separa *"resíduo histórico a backfillar"* de *"a F1b tem produtor escapando"*.
    Divergência **depois** do corte > 0 seria furo. Query pronta no histórico da sessão de 14/08.
  - **mais duas páginas mortas**, no mesmo critério que matou as duas removidas: `ContactsPage.tsx`
    (nenhuma rota a monta desde que `/contacts` virou redirect) e `AnaliseContatosPage.tsx`
    (importada em `routes.tsx`, mas `analise/contatos` é `Navigate`). Não removidas junto por
    disciplina de escopo — a F3 já tinha aberto três fatias não previstas.
- **F4** · visão 2 (pivô, árvore/cronologia num componente com toggle, internas dobradas).
  ⏳ **Ordem e pré-requisitos: ver § "🧭 Ordem de trabalho PROPOSTA" no topo deste arquivo.** Resumo de
  uma linha, para não duplicar o plano: o F4 **não é bloqueado** pelos resíduos da D14.1, e sim por
  uma **medição** (rodar `limite_entrega` e reler `spawn_reason`) e uma **decisão de texto**
  (`contatos` × `acessos do cliente`) — as duas baratas, as duas antes de renderizar.
- **F5** · `ContextStorePersister` — fase própria, desenho fechado no ADR §3 (mascarado, estado final,
  ctx de processo a cada close, foto inteira).

**Decisões abertas** (ADR §4): ~~`collect` que expira sem engajamento conta como contato?~~ **fechada
2026-08-14, por ausência** · ~~texto do rótulo do chip~~ **fechada 2026-08-14 (F3.3)**, e o achado é
que ele é **condicional** em `meta.window_applied`, não permanente · `uniq(root_session_id)` como
métrica de cabeçalho (lacuna registrada, não fechada).

### ✅ O chip de PROCESSO SOME para usuário escopado — DECIDIDO E IMPLEMENTADO (2026-08-26)

> ⚠️ **O título dizia "decisão aberta" enquanto o corpo dizia decidido, implementado e provado.**
> Corrigido em 2026-08-30, depois de a discrepância ter feito o item ser levantado como pendência
> numa varredura por títulos. É o item 5 da § *"Erros de método"* no seu caso mais barato.

> **Triado, e a triagem herdada estava errada em dois pontos.** A passagem 09-12 mandava marcar
> *"Include internal sessions"* e ver se a contagem voltava a 108. **Não pode**: o toggle manda
> `scope=all` e acrescenta LINHAS, mas o cabeçalho é sempre `meta.total_contacts` — a própria
> `ListaTab.tsx:45` diz *"a contagem de contatos do cabeçalho não muda por isso"*. E a passagem
> afirmava `accessible_pools = []` (irrestrito) para o admin; o token traz **5 pools**.

**Metade 1 — `120 → 86` NÃO é defeito.** São os 34 contatos em pools fora dos 5 do admin. O
`108 → 84` que a passagem registrou como regressão é a própria mudança de ambiente que ela
descreve duas seções acima. **Encerrado.**

**Metade 2 — o chip é defeito próprio, e a causa é comum à metade 1.** Medição (dois scripts novos,
re-executáveis):

| | anon | admin (5 pools) |
|---|---|---|
| `meta.total_contacts` | 120 | **86** |
| linhas com `journey_session_count > 1` | 29 | **0** |
| linhas com o campo presente (testemunha) | 120 | **86** |

`page_size` (50 × 200) é inerte — não é a contagem-derivada-da-página de `/reports/segments`.
Discriminador: das 29 linhas multi, **6 estão VISÍVEIS ao admin** e reportam `1`, contra `4`, `3`,
`4`, `4`, `4` no escopo aberto. Logo não é "as linhas sumiram": é
**`journey_session_count` computado SOB o recorte** — `_attach_session_journey_chip` inclui
`_session_scope_clause(db, accessible_pools)` (`reports_query.py:1248-1250`).

**E isso é DELIBERADO** (`:1179`: *"só tenant + escopo de contato + **ABAC** + origem"*) — o que o
docstring recusa é herdar período/canal/pool, não a ABAC. A razão é boa: contar as 4 revelaria o
tamanho de um processo que toca pools inacessíveis. **O que não estava previsto é a consequência:**
com `n=1`, o front esconde o chip (`ListaTab.tsx:269`, `> 1`) e a tela passa a **afirmar** *"este
contato não pertence a processo nenhum"* — exatamente a mentira que o mesmo docstring proíbe quatro
parágrafos abaixo (*"Falha ⇒ `None`, nunca `1`"*). A regra foi aplicada à FALHA e não ao RECORTE.

**✅ DECIDIDO pelo dono em 2026-08-26 — caminho (a), o marcador de existência.** Código escrito;
falta `build` + baseline + gate (ver o fim desta seção). As três opções, para o registro:

- **(a) marcador de existência, sem tamanho — ESCOLHIDA.** Booleano
  `journey_has_scoped_out_members` ao lado da contagem escopada; chip volta como `PRC-xxxx · 1+`,
  dizendo *"há mais, você não alcança"* sem revelar quantos nem quais. Preserva a intenção da ABAC e
  mata o silêncio.
- **(b) tirar a ABAC da contagem** — alinhado ao §1 do docstring (*"o processo não encolhe porque
  alguém olhou uma semana dele"*), mas revela o TAMANHO de processos que tocam pools fora do escopo.
  ⚠️ **Reclassificada na mesma sessão:** deixou de ser alternativa e passou a ser a **primeira fatia
  da (d)** — e a fatia cuja ordem está errada (ver a seção da (d) abaixo).
- **(c) manter como está** e consertar só o texto — o rodapé condicional já existe
  (`lista.processFootnote`); precisaria dizer que o chip é escopado. Mais barato, menos honesto: o
  chip continua AUSENTE, e rodapé não explica ausência.

**O que decidiu:** o dado que falta ao front não é o tamanho, é a EXISTÊNCIA — e `journey_id` já
viaja em toda linha (`:1228`/`:1241`), inclusive nas 6 escopadas.

**Como está implementado** (`reports_query.py`, `_attach_session_journey_chip`): a ABAC **saiu do
`WHERE`** e virou o predicado dos `countIf`. A população agregada passa a ser o processo inteiro; o
que SAI continua escopado (os quatro números são `countIf(acc)`, idênticos aos de antes). O único
fato novo é `count() > countIf(acc)`. Sem restrição o predicado colapsa em `1` e o marcador sai
`false` — **medido, nunca `null`**: `false` é *"não há membro fora do seu alcance"* e `null` é *"não
consegui medir"*, e colapsá-los faria o front tratar quem tem visão completa como caso desconhecido.

No front (`ProcessChip.tsx`, superfície ÚNICA — lista + breadcrumb): `hasProcess` ganhou a segunda
razão para pivotar (`total > 1 || scopedOut`), e **sob o marcador a QUEBRA não é desenhada**. Isso
não é economia de espaço: a quebra também é escopada, e uma classe inteira fora do alcance
apareceria como `0` — publicar `· 3 + 0` afirmaria *"não há etapa interna"*, o mesmo defeito um nível
abaixo, convivendo com o marcador que anuncia o contrário. Sob o marcador: um número e o `+`.

✅ **PROVADO em 2026-08-26** — `infra/test/probe_process_chip_scoped_marker.sh`, com
`operator@plughub.local` (o admin tem 22 pools e **não exerce** o defeito). Medido:

```
linhas anon=120 · auth=80 · chave presente 120/120 e 80/80
anon (sem ABAC): false=120 · true=0 · null=0
população derivada: PERDEM membro=6 · escopo cobre=74 · impossíveis=0
  4→1 · 3→1 · 4→1 · 4→1 · 4→1 · 5→1   (todas com o marcador `true`)
VERDE
```

São **as mesmas 6 linhas** que a triagem tinha encontrado reportando `1` contra `4`/`3`/`4`/`4`/`4`.
A contagem continua escopada (`→ 1`), o marcador acende, e os 74 processos cobertos saem `false` —
as duas proposições opostas provadas na mesma execução. Baseline da analytics-api: **620 passed**
(617 + 3 testes novos).

⚠️ **A COBAIA É O `operator`, NÃO O ADMIN — e isso é permanente, não circunstancial.** O gate sai
**INCONCLUSIVO** com o admin (`PERDEM membro=0 · escopo cobre=29`), e está CERTO em sair: escopo
largo não refuta o defeito, apenas não o exercita. Quem rodar isto com o admin e ler o exit 2 como
"não há defeito" repete o erro que a v1 deste gate cometeu ao contrário.

🔴 **A v1 deste gate reprovava código CORRETO, e a lição é de método.** Ela tomava a INTERSEÇÃO
(*"linhas multi visíveis"*) como sendo *"processos com membro fora do escopo"* — verdade só enquanto
o admin tinha 5 pools. Com 22, imprimiu `4 → 4 · false` em 29 linhas e chamou as 29 de defeito, em
DOIS ramos (mudo + vazamento). **A população de um gate se DERIVA do dado, nunca se assume do
recorte**: `auth_n < anon_n` ⇒ marcador deve ser `true`; `==` ⇒ deve ser `false`; `>` ⇒ impossível.
Sem nenhuma linha `<`, não julga. Agravante: o erro foi cometido **depois** de eu consertar o
idêntico no `q_scope_delta_stage2.sh` (que decidia por "interseção não-vazia" e imprimia
`anon=4 auth=4` nas próprias linhas de prova). Ver D14.1 na § Postura de Engenharia.

✅ **METADE VISUAL CONFIRMADA (2026-08-26)**, com `supervisor@plughub.local` escopado em
`limite_ia` + `limite_retorno` — previsão escrita antes, **4 de 4**:

| linha | antes | depois |
|---|---|---|
| 3× `PRC-8c47326d` (total 5, alcança 3) | `· 3` | **`· 3+`** |
| 2× `PRC-faf611c6` (total 3, alcança 2) | `· 2` | **`· 2+`** |
| `…0-4ecf5c0f49c0` (total 5, alcança **1**) | **`—`** | **`PRC-ca44ca47 · 1+`** |
| `…4-0ddf15a02b31`, `…b-a3ae157e0fad` (`scoped_out=false`) | `—` | `—` |

A terceira linha é o defeito em estado puro: a tela AFIRMAVA *"não pertence a processo nenhum"* sobre
um processo de cinco sessões. A quarta é a testemunha negativa **na tela** — se elas tivessem ganhado
chip, o marcador estaria aceso onde não há nada fora do alcance.

⚠️ **A cobaia foi o `supervisor`, não o `operator`** — o `operator` tem o escopo certo mas não alcança
a tela (portão de papel no `Sidebar.tsx:121`, seção própria abaixo). Montar o caso exigiu conceder
dois pools ao supervisor pela tela de Acesso.

**Scripts (rodam do host, sem build):**
`infra/test/q_process_chip_delta.sh` (grade 2×2 token × page_size, com testemunha de presença
ao lado do contador de ausência) · `infra/test/q_scope_delta_stage2.sh` (o discriminador
`multi_ids ∩ auth_ids`, que separa *"linhas removidas"* de *"contagem colapsada"*, e imprime os
claims do token — foi ele que derrubou o `accessible_pools = []` herdado).
⚠️ O `q_scope_delta_stage2.sh` ainda imprime o aviso *"`accessible_pools: []` significa
IRRESTRITO"*, que era relevante sob a premissa herdada e hoje só confunde — corrigir ao tocar.
🔴 **E o aviso está CERTO como fato de código, embora fosse enganoso como diagnóstico:**
`pool_auth.py:157-161` implementa exatamente essa convenção (`[] → None → irrestrito`). Ver a
seção do furo abaixo, onde ela deixa de ser curiosidade e vira decisão.

### 🟡 (d) "ABAC no conteúdo, lista aberta" — ADIADA; **peça 1 ✅ em 2026-08-30**

> **Segue adiada**, e a **peça 1 está FEITA** (2026-08-30): as quatro rotas de conteúdo
> recortam por pool, com decisor único e gate `probe_session_content_scope.sh` (6 ramos,
> vermelho antes de verde). Detalhe no `CHANGELOG.md` da data.
>
> **A tabela abaixo está DESATUALIZADA e foi mantida como registro** — ela é de 2026-08-26 e
> já estava errada em dois pontos a favor quando foi remedida hoje: `POST /join` ganhou
> portão em 08-27 e `/customers/{id}/360` passou a repassar `accessible_pools`. Estado real
> em 2026-08-30, medido: das 11 linhas, as que faltavam eram **quatro**, e as quatro foram
> fechadas.
>
> **O que a peça 1 NÃO fez, de propósito:** abrir as listas. A ordem registrada abaixo
> (*portões de conteúdo primeiro, abrir as linhas depois*) fica intacta — o que mudou é que
> a primeira metade dela existe. A peça 3 (filtro de relevância) só existe SE as listas
> abrirem, e continua sem dono. Ver a § *"SETE decisões do dono — sessão de 2026-08-30"*, #6.

**A proposta.** Tirar a ABAC da montagem do chip *e da lista* — todos enxergam todos os contatos e
segmentos — e aplicá-la **na exibição do conteúdo**, de modo que só quem tem escopo faça drill-down.

**O diagnóstico está certo, e é o da casa:** `accessible_pools` carrega HOJE dois fatos num campo só
— **relevância** (*"esta é a minha operação"*, por isso o supervisor vê 86 e não 120) e
**autorização** (*"posso ver isto"*). É a mesma família de *"papel são DOIS fatos"* e de *"tem config
≠ tem endereço"*.

**A medição (estática, por DERIVAÇÃO, 2026-08-26) mostrou que só um dos dois existe.** Endpoints de
conteúdo/drill de UM contato, na analytics-api:

| Endpoint (arquivo:linha) | Devolve | ABAC por pool? |
|---|---|---|
| `transcript.py:192` `/transcript/sessions/{id}` | transcrição mascarada | **NÃO** — principal só resolve tenant; o docstring diz *"this read enforces tenant isolation only"* |
| `sessions.py:463` `/{id}/stream` | eventos da sessão **AO VIVO** (SSE) | **NÃO** |
| `sessions.py:696` `/{id}/workflow-trace` | trajetória | **NÃO** |
| `sessions.py:979` `/{id}/pipeline-state` | estado do fluxo | **NÃO** |
| `sessions.py:183` `/customer/{id}` | contatos do cliente | **NÃO** |
| `sessions.py:350` `/customer/{id}/search` | **busca dentro das mensagens** | **NÃO** |
| `reports.py:1408` `/customers/{id}/360` | agregado do cliente | **NÃO** (sem principal) |
| `supervisor.py:81` `POST /join` | entra na sessão viva e **ESCREVE no stream** | **NÃO** — confere só tenant |
| `audit.py:246` `/audit/sessions/{id}/messages` | mensagens | gate próprio, por `module_config.audit` — não por pool |
| `reports.py:1435` `/sessions/{id}/trace` | árvore de proveniência | **SIM** |
| `reports.py:457` `/segments` · `:1361` `/journeys` · `:107` `/sessions` | listas | **SIM** |

O módulo `sessions.py` inteiro importa `from fastapi import APIRouter, Query, Request` — **nenhum
`Depends`**. Não é escopo mal aplicado: é escopo ausente por construção.

🔴 **Por que a (d) NÃO é decomponível na ordem proposta.** O que protege a transcrição hoje não é
permissão — é o supervisor **não receber o `session_id`**. Como os ids são uuid, isso funciona como
barreira de CAPACIDADE (obscuridade que, na prática, segura). Abrir as linhas primeiro **entrega as
chaves** de endpoints que não checam nada: o supervisor passaria a ver os contatos fora dos pools
dele *e*, a um clique, a transcrição e o stream ao vivo deles. **Ordem correta: portões de conteúdo
primeiro, abrir as linhas depois** — e são ~8 endpoints, um SSE e um POST que escreve.

**Terceiro item que a (d) exige e ninguém pediu:** o **filtro de relevância** tem de nascer no lugar
que a ABAC desocupar, senão todo agregado escopado (Fila/SLA, TMA, bancada) passa a contar pools que
não são do supervisor no mesmo dia. Escopo não é só segredo — é também *"o relatório é da MINHA
operação"*.

**Estado: adiada por decisão do dono em 2026-08-26**, em favor da (a). Se voltar, vira ADR com as
três peças na ordem acima. A (b) é a fatia 1 dela e **não deve ser feita isolada**.

### ✅ O ABAC de pool é OPT-IN DO CHAMADOR — **os TRÊS bypasses fechados** *(2026-08-27; conferido ao vivo 2026-08-30)*

> **O título estava 🔴 e o defeito não existe mais** — os três ramos fail-open descritos abaixo
> foram fechados pelo endurecimento de `optional_pool_principal` em 2026-08-27, e o texto ficou
> para trás. Medido ao vivo em 2026-08-30, com a stack de pé:
>
> | ramo | hoje |
> |---|---|
> | 1 · sem `auth_jwt_secret` | **401** `auth_unavailable` (só `analytics_open_access`, declarado pelo operador, libera) |
> | 2 · sem header `Authorization` | **401** `auth_required` |
> | 3 · SSE com token **EXPIRADO** em `?token=` | **401** (testado com token `exp` no passado) |
>
> É o padrão que este arquivo cataloga: **o TÍTULO é o que mente para mais gente**. Um 🔴 falso
> custa mais que uma seção ausente — ele esconde os vermelhos reais na varredura. O diagnóstico
> abaixo fica como registro do que era.


`pool_auth.py` declara três bypasses, todos por escrito e todos fail-OPEN:

1. `:130-131` — **sem `auth_jwt_secret`** configurado ⇒ `accessible_pools=None` (irrestrito).
2. `:16-18` e `:133-135` — **sem header `Authorization`** ⇒ irrestrito, com o motivo declarado:
   *"backward-compatible with existing dashboard/report consumers"*. **Não há 401**: quem omite o
   token lê os contatos de todos os pools do tenant.
3. `:182-184` — `accessible_pools_from_token` (caminho SSE, token na query string) degrada aberto
   **de propósito**: token ausente, inválido OU **expirado** ⇒ irrestrito. O comentário justifica
   (*"a bad token can't 401 a stream"*), e a consequência é que o pior caso — token vencido — é o
   mais permissivo.

Confirmado pela medição da sessão anterior: `curl` **sem token** devolveu **120 contatos** (todos os
pools) e com token de 5 pools devolveu **86**, sem 401 em nenhum dos dois.

✅ **E NÃO é ele a causa do que apareceu na tela do dono — MEDIDO 2026-08-26, as duas hipóteses
caíram.** A queixa era *"o admin enxerga sessões de pools não liberados"*, com a lista em 120. Nem
H1 (requisição sem header) nem H2 (token com lista vazia): o token traz **22 pools** (43 com os
espelhos `-int`), e eles simplesmente **alcançam as 120 linhas da janela**.

`infra/test/q_scope_leak_check.sh` (novo) reconstrói as TRÊS cláusulas do
`_session_scope_clause` a partir da mesma resposta que a tela recebe, com uma testemunha de presença
por cláusula:

```
linhas=120 · cláusula 1 (entrou por pool meu)=101 · cláusula 2 (sem pool)=9
            cláusula 3 (pool meu ATENDEU)=10 · SUSPEITAS=0        → VERDE
```

🟡 **A cláusula 3 é o que engana o olho, e ela é desenho** (`reports_query.py`, docstring do
`_session_scope_clause`): um contato que ENTROU por `sac_ia` e foi ATENDIDO por `retencao_humano` é
do supervisor de retenção, mesmo sem `sac_ia` no escopo. São **10 linhas** aqui. Ver a coluna
*"ENTERED VIA"* mostrando pool alheio **não é** evidência de furo — só é furo a linha que falha nas
três, e essas são zero. Fica registrado como **discutível enquanto DESENHO** (é ampliação
deliberada), não como defeito.

⚠️ **O furo de transporte segue REAL e não medido em produção** — o que caiu foi a atribuição a ele
do sintoma da tela, não ele. `curl` sem token continua devolvendo as 120 linhas sem 401.

🔴 **DECISÃO DO DONO (2026-08-26): o admin RESPEITA a ABAC como qualquer um. Não há bypass por
papel.** Isso resolve a ambiguidade e move o defeito de lugar: o problema deixa de ser *"falta um ramo
para o admin"* e passa a ser **a convenção `accessible_pools: [] → irrestrito`**
(`pool_auth.py:157-161`), que é um bypass por AUSÊNCIA de grant — exatamente o contrário de
grant-first. O `pool_auth` nunca lê `roles`, e sob esta decisão isso está **certo**; o que está errado
é a lista vazia significar "tudo".

**Precedente na casa:** a ABAC do `scheduler.{configurar,operacao}` é **grant-first, sem role default
nem bypass de admin** (D2 do arco Scheduler). A regra do dono alinha `accessible_pools` a ela.

✅ **CONFIRMADO NA TELA em 2026-08-26, e a confirmação é o próprio problema:** o
`supervisor@plughub.local` tem **zero pools** e vê **120 contatos com todos os chips** em
`/analise/sessions`. Não há dúvida a resolver — lista vazia É acesso a tudo, no analytics inteiro
(`pool_auth.py:157-161`), e a tela é a demonstração de que ninguém precisa de grant para ler tudo.

⚠️ **Mas NÃO inverter direto para `[] → nada`.** Hoje `[]` é o que admin/developer carregam; virar o
significado num commit tranca todo mundo fora, e "ninguém vê nada" é tão mudo quanto "todo mundo vê
tudo". A forma é a mesma da V1-antes-da-V4 do arco ALLOWLIST — **contar antes de inverter**:
1. ~~**Fechar o fail-open de transporte** (sem header ⇒ **401**, não "todos os pools"). Independe da
   convenção e é o furo de verdade.~~ ✅ **FEITO 2026-08-27** — e não como "401 sempre": o bypass foi
   **amarrado a `analytics_open_access`** (default `False` no código, `"true"` só no demo), que é o
   mesmo mecanismo e a mesma grafia de ator que o gate de AUDITORIA já usa. Isso resolve o defeito
   *pelo nome que ele tem* — deixa de ser opt-in do CHAMADOR e vira declaração do OPERADOR — sem
   converter em mudez os **18 scripts de `infra/test/` que chamam sem token** (3 deles gates). O
   caminho SSE foi junto: token **expirado** era o caso MAIS permissivo, e agora recusa. Detalhe no
   `CHANGELOG.md` § 2026-08-27; baseline **626 → 630**. **Endurecer o DEMO (flag → false) é passo
   próprio**, e exige tratar os 18 antes.
2. ~~**Tornar o irrestrito EXPLÍCITO** — claim próprio, nunca inferido de vazio.~~ ✅ **FEITO
   2026-08-27.** Claim `unrestricted: true` + coluna `auth.users.unrestricted` (migração idempotente,
   **sem backfill**). Os **7** tradutores medidos passaram a preferir o claim; a cunhagem é **um** site
   e serve login e refresh. **Ordem dos ramos: o RESTRITIVO vence** — lista não-vazia decide, depois o
   claim, depois o legado *contado* (`LEGADO_POOLS_VAZIO`, distinguindo token velho de usuário sem
   escopo declarado). Gate `infra/test/probe_unrestricted_claim.sh` (visto vermelho→verde). Detalhe no
   `CHANGELOG.md`.
3. **Só então** `[]` passa a significar "nenhum pool", com o inventário do passo 2 na mão.

   **Pré-requisitos do passo 3, agora nomeados:**
   - [ ] Ler o inventário `LEGADO_POOLS_VAZIO` (§5 do gate) e **decidir usuário a usuário** quem recebe
     `unrestricted: true`. A lista é a saída do passo 2; sem ela a inversão é aposta.
   - [ ] `db.py:30` — `accessible_pools TEXT[] NOT NULL DEFAULT '{}'` faz **todo usuário novo nascer
     irrestrito** pela convenção. No passo 3 o default passa a significar "nenhum pool", que é o
     default seguro — mas a UI precisa deixar isso óbvio no momento da criação.
   - [ ] `db.py:seed_admin_if_absent` já cria o admin com `unrestricted=True` (feito no passo 2).
   - [ ] Validar na ESCRITA a ambiguidade "`unrestricted=true` **e** lista não-vazia". Hoje ela é
     inofensiva na leitura (o restritivo vence) e **não** é recusada na escrita: validar exigiria
     conferir contra a linha do banco no update parcial, e validação pela metade é pior que nenhuma.
   - [ ] `platform-ui/AccessPage`: hoje o formulário zera a lista ao marcar a caixa. Falta o caminho
     inverso explícito (marcar pools com a caixa ligada) — hoje só desabilita.

*(Estado do token do admin, medido 2026-08-26 com `q_scope_delta_stage2.sh`: **22 pools**, incluindo o
espelho `retencao_humano-int` — não vazio, não 5, não 36. O número herdado de "5 pools" da passagem
anterior estava velho.)*

#### ✅ INVENTÁRIO DO PASSO 1/2 — MEDIDO 2026-08-27 (`q_analytics_authless_inventory.sh`)

O plano acima exigia contar antes de inverter. Contado, e **duas premissas dele caíram**.

**Blast radius:** **43** call sites com `Depends(optional_pool_principal)` (testemunha: o símbolo
existe, `1` definição — `0` ali significaria grep quebrado, não "ninguém usa").

**Chamadores server-side header-less** (derivação por alvo `:3500`, nunca por menção do nome
`ANALYTICS_API_URL` — foi assim que o 4º apareceu):

| serviço | endpoint | gateado por `pool_auth`? |
|---|---|---|
| `mcp-server-plughub` → `tools/evaluation.ts:1114` | `/v1/audit/mcp-calls` | não — gate de AUDIT |
| `evaluation-api` → `backfill.py:75` | `/reports/segments` | **SIM** |
| `evaluation-api` → `router.py:2177` | `/v1/transcript/sessions/{id}` | não (só tenant) |
| `agent-registry` → `routes/skills.ts:594` | `/reports/sessions` | **SIM**, e **INERTE** |

🔴 **Defeito de lambuja, alheio ao arco:** o compose **não declara `ANALYTICS_API_URL` para o
agent-registry** (só para `mcp-server-plughub`, `evaluation-api` e `e2e-runner`), então a URL cai no
default `http://localhost:3500` = o próprio container. A chamada falha e morre num `catch {}` **vazio**
(`skills.ts:598`), logo `GET /v1/skills/:id/handoff-status` reporta `activeSessionCount: 0` **sempre**.
É a checagem de "deploy seguro" respondendo com um **valor plausível**: fiação ausente + degradação
muda, as duas famílias do CLAUDE.md no mesmo `try`.

**Chamadores browser-side:** 🔴 **não existe choke point único.** São **três** mecanismos de token
(`apiFetch()` = **86** call sites · header manual `Authorization` em `api/evaluation-hooks.ts` ·
query-param no SSE de `modules/service/api/hooks.ts`; `getAccessToken()` = **25** sites) e uma
**quarta classe sem auth nenhuma**: os **6** hooks de agent-assist chamam `fetch(url)` nu, `auth_refs=0`
nos seis (`useCustomer360`, `useSessionTranscript`, `useCustomerHistory`, `useCustomerSearch`,
`useSessionTrace`, `useCustomerJourneys`). Dois deles batem em endpoint **gateado** e portanto
**quebram no passo 1**: `useSessionTrace` → `/reports/sessions/{id}/trace` e `useCustomerJourneys` →
`/reports/journeys`. O `apiFetch.ts` documenta no próprio docstring o furo que fecha — e o defeito é
que ele é *convenção*, não caminho obrigatório.

🔴 **PREMISSA DERRUBADA — "virar `[]` tranca todo mundo fora" é FALSO nesta base.** Medidos os
principals: **3 usuários, ZERO dependentes do vazio**. O `admin` carrega **22 pools explícitos**, não
`[]`. O passo 3 é, aqui, muito mais barato do que o plano supunha.

⚠️ **Mas "zero hoje" não é "zero sempre", e a diferença é estrutural:** os **produtores** do vazio
seguem vivos — `db.py:30` declara `accessible_pools TEXT[] NOT NULL DEFAULT '{}'` (todo usuário novo
**nasce irrestrito**) e `db.py:821` (`seed_admin_if_absent`) cria o admin com `accessible_pools=[]`.
Logo **uma instalação limpa nasce com admin irrestrito**, e a medição acima descreve estado
**herdado**, não declarado — é o caso *"ambiente que só sobe porque já subiu antes"*. Inverter o
significado de `[]` **exige tocar o DEFAULT da coluna junto**, senão troca-se "todo usuário novo vê
tudo" por "todo usuário novo não vê nada", que é a mesma mudez na direção oposta.

**Prova viva (§5 do script), com testemunha:** sem `Authorization` → **HTTP 200, total=94**; com token
**lixo** → **HTTP 401**. A testemunha é o que importa: ela prova que o 401 **existe** naquele caminho,
logo o 200 do anônimo é **escolha declarada**, não ausência de mecanismo.


#### 🔴 NOVO — endurecer o DEMO exige tratar 18 scripts antes (medido 2026-08-27)

O passo 1 fechou o furo **por default** (produção), e **deixou o demo aberto de propósito**. O que
falta para fechar o demo (`PLUGHUB_ANALYTICS_OPEN_ACCESS: "false"` em `docker-compose.demo.yml:979`)
é tratar os chamadores que hoje não mandam token. Medido pela §6 do
`infra/test/q_analytics_authless_inventory.sh`:

**30 scripts falam com o analytics · 12 COM token · 18 SEM.** (O contador "com token" é a testemunha:
se fosse 0, o padrão de busca estaria errado, não o resultado.)

Os 18, com o cruzamento contra as **42 rotas gateadas** derivadas do fonte — **pelo menos 12 batem em
rota gateada**; os outros 6 **não estão provados seguros**, o cruzador tem falso-negativo por prefixo
de router (`test_t11_quality_report.sh` chama `/reports/evaluations/quality`, e a lista traz `/quality`):

| script | rota gateada? |
|---|---|
| `gate_queue_report_per_wait.sh` · `gate_sla_segment_target.sh` | SIM (`/reports/pools/queue`) |
| `probe_chip_breakdown.sh` · `probe_duration_definitions.sh` · `probe_f4_direction_and_classes.sh` | SIM (`/reports/sessions`) |
| `probe_f3_contact_list_contract.sh` · `probe_journeys_window_applied.sh` | SIM (`/reports/journeys` + `/reports/sessions`) |
| `probe_journey_merge_status_access.sh` | SIM (`/reports/journeys`) |
| `probe_segments_journey_window.sh` | SIM (`/reports/segments`) |
| `probe_invisibility_window.sh` | SIM (`/reports/wrapup-summary`) |
| `seed_deploy_lens_demo.sh` | SIM (`/reports/agents/compare`) |
| `test_r5_tier2_smoke.sh` | SIM (`/v1/audit/mcp-calls`) |
| `gate_pool_config_ttl_source.sh` · `test_h1_customer_history_drill.sh` · `test_h2_customer_history_search.sh` · `test_t11_quality_report.sh` · `test_t9a2_campaign_summary.sh` · `test_t9c1_transcript_window.sh` | **INCONCLUSIVO** — não classificar como seguro |

🔴 **O modo de falha é o que torna isto urgente-quando-for-feito, e não urgente agora:** um gate que
passa a receber 401 **não fica vermelho**. Ele fica **INCONCLUSIVO** — ou pior, conta zero linhas e lê
isso como *"não há dado"*. Virar a flag sem tratar os 18 converteria boa parte da superfície de
verificação do repositório em mudez, por atacado, e o sintoma seria AUSÊNCIA.

**Forma do conserto, para não pagar duas vezes:** extrair `infra/test/_auth.sh` com um `plughub_token()`
— os 12 que já autenticam repetem o mesmo bloco de 5 linhas de login, então o helper paga sozinho — e
fazer os 18 o consumirem. ⚠️ **Fazer isto ANTES de virar a flag, e medir no meio**: adicionar o header
NÃO é no-op, porque estreita o escopo de irrestrito para os 22 pools do admin. Se algum script assertar
contagem, o número muda — e nessa ordem a causa é inequívoca (escopo), enquanto virar os dois juntos
deixaria a falha ambígua entre *401* e *escopo estreitou*.

⚠️ **Achado de lambuja que o cruzamento produziu:** vários desses gates medem relatórios **escopados
por pool** enquanto **passam por fora do escopo**. Isto é, medem uma configuração que **nenhum usuário
real tem**. Não é só "vai quebrar quando a flag virar" — é que a medição de hoje responde a uma
pergunta ligeiramente diferente da que se fez.

#### ✅ 16 de 18 CONVERTIDOS (2026-08-27) — e o bloqueio real não eram os scripts

`infra/test/_auth.sh` criado (`plughub_auth_curl_shim` decide sobre a **URL real, em runtime** — a
detecção estática erraria 6 dos 18, em silêncio). **16 convertidos, 2 bloqueados, 11 já autenticavam.**
Todos os `rc` idênticos à baseline, inclusive os 2 que já falhavam antes. Detalhe no `CHANGELOG.md`.

✅ **DESBLOQUEADO 2026-08-27 pelo passo 2** — os dois gates abaixo foram convertidos ao principal
`probe@plughub.local` (`unrestricted: true` + lista vazia) e **seguem verdes**. Endurecer o demo
(`PLUGHUB_ANALYTICS_OPEN_ACCESS: "false"`) não depende mais de nada deste bloco; depende de decidir o
que fazer com os consumidores sem login (ver `agent-assist-ui` na porta 5173).

> 🔴 **REORDENAÇÃO DO PLANO — endurecer o demo estava bloqueado no PASSO 2, não na conversão.**

Medido: o tenant tem **36 pools**, o `admin` alcança **22**, **14 ficam fora**. **Nenhum usuário deste
ambiente enxerga o tenant inteiro**, exceto pelo caminho sem header. Dois gates comparam agregado da
**API** contra **ledger** lido direto, logo só fecham sob principal irrestrito:

| gate | por quê |
|---|---|
| `gate_queue_report_per_wait.sh` | com admin cai de **80 → 71** esperas (falta `formfill_demo_ia`) |
| `gate_sla_segment_target.sh` | semeia `pool_a/b/c` sintéticos, que ninguém tem concedidos |

Criar usuário com `accessible_pools: []` resolveria hoje e é **retrabalho por construção** — o passo 3
inverte esse significado. **O passo 2 (irrestrito EXPLÍCITO, claim `unrestricted: true`) deixou de ser
follow-up e virou pré-requisito do endurecimento.** O motivo está escrito dentro dos dois gates, não
só aqui.

⚠️ **Erro de método registrado:** concluí *"os 22 pools do admin cobrem a janela"* a partir de **um**
endpoint (`/reports/sessions`, 97 = 97). Não vale para `/reports/pools/queue`. **Cobertura de escopo é
por ENDPOINT e por JANELA, nunca generalizável de uma amostra** — e o instrumento errou antes do
diagnóstico: a primeira comparação cruzou `pools.id` (UUID) com `accessible_pools` (slug), e teria
publicado *"o admin cobre 0 de 36"*.

⚠️ **Confundidor a lembrar em qualquer comparação antes/depois neste ambiente:** o demo **gera dados**
(o total de `/reports/sessions` foi de 94 para 97 no mesmo dia). Comparar por número exato produz
falso positivo — o sinal confiável é o `rc` e a linha de veredicto. Um script mudou de saída na
conversão (`probe_invisibility_window.sh`, `no_sample` → `measured`) por uma chave de ledger que
apareceu entre as execuções, e **não** pela mudança.


#### 🟡 O `agent-assist-ui` legado (5173) não tem login NENHUM — e não há onde pendurar credencial

Medido 2026-08-27: zero ocorrências de `getAccessToken`/`Authorization` em
`packages/agent-assist-ui/src`. Ele faz **uma** chamada ao analytics
(`useCustomerHistory.ts:48` → `/analytics/sessions/customer/{id}`), que **não é gateada** — por isso o
passo 1 não o quebrou, e por isso ele não aparece na lista dos 18.

**Mas ele quebra nos portões de conteúdo da (d)**, que é exatamente a lista onde
`/sessions/customer/{id}` aparece como *sem ABAC*. Quando aquele portão for construído, este app fica
sem acesso e **não tem tela de login para consertar**. Decidir então entre: (a) aposentá-lo de vez
(está no compose como serviço vivo), (b) dar-lhe autenticação, ou (c) declará-lo fora do escopo do
portão — a terceira sendo a que reabre o furo por outro nome.

### ✅ FECHADO (parcial) — portão de PAPEL no Analytics + guard de rota ausente (corrigido 2026-08-27)

**Fechado para o grupo `analise`.** `roles: ['supervisor','admin','business']` removido do Sidebar; as
**10 rotas de página** de `analise/*` envolvidas em `RequireAbac`; o predicado extraído para
**`lib/permissions.ts::passesAbacRule`**, consumido pelo Sidebar E pelo guard (uma casa, dois
consumidores — dar guard à rota sem extrair criaria a segunda porta que a V2 do ALLOWLIST acabou de
eliminar noutro lugar). Detalhe no `CHANGELOG.md` § 2026-08-27. Gate
`infra/test/probe_analise_route_guard.sh` — visto **vermelho com 11 reprovações** antes de verde.

**Medição que autorizou remover o papel:** os 3 usuários têm `module_config`, e os 3 têm
`contacts.visualizar: read_only` — **inclusive o `operator`**. A ABAC já concedia; só o papel
bloqueava. Remover não tirou acesso de ninguém e **criou a cobaia natural** (escopo estreito +
alcança a tela) que só existia por remendo à mão no `supervisor`.

#### 🟡 O que ficou aberto, de propósito

1. **Os outros 7 grupos com `roles:`** (`Sidebar.tsx` `:47`, `:55`, `:65`, `:86`, `:99`, `:146`,
   `:156`). Cada um gateia campos ABAC diferentes e precisa da **mesma medição** que o `analise`
   recebeu — remover em bloco, sem olhar o `module_config` de cada campo, é como se conceder acesso
   por engano. O gate conta os restantes numa testemunha, então "zero" nunca vai significar "o padrão
   mudou".

2. 🔴 **O bypass de admin/supervisor do ramo NÃO-STRICT contradiz a decisão do dono.**
   `passesAbacRule` preserva `if (['admin','supervisor'].includes(role)) return true` para itens sem
   `strict: true` — e a decisão de 2026-08-26 diz *"o admin respeita a ABAC como qualquer um; não há
   bypass por papel"*. Foi preservado porque mudá-lo altera **todo** item não-strict de uma vez, e
   juntar as duas mudanças tornaria qualquer regressão ambígua entre *"o guard está errado"* e *"a
   semântica do portão mudou"*.
   **Forma do conserto:** virar os itens para `strict: true` um módulo por vez, medindo o
   `module_config` antes — igual ao que foi feito aqui. ⚠️ O ramo não-strict tem **duas** liberações
   (bypass de papel **e** degradação graciosa com config vazio); só a primeira é o defeito. A segunda
   protege conta legada, e removê-la sem contar quantas existem repete o erro do `[] → irrestrito`.

3. ⚠️ **Isto não é autorização, e o `CHANGELOG` diz por quê.** A fronteira de dados é o escopo de pool
   no backend; os **~8 endpoints de conteúdo da (d)** seguem sem portão nenhum. Quem digitar a URL de
   um **drill** ainda alcança o dado. O guard fecha a PÁGINA, não o endpoint — e continua valendo a
   ordem registrada na (d): **portões de conteúdo primeiro, abrir as linhas depois**.


### ~~🔴 Analytics e Monitor têm um portão de PAPEL a montante da ABAC — hardcoded, não editável na UI (medido 2026-08-26)~~ *(registro original; ver o bloco acima)*

Levantado pelo dono; confirmado no código. O grupo de navegação `analise` declara
**`roles: ['supervisor', 'admin', 'business']`** (`platform-ui/src/shell/Sidebar.tsx:121`), e só
DEPOIS cada item declara sua ABAC (`:123` — `abac: { module: 'contacts', field: 'visualizar' }`). São
dois portões em série, e **o de cima não é configurável**: o `operator` tem
`contacts.visualizar: read_only` no seed (`seed_auth.py:275`) e mesmo assim não alcança o menu,
porque o portão de papel falha antes de a ABAC ser consultada. O de baixo — o que a tela de Acesso
edita — nunca é atingido.

Isso viola a invariante **"Every config field is UI-editable"** (CLAUDE.md § Configuration) e esvazia
o `module_config` justamente onde ele deveria decidir. O mesmo padrão vale para os outros grupos com
`roles:` (`:47`, `:55`, `:65`, `:86`, `:99`, `:146`, `:156`).

⚠️ **E o portão é COSMÉTICO, não autorização.** `app/routes.tsx:122-147` registra
`analise/sessions` (e as irmãs) como elemento nu — **sem guard nenhum**. O papel esconde o MENU;
digitar a URL entra. A única restrição real de dados é o escopo de pool no backend, que é outro eixo.
*(Casa com a dívida já registrada "guard de rota ABAC em `analise/*`" — que aqui deixa de ser dívida
genérica e ganha o par: **guard ausente na rota + portão de papel na navegação**, os dois errados em
direções opostas.)*

🔴 **Consequência imediata, e ela travou a validação visual do chip:** não existe hoje usuário que
combine *acesso ao Analytics* com *escopo de pool estreito*. O `operator` tem escopo estreito e não
alcança a tela; o `supervisor` alcança a tela e tem `accessible_pools: []` (irrestrito). A prova pela
API foi feita com o `operator` (gate verde); a prova pela TELA depende de conceder alguns pools ao
`supervisor` em Configuração › Acesso.

### ✅ FECHADO — `POST /supervisor/*` não tinha autorização (medido 2026-08-26, corrigido 2026-08-27)

**Era pior que o registro dizia.** A linha abaixo afirmava *"qualquer token válido do tenant entra"*.
Medido: o router **não tinha `Depends` nenhum** — não exigia token **algum**. O gate contra a imagem
antiga saiu anônimo → **200**, token **lixo** → **200**, sessão de pool **fora do escopo** → **200 com
`participant_id`**. Não era autorização fraca; era ausente.

**Fechado** com `require_pool_principal` (irmão estrito do `pool_auth`, 401 nos dois ramos de fail-open)
+ `resolve_live_session_pools` (união `meta.pool_id` ∪ `pools[]` das instâncias nos SETs de agente) +
tenant vindo do **token** + `message`/`leave` **author-bound** + identidade no stream = `sub`. Escopo
indeterminável **RECUSA** (`session_pools_undeterminable`). Nada lê `roles` — decisão do dono.
Detalhe e o porquê da união (o `meta.pool_id` significa entrada, ausente ou atendimento conforme o
canal) no `CHANGELOG.md` § 2026-08-27. Gate: `infra/test/probe_supervisor_join_authz.sh` (6 ramos,
3 testemunhas, visto vermelho antes de verde). Baseline **620 → 626**.

*(Registro original preservado abaixo pela cicatriz que ele cita.)*

~~`supervisor.py:81`. Qualquer token válido do tenant entra numa sessão **ao vivo** e **escreve** no
stream dela (`participant_joined` via `_xadd`).~~ Não é escopo de leitura: é fronteira de autorização
ausente num caminho de ESCRITA. Achado de lambuja da varredura da (d), independente dela.
*(O arquivo já carrega a cicatriz de um fail-open vizinho: `:100-104` documenta o check de tenant que
se auto-anulava por default igual ao valor comparado.)*

### 🔴 As cinco baselines só rodam em container HERDADO — somem no primeiro `build` (medido 2026-08-27)

`packages/analytics-api/Dockerfile:9` faz `pip install --no-cache-dir -e .`, e `pytest` mora em
`[project.optional-dependencies].dev` do `pyproject.toml`. **Imagem recém-construída não tem pytest.**
A baseline de 620 que a passagem lista como rede de segurança some exatamente quando é necessária —
logo depois do `build` da mudança que se quer verificar. Apareceu assim aqui: `No module named pytest`
imediatamente após o primeiro build de hoje, num comando que minutos antes tinha devolvido 620.

É *"ambiente que só sobe porque já subiu antes"* aplicado ao **instrumento**, e um teste que não pode
rodar é da mesma família do que não pode reprovar. **Medido nas outras quatro (2026-08-27), e sao TRES, nao quatro:** `orchestrator-bridge`, `session-replayer` e `routing-engine` tem a MESMA forma — nenhum Dockerfile instala `[dev]` ou `pytest`, e os tres `pyproject.toml` declaram pytest como dependencia opcional. O `mcp-server-plughub` **escapa**: o Dockerfile faz `npm install` puro (nao `npm ci --omit=dev`), entao o `vitest` entra na imagem e aquela baseline sobrevive a um rebuild. Ou seja, das cinco baselines da passagem, **quatro sao herdadas** (as quatro de Python) e **uma e real**.

Conserto candidato: `pip install -e ".[dev]"` num estágio de teste do Dockerfile, ou um alvo
declarado em `infra/` que instale antes de rodar. Contorno de hoje:
`pip install pytest pytest-asyncio httpx` dentro do container após cada build.

### ✅ `agent-registry` chama o analytics-api num endereço que é ele mesmo — `handoff-status` sempre 0 — **RESOLVIDO 2026-08-30**

> **O conserto foi EXATAMENTE o que esta seção prescrevia** (env no compose + `catch` que diz o
> que deixou de valer), mais a credencial de serviço que ela anteviu. Medido depois:
> `active_sessions` de 0 → **24**. Gate: `infra/test/probe_internal_service_callers.sh`, ramo B,
> com testemunha positiva obrigatória (`> 0`) — porque o defeito ERA o zero.
>
> ⚠️ **Mas ele foi re-derivado do zero, sem que esta seção fosse lida** — três dias depois de
> escrita, por um caminho mais caro (um `console.warn` novo em vez de um `grep`). O `CHANGELOG`
> de 08-30 chegou a creditar o instrumento pela descoberta, e foi corrigido. A lição não é do
> código: **achado que não é procurado é achado que se paga duas vezes**, e num `TODO.md` de
> ~7 900 linhas isso é o caso comum, não o raro.

`routes/skills.ts:594` monta `${config.analytics_api_url}/reports/sessions` para contar sessões ativas
antes de um deploy. O `docker-compose.demo.yml` **não declara `ANALYTICS_API_URL` para o
agent-registry** (só para `mcp-server-plughub`, `evaluation-api` e `e2e-runner`), então a URL cai no
default `http://localhost:3500` — dentro do container, ele mesmo. A chamada falha e morre num
`catch {}` **vazio** (`:598`), logo `GET /v1/skills/:id/handoff-status` reporta
`activeSessionCount: 0` **sempre**.

A checagem de "deploy seguro" responde com um **valor plausível**: fiação ausente + degradação muda,
as duas famílias do `CLAUDE.md` no mesmo `try`. Conserto: declarar a env no compose **e** fazer o
`catch` dizer o que deixou de valer (hoje ele nem loga). ⚠️ Note que a rota é **gateada por
`pool_auth`** — consertar a fiação a torna um dos chamadores header-less do inventário do passo 1.


### ✅ REFUTADO — "chip diz `· 3` com CINCO linhas do mesmo processo" NÃO é defeito (2026-08-26)

**Medido com `infra/test/q_chip_row_dump.sh`** (novo — despeja os cinco campos crus por linha):

```
a-aeda66758c14  jid=8c47326d  total=5  acc=3  int=2  unk=0  scoped_out=false
4-06745737b1b6  jid=faf611c6  total=3  acc=2  int=1  unk=0  scoped_out=false
```

A invariante FECHA (`5 = 3+2+0`, `3 = 2+1+0`): o backend classifica as etapas internas
corretamente e o chip publica `· 3 + 2`. O que a tela mostrava era `· 3` porque **a coluna PROCESS é
a última de uma tabela com rolagem horizontal** — o ` + 2` estava fora da viewport.

🔴 **A lição de método é a do observador, não a do código.** Duas telas foram lidas como *"o chip
publica só os acessos"*, e a hipótese chegou a ser registrada aqui como provável defeito de backend
(`_apply_contact_scope` excluindo do agregado o que a lista mostra). A evidência que a desmentia
estava **na mesma imagem**: a barra de rolagem horizontal. **Tela é evidência do que ela MOSTRA, não
do que ela contém** — antes de inferir estado de backend a partir de um render, garantir que o render
foi visto inteiro. Custo: uma seção inteira de TODO escrita sobre um defeito inexistente, e uma
previsão errada (previ `int=0`, veio `int=2`).

⚠️ **Sobra um item de UX, esse real:** o chip fica cortado em telas comuns, e ele é o ÚNICO pivô da
visão 1 para a visão 2. Truncar silenciosamente o número que o operador clica é a mesma família do
resto deste arco — a tela mostrando menos do que sabe. Candidatos: mover PROCESS para antes de
SEGMENTS, ou fixar a coluna (`sticky`) à direita.

### ~~🟡 Chip diz `· 3` com CINCO linhas do mesmo processo na tela~~ (registro original, mantido pela lição)

Na tela do dono, `PRC-8c47326d` aparece em **5** linhas (3 acessos `↓` + 2 etapas internas `⚙️`) e o
chip publica **· 3**. `PRC-faf611c6`: **3** linhas (2 `↓` + 1 `⚙️`), chip **· 2**. O padrão é exato —
o chip está publicando **só os acessos**, com a parte "etapas internas" chegando falsy.

Isso contradiz o que o código promete: `acesso + interna + não classificada == total`, e o comentário
de `_attach_session_journey_chip` §3 nomeia justamente *"o chip diria `·3` e a tela para onde ele leva
mostraria 5 linhas"* como a regressão que os testes existem para pegar.

**Não teorizar — é uma query.** Hipótese a testar primeiro: `_apply_contact_scope` excluindo as duas
`⚙️` do AGREGADO mas não da LISTA (o helper exclui sessão de pool **interno**; a `⚙️` da lista é
direção `internal`, derivada de `spawn_reason` — são os DOIS sentidos de "interno" que o próprio
docstring avisa não se substituírem). Se for isso, o defeito não é do marcador nem da (a): é a
divergência entre os dois predicados, e é anterior a esta sessão.

**A verificar antes de construir** (nenhum destes foi medido):

- o literal que o cliente usa em `messages.author_role` — suposto em D9.
- ~~**`contatos` ≠ `acessos do cliente`, e nada hoje os separa.**~~ **DECIDIDO pelo dono em 2026-08-25:
  o cabeçalho conta ACESSOS DO CLIENTE, e são N, não 2.** Medido em 2026-08-14: das 4 sessões da
  journey de referência só **2** são acesso do cliente (webchat, `spawn_reason NULL`); as outras duas
  são maquinaria (webhook, `trigger`), e `aprovacao_credito` **não** é `purpose=internal`, logo
  `_apply_contact_scope` não a exclui. O cabeçalho de F4 diria *"contatos 4"* para quem nos procurou 2
  vezes. O discriminador de D4 é derivável hoje, sem dado novo.

  > ✅ **A decisão abriu um bloqueio de MEMBERSHIP, e ele foi FECHADO no mesmo dia (2026-08-25,
  > passo 2b — ver `CHANGELOG.md`).** O relato abaixo é o do defeito, preservado porque a lição de
  > método é o achado: a F1 constava ✅ com gate que só exercitava o ramo que funciona.
  > O dono nomeou a forma real do processo: acesso de **abertura** + N acessos intermediários de
  > **consulta de status** + acesso **final** de resposta — *"os intermediários não estão relacionados
  > e precisariam estar"*. Confirmado no código em 2026-08-25, e é um ramo, não uma ausência de
  > desenho: `skill_limite_entrada_v1.yaml:254-261` manda `policy == "auto"` (acesso 3) para
  > `unificar_journey` → `journey_merge`, e o **default — que é a consulta de status (`offer`, "em
  > análise") — vai direto ao `menu_continuidade`, sem merge**. É a **única** ocorrência de
  > `journey_merge` no arquivo (`:362`). Consequência: abertura é raiz, resposta entra por merge, e
  > **todo acesso de consulta nasce raiz de si mesmo e fica fora do processo** — quantos forem.
  >
  > ⚠️ **Não confundir com a decisão vizinha, que está certa:** `:305-306` recusa `workflow_resume` no
  > acesso 2 de propósito (*"é o que torna o acesso 2 uma leitura, não uma ação"*). Merge é carimbo de
  > **proveniência**, não ação de negócio — as duas coisas são independentes, e a segunda não justifica
  > a primeira. O ADR manda o contrário na própria tabela de pertença (*acesso espontâneo → merge, F1*).
  >
  > ⚠️ **Isto rebaixa a prova da F1.** Ela consta ✅ *"provada por aresta ativa em `journey_aliases`"* —
  > a aresta existe, mas veio do ramo `auto`. Um gate que só exercita o ramo que funciona não pode
  > reprovar o ramo que não funciona: mesma família do `probe_journey_limite` registrado logo abaixo.
  > **A F1 está entregue para o acesso 3 e AUSENTE para o acesso 2**, e o cabeçalho não dizia isso.
  >
  > **Consequência para a ordem:** o passo 2 tinha dois bloqueios declarados; são **três**. Este é de
  > pertença, precede o texto (não se conta acesso que não é membro) e é a fatia menor das três.
- ~~**O tipo de linha "acesso outbound" tem ZERO amostras.**~~ ✅ **RESOLVIDO POR MEDIÇÃO 2026-08-25 —
  o produtor de `collect` EXISTE e funciona ponta a ponta.** Gate:
  `infra/test/probe_spawn_reason_collect.sh` (8/0, re-executável). Medido: baseline `NULL` **449** ·
  `trigger` **109** · `collect` **0** → depois do cenário **`collect` 1**, com a linha em
  `analytics.sessions` fechada em **1 s** e `spawn_reason='collect'`. O zero era **ausência de
  população**, nunca produtor mudo — e a contagem sozinha não podia dizer qual dos dois, que era
  exatamente o achado que travava o passo 2.

  **Três coisas que a leitura do código estabeleceu ANTES de rodar, e que mudaram o desenho da
  medição** (a versão do plano — *"rodar o cenário `limite_entrega` e reler `spawn_reason`"* — teria
  medido zero e o zero pareceria defeito pela terceira vez):
  1. `spawn_reason='collect'` tem produtor ÚNICO em `handle_collect_engage` (`webhook.py:2118`), que é
     o **engajamento**. `handle_collect` é LAZY e suspende sem criar sessão ⇒ **parquear produz zero por
     construção**. O clique é obrigatório, e é a única parte sintética do gate.
  2. `GET /survey/{token}` **não** publica inbound — só semeia o ctx e cunha o JWT; quem publica é a
     PÁGINA ao conectar o WS (`webchat.py:304`). Um `curl` mede a metade errada. Daí o
     `infra/test/_ws_engage.py` (cliente WS mínimo, roda dentro do channel-gateway).
  3. `sessions.spawn_reason` só é carimbado no **fechamento** (`orchestrator-bridge/main.py:2918`) ⇒
     sessão ABERTA devolve `NULL` legítimo. É uma **quarta** ausência, e o gate a nomeia em vez de
     contá-la como defeito.

  🔴 **O `delegate = 0` está EXPLICADO, e não é defeito — é desenho.** Esta linha dizia *"não
  explicado (o carimbo existe em `webhook.py:1604`)"*. O carimbo existe (hoje `:1610`/`:1646` — as
  linhas andaram), mas o caminho que o executa está **morto por decisão**:
  `e2e-tests/services/skill-flow-service/src/index.ts:509-514` declara que `delegate()` **sempre** roda
  como conference specialist DENTRO da sessão do chamador, e o único chamador é
  `/v1/channels/webhook/delegate-conference`. `webhook.py:1669` diz o mesmo por escrito (*"este caminho
  está inerte hoje"*). Conferência **reusa o `session_id` do pai** ⇒ nenhuma sessão-filha nasce ⇒
  nenhuma linha com esse rótulo, e nenhuma rodada de cenário mudaria isso. **Não medir de novo.**
  Item próprio: `handle_delegate` + a rota `/v1/channels/webhook/delegate` são caminho morto — decidir
  entre remover e declarar (hoje a rota está viva e nada a chama).

  **F3 seguiu em frente com o ramo escrito e não verificável** — a diferença para ANI/DNIS é que aqui
  o ramo é o DOMÍNIO do campo (não uma coluna a mais na tela), e o custo de não tê-lo seria classificar
  um `collect` futuro como *inbound* em silêncio. Está registrado, não descoberto na revisão.

  ⚠️ **RESÍDUO — "o produtor funciona" ≠ "a classe tem população no mundo real".** O gate provou o
  caminho **dando o clique ele mesmo**, e é honesto sobre isso. Em produção ninguém clica: a entrega
  do link é trilha NÃO construída (`skill_limite_entrega_v1.yaml:65-67`; `webhook.py:2055`
  `TODO(J4c fase 2): real SMS/email delivery`). Enquanto não houver entrega, `collect` continua
  crescendo só por gate. **Para o F4 isto basta** — a classe de linha deixou de ser não-verificável e o
  ramo tem prova; para o negócio, não.

  🔴 **Defeito vivo que o gate revelou de lambuja: o cliente que CLICA no link não recebe nada.** A
  sessão de engajamento roteia para `limite_retorno`, cujo `skill_limite_retorno_v1:41-48` ramifica em
  `@ctx.session.customer_present` — e `handle_collect_engage` **não semeia essa tag**. Resultado
  medido: `aguardar_inbound` → `complete resolved` em **1 s**, `CLOSED_BY_SERVER`, cliente presente e
  tela vazia. Hoje está mascarado porque ninguém clica: **dois defeitos se cobrindo**, e o primeiro a
  ser consertado (entrega do link) desmascara o segundo. Conserto provável = semear
  `session.customer_present=true` no ctx do engajamento, junto do `spawn_reason` — mas é DECISÃO, não
  obviedade: o discriminador foi desenhado para *falhar adiando*, e ligar a tag sem medir troca o lado
  seguro do erro.
- **`customer_id LIKE 'sys:%'` — desempate NÃO codificado, e a medição é o motivo.** Previsão: 0.
  Medido: **1 em 420**, e é `webhook`+`trigger`+`limite_entrega`, já classificada como interna pelo
  primeiro ramo da regra. `sys:` ali é consequência de nascer de máquina, não critério independente.
  Reabrir só com população que o exercite.
- **`probe_journey_limite.sh` não pode reprovar na dimensão de F1** — conta por proveniência (disse 3)
  enquanto `/reports/journeys` conta proveniência ∪ alias (disse 4). Se ele for usado como gate de
  journey depois de F1, compra confiança sem dar nada.

**Filtros da visão 1, revisados e medidos (ADR D12b):** `período · canal · entrou por · atendido por`.
O **DNIS saiu e não volta** — endpoint→pool é **1:1** (13 webhook/13 pools, 2 webchat/2 pools), logo o pool
o substitui sem perda; e `whatsapp`/`voice`/`sms`/`email` têm **zero** linhas em `channel_endpoints`. O
**canal fica**: tirá-lo economizaria zero (já está preenchido em todas as sessões) e 4 pools do demo
declaram `[webchat, whatsapp]`, então o pool não o subsume por config — só por ausência de amostra.

---

## 15 `session_id` existem em `segments` e NÃO existem em `sessions` *(achado 2026-08-14, no contador-testemunha da F1b)*

O contador-testemunha da base acusou **422 sessões com segmento contra 413 linhas em `sessions`** —
um lado do substrato tem sessão que o outro não tem. Nomeados (contar não é identificar): os 15 são
**todos** `pool=sac_ia · role=primary · agent_type=ai`.

Não são conferência/hook — esses compartilham o `session_id` do pai e não criariam id novo. São
segmentos primários de IA sem linha de contato, ou seja: **o contato não aparece em nenhum relatório**
(toda query de sessão parte de `sessions`), enquanto o trabalho do agente aparece em `/reports/segments`.
Qualquer confronto entre as duas superfícies vai discordar em 15.

Não investigado: é anterior à F1b, ortogonal a ela, e entrar nisso teria trocado o escopo da fase.
Primeira pergunta para quem pegar: são antigos (anteriores a algum conserto de ingest) ou o
`sac_ia` produz isso hoje? `min/max(started_at)` desses 15 responde em uma query.

> ✅ **Convergência medida 2026-08-21 — esta seção e a do "segmento que nunca fecha" descreviam as
> MESMAS 15 linhas.** Medido: **16** segmentos `primary` abertos no tenant, e **uma única sessão** os
> contém (status `active`, legítima). Os outros **15** pertencem a `session_id` que não existem em
> `sessions` — e a contagem de órfãos por papel devolve **`primary` 15 e mais nada**.
>
> **Muda o enquadramento:** não é *"o segmento não fecha"*, é *"a sessão nunca existiu, então nunca
> houve fechamento para acontecer"*. São defeitos diferentes — um é do produtor de participantes, o
> outro do ingest de sessão — e foram contados juntos por meses.
>
> **Também explica por que `primary` travado em sessão fechada foi a 0:** os 5 de 14/08 caíram mesmo com
> o fix de 18/08; os 15 nunca estiveram nessa população. ⚠️ *Um zero só julga se o filtro que o produziu
> não puder tê-lo fabricado* — o `status='closed'` da minha query os excluía por construção.
>
> O número **não cresceu** (15 em 14/08, 15 em 21/08), o que favorece "resíduo histórico" sobre "produtor
> vivo" — mas `min/max(started_at)` continua sendo a query que decide, e continua não tendo sido rodada.

---

## Workflow trace é assimétrico na proveniência — inclui o PAI, não inclui o FILHO *(achado 2026-08-12, no E2E de tela)*

O *Workflow trace* de `/analise/sessions` para a sessão de ANÁLISE do cenário de limite
(`af64c36b-…-21a1824ad58d`) lista **8 execuções**, e a primeira é `skill limite entrada` no pool
`limite_ia` — que rodou em **outra sessão**, a de intake (`48f7cce5-…`, a raiz). Ou seja: o trace
**atravessa a fronteira de sessão para trás**.

Mas ele **para** em `skill limite processo — resolved` (13:35) e **não** inclui a sessão de entrega
(`f4db86cf-…`, pool `limite_entrega`, 13:35→13:46), que é filha da mesma análise por
`origin_session_id` e carrega a mesma raiz.

**A pergunta em aberto não é "falta a entrega"** — é *por que o escopo é assimétrico*. Se o trace
segue proveniência, deveria seguir nos dois sentidos; se é session-scoped, não deveria mostrar o
intake. Uma das duas leituras está errada, e não sei qual: pode ser que o intake apareça por outro
motivo (o `origin_session_id` da própria sessão, e não uma varredura), o que seria desenho
consistente e não assimetria.

**Medir antes de consertar:** ler a query/endpoint que alimenta o trace e responder "qual é o escopo
declarado?". Só depois decidir. Consertar para "seguir os dois sentidos" sem saber a intenção
transforma uma pergunta em duas.

Contexto: as três sessões estão corretas no dado — `/reports/journeys?root_session_id=…` devolve
`session_count: 3` e `/reports/sessions?root_session_id=…` devolve as três com `origin_session_id`
encadeado. A Vista Processos renderiza a árvore certa (ROOT WebChat → análise → entrega). **Não há
defeito de agrupamento**; isto é só sobre o escopo do trace.

---

## Modelo journey/session/segment — ADR fechado, spec pendente *(2026-08-10)*

Fixado em [`docs/adr/adr-journey-session-segment-model.md`](docs/adr/adr-journey-session-segment-model.md):
três definições + discriminador por **identidade de contato** (não por duração), a **regra dual** de
escopo, pertença de journey com **uma** regra (o resto é filtro), **transição como primeira classe** (D4),
`journey_id` como projeção **com reconciliação** (D5), *workflow declara / journey observa* (D6), merge
como reparo de proveniência não observável (D7), **porta externa de resume como pré-requisito** (D8) e
**definição única de duração** (D9).

**§8 todo medido** (2026-08-10, ambiente pós-`--wipe`): legado **zero** em todas as tabelas ⇒ **não há
backfill**; transição **observável** num ciclo real (suspend → delegate a humano → submit → resume →
`resolved`, 6 min 23 s de lacuna); TTL do `pipeline` ~24 h ⇒ `current_step` cobre o recente, não o
histórico; `journey_aliases = 0` ⇒ a reconciliação da D5 nasce sem dado de teste; **não há re-carimbo de
`opened_at`**.

**Achados adjacentes registrados no ADR, nenhum investigado:**

- **`sequence_index` não ordena** — medido `0, 0, 2` numa sessão e `0, 0` noutra. Ordenar por
  `started_at` é o único caminho correto hoje; código que use o índice como chave de ordem está errado.
- **Composição de segmentos varia por caminho** — item de pull não gera segmento de fila, push gera.
  Contar segmentos para inferir ciclos é frágil por natureza (reforça a D4).
- **`handle_time_ms` com dois comportamentos vivos** + um terceiro que o `CLAUDE.md` afirma e o código
  registra como adiado. Virou **D9**, dentro deste arco.

**Spec escrita 2026-08-10** — [`docs/product/workflow-arc-implementation-spec.md`](docs/product/workflow-arc-implementation-spec.md).
Fases 0→4 (declarar a borda · porta externa de resume · transição · duração · remoção), cada uma com
instrumento e número previsto. Dois achados do inventário estático mudaram a spec antes da primeira linha
de código:

- ⚠️ **A "porta externa" não é externa.** `/channel/webhook/{slug}` (`channel-gateway/main.py:1302`) e
  `/v1/channels/...` (`:1387`) são rotas do MESMO app na MESMA porta (`docker-compose.demo.yml:1185`);
  não há proxy para `/channel` em `vite.config.ts` nem no `Dockerfile`, e **não existe nginx versionado no
  repo**. A separação é o filtro `allowed_origins={"external"}` (`:1347`) — de código, não de topologia.
  O `CLAUDE.md` opõe os dois como se a borda existisse. Virou a Fase 0: **declarar** o requisito de deploy,
  não construir a borda no meio deste arco.
- ⚠️ **O TTL de `{tenant}:resume_tokens` é do HASH, não do token** (`index.ts:463`, `webhook.py:1723`,
  `:2152`): o hash é compartilhado por todo o tenant e o último escritor redefine o prazo de todos. Hoje é
  higiene; quando o prazo virar contrato com um terceiro ("seu link vale 48 h"), vira promessa não cumprida
  — um `collect` de 1 h escrito depois encurta o token de 48 h. Entra na Fase 1, junto com a chave
  `resume_meta` que a Fase 2 precisa para cumprir o invariante RMT.

E um terceiro que é de segurança: o **`source` do resume é asserido pelo chamador** (`webhook.py:131-141`)
— sem JWT dá para declarar `source:"supervisor:x"` e obter `acw_supervisor_closed` no registro terminal
durável de 25 h. Já catalogado neste TODO; **abrir a porta externa o torna explorável**, então é gate da
Fase 1, não follow-up.

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

## 📂 TEMA · Wrap-up, fila humana e trabalho

## I5 — encerramento de trabalho author-bound *(**sem defeito vivo desde 2026-08-07**; núcleo A+B ✅, relatório fatias 1–2 ✅, arco de duplicação/afinidade A–F ✅, lacunas 3/4/5/6 ✅. Resíduo NÃO é defeito: fatia 3 e lacuna 2 estão **gated em evidência que manda não construir**; sobram 3 timeouts constantes + o portão de deriva do seed, ambos de consolidação de config)*

> ⚠️ **Cabeçalho corrigido em 2026-08-03.** Dizia *"resta o relatório"*, e o relatório está pronto:
> fatia 1 (Monitor › Pendências) ✅ e fatia 2 (Analítico › Histórico de Wrap-up) ✅, ambas em
> 2026-07-30. O que resta é a **fatia 3**, e ela não é "pendente" no sentido usual — está
> **explicitamente gated em medição** (*"não construir sem medir"*), com o gate já rodado uma vez:
> `unfilled_rate` 22,2%, e os 2 vencidos eram **reivindicados**, não nunca-reivindicados. Ou seja: a
> evidência disponível diz para NÃO construir. Ler "resta o relatório" no índice sugeria o oposto.
>
> **Corrigido DE NOVO no mesmo dia**, e o segundo erro é mais sutil que o primeiro: o cabeçalho
> passou a dizer *"resta **só** a fatia 3"* enquanto a tabela § Lacunas, 100 linhas abaixo, listava
> **cinco** lacunas abertas — nº 2 (reaper), 3 (TTL de fila não alcança pull), 4 (parcial), 6 (enum
> de `close_reason`). "Resta só" é uma afirmação de completude, e ela não era do autor do título
> para dar: quem varre o índice conclui que a seção está a um item de fechar. *O erro nº 5 tem uma
> forma pior que o título desatualizado — o título que resume a MAIS do que sabe.*
>
> **Terceira correção — 2026-08-07, e desta vez o título errava para o LADO OPOSTO.** Ele listava
> *"seguem abertas 2/3/4/6"*, e conferido contra o código: a **3** já estava marcada fechada na
> própria tabela desta seção (2026-08-05, sem código); a **6** foi fechada pela **Fase E**
> (`_TRANSPORT_TO_SEGMENT_CLOSE_REASON`, `orchestrator-bridge/main.py:3256`, separado do de contato
> em `:3194`, com `test_segment_close_reason_domain.py` assertando não-sobreposição); a **2** está
> meio-fechada desde 08-03 e teve a outra metade *reenquadrada pela medição*; e a **4** perdeu o
> `force-complete` em 08-05. O corpo, mais abaixo, ainda dizia **"Próximo: Fase E"** — com E e F
> concluídas em 08-04 e registradas no `CLAUDE.md`. *Título que superestima o pendente custa
> diferente do que subestima: ele não faz ninguém pular a seção, faz gastar a sessão re-medindo o
> que já foi medido. Foi o que quase aconteceu ao escolher o próximo item por esta linha.*

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
| 2 | **Não há reaper de `claim_lease`** — ~~vaga presa~~ ✅ **2026-08-03**; janela de invisibilidade SEGUE ABERTA | nenhum poller varre `*:pool:*:claim:*`; a lease expira passivamente. Defeito da família pull inteira (aprovação também), não do wrap-up. ✅ docstring do heartbeat inexistente corrigido (2026-07-30) — e **corrigido de novo em 2026-08-03**, porque o substituto afirmava outra rede que também não existe (ver § abaixo). Instrumento: estado `orphaned` do relatório de pendências, ✅ **CALIBRADO em 2026-07-31** (smoke 14/14). **O que fechou:** a VAGA, que nunca voltava — ver § "Lacuna 2 — o que fechou e o que não" |
| 3 | ~~**O TTL de fila existente nunca alcança fila pull**~~ | ✅ **NÃO É DEFEITO — item mal especificado, fechado 2026-08-05 sem código.** Ver § "Lacuna 3" abaixo |
| 4 | **Nenhuma ação de terceiro encerra item de tarefa** — ⚠️ **ÚNICA LACUNA COM DEFEITO VIVO** | ✅ fila pull (`/api/work_queue/expire/:sessionId`). ✅ **`force-complete` — 2026-08-05**, ramificado em 200/404/501, probe 9/9 (ver § "Lacuna 4" abaixo). Resta `/v1/workflow/instances/:id/cancel` = **410 hard** — e "inerte" era descrição errada: **4 telas o chamam** e a mensagem do 410 aponta um substituto **que não existe**. Ver § "Lacuna 4b" abaixo |
| 5 | ~~**A fila pull não é consultável pelo analytics**~~ | ✅ **resolvido para a pergunta operacional (2026-07-30)**: `GET /api/work_queue/pending` varre o ledger `{t}:work_task:*` e cobre as duas formas de pendência com uma linha só (o claim não apaga o ledger). Segue sem evento/tabela espelho — o histórico do **nunca-reivindicado** continua sem fonte (fatia 3, gated) |
| 6 | ~~**`close_reason` de segmento não tem enum**~~ | ✅ **FECHADA pela Fase E (2026-08-04).** Os mapas foram separados por domínio: `_TRANSPORT_TO_SEGMENT_CLOSE_REASON` (`orchestrator-bridge/main.py:3256`, vocabulário de SEGMENTO) × `_TRANSPORT_TO_CLOSE_REASON` (`:3194`, enum de CONTATO), com `test_segment_close_reason_domain.py` assertando que os conjuntos não se cruzam. O `contact-segment.ts:83` segue `z.string()` livre **de propósito** — o domínio de segmento é aberto (`task_submitted`, `acw_expired`, `agent_release_item`, …); o que estava errado não era a ausência de enum, era o mapa compartilhado escolhendo domínio em silêncio. Registro original abaixo. ⟨histórico⟩ `contact-segment.ts:83` é `z.string()` livre; `task_submitted`/`session_teardown`/`acw_expired`/`acw_supervisor_closed` são literais no publish do bridge. O enum fechado (`CloseReasonSchema`, `common.ts:44-56`) é o de SESSÃO — domínio diferente. **O `_TRANSPORT_TO_CLOSE_REASON` do bridge serve os DOIS** (`main.py:5755` = contato; `:6401` = segmento), e por isso todo `agent_disconnect` (um F5 no Console) gera segmento SEM `close_reason`, com aviso no log. Conserto = separar os mapas, não estender o compartilhado. Ver § "um F5 no Console devolve à fila um item em trabalho" |

### Lacuna 4 — `force-complete` ✅ *(2026-08-05; resta só o `cancel` 410)*

Detalhe em `CHANGELOG.md` § "`force-complete` deixou de mentir". Aqui fica só o que serve ao
próximo item, porque **os dois achados não estavam na descrição da lacuna** e valem como método:

- **A lacuna descrevia o handler; o defeito estava no caminho.** Ela dizia *"só reescreve uma chave
  Redis"* — verdade sobre o handler, e irrelevante na prática: as duas chamadas da UI iam **sem
  `Authorization`** num endpoint que exige `supervisor|admin`, logo tomavam **401 antes de chegar
  lá**. O comportamento descrito só era alcançável por curl. *Ao auditar um endpoint, ler também
  QUEM o chama e COM O QUÊ — senão descreve-se um trecho que ninguém executa.*
- **Chave com TTL não serve de condição de existência.** O `404 session_not_found` vinha de
  `session:{sid}:meta` ausente — e o caso que motiva o botão é justamente a sessão parada há muito
  tempo, cujo `meta` já expirou. O guarda escondia exatamente o alvo.
- **Um 501 que NOMEIA a ausência vale mais que um flag falso.** Abortar pipeline em execução não
  tem mecanismo (o engine não consulta cancelamento). Inventar um campo que ninguém lê seria repetir
  o defeito com outro nome — foi o que o endpoint fazia.

**Aberto na mesma linha:** `/v1/workflow/instances/:id/cancel` segue **410 hard**. ✅ **Conferido em
2026-08-07** pelo mesmo levantamento — e rendeu de novo dois achados fora do enunciado. Ver § abaixo.

**Não coberto:** a mudança de UI foi verificada por leitura, não executada. O probe exerce o
endpoint por curl.

### Lacuna 4b — o `/cancel` 410 *(levantado 2026-08-07; sonda escrita, medição pendente)*

O enunciado dizia *"segue **inerte**"*. Inerte sugere endpoint sem chamador. Medido por leitura:

- **410 hard sem handler** — `workflow-api/router.py:462`, só `raise HTTPException(410, …)`.
- **Quatro telas o chamam**, com o corpo idêntico: `ProcessosPage.tsx:414`, `WorkflowsPage.tsx:52`,
  `WorkflowMonitorPage.tsx:69`, `MonitorTab.tsx:642` — `confirm(…confirmCancel)` → `cancelWorkflow()`
  → `catch { alert(String(e)) }`. `cancelWorkflow` (`hooks.ts:221`) lança `HTTP ${status}` e
  **descarta o corpo**, então o operador confirma um cancelamento e recebe um `alert` dizendo
  **`Error: HTTP 410`**, sem nada mais.
- **A mensagem que ele não vê aponta um substituto inexistente.** O 410 instrui *"cancel webhook
  sessions via the channel-gateway (`DELETE /v1/channels/webhook/{session_id}`)"* — e o
  channel-gateway **não tem nenhuma rota `DELETE`**: a superfície webhook é POST
  trigger/resume/pool/collect/delegate + `GET …/status`.

**Modo de falha invertido em relação ao `force-complete`.** Lá a mentira era de SINAL (`200 ok:true`
sem fazer nada). Aqui o status é honesto — 410 é o certo para deprecado — e a mentira está no
**ponteiro**: manda usar um caminho que ninguém construiu. É a mesma forma do docstring do
`_claim_lease_key`, que citava uma segunda rede inexistente: *o 410 tem cara de decisão
arquitetural tomada (Arc 19 Fase D), e por isso ninguém foi conferir se o substituto nasceu.*

**Pré-requisito NÃO verificado do conserto óbvio.** Religar as 4 telas a
`POST /api/force-complete/:sessionId` (BFF, JWT `supervisor|admin`, já ramificado 200/404/501) é o
caminho natural — é literalmente o "encerramento por terceiro" que a D4 pedia. Mas ele é endereçado
por **`session_id`**, e a linha da lista de instâncias traz `session_id?` **opcional**
(`hooks.ts:24`). Se vier vazio, o botão troca `HTTP 410` por `HTTP 404` — defeito novo com data
recente, que é o pior tipo. **Medir antes:** `infra/test/probe_workflow_cancel_callers.sh` conta a
cobertura de `session_id` nas linhas reais e ramifica o veredicto em três estados.

**Três saídas, e a medição escolhe** (não decidir antes do número):

| Cobertura de `session_id` | Conserto |
|---|---|
| ~100% | religar as 4 telas ao `force-complete`; apagar o `cancel` e a rota deprecada |
| parcial | o botão precisa **declarar por que não pode agir** naquela linha (e as linhas sem `session_id` são o próprio diagnóstico: instâncias pré-Arc 19) |
| ~0% | o botão não tem alvo sob o modelo unificado — remover das 4 telas fecha a lacuna sem backend novo |

#### Medição de 2026-08-07 — respondeu, e por outra via

`probe_workflow_cancel_callers.sh`, 2ª execução (a 1ª caiu no preflight: pingava `/health`, e o
serviço expõe `/v1/health` — caminho copiado do comentário errado em `docker-compose.demo.yml:207`).

Resultado: **`workflow.instances` tem ZERO linhas em `tenant_demo`**, com o serviço de pé e o
endpoint respondendo. Isso reprovou a P1 e, pela regra da própria seção, seria *ausência de amostra*
— o probe declarou INCONCLUSIVO, corretamente. **Mas a pergunta não precisava da amostra**, e a
evidência que a fecha é estática:

1. **A tabela tem UM único escritor**: `db_create_instance` (`db.py:252`), chamado num só lugar —
   `POST /v1/workflow/webhook/{webhook_id}` (`router.py:794`), o gatilho legado por token.
2. **Esse escritor grava `"session_id": None` HARDCODED** (`router.py:799`). Logo a cobertura de
   `session_id` nesta tabela é **0% por construção**, não por amostragem: nenhuma linha que ela
   possa vir a ter jamais terá `session_id`.
3. **O caminho canônico não escreve nada aqui.** `POST /v1/workflow/trigger` (`router.py:158`) é,
   desde o Arc 19 Fase D, um *proxy* para o channel-gateway: cria sessão e devolve `session_id`,
   sem linha em `workflow.instances`.
4. **Uma linha criada nunca muda de estado.** `persist-suspend`, `complete`, `fail`, `cancel`,
   `collect/persist` e `collect/respond` são **todos 410**. O único mutador vivo — `/v1/workflow/resume`
   no ramo legado — exige `status == 'suspended'` (`router.py:299`), e nada pode pôr uma linha em
   `suspended`. Ela nasce `active` e **congela ali para sempre**.

**Conclusão: saída (c).** O `force-complete` está descartado como conserto — ele é endereçado por
`session_id`, que esta tabela nunca tem. Não há backend a construir: as 4 telas listam uma tabela
cujo único escritor é legado e cujas linhas são imutáveis por desenho.

> *Método, e é o mesmo escorregão registrado na § Lacuna 2 com outro nome:* a sonda foi desenhada
> para **esperar o fenômeno** (contar linhas, classificar ramos) quando a resposta estava legível
> no **produtor** desde o primeiro minuto — `session_id: None` é uma constante no código, não uma
> distribuição a medir. Antes de instrumentar uma leitura, perguntar se o ESCRITOR já responde.
> A sonda não foi perdida: é ela que prova o estado *vazio* da lista, que a leitura de código
> sozinha não provaria.

#### Executado em 2026-08-07 — saída (c) aplicada ✅

- **platform-ui**: `cancelWorkflow` removida (`hooks.ts`), com o motivo medido no lugar dela; botão
  + `handleCancel` fora das 4 telas; prop `onCancel`/`canCancel` dos dois `InstanceDetail`; 4 chaves
  i18n nos **dois** locales (`instance.confirmCancel`, `instance.cancelInstance`,
  `processes.instances.confirmCancel`, `…detail.cancelButton`). `refresh` saiu do `MonitorTab` (não
  tinha outro consumidor — o polling de 10 s já mantém a lista viva).
- **workflow-api**: rota apagada (era `status_code=410`); teste virou `test_cancel_route_is_gone`
  assertando **404**, não `!= 410` — `!= 410` passaria se alguém reintroduzisse a rota com outro
  código, que é a regressão que o teste existe para pegar.
- **e2e**: Parte F do cenário 13 e `WorkflowClient.cancel()` removidas.
- **Gate novo**: `infra/test/gate_orphan_ui_callers.sh` — falha se alguma tela chamar rota declarada
  com `status_code=410|501`. Estático (sem stack, sem efeito colateral), com **contador-testemunha**
  (zero rotas duras ⇒ INCONCLUSIVO, porque o zero mediria o detector). Distinção central: 410/501
  **condicional** não conta — o 501 do ramo 2 do `force-complete` é o padrão BOM que a I5
  estabeleceu, e marcá-lo ensinaria a esconder a ausência. Previsão contada: **5 rotas duras, 0
  órfãos**.

**Validação ✅ 2026-08-07** — `test_cancel_route_is_gone` PASSED **nomeado** (`-v --no-header`) e da
imagem RECONSTRUÍDA: `workflow-api` não tem volume mount, então a 1ª rodada (`48 passed`, sem nomes)
exercitou código velho + teste velho, coerentes entre si — passaria de qualquer jeito. Gate
`gate_orphan_ui_callers.sh` verde (5 rotas duras, 0 órfãos). UI conferida **nas duas telas
alcançáveis**, com fixture `skill_probe_ui_v1` (`status=suspended`, que era exatamente a condição de
render do botão): `/flow/monitor` › Processes e `/flow/processos` › Instances abrem o painel de
detalhe **sem** o rodapé vermelho.

**Correção do próprio levantamento:** eram *quatro chamadores em código*, mas só **duas telas
alcançáveis** — `WorkflowsPage.tsx` e `WorkflowMonitorPage.tsx` não são importadas por ninguém
(`routes.tsx:106,108` mandam `/workflows` e `/workflow/monitor` para `/flow/monitor`). Arquivos
mortos, e a frase "quatro telas vivas" superestimava a gravidade. *Contar call sites não é contar
superfície: o roteador é que decide o que existe.*

**O que a validação mostrou de graça (argumento da saída removida do escopo):** o painel exibe um
processo `Suspended` que não oferece **ação nenhuma** — nem cancelar, nem retomar. Coerente com o
achado: as linhas nascem `active`/`suspended` e congelam. A listagem inteira é candidata a remoção
sob o Arc 19, mas isso é decisão de produto, não conserto de defeito. *(O chip de filtro
"Cancelled" segue na tela e sempre volta vazio — defensável, porque linhas legadas de tenants reais
podem ter esse status; anotado para não ser "descoberto" de novo.)*

#### Quatro sintomas independentes, uma decisão de produto *(2026-08-07)*

Não construir nada com isto — é o dossiê para quando a remoção da listagem for decidida. Cada linha
foi observada por uma via diferente, o que é o que dá peso ao conjunto:

1. **Nada a fazer** — o painel de detalhe não oferece ação alguma (visto na tela, após a remoção).
2. **Nada muda** — todo mutador é 410; o único vivo exige `suspended`, estado inalcançável.
3. **O campo canônico está vazio** — a tabela TEM `pool_id`, e o único escritor grava `None`
   (`router.py:801`). Por isso o filtro da tela é por **`flow_id`** (= `skill_id`, o endereço
   legado), e **está certo assim**: "corrigi-lo" para pool, obedecendo o invariante ao pé da letra,
   criaria um filtro sobre coluna sempre nula — digitar qualquer pool devolveria vazio sem erro.
   *Trocar um rótulo legado honesto por um filtro que nunca casa é a armadilha do valor plausível.*
4. **O produtor é inalcançável pelo menu** — as linhas só nascem do registro legado
   `workflow.webhooks` (`flow_id` + token `plughub_wh_…`), cujo editor é o `WebhooksTab` em
   **`/workflow/calendar`** (`routes.tsx:104`): rota existente, **sem entrada em nav nenhuma**. Só
   se chega digitando a URL.

**Duas gerações de "webhook", e é isso que confunde ao ler a tela:** o canônico é o `ChannelEndpoint`
de `/config/channels` › Webhook (**slug → pool**, ex. `crm-callback` → `retencao_humano`), que cria
sessão normal; o legado é o `workflow.webhooks` (**`flow_id`** = skill), que cria linha em
`workflow.instances`. Não são o mesmo campo em telas diferentes — são a mesma pergunta respondida
antes e depois do invariante "o pool é a unidade endereçável".

➡️ **Isto virou arco próprio:** [`docs/adr/adr-webhook-endpoint-single-registry.md`](docs/adr/adr-webhook-endpoint-single-registry.md)
(proposto, 2026-08-07) — registro único + `identifier` opaco. Ver § abaixo.

---

### Portão de deriva do seed do config-api *(proposta — 2026-08-04; movido para cá em 2026-08-26)*

> Movido da § *"Guard de teardown-hook"* na poda de 2026-08-26. O cabeçalho desta seção já o nomeava
> como resíduo; o detalhe morava na seção podada.

**O achado.** Ao aplicar as chaves da Fase C, o `config-seed` reportou `inserted=2`: uma minha e
**`survey.link_delivery`, que estava em `seed.py` e nunca fora aplicada**. A fonte declarativa e o
store estavam divergentes havia tempo indeterminado, e ninguém notou — porque não há o que notar. O
modo de falha é mudo dos DOIS lados: a UI simplesmente não lista a chave, e o leitor no código cai
no seu próprio default. Lê-se como *"config com valor padrão"*, não como *"config inexistente"*.

*(Neste caso o valor semeado era igual ao default do código — `mock`/vazio — então nada mudou de
comportamento. Isso é sorte, não garantia: nada obriga os dois a coincidirem.)*

**Proposta (barata).** `infra/test/gate_config_seed_drift.sh`: varre `_SEED` e falha (vermelho) se
existir chave ausente do store, ou presente com **descrição** divergente. Transforma "descobrir por
acaso, meses depois" em CI vermelho no dia. Não deve comparar VALOR — o tenant edita legitimamente
pela tela, e um portão que exigisse valor igual ao seed brigaria com o próprio invariante
"every config field is UI-editable".

**Cuidado de implementação.** O gate precisa consultar o **store** (`GET /config/{ns}/{key}?tenant_id=`),
nunca reler `seed.py`; e `config-seed` tem **imagem própria** (`build:` separado do `config-api`),
então qualquer automação que rode o seed precisa buildá-lo antes — foi essa peça que fez a primeira
tentativa de aplicar as chaves da Fase C rodar o `seed.py` antigo em silêncio.

### Timeouts ainda constantes no caminho da I5 *(arco de consolidação de config; movido em 2026-08-26)*

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

---

## Wrap-up unificado — resíduos após a Phase 2 ✅ *(arco fechado 2026-07-27, ver CHANGELOG)*

**Polish (não bloqueia):** latência do auto-atendimento (~2-3s do poll da inbox) → instantâneo bombando o
`refreshSignal` do `PullInboxPanel` no `conversation.assigned`. **Agora é seguro**: antes da Phase 2 o claim
instantâneo AUMENTARIA a chance de chegar antes do release (`-1` → cai na inbox); com o hold, as duas ordens
são cobertas. E: UI para a config de `dispatch` inline/detached do hook (hoje só YAML — invariante "config
UI-editável" pendente para hooks de pool).

**Camada E2 restante:** ~~**E2f**~~ ✅ (2026-07-29) · ~~**Camada F**~~ ✅ **2026-07-30** (F1 atribuição,
F2 G1 no relatório, F3 pull direcionado 5/5, F4 expiração — ver CHANGELOG). **Arco A–F completo.**
Resíduo da F: a **lease** não foi medida (a sonda observou a chave de outra sessão), e o que ficou
provado é que o **prazo** devolve a vaga. ⚠️ **Atualizado 2026-08-03** — a frase seguinte dizia
*"a lacuna 2 segue como estava"*, e não segue: o prazo **só** devolvia a vaga porque a sonda pegou
o caso de lease viva; com a lease expirada (o caso normal, 180 s × 24 h) a vaga ficava presa. Ver
§ I5 → "Lacuna 2 — o que fechou e o que não". *A medição que faltou é a que teria mostrado isso.*
*(E2e — produtor do marker `acw_pending` — **saiu de escopo** com a remoção da Camada C, 2026-07-29.)*

**Resíduo herdado da seção `close_reason` (podada em 2026-08-03; o fix está no CHANGELOG de 07-30):**
o `_TRANSPORT_TO_CLOSE_REASON` cobre **6 transportes**; qualquer outro agora produz `close_reason`
ausente **com WARNING**, em vez do `agent_hangup` inventado de antes. **Gated em evidência:** completar
o mapa só se o WARNING aparecer em produção — `infra/test/check_close_reason_persisted.sh` tem uma
asserção que o varre nos logs. *Ausência barulhenta é o estado desejado, não o pendente.*

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
| 2 | **Arc 12 `segment_id`** em `agent_business_events` (plano A+C já decidido, seção própria) | ✅ 2026-08-03 — **coluna existe, e nunca recebeu dado real** (ver abaixo) |
| 3 | **Capture de wrap-up** — roteamento no `segment_outcome_record` | ✅ **backend 2026-08-03** (smoke 14/14) — **resta o editor** |
| 4 | **Relatório de wrap-up** — cai sobre `/reports/agent-events/*` (série/summary/categorias já existem) | **destravada** — o Arc 12 passou a ter dado real com `segment_id` |

**Medição de 2026-08-03 que reenquadra as fatias 3 e 4** (`infra/test/probe_block2.sh`):

| Onde o wrap-up grava HOJE | Medido |
|---|---|
| `segments.wrapup_summary` + `segments.wrapup_next_steps` | **13 segmentos**, o último em 2026-08-03 18:06 |
| `agent_business_events` (Arc 12) | **1 linha, de seed**, zero com `segment_id` |

Logo a fatia 3 **não acrescenta captura — ela muda o sink**, e ao fazê-lo passa a ser o
primeiro produtor vivo do Arc 12.

**D6 — os dois sinks COEXISTEM** *(decidido 2026-08-03)*. Não é uma migração: é uma divisão por
natureza do dado, coerente com a D2 desta seção e com `clickhouse.py:470` (*"prosa não cabe em
`agent_business_events`: lá `value` é numérico e o nominal vive na CATEGORIA"*).

| Dado | Sink | Por quê |
|---|---|---|
| resumo em prosa, próximos passos | `segments.wrapup_summary` / `wrapup_next_steps` | texto livre não é `value` numérico nem folha de categoria |
| FCR (pontuável), serviço/motivo (nominal) | `agent_business_events` (Arc 12) | é o que agrega, série histórica, cruza por `segment_id` |

**Corolário que a decisão obriga a escrever, senão ela não vale:** para cada pergunta do
relatório existe UMA fonte, declarada. Prosa nunca é agregada; contagem/taxa nunca sai de
`segments`. Escrever nos dois lugares sem essa regra é como nasce a divergência que não
reconcilia — o mesmo defeito que a D3 previne na folha nominal (`options[].value` como lista
controlada, senão `troca_titularidade` × `troca_de_titularidade` viram duas séries).

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

## Visibilidade seletiva da sessão de wrap-up em Analytics/Sessions *(proposta 2026-08-11, ver ADR §7)*

> **Origem:** operador rodou um E2E completo e não achou o wrap-up em `Analytics > Sessions` — nem como
> segmento (não é; gravação por referência, §3α D3 do ADR), nem como sessão própria (é, mas some). Causa:
> E2f (`pools.purpose = 'internal'`) exclui de forma **incondicional**, sem parâmetro de override, e isso
> vale mesmo com `accessible_pools` liberado — não é bug de permissão, é o comportamento como fechado.
> **Desenho fechado em [`docs/adr/adr-wrapup-detached-pull.md`](docs/adr/adr-wrapup-detached-pull.md) §7**
> (emenda ao ADR já dono do trade-off, não ADR novo). Este item é só o rastreio de implementação.

**Decisão (ADR §7):** visibilidade ≠ contagem. Todo agregado (TMA, "N contacts", métricas de
pool/agente) continua excluindo `purpose=internal` sem exceção — E2f não é reaberto. A **listagem**
(`/reports/sessions`) ganha parâmetro opcional `scope: contacts|all` (default `contacts`, idêntico ao
comportamento atual). Associação ao contato pai é por **`origin_session_id`** (já gravado de forma
confiável nos dois modos de dispatch — inline e detached — desde o fix de 2026-07-27), não por Journey;
Journey artificial de 1+1 para todo wrap-up foi descartada por poluir `/reports/journeys` com processos
triviais.

### Fatias

| # | Entrega | Estado |
|---|---|---|
| 1 | `scope=contacts\|all` em `GET /reports/sessions` — filtro condicional em vez de incondicional; cabeçalho de contagem sempre lê `scope=contacts` mesmo com a tabela expandida | ✅ **2026-08-11** (ver CHANGELOG) — `meta` passou a devolver `total` (paginação) + `total_contacts` (cabeçalho) + `total_internal` |
| 1b | **Marcar a linha como interna na resposta** — o veredicto `purpose=internal` é computado no backend (`_internal_pools_for`) e descartado; a UI recebe só `pool_id` e não tem como saber | ✅ **2026-08-11** — `is_internal` por linha (`_mark_internal_rows`) + `meta.internal_pools_known` (contagem, não flag de saúde). Entra no CSV de graça (`_to_csv` tira as colunas da 1ª linha) |
| 2 | Coluna/badge "Origin" na linha da sessão interna (quando `scope=all`), linkando para o `session_id` pai via `origin_session_id` | ✅ **2026-08-11** — coluna **`parent`** ("Contato de origem"), **não** `origin`: `lista.columns.origin` já é **ANI** nesta tabela (e `destination` é DNIS); reusar o nome daria duas "Origem" com sentidos diferentes na mesma linha. Só renderizada com o toggle ligado — fora dele não há linha interna, e coluna vazia prometeria vínculo inexistente |
| 3 | Toggle "Incluir sessões internas (wrap-up, dispatch)" na UI (`ListaTab.tsx`, ns i18n `contacts`), desligado por padrão; tag visual por `row.is_internal`. Cabeçalho lê `meta.total_contacts`, paginação lê `meta.total`; com `meta.internal_pools_known == 0` **não oferecer o toggle** (não há como distinguir nada — não prometer o recurso) | ✅ **2026-08-11** — medido na tela: desligado `12 contacts`, ligado `12 contacts · 9 internal` com o cabeçalho **imóvel** (é a imobilidade, não a aparição das linhas, que prova o §7.2). Toggle desabilitado durante o fetch: o `pendingRef` do `load` descarta requisição concorrente, e um clique em voo seria no-op silencioso |
| 4 | Isentar o drill-down de UMA journey já aberta (`journey → sessions → segments`) do filtro E2f — sempre mostra sessões internas associadas, independente do `scope` da listagem topo | ✅ **2026-08-11** — mesma válvula do `session_id` em `_fetch_sessions` (`if not session_id and not root_session_id`), com controle negativo no teste (a listagem sem `root_session_id` TEM de manter a exclusão) |
| 4b | **Segundo número no card da journey** — "3 contatos · 1 interna", para o card não discordar do drill que ele expande | ✅ **2026-08-11** — `internal_session_count` por pós-passe (`_attach_journey_internal_counts`), bounded à página, no padrão do `_attach_journey_signals`. **Não** entrou como agregado da query principal: para contar as internas ali elas teriam de entrar no `WHERE` e contaminariam `channels`/`pool_ids`/`open_count` e o wall-clock do processo — o G1 reaberto um nível acima |

`spawned_from_root` **não** precisa de isenção: sessão interna nasce com `journey: "inherit"`, então
nunca atravessa a fronteira e nunca aparece nessa lista.

**Limites conhecidos do `scope=all` (fatia 1, as-built):**

- Mostra sessão de **pool interno**, não "tudo que é interno": hook que roda NA CONFERÊNCIA (NPS
  inline) não tem sessão própria e é filtrado pela regra do CANAL, que `all` **não** relaxa (relaxá-la
  duplicaria sessão ativa — ver CHANGELOG). O rótulo do toggle tem de dizer isso.
- ~~**`format=csv` herda o `scope`** e exporta os dois domínios sem coluna que os separe.~~ ✅ fechado
  pela 1b: `is_internal` está na linha, e o `_to_csv` monta o cabeçalho a partir das chaves dela.
- **Escopo ABAC — premissa REFUTADA por medição (2026-08-11); sobra um caso estreito.** A redação
  original dizia que a linha da SESSÃO de wrap-up carrega `wrapup_detached_ia` (pool webhook, não
  espelho), e daí que supervisor com escopo restrito ligaria o toggle sem ver nada. Medido:
  `sessions.pool_id = 'retencao_humano-int'` — o **espelho**, que `_with_internal_mirrors` já deriva.
  `wrapup_detached_ia` é o pool que **dispara** o workflow (execution metadata do trace); o que sobra na
  linha é o do último roteamento, isto é, o pool onde o humano reivindicou o item. **Para o wrap-up
  atendido não há problema de escopo.**
  **O que resta medir:** wrap-up que nunca é reivindicado (expira por prazo, `acw_expired`) pode nunca
  ser roteado a um espelho e reter o pool webhook em `sessions.pool_id` — nesse caso a linha só aparece
  para quem tem `wrapup_detached_ia` no escopo. Repetir a query sobre uma sessão expirada; se confirmar,
  decidir entre incluir o pool webhook no escopo derivado ou logar a ausência.
  ```
  SELECT session_id, pool_id, origin_session_id FROM plughub_demo.sessions FINAL
  WHERE tenant_id='tenant_demo' AND session_id LIKE '%<sufixo>'
  ```
  (banco = `plughub_demo` no demo, `plughub` por default; **`analytics` não existe em ambiente nenhum** —
  é o nome usado nos testes e o `{db}` interpolado nas queries.)
  Se vier o espelho, apagar este bullet e corrigir a nota gêmea no CHANGELOG (fatia 4b, "Achado de
  passagem"); se vier `wrapup_detached_ia`, então há DOIS produtores de `pool_id` para a mesma sessão e
  o item deixa de ser sobre ABAC.

**Guardrails (não reabrir o que E2f fechou):** nenhum endpoint de agregado aceita/lê `scope`; `scope=all`
não se estende a `/reports/journeys` (listagem topo), só a `/reports/sessions` e ao drill-down do item 4.

**Não-objetivos:** contar sessões internas em TMA/contagem de contatos; Journey sintética para contato
sem processo multi-sessão; mostrar o conteúdo respondido do wrap-up (`wrapup_summary`/`wrapup_next_steps`,
já existente em `segments` mas sem UI nenhuma) — gap real, mas separado, ainda sem item próprio aqui.

---

## Detach de hooks de finalização + Pull direcionado + ACW *(desenho fechado 2026-07-23)*

> ⚠️ **SEÇÃO ESTAGNADA NO PLANO DE 2026-07-23 — não usar como estado.** Corrigido em 2026-08-20 por
> medição do código (ver CLAUDE.md § homônima). Este cabeçalho dizia *"Camada A iniciada"* e o bullet
> da **E2** dizia *"pendente"*, quando A–F foram entregues entre 24 e 30 de julho; o tell é que a
> própria seção ainda lista a **E2e** como escopo, item que morreu com a reversão da Camada C em 07-29.
> O estado vivo está em **§ "Camada E2 restante"** (~linha 2652) e no `CHANGELOG.md:6542`
> (*Camada F — validação do arco de detach de hooks ✅ 2026-07-30*).
> **O que sobra de aberto, medido:** (a) a F4 declara a própria lacuna — a **lease** não foi medida e
> não há reaper; (b) **não existe gate re-executável da Camada F** (validada por medição manual, com
> os smokes de B/D/R0/I5 reaproveitados via `DISPATCH`/`ACW_HOURS` em
> `infra/test/smoke_internal_work_queue.sh:85-89`).
> O texto abaixo fica como **registro do desenho original**, útil para ler a intenção — não o as-built.

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
- **E2 — wrap-up humano → `detached`** *(o texto abaixo é o PLANO de 07-23; entregue em 24/27/29 —
  ver § "Camada E2 restante" ~2652. O desenho unificado de 07-27 SUPERA este plano: `inline` e
  `detached` viraram dois modos de ENTREGA da mesma máquina, e o wrap-up não tem skill próprio):*
  `agente_wrapup_v1`/`wrapup_ia` (inline hoje) vira item de pull
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
- **F — validação ✅ 2026-07-30** (`CHANGELOG.md:6542`): G1 no relatório (F2), atribuição de segmento (F1),
  pull direcionado 5/5 em duas execuções (F3), expiração com duração real (F4). **Duas lacunas, ambas
  nomeadas pela própria entrada:** a **lease** não foi medida (sem reaper — lacuna 2 aberta), e a
  validação **não deixou gate versionado** — rodou por medição manual instrumentada, com os smokes das
  camadas anteriores reaproveitados por override de env. Escrever esse gate é trabalho em aberto: um
  arco declarado completo sem gate re-executável é lembrança, não verificação.

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

### Achados de 2026-08-04 (na validação do F2) — três, em ordem de gravidade

1. ~~**A recusa de claim é EFÊMERA demais**~~ ✅ **2026-08-04 — PREMISSA FALSA, medida na tela.**
   Esta é a **terceira** redação do item, e as duas primeiras erraram pelo mesmo motivo: descreviam
   a tela a partir do que se esperava do código. O que a leitura dos handlers + o experimento
   acharam:

   · **A inbox não tem botão de claim.** A linha do item é um `<button>` único que abre o preview
     (`PullInboxPanel.tsx` §342). O `handlePull` — o do `setError` que as redações anteriores
     chamavam de "erro fixo no painel" — tem **um único chamador**: o efeito de auto-atendimento do
     wrap-up (§254). O operador nunca o dispara clicando.
   · **E esse erro se apaga sozinho**: `setError(motivo)` na §224, `await refresh()` na §225, e
     `refresh()` faz `setError(null)` na §130. Vive o tempo de um fetch. *(Contraria "degradação
     NUNCA é silenciosa", mas só no caminho do auto-atendimento — item próprio, abaixo.)*
   · **O claim manual é só o do preview, e lá o botão é `disabled={atCapacity}`**
     (`AgentAssistPage.tsx` §681). Com as vagas cheias **não há clique, logo não há toast**.

   **Experimento** (`infra/test/probe_claim_capacity_sources.sh` + tela, agente `human-bef14526…`,
   `max_concurrent` 3), previsto × medido — todos bateram:

   | | previsto | medido |
   |---|---|---|
   | cartões × ocupantes do semáforo | iguais | **iguais** (3 e 3, mesmos ids) |
   | botão do preview com vagas cheias | cinza | **cinza** |
   | toast de `no_capacity` | não dispara | **não disparou** |
   | teto cliente (JWT) × árbitro (registro) | 3 × 3 | **3 × 3** |

   **O gap real, que sobrou e foi consertado:** o teto nunca era exibido. O crachá dizia
   "Serving 3", não "3/3", e a única explicação do botão cinza era um `title` — hover apenas,
   inexistente no toque. Um controle desabilitado sem causa legível é lido como tela quebrada, e foi
   essa leitura ("reivindiquei e veio vazio") que disparou a investigação errada. Fix: crachá vira
   fração e ganha cor de lotação; o preview mostra "Todas as vagas em uso (3/3)" ao lado do botão.

   **Não fazer:** tornar o toast persistente. Ele não dispara neste caminho — seria conserto
   especulativo sobre um defeito não observado.

1b. ~~**`handlePull` apaga o próprio motivo de falha**~~ ✅ **JÁ ESTAVA CONSERTADO — item obsoleto,
   fechado por LEITURA + medição em 2026-08-05.** Nenhuma linha de código foi escrita para fechá-lo;
   o trabalho foi descobrir que ele já não descrevia o código. O que `PullInboxPanel.tsx` tem hoje:

   · **estado `claimError` próprio**, separado do `error` de listagem (§115) — e o `refresh()` roda
     ANTES do `setClaimError` (§241), com comentário explicando que a ordem é o conserto;
   · **faixa persistente** com botão de dispensa (§335-353);
   · **invalidação pelo fato certo**: a recusa some quando o ITEM sai da fila (§257), não quando a
     lista recarrega — recarregar não torna a recusa falsa;
   · props `claimDisabled`/`claimDisabledReason` **removidas** (§51-55, com lápide);
   · i18n completo nos DOIS locales, incluindo os 6 `pullInbox.claimReason.*`.

   **Verificado onde importa, não só no repo:** `grep claimFailedDismiss` dentro do bundle servido
   (`/usr/share/nginx/html/assets/index-*.js`) devolve o arquivo — o conserto está na TELA, não só no
   fonte. Num pacote sem volume mount, essa é a única prova que vale; o fonte estar certo é
   compatível com o Console rodar o bundle de ontem.

   **Lição de método:** este item descrevia como "aberto" um caminho (`setError` §224 → `refresh()`
   §225) que o código já não tinha. *Entrada de TODO é comentário como qualquer outro — envelhece, e
   envelhece em silêncio.* A releitura custou minutos; implementar por cima do registro teria custado
   um conserto duplicado sobre código já correto. **Antes de abrir um item antigo, conferir contra o
   código que executa** — a mesma regra que vale para docstring, e pelo mesmo motivo.

1c. **Item reivindicado depois de sair do ZSET** *(observação, causa não determinada)*. Na montagem
   deste experimento, `509d5441…` foi removido da fila (`ZREM` → `1`) e o probe seguinte mostrou o
   semáforo **vazio**; ainda assim o item foi reivindicado com sucesso minutos depois e virou
   cartão. O `work_task_claim` §739 exige o `ZREM` vencedor (`atomic_claim_dequeue`), logo ele
   **estava** no ZSET no momento do claim — algo o repôs. Pode ser um reconciliador fazendo o certo
   (o ZSET é projeção; o ledger `work_task` é a verdade) ou um requeue perdido. **O `ZREM` foi
   mutação sintética minha** — não teorizar sobre rastro próprio; reproduzir sem ele antes de
   chamar de defeito.

2. ~~**Vaga ocupada por sessão que nunca virou cartão.**~~ ✅ **CONSERTADO 2026-08-05** — transporte
   `agent_release_item`: o mcp-server anuncia `contact_closed` depois do `released:true` do árbitro e
   o **bridge** faz o desmonte pelo caminho da queda. Escopo maior que o mínimo por medição: publicar
   `agent_released` também restaura a membership do `ready_set`, que o `work_task_release` não toca
   (sem isso o agente sumia do push a cada devolução). Validado na tela, 3 cartões no re-claim contra
   0 antes, `Skipping duplicate` = 0. Detalhe em `CHANGELOG.md` e `docs/guias/conference-mechanics.md`
   § Mudança 32; instrumento em `infra/test/probe_release_presence.sh`.

   **Lição de método, guardada porque muda como se monta reprodução:** os marcadores expiram com o
   TTL da SESSÃO. O lixo deixado como "reprodução viva" já não reproduzia nada 14 h depois — as vagas
   seguiam presas, os marcadores não existiam mais. **O rastro sobrevive ao mecanismo**, e medir o
   rastro não mede a causa. Reproduções que dependem de estado com TTL precisam ser refeitas do zero
   na sessão que as usa.

   <details><summary>Diagnóstico original (2026-08-04) — mantido pelo valor de método</summary>

   **Receita de reprodução (3/3, determinística):** reivindicar N itens → **"Return to queue"** em
   todos → reivindicar de novo. Os re-claims sobem a ocupação (1→2→3) e **nenhum cartão aparece**.

   **Cadeia, medida:**
   1. MONITOR do Redis: os três `ZREM` de claim em `…921`, `…928`, `…932` (deltas 7s e 4s), cada um
      com o `EVAL` de ocupação subindo. O árbitro concedeu as três vagas.
   2. `orchestrator-bridge`: três `Skipping duplicate routing for already-served session …
      skill_running=False human_active=True` em **22:18:41, 22:18:48, 22:18:52** — deltas **7s e 4s**.
      Mesmos eventos, casados pelo relógio.
   3. `mcp-server-plughub`: **nenhum** `Forwarding conversation.assigned` nesses instantes (os três que
      aparecem no log são da 1ª rodada de claims, 22:08–22:09). O evento nunca chegou ao frontend —
      o que também **exclui o bundle de UI** como causa.
   4. `EXISTS {t}:session:{sid}:closed` = **0** nos três: sessões VIVAS. Vaga gasta, trabalho vivo,
      tela vazia.

   **Causa:** o guard de dedup (`main.py` §3509-3519) descarta `conversations.routed` sem
   `conference_id` quando existe `session:{sid}:human_agent`. Esse marcador é escrito por
   `activate_human_agent` (§808, `SETEX` no TTL da sessão) e apagado **só** em caminhos de
   encerramento/queda: close de contato (§2696, §6347), último humano derrubado (§6810) e
   `agent_done` (§7284). **O `work_task_release` ("Return to queue") não está entre eles** — libera a
   vaga do árbitro e deixa a presença no bridge. O re-claim vira, para o guard, uma re-emissão do
   drain periódico (que é contra o que o guard foi escrito) e é indistinguível dela.

   Mesma assimetria já registrada no CLAUDE.md sobre este caminho — *"`remove_conversation` também
   restaura a membership dos SETs do pool, que o `work_task_release` não faz"*. Mais um fato que ele
   não restaura.

   **Forma do conserto (não implementado):** limpar `session:{sid}:human_agent` + o membro
   correspondente de `session:{sid}:human_agents` na devolução à fila, simétrico ao ramo de queda do
   §6810 — o marcador significa "há humano anexado", e depois do release não há. **Preferir isso a
   afrouxar o guard**: exceção no guard trocaria um caso mudo por outro (o spam de
   `participant_joined` que ele existe para impedir), enquanto corrigir o estado torna o guard
   verdadeiro. Cuidado ao escrever o teste: o TTL do marcador é o da sessão, então "some sozinho"
   é uma janela de horas, não um conserto.

   **Tentativa de reprodução em 2026-08-04 (achado 1): NÃO reproduziu.** Três claims seguidos,
   ocupantes do semáforo == cartões na tela, mesmos ids, nenhum hold. Isso **não** absolve o
   achado — só diz que o caminho feliz não o produz. Ficou o instrumento:
   `infra/test/probe_claim_capacity_sources.sh` **identifica** cada ocupante (`SMEMBERS`, não
   `SCARD` — contar não diz QUAL) e separa hold de sessão, então da próxima vez que a vaga sumir
   basta rodá-lo para ter o id do ocupante sem cartão. Vale rodar logo após um wrap-up inline: a
   janela do hold é curta e é a única ocupação legítima que nunca vira cartão.

   </details>

3. ~~**Rótulo "Reserva expirada"**~~ ✅ **2026-08-04** — os crachás de `reservedToMeExpired` e
   `overflowed` foram **removidos**, não reescritos. Primeira tentativa trocou os dois por "Aberto
   a todos"; o dono do produto apontou o erro: **a ausência de crachá já é a notação de "qualquer
   um pode pegar"** (é como `shared` é exibido), então o selo anunciava o default e competia com o
   único que carrega informação, "Reservado a você". Os nomes antigos descreviam o que acontecera
   com a RESERVA e eram lidos como "o item morreu". O estado segue no `reservationOf` — filtro e
   ordenação intactos; só a marca visual saiu.

   **E a ordenação foi junto**, pela regra geral que o dono do produto formulou: *item devolvido à
   fila preserva o timestamp original, logo é ordenado normalmente pela espera*. `first_queued_ms`
   já existe exatamente para isso. Então `rank` 0 ficou só com `reservedToMe` (dentro da janela —
   exclusivo, ninguém mais pode pegar, o topo não fura fila); expirado e transbordado voltaram à
   ordem por espera. **Achado colhido na mesma passada:** o `sort` usava `queued_at_ms` (que RESETA
   na devolução) enquanto a tela exibia idade de `first_queued_ms` (que não reseta) — a lista
   parecia ordenada pelo número mostrado e não estava. Agora desempata pelo próprio `ageOf`, o que
   torna a ordem conferível a olho.

4. ~~**Requeue carimba score NOVO no ZSET**~~ ✅ **CONSERTADO 2026-08-05 — e a descrição do item
   estava errada.** Não era "o item perde o lugar". São **duas fontes de tempo**: o aging e o
   `max_wait_exceeded` leem o JSON `contact.queued_at_ms` (que já era preservado), enquanto a posição
   publicada ao cliente (`get_queue_rank`) e a urgência de SLA do pool (`get_oldest_queue_wait_ms`)
   leem o **score do ZSET** (que reiniciava). Quem decidia o atendimento estava certo; quem o cliente
   via, não. Conserto: score = `queued_at_ms` original, que é o que o rollback do `work_task_claim`
   já fazia. Medido no mesmo item, mesma chamada: score **voltou** de `…355601` para `…433275`
   (== `first_queued`), com os outros dois itens da fila como controle na mesma saída. Detalhe em
   `CHANGELOG.md` e `docs/adr/adr-work-item-requeue-and-agent-affinity.md` § **D2b**.

   **Lição:** o discriminador `score > first_queued` do `probe_release_presence.sh` existia **por
   causa do defeito** e morreu com ele — todo item devolvido passaria a parecer virgem, e o gate do
   achado 2 viraria verde vazio sem nada ficar vermelho. Trocado por `session:{sid}:segment_seq`.
   *Instrumento que depende de um defeito precisa ser revisto no mesmo commit que o conserta.*

   *(A pendência sobre `get_queued_contacts`/ZREVRANGE que este item deixou aberta foi fechada no
   mesmo dia — ver item 6.)*

6. ~~**`get_queued_contacts` lê ZREVRANGE**~~ ✅ **CONSERTADO 2026-08-05 — e a pergunta de produto
   já estava respondida pelo código.** O item mandava decidir antes *"o score é timestamp ou
   prioridade?"*. Não era escolha: há **um único escritor** do score (`add_queued_contact`) e ele
   grava `queued_at_ms`; e prioridade **não pode** ser um score armazenado, porque
   `score_contact_in_queue` depende de `now_ms` (aging e breach crescem com a espera) — gravá-la seria
   gravar um valor que nasce velho. O `"queue_scorer may override with priority"` do docstring
   descrevia um caminho inexistente, e foi ele que autorizou a leitura invertida.

   **Eram DOIS leitores** (este item registrava um): `get_queued_contacts` (janela 10 → `Router.dequeue`,
   só pool push, consequência = **atendimento**) e `listQueue` (janela 20 → inbox pull do Console,
   consequência = **visibilidade**, e invisível de propósito: o Console ordena por idade o que
   RECEBEU, então a lista parecia ordenada e estava sem o começo). Medido em fila de 25 com previsão
   escrita antes: antes `06..25` e `16..25`; depois `01..20` e `01..10`. Detalhe em `CHANGELOG.md`;
   instrumento em `infra/test/probe_queue_window_order.sh`.

   **Fica em aberto, e é da JANELA, não do sentido:** contato de tier alto que chegue além do corte
   também não é pontuado. Ordenar pela espera é estritamente melhor (o aging é monótono no tempo de
   espera), mas não zera o efeito para `base_priority`. Não consertado de propósito — leitura integral
   tornaria o drain O(fila). Se virar requisito, o desenho é uma segunda passada por tier, não a
   remoção do limite.

   **Lição de método, guardada porque mudou como esta suíte valida:** o controle negativo por fora
   (reverter → rebuildar → rodar → restaurar) falhou **duas vezes**, das duas por pular o rebuild, e
   das duas o resultado pareceu resposta — `238 deselected` (nenhum teste selecionado, exit 0) e
   depois `2 passed` contra um container ainda consertado. Substituído por um teste **diferencial**
   que lê a mesma fila pelas duas semânticas e exige que divirjam. Ele ainda cobre o que o ritual
   manual nunca cobriria: fixture encolhida para dentro da janela faz as duas leituras coincidirem,
   os testes de sentido seguem verdes e param de discriminar — agora isso fica vermelho.

5. ~~**Janela de ordenação entre `conversations.events` e `conversations.routed`**~~ ✅ **MEDIDO
   2026-08-05 — risco caracterizado, sem conserto; VIGIAR.** Três coisas mudaram na descrição.

   **(a) A causa é maior do que "dois tópicos sem ordem entre si".** O bridge tem UM consumidor para
   os seis tópicos e despacha com `asyncio.create_task(_dispatch(...))`, sem `await` (`main.py` §9021).
   Isso **descarta a ordenação do Kafka inteira** — inclusive dentro de uma partição do mesmo tópico.
   Não há ordem entre dois eventos quaisquer, e nunca houve.

   **(b) `auto_attend` NÃO é o candidato rápido que o item afirmava.** Acelerador e cruzamento são
   mutuamente exclusivos: na MESMA aba o `refreshSignal` refaz a lista na hora após o release, mas o
   `autoAttendedRef` já tem o id (a aba não re-reivindica o que devolveu); em OUTRA aba o ref está
   vazio, mas o gatilho é o poll de 4 s. Reproduzir "pelo uso" devolveria NÃO-REPRODUZIU explicado
   pelos 4 s — inconclusivo por construção.

   **(c) O que foi medido, então, foi a JANELA** (`infra/test/probe_release_reclaim_race.sh`, 5
   rodadas, release→re-claim back-to-back num único processo para o gap ser um round-trip HTTP e não
   o custo do `docker exec`): desmonte da presença em **~30 ms** a partir do release; re-claim
   disparado a **~15 ms**; **0 engolidas** em 5/5, com 10 `Return to queue` no log como testemunha de
   que o transporte rodou. Guard **em jogo**, não isento — o claim manda `conference_id=""` e
   `work_task_claim` monta o routed com `conference_id or None` (`router.py` §816), então a condição
   `if not conference_id` (§3517) foi avaliada.

   **A margem é incidental, e não é (window − gap).** Os dois eventos atravessam o Kafka; o que
   protege é o `contact_closed` ser publicado ANTES (no início do release) enquanto o routed depende
   de todo o resto (release responder + claim ir e voltar). A margem é esse **offset de publicação**
   menos a diferença de latência dos dois handlers. Sob carga, o `create_task` pode atrasar o handler
   de `contact_closed` enquanto o de routed corre — **é aí que o risco mora, e o probe não mede isso**
   (rodada ociosa ≠ rodada sob carga).

   **Gatilhos que reabrem:** auto-claim server-side (sem round-trip de UI no meio), `pollMs` menor no
   inbox, um ref que não guarde o id, ou qualquer coisa que engorde o prólogo do handler de
   `contact_closed` até o `DEL session:{sid}:human_agent` (§6841). O conserto, se preciso, **não** é
   afrouxar o guard (trocaria um caso mudo por outro, o spam de `participant_joined` do drain): é dar
   ao routed de CLAIM um discriminador que o drain não tem.

   **Lição de instrumento:** `presence_at_reclaim` é medido quando o re-claim é DISPARADO, e o guard
   avalia quando o bridge PROCESSA o routed. O proxy **superestima** o acerto — na 1ª leitura eu
   afirmei "caiu dentro da janela 5/5" com base nele, e estava errado. Quem decide é a contagem no
   log. *Proxy medido no instante errado é um número certo respondendo outra pergunta.*

*(Medido junto, e por isso NÃO é item: no encerramento real por supervisor o árbitro devolve a vaga
e o Console derruba o cartão sozinho — `SMEMBERS` da instância vai a vazio em ≤6 s. Ver CHANGELOG
§ F2.)*

## 📂 TEMA · Roteamento e Capacidade

## `fila_humano` está declarado `agent_kind: ai` — nome e tipo discordam *(achado 2026-08-11)*

`infra/registry/tenant_demo.yaml` :168-169. **Meça antes de tratar como erro de digitação**: se o pool
tem `queue_config`, o agent-registry recusaria `ai` (`POST/PUT /v1/pools` valida *"queue_config exige
agent_kind 'human'"*) — então ou não tem fila, ou o tipo é deliberado e o **nome** é que mente.

Por que importa agora: desde 2026-08-11 o seletor de presença do Console esconde pool `'ai'`. Hoje o
`fila_humano` não está entre os pools acessíveis do admin do demo, então nada acontece. No dia em que
alguém der acesso dele a um humano, o pool **some da lista** e a explicação não estará no nome — o
sintoma vai parecer permissão, e a causa é tipo.

---

## Capacidade, licenças e isolamento entre pools *(A e B ✅ 2026-08-02/03 — histórico no CHANGELOG; resta C + fatia 4)*

> **Podada em 2026-08-03: 503 → 81 linhas.** O as-built das fatias F1–F5b e P1–P3 mora no
> `CHANGELOG.md` (14 entradas, de *"fatia 1: tag de pool no membro do semáforo"* a *"pré-requisito
> da F3, F5 e o fóssil em quarentena"*) e o modelo vigente no `CLAUDE.md` § Operational Visibility
> e § Admissão de sessão. Duas casas para a mesma informação é o defeito que este projeto evita em
> toda parte — e a segunda casa já estava mentindo em dois pontos (registrados no CHANGELOG da
> poda, não aqui: contradição resolvida é histórico).
>
> · Desenho de relatório: [`docs/product/shared-capacity-pool-as-tag-design.md`](docs/product/shared-capacity-pool-as-tag-design.md)
> · ADR de licenciamento: [`docs/adr/adr-agent-licensing-and-pool-isolation.md`](docs/adr/adr-agent-licensing-and-pool-isolation.md)

| | Problema | Estado |
|---|---|---|
| **A** | relatório mente: `available` por pool ignora consumo dos irmãos; KPI soma recurso compartilhado | ✅ F1–F5b + P1–P3 (2026-08-02/03) |
| **B** | teto de licença mistura moedas (`C = ai + human`) e gateia sessão humana | ✅ fatia 3 (2026-08-02) — sobrou `kind:ai ≤ C_ai`, gate único |
| **C** | piso/teto por pool, licenças materializadas, cerimônia de deploy | **ADIADO por medição** — é capacidade NOVA, não conserto |

### O que segue aberto

**1. Fatia 4 / defeito C — adiada, e a medição que a adiou precisa ser REFEITA antes de reabrir.**
`Q1` (IA roda > 1 sessão por instância?) e `Q2` (alguém usa `session_reservation`?) saíram do banco
ERRADO: `plughub_demo.public.pools` era fóssil congelado, o agent-registry vive em
`plughub_registry`. A perna de CÓDIGO de Q1 sustenta a conclusão (`instance_bootstrap.py:1054-1072`
usa *"Concurrent sessions: N"* como número de INSTÂNCIAS, cada uma `max_concurrent=1` ⇒ para IA,
instância == sessão); a perna de DADO não. **Q2 não é re-mensurável** — a coluna foi dropada em
02/08, e as evidências que sobram (`infra/registry/*.yaml` não declara reserva em pool nenhum; o
fóssil também marcava zero) apontam para a mesma conclusão sem serem o banco vivo no momento da
decisão. *O método estava errado mesmo com o resultado provavelmente certo.* O script já aponta
para o banco certo e aborta se não for (portão Q-1, `_prisma_migrations` como discriminador):

```bash
bash infra/test/measure_capacity_licensing_baseline.sh tenant_demo
```

**2. `max_concurrent_sessions` ainda soma as moedas** — hoje só como teto de PROVISIONAMENTO
(`lib/capacity.ts`, `deployViolation`: Σ declarada nos slots ≤ C). A fatia 3 deixou o defeito C mais
VISÍVEL, não menor; trocar esse gate agora seria construir a fatia 4 no meio da 3. Anotado no
docstring do próprio arquivo, para quem chegar nele primeiro não repetir a conta.

**3. Itens independentes achados na medição de 2026-07-31 — DATADOS, não verificados desde.**

| Achado | O que se mediu | Ressalva antes de agir |
|---|---|---|
| vazamento de admissão | 3 sessões presas em `…:admission:shared` (todas `kind:ai`, pool `survey_journey_wf`) com zero instâncias ocupadas; o reconciler não as liberou | o SET `shared` **não existe mais** (fatia 3). O mecanismo sobrevive em `kind:ai`: a liberação depende do marcador `closed` + reconciler. **Re-medir antes de tratar como defeito** |
| pools fantasma | `formfill_demo`, `ramal_test`, `survey_journey_wf` — resíduo de smoke com estado vivo | limpeza, não defeito |
| tenant fantasma *(colhido na poda de 03/08)* | `smoke_gprobe_pricing`, capacidade 1, aparece no `sync_all` do boot do pricing | mesma família da linha acima; herdado da seção "Pricing → quota Redis", podada |
| **`webhook_skill_id` é um pool** | com 3 instâncias: **o nome de um campo virou id de pool** | o mais concreto dos três; bug de seed/provisionamento |
| `retencao_humano-int` fora de `public.pools` | espelho vive só em runtime ⇒ **invisível a validação em tempo de config** | é por desenho (ADR §9.1: pool interno resolve licenciamento no pai). Registro, não defeito |
| `fila_humano` com `agent_kind = ai` | pelo nome deveria ser humano; muda licenciamento e hook | dado de tenant, não código |

**4. Costura única `acquire`/`release`** — arco separado; ver a seção própria mais abaixo.

### O que a poda NÃO pode levar junto

- **Não somar linhas de pool.** `Σ available(pool)` conta o mesmo recurso uma vez por pool e **não é
  corrigível na linha do pool**: a linha está certa (aquele pool alcança mesmo N vagas), somá-la é
  que não pode, e a informação de sobreposição não está lá. Vale igual na série
  `pool_occupancy_peaks` — `__total__` e `__capacity_{kind}__` são deduplicados (F4c), a linha do
  pool **não** é, por construção. E `by_channel` é PROJEÇÃO, não partição: instância que serve 2
  canais conta nos dois, então Σ entre canais excede o total do tipo.
- **Duas descontinuidades a marcar no eixo**, se a série virar base de dimensionamento:
  (1) `peak_concurrency` trocou de fonte (`active_count` → `used_here`, 2026-08-02) — o contador
  antigo derivava para CIMA, então o histórico tende a estar **superestimado**; degrau não medido.
  (2) `admission.shared_series` morreu e `admission.ai_series` começa em 2026-08-02 — não é
  renomeação: o denominador mudou de `370 − Σ reservas` para `C_ai = 360` e o numerador deixou de
  contar sessão humana.
- **`peak_concurrency` nunca responde "ocupação média"** — o registro por minuto já é máximo, e média
  de máximos não é média de ocupação. Média exigiria soma+contagem de amostras por minuto (campo
  novo, não pedido).

### Alternativas descartadas — não reabrir sem argumento novo

Reservar vagas de sessão por pool (fragmenta o recurso — contraria o invariante *"capacidade é do
RECURSO"*); só piso sem teto (sem teto não há limite a impor); empréstimo do piso ocioso (garantia
que exige espera não é garantia); baixar o TTL do snapshot (cura por expiração); métrica única de
"degradação" (valor plausível que esconde privação, espera e atribuição); adotar `current_sessions`
em vez do SET de ocupantes (é da mesma família do contador por pool — trocar um contador por outro
não fecha a classe, só muda qual deles vai mentir depois).

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

## 📂 TEMA · Borda, Webhook e Workflow

## Webchat — a promessa de reconexão pode não existir *(migrado da passagem de 2026-08-26; contradição doc × código)*

O `CLAUDE.md` § WebChat promete *"Reconnect via cursor: zero messages lost"*. **MEDIDO no cenário e2e
12:** após queda do WS e reconexão 200 ms depois, `session:{sid}:stream` está **VAZIO**, o cliente
recebe `conn.session_ended{reason:"session_expired"}` e `cursor_no_reauth == cursor_enviado`.

**NÃO medido, e é o primeiro passo:** quem apaga o stream, e quem emite `session_expired` (grep pelo
literal → produtor). ⚠️ `client_disconnect` (`webchat.py:320-329`) **explica o sintoma e NÃO foi o
caminho** — a instrumentação mostrou `reason: "session_expired"`, outra causa. É "valor plausível"
aplicado a uma hipótese: a leitura estava certa sobre o que aquele caminho FAZ e errada sobre ser o
percorrido.

**É decisão de PRODUTO, com custo próprio:** janela de graça de quanto? o que acontece com o AHT e
com o `close_reason` da analítica? As duas saídas honestas são implementar a graça **ou apagar a
promessa do doc**. O cenário 12 fica vermelho até a decisão — um verde aqui exigiria enfraquecer
justamente a asserção que julga a garantia.

## `source` do resume é asserido pelo CLIENTE na porta pública *(achado 2026-08-04, ao implementar a Fase F)*

`_terminal_cause` (channel-gateway `adapters/webhook.py`) decide entre `task_done`, `acw_expired` e
`acw_supervisor_closed` lendo `payload["source"]`. Os gatilhos internos escrevem esse campo
server-side (o tool marca `agent`, o scanner `timeout_scanner`, o endpoint do supervisor
`supervisor:{sub}`) — mas o `POST /v1/channels/webhook/resume/{token}` **repassa o `payload` do corpo
verbatim**. Um chamador externo pode declarar `source: "supervisor:x"` e obter o carimbo
`acw_supervisor_closed` no segmento.

**A exposição é anterior à Fase F** — a expressão inline lia o mesmo campo — mas a F a tornou
**durável**: a causa agora também vai para `{t}:resume_terminal:{token}`, que vive 25 h e é o que
nomeia a recusa do próximo. Uma causa forjada deixou de ser efêmera.

**Por que NÃO foi fechado junto:** o conserto óbvio (rebaixar `source` quando não há principal
verificado) depende de `_resolve_approver_principal`, que ainda não foi lido. No caminho genérico de
form-fill o `resume_required_abac` devolve `None` — se disso resultar `approver is None` também para
um supervisor legítimo, o downgrade cego **derrubaria o expire do supervisor**. E esse caminho
`acw_supervisor_closed` tem **0 ocorrências** no demo (medido): ele não reclamaria. Fechar no escuro
seria trocar um defeito silencioso por outro.

**Primeiro passo:** ler `_resolve_approver_principal` e responder *"um Bearer de supervisor sem ABAC
exigida produz principal?"*. Só então decidir entre rebaixar no endpoint ou exigir o header. Validar
com `INSTANCE=human-<user_id> bash infra/test/smoke_acw_expire.sh`, que é o único jeito de exercitar
o ramo reivindicado do supervisor.

## Autenticação de endpoint webhook ✅ ARCO FECHADO *(1 ✅ · 2 ✅ 2026-08-07 · 3 ❌ cancelada · 4 ✅ · borda ✅ 2026-08-10 — ADR §7.9 e §7.10; aberto só o 2b, que espera número de volume)*

✅ **Fatia 1 — mecanismo + medida** (detalhe no `CHANGELOG.md`). `auth_required` opcional por endpoint,
default **false**; token portado do `workflow-api/webhooks.py`; hash só para chamador de serviço;
verificação nas **duas** portas a partir de UMA função; **fail-closed** quando não dá para verificar;
revogar desliga a exigência junto (não deixa estado impossível de satisfazer). Coluna `Auth` na tela e
seção **F6** no probe contam os anônimos — o antídoto do opt-in é a ausência medida, não o default
agressivo. Gate `infra/test/gate_webhook_endpoint_auth.sh` com **controle de não-regressão** (endpoint
anônimo tem de seguir aceitando sem header — é o que prova que o default OFF não virou ON).

**Pendências, em ordem de valor:**

1. ✅ **Invalidação de cache por `registry.changed` — FEITA** (`registry_invalidation_consumer.py`).
   Rotação/revogação passa a valer em segundos, não em 30 s. **`group_id` único por processo**: o cache é
   in-process, logo invalidação é *broadcast*, não fila — com group compartilhado só uma réplica receberia
   o evento e as outras seguiriam com o hash revogado. (O routing-engine usa group compartilhado no MESMO
   tópico e está certo, porque o cache dele vive no Redis.) Gate cobre por **rotação**, não revogação —
   revogar desliga `auth_required` e o endereço fica anônimo, o que não distingue nada.
2. ✅ **UI de token — FEITA** (fatia 2, detalhe no `CHANGELOG.md`). Gerar/rotacionar/revogar em
   `/config/channels` › Webhook; banner que **não some sozinho** (o segredo aparece uma vez e não é
   recuperável); confirmação **assimétrica** (gerar não pergunta, rotacionar sim — confirmar sempre
   treina a clicar sem ler); a confirmação de revogar diz que a autenticação é **desligada junto**.
   Botões só em linhas `external`, porque ligar auth em `internal` silencia o disparo interno até a
   fatia 3.
2b. **Janela de aceitação de credencial revogada — medida, não zero.** Com o consumidor de invalidação
   já no grupo: **0 s**. Rodando durante o join (~3 s após o boot do gateway): **3 s** — o
   `invalidate_all()` pós-join fecha aí. Se o evento se perder por qualquer motivo, volta ao TTL (**30 s**).
   Para material de credencial, staleness tem custo de segurança que `pool_id` não tem. Opções, com o
   trade-off explícito: (a) TTL curto só quando `auth_required` (ex. 5 s) — mais consultas só nos
   protegidos; (b) não cachear o `token_hash`, resolvendo o resto do cache — uma ida ao registry por
   disparo autenticado, aceitável em webhook de baixo volume, cara em alto; (c) manter como está e confiar
   na invalidação, que é o desenho atual. **Decidir com número de volume na mão**, não por preferência.
3. ❌ **CANCELADA — fatia 3 (plumbing de credencial nos chamadores internos)** *(2026-08-10, ADR §7.9)*.
   Não foi adiada: o **inventário estático dos discadores** (método das Fases A/E) mostrou que a tarefa
   **não deve ser feita**. Dos dez `origin=internal`, **nove não têm chamador algum** na porta por
   identificador — os pools deles são disparados por `/v1/channels/webhook/pool/{id}`, que não passa pelo
   registro e **não tem onde pendurar token** (§7.6.1). Só `skill_portabilidade_demo_v1` é discado por
   identificador (intake + `smoke_journey_root`); o `_fire_detached_hook`, citado no enunciado da fatia,
   é porta por pool e **está fora do escopo**. Daí o argumento, estrutural e não de risco: `/v1/*` exposto
   na borda ⇒ a porta por pool está junto ⇒ auth por identificador é **teatro**; `/v1/*` não exposto ⇒ os
   internos são inalcançáveis ⇒ auth é **redundante**. Nos dois ramos compra zero, e no primeiro ainda
   custa (silencia disparo interno). *"Dez anônimos" era dez ENDEREÇOS e um DISCADOR.* Entregue junto:
   **F6 reclassificado** (internal = "por DECISÃO", com o probe declarando que **não verificou** a borda —
   ele lê o store, exposição é infra) e **guard `INTERNAL_AUTH_REFUSAL`** (422 nomeado no create *e* no
   `POST /{id}/token`, que era o furo real: read-only da tela ≠ read-only da API, §7.6.4). ⚠️ **A decisão
   depende da porta por pool seguir anônima por construção** — registrá-la ou fechá-la reabre a pergunta.
4. ✅ **Fatia 4 — FEITA, mas NÃO como "default ON"** *(2026-08-10, ADR §7.10)*. O inventário dos
   **criadores** refutou a premissa de que existe um só: o operador pela UI **recebe** o token (o 201 é a
   única janela em que ele existe em claro), e o `RegistrySyncer` faz o mesmo POST a partir do YAML e
   **descarta o corpo**. Default ON faria instalação limpa nascer com `crm-callback` exigindo um token que
   ninguém viu — 401 permanente, dormindo até o `--wipe` porque seed-if-absent dá 409 nas linhas que já
   existem. **Decisão: sem default.** `auth_required` ausente em `channel=webhook` + `origin=external` ⇒
   **422 nomeado** — "este chamador consegue guardar um segredo?" só é sabido NO chamador, então o route
   para de adivinhar. A UI declara `true` (caixa **marcada por padrão**, que é onde a intenção do "default
   ON" legitimamente vive) e mostra o banner do token no create; o YAML declara `false`, e **só `false` é
   válido ali** (o syncer rebaixa `true` com log ERROR em vez de criar endpoint inalcançável). Junto:
   **`auth_required` recusado em canal não-webhook** (a flag só é lida nas rotas de webhook; nos demais a
   linha afirmaria proteção que ninguém aplica). Cobertura: **P11/P12** no gate, com os ids dos creates
   que devem falhar no `trap`.
5. ✅ **Requisito de borda ESCRITO** *(2026-08-10)* — era o último item do arco. Vive em dois lugares, de
   propósito: **invariante** em `CLAUDE.md` § What Never To Do (é o arquivo lido toda sessão; prosa em
   guia não é requisito) e **detalhe** em `docs/guias/webhook-patterns.md` § Exposição na borda (tabela
   dos dois prefixos, por que a porta por pool é inprotegível, o que conferir num ambiente). O argumento
   registrado é o da porta (2): `/v1/channels/webhook/pool/{id}` é anônima por construção, logo publicar
   o prefixo torna disparável todo pool webhook do tenant e **nenhum `auth_required` muda isso**.
   ⚠️ Continua **sem cobertura de teste** — ver item 6.
6. **Probe EXTERNO de borda** *(aberto 2026-08-10)* — o requisito do item 5 está escrito e não é
   verificável de dentro: todo instrumento que temos roda na rede interna, onde `/v1/*` **deve** mesmo
   responder. Um probe que rodasse ali confirmaria o oposto do que se quer provar. O teste válido é um
   `curl` **de fora** contra o host publicado, esperando: `/channel/webhook/{identifier}` responde e
   `/v1/channels/webhook/pool/{qualquer}` **não**. É trabalho de infra (precisa de um ponto de origem
   externo), não de código — e enquanto não existir, o F6 do probe de inventário declara que não mediu,
   que é o comportamento correto. **Não fabricar um substituto interno:** um teste que só pode passar
   não distingue nada, e aqui ele ainda daria a impressão de que a borda foi conferida.

**Arco de autenticação de endpoint webhook: FECHADO** (fatias 1 ✅, 2 ✅, 3 ❌ cancelada com motivo, 4 ✅,
requisito de borda ✅). Aberto só o item **2b** (acima), que espera número de volume para decidir o regime
de cache do material de credencial.

⚠️ **Os endereços por pool (`/v1/channels/webhook/pool/{id}`) seguem anônimos por construção** — não passam
pelo registro (ADR §7.6.1), logo não têm onde pendurar credencial. Se isso for inaceitável num ambiente, a
saída não é registrá-los (ver o argumento da função identidade), é restringir o prefixo `/v1/*` na borda.

---

## Porta externa de resume × posse do item de pull — decisão pendente *(achado 2026-08-10, ao escrever o gate da Fase 1)*

A conferência de posse do A5 é **gateada em `approver is not None`** (`webhook.py:1148`
— `if approver is not None and claim_instance_id:`). A porta externa
(`POST /channel/webhook/resume/{token}`, Fase 1) passa `approver=None` por construção — quem tem JWT usa
a porta interna. **Logo ela não confere posse.**

Para um token de `suspend` puro isso é correto e é o ponto da porta: o chamador está retomando a
**própria** execução suspensa. Para um token de `delegate` a uma fila de PULL, o mesmo token é a
conclusão de um **item de trabalho humano** — e aí o A5 existe justamente para impedir que uma submissão
descarte trabalho que voltou à fila ou que outro agente detém.

**Três estados, e só o primeiro é claramente aceitável:**

| Estado do item no árbitro | Hoje pela porta externa | Deveria? |
|---|---|---|
| não existe item (suspend puro) | resume passa | ✅ sim — é o caso de uso da porta |
| `found=False, in_queue=True` (na fila, sem dono) | resume passa | ⚠️ **indeciso** — submete sobre trabalho disponível a outro agente |
| `held_by=X` (humano detém) | resume passa | ❌ **não** — descarta trabalho em curso |

O terceiro caso é errado sob qualquer política e não precisa de principal para ser recusado: **é
propriedade do ITEM, não do chamador** — o mesmo ramo (2) do A5, aplicado sem JWT. O obstáculo é
mecânico, não conceitual: `_routing_work_task_holder` exige `pool_id`, que a porta externa não recebe;
derivá-lo do ledger `work_task` da sessão é o caminho.

**Não implementado de propósito nesta fase.** O gate `gate_external_resume.sh` nunca reivindica o item,
então mede sempre o segundo estado — um portão que só sabe medir o caso indeciso não pode julgar o caso
errado. Fechar isto pede um passo que **reivindique** primeiro (Console/`work_queue`), e aí a recusa vira
verificável. Ordem sugerida: junto da Fase 2, que já vai mexer no que o resume lê antes do consumo.

⚠️ **Não confundir com a Camada F.** O lock dá unicidade (só um resume vence); ele não diz *qual* dos
dois deveria vencer. Posse é a pergunta que sobra depois da unicidade.

---

## Remoção física do legado de workflow por token *(aberto pela Fase F, 2026-08-07)*

Executar a decisão do ADR §7.8.4. **Remover:** `workflow.webhooks` + CRUD + `POST /v1/workflow/webhook/{id}`;
`workflow.instances` e o resto do lifecycle 410; `WebhooksTab` + a rota órfã `/workflow/calendar`.

⚠️ **NÃO remover junto sem análise própria:** o tópico `workflow.events` e o `skill-flow-worker`. O
cenário e2e **18** depende do worker, e a **evaluation-api consome** `workflow.events` para
`suspended`/`completed` (motor de review legado, reactive-only). Matar o tópico junto seria repetir o erro
que a Fase F acabou de desfazer — tratar como pacote coisas que só estão adjacentes.

**Gate:** o `probe_webhook_endpoint_inventory.sh` já reprova se `F3 > 0`; depois da remoção, F3 passa a ser
estruturalmente 0 e a checagem vira testemunha (mesma reclassificação que os contadores de fallback
sofreram na Fase E).

📄 **Doc a corrigir junto:** `docs/guias/webhook-patterns.md` § "Padrão 1" descreve exatamente este
caminho legado (`POST /v1/workflow/webhook/{id}` + registro por `X-Admin-Token`) como se fosse o trigger
canônico. Recebeu um aviso de obsolescência no topo em 2026-08-10, mas o corpo continua ensinando o
caminho errado a integradores — reescrever para o `ChannelEndpoint` faz parte da remoção, não é
follow-up. (O guia também ainda cita `notify` como step depreciado no Arc 16 e o `skill-flow-worker`;
conferir o resto ao mexer.)

---

## Deploy de skills — cleanup de campos órfãos *(follow-up do redesenho D1–D4, 2026-07-13)*

Depois do modelo novo de deploy ("uma definição editável + cópia imutável no slot"), ficaram órfãos:
dropar `flow_draft` e `deploy_status` do schema Prisma (agent-registry) e remover o endpoint
`POST /v1/skills/:id/deploy`. Deixados para depois de o modelo novo rodar; histórico completo do
redesenho no `CHANGELOG.md`.

---

## 📂 TEMA · Telefonia e Voz

## Telefonia — DOIS arcos, não um *(desenho fechado 2026-08-19; nada implementado)*

Nasceu do pedido de integrar a plataforma a uma **Avaya IP Office Server Edition** por CTI. O
levantamento derrubou a premissa (o canal de voz não roda) e a discussão separou a coisa em dois arcos
que **não são fases um do outro** — são ofertas paralelas, para clientes diferentes.

**A fronteira é atendimento × telefonia interna, não controle × mídia.** A plataforma não pretende ser
um PABX. Isso é o que faz os dois modos caírem.

### Arco 1 — CTI gateway multi-driver *(modo CTI)*

[`adr-cti-gateway-multi-driver.md`](docs/adr/adr-cti-gateway-multi-driver.md) — proposto.

PABX ancora a chamada, a plataforma governa por CTI, **a mídia nunca sai da LAN do cliente** e **não
existe IA na voz**. Serviço on-prem `packages/cti-gateway/` com N drivers sobre **modelo canônico =
perfil reduzido de CSTA**. Fases **F0** (núcleo + driver IP Office; entrega os seis requisitos) →
**F1** (segundo driver, *estruturalmente diferente*, obrigatória antes de qualquer outro) → **F2**
(demanda).

Decisões que carregam peso: capability **declarada por driver com recusa alta** (nunca emulação muda);
identidade da chamada resolvida por **componente conexa sob aliases**, reusando o padrão
`root_session_id`+union-find em vez de inventar o terceiro mecanismo; **monitor ⟺ ramal alocado**, que é
o que segura o teto de sinalização do IPO; e **nenhum driver na matriz de suporte sem traço gravado** —
o que promove o Record/Replay Harness de backlog a infraestrutura deste arco.

**Bloqueio de método:** cinco medições contra a central de homologação **antes** de F0 (§8 do ADR) —
exclusividade do `EnhTcpaService`, perfil CSTA (II × III), `DeflectCall` sobre chamada em fila, hot-desk
comandável por CSTA ou só por short code, e teto de mensagens/monitores. Cada uma muda desenho, não
implementação.

### Arco 2 — Voz própria / plano de mídia *(modo SIP)*

[`adr-voice-media-plane.md`](docs/adr/adr-voice-media-plane.md) — proposto.

**Não é integração com Avaya.** No modo SIP a parte Avaya é *um tronco e um `REFER`*; todo o resto —
terminação SIP, SFU, STT/TTS, perna do agente, gravação — vale igual contra qualquer central ou contra
nenhuma. Classificar isso como "integração IPO" escondia o custo (parece driver, é pilha de telecom) e o
valor (parece de um cliente, é capacidade de produto).

**Consolida três dívidas que sozinhas não se justificam** e que hoje ninguém consegue priorizar: o canal
`voice` que não roda (seção própria neste arquivo), o Arc 15 que é placebo, e o discador que está
bloqueado por falta de mídia.

**V1 resolveu a decisão que parecia grande:** o plano de mídia **não tem topologia própria — acompanha o
deploy da plataforma**. Elimina SFU de terceiro; no on-prem não há WAN nem SBC no caminho da voz; na
nuvem, SBC é do produto.

**V6 é a decisão de método, e é a mais importante:** `_dev_mode` **sai**; sem credencial o provider
**recusa alto**. Token bem-formado e falso é o valor plausível mais caro que este repositório já
produziu — foi ele que deixou o Arc 15 passar por pronto por meses, sobrevivendo inclusive a revisões de
arquitetura.

Fases **V-F0** (infra de pé — fase própria e primeira) → **V-F1** (perna SIP entrante) → **V-F2** (bot
leg STT/TTS; conserta o `collect` morto) → **V-F3** (gravação) → **V-F4** (egress + supervisão) →
**V-F5** (validação com instalação limpa).

### Derivado — retenção de artefato é config, e hoje viola três invariantes

**Achado 2026-08-19.** A retenção de artefato existe como `attachment_expiry_days: int = 30` em
`channel-gateway/config.py:119`: **env** (a regra é *env só para segredo e topologia*), **um número
único para todas as classes** (a regra é *one source per domain*), e **sem superfície de UI** (a regra é
*every config field is UI-editable*).

Consequência medida: a gravação de voz já grava no AttachmentStore (`voice.py:410-416`), então **os "5
anos" de `channel-gateway-multi-channel.md:1371-1550` não têm implementação** — o ciclo apagaria tudo aos
30 dias. E `docs/layers/07-data-layer.md:101` dizia 30 dias por LGPD. Dois documentos, dois números,
nenhum descrevendo o código.

**Decidido (V5):** namespace `storage` na config-api, **uma entrada por classe de artefato**
(`call_recording`, `webchat_attachment`, `whatsapp_media`, `survey_audio`, …), com UI. O que resta é o
**default de cada classe**, que é decisão de negócio/jurídico. Bloqueia V-F3.

### Dívida de método que este levantamento expôs

Os três documentos de estado (`visao-geral.md`, `layers/01-channel-layer.md`,
`product/value-proposition.md`) afirmavam voz **entregue** desde a auditoria de 2026-05, e o
`pacotes/channel-gateway.md` carimbava `✅ Implementado`. Todos corrigidos em 2026-08-19 com a medição.

O padrão vale além da voz: **auditoria de documentação que classifica por leitura de doc, e não por
execução, propaga a afirmação em vez de verificá-la.** A `revisao-documentacao-2026-05.md:266,300` é
exatamente onde `voice` saiu de "lacuna aspiracional" para "implementado" — e nada rodou no meio.
Próxima auditoria: o critério de "implementado" é *existe caminho executado*, não *existe seção no doc*.

---

## `voice.py` chama ~~dois~~ **CINCO** métodos que não existem, e o teste os fabrica *(achado 2026-08-12; recontado e DECIDIDO 2026-08-19)*

> **Atualização 2026-08-19.** Duas correções e uma decisão. **(1) São cinco, não dois** — além de
> `_open_session` e `_route_inbound`, faltam `_publish_inbound` (`voice.py:433,565`), `_normalize_text`
> (`:558`) e `_normalize_menu_result` (`:724`), todos igualmente mockados em
> `tests/test_voice_adapter.py:116-121`. Subcontar aqui não é detalhe: "dois métodos" soa como remendo,
> "o caminho inbound inteiro" é o que de fato é. **(2) Não está sozinho** — o mesmo adapter tem
> `channel_name` em vez de `channel` (`:90`, viola a ABC); `_collect_loop` prometido no docstring e
> inexistente, com `stt_queue` nunca drenada e `_handle_stt_result` sem chamador (`:624-629,657`) ⇒
> **collect por voz morto, só DTMF**; `hangup` lendo chave nunca escrita (`:884` vs `:229-233`);
> `_get_contact_id` retornando `None` por construção (`:1032-1037`); e `deliver_outbound` (81 linhas)
> que **nunca é invocado** (`:772` vs `outbound_consumer.py:95-106`). **(3) A pergunta "implementar os
> dois ou reescrever" está RESPONDIDA: reescrever.** Ver [`docs/adr/adr-voice-media-plane.md`](docs/adr/adr-voice-media-plane.md)
> — o canal é reconstruído sobre plano de mídia próprio, e o `VoiceAdapter` atual não é o ponto de
> partida (o molde é o `WebRTCAdapter`). O que se aproveita é `voice_provider.py`
> (`FallbackSTTProvider`/`FallbackTTSProvider`, Deepgram, ElevenLabs) e o **desenho** de
> `docs/arcos/channel-gateway-multi-channel.md` §9/§13, que segue válido.

`adapters/voice.py:236` e `:247` chamam `self._open_session(...)` e `self._route_inbound(...)`. **Não há
definição de nenhum dos dois em lugar nenhum de `packages/channel-gateway`** — nem em `ChannelAdapter`
(`adapters/base.py:28-73`), nem em `VoiceAdapter`. As únicas outras ocorrências estão em
`tests/test_voice_adapter.py:116,118`, que os atribui como `AsyncMock` e depois afirma
`assert_awaited_once()` (`:350-351`).

Ou seja: o teste **cria** o método que a produção não tem e então verifica que ele foi chamado. É o caso
canônico de *"um teste que não pode reprovar é pior que teste nenhum"* — só que agravado, porque não é um
verde por ausência de amostra, é um verde por o próprio teste ter suprido o que faltava.

Consequência esperada: o caminho inbound de voz levanta `AttributeError` ao ser alcançado. Não medido em
runtime — **não há uma única sessão de voz no ambiente**, o que é consistente com a hipótese e é, por si só,
o sintoma que ninguém leu. Antes de consertar, decidir se o alvo é implementar os dois métodos (a
documentação os descreve em `docs/arcos/channel-gateway-multi-channel.md:163,183`) ou reescrever o caminho
sobre os helpers que os outros adaptadores usam.

---

## Masking — Bloco 3: Channel Gateway TTS *(deferred até implementação de voz)*

Quando qualquer adapter de voz/TTS for criado, deve consultar `rule.{category}.display_voice` no namespace `masking` do Config API antes de passar texto ao sintetizador. Comportamentos: `silence` (pula o valor), `beep` (tom de beep), `speak_placeholder` (fala "valor mascarado"). Não implementar antes de definir qual engine TTS será usada.

---

## 📂 TEMA · Segurança, LGPD e Masking

## 🆕 ARCO PROPOSTO — ContextStore como ALLOWLIST: campo sem regra não é acessível *(proposto pelo dono 2026-08-26; **ADR ESCRITO 2026-08-26**)*

> ✅ **Fases entregues (ver `CHANGELOG.md`): V0, V1, V1b e V2.** A V1b fechou a **segunda
> porta** do §1.5 — o tool MCP `supervisor_state` devolvia o hash CRU. A política mudou-se para
> `packages/mcp-server-plughub/src/lib/context-masking.ts` (uma casa, importada pelas duas portas) e
> o tool entrega em **grau operator, sem portão de namespace**, com
> `customer_context.context_masking = { grade, total, hidden_count }`. Gate
> `infra/test/probe_supervisor_tool_masking.sh`. **Próxima fase é a V3** (mapa + aliases contados +
> modo auditoria).
>
> ### 🆕 O que a V2 deixou aberto (2026-08-26)
>
> · 🔴 **As duas portas Python de masking continuam CÓPIA** (`quality-ingest/masking.py`,
>   `channel-gateway/adapters/webhook.py`). Estão alinhadas e há gate
>   (`probe_masking_display_parity.sh`), mas o fim da duplicação é lerem `masking.types` do
>   config-api em runtime — e isso exige **recusar alto** quando a config não vier, porque degradar
>   em masking é vazar PII. É a única das quatro políticas de D4 em que o fallback silencioso não é
>   opção. Fase própria, não backlog difuso.
> · 🆕 **`CATEGORY_META` (`MaskedToken.tsx`) tem rótulos hardcoded em português** — `'Cartão'`,
>   `'Fone'`, `'Passaporte'`. Viola a invariante i18n do `CLAUDE.md`. A V2 tirou os fantasmas de lá
>   mas não os rótulos; o destino é o `label` do próprio tipo, traduzido.
> · ✅ ~~**Contador de aliases: `masking.rule.*` = 0 hoje.** O leitor mantém o fallback legado e o
>   CONTA (aviso no console). É o mesmo mecanismo do D3 — a remoção do fallback é **medida** por
>   este número continuar zerado, não agendada por opinião.~~ — **FECHADO na V2b (2026-08-29)**: o
>   número continuou zerado (medido no `platform_config` INTEIRO, não só em `tenant_demo`), os 4
>   leitores saíram e o gate `probe_legacy_display_rule_closed.sh` impede o retorno. Foi o contador
>   que autorizou, exatamente como esta linha previa.
> · 🆕 **O namespace `masking` ainda carrega 4 chaves marcadas `[DEPRECATED — use audit_policy.*]`**
>   (`authorized_roles`, `default_retention_days`, `capture_input_default`, `capture_output_default`),
>   duplicadas em `audit_policy`. São duas grafias da mesma config, e ninguém as está contando.
> · 🆕 **`address`/`health`/`financial` estão declarados e não têm produtor nenhum** — o caminho que
>   os alcança (`AuditPolicy.data_categories`, declarado por tool) **não é usado por nenhuma tool do
>   repositório**. Não é defeito: é o inventário que a V3 precisa ter à mão quando decidir se o
>   catálogo declara capacidade ou uso.
> · ⚠️ **`financial` é a MESMA palavra em dois enums** — `DataCategory` (classe de dado) e
>   `ContextMaskingType` (forma de máscara). Convivem por acaso; num mapa que cruze os dois, colidem.
>
> ### 🆕 F0 do `masked` TIPADO — censo feito em 2026-08-29
>
> Instrumento re-executável: `infra/test/q_masked_declaration_census.sh` (três eixos, cada um com
> testemunha de presença). **Não é gate — é número.** O ADR ainda não foi escrito; isto é o que ele
> tem de partir.
>
> · **Quatro declarações, um resolvedor.** O booleano é declarado em **2×2**: nível do nó
>   (`skill.ts:476` `MenuStep.masked` · `dialog.ts:314` `QuestionNode.masked`) × nível do campo
>   (`skill.ts:503` · `dialog.ts:258`). A precedência (*campo vence step*) é aplicada **uma vez**, em
>   `skill-flow-engine/src/masking-policy.ts` (`isFieldMasked` + `computeMaskedFieldIds`, com teste
>   próprio), e o `form_get` (`mcp-server/tools/dialog.ts:110-140`) **achata** as duas declarações do
>   DialogForm em `render.fields[].masked`, que o step `menu` consome. **Arquitetura favorável:** a
>   tipagem muda declaração + normalizador; os consumidores a jusante recebem `masked_fields[]` (lista
>   de ids) e só precisam mudar onde o TIPO tiver de chegar (máscara por papel, regra de canal).
> · 🔴 *(agora coberto por [`adr-masked-typed-declaration.md`](docs/adr/adr-masked-typed-declaration.md) §1.3)* **A submissão de form ENTRA na transcrição durável, e o campo não-declarado vai em CLARO.**
>   Medido ao vivo: 2 linhas `[Formulário: …]` em `plughub_demo.messages` (`author_role=customer`,
>   contra 1 564 de testemunha), com `senha` e `codigo_2fa` redigidos e **`email` em claro** — porque
>   o autor do form não marcou `masked`. **O catálogo TEM regra para ele** (`email_addr`, com
>   `detect_pattern` e `lgpd=pessoal`) e ela não é aplicada nesse caminho. Não é buraco no catálogo:
>   é §1.4 do ADR em ato — **detecção** e **declaração** moram em casas separadas, e o caminho de
>   escrita do bridge consulta só a declaração. É o argumento central do `masked` tipado, medido em
>   vez de afirmado.
> · 🟡 **`DialogFormRenderer.tsx` ignora `masked` — zero ocorrências.** A 4ª superfície do dialog
>   primitive (Console: aprovação, wrap-up, collect-form genérico) não honra a declaração, então o
>   valor iria para `payload.answers` → `workflow_resume` → `pipeline_state`, contra a invariante
>   *"nunca escrever valor mascarado em pipeline_state"*. **Exposição × dano medidos e diferentes:**
>   dos 10 DialogForms do tenant, **1** declara campo masked (`dialog_limite_solicitacao`), e ele não
>   é dos que chegam ao Console (aprovação/wrap-up/demo têm zero). **Armadilha ARMADA, dano hoje 0** —
>   e é o primeiro form masked roteado ao Console que a dispara.
> · ⚠️ **Ordem entre ADRs:** a S1 do [`adr-deploy-time-content-snapshot`](docs/adr/adr-deploy-time-content-snapshot.md)
>   e o `masked` tipado **mexem no MESMO bloco `render`** do `form_get` (a S1 leva `captures`; a
>   tipagem teria de levar o tipo em `render.fields[].masked`). Fazer as duas sem decidir a ordem é
>   colisão de contrato — não é bloqueio de nenhuma das duas, é decisão a tomar antes da segunda.
> · ✅ **Teto de `category_l1..l4` CONFERIDO — a prosa do `CLAUDE.md` estava certa**, e agora é
>   medição: regex `AGENT_EVENT_CATEGORY_REGEX = /^[a-z0-9_]+(\.[a-z0-9_]+){1,4}$/` aceita **2–5**
>   segmentos · `decomposeCategoryLevels` extrai **4** (`const [l1,l2,l3,l4] = split(".")` — o 5º cai
>   no destructuring, em silêncio) · DDL tem **4** colunas, e `category_l4` **nem está no `ORDER BY`**
>   · profundidade 5 da árvore exigiria **8** (3 do `pool.skill.metric` + 5 do caminho). A F4 do
>   [`adr-dialog-tree-options`](docs/adr/adr-dialog-tree-options.md) segue bloqueada como escrito.
>   **`agent_business_events` está VAZIA (0 linhas)** ⇒ bloqueio **latente**: sem exposição e, o que
>   importa para a fase, **sem backfill**. Nota adjacente: tabela vazia também significa que nenhum
>   skill deste ambiente emite `agent_event` — o Arc 12 não tem produtor vivo aqui.
>
> ### 🟡 `agent-registry`: 2 testes de `pools.test.ts` vermelhos, PRÉ-EXISTENTES (medido 2026-08-29)
>
> `POST /v1/pools > cria pool válido` e `GET /v1/pools > retorna lista` falham com **500 onde se
> espera 201/200**. **Não são regressão** — conferido por `git stash` das alterações da T5: as mesmas
> duas falham sem elas. Os outros 6 do arquivo passam, então não é o arquivo inteiro fora do ar; é um
> caminho específico. Parente do achado do `mcp-server` que não compilava: verde por ninguém rodar.
>
> ### 🔴 O `mcp-server-plughub` NÃO COMPILAVA — descoberto em 2026-08-29, ao construir a T1
>
> **Nada disto tem a ver com masking.** O commit `10bde79` (arco de relatórios, atribuição de custo
> por segmento) acrescentou `segment_id` a `UsageEventSchema` com `.default(null)` — e `.default()`
> torna o campo **obrigatório no tipo de SAÍDA** do Zod. `EmitParams` é
> `Omit<UsageEvent, "event_id"|"timestamp">`, então os dois emissores de
> `mcp-server-plughub/src/lib/usage-emitter.ts` (`:73` sessões, `:102` mensagens) passaram a não
> compilar. Eles não são tocados desde `da57af3`, muito anterior.
>
> **O pacote inteiro estava sem compilar desde `10bde79`, e nada ficou vermelho** porque ninguém
> reconstruiu a imagem — é o *"ambiente que só sobe porque já subiu antes"* do `CLAUDE.md`, com o
> agravante de que o serviço em execução é o **mcp-server**, onde moram as tools. Corrigido no mesmo
> passo (`segment_id: null` nos dois, que é o que o próprio schema manda: *"null = chamador que ainda
> não a informa. Nunca inventar"* — os dois são fatos de SESSÃO e não têm segmento em escopo).
>
> ⚠️ **O que fica em aberto é maior que o conserto:** ninguém sabe **quantos outros pacotes não
> compilam**, porque a única evidência é reconstruir. `infra/scripts/rebuild-all.sh --wipe` existe e é
> exatamente este teste; a dívida é rodá-lo **de propósito, em dia calmo**, e contar. Enquanto isso
> não acontecer, "a stack está de pé" não é evidência de que a árvore compila.
>
> · 🟡 **Lição já aplicada ao gate:** `probe_type_catalog.sh` ganhou o ramo **P0b (imagem × FONTE)**.
>   O P0 conferia que o símbolo EXISTE, não que é o de agora; e o ramo C compara imagem × config
>   viva, que são **dois artefatos implantados** — estando os dois atrás do fonte, eles CONCORDAM e a
>   concordância é lida como aprovação. Foi o que aconteceu: o build reprovou, o `up -d` subiu a
>   imagem anterior e o gate saiu **VERDE**. A cura não é comparar melhor os dois implantados: é
>   trazer o fonte para a mesa, que é o único lado que não pode estar atrás de si mesmo. **Todo gate
>   que julga conteúdo de imagem precisa deste ramo** — os demais ainda não têm.
>
> ### 🆕 DETECÇÃO de PII — dois achados medidos em 2026-08-29 (arco PRÓPRIO, não é o `masked` tipado)
>
> **Decisão de escopo tomada aqui:** declaração (`masked:`) e detecção (regex) **não são duas
> implementações da mesma coisa** e o ADR do `masked` tipado **não** absorve a segunda. Declaração
> responde *"este campo VAI receber segredo"* — sabe-se **antes** do valor existir, alcança o valor
> inteiro e pode **suprimir na origem**. Detecção responde *"este texto CONTÉM algo parecido com
> PII"* — só se sabe **depois**, alcança um trecho, e só pode tokenizar post-hoc. O que as duas
> compartilham é o **TIPO** (`formato.detect_pattern` × `mascara.by_role` × `lgpd`), e a detecção já
> o lê desde a V2 (`DEFAULT_MASKING_RULES` é derivada do catálogo). Fundi-las devolveria duas
> respostas para uma pergunta — o defeito que a V2b fechou.
>
> O mecanismo de detecção **não** é o problema: é único (`MaskingService.applyMasking`,
> `mcp-server-plughub/src/lib/masking.ts`) e funciona (há `meu cpf e [cpf:tk_ab12:***-00]` na base).
> O problema é **COLOCAÇÃO** — um único call site (`tools/session.ts:472`, gated em
> `role === customer|primary`), e o caminho que vazou não passa por ele: a submissão de form é
> escrita pelo **bridge**, em Python. Medido: dos **439** recados de cliente em
> `plughub_demo.messages`, **0 CPFs crus** e **2 emails crus** — e os 2 são exatamente as 2
> submissões de form. Três colocações possíveis, cada uma com custo real: (a) choke point de escrita
> do stream — certo em tese, mas o bridge não usa `writeStreamEntry()`; (b) no redator do bridge —
> resolve o caso medido e cria a **segunda implementação** que a V2b gastou esforço para eliminar;
> (c) net-pass na borda de ingestão — uma casa, agnóstica de linguagem, mas *depois* do fato e sem o
> vault à mão. **Escolher é o F1 deste arco; medir o volume por caminho é o F0.**
>
> · 🔴 **`session.ts:485` — detecção que falha entrega o conteúdo CRU, em silêncio.**
>   `} catch { /* mascaramento não-fatal — entrega conteúdo original */ }`. É `except: pass` no
>   único lugar do produto onde degradar significa **vazar PII**, e o próprio ADR já registra masking
>   como a política em que fallback mudo não é opção (D4). Não medi se dispara hoje — **o ponto é que
>   ninguém saberia**, que é a definição da patologia. Conserto mínimo: logar NOMEANDO o que deixou
>   de valer (categoria/sessão), no molde do aviso de degradação da V2b. Conserto certo: decidir se
>   recusa alto. *Nota: há 7 outros `catch` mudos no mesmo arquivo (`:249 :306 :317 :326 :430 :585
>   :607`); os demais degradam para ausência de enfeite, não para vazamento — só este é desta
>   classe, e é por isso que ele sai da lista.*
> · ✅ ~~**A transcrição durável não tem PROVENIÊNCIA de mascaramento**~~ — **FECHADO para o caminho
>   da DECLARAÇÃO na T3 (2026-08-29)**: `messages.masked_types` (`Map(String,String)`, campo → id do
>   tipo), provado por contato real. **E são CINCO camadas, não quatro** — a contagem abaixo estava
>   incompleta e o erro custou uma depuração: com schema, produtor, parser e DDL corretos, a coluna
>   gravava `{}` em toda linha, porque o **ESCRITOR** (`clickhouse.py` `_MESSAGE_COLS` +
>   `_message_row`) monta o INSERT a partir de uma **lista fixa de colunas** — chave extra no dict do
>   parser é ignorada, sem erro e sem log. É a camada que descarta calada, e é a que faltava.
>   *Segue aberto para o caminho da DETECÇÃO* (`masked_categories` existe em
>   `session_stream_events`, não em `messages`). Texto original abaixo, pelo diagnóstico:
>   Conferido nas quatro camadas: `ConversationMessageSentSchema`
>   (`platform-events.ts:419-432`) **não declara** `masked`/`masked_categories`; o produtor do bridge
>   (`main.py:9401+`) não os envia; o parser (`models.py:492-508`) não os lê; o DDL de
>   `plughub_demo.messages` (`clickhouse.py:161-174`) não tem as colunas. Consequência: **não dá para
>   distinguir "detectamos e tokenizamos" de "nunca olhamos"** — as duas leituras são idênticas, que
>   é a assinatura de *valor plausível*. O flag existe só no stream canônico do Redis, escrito pelo
>   `session.ts`. Pré-requisito de qualquer medição séria de cobertura de detecção: sem a coluna, o
>   F0 do arco acima só sabe contar PII crua que ele mesmo souber procurar por regex — ou seja, mede
>   o que já sabe, e não o que escapou.
>
> ### 🆕 O que a V2b deixou aberto (2026-08-29) — achados FORA do escopo dela
>
> · 🔴 **`MaskingDisplayRule` é redefinida à mão no `platform-ui`** (`MaskedToken.tsx:43-57`:
>   interface + `DEFAULT_DISPLAY_RULE` + `MaskingRulesMap`), duplicando `MaskingDisplayRuleSchema`
>   (`schemas/src/audit.ts:308-314`). **Conferido em 2026-08-29: os quatro campos e os quatro
>   defaults COINCIDEM hoje** — não há drift, e é por isso que é dívida e não defeito. Deduplicar
>   exige fazer o `platform-ui` depender de `@plughub/schemas`, que é o **desacoplamento de ~960
>   linhas** já registrado como fora de escopo em
>   [`adr-skill-flow-editor-validation.md`](docs/adr/adr-skill-flow-editor-validation.md) (com risco
>   de dual-instance de Zod documentado em `agent-registry/src/app.ts:58-61`). Não fazer por dentro
>   desta fase foi decisão, não esquecimento.
> · 🟡 **`section.displayRules.title` e `.description` não existem em locale nenhum** — a
>   `MaskingPage` os chama com `defaultValue` em inglês (`:400-401`), então **pt-BR vê inglês**.
>   Viola a invariante i18n do `CLAUDE.md` (chave nos DOIS arquivos). Descoberto ao remover
>   `legacyOverride`, que era a única chave do bloco `displayRules` — o bloco saiu vazio. Não
>   corrigido junto porque **acrescentar tradução muda texto visível**, e isso é mudança própria,
>   não resíduo de uma remoção. Mesma família do `CATEGORY_META` acima.
> · 🟡 **O ramo INCONCLUSIVO de `q_type_catalog_state.sh` não distingue "sem `.entries`" de "sem
>   `jq`"** — com `jq` ausente ele acusa *"resposta sem campo .entries; leitor quebrado"* enquanto
>   imprime um corpo que **tem** `.entries`. A mensagem contradiz a evidência que ela mesma mostra.
>   Inofensivo onde a casa roda (WSL tem `jq`), mas é diagnóstico que aponta para o lugar errado —
>   e o script é um instrumento, que é justamente onde isso custa caro.
>
> ✅ **O ADR existe:** [`docs/adr/adr-contextstore-allowlist.md`](docs/adr/adr-contextstore-allowlist.md)
> — status **Aceito, parcialmente implementado** (V0 metade · V1 · V1b · V2 · V2b entregues; a V3 é a
> próxima; a V4, que é a inversão não reversível, segue não iniciada). *Dizia "proposto" aqui e no
> cabeçalho do ADR até 2026-08-29, com quatro fases já FEITAS na §6 — quem lesse só o rótulo
> reimplementaria a V2.* As sete perguntas abaixo estão **respondidas
> lá** (§3), e o modelo mudou em três pontos por decisão do dono na sessão de 08-26:
> **(1)** categoria abstrata virou **TIPO** (formato × máscara-por-papel × classe LGPD numa
> declaração só, qualquer uma podendo ser vazia); **(2)** a hierarquia de negócio entra na CHAVE,
> em `escopo.dominio.campo` — o escopo fica no primeiro segmento porque hoje ele roteia hash+TTL;
> **(3)** o legado vira **alias contado e datado** no próprio nó do mapa, com a NOVA como canônica.
>
> Este bloco permanece como **evidência medida** (a varredura, a divergência seed×config viva, as
> armadilhas). O plano vive no ADR § 6.
>
> ⚠️ **Duas referências deste bloco estão OBSOLETAS** (conferidas em 08-26): o defeito do pool de
> entrada não está em `server.ts:1421` (é `:1636` + `:1654-1668`) e o `catch {}` mudo não está em
> `:1452` (é `:1667`). O arquivo deslocou ~215 linhas com a F5. **E deslocou de novo na V1b**, que
> tirou ~190 linhas do `server.ts` para a lib — os dois números acima estão outra vez defasados,
> desta vez para MENOS. Conferir por conteúdo, nunca por linha herdada.
>
> ### 🆕 Três achados da V1b, nenhum consertado
>
> · 🔴 **`packages/e2e-tests/scenarios/17_context_store.ts` Parte E nunca exerceu o tool.** Faz
>   `POST ${mcpServerUrl}/mcp` à mão — **rota que não existe** (o transporte é SSE: `GET /sse`
>   anuncia `/messages?sessionId=…`, e as respostas voltam pelo stream). Recebe o HTML de 404 do
>   Express e cai no `catch` → `fail`. As três asserções sobre `context_snapshot` daquele cenário
>   são inertes, apesar de o mesmo pacote ter um cliente MCP correto
>   (`packages/e2e-tests/lib/mcp-client.ts`, `SSEClientTransport`) usado por seis outros cenários.
>   O conserto é trocar o `fetch` pelo `McpTestClient`; a Parte E então passa a exercer a máscara
>   nova, e as asserções de PRESENÇA de chave continuam válidas (a máscara preserva a chave).
> · 🔴 **`src/__tests__/bpm.test.ts` — `conversation_escalate` VERMELHO desde 2026-08-18, e o
>   serviço está CERTO.** O teste não escreve `session:{id}:meta`, e a recusa por tenant
>   desconhecido é o conserto deliberado daquela data (*"identidade não tem fallback"*). Baseline
>   do pacote medida em 08-26: **`1 failed | 221 passed`**. Consertar é escrever o meta no setup —
>   ou, melhor, ASSERTAR a recusa, que é o comportamento que se quer proteger.
> · 🆕 **`invalidateContextMaskingCache` não tem call site nenhum** (agora em
>   `lib/context-masking.ts`). O comentário promete um consumidor de `config.changed` que não
>   existe — promessa-sem-produtor. A janela real de propagação é o **TTL de 60 s**, e é ele que
>   faz medição logo após editar a regra ler a política ANTIGA.

> **Este item era BRIEFING para escrever o ADR, não plano de implementação.** As decisões
> abaixo estão marcadas como DECIDIDA / A DISCUTIR de propósito — o valor da próxima sessão
> estava em fechar as segundas, não em codar as primeiras.

### O problema, medido

`default_unmatched_operator: "plain"` é **deny-nothing**: toda tag sem regra é exibida (e, desde
a F5, **persistida**) em claro. A proteção depende de alguém lembrar de escrever uma regra exata.

Medido em 2026-08-26 com `infra/test/sweep_ctx_tags.sh` sobre 24 sessões vivas — três buracos, e
o padrão entre eles importa mais que os três:

| tag | sessões | o que é |
|---|---|---|
| `session.cpf` | 2 | CPF em claro. Havia regra para `caller.cpf`, não para `session.cpf` |
| `journey.numero_cartao` | 1 | cartão em claro. Havia para `session.numero_cartao`, não para `journey.` |
| `session.delegate_resume_token` | 11 | **capacidade** em claro |

**A causa é estrutural, não esquecimento.** `caller.*` e `account.*` têm catch-all; `session.*` e
`journey.*` **não podem ter** — o seed do config-api avisa por escrito que um `session.* → hidden`
derruba `session.dialog_form_id`/`session.decisions` e a tela de aprovação para de renderizar. E é
exatamente em `session.` que o `delegate.context` de um workflow deposita os campos. **Todo campo
que um workflow passa adiante nasce desprotegido.**

**Remendo já aplicado (2026-08-26, dentro da F5), que NÃO fecha o arco:** globs de SUFIXO
(`*.cpf`, `*.resume_token`, …) protegem por TIPO DE CAMPO em vez de por namespace. Cobrem os tipos
que **por acaso conhecemos** — um `session.rg` amanhã nasce exposto de novo. É patch no modelo,
não o modelo.

### A proposta do dono

1. Padronizar: **tudo que vai ao ContextStore é declarado** em `Configuration › Masking › Regras de
   Context Store`.
2. **Campo não configurado NÃO é acessível** (inversão para deny-by-default).
3. Na tela do pool, `Context Store Visibility` lista **só o que está configurado**, em vez de texto
   livre.

### O que já está DECIDIDO

- **A inversão está certa.** É a mesma forma que o repo já adota na borda do channel-gateway
  (*"é uma ALLOWLIST de sete prefixos — nunca uma proibição"*). Inverte o modo de falha: esquecer
  uma regra passa a ser *"o operador não vê um campo"* — visível, reclamável, corrigível — em vez
  de vazamento silencioso.
- **A tela do pool vira seletor**, não texto livre. Foi texto livre que deixou a dica prometer o
  namespace `journey` num default que nunca o teve (corrigido em 2026-08-26).
- **Não entra por dentro de outra fatia.** Mexe em política de exibição de PII: ADR próprio.

### PRÉ-REQUISITO inegociável — a omissão precisa deixar de ser MUDA

Hoje `applyContextMaskingDynamic` faz `continue` e o campo **some sem dizer**. Virar a chave sem
consertar isso **troca um vazamento de PII por uma quebra muda de UI** — trade pior: vazamento se
descobre auditando; tela que some sem motivo se descobre com o operador parado.

O conserto já existe em FORMA, na F5: **contar, não omitir** (`value: null` + `category: "hidden"`
+ `hidden_count` em coluna própria). A UI precisa do mesmo: *"3 campos ocultos por política"*,
dizendo quais. **Esta fatia vale sozinha, mesmo que a inversão nunca aconteça.**

### O que o ADR precisa DISCUTIR e fechar → **RESPONDIDAS em `adr-contextstore-allowlist.md` §3**

> As sete abaixo ficam como registro do raciocínio. Duas mudaram de forma ao serem respondidas:
> a **4** (`hidden` × `full`) deixou de ser escolha global — virou propriedade do TIPO; e a **2**
> (granularidade) ficou: allowlist **por campo**, reuso **por tipo**. `segment.{uuid}.*` continua
> sendo o caso sem padrão exato possível, agora declarado como trabalho da fase V3.

1. **✅ RESPONDIDA em 2026-08-26 — as regras VÊM do config-api, e a TELA é que mente.**
   Medido: `GET /config/masking?tenant_id=tenant_demo` devolve `entries.context_rules` com **14
   regras** (nível global, que o tenant resolve). Elas estão em vigor — o `***25` do CPF sai dali.
   **O defeito é da tela:** `MaskingPage` mostra *"Nenhuma regra configurada — usando defaults do
   sistema"* e `0 regras configuradas`. Ela lê
   `maskingEntries['context_rules']?.value ?? maskingEntries['context_rules']`, e o `??` deveria
   cair no objeto cru — então há algo antes disso (fetch sem `tenant_id`? outra forma em
   `maskingEntries`?). **É o melhor primeiro alvo da sessão nova:** barato, visível, e é a razão da
   desconfiança que abriu este arco (*"não sei se seguem"*).
   ⚠️ **Armadilha de medição que quase produziu a conclusão OPOSTA:** a 1ª versão de
   `apply_masking_suffix_rules.sh` lia `.entries.context_rules.value` — mas o envelope `.value` é
   do **corpo do PUT**, não da resposta do GET. O script saiu `não consegui ler` e a leitura
   natural seria *"a chave não existe ⇒ a página é decorativa"*, que é FALSO. **Leitor quebrado e
   ausência real produzem saída idêntica.** Corrigido com `select(has("rules"))`.

1b. **🔴 O seed e a config VIVA divergiram — nos dois sentidos** (medido no `antes:` do apply):

   | regra | `config-api/seed.py` | config viva |
   |---|---|---|
   | `session.vencimento_cartao` | ✓ | **ausente** |
   | `session.cpf_titular` | **ausente** | ✓ |

   Armadilha conhecida (*fonte declarativa tem aplicador separado*): o `seed.py` foi editado depois
   de a base estar semeada, e seed-if-absent nunca reaplicou. Consequências medidas: (a)
   `session.vencimento_cartao` **nunca esteve protegido** neste ambiente, e a varredura mostra que
   a tag existe — quarto buraco, hoje coberto pelo `*.vencimento_cartao`; (b) `session.cpf_titular`
   só existe vivo, então **instalação limpa nasce sem ele** — e `*.cpf` **não** o cobre (o sufixo é
   `cpf_titular`, e o casamento é em fronteira de segmento). **O ADR precisa decidir quem é a fonte
   de verdade destas regras**, porque hoje não é nenhuma das duas com confiança.
2. **Como declarar as ~40 tags reais sem virar um formulário impraticável.** A varredura mostra
   famílias, não campos soltos: `session.pool.*`, `hook.*`, `segment.{uuid}.*`, `session.queue.*`.
   Um `segment.{uuid}.inviter_participant_id` **não tem padrão exato possível** — precisa de
   `segment.*`. Pergunta: a allowlist é por família (prefixo/sufixo) ou por campo? Se for por
   campo, quem mantém?
3. **Quem semeia a allowlist inicial.** Sem semente, virar a chave apaga a tela. A varredura é a
   fonte natural — mas ela vê só o que o demo exercitou. Tag de fluxo raro não aparece, e o
   modo de falha é a tela sumir em produção, não em teste.
4. **O default é `hidden` ou `full`?** `hidden` some da linha (e a UI conta); `full` mostra `***`.
   Para campo funcional (`dialog_form_id`) os dois quebram igual; para PII, `full` é mais
   informativo e `hidden` mais restritivo.
5. **Vale um modo de AUDITORIA antes de virar a chave?** Rodar deny-by-default em modo "só loga o
   que teria sido escondido" por N dias produz a lista real sem quebrar ninguém. É a versão
   honesta do que hoje seria adivinhação — e a passagem de hoje tem três casos de gate que nasceu
   verde por falta de população.
6. **O escopo da inversão inclui a PERSISTÊNCIA (F5)?** O snapshot hoje usa
   `maskContextForPersistence`, que aplica masking de VALOR e **não** o portão de namespace (desvio
   deliberado, ver ADR de histórico §F5). Deny-by-default no valor afeta o registro durável; no
   portão, não deve afetar — mas isso precisa ser dito, não presumido.
7. **`journey.*` agora é durável e tem TTL de 30 dias.** Merece política própria ou herda a de
   `session.*`? O ctx de processo atravessa N contatos e sobrevive a todos eles.

### Armadilhas registradas — não redescobrir

- **`session.*` não pode receber catch-all `hidden`** (derruba a tela de aprovação, em silêncio).
- **Matcher só entende exato, prefixo `x.*`, sufixo `*.x` e `*`** — qualquer outro padrão devolve
  `null` e vira **regra que não regra** (aparece na tela, não faz nada). Multi-nível funciona nos
  dois lados; casamento é em **fronteira de segmento**, nunca substring (`*.cpf` não pode casar
  `session.xcpf`).
- **Score é sensível à profundidade** desde 2026-08-26 (prefixo `10+n`, sufixo `15+n`, exato 20).
  Sem isso, dois globs da mesma família empatam e o desempate vira ORDEM DA LISTA.
- **`admin` é `supervisor_role` por default** e pula o portão inteiro. **Testar a política logado
  como `operator`** — comparar duas telas de admin não julga nada, e comparar admin × operator
  numa sessão sem PII também não (aconteceu em 2026-08-26: as duas telas idênticas, zero
  informação). Discriminador pronto: `infra/test/probe_context_visibility.sh`.
- **O mcp-server cacheia a config de masking por 60 s.** Medir antes disso lê a política antiga e
  parece que a escrita não pegou.

### Evidência e ferramentas prontas

`infra/test/sweep_ctx_tags.sh` (inventário de tags, com `--values <tag>`) ·
`infra/test/probe_context_visibility.sh` (portão × allow-tag × bypass, com controle positivo) ·
`infra/test/probe_context_snapshot_endpoint.sh` (o endpoint da F5) ·
`infra/test/apply_masking_suffix_rules.sh` (escrita pela API oficial, com leitura de volta) ·
`server.ts` `ruleSpecificity` / `applyContextMaskingDynamic` / `maskContextForPersistence` ·
`config-api/seed.py` § `masking/context_rules`.


## `Context Store Visibility` — o portão FUNCIONA; o que sobra são dois desalinhamentos *(medido 2026-08-26)*

> ✅ **A pergunta original foi respondida por medição, e a resposta é "sim, respeita".**
> `infra/test/probe_context_visibility.sh` (novo, re-executável, sem resíduo): portão de namespace
> ✓, allow-tag furando o portão ✓, bypass de supervisor ✓, testemunha de presença ✓.
> Discriminador visível no número: **admin 5 campos, operator 4** — a diferença é o `caller.nome`.
>
> ⚠️ **A tentativa ingênua não julgava nada.** Comparar a aba Contexto como admin × operator deu as
> duas telas IDÊNTICAS — porque (a) `admin` está em `supervisor_roles` por default e pula o portão,
> e (b) a sessão só tinha tags `session.pool.*`, dentro do default. *Um teste de igualdade só julga
> se a população contiver o caso em que A ≠ B.* O probe INJETA `caller.nome` (ns barrado) +
> `caller.customer_id` (allow-tag) — os dois no MESMO namespace de propósito, que é o par que separa
> "portão por namespace" de "bloqueio por namespace".

**1. A tela prometia um namespace que o código não concede — CORRIGIDO, sem mecanismo.**
As duas locales diziam *"Padrão: service, journey, session"*; `DEFAULT_OPERATOR_NAMESPACES` é
`["service", "session"]`. Divergência na direção pior: promete MAIS visibilidade do que existe, e o
sintoma é *"o operador não vê o que a tela disse que veria"* — ninguém abre chamado sobre um campo
que não apareceu. Decisão do dono: **alinhar o TEXTO ao código** (o código é o comportamento vigente
e foi escolhido conservador; acrescentar `journey` ampliaria a exposição de todo operador em todos
os pools de uma vez, e isso é decisão de produto, não conserto de texto).
⚠️ **Não há mecanismo que impeça a volta** — só um comentário cruzado em `server.ts`, que é a mesma
família de promessa-sem-produtor que a § Postura de Engenharia critica. Um gate por `grep` da
constante seria frágil (o próprio comentário que a documenta reescreve o nome). **Aberto: como
travar constante × texto de UI sem inventar um teste que não pode reprovar.**

**2. A visibilidade é lida do pool de ENTRADA, não do que atende.** `server.ts:1421` resolve o
`poolId` de `session:{id}:meta` — e esse campo é o pool de entrada (fatia C, abaixo). Num contato
transferido, a config aplicada é a do pool de ORIGEM: configurar `retencao_humano` não teria efeito
para quem entrou pelo `sac_ia`, e nada fica vermelho. **É consumidor novo da fatia C**, não item
próprio — ver `session:{id}:meta` no TEMA · Sessão.

**3. Degradação silenciosa na leitura da config** (`server.ts:1452`): `catch {}` sem log, e o
`if (cfgRaw)` cai no default sem dizer. Hoje inócuo — o bridge é renovador único da chave a cada
15 s —, mas "config não aplicada" e "config é o default" são indistinguíveis se um dia falhar.
Conserto é uma linha de log, e cabe junto de qualquer toque futuro nesse bloco.

## Auditoria MCP sem STORE — `mcp_audit_log` não existe em banco nenhum *(medido 2026-08-21; **reenquadrado e parcialmente fechado 2026-08-22**)*

> ✅ **Fechados em 2026-08-22** (ver CHANGELOG): o **gate ABAC** de `/v1/audit/*`, que existia só no
> docstring — qualquer token válido do tenant lia dado pessoal, e o `401` de token malformado é
> autenticação, não autorização — e o **`audit_access_log`**, cuja promessa ("todo acesso é
> registrado", no docstring e no banner da UI) era falsa. Gate: `probe_audit_surface.sh` +
> `tests/test_audit_gate.py` (17).
>
> **O enquadramento desta seção estava errado**, e a medição é que mostrou: o pipeline **não degrada
> mudo**. `parse_mcp_audit_event` grava em `session_timeline` e é de lá que `/v1/audit/mcp-calls` lê —
> a chamada MCP é registrada e é legível. A pergunta "o consumer tenta criar a tabela?" tem resposta
> simples: o DDL **nunca esteve** em `_ALL_DDL` (`clickhouse.py`), e não há `DROP` registrado, ao
> contrário de outras remoções deliberadas do mesmo arquivo. Sumiu sem rastro no código, com rastro só
> no CHANGELOG de 2026-05-14, que também declarava entregues o `audit_router.py` e o
> `_require_audit_access` — nenhum dos dois existia.
>
> **ABERTO — `mcp_audit_log`, e é DORMENTE por medição, não por opinião.** `session_timeline` tem 0
> linhas e recebe linha de um único parser (o de `mcp.audit`) ⇒ a borda `invoke` nunca foi exercitada
> neste ambiente. **Não criar a tabela antes de haver tráfego**: uma tabela vazia com cara de pronta é
> indistinguível de "ninguém acessou", e é o modo de falha que já custou caro aqui. Ordem correta:
> (1) exercitar `invoke` de verdade e confirmar linha em `session_timeline`; (2) só então DDL +
> dual-write em `parse_mcp_audit_event` + branch em `_write_row`.
>
> **Resíduo medido de lado, não consertado:** o `invoke` emite `session_id: ""` quando a instância tem
> 0 ou 2+ sessões ativas (`external-agent.ts:194-215`), e `parse_mcp_audit_event` **descarta** evento
> sem `session_id` (`models.py:713-718`) — perda silenciosa que nenhum contador acusa. Vale medir junto
> com (1), porque decide se o dual-write chega a receber a linha.

Consulta a `system.tables` por `%audit%` **ou** `%mcp%`, sem filtro de database, voltou **vazia**.
Nem `mcp_audit_log` nem `audit_access_log` existem neste ambiente — as duas que o `CLAUDE.md`
§ Audit LGPD descreve como criadas pela analytics-api.

Peso: o invariante *"nenhum caller pode optar por sair do audit — política definida na tool"* é de
LGPD, e a única borda de interceptação **em vigor** (`invoke` do mcp-server, `source:
"mcp_server_invoke"`) publica em `mcp.audit` para um consumidor que não tem onde gravar. É a família
*"'existe' ≠ 'está pronto'"*, com o agravante de que o produtor não fica vermelho: publicar em Kafka
funciona; é o outro lado que não materializa.

**Antes de consertar, medir** (a lição de 08-20): o consumer da analytics-api chega a tentar criar a
tabela? Falha no boot e degrada mudo, ou o DDL nunca foi chamado? Uma tabela ausente e um consumer
que nunca rodou produzem a mesma tela. Descoberto de raspão — a pergunta original era "quem chama os
tools `operational`", e ela ficou **inconclusiva por falta deste instrumento**.

---

## As chamadas de domínio do agente NATIVO não passam por interceptação nenhuma *(achado 2026-08-13, ao alinhar o audit do `invoke` — ver CHANGELOG)*

O invariante da plataforma diz que **nenhuma** chamada MCP chega a um domain server sem validação de
permissão, injection guard e `AuditRecord`. Estão cobertas duas bordas de três, e a que falta é a de
maior volume:

| Borda | Quem usa | Estado |
|---|---|---|
| `invoke` (mcp-server) | agente `external-mcp` | ✅ desde 2026-08-13 |
| proxy sidecar | agente externo que fala direto com o domain server | ✅ implementado — mas **só roda se o operador subir o sidecar** |
| `McpInterceptor` (SDK, em-processo) | agente nativo | ❌ **nunca instanciado** — existe em definição e em comentários |

O caminho real do agente nativo é o `mcpCall` do `skill-flow-service`
(`packages/e2e-tests/services/skill-flow-service/src/index.ts:149`, o que o orchestrator-bridge
executa) e o do `skill-flow-worker` (`engine-runner.ts:150`, legado): `fetch` JSON-RPC cru. Sem
filtro de `permissions[]`, sem guard, sem registro.

**Por que ninguém notou:** o modo de falha é a AUSÊNCIA de linhas num relatório. Nada fica vermelho,
nenhuma chamada falha; o `mcp_audit_log` simplesmente não tem o que mostrar, e "não houve chamada"
é indistinguível de "não foi auditada" para quem só olha a tela. Foi o que manteve o `invoke`
publicando num tópico órfão por meses.

**Decisão fechada em ADR** (2026-08-13, proposto): a regra mora no `mcp-server-plughub` e o
`mcp_call` nativo passa a atravessá-lo — saída **(b)**. A saída (a), instanciar o `McpInterceptor` no
skill-flow-service, foi descartada por criar uma segunda implementação VIVA do mesmo veredicto, em
outro processo e outro ciclo de deploy; a regra já está escrita três vezes e as cópias já
divergiram (curinga `server:*` e `permissions[]` vazia decidem diferente no `invoke` e no sidecar).

O ADR levanta o ponto que decide de verdade: **borda é fato de rede, não de código**. Enquanto um
domain MCP server for alcançável a partir do processo do agente, qualquer borda é evitável por
omissão — e nada no repositório garante o contrário hoje.

Fases, gates e custos: [`docs/adr/adr-mcp-interception-single-border.md`](docs/adr/adr-mcp-interception-single-border.md).
**Primeiro passo é M0 — medir** o volume por caminho (nativo × `external-mcp`): o número diz se isto
é lacuna de 5% ou de 95% do tráfego, e o argumento LGPD depende dele.

---

## Masking — 5 mecanismos distintos, config espalhada entre seed.py, Config API e YAML *(achado 2026-08-13, ao investigar por que `session.cpf` aparece aberto pro operator no pacote de aprovação de `skill_limite_processo_v1`)*

Levantamento factual (nenhum código mudado por este item). O sintoma que disparou a investigação
não é bug: `session.cpf` simplesmente não tem regra no Mecanismo 2 (nunca teve, nem quando a
âncora era `contact_identifier`/telefone) — mas expôs que "masking" no PlugHub não é UM sistema,
são cinco, cada um com config/enforcement em lugar diferente, e ao menos um (seed.py) não é
editável em produção sem redeploy de código.

**Os cinco:**

1. **Message/Stream masking** (`MaskingService`, `packages/mcp-server-plughub/src/lib/masking.ts`)
   — regex sobre texto livre do cliente, produz token `[cat:tk_xxx:partial]` no `content` da
   mensagem (stream). Config: namespace Config API `masking`, key `rule.{category}` — **editável
   na UI** (`/config/masking`, `MaskingPage.tsx`). Vault: `token-vault.ts`.
2. **ContextStore field-level masking** (`applyContextMaskingDynamic`,
   `packages/mcp-server-plughub/src/server.ts:1054`) — regras casadas por NOME EXATO/glob de tag
   (`session.numero_cartao`, `caller.*`, `*`) × role do visualizador. Config: namespace Config API
   `masking`, key `context_rules` — **também editável na UI** (`ContextRulesSection` na mesma
   `MaskingPage.tsx`), MAS as regras de `session.*` específicas de cada skill (ex. as três do
   pacote de aprovação de limite) são semeadas em `packages/config-api/src/plughub_config_api/
   seed.py:483-513` — ou seja, a INFRAESTRUTURA do mecanismo é UI-editável, mas o CONTEÚDO
   operacional (quais tags cada skill precisa mascarar) hoje só entra via seed.py, que é
   seed-if-absent: uma vez semeado, editar o arquivo é no-op sem reconcile+redeploy. Isto é
   exatamente o padrão descrito em `docs/adr/...` sobre "toda config de negócio tem que ser
   UI-editável" — este mecanismo viola o invariante na prática, mesmo tendo UI.
3. **Masked Input** (`masked: true` em step/campo de menu, namespace `@masked.*`,
   `begin_transaction`/`end_transaction`) — não é masking de EXIBIÇÃO, é ausência de persistência:
   o valor nunca chega a existir em `pipeline_state`/stream/Redis/log. Declarado em YAML de skill
   OU em campo de DialogForm JSON (`infra/dialog/*.json`, ex. `cvv` em
   `dialog_limite_solicitacao.json`). Enforçado em `packages/skill-flow-engine/src/masking-policy.ts`
   + `steps/begin-transaction.ts`/`end-transaction.ts`/`menu.ts`. Consumido pelo webchat como
   `<input type="password">`.
4. **Port Python no quality-ingest** (`packages/quality-ingest/src/plughub_quality_ingest/
   masking.py:mask_text()`) — réplica HARDCODED (não lida de Config API) do Mecanismo 1, porque
   não existe engine de masking em Python no repo. Roda como rede de segurança na importação de
   transcrições externas.
5. **Audit access log** (`packages/analytics-api/src/plughub_analytics_api/audit.py`) — não é
   masking em si, consome o resultado dos Mecanismos 1 e 3. `masked_input_fields` está no schema
   (`audit.ts`) e no guia, mas **não tem escritor** em `mcp-server-plughub` — sempre `[]` na
   prática (Fase 2 pendente, já registrada em `docs/arcos/audit-lgpd.md`).

**O que falta decidir antes de tocar em código** (por isso este item fica só como levantamento):
- Os Mecanismos 1 e 2 já são via Config API/UI — o problema real é só o CONTEÚDO do Mecanismo 2
  nascer em seed.py em vez de nascer por API/UI desde o início de cada skill novo. Precisa de um
  fluxo (no editor de skill? no editor de DialogForm? um novo painel "regras de contexto por
  skill"?) que evite todo skill novo com campo sensível em `delegate.context` precisar de uma
  entrada manual em seed.py.
- Mecanismo 3 é conceitualmente diferente dos outros (não-persistência vs. mascaramento de
  exibição) — não faz sentido "unificar" ele com 1/2, mas vale deixar isso EXPLÍCITO em algum lugar
  (hoje só está implícito no guia `docs/guias/masked-input.md`) para não ser confundido como
  concorrente dos outros dois numa unificação futura.
- Mecanismo 4 é dívida técnica pura (duplicação de regra sem fonte única) — collapse natural seria
  o quality-ingest ler as MESMAS regras via Config API em vez de hardcode, mas isso muda o
  contrato de "produtor puro, sem dependência do resto da stack" do quality-ingest (ver
  `docs/arcos/quality-ingest.md`) — precisa avaliar se vale a pena.

Levantamento completo (paths, funções, formatos exatos) disponível sob demanda — não replicado
aqui para não duplicar código-fonte em prosa.

---

## Agent Principal — identidade de máquina p/ agentes IA *(spec, 2026-06-28)*

Identidade de máquina (`subject_type:"agent"`) p/ agentes nativos e externos se autenticarem, distinta das
roles humanas; capability vem do `agent_type` (registry), auth-api só emite/rota credencial; audit por
`principal_id`. Nativo = auto-provisionado, **sem UI**; externo = cadastro + secret (API/CLI; UI enxuta na F3).
Fases F1–F4. **Spec**: `docs/product/agent-principal-identity-spec.md`. *(discussão; não implementado)*

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

## Audit LGPD — Fases Pendentes

Fase 1 concluída — ver CHANGELOG 2026-05-14 e `docs/arcos/audit-lgpd.md`.

- **Fase 2** — `original_content` desmascarado: endpoint de resolução de tokens em Core → analytics-api expõe conteúdo original ao DPO. Requer endpoint batch de resolução de tokens no Core.
- **Fase 3** — `user_access` logs: topic Kafka `user_access.events` em auth-api + tabela ClickHouse + tab ativo em AuditPage.
- **Fase 4** — SAR/Erasure pipeline: CRUD de Subject Access Requests + pseudonimização em `sessions_stream` + anonimização ClickHouse (TTL/partition replacement).
- **Fase 5** — `config_snapshot`: leitura read-only do namespace `masking` do Config API para verificação DPO.

---

## 📂 TEMA · Identidade, Outbound e Surveys

## Resolvedor de Identidade — próximos passos (Fases A ✅ e B ✅; falta a Fase C) *(2026-07-02, cabeçalho corrigido 2026-08-03)*

> ⚠️ **Terceiro cabeçalho stale achado na mesma varredura.** Dizia *"falta Slice 3 + Fase B"*. O
> `CLAUDE.md` registra o **Slice 3 ✅ (2026-07-03)** e a **Fase B completa em 3 fases (2026-07-04)** —
> identidade progressiva, posse de canal por OTP e gate seguro. O que falta é a **Fase C**
> (`external_refs` + merge de clientes, wiring do step CRM `resolve`, `resume_origin=same_channel`,
> transporte real do OTP), e ela depende de haver um CRM.
>
> Os três cabeçalhos stale (este, I5 e Capacidade) têm a mesma assinatura: **corpo mantido, título
> não.** Vale como padrão a vigiar — o título é o que sobrevive à leitura rápida, então ele mente
> para mais gente do que qualquer parágrafo interno.

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
   - **Nenhum produtor emite CES/PMF/FCR** — ✅ **reconfirmado 2026-08-03**: os seeds só cobrem `nps`
     (`seed_dialog_nps_buttons_form.sh`) e `csat`+`nps` (`seed_dialog_survey_multi_form.sh`). A
     normalização está pronta e sem dado; falta um form de seed com dimensions CES/PMF/FCR para um E2E
     de verdade (e para o S6/S8 mostrarem algo além de NPS/CSAT).
   - **UI ignora `value_label`** — ✅ **reconfirmado 2026-08-03**: o ternário vazio segue literal em
     `CustomerVoicePage.tsx:161` (`data.instrument.rollup === 'avg' ? '' : ''` — os dois ramos idênticos,
     que é a assinatura de um sufixo que nunca foi escrito), e `AnaliseSurveysPage.tsx:21` declara
     `value_label` na interface sem renderizá-lo. Fatia C do S1.
   - **Rótulos mistos** — CES/PMF/FCR em inglês (spec), NPS/CSAT em pt-BR (histórico gravado). Unificar exige
     decidir migração do histórico + i18n na UI.
2. **S7 (refinos do editor `/config/dialog-forms`):** biblioteca `survey_question` reutilizável, ABAC no
   write (hoje só `X-Admin-Token`), drag reorder, locale lado-a-lado + preview.
3. **S6 (fechar):** view consolidada "Visão do cliente" (cross-cut multi-métrica + divergências §8/§10)
   sobre a base que a lente `customer_voice` já expõe (Customer Voice Fatia 1 = só grão×instrumento + SLA).
4. **Higiene S2 — enunciado CORRIGIDO (medido 2026-08-03).** Dizia *"o registry ainda roda o conjunto
   antigo"*. **Falso**: o conjunto antigo foi REMOVIDO na Camada E1 (2026-07-24) — de
   `tenant_demo.yaml` sobrou só o comentário na linha 374 explicando a remoção. O estado real é
   outro e é pior: o **trio novo existe como YAML e NENHUM pool o deploya** —
   `skill_survey_runner_v1`, `skill_survey_outbound_v1` e `skill_survey_trigger_v1` estão em
   `packages/skill-flow-engine/skills/` e não têm uma única menção em `infra/registry/`. São
   arquivos mortos da mesma família dos dois pacotes fósseis: existem, não rodam, e ensinam um
   modelo que ninguém executa. **Decidir**: deployar como pools, ou remover junto com os fósseis.
5. **Store per-response** — ✅ **FEITO E VALIDADO (2026-07-23, ver CHANGELOG).** Schema PG `survey`
   (`survey.survey_instance` + `survey.survey_response`, `db.py:632-672`), `persist_survey_response`
   (`:723`) e `list_survey_responses` (`:801`); `survey_record` persist-first; `survey_web.submit`
   captura verbatim. ADR aceito: [`docs/adr/adr-survey-response-store.md`](docs/adr/adr-survey-response-store.md).

   > **Resíduo textual removido em 2026-08-03:** este item terminava com *"**Falta só codar**"* logo
   > depois de se declarar FEITO — parágrafo da spec pré-implementação que sobreviveu à
   > implementação. Contradição dentro do mesmo item, e do tipo que engana: quem lê o fim decide
   > que há trabalho, quem lê o começo decide que não. Conferido no código antes de podar (as duas
   > tabelas, o persist e o list existem). O "endpoint de leitura de S8" que ele listava como aberto
   > também já existe — é o `list_survey_responses`.
6. **Valor novo (loop captura→leitura→ação):** **S8** ✅ **FEITO (2026-07-23).** Restante: **S9**
   (`agente_survey_analyst_v1` — classifica verbatim + áudio/transcript via `attachment_store`) →
   **S10** (retorno outbound + caixa de ações) → **S11** (NPS/PMF relacional agendado). Refino de S8:
   export CSV (opcional) + guard de rota ABAC (Item 3 app-wide).

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

- ~~**Fase 3b do Outbound — a validar**~~ ✅ **validado 2026-08-20** (ver CHANGELOG). O smoke rodou
  verde e ganhou as duas metades que faltavam: **testemunha** (cliente SEM opt-out na MESMA campanha e
  MESMO canal → `allowed`) e **opt-out por canal**. Antes disso o gate provava que *alguém* era vetado,
  não que era vetado *quem optou por sair*: passos 2 e 4 ficavam verdes mesmo se o portão vetasse todo
  mundo, porque o único caso permitido era `transactional`, que pula o portão antes de consultar o
  cadastro (`db.py:863`). **Ramo ainda não exercitado, declarado na saída do gate:** a degradação com o
  identity fora do ar (→ allow barulhento) — exige derrubar o channel-gateway.
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

## 📂 TEMA · Analytics e UI

## ✅ A barra de SLA do Console não estava ligada em nada — REMOVIDA *(medido e resolvido 2026-08-24)*

**Achado colateral da D14.1.** Decisão do dono: **remover** (não existe alvo de atendimento por
segmento no produto) e o relógio ⏱ passa a mostrar o **tempo neste segmento**, ancorado no servidor.
Entregue — ver `CHANGELOG.md` de 2026-08-24. O diagnóstico abaixo fica como registro do que havia.

O `supervisor_state` tem **duas implementações**, e quem alimenta a tela é o endpoint
HTTP (`useSupervisorState.ts:30` → `server.ts:1617`) — não a tool. Ele devolve:

```
sla: { elapsed_ms: 0, target_ms: 480_000, percentage: 0, breach_imminent: false }
```

**Constantes.** Quatro efeitos, todos verificáveis na tela sem instrumentação:

| superfície | onde | efeito |
|---|---|---|
| aba **Estado** (painel direito) | `EstadoTab.tsx:72,213,217` | `sla.percentage` = 0 sempre ⇒ barra vazia, "0%", breach nunca acende |
| **barra fina** da linha na lista de contatos | `ContactList.tsx:146-147` | `slaFromState?.target_ms ?? contact.slaTargetMs` — `480_000` é **truthy** e vence **assim que o primeiro poll chega**; antes disso o valor do pool é usado ⇒ a barra **muda de escala sozinha** no primeiro poll e depois fica em 8 min para todo contato (um em `retencao_humano` 5 min e um em `limite_entrega` 7 d passam a ter a **mesma**) |
| **borda esquerda** de urgência (3 px) + cor do cronômetro | `ContactList.tsx:60-66` | mesma coisa: 8 min para todos |
| **breach** | `ContactList.tsx:151` | `slaFromState?.breach_imminent ?? (…)` — `false` é boolean, o `??` não cai fora ⇒ **o fallback que a UI já tinha está desarmado** |

⚠️ **O último é o `CLAUDE.md` § Sentiment se repetindo palavra por palavra:** *"Um default no
produtor derruba a guarda do consumidor sem deixar rastro."* Mesmo modo de falha, campo diferente —
e, como lá, o defeito estava na implementação HTTP, não na tool que todo mundo lê primeiro.

⚠️ **Nem o caminho calculado entrega.** `tools/supervisor.ts:202-204` computa de verdade
(`elapsedMs`, `urgency`, `breach_imminent`) mas devolve **`urgency`**, e a UI lê **`percentage`**
(`types.ts:157`) — nome que **nenhum produtor escreve**. Duas implementações, nenhuma alimenta o
campo que a tela consome.

**Saída escolhida: (a) a barra sai.** Uma barra que normaliza o tempo de um contato **já atendido**
por um alvo de espera não mede nada — a espera acabou quando o contato chega ao Console. O que
sobrou e mede de verdade é o `max_reply_time_ms` (campo real, por mensagem, com superfície própria
no timer 💬). **Não foi consertado o cálculo**: dar precisão a um indicador sem alvo definido seria
trocar um valor plausível por outro mais convincente.

Removido nos **dois apps** — `platform-ui` (`ContactList`, `EstadoTab`, `Header`, `AgentAssistPage`,
`types`) e `agent-assist-ui` (`ContactList`, `Header`, `EstadoTab`, `App`) — mais o produtor
(`server.ts`), que era a origem. ⚠️ **Achado no caminho:** das seis superfícies, cinco guardavam com
`sla &&` e teriam degradado sozinhas; **`agent-assist-ui/EstadoTab.tsx:59` não guardava** e teria
lançado em runtime ao remover o campo do servidor.

### ⚠️ E o relógio ao lado da barra tem defeito PRÓPRIO — a base é do navegador

Achado ao investigar a barra (2026-08-24). O ⏱ rotulado *"Tempo em atendimento"*
(`ContactList.tsx:140`) e o `HandleTimer` do `ActionBar.tsx:271` derivam ambos de
`contact.sessionStartedAt`, que é **`new Date()`** — o instante em que **aquela aba do navegador**
criou o objeto do contato (`AgentAssistContext.tsx:67`). **Nenhum caminho o preenche com dado do
servidor**; a fábrica é o único escritor.

Consequências:

- **zera no F5.** O agente recarrega e o relógio do contato volta a zero.
- não é o início do **segmento** nem o do **contato** — é *"desde que esta aba soube deste contato"*.
- é a **mesma base** do `handleMs` que alimenta a barra morta e a cor da borda de urgência.

⇒ Não são "uma barra quebrada e um relógio bom": são **dois indicadores sobre a mesma base errada**,
e a barra só chama mais atenção por não ter número que denuncie. Consertar a barra sem consertar a
base seria dar precisão a um valor que reinicia sozinho.

**Três fatos distintos existem no modelo e nenhum está na tela** (levantado pelo operador, que
perguntou se valeria exibir o tempo de contato):

| fato | nível | valor para o agente ao vivo |
|---|---|---|
| tempo neste **segmento** | segmento | é o que o ⏱ tenta ser; alimenta o AHT do agente |
| **tempo de contato** | sessão | *"está nisto há 22 min, com dois agentes antes de mim"* — muda o tom |
| **espera antes de chegar a mim** | segmento de fila | *"esperou 8 min"* — muda a primeira frase |

O terceiro passou a ser fato de ledger confiável com a fatia (A) de 2026-08-24 (antes duas esperas
colidiam numa linha só).

✅ **Decidido e entregue (2026-08-24): o ⏱ mostra o TEMPO NESTE SEGMENTO**, ancorado no `assigned_at`
do servidor. O carimbo já viajava no evento (`orchestrator-bridge/main.py:1155`), o tipo já o
declarava (`WsConversationAssigned.assigned_at`) e o replay preserva o valor original (o bridge
persiste o **mesmo `event_json`** em `pool:pending_assignment`) — **só ninguém o lia**. Corrigido nos
dois apps, com degradação BARULHENTA: carimbo ausente/ilegível volta ao relógio do navegador **e
loga**. Fallback mudo aqui restauraria o defeito sem deixar rastro, porque relógio errado conta
igualzinho a relógio certo.

⏳ **Aberto:** exibir *tempo de contato* e *espera antes de chegar ao agente* segue sem decisão — são
informação nova (não conserto), e pedem desenho de onde cabem na linha.

⚠️ **Sem gate.** Falseabilidade sugerida: o probe pede `/api/supervisor_state/{sid}` de uma sessão
viva e reprova se `sla.percentage` vier `0` **com** `elapsed_ms` `0` — testemunha de presença ao
lado: `turn_count > 0` no mesmo payload (senão "sessão vazia" e "campo morto" têm a mesma cara).

## Console não libera a tela após `Transfer` — histórico e contexto ficam com a sessão já fechada *(inconformidade relatada 2026-08-24, NÃO diagnosticada)*

**Relato do operador (é o requisito, não uma hipótese):** depois de executar **Transfer**, o esperado é
que o Console se comporte como o **Close** — apagar histórico da conversa e contexto da tela e ficar
livre para o próximo contato. Não é o que acontece: a tela mantém a transcrição e o painel de contexto
mesmo com `Session closed`.

**Observado na tela (contato `27651d1b-…dc9a3d1a0c0c`, transferido para `especialista_onboarding`):**

| Elemento | Estado |
|---|---|
| cartão do contato | badge `ended`, ainda listado em `CONTACTS (1)` |
| cabeçalho | `Session closed` + botão `Close` ativo |
| faixa 1 | `Client disconnected — fill in the close form to release the contact.` |
| faixa 2 | `Wrap-up in progress — respond to the finalization agents' prompts below.` |
| painel direito | `Admin · primary · ✓ Closed` |
| toasts | `Transferred to especialista_onboarding` + `Contato transferido. Aguardando encerramento...` |

**A hipótese "o wrap-up está segurando a tela" foi levantada e REFUTADA no mesmo dia**, por dois fatos
independentes — fica registrada para ninguém a reabrir:

- **fato do dono do código:** o wrap-up **sempre** apaga a tela, porque **sempre restaura o histórico
  ao exibir**. Ficou assim quando `inline`/`detached` virou configurável — os dois modos foram
  padronizados e a tela de wrap-up mostra um histórico REDUZIDO próprio. Logo a transcrição da tela 1
  não é a do wrap-up.
- **fato medido:** `GET :3300/v1/pools/retencao_humano` → `hooks.on_human_end[0].pool =
  "wrapup_detached_ia"`, `side: agent` ⇒ o pool está em **`detached`**, e o `CLAUDE.md` (medido
  2026-08-22) estava certo. O conflito doc × tela que eu havia registrado **não existe**.
- **e o que fecha:** a tela permanece **mesmo depois de o wrap-up ser finalizado**. Não há input
  pendente para justificar a retenção.

⇒ **Diagnóstico: teardown ausente no caminho do `Transfer`.** O `Close` libera a tela; o `Transfer`
não. Não é config de hook, não é wrap-up, não é modo de dispatch.

**Pergunta que abre a investigação:** `Transfer` e `Close` compartilham o caminho de teardown do
Console, ou são dois? Se forem dois, a divergência é estrutural e reaparece a cada mudança — mesma
família do `agent-assist-ui` logo abaixo (dois lugares para o mesmo conserto). ⚠️ E se houver conserto
de Console, ele provavelmente precisa ser feito **duas vezes** enquanto o `agent-assist-ui` viver.

⚠️ **Escopo do relato:** observado no fluxo transfer → cliente desconecta → wrap-up finalizado. **Não**
foi testado transfer com o cliente ainda conectado — caso em que a sessão do agente de origem termina
mas o contato CONTINUA, e aí "limpar a tela" tem outro significado. Vale cobrir os dois no conserto.

## `agent-assist-ui` é um SEGUNDO Console vivo — aposentar ou assumir *(migrado da passagem de 2026-08-26)*

`packages/agent-assist-ui/` é app legado e **serviço vivo na porta 5173 do compose demo**, sem
profile que o desligue. Renderiza a mesma tela do Console do `platform-ui`, e por isso **todo
conserto de Console precisa ser feito duas vezes** — foi o que aconteceu no conserto de sentimento
de 2026-08-25, que só ficou completo porque a duplicação foi notada a tempo.

**Decisão pendente, e é binária:** aposentar (removendo do compose e do repo) ou assumir (e então
entra na regra de que toda mudança de Console é feita nos dois). O estado atual — vivo, sem dono e
sem regra — é o que garante que a próxima correção nasça pela metade.

› Resíduo adjacente já registrado na § *Resíduos do conserto da exibição de sentimento*: código morto
em `platform-ui/…/ContactList.tsx` (`sentimentColor` e `sentimentScore` sem uso).

## O adapter de whatsapp publica o `phone_number_id` DENTRO do campo `pool_id` *(achado 2026-08-14, ao desenhar o carimbo `entrou por`)*

`adapters/whatsapp.py:386-387` faz `pool_id = phone_id` com o comentário
`# pool resolved by routing engine from phone_number_id`. **A resolução prometida não existe:**
`grep -r phone_number_id packages/routing-engine` devolve **zero**. O consumidor trata `pool_id`
como pool literal, não acha, e cai no drop gracioso de `main.py:791`.

Por que só apareceu agora: até a F1b o valor era sobrescrito pelo `routed` antes de chegar a
qualquer leitor — invisível por acidente. Com o carimbo first-write-wins, ele **congelaria** um
número de telefone dentro de `sessions.pool_id`.

**Exposição medida (2026-08-14): ZERO.** `tenant_demo` tem 288 sessões `webchat` e 125 `webhook`,
**nenhuma** `whatsapp` — coerente com o M2 do ADR (zero linhas de whatsapp em `channel_endpoints`).
Por isso **nada foi feito no analytics**: pôr um `if channel == 'whatsapp'` no parser seria mascarar
defeito de produtor e enfiar conhecimento de canal numa camada que não deve tê-lo. O conserto certo
é no channel-gateway — ou o adapter resolve o pool de verdade, ou manda o phone num campo próprio e
deixa `pool_id` vazio.

⚠️ **Gatilho de reativação:** primeira sessão real de whatsapp. `probe_entry_pool_base.sh` bloco 3
conta por canal e é onde isso aparece.

---

## Tabela construída como duas grids irmãs — 2 telas abertas *(achado 2026-08-11, ao consertar `/analise/wrapup`)*

O cabeçalho é um `<div grid grid-cols-[...auto...]>` e **cada linha de dado é outro**. Grids irmãs não
compartilham trilha: `auto` dimensiona pelo conteúdo *daquela* grid, e os conteúdos são de naturezas
diferentes — palavra no título, dígito no dado. As colunas divergem **por construção**, e o `1.4fr` da
primeira coluna, absorvendo sobras diferentes em cada grid, espalha o cabeçalho e espreme os números.

Corrigido em `analise/WrapupSummaryPage.tsx` (virou `<table>`, ver CHANGELOG 2026-08-11). **Abertos:**

| arquivo | linhas | por que ainda não saltou |
|---|---|---|
| `work-items/WorkItemsPage.tsx` | :88 / :352 | `1.2fr_auto×5`; os dados são mais largos, então o desvio é pequeno — **não é ausência de defeito, é ausência de sintoma** |
| `schedules/SchedulesMonitorPage.tsx` | :101 / :108 | `1fr_1fr_auto_1.4fr`; só uma coluna `auto`, o resto é `fr` (que não depende do conteúdo) |

**Não trocar `auto` por px fixo** — conserta a foto e não a causa: o primeiro rótulo traduzido mais
longo (pt-BR costuma ser) reabre o defeito sem nada ficar vermelho. A saída é `<table>`, que é também o
padrão dominante do platform-ui (46 usos / 31 arquivos). Regra registrada em
`docs/arcos/platform-ui.md` § "What never to do".

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

## Analytics — revisar workarounds pré-`row_version` *(resíduo do fix de 2026-07-13)*

Com `sessions` já em `ReplacingMergeTree(row_version)`, revisar (e provavelmente remover) os workarounds
de `COALESCE` / `channel=""` no analytics-api que existiam **só** para mitigar a corrida entre tópicos.
Histórico do bug e do fix no `CHANGELOG.md`.

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

## 📂 TEMA · Config, Registry e provisionamento

## Seeds escrevem substrato de produção sem carimbar `origin` *(achado 2026-08-12, ao medir o histórico)*

`infra/test/seed_deploy_lens_demo.sh:61` e `infra/test/seed_epoch_demo.sh:63` inserem `segments` **direto
no ClickHouse por HTTP**, fora do pipeline de eventos — daí não nascer linha em `sessions` (15 sessões
órfãs medidas). A assinatura é `started_at` em `10:00:00.000` exato, `pool_id='sac_ia'`, `channel='webchat'`.

**O defeito não é a órfã: é que a lista de colunas do INSERT não inclui `origin`**, então as linhas caem no
default e saem como `live`. O discriminador `origin: live|import|reeval` foi construído exatamente para
manter substrato não-produtivo fora dos relatórios, com filtro default `live` na camada de leitura — e o
escritor passa por fora do mecanismo que existe para ele. É o mesmo formato de *"fonte declarativa tem
aplicador separado"*: o mecanismo existe, quem escreve não o usa, e nada fica vermelho.

Efeito medido: `sac_ia` tem 85 contatos reais **mais** 15 sessões sintéticas em `segments`, indistinguíveis
por query. Para a lista de contatos não vaza (as órfãs caem fora ao juntar com `sessions`), mas vaza em tudo
que agrega `segments` sem juntar — incluindo o filtro *atendido por* de D12, se implementado como agregação
em vez de subconsulta.

Conserto barato: os seeds carimbarem `origin`. Vale também um gate que reprove INSERT em tabela de substrato
sem a coluna — senão o próximo seed repete.

---

## Prontidão de provisionamento — não há sinal de "o syncer terminou" *(aberto 2026-08-10)*

`rebuild-all.sh` termina em `up -d` e imprime *"Acompanhe a convergência"*, delegando ao olho humano; e o
`orchestrator-bridge` **não tem healthcheck**, então `docker compose ps` não consegue dizer se o
provisionamento acabou. O bridge é o ÚLTIMO a convergir (espera agent-registry + skill-flow-service
healthy, depois sincroniza skills → pools → channel_endpoints), e nada anuncia esse fim.

**Consequência medida, três vezes:** ADR §7.4 (`F2=1` lido antes do seed, que parecia "o seed não
aplicou"), `up -d <serviço>` subindo só o subgrafo, e 2026-08-10 (bateria inteira em INCONCLUSIVO logo
após um `--wipe`, com o bridge em *"Up Less than a second"*). Nos três, o número era **plausível** — e
por isso pareceu resultado, não ausência de medição.

**Conserto:** `infra/test/wait_registry_converged.sh` ✅ *(feito 2026-08-10)* — bloqueia até o registro
ficar **quiescente** (duas amostras iguais e > 0), com timeout e veredicto de 3 estados. Critério é
estabilidade, não contagem fixa: contagem fixa faria do helper um teste do tenant demo, que envelhece a
cada pool novo, e prontidão não é a mesma pergunta que inventário (essa é do probe). `EXPECT_*` permite
exigir números exatos quando o chamador os conhece.

**Falta:** chamá-lo no fim do `rebuild-all.sh`. Não foi feito junto de propósito — o script é o caminho
de instalação de todo mundo, e mudá-lo no mesmo movimento em que se cria a ferramenta mistura duas
coisas que devem falhar em separado.

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

## 📂 TEMA · Infra de teste, build e observabilidade

## Três cenários e2e testam o que não existe mais (15/16/17) *(migrado da passagem de 2026-08-26)*

**15 e 16** falam com `/v1/agent-types` — **entidade APOSENTADA**; o próprio `fixtures/seed.ts:93-115`
documenta a remoção em prosa. **17** chama `POST /mcp`, rota que **não existe** (devolve HTML 404).
São testes mortos.

Apagar ou migrar é decisão a tomar — **o que não pode é ficarem vermelhos permanentes, porque
vermelho permanente ensina a ignorar a cor.** Junto: o 17 tem **três `pass()` de "skipped"**
(`:312-317`) que transformam indisponibilidade em verde, que é o defeito inverso e pior.

## Lacunas de asserção na suíte e2e *(migrado da passagem de 2026-08-26)*

**1. Sentimento não é asserido, e o caro já está pronto.** O cenário 29 produz o contato de FILA
reprodutível — a metade cara. Faltam duas asserções: `{tenant}:ctx:{sid}` →
`session.sentimento.current` medido, e o agregado `{tenant}:pool:{p}:sentiment_live`. Substitui a
reprodução MANUAL do `gate_sentiment_engine_half.sh`. ⚠️ A asserção precisa de **nome próprio** (como
o `D1`), senão "sem credencial" volta a ser indistinguível de "não mediu".

**2. O cenário 03 passa pulando o que dá nome a ele.** Imprime dois warnings de `docker: not found`
e passa mesmo assim — o kill/restart de container é pulado e o teste vira injeção de estado no
Redis. Verde honesto, mas **menor do que o nome promete**: quem lê a lista de cenários acredita que
há cobertura de recuperação de queda, e não há.

## `npx vitest` no host mede `@plughub/schemas` de 2026-08-02 *(medido 2026-08-21)*

`packages/schemas/dist/` está congelado em 2 de agosto e **não contém `mcp_server_invoke`**, valor que
o fonte (`schemas/src/audit.ts:335`) declara. Consequência imediata: duas falhas em
`invoke-audit.test.ts` que **não são defeito** — o teste valida contra o contrato de 17 dias atrás.

O modo de falha perigoso é o simétrico: uma mudança de schema pode ficar **verde** no host porque o
`dist/` velho ainda aceita a forma antiga. No container não acontece (o Dockerfile do mcp-server
roda `npm run build` do schemas antes do consumidor, linhas 16-17), então produção não é afetada —
**é o instrumento do host que está velho, não o produto**.

Decidir: `npm run build -w @plughub/schemas` como pré-passo declarado da suíte de host, ou aceitar
que a suíte de host não julga contrato e dizer isso onde ela roda.

---

## `bpm.test.ts` assere o comportamento PRÉ-endurecimento do `conversation_escalate` *(medido 2026-08-21)*

`conversation_escalate publica evento de roteamento com pool_id explícito` falha porque o tool agora
**recusa** quando `session:{id}:meta` está ausente (arco P2, 2026-08-18 — "a escalada para de inventar
tenant"). O teste não foi atualizado naquele arco e não semeia o meta.

Não é regressão: a recusa é o conserto, e ela loga o motivo inteiro. Mas uma suíte que fica vermelha
por dívida conhecida **deixa de sinalizar regressão nova** — a próxima falha real entra no meio de
três vermelhos já aceitos. Conserto: semear o meta no setup (o caminho legítimo) e acrescentar o caso
simétrico, que hoje não existe — meta ausente ⇒ recusa, com o motivo nomeado.

---

## Subida automática falhou uma vez e a causa ficou NÃO PROVADA *(achado 2026-08-12)*

`agent-assist-ui`, `skill-flow-service`, `channel-gateway` e `orchestrator-bridge` deixaram de subir
sozinhos; só ficaram de pé iniciados na mão. Três hipóteses foram medidas e **as três caíram**:
corrida de arranque sem gate de health (o `stop`+`start` subiu tudo), container velho × compose novo
(o compose não muda desde 10/08), e crash-loop por 422 de capacidade dos pools novos do commit
`32f197f` (`RestartCount 0` em todos).

A leitura que sobrou — stack montada aos pedaços (containers de 46 h a 12 h, de `build X` + `up -d X`
serviço a serviço) somada ao botão Start do Docker Desktop, que **inicia containers e não reconcilia a
stack** — explica todas as observações sem inventar mecanismo, mas **não foi testada**: um `up -d`
completo consertou o ambiente e destruiu a evidência antes que os logs fossem coletados.

**Não atacar este item especulativamente.** O que existe hoje é instrumento, não diagnóstico:
`infra/scripts/up.sh` grava `.logs/up-*.log` em toda subida, e os cinco serviços que não tinham
política ganharam `restart: on-failure` (ver CHANGELOG 2026-08-12). **Gatilho de reabertura:** a
reincidência. Quando ocorrer, a ordem é `logs` do container que falhou **antes** de qualquer conserto
— inclusive antes do `up -d` que sabidamente resolve.

Item aberto de verdade, herdado da investigação: **nada verifica que a stack em execução corresponde ao
compose.** Um container criado há dois dias roda a config de dois dias atrás, e o único sinal disso é a
coluna `CREATED` do `ps`, que ninguém lê. Um probe que compare `docker inspect` × `docker compose
config` (env e `depends_on` por serviço) transformaria deriva silenciosa em vermelho — e é o que teria
respondido esta sessão em um comando.

---

## Fixtures do e2e ainda falam AgentType — **seed base ✅ 2026-08-05; sobram 3 bolsões**

**Fechado nesta passada (saída (a), a que o item recomendava):** removido o bloco morto de
`createAgentType` do `seedBaseFixtures` e migradas **23 call sites** de `"agente_retencao_v1"` →
`"skill_retencao_oferta_v1"` em 11 arquivos (01, 02, 03, 04, 06×5, 07×6, 08×3, 09, 10, 11,
regressions×2), mais os 2 comentários que citavam o id antigo. Ver CHANGELOG.

**Três medições que corrigem o texto original abaixo — ler antes de reaproveitá-lo:**

1. **A contagem estava baixa.** O item dizia "~15 chamadas em 9 arquivos"; são **23 em 11**. Duas não
   são `agentLogin`: `06:116` e `07:198`/`07:295` são `agentJoinConference` (a tool **não** valida
   contra o registry — passa o id adiante como identidade do participante IA), e `08:59` é o campo
   `agent_type_id` de `outbound_contact_request`.
2. **O escopo do dano estava subestimado.** O item lia como "o cenário 10 está barrado". `runner.ts`
   §363 chama `seedBaseFixtures` para **todo** cenário com `needsRegistry` (todos fora de 13/14/16) —
   logo o 404 parava a suíte **inteira**, e o "cenário 10" era só o que estava na linha de comando.
3. **A migração não custou os `pools` porque eles já não existiam.** O bloco morto declarava
   `pools: ["retencao_humano"]` e `max_concurrent_sessions: 2`, mas o cliente HTTP de produção
   (`mcp-server/infra/registry-client.ts` §69-72) devolve `pools: []` e `max_concurrent_sessions: 1`
   **fixos** — config por-agente mudou para o slot de deploy do pool. `agent_ready`
   (`runtime.ts` §271-275) itera lista vazia e **não inscreve a instância em pool nenhum**.
   *Consequência que vale mais que o item*: qualquer cenário e2e que dependa de o **routing alocar**
   uma instância logada por essa via está quebrado — por uma causa anterior e independente desta.
   Ainda não medido quais são; o 10 não é um deles (semeia `session:meta` à mão).

**Os três bolsões que sobram** (nenhum bloqueia a suíte hoje):

- **`seedPerfFixtures` (cenário 05)** — 50 `createAgentType` na rota morta, E **a função não é chamada
  por ninguém**: `runner.ts` §90 importa só `seedBaseFixtures`. O cenário 05 monta `agent_perf_{i}_v1`
  a partir de fixtures que nunca foram semeadas em execução alguma. Remover o código morto e migrar o
  05 são a mesma decisão.
- **`15_instance_bootstrap.ts`** — `listAgentTypes()` (§78) é a **espinha** do cenário: ele deriva
  `instance_id` de `agent_type_id`+`max_concurrent_sessions` e confere pertencimento a pools a partir
  do registro. Não é renomear chamada; é reescrever o cenário sobre skills+slots. Por isso o método
  sobreviveu no `http-client`, agora com aviso de rota aposentada.
- **`fixtures/seed_demo.ts`** — script standalone (`ts-node fixtures/seed_demo.ts`), 2 `createAgentType`
  (`orquestrador_demo_v1`, `agente_suporte_humano_v1`). Morre no primeiro `await` desde que a entidade
  saiu. Ninguém reclamou, o que sugere que ninguém o roda.

**Um comentário que mentia foi corrigido** (`lib/http-client.ts` §68): dizia que "`/v1/agent-types`
NÃO é gateado — só pools e skills precisam da credencial". Verdadeiro e enganoso ao mesmo tempo: não é
gateado porque **não existe**. Descrever rota inexistente pelo que ela não exige sugere que funcione
sem credencial. Mesma família do §57 ("o TÍTULO é o que mente para mais gente").

<details><summary>Texto original do item (2026-08-05) — mantido para rastreio</summary>

Com o 401 resolvido, o seed avança e morre em `POST /v1/agent-types → 404 Cannot POST`
(`fixtures/seed.ts:94`). **Não é permissão: a rota não existe.** Zero ocorrências de `agent-types` em
todo o `agent-registry/src`; o `app.ts` §43-49 monta pools, skills, instances, channels,
channel-endpoints e operational, mais nada. A decisão está nomeada em
`registry_syncer.py` §293 — *"AgentType entity retired (Fase 3d/C)"* — e o mundo novo está em
`mcp-server/infra/registry-client.ts` §42: **a identidade de um agente É o `skill_id` deployado**, e
`agent_login` resolve por `GET /v1/skills/{id}`.

**A camada de fixtures ficou meio migrada, e dá para ver a costura.** O seed cria três skills (vivas)
e dois agent-types (mortos). Nos cenários, `09` e `11` já logam o avaliador como `skill_avaliacao_v1`
— e o comentário §54-59 do próprio `seed.ts` documenta essa migração, feita quando o gate de
`agent_role` entrou. A identidade do **executor** ficou para trás: `"agente_retencao_v1"` aparece em
~15 chamadas de `agentLogin` em 9 arquivos (`06`, `07`, `08`, `09`, `10`, `11`, `regressions`, mais
`01`–`04` em chamadas multi-linha não conferidas).

**Consequência**: remover só o bloco morto do seed não basta — o cenário 10 passaria a morrer no
login com `agent_type_not_found`, porque não existe skill chamada `agente_retencao_v1`.

**Duas saídas, e a barata é a errada.** (a) Migrar as identidades para `skill_id`
(`skill_retencao_oferta_v1`), removendo o bloco morto: é o conserto certo, coerente com o que `09`/`11`
já fizeram e com a convenção `skill_{slug}`, ao custo de ~15 edições. (b) Semear uma skill chamada
`agente_retencao_v1`: uma linha, zero churn — e grava um nome com forma de `agent_type` como
`skill_id`, consertando o sintoma e deixando a dívida **com aparência de resolvida**. Preferir (a).

**Antes de atacar, conferir contra o código que executa** (a lição de 2026-08-05, que rendeu 3 de 7):
este item nasceu de leitura de fonte, não de execução. Confirmar em especial se `01`–`04` usam mesmo
`agente_retencao_v1` nas chamadas multi-linha, e o que `regressions.ts` espera.

**O que isto desbloqueia:** o cenário 10 e, com ele, o único exercício vivo do leitor de `role`
(§1055 Fatia B) — a mesma cadeia que o §101 abriu.

Visto no log do mcp-server em **todo** pool do login do Console, seguido de sucesso:

```
[agent-ws] Pool registration returned HTTP 401: pool=formfill_demo
[agent-ws] Human agent registered: instance=human-bef14526-… pools=retencao_humano,aprovacao_deploy,formfill_demo status=ready
```

O registro **degrada e segue** — e loga o motivo, que é o comportamento certo pela postura de
engenharia. Mas um 401 é falha de **autenticação de serviço**, não estado normal: alguma chamada de
registro de pool está indo sem `x-service-token` (ou com um que o destino não aceita), e o caminho
que "conserta" a ausência é justamente o que impede de notar.

**Não investigado, e por isso não afirmado:** qual endpoint devolve o 401, o que ele registraria se
tivesse sucesso, e se alguma coisa depende disso mais tarde. O login funciona, o claim funciona e a
Fase E foi validada com ele presente — então não é bloqueante, e misturá-lo com o arco de requeue
seria confundir dois defeitos.

**Primeiro passo quando for atacado:** capturar a URL e o status body (`console.error` no ramo do
401 já basta), e responder *"o que este registro grava, e quem lê"*. Se a resposta for "nada que
alguém leia", o item vira remoção da chamada — não conserto do token.

</details>

> **Nota de arquivo (2026-08-05):** os últimos 5 parágrafos do texto original acima
> (`[agent-ws] Pool registration returned HTTP 401`) **não são deste item** — falam do registro de
> pool no login do Console, que é outro assunto e tem seção própria já fechada no topo do arquivo.
> Chegaram aqui por colagem. Não confundir com o 401 do seed do e2e, que era em `POST /v1/skills` e
> está resolvido.

## Seis serviços rodam SEM logging configurado — todo `logger.info` invisível *(achado 2026-08-07)*

Descoberto pelo gate da Fase C do webhook, que reprovou por não achar uma linha INFO que o serviço
**nunca emitiu**. Causa no `channel-gateway` (já corrigida): `logging.basicConfig` morava dentro de
`run()`, o entry point de `python -m`, mas o `CMD` do Dockerfile é `uvicorn …:app` — uvicorn importa o
módulo e nunca chama `run()`. Root logger no default `WARNING` ⇒ **todo `logger.info` do pacote
descartado em silêncio**, desde sempre. `logger.warning` seguia aparecendo (handler de último recurso),
o que fazia o defeito passar por "log normal".

**Não é isolado.** O `ai-gateway` já carrega um comentário descrevendo o mesmo sintoma ("todo
`logger.info` sumia"), corrigido lá e nunca varrido. Levantamento por `CMD` × presença de `basicConfig`
em qualquer `.py` do pacote — **seis serviços `uvicorn …:app` sem NENHUMA configuração de logging**:

| Serviço | Porta |
|---|---|
| `dialog-api` | 3760 |
| `scheduler-api` | 3650 |
| `mailing-api` | 3660 |
| `analytics-api` | 3500 |
| `calendar-api` | 3700 |
| `config-api` | 3600 — tem `basicConfig` só no `seed.py`, que é job à parte |

Sadios: `evaluation-api`, `pricing-api`, `ai-gateway`, `auth-api` (config no nível do módulo) e os
serviços de console-script/`python -m`, onde a função que configura **é** o entry point
(`routing-engine`, `rules-engine`, `orchestrator-bridge`, `session-replayer`, `usage-aggregator`,
`conversation-writer`, `clickhouse-consumer`).

**Conserto:** o padrão aplicado no channel-gateway — `_configure_logging()` chamado no import, nível por
`PLUGHUB_LOG_LEVEL` (default INFO), `run()` mantido chamando (no-op se o root já tem handler).
**Cuidado ao varrer:** o efeito é aumento real de volume de log em seis serviços de uma vez; fazer com
medição de volume, não no dia em que se precisa da stack quieta. **E a lição de método:** configuração
de logging presa ao entry point ERRADO é a mesma família de "fonte declarativa tem aplicador separado" —
o código está lá, correto, e não roda; o sintoma é ausência, e ausência parece "não aconteceu".

---

## Dois pacotes fósseis — `clickhouse-consumer` e `conversation-writer` *(quarentena 2026-08-03)*

Vieram do resíduo acima. Estavam INCONCLUSIVOS no `report_suite_skips.sh` desde sempre, e a
leitura fácil ("a sonda não alcança o serviço") estava errada em ambos: **não há serviço**.
Nenhum dos dois é serviço do compose, nenhum tem `Dockerfile`, e os dois só existem no
`ecosystem.config.js` — a topologia PM2 anterior ao Docker. Também não constam da §
Repository Structure do `CLAUDE.md`.

| | consome | escreve | estado medido |
|---|---|---|---|
| `clickhouse-consumer` | `evaluation.results` | `evaluation_results` (CH) | tópico **não existe** no broker e nenhum produtor no repo o escreve; auto-create desligado |
| `conversation-writer` | `conversations.*` (vivos) | `transcripts`, `transcript_messages` | tabelas **não existem** em `plughub_demo`; ninguém as lê |

Ambos ganharam `README.md` de fóssil no próprio diretório, com a evidência e o que exigiria
reativação. **Não apagados** — mesmo critério da tabela `pools` fóssil: reversível, e o erro
fica visível.

**O `conversation-writer` é o que merece atenção se alguém mexer**: ele consome tópicos
**vivos** e traz `migrate()` embutido no `postgres_writer.py`. Subi-lo não daria erro —
criaria uma segunda persistência de transcrição, paralela ao `StreamPersister`, divergente e
sem leitor. Fóssil que falha barulhento é inofensivo; este falharia em silêncio.

**Falta decidir** (não urgente, e a quarentena já tira o dano): apagar os dois pacotes junto
com as entradas do `ecosystem.config.js`, ou manter. Apagar só o pacote e deixar o PM2
apontando para um script inexistente troca um fóssil silencioso por um erro de boot.

**Terceiro caso da mesma família (2026-08-03):** o trio `skill_survey_runner_v1` /
`skill_survey_outbound_v1` / `skill_survey_trigger_v1` — existe como YAML, **nenhum pool o
deploya**, e as únicas menções fora dos próprios arquivos são exemplos em docstring. Cada um
ganhou a marca de quarentena no cabeçalho. **Armadilha registrada lá:**
`skill_survey_outbound_v1` NÃO é o outbound vivo — o que roda é o par
`skill_outbound_survey_dispatch_v1`/`_worker_v1` (fase 5b), com pool. Nomes quase idênticos,
destinos opostos; quem for apagar precisa olhar duas vezes.

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

## Arc 19 — cleanup residual de infra *(arco concluído 2026-05-28; histórico no CHANGELOG)*

**Arquivar o package `skill-flow-worker`.** Confirmado pela triagem de 2026-08-17: das quatro saídas do
worker, três são endpoints hoje 410 (`workflow-client.ts:79, 90, 104, 120`) e a quarta posta em
`${mcpServerUrl}/mcp` (`engine-runner.ts:131`), rota que **não existe** — o mcp-server expõe `/sse`
(`server.ts:1182`) e `/messages` (`:1258`). *"Conserta"* não é opção: seria reconstruir para um caminho
sendo abandonado.

⚠️ **Corrigido 2026-08-17 — NÃO remover o tópico `workflow.events` junto.** Esta seção mandava remover os
dois, e o tópico tem **dois consumidores vivos e independentes do package**: evaluation-api
(`main.py:37-124`, agendado em `:578-583`) e analytics-api (`consumer.py:332` → tabela `workflow_events`).
Removê-lo derrubaria os dois em silêncio. *(O consumer da evaluation-api é cola legada do motor de revisão
por workflow, já declarado superseded — sai por decisão própria, não de arrasto.)*

---

## Record/Replay Harness — gravação/replay em todas as costuras *(proposta — não implementado)*

Visão + spec em [`docs/product/record-replay-harness-spec.md`](docs/product/record-replay-harness-spec.md). Generaliza o Session Replayer (que hoje replaya só o stream da sessão, para avaliação) num harness "VCR" em todas as costuras (channel-gateway, AI Gateway, MCP, Kafka) — cada costura como **driver** (injeta inputs gravados) ou **mock** (devolve outputs gravados), com timings.

**Base que já existe**: `session-replayer` (persister/hydrator/replayer/comparator), `ComparisonReport` (Jaccard + deltas), `delta_ms`/`speed_factor`, Kafka como log, harness `e2e-tests`. **A construir**: captura full-fidelity de payload em MCP/AI Gateway (hoje `mcp.audit` é só metadado), clock/seed injetável (determinismo), harness multi-costura, gravação seletiva (golden/amostrada/on-demand) com masking, e o **gate de promoção** consumindo o `ComparisonReport` como critério objetivo. Aplicações: regressão determinística, repro de bug, simulação de carga, datasets de avaliação.

---

---

## 📂 TEMA · Metering e Pricing

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

## 📂 TEMA · Defeitos e follow-ups avulsos

## ai-gateway — quatro defeitos herdados, nenhum medido em runtime *(migrado da passagem de 2026-08-26)*

1. **Copilot nunca foi validado em runtime.** Dois bugs achados por LEITURA em 2026-08-23 (`system=`
   inexistente; `.text` por `.content`) e **nunca rodados**. ⚠️ Conferir PRIMEIRO de onde o copilot
   busca o provider: se for pelo caminho que o sentimento usava (`inference_engine.providers`,
   atributo que não existe — o certo é `_providers`), está morto pelo mesmo motivo, e os dois bugs
   de leitura são irrelevantes até isso ser consertado.
2. **`intent.confidence ?? 0`** — irmão exato do defeito de sentimento consertado em 08-25, em campo
   diferente: converte NÃO-MEDIDO num ponto legítimo da escala.
3. **Contador de credencial é BALDE de calendário** (`now // 86400`), e **`calls_ok` SUBCONTA** — só
   incrementa quando o `AccountSelector` escolheu CONTA, e no demo vai tudo pelo alias legado. Logo
   `calls_ok` baixo não distingue "pouco uso" de "caminho não instrumentado".
4. **`extract_context_from_response`** (`context.py`) é heurística de contagem de palavras-chave em
   português, e alimenta `intent`/`confidence`/`flags` do `/inference` — **que não tem chamador**.
   Decidir entre ligar a um chamador real ou remover; hoje é superfície que parece medir e não mede.

## Guarda `_default_` assimétrica nos 5 leitores *(migrado da passagem de 2026-08-26)*

A assimetria `!== "_default_"` × truthiness segue em `orchestrator-bridge/main.py:9180` e
`mcp-server/server.ts:2471`/`:2487`/`:2497`/`:3641`. **Hoje não há produtor de campo vazio** — o
próximo caminho que ativar agente nativo sem `instance_id` reabre o buraco. Tratar `""` como
`_default_` **e LOGAR**. 💡 Agora dá para testar: o cenário 29 exercita exatamente esse caminho.

## O `on_failure` do `responder_cliente` não fala com o cliente *(migrado da passagem de 2026-08-26)*

Ele volta a `aguardar_mensagem` **sem emitir nada**, então **falha de LLM na fila é indistinguível de
"o agente ignorou"** pelo lado do cliente. É degradação silenciosa na superfície que mais custa: o
contato em espera. O conserto mínimo é uma fala de erro honesta antes do retorno ao loop.

## Resíduos do conserto da exibição de sentimento *(2026-08-25)*

*A leitura foi consertada e tem gate (`gate_console_sentiment_source.sh`); ver CHANGELOG. O que sobra
aqui é o que a medição revelou e o conserto deliberadamente NÃO cobriu.*

- **`session:{id}:sentiment` não tem produtor** *(promessa sem produtor)*. O `CLAUDE.md` § Sentiment
  Tracking documenta esse array como o histórico por sessão, e **nenhum componente escreve a chave**.
  O emitter grava três destinos, nenhum deles este. Consequência viva: `trajectory: []` e
  `trend: null` — o gráfico da `EstadoTab` e a seta de tendência **não renderizam**. São ~15 linhas em
  `sentiment_emitter.write_context_store_sentiment` (RPUSH + LTRIM + EXPIRE) mais a leitura no helper,
  que já recebe `trajectory` como parâmetro justamente para que esse dia mude um lugar só. Alternativa
  honesta ao trabalho: **apagar a promessa do `CLAUDE.md`** — o que não pode continuar é o documento
  descrever uma chave que não existe.
  ⚠️ **Não** tentar derivar de `consolidated_turns`: o `float(… or 0.0)` de `update_partial_params` já
  achatou lá dentro, e um `0.0` medido é indistinguível de turno sem medição.

- **`intent.confidence ?? 0` é o irmão do defeito consertado.** Nas duas implementações de
  `supervisor_state`, `confidence` cai para `0` quando o `output_schema` não declara o campo — e
  `0` é "certeza nenhuma", que é uma leitura, não uma ausência. Mesma família do `?? 0` do sentimento,
  em campo diferente. Não foi tocado por ser escopo próprio: `intent` e `confidence` **são** medidos
  por outro caminho (extração do resultado do LLM), então o remédio não é o mesmo.

- **`packages/agent-assist-ui/` é um segundo Console vivo.** App legado que renderiza a mesma tela do
  módulo `agent-assist` do platform-ui e sobe como **serviço na porta 5173** do
  `docker-compose.demo.yml`, sem profile que o segure. O `CLAUDE.md` proíbe *criar* app de UI
  standalone, mas este é anterior à regra e ninguém decidiu o destino dele. Todo conserto de Console
  precisa ser feito duas vezes hoje — este foi. **Decisão pendente: aposentar ou assumir.** Enquanto
  não for tomada, o custo é silencioso e o risco é divergência entre as duas telas.

- ⚠️ **A promessa de reconexão do webchat pode não existir — contradição doc × código, MEDIDA
  em parte (2026-08-25).** O `CLAUDE.md` § WebChat afirma *"Reconnect via cursor: zero messages
  lost"*. Medido no cenário e2e 12: depois de uma queda de WS e reconexão 200 ms depois, o
  `session:{sid}:stream` está **VAZIO** e o cliente recebe **`conn.session_ended`** — a mensagem
  semeada antes da queda não é reentregue.
  ⚠️ **A primeira hipótese foi REFUTADA pela medição seguinte, e o registro fica como aviso.**
  Lendo `webchat.py:320-329` (`close_reason = "client_disconnect"` → `_close` → `contact_closed`
  com `customer_disconnect`, sem janela de graça), concluiu-se que a queda do WS encerrava o
  contato. Instrumentado o `reason` do `conn.session_ended`, veio **`session_expired`** — outra
  causa. A leitura de código estava certa sobre o que aquele caminho faz e **errada sobre ser
  ele o caminho percorrido**. *Um mecanismo plausível que explica o sintoma não é prova de que
  foi ele que agiu.*
  **O que está MEDIDO:** stream vazio + `conn.session_ended{reason:"session_expired"}` +
  `cursor_no_reauth == cursor_enviado` (o gateway não tinha nada mais novo).
  **O que NÃO está medido, e é a próxima medição:** quem apaga `session:{sid}:stream`, e quem
  emite `session_expired` (grep pelo literal, depois o produtor).
  **Não é conserto de teste, e por isso não foi feito de carona:** decidir se o webchat segura o
  contato através de uma reconexão é decisão de PRODUTO com custo próprio (por quanto tempo? o
  que acontece com AHT e com o `close_reason` da analítica?). As duas saídas honestas são
  implementar a graça **ou apagar a promessa do `CLAUDE.md`** — o que não pode continuar é a
  documentação afirmar uma garantia que o código não sustenta, que é a família do DDL de
  `participation_intervals`. O cenário 12 fica **vermelho de propósito** até a decisão.

- **`gate_sentiment_engine_half.sh` ainda depende de reprodução MANUAL.** O cenário e2e 29
  (2026-08-25) produz um contato de fila reprodutível — que é a metade cara da reprodução —, mas
  não asserta sentimento. Automatizar = acrescentar ao 29 duas asserções sobre o contato que ele
  já cria: `{tenant}:ctx:{sid}` → `session.sentimento.current` com valor medido, e o agregado
  `{tenant}:pool:{p}:sentiment_live`. ⚠️ Depende de credencial de LLM viva, então tem de ser
  asserção **com nome próprio** (como o `D1` do 29), senão "sem chave" volta a ser indistinguível
  de "não mediu" — que é o defeito que 08-24 levou cinco camadas para descascar.

- **Código morto em `platform-ui/…/ContactList.tsx`**: `sentimentColor` (linha 46) e `sentimentScore`
  (linha 142) não são usados — o "Sentiment dot" que o cabeçalho do arquivo anuncia saiu da linha em
  algum momento e as duas derivações ficaram. Inofensivo, mas é o tipo de resíduo que faz um `grep`
  futuro contar superfície que não existe.

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

## 📂 TEMA · Referência — não é trabalho a fazer

## Histórico da investigação — 4 hipóteses eliminadas em 2026-08-17 *(referência)*

**Fato, remedido em 2026-08-17 (idêntico à base da F3):** 9 segmentos com `ended_at IS NULL` em
sessões **fechadas** — `primary` 5/597 · `queue` 2/11 · `specialist` 2/68, em 676. Nove sessões
distintas, 5 dias, 6 skills, 2 canais. Gate: `infra/test/probe_open_segments_closed_sessions.sh`.

**Escopo, para não recontar errado.** O tenant tem **26** segmentos abertos, não 9: 15 são linhas de
**seed** (`dlz_s*`/`sess_epoch_*`, `participant_id` sintético, timestamp redondo `10:00:00.000`) e 2
estão em sessões ainda **abertas** (não são defeito). Toda contagem deste item filtra por sessão
fechada; um probe que não filtre mede 65% de contaminação — aconteceu.

### Onde o dano está (e onde NÃO está)

`agent_time_ms` (`reports_query.py:1354`) filtra `role IN ('primary','specialist')` — **`queue` está
fora por papel**, não por `duration_ms IS NOT NULL`. Logo:

- a pergunta que o kickoff dizia estar embutida (*"a espera em fila conta como tempo de agente?"*)
  **já está respondida pelo código: não**, e o segmento aberto de fila não muda isso. O custo da
  família A é de UI (`live`, `join` numa conferência destruída, contador de ativos mentindo);
- quem **some do `agent_time_ms`** são os 7 da família B, todos `native` e `primary`/`specialist`.

### Três formas, separadas por medição — a posição do órfão é o discriminador

| | forma | casos |
|---|---|---|
| **A** | `queue` aberto: `sac_ia` escala, o `queue-{sid}` abre 53 ms depois, o humano assume 6–13 s depois e o contato roda até o fim | `61dd213c`, `05f4bc74` |
| **B1** | o órfão é a janela de **resume**: abre 15–19 ms depois do `resumed_at`, e nada mais acontece | `04d68192`, `fb5dcfea`, `e2764d9b` |
| **B2** | o órfão é o **specialist** aberto 11–18 ms após o suspend do primary; o primary retoma minutos depois e fecha em 8–12 ms | `8a5d3ce3`, `fe7c611d` |
| **B3** | o órfão é a janela **pré-suspend** (`seq=0`) e a de resume fecha normal — espelho do B1 | `3c124d3b` |
| **B4** | sem NENHUMA linha em `session_transitions` | `7ccbbc6c` |

### Hipóteses ELIMINADAS — não refazer

1. **Timeout / retomada por prazo.** `session_transitions` mostra as 6 lacunas retomadas **por
   resposta** — `resume_expires_at` a 1 h, 1 dia ou 7 dias do `resumed_at`, inclusive nas de 7 e 10
   minutos. Ninguém foi retomado por scanner. *(Probe: `probe_family_b_suspend_resume.sh`.)*
2. **Exceção + retry do dispatcher.** Zero `[retry`, zero `[dlq]` no instante do órfão, com o log
   cobrindo a janela (13/08 17:50 → 17/08 10:53). Os 10 `Traceback` do log são todos o mesmo crash de
   boot por `kafka:29092`, sem relação. *(Probe: `probe_orphan_segment_exception.sh`.)*
3. **Empate de `ReplacingMergeTree`.** A hipótese era boa — `segments` é `RMT(ingested_at)` com
   `ingested_at DateTime` (**resolução de segundo**), e `joined`/`left` de um segmento curto caem no
   mesmo segundo. Mas o log de `e2764d9b` prova que o bridge chegou ao publish e nenhuma das duas
   tabelas tem o evento; empate explicaria perda em `segments`, não em ambas.
4. **Concorrência da mesma instância em sessões diferentes.** Teste diferencial com controle por
   instância: **3/9 órfãos** com vizinho a ±10 s contra **35,4%** dos fechados das MESMAS instâncias.
   Indistinguível — e no sentido oposto ao previsto. *(Probe: `probe_orphan_concurrency_rate.sh`.)*

### Dívidas de versionamento que a investigação expôs (reais, mesmo não sendo a causa)

- **`segments` = `ReplacingMergeTree(ingested_at)`, `ingested_at DateTime`** — versão de granularidade
  de SEGUNDO para eventos que distam milissegundos. É a mesma família do bug que deu `row_version` à
  `sessions` (ver o comentário do DDL, `clickhouse.py` §75-93: *"a última linha inserida vence" é
  premissa FALSA*). Conserto natural: `coalesce(ended_at, started_at)`.
- **`participation_intervals` = `ReplacingMergeTree()` SEM coluna de versão**, apoiada num comentário
  que promete ordenação do Kafka. Pior: `ORDER BY (tenant_id, session_id, participant_id)` — **não por
  segmento** —, então dois segmentos do mesmo participante na mesma sessão (exatamente o caso do
  resume) COLIDEM numa linha só. Ela **não serve como testemunha** por-segmento; foi usada como tal
  numa rodada desta investigação e a conclusão teve de ser retirada.

### O que sobrou provado (famílias B)

Para `e2764d9b`: o bridge **publicou** o `participant_left` (log prova execução além de `main.py:8170`
— `Native agent executed … resolved` em .641, `_close_contact_layer` em .643) e **nenhuma** das duas
tabelas tem o evento. O único trecho entre os dois pontos era cego, e foi instrumentado
(`CHANGELOG.md` 2026-08-17). Reprodução serial em `limite_processo` (pool de um dos órfãos) rodou o
ciclo completo em 17/08 e **fechou os 3 segmentos novos**, sem WARNING: o caminho funciona sozinho.

### Família A — medida em 2026-08-18: o log NOMEIA o ramo, e a cadeia óbvia foi REFUTADA

*(Gates: `infra/test/probe_family_a_queue_signal.sh` · `infra/test/probe_queue_segment_exit_paths.sh`.)*

O log do routing-engine (janela 12/08 → 18/08, cobre os dois casos) traz a MESMA linha nas duas órfãs:

```
Queue drain: re-routing session=… to pool=retencao_humano
             (agent=human-… became ready, no queue agent active)
```

É o ramo **ELSE** de `kafka_listener.py:707`: o marcador `queue:agent_active:{sid}` não existia, então
o drain re-publicou em vez de dar `LPUSH __agent_available__`. A cadeia que isso sugere — sem LPUSH, o
`menu timeout_s:0` do agente de fila não destrava, `activate_native_agent` (bridge `:5546`) não
retorna, e o `participant_left` de `:5575` nunca é PRODUZIDO — é **plausível e insuficiente**:

- **`fa2c7cfb` tem o mesmo ramo ELSE e o segmento FECHOU** (em 3 ms). O ramo não é o discriminador.

**O que o inventário dos 16 segmentos `queue` mostra** (o `outcome` separa as populações):

| população | pools | outcome | fecha? |
|---|---|---|---|
| 12 casos | `formfill_demo_ia`, `limite_processo`, `aprovacao_credito`, `limite_ia` | `handoff` | **12/12 fecham** (92 ms → 296 s) |
| 4 casos | **`retencao_humano`** | **∅ (NULL)** | 2 fecham em **3 ms / 6 ms**, 2 **nunca** |

Duas leituras que a tabela impõe:

1. **No `retencao_humano` o agente de fila NUNCA completa** — 0 de 4 chegam a `handoff`. Onde ele
   funciona (12/12), o pool é de workflow. Isto é fato de POOL, não de corrida, e a comparação de
   `queue_config` entre os dois grupos ainda **não foi feita**.
2. O discriminador refinado é **`activate_native_agent` bloqueou ou voltou na hora**: 3 ms/6 ms com
   `outcome` NULL é retorno imediato sem resultado (falha rápida) — e aí o `participant_left` sai
   normal. Os órfãos são os casos em que ele de fato bloqueou.
3. **`signalled queue agent` = 0 no log INTEIRO** (contra 3 do ramo ELSE): o caminho do sinal **nunca
   rodou** neste ambiente. Não é um ramo raro — é um ramo morto.

### A contradição que sobrou — é ali que o defeito está

O marcador é escrito em `main.py:5504` **antes** do `participant_joined` (`:5527`) e apagado só em
`:5593`, **depois** do `participant_left` (`:5575`). Nos órfãos o `left` nunca saiu ⇒ o `delete` nunca
rodou ⇒ o marcador **deveria estar lá** 12 s depois, quando o drain o consultou. Não estava.
Uma das duas premissas é falsa. Candidatos, **nenhum medido**:

- (a) o `SET` levantou exceção — o `except` loga WARNING, e o log do bridge **cobre 25 segundos**
  (17/08 18:18:30 → 18:18:55), então a ausência não é evidência;
- (b) há um apagador do marcador fora dos 4 pontos conhecidos (`grep` do repositório inteiro só acha
  set/delete/exists no bridge e dois `get` no routing);
- (c) o bridge morreu entre o `SET` e o drain — mas isso não apaga chave com TTL de 4 h.

### A causa raiz, medida no mesmo dia — `queue_config.skill_id` é decorativo

A comparação de config entre os dois grupos (grátis, feita antes de instrumentar qualquer coisa)
respondeu quase tudo:

- **`retencao_humano` é o ÚNICO dos cinco com `queue_config`** (`{skill_id: skill_fila_v1,
  max_wait_s: 1800, agent_type_id: ""}`); os outros quatro têm `null` e entram pelo default de tenant.
- `skill_fila_v1` **existe**, `published`, com `flow` — a referência não está pendurada.
- **`retencao_humano` não tem slot nenhum**: `previous`/`current`/`next` todos `set:false`.
- `ALLOW_LIVE_FLOW_FALLBACK` **não está no ambiente do bridge**.

Cruzando com `resolve_flow_for_agent` (`main.py:494-497`): produção = **snapshot do slot `current` do
POOL**, e `_activate_queue_agent` passa o **pool de destino** como `pool_id`. Logo o
`queue_config.skill_id` **nunca é consultado** — quem decide o que o "agente de fila" executa é o
deploy do pool onde o cliente espera. Isso fecha as duas metades da tabela sem hipótese extra:

- pool humano **sem slot** → `resolve` devolve `None` → `activate_native_agent` devolve `{}` na hora →
  `left` com `outcome` NULL: **os casos de 3 ms e 6 ms**;
- pools de workflow **com slot** → rodou o flow do PRÓPRIO pool sob `role='queue'`: **os 12 `handoff`**
  não são agente de fila nenhum.

**Dois defeitos, de níveis diferentes:**

1. **`queue_config` não executa nada** (config visível na UI, sem efeito) — e o modo de falha é
   sucesso aparente: o segmento de fila aparece no relatório de Fila/SLA como se tivesse havido espera
   atendida. *(Toca `docs/arcos/queue-attended-model.md` — o relatório de fila mede um agente que,
   neste pool, nunca rodou.)*
2. **A ordem no bridge**: marcador (`:5504`) e `participant_joined` (`:5527`) são escritos **antes** de
   qualquer tentativa de resolver o flow (`:5546`). O segmento de fila nasce mesmo quando o agente não
   pode rodar — daí segmento `queue` de 3 ms que não enfileirou ninguém.

### CAUSA RAIZ — `conversations.participants` publicado SEM CHAVE em tópico de 3 partições

Reproduzido ao vivo em 2026-08-18 (sessão `dce98532`, com a instrumentação de marcador+TTL no ar). O
que a reprodução desfez, antes de nomear o defeito:

- o marcador **foi** escrito e **foi** apagado pelo dono 7 s antes do drain — o `ttl=-2` era honesto.
  **O "fio aberto" da versão anterior desta seção não existia**;
- o `participant_left` **está no tópico Kafka** (`probe_participant_event_in_kafka.sh`:
  `queue-dce98532… joined=1 left=1`, com `sac_ia-001 joined=1 left=1` de testemunha) e **não está em
  nenhuma das duas tabelas**.

`main.py:3232` publica `send_and_wait(TOPIC_PARTICIPANTS, payload)` — **sem `key`** — e o tópico é
criado com **`--partitions 3`** (`docker-compose.demo.yml:533`). Sem chave o particionador espalha, e
**ordem no Kafka é por partição**: o `joined` e o `left` do mesmo segmento podem cair em partições
diferentes e ser inseridos fora de ordem. Quando o `left` entra primeiro, as DUAS tabelas perdem pelo
mesmo motivo — `segments` é `RMT(ingested_at)` e o `joined` inserido depois vence; `participation_intervals`
é `RMT()` **sem coluna de versão** e o último inserido vence. Sem erro em lugar nenhum.

O DDL de `participation_intervals` (`clickhouse.py:350`) **afirma a premissa falsa em prosa**:
*"The 'left' event is always inserted after 'joined' (Kafka ordering)"*. Nunca houve chave; a premissa
nunca valeu. Explica a intermitência (2 de 4 no pool, ~1,3% no tenant), a sobre-representação de
segmentos **curtos** (3 ms entre os dois eventos = maior chance de inversão) e a família B, onde o log
provava publicação e nenhuma tabela tinha o evento.

**Conserto em DUAS partes — uma só não basta:**

1. `key=session_id` no publish (ordenação por segmento — necessária, não suficiente);
2. coluna de versão que **discrimine**: com a ordem garantida, `RMT(ingested_at)` em resolução de
   SEGUNDO ainda empata para eventos que distam milissegundos, e empate em RMT não tem vencedor
   definido. `participation_intervals` sequer tem versão.

**Gate:** `probe_open_segments_closed_sessions.sh` antes/depois (base `primary` 5 · `queue` 2 ·
`specialist` 2), com a testemunha obrigatória — os que já fecham têm de continuar fechando pelo caminho
deles. **Não repara o passado**: os eventos seguem no tópico, então reprocessar é possível; decisão à
parte.

*(Instrumentação que sobreviveu e fica: `marker SET`/`marker DELETE deleted=N` em INFO no bridge e o
TTL da chave no ramo ELSE do drain — foi ela que separou "o marcador sumiu" de "o marcador foi apagado
por quem devia".)*

---


## Cobertura de credencial por ROTA — o TERCEIRO eixo de autorização ✅ FECHADO 2026-08-29

Descoberto em 2026-08-28 (achado colateral da T3 do `adr-relatorios-duas-superficies-e-lentes.md`),
fechado em 2026-08-29. Histórico em `CHANGELOG.md`; a regra e o estado ficam no `CLAUDE.md`
§ Security. Gate: `infra/test/probe_route_credential_coverage.sh` (A: censo AST · B: ao vivo).

**O recorte do achado não era o do eixo.** O TODO original falava de "12 das 38 rotas
`/reports/*`". O censo mediu **19 descobertas em 73**, e as sete fora daquele prefixo
carregavam a pior: `GET /sessions/{id}/stream` servia a **transcrição inteira do contato**
sem credencial — enquanto a rota irmã `/v1/transcript/sessions/{id}`, feita para servir esse
mesmo dado, já exigia. Resultado: 18 gateadas, 1 isenta nomeada (`/v1/health`).

O que sobrou está abaixo, contado. Nenhum destes é "por enquanto".

---

### Recorte de linha nas rotas recém-gateadas (dívida contada, aberta)

**As doze rotas de `reports.py` exigem credencial e NÃO recortam linha.** Um operador
escopado a um pool, autenticado, lê estes agregados inteiros. É estritamente melhor que o
anônimo lê-los inteiros, e estritamente pior que o alvo.

A causa é mecânica, não esquecimento: as `query_*` que as servem **não aceitam
`accessible_pools`**. Fabricar o filtro por rota exigiria decidir, uma a uma, qual coluna é
"o pool desta agregação" — e o precedente está medido: a F2 do ADR de relatórios encontrou
um filtro de canal que não filtrava, **esvaziava** (subconsulta que o ClickHouse recusava,
`except` devolvendo `data_unavailable`, 200 com zero linha, 683 testes verdes).

| Rota | Query | Critério que falta decidir |
|---|---|---|
| `/reports/usage` | `query_usage_report` | `usage_events` não tem pool; a chave seria `session_id`→`sessions` |
| `/reports/workflows` | `query_workflows_report` | idem, via `instance_id` |
| `/reports/campaigns` | `query_campaigns_report` | idem, via `collect_token` |
| `/reports/evaluations` · `/summary` · `/quality` | `query_evaluations_*` | join a `segments` (o pool que ATENDEU) × `sessions` (o de entrada) — é a D10 |
| `/reports/customers/{id}/360` | `query_customer_360` | tem `sess_conds` compartilhado; `_session_scope_clause` serviria, mas exige alias no `FROM` de 3 queries |
| `/reports/agent-events/*` | `query_agent_events_*` | `category_l1` é o `pool_id` por convenção do Arc 12 — convenção não é coluna |
| `/reports/evaluator-calibration` | `query_evaluator_calibration` | eixo é o AVALIADOR, não o pool; pode ser que a resposta certa seja "não recorta" |

**Exceção já feita:** `/sessions/active` recorta (403 `pool_scope_denied`), porque o chamador
**nomeia** o pool — teste de pertinência à lista do token, não predicado de coluna.

O `/reports/customers/{id}/360` é o mais urgente: é dado de CLIENTE, e a linha da tabela
diz que o predicado já existe.

---

### Escopo por SESSÃO na leitura de um contato (dívida contada, aberta)

Vale para **duas** rotas, e é uma dívida só: `GET /sessions/{id}/stream` e
`GET /v1/transcript/sessions/{id}`. As duas exigem credencial; nenhuma confere se **aquela
sessão** pertence ao escopo do chamador. Quem tem qualquer token do tenant lê qualquer
contato.

O padrão existe (`resolve_live_session_pools` + `_assert_session_in_scope`, em
`supervisor.py`), mas só decide sessão **VIVA** — e metade do tráfego destas rotas é sessão
FECHADA servida do ClickHouse, onde ele devolve conjunto vazio, que quem chama tem de ler
como recusa. Aplicar só à metade viva trocaria um buraco por um buraco **intermitente**, que
é pior de diagnosticar.

O que falta decidir antes de implementar: o predicado da sessão fechada. Candidato natural é
`_session_scope_clause` (entrou por pool meu ∪ pool meu atendeu), que é o mesmo que os
relatórios já usam — mas ele é SQL sobre `sessions`, e estas rotas leem Redis primeiro.

---

### Achados colaterais, medidos ao fechar o eixo (não consertados)

Nenhum é do eixo de credencial; os três apareceram porque o conserto passou por perto.

1. **`useCampaignReport` chama rota que não existe.** `evaluation-hooks.ts:829` faz
   `GET /v1/evaluation/reports/campaigns/{id}` → **404** (verificado ao vivo). Consumido por
   `CampaignsPage` e `ReportsPage`, que mostram "sem relatório" desde sempre. O `.then(r =>
   r.ok ? r.json() : null)` engole o 404. Consertar exige decidir **qual** agregado a tela
   quer (`{total, completed, pending, in_review}` não é o que
   `/reports/campaigns` da analytics devolve) — produto, não plumbing.

2. **A aba Consumption do Billing lê o campo errado.** `/reports/usage` devolve
   `{data, meta}` com 20 linhas de EVENTOS; `BillingPage.tsx:105` lê `data.rows` (undefined)
   e o tipo que espera é `{dimension, total}` — AGREGADO. Duas discordâncias, e a tela diz
   "No consumption data available" com o endpoint respondendo 200. Pré-existente ao gate
   (o gate só provou que a credencial chega). Conserto = decidir se agrega no servidor ou
   no cliente.

3. ~~**`mcp-server-plughub` não compila.**~~ ⚠️ **VENCIDO — medido `TSC_OK` em 2026-08-30**
   (o conserto `segment_id: null` está em `usage-emitter.ts:82`, e o container BATE com o repo —
   sem bind mount, imagem reconstruída). O registro abaixo fica pelo modo de falha que descreve.
   Texto original: `src/lib/usage-emitter.ts:73` e `:102` — dois
   `TS2345` em `EmitParams`. Consequência operacional: `docker compose up -d --build
   platform-ui` **falha**, porque o platform-ui tem `depends_on: mcp-server-plughub` e o
   compose constrói a dependência. O contorno é `build platform-ui` + `up -d --no-deps
   platform-ui`. Não tocado (é do arco de metering).

---

## `/v1/evaluation/survey/responses` não confere `evaluation.report` (medido 2026-08-29, na F4)

**O docstring do handler afirma o gate que ele não tem.**
`router.py:list_survey_responses` diz *"S8 — navegador de respostas. Verbatim incluído
(**gate = acesso ao módulo evaluation**, postura LGPD)"*. O corpo só faz pool-scoping
(`_scope_from_claims(jwt_payload, "transcript")`). Não há checagem de
`module_config.evaluation.report` em lugar nenhum da rota.

Prosa prometendo invariante sem mecanismo — a família do DDL de
`participation_intervals` e do docstring de `channel-gateway/auth.py`.

**O gate real era o MENU.** `/analise/surveys` tinha `abac: { module: 'evaluation',
field: 'report' }` no `Sidebar.tsx`. Gate de navegação, não fronteira: `curl` com
qualquer JWT do tenant sempre passou.

**Exposição × dano, medidos separadamente** (a lição da D14.1 — são duas grandezas):

| | |
|---|---|
| exposição estrutural | real: a rota devolve verbatim a qualquer JWT válido cujo domínio de pool alcance as respostas |
| população que alcança `nps_ia` (onde estão as 51 respostas) sem o grant | **4 de 6 usuários** |
| desses, quem é usuário de negócio | **zero** — `navprobe@`, `useradmin@` e `probe@` são fixtures dos probes de segurança; o quarto é `admin@` |
| dano hoje | nenhum |

**A F4 não alargou nem consertou.** Ao absorver a lista como o nível de RESPOSTAS da Voz
do Cliente, o gate de navegação veio junto: o toggle "Respostas" só aparece para quem
tem `evaluation.report` (`CustomerVoicePage`, via `passesAbacRule` — a mesma casa que o
`Sidebar` usa). Isso mantém a F4 neutra; a rota continua como estava.

**Por que não foi consertado aqui:** é fronteira de outro serviço (evaluation-api), e
fechá-la é mudança de autorização com raio próprio — pertence ao arco de authz, junto
com o eixo de cobertura de rota, não a um refactor de relatórios. O caminho, quando for
feito, é o do passo 6 daquele arco: contar a população antes (feito acima), aplicar
`abac_can(payload, "evaluation", "report")` do `plughub_authz`, e escrever o caso que
prova que alguém COM o grant passa — o negativo sozinho passa pelo motivo errado.

⚠️ **Não confundir com o eixo de COBERTURA DE ROTA.** Aquele conta rotas que não pedem
credencial nenhuma; esta pede (o JWT é lido, o escopo de pool vale). O que falta é o
segundo eixo — *quais FUNÇÕES o portador pode exercer* —, e é por isso que o censo do
`probe_authz_single_verifier` não a acusa: ela não decodifica JWT por conta própria nem
resolve escopo por conta própria. Terceiro caso da mesma regra: um censo desenhado para
um eixo não prova nada sobre o vizinho.
