# ADR: Fila interna por pool — trabalho author-bound não é trabalho pooled

**Status:** Aceito (2026-07-29) — **I1–I4 implementadas** (2026-07-30); **I5 núcleo A+B implementado**
(2026-07-30: encerramento único com dois gatilhos). Resta o **relatório de pendências** (fatia própria,
bloqueado pela lacuna 5 do `TODO.md`). Ver § "Correções ao desenho original".
**Componentes:** `packages/agent-registry` (flag + espelho auto-provisionado), `packages/routing-engine`
(claim sem transbordo), `packages/platform-ui` (acesso derivado, UX oculta, rótulo por origem),
`packages/analytics-api` (derivação no `accessible_pools`), `packages/orchestrator-bridge` +
`skill-flow-engine/skills/skill_wrapup_detached_v1.yaml` (alvo dinâmico do delegate).
**Relacionado:** `docs/adr/adr-wrapup-detached-pull.md` (Camada E2 / wrap-up-α),
`docs/product/finalization-hooks-detach-and-directed-pull-design.md` (Camadas A–F),
`TODO.md` § "Segmento humano do wrap-up NUNCA fecha", `CHANGELOG.md` (E2f, H1, H2 — 2026-07-29).

---

## Contexto — como chegamos aqui

A cadeia é relevante porque cada elo corrigiu uma premissa do anterior:

1. **E2f** pedia tirar a sessão de wrap-up da contagem de contato/TMA. O discriminador escolhido foi
   `pools.purpose: contact|internal` — a sessão de wrap-up é roteada a um pool distinto, e `sessions`
   e `segments` já carregam `pool_id`.
2. Ao fechar o resíduo ("excluir wrap-up do TMA por agente"), a query mostrou que **não havia o que
   excluir**: o segmento humano do wrap-up tinha duração nula porque **nunca fechava**.
3. A medição achou **87 segmentos humanos abertos** — 78 no `formfill_demo`, 9 no `aprovacao_deploy`,
   **zero** no caminho canônico. Não era defeito do wrap-up: a aprovação vazava 11 dias antes dele
   existir. O defeito era da **família pull** inteira (H1/H2, corrigidos).
4. Com o vazamento fechado, o resíduo **ressuscitou de verdade** — e revelou que o filtro por
   `segments.pool_id` **não serve**: o segmento humano carrega o pool onde ele foi *reivindicado*
   (`formfill_demo`, um pool `contact`), não o pool interno do workflow.

O conserto óbvio era dar ao claim um pool próprio. Ao desenhá-lo apareceu o custo: **associar cada
agente humano a mais um pool**. E ao questionar esse custo apareceu a observação que reenquadra tudo.

## O achado — duas naturezas de trabalho, uma só máquina

`fallback_to_pool_after_s` (transbordo) existe porque item de fila é trabalho **pooled**: qualquer
agente do time serve, e a reserva é uma *preferência de roteamento*.

Wrap-up não é isso. **Só quem atendeu pode classificar o próprio atendimento.** A identidade do
executor é parte da *definição* da tarefa, não uma preferência. É trabalho **author-bound**.

Aplicar transbordo a trabalho author-bound é um erro de categoria — e foi o que a Camada B fez ao
tratar wrap-up como mais um item de pull.

| | **Pooled** | **Author-bound** |
|---|---|---|
| Quem pode executar | qualquer agente do pool | **só o autor** |
| Reserva (`assigned_to`) | preferência | definição |
| Transbordo | correto e desejável | **incoerente** |
| Exemplos | aprovação, atendimento, back-office | wrap-up, correção da própria disposição |

**Aprovação é pooled** (outro aprovador pode decidir) e continua no modelo atual. O critério é o que
separa os dois — não "é humano" nem "é workflow".

---

## Decisões

### D1 — A fila interna é um POOL REAL, auto-provisionado, não uma fila virtual

Ligar a flag num pool cria/atualiza um **espelho físico** `<pool_id>-int`
(`purpose: internal`, `dispatch_mode: pull`, mesmos canais do pai). Não é namespace derivado em Redis.

