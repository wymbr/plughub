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
| ALW-19 | **`echo_to_customer` tambem nao tem leitor de runtime — mas por outro motivo, e por isso NAO saiu com o irmao.** Medido em 2026-09-12 ao fechar a ALW-17: o campo e declarado no schema, semeado no catalogo e validado na migracao, e nenhum codigo o LE para decidir coisa alguma. A diferenca que justifica mante-lo: ele tem consumidor NOMEADO — a perna de voz, onde `plain` verbaliza o digito, `masked` bipa e `none` cala (`channel_capability_registry.py`). Inerte por falta de CANAL nao e o mesmo que inerte por colapso dos proprios modos, que foi o caso do `echo_to_operator`. ⚠️ **O risco e o prazo:** quanto mais tempo um campo editavel na tela fica sem leitor, mais ele parece vivo — e o tenant que o configurar hoje nao recebe nada. Fechar e uma de duas: o adapter de voz passa a le-lo (VOZ-05, bot leg), ou ele sai da TELA ate existir quem o consuma, ficando so no schema | `adiado` — gatilho: a perna de voz ganhar bot leg (VOZ-05), ou o primeiro tenant que editar `echo_to_customer` esperando efeito | `CHANGELOG.md` § 2026-09-12 (8) |
| ALW-18 | **A lista que serve a tela de HISTORICO do Console esta incompleta em sessoes so de IA.** `session:{sid}:messages` (Redis, servida por `GET /api/conversation_history` no mcp-server) e escrita por dois sitios do channel-gateway: `_handle_menu_submit` (cliente) e `OutboundConsumer._dispatch` (agente). Medido em 2026-09-10: contatos de `auth_form_ia` **nao criam a chave**, e um contato de `limite_ia` que recebeu duas mensagens do agente ficou com **uma** entrada — a que chegou pelo consumidor de outbound; as que chegam pelo laco de STREAM (XREAD) nao sao apendadas. ⚠️ **E anterior a MSK-03** (medido numa sessao criada antes do rebuild), entao nao e regressao dela. ⚠️ **O modo de falha e o do catalogo:** a tela nao fica vazia nem da erro — mostra MENOS conversa, e menos parece plausivel. Antes de consertar, medir a populacao: quantas sessoes vivas tem a chave e com quantas mensagens contra o stream canonico. **Gatilho:** a proxima vez que alguem usar a aba de historico do Console para reconstituir um atendimento | `aberto` | `CHANGELOG.md` § 2026-09-10 (4) |

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
| CNS-25 | **A Console ainda precisa do TOKEN de retomada no browser para submeter um formulário.** A CNS-24 (2026-09-11) devolveu ao operador o que ele via antes da CNS-11 — `dialog_form_id` e os tokens — por decisão do dono, sustentada pelo A5 (o ingress confere a POSSE no árbitro e recusa não-detentor) e pelo portão por pool (AUT-47). ⚠️ **A exceção medida é o árbitro fora do ar:** ali o A5 libera o submit, com log, e o token na mão de um operador do mesmo pool vira suficiente. **A saída mais forte:** detectar a tarefa de formulário pelo `dialog_form_id` e RETOMAR do lado do servidor, pelo `resume_token` que a `work_task` de quem reivindicou já guarda (`assigned_to` + `resume_token`) — o token deixa de sair do servidor, e `PLATFORM_CONSOLE_TAGS` encolhe para uma tag só. Muda o contrato do `DialogFormRenderer` e do `ApprovalPanel` (hoje ambos postam em `/v1/channels/webhook/resume/{token}`). **Gatilho:** o próximo trabalho no ingress de resume, ou a primeira vez que o árbitro ficar fora do ar com tarefa de formulário pendente | `aberto` | `CHANGELOG.md` § 2026-09-11 (1) |

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
| VOZ-01 | **Provisionar o SFU — é ambiente de teste, não telecom.** Não há LiveKit em compose, env `LIVEKIT_*`, manifesto k8s, nem o SDK no `pyproject`; o provider roda em `_dev_mode`, que devolve token, sala e egress **placebo**. ⚠️ **Reposicionada em 2026-09-12:** com as fases reordenadas (WebRTC primeiro), ela deixa de ser a porta de um arco de telecom e passa a ser **o ambiente que destrava testar voz e vídeo sem operadora nenhuma** — SFU em compose, mais TURN (coturn) para atravessar redes. Inclui a recusa alta do V6: sem credencial o provider recusa, em vez de fingir | `aberto` | `docs/adr/adr-voice-media-plane.md` V-F0 |
| VOZ-02 | **Perna SIP — a última milha.** Tronco (SBC do cliente ou ITSP) → sala, por **conversor pronto** (Asterisk, FreeSWITCH, Kamailio+rtpengine, ou o serviço SIP do próprio SFU); `REFER` para ramal quando houver central. ⚠️ **Deixou de ser `bloqueado` e passou a `adiado` em 2026-09-12**: não é impedimento técnico, é ORDEM — tudo acima dela é exercitável por WebRTC. Decisões de projeto que vêm junto: G.711 nas pernas com SIP (relay, sem transcodificar), DTMF por RFC 4733, TLS/SRTP no enlace, `REFER` de saída | `adiado` — **gatilho:** o primeiro cliente com PABX/SBC, ou a decisão comercial de oferecer voz pública | ADR §5 V-F5 · §8 |
| VOZ-04 | **Contato de voz/vídeo ponta a ponta por WebRTC** (V-F1): sala, perna do agente no browser, admissão, roteamento, fila e alocação. Fecha o A4 do ADR — `add_participant` existe com docstring *"Add a leg (human agent SIP/WebRTC) to the conference"* e **nunca é chamada**, ou seja, hoje não há caminho de código que ponha um humano numa conferência de voz. É a primeira vez que o canal publica em `conversations.inbound`. **Sem telecom** | `aberto` | ADR §5 V-F1 |
| VOZ-05 | **Bot leg: STT/TTS** (V-F2) — URA e agente de IA por voz, `notify` falado, `menu` por voz com DTMF **e** STT. É o que conserta o `collect` morto do A2 e o que dá à voz transcrição — e, com ela, histórico, contexto e avaliação. Depois da VOZ-04, e ainda sem telecom | `aberto` | ADR §5 V-F2 |
| VOZ-06 | **Gravacao por segmento, com aviso e opt-out** (V-F3), no AttachmentStore com classe de retencao. ⚠️ **A premissa da destrava de 2026-09-12 foi MEDIDA em 2026-09-13 e caiu em tres pontos.** (1) *"o numero vem do env"* — falso: `resolve_attachment_expiry_days` le `webchat.attachment_expiry_days` da config-api, com tela (`WebChatConfigPage`); o env e so o fallback. (2) *"a gravacao ja escreve no store"* — falso: `voice.py` chamava `self._store.store()`, metodo que o `AttachmentStore` nunca teve, e **nenhuma gravacao de voz jamais foi armazenada**; `whatsapp.py` e `email.py` tinham o mesmo defeito de outra forma (`commit` sem `tenant_id`). Os tres foram consertados na VOZ-08. (3) *"o ciclo unico a apagaria aos 30"* — falso: **nao existia ciclo nenhum** (VOZ-07, implementado em 2026-09-13). Populacao do store: 6 linhas, todas `image/jpeg`. **O que sobra desta ficha e o MECANISMO por classe** (`call_recording` × `webchat_attachment` × …), e ele so tem objeto quando houver gravacao REAL: politica por classe contra populacao zero e o erro que a decisao #4 firmou. O default de 30 dias do dono continua valendo e ja e o que a chave unica carimba | `bloqueado` — ate existir gravacao REAL no store: o primeiro caminho capaz e o egress WebRTC (VOZ-01 + VOZ-04) | ADR §8 · A5 · `CHANGELOG.md` § 2026-09-13 (1) |
| VOZ-03 | **Metade entregue em 2026-09-07; falta o ciclo de vida de SESSÃO.** O enunciado anterior desta linha (*"o `collect` de voz nunca completa — `_normalize_menu_result` é chamado e nunca definido"*) media o sintoma que alguém tinha olhado: medido contra a IMAGEM construída, eram **seis** métodos ausentes, e o defeito era *o canal de voz nunca publicou nada* — transcrição de STT (`:574`), evento de gravação (`:440`) e resultado de coleta (`:732`) morriam no mesmo `AttributeError`, dentro de um `except` largo que o reportava como fim NORMAL do laço, em `debug`. **Entregues:** `_publish_inbound` · `_normalize_text` · `_normalize_menu_result`, mais o `except` barulhento e o teste que mockava os inexistentes. **Restam três, e não é "definir três métodos":** `_open_session` (`voice.py:243`) · `_route_inbound` (`:254`) · `_close_session` (`:323`) — é decidir como uma chamada PSTN abre sessão na plataforma, roteia a um pool e fecha com a taxonomia de `contact_closed`, e a semântica depende do plano de mídia. Fabricá-los agora escolheria isso no lugar errado. A dívida é CONTADA pelo gate a cada rodada, e some do placar quando os três existirem | `bloqueado` por `VOZ-02` — **corrigido em 2026-09-12**: a semântica de sessão PSTN é da perna SIP, não do SFU. Com WebRTC primeiro, a sessão que o V-F1 abre é a da sala; o ciclo de vida que falta aqui é o da chamada de operadora, e ele chega com a VOZ-02 | `voice.py:243`/`:254`/`:323` · `infra/test/probe_adapter_self_calls.sh` (tabela `DIVIDA`) · `CHANGELOG.md` 2026-09-07 (10) |

