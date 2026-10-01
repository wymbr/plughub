# ADR — PlugHub como servidor A2A: canal `a2a` sobre pool + sessão

- **Status:** proposto — **revisão 2** (2026-09-30). As decisões marcadas *(rev. 2)* foram aceitas
  pelo dono em 2026-09-30; o arco segue sem código.
- **Data:** 2026-08-13 · revisado em 2026-09-30
- **Escopo:** apenas **servidor** (agentes externos consomem pools da plataforma).
  PlugHub como **cliente** A2A está **fora** — ver §8.
- **Revisão 2 — de onde veio:** [`docs/product/a2a-binding-revisao-2026-09-30.md`](../product/a2a-binding-revisao-2026-09-30.md)
  (medição, alternativas descartadas e o que uma conversa externa afirmou e não se sustentou). Em
  resumo, a v1 foi escrita para **um** chamador (o sistema de um parceiro ou do tenant) e não cobria
  quatro coisas: a identidade de quem é atendido, o agente pessoal do consumidor, anexos e mídia.

---

## 1. Problema

Hoje, para um agente externo (LangGraph, CrewAI, Copilot Studio, Gemini Enterprise, o agente de um
parceiro, o orquestrador do próprio cliente ou o assistente pessoal de um consumidor) usar um agente
da plataforma, ele tem de **se integrar à plataforma**. Precisa descobrir que existe
`POST /v1/channels/webhook/pool/{pool_id}`, saber o `pool_id` de cor e montar um corpo cujo contrato
não está declarado em lugar nenhum. Depois descobre **sozinho** que a resposta é só `{session_id}` e
que não existe endpoint que devolva o resultado. Cada integração é bespoke, e a plataforma não tem
como cobrar, escopar ou auditar quem chamou.

O objetivo é o inverso: **o agente externo trata um pool da PlugHub como um agente A2A padrão**. Ele
descobre pelo AgentCard, manda mensagem, acompanha a task e recebe artefato, sem saber que existe uma
PlugHub do outro lado. Atrás do endpoint pode haver uma IA, uma pessoa, ou um time em conferência; o
chamador vê **um agente e um contrato** (*Opaque Execution*).

## 2. Achado que determina o desenho

O modelo de tarefa do A2A v1.0 já existe as-built, com outro nome:

| A2A | PlugHub as-built | Onde |
|---|---|---|
| `AgentCard` | pool + (descritor ausente) | `PoolRegistrationSchema` |
| `Task` / `taskId` (gerado pelo servidor) | sessão / `session_id` | modelo unificado de sessão |
| `contextId` (gerado pelo servidor) | journey / `root_session_id` | Journey J1 |
| `messageId` | entrada no stream | stream canônico |
| `submitted → working` | `active` | Arc 19 Fase B |
| `input-required` | `menu` aguardando resposta · `suspended` + `resume_token` | skill flow · `handle_resume` |
| `auth-required` | evidência de identidade exigida e ausente | `adr-identity-door-evidence` |
| `completed` / `failed` | `closed` + `outcome` / `close_reason` | domínio de `close_reason` |
| `message/stream`, `tasks/resubscribe` | `session:{id}:stream` (XREAD) | stream canônico |
| `referenceTaskIds` | proveniência | journey por (proveniência ∪ alias) |

**Consequência:** A2A é **representação externa de superfície existente**, não um motor de
orquestração novo. O routing-engine, o bridge, o skill-flow-engine e o modelo de capacidade
**não mudam de comportamento**.

**Contra-achado, que é o custo real** (re-medido em 2026-09-30, continua verdade): o caminho webhook
nasceu *fire-and-forget* para chamadores internos, que pegam o resultado pelo `pipeline_state` ou
pela retomada, nunca por HTTP. Logo **não existe superfície de resultado**:

- o trigger devolve só `{session_id}`;
- `GET /v1/channels/webhook/{session_id}/status` responde `"closed"` quando a chave não existe
  (`webhook.py:1812-1814`), então "não sei" e "terminou" são indistinguíveis;
- o step `complete` não persiste nada legível (`complete.ts` só sinaliza `agent_done`).

Para consumidor interno isso passou; num contrato externo é um valor plausível escondendo ausência.
**O net-new do arco é o artefato, não o protocolo.**

---

## 3. Decisões

### D1 — A2A é um **canal**: `a2a`, `medium: message`, perfil `agent` *(rev. 2)*

> **Revisão 2 inverte a D1 da v1**, que dizia *"binding, não `channel`"*. O motivo registrado ali
> continua verdadeiro: canal não é fato de credencial. Mas a conclusão tinha duas falhas, e as duas
> foram medidas.
>
> 1. **O caminho webhook roda o perfil `workflow`, que proíbe `menu`, `notify` e
>    `begin/end_transaction`** (recusado no deploy desde a CTR-01). Mapeado sobre ele, o binding
>    só expunha workflows de tiro único. Nenhum agente conversacional (triagem, retenção, segunda
>    via) podia ser alcançado, e esses são exatamente os que um assistente externo quer chamar.
> 2. **Traduzir `menu` para o que o chamador consegue renderizar é lógica de renderização por
>    canal**, e por invariante ela mora num adapter do channel-gateway. Ou seja, canal **é**
>    capacidade de renderização, e o A2A precisa de uma.