**Por quê:** o invariante *"o POOL é a unidade endereçável"*. Fila virtual criaria uma segunda classe
de coisa endereçável, e routing, capacidade, `segments.pool_id`, `pools_client` e a
`mv_agent_performance_daily` passariam a precisar de duas gramáticas. Com pool real, **nada disso
muda** — o filtro da E2f já funciona, a MV (chaveada por `pool_id`) já funciona, e o resíduo do TMA
por agente **desaparece sem código**.

Custo aceito: o registry passa a ter ciclo de vida de espelho (criar/atualizar/remover na flag).

### D2 — Acesso é DERIVADO, em dois pontos (não associação)

`pullPools = acessíveis ∪ {p + "-int" | p ∈ acessíveis}`. Nenhuma associação nova por agente.

**Os dois pontos são inegociáveis:**
1. **Inbox do agente** (`PullInboxPanel` ← `accessible ∩ dispatch_mode=pull`) — senão o agente não vê
   o próprio wrap-up.
2. **`accessible_pools` do analytics** (`_apply_pool_scope`) — senão o supervisor que tem acesso a
   `retencao_humano` **não enxerga** o ACW de `retencao_humano-int`. Tornaríamos o tempo mensurável e
   o esconderíamos de quem precisa dele.

Esquecer (2) é a falha mais provável desta ADR, porque o sintoma é ausência, não erro.

### D3 — UX: oculta onde se ESCOLHE, visível onde se MEDE

| Superfície | Espelhos `-int` |
|---|---|
| Configuração → Recursos → Pools | **ocultos** |
| Seletores de pool (hooks, skills, agendas, campanhas) | **ocultos** |
| Analytics / Monitor / relatórios | **visíveis** — é onde o ACW existe |
| Inbox do agente | visível, rotulado pela **ORIGEM** ("Wrap-up — Retenção"), nunca pelo id cru |

O id `<pool>-int` é convenção de construção, não rótulo de produto.

### D7 — Configuração do hook viaja com o hook (`PoolHookEntry.context`)

A entrada de hook ganha `context: Record<string,string>`, que o bridge injeta prefixado `hook.*` no
ContextStore da sessão do hook. É o que permite ao skill do hook ser genérico de verdade: ele recebe
alvo e formulário em vez de conhecê-los.

```yaml
on_human_end:
  - pool: wrapup_detached_ia
    dispatch: detached
    context:
      dialog_form_id:    dialog_wrapup_retencao
      acw_timeout_hours: "24"      # I5 — prazo é fato do pool, como o formulário
```

Reservadas (escritas pelo bridge, não sobrescrevíveis pela config do tenant): `hook.type`,
`hook.origin_pool`, `hook.wrapup_pool`. Sem essa proteção, config de tenant poderia sequestrar o alvo
do delegate.

### D4 — Sem transbordo. A saída é supervisor + expiração

Transbordo automático some (D1/o achado). No lugar:

- **Supervisor pode encerrar** a pendência. Ele **não** finge ser o autor: encerra *sem disposição*.
- **Relatório de wrap-ups pendentes por agente** — a pendência precisa ser visível, o que é útil por
  si só (supervisor quer esse número).
- **TTL de expiração** fecha o que ninguém resolveu.

Sem isso recriaríamos, em outra forma, os 87 segmentos órfãos que acabamos de consertar.

### D5 — Expiração NÃO reusa `abandoned` — a ausência de disposição é o fato

`abandoned` **já está ocupado**: é o mapeamento da disposição *"cancelado"*, escolhida
deliberadamente pelo atendente (`WRAPUP_OUTCOME_MAP`, `mcp-server-plughub/src/tools/segment.ts`).
Reusá-lo para "nunca preenchido" tornaria **indistinguíveis** duas coisas opostas: o atendente que
classificou o contato como cancelado e o wrap-up que ninguém tocou.

Portanto, na expiração o segmento do wrap-up fecha com:

```
outcome      = null              (ausência é o dado — nada é inventado)
close_reason = "acw_expired"     (nomeia por que fechamos, que é o que sabemos)
```

**As-built (2026-07-30): são TRÊS valores, não dois.** O encerramento pelo supervisor não
é o mesmo fato que o prazo vencido — e como ambos têm `outcome = null`, o `close_reason` é
o único lugar onde podem se separar:

