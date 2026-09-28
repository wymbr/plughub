# ADR: A espera do `menu` ESTACIONA — o engine devolve a requisição e é acordado pela resposta

**Status:** Proposto — P1 e P2 decididas pelo dono em 2026-09-28 (§ Perguntas).
**Data:** 2026-09-28
**Ficha:** `DUR-01` (P0 da produção v1, `docs/product/producao-v1.md` § 5).
**Componentes:** `packages/skill-flow-engine` (`steps/menu.ts`, `engine.ts`, `state.ts`),
`packages/e2e-tests/services/skill-flow-service` (o `/execute` que produção roda — ver `SFS-01`),
`packages/orchestrator-bridge` (`activate_native_agent`, `process_inbound`, `process_routed`),
`packages/routing-engine` (`crash_detector.py`, `admission.py`), `packages/channel-gateway`
(`webrtc.py`, que lê `menu:waiting`), `packages/mcp-server-plughub` (escritores de `menu:result`).
**Relacionado:** [`adr-orchestrator-tree-navigation.md`](adr-orchestrator-tree-navigation.md) § D11 (onde
a espera foi medida), `docs/arcos/arc19-unified-session-model.md` (o `suspend` que já estaciona),
`CLAUDE.md` § Admissão de sessão.

---

## Contexto — o que a espera custa HOJE (medido em 2026-09-28)

O `menu` manda o prompt (`notification_send`) e espera a resposta **dentro do processo**:
`BLPOP [menu:result:{sid}[:{iid}], session:closed:{sid}, menu:signal:{sid}[:{iid}]]`
(`menu.ts:526`), num laço em memória que também conta re-perguntas (`menu.ts:516-635`). Enquanto o
cliente pensa, a conversa segura:

| recurso | por quanto tempo | evidência |
|---|---|---|
| requisição HTTP `POST /execute` aberta no bridge | a conversa inteira | `main.py` `activate_native_agent`, `total=None` (teto de 100 removido na `PRD-03`) |
| **uma conexão Redis dedicada** no executor | a conversa inteira | `skill-flow-service/src/index.ts:410` (`redis.duplicate()` por requisição) |
| execution lock `{t}:pipeline:{sid}:running` | renovado a `timeout+60` — até **14 460 s** num menu infinito | `menu.ts:436-443`, `state.ts:136-146` |
| timer de atividade no processo | a cada 15 s | `menu.ts:451-472` |
| vaga da instância de IA (`agent_busy` até o `agent_done`) | a conversa inteira | bridge publica `agent_ready`/`agent_done` só quando o `/execute` volta |
| a resposta em voo, se o processo cair | perdida: a chave tem o `instance_id` morto, o cliente é **perguntado de novo** | ADR da árvore § D11 |

A 3.000 sessões, são ~3.000 conexões Redis e ~3.000 requisições abertas **ociosas**: o trabalho real
de um turno dura milissegundos (menu roteirizado) a poucos segundos (LLM), e a espera dura dezenas
de segundos a minutos.

### ⚠️ Correção da premissa da ficha — a licença de IA NÃO é liberada por isto

A `DUR-01` dizia que o argumento principal era *"liberar a licença entre turnos"*. **Medido: a
licença é por SESSÃO, não por execução.** `{t}:admission:kind:ai` é um SET de `session_id`
(`admission.py:117-156`), debitado no roteamento e liberado no `contact_closed`, na fila muda, na
escalação para humano e pelo reconciliador. Nada nele olha se há um turno executando. Estacionar a
espera **não muda a licença** — só a mudaria uma redefinição do que a licença conta (Pergunta 1).

O mérito que sobra é o de capacidade de **infraestrutura** (conexões, requisições, processos) e
**robustez** (queda não perde a resposta nem pergunta de novo). Isso basta para a v1: é o que
separa 3.000 sessões de 3.000 sockets ociosos.

### O modelo já existe no engine — é o `suspend`

O `suspend` da Arc 19 já faz *"devolver agora, retomar depois no step X com um valor injetado"*:
grava sentinela, retorna `__suspended__` → `outcome: "suspended"` (`engine.ts:803-807`), e na retomada
o `resume_context.step_id` o faz seguir por `on_resume` com o payload (`suspend.ts:60-113`); o fluxo e
a config ficam fixados pelo pin (`engine.ts:514-576`). O que falta é o `menu` usar a mesma forma —
**sem** a parte de workflow (token público, TTL de dias, `persist-suspend`).

---

## Decisões

### D1 — `menu` fora de transação ESTACIONA em vez de bloquear

