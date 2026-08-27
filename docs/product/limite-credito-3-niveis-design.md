# Aumento de limite de crédito — cenário de referência do modelo de 3 níveis

> **Status:** design. §1–§5 são desenho fechado; §6 é o fatiamento; §7 são dívidas que o cenário
> expõe. Escrito em 2026-08-11.
> **Motivação:** hoje **não existe nenhum skill-flow as-built com os 3 níveis separados**. O padrão real
> é de 2 níveis (workflow de processo + agente de I/O), e o `agente_portabilidade_intake_v1` é (b) e (c)
> **fundidos**. Este cenário é a primeira materialização do nível (b) como artefato.
> **Referência-mãe:** [`business-in-any-media-arquitetura-alvo.md`](business-in-any-media-arquitetura-alvo.md)
> — que declara (b) como *"parcial / a consolidar — as peças existem dispersas, mas não como camada
> explícita e reusável"* (linha 26).

---

## 0. Veredito de prazo — o que cabe em menos de uma semana

O cenário completo é **2–4 dias de trabalho de quem conhece a base**, mais o ritual de deploy
(seed-if-absent → `PUT` com `x-skill-publish` → `set-next` → `promote`). Colocá-lo no caminho crítico de
uma demo desta semana é apostar a apresentação num build novo.

**Recomendação: a demo desta semana roda a Fase 0** — que entrega **a tese inteira** (fila pull +
aprovação humana + mascaramento por política + os dois retomadores) **sem nenhum skill novo**, enxertando
um `delegate` de aprovação no fluxo de portabilidade que já funciona. Custo: ~4–6 h.
O cenário de limite entra como Fases 1–3, depois da demo, com este desenho pronto.

Se a decisão for construir o limite mesmo assim, o corte mínimo honesto é a **Fase 1** — e ela **depende**
de uma correção prévia (§7.1), sem a qual o preview de retomada vem vazio e o acesso #2 não funciona.

---

## 1. Os três níveis, e por que N2 não é workflow

| Nível | Responsabilidade | Perfil | Artefato neste cenário |
|---|---|---|---|
| **N3 · (a)** processo negocial | A regra de negócio, **abstraída de canal**. Não sabe por onde o cliente falou. | `workflow` (pool `webhook`) | `skill_limite_processo_v1` |
| **N2 · (b)** acesso a canal | Resolve identidade, localiza processo pendente, oferece retomada, **decide/concilia canal**. | **`agent`** (fino) | `skill_acesso_canal_v1` |
| **N1 · (c)** I/O no canal | Render nativo, captura de input, mídia. Não conhece o negócio. | `agent` + adapter | `skill_limite_intake_v1`, `skill_limite_retorno_v1` |

### N2 é perfil `agent`, não `workflow` — três razões

1. **Segregação de perfil torna um N2-workflow inútil.** `menu`/`notify` são exclusivos do perfil
   `agent`; `suspend`/`collect` são exclusivos do perfil `workflow`. Um N2 em perfil `workflow` **não
   consegue falar com o cliente** — e falar com o cliente para conciliar canal é precisamente o que N2
   existe para fazer.
2. **Não há caminho de "N3 chama N2 e espera".** `delegate` é A2A para **agente** e cria conference
   specialist na sessão do chamador (`delegate-workflow-io.md`, v2). `delegate` a pool webhook foi
   **explicitamente aposentado** (idem, linhas 94-98). Workflow→workflow só por `workflow_trigger`
   (fire-and-forget, não espera) ou `task` (polling, e o alvo ainda é `skill_id`, não pool).
3. **Decisão de canal é fato de step, não processo.** Ela já existe declarativa em
   `collect.channel_policy` (`skill.ts:590-614`), resolvida por `_negotiate_channel`
   (`channel-gateway/adapters/webhook.py:1736-1791`). Promovê-la a workflow é criar contêiner largo para
   fato estreito — o invariante que o CLAUDE.md nomeia.

> **Limite honesto:** a negociação de canal roda, mas é **determinística** hoje. `_reachable_channels`
> retorna `[]` hardcoded (`webhook.py:1723-1734`), consentimento e política de tenant são slots vazios,
> `urgency` é parseado e nunca lido, e a entrega é mock (`link = f"/survey/{collect_token}"`,
> `webhook.py:1953`). Na prática elege `preferred_order[0]`. **Não prometa eleição inteligente de canal
> numa demo.**

### Correspondência com os três níveis de observabilidade

Vale dizer em voz alta, porque fecha bonito e é verdade: os três níveis do **processo** produzem
exatamente os três níveis de **medição**. N3 gera a journey (contatos sob a mesma raiz), cada acesso do
cliente é uma session, e cada participante dentro dela é um segment. Não é coincidência de nomenclatura —
é a mesma decomposição vista da execução e da análise.

---

## 2. O cenário — três acessos do cliente

### Acesso 1 · solicitar

O cliente entra pelo webchat, N2 resolve identidade e não encontra pendência, N1 coleta o formulário,
N2 dispara N3 com os dados.

**Ponto de desenho que não é óbvio:** a coleta acontece em **N1, na sessão do cliente** — *antes* de N3
existir. `workflow_trigger` é fire-and-forget e cria uma sessão `webhook` nova; um `delegate` feito por
N3 rodaria como specialist **na sessão do N3**, não na do cliente, e o formulário não apareceria para
ninguém. É por isso que o `agente_portabilidade_intake_v1` coleta tudo e só então dispara (`:480`).
**N3 recebe dados; N3 não coleta.**

Formulário (`dialog_limite_solicitacao`), um único turno via `menu` `interaction: form`:

| campo | masked? | destino |
|---|---|---|
| `numero_cartao` | não | tag `session.numero_cartao`, mascarada **por política** na leitura |
| `cpf` | não | tag `session.cpf`, idem |
| `limite_atual` | não | tag `session.limite_atual` |
| `limite_solicitado` | não | tag `session.limite_solicitado` |
| `cvv` | **sim** (`masked: true`) | `@masked.cvv` — memória do processo, **nunca persistido** |

### Acesso 2 · "ainda está em andamento"

O cliente volta enquanto o aprovador não agiu. N2 resolve identidade → `pending_workflow_get` encontra
a pendência do `delegate` de aprovação, com `policy: offer` → menu:

