# ADR — CTI Gateway multi-driver: telefonia legada como canal, sobre um modelo canônico de chamada

- **Status:** proposto
- **Data:** 2026-08-19
- **Escopo:** **modo CTI apenas** — integração da PlugHub a PABX legado por interface CTI,
  começando por **Avaya IP Office Server Edition** (CSTA nativo). Cobre o plano de
  **controle**; o plano de **mídia permanece inteiramente na central**. O modo SIP (a
  plataforma ancora a chamada) é arco próprio — ver §0 e
  [`adr-voice-media-plane.md`](adr-voice-media-plane.md).
- **Primeiro consumidor:** Avaya IP Office SE. **Alvo:** telefonia legada como estratégia de
  integração, com N drivers sobre um modelo único.
- **Emendado 2026-08-19** — §0 acrescentado; D1, D6, D8, D9, §5 e §7 ajustados. A versão
  original tratava o modo CTI como caminho único e reivindicava chegar por F2 ao cenário com
  IA na voz. Isso estava errado: ver §0.

---

## 0. Posicionamento — a fronteira é atendimento × telefonia interna

A pergunta que este ADR respondia originalmente ("como integrar ao PABX") esconde uma decisão
anterior: **até onde vai a plataforma de atendimento e onde começa a telefonia interna do
cliente.** A PlugHub não pretende ser um PABX. Isso fixa a fronteira e faz cair **dois modos**,
que não são fases um do outro — são ofertas diferentes, para clientes diferentes.

| | **Modo CTI** (este ADR) | **Modo SIP** (arco de voz própria) |
|---|---|---|
| Quem ancora a chamada | o PABX | a plataforma |
| Papel do PABX | dono da voz de atendimento | **PABX interno apenas** |
| Proposta de valor | *"sua central continua com a voz; a PlugHub acrescenta os demais canais e governa o conjunto"* | *"a voz de atendimento vem para a plataforma; a central cuida do interno"* |
| Interface com o PABX | CTI (monitorar + comandar) | tronco SIP, e **de saída** |
| Mídia | nunca sai da LAN do cliente | responsabilidade da plataforma |
| IA na voz / transcrição / gravação | **não** | sim |
| Ramal alocado no login | sim (D8) | não se aplica |
| Pré-requisito bloqueante | nenhum | plano de mídia (hoje em zero — A3) |
| Portabilidade entre PABX | matriz de drivers (D3/D4/D10) | universal — todo PABX manda SIP |

**Idêntico nos dois, e é o que justifica tratá-los como um só canal conceitual:** modelo de
sessão, `ChannelEndpoint` para DNIS→pool, admissão, roteamento, fila, transferência **para
fila**, relatórios e avaliação.

**Achado que reposicionou o arco (2026-08-19).** No modo SIP, a parte Avaya é *um tronco e um
`REFER`*. Todo o resto — terminação SIP, SFU, STT/TTS, WebRTC do agente, gravação — não tem
relação com Avaya e vale igual contra qualquer central, ou contra nenhuma. Logo o modo SIP
**não é um modo deste módulo**: é o arco de **voz própria da plataforma**, e classificá-lo como
"integração com IP Office" escondia tanto o custo (parece driver, é pilha de telecom) quanto o
valor (parece específico de um cliente, é capacidade de produto).

**Corolário sobre o tronco no modo SIP — emendado 2026-08-19.** A versão original dizia que o
enlace SIP com o PABX *"inverte de direção e encolhe de papel: deixa de ser a entrada de toda
chamada e passa a ser o caminho de handoff, consumindo canal só nas chamadas transferidas"*. Isso
descreve **uma** das duas topologias de entrada, não a única, e a preferida é a outra:

| | **A — DID direto** | **B — desvio no SBC/PABX** *(preferida)* |
|---|---|---|
| Entrada | operadora → plataforma | operadora → SBC/PABX → tronco SIP interno → plataforma |
| Papel do enlace com o PABX | só handoff de saída | **carrega 100% das chamadas de atendimento** |
| Canais consumidos na central | 1 (só nos handoffs) | **2 por chamada** (entrada + tronco interno) |
| Latência de mídia | depende do deploy | LAN, quando a plataforma é on-premise |

**A escolha é de posicionamento e implantação, não de arquitetura** — as duas terminam no mesmo
lugar dentro da plataforma, e por isso a origem da chamada segue **fora do escopo** do ADR de
mídia. O que motiva preferir B é apresentar o conjunto como **uma solução integrada dentro da
planta do cliente** (mais adoção incremental, sem renumeração, sem mexer no contrato de tronco).
O custo é o dobro de canais na central, e é ele que pode tornar A preferível num cliente
específico. Registrar aqui para que ninguém releia o corolário antigo como invariante.

