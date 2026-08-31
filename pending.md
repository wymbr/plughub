# pending.md — trabalho ABERTO, agrupado por demanda

> **Este arquivo é a lista de trabalho. `done.md` é o índice do que fechou.**
> As regras vivem em `CLAUDE.md` § *Ledger de tarefas*; quem as impõe é
> `infra/test/probe_task_ledger.sh`, não a boa vontade de quem edita.
>
> **Nasceu em 2026-08-31**, das frentes vivas validadas uma a uma contra o `CHANGELOG.md`.
> A validação encontrou **nove marcadores desatualizados** — em todos, o corpo estava certo e o
> **título** estava velho. Daí a regra nº 1: título não afirma status.

## Estados

| estado | significa | sai daqui quando |
|---|---|---|
| `aberto` | a fazer | fecha → `done.md` |
| `bloqueado` | tem impedimento NOMEADO | o impedimento cai |
| `adiado` | decidido **não agora**, com gatilho declarado | o gatilho dispara |

`adiado` não é `done`. Existe para que decisão tomada não volte à mesa como pendência — que era o
defeito relatado pelo dono em 2026-08-31.

---

## `docs/adr/adr-contextstore-allowlist.md` — ContextStore como ALLOWLIST

Arco entregue até V3/D8 e a FATIA 1 da D9. **A V4 inverte o default e não é reversível.**

| id | tarefa | estado | evidência |
|---|---|---|---|
| ALW-01 | **V4** — inverter o default: campo sem regra deixa de ser acessível. Exige `unknown == 0` na auditoria | `bloqueado` por ALW-02/03/04 | `TODO.md:381` |
| ALW-02 | D9 #1 — choke point de escrita (hoje **12 `HSET` diretos**), que habilita o carimbo do `atributo` (D9.6). Maior esforço do arco | `aberto` | `TODO.md:326` |
| ALW-03 | D9 #2 — decidir onde mora a tela de cadastro; é a ausência dela que faz gente contornar | `aberto` | `TODO.md:326` |
| ALW-04 | D9 #3 — lista de domínios (critério de PAPEL); bloqueia o mapa crescer além dos domínios existentes | `aberto` | `TODO.md:326` |
| ALW-05 | V5 (metade) — fechar aliases | `bloqueado` — por **tempo**, não esforço: depende do contador decair | índice `CLAUDE.md` |
| ALW-06 | D7 (metade) — tela de proveniência: global × override por nó | `aberto` | índice `CLAUDE.md` |

---

## `docs/adr/adr-historico-unificado-duas-visoes.md` — ler um processo num lugar só

| id | tarefa | estado | evidência |
|---|---|---|---|
| HIS-01 | **F5** — `ContextStorePersister`, fase própria (desenho fechado no ADR §3) | `aberto` | `TODO.md:3486` |
| HIS-02 | Lente C — destino registrado | `aberto` | índice `CLAUDE.md` |

⚠️ O título de `TODO.md:3384` diz *"restam F4, F5"*. **A F4 fechou em 2026-08-25**
(`CHANGELOG.md:5559`). Título velho, corpo certo — o padrão que este arquivo existe para acabar.

---

## `docs/adr/adr-journey-session-segment-model.md` — D12: a janela de espera

| id | tarefa | estado | evidência |
|---|---|---|---|
| WAI-01 | Produtor da janela de espera no caminho **ATENDIDO**: contato que espera e é atendido não gera registro nenhum (medido: 21,35 s, zero linha) | `aberto` | `TODO.md:2589` |

⚠️ **Validação parcial, declarada.** A fatia B (2026-08-24, `CHANGELOG.md:6304`) criou um produtor
para o tier `max_wait_exceeded`, e o arco D14 (i/ii/iii) passou a carimbar `sla_target_ms` na saída
da fila. **Não confirmei** se o ramo atendido continua sem registro depois disso. Medir antes de
construir — o oposto foi o que produziu os nove títulos velhos.

---

## `docs/adr/adr-voice-media-plane.md` — voz própria / Arc 15 WebRTC

| id | tarefa | estado | evidência |
|---|---|---|---|
| VOZ-01 | **Provisionar o SFU.** Não há LiveKit em compose, env `LIVEKIT_*`, manifesto k8s, nem o SDK no `pyproject`; o canal roda em `_dev_mode`, que devolve token bem-formado e falso | `aberto` | `CLAUDE.md` § Arc 15 |
| VOZ-02 | Decidir o bridge PSTN→WebRTC via SIP Ingress | `bloqueado` por VOZ-01 — não se decide topologia de mídia sobre um SFU que não existe | idem |