```
📋 Consultar status        → notify com pendings[0].intent, complete.  NÃO retoma.
❌ Cancelar solicitação    → workflow_resume(decision: rejected).      Retoma no on_reject.
🔄 Novo atendimento        → segue o fluxo normal.
```

**Este é o ponto que não existia** e a solução que o faz funcionar sem build: o status de negócio viaja
como `intent` no `delegate.context`, e volta em `pendings[0].intent`. Não há `workflow_status_get`, e
`pending_workflow_get` não devolve etapa nem progresso — a pendência é binária. `intent` é o único campo
livre que atravessa.

⚠️ `intent` **não está no achatamento** de `find_pending_by_customer` (`webhook.py:2253-2261`), que só
promove `resume_token`/`pool`/`policy`/`context`/`root_session_id`. O YAML precisa indexar o array:
`$.pipeline_state.pendencia.pendings[0].intent`. Funciona (o engine usa `jsonpath-plus`), embora o
docstring desencoraje.

### Acesso 3 · resultado

Depois da decisão, N3 parqueia o resultado num `delegate` `customer_resumable: true` com
`resume_policy: **auto**`. O próximo inbound do cliente retoma **direto**, sem menu, e N1 notifica o
resultado.

**A distinção entre o acesso 2 e o 3 sai de graça:** o `choice avaliar_politica_retomada` que já existe
no intake ramifica por `policy` — `offer` (em análise, o cliente escolhe) × `auto` (resultado pronto,
entrega direta). Zero maquinaria nova.

### Os dois retomadores — o melhor momento técnico do cenário

Entre o acesso 1 e a decisão, o **mesmo workflow suspenso** tem dois retomadores possíveis: o aprovador
(pelo Console) e o cliente (cancelando). Isso não é acidente do desenho — é regra de negócio real: o
cliente pode desistir enquanto está em análise.

E é seguro por causa da **Camada F** (resume terminal-uma-vez): `SET NX` no topo de `handle_resume`,
registro `{t}:resume_terminal:{token}` gravado antes do consumo, e a recusa **nomeada** — token ausente
com registro → **409**, sem registro → 404 honesto. Se aprovador e cliente agirem no mesmo segundo, um
recebe um erro que diz *o que aconteceu*, não "token não encontrado". Vale encenar ao vivo.

---

## 3. Contratos entre os níveis

**N2 → N3** (`invoke workflow_trigger`):

```yaml
pool_id: limite_processo
origin_session_id: "$.session_id"          # a raiz da journey se propaga daqui
context_json:
  customer_id:        "@ctx.caller.customer_id"
  contact_identifier: "$.pipeline_state.contato"   # necessário: define as âncoras do pending
  numero_cartao:      "$.pipeline_state.form.numero_cartao"
  cpf:                "$.pipeline_state.form.cpf"
  limite_atual:       "$.pipeline_state.form.limite_atual"
  limite_solicitado:  "$.pipeline_state.form.limite_solicitado"
```

⚠️ `contact_identifier` **não é opcional**: `_anchors_from_context` (`webhook.py:2136-2151`) deriva dele
as âncoras sob as quais o pending é indexado. Sem ele, o pending existe mas o cliente nunca o encontra.

**N3 → aprovador** (`delegate` ao pool):

```yaml
type: delegate
pool: aprovacao_credito
customer_resumable: true
resume_policy: offer
context:
  title:          "Aumento de limite — análise"
  summary:        "@ctx.approval.summary"          # texto SEM dado sensível
  dialog_form_id: "dialog_limite_aprovacao"
  decisions:      '[{"id":"aprovar",...},{"id":"recusar",...}]'
  intent:         "Sua solicitação está em análise pelo time de crédito"
  numero_cartao:  "@ctx.session.numero_cartao"     # tags separadas, NÃO no summary
  cpf:            "@ctx.session.cpf"
  limite_atual:      "@ctx.session.limite_atual"
  limite_solicitado: "@ctx.session.limite_solicitado"
on_resume:  { next: avaliar_decisao }
on_reject:  { next: encerrar_cancelado_cliente }
on_timeout: { next: encerrar_timeout }
```

Duas propriedades que fazem isso funcionar:

- **`delegate.context` É interpolado** (`delegate.ts:206-218`, `resolveInputMap`) — ao contrário do
  DialogForm, que não interpola nada (§4).
- O gateway grava cada chave com prefixo `session.` no ContextStore da sessão-filha
  (`webhook.py:1572-1586`). É isso que põe os valores ao alcance da política de masking.

**Fluxo N3 completo** — 5 steps, espelhando `skill_portabilidade_demo_v1`:

```
entry: montar_pacote
  montar_pacote        invoke context_set → approval.summary  (+ journey.limite_status)
  aprovar              delegate → aprovacao_credito           (acima)
  avaliar_decisao      choice sobre $.pipeline_state.aprovar.decision
  parquear_resultado   delegate → limite_retorno, customer_resumable: true, resume_policy: auto
  encerrar_*           complete (aprovado | recusado | cancelado_cliente | timeout)
```

---

## 4. Mascaramento — o que a política entrega, e o que ela não entrega

**Requisito adotado: política por tag × role.** O dado vai ao ContextStore; a tela aplica
`masking.context_rules`; mudar a regra é mudar config, não fluxo.

### O caminho que funciona hoje, com zero código

1. N3 passa cada campo sensível como **tag própria** no `delegate.context` (não embutido no `summary` —
   uma string é um valor só, e a regra é por tag).
2. O gateway grava como `session.numero_cartao`, `session.cpf`, … na sessão de aprovação.
3. Em `/config/masking`, acrescentar as regras:

   | pattern | role | type | render |
   |---|---|---|---|
   | `session.numero_cartao` | operator | `last_4` | `***4321` |
   | `session.cpf` | operator | `last_2` | `***01` |
   | `session.limite_solicitado` | operator | `financial` | valor redigido |
   | `*` | supervisor | `plain` | (já existe no seed) |

4. `GET /api/supervisor_state` aplica `applyContextMaskingDynamic` (`server.ts:1054-1125`) e a **aba
   Contexto** do Console renderiza mascarado, com badge `🔒 PII` (`ContextoTab.tsx:460-468`).
5. O aprovador vê o **formulário na coluna central** (DialogFormRenderer) e os **dados mascarados na aba
   Contexto**, à direita, na mesma tela.