**Quando escolher o modo CTI:** há agentes em deskphone; a mídia não pode sair da LAN; há
coexistência forte com o plano de discagem existente; o cliente não aceita repontar os DIDs de
atendimento.

---

## 1. Problema

O cliente tem uma central Avaya IP Office Server Edition e quer que a PlugHub: reconheça a
chegada da ligação, associe DNIS a pool, faça o contato percorrer todas as camadas normais
(admissão → roteamento → fila ou agente), permita transferir para outra fila ou ramal
acionando a central, e aloque um ramal ao agente humano no login.

Isso é uma integração pontual. O objetivo maior é outro: **transformar a integração com PABX
legado numa capacidade da plataforma**, com uma lista crescente de interfaces CTI suportadas
(Aura/AES, Cisco, Mitel, Unify, Alcatel, Asterisk, FreeSWITCH, 3CX). O risco correspondente
é construir um cliente CSTA-do-IP-Office com nome genérico e descobrir no segundo driver que
a abstração não abstrai nada.

Este ADR decide **onde fica a fronteira de extensibilidade** e o que atravessa cada camada.

---

## 2. Achados que determinam o desenho

Todos medidos no repositório, com caminho. Os três primeiros mudam o enquadramento.

| # | Achado | Evidência |
|---|---|---|
| A1 | **O canal `voice` não roda.** `handle_inbound` chama `self._open_session(...)`, `_route_inbound`, `_publish_inbound`, `_normalize_text`, `_normalize_menu_result` — os cinco **não existem** em `voice.py` nem em `ChannelAdapter`. Estão **mockados** nos testes, e é por isso que a suíte é verde. Em runtime real, toda ligação entrante levanta `AttributeError` antes de publicar qualquer coisa | `adapters/voice.py:236,247,433,558,565`; `adapters/base.py:44-77`; `tests/test_voice_adapter.py:116-121` |
| A2 | Defeitos correlatos no mesmo adapter: `channel_name` em vez de `channel`; `_collect_loop` prometido no docstring e inexistente, `stt_queue` nunca drenada (**collect por voz morto**, só DTMF); `hangup` lê chave nunca escrita; `_get_contact_id` retorna `None` por construção; `deliver_outbound` (81 linhas) nunca é invocado; `voice` não declara capability `text` | `voice.py:90, 11-13, 624-629, 657, 884, 1032-1037, 772`; `outbound_consumer.py:95-106`; `channel_capability_registry.py:32` |
| A3 | **O plano de mídia do Arc 15 nunca subiu.** Zero serviço LiveKit em compose algum, zero env `LIVEKIT_*`, SDK não é dependência do pacote; com credencial vazia o provider liga `_dev_mode` e devolve token, room, participantes e egress **placebo** | `arc15-webrtc.md:3-17`; `webrtc_provider.py:167`; `channel-gateway/pyproject.toml:6-23` |
| A4 | **DNIS → pool já existe e é config, não código.** `ChannelEndpoint` (`channel × identifier → pool_id`, unique por tenant, com `settings`, `active`, `origin`, auth por token), cliente com cache e invalidação por Kafka, UI pronta. O Zod já documenta `voice — DID / E.164` | `prisma/schema.prisma:272-317`; `schemas/src/channel-endpoint.ts:46`; `endpoint_resolver.py:102-355`; `ChannelEndpointList.tsx` |
| A5 | **Não existe conceito de recurso físico no repositório.** `ramal` aparece só como metáfora arquitetural; `auth.users` não tem coluna de telefonia; a instância humana no Redis não tem device. `voice_provider.py:119` declara `add_participant(...)` com docstring *"Add a leg (human agent SIP/WebRTC) to the conference"* — e **nunca é chamada** | `CLAUDE.md:1496`; `auth-api/db.py:22-36`; `server.ts:496-536`; `voice_provider.py:119` |
| A6 | **Transferência para agente específico é proibida por invariante escrito em três lugares** (*"o POOL é a unidade endereçável"*). O escape sancionado é o pull direcionado (`assigned_to` + `fallback_to_pool_after_s`, gate dentro de `work_task_claim`), que o próprio `CLAUDE.md` chama de *"embrião de transfer-to-agent"* | `CLAUDE.md:211-219, 1496`; `routing-engine/models.py:332`; `router.py:700-729` |
| A7 | A transferência que existe hoje é **puramente lógica**: `participant_left` → `session.closed` no pub/sub da Console → `contact_closed(agent_transfer)` → re-publish de `conversations.inbound` com o pool alvo. O bridge reconhece a continuação e **não fecha o contato**. Nada aciona central nenhuma | `server.ts:2216-2321`; `orchestrator-bridge/main.py:5863, 7374-7410` |
| A8 | O `Literal` de `ConversationInboundEvent.channel` é **fechado**: canal fora da lista é **descartado em silêncio** pelo routing | `routing-engine/models.py:47, 202, 215-218` |
| A9 | `channel` só é hard filter no caminho **legado** (`get_candidate_pools`). Com `pool_id` explícito — regime de 100% dos entry points — nada valida se o pool atende aquele canal | `registry.py:3099-3115` vs `router.py:90-92` |
| A10 | **O bridge não preserva ordem entre eventos Kafka**, nem entre tópicos nem dentro do mesmo: consumer único com `create_task` sem `await`. O que protege caminhos sensíveis hoje é offset incidental de publicação, não garantia | `conference-mechanics.md:599-620` |
| A11 | O `channel-gateway` só publica externamente pelos sete prefixos da allowlist; `/v1/*` é interno **e abriga porta anônima por construção** (`POST /v1/channels/webhook/pool/{pool_id}`, sem onde pendurar credencial). Prefixo novo sem linha na tabela é reprovado pelo probe | `webhook-patterns.md:25-83`; `infra/test/probe_edge_surface.sh` |

