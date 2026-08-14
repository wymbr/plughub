# Histórico unificado — desenho das telas (F3 / F4)

> Companheiro visual do [`../adr/adr-historico-unificado-duas-visoes.md`](../adr/adr-historico-unificado-duas-visoes.md).
> **Nada aqui é para ser construído antes de F0.** O ADR é explícito: F0 muda o dado que a tela mostra.
> Este documento existe para que a decisão de exibição já esteja escrita quando F3/F4 começarem — e para
> que os pontos em que o desenho **depende** de uma fase anterior fiquem nomeados, não descobertos.

Rota única: `/analise/sessions` absorve `/analise/processos`. `AnaliseProcessosPage.tsx` já é código morto
(achado 7); `AnaliseJourneysPage` deixa de ser página e vira o **nível 2** da mesma rota.

---

## 1. Visão 1 — lista de contatos

Unidade da linha: **o contato**. O processo nunca é linha (D3).

### Barra de filtros

`período · canal · pool` — e o pool são **dois filtros com nomes diferentes**, nunca um só.

> **Revisão de 2026-08-12, medida.** A versão anterior desta seção previa `período · canal · endereço de
> entrada · atendido por`, seguindo D12 do ADR. O endereço **saiu**, e a justificativa está em §5.

| Filtro | Semântica | Fonte |
|---|---|---|
| canal | por qual canal o contato chegou | `sessions.channel` — **já funciona**, preenchido nas 314 |
| **entrou por** | a porta: qual pool recebeu o contato | `sessions.pool_id` com **first-write-wins** *(não existe hoje)* |
| **atendido por** | qualquer pool que trabalhou no contato | `session_id IN (SELECT … FROM segments WHERE pool_id = X)` (D12) |

**Os dois pools não podem se chamar "Pool" na tela.** Se chamarem, o operador lê um e recebe o outro — que
é precisamente o erro que `sessions.pool_id` comete hoje.

**`entrou por` é o trabalho real, e ele não é "adicionar um campo" — é parar de apagar um que já existe.**
O channel-gateway resolve o pool **antes** de publicar `conversations.inbound`; o valor já viaja no evento e
`parse_inbound` já o escreve (`analytics-api/models.py:119-150`). Ele é destruído depois, pelo
`routed`/`queued`/`closed`, porque `sessions` é `ReplacingMergeTree` de linha inteira.

Por que estar em `_IDENTITY_FIELDS` não bastou: `_learn_session_identity` faz `if value: entry[field] = value`
(`consumer.py:133-136`) — a cache **segue o último**; e `_inject_session_identity` só preenche ausência, nunca
divergência (`:152-154`). O campo está protegido contra sumiço e desprotegido contra sobrescrita.

**O padrão já existe no mesmo arquivo, aplicado a exatamente um campo.** `consumer.py:137-139` e `:155-160`
tratam `opened_at` com first-write-wins, sob o comentário *"A abertura é imutável: vence sempre a mais antiga
conhecida"*. O pool de entrada é da mesma classe de fato — implementar é estender esse tratamento, não
inventar mecanismo.

> Ressalva: dar a `sessions.pool_id` o significado *pool de entrada* é **definir** uma coluna que hoje não
> tem significado nenhum (é o que escreveu por último), não *mudar* um significado estabelecido. Ainda
> assim, medir quem a lê antes de virar a chave — quem depende do acidente quebra em silêncio.

### Colunas

| # | Coluna | Fonte | Observação |
|---|---|---|---|
| 1 | direção | **derivada** de `spawn_reason` + canal (D8) | `NULL`→inbound ⇣ · `collect`→outbound ⇡ · `trigger`/`delegate`→interno ⚙. Desempate: `customer_id` com prefixo `sys:` |
| 2 | contato | `session_id` (últimos 10) + `channel` | sem endereço — ver §5 |
| 3 | entrou por → atendido por | `pool_id` (entrada) e `segments.pool_id` | uma seta quando houve handoff; um nome só quando não houve |
| 4 | início | `opened_at` | |
| 5 | duração | **`elapsed_time_ms`** | nunca `agent_time_ms`, nunca Σ segmentos |
| 6 | desfecho | `outcome` + `close_reason` | |
| 7 | processo | chip `PRC-{root[:4]} · N` | só quando `N > 1` |

**Colunas que saem:** `origin (ANI)` e `destination (DNIS)`. São permanentemente vazias hoje — zero valores
nos dois canais existentes (achados 1 e 3). Mantê-las é publicar duas colunas que só sabem dizer `—`.
**Não voltam** — nem depois de F1. Ver §5.

### O chip

