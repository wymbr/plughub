# Kickoff — triagem do backlog contra a linha mestra n8n

> ⛔ **HISTÓRICO — kickoff de uma tarefa já executada, contra uma direção depois revertida.**
> A triagem que ele encomendou virou [`n8n-triagem-2026-08-17.md`](n8n-triagem-2026-08-17.md); a direção
> foi abortada em 2026-08-18 ([`n8n-arco-abortado-2026-08-18.md`](n8n-arco-abortado-2026-08-18.md)).
> **Não abrir sessão nova com este arquivo.**

> **Uso:** abrir sessão NOVA com Opus e este arquivo como primeira leitura.
> **Tarefa:** reavaliar todo o trabalho em aberto contra a decisão de direção de 2026-08-17, e
> classificar cada item.
> **Não é tarefa desta sessão:** redesenhar a integração (já está fechada em
> `docs/product/n8n-interop-boundaries-and-seams.md`), nem implementar nada.

---

## 1. O que carregar

| Carregar | Por quê |
|---|---|
| `docs/product/n8n-interop-boundaries-and-seams.md` | A linha mestra, **revisão 3**. Ler §0, §5, §10 (incl. 10.1/10.2/10.3) e §11 com atenção; o resto sob demanda |
| `TODO.md` | O que será triado |
| `CLAUDE.md` § `Pending (Next Iteration)` | Itens que vivem lá e não no TODO |

> ⚠️ **A §0 é leitura obrigatória e não é resumo.** Ela escolhe entre duas justificativas possíveis e
> adota a do **editor**, descartando a de *"parar de duplicar"*. Isso muda o que a triagem pode
> abortar — ver §3.

**Não carregar** a árvore `docs/` inteira, nem `CHANGELOG.md`, nem `plughub_spec_v1.docx`. Leitura
seletiva vale mais aqui do que em qualquer outra tarefa — a triagem decide abortar trabalho real, e
é onde qualidade degradada por contexto inchado custa mais caro.

---

## 2. A linha mestra em onze linhas

1. **A justificativa adotada é a do EDITOR, não a de "parar de duplicar".** O ganho não é apagar
   código — é tirar 100% da autoria de fluxo de YAML + canvas caseiro. O código que some é efeito
   colateral.
2. Medição (contexto, não justificativa): ~12% do código é território n8n; ~45% é fosso sem
   equivalente. **A maioria daqueles 12% FICA** — é estado e governança, não orquestração.
3. **Alvo: todo skill associado a um pool, perfis `workflow` E `agent`.** O editor de fluxo sai por
   completo; só o bloco `flow:` do skill sai com ele.
4. **O skill sobrevive como envelope de configuração** — `config_params`, `interface_schema`,
   masking, perfil, `mention_commands`. O modelo de slot/`promote`/`deploy_version` fica inalterado.
5. Regra de fronteira: **n8n toca sistemas; PlugHub toca pessoas.** Todo contato atravessa o
   channel-gateway; é a travessia que produz `journey`/`session`/`segment`.
6. Segunda regra: **fica o que produz ou consome fronteira, ou é governança de contato com pessoa;
   sai a camada de orquestração.**
7. n8n é **recurso chamado** (canal na entrada, domain MCP server na saída), nunca pool, skill ou
   agent type. Padrão único: **pool com skill nativo que CHAMA o n8n**.
8. Quatro costuras: A (webhook) + E (Kafka Trigger) = fase 0; B (cliente MCP) = principal externo;
   C (domain MCP server) = maior retorno; D (node/template) = fase 5.
9. **O resíduo é um RUNNER, não um fluxo.** O que não sai é código, não autoria — por isso o editor
   morre inteiro. Consequência: o interpretador genérico (`skill_dialog_runner_v1`, hoje ele mesmo um
   skill em YAML) precisa virar **código**, e isso é **pré-requisito da fase 5**.