**Prova ao vivo em 20 segundos:** logar como `operator` → `***4321`; logar como `supervisor` → valor em
claro. Mesma tela, mesma sessão, política diferente. É exatamente "mascaramento é config, não código".

### ⚠️ Duas armadilhas

- **Nunca configure `default_unmatched_operator: "hidden"`.** `applyContextMaskingDynamic` faz `continue`
  em campo `hidden` (`server.ts:1104-1107`), o que derruba `session.dialog_form_id`, `session.decisions` e
  o resume token. `isApprovalSnapshot` (`ApprovalPanel.tsx:51-53`) devolve `false` e **a tela de aprovação
  simplesmente não renderiza**. A política de masking pode quebrar a fila de aprovação em silêncio.
- **O `summary` não é mascarado por regra** — é uma tag só. Trate-o como texto público.

### O que a política NÃO alcança — e por que está certo assim

| Mecanismo | Alcança o aprovador? | Por quê |
|---|---|---|
| Tag do ContextStore + `context_rules` | ✅ mascarado por role | é o caminho acima |
| **Masked Input** (`masked: true`, `@masked.*`) | ❌ **nem mascarado nem em claro** | vive em `engine.ts:518`, variável local do processo; zerada no `end_transaction`; nunca serializada. É **invariante declarada** (`masked-input.md:391-393`), não pendência |
| **DialogForm `masked`** | ❌ descartado | existe no schema (`dialog.ts:255,314`), e a interface do renderer (`DialogFormRenderer.tsx:104-111`) **não tem o campo**. Um contrato que mente — ver §7.3 |
| **Token vault** | ❌ ninguém resolve | `TokenVault.resolve` existe (`token-vault.ts:110`), mas o store que alimenta o briefing nunca foi tokenizado e o renderer imprime texto cru |

**Use o CVV a favor da narrativa.** Ele é coletado com `masked: true` e é **provadamente inalcançável**
por qualquer aprovador — o oposto do cartão, que é mascarado por política mas recuperável por quem tem
role. Dois requisitos diferentes, dois mecanismos diferentes, ambos na mesma tela: *"este eu escondo de
quem não tem papel; este eu esqueci."*

---

## 5. Reuso × build novo

| Peça | Estado |
|---|---|
| Pool de aprovação humana em fila pull | ♻️ **`aprovacao_deploy` já existe** (`tenant_demo.yaml:179-186`): `human`, `pull`, **sem deploy/skill**. O form vem do `delegate.context` — nenhum skill de aprovação é necessário. Criar `aprovacao_credito` é 8 linhas de YAML, só para não misturar semântica |
| Renderer de aprovação | ♻️ `DialogFormRenderer` + `ApprovalPanel` (ABAC `approvals.operacao` / `approvals.decide`) |
| Fila pull direcionada, lease, transbordo | ♻️ Camada B do arco de detach |
| Retomada por identidade + OTP | ♻️ Resolvedor Fase A/B completo |
| Lock de resume terminal-uma-vez | ♻️ Camada F |
| Journey por proveniência | ♻️ `origin_session_id` → `root_session_id` |
| Masking por tag × role | ♻️ motor pronto; **build = 3 linhas na página `/config/masking`** |
| `skill_limite_processo_v1` (N3) | 🔨 ~80 linhas, espelho de `skill_portabilidade_demo_v1` |
| `skill_limite_intake_v1` (N1 coleta) | ♻️🔨 **não escrever um coletor novo** — estender `skill_dialog_runner_v1` com ramo multi-campo (§8). ~20 linhas no runner, e serve todo domínio futuro |
| `skill_limite_retorno_v1` (N1 resultado) | 🔨 ~40 linhas, espelho de `agente_confirmacao_portabilidade_v1` |
| `skill_acesso_canal_v1` (N2 genérico) | 🔨 ~250 linhas — extração do intake de portabilidade, parametrizada por `$.config` |
| `dialog_limite_solicitacao` + `dialog_limite_aprovacao` | 🔨 2 JSONs em `infra/dialog/` |
| Preview de retomada genérico | 🔨 **pré-requisito** — ver §7.1 |

---

## 6. Fatiamento

**Fase 0 — o gate de aprovação sobre portabilidade** *(~4–6 h; é o que cabe na semana)*
Um `delegate` ao pool `aprovacao_deploy` enxertado em `skill_portabilidade_demo_v1`, entre o suspend da
operadora e a confirmação, + 1 DialogForm + as regras de masking. Entrega fila pull, aprovação humana,
mascaramento por política e os dois retomadores. **Nenhum skill novo.**
Validação: o item aparece na inbox pull, o aprovador vê `***4321` como operator e em claro como
supervisor, e a decisão retoma o processo.

**Fase 1 — o cenário de limite, 2 níveis** *(~2 dias; depende de §7.1)*
N3 + os dois N1, com o intake de portabilidade **clonado** e adaptado como ponto de entrada. Os três
acessos funcionam. N2 ainda é (b)+(c) fundido — assumido e registrado.
Smoke: `smoke_limite_tres_acessos.sh` — solicitar, consultar status sem retomar, retomar com resultado.

**Fase 2 — extrair o N2 genérico** *(~2 dias; é o artefato do nível (b))*
`skill_acesso_canal_v1`, domain-agnostic, parametrizado por `$.config` (`processo_pool`,
`form_id`, `menu_intents`). Portabilidade **e** limite passam a entrar por ele. É aqui que o nível (b)
deixa de ser implícito, e é o único item desta lista que vale por si, fora da demo.
Smoke: os dois domínios entrando pelo mesmo N2, com config diferente.

**Fase 3 — correções estruturais** *(independente; §7.2 e §7.3 valem sem o cenário)*

---

## 7. Dívidas que o cenário expõe

### 7.1 `_pending_context_preview` é hardcoded para portabilidade — **bloqueia a Fase 1**

`webhook.py:2117-2133` só reconhece as chaves `operadora_destino` e `numero_atual`, e **descarta
silenciosamente todo o resto**. Um preview de limite viria **vazio**, e o menu de continuidade mostraria
campos em branco. Correção: mapa de preview declarativo (chave → tipo de máscara) vindo do
`delegate.context`, em vez do `if` por domínio. ~20 linhas.

### 7.2 `/api/conversation_history/:sessionId` — exposição presente, não pendência de feature

