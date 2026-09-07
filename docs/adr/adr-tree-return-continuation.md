# ADR — A folha DEVOLVE: continuação por retorno na árvore de navegação

**Status:** proposto · 2026-09-06
**Contexto:** fecha o beco em que a CTR-04 parou; endereça a CTR-03 e reordena o que vem depois
**Antecede:** [`adr-orchestrator-specialist-contract.md`](adr-orchestrator-specialist-contract.md)
(emenda a D1) · [`adr-orchestrator-tree-navigation.md`](adr-orchestrator-tree-navigation.md)
(estende a D2/D10)

---

## O problema, e ele foi encontrado por medição, não por projeto

A CTR-04 tinha duas metades. A primeira (o retorno dos executores) foi entregue. A segunda — *"os
menus de demanda sobem para o orquestrador"* — **não pôde ser feita**, e a razão não era esforço:

```
demo_ia (orquestrador)  →  escalate  →  sac_ia
                           ^^^^^^^^
                           TERMINAL — o orquestrador SAI da sessão
```

Quando o `sac_ia` chega em *"posso ajudar com mais alguma coisa?"*, **não há mais orquestrador na
sessão para receber essa pergunta**. Apagar o `menu_resolucao` não moveria a pergunta para cima: ela
deixaria de ser feita, e o cliente perderia a opção de pedir um especialista depois de resolvido.
Isso é regressão de produto, não refatoração.

O diagnóstico do dono nomeia a causa: **a execução de agente está sendo tratada como etapa final, e
não deveria ser.** A folha não é o fim do fluxo.

---

## O que já estava medido quando este ADR foi escrito

| fato | medição |
|---|---|
| `delegate` mantém o chamador como `primary` e o alvo entra como `specialist` | `dialog_runner` **24 segmentos, 100 % `specialist`**; `limite_ia` **49, 100 % `primary`** |
| o chamador **retoma** | `limite_ia primary seq=0` → `dialog_runner specialist seq=0` → `limite_ia primary seq=1` |
| a instância do chamador é **devolvida** durante a delegação | os dois segmentos do chamador têm `duration_ms` preenchido — fecham e reabrem, não é um contínuo |
| 4 dos 6 destinos de navegação já devolvem o controle | CTR-04 metade do retorno; `probe_orchestrator_delegability.sh` foi de **0 → 4** |
| `QuestionNode` **já tem `id`** | `packages/schemas/src/dialog.ts:394` |
| "bloco" é **projeção**, não entidade | `buildRender` agrupa statements em `before`/`after` pela primeira question — não há id de bloco |
| a licença de IA **não** é liberada na suspensão | `{t}:admission:kind:ai` é SET de `session_id`; solta no fechamento ou na migração para pool humano |

---

## Decisões

### D1 — A folha não é o fim do fluxo; execução de agente é ETAPA

Chamar um agente é uma etapa do atendimento, com resultado, e não o desfecho dele. **Folha de
verdade passa a ser apenas `escalate` e `complete`** — os comandos que genuinamente encerram o
contato. Nenhum agente é terminal por natureza.

⚠️ Isto **não** cria capacidade nova: o `delegate` já suspende o chamador e o devolve à sessão no
retorno, e isso está medido em 24 delegações vivas. O que muda é a árvore passar a declarar **para
onde continuar**.

### D2 — O ponto de continuação é um PONTEIRO, nunca um switch

A folha ganha **um campo**: `on_return: <question_id>`. Ele nomeia a question que o **chamador**
executa quando o agente devolve o controle.

**Por que ponteiro e não switch** — e esta é a decisão que torna o desenho barato. A alternativa
avaliada (um nó `output`, ramificando sobre o código de retorno do agente) colidia com um invariante
escrito no próprio schema:

> *"`nodes` order IS the flow — there is deliberately no conditional `next` (branching = control,
> owned by the calling skill)."* — `DialogFormSchema`

Um switch por código de retorno **é** branching no JSON: exigiria emendar aquele invariante e
defender a emenda. Um ponteiro é **aresta única** — não ramifica, e por isso não emenda nada. A
economia não é de linhas; é de não mexer numa regra que protege o `DialogForm` de virar linguagem.