Decisão:

- **Um canal só, `a2a`, no perfil `agent`.** O perfil `agent` permite `invoke`, `reason` e
  `complete`, então uma tarefa de tiro único é uma conversa sem `menu` e cabe no mesmo canal. Não há
  dois bindings.
- **O processo longo** (`suspend`/`collect`: aprovação, timer, coleta ativa) continua no pool webhook
  e é alcançado **por dentro**, com `delegate` a partir do agente `a2a` (caminho vivo, que a CTR-01
  preservou). O endereço público é um só; a composição é interna.
- **O canal como filtro duro faz o opt-in de graça.** Pool que não lista `a2a` em `channel_types` não
  atende agente de fora. Reusar `webchat` (decisão da v1, § 8) faria todo pool de webchat atender
  agente de fora sem ter escolhido.
- **As métricas não se misturam.** Contato de agente externo aparece com `channel = a2a` em toda
  bancada, e isso resolve a mistura que o §9 da v1 registrava (TMA e SLA de "cliente" junto com
  "agente de parceiro").

Efeito: `ChannelSchema` ganha o valor `a2a` e `skill-profile.ts` o mapeia para o perfil `agent`. O
algoritmo de roteamento não muda.

### D2 — O AgentCard é **projeção** do agent-registry, nunca documento editável

Montado em request time (cache curto) a partir de `Pool` + slot `current` + descritor (D3) +
principal autenticado (D6). Consequências que vêm de graça:

- `AgentCard.version` = **`set_at` do slot `current`** (a mesma identidade de versão que
  `segments.deploy_version` já carimba). O contrato externo versiona junto com o deploy.
- Editar o card = editar o pool na tela. Não existe card escrito à mão (one-source).
- Promover um deploy **muda o card**. Se isso for indesejável para um parceiro, a resposta é pin de
  versão no cliente, não card congelado.
- **Os modos de mídia do card também são derivados, nunca declarados** (D14). Os `securitySchemes`
  vêm dos tipos de principal que o pool admite (D6).
- **Card público × card estendido.** O card público só existe para pool marcado `discoverable`.
  O card estendido (`GetExtendedAgentCard`, depois de autenticar) é o que **aquele** principal vê,
  inclusive de pool que não é descobrível.

> **Correção de 2026-10-01 (AAS-03, decisão do dono): um card por POOL, endereçado por
> `ChannelEndpoint`.** Esta seção dizia que `/.well-known/agent-card.json` *"lista só os pools
> marcados `discoverable`"* — um card listando vários pools. Medido no `a2a.proto` v1.0: o
> `AgentCard` descreve **um** agente (um nome, uma descrição, skills, interfaces), e nada numa
> mensagem diz para qual pool ela vai — um card de vários pools obrigaria o adapter a CLASSIFICAR o
> pedido, que é orquestrador, não canal. E o gateway não tem host→tenant. Ficou:
>
> - card em **`{base}/a2a/{slug}/.well-known/agent-card.json`**, interface em `{base}/a2a/{slug}`;
>   o `{slug}` é um `ChannelEndpoint` de canal `a2a` — o mesmo modelo do slug do webchat e do
>   número da voz, registro único de endereço. A URL não expõe `tenant_id` nem `pool_id`;
> - o endereço só se cadastra para pool de **contato** com o canal e o contrato (422 no resto), e o
>   slug é `[a-z0-9][a-z0-9_-]*` porque vai na URL;
> - o **`/.well-known/agent-card.json` da raiz não é servido**: não há um agente por host;
> - o card é montado no agent-registry (`GET /v1/a2a-cards/{slug}`, só leitura) e servido pelo
>   gateway com cache de 30 s; recusa é 404 **mudo** para fora e motivo no log (sem oráculo de
>   "existe mas não é público"); `version` = `set_at` do `current`, e pool sem `current` não tem
>   card (não roda);
> - o `input_schema`/`output_schema` vai numa extensão (`urn:plughub:a2a:extension:io-schema:v1`):
>   o card v1.0 não tem campo para JSON Schema;
> - `extendedAgentCard: false` até o principal existir (AAS-04) — anunciá-lo antes seria prometer
>   uma rota que não responde. Ficha `AAS-13`.

### D3 — O descritor é o contrato; o canal é o opt-in *(rev. 2)*

Bloco novo em `PoolRegistrationSchema`, **obrigatório quando `channel_types` contém `a2a`** e
proibido quando não contém. Um pool exposto sem contrato é o defeito que este bloco existe para
impedir.

```ts
a2a: z.object({
  display_name:  z.string(),
  description:   z.string(),              // o que este agente faz, em prosa, para o LLM do caller
  input_schema:  z.record(z.unknown()),   // JSON Schema 2020-12
  output_schema: z.record(z.unknown()),   // vira o DataPart do artefato (D5)
  skills:        z.array(A2ASkillSchema).min(1),  // id/name/description/tags/examples
  discoverable:  z.boolean().default(false),      // aparece no card PÚBLICO (D2)
  principal_kinds: z.array(z.enum(["partner", "customer_agent"])).min(1),  // D6
}).optional()
```

*(Rev. 2: o `exposed: boolean` da v1 saiu. Com o canal, "está exposto" teria duas casas,
`channel_types` e `exposed`, e duas casas para o mesmo fato divergem.)*