`server.ts:1765` não tem JWT e não aplica masking. Lê `session:{id}:messages`, store que o
channel-gateway escreve com o **texto cru** do cliente (`webchat.py:665`) — o `MaskingService` só toca o
stream canônico (`tools/session.ts:472`), que é outro store. E `DialogFormRenderer.tsx:476` renderiza
`{m.text}` sem `renderWithTokens`, embora o componente exista (`MaskedToken.tsx:139`).

**Consequência hoje, em produção:** o briefing do wrap-up mostra em claro qualquer PII que o cliente
tenha digitado em texto livre. É o achado mais sério deste levantamento e **não depende deste cenário**.
Correção: `requireJwtRole` + masking na leitura (ou tokenizar na escrita) + `renderWithTokens` no
briefing — a segunda é uma linha.

### 7.3 `masked` no DialogForm é um contrato que mente

Aceito no schema (`dialog.ts:255,314`), propagado pelo `form_get` (`tools/dialog.ts:117,134`), e
**descartado** pelo renderer do Console. Quem lê o schema conclui que há suporte. Correção: campo na
interface `DialogFormField` + ramo `type="password"` em `renderField` (`:384`). ~10 linhas.

### 7.4 `collect` não gera pending

O engine envia `customer_resumable`/`resume_policy` no `persistCollect` (`collect.ts:192-193`), mas
`POST /v1/channels/webhook/collect` **não lê** os campos (`main.py:977-998`) e `handle_collect` nem os
tem na assinatura. **Só `delegate` parqueia pendência** — por isso todo parking neste desenho é
`delegate`. Fechar a assimetria ou documentá-la no schema.

### 7.5 Chave do pending é `session_id`, não step

`identity/index.py:266` — dois `delegate` em sequência no mesmo workflow **sobrescrevem-se**. Há N
pendências por cliente apenas quando são N sessões distintas. E o intake as-built lê só o achatado (o
mais recente), sem menu de desambiguação em lugar nenhum. **Se o cliente da demo tiver pendência de
portabilidade e de limite ao mesmo tempo, a mais recente vence em silêncio.**

### 7.6 Enforcement de perfil não localizado em código

A segregação `workflow` × `agent` está documentada (`CLAUDE.md:1115`, `arc19:257-261`) mas a
allowlist/denylist **não foi encontrada como código** — `validateFlow` (`engine.ts:268`) valida só ciclos
não-guardados. Trate como convenção + disciplina de autoria até confirmar. Numa demo técnica alguém vai
perguntar *"o que me impede?"* — melhor saber a resposta antes.

---

## 8. O coletor como agente especialista

Instinto certo, base errada: **estender `skill_dialog_runner_v1`, não bifurcar `auth_form_ia`.**

### A capacidade existe inteira — e nenhum skill a usa

| Elo | Onde | Estado |
|---|---|---|
| `question` com N `fields[]`, `masked` por campo | `dialog.ts:313`, `:258` | ✅ |
| `form_get` emite `render.fields[]` propagando `masked` | `tools/dialog.ts:110-127`, `:117` | ✅ |
| `menu.fields` aceita união `array \| ref` | `skill.ts:473-487` | ✅ |
| engine honra `masked` por campo | `menu.ts:162`, `:444-454` | ✅ |
| cadeia até `<input type="password">` no webchat | `menu.ts:181` → `WsMenuRender.masked_fields` | ✅ |

**Mas o único skill as-built com `interaction: form` é `agente_auth_form_v1`**, com fields estáticos inline
(`:75-86`). `render.fields` não é consumido por nenhum YAML. A peça genérica existe na plataforma e não
existe no catálogo.

### Por que o runner e não o `auth_form`

`skill_dialog_runner_v1` já recebe `dialog_form_id` por `delegate.context` e já devolve por
`workflow_resume`. Acrescentar um ramo de formulário é **um `choice` + um `menu interaction: form` +
payload como mapa**. O contrato escalar `payload = {value}` é **convenção do YAML, não limite da
plataforma**: `workflow_resume.payload` é `z.record(z.unknown())` (`workflow.ts:262`) e o `delegate`
devolve o payload inteiro como `output_value` (`delegate.ts:66-72`).

`auth_form_ia` exigiria cinco consertos e entregaria um segundo runner:

1. não tem `config_params` — fields são estáticos;
2. termina em **`escalate`**, que é o bug documentado em `TODO.md:3853`: *especialista de conferência
   que termina com `escalate` re-roteia o CONTATO* em vez de voltar ao chamador;
3. **`context_tags` no step `menu` é config morta** — `MenuStepSchema` (`skill.ts:424-557`) não tem o
   campo, o Zod faz strip, e `menu.ts` nunca o lê. As tags `caller.email` declaradas em `:87-92`
   **nunca são escritas**;
4. validação hard-coded em `mcp-server-auth/validate_pin` com `@masked.senha`;
5. bloco de retry duplicado literal.

> ⚠️ **Retry por formato não funciona em `interaction: form`.** `menu.ts:156` desabilita o reprompt na
> mesma superfície (`resolvedInteraction !== "form"`). O laço de re-coleta continua explícito no YAML —
> é por isso que o `auth_form` tem o bloco duplicado. Vale para qualquer coletor multi-campo.

### `@mention` × `delegate` — a diferença é decisiva

| | `@mention` | `delegate` |
|---|---|---|
| Retorno estruturado | **não existe** | `payload` arbitrário → `$.pipeline_state.<step>` |
| Mecanismo | `mention_commands` tem só `set_context` (valores **literais** no YAML — nem aceita ref para dado coletado), `trigger_step`, `terminate_self` (`mention-commands.ts:104-136`) | `workflow_resume` com token injetado pelo step |
| Suspende o chamador | não (primary segue livre; especialista em `standby`) | sim |

**Um especialista invocado por menção não consegue devolver um formulário.** Só publica por steps que
suportam `context_tags` — `invoke`/`reason`/`notify`/`resolve`, **nunca** `menu`.

**E aí está a convergência com §4:** o desenho de mascaramento por política **precisa** dos valores como
tags do ContextStore de qualquer jeito. Um coletor invocado por menção que termine em `invoke
context_set` por campo entrega o canal de retorno **e** o substrato de masking na mesma tacada — e
escreve na sessão do cliente, já que o especialista roda como conference specialist na sessão do
chamador.