| `close_reason` | Causa |
|---|---|
| `task_submitted` | o humano entregou o formulário |
| `acw_expired` | o prazo do delegate venceu sem entrega |
| `acw_supervisor_closed` | um supervisor encerrou a pendência |

Colapsar os dois últimos custaria a única pergunta que o supervisor vai fazer — *quantos
eu tive de limpar?* — e série histórica não se reprocessa. A distinção vem do `source` do
resume, escrito server-side pelo gatilho, nunca asserido pelo cliente da API.

**Limite conhecido:** o item **nunca reivindicado** não tem segmento humano, logo não
existe linha onde carimbar `acw_expired`. O núcleo A+B garante a *limpeza* (item sai da
fila, vaga volta, workflow completa); a *visibilidade* dessa pendência depende do
relatório (fatia própria).

Simetria já existente: `null` é exatamente como `scoring.ts` representa item NA/skipped, com
re-normalização de peso. A ausência é cidadã de primeira classe no modelo, não um buraco.

**Escopo — o `outcome` vai no segmento do WRAP-UP, nunca no da ORIGEM.** Vazar para a origem faria o
relatório dizer que o *contato* terminou mal: o cliente foi atendido; quem não concluiu foi o
preenchimento. É o invariante do CLAUDE.md (fato de escopo estreito em campo largo), e aqui seria
especialmente traiçoeiro porque o valor é legítimo no domínio.

**Ganho:** "% de contatos sem disposição" vira métrica de qualidade operacional consultável, em vez
de ruído indistinguível.

### D7b — Fonte do relatório de pendências: o ledger é o índice, `segments` é o histórico

*(desenho fechado 2026-07-30, após a Camada F; implementação pendente)*

A D4 pede "relatório de wrap-ups pendentes por agente" sem dizer de onde. O `TODO.md`
registrava o bloqueio como lacuna 5 (a fila pull não é consultável pelo analytics) e o risco
de um relatório que cubra só uma das duas formas de pendência — e portanto **minta**.

**O ledger da I5 resolve isso por construção.** `{t}:work_task:{session}` nasce no despacho
do delegate e morre no resume: seu tempo de vida **é exatamente o intervalo da pendência**.
O claim **não** o apaga — logo ele cobre as DUAS formas com uma linha só (nunca reivindicada
e reivindicada-não-submetida), que é justamente o que o relatório precisava e não tinha. E
carrega `assigned_to`, que é a identidade do agente no trabalho author-bound.

| Pergunta | Fonte | Por quê |
|---|---|---|
| "Quem está com wrap-up pendente **agora**" | ledger, lido pelo BFF | Pergunta operacional, instantânea. Precedente estabelecido: `work_queue_list` já é Redis-direto no mcp-server. Classificar reivindicada × nunca-reivindicada = cruzar com o ZSET/lease do routing |
| "Quantos o agente X **deixou vencer** no período" (reivindicados) | `segments` | Já existe: `close_reason` ∈ {`task_submitted`, `acw_expired`, `acw_supervisor_closed`}, com duração e `user_id`/`user_login`. Nada a construir |
| Idem, **nunca reivindicados** | **lacuna real** | Não têm segmento (ninguém participou). O único vestígio hoje é o segmento do WORKFLOW com `outcome: failed`, que não carrega o agente |

**O que NÃO fazer, e por quê.** Criar um segmento sintético para o item nunca reivindicado
resolveria o relatório de graça — a tentação é legítima e vem da própria D1 (preferir a
entidade real a uma nova gramática). Mas `duration_ms` não tem valor honesto ali: `0` dilui a
média de ACW que a E2f acabou de fazer existir; a janela de pendência transforma espera em
tempo de trabalho (o engano `duration_ms × handle_time_ms` já catalogado); e `NULL` queima a
assinatura "segmento humano sem duração", que é o detector que achou os 87 órfãos. Segmento
responde *"o que este participante fez?"*; pendência responde *"o que aconteceu com este item
de trabalho?"* — e esta tem campos (dono, prazo, tempo parado, quem encerrou) que aquele não
comporta.