---

## `docs/product/identity-resolver-fase-a-plano.md` — identidade e comércio conversacional

| id | tarefa | estado | evidência |
|---|---|---|---|
| IDN-01 | Fase C — `external_refs` + merge de clientes | `aberto` | `CLAUDE.md` § Business in Any Media |
| IDN-02 | Gate de identificação | `aberto` | idem |
| IDN-03 | Commerce-cards: checkout mascarado + repasse ao PSP | `aberto` | idem |
| IDN-04 | Novas `ChannelCapability` | `aberto` | idem |
| IDN-05 | Rejulgar nível (a), contrato delegate-por-pool e intake-flow — cortados por uma razão que caiu (tarefa **B1**) | `aberto` | `TODO.md` § Reexame dos 9 |

---

## `docs/adr/adr-human-approval-workflow-step.md` — aprovação humana

v1 **entregue em 2026-07-17**. O que resta é segunda onda, não v1 inacabado.

| id | tarefa | estado | evidência |
|---|---|---|---|
| APR-01 | **R1** — anexos, masking-por-role e ABAC `approvals` | `aberto` | `CHANGELOG.md:16658` |
| APR-02 | A6 — quatro-olhos (2 aprovadores) | `aberto` | `TODO.md:5157` |
| APR-03 | A6 — reatribuição por supervisor (= conferência padrão) | `aberto` | idem |
| APR-04 | A6 — notificações e SLA na inbox | `aberto` | idem |
| APR-05 | A6 — rework rate (Bancada / Arc 6) | `aberto` | idem |
| APR-06 | A6 — auto-aprovação (pool IA) | `aberto` | idem |
| APR-07 | Promote real: `invoke` de deploy no `efetuar_promocao`, hoje `complete` | `adiado` — não-objetivo v1. Gatilho: promoção agendada precisar valer em produção | idem |

---

## `docs/adr/adr-work-item-requeue-and-agent-affinity.md` — pull direcionado e wrap-up

Arco A–F completo. Restam duas dívidas de **verificação**, não de função.

| id | tarefa | estado | evidência |
|---|---|---|---|
| PUL-01 | Lacuna 2 — a janela entre os 180 s da lease e o prazo do item, em que o trabalho fica **invisível a todos os agentes**. Sem reaper, e **ninguém a mediu** | `aberto` | `TODO.md:1219` |
| PUL-02 | Gate re-executável da Camada F — hoje validada por medição manual instrumentada. *Arco completo sem gate versionado é lembrança, não verificação* | `aberto` | `CLAUDE.md` § Detach |

---

## `docs/arcos/customer-surveys.md` — módulo de pesquisas

| id | tarefa | estado | evidência |
|---|---|---|---|
| SUR-01 | **S7** — editor de DialogForm (ganha importância com a reversão do n8n: o conteúdo segue autorado em casa) | `aberto` | `CLAUDE.md` § Customer Surveys |
| SUR-02 | Nenhum produtor de **CES/PMF/FCR** existe | `aberto` | idem |
| SUR-03 | `value_label` ignorado em `CustomerVoicePage.tsx:161` | `aberto` | idem |
| SUR-04 | S5, S8, S9–S11 e o store per-response | `aberto` | idem |
| SUR-05 | Decidir se o S2 (runner genérico) volta a ter dono próprio (tarefa **C2**) | `aberto` | `TODO.md` § Reexame dos 9 |
| SUR-06 | Remedir o resíduo do `value_label`, citado com **arquivo errado** na triagem (tarefa **C4**) | `aberto` | idem |

---

## `docs/arcos/arc7-auth.md` — ABAC e escopo

O arco ABAC TOTAL (8 passos) está em `done.md`. Aqui fica só o que não fechou.

