# ADR — Navegação de orquestrador como ÁRVORE de `DialogForm`

**Status:** proposto · **Data:** 2026-09-05 · **Depende de:** `adr-dialog-tree-options` (implementado),
`adr-agent-flow-single-authored-level` (proposto), `adr-dialog-primitive` / `adr-otp-workflow-and-dialog-primitive`

> **Tese em uma frase:** o menu de um orquestrador é a **mesma estrutura** que a taxonomia de wrap-up —
> pastas navegam, folhas endereçam serviço —, e por isso o runner genérico de `DialogForm` pode fazer
> navegação sem virar linguagem, sem LLM, e produzindo o eixo de **demanda** que hoje não existe.

---

## Contexto — o que foi MEDIDO antes de decidir (F0, 2026-09-05)

### 1. O orquestrador já existe, escrito à mão

`agente_triagem_v2.yaml` gasta **15 steps** para expressar uma tabela de 5 entradas:

| tipo de step | quantidade |
|---|---|
| `notify` | 6 |
| `escalate` | 5 |
| `complete` | 2 |
| `menu` | 1 |
| `choice` (5 condições) | 1 |

Acrescentar um serviço custa 3 steps, mais uma condição, edição de YAML, `set-next` e `promote`.
O conteúdo (os rótulos que o cliente lê) e o controle (para onde ir) moram no mesmo artefato.

### 2. Os menus são de DUAS espécies, e só uma é navegação

Levantamento dos 17 skills com `menu`, com foco nos cinco do caminho de atendimento:

| espécie | onde | exemplos |
|---|---|---|
| **A — navegação** (*para onde ir*) | `agente_triagem_v2`, `skill_atendimento_sac_v1`, `skill_atendimento_auth_v1` | sac · portabilidade · reembolso · "falar com especialista" |
| **B — coleta** (*o serviço fazendo o trabalho*) | `agente_reembolso_intake_v1`, `agente_portabilidade_intake_v1` | motivo do reembolso · operadora de destino · nº do pedido |

A distinção não é estilística: absorver a espécie **B** faria o orquestrador coletar dado do
especialista **antes de o especialista existir** — a regra de escopo (*"never store a narrower-scope
fact in a wider-scope field"*) — e multiplicaria a profundidade da árvore
(operadora × motivo × …), que é justamente o recurso escasso (ver D4).

### 3. O eixo de DEMANDA não tem produtor — zero, medido

```
agente_triagem_v2                eventos de negocio: 0
skill_atendimento_sac_v1         eventos de negocio: 0
skill_atendimento_auth_v1        eventos de negocio: 0
agente_reembolso_intake_v1       eventos de negocio: 0
agente_portabilidade_intake_v1   eventos de negocio: 0
```

Nenhum emite `agent_event` nem `segment_outcome_record`. **Toda resposta de menu que o cliente dá hoje
é descartada** — vive no `pipeline_state` e morre com a sessão.

O contraste é o que dá o tamanho da oportunidade: o Arc 12 mede com precisão o que o **agente** diz que
o contato foi (wrap-up, taxonomia em árvore, com época e lente própria), e **nada** do que o **cliente**
disse que queria. Este ADR não move um menu de lugar: ele faz o eixo da demanda **passar a existir**, e
ele cai na lente de árvore que já está pronta.

### 4. O menu duplicado

`agente_triagem_v2` pergunta *"Como posso te ajudar hoje?"* → escala para `sac_ia` → que pergunta
*"Qual é o motivo do seu contato?"*. **Dois menus seguidos**, o segundo sendo sub-roteamento do
primeiro, em dois artefatos, com dois deploys, e nenhum dos dois medido.

### 5. A descida na árvore já é expressável — o engine não muda

O validador de ciclos (`engine.ts`, política de 2026-06-04) aceita back-edge quando o ciclo passa por um
step que **bloqueia esperando o mundo externo**, e `menu` é explicitamente um deles:

> *"cada iteração exige input humano/externo, então não há runaway (loops de reason/notify/invoke
> queimando LLM sem freio)"*

Logo `menu → choice(é pasta?) → menu` é ciclo **controlado por construção**. Não é preciso iterador
novo, nem step novo, nem mudança no engine para descer a árvore.

### 6. A única primitiva que falta

`EscalateTargetSchema = z.object({ pool: z.string() })`, e o executor passa `step.target.pool` **cru**
(`packages/skill-flow-engine/src/steps/escalate.ts:22`) — sem interpolação. *"Despache para o pool que
esta folha endereça"* **não é expressável hoje**. É o caminho crítico inteiro deste ADR (ver F3).

---

## Decisões

### D1 — Navegação é DOMÍNIO DE VALOR, exatamente como a taxonomia de wrap-up