**Gate para a fatia histórica.** O caso nunca-reivindicado só justifica evento próprio
(`work_item.expired` → ClickHouse) se tiver volume. A superfície instantânea o mede sem
custo: se a operação mostrar que quase toda expiração é de item **reivindicado** — como nas
medições da Camada F —, o histórico já está em `segments` e a fatia 2 não precisa existir.
Medir antes de construir; o inverso cria uma tabela para responder uma pergunta rara.

#### As-built da fatia 1 *(2026-07-30)*

`GET /api/work_queue/pending` (BFF, `lib/work-queue.ts::listPendingWorkTasks`) + **Monitor ›
Pendências** (`/monitor/work-items`, ABAC `contacts.operacao`; o encerramento segue
`supervisor|admin` no endpoint). Três coisas divergiram do desenho e ficam registradas:

**1. O escopo `-int` é obrigatório, não cosmético.** O ledger é **genérico**: `_write_work_task`
roda incondicionalmente nos dois handlers de delegate — o docstring já assumia pool push —, logo
ele indexa também **aprovação** e **delegate a especialista IA**. Um relatório que somasse as três
populações mentiria na direção oposta à que esta seção queria evitar. O corte usa o sufixo `-int`
(garantia por construção pela D6). O critério é o mesmo **author-bound × pooled** da D1: aprovação
tem transbordo por `fallback_to_pool_after_s`, então ninguém fica preso nela.

**2. São QUATRO estados, e o quarto não estava previsto.** Além de `unclaimed`/`claimed`/
`not_queued`, existe **`orphaned`**: pool *pull*, item fora do ZSET e **sem lease** — a lease
venceu e nada o devolveu à fila, que é a **lacuna 2** (ausência de reaper de `claim_lease`).
Colapsá-lo em `not_queued` o esconderia atrás de um valor plausível. O relatório vira, de graça,
o instrumento que a Camada F não teve para medi-la. (`unknown` cobre pool sem `pool_config` no
cache — ausência de infra não vira presunção de "push".)

**3. A superfície é uma janela de ~25 h, não um acumulado.** O ledger vive
`timeout_hours*3600 + 3600`. No caminho normal morre antes, no resume; mas se o timeout scanner
não passar, a pendência **some sem rastro** — item nunca reivindicado não deixa segmento. Isso
não muda o gate da fatia 3, muda **como** medir: olhar e anotar, porque nada se acumula.

### D6 — Guardas

- O registry **rejeita** criação manual de pool com sufixo `-int` — senão o sufixo deixa de ser
  garantia e a derivação de acesso vira adivinhação.
- **Desligar a flag com pendências na fila é bloqueado** (erro explícito), não migrado em silêncio.
- O espelho herda canais e tenant do pai; **capacidade não é redefinida** — a vaga é do RECURSO
  (semáforo `claim_instance`), não da fila.

---

## Consequências

**Positivas.**
- O resíduo do TMA por agente **deixa de existir**: o segmento do wrap-up passa a carregar um
  `pool_id` com `purpose: internal`, e o filtro da E2f já o cobre — sem subquery, sem tocar na MV.
- **ACW por pool de origem** ("quanto Retenção gasta em pós-atendimento") vira pergunta respondível
  sem trabalho novo. Um pool de claim único teria misturado todos os times.
- Zero administração de acesso por agente.
- O `_fire_detached_hook` já injeta `hook.origin_pool` no contexto → o skill resolve o alvo do
  `delegate` dinamicamente, eliminando o `pool: formfill_demo` cravado no YAML sem trabalho no engine.
- O critério **author-bound × pooled** fica nomeado e reaproveitável — impede que aprovação (pooled)
  seja jogada na fila interna por semelhança superficial.

**Negativas / aceitas.**
- N espelhos na tabela de pools (mitigado por D3 — nunca exibidos onde se configura).
- Ciclo de vida de espelho no registry (criar/atualizar/bloquear-remoção).
- Duas derivações de acesso a manter em sincronia (D2) — a ficha mais fácil de derrubar.

**Não faz backfill.** Os 87 segmentos já órfãos seguem órfãos; ver `TODO.md`.

---

## Correções ao desenho original *(levantamento de código, 2026-07-30)*

Cinco pontos em que a ADP proposta divergia do código. Os três primeiros mudaram decisões.

