# ADR — Modelo de três níveis: segment, session, journey

**Status:** aceito — D1–D8 fixadas 2026-08-10, D9 no mesmo dia, **D10–D13 em 2026-08-21**,
**D10.1 e D14 em 2026-08-28**. Seções marcadas ⏳ dependem de medição (ver §8).

> **D14 (2026-08-28):** *SLA é fato do segmento de espera, nunca da sessão.* Hoje `sla_target_ms` é
> coluna de `sessions` e os três leitores de SLA do repositório leem dali — uma sessão só consegue
> carregar **um** alvo, e contato que espera em duas filas perde a segunda violação.

> ⚠️ **A linha *(espera)* da D9 foi REFUTADA por medição em 2026-08-21** e substituída pela **D12**.
> `duration_ms` de `role='queue'` mede o flow do agente de fila, não a espera do cliente — e não existe
> quando o agente não roda. Ver a correção dentro da D9.

**Contexto imediato:** o arco de remoção do legado de workflow por token travou numa pergunta que não
era sobre workflow — *"qual dos três níveis substitui a `WorkflowInstance`?"*. Ninguém conseguia
responder sem hesitar, e é por isso que ela sobreviveu a cinco fases de remoção.

---

## 1. O problema

A plataforma **mede** nos três níveis (`/reports/segments`, `/reports/sessions`, `/reports/journeys`),
mas nunca fixou o que cada um é. A consequência não é confusão de vocabulário — é a criação de
**contêineres a mais**:

- `WorkflowInstance` (Arc 4) foi um quarto nível para um processo que já era sessão + journey.
- A entidade `Journey` (Arc 10) foi um terceiro nível materializado para o que hoje é derivado.

Os dois foram removidos, cada um num arco próprio, e o segundo só depois que o primeiro provou o
argumento. Um modelo sem definição não impede o próximo — apenas garante que ele custe outro arco.

> **A pergunta que este ADR responde de uma vez:** *dado um fato, em qual nível ele mora?*

---

## 2. D1 — As três definições, e o discriminador é IDENTIDADE

| Nível | É | Unidade de medida de | Identidade |
|---|---|---|---|
| **segment** | a janela de **um participante** dentro de uma sessão | quem fez o quê, por quanto tempo; grão de avaliação (N1) | `segment_id` |
| **session** | **um contato** — uma sala de conferência, um engajamento de canal | roteamento, capacidade, admissão, `close_reason` | `session_id`, **estável através de suspend/resume** |
| **journey** | **um processo de negócio** sobre N contatos | desfecho do processo, voz do cliente no grão N3 | raiz canônica, **derivada** — nunca entidade |

### 2.1 O corte entre session e journey NÃO é tamanho

*"Passou de uma sessão"* não pode significar **longo**, **multi-segmento** nem **complexo**. Um workflow
que suspende 48 h e retoma tem N+1 segmentos e dura dias — e é **uma sessão**, por decisão explícita do
Arc 19 (`session_id` é o identificador persistente *"incluindo múltiplos ciclos de suspend/resume"*).

**O discriminador é: nasceu um contato NOVO?** Se sim, journey. Se a mesma sala persiste — ainda que
suspensa por dias — continua sendo uma sessão.

Sem esta precisão, a leitura ingênua da regra empurraria suspend/resume para journey, que é exatamente o
erro que o Arc 19 desfez. *Duração é consequência; identidade é o critério.*

### 2.2 Suspend/resume produz SEGMENTOS, não sessões

O `suspend()` fecha o segmento e devolve o agente ao pool; o resume aloca de novo e abre **outro**
segmento. A suspensão é o **intervalo entre eles**, e disso decorre a D4.

> ⚠️ **Correção medida 2026-08-10 — a regra de CONTAGEM que eu tinha escrito aqui estava errada.**
> A v1 dizia *"N suspensões ⇒ N+1 segmentos"*. Falso: `segments` inclui um segmento de **fila**
> (`role='queue'`, `agent_type='system'`, `outcome='handoff'`) antes do segmento do agente. Medido numa
> sessão webhook suspensa: dois segmentos, sendo o primeiro a fila (92 ms) e o segundo o agente
> skill-flow (`role='primary'`, `outcome='suspended'`, 30 ms) — **com zero resumes**.
>
> Consequência prática: **`count(segments) > 1` NÃO é proxy de "houve retomada"** — na maioria dos casos
> significa "houve fila". Qualquer derivação da D4 que conte segmentos para inferir ciclos de execução
> precisa filtrar por `role`/`agent_type`, e é justamente esse tipo de derivação implícita que a
> transição como primeira classe existe para tornar desnecessária.
>
> **Achado adjacente:** ver §2.3 — `sequence_index` não é chave de ordenação confiável.

### 2.3 A transição, MEDIDA — ciclo completo suspend → delegate → resume

Sessão `5553c72a` (2026-08-10), workflow `skill_formfill_demo_v1`, retomada por submit real no Console:

| # | `agent_type` | `sequence_index` | janela | `outcome` |
|---|---|---|---|---|
| 1 | `native` | 0 | 19:16:31.432 → .480 (48 ms) | `suspended` |
| 2 | `human` | 0 | 19:22:54.020 → 19:23:24.591 (30 s) | `NULL` |
| 3 | `native` | **2** | 19:23:24.577 → .590 (13 ms) | `resolved` |

**Confirma a D4 e precisa o que ela materializa.** Os segmentos 1 e 3 são o **mesmo agente skill-flow**,
antes e depois. A transição é a lacuna entre `ended_at(1)` e `started_at(3)` — **6 min 23 s** aqui —, e
ela **não é vazia**: contém o segmento do delegatário (2). Ou seja, a transição tem começo, fim, motivo
e **conteúdo**, e nenhum dos quatro tem hoje um lugar nomeado. Derivá-la exige saber que se deve parear
segmentos do mesmo `agent_type`/participante e ignorar o que estiver no meio — precisamente a derivação
implícita que a D4 existe para eliminar.

⚠️ **`sequence_index` NÃO ordena — e são DOIS problemas sobrepostos**, investigados 2026-08-10:

1. **Defeito de escrita, causa raiz única.** O índice é atribuído no `participant_joined` mas **não é
   persistido junto com o `segment_id`** no Redis (`main.py:918-922`). No `participant_left` ele é
   reconstruído como `0` (`main.py:6759`) ou omitido (default `0`, `main.py:3030`) — e como
   `analytics.segments` é `ReplacingMergeTree` **a linha do left substitui a do join**, apagando o
   índice correto. Atinge **todo segmento humano e todo especialista**; os nativos escapam só porque
   join e left compartilham escopo léxico na mesma função. Reproduz exatamente o `0, 0, 2` medido acima.
2. **Escopo genuinamente mais estreito que a spec, e isso NÃO é defeito.** `queue`, sintéticos
   (`system`: outage, mute queue) e **especialistas de conferência** ficam fora do contador por decisão
   (`main.py:4136-4145`, `5412-5417`). O `0, 0` de `b934b602` é comportamento projetado.

**Corolário que vale para a spec:** mesmo depois de corrigir (1), o campo **continua não sendo ordenação
total** dos segmentos da sessão — é *"ordem entre segmentos primários não-sintéticos"*, e nada no nome,
no Zod ou no DDL diz isso. **Ordenar por `started_at` permanece o único caminho correto**, e a D4 não
deve depender do índice.

O conserto de (1) e suas consequências (atribuição de agente em relatórios de qualidade) saem como item
próprio — ver `TODO.md` § "`sequence_index` apagado pelo participant_left".

> **Conserto executado 2026-08-10, e ele produziu um achado que pertence à D4.** A atribuição
> (`_session_agent_attribution_sql`) **filtra** por `role='primary' AND agent_type != 'system'`, escopo
> que coincide com o do contador — então o conserto torna o índice único ali e o empate do `argMax`
> desaparece. Sobra o que o índice nunca causou: a regra é *"último primário não-sintético"*, e na sessão
> `5553c72a` o último primário é o segmento nativo de **13 ms** que só processou o resume — a lente de
> qualidade credita a máquina pelos 30 s de trabalho do humano, e depois do conserto passa a errar
> **deterministicamente**.
>
> É a D4 cobrando o preço em outro lugar: sem a transição nomeada, "quem atendeu esta sessão" tem de ser
> adivinhado a partir da posição do segmento, e a posição não sabe distinguir *fez o trabalho* de
> *fechou a porta*. Some-se a isto a §8.1b — o mesmo caminho já produz duas respostas para "quanto durou".
> **A transição como primeira classe deixa de ser conveniência de relatório: dois defeitos vivos de
> medição a apontam.**

*(Sobreposição de 14 ms entre o fim do segmento 2 e o início do 3 é esperada: o resume dispara enquanto
o segmento do humano ainda está fechando.)*

*(Esta sessão **não** tem segmento de fila, ao contrário de `b934b602`. A explicação que escrevi aqui —
*"item de pull não passa pela mesma fila que o push"* — **está errada, e foi refutada em 2026-08-10** por
uma segunda execução da MESMA smoke (`f1ecc571`, mesmo skill, mesmo pool), que produziu
`queue/system 2 620 ms handoff` **antes** do primeiro primário. O segmento de fila é da entrada do
workflow no pool dele, não do item de pull, e apareceu numa execução e não na outra — o discriminador é
**haver instância livre naquele instante**, não push×pull. Reforça o §2.2 por um motivo mais forte do que
eu tinha: a composição de segmentos varia não só por caminho, mas **entre duas execuções do mesmo
caminho**. Contá-los para inferir ciclos é frágil por natureza — e uma amostra de uma execução não
estabelece a composição de nenhum caminho.)*

---

## 3. D2 — A regra dual

O `CLAUDE.md` já tem metade:

> Never store a narrower-scope fact in a wider-scope field — derive it where the scope is known.

Falta a outra metade, e as duas são duais:

> **E não crie contêiner largo para fato que cabe num estreito.**

Separadas, cada uma deixa passar o oposto da outra: a primeira sozinha não impediu a `WorkflowInstance`
(que não guardou fato largo em campo estreito — criou um contêiner novo); a segunda sozinha não impediria
`session.human_agent_participant_id` (que colapsa em multi-humano). Enunciadas juntas, cobrem os dois
modos de falha.

---

## 4. D3 — Journey tem UMA regra de pertença; o resto é FILTRO

Pertença à journey é **exatamente uma coisa**: componente conexa sob **(proveniência ∪ alias)**,
identificada pela raiz canônica por union-find.

**Qualquer outro agrupamento é filtro, não journey.** Mesmo cliente → filtro por `customer_id`. Mesma
campanha → filtro por `campaign_id`. Mesmo assunto → filtro por tag.

