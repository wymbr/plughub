# ADR — Devolução de trabalho à fila, posse e afinidade de agente

**Status:** proposto (2026-08-04)
**Contexto de origem:** I5 / lacuna 2b — ver `TODO.md` § *"um F5 no Console devolve à fila um item em trabalho"*
**Substitui:** o enquadramento da lacuna 2b (*"janela de invisibilidade; falta um reaper de `claim_lease`"*), que a medição refutou.

---

## 1. O que a medição achou

Procurando o volume da lacuna 2b, apareceu o defeito **contrário** ao que ela descreve. Medido
ponta a ponta em `formfill_demo` (probes `probe_console_restore_after_reload.sh`,
`probe_reclaim_duplication.sh`, `probe_requeue_culprit.sh`):

**Um reload do Console (~2 s de WS fechado) é tratado como abandono do atendimento.** O bridge
publica `agent_done`, re-rota o contato ao pool (`main.py:6634`) e o item reivindicado volta ao
ZSET — enquanto o formulário reaparece na tela do primeiro agente, porque o mcp-server **replay-a
o `conversation.assigned`** na reconexão. Estado final conferido: item no ZSET **e** instância
ocupando vaga **e** lease ausente.

Três defeitos compostos:

1. **Duplicação** — dois donos possíveis para o mesmo trabalho.
2. **Perda de enriquecimento** — o re-route republica em `conversations.inbound` um evento de
   **seis campos**; todo o resto volta como default do Pydantic. Medido no JSON pós-re-route:
   `conference_id: null`, `work_item_deadline: ""`, `skill_id: ""`, `agent_type_id: null` — todos
   preenchidos no enfileiramento original. As chaves continuam presentes, e é por isso que o
   defeito atravessou a Frente 1 inteira: o JSON *parece* íntegro.
3. **Segmento sem `close_reason`** — `agent_disconnect` não está em `_TRANSPORT_TO_CLOSE_REASON`,
   e o mapa serve DOIS domínios (`main.py:5755` = contato, enum fechado; `:6401` = segmento,
   vocabulário livre). É a lacuna 6 com produtor concreto.

**A causa não é a lease.** A posse não expirava — era **devolvida**, por decisão de código, a cada
queda de socket.

---

## 2. Princípio

> **O item de trabalho tem UMA casa: a fila. O claim é um estado transitório.**

Toda a confusão anterior vem de a posse ser um fato disperso em três chaves (membro do ZSET,
lease, ocupante do semáforo) que podem divergir — e divergiram nos dois sentidos: a fix 2a de
2026-08-03 tratou "vaga presa sem item"; este ADR trata "item na fila com vaga presa".

---

## 3. Decisões

### D1 — Devolver à fila é sempre pelo caminho de pull

Item de fila pull que volta ao ZSET volta por `work_task_release`, **nunca** por re-publish em
`conversations.inbound`. O caminho genérico perde `assigned_to`, `conference_id`,
`work_item_deadline`, `auto_attend` e o resto do pacote.

Não é escolha de política — é correção. O caminho certo já existe e é o mesmo do botão
"Return to queue" do Console.

### D2 — Sempre devolver enquanto não houve submit; a saída é o prazo, não um contador

Perda de transporte do dono ⇒ o item volta à fila. Na reconexão o agente reivindica de novo
(automático ou manual). **Sem teto de rodadas:** o item preserva `first_queued_ms` e sai por
submit, por prazo, ou por decisão de supervisor (D7). O vaivém não compra tempo extra, então
o contador seria uma segunda regra dizendo o que o prazo já diz.