**1. O sufixo `-int` era inválido.** `PoolRegistrationSchema.pool_id` era `^[a-z0-9_]+$` — hífen não
passa. **Decidido:** relaxar para `^[a-z0-9_]+(-int)?$`, com o hífen legal **só** nessa posição. Assim o
sufixo é reservado **por construção** (nenhum pool legado pode contê-lo) e `endsWith` vira garantia em vez
de convenção — que é o que a D6 precisa para derivar acesso sem adivinhar. `PoolHookEntrySchema.pool`
segue **estrito**: hook nunca aponta para espelho, e isso vira guarda de graça.

**2. "I1 sozinha faz o resíduo desaparecer" era falso.** `delegate.ts` passava `pool: step.pool`
**literal** — só `assigned_to`, `auto_attend` e `context` iam por `resolveInputMap`. Criar o espelho não
movia o item: ele continuava caindo em `formfill_demo` por hardcode. **I1 e I3 viraram uma fatia só**, e
I3 **não** sai "sem trabalho no engine" como a ADR afirmava: exigiu tornar `delegate.pool` resolvível,
com ref não resolvida como **falha dura** (nunca passa adiante como literal — rotear para um pool chamado
`@ctx.hook.wrapup_pool` acusaria "pool not found" e apontaria para o registry em vez de para a tag ausente).

**3. A D2 tem TRÊS pontos, não dois — e o terceiro falha por ausência.** Além da inbox e do
`accessible_pools` do analytics, a inbox é governada por `activePools` = **pools com WebSocket aberto**
(`AgentAssistPage.tsx`, `AgentAssistContext.tsx`), não por `accessiblePools`. Sem login no espelho:
a inbox nem consulta a fila; um claim forçado **passa** (o motor é pool-agnóstico — `work_task_claim`
nunca valida instância×pool) mas o `conversation.assigned` vai para `pool:events:{pool}-int`, que ninguém
assina → *claimado com sucesso, tela vazia*; `mark_busy` incrementa um pool cujo ready-set está vazio; e
`pool_config:{pool}-int` não existe → sem SLA na inbox e sem lookup de hooks no bridge.
**Decidido:** o toggle de pool abre o WS do pai **e** o do espelho — `registerHumanAgent` resolve os
quatro de uma vez, e continua derivado (nada administrado). A derivação para o analytics fica em
`pool_auth.py`, **não** no JWT: mantém o token intacto e dispensa re-login.

**4. Não existe DELETE de pool** na API do registry (só POST/PUT), e o `RegistrySyncer` não poda pools.
"Remover o espelho" é **desativar** (`status: inactive`) — desejável, porque o espelho aparece em
`segments` e apagá-lo tornaria ilegível o ACW já medido.

**5. O bloqueio de desligar-com-pendências (D6) não é verificável daqui.** A fila vive no Redis do
routing-engine e o invariante proíbe acesso direto pelo registry. **Decidido:** tratar a impossibilidade
de verificar como pendência — recusa por default, desligamento exige `?force_disable=true`. O que se
evita é a falha por ausência: desativar o espelho o tira de `availablePools`, a inbox para de listá-lo e
os itens somem da vista do agente sem erro nenhum.