A recursão vive em `DialogOption`; `nodes[]` segue **plano**; a árvore inteira é **uma pergunta**. O
laço é do SKILL (`menu → choice → menu`), não do JSON — e é um ciclo já sancionado pelo engine (contexto
§5). Nada aqui reabre *"branching no formulário"*, que é a guarda load-bearing do
`adr-dialog-conditional-skip-logic`.

**Corolário que preserva a costura:** para descer, o runner precisa saber se a opção é **pasta ou
folha** — e pela **D2 do `adr-dialog-tree-options`** isso é *derivado da estrutura*, não do significado.
O runner lê a **forma**, nunca o assunto: continua cego ao domínio, e as quatro costuras do
`skill_dialog_runner_v1` (conteúdo · controle · canal · segredo) sobrevivem inteiras.

### D2 — A folha carrega o CAMINHO; o pool mora na config do pool orquestrador

`DialogOption` tem hoje `id · label · value · capture · options · active` — **nenhum campo de destino**,
e não ganha um.

Duas razões, e nenhuma é de gosto:

1. **Ciclos de vida incompatíveis.** O form publicado é **congelado no promote**
   (`adr-deploy-time-content-snapshot`); pool é **DB-owned**, editável na UI, com `seed-if-absent`. Um
   `pool_id` dentro do snapshot seria uma segunda fonte de verdade de roteamento, **invisível de quem
   administra pools** — e imutável até o próximo promote do formulário.
2. **O editor de formulário viraria editor de roteamento**, que é exatamente o que
   `adr-dialog-conditional-skip-logic` chama de guarda load-bearing.

O mapa `caminho → pool` mora na config do pool orquestrador, com **precedente de forma idêntica**:
`mentionable_pools: z.record(z.string())` (`agent-registry.ts:440`). Isso mantém o invariante
*"o POOL é a unidade endereçável"* e satisfaz *"every config field is UI-editable"*.

### D3 — Só a espécie A é absorvida

Navegação sobe para a árvore do orquestrador; **coleta fica no especialista**. Ver contexto §2 para o
critério e o dano de errar.

### D4 — Navegação é resposta ÚNICA; `checklist` é proibido aqui

`interaction: list | button`, nunca `checklist`.

⚠️ Isto merece registro explícito porque a **D5 do `adr-dialog-tree-options` foi REVOGADA em 2026-09-05**
e a multi-seleção cross-pasta passou a ser o comportamento permissivo. Marcar duas folhas descreve bem o
que *aconteceu* (wrap-up); não descreve para quem **despachar**.

**E a profundidade é o recurso escasso.** O agente vê colunas Miller — a árvore toda de uma vez. O
cliente vê **um nível por turno**: quatro níveis são quatro idas e voltas antes de alguém ser atendido —
em voz é URA, em chat é abandono. **Teto de 2 níveis** para árvore de navegação, e é restrição de
CONTEÚDO, que o caso do wrap-up nunca teve.

### D5 — Várias folhas → um pool é legítimo, e é o ponto

`sac.info_plano`, `sac.status_servico`, `sac.problema_tecnico` mapeiam todas para `sac_ia`. O roteamento
**colapsa**, o caminho **viaja**: o especialista recebe o cliente já classificado em vez de reperguntar
(contexto §4), e a série do Arc 12 mede a demanda por folha ainda que o destino seja um só.

É também o que permite as duas taxonomias divergirem: o menu fala a língua do **cliente**, os pools se
organizam por **competência**. Forçá-las a ser a mesma árvore é o modo clássico de a URA falar jargão
interno.

### D6 — A árvore é o contrato COMUM ao orquestrador determinístico e ao com LLM

Com LLM, quem navega é o LLM — não a árvore. Mas a árvore continua sendo o **domínio de desfechos
permitidos**: o LLM navega livre e tem de **aterrissar numa folha declarada**.

Três consequências, nenhuma delas custando nada:

1. Os dois orquestradores emitem o **mesmo `category`** ⇒ mesma série, mesma lente, **comparáveis**. Sem
   isso não há como provar que o LLM roteia melhor: os dois mediriam em unidades diferentes.
2. A **folha de escape (D7 do ADR da árvore)** vira o *"não sei"* do LLM — e, pelo próprio argumento da
   D7, um **fato contável** em vez de um nulo indistinguível de *"não perguntamos"*.
3. Restringir a saída a um vocabulário declarado é o que torna um roteador LLM **avaliável**. Livre, ele
   é inauditável.

### D7 — Sem mensagem por folha

Hoje `agente_triagem_v2` tem um `notify` sob medida antes de cada `escalate` (6 para 5 destinos). A
árvore usa **uma frase genérica com o `label`** (*"transferindo para {label}"*).

Recusado acrescentar um campo de mensagem a `DialogOption`: é o primeiro passo para o formulário virar
config de roteamento (D2), e o ganho é uma frase.

