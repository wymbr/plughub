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
| ALW-15 | **Eco ao CLIENTE: a GATEWAY resolve, a ponta só executa.** ⚠️ **Reformulada em 2026-09-03 depois de levantar o código do webchat** — não é *"mandar o modo em vez do tipo"*, é **construir o resolvedor por canal que três comentários já prometem**. Medido: quem implementa o mascaramento que se vê no cliente é **o próprio cliente** (`webchat-test.html:368`: `masked_fields.length > 0` → `type=password`); a gateway repassa o stream literalmente (`send_json(msg)`). E são **três canais com três respostas, nenhuma vinda de config**: webchat decide no cliente · `sms.py:442` escreve um aviso à mão lendo `field.masked` (vocabulário OUTRO) · `whatsapp.py:564` usa `masked_fields` só para inventar rótulo, sem tratamento nenhum. **`supports_masked_input`, `masked_fallback` e `masked_fallback_message` têm ZERO consumidores** — promessa sem mecanismo em três casas (`channel-events.ts:102`, `models.py:174`, `bpm.ts:148`, esta última nomeando um `outbound_consumer` que não tem uma linha sobre masking). **A regra do desenho:** separar POLÍTICA (tipo × canal → modo; é da gateway, sempre) de RENDER (vira `type=password`, bipe, supressão de DTMF; acontece na ponta por necessidade). A gateway emite instrução no vocabulário do canal; a ponta executa **sem conhecer o catálogo**. ⚠️ **Não é enforcement e não deve ser vendido como tal**: um cliente pode ignorar a instrução — mover a decisão a torna CONSISTENTE e NOSSA, não imposta (mesma distinção da ALW-10: operador é fronteira, cliente é advisory). **Argumento que fecha a questão:** para WhatsApp, SMS e voz **não existe cliente que controlemos** — o desenho client-resolve é implementável em exatamente um canal, e esse canal é o fixture de teste. Fecha a MSK-01 de carona. ⚠️ **Onde a política mora ficou decidido em 2026-09-03** por [`adr-agent-flow-single-authored-level.md`](docs/adr/adr-agent-flow-single-authored-level.md): a gateway resolve, a ponta executa. Esta tarefa é o **modo de eco** no canal já eleito; a NIV-02 é a **eleição** do canal. Eixos diferentes, as duas necessárias | `aberto` | `docs/adr/adr-contextstore-allowlist.md` |
| ALW-05 | V5 (metade) — fechar aliases | `bloqueado` — o critério foi **CORRIGIDO em 2026-09-02** e o bloqueio deixou de ser vago. Não é o contador decair: ele mede TRÁFEGO, e as grafias voltam a cada execução do demo. São TRÊS dimensões — produtor (estática) × história durável × **idade do alias**. Medido: dos 119 aliases, **A=49 têm produtor · B=21 estão no durável (ficam por regra) · C=49 removíveis** — mas **29 dos 49 entraram em 2026-09-01**, redes do rename do core armadas junto com a migração. **Gatilho:** re-rodar `infra/test/aliases_v5_buckets.py` quando o balde C tiver ≥14 dias de `git log -S` na semente E o durável cobrir esse período; então `infra/scripts/remove_dead_aliases.py --antes DATA --aplicar`. ⚠️ Duas correções que a execução produziu e que quem retomar precisa saber: minha proxy de idade por *entrada no mapa* é fraca (o que importa é quando o PRODUTOR parou, e medir isso hoje sai contaminado pelos commits que moveram o mapa de casa); e o custo é assimétrico — manter alias morto ≈ 0, remover cedo > 0 | ADR §V5 |
| ALW-16 | **`masking.types[].formato` e read-only na tela, e a regex nem aparece.** A `MaskingPage` mostra `formato.display` em `<code>` e reduz `detect_pattern` a um booleano interno; nenhum dos dois e editavel. Medido: **um unico commit** tocou os dois na UI (`17bd7f11`, V2), e ja os introduziu read-only — nunca foram editaveis ali. A casa que ERA editavel (`masking.rule.{category}`, com `pattern` + `replacement`) fechou na V2b em 2026-08-29, medida em zero escritores e zero chaves vivas. Entao nada regrediu; o que falta e superficie para a dimensao `formato`, que a CNS-07 nomeou como uma das QUATRO do `DataType` e deixou de fora ao entregar as outras tres | `aberto` | `adr-contextstore-allowlist.md` |

---

## `docs/product/contextstore-core-namespace-spec.md` — namespace do CORE do ContextStore

Reformulação do dono (2026-09-01), feita sem a linguagem do ADR da allowlist. Convergiu em
quatro pontos e acrescentou três. **É pré-requisito prático do arco ALW-\***: a padronização mata **23 dos 69 aliases**
sem tocar em vocabulário de skill, porque quem os escreve é o core. **CNS-02 fechou em
2026-09-01** — a reserva é o root `core.*`; `session.`/`journey.`/`segment.` ficam livres.