- **Conta o processo inteiro, não a fatia filtrada.** Janela que pega 2 de 3 contatos mostra `· 3`.
  Isso vai parecer defeito, e por isso o rodapé da tabela carrega o rótulo explícito. *(Decisão aberta #2
  do ADR: o texto exato ainda não está escrito.)*
- **Contato de processo único não recebe chip** — não há para onde pivotar, e é o caso majoritário.
- Computado sobre a **página retornada** (`GROUP BY root_session_id` sobre ~200 linhas), no mesmo passe que
  já resolve `_journey_resolved_map`.

### Sessões internas

Toggle já existente (`scope=all`), com o contador ao lado (`meta.total_internal`). Default: ocultas.

---

## 2. Visão 2 — processo

Chega-se aqui **pivotando do chip** (D2). Não há lista de processos — a única lista escopada prevista é a
do [`adr-customer-360-two-surfaces.md`](../adr/adr-customer-360-two-surfaces.md) (filtro `customer_id` +
`open`), que é entrada alternativa, não navegação livre.

### Cabeçalho

```
PRC-3f9c   cliente cus_9a21…   [desfecho provisório · 1 em aberto]
acessos do cliente 3   ·   contatos 4   ·   decorrido 7d 02h
[ 2 sessões internas ocultas ]  [ offset relativo à abertura ]
```

**A aritmética do cabeçalho — revisada em 2026-08-14, com a journey de referência medida.**

| Linha | Classe | Conta como contato hoje? |
|---|---|---|
| acesso 1 (inbound webchat, `spawn_reason NULL`) | acesso do cliente | sim |
| análise (webhook, `trigger`, pool `aprovacao_credito`) | etapa interna | **SIM — e não deveria** |
| entrega (webhook, `trigger`, pool `limite_entrega`) | etapa interna | **SIM — e não deveria** |
| acesso 2 (inbound espontâneo, trazido por alias) | acesso do cliente | sim — `journey_merge` (F1 ✅) |
| ~~saída ativa (`collect` expirada)~~ | — | **não existe** — ver abaixo |
| wrap-up destacado | etapa interna | não — `purpose=internal` |

**Duas correções sobre a versão anterior desta seção:**

1. **A linha da saída expirada não existe.** O `collect` é lazy: sem clique não nasce sessão
   (`webhook.py:1818-1838`). Decisão aberta #1 fechada **por ausência**, não por política.
2. **`contatos` ≠ `acessos do cliente`, e o desenho anterior supunha que `_apply_contact_scope`
   resolvia.** Não resolve: ele exclui pool `purpose=internal`, e `aprovacao_credito`/`limite_entrega`
   não são. Medido: `session_count: 4` para um cliente que nos procurou **2** vezes.

O cabeçalho precisa, portanto, dos **dois** números — `acessos do cliente 2 · contatos 4` — e não de
um só. O discriminador é derivável hoje, sem dado novo: `spawn_reason IS NULL` + canal de cliente (D8).

> ⚠️ **A classe *acesso outbound* tem ZERO amostras no ambiente.** `spawn_reason` só assume dois
> valores no tenant inteiro (`NULL` 342 · `trigger` 65) — nem `collect` nem `delegate`. Construir a
> linha outbound é construir sobre um caso que nada exercita, exatamente como as colunas ANI/DNIS que
> esta revisão removeu. **Não bloqueia F3/F4**, mas a tela não poderá ser verificada nesse ramo — e
> isso tem de estar escrito antes da revisão, não descoberto nela.

Cuidado registrado: o cabeçalho tem de bater com a tabela. Hoje `_fetch_journeys` aplica
`_apply_contact_scope` e `/reports/sessions?root_session_id=` **não** — cabeçalho diz 3, tabela mostra 4
(D11). O toggle de internas é o que reconcilia; quando ligado, `internal_session_count` entra no cabeçalho.

### Duas classes de linha (D4)

| Classe | O que a linha mostra | Default |
|---|---|---|
| **acesso do cliente** | direção, canal, endereço, offset, duração, par *emitiu saída → close_reason* | expandida, com destaque |
| **etapa interna** | pool, skill, participantes de agente | **dobrada** — `⋯ 2 etapas internas (análise, entrega)` |

O caso *em espera* precisa de rótulo honesto. Uma sessão que existe só para aguardar lê como sessão
quebrada no meio do processo se a linha só disser `suspended`.

### Confirmação — nunca inferida de `visibility='all'` (D9)

`messages` permite afirmar *"houve emissão ao cliente"* sem tocar `content`. Mas **emitido ≠ recebido**, e
usar emissão como prova de entrega mente exatamente no caso que originou o cenário: a sessão de entrega
parqueia **porque o cliente não está lá**.