⚠️ **O preço, declarado:** sem switch, *"resolvi"* e *"não consegui"* apontam para a MESMA
continuação. A discriminação por resultado passa a viver no AGENTE (D6), não na árvore — ele escala
quando falha e devolve quando resolve. É consistente, e é consequência direta desta escolha.

> ✅ **CORREÇÃO medida em 2026-09-07 — o preço acima é MENOR do que este parágrafo afirma, e a
> diferença importa.** *"Resolvi"* e *"não consegui"* **não** apontam para a mesma continuação: o
> step `delegate` tem TRÊS arestas (`on_resume` · `on_reject` · `on_timeout`) e os quatro executores
> já as usam — `decision: "input"` para resolvido, `"rejected"` para *precisa humano* /
> *identidade não verificada*, `"timeout"` para prazo. No orquestrador, `on_reject` e `on_timeout`
> vão a `escalar_humano`; **só o sucesso alcança a question de continuação**.
>
> Ou seja: o switch que a D2 recusou **no JSON** já existe **no YAML**, que é exatamente onde o
> invariante do `DialogFormSchema` diz que controle mora. A escolha da D2 não pagou o preço que ela
> declarou — ela o transferiu para a casa certa, e a casa certa já estava ocupada.
>
> **O que sobra de real** (medido nos retornos publicados, todos no ramo do SUCESSO): `auth_sac_ia`
> devolve `payload.proximo: "sac"` — proposta explícita — e ninguém a lê; e `devolver_encerrado`
> manda `motivo: cliente_encerrou` com `decision: "input"`, então a continuação pergunta *"mais
> alguma coisa?"* a quem acabou de dizer que terminou. Os dois são do `auth_sac_ia`, que tem **zero
> segmentos** na história — exposição sem dano. Registrados como `RET-10`.

### D3 — Questions identificadas, `main` é a entrada

O form de navegação passa a declarar N questions no topo. A executada quando o form é chamado é
sempre a de **`id: "main"`**; as demais existem para serem alvo de `on_return`.

⚠️ **Isto é CONVENÇÃO, não campo novo** — `QuestionNode.id` já existe. O schema só ganha o campo da
D2.

### D4 — O alvo do ponteiro é a QUESTION, e o bloco vem junto

Apontar para a question **é** apontar para o bloco: bloco é projeção ancorada nela (`before` +
question + `after`), e a question é a única das duas que tem id. Assim a continuação pode abrir com
mensagem antes do menu (*"Reembolso registrado. Posso ajudar em mais alguma coisa?"*) sem entidade
nova e sem segundo campo.

⚠️ **Consequência real:** `buildRender` hoje assume **uma** question por form (o `before`/`after` é
decidido por `seenQuestion`). Ele passa a receber *a partir de qual question* montar — e a mesma
função serve o `form_get` e a preview do editor, então as duas superfícies mudam juntas.

### D5 — A continuação é RAIZ PRÓPRIA no `category_path`

As opções de uma question de continuação geram caminhos `pos_sac.especialista`, **não**
`sac.info_plano.pos_sac.especialista`.

Duas razões, e nenhuma é estética:

- **a série fica legível** — separa *"o que o cliente pediu ao entrar"* de *"o que pediu depois de
  ser atendido"*. Isso entrega a **D4 do ADR do contrato** (tag de origem da demanda) **por
  topologia**, sem campo novo;
- **não come o teto** — `AGENT_EVENT_CATEGORY_MAX_SEGMENTS` é 8, e aninhar gastaria segmentos numa
  hierarquia que não descreve hierarquia nenhuma.

⚠️ **Consequência:** `navigation_pools` ganha entradas para os prefixos de continuação. O casamento
por prefixo de segmento já cobre isso sem código novo, mas a **config** precisa existir — e o
`pool_route_resolve` **não tem default** (por decisão), então um prefixo de continuação sem entrada
recusa alto em vez de despachar para o lugar errado.