| id | tarefa | estado | evidência |
|---|---|---|---|
| CNS-22 | **Tres pools ainda executam snapshot anterior a CNS-11, lendo alias cuja canonica e `core.*`** — `outbound_survey_worker` (`survey_grain`) · `survey_multi_ia` (`pool.id`) · `copilot_sac` (`sentimento.categoria`). Snapshots de 2026-08-10/12, e **sem trafego** nos testes de 2026-09-03/04. ⚠️ **Re-promover em bloco e perigoso**: cada leitura so passa a funcionar se o PRODUTOR daquela tag tambem escrever a canonica — medir produtor por produtor e a metade que faltou na CNS-11 e o que quebrou o OTP na CNS-19. Gatilho para quitar: o pool voltar a ser exercido. ✅ **Mecanismo entregue** (`infra/test/gate_published_alias_census.sh`): os tres sao a **divida declarada** dele, e quitar uma linha e re-promover **e apagar a linha** (ramo C). ✅ **`wrapup_detached_ia` quitado em 2026-09-04** — e o achado foi que ele **nao** funcionava: so a metade visivel funcionava, a que GRAVA a disposicao estava morta desde 2026-09-01 | `aberto` | `infra/test/gate_published_alias_census.sh` § DIVIDA |
| CNS-16 | **Vocabulário de ContextStore por tenant** — chave SEPARADA (`masking.context_map_tenant`) mesclada na leitura, para que cada chave mantenha a semântica uniforme de config e o tenant não alcance `core` **por construção do mesclador**, não por portão que alguém pode esquecer. Desenhada na CNS-08 e **adiada por população zero**: medido em 2026-09-01, zero tenants sobrescrevem o mapa e a instalação tem um tenant. ⚠️ Um banco por tenant **não** substitui isto — troca *override substitui* por *reseed sobrescreve*, que é o mesmo dilema de duas direções da D7; a questão é de PROPRIEDADE do dado, não de topologia | `adiado` — gatilho: segundo tenant que precise de vocabulário próprio | `CHANGELOG.md` § 2026-09-01 CNS-08 |
| CNS-14 | **Quem tem `config.masking` escreve o `__global__`** — mandando `tenant_id: null`, sem que nada no modelo distinga *operador da plataforma* de *administrador do tenant*. ⚠️ **MEDIDO em 2026-09-02, e a medição reordena a tarefa.** (a) A premissa do título está VELHA: desde a ALW-03 o mapa é gateado por `config.context_map`, não por `config.masking` — `_ns_field('masking','context_map')` resolve para o campo próprio, então esse grant já não alcança o `core.*`. (b) População medida: **1 portador** de `config.masking` e **1** de `config.context_map` (`admin@plughub.local`, papéis `{admin,developer}`), num tenant só (`tenant_demo`, 8 usuários). Uma cerca aqui hoje não separa ninguém de nada, e é a *política contra população zero* que este repositório já catalogou. (c) A metade que MACHUCAVA — a escrita global sair em silêncio — **foi fechada**: `PUT` global devolve `shadowed_by[]` nomeando os tenants que a ignoram, e o banner da tela declara o escopo (ver ALW-06 no `done.md`). **Gatilho para reabrir:** um segundo tenant, OU um portador de `config.masking`/`config.context_map` que não seja operador da plataforma. Aí a decisão deixa de ser uma cerca e passa a ser um conceito que falta no modelo (*escopo de administração*), que é trabalho de desenho, não de portão | `adiado` | `router.py` `_reject_tenant_context_map` (docstring) |
| CNS-13 | **Cauda dos docs da CNS-11 — 248 → 62 ocorrências em 32 arquivos.** Fechados: `CLAUDE.md` (+ a invariante do `core.*`, que não existia lá), `docs/guias/context-store.md` e `context-store-taxonomy.md`, mais 7 docs vivos de arco/módulo, e a spec emendada. O `contextstore-cadastro-censo.md` **não foi reescrito** — é medição DATADA; ganhou nota de datação apontando a tabela de-para. ⚠️ **O que resta é majoritariamente narrativa**: ADRs e design docs onde o nome aparece dentro do raciocínio de uma decisão de época (`adr-contextstore-allowlist.md` 8, `adr-wrapup-detached-pull.md` 7, `limite-credito-3-niveis-design.md` 8). Cada um exige julgamento — reescrever raciocínio passado corrompe a evidência, que é o mesmo motivo de `CHANGELOG.md`/`TODO.md` estarem fora | `aberto` | medido 2026-09-01 |
| CNS-23 | **O guard de escopo cobre UMA chave de um par com o mesmo modo de falha.** `masking.context_map` recusa override de tenant (`router.py:311`) porque a resolucao e tenant-vence-global POR INTEIRO — um override substitui as 94 folhas da plataforma. `masking.types` e a chave IRMA, tem exatamente o mesmo modo de falha e **nao tem o guard**. Medido ao cometer o erro em 2026-09-04: um `PUT` com `tenant_id` criou o override e deixou o tenant com catalogo congelado em 15 tipos, desligado do global. Desfeito no mesmo dia via `DELETE`. ⚠️ O `GET` NAO denuncia: o `tenant_id` da resposta ecoa o que foi PEDIDO, nao o escopo que respondeu — quem responde e `_provenance` (`effective_scope`/`tenant_present`) | `aberto` | `router.py:285-317` |

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
| VOZ-03 | **O `collect` de voz NUNCA completa — `_normalize_menu_result` é chamado e nunca definido.** `voice.py:716` o invoca quando todos os campos foram coletados; medido contra a IMAGEM construída: `hasattr(VoiceAdapter, "_normalize_menu_result")` é `False`, e o MRO é `VoiceAdapter → ChannelAdapter → ABC → object` — nenhum o tem. O `AttributeError` cai no `except Exception` largo do laço da WS de mídia (`voice.py:527`) e sai como `logger.debug("voice media WS receive loop ended")`: **falha silenciosa, em nível debug, reportada como fim normal do laço**. ⚠️ **E o teste passa porque MOCKA o método que não existe** (`test_voice_adapter.py:121` atribui um `MagicMock` à instância, `:727` asserta que foi chamado) — é o *teste que não pode reprovar* na forma mais pura: ele prova a CHAMADA e esconde a ausência. Exposição real; dano medido ~zero (1 sessão de voz em toda a instalação, canal em `_dev_mode`). Achado em 2026-09-03 ao confirmar se a redação do bridge alcança o `menu_result` vindo de voz — **alcança, e é channel-agnóstica** (chaveia em `menu:waiting:{sid}`, escrito pelo step, não pelo canal), mas o evento nunca chega lá | `aberto` | `voice.py:716` · `test_voice_adapter.py:121` |

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

## `docs/adr/adr-agent-flow-single-authored-level.md` — um nível autorado

*(⚠️ NÃO confundir com o modelo de escopo `segment`/`session`/`journey`, que segue vigente e
intocado. "Três níveis" nomeia dois modelos neste repositório — ver a desambiguação no topo da ADR.)*