- **Depois de F0**, a confirmação é evento observado: três estados — `confirmado` / `expirado` /
  `abandonado`.
- **Onde não houver `collect`**, o par honesto é `(emitiu?, close_reason)`.
- **Antes de verificar:** o literal que o cliente usa em `author_role`
  (`SELECT DISTINCT author_role FROM plughub_demo.messages`) foi suposto, não medido.

### Lentes A e B — um componente, dois eixos (D6)

Com `started_at` na linha (custo zero, já vem no shape do segmento), árvore e cronologia são o **mesmo
componente com um toggle de ordenação**.

- **A · árvore** — indentação por `origin_session_id`.
- **B · cronologia** — ordenação por `started_at`, contato como cabeçalho de grupo.
- Em ambas, além do timestamp absoluto, **offset relativo à abertura do processo** (`+2d05h`). Dois
  timestamps absolutos em níveis de indentação diferentes são difíceis de comparar de olho.

**A árvore esconde sobreposição, e a sobreposição existe em dois níveis.** Segmentos se sobrepõem
(`@mention` paralelo ao primary é rotina) — e **contatos também**: o acesso 2 roda enquanto a análise está
aberta (achado 12). A lente A mostraria a consulta como irmã, sem dizer que rodou dentro da janela da
análise. Por isso a barra de tempo da lente B não é decoração: é a única superfície onde o fato aparece.

> Invariante herdado: **nunca somar segmentos** para obter duração de processo. `elapsed_time_ms`, jamais
> `Σ agent_time_ms`.

**Lente C (faixas por personagem)** fica como destino registrado. Não exige backend além de D10, mas tem
dois riscos já nomeados: colisão intra-faixa no fan-out, e o fato de que **o cliente não tem segmento** —
ele é a banda do contato, não uma faixa com atividade.

### Segmento é a folha (D5)

A timeline para no segmento. Mensagens só no drill de **um** contato, via `SessionTranscript`, que já
existe e já é masked-by-construction. **Não haverá transcript fundido atravessando contatos** — ele
exigiria reconciliar três regimes de visibilidade e obrigaria a um ADR de masking próprio.

---

## 3. Do que este desenho depende

| Depende de | O que quebra sem isso |
|---|---|
| **F0** | a saída ativa não existe como linha; a visão 2 renderiza *parking* sem direção nem confirmação |
| **F1** (`journey_merge` no intake) | os acessos 2 e 3 não pertencem ao processo — o chip diz `· 1` e a visão 2 fica com um contato só |
| **novo — `entrou por` first-write-wins** | o filtro por pool de entrada perde 46 sessões em 314 e a atribuição de volume por pool fica errada |
| **F2** (`root_session_id` em `/reports/segments`) | a lente precisa de N+1 requests, cada uma truncada pela janela de 7 dias (achado 5) |

O carimbo de endereço saiu da F1: **F1 é só o `journey_merge`.** Essa é a economia grande desta revisão.

Achado 6, que morde F4 e não está no caminho crítico: `/reports/segments` aplica
`pool_id IN (accessible_pools)` **sem** o `OR pool_id = ''` que `/reports/sessions` usa — segmento não
roteado desaparece para supervisor restrito. Duas lentes sobre o mesmo processo devolveriam árvores
diferentes conforme o usuário.

---

## 4. Dívida de implementação que a unificação herda

As três telas de hoje quase não usam `src/components/ui/` — tabela, badge, drawer e tabs são inline em
cada uma, e `SegmentList` usa **cores hex hardcoded** (`OUTCOME_COLORS`, `ROLE_COLORS`) fora dos tokens.
`AnaliseJourneysPage` usa `text-[10px]`/`text-[11px]`, contra a regra do projeto (a escala tem `text-micro`
e `text-2xs`).

`SegmentList` já **funde** segmentos + sessões-filhas num eixo único ordenado por tempo (achado 8): é a
primitiva de timeline pedida, um nível abaixo, e não é compartilhada. F4 deve extraí-la, não reescrevê-la.

i18n: namespace `contacts` (`pt-BR` + `en`), blocos `lista.*`, `journeys.*`, `segments.*`. Toda string nova
entra nos **dois** locales antes do PR.

---

## 5. Medições que fecharam o desenho (2026-08-12, `tenant_demo`)

Todas rodadas contra `docker-compose.demo.yml`. Números, não impressões.