Mandado o prompt, o step grava o **registro de espera** e o engine devolve `outcome: "awaiting_input"`,
liberando o lock, a conexão Redis e a requisição. O registro carrega tudo o que hoje vive em memória
e decide a próxima volta: `step_id`, `menu_id`, `interaction`, opções, `attempt`, `fora_da_opcao`
(NIV-13), `deadline_ms`, `visibility`, `pipeline_session_id`. Vai para o `pipeline_state` (durável) e
continua espelhado no HASH `menu:waiting:{sid}` — que o gateway de voz lê como sinal *"há menu vivo"*
(`webrtc.py:164-176`, `webrtc_call.py:465-480`) e por isso **não muda de semântica**.

### D2 — Menu DENTRO de `begin_transaction` continua bloqueando

`@masked.*` e o alvo de rebobinagem da transação vivem só em memória (`engine.ts:663-666`), e o
invariante *"nunca gravar valor mascarado em Redis/pipeline_state"* proíbe persisti-los. Estacionar
no meio da transação perderia os dois. Hoje são 3 steps em 2 skills (`agente_auth_form_v1`,
`agente_auth_ia_v1`), esperas curtas de credencial. A regra é do ENGINE, não do autor: dentro de
transação o step bloqueia, e isso é logado, nunca escolhido em silêncio.

### D3 — A caixa de entrada é da CONVERSA, não da instância

> ⚠️ **Dispensada pela P2 (2026-09-28), antes de ser implementada.** O defeito que ela fechava — a
> resposta presa numa chave com o `instance_id` morto — nasce de a retomada vir por OUTRA instância.
> Com afinidade, a retomada usa o MESMO `instance_id`, e `menu:result:{sid}:{iid}` já é estável: a
> F1 manteve a chave de hoje e nenhum escritor precisou mudar. Vale reabrir só se a instância
> deixar de ser fixa na conversa.

Hoje a resposta vai para `menu:result:{sid}:{iid}` e se perde quando a instância morre. Estacionado,
o destino é `menu:inbox:{psid}` (`psid` = `pipeline_session_id`, que já isola agentes de conferência,
`main.py` `--seg--`). Todo escritor atual (bridge, mcp-server, routing-engine) continua fazendo
`LPUSH`; o que muda é a chave. A retomada lê a caixa **antes** de qualquer outra coisa.

### D4 — Estacionar é ATÔMICO contra a resposta que chega junto

O defeito a evitar: o engine confere a caixa vazia, a resposta chega, o acordador bate no lock ainda
preso (412) e desiste, o engine solta o lock — e a resposta fica na caixa sem ninguém para lê-la.
Regra: **soltar o lock e marcar `parked` é um Lua só, condicionado à caixa vazia**; se não estiver,
o engine não estaciona e processa a resposta na mesma execução. Do outro lado, o acordador faz
`LPUSH` **e depois** `/execute`; um 412 é seguro, porque quem segura o lock ainda vai olhar a caixa.

### D5 — Um só acordador: o bridge

O bridge é o único que invoca `/execute` (é assim hoje). Resposta do canal (`process_inbound`),
sinal de coleta e `@mention` já passam por ele: passam a `LPUSH` + retomar. Os escritores de fora
(mcp-server: `menu_submit` e resposta humana; routing-engine: `__agent_available__` do agente de
fila) **não** chamam o executor — publicam `conversations.inbound` com `type: menu_wake`, e o bridge
acorda. Nenhum componente novo fala com o executor.

### D6 — O prazo sai do processo e vira fato durável

O `timeout_s` hoje é o timeout do BLPOP. Estacionado, o prazo vai para um ZSET
`{t}:menu:deadlines` (score = `deadline_ms`, membro = `psid`); uma varredura do bridge acorda os
vencidos com o sinal `timeout`. Com N réplicas, o **`ZREM` é o claim** — quem remove acorda, quem não
remove não faz nada (a varredura é idempotente, ver `PRD-06`). Menu infinito (`0`/`-1`) não entra.

### D7 — `awaiting_input` não fecha nada

No bridge, `awaiting_input` entra em `_OUTCOMES_QUE_NAO_FECHAM` e **não publica `agent_done`**: o
segmento segue aberto, porque o atendimento não acabou — só a execução. O fechamento do contato com
menu estacionado acorda o engine com o sinal `closed`, para o `on_disconnect` rodar como hoje.

### D8 — O CrashDetector não re-enfileira conversa estacionada

Estacionada, a conversa fica sem lock e sem flag de atividade — exatamente o que o CrashDetector lê
como *"o processo morreu"* (`crash_detector.py:146-166`). Sem esta regra, a queda de uma instância
re-enfileiraria toda conversa estacionada nela. Ele passa a pular quem tem `parked` no
`pipeline_state` (ou no status), e contar à parte.

### D9 — Rollout por POOL, com o bloqueio como padrão

Campo de pool `menu_wait: block | park` (padrão `block`), editável na tela de deploy como todo campo
de config. Permite ligar pool a pool, medir com o harness (`PRD-02`) e voltar sem deploy. Some quando
`park` virar o único modo fora de transação (F4).