> ⚠️ ~~**`first_queued_ms` passa a ser load-bearing e hoje NÃO é escrito no caminho de re-enqueue**
> (medido ausente no JSON pós-re-route).~~
>
> **RETIRADO por medição (2026-08-04, Fase B).** A nota confundia duas coisas. `first_queued_ms`
> **não é campo do JSON do contato** — é a chave própria `{t}:queue:first_queued:{sid}`, escrita com
> **NX** por `add_queued_contact` e lida de lá pelo inbox (`work-queue.ts` → `PullInboxPanel`). O NX
> a faz sobreviver a todo re-enfileiramento, inclusive ao re-route ruim. Procurá-la *dentro* do JSON
> a encontraria ausente em qualquer época, inclusive antes do defeito.
>
> Medido em item real na fila de `formfill_demo` (`probe_first_queued_on_real_item.sh`):
> `first_queued=1785849262212` (2026-08-04T13:14:22Z), TTL 604798 s. 1 item na fila, 1 com a chave,
> 0 sem.
>
> *Método — duas medições não serviram antes desta, e por motivos diferentes:* (a) o espécime sujo
> de 08-04 respondeu `TTL -2`, mas ele já tinha sido **finalizado na tela**, e ausência num item
> morto não é evidência sobre caminho de escrita; (b) o pytest enfileira chamando
> `add_queued_contact` direto — exercita a FUNÇÃO lida, não a ROTA do produto, e ficaria verde
> mesmo se a rota divergisse. Só a leitura de um item vivo, enfileirado pelo caminho real,
> separava as hipóteses.
>
> **Consequência:** a Fase B não precisa carimbar nada. `first_queued` já é load-bearing e já está
> preenchido.

### D3 — Reserva com janela cobre os dois tipos; não existe "carência" como mecanismo próprio

O item volta à fila **reservado ao dono anterior**, com transbordo automático — isto é,
`assigned_to` + `fallback_to_pool_after_s`, o mecanismo da **Camada B**, já implementado e com
smoke próprio (`smoke_directed_pull.sh`).

Isto substitui o desenho intermediário de uma *carência* (segurar o drop N segundos esperando o
mesmo agente). A carência é um timer novo, específico de desconexão; a reserva é preferência
declarativa, serve outros casos (retorno de cliente, callback, segundo contato) e **já existe**.

Parâmetro por tipo, mecanismo único:

| Tipo | Janela de reserva | Razão |
|---|---|---|
| Item **author-bound** (`-int`: wrap-up) | **NENHUMA — reserva permanente** | só quem atendeu pode classificar o próprio atendimento; a identidade do executor é parte da DEFINIÇÃO da tarefa. Saída = prazo ou expire do supervisor (D7), que encerra **sem disposição** |
| Item **pooled** (aprovação, demais filas pull) | curta (30 s) | qualquer agente do time serve; a preferência pelo anterior não pode custar tempo a quem espera |
| Contato de cliente | curta | o cliente está esperando; a preferência não pode custar atendimento |

> **Emenda de 2026-08-04 (implementação).** A redação original dizia *"item de trabalho (`-int`,
> author-bound): janela generosa (minutos)"*, e a primeira implementação semeou 300 s. Está errado, e
> a migration `20260730000000_pool_internal_queue` já dizia por quê: *"transbordo existe porque item
> de fila é trabalho POOLED… wrap-up não é isso. A identidade do executor é parte da DEFINIÇÃO da
> tarefa, não uma preferência: aplicar transbordo a ele é **erro de categoria**"*.
>
> O eixo correto não é `-int` × cliente, é **author-bound × pooled**. Aprovação é `pooled` (outro
> aprovador pode decidir) e transborda; wrap-up é author-bound e **não transborda em tempo nenhum** —
> uma janela grande seria a mesma tarefa deixando de ser do autor, só que mais tarde. O motivo
> original da nota ("a digitação parcial só existe no navegador") continua verdadeiro, mas justifica
> reserva **permanente**, não janela larga.

### D4 — Afinidade é reserva/score DENTRO do pool, nunca endereçamento

"Preferir o mesmo agente" é fator de alocação no pool, não alvo de roteamento. O invariante é
literal no `CLAUDE.md`: *o POOL é a unidade endereçável*, e o "ramal" da Camada B é filtro de
claim sobre trabalho pooled — não um bypass.

Afinidade **sem janela não resolve o reload**: o reconnect medido levou ~6 s (`10:56:53` fechou,
`10:56:59` reentregou), e nesses segundos o agente não está disponível — qualquer outro livre
leva o contato. É por isso que D3 e D4 são a mesma decisão, não duas.

### D5 — A tela não é fonte de posse

