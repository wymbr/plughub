# TODO — PlugHub Itens Pendentes

> Itens genuinamente não implementados. Histórico de implementações concluídas em `CHANGELOG.md`.

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
| J5a ✅ (2026-07-14, ver CHANGELOG) | `@ctx.journey.*` **vivo** (bridge resolve a raiz canônica → `journey_id` no `/execute` → `journeyId` no engine; TTL próprio de 30d) + **merge acíclico por construção** (aresta raiz→raiz via mapa de aliases no Redis; idade vem do stream canônico, não do `meta` que só o webchat escreve) + 12 testes do `journey_merge`. Validado E2E com escritor e leitor em sessões diferentes da mesma journey, com controle negativo. | J3, J4 |
| J5b | i18n dos **enums** na Vista Processos (`status`, `business_outcome`, `outcome`, `channels` são renderizados crus; o dicionário já existe em `workflows.json`). Remover os steps de demo do `@ctx.journey.*` quando houver consumidor real. | J5a |
| — (app-wide, fora do Journey) | **Guard de rota ABAC**: nenhuma página de `analise/` tem gate próprio — só o Sidebar. Deep-link contorna a UI (o dado segue filtrado por `accessible_pools` no backend). Consertar só a de Journeys seria cosmético; é um item do app. | — |

J1+J2 já entregam journey por proveniência (o essencial do D1); J3 adiciona o que a proveniência não dá.

**Decisões resolvidas (design §9):** sobrevivente = mais antiga; cache eventualmente consistente; manter os dois
campos; propagação = do chamador; `@ctx.journey.*` reaceso; filtro "significativa" = **default de UX** (as vistas
Sessions `session→[segments]` e Processos `journey→[sessions→[segments]]` diferem por profundidade de drill — não
redundância).

**Riscos:** refresh de cache re-emite N linhas em merges de journeys grandes (raro; medir; cache lazy se preciso);
`/reports/journeys` carrega union-find por request (alias table pequena; materializar em `journey_id` se medir exigir).

**Docs:** design `docs/product/journey-retorno-modelo-3-niveis-design.md` · spec
`docs/product/journey-3-niveis-implementation-spec.md` · diagrama `docs/product/journey-3-cenarios-unionfind.svg`.

---

## ✅ Analytics — `contact_closed` perdido por corrida no ReplacingMergeTree *(bug CORRIGIDO 2026-07-13)*

> **Corrigido:** `sessions` migrada para `ReplacingMergeTree(row_version)`, com
> `row_version` = timestamp do **evento** (não da inserção). O `contact_closed` carrega `ended_at` —
> por definição o instante final da sessão — então vence sempre, independente da ordem de inserção
> entre tópicos. Migração faz **rebuild** (ClickHouse não faz ALTER de engine) e **repara o histórico**
> via `DEFAULT coalesce(closed_at, opened_at)`. Validado: E2E novo fecha as 3 sessões (`open_count: 0`).
> **Polimento pendente:** sessões que passaram por suspend/resume fecham com `outcome: suspended`
> em vez de `resolved` (o `status` fecha correto; só o `outcome` do evento de close está errado).

**Sintoma:** sessões fechadas pelo bridge continuam `active` no ClickHouse (`closed_at NULL`),
corrompendo `open_count`, TMA, SLA e duração. Reproduzido no E2E do J4c: das 3 sessões da journey,
o bridge publicou `contact_closed` para todas (logs comprovam), mas nenhuma fechou em `sessions`.

**Causa raiz.** `clickhouse.py` (§Design decisions) assume: *"No explicit version column […]
Deduplication keeps the LAST inserted row per ORDER BY key. **Kafka ordering** [garante a ordem]."*
Mas o consumer lê de **múltiplos tópicos** (`conversations.inbound`, `conversations.routed`,
`conversations.events`, …) e o Kafka só garante ordem **dentro de uma partição**, não **entre tópicos**.
Então uma linha `routed` (status=active) inserida **depois** do `contact_closed` **vence a
deduplicação** e apaga o fechamento:

```
16:56:29,365  conversations.routed   (re-alocação do resume) → status=active
16:56:29,379  conversations.events   (contact_closed)        → status=closed   ← perdido
```

Evidência: `d33d4a89` tem as DUAS linhas gravadas; `SELECT … FINAL` devolve a `active`.
`939154af` e a raiz perderam a linha de close no merge (sobrou só a `active`, com `opened_at`
carimbado no instante do **resume**).

**Não é bug do J4c** — é pré-existente. O J4c apenas o tornou reproduzível: o `collect` faz
resume→close numa rajada de ~14ms, enquanto antes o intervalo entre roteamento e fechamento era
grande o bastante para mascarar a corrida. **Qualquer** sessão com re-roteamento próximo do
fechamento (transfer, re-queue, resume) está exposta.

**Correção:** coluna de versão não-nula (`row_version DateTime64(3)` = timestamp do evento) +
`ReplacingMergeTree(row_version)` → o evento mais recente vence deterministicamente, independente da
ordem de inserção entre tópicos. Migração de schema + carimbo do `row_version` em todos os writers de
`sessions` (`parse_inbound`, `parse_routed`, `parse_queued`, `parse_conversations_event`,
`session_suspended`). Revisar os workarounds de `COALESCE`/`channel=""` que existem hoje só para
mitigar esse mesmo problema.

---

### Survey — nomeação por PAPEL + grão em config (S1 ✅ / S2 pendente, 2026-07-14)

**Decisão de eixo.** Os skills de survey são nomeados pelo **papel**, e o **grão**
(journey/session/segment) é **parâmetro de deploy**, não uma família de skills. Grão e papel
são eixos **ortogonais**: nomear pelo grão duplicaria a cadeia inteira (3 papéis × N grãos)
para diferenças que são de config — e deixaria ambíguo qual dos dois skills de grão *journey*
é o gatilho e qual é o workflow (foi exatamente a colisão `journey_survey` × `survey_journey`
que custou um diagnóstico errado no J4c). O que o operador vê como "survey de journey" vs
"survey de sessão" é o **nome do pool**, não o `skill_id`.

**S1 ✅ — rename puro (sem mudança de comportamento):**

| papel | skill | era |
|---|---|---|
| **gatilho** — consome o hook de fim, decide *se* pesquisa, dispara o workflow | `skill_survey_trigger_v1` | `skill_journey_survey_v1` |
| **workflow** — faz o `collect`, suspende esperando o clique | `skill_survey_outbound_v1` | `skill_survey_journey_v1` |
| **runner** — renderiza o DialogForm ao vivo, grava o sinal, retoma o workflow | `skill_survey_runner_v1` | `skill_survey_collect_v1` |

O gatilho ficou agnóstico de grão (testa `process_outcome` **ou** `contact_outcome` — só uma
das tags existe por disparo; um `field` vindo de `$.config` exigiria dupla indireção, que o
engine não faz).

**Custo operacional do rename (os pools são DB-owned, criados pela UI — não há bloco `deploy:`
em `infra/registry`):** `skill_id` é a identidade, então os YAMLs novos **semeiam skills novos**
e os slots dos pools continuam apontando para os antigos. Exige, por pool: `set-next` + `promote`
com o id novo, atualizar o `webhook_skill_id` do pool `survey_journey_wf`, e apagar os 3 skills
órfãos.

**S2 ✅ — grão vira config (não skill).** O runner tinha `grain: "journey"` **hardcoded** — o único
ponto não-genérico de um skill que se descreve como domain-blind. Agora:

- `CollectStepSchema.signal_grain` (união `enum | ref`, reusa `SignalGrainSchema` de `survey.ts` —
  sem redefinir o enum), propagado por `persistCollect` → skill-flow-service → channel-gateway.
- `skill_survey_outbound_v1` declara `signal_grain: "$.config.grain"` + `config_param` `grain`.
- **A tradução grão→chave é do N2** (`_resolve_signal_target`), porque só o chamador tem o contexto:
  `journey` → raiz canônica; `session` → `session.origin_session_id` do chamador; `workflow` → a
  própria sessão. Isso NÃO é regra de negócio no core — é a definição de o que cada grão *significa*
  no modelo de sessão (mesma natureza de `root_session_id`). O que é negócio (*qual* grão) fica no
  `config_json` do deploy.
- **`segment` é rejeitado alto**: `survey_record` exige `segment_id`, que o workflow outbound não
  conhece (foi disparado por um hook de fim de sessão, não de segmento). Gravar o sinal na chave
  errada contamina o relatório em silêncio — melhor falhar.
- O gateway semeia `session.survey_grain` + `session.survey_target_id` no ctx da sessão de survey; o
  runner lê ambos → **zero grão e zero métrica no skill**. Survey de sessão = novo **deploy**.
- Retrocompat: pending sem os campos → default `journey`/raiz, que é o que faziam hardcoded.

**Armadilha de build:** `CollectStepSchema` mudou ⇒ **agent-registry, skill-flow-service e o engine
rebuildam juntos**. `z.object()` **descarta** chave desconhecida em silêncio — um agent-registry
velho gravaria o flow **sem** `signal_grain`, sem erro nenhum.

**Armadilha de DX (custo do D2, achada no S2):** com *seed-if-absent*, **editar um `skill_*.yaml` já
semeado NÃO propaga** — o DB é a verdade e o syncer não sobrescreve (é justamente o que faz a edição
da UI sobreviver ao restart). O loop "edito o arquivo, reinicio o bridge" morreu junto. O caminho é o
`REGISTRY_SYNC_RECONCILE=true`, agora exposto no `docker-compose.demo.yml`:
`REGISTRY_SYNC_RECONCILE=true docker compose -f docker-compose.demo.yml up -d orchestrator-bridge`
(e voltar ao default depois). Vale um aviso no log quando um YAML difere do DB semeado — hoje o
silêncio faz parecer que a edição funcionou.

**S3 ✅ — survey de segmento (grão `segment`).** Decisão de produto (usuário): o gatilho é o de
**fim de contato** (não `on_human_end`, que dispararia a pesquisa com o contato ainda aberto em caso de
transferência), e o **segmento é configurável**, com default = **último segmento primary** (quem fechou
o atendimento).

Princípio: **QUAL segmento pesquisar é política, e política mora no skill.** A plataforma só **expõe os
fatos**; quem escolhe é o gatilho.
- **bridge** (`_write_pre_hook_context`): carimba `session.last_primary_segment_id` +
  `session.last_primary_agent_key` (`user_login` humano | `agent_type_id` IA). São fatos de **sessão**
  (existe exatamente um "último"), então não ferem o ADR de identidade — o que colapsa em multi-humano
  é um fato *por-segmento* num campo global (ex.: "qual humano este wrap-up serve": há N wrap-ups).
- **gatilho** (`skill_survey_trigger_v1`): propaga a escolha via `context_json` do `workflow_trigger`
  (string JSON com `{{...}}`). Trocar o critério = editar **duas linhas do YAML**, sem tocar em
  plataforma.
- **N2**: `segment` resolve a mesma chave que `session` (a sessão que CONTÉM o segmento); o que os
  separa é o `segment_id` + `agent_key` que acompanham o sinal. Falha alto se o gatilho não escolheu.
- **runner**: repassa `segment_id`/`agent_key` — continua sem saber de grão.
- **schema**: `SurveyRecordInputSchema.segment_id`/`agent_key` viraram **`.nullish()`** — o runner é UM
  só para todos os grãos, então sempre passa os campos, e num grão ≠ segment a ref resolve para `null`.
  `.optional()` aceita *ausente*, não `null`, e rejeitaria a chamada inteira: **é o mesmo bug do
  `survey_link_create` do J4b** (`customer_key: z.string().default("")` vs `null`), que falhava em
  silêncio porque a tool devolve `isError` e o `invoke` segue por `on_failure` sem log.
- **guard**: `_read_ctx_tag` normaliza `"null"`/`"undefined"`/`""` → `None`. Uma tag semeada por
  `context_json` cujo ref não resolveu vira a **string** `"null"`, que é truthy em Python e passaria por
  qualquer `if not value`.

**Aviso de drift no seed-if-absent ✅** (custo do D2, achado no S2): o syncer agora **avisa** quando o
YAML diverge da definição no DB e não vai ser aplicado, em vez de pular em silêncio. Compara por
**contenção** (o YAML está contido no DB?), não por igualdade — o agent-registry grava o flow **depois
dos defaults do Zod**, então o DB legitimamente tem mais chaves; igualdade acusaria drift em todo skill
a cada boot, que é o falso positivo do D4 com outra roupa ("foi escrito" ≠ "mudou").

**S4 ✅ — o POOL é a unidade endereçável, nunca o `skill_id`** (invariante no CLAUDE.md).

Motivação do usuário: *"hooks em geral devem acionar somente pools e nunca skill_id, justamente por causa
da configuração dos skills, que precisa estar num único local para não gerar dúvida sobre o que está
rodando e com que configuração."* Correto — e conserta um **vazamento do modelo**, não só o survey: o
`workflow_trigger` por `skill_id` sempre foi a exceção ao Arc 19 ("pool webhook = endpoint"), e só
funcionava porque havia um pool por skill.

- **`workflow_trigger`** ganhou `pool_id` (canônico; vence sobre `skill_id`, que vira legado) → nova rota
  `POST /v1/channels/webhook/pool/{pool_id}` na channel-gateway (declarada antes da greedy `/{skill_id}`).
  O mecanismo já existia — o `handle_trigger` **já aceitava `pool_id`** e o evento inbound já o honrava
  (foi assim que fizemos o slug→pool). Faltava só a tool poder endereçar por pool.
- **Guard no router**: `skill_id` que casa **>1** pool webhook = endereço **ambíguo** → `pools = []` +
  ERROR explicando. Antes ele escolhia por score, em silêncio — rodaria um deploy que o chamador não
  pediu. `skill_id` só é endereço enquanto **um** pool o declara.
- **`skill_survey_trigger_v1`** ganhou `config_param` **`outbound_pool`**: o operador escolhe o pool de
  survey na tela de Deploy do gatilho, e **o grão vem junto** — porque cada pool outbound é um deploy do
  MESMO `skill_survey_outbound_v1` com `grain` diferente. Some a assimetria "grão numa tela, política em
  outra": tudo que responde *"como pesquisamos"* fica no deploy do gatilho.
- **Demo**: três pools outbound (`survey_journey_wf`, `survey_session_wf`, `survey_segment_wf`).

**Nota:** a política de escolha do segmento continua no YAML do gatilho (é política, e política mora no
skill). Torná-la `config_param` exigiria dupla indireção (`@ctx.{{$.config.tag}}`), que o engine não faz.

---

### Bug: escrita PARCIAL em `sessions` apaga a identidade da sessão (achado 2026-07-13) — corrigido, a validar

**Sintoma:** a sessão do workflow (`37324d63`, `status: suspended`) ficou com `pool_id` **vazio** no ClickHouse,
embora o roteamento tivesse registrado `pool=survey_journey_wf`.

**Causa (classe de bug, não caso isolado).** `sessions` é `ReplacingMergeTree` de **linha inteira** — a versão
mais nova SUBSTITUI a anterior, sem merge por coluna. Mas **todo** escritor é parcial: `inbound` sabe
canal/cliente (ainda não roteou → sem pool); `routed`/`queued` sabem o pool (routing não conhece canal →
escrevem `channel=''`); `session_suspended` sabia só o `status`. Cada escrita **apaga** o que a anterior sabia.
Ninguém percebeu porque o `contact_closed` monta a linha **completa** (relê o `session:{id}:meta`) e **cura
tudo no fim** — então o dano só existia entre o roteamento e o fechamento. Bastou existir uma sessão que
**não fecha** (workflow `suspended` esperando um `collect` por até 48h) para o defeito ficar permanente e
visível. `opened_at` sofria o mesmo: `routed`/`suspended` carimbavam o instante do próprio evento,
**adiantando o nascimento** da sessão e encurtando TMA/duração.

**Fix (dois níveis):**
- **bridge** — o evento `session_suspended` passou a repetir a identidade (`pool_id`, `channel`, `customer_id`,
  `opened_at` do meta), como o `contact_closed` já fazia.
- **analytics-api (estrutural)** — o `_channel_cache` (que já existia, mas só para `channel`+`origin` e só no
  tópico `routed`) virou **`_session_identity_cache`**: aprende a identidade de qualquer linha que a traga e
  reinjeta nas que não trazem, em **todos** os tópicos. Só preenche vazio (nunca sobrescreve o que o produtor
  sabe); `opened_at` mantém sempre o mais antigo conhecido. Campos de **estado** (`status`, `outcome`,
  `closed_at`) ficam de fora de propósito — a linha nova tem o direito de sobrescrevê-los.

**Terceiro primo do mesmo dia:** o bug de `row_version` (linha de close fisicamente apagada no merge) e o de
hooks `side=agent` (linha de close nunca emitida). Os três produzem métricas erradas em `sessions`; os três
vinham de tratar um `ReplacingMergeTree` como se fizesse merge por coluna.

---

### Bug: sessão com hook `side=agent` NUNCA fecha a camada 1 (achado 2026-07-13) — corrigido, a validar

**Sintoma:** a sessão do processo (N3, pool webhook) fica `closed_at NULL` / `status NULL` para sempre no
ClickHouse. Reproduzido no E2E do item 2: das 3 sessões da journey, as duas sem hook fecharam; a raiz (que
dispara `on_process_end`) não.

**Causa.** Com hooks, o teardown é **diferido** e cada camada tem gatilho próprio: a camada 1
(`_close_contact_layer` → publica `contact_closed`) dispara quando `posatt:customer_active` zera; a camada 3
(`_destroy_conference`) quando `posatt:active` zera. Mas `posatt:customer_active` só é **incrementado** por
hooks `side=customer`. Um pool cujos hooks são todos `side=agent` — que é o caso do `on_process_end`, porque o
survey sai por veículo **outbound**, não inline — nunca incrementa o contador, logo ele nunca zera, logo a
camada 1 **nunca fecha**. O caminho humano (`agent_done` → `on_human_end`) já tinha a guarda
(`if not _has_customer_hooks: _close_contact_layer(...)`); o ramo do **AI primary** não.

**Agravante:** `_destroy_conference` grava `session:{id}:close_fired`, e o `session_watchdog` usa a ausência
dessa chave como critério de órfã — então a rede de segurança **também** não pega. A sessão fica aberta
indefinidamente, corrompendo `open_count`, TMA, SLA e duração (mesmo efeito do bug de `row_version`, causa
diferente).

**Fix:** espelhada a guarda do caminho humano no ramo do AI primary (`main.py`, dispatch de
`on_contact_end`/`on_process_end`). NPS inline (`on_contact_end`, side=customer) segue fechando pelo contador.

---

### Follow-ups abertos pelo J4c (achados durante o E2E, 2026-07-13)