| id | tarefa | estado | onde |
|---|---|---|---|
| NIV-09 | **O ramo MORTO da eleição de canal — decidir se sai ou se volta a viver.** `select_channel` (pura, testada, capability-aware) só é alcançada por `main.py::_dispatch_collect`, consumidor de `collect.requested`; o único produtor desse evento tem **zero chamadores** (AST) e as duas rotas de collect da workflow-api respondem **410** desde o Arc 19 Fase D. Com ele parado há duas eleições no repositório e **uma só decide** — situação estável, e por isso não é defeito hoje. O que NÃO pode acontecer é o produtor voltar sem que alguém escolha: aí voltam a ser duas decidindo, com semânticas diferentes (`requires[]` declarado × derivado), e a permissiva vence. O predicado (`channel_satisfies`) já é compartilhado — o que está em jogo é só a eleição. ⚠️ **Não confundir com o ramo `_dispatch_collect` → `adapter.handle_collect_event` dos adapters sms/email/whatsapp**, que morre junto | `adiado` | gatilho: `emit_collect_requested` ganhar chamador (ramo E de `infra/test/probe_collect_masked_requirement.sh`) |
| NIV-11 | **Migrar o roteiro dos 23 fluxos restantes** — o padrão está pronto e provado no piloto: uma forma por fluxo, **uma** carga (`invoke form_get` → `output_as: roteiro`) e cada `notify`/`menu` referenciando `render.by_node.<id do step>`. Medido em 2026-09-03: **99 pontos de roteiro cravado em 24 skills** (79 estáticos + 20 dinâmicos); o piloto tirou 12, restam **67 estáticos**. ⚠️ **Continua caso a caso, na próxima edição de cada fluxo — não é varredura**: cada migração custa `set-next`+`promote` do pool dono e não muda comportamento nenhum, então em massa é risco sem entrega. ⚠️ **A frase de degradação de cada fluxo FICA cravada** — é o caminho de quem falhou ao carregar o roteiro, e buscá-la na forma que acabou de falhar deixaria o fluxo MUDO. ⚠️ Rótulos de `options[]` **não** entram nesta linha: `by_node` mapeia texto de nó, e endereçar opção pede outro passo (medir antes) | `adiado` | gatilho: a próxima edição de cada skill · exemplar em `skill_limite_entrada_v1` |
| NIV-12 | **Os 20 pontos de roteiro DINÂMICO não migram — o `interpolate` é de passe ÚNICO.** Medido: ele coleta os `{{…}}` do template ORIGINAL, resolve e substitui; um valor inserido que contenha `{{…}}` é colocado **verbatim e nunca reinterpretado**, então texto vindo da forma com referência a `$.pipeline_state.*` chegaria ao cliente **com as chaves literais na tela**. É o caso do `menu_continuidade` (cartão, limite, status) e do `confirmar_recebimento`. Habilitar pede uma segunda passada, e ela tem **vetor próprio**: quem edita conteúdo passaria a poder injetar referências ao `pipeline_state` — hoje o conteúdo é texto e nada mais. Decidir o escopo (lista de refs permitidas? só `$.pipeline_state.*`?) antes de habilitar | `aberto` | `skill-flow-engine/src/interpolate.ts` |
| NIV-05 | **A capacidade `masked_input` está definida pelo MECANISMO, e isso torna o gatilho de voz inalcançável.** O enum diz `password-overlay masked field (webchat)` — lido ao pé da letra, voz nunca qualifica, ainda que o plano de mídia suba E o DTMF seja suprimido. Achado pelo dono ao perguntar se a NIV-01 mantinha voz elegível; era **erro meu** escrever um gatilho que a própria definição fecha. Capacidade tem de descrever a **GARANTIA** — *"o canal coleta entrada sem que o valor apareça em nenhuma superfície de leitura (transcript, histórico, gravação)"* — e não a implementação. **Redação fechada em 2026-09-03**, depois de duas correções do dono: *"o canal coleta entrada sem que o valor apareça em nenhuma superfície de leitura **controlada pela plataforma** — transcript, histórico durável, e gravação **quando a plataforma grava**"*. O recorte por CONTROLE é o que faz a definição sobreviver aos três modos: em SIP/WebRTC a plataforma grava e o dígito não está no áudio (RFC 4733/2833); em CTI a gravação é do PABX e simplesmente **não está no conjunto dela** — que é diferente de estar e ser segura. Assim webchat qualifica por overlay e voz por transporte out-of-band: mecanismos diferentes, mesma garantia. É o mesmo movimento do `EchoMode`: domínio abstrato, cada canal interpreta. ⚠️ **Muda quem pode reivindicar a capacidade** — é decisão de contrato, não ajuste de comentário. **Pré-requisito real** do gatilho de voz ⚠️ **Passo 3 do conjunto de reentrada da voz** (ver o quadro acima do grupo). | `aberto` | `skill.ts` `ChannelCapabilitySchema` |
| NIV-06 | **Construir o tratamento de eco em voz — é LACUNA, não vazamento.** Medido em 2026-09-03: `voice.py` tem **zero ocorrências de "masked"**. Não há eco a mascarar porque não há eco: o adapter não verbaliza o dígito, não bipa e não cala por política. O desenho vem pronto do dono e do `EchoMode` (ALW-10): `plain` verbaliza (*"um, dois, nove"*) · `masked` bipa · `none` cala — **o eco existe sempre para dar feedback de tecla**, e é por isso que `none` é modo próprio. ⚠️ Esta é a linha que substitui a NIV-06 original, cujo enunciado (*"supressão de DTMF na gravação"*) foi **refutado** — ver NIV-07. ⚠️ **Há causa a montante: a VOZ-03** — o `collect` de voz nunca completa (método chamado e nunca definido), então o caminho onde o eco viveria nunca foi exercido ponta a ponta ⚠️ **Passo 4 do conjunto de reentrada da voz.** | `bloqueado` | depende do plano de mídia (Arc 15) |
| NIV-07 | **Asserir que o DTMF foi negociado OUT-OF-BAND, e recusar o fallback in-band.** É o que sobrou do argumento da gravação depois da correção do dono: em WebRTC (RFC 4733 `telephone-event`) e SIP (RFC 2833 / SIP INFO) o dígito viaja fora do áudio, e o beep audível é tom uniforme gerado pela rede — sem informação de tecla. **Mas isso é NEGOCIADO no SDP**, não é propriedade: se a outra ponta não oferecer `telephone-event`, o endpoint pode cair para in-band. Garantia que depende de configuração correta, sem mecanismo que a imponha, é promessa — o padrão que este repositório cataloga. Verificação de SDP, não trabalho de mídia ⚠️ **Passo 5 do conjunto de reentrada da voz.** | `bloqueado` | depende do plano de mídia (Arc 15) |
| NIV-08 | **Recusar `masked` + `input_mode: voice` — combinação inválida.** O default é `dtmf`, mas `input_mode` vem do payload do menu (`voice.py:858`). Declarado `voice`, o cliente **fala** o valor e o STT o transcreve: o segredo entra pela porta de áudio por construção, e **nenhum RFC de transporte ajuda** — é vetor independente do DTMF. Pede RECUSA (no deploy, junto com a NIV-03, que já decide sobre pool × canal), não supressão ⚠️ **Passo 6 do conjunto de reentrada da voz — PRECEDE a declaração da capacidade, não é disparada por ela.** | `aberto` | `voice.py:858` |
| NIV-10 | **`masked_fallback` prevê `message` e `link`, e nenhum dos dois existe — a recusa é o único desfecho.** `MaskedFallbackPolicySchema` declara três modos; medido em 2026-09-03, **não há namespace `masking` no config-api** (`GET /v1/config/masking` → 404), logo não há política a consultar e o restritivo é o único honesto. A guarda de runtime **diz isso na mensagem de recusa** em vez de fingir que consultou. Não é defeito: é a diferença entre *decidimos recusar* e *não sabemos o que fazer*, e hoje é a primeira. Vira trabalho quando alguém quiser que o cliente receba um AVISO (ou um link one-time para o webchat) em vez de o fluxo cair no `on_failure` — aí precisa de store, de UI e de decidir se o modo é por canal ou por tenant. ⚠️ O ramo E de `probe_masked_channel_gate.sh` guarda o inverso: `masked_fallback` ganhar leitor sem a guarda consultá-lo REPROVA | `adiado` | gatilho: pedido de produto por aviso/link no lugar da recusa |

