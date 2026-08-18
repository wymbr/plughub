# Interop com n8n — arco ABORTADO, e a direção que o substitui

> **Status:** decisão de reversão — 2026-08-18
> **Reverte:** a decisão de direção de 2026-08-17 registrada em
> [`n8n-interop-boundaries-and-seams.md`](n8n-interop-boundaries-and-seams.md),
> [`n8n-triagem-2026-08-17.md`](n8n-triagem-2026-08-17.md),
> [`n8n-plano-execucao.md`](n8n-plano-execucao.md) e no `TODO.md` §"Interop com n8n".
> **Decide:** que o alvo *"todo skill associado a um pool passa a ser autorado no n8n; o editor
> de fluxo local sai por completo"* **não será perseguido**, e o que ocupa o lugar dele.
> **Não decide:** o destino da costura C — ver §7, é a única decisão deixada em aberto de propósito.

---

## 1. A decisão, em uma frase

**A autoria de fluxo fica em casa; a fronteira se abre por protocolo padrão, não por integração
com um fornecedor.** Em vez de exportar a orquestração para o n8n, o PlugHub se expõe como
**servidor A2A** (uma borda, N consumidores) e **constrói o editor gráfico que faltava**.

Os três documentos acima passam a ser **histórico**. Nenhum deles deve ser citado como contrato,
plano ou justificativa. Este documento é o registro do abortado, conforme a regra do `TODO.md`:
*"o registro da decisão e o registro do abortado são o mesmo documento"*.

---

## 2. Por que o arco foi abortado

Cinco razões, em ordem de peso. Nenhuma delas é "o n8n é ruim" — o n8n é bom no que faz, e é
por isso que a §3 mantém uma porta aberta para ele.

### 2.1 A justificativa adotada não sobrevive à sua própria consequência

