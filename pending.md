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
| ALW-14 | **Campo cadastrado pela TELA não tem provisionamento e some num `--wipe`.** Nasceu com a E2 (2026-09-02), que deu à tela de `/config/context-map` as operações de criar, renomear e arquivar. O que o autor cadastra ali vive só no store: `infra/context-map/{tenant}.json` é semente, e a tela não escreve nele (nem poderia — é o browser). É a MESMA classe da ALW-12, agora por decisão em vez de omissão: a convenção da casa é seed-if-absent/DB-owned, e o ramo C do gate passou a medir por CONTENÇÃO com o excedente contado e NOMEADO — quem rodar o gate vê quantos campos vieram da tela. Caminhos possíveis: (a) rota de export do store para o arquivo; (b) aceitar a perda e documentar que `--wipe` exige recadastro; (c) a chave `masking.context_map_tenant` da CNS-16, que já está desenhada. **Gatilho:** o primeiro cadastro pela tela que alguém precise ver sobreviver a um wipe | `aberto` | `CHANGELOG.md` § 2026-09-02 E1–E7 |
| ALW-09 | Mover `cliente` e `contato` (16 campos, 19 aliases) de `session.*` para `core.customer.*` — decisão do dono na ALW-04, **BLOQUEADA por falta de caminho de escrita**. Medido em 2026-09-02: o hash do cliente (`{t}:ctx:customer:{id}`) existe só no SDK e no gêmeo de e2e, **nenhum serviço de produção o escreve**; o funil TS roteia por prefixo HARDCODED (`journey.`/`core.journey.`), então `core.customer.*` cairia no hash da SESSÃO em silêncio, a 4 h em vez de 90 d; e o funil Python RECUSA escopo não-sessão. Pré-requisito: os dois funis honrarem `resolveContextStore` e receberem `customer_id`. Blast radius medido: 48 arquivos | `bloqueado` | ADR §D9 #3 |
| ALW-15 | **Eco ao CLIENTE: a GATEWAY resolve, a ponta só executa.** ⚠️ **Reformulada em 2026-09-03 depois de levantar o código do webchat** — não é *"mandar o modo em vez do tipo"*, é **construir o resolvedor por canal que três comentários já prometem**. Medido: quem implementa o mascaramento que se vê no cliente é **o próprio cliente** (`webchat-test.html:368`: `masked_fields.length > 0` → `type=password`); a gateway repassa o stream literalmente (`send_json(msg)`). E são **três canais com três respostas, nenhuma vinda de config**: webchat decide no cliente · `sms.py:442` escreve um aviso à mão lendo `field.masked` (vocabulário OUTRO) · `whatsapp.py:564` usa `masked_fields` só para inventar rótulo, sem tratamento nenhum. **`supports_masked_input`, `masked_fallback` e `masked_fallback_message` têm ZERO consumidores** — promessa sem mecanismo em três casas (`channel-events.ts:102`, `models.py:174`, `bpm.ts:148`, esta última nomeando um `outbound_consumer` que não tem uma linha sobre masking). **A regra do desenho:** separar POLÍTICA (tipo × canal → modo; é da gateway, sempre) de RENDER (vira `type=password`, bipe, supressão de DTMF; acontece na ponta por necessidade). A gateway emite instrução no vocabulário do canal; a ponta executa **sem conhecer o catálogo**. ⚠️ **Não é enforcement e não deve ser vendido como tal**: um cliente pode ignorar a instrução — mover a decisão a torna CONSISTENTE e NOSSA, não imposta (mesma distinção da ALW-10: operador é fronteira, cliente é advisory). **Argumento que fecha a questão:** para WhatsApp, SMS e voz **não existe cliente que controlemos** — o desenho client-resolve é implementável em exatamente um canal, e esse canal é o fixture de teste. Fecha a MSK-01 de carona | `aberto` | `docs/adr/adr-contextstore-allowlist.md` |
| ALW-05 | V5 (metade) — fechar aliases | `bloqueado` — o critério foi **CORRIGIDO em 2026-09-02** e o bloqueio deixou de ser vago. Não é o contador decair: ele mede TRÁFEGO, e as grafias voltam a cada execução do demo. São TRÊS dimensões — produtor (estática) × história durável × **idade do alias**. Medido: dos 119 aliases, **A=49 têm produtor · B=21 estão no durável (ficam por regra) · C=49 removíveis** — mas **29 dos 49 entraram em 2026-09-01**, redes do rename do core armadas junto com a migração. **Gatilho:** re-rodar `infra/test/aliases_v5_buckets.py` quando o balde C tiver ≥14 dias de `git log -S` na semente E o durável cobrir esse período; então `infra/scripts/remove_dead_aliases.py --antes DATA --aplicar`. ⚠️ Duas correções que a execução produziu e que quem retomar precisa saber: minha proxy de idade por *entrada no mapa* é fraca (o que importa é quando o PRODUTOR parou, e medir isso hoje sai contaminado pelos commits que moveram o mapa de casa); e o custo é assimétrico — manter alias morto ≈ 0, remover cedo > 0 | ADR §V5 |