> **CONJUNTO DE REENTRADA DA VOZ — e a ORDEM importa.** Voz **suporta** coleta
> mascarada por natureza: em SIP/WebRTC o dígito viaja fora do áudio (RFC 4733 /
> 2833) e o eco é um bipe. O que hoje a impede não é a natureza, é o **estado da
> implementação**: medido em 2026-09-03, `voice.py` tem **18 menções a DTMF e ZERO
> a "masked"** — o adapter coleta dígito e não distingue campo sensível de campo
> comum. Por isso a NIV-03 **recusa** menu mascarado em voz: ela não removeu
> suporte, trocou vazamento silencioso por recusa alta.
>
> A declaração da capacidade (`voice` ganhar `masked_input` em
> `@plughub/schemas/src/channel-capabilities.ts` **+ o gêmeo Python**) é o **ÚLTIMO**
> passo, nunca o primeiro — e quando ele acontecer, nenhuma guarda muda: elas leem
> a tabela. No mesmo instante o deploy para de bloquear, o runtime para de recusar
> e o negociador passa a **eleger** voz para coleta mascarada.
>
> Ordem das pré-condições:
>
> 1. **VOZ-01** — provisionar o SFU. Sem canal de pé nada disto é testável.
> 2. **VOZ-03** — o `collect` de voz completar. Hoje o menu de voz não fecha nem
>    sem máscara (método chamado e nunca definido).
> 3. **NIV-05** — redefinir a capacidade pela **GARANTIA**. Enquanto o enum disser
>    `password-overlay … (webchat)`, voz não qualifica nem com tudo pronto.
> 4. **NIV-06** — construir o eco (`plain` verbaliza · `masked` bipa · `none` cala).
> 5. **NIV-07** — asserir a negociação out-of-band no SDP; recusar fallback in-band.
> 6. **NIV-08** — recusar `masked` + `input_mode: voice` (aí o cliente **fala** o
>    valor e o STT transcreve — vetor independente do DTMF, e nenhum RFC ajuda).
> 7. **só então** declarar `masked_input` para `voice`.
>
> ⚠️ A NIV-08 **precede** a declaração; adiá-la com gatilho *"voz ganhar
> `masked_input`"* construiria a recusa depois do buraco abrir. Erro de ordenação
> corrigido em 2026-09-03, antes de virar linha de ledger.


## `docs/guias/masked-input.md` — mascaramento de entrada por canal

| id | tarefa | estado | onde |
|---|---|---|---|
| MSK-02 | **O MESMO numero de cartao chega ao cliente mascarado num caminho e CRU no outro — e medido, nao suposto.** Telas do demo em 2026-09-04: `skill_limite_entrada_v1:356` imprime `***4444` (le `$.pipeline_state.pendencia.context.numero_cartao`, ja mascarado por quem monta o `context_preview`) e `skill_limite_retorno_v1:71` imprime `1111222233334444` (le `@ctx.session.numero_cartao`, cru). Varredura do Redis no mesmo dia: o valor CRU esta em **2 streams canonicos e 3 hashes de ctx** (sessao + journey). ⚠️ **A causa nao e o catalogo de formatos e nem a deteccao**: `credit_card` TEM `detect_pattern` e ele casa 16 digitos sem separador; as regras vivas `*.numero_cartao → last_4` existem mas sao `role: operator`, ou seja protegem quem LE o ctx, nao a interpolacao num texto para o cliente. A causa e que o campo **nao e declarado `masked`** — entao nada sistematico o protege, e um template mascara a mao enquanto o outro nao. E o argumento vivo da §D8 do catalogo de formatos e do `adr-masked-typed-declaration`. ⚠️ Fixture de demo com numero falso: **nao ha vazamento**, ha o SHAPE do defeito. Escopo a decidir: (a) declarar `masked: "credit_card"` no campo — muda fluxo de dado (vai para o escopo em memoria e some do `pipeline_state`, entao os dois templates precisam de outra fonte); (b) entender por que a deteccao nao mascarou a mensagem de saida, que e pergunta de OUTRO arco. **Gatilho:** antes de qualquer forma com cartao real | `aberto` | medido 2026-09-04, telas do demo + varredura de Redis |

## `docs/adr/adr-dialog-tree-options.md` — opcoes em ARVORE no DialogForm

*(A F0 — `flattenBlocks` lossless — fechou em 2026-09-04; ver `done.md`. As demais fases
seguem sem tarefa aberta neste ledger.)*

| id | tarefa | status | referencia |
|---|---|---|---|

## `docs/arcos/arc12-agent-business-events.md` — eventos de negocio do agente

*(O wrap-up real passou a emitir captura Arc 12 em 2026-09-04; ver `done.md`.)*

| id | tarefa | status | referencia |
|---|---|---|---|

## `docs/adr/adr-dialog-input-format-catalog.md` — catalogo de formatos de entrada

ADR **proposto** em 2026-09-04. Censo do mesmo dia: `pattern` tem **0 usuarios** (0 de 11 formas
publicadas, 0 de 11 semeadas, 0 YAMLs de skill); **2** formas declaram validacao e **1 delas e
inerte**; `fields[].validation` e descartado no `form_get`; Console e pagina web nao validam nada.
**F2 antes de F3** e a unica ordem que nao pode inverter — remover `pattern` antes de o engine
entender `format` abriria janela sem validacao nenhuma.

