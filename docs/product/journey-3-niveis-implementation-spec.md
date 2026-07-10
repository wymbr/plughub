# Journey sob o modelo de 3 níveis — Spec de Implementação

> **Estado:** spec, pré-código. 2026-07-08.
> **Design base (decisões travadas):** [`journey-retorno-modelo-3-niveis-design.md`](journey-retorno-modelo-3-niveis-design.md).
> **Modelo:** D1.5 — journey = componente conexa de sessões sob (proveniência ∪ alias), identificada pela **raiz
> canônica** (valorada em `session_id`, resolvida por union-find na leitura). **Sem** entidade transacional, sem
> lifecycle governado, sem topic `journey.events` de 9 tipos (isso é a entidade Arc 10, removida no Arc 19 Fase F).
> **Nomenclatura:** N3=negocial (perfil `workflow`), N2=acesso a canais, N1=I/O (perfis `agent`). Ver design §0.

---

## 1. Escopo

Reintroduzir a capacidade de agrupar N contatos (sessões N1/N2) em torno de um processo de negócio (N3) de longa
duração, e as três superfícies: **(a)** mostrar os 3 níveis, **(b)** medir/avaliar cada nível, **(c)** exibir os
contatos sob a journey. Tudo por **derivação + uma camada mínima de alias**, sem violar o invariante do Arc 19
("rastreabilidade via trace, sem entidade").

**Fora de escopo:** merge/split governado com máquina de estados; contexto de journey como store transacional;
qualquer coisa que reviva `workflow.journeys`/`journey_types`/`journey.events`.

---

## 2. Modelo de dados

### 2.1 `root_session_id` (raiz local, imutável)

Novo campo em **toda sessão**. Valor = `session_id` de outra sessão (a raiz da árvore de proveniência) ou `self`.

- **schemas** (`packages/schemas/src`): adicionar `root_session_id: z.string()` (não-nullable) ao schema de
  sessão / ao `ConversationInboundEvent` e ao `session_opened`. Regra de default documentada: recebido por
  propagação (§3.2) ou `= session_id`.
- **ClickHouse** `analytics.sessions` (`clickhouse.py`, hoje `ReplacingMergeTree ORDER BY (tenant_id, session_id)`,
  já tem `origin_session_id` e `journey_id Nullable`): migração aditiva
  `ALTER TABLE sessions ADD COLUMN IF NOT EXISTS root_session_id String DEFAULT session_id` — segue o padrão da
  migração `_DDL_SESSIONS_MIGRATE_ORIGIN` (Arc 19). **`ORDER BY` não muda** (mexer na chave exige rebuild).
- **Diferença de `origin_session_id`:** `origin_session_id` = 1 salto (quem me disparou, desenha as arestas do
  `SessionTrace`); `root_session_id` = raiz **transitiva** (agrupa). **Mantemos os dois** (design §9.3).

### 2.2 `journey_id` (cache eventualmente consistente do canônico)

Reaproveita a coluna **dormente** `sessions.journey_id Nullable(String)` (já existe em `clickhouse.py:600`).

- **Fonte de verdade NÃO é essa coluna** — é `root_session_id` + `journey_aliases` resolvidos por union-find.
- Preenchida no `session_opened` (`= root_session_id` no nascimento, pré-merge) e **refrescada no merge** (§3.3).
- v1 **não lê** dela; reads resolvem por union-find (§3.4). Adotá-la depois = trocar a query, sem backfill.

### 2.3 `journey_aliases` (as arestas de merge)

Nova tabela ClickHouse, fonte de verdade das uniões:

```sql
CREATE TABLE IF NOT EXISTS analytics.journey_aliases (
    tenant_id       String,
    source_root     String,          -- raiz da journey mais NOVA (absorvida)
    canonical_root  String,          -- raiz da journey mais ANTIGA (sobrevivente)
    merged_at       DateTime64(3),
    actor           String,          -- quem comandou (skill_id/participant)
    active          UInt8 DEFAULT 1, -- 0 = merge revertido
    _ingested_at    DateTime64(3) DEFAULT now64(3)
) ENGINE = ReplacingMergeTree(_ingested_at)
ORDER BY (tenant_id, source_root)
PARTITION BY toYYYYMM(merged_at);
```

