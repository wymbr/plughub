# ADR — Aprovação humana como passo de workflow (DialogForm + agente logado)

> **Status:** Proposto (2026-07-16). Convergência de uma discussão de design que fechou as bifurcações
> deixadas em aberto pelas specs de Frente 1 (pull dispatch + inbox + aprovação).
> **Contexto de origem:** o substrato genérico de **pull dispatch** já está construído (CHANGELOG "Frente 1 —
> Pull core F1.0–F1.3", "F2 API+tools+inbox", "F2b preview/triagem"): `dispatch_mode: pull` no pool, claim
> atômico (`ZREM`) + semáforo de capacidade, tools `work_queue_*`, `PullInboxPanel` com preview. O que faltava
> era a **especialização de aprovação** (F4/F5 do plano). Ao detalhá-la, três forks que os specs não tinham
> fechado foram resolvidos: (1) **conteúdo** do pacote, (2) **quem é o aprovador**, (3) **alcance de canal**.
> **Relacionados:** `docs/product/human-work-queue-aprovacao-spec.md`, `docs/product/routing-pull-dispatch-spec.md`,
> `docs/product/pull-inbox-console-ui-spec.md`, `docs/product/frente1-dispatch-pull-aprovacao-plano-consolidado.md`
> (planos que este ADR **reconcilia/atualiza**); `docs/product/dialog-primitive-and-runner-design.md` +
> `docs/adr/adr-otp-workflow-and-dialog-primitive.md` (DialogForm); `docs/arcos/arc19-unified-session-model.md`
> (`collect`), `docs/arcos/arc9-agent-groups.md`, `docs/arcos/arc8-agent-availability.md`, `docs/arcos/arc7-auth.md`
> (ABAC), `docs/adr/adr-identity-channel-possession.md` (posse de canal — relevante só p/ o veículo omnichannel adiado).

---

## 1. Contexto

Um "pedido de aprovação" é um ponto **humano-no-loop** de um workflow: o fluxo monta um pacote (resumo,
contexto, anexos, opções de decisão), pede a um humano que **decida** (e eventualmente **edite** algo), e
**ramifica** conforme a decisão. Aprovação é o primeiro instanciamento; o mecanismo serve igual para revisão de
reembolso, liberação de crédito, conferência de pedido, ou revisão da resposta da IA antes do envio.

As specs de Frente 1 já resolveram a **coordenação** (fila pull + claim atômico como árbitro único) e
assumiram um "pacote" com form padrão bespoke. A discussão de 2026-07-16 fechou o resto: o pacote é um
**DialogForm** (reuso do primitivo de survey/OTP), o aprovador é um **agente logado** (não uma pessoa alcançada
por fora), e o alcance é **web-only no v1** (omnichannel adiado, mas sem retrabalho futuro).

---

## 2. Decisões

### D1 — Aprovação é um passo **transparente** do workflow; o work item é uma sessão suspensa

Aprovação **não é entidade nova**. O workflow monta o pacote num passo anterior (via `context_set` + anexos no
AttachmentStore) e então **`delegate`/`collect`** a um **pool de aprovação**. A sessão suspende — o work item é
essa **sessão de workflow suspensa** na fila do pool. A decisão **volta pelo retorno do delegate** (já cai em
`$.pipeline_state.<id_do_delegate>`, sem mudança de schema/engine) e o **workflow principal roteia** com um
`choice`. O autor do workflow escreve um passo + um `choice`; fila, atendimento e retorno são transparentes —
exatamente como o `collect` já é (Arc 19: cria sessão-filho, agente atende, retorna resultado; o workflow nunca
sabe o canal).

### D2 — Distribuição é **config do pool** (`dispatch_mode: push | pull`) — "os dois"

Não se escolhe push OU pull no desenho; é um campo do pool. **Pull** = o grupo de aprovadores vê a fila e
**puxa** (claim, `ZREM`, um único vencedor) — triagem. **Push** = o router **auto-atribui** ao aprovador
disponível (ACD). Ambos reusam a **mesma** máquina de concorrência do roteamento (alocação atômica + semáforo
`claim_instance`). Nenhuma concorrência nova é escrita.

### D3 — Conteúdo do pacote = **DialogForm** (reuso do Dialog Primitive)

O "form padrão de aprovação" é um **`DialogForm`** (dialog-api, porta 3760): nós `statement` = contexto
read-only; nós `question` = campos editáveis + as **decisões** (`interaction: button`, opções = `decisions[]`).
Ganha de graça **versionamento** (draft/published), **i18n** e o **editor `/config/dialog-forms`** — fecha a
dívida "form = dado do tenant, UI-editável" também para aprovação. O renderer reusa o de "form inteiro de uma
vez" que o veículo web de survey (`/survey/:token`) já tem. **Anexos** (refs do AttachmentStore) viajam **ao
lado** do form no `delegate.context`, não dentro do DialogForm (mantém o form puro).

Extensão compartilhada: o vocabulário de campo do DialogForm (hoje text/button/list) ganha **tipos de campo**
(money/select/date/bool) — enriquecimento do form-builder que **também serve survey**, não é aprovação-only.
Campo **pré-preenchido editável + auditoria da edição** (antes/depois) é a outra extensão.

**Invariante de fronteira:** `decisions[]` → roteamento vive no **`choice` do workflow**, NUNCA no JSON do form
(senão "vira linguagem em JSON"). O DialogForm captura *qual* decisão; o workflow decide *para onde vai*. É a
mesma regra que separou survey-record de OTP-verify: compartilha-se o **conteúdo** (DialogForm/`form_get`), nunca
o **controle**.

### D4 — Aprovador = **agente/recurso logado** (Modo A, presença)

O aprovador é um **agente humano logado** atendendo um contato de aprovação — participante de primeira classe do
modelo de sessão simétrico (humano/IA). Ser recurso roteável **implica presença** (`agent_login → agent_ready`,
capacidade, heartbeat). Reuso total: **Agent Groups** (Arc 9) monta o pool de aprovadores; **disponibilidade**
(Arc 8) diz quem está apto; **ABAC** (Arc 7 + módulo novo `approvals`) gate ver/decidir por `accessible_pools`.
Bônus da simetria: o pool de aprovação pode ser **humano OU IA** — **auto-aprovação sob política** é o *mesmo*
desenho, trocando o tipo do pool; o workflow não sabe a diferença.

**Pool scope = `accessible_pools`, não `scope[]` por campo.** Os campos ABAC `approvals`
(`operacao`/`decide`) são **não-scopable**: "quais pools posso aprovar" = ter a capacidade **E** o
pool ∈ `accessible_pools` (a dimensão de pool já existente). O enforcement é pool-agnóstico na
capacidade (`ApprovalPanel.perms.can("approvals",…)` sem `scopeId`) + `accessible_pools` no inbox pull.
Um `scope[]` por campo duplicaria a dimensão de pool e pediria a lista em dois lugares. (Módulos onde a
permissão vale só p/ alguns pools independentemente da visibilidade de dados — ex.: qualidade — seguem
scopable; aprovação não é esse caso.)

### D5 — Superfície = **Console/inbox responsivo** (mobile-friendly, tap-first)

O aprovador trabalha no Console. Pull reusa o `PullInboxPanel` (lista/preview/claim já prontos); o **novo** é o
**renderer do pacote** (DialogForm + campos editáveis + anexos respeitando masking + botões de decisão + devolver
à fila). A view de aprovação é **responsiva e glanceável** (aprovação é rápida — tap-to-decide): um gerente abre
o Console no navegador do celular, vê a fila, toca aprovar. É layout responsivo de uma view enxuta, **não** um
app/PWA dedicado.

### D6 — Omnichannel **adiado**; conteúdo e retorno **canal-agnósticos** desde o dia 1

Aprovar por WhatsApp/e-mail/voz **sem estar logado** (o "aviso interativo + aprovar remoto") é **não-objetivo do
v1**. Ele arrasta camadas especulativas: outbound interativo + correlação inbound por item, **posse de canal**
por aprovador (`adr-identity-channel-possession`), política de masking por canal, degradação de form por canal.
A maior parte do valor "aprovar de qualquer lugar" já vem do **Console mobile-friendly** (login de baixo atrito
via silent re-auth). Guard-rail que torna o adiamento **de graça** (sem retrabalho): manter **conteúdo
(DialogForm) e retorno (payload do delegate) canal-agnósticos** — assim, omnichannel entra depois como "mais um
veículo de entrega" sobre o **mesmo item de fila**, nunca como reescrita do núcleo. Corolário: **não** se cria
`channel_type: "console"` (a fila é coordenação, não canal — decisão D1 do plano consolidado, preservada).

### D7 — Edição e decisão **auditadas**

Edições de campo voltam no `payload.edits` do retorno do delegate; cada alteração é **auditada** (quem, quando,
antes/depois) — trilha append-only no espírito de `ContestationThread`/`audit_access_log`. Visualização de
anexo/contexto registrada (como a Auditoria LGPD já faz). Campos sensíveis via **masked input** (não vazam a
stream/log). Editar binário de anexo = fora de escopo (só anotar/adicionar novo).

---

## 3. Invariantes — never violate

- **Routing Engine é o árbitro único** — claim (pull) / alocação (push) = alocação; aprovação não fura isso.
- **O workflow principal é dono do roteamento** — o humano devolve uma **decisão limitada** (`decisions[]`),
  não salta para um passo arbitrário; o `choice` do workflow mapeia decisão → `next` (inclui rework p/ passo anterior).
- **O POOL é a unidade endereçável** — o `delegate`/`collect` aponta para um **pool**; skill/config são detalhe do deploy.
- **Sem concorrência nova** — reusa alocação atômica + semáforo `claim_instance` (push+pull compartilham capacidade).
- **Sem `channel_type: "console"`** — fila = coordenação, canal = entrega; eixos distintos.
- **Branching nunca no JSON do form** — `decisions[]`→rota no `choice` do workflow; o DialogForm só captura a escolha.
- **Conteúdo e retorno canal-agnósticos** — DialogForm + payload do delegate; omnichannel é veículo posterior, sem rework.
- **Sem primitiva nova** — `delegate`/`collect` + DialogForm + `form_get` + pull dispatch já existem; constrói-se a
  *superfície* (renderer no inbox), o *contrato do pacote* e as *extensões de form-builder*.

---

## 4. Consequências

**Positivas.** Reuso quase total (roteamento, concorrência, inbox pull, Agent Groups, disponibilidade, ABAC,
silent re-auth); o form de aprovação vira **dado do tenant editável** (dialog-api/editor) sem schema bespoke;
**auto-aprovação e aprovação manual são o mesmo desenho** (só muda o tipo do pool); o **gate de promoção
homologação→produção** vira naturalmente um workflow com passo de aprovação; as extensões de form-builder
(tipos de campo, pré-preenchido, auditoria de edição) melhoram **survey** também.

**Custos.** Renderer do pacote no inbox (DialogForm + editáveis + anexos + decisão); extensão do vocabulário de
campo do DialogForm; auditoria de edição; módulo ABAC `approvals` (`operacao`/`decide`) em auth-api. Presença
obrigatória do aprovador (Modo A) não cobre "aprovar sem login" — aceito no v1.

**Riscos.** (a) Anexos/contexto rico com PII exigem masking por role no renderer (reuso do masking existente).
(b) Métrica de **rework** (quantas vezes uma task volta a um passo anterior) é feature futura (liga à Bancada/Arc 6).

---

## 5. Alternativas consideradas

- **Pacote com schema bespoke de aprovação** — rejeitado: reusar **DialogForm** dá versionamento/i18n/editor de graça.
- **Novo `channel_type: "console"`** — rejeitado: fila é coordenação, não canal (preserva D1 do plano consolidado).
- **Omnichannel no dia 1** (aprovar por qualquer canal sem login) — **adiado**: veículo posterior sobre o mesmo
  item; conteúdo/retorno canal-agnósticos deixam a porta aberta sem custo (D6).
- **Aprovador como pessoa alcançada por fora, sem presença (Modo B)** — **adiado** junto com o omnichannel:
  mantém o aprovador como *usuário* (identidade+ABAC+posse) mas troca presença por notify+correlate; é a camada
  que realiza o omnichannel, fora do v1.
- **Weight-ordering da fila** — adiado (F6 do plano consolidado): v1 pull = FIFO + cor de SLA.

---

## 6. Fases (plano fatiado — reconcilia F4/F5 do plano consolidado)

> Pré-existente ✅: F1 (pull core), F2 (tools + inbox), F2b (preview). Este ADR detalha o que falta.

| Fase | Entrega | Dep. | Esforço | Gate |
|---|---|---|---|---|
| **A1 — DialogForm de aprovação (conteúdo)** | `DialogField.type` **já é string aberta** (achado): tipos money/select/date/bool são suporte de *renderer*, não schema; `interaction: "form"`+`fields[]` já modela "form inteiro". Adição de schema **real** ✅: `DialogField.value` (pré-preenchido editável) + `DialogField.options` (select por campo). Render "form inteiro" reusado do veículo web. **Serve survey também.** | dialog-api | **P** (menor que estimado) | form de aprovação criável no editor + `form_get` normaliza |
| **A2 — Pacote + agente + rota (backend)** | `delegate.context` carrega `form_id` (DialogForm) + `decisions[]` + `attachments[]`; **agente de aprovação genérico** (skill nível c) que renderiza via `form_get`, captura decisão+edições, devolve por `workflow_resume`; `choice` no workflow principal sobre `$.pipeline_state.<delegate>`. **Sem mudança de engine.** | A1, F1 | **M** | E2E: delegate→claim→decide→resume→rota (inclui rework p/ passo anterior) |
| **A3 — Renderer no inbox (Console, responsivo)** | no `PullInboxPanel`: visão completa do pacote (contexto read-only mascarado + editáveis + anexos + botões de decisão + devolver à fila); **layout responsivo/mobile-friendly**; ABAC `approvals` (`operacao`/`decide`). | A2, F3 | **G** | UI E2E aprovação (web + viewport mobile); masking por role |
| **A4 — ABAC `approvals`** | módulo/campo `approvals` (`operacao`/`decide`, `scope[]` por pool) em auth-api `module_registry` + `infra/modules.yaml`; gate no inbox e na decisão. | — | **P** | gate: sem `decide` = read-only |
| **A5 — Auditoria de decisão/edição** | trilha append-only (quem/quando/antes-depois) das edições + decisão; visualização de anexo registrada; masked input p/ campos sensíveis. | A2 | **P–M** | audit E2E |
| **A6 — Extras (pós-v1)** | quatro-olhos (2 aprovadores); reatribuição por supervisor (= conferência padrão); notificações/SLA; **rework rate** (Bancada/Arc 6); **auto-aprovação** (pool IA). | A3 | **M** | — |

**Sequência v1:** A4 (ABAC, destrava gate) → A1 (form) → A2 (backend) → A3 (inbox) → A5 (auditoria). A6 é pós-v1.
**Caso âncora da demo (decidido 2026-07-16):** **gate de promoção de skill** — promover deploy homolog→prod vira
um workflow com passo de aprovação; o pacote traz skill/versão/diff + decisões aprovar/recusar; conecta com o
deploy/slots já existentes (`set-next`/`promote` no agent-registry). Reembolso e revisão-de-IA ficam como
próximos instanciamentos do mesmo mecanismo.

**Não-objetivos v1:** omnichannel/Modo B (D6); weight-ordering (F6); rework rate (A6); auto-aprovação (A6).

## 7. Itens abertos achados na validação (2026-07-16)

- **Tempo de espera preservado no re-enfileiramento** *(com A5)*: o display do inbox usa `queued_at_ms` (resetado no release). O padrão certo já existe (`{t}:queue:first_queued:{sid}` NX+TTL da mute_queue). Fix baixo: setar `first_queued` NX no `add_queued_contact` (routing-engine) + `listQueue` (mcp-server) lê ele p/ a idade (fallback `queued_at_ms`) — sem mudança de frontend. Decisão relacionada: usar `first_queued` também no **score** da fila = fairness (tarefa devolvida não perde a posição).
- **Release→re-claim de aprovação-conferência perde o contato + vaza capacidade** *(RESOLVIDO 2026-07-17)*: uma tarefa de aprovação devolvida à fila, ao ser re-reivindicada, saía da fila mas **não re-anexava** no Console; e o resolve **não liberava a capacidade** do agente (observado SCARD=3 após 3 ciclos → "No capacity available").
  - **Hipótese inicial FALSEADA:** occupant `session::conf` vs release por `session` → "não casa". Errada: `release_instance` (`_RELEASE_INSTANCE_LUA`) libera **por prefixo `{session}::`**, que casa com `{session}::{conf}`. Além disso o occupant observado tinha **conf VAZIO** (`session::`), não `session::conf`.
  - **Causa-raiz real (uma só):** o **`conference_id` era descartado em toda a cadeia de claim** — `listQueue`/`QueueContact` (mcp-server) não expunha o campo; o `PullInboxPanel` não o enviava; e `work_task_claim` montava o `RoutingResult` **sem** `conference_id`. Resultado: occupant `session::` (vazio) e `conversations.routed` sem conf → o humano era anexado como **primary**, fora da conferência do delegate. Isso quebrava a re-anexação (P2, o `conversations.routed` sem conf cai no dedup do bridge) **e** o release no resolve (P1: sem ser especialista de conferência, o teardown do resume não emitia o agent_done do humano → a vaga nunca soltava).
  - **Fix:** propagar `conference_id` nos 3 pontos (`listQueue` → `PullInboxPanel.handlePull` → `RoutingResult` de `work_task_claim`). Com o humano corretamente anexado como especialista, o teardown do resolve emite `remove_conversation` na instância humana → libera a vaga (release por-prefixo). **Um fix fecha P1 e P2** (a unificação "mesmo subsistema" prevista no §8). Validado por E2E via API: occupant vira `session::conf`, e 3 ciclos claim→decide deixam `SCARD=0` (sem vazar). **Pendência residual:** o bundle Vite do Console precisa entrar em vigor para os claims da UI carregarem `conference_id` (backend validado por curl; UI em burn-down).
  - **Reset de emergência:** `DEL {t}:instance:human-*:sessions` + `DEL {t}:pool:aprovacao_deploy:queue`.

## 8. Pass focado — Release / Conference Lifecycle (com A5)

Todas as arestas achadas na validação são o MESMO subsistema (fila pull × sessão de conferência suspensa). Resolver juntas, com teste próprio, não em patches isolados:

> **Achado da validação (2026-07-17):** P1 e P2 tinham a **mesma** causa-raiz — o `conference_id`
> descartado na cadeia de claim (ver §7). **Um único fix** (propagar `conference_id` em `listQueue` →
> `PullInboxPanel` → `RoutingResult` de `work_task_claim`) fecha os dois: o humano passa a ser anexado
> como especialista de conferência, o `conversations.routed` com conf re-anexa (P2) e o teardown do
> resolve emite `remove_conversation` na instância humana → libera a vaga (P1). Bug A (release explícito
> no resolve) descartado — desnecessário. Gate batido: 3 ciclos claim→decide com `SCARD=0`.

| Item | Entrega | Gate | Status |
|---|---|---|---|
| **P1 — release libera capacidade** | ~~release do pull com o mesmo occupant~~ **causa real:** propagar `conference_id` no claim → humano vira especialista → teardown do resolve libera a vaga. | claim→decide→`SCARD`==0; N ciclos sem vazar | ✅ (backend; 3 ciclos `SCARD=0`) |
| **P2 — re-claim re-anexa** | mesma correção do `conference_id`: `conversations.routed` com conf isenta do dedup do bridge e re-publica o assign → Console re-monta o pacote. | release→re-claim mostra o pacote de novo | ✅ (backend; auto-anexo com conf) |
| **P2b — claims da UI enviam `conference_id`** | os **dois** call sites (`PullInboxPanel.handlePull` + `AgentAssistPage.claimPreviewContact` do preview) enviam `conference_id`; `platform-ui` é imagem buildada → exige `build --no-cache`. | claim pela UI gera occupant `session::conf`; re-claim re-anexa | ✅ (E2E UI: claim→return→re-claim→aprovar OK) |
| **P3 — timer `first_queued`** | `first_queued` NX no `add_queued_contact` + `listQueue`/`PullInboxPanel.ageOf` ancoram nele (fallback `queued_at_ms`). Score da fila segue em `queued_at_ms` (fairness = opcional, deferido). | display: idade = espera total, preservada no re-enfileiramento | ✅ (E2E: score +344s, idade segue 6m8s) |
| **P4 — refresh imediato pós-release** | ~~inbox atualiza na hora após release~~ **ADIADO** — achado: `work_task_release` devolve `requeued:false` (o JSON `queue_contact` é deletado quando o contato é roteado no claim — kafka_listener/main.py); o contato só reaparece ~4s depois por um **fallback assíncrono**. Frontend não tem o que mostrar na hora. Requer backend (preservar o JSON no claim p/ o release re-enfileirar síncrono). Frontend já pronto (`refreshSignal`+bump) + `cache:no-store` no fetch (fila = tempo-real, evita 304 stale). | UX | adiado (backend) |
| **A5 — auditoria** | trilha de decisão/edições (o item pendente do v1). **Design fechado → §9; implementado → §10.** | audit E2E | ✅ (E2E 2026-07-17) |

---

## 9. A5 — Modelo de auditoria de decisão (design fechado, 2026-07-17)

> Convergência de uma discussão de design. O ponto de partida (prompt de passagem) propunha um novo
> `StreamEventType` "approval_decision" escrito via `writeStreamEntry` numa rota do mcp-server. A investigação
> reenquadrou o problema e fechou um modelo **mais barato e mais reusável**, ancorado em três achados de código.

### 9.1 Achados que ancoram o modelo

- **A decisão contorna o stream hoje.** `ApprovalPanel.decide()` faz `POST /v1/channels/webhook/resume/{token}`
  (REST, **sem** `Authorization`) com `payload:{decision,source:"operator",choice,edits}`. Não passa por
  `writeStreamEntry`, não vira mensagem — logo **não** aparece em transcript/HistoricoTab. O modelo mental
  "decisão = troca de mensagens de canal interno" **não** é o estado atual; é o que a escrita no stream cria.
- **O resume do webhook JÁ escreve no stream.** `handle_resume` (`webhook.py`) faz `xadd` em
  `session:{id}:stream` com `type:"session_resumed"`, `visibility:"agents_only"` e o `payload` da decisão dentro.
  Ou seja o **conteúdo já aterrissa** — mas mal-autorado (`author_id:"webhook_adapter"`, `author_role:"system"`),
  sem masking e como evento de ciclo de vida, não como mensagem legível.
- **O "quem" é descartado.** O endpoint de resume é identity-blind. A identidade verificada do aprovador só
  existe no **claim** (`work_task_claim` carregou JWT → humano vira participante/especialista da sessão-filho).

### 9.2 Decisões (A5)

- **D-A5.1 — Store = stream canônico; forma = `message` (agents_only) + bloco de metadados de aprovação.**
  A decisão é uma **mensagem** no stream (aparece na narrativa/HistoricoTab, reusa o masking `content`/
  `original_content`), com um bloco estruturado no payload: `{choice, edits:[{field,before,after}],
  principal_type, decided_by, verification_class, threshold_in_force, attachments_viewed, decided_at}`. **Não** se
  adiciona valor ao `StreamEventTypeSchema` (evita rebuild de todos os consumidores TS) — a estrutura vive no
  payload da mensagem. `principal_type` (`human`|`agent`|`system`) torna "quem decidiu" legível como o TIPO de
  principal, ao lado do id (`decided_by`) e da classe de confiança — ver §9.5.
- **D-A5.2 — Autoria = identidade resolvida com CLASSE DE CONFIANÇA (simetria com o cliente).** Identificar o
  autor é como identificar o cliente por âncora/ANI: um identificador que o chamador passa, resolvido pela mesma
  entidade (o webhook adapter já hospeda o Identity Resolver) a um id + `verification_class` (`claimed`|
  `possessed`, o mesmo modelo do OTP de posse). A assimetria real não é "JWT vs não" — é o **uso** da identidade:
  para o aprovador ela é **autoridade + não-repúdio**, então uma decisão que importa exige grau `possessed`,
  exatamente como uma ação sensível do cliente exige OTP.
- **D-A5.3 — Console passa o JWT do aprovador no header do resume → grau `possessed`.** O header **é** a prova de
  posse (identidade lastreada por sessão autenticada viva). É **aditivo**, não substitui o `resume_token`
  (`resume_token`=capacidade "posso retomar este workflow?"; JWT=identidade "quem sou"). Header ausente → caminho
  externo (credencial/`system`, `claimed`). O ingress **verifica server-side** (decode HS256 local + `auth_jwt_secret`),
  reforça ABAC `approvals.decide` + `accessible_pools`, e **cruza com o claim** (`caller == claimant`, defende
  contra `resume_token` vazado). A atribuição mais forte é o **claim** (JWT-authed, exclusivo); o header deixa o
  ingress **ligar a decisão ao claimant verificado**.
- **D-A5.4 — Locus de escrita = webhook adapter, no resume interno.** No mesmo ponto onde já escreve
  `session_resumed` e onde agora verifica o JWT, o adapter grava a mensagem de auditoria e **injeta a identidade
  resolvida no escopo do SEGMENTO** do aprovador (invariante single-source de identidade de participante — nunca
  campo session-global). Masking dos campos sensíveis de `edits` via `content`/`original_content`. O **antes** do
  diff é capturado no submit (pré-preenchimento do form), não só o **depois**.
- **D-A5.5 — Gate de confiança no WORKFLOW, declarado pelo autor.** Mecanismo/confiança = plataforma; **limiar =
  negócio = autor**. O limiar vive no **passo do workflow** (`delegate`/`collect`/`choice`), **não** no DialogForm
  (preserva o invariante D3: form=conteúdo, workflow=controle). **Default seguro (inegociável):** ausência ⇒ exige
  `possessed`; rebaixar para `claimed` é escolha **explícita e auditável** (postura "degradação nunca é
  silenciosa"). O gate é um `choice` normal sobre o `verification_class` resolvido; o step-up (re-desafio OTP) reusa
  o `OtpService` existente. O **limiar-em-vigor** é gravado no registro (com a `deploy_version` do workflow) para o
  gate ser auditável.

### 9.3 Unificação externa × interna

Um ingress (resume do webhook), dois regimes de atribuição — só muda **a quem** a auditoria atribui:

| | Autor | `verification_class` | Ingress |
|---|---|---|---|
| **Externo** (`suspend`/sistema) | credencial webhook (endpoint id) | `claimed`/system | resume token-authed; o `system`-authored de hoje já é honesto — falta carimbar o endpoint + mascarar |
| **Interno** (`collect`/`delegate`/humano) | usuário aprovador (do JWT), ligado ao claim | `possessed` (Console logado) | resume + `Authorization: Bearer`; adapter verifica + injeta no escopo do segmento |

`collect`/`delegate` capturam o aprovador como participante (via claim) → habilitam a injeção verificada; um
`suspend` puro interno **não** captura ninguém e recairia em identidade client-asserted (evitar para aprovação).

### 9.4 Nota de arquitetura

Isto torna o endpoint de resume do webhook **auth-aware no caso interno** (canal token/máquina passando a
verificar JWT humano + ABAC). Aceito, desde que **deliberado**: o handler ramifica explicitamente — header
presente → verifica + atribui + gate; ausente → caminho externo de sempre. Não um verify silencioso no meio do
fluxo de canal.

### 9.5 Taxonomia de principal — `principal_type` (discriminador, não fusão de registros)

O autor de uma decisão pode ser de **três tipos**, cada um na sua **fonte nativa** (invariante single-source —
identidade não se duplica; cada principal vive na sua fonte). O rastro carrega um **discriminador**
`principal_type` no autor; **não** se funde tudo numa tabela de "usuário".

| Principal | Registro nativo | Auth / posse | `principal_type` |
|---|---|---|---|
| **Humano** (operador/aprovador) | auth-api (users + ABAC) | login/JWT → `possessed` | `human` |
| **Agente IA** (inclui auto-aprovação, D4) | agent-registry (agent_type/instance) | credencial de instância (JWT + permissions) | `agent` |
| **Sistema externo** (cliente de webhook) | **webhook registrado** (`plughub_wh_*`, Arc 4 CRUD) — **não** um user | token bearer (→ assinatura HMAC/mTLS p/ `possessed`) | `system` |

**Decisão sobre clientes externos:** cliente externo de webhook = **credencial de webhook nomeada**, NÃO um usuário
humano com login. Login/refresh/silent-reauth são afordâncias humanas; uma máquina porta credencial. Cada cliente
= um webhook registrado (id/nome/escopo por pool — "o POOL é a unidade endereçável"), e a auditoria atribui **àquele
webhook** (fecha o "falta carimbar o endpoint" da §9.3), com `verification_class: claimed`. O análogo de máquina
para `possessed` (não-repúdio de aprovação externa) é **assinatura de requisição** (HMAC por-cliente / mTLS), não
login — estende o eixo `claimed`/`possessed` para sistemas sem quebrar o modelo. `Pool.agent_kind` segue canônico
para a tipagem humano/IA (não reintroduzir segunda fonte de verdade).

---

## 10. As-built (implementado + validado E2E 2026-07-17)

Fatias A5.1–A5.6 no CHANGELOG (2026-07-17). Desvios/decisões que emergiram da implementação:

- **Verificação de JWT de usuário no channel-gateway (novo).** O canal não autenticava usuário do auth-api;
  criado `auth.py` reusável (`verify_user_jwt`/`abac_can`/`pool_in_scope`/`accessible_pools`/`bearer_from_header`)
  + env `PLUGHUB_AUTH_JWT_SECRET` **padronizada** (mesmo nome/segredo da analytics-api; adotável por outros módulos).
- **Bypass de elevados.** admin/supervisor bypassam ABAC `approvals.decide` **e** pool-scope (autoridade global;
  contas elevadas não carregam `module_config` por campo; e o pool-scope já é exercido no inbox/claim — re-exigir
  no resume criava assimetria claim↔resume). Operador comum segue barrado por capacidade + pool.
- **caller==claimant via árbitro.** Novo endpoint de leitura `POST /v1/work_queue/holder` (routing-engine) devolve
  o `instance_id` da claim lease; o ingress exige match + `instance==human-{sub}` (auth-api assina `sub=user_id`;
  Console usa `human-{userId}` → casam). Lease de TTL curto ausente **não** bloqueia (instance/identity + posse do
  token seguram). Respeita o invariante do árbitro único (o gateway não lê o Redis do routing direto).
- **Leitura sobre a stream, não ClickHouse.** A `message` de decisão vive na stream (Redis vivo / PG no close) e
  não é replicada ao ClickHouse (o transcript/HistoricoTab é ClickHouse) → `GET /api/approval_audit/:sessionId`
  (mcp-server, XRANGE, gate `approvals.operacao`) alimenta a nova seção da HistoricoTab.
- **Masking.** Net-pass de PII (CPF/cartão/e-mail/telefone) nos valores de `edits` no channel-gateway (o mecanismo
  primário field-level do DialogForm `masked` não chega ao servidor).

**Pendências menores (não-bloqueantes):** `segment_id` do aprovador carimbado pelo bridge (hoje `""` no v1);
leitura PG (sessão fechada) no endpoint de auditoria (hoje só Redis vivo); `attachments_viewed` populado quando o
viewer de anexos renderizar refs.