**O descritor se paga sozinho.** É o mesmo artefato que o *contrato delegate-por-pool* e a
renderização de "Adicionar Especialista" no Console já pedem.

⚠️ **O contrato de contexto não é `inputModes`/`outputModes`** (CTR-05). O `input_schema` diz *o
que* o agente precisa (por exemplo, o CPF); os modos dizem *em que formato de interface* ele fala.
Colapsar os dois faria um parceiro ler *"preciso de CPF"* como *"aceito text/plain"*.

**Como ficou (AAS-01, 2026-10-01).** `PoolA2ADescriptorSchema` em `@plughub/schemas/agent-registry.ts`,
mais estrito que o esboço acima, porque contrato vazio não diz nada ao chamador: textos com
`min(1)`, `input_schema`/`output_schema` exigem `type` string, `skills[].id` em snake_case e único,
`principal_kinds` sem repetição, objeto `.strict()`. O portão mora numa casa só
(`agent-registry/src/lib/a2a-descriptor.ts`) e julga o **estado resultante** no POST e no PUT, como
o `media_policy` (VOZ-10): tirar o canal com o descritor gravado é recusado; tirar os dois juntos
passa. Duas decisões de implementação:
- **Só pool `purpose: contact` exige o descritor.** O espelho de fila interna (`-int`) herda os
  canais do pai e não tem chamador externo; exigir ali forçaria um contrato inventado. Por isso o
  card (A1) lista **só pools de contato**. A proibição (descritor sem canal) vale para qualquer pool.
- **O canal nasce sem capacidade** (`CHANNEL_CAPABILITIES.a2a = []`, nas duas casas): o adapter é a
  A4, e canal que declara o que ainda não faz é o defeito medido na VOZ-12. Até lá, nenhum
  `collect` escolhe `a2a`, e saída para o canal não tem adapter.
Gate: `infra/test/probe_aas01_a2a_pool.sh` (bateria `mut_aas01_a2a_pool.sh`, 5 de 5).

### D4 — `Task` **é** a sessão; `contextId` **é** a journey. Sem entidade nova

`taskId := session_id`, `contextId := root_session_id`. Não se cria tabela, ledger nem ciclo de
vida de task.

Isto é aplicação direta de *"never create a wide container for a fact that fits a narrow one"*: a
`WorkflowInstance` e a entidade `Journey` já foram removidas por serem contêineres novos para fatos
que eram sessão e proveniência. Uma Task A2A é **exatamente** uma sessão, e o `contextId` é
**exatamente** a journey por proveniência. Reintroduzir um contêiner aqui repetiria o erro pela
terceira vez. As regras de início, fim e continuação estão em D15.

### D5 — Artefato e status honesto são pré-requisito (não refinamento)

1. O `complete` passa a **persistir o resultado terminal** (outcome + payload declarado no step,
   validado contra o `output_schema` do descritor) numa chave legível, com TTL alinhado ao da
   sessão. Por A2A ele sai como `DataPart`.
2. O status passa a distinguir **`unknown`** de `closed`. Ausência de chave é ausência, não
   conclusão.

Sem (1) não existe `tasks/get` com artefato; sem (2) o chamador recebe "terminou" para uma sessão
que nunca existiu, e vai construir lógica em cima disso.

### D6 — Principal de máquina: **um** mecanismo (`agent_principals`), dois tipos *(rev. 2)*

> **Rev. 2.** A v1 criava a entidade `a2a_client` no auth-api. A `agent-principal-identity-spec.md`
> já propunha `auth.agent_principals` com `origin: external` e client credentials, e a triagem do
> n8n (C6) já tinha registrado que seriam **dois mecanismos para o mesmo fato**. Decisão: **o
> principal A2A é um `agent_principal` de origem externa.** Não existe `a2a_client`.

Toda chamada carrega **dois fatos distintos**, que nunca dividem um campo:

| Fato | Pergunta | Onde mora |
|---|---|---|
| **principal chamador** | que software chama, com que cota, para quais pools? | credencial (`agent_principal`) |
| **titular** | em nome de qual cliente, com que prova? | identidade do cliente, fixada na **sessão** (D12) |

Dois tipos de principal externo:

- **`partner`**: emitido pelo admin do tenant, para o ERP, orquestrador ou parceiro do tenant.
  `allowed_pools` explícito, sem titular fixo. Um titular declarado pelo chamador é **sempre
  `claimed`**. No card: `HTTPAuth` bearer ou `OAuth2` client credentials.
- **`customer_agent`**: **emitido ao próprio cliente**, por autosserviço, depois de uma prova de
  identidade (D12). O cliente entra no portal ou widget do tenant e gera um token pessoal, revogável,
  de validade curta e cota baixa, preso ao `customer_id`, com o nível de prova e a idade da emissão.
  É esse token que ele cola no seu assistente (por exemplo, o `outboundToken` do OpenClaw). O titular
  vem **do token**. No card: bearer, e `OAuth2` authorization code + PKCE ou device code para os
  clientes que suportem.

O `customer_agent` é o que substitui o *"cadastro pelo admin"* para o caso do consumidor: a
credencial nasce de prova, sem ninguém do tenant no meio.

A fusão preserva o que a spec de Agent Principal exige além do A2A: `origin: native` e
`principal_id`/`subject_type` no `AuditRecord`.