Invariante estrutural que **elimina ciclos**: o merge sempre liga a raiz **mais nova → mais antiga**
(`started_at`, desempate por `session_id`). Como isso é uma ordem total, as arestas formam uma floresta apontando
para a raiz mais antiga — **impossível formar ciclo por construção**, sem necessidade de cycle-guard síncrono.

### 2.4 `PendingEntry.root_session_id` (elo com a journey pendente)

`packages/channel-gateway/.../identity/index.py` — o dataclass `PendingEntry` (hoje: `session_id`,
`customer_id`, `resume_token`, `pool`, `skill_id`, `policy`, `intent`, `context_preview`) ganha
**`root_session_id: str`**. É o que o resolvedor devolve no inbound (cenário 3) para o fluxo comandar o merge.

### 2.5 Redis / ContextStore

- `session.root_session_id` no ContextStore (`{tenant}:ctx:{sessionId}`), source `core`/`bridge`, confidence 1.0,
  visibility `agents_only` — análogo a `session.origin_session_id` (Arc 19).
- Reacender `@ctx.journey.*` → hash `{tenant}:ctx:journey:{root_canonical}` (engine já roteia; §3.5).

---

## 3. Runtime

### 3.1 Nascimento do `root_session_id`

No `session_opened`/criação (Core e `channel-gateway` adapters; `orchestrator-bridge` para sessões de agente
nativo): resolver `root_session_id` = valor propagado no `context` do evento inbound (§3.2), senão `= session_id`.
Escrever em ContextStore e no payload do evento de sessão → consumer da analytics grava a coluna.

### 3.2 Propagação top-down (automática, não manual)

`delegate`/`collect`/`task` **carregam automaticamente** o `root_session_id` do chamador ao filho — mesma
mecânica com que o Arc 19 já propaga `origin_session_id` (star topology em
`channel-gateway/.../adapters/webhook.py::handle_trigger`/`handle_delegate`). **O autor do fluxo não precisa
declarar** um campo de contexto; a plataforma injeta. Assim:

- Cenário 1 (top-down) e cenário 2-com-journey → filho nasce na mesma journey. **Sem merge.**
- `journey` = **do chamador** (design §9.4).

### 3.3 Merge (tool MCP + evento)

- Tool **`journey_merge(source_root, canonical_root)`** em `mcp-server-plughub` (grupo a definir; auditada pelo
  `McpInterceptor`). Só `role: primary`/`role: human`/fluxo N3 podem comandar (herda a política de quem pode
  emitir side-effects). Determina a sobrevivente = mais antiga (default; fluxo pode nomear).
- Emite evento canônico **`journey_merged`** no topic **`journey.merges`** (topic novo, **1 tipo só** — não é o
  `journey.events` de 9 tipos removido). Payload: `{tenant_id, source_root, canonical_root, merged_at, actor}`.
  Schema Zod `JourneyMergedEventSchema` em `@plughub/schemas`.
- **analytics-api** consome → insere em `journey_aliases` → **refresh do cache**: re-emite as linhas de `sessions`
  cujo canônico mudou com `journey_id` atualizado (raro e limitado; `ReplacingMergeTree` substitui).

### 3.4 Resolução canônica (leitura)

Na **analytics-api** (Python), por tenant: carregar `journey_aliases active=1` (tabela pequena) → montar
`child_root → canonical_root` → **union-find com path compression** → `find(root)`. Para uma journey J, o conjunto
de raízes locais que resolvem a J alimenta um `WHERE root_session_id IN (...)`. **Evita CTE recursiva** no
ClickHouse (fraco em recursão). Cache do resultado por request (TTL curto).

