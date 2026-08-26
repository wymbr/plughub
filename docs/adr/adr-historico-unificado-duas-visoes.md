# ADR — Histórico unificado: duas visões sobre um só substrato

**Status:** **F0 · F1 · F1b · F2 · F3 · F4 · F5 ✅** — arco de fases COMPLETO (as-built e achados por
fase no `CHANGELOG.md`; F4 em 2026-08-25, **F5 em 2026-08-26**). Aberta só a **lente C** (faixas por
personagem), **SUSPENSA por decisão do dono em 2026-08-26**: não é essencial e traz risco de desenho
(ver §"Riscos de C" no D6 — colisão intra-faixa no fan-out, e o cliente não ter segmento). Plano em
[`../product/historico-unificado-plano-execucao.md`](../product/historico-unificado-plano-execucao.md).
**Supersede parcialmente:** a suposição, implícita em `/analise/sessions` × `/analise/processos`, de que contato e processo são objetos de telas diferentes.
**Não altera:** [`adr-journey-session-segment-model.md`](adr-journey-session-segment-model.md) — os três níveis e a natureza derivada da journey permanecem exatamente como estão.
**Origem:** [`../product/journey-contatos-em-sequencia-handoff.md`](../product/journey-contatos-em-sequencia-handoff.md).

---

## 1. Contexto

O pedido original: *"vejo journey e sessions como partes de um histórico… a ideia do journey é estender
este modelo para vincular todos os contatos relacionados e poder visualizar o que cada personagem
interagiu em cada sessão/contato, em ordem cronológica"*.

Isto não pede entidade nova. `segment` → `session` → `journey` já é o modelo, e a journey já é derivada
de (proveniência ∪ alias). O que falta é **superfície**.

O diagnóstico que emergiu ao longo da discussão, e que define o tamanho deste ADR:

> Em quatro pedidos consecutivos — direção do acesso, prova de saída, perna do workflow, output com
> confirmação — o fato pedido **já está persistido** e não tem superfície. Um deles está persistido e
> **desligado por um gate assimétrico**. Nenhum exigiu modelo de dados novo.

Consequência de escopo: este é majoritariamente um ADR de **exibição**, mais dois carimbos pequenos e
um conserto de gate.

---

## 2. Decisões

### D1 — Um histórico, duas visões

`/analise/sessions` e `/analise/processos` colapsam num módulo só.

| Visão | Unidade da linha | Para quê |
|---|---|---|
| **1 · Contatos** | contato (não relacionados) | achar contatos por filtro |
| **2 · Processo** | contatos relacionados | ler um caso ponta a ponta |

Justificativa: se a journey é derivada e a maioria tem um contato só, ela não é outro objeto — é um
**escopo** sobre o mesmo substrato. Duas telas para o mesmo dado eram acidente histórico.

### D2 — Processo é pivô, nunca navegação livre

A visão 2 **não tem lista própria de processos**. Chega-se a ela pivotando de um contato.

Corolário obrigatório: **lista de processos só existe escopada por atributo de contato**
(`customer_id`, `open`) — nunca como navegação livre. É isso que mantém o filtro sempre no nível de
contato e evita o impasse "filtro é de contato, linha é de processo", em que filtrar por pool devolveria
*journeys que tocaram o pool* em vez de *contatos daquele pool*.

Duas consequências que caem de graça:

- `significant_only` fica **irrelevante** (não há lista para filtrar). Como a maioria das journeys tem
  1 contato, mantê-lo como default `true` numa lista de processos esconderia justamente o caso comum.
- O custo do `GROUP BY` com union-find **evapora**: um processo por vez, sob demanda, em vez de
  agregação na lista default.

O fetch direcionado já existe e é isento de `significant_only` **e** da janela de data:
`GET /reports/journeys?root_session_id=…`.

### D3 — O processo aparece como chip na linha de contato

O processo nunca é linha; é **propriedade de uma linha de contato** e destino de pivô. Contato de
processo único não recebe chip — não há para onde pivotar, e esse é o caso majoritário.

- **O chip conta o processo inteiro, não a fatia filtrada.** Janela que pega 2 de 3 contatos mostra
  `· 3`. É correto e vai parecer defeito: exige rótulo explícito.
- Computado sobre a **página retornada** (`GROUP BY root_session_id` sobre ~200 linhas), no mesmo passe
  que já resolve `_journey_resolved_map`. Sobre a janela inteira seria caro.
- 🆕 **"Processo inteiro" tem um limite que esta decisão não previu: a ABAC** (emenda de 2026-08-26,
  implementada — ver `CHANGELOG.md`). A contagem é feita SOB `accessible_pools`, de propósito — contar
  os membros que o operador não alcança revelaria o tamanho de um processo que toca pools fora do
  escopo dele. A consequência não prevista era a linha acima (*"processo único não recebe chip"*)
  passar a valer para o processo que é único **apenas aos olhos de quem olha**: com `1`, o chip sumia
  e a tela **afirmava** *"este contato não pertence a processo nenhum"*. A regra da D3 foi escrita
  para o processo REAL e aplicada ao processo RECORTADO.
  **Emenda:** o chip publica também `journey_has_scoped_out_members` — **existência, nunca tamanho** —
  e pivota com `PRC-xxxx · 1+`. Sob o marcador a quebra da D4 **não** é desenhada: ela também é
  escopada, e uma classe inteira fora do alcance apareceria como `0`, afirmando *"não há etapa
  interna"*. Medido no `tenant_demo`: 6 processos de 4/3/4/4/4/5 sessões chegavam como `1` a um
  operador escopado. Gate: `infra/test/probe_process_chip_scoped_marker.sh` (exige usuário de escopo
  estreito; com escopo largo sai INCONCLUSIVO, porque não exercita o caso).

### D4 — Duas classes de linha: acesso × etapa interna

Tratar as sessões-membro como pares é o que fazia o processo parecer que não respondia à pergunta.

| Classe | Tem | Papel |
|---|---|---|
| **Acesso do cliente** | direção (inbound/outbound), canal, endereço, par entrada→saída | o que o cliente percebe — **protagonista** |
| **Etapa interna** | pool, skill, participantes de agente | maquinaria entre acessos — **dobrada por default** |