### D7 — `tenant_id` **nunca** vem do corpo

Re-medido em 2026-09-30: `webhook_trigger_by_pool` ainda faz `body.get("tenant_id") or
settings.tenant_id` (`main.py:1538`). A WHK-01 acrescentou a conferência de que o pool existe, mas
nenhuma credencial de chamador. Isso é aceitável enquanto a rota é interna, e é **cross-tenant por
construção** no instante em que a superfície fica pública. No canal `a2a` o tenant vem
**exclusivamente da credencial**. Item de segurança, não de higiene.

### D8 — Masking: mascarado por padrão, sem opção; bloco mascarado não trafega *(rev. 2)*

- O chamador A2A **não** entra em `authorized_roles`. `original_content` nunca sai por A2A, nem sob
  configuração. O que o agente externo recebe é o que o cliente receberia.
- *(rev. 2)* **Um bloco `begin/end_transaction` (entrada mascarada) nunca atravessa A2A.** Um agente
  de terceiro não é portador de dado de cartão ou senha. O adapter responde `INPUT_REQUIRED` com um
  **link fora de banda** (o mesmo mecanismo do link web do survey), a pessoa digita no navegador, e o
  agente só vê o resultado.
- *(rev. 2)* O anexo que o cliente enviou nunca volta por A2A (D14).

### D9 — Capacidade não muda; cota **por principal** volta *(rev. 2)*

A sessão A2A ocupa vaga como qualquer sessão do pool; o gate de admissão (fatia 3) **não se toca**.
A contenção por pool é resolvida pelo [`adr-pool-no-resource-policy.md`](adr-pool-no-resource-policy.md)
(`on_no_resource: reject`).

*(Rev. 2)* O `no-resource-policy` dispensou a cota por chamador *"até aparecer um segundo caller
disputando o mesmo pool"*. No `customer_agent` isso acontece **por construção**: N consumidores, um
pool. A cota por `agent_principal` (dimensão `a2a_tasks` no `assertQuota`) é **requisito** do tipo
`customer_agent`, e opcional no `partner`.

### D10 — `origin` continua `live`; procedência pelo canal *(rev. 2)*

Sessão dirigida por A2A é produção (`origin: live`) e **entra** na amostragem de qualidade. Com a
D1 revisada ela é contato **inbound** (`spawn_reason` nulo), e o que a distingue nos relatórios é
`channel = a2a`. Não se inventa discriminador novo. O `principal_id` vai no segmento e na auditoria
(D6).

### D11 — *(substituída)* Graça de espera do chamador

Substituída pelo D6 do [`adr-pool-no-resource-policy.md`](adr-pool-no-resource-policy.md): o
desfecho sem recurso é **política do pool** (`on_no_resource`), serve a qualquer chamador
programático, e o canal só a **traduz** (`reject` vira `503` + `Retry-After` na criação, ou
`TASK_STATE_REJECTED`). O cabeçalho fica porque é citado por outros documentos.

### D12 — Prova do titular: `AUTH_REQUIRED`, fora de banda, pelo mecanismo de evidência que já existe *(rev. 2)*

A identidade do cliente segue o [`adr-identity-door-evidence.md`](adr-identity-door-evidence.md)
inteiro (evidência sem score, escritor único `/internal/identity-evidence`, `SATISFIED_BY`,
procedência como segundo eixo). O canal `a2a` só acrescenta o **transporte**:

1. **Identidade dita numa `Part` é sempre `claimed`.** "Sou o CPF X", dito por um agente, pesa o
   mesmo que um cliente digitando, e nunca mais que isso.
2. **Quando o skill exige evidência que a sessão não tem**, a task vai para
   `TASK_STATE_AUTH_REQUIRED` com uma `Message` contendo um **link de prova**. A pessoa abre o link
   no navegador (OTP ou login federado), o agente faz polling, e a sessão ganha a evidência. O
   agente **nunca** recebe o código nem o `id_token`: é a costura do segredo do Dialog Primitive
   valendo para agente de fora.
3. **Login federado entra como dois mecanismos novos de evidência**, pelo escritor único:
   - **`princ`**: o `sub` do IdP **do próprio tenant** (portal ou app, em Keycloak, Auth0 ou Entra
     External ID). O tenant declara o emissor como *sistema de registro*, e `(iss, sub)` é o
     `external_ref` que liga ao cadastro importado (IDN-01). **É o produtor que a PID-21 procura.**
   - **`oidc_email`**: login social (Google, Apple, Microsoft; Facebook por último, porque o Meta
     Login é OIDC só parcial e o e-mail é opcional). Prova **posse de um e-mail**, e só vale como
     evidência quando `email_verified = true` **e** o e-mail é âncora `authoritative` do cliente
     (D8/D13 do ADR de identidade). Nesse caso ele equivale ao OTP (`SATISFIED_BY.otp` ganha
     `oidc_email`), como a chegada pelo WhatsApp. E-mail sem dono cadastrado dá `failed`, nunca
     "cadastro novo seguro".
   - **Login social não é identidade civil** (o Google não devolve CPF) e **não cria
     `authoritative`**. O único caminho para `authoritative` continua sendo a importação com
     credencial (PID-12). Um emissor só é `princ` se for o do tenant.
