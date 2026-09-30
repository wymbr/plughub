# Revisão do ADR A2A servidor — identidade do cliente, agentes de automação, anexos e mídia

- **Status:** **incorporada ao ADR em 2026-09-30 (revisão 2).** Este documento fica como registro da medição e das alternativas; as decisões moram no ADR
- **Data:** 2026-09-30
- **Alvo:** [`docs/adr/adr-a2a-server-binding.md`](../adr/adr-a2a-server-binding.md) (proposto, 2026-08-13)
- **Insumo:** conversa externa com Gemini sobre A2A/OpenClaw, anexos e LGPD. Cada afirmação dela
  usada aqui foi conferida contra a spec, a documentação do OpenClaw ou o código; as que não se
  sustentaram estão no § 7.

---

## 0. Veredicto

**O ADR precisa de revisão.** O que ele decide continua certo: binding e não motor, `Task` = sessão,
card como projeção, tenant vindo da credencial e mascaramento sem opção. Mas ele foi escrito para
**um** tipo de chamador (o sistema de um parceiro ou do próprio tenant, B2B), e as quatro perguntas
desta rodada caem justamente fora desse caso:

| Assunto | O que o ADR tem | Lacuna |
|---|---|---|
| Identidade do cliente (OIDC/social) | nada; D6 é credencial do **software** chamador | não distingue *quem chama* de *em nome de quem*. O `TASK_STATE_AUTH_REQUIRED` da spec v1.0 não é usado |
| Agente de automação (OpenClaw) | "caller" B2B com credencial emitida pelo tenant | o agente **pessoal** do consumidor não tem quem lhe emita credencial: seria o cadastro pelo admin outra vez |
| Anexos e LGPD | `D8` (masking) e mais nada | a governança de anexo que existe hoje não aguenta um anexo vindo de fora (§ 4) |
| Mídia | nenhuma decisão sobre `Part`/`inputModes` | o card não tem como declarar o que o pool consome |

Somam-se três fatos que envelheceram (§ 1) e um conflito que o ADR não viu: **o caminho webhook
proíbe `menu`/`notify`**, então um agente conversacional não pode ser exposto pelo binding como está
desenhado (§ 3.3).

---

## 1. O que envelheceu desde 13/08 (medido hoje)

