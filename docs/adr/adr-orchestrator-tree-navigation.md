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
| **F2** | **Renderização em canal** (`DLG-15`): hoje nenhum canal de cliente desenha árvore — o adapter de WhatsApp trata `options` como lista plana, dimensionada por `len()` | F0 |
| **F3** | `escalate` com **alvo interpolável** + mapa `caminho → pool` na config do pool (D2) | F2 |
| **F4** | O **runner genérico** assume a navegação; `agente_triagem_v2` sai | F1, F3 |
| **F5** | **Paridade LLM** (D6): o orquestrador IA aterrissa nas mesmas folhas declaradas | F4 |

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

## Riscos e questões abertas

- **A F3 é a porta da erosão.** Tornar o alvo do `escalate` interpolável é necessário — e é exatamente
  por onde *"roteamento condicional no formulário"* entraria depois. A guarda da D10 precisa nascer
  **junto** com a interpolação, não depois.
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