O Console deve derivar *"eu detenho este item"* do **claim**, não de um `conversation.assigned`
republicado na reconexão. O replay de pub/sub parece recuperação e é parte do defeito: hoje ele
exibe formulário de item que o backend já devolveu à fila.

### D6 — O submit confere posse, contra um REGISTRO DURÁVEL

Segunda linha de defesa, e necessária mesmo com D5 (aba velha, duas janelas, race).

O check `caller == claimant` do A5 existe, mas (a) só no caminho de aprovação e (b) **falha aberto**
quando a lease não existe — que é exatamente o estado depois de uma devolução.

> Aplicação direta do achado da fix 2a: *posse não pode morar só numa chave cujo TTL é menor que o
> prazo do item.* A lease é carimbo curto; a posse precisa de registro.

#### Emenda de 2026-08-04 — o ledger `work_task` **não** serve de fonte de posse

A redação original mandava conferir contra `{t}:work_task:{session}`, "que sobrevive ao claim e
carrega `assigned_to`". Ao implementar, a leitura do código mostrou que isso **não fecha o buraco**:

- o ledger é escrito **uma vez, no despacho** (`_write_work_task`, channel-gateway) e carrega
  `pool_id`, `queue_session_id`, `resume_token`, `step_id`, `assigned_to`, `deadline`. **Nada escreve
  claimant nele** — `work_task_claim` não o toca;
- `assigned_to` é **reserva** (Camada B: quem *pode* puxar, com transbordo por
  `fallback_to_pool_after_s`), não posse. Em item **pooled** (aprovação, `formfill_demo`) ele nasce
  vazio ⇒ o check continuaria falhando aberto justamente onde a duplicação foi medida; em item
  reservado **após o transbordo** ele rejeitaria o claimante legítimo e aceitaria o dono original,
  que já não detém nada.

Ler posse de `assigned_to` é guardar um fato de escopo **(item, instante)** num campo de escopo
**(item, política de alocação)** — o erro que o `CLAUDE.md` nomeia em *"never store a narrower-scope
fact in a wider-scope field"*, aplicado ao contrário.

**Decisão (dono do produto, 2026-08-04): o registro de posse é do ÁRBITRO.**
`work_task_claim` passa a gravar `{t}:pool:{pool}:claim_record:{session}` =
`{instance_id, claimant_user_id, claimed_at}`, com **TTL derivado do `work_item_deadline` do item**
(fallback 25 h, a convenção do ledger — nunca `claim_lease_s`, que reabriria o fail-open). Apagado em
**todo** caminho que tira a posse: `work_task_release`, `work_task_expire` e o **re-parque** do
`route()` — este último é o caminho do F5 e a razão de o registro existir.

`POST /v1/work_queue/holder` passa a responder `{found, instance_id, claimant_user_id, claimed_at,
via, in_queue}`, consultando lease → registro. **`in_queue` é o campo que torna o veredicto
fechável**: o claim é um `ZREM`, então membro do ZSET é item sem dono — `found=false, in_queue=true`
é resposta **positiva** ("ninguém detém, está na fila"), não ausência de informação.

O bloco A5 do `handle_resume` vira **quatro ramos** (antes dois, e o segundo misturava dois fatos):

| Estado do árbitro | Veredicto |
|---|---|
| detido por mim (via lease ou registro) | passa |
| detido por outro | 403 — forja / segundo dono |
| ninguém detém **e** item na fila | **403 — "reivindique antes de submeter"** *(o que a Fase A fecha)* |
| ninguém detém e item fora da fila | passa, com log — push, encerrado, ou claim pré-Fase A |
| árbitro sem resposta (`None`) | passa, com log — falha de rede não recusa submissão legítima |

O ledger `work_task` **permanece** com seu papel atual (encerramento pelo prazo/supervisor, relatório
de pendências da D4). O que muda é que ele deixou de ser candidato a fonte de posse.

*Efeito colateral aproveitado:* `work_task_expire` ganha o registro como **segunda via** de dono
(antes da busca no semáforo), que é mais direta e cobre exatamente o cenário que o motiva — item
reivindicado, lease vencida, nunca submetido.

### D7 — Encerramento por supervisor permanece, e ganha importância