O risco que isto fecha: *"tudo que passa de uma sessão é journey"* transformaria journey em balde, e
balde acumula semânticas incompatíveis — a identidade por union-find deixaria de significar uma coisa só.
O ADR do Cliente 360 já decidiu assim para o caso do cliente (*"jornadas do cliente = filtro `customer_id`
no `/reports/journeys`"*); aqui isso vira regra geral.

---

## 5. D4 — A transição é PRIMEIRA CLASSE

**Decidido:** a transição entre segmentos ganha existência própria.

### Por que não cabia em nenhum dos três

`suspend_reason` (`approval|input|webhook|timer`) e `resume_expires_at` não são fato do segmento que
fechou, nem do que abriu, nem da sessão — são fato da **lacuna entre segmentos**. Foi por isso que os
dois ficaram órfãos no Arc 19: não havia onde colocá-los, e a `WorkflowInstance` legada seguiu sendo a
única portadora. O buraco não era de dado; era de **lugar nomeado**.

### O que a transição carrega

| Fato | Hoje |
|---|---|
| `suspend_reason` | só em `workflow_events` (tabela cujo produtor está morto) |
| `resume_expires_at` | só em Redis, sem report |
| `suspended_at` | derivável de `segments.ended_at` do segmento N |
| `resumed_at` | derivável de `segments.started_at` do segmento N+1 |
| `resume_origin` (`same_channel\|token\|identity`) | existe no evento `session_resumed`, sem persistência analítica |

Os dois primeiros são **net new**; os demais existem e passam a ter nome no mesmo lugar, em vez de
exigirem que o leitor saiba juntar dois segmentos adjacentes.

**Alternativa recusada:** pendurar no segmento que fecha. É mais barato e defensável (*quem suspende é
quem estava lá*), mas força *"por que a sessão está parada?"* a ser lido do segmento **anterior** — uma
pergunta sobre o presente respondida por um registro do passado. E não tem onde pôr `resume_expires_at`,
que é fato do **futuro** daquela lacuna.

⏳ **Forma concreta (tabela nova × colunas em `segments` × evento próprio) fica para a spec**, e depende
da medição §8.2: se a transição não for observável no dado real hoje, a decisão sobre onde materializá-la
muda.

---

## 6. D5 — `sessions.journey_id` permanece como PROJEÇÃO, com reconciliação

**Decidido:** a coluna fica. Merge é exceção; pagar no raro é melhor que pagar em toda leitura.

**Com três condições, e a terceira não é zelo:**

1. **A autoridade continua sendo o union-find.** A coluna é projeção declarada, nunca fonte.
2. **O refresh é TRANSITIVO.** Merges compõem: se A→B e depois B→C, refrescar só o par recém-unido deixa
   as sessões de A apontando para B, que não é mais raiz. O refresh tem de caminhar até a raiz canônica
   antes de escrever — ou seja, ele faz o union-find de qualquer jeito, só que uma vez.
3. **Existe conferência periódica** que compara coluna × union-find e **loga o drift**.

A (3) é o que separa cache de segunda fonte. É o mesmo caso do contador de ocupação, sobre o qual o
`CLAUDE.md` escreve: *"o contador só existe porque é CONFERIDO… sem ela este contador é o `active_count`
que o arco removeu, e deve sair junto"*. E o modo de falha aqui é silencioso — `journey_id` obsoleto parte
uma journey em duas no `GROUP BY`, sem nada ficar vermelho.

⚠️ **Custo real do refresh, que o desenho tem de encarar:** `sessions` é `ReplacingMergeTree` — não há
UPDATE. Refrescar exige **reinserir a linha inteira** de cada sessão da componente, reidratada. É
exatamente a operação que o `CLAUDE.md` marca como origem de três bugs de `sessions` num dia só. O
refresh é raro, mas não é barato, e escrevê-lo parcialmente é pior que não escrevê-lo.

---

## 7. D6–D8 — Consequências que mudam outros arcos

### D6 — Workflow DECLARA uma journey; journey OBSERVA uma

Não são contêineres concorrentes: são mecanismo e medição. E o declarado **produz** o observado — um
workflow que dispara filhos cria sessões que herdam `root_session_id`, e elas formam journey por
proveniência. **O orquestrado é caso particular do emergente, não alternativa a ele.**

Isto explica, melhor do que a Fase F explicou, *por que* a `WorkflowInstance` era redundante: a execução
do workflow **é** uma sessão; o processo que ele orquestra **é** a journey. Nunca houve terceira coisa —
havia um nome para a segunda.

**Consequência acionável:** a journey precisa dizer *"fui declarada pelo skill X"* × *"emergi"*.
`spawn_reason` existe no grão de sessão; ⏳ conferir se está exposto no grão de journey. Nenhum relatório
de processo é honesto sem esse discriminador — processo desenhado e processo descoberto não se comparam.

### D7 — Merge compensa proveniência não observável; sua frequência é MÉTRICA

O merge existe porque, no instante do contato, o vínculo às vezes não é observável. Volta por caminho que
carrega o vínculo (resume token, pending-by-identity com âncora `possessed`) ⇒ proveniência funciona, sem
merge. Volta por caminho que não carrega (liga do nada, canal novo, identidade não resolvida) ⇒ a
plataforma cunha raiz nova — **corretamente**, porque na hora não havia como saber — e o merge é o reparo.

**Consequência acionável:** frequência de merge é **métrica de qualidade do resolvedor de identidade**.
Muitos merges ⇒ o Resolvedor (Fase A/B) está deixando de ligar o que deveria. O dado já existe
(`journey.merges` / `journey_aliases`) e ninguém o usa como indicador.

### D8 — A porta EXTERNA de resume é pré-requisito de qualquer remoção

Achado ao analisar o cenário 3, e ele reordena o arco de workflow.

A discriminação entre "novo contato" e "retomada" é **por ROTA, não por canal**:

| Rota | Efeito |
|---|---|
| `POST /v1/channels/webhook/{identifier}` | resolve pelo registro → `conversations.inbound` → **sessão nova** |
| `POST /v1/channels/webhook/resume/{token}` | hash Redis `{tenant}:resume_tokens → session_id` → **retoma**, novo segmento |

O `resume_token` é uma **capability**: opaco, ligado a uma sessão, escrito por `suspend`/`delegate`/
`collect`. Não endereça pool nem canal — endereça *execução suspensa*. Por isso não passa pelo registro de
endpoints e não cria sessão: **não é endereço de entrada, é ponteiro para dentro.**

⚠️ **O buraco:** essa rota vive em `/v1/*`, o prefixo que o `CLAUDE.md` acaba de declarar que **não pode
estar na borda**. Hoje o sistema externo do Padrão 2 alcança o resume via `POST /v1/workflow/resume` da
workflow-api (proxy para aquela rota) — uma das poucas coisas realmente vivas no legado. **Remover a
workflow-api tira a única porta de resume alcançável de fora, sem substituto.**

A saída não é expor `/v1/*`. É dar ao resume uma porta externa simétrica à do trigger — algo como
`POST /channel/webhook/resume/{token}`. O token **já é** a credencial (opaco, ligado a uma sessão, de uso
único pela Camada F), então a rota é autoautenticada, como o link de survey: sem `auth_required` e sem
registro, porque não há endereço a registrar.

**Ordem imposta (D7 do ADR de webhook, outra vez):** a porta externa de resume existe **antes** de
qualquer remoção.

---

## 8. ⏳ Medições pendentes

Nenhuma decisão acima depende delas; a **spec de implementação** depende.

| # | Pergunta | Resultado (2026-08-10) |
|---|---|---|
| 8.1 | Há dado legado em `workflow.instances` / `workflow_events`? | ✅ **NÃO — tudo zero.** `instances=0 · webhooks=0 · collects=0`, e `workflow_events` **sem nenhuma linha**. Previ resíduo de `started`/`resumed`; não há. **Não existe pergunta de backfill.** O `/reports/workflow-summary` da aba default de `/flow/processos` agrega sobre tabela vazia. |
| 8.2 | Sessões webhook com >1 segmento existem? | ✅ **Sim, mas a leitura era outra** — ver §2.2. O 2º segmento é a **fila**, não uma retomada. |
| 8.3 | TTL de `{tenant}:pipeline:{sid}` | ✅ **≈60 200 s restantes** (TTL aparente de 24 h). `current_step`/`pipeline_state` cobrem o **recente**, não o histórico — para histórico o substrato é `session_pipeline_state` (R5/B). |
| 8.4 | Divergência `opened_at` × `min(segments.started_at)` | ⚠️ **Contestado** — `−13 ms`, `−49 ms`, `−11 ms` (ordem sadia) em 3 sessões, nenhuma com retomada limpa. Ver §10.1. |
| 8.5 | `journey_aliases` tem linhas? | ✅ **0.** O merge **nunca rodou** neste ambiente ⇒ o refresh da D5 nasce sem dado de teste e a reconciliação precisa de cenário sintético. |
| 8.6 | **Teste decisivo do re-carimbo** | ✅ **NÃO HÁ RE-CARIMBO.** Sessão `5553c72a`, ciclo completo e real (suspend 19:16:31 → claim humano → submit no Console → resume → `resolved` 19:23:24): `opened_at = 19:16:31.389`, **43 ms ANTES** do primeiro segmento e **6 min 53 s antes** do resume. O valor original sobreviveu à retomada. Hipótese (a) da §10.1 **refutada**; a justificativa do comentário da Fase E.2 não se sustenta neste caminho. |
| 8.7 | **Quantas definições de duração existem?** | ✅ **DUAS vivas + uma declarada e não implementada** — ver §8.1b. Vira **D9**. |

### 8.1b `handle_time_ms` / `business_duration_ms` — o que está vivo, e o que só está escrito

Levantado ao medir 8.6 e **fechado** por leitura direta. Para a sessão `5553c72a` (6 min 53 s de
wall-clock, ~30,6 s de trabalho somando segmentos):

**Vivas:**

1. **Coluna armazenada = `NULL`** para webhook ⇒ `avgOrNull(handle_time_ms)` (`query.py:94`,
   `admin_query.py:115/168`) **exclui** essas sessões das médias. Coerente com o que a Camada F já
   registrou (*"`handle_time_ms` é NULL nas internas"*).
2. **`/reports/sessions` calcula na query** (`reports_query.py:647-657`): webhook →
   `closed_at − first_started_at` = **wall-clock incluindo as esperas** ⇒ 6 min 53 s.

**Declarada e NÃO implementada:**

3. O `CLAUDE.md` (Arc 19) afirma **como fato**: *"TMA webhook = `SUM(segment.duration_ms)`, não
   wall-clock"* ⇒ daria ~30,6 s. Não existe tal computação. O código diz o contrário, e diz na cara:
   `reports_query.py:898-899` documenta o mesmo refino como **adiado** —
   *"refino 'exclui suspenso via `SUM(segment.duration_ms)`' fica p/ iteração"* — no
   `business_duration_ms` da journey, que hoje também é wall-clock (`min(opened_at) → max(closed_at)`).

> ⚠️ **Correção de leitura minha, no mesmo dia:** cheguei a escrever aqui *"três respostas possíveis"*.
> São **duas vivas**; a terceira é intenção documentada como implementação. É a família de
> *"descrição de configuração não é configuração"*, agora aplicada a métrica — e desta vez o tell estava
> no próprio código, que registra a pendência a poucas linhas de distância.

> ⚠️ **Segunda correção, 2026-08-10 (inventário estático para a spec): eram TRÊS, não dois — e há um
> quarto NOME.** (1) coluna crua (`query.py:94`, `admin_query.py:115/168`, `timeseries_query.py:279`,
> `reports_query.py:708`); (2) recomputado por canal (`reports_query.py:647-657`); (3) **wall-clock ao
> vivo** em `/sessions/live` (`sessions.py:127`, `now − opened_at`, sem tocar a coluna); e (4)
> `/sessions/customer/{id}` renomeia o valor para **`duration_ms`** (`sessions.py:268-279`), consumido
> pela `HistoricoTab`. Contei dois porque procurei o campo nos relatórios e não nos endpoints ao vivo.
>
> ⚠️ **E "`handle_time_ms` é NULL para webhook" é EMPÍRICO, não derivável do código.** `models.py:319-330`
> produz NULL só nas condições que lista; **não existe caminho que garanta** que uma sessão webhook nunca
> receba um `contact_closed` com `started_at`+`ended_at` válidos. A frase entrou no `CLAUDE.md` como
> invariante e é observação de medição — a D9 item (iii) depende dela e precisa medi-la antes.
>
> ⚠️ **Os dois grãos já discordam POR DECISÃO:** sessão **inclui** o suspenso de propósito
> (`reports_query.py:645-646`: *"Inclui as esperas (suspends) — é a duração real do caso"*), journey
> registra **excluir** como refino adiado (`:898-899`). Não é descuido dos dois lados; é uma escolha em
> cada lado, e unificar é escolher uma.

**O que sobra de real:** um nome (`handle_time_ms`) com três comportamentos vivos e um quarto nome de
saída, e o `CLAUDE.md` prometendo um quinto. Nenhum dos três separa as duas
grandezas que a §7 distingue: **trabalho consumido** (soma de segmentos) × **processo decorrido**
(wall-clock com esperas). O sintoma aparece aqui como fator ~13× entre as duas, na mesma sessão.

## D9 — a duração ganha DEFINIÇÃO ÚNICA, e é item deste arco

**Decidido 2026-08-10** (a pedido, escopo do arco de workflow).

As duas grandezas são legítimas, precisam de nomes diferentes — e, o achado que fecha a questão,
**estão em UNIDADES diferentes:**

| Grandeza | Unidade | Pergunta | Fonte |
|---|---|---|---|
| **tempo-agente** | agente × tempo | quanto RECURSO este atendimento consumiu? | `SUM(segment.duration_ms)` filtrado |
| **tempo decorrido** | tempo | quanto o caso levou, do ponto de vista do cliente? | wall-clock (sessão) / `min→max` (journey) |
| *(espera)* | tempo | quanto o cliente esperou? | ⚠️ **REFUTADO 2026-08-21 — ver bloco abaixo** |

> ⚠️ **Correção medida em 2026-08-21: `duration_ms` de `role='queue'` NÃO é a espera do cliente.**
> É a duração do **flow do agente de fila** — `_q_joined_at` é carimbado imediatamente antes de
> `activate_native_agent` e o fim é o retorno dessa chamada (`main.py:5924-5998`). Transcrição de um caso
> real (sessão `sess-e2e-2920b0d1-…`, segmento `queue` de 6 s) mostra o agente **conversando** com o
> cliente dentro da janela. É segmento de trabalho, e vinha sendo lido como espera.
>
> Pior: ele **só existe se o agente de fila rodar**. Contato real medido no mesmo dia
> (`81d194ad-…-ce81b30e8343`) esperou **21,35 s** entre o fim do segmento de IA (`escalated_human`,
> 18:14:46.926) e a entrada do humano (18:15:08.276) e produziu **zero** registro — nem segmento, nem
> linha em `session_transitions`, nem nada. **A janela de espera nunca existiu como fato**, nem no caso
> atendido (onde o segmento mede outra coisa) nem no mudo (onde não há segmento).
>
> Consequência para a D9: a linha *(espera)* não tem fonte hoje. Ver **D12** e
> [`docs/product/fila-janela-de-espera-2026-08-21.md`](../product/fila-janela-de-espera-2026-08-21.md).

⚠️ **A soma NÃO é uma duração, e chamá-la assim é o erro a evitar.** Segmentos **se sobrepõem**, por três
mecanismos distintos:

1. **`@mention` — SEMPRE paralelo.** O agente mencionado entra na conferência ao lado do primary, que
   segue vivo (é ele quem emite a menção: só `role: primary` ou `role: human` podem). Não há caso em que
   um segmento de menção seja sequencial ao principal. **É o caso mais perigoso para a soma**, porque é
   frequente, é rotina de operação e não deixa nenhum rastro que distinga o segmento como paralelo —
   nem `parent_segment_id` é garantia de leitura para quem só somou a coluna.
2. **Especialista de conferência** — nasce e morre **dentro** da janela do primary
   (`main.py:4130-4145`, lendo `session:{sid}:primary_segment`, chave que só existe porque o pai está
   aberto).
3. **Hooks posatt** (NPS, wrap-up) — paralelos entre si por desenho (Arc 14).

Logo `SUM ≥ wall-clock` sempre que houver menção, conferência ou posatt, e `SUM ≤ wall-clock` quando
houver lacunas (suspensão, intervalo entre segmentos). **Os dois nunca são comparáveis, e a soma NUNCA
deve ser usada para obter tempo de sessão.** Não existe no repositório query alguma que faça união de
intervalos; o refino está registrado como adiado em `reports_query.py:898-900`.

**Filtros mínimos para tempo-agente**, com precedente vivo na plataforma
(`reports_query.py:3725-3743`, `busy_ms` da ocupação humana):

```sql
agent_type != 'system'                 -- sintético: outage, mute queue. Zero recurso.
AND role IN ('primary','specialist')   -- exclui 'queue' (espera, não trabalho)
AND duration_ms IS NOT NULL            -- segmento aberto some silenciosamente no sum()
```

⚠️ **Caso ambíguo que exige decisão, não filtro:** `role='queue'` **com `agent_type='native'`** é fila
**atendida** — espera para o cliente, consumo real de instância de IA. Todos os relatórios atuais a
excluem. E o ACW: `close_reason='task_submitted'` é trabalho; `acw_expired`/`acw_supervisor_closed`
medem claim→prazo, que é **abandono**, e o relatório de wrap-up já os corta (`reports_query.py:1891-1893`).

Entra no arco: **(i)** fixar os dois nomes e implementar o que falta; **(ii)** fazer o `CLAUDE.md` parar
de afirmar o não-implementado; **(iii)** decidir o que `avgOrNull` deve fazer com webhook — hoje a
exclusão é efeito colateral de a coluna ser `NULL`, não decisão.

⚠️ **`business_duration_ms` da journey tem o mesmo defeito e o mesmo refino adiado.** Consertar só o grão
de sessão deixaria os dois níveis discordando — e a §7 existe justamente para que não discordem.

### D9.1 — O domínio real de `role`/`agent_type` diverge do declarado

Levantado junto, e a spec tem de usar o real:

- **`role`** — o Zod declara `primary|specialist|supervisor|evaluator|reviewer|queue`. **`supervisor`,
  `evaluator` e `reviewer` não têm produtor nenhum.** Supervisor de barge-in escreve *mensagem* com
  `author.role`, não segmento; avaliação roda em sessão própria. Três valores aspiracionais.
- **`agent_type`** — o Zod declara `ai|human`. **A realidade é `native|human|system`.** Nenhum produtor
  emite `"ai"` (só testes e o tipo da UI). Filtrar por `agent_type='ai'` devolve vazio.
- O DDL não tem enum e o consumer copia sem validar (`models.py:844`); o **evento Kafka
  (`ConversationParticipantEventSchema`) nem declara `sequence_index`**. Não há guarda em ponto algum do
  pipeline — nada detectaria um produtor que omite ou inventa valor.

⚠️ **Amostra pequena e enviesada.** As sessões medidas nasceram **depois de um `--wipe`**, criadas por
gates e smokes; nenhuma exercita suspend→resume→continua→fecha. Sete sessões, das quais uma passou pelo
resume e foi encerrada pelo *scanner*. Todo `0` aqui é *"não houve o fenômeno na amostra"*, não
*"o fenômeno não ocorre"* — a distinção que este repositório já pagou caro para aprender.

---

## D10–D13 — vocabulário de contato, pool e espera

**Aceitas 2026-08-21.** Discussão e medição em
[`docs/product/fila-janela-de-espera-2026-08-21.md`](../product/fila-janela-de-espera-2026-08-21.md).

As quatro caem sob a **D2** (regra dual de escopo) — é isso que dá confiança de que não são preferência:
cada uma põe um fato no escopo dele.

### D10 — Pool de ENTRADA é da sessão; pool que ATENDE é do segmento

`entry_pool_id` é fato da **sessão** (imutável, nasce com o contato; já entregue pela F1b como
first-write-wins). Pool que atende é fato do **segmento**. `attended_pool_ids` na linha da sessão é
**projeção** — legítima para filtrar, nunca verdade, e por isso a F3 a construiu como pós-passe.

**Consequência que fecha uma sobrecarga viva:** o segmento de fila carrega hoje o pool de **destino**
(`main.py:5919-5923`, dimensão do relatório Fila/SLA) enquanto quem executa é o pool de **fila**
(`_flow_pool_id`). Um campo carrega a verdade que o relatório quer ao custo da que a atribuição quer.
Com a D12 são dois registros, cada um com o seu pool, e a sobrecarga desaparece sem campo novo.

**Aplica-se também ao runtime:** em `session:{id}:meta`, o canal escreve `entry_pool_id` (dono: canal) e o
bridge escreve `pool_id` (dono: bridge). Isso fecha a fatia C de
[`session-meta-ownership.md`](../guias/session-meta-ownership.md) **e** a maior parte da fatia B — hoje
webchat/webrtc não roubam campo do bridge, escrevem *entrada* num campo chamado *atendimento*; renomeado,
a violação some por construção, e "recusar campo alheio" passa a não ter violador legítimo a acomodar.

### D11 — `session` é qualquer acesso à plataforma; **"contato" é FILTRO, não nível**

Os três níveis do §2 continuam fechados: `segment · session · journey`. `session` = **qualquer acesso à
plataforma por qualquer canal** — inclusive maquinaria (`trigger`), retorno assíncrono e sessão de
wrap-up. **"Contato" deixa de ser um nível e passa a ser um filtro** sobre um atributo da sessão.

Medido: 518 sessões no tenant, **96 delas `trigger`** — maquinaria sem cliente do outro lado. Na journey
de referência, 2 das 4 sessões são acesso do cliente. *Sessões ≠ contatos* já era decisão as-built em três
lugares independentes (`purpose=internal`, exclusão do wrap-up, `spawn_reason`); o que faltava era admitir
que o termo sobrecarregado é **"contato"**, não o modelo.

**Journey NÃO substitui isto.** Journey agrupa, não classifica: uma journey com 4 sessões não diz quais 2
o cliente tocou. Usá-la como resposta a *"quantas vezes o cliente nos procurou"* move o erro de cabeçalho
um nível acima. É a **D3** aplicada ao caso: pertença tem uma regra, o resto é filtro.

### D12 — A janela de espera é fato de ROTEAMENTO, e precisa de produtor próprio

Refuta a linha *(espera)* da D9 (ver a correção lá). A espera não pode ser efeito colateral da ativação do
agente de fila, porque essa ativação depende de config que degrada em silêncio — e quando ela não ocorre,
não sobra registro nenhum.

**Quem tem o fato é o routing-engine**, que já loga as duas bordas (`Queued session=… — no agents
available` / `Contact persisted to queue` na entrada; `Queue cleanup: removed … reason=…` na saída). O
bridge só sabe da espera quando decide entreter.

**Veículo = segmento**, não tabela nova: `session_transitions` é livro-razão de suspend/resume com token
(`resume_token`, `step_id`, `suspend_reason`, `resume_expires_at`) — **o nome é mais largo que o
conteúdo**, não há `from_state`/`to_state`, e alargá-la deixaria a maioria das colunas nula por linha.
Criar uma terceira tabela violaria *"nunca inventar o 3º mecanismo"*.

Duas emendas ao veículo:

1. **`segment_id` determinístico** derivado do `session_id` — hoje `uuid.uuid4()` por invocação
   (`main.py:5924`). O padrão existe no repositório (quality-ingest deriva ids determinísticos para
   idempotência). Com id determinístico, invocação repetida produz a mesma linha e o `ReplacingMergeTree`
   deduplica — o defeito de re-entrância medido some sem guard novo.
2. **O agente de fila é `specialist`**, não `queue`: ele é agente, faz trabalho de agente e consome
   inferência. `primary` está descartado por razão dura — o invariante analítico *"atendido = primeiro
   segmento `primary` da sessão"* faria o contato parecer atendido no instante em que entrou na fila.
   ⚠️ **Efeito colateral a declarar:** `agent_time_ms` filtra `role IN ('primary','specialist')`, logo a
   reclassificação **move o tempo do agente de fila para dentro do tempo de agente**. É defensável (é
   trabalho de IA), mas muda TMA/AHT do ambiente — mudança de número sem pedido é a família *valor
   plausível*, e tem de entrar declarada.

⏳ **Aberto, não medido:** se `retencao_humano` opera em pull, existem **duas** esperas distintas — "sem
agente disponível" (fila) e "agente disponível que ainda não reivindicou" (inbox). Para SLA as duas
contam; hoje nenhuma tem registro confiável.

### D13 — Discriminador de contato é ÚNICO e ternário, derivado de `spawn_reason`

| `spawn_reason` | Classe |
|---|---|
| `NULL` | acesso **inbound** |
| `collect` | acesso **outbound** |
| `trigger` / `delegate` | **interno** — não há cliente |

**`pools.purpose` deixa de ser critério de contato** e volta a ser só atributo de pool. É a D2 outra vez:
*"esta sessão teve cliente"* é fato da **sessão**, não do pool — e é exatamente por classificar pelo pool
que `aprovacao_credito` escapa hoje de `_apply_contact_scope`.

Binário não serve: com só `inbound|outbound`, as 96 sessões `trigger` teriam de virar outbound, inflando
justamente o número que a D11 existe para tornar confiável.

⚠️ **Medido 2026-08-21: a classe *outbound* tem ZERO amostras** (`collect` e `delegate` ausentes de
`spawn_reason`), 13 dias depois do F0 do histórico unificado. A decisão vale **no modelo**; *renderizar* a
terceira classe numa tela hoje seria construir o ramo que nada exercita — a armadilha ANI/DNIS.
`delegate = 0` segue não explicado, embora o carimbo exista em `webhook.py:1604`.

---

## D10.1 / D14 — emendas de 2026-08-28 (pool da espera, e o grão do SLA)

Nascidas de uma pergunta durante a implementação da D12: *"se a fila é sempre um segmento com um pool
próprio, diferente do destino, o TMA do destino não fica intacto?"*. A discussão separou três coisas
que estavam juntas — duas já resolvidas, uma que muda decisão.

### D10.1 — O pool do segmento de ESPERA é o DESTINO; a fila que executou é campo PRÓPRIO

**Decidido.** São dois fatos e precisam de dois campos:

| Fato | Campo | Por quê |
|---|---|---|
| para onde se esperou | `pool_id` do segmento de espera = **destino** | é a dimensão do relatório e onde o SLA está configurado |
| qual fila executou | campo próprio (`queue_pool_id`) | identidade do deploy que atendeu a espera |

**Por que o pool da espera NÃO vira o pool de fila** — e isto é medível, não preferência: o Fila/SLA
agrupa por `if(segs.q_pool != '', segs.q_pool, ss.pool_id)` (`reports_query.py:5741`), ou seja, **o pool
do segmento de fila É a dimensão do relatório**. Como a fila é a *default do tenant* por desenho, movê-lo
para o pool de fila faria **todas as esperas colapsarem numa linha só**, e *"para qual destino se espera
demais?"* deixaria de ter resposta. A espera é espera **por** alguém; o destino é o que lhe dá sentido.

⚠️ **E o modelo "sempre um pool de fila distinto" quebra no tier MUDO**, que é definido justamente por
*pool sem `queue_config`*: não há pool de fila. Mantê-lo literal exigiria **inventar** um pool sintético
que ninguém configurou e que apareceria em relatório agrupado por pool — *valor plausível* com cara de
configuração. O campo próprio já existe como diagnóstico no marcador do bridge
(`"queue_pool_id": _flow_pool_id`, marcado lá como *"diagnóstico, não contrato"*); promovê-lo a contrato
é barato e não custa a dimensão.

**O que a proposta acerta, e é importante:** *"pool de fila sempre distinto do destino"* **não é modelo
alternativo — é o estado-alvo da CONFIGURAÇÃO**, que `queue_config.pool_id` já suporta e que o defeito
do `skill_id` legado bloqueia (`_flow_pool_id = "" or pool_id` cai no próprio destino). Segundo caminho
independente chegando à mesma ordem: **consertar o tenant default vem antes.**

**Sobre o TMA, os dois modelos são equivalentes** — `agent_time_ms` filtra
`role IN ('primary','specialist')`, logo o segmento de espera (`role='queue'`) está fora **por
construção, qualquer que seja o pool dele**. A contaminação nunca veio da espera: veio de reclassificar
o *agente* de fila para `specialist`, e o conserto é a D10 aplicada a **esse** segmento
(`pool_id → _flow_pool_id`), já escrita.

### D14 — SLA é fato do SEGMENTO DE ESPERA, nunca da sessão

**Decidido**, por argumento de domínio: *não existe SLA por sessão na prática de contact center — o
alvo é do segmento/fila, e somar esperas contra alvos diferentes produz um número sem utilidade.*

**Estado as-built, medido:** `sla_target_ms` é **coluna de `sessions`** (`clickhouse.py:114`, ALTER
`Nullable(Int64)`), populada por `parse_routed`, e **as três computações de SLA do repositório leem
dali** — `query.py:240` (`wait_time_ms <= sla_target_ms`), `reports_query.py:3802` (overlay de SLA) e o
Fila/SLA (`:5743`, via `ss.sla_target_ms`). **Nenhuma lê do segmento**, e `segments` não tem a coluna.

**É a D2 outra vez, e do lado que este ADR já nomeia:** o alvo de SLA é fato de **uma espera** —
guardá-lo na sessão força **um valor por sessão**. Consequência concreta: contato que espera 30 s por
`retencao_humano` (alvo 300 s), é transferido e espera 120 s por outro pool (alvo 60 s) só consegue
carregar **um** alvo; a violação da segunda espera fica invisível, e a média mistura populações que não
são comparáveis. Com o produtor da D12 a espera passa a ter registro por segmento — e é ali que o alvo
tem de estar.

#### ⚠️ D14.1 — antes do grão, há um problema de IDENTIDADE: o campo é declarado como um SLA e consumido como outro

Levantado na mesma discussão, com a tela de config à vista (2026-08-28). **O grão errado não é o defeito
mais grave — o campo mede outra coisa que a que promete.**

| | O que diz | Onde |
|---|---|---|
| **Rótulo da UI** | *"Total service SLA (ms)"* / *"SLA total do atendimento (ms)"* | `configRecursos.json:29`, **nos dois locales** |
| **Todos os consumidores** | comparam com **tempo de ESPERA** | `query.py:240` (`wait_time_ms <= sla_target_ms`), `reports_query.py:3802-3803`, Fila/SLA `:5743` + `:5816` (*"compara a duração do segmento de fila com o sla_target"*) |
| **Default do formulário** | `30000` ms | `PoolsPage.tsx:603,755` — 30 s só faz sentido como alvo de **espera** |

O código contradiz o próprio rótulo **inclusive no default que ele escreve**.

**E isso já morde, não é latente.** Medido na tela: `aprovacao_deploy` tem
`sla_target_ms = 86 400 000` (**24 h**) — valor coerente com o rótulo (aprovação de deploy pode levar um
dia) e **sem sentido como alvo de espera**. Consequência: aquele pool **não pode violar SLA**, e sua
conformidade é 100% por construção — um verde que não pode ficar vermelho, que é a família de teste que
este repositório já catalogou. Ao lado, `retencao_humano` tem `300 000` (5 min), configurado como
espera. **O mesmo campo carrega duas intenções diferentes em pools diferentes**, e o operador não tem
como saber qual vale: ele leu o rótulo.

**⚠️ E não é um pool desviante — é METADE do parque (contagem dos 36 pools, 2026-08-28):**

| Faixa | Pools | Leitura |
|---|---|---|
| 15 s – 10 min | **18** | alvo de **espera** — plausível |
| **≥ 1 hora** | **18** | prazo de **processo** — impossível violar como espera |

Dentro dos ≥1 h: 5 em 1 h (`outbound_*`), 9 em 24 h, 3 em 48 h e **um em 7 DIAS**
(`limite_entrega`, 604 800 000 ms). O default do formulário (30 s) é usado por **2** pools — ou seja, o
parque foi configurado **majoritariamente à mão, seguindo o rótulo**.

**E a divisão não é aleatória: ela coincide com o tipo de pool.** O grupo ≥1 h é exatamente o de
processo/aprovação/workflow (`gate_promocao_ia`, `formfill_*`, `wrapup_detached_ia`, `deploy_promote_ia`,
`outbound_*`, `limite_processo`, `aprovacao_credito`, `portabilidade_processo_ia`, `limite_retorno`,
`retencao_humano-int`) — pools em que **não há cliente esperando em fila**. O campo já carrega as duas
semânticas, e **o discriminador que as separa é o mesmo da D13** (acesso de contato × interno). Isso
torna a correção tratável: não é preciso adivinhar a intenção pool a pool.

**Consequência para qualquer número agregado de SLA hoje:** ele mistura duas populações incomparáveis —
metade medida contra alvo de espera, metade contra prazo de processo que nunca vence. É o mesmo
argumento que motivou a D14, um nível acima: *somar esperas contra alvos diferentes não produz número
com uso*.

**Há pelo menos TRÊS SLAs distintos no domínio, e hoje um campo e meio:** (a) **espera** — alvo da fila,
grão de segmento (D14); (b) **atendimento total** — o que o rótulo promete, grão de sessão ou de
processo; (c) **tempo máximo de resposta por mensagem** — este já tem campo próprio (*"Max. reply
time"*, `Per customer message — optional`). Que (c) exista separado é a evidência de que a separação é
natural no domínio; (a) e (b) é que estão fundidos.

⚠️ **Ordem imposta:** decidir **o que `sla_target_ms` É** vem **antes** de migrar os leitores para o
segmento. Migrar primeiro só levaria um número errado para um grão melhor. As saídas são renomear o
rótulo para o que o código faz (barato, honesto, e reabre a pergunta "onde fica o SLA de atendimento
total?") ou **partir em dois campos** — e a segunda exige varrer os valores já configurados, porque
`aprovacao_deploy` prova que há pools preenchidos segundo a leitura errada.

⏳ **Não decidido aqui:** se o alvo é **copiado** para o segmento no fechamento da espera (denormalização,
simétrico ao que o routing já faz para a sessão) ou **resolvido na leitura** a partir do pool de destino
do segmento. O primeiro sobrevive a mudança de config (o alvo do dia); o segundo não duplica dado.
`sessions.sla_target_ms` permanece como **projeção** — nunca como fonte de cálculo de SLA — e a
migração dos três leitores é fatia própria, com contagem antes e depois (os números de conformidade
**vão** mudar).

---

## 9. O que este ADR NÃO decide

- ~~**A forma física da transição** (tabela × colunas × evento) — spec, após §8.2.~~ **Decidida na spec**
  ([`docs/product/workflow-arc-implementation-spec.md`](../product/workflow-arc-implementation-spec.md)
  §2.1): tabela `analytics.session_transitions`, `ReplacingMergeTree` chaveada pelo **`resume_token`** —
  que já É a identidade da lacuna (nasce no suspend, morre no `HDEL` do resume), então não se inventa
  identidade nova. Isso impõe uma ordem técnica: o escritor do resume só manda a linha inteira (invariante
  RMT) se o `suspend_reason` estiver num registro lido com `get` antes do `HDEL` — logo a porta externa
  (D8) vem antes por razão de mecanismo, não só de disponibilidade.
- **O destino de `GET /reports/workflows` e `/reports/workflow-summary`** — se somem ou são reapontados
  para o substrato de sessão. Depende de §8.1.
- **`installation_id` / `organization_id`** da `WorkflowInstance`: sem equivalente em sessão, suspeita de
  vestígio de multi-tenancy. Merece decisão explícita, não porte automático.
- **Se `sessions.journey_id` deve existir também em outras tabelas** (`segments`, `messages`). Fora de
  escopo: a D5 fala da coluna que existe.

---

## 10. Nota de método

Duas correções de leitura minhas ficaram registradas no caminho até aqui, e valem mais que o resultado:

1. **Chamei de "bug" o re-carimbo de `sessions.opened_at` a cada resume.** Não é: o mesmo comportamento
   vale para switch de canal e reconexão por identidade, logo o campo significa *"início do período de
   atividade corrente"* e o contrato é coerente. O tell que eu ignorei: havia **comentário explícito**
   explicando o comportamento. *Comportamento com comentário que se explica raramente é bug — é decisão, e
   o passo seguinte é ler a decisão, não classificá-la.* O que sobra é menor e real: existem **duas**
   respostas para "quando esta sessão abriu" e só uma tem contrato (a outra é `min(segments.started_at)`,
   usada como contorno em pelo menos um ponto do report layer).

   ⚠️ **E há um terceiro estado, aberto: o re-carimbo NÃO FOI OBSERVADO.** O comentário
   (`reports_query.py:643-646`, Fase E.2) afirma *"usa `first_started_at` porque o `opened_at` é
   re-carimbado a cada resume"*. Medindo três sessões webhook em 2026-08-10 — duas suspensas e uma
   fechada **depois de passar pelo caminho de resume** — o `opened_at` veio **antes** do primeiro
   segmento nas três (−13 ms, −49 ms, −11 ms), que é a ordem sadia (sessão abre → routing aloca →
   segmento começa). Nenhum re-carimbo.
   **Três hipóteses, nenhuma descartada:** (a) o re-carimbo vive num caminho de resume que essas sessões
   não tomaram — a que fechou foi encerrada pelo *timeout scanner* (`acw_expired`), não por retomada em
   novo segmento; (b) o escritor do close **reidrata a linha inteira** (invariante RMT) e restaura o
   `opened_at` original, tornando o re-carimbo visível só enquanto a sessão está aberta; (c) foi
   corrigido em algum ponto e o comentário ficou.
   **Enquanto não se decidir entre elas, `handle_time_ms` de webhook depende de uma premissa não
   verificada.** O teste decisivo é *antes/depois na MESMA sessão*: ler `opened_at` de uma sessão
   parqueada, retomá-la de verdade (submit no Console) e reler. Ver §8.6.

2. **Consequência concreta do re-carimbo, que sobrevive à correção:** relatório que **janela** por
   `opened_at` faz a sessão retomada **migrar de janela** — iniciada semana passada, retomada hoje, entra
   no recorte de hoje. Mesma família da armadilha já catalogada (*"janela por `started_at` cobrando dado
   gravado antes do deploy — o corte certo é `ingested_at`"*): o campo escolhido para janela decide o
   conjunto, e campo que se move move o conjunto.