10. **Item bloqueante:** os mapeadores de `flow_definition`/`pipeline_state`. Com o alvo cobrindo
    100% dos skills, a avaliação de IA degradaria **em bloco** para grau-transcript. São bloqueantes
    da fase 5, não trabalho opcional.
11. Gate de latência é **condição de prosseguir**, não prudência: instrumentar antes de migrar o
    perfil `agent`.

---

## 3. Passo 0 — particionar antes de classificar

**O `TODO.md` tem ~85 seções e a maioria esmagadora é backlog de DEFEITO** — segmento que não fecha,
`phone_number_id` gravado dentro de `pool_id`, seis serviços sem logging, `voice.py` chamando método
inexistente, seeds sem carimbo de `origin`. Nada disso é triável contra o n8n: não é trabalho de
direção, é conserto. A triagem real são ~25 itens.

Antes de abrir a tabela dos quatro baldes, uma passada rasa marcando cada seção como:

| Marca | Destino |
|---|---|
| `defeito` | **Fora da triagem.** Vira entrada própria, com evidência `arquivo:linha`. Não recebe balde — um balde "Corrige" misturaria decisão de direção com backlog de bug |
| `concluído` | **Fora da triagem.** Aposentar código em produção é a fase 6, com gate de paridade — não é esta sessão (§3.1) |
| `direção` | **Entra** na tabela dos quatro baldes |

### 3.1 O que esta sessão NÃO tria

A lista de aposentadorias da §10 do doc (o que resta dela) **não** é triada aqui. É fase 6, gated por
paridade, e encolheu muito na revisão 3. Sem esta linha, a sessão tentaria "abortar" arcos marcados
✅ e produziria uma tabela que mistura backlog aberto com desmonte de código vivo.

### 3.2 Taxonomia — quatro baldes

"Continuar ou abortar" perde os dois casos mais comuns. Classificar cada item de `direção` em:

| Balde | Critério |
|---|---|
| **Segue** | Está no fosso, ou é pré-requisito de uma fase do §11 do doc |
| **Escopo reduzido** | Parte sobrevive — nomear qual parte, qual morre, e **quem passa a ser o consumidor** da parte que fica |
| **Congela** | Não morre, mas não investir até o gate da fase 3 |
| **Aborta** | **Depende do editor de fluxo que morre.** Só isso |

> ⚠️ **"O n8n cobre" NÃO é fundamento para abortar.** Era, na revisão 2. A revisão 3 adotou a
> justificativa do editor e o §10 mostra a consequência: `mailing-api` inteiro, `scheduler-api` e
> `calendar-api` são cobertos pelo n8n em alguma medida e **ficam os três**. Abortar por cobertura
> reproduz exatamente o erro que o §10.1 e o §10.2 corrigiram.

**Critério permanente** (§0 do doc): *fica o que produz ou consome fronteira de
`journey`/`session`/`segment`, ou é governança de contato com pessoa; sai a camada de orquestração.*

Para cada item, registrar **uma linha de justificativa** amarrada ao critério. Item classificado sem
justificativa é item que será reaberto daqui a dois meses.

---

## 4. A triagem também PROMOVE — não só corta

Enquadrar como "o que abortar" perde o efeito inverso. **Cinco** itens saem de baixa urgência para
caminho crítico — e os dois maiores não existem hoje como item em lugar nenhum.

**Duas frentes NOVAS.** Não estão no `TODO.md` nem no `CLAUDE.md` § Pending. Nascem já na tabela de
triagem, classificadas como **Segue — pré-requisito da fase 5**, com a dependência explícita:

- **Promover o interpretador genérico a serviço de código.** O `skill_dialog_runner_v1` é hoje ele
  mesmo um skill em YAML; com o YAML de fluxo morto, ele precisa virar código de primeira classe. O
  §5.3 do doc diz textualmente que isso *"deixa de ser efeito colateral e vira pré-requisito da fase
  5"*. Arrasta a superfície inteira do dialog primitive (hooks, NPS, wrap-up, survey, OTP).
