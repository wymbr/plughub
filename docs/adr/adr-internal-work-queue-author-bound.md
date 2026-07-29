# ADR: Fila interna por pool — trabalho author-bound não é trabalho pooled

**Status:** Proposto (2026-07-29) — não implementado.
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

Simetria já existente: `null` é exatamente como `scoring.ts` representa item NA/skipped, com
re-normalização de peso. A ausência é cidadã de primeira classe no modelo, não um buraco.

**Escopo — o `outcome` vai no segmento do WRAP-UP, nunca no da ORIGEM.** Vazar para a origem faria o
relatório dizer que o *contato* terminou mal: o cliente foi atendido; quem não concluiu foi o
preenchimento. É o invariante do CLAUDE.md (fato de escopo estreito em campo largo), e aqui seria
especialmente traiçoeiro porque o valor é legítimo no domínio.

**Ganho:** "% de contatos sem disposição" vira métrica de qualidade operacional consultável, em vez
de ruído indistinguível.

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

## Fases

| Fase | Entrega | Depende |
|---|---|---|
| I1 | Flag no pool + espelho auto-provisionado + guardas (D1, D6) | — |
| I2 | Acesso derivado — inbox **e** `accessible_pools` do analytics (D2) | I1 |
| I3 | Skill resolve o alvo do delegate por `hook.origin_pool` (some o hardcode) | I1 |
| I4 | UX — ocultar nos seletores, rotular pela origem na inbox (D3) | I1 |
| I5 | Sem transbordo + supervisor + TTL `acw_expired` + relatório de pendências (D4, D5) | I2 |

I1–I3 já entregam o valor de métrica; I5 é o que evita recriar órfãos.

## Não-objetivos

- Reviver `acw_gate` ou qualquer gate de disponibilidade por instância (revertido, ver CHANGELOG).
- Estender a fila interna a trabalho **pooled** (aprovação segue no modelo atual).
- Transformar wrap-up em contato (item de tarefa não tem "Encerrar").
- Backfill dos segmentos órfãos existentes.