O §0 do documento mestre escolhe explicitamente a justificativa **do editor** ("ter um editor
melhor") sobre a de "parar de duplicar", e reconhece que a segunda não se sustenta porque a §10
mostra que a maioria do código sai *ficando*.

Mas a justificativa do editor autoriza **trocar o editor**, e o alvo escolhido foi **exportar a
orquestração**. São coisas diferentes, e a segunda não decorre da primeira. Um editor
insuficiente é argumento para construir um editor melhor — não para transferir o motor.

### 2.2 O motivo não aparece em nenhum documento de venda

Varredura de `value-proposition.md`, `target-audience.md` e `competitive-analysis-2026-07.md`:
**editor de fluxo, low-code/no-code e "facilidade de autoria" não aparecem como fator de compra
nem como objeção registrada.** As personas compradoras são Head de Contact Center, CTO/Head de
IA, CFO, CISO/DPO e integrador. Não há "analista de negócio" nem *citizen developer*.

O editor local não está perdendo venda hoje, porque não está no discurso. O arco consumiria a
capacidade de execução de vários trimestres para um ganho que os documentos de produto não
conseguem nomear.

### 2.3 O alvo degradaria capacidades que o produto vende como diferencial

Todas medidas e admitidas no próprio documento:

| Capacidade | O que aconteceria | Onde está admitido |
|---|---|---|
| **Avaliação tier-2 de IA** | degrada **em bloco** para grau-transcript até os mapeadores existirem — a mesma limitação que o quality-ingest documenta para histórico *alheio* | §5.4 |
| **Masking** | *"o PlugHub faz o valor transitar sem pousar; o n8n é construído para tudo pousar — arquiteturalmente opostos"*; reintroduz por fora o vazamento que a decisão R7 recusou por dentro | §6.3, §8 |
| **Latência de turno** | com 100% dos skills migrados, *todo turno conversacional atravessa o n8n*, e a estimativa de 5–8 travessias é declaradamente palpite | §6.2 |
| **Fronteira de orquestração** | o §22 do descritivo já diz que as capacidades diferenciadas são do skill-flow nativo e que o agente externo *"participa, não orquestra"* — o alvo move a orquestração para fora dessa fronteira | §5.3 |

Sacrificar o módulo de qualidade é especialmente caro: ele é o argumento contra os *outcome
players*, que a análise competitiva ataca justamente por **taxa autorreportada**.

### 2.4 O modelo comercial entra em conflito, e isso não está em nenhum risco listado

O n8n cobra **por execução**. O §21 do descritivo vende previsibilidade por capacidade, num
quadro em que o PlugHub é o único "Alta" contra Agentforce, Gemini e Fin. Pôr um motor cobrado
por execução no caminho de todo turno conversacional ataca o argumento comercial na raiz.

O §13 (Riscos) do documento mestre não menciona isso.

### 2.5 A mitigação declarada para o risco "n8n" é o oposto do alvo

A análise competitiva já registra *"n8n comoditiza orquestração no mid-market"* como risco de
médio prazo, com mitigação declarada: **"subir em compliance enterprise + verticalização"**.

O alvo fazia o contrário — descia a orquestração para o comoditizador.

---

## 3. O que ocupa o lugar

### 3.1 A2A server binding, promovido de "proposto" a direção

[`adr-a2a-server-binding.md`](../adr/adr-a2a-server-binding.md) resolve o mesmo problema de
fronteira com propriedades melhores:

- **Uma implementação, N consumidores.** LangGraph, CrewAI, Copilot Studio, orquestrador do
  cliente — e o n8n entre eles. A integração com um fornecedor resolvia um.
- **Não toca a autoria.** O motor, o `pipeline_state`, o masking e a avaliação tier-2 ficam
  intactos. Nenhuma das quatro degradações da §2.3 acontece.
- **É binding, não motor.** O ADR mediu que `Task`↔sessão, `contextId`↔`root_session_id`,
  `input-required`↔`suspended`+resume token já existem as-built. Routing, capacidade, bridge e
  engine **não mudam**.
- **O argumento de venda já está escrito**, no §7 do próprio ADR: *"o fosso continua sendo fila +
  capacidade + qualidade medida + auditoria — que é justamente o que um endpoint A2A de framework
  não tem"*. Um AgentCard de framework devolve uma função; o nosso devolveria **uma fila com
  gente atrás, SLA, masking por turno e nota de qualidade**.

**Fases A0→A6 do ADR seguem como estão.** Duas emendas desta decisão:

- **D11 substituído** pelo [`adr-pool-no-resource-policy.md`](../adr/adr-pool-no-resource-policy.md)
  — a graça de espera vira config de pool (`on_no_resource`), e o binding só traduz.
- **D9 (cota por `a2a_client`) sai do v1.** A contenção é `allowed_pools` (D6) + pool dedicado +
  `deployed_max_concurrent_sessions` + `on_no_resource: reject`. Reabre se aparecer um segundo
  caller disputando o mesmo pool.

### 3.2 Editor gráfico próprio — construído pela alavanca certa

O diagnóstico original estava correto: o editor atual é insuficiente e um editor sem investimento
apodrece. A conclusão é que ele precisa de investimento, não de substituto.

**O que faz o n8n parecer bom não é o canvas — é rodar e ver o dado passando nó a nó.** Canvas é
commodity (React Flow/xyflow), e a parte difícil já existe: `validateFlow` faz adjacência fechada
e guarda de ciclo.

**A alavanca é execução observável, e o substrato é melhor que o do n8n:** `pipeline_state`
persiste a cada transição de step (invariante), o Session Replayer reconstrói contexto, o
Record/Replay Harness está especificado. Um editor que roda o fluxo com dados de teste, mostra o
`pipeline_state` passo a passo e **replaya uma sessão real dentro do canvas** não é paridade — é
superior, e é derivado de coisa que já existe.

**Duas frentes reduzem o escopo do editor e continuam valendo por si:**

- A direção *"config + interpretador genérico"* do §5.3 do documento abortado **sobrevive
  inteira e não depende do n8n**. O `agente_nps_v1` é `form_get → menu → notify → complete` — não
  é fluxo, é config de runner. Quanto mais skills viram config, menos superfície o editor cobre.
- O **editor de DialogForm** (`/config/dialog-forms`) já existe e é onde mora o conteúdo
  conversacional. A guarda do `ask_when` (sem control-flow no form) fica **mais** load-bearing,
  não menos — a pressão para empurrar branching ao formulário existe com ou sem n8n.

### 3.3 Distribuição, se e quando for perseguida, é node **sobre A2A**

Se a tese de acessar a base instalada do n8n for perseguida, o caminho é um **node verificado
publicado por nós, que fala A2A com o nosso servidor** — não uma integração bespoke. Medido em
2026-08-18: no n8n, A2A só existe como *community node* de terceiros
(`n8n-nodes-agent2agent`, `n8n-nodes-a2a-protocol`), e node não-verificado **não roda no n8n
Cloud**.

**Mas o gargalo da distribuição não é costura, é go-to-market**, e nada disso estava no arco
abortado: auto-serviço (não há signup nem provisionamento de tenant; o isolamento multi-tenant é
o item nº 1 de PoC pelo §24.3), tier de entrada com unidade compatível, e empacotamento do node
verificado (publicação via GitHub Actions com *provenance*, exigida desde 01/05/2026).

⇒ **A distribuição é decisão de produto separada, com dono próprio, e não é pré-requisito de
nada acima.**

---

## 4. Reclassificação — o que morre, o que muda de dono, o que nunca foi n8n

> **Regra que governa esta seção:** *"nome de pacote não era unidade de decisão"* — a lição que a
> própria §10.4 do documento abortado registrou. Matar por associação de nome repetiria o erro
> que ele corrigiu. Vários itens entraram na fila do n8n sem serem sobre n8n.

### 4.1 MORRE — é o alvo, e some com ele

- Alvo *"100% dos skills autorados no n8n"*; migração dos perfis `workflow` e `agent`.
- Morte do editor `agent-flow` e do bloco de steps de todo skill.
- **Costura B** (n8n como cliente MCP) — **absorvida** pelo principal externo do A2A (fase A2).
  Não construir dois mecanismos era invariante do próprio plano; agora só resta um.
- **Frente 7 — mapeadores `flow_definition`/`pipeline_state` ← n8n.** Deixa de existir: nada
  degrada, porque a trajetória continua sendo produzida em casa. *(O insumo "trace de execução"
  sobrevive só como entrada do Record/Replay — §4.3.)*
- **Gate de latência (3-gate).** Sem travessia por turno, não há o que instrumentar por este
  motivo.
- **Fachada OpenAI no ai-gateway** como *requisito*. Existia para o AI Agent node do n8n usar o
  gateway. Sobrevive como conveniência de baixa prioridade, não como fase.
- **Portabilidade com o JSON do n8n como alvo** (`skill-extract` → n8n).
- **Costura D como "disciplina de propagação de `root_session_id`"**. A propagação continua sendo
  requisito — mas do binding A2A (`contextId`), não de um template de fornecedor.

### 4.2 MUDA DE DONO — o trabalho fica, a justificativa é outra

| Item | Novo dono | Por quê |
|---|---|---|
| **Fase 1 — superfície de resultado honesta** (status de 3 estados, artefato buscável) | **A3 do ADR de A2A** | É literalmente a mesma entrega. O `get_status` que responde `"closed"` quando a chave não existe é defeito hoje, com ou sem n8n |
| **Costura A + E** (webhook de entrada, Kafka Trigger) | **já existem** | Nada a construir. Continuam disponíveis a qualquer consumidor externo |
| **Costura D** (node/template) | **§3.3** — distribuição, sobre A2A | Deixa de ser fase de um arco de interop |
| **Frente 6 — promover o interpretador genérico a serviço de código** | **Editor / config+runner (§3.2)** | Era bloqueante da fase 5; vira redutor de escopo do editor. Ganha, não perde, com a reversão |

### 4.3 NUNCA FOI n8n — permanece na fila, com prioridade inalterada ou maior

Matar estes por associação seria o dano real desta reversão.

- **Fase 0a — a rota anônima `POST /v1/channels/webhook/pool/{pool_id}`.** O plano de execução
  mediu que ela *"não é anônima e sem uso — ela é anônima e é o barramento interno de disparo do
  produto"*, com três chamadores de produção (`scheduler-api/…/dispatcher.py:98`,
  `orchestrator-bridge/…/main.py:1337`, `mcp-server-plughub/src/tools/workflow.ts:176`) e
  `tenant_id` vindo do **corpo** (`channel-gateway/…/main.py:1037`) — cross-tenant por construção
  assim que a superfície for publicada. **Achado de segurança independente**, e o D7 do ADR de
  A2A o exige de qualquer forma.