---

## Perguntas ao dono

> **Decididas em 2026-09-28.** A premissa das duas estava errada no ponto de partida: *"licença"*
> misturava três coisas — a capacidade que o tenant CONTRATA do PlugHub, a conta de LLM e a instância
> lógica de roteamento. O modelo decidido:
>
> - **Instalação dedicada, conta de LLM do CLIENTE.** O token é dele; o painel de consumo existe para
>   ele cruzar com a fatura do provedor. Nada consome token sem contato, e avaliação e canais só entram
>   por pedido do cliente.
> - **Cobrança = capacidade simultânea de SESSÕES contratada**, todos os canais (`PRD-08`).
> - **P1 → a capacidade de IA é por TURNO**, limitada pelas contas de LLM do cliente: se há, usa; se
>   não, espera até um prazo e falha por timeout nomeado (`AIG-03` — medido que essa espera **não
>   existe hoje** no `/v1/reason`). O portão de IA por sessão (`{t}:admission:kind:ai`) sai quando a
>   `PRD-08` entrar (`PRD-10`).
> - **P2 → (a), afinidade.** Com a IA limitada no turno, manter a instância lógica durante a espera
>   não custa capacidade; as instâncias são dimensionadas pelo teto de sessões.
> - Sentimento: carona na fala do cliente, mas chamada dedicada de LLM — passa a ser desligável (`AIG-04`).
>
> O texto abaixo é o da proposta, mantido para o registro do raciocínio.

**P1 — O que a licença de IA conta?** Hoje: sessão aberta com IA (inclusive esperando o cliente).
Alternativa: turno em execução. A segunda multiplicaria a capacidade vendida por licença (um turno
dura segundos, a conversa minutos), mas muda o produto: *"N atendimentos simultâneos de IA"* vira
*"N turnos simultâneos"*, e a recusa na porta deixa de existir (a admissão passa a ser fila de turno).
**Recomendação: manter por sessão na v1** — é o que o cliente entende e compra, e o dimensionamento
de `producao-v1-dimensionamento.md` já parte dele. Esta ADR não depende da resposta.

**P2 — A instância de IA segue ocupada enquanto a conversa está estacionada?**
- **(a) Sim — afinidade:** a instância fica `busy` até o `agent_done`; a retomada usa o MESMO
  `instance_id` (como o resume da Arc 19, `main.py:9510-9577`). Nada muda em ocupação, snapshots e
  relatórios. O ganho é só de infraestrutura.
- **(b) Não — instância vira "vaga de execução":** libera na espera e re-aloca a cada turno. Mais
  capacidade por instância, mas a retomada pode achar tudo ocupado **no meio da conversa**, e
  ocupação/`busy`/TMA por instância mudam de significado.
**Recomendação: (a).** A capacidade de IA já é governada pela licença (P1); a instância de IA é
construção lógica do bootstrap, não CPU. (b) só faria sentido junto com a alternativa de P1.

---

## Fases