---

## `docs/product/identity-resolver-fase-a-plano.md` — identidade e comércio conversacional

| id | tarefa | estado | evidência |
|---|---|---|---|
| IDN-01 | Fase C — `external_refs` + merge de clientes | `aberto` | `CLAUDE.md` § Business in Any Media |
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
| APR-09 | **O ingress de resume aplica `approvals.decide` a QUALQUER resume com JWT** — follow-up medido do R1; parametrizar por tipo de tarefa. Um portão que decide sobre a espécie errada de trabalho autoriza a certa pelo motivo errado | `aberto` | `CLAUDE.md` § Pending, movido em 2026-09-05 |
| APR-07 | Promote real: `invoke` de deploy no `efetuar_promocao`, hoje `complete` | `adiado` — não-objetivo v1. Gatilho: promoção agendada precisar valer em produção | idem |

---

## `docs/adr/adr-work-item-requeue-and-agent-affinity.md` — pull direcionado e wrap-up

Arco A–F completo e **verificado por gate** desde 2026-09-09 (PUL-02). A **lacuna 2 foi medida**
no mesmo dia (PUL-01) e segue aberta por decisão, com gatilho declarado — nada de função em aberto.

| id | tarefa | estado | evidência |
|---|---|---|---|
| PUL-01 | **Lacuna 2 — a janela entre a lease de 180 s e o prazo do item. ✅ MEDIDA em 2026-09-09; segue ABERTA por decisão.** A ficha dizia *"ninguém a mediu e não há reaper"* desde 2026-08-03, e a afirmação vinha de um **docstring** (`registry.py:105`) — a família que já mentiu **duas vezes neste mesmo arquivo** (prometeu auto-release por heartbeat, que nunca existiu; foi "corrigida" para o reap de órfãos, que também não alcança, porque ele só colhe sessão FECHADA e no claim abandonado o delegate está SUSPENSO). Hoje nada é lido: `infra/test/probe_claim_lease_invisibility.sh` **exerce**. **O que a medição achou:** (a) a janela EXISTE — o claim faz ZREM, e com a lease vencida outro agente é recusado, **com controle positivo ao lado** (após `release`, o mesmo agente leva o item); (b) **há uma rede, e ela passa ao lado** — o `CrashDetector` detecta a instância morta e republica a CONVERSA em `conversations.inbound`, não o ITEM pelo `work_task_release`, que é a porta que o próprio bridge declara ser a única válida: medido ao vivo, o item **não volta** à fila 40 s após a queda; (c) **DANO = 0** — 85 wrap-ups no histórico, **80** submetidos, **zero** `acw_expired` e **zero** `acw_supervisor_closed`: nenhum item chegou ao prazo sem ninguém. ⚠️ **Achado fora do enunciado:** a recusa ao segundo agente é `already_claimed`, não `not_in_queue` — o motivo de quem **perde uma corrida**, e aqui não há corrida: o dono sumiu e o item não volta. O sinal mais visível da lacuna se lê como concorrência saudável. **Candidato de conserto, nomeado e não construído:** o `CrashDetector` já detecta a queda — falta o ramo que, para sessão com ledger `work_task`, chame `work_task_release` em vez de republicar (mesmo processo, sem HTTP). **Gatilho para retomar: o probe ficar VERMELHO** — dano > 0 (um `acw_expired` aparece) ou a janela fechar sozinha | `adiado` | `CHANGELOG.md` § 2026-09-09 (13); gate `probe_claim_lease_invisibility.sh` |

---

## `docs/arcos/customer-surveys.md` — módulo de pesquisas

| id | tarefa | estado | evidência |
|---|---|---|---|
| SUR-01 | **S7** — editor de DialogForm (ganha importância com a reversão do n8n: o conteúdo segue autorado em casa) | `aberto` | `CLAUDE.md` § Customer Surveys |
| SUR-02 | Nenhum produtor de **CES/PMF/FCR** existe | `aberto` | idem |
| SUR-03 | `value_label` ignorado em `CustomerVoicePage.tsx:161` | `aberto` | idem |
| SUR-04 | S5, S8, S9–S11 e o store per-response | `aberto` | idem |
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
| AUT-22 | **`config.permissions` deve ser `scopable`?** Hoje e `false`: a capacidade e do tenant INTEIRO, logo auto-conceder um pool **nao e escalacao para o detentor legitimo** — ele ja manda no escopo de todos. A tela de Access lista os 36 pools porque `GET /v1/pools` filtra so por tenant. Se algum dia existir "admin regional", isso muda e a lista irrestrita vira furo. **Pergunta de desenho, nao defeito**. ⚠️ **Confirmada pelo dono em 2026-09-12**: segue adiada com este gatilho — a resposta depende de um papel que nao existe, e antecipa-la seria decidir no lugar errado | `adiado` — gatilho: surgir papel de administracao de permissoes com escopo | ADR granularidade, secao "NAO decide" |
| AUT-28 | **Recorte de pool nas duas rotas de DÍVIDA declarada** (`_SCOPE_DEBT` em `reports.py`): `/reports/campaigns` (`collect_events` só tem `collect_token`/`instance_id`) e `/reports/evaluator-calibration` (`calibration_events` só tem `evaluator_id`). Há um caminho concebível para a primeira — `instance_id` → `workflow_events.pool_id` — e ele **não foi construído de propósito**: as duas tabelas estão VAZIAS, então o join entraria sem nunca ter sido visto funcionar (é como nasceu o filtro de canal da F2, que filtrava esvaziando). O censo do `probe_report_row_scope.sh` as CONTA a cada execução | `adiado` — gatilho: qualquer das duas tabelas ganhar produtor | `CLAUDE.md` § Security |
| AUT-29 | **A recusa por escopo precisa de um jeito de saber "este chamador alcança o tenant inteiro", e ele não existe.** ⚠️ *Linha CORRIGIDA em 2026-08-31: a versão anterior dizia `bloqueado por AUT-15`, presumindo que a AUT-15 entregaria o claim `unrestricted` ao admin — e a AUT-15 é o **oposto**, a remoção do campo. Eu a escrevi errado no fecho da AUT-01, no mesmo dia; a dependência estava invertida.* O fato medido continua de pé: `admin@plughub.local` carrega uma LISTA de 36 pools, `accessible_pools is None` só vale para principal de SERVIÇO, e por isso *"recuse o chamador escopado"* devolveria 403 ao administrador. Com o campo removido, a decisão do dono é que **escopo é sempre enumerado** — então o discriminador teria de ser *"o escopo cobre o universo de pools do registry?"*, dependência nova com caminho de degradação próprio. Só se paga se as tabelas da AUT-28 ganharem produtor. ⚠️ **DESADIADA em 2026-09-08 pela emenda E5 do ADR de granularidade**: a decisão de que **pools são sempre ENUMERADOS** — ninguém alcança o tenant por regra, o `admin@` alcança por seed que enumera — torna o discriminador **computável** (`escopo ⊇ universo de pools do registry`), que é exatamente o que esta ficha declarava não existir. Sai de adiada-por-impossibilidade para aberta-por-trabalho. ⚠️ **Correção de algumas horas depois, no mesmo dia:** eu escrevi aqui que a E5 *"depende da inversão da AUT-03"* — a dependência não existe, porque **a AUT-03 fechou em 2026-08-31** e a inversão está viva (`LEGACY_EMPTY_MEANS_UNRESTRICTED = False`, medido). Eu a li como `bloqueado` numa **nota de prosa** deste arquivo que sobreviveu ao fechamento da ficha. O efeito é o inverso do que escrevi: a enumeração da E5 não fica esperando nada — ela ficou **urgente**, porque a inversão já vale e o `admin@` está com `{}` (ver AUT-43) | `aberto` | `CLAUDE.md` § Security |
| AUT-32 | **Coluna física `auth.users.unrestricted` sobrevive ao código.** O `DROP COLUMN` é irreversível e apagaria as 2 linhas que hoje a têm `true`; o precedente da casa é o oposto (`agent_group_members`/`agent_group_shifts`, 2026-07-02: *"podem existir fisicamente em bancos antigos — o código não mais as cria/lê/escreve"*). O `CREATE TABLE` deixou de criá-la e o `ALTER ... ADD COLUMN` saiu, então ela não renasce a cada boot. Fechar exige decidir se vale uma migração destrutiva por uma coluna inerte. ⚠️ **Confirmada pelo dono em 2026-09-12**: segue adiada com este gatilho — coluna inerte nao custa nada, e migracao destrutiva so se paga de carona com outra | `adiado` — gatilho: próxima migração destrutiva já planejada em `auth.users` | `CHANGELOG.md` § lápide do `unrestricted` |
| AUT-51 | **`analytics-api/auth.py` decodifica JWT por conta própria, em três sites, e o censo do `probe_authz_single_verifier` NÃO o conta.** Não é furo do probe — o critério do C1 é *“lê `module_config` E decodifica”*, e esse arquivo só faz a segunda metade (é a porta que confere `admin_jwt_secret` **e** `auth_jwt_secret`, e onde sobrevivem os detalhes `Token expired`/`Invalid token` que a AUT-45 aposentou no vizinho). Medido de passagem ao fechar a AUT-45, em 2026-09-12, num arquivo que não foi tocado. Decidir: migrar para `plughub_authz.verify_user_jwt` — os DOIS segredos são o que torna a decisão não-óbvia, porque são duas confianças distintas na mesma função —, ou declarar a isenção no cabeçalho do probe com o motivo. **Enquanto nenhuma das duas for feita, o eixo fica afirmado por herança**, que é o defeito que o C4 fechou do outro lado em 2026-08-28 | `aberto` | `CHANGELOG.md` 2026-09-12 (2) |