- **Mapeadores de `flow_definition` e `pipeline_state`.** O §5.4 os promoveu de "trabalho que
  ninguém lembra até o relatório ficar plano" a **bloqueantes da fase 5**. Arrasta `Frente 2 —
  avaliação campaign-driven` e `sequence_index apagado`.

> Juntas, essas duas provavelmente somam mais trabalho que as três abaixo. Sem elas na tabela, a
> fase 5 fica com dois bloqueadores sem dono.

**Três já existentes:**

- **`adr-mcp-interception-single-border.md`, fase B2** (`mcpCall` nativo roteando por
  `mcp_server`) — é literalmente a costura C. Sem ela o n8n não pode ser domain server.
- **`adr-a2a-server-binding.md`, fase A2** (principal externo) — é o mesmo mecanismo que a costura
  B exige, e o item `Agent Principal — identidade de máquina p/ agentes IA` é o terceiro nome da
  mesma coisa. **Avaliar fusão** em vez de esforços paralelos; o doc é explícito em não criar dois
  mecanismos de principal externo.
- **Os três itens de webhook que encostam na fase 0a** — `Autenticação de endpoint webhook` (o 2b
  aberto), `Porta externa de resume × posse do item de pull`, e `source do resume asserido pelo
  cliente`. A fase 0 não sobe sem fechar a rota anônima.

**Não é promoção — decisão já tomada:** `Usage Metering` / `metering × pricing` fica em **Congela**,
com a razão registrada: depende da evolução dos módulos de channel-gateway, que não andou. O achado
de que `llm_tokens_*` não é emitido no `/v1/reason` continua valendo, mas como **defeito** (passo 0),
não como item de direção.

**Correção, não promoção:** `adr-dialog-conditional-skip-logic.md` está **Aceito + implementado desde
2026-07-08** — `ask_when` aparece em 8 arquivos de `packages/`, e o CHANGELOG registra validação ao
vivo no webchat. A §5.5 do doc já foi corrigida; falta a linha 107 do `CLAUDE.md`, que ainda o lista
como *proposto*. A guarda não é tarefa: é **invariante em vigor**, e "load-bearing" significa risco de
regressão. O trabalho real ali são as **3 decisões em aberto do próprio ADR** (`field` apontando para
pergunta pulada, `checklist` multi-valor, UX do construtor de condição).

---

## 5. Armadilha de sequenciamento — triar por função, não por pacote

Abortar por nome de componente erra. Exemplo concreto: *"abortar `workflow-api`"* parece seguro, mas
o step `collect` vive no perfil workflow e **é produtor de fronteira** (cria sessão-filho de
contato). Aborta-se o motor de workflow; não se aborta o `collect`.

**A armadilha foi confirmada por medição** (quadro do §6): o `collect` nem sequer executa no
`workflow-api` — roda no `skill-flow-service`, e quem cria a sessão-filho é o channel-gateway. Mas o
mesmo pacote guarda o único escritor de `workflow.instances` e 12 pontos de fetch do platform-ui.
Nome de pacote não é unidade de decisão.

Antes de mandar qualquer item para **Aborta**, responder: *isso produz ou consome fronteira? isso é
pré-requisito de alguma fase do §11?* Se sim para qualquer uma, o balde certo é outro.

Segunda armadilha: **não abortar agora o que uma fase posterior vai precisar.** As fases 4 e 5
dependem de coisas construídas nas fases 0–3.

---

## 6. Ordem de triagem — contestados primeiro

A §8 manda `/compact` a cada bloco, e quem vem depois pega contexto degradado. Logo os itens de fosso
puro — que precisam de contexto ruim — vão **por último**, e os que envolvem decisão real vão
primeiro.

### Bloco 1 — decisão real, alto risco de errar

1. **`workflow-api` + `skill-flow-worker`** + `Remoção física do legado de workflow por token`.
   **Já medido — ver o quadro abaixo.** Vai a **Escopo reduzido** com três decisões separadas, não a
   Aborta.
2. **Promoção do interpretador a serviço** (frente nova, §4).
3. **Mapeadores tier-2** (frente nova, §4) — arrasta `Frente 2 — avaliação campaign-driven` e
   `sequence_index apagado`.
4. `Flow — step de expressão sandboxed (NÃO eval cru)` — é linguagem de fluxo; mais contestado agora
   que o alvo cobre os dois perfis. Cuidado: o `ask_when` reusa esse vocabulário.
5. `Skill hot-reload via YAML em disco sem restart` — candidato limpo a **Aborta**.
6. `Business in Any Media` — a proposta mais cara e a mais sobreposta ao território n8n.
7. `Dois pacotes fósseis` (clickhouse-consumer, conversation-writer) — já em quarentena.
8. `Record/Replay Harness` — fica mais difícil com runtime externo e mais valioso como gate de
   contrato. Reexaminar, não presumir.

> **Medição do item 1 (feita, com evidência):** `workflow-api` tem cinco endpoints **410 hard**
> (`persist-suspend`, `complete`, `fail`, `collect/persist`, `collect/respond`), mas o trigger
> público `POST /v1/workflow/webhook/{id}` é o **único escritor** de `workflow.instances`, o
> platform-ui tem **12 pontos de fetch** em `/v1/workflow/*`, e `workflow.events` tem mais **dois**
> consumidores vivos (evaluation-api e analytics-api) que sobrevivem ao pacote. `handle_resume` e o
> suspend real já moram no channel-gateway (`adapters/webhook.py:820`), e existem **dois** timeout
> scanners em serviços diferentes. Já o `skill-flow-worker` está morto: as três saídas são dois 410 e
> uma rota `/mcp` que **não existe** no mcp-server (que expõe `/sse` + `/messages`) — "conserta" não é
> opção viável, seria reconstruir para um caminho sendo abandonado. **O `collect` está longe dos
> dois**: executa no `skill-flow-service` e quem cria a sessão-filho é o channel-gateway.
> ⇒ A tabela do §10 do doc mestre (`workflow-api` → "Sai"; `skill-flow-worker` → "Sai ou conserta")
> não sustenta a medição. **Corrigir o doc é entregável desta sessão** (§7.4).

### Bloco 2 — sobem por causa do runner

Deixaram de ser fosso tranquilo: a promoção do interpretador (§4) vai reimplementar essa superfície.

9. `OTP + primitivo de diálogo genérico — resíduos`
10. `Customer Surveys — estado as-built S1–S11`
11. `Detach de hooks de finalização + Pull direcionado + ACW`
12. `Wrap-up como fonte de dados`
13. `Revisão do editor de diálogos` — **sobe**: o editor de DialogForm sobrevive e ganha importância

### Bloco 3 — promoções e pré-requisitos de fase

14. `Agent Principal — identidade de máquina p/ agentes IA` (fusão com A2 / costura B)
15. Os três itens de webhook da fase 0a (§4)
16. `Config Consolidation / HTTP Propagation` e `Frente 3 — eliminar seeds` — o catálogo
    `mcp_servers` da fase 2b aterrissa aqui
17. `Agent-registry — unificar binding skill↔pool` e `Deploy de skills — cleanup de campos órfãos`
18. `Tópicos Kafka órfãos` e `Eventos — três superfícies para duas ideias` — a costura E congela
    contrato Kafka
19. `Masking — 5 mecanismos` — retenção de PII no n8n aumenta a urgência

### Bloco 4 — fosso, confirmação rápida por doc

20. Journey (3 níveis; F4/F5 do histórico unificado; modelo journey/session/segment), Cliente 360 e
    histórico pós-H5, Capacidade e licenças, Resolvedor de Identidade Fase C, Audit LGPD (o n8n
    **aumenta** a urgência — PII fora do regime de masking), Arco de segurança pool-scoping,
    Quality Ingest (concerns abertos), Arc 15 WebRTC, **Scheduler / Outbound — resíduos** e
    **Migração dos timers legados**.

> **Duas hipóteses da revisão 2 já foram derrubadas pelo próprio doc mestre**, e não precisam ser
> testadas de novo: *"refinamentos de Outbound congelam porque o drain/pacing sai"* — o §10.1 mostra
> que o pacing nunca morou no `mailing-api` (é a agenda; o laço é o step `loop`); e *"`scheduler-api`
> sai"* — o §10.2 fechou o caso a favor de ficar. Ambos viram **Segue**.

⚠️ **Esta ordem saiu de TÍTULOS**, e o `TODO.md` tem uma seção inteira avisando que o título é o que
mais mente. Serve para ordenar, não para classificar.

---

## 7. Entregável esperado

1. **`docs/product/n8n-triagem-2026-08-17.md`** — a tabela de triagem completa, e a **casa do
   abortado**: `item → balde → âncora → justificativa de uma linha`.
   - **âncora** = a fase do §11 do doc, quando o item é pré-requisito; a palavra `fosso`, quando não
     é. Sem essa coluna, "Segue" colapsa dois regimes diferentes — fosso sem data e caminho crítico
     datado — e vira sinônimo de "não mexi".
   - O item abortado é uma **linha desta tabela**, com a razão. Não se cria seção de abortados
     mantida viva em lugar nenhum: o registro da decisão e o registro do abortado são o mesmo
     artefato.
2. **`TODO.md` atualizado** — itens abortados saem; os marcados `defeito` no passo 0 são
   reorganizados como defeitos, não como direção.
3. **`CLAUDE.md` § `Pending (Next Iteration)` atualizado.** A seção está poluída de ✅ (Arc 6 Fase 2,
   Quality Ingest, Outbound), contra a regra do próprio arquivo. Limpar **só o que a triagem tocar** —
   varrer a seção inteira é escopo à parte. Risco concreto se não fizer nada: item concluído dentro de
   uma seção chamada *Pending* ser triado como trabalho em aberto.
4. Lista curta de itens **promovidos**, com a nova dependência explicitada (§4 já tem cinco).
5. **Correções no doc da linha mestra.** Duas já nomeadas e devidas: a tabela do §10 sobre
   `workflow-api`/`skill-flow-worker` (quadro do §6), e a linha 107 do `CLAUDE.md` sobre o ADR de
   skip-logic (§4). Se a triagem revelar outros conflitos, **corrigir o doc** — descobrir que a
   especificação pediu a coisa errada é resultado válido, não desvio.

---

## 8. Regras de método para a sessão

- **Nada de veredicto por prosa** — mas com assimetria, senão a §1 (não carregar `docs/`) e esta
  regra se contradizem e a sessão ou estoura contexto ou pula a verificação em silêncio:

  | Balde | Exigência |
  |---|---|
  | **Aborta** e **Escopo reduzido** | Confirmação **no código**, citando `arquivo:linha` |
  | **Segue** e **Congela** | Pode ir por doc |

  A assimetria vem de errar-mantendo ser barato e errar-cortando ser caro. Doc que descreve config
  não é a config — e três documentos já discordaram do código nesta mesma investigação (o ADR de
  skip-logic, a §5.5 do doc mestre e o índice do `CLAUDE.md`).
- **Desconfiar do item que parece razoável**, não do que parece errado.
- Se um item não puder ser classificado sem medir algo, o balde é **Congela** com a medição nomeada
  — nunca *Aborta* por conveniência.
- `/compact` ao fechar cada bloco de itens; não esperar estourar.

---

## Referências

- `docs/product/n8n-interop-boundaries-and-seams.md` — a linha mestra
- `TODO.md` § "Interop com n8n — alvo: eliminar o editor de fluxo local"
- `docs/adr/adr-mcp-interception-single-border.md` · `docs/adr/adr-a2a-server-binding.md`
- `docs/adr/adr-dialog-conditional-skip-logic.md` — a guarda que passa a ser load-bearing.
  **Aceito + implementado (2026-07-08)**, apesar do índice do `CLAUDE.md` ainda dizer *proposto*