---

## `docs/product/contextstore-core-namespace-spec.md` — namespace do CORE do ContextStore

Reformulação do dono (2026-09-01), feita sem a linguagem do ADR da allowlist. Convergiu em
quatro pontos e acrescentou três. **É pré-requisito prático do arco ALW-\***: a padronização mata **23 dos 69 aliases**
sem tocar em vocabulário de skill, porque quem os escreve é o core. **CNS-02 fechou em
2026-09-01** — a reserva é o root `core.*`; `session.`/`journey.`/`segment.` ficam livres.

| id | tarefa | estado | evidência |
|---|---|---|---|
| CNS-16 | **Vocabulário de ContextStore por tenant** — chave SEPARADA (`masking.context_map_tenant`) mesclada na leitura, para que cada chave mantenha a semântica uniforme de config e o tenant não alcance `core` **por construção do mesclador**, não por portão que alguém pode esquecer. Desenhada na CNS-08 e **adiada por população zero**: medido em 2026-09-01, zero tenants sobrescrevem o mapa e a instalação tem um tenant. ⚠️ Um banco por tenant **não** substitui isto — troca *override substitui* por *reseed sobrescreve*, que é o mesmo dilema de duas direções da D7; a questão é de PROPRIEDADE do dado, não de topologia | `adiado` — gatilho: segundo tenant que precise de vocabulário próprio | `CHANGELOG.md` § 2026-09-01 CNS-08 |
| CNS-14 | **Quem tem `config.masking` escreve o `__global__`** — mandando `tenant_id: null`, sem que nada no modelo distinga *operador da plataforma* de *administrador do tenant*. ⚠️ **MEDIDO em 2026-09-02, e a medição reordena a tarefa.** (a) A premissa do título está VELHA: desde a ALW-03 o mapa é gateado por `config.context_map`, não por `config.masking` — `_ns_field('masking','context_map')` resolve para o campo próprio, então esse grant já não alcança o `core.*`. (b) População medida: **1 portador** de `config.masking` e **1** de `config.context_map` (`admin@plughub.local`, papéis `{admin,developer}`), num tenant só (`tenant_demo`, 8 usuários). Uma cerca aqui hoje não separa ninguém de nada, e é a *política contra população zero* que este repositório já catalogou. (c) A metade que MACHUCAVA — a escrita global sair em silêncio — **foi fechada**: `PUT` global devolve `shadowed_by[]` nomeando os tenants que a ignoram, e o banner da tela declara o escopo (ver ALW-06 no `done.md`). **Gatilho para reabrir:** um segundo tenant, OU um portador de `config.masking`/`config.context_map` que não seja operador da plataforma. Aí a decisão deixa de ser uma cerca e passa a ser um conceito que falta no modelo (*escopo de administração*), que é trabalho de desenho, não de portão | `adiado` | `router.py` `_reject_tenant_context_map` (docstring) |
| CNS-13 | **Cauda dos docs da CNS-11 — 248 → 62 ocorrências em 32 arquivos.** Fechados: `CLAUDE.md` (+ a invariante do `core.*`, que não existia lá), `docs/guias/context-store.md` e `context-store-taxonomy.md`, mais 7 docs vivos de arco/módulo, e a spec emendada. O `contextstore-cadastro-censo.md` **não foi reescrito** — é medição DATADA; ganhou nota de datação apontando a tabela de-para. ⚠️ **O que resta é majoritariamente narrativa**: ADRs e design docs onde o nome aparece dentro do raciocínio de uma decisão de época (`adr-contextstore-allowlist.md` 8, `adr-wrapup-detached-pull.md` 7, `limite-credito-3-niveis-design.md` 8). Cada um exige julgamento — reescrever raciocínio passado corrompe a evidência, que é o mesmo motivo de `CHANGELOG.md`/`TODO.md` estarem fora | `aberto` | medido 2026-09-01 |

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
| AUT-20 | **`api/registry.ts` — 23 chamadas sem credencial, e a outra origem.** Excluído da migração de propósito: `getBaseUrl()` devolve `VITE_REGISTRY_URL || http://localhost:3300`, ou seja **fura o proxy e vai a outra origem** (só funciona em dev, e mandar Bearer cross-origin merece decisão, não varredura). É o ÚNICO resto real. Usado por config-channels e `config-recursos/PoolsPage` | `aberto` | medido 2026-08-31 |
| AUT-21 | **Estender `probe_ui_credential_coverage.sh` além de analytics.** O portão existe desde 2026-08-28 e está verde, mas só cobre chamadas a **analytics** — e declara o próprio ponto cego (*"3 chamadas com URL não-literal, fora do alcance desta via; é assim que o CardRenderer escapou"*). A migração de hoje fechou 116 chamadas que ele não vigiava; sem estender o portão, a próxima nasce sem gate | `aberto` | derivado 2026-08-31 |
| AUT-09 | `analytics-api/auth.py:47` — `Principal.role == "admin"` carrega *"assinado pelo segredo de SISTEMA"*, não papel de produto (o próprio comentário diz: *"papel de produto não é papel de sistema"*). São **dois fatos no mesmo campo**, com o mesmo vocabulário que o passo 8 removeu em toda parte. Renomear para `principal_kind: system\|user` | `aberto` | medido 2026-08-31 |
| AUT-26 | **Três pacotes declaram testes e não têm runner na imagem** — `sdk` e `gitagent` (sem container algum) e `mcp-server-knowledge` (tem container, a imagem não traz vitest). É o mesmo *"declara e não instala"* que o gêmeo Python mediu nas 14 imagens. O `probe_ts_suites.sh` os **NOMEIA a cada execução** em vez de omitir, e de propósito não reprova por isso: gate que nasce vermelho ensina a ser ignorado | `aberto` | medido 2026-08-31 |
| AUT-25 | **O `platform-ui` não tem suíte de teste — zero `*.test.*`, sem vitest/jest.** ⚠️ *Metade FECHADA em 2026-08-31:* o typecheck do pacote virou o ramo C do `probe_apifetch_reauth.sh`, e ele existe porque compilar dois arquivos **não pega colisão de nome** — foi assim que a varredura da AUT-18 quebrou quatro arquivos e o build sem nada ficar vermelho. O que sobra é a decisão: o pacote ganha suíte própria (custo: dependência + config + CI) ou fica com harnesses pontuais compilando arquivos de produção, como o da AUT-19? | `aberto` | decidido em parte 2026-08-31 |
| AUT-22 | **`config.permissions` deve ser `scopable`?** Hoje e `false`: a capacidade e do tenant INTEIRO, logo auto-conceder um pool **nao e escalacao para o detentor legitimo** — ele ja manda no escopo de todos. A tela de Access lista os 36 pools porque `GET /v1/pools` filtra so por tenant. Se algum dia existir "admin regional", isso muda e a lista irrestrita vira furo. **Pergunta de desenho, nao defeito** | `adiado` — gatilho: surgir papel de administracao de permissoes com escopo | ADR granularidade, secao "NAO decide" |
| AUT-28 | **Recorte de pool nas duas rotas de DÍVIDA declarada** (`_SCOPE_DEBT` em `reports.py`): `/reports/campaigns` (`collect_events` só tem `collect_token`/`instance_id`) e `/reports/evaluator-calibration` (`calibration_events` só tem `evaluator_id`). Há um caminho concebível para a primeira — `instance_id` → `workflow_events.pool_id` — e ele **não foi construído de propósito**: as duas tabelas estão VAZIAS, então o join entraria sem nunca ter sido visto funcionar (é como nasceu o filtro de canal da F2, que filtrava esvaziando). O censo do `probe_report_row_scope.sh` as CONTA a cada execução | `adiado` — gatilho: qualquer das duas tabelas ganhar produtor | `CLAUDE.md` § Security |
| AUT-29 | **A recusa por escopo precisa de um jeito de saber "este chamador alcança o tenant inteiro", e ele não existe.** ⚠️ *Linha CORRIGIDA em 2026-08-31: a versão anterior dizia `bloqueado por AUT-15`, presumindo que a AUT-15 entregaria o claim `unrestricted` ao admin — e a AUT-15 é o **oposto**, a remoção do campo. Eu a escrevi errado no fecho da AUT-01, no mesmo dia; a dependência estava invertida.* O fato medido continua de pé: `admin@plughub.local` carrega uma LISTA de 36 pools, `accessible_pools is None` só vale para principal de SERVIÇO, e por isso *"recuse o chamador escopado"* devolveria 403 ao administrador. Com o campo removido, a decisão do dono é que **escopo é sempre enumerado** — então o discriminador teria de ser *"o escopo cobre o universo de pools do registry?"*, dependência nova com caminho de degradação próprio. Só se paga se as tabelas da AUT-28 ganharem produtor | `adiado` — gatilho: AUT-28 sair de `_SCOPE_DEBT` | `CLAUDE.md` § Security |
| AUT-31 | **`gate_sla_segment_target.sh` vermelho no PRÓPRIO tenant sintético** (`t_gate_sla`): o `/reports/pools/queue` devolve `series: []` e `by_pool: []` para a janela que ele semeia, e os três veredictos saem vazios. **Não é escopo nem AUT-01** — medido na mesma rodada, a rota vive para `tenant_demo` e devolve o MESMO número (78 séries · 11 pools) para o admin e para o principal de serviço. Encontrado ao consertar o `mk_unrestricted_principal.sh`, que destravou os outros dois gates da mesma família | `aberto` | `CLAUDE.md` § Security (D14) |
| AUT-32 | **Coluna física `auth.users.unrestricted` sobrevive ao código.** O `DROP COLUMN` é irreversível e apagaria as 2 linhas que hoje a têm `true`; o precedente da casa é o oposto (`agent_group_members`/`agent_group_shifts`, 2026-07-02: *"podem existir fisicamente em bancos antigos — o código não mais as cria/lê/escreve"*). O `CREATE TABLE` deixou de criá-la e o `ALTER ... ADD COLUMN` saiu, então ela não renasce a cada boot. Fechar exige decidir se vale uma migração destrutiva por uma coluna inerte | `adiado` — gatilho: próxima migração destrutiva já planejada em `auth.users` | `CHANGELOG.md` § lápide do `unrestricted` |

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