⚠️ **A nota que explicava por que a AUT-03 estava `bloqueada` foi REMOVIDA em 2026-09-08, e a
remoção é o próprio achado.** Ela dizia que inverter `[]` era arriscado pela **cauda**, e a AUT-03
**fechou em 2026-08-31** (`LEGACY_EMPTY_MEANS_UNRESTRICTED = False`, medido ao vivo hoje) — a nota
sobreviveu ao fato por oito dias, afirmando `bloqueado` sobre uma ficha que já estava no `done.md`.
⚠️ **O `probe_task_ledger.sh` não podia pegar**: o ramo A lê **id na primeira célula de linha de
tabela**, e isto era prosa. Status velho em parágrafo é o ponto cego do gate — foi exatamente o que
me fez afirmar, nesta sessão, que a inversão ainda não tinha acontecido. E a cauda que a nota temia
**existe, na direção oposta à prevista**: ver AUT-43.

---

## `docs/adr/adr-abac-module-granularity-and-delegation.md` — granularidade e delegacao ABAC

Nasceu da demanda do dono (2026-08-31): rotatividade alta exige que o supervisor contrate sem
poder reescrever a propria fronteira. **A ordem G1 -> G2 -> G3 e inegociavel** — revogar antes
de existir o veiculo tira a contratacao do supervisor sem dar nada em troca.

| id | tarefa | estado | evidencia |
|---|---|---|---|

---

## `docs/product/journey-retorno-modelo-3-niveis-design.md` — Journey

Frente fechada (ver `done.md`); resta um item **adiado por decisão**.

| id | tarefa | estado | evidência |
|---|---|---|---|
| JRN-03 | Refrescar o cache `sessions.journey_id` no merge | `adiado` por decisão — as leituras vão por union-find. Gatilho: custo de leitura medido | `CHANGELOG.md:17736` |
| JRN-04 | **Exibição do sinal N3 no drill da própria Vista Processos** — o sinal é produzido e não aparece onde o operador o procuraria | `aberto` | `CLAUDE.md` § Pending, movido em 2026-09-05 |

---

## `docs/guias/mention-protocol.md` — protocolo @mention

✅ **As duas denúncias deste grupo FECHARAM em 2026-09-12 (MEN-01/MEN-02).** Ficam
registradas porque explicam o que o grupo mediu e por que ele existiu.

**(1) O invariante declarado não era imposto pelo gate que existia para impô-lo** (medido
2026-09-01). O guia dizia *"Agentes IA em conferência **não** podem usar `@mention`"* e,
duas linhas acima, definia a regra como *"apenas `role: primary` ou `role: human`"* — duas
frases que não são a mesma coisa, porque `primary` é POSIÇÃO na sessão e não espécie.
Medido: **1144 segmentos `native/primary` + 100 `ai/primary`** contra 333 `human/primary`.
Hoje a regra escrita e a imposta são a mesma — *quem conduz menciona; quem foi convidado
não convida* —, e `role: human`, que nunca existiu no domínio de papel, saiu do código.

**(2) O gate tinha o MESMO defeito estrutural do gate do avaliador**: resolvia o papel de
um `participant_id` vindo do **input**, tendo o `instance_id` assinado em mãos. Consertado
na mesma passada (`resolveRoleByInstance`, sem fallback para a identidade declarada). A
tabela dos dois gates vive em `docs/adr/adr-remove-agent-role-axis.md` § *Achado estrutural
compartilhado*, e ganhou lá a nota de fechamento desta metade.

⚠️ **Eixo DIFERENTE do `agent_role`** (grupo `CAP`): campo, casa, produtor e consumidor
distintos. O que os une é o defeito de forma, não o assunto.

| id | tarefa | estado | evidência |
|---|---|---|---|
| MEN-04 | **Só há EXPOSIÇÃO medida, não DANO.** O caminho está aberto (LLM recebe a tool porque `permissions: []` = sem filtro; a IA é `primary`; o gate passa). NÃO medido que alguém passou: 0 @mentions na janela de log, anteriores perdidos no rebuild. Fecha com um contato real em pool de IA com `@alias` no texto — os três desfechos do gate já logam diferente. **Publicar exposição como dano é a D14.1 ao contrário**. ⚠️ **Remedido em 2026-09-12**: 0 @mentions em 2 734 mensagens de `tenant_demo` desde 2026-06-10 — o zero desta ficha continua de pe, e continua sendo ausencia de TRAFEGO, nao prova de que o caminho esteja fechado. ⚠️ **Continua de pé depois da MEN-01 (2026-09-12), e por isso mesmo:** o gate mudou de eixo e ganhou binding de identidade assinada, mas nada disso é DANO medido — segue faltando um contato real em pool de IA com `@alias` no texto. Os desfechos do gate continuam logando diferente, e agora são dois caminhos a observar, não um | `aberto` — precisa de tráfego | `TODO.md` § gate de @mention |
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
| CAP-10 | **DECIDIDO pelo dono em 2026-09-12: a resposta continua sendo TOPOLOGIA; as 47 viram divida de defesa-em-profundidade, com gatilho nomeado.** ⚠️ **A premissa antiga da ficha estava VENCIDA havia 11 dias** — ela dizia *"`3100:3100` no demo e no full, e nenhum compose a restringe"*, e os dois publicam `127.0.0.1:3100:3100` desde a **CAP-13** (2026-09-01). Censo ao vivo de 2026-09-12: **74 tools**, `ok=23 · isento=1 · divida=50` (eram 47 ate a CAP-18 classificar as tres que faltavam), **36 sem camada de guarda nenhuma**, transporte `/sse` anonimo por construcao — o que torna FALSO o invariante do `CLAUDE.md` do proprio pacote (*"Every tool authenticates via JWT"*), corrigido agora. ⚠️ O furo que a topologia esconde, e a razao de fechar tool a tool render pouco: **`agent_login` e AUTO-SERVICO** — quem alcanca a porta cunha um `session_token` assinado nomeando qualquer `skill_id`, entao as 23 `ok` aceitam um token que qualquer um emite. A saida do dia do gatilho ja esta nomeada no proprio compose: `MCP_INTERNAL_SERVICE_TOKEN`, falhando FECHADO | `adiado` — gatilho: a 3100 sair do loopback, ou o deploy virar distribuido | `CHANGELOG.md` § 2026-09-12 (4) |
| CAP-15 | **Eliminar a publicação da 3100 de vez — e o gatilho é a topologia de deploy, não esforço.** O passo intermediário fechou em 2026-09-01 (CAP-13): o bind é `127.0.0.1:3100:3100` e o transporte MCP saiu da LAN (medido antes: `Test-NetConnection 192.168.1.124:3100` → **ACEITA**; depois → **recusada**, com a 5174 de controle seguindo ACEITA nas duas rodadas). O que sobra é remover a linha `ports:` inteira, e ela ainda tem consumidores REAIS de host: o proxy do `vite.config.ts` (dev), a suíte e2e (`MCP_SERVER_URL`) e os probes de `infra/test/` — todos em `localhost`. Fechar exige **mover essas três famílias para dentro da rede do compose** (o precedente existe: o `probe_release_reclaim_race.sh` já roda por `docker compose exec` justamente para medir de dentro). ⚠️ **A bifurcação que decide se isto vale a pena:** se o alvo for deploy DISTRIBUÍDO — contêineres em máquinas diferentes —, a porta volta a ser NECESSÁRIA e a pergunta deixa de ser topologia: vira **autenticação de transporte**, e aí o trabalho é outro (o padrão já existe na casa: `MCP_INTERNAL_SERVICE_TOKEN`, usado pelas duas `/internal/*`, falhando FECHADO). Investir em remover a porta antes dessa decisão pode ser trabalho jogado fora. ⚠️ **Ao mover para a borda, NÃO mapear `/sse` e `/messages` no nginx** — mapear tudo transfere a exposição da 3100 para a 5174 e desfaz o ganho; hoje a borda devolve `text/html` no `/sse`, e é isso que faz as 48 dívidas de tool da CAP-09 serem inalcançáveis de fora. Gate que trava o estado atual: ramo F do `probe_mcp_rest_surface.sh` (declaração nos dois composes × bind vivo, provado falseável nas duas metades) | `adiado` — gatilho: decidir se o alvo é rede compartilhada ou deploy distribuído | `CHANGELOG.md` 2026-09-01, `infra/test/probe_mcp_rest_surface.sh` ramo F |
| CAP-14 | **As nove rotas da CAP-12 exigem CREDENCIAL e não recortam LINHA — e a pior delas nem tem tenant.** São dois fatos, e a analytics-api pagou para aprender que são (*"EXIGIR CREDENCIAL e RECORTAR LINHA são dois fatos"*, 08-29 × 08-30). Hoje qualquer operador autenticado lê a conversa de **qualquer** sessão por `GET /api/conversation_history/{id}` — e a chave que ela lê, `session:{id}:messages`, **não tem prefixo de tenant**, então nem o isolamento por tenant existe ali (as irmãs `copilot_state` e `supervisor_capabilities` resolvem o tenant pelo `resolveSessionTenant` e recusam o indeterminável; esta não tem o que resolver). ⚠️ **Agrava por repetição de padrão:** o irmão do mesmo dado (`analytics-api /v1/transcript/sessions/{id}`) é gateado **e escopado** desde 2026-08-30 — de novo *duas portas para o mesmo dado, e só uma recorta*, que é exatamente a forma do achado de 08-28. ⚠️ **Fechar não é copiar o predicado do analytics:** lá o recorte é sobre ClickHouse (`_session_scope_clause`, união entrou-por ∪ atendeu-por), e aqui a fonte é Redis vivo; o decisor teria de ser o `resolveSessionTenant` + o `accessible_pools` do JWT, e a decisão sobre o INDETERMINÁVEL precisa ser medida antes (no analytics foram 10 de 947). Gatilho: o primeiro tenant com operadores que não devem se ver, ou o primeiro pedido de multi-tenant nesta porta | `adiado` — gatilho declarado acima | `infra/test/probe_mcp_rest_surface.sh` § DÍVIDA DE ESCOPO |
---