> **F4 entregue em 2026-09-28 — a ficha fecha.** Estacionar deixou de ser escolha do pool: o bridge
> pede `park` em toda ativação que sabe acordar (`MENU_WAIT_PARK`), e o campo `pool.menu_wait` saiu
> inteiro (coluna, schema, rotas, tela e i18n — migração `20260928180000_pool_menu_wait_drop`).
> **A prova da tabela abaixo não vale como foi escrita, e o motivo é uma premissa errada da D5:** o
> bridge **não** é o único que chama o `/execute`. Há mais três chamadores, e nenhum sabe acordar uma
> conversa estacionada: o avaliador do routing-engine (`evaluation_consumer`), a delegação `assist`
> dentro do skill-flow-service e o fallback YAML do próprio bridge (registry sem resposta). Por isso
> o engine continua com o parâmetro `menu_wait`, agora com outro sentido: **pedir `park` é declarar
> que sabe acordar**. Quem não declara bloqueia, como antes. Medido: o avaliador não tem `menu`
> (`agente_avaliacao_v1`, 0 steps) e o workflow é proibido de ter por perfil; o que pode bloquear
> fora de transação é a delegação `assist` e o fallback YAML, os dois nomeados. Um site novo de
> ativação no bridge sem `menu_wait` reprova em `test_toda_ativacao_que_o_bridge_sabe_acordar_estaciona`.
> Medido ao vivo, sem pool nenhum configurado: 10/10 conversas no `demo_ia` com pico de BLPOP do
> executor **0**; agente de fila estacionado (`kind=queue`), limpo no fechamento.
>
> **F3 entregue em 2026-09-28**, com dois ajustes ao desenho:
> 1. **O aviso tem tópico próprio, `menu.wake`, e não viaja em `conversations.inbound`** como a D5
>    dizia: o `inbound` é consumido também pelo routing-engine, que trataria o aviso como contato
>    novo. `MenuWakeEventSchema` (`@plughub/schemas/menu-wake.ts`), chave = `session_id`. Produtores:
>    mcp-server (`menu_submit` do Console, resposta de agente de hook) e routing-engine
>    (`__agent_available__` e `queue_timeout` do agente de fila); consumidor: o bridge, que chama o
>    mesmo `wake_parked_run`. O aviso vem **depois** do `LPUSH`, então se ele se perder a resposta
>    continua na lista e o prazo acorda a conversa.
> 2. **Estacionam também o agente de FILA, a RETOMADA de sessão webhook e os ESPECIALISTAS**, cada
>    um com o seu fechamento (`_finish_queue_segment`, `_finish_resume_segment`,
>    `_finish_native_segment`), escolhido pelo `kind` gravado no registro. A retomada entrou porque
>    era ela, e não o especialista, que segurava os BLPOPs que a F2 viu no demo. O especialista é
>    acordado também quando o humano encerra a conferência.
> Medido ao vivo: agente de fila estacionado com BLPOP 0; o `menu.wake` o acordou, ele avisou o
> cliente e estacionou de novo; o fechamento acordou a fila estacionada e o segundo aviso de
> fechamento bateu no `wake_only` (409), sem recomeçar a saudação. `demo_ia` em `park`: pico de
> BLPOP do executor **0** ao longo de conversas inteiras, 10/10 duas vezes, p95 igual ao `block`.
> **O mesmo teste achou um defeito antigo e sem relação com o estacionamento**: o scan de instâncias
> do bridge (`{t}:instance:*`) lia a chave auxiliar `…:reap_cooldown` (valor `1`) como instância, e a
> subida do bridge entrava em crash-loop enquanto ela vivesse, derrubando as conversas em curso.
> Corrigido junto (`instance_bootstrap._scan_instances_from_redis`).
>
> **F2 entregue em 2026-09-28**, com três ajustes ao desenho acima, todos medidos:
> 1. **Só o agente PRINCIPAL estaciona.** Especialista de conferência tem o fechamento amarrado a
>    contadores de conferência (`hook_pending`, `posatt:*`, `active_ai_specialists`) e continua
>    bloqueando. No demo isso aparece: depois do primeiro menu, o `sac_ia` entra como especialista
>    e bloqueia. Estender a eles fica com a F3.
> 2. **O fechamento do segmento virou função** (`_finish_native_segment`): ele era o fim do
>    `process_routed` e só rodava ali porque a requisição durava a conversa inteira.
> 3. **A limpeza da subida do bridge também precisou pular a conversa estacionada**: ela supunha que
>    toda conversa em curso morria com o processo. Hoje um restart deixa de derrubar o estacionado.
> Mais a guarda `wake_only` no engine (409 `NOT_PARKED`), para um acordar atrasado não recomeçar o
> fluxo. Medido ao vivo com 10 conversas esperando num menu: `block` 10 conexões do executor em
> BLPOP, `park` 0; prazo vencido acordou as 4 conversas estacionadas no segundo certo.
>
> **F1 entregue em 2026-09-28** (engine + `menu_wait` no `/execute`), com o padrão `block`: nada em
> produção estaciona até a F2. Chaves como construídas: `{t}:pipeline:{psid}:parked` (o que a D8
> lê) e `{t}:menu:deadlines` (o que a D6 varre). Detalhe em `packages/skill-flow-engine/CLAUDE.md`
> § *Park mode*. A F0 (medir a linha de base) ficou para a rodada do `PRD-02` nas VMs.

| fase | entrega | prova |
|---|---|---|
| F0 | Medir no harness: menus em espera, conexões Redis do executor, duração de execução × espera por turno | linha de base registrada |
| F1 | Engine: estacionar (D1, D2, D4), caixa por conversa (D3), contadores no registro, retomada lendo a caixa | teste: queda entre prompt e resposta **não** repergunta; corrida resposta × estacionamento com os dois lados |
| F2 | Bridge: `awaiting_input` (D7), acordar em resposta e fechamento, varredura de prazo (D6), CrashDetector (D8), campo `menu_wait` (D9) | pool piloto em `park`; harness a N sessões com Redis do executor ~constante |
| F3 | `menu_wake` dos escritores de fora (D5): Console, agente de fila | agente de fila em `park` |
| F4 | `park` padrão; bloqueio só em transação; `menu_wait` removido | `grep` do BLPOP de menu restrito à transação |

## Fora de escopo

- `receive` (2 skills de teste/voz) — mesmo padrão, mesma solução depois da F2.
- A licença por turno (P1) e a instância como vaga (P2b), se decididas, são ADR próprio.
- A voz em tempo real: o bot leg continua lendo `menu:waiting` e mandando `menu_result` pelo mesmo
  caminho; o que muda é quem espera do outro lado.