4. **Mecânica OIDC:**
   - a plataforma é Relying Party por tenant;
   - authorization code + PKCE, com `state` e `nonce`;
   - conferência de `iss`, `aud`, `exp` e `nonce` pelo JWKS;
   - nunca fluxo implícito, e nunca `id_token` recebido no corpo de uma chamada (substituição de
     token: um token de outra `aud` repassado por um agente é barrado pela conferência do `aud`);
   - **access e refresh token são descartados** depois de validar: a plataforma não age na conta do
     cliente, então não guarda o token;
   - escopos mínimos (`openid email`);
   - o consentimento é fato da sessão;
   - `(iss, sub)` e o e-mail entram no dossiê (AUD-03) e na eliminação (AUD-06).
5. **Configuração:** emissor, `client_id`, escopos, se o emissor é `princ` e a lista de redirect
   ficam no config-api (namespace próprio, editável na tela). O **`client_secret` vai para cofre de
   segredos por tenant**. Enquanto o cofre não existir, a dívida fica registrada; não se cria um
   terceiro padrão ao lado de `llm_accounts` (env) e `webchat.jwt_secret` (Redis).
6. **Um mecanismo, vários canais.** A mesma prova serve ao webchat (botão no widget), ao WhatsApp e
   SMS (link) e ao A2A (`AUTH_REQUIRED`). O skill exige *evidência*; o método é escolha do tenant.

### D13 — Mandato do agente do cliente *(rev. 2)*

O token `customer_agent` carrega **o que o agente pode fazer em nome da pessoa**, e não só quem ela
é: por exemplo, `consultar` sem `contratar` ou `cancelar`. É conjunto de capacidades declarado na
emissão, pela pessoa. Uma ação de risco (a lista é declarada no skill, como os pisos do ADR de
identidade) exige **confirmação da pessoa** pelo mesmo link fora de banda da D12: `AUTH_REQUIRED`
de novo, com o texto da ação. A plataforma nunca presume que a pessoa autorizou o que o agente dela
decidiu.

### D14 — Mídia e interação *(rev. 2)*

O que a spec v1.0 dá: `Part` com `text`, arquivo (`url` ou `raw`, com `mediaType` e `filename`) e
`data` (JSON). Modos de mídia no card (`defaultInputModes`/`defaultOutputModes` e por skill) e, por
tarefa, `acceptedOutputModes` do cliente. **Não há mídia em tempo real**: áudio e vídeo só como
arquivo.

1. **v1 = `text` + `data`.** O artefato é o `DataPart` do `output_schema` (D5).
2. **Interação negociada por tarefa, com fallback no adapter** (como os adapters já fazem com `menu`
   em canal sem botão). Em ordem de preferência, conforme o que o cliente aceita:
   1. **A2UI** (`application/json+a2ui` num `DataPart`): botões, listas e formulários desenhados pelo
      cliente, a partir de catálogo declarativo e sem código executável. O Gemini Enterprise
      renderiza.
   2. **`DataPart` com JSON Schema**: o cliente de máquina preenche.
   3. **Texto com opções numeradas**: o mesmo fallback dos canais de voz.

   A escolha é fato do **participante**, não do canal (VOZ-09).
3. **Os modos do card são derivados do pool, nunca declarados à mão.** Um pool `agent_kind: ai`
   declara `text/plain` e `application/json`, porque o ai-gateway só consome texto. `image/*` e PDF
   só aparecem em pool que tenha humano **e** Console que renderize anexo. Declarar o que ninguém
   consome é o card mentindo.
4. **Arquivo (`url`/`raw`) só entra depois do arco de governança de anexos (ATT, § 6).** Quando
   entrar:
   - download só pela plataforma, por proxy de egresso: somente `https`, sem IP privado, sem
     redirect para rede interna, com teto de tamanho e de tempo;
   - depois, a mesma esteira de ingestão dos outros canais (allowlist, magic bytes fail-closed,
     re-codificação de imagem, antivírus, sha256);
   - a URL externa **nunca** chega ao Console nem a um LLM;
   - `raw` inline tem teto baixo.
5. **Arquivo de saída** (segunda via, comprovante) sai como **URL assinada curta**. Um anexo que o
   cliente enviou não volta (D8).
6. **Voz entre agentes fica fora.** Quem fala com a pessoa é o agente dela; entre agentes, o certo é
   texto e dado estruturado.

### D15 — Início, fim e continuação da tarefa *(rev. 2)*

Medido na spec v1.0: `taskId` e `contextId` são gerados pelo servidor; uma tarefa terminal não
aceita mensagem (`UnsupportedOperationError`); **não há** mecanismo de fim de contexto, e a expiração
é política do servidor, que *"SHOULD document"*.

1. **Toda mensagem nova cria `Task`.** O servidor nunca responde com `Message` solta, embora a spec
   permita, porque todo contato é sessão: auditoria, cota, qualidade e cobrança dependem dela.
2. **Continuação:** mensagem com o mesmo `taskId` (e `contextId`) enquanto a task está em
   `INPUT_REQUIRED` ou `AUTH_REQUIRED` é o próximo turno da **mesma** sessão.
3. **Fim:** é o estado terminal, decidido pelo agente (`complete` → `COMPLETED`) ou pela plataforma
   (`FAILED`/`REJECTED`). Continuar o assunto depois disso é **task nova no mesmo `contextId`**, ou
   seja, sessão nova na mesma journey.