### 3.5 `@ctx.journey.*` (contexto compartilhado do processo)

Reacender o namespace no engine (`skill-flow-engine/src/engine.ts` + `context-accumulator-util.ts` já roteiam
`journey.*` para `{tenant}:ctx:journey:{journeyId}` quando `journeyId` presente). Alimentador = `root_session_id`
canônico da sessão. Política de visibilidade/escrita herda a taxonomia do ContextStore.

---

## 4. Componentes por pacote

| Pacote | Mudança |
|---|---|
| **schemas** | `root_session_id` no schema de sessão + `session_opened`/inbound; `JourneyMergedEventSchema` (topic `journey.merges`); `PendingEntry` (contrato) ganha `root_session_id`. Named exports explícitos (nunca `export *`). |
| **channel-gateway** | `identity/index.py`: `PendingEntry.root_session_id` (write + read em `write_pending`/`find_pending`); adapters webhook propagam `root_session_id` no `context` do inbound (junto do `origin_session_id`); inbound sem param → `root = self`. |
| **orchestrator-bridge** | Resolver/carimbar `root_session_id` no `session_opened` (ContextStore + evento); na camada de fechamento (`_close_contact_layer`) garantir `root_session_id` na linha de fechamento (junto do `customer_id`, Slice 4). |
| **skill-flow-engine** | Propagação automática de `root_session_id` em `executeDelegate`/`executeCollect`/`task`; reacender roteamento `@ctx.journey.*` com o canônico; o step que comanda merge chama a tool. Schema `skill.ts` **não** ganha campo novo obrigatório (propagação é de plataforma). |
| **mcp-server-plughub** | Tool `journey_merge` (via `McpInterceptor`, auditada); emite `journey.merges`. **Nunca** `redis.xadd` direto — `writeStreamEntry()`. Sem lógica de negócio na tool (só side-effect + audit). |
| **analytics-api** | Migração `sessions.root_session_id`; tabela `journey_aliases`; consumer `journey.merges` (+ refresh de cache); resolução union-find; endpoint `/reports/journeys`; `session_signal` grain=`journey` (já enumerado). |
| **platform-ui** | Vista **Processos** (aba em Monitor + Analytics) com drill 3 níveis; `SessionTrace` ancorado na raiz canônica + arestas de alias; filtros reusados (semântica "≥1 membro casa"); i18n `pt-BR`+`en`; ABAC gate `visualizar`/`report`. Strings via `t()`. |

---

## 5. Eventos Kafka

| Topic | Produtor | Consumidor | Novo? |
|---|---|---|---|
| `journey.merges` (`journey_merged`) | mcp-server-plughub | analytics-api | **Novo** (1 tipo; ≠ `journey.events` removido) |
| `conversations.session_opened` | Core | analytics-api | Existente — carrega `root_session_id` |
| `conversations.inbound` | channel-gateway | Core, Routing | Existente — carrega `root_session_id` no `context` |
| `session.signals` (grain=`journey`) | mcp-server-plughub (`survey_record`/N3 quality) | analytics-api | Existente — já enumera `journey` |
| `mcp.audit` | McpInterceptor | Analytics, LGPD | Existente — audita `journey_merge` |

Nenhuma escrita direta em Redis fora de routing-engine/skill-flow-engine; `insight.*` só via Kafka.

---

## 6. (a) Mostrar · (b) Medir/Avaliar · (c) Exibir — wiring

**(a)** Vista Processos: `GET /reports/journeys` agrega por raiz canônica; drill `journey → sessions → segments`
via `/reports/sessions?root_session_id=` e `/reports/segments`. Motor visual = `SessionTrace` (Arc 19) ancorado
no canônico. Duas vistas distintas por profundidade de drill (design §6) — Sessions = 2 níveis, Processos = 3.