1. ✅ **RESOLVIDO (2026-07-13) — virou o redesenho do modelo de deploy (D1–D4).** A armadilha ("promovi e
   rodou a versão velha") era só o sintoma visível. Investigando, achamos uma **incoerência de fundo** e uma
   **cadeia de 4 bugs que se escondiam mutuamente**:

   - O CLAUDE.md afirmava *"skills seguem upsert (são código)"* — **arquivo é a verdade** — **e** existia um
     editor de skills na UI — **banco é a verdade**. Não podiam ser ambas.
   - O `RegistrySyncer` publicava com `x-skill-publish: true`, que grava `{ flow, flow_draft: null }` → **a
     cada boot do bridge o rascunho do editor era APAGADO**. Perda silenciosa de trabalho.
   - Mas ninguém percebia, porque **o editor nunca conseguiu salvar**: o `PUT /v1/skills` voltava **401** (o
     `SkillFlowsPage` tinha um `operatorHeaders` local que não anexava o Bearer).
   - Removido o 401, apareceu o **round-trip quebrado**: o editor devolvia no PUT os campos gerenciados pelo
     servidor e os `null` de campos opcionais (que o `SkillSchema` rejeita — aceita *ausente*, não `null`).
   - E o erro disso aparecia na tela como **`[object Object]`**, escondendo a causa.

   **O `draft` existia para proteger uma produção que ninguém conseguia alterar pela UI** — um remendo
   defendendo uma porta trancada.

   **Modelo novo (decidido com o usuário): uma definição editável + cópia imutável no deploy.**
   | Antes | Agora |
   |---|---|
   | editar skill → rascunho, **apagado no próximo boot** | grava a **definição**; sobrevive a restart (skills = *seed-if-absent*, como pools) |
   | pool sem deploy → rodava a **definição viva** (vazamento silencioso) | **não roda**; log diz o que fazer (`ALLOW_LIVE_FLOW_FALLBACK=true` restaura o legado) |
   | promover congelava "o publicado naquele instante" | congela a **definição atual**; a UI avisa **"⚠ alterações não implantadas"** (`definição.updated_at` × `slot.set_at`) |
   | YAML sobrescrevia tudo a cada boot | YAML **semeia** DB vazio (`REGISTRY_SYNC_RECONCILE=true` p/ GitOps) |

   **Cleanup pendente:** dropar `flow_draft` / `deploy_status` do schema Prisma e o endpoint
   `/skills/:id/deploy` (ficaram órfãos; deixados para depois de o modelo novo rodar).

2. ✅ **RESOLVIDO (2026-07-13, ver CHANGELOG) — canal→pool vem do DEPLOY do skill N3.**
   Era: o N2 (`handle_collect`) resolvia o pool via `ChannelEndpoint` global (`resolve_pool(channel,
   "default")`), cuja coluna `channel` é independente dos `channel_types` do pool → o operador declarava o
   canal duas vezes e um endpoint na aba errada dava 409 sem relação óbvia com a causa.
   Agora: **decisão do usuário** — "o próprio skill-flow N2 recebe por configuração quais canais e pools usar,
   pois isso varia por negócio, não é problema do core". O par canal→pool vive no `config_json` do slot
   (`$.config.channel_policy`, mapa `{canal: pool}`); `SkillConfigParamSchema.type` ganhou `"object"` e a tela
   de Deploy renderiza `JsonParamInput`. `ChannelEndpoint` dedicado eliminado do caminho de survey.
   Validado E2E: pending nasce com `pool_id`/`channel` vindos do deploy.

   **Achado no caminho (3 bugs de deploy, ver CHANGELOG):** `resolve_flow_for_agent` era chamado **sem
   `pool_id`** em 2 call sites (YAML-fallback e @mention) — antes do D1 caía no `skill.flow` vivo e ninguém
   via; `POST /v1/skills` ainda gravava `flow: null` + `flow_draft` (sobra da Fase B que o D3 não alcançou),
   parindo skills sem definição publicável; e o seed-if-absent do D2 confundia **linha existente** com
   **skill semeado**, tornando o buraco permanente. `PUT /slots/next` agora rejeita (422) congelar snapshot
   nulo.

3. **Bug corrigido:** `listChannels` (platform-ui `api/registry.ts`) devolvia o JSON cru (`{channels: […]}`)
   enquanto os callers liam `.items` → a lista de integrações de canal renderizava **sempre vazia**, mesmo com
   os registros persistidos. Normalizado (`data.channels ?? data.items`), como o `listPools` já fazia.

4. ✅ **RESOLVIDO (2026-07-13, ver CHANGELOG) — sessão de suspend/resume fechava com `outcome: suspended`.**
   O `_close_contact_layer` deriva o outcome da SESSÃO do marcador `session:{id}:last_outcome` (o segmento é a
   fonte única; a sessão é derivada). O `process_routed` grava esse marcador ao fim de cada ativação primary —
   mas o **resume roda por outro caminho** (`handle_resume`), que publicava o segmento com o outcome correto e
   **não regravava o marcador**. Sobrevivia o `suspended` da janela PRÉ-suspend, e a sessão fechava como
   `suspended` mesmo tendo resolvido → o `business_outcome` da journey mentia. Espelhada a escrita do
   `process_routed` (mesmo marcador/TTL, `agent_kind: ai`); um re-suspend regrava `suspended`, que é o estado
   correto nesse caso. Validado E2E: journeys respondidas depois do fix fecham `resolved` (as anteriores
   seguem com o carimbo errado na história).

---

## Saneamento `docs/kafka-eventos.md` → Arc 19 *(dívida de doc, 2026-07-08)*

O doc está marcado "Estado: Arc 16" e descreve como **atuais** artefatos removidos no Arc 19 Fase F. Corrigir:

- `journey.events` (9 tipos) + `JourneyEventSchema`/`journey.ts` → marcar como **legado removido** (feito
  parcialmente 2026-07-08; falta a seção de detalhe do tópico, se houver âncora `#journeyevents`).
- `workflow.events` + `WorkflowInstance` + endpoints lifecycle do `workflow-api` → removidos/410 no Arc 19
  (workflow = canal `webhook`); atualizar produtor/consumidor e o texto.
- `skill-flow-worker` como consumidor → subsumido pelo `orchestrator-bridge`.
- Cabeçalho "Estado: Arc 16" → Arc 19; refletir modelo unificado (`session_id` persistente, status `suspended`,
  `origin_session_id`, e futuramente `root_session_id`/`journey.merges`).
- Adicionar tópicos vivos que faltam (`session.signals`, `calibration.events` se ausente, etc.).

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

## OTP produção + primitivo de diálogo genérico (survey + OTP) — decisão ADR *(2026-07-06)*

**Estado:** OTP Fase B **implementado** (tool-based) — identidade progressiva + `verification_class` +
`OtpService` (challenge/verify, rate-limit, entrega mockada) + gate seguro (retomada cross-canal exige
`possessed`). Ver `CHANGELOG.md` § 2026-07-04 e `docs/adr/adr-identity-channel-possession.md`. É um **MVP**;
o desenho de produção foi travado num ADR (Proposto) e supersede o "mini-spec de otp_challenge/otp_verify"
que a seção de identidade acima listava como próximo artefato.

**Decisão (ADR Proposto — `docs/adr/adr-otp-workflow-and-dialog-primitive.md`):**
- **D1 — OTP é workflow negocial + especialistas de canal** (padrão `delegate-workflow-io`, Arc 19): workflow
  channel-abstract (gerar→enviar→coletar→verificar) exposto como **step-up reusável** (qualquer fluxo delega,
  recebe `{verified}`); especialista Tier-3 dono do **canal + coleta da resposta**; entrega real (item 1) vira
  o `collect`/outbound do especialista; caminho leve quando âncora == canal da sessão.
- **D2 — primitivo de diálogo genérico** (construir antes, ancorado em 2 consumidores): reenquadrar o
  interpretador/editor de survey como **script/dialog-builder** (statements sem resposta + retry na mesma
  superfície, além de perguntas); form/dialog **JSON versionado** + i18n + `form_get` genérico; home neutra
  (não `/config/surveys`); reusa as 2 extensões de engine do §17.3 (`$.config.*` + `menu.options/fields`
  dinâmicos). Atualizar o spec de survey (§17/§19) para consumir.
- **D3 — tela de OTP em Configurations** (config-api namespace `identity`/`otp`): comportamento (TTL,
  tentativas, rate-limit, canais de posse) + **bindings** (`form_id` dos prompts, `template_id` de entrega).
  Texto vive na biblioteca de diálogo; a tela referencia ids.

**Costuras (invariantes):** conteúdo (dado/JSON) × controle (skill/workflow) × canal (especialista) × **segredo**
(serviço confiável). **Inegociável:** o código do OTP **nunca** passa pela mão de um agente — gerar/enviar/
verificar ficam no `OtpService`/channel-gateway (envio direto ao canal); o especialista (pode ser IA) só
orquestra canal e carrega o que o **cliente** digitou.

**Reuso pelo survey:** OTP e survey são a mesma família — "interação scriptada delegada". O `skill_survey_runner_v1`
e o especialista de coleta do OTP **convergem para um "dialog-runner" genérico** (Tier-3: renderiza `form_id`,
coleta input cru, devolve), parametrizado por `(form_id, política de canal, sink)`. O outbound do survey (§19,
já usa `collect`) = o mesmo mecanismo de especialista do OTP. **Não unificar** o result-handling (verify × record)
nem a costura de segredo. Guardrail análogo: no survey a resposta tem que ser input real do cliente, não
fabricada por IA (integridade do dado; mesmo princípio, aposta diferente).

**Gaps de produção do OTP, agrupados nas trilhas:**
- **Trilha A (primitivo de diálogo):** textos/i18n dos prompts (item 3) + retry na mesma superfície (parte do 4).
- **Trilha B (config-api):** tuning numérico env-only → namespace `identity`/`otp` UI-editável (item 2).
- **Trilha C (backend/segurança):** auditoria de challenge/verify (item 5, Kafka/`mcp.audit`); OTP como step-up
  genérico (item 6 — resolvido por D1); lockout crescente (item 7); teste de unidade do adapter/endpoints (item 8).
- **Item 1 (entrega real, adiado até termos canais):** vira o `collect` do especialista (D1) — provedor SMS/e-mail,
  seleção de canal pelo especialista, envio por canal ≠ sessão (posse forte). Retomar quando houver os canais.

**Desenho ✅ (2026-07-06):** os 2 artefatos travados em `docs/product/dialog-primitive-and-runner-design.md`
(schema do form/dialog JSON + contrato do dialog-runner), com as 6 bifurcações decididas (store dedicado,
runner devolve cru, i18n embutido, render v1 estagiado, etc.).

**Fatia 1 ✅ (2026-07-06):** primitivo v1 implementado e validado no demo (ver `CHANGELOG.md` §
2026-07-06). `@plughub/schemas/dialog.ts`; `dialog-api` (porta 3760) + `form_get`; extensões de engine
§17.3-1 (`$.config.*`) e §17.3-2 (menu `options/fields` dinâmicos); `skill_dialog_runner_v1` (pool
`dialog_runner`); OTP como consumidor de validação (intake delega a coleta do código ao runner; `OtpService`/
`otp_verify` seguem no intake — segredo intacto). **Binding as-built = contexto de delegate
(`@ctx.session.dialog_form_id`)**, não `$.config` (o hook `$.config` foi construído, deploy-por-slot fica p/ Fatia 2).

**Fatia 2 — adoção pelo survey ✅ (2026-07-06, parcial):** o survey vira o 2º consumidor (ver `CHANGELOG.md`).
`dialog_nps_v1` (form NPS texto) + `agente_survey_reconnect_v1` delega ao `dialog_runner` + `skill_survey_v1`
faz o `survey_record`. Delegate de nível único (reconnect→runner; aninhar no collector = colisão de
`session.delegate_resume_token`, rejeitado). Validado no veículo conversacional.

**Fatia 2b — NPS botões + interação/visibilidade dinâmicas ✅ (2026-07-06):** engine §17.4 (`menu.interaction`
e `menu.visibility` união `enum|array|ref`); `form_get` render nativo single-question; runner com contrato
uniforme `payload={value}` (OTP/survey atualizados p/ `.value`); `dialog_nps_buttons` (botões 0-10 customer-only).
**NPS ativo migrado** (`agente_nps_v1`, hook `on_contact_end`) — mas **inline** (form_get + menu dinâmico), NÃO
via runner: *achado* — hooks de fim-de-contato não podem delegar (suspend = hook concluído → contato fecha antes
de renderizar). Runner serve chamadores que podem suspender; hooks usam inline. Validado: NPS botões + survey
reconnect (`{value}`) + OTP por simetria. Ver `CHANGELOG.md` §2026-07-06.

**Fatia 2 — editor (form-builder) ✅ (2026-07-06):** `/config/dialog-forms` no platform-ui (grupo Configuração,
ABAC `config.platform`) consumindo o `dialog-api` (proxy `/v1/dialog` no nginx+vite). Lista + editor de nós +
publish. Fecha a dívida "form = dado do tenant, UI-editável". Ver `CHANGELOG.md` §2026-07-06. MVP: locale único,
sem preview, writes abertos no demo.

**Fatia 2 — loop no engine ✅ (2026-07-06):** step `loop` (N perguntas sequenciais em canal pobre). Modelado no
contador do `receive` (`_loop_idx_{id}`), item atual em path FIXO (sem índice variável em ref), acumula
`{metric,value}`, guardado pelo `menu` do body + `max_iterations`. Consumidor real: `dialog_survey_multi_v1` +
`skill_survey_multi_v1` (pool `survey_multi_ia`, webchat direto). Validado (csat+ces no `survey_record`). Ver
`CHANGELOG.md` §2026-07-06.

**Retry por formato ✅ (2026-07-07):** `MenuStep.validation`+`retry` (união objeto|ref); `menu.ts` faz reprompt
na mesma superfície em falha de FORMATO, honra `max_attempts`, esgota→`on_failure`. `form_get` expõe
validation/retry no render; runner + loop consumer passam os refs. Validado (`abc`/`200`→reprompt, `15`→ok).
Ver CHANGELOG (inclui a nota de deploy: rebuild `--no-cache` dos consumidores do schema + re-snapshot do slot
via `set-next`+`promote` com auth `x-service-token`).

**Fatia 2 (restante, pendente):**
- **timeout dinâmico do runner ✅ (2026-07-08):** o `DialogForm` já tinha `timeout_s` por pergunta; agora o
  `form_get` expõe `render.timeout_s`, o `MenuStep.timeout_s` aceita `number | ref` ($./@ctx.), o engine
  (`menu.ts`) resolve o ref → número (fallback 300), e o runner usa `$.pipeline_state.dialog.render.timeout_s`;
- ~~**`channel_policy: elect`** (runner apresenta menu de canal, reach cross-canal)~~ — **decisão C (2026-07-08):
  adiado.** A eleição de canal já é alcançável hoje como uma `question` do form que o workflow lê e age; o `elect`
  de 1ª classe (runner dispara o re-dispatch sozinho) conflita com a segregação de perfil (reach = `collect`,
  exclusivo de `workflow`; runner é `agent`) e não tem consumidor concreto ainda. Reabrir quando houver um fluxo
  que exija o runner ELE MESMO re-despachar cross-canal (aí decidir A escopado vs B pleno);
- **editor multi-locale ✅ (2026-07-07):** LocaleBar (chips add/remove/selecionar idioma em edição), `setLt`
  preserva o mapa `{locale}` (string pura = só o default_locale), indicador "sem tradução" por nó, save garante
  `default_locale ∈ locales[]`. Aplica a text/prompt/labels. Refinamentos de editor → ver **"Revisão do editor de
  diálogos"** abaixo;
- **config params por deploy — declaração + UI ✅ (2026-07-08):** `SkillConfigParam[]` (`@plughub/schemas`:
  `key/type/label/description/required/default/source/options/min/max`) declarado no topo do skill →
  coluna `config_params` no agent-registry (passthrough na rota + forward no RegistrySyncer) → **UI de
  Flow › Deploy** (`ConfigForm` lê `config_params`; `source` conhecido = combo populado por endpoint
  [`dialogforms`/`pools`/`skills`], `options` = select estático, senão input por `type`; source desconhecido
  degrada p/ texto — interpretação é 100% da UI, engine só vê `$.config.<key>` literal). `skill_survey_multi_v1`
  ganhou `config_params: [form_id source=dialogforms]`.
- **config params — plumbing runtime ✅ (fatia 1, 2026-07-08):** o bridge lê o `config_json` do slot `current`
  (`get_pool_current_flow` → `_pool_config_cache`, invalidado no `registry.changed(pool)`) e injeta como `config`
  no `/execute` (`activate_native_agent`; também nos call sites de fila e webhook-resume). O skill-flow-service já
  resolvia `$.config.*` (§17.3-1). `skill_survey_multi_v1` trocou os 2 literais `form_id` por `$.config.form_id`
  → **survey virou skill-único parametrizado por deploy.** **Exige deploy por slot** com `config_json.form_id`
  (set-next+promote): sem isso `form_get` falha (contrato do skill parametrizado). Typo de `source` NÃO tratado
  no deploy (por decisão); **lint no publish ✅ (2026-07-08)** — agent-registry `configParamSourceWarnings`
  avisa (não-bloqueante, `config_param_warnings` na resposta do PUT/POST + log) quando um config_param declara
  `source` fora do conjunto conhecido; pega o typo no authoring sem fechar o schema (UI defasada não vira erro).
  Worker legado (`skill-flow-worker`) fora de escopo (Arc 19 o deprecou; survey roda como agente nativo via bridge).
- **entrega real do link web** ✅ (2026-07-08, provider layer): veículo web + camada de providers plugável.
  `SurveyLinkDelivery` virou **roteador** sobre `LinkDeliveryProvider` (protocol): `MockProvider` (dev log,
  default/fallback) + `WebhookProvider` **vendor-neutro** (POST `{kind,address,url,tenant_id}` ao gateway do
  próprio tenant, sem SDK de vendor = no-lock-in). Seleção por `kind` a partir do config-api
  (`survey.link_delivery`: `default_provider`/`routes`/`webhook.url`), segredo (token) em env
  (`PLUGHUB_SURVEY_LINK_WEBHOOK_TOKEN`); fallback gracioso p/ mock. Cache por tenant + invalidação no
  `config.changed(survey)`. Seed default + 10 testes (router + webhook httpx mock). **Falta só operacional
  (sem código):** o tenant apontar `webhook.url` pro gateway SMS/e-mail dele + setar o token. Um `SmtpProvider`
  nativo p/ e-mail é opção futura (webhook já cobre via gateway do tenant). Superfície de UI dedicada p/
  `link_delivery` = follow-up (hoje editável como config genérica). §9.2/§19 de customer-surveys.

### Revisão do editor de diálogos (UX + completude) — `/config/dialog-forms` ✅ parcial (2026-07-07)

Reformado para o **modelo de blocos** (instrumento pontuado vs. Diálogo sem nota) — ver CHANGELOG
"Editor de dialog-forms por blocos". **Feito:** nós colapsáveis; completude toda (`retry` reprompt+max_attempts,
`min_length`/`max_length`/`pattern`, `masked`, `timeout_s`, `value` por opção); validação/opções escondidas em
pergunta pontuada (herda do instrumento) e nos blocos de Diálogo aparecem por-pergunta; `description` +
`default_locale` como select; catálogo de instrumentos em config-api.

**Falta (2ª passada):** reordenar por **drag** (hoje setas ↑↓); **edição de locale lado-a-lado** + **progresso de
tradução** estável + **preview** do que o cliente vê; **auth no write** (gate ABAC `config.*` — hoje aberto);
**validação client-side** com mensagens (form_id slug, output_key único, dimension_id snake_case); **confirmação
ao descartar rascunho** (dirty/blocker); `interaction=form` com múltiplos `fields`. Base: `DialogFormSchema`
(`@plughub/schemas/dialog.ts`); nada de controle no editor (as 4 costuras valem). Spec:
`docs/product/dialog-primitive-and-runner-design.md`.

### Composição de nota em survey — dimension + perguntas ponderadas ✅ schema+runtime+E2E (2026-07-07)

Implementado (ver CHANGELOG + [`docs/adr/adr-survey-form-scoring-composition.md`](docs/adr/adr-survey-form-scoring-composition.md)):
`@plughub/schemas/scoring.ts` (`composeScore`, weighted_mean + re-normalização de NA) + `DialogForm.dimensions`
(escala+agregação na dimension, perguntas herdam) + `survey_record` compõe (`form_id`+`answers`, aceita o array
do loop via `answersToMap`) + `skill_survey_multi_v1` grava via compose + form CSAT composto (seed). **Validado
ao vivo** no webchat: atendimento=5, resolução=3 → `csat`≈4.33 ponderado + `nps`=10 (2 sinais). Store = dialog-api
(D8); agregação no `survey_record` (D9); dimensions paralelas (≠ composite do Quality). Compat: `capture.metric`
legado = dimension 1-item.

**Falta:** (1) **editor de dialog-forms com UI de dimension** ✅ (2026-07-07 — modelo de blocos, ver CHANGELOG +
o item "Revisão do editor de diálogos" acima); (2) composite de form / health score ✅ (2026-07-08 —
`DialogForm.composite`, roll-up 0–100 no `survey_record`, editor com toggle + peso por dimension);
(3) `survey_question` reutilizável — fora do 1º corte; (4) adoção do `scoring.ts` pelo `EvaluationForm` ✅
(2026-07-08): fronteira TS/Python → não é reuso de módulo, é **alinhamento de semântica** (decisão A). O
`scoring.py` (`aggregate_scores`) ganhou o kernel `_compose` espelhando o `composeScore`, passa a honrar
`aggregation` (`weighted_average`→`weighted_mean`, `min_score`→`min`) e `scoring_method` (`simple_average`=pesos
iguais) — conserta o bug latente de ignorá-los; default idêntico; `test_scoring.py` com paridade+regressão.

### Skip-logic condicional em DialogForm — guarda `ask_when` ✅ (2026-07-08)

Implementado nas 4 fases (ver CHANGELOG + [`docs/adr/adr-dialog-conditional-skip-logic.md`](docs/adr/adr-dialog-conditional-skip-logic.md)):
guarda **declarativa** `ask_when {field, op, value}` em statement/question; `evaluateAskWhen` puro +
`askWhenForwardRefErrors` (só-para-trás) no `@plughub/schemas`; step `loop` pula item falso; `survey_web.py`
espelha em JS (show/hide reativo); editor com a linha "only ask if…". **Validado ao vivo** (`atendimento < 3`
pergunta/pula o follow-up; pulada = NA, `signals=2` nos dois casos). **Completado 2026-07-08:** guarda
**por-bloco** (fan-out no editor — "only ask this block if…"), `in` multi-valor (vírgula), validação de
forward-ref bloqueando o save. Decisão de borda confirmada: `field` não respondido ⇒ guarda falsa (skip).

**Guard: proibir suspend em skills de hook de teardown ✅ (2026-07-06):** implementado no `registry_syncer.py`
(`_validate_teardown_hooks` + `_load_skill_steps`, chamado após o sync de skills). Read-only, fail-open, ERROR
loud nomeando pool→hook→pool-alvo→skill→step. Config atual passa limpa (nps_ia/wrapup inline). Avaliação:
hoje um `delegate`/`suspend`/
`collect` num skill amarrado a hook de fim-de-contato passa sem erro e vira bug silencioso (o bridge trata
`suspended` como hook concluído → fecha o contato antes de renderizar). **Achado da avaliação:** o sinal certo
NÃO é `classification` (só categoriza + regra "orchestrator precisa de flow") nem o `execution_model`
(derivado por `_computeFlowModel` de `suspend`/`collect`; furos: não inclui `delegate`, e é do skill não do
contexto de hook — skills de hook são legitimamente "agent", e OTP/survey "agent" legitimamente delegam). O
sinal correto é o **cross-reference `PoolHooks.on_contact_end/on_human_end/post_human` → pool-alvo → skill
deployado**. **Guard proposto:** na validação de deploy/sync (agent-registry/RegistrySyncer), rejeitar quando o
flow do skill de um pool-alvo de hook contiver step que suspende (`delegate`/`suspend`/`collect`) — reusar a
varredura do `_computeFlowModel` **estendida com `delegate`**. Erro explícito de config em vez de footgun.
Alternativa: flag declarado (`classification.execution_context`), menos robusto (depende do autor). Tarefa #17.

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

## Isolamento do substrato por `origin` — ✅ ARCO COMPLETO (2026-06-25) — resta fase 2

Passos 1–6 + fix de rótulo concluídos e validados E2E (`infra/test/smoke_origin_reeval.sh`); detalhe em
`CHANGELOG.md`, racional em [`docs/adr/adr-quality-substrate-isolation.md`](docs/adr/adr-quality-substrate-isolation.md)
(Status: Aceito — implementado). Resolveu o concern §9(c) do Quality Ingest.

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

## Relatórios analíticos — Agentes e Pools

Avaliação + proposta em [`docs/arcos/analytics-reports-redesign.md`](docs/arcos/analytics-reports-redesign.md).
Hoje o Analytics/Agents mistura agente×pool e não separa humano×IA.

- **Fase 1 — relatório de agentes**: humano por usuário×pool (lookup login), IA por
  flow_id(skill)×pool; abas distintas; excluir webhook; daily trend de segments; link→Quality.
  (`reports_query` + `AnaliseAgentesPage`.) `flow_id` no segments ✅.
- **Fase 1b — tempo logado/disponibilidade ✅** (2026-06-02): tabela `agent_login_intervals`
  + máquina de estados no consumer (reusa agent_ready/agent_login → abre, agent_logout → fecha;
  Redis `{tenant}:login:{instance}`); endpoint `agent-availability` reescrito por instance_id
  (logged_ms/available_ms/user_login) + donut de motivos no `AgentsTab`. Ver `CHANGELOG.md`.
  Derivados ✅: **ocupação** (busy dos segments ÷ disponível) e **gestão de motivos de pausa**
  (i18n) — ambos concluídos 2026-06-02, ver `CHANGELOG.md`.
- **Timeline do agente — presença por pool ✅** (2026-06-02): tabela `agent_pool_intervals`
  (diff de `pools[]` no consumer) + endpoint `/reports/agent-timeline` + componente
  `AgentTimeline` (swimlanes: Total + faixa por pool, overlay de pausas) com drill-down da
  tabela de Disponibilidade. Ver `CHANGELOG.md`. Precisão por pool é aproximada (atribui o
  intervalo inteiro a cada pool tocado); sub-intervalos exatos por pool = refinamento futuro.
- **Pausa — persistência através de reconnect ✅** (2026-06-02): key durável
  `{tenant}:agent_paused:{instanceId}` (pause grava TTL 16h, resume deleta); `registerHumanAgent`
  e o heartbeat passam a carregar `status=paused` quando a key existe → o routing mantém
  `state=paused` (alocação exige `state=="ready"`, linha 161/652 do registry) → agente continua
  excluído sem cirurgia em sets; novo `GET /api/agent-state` + a UI lê ao montar (botão reflete
  a realidade). TTL por motivo (`max_minutes`) + logout explícito limpa a key (`POST
  /api/agent-clear-pause`). Órfã resolvida: no `agent_logout` o analytics fecha a pausa aberta
  **só** quando a key durável está ausente (= logout explícito), distinguindo de navegação. Ver `CHANGELOG.md`.
- **Pausas — gestão de motivos ✅/decidido** (2026-06-02): a pausa é do **agente** (remove de
  todos os pools), então motivo **por pool é semanticamente errado** — lista global é o correto.
  Config UI de cadastro descartada por overkill (Config API `pause_reasons` semeável + fallback de
  defaults já cobre); seletor de motivo já existe (`PauseReasonModal`). Único ajuste feito:
  **i18n** dos motivos default + textos do modal (seguiam fixos em pt-BR) → namespace `agentAssist`
  seção `pause` (en + pt-BR). Labels do Config API permanecem como configurados pelo tenant.
- **Fase 2 — relatório de Pools/Infra ✅ concluída** (2026-06-04): pool×canal×**endpoint**×tempo
  — volumetria, fila (espera/tamanho/abandono/disponíveis), concorrência vs capacidade
  (headroom), SLA. Spec/ADR em [`docs/arcos/pools-infra-report.md`](docs/arcos/pools-infra-report.md).
  **Atualização 2026-06-03**: Fila/SLA reescritos sobre segments `role='queue'` + demanda
  reprimida no Volume (queue-attended-model Fase D ✅, ver `CHANGELOG.md`).
  **Atualização 2026-06-04**: dívida `sessions.sla_target_ms` resolvida ✅ (ver
  `CHANGELOG.md`) — aba SLA popula a partir dos contatos novos; sessões históricas
  permanecem NULL (valor nunca foi persistido, irrecuperável).
  **Fechamento 2026-06-04 ✅** (ver `CHANGELOG.md`): recon confirmou (TODO atrás do
  código de novo) que sampler/consumer/endpoints/aba já existiam; decisões: (a)
  occupancy **sampler** basta (carry-over implícito, `peak_total` instantâneo —
  contadores event-driven descartados); (b) teto do **total** = configurada no pricing
  (novo `GET /v1/pricing/capacity/{tenant_id}`, `capacity_source` no occupancy,
  fallback gracioso), per-pool segue provisionada; (c) time-series de capacidade na
  aba Capacidade ✅ (Arc 19). Residuais opcionais no spec (§ Pendente→Concluído):
  sub-aba Visão geral, heatmap hora×dia, SETs de session_id, overlay licenciada v2.
  **Dívida descoberta na validação (2026-06-04)**: a integração pricing→quota Redis
  (`{t}:quota:*` lidas pelo `assertQuota`) está documentada em `docs/arcos/pricing.md`
  e no CLAUDE.md mas **não existe no pricing-api** (zero código Redis; verificado:
  `keys 'tenant_demo:quota:*'` vazio após POST de resources). O teto contratado hoje
  é só analítico (denominador do occupancy); o gate de admissão por quota não arma.
  Implementar a escrita das quotas no upsert de resources (ou na ativação de plano)
  e corrigir `pricing.md` enquanto isso.
- **Queue-attended-model — residuais pós Fase E** (2026-06-03, ver spec): (a) ~~render v2
  webchat~~ ✅ (2026-06-04, ver `CHANGELOG.md`) — `deliver_text` entrega mensagens de
  sistema via WS e `deliver_session_closed` renderiza `farewell_text` antes do close;
  validado no cenário outage (`reservation_full`). Canais voice/whatsapp ainda não
  renderizam `farewell_text` (voice = TTS futuro);
  (b) ~~limpar `queue_config`/`session_reservation` via PUT~~ ✅ (2026-06-04, ver
  `CHANGELOG.md` — `.nullable()` nos campos de pool + `DbNull` no registry + UI);
  (c) cenários fila muda e drop sem pool_id não exercitados em teste.
- ~~**Sessões sem `pool_id` no relatório de fila**~~ ✅ (2026-06-05): origem
  identificada — sessões nunca roteadas nem enfileiradas (pool vazio E sem
  segmento de fila; ex. webchat que conecta e não engaja). Sem semântica de
  fila → filtradas do `/reports/pools/queue` (`WHERE pool_id != ''` no
  per-session, com justificativa em comentário); o volume delas segue no
  Volume report.
- ~~**i18n quebrado no dropdown de pools do Console**~~ ✅ (2026-06-05): a chave
  `header.comboPools` interpola `{{pools}}` mas o cabeçalho do dropdown passava
  `{ count }` → literal `POOLS ({{POOLS}})`. Fix no `Header.tsx` (passa
  `pools: "ativo/total"`, mesmo formato do botão).
- ~~**Env do Config API no routing**~~ ✅ (2026-06-05): faltava
  `PLUGHUB_CONFIG_API_URL` no compose — o RoutingConfigCache tentava
  `localhost:3600` no boot e caía nos defaults hard-coded (custom de
  mensagens/limites do tenant não chegava até um config.changed). Adicionado
  `http://config-api:3600`.
- **Reformulação Analytics/Agents — Bancada de comparação 360° (novo)**: reescreve a aba como
  bancada de comparação (média dos agentes × indivíduos), unificando quantitativo + qualitativo
  (Arc 6) + voz do cliente (NPS/pesquisa) + voz do agente (wrap-up) na mesma entidade `agent_key`.
  **Spec/ADR** em [`docs/arcos/analytics-agents-workbench.md`](docs/arcos/analytics-agents-workbench.md)
  — decisões fechadas: média aritmética rotulada "média dos agentes" + N; comparabilidade por
  domínio de métrica (desabilita no seletor); camada `session_signal` (NPS/wrap-up/pesquisa via
  Arc 12 + journey, `session_at`×`captured_at`, normalização por pool); detalhe type-aware;
  cruzamento das vantagens (concordância/quadrante) + calibração do avaliador (Arc 13).
  **Recon 2026-06-07 (§13 do spec)** — premissas validadas no código + decisões travadas:
  · `evaluation_results` **sem** atribuição a agente → exige join `→ segments` por `session_id` (F2);
  · hooks NPS/wrap-up **não** emitem `agent_event` (dado preso no ContextStore); `session_signal` inexistente (F5);
  · outcome humano é **placeholder** (não 0%) — fonte real = `session.wrapup.classificacao`;
  · **decisão**: `complete` de todo agente devolve outcome **dinâmico**; `primary` humano **propaga** o do wrap-up;
  · domínio `pending≡suspended`, `transfer≡escalate` (sem valor novo) — mapa wrap-up: resolvido→resolved, escalado→escalated, cancelado→abandoned, pendente→suspended;
  · contrato do segmento (lido igual humano/IA): `outcome` + `close_reason` (enum, iniciativa) + `handoff_reason` (livre, escalação) + `issue_status` (rótulo curto); texto livre rico no detalhe sob demanda (LGPD).
  **Fases**: **F1 espinha (outcome real) ✅ 2026-06-07** (ver `CHANGELOG.md` — inclui correção da
  causa-raiz: notify nunca implementara `context_tags`, destravando também o NPS) → **F2 join
  qualidade ✅ 2026-06-07** (atribuição validada E2E; pipeline de avaliação religado — ver
  CHANGELOG; pendências test-grade: ReplayContext sem session_meta e sem associação campanha/form
  → arco da visão final) → **F3 endpoint `/reports/agents/compare` ✅ 2026-06-07** (5 lentes v1;
  média aritmética c/ gap; validado com dado real — ver CHANGELOG) → **F4 UI bancada ✅ 2026-06-09**
  (AgentsBenchPage; subfases F4.1–F4.5 no CHANGELOG; pendentes na UI: nps/wrapup→F5, quality_criteria;
  refinamento: pool-average agregado via pseudo-entidade `pool:`) → **F5 NPS+wrap-up (grão segmento)
  ✅ 2026-06-09** (derivado de segments, NÃO session_signal; refator per-segmento no bridge — ver
  CHANGELOG + conference-mechanics §Mudança 7; session_signal p/ grãos contato/jornada fica futuro)
  → **F6 cruzamentos ✅ 2026-06-09** (endpoint `/reports/agents/cross` + view Cross-cut: concordância
  + quadrante) → **F8 quality_criteria ✅ 2026-06-09** (lente por dimensão + heatmap + radar) →
  **F9 pool-average `pool:` ✅ 2026-06-09** (pseudo-entidade) → **F7 motivo de escalação ✅ 2026-06-09**
  (taxonomia configurável + lente empilhada) → **F10 `session_signal` (grão contato/jornada — em curso)**:
  **F10.1 camada de dados ✅ 2026-06-10** (tabela `session_signal`; ingest inicial via dual-write de
  `agent_event` — substituído na F10.2a) → **F10.2a tool `survey_record` + tópico `session.signals` ✅
  2026-06-10** (store unificado: TODOS os grãos `segment|session|workflow|journey` gravados
  explicitamente via tool MCP dedicada; `segment` com `segment_id`+`agent_key`; dual-write de
  `agent_event` retirado, contrato Arc 12 intacto; validado E2E — ver CHANGELOG) → **F10.2b.1 esqueleto
  trigger→record ✅ 2026-06-10** (fluxo primário dispara sub-workflow `skill_survey_v1` via
  `workflow_trigger` passando `origin_session_id`; `survey_record` tenant-explícito; validado E2E.
  **Destravou 4 fixes de plataforma**: input array no `StepInputValueSchema`; resolução webhook
  `skill_id`→pool no routing — nunca existira, funcionava por acaso com 1 pool webhook —
  via `webhook_skill_id`; `skill_id` no `ConversationInboundEvent`; demo exige `INCRBY` na quota
  `max_concurrent_sessions`. Ver CHANGELOG) → **F10.2b.2 coleta real de NPS via delegate (inbound_only)
  ✅ 2026-06-10** (skills `agente_survey_nps_v1`+`agente_survey_reconnect_v1`; pools `survey_collector_ia`+
  `survey_reconnect_ia`; reconexão webchat via `pending_workflow_get`; fix de plataforma: recursão de
  arrays no `interpolate.ts`; validado E2E real com NPS=8 — ver CHANGELOG) → **F10.3a exposição do NPS
  de sessão na bancada ✅ 2026-06-10** (lente `session_nps`: `session_signal` grain=session ⋈ atribuição
  por session_id → NPS de sessão dos contatos do agente, cruzamento §8; seção "Voz do cliente" no
  detalhe type-aware: NPS agente × NPS sessão; i18n; teste passa; endpoint 200 — ver CHANGELOG. Não toca
  F5) → **F10.3b cutover F5 ✅ 2026-06-10** (caminho B unificado: `agente_nps_v1` chama
  `survey_record(grain=segment)`; bridge escreve `session.surveyed_segment_id`/`agent_key` via `@ctx`;
  `_compare_nps_lens` migra para `session_signal` (join segments p/ metadata) → lentes `nps`+`session_nps`
  leem a mesma tabela, **acaba a duplicação**. **Cutover final**: validado E2E o write do hook (fluxo
  humano real → `survey_record grain:segment, nps=8`); legado removido (bridge não escreve mais
  `segments.nps_score`; `_apply_nps_to_segment` deletado). Coluna `nps_score` vestigial (DROP opcional).
  **Fatia F10 concluída.** Ver CHANGELOG). **F11 futura**: survey
  **diferida** (`captured_at ≠ session_at`, `session_at` da origem via enrichment) + grão **journey**
  ponta-a-ponta. Vocabulário: `journey`=grão (relacionamento multi-sessão), não a entidade eliminada.
  Detalhe em §13/§14 do spec. Débito pré-existente notado na F1:
  3 falhas em `resolve.test.ts` (BLPOP/mention mocks — não relacionadas). Débito notado na F10.3a:
  6 falhas em `test_reports.py::TestQueryAgentAvailabilityReport` (`query_agent_availability() missing
  positional arg 'tenant_id'` — descasamento assinatura×teste, pré-existente, não relacionado à bancada).
  **▶ PRÓXIMA SESSÃO (planejada 2026-06-10) — FECHAR A BANCADA (follow-ups A), ordem sugerida:**
  1. ✅ (2026-06-11) **`$.segment_id` no `interpolate.ts`** — `segment_id: ctx.segmentId` no evalContext
     de `resolveJsonPathRef`; teste em `invoke.test.ts`. Skill lê `$.segment_id` p/ `survey_record(grain=segment)`
     "sobre si mesmo". Ver CHANGELOG 2026-06-11.
  2. **F11.1** ✅ (2026-06-11) — enrichment de `session_at`: consumer resolve `analytics.sessions.opened_at`
     da origem (por `origin_session_id`) e sobrescreve `session_at` no ramo `session.signals`; fallback
     `captured_at`. `AnalyticsStore.lookup_session_opened_at` + `consumer._enrich_signal_session_at` (cache).
     Grão `journey` já aceito. **F11.2 (validação)**: diferido **simulado via curl/seed** (decisão do
     usuário) — publicar `session.signals`/`survey_record` com origem de `opened_at` anterior + grão journey,
     conferir `session_at = opened_at`. Workflow agendado real (dias depois) fica futuro. Ver CHANGELOG.
  3. ✅ (2026-06-11) **quality cross-form — re-escopado**: merge de dimensões cross-form **descartado**
     (inventa equivalência inexistente). Regra de comparabilidade: cross-agente exige mesmo form;
     cross-form só p/ um único agente. `_compare_quality_lens` expõe `summary.form_ids`; UI da lente
     `quality` faz guard/ressalva. `quality_criteria` segue same-form. **Futuro**: catálogo canônico de
     dimensões (única base rigorosa p/ comparar dimensões entre forms) → arco próprio. Ver CHANGELOG.
  4. **Validações E2E reais F5/F7 + limpeza de fixtures** — EM ANDAMENTO (2026-06-11):
     - **F7** ✅ **VALIDADO E2E REAL (2026-06-12, ver CHANGELOG)**: contato real
       `sac_ia`→escala→`retencao_humano`→wrap-up escalado+motivo (conduzido via webchat+Console no
       navegador). `plughub_demo.segments` da sessão: 1 linha IA (`flow_id=skill_atendimento_sac_v1`,
       `outcome=escalated_human`, `escalation_reason=specialist_needed`) + 1 linha humana
       (`agent_type=human`, `outcome=escalated`, `escalation_reason=retention`). Wiring confirmado ponta
       a ponta (IA via `pipeline_state.results.escalation_reason`; humano via menu do wrap-up→`seg_signal`
       →bridge). Nota de execução: menu `list`/`button` exige **eventos de mouse completos** p/ submeter
       a seleção (um `.click()` JS puro não dispara o handler).
     - **F5** inline (grão segmento) — ✅ **CONCLUÍDO E VALIDADO E2E (2026-06-12)**. O NPS/wrap-up inline
       é **1 por contato, no segmento humano final** — "2 NPS inline num contato" é estrutural (não existe).
       O **transfer funcional** (ver CHANGELOG "Console Transfer + G7") destravou e **validou** a atribuição
       per-segmento real: contato com 2 segmentos humanos (`operator@…` `transferred` em `retencao_humano` →
       `admin@…` `resolved` em `humanoxxx`), e o sinal `session_signal grain=segment metric=nps=10`
       corretamente chaveado ao `segment_id`/`agent_key` do segmento **final** (admin), não ao transferido.
       Caminho de escrita do NPS confirmado saudável (`survey_record grain=segment`; o "não gravava" em
       automação era artefato de `.click()` JS no webchat, não regressão).
       **Reclassificação (decisão 2026-06-12)**: a riqueza "**N sinais por agente/segmento**" **NÃO é inline**
       — é o **modelo de pesquisa multi-grão OUTBOUND** (`session_signal` grãos `journey | session | segment`,
       até 3 grãos por fluxo, configurável: avaliar a journey, cada contato e cada segmento). Base parcial na
       F10.2b (`survey_collector_ia`/`survey_reconnect_ia`). Falta o **planejamento da orquestração** (quando/
       como cada grão dispara, surveys diferidas `captured_at≠session_at`) → vira **F11 / arco de pesquisa
       multi-grão** (evaluation), separado do G7 (ciclo de vida). Ver `docs/arcos/g7-segment-contact-decoupling.md` §5.
     - **NPS render (cosmético, diferido)**: a mensagem do `menu`/`notify` passou a ser exibida no
       transcript como "structured content" (texto dentro do envelope) em vez de texto puro; o **dado do
       NPS é gravado normalmente**. Revisar o emit do `menu`/`notify` + render no transcript depois.
     - **F8** ⏸ **ADIADO**: `evaluation_dimension_scores` segue com fixture (seed de `evaluation_results`).
       O avaliador `agente_avaliacao_v1` não roda no demo (test-grade, sem associação form/campanha) —
       consertar o pipeline de avaliação é **arco próprio**. Fixture documentado até lá.
  5. ✅ (2026-06-11) **DROP `segments.nps_score`**: leitor esquecido no `query_agents_cross` (F6)
     migrado p/ `session_signal` (grain=segment); removido de DDL/cols/row-builder/parser (analytics) e
     do bridge (`_publish_participant_event`/republish, vestigial). DROP idempotente
     (`_DDL_SEGMENTS_DROP_NPS`) auto-aplica no startup do analytics-api — sem passo manual. Testes do
     cross atualizados (seg→nps→eval). Ver CHANGELOG.
  6. ✅ (2026-06-11) **Débitos de teste pré-existentes**: ambos eram drift teste×impl (produção OK).
     `TestQueryAgentAvailabilityReport` (6) — além da assinatura `(client, database, tenant_id, …)`, o
     mock estava obsoleto: a fn foi reescrita na Fase 1b (4 queries login/pause/reason/busy, não 3); o
     mock com 3 resultados esgotava o `side_effect` → `StopIteration` no `to_thread` **travava o pytest**.
     Testes reescritos pro modelo novo. `resolve.test.ts` (3) — modelo multi-instância: result key com
     `instanceId` + `hdel` no hash `menu:waiting` (testes usavam key plana + `del`). Só testes. Ver
     CHANGELOG. **Follow-ups A (1–6) COMPLETOS.**

  **✅ BUG corrigido (2026-06-11) — contato vazava p/ todos os agentes do mesmo pool no Console:**
  Causa-raiz: `conversation.assigned` publicado no canal do POOL `pool:events:{poolId}`; o WS handler
  aceitava qualquer assignment sem filtrar o `instance_id` alvo → fan-out pro pool (regressão do modelo
  por-usuário C1 sobre o canal por pool legado). **Fix**: conexão calcula `expectedInstanceId =
  "human-${userId}"` e descarta `conversation.assigned` de outro alvo, nos dois caminhos (pub/sub ao vivo
  + reentrega de `pool:pending_assignment`). Helper puro `lib/assignment-filter.ts` (`shouldDropAssignment`)
  + teste. Backward-compat (userId/target vazio → não filtra). Rebuild `mcp-server-plughub`. Ver CHANGELOG
  + `conference-mechanics.md` § Histórico.
  **Pendências relacionadas (abertas)**: (a) `pool:pending_assignment:{poolId}` é UMA chave por pool
  (last-write wins) → chave por-instância é melhoria futura (liga à fila pull/inbox).

  **✅ Transfer "No destinations available" RESOLVIDO (2026-06-11) — eram 3 camadas:**
  (8.1) contrato — `escalation_pools` no `SupervisorConfigSchema` (registry parava de descartar no write);
  (8.2) config — seed do campo no `retencao_humano` (YAML→registry); (8.3) **endpoint** — a rota REST
  `GET /api/supervisor_capabilities/:sessionId` (server.ts) era um **stub vazio**; passou a resolver
  pool do session meta e ler `escalation_pools` da registry. **Validado E2E**: combo lista sac/reembolso/
  portabilidade. Ver CHANGELOG. **Pool Config Surface** (editar esses campos na UI) segue como F2-pools.
  **Iniciativa maior (decidida pelo usuário)**: o YAML é seed-a-eliminar; TODO config de pool deve ser
  editável na tela `config/resources/pool` (registry-backed), pra provisionamento sair 100% da config.
  **Inventário-fonte + plano**: `docs/arcos/pool-config-surface.md`. Gap principal (não na UI hoje):
  `hooks` (wrap-up/NPS/post), `supervisor_config` (escalation_pools/intent_map), `mentionable_pools`,
  `deploy` (skill+concorrência IA), `evaluation`, `agent_kind`, `session_reservation`,
  `max_concurrent_sessions`, `agent_groups`, `webhook_skill_id`. Fases no doc.

  **▶ ESCOPO: Config Consolidation (estratégia HÍBRIDA, 2026-06-11)** — plano completo em
  `docs/arcos/config-consolidation.md` §8. Os invariantes "Configuration — Single Source" no CLAUDE.md
  são **permanentes**; este escopo é o burn-down das violações herdadas até o guard ficar limpo.
  - [x] **F0.1** ✅ Invariantes de config (permanentes) no CLAUDE.md, seção "Configuration — Single Source"
  - [x] **F0.2** ✅ Guard-rail: `infra/check_config_invariants.py` (allowlist de 4 violações conhecidas;
        falha se surgir nova; avisa quando uma é corrigida). Roda via `python3` ou container:
        `docker run --rm -v "$PWD":/repo -w /repo python:3.11-slim python infra/check_config_invariants.py`
  - [x] **F1.1a** ✅ (2026-06-11) `seed.py` não escreve mais Redis: removidos `seed_redis()` + helper
        `RedisConn` (redundante — routing-engine popula `pool_config:{id}` e `{tenant}:pools` via
        `registry.changed`→`save_pool_config`). Guard: `seed_redis_write` saiu do allowlist.
  - [x] **F1.1b** ✅ (2026-06-11) `seed.py` aposentado: `channel_endpoints` migrados p/ YAML +
        `RegistrySyncer._sync_channel_endpoints` (corrige `label`→`display_name`); agent_types eram mortos
        (entidade removida); serviço `demo-seed` removido do compose; seed.py vira stub. Guard zerado
        (`pools_double_source` resolvido). **FASE 1 COMPLETA — guard 0/0.** Ver CHANGELOG.
  - [x] **F1.2** ✅ (2026-06-11) Precedência env×config (rigoroso, config-api vence):
        `attachment_expiry` — channel-gateway lê `{tenant}:config:webchat:attachment_expiry_days` do
        config-api (helper `resolve_attachment_expiry_days`, 4 adapters) + env removido + teste.
        `instance_ttl` — env removido (routing-engine usa default 30s da spec; tunable→config-api se preciso).
        Guard 3→1 (detecção por assignment ativo). Ver CHANGELOG.
  - [ ] **F2** Migração por domínio (read-path-first): pools (UI, `pool-config-surface.md`) → TTLs → hooks → masking → ABAC/users → evaluation/pricing → defaults hardcoded
    - **F2-pool (UI de pool)** — fatiado por grupo de campos. Decisões de modelagem 2026-06-12 em
      `pool-config-surface.md` § Decisões: combos referenciam **pool_id** (não skill, estável a versões);
      Transfer ≠ @mention (listas separadas); `max_concurrent_sessions` e `webhook_skill_id` ficam fora
      do drawer (este último é config de canal); `supervisor_config.enabled` não exposto.
      - [x] **F2.A** ✅ (2026-06-12) Hooks (on_human_start/on_human_end/post_human) — `HookListEditor` no
            PoolsPage + tipos + i18n. Backend já persistia. Ver CHANGELOG.
      - [x] **F2.B** ✅ (2026-06-12) Transfer (`escalation_pools`, merge-safe em supervisor_config) +
            @mention (`mentionable_pools`, lista alias→pool) — `PoolListEditor`/`MentionListEditor`, seções
            separadas no drawer. Backend já persistia. Ver CHANGELOG.
      - [x] **F2.C** ✅ (2026-06-12) Tipo & Capacidade: `agent_kind` (Select inferido/human/ai) +
            `session_reservation`; aviso queue⇒human; `registry.ts` propaga 422 (Σ≤C) ao banner.
            `max_concurrent_sessions`/`webhook_skill_id` fora por decisão. Ver CHANGELOG.
      - [x] **F2.D** ✅ (2026-06-12) **DISSOLVIDA por fonte única** — nada a expor no pool. `evaluation`/
            `evaluation_template_id` são donos de **Quality/Campaigns** (evaluation-api; o `pool.evaluation`
            do rules-engine é caminho legado/dormente — `on_pool_config` nunca é chamado). `agent_groups`
            é dono do **módulo Groups + JWT** (`supervised_groups[]`, Arc 9). Expor qualquer um violaria o
            invariante de fonte única. Ver `pool-config-surface.md` § Decisões/Gap.
            *Cleanup futuro (opcional): remover o caminho dormente `evaluation_sampler`/`on_pool_config`
            do rules-engine, ou religá-lo só se a campanha não cobrir.*
      - [x] **F2.E** ✅ (2026-06-12) Deploy — **RESOLVIDO (decisão: nada no pool)**. Investigação confirmou
            consumo ponta a ponta: `PUT /slots/next`→`promote` (next→current) publica `registry.changed` →
            orchestrator-bridge `bootstrap.request_refresh()` → `_build_desired_from_deploy` lê
            `deployed_skill_id`/`deployed_max_concurrent_sessions` do `GET /v1/pools` e provisiona instâncias.
            Dono = tela Fluxo→Deploy; por fonte única, não se duplica no drawer de pool. **F2-pool COMPLETA.**
    - **F2-TTL (TTLs/timeouts env×config)** — §8 item 2.
      - [x] **ws_auth_timeout** ✅ (2026-06-12) `resolve_ws_auth_timeout_s` lê `webchat.auth_timeout_s` do
            config-api; webchat + webrtc (foldado, sem a constante `_AUTH_TIMEOUT_S` hardcoded) usam o
            resolver; env `PLUGHUB_WS_AUTH_TIMEOUT_S` removido; guard ganhou `env_dup_ws_auth_timeout`
            (0/0). Ver CHANGELOG.
      - [x] **Item 7 (cat. C)** ✅ (2026-06-12): 7a `VITE_DEFAULT_POOL` era env morto → removido. 7b
            `EVALUATOR_POOL`+`REPLAY_SPEED_FACTOR` → config-api `evaluation` (session-replayer lê via HTTP;
            consertados de passagem: CONFIG_API_URL 3500→3600 + ausente no compose, `?tenant_id=` faltando,
            default errado `avaliador_qualidade`). `PLUGHUB_ANALYTICS_OPEN_ACCESS` fica (flag de demo).
            `webrtc._AUTH_TIMEOUT_S` é só default (ok). **Categoria C fechada.** Ver CHANGELOG.
      - [x] **Item 5 (ABAC/users)** ✅ (2026-06-12): `modules.yaml` = catálogo (auth-api carrega no startup);
            `seed_auth.py` provisiona users via API. Bug: `module_config` do seed drifted do catálogo
            (módulo `analytics` inexistente, `relatorio` vs `report`, `billing.view` vs `visualizar`) → 422
            → demo users sem ABAC. Realinhado ao `modules.yaml`; `set_module_config` falha em 422. Ver CHANGELOG.
      - [ ] **Item 6** seeds `seed_evaluation`/`seed_pricing` → bootstrap idempotente via API (liga à Fase 3).
            **Estacionado (2026-06-12): atacar junto da revisão dos módulos evaluation/pricing.**

  **▶ ARCO: Config HTTP Propagation** (aberto 2026-06-12) — `docs/arcos/config-http-propagation.md`.
  Achado durante o masking: o padrão "config-api vence via leitura direta do Redis" **nunca funcionou**
  (chave `{tenant}:config:...` nunca escrita; cache `plughub:cfg:...` é TTL). **F1.2 e F2-TTL eram
  latentes** (sempre default) — consertados pela Fase 1. Padrão-alvo = HTTP-backed cache
  (Session/RoutingConfigCache).
  - [x] **Fase 1** ✅ (2026-06-12) channel-gateway `WebchatConfigCache` (HTTP + config.changed); resolvers
        leem do cache; `config_api_url`/`PLUGHUB_CONFIG_API_URL`; testes reescritos. Conserta F1.2+F2-TTL.
  - [x] **Fase 2** ✅ (2026-06-12) mcp-server masking via HTTP (`GET /config/masking`) + seed
        `masking.context_rules` global + aposentado JSON órfão e `saveContextMaskingConfig` dead-code.
        Fecha o item 4 "masking" do §8. Ver CHANGELOG.
  - [x] **Fase 3** ✅ (2026-06-12) **ARCO COMPLETO**. 3b: `authorized_roles` migrado para HTTP
        (`loadAccessPolicy` + cache; `saveAccessPolicy` dead-code removido). 3c: creds
        `{tenant}:config:sms|whatsapp|voice:*` + `webchat:jwt_secret` são **secrets exemptos** (sem writer;
        env-first; documentado). 3a: guard `config_cache_direct_read` (falha em leitura direta de
        `plughub:cfg:*` fora do config-api; 0 ofensores). Ver CHANGELOG.
  - [ ] **F3** Bootstrap idempotente único (substitui `infra/seed/*.py` + YAML-fonte; só via APIs).
        **Nota (2026-07-02)**: distinto do fix de DDL do agent-registry (`db push` → `migrate deploy`
        auto-detectado, ver CHANGELOG "agent-registry — bootstrap seguro") — aquele resolveu o **schema**
        (risco vivo, já causou perda de dados 2x); esta F3 é sobre a **camada de seed data**
        (`infra/seed/*.py` dispersos), que já é seed-if-absent em todos os stores (sem bug vivo, arquitetural,
        baixa urgência — ver `docs/arcos/config-consolidation.md` §9).
  - [ ] **F4** Política de env vars (segurança) — inventário final
  - **Transfer (8.1/8.2)** acima é a primeira fatia concreta da F2-pools (escalation_pools).
  **Nota técnica F10.3 — contexto de atribuição para `survey_record(grain=segment)` (recon 2026-06-10):**
  o que o skill já tem vs. o que falta para chamar `survey_record` com atribuição:
  · `session_id` — **disponível** à YAML como built-in `$.session_id` (`interpolate.ts` `resolveJsonPathRef`,
  junto de `tenant_id`/`customer_id`/`instance_id`). Logo `grain=session|workflow|journey` é direto.
  · `segment_id` do PRÓPRIO agente — o bridge **já passa** no `/execute` (`activate_native_agent`
  `payload["segment_id"]`, main.py ~465) → `StepContext.segmentId`; usado em `@segment.*` e escritas
  `scope: segment`. **Exposto como built-in `$.segment_id`** ✅ (2026-06-11, follow-ups A item 1) —
  `resolveJsonPathRef` (`segment_id: ctx.segmentId` no evalContext); o skill já lê e passa à tool.
  · segmento de OUTRO agente (caso NPS-sobre-o-humano no `on_human_end`): o `segment_id`/`agent_key` do
  ALVO vivem no `hook_conf` (5º campo) — no **bridge**, não no ctx do agente de pesquisa. Cutover precisa
  o bridge **injetar no ctx** (ex.: `session.surveyed_segment_id` + `session.surveyed_agent_key`) antes
  de disparar a pesquisa, OU passar via metadata do trigger. Esse é o real trabalho de atribuição do
  cutover; sinal de segmento "sobre si mesmo" só precisa do `$.segment_id` exposto.
  ContextStore NÃO guarda registro dos segment_ids do contato — só namespace por segmento
  `segment.{segmentId}.*` (precisa saber o id) e `session.*_participant_id` (participant, não segment).
  **Pipeline de avaliação (descoberto na F2, 2026-06-07)**: a cadeia Arc 3/6 estava DORMENTE —
  `conversations.session_closed` sem produtor (adicionado ao bridge), persister sem self-healing de
  schema, `EVALUATOR_POOL` apontando p/ pool inexistente, consumer do routing filtrando `event` em
  vez de `event_type`, `SKILL_FLOW_SERVICE_URL` ausente no compose, flow do avaliador sem mount no
  container, e **avaliador sem identidade** (session_token/participant_id nunca injetados).
  Test-grade: `agente_avaliacao_v1` ganhou step `agent_login` inicial (opção A — token próprio).
  **Visão final (decisão 2026-06-07)**: o avaliador deve poder rodar a qualquer momento; na versão
  definitiva é disparado pelo **calendário** na data/hora da agenda da campanha do módulo quality
  (campo `schedule` JSONB já existe em `evaluation.campaigns`), recebendo como parâmetro o
  `session_id` a avaliar — substituindo o gatilho incondicional do Persister por
  agendamento+amostragem da campanha. Vira arco próprio quando priorizado.
- **Fase 3 — migrar provisionamento do demo para Config + Deploy** (elimina YAML/agent_type):
  - **3b / 3a / 3c / 3d-parcial — concluídas** — ver `CHANGELOG.md` (2026-05-31, 2026-06-01)
    e `docs/arcos/instance-bootstrap.md`. Pools IA migrados; `mention_commands` via embed no
    flow; slots vêm do `deploy:` de cada pool (boot limpo OK); agent_types IA aposentados do
    YAML (só o human resta, prune limpa o registry); reconcile deploy-only; hack
    `_applyMaxConcurrentSessions` e builder legado `_build_desired_state` removidos.
  - **Fase C — rename em massa DESCARTADO** (1198 ocorrências/136 arquivos, semanticamente
    errado p/ humano); `agent_type_id` permanece como carrier. Re-escopada em C1/C1b/C2/C3:
    - **C1 ✅** (2026-06-01): identidade do agente humano por `user_id`/`user_login` (login)
      nos segments — threading platform-ui→mcp-server→routing-engine→bridge→analytics; colunas
      no ClickHouse; exibição na lista e detalhe de Analytics/Sessions. Ver `CHANGELOG.md`.
    - **C1b-A ✅** (2026-06-01): Analytics/**Agents** — `_fetch_agent_performance` agrupa humano
      por `user_id` (display `user_login`), IA por `flow_id`; abas Human/AI com tabela de
      performance própria e KPIs filtrados. Ver `CHANGELOG.md`.
    - **C1b-B ✅** (2026-06-02): daily trend por identidade — `_fetch_agent_performance_daily`
      reescrito para ler `segments` direto (humano por `user_id`, IA por `flow_id`), sem
      depender da MV `mv_agent_performance_daily` (que colapsa humano por `agent_type_id`);
      `AnaliseAgentesPage` filtra `tabDailyRows` por `agent_type` por aba. Fix colateral: stroke do
      TrendChart usava `var(--color-*)` inexistente → linhas invisíveis (bug pré-existente mascarado
      enquanto o endpoint daily não trazia dado) → trocado por hex dos tokens. Ver `CHANGELOG.md`.
      Pendente derivado → **Fase 1b** (availability/pauses vazio no humano). **Correção 2026-06-07**:
      "outcome humano = 0%" era premissa errada — o segmento humano **grava** outcome, mas é
      **placeholder** (Console hardcoda `resolved`/`abandoned`; ClickHouse: 24 resolved / 12 abandoned
      / 19 NULL em 55 segs, `issue_status` 0/55). Disposição real em `session.wrapup.classificacao`
      (ContextStore). Tratamento → Fase F1 da bancada (`docs/arcos/analytics-agents-workbench.md` §13).
    - **C2/C3/C4 ✅** (2026-06-01): entidade `AgentType` **REMOVIDA** (tabelas `agent_types` +
      `agent_type_pools` dropadas via `prisma db push`). As UIs de CRUD eram código morto (não
      roteadas) → deletadas sem migração. mentionable-agents/delegation/agent_login repontados
      p/ deploy slots/skills. Ver `CHANGELOG.md`.
    - **Cleanup residual** (inofensivo, dead code — varrer quando der): `_sync_agent_type`/
      `_prune_agent_types` (registry_syncer.py, sem chamador); Path A `elif framework=="human"`
      (main.py, inalcançável); `AgentTypeSchema` (@plughub/schemas) + `validators/agent-type.ts`
      órfão. Testes do agent-registry que referenciavam agent_type foram deletados; revisar a
      suíte se reativar CI.

---

## Governança de Capacidade — contratado como fonte única *(✅ ARCO CONCLUÍDO 2026-06-05)*

Nasce da validação do fechamento Fase 2 Pools: contratado não governa config nem
runtime (Σ reservas pode exceder C / shared negativo; quota Redis documentada mas
inexistente; demo deploya 295 vs 25 contratados sem alerta). **Modelo fechado** em
[`docs/arcos/capacity-governance.md`](docs/arcos/capacity-governance.md): C
(pricing) é fonte única; **recursos criados no momento do uso** → gate primário na
criação (instância IA on-demand, humano = concorrentes logados) contra o C vigente;
declaração no flow/deploy validada no deploy; Σ reservas ≤ C e shared ≥ 0 (zero ok,
negativo nunca); redução de C sempre aceita com revalidação + alerta de
não-conformidade (nunca bloqueia); P (alocado) vira medidor de consumo do contrato
(UI: C × alocado × saldo). Absorve a dívida pricing→quota Redis registrada na
Fase 2. Pendente de implementação: ver § Pendente do spec.
**Item 1 ✅** (2026-06-04, ver `CHANGELOG.md`): quota sync no pricing-api —
mutações de resources gravam `{t}:quota:max_concurrent_sessions` (C = ai+human,
base + reservas ativas); `sync_all` no boot; o gate já existente da admissão
híbrida (`shared = C − Σ reservas`) passa a armar de verdade. `pricing.md`
§ Quota Side Effects corrigido (descrevia integração inexistente).
**Item 3a ✅** (2026-06-04, ver `CHANGELOG.md`): agent-registry valida
`Σ session_reservation ≤ C` no POST/PUT de pool (422 só em aumentos; reduções
sempre passam; sem C → fail-open) + `GET /v1/pools/capacity/conformance`
(conformidade derivada, revalidação implícita on contract-change).
**Item 4 ✅** (2026-06-04, ver `CHANGELOG.md`): aba Capacidade na BillingPage —
contratado × alocado × saldo + reservado/shared com alertas de não-conformidade
(reservas > C; alocado > C). Restam: 3b (Σ dos deploys ≤ C), 2 (gates por
tipo), 5 (aba Analytics contratado-cêntrica) e 6 (demo coerente).
**Item 3b ✅** (2026-06-04, ver `CHANGELOG.md`): Σ declarada nos deploys ≤ C
validada no PUT slots/next + promote (rollback isento; reduções passam;
helper `lib/capacity.ts` compartilhado com 3a).
**Itens 5+6 ✅** (2026-06-04, ver `CHANGELOG.md`): aba Capacidade
contratado-cêntrica (KPI Alocado como diagnóstico) + `pricing-seed` do demo
(ai 300 + human 10 → C=310, não-destrutivo). Resta do arco: 2 (gates por
tipo) e 7 (UX do available).
**Item 2 / Etapa 1 ✅** (2026-06-05, ver `CHANGELOG.md`): `agent_kind` ponta a
ponta (schemas+Prisma+backfill+rotas+routing+YAML) + quotas por tipo
(`{t}:quota:capacity:{ai_agent|human_agent}`) + decisões de tipagem fechadas
no spec (queue_config⇒human; fila atendida=ai cobrável; tier grátis = fila de
sistema, arco futuro).
**Item 2 / Etapa 2 ✅** (2026-06-05, ver `CHANGELOG.md`) — **item 2 completo**:
gate humano (logins concorrentes ≤ C_human + kind do pool no registerHumanAgent,
`login_denied` com toast no Console), gate IA (sessões em pools ai ≤ C_ai na
admissão, cause `quota` → demanda reprimida), recurso×kind (deploy em pool
human → 422; login humano em pool ai → negado). **Resta do arco: só o item 7**
(UX do available físico × admissível).
**Item 7 — design fechado 2026-06-05** (ver § 7 do spec): dois números
(físico/admissível ⊕), organização Reservados × Compartilhado × Fila gratuita
com donuts ("total e como está sendo consumido") + tiles do pipeline; HASH
`{t}:admission:shared_pools` para atribuição exata do shared. Execução:
**7a ✅** (2026-06-05, ver `CHANGELOG.md`): HASH shared_pools (atribuição exata)
+ agregador no /v1/operational/pools (admissible, regimes, tiers, summary) +
Monitor/Pools com tiles/donuts/seções + tiles no Monitor/Sessions.
**7b ✅** (2026-06-05, ver `CHANGELOG.md`): sampler amostra admissão →
admitted_peak + linhas __reserved__/__shared__/__buffer__ → bloco admission
no occupancy → aba Capacidade com "Admissão no tempo" e "Sala de espera
gratuita no tempo". **ITEM 7 COMPLETO — ARCO CONCLUÍDO.** Verificações na
validação do 7b: segmento sintético no detalhe de Sessions; nenhum `system`
em Analytics/Agents.

---

## Fila de sistema — tier gratuito *(✅ ARCO CONCLUÍDO 2026-06-05)*

**Spec/ADR**: [`docs/arcos/system-queue.md`](docs/arcos/system-queue.md).
Recon 2026-06-05 (a armadilha de sempre, na direção boa): a fila muda está
**majoritariamente viva** — ledger ZSET, aviso de espera ao cliente (mantido no
render v2), drain-on-ready, `queue_max_wait_default_s`, evento `queued` →
analytics. O arco real é bem menor que o esboço supunha. **Decisões fechadas**:
(1) isenção de C libera os buckets de admissão no enqueue mudo (re-admissão
natural no drain; C cheio → re-enfileira); (2 revisada) teto TOTAL do tenant —
`max_queue_total` no Config API + SET `{t}:queue:unadmitted` (SCARD = ocupação;
sem teto por pool; vizinho barulhento = refinamento futuro), estouro = outage
causa NOVA `queue_full`; (2b) **overflow**: C esgotado em pool humano cai na
fila muda gratuita em vez de rejeitar na porta (rejeita só com fila cheia);
(3 superada na implementação) saídas da fila muda viram SEGMENTOS SINTÉTICOS
`role=queue` (handoff/abandoned) — zero tópicos novos, zero dual-source, o
relatório Fase D conta fila muda sem mudar; (4) resta só tier da fila por pool;
(5) updates de posição = v2 opcional.
**Fase A ✅** (2026-06-05, ver `CHANGELOG.md`): isenção de C + overflow +
proteções (queue_max_total, max_wait por canal, queue_full) + segmentos
sintéticos + backstops + fixes da validação (headroom nos drains, dedupe do
aviso, release imediato no contact_closed). **Fase B ✅** (2026-06-05): causa
queue_full na demanda reprimida + tier da fila (Atendida/Sistema) na aba Fila.
**ARCO CONCLUÍDO** — item 7 do capacity-governance destravado.

---

## G7 — Decoupling segment-end × contact-close *(arco aberto)*

Spec em [`docs/arcos/g7-segment-contact-decoupling.md`](docs/arcos/g7-segment-contact-decoupling.md).
`on_human_end` está acoplado a `_trigger_contact_close` (conflação camadas 1/3). Entregue:
Fase 0 (classificador `_has_continuation`) + branch `agent_transfer` (transfer funcional — Mudança 9) +
**Slice A ✅** (wrap-up multi-humano: identidade de participante por-segmento — Mudança 10 + ADR
`adr-participant-identity-single-source`; resolve o gap (2) menu-routing do sub-arco multi-humano).
**Slice B ✅** (wrap-up no transfer — hook type `segment_wrapup`, fim-de-segmento sem armar close; Mudança 11).
**Fase 3 ✅ COMPLETA** (2026-06-13): **3a** (close governado por `_has_continuation` + marcador
`session:closed` condicional, parity-preserving single+transfer), **3b-i** (`on_contact_end` no schema +
cutover YAML + `infra/migrations/g7_nps_to_on_contact_end.py` p/ pools de DB + dispatch no bridge nos 4
sites; sem `arm_close` — wrap-up via `on_human_end` e NPS via `on_contact_end`, ambos armam `posatt`;
completion handler genérico inalterado), **3b-ii** (editor de `on_contact_end` na UI de Pools + i18n;
fecha o invariante UI-editable). Invariante de posse: dono = `primary` corrente, posse só move via
`transfer`; `task`/`assist`/`delegate` são `specialist` que volta ao chamador. Validado E2E (single ×2 +
transfer A→B com NPS só em B, incl. pool migrado `humanoxxx`). Mudança 12/13 + g7 §10.

**Sub-arco multi-humano** (modelo **peer / Teams-like, kind-agnostic** — invariante revisada g7 §10/§11;
bloqueante p/ Fase 1). Raiz do §8.1 = identidade de participante em campo de escopo-sessão. Anchor de
ciclo de vida = **último agente com I/O ao cliente** sai (humano ou IA); `primary`/posse = papel
(analytics + NPS), não âncora; sem sucessão/owner-lifeline. Fatias:
- **Slice 1 ✅** (2026-06-13, +1b) — identidade por-participante no close (Console envia `instance_id`;
  mcp-server usa `body`; bridge lê pool/agent_type por-instance via `participant_meta`). Cada humano
  encerra seu segmento, com seu pool; contato fecha quando o último sai, em qualquer ordem.
- **Slice 2′ ✅** (2026-06-13) — wrap-up por peer humano: `other_human_active` dispara `segment_wrapup`
  para o humano que sai (incl. não-último). É a **Fase 1**. Limitação: `human_seg` keyed por pool (2
  humanos no mesmo pool colidem); customer-disconnect multi-humano → Slice 4′. Mudança 14 / g7 §11.
- **Slice 3 ✅** (2026-06-13) — fan-out msg humano↔humano (gap 1): ramo normal do agent-WS publica em
  `agent:events` + self-skip no forward. Mudança 15 / g7 §11. (Polish: atribuição-por-nome do remetente.)
- **Slice 4′ Item 1 ✅** (2026-06-13) — bridge desfaz `session:closed` em `other_human_active` (mcp-server
  segue setando síncrono; fecha o vazamento do §4).
- **Slice 4′ Item 2 — wrap-up por peer no customer-disconnect multi-humano (RETOMADO 2026-06-14, fatiado).**
  Investigação reconfirmou o nó frágil (path customer-disconnect lê 1 pool do `meta`; `segment_wrapup` por
  humano de um único evento esbarra em `hook_pending` SET + `posatt` não-armado → exige contabilidade
  aditiva). Decisão: colisão "mesmo pool" é operacionalmente inexistente (1 agente/pool); fan-out endereça
  por `instance_id`. Ver g7 §11 + `conference-mechanics.md` § Mudança 17.
  - **Fatia 1 ✅** (2026-06-14) — `human_seg:{pool}`→`{instance_id}` (dual-write + param `human_instance_id`
    em `fire_pool_hooks`; threading em 10 call-sites). Parity-preserving; validado E2E single + multi-humano
    pools distintos (`fallback=False`, zero cross-attribution). Levanta a limitação "mesmo pool" da
    Mudança 14/Slice 2′. Ver CHANGELOG.
  - **Fatia 2a ✅** (2026-06-14) — idempotência do close do agente: gate `SREM human_agents` atômico no
    topo do branch (`removed==0`→no-op), `SREM` redundante removido. Mata o double-processing (segmento
    fantasma + wrap-up duplicado). Validado E2E (não-regressão multi-humano agent_done; sem fantasma).
    Log `Duplicate/late agent close ignored`. Ver CHANGELOG + `conference-mechanics.md` § Mudança 18.
  - **Fatia 2b/3 ✅ (lado bridge) — fan-out implementado e correto; E2E bloqueado por gap-2** (2026-06-14):
    contador `contact_close_pending` + `close_arming:{conf}` + guarda no `_destroy_conference` + DECR/teardown
    na conclusão do `segment_wrapup`; customer_disconnect dispara `segment_wrapup(arm_contact_close)` por peer;
    `human_seg:{instance}` escrito no loop `customer_side`; `_contact_close_timeout_guard`. **Entrega/atribuição
    validadas** (2 human_seg WRITE, READs fallback=False, cada menu ao seu console). **NÃO fecha E2E** →
    gap-2 abaixo. Ver CHANGELOG + `conference-mechanics.md` § Mudança 19.
  - **Fatia 4 ✅ (2026-06-15)** — cleanup: logs `G7 Item1 human_seg` (READ + 2× WRITE) rebaixados a `debug`.
    Espelho `human_seg:{pool}` **mantido por decisão** — é fallback defensivo barato no `fire_pool_hooks`
    (~linha 1002, p/ sessões in-flight durante deploy); remover teria valor marginal num path frágil (close).

### Router — corrida de sobre-alocação de instância (concorrência) *(arco próprio, root-caused 2026-06-14)*
**É a causa-raiz REAL do bloqueio E2E da Fatia 2b/3** (o "gap-2 de menu" era sintoma). Cadeia confirmada:
(1) instâncias AI são **single-occupancy** — bootstrap cria N instâncias `max_concurrent=1` a partir de
`max_concurrent_sessions` (`instance_bootstrap.py:1008-1036`); (2) o consumer do routing processa inbound
**concorrente** (`main.py:149` `asyncio.create_task(_process_message)` por msg, sem serialização);
(3) `get_ready_instances`(`registry.py:161`)→`mark_busy`(`registry.py:639` `current_sessions += 1`) é
**não-atômico**, sem claim. → Dois inbound paralelos (ex.: fan-out de wrap-up) leem a mesma instância com
`current_sessions=0`, ambos a escolhem, ambos `mark_busy` `0→1` (**lost update**) → 2 sessões na MESMA
instância single-occupancy. **Visível** quando são 2 segmentos da MESMA sessão (chave de menu
`{sid}:{instanceId}` colide → inputs cruzam, menus expiram); **latente** p/ sessões distintas (só
desbalanceia carga / estoura capacidade em silêncio). **Afeta todos os pools sob concorrência.**
**Fix primário = alocação atômica** (claim que rejeita sobre-capacidade e re-seleciona). **Modelo escolhido
(decisão 2026-06-14): semáforo de contagem por-instância via SET de occupant_ids + Lua atômico** —
`claim`=SADD-se-SCARD<max, `release`=SREM, `current_sessions`=SCARD. Atômico **e idempotente** (occupant
repetido = no-op → cobre redelivery de agent_done). Por que não as alternativas: contador INCR/DECR no JSON
não é idempotente (double agent_done sub-conta); mutex grosso por-pool serializa o select+score lento e tem
fragilidade de TTL (expira no meio do trabalho → corrida volta; precisaria Redlock/fencing). A consulta
(`get_ready_instances`) é read-only e o `decide()` pontua TODOS os candidatos antes de escolher → não dá pra
"marcar na consulta"; a marca vem **depois**, atômica, com re-seleção do perdedor (otimista/CAS).
Fatias:
- **Fatia A — primitivas atômicas ✅ VALIDADA** (2026-06-14; `test_instance_semaphore.py` 5/5 contra Redis real,
  incl. 25 claims concorrentes em max=1 → 1 vencedor): `registry.py`
  ganhou `_instance_sessions_key`, Lua `_CLAIM_INSTANCE_LUA`/`_RELEASE_INSTANCE_LUA` e métodos
  `claim_instance`/`release_instance`/`instance_session_count`. **Aditiva** — nada chama ainda (zero mudança
  de comportamento). Teste de integração `tests/test_instance_semaphore.py` (Redis real, skippable): N claims
  concorrentes em max=1 → 1 vencedor; idempotência claim/release; teto multi-capacidade; claim×release sem
  lost update. **Gate**: `REDIS_URL=redis://localhost:6379 pytest test_instance_semaphore.py` verde.
- **Fatia B — wiring no `decide()` ✅** (2026-06-14): `route()` coleta candidatos pontuados → claim em cascata
  com re-seleção do perdedor (`-1`→próximo best); `_try_affinity` faz claim da instância de afinidade. occupant
  composto `"{session_id}::{conference_id}"` (confs da mesma sessão não dividem vaga). `mark_busy` sincroniza
  `current_sessions` do `SCARD` (não incrementa). **Absorveu a Fatia C**: `remove_conversation` usa
  `release_instance` (release por prefixo de sessão). Validado: suíte verde + 2 testes de re-seleção. Ver CHANGELOG.
- **Fatia C — release ✅ (foldada na B)**: `remove_conversation`→`release_instance` por prefixo. Resíduo opcional:
  `get_ready_instances`/snapshots passarem a ler `SCARD` direto (hoje leem o JSON mantido em sincronia pelo
  claim/release — funciona como hint; o claim é o gate atômico). Baixa prioridade.
- **Fatia D — gate E2E ✅ (2026-06-15)**: re-seleção + **instâncias distintas** provadas E2E (`router.claim ...
  claim=-1 — re-selecting`; `wrapup_ia-002`/`-018`), zero sobre-alocação. "Os dois wrap-ups completam"
  **validado** após a Camada 3 (Fatias A/A2; 2 runs verdes, `pushed=true` nos dois). **Arco do router
  concluído.** Residual opcional (baixa prioridade): "2 contatos simultâneos no mesmo pool → spread" não
  exercitado isoladamente.

### Camada 3 — isolamento de `pipeline_state`/lock por conferência ✅ *(resolvido 2026-06-15 — ver g7 §11 Item 2, conference-mechanics § Mudança 21)*
**Fechada.** Diagnóstico da Mudança 20 estava **errado para HEAD**: o bridge já sufixava `pipeline_session_id`
por `--seg--{segment_id}` (a evidência `5ea8dfae` era **build stale**). Bloqueios reais corrigidos:
**Fatia A** (chave de pipeline endurecida em `activate_native_agent`: `segment_id or instance_id or uuid`,
nunca `session_id` cru; fecha branch `--conf--` + YAML-fallback) e **Fatia A2** (isenção de hook no dedup
`conference:specialist:{pool_id}` que colapsava os 2 wrap-ups do mesmo pool numa corrida). Validado E2E 2×.
- **Follow-up ✅ RESOLVIDO (2026-06-15, Fatia 1 — hook-pool por segmento)**: `on_human_end`/`on_contact_end` do
  último/âncora passam a resolver o pool de `participant_meta:{instância que fecha}` (fallback `meta`), nos
  **dois** close paths (`agent_closed` `_pool_id_hooks` + `customer_disconnect` `_cs_pool_id`; cobre o
  **deferred** via stash). Validado E2E (admin último → `origin_pool=retencao_humano`; pré-fix `humanoxxx`).
  Ver CHANGELOG 2026-06-15 + conference-mechanics § Mudança 22.
- **Gaps remanescentes do modelo de hooks (follow-ups, baixa prioridade)**: (2) survey **customer-side
  por-segmento** (grão=segment NPS) não dispara p/ peers no fan-out — `segment_wrapup` reusa a lista
  `on_human_end` mas filtra `side=agent` (`main.py` ~938), então surveys customer-side só saem na âncora/
  primário; (4) binding **grão↔boundary** (skill em "contact ends" gravar `grain=session`) é convenção, não
  contrato; disparo **grão=journey** não plumbado (sem boundary de fim-de-journey) → F11. Convergir
  `on_human_end`(último)+`segment_wrapup`(peers) num mecanismo único de wrap-up por-segmento = higiene opcional.
- **Hardening opcional (gap-2 menu)**: chave de menu por `segmentId` como defesa-em-profundidade p/ pools com
  `max_concurrent>1` legítimo + 2 segmentos da mesma sessão. **Desnecessário** após a alocação atômica
  (concorrentes vão para instâncias distintas) + Fatia A (pipelines distintos). Encerrado salvo regressão.

### Latência do `@mention` de humano — RESOLVIDO (não é bug, 2026-06-15)
**Conclusão**: não havia latência anômala. O `@mention` de um pool cujo **agente humano ainda não logou** cai
na fila do contato (nenhuma instância `ready` no pool) e é entregue assim que o operador **faz login**
(`agent_ready` → `Queue drain: ... became ready`). Comportamento **esperado**. Confirmado: com o operador
logado, `@mention` → `Routed → human-…` em **~33 ms** (sem fila); sem login, aguarda na fila até o login. Os
"alguns segundos / nem sempre" eram o tempo até o operador (`humanoxxx`) logar — o que estava logado/servindo
era o `retencao_humano` (admin), daí a impressão de "coincidir com a escalação". **Sem ação.** (Mecanismo de
referência: convite a pool sem agente ready → `Contact persisted to queue` → drain no `agent_ready`; eventual
melhoria de UX — sinalizar no Console "convidando, aguardando login do agente" — é cosmética, não bug.)

**Sub-arco multi-humano: Slices 1/2′/3/4′ ✅; Item 2 ✅ (Camada 3, 2026-06-15).** Restam só os arcos próprios
abaixo (unificação de contabilidade; queda involuntária de humano) + o follow-up `_cs_pool_id` acima.

### Unificação de contabilidade de agente (kind-agnostic) *(arco próprio, proposta — diferido)*
Anchor "último agente customer-facing" hoje é aproximado por **4 chaves** com papéis distintos:
`human_agent` (flag → entrega inbound/guard), `human_agents` (SET → remaining/close/restore/participant_left),
`ai_agents` (SET → restore + leitura supervisor/bpm), `active_ai_specialists` (SET → defer/continuação).
Três dimensões misturadas: anexação × kind (entrega/restore/wrap-up) × estado (rodando). Alvo: HASH único
`session:{id}:agents → {kind, role, customer_facing, running}` do qual as 4 respostas são derivadas.
**Investigação 2026-06-13 — DIFERIDO**: é refactor **puro-interno** (não corrige bug; o modelo de 2 sets+defer
já aproxima o anchor), toca o caminho **mais frágil** (close) + consumidores cross-package (mcp-server
supervisor/bpm/server) e só é gateável por **paridade**. Alto custo/risco, payoff diferido (manutenibilidade).
**Decisão**: fazer **oportunística** (quando um bug concreto justificar ou encostada em feature que já toque
essas chaves), não como refactor standalone. Heartbeat priorizado antes (valor real).
**Re-avaliação 2026-06-15 (pós Camada 3 + fan-out) — MANTÉM DIFERIDO**: as entregas recentes adicionaram
bookkeeping paralelo (`human_seg:{instance}`, `hook_conf`, `posatt`, `contact_close_pending`, `close_arming`)
**por cima** das 4 chaves, sem reestruturá-las; e os bugs corrigidos estavam no `session:meta` (last-writer) e
no dedup por `pool_id`, **não** nas 4 chaves de contabilidade (que seguem aproximando o anchor corretamente).
Nenhum dos 2 gatilhos foi atingido. Argumento contra reforçado: close path recém-estabilizado (2 runs verdes),
refactor gateável só por paridade com raio cross-package (mcp-server supervisor/bpm/evaluation). Mapa atual:
`human_agent` flag (~10 sites, hot path entrega) · `human_agents` SET (~10: remaining/restore/participant_left/
fan-out) · `ai_agents` SET (~8: restore no close) · `active_ai_specialists` SET (~7: defer G2). Único
incremento baixo-risco se encostar no path de entrega: derivar `human_agent` de `SCARD(human_agents)>0` — mas
há aresta (flag setada mesmo com `instance_id` vazio em `activate_human_agent` → não é 1:1). Manter HASH único
oportunístico.

### Detecção de queda involuntária de humano *(arco próprio)*
Humano que cai (disconnect/crash) deixava o contato órfão (gap G4). Alvo: drop → re-rota ao pool do dono
(posse re-estabelecida por alocação, não promoção); contato vivo sob os agentes customer-facing restantes.
- **Slice 1 ✅** (2026-06-13) — detecção via `ws.close`+grace (mcp-server publica
  `contact_closed(agent_disconnect)` p/ sessões onde o humano ainda está em `human_agents`) + bridge:
  `remaining>0` sem peer wrap-up; `remaining<=0` re-rota `conversations.inbound` ao `_ha_pool`. Mudança 16.
- **Slice 2 ✅** (2026-06-13, hardening) — pong-tracking: ping de PROTOCOLO (`ws.ping`, auto-respondido
  pelo browser) + evento `pong` reseta `isAlive`; sem pong num ciclo de 30s → `ws.terminate()` dispara
  `ws.close` → grace → `agent_disconnect` (Slice 1). Fecha o "drop sujo" (meia-conexão que não emite
  `close`). Mudança 16 (adendo). **Arco heartbeat completo.**

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

## F11 — Pesquisa multi-grão outbound → superseded pelo módulo Customer Surveys

> **Consolidado (2026-07-02):** o planejamento de orquestração que este item pedia ("quando/como cada
> grão dispara") está **fechado** em [`docs/arcos/customer-surveys.md`](docs/arcos/customer-surveys.md)
> §5 (gatilho decidido no skill, não na plataforma) + §12 (plano de fases S1–S11). O **S11** daquele
> plano é exatamente o "NPS/PMF relacional agendado + grão journey E2E" que este F11 apontava como
> pendência. Não há mais planejamento em aberto aqui — o que resta é **implementação** das fases S1–S11
> (nenhuma iniciada). Ver também `CLAUDE.md` § Pending → "Customer Surveys" e "Histórico de contatos do
> cliente" (capacidade transversal, §20 do mesmo doc, spec própria em `docs/arcos/customer-contact-
> history.md`). F5 inline (grão segmento) segue ✅ concluído — a riqueza "N sinais por agente" mora no
> módulo Surveys (grãos outbound), não no inline.

---

## Scheduler central de timers *(diferido — ADR aceito)*

Consolidar os timers espalhados (timeout de suspend/delegate no channel-gateway,
`_hook_timeout_guard` no bridge, timeout de `collect`) num módulo único de scheduling:
sorted-set de deadlines (`ZADD`/`ZRANGEBYSCORE`) + poller único + evento `timer.fired`
com os donos reagindo; calendar-api permanece o engine de prazo (calcula o *quando*, não
dispara). Primeiro corte funcional já existe (`run_timeout_scanner` no channel-gateway).
Decisão e mecanismo em [`docs/adr/adr-timer-scheduler.md`](docs/adr/adr-timer-scheduler.md).

---

## Agent-registry — unificar binding skill↔pool (3→1) *(proposta — concern do registry)*

Origem: discussão do doc de avaliação (`docs/arcos/arc-evaluation-metrics-methodology.md` §IV.3),
scoped-out de lá por ser refactor do agent-registry, não de avaliação.

**Achado**: a associação skill↔pool aparece em **três** lugares no `schema.prisma` — `PoolSkillSlot`
(slot do pool), `SkillVersionSlot.pool_ids` (previous/current/next) e `SkillDeployment.pool_ids`
(histórico de deploy). Risco de divergência entre eles.

**Alvo**: uma relação **autoritativa** do binding atual (slot) + o histórico como **append-log** das
mudanças de slot (o `SkillDeployment` deixaria de precisar do próprio `pool_ids`). **Pré-trabalho**:
auditar os 3 modelos + todos os readers (routing/alocação no caminho quente, RegistrySyncer, lente
deploy do Arc 6 Fase 2, `GET /v1/pools/:id/deployments`) antes de cravar o modelo unificado.

---

## Skill hot-reload via YAML em disco sem restart *(deferred — dev/demo only)*

**Fluxo editor → deploy já funciona**: `POST /v1/skills/:id/deploy` → `publishRegistryChanged` → bridge invalida `_skill_flow_cache` → próxima execução busca conteúdo atualizado do agent-registry. Nenhuma mudança necessária para este caminho.

**Gap**: edição direta de arquivo YAML em disco (dev/demo) ainda requer `restart orchestrator-bridge` para o RegistrySyncer re-ler e fazer PUT para o agent-registry. A solução correta é um endpoint `POST /admin/skills/sync` (ou handler de `registry.changed` com `source: disk`) no bridge — chama `RegistrySyncer._sync_skills()` → PUT → `registry.changed` → cache invalidado. Deve ser acionado pelo processo de deploy YAML (CI/CD, script), não pelo editor.

---

## Arc 19 — Modelo Unificado de Sessão: Workflow como Canal Webhook

Spec em [`docs/arcos/arc19-unified-session-model.md`](docs/arcos/arc19-unified-session-model.md). Elimina a dualidade contact/workflow tratando workflows como canal `webhook` na channel-gateway.

- **Fase A** ✅ — WebhookAdapter + `channel_type: webhook` + routing engine (2026-05-28)
- **Fase B** ✅ — Status `suspended` + TTL extension + hash Redis `resume_tokens` + stream events (2026-05-28)
- **Fase C** ✅ — orchestrator-bridge: `persistSuspendWebhook` wired in skill-flow-service; `_handle_webhook_session_resumed`; `process_inbound` http param (2026-05-28)
- **Fase D** ✅ — workflow-api: proxy trigger/resume → channel-gateway; 410 Gone para persist-suspend/complete/fail/cancel/collect; `business_hours` + `calendar_id` em `persistSuspendWebhook` (2026-05-28)
- **Fase E** ✅ — Monitor e Analytics unificados: filtro `channel_type`/`webhook` badge/`suspended` badge; Events tab (Arc 12); status filter analytics end-to-end (2026-05-28)
- **Fase F** ✅ — Eliminação Journey (Arc 10/16/17 → CHANGELOG); platform-ui limpa; Arcs 10/16/17 retired (2026-05-28)

**Arc 19 completo.** Cleanup residual (infra): remover `workflow.events` topic do Kafka e arquivar o package `skill-flow-worker`.

---

## Arc 18 — Workflow Execution Trace *(DEPRECATED pelo Arc 19)*

A spec original em [`docs/arcos/arc18-workflow-execution-trace.md`](docs/arcos/arc18-workflow-execution-trace.md) está superseded pelo Arc 19.

**Por que deprecated**: todas as superfícies de Arc 18 dependem de entidades eliminadas pelo Arc 19 — `workflow-api` (deprecado Fase D), `Analytics/Processes` (eliminado, merge em Analytics/Sessions), `Analytics/Journeys` (eliminado com Journey na Fase F), rotas `/analytics/processes/:instanceId` e `/analytics/journeys/:journeyId` (desaparecem).

**O que sobrevive do conceito**: conforme documentado em `docs/arcos/arc19-unified-session-model.md` §Analytics/Sessions, a hierarquia correta é **lista de sessions → lista de segments → detalhe do segment**. Workflows webhook aparecem em Analytics/Sessions com `channel_type: webhook`; cada suspend/resume cria um segmento distinto; o padrão de navegação é idêntico ao de sessões normais (webchat, voice). Não há Trace tab separada — o usuário navega pelos segmentos da sessão webhook da mesma forma que navega pelos segmentos de qualquer outra sessão.

**Pendência real ✅** (constatada já implementada em 2026-06-04 — Fase E do delegate
entregou): `WorkflowTraceList` renderiza a lista ordenada de segmentos da sessão
webhook com numeração de ciclo, badge de tipo (intake/execução/specialist), status
por nó (live/outcome/closed), pool+timing e contadores de execuções/suspensões; a
navegação por canal real (Fase C do delegate) garante que sessão webhook sempre
passa pela lista antes do detalhe.

---

## Step `delegate` + MCP tool `workflow_resume` ✅

Padrão implementado completo. Componentes entregues:

- `skill-flow-engine/src/steps/delegate.ts` — executor do step
- `skill-flow-engine/src/engine.ts` — `persistDelegate` em `SkillFlowEngineConfig` + wiring em `_buildContext`
- `mcp-server-plughub/src/tools/workflow.ts` — MCP tool `workflow_resume`
- `channel-gateway/adapters/webhook.py` — `handle_delegate` (cria sessão-filho + ContextStore)
- `channel-gateway/main.py` — `POST /v1/channels/webhook/delegate` (antes de `/{skill_id}`)
- `e2e-tests/services/skill-flow-service/src/index.ts` — `persistDelegateFn` + `CHANNEL_GATEWAY_URL`
- `docker-compose.demo.yml` — `CHANNEL_GATEWAY_URL` + `CALENDAR_API_URL` no skill-flow-service
- `skill_portabilidade_demo_v1.yaml` v2.0 — usa `delegate` (sem notify/collect no workflow)
- `agente_confirmacao_portabilidade_v1.yaml` — agente de I/O de confirmação
- `infra/registry/tenant_demo.yaml` — pool `portabilidade_confirmacao`

---

## Webhook workflow trace — segmentos históricos sem origin_session_id *(deferred)*

A migração ClickHouse `_DDL_SESSIONS_MIGRATE_ORIGIN` adiciona a coluna `origin_session_id` à tabela `sessions`, mas sessões webhook criadas antes da migração têm o campo NULL. O `WorkflowTraceList` não vai exibir o segmento de entrada (intake) para essas sessões. Apenas sessões criadas após a migração terão o link correto.

Não requer ação — os dados históricos permanecem corretos para análise; apenas o link de rastreabilidade cross-session ficará ausente para sessões antigas.

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

## Fila de trabalho humano / dispatch pull + inbox no Console *(proposta — não implementado)*

Modo de despacho **pull** genérico no Routing Engine (operador puxa da fila) + inbox no Console, tendo a **fila de aprovação** como primeira especialização (revisão de processo montado por IA num passo anterior). Especificações em `docs/product/`:

- **Dispatch pull genérico** — [`docs/product/routing-pull-dispatch-spec.md`](docs/product/routing-pull-dispatch-spec.md). `dispatch_mode: push|pull` no `PoolConfig` (único toque de schema); reusa o sorted set de fila; claim atômico via `ZREM` (alocação concedida pelo routing — invariante preservada); lease TTL + auto-release event-driven (crash_detector); release re-enfileira pelos critérios do routing; ordenação por peso da fila + tags `session.queue.*` no ContextStore; respeita `max_concurrent_sessions`.
- **Fila de aprovação (especialização)** — [`docs/product/human-work-queue-aprovacao-spec.md`](docs/product/human-work-queue-aprovacao-spec.md). Item = sessão de workflow suspensa (delegate ao pool pull); pacote (form padrão + extensão + `decisions`); decisão volta pelo **retorno do delegate** (`output_as: step.id` já existe — sem schema novo); workflow principal roteia (`choice`); edição auditada.
- **Inbox no Console (UI)** — [`docs/product/pull-inbox-console-ui-spec.md`](docs/product/pull-inbox-console-ui-spec.md). Integrada ao atendimento (rail de filas piscando → lista → preview no centro → "Pull" na action bar); cor por SLA (verde/amarelo/vermelho); notificação via ciclo do heartbeat; gating de capacidade.

Liga com o **gate de promoção** homologação→produção (descritivo §20.1): promover vira um workflow com passo de aprovação.

**Status (2026-06-15):** plano consolidado em `docs/product/frente1-dispatch-pull-aprovacao-plano-consolidado.md`
(módulos + task list + esforço; decisões D1–D3 resolvidas). Sub-fatiamento da F1 (pull core) confirmado:
F1.0 (plumbing `dispatch_mode`) → F1.1 (branch `route()`) → F1.2 (claim atômico) → F1.3 (lease).
- **F1.0 ✅ (2026-06-15)** — `dispatch_mode: push|pull` (default push) ponta a ponta: `@plughub/schemas`
  `PoolRegistrationSchema`, agent-registry (coluna Prisma + migração + POST/PUT), routing `PoolConfig` +
  `kafka_listener`, **UI select** na PoolsPage (+ i18n). Aditivo. Validado (`teste_demo` → `dispatch_mode=pull`).
- **F1.1 ✅ (2026-06-15)** — branch no `route()` (pool pull → parqueia, pula `_allocate`, reusa caminho queued)
  + `_drain_queue_for_agent` e `_periodic_queue_drain` pulam pools pull. Validado: push byte-parity; pull
  parqueia (`Contact persisted to queue pool=teste_demo`) sem `Routed`/drain.
- **F1.2 ✅ (2026-06-15)** — claim atômico no Router: `work_task_claim` (`ZREM` 1-vencedor + `claim_instance`
  no semáforo do recurso + rollback se −1 + `mark_busy` + lease + publica `conversations.routed` → reusa
  bridge/Console) e `work_task_release` (lease off + `release_instance` + re-enfileira). Registry:
  `atomic_claim_dequeue`, `write/delete_claim_lease`. Testes `test_work_queue_claim.py` 5/5 + suíte 96 verde.
  Invocação (tool mcp-server) é F2.
- **F1.3 ✅ (2026-06-15)** — `claim_lease_s` no config-api (ns `routing`, 180) + `routing_config` + `Router`
  lê dele; branch pull do `route()` **deleta a claim lease** no re-parque. **Correção do desenho**: o
  crash_detector **pula humanos** → o auto-release de pull (humano) é **emergente**: desconexão (mcp-server WS
  lifecycle / arco "queda involuntária") → bridge re-roteia → `route()` parqueia (F1.1) + limpa lease → contato
  volta claimável + vaga liberada por `agent_done`/`release_instance`. **Diferido** (spec "sem sweep dedicado"):
  renovação da lease por heartbeat + sweeper de "conectado-mas-ocioso" (a inbox da F2 sinaliza melhor). Testes
  6/6 + suíte 96 verde. **Pull core (F1.0–F1.3) COMPLETO.**
- **F2** — tools mcp-server + API HTTP no routing + inbox no Console. Sub-fatiada:
  - **F2a-1 ✅ (2026-06-15)** — API HTTP no routing (`http_api.py`, aiohttp): `POST /v1/work_queue/{claim,release}`
    → `Router.work_task_claim/release`; `/health`; auth `X-Admin-Token` opcional; porta `ROUTING_HTTP_PORT`
    (3550). `main.py` injeta `router._producer` (liga claim routed **e** `queue.position_updated`, antes latente)
    + inicia o server. `aiohttp` no pyproject. Validado (health + claim wiring) + suíte 96 verde.
  - **F2a-2 ✅ (2026-06-15)** — tools mcp-server (`tools/work_queue.ts`): `work_queue_list` (Redis-direct: lê
    sorted set + `queue_contact`) + `work_task_claim`/`work_task_release` (`fetch` → API routing). Registrado
    nos 2 sites do `server.ts`; keys `queueContact`/`claimLease` no `infra/redis`. Build TS OK. **F2a completa
    (backend do pull pronto).** Validação funcional das tools = via F2b (a Console é o cliente MCP).
  - **F2b-1 ✅ (2026-06-15)** — rotas HTTP `/api/work_queue/{list,claim/:sessionId,release/:sessionId}` no
    mcp-server (Express, onde vivem `/api/agent_done` etc.) + `lib/work-queue.ts` compartilhado (a tool MCP e as
    rotas HTTP usam a mesma lógica). **Correção de rumo**: a Console usa rotas HTTP `/api/*`, não tools MCP — as
    tools (F2a-2) servem clientes MCP/IA. Validado (`/api/work_queue/list` → `{contacts:[],total:0}`).
  - **F2b-2a ✅ (2026-06-16)** — inbox mínima funcional: `PullInboxPanel` no rodapé da coluna de contatos do
    Console (`AgentAssistPage`), poll 4s de `/api/work_queue/list?pools=`, botão **Pull** → `claim/:sid` →
    contato vira atendimento normal (Serving). `dispatch_mode` em `PoolInfo`/`fetchPools`. Junto: **fix de
    propagação de `pool_config`** (`publishRegistryChanged` no POST/PUT de pool + `agent_kind`/`dispatch_mode`/…
    no `_pool_config_diverged.MANAGED`) — destravou login humano em pool human e toggle push↔pull. **E2E OK**:
    parqueia → lista → Pull → atende (bidirecional).
  - **F2b-2b-1 ✅ (2026-06-16) — preview/triagem read-only antes do claim** *(pedido do usuário)*: clicar na
    linha da inbox abre preview read-only no centro (conversa via `conversation_history` + abas Context/History
    via `supervisor_state`, ambos read-only por sessionId — **sem backend novo**), action bar "Atender (Pull)"/
    "Fechar", input oculto, poll 4s, sem cache ao trocar. Atender → claim existente → atende. **E2E OK**.
  - **F2b-2b-2 ✅ (2026-06-16) — polish**: cor por SLA nas linhas (verde<0.6 / amarelo≥0.6 / vermelho≥1.0 de
    idade÷sla_target do pool); idade ao vivo (tick 1s a partir de `queued_at_ms`); gating de capacidade
    (`atCapacity = contacts.size ≥ maxConcurrentSessions` do JWT) desabilita Pull/Atender + hint; auto-clear do
    preview quando o contato sai da fila (claim de outro / timeout). Reforça o rollback server-side `no_capacity`
    (F1.2) com feedback antecipado. **Frente 1 / Pull — completa (F1+F2).**
  - **F2b-2b-3 ✅ (2026-06-16)** — agrupamento da inbox por pool (cabeçalho recolhível nome+contagem; dentro do
    grupo mais-antigo-primeiro; pool_id sai da linha). Facilita localizar contatos por fila.
  - **F2b-2b-4 ✅ (2026-06-16)** — divisória arrastável entre contatos e fila pull: `flexBasis` da área de
    contatos controlado por drag (clamp 15–85%), persistido em `localStorage` (`plughub_pull_split_pct`).
    **Frente 1 / Pull — encerrada (F1 + F2 + polish).**

## Frente 2 — Avaliação campaign-driven (shakedown E2E)

> **STATUS (revisão 2026-06-26): efetivamente CONCLUÍDA — não é pendência de faxina.** O pipeline
> campaign-driven (S1/S2.1/S2.Q1) e a **consolidação Quality** estão ✅: as abas Trend/Comparison da página
> Quality foram removidas e **toda a comparação de qualidade vive no bench (Analytics→Agents)** + Quality
> Summary. A lente `deploy` (Arc 6 Fase 2) **P2 ✅ + P3 ✅** (ancorada no pool, validada no browser); o caminho
> do avaliador real foi validado (2026-06-17). **Só restam, ambos DIFERIDOS por decisão do usuário, não-cleanup:**
> (a) **P4** — eixo X por epoch/versão (hoje é tempo + markers); (b) ~~disparo do avaliador real pelo calendário~~
> **DESTRAVADO (2026-06-28)** — o combo de calendário da campanha estava vazio (422: faltava `organization_id` no
> GET; ver `docs/product/calendar-consolidation-and-trigger.md`). Combo corrigido → `evaluation_calendar_id`
> selecionável; o backend (`compute_expires_at` + dispatcher windowed T15) já respeitava a janela. (c) nits do
> bench (denominador do quality score, janela/período). Reabrir só se a observabilidade por deploy/versão virar
> requisito.

Decisão de arquitetura: avaliação é **sempre dirigida por campanha**, nunca pelo fechamento inline (modelo antigo,
removido). Janela de despacho = **calendário da campanha** (`evaluation_calendar_id`, sem campo novo); throttle =
capacidade do pool avaliador (`avaliacao_ia.max_concurrent_sessions`). Avaliar no fim do atendimento = opt-in via
pool hooks genéricos (sem campo dedicado).
- **S1 ✅ (2026-06-16)** — create de campanha/form destravado (pool_id espelha evaluation_pool_id; forms expõem
  form_id). Ver CHANGELOG.
- **S2.1 ✅ (2026-06-16)** — trigger inline removido (session-replayer) + payload `session_closed` enriquecido
  (bridge) + consumer de sampling na evaluation-api criando `EvaluationInstance(scheduled)` por campanha ativa.
- **S2.Q1 ✅ (2026-06-16)** — avaliador FAKE: `POST /v1/evaluation/admin/seed-synthetic` gera avaliações
  sintéticas (via ingest real) + NPS, p/ validar o módulo em volume sem o LLM. Botão na CampaignsPage. Corrigido
  bug `initial_state` no ingest (ramo ai_agent). Falta: chaves i18n `campaigns.seedSynthetic*` (en/pt-BR) +
  (opcional) backdating de datas e segmentos sintéticos p/ time-series/agent-comparison/epochs do bench.
- **S2.Q1b ✅ (2026-06-16)** — seeder enriquecido (datas espalhadas + segments sintéticos p/ atribuição de agente)
  → validou o **bench** de Agents (lentes quality / quality by dimension + NPS) com dado sintético.
- **Consolidação Quality ✅ (2026-06-16)** — abas **Trend** e **Comparison** da página Analytics→Quality
  **removidas** (estavam quebradas/redundantes; `quality-timeseries`/`quality-comparison` não retornavam dado
  sintético). Comparação de qualidade por agente/dimensão/tempo vive no **bench** (Analytics→Agents). Página
  Quality ficou só com **Summary**. `TimeseriesView`/`ComparisonView` viraram código morto (não removido).
  **Backlog (diferido)** — capacidades ÚNICAS das abas removidas, NÃO presentes no bench: (a) marcadores/
  comparação por **deploy-epoch** (Arc 6 Fase 2 — "deploy melhorou a qualidade?"); (b) **significância
  estatística** (N<30); (c) comparação de **períodos arbitrários A vs B**; (d) overlay multi-métrica num gráfico.
  Se a observabilidade por deploy virar necessidade, adicionar um modo "comparar fatias/deploy" ao bench.
  - **T16 ✅ (2026-06-19) — correção de verdade nas docs**: `arc6-phase2-observability.md` e o resumo do
    CLAUDE.md (§ Arc 6 Fase 2) afirmavam "implementado ✅", mas auditoria confirmou **gap total** (sem
    `analytics.deploy_events`, sem consumer `skill_deployed`, sem `deploy-timeline`/`quality-comparison`/
    `quality-timeseries`). Corrigidos para "NÃO implementado / proposta"; gap registrado no `## Pending` do
    CLAUDE.md. A base de qualidade finalizada do T11 (`evaluation_finalized` + `/reports/evaluations/quality`)
    é independente desta Fase 2. *(Demais "✅ falsos" da spec §19 — finalize/`result_state`/`evaluation_finalized`
    — foram de fato implementados nos T1–T11, então deixaram de ser falsos.)*
  - **Spec de implementação fechada (2026-06-19):** `docs/product/arc6-phase2-deploy-observability-spec.md`.
    Decisões: **entra como NOVA LENTE `deploy` no board de Agentes** (`/reports/agents/compare?lens=deploy`),
    NÃO tela/aba nova (consolidação 2026-06-16 — toda comparação no bench; views Trend/Comparison da Quality
    são deletadas no cleanup); deploy timeline via REST do agent-registry (sem tabela/consumer); qualidade =
    `evaluation_finalized`/Oficial; comparação de versão via **epochs como buckets + N/significância** (sem
    endpoint A/B). Chunks: **P2-A** REST deploys (helper) · **P2-B** lente `deploy` em `query_agents_compare`
    · **P2-C** front (LENSES+CompareChart) + cleanup. Backlog (fora): período-A/B arbitrário, overlay
    multi-métrica/agent_event, C3, NPS, export.
  - **P2 entregue — 1º corte §6 (2026-06-20):** P2-A/B/C implementados e validados no browser (ver CHANGELOG).
    A lente `deploy` existe no bench como **série DIÁRIA + `deploy_markers`** (Oficial). **Núcleo PENDENTE**
    (decisão do usuário: deixar como está e reavaliar): (1) **série por epoch/versão** (§4.1/D4) — eixo X =
    versões, ponto = qualidade média da versão, N por versão; hoje "v1 vs v2" é leitura manual via markers;
    (2) **média/multi-seleção** herdadas do board (§4.5/D3) são ruído numa lente de versões de UMA skill —
    avaliar remover/ocultar a média e focar single-skill quando o epoch entrar; (3) markers exigem
    `flow_id == skill_id` (§8) — no demo `sac_ia` (agent_type_id) ≠ `skill_atendimento_sac_v1` (skill_id),
    então só alinha quando o `flow_id` carrega o skill_id real. Seed de validação: `infra/test/seed_deploy_lens_demo.sh`.
  - **Decisão de âncora — POOL (2026-06-20, spec §11):** após walkthrough, a unidade da lente passa a ser o
    **pool** (par `(pool, skill)` colapsado), não o `flow_id`/skill. Motivo: `skill_id` é estável (deploy não
    muda o id; `version` é campo à parte; deploy é pool-centric via `PoolSkillSlot`+`SkillDeployment.pool_ids`),
    e **um skill pode rodar em vários pools** → âncora-skill mistura pools. Com pool: 1 curva por pool, deploy
    compartilhado vira o mesmo marcador em cada curva. **Chunks P3:** P3-A `GET /v1/pools/:id/deployments`
    (agent-registry) · P3-B re-ancorar `_compare_deploy_lens` por `pool_id` + markers da timeline do pool
    (analytics-api) · P3-C front (entidades=pools, curva/pool, sem média). Detalhe: spec §11.
  - **P3 ✅ entregue e validado no browser (2026-06-20)** — ver CHANGELOG "P3 — ... RE-ANCORADA no POOL".
    Curva por pool + pontos de deploy coloridos por pool (`ReferenceDot` na altura da curva), sem média,
    flag N<30, estado-vazio "selecione um pool". Fix CH: `any(attr.agent_type)` (constante `'ai'` colidia).
    **Resta P4 (núcleo §4.1):** série por **epoch/versão** — eixo X = versões do pool (`[deploy N, deploy N+1)`),
    ponto = qualidade média da versão, N por versão. Hoje o eixo é tempo + pontos de deploy (leitura da versão
    ainda manual). Diferido por decisão do usuário ("deixar e reavaliar").
- **Nits do bench (diferido)**:
  - **Quality score geral diluído** — KPI "Quality score 0.00 (N evals)" do drill-down e a curva da lente quality
    saem baixos/zero, enquanto as **dimensões** (radar) estão corretas.
    **Investigado ao vivo por leitura de código (2026-07-02), NÃO fechado — hipótese original refutada**: a
    causa provável original ("agregado geral com zero-fill por sessão sem avaliação, divide por sessões em vez
    de evals") **não se sustenta** em `analytics-api/reports_query.py` — tanto `_fetch_agents_cross` (cross-cut,
    correto) quanto `_compare_quality_lens` (lente `quality`, usada no drill-down) calculam a média
    exclusivamente sobre `evaluation_results` via INNER JOIN de atribuição; nenhum dos dois faz zero-fill por
    sessão sem avaliação. Achado real (mas não confirmado como causa): `_compare_quality_lens` filtra o
    período pelo `timestamp` da própria avaliação (quando foi pontuada), enquanto `_fetch_agents_cross` filtra
    por `attr.session_started_at` (data da sessão) — o docstring da lente afirma bucketizar "pela data da
    sessão (regra de ouro §7)" mas o filtro de janela não segue essa regra, então evals de sessões fora do
    range selecionado podem entrar (ou sair) da agregação. **Mas essa mesma divergência de filtro existe
    IGUALMENTE em `_compare_quality_criteria_lens`** (dimensões/radar), que o próprio nit diz estar correto —
    enfraquece a hipótese do filtro como causa única. **Requer reprodução ao vivo com dado real** (range +
    valores exatos de Quality/N evals/Sessions no drill-down vs. a linha do mesmo agente na tabela principal
    do bench) para fechar o diagnóstico — não fazer fix especulativo sem isso.
  - **Janela/período inconsistente** *(hipótese do usuário)* — KPI, lente e tabela de dimensão podem usar
    períodos diferentes; e o **default do range é estranho** (volta alguns dias em vez de hoje/vazio).
    **Nota (2026-07-02)**: o default backend (`_default_from`/`_default_to`, 7 dias) e o default frontend
    (`DEFAULT_FILTERS` em `contacts/types.ts`, `iso7dAgo()`/`isoToday()`) já **concordam entre si** — 7 dias
    em ambos, não são valores diferentes. `DEFAULT_FILTERS` é importado do módulo `contacts`, reaproveitado
    sem customização para o contexto do bench — pode explicar a sensação de "default estranho" (UX, não bug
    de inconsistência de valores). Revisar: (a) confirmar se os 3 (KPI/lente/tabela) realmente usam períodos
    diferentes no mesmo request (não confirmado ainda); (b) considerar default próprio do bench se 7 dias não
    fizer sentido pro caso de uso.
  - NPS por agente parece alto. Pequeno.
- ~~**S2.2**~~ ✅ 2026-06-16 (ver CHANGELOG) — `evaluator_pool` por campanha (SELECT/UI) + dispatcher `POST /campaigns/{id}/dispatch`
  ("Rodar agora") emitindo `evaluation.requested` por instância scheduled + Replayer carregando o form no ReplayContext.
  **Gate E2E rodado 2026-06-17**: cadeia provada ponta-a-ponta — dispatch → Replayer(+form) → routing → `agente_avaliacao_v1`
  (`login → get_context → evaluate` com inferência real do Claude). Dois bugs pré-existentes corrigidos no caminho:
  (a) tools `evaluation_context_get`/`evaluation_submit` exigiam `participant_id`/`evaluation_id` UUID (IDs são opacos)
  → `z.string().min(1)`; (b) `ReasonEngine.max_tokens` hardcoded 1024 truncava a rubrica → 4096.
  **✅ VALIDADO VERDE 2026-06-17 — sessão real (ver CHANGELOG)**: sessão webchat real de retenção → instance →
  dispatch → avaliação real do Claude → `EvaluationResult` persistido (`overall_score=7.8`) + instance `completed` +
  visível em Avaliações. Três causas-raiz corrigidas: (1) `agente_avaliacao_v1.yaml` lia `context.replay_events`
  mas o ReplayContext serializa `events` → transcript nunca chegava ao LLM (latente, mascarado por sessões vazias);
  (2) shim defensivo no `evaluation_submit` p/ o drift prompt×schema (ver revisão form-driven abaixo); (3) **elo
  faltante (Arc 13)**: novo consumer `evaluation.completed → ingest` na evaluation-api (o resultado do avaliador real
  só ia pro ClickHouse, nunca pro Postgres; a instance ficava `scheduled` pois o flow nunca dá `claim`).
- **Revisão — prompt de avaliação form-driven + conveyance de output_schema + contrato único** *(desenhado, não
  implementado — decisão 2026-06-17)*: o gate da S2.2 expôs drift de contrato em 3 frentes: (1) o prompt
  `evaluation_rubric_v3` é **fixo** — deveria derivar do `EvaluationForm` (rubrica/instrução/contrato de saída por
  formulário); (2) o ai-gateway `_format_schema` é **lossy** — só emite campos top-level, descarta
  `items`/`properties`/`description`/`nullable` (o `OutputFieldSchema` nem os modela) → o LLM adivinha o shape;
  (3) **três vocabulários divergentes**: YAML `output_schema` usa `value`/`evidence_refs`; `evaluation_submit` Zod usa
  `score`/`evidence`; LLM emite `score`/`observation`/objetos. Alvo: contrato ÚNICO (YAML `output_schema` ≡ submit
  Zod), schema aninhado transmitido ao LLM, prompt parametrizado pela form → **remove os shims de compat** do
  `evaluation_submit`. Nit relacionado: `criterion.justification` é stripado pelo Zod do submit (perda de texto).
  **RESOLVIDO (T9-C.fix + T9-C.fix2, 2026-06-19)**: (a) ingest da evaluation-api faz fallback
  `notes ← justification` e `evidence ← evidence_entries` (`db.create_criterion_responses`); (b) o
  `EvaluationCriterionResponseInputSchema` do mcp-server passou a aceitar `justification` e o
  `EvidenceRefInputSchema` o shape form-driven (`stream_entry_id`), com o legado opcional p/ compat
  (unit test vitest cobrindo). A justificativa + evidência por `stream_entry_id` atravessam agora o
  caminho do avaliador REAL (LLM → evaluation_submit → ingest → UI nível 3). *Nota: a unificação maior
  do contrato (prompt form-driven + conveyance não-lossy do output_schema, removendo os shims) segue
  pendente — este nit específico (perda da justificativa/evidência) está fechado.*
- **Robustez avaliador — sessão sem dados** *(backlog — não bloqueou hoje; contrato escolhido: opção a)*: avaliar uma
  sessão "magra" (sem transcrição/participantes) ainda falha duro no `evaluation_submit` (o LLM devolve
  `overall_score=null` e o `composite_score` é `number` obrigatório). Contrato escolhido: o avaliador **detecta sessão
  sem conteúdo e marca a instance `skipped`/`error` com motivo, sem chamar submit** (mantém submit estrito). Pode
  exigir status `skipped` no enum (hoje só `error`).
- **S2.3** — dispatcher automático: drena instances `scheduled` das campanhas cujo `evaluation_calendar_id` está
  aberto (calendar-api `is_open`), respeitando a capacidade do pool avaliador.
- **S2.4** — amarrar o workflow de revisão (`review_workflow_skill_id`) ao resultado.
- **Gap UI (pós-pipeline)** — CampaignsPage não tem editar/deletar campanha (só create + pause/resume); a API tem
  `CampaignUpdate`/PUT. Adicionar quando o pipeline estiver fluindo.
- **Surface de instances** — verificar se há tela que lista instances `scheduled` (hoje Avaliações mostra
  resultados). Pode ser preciso um surface para o operador ver a fila de avaliação agendada.

### Shakedown pós-submit Arc 13 (2026-06-17) — gaps descobertos

> **Status (recon 2026-06-25): G-FIN e G-TIMEOUT ✅ RESOLVIDOS pelos T1–T11 (2026-06-19), depois deste
> shakedown.** O `finalize_evaluation` virou o **ponto único** que grava `final_score` + emite
> `evaluation_finalized`, chamado por TODOS os caminhos terminais (ingest IA, ai_review, `submit_review`
> humano e o deadline scanner); o `_run_deadline_scanner` (60s) está wired no startup. Validado E2E em
> 2026-06-25 (`infra/test/smoke_eval_finalize_timeout.sh`): resultado vencido → scanner finaliza
> (`timeout_contestation`/`uncontested`, `final_score` gravado) + evento no ClickHouse `evaluation_finalized`.
> Detalhe: CHANGELOG T11-A+B / T3 / T4. **Restam abertos só G-S2.4, G-PROBE e G-UI.**

- **G-FIN — ✅ RESOLVIDO (T3, 2026-06-19; validado 2026-06-25)**: `finalize_evaluation` é o emissor único
  (idempotente) e o `submit_review` (humano) o chama nos estados terminais (`closed_upheld`/`closed_revised`/
  `closed_max_rounds`), gravando `final_score` + emitindo `evaluation_finalized`. Avaliações humanas reais
  passam a contar nos relatórios canônicos. *(Resíduo: o caminho via **workflow** — `workflow.completed` →
  só `lock_result` — segue não-finalizante; isso é o G-S2.4 abaixo, não o G-FIN.)*
- **G-TIMEOUT — ✅ RESOLVIDO (T4, 2026-06-19; validado 2026-06-25)**: `_run_deadline_scanner` (background,
  60s) varre `list_expired_results` (`result_state IN open|under_review` com `deadline_at` vencido) e finaliza
  via `finalize_evaluation` → `timeout_contestation`(`uncontested`) / `timeout_review`. Smoke E2E verde.
- **G-S2.4 — motor de review por workflow ✅ RESOLVIDO POR DECISÃO (2026-06-25): APOSENTADO.** Não é bug, é
  bifurcação: o contest→review→finalize **canônico já existe** no Arc 13 REST (`contestation_router` →
  `finalize_evaluation`, emite `evaluation_finalized`). O motor por workflow (`review_workflow_skill_id`/
  `skill_revisao_treplica_v1`) é paralelo e **inerte** (nada no backend o dispara — config morta lida só pela
  UI; único trigger = e2e cenário 28, opt-in, fora da suíte default; termina em `lock`, não finaliza). Decisão:
  Arc 13 é o contrato único; o motor por workflow vira **legado/superseded** (doc + anotações de legado no
  consumer reativo, no doc Arc 6 e no cenário 28). NÃO toca o motor genérico (skill-flow/workflow-api).
  **Follow-up opcional (remoção física da cola específica):** consumer reativo `workflow.events` na
  evaluation-api, coluna/seletor `review_workflow_skill_id`, skills `skill_revisao_*`/`agente_revisor_v1`,
  cenário 28. Deixado para um slice próprio (raio de teste no cenário 28).
- **G-PROBE — auth dos endpoints de escrita Arc 13** *(segurança; DESENHADO 2026-06-25, impl. pendente)*.
  **Recon corrigiu o diagnóstico** (o bullet antigo estava stale): os endpoints **avaliado-facing já estão
  gated** — `file_contestation` e `submit_review` chamam `_decode_jwt` + `_check_abac_permission`
  (`contestar*`/`revisar*` por round; UI manda `Bearer JWT`); `submit_ai_review` usa `_require_admin`. O gap
  real é **curadoria/calibração/config header-only** (aceitam qualquer `X-Tenant-ID`/`X-User-ID`, sem
  JWT/ABAC/admin): `resolve_curation`, `blind_rescore`, `blind_resolve`, `submit_pre_review`,
  `publish_calibration_note`, `create/update/delete_sampling_rule`.
  **Callers reais:** `blind_*`/`resolve_curation` → `CuradoriaPage` (humano, header-only hoje);
  `submit_pre_review` → tool MCP `evaluation_pre_review_submit` (**agente/sistema**, header-only hoje);
  `submit_ai_review` → gate T12 (**agente/sistema**, `_require_admin`); `seed/flush-synthetic` → tooling
  dev/demo (botão admin). `sampling_rule`/`calibration_note` POST não são chamados pela UI.

  > **Nota (2026-07-01)**: a perna agente/sistema deste item foi re-roteada para o **Agent Principal**
  > (identidade de máquina genérica), não mais um token de serviço ad-hoc — ver decisão no detalhe abaixo.

  **DESENHO FINAL FECHADO (2026-06-25) — princípio: quality = ABAC puro p/ usuário; admin token só p/
  serviço/infra (domínio configuration).**
  - **Surface de USUÁRIO do quality → ABAC, admin token REMOVIDO.** Novo campo **`curar`** no módulo
    evaluation (`modules.yaml`: `none|read_only|read_write`, `scopable: pool`) gateia `resolve_curation`/
    `blind_rescore`/`blind_resolve` (write=read_write; GET `get_blind_context`/`list_curations`=read_only) via
    `_decode_jwt`+`_check_abac_permission('curar', pool)`. `sampling_rule` CRUD → reusa `formularios`; Rubric/
    Prompt → `gerir_rubrica` (já existe); review/contest → `revisar`/`contestar` (já existe). Concedido pela
    tela de Access (não é role; é feature por-usuário), escopo por pool.
  - **Endpoints de AGENTE/SISTEMA → credencial de serviço (M2M), NÃO role do quality.** `pre-review`/`ai-review`
    (+ `seed/flush`) mantêm token de serviço (admin token relabeled "service credential", domínio infra/
    configuration). Não viola "quality sem admin" — é máquina-a-máquina. Bônus: `pre-review`, hoje aberto,
    passa a exigir credencial (hardening).
  - **UI**: `CuradoriaPage` (e Campaigns/Rubric onde virar ABAC) passam a mandar `Bearer JWT` (já têm
    `useAuth.accessToken`); deixam de mandar admin token onde virou ABAC.
  - **Calibration (leitura)** aberta/`report`; escrita (`publish_calibration_note`) é serviço (vem do
    `blind_resolve`, server-side).
  - **Verificação**: smoke 403 sem grant `curar` / 200 com grant; endpoints de serviço 401 sem token.
  - **Seed**: grants concedidos pela tela (como no G-UI) — opcional semear um curador demo em `seed_auth.py`.

  **DECISÃO (2026-07-01): a perna "AGENTE/SISTEMA" deste item NÃO será resolvida como credencial de serviço
  ad-hoc (token único compartilhado).** Em vez disso, será resolvida de forma genérica pelo item **Agent
  Principal — identidade de máquina p/ agentes IA** (`subject_type:"agent"`, ver seção própria neste arquivo):
  cada agente/serviço chamador de `pre-review`/`ai-review`/`seed-flush` passa a ter **principal_id próprio**
  (não um token único), com capability derivada do `agent_type` e audit por `principal_id`. Isso substitui o
  "service credential" único como o mecanismo de auth desses endpoints — o gate em si (exigir credencial em vez
  de header aberto) continua necessário, só que a **identidade por trás da credencial** vem do Agent Principal,
  não de um relabel do admin-token. A perna de **usuário humano** (`curar` ABAC) segue como desenhado acima,
  não é afetada por esta decisão.
  **Perna humana `curar` — ✅ RESOLVIDA (2026-07-02).** Recon encontrou o desenho já implementado quase
  por completo (código de sessão anterior, não registrado como concluído aqui): catálogo `curar` em
  `infra/modules.yaml` (scopable: pool); `contestation_router.py` já gateava `list_curations`/
  `resolve_curation`/`get_blind_context`/`blind_rescore`/`blind_resolve` via `_require_curar` (Bearer) +
  `_check_abac_permission('curar', pool_id, min_access=...)`; `CuradoriaPage` já mandava Bearer
  (`useAuth`), não admin-token. Faltava só: **seed de grant demo** (`supervisor@plughub.local` ganhou
  `evaluation.curar=read_write` em `seed_auth.py` — sem isso ninguém no demo tinha o grant, a tela ficava
  403 por padrão) e **smoke de verificação** (`infra/test/smoke_gprobe_curar_auth.sh` — 401 sem Bearer,
  403 sem grant, 403 read_only tentando escrever, 200/404 com grant correto, provando que o ABAC passa
  antes do lookup no banco). Testes unitários (`test_curar_*` em `test_available_actions.py`) já
  cobriam `_check_abac_permission` isoladamente.
  (b) perna agente/sistema — depende do Agent Principal (F1–F4) estar implementado antes de gatear
  `pre-review`/`ai-review`/`seed-flush` por `principal_id`.
- **G-UI — UI de review/contestação humana existe mas não surfaça** *(bullet ORIGINAL de 2026-06-17, stale —
  recon 2026-07-01 encontrou a maior parte já corrigida)* — **✅ VALIDADO E2E AO VIVO (2026-07-01)**: operator
  contestou uma avaliação `open` (badge virou "Under review (r1)"); supervisor revisou por dimensão
  (`HumanReviewPanel`, upheld/revised com score override) e o resultado finalizou como `closed_revised`
  (nota da dimensão ajustada 0.0→9.0). Ciclo contest→review→finalize confirmado funcional na UI real, não só
  via smoke/API. Ver detalhe abaixo.

  **Recon 2026-07-01 — o que já estava resolvido (nenhuma ação nova):**
  - `infra/seed/seed_auth.py` **já concede** `evaluation.revisar` (read_write) a `supervisor@plughub.local` e
    `evaluation.contestar` (read_write) a `operator@plughub.local`. O achado original ("os usuários demo, Admin
    incluso, não têm grants") só é verdade para o **Admin** — que não é o papel certo para revisar/contestar de
    qualquer forma. Testar com `supervisor@`/`operator@`, não com Admin.
  - A tabela principal da `AvaliacoesPage` **já usa `ResultStateBadge`** (T9-A1) — mostra `result_state`+round+
    `finalize_reason` real (`closed_upheld` etc.), não `eval_status` genérico.
  - A coluna **Date já é populada** (`finalized_at` quando finalizado, `deadline_at` quando há ação pendente,
    senão `created_at` + tempo decorrido no estado) — não é mais "—".
  - `available_actions` é computado **server-side** por `_compute_available_actions` (router.py), função pura
    com suíte própria (`tests/test_available_actions.py`, T10) cobrindo result_state+round+posse.
  - Timeline de `ContestationThread` **já renderiza** no drill-down (`DimensionThreadCard`, dentro do
    `DetailPanel`) quando a instance tem threads Arc 13.
  - `evaluation_finalized` (G-FIN) já dispara pelo `submit_review`/`finalize_evaluation` — o status evolui.

  **Corrigido nesta sessão (2026-07-01)**: o cabeçalho do `DetailPanel` (drill-down) ainda usava
  `<StatusBadge status={result.eval_status}>` (mostrava "Submitted" genérico) em vez do `ResultStateBadge`
  canônico já usado na tabela — inconsistência real, agora alinhada. Arquivo:
  `packages/platform-ui/src/modules/evaluation/AvaliacoesPage.tsx`.

  **✅ Validado ao vivo (2026-07-01)**: com o fix de dado da campanha (`pool_id` alinhado a
  `evaluation_pool_id`, ver bug acima) + uma linha `open` com `evaluated_user_id` = UUID real do operator, o
  botão Contest apareceu corretamente para `operator@plughub.local` no drill-down (`DimensionContestPanel13`).
  A unificação Arc 6×Arc 13 (o código ainda tem os dois caminhos — `isArc13` decide qual painel renderizar)
  segue como compat intencional, não bug.

  **i18n — chaves do Arc 13 nunca foram adicionadas ✅ CORRIGIDO (2026-07-01)**: `DimensionContestPanel13`/
  `HumanReviewPanel` usavam `contest.dimensionList`/`minWords`/`wordsRemaining`/`noDimensions` e
  `review.dimensionDecisions`/`uphold`/`revise`/`newScore`/`justification`/`minWords`/
  `justificationPlaceholder`/`allFieldsRequired`/`noContestedDimensions`/`cancel`/`submitDimensionReview` —
  nenhuma dessas chaves existia em `en/evaluation.json`/`pt-BR/evaluation.json` (só as do Arc 6 por-critério),
  então apareciam cruas na tela (violava o i18n Invariant do CLAUDE.md). Adicionadas nos dois locales.
  **Falta**: confirmar o fluxo de Review (login como `admin@plughub.local` após o operator contestar) e
  rebuildar `platform-ui` para essas chaves entrarem em produção.
- **Superfícies a confirmar/faltando**: avaliado **acompanhar/contestar as próprias avaliações** (self-view existe no
  código, idem gated por ABAC); fila de revisão do supervisor ("Awaiting my action" depende de `available_actions`);
  timeline de `ContestationThread` no drill-down; páginas **Curation**/**Calibration** (Arc 13 Fase H) — existem mas
  nunca validadas com dado real (só seeder).

  **Bug self-view ✅ CORRIGIDO (2026-07-02)** — `_compute_result_scope`/`list_results` bloqueavam o próprio
  avaliado quando o `pool_id` administrativo da campanha divergia do pool operacional real. Fix + detalhe
  completo em `CHANGELOG.md` § "evaluation-api — bug self-view...".
- ~~**Confirmado ao vivo (2026-06-17)**: probe contest→review→`closed_upheld` sem `evaluation_finalized`~~ →
  **obsoleto: corrigido pelos T1–T11** (finalize_evaluation no submit_review). Re-validado 2026-06-25 (smoke).
- **Próximo (gaps remanescentes)**: G-UI (só falta validação ao vivo pós-fix de 2026-07-01, ver acima),
  G-S2.4 (aposentado por decisão — ver acima, não bloqueia), G-PROBE (perna humana `curar` ✅ resolvida
  2026-07-02, ver acima; perna agente/sistema — depende do Agent Principal); e exercitar Fluxo 2
  (curadoria/`calibration_signal`→CalibrationNote→KB), que só rodou via seeder.

**Achados pré-existentes (registrados durante a F1.0 — NÃO causados por ela; F1.0 é inerte):**
- **A — specialist-return (pré-requisito da F4)**: um conference specialist (ex.: `auth_form_ia` via @mention)
  que termina com `escalate` re-roteia o CONTATO em vez de **voltar ao chamador**. O `agente_auth_form_v1.yaml`
  escala nos dois caminhos (sucesso/falha) → invocado como specialist (admin servindo), escala pro
  `retencao_humano` → fila → drena de volta (sintoma: mensagem de fila espúria). Modelo-alvo (definição do
  usuário): invite/task **sempre voltam ao chamador**. Fix preferido: **engine** — flow em modo conference
  specialist trata `escalate`/`complete` como **retorno-ao-chamador** (devolve outcome), não re-roteia o contato.
  É o **núcleo da F4** (aprovação = specialist que devolve outcome). Sub-arco próprio.
- **B — multi-sessão humana no push (ligado ao pull)**: humano servindo entra `state="busy"`;
  `get_ready_instances` exige `state=="ready"` → mesmo com vaga (`max_concurrent=3`; a cap humana vem da URL do
  WS do Console — `mcp-server` server.ts:2147 default 3 — não do `auth`), um humano em atendimento não recebe 2º
  contato concorrente via **push** → vai pra fila. É o gap que o **pull (F1)** endereça (o humano puxa o
  próximo). Decisão de modelo: o push também deveria oferecer (manter `ready` enquanto sob capacidade)? Medir ao
  vivo (`state`/`current_sessions`/`max_concurrent` da instância) se for atacar.

---

## Record/Replay Harness — gravação/replay em todas as costuras *(proposta — não implementado)*

Visão + spec em [`docs/product/record-replay-harness-spec.md`](docs/product/record-replay-harness-spec.md). Generaliza o Session Replayer (que hoje replaya só o stream da sessão, para avaliação) num harness "VCR" em todas as costuras (channel-gateway, AI Gateway, MCP, Kafka) — cada costura como **driver** (injeta inputs gravados) ou **mock** (devolve outputs gravados), com timings.

**Base que já existe**: `session-replayer` (persister/hydrator/replayer/comparator), `ComparisonReport` (Jaccard + deltas), `delta_ms`/`speed_factor`, Kafka como log, harness `e2e-tests`. **A construir**: captura full-fidelity de payload em MCP/AI Gateway (hoje `mcp.audit` é só metadado), clock/seed injetável (determinismo), harness multi-costura, gravação seletiva (golden/amostrada/on-demand) com masking, e o **gate de promoção** consumindo o `ComparisonReport` como critério objetivo. Aplicações: regressão determinística, repro de bug, simulação de carga, datasets de avaliação.

---

---