4. **`contextId` enviado pelo cliente só é aceito se foi emitido por nós, para o mesmo principal e o
   mesmo titular.** Fora disso é recusado, nunca adotado. Sem essa regra, um agente penduraria a
   própria task na conversa de outra pessoa e herdaria o contexto dela.
5. **Inatividade em estado interrompido:** o `session_timeout` fecha a sessão e a task vira `FAILED`
   com uma `Message` que diz o motivo. O prazo é **publicado** no descritor.
6. **Validade do `contextId`** = TTL da journey (30 d), **publicada**.
7. **`tasks/cancel`** fecha a sessão com um `close_reason` novo, **`caller_cancel`**. Nunca
   `customer_abandon`, senão a taxa de abandono do pool sobe por desistência programática e continua
   plausível.
8. **`tasks/list` por `contextId`** lista só as tasks daquele principal e titular.

### D16 — Face MCP do mesmo pool, para assistentes de consumo *(rev. 2, direção)*

Medido em 2026-09-30: os assistentes **corporativos** chamam A2A (Copilot Studio GA abr/2026, Gemini
Enterprise GA 17/08/2026, Agentforce, ServiceNow). Os **de consumo** não: ChatGPT e Claude chegam a
terceiros por **MCP**, e o Meta Muse por diretório curado. O mesmo pool deve então ter uma segunda
representação pública, um **servidor MCP remoto**, que reusa o principal (D6), a prova do titular
(D12) e o mandato (D13). A2A e MCP são duas **representações** do mesmo pool, como webchat e
WhatsApp são dois canais da mesma sessão.

⚠️ Essa face **não** é o transporte do `mcp-server-plughub` (`/sse`, `/messages`), que exige
credencial de serviço desde a CAP-10 e continua interno. É superfície de borda nova, e a forma dela
(canal próprio ou não) vai para **ADR próprio**. Aqui fica só a direção, e a garantia de que
principal e identidade são os mesmos.

---

## 4. Borda

Prefixo novo: **`/a2a`**, **externo** (o card mora sob ele desde a AAS-03 — `/.well-known` da raiz
não é servido, ver a correção do D2), mais a página
do link de prova e de entrada mascarada (D8/D12) sob um prefixo público já existente ou novo, a
decidir na A4.

A allowlist da borda do channel-gateway (hoje **seis** prefixos) é regra, não gosto: cada prefixo
novo precisa de linha na tabela do `infra/test/probe_edge_surface.sh`, que reprova prefixo sem
classificação. `/v1` **continua interno**; o canal `a2a` não o publica.

⚠️ A separação externo×interno é de **código** (`allowed_origins`), não de topologia, e nada no
repositório garante o que o deploy publica. A borda do platform-ui (nginx) também alcança o gateway
e só repassa caminhos nomeados (AUT-20): `/a2a` **não** deve passar por ela.

## 5. O que **não** muda

- algoritmo de roteamento;
- modelo de admissão e capacidade;
- skill-flow-engine;
- orchestrator-bridge;
- modelo de sessão, segmento e journey;
- MCP como único protocolo de integração **interna**. A2A é protocolo de **borda**, na mesma classe
  de WhatsApp e webchat; se alguém implementar A2A falando direto com o routing-engine, os dois
  invariantes caem.

O que muda por declaração: `ChannelSchema` (+`a2a`), `skill-profile.ts` (`a2a` → `agent`),
`PoolRegistrationSchema` (+`a2a`), o domínio de `close_reason` (+`caller_cancel`) e o domínio de
mecanismos de evidência (+`princ` e +`oidc_email`).

## 6. Fases *(rev. 2)*

| # | Fase | Entrega | Nota |
|---|---|---|---|
| **ATT-0** | Risco de anexo **de hoje** | allowlist e tamanho em todo canal de entrada, magic bytes fail-closed, `nosniff`, CSP `sandbox`, `filename*`, `inline` só para imagem | **independe do A2A e vem antes**: WhatsApp e e-mail pulam `validate_mime` e a porta pública serve inline |
| **A0** | Canal + descritor | `a2a` no `ChannelSchema` e no perfil; bloco `a2a` no pool; tela | destrava delegate-por-pool · **feita em 2026-10-01 (AAS-01)** |
| **A1** | AgentCard read-only | card público + estendido, modos derivados, `securitySchemes` | **sem execução**; força o descritor a ser honesto · **card público feito em 2026-10-01 (AAS-03)**; o estendido espera o principal (`AAS-13`) |
| **A2** | Principal `partner` | `agent_principals` (fusão, D6), credencial, `allowed_pools`, tenant da credencial (D7), linha no probe de borda | **bloqueia A4** |
| **A3** | Artefato + status honesto | resultado terminal legível; `unknown` ≠ `closed` | o net-new que ninguém espera |
| **A4** | Adapter JSON-RPC | `message/send` (bloqueante com teto + `returnImmediately`), `tasks/get`, `tasks/cancel`, `tasks/list`; `menu` → `INPUT_REQUIRED` com `DataPart`/texto; D15 inteira | **basta para o OpenClaw** (só bearer, texto e JSON, polling) |
| **A5** | Streaming + A2UI | `message/stream`, `tasks/resubscribe` (SSE) sobre o stream canônico; A2UI no fallback | *(rev. 2)* **otimização, não pré-requisito**: os clientes medidos saem do blocking por polling |
| **A6** | Validação | clientes de referência: SDK oficial, **OpenClaw** (o mais restrito) e **Copilot Studio ou Gemini Enterprise** (o comprador real); isolamento cross-tenant e cross-titular (D15.4); probe de borda | gate |
| **B1** | Prova federada (ID-FED) | `princ` (PID-21) e `oidc_email` pelo escritor único; RP OIDC por tenant; cofre de segredo | **bloqueia B2** |
| **B2** | `customer_agent` | emissão por autosserviço após prova, cota por principal (D9), `AUTH_REQUIRED` (D12), mandato (D13) | o caso do consumidor |
| **B3** | Pool humano por A2A | fila de pessoas atrás do canal, com `on_no_resource` e cota | *(rev. 2)* deixa de ser "fase 2 com canal a decidir": o canal já existe |
| **ATT** | Governança de anexo completa | esteira única de ingestão, antivírus com quarentena, portas pública (URL assinada) e interna (capacidade + `audit.access`), retenção como classe do namespace `retention` | **bloqueia `url`/`raw`** (D14.4) |
| **C** | Face MCP (D16) | ADR próprio | direção |