**(b)** Por nível:
- **N1** — `evaluation_finalized` grain=segment (QA) + `mv_agent_performance_daily` + `/reports/segments`. Já existe.
- **N2** — outcome do episódio: `collect_events`/`segments`; formalizar "episode outcome" (gap menor).
- **N3** — `/reports/journeys`: conclusão do negócio (workflow `complete`/`resolved`), duração de negócio
  (`SUM(segment.duration_ms)`, exclui suspenso — como o TMA webhook do Arc 19), nº de contatos/canais, funnel por
  step. Qualidade do processo (CSAT/NPS end-to-end) via `session_signal` grain=`journey`, chaveado no canônico.
- Regra: métrica de nível inferior **não** vira métrica de superior por soma; a journey agrega (pior sentiment,
  nº de escalações) mas **avalia** no grão dela.

**(c)** `/reports/journeys/{root}/sessions` = `WHERE canonical(root_session_id)=root ORDER BY started_at`;
folhas clicáveis → transcrição (reusa H1/H2 de `customer-contact-history`). Fontes unidas: proveniência
(`root_session_id`) ∪ merge (`journey_aliases`). Default de UX "journey significativa"
(`session_count>1` OU workflow N3 OU fundida) só reduz volume.

### 6.1 — Gatilho do sinal N3: hook de pool `on_process_end` (design J4)

O `session_signal grain=journey` (§6(b) N3) precisa de um **produtor**: o que dispara a pesquisa de journey
end-to-end? Resposta = um **novo hook de pool de nível N3, `on_process_end`**, na config do **pool `webhook`**
(o processo). Isto **estende** a família de hooks existente (`on_human_start`/`on_human_end`/`on_contact_end`/
`post_human`, todos de nível **contato**) com uma família de nível **processo/N3**.

**Distinção inegociável — `on_process_end` NÃO é inline como os hooks de contato.** Os hooks de contato disparam
um `conversations.inbound` sintético **com `conference_id`** e rodam o agente *dentro da conferência, com o cliente
conectado* (o `on_human_end` segura o WebSocket até o NPS terminar). No fim do processo N3 o cliente **não está
presente**: a sessão de workflow é headless e rodou assíncrona (suspend/resume de horas/dias). Logo o
`on_process_end` **agenda um survey OUTBOUND** (a trilha de `customer-surveys.md` §19: `collect`/Arc 19, link web,
ou caixa no Console) — não injeta agente em conferência.

**Contrato do `on_process_end`:**
- **Gatilho:** a sessão de workflow N3 (pool `webhook`) atinge `complete` (evento concreto — journey NÃO tem
  lifecycle governado, então "fim de processo" = workflow-complete é a âncora correta).
- **Escopo/config:** declarado no `PoolHooks` do **pool webhook**; só faz sentido para `channel_type: webhook`.
- **Outcome-aware:** carrega o desfecho do processo (`resolved`/`failed`/`timeout`) — como o `on_contact_end` lê
  `contact_outcome` — para pesquisar só em ciclo fechado e escalar/recuperar nos demais. Pré-req de plataforma:
  carimbar `process_outcome` no ContextStore pré-hook (análogo ao `contact_outcome`).
- **Chaveamento:** o survey resultante grava `session_signal grain=journey` na **raiz canônica** (J3) e no
  `customer_id` da journey (resolvido via J1/identidade), **não** no `session_id` do workflow. Fecha o loop N3.

**Invariante de dedup em merge (J3) — NÃO é um agente, é regra do agendador:** se a journey A já foi pesquisada/
agendada e depois faz `journey_merge` na B, o cliente seria pesquisado **duas vezes sobre o mesmo processo**. O
agendamento de `on_process_end` deve ser **idempotente por raiz canônica** e passar pela **quarentena anti-fadiga**
do `customer-surveys` (`survey_eligibility_check`). Chavear pela canônica (não pelo workflow) já cobre o caso
comum; a quarentena cobre o resto.

