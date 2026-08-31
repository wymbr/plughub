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
| AUT-15 | **Resto do campo `unrestricted`: só a superfície de PERSISTÊNCIA/API.** O caminho vivo fechou (não é emitido, não é lido, não decide escopo de supervisão). Sobram: `models.py` (4 campos) · `db.py` CRUD+DDL · guarda de capacidade `router.py:115-156` · coluna do banco (migração destrutiva, tratar à parte) · `_user_to_response` | `aberto` | 2026-08-31 |
| AUT-20 | **`api/registry.ts` — 23 chamadas sem credencial, e a outra origem.** Excluído da migração de propósito: `getBaseUrl()` devolve `VITE_REGISTRY_URL || http://localhost:3300`, ou seja **fura o proxy e vai a outra origem** (só funciona em dev, e mandar Bearer cross-origin merece decisão, não varredura). É o ÚNICO resto real. Usado por config-channels e `config-recursos/PoolsPage` | `aberto` | medido 2026-08-31 |
| AUT-21 | **Estender `probe_ui_credential_coverage.sh` além de analytics.** O portão existe desde 2026-08-28 e está verde, mas só cobre chamadas a **analytics** — e declara o próprio ponto cego (*"3 chamadas com URL não-literal, fora do alcance desta via; é assim que o CardRenderer escapou"*). A migração de hoje fechou 116 chamadas que ele não vigiava; sem estender o portão, a próxima nasce sem gate | `aberto` | derivado 2026-08-31 |
| AUT-09 | `analytics-api/auth.py:47` — `Principal.role == "admin"` carrega *"assinado pelo segredo de SISTEMA"*, não papel de produto (o próprio comentário diz: *"papel de produto não é papel de sistema"*). São **dois fatos no mesmo campo**, com o mesmo vocabulário que o passo 8 removeu em toda parte. Renomear para `principal_kind: system\|user` | `aberto` | medido 2026-08-31 |
| AUT-26 | **Três pacotes declaram testes e não têm runner na imagem** — `sdk` e `gitagent` (sem container algum) e `mcp-server-knowledge` (tem container, a imagem não traz vitest). É o mesmo *"declara e não instala"* que o gêmeo Python mediu nas 14 imagens. O `probe_ts_suites.sh` os **NOMEIA a cada execução** em vez de omitir, e de propósito não reprova por isso: gate que nasce vermelho ensina a ser ignorado | `aberto` | medido 2026-08-31 |
| AUT-25 | **O `platform-ui` não tem suíte de teste — zero `*.test.*`, sem vitest/jest.** ⚠️ *Metade FECHADA em 2026-08-31:* o typecheck do pacote virou o ramo C do `probe_apifetch_reauth.sh`, e ele existe porque compilar dois arquivos **não pega colisão de nome** — foi assim que a varredura da AUT-18 quebrou quatro arquivos e o build sem nada ficar vermelho. O que sobra é a decisão: o pacote ganha suíte própria (custo: dependência + config + CI) ou fica com harnesses pontuais compilando arquivos de produção, como o da AUT-19? | `aberto` | decidido em parte 2026-08-31 |
| AUT-22 | **`config.permissions` deve ser `scopable`?** Hoje e `false`: a capacidade e do tenant INTEIRO, logo auto-conceder um pool **nao e escalacao para o detentor legitimo** — ele ja manda no escopo de todos. A tela de Access lista os 36 pools porque `GET /v1/pools` filtra so por tenant. Se algum dia existir "admin regional", isso muda e a lista irrestrita vira furo. **Pergunta de desenho, nao defeito** | `adiado` — gatilho: surgir papel de administracao de permissoes com escopo | ADR granularidade, secao "NAO decide" |
| AUT-01 | Recorte de pool nos **AGREGADOS**: as `query_*` que servem as 12 rotas de `reports.py` não aceitam `accessible_pools` — **não é argumento esquecido, é filtro que não existe**. Inventá-lo por rota escolheria qual coluna é "o pool da agregação", e o precedente está medido (F2: um filtro de canal que não filtrava, **esvaziava**) | `aberto` | `CLAUDE.md` § Security |

⚠️ **Por que AUT-03 é `bloqueado` e não `aberto`.** O interruptor é único e os testes cobrem os
dois estados, então virar é uma linha. O que não está pronto é a **cauda**: `resolve_scope` passa a
devolver `[]`, e todo consumidor que trate lista vazia como "sem filtro" converte restrição em
liberação — sem erro, sem log, sem tela vermelha. É a assinatura da § Postura de Engenharia: o
valor plausível (um relatório que responde normalmente) escondendo o defeito. Inverter antes da
AUT-04 é trocar um vazamento conhecido por um vazamento invisível.

---

## `docs/adr/adr-abac-module-granularity-and-delegation.md` — granularidade e delegacao ABAC

Nasceu da demanda do dono (2026-08-31): rotatividade alta exige que o supervisor contrate sem
poder reescrever a propria fronteira. **A ordem G1 -> G2 -> G3 e inegociavel** — revogar antes
de existir o veiculo tira a contratacao do supervisor sem dar nada em troca.

| id | tarefa | estado | evidencia |
|---|---|---|---|
| MOD-01 | **G0 — censo de `config.permissions`**: quem detém no banco × quem `role_defaults`+seed declaram. Medido 2026-08-31 (recontado ao fim do dia): **`supervisor@` detém contra a declaração** — o `seed_auth.py` o exclui de propósito, com comentário que descreve a consequência. *(O `useradmin@` também detinha; foi limpo como efeito colateral de rodar o `probe_config_permissions_split`, cujo `PUT module-config` substitui o config inteiro — evidência de que a deriva se corrige sozinha só onde há probe, e é por isso que o censo precisa existir.)* Instrumento antes de qualquer mudança | `aberto` | medido 2026-08-31 |
| MOD-02 | **G1 — D3+D4**: rota `apply-template` (capacidade vem do template, nunca do corpo) + flag `delegable` + **recusa derivada** para template que conceda campo privilegiado. Sem a D4 o desenho e caminho de escalacao: `config.users` + template admin + senha + login | `bloqueado` por MOD-01 | ADR D3/D4 |
| MOD-03 | **G2 — D5**: decidir QUEM aplica (campo proprio x junto de `config.users` x pelo `access`) e implementar a criacao de usuario por template na UI | `bloqueado` por MOD-02 | ADR D5 (aberta) |
| MOD-04 | **G3 — revogar `config.permissions`** de `supervisor@` e de quem mais o censo apontar, pela API oficial | `bloqueado` por MOD-03 | ADR fase G3 |
| MOD-05 | **G4 — corte #1**: `contacts.operacao` -> `monitorar` x `atender`. Inclui backfill de todo portador do campo largo, senao o corte rebaixa em silencio | `aberto` | ADR D6 #1 |
| MOD-06 | **G5 — cortes #2 e #3**: `workflows.operacao` (Editor x Monitor) e `config.resources` (Pools x Skills) | `bloqueado` por MOD-05 | ADR D6 #2/#3 |
| MOD-07 | **G6 — corte #4**: recorte de `contacts.visualizar` por superficie de Analytics | `bloqueado` por AUT-01 | ADR D6 #4 |

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