### D8 — A árvore diz PARA ONDE IR, nunca o que vem depois

Retorno ao menu, encadeamento, encerramento: ciclo de vida de sessão, não taxonomia.

Instância concreta medida: **`auth_sac`** (*"auth + SAC"*) não é um serviço, é uma **composição** — uma
sequência. Fica **uma folha → um pool**, e o encadeamento é problema daquele pool. Se aparecer um segundo
caso assim, é sinal de que falta um nível de **processo**, não um nó na árvore.

### D9 — Disponibilidade é RUNTIME, nunca conteúdo

Folha cujo pool está sem agente não pode ser resolvida no formulário: conteúdo é congelado no promote
(D2). Quem confere é o orquestrador **no despacho**, e a recusa é barulhenta.

### D10 — `ask_when` esconde ramo; nunca escolhe alvo

Guarda declarativa é sancionada (`adr-dialog-conditional-skip-logic`): ocultar um ramo por contexto
(*"só oferece 2ª via a quem tem fatura"*) é legítimo. No instante em que uma condição **escolhe
destino**, é workflow — não formulário. Esta é a linha que a D2 protege, e é por onde a erosão entraria.

### D11 — A navegação HERDA a durabilidade do `menu`, e isso é PARIDADE, não escolha

*(Levantada pelo dono em 2026-09-06, com a pergunta certa: para substituir o skill a contento, o modelo
precisaria ser contextless e persistir as paradas no Redis — ou o `DialogForm` vira contexto em memória?)*

**Ele NÃO vira contexto em memória.** Medido, peça por peça:

| peça | durável? | evidência |
|---|---|---|
| conteúdo do form (`render` no `pipeline_state`) | **sim** | `PipelineStateManager.save` no laço do engine, a cada transição concluída |
| cursor da navegação (saída de `invoke`) | **sim** | `invoke` conclui na hora, e o `output_as` entra no mesmo `save` |
| posição (`current_step_id`) | **sim, e o engine RETOMA dali** | teste `"retoma do current_step_id sem reiniciar do entry"` |
| não re-disparar trabalho feito | **sim** | teste `"retoma task step após crash — não re-dispara agent_delegate"`, via `job_id` persistido |
| **a ESPERA do `menu`** | **NÃO** | ver abaixo |

> ### ⚠️ CORREÇÃO de 2026-09-06 — a D11 nasceu com o achado central ERRADO
>
> A versão original desta decisão afirmava: *"`menu` não chama `saveState`, só `catch`, `collect` e
> `delegate` chamam"* e *"**ninguém reinvoca `run()`**; morto o processo, a resposta do cliente fica na
> lista até o TTL"*. **As duas metades estavam erradas**, e o erro foi de método: o `grep` que as
> produziu tinha `| head`, e eu li uma lista TRUNCADA como se fosse completa. É a família catalogada na
> § Postura de Engenharia — *uma lista parece completa por ser uma lista* —, agora cometida ao MEDIR a
> durabilidade, que era justamente a pergunta do dono.
>
> **O que a medição completa mostra:**
>
> | fato | correto |
> |---|---|
> | quem chama `saveState` | **nove** arquivos de step, não três (`catch`, `collect`, `delegate`, `invoke`, `loop`, `notify`, `receive`, `suspend`, `task`) |
> | efeito colateral de MCP | `invoke`/`notify` têm **sentinela de duas fases** (`dispatched`→`completed`) que torna a chamada idempotente através de queda |
> | reentrada após queda | **EXISTE** — o `CrashDetector` re-enfileira a conversa, e o engine retoma do `current_step_id` |
>
> A reentrada é guardada contra falso positivo por DOIS sinais: o *execution lock*
> (`{t}:pipeline:{sid}:running`, TTL 400 s, renovado para `timeout+60` antes do BLPOP) e o *activity
> flag* (`{t}:session:{sid}:active_instance:{iid}`, TTL 30 s **renovado por um timer dentro do
> processo**). Morto o processo, o timer morre com ele: o flag some em ≤30 s, o lock expira, e o
> `CrashDetector` re-enfileira → re-roteia → `run()` retoma do `current_step_id`, que é o `menu`.
>
> **O fluxo NÃO se perde.** O que se perde é menor e ainda assim real:
>
> 1. a resposta em voo, empurrada para `menu:result:{sid}:{iid}` — a chave carrega o `instance_id` do
>    processo morto, então a nova instância nunca a lê e o cliente **é perguntado de novo**;
> 2. o tempo até a recuperação, limitado pelo TTL do lock (para um menu de 120 s, até ~180 s);
> 3. a licença de IA, retida durante toda a espera.
>
> **`menu` segue sendo o único step de espera sem `saveState` próprio** — mas isso importa muito menos
> do que a versão original dizia: a transição PARA o menu já foi salva pelo laço do engine, então o
> `current_step_id` está correto no Redis e é dele que a retomada parte.
>
> **Consequência para a `DUR-01`:** o mérito dela MUDA de natureza. Não é mais *"o fluxo se perde"* —
> é **latência de recuperação, uma repergunta ao cliente, e licença retida na espera**. O argumento de
> CAPACIDADE (liberar a licença entre turnos), que era o terceiro da lista, passa a ser o principal.