`POST /api/work_queue/expire/:sessionId` **não** é "preencher no lugar do agente" — a D5 da ADR
author-bound recusou isso explicitamente: encerra **sem disposição**, o supervisor não finge ser o
autor. É o mesmo caminho terminal do prazo, com outro gatilho.

Sob D2 ele fica **mais** necessário: item abandonado agora **circula** na fila até o prazo, e o
expire é a única forma de tirá-lo de cena antes disso.

**Corrida a resolver:** sob D2 o item pode estar reivindicado e sendo preenchido quando o
supervisor encerra. O resume tem de ser terminal-uma-vez — o segundo a chegar (submit ou expire)
recebe recusa explícita, nunca aplica em silêncio.

### D8 — Queda de transporte não publica `agent_done`

Hoje todo drop publica `agent_done` no `agent.lifecycle` e fecha segmento. Sob D2, uma conexão
instável produziria uma pilha de `agent_done` falsos e de segmentos por um único wrap-up —
contaminando contagem, AHT e a bancada de agentes.

Queda de transporte não é conclusão de trabalho. O fim do segmento por queda precisa de um
`close_reason` do **domínio de segmento** (vocabulário livre, onde já vivem `task_submitted`,
`acw_expired`, `acw_supervisor_closed`) — e **não** de mais uma linha no
`_TRANSPORT_TO_CLOSE_REASON` compartilhado, que escolheria um dos dois domínios em silêncio.
Separar os mapas fecha a lacuna 6 junto.

#### Emenda de 2026-08-04 — o dano está certo, o VEÍCULO estava errado

Medido antes de implementar (`infra/test/probe_fase_e_drop_footprint.sh`, tenant demo, 48 h):

| | medido |
|---|---|
| segmentos sem `close_reason`, bruto | 69 de 86 |
| … dos quais **IA** (`native`/`system`) | 55, **100 %** — nenhum call site `native` passa o campo |
| **a lacuna 6 de verdade** (humanos) | **14 de 31** |
| por família | pull de contato (`posse_test` 7/7, `formfill_demo` 5/5) = 12 · contato de cliente (`retencao_humano`) 2/9 · **fila interna `-int` 0/9** |
| pilha (mesmo par sessão×participante) | 1 par, 2 segmentos, ambos sem motivo e sem outcome |

Dois achados que mudaram a fase:

**1. A lacuna 6 é maior do que "o F5 esquece o campo".** A fila interna carimba 9/9
(`task_submitted`), e os pools de pull de contato carimbam 0/12. O carimbo de domínio de segmento
**já existe e funciona** — mas só pela porta do SUBMIT (`_wrapup_close_reason`, no
`session_resumed`). Toda saída de item reivindicado que não seja entrega sai muda. *(Ressalva: 7
dos 12 são de `posse_test`, pool do `smoke_claim_possession.sh` — origem sintética, não contam
como dano observado.)*

**2. Nada analítico lê o `agent_done`.** `analytics-api/models.py` mapeia `agent_done → None`
desde 2026-07-28, quando a tabela `agent_events` foi removida — e o comentário da remoção registra
que o ramo *"exigia `session_id`, mas os call sites do orchestrator-bridge chaveiam o contato como
`conversation_id` — então descartava 100 % do `agent_done` do bridge, em silêncio"*. O
rules-engine só consome `agent_login`. Sobra **um** consumidor que age: o routing-engine
(`remove_conversation`, liberação de vaga).

Logo a pilha de `agent_done` falsos **nunca chegou** a contagem, AHT ou bancada. O dano que esta
decisão nomeia é real e vem inteiro da pilha de **segmentos** (`conversations.participants` →
`analytics.segments`). Deixar de publicar o evento, isolado, não limparia relatório nenhum.

**3. E suprimi-lo teria sido uma regressão.** O primeiro desenho da Fase E suprimia o publish no
ramo de item de trabalho ("o `work_task_release` já libera a vaga"). Ler o `remove_conversation`
derrubou isso: além de liberar a vaga ele restaura a **membership dos SETs do pool** (SADD no
ready_set, SREM no busy_set) e ressincroniza o espelho `current_sessions` — nada disso está no
`work_task_release`. Como o `mark_busy` do claim tira o humano do ready_set ao bater a capacidade
(`max_concurrent=1` é o caso comum), suprimir o evento deixaria o agente **fora do ready_set após
cada F5**: invisível para o roteamento por push, e sem nada ficar vermelho.

