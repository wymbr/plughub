# Handoff — Journey: os contatos do processo, em sequência, num lugar só

> Cole isto no início da sessão. Objetivo: retomar sem redescobrir o que já foi medido em 2026-08-12.
> Item correspondente no `TODO.md`: *"Ler um processo = ver seus CONTATOS em sequência, num lugar só"*.

## O pedido, nas palavras do usuário

> *"Vejo journey e sessions como partes de um histórico. Hoje o `/analise/sessions` funciona
> relativamente bem para contatos com agentes: mostra todas as etapas de um contato, com todos os
> segmentos, debaixo de um único `session_id`. A ideia do journey é **estender este modelo** para
> vincular todos os contatos relacionados e poder visualizar **o que cada personagem interagiu em
> cada sessão/contato, em ordem cronológica**."*

Isto é o modelo já documentado (`CLAUDE.md`): `segment` (janela de UM participante) → `session` (UM
contato) → `journey` (processo sobre N contatos, **derivado** de proveniência ∪ alias). O pedido não
é entidade nova — é a mesma leitura, um nível acima.

---

## O que JÁ funciona — medido, não suponha o contrário

Cenário de referência: aumento de limite de crédito
(`docs/product/limite-credito-3-niveis-design.md`), raiz `48f7cce5-c996-4962-9443-c737a8102780`.

| Verificação | Resultado |
|---|---|
| `infra/test/probe_journey_limite.sh` | **5/0** — herança de raiz transitiva através de DOIS `workflow_trigger` |
| `GET :3500/reports/journeys?root_session_id=…` | `session_count: 3`, `pool_ids: [limite_entrega, limite_ia, aprovacao_credito]` |
| `GET :3500/reports/sessions?root_session_id=…` | as 3, encadeadas por `origin_session_id`, `spawn_reason: trigger` |
| Vista Processos (UI) | árvore correta: `ROOT WebChat → análise Webhook → entrega Webhook` |

**O agrupamento por proveniência está certo ponta a ponta.** Não há defeito de journey a consertar.
Chamadas que a UI faz (conferidas no network): `/reports/journeys?…&significant_only=false&page_size=1`,
`/reports/sessions?…&root_session_id=…&page_size=200` e `/reports/sessions?…&spawned_from_root=…&page_size=50`.

---

## O obstáculo real — e ele é de MODELO, não de UI

**Os contatos do acesso 2 ("consultar status") e do acesso 3 ("receber resultado") NÃO estão na
journey.** Cada reconexão do webchat é um contato novo: nasce com raiz própria e se liga ao processo
apenas por **identidade** (`resume_origin=identity`), nunca por proveniência.

Consequência dura: **nenhuma tela que leia `root_session_id` vai exibi-los**, por melhor que seja.
Construir a view antes de fechar isto entrega uma tela bonita que continua sem os contatos que
motivaram o pedido.

**O conserto barato existe e já está quase todo pronto:** a tool `journey_merge`, o topic
`journey.merges`, a tabela `journey_aliases`, o union-find na leitura e o `root_session_id` dentro do
`PendingEntry` — tudo existe. Falta um **`invoke journey_merge`** no ponto em que o intake reconhece
a pendência e retoma (`agente_portabilidade_intake_v1` e `skill_limite_entrada_v1`, no ramo pós-OTP
que resolve `pending_workflow_get`). O roteiro da demo já narra essa lacuna com honestidade.

**Primeiro passo sugerido:** fechar a pertença. Feito isso, a árvore que já existe passa a mostrar os
3 contatos sem UI nova — e só então vale discutir apresentação.

---

## A bifurcação de custo que define o escopo

**Personagem = participante ⇒ a unidade é o SEGMENTO, não a mensagem.**

O segmento já carrega `participant_id`, `role`, `agent_type`, `user_id`, `user_login`, `pool_id`,
`flow_id`, `started_at`, `ended_at`, `duration_ms`, `outcome`, `issue_status`, `escalation_reason`.
A linha do tempo da journey é a **união dos segmentos das sessões-membro**, ordenada por `started_at`
e agrupada por contato.

| Nível | Responde | Custo |
|---|---|---|
| **Segmento** | *quem atuou, em qual contato, quando, com que desfecho* | barato — **metadado, não cruza visibilidade** |
| **Mensagem** | *as palavras trocadas, atravessando contatos* | caro — cruza TRÊS regimes de visibilidade ⇒ **ADR de masking** |