**Decisão: a F1–F4 miram PARIDADE.** O skill que elas substituem tem exatamente a mesma propriedade —
`agente_triagem_v2` é um `menu` num BLPOP, e `skill_atendimento_sac_v1` também. O modelo com `DialogForm`
não é regressão, e o critério *"substituir a contento"* está cumprido sem nada de novo.

**A contagem de esperas fica NEUTRA**, o que não era óbvio: hoje são duas (menu da triagem + menu do
`sac_ia`, em **dois** agentes); com a árvore são duas também (nível 1 + nível 2), num agente só. Mesma
exposição, uma passagem a menos.

#### O modelo contextless já existe — e por isso é arco PRÓPRIO, não fatia desta ADR

O par `collect`/`suspend`/`delegate` já faz exatamente o que a pergunta descreve: salva o estado, devolve
`__suspended__`, e um evento externo reinvoca `run()`, que retoma do `current_step_id`. Fazer o `menu`
seguir esse padrão é viável com as peças que existem — e mesmo assim **não entra aqui**, por três
consequências que são de plataforma, não de feature:

1. **Atinge todo skill de agente**, não o orquestrador — `menu` é o cavalo de batalha (17 skills o usam).
2. **Muda o ciclo de vida do segmento:** pela Arc 19, `suspend` devolve o agente ao pool. Para um menu isso
   significaria **liberar a licença de IA entre turnos** — ganho real de capacidade, mas que mexe na
   semântica de ocupação que a § *Operational Visibility* mede e publica.
3. **Exige que o bridge REINVOQUE** em vez de fazer LPUSH — inversão do contrato que ele declara hoje.

Enfiar isso aqui seria a *"wide container for a narrow fact"* que esta própria ADR recusa noutro eixo: uma
decisão de plataforma entrando pela porta de uma feature. Vai para `DUR-01`, com mérito próprio.

⚠️ **Ordem, se a durabilidade virar prioridade:** ela vem **antes da F4** (quando o `agente_triagem_v2`
sai), nunca depois — senão o orquestrador nasce no modelo antigo e é migrado duas vezes.

---

---

## A árvore que sai da medição

```
sac                    [pasta]
  info_plano                    → sac_ia
  status_servico                → sac_ia
  problema_tecnico              → sac_ia
  especialista                  → retencao_humano
portabilidade                   → portabilidade_ia
reembolso                       → reembolso_ia
auth_form                       → auth_form_ia
auth_sac                        → auth_sac_ia
nao_se_aplica       [escape D7] → retencao_humano
```

9 folhas, profundidade 2 (dentro do teto da D4), mapa de 8 entradas. `agente_triagem_v2` (15 steps) sai
inteiro.

---

## Fases

| fase | entrega | depende de |
|---|---|---|
| **F0** ✅ | **Medir** — inventário de menus, espécies A×B, zero produtores de demanda, sanção de ciclo, alvo literal do `escalate` | — |
| **F1** | **O eixo de demanda passa a existir**: árvore autorada no `DialogForm` + `agent_event` com o caminho, **ainda escalando pelo `choice` atual**. Sem tocar no engine | F0 |
| **F2** ✅ | **Renderização em canal** (`DLG-15`) — seções no WhatsApp, grupos no webchat, e recusa NOMEADA onde não cabe | F0 |
| **F3** ✅ | `escalate` com **alvo interpolável** + mapa `caminho → pool` na config do pool (D2) | ~~F2~~ — ver emenda |
| **F4** ✅ | O **runner genérico** assume a navegação; `agente_triagem_v2` sai | F1, F3 |
| **F5** ✅ | **Paridade LLM** (D6): o orquestrador IA aterrissa nas mesmas folhas declaradas | F4 |

