# ADR — PlugHub como servidor A2A: binding externo sobre pool + sessão

- **Status:** proposto
- **Data:** 2026-08-13
- **Escopo:** apenas **servidor** (agentes externos consomem pools da plataforma).
  PlugHub como **cliente** A2A está **fora** — ver §8.

---

## 1. Problema

Hoje, para um agente externo (LangGraph, CrewAI, agente de um parceiro, orquestrador do
próprio cliente) usar um agente da plataforma, ele tem de **se integrar à plataforma**:
descobrir que existe `POST /v1/channels/webhook/pool/{pool_id}`, saber o `pool_id` de cor,
montar um corpo cujo contrato não está declarado em lugar nenhum, e depois **descobrir
sozinho** que a resposta é só `{session_id}` e que não existe endpoint que devolva o
resultado. Cada integração é bespoke, e a plataforma não tem como cobrar, escopar ou
auditar quem chamou.

O objetivo é o inverso: **o agente externo trata um pool da PlugHub como um agente A2A
padrão** — descobre pelo AgentCard, manda mensagem, acompanha a task, recebe artefato —
sem saber que existe uma PlugHub do outro lado.

## 2. Achado que determina o desenho

O modelo de tarefa do A2A v1.0 já existe as-built, com outro nome:

| A2A | PlugHub as-built | Onde |
|---|---|---|
| `AgentCard` | pool + (descritor ausente) | `PoolRegistrationSchema` |
| `Task` / `taskId` | sessão / `session_id` | Arc 19 |
| `contextId` | `root_session_id` (journey) | Journey J1 |
| `submitted → working` | `active` | Arc 19 Fase B |
| `input-required` | `suspended` + `resume_token` | `handle_resume` |
| `completed` / `failed` | `closed` + `outcome` / `close_reason` | domínio de `close_reason` |
| `message/stream`, `tasks/resubscribe` | `session:{id}:stream` (XREAD) | stream canônico |
| push notification config | webhook-na-borda (§9.6a) | spec |

**Consequência:** A2A é um **binding de protocolo sobre superfície existente**, não um
motor de orquestração novo. O routing-engine, o bridge, o skill-flow-engine e o modelo de
capacidade **não mudam**.

**Contra-achado, que é o custo real:** o caminho webhook nasceu *fire-and-forget* para
chamadores internos, que pegam o resultado pelo `pipeline_state`/resume — nunca por HTTP.
Logo **não existe superfície de resultado**: o trigger devolve `{session_id}` e nada mais,
e `GET /v1/channels/webhook/{session_id}/status` devolve um enum de três valores **e
responde `"closed"` quando a chave não existe** (`main.py`/`webhook.py: get_status`) — isto
é, "não sei" e "terminou" são indistinguíveis. Para consumidor interno isso passou; num
contrato externo é um valor plausível escondendo ausência, exatamente o modo de falha que a
Postura de Engenharia manda caçar. **O net-new do arco é o artefato, não o protocolo.**

---

## 3. Decisões

### D1 — A2A é um **binding**, não um `channel` novo

`channel` é **filtro duro de roteamento** (invariante). Criar `channel: "a2a"` obrigaria
todo pool exposto a declarar mais um canal e faria o roteamento depender de **quem chamou**,
que não é fato de canal — é fato de *credencial*. O trabalho continua no canal `webhook`;
A2A é a **representação externa** do mesmo endereço.

Efeito: **zero mudança no routing-engine e no `ChannelSchema`**.

### D2 — O AgentCard é **projeção** do agent-registry, nunca documento editável

Montado em request time (cache curto) a partir de `Pool` + slot `current` + descritor.
Consequências que vêm de graça:

- `AgentCard.version` = **`set_at` do slot `current`** (a mesma identidade de versão que
  `segments.deploy_version` já carimba). O contrato externo versiona junto com o deploy.
- Editar o card = editar o pool na tela. Não existe card escrito à mão (one-source).
- Promover um deploy **muda o card**. Se isso for indesejável para um parceiro, a resposta
  é pin de versão no cliente, não card congelado.

### D3 — Exposição é **opt-in por pool**, e o descritor é o portão

Bloco novo, opcional, em `PoolRegistrationSchema`:

```ts
a2a: z.object({
  exposed:       z.boolean().default(false),
  display_name:  z.string(),
  description:   z.string(),          // o que este agente faz, em prosa, para o LLM do caller
  input_schema:  z.record(z.unknown()),   // JSON Schema 2020-12
  output_schema: z.record(z.unknown()),
  skills:        z.array(A2ASkillSchema).min(1),  // id/name/description/tags/examples
}).optional()
```

`exposed: true` **sem** descritor completo é rejeitado no registro. Pool exposto sem
contrato declarado é o defeito que este bloco existe para impedir.

**O descritor se paga sozinho.** É o mesmo artefato que o *contrato delegate-por-pool*
(Business in Any Media) e a renderização de "Adicionar Especialista" no Console já pedem.
Vale construir mesmo que o binding A2A nunca saia.

### D4 — `Task` **é** a sessão. Sem entidade nova

`taskId := session_id`, `contextId := root_session_id`. Não se cria tabela, ledger ou
lifecycle de task.

Isto é aplicação direta de *"never create a wide container for a fact that fits a narrow
one"*: a `WorkflowInstance` e a entidade `Journey` já foram removidas por serem contêineres
novos para fatos que eram sessão e proveniência. Uma Task A2A é **exatamente** uma sessão, e
o `contextId` do A2A é **exatamente** a journey por proveniência. Reintroduzir um contêiner
aqui repetiria o erro pela terceira vez.

### D5 — Artefato e status honesto são pré-requisito do binding (não refinamento)

1. O `complete` passa a **persistir o resultado terminal** (outcome + payload declarado no
   step) numa chave legível por HTTP, com TTL alinhado ao da sessão.
2. `get_status` passa a distinguir **`unknown`** de `closed`. Ausência de chave é ausência,
   não conclusão.

Sem (1) não existe `tasks/get` com artefato; sem (2) o caller externo recebe "terminou" para
uma sessão que nunca existiu — e vai construir lógica em cima disso.

### D6 — O principal externo é entidade **nova**, no **auth-api**

Já existe `ChannelEndpoint` com `auth_required`, `token_hash`, rotação e procedência — mas
**um token por endpoint ≠ um principal com um conjunto de pools**. O modelo A2A é
`1 caller → N agents`. Enfiar o caller no contêiner de *endereço* seria guardar fato de um
escopo em campo de outro.

Entidade `a2a_client` no **auth-api** (domínio de identidade e autorização; one-source):
`client_id`, credencial (hash), `tenant_id`, `allowed_pools[]`, `quota`, `active`. O
AgentCard servido **a um cliente autenticado lista só os pools que ele alcança**; o card
público (`/.well-known/`) lista só os marcados como publicamente descobríveis.

### D7 — `tenant_id` **nunca** vem do corpo

Hoje `webhook_trigger_by_pool` faz `body.get("tenant_id") or settings.tenant_id`. Isso é
aceitável enquanto a rota é interna e anônima por construção — e é **cross-tenant por
construção** no instante em que a superfície é pública. No binding A2A o tenant vem
**exclusivamente da credencial**. Item de segurança, não de higiene.

### D8 — Masking: mascarado por padrão, sem opção

O caller A2A **não** entra em `authorized_roles`. `original_content` nunca sai por A2A, nem
sob configuração. Um agente externo é um consumidor não-auditável do ponto de vista da LGPD
do tenant: o que ele recebe é o que o cliente receberia.

### D9 — Capacidade não muda; cota por cliente é nova

A sessão A2A ocupa vaga de `C_ai` como qualquer sessão de pool `agent_kind: ai` — o gate de
admissão já está certo (fatia 3) e **não se toca nele**. O que falta é **cota por
`a2a_client`**, senão um parceiro consome a capacidade do tenant inteiro. Reusa
`assertQuota` do usage-metering com dimensão nova `a2a_tasks`.

### D10 — `origin` continua `live`; procedência já tem campo

Sessão dirigida por A2A é produção (`origin: live`) e **entra** na amostragem de qualidade.
Quem originou já é expressável por `spawn_reason` — não inventar discriminador novo. Na
bancada essas sessões aparecem como as do scheduler e do outbound já aparecem.

### D11 — Esgotar a graça de espera é **saída da fila**, não estado de espera exposto

Graça interna por pool (~5 s, irmão de `max_wait_s`, config de routing — não inventar store).
Se não alocou dentro dela **e o caller não declarou presença**, o item é **retirado da fila** e
a task **nunca é exposta**: resposta `503` + `Retry-After`, como se não houvesse capacidade.