Os três regimes que o transcript de mensagens teria de reconciliar: intake (`all`, cliente) · análise
(`agents_only` + PII mascarada por política + `[Dados sensíveis omitidos]`) · entrega (`all`).
Fatiando por segmento, o valor principal sai **fora** do caminho da decisão cara.

**Segundo eixo, do mesmo dado:** com `user_id` estável (humano) e `flow_id`/pool (IA), dá para virar
de *"o que aconteceu, em ordem"* para *"o que a Ana fez neste processo inteiro"*, atravessando
contatos. Avaliar qual o operador pede primeiro.

---

## Armadilhas medidas em 2026-08-12 — não redescobrir

- **Segmentos se SOBREPÕEM.** `@mention` roda paralelo ao primary (é rotina), especialista de
  conferência nasce dentro da janela do pai, hooks posatt são paralelos entre si. "Ordem cronológica"
  no nível de journey é **linha do tempo com sobreposição, não lista**. Herda o invariante: **nunca
  somar segmentos** para obter duração de processo — usar `elapsed_time_ms`, jamais `Σ agent_time_ms`.
- **`sessions.pool_id` é reescrito pelo `ReplacingMergeTree`.** A sessão da ANÁLISE sai com
  `pool_id = aprovacao_credito` (o delegate ao pool humano reescreve a linha inteira), não
  `limite_processo`. **Filtrar a Vista Processos por `limite_processo` não acha a journey.**
- **Estado transitório passa por defeito.** A Vista Processos mostrou `· 1 sessions` e
  `Duration 15m 35s` enquanto a sessão de entrega ainda não fechara; minutos depois, `3 sessions` e
  `27m 53s`. **Antes de reportar tela vazia/incompleta, recarregue e confira contra a API.**
- **Sessão de processo não tem conversa.** A sessão da análise é webhook entre workflow e aprovador —
  o cliente nunca fala nela. O drill mostra 3 caixas internas porque *é só isso que existe ali*.
  Parece tela quebrada e está correto. Um estado vazio honesto ("sessão de processo · N eventos
  internos · sem interação com cliente") resolveria a impressão; é barato e independente do resto.
- **`_apply_contact_scope` exclui pools `purpose: internal`** (`wrapup_detached_ia`,
  `retencao_humano-int`). Nenhum pool `limite_*` é interno — mas se uma journey "perder" sessões,
  cheque isto antes de culpar o agrupamento.
- **`significant_only` default é `true`** e esconde journey de 1 sessão sem webhook. O fetch
  direcionado (por `root_session_id`) ignora esse filtro **e** a janela de data, de propósito.

---

## Pergunta em aberto, já registrada

**Workflow trace é assimétrico na proveniência.** O trace da sessão de análise lista 8 execuções e
inclui `skill limite entrada` (que rodou em OUTRA sessão, a raiz), mas **não** inclui a sessão de
entrega, que é filha da mesma análise. Se segue proveniência, deveria seguir nos dois sentidos; se é
session-scoped, não deveria mostrar o intake. **Medir o escopo declarado da query antes de consertar**
— pode ser que o intake apareça por outro motivo e não haja assimetria nenhuma.

---

## Comandos de verificação

```bash
bash infra/test/probe_journey_limite.sh          # 5/0 — a journey de 3 sessões
bash infra/test/smoke_limite_tres_acessos.sh     # 16/0 — o cenário inteiro por API
```

```bash
# Ground truth dos dois níveis (sem auth ⇒ sem accessible_pools; com Bearer o escopo muda)
curl -s "http://localhost:3500/reports/journeys?tenant_id=tenant_demo&root_session_id=<RAIZ>"
curl -s "http://localhost:3500/reports/sessions?tenant_id=tenant_demo&root_session_id=<RAIZ>"
```

UI: `http://localhost:5174/analise/processos?journey=<RAIZ>` · ClickHouse: db **`plughub_demo`**
(`analytics` só existe nos testes) · analytics-api **:3500** · agent-registry **:3300** ·
auth-api **:3202** (3200 do host é o ai-gateway).

## Onde está o resto

- `docs/adr/adr-journey-session-segment-model.md` — os três níveis, e por que journey é derivada
- `docs/product/journey-3-niveis-implementation-spec.md` — J1–J5 as-built
- `docs/product/limite-credito-3-niveis-design.md` §11 — o cenário e os seis defeitos que o geraram
- `docs/product/demo-roteiro-30min.md` — Bloco B narra a lacuna do `journey_merge` com honestidade
- `packages/analytics-api/.../reports_query.py` — `_fetch_journeys`, `_apply_contact_scope`,
  `_journey_resolved_map` (union-find)