**A2 antes de A4 é inegociável.** Publicar execução antes do principal é publicar um disparador
anônimo de pools que promovem deploy e contatam clientes. **B1 antes de B2 também**: um
`customer_agent` emitido sem prova é o cadastro declarado com credencial.

## 7. Riscos

- **Colisão de nome.** "A2A" já significa *delegação interna* nos docs (`task` step,
  `assist`/`transfer`) e **não é** o protocolo Agent2Agent. Renomear o uso interno para
  "delegação" antes de A1, ou os docs passam a ter dois A2A.
- **Diferencial não é durável.** A2A v1.0; o protocolo **entrou na Agentic AI Foundation em
  27/08/2026**; Kong AI Gateway 2.0 suporta A2A, e a Genesys anunciou A2A com Agentforce e
  ServiceNow. O protocolo virou item de catálogo. O fosso continua sendo o que está **atrás** do
  endpoint: fila de pessoas, capacidade, SLA, qualidade medida e auditoria. É o argumento de venda
  deste arco: *atrás do endpoint deles há um agente; atrás deste pode haver um time, e o contrato
  não muda.*
- **O cliente A2A de consumo ainda não existe em escala.** O A2A alcança hoje o assistente
  corporativo; o consumidor chega por MCP (D16). Construir B2 sem a face MCP atende só OpenClaw e
  afins.
- **Assistentes vão chegar pelos canais que já existem.** Agentes de consumo já operam navegador e
  telefone. Webchat e voz vão receber agentes se passando por cliente, e isso é assunto de política
  de canal (reconhecer o agente declarado, exigir prova, separar a métrica). Não é deste ADR, mas é
  o mesmo problema visto do outro lado.
- **Card muda no promote** (D2). Documentar para parceiros antes do primeiro contrato.

## 8. Fora de escopo (explícito)

- **PlugHub como cliente A2A.** Um agente externo não tem
  `agent_login → ready → busy → done`, heartbeat, semáforo de vaga, pausa nem interceptação MCP.
  Modelá-lo como pool exigiria inventar capacidade para recurso que não é nosso, em colisão frontal
  com o arco de capacidade (moedas não-fungíveis). Se um dia entrar, entra como **primitivo
  `invoke`** (tool cliente A2A), nunca como pool.
- **Runtime importado** (agente de terceiro rodando **como pool**, via SDK de certificação +
  `plughub-sdk proxy`). **Fora por decisão de produto, não por custo:** importar pede que a
  plataforma garanta capacidade, heartbeat, pausa, contrato `agent_done` e auditoria não-optável
  sobre código que ela não controla, e isso corrói a camada de governança que é o diferencial. Este
  ADR **padroniza** a fronteira em vez de dissolvê-la. Não confundir com **`external-mcp`** (expor
  *tool*, única borda em vigor, **fica**) nem com **portabilidade** (`certify`/`skill-extract`, que
  sustenta o "sem lock-in", **fica**, e A2A **não** a cobre). Reabrir só com demanda comercial
  nomeada. Ver [`docs/product/agentes-externos-reclassificacao.md`](../product/agentes-externos-reclassificacao.md).
- **Mídia em tempo real entre agentes** (a spec não tem; D14.6).
- **IA multimodal no ai-gateway.** Assunto próprio (custo, conta e modelo por pool); não bloqueia
  este arco, e enquanto não existir os modos do card dizem a verdade (D14.3).
- **Login de operador por SSO corporativo.** Pedido real de enterprise, mas mora no auth-api e não
  tem relação com a identidade do cliente. Ficha própria.
- **Binding gRPC** (só JSON-RPC sobre HTTPS + REST no v1).
- **Assinatura do card** (JWS/`AgentCardSignature`), até haver parceiro que exija.
- **Push notifications** de volta ao chamador. O v1 é polling + SSE, e nenhum cliente medido pede
  push.

## 9. Indisponibilidade de recurso e espera — o que o protocolo dá