## `docs/adr/adr-agent-flow-single-authored-level.md` — um nível autorado

*(⚠️ NÃO confundir com o modelo de escopo `segment`/`session`/`journey`, que segue vigente e
intocado. "Três níveis" nomeia dois modelos neste repositório — ver a desambiguação no topo da ADR.)*

| id | tarefa | estado | onde |
|---|---|---|---|
| NIV-09 | **O ramo MORTO da eleição de canal — decidir se sai ou se volta a viver.** `select_channel` (pura, testada, capability-aware) só é alcançada por `main.py::_dispatch_collect`, consumidor de `collect.requested`; o único produtor desse evento tem **zero chamadores** (AST) e as duas rotas de collect da workflow-api respondem **410** desde o Arc 19 Fase D. Com ele parado há duas eleições no repositório e **uma só decide** — situação estável, e por isso não é defeito hoje. O que NÃO pode acontecer é o produtor voltar sem que alguém escolha: aí voltam a ser duas decidindo, com semânticas diferentes (`requires[]` declarado × derivado), e a permissiva vence. O predicado (`channel_satisfies`) já é compartilhado — o que está em jogo é só a eleição. ⚠️ **Não confundir com o ramo `_dispatch_collect` → `adapter.handle_collect_event` dos adapters sms/email/whatsapp**, que morre junto. ⚠️ **Confirmada pelo dono em 2026-09-12**: segue adiada com este gatilho — situacao estavel, e decidir agora a semantica vencedora seria politica contra populacao zero | `adiado` | gatilho: `emit_collect_requested` ganhar chamador (ramo E de `infra/test/probe_collect_masked_requirement.sh`) |
| NIV-11 | **Migrar o roteiro dos 23 fluxos restantes** — o padrão está pronto e provado no piloto: uma forma por fluxo, **uma** carga (`invoke form_get` → `output_as: roteiro`) e cada `notify`/`menu` referenciando `render.by_node.<id do step>`. Medido em 2026-09-03: **99 pontos de roteiro cravado em 24 skills** (79 estáticos + 20 dinâmicos); o piloto tirou 12, restam **67 estáticos**. ⚠️ **Continua caso a caso, na próxima edição de cada fluxo — não é varredura**: cada migração custa `set-next`+`promote` do pool dono e não muda comportamento nenhum, então em massa é risco sem entrega. ⚠️ **A frase de degradação de cada fluxo FICA cravada** — é o caminho de quem falhou ao carregar o roteiro, e buscá-la na forma que acabou de falhar deixaria o fluxo MUDO. ⚠️ Rótulos de `options[]` **não** entram nesta linha: `by_node` mapeia texto de nó, e endereçar opção pede outro passo (medir antes) | `adiado` | gatilho: a próxima edição de cada skill · exemplar em `skill_limite_entrada_v1` |
| NIV-12 | **Os 20 pontos de roteiro DINÂMICO não migram — o `interpolate` é de passe ÚNICO.** Medido: ele coleta os `{{…}}` do template ORIGINAL, resolve e substitui; um valor inserido que contenha `{{…}}` é colocado **verbatim e nunca reinterpretado**, então texto vindo da forma com referência a `$.pipeline_state.*` chegaria ao cliente **com as chaves literais na tela**. É o caso do `menu_continuidade` (cartão, limite, status) e do `confirmar_recebimento`. Habilitar pede uma segunda passada, e ela tem **vetor próprio**: quem edita conteúdo passaria a poder injetar referências ao `pipeline_state` — hoje o conteúdo é texto e nada mais. Decidir o escopo (lista de refs permitidas? só `$.pipeline_state.*`?) antes de habilitar | `aberto` | `skill-flow-engine/src/interpolate.ts` |
| NIV-05 | **A capacidade `masked_input` está definida pelo MECANISMO, e isso torna o gatilho de voz inalcançável.** O enum diz `password-overlay masked field (webchat)` — lido ao pé da letra, voz nunca qualifica, ainda que o plano de mídia suba E o DTMF seja suprimido. Achado pelo dono ao perguntar se a NIV-01 mantinha voz elegível; era **erro meu** escrever um gatilho que a própria definição fecha. Capacidade tem de descrever a **GARANTIA** — *"o canal coleta entrada sem que o valor apareça em nenhuma superfície de leitura (transcript, histórico, gravação)"* — e não a implementação. **Redação fechada em 2026-09-03**, depois de duas correções do dono: *"o canal coleta entrada sem que o valor apareça em nenhuma superfície de leitura **controlada pela plataforma** — transcript, histórico durável, e gravação **quando a plataforma grava**"*. O recorte por CONTROLE é o que faz a definição sobreviver aos três modos: em SIP/WebRTC a plataforma grava e o dígito não está no áudio (RFC 4733/2833); em CTI a gravação é do PABX e simplesmente **não está no conjunto dela** — que é diferente de estar e ser segura. Assim webchat qualifica por overlay e voz por transporte out-of-band: mecanismos diferentes, mesma garantia. É o mesmo movimento do `EchoMode`: domínio abstrato, cada canal interpreta. ⚠️ **Muda quem pode reivindicar a capacidade** — é decisão de contrato, não ajuste de comentário. **Pré-requisito real** do gatilho de voz ⚠️ **Passo 3 do conjunto de reentrada da voz** (ver o quadro acima do grupo). | `aberto` | `skill.ts` `ChannelCapabilitySchema` |
| NIV-06 | **Construir o tratamento de eco em voz — é LACUNA, não vazamento.** Medido em 2026-09-03: `voice.py` tem **zero ocorrências de "masked"**. Não há eco a mascarar porque não há eco: o adapter não verbaliza o dígito, não bipa e não cala por política. O desenho vem pronto do dono e do `EchoMode` (ALW-10): `plain` verbaliza (*"um, dois, nove"*) · `masked` bipa · `none` cala — **o eco existe sempre para dar feedback de tecla**, e é por isso que `none` é modo próprio. ⚠️ Esta é a linha que substitui a NIV-06 original, cujo enunciado (*"supressão de DTMF na gravação"*) foi **refutado** — ver NIV-07. ⚠️ **Há causa a montante: a VOZ-03** — o `collect` de voz nunca completa (método chamado e nunca definido), então o caminho onde o eco viveria nunca foi exercido ponta a ponta ⚠️ **Emenda de 2026-09-12 — não é por canal.** O eco é áudio que NÓS geramos, nasce no bot leg e é entregue à perna onde o cliente estiver: é **uma implementação só**, não uma por canal. O que varia é se a perna tem teclado — PSTN sempre tem, o cliente WebRTC só se lhe dermos um dialpad; coleta por campo cai no `type=password` da ALW-15, que é outro caso ⚠️ **Passo 4 do conjunto de reentrada da voz.** | `bloqueado` por `VOZ-05` — chega com o bot leg (STT/TTS), não com o SFU |
| NIV-07 | **Segredo digitado não pode alcançar superfície de leitura da plataforma — são QUATRO controles, em ordem de força.** ⚠️ **Reescrita em 2026-09-12**, depois de o dono observar que *"recusar o fallback"* não é configurável: **in-band não é uma opção negociada, é a AUSÊNCIA de negociação** — fora de banda aparece no SDP (`a=rtpmap:101 telephone-event/8000`), in-band não aparece em lugar nenhum. E a exposição **não é só a gravação**: durante a coleta o áudio chega também ao **agente humano** e ao **supervisor em escuta**, e um tom se decodifica com ferramenta gratuita. Os controles: **(1) exigir `telephone-event` na negociação** e recusar quando não vier — estrutural, e é configuração de qualquer stack sério; **(2) isolar a perna durante o bloco mascarado** — só o bot leg assina o áudio do cliente, gravação pausada: estrutural, **não depende do stack**, e é o equivalente em voz do `begin_transaction`/`end_transaction` que o mascaramento por texto já tem; **(3) clamping no caminho de mídia**, que cobre a ponta que manda os dois — probabilístico (tom curto ou distorcido pelo codec escapa) e por isso **critério de seleção do conversor/SBC, verificado em POC**, não promessa de config; **(4)** ~~filtrar só no gravador~~ — insuficiente sozinho: limpa a gravação e deixa agente e supervisor ouvindo. ⚠️ **Com (1) e (2), voz pode declarar `masked_input` mesmo em stack sem clamping** — o que destrava a NIV-05 sem depender de fornecedor. Duas metades, dois prazos ⚠️ **Passo 5 do conjunto de reentrada da voz.** | `bloqueado` por `VOZ-04` (metade WebRTC) e por `VOZ-02` (metade SIP) | `docs/adr/adr-voice-media-plane.md` §8 · NIV-05 é pré-requisito |
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
> 2. **VOZ-03** — o ciclo de vida de SESSÃO do canal (`_open_session`,
>    `_route_inbound`, `_close_session`, ainda ausentes). A publicação do
>    `menu_result` de voz foi entregue em 2026-09-07, mas sem abertura e
>    fechamento de sessão o caminho não roda ponta a ponta.
> 3. **NIV-05** — redefinir a capacidade pela **GARANTIA**. Enquanto o enum disser
>    `password-overlay … (webchat)`, voz não qualifica nem com tudo pronto.
> 4. **NIV-06** — construir o eco (`plain` verbaliza · `masked` bipa · `none` cala).
> 5. **NIV-07** — os quatro controles do dígito (exigir `telephone-event`; isolar a
>    perna no bloco mascarado; clampar no caminho de mídia; gravador não basta).
>    *Reescrita em 2026-09-12 — antes dizia só "asserir a negociação e recusar o
>    fallback", e o fallback não é recusável: in-band é a ausência de negociação.*
> 6. **NIV-08** — recusar `masked` + `input_mode: voice` (aí o cliente **fala** o
>    valor e o STT transcreve — vetor independente do DTMF, e nenhum RFC ajuda).
> 7. **só então** declarar `masked_input` para `voice`.
>
> ⚠️ A NIV-08 **precede** a declaração; adiá-la com gatilho *"voz ganhar
> `masked_input`"* construiria a recusa depois do buraco abrir. Erro de ordenação
> corrigido em 2026-09-03, antes de virar linha de ledger.