**Achados do ambiente do cliente (informados, a confirmar em campo — §8):** o IP Office SE
expõe CSTA nativamente pelo serviço interno `EnhTcpaService` (XML sobre TCP, tipicamente
:50797), independente do ecossistema Aura/AES, com **teto de sessões e de mensagens/s
sensivelmente menor** que o AES e sem SDK rico. O *WebRTC Gateway* do IP Office existe mas é
suportado apenas para clientes Avaya (Communicator for Web / IP Office Web Client, usuário
interno) — **não é superfície aberta para cliente SIP de terceiro no browser**.

### Consequência do conjunto

Não estamos acrescentando um segundo canal de voz ao lado de um que funciona (A1/A2), nem
podemos assumir plano de mídia pronto (A3). Em compensação, metade dos requisitos já está
implementada na plataforma como config (A4) ou como caminho default de roteamento. O
trabalho real é: **um plano de controle novo, e uma decisão explícita sobre mídia.**

---

## 3. Decisões

### D1 — No modo CTI, o PABX é o **âncora**; o CTI é o **efetuador**, nunca o árbitro

> *Emendado.* A frase original dizia "em todos os cenários". É falsa no modo SIP, onde quem
> ancora é a plataforma e não há CTI no caminho. O que segue vale para o **modo CTI**.

A chamada permanece ancorada na central em todos os cenários **deste modo**. O CTI não escolhe destino: ele
**aponta** a chamada para onde o Routing Engine decidiu. Três destinos possíveis para a mesma
chamada ancorada — perna de mídia (URA/IA), ramal do agente, conferência dos dois.

Isso preserva o invariante *"Routing Engine is the sole arbiter"* sem exceção e sem asterisco.
Se um cenário exigir que a central escolha o agente (hunt group decidindo), esse cenário
**quebra o invariante** e precisa de decisão própria — não entra por omissão.

**Corolário — estacionar é estado de mídia, não estado lógico.** Uma chamada esperando precisa
ouvir alguma coisa. Ou ela está em fila na central (não atendida, ouvindo a central) ou está
atendida numa perna da PlugHub (e a fila é lógica). Não existem os dois ao mesmo tempo, e é
isso que acopla "quem enfileira" a "quem faz a URA".

### D2 — Serviço próprio `packages/cti-gateway/`, não um adapter dentro do channel-gateway

Motivos, em ordem de peso: **(1)** é uma **fronteira anti-corrupção** — mesmo papel que o
`quality-ingest` cumpre no arco de qualidade, com o mesmo formato (serviço fino, contrato
versionado, tradução na borda); **(2)** ciclo de vida diferente — socket persistente,
reconexão, resync de monitores, nada disso cabe no modelo request/response do gateway;
**(3)** escala diferente — um por PABX/site, não por tenant; **(4)** **executa on-premise**
(D11).

O `channel-gateway` ganha um adapter magro que consome apenas o modelo canônico e não sabe
que CSTA existe.

**Nome:** o módulo é um **CTI gateway com plano de mídia opcional**. "Media gateway" descreve
o que ele *não* é no corte inicial, e o nome errado puxa o desenho para o lado errado.

### D3 — O modelo canônico é um **perfil reduzido de CSTA**

CSTA (ECMA-269) é o ancestral comum de TSAPI e JTAPI e é o protocolo **nativo** de vários
fabricantes fora da Avaya. Escolhê-lo como vocabulário interno significa que para uma fatia do
mercado o driver é quase pass-through, e que para o resto (TAPI, AMI/ARI, JTAPI) a tradução
tem um destino natural em vez de um esperanto inventado.