### D6 — A titularidade é PARCIAL, e é o ERRO que a torna inevitável

O agente chamado **pode** encerrar o contato (`escalate` ou `complete`). Quando o faz, o contato
termina e o orquestrador perde a titularidade.

Isto **emenda a D1 do ADR do contrato** (*"o orquestrador é dono do contato"*), e a emenda precisa
estar escrita aqui, senão daqui a três meses alguém lê aquela decisão e trata o agente que escala
como defeito.

⚠️ **Por que não é concessão, e sim a única leitura honesta:** mesmo que se quisesse um modelo em que
o chamado *nunca* termina o contato, **a finalização por erro existe de qualquer forma** — falha de
MCP, pool de destino indisponível, exceção não tratada. Um desenho que ignorasse isso seria falso no
primeiro incidente. Trata-se o caminho, não se deseja que ele não exista.

**Regra de quando devolver × quando terminar:** se o fluxo do agente **define** `escalate`/`complete`
para aquele desfecho, ele termina; se **não define nada**, devolve ao chamador ao fim da execução.

⚠️ **O que a CTR-04 já implementou continua valendo, e é mais estreito que isto:** os 4 executores
escalam quando **não há chamador** (porta própria de canal) e **propõem** (`motivo:
precisa_humano`) quando há. A D6 permite o outro regime; não o exige.

### D7 — Chamado que termina EXIGE cancelamento do delegate pendente

Decorre da D6 e **não é opcional**. Se o agente encerra sem devolver, o token nunca é consumido: o
`pipeline_state` do chamador fica suspenso no Redis, o `resume_token` fica no hash, e o timeout
scanner vai tentar retomar **uma sessão que já fechou**.

O segmento não é o problema (o chamador já fechou o dele ao delegar — medido). O problema é estado
morto com um scanner ativo em cima, e o sintoma seria erro periódico no log, no melhor caso.

**Mecanismo:** no fechamento do contato, invalidar os `resume_tokens` daquela sessão.

### D8 — O teto do ciclo é CONTADOR, nunca tempo

`folha → question → folha → question` é ciclo legítimo para o `validateFlow` (passa por step
bloqueante). Mas contato que nunca fecha segura licença.

⚠️ **O teto não pode ser tempo:** `session_timeout` é por **inatividade**, e um ciclo ativo nunca
bate nele. É contador de iterações, na família do `max_iterations` do `receive`.

### D9 — Sobre a licença: o ciclo não muda o modelo, muda a duração

Registrado aqui para não ser re-derivado. São **duas moedas**, e elas se comportam de forma oposta
na delegação:

| moeda | na delegação |
|---|---|
| vaga de atendimento (instância, semáforo do RECURSO) | **liberada** — o chamador fecha o segmento e é realocado no retorno, possivelmente noutra instância |
| licença de IA (`{t}:admission:kind:ai` × `quota:capacity:ai_agent`) | **retida** — é SET de `session_id`, e a sessão continua aberta |

⚠️ **Não é o contexto que prende a licença — é a sessão estar aberta.** Passar contexto no chamado
não a libera, e nem precisa: o contexto **já** viaja pelo ContextStore (que é por sessão) e a
instância **já** é fungível. O modelo já é stateless quanto à instância.

Consequência para o ciclo: **ele não cria custo de licença novo** — permite que o contato dure mais,
e um contato longo já custava o mesmo (um cliente que faz muitas perguntas retém a licença igual). O
ciclo muda a distribuição de duração, não o licenciamento. É por isso que a D8 é contador de
iterações e não teto de custo.

---

## Alternativas recusadas

**Nó `output` — switch sobre o código de retorno do agente.** Era mais expressivo (discriminava
`resolved` × `failed` na topologia) e resolvia o menu contextual com precisão. Recusado por **emendar
o invariante do `DialogForm`** (branching no JSON) e por custo: mexeria em `leafPaths`, `is_tree`,
`flatten_to_sections`, no achatamento em canal e no gate da árvore — porque a folha ganharia filhos.
Com o ponteiro, **a folha continua folha** e nada disso se move. O que se perde está declarado na D2
e é compensado pela D6.