| id | tarefa | status | referencia |
|---|---|---|---|
| FMT-01 | **O censo encolheu de fase para RAMO DE GATE, e a razao e a nota de ambiente.** Ele nasceu como pre-requisito para autorizar a remocao do `pattern` ("provar o zero antes de apagar"), e essa funcao caiu junto com o argumento de adocao: num produto pre-producao toda contagem e perto de zero, entao o zero nao autoriza nada que a §1.2(c)(d) ja nao autorize. O que sobra e valor de REGRESSAO — *"declarou e nao aplica"* tem de continuar sendo pego conforme as formas se multiplicam —, e isso e a F5. Escopo restante: um ramo que conte formas com `validation` sem consumidor efetivo, dentro da suite de gates do arco | `aberto` | ADR §1 (nota de ambiente), §4 |
| FMT-09 | **O engine le o catalogo EMBUTIDO, nao o `dialog.formats` do config-api.** O `skill-flow-engine` nao tem cliente de config, entao `validateFormat` resolve `format` pelo `DEFAULT_DIALOG_FORMAT_CATALOG` de `@plughub/schemas`. Hoje os dois concordam por construcao (o store foi semeado do mesmo default e ninguem editou), e e por isso que a divergencia esta escrita no proprio arquivo em vez de descoberta depois: **um tenant que EDITE o catalogo vera o engine seguir o embutido, sem nada ficar vermelho** — e a fonte de verdade em runtime e o store. Duas saidas possiveis: (a) cliente de config no engine, com cache e invalidacao no `config.changed`, como os outros consumidores; (b) o `form_get` resolver o formato para dentro do render, tirando a necessidade de catalogo no engine para o caminho DialogForm — mas isso nao cobre skill que declare `format` literal no YAML. **Gatilho:** a primeira edicao real de `dialog.formats` por um tenant, ou a F4 (que traz um terceiro leitor e torna a pergunta *"quem le de onde"* inevitavel) | `aberto` | `CHANGELOG.md` § 2026-09-04 F2; `skill-flow-engine/src/steps/menu.ts` |
| FMT-10 | **Formato em fixture viva espera a afordancia chegar ao CANAL — medido, nao suposto.** `dialog_limite_solicitacao` (cartao, vencimento, valor, sem regra nenhuma e com contraparte pronta no catalogo) e consumida por um step `menu` de `skill_limite_entrada_v1`: e renderizada pelo **canal**, nao pelo Console. A F4 levou afordancia a duas superficies e o chat nao e uma delas, entao ligar `credit_card` ali recusaria quem digitasse `4539148803436467` sem que nada o guiasse. Depende da **FMT-11**. ⚠️ `numero_cartao` segue nao mascarado e viajando em claro para `pipeline_state` — fixture de demo, sem vazamento, mas e o shape do defeito e o melhor argumento da D8 | `bloqueado` — falta a FMT-11 | ADR §D7; `CHANGELOG.md` § 2026-09-04 F4 |
| FMT-11 | **A afordancia nao chega ao CANAL.** O adapter recebe `masked_types` (overlay de senha) mas nunca a mascara de digitacao, o `inputmode` nem o `maxlength` — entao no webchat, WhatsApp e voz o cliente digita sem guia e so descobre o formato pela RECUSA. A F4 fechou Console e pagina web; o chat e a terceira superficie de CLIENTE e ficou de fora. Forma provavel: o `notification_send` ja carrega o bloco `menu` — a afordancia resolvida cabe ali, e o adapter traduz para o que cada canal suporta (o invariante de rendering por canal ja manda isso). ⚠️ Voz nao tem teclado: a traducao la e outra coisa (ditar o formato no prompt), e tratar as duas como a mesma seria o erro que a §D7 nomeia. **Gatilho:** e pre-requisito declarado da FMT-10 | `aberto` | ADR §D7; `CHANGELOG.md` § 2026-09-04 F4 |
| FMT-06 | **F5 — os cinco gates, cada um com controle positivo.** O que carrega o arco e `probe_dialog_format_surfaces.sh`: as tres superfícies tem de dar o **mesmo** veredicto para a mesma entrada, e ele e a unica coisa que impede a D2 de virar o `askWhen` de novo com o tempo. Os outros quatro: catalogo resolve (`from_masked_type` inexistente ⇒ reprova), veredicto semantico (`31/02/2026` e `000.000.000-00` recusados, **com data valida aceita ao lado** — senao passa por recusar tudo), validacao vale sem `retry`, e `fields[].validation` sobrevive ao round-trip | `aberto` | ADR §5 |
| FMT-07 | **`masking.types` declara `display: "R$ #.##0,00"` para `address`, `health` e `financial`.** Endereco nao se exibe como moeda — e copy-paste, encontrado pelo censo da FMT-01 e deixado de fora do ADR de proposito: mexe em politica de mascaramento, que tem dono proprio. Efeito hoje e cosmetico (os tres nao tem `detect_pattern`, entao nao ha caminho de deteccao que use a mascara), mas a FMT-02 passa a **ler** esse bloco via `from_masked_type`, e um valor errado ali deixa de ser cosmetico | `aberto` | ADR §D3 (nota), `config-api/seed.py:554` |
| FMT-13 | **O catalogo de formatos nao tem tela — divida criada pelas F1-F4 deste arco.** `dialog.formats` define mascara de digitacao, `inputmode`, `maxlength`, placeholder e veredicto em dois niveis, e a `DialogFormsPage` apenas **escolhe** uma entrada (`<option>`); nao existe rota para AUTORAR o catalogo. Contraria o invariante *"Every config field is UI-editable — campo que so existe em YAML/arquivo e divida a fechar"*. Hoje so por API. ⚠️ Quem autora formato e quem autora TIPO sao pessoas diferentes (a ALW-03 ja separou mapa de catalogo pelo mesmo criterio), entao a tela nova nao deve nascer dentro de `/config/masking` sem repetir aquela discussao | `aberto` | ADR do catalogo de formatos |

## `docs/adr/adr-context-read-audience-policy.md` — leitura de contexto por plateia

ADR **proposto** em 2026-09-04, disparado por um teste do dono: o mesmo cartao apareceu `***4444`
numa tela e cru na outra. Censo do mesmo dia: **167** interpolacoes `@ctx.*`, **225**
`$.pipeline_state.*`, **214** tags tipadas no mapa, e **20** pontos resolvendo para tipo com
`echo_to_customer: none` — dos quais **10 sao legitimos** (token em link) e 4 estao num skill sem
`notify`/`menu`. **A EXCECAO (F2) vem antes da APLICACAO (F3)** — inverter quebra survey link e OTP.