**Reduzido** é parte da decisão: adotamos o subconjunto observável na maioria dos switches, não
o CSTA inteiro.

```
Device { device_id, kind: extension|trunk|group|virtual, address, tenant_id, site_id }
Call   { call_id, native_ids[], direction, ani, dnis, started_at }
Leg    { leg_id, call_id, device_id, state, role, started_at, ended_at }
```

**Estados de leg:** `initiated · alerting · queued · connected · held · failed · cleared`

**Eventos (driver → canônico):** `call.arrived` · `leg.alerting` · `leg.queued` ·
`leg.connected` · `leg.held` / `leg.retrieved` · `leg.cleared` · `call.transformed`
(`kind: transferred|conferenced`, com `before[]`/`after[]`) · `device.state_changed` ·
`call.cleared`

**Comandos (canônico → driver):** `answer` · `deflect` · `transfer_single_step` ·
`transfer_consult_initiate` / `_complete` · `conference` · `hold` / `retrieve` · `make_call` ·
`clear_connection` / `clear_call` · `monitor_start` / `monitor_stop` · `hot_desk_login` /
`hot_desk_logout` · `query_user_device` · `agent_state` · `attach_call_data`

**Emenda 2026-08-19 — três comandos que faltavam, e o motivo de cada um:**

- **`clear_connection` × `clear_call` são serviços distintos**, não um só. Liberar a perna do
  agente e derrubar a chamada inteira divergem quando há consulta ou conferência em curso, e o
  encerramento **tem de existir nos dois sentidos**: o desligamento pelo aparelho chega como evento
  (é o que o agente fará por reflexo), e o encerramento pela tela precisa de comando. Assumir que o
  agente sempre desliga no telefone deixaria a plataforma sem como registrar disposição e derrubar
  na mesma ação — e sem como encerrar chamada travada por supervisão.
  **Invariante derivado:** *o EVENTO da central é a fonte da verdade sobre o fim da chamada.* O
  segmento fecha no evento, nunca no clique — senão a plataforma dá por encerrado um atendimento
  cuja chamada continua no ar.
- **`query_user_device`** — perguntar à central em qual dispositivo um usuário está conectado.
  Resolve a associação agente↔ramal **por leitura**, e não por inferência. Ver D8 emendada.
- **`route_request` / `route_select` (roteamento assistido)** — ver D12.

### D4 — **Capability é declarada por driver, e o que não existe recusa alto**

Cada driver declara o que suporta. O gateway **recusa nomeando o motivo** o que o driver não
declara; **nunca emula em silêncio**.

```
monitor_device · monitor_trunk · monitor_group · queued_event
route_request · deflect_from_alerting · deflect_from_queue
single_step_transfer · consult_transfer · conference · hold_retrieve
make_call · clear_connection · clear_call · attach_call_data
hot_desk_login · query_user_device · agent_state
```

**`route_request` ∨ `deflect_from_queue` é a capability decisiva** (D12): sem uma das duas o driver
não entrega distribuição pela plataforma, só observabilidade. Deve ser reprovado no boot com essa
palavra, não descoberto em produção.

Esta é a decisão que faz a abstração ser honesta. Um `transfer()` que numa central é atômico e
noutra é consulta+completa com janela de corrida, escondido atrás do mesmo método, é o **valor
plausível** que a § Postura de Engenharia manda caçar — e o pior tipo dele, porque a falha
aparece em produção como chamada perdida, não como exceção.

Corolário: **o `cti-gateway` valida a capability no boot**, comparando o declarado com o que o
switch responde, e loga divergência. Capability declarada e não confirmada é a mesma família de
`_dev_mode`.

### D5 — A identidade da chamada é fato do **gateway**, resolvida por componente conexa

Transferência e conferência **trocam o identificador nativo** em praticamente todo CTI. O
gateway mantém a correlação `plughub_call_id ↔ N identificadores nativos ao longo do tempo`,
alimentada pelos eventos `call.transformed`.

**Reusar o padrão que a casa já tem**: é o mesmo problema de `root_session_id` + `journey_aliases`
+ union-find (componente conexa sob aliases, raiz canônica resolvida na leitura). Não inventar
um terceiro mecanismo de correlação.

Quando o driver declara `attach_call_data` (UUI/correlator), o gateway carimba o
`plughub_call_id` na própria chamada — a correlação passa a ser afirmada pela central em vez de
deduzida, e vira a via preferencial.

### D6 — Canal próprio `pbx`, com capability de mídia **vazia**

> *Emendado.* A versão original criava `pbx` e deixava `voice` de lado como dívida. Com os dois
> modos explícitos (§0), a separação passa a ser **dois canais**, e por um motivo mais forte que
> "o `voice` está quebrado".

