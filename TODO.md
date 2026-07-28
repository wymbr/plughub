# TODO — PlugHub Itens Pendentes

> Itens genuinamente não implementados. Histórico de implementações concluídas em `CHANGELOG.md`.

---

## Wrap-up unificado — resíduos após a Phase 2 ✅ *(arco fechado 2026-07-27, ver CHANGELOG)*

**Polish (não bloqueia):** latência do auto-atendimento (~2-3s do poll da inbox) → instantâneo bombando o
`refreshSignal` do `PullInboxPanel` no `conversation.assigned`. **Agora é seguro**: antes da Phase 2 o claim
instantâneo AUMENTARIA a chance de chegar antes do release (`-1` → cai na inbox); com o hold, as duas ordens
são cobertas. E: UI para a config de `dispatch` inline/detached do hook (hoje só YAML — invariante "config
UI-editável" pendente para hooks de pool).

**Camada E2 restante:** `acw_pending` (produtor do marker p/ o `acw_gate: hard` da Camada C — **note** que o
marker e o gate foram REVERTIDOS na Phase 0; reabrir só sob requisito real) · **E2f** (sessão de wrap-up fora da
contagem de contato/TMA no analytics — ponto de atenção aberto) · **Camada F** (validação do arco: G1/AHT,
atribuição de segmento no relatório).

**Cleanup:** `infra/test/smoke_acw_gate.sh` ficou **órfão** — testa o gate `acw_pending` removido na Phase 0 e
falha hoje. `git rm`.

---

## Vaga só é liberada no `agent_done` — reap é rede, não conserto da origem *(2026-07-28)*

O reap de ocupantes órfãos está **implementado e validado** (ver CHANGELOG): ocupante cuja sessão tem
`session:{sid}:closed` sai do semáforo, nos dois sites onde a lotação pode ser mentira
(`get_ready_instances` e `claim_instance`), com cooldown de 60 s por instância.

**O que continua aberto é a origem.** `release_instance` só é chamado no `agent_done`. Todo caminho de
morte de sessão que não passa por ele segue vazando vaga até o próximo reap — o reap repara *depois*,
não impede. Assimetria que denuncia a premissa: o **hold** de wrap-up tem expiração passiva porque o
desenho previu "wrap-up que nunca chega"; o ocupante real não tem equivalente porque se presumiu que
todo claim termina em `agent_done`.

**Instrumento de decisão:** o `warning` de `reap:`. Ele existe para MEDIR, não só para consertar.

- Se aparecer **raro** (só após crash/restart do bridge) → a rede basta, não mexer.
- Se aparecer **em uso normal** → existe um produtor de `agent_done` faltando. Caçá-lo é melhor que
  seguir reparando: cada linha de `reap:` nomeia o `session_id`, e o `session:{sid}:closed` guarda o
  `reason` (7 d de TTL) — dá para agrupar por motivo de fechamento e achar qual caminho não publica.

Só depois dessa medição decidir se cabe fechar a origem (publicar `agent_done` também nos caminhos de
morte abrupta) ou aceitar a rede como suficiente.

---

## `role` nunca é escrito no hash de participante *(resíduo da F5 de identidade por-pool, 2026-07-28)*

Quatro sites LEEM `role` de `{tenant}:agent:instance:{participant_id}` — `session_context_get`,
`message_send`, `evaluation.ts` (×2) — e **nenhum produtor escreve o campo**. Todos caem no default.

Consequências vivas:

- A tool MCP `message_send` **não roteia @mention nenhuma**: o gate da F5 exige leitura positiva
  (falha fechada, de propósito). Correto por ora — o Console usa o WS, que conhece o agente pela
  conexão — mas é capacidade desligada por falta de produtor, não por decisão. Fechar quando/se
  existir agente humano via SDK.
- O mesmo default decide **mascaramento** (`session.ts`: `role === "customer" || role === "primary"`
  → mascara) e carimba `author_role` no stream. Como nunca é lido de fato, toda mensagem via
  `message_send` é mascarada e sai como `primary`. Blast radius maior que o do @mention; mesmo
  produtor ausente.

Correlatos do mesmo arco (fechado — ADR
[`adr-human-agent-pool-scoped-identity`](docs/adr/adr-human-agent-pool-scoped-identity.md)):
`crash_detector.py:144` ainda usa `meta.pools[0]` (mitigado por pular `human-*` em `:98`; o docstring
de `update_instance_meta` agora avisa que o meta é cache, não constante) · **testes de estabilidade
multi-pool** seguem inexistentes, embora a F5 os previsse.

---

## `agent_done` de crash-recovery é descartado pelo analytics *(achado 2026-07-27 na F4, não corrigido)*

Dois caminhos de recuperação no bridge publicam `agent_done` em `agent.lifecycle` com **`conversation_id`**:
`process_contact_event` (contact_closed com `ai_completing` expirado) e `_cleanup_stale_completing_at_startup`.
Mas `parse_agent_lifecycle` (analytics-api `models.py`) exige **`session_id`** para o `agent_done` e devolve
`None` sem ele — então **essas linhas nunca chegam em `agent_events`**. O consumidor do routing-engine funciona
(usa só `conversation_id`/`pools`), então a capacidade é liberada corretamente; o que falta é só o registro
analítico.

Descoberto ao remover o `agent_type_id` desses eventos na F4: fui checar quem consumia o campo e a resposta foi
"ninguém, porque o evento inteiro é descartado".

**Por que não foi corrigido junto:** não é do arco de identidade, e a correção não é óbvia. Renomear para
`session_id` faria aparecerem linhas novas em `agent_events` para contatos recuperados de crash — com
`outcome`/`handle_time_ms` ausentes e um `timestamp` que é o da recuperação, não o do fim real do atendimento.
Isso mexe em TMA e taxa de resolução. Antes de corrigir é preciso decidir **o que essas linhas devem
significar** (evento de recuperação distinto? `outcome` sintético? excluir do TMA?) — decisão de produto sobre
métrica, não conserto de campo.

**Nota transversal:** o descarte é silencioso (`return None` sem log), o que é o padrão que a § *Postura de
Engenharia* do CLAUDE.md nomeia. Independente da decisão acima, o parser deveria **logar** o motivo do skip —
foi só por acaso que isso apareceu.

---

## Posição na fila — resíduos após o fix do `queue.position_updated` ✅ *(2026-07-27, ver CHANGELOG)*

O evento voltou a ser publicado e `queue_position`/`estimated_wait_ms` são corretos. O que ficou:

- **Nenhum canal consome o evento.** O comentário do código promete "channel-gateway (to inform customer)", mas
  o channel-gateway só assina `collect.events` — **mostrar a posição ao cliente nunca foi implementado**. É
  feature, não regressão: exige consumidor no gateway + render por canal (webchat WS; voz = prompt falado).
- **Ruído do drain na tabela.** O drain periódico re-enfileira o mesmo contato a cada ~5 s e cada ciclo grava um
  par `queued`+`position_updated` (10 linhas para 1 contato em 45 s). Ou o publish passa a ser condicionado a
  MUDANÇA de posição, ou a série é agregada na leitura. Decidir antes que a tabela vire lixo em produção.
- **`available_agents` é enganoso**: conta instâncias no set `ready` (SCARD), não vagas livres — um agente
  lotado ainda aparece como "disponível". Renomear para `ready_instances` ou passar a contar capacidade real.
- **`queue_length` não é persistido**: o payload leva, a tabela `queue_events` não tem a coluna. Se o tamanho da
  fila no instante interessa ao relatório, é `ALTER TABLE … ADD COLUMN queue_length Nullable(Int32)` + a linha no
  `CREATE TABLE` do `clickhouse.py`.

---

## Journey (retorno) — modelo de 3 níveis *(design fechado 2026-07-08, pré-código)*

**Contexto:** o modelo de 3 níveis (N3 negocial `workflow` / N2 acesso a canais / N1 I/O — perfis `agent`) faz
voltar a necessidade de amarrar vários contatos a um processo de longa duração. A entidade `Journey` (Arc 10) foi
removida no Arc 19 Fase F (dualidade contact/workflow; "rastreabilidade via `parent_session_id`, sem entidade").
O retorno é **como lente + camada mínima de alias**, não como entidade.

**Decisão (D1.5):** journey = componente conexa de sessões sob (proveniência ∪ alias), identificada pela **raiz
canônica** valorada em `session_id`. Descartado D1 puro (não resolve cenário 2-unify nem 3-inbound — proveniência
é imutável) e D2 (entidade — reintroduz o que o Arc 19 removeu). Insight: sem merge, `journey_id=session_id` é só
`origin_session_id` replicado; o merge/alias é a única coisa que a derivação por proveniência não expressa.

**Invariantes:**
- `root_session_id` imutável, **nunca null** (param propagado no `delegate`/`collect`/`task` = do chamador; senão
  auto-mint = `self`). Propagação é de plataforma (injetada como o `origin_session_id`), não campo de fluxo.
- Fonte de verdade = `root_session_id` + `journey_aliases`; `sessions.journey_id` = **cache** eventualmente
  consistente (refresh no merge; reads não dependem dele em v1 — resolve por union-find).
- Merge sempre **novo→antigo** (ordem total por `started_at`,`session_id`) ⇒ floresta sem ciclo, sem cycle-guard.
- `journey.merges` = topic de **1 tipo**; proibido reviver entidade/lifecycle/merge-split/`journey.events` (9 tipos).
- Mantém `origin_session_id` (1 salto, desenha o `SessionTrace`) **E** `root_session_id` (raiz transitiva, agrupa).

**Fases:**

| Fase | Entrega | Depende de |
|---|---|---|
| J1 ✅ (2026-07-09, ver CHANGELOG) | `root_session_id` (schemas + CH + nascimento + propagação automática); `journey_id` cache=root no open. Cenários 1 e 2-com-journey. Persistência da raiz via **enrichment central no consumer** (lê ContextStore autoritativo — não repete root em cada evento nem toca routing-engine). Validado E2E (`infra/test/smoke_journey_root.sh`, transitividade W3 origin=W2/root=W1). | — |
| J2 ✅ (2026-07-09, ver CHANGELOG) | `/reports/journeys` (proveniência-only) + filtro `root_session_id` no `/reports/sessions` (drill) + Vista Processos (`AnaliseJourneysPage`, repurpose de `/analise/processos`) + drill 3 níveis + toggle "significativa". Só Analytics (Monitor fica p/ depois). | J1 |
| J3 ✅ (2026-07-09, ver CHANGELOG) | `journey_merge` tool + `journey.merges` + `journey_aliases` + union-find (resolução na leitura via `transform()`; cache `journey_id` **diferido**, não refresh — reads por union-find) + `PendingEntry.root_session_id`. Cenário 2-unify validado E2E; cenário 3 = pipeline pronto, falta o skill disparar a tool. | J1, J2 |
| J4a ✅ (2026-07-10, ver CHANGELOG) | Leitura N3: `session_signal` grain=`journey` + métricas de processo (`business_outcome`, `business_duration_ms`, `signal_count`, `nps_avg`/`csat_avg`/`ces_avg`) no `/reports/journeys` + colunas Outcome/NPS na Vista Processos. | J2 |
| J4b ✅ (2026-07-10, ver CHANGELOG) | Hook **genérico** `on_process_end` (dispara em desfecho terminal, carimba `session.process_outcome`; mecanismo igual aos outros hooks, survey é 1 consumidor). Agente `skill_journey_survey_v1` cria survey OUTBOUND (`survey_link_create`, form `dialog_nps_buttons`) grain=journey keyed na raiz. Validado E2E via trigger slug→pool (`/channel/webhook/{slug}`). | J4a |
| **J4c ✅** (2026-07-13, validado E2E — spec `docs/product/journey-j4c-survey-collect-spec.md`, ADR `adr-outbound-survey-as-collect-contact.md`) | **Survey outbound = contato via `collect` (Arc 19 suspend/resume), não sinal solto.** Modelo 3 camadas: **N3** (workflow de survey, **channel-agnostic**, faz `collect`+suspende) → **N2** (handler `persistCollect` = resolvedor de canal **único e cego ao processo**: alcançabilidade via Resolvedor de Identidade + `channel_policy` declarativo de N3 + consentimento/política como slots plugáveis) → **N1** (sessão-filho **roteada** a um pool de survey, herda `root`→membro da journey). **Opção A + criação LAZY (decidida 2026-07-10):** separa o assíncrono (esperar o cliente) do síncrono (o survey). **(1)** `collect` = convite: N2 **entrega o link + guarda pending, suspende — zero sessão/recurso/metering** até o clique (sem clique→timeout→nada alocado). **(2)** clique com token válido = **inbound PADRÃO** (cliente presente), roteado ao pool de survey → Routing admite (cota + `max_concurrent_sessions`) + Core metera — **limites só no engajamento real**; `dialog_runner` (agente único, DialogForm por config) renderiza **ao vivo** (síncrono → `menu` funciona, e o princípio "agente único interpreta o form" sobrevive). **(3)** fim do survey → `session_closed` + sinal grain=journey no close + `collect.responded`→resume N3 (collect resolve **no fim**). Resolve a regra de perfil (`menu`≠`suspend` no mesmo skill) e o custo de capacidade do assíncrono. "delega"≠step `delegate()` (é inbound, sessão própria). **Segmentação/billing por pool** (sem canal-classe novo, sem carve-out — capacity-based; `max_concurrent_sessions` = botão de volume). Trabalho central: **wirar `persistCollect`** (hoje só `persistDelegate`; `collect` cai em wall-clock). `survey_link_create` = legado/anônimo. **Invariantes:** N3 nunca nomeia canal (só `channel_policy`); N2 nunca ramifica por `skill_id`/`campaign_id` (guard de CI estilo `check_config_invariants.py`); escolha de canal = concern reutilizável. Fatias J4c-1..5. Demo = web+mock; SMS/e-mail/consent/policy = slots futuros por config. | J4b |
| J5a ✅ (2026-07-14, ver CHANGELOG) | `@ctx.journey.*` **vivo** (bridge resolve a raiz canônica → `journey_id` no `/execute` → `journeyId` no engine; TTL próprio de 30d) + **merge acíclico por construção** (aresta raiz→raiz via mapa de aliases no Redis; idade vem do stream canônico, não do `meta` que só o webchat escreve) + 12 testes do `journey_merge`. Validado E2E com escritor e leitor em sessões diferentes da mesma journey, com controle negativo. **J5a-2 ✅ (2026-07-22, ver CHANGELOG):** fechada a **escrita IMPERATIVA** — `context_set` (skill-flow) e `/api/inject-context` (supervisor) gravavam raw no hash da sessão; agora roteiam pelo helper único `writeContextTag` (`journey.*` → hash do processo/raiz canônica, TTL 30d; reusa `resolveJourneyRoot`, sem dep de `@plughub/sdk`). Smoke `smoke_journey_context.sh`. | J3, J4 |
| J5b ✅ (2026-07-14) | i18n dos **enums** na Vista Processos. `status`/`outcome`/`business_outcome`/`channels` chegavam crus da analytics-api e eram renderizados assim (o operador via inglês técnico em pt-BR); a moldura já passava por `t()`, faltavam os **valores**. Reusa `sessions.status.*` (já existia no namespace) e adiciona `enums.outcome.*` + `enums.channel.*` (en+pt-BR) — não duplica dicionário. `defaultValue: <valor cru>` em todos: enum novo no backend degrada para o valor cru em vez de quebrar a tela. `t` passa por **parâmetro** nos helpers (a regra proíbe `useTranslation` fora de componente). `title` guarda o valor cru para debug. | J5a |
| — (app-wide, fora do Journey) | **Guard de rota ABAC**: nenhuma página de `analise/` tem gate próprio — só o Sidebar. Deep-link contorna a UI (o dado segue filtrado por `accessible_pools` no backend). Consertar só a de Journeys seria cosmético; é um item do app. | — |

### Journey — 3 itens pendentes: natureza + mini-plano (levantamento 2026-07-23)

Cruzados contra o código. **São três naturezas distintas** — só o Item 1 é entrega de valor acionável.

**Item 1 — sinal N3 no drill da Vista Processos ✅ ENTREGUE (Fatias 1+2, 2026-07-23 — ver CHANGELOG).**
Painel **PROCESS SIGNAL** no cabeçalho do L2 (desfecho+provisório, duração, NPS/CSAT/CES, `signal_count`);
`csat_avg`/`ces_avg` agora renderizados. Fatia 1 = UI-only (`selectedJourney` no `AnaliseJourneysPage` →
prop). Fatia 2 = filtro `root_session_id` no `/reports/journeys` (resolve canônico, ignora janela+significant)
+ rebusca no `JourneySessions` para deep-link. Validado (clique + deep-link). *Limitação:* fetch direcionado
varre `sessions` por lista de roots-membros — medir se houver journeys enormes sob merge.

**Item 2 — cache `sessions.journey_id` diferido** *(otimização adiada por decisão, não é bug)*. A coluna
existe (escrita = raiz no nascimento) mas **não é refrescada no merge**; reads resolvem por union-find sobre
`journey_aliases` (`_journey_resolved_map`). "Ativar" = refrescar `journey_id` no consumer de merge para
`GROUP BY journey_id` direto. Custo atual baixo (tabela de aliases minúscula, 1 hop pré-resolvido), correção
intacta (cache nunca é lido como verdade). **Só sob pressão de latência/volume medida.**

**Item 3 — guard de rota ABAC** *(dívida app-wide, defesa-em-profundidade/UX, NÃO vazamento)*. Rotas
`analise/*` (`routes.tsx`) sem wrapper — só o `Sidebar` esconde o nav; deep-link renderiza o chrome. O dado
**segue filtrado** por `accessible_pools` no backend (`_apply_pool_scope`), então não vaza. Modelo de correção
já existe no repo: `RequireEvalAccess` (guard por-rota das telas de Avaliação, hoje hard-coded a
`module='evaluation'`) — generalizar (prop `module`) ou criar `RequireAbac` irmão e envolver `analise/*`.
**App-wide** (analise/monitor/config são todos nav-only) — melhor numa passada dedicada, não enxertado no
Journey.

### Journey — Árvore de proveniência (T1–T6) ✅ COMPLETA (2026-07-14/15)

Toda a árvore de proveniência entregue e validada — movida para `CHANGELOG.md` (entradas **"Journey T1–T5"**
e **"Journey T6"**): T1 persistir `origin_session_id` · T2 desfecho = raiz (+ provisório) · T3 `journey:
inherit|new` · T4 `spawn_reason` · T5 UI em árvore + prefixo `PRC-` · T6 rastro forense bidirecional
(`GET /reports/sessions/{id}/trace` + `TraceDrawer`). Bug colateral fechado no caminho: `/reports/sessions`
nunca rodava a query principal (alias-shadowing → fallback mudo pelo tier 3). Design/decisões e não-objetivos
na spec `docs/product/journey-provenance-tree-spec.md` (§9). ⚠️ T2 mudou números já exibidos (desfecho passou
a ser o da raiz) — correção, quebra comparação com prints anteriores.

---

## Deploy de skills — cleanup de campos órfãos *(follow-up do redesenho D1–D4, 2026-07-13)*

Depois do modelo novo de deploy ("uma definição editável + cópia imutável no slot"), ficaram órfãos:
dropar `flow_draft` e `deploy_status` do schema Prisma (agent-registry) e remover o endpoint
`POST /v1/skills/:id/deploy`. Deixados para depois de o modelo novo rodar; histórico completo do
redesenho no `CHANGELOG.md`.

---

## Analytics — revisar workarounds pré-`row_version` *(resíduo do fix de 2026-07-13)*

Com `sessions` já em `ReplacingMergeTree(row_version)`, revisar (e provavelmente remover) os workarounds
de `COALESCE` / `channel=""` no analytics-api que existiam **só** para mitigar a corrida entre tópicos.
Histórico do bug e do fix no `CHANGELOG.md`.

---

## Tópicos Kafka órfãos — achados do saneamento do doc *(2026-07-27, doc ✅ saneado)*

O saneamento de `docs/kafka-eventos.md` (✅ feito, ver CHANGELOG) reconciliou a doc contra o código e expôs
**quatro defeitos reais** — nenhum é de documentação:

> **Propósito declarado (2026-07-27, decisão do dono do produto):** estes eventos são **negociais, de
> MEDIÇÃO** — contam ocorrências nos fluxos de agentes gerados nos skills, para análise e comparação
> posterior. Não são mecanismo (a ação já acontece por outra via) e **não devem ser removidos**: estão
> incompletos, não mortos. Isso muda a pergunta de "remover ou ligar consumidor" para **"onde essa medição
> deve aterrissar"**.
>
> **Substrato que já existe (avaliar ANTES de criar consumidor/tabela novos):** o **Arc 12** faz exatamente
> isso — `agent.events` → ClickHouse `analytics.agent_business_events`, com `category` hierárquico
> (`pool_id.skill_id.metric_key`, decomposto em `category_l1..l4`), endpoints
> `/reports/agent-events/{series,summary,categories}` e integração com a lente de deploy do Arc 6 Fase 2
> (`metrics[]=agent_event:{category}` — "esta versão do skill mudou a taxa de ocorrência?"). Se a medição de
> regras entrar por aí, ganha série temporal, drill e comparação por versão **sem infra nova**.

1. **`rules.escalation.events`** — telemetria de escalação disparada (modo `active`), sem consumidor. (NÃO é a
   via da escalação — correção de um diagnóstico meu errado: `escalator.py:79` chama
   `POST /tools/conversation_escalate` e só depois publica o evento, `:91`.) Falta o destino de medição.
2. **`rules.shadow.events`** — o shadow mode existe para MEDIR o que uma regra faria antes de ativá-la; hoje o
   único registro é um `logger.info`. É o caso em que a medição É a feature.

**Opções para os dois** (mesma decisão): (a) o rules-engine passa a emitir `agent_event` com categoria
(`{pool}.{skill}.rule_escalation` / `.rule_shadow`) e os tópicos `rules.*` são aposentados — reuso máximo;
(b) consumidor dedicado no analytics com tabela própria (mais fiel ao schema atual, mais infra); (c) manter
publicando e aterrissar depois. **Correção pendente no CLAUDE.md** em qualquer caso: a tabela de tópicos lista
`rules.escalation.events` → consumidor `Routing Engine`, o que nunca foi verdade.
3. **`agent.done`** — ✅ **REMOVIDO (2026-07-27, ver CHANGELOG).** Publicação órfã + dupla no mcp-server; teste
   reescrito para cobrir as vias reais. Resíduo: `issue_status` não trafega mais em nenhum tópico (só era
   publicado no órfão; segue validado na entrada). Se o analytics precisar dele, adicionar ao `contact_closed`.
4. **`usage.cycle_reset`** — ✅ **REMOVIDO (2026-07-27, ver CHANGELOG).** Consumo morto no usage-aggregator; o
   reset segue pelo `POST /admin/cycle-reset` (mesma classe). O schema fica em `usage.ts` — se o caminho por
   evento for desejado, falta o PRODUTOR.

Também corrigido na doc (era erro de documentação, não de código): `conversations.events` — o tópico mais
movimentado da plataforma — estava listado como "nome obsoleto que não existe mais"; e cinco tópicos
documentados **não existem** (`conversations.session_opened`, `conversations.message_sent`,
`conversations.abandoned`, `rules.session_tagged`, `gateway.heartbeat` — os três primeiros confundiam evento
com tópico).

**Dívida de contrato:** `conversations.events` não tem schema Zod único, sendo o tópico central e o de maior
fan-in (5 produtores × 6 consumidores). Contraria o princípio "todo evento cross-package tem contrato
validado" registrado no próprio doc.

**Correção pendente no CLAUDE.md**: a tabela de Kafka topics lista `rules.escalation.events` → consumidor
`Routing Engine` e `agent.done` → `Rules Engine, Analytics`. Ambas falsas — atualizar junto com a decisão (1).

**Método:** cross-check contra `packages/analytics-api/src/plughub_analytics_api/clickhouse.py` (DDLs reais) e
`CLAUDE.md § Kafka Topics` (que já está correto e serve de gabarito). Baixo risco, alta clareza — chore de doc.

---

## Resolvedor de Identidade — próximos passos (Fase A ✅ Slices 1–4; falta Slice 3 + Fase B) *(2026-07-02)*

**Estado:** Fase A completa e validada (ver `CHANGELOG.md` § Slices 1/2/4 e `docs/product/identity-resolver-fase-a-plano.md`). Cadastro mínimo interno sem CRM: índice Redis + durabilidade PG (`schema identity`) + retomada cross-canal + `sessions.customer_id` = nativo no fechamento (conserta `contact_id`-como-`customer_id`, reconecta H1/H2/H3).

**Próximo (recomendado — desbloqueia o valor no demo):**
- **Wiring do intake para escrever `caller.customer_id` NATIVO ✅ (2026-07-03, CHANGELOG).** `agente_portabilidade_intake_v1` chama `customer_resolve` (âncoras `numero_atual`+`contact_identifier`, kind detectado por choice `contains "@"`) e grava `caller.customer_id` via `context_set` **pré-ramificação** (não `context_tags.outputs` — `context_set` é o caminho já provado no runtime nativo do bridge e é a tag exata que `_resolve_close_customer_id` lê). Validado no demo: 2 intakes, mesmo número → mesmo `cus_…` em `sessions.customer_id`. Deploy exigiu `set-next`+`promote` (pool migrado a `PoolSkillSlot`; YAML+restart republica `skill.flow` mas não re-snapshota o `current`).
- **Slice 3** — campos `customer_resumable`/`resume_policy` no step `delegate` (schema `skills.ts` + propagação no engine até o callback `persistDelegate` — **verificar** se o engine repassa campos novos) + `session_resumed` com `resume_origin: same_channel|token|identity`. Ver plano §2 Slice 3 + spec §6/§11.
- **Fase B** — identidade progressiva (anexar âncora nova a cliente existente em match parcial — hoje retorna o existente sem indexar as novas), `external_refs` (CRM id → `external_refs`, não como chave), merge de clientes. Spec §5/§12.
- **Consolidar `caller.customer_id = nativo` no step CRM `resolve`** (`agente_contexto_ia_v1.yaml`): hoje o `buscar_crm` grava `caller.customer_id` com o id do CRM; no modelo novo o nativo é a chave e o CRM vai p/ `external_refs`. Spec §13.8-5 / §3 nota de migração.

**Candidato Fase B/C — gate de validação p/ steps sensíveis + OTP de posse de canal (proposta 2026-07-02, REVISADA 2026-07-03):** liberar sequências **sensíveis** só com validação da identidade/posse que entrou em contato. Duas classes de verificação, decisão consciente:

- **Posse de canal (NOVO — plataforma PODE ser autoridade):** OTP interno (plataforma gera+envia+valida) prova que quem está na conversa **controla o handle agora** → eleva a âncora `phone`/`email` de fraca→verificada. Isto **NÃO** é autoridade de identidade-de-registro; é autoridade de posse de canal (a plataforma é dona dos canais). Gate para ações **não-sensíveis / baixo-médio risco** (retomar carrinho, ver histórico, confirmar dado cadastral) e é o que torna `resume_policy: auto` seguro (vs foot-gun).
- **Identidade-de-registro / credencial / KYC / pagamento (INALTERADO — só retaguarda):** continua **sempre** delegada ao tenant via `identity_verify` MCP; a plataforma relaya e guarda só o veredito. Princípio 7 preservado *neste eixo*.

**Correção de posição:** a proposta original (2026-07-02) proibia OTP próprio da plataforma ("só se emitido pela retaguarda"). Revisão: permitir OTP de **posse de canal** exige **emenda explícita ao princípio 7 e §4.4** — hoje a spec reserva TODA elevação de `confidence`/`verified` ao backend (§ linha 105: "confidence reflete o veredito do backend, não um palpite nosso"). Emenda = separar as duas classes acima; **fazer a emenda antes do código**.

**Não-negociável de modelagem — classe na DADO, não só na prosa:** `confidence` escalar único colapsa semânticas de confiança não-intercambiáveis (0.95-OTP ≠ 0.95-CRM). Adicionar `verification_method`/`verification_class ∈ {channel_otp, backend_identity, none}` ao lado de `verified_at` na `customer_secondary_keys` (colunas já existem: `confidence`, `verified_at`). Consumidores gateiam pela classe certa: `auto`-resume → `channel_otp` recente; ação sensível → `backend_identity`. Veredito escopado a `(customer_id, kind, value_hash)`, nunca ao handle global.

**Precisões:** (a) OTP mata **spoof**, não a **ambiguidade de handle compartilhado** (`matched_by="ambiguous"` ainda precisa de discriminador — pessoa escolhe conta / backend desambigua); não é primitiva de merge. (b) "Nunca guardar o código" tem asterisco: o **desafio** gerado vive efêmero server-side `{t}:otp:{challenge_id}` (hasheado, TTL, uso único, bound a session+customer_id) p/ comparar; a resposta digitada do cliente é `@masked.*` (comparada e descartada); só o veredito persiste. O desafio **não** usa o namespace `@masked.*`. (c) Primitiva = **tools MCP** `otp_challenge`/`otp_verify` via `invoke` (não novo step-type). Composição: `invoke otp_challenge` → `menu masked:true` (coleta código) → `invoke otp_verify(@masked.code)` → `choice` no veredito. (d) **Degradação graciosa** obrigatória (código errado/expirado/max-tentativas → modo baixa-confiança ou escala; nunca hard-block). (e) Entrega pelos adapters de canal existentes; créditos/provedor (SMS/WA template) = integração/custo do tenant; anti-enumeração (só OTP p/ handle que o cliente forneceu no contato que ele iniciou — nunca "esse número tem conta aqui?") + consentimento no envio proativo.

**Fronteira (clarificação 2026-07-03):** OTP é **fator componível / step-up**, nunca o autenticador final. A plataforma provê a primitiva + o veredito-com-classe; **o nível de segurança é definido pelo fluxo do tenant** (regra de negócio, não modelada aqui). Não-sensível: fluxo pode aceitar `channel_otp` só. Sensível/regulado: fluxo **encadeia** OTP (posse) → `identity_verify` retaguarda (identidade-de-registro/KYC) — a plataforma nunca vira autenticador final. `resume_policy: auto` em `channel_otp` é default opt-in do fluxo, não mandato. Requisito que isso impõe: `verification_class` no dado (a primitiva é neutra; a classe dá ao fluxo o poder de compor a barra "posse E/OU identidade").

**Sequência:** o wiring de intake (gargalo) está ✅. OTP é independente do Slice 3 mas complementar — Slice 3 define o campo `resume_policy`, OTP dá a prova que deixa `auto` disparar com segurança. Config no namespace `identity` (tamanho, TTL, máx-tentativas, rate-limit). **Próximo artefato:** mini-spec de `otp_challenge`/`otp_verify` (contrato das tools, chaves Redis, config, fluxo anti-enumeração, emenda ao princípio 7/§4.4) — criticar antes de codar. Ver spec §4.4 (dois momentos), §5, §6/§8 (gate no delegate), princípio 7.

**Dívida colateral ✅ (2026-07-08):** os 2 testes pré-existentes de `test_webhook_bridge.py` (drift anterior, sem
relação com identidade) foram corrigidos — `test_resume_publishes_agent_ready_and_agent_done` usa `AsyncMock` no
`producer.send` (awaitable p/ o `create_task`); `test_process_inbound_does_not_call_resume_handler_for_customer_msg`
deixa o `process_inbound` correr contra o `mock_redis` (a função `forward_inbound_to_active_agent` não existe mais),
com `get`/`hgetall` configurados p/ pular o retry-loop e não vazar coroutine. 17/17 verdes. Ver `CHANGELOG.md`.

---

## OTP produção + primitivo de diálogo genérico (survey + OTP) — resíduos *(ADR ainda Proposto; primitivo v1 + Fatias 1/2 ✅, ver CHANGELOG)*

OTP Fase B é um **MVP tool-based** (identidade progressiva + `verification_class` + `OtpService` + gate `possessed`);
o dialog-primitive v1 (`dialog-api`, `skill_dialog_runner_v1`, `form_get`, editor `/config/dialog-forms`) está entregue
e adotado por OTP, NPS e survey multi-pergunta. ADRs: `docs/adr/adr-otp-workflow-and-dialog-primitive.md` (**Proposto**),
`docs/adr/adr-identity-channel-possession.md`; spec: `docs/product/dialog-primitive-and-runner-design.md`.
**Inegociável (invariante):** o código do OTP nunca passa pela mão de um agente — gerar/enviar/verificar ficam no `OtpService`/channel-gateway.

**OTP — produção (ADR não implementado)**
- **D1 — OTP como workflow negocial + especialista de canal** (`delegate-workflow-io`, Arc 19) segue **só desenhado**: workflow channel-abstract exposto como step-up reusável (`{verified}`) + especialista Tier-3 dono do canal. Hoje é tool-based no intake. Item 6 (OTP como step-up genérico) depende disto.
- **Item 1 — entrega real** (SMS/e-mail, envio por canal ≠ sessão = posse forte) **adiado até termos canais**; vira o `collect` do especialista.
- **Trilha B / D3 — tela de OTP em Configurations**: tuning numérico (TTL, tentativas, rate-limit, canais de posse) é **env-only**; falta namespace `identity`/`otp` no config-api + bindings (`form_id` dos prompts, `template_id` de entrega).
- **Trilha C — segurança**: auditoria de challenge/verify (Kafka/`mcp.audit`, item 5); **lockout crescente** (item 7); **testes de unidade** do adapter/endpoints (item 8).
- **Trilha A** — textos/i18n dos prompts de OTP (item 3) *(verificar: o retry na mesma superfície já saiu em 2026-07-07)*.
- **D2** — atualizar o spec de survey (§17/§19) para consumir o primitivo de diálogo *(verificar se já feito)*.

**Limitações declaradas do primitivo (aceitas, sem fix)**
- **Hooks de fim-de-contato não podem delegar** — `suspend` = hook concluído → o contato fecha antes de renderizar. Por isso o NPS ativo (`agente_nps_v1`, `on_contact_end`) roda **inline** (form_get + menu dinâmico), não via runner. Runner só serve chamadores que podem suspender.
- **Delegate de nível único** — aninhar o runner dentro do collector colide em `session.delegate_resume_token` (rejeitado).
- **`channel_policy: elect` adiado (decisão C, 2026-07-08)** — eleição de canal hoje é uma `question` do form lida pelo workflow; o `elect` de 1ª classe conflita com a segregação de perfil (reach/`collect` é exclusivo de `workflow`, runner é `agent`). Reabrir quando houver fluxo que exija o runner **ele mesmo** re-despachar cross-canal (aí decidir A escopado vs B pleno).
- **Binding do form no runner é contexto de delegate** (`@ctx.session.dialog_form_id`), não `$.config` — o hook `$.config` existe, mas a migração para deploy-por-slot só foi feita no `skill_survey_multi_v1` *(verificar se o runner/OTP ainda dependem do ctx)*.

**Config params por deploy**
- Skill parametrizado **exige deploy por slot** com `config_json.form_id` (`set-next` + `promote`); sem isso o `form_get` falha em runtime.
- **Typo de `source` não é tratado no deploy** — o lint no publish (`configParamSourceWarnings`, agent-registry) é apenas **avisador, não-bloqueante**.
- Worker legado `skill-flow-worker` fora de escopo (Arc 19 o deprecou).

**Editor de dialog-forms `/config/dialog-forms` — 2ª passada**
- Reordenar nós por **drag** (hoje setas ↑↓); **edição de locale lado-a-lado** + progresso de tradução estável; **preview** do que o cliente vê.
- **Auth no write** — hoje **aberto**, sem gate ABAC `config.*`.
- Validação client-side com mensagens (form_id slug, `output_key` único, `dimension_id` snake_case); confirmação ao descartar rascunho (dirty/blocker); `interaction=form` com múltiplos `fields`.

**Survey / scoring**
- `survey_question` **reutilizável** — fora do 1º corte, ainda pendente.
- **Entrega do link web**: falta só o **operacional** (tenant apontar `survey.link_delivery.webhook.url` pro gateway SMS/e-mail dele + `PLUGHUB_SURVEY_LINK_WEBHOOK_TOKEN`); `SmtpProvider` nativo é opção futura; **UI dedicada** para `link_delivery` é follow-up (hoje só config genérica). §9.2/§19 de customer-surveys.

**Guard de teardown-hook (Tarefa #17) — endurecer**
- O guard atual (`_validate_teardown_hooks`/`_load_skill_steps` no `registry_syncer.py`) é **read-only, fail-open**: só loga ERROR. O desenho pede **rejeitar no deploy/sync** (agent-registry/RegistrySyncer) quando o flow de um skill deployado em pool-alvo de `PoolHooks.on_contact_end/on_human_end/post_human` contiver step que suspende — reusando a varredura do `_computeFlowModel` **estendida com `delegate`** (hoje `_computeFlowModel` só olha `suspend`/`collect`). Alternativa descartada por ser menos robusta: flag declarado `classification.execution_context`.

---

## evaluation-api — 10 testes de `test_router.py` quebrados por drift de ambiente *(achado ao vivo, 2026-07-02)*

Encontrado ao validar o fix de self-view (ver `CHANGELOG.md` § "evaluation-api — bug self-view..."): rodando
a suíte local (`pytest`, Python 3.12.3, `pytest-9.1.1`) — ambiente mais novo que o usado da última vez que os
`.pyc` cacheados foram gerados (Python 3.10) — **10 de 83 testes falham**, todos em `test_router.py`, **nenhum
relacionado à mudança de self-view** (confirmado por leitura: os testes que tocam `_compute_result_scope`/
`list_results` — seção T10-C de `test_available_actions.py` + o novo teste de regressão — passam 100%).
Três causas raiz distintas, todas pré-existentes:

1. **`AsyncMock.keys() returned a non-iterable` (7 casos)** — `_row()` em `db.py` faz `dict(record)` sobre o
   retorno de `fetchrow()` de um `MagicMock()` fake; a versão mais nova da lib `mock` (stdlib do Python 3.12)
   trata isso diferente. Afeta `TestIngest` (4 casos, via `_db.set_contestation_state`) e `TestResults` (
   `test_list_results` via `_db.get_campaign` — chamada pré-existente no handler pra montar
   `available_actions`, nunca mockada pelo teste; `test_lock_result` via `_db.lock_result`).
2. **`422` em vez de `200`/`400` (2 casos)** — `test_review_result`, `test_review_invalid_outcome`: o schema
   de validação do endpoint `/review` evoluiu desde que os testes foram escritos.
3. **`'State' object has no attribute 'redis'` (2 casos)** — `TestContestations::test_create_contestation` e
   `test_cannot_contest_locked_result`: o endpoint legado de contestação (`/v1/evaluation/contestations`)
   passou a exigir `request.app.state.redis`; a fixture `_app_with_mocks` não seta isso.

**Não bloqueia** nenhum trabalho corrente — documentado aqui só pra não perder o achado. Corrigir exige (1)
atualizar `_row`/os testes pra funcionar com o mock mais novo (ou fixar versão de `mock`/pytest do projeto),
(2) alinhar `test_review_*` ao schema atual do endpoint, (3) a fixture `_app_with_mocks` setar `state.redis`
(`AsyncMock()`) por padrão.

---

## Flow — step de expressão sandboxed (NÃO eval cru) *(decisão de design, 2026-06-28)*

**Necessidade**: valores computados / lógica mais rica em flows (ex.: o loop p/ ler o form JSON de pesquisa de
satisfação; condições derivadas além de JSONPath em `choice`). **Ideia descartada**: um step que roda
**JavaScript livre (`eval`)** com acesso ao ContextStore — quebra invariantes (Redis só via routing/skill-flow,
MCP audit, masking/LGPD, isolamento de tenant) e abre RCE/exfiltração/loop infinito.

**Recomendado**: **step de expressão sandboxed, read-only**:
- avaliador de expressão **restrito** (estilo CEL/jsonlogic), **puro e determinístico**, **sem I/O nem rede**,
  com limite de CPU/tempo; lê `@ctx.*` (respeitando escopo/visibility), **não** escreve direto no Redis;
- saída tipada gravada via os mecanismos já existentes (`context_tags`/output), nunca acesso bruto ao store;
- cobre a maioria dos "flows complexos" sem o buraco de segurança do eval.
- **Casos específicos já têm caminho seguro**: pesquisa de satisfação → form JSON interpreter + menu dinâmico
  (decisão B do ADR de surveys); lógica que não cabe em expressão → step `reason` (AI Gateway + `output_schema`).
- **Código de verdade** (Turing-completo) só no **SDK/agente nativo** (runtime controlado, já auditado), nunca
  como step de flow.

Invariante a preservar: nenhum step de flow executa código arbitrário do tenant com acesso ao runtime interno.
*(discussão; sem implementação)*

---

## Agent Principal — identidade de máquina p/ agentes IA *(spec, 2026-06-28)*

Identidade de máquina (`subject_type:"agent"`) p/ agentes nativos e externos se autenticarem, distinta das
roles humanas; capability vem do `agent_type` (registry), auth-api só emite/rota credencial; audit por
`principal_id`. Nativo = auto-provisionado, **sem UI**; externo = cadastro + secret (API/CLI; UI enxuta na F3).
Fases F1–F4. **Spec**: `docs/product/agent-principal-identity-spec.md`. *(discussão; não implementado)*

---

## Dashboards — cobertura de catálogo *(spec, 2026-06-28)*

O sistema composável (estilo Grafana) **já existe** (Dashboard #35/Arc 16: DisplayTool registry, grid,
Add Card 3-passos, runtime filters, `/reports/display/*`). Fases (spec): **F1 cobertura** — expor no
`ENDPOINT_CATALOG` os relatórios ausentes (segmentos/complexidade, disponibilidade, Fila/SLA, Pools/Infra,
qualidade/calibração, surveys, performance diária) via o contrato existente; **F2 consumo no Home** — `HomePage`
renderiza o dashboard do usuário (destravar p/ todas as roles; builder segue em Config/admin); **F3 allowlist +
starter por role** (`role_catalog:{role}` no Config API: admin define componentes liberados + layout starter;
reconcile no load); **F4 picker do usuário** (escolhe/arruma dentro da allowlist; layout pessoal já existe).
Escopo de dados sempre via ABAC/`accessible_pools`/`supervised_*` no endpoint. **Decisão: NÃO** construir
datasource/query-builder genérico (dado interno); novos tools (heatmap/gauge/leaderboard) só sob demanda.
**Spec**: `docs/product/dashboard-catalog-coverage-spec.md`. *(discussão; não implementado)*

---

## Isolamento do substrato por `origin` — Fase 2 (adiada) *(arco completo 2026-06-25; histórico no CHANGELOG)*

**Fase 2 — ADIADA por decisão (2026-06-25), não enterrada.** Conteúdo: partição CH
`PARTITION BY (toYYYYMM(date), origin)` em tabelas novas/migração versionada (lifecycle/LGPD; **não**
in-place — CH não altera partition key in-place); campo `pool.origin_class: production|import|review`
(default production), **ortogonal a `agent_kind`**, como atalho/validador p/ pools dedicados + eixo de
agrupamento na UI.

**Por que adiar:** a fase 2 é **governança/lifecycle, não correção**. A separação dos dados (o problema
real) já está garantida pelo **filtro de leitura default `live`** (passo 4) + sampling (passo 5); a partição
não muda nada disso. Hoje não há importação externa real e a reavaliação é de volume mínimo → custo/benefício
não fecha.

**Gatilho que reativa (vira necessária, não opcional):** entrada de **importação externa real com obrigação
de retenção/erasure própria** (LGPD — dado de terceiro com prazo distinto, ou direito ao esquecimento que
precise expurgar **só** o `import`/`reeval`). Nesse cenário o filtro de leitura não basta: precisa da
separação **física** para `DROP PARTITION` barato/limpo (a alternativa, `ALTER … DELETE`/mutation, é pesada
e não-particionada). Enquanto esse requisito não existir, fica como backlog.

---

## G-PROBE — Auth ABAC/serviço nos endpoints do Quality (evaluation-api)

**Fase 1 ✅ (config humana, 2026-06-25):** mutações de forms/campaigns/rubric gateadas por
`_require_evaluation_field` (grant-first, deny em config vazio; forms/campaigns→`formularios`,
rubric→`gerir_rubrica`, read_write). Route guard `RequireEvalAccess` em todas as rotas de evaluation
(espelha o nav strict, sem bypass). Bearer JWT (de `session.accessToken`) nas mutações + hooks de lista
no platform-ui. Detalhe em `CHANGELOG.md`.

**Listas abertas (decisão fase 1):** `list_forms/campaigns/rubric` ficaram **sem gate** — são read
compartilhado (Avaliações/Calibração/Curadoria/Reports mapeiam id→nome com `report`/`revisar`/`curar`,
não `formularios`; gateá-las quebraria essas telas). GET-by-id/resolve/effective também abertos
(runtime: session-replayer lê `forms/{id}`, mcp-server lê `rubric-templates/effective`).

**Fase 2 — slice backend ✅ (2026-06-26); wiring + UI PENDENTES.** Decisões da sessão: gate de serviço
**strict** (sem fallback admin-token); UI usa **Bearer+ABAC** (sem segredo no frontend); slice backend-first.

- ✅ **`_require_service`** (strict `X-Service-Token`, `config.service_token` env
  `PLUGHUB_EVALUATION_SERVICE_TOKEN`, vazio = no-op/demo) em: `ingest`, `claim_instance`,
  `expire/skip/mark-error`, `dispatch_scan`, `submit_pre_review`, `submit_ai_review`,
  `publish_calibration_note`.
- ✅ **`_require_service_or_eval_write`** (serviço OU Bearer+ABAC `formularios:rw`) nas ações de ops
  disparáveis pela UI: `dispatch_campaign`, `backfill`, `seed/flush-synthetic`, `sampling-rules` CUD.
- ✅ **`_require_any_evaluation`** (any-of, degradação graciosa) nas LEITURAS de lista: forms, campaigns,
  rubric-templates, instances, contestations, calibration-notes, sampling-rules.
- ✅ Testes `tests/test_gprobe_phase2.py` (funções puras). Ver CHANGELOG.

**Slice caller-wiring ✅ (2026-06-26):**
- ✅ **Provisionado** `PLUGHUB_EVALUATION_SERVICE_TOKEN` no `docker-compose.demo.yml` (evaluation-api +
  mcp-server-plughub; valor demo `changeme_eval_service_token_demo`). Gates de serviço agora ENFORCED no demo.
- ✅ **mcp-server** `evaluation_pre_review_submit` envia `X-Service-Token` (env; `EVALUATION_API_URL` também
  provisionado p/ o container). Único caller HTTP backend de endpoint service-gated (o avaliador real publica
  por Kafka, não por HTTP `/ingest`; os scanners chamam a função direto, não o endpoint).
- ✅ **UI bridge**: `seed/flush/dispatch` da `CampaignsPage` passam o Bearer do operador (`session.accessToken`)
  → `_require_service_or_eval_write` aceita via ABAC. Input de admin-token vira vestigial (remoção = cleanup UI).
- ✅ **Smoke** `infra/test/smoke_gprobe_service_auth.sh` valida os 3 gates (service strict / dual / any-of).

**Follow-ups restantes:**
- ⏳ **Repair dos ~15 e2e legados de eval** (`test_t7a/t9*/t10*/t12/t13/t14/t15/t17/r1/r6/t7b2`): **já vermelhos
  pela Fase 1** (criam form/campanha SEM Bearer; `create_form/create_campaign` exigem `formularios:rw`) —
  precisam de (a) Bearer mintado p/ o setup E (b) `X-Service-Token` nos calls G-PROBE-gated (ingest/dispatch/
  scan/backfill/ai-review/skip/mark-error/sampling-rules). Dívida pré-existente da Fase 1; smoke dedicado cobre
  o G-PROBE no intervalo.
- ✅ **Cleanup UI** (2026-06-26): input de admin-token removido da `CampaignsPage` (state/input/props +
  i18n `campaigns.sidebar.adminTokenPlaceholder` en/pt); `saveCurationSamplingRules`/`useCurationSamplingRules`
  passam o Bearer do operador. Bearer explícito nos consumidores de lista que faltavam (`useInstances`,
  `useContestations`, `useCurationSamplingRules`); forms/campaigns/rubric/results/curations já tinham. Ver CHANGELOG.

**Pendente — admin-token boxes platform-wide → Bearer+ABAC (FORA do escopo G-PROBE, não bloqueia):**
G-PROBE cobriu só o módulo Quality (evaluation-api). O MESMO anti-padrão (caixa de texto de admin-token na UI,
em vez de autorizar pelo JWT do operador + ABAC) persiste em outras telas, cada uma gateando um serviço
diferente pelo seu admin-token. Migrar cada uma é um "mini-G-PROBE" por serviço (gatear endpoints em
Bearer+ABAC + remover a caixa). Inventário:
- ✅ **`config/access` (`AccessPage`) + `config/groups` (`GroupsPage`) → auth-api** (`config.usuarios`) — slice
  CONCLUÍDO (2026-06-26): gate strict Bearer+ABAC na auth-api (router + groups_router), seed_auth minta Bearer
  de bootstrap, UI usa session Bearer (listas carregam no login — conserta o bug reportado). Smoke
  `smoke_config_usuarios_auth.sh`. Ver CHANGELOG. *(Follow-up: `auth-api/tests/test_router.py` em X-Admin-Token
  → refresh; envs `*_AUTH_ADMIN_TOKEN` vestigiais → cleanup.)*
- ✅ `config/platform` (`ConfigPlataformaPage`) + `config/masking` (`MaskingPage`) → **config-api** — slice
  CONCLUÍDO (2026-06-26): gate DUAL (admin-token OU Bearer+ABAC mapeado por namespace; default→`plataforma`,
  masking/audit_policy→`masking`); `putConfig/deleteConfig` com Bearer opcional; caixas removidas das 2 telas.
  Smoke `smoke_config_write_auth.sh`. Demais telas de config (Channels/Billing/Dashboards) seguem em admin-token
  (dual cobre) até suas fatias. Ver CHANGELOG.
- ✅ `config/resources → Skills` (`SkillsPage`, `competencySkills`) → **config-api** (NÃO era agent-registry —
  escreve namespace `competency_skills` via `putConfig`, mapeia ao default `config.plataforma`). Slice UI-only
  CONCLUÍDA (2026-06-26): caixa removida, escritas via Bearer; backend já coberto pelo gate dual da config-api.
- ✅ **agent-registry — gate dual nas mutações de config** (2026-06-26): middleware `requireResourceWrite`
  (Express, verificação HS256 em stdlib `crypto`) nos routers **pools/skills/channels/channel-endpoints** —
  GET aberto; mutação exige **X-Service-Token** (callers internos) OU **Bearer+ABAC `config.resources`** (UI).
  Callers internos wirados: RegistrySyncer (`registry_syncer.py`) + `skill_deploy` (`deploy.ts`) mandam
  `x-service-token`. UI: `registry.ts` manda Bearer via novo `auth/token-store.ts` (holder de módulo espelhado
  pelo AuthContext) → caixa da `SkillsPage` removida. Provisionado `PLUGHUB_JWT_SECRET` +
  `AGENT_REGISTRY_SERVICE_TOKEN` (agent-registry + orchestrator-bridge + mcp-server). Smoke
  `smoke_agent_registry_write_auth.sh`. Ver CHANGELOG.
  - **Residual (fora desta fatia, FORA do gate de propósito):** `pool-slots` (promote/rollback do Fluxo→Deploy,
    cadeia via mcp-server), `instances`/`operational` (runtime: bootstrap/heartbeat). Gatear esses = fatia
    própria (wirar a cadeia de deploy + bootstrap). Ferramentas CLI de import (`sdk/cli/import.ts`,
    `gitagent/import.ts`) mutam `/v1/skills` sem token — dev/CI, não-runtime; passar `x-service-token` se forem
    usadas contra registry gateado.
- ✅ `config/channels` (`WebChatConfigPage` + `WebhookConfigPage`) → **config-api** `config.canais` — slice
  CONCLUÍDO (2026-06-26): backend já dual; add `webhook`→`canais` no mapa; caixas removidas, escritas via Bearer.
  Smoke estendido (§4). Ver CHANGELOG.
- ✅ `config/billing` (`BillingPage`) → **pricing-api** (NÃO era config-api — usa `/v1/pricing/*`) — slice
  CONCLUÍDO (2026-06-26): gate DUAL na pricing-api (admin-token OU Bearer+ABAC **`config.plataforma`** — decisão:
  reusa config.plataforma, sem campo billing novo; o módulo `billing` só tem `visualizar`/read). `jwt_secret` +
  `PLUGHUB_PRICING_JWT_SECRET`. Caixa removida; reserve activate/deactivate via Bearer. Smoke
  `smoke_pricing_write_auth.sh`. Ver CHANGELOG.
- ✅ `config/dashboards` (`DashboardsPage`) → **config-api** namespace `dashboards` (→ default `config.plataforma`)
  — slice UI-only CONCLUÍDA (2026-06-26): `dashboard-hooks` (configGet/Put/Delete/List) mandam Bearer via
  token-store; caixa de admin-token (+ localStorage `plughub_admin_token`) removida. Backend já coberto pelo gate
  dual da config-api. Ver CHANGELOG.
- ✅ `evaluation/knowledge` (`KnowledgePage`) — **fatia de wiring CONCLUÍDA (2026-06-26)**. Recon confirmou que a
  página estava **morta**: `/v1/knowledge/*` não existia em lugar nenhum (proxy ia p/ eval-api:3400 sem rotas;
  mcp-server-knowledge só tinha `/admin/*` + MCP tools). Construído o **surface REST** na mcp-server-knowledge
  (`routes/knowledge.ts`: GET `/v1/knowledge/search`, POST/DELETE `/v1/knowledge/snippets`, reusando `db.ts`),
  gate DUAL (`require-knowledge-access.ts`: X-Service-Token OU Bearer+ABAC `evaluation.gerir_rubrica`, read p/
  search / read_write p/ snippets). Proxy Vite `^/v1/knowledge` → **3401**. Publish de CalibrationNote da
  evaluation-api passa `X-Service-Token` (conserta o KB vetorial do Arc 13, que silenciava em 404). UI usa Bearer
  (token-store) e perde a caixa. Smoke `smoke_knowledge_rest_auth.sh`. Ver CHANGELOG.
- ✅ `Avaliações` filters (`AvaliacoesPage`) — caixa de admin-token removida (2026-06-26); a adjudicação Arc6
  **legada** usa o Bearer do operador (`adjudicateContestation` → `bearerHeaders`). *Resíduo:* a **retirada
  física** do endpoint/UI `adjudicate` segue junto da limpeza do motor Arc6 legado (não bloqueia).
Decisão (2026-06-26): sequenciável por serviço; auth-api foi a 1ª fatia (strict, decisão da sessão). Inventário
completo das telas com caixa de admin-token: access, groups (✅ auth-api), platform, masking (config-api),
resources/skills (agent-registry), knowledge (mcp-server-knowledge), avaliações/adjudicate (evaluation-api legado).

**Rot pré-existente (separado do G-PROBE, não bloqueia):** `evaluation-api/tests/test_router.py` tem
11 testes quebrados **independentes do gate** (classes TestInstances/Ingest/Results/Contestations):
mocks não cobrem `set_contestation_state`/`get_campaign`/`lock_result` (chamadas novas Arc 13),
`app.state.redis` ausente no app de teste, payload de review desatualizado (422), `expire_instance`
sem `x-admin-token` (container tem `admin_token` setado). Atualizar os mocks ao contrato evoluído.

---

## Webhook pools — throttle de downstream: enforcement no routing *(deferred)*

Re-validação 2026-06-04 (ver `CHANGELOG.md`): o default 500 **já não existia** no código
(schema `.optional()`, registry grava null); a premissa "nada é pré-instanciado" ficou
stale pós Arc 19 Fase C — capacidade real de webhook = slots de instância do deploy
(Bootstrap) + admissão híbrida. O `max_concurrent_sessions` pool-level era display-only
no Monitor (capacidade fictícia) — coerência aplicada: removido do YAML demo, comments
schema/registry revisados ("throttle opcional de downstream").

**Deferred**: enforcement real do throttle no routing quando configurado
(`active_count ≥ max` → enfileira; backpressure p/ downstream frágil, ex. ERP).
Implementar quando houver caso de uso real.

---

## Delegate v2 — itens restantes (pós-correção do ciclo de portabilidade)

Modelo corrigido e backend verde em [`docs/arcos/delegate-workflow-io.md`](docs/arcos/delegate-workflow-io.md)
(delegate sempre roda o alvo como segmento conference do chamador; A-new fecha como webchat;
`context_set` registrado; specialist de B adia instantâneo). Restam:

- **Fase C — heurística de canal na UI ✅** (já implementada — TODO estava
  desatualizado): `ListaTab.tsx` classifica pelo `channel_type` real (canal decide
  WorkflowTraceList vs SegmentList) e o badge "suspended" é restrito a `channel ===
  'webhook'` (webchat em delegate-wait lê live). Nota residual no código: contador
  de participantes vivos exigiria suporte de backend — channel é o proxy aceito.
- **Fase D — timeout scanner do delegate ✅** (já implementado — TODO estava
  desatualizado; ver `delegate-workflow-io.md` § Fase D): `run_timeout_scanner` em
  `channel-gateway/adapters/webhook.py` (lifespan, 60s) expira `resume_tokens`
  vencidos via `handle_resume(decision="timeout")` → `on_timeout` do step; cobre
  suspend e delegate; `pending_workflow` stale auto-limpa no próximo reconnect.
- **Fase E — Workflow Execution Trace (step-level)** ✅ (E.1/E.2/E.3 + transcript):
  step timeline já renderiza; `step_io` com `decision`/`payload`/`child_session_id` por step
  (E.1); `resumed_by` por step (E.3); duration webhook = tempo decorrido total (E.2);
  transcript do specialist via clique no nó de agente (já existia). Design em
  `docs/arcos/delegate-workflow-io.md` § Fase E.
  - **E.4 diferido (sem dado no demo)**: (a) **MCP audit** por step — `skill-flow-service`
    chama o mcp-server via cliente cru, não pelo `McpInterceptor`, então os `invoke` não
    geram `mcp.audit`; construir quando a execução passar pelo interceptor. (b)
    **agent_events** (Arc 12) — agentes de portabilidade não emitem. (c) snapshot de
    ContextStore com evolução entre suspends (hoje só o estado atual no strip Input context).
    (d) duration "corridas vs úteis" (business_hours) lado a lado.

## Pricing → quota Redis não existe: o gate de admissão não arma *(achado 2026-06-04)*

As chaves `{t}:quota:*` lidas pelo `assertQuota` estão documentadas em `docs/arcos/pricing.md` e no
CLAUDE.md, mas o **pricing-api não tem código Redis** (`keys 'tenant_demo:quota:*'` volta vazio depois
de um POST de resources). Consequência: o teto contratado é só **analítico** (denominador do
occupancy) — o gate de admissão **nunca arma**. No demo isso obriga `INCRBY` manual na quota
`max_concurrent_sessions`.

Correção: escrever as chaves no upsert de resources (ou na ativação de plano) e corrigir
`docs/arcos/pricing.md`, que hoje descreve um comportamento que não existe.

---

## Relatórios analíticos — Agentes e Pools *(só o que resta aberto; histórico no CHANGELOG)*

Arco de relatórios (agentes + pools/infra) e Bancada de comparação 360° por `agent_key`. Specs:
[`analytics-reports-redesign.md`](docs/arcos/analytics-reports-redesign.md) · [`pools-infra-report.md`](docs/arcos/pools-infra-report.md) ·
[`analytics-agents-workbench.md`](docs/arcos/analytics-agents-workbench.md) · [`config-consolidation.md`](docs/arcos/config-consolidation.md) ·
[`config-http-propagation.md`](docs/arcos/config-http-propagation.md).

### Dívidas e limitações declaradas

- **`sessions.sla_target_ms` histórico**: sessões antigas permanecem NULL (valor nunca persistido,
  irrecuperável); a aba SLA só popula com contatos novos.
- **`AgentTimeline` — precisão por pool é aproximada**: atribui o intervalo inteiro a cada pool
  tocado; sub-intervalos exatos por pool = refinamento futuro.
- **`farewell_text` só renderiza no webchat**: voice/whatsapp não renderizam (voice = TTS futuro).
- **Quality ainda em fixture (F8 ⏸ adiado)**: `evaluation_dimension_scores` vem de seed de
  `evaluation_results`; `agente_avaliacao_v1` não roda no demo (test-grade, sem associação
  form/campanha). Pendências test-grade da F2: ReplayContext sem `session_meta` e sem associação
  campanha/form. Consertar o pipeline de avaliação = arco próprio.
- **`pool:pending_assignment:{poolId}` é UMA chave por pool** (last-write wins) → chave
  por-instância é melhoria futura (liga à fila pull/inbox).
- **NPS render (cosmético, diferido)**: a mensagem de `menu`/`notify` aparece no transcript como
  "structured content" em vez de texto puro (o dado do NPS grava normalmente) — revisar emit + render.
- **Cenários sem teste** (queue-attended-model): "fila muda" e "drop sem `pool_id`".
- **(verificar)** "Fase 1 — relatório de agentes" nunca foi marcada ✅ (parece absorvida por
  C1/C1b-A/C1b-B + Bancada); idem "Fase 3 · 3d-**parcial**" do provisionamento — conferir o que ficou fora.

### Trabalho futuro planejado

- **F11 — pesquisa multi-grão / surveys diferidas** (arco de evaluation, separado do G7): falta o
  **planejamento da orquestração** — quando/como cada grão (`journey | session | segment`, até 3 por
  fluxo) dispara, e surveys diferidas (`captured_at ≠ session_at`). Base parcial na F10.2b
  (`survey_collector_ia` / `survey_reconnect_ia`). Ver workbench §13/§14 e
  `g7-segment-contact-decoupling.md` §5.
  - **F11.2 (validação)** diferida: simular via curl/seed (publicar `session.signals`/`survey_record`
    com origem de `opened_at` anterior + grão `journey` e conferir `session_at = opened_at`);
    workflow agendado real (dias depois) fica futuro.
- **Catálogo canônico de dimensões de qualidade** (arco próprio): única base rigorosa p/ comparar
  dimensões entre forms. Hoje cross-agente exige mesmo form e cross-form só vale p/ um agente
  (`_compare_quality_lens` expõe `summary.form_ids`; a UI faz o guard).
- **Avaliador dirigido por calendário/campanha** (arco próprio, decisão 2026-06-07): disparar pelo
  `schedule` (JSONB de `evaluation.campaigns`) passando o `session_id`, substituindo o gatilho
  incondicional do Persister.
- **Residuais opcionais do relatório de Pools/Infra** (spec § Pendente): sub-aba Visão geral,
  heatmap hora×dia, SETs de `session_id`, overlay de capacidade licenciada v2.

### Config Consolidation / HTTP Propagation — o que falta

- [ ] **F2** migração por domínio: faltam **hooks**, **evaluation/pricing** e **defaults hardcoded**
      (pools, TTLs, masking e ABAC/users ✅).
  - [ ] **Item 6** — seeds `seed_evaluation`/`seed_pricing` → bootstrap idempotente via API.
        **Estacionado (2026-06-12)**: atacar junto da revisão dos módulos evaluation/pricing.
- [ ] **F3** bootstrap idempotente único (substitui `infra/seed/*.py` + YAML-fonte, só via APIs).
      Arquitetural, sem bug vivo, baixa urgência (`config-consolidation.md` §9).
- [ ] **F4** política de env vars (segurança) — inventário final.
- *Cleanup opcional*: remover o caminho dormente `evaluation_sampler`/`on_pool_config` do
  rules-engine (`on_pool_config` nunca é chamado) — ou religá-lo se a campanha não cobrir.
- *Dead code a varrer*: `_sync_agent_type`/`_prune_agent_types` (`registry_syncer.py`, sem chamador);
  Path A `elif framework == "human"` (main.py, inalcançável); `AgentTypeSchema` (@plughub/schemas) +
  `validators/agent-type.ts` órfão. Testes do agent-registry com agent_type foram deletados — revisar
  a suíte se reativar CI.

---

## G7 — Decoupling segment-end × contact-close *(fases entregues; restam follow-ups + 2 arcos próprios)*

Spec em [`g7-segment-contact-decoupling.md`](docs/arcos/g7-segment-contact-decoupling.md) (§10/§11) +
`conference-mechanics.md`. Fases 0/3, Slices A/B, sub-arco multi-humano (Slices 1/2′/3/4′), arco do
router (alocação atômica) e Camada 3 estão entregues e validados E2E — histórico no CHANGELOG. Resta:

### Follow-ups do modelo de hooks *(baixa prioridade)*

- **Gap (2) — survey customer-side por-segmento não chega aos peers**: `segment_wrapup` reusa a lista
  de `on_human_end` mas filtra `side=agent` (`main.py` ~938) → surveys customer-side (grão=segment,
  NPS) só saem na âncora/primário.
- **Gap (4) — binding grão↔boundary é convenção, não contrato** (skill em "contact ends" gravar
  `grain=session`); disparo com **grão=journey** não está plumbado (não há boundary de fim-de-journey) → F11.
- **Higiene opcional**: convergir `on_human_end` (último) + `segment_wrapup` (peers) num mecanismo
  único de wrap-up por-segmento.
- **Polish (Slice 3)**: atribuição-por-nome do remetente no fan-out humano↔humano.
- **UX cosmético**: sinalizar no Console "convidando, aguardando login do agente" quando o `@mention`
  vai p/ pool sem instância `ready` (não é bug — fila + drain no `agent_ready`, conclusão 2026-06-15).

### Router — alocação atômica *(arco concluído; só residuais opcionais)*

- `get_ready_instances`/snapshots poderiam ler `SCARD` direto (hoje leem o JSON sincronizado pelo
  claim/release — funciona como hint; o claim é o gate atômico). Baixa prioridade.
- Cenário "2 contatos simultâneos no mesmo pool → spread" não exercitado isoladamente.
- Hardening da chave de menu por `segmentId` julgado **desnecessário** após a alocação atômica +
  Camada 3 Fatia A — reabrir só se houver regressão.

### Unificação de contabilidade de agente (kind-agnostic) *(arco próprio — DIFERIDO)*

Anchor "último agente customer-facing" é aproximado por 4 chaves de papéis distintos: `human_agent`
(flag, ~10 sites, hot path de entrega) · `human_agents` (SET, ~10: remaining/restore/participant_left/
fan-out) · `ai_agents` (SET, ~8: restore no close) · `active_ai_specialists` (SET, ~7: defer G2).
Alvo: HASH único `session:{id}:agents → {kind, role, customer_facing, running}`.
- **Decisão (2026-06-13, reafirmada 2026-06-15)**: fazer **oportunisticamente** — só quando um bug
  concreto justificar ou encostado em feature que já toque essas chaves. Refactor puro-interno,
  gateável só por paridade, raio cross-package (mcp-server supervisor/bpm/evaluation), no path mais
  frágil (close).
- Único incremento baixo-risco se encostar no path de entrega: derivar `human_agent` de
  `SCARD(human_agents) > 0` — atenção à aresta (flag setada mesmo com `instance_id` vazio em
  `activate_human_agent`; não é 1:1).

### Detecção de queda involuntária de humano *(Slices 1/2 ✅ — verificar se o alvo está coberto)*

- **(verificar)** Slices 1 (ws.close + grace → `contact_closed(agent_disconnect)`; re-rota ao
  `_ha_pool` quando `remaining<=0`) e 2 (pong-tracking `ws.ping` + `terminate` em 30s) estão ✅ e o
  texto declara "arco heartbeat completo", mas o fechamento do sub-arco multi-humano ainda listava
  este arco como restante — conferir o alvo "posse re-estabelecida por alocação" no caso `remaining>0`.

---

## Frente 3 — Revisão de config / eliminar seeds *(em curso)*

Meta: produção sem seeds re-aplicados — DB é fonte de verdade; setup inicial de DB versionado.
- **Fase 1 ✅ (2026-06-15)** — **seed-if-absent / DB-owned** no `RegistrySyncer` (`registry_syncer.py`): no 409,
  não sobrescreve pool config nem deploy-slot (capacidade); edições de UI sobrevivem a rebuild. Env
  `REGISTRY_SYNC_RECONCILE=true` = reconcile legado (YAML vence) p/ dev. Skills seguem upsert (código). Curou o
  sintoma "Transfer/`escalation_pools` some a cada build". Ver CHANGELOG 2026-06-15 + CLAUDE.md § Configuration.
- **Fase 2 — correção ✅ / arquitetura DIFERIDA (auditoria 2026-06-15)**: a auditoria por store mostrou que
  **todos já são seed-if-absent** (pools via Fase 1; config-api `overwrite=False`; pricing/evaluation checam
  existência; users 409; catálogo ABAC e skills re-aplicam de propósito = código). Ou seja, **não há bug
  pendente** — a "config some no rebuild" está resolvida. O que sobra é só o **sonho arquitetural** (converter
  seeds/YAML em **migração versionada if-absent**, modelo `initdb/01_platform_config.sql`, aposentando
  `infra/seed/*.py` + YAML de registry, store por store) — **baixa urgência**, burn-down gradual sem retrabalho.
  Resíduo opcional: `set_module_config` do `seed_auth` if-absent (demo-users). Ver `docs/arcos/config-
  consolidation.md` §9.
- **Doc** ✅ — `docs/arcos/config-consolidation.md` existe; atualizado com a auditoria + precedência seed-if-
  absent (§9). Referências de `CLAUDE.md`/`registry_syncer.py` resolvem.

---

## Hardening de Auth — postura de sessão do Console *(proposta — não é bug)*

Hoje (Arc 7, por design): `access_token` em memória; `refresh_token` em `localStorage('plughub_refresh_token')`
→ **silent re-auth** no mount (`POST /auth/refresh`). Reabrir a URL após fechar a aba entra logado sem
credencial — esperado, mas é um trade-off UX×segurança. Levers de endurecimento (cada um é arco próprio,
escolher conforme exigência de segurança para um console que vê PII):
- **refresh_token em cookie httpOnly** (em vez de `localStorage`) → mitiga exfiltração por XSS. Maior
  mudança (auth-api seta cookie; CORS/SameSite; CSRF token).
- **Idle/inactivity timeout** — não existe hoje; sessão dura enquanto o refresh_token for válido. Adicionar
  expiração por inatividade no Console + invalidação no auth-api.
- **TTL do refresh_token** — encurtar no auth-api (hoje rotaciona indefinidamente enquanto usado).
- **"Fechar aba = deslogar"** — trocar `localStorage` por `sessionStorage` (morre com a aba); custo de
  conforto (reloga a cada nova aba).
Decisão de produto/segurança pendente: qual combinação aplicar. Sem isso, manter o comportamento atual.

---

## Customer Surveys — estado as-built das fases S1–S11 *(levantamento 2026-07-23)*

> Cruzamento do plano §12 de [`docs/arcos/customer-surveys.md`](docs/arcos/customer-surveys.md) contra o
> **código real** (o F11 abaixo dizia "nenhuma fase iniciada" em 2026-07-02 — **desatualizado**). Tabela
> as-built + evidências + próximos passos completos em **`customer-surveys.md` §12.1**. Achado central:
> várias fases estão **feitas-por-substituição** (dialog-api, `contact_eligibility_check`, `session_signal`
> genéricos cobrem o que o spec pedia como entidades dedicadas de survey).

**Feito / feito-por-substituição (não é trabalho pendente):** S2 (runner genérico + DialogForm), S3 (gatilho
lê outcome), S4 (quarentena → `contact_eligibility_check` genérico), S5 (web + link → `session.signals`).

**Pendente — eixo "fechar parciais primeiro" (decidido 2026-07-23):**

1. **S1 — ✅ FEITO (2026-07-27, ver CHANGELOG).** Catálogo único `survey_catalog.py` + roll-up por instrumento.
   **Resíduos:**
   - **Nenhum produtor emite CES/PMF/FCR** — nenhum DialogForm (`infra/test/seed_dialog_*`) nem skill de survey
     os captura. A normalização está pronta e sem dado: falta um form de seed com dimensions CES/PMF/FCR para
     um E2E de verdade (e para o S6/S8 mostrarem algo além de NPS/CSAT).
   - **UI ignora `value_label`** — `SignalChips` (`AnaliseSurveysPage.tsx`) renderiza só `metric` + número, até
     para NPS ("nps 9" em vez de "Promotor"); `CustomerVoicePage` tem um ternário vazio (`rollup === 'avg' ? ''
     : ''`) onde deveria sufixar `%` para `pct`/`nps_index`. Fatia C do S1.
   - **Rótulos mistos** — CES/PMF/FCR em inglês (spec), NPS/CSAT em pt-BR (histórico gravado). Unificar exige
     decidir migração do histórico + i18n na UI.
2. **S7 (refinos do editor `/config/dialog-forms`):** biblioteca `survey_question` reutilizável, ABAC no
   write (hoje só `X-Admin-Token`), drag reorder, locale lado-a-lado + preview.
3. **S6 (fechar):** view consolidada "Visão do cliente" (cross-cut multi-métrica + divergências §8/§10)
   sobre a base que a lente `customer_voice` já expõe (Customer Voice Fatia 1 = só grão×instrumento + SLA).
4. **Higiene S2:** deployar o trio renomeado (`skill_survey_runner_v1`/`outbound`/`trigger`) como pools no
   `infra/registry/tenant_demo.yaml` — o registry ainda roda o conjunto antigo.
5. **Store per-response** (gargalo que travava S8/S9) — ✅ **FEITO E VALIDADO (2026-07-23, ver CHANGELOG).**
   Schema PG `survey` + endpoint idempotente (evaluation-api); `survey_record` persist-first (mcp-server);
   `survey_web.submit` **captura verbatim** + persist-first (channel-gateway). Smoke
   `smoke_survey_response_store.sh` verde. **Desbloqueia S8.** ADR aceito:
   [`docs/adr/adr-survey-response-store.md`](docs/adr/adr-survey-response-store.md). **Opção A** decidida:
   schema PG `survey` dedicado, escopo mínimo `survey_instance`+`survey_response` (com `open_text`/`audio_ref`),
   host = **evaluation-api**; poda o §7.2 (definições→dialog-api, quarentena→mailing-api). Caminho de escrita:
   `survey_record` persiste antes de emitir + `survey_web.submit` para de descartar verbatim. **Contrato de
   implementação PRONTO** (DDL das 2 tabelas, endpoint `POST /v1/evaluation/survey/responses`, idempotência,
   ordem persist-first, wiring com linha/símbolo exatos, checklist de build):
   [`docs/product/survey-response-store-implementation-spec.md`](docs/product/survey-response-store-implementation-spec.md).
   **Falta só codar.** Abertos: endpoint de leitura de S8, áudio/transcript (S9).
6. **Valor novo (loop captura→leitura→ação):** **S8** (navegador de respostas `/analise/surveys` + verbatim)
   ✅ **FEITO (2026-07-23, ver CHANGELOG).** Restante: **S9** (`agente_survey_analyst_v1` — classifica verbatim +
   áudio/transcript via `attachment_store`) → **S10** (retorno outbound + caixa de ações) → **S11** (NPS/PMF
   relacional agendado). Refino de S8: endpoint de LEITURA já existe; falta só export CSV (opcional) e o
   guard de rota ABAC (Item 3 app-wide).

---

## Arco de Segurança — Pool-scoping em relatórios (ABAC no DADO) *(achado 2026-07-23; Fase A preparada)*

**Problema (levantado pelo usuário, confirmado em código).** O modelo pretende que relatórios/monitores
respeitem o **domínio de pools** do usuário (Arc 7c: `accessible_pools` = filtro de linha; ABAC + grupos).
Hoje isso está **inerte** em toda a superfície de Analytics.

- **Causa raiz (app-wide):** a **platform-ui não envia `Authorization: Bearer`** nas chamadas de `/reports/*`
  e `/v1/evaluation/*` — as páginas de `/analise` usam `fetch(url)` cru; o proxy do Vite é pass-through
  (`vite.config.ts` `^/reports` e `^/v1/evaluation`, só `changeOrigin`). Sem token, o `optional_pool_principal`
  (analytics-api `pool_auth.py`) e o `_decode_jwt_optional` (evaluation-api) resolvem `accessible_pools=None`
  = **irrestrito** ("unauthenticated → all pools", documentado). Ou seja, o filtro por pool é **no-op**: qualquer
  usuário vê **todos os pools**. Vale para journeys, sessions, survey, etc. Postura de demo — mas fura o modelo.
- **Fix camada de dado:** a UI passa a anexar o `bearer()` (existe em `api/registry.ts`, lê o token em memória)
  nas chamadas de relatório — ou um gateway injeta o header. Necessário para QUALQUER scoping de Analytics
  funcionar. Distinto do **Item 3 (guard de rota ABAC)** da seção Journey: aquele protege o *chrome* da página;
  este protege o *dado*. Os dois juntos = enforcement real (rota + linha).

**Gaps ESPECÍFICOS do survey (S8) — só mordem quando o token for enviado:**
1. **`survey_instance.pool_id` não é populado na escrita.** Veículo web (`survey_web.submit`, channel-gateway):
   `pool_id` sai **sempre vazio** (o token congelado não carrega o pool da sessão pesquisada). `survey_record`
   (mcp-server): `pool_id` é input **opcional** → vazio quando omitido. **Decisão de produto**: a resposta deve
   ser atribuída ao **pool da sessão/segmento PESQUISADO** (resolver na escrita — web: do `origin_session_id`
   no `survey_link_create`/persist; record: exigir/derivar). Sem isso o scoping não tem em que se ancorar.
2. **Sem escape hatch de pool vazio** em `db.list_survey_responses` (`i.pool_id IN (...)`), ao contrário da
   analytics-api que usa `(s.pool_id IN (...) OR s.pool_id = '')` de propósito. Com o token ativo + pool vazio,
   um supervisor restrito veria **zero** respostas web (inverte "vê tudo"→"vê nada"). Decidir a política de
   pool vazio junto com o fix (1).
- **LGPD reforça a prioridade:** o verbatim é texto aberto do cliente (dado controlado); ler verbatim de pools
  fora do escopo é vazamento cross-pool, não só cosmético.
- **Referência do padrão correto:** evaluation-api `list_results` + `_compute_result_scope` (row-scope por
  role+grupo+pool, trata self-ownership) — mas **também** depende do token que a UI não manda.

**Fases:**

| Fase | Entrega | Depende |
|---|---|---|
| **A — propagar o token na UI** | ✅ **Completa (2026-07-23):** helper `apiFetch` + **8 arquivos de `analise/`** + **varredura dos demais consumidores** (18 call sites `/reports` em 15 arquivos: `contacts/*`, `contacts/tabs/*` [Monitor/Analise/Agents/AgentTimeline/Lista], `agent-reports/`, `agent-flow/*`, `service/SessionTranscript`, `billing/`, `campaigns/`, `analise/CustomerVoicePage` instruments). Único `fetch` cru remanescente a `/reports` = `api/evaluation-hooks.ts:515` (POST flush-synthetic, já anexa `bearerHeaders`). | — |
| **B — `pool_id` na escrita do survey** | 🟢 **Feito p/ web + NPS inline + J4c collect + multi (2026-07-23):** veículo web plumba `pool_id` (`survey_link_create`→token→`submit`); outbound 5b carimba `origin_pool` na metadata→dispatcher→worker; `agente_nps_v1`/`skill_survey_multi_v1` usam `@ctx.session.pool.id` (origem = self); **J4c** — `handle_collect` resolve o pool do alvo e semeia `session.survey_pool_id` no engage, `skill_survey_runner_v1` o carimba. Smokes: `smoke_outbound_fase5b.sh` + pytest `test_collect_pool_scoping.py`. **Resta 1 seam:** `skill_survey_v1` (survey_processo_ia, F10.2b delegate) grava de `@ctx.session.origin_session_id` sem passar pelo collect → semear o pool no `handle_trigger` (do `origin_session_id`). Até lá pool vazio = admin-only (decisão C). | — |
| **C — política de pool vazio** | ✅ **DECIDIDA strict (2026-07-23): pool vazio = só irrestrito/admin vê.** Sem escape hatch — respeita o domínio (resposta sem pool não pertence a nenhum domínio; over-expor a todos seria mais inseguro que sub-expor). É o comportamento ATUAL da query (`pool_id IN (domain)` já exclui vazio p/ restrito), **sem código**. O "restrito vê zero survey web" é sintoma de B (pool vazio na escrita), não de C. | — |
| **D — endpoints operacionais + `/reports/*` sem scoping** | ✅ **COMPLETA (2026-07-23):** `/v1/operational/pools` (agent-registry) + Monitor SSE `/dashboard/{operational,sentiment,pool-sla}` (token por query param) + auditoria `/reports/*`: `contact-insights` ESCOPADO (subquery a segments); demais não-escopados por decisão fundamentada (`usage`/`campaigns` não pool-atribuídos; `workflows` metadado de processo; `evaluations*` gateados por ABAC evaluation; `quality` unscoped por construção; `instruments` catálogo). Follow-up de posture: JWT em URL do SSE → cookie/ticket em prod. Ver CHANGELOG "Fase D COMPLETA". | A |
| **E — filtro de pool = combo do DOMÍNIO (não texto)** | ✅ **Completa (2026-07-23):** survey usa `PoolMultiSelect` (multi, `pool_ids[]` + reinterseção no backend); **agentes/contatos** usam o novo `PoolDomainSelect` (single) — `AnaliseAgentesPage`/`AnaliseContatosPage` trocaram o texto livre por combo do domínio (`listPools ∩ accessiblePools`). Single (não multi) por decisão: `ContactFilters.poolId` é singular e compartilhado (blast radius) e a segurança já é backend (`optional_pool_principal`). i18n `agentReports.filters.allPools`. | A |

Enforcement completo = **rota** (Item 3 do Journey — guard ABAC de `/analise/*`) + **dado** (este arco).
Ver `docs/arcos/arc7-auth.md` (ABAC/accessible_pools) e `docs/arcos/customer-surveys.md` §7.3.

### Fase A — preparada (turnkey)

**Decisão:** helper explícito `apiFetch` (consistente com o `bearer()` já existente em `api/registry.ts`), NÃO
monkey-patch do `window.fetch`. Motivo: a base já faz merge explícito de header (`bearer()`), sem interceptor
global; um patch global tem efeito colateral em chamadas que não devem levar token (auth/refresh, CDNs). O
custo do explícito (migrar call sites) é aceitável e a segurança do backend **já enforça** quando o token chega
(o gate é permissivo só na ausência) — logo A é **puramente frontend**.

1. **Novo helper** `packages/platform-ui/src/api/apiFetch.ts`:
   ```ts
   import { getAccessToken } from '@/auth/token-store'
   /** fetch que anexa Authorization: Bearer do token em memória (se houver e não já setado).
    *  Usar em TODA chamada de relatório (/reports, /v1/evaluation, /analytics). */
   export function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
     const t = getAccessToken()
     const headers = new Headers(init.headers)
     if (t && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${t}`)
     return fetch(input, { ...init, headers })
   }
   ```
2. **Migrar os call sites** de `fetch(` → `apiFetch(` nas chamadas de `/reports/*` e `/v1/evaluation/*`.
   Superfície confirmada (8) em `src/modules/analise/`: `AnaliseSurveysPage`, `AnaliseJourneysPage`,
   `CustomerVoicePage`, `AnalisePoolsPage`, `AnaliseAgentesPage`, `AgentsBenchPage`, `MetricSelector`,
   `AnaliseComparacaoPage` — **+ varrer `src/modules/monitor/`** e demais consumidores de `/reports`
   (grep `fetch\(['"\`]/(reports|v1/evaluation|analytics)`). Só GET de relatório; não tocar chamadas de auth.
3. **Backend: zero mudança em A** — `optional_pool_principal` (analytics-api) e `_decode_jwt_optional`
   (evaluation-api) já leem o `Authorization` e aplicam `accessible_pools`. **Exceção:** para o survey,
   entregar a **Fase C junto** (escape hatch), senão o admin segue vendo tudo (accessible_pools vazio→None) mas
   um supervisor restrito perde as respostas web (pool vazio).
4. **Verificação:** logar com um usuário **restrito** (accessible_pools não-vazio, sem admin) → só vê linhas
   dos seus pools em `/analise/*`; admin (accessible_pools vazio→None) → vê tudo. Cobre journeys + survey.
   Guard futuro (opcional): lint/grep que falha em `fetch('/reports`|`fetch('/v1/evaluation` cru (fora do
   `apiFetch`), p/ não reintroduzir call site sem token.

**Consequência aceita (decisão C strict):** com o token fluindo (A) + a decisão strict (C), um usuário
**restrito** vê **zero** respostas web hoje (todas com pool vazio) — é correto (não pertencem ao domínio dele),
não um bug. **Admin não é afetado** (domínio vazio→None→vê tudo). A completude vem de **B** (carimbar o pool
da sessão pesquisada na escrita), que faz as respostas web aparecerem para o supervisor do pool certo.
**Próximo passo natural do arco: B.** (Validação E2E do A/C/E ✅ 2026-07-23 — admin restrito a 2 pools passou a
ver só o pool do domínio; ver CHANGELOG.)

### Fase B — 🟢 web + NPS inline feitos (2026-07-23); falta J4c runner/workflow

**Entregue (ver CHANGELOG § "Segurança — Pool-scoping: Fase B"):** veículo web plumba `pool_id`
(`survey_link_create`→`create` congela no token→`submit` carimba persist + `session.signals`); outbound 5b
carimba `origin_pool` na metadata→dispatcher (`session.survey_origin_pool`)→worker; `agente_nps_v1` passa
`@ctx.session.pool.id`. Smoke `smoke_outbound_fase5b.sh` prova pool não-vazio + controle negativo.

**J4c collect-based ✅ (2026-07-23):** `handle_collect` resolve o pool do alvo (`signal_target_id`) do ctx
(`session.pool.id`), congela em `pending.signal_pool_id`; `handle_collect_engage` semeia `session.survey_pool_id`;
`skill_survey_runner_v1` passa `pool_id: "@ctx.session.survey_pool_id"`. `skill_survey_multi_v1` pesquisa a
própria sessão → `@ctx.session.pool.id`. Pytest `test_collect_pool_scoping.py`.

**Resta `skill_survey_v1` (F10.2b delegate, survey_processo_ia):** grava via `survey_record` de
`@ctx.session.origin_session_id`, mas NÃO passa pelo `handle_collect` (é delegate, não collect). Para carimbar o
pool: no `handle_trigger` (webhook.py), quando `origin_session_id` vier no `workflow_trigger`, ler o
`session.pool.id` do ctx da origem e semear `session.survey_pool_id` na sessão do workflow → `skill_survey_v1`
passa `pool_id: "@ctx.session.survey_pool_id"`. Mudança genérica no trigger (afeta todo trigger com origin) —
avaliar custo/benefício. Até lá, pool vazio = admin-only (decisão C), correto e sem crash.

**Objetivo (histórico):** `survey_instance.pool_id` deixa de nascer vazio — carimbar o **pool da sessão/segmento
PESQUISADO**, para a resposta ter domínio e o supervisor do pool certo a ver.

**Decisão de produto:** o pool da resposta = o pool da **sessão de origem** (`origin_session_id`), não o pool
do dispatcher/runner de survey. É o atendimento que gerou a pesquisa que define o domínio.

**Dois veículos (investigar a origem do pool em cada um):**
1. **Web** (`survey_web`, channel-gateway): o token (`survey_web:token`) tem `origin_session_id`+`grain` mas
   **não** o pool. Duas opções a decidir: **(a)** `survey_link_create` (mcp-server `tools/survey.ts`) passa o
   `pool_id` do contexto do chamador (o hook/skill que cria o link roda numa sessão COM pool — `session.pool.id`
   no ContextStore) → congela no token → persiste; **(b)** resolver no persist a partir do `origin_session_id`
   (lookup do pool da sessão — analytics-api `sessions.pool_id` OU ContextStore `session.pool.id`). (a) é mais
   barato (sem lookup) e o pool já está no contexto de quem dispara; preferir (a), (b) como fallback.
2. **Conferência/inline** (`survey_record`, mcp-server): `pool_id` é input **opcional**. O runner/inline
   (`agente_nps_v1`, `skill_survey_runner_v1`) roda na sessão pesquisada → tem `session.pool.id` no contexto →
   passar via `$.pipeline_state`/`@ctx`. Verificar se o skill já resolve o pool e só não o passa.

**Escopo mínimo:** carimbar o pool na escrita (web + record) + demo/smoke que prova a resposta nascendo com o
pool real (não vazio) e o usuário restrito daquele pool passando a vê-la. **Não** precisa migração de dado
antigo (pool vazio legado = admin-only, decisão C). **Entry points:** `channel-gateway/survey_web.py` (create/
submit + token record), `mcp-server/tools/survey.ts` (`survey_link_create`/`survey_record`), ContextStore
`session.pool.id` (escrito pela Routing Engine no `_write_pool_context`). Ver ADR `adr-survey-response-store.md`
(o `pool_id` já existe no schema; falta a origem na escrita) e `customer-surveys.md` §7.3.

### Fase E — filtro de pool = combo do domínio ✅ (2026-07-23)

**Concluída:** survey → `PoolMultiSelect` (multi, `pool_ids[]`); agentes/contatos → `PoolDomainSelect` (single,
`components/ui/PoolDomainSelect.tsx`) em `AnaliseAgentesPage`/`AnaliseContatosPage`. Single por decisão
(`ContactFilters.poolId` singular/compartilhado; segurança já no backend). Ver CHANGELOG "Fase E (combo do
domínio em agentes/contatos)". Notas de design abaixo (mantidas p/ referência).

**Confirmado (2026-07-23):** o domínio do usuário = bloco **"Accessible Pools"** em Configuration > Access
(`AccessPage.tsx` → `user.accessible_pools` na auth-api → claim `accessible_pools` no JWT; **vazio = todos**).
A sessão **já expõe** isso no client: `useAuth().session.accessiblePools` (`AuthContext`, `[]` = todos).

**Problema:** o filtro de pool nas telas de Analytics é **caixa de texto** — `AnaliseSurveysPage.tsx:233` (a
nova), `AnaliseAgentesPage.tsx:376`, `AnaliseContatosPage.tsx:107`. Deveria ser um **combo multi-select do
domínio**. (`AnaliseJourneysPage`/`CustomerVoicePage` não têm filtro de pool.)

**Design:**
1. **Fonte das opções (client):** `registryApi.listPools(tenantId)` (`api/registry.ts`, já normaliza `items`)
   **∩ `session.accessiblePools`** — se `accessiblePools` vazio (admin) → lista cheia. Assim o combo mostra
   só o que o usuário pode ver (o filtro nunca oferece pool fora do domínio). Referência de `<select>`
   populado por `listPools`: `AnaliseProcessosPage.tsx` (fetch L104-108 + select L151-157) — copiar, mas
   **multi-select** (checkbox-list, como o de `AccessPage.tsx` L430-478, o único multi-select do app; não há
   componente compartilhado — extrair um `PoolMultiSelect` reusável é oportuno).
2. **Backend aceita lista:** `GET /v1/evaluation/survey/responses` troca `pool_id: str` por `pool_ids`
   (repetido ou CSV); `db.list_survey_responses` já filtra `i.pool_id IN (...)` — passar a lista do filtro
   **interseccionada com `accessible_pools`** (o filtro é subconjunto do domínio; a fronteira dura continua no
   scoping da Fase A/C). Vazio no filtro = todo o domínio (não todos os pools).
3. **Invariante:** filtro (subconjunto escolhido) ≠ scoping (domínio permitido). O combo só oferece o domínio;
   o backend **sempre** reintersecta com `accessible_pools` (nunca confia só na UI).
4. Aplicar o mesmo `PoolMultiSelect` às outras telas de texto (agentes, contatos) na varredura.

---

## Detach de hooks de finalização + Pull direcionado + ACW *(desenho fechado 2026-07-23; Camada A iniciada)*

Unifica a coleta de finalização (survey/wrap-up) e aposenta a **Forma A (delegate `skill_survey_v1`)**. Hooks de
finalização não podem suspender/collect (o bridge trata `suspended` como concluído → fecha o contato cedo). A
razão de segurar o contato é **atribuição** — que a Journey (`root_session_id`) + referência de segmento no
payload resolvem sem segurar. Reduz de 3 mecanismos (inline/delegate/collect) para 2 (inline síncrono / collect
assíncrono). Fecha **G1** (AHT inflado por wrap-up) e generaliza **G7** (desacoplamento de `on_human_end`).

**Invariante preservado (PABX):** o "ramal" (direcionar a um recurso) NÃO vira alvo de roteamento — é um work
item que mora num **pool** (fila) com filtro de claim `assigned_to` + **fallback pro pool** por lease. Fila =
pool+dispatch; ramal = pull item direcionado + overflow. Embrião de transfer-to-agent, sem quebrar o invariante.

**Camadas:**
- **A — fundação ✅ (iniciada):** `dispatch: inline|detached` no `PoolHookEntry` (`@plughub/schemas`), default
  `inline`; guard de parse rejeita `detached` em `on_human_start` (não-finalização). Rebuild: agent-registry +
  skill-flow-service + mcp-server (validam skills/pools).
- **B — pull direcionado ✅ (2026-07-24, smoke 5/5):** `assigned_to` + `fallback_to_pool_after_s` +
  `assigned_at_ms` no work item + claim-eligibility em `Router.work_task_claim` (reusa `dispatch_mode: pull`/
  `work_queue`/`PullInboxPanel`). Wrap-up como consumidor = Camada E (não wirado aqui). Smoke
  `infra/test/smoke_directed_pull.sh`.
- **C — ACW ✅ (2026-07-24, smoke 3/3):** `acw_gate: none|soft|hard` por pool (coluna Prisma
  `20260724000000_pool_acw_gate` + Zod + pools.ts + UI `PoolsPage`/i18n `pools.acw.*`); propaga a routing
  (`PoolConfig`+`kafka_listener`); `get_ready_instances` em `hard` pula instância com marker `:acw_pending`
  (wrap-up detached pendente) — ACW bloqueante enforçado no roteamento, não segurando o contato; inline
  `wrap_up_pending` intacto ("ou mantém inline"). **Produtor do marker `acw_pending` = Camada E.** Smoke
  `infra/test/smoke_acw_gate.sh`.
- **D — bridge ✅ (2026-07-24, smoke 2/2):** `_fire_detached_hook` (workflow webhook fire-and-forget
  `POST {CHANNEL_GATEWAY_URL}/v1/channels/webhook/pool/{id}`, `origin_session_id`+`journey:inherit`+ref de segmento
  no `context`); `_entry_will_dispatch` exclui detached do barrier (`hook_pending`/`posatt`); auto-close
  `_trigger_contact_close` na leva 100% detached de finalização (fecha G1); guardas `_has_customer_hooks` (IA-primário
  + humano) excluem detached; env `CHANNEL_GATEWAY_URL`. **conference-mechanics.md § Histórico → Mudança 25 ✅.**
  Limitações registradas: `post_human`+detached e `segment_wrapup` fanout detached → Camada E. Smoke
  `infra/test/smoke_detached_hook.sh`.
- **E1 — Forma A aposentada ✅ (2026-07-24):** pools `survey_processo_ia`/`survey_collector_ia`/`survey_reconnect_ia`
  + skills `skill_survey_v1`/`skill_survey_nps_v1`/`skill_survey_reconnect_v1` estavam **inertes** (sem hook/trigger
  vivo); removidos do YAML + arquivos. Coleta de survey = NPS inline + J4c collect. *(DB rodando persiste inerte;
  purge opcional via PRUNE — sem DELETE de pool na API.)*
- **Renderer R0 ✅ (2026-07-24, pré-requisito do Path α):** `DialogFormRenderer.tsx` (núcleo genérico) entregue e
  validado — ver CHANGELOG "Renderer genérico de collect-form no Console — R0". Superfície estável que a E2
  consome: claim de workflow suspensa (`session.dialog_form_id`+resume token) → briefing (`session.briefing_session_id`)
  + DialogForm → `workflow_resume` com `payload.answers`. Falta só o conteúdo/plumbing da E2 (abaixo).
- **E2 — wrap-up humano → `detached` (pendente):** `agente_wrapup_v1`/`wrapup_ia` (inline hoje) vira item de pull
  inbox `assigned_to` o humano (fecha G1 do humano). Plumbar `assigned_to` webhook trigger→routing; `wrapup_ia`→
  `dispatch_mode: pull`; skill de wrap-up como workflow pull (DialogForm no claim); gravação do outcome por
  referência (`surveyed_segment_id`); **produtor do marker `acw_pending`** (setar no dispatch detached de pool
  `hard`, limpar na resolução); briefing. NPS síncrono presente fica `inline`. Fecha as limitações da Camada D
  (post_human+detached, segment_wrapup fanout). **Desenho FECHADO** → ADR
  [`docs/adr/adr-wrapup-detached-pull.md`](docs/adr/adr-wrapup-detached-pull.md). **Decisão (2026-07-24): Path α,
  renderer-first** — o renderer é o **tratamento genérico de collect-form no Console** (não "renderer de
  aprovação"; reenquadramento 2026-07-24, ADR §2.1): renderiza o DialogForm de qualquer `collect`/`delegate`
  reivindicado no inbox pull + submit via `workflow_resume`; serve aprovação + wrap-up + survey-no-Console **sem
  skill por caso** (o wrap-up deixa de ter skill próprio). Construir ANTES (arco/sessão dedicado; kickoff do
  núcleo R0 em `docs/product/approval-renderer-kickoff.md`); wrap-up-α por cima. Path β (skill agente menu) **NÃO
  viável no pull-standalone** (humano reivindica → vira primário, sem IA p/ renderizar; só o Console renderiza).
  Comuns aos dois
  (não se perdem na troca): **E2a** (DialogForm
  `dialog_wrapup_v1` + skill) · **E2b** (tool `segment_outcome_record`) · **E2c** (plumbing `assigned_to` no
  `ConversationInboundEvent`) · **E2d** (dispatch pull sintético no bridge) · **E2e** (`acw_pending` set/clear) ·
  **E2f** (analytics: sessão de wrap-up fora da contagem de contato/TMA — **ponto de atenção**) · **E2g** (config
  `wrapup_ia`→pull + smoke E2E).
- **F — validação:** G1 (AHT), atribuição de segmento no relatório, smoke wrap-up na pull inbox (claim direcionado
  + fallback), pool-scoping do survey sem delegate.

Design fechado: [`docs/product/finalization-hooks-detach-and-directed-pull-design.md`](docs/product/finalization-hooks-detach-and-directed-pull-design.md).

### Camada B — pull direcionado ("ramal") — ✅ (2026-07-24, smoke 5/5; ver CHANGELOG)

> **As-built (2026-07-24):** entregue conforme o kickoff abaixo. Toques do que ficou:
> - **Item = dict `contact_data`** (JSON em `{t}:queue_contact:{sid}`) — sem novo schema Zod; campos `assigned_to`/
>   `fallback_to_pool_after_s`/`assigned_at_ms` tipados em `QueuedContact` (routing `models.py`) e na interface
>   `QueueContact` (TS: `lib/work-queue.ts` + `PullInboxPanel`).
> - **Âncora da janela = `assigned_at_ms`**, auto-carimbada no 1º `add_queued_contact` (registry) e **preservada
>   no re-enqueue** (contact_data re-passado verbatim) — a janela conta desde a atribuição, não reinicia a cada
>   requeue. Fallback p/ `queued_at_ms` se ausente.
> - **Gate em `Router.work_task_claim`** (antes do `ZREM`): reservado só é claimable pelo dono OU após transbordo
>   (idade ≥ `fallback_to_pool_after_s`; ausente = permanente). `reason: reserved_to_other`, **logado** (degradação
>   nunca silenciosa). Sem I/O extra — âncora já no pacote lido no passo 2.
> - **Claimant** = `claimant_user_id` explícito (opcional, plumbado em http_api/tools/server) OU derivado de
>   `instance_id` (`human-{userId}`). Retrocompat: sem `assigned_to` = fila compartilhada (comportamento atual).
> - **Inbox:** `PullInboxPanel` esconde reservados-a-outro (até transbordo), rotula "reservado a você"/"transbordado",
>   ordena reservados-a-mim primeiro; i18n `pullInbox.{reservedToYou,overflow}` + `claimReason.reserved_to_other`.
> - **Sem reaper de lease** (o transbordo é por idade do item, não expiração de lease — o kickoff antecipava lease;
>   o modelo real dispensa). Smoke `infra/test/smoke_directed_pull.sh` (userB barrado na janela; dono sempre;
>   userB após transbordo; reserva permanente nunca transborda).
> - **Validado (2026-07-24):** build dos 3 serviços OK + smoke `smoke_directed_pull.sh` 5/5. **Não wirado:**
>   wrap-up como consumidor = Camada E.

**Objetivo:** um work item da fila pull pode ser **reservado** a um recurso específico (`assigned_to`), com
**transbordo pro pool** por lease. Fila = pool; ramal = item direcionado + overflow. Invariante: `assigned_to` é
elegibilidade de claim sobre trabalho *pooled* — **nunca** alvo de roteamento que bypassa o pool.

**Pré-investigação (abrir a sessão lendo isto):** onde vive o work item e o claim hoje —
- Routing Engine: `dispatch_mode: pull` (claim atômico `ZREM`, lease+auto-release). Achar a estrutura do item na
  fila e o handler de claim (`work_queue_claim`?). Ver `packages/routing-engine`.
- Tools MCP `work_queue_*` (mcp-server-plughub) — o preview/claim que a UI consome.
- `PullInboxPanel` (platform-ui) — como lista/filtra os itens.
- ADR `docs/adr/adr-human-approval-workflow-step.md` (a aprovação já é o 1º uso do pull; reusar o mesmo item).

**Sub-etapas:**
1. **Schema do work item:** `assigned_to?: string` (user_id preferido) + `fallback_to_pool_after_s?: number`
   (default: sem reserva). Onde o item é modelado (schemas / routing). Retrocompat: ausência = fila compartilhada
   (comportamento atual).
2. **Claim-eligibility no Routing Engine:** ao reivindicar, um item com `assigned_to` só é elegível se
   `claimant.user_id == assigned_to` **OU** a idade do item ≥ `fallback_to_pool_after_s` (aí vira claimable por
   qualquer um do pool/grupo). Sem `assigned_to` = elegível a todos (hoje). Cuidar do hot path (barato, sem
   query extra — a idade já está no ZSET score).
3. **Fallback por lease:** o transbordo é do **direcionamento**, não do item (o item continua na fila; só deixa
   de ser exclusivo). Nada de mover de fila.
4. **Tools MCP `work_queue_*`:** expor `assigned_to`/estado ("reservado a você" × "transbordado") no preview.
5. **`PullInboxPanel`:** mostrar itens reservados ao usuário + rótulo de transbordo; ordenar reservados primeiro.
6. **Smoke:** enfileira item com `assigned_to=userA` + `fallback` curto → userB NÃO vê antes do fallback; após,
   userB vê; userA vê sempre. `infra/test/smoke_directed_pull.sh`.

**Não fazer nesta camada:** o wrap-up ainda não é wirado como consumidor (isso é a Camada E, depois de a B e a D
existirem); aqui só o primitivo genérico de pull direcionado. E **nunca** transformar `assigned_to` em alvo de
roteamento (bypass do pool) — é filtro de claim com fallback.

---

## Histórico de contatos do cliente — backlog pós-H5

> O arco Customer History está **completo no v1** (H1–H5 + C1a/C1b ✅ — ver `CHANGELOG.md` e
> `docs/arcos/customer-contact-history.md` §9). Resta:
- **Busca full-text `GIN(tsvector)` (escala)** *(adiado no H5)* — a busca de mensagens (H2) usa hoje
  ClickHouse substring (`positionCaseInsensitiveUTF8`), suficiente no volume atual. Para escala, migrar
  para full-text tokenizado real (índice `GIN(tsvector)` no Postgres `session_stream_events`, ou skip-index
  ClickHouse). É otimização, não correção — a busca funciona. Gatilho: latência/volume medidos.
- **H4-survey** *(bloqueado)* — origem+resultado do survey no **briefing de retorno** (`customer-surveys.md`
  §19), que ainda não existe.

---

## Scheduler / Outbound — resíduos *(arco Scheduler 1–3 ✅ e arco Outbound 1–5 ✅; histórico no CHANGELOG)*

- **Fase 3b do Outbound — ⚠️ a validar:** opt-out global `do_not_contact` no cadastro (identity), veto de
  maior precedência no eligibility salvo `transactional`; `mailing_unsubscribe scope=global` escreve o
  atributo. O smoke `infra/test/smoke_outbound_fase3b.sh` está escrito mas **não foi validado**.
- **Refinamentos do Outbound 5b (backlog):** `responded` por-delivery (submit → `campaign_delivery_result`);
  skill de processo que **auto-alimenta a mailing** no `complete` (journey_complete real — hoje é seed direto).

### Migração dos timers legados *(follow-up — antigo "Scheduler central de timers")*

Consolidar os timers espalhados (timeout de suspend/delegate no channel-gateway,
`_hook_timeout_guard` no bridge, timeout de `collect`) no substrato do scheduler-api:
sorted-set de deadlines (`ZADD`/`ZRANGEBYSCORE`) + poller único + evento `timer.fired`
com os donos reagindo; calendar-api permanece o engine de prazo (calcula o *quando*, não
dispara). Primeiro corte funcional já existe (`run_timeout_scanner` no channel-gateway).
Decisão e mecanismo em [`docs/adr/adr-timer-scheduler.md`](docs/adr/adr-timer-scheduler.md).

---

## Agent-registry — unificar binding skill↔pool (2→1) *(proposta — concern do registry)*

Origem: discussão do doc de avaliação (`docs/arcos/arc-evaluation-metrics-methodology.md` §IV.3),
scoped-out de lá por ser refactor do agent-registry, não de avaliação.

**Achado (revisado 2026-07-20):** a associação skill↔pool aparecia em **três** lugares no `schema.prisma`, mas
o `SkillVersionSlot.pool_ids` (3-slot POR skill) **já foi aposentado** (Skill Versioning Fase E, 2026-06-24 — o
modelo virou pool-cêntrico; `db push` dropou `skill_version_slots`). Hoje sobram **dois**: `PoolSkillSlot`
(slot do pool — binding vivo, autoritativo) e `SkillDeployment.pool_ids` (histórico de deploy). Risco de
divergência entre eles.

**Alvo**: `PoolSkillSlot` como relação **autoritativa** do binding atual + o histórico como **append-log** das
mudanças de slot (o `SkillDeployment` deixaria de precisar do próprio `pool_ids`, derivável do contexto).
**Pré-trabalho**: auditar os readers de `pool_ids` (routing/alocação no caminho quente, RegistrySyncer, lente
deploy do Arc 6 Fase 2, `GET /v1/pools/:id/deployments`) antes de dropar o campo. Escopo menor do que o "3→1"
original sugeria.

---

## Skill hot-reload via YAML em disco sem restart *(deferred — dev/demo only)*

**Fluxo editor → deploy já funciona**: `POST /v1/skills/:id/deploy` → `publishRegistryChanged` → bridge invalida `_skill_flow_cache` → próxima execução busca conteúdo atualizado do agent-registry. Nenhuma mudança necessária para este caminho.

**Gap**: edição direta de arquivo YAML em disco (dev/demo) ainda requer `restart orchestrator-bridge` para o RegistrySyncer re-ler e fazer PUT para o agent-registry. A solução correta é um endpoint `POST /admin/skills/sync` (ou handler de `registry.changed` com `source: disk`) no bridge — chama `RegistrySyncer._sync_skills()` → PUT → `registry.changed` → cache invalidado. Deve ser acionado pelo processo de deploy YAML (CI/CD, script), não pelo editor.

---

## Arc 19 — cleanup residual de infra *(arco concluído 2026-05-28; histórico no CHANGELOG)*

Remover o tópico `workflow.events` do Kafka e arquivar o package `skill-flow-worker`.

---

## Usage Metering — Channel Gateway Adapters *(deferred)*

Funções em `usage_emitter.py` implementadas, mas os adapters de canal ainda não as chamam. Será wired quando cada adapter for criado:

- `whatsapp_conversations` — adapter WhatsApp
- `voice_minutes` — adapter WebRTC/Voice
- `sms_segments` — adapter SMS
- `email_messages` — adapter Email

---

## Pricing Module — Integração metering × pricing *(deferred)*

Módulo que lê contadores de `usage.events` no Redis/ClickHouse, aplica planos configurados no Config API e escreve `{tenant}:quota:limit:*` no Redis. Metering registra mas pricing não consome ainda.

---

## Masking — Bloco 3: Channel Gateway TTS *(deferred até implementação de voz)*

Quando qualquer adapter de voz/TTS for criado, deve consultar `rule.{category}.display_voice` no namespace `masking` do Config API antes de passar texto ao sintetizador. Comportamentos: `silence` (pula o valor), `beep` (tom de beep), `speak_placeholder` (fala "valor mascarado"). Não implementar antes de definir qual engine TTS será usada.

---

## Audit LGPD — Fases Pendentes

Fase 1 concluída — ver CHANGELOG 2026-05-14 e `docs/arcos/audit-lgpd.md`.

- **Fase 2** — `original_content` desmascarado: endpoint de resolução de tokens em Core → analytics-api expõe conteúdo original ao DPO. Requer endpoint batch de resolução de tokens no Core.
- **Fase 3** — `user_access` logs: topic Kafka `user_access.events` em auth-api + tabela ClickHouse + tab ativo em AuditPage.
- **Fase 4** — SAR/Erasure pipeline: CRUD de Subject Access Requests + pseudonimização em `sessions_stream` + anonimização ClickHouse (TTL/partition replacement).
- **Fase 5** — `config_snapshot`: leitura read-only do namespace `masking` do Config API para verificação DPO.

---

## Business in Any Media — processo channel-abstract + framework de loja *(proposta — não implementado)*

Reposicionamento process-centric ("nunca perca um negócio por causa de canal") + framework de comércio conversacional sobre o modelo de 3 níveis (a = fluxo negocial channel-abstract; b = acesso a canais; c = agente de I/O). Especificações em `docs/product/`:

- **Arquitetura-alvo (3 níveis)** — [`docs/product/business-in-any-media-arquitetura-alvo.md`](docs/product/business-in-any-media-arquitetura-alvo.md) + diagrama `business-in-any-media-3-niveis.svg`. Define as 3 camadas, contratos, e o que falta construir no nível (b).
- **Resolvedor de identidade + cadastro (nível b)** — [`docs/product/identity-resolver-nivel-b-spec.md`](docs/product/identity-resolver-nivel-b-spec.md) + sequência `identity-resolver-sequencia.mermaid`. Generaliza o `pending_workflow` existente: cadastro nativo (`customer_id` canônico, dois andares Redis/PG), índice multi-âncora hasheado, retomada cross-canal. Governança: plataforma não é autoridade de identidade/pagamento; só chaves mascaradas; uso interno.
- **Contrato delegate por pool (a→b)** — [`docs/product/delegate-contrato-por-pool-spec.md`](docs/product/delegate-contrato-por-pool-spec.md). Delegação por pool (não skill); decidido alinhar `task.target` a pool; 1 skill publicada por pool; gate de identificação como lógica de fluxo (não campo de schema).
- **Commerce-cards (nível c)** — [`docs/product/commerce-cards-nivel-c-spec.md`](docs/product/commerce-cards-nivel-c-spec.md). `component` tipado em `notify`/`menu` (product_card/carousel/cart/checkout/order_status), render nativo por canal; checkout com masked input + repasse ao PSP; novas ChannelCapability `rich_card`/`carousel`.
- **Fluxo de intake (nível c)** — [`docs/product/intake-flow-nivel-c-spec.md`](docs/product/intake-flow-nivel-c-spec.md). Generaliza o `agente_portabilidade_intake_v1`: resolve identidade (origem do canal) → checa pendência → oferta de retomada → roteia intenção; gate de identificação flow-wired.

Descritivo técnico-funcional consolidado (com a seção de roadmap §20.7): [`docs/product/plughub-descritivo-tecnico-funcional.md`](docs/product/plughub-descritivo-tecnico-funcional.md) (+ `.html` print-ready) — **manter atualizado conforme cada item for implementado**.

**Base que já existe** (não confundir com o gap): workflow + canais + suspend/resume + retomada via `pending_workflow` + masking. **A construir**: cadastro de identidade completo, commerce-cards, gate, e o nível (b) como camada de primeira classe.

---

## Fila de trabalho humano / dispatch pull + inbox no Console — resíduos pós-v1 *(v1 concluído 2026-07-17; histórico no CHANGELOG)*

**Resta (A6 — pós-v1, ADR §6 `adr-human-approval-workflow-step.md`):** quatro-olhos (2 aprovadores);
reatribuição por supervisor (= conferência padrão); notificações/SLA na inbox; **rework rate**
(Bancada/Arc 6); **auto-aprovação** (pool IA). **Não-objetivos v1 (adiados por decisão):** omnichannel/
Modo B (D6); weight-ordering (F6); **promote real** (invoke de deploy no `efetuar_promocao`, hoje
`complete`). **Follow-ups menores (CHANGELOG A5):** Context/History trazendo a journey do workflow por
`root_session_id` (aprovação raramente tem `customer_id`); gate de servibilidade do pool de aprovação
pelo ABAC `approvals` (fechar o claim genérico); refresh imediato do inbox pós-release.

**Diferido desde a F1.3** (spec "sem sweep dedicado"): renovação da lease de claim por heartbeat +
sweeper de "conectado-mas-ocioso". Hoje o auto-release do pull é emergente (desconexão → bridge
re-roteia → `route()` parqueia e limpa a lease); a inbox sinaliza melhor que um sweep.

## Frente 2 — Avaliação campaign-driven — resíduos *(pipeline S1/S2.1/S2.Q1/S2.2 ✅ e lente `deploy` P2+P3 ✅; histórico no CHANGELOG)*

Avaliação é **sempre dirigida por campanha** (janela = `evaluation_calendar_id`, throttle = `avaliacao_ia.max_concurrent_sessions`).
Pipeline validado E2E com avaliador real (2026-06-17) e lente `deploy` ancorada no pool (2026-06-20).
Specs: `docs/product/arc6-phase2-deploy-observability-spec.md`, `docs/product/calendar-consolidation-and-trigger.md`.

**Diferidos por decisão do usuário (reabrir só se observabilidade por deploy/versão virar requisito)**
- **P4 (núcleo §4.1/D4)** — série por **epoch/versão**: eixo X = versões do pool (`[deploy N, deploy N+1)`), ponto = qualidade média da versão, N por versão. Hoje o eixo é tempo + `deploy_markers` (leitura de "v1 vs v2" ainda manual). Seed: `infra/test/seed_deploy_lens_demo.sh`.
- **Ruído herdado do board na lente `deploy` (§4.5/D3)** — média/multi-seleção fazem pouco sentido numa lente de versões; avaliar remover/ocultar e focar single-skill quando o epoch entrar.
- **Markers exigem `flow_id == skill_id` (§8)** — no demo `sac_ia` (agent_type_id) ≠ `skill_atendimento_sac_v1`; só alinha quando o `flow_id` carrega o skill_id real *(verificar se o re-ancoramento por pool do P3 já tornou isso irrelevante)*.
- **Capacidades perdidas com a remoção das abas Trend/Comparison** (não existem no bench): significância estatística (N<30), comparação de **períodos arbitrários A vs B**, overlay multi-métrica. Se voltarem, entram como modo "comparar fatias/deploy" no bench.
- `TimeseriesView`/`ComparisonView` continuam no repo como **código morto** (não removidos no cleanup).

**Nits do bench (diferidos, não fechados)**
- **Quality score geral diluído** — KPI "Quality score 0.00 (N evals)" do drill-down e a curva da lente `quality` saem baixos/zero enquanto o radar de dimensões está correto. Hipótese original (zero-fill por sessão) **refutada** por leitura de `analytics-api/reports_query.py`. Achado real não confirmado como causa: `_compare_quality_lens` filtra a janela pelo `timestamp` da avaliação, enquanto `_fetch_agents_cross` filtra por `attr.session_started_at` — mas a mesma divergência existe em `_compare_quality_criteria_lens` (que está correto). **Requer reprodução ao vivo com dado real** (range + Quality/N evals/Sessions do drill-down vs. a linha do mesmo agente na tabela) antes de qualquer fix.
- **Janela/período** — confirmar se KPI, lente e tabela de dimensão usam períodos diferentes no mesmo request (não confirmado); considerar default próprio do bench (hoje reusa `DEFAULT_FILTERS` de `contacts/types.ts`, 7 dias, alinhado com `_default_from`/`_default_to`).
- **NPS por agente parece alto** (pequeno).

**Contrato de avaliação / robustez**
- **Unificação do contrato prompt×schema (desenhada, não implementada)** — prompt `evaluation_rubric_v3` é fixo e deveria derivar do `EvaluationForm`; `_format_schema` do ai-gateway é **lossy** (descarta `items`/`properties`/`description`/`nullable`; `OutputFieldSchema` nem os modela); alvo = YAML `output_schema` ≡ Zod do `evaluation_submit`, permitindo **remover os shims de compat**. *(O nit específico da perda de `justification`/evidência foi fechado no T9-C.fix2.)*
- **Sessão sem dados** — avaliar sessão "magra" ainda falha duro no `evaluation_submit` (`overall_score=null` × `composite_score: number` obrigatório). Contrato escolhido: avaliador detecta sessão sem conteúdo e marca a instance `skipped`/`error` com motivo, **sem** chamar submit; pode exigir `skipped` no enum (hoje só `error`).

**Pipeline / superfícies faltantes**
- **S2.3** — dispatcher automático drenando instances `scheduled` das campanhas com `evaluation_calendar_id` aberto (calendar-api `is_open`), respeitando a capacidade do pool avaliador *(verificar sobreposição com o dispatcher windowed T15 já existente)*.
- **Surface de instances `scheduled`** — hoje Avaliações mostra só resultados; operador não tem visão da fila agendada.
- **CampaignsPage** — sem editar/deletar campanha (só create + pause/resume), embora a API já tenha `CampaignUpdate`/PUT.
- **i18n** — chaves `campaigns.seedSynthetic*` (en/pt-BR) nunca adicionadas; e rebuild do `platform-ui` para as chaves Arc 13 (`contest.*`/`review.*`) entrarem em produção *(verificar se já rebuildado)*.
- **Curation/Calibration (Arc 13 Fase H)** — telas existem mas nunca validadas com dado real; exercitar o **Fluxo 2** (curadoria → `calibration_signal` → CalibrationNote → KB), que só rodou via seeder.
- **Fila de revisão do supervisor** ("Awaiting my action", depende de `available_actions`) — confirmar se existe.

**Auth / limpeza**
- **G-PROBE, perna agente/sistema** — `submit_pre_review`, `seed/flush-synthetic`, `create/update/delete_sampling_rule`, `publish_calibration_note` seguem **header-only** (`X-Tenant-ID`/`X-User-ID`). Decisão 2026-07-01: **não** usar credencial de serviço ad-hoc; gatear por `principal_id` do **Agent Principal** (F1–F4) quando existir. Perna humana `curar` ✅ resolvida. Ver seção `## G-PROBE` própria neste arquivo.
- **G-S2.4 aposentado (decisão 2026-06-25)** — resta o *follow-up opcional* de **remoção física da cola morta**: consumer reativo `workflow.events` na evaluation-api, coluna/seletor `review_workflow_skill_id`, skills `skill_revisao_*`/`agente_revisor_v1` e o cenário e2e 28. Slice próprio (raio de teste no 28).

**Achados pré-existentes (não causados pela F1.0)**
- **A — specialist-return (pré-requisito/núcleo da F4)**: conference specialist que termina com `escalate` re-roteia o CONTATO em vez de **voltar ao chamador** (ex.: `agente_auth_form_v1.yaml` → `retencao_humano` → fila, com mensagem de fila espúria). Fix preferido: **engine** — flow em modo conference specialist trata `escalate`/`complete` como retorno-ao-chamador devolvendo outcome. Sub-arco próprio.
- **B — multi-sessão humana no push**: humano servindo entra `state="busy"` e `get_ready_instances` exige `state=="ready"` → mesmo sob capacidade (`max_concurrent=3`, vindo da URL do WS do Console — `mcp-server` server.ts:2147 — não do `auth`) não recebe 2º contato via push. Pull (F1) endereça; decisão pendente: o push também deveria manter `ready` enquanto sob capacidade? Medir ao vivo antes de atacar.

---

## Record/Replay Harness — gravação/replay em todas as costuras *(proposta — não implementado)*

Visão + spec em [`docs/product/record-replay-harness-spec.md`](docs/product/record-replay-harness-spec.md). Generaliza o Session Replayer (que hoje replaya só o stream da sessão, para avaliação) num harness "VCR" em todas as costuras (channel-gateway, AI Gateway, MCP, Kafka) — cada costura como **driver** (injeta inputs gravados) ou **mock** (devolve outputs gravados), com timings.

**Base que já existe**: `session-replayer` (persister/hydrator/replayer/comparator), `ComparisonReport` (Jaccard + deltas), `delta_ms`/`speed_factor`, Kafka como log, harness `e2e-tests`. **A construir**: captura full-fidelity de payload em MCP/AI Gateway (hoje `mcp.audit` é só metadado), clock/seed injetável (determinismo), harness multi-costura, gravação seletiva (golden/amostrada/on-demand) com masking, e o **gate de promoção** consumindo o `ComparisonReport` como critério objetivo. Aplicações: regressão determinística, repro de bug, simulação de carga, datasets de avaliação.

---

---