| id | tarefa | status | referencia |
|---|---|---|---|
| CTX-08 | **DUAS casas ligam valor -> tipo, e elas ja discordaram.** `masking.context_map` (que a F3 vai usar) e a spec `preview` do `delegate`, declarada por CALL SITE (`skill_limite_processo_v1.yaml:114`). Sobre `limite_solicitado` uma dizia `financial` e a outra `valor_declarado_pelo_cliente` — o mesmo valor mascarado num caminho e aberto no outro, e so nao aparecia porque a F3 nao existe. Alinhadas a mao em 2026-09-04; **nada impede a proxima divergencia**. O desenho provavel e a spec do preview citar a TAG e deixar o mapa responder pelo tipo — hoje ela renomeia o campo e retipa de novo | `aberto` | ADR §6 |
| CTX-09 | **O `invoke` sai CRU e nao e gateado — a §D2 afirmava que era.** Ela dizia que o valor inteiro num argumento de tool era aceitavel porque *"o gate e o que ja existe (`AuditPolicy.data_categories`)"*. Medido no repositorio inteiro: o campo existe no schema (`audit.ts:35`), **zero** tools o declaram, **zero** ocorrencias em `mcp-server-plughub/src`, e quem o le e `sdk/src/mcp-interceptor.ts` — que o proprio `CLAUDE.md` mede como **nunca instanciado**. A decisao nao muda (mascarar argumento de tool quebraria o produto); o que muda e parar de citar um gate inexistente como razao. O buraco e da borda MCP e tem ADR proprio | `aberto` | ADR §D10 |
| CTX-10 | **O carimbo de proveniencia deixou de BLOQUEAR e virou divida de PRECISAO.** A F5 foi entregue pela REDE (§D12), que e idempotente e por isso nao corrompe valor ja mascarado — era esse o risco que exigia o carimbo. O que o carimbo ainda daria: **tipo declarado** onde a rede so tem FORMA (ela pega 4 de 15 tipos). A decisao embutida continua a mesma e continua sendo do dono: valor DERIVADO (concatenacao, resumo de LLM) herda o tipo de qual. ⚠️ O recorte recomendado e carimbar so o INEQUIVOCO (resposta a campo declarado de DialogForm) e deixar o resto sem carimbo — carimbo errado e invisivel, ausencia e contavel | `aberto` | ADR §D11, §D12 |

## `docs/product/dialog-primitive-and-runner-design.md` — primitivo de dialogo (survey + OTP)

Fatias 1 e 2 entregues; a autoria de `fields[]` fechou em 2026-09-04 pelo editor JSON e foi
REDESENHADA em 2026-09-05 — `form` e TIPO DE BLOCO, com widget por campo (ver `done.md`).