---

## `docs/adr/adr-identity-door-evidence.md` — porta de identidade

Proposta em 2026-09-11, depois de fechada com o dono. **A plataforma identifica, prova e registra;
a régua é da aplicação.** Caminho crítico: **PID-01 → PID-02 → PID-03** — sem identidade assinada
nas tools e sem evidência que não se forja, a porta não garante nada contra fluxo autorado.
**PID-01, PID-02, PID-03 e PID-07 fechadas em 2026-09-13** — a PID-06 está desbloqueada: o slot já não é gravável por outro tenant nem sem autor. **PID-10 depende de IDN-07.** A migração dos dois intakes (PID-04) vem
**depois** da chave de retomada. As três lacunas do cadastro existente estão no grupo IDN
(`IDN-06..08`).

| id | tarefa | estado | evidência |
|---|---|---|---|
| PID-04 | **`skill_intake_runner_v1` — a porta de plataforma**, com `door_mode`, `require`, `on_new_pool`, `degrade_target`, `dialog_form_id`, `accept_resume_key`; e a migração dos dois intakes vivos, que repetem os mesmos 18 step ids (`skill_limite_entrada_v1` × `agente_portabilidade_intake_v1`). A migração vem depois da chave de retomada | `aberto` | ADR D2, D12 |
| PID-05 | **`skill_identity_orchestrator_v1`** — dono da composição, um `config_param` por mecanismo (`enable_otp`, `enable_biometrics`), tipo novo `required: false` com default. Pode nascer dentro da porta: a interface é a mesma | `aberto` | ADR D3 |
| PID-06 | **`resume_requires` e `resume_door` no step do N3**, por `$.config.*` (união objeto \| ref, como `channel_policy`); mínimo declarado no skill; `judgeIdentityFloor` recusa no deploy a config que não contém o mínimo — nunca ajusta em silêncio. Desbloqueada (PID-07 fechada): trocar o `skill_id` no slot exige `skill_flows.operacao` no tenant do token, com autor gravado — o `judgeIdentityFloor` continua necessário porque um devops legítimo pode promover config sem o mínimo | `aberto` | ADR D7 |
| PID-08 | **O deploy em lote registra sem mudar.** `skill_deploy` (`pool_ids`) → `POST /v1/skills/:id/deploy` grava `skill.flow` e um `SkillDeployment` com os pools, mas não toca slot; o bridge executa o snapshot do slot `current`. Para pool com slot, a linha diz "implantado em X" enquanto X roda o antigo. Aposentar ou corrigir, e dar às portas um promote em lote sobre slots, com rollback por pool | `aberto` | `skills.ts:410` · ADR §4 |
| PID-09 | **A chegada autenticada não gera âncora.** O `from` do WhatsApp é o E.164 autenticado pela Meta e o adapter só o usa como chave de sessão; o envelope `origin_identity` da spec §4.4 não existe no código. O adapter passa a produzir evidência de chegada: `princ` (login federado) e `(whatsapp, from)` quando bate com o telefone cadastrado. ⚠️ O inbound do WhatsApp chama o telefone de `customer_id`, que não é o `customer_id` nativo — mesmo nome, dois fatos | `aberto` | `whatsapp.py:155` · ADR D9 |
| PID-11 | **Porta compartilhada: prova para VER, prova para ENTRAR.** A lista de pendências é liberada pela evidência de chegada (forte → direto; fraca → prova mínima antes); a seleção aciona a exigência do item; ordem por `expires_at` — hoje `find_pending_by_customer` achata a primeira em ordem arbitrária. Abrir processo novo nunca exige identificação | `aberto` | ADR D11 |

---

## `docs/guias/masked-input.md` — mascaramento de entrada por canal

| id | tarefa | estado | onde |
|---|---|---|---|

## `docs/adr/adr-dialog-tree-options.md` — opcoes em ARVORE no DialogForm

*(A F0 — `flattenBlocks` lossless — fechou em 2026-09-04; ver `done.md`. As demais fases
seguem sem tarefa aberta neste ledger.)*

| id | tarefa | status | referencia |
|---|---|---|---|

## `docs/adr/adr-orchestrator-tree-navigation.md` — navegacao de orquestrador como arvore

*(F0 — medicao — fechou em 2026-09-05 ao escrever o ADR; ver `done.md`.)*