| id | tarefa | estado | evidência |
|---|---|---|---|
| AUT-10 | **(a) Afordância do escopo vazio.** A tela avisa que operar exige escopo de pool atribuído. Ao CRIAR um pool, oferecer sua inclusão no perfil do criador (`Pool.created_by` já existe) — ⚠️ **gatear a oferta por `config.permissions`**, senão vira auto-grant: criar pool exige `config.resources`, que é módulo DIFERENTE, e teríamos duas portas para a mesma decisão | `aberto` | decidido 2026-08-31 |
| AUT-12 | **(c) Papel semeado vira config**, ao lado de `seed_admin_email`/`password`/`name`, que já são config — hoje só `roles` ficou de fora, o que é incoerência e não decisão | `aberto` | decidido 2026-08-31 |
| AUT-15 | **Resto do campo `unrestricted`: só a superfície de PERSISTÊNCIA/API.** O caminho vivo fechou (não é emitido, não é lido, não decide escopo de supervisão). Sobram: `models.py` (4 campos) · `db.py` CRUD+DDL · guarda de capacidade `router.py:115-156` · coluna do banco (migração destrutiva, tratar à parte) · `_user_to_response` | `aberto` | 2026-08-31 |
| AUT-20 | **`api/registry.ts` — 23 chamadas sem credencial, e a outra origem.** Excluído da migração de propósito: `getBaseUrl()` devolve `VITE_REGISTRY_URL || http://localhost:3300`, ou seja **fura o proxy e vai a outra origem** (só funciona em dev, e mandar Bearer cross-origin merece decisão, não varredura). É o ÚNICO resto real. Usado por config-channels e `config-recursos/PoolsPage` | `aberto` | medido 2026-08-31 |
| AUT-21 | **Estender `probe_ui_credential_coverage.sh` além de analytics.** O portão existe desde 2026-08-28 e está verde, mas só cobre chamadas a **analytics** — e declara o próprio ponto cego (*"3 chamadas com URL não-literal, fora do alcance desta via; é assim que o CardRenderer escapou"*). A migração de hoje fechou 116 chamadas que ele não vigiava; sem estender o portão, a próxima nasce sem gate | `aberto` | derivado 2026-08-31 |
| AUT-19 | **"Vazio" tem duas causas e a tela não as distingue.** Decorre do lembrete do dono: pool vazio é **config válida**, então a lista vazia por falta de credencial fica indistinguível de escopo legitimamente vazio. O servidor já separa as duas no log (ausente × inválido); falta decidir se a RESPOSTA carrega a distinção, para a tela dizer *"sessão expirada"* em vez de *"nenhum pool"* | `aberto` | derivado 2026-08-31 |
| AUT-14 | **Deriva do check-all.** "Selecionar todos" é INSTANTÂNEO, não regra: concede os N pools de hoje e não o N+1 de amanhã. O sintoma é AUSÊNCIA — *"a falha mais provável desta ADR"*, nas palavras de `pool_auth.py:70`. Tornar visível (lista de "pools sem escopo atribuído" e/ou oferta no fluxo de criação), nunca deduzir em silêncio | `aberto` | derivado 2026-08-31 |
| AUT-09 | `analytics-api/auth.py:47` — `Principal.role == "admin"` carrega *"assinado pelo segredo de SISTEMA"*, não papel de produto (o próprio comentário diz: *"papel de produto não é papel de sistema"*). São **dois fatos no mesmo campo**, com o mesmo vocabulário que o passo 8 removeu em toda parte. Renomear para `principal_kind: system\|user` | `aberto` | medido 2026-08-31 |
| AUT-01 | Recorte de pool nos **AGREGADOS**: as `query_*` que servem as 12 rotas de `reports.py` não aceitam `accessible_pools` — **não é argumento esquecido, é filtro que não existe**. Inventá-lo por rota escolheria qual coluna é "o pool da agregação", e o precedente está medido (F2: um filtro de canal que não filtrava, **esvaziava**) | `aberto` | `CLAUDE.md` § Security |

⚠️ **Por que AUT-03 é `bloqueado` e não `aberto`.** O interruptor é único e os testes cobrem os
dois estados, então virar é uma linha. O que não está pronto é a **cauda**: `resolve_scope` passa a
devolver `[]`, e todo consumidor que trate lista vazia como "sem filtro" converte restrição em
liberação — sem erro, sem log, sem tela vermelha. É a assinatura da § Postura de Engenharia: o
valor plausível (um relatório que responde normalmente) escondendo o defeito. Inverter antes da
AUT-04 é trocar um vazamento conhecido por um vazamento invisível.

---

## `docs/product/journey-retorno-modelo-3-niveis-design.md` — Journey

Frente fechada (ver `done.md`); resta um item **adiado por decisão**.

| id | tarefa | estado | evidência |
|---|---|---|---|
| JRN-03 | Refrescar o cache `sessions.journey_id` no merge | `adiado` por decisão — as leituras vão por union-find. Gatilho: custo de leitura medido | `CHANGELOG.md:17736` |

---

## `sem-demanda` — trabalho sem decisão por trás

**Contador: 0.** Balde declarado, não omissão. Se crescer, é sinal de que está entrando trabalho
sem ADR nem spec — informação de gestão, não detalhe de formato. Segue o precedente do balde
`unknown` do rollup de capacidade: publicado como tipo próprio, contado, nunca dobrado no vizinho.

*(vazio)*