## `docs/guias/mention-protocol.md` — protocolo @mention

⚠️ **O invariante declarado NÃO é imposto pelo gate que existe para impô-lo** (medido
2026-09-01). O guia diz *"Agentes IA em conferência **não** podem usar `@mention`"* (§42)
e, duas linhas acima, define a regra como *"apenas `role: primary` ou `role: human`"*
(§40). As duas frases **não são a mesma coisa**: `primary` é POSIÇÃO na sessão, não
espécie do participante. Medido em histórico real: **1144 segmentos `native/primary` +
100 `ai/primary`** contra 333 `human/primary` — o gate deixa passar exatamente a
população que o comentário dele diz excluir. O discriminador de espécie está na MESMA
entrada do roster, sem ser lido: `agent_type` (`human` | `native` | `ai`).

⚠️ **E ele tem o MESMO defeito estrutural do gate do avaliador**: resolve o papel de um
`participant_id` vindo do **input**, tendo o `instance_id` assinado em mãos
(`senderInstanceId`, extraído na l.403 e usado para outra coisa na l.426). A tabela dos
dois gates vive em `docs/adr/adr-remove-agent-role-axis.md` § *Achado estrutural
compartilhado* — escrita **uma vez**, não repetida aqui.

⚠️ **Eixo DIFERENTE do `agent_role`** (grupo `CAP`): campo, casa, produtor e consumidor
distintos. O que os une é o defeito de forma, não o assunto.