| id | tarefa | status | referencia |
|---|---|---|---|
| ORQ-08 | **`WsMenuRender` é declaração cujo único consumidor é o próprio teste.** Achado de passagem na F2 (2026-09-06): o modelo declara `options: list[dict[str, str]]`, que **rejeitaria** uma opção com filhos (o valor seria uma lista, não `str`) — e não rejeita, porque o caminho real do webchat não o constrói: `WsMenuRender(` aparece em `models.py` (definição) e em `tests/test_models.py`, e em lugar nenhum mais. O payload viaja como dict cru. Mesma família do `options_tree` e do `process_context`: um contrato que parece impor forma e não impõe nada. **Não é defeito vivo** — é escolher entre usá-lo (e aí o tipo precisa admitir árvore) ou removê-lo. | `aberto` | medido 2026-09-06 na F2 |
| DUR-01 | **`menu` durável: suspend-resume em vez de BLPOP em processo.** ⚠️ **NÃO é fase da ORQ** — mora sob este grupo porque foi a D11 deste ADR que o mediu. ⚠️ **CORRIGIDO em 2026-09-06:** a primeira versão desta linha dizia que *"ninguém reinvoca `run()`"* e que a resposta ficava na lista até o TTL. **Errado** — o `grep` que produziu aquilo estava truncado por `| head`. A reentrada EXISTE: o `CrashDetector` pula a conversa enquanto o *execution lock* (TTL 400 s) ou o *activity flag* (TTL 30 s, renovado por timer DENTRO do processo) existirem, e **re-enfileira** quando os dois somem — o engine então retoma do `current_step_id`. São **nove** os steps que chamam `saveState` (não três), e `invoke`/`notify` ainda têm sentinela de duas fases que torna a chamada MCP idempotente através de queda. **O fluxo não se perde.** O que se perde: a resposta em voo (a chave `menu:result` carrega o `instance_id` morto ⇒ o cliente é **perguntado de novo**), o tempo até recuperar (até ~180 s para um menu de 120 s) e a **licença de IA retida na espera**. **Por isso o mérito MUDOU de natureza:** não é robustez, é **capacidade + latência**. Pela Arc 19 `suspend` devolve o agente ao pool, então o modelo contextless liberaria a licença entre turnos — este passou a ser o argumento principal, não o terceiro. Atinge todo skill de agente (17 usam `menu`) e exige que o bridge reinvoque em vez de LPUSH. **Ordem:** se virar prioridade, vem **antes da ORQ-04**. **Gatilho para ADR próprio:** a decisão de priorizá-lo | `adiado` | gatilho declarado; ADR § D11 + correção de 2026-09-06 |


## `docs/adr/adr-orchestrator-specialist-contract.md` — orquestrador dono, especialista executor

*(G0 — medicao — fechou em 2026-09-06 ao escrever o ADR; ver `done.md`.)*

| id | o que falta | estado | ancora |
|---|---|---|---|
| CTR-02 | **G2 — `provides` simetrico ao `required_context`, com CONFERENCIA.** ⚠️ **REBAIXADA em 2026-09-07, e a razao e que a JUSTIFICATIVA caiu.** O ADR dizia *"sem ela a cadeia orquestrador->especialista->orquestrador nao se compoe"*; ela **se compoe** — `delegate` + `on_return` a compuseram, e o dono validou em contato real (varios ciclos + escalate com NPS e wrap-up). Logo `provides` nao destrava nada: o valor que resta e declarar PRE-CONDICAO, para o orquestrador nao despachar a quem nao pode comecar. E real, e menor, e nao bloqueia ninguem. ⚠️ **E a metade de ENTRADA continua ociosa depois de um arco inteiro** (medido 2026-09-07, o numero PIOROU porque o parque cresceu): **1 de 46** skills declara `required_context`, **0 de 46** le `@ctx.__gaps__`, **0 de 46** usa o step `resolve`, **0 de 46** declara `provides`. Construir a metade de SAIDA sobre um mecanismo que ninguem usa cria o segundo catalogo de declaracoes sem consumidor — que e exatamente o risco que o proprio ADR nomeia para o `provides`. **Gatilho: UM skill declarar `required_context` E ler os gaps num caminho vivo.** Enquanto isso nao acontecer, a saida e engenharia especulativa. Quando destravar, o mecanismo continua sendo comparar o declarado com o que esta no store ao fim do skill e CONTAR a diferenca — declarar sozinho e promessa | `adiado` | ADR G2/D6; refutada por medicao em 2026-09-07 |
| CTR-04 | **G4 — os especialistas viram executores.** Medido: nenhum dos 5 e executor puro (todos encerram sozinhos, 3 escalam, todos perguntam) — heranca de terem nascido como atendentes exclusivos. E ha **3 menus de DEMANDA dentro de folhas**: `sac.menu_motivo`, `auth.menu_continuar` e `sac.menu_resolucao`. ⚠️ **Isto reclassifica a F4**: o atalho por `session.navegacao.path` foi tratado como conserto e e tratamento de SINTOMA — `auth_sac_ia` tem a mesma folha-que-navega e produzira a mesma tela que o dono confundiu com bug ⚠️ **Ganhou uma segunda metade, medida em 2026-09-06:** alem dos menus de demanda, cada desfecho do especialista (resolvido, timeout, escalou ao humano) tem de **devolver o controle** via `workflow_resume` com a sua `decision` — hoje **4 dos 5 nao chamam a tool**, e e isso, nao os menus, que bloqueia a CTR-03. O padrao canonico existe (`skill_dialog_runner_v1`: `retornar` / `retornar_falha`). Verificavel por `probe_orchestrator_delegability.sh`, que vira verde por destino conforme cada um passa a retornar ✅ **Metade do RETORNO ENTREGUE em 2026-09-06** (ver `done.md` CTR-08): os 4 executores devolvem o controle em todos os desfechos, condicionado ao token. ⚠️ **Ganhou um item em 2026-09-07, e ele so existe porque a CTR-06 o destravou:** `agente_portabilidade_intake_v1` e o QUINTO executor e nao devolve o controle — ele invoca `workflow_resume` cinco vezes e nenhuma delas retoma o chamador (retoma um `suspend` proprio). Enquanto isso durar, `portabilidade_ia` e o unico destino do mapa que continua fora do `delegate`. **Falta tambem a metade dos MENUS DE DEMANDA**: `sac.menu_motivo` (ja contornado pelo `veio_da_navegacao` da ORQ-04, e FICA por decisao — o pool tem porta propria), `auth.menu_continuar` (nunca observado) e `sac.menu_resolucao` (o ADR quer que vire o RETORNO — com o contrato de saida agora existindo, e o orquestrador que deve perguntar *e agora?*, nao a folha) | `aberto` | metade restante: os 3 menus de demanda |
| CTR-05 | **G5 — tag de ORIGEM no evento de demanda + fronteira com o AgentCard.** Fluxo ATIVO emite a mesma `category`, mas o caminho veio do PROCESSO, nao do cliente; sem distinguir, a serie mistura *"o que o cliente pediu"* com *"o que fomos oferecer"* e perde o sentido que a F1 lhe deu. E o contrato de contexto **nao e** `inputModes`/`outputModes` do A2A (modalidade de interface): colapsar os dois faria um parceiro ler *"preciso de CPF"* como *"aceito text/plain"* | `aberto` | ADR G5/D4/D8 |

## `docs/adr/adr-tree-return-continuation.md` — a folha devolve, o chamador continua

| id | o que falta | estado | ancora |
|---|---|---|---|
| RET-10 | **O `payload` do retorno carrega decisao que ninguem le — e os dois casos sao do mesmo pool, que nunca rodou.** `auth_sac_ia.devolver_autenticado` manda `proximo: "sac"` (proposta explicita de continuacao: o cliente autenticou PARA falar com o SAC) e `devolver_encerrado` manda `motivo: cliente_encerrou` com `decision: "input"` — entao a continuacao pergunta *"mais alguma coisa?"* a quem acabou de dizer que terminou. O dado ja chega ao fluxo (`output_as: step.id` no delegate, logo `$.pipeline_state.delegar.*`), entao o consumo e YAML, sem schema nem engine. ⚠️ **NAO construido de proposito em 2026-09-07**: `auth_sac_ia` tem **zero segmentos** na historia, e ler o `proximo` abre um caso de demanda de ORIGEM PROCESSO (nao do cliente) que a CTR-05 ainda nao decidiu — emiti-lo sem distinguir poluiria a serie que a F1 criou. Gatilho: o primeiro contato real em `auth_sac_ia`; ou a CTR-05 fechar, o que vier antes | `aberto` | ADR § D2 (correcao de 2026-09-07); depende de CTR-05 |