Isto colapsa os dois ramos (com fila e sem fila) num só e é **totalmente conforme**: com o
abandono real, não há estado meio-morto para mentir a respeito. Não é mecanismo novo — o
caller A2A que desiste **é** o cliente que desliga esperando na fila, caminho que já existe e
está testado (`remove_queued_contact`, `outcome: abandoned`, `atomic_claim_dequeue`).

**Presença é derivada de fato observável**, como no webchat:

| Modo do caller | Presença | Comportamento na fila |
|---|---|---|
| `message/send` blocking, sem stream | nenhuma | graça, depois **sai da fila** |
| `message/stream` (SSE) | segura a linha | fica enquanto o stream viver |
| `returnImmediately: true` + polling/push | declarada (volta buscar) | fica até `max_wait_s` |

Três condições, todas obrigatórias:

1. **`close_reason` próprio** (ex.: `caller_timeout`), **nunca** `customer_abandon`. Abandono
   programático em massa sob o rótulo humano faz a taxa de abandono do pool subir e continuar
   **plausível** — o pool parece estar falhando com clientes reais e nada fica vermelho.
2. **Retirada atômica contra a alocação** (`atomic_claim_dequeue`/ZREM). Alocar em 5,1 s
   enquanto se remove entrega sessão fechada ao agente — a classe de duplicação que o arco D
   fechou.
3. **A graça não substitui a espera.** 5 s contra fila humana de 4 min abandona em ~100% dos
   casos; sem os modos de presença acima, um pool humano nunca serviria um caller A2A.

---

## 4. Borda

Prefixos novos: **`/a2a`** e **`/.well-known/agent-card.json`**, ambos **externos**.

A allowlist de sete prefixos é regra, não gosto: cada um precisa de linha na tabela do
`infra/test/probe_edge_surface.sh`, que reprova prefixo novo sem classificação. `/v1`
**continua interno** — o binding A2A não o publica; ele chama o caminho webhook por dentro.

⚠️ Lembrete que o próprio CLAUDE.md registra: a separação externo×interno é de **código**
(`allowed_origins`), não de topologia. O `/a2a` ser público não torna `/v1` menos interno,
mas também nada no repositório garante o que o deploy publica. O probe declara; o deploy
ainda não é verificado.

## 5. O que **não** muda

routing-engine · modelo de admissão e capacidade · `ChannelSchema` · skill-flow-engine ·
orchestrator-bridge · modelo de sessão/segmento/journey · MCP como único protocolo de
integração **interna** (A2A é protocolo de **borda**, na mesma classe de WhatsApp e webchat —
se alguém implementar A2A falando direto com o routing-engine, os dois invariantes caem).

## 6. Fases

| # | Fase | Entrega | Nota |
|---|---|---|---|
| **A0** | Descritor no pool | schema + registry + tela | standalone; destrava delegate-por-pool |
| **A1** | AgentCard read-only | `GET /.well-known/agent-card.json` + card por cliente | **sem execução**; força o descritor a ser honesto |
| **A2** | Principal externo | `a2a_client` no auth-api, credencial, `allowed_pools`, cota, tenant-da-credencial (D7), linha no probe de borda | **bloqueia A4** |
| **A3** | Artefato + status honesto | resultado terminal do `complete` legível por HTTP; `unknown` ≠ `closed` | o net-new que ninguém espera |
| **A4** | Binding JSON-RPC | `message/send`, `tasks/get`, `tasks/cancel` sobre o caminho webhook-pool; `input-required` ← `suspended`+resume token | adaptador |
| **A5** | Streaming | `message/stream`, `tasks/resubscribe` (SSE) sobre o stream canônico | reusa o loop do webchat |
| **A6** | Validação | cliente A2A de referência (SDK oficial) contra o demo; isolamento cross-tenant; probe de borda | gate |

**A2 antes de A4 é inegociável.** Publicar execução antes do principal externo é publicar
um disparador anônimo de pools que promovem deploy e contatam clientes.

## 7. Riscos

- **Colisão de nome.** "A2A" já significa *delegação interna* nos docs (`task` step,
  `assist`/`transfer`) e **não é** o protocolo Agent2Agent. Renomear o uso interno para
  "delegação" antes de A1, ou os docs passam a ter dois A2A.