Canal novo `pbx`, não um sabor de `voice`. O ADR do webchat fixa que canais são permanentemente
separados quando a semântica de roteamento difere — e aqui ela difere ao **máximo**: no modo CTI
a plataforma **não transporta áudio**. Logo `pbx` declara `CHANNEL_CAPABILITIES` **sem mídia**, e
um skill que faça TTS, `notify` falado ou `menu` por voz **não pode rodar nele** — é o
`channel_capability_registry` que tem de reprovar isso na cara, não o operador que tem de saber.

Custo mecânico: acrescentar o valor ao `Literal` fechado de `ConversationInboundEvent.channel` —
sem isso o evento é **descartado em silêncio** (A8).

O molde estrutural é o `WebRTCAdapter` (providers injetáveis, stream watcher, `_publish_inbound`
próprio), não o `VoiceAdapter`.

O canal `voice` **não** é consertado aqui: ele é reconstruído no arco de voz própria
([`adr-voice-media-plane.md`](adr-voice-media-plane.md)), onde `FallbackSTTProvider` /
`FallbackTTSProvider`, Deepgram, ElevenLabs e o desenho de gravação por segmento com aviso LGPD
são reaproveitados. A1/A2 pertencem àquele arco, não a este — consertar Twilio dentro deste
misturaria dois problemas e faria o gate de um depender do outro.

### D7 — DNIS → pool por `ChannelEndpoint`; opção de URA da central = **um pool por destino**

Zero código (A4): cadastrar `channel=pbx`, `identifier=<DNIS/E.164>`, `pool_id`.

Quando a URA fica na central, a única informação que ela consegue transmitir à plataforma é
**para onde mandou a chamada** — dígitos coletados no auto-attendant não chegam pelo CTI. Logo
o modelo é: **cada opção da URA é um DNIS/grupo próprio, e cada um é um pool**. Isso é coerente
e suficiente para roteamento; o que se perde é o *conteúdo* da URA (sem transcrição, sem
contexto no ContextStore, sem esse trecho no histórico unificado nem na avaliação). Perda
consciente, não descuido — registrada aqui para não ser redescoberta.

### D8 — Ramal é **recurso escasso com semáforo próprio**, e o fato tem escopo

> *Escopo (emenda).* Vale **só no modo CTI**. No modo SIP o agente atende no browser e não há
> ramal a alocar; sobra apenas o resíduo de um agente que **também** tem ramal interno para
> chamadas internas — e isso é atributo de usuário, não alocação.