| id | o que falta | estado | ancora |
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
| CTX-10 | **O carimbo de proveniencia EXISTE desde 2026-09-10, e o que resta aqui e a metade de PRECISAO.** ⚠️ A redacao anterior dizia que o carimbo virara divida de precisao *porque a rede e idempotente e nao corrompe valor ja mascarado* — verdadeiro, e resposta a pergunta vizinha: ele tambem nao corrompia o que NUNCA foi dado de cliente, e corrompia (15 contatos, `CTX-11`). O mecanismo ja esta de pe (`DECLARED_CONTENT_TOOLS` + `setResult`), so que na direcao da ISENCAO: ele diz *'isto e roteiro, nao olhe'*. **O que continua aberto e a direcao oposta** — carimbar o valor CAPTURADO com o TIPO declarado, onde hoje a rede so tem FORMA (pega 4 de 15 tipos). A decisao embutida continua sendo do dono: valor DERIVADO (concatenacao, resumo de LLM) herda o tipo de qual. ⚠️ O recorte recomendado segue o mesmo: carimbar so o INEQUIVOCO (resposta a campo declarado de DialogForm) e deixar o resto sem carimbo — carimbo errado e invisivel, ausencia e contavel. **Gatilho:** o proximo tipo do catalogo que a rede nao reconheca chegar a plateia de gente | `aberto` | ADR §D11, §D12, §D12.1 |
| CTX-12 | **A rede tipa um CPF CRU como TELEFONE, e a categoria nao e cosmetica.** Medido em 2026-09-10 ao diagnosticar a `CTX-11`: `detect_pattern` do `cpf` exige pontuacao (`\d{3}\.\d{3}\.\d{3}-\d{2}`), entao 11 digitos crus escapam da regra do CPF e casam a do telefone (`\d{2}` + `9?` + `\d{4}` + `\d{4}` = 11). O valor ate fica mascarado — mas com o TEMPLATE do telefone (`(##) ****-####`) e a CATEGORIA `phone`, que e o que vai para o log e para `FreeTextMaskResult.categories`. ⚠️ **Duas consequencias de naturezas diferentes:** (a) quem ler a trilha vera 'telefone' onde houve CPF; (b) o cliente que digita um CPF cru recebe de volta um gabarito de telefone, que foi exatamente o dano visivel da `CTX-11`. ⚠️ **E o conserto obvio tem risco proprio:** acrescentar `\b\d{11}\b` ao CPF faria a regra casar tambem telefone com DDD sem pontuacao — 11 digitos nao dizem qual dos dois sao, e a ORDEM das regras passaria a decidir. Nao ha decisao a tomar as cegas: medir primeiro a populacao de 11 digitos crus em texto livre e ver quantos sao CPF. **Gatilho:** a proxima vez que a categoria da rede for consumida por algo alem de log | `aberto` | `CHANGELOG.md` § 2026-09-10 (3) |

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
| DLG-18 | **O botao "+ pergunta" na linha da opcao — a pergunta IRMA ja guardada por `prefix`.** E a sintese aprovada no mockup: o autor ve o aninhamento (pergunta indentada sob a opcao que a dispara) e o JSON continua PLANO, porque o que se cria e uma irma com `ask_when {op:"prefix", value:<caminho>}` preenchido. Ninguem digita a guarda. ⚠️ **Nota de desenho que a implementacao da F5 revelou:** o mockup editava prompt/`output_key` INLINE, e isso seria um SEGUNDO editor para o mesmo no — o defeito de duas casas, em widget. A vista deve mostrar um CHIP e levar ao editor existente, nunca duplica-lo. Pre-requisito ja atendido: `ask_when` e autoravel por pergunta, e o op `prefix` esta no seletor desde a F5. **Gatilho:** decisao do dono — e afordancia, nao correcao | `aberto` | mockup aprovado 2026-09-05; `AskWhenRow` por pergunta ja existe |
| DLG-23 | **O `category` do Arc 12 nao tem SERIE por caminho na tela, so a tabela.** A vista de Eventos do Monitor foi consertada (ver `CHANGELOG` 2026-09-05 (12)) e ja responde a pergunta "quantos por caminho", mas e uma tabela de 24 h: nao ha como ver `motivo.financeiro.*` **ao longo do tempo** sem montar um cartao de dashboard a mao. O `/reports/agent-events/series` existe e aceita o mesmo prefixo; falta a superficie. ⚠️ Antes de desenhar, decidir de quem e a casa — a lente de contato (`adr-relatorios-duas-superficies-e-lentes`) ja tem forma `metric_lines`, e uma tela nova de eventos seria a terceira superficie a desenhar serie. **Gatilho:** primeira pergunta de tendencia sobre taxonomia de wrap-up ("cobranca indevida esta subindo?") | `aberto` | medido 2026-09-05, `/reports/agent-events/series` sem consumidor de UI |
| DLG-33 | **A tela do pool oferece um seletor de *Form* por entrada de hook, e o skill do outro lado pode simplesmente não ler o parâmetro — hoje o NPS não lê.** Medido em 2026-09-08 no `retencao_humano`: o hook `on_human_end → wrapup_detached_ia` traz `context.dialog_form_id` e o `skill_wrapup_detached_v1` o consome como `@ctx.hook.dialog_form_id` — funciona ponta a ponta. Já o hook `on_contact_end → nps_ia` mostra o MESMO seletor, e o `skill_nps_v1` **não referencia `@ctx.hook.*` em lugar nenhum** (o snapshot do slot `current` só lê `@ctx.core.*`): ele crava `form_id: dialog_nps_buttons` no `form_get`. ⚠️ **O bridge escreve o `context` do hook como `hook.<chave>` para QUALQUER hook** (`main.py:1826`), então escolher um form ali grava a tag no ContextStore e o NPS a **ignora em silêncio** — o operador vê o combo, salva, e nada muda. O default *"None (hook decides)"* é honesto; o valor ESCOLHIDO é que some. É a família *promessa sem mecanismo*, agravada porque aqui a promessa está numa TELA, que é onde ela é mais crível. **Dois desfechos, e é decisão de produto:** (a) o `skill_nps_v1` passa a ler `@ctx.hook.dialog_form_id` com fallback para o cravado — o seletor vira verdade e o NPS ganha o mesmo modelo do wrap-up; ou (b) a tela só oferece o seletor para pool cujo skill DECLARE que lê o parâmetro, o que exige um declarante (algo como o `config_params`, mas para `@ctx.hook.*`, que hoje não existe). ⚠️ Medir antes de escolher: quantas entradas de hook em produção têm form escolhido cujo skill não o lê — o número decide se isto é conserto ou afordância | `aberto` | medido 2026-09-08; `skill_wrapup_detached_v1.yaml:58` × snapshot de `nps_ia` |
| PRM-04 | **O formulário de deploy propõe `max_concurrent_sessions: 1` para um pool que está rodando 10 — e a promoção aceita.** Medido em 2026-09-08, com dano real: o primeiro deploy do `demo_ia` levou a capacidade de 10 para **1**, o bootstrap reagiu na hora e o pool ficou com uma instância até o deploy corretivo, 4 minutos depois. **A causa é do formulário** (`AgentFlowDeployPage.tsx:693`): o campo inicializa a partir do slot `next` — que é vazio no caso normal, porque `next` só existe entre o `set-next` e o `promote` — com fallback **`1`**. O botão *Copy from Current* conserta, mas é ação do operador: a defesa depende de alguém lembrar. ⚠️ **O número não parece errado**, e é isso que o torna caro: `1` é plausível, o formulário parece preenchido e o promote não tem por que recusar. **Duas saídas, e a escolha é de produto:** (a) inicializar do `current` quando não há `next` — o default vira *manter o que roda*, e reduzir capacidade passa a ser um ato deliberado; ou (b) o promote AVISAR quando a capacidade declarada cai em relação ao `current` (avisar, não recusar — reduzir é legítimo). A (a) é a que não depende de memória. **Gatilho:** o próximo deploy de qualquer pool com capacidade > 1 | `aberto` | medido 2026-09-08, `CHANGELOG.md` § 2026-09-08 (14) |

---

## `docs/arcos/usage-metering.md` — metering por dimensão

Movido do `CLAUDE.md` § *Pending* em 2026-09-05 (DOC-01): aquela seção era a segunda casa do que
este ledger existe para dizer, e nasceu antes dele.

| id | tarefa | estado | evidência |
|---|---|---|---|
| USG-01 | **Quatro dimensões têm função e não têm chamador** — `whatsapp_conversations`, `voice_minutes`, `sms_segments`, `email_messages` existem em `usage_emitter.py` e nenhum adapter as chama. É *promessa sem produtor*: o painel de consumo mostra zero e zero é um valor plausível. *(Separado: `llm_tokens_*` não emitido no `/v1/reason` é **defeito**, não item de direção)* | `aberto` | `CLAUDE.md` § Pending, movido em 2026-09-05 |

---

## `docs/arcos/pricing.md` — faturamento por capacidade

| id | tarefa | estado | evidência |
|---|---|---|---|
| PRC-01 | **Integração metering × pricing** — módulo que aplica planos e escreve `{tenant}:quota:limit:*`. Hoje os limites de quota são escritos na ativação de plano e **não** semeados pelo Config API; falta o elo que fecha o ciclo consumo → plano → limite | `aberto` | `CLAUDE.md` § Pending, movido em 2026-09-05 |

---

## `docs/arcos/audit-lgpd.md` — trilha e direitos do titular

Fase 1 entregue (rotas `sessions` e `mcp_calls` gateadas, trilha gravada inclusive na recusa). As
quatro abaixo são **obrigação legal com razão própria** — não dependem de nenhuma direção de
produto, e por isso não caducam com ela.

| id | tarefa | estado | evidência |
|---|---|---|---|
| AUD-01 | `original_content` desmascarado na trilha — exige endpoint batch em Core | `aberto` | `CLAUDE.md` § Pending, movido em 2026-09-05 |
| AUD-02 | Logs `user_access` — campo ABAC já declarado, sem produtor | `aberto` | idem |
| AUD-03 | Pipeline SAR / erasure (direito de acesso e de eliminação) | `aberto` | idem |
| AUD-04 | `config_snapshot` para o DPO | `aberto` | idem |