- **Fase 2a — promover `skill-flow-service` a pacote de primeira classe.** O runtime de produção
  dos skills mora em `packages/e2e-tests/services/`, com cabeçalho que se declara *"Thin HTTP
  wrapper … for E2E testing"*, sendo dependência `service_healthy` de três serviços. É achado de
  due diligence, e o descritivo se vende para due diligence.
- **Catálogo `mcp_servers` + remoção do fallback silencioso.** `MCP_SERVER_{NOME}_URL` é
  impopulável pela UI e está vazia; `skill-flow-service/src/index.ts:142-144` roteia servidor
  desconhecido para o `mcp-server-plughub` em vez de falhar, e
  `agente_contexto_ia_v1.yaml:96` aponta para um `mcp-server-crm` que **não existe** — o erro
  sai como "tool desconhecida". Viola a Postura de Engenharia com ou sem n8n.
- **Os 13 defeitos colaterais da §14**, que o próprio documento marca *"independentes do n8n"*:
  `llm_tokens_*` não emitido em `/v1/reason`; hint de backfill que mente; `schedule` JSONB morto;
  `MCP_PROXY_URL` para serviço inexistente; DECR de `hook_pending` sem inspecionar outcome;
  `inline` com dois significados; assimetria de permissão entre bordas; `evaluateAskWhen`
  triplicado; `masked_input_fields` sem escritor; `EventsView` pedindo `period=24h` a endpoint
  que só aceita `from_dt`/`to_dt`; `spawn_reason` sem amostras de `collect`/`delegate`.