| id | tarefa | estado | evidência |
|---|---|---|---|
| DLG-05 | **O ramo `form` existe só no runner GENÉRICO** — `skill_survey_runner_v1` e `agente_nps_v1` também chamam `form_get` e continuam passando `interaction/options` sem `fields`, então uma forma `interaction: form` entregue a eles renderiza vazia. Não entrou de carona por disciplina de escopo: o ramo custa um `choice` + um `menu` por skill, e multiplicar a mudança por três num dia só amplia a superfície sem necessidade medida — as formas de survey são escalares por natureza (as 12 semeadas confirmam: nenhuma forma de survey usa `form`). ⚠️ Consequência a herdar quando fechar: no ramo `form` o `payload.value` vira **MAPA**, e o chamador do survey lê escalar hoje. **Gatilho:** a primeira forma `form` endereçada a um desses dois | `adiado` | `CHANGELOG.md` § 2026-09-04 (8) |
| DLG-06 | **O preview mostra o `render`, não como o Console desenha.** O fiel exigiria extrair o caminhador de nós do `DialogFormRenderer` para um componente apresentacional montado pelos dois — e ele é componente VIVO que serve aprovação e wrap-up, num pacote **sem infraestrutura de teste nenhuma** (AUT-25). Recusado nesta fatia por isso, não por custo de UI. ⚠️ Ao fazer, o rótulo continua obrigatório: seria *"como o Console renderiza"*, nunca *"como o cliente vê"* — o webchat e a página web desenham diferente, e o `value` pré-preenchido, por exemplo, **só o Console honra**. **Gatilho:** o pacote ganhar suíte (decisão da AUT-25) | `bloqueado` — depende da decisão da AUT-25 | `CHANGELOG.md` § 2026-09-04 (8) |
| DLG-08 | **As opcoes POR CAMPO (`field.options`, para `type: select`) nao ganharam widget** — o unico nivel mais profundo que o editor de bloco form nao desce. Medido antes de decidir: entre os 10 campos publicados, **zero** tem `options` (tipos `text` 9 · `bool` 1), zero tem `capture` e um tem `validation`. Widget para populacao zero e trabalho contra ninguem; e o que ja existe **sobrevive ao round-trip** e a tela o **anuncia** (campo `select` sem opcoes aparece dito, nunca silenciado) — o autor que precise disso hoje usa o editor JSON. **Gatilho:** a primeira forma publicada com campo `select` de `options` nao vazio | `adiado` | `CHANGELOG.md` § 2026-09-05 |
| DLG-10 | **Resposta de no PULADO: a pagina web apaga, o Console guarda — e o comentario do Console promete o que ele nao faz.** Achado ao escrever o probe de paridade (DLG-09): o VEREDICTO das tres coincide, a CONSEQUENCIA nao. `survey_web.py` faz `delete answers[nodeOk[i]]` quando a guarda vira falsa (⇒ NA no submit); o `DialogFormRenderer` apenas deixa de renderizar — nenhum dos tres `setAnswers` poda, e o submit manda o objeto `answers` INTEIRO. O comentario dele diz *"clearing any answer it left (→ NA on submit)"*: promessa sem mecanismo, a familia do DDL de `participation_intervals`. Consequencia quando morder: NA re-normaliza peso na composicao, valor obsoleto e CONTADO — mesma forma, duas notas, por superficie. ⚠️ **Armadilha ARMADA, nao incendio**: 1 guarda no store hoje (`dialog_survey_multi_v1`, `lt`), numa forma de survey que o renderer do Console nao serve. O arco da arvore e quem cria a populacao. **Gatilho:** a F1 do `adr-dialog-tree-options`, ou a primeira guarda numa forma servida pelo Console | `aberto` | medido 2026-09-05, `DialogFormRenderer.tsx` × `survey_web.py` |
| DLG-15 | **A recusa alta em canal pobre (D11) nao tem casa ainda.** O `render` ja DECLARA `options_tree` (derivado da estrutura), e o Console desenha; falta quem RECUSE onde nao sabe desenhar. ⚠️ O lugar obvio nao serve: o `form_get` **nao conhece o canal**, e dar-lhe um parametro cruzaria a costura conteudo x canal. Quem conhece e o gateway — logo a recusa e do adapter, onde a renderizacao por canal ja mora por invariante. Antes de escrever politica, MEDIR: existe mecanismo de `ChannelCapability` para tipo de interacao, ou ele nasce aqui? Quais canais sabem desenhar arvore (web sim; WhatsApp `list` nao)? Achatar `Financeiro > Cobranca indevida` em 40 botoes e emulacao muda — a tela parece certa e a hierarquia que a serie do Arc 12 conta se perde. ⚠️ **DEIXOU DE SER PROSPECTIVO: reproduzido AO VIVO em 2026-09-05** (medicao da F6). O Console servido estava num build anterior a F3; o `form_get` entregou a subarvore com `options_tree: true` e a tela **ignorou** — desenhou so o nivel de cima e deixou submeter. O contato real gravou `retencao_humano.wrapup.motivo.tecnico` e `...servico.cadastro`, **duas PASTAS**, que e exatamente a emulacao muda descrita acima, agora com dado no ClickHouse. O agravante e a forma: o degrade nao veio de canal pobre, veio de **superficie DESATUALIZADA** — quem nao sabe desenhar arvore nem sempre se declara, entao a recusa nao pode depender de o consumidor se identificar. ⚠️ **Generalizacao de 2026-09-05 (ao desenhar o relatorio):** canal pobre e build velho produzem **a MESMA linha** no ClickHouse — categoria que para numa pasta. Logo a deteccao nao precisa saber POR QUE a superficie falhou: `own > 0` numa pasta e detector generico de *superficie que nao desenha a arvore*, e ele mora no relatorio, nao na borda. Isso muda o que o gate desta tarefa deve vigiar — nao "o canal X recusou?", e sim "apareceu linha parada em pasta?". | `aberto` | medido 2026-09-05, `form_get` sem parametro de canal; reproduzido ao vivo no mesmo dia |
| DLG-16 | **A conferencia do prefixo comum (D5) no submit nao existe — e hoje nao precisa.** A multi-selecao e DENTRO de uma pasta, e no Console isso vale **por construcao**: navegar limpa as marcacoes, entao nao ha como montar cesta cross-ramo clicando. O `segment_outcome_record` recebe os paths e nao confere prefixo comum; um chamador que nao seja a tela poderia gravar duas series que nao deviam coexistir. Hoje ha UM chamador (o renderer do Console) e ele nao consegue produzir o estado invalido. **Gatilho:** um segundo chamador da tool, ou uma superficie de arvore que permita marcar navegando | `adiado` | medido 2026-09-05, `deriveAgentEvents` sem guarda de prefixo |
| DLG-18 | **O botao "+ pergunta" na linha da opcao — a pergunta IRMA ja guardada por `prefix`.** E a sintese aprovada no mockup: o autor ve o aninhamento (pergunta indentada sob a opcao que a dispara) e o JSON continua PLANO, porque o que se cria e uma irma com `ask_when {op:"prefix", value:<caminho>}` preenchido. Ninguem digita a guarda. ⚠️ **Nota de desenho que a implementacao da F5 revelou:** o mockup editava prompt/`output_key` INLINE, e isso seria um SEGUNDO editor para o mesmo no — o defeito de duas casas, em widget. A vista deve mostrar um CHIP e levar ao editor existente, nunca duplica-lo. Pre-requisito ja atendido: `ask_when` e autoravel por pergunta, e o op `prefix` esta no seletor desde a F5. **Gatilho:** decisao do dono — e afordancia, nao correcao | `aberto` | mockup aprovado 2026-09-05; `AskWhenRow` por pergunta ja existe |
| DLG-22 | **Falta UM contato marcando duas folhas na mesma pasta.** A F6 mediu tudo o resto ao vivo (caminho de 3 niveis ate a folha, `checklist` como lista, guarda `prefix` discriminando, classificacao virando outcome, contagem por prefixo conferindo). O que sobrou: o operador marcou **um** servico, e lista de um elemento **nao distingue** *o emissor itera a lista* de *o emissor pega o primeiro* — que e a proposicao que a F2 existe para sustentar. O `probe_multi_answer_events.sh` cobre 2 folhas na cadeia **engine → tool**; falta a ponta de cima, a TELA emitindo um array de 2 a partir das caixas dentro de uma pasta. **Gatilho:** proximo wrap-up de retencao — marcar 2+ servicos dentro de `cadastro` ou de `plano` | `aberto` | medido 2026-09-05, contato `4919af4a` |
| DLG-23 | **O `category` do Arc 12 nao tem SERIE por caminho na tela, so a tabela.** A vista de Eventos do Monitor foi consertada (ver `CHANGELOG` 2026-09-05 (12)) e ja responde a pergunta "quantos por caminho", mas e uma tabela de 24 h: nao ha como ver `motivo.financeiro.*` **ao longo do tempo** sem montar um cartao de dashboard a mao. O `/reports/agent-events/series` existe e aceita o mesmo prefixo; falta a superficie. ⚠️ Antes de desenhar, decidir de quem e a casa — a lente de contato (`adr-relatorios-duas-superficies-e-lentes`) ja tem forma `metric_lines`, e uma tela nova de eventos seria a terceira superficie a desenhar serie. **Gatilho:** primeira pergunta de tendencia sobre taxonomia de wrap-up ("cobranca indevida esta subindo?") | `aberto` | medido 2026-09-05, `/reports/agent-events/series` sem consumidor de UI |
| DLG-24 | **O evento nao carrega a FORMA, e a serie ja esta misturada — medido num pool so, no mesmo dia.** `servico.troca_titularidade` (prof 1, forma `arc12`, n=2) convive com `servico.cadastro.troca_titularidade` (prof 2, forma `arvore`, n=1): o MESMO servico do mundo real em dois caminhos, porque repontar `PoolHookEntry.context.dialog_form_id` troca o vocabulario **sob a mesma serie**. ⚠️ **Filtro nenhum conserta**: forcar um pool falha (a troca e temporal, nao espacial); resolver forma→pools na consulta responde o AGORA e reescreve o passado; encurtar periodo so estreita a classe de falha. E a regra da identidade derivada — o discriminador tem de ser do FENOMENO (*resposta a ESTE vocabulario*), nunca do conteiner (pool). **Fecha assim:** `dialog_form_id` + versao no evento, carimbados na emissao — o `deriveAgentEvents` **ja le o formulario**, o dado esta na mao; forward-only, com corte declarado em data como a `SEGMENT_SLA_EPOCH`. **Antes disso**, sem coluna nova: a colisao tem assinatura NOS DADOS (mesmo id em profundidades diferentes sob o mesmo pai; id que e folha numa linha e pasta noutra) ⇒ a lente recusa total e percentual e nomeia o conflito, que e o `comparability: 'same_form'` do contrato de lentes fazendo o seu trabalho. ⚠️ **NAO proibir o "tudo"**: nao resolve, e quebra o caso que a taxonomia controlada existe para servir — N pools sob o MESMO vocabulario sao comparaveis de proposito ⚠️ **Virou pre-requisito UNICO da D13** (epoca por forma, decidida no mesmo dia): sem o carimbo nao ha como recortar, e inferir a forma pela SILHUETA da arvore e heuristica que quebra quando duas formas compartilham prefixo. | `aberto` | medido 2026-09-05, `agent_business_events` sem coluna de forma |
| DLG-25 | **D13 — troca de forma vira EPOCA: um bloco por RUN contiguo, recortando o periodo.** Decisao do dono; a manipulacao e a exibicao ficam identicas e muda so a janela, entao custa **zero** conceito novo — e o mesmo idioma do `mode=epoch` da lente `deploy`, com outro discriminador. Tres regras que a medicao fixou: **(a)** a epoca sai dos EVENTOS, nunca da config — o registry tem `skill_deployments` mas **nada equivalente para hooks** (a tabela `pools` e atualizada no lugar, o `dialog_form_id` anterior deixou de existir no `PUT`), e derivar do evento e melhor porque troca sem trafego nao produz epoca; **(b)** o bloco e por RUN, nao por forma — rollback do hook faria *um bloco por forma* fundir duas fases e apagar a do meio, mesma distincao do `queue_wait_segment_id`; **(c)** a fronteira e FAIXA, nao linha — com `acw_timeout_hours: 24` um wrap-up reivindicado antes e submetido depois carrega a forma nova, entao duas epocas se sobrepoem por ate um dia, e isso e dado. ⚠️ Residuo: `form_id` nao pega edicao in-place (D6: acrescentar folha nao muda o id) — escolha e partir por `form_id` e MARCAR as versoes dentro do bloco, em vez de inventar um criterio *"e adicao pura?"* que envelhece. **Bloqueado por:** `DLG-24` | `bloqueado` | decidido 2026-09-05; depende do carimbo da forma no evento |