**Decisão emendada.** A queda continua publicando — com **nome próprio**:

| ramo | evento | por quê |
|---|---|---|
| encerramento normal | `agent_done` | inalterado |
| queda (`agent_disconnect`), qualquer ramo | **`agent_released`** | mesmo efeito de capacidade no routing (`remove_conversation`), sem afirmar conclusão |

O routing trata os dois no mesmo `elif` — para a pergunta *"devolvo a vaga?"* eles dizem a mesma
verdade, e o handler é dono de uma coisa só. O que muda é o significado, e ele importa a montante
(quem lê `agent_done` como conclusão) e no log. Reusar um nome para dois fatos é a mesma falha que
esta decisão corrige no mapa de `close_reason`.

Única mudança de efeito: a queda publica `keep_slot_for_wrapup=false` incondicionalmente (e o
routing força `false` para `agent_released`, como segunda linha de defesa). O hand-off de vaga
existe para o wrap-up inline **do próprio atendente** herdar a vaga; numa queda não há herdeiro, e
segurá-la é segurá-la para ninguém até o TTL do hold.

---

## 4. Alternativas descartadas

| Alternativa | Por que caiu |
|---|---|
| **Reaper de `claim_lease` que re-enfileira** (o desenho que a lacuna 2b supunha) | Faria o que o defeito já faz sozinho. Além disso, sem sinal de liveness o reaper dispara sobre quem está trabalhando — nada renova a lease |
| **Esticar o TTL da lease até o prazo do item** (proposto e retirado nesta sessão) | Nasceu de supor que a posse **expirava**. Ela era **devolvida**. Não toca a causa |
| **Status `claimed` no próprio ZSET** (em vez de fila apartada) | O `ZREM` é a exclusão mútua atômica. Trocá-lo por um campo de status exige CAS e faz todo leitor do ZSET contar item reivindicado como enfileirado — `queue_length` alimenta o desempate do routing, o snapshot do pool e o relatório Fila/SLA. Três métricas infladas com valor plausível |
| **Fila apartada nova para reivindicados** | Desnecessária: o ZSET (ausência = reivindicado) mais o registro de posse do árbitro já respondem quem detém o quê |
| **Ledger `work_task` como fonte de posse** (a redação original da D6) | Retirada na emenda de 2026-08-04, ao implementar: o ledger não carrega claimant, e seu `assigned_to` é *reserva* — vazio em item pooled (fail-open intacto) e enganoso após o transbordo. Ver D6 § Emenda |
| **Carência (timer) como mecanismo próprio** | Absorvida pela D3. Timer novo, específico de desconexão, resolvendo um caso do que a reserva com janela resolve em geral |
| **Teto de rodadas de devolução** | O prazo já é o teto. Contador seria segunda regra dizendo o mesmo |
| **Acrescentar `agent_disconnect` ao `_TRANSPORT_TO_CLOSE_REASON`** | Parecia conserto de uma linha. O mapa serve dois domínios e no `agent_disconnect` o contato **não fecha** — estender o compartilhado escolheria um domínio em silêncio, que é o palpite que o docstring da função existe para impedir. *O teste `test_contact_map_never_absorbs_segment_transports` existe só para pegar quem tentar de novo: com a linha extra, todos os testes de MAPEAMENTO continuariam verdes* |
| **Suprimir o `agent_done` do drop no ramo de item de trabalho** (o 1º desenho da Fase E) | Retirada ao implementar. O `work_task_release` libera a vaga, mas **não** restaura a membership dos SETs do pool nem o espelho `current_sessions` — e o `mark_busy` do claim tira o humano do ready_set. O agente sairia invisível para o roteamento por push depois de cada F5. Ver § D8 Emenda, item 3 |
| **Não publicar nada no drop** (a leitura literal da D8) | Exigiria inventar uma liberação de vaga para o ramo de contato de cliente, sob pena de reabrir "vaga presa sem item" (fix 2a). Mais código, e — medido — zero ganho analítico: nada lê o `agent_done` |