- **D.1 — `sequence_index` calculado e nunca persistido** (`orchestrator-bridge/main.py:915`),
  quebrando 5 `argMax` em `reports_query.py:2183-2209`.
- **D.2 — seis serviços rodando em WARNING**, com `logger.info` sumindo.
- **Record/Replay Harness.** Sobrevive com justificativa própria: gate de promoção. Perde a
  justificativa *"é o único que pega a avaliação tier-2 apagando"* — porque ela deixa de apagar.

### 4.4 DESCONGELAM — 14 itens

Os 14 itens no balde **Congela** da triagem de 2026-08-17 estavam congelados *"até o gate da fase
3"*. **Esse gate não existe mais.** Voltam à triagem normal, sem prioridade herdada:

autenticação de endpoint webhook · Frente 3 (eliminar seeds) · binding skill↔pool · deploy de
skills cleanup · busca GIN · capacidade/licenças defeito C · isolamento por `origin` · timers
legados · Usage Metering · Pricing · costura de aquisição · Dashboards catálogo · hardening de
Auth Console · Arc 15 WebRTC.

### 4.5 REEXAMINAR — 9 itens em "Escopo reduzido"

Nove itens foram reduzidos com a razão *"esta parte vira template n8n"*. Sem n8n, a redução perde
o fundamento e precisa ser refeita item a item — notadamente o nível (a) de **Business in Any
Media** (fluxo negocial channel-abstract), o **contrato delegate-por-pool**, o **intake-flow**, e
**Customer Surveys S2/S7**.

⚠️ **Não é "volta tudo".** Alguns cortes eram bons por mérito próprio (o trio de skills YAML que
nenhum pool deploya, por exemplo). Reexaminar significa julgar de novo, não reverter em bloco.

### 4.6 SEGUEM ABORTADOS — 4 itens, por mérito próprio

`skill-flow-worker` · step de expressão sandboxed · hot-reload de skill YAML em disco · o trio de
skills YAML sem pool.

A própria triagem registrou: *"nenhum deles foi abortado por 'o n8n cobre'"*. Esta reversão não os
ressuscita. O `skill-flow-worker` em particular tem quatro das cinco saídas HTTP em 410 e a quinta
apontando para rota inexistente.

---

## 5. Efeito no `TODO.md`

1. Remover a seção **"Interop com n8n — alvo: eliminar o editor de fluxo local"** (linhas ~260-336),
   substituindo por um ponteiro de uma linha para este documento.
2. Reescrever o cabeçalho **"⚠️ Triagem de 2026-08-17"** (linhas 11-19): a triagem deixa de ser o
   filtro vivo do backlog e passa a ser insumo histórico. Os baldes `Congela` e `Escopo reduzido`
   não valem mais como estão (§4.4, §4.5).
3. Mover para as seções de destino, **sem perder prioridade**: os itens da §4.3.
4. Abrir duas frentes novas: **A2A server binding** (ponteiro para o ADR) e **Editor de fluxo —
   execução observável** (§3.2).