---

## `sem-demanda` — trabalho sem decisão por trás

**Contador: 3.** Balde declarado, não omissão. Se crescer, é sinal de que está entrando trabalho
sem ADR nem spec — informação de gestão, não detalhe de formato. Segue o precedente do balde
`unknown` do rollup de capacidade: publicado como tipo próprio, contado, nunca dobrado no vizinho.

| id | tarefa | status | âncora |
|---|---|---|---|
| ROT-01 | **Link direto e F5 em ROTA `/config/*` NOVA devolvem 422 — e a lista que decide isso ENVELHECE.** O nginx do `platform-ui` faz `proxy_pass` de `/config/*` para o config-api, que responde `{"loc":["query","tenant_id"]}`. ⚠️ **Corpo corrigido em 2026-09-04 por medição:** o título anterior dizia *qualquer* rota e citava `/config/masking` como quebrada — **hoje ela devolve 200 `text/html`**. Existe uma ALLOWLIST no `location` (`access|billing|platform|masking|recursos|resources|channels|canais|groups|calendars|schedules|outbound`) que cai no `index.html` quando o `Accept` é `text/html`. Medido com controle positivo: **3 de 15** rotas quebram — `agent-reports`, `context-map`, `dialog-forms` —, e são exatamente as que nasceram DEPOIS da lista. É esse o defeito real, e ele é pior que o descrito: **página nova entra quebrada em silêncio**, e só quem der F5 descobre. A decisão continua sendo dos dois lados (prefixo da UI × `location`), mas ganhou uma terceira opção que remove a classe em vez de remendá-la: **inverter para denylist** — o SPA atende `/config/*` e só os caminhos que o config-api realmente serve saem pela API. Achado ao verificar o editor JSON de DialogForm (DLG-01): um autor que favorite a própria tela recebe JSON de erro | `aberto` | medido 2026-09-04, `packages/platform-ui/Dockerfile` (nginx inline) |
| GAT-02 | **110 dos 281 scripts de `infra/test/` seguem NÃO TRIADOS — e a lista é nomeada, linha a linha, no `gates.manifest`.** É o que sobrou da triagem da GAT-01 (2026-09-04), e está separada dos ISENTOS de propósito: isenção é decisão, dívida é pergunta em aberto, e juntá-las faria a dívida herdar a tranquilidade da decisão. Composição medida: **74 nunca rodados** (os 35 `test_*`, que julgam com vocabulário próprio `✅`/`❌` e são de 2026-06, mais os `smoke_*` fora do critério) · **18 INCONCLUSIVO** · **8 VERMELHO** (ver GAT-03) · **6 VERDES cuja natureza eu não decidi** — invariante ou medição de um momento? — · **4 TIMEOUT/RC**. O trabalho é por LOTES, e cada lote só pode encolher a lista: o ramo B do `probe_gates_manifest_coverage` reprova script novo sem classe, então a dívida não cresce em silêncio. | `aberto` | `infra/test/gates.manifest` § NÃO TRIADO |
| GAT-03 | **13 gates saem não-verdes hoje, e ninguém sabe, caso a caso, se o defeito é do produto ou do gate.** Duas populações, medidas em 2026-09-04 com a stack de pé. **(a) Os 5 que JÁ estavam no manifesto** e portanto o `run_gates.sh` sempre reprovou — `gate_supervisor_tenant_guard` · `probe_nav_backend_field_agreement` · `probe_role_preset_on_create` · `gate_sla_segment_target` (vermelhos) e `gate_queue_report_per_wait` (inconclusivo), mais o ramo F de `probe_context_map_seed`, cujo cenário não monta. Estes são os mais urgentes: **enquanto houver vermelho não triado no conjunto declarado, o runner ensina a ignorar o vermelho.** **(b) Os 8 do conjunto NÃO TRIADO** — `probe_report_row_scope` · `probe_seed_drift_named` · `probe_resume_outlives_meta` · `probe_open_segments_closed_sessions` · `probe_llm_call_paths` · `probe_contacts_count_internal_pools` · `smoke_formfill_renderer` · `probe_mcp_permissions_producer` (este já corrigido: saía 1 por falta de `node`, hoje sai 2). **A distinção não é acadêmica** — a triagem de 2026-09-04 produziu um caso de cada: `probe_masked_type_provenance` e `probe_ctx_writer_census` reprovavam por contar MENÇÃO em comentário como uso (defeito do gate, os dois corrigidos), enquanto `probe_context_map_seed` acusava um buraco real de provisionamento. | `aberto` | medido 2026-09-04, `CHANGELOG.md` § 2026-09-04 (9) |