**Orquestrador IA:** o caminho canônico de uma IA para acionar especialista é `task mode: assist` ou
`delegate`, **não** `@mention` — o gate de menção é por role (`primary`|`human`), falha fechado, e o
protocolo registra *"IA usa `task mode: assist`"*. Confirme antes de apostar numa menção emitida por IA
no palco.

---

## 9. Survey de dois grãos e quarentena, no mesmo fluxo

### Dois grãos: sim — ~15 min de preparo, zero código

- **`grain=segment`** nasce **só** do hook `nps_ia` inline, e **exige humano no contato**:
  `fire_pool_hooks` (`orchestrator-bridge/main.py:1672-1741`) lê `session:{sid}:human_seg:{instance}` e
  semeia `session.surveyed_segment_id`. Contato só-IA cai para `grain=session` — o `choice` de
  `agente_nps_v1.yaml:101-107` é **exclusivo**.
- **`grain=journey`** nasce **só por link web** hoje. O caminho collect J4c (`skill_survey_runner_v1`)
  **não tem pool** no `tenant_demo.yaml` → inerte.
- **Nenhum skill as-built grava dois grãos.**

Caminho curto: contato com humano → NPS inline dá o sinal de **segmento**; em seguida, com a **mesma
raiz**:

```bash
curl -X POST http://localhost:8010/v1/survey/web/create \
  -H 'content-type: application/json' \
  -d '{"tenant_id":"tenant_demo","form_id":"dialog_nps_buttons",
       "origin_session_id":"<sessão ou raiz canônica>","grain":"journey",
       "pool_id":"retencao_humano"}'
# abrir /survey/{token} no browser e submeter
```

É exatamente o que `smoke_outbound_fase5b.sh:80-92` faz no final — pula mailing, campanha, dispatcher e
worker. Em `/analise/customer-voice`, alternar o seletor `grain` com `metric=nps` mostra as duas séries.

> ⚠️ **Use `dialog_nps_buttons`, não `dialog_survey_multi_v1`.** A via web lê **apenas** `capture.metric`
> e **ignora `dimensions`** (`survey_web.py:601-618`) — o CSAT composto não sairia. A composição
> server-side (`composeScore`, `scoring.ts:86-114`) funciona, mas só no caminho `survey_record`.

### Quarentena: existe, e não está onde se espera

- **`survey_eligibility_check` NÃO EXISTE** — substituído por decisão de 2026-07-21 pelo motor genérico
  **`contact_eligibility_check`** (`tools/outbound.ts:263-304` → `mailing-api` `db.py:830-942`).
- Ele **funciona sem `campaign_id`**, caindo na policy de escopo **tenant** (`db.py:797-802`) — que é
  precisamente o que um hook inline precisaria. Prova pronta: `smoke_outbound_fase2.sh:21-44`.
- **`agente_nps_v1` não chama gate nenhum.** O NPS pós-atendimento roda **sem quarentena** hoje.
- Precedência: `opt_out` → `outside_window` → `quarantine` → `frequency_cap` → `channel_cap`. Retorno é
  **`allowed`** (não `eligible`), com `reason`, `retry_after` e `claimed`.
- **Semântica de `quarantine_after`**: *"não contatar de novo por N tempo após **qualquer** contato"* —
  um cap global max=1 na janela. **Não** é "após N contatos".

> ⚠️ O parser de janela degrada lixo para **0**, e `0` **desabilita a regra** com apenas um WARN
> (`db.py:671-692`). Regra desligada é indistinguível de regra cumprida — exatamente o modo de falha que
> a Postura de Engenharia nomeia.

**Duas formas de mostrar:**

| Custo | Como |
|---|---|
| **zero** | O gate **já roda no fluxo real** do survey outbound (`skill_outbound_survey_worker_v1.yaml:38-49`, antes do `survey_link_create`). Rodar a campanha duas vezes para o mesmo cliente dá `skipped_ineligible` com `reason` nomeado. `smoke_outbound_fase2b.sh` prova isso **no fluxo**, não na API |
| **~1–2 h** | `invoke contact_eligibility_check` em `agente_nps_v1` + uma `contact_policy` scope=tenant. Mas mexe em skill deployada por slot: `PUT` com `x-skill-publish` → `set-next` → `promote` no `nps_ia`. **Não faria isso na semana da demo** |

---

## 10. O que não fazer

- **Não fazer N2 virar workflow.** §1. Perde I/O por contrato e cria contêiner largo para fato estreito.
- **Não usar `delegate` para pool webhook.** Caminho aposentado (`delegate-workflow-io.md:94-98`).
- **Não aninhar `delegate` dentro de `delegate`.** `session.delegate_resume_token` é session-scoped;
  aninhar foi **rejeitado por decisão**. Uma sessão é single-threaded.
- **Não tentar mostrar valor de Masked Input ao aprovador.** Exigiria revogar a invariante, com ADR
  próprio. O requisito real quase sempre é o de política por tag × role — que é outro mecanismo.
- **Não prometer eleição inteligente de canal.** Alcançabilidade e consentimento são slots vazios e a
  entrega é mock.
- **Não bifurcar `auth_form_ia` num segundo coletor genérico.** §8 — cinco consertos, e o resultado é um
  runner concorrente. Estender o `dialog_runner` é menor e consolida.
- **Não esperar retorno estruturado de `@mention`.** Não existe canal. Um especialista de menção publica
  por `context_tags` em `invoke`/`reason`/`notify`/`resolve` — nunca em `menu`.
- **Não declarar `context_tags` num step `menu`.** O Zod faz strip e o engine nunca lê. É config morta
  hoje em `agente_auth_form_v1.yaml:87-92` e `:155-160`.
- **Não terminar um especialista de conferência com `escalate`.** Re-roteia o CONTATO em vez de voltar
  ao chamador (`TODO.md:3853`). Volte por `workflow_resume` + `complete`.
- **Não procurar `survey_eligibility_check`.** Não existe; o motor é o `contact_eligibility_check`.
- **Não acrescentar catch-all `session.*` com `hidden`** em `masking.context_rules`: derruba
  `session.dialog_form_id`/`session.decisions` e a tela de aprovação para de renderizar em silêncio.

---

## 11. As-built da Fase 1

### Artefatos