> **Emenda de 2026-09-06 — D12: a categoria é COMPOSTA no servidor, não conferida.**
> Esta tabela dizia *"`agent_event` com o caminho"*, e a F1 descobriu que **aquela tool é
> inalcançável** deste caminho: ela exige `session_token` de `agent_login`, e o orchestrator-bridge
> não emite nenhum (`session_token` aparece **0 vezes** no `main.py` dele). Era o bloqueio da
> ORQ-06. A saída escolhida pelo dono foi a **(b)**: `agent_event_record`, identificado pela
> SESSÃO.
>
> **A troca não é de credencial, é de quem compõe.** O `agent_event` recebe a `category` pronta e
> **confere** que o primeiro segmento é o pool — e conferir depois obriga o chamador a saber em que
> pool roda, que é por que o YAML precisava interpolar `{{@ctx.core.pool.id}}`. O
> `agent_event_record` recebe `emitter` + `metric_key` + `path` e compõe
> `{pool}.{emitter}.{metric_key}[.{path}]` lendo o pool do `session:{id}:meta`: o isolamento de
> namespace do Arc 12 vale **por construção**, e o chamador deixa de precisar da informação.
> `emitter` é rótulo estável, nunca um `skill_id` — renomear partiria a série.
>
> **A afirmação de que a lente existente cobre esta série foi MEDIDA, não lida.** Um evento real
> (sessão sintética, depois apagada) saiu
> `demo_ia.navegacao.destino.sac.info_plano`, chegou ao ClickHouse e a lente o desenhou nos **três**
> níveis (`depth` 3/4/5, `derived_leaf` na folha). Havia risco real: as colunas pré-decompostas
> param em `category_l4` e o caminho tem 5 segmentos — mas a lente não as usa, ela fatia o
> `category` inteiro.
>
> ⚠️ **E há um carimbo que a F1 quase perdeu em silêncio.** A época (D13/D14) viaja em
> `tags['dialog_form_id']` + `tags['dialog_form_version']`; a primeira versão do skill escreveu
> `form_id`. Nada fica vermelho — o evento só nasce **sem época para sempre**, e a árvore devolve
> `single_vocabulary: false` sem saber dizer por quê. Os dois valores têm de vir da **saída do
> `dialog_tree_level`**, porque o carimbo é fato do RENDER e um literal pode discordar da forma que
> o cliente viu. Guardado pelo **ramo E** do gate, que mede as duas pontas (produtor escreve,
> consumidor lê) e foi testado por mutação.

> **Por que a F1 vem antes de tudo, e sozinha:** ela entrega a medição que hoje é **zero** sem depender
> de canal nem de engine. Se a F2 ou a F3 demorarem, o eixo de demanda já existe e já é legível na lente
> de árvore. É o oposto do arco que só dá valor no fim.

---

## Alternativas consideradas e RECUSADAS

| alternativa | por que não |
|---|---|
| **`pool_id` na folha** | Form congelado no promote × pool DB-owned ⇒ segunda fonte de verdade de roteamento, invisível de quem administra pools (D2) |
| **N ramos de `choice`, um por folha** | Explosão de control-flow — os 15 steps do `agente_triagem_v2` para 5 entradas já são a prova empírica |
| **Absorver a espécie B (coleta)** | Coletaria dado do especialista antes de ele existir, e multiplicaria a profundidade, que é o recurso escasso (D4) |
| **Campo de mensagem em `DialogOption`** | Primeiro passo para o formulário virar config de roteamento; o ganho é uma frase (D7) |
| **LLM roteando sem a árvore** | Perde a comparabilidade e a auditabilidade; o *"não sei"* volta a ser nulo em vez de folha contável (D6) |

---

> **Emenda de 2026-09-06 — F5 entregue; o arco fecha.** O gatilho pedia *"F4 entregue **e** um pool
> de orquestração IA candidato"*. A primeira metade fechou hoje; a segunda **não existia** — medidos
> 6 skills com `reason`, e nenhum decide DESTINO (respondem, sugerem, extraem contexto, avaliam). O
> candidato foi criado: `skill_navegacao_llm_v1` no pool `demo_llm_ia`, com o MESMO
> `navigation_pools` do `demo_ia`.
>
> **As três consequências da D6, uma a uma:**
>
> 1. **Mesma série.** `emitter` e `metric_key` idênticos (`navegacao.destino`); o que separa os dois
>    é o `l1`, que é o POOL — e o pool é exatamente a dimensão pela qual se quer compará-los.
> 2. **Escape contável.** Caminho inventado, pasta em vez de folha e string vazia caem os três em
>    `nao_se_aplica`, que é uma linha na mesma série — dá para ler *"o roteador não classificou X%"*.
> 3. **Auditável.** A saída é restrita ao vocabulário, e restringir é o que permite avaliar.
>
> ⚠️ **A parte frágil da D6 não é o prompt — é a CONFERÊNCIA.** Mandar a lista ao modelo é
> instrução; ele pode devolver um caminho plausível que não existe, e sem conferir isso viraria uma
> categoria que a lente desenha **como se alguém a tivesse autorado**. Quem decide é o step
> `conferir`, que usa a MESMA projeção do orquestrador determinístico (`dialog_tree_level` com o
> caminho pontuado, F2) — não uma segunda leitura. Medido ao vivo: `financeiro.boleto_2via` RECUSA,
> `sac` (pasta) RECUSA, `""` RECUSA, `sac.info_plano` ACEITA.
>
> `leafPaths` em `@plughub/schemas` dá o vocabulário, e ele alimenta as **duas** metades — prompt e
> conferência — da mesma fonte. Fontes separadas fariam o modelo receber opções que a plataforma
> depois recusaria, e o escape contaria alto por defeito NOSSO.
>
> Ramo **J** do gate, verificado por duas mutações: pular a conferência ⇒ VERMELHO; medir noutra
> unidade ⇒ VERMELHO.