Taxonomia de contato, que decide o que cada linha precisa mostrar: **com cliente · com agente interno ·
em espera**. O caso "em espera" (sessão que existe só para aguardar) precisa de rótulo honesto
(*"aguardando retorno do cliente · sem interação"*), senão lê como sessão quebrada no meio do processo.

No cenário de referência (limite de crédito), a leitura correta é:

```
acesso 1 · inbound WebChat   → emitiu saída · flow_complete
   ⋯ 2 etapas internas (análise, entrega/parking)
acesso 2 · inbound WebChat   → emitiu saída · flow_complete
acesso 3 · inbound WebChat   → emitiu saída · flow_complete
```

**Armadilha registrada:** o processo tem 3 sessões e o cenário tem 3 acessos do cliente. **Não são os
mesmos três.** Hoje, das 3 sessões da journey, **uma só** tem interação com o cliente (a raiz); a
análise é entre workflow e aprovador, e a entrega é parking sem ninguém. Os acessos 2 e 3 são contatos
novos, fora da journey até o `journey_merge` (F1).

### D5 — Segmento é a folha

A linha do tempo para no segmento. Mensagens só no drill de **um** contato, via `SessionTranscript`, que
já existe e já é masked-by-construction.

**Não haverá transcript fundido atravessando contatos.** Ele exigiria reconciliar três regimes de
visibilidade (`all` do intake, `agents_only` + PII mascarada da análise, `all` da entrega) e obrigaria a
um ADR de masking próprio. Fatiando por segmento, o valor principal sai fora do caminho da decisão cara.

Metadado de mensagem (contagem, timestamp, `author_role`, `visibility`) **não** é conteúdo e pode ser
usado — ver D9.

### D6 — Lentes: A e B são um componente; C é destino

Com `started_at` na linha (custo zero — já vem no shape do segmento), **A e B deixam de ser dois
modelos**: viram o mesmo componente com dois eixos de ordenação.

| Lente | Esqueleto | Status |
|---|---|---|
| **A · árvore** | proveniência (indentação por `origin_session_id`) | v1 |
| **B · cronologia** | ~~ordenação por `started_at`, contato como cabeçalho de grupo~~ → **ordem global estrita por `opened_at`, sem agrupamento** | v1 — mesmo componente, toggle. **Emendado 2026-08-25** |
| **C · faixas por personagem** | participante × tempo | **destino registrado** |

> ⚠️ **Emenda medida na tela (2026-08-25): "ordenar por tempo" e "contato como cabeçalho de
> grupo" eram duas coisas, e juntas se anulavam.** Com o agrupamento, a maquinaria fica presa ao
> acesso que a originou — uma etapa das 17:11:31 renderizava ACIMA de um acesso das 17:10:52 — e,
> com as internas dobradas (o default), **as duas lentes produziam exatamente as mesmas linhas**,
> porque os acessos já saem em ordem na caminhada da árvore. O dono perguntou se o toggle "por
> enquanto não faz nada": fazia, e a diferença era a indentação de um neto.
>
> **Um controle que não muda nada no caso comum é indistinguível de um controle quebrado** — a
> mesma família do seletor «Inbound/Outbound» que a F3 removeu por não filtrar nada. A lente B
> passou a ser ordem de tempo e só; o agrupamento por acesso não se perde, é a lente A ao lado.

Refinamento de leitura, barato: além do `started_at` absoluto, exibir **offset relativo à abertura do
processo** (`+7m54s`). Dois timestamps absolutos em níveis de indentação diferentes são difíceis de
comparar de olho; o offset faz o aninhamento saltar.

Na lente C, **a faixa é a identidade do personagem, não o segmento** — `user_id` para humano,
`(pool_id, flow_id)` para IA. Isso *colapsa* cardinalidade em vez de multiplicá-la, e entrega de graça o
segundo eixo (*"o que a Ana fez neste processo inteiro"*). C não exige backend adicional além de D10.

Riscos de C, registrados para quando for construída:
- **Colisão intra-faixa** no fan-out (N workers do mesmo skill em paralelo): duas barras da mesma
  identidade se sobrepõem dentro da lane e o gantt não renderiza. Exige split em sub-faixas.
- **O cliente não tem segmento.** Ele é a **banda do contato**, não uma faixa com atividade. Com D5, o
  eixo Y de C é rigorosamente "agentes", não "personagens" no sentido pleno.

**Sobreposição existe em dois níveis, e a árvore esconde os dois.** Segmentos se sobrepõem (`@mention`
paralelo ao primary é rotina; especialista nasce dentro da janela do pai; hooks posatt são paralelos
entre si) — e **contatos também**: no cenário de referência, o acesso 2 acontece enquanto a análise está
aberta. A lente A mostraria a consulta como irmã, sem dizer que rodou dentro da janela da análise.

> Herda o invariante: **nunca somar segmentos** para obter duração de processo. Usar `elapsed_time_ms`,
> jamais `Σ agent_time_ms`.

### D7 — Personagem: quem atua, e por onde se entrou

| Personagem | Identidade | Fonte |
|---|---|---|
| Cliente | `customer_id` | `sessions.customer_id` + a banda do contato |
| Agente humano | `user_id` / `user_login` | `segments` |
| Agente IA / workflow | `pool_id` + `flow_id` | `segments` |
| Sistema de entrada | canal + endereço discado | `sessions.channel` + carimbo de F1 |
| Sistema que retomou | `resume_origin` | `session_transitions` (existe, invisível) |