**Invariante — pertença à journey (vale para TODOS os hooks, inclusive `on_process_end`):** o agente de um hook
roda como **especialista de conferência DENTRO da sessão existente** (o `fire_pool_hooks` publica um
`conversations.inbound` sintético **com `conference_id`**, e `parse_inbound`/`parse_routed` **pulam** eventos com
`conference_id` — não criam linha `sessions`). Como a pertença à journey é um fato de **sessão** (`root_session_id`
por sessão), o agente de hook **não forma journey nova** — é um segmento da sessão que já é membro da journey.
E o que o hook *cria* fica atado à mesma raiz: (a) filho via `delegate`/`collect` ou novo processo via
`workflow_trigger` → herda a raiz transitiva pela propagação do **J1** (resolvida do ctx do chamador); (b) o sinal
do survey → `origin_session_id = @ctx.session.root_session_id` (raiz canônica) + `grain=journey`. Único ponto de
atenção: o agente que cria algo deve **ler a raiz do ctx** (o caminho de plataforma delegate/trigger propaga
sozinho; só quebraria se um agente contornasse a plataforma).

**Fora do escopo desta adição** (registrado como possível futuro, não decidido): `on_process_start` (seed de
`@ctx.journey.*`, SLA do processo, confirmação de recebimento) e `on_process_suspend/resume` (nudges de CX em
espera longa — que o flow também resolve com `notify`/`collect`). Milestone/step-level **não** vira hook de pool.

→ Referência cruzada: `docs/guias/pool-hooks.md` (nova família N3), `docs/arcos/customer-surveys.md` §19
(outbound + quarentena).

---

## 7. Fases

| Fase | Entrega | Depende de |
|---|---|---|
| **J1 — Espinha de proveniência ✅ (2026-07-09)** | `root_session_id` (schemas + ClickHouse + nascimento + propagação automática). `journey_id` cache = `root` no open. Cobre cenários 1 e 2-com-journey. Ver CHANGELOG. | — |
| **J2 — Endpoint + Vista Processos (proveniência-only) ✅ (2026-07-09)** | `/reports/journeys` agregando por `root_session_id` (sem alias ainda); `root_session_id` no `/reports/sessions` (drill); Vista Processos (`AnaliseJourneysPage`, repurpose de `/analise/processos`) + drill 3 níveis + toggle "significativa". Só Analytics. Ver CHANGELOG. | J1 |
| **J3 — Merge/alias ✅ (2026-07-09)** | `journey_merge` tool + `journey.merges` + `journey_aliases` + resolução union-find (via `transform()`; cache `journey_id` **diferido** — reads por union-find, sem refresh). `PendingEntry.root_session_id`. Cenário 2-unify validado E2E; cenário 3 pipeline pronto (falta o skill disparar). Ver CHANGELOG. | J1, J2 |
| **J4 — Avaliação N3** | `session_signal` grain=`journey`; métricas de processo em `/reports/journeys`; **hook de pool `on_process_end`** (§6.1) como gatilho do survey de journey (outbound, outcome-aware, chaveado no canônico; dedup por raiz + quarentena); `@ctx.journey.*` reaceso. | J2, J3 |
| **J5 — Contexto compartilhado & polish** | `@ctx.journey.*` alimentado; i18n; ABAC; guard de invariantes. | J3, J4 |

J1+J2 já entregam journey por proveniência (o essencial do D1); J3 adiciona o que a proveniência não dá.

### As-built J1 (correções à spec — ler antes de J2/J3)

- **`sessions.journey_id` NÃO existia** (§2.2 assumia coluna dormente em `clickhouse.py:600`, que é de outra
  tabela). Foi **criada** por migração nova (`_DDL_SESSIONS_MIGRATE_JOURNEY`, `String DEFAULT session_id`).
- **`origin_session_id` é no-op latente**: a coluna existe e `parse_inbound` a popula no dict, mas
  `_SESSION_COLS`/`_session_row` **omitem** o campo → nunca chega à tabela (aparece `NULL`). Não corrigido no J1.
  Se J2 precisar de `origin_session_id` na tabela, wirar o campo no INSERT primeiro.