## Riscos e questões abertas

> **Emenda de 2026-09-06 — a F3 NÃO dependia da F2, e isso foi medido.** Esta tabela dizia
> `F3 ← F2`, e a linha do ledger repetia *"depende da ORQ-02 para ser testável ponta a ponta"*. O
> contato real da F1 refutou: a navegação desce **um nível por turno** (`optionsAtPath` projeta um
> nível; o `menu` renderiza uma lista PLANA), então **nenhum canal precisa desenhar árvore** para o
> caminho inteiro funcionar. A F2 melhora a experiência — menos turnos —, não destrava a F3.
> Dependência herdada de raciocínio, desfeita por medição.
>
> **O que a F3 entregou:** `escalate.target.pool` aceita referência (`$.`/`@ctx.`/`{{…}}`), resolvida
> no engine · `pools.navigation_pools` (`Record<caminho, pool_id>`, forma idêntica à do
> `mentionable_pools`, migração `20260906120000`) · tool `pool_route_resolve` (prefixo mais longo
> por SEGMENTO) · superfície de UI em *Configuração → Recursos → Pools*, reusando o editor do
> `mentionable_pools` em vez de copiá-lo. O `skill_navegacao_v1` foi de **16 para 12 steps**: saíram
> um `choice` de 5 condições e 5 `escalate` literais — a tabela de roteamento que estava escrita no
> controle de fluxo.
>
> ⚠️ **Não há default, por decisão.** Caminho sem prefixo declarado é `isError` nomeando o caminho e
> as chaves existentes; o fluxo o manda ao humano. Um destino de emergência dentro do resolvedor
> seria o `queue_pool_id or pool_id` do `CLAUDE.md`: config ausente convertida em despacho para o
> lugar errado, em silêncio.
>
> ⚠️ **E o prefixo é por SEGMENTO.** Um `startsWith` cru faria `sac_premium` casar com a chave `sac`
> e mandar o contato para o pool errado **com o log dizendo que casou**. É o caso que carrega o peso
> nos testes do resolvedor, e foi verificado por mutação.

- **A F3 é a porta da erosão.** Tornar o alvo do `escalate` interpolável é necessário — e é exatamente
  por onde *"roteamento condicional no formulário"* entraria depois. A guarda da D10 precisa nascer
  **junto** com a interpolação, não depois.
  **✅ Nasceu junto (2026-09-06):** ramo **G** do `probe_orchestrator_tree_nav.sh` — varre as 14
  formas publicadas e o `DialogOptionSchema` atrás de **nove** nomes que significariam destino
  (`pool`, `pool_id`, `target`, `target_pool`, `escalate_to`, `route`, `route_to`, `skill_id`,
  `agent_type_id`). As **duas** metades importam: forma limpa hoje não impede campo novo amanhã, e
  *campo que existe acaba usado*. Verificado por mutação (uma folha com `pool` ⇒ VERMELHO).
> **Emenda de 2026-09-06 — F4 entregue, e o menu duplicado morreu por CONDIÇÃO, não por remoção.**
> A triagem já tinha saído como efeito da F1 (o promote da navegação no `demo_ia`); o que faltava era
> o menu duplicado, e ele **não podia ser apagado**: o `sac_ia` tem endpoint de canal próprio, então
> o cliente pode cair lá sem passar por navegação nenhuma. Apagar consertaria o caminho novo e
> quebraria o antigo. O menu **fica**; deixa de ser o Único caminho.
>
> A navegação grava `session.navegacao.path` no ContextStore e o especialista ramifica sobre ele.
> ⚠️ **O `escalate` NÃO serve de veículo:** o `conversation_escalate` publica
> `process_context: { pipeline_state }` no evento e **ninguém lê** — medido, uma única ocorrência no
> repositório, na declaração do modelo do routing-engine. Declaração sem consumidor, a mesma família
> do `options_tree` logo abaixo.
>
> ⚠️ **E o `default` desse `choice` é PERGUNTAR** — seguro, e por isso mesmo um esconderijo:
> renomear uma folha faria o especialista deixar de reconhecer o caminho e voltar a perguntar, **sem
> nada ficar vermelho**. Daí o ramo **H** do gate, que compara de fora os dois conjuntos (folhas
> roteadas para um pool × ramos do skill daquele pool) e foi verificado por mutação.
>
> ⚠️ **O seed foi a parte que quase escapou.** `infra/registry/tenant_demo.yaml` ainda declarava
> `skill_triagem_v2` no `demo_ia`, e o mapa `navigation_pools` só existia no DB. Como o YAML é
> seed-if-absent, isso é **inerte hoje e volta a valer num `--wipe`**: a triagem renasceria e o
> `pool_route_resolve` recusaria TODO caminho por `route_map_absent`. Instalação limpa é um teste, e
> teste que nunca roda não é cobertura.
>
> `agente_triagem_v2.yaml` **fica no repositório**, rotulado como aposentado — mesmo critério dos
> pacotes em quarentena, e com uma razão a mais medida: os promotes sucessivos deixaram o slot
> `previous` do `demo_ia` com outra versão da própria navegação, então **um rollback não traz a
> triagem de volta** e este arquivo é a última receita dela fora do git.