**6. O "nó" da I5 não existia — e o fato ausente era outro** *(2026-07-30)*. O plano
registrava que o `ZREM` precisaria do id da sessão-FILHA do delegate, indisponível no
handler de resume. Levantamento: o `persistDelegate` do `skill-flow-service` chama
**sempre** `/v1/channels/webhook/delegate-conference` (*"delegate() is A2A: the target
agent ALWAYS runs as a conference specialist INSIDE the caller's session"*), logo
`child_session_id == parent session_id` e o id já está em mãos. O `handle_delegate`
roteado está inerte (só alcançável por POST direto; sequer aceita `assigned_to`/
`auto_attend`, que o wrap-up usa). O que **de fato** faltava era o **pool** do item:
`session:{id}:meta` carrega o pool do WORKFLOW enquanto ninguém reivindica — mente
exatamente no caso que importa — e `{t}:queue_contact:{sid}` morre por TTL antes do prazo.
**Decidido:** ledger `{t}:work_task:{session_id}` escrito no despacho (único ponto que
conhece o fato), chaveado pela sessão que o resume resolve, carregando `queue_session_id`
(o id que está DE FATO no ZSET) para que a topologia não volte ao caminho.

**7. TTL do JSON da fila < prazo do item** *(achado 2026-07-30)*. `add_queued_contact`
usava TTL fixo de 4 h contra `timeout_hours: 24`. Entre as duas marcas o membro do ZSET
sobrevive sozinho e o item **mente**: continua listado na inbox, **sem `assigned_to`** —
perde o author-binding que é a razão de ser desta ADR — e irreivindicável
(`not_in_queue`). **Decidido:** o delegate propaga `work_item_deadline` e o TTL o
acompanha.

**Peça acrescentada — `PoolHookEntry.context`.** O skill do hook é genérico; o que varia é a configuração
de quem o invoca. Qual DialogForm o wrap-up de Retenção usa não é fato do `skill_wrapup_detached_v1` — é
fato do `retencao_humano`. Sem esse campo o valor só teria dois lugares onde morar, e os dois erram o
escopo: cravado no YAML do skill (um form global) ou no `config_json` do slot de deploy do pool do hook
(idem, o deploy é um só). O bridge mescla o `context` da entrada como `hook.*`, com
`hook.type`/`hook.origin_pool`/`hook.wrapup_pool` reservadas e não-sobrescrevíveis.

**Alvo do delegate: UMA tag, resolvida no bridge.** A alternativa (duas tags + `choice` no skill) não
multiplicaria YAMLs, mas multiplicaria a **política de fallback**: cada consumidor de hook destacado
responderia à sua maneira "e se o pool de origem não tiver fila interna?". Com tag única, a pergunta é
respondida onde a configuração é conhecida — e quando a flag está desligada a tag simplesmente não é
escrita, o `warning` nomeia o motivo e o delegate falha apontando a ref.

---

## Fases

| Fase | Entrega | Depende | Estado |
|---|---|---|---|
| I1 | Flag no pool + espelho auto-provisionado + guardas (D1, D6) | — | ✅ 2026-07-30 (smoke 7/7) |
| I3 | `delegate.pool` resolvível + `hook.wrapup_pool`/`hook.*` no bridge + skill genérico | I1 | ✅ 2026-07-30 |
| I2 | Acesso derivado — `fetchPools`, **WS do espelho junto com o do pai**, `pool_auth` (D2) | I1 | ✅ 2026-07-30 |
| I4 | UX — oculto nos seletores + rótulo pela origem na inbox (D3) | I1 | ✅ 2026-07-30 |
| I5 (núcleo A+B) | Encerramento único com dois gatilhos: `work_task_expire` + ledger + supervisor + `close_reason` distintos (D4, D5) | I2 | ✅ 2026-07-30 |
| I5 (relatório) | Pendências por agente — visibilidade do item nunca reivindicado | lacuna 5 | pendente |
| F | Validação do arco de detach (G1, atribuição, pull direcionado, expiração) | I5 | ✅ 2026-07-30 |

I1+I3 movem o item para um pool `internal` (é o que faz o resíduo do TMA por agente desaparecer sem query
nova); I2 é o que o devolve ao agente. **I5 é o que evita recriar órfãos**: sem transbordo e sem TTL, um
wrap-up que ninguém preenche fica pendurado para sempre — a mesma forma dos 87 segmentos que acabamos de
consertar, em outra roupa.

**Três pontos de derivação, não dois** (a "ficha mais fácil de derrubar" das Consequências):
`fetchPools`/`handleTogglePool` (`AgentAssistContext.tsx`) e `_with_internal_mirrors`
(`analytics-api/pool_auth.py`, aplicado nos DOIS decodificadores — header e query param, senão o mesmo
relatório mostra pools diferentes conforme por onde o token chega). O sufixo é a única coisa que os
mantém em sincronia; é por isso que ele precisa ser garantido pela regex, não convencionado.

## Não-objetivos

- Reviver `acw_gate` ou qualquer gate de disponibilidade por instância (revertido, ver CHANGELOG).
- Estender a fila interna a trabalho **pooled** (aprovação segue no modelo atual).
- Transformar wrap-up em contato (item de tarefa não tem "Encerrar").
- Backfill dos segmentos órfãos existentes.