- **Persistência da raiz = enrichment central no consumer, NÃO repeat-por-evento.** `sessions` é
  `ReplacingMergeTree` (substitui a linha inteira) e writers como `conversations.routed`/`queued`,
  `session_suspended` e caminhos de abandono **não carregam** a raiz → clobram para self. A solução escolhida é
  `_enrich_session_root` no consumer (`consumer.py`): lê a raiz **autoritativa do ContextStore**
  (`{tenant}:ctx:{sid}` → `session.root_session_id`, semeada pelo channel-gateway) e sobrescreve em toda escrita
  de linha `sessions`. **Não** foi tocado o routing-engine, e o repeat nos parsers/bridge fica como fallback
  (ctx expirado). J3 (merge) deve refrescar o cache pela mesma via ou por reescrita das linhas afetadas.
- **Delegate do demo = especialista de conferência** (roda dentro do chamador, sem linha `sessions` filha). A
  propagação para sessão separada é exercida por `handle_trigger`+`origin_session_id` (cenário 2). `handle_delegate`
  (filha separada) está wirado mas não é o caminho primário do delegate atual.

---

## 8. Invariantes novos (never violate)

- `root_session_id` **imutável e nunca null**; merge **nunca** o reescreve — só escreve aresta.
- Fonte de verdade da journey = `root_session_id` + `journey_aliases`; `sessions.journey_id` é **cache**, nunca
  fonte. Reads não dependem do cache até ele ser explicitamente adotado.
- Merge sempre **novo → antigo** (ordem total por `started_at`,`session_id`) — garante ausência de ciclo.
- `journey.merges` é um topic de **1 tipo**; **proibido** reintroduzir a entidade Journey (tabela, lifecycle,
  merge/split governado, 9 tipos de evento).
- Propagação de `root_session_id` é **de plataforma** (injetada no delegate/collect/task), não campo de fluxo.
- Journey nunca é fonte para relatório de qualidade — o sinal N3 vive em `session_signal` grain=`journey`.

---

## 9. Verificação

- **Unit:** resolução union-find (encadeado, revert, tenant isolation); default de sobrevivente por `started_at`;
  parser `session_opened` com/sem `root_session_id`; `PendingEntry` round-trip com `root_session_id`.
- **e2e (harness):** os **três cenários** do design §3 — (1) top-down propaga sem merge; (2) unify de duas
  journeys populadas; (3) inbound null → resolve pendente → `journey_merge` → sessão de intake aparece sob a
  journey. Assert na Vista Processos (contagem de membros, ordenação por `started_at`) e no cache refresh.
- **Guard:** teste que falha se algum caminho escrever/ler `sessions.journey_id` como fonte de verdade, ou se um
  merge reescrever `root_session_id`.
- **Doc:** atualizar `CLAUDE.md` (§ nova "Journey (retorno) — modelo 3 níveis" com resumo ≤20 linhas + link),
  `docs/kafka-eventos.md` (topic `journey.merges`), `docs/modelos-de-dados.md` (`root_session_id`,
  `journey_aliases`), e mover fases concluídas ao `CHANGELOG.md`.

---

## 10. Riscos / decisões herdadas

- **Refresh de cache no merge** pode re-emitir muitas linhas se journeys grandes forem fundidas. Mitigação: merge
  é raro e comandado; medir; se preciso, adiar o refresh (cache lazy) já que reads não dependem dele em v1.
- **`/reports/journeys` sem materialização** carrega a resolução union-find por request. Mitigação: alias table é
  pequena; cache por request; promover ao `journey_id` materializado se medição exigir (design §9.2).
- **Herdadas do design §9 (todas resolvidas):** sobrevivente = mais antiga; cache eventualmente consistente;
  manter `origin_session_id` + `root_session_id`; propagação = do chamador; `@ctx.journey.*` reaceso; filtro =
  default de UX.