| Arquivo | Papel |
|---|---|
| `packages/skill-flow-engine/skills/skill_limite_entrada_v1.yaml` | N2+N1 — identidade, pendência, os três acessos, coleta |
| `packages/skill-flow-engine/skills/skill_limite_processo_v1.yaml` | N3 — pacote de análise, delegate ao aprovador, parking do resultado |
| `packages/skill-flow-engine/skills/skill_limite_retorno_v1.yaml` | N1 — entrega o resultado; parqueia quando não há cliente |
| `infra/dialog/dialog_limite_solicitacao.json` | form multi-campo, `cvv` com `masked: true` |
| `infra/dialog/dialog_limite_aprovacao.json` | campos editáveis do pacote (as decisões vêm do `delegate.context`) |
| `infra/registry/tenant_demo.yaml` | pools `limite_ia`, `limite_processo`, `limite_retorno`, `aprovacao_credito` + endpoint webhook |
| `packages/channel-gateway/.../adapters/webhook.py` | `_pending_context_preview` generalizado (§7.1 fechado) |
| `packages/config-api/.../seed.py` | 3 regras de masking para o pacote de aprovação |
| `infra/demo/web/webchat-test.html` | `<option>` de `limite_ia` |
| `infra/test/smoke_limite_tres_acessos.sh` | aceite, veredicto de 3 estados |

### Três decisões tomadas durante a escrita

**1. O coletor ficou INLINE no agente de entrada, não delegado ao runner.**
§8 recomenda estender `skill_dialog_runner_v1`, e isso segue certo — mas como Fase 2. Fazê-lo agora
exigiria `set-next`+`promote` no pool `dialog_runner`, que é skill de produção do OTP e do NPS: um erro
ali derruba dois fluxos que já funcionam. A versão inline usa a MESMA cadeia (`form_get` →
`render.fields` → `menu interaction: form`), que **nenhum skill do repositório exercitava** — então o
caminho genérico passa a ter um consumidor real, que é o pré-requisito honesto para extraí-lo depois.

**2. `preview` virou uma allowlist declarativa — e é o único canal de volta.**
Não é só cosmético: `pending_workflow_get` devolve o `context_preview`, **nunca** o `context` cru do
delegate. Logo tudo que o N2 precisa no acesso 3 (`resultado`, `limite_aprovado`, `parecer`) tem de
estar declarado na spec de preview do `parquear_resultado`. Campo não declarado não chega — e é por isso
que o smoke afirma explicitamente que o **CPF não aparece**: sem essa asserção, um preview que virasse
passthrough passaria por verde.

**3. Máscara desconhecida OMITE o campo e loga; não rebaixa para `plain`.**
Erro de configuração vira campo ausente (barulhento), não vazamento silencioso.

### Ordem de deploy

Skills, pools e DialogForms são todos **inéditos** — logo o seed-if-absent trabalha a favor: sobem no
restart do bridge (skills/pools) e do `dialog-seed` (forms), **sem** `x-skill-publish` e **sem**
`set-next`/`promote`. É a razão de o cenário ter pools próprios em vez de editar `agente_triagem_v2`.

A exceção é `masking.context_rules`: a **chave** já existe, e o seed é if-absent por chave — em base já
semeada as três regras novas **não entram** e precisam ser acrescentadas em `/config/masking`. Sem elas
o aprovador vê cartão e CPF em claro, e a demonstração de política perde o sentido.

### Achado da primeira execução — o `delegate.context` é namespace compartilhado

O smoke encontrou, na primeira rodada real, um defeito que nenhuma leitura de código
teria pego: a pendência foi **escrita com sucesso e ficou invisível**.

`_anchors_from_context` varre `phone`/`email`/**`cpf`**/`princ` do `delegate.context` como
âncoras de identidade. O contexto do pacote de aprovação tinha uma chave `cpf` — que eu
pusera ali como *campo de tela*. Ela virou âncora, o CPF era inédito, e o
`resolve_or_provision` **provisionou um cliente novo** (`matched_by=provisioned`) em vez de
casar com o telefone já conhecido. A pendência foi indexada sob um `customer_id` que
ninguém consulta. Nada falhou; nada logou erro; o `found:false` do acesso 2 parecia
"pendência não escrita" quando era "pendência escrita no lugar errado".

Duas correções, e as duas são necessárias:

1. **`contact_identifier` passou de fallback a ADITIVO** (`webhook.py`). Era
   `if not anchors:` — ou seja, uma âncora tipada desconhecida **descartava** a âncora
   conhecida. Descartar o que se sabe porque apareceu algo que não se sabe é a troca
   errada em qualquer ordem de grandeza.
2. **A tag virou `cpf_titular`** no fluxo (skills, seed de masking, smoke). O campo do
   formulário continua `cpf`; a **tag** não pode, porque o nome vaza para a resolução de
   identidade. Anexar o CPF como âncora progressiva segue desejável — mas como decisão
   explícita, não como efeito colateral do nome de um campo.

Gate de regressão: o smoke agora afirma que `pendings.customer_id == customer_id do
Lookup 1`. Sem essa asserção, o mesmo defeito volta e passa por verde.

**Dívida que isto expõe:** o `delegate.context` serve a dois donos — payload de tela e
âncoras de identidade — no mesmo espaço de nomes, sem nada que avise no ponto de uso.
Separar (`context` × `anchors`) é a correção estrutural; renomear a chave é o curativo.

### Achado do teste ao vivo — o pacote de aprovação fica ARMADO depois da decisão

Sintoma relatado: aprovada a tarefa, o contato não fechou; ao trocar de página e voltar, o
**mesmo formulário reapareceu**; aprovar de novo fechou o contato.

Diagnóstico: o primeiro submit resumiu o delegate `aprovar` e o workflow seguiu para
`parquear_resultado` — **outro delegate na mesma sessão**. Mas `session.dialog_form_id` e
`session.decisions` **não são limpos pela plataforma**; só o `session.delegate_resume_token` é
sobrescrito. O Console então lê o snapshot do parking como tarefa de aprovação e renderiza o
mesmo form. O segundo "Aprovar" **não re-aprovou**: resumiu o *parking* com `decision: input`
→ `encerrar_sucesso` → processo completo. **O cliente nunca receberia o resultado — o acesso 3
foi consumido por quem estava olhando a tela.**