| Afirmação do ADR | Estado em 2026-09-30 |
|---|---|
| D11: graça de espera de ~5 s dentro do binding | **substituída** pelo D6 de [`adr-pool-no-resource-policy.md`](../adr/adr-pool-no-resource-policy.md) (também proposto). O ADR ainda carrega o D11 inteiro, e hoje há duas casas para a mesma decisão |
| §7: "v1.0 abr/2026, Linux Foundation" | o A2A **entrou na Agentic AI Foundation** em 27/08/2026 ([anúncio](https://a2a-protocol.org/latest/blog/2026/08/27/a-new-chapter-for-a2a-joining-the-agentic-ai-foundation/)). A `competitive-analysis-2026-09.md` diz o contrário ("projeto próprio da LF, não da AAIF") e precisa de correção junto |
| D5: `get_status` responde `closed` para chave ausente | **continua verdade** (`webhook.py:1812-1814`); a rota segue sem credencial e toma o tenant da query |
| D7: `tenant_id` vem do corpo | **continua verdade** (`main.py:1538`). A WHK-01 acrescentou a checagem de que o pool existe, mas nenhuma credencial de chamador |
| D5: o `complete` não persiste resultado | **continua verdade** (`complete.ts` só sinaliza `agent_done`) |
| A0: descritor no pool | não começou: não há bloco `a2a` nem `on_no_resource` em schema algum |

A spec v1.0 tem dois recursos que o ADR não usa e que resolvem metade desta revisão:

- `TASK_STATE_AUTH_REQUIRED`, estado **interrompido** (não terminal): "preciso de autenticação para seguir";
- `securitySchemes` no card (`APIKey`, `HTTPAuth`, `OAuth2` com os fluxos authorization code, client
  credentials e device code, `OpenIdConnect`, `MutualTls`), mais o **card estendido** servido só
  depois de o cliente se autenticar.

---

## 2. Identidade do cliente por OIDC/OAuth 2.0 (Google, Apple, Microsoft, IdP do tenant)

### 2.1 Como é hoje (medido)

Não existe login de cliente nem autocadastro. O cliente nasce por três caminhos
(`channel-gateway/identity/index.py`):

| Caminho | Procedência | Nível |
|---|---|---|
| primeiro contato pelo canal | `channel_origin` / `declared` | `claimed` |
| operador no Console (IDN-08) | `operator` | `claimed` |
| importação pelo admin (PID-12, até 1000 linhas) | **`authoritative`**, e é o **único** caminho para ela | — |

A prova de posse (`possessed`) só existe em telefone e e-mail, e só por OTP (IDN-13) ou pela chegada
via WhatsApp do telefone autoritativo (PID-09). O `grep` por `oidc|openid|jwks|id_token|oauth` não acha
nada, nem para cliente nem para operador. A `PID-21` (aberta) registra que *"o `sub` de login federado
do tenant não tem produtor"*.

Ou seja: "o cadastro é do admin" vale para o cadastro **autoritativo**. O cadastro declarado já nasce
sozinho; o que falta é um jeito de o cliente **provar** quem é sem depender de um OTP entregue num
canal que o tenant já conhece.

### 2.2 Proposta — prova federada como **novo método de prova**, não como novo cadastro

O ponto central: **login social prova posse de uma conta num provedor, não identidade civil.** O Google
não devolve CPF, e o Facebook nem sempre devolve e-mail. Tratar o login social como "cadastro seguro"
seria o valor plausível da Postura de Engenharia: parece identidade e não é. Por isso:

1. **Chave nova de identidade: `oidc:{iss}|{sub}`.** É estável e não é PII legível; vai para
   `customer_secondary_keys` com hash, como as outras.
2. **O e-mail só vira `possessed` se `email_verified=true`** e o provedor constar da lista do tenant.
   Sem isso ele entra como `claimed`, igual a um e-mail digitado.
3. **Procedência nova: `federated`, com o emissor registrado.** Ela **não** vira `authoritative` por
   default. A exceção é **o IdP do próprio tenant** (o portal ou app do banco ou da telco, em
   Keycloak, Auth0 ou Entra External ID): o tenant declara aquele `iss` como *sistema de registro*, e
   então `(iss, sub)` **é** um `external_ref` (`IDN-01`) e liga ao cadastro importado. **É esse o
   produtor que a PID-21 procura.**
4. **Provedores, em ordem de valor:**
   - o IdP do tenant (vincula ao cadastro autoritativo);
   - Google, Apple e Microsoft (OIDC completo, `email_verified` confiável);
   - Facebook por último. O Meta Login é OAuth 2.0 com OIDC só parcial ("Limited Login"), o e-mail é
     opcional, e para o PlugHub ele serve de pouco além do WhatsApp que já existe.
   - O gov.br dá CPF com nível de confiança, mas o acesso para entidade privada é restrito. Entra só se
     o tenant tiver o convênio.
5. **A plataforma é Relying Party por tenant.** O fluxo é authorization code + PKCE, com `state` e
   `nonce`, e confere `iss`, `aud`, `exp` e `nonce` pelo JWKS. Nunca fluxo implícito, e nunca aceitar
   `id_token` recebido no corpo de uma chamada. Um token emitido para outra `aud` e repassado por um
   agente externo é o ataque de substituição, e a conferência do `aud` é o que o barra.
6. **Configuração segue o invariante.** Emissor, `client_id`, escopos, se o emissor é autoritativo e
   a lista de redirect ficam no config-api (namespace novo, editável na tela). O `client_secret` é
   segredo, logo não vai para o config-api. Decisão aberta: o padrão `llm_accounts` (segredo em env,
   por convenção de nome) não escala para N tenants × M provedores, e o precedente
   `{tenant}:config:webchat:jwt_secret` guarda o segredo no Redis. Recomendo cofre de segredos por
   tenant e registrar a dívida, sem inventar um terceiro padrão.
7. **LGPD:**
   - escopos mínimos (`openid email`, e `profile` só se o tenant justificar);
   - **descartar** access e refresh token depois de validar o `id_token`: a plataforma não age na
     conta do cliente, então não guarda o token;
   - o consentimento é registrado como fato da sessão;
   - `(iss, sub)` e o e-mail entram no dossiê (AUD-03) e na eliminação (AUD-06), que já percorrem
     `customer_secondary_keys`.
8. **Um mecanismo, vários canais.** A prova federada é registrada por sessão e por cliente, no mesmo
   lugar da prova por OTP (modelo PID). Chegam a ela:
   - o **webchat**, com botão de login no widget;
   - o **WhatsApp/SMS**, por link;
   - o **A2A**, por `AUTH_REQUIRED` com o link (§ 3.2).

   Um skill que hoje pede OTP passa a pedir "prova de nível X", e o método é escolha do tenant.

> **Operador por SSO é outro assunto.** Login de operador pelo IdP corporativo (Entra, Google
> Workspace) é pedido real de enterprise, mas mora no auth-api e não tem relação com o cadastro de
> cliente. Recomendo ficha própria e não misturar os dois nesta revisão.

---

## 3. Agentes de automação (OpenClaw e afins) chamando os agentes internos

### 3.1 O que o OpenClaw realmente faz (medido na documentação dele, não na conversa)

[`docs.openclaw.ai/channels/a2a`](https://docs.openclaw.ai/channels/a2a):

- funciona como **servidor e como cliente**; para nos chamar, usa `peers.<nome>.url` + `outboundToken`;
- **só bearer token.** Não há OAuth. A conversa com o Gemini disse "Bearer/OAuth": não confere;
- **só texto e `data` (JSON).** Diz a documentação: *"File URL and raw binary parts are ignored."*;
- **só polling**, sem streaming e sem push. Ou bloqueia (`replyTimeoutMs`, no máximo 600 s), ou usa
  `returnImmediately: true` + `GetTask`;
- `CancelTask` é recusado.

**Consequências para o roteiro do ADR:**

- **A5 (SSE) deixa de ser pré-requisito para este cliente.** O ADR o promoveu a pré-requisito de pool
  humano por causa do blocking-por-padrão, mas o OpenClaw sai do blocking com `returnImmediately` e
  polling. O A4 completo (`returnImmediately` + `tasks/get`) já atende, e o A5 volta a ser otimização.
- **O artefato é `DataPart`.** O `output_schema` do descritor (D3) vira exatamente a `data` que o
  OpenClaw consome, e o A3 continua sendo o net-new.
- **Anexo de ida ou de volta não trafega com esse cliente.** Isso reduz a pressão sobre o § 4, mas não
  a elimina: outros clientes A2A enviam arquivo.

### 3.2 A lacuna que o ADR não cobre — dois principais numa chamada

O D6 modela **um** principal: o `a2a_client`, emitido pelo tenant, com `allowed_pools`. Serve para o
ERP do tenant ou o orquestrador de um parceiro. O OpenClaw é outra coisa: é o agente **pessoal de um
consumidor**, agindo **em nome dele**. Nesse caso há dois fatos distintos, e juntá-los num campo só é o
corolário do Arc 7 (*campo cujo rótulo tem "e" são dois fatos*):

| Fato | Pergunta | Onde mora |
|---|---|---|
| **principal chamador** | que software está chamando, com que cota e para quais pools? | credencial |
| **titular** | em nome de qual cliente, e com que nível de prova? | identidade do cliente (§ 2), fixada na **sessão** |

**Proposta D6-revisada:**

1. **Uma casa para o principal de máquina.** A [`agent-principal-identity-spec.md`](agent-principal-identity-spec.md)
   já propõe `auth.agent_principals` com `origin: external` e client credentials
   (`POST /v1/agent/token`). Criar também um `a2a_client` seriam duas entidades para "software de fora
   com credencial". O `a2a_client` deve **ser** um `agent_principal` de origem externa. Ou então o
   ADR decide explicitamente por que não é, e registra isso.
2. **Dois tipos de principal externo:**
   - `partner`: emitido pelo admin do tenant. `allowed_pools` explícito e sem titular fixo; o titular
     é declarado por chamada e **sempre `claimed`**.
   - `customer_agent`: **emitido ao próprio cliente** depois de uma prova (OTP ou federada, § 2). O
     cliente faz login no portal ou widget do tenant, gera um token pessoal, revogável, com validade
     curta e cota baixa, e cola esse token no `outboundToken` do OpenClaw. O titular vem **do token**,
     com o nível de prova da emissão e a idade dela. É isso que resolve o "cadastro pelo admin": a
     credencial nasce de autosserviço com prova, sem ninguém do tenant no meio.
   - Para quem suporta OAuth (não é o caso do OpenClaw hoje), o mesmo `customer_agent` sai por
     **authorization code + PKCE** ou **device code**, declarados em `securitySchemes`. É o mesmo
     principal por uma porta padrão.
3. **`AUTH_REQUIRED` é a prova dentro da tarefa.** Quando o skill exige um nível de prova que a
   sessão não tem, a task vai para `TASK_STATE_AUTH_REQUIRED` com uma `Message` contendo um link de
   prova (OTP ou OIDC). O humano abre o link no navegador, o agente faz polling, e a sessão sobe de
   nível. O agente **nunca** recebe o código OTP nem o `id_token`, e esta é a quarta costura do Dialog
   Primitive (o segredo nunca passa pela mão de um agente) valendo para agente de fora.
4. **Identidade declarada numa `Part` é sempre `claimed`.** "Sou o CPF X", dito por um agente, pesa o
   mesmo que um cliente digitando, e nunca vale mais que isso.
5. **A cota por principal volta.** O `adr-pool-no-resource-policy` dispensou a cota por `a2a_client`
   *"até aparecer um segundo caller disputando o mesmo pool"*. No B2C isso acontece **por construção**
   (N consumidores, um pool), e o gatilho de reabertura está atendido antes do primeiro deploy.

### 3.3 Conflito com o perfil `workflow` — dois bindings, não um

O ADR mapeia o A2A sobre o caminho webhook-pool. Pelo Arc 19, pool `channel_types: [webhook]` roda o
**perfil `workflow`**, que **proíbe `menu`, `notify` e `begin/end_transaction`**, e desde a CTR-01 isso
é recusado no deploy. Resultado: **nenhum agente conversacional** (triagem, retenção, segunda via, que
são exatamente os que um agente pessoal quer chamar) pode ser exposto pelo binding como está. Só
workflows de tarefa única (entrada → artefato).

Então são dois bindings:

| | **Binding de tarefa** (o ADR atual) | **Binding conversacional** (novo) |
|---|---|---|
| alvo | pool webhook, perfil `workflow` | pool de contato, perfil `agent` |
| chamador típico | ERP, orquestrador do tenant | agente pessoal do consumidor |
| `menu` | não existe | `INPUT_REQUIRED` + `DataPart` com as opções e o JSON Schema do form (renderização no **adapter**, invariante do channel-gateway) |
| `notify` | não existe | `Message` na task |
| bloco mascarado | não existe | **não trafega.** `AUTH_REQUIRED`/`INPUT_REQUIRED` com link fora de banda (o do survey web). Um agente de terceiro não é portador de dado de cartão |
| humano | fora | é aqui que "fila de pessoas via A2A" (§ 8 do ADR) passa a ter onde morar |

**Decisão que precisa ser reaberta.** O § 8 do ADR adiantou *"reusa `webchat`, não cria canal a2a"*.
Medido contra o invariante de renderização, **recomendo `channel: a2a` (medium `message`) para o
binding conversacional**, pelos seguintes motivos:

- traduzir `menu` para `DataPart` é lógica de renderização por canal, que por invariante mora num
  adapter do channel-gateway;
- o `channel` como filtro duro passa a fazer o opt-in do D3 de graça: pool que não lista `a2a` não
  atende agente de fora;
- reusar `webchat` faria todo pool de webchat atender agente de fora sem ter escolhido isso.

O D1 continua valendo para o binding de tarefa, onde *quem chamou* é mesmo fato de credencial. A
premissa do D1 é que canal não é credencial, e isso segue verdadeiro. Mas canal **é** capacidade de
renderização, e o binding conversacional precisa de uma.

---

## 4. Anexos, visualização e LGPD — medido, e é pré-requisito independente do A2A

### 4.1 Estado atual (2026-09-30)

| Aspecto | Hoje | Onde |
|---|---|---|
| Porta de leitura | `GET /webchat/v1/attachments/{file_id}` **sem autenticação**: o `file_id` é a credencial | `upload_router.py:119-124` |
| Cabeçalhos | `Content-Disposition: inline; filename="{original_name}"` com o nome **sem escape**, sem `X-Content-Type-Options: nosniff`, sem CSP | `upload_router.py:155-162` |
| Auditoria de acesso | **nenhuma.** Só a gravação (VOZ-36) escreve em `audit.access` | `recording_router.py` |
| Allowlist e limite | só no upload do webchat. **WhatsApp e e-mail pulam `validate_mime`**: MIME e tamanho vêm do provedor | `whatsapp.py:314-365`, `email.py:333` |
| Magic bytes | fail-open para tipo sem assinatura | `attachment_store.py:151-155` |
| Antivírus, EXIF, moderação | inexistentes | — |
| Retenção | `webchat.attachment_expiry_days` (30), **fora** do namespace `retention` (AUD-07/08); aplica-se também a anexo de WhatsApp | `webchat_config.py:34` |
| Dossiê e eliminação | cobrem (metadado no AUD-03; `[erased]` + purge no AUD-06) | `attachment_store.py:288-350` |
| Console, avaliador, replay | **não renderizam anexo**: o operador não vê a imagem que o cliente mandou | `modules/agent-assist` |
| IA | só texto (`content: str`) | `ai-gateway/models.py` |

A soma dá um risco que existe **hoje**, sem A2A nenhum. Um documento de WhatsApp com MIME `text/html`
declarado pelo remetente é gravado sem allowlist e servido `inline`, com esse MIME, por uma porta
pública na origem do gateway. Por isso esta parte é pré-requisito e deve andar **antes** e **fora** do
arco A2A.

### 4.2 Proposta — governança de anexo como arco próprio (ATT)

1. **Uma esteira de ingestão para todos os canais** (webchat, WhatsApp, e-mail, A2A), com estas etapas:
   - allowlist e tamanho por classe;
   - magic bytes **fail-closed**;
   - imagem **re-codificada**, o que remove EXIF/GPS pelo mesmo mecanismo que neutraliza polyglot;
   - antivírus (ClamAV como serviço) com estado `quarantined → clean | rejected`;
   - sha256 no metadado.

   O anexo **só aparece para alguém depois de `clean`**. "Gravado" não é o mesmo que "pronto".
2. **Duas portas, como na gravação:**
   - **Pública**, para o próprio cliente: URL **assinada, curta e presa à sessão** no lugar do
     `file_id` nu.
   - **Interna**, com Bearer + capacidade nova (`contacts.attachments`: `read_only` visualiza,
     `read_write` baixa), decidida pelo pool que **atendeu**, como na VOZ-36. Toda visualização,
     download e recusa vai para `audit.access`.
3. **Cabeçalhos:**
   - `nosniff`;
   - `Content-Security-Policy: sandbox; default-src 'none'`;
   - `filename*` pela RFC 5987;
   - `inline` só para imagem da allowlist. PDF vai como `attachment`, ou é renderizado no Console
     por pdf.js em iframe `sandbox` servido de **origem separada**.
4. **Visualização por papel** (sem prometer o que não se sabe fazer):
   - operador que atende vê o anexo;
   - supervisor, avaliador e replay recebem o anexo **borrado**, com "revelar" explícito e
     **auditado**;
   - a tarja automática de PII sobre imagem por OCR, que a conversa com o Gemini apresenta como
     trivial, fica como fase posterior e medida, nunca como garantia de v1.
5. **Retenção é classe do namespace `retention`** (`attachment_days`, e opcionalmente
   `attachment_sensitive_days`). O `webchat.attachment_expiry_days` sai, para existir uma casa só, e
   segue a regra da AUD-08: classe nova entra junto com o expurgo dela. **Não existe prazo legal de
   "7 a 30 dias" na LGPD**: o prazo é finalidade declarada pelo controlador (o tenant), e por isso
   fica configurável por tenant.
6. **Anexo vindo por A2A** (quando o binding aceitar `url`/`raw`):
   - download só pela plataforma, por proxy de egresso: somente `https`, sem IP privado, sem redirect
     para rede interna, com teto de tamanho e de tempo;
   - depois passa pela mesma esteira do item 1;
   - a URL externa **nunca** chega ao Console nem a um LLM;
   - `raw` inline tem teto baixo.
7. **Anexo de saída por A2A:** o que os agentes da plataforma **produzem** (segunda via, comprovante)
   sai como URL assinada curta. Anexo que **o cliente enviou** nunca volta por A2A (D8).

---

## 5. Mídias no A2A pensando em automação

| `Part` (v1.0) | Uso proposto | v1 |
|---|---|---|
| `text` | fala do agente, `notify` | sim |
| `data` (JSON) | artefato do `output_schema`; `menu`/form como opções + JSON Schema; posição de fila | **sim, e é o principal**: é o que o OpenClaw consome |
| `url` / `raw` + `mediaType` + `filename` | anexo de ida e de volta | **só depois do arco ATT** |
| voz ou vídeo em tempo real | — | **fora.** A spec não abre sessão WebRTC paralela (§ 7). Se um dia houver, será extensão com link de sala LiveKit, e não é caso de agente de automação |

**Os modos do card são derivados do pool, nunca declarados à mão** (D2 aplicado à mídia). Um pool
`agent_kind: ai` consome `text/plain` e `application/json`, porque o ai-gateway é só texto. Imagem e PDF
só aparecem nos `inputModes` de pool que tenha humano **e** Console que renderize anexo. Declarar
`image/*` num pool de IA seria o card prometendo um consumo que ninguém faz: o mesmo defeito que a
VOZ-09 fechou para mídia de chamada ("mídia é fato de quem consome").

Tornar o ai-gateway multimodal é assunto próprio (custo, conta e modelo por pool) e não bloqueia o A2A.

---

## 6. Revisões propostas no ADR (checklist)

1. **Contexto.** Data da revisão; spec v1.0 com `AUTH_REQUIRED` e `securitySchemes`; AAIF (§ 7 do ADR
   e a `competitive-analysis-2026-09.md`).
2. **D11.** Remover e apontar para o D6 do `adr-pool-no-resource-policy`, deixando uma casa.
3. **D6.** Separar *principal chamador* de *titular*; convergir `a2a_client` com `agent_principals`;
   dois tipos, `partner` e `customer_agent`; `securitySchemes` publicados no card.
4. **D9.** Cota por principal **volta**, porque o B2C atende o gatilho do `no-resource-policy`.
5. **D12 (novo), prova do titular.** `AUTH_REQUIRED` com link fora de banda; métodos OTP e federado;
   identidade em `Part` é sempre `claimed`; o segredo nunca passa pelo agente.
6. **D13 (novo), dois bindings.** Tarefa (webhook, `workflow`) e conversacional (contato, `agent`);
   reabrir "reusa webchat" com a recomendação `channel: a2a`; bloco mascarado nunca trafega.
7. **D14 (novo), mídia.** v1 = `text` + `data`; modos derivados do pool; arquivo depende do arco ATT;
   regras de download de `url`; saída por URL assinada.
8. **Fases.**
   - `A0 → A2 → A3 → A4` com `returnImmediately` + polling (bastam para o OpenClaw); **A5 vira
     otimização**.
   - Arcos paralelos que entram como pré-requisito: **ID-FED** (§ 2), obrigatório para
     `customer_agent`, e **ATT** (§ 4), obrigatório para `url`/`raw`.
   - **A6 valida também com o OpenClaw** como cliente de referência, além do SDK oficial: é o cliente
     mais restrito que existe (só bearer, sem arquivo, sem cancel), o que faz dele um bom teste.
9. **`tasks/cancel`.** O OpenClaw não o usa; mantém-se, com o `close_reason` novo que o § 9 do ADR já
   pede.

Nada disso mexe no que o § 5 do ADR diz que não muda: routing, admissão, skill-flow-engine, bridge e
modelo de sessão. O único ponto que encosta num invariante é o `channel: a2a` do D13, e ele o
**respeita** (renderização no adapter) em vez de contorná-lo.

---

## 7. O que a conversa com o Gemini afirmou e não se sustenta

| Afirmação | Conferido |
|---|---|
| o OpenClaw autentica "via Bearer Token/OAuth" | só bearer (documentação do OpenClaw) |
| o OpenClaw recebe "resposta via SSE streaming" | sem streaming e sem push; só polling |
| anexo A2A é sempre "baseado em ponteiro" | a v1.0 aceita `raw` (bytes inline) e `url` |
| "o A2A inicia uma sessão WebRTC paralela" para voz | não está na spec |
| `/.well-known/agent.json` | na v1.0 é `agent-card.json` (o ADR já usa o nome certo) |
| "7 a 30 dias — LGPD" | a LGPD não fixa prazo; o prazo é a finalidade do controlador |
| o A2A segue na Linux Foundation | está na AAIF desde 27/08/2026 |
| ACP "concorrente" do A2A | pelo que sei, o ACP da IBM foi incorporado ao A2A em 2025. **Não conferi nesta rodada** |
| AGX / NIP-AGX | não conferido; é nicho e não muda nenhuma decisão aqui |
| tarja automática de PII em imagem para o operador | viável, mas é OCR com taxa de erro. Não é garantia de v1 (§ 4.2 item 4) |

O que a conversa acerta e esta proposta incorpora:

- proxy de download: a URL externa nunca vai para dentro;
- remoção de metadados;
- antivírus;
- borrar e revelar com auditoria;
- `hash` no log em vez do nome do arquivo;
- tarefa longa com polling para atendimento humano.

---

## 8. Decisões que ficam com o dono

1. `a2a_client` = `agent_principal` externo (recomendado), ou entidade separada com justificativa.
2. Binding conversacional com `channel: a2a` (recomendado), ou reusar `webchat` (decisão de 13/08).
3. Onde mora o `client_secret` OIDC por tenant (cofre, Redis como o webchat, ou env).
4. Quais emissores o tenant pode declarar como **autoritativos** (só o IdP próprio, recomendado).
5. Ordem: o arco ATT antes do A2A (recomendado, porque o risco do § 4.1 existe hoje) ou em paralelo.

Depois dessas decisões: abrir as fichas no `pending.md` (grupos A2A, ID-FED e ATT) e reescrever o ADR.

---

## 9. Adendo (rodada 2, mesmo dia): um canal só, negociação por tarefa, e quem são os clientes

**Revisão da recomendação do § 3.3.** Os dois bindings (tarefa × conversa) nasciam de um fato
verdadeiro (o perfil `workflow` proíbe `menu`/`notify`), mas a conclusão estava errada. O perfil
`agent` **permite** `invoke`, `reason` e `complete`, e só proíbe `suspend` e `collect`. Então uma
tarefa de tiro único é simplesmente uma conversa sem `menu`, e cabe no mesmo canal. Fica **um canal,
`a2a`**, no perfil `agent`. O processo longo (`suspend`/`collect`, aprovação, timer) continua no pool
webhook e é alcançado **por dentro**, com `delegate` a partir do agente `a2a` (caminho vivo, CTR-01).
Assim o endereço público é um só e a composição fica interna, com o pool como unidade endereçável.

**Mídia e interação no canal único.** O A2A **não tem mídia em tempo real**. A spec só trata áudio e
vídeo como arquivo (`url` ou `raw`) e diz *"audio/video (via file references)"*. O canal é, portanto,
`medium: message`. A riqueza varia por **chamador**, e o próprio protocolo a negocia por tarefa
(`acceptedOutputModes`). O adapter escolhe a melhor forma que o cliente aceita e cai para a próxima,
como os adapters atuais já fazem com `menu` em canal sem botão:

1. **A2UI** (`application/json+a2ui` num `DataPart`): botões, listas e formulários desenhados pelo
   cliente, a partir de um catálogo declarativo e sem código executável. O Gemini Enterprise o
   renderiza.
2. **`DataPart` com JSON Schema:** o cliente de máquina preenche.
3. **Texto com opções numeradas:** o mesmo fallback dos canais de voz para agentes.

A escolha é fato do **participante**, não do canal (VOZ-09). Voz entre agentes fica fora: o chamador
é quem fala com a pessoa, e entre agentes o certo é texto e dado estruturado.

**Quem chama A2A hoje (medido em 2026-09-30):**

| Cliente | A2A cliente? | Porta que ele usa para chegar a terceiros |
|---|---|---|
| Microsoft Copilot Studio | **sim, GA abr/2026** | A2A |
| Gemini Enterprise | **sim, GA 17/08/2026** (registro e importação de agentes A2A, A2UI) | A2A |
| Salesforce Agentforce, ServiceNow; Genesys com os dois | sim (anunciado) | A2A |
| OpenClaw | sim (só bearer, texto e JSON, polling) | A2A |
| ChatGPT, Claude | não | **MCP** (Apps SDK, conectores) |
| Meta Muse (08/09/2026) | não | **diretório curado** de conectores revisados pela Meta; sem MCP no app de consumo |
| Gemini de consumo, Siri | não documentado | ecossistema próprio |

**Consequência:** o A2A alcança o **assistente corporativo**. Um exemplo é o Copilot do funcionário do
cliente chamando o agente de retenção ou a fila de especialistas. O **assistente pessoal do
consumidor** hoje chega por **MCP** ou por diretório curado, e não por A2A. O mesmo pool deveria
então ter uma segunda face pública, um **servidor MCP remoto**, com autorização OAuth/OIDC (a da spec
MCP de 2026-07-28), e ela reaproveita o `customer_agent` e o ID-FED (§ 2 e § 3.2). A2A e MCP são duas
**representações** do mesmo pool, como webchat e WhatsApp são dois canais da mesma sessão.