---

## `docs/arcos/quality-ingest.md` — leitor de histórico plugável

Arco A–D completo. O que resta são **concerns nomeados no fechamento**, não fases inacabadas.

| id | tarefa | estado | evidência |
|---|---|---|---|
| QIN-01 | `ReplayContext` entrega `session_meta` / `participants` / `sentiment` em **default** para contatos importados — default é valor plausível, e aqui ele viaja para dentro da avaliação | `aberto` | `CLAUDE.md` § Pending, movido em 2026-09-05 |
| QIN-02 | Correlação **por-requisição**: `pool_id` degrada se um contato vier partido entre POSTs | `aberto` | idem |

---

## `docs/adr/adr-quality-substrate-isolation.md` — isolamento do substrato por `origin`

| id | tarefa | estado | evidência |
|---|---|---|---|
| QSI-01 | **Fase 2** — partição ClickHouse `PARTITION BY (…, origin)` + `pool.origin_class`. É governança/lifecycle, não correção | `adiado` — gatilho: importação externa real com obrigação de retenção/erasure (`DROP PARTITION`) | `CLAUDE.md` § Pending, movido em 2026-09-05 |

---

## `docs/product/record-replay-harness-spec.md` — harness de gravação e replay

| id | tarefa | estado | evidência |
|---|---|---|---|
| RRH-01 | Harness de gravação/replay em todas as costuras, para regressão determinística e **gate de promoção**. Falta captura full-fidelity MCP / AI Gateway, clock e seed injetáveis, gravação seletiva. ⚠️ Segue **proposta**: o spec existe, dono não | `aberto` | `CLAUDE.md` § Pending, movido em 2026-09-05 |

---

## `docs/arcos/outbound.md` — mailing, campanha e delivery

Fases 1–5 entregues. Abaixo, refinamentos.

| id | tarefa | estado | evidência |
|---|---|---|---|
| OUT-01 | `responded` por-delivery (submit → `campaign_delivery_result`) | `aberto` | `CLAUDE.md` § Pending, movido em 2026-09-05 |
| OUT-02 | Skill de processo que auto-alimenta a mailing no `complete` — hoje o seed é direto | `aberto` | idem |
| OUT-03 | Pertença à journey via `journey_merge` | `aberto` | idem |
| OUT-04 | Pacing `look_ahead` para o discador de voz | `bloqueado` por `VOZ-01` — depende do plano de mídia | idem |

---

## `docs/arcos/customer-contact-history.md` — histórico de contatos do cliente

⚠️ **Correção paga em 2026-08-31 e preservada aqui:** o `CLAUDE.md` listava H3, HJ, H4-geral, C1a,
C1b e H5 como abertos; `CHANGELOG.md:17339` (2026-07-16) declara os seis fechados havia seis
semanas. O `TODO.md:6778` já dizia o certo — duas casas afirmando, e a errada era a que o índice
lia. É o defeito que este ledger existe para não repetir.

| id | tarefa | estado | evidência |
|---|---|---|---|
| CCH-01 | Busca full-text `GIN(tsvector)` — a busca de mensagens usa substring no ClickHouse, suficiente no volume atual. **Otimização, não correção** | `adiado` — gatilho: latência/volume medidos | `CHANGELOG.md:17339` |
| CCH-02 | **H4-survey** — origem + resultado do survey no briefing de retorno | `bloqueado` — o briefing de retorno ainda não existe | idem |

---

## `sem-demanda` — trabalho sem decisão por trás

**Contador: 4.** Balde declarado, não omissão. Se crescer, é sinal de que está entrando trabalho
sem ADR nem spec — informação de gestão, não detalhe de formato. Segue o precedente do balde
`unknown` do rollup de capacidade: publicado como tipo próprio, contado, nunca dobrado no vizinho.

| id | tarefa | status | âncora |
|---|---|---|---|
| ROT-01 | **Link direto e F5 em ROTA `/config/*` NOVA devolvem 422 — e a lista que decide isso ENVELHECE.** O nginx do `platform-ui` faz `proxy_pass` de `/config/*` para o config-api, que responde `{"loc":["query","tenant_id"]}`. ⚠️ **Corpo corrigido em 2026-09-04 por medição:** o título anterior dizia *qualquer* rota e citava `/config/masking` como quebrada — **hoje ela devolve 200 `text/html`**. Existe uma ALLOWLIST no `location` (`access|billing|platform|masking|recursos|resources|channels|canais|groups|calendars|schedules|outbound`) que cai no `index.html` quando o `Accept` é `text/html`. Medido com controle positivo: **3 de 15** rotas quebram — `agent-reports`, `context-map`, `dialog-forms` —, e são exatamente as que nasceram DEPOIS da lista. É esse o defeito real, e ele é pior que o descrito: **página nova entra quebrada em silêncio**, e só quem der F5 descobre. A decisão continua sendo dos dois lados (prefixo da UI × `location`), mas ganhou uma terceira opção que remove a classe em vez de remendá-la: **inverter para denylist** — o SPA atende `/config/*` e só os caminhos que o config-api realmente serve saem pela API. Achado ao verificar o editor JSON de DialogForm (DLG-01): um autor que favorite a própria tela recebe JSON de erro | `aberto` | medido 2026-09-04, `packages/platform-ui/Dockerfile` (nginx inline) |
| GAT-02 | **102 dos 300 scripts de `infra/test/` seguem NÃO TRIADOS — e a lista é nomeada, linha a linha, no `gates.manifest`.** É o que sobrou da triagem da GAT-01 (2026-09-04), e está separada dos ISENTOS de propósito: isenção é decisão, dívida é pergunta em aberto, e juntá-las faria a dívida herdar a tranquilidade da decisão. ⚠️ **Números atualizados em 2026-09-07:** a GAT-03 fechou as duas metades dela e levou **8** desta lista (110 → 102; AUTO 112 → 118, ISENTO 72 → 74, cobertura 63% → 66%) — entre eles TODOS os que saíam VERMELHO. O que resta é, na maioria, script **nunca rodado**: os 35 `test_*` de 2026-06, que julgam com vocabulário próprio `✅`/`❌`, mais `smoke_*` fora do critério, mais os INCONCLUSIVO e os verdes de natureza não decidida (invariante ou medição de um momento?). O trabalho é por LOTES, e cada lote só pode encolher a lista: o ramo B do `probe_gates_manifest_coverage` reprova script novo sem classe, então a dívida não cresce em silêncio | `aberto` | `infra/test/gates.manifest` § NÃO TRIADO |
| SFS-01 | **O `skill-flow-service` se declara *"E2E test harness only"* no próprio `package.json`, e é o executor de skill-flow do `docker-compose.demo.yml`.** O rótulo está na descrição do pacote (*"Thin HTTP wrapper for @plughub/skill-flow-engine — E2E test harness only"*) e o serviço roda em produção do demo, com o `persistSuspendWebhook` que a RSM-01 acabou de corrigir. Não é defeito de comportamento: é rótulo que induz a tratar como descartável um caminho que decide prazo de token e de sessão — a mesma família dos comentários que prometem invariante sem mecanismo. **Decidir qual das duas coisas é verdade** (promover o pacote para fora de `e2e-tests/`, ou declarar que o demo roda o harness de propósito) e alinhar a descrição. **Gatilho:** a próxima mudança no `skill-flow-service`, ou o primeiro pacote de produção que dependa dele | `aberto` | medido 2026-09-07, `CHANGELOG.md` § 2026-09-07 (16) |
| ORF-03 | **Re-claim do MESMO operador sem `participant_left` da primeira janela.** Uma ocorrencia, `ee2a3813` (`formfill_demo`, 2026-09-12 19:00Z, anterior ao deploy da ORF-02): no topico `conversations.participants` o humano tem `joined=2 left=1` — o produtor (bridge) omitiu o `left` da janela de 19:00:03, e o re-claim de 19:00:41 fechou normalmente. Dirigido por fixture (`probe_console_restore_after_reload.sh`, que claima com a identidade do operador REAL, junto com a reentrega da PUL-05), mas o codigo que falhou e de producao. **Nao se reproduz pelo caminho normal:** claim -> *Return to queue* -> re-claim -> submit fechou as duas janelas (`joined=2/left=2`). Linha expurgada na ORF-02; o bridge da epoca perdeu os logs no rebuild, entao nao ha como ler se o aviso `ORF-02: fechamento do segmento do CLAIMANTE NAO aconteceu` disparou. **Adiado porque** investigar sem reproducao e adivinhar. **Gatilho:** `probe_open_segments_closed_sessions.sh` VERMELHO com segmento humano `primary` cujo participante tenha `joined>left` em `probe_participant_event_in_kafka.sh <sid>` — e, antes de qualquer rebuild do bridge, guardar o log com `grep 'ORF-02'`. | `adiado` | `CHANGELOG.md` § 2026-09-12 (9) |