Por que o smoke não pegou: ninguém abre o Console durante ele. Sem observador, o parking fica
intacto e o passo 8 vê `policy: auto` corretamente. Era um teste que não podia reprovar por
construção — o defeito só existe quando há humano.

Correção: dois `context_set` que **desarmam** o pacote entre a decisão e o parking
(`limpar_form_do_pacote` → `limpar_decisions_do_pacote`), mais o passo **7b** do smoke, que
afirma sobre o ESTADO (as tags vazias), não sobre a tela.

**Dívida de plataforma que isto expõe:** `delegate.context` é gravado no ContextStore da sessão
com prefixo `session.`, mas nada limpa as chaves do delegate ANTERIOR. Dois delegates em
sequência na mesma sessão herdam um snapshot híbrido — e o consumidor (Console) não tem como
saber quais chaves pertencem ao delegate corrente. A correção estrutural é o delegate escrever
num escopo próprio (por `step_id`) ou limpar o que não declara; renomear/limpar no fluxo é o
curativo. Ver também §7.5: o índice de pendências tem o mesmo problema de chave por sessão.

### A raiz das três manifestações — sessão em pool humano não pode sobreviver ao humano

Três defeitos, um mecanismo. A plataforma assume que **sessão alocada a pool HUMANO termina
quando o trabalho do humano termina**, e usa `contact_closed` para limpar tudo que é
session-scoped. O desenho original mantinha a sessão da análise viva para esperar o cliente —
e cada limpeza deixou de acontecer:

| # | Sintoma | Mecanismo |
|---|---|---|
| 1 | Cartão do aprovador não some do Console | a lista é session-scoped; sem `contact_closed`, nada o remove |
| 2 | O mesmo formulário reaparece e "aprovar" consome o parking | `session.dialog_form_id`/`decisions` não são limpos entre delegates |
| 3 | **O item volta para a fila pull** e oferece Claim de novo | ao cair o WS do aprovador (trocar de aba basta), `remove_conversation` **restaura a membership dos SETs do pool**; a sessão viva e sem agente é re-oferecida |

O `skill_gate_promocao_v1` nunca sofre disso porque termina em `complete` logo após a decisão.

**Correção estrutural (2026-08-11):** a análise **fecha na decisão**
(`disparar_entrega` → `workflow_trigger` → `encerrar_sucesso`), e a espera passa a viver em
`skill_limite_entrega_v1`, num pool webhook (`limite_entrega`) que **nunca toca pool humano**.
A journey continua ligada por `origin_session_id` — intake → análise → entrega são três sessões
do mesmo processo, e o drill de 3 níveis fica mais rico, não mais pobre.

Gates: passo **7c** (sessão da análise `closed` e fora do ZSET da fila) + **1c** estendido ao
novo pool. As limpezas do item 2 ficam como defesa em profundidade.

**Regra que fica:** um `delegate` a pool humano deve ser o **último** ato relevante da sessão.
Precisa continuar o processo depois? Dispare outro processo — não segure a sessão.

**Preço aceito, medido na tela:** o *Workflow trace* de `/analise/sessions` é **session-scoped**.
Com análise e entrega na mesma sessão ele mostrava **7 execuções**; separadas, mostra **3** e para
em `skill limite processo — resolved`. Não é dado perdido — é dado noutra sessão. A leitura
ponta-a-ponta passa a ser a **Vista Processos** (`/analise/processos`), que agrupa por journey; a
sessão de entrega herda a raiz por `origin_session_id`.

**Verificado ✅ (2026-08-12) — `infra/test/probe_journey_limite.sh`, 5/0.** As três sessões formam
UMA journey, raiz = o `session_id` do intake. A dúvida era legítima (o caminho testado até então era
`workflow_trigger` disparado de um **agente** de intake, nunca de dentro de **outro workflow**), mas
o mecanismo não distingue os dois casos: `handle_trigger` resolve a raiz lendo
`session.root_session_id` do ctx do CHAMADOR (`webhook.py:476-486`) e sempre semeia a tag na sessão
nova (`:534`) — a herança é transitiva por construção. `spawn_reason='trigger'` confirma a aresta.
Nenhum pool `limite_*` é `purpose: internal`, logo o `_apply_contact_scope` do relatório não exclui,
e o `HAVING` de significância (`count() > 1 OR channel='webhook'`) passa pelos dois lados.

**Achado colateral da medição:** a sessão da ANÁLISE sai com `sessions.pool_id = aprovacao_credito`,
não `limite_processo` — o delegate ao pool humano reescreve a linha inteira no `ReplacingMergeTree`
(a mecânica que o `CLAUDE.md` já nomeia). Não afeta o agrupamento, que é por `root_session_id`, mas
afeta a BUSCA: **filtrar a Vista Processos por pool `limite_processo` não acha esta journey** pela
sessão da análise. Buscar pela raiz, ou pelo pool `limite_ia`/`limite_entrega`.

### O smoke afirmava contra a própria suposição — `edits` vem de `fields[]`, não de `answers`

Acesso 3 rodou ao vivo pela primeira vez e entregou: *"Novo limite: **R$ **"*. Vazio — com o
smoke em 16/0.

`ApprovalPanel` monta `payload.edits` a partir dos **`fields[]`** do DialogForm (padrão form_ext:
campos pré-preenchidos que o humano edita), e o payload de decisão é
`{decision, source, choice, edits, field_edits}` — **sem `answers`**. O form usava *question
nodes* com `output_key`: o aprovador digitava e o valor não tinha para onde ir. `choice` chegava
(por isso `resultado: aprovado` funcionava), `edits` vinha vazio.

**Por que o smoke não podia pegar:** o passo 7 **fabrica** o payload com `edits`. Ele testa a
capacidade do workflow de *ler* `edits`, nunca a do Console de *produzir*. Um teste que constrói
a entrada que ele mesmo espera não observa a integração — observa a si próprio. É a versão sutil
do "teste que não pode reprovar": ele *pode*, mas só contra a metade que não interessa.

Correção: o form passou a usar `interaction: form` + `fields[]`, e o **passo 1a** afirma que o
form publicado expõe `limite_aprovado` e `parecer` em `fields[]`. Não é o mesmo que exercitar o
Console, mas é a ponte verificável entre o contrato do form e o que o workflow lê.

**Regra que fica:** num pacote de aprovação, campo que o workflow vai ler tem de ser `field`, não
`question`. Question node em form de aprovação é decorativo.