| id | tarefa | estado | evidência |
|---|---|---|---|
| MEN-01 | **Antes de decidir, responder a pergunta que resolveu o gate do avaliador: QUAL cenário isto impede?** Não foi feita para o @mention, e foi justamente pular essa pergunta que produziu duas respostas erradas no grupo `CAP`. Só depois dela as opções fazem sentido: **(A)** ler `agent_type` e o invariante vira verdadeiro · **(B)** remover o gate e retirar as 4 cópias da promessa (`CLAUDE.md` ×2, guia §40/§42/§80, comentário em `session.ts:611`) · **(C)** o que a resposta do avaliador sugere — se não houver cenário, remover. **Não é opção deixar como está** | `bloqueado` — precisa da análise de cenário | ADR `adr-remove-agent-role-axis.md` § achado estrutural |
| MEN-02 | **A aplicação é ASSIMÉTRICA e só um caminho tem gate.** WS `agent-ws` (`server.ts:3638` — o caminho VIVO do Console) chama `routeMentions` **sem checagem nenhuma**, por desenho declarado (*"o WS conhece o agente pela conexão"*). MCP `message_send` tem o gate. Aqui é defensável — cada camada gateia onde consegue provar —, mas precisa ser **decidido e escrito**, não herdado | `bloqueado` por MEN-01 | `TODO.md` § gate de @mention |
| MEN-03 | **Mensagem de log aponta para a casa ABANDONADA do campo.** O gate resolve pelo roster `session:{id}:participants` desde a Fatia B do §1055, mas o aviso ainda diz *"hash `{t}:agent:instance:{id}` sem campo `role`"* — manda quem depura para o lugar errado. Medido: aquele hash tem **0 de 5** com o campo, nenhum escritor e nenhum leitor. **Independente do MEN-01 e imediato: 1 linha** | `aberto` | `TODO.md` § gate de @mention |
| MEN-04 | **Só há EXPOSIÇÃO medida, não DANO.** O caminho está aberto (LLM recebe a tool porque `permissions: []` = sem filtro; a IA é `primary`; o gate passa). NÃO medido que alguém passou: 0 @mentions na janela de log, anteriores perdidos no rebuild. Fecha com um contato real em pool de IA com `@alias` no texto — os três desfechos do gate já logam diferente. **Publicar exposição como dano é a D14.1 ao contrário** | `aberto` — precisa de tráfego | `TODO.md` § gate de @mention |
---