- **Diferencial não é durável.** A análise competitiva de jul/2026 concluiu que MCP virou
  infraestrutura e o que sobrou foi o *gateway de governança*. A2A segue o mesmo caminho
  (v1.0 abr/2026, Linux Foundation, 150+ orgs). Tratar como binding; o fosso continua sendo
  fila + capacidade + qualidade medida + auditoria — que é justamente o que um endpoint A2A
  de framework **não** tem, e é o argumento de venda deste arco.
- **Pressão para expor pool humano.** Ver §8.
- **Card muda no promote** (D2). Documentar para parceiros antes do primeiro contrato.

## 8. Fora de escopo (explícito)

- **Pools humanos / de contato via A2A.** Tecnicamente atraente (agente externo falando com
  uma fila de especialistas humanos, com `input-required` a cada turno) e provavelmente o
  produto mais diferenciado que existe aqui — mas arrasta espera de fila, masking por turno,
  SLA e cobrança por atendimento. **Fase 2**, e a decisão então será *"reusa `webchat`"*,
  não *"cria canal a2a"*.

  **Achado que reordena o roadmap (2026-08-13):** o A2A é **blocking por padrão**
  (`returnImmediately` unset ⇒ o servidor *MUST wait* até estado terminal ou interrompido —
  spec §3.2.2). Numa fila humana com espera de minutos, o único caminho **conforme** é
  manter a conexão aberta — logo **A5 (streaming) deixa de ser fase opcional e vira
  pré-requisito da fase 2**. Devolver cedo violaria o MUST; devolver `503`+`Retry-After`
  transfere a espera para o caller. Ver §9.
- **PlugHub como cliente A2A.** Um agente externo não tem
  `agent_login → ready → busy → done`, heartbeat, semáforo de vaga, pausa nem interceptação
  MCP. Modelá-lo como pool exigiria inventar capacidade para recurso que não é nosso —
  colisão frontal com o arco de capacidade (moedas não-fungíveis). Se um dia entrar, entra
  como **primitivo `invoke`** (tool cliente A2A), nunca como pool.
- **Runtime importado** (agente de terceiro rodando **como pool**, via SDK de certificação +
  `plughub-sdk proxy`). **Fora por decisão de produto, não por custo:** importar pede que a
  plataforma garanta capacidade, heartbeat, pausa, contrato `agent_done` e auditoria
  não-optável sobre código que ela não controla — corrói a camada de governança que é o
  diferencial. Este ADR **padroniza** a fronteira em vez de dissolvê-la. Não confundir com
  **`external-mcp`** (expor *tool*, única borda em vigor, **fica**) nem com **portabilidade**
  (`certify`/`skill-extract`, sustenta o "sem lock-in", **fica**, e A2A **não** a cobre).
  Reabrir só com demanda comercial nomeada.
  Ver [`docs/product/agentes-externos-reclassificacao.md`](../product/agentes-externos-reclassificacao.md).
- **Binding gRPC** (só JSON-RPC sobre HTTPS + REST no v1).
- **Assinatura do card** (JWS/`AgentCardSignature`) — quando houver parceiro que exija.
- **Push notifications** de volta ao caller — v1 é polling + SSE.

## 9. Indisponibilidade de recurso e espera — o que o protocolo dá (e o que não dá)

Medido na spec v1.0 (`a2a-protocol.org/latest/specification`).

**Não existe** estado `queued`, posição de fila, ETA, nem timeout de espera negociável pelo
cliente. O `TaskState` é fechado em nove valores. Mas os assentos existem:

| Situação PlugHub | A2A | Nota |
|---|---|---|
| enfileirado aguardando agente | `TASK_STATE_SUBMITTED` | *"successfully submitted and acknowledged"* — é o único estado de "aceita, ninguém pegou"; não carrega posição |
| alocado, agente atendendo | `TASK_STATE_WORKING` | |
| agente pede dado ao caller | `TASK_STATE_INPUT_REQUIRED` | estado *interrompido*, não terminal |
| `no_resource` (sem agente e sem fila) | `TASK_STATE_REJECTED` | a spec prevê rejeição **na criação**: *"may be done during initial task creation"* |
| `max_wait_exceeded` | `TASK_STATE_FAILED` | **não** `REJECTED`: a task foi aceita e depois expirou |
| admissão negada por cota | `503` + `Retry-After` | task **não nasce**; a spec autoriza (*"MAY include retry guidance"*) |
| caller desiste | `tasks/cancel` → `TASK_STATE_CANCELED` | exige `close_reason` novo do lado de cá (**não existe** no domínio hoje) |