~~**Premissa aceita:** a plataforma só permite interação com sistemas externos via **webhook cadastrado**.
Com a Fase E do `ChannelEndpoint` (*"o registro é agora o único resolvedor; não resolveu ⇒ a sessão não
nasce"*), toda entrada externa tem linha no registro, logo id estável e rótulo de exibição.~~

> ⚠️ **Premissa FALSA — medido 2026-08-12.** A Fase E existe **só no webhook** (`main.py:1399-1406`).
> `voice` (`adapters/voice.py:283-287`) e `webchat` (`main.py:517-519`) consultam o registro mas caem em
> pool default quando não resolve; **`whatsapp`, `sms` e `email` nunca consultam** (usam `*_default_pool_id`);
> `webrtc` (`adapters/webrtc.py:1443`) resolve com `channel="webrtc"`, ausente do enum de `ChannelEndpoint`
> (`schemas/src/channel-endpoint.ts:11-18`), logo **nunca casa**. `channel_endpoints` tem 13 linhas de webhook
> e 2 de webchat; os demais canais têm zero.
>
> Além disso, o endereço morre **antes do evento**, em dois pontos: `ResolvedEndpoint`
> (`endpoint_resolver.py:76-90`) devolve só `pool_id`, sem `id` nem `identifier`; e
> `NormalizedInboundEvent`/`ContactOpenEvent` (`channel-gateway/models.py:209-237`) não têm campo de endereço.
>
> **Isto é o que torna D12b possível:** como o endereço custaria três camadas mais seis adaptadores e o
> pool o substitui sem perda medida (M2 = 1:1), o carimbo de `endpoint_id` sai do arco inteiro.

Escopo da premissa: ela dá identidade ao **inbound**. Não cobre o outbound (PlugHub chamando CRM,
calendar), que segue pelo caminho MCP — ver §5.

### D8 — Direção do acesso é derivada, não armazenada

`spawn_reason` foi criado exatamente para isto (`models.py:140`: *"POR QUE esta sessão existe… NULL numa
sessão de topo (iniciada pelo cliente)"*).

| `spawn_reason` | Direção |
|---|---|
| `NULL` + canal de cliente | **inbound** — o cliente procurou |
| `collect` | **outbound** — a plataforma procurou o cliente |
| `trigger` / `delegate` | nenhuma — maquinaria interna |

Desempate: `customer_id` com prefixo `sys:`, que o gateway carimba quando o disparo não é do cliente.

É fato do contato, derivado dos campos do próprio contato — deriva-se onde o escopo é conhecido, em vez
de guardar num campo mais largo.

### D9 — "Recebeu a saída" nunca se infere de `visibility='all'`

`messages` tem `author_role`, `visibility`, `timestamp`, e permite afirmar *"houve emissão ao cliente"*
sem tocar `content`. Mas **emitido ≠ recebido**, e usar a emissão como prova de entrega mente exatamente
no caso que originou o cenário: a sessão de entrega **parqueia porque o cliente não está lá**; uma
`notify` num contato de onde o cliente já desconectou produz a mesma linha `visibility='all'`.

- **Depois de F0**, a confirmação é **evento observado**: resposta ao `collect`, ou timeout. Três estados
  honestos — confirmado / expirado / abandonado.
- **Onde não houver `collect`**, o par honesto é `(emitiu?, close_reason)`. O domínio já separa
  `flow_complete` de `customer_disconnect` / `customer_abandon` / `customer_hangup`.
- Não existe recibo de entrega em `messages`. Para webchat a mensagem aguarda reconexão no stream; para
  o outbound é pior — a entrega real do link (SMS/e-mail) é trilha declaradamente não construída.

> A verificar antes de construir: o literal que o cliente usa em `author_role`
> (`SELECT DISTINCT author_role FROM plughub_demo.messages`). Foi suposto, não medido.

### D10 — `root_session_id` em `/reports/segments`, com isenção de janela

Hoje o endpoint aceita **um** `session_id` e aplica **sempre** a janela `started_at` (default 7 dias),
sem isenção — ao contrário de `/reports/sessions`, que isenta `origin_session_id`. Uma journey que
atravessa semanas volta silenciosamente incompleta.

Decisão: aceitar `root_session_id`, replicando `_journey_resolved_map` / `_journey_member_roots` e o
padrão `{jroots:Array(String)}` que `_fetch_journeys` já usa, **e isentar a janela quando ele estiver
presente** — mesma decisão já tomada em `_fetch_journeys`.

Rejeitado: N+1 no cliente (14 requests numa journey de 12 sessões, cada uma truncada pela janela) e
`session_ids` CSV (mais barato, mas ainda exige a isenção e não reusa o union-find).

Este único endpoint serve **as três lentes** sem diferença. O esqueleto é escolha de renderer, reversível.

### D11 — Sessões internas escondidas por default, com toggle

Fecha uma divergência real: `_fetch_journeys` aplica `_apply_contact_scope` incondicionalmente
(`session_count: 3`), mas `/reports/sessions?root_session_id=` **não** aplica — o wrap-up destacado
aparece na árvore. Cabeçalho diz 3, tabela mostra 4.

O toggle preserva o valor forense sem quebrar o número. Quando ligado, `internal_session_count` (já
computado) entra no cabeçalho.

### D12 — Filtro por pool passa a significar "atendido por"

`sessions.pool_id` **não é o pool de entrada** — é o último pool que reescreveu a linha
(`ReplacingMergeTree` substitui a linha inteira). Medido: a sessão da análise sai com
`pool_id = aprovacao_credito`, não com o pool de entrada, porque o `delegate` ao pool humano a reescreveu.

Consequência viva: **o filtro por pool já existe e já mente.** Um contato que foi
`limite_ia → aprovacao_credito` não aparece ao filtrar por `limite_ia`.

Decisão: filtrar por `segments.pool_id` (`session_id IN (SELECT … FROM segments WHERE pool_id = X)`),
que responde *"atendido por"* em vez de *"terminou em"*. É mudança de semântica de um filtro existente —
registrada como decisão, não aplicada como correção silenciosa.

Filtros da visão 1: ~~**período · canal · DNIS · pool**. O DNIS depende de F1.~~ — **emendado, ver D12b.**

### D12b — Emenda medida (2026-08-12): o DNIS sai, e o pool vira dois fatos

D12 acertou o diagnóstico e errou o remédio pela metade. Medições em `tenant_demo`:

| # | Medição | Resultado |
|---|---|---|
| M1 | `sessions.pool_id` × pool do primeiro segmento | **46 divergentes em 314** (14,6%), todas orgânicas |
| M1b | os 46, por par | 5 pares, soma exata, **sem resíduo** — não há segundo mecanismo |
| M2 | endpoints por pool (`channel_endpoints`) | **1:1** — 13 webhook/13 pools, 2 webchat/2 pools |
| M3 | canais por pool (dado) | todos `1` — mas por **ausência de amostra**, não por estrutura |

**Filtros da visão 1 passam a ser: `período · canal · entrou por · atendido por`.**

- **`DNIS` sai, e não volta.** M2 mostra 1:1 — nenhum pool tem dois endereços, logo filtrar por pool não
  perde nada que o DNIS daria. E `whatsapp`/`voice`/`sms`/`email` têm **zero** linhas em `channel_endpoints`.
  **Consequência de escopo: F1 fica só com o `journey_merge`** — o carimbo de `endpoint_id` sai do arco.
- **`canal` fica.** M3 mostra todos os pools com um canal só, mas isso mede a ausência de whatsapp no
  ambiente, não a estrutura: 4 pools de `tenant_demo.yaml` declaram `[webchat, whatsapp]`. A assimetria que
  decide não é fragilidade, é preço — tirar o DNIS economiza três camadas e seis adaptadores; tirar o canal
  economiza **zero**, porque `channel` já está preenchido em todas as sessões.
- **`pool` vira dois filtros com nomes distintos.** *"entrou por"* (`sessions.pool_id` com **first-write-wins**)
  e *"atendido por"* (a subconsulta em `segments` que D12 já decidiu). Se os dois se chamarem "Pool" na tela,
  o operador lê um e recebe o outro — o erro que D12 existe para corrigir.

**O carimbo `entrou por` não é campo novo: é parar de apagar um que já existe.** O channel-gateway resolve o
pool antes de publicar `conversations.inbound`, e `parse_inbound` já o escreve (`analytics-api/models.py:119-150`).
`pool_id` **está** em `_IDENTITY_FIELDS`, mas `_learn_session_identity` faz `if value: entry[field] = value`
(`consumer.py:133-136`) — a cache segue o último — e `_inject_session_identity` só preenche ausência
(`:152-154`). O padrão correto já existe no mesmo arquivo para `opened_at` (`:137-139`, `:155-160`), sob o
comentário *"A abertura é imutável"*.

Os 46, nomeados:

```
limite_processo     → aprovacao_credito      19
wrapup_detached_ia  → retencao_humano-int    12
sac_ia              → retencao_humano        12
formfill_demo_ia    → formfill_demo           2
gate_promocao_ia    → aprovacao_deploy         1
```

Quatro pares são maquinaria interna. **`sac_ia → retencao_humano` são 12 contatos de cliente** em webchat que
somem hoje ao filtrar por `sac_ia`. E como M3 foi computada sobre a coluna que mente, a correção não conserta
só o filtro: `limite_processo` aparece com 1 sessão tendo sido a porta de entrada de 20 — a **atribuição de
volume por pool** está errada em todo relatório que agrupa por `sessions.pool_id`.

Desenho das telas: [`../product/historico-unificado-telas-design.md`](../product/historico-unificado-telas-design.md).

---

## 3. Fases

A ordem não é arbitrária: **F0 muda o dado que a tela vai mostrar.** Sem ele a visão 2 renderiza
parkings; com ele renderiza acessos outbound com confirmação.

### F0 — Conserto do gate do `collect` *(antes da UI)*

`collect` **já é** "output com confirmação": contata o alvo por canal, cria sessão-filho de contato,
suspende até resposta ou timeout, e carimba `spawn_reason='collect'`. Ele entrega, de uma vez:

- output que fica suspenso até o fim, com a confirmação como **evento observado**;
- a perna do output **como sessão**, persistida como qualquer contato;
- a **direção outbound** (único `spawn_reason` que significa "a plataforma procurou o cliente");
- e pertença ao processo **por proveniência** (`origin_session_id` aponta para o workflow) — ou seja,
  **sem `journey_merge`**.

Não está em uso por um defeito, documentado no próprio YAML
(`skill_limite_entrega_v1.yaml:41-42`):

> `# Tem de ser delegate, não collect: o collect NÃO gera pendência — o engine`
> `# envia customer_resumable/resume_policy mas o endpoint não lê os campos.`

O CLAUDE.md corrobora: a dual-write de `pending_by_customer` foi gated em `handle_delegate` **e**
`handle_delegate_conference`; `handle_collect` não aparece na lista.

Trabalho:

1. Honrar `customer_resumable` / `resume_policy` em `handle_collect` (simétrico aos dois handlers de
   delegate). **Confirmar em `handle_collect` antes de construir** — o defeito está registrado no YAML,
   não medido por nós.
2. Migrar `skill_limite_entrega_v1.parquear_resultado` de `delegate` para `collect`, preservando o ramo
   `encerrar_nao_retirado` (timeout de 7 dias).

Isto reparte o pré-requisito de pertença em dois, e só um sobra para o merge:

| Caso | Como entra no processo |
|---|---|
| **Output ativo** — nós avisamos o cliente | `collect` → proveniência, automático |
| **Acesso espontâneo** — o cliente volta por conta (acesso 2) | `journey_merge` (F1) |

> ✅ **A segunda linha desta tabela passou a valer em 2026-08-25 (passo 2b).** `unificar_journey`
> subiu para logo depois de `avaliar_pendencia`, então a pertença acontece antes da política e vale
> para os dois acessos. Gate `infra/test/probe_journey_merge_status_access.sh` (vermelho→verde,
> ✅6❌0, com DUAS consultas). O diagnóstico abaixo fica como registro do defeito e da lição.
>
> 🔴 **Medido 2026-08-25 — a segunda linha desta tabela NÃO estava implementada, e a F1 constava ✅.**
> `skill_limite_entrada_v1.yaml:254-261` ramifica a pendência: `policy == "auto"` (acesso **3**, o
> resultado) vai a `unificar_journey` → `journey_merge`; o **default — que é a consulta de status
> (`offer`, "em análise"), ou seja o acesso 2 desta tabela — vai direto ao menu, sem merge**. É a
> única ocorrência de `journey_merge` no arquivo (`:362`). Consequência: abertura é raiz, resposta
> entra por merge, e **todo acesso de consulta nasce raiz de si mesmo e fica fora do processo** —
> quantos forem, e o dono confirmou que na prática são N.
>
> ⚠️ **Não confundir com a decisão vizinha, que está certa:** `:305-306` recusa `workflow_resume` no
> acesso 2 de propósito (*"é o que torna o acesso 2 uma leitura, não uma ação"*). Merge é carimbo de
> **proveniência**; resume é ação de negócio. São independentes, e a segunda não justifica a primeira.
>
> ⚠️ **E rebaixa a prova da F1:** ela foi declarada entregue *"provada por aresta ativa em
> `journey_aliases`"* — a aresta existe, mas veio do ramo `auto`. **Um gate que só exercita o ramo
> que funciona não pode reprovar o ramo que não funciona.** Ver `TODO.md` § passo 2b.

### F1 — Carimbos de pertença

Ambos são pertença: um diz *de que processo esta sessão faz parte*, o outro *por onde ela entrou*.

1. **`invoke journey_merge` no intake** — no ramo pós-OTP que resolve `pending_workflow_get`
   (`agente_portabilidade_intake_v1`, `skill_limite_entrada_v1`). Tudo o mais já existe: a tool, o topic
   `journey.merges`, a tabela `journey_aliases`, o union-find na leitura, `root_session_id` no
   `PendingEntry`.
2. ~~**Endereço de entrada, imutável.** Carimbar o `endpoint_id` do `ChannelEndpoint`…~~ — **REMOVIDO do
   arco (D12b, 2026-08-12).** M2 mediu endpoint→pool **1:1**, então o pool substitui o endereço sem perda;
   e a premissa que dava identidade ao endereço (D7) é falsa em 5 dos 6 canais. **F1 fica só com o
   `journey_merge`.**

### F1b — `entrou por`: first-write-wins em `sessions.pool_id` — ✅ **2026-08-14**

Independente de F0 — nenhum bloqueia o outro. As-built no `CHANGELOG.md`; abaixo só as decisões que
o desenho não tinha e que a implementação teve de tomar.

1. **O critério de "primeiro" é o MENOR `timestamp`, não a ordem de chegada.** `inbound`, `routed` e
   `queued` são tópicos diferentes e ordem entre tópicos não é garantida — a regra por ordem daria
   resultados distintos entre dois replays do mesmo histórico. É o espelho exato de `opened_at`.
2. **A fonte passou a ser única de verdade**: o fallback `_pool.pool_v` de `_fetch_sessions` foi
   **removido** (servia 1 de 407 sessões e trazia *"atendido por"* para dentro de uma célula que diz
   *"entrou por"*).
3. **`_fetch_pools_queue` teve a precedência invertida** para o segmento `role='queue'`. Achado da
   fase, não consequência dela: o relatório de fila **já atribuía** 6 das 15 esperas ao pool errado.
4. **O ABAC precisou de conserto, e a medição é que disse isso.** Ver §Achado 7.

Cuidado registrado, confirmado por medição: derivar o pool de entrada do primeiro segmento **não**
serve como alternativa — **5** sessões do ambiente têm pool e **nenhum** segmento (abandono antes de
qualquer agente entrar), que é justamente o caso que um relatório de fila precisa ver.

#### Achado 7 — o ABAC autorizava pelo fato errado, e não era latente

A F2 registrou um risco de ABAC que, medido, **não tinha amostra** (achado 6). Este é o oposto: a
hipótese era a mesma — *"provavelmente ninguém tem `accessible_pools` não-vazio"* — e a medição a
derrubou. **2 de 3 usuários têm** (`admin` inclusive, porque `open_access` não desliga pool-scoping),
e **52 dos 67** contatos divergentes sairiam do escopo deles, incluindo os 14 `sac_ia`→
`retencao_humano`, que são contatos de cliente.

A causa é anterior ao carimbo: autorizar por `sessions.pool_id` sempre foi autorizar pelo acidente do
último escritor. O predicado correto é a **união** — entrou por pool meu · ainda não tem pool · um
pool meu participou (segmento). Implementado em `_session_scope_clause`, **um** lugar no lugar de
quatro cópias inline. É estritamente ampliador: medido +9 sessões, −0.

> A lição que fica das duas fases juntas: *"risco derivado de leitura de código"* não tem sinal —
> na F2 a medição o dissolveu, na F1b a medição o confirmou e ainda mostrou que era maior do que a
> hipótese. O que decide não é a plausibilidade do risco, é a contagem.

### F2 — `root_session_id` em `/reports/segments` (D10) — ✅ **2026-08-14**

Implementada como **subconsulta** em `sessions` (a coluna não existe em `segments`), com o mesmo
union-find de `_fetch_journeys`, e `meta.window_applied` marcando o ramo isento. Gate diferencial em
`infra/test/probe_segments_journey_window.sh` (6/0). As-built no `CHANGELOG.md`.

**Achado 6 (a assimetria de ABAC) foi MEDIDO e não é defeito:** 723 segmentos com pool, **zero** com
`pool_id` vazio. A afirmação era derivada de leitura de código e não tem amostra — segmento nasce
quando um participante entra, e participante entra num pool. `_apply_pool_scope` **não** foi alterado;
o risco fica registrado como latente, e o conserto certo, se ocorrer, é um parâmetro e não a mudança
de uma função compartilhada com `_fetch_pools_volume` e `_fetch_session_complexity`.

### F3 — Visão 1: lista de contatos + chip de processo + direção do acesso (D3, D8, D12)

As-built no `CHANGELOG.md` (2026-08-14). **Resíduos fechados na F4** (2026-08-25): filtro por
direção, `?session_id=` honrado e as duas páginas mortas removidas — ver abaixo.

### F4 — Visão 2: pivô, lente A/B com toggle de ordenação, internas dobradas (D4, D6, D11) — ✅ **2026-08-25**

As-built no `CHANGELOG.md`. Abaixo só o que o desenho não tinha e a implementação teve de decidir.

1. **A direção mudou de casa, e isso não era item da fase.** O D8 diz *derivada, nunca
   armazenada* e não diz ONDE. Enquanto só existia a coluna, derivar na UI era inofensivo; no
   instante em que o resíduo da F3 pediu um FILTRO, derivar nos dois lados criaria duas
   respostas para a mesma pergunta. A expressão passou a ser única
   (`reports_query._DIRECTION_EXPR`), usada como coluna e como predicado **na mesma query**.
   *Generaliza:* uma regra derivada em dois runtimes só é segura enquanto um dos dois não
   precisar decidir nada com ela.

2. **O cabeçalho não precisou de toggle — precisou de dois domínios.** O D11 propunha que
   `internal_session_count` entrasse no cabeçalho quando o toggle ligasse. Implementado assim,
   o número protagonista mudaria de significado conforme um controle de VISIBILIDADE. Os dois
   domínios são reportados sempre (`N acessos · M etapas internas`), como a `ListaTab` já faz
   com contatos × internas, e o *"diz 3, mostra 4"* some sem inventar um quarto número.

3. **Três classes, não duas.** `spawn_reason` desconhecido não é acesso nem etapa: vira linha
   própria, contada à parte, nunca dobrada. Somá-la a "acessos" inflaria o número protagonista
   com uma linha que ninguém sabe ler — e o balde plausível é o que este arco recusa em toda
   parte. Medido no ambiente: **0 não classificadas** em 115 sessões (o tenant só tem `NULL`,
   `trigger` e `collect`), então o ramo existe sem população — e é assim que ele deve existir.

4. **A lente C (faixas por personagem) segue destino registrado.** Nada nesta fase a bloqueia.

5. **Achado da fase, fora do escopo dela:** `journeyLabel` tinha duas implementações (4 × 8
   caracteres do id) e o comentário de uma afirmava que eram a mesma convenção. O chip levava
   a um cabeçalho com outro código para o mesmo processo. Unificado em 8.

### F5 — `ContextStorePersister` — ✅ **2026-08-26**

> **O desenho abaixo foi implementado, com DOIS desvios que a medição obrigou.** As-built completo
> no `CHANGELOG.md`; aqui só o que o desenho não previa.
>
> **1. *"Persistir mascarado"* custava mais do que o ADR supunha: o mascarador é TypeScript.**
> `applyContextMaskingDynamic` e amigos existem só no mcp-server, e o persister vive num serviço
> Python. Reimplementar seria a segunda implementação de uma regra de SEGURANÇA — de menos vaza,
> de mais é invisível. Resolvido por hop HTTP (`POST /internal/context-snapshot`), com o PII **não**
> viajando no corpo (vai `session_id`; volta mascarado) e credencial que **recusa** quando ausente.
>
> **2. O portão de NAMESPACE não é aplicado na persistência.** A frase *"persistir mascarado"*
> trata como uma coisa o que são duas: filtro de EXIBIÇÃO por pool × mascaramento de PII. Só o
> segundo pertence a um registro durável — aplicar o primeiro faria a config de UI de um pool
> **apagar história em silêncio**, o mesmo defeito que o `CLAUDE.md` documenta para o sentimento.
> Gate `probe_context_snapshot_endpoint.sh` ramo E fica vermelho se alguém reverter.
>
> **3. A raiz canônica é resolvida no mcp-server**, pela mesma via de `writeContextTag`
> (proveniência → union-find). Em Python seria a terceira casa da definição de *"qual journey é
> esta"*, e uma foto na raiz errada não fica vermelha.
>
> **Grau OPERATOR, para sempre** (decisão do dono): ⚠️ este registro **não serve a auditoria que
> precise do valor real** — essa continua sendo o `TokenVault` de mensagens.
>
> ⚠️ **Forward-only e sem leitor.** O valor de leitura (o diff entre fotos consecutivas) é fatia
> própria, e depende de processos com ctx de journey — hoje raros no demo.

*(desenho original, mantido para referência)*

Irmão do `PipelineStatePersister` (R5/B), mesma justificativa: *"a trajetória real não vai ao stream e o
Redis tem TTL 24h"*.

- **Gatilho:** `session_closed`. **Destino:** PG `session_context_snapshot`.
- **Conteúdo:** o hash `{t}:ctx:{sessionId}` entrada a entrada, preservando
  `{value, confidence, source, visibility, updated_at}`.
- **Masking: persistir mascarado, e ponto.** O valor cru nunca sai do Redis vivo. Persistir cru repetiria
  o defeito que a R7 fechou no `output_snapshot`. O efeito observável continua legível
  (*"`caller.cpf` escrito às 10:04:12, source `crm`, confiança 0.9, `***-00`"*) sem criar cofre novo.
- **Estado final**, não trajetória. Perde-se sobrescrita *dentro* do contato — o que deve ser declarado,
  não descoberto.
- **Contexto de processo:** snapshot de `{t}:ctx:journey:{raiz}` **a cada close de sessão-membro**, junto
  com o estado. Cardinalidade: N fotos por processo, uma por contato encerrado.
- **Foto inteira, nunca delta.** Fotos de ctx de processo são pequenas; leitor que reconstrói estado a
  partir de deltas falha em silêncio quando um delta se perde.

**Propriedade emergente que torna as duas escolhas coerentes:** "estado final" perde sobrescrita dentro
do contato, mas o snapshot por close **recupera a sobrescrita entre contatos**. A granularidade de
recuperação de trajetória passa a ser exatamente o **contato**, que é a unidade da hierarquia. O valor de
leitura está no **diff entre fotos consecutivas** — literalmente *"o que este contato acrescentou ao
processo"*. Com `updated_at` por entrada, boa parte da atribuição sai até sem diff.

**Regra de exibição:** entrada oculta por `visibility` é **contada, não omitida** (*"3 entradas ocultas
(agents_only)"*). Omitir em silêncio faz o leitor concluir que a chamada não escreveu nada.

### Destino registrado — lente C (D6)

Sem custo de backend além de D10.

---

## 4. Decisões abertas

1. ~~**`collect` que expira sem engajamento conta como contato?**~~ — **FECHADA em 2026-08-14, por
   ausência.** Não conta porque **não existe**: o `collect` as-built é **lazy**
   (`webhook.py:1818-1838`) — entrega o convite, suspende, e não cria sessão nem aloca recurso. A
   sessão-filha só nasce em `handle_collect_engage`, que é também o único lugar que escreve
   `spawn_reason='collect'` (`:2076`). Sem clique não há linha a excluir, e nada infla.

   **Duas consequências que este ADR não previa:**
   - **"A perna do output como sessão" é condicional ao engajamento** (§3 F0 a lista sem ressalva).
     No cenário de referência, cujo retorno real é o espontâneo por identidade, ela nunca materializa.
   - **A prova de saída para o caso não-engajado perde linha própria.** Resta o par honesto de D9 —
     `(emitiu?, close_reason)` no contato anterior — mais o `suspend` do chamador, que vive em
     `session_transitions` e não tem superfície. Decisão de exibição de F4; escrever antes de renderizar.

   Medido junto: **`spawn_reason` tem só dois valores no tenant** (`NULL` 342 · `trigger` 65), nem
   `collect` nem `delegate` — o tipo de linha *"acesso outbound"* tem **zero amostras**.
2. ~~**Semântica exata do chip** quando o processo tem contatos fora da janela filtrada (D3)~~ —
   **FECHADA em 2026-08-14 (F3.3).** O N conta o processo **inteiro** e o rótulo do rodapé o nomeia:
   *"O número no chip conta o processo inteiro — o período recorta os contatos listados, não o tamanho
   do processo."* (EN: *"The chip counts the whole process — the date range narrows the contacts
   listed, not the size of the process."*)

   **O rótulo é CONDICIONAL, e é essa a parte que a decisão acrescenta ao desenho.** Ele só aparece
   quando `meta.window_applied` é verdadeiro **e** há chip na página: no drill a janela não incide,
   não há divergência a explicar, e uma frase permanente viraria ruído na tela em que a maioria das
   linhas não tem chip. O marcador `window_applied` (entregue dias antes, para outro fim) é o que
   tornou o condicional possível — sem ele o texto teria de ser permanente.

   **Duas escolhas de implementação que a decisão fixa:** o N conta **contatos**, com o predicado
   idêntico ao de `_fetch_journeys.session_count` (predicado próprio faria o chip dizer `·2` e o
   cabeçalho da visão 2, no clique seguinte, dizer `4`); e o rótulo do chip é a raiz **canônica**
   (`journey_id`, union-find), não `root_session_id` — na amostra de referência os dois **diferem**
   (`e2764d9b…` × `d62d7121…`), e o cru daria duas etiquetas para o mesmo processo.
3. **`uniq(root_session_id)` como métrica de cabeçalho.** *"Quantos processos tive este mês"* não sai de
   uma lista de contatos. É métrica, não linha. Lacuna registrada, não fechada.

---

## 5. O que este ADR NÃO faz

- **Não mostra interação sistema-a-sistema dentro da sessão** (agente chamando CRM/calendar). Três
  quebras independentes, todas fora deste escopo — ver §6.
- **Não cria entidade Journey.** A journey segue derivada de (proveniência ∪ alias).
- **Não transforma cada perna do workflow em sessão.** Ver §6, "Rejeitado".

### Rejeitado — "cada perna do workflow é uma session"

Proposta considerada e recusada. *"Todo acesso pelo canal gera um session_id"* **já é verdade para
acessos**; não é para **retomadas**: `POST …/webhook/resume/{token}` continua a mesma sessão e abre um
**segmento** novo.

Isso é invariante declarado: *"session = UM contato (identidade estável através de suspend/resume —
duração e nº de segmentos são consequência, não critério)"*, com o discriminador *"nasceu um contato
NOVO?"*. Uma retomada por token não faz nascer contato — continua um.

Dano concreto se mudado: `session_count`, TMA, taxa de abandono e a contagem de contatos da journey
inflariam juntos, e o número do cabeçalho deixaria de significar *"quantas vezes este cliente nos
procurou"*. Combinado com F0 — que aumenta o número de suspensões — o efeito se multiplica.

**E a uniformidade pretendida já existe:** a perna é um segmento, e `session_transitions` guarda o par
suspend→resume com `resume_origin`, `suspend_reason`, `step_id`, `resumed_at`. Está persistido, é
uniforme, e não aparece em lugar nenhum da UI. O gap é de superfície, não de modelo.

---

## 6. Achados medidos — não redescobrir

Todos de 2026-08-12, salvo indicação.

| # | Achado | Evidência |
|---|---|---|
| 1 | **`ani` e `dnis` são universalmente vazios** — 314 sessões (webchat 235, webhook 79), zero valores nos dois canais existentes | query direta em `plughub_demo.sessions` |
| 2 | O endereço discado do webhook **existe no evento e se perde no ingest**: `skill_id` carrega o papel de DNIS (`webhook.py:504`, `main.py:1411`), e `models.py:114` não o lê | leitura de código |
| 3 | `ListaTab` tem colunas `origin (ANI)` e `destination (DNIS)`, e o CLAUDE.md § Arc 19 descreve o recurso como entregue. **São duas colunas permanentemente vazias na tela** | decorre de 1 |
| 4 | **`sessions.pool_id` é o último pool**, não o de entrada — reescrito pelo `ReplacingMergeTree` no `delegate` | handoff, medido |
| 5 | **`/reports/segments` trunca em silêncio**: janela `started_at` sempre aplicada (default 7 dias), sem isenção para `session_id` | leitura de código |
| 6 | **ABAC divergente**: `/reports/segments` aplica `pool_id IN (accessible_pools)` **sem** o `OR pool_id = ''` que `/reports/sessions` usa — segmento não roteado some para supervisor restrito | leitura de código |
| 7 | `AnaliseProcessosPage.tsx` é **código morto** (nenhum import em todo o `src/`); a rota `/analise/processos` aponta para `AnaliseJourneysPage` | varredura |
| 8 | `SegmentList` **já funde** segmentos + sessões-filhas num eixo único ordenado por tempo — é a primitiva de timeline pedida, um nível abaixo, e não é compartilhada | leitura de código |
| 9 | **Audit LGPD: documentado e ausente.** Não há DDL de `mcp_audit_log` nem de `audit_access_log` em `clickhouse.py`; `_require_audit_access()` não existe. O que roda é `optional_pool_principal`, que **passa sem header `Authorization`**. O CLAUDE.md dá as três por implementadas | varredura |
| 10 | **`mcp.audit` não tem produtor vivo**: `McpInterceptor` nunca é instanciado; `engine-runner.ts:126` faz `fetch` JSON-RPC cru sem auditar. Existe um topic órfão `audit.mcp_calls` (nome divergente) sem consumidor | varredura |
| 11 | `/v1/audit/mcp-calls` **seleciona `actor_id` e o descarta** antes de responder; `session_timeline` ainda tem `segment_id`, que a query nem seleciona | `audit.py:170-203` |
| 12 | **Sobreposição existe entre contatos**, não só entre segmentos: o acesso 2 roda enquanto a análise está aberta | cenário de referência |
| 13 | **46 sessões em 314 (14,6%)** saem com `pool_id` diferente do pool de entrada — 5 pares, soma exata, todas orgânicas. Uma delas (`sac_ia → retencao_humano`, 12) são contatos de CLIENTE que somem do filtro | M1/M1b, 2026-08-12 |
| 14 | **endpoint→pool é 1:1** (13 webhook/13 pools, 2 webchat/2 pools); `whatsapp`/`voice`/`sms`/`email` têm **zero** endpoints | `channel_endpoints`, 2026-08-12 |
| 15 | **A Fase E do `ChannelEndpoint` só existe no webhook** — os outros 5 canais caem em pool default ou nem consultam; `webrtc` resolve com um valor de canal ausente do enum | leitura de código, 2026-08-12 |
| 16 | **Seeds escrevem `segments` direto no ClickHouse sem carimbar `origin`**, logo saem como `live`. 15 sessões sintéticas no pool de produção `sac_ia`. O discriminador construído para isolá-las não é usado por quem as escreve | `seed_deploy_lens_demo.sh:61`, `seed_epoch_demo.sh:63` |
| 17 | **`voice.py:236,247` chamam `_open_session`/`_route_inbound`, que não têm definição em lugar nenhum do pacote.** O teste as **cria** como `AsyncMock` e depois afirma `assert_awaited_once` — um teste que só pode passar, sobre um método que a produção não tem | grep em `packages/channel-gateway`, 2026-08-12 |

Os achados **9, 10 e 11** estão fora do escopo deste ADR mas merecem item próprio — um gate de LGPD que
a documentação dá por fechado é o pior lugar possível para doc-descreve-config.

Os achados **16 e 17** também são item próprio, e pelo mesmo motivo estrutural: nos dois, o mecanismo que
existiria para impedir o defeito **está construído e não é usado por quem escreve** — `origin`, no caso do
seed; o teste, no caso do voice. O 17 é o mais sério: `voice` é canal que a documentação dá por entregue, e a
suíte é verde porque afirma sobre um mock que ela mesma criou. Não pertence a este arco, mas explica por que
não existe uma sessão de voz no ambiente — e, portanto, por que M3 não pôde decidir nada sobre canal.

---

## 7. Verificação

```bash
bash infra/test/probe_journey_limite.sh          # 5/0 — a cadeia por PROVENIÊNCIA
bash infra/test/smoke_limite_tres_acessos.sh     # 18/0 — o cenário inteiro por API
```

> **Números conferidos em 2026-08-14.** O smoke é **`18/0`**, não `16/0`: cresceu em `daeb9a9`, que
> acrescentou as duas asserções de não-vazamento (vencimento e CPF no preview). O denominador
> registrado estava velho — quem compara "passou" com o número antigo lê regressão onde houve reforço.
>
> ⚠️ **O probe conta por PROVENIÊNCIA e por isso não pode reprovar na dimensão de F1.** Ele afirmou
> *"a journey tem 3 sessões"* minutos antes de `/reports/journeys` responder `4` para a mesma raiz — o
> endpoint conta **proveniência ∪ alias**. Os dois estão certos e medem coisas diferentes; o problema
> é o probe se apresentar como o gate da journey. Um gate de merge é query própria sobre
> `journey_aliases`, não este arquivo.

```bash
DC="docker compose -f docker-compose.demo.yml"
```

```bash
$DC exec -T clickhouse clickhouse-client -q "
SELECT channel, count() AS n,
 countIf(ifNull(ani,'')!='')  AS com_ani,
 countIf(ifNull(dnis,'')!='') AS com_dnis
FROM plughub_demo.sessions FINAL
WHERE tenant_id='tenant_demo'
GROUP BY channel ORDER BY n DESC FORMAT TSV"
```

**Nota sobre este gate:** ele é hoje **inconclusivo quanto ao leitor** — não há sessão de voz ou WhatsApp
no ambiente, e são elas que carregariam `dialed_number`/`to`. O par `ani`/`dnis` nunca foi exercido, logo
o teste não pode reprovar por ausência de amostra. Depois de F1, o ramo esperado passa a ser
`webhook.com_dnis = n`, e aí ele vira gate de verdade.

---

## 8. Onde está o resto

- [`adr-journey-session-segment-model.md`](adr-journey-session-segment-model.md) — os três níveis, e por que journey é derivada
- [`adr-customer-360-two-surfaces.md`](adr-customer-360-two-surfaces.md) — Console × Analytics sobre `customer_id`; D2 daquele ADR é o consumidor comprometido da lista de processos escopada (D2 deste)
- [`../product/journey-contatos-em-sequencia-handoff.md`](../product/journey-contatos-em-sequencia-handoff.md) — o handoff que originou esta discussão
- [`../product/journey-3-niveis-implementation-spec.md`](../product/journey-3-niveis-implementation-spec.md) — J1–J5 as-built
- [`../product/limite-credito-3-niveis-design.md`](../product/limite-credito-3-niveis-design.md) — o cenário de referência; §11 e a correção estrutural de 2026-08-11
- `packages/analytics-api/…/reports_query.py` — `_fetch_journeys`, `_apply_contact_scope`, `_journey_resolved_map`
- `packages/platform-ui/src/modules/analise/AnaliseJourneysPage.tsx` — a Vista Processos atual
- `packages/platform-ui/src/modules/service/components/SegmentList.tsx` — a primitiva de timeline (achado 8)