## `docs/adr/adr-remove-agent-role-axis.md` — remoção do terceiro eixo

O gate do avaliador **sai**, e `agent_role` com ele. Não porque o eixo incomoda: porque
**não impede nenhum cenário que alguém consiga descrever**. Medido em 2026-09-01:

- ele **não autentica o chamador** — o `session_token` carrega `instance_id` (identidade
  assinada) e as duas tools o DESCARTAM, consultando o papel do `participant_id` que veio
  do **input**. Passa quem nomear qualquer avaliador;
- o cenário de PII **não fecha**: o `ReplayContext` exige `session_id` fechado **e**
  amostrado **e** dentro do TTL de 1 h, e nenhuma tool devolve lista de sessões a agente;
- o que ele de fato separa é **avaliador mal configurado** — detecção de erro de deploy,
  não fronteira de segurança.

⚠️ A versão anterior deste grupo propunha **trocar o eixo** (grant de capacidade, 3
fases). Foi **refutada por medição no mesmo dia** e está preservada dentro do ADR § —
sem ela, "trocar o eixo" é reproposto em três meses.

| id | tarefa | estado | evidência |
|---|---|---|---|
| CAP-07 | **A borda `invoke` tem ZERO destinos configurados.** Fechada a CAP-06, a permissão atravessa e a chamada morre na parede seguinte: `_resolveDomainUrl` exige `MCP_SERVER_{NOME}_URL` e **não há nenhuma** em compose algum (medido 2026-09-01, `docker inspect` e `grep` no `docker-compose.demo.yml`). Não é regressão nem urgência: é a razão **restante** de a borda não ter tráfego, e ela **grita** — a recusa nomeia a variável exata que falta, ao contrário do `permission_denied` mudo que a antecedia. Gatilho para fechar: o primeiro agente `external-mcp` real, que é quando alguém precisa de um domain server alcançável | `adiado` — gatilho: primeiro `external-mcp` configurado | medido 2026-09-01 |
| CAP-08 | **A declaração `tools[]` não é conferida contra nada — o casamento skill×MCP é ASSUMIDO.** A "validação cruzada" de `packages/agent-registry/src/routes/skills.ts:53-60` é um `TODO` com `void mcpServers`, enquanto o cabeçalho do arquivo *afirma* que ela existe (*"mcp_server em tools deve estar registrado no tenant"*) — promessa sem mecanismo, família do DDL de `participation_intervals`. Medido 2026-09-01 com typo deliberado (`validate_pinn`): o registry **aceita**, o `agent_login` **assina** a permissão inexistente, e chamar a tool CERTA devolve `permission_denied`. ✅ **Atenuante medido:** a recusa nomeia as DUAS listas lado a lado (`falta 'validate_pin'` · `autorizadas: [validate_pinn]`), então o typo é diagnosticável numa olhada — é falha **não prevenida**, não falha silenciosa. Por isso é dívida, não defeito urgente. ⚠️ **A associação NÃO é tabela a manter à mão: o MCP tem descoberta nativa (`tools/list`), e ninguém a chama no repo (0 ocorrências).** Um mapa paralelo seria segunda casa afirmando o mesmo fato. ⚠️ **São DOIS universos** (`InvokeStepSchema`): forma nativa (`tool:` solto → `mcp-server-plughub`, **sem gate nenhum**, `mcpCall` cru) × forma completa (`target.{mcp_server,tool}` → borda `invoke`, gateada). Declarar `tools[]` para a primeira não autoriza nem nega nada hoje. **Forma de fechar, pelas regras já decididas na casa:** catálogo derivado por `tools/list` serve de **afordância** (autocomplete no editor) e o **veredicto** vem do servidor no `PUT` (`adr-skill-flow-editor-validation.md`); e **`unverifiable ≠ invalid`** (CAP-04) — domain server fora do ar degrada para aceito-com-aviso NOMEANDO o que não foi verificado, nunca recusa (senão publicar skill passa a exigir todo domain server no ar) | `bloqueado` — falta a fonte de verdade de *"servidor registrado no tenant"*: não há tabela `mcp_servers` (`GET /v1/mcp-servers` → 404) e a resolução é por env var (ver CAP-07) | medido 2026-09-01 |
| CAP-10 | **Política de credencial das TOOLS da plataforma — 48 dívidas, e a medição REBAIXOU a urgência.** Estado travado pela CAP-09: `ok=23 · isento=1 · divida=48` de **72**. ✅ **Destravado pela CAP-11:** a única borda do repositório (nginx gerado no `packages/platform-ui/Dockerfile`) **não publica o transporte MCP** — pela borda `/sse` devolve `text/html`, e só direto na 3100 devolve `text/event-stream`. Logo as 48 dívidas estão atrás de uma porta que nenhum deploy descrito no repositório expõe **por desenho de borda** — mas a expõe **por publicação de porta** (`3100:3100` em `docker-compose.demo.yml` e `.full.yml`, e nenhum compose a restringe). ⚠️ **A decisão que sobra é de TOPOLOGIA antes de código:** ou a porta deixa de ser publicada (e aí as 48 são dívida de defesa-em-profundidade), ou continua publicada (e aí são exposição real). Escolher tool a tool sem essa decisão é decidir dano sem decidir alcance. ⚠️ Dívidas não homogêneas: `transcript_get` serve transcrição e tem irmão gateado no analytics (*"duas portas, só uma trancada"*, 2026-08-28); `pool_promote`/`skill_deploy` promovem deploy; `context_set` escreve no ContextStore; `calendar_*` e `system_availability_check` podem ser isenção legítima. Todo fechamento move a linha de `divida` p/ `ok` no probe — é o que impede a política de existir só em prosa. ⚠️ **O `session_token` é AUTO-SERVIÇO, e isso rebaixa as 23 "gateadas":** medido, uma conexão anônima ao `/sse` chama `agent_login` nomeando um `skill_id` (que o registry serve **sem credencial**) e recebe token **assinado**. `agent_login` não pode exigir token — é o emissor, e a isenção está certa —, mas ela só era inócua enquanto o transporte tivesse dono. Logo *"23 de 72 verificam token"* não é 23 protegidas: o portão existe e o **dispensador está aberto** para quem alcança a porta | `aberto` — precede-a a decisão de publicar ou não a 3100 | `CHANGELOG.md` 2026-09-01, `infra/test/probe_mcp_tool_guard_census.sh` |
| CAP-15 | **Eliminar a publicação da 3100 de vez — e o gatilho é a topologia de deploy, não esforço.** O passo intermediário fechou em 2026-09-01 (CAP-13): o bind é `127.0.0.1:3100:3100` e o transporte MCP saiu da LAN (medido antes: `Test-NetConnection 192.168.1.124:3100` → **ACEITA**; depois → **recusada**, com a 5174 de controle seguindo ACEITA nas duas rodadas). O que sobra é remover a linha `ports:` inteira, e ela ainda tem consumidores REAIS de host: o proxy do `vite.config.ts` (dev), a suíte e2e (`MCP_SERVER_URL`) e os probes de `infra/test/` — todos em `localhost`. Fechar exige **mover essas três famílias para dentro da rede do compose** (o precedente existe: o `probe_release_reclaim_race.sh` já roda por `docker compose exec` justamente para medir de dentro). ⚠️ **A bifurcação que decide se isto vale a pena:** se o alvo for deploy DISTRIBUÍDO — contêineres em máquinas diferentes —, a porta volta a ser NECESSÁRIA e a pergunta deixa de ser topologia: vira **autenticação de transporte**, e aí o trabalho é outro (o padrão já existe na casa: `MCP_INTERNAL_SERVICE_TOKEN`, usado pelas duas `/internal/*`, falhando FECHADO). Investir em remover a porta antes dessa decisão pode ser trabalho jogado fora. ⚠️ **Ao mover para a borda, NÃO mapear `/sse` e `/messages` no nginx** — mapear tudo transfere a exposição da 3100 para a 5174 e desfaz o ganho; hoje a borda devolve `text/html` no `/sse`, e é isso que faz as 48 dívidas de tool da CAP-09 serem inalcançáveis de fora. Gate que trava o estado atual: ramo F do `probe_mcp_rest_surface.sh` (declaração nos dois composes × bind vivo, provado falseável nas duas metades) | `adiado` — gatilho: decidir se o alvo é rede compartilhada ou deploy distribuído | `CHANGELOG.md` 2026-09-01, `infra/test/probe_mcp_rest_surface.sh` ramo F |
| CAP-14 | **As nove rotas da CAP-12 exigem CREDENCIAL e não recortam LINHA — e a pior delas nem tem tenant.** São dois fatos, e a analytics-api pagou para aprender que são (*"EXIGIR CREDENCIAL e RECORTAR LINHA são dois fatos"*, 08-29 × 08-30). Hoje qualquer operador autenticado lê a conversa de **qualquer** sessão por `GET /api/conversation_history/{id}` — e a chave que ela lê, `session:{id}:messages`, **não tem prefixo de tenant**, então nem o isolamento por tenant existe ali (as irmãs `copilot_state` e `supervisor_capabilities` resolvem o tenant pelo `resolveSessionTenant` e recusam o indeterminável; esta não tem o que resolver). ⚠️ **Agrava por repetição de padrão:** o irmão do mesmo dado (`analytics-api /v1/transcript/sessions/{id}`) é gateado **e escopado** desde 2026-08-30 — de novo *duas portas para o mesmo dado, e só uma recorta*, que é exatamente a forma do achado de 08-28. ⚠️ **Fechar não é copiar o predicado do analytics:** lá o recorte é sobre ClickHouse (`_session_scope_clause`, união entrou-por ∪ atendeu-por), e aqui a fonte é Redis vivo; o decisor teria de ser o `resolveSessionTenant` + o `accessible_pools` do JWT, e a decisão sobre o INDETERMINÁVEL precisa ser medida antes (no analytics foram 10 de 947). Gatilho: o primeiro tenant com operadores que não devem se ver, ou o primeiro pedido de multi-tenant nesta porta | `adiado` — gatilho declarado acima | `infra/test/probe_mcp_rest_surface.sh` § DÍVIDA DE ESCOPO |
---