### Achado colateral: `{t}:session:{id}:status` mente para toda workflow encerrada

Perseguindo o fechamento da análise, medi a chave Redis `{tenant}:session:{id}:status` e ela
devolvia `active` minutos depois. Os logs do bridge mostraram o oposto — o caminho inteiro rodou:
`outcome=resolved` → segmento do claimante fechado → `conversations.outbound session.closed` →
`agent_done` devolvendo a vaga do pull → `contact_closed` → `conversations.session_closed` →
`_destroy_conference`.

A chave é escrita como `"active"` pelo caminho de **resume** (`webhook.py:1254`) e quem escreveria
`"closed"` é o **Core** (comentário em `:1451`) — que não participa de sessão webhook. Logo ela
reporta `active` para **toda** sessão de workflow encerrada, portabilidade inclusive. Consequência
real: `GET /v1/channels/webhook/{session_id}/status` mente. Pequeno, pré-existente, fora do escopo
deste cenário — mas é dívida nomeada agora.

Lição do instrumento: **medir a chave errada acusou defeito onde o comportamento estava certo.**
O 7c passou a afirmar sobre o fato durável (`analytics.sessions`, alimentado pelo
`conversations.session_closed`), não sobre um cache que ninguém mantém.

### Dívida de plataforma: transição para step inexistente PARA o workflow em silêncio

Ao renomear `parquear_resultado` → `disparar_entrega` esqueci de atualizar dois
`on_success`/`on_failure`. O resultado não foi erro nenhum: o workflow **executou até a
transição órfã e parou**, deixando a sessão `active` para sempre. Sem log, sem `isError`, sem
step de falha — e o segmento durou 19 ms, indistinguível de sucesso.

`validateFlow` (`engine.ts:268`) valida **ciclos não-guardados**, não a **existência dos alvos**
de `next`/`on_success`/`on_failure`. Um typo ou rename produz halt mudo.

Guard escrito: **`infra/test/probe_flow_transitions.sh`** — varre todos os YAMLs de `/skills`
dentro do bridge (que já tem PyYAML e monta o diretório) e reprova qualquer alvo inexistente em
`entry`, `on_success/failure/timeout/disconnect`, `on_resume/reject.next`, `conditions[].next` e
`default`. Heurística deliberadamente estreita (ignora valores com `.`/`$`/`@`, que são refs, não
ids): probe barulhento é pior que probe nenhum, porque manda procurar bug onde não há.
Correção de verdade seria no `validateFlow`; o probe é a rede enquanto isso.

Sintoma para reconhecer da próxima vez: **sessão presa em `active` + último segmento muito
curto + o step seguinte sem nenhum rastro nos logs do tool que ele deveria chamar.**

### Estado da validação (2026-08-11)

> **Ritual de deploy — três operações distintas, e confundi-las custou dois ciclos.**
> `seed-if-absent` cria entidade INÉDITA no restart · `REGISTRY_SYNC_RECONCILE=true` republica o
> `skill.flow` de skill JÁ EXISTENTE · **`set-next` + `promote` re-snapshota o slot** — e é o
> **snapshot** que o bridge executa. Reconcile **não** promove. O sintoma é cruel: `GET /v1/skills/:id`
> mostra o step novo, e o runtime segue rodando o antigo. O passo 7b separa os dois estados e nomeia o
> remédio de cada um.

**Verde por API — `smoke_limite_tres_acessos.sh` 16/0.** Quatro das dezesseis asserções nasceram
de defeitos reais encontrados nesta bateria, não de imaginação: `customer_id` da pendência,
pacote desarmado, contato da análise encerrado, e slot `current` executável. Está provado: o processo suspende na
análise; o item cai na fila pull; a pendência indexa sob o customer_id certo com `policy: offer`,
status de negócio e cartão `***1234`; o CPF **não** vaza (allowlist); o aprovador reivindica e decide
com `approvals.decide` (HTTP 200, `verification_class` NÃO veio `claimed` — a suspeita (a) não se
confirmou); e a pendência vira `policy: auto` carregando `resultado`, `limite_aprovado` (9000, editado
pelo humano sobre um pedido de 12000) e `parecer`. A suspeita (b) também caiu: `handle_trigger` aceita
as chaves `session.*` cruas.

**Verde ao vivo (2026-08-11).** Os três acessos percorridos no webchat com o Console fazendo a
aprovação: formulário multi-campo com CVV mascarado, menu de continuidade com status de negócio e
cartão `***4444`, e entrega do resultado com o valor **editado pelo humano** (`R$ 3500` sobre um
pedido maior) chegando ao cliente. Fase 1 validada em API e em canal.

Pendência cosmética conhecida: o cliente de teste (`webchat-test.html`) **não renderiza markdown**
— `**negrito**` aparece com os asteriscos literais e `\n\n` é colapsado. Mesmo cliente que ignora
`field.type`. Para demo, tirar os `**` das mensagens ao cliente.

**Não exercitado (histórico — resolvido acima):**

| O quê | Por quê importa |
|---|---|
| `skill_limite_entrada_v1` inteiro | O smoke dispara o N3 direto, simulando o que o N2 faria. Identidade, OTP, menu de continuidade e ramificação dos três acessos nunca rodaram |
| `form_get` → `render.fields` → `menu interaction: form` | É a cadeia que **nenhum skill do repositório usava**. É o maior risco isolado que resta |
| `masked: true` no `cvv` | A prova de que o campo não aparece em lugar nenhum depende de coletar de verdade |
| ~~`limite_retorno` com slot executável~~ | ✅ fechado pelo passo **1c**. O delegate parqueia a pendência **antes** de qualquer agente rodar, então o step 8 ficava verde com o pool sem skill — teste que não podia reprovar. O 1c exige slot `current` com snapshot nos três pools de IA; hoje passa nos três |
| `skill_limite_retorno_v1` **executando de verdade** | Slot existe; ninguém viu o agente entregar o resultado ao cliente |
| Aprovação renderizada no Console | O smoke resume por HTTP; ninguém viu o `DialogFormRenderer` com este pacote |
| Masking por política na tela | As 3 regras estão no seed, mas a chave `masking.context_rules` já existia ⇒ seed-if-absent **não** as aplica em base semeada. Sem acrescentá-las em `/config/masking`, o aprovador vê cartão e CPF em claro |