Dois fatos distintos, em lugares distintos (aplicação direta de *"never store a narrower-scope
fact in a wider-scope field"*):

- **Elegibilidade** (esta pessoa opera telefonia? em qual grupo de ramais?) é fato do
  **usuário** → `auth.users`, ao lado de `max_concurrent_sessions`, cuja trilha até o WS já
  está pavimentada de ponta a ponta.
- **Ramal alocado** é fato da **sessão de login** → hash da instância
  `{t}:instance:human-{userId}`, escrito pelo mesmo Lua que já grava `pools`/`max_concurrent`.
  Nunca em `auth.users`, que é registro estável do recurso.

**Emenda 2026-08-19 — como a associação é estabelecida.** Três formas, em ordem de preferência:
**(1) ler da central** (`query_user_device`) quando o agente já usa hot-desk — a central já sabe, e
inferir o que ela sabe é trocar certeza por heurística; **(2) o agente declara o ramal** ao entrar,
que funciona sempre e não depende de capability; **(3) ramal fixo no cadastro**, sem posição
compartilhada.

**Descartado explicitamente: descobrir o ramal pelo endereço IP da estação.** Aparelho e computador
vivem em redes distintas (VLAN de voz × de dados), então o IP da estação não indica o do telefone;
correlacionar por porta de switch ou pelo encadeamento do PC atrás do telefone exige integração com
a **infraestrutura de rede**, não com a telefonia, e quebra com endereçamento dinâmico, trabalho
remoto e troca de posição. Temos a identidade do agente; o que falta é ler a associação, não
adivinhá-la.

Alocação atômica no login, devolução no logout/desconexão com grace. Esgotamento devolve
`login_denied` com razão nova `no_extension_available`, **simétrico ao
`human_capacity_exhausted` que já existe** — mesmo formato, mesmo caminho até a Console.

**O ciclo do monitor é acoplado à alocação:** `monitor_start` no claim, `monitor_stop` na
devolução. Isso não é elegância — é o que **limita o número de monitores ativos ao número de
agentes logados**, uma ordem de grandeza abaixo do parque de ramais, e é o que mantém a
operação dentro do teto de sinalização do IP Office. A escassez de ramal e o teto de CTI são o
mesmo recurso visto de dois ângulos.

**Duas licenças, duas moedas.** Ramal e licença de agente são recursos distintos e não
fungíveis — a plataforma já recusa somar moedas diferentes na admissão. Acabar ramal com
licença sobrando (e vice-versa) é estado legítimo e deve ser **observável**, não silenciado
por um default.

### D12 — A distribuição no modo CTI usa **ponto de espera sem atendente**, e o alvo é roteamento assistido

*(Acrescentada 2026-08-19. O ADR descrevia "o PABX é o âncora, o CTI é o efetuador" sem dizer **por
qual mecanismo** a plataforma passa a escolher o agente. Sem esta decisão, D1 é slogan.)*

A distribuição sai da central para a plataforma **sem que a chamada saia da central**. Dois
mecanismos, em ordem de preferência:

**(a) Roteamento assistido — preferível.** A central emite um pedido de destino à aplicação e
**aguarda a resposta** antes de enfileirar, com prazo e destino de contingência declarados no
próprio pedido. É o `RouteRequest`/`RouteSelect` do CSTA, e é o que o Communication Manager oferece
como roteamento assistido sobre VDN. Elimina a janela de corrida e não gera tratamento de fila
desnecessário. **Capability `route_request`.**

**(b) Ponto de espera sem atendente — fallback.** Grupo dedicado na central **sem nenhum agente
conectado**; as rotas de entrada e a URA encaminham para ele; a chamada fica em espera recebendo o
tratamento da central; o gateway recebe o evento de entrada na fila, a plataforma decide, e o
gateway emite `deflect` sobre a chamada enfileirada. **Capability `deflect_from_queue`.**

**Consequências que precisam estar escritas:**

1. **A espera é estado de MÍDIA e fica na central; a decisão é lógica e fica na plataforma.** É a
   aplicação direta do corolário de D1, e é o que permite a mesma fila governar voz e canais
   digitais sem mover mídia.
2. **O transbordo por tempo do ponto de espera é o plano de contingência**, e é configuração da
   central, não código nosso. Plataforma indisponível ou lenta ⇒ a chamada segue o caminho
   convencional e é atendida. **Sem esse transbordo o arranjo não é seguro e não deve ir a
   produção.**
3. **Sem `route_request` nem `deflect_from_queue`, o modo CTI não entrega distribuição pela
   plataforma** — vira observabilidade. É a capability mais decisiva da matriz, e o driver que não
   declarar nenhuma das duas deve dizê-lo alto no boot, não descobrir em produção.
4. Em (b), a chamada **não é atendida** enquanto espera, salvo se o tratamento da central exigir
   atendimento para tocar anúncio. Muda supervisão de atendimento e tarifação — verificar por
   central.
5. **A PAUSA NÃO É DEPENDÊNCIA DA DISTRIBUIÇÃO.** Como o árbitro é a plataforma, basta que *ela*
   saiba que o agente está pausado para não escolhê-lo — o PABX não precisa participar. O estado de
   indisponibilidade lido da central (`agent_state`, DND) compra outra coisa, mais estreita:
   **evitar entrega que falha** no agente que se ausentou pelo aparelho sem avisar a plataforma.
   Onde o driver não expuser isso, a contenção é **entrega recusada/não atendida devolve ao ponto
   de espera e marca o agente indisponível**. Registrado porque é candidato natural a virar bloqueio
   inventado numa releitura futura: não é.
6. **Corolário de rotina.** A pausa é declarada na superfície que o agente já tem aberta; usar
   *logout* como mecanismo de pausa é recusado — em posição compartilhada obriga a refazer o login
   do aparelho a cada intervalo, que é justamente alterar a rotina que este modo existe para
   preservar. Motivo de pausa, quando a central só der estado binário, é fato da PLATAFORMA
   (oferecido sem bloquear; ausente ⇒ `não classificada`, nunca uma categoria inventada).

### D9 — Transferência: a decisão é da plataforma, a execução é do CTI

- **Para outra fila** → re-publish de `conversations.inbound` com o `pool_id` alvo, exatamente
  o caminho que já existe (A7). O gateway apenas **reage à nova alocação** com o comando
  apropriado (`transfer_single_step` ou `deflect`, conforme capability).
- **Para agente específico** → item de fila com `assigned_to` (pull direcionado, A6). Não é
  destino de roteamento, é elegibilidade de claim — o invariante fica intacto e o requisito
  é atendido, com fallback para a fila por lease de graça.

O CTI **nunca** escolhe destino. Ele executa e reporta.

**Emenda — "para ramal" significa coisas diferentes nos dois modos, e a diferença não é de
implementação, é de domínio:**

| | Modo CTI | Modo SIP |
|---|---|---|
| Ramal de **agente PlugHub** | comando na central; o **contato continua**, novo segmento | não existe (o agente está no browser) |
| Ramal **interno não-agente** (financeiro, gerente) | comando na central; sai da governança da plataforma | `REFER` para o PABX; a plataforma **entrega e sai** |

No modo SIP, transferir para um ramal interno é **saída do contato, não continuação de
segmento** — quem recebe não é agente da plataforma. Fecha com `close_reason` próprio.
Permanecer na chamada por bridge só se justifica para continuar gravando, e aí é decisão
consciente com custo de canal, nunca default.

### D10 — Nenhum driver entra na matriz de suporte sem **traço gravado**

Um driver de PABX só se valida contra o PABX; não há CI que substitua um CUCM. Uma tabela de
"interfaces suportadas" sem gate é uma afirmação de produto que o código não sustenta — a mesma
família do `_dev_mode` do LiveKit: parece pronto, ninguém fica vermelho.

O gate é o **Record/Replay Harness** (hoje em `## Pending`): gravar a sessão CTI real contra o
switch de homologação e reproduzi-la como regressão determinística. Com ele, *"suportado"*
significa *"existe traço gravado e verde"*. Sem ele, significa *"alguém integrou uma vez"*.

**F0 já nasce gravando.** O harness deixa de ser backlog e vira infraestrutura deste arco.

### D11 — O `cti-gateway` executa **on-premise**, com conexão de saída e porta autenticada

CSTA é TCP na LAN do cliente. O gateway é, portanto, um **artefato entregável no cliente**, com
versionamento, upgrade e suporte próprios — o primeiro pacote do repositório com essa natureza.

Ele **não publica em Kafka diretamente** (não se coloca broker na LAN do cliente nem se expõe o
broker) e **não usa `POST /v1/channels/webhook/pool/{pool_id}`**, que é anônima por construção e
mora em prefixo interno (A11). Ele fala com o `channel-gateway` por uma **porta externa própria,
autenticada por token**, que precisa de **linha nova na tabela de exposição de borda** —
`probe_edge_surface.sh` reprova prefixo sem classificação.

Consequência de deploy que pesa na escolha de mídia: **sem plano de mídia, o áudio nunca sai da
LAN do cliente** — sem SBC, sem RTP na WAN, sem gravação atravessando fronteira. Com plano de
mídia, ou o SFU vai para o cliente ou o RTP vai para a nuvem. Ver §7.

---

## 4. O que este desenho entrega, por requisito

| Requisito | Como | Net-new? |
|---|---|---|
| 1. Identificar chegada da ligação | `call.arrived` do monitor de tronco/DNIS | driver |
| 2. Canal padrão adicional | canal `pbx` + adapter magro sobre o modelo canônico | adapter |
| 3. DNIS → pool | `ChannelEndpoint` (A4) | **nenhum** |
| 4. Todas as camadas até fila ou agente | `ConversationInboundEvent` com `pool_id` → admissão → routing → fila/alocação | **nenhum** |
| 5. Transferência para fila ou ramal | D9 — decisão na plataforma, execução no CTI | comando |
| 6. Ramal alocado no login | D8 — semáforo próprio, monitor acoplado | recurso novo |

---

## 5. Fases

**F0 — núcleo + driver IP Office, sem plano de mídia.** Modelo canônico; `cti-gateway` com
driver CSTA-IPO; correlação de identidade; capability declarada e verificada no boot; canal
`pbx` + adapter; `ChannelEndpoint` por DNIS; pool de ramais com hot-desk; transferência.
Gravação de traço desde o primeiro dia. **Entrega os seis requisitos com a mídia inteiramente
dentro da central.**

**F1 — segundo driver, com o IP Office congelado.** É o segundo driver que prova se o modelo
canônico é abstração ou se é o CSTA-IPO com outro nome. Candidato: algo **estruturalmente
diferente** de CSTA — Asterisk AMI/ARI é barato de montar em lab e é o teste mais duro.
Sobrevivendo a ele, sobrevive ao resto. *Nenhum driver novo antes deste.*

**F2 — drivers adicionais** conforme demanda comercial, cada um com traço (D10).

> *Emenda.* A F2 original era "plano de mídia (SFU + SIP entre central e SFU)". Ela **saiu deste
> ADR**: com §0, plano de mídia não é a continuação do modo CTI — é o arco de voz própria, que
> não depende de PABX nenhum. Ver [`adr-voice-media-plane.md`](adr-voice-media-plane.md). Este
> arco termina em F2 e **nunca** entrega IA na voz; quem quiser isso está pedindo o outro modo.

---

## 6. Invariantes do módulo

1. **O CTI nunca decide destino.** Roteamento é do Routing Engine; o driver executa e reporta.
2. **Capability não declarada recusa alto.** Nenhuma emulação silenciosa de comando ausente.
3. **Nenhum driver na matriz de suporte sem traço gravado.**
4. **O driver não conhece PlugHub; o adapter não conhece CSTA.** A tradução acontece uma vez, no
   modelo canônico, e em nenhum outro lugar.
5. **A identidade da chamada é do gateway**, por componente conexa sob aliases — nunca o
   identificador nativo cru atravessando a fronteira.
6. **Ramal alocado é fato da sessão de login**, não do usuário; elegibilidade é fato do usuário,
   não da sessão.
7. **Monitor ativo ⟺ ramal alocado.** Monitor sem alocação é vazamento e denuncia escritor fora
   do caminho de claim.
8. **O gateway não publica em Kafka nem usa porta anônima.** Porta externa própria, autenticada,
   classificada na tabela de borda.

---

## 7. Fora de escopo (decisões próprias, não bloqueiam F0/F1)

> *Emenda 2026-08-19.* O item "on-prem × nuvem para o plano de mídia" **saiu daqui**: migrou
> para [`adr-voice-media-plane.md`](adr-voice-media-plane.md), onde é a primeira decisão e não
> uma nota de rodapé. E lá ela foi **resolvida**: o plano de mídia não tem topologia própria —
> acompanha o deploy da plataforma.

- **Todo o modo SIP / plano de mídia** — arco próprio (§0).
- **Consertar o canal `voice` (Twilio)** — A1/A2, pertencem ao arco de voz própria.
- **Discador / pacing `look_ahead`** — desenho próprio (`dialer-compliance-invariants.md`,
  `outbound.md`); depende do arco de voz, não deste.
- **Gravação pela plataforma** — não existe no modo CTI: a plataforma não toca o áudio. Gravação
  aqui é da central, e integrá-la (SIPREC, Media Manager) é decisão futura e separada.
- **IA por voz** — **nunca** neste arco, por construção (D6: capability de mídia vazia).

---

## 8. O que medir antes de F0

Nenhum destes é opinável, e cada um muda desenho — não implementação.

1. **O `EnhTcpaService` aceita mais de um cliente simultâneo?** O one-X Portal pode já estar
   ocupando a interface. Se for exclusiva, a topologia muda.
2. **Perfil CSTA: Phase II ou III?** Determina se há `SingleStepTransfer` ou apenas
   consulta+completa — e isso é o desenho da transferência, não um detalhe dela.
3. **Como a plataforma passa a escolher o agente (D12)** — duas perguntas, nesta ordem:
   **(a)** existe **roteamento assistido** (a central consulta a aplicação e aguarda, com prazo e
   contingência)? **(b)** se não, `DeflectCall` sobre chamada **enfileirada em grupo** é aceito?
   Resposta negativa às duas significa que o modo CTI não entrega distribuição pela plataforma —
   é a medição que mais pode mudar o escopo do arco.
4. **Hot-desk: comandar e consultar são duas perguntas.** *(a)* login/logout é comandável por
   CSTA ou só por discagem de short code (`*90`, `ExtnLogin`)? *(b)* **é possível CONSULTAR em qual
   dispositivo um usuário está conectado** (`query_user_device`)? A (b) é a que mais importa: com
   ela a associação agente↔ramal é lida da central e o agente não informa nada (D8 emendada).
5. **Teto de mensagens/s e de monitores simultâneos** nesta versão. Dimensiona o pool de ramais
   e confirma ou derruba o acoplamento monitor⟺alocação.

**Postura:** medir com `MonitorStart` num ramal de teste e dump do XML **antes** de qualquer
linha de código de driver. Um ambiente que só funciona porque alguém já o configurou não está
sendo verificado — está sendo lembrado.

---

## 9. Riscos

| Risco | Mitigação |
|---|---|
| O modelo canônico ser CSTA-IPO disfarçado | F1 obrigatória antes de qualquer driver adicional, e com driver estruturalmente diferente |
| Teto de sinalização do IP Office estourar em produção | Monitor acoplado à alocação (D8); medição §8.5 antes de F0 |
| Matriz de suporte virar afirmação sem lastro | D10 — traço gravado como condição de entrada |
| Correlação de chamada perder o fio numa transferência | `attach_call_data` como via preferencial quando disponível; union-find como fallback; traço cobrindo transferência e conferência |
| Concorrência de eventos na fronteira | O bridge não preserva ordem entre eventos Kafka (A10) — o gateway não pode assumir ordenação, e o modelo canônico deve ser idempotente por `(call_id, leg_id, state)` |
| Gateway on-prem desatualizado no cliente | Versionamento do contrato canônico + rejeição explícita de versão incompatível na porta externa |