## `docs/guias/masked-input.md` — mascaramento de entrada por canal

| id | tarefa | estado | onde |
|---|---|---|---|
| MSK-01 | **Campo `masked` chega a canal que não sabe mascarar, e nada acontece.** Medido em 2026-09-03: o pool `limite_ia` declara `channel_types: [webchat, whatsapp]` e roda `skill_limite_entrada_v1`, que mascara **CVV**. No WhatsApp isso vira campo de formulário comum — sem fallback, sem aviso, sem recusa. O `supports_masked_input: false` que a tabela de `channel-events.ts:143-148` atribui a whatsapp/sms/email é **comentário sem leitor**. ⚠️ **Exposição × dano, separados de propósito** (D14.1): exposição é REAL e declarada; dano medido é **~zero** — 2 sessões WhatsApp em toda a instalação, contra 565 webhook e 413 webchat, e sem credencial real de provedor aqui. Não é incidente; é porta que existe e não está fechada. **Tem desfecho BARATO e INDEPENDENTE da ALW-15** — tirar `whatsapp` de `limite_ia`, ou fazer o adapter RECUSAR alto o step masked — e é por isso que é item próprio: não precisa esperar o resolvedor por canal. **Gatilho para virar urgente:** tráfego WhatsApp real, ou um segundo pool com masked em canal sem suporte | `aberto` | `packages/channel-gateway/.../adapters/whatsapp.py:564` |