**Menu de continuação no YAML do orquestrador.** Devolveria o conteúdo a uma segunda casa, contra a
D2 do ADR da árvore — e o editor de formulário deixaria de ser a fonte do que o cliente lê.

**Volta ao menu raiz, fixa.** Simples e sem conteúdo novo, mas perde o contexto: quem acabou de
registrar um reembolso veria o mesmo menu de quem acabou de entrar.

---

## Fases

| fase | entrega | depende de |
|---|---|---|
| **R1** | `on_return` no `DialogOptionSchema` + convenção `main` + `buildRender` a partir de uma question | — |
| **R2** | `delegate` no orquestrador com verbo **derivado por destino** (CTR-03), lendo `on_return` no retorno | R1, CTR-04 ✅ |
| **R3** | Cancelamento de `resume_token` no fechamento do contato (D7) | R2 |
| **R4** | Teto por contador de iterações (D8) | R2 |
| **R5** | Migrar `menu_resolucao` e `menu_continuar` para questions de continuação; `navigation_pools` ganha os prefixos (D5) | R2 |

> **Por que a R2 precisa do verbo DERIVADO, e não de um `delegate` único:** 2 dos 6 destinos não são
> delegáveis — `portabilidade_ia` (`nao_retorna` desde a CTR-06; era `cadeia_delegate`) e
> `retencao_humano` (`sem_deploy`). Um
> `delegate` para `$.pipeline_state.rota.pool` penduraria o contato de portabilidade. O critério já
> existe e é derivado do artefato: `probe_orchestrator_delegability.sh`.

---

## Riscos e questões abertas

- ~~**`portabilidade_ia` vira folha POR DEFEITO, não por desenho.**~~ ✅ **A CAUSA caiu em
  2026-09-07 (CTR-06)**, e o resíduo mudou de natureza. O token do chamador é fato da ARESTA e vivia
  numa tag ÚNICA da sessão; hoje o engine o captura no nascimento do pipeline (isolado por segmento)
  e o restaura na retomada, então delegar por dentro **não** sobrescreve mais o token de quem chamou.
  `portabilidade_ia` continua não-delegável, mas pelo motivo VERDADEIRO — `nao_retorna`, porque ele
  ainda não devolve o controle. Isso é a metade restante da CTR-04, não um limite do modelo.
  ⚠️ **Tirar o `cadeia_delegate` sozinho teria aberto um buraco:** `nao_retorna` perguntava *"existe
  um step com `tool: workflow_resume`?"* — proposição ADJACENTE. `agente_portabilidade_intake_v1`
  invoca a tool **cinco vezes** e nenhuma delas retoma o chamador (retoma um `suspend` PRÓPRIO); ele
  só era barrado porque o outro critério vinha antes. O critério agora afere o TOKEN.
- **O eixo de demanda vira N por contato.** Deixa de ser uma marca. `branch_marks` × `branch_contacts`
  sai de curiosidade e vira leitura obrigatória — a árvore já sabe contar as duas, os consumidores
  ainda leem como se fosse uma. A D5 mitiga (separa entrada de continuação), não resolve.
- **Hooks de fim de contato.** `sac_ia` tem `on_contact_end` com NPS. Se o orquestrador retomar, o
  NPS do especialista dispara **no meio** do contato. Quem fecha o contato — e quando — continua
  aberto, e este ADR não o resolve.
- **`buildRender` serve duas superfícies.** A mudança da D4 atinge o `form_get` e a preview do
  editor pela mesma função; a preview é o VEREDICTO do servidor, então uma divergência ali aparece
  como *"não verificado"* e não como erro.
- **Dois dos quatro executores nunca rodaram.** `auth_sac_ia` e `reembolso_ia` têm **zero
  segmentos**: o retorno neles está validado estruturalmente e no snapshot promovido, mas nunca foi
  exercido com cliente do outro lado. O primeiro contato real por esses caminhos é o primeiro teste.