| # | Medição | Resultado |
|---|---|---|
| M1 | `sessions.pool_id` (final) × pool do primeiro segmento | **46 divergentes em 314** (14,6%) |
| M1b | os 46, nomeados por par | 5 pares, soma **exata**, sem resíduo — nenhum segundo mecanismo |
| M2 | endpoints por pool (`channel_endpoints`) | **1:1** — 13 webhook/13 pools, 2 webchat/2 pools |
| M3 | canais por pool (dado) | todos `1`; mas 4 pools declaram `[webchat, whatsapp]` na config |
| M4 | segmentos sem linha em `sessions` | 15, **100% seed** (`dlz_`=3, `sess_epoch_`=12) |

### Os 46, nomeados

```
limite_processo     → aprovacao_credito      19
wrapup_detached_ia  → retencao_humano-int    12
sac_ia              → retencao_humano        12
formfill_demo_ia    → formfill_demo           2
gate_promocao_ia    → aprovacao_deploy         1
```

**Os 46 são orgânicos.** Verificado depois de descobrir que o banco tem seed de apresentação: os cinco pares
têm `session_id` em UUID (nenhum prefixo `dlz_`/`sess_epoch_`) e **zero** sessões abrindo em `10:00:00`, que é
a assinatura dos seeds. A contaminação medida em M4 não alcança M1 — o join é `INNER` e as sintéticas não têm
linha em `sessions`.

Quatro pares são maquinaria interna — errados, porém invisíveis ao operador. **`sac_ia → retencao_humano` é
outra coisa:** são 12 contatos de cliente, webchat, que entraram no `sac_ia` e foram transferidos a humano.
Quem filtra a lista por `sac_ia` hoje **não os vê**. Não é imprecisão de relatório interno — é a tela de
histórico devolvendo menos contatos do que existem, calada.

**Consequência recursiva:** a própria tabela pool×canal (M3) foi computada sobre a coluna que mente.
`limite_processo` aparece nela com 1 sessão, tendo sido a porta de entrada de 20. O conserto não melhora só
o filtro — corrige a **atribuição de volume por pool** em todo relatório que agrupa por `sessions.pool_id`.

### Por que o DNIS sai e o canal fica

Os dois são "1:1 hoje, quebráveis por config amanhã" — a distinção não é fragilidade, é **preço**:

| | Perda medida ao tirar | Economia ao tirar |
|---|---|---|
| **DNIS** | zero (M2: nenhum pool tem dois endereços) | 3 camadas + 6 adaptadores + a Fase E, que só existe no webhook |
| **canal** | zero (M3, mas por ausência de amostra) | **zero** — `channel` já está preenchido em todas as sessões |

Tirar o DNIS compra economia grande por risco nenhum. Tirar o canal compra nada por risco pequeno e real
(4 pools *podem* receber dois canais; o dia em que receberem, nada fica vermelho).

Registrado também: `whatsapp`, `voice`, `sms` e `email` têm **zero** linhas em `channel_endpoints`. Não há
endereço a carimbar nesses canais mesmo que quiséssemos, e a premissa D7 do ADR (*"com a Fase E, toda entrada
externa tem linha no registro"*) **é falsa hoje** — a Fase E existe só no webhook; `voice`/`webchat` consultam
com fallback a pool default; `whatsapp`/`sms`/`email` nunca consultam; `webrtc` resolve com um valor de canal
ausente do enum de `ChannelEndpoint`, logo nunca casa.

### Achado de higiene, item próprio — seeds escrevem `origin='live'`

As 15 órfãs vêm de `infra/test/seed_deploy_lens_demo.sh` e `infra/test/seed_epoch_demo.sh`, que inserem
`segments` **direto no ClickHouse por HTTP**, sem passar pelo pipeline — daí não nascer linha em `sessions`.
Assinatura: `started_at` em `10:00:00.000` exato, `pool_id='sac_ia'`, `channel='webchat'`.

O ponto não é a órfã: é que a lista de colunas do INSERT **não inclui `origin`**, então as linhas caem no
default e saem como `live`. O discriminador `origin: live|import|reeval` foi construído exatamente para manter
substrato não-produtivo fora dos relatórios, com filtro default `live` na camada de leitura — e o escritor
passa por fora do mecanismo que existe para ele.

Efeito prático: `sac_ia` tem 85 contatos reais mais 15 sessões sintéticas em `segments`, indistinguíveis por
query. Para a **lista de contatos** isso não vaza (as órfãs caem fora ao juntar com `sessions`), mas vaza em
tudo que agrega `segments` sem juntar — inclusive no filtro *atendido por* proposto em D12, se ele for
implementado como agregação em vez de subconsulta. Conserto barato: os seeds carimbarem `origin`. Não pertence
a este arco — mesmo tratamento que o ADR deu aos achados 9/10/11.