Medido na spec v1.0. **Não existe** estado `queued`, posição de fila, ETA, nem timeout de espera
negociável pelo cliente. O `TaskState` é fechado em nove valores, e os assentos existem:

| Situação PlugHub | A2A | Nota |
|---|---|---|
| enfileirado aguardando agente | `TASK_STATE_SUBMITTED` | é o único estado de "aceita, ninguém pegou"; não carrega posição |
| alocado, agente atendendo | `TASK_STATE_WORKING` | |
| agente pede dado ao chamador | `TASK_STATE_INPUT_REQUIRED` | interrompido, não terminal |
| exigida evidência de identidade ou confirmação | `TASK_STATE_AUTH_REQUIRED` | interrompido; link fora de banda (D12/D13) |
| `on_no_resource: reject` | `503` + `Retry-After` ou `TASK_STATE_REJECTED` | rejeição **na criação**, autorizada pela spec (`no-resource-policy` D6) |
| `max_wait_exceeded` | `TASK_STATE_FAILED` | **não** `REJECTED`: a task foi aceita e depois expirou |
| admissão negada por cota | `503` + `Retry-After` | a task **não nasce** |
| inatividade em estado interrompido | `TASK_STATE_FAILED` | com `Message` de motivo (D15.5) |
| chamador desiste | `tasks/cancel` → `TASK_STATE_CANCELED` | `close_reason: caller_cancel` (D15.7) |

**Timeout de espera pelo recurso não é parâmetro de chamada, e isso está certo.** No A2A o cliente
só escolhe *bloquear ou não* (`returnImmediately`). O teto é `pool.queue_config.max_wait_s`,
propriedade **do agente**, publicada no descritor.

**Posição de fila**, se desejada, vai como `Message` dentro do `TaskStatusUpdateEvent` do stream. O
`queue.position_updated` do routing já produz o dado. **Não** usar `Extension` da AgentCard para
isso: extensão que o cliente não entende é ruído, e `required: true` quebraria interop.

**Assimetria a dimensionar: um chamador A2A não é uma pessoa esperando.** Não abandona por tédio;
reenvia, de graça, em loop. Back-pressure explícito (`503` + `Retry-After`) é requisito, e a cota do
principal (D9) tem de ser **menor** que a capacidade do pool.

## 10. Referências

- [A2A Protocol Specification v1.0](https://a2a-protocol.org/latest/specification/) ·
  [A2A entra na AAIF (27/08/2026)](https://a2a-protocol.org/latest/blog/2026/08/27/a-new-chapter-for-a2a-joining-the-agentic-ai-foundation/) ·
  [a2aproject/A2A](https://github.com/a2aproject/A2A)
- Clientes medidos em 2026-09-30:
  [OpenClaw A2A](https://docs.openclaw.ai/channels/a2a) ·
  [Copilot Studio A2A](https://learn.microsoft.com/en-us/microsoft-copilot-studio/add-agent-agent-to-agent) ·
  [Gemini Enterprise A2A](https://docs.cloud.google.com/gemini/enterprise/docs/register-and-manage-an-a2a-agent) ·
  [A2UI no Gemini Enterprise](https://cloud.google.com/blog/topics/developers-practitioners/guide-to-gemini-enterprise-and-a2ui-integration)
- Repo:
  - `docs/product/a2a-binding-revisao-2026-09-30.md` (revisão 2: medição e alternativas);
  - `docs/adr/adr-identity-door-evidence.md` (D12);
  - `docs/product/agent-principal-identity-spec.md` (D6);
  - `docs/adr/adr-pool-no-resource-policy.md` (D9, D11);
  - `docs/arcos/arc19-unified-session-model.md` (Task↔sessão, perfis);
  - `docs/adr/adr-journey-session-segment-model.md` (D4);
  - `docs/adr/adr-webhook-endpoint-single-registry.md`;
  - `docs/guias/webhook-patterns.md` § Exposição na borda (§4);
  - `docs/product/competitive-analysis-2026-09.md` (§7).

---

## Histórico

- **2026-08-13, v1.** Binding sobre o caminho webhook, `a2a_client` no auth-api, streaming como
  pré-requisito de pool humano.
- **2026-08-31.** Um resumo denso deste ADR, que vivia no índice do `CLAUDE.md`, foi migrado para
  cá como apêndice, marcado como trabalho aberto a dobrar no corpo.
- **2026-09-30, rev. 2.** Apêndice dobrado no corpo (nenhum item dele ficou sem casa: Task=sessão,
  card-projeção, net-new = artefato, principal, tenant, masking, cota, fases, fora de escopo).
  Mudanças: D1 invertida (canal `a2a`); D3 sem `exposed`; D6 fundida em `agent_principals` com dois
  tipos; D8 e D9 ampliadas; D10 pelo canal; D11 substituída; D12–D16 novas; A5 rebaixada a
  otimização; fases ATT-0, B1–B3, ATT e C.
- **2026-10-01, fase A0 (AAS-01).** Canal e descritor entregues; D3 ganhou o *como ficou*
  (schema estrito, portão sobre o estado resultante, espelho `-int` isento, capacidade vazia até a A4).
- **2026-10-01, fase A1 (AAS-03).** D2 corrigida por decisão do dono: um card por pool, endereçado
  por `ChannelEndpoint` `a2a`, sem `/.well-known` na raiz; o card estendido virou a `AAS-13`.