5. Marcar os três documentos de n8n como histórico, com aviso no topo de cada um.

---

## 6. Riscos desta reversão — o outro lado

Registrados porque a decisão é de *construir* em vez de *integrar*, e isso tem custo real.

| Risco | Sinal de que aconteceu |
|---|---|
| **É mais trabalho, não menos.** A2A completo + editor próprio excede o arco de n8n em esforço | O trimestre fecha sem nenhuma das duas frentes entregue |
| **O apodrecimento do editor volta a ser possível** — o diagnóstico original estava certo | Passam dois trimestres sem commit em `agent-flow`; a frente vira "fallback sem investimento", que o documento abortado proibia com razão |
| **A2A pode comoditizar como o MCP comoditizou** | O §7 do ADR já antecipa; a defesa é tratá-lo como binding e manter o fosso onde ele está |
| **Nenhuma demanda comercial nomeada** para pool humano via A2A | A fase 2 do ADR permanece fora de escopo até existir; investir nela antes é construir para hipótese |
| **A base do n8n fica inacessível** se a distribuição não for perseguida separadamente | Nada acontece — é justamente por isso que a §3.3 tem dono próprio |

**O melhor argumento do lado abortado**, preservado para quem reabrir: um editor caseiro é imposto
permanente que nunca vence os editores de mercado, e cada mês investido nele é mês não investido
no fosso. Isso continua verdadeiro. A resposta desta decisão é que ele argumenta por **encolher**
o editor (config + interpretador genérico, §3.2) — não por exportar o motor.

---

## 7. A decisão que este documento **não** toma

**A costura C — n8n como domain MCP server governado — não é abortada aqui.** É a única costura
que aponta na direção contrária (PlugHub chama o n8n), não toca a autoria, e é a que o próprio
documento marcava como **maior retorno**. O argumento de produto é forte e independente de tudo
acima:

> Não se compra conectores — compram-se **conectores governados**. Centenas de integrações
> prontas, cada chamada com validação de permissão, guarda de injeção e `mcp.audit`.

Isso ataca um buraco real: não existe catálogo de integrações em nenhum documento de produto, e
"integra com meu ServiceNow/SAP?" é pergunta de primeira reunião.

**Recomendação:** não matar por associação de nome. Requeue como **arco próprio**, com dono e
gate de demanda, e com dois pré-requisitos que já estão na §4.3 de qualquer forma (catálogo
`mcp_servers`, fim do fallback silencioso) mais o gate de compliance do §8 do documento abortado
(retenção de execução desligada quando a tool recebe PII).

Se a decisão for matar isso também, **é decisão explícita a tomar**, não consequência automática
desta reversão.

---

## 8. Referências

- Substituídos (histórico): [`n8n-interop-boundaries-and-seams.md`](n8n-interop-boundaries-and-seams.md) ·
  [`n8n-triagem-2026-08-17.md`](n8n-triagem-2026-08-17.md) ·
  [`n8n-plano-execucao.md`](n8n-plano-execucao.md) ·
  [`n8n-backlog-triage-kickoff.md`](n8n-backlog-triage-kickoff.md)
- Substituem: [`adr-a2a-server-binding.md`](../adr/adr-a2a-server-binding.md) ·
  [`adr-pool-no-resource-policy.md`](../adr/adr-pool-no-resource-policy.md)
- Base de produto: [`plughub-descritivo-tecnico-funcional.md`](plughub-descritivo-tecnico-funcional.md)
  (§5.4, §6.3, §21, §22, §24.3) · [`value-proposition.md`](value-proposition.md) ·
  [`target-audience.md`](target-audience.md) ·
  [`competitive-analysis-2026-07.md`](competitive-analysis-2026-07.md)
- Medições externas (2026-08-18): [n8n community nodes verificados / n8n
  Cloud](https://docs.n8n.io/integrations/community-nodes/installation/verified-install/) ·
  [submissão e provenance](https://docs.n8n.io/integrations/creating-nodes/deploy/submit-community-nodes/) ·
  nodes A2A de terceiros ([agentic-layer](https://github.com/agentic-layer/n8n-nodes-a2a),
  [pjawz](https://github.com/pjawz/n8n-nodes-agent2agent))