> **Emenda de 2026-09-06 — F2 entregue, e o alvo não era o que a linha dizia.** *"Nenhum canal
> desenha árvore"* é verdade e não é o dano. O dano é que os filhos eram descartados **em
> silêncio**: inofensivo quando o fluxo desce nível a nível, e beco sem saída quando um runner
> genérico entrega o `render` inteiro a um `menu` — o cliente vê só as pastas e nunca alcança uma
> folha. **Exposição: 4 runners** (`agente_nps_v1`, `skill_dialog_runner_v1`,
> `skill_survey_multi_v1`, `skill_survey_runner_v1`). **Dano: zero** — nenhuma das formas deles tem
> árvore hoje. É guarda, não conserto de dano vivo, e a fase foi dimensionada por isso.
>
> Entregue: `option_tree.py` no channel-gateway (`is_tree`/`tree_depth`/`flatten_to_sections`, 7
> testes) · WhatsApp desenha **seções tituladas** quando cabe e **loga nomeando** quando não ·
> webchat agrupa · `chosen_id` aceita caminho pontuado, então turno-único e turno-a-turno aterrissam
> idênticos (medido) · `DialogOptionSchema` proíbe ponto no id.
>
> ⚠️ **O ponto já era separador em três mecanismos e ninguém o impunha** — `category_path` do Arc 12,
> o prefixo de `navigation_pools` (F3) e agora o `chosen_id`. Promessa sem mecanismo, a família do
> DDL de `participation_intervals`. Medido antes de fechar: **0 de 87** ids têm ponto.
>
> ⚠️ **A paridade Python×TS é o ramo I, e ela não é zelo.** São duas implementações em duas
> linguagens, sem código compartilhado possível. Trocar o separador de um lado só mantém a linha
> bonita na tela e faz a projeção devolver `found: false` — a navegação reinicia parecendo certa.
> Verificado por mutação (`.` → `|` só no Python: VERMELHO, nomeando as 4 linhas).

- **`options_tree` é hoje uma declaração sem consumidor** (medido: aparece só dentro de
  `packages/schemas` — definição, derivação, teste e `.d.ts`; o Console decide por conta própria com
  `temArvore`). A F2 é quem lhe dá o primeiro consumidor — e, de quebra, impede a terceira cópia da
  regra *"isto é árvore?"*, que já são duas.
- **Duas taxonomias, uma raiz.** Demanda (navegação) e desfecho (wrap-up) devem compartilhar a raiz de
  `category` para serem comparáveis, mas **não** precisam ter a mesma forma. A divergência entre elas é
  o produto — *"12 entraram por `financeiro`, 5 terminaram em `tecnico`"* — e obrigar as duas a coincidir
  destruiria justamente o sinal.
- **Época.** Menu muda muito mais que formulário de classificação, então a D13/D14 do ADR da árvore
  (época por forma) importa **mais** aqui do que no wrap-up. Já está pronta; só precisa ser exercida.

## Estado em 2026-09-22 — a folha de limite, e o que o LLM enxerga

O primeiro teste por VOZ do orquestrador com LLM (WCH-09) mediu um pedido de *aumento de limite*
aterrissando em `sac.especialista`. Não era erro do LLM: a árvore não tinha folha de limite (e o
`limite_ia` não estava no `navigation_pools`). A folha `aumento_limite` entrou na v5 da
`dialog_navegacao_atendimento_v1`, raiz, apontando `limite_ia` com verbo `escalate` (o runner do
limite retoma a pendência PRÓPRIA, não o chamador — `nao_retorna`) — ORQ-11.