## `sem-demanda` — trabalho sem decisão por trás

**Contador: 2.** Balde declarado, não omissão. Se crescer, é sinal de que está entrando trabalho
sem ADR nem spec — informação de gestão, não detalhe de formato. Segue o precedente do balde
`unknown` do rollup de capacidade: publicado como tipo próprio, contado, nunca dobrado no vizinho.

| id | tarefa | status | âncora |
|---|---|---|---|
| ROT-01 | **Link direto e F5 em qualquer rota `/config/*` da UI devolvem 422.** O nginx do `platform-ui` faz `proxy_pass` de `/config/*` para o config-api, que responde `{"loc":["query","tenant_id"]}`. O prefixo da UI colide com o da API. Medido em 2026-09-02 nas oito rotas do grupo (`/config/masking` inclusive — é **pré-existente**, não veio da tela nova). Não quebra o uso normal porque a navegação é client-side; quebra bookmark, refresh e qualquer link compartilhado. Consertar tem consequência para os dois lados (prefixo da UI × `location` do nginx), por isso é decisão, não conserto óbvio | `aberto` | `packages/platform-ui/Dockerfile` (nginx inline) |
| GAT-01 | **37 dos 103 gates estão no `gates.manifest`; os outros 66 ninguém roda.** Achado de passagem em 2026-09-02: os dois gates do arco ALLOWLIST não estavam registrados desde a CNS-06, e foram acrescentados no mesmo commit — mas o número diz que isso não é caso isolado, e sim o padrão. É exatamente o modo de falha que o `run_gates.sh` foi escrito para impedir (*"gate que ninguém roda não é cobertura, e o modo de falha é AUSÊNCIA"*), acontecendo dentro de casa. A tarefa **não é registrar os 66** — parte deles é assistido, obsoleto ou exige ambiente; é **triar com critério declarado** e deixar a ausência CONTADA, para que o manifesto pare de parecer completo por ser uma lista. | `aberto` | `infra/test/run_gates.sh` (cabeçalho) |