**Timeout de espera pelo recurso não é parâmetro de chamada — e isso está certo.** No A2A o
cliente só escolhe *bloquear ou não* (`returnImmediately`), nunca *por quanto tempo esperar*.
O timeout de espera é `pool.queue_config.max_wait_s` — propriedade **do agente**, publicada
no descritor/AgentCard ("este agente pode demorar até X"), não do chamador. Alinhado ao
princípio *Opaque Execution*.

**Posição de fila**, se desejada, vai como `Message` dentro do `TaskStatusUpdateEvent` do
stream — interoperável e ignorável por quem não liga. O `queue.position_updated` do routing
já produz o dado. **Não** usar `Extension` da AgentCard para isso: extensão que o cliente
não entende é ruído, e `required: true` quebraria interop.

**Assimetria a dimensionar: um caller A2A não é uma pessoa esperando.** Não abandona por
tédio — reenvia, de graça, em loop. Consequências: back-pressure explícito
(`503`+`Retry-After`) deixa de ser cortesia e vira requisito; `max_wait_exceeded`, raro em
contato humano, vira o caso comum se a cota do `a2a_client` (D9) não for **menor** que a
capacidade do pool; e a bancada mistura "cliente" com "agente de parceiro" no TMA/SLA do
pool — `spawn_reason` hoje não distingue caller A2A de trigger interno.

## 10. Referências

- [A2A Protocol Specification](https://a2a-protocol.org/latest/specification/) ·
  [a2aproject/A2A](https://github.com/a2aproject/A2A)
- Repo: `docs/arcos/arc19-unified-session-model.md` (Task↔sessão),
  `docs/adr/adr-journey-session-segment-model.md` (D4),
  `docs/adr/adr-webhook-endpoint-single-registry.md` (D6),
  `docs/guias/webhook-patterns.md` § Exposição na borda (§4),
  `docs/product/competitive-analysis-2026-07.md` (§7).


---

## Apêndice — resumo denso migrado do índice do `CLAUDE.md` (2026-08-31)

> Este bloco vivia como **uma linha** do índice `docs/` no `CLAUDE.md`, onde ocupava 1237 bytes.
> Medido antes de mover: **~85% do seu vocabulário já existe neste ADR** — ele é uma condensação
> independente, não uma cópia, e por isso os ~15% restantes (achados, números e nomes de arquivo que
> só foram registrados no índice) **não existiam em lugar nenhum além dali**. Movido inteiro, sem
> resumir, porque a alternativa — cortar no CLAUDE.md e confiar que o ADR já dizia tudo — perderia
> exatamente a fração que não dá para recuperar.
>
> **É trabalho aberto**, não documentação final: a fração nova deve ser dobrada no corpo do ADR e
> este apêndice, encolhido. Enquanto isso não acontece, ele é a única cópia.

PlugHub como **servidor** A2A: binding de borda sobre pool+sessão, sem motor novo. `Task`=sessão (`taskId`=`session_id`, `contextId`=`root_session_id` — não criar contêiner, é o erro da `WorkflowInstance`/`Journey` pela 3ª vez); AgentCard = PROJEÇÃO do agent-registry (`version`=`set_at` do slot ⇒ contrato externo versiona junto com o deploy); A2A é **binding, não `channel`** (canal é filtro de roteamento; quem chamou é fato de CREDENCIAL) ⇒ zero diff no routing. Net-new real **não é o protocolo, é o ARTEFATO**: o caminho webhook nasceu fire-and-forget (trigger devolve só `{session_id}`) e `get_status` responde `"closed"` quando a chave não existe — "não sei" indistinguível de "terminou". Principal externo = `a2a_client` no auth-api (token por endpoint ≠ caller com N pools), `tenant_id` **nunca do corpo** (hoje vem), masking sem opção, cota `a2a_tasks`. Fases A0 descritor → A1 card read-only → **A2 principal (bloqueia A4)** → A3 artefato+status honesto → A4 JSON-RPC → A5 SSE → A6 validação. FORA: pool humano (fase 2, será `webchat`), PlugHub como CLIENTE (seria `invoke`, nunca pool — inventaria capacidade de recurso alheio) — proposto