---

## 5. Consequências

**Positivas.** Uma regra de saída (submit / prazo / supervisor). A posse deixa de ser derivável de
três chaves divergentes. `orphaned` desaparece por construção — o item está sempre na fila ou
reivindicado. A afinidade de agente vira feature reutilizável, não remendo de desconexão.

**Custos e riscos.**

- **Digitação parcial perdida no transbordo.** O formulário meio preenchido só existe no navegador.
  O ContextStore e o stream cobrem o *contexto* (quem pegar consegue retomar); não cobrem as
  respostas já digitadas. Mitigação = janela generosa (D3), não persistência de rascunho (que seria
  outro arco).
- **Churn visível.** Sob D2 o item aparece e some da inbox a cada queda. É honesto (ele está mesmo
  disponível), mas muda o que o operador vê.
- **Contato de cliente entra no arco.** Decisão do dono do produto (2026-08-04): entra, porque não
  há nada em produção. É o pedaço que mexe com atendimento ao vivo.

---

## 6. Não medido — não tratar como fato

1. **Se um segundo agente logado no mesmo pool consegue de fato puxar o item duplicado.** Exige dois
   logins. A duplicação está provada no ESTADO; a colisão efetiva, não.
2. **Se o wrap-up `-int` perde o `assigned_to` no re-route.** DEDUZIDO do evento de seis campos —
   em `formfill_demo` o campo nasceria vazio de qualquer forma, então o `null` observado não é
   evidência sobre item author-bound.
3. **Se um contato de CLIENTE real é re-roteado num F5 do agente.** Deduzido do código
   (`main.py:6634` é genérico, ramifica só por `reason` e `remaining`), não observado.
4. ~~Previsão falsificável em aberto~~ ✅ **CONFIRMADA em 2026-08-04, e sem esperar.** A previsão
   original mandava observar a expiração às ~14:56 UTC; o valor que a determina já estava legível o
   tempo todo. Medido com ~1h33m decorridos: `TTL {t}:queue_contact:{sid}` = **8909 s** (restam
   2h28m ⇒ original ≈ **4h01m**, o default) contra `TTL {t}:work_task:{sid}` = **84471 s** (restam
   23h27m de 25 h). O JSON morre ~20 h antes do ledger: nesse intervalo o membro do ZSET sobrevive
   sozinho e o item fica **listado na inbox e irreivindicável** (`not_in_queue`) — a assinatura do
   `work_item_deadline` perdido, e o defeito que `models.py:92-97` descreve como já consertado no
   caminho normal.

   *Método:* esperar a expiração para provar o TTL é a versão temporal de "esperar volume para
   decidir". Comparar os dois TTLs responde hoje. O probe passou a fazê-lo
   (`probe_reclaim_duplication.sh` § TTL).

---

## 7. Fases propostas

| # | Entrega | Depende de |
|---|---|---|
| A | **D6** — posse conferida no submit contra o registro durável do árbitro (ver § Emenda) | — (pré-requisito de segurança: sem ele, D2 troca dois donos possíveis por dois efetivos) |
| B | **D1** — devolução pelo `work_task_release` *(o `first_queued_ms` saiu do escopo: já é escrito, ver D2)* | A |
| C | **D2+D3** — sempre devolver, reservado ao dono, com janela por tipo ✅ *(2026-08-04; D2 já vinha satisfeita por B — na prática é a D3. Janela em config-api ns `routing`: `-int` 300 s, demais 30 s)* | B |
| D | **D5** — Console deriva posse do claim ✅ *(2026-08-04; guarda no mcp-server sobre `pool:pending_assignment`, veredicto puro `shouldDropOnPossession` — mesmos 4 ramos do submit e do drop)* | B |
| E | **D8** — mapas de `close_reason` separados (fecha a lacuna 6) + a queda publica `agent_released` no lugar de `agent_done` ⚠️ *(2026-08-04; ver § D8 Emenda — o "deixa de publicar" original virava regressão de membership de SET)* | C |
| F | **D7** — resume terminal-uma-vez (corrida submit × expire) | C |

A ordem não é negociável em A→B→C: cada uma torna a seguinte segura.