O achado que sobra é de desenho: `leaves` leva ao prompt só o CAMINHO das folhas, sem rótulo nem
descrição, e o LLM classifica pelo texto do código. As três fichas abertas — `ORQ-12` (descrição e
exemplos na folha, como SIGNIFICADO, nunca roteamento), `ORQ-13` (uma pergunta de esclarecimento
antes do escape) e `ORQ-14` (medir o roteamento errado) — respeitam D2, D5 e D6 como estão.

### D8 — a folha declara SIGNIFICADO, e ele é distinto do roteamento (ORQ-12, 2026-09-22)

`DialogOption` ganhou dois campos OPCIONAIS, e o eixo que os separa não é o formato, é **quem lê**:

| campo | quem lê | onde aparece |
|---|---|---|
| `description` | o classificador **e** o cliente | prompt hoje; menu na `ORQ-15` (teto de 72 = linha de lista do WhatsApp) |
| `examples` | **só** o classificador | em lugar nenhum — nunca entra no `render`, que é o bloco dos canais |

Quatro consequências que sustentam a decisão:

1. **É significado, nunca roteamento.** O mapa folha→pool continua no `navigation_pools` do POOL
   (D2), e a conferência contra a folha declarada (D6) não muda uma linha. Um campo `pool` na folha
   continua proibido, e o ramo G do gate continua medindo isso.
2. **Não se reusa a descrição do POOL.** Aquilo é texto de operador; o menu fala a língua do
   CLIENTE (D5). Reusá-la poria o vocabulário do operador na boca do orquestrador.
3. **`examples` só em folha.** Numa pasta, ele ensinaria o LLM a aterrissar no que não é resposta —
   e pasta × folha é derivado (D2), então a regra vale sozinha quando alguém acrescenta um filho.
4. **Duas leituras da mesma árvore, CONFERIDAS.** `leafMeanings` lê o form cru (porque `examples`
   não pode viajar no `render`) e o `dialog_tree_level` compara os caminhos com `leafPaths`; se
   divergirem, o `vocabulary` **sai** e o motivo vai ao log. Degradar para "só os caminhos" é o
   comportamento de antes da ficha; degradar para "sem destino nenhum" teria feito todo contato
   escapar por defeito nosso.

O que a ficha NÃO fez, e está nomeado: exibir a descrição ao cliente (`ORQ-15`) — a descrição ainda
não viaja ao canal.

### D9 — o destino é série; o ERRO é derivado do que veio depois (ORQ-14, 2026-09-22)

`{pool}.navegacao.destino.{caminho}` diz ONDE o contato aterrissou, e é a mesma série nos dois
orquestradores — é o que os torna comparáveis. Ela não diz se aterrissou CERTO, e sem isso
*"o LLM roteia melhor"* não é afirmação medível: dois roteadores podem distribuir igual e errar
diferente.

**O erro não se observa; a consequência sim.** `GET /reports/navigation/routing` conta, por
destino, o contato cujo destino **não concluiu** e cujo atendimento **seguiu em outro pool**. É
proxy, e o campo se chama `re_roteados` exatamente para que ninguém o leia como veredicto: há
falso positivo legítimo (o destino certo que descobre, atendendo, que o caso é de outra área) e
falso negativo (o destino errado que resolve assim mesmo).

Três exclusões fazem o número significar atendimento, e cada uma tem motivo próprio:

| fora | por quê |
|---|---|
| hook e convidado (`role != 'primary'`) | NPS, wrap-up e `@mention` são paralelos, não continuação — contá-los faria todo contato com NPS parecer re-roteado |
| agente de fila (`agent_type = 'system'`) | segurar o contato não é atender |
| a volta ao ORQUESTRADOR | é o *"tenho outro assunto"* do cliente: decisão NOVA, com evento próprio |

`sem_destino` (o cliente saiu antes de ser atendido) sai da BASE da taxa — somá-lo faria a folha
abandonada parecer a melhor. `sem_cadeia` é defeito de dado e tem contador para não sumir calado.
Sem ninguém atendido, a taxa é `null` (**não medida**), nunca `0.0`.

**`proximos` é o campo acionável:** um destino que termina sempre no mesmo outro pool é uma folha
que falta na árvore. Primeira medição (30 dias, 44 contatos, 27% de re-roteio):
`demo_ia / sac.info_plano` 50% → `retencao_humano`, `portabilidade_ia`;
`demo_llm_ia / aumento_limite` 60% → `sac_ia`. O segundo não é erro de roteamento: é o runner do
limite escalando quando a identificação falha — que é o falso positivo previsto, e aparece aqui
com o caminho que o explica.

⚠️ **O que esta métrica ainda NÃO autoriza:** comparar os dois orquestradores com os dados de hoje.
Eles têm janelas, volumes e destinos diferentes — a métrica passou a existir, a comparação precisa
de amostra pareada. Dizer "o LLM erra menos" com 5 contatos contra 14 seria a mesma pressa que a
métrica existe para evitar.
