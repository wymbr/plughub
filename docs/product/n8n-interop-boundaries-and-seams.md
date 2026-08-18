# n8n × PlugHub — fronteiras, costuras e escopo

> ⛔ **HISTÓRICO — ARCO ABORTADO em 2026-08-18.** O alvo abaixo (*"todo skill autorado no n8n; o editor
> de fluxo local sai por completo"*) **não será perseguido**. Decisão, justificativa e reclassificação
> item a item em [`n8n-arco-abortado-2026-08-18.md`](n8n-arco-abortado-2026-08-18.md). Não citar este
> documento como contrato, plano ou justificativa.
>
> **Sobrevivem dele, com dono novo:** a direção *"config + interpretador genérico"* (§5.3), que nunca
> dependeu do n8n; os 13 defeitos colaterais da §14, explicitamente marcados como independentes; e a
> **costura C** (n8n como *domain MCP server* governado), que **não foi abortada** — aponta na direção
> contrária, não toca autoria, e espera decisão explícita (§7 do documento de reversão).

> **Status: proposto** — 2026-08-17 (revisão 3)
> **Decide:** o que o n8n assume, o que o PlugHub retém, por onde os dois se falam, onde vivem
> relatórios e histórico, e o que sai de escopo.
> **Alvo declarado:** **todo skill associado a um pool** — perfis `workflow` e `agent` — passa a ser
> autorado no n8n. **O editor de fluxo local sai por completo.** O skill sobrevive como **envelope
> de configuração**.
> **Não decide:** cronograma, nem hospedagem do n8n.

---

## 0. Resumo — e qual justificativa está sendo adotada

Existem **duas** justificativas possíveis para este movimento, e elas autorizam coisas diferentes.
Confundi-las produziria um documento que promete aposentadorias que não vão acontecer.

| Justificativa | O que autoriza | Risco |
|---|---|---|
| **Parar de duplicar** | Aposentar scheduler, mailing, importador, workflow-api | Alto — depende das aposentadorias acontecerem, e a §10 mostra que a maioria **não** deve |
| **Ter um editor melhor** ← **adotada** | Trocar a autoria de fluxo de 100% dos skills | Baixo — e é o que motivou a discussão desde o início |

**A justificativa adotada é a do editor.** O editor local não acompanha os editores de mercado, e
restaurá-lo não resolveria isso — cair no fallback é voltar ao estado julgado insuficiente, e um
editor sem investimento apodrece contra a plataforma que continua andando.

O ganho **não é apagar código**. É que toda autoria de fluxo — workflow e agente — sai de YAML mais
canvas caseiro e vai para uma ferramenta madura, com ecossistema, comunidade e centenas de
conectores. O código que some é efeito colateral, não o objetivo.

**O seguro contra dependência não é um editor de reserva — é portabilidade.** `certify`,
`verify-portability`, `skill-extract`, `regenerate` respondem *"posso sair daqui?"*, e o CLAUDE.md os
mantém deliberadamente separados da reclassificação por isso. Com os workflows endereçados por
`(mcp_server, tool)` sobre MCP aberto, trocar de n8n é mudar entrada de registro, não reescrever.
**Ação devida:** o alvo da portabilidade passa a incluir o JSON do n8n.

Duas regras governam o resto do documento:

> **n8n toca sistemas. PlugHub toca pessoas.**

> **Fica o que produz ou consome fronteira, ou é governança de contato com pessoa. Sai a camada de
> orquestração.**

---

## 1. Contexto — a medição

Arquivos de produção em `packages/` (excluídos testes, `dist`, `node_modules`), 2026-08-17.
**575 arquivos.**

| Classe | Arquivos | % |
|---|---:|---:|
| **Fosso real** — sessão, conferência, roteamento, capacidade, canais, qualidade, governança | ~257 | 45% |
| **Território n8n** | ~66 | 12% |
| **Infra compartilhada** | ~250 | 43% |

**Isto é contexto, não justificativa.** Serve para responder uma pergunta específica — *o PlugHub
virou um clone de n8n?* — e a resposta é **não**: a sobreposição é de ~12%, e os 45% de fosso não
têm equivalente no mercado. A §10 mostra que a maior parte desses 12% **fica**, porque é estado e
governança, não orquestração.

**Duas limitações declaradas.** (a) Contagem de arquivo não é esforço nem valor; para orçar, refaça
por linhas ou commits. (b) **A medição excluiu `packages/e2e-tests/`** — e o runtime de produção dos
skills vive lá (§9). O número está subestimado exatamente onde a integração aterrissa.

### 1.1 O corte dos step types

`packages/skill-flow-engine/src/steps/` tem **17 arquivos**: 10 conversacionais (`menu`, `notify`,
`collect`, `receive`, `resolve`, `task`, `escalate`, `delegate`, `begin/end-transaction`) e 7
genéricos (`choice`, `catch`, `invoke`, `reason`, `complete`, `suspend`, `loop`).

Na revisão 1 este corte definia o escopo — perfil `agent` era intocável. **Não é mais o critério:**
com o alvo cobrindo 100% dos skills, o corte serve para dimensionar o esforço de tradução, não para
delimitar o que move. O que delimita é o resíduo (§5.3).

---

## 2. O que o n8n é — e o que nunca é

**Teste canônico:** *a plataforma precisa saber quantos deles existem e se estão vivos?*

- **Sim** → recurso **alocável**: exige instância com identidade, heartbeat, `max_concurrent`,
  pausa, `claim_instance`, `agent_done` com `issue_status`.
- **Não** → recurso **chamado**: MCP server ou canal.

O n8n gerencia a própria concorrência (queue mode, workers). O PlugHub não sabe nem deve saber
quantos workers n8n existem. **Logo: recurso chamado.**

| Papel | Correto? |
|---|---|
| Canal / cliente do canal `webhook` (entrada) | ✅ |
| Domain MCP server (saída) | ✅ |
| Skill publicado num pool · agente · instância · pool | ❌ |

### 2.1 O padrão único: pool com skill que chama o n8n

**Pool com skill nativo cujo corpo delega ao n8n por `invoke`.** A instância é do PlugHub
(contabilidade correta), o segmento existe, `segments.deploy_version` descreve o deploy, e o n8n
entra como domain server auditado.

> **O pool contém um skill que CHAMA o n8n. O pool não contém o n8n.**

**Modo de falha se registrado como pool "n8n":** `available` publica capacidade que ninguém garante;
a admissão debita `{t}:admission:kind:ai` para algo que não é licença de IA; `deploy_version`
carimba snapshot que não descreve o que rodou; não há `agent_done` honesto para fechar o segmento.

**Condição de capacidade:** capacidade n8n ≥ Σ `max_concurrent` dos pools que delegam a ele. Nessa
forma o PlugHub **é** o limitante e a garantia de capacidade permanece real. Item de monitoramento,
não bloqueio arquitetural.

### 2.2 A decisão de 2026-08-13 não decide esta questão

`agentes-externos-reclassificacao.md` rebaixou o **runtime importado** por exigir que a plataforma
garantisse capacidade, heartbeat e `agent_done` sobre código que não controla. Aquilo era sobre
agente de **terceiro**. Um n8n operado pelo **próprio tenant** tem os dois lados sob a mesma
administração — o argumento não transfere limpo, e este documento não o invoca como se fechasse a
questão. O que fecha é o §2.1: na shape do pool-com-skill não há runtime importado nenhum.

---

## 3. As fronteiras: quem produz journey, session, segment

**Nenhuma das três é declarada pelo fluxo.**

| Fronteira | Quem produz | Natureza |
|---|---|---|
| **segment** | orchestrator-bridge, quando um participante entra e sai | **Estrutural** |
| **session** | channel-gateway, na travessia da borda | **Estrutural** |
| **journey** | `root_session_id` propagado pelo chamador (∪ `journey_merge`) | **Contratual** |

O fluxo **dispara** fronteiras; nunca as define — sempre chamando um primitivo que é dono dela.
`choice`, `catch`, `loop` e `reason` produzem **zero** fronteiras.

### 3.1 A borda é a única fábrica de sessão

Contato inbound de canal real, webhook, `collect` outbound e survey web nascem todos no
channel-gateway. É isso que torna o modelo robusto à substituição da lógica: **o n8n pode ser autor
da lógica sem nunca ser autor da fronteira.**

Único modo de quebra: workflow n8n mandando WhatsApp direto pela API da Meta — aquele contato **não
existe** para o PlugHub. Não é o editor que importa; é quem encosta no canal.

### 3.2 A journey é a fronteira frágil

`root_session_id` é *"param propagado do chamador ou auto-mint = `self`"*. Disparo n8n sem propagar
**não dá erro**: a sessão vira sua própria raiz, o processo se parte em dois, e ambos parecem
válidos.

1. **Propagação explícita e obrigatória** — o sub-workflow/node publicado carrega o campo como
   cidadão de primeira classe (§4.5).
2. **Logar o auto-mint** em disparo de origem externa. Fecha uma violação existente da postura,
   independente do n8n.
3. `journey_merge` como reparo — existe, mas reparo é remendo.

---

## 4. As costuras

### 4.0 Quatro fatos que condicionam tudo

1. **O PlugHub não tem saída HTTP genérica.** Única chamada a URL de terceiro: o `WebhookProvider`
   de `channel-gateway/.../survey_web.py`.
2. **A entrada existe e está aberta.** `POST /v1/channels/webhook/pool/{pool_id}` é **anônima**.
   `POST /channel/webhook/{slug}` é a porta limpa (`allowed_origins={"external"}`, `X-Webhook-Token`
   opcional).
3. **Não existe superfície de resultado.** O trigger devolve só `{session_id}`; o `GET .../status`
   responde `"closed"` também quando a chave não existe.
4. **O cliente MCP existe mas nunca funcionou em produção** (§9). A tool `invoke` usa `Client` +
   `SSEClientTransport` oficiais, mas resolve endereço por env `MCP_SERVER_{NOME}_URL` — **não
   definida em lugar nenhum**. E `tools/list` não é chamado em ponto algum do repositório.

### 4.1 Costura A — n8n → PlugHub por webhook  *(existe)*

```
n8n HTTP Request → POST /channel/webhook/{slug}
  X-Webhook-Token: <token>
  { tenant_id, customer_id, root_session_id?, context, journey }
← 201 { session_id }
```

**A não serve sozinho.** Das três formas de o n8n saber que terminou, só a terceira funciona:
polling no `status` mente; callback HTTP não existe; **Kafka Trigger em
`conversations.session_closed`** funciona. ⇒ **fase 0 é `A + E`.**

### 4.2 Costura E — n8n consome eventos  *(existe)*

Kafka Trigger em `conversations.session_closed`, `evaluation.events`, `session.signals`,
`agent.lifecycle`, `journey.merges`. **Preço:** tópico interno vira contrato público. **Mitigação
diferida:** consumidor de fan-out com envelope versionado — não antes de precisar.

### 4.3 Costura B — n8n como cliente MCP  *(construir)*

O n8n deixa de ser gatilho e vira **participante**: consulta antes (`pool_status_get`), dispara
(`workflow_trigger`), acompanha (`work_queue_list`), lê contexto.

**Bloqueio: principal externo.** Toda tool valida `session_token` de `agent_login`, que é identidade
de *instância de agente*. Precisa de principal novo, token por endpoint, `tenant_id` **nunca do
corpo**, escopo de tools por credencial.

> Mesmo trabalho da fase A2 do `adr-a2a-server-binding.md`. **Não criar dois mecanismos.**

⚠️ Não há auth de transporte na frente de `/sse` e `/messages`. Verificar antes de expor.

### 4.4 Costura C — n8n como domain MCP server  *(construir — maior retorno)*

```
skill (config): invoke → target: { mcp_server: "mcp-server-n8n-itsm", tool: "servicenow_case_create" }
engine → ctx.mcpCall(tool, input, server)
       → mcp-server: permissão (JWT) → injection guard → mcp.audit → despacha
       → n8n MCP Server Trigger → sub-workflow → ServiceNow
```

- **Borda única intacta** — o veredicto continua no mcp-server.
- **Sem HTTP arbitrário** — o skill escolhe servidor registrado, não URL.
- **Audit LGPD por construção** — não se compra conectores, compra-se **conectores governados**.

**Invariante de registro:** **proibido** publicar tool de canal como domain MCP server do n8n.
`salesforce_case_create` é legítimo; `whatsapp_send` viola o §3.1.

#### 4.4.1 Endereçamento — não é um id por workflow

O endereço é o par `(mcp_server, tool)` — a mesma string de permissão que o `invoke` exige no JWT.

```
1 domínio = 1 workflow n8n com MCP Server Trigger = 1 mcp_server
N automações = N sub-workflows = N tools
```

Agrupar por domínio: server-por-workflow explodiria as permissões e mataria o curinga `server:*`.

**Duas disciplinas:** id de server **estável e sem versão**; **nome de tool é contrato público** —
renomear quebra os skills e faz a permissão parar de casar, falhando fechada mas em silêncio.

**Superfície de config (requisito):** a convenção de env `MCP_SERVER_{NOME}_URL` é impopulável pela
UI e está vazia. Substituir por catálogo no config-api (namespace `mcp_servers`:
`{id, display_name, url, transport, active}`, token só em env), copiando o **LLM Accounts Catalog**.
Na tela de config do skill, seletor `server → tool`.

**Simetria:** inbound o endereçável é o **pool**, nunca o `skill_id`; outbound é
**`(mcp_server, tool)`**, nunca o id do workflow.

#### 4.4.2 Atribuição de versão

**Verificado no n8n (ago/2026):** `versionId` e `updatedAt` existem na API; Workflow History guarda
versões (completo em Enterprise, 5 dias no Cloud Pro, 24h nos demais). **Mas o workflow em execução
não vê a própria versão** — `$workflow` expõe só `id`, `name`, `active`; há feature request aberto.
⇒ **o eco da versão pela própria tool não é implementável nativamente.**

Três fontes que se conferem:

| Fonte | Cobre |
|---|---|
| **Nome da tool** (`agent_atendimento_v3`) — já gravado em `mcp.audit` | Atribuição por execução |
| **Snapshot de `tools/list` no slot, no promote** | Drift de **assinatura** |
| **`versionId` fixado no promote + reconcile** | Drift de **comportamento** com mesma assinatura |

A discrepância entre elas é o alarme. O snapshot de `tools/list` é superior ao `versionId` para
contrato — é conteúdo canonicalizado — e não depende de tier Enterprise.

**Nunca usar `updatedAt` como identidade.** **Licença:** o alarme funciona em qualquer tier; a
forense *"o que a v2 continha?"* exige Workflow History (Enterprise).

### 4.5 Costura D — node/template PlugHub no n8n  *(fase 5; precursor na fase 0)*

**Não descartada — sequenciada.** É onde mora a **disciplina de propagação de `root_session_id`**
(§3.2), a única fronteira sem enforcement estrutural.

- **Fase 0:** **sub-workflow template** ("PlugHub: disparar contato"). Versiona como dado, custa
  quase nada.
- **Fase 5:** node compilado, se a adoção justificar — depois da superfície de resultado.

---

## 5. O alvo: eliminar o editor de fluxo

**"Fluxo" = todo skill associado a um pool**, nos dois perfis. O editor sai **por completo**.

### 5.1 O skill sobrevive como envelope de configuração

**Só o corpo do fluxo sai.** O skill continua carregando `config_params`, `interface_schema`,
política de masking, declaração de perfil, `mention_commands`, identidade e versionamento.

> ⚠️ **Nota de vocabulário (corrigida 2026-08-17).** Este documento dizia *"o bloco `flow:`"*, e quem
> seguir a letra e grepar `flow:` nos YAML acha **zero**. Nos 42 arquivos de
> `packages/skill-flow-engine/skills/` a chave top-level é **`steps:`** (ex.:
> `skill_dialog_runner_v1.yaml:49-118`); **`flow` é o nome da COLUNA** no agent-registry — é o que
> `evaluation_context_get` lê como `sk["flow"]` (`tools/evaluation.ts:1146-1150`). São a mesma coisa em
> dois lugares com dois nomes, e a distinção importa porque o mapeador da §5.4 escreve na **coluna**,
> não no arquivo.

| Item | Efeito |
|---|---|
| Campos não-fluxo do YAML | **Mantêm a casa** — zero migração |
| Slot, `set-next`/`promote`, `deploy_version` | **Inalterados.** Muda só a carga do snapshot: de `yaml_snapshot` para config + referência + snapshot de `tools/list` |
| `interface_schema` → `config_json` → `$.config` | **Intacto** — é o que parametriza o dialog primitive |
| Guard de perfil | **Migra**, não some: de parse-time para boundary-time (a tool recusa quando o perfil do pool não permite). Mais fraco, pega na execução |
| `mention_commands.trigger_step` | **Redesenho**: id de step → nome de tool no mesmo server |

**Consequência de segunda ordem:** skill que é config declarativa sem lógica **não precisa de YAML à
mão** — vira formulário na UI, satisfazendo *"every config field is UI-editable"* sem canvas. O que
morre é a **tela de fluxo**; o que a substitui é uma **tela de config**.

### 5.2 O que morre, o que sobrevive

| Camada | Destino |
|---|---|
| UI do editor de fluxo (`agent-flow`, 7 arquivos) | **Morre por completo** — parar de investir agora |
| Bloco `flow:` de todo skill, dos dois perfis | **Morre** — vira referência a `(mcp_server, tool)` |
| Parte do agent-registry que gere `flow`/`flow_draft`/publish | **Morre** |
| Skill como entidade de config | **Sobrevive**, e simplifica |
| Engine | **Sobrevive e é PROMOVIDO** a interpretador/runner de primeira classe (§5.3) |
| Slot/deploy | **Sobrevive**, troca de carga |
| Editor de DialogForm (`/config/dialog-forms`) | **Sobrevive e ganha importância** |

### 5.3 O resíduo é um RUNNER, não um fluxo

Correção em relação à revisão 2, que tratava o resíduo como "fluxos que não podem sair". **O que não
pode sair é código, não autoria** — e por isso o editor pode morrer inteiro.

**NPS inline.** O flow do `agente_nps_v1` é `form_get → menu → notify → complete`. Isso não é fluxo:
é exatamente o que um runner genérico de formulário faz. Vira **config** (qual form, qual gatilho).

*Por que precisa ficar local:* `_is_workflow_dispatch_entry` (`orchestrator-bridge/main.py:1233`)
mostra que **só `side=customer` + `inline` permanece na conferência** — todo o resto já migrou para o
veículo de workflow. E não é destacável por natureza: destacar fecha o contato (`main.py:2083`) e o
cliente sai do WS.

⚠️ **Usar DialogForm não muda o veículo.** DialogForm é *conteúdo*; inline × destacado é *veículo*.
⚠️ **`inline` tem dois significados** conforme o `side`: conferência quando `customer`, máquina
destacada quando `agent`.

**Transação mascarada.** Vira DialogForm com campos `masked: true` + runner confiável, e **o n8n
recebe o resultado, nunca o valor**. O padrão já existe e está documentado no OTP: *"o código nunca
passa pela mão de um agente/runner — gerar/enviar/verificar ficam no serviço confiável"*. Generaliza
direto.

**Consequência:** o interpretador genérico é hoje ele mesmo um skill em YAML
(`skill_dialog_runner_v1`). Com o YAML de fluxo morto, ele precisa virar **código** — serviço de
primeira classe. **Isso deixa de ser efeito colateral e vira pré-requisito da fase 5.**

### 5.4 O item bloqueante — evidência de execução para avaliação tier-2

`evaluation_context_get` injeta `flow_definition` (trajetória **esperada**, do agent-registry) e
`pipeline_state` (trajetória **real**). Ambos são artefatos do engine local.

Na revisão 2 isto valia para parte dos agentes. **Com o alvo cobrindo 100% dos skills, vale para
todos** — a avaliação de IA degradaria em bloco para **grau-transcript**, a mesma limitação que o
quality-ingest documenta para históricos *externos*. Seria importar voluntariamente a restrição
criada para o caso alheio.

⇒ **Os mapeadores são bloqueantes da fase 5, não trabalho opcional:**

- `flow_definition` ← snapshot de `tools/list` + JSON do workflow n8n via API.
- `pipeline_state` ← trace de execução do n8n.

**Atenuante:** a atribuição se parte em duas e a parte que mais importa fica em casa. O
**DialogForm já é versionado** na dialog-api. n8n versiona a **lógica**; dialog-api versiona o
**conteúdo conversacional**.

### 5.5 A guarda que passa a ser load-bearing

O **DialogForm foi desenhado para NÃO ser linguagem de fluxo** — sem `next` condicional; *"branching
é do skill, senão vira linguagem em JSON"*. Com o editor morto, haverá **pressão para empurrar
control-flow para dentro do form**, e o `adr-dialog-conditional-skip-logic.md` (guarda declarativa
`ask_when`, *não* control-flow) vira **load-bearing**.

> Se essa guarda ceder, o editor de fluxo é reconstruído dentro do editor de formulário — com uma
> linguagem pior.

**Direção já em curso:** `$.config` do slot, `menu.options/fields` como ref, `form_get`, as quatro
superfícies do dialog primitive, o `DialogFormRenderer`. A plataforma vem migrando de *"lógica em
YAML"* para *"config + interpretador genérico"* há meses. Este alvo é a extrapolação da trajetória.

---

## 6. Autoria de agente no n8n — avaliação

### 6.1 Capacidade — sem perda, na shape do §2.1

A instância é do PlugHub; vaga, heartbeat e pausa continuam no routing. Com capacidade n8n ≥ Σ
`max_concurrent`, o PlugHub é o limitante e a garantia é real. A shape que quebraria — n8n fazendo
`agent_login` e disputando `wait_for_assignment` — **não é o padrão adotado**.

### 6.2 Latência — gate de prosseguimento, não prudência

O custo não é o protocolo (MCP e webhook são ambos um round trip); é a **quantidade** de travessias.
O que hoje é implícito vira explícito: `@ctx.*` interpolado em inputs e condições, `context_tags`
com confidence e merge pós-chamada, e o `resolve` de 5 fases (com a otimização de 0 LLM quando o CRM
resolve).

Com o alvo cobrindo 100% dos skills, **todo turno conversacional atravessa o n8n.** A instrumentação
deixa de ser prudência e vira **condição de prosseguir** (fase 3-gate).

**Mitigação:** contrato de granularidade grossa, com forma de turno, em vez de tools finas.

> **Nota de método:** a estimativa de "5–8 round-trips" é palpite, não medição.

### 6.3 Masking — retenção, não acesso

O agente **precisa** ver o dado aberto; o `resolveToken` existe para isso. O invariante é que
`@masked.*` **não é persistido**. O runtime do PlugHub faz o valor **transitar sem pousar**; o n8n é
construído para **tudo pousar**. Arquiteturalmente opostos nesse eixo.

- **Escopo:** como domain server a exposição é por chamada; como runtime, veria **toda mensagem do
  cliente**.
- **`begin_transaction`/`end_transaction`:** resolvido pelo padrão do §5.3 — o n8n recebe o
  resultado, nunca o valor.

### 6.4 AI Gateway e a fachada OpenAI

**O overhead não é o problema** — um hop interno contra uma chamada de 1–5 s é ruído. O problema é
ergonomia: o que faz valer autorar no n8n é em boa parte o AI Agent node; chamar `/v1/inference` cru
significa reconstruir o loop agêntico à mão.

**Solução: fachada compatível com OpenAI no ai-gateway.** Com `/v1/chat/completions` na borda,
preservam-se rotação multi-conta, throttle 429/529, fallback cross-provider e `preferred_config_ids`
por pool.

Estado medido: rotas são `/inference`, `/v1/turn` (legacy), `/v1/reason`, `/v1/copilot/analyze`,
`/v1/health`, contrato proprietário (`model_profile`, não `model`). **Não há shim.** Mas
`providers/openai_provider.py` já converte o formato interno *para* Chat Completions na saída — a
fachada de entrada é largamente reverter isso. `model_profile` vira pseudo-modelo
(`plughub/fast`, `plughub/balanced`).

⚠️ A metering de tokens já está apagada no caminho principal — §14.

---

## 7. Relatórios e histórico

**Separados por dono, costurados por correlação, nunca fundidos.** Execução de n8n não é segment,
não é session, não é journey — é uma quarta coisa.

| Fato | Dono |
|---|---|
| "o workflow rodou, o nó X recebeu Y e devolveu Z" | **n8n** |
| "houve um contato, durou T, com estes participantes" | **PlugHub** (`sessions`/`segments`) |
| "o cliente disse isto, o agente respondeu aquilo" | **PlugHub** (`messages` + stream canônico) |
| "a qualidade daquele atendimento foi N" | **PlugHub** (`evaluation_finalized`) |
| "o processo atravessou 3 contatos" | **PlugHub** (union-find sobre `root_session_id`) |
| "o Salesforce foi chamado" | **ambos** — n8n o *detalhe*, PlugHub o *veredicto* |

**Correlação por ID, não cópia:** `session_id` e `root_session_id` já existem; nenhuma tabela nova.
Para o drill PlugHub → n8n, o `execution_id` vai em **`mcp_audit_log`** — é fato de uma chamada, não
da sessão.

| Pergunta | Superfície |
|---|---|
| "O que aconteceu com o cliente?" | Console / Analytics — **sempre** |
| "Por que a automação falhou?" | Executions do n8n — **sempre** |
| "A integração está autorizada?" | `mcp_audit_log` / AuditPage |

**Regra que impede a recaída:** o PlugHub **não constrói tela para execução de n8n.**

**Contaminação de relatório — já resolvida.** Sessão disparada por webhook é *etapa interna* (pool
`channel_types: [webhook]`); o contato gerado via `collect` é *acesso do cliente*. Não precisa de
campo novo, e **não estender o enum `origin`** — ele é procedência do substrato de qualidade, não
autoria do disparo.

---

## 8. Compliance

Na costura C o domain server recebe params **desmascarados**, e o n8n **retém input/output por
default**. Reintroduz-se pelo lado de fora o vazamento que a decisão R7 recusou por dentro.

| Mitigação | Força |
|---|---|
| Desligar retenção de execução nos workflows que tocam PII | **Obrigatória** — gate de registro |
| n8n no escopo do DPO, com retenção declarada | Obrigatória |
| Passar token em vez de valor | Rejeitada — exigiria n8n falando com o `TokenVault` |

**Item de segurança urgente:** `POST /v1/channels/webhook/pool/{pool_id}` é **anônima**. Com n8n do
outro lado é exposição ativa — qualquer um dispara qualquer pool webhook do tenant, inclusive os que
promovem deploy e contatam clientes. **A fase 0 não sobe sem fechar isso.**

---

## 9. Achado paralelo — o runtime de produção é um pacote de e2e-tests

> **Recomendação: promover `skill-flow-service` a pacote de primeira classe.** Não é assunto
> separado: é **onde a integração aterrissa**, e a promoção e a fase 2 são a mesma obra.

| Fato | Evidência |
|---|---|
| Skills conversacionais executam em `packages/e2e-tests/services/skill-flow-service/` | `orchestrator-bridge/main.py:672` faz `POST {SKILL_FLOW_URL}/execute`; compose 763-801, porta 3460 |
| É dependência `service_healthy` do bridge, do mcp-server e da evaluation-api | compose `:1480`, `:620`, `:666` |
| Seu cabeçalho diz *"Thin HTTP wrapper … for E2E testing"* | `src/index.ts:1-7` |
| O `skill-flow-worker` está morto para MCP, e o código admite | `orchestrator-bridge/main.py:638-639` |
| Ele ainda consome `workflow.events`, mas posta em `/mcp`, rota inexistente | `engine-runner.ts:131` × mcp-server só expõe `/sse` e `/messages` |
| Mapa de servidores MCP hardcoded com 2 entradas + **fallback silencioso** | `skill-flow-service/src/index.ts:35-38`, `:142-144` |
| `agente_contexto_ia_v1.yaml:96` aponta para `mcp-server-crm`, que **não existe** | O erro é "tool desconhecida", não "servidor não configurado" |
| `mcp-server-auth` é domain server legítimo (porta 3150) | Prova que o conceito funciona |

**Consequência para a fase 2:** a costura C **não é reusar um caminho que funciona** — é fazer o
caminho funcionar pela primeira vez em forma de produção.

**Escopo da promoção:** mover para `packages/skill-flow-service/`, absorver o catálogo `mcp_servers`,
implementar `tools/list`, **remover o fallback silencioso**, decidir o destino do `skill-flow-worker`.

*Ressalva: "404" e "env ausente" são conclusões estáticas, não observadas em execução.*

---

## 10. O que fica e o que sai

O critério é o do §0: **fica o que produz ou consome fronteira, ou é governança de contato com
pessoa; sai a camada de orquestração.**

| Componente | Veredicto | Razão |
|---|---|---|
| `mailing-api` **inteiro** | **Fica** | §10.1 |
| `contact_policy` / `contact_log` / `contact_eligibility_check` | **Fica** | Governança de contato, e cross-campanha |
| `scheduler-api` | **Fica** | §10.2 |
| `calendar-api` | **Fica** | §10.3 |
| `dialog-api` + editor de DialogForm | **Fica** | Conteúdo conversacional versionado |
| Engine (promovido a runner) | **Fica** | §5.3 |
| Skill como envelope de config, slot/deploy | **Fica** | §5.1 |
| **Editor de fluxo (`agent-flow`)** | **Sai por completo** | O alvo |
| **Bloco `flow:` de todo skill** (`outbound_dispatch`, `outbound_worker`, `deploy_promote`, agentes) | **Sai** | O alvo |
| `workflow-api` | **Escopo reduzido** | **Corrigido 2026-08-17 pela triagem — §10.4.** Sai o motor; a **porta** (trigger por token) é o único escritor de `workflow.instances` e produz fronteira |
| `skill-flow-worker` | **Sai** | **Corrigido 2026-08-17 — §10.4.** *"Conserta"* não é opção viável: 3 das 4 saídas são 410 e a 4ª é uma rota que não existe |
| Importador CSV/xlsx | **Escopo reduzido** | O parsing vai para o n8n; a camada A (`batch_ingest`) sobrevive como seam público |

### 10.1 Outbound — correção da revisão 2

A revisão 2 dizia que `mailing-api` (drain/pacing) sairia. **Está errado, e por um motivo bobo: no
PlugHub o pacing já não mora no `mailing-api`.** Pacing é a agenda recorrente; o laço é o step `loop`
do skill. O `mailing-api` é o **modelo de estado**.

**O que o n8n dá:** Loop Over Items (batching sequencial), Wait node (ritmo), Batching do HTTP
Request, Remove Duplicates (com histórico entre execuções) e Data Tables (estado persistente).

**O que o n8n não tem:** entidade de audiência; máquina de estado por destinatário (`campaign_deliveries`
com claim atômico `FOR UPDATE SKIP LOCKED`, idempotência por `UNIQUE(campaign_id, mailing_entry_id)`,
contagem contra `max_attempts`); separação **membership ≠ suppression** (o Remove Duplicates colapsa
as duas); fadiga cross-campanha em camadas com janelas; opt-out com precedência.

⇒ **O n8n orquestra; o estado continua no PlugHub.** O workflow vira: Schedule Trigger →
`campaign_drain` (claim) → Loop → por item `contact_eligibility_check` → dispara pela costura A →
`campaign_delivery_result`.

⚠️ **Risco de regressão:** o fan-out da fase 5a existe porque era preciso paralelismo. **Loop Over
Items é sequencial por construção.** Migrar ingenuamente troca fan-out por fila serial e derruba
throughput. Paralelizar no n8n é possível (queue mode, sub-workflows sem aguardar), mas é decisão
explícita, não default.

### 10.2 Agendamento — `scheduler-api` fica

Caso aberto na revisão 2, **fechado**. Quatro motivos:

1. **Cron não é agendamento em dia útil.** `business_day_policy` consulta a calendar-api para
   "próximo slot aberto" honrando feriados e horário por tenant. Schedule Trigger é cron puro.
   Reimplementar seria reconstruir a parte interessante do scheduler — e o desenho é justamente
   **não** reimplementar o "quando", delegando à única autoridade.
2. **Autoria com ABAC.** Supervisor cria, pausa e dispara em `/config/schedules`, gateado por
   `scheduler.{configurar,operacao}`, sem ver o n8n. O n8n não tem ABAC amarrado aos pools.
3. **Ledger com drill-through.** `agenda_dispatches` referencia `session_id`; migrar exigiria
   refazer por costura o que hoje é nativo.
4. **O horário é atributo de entidade de domínio do PlugHub.** Jogar o "quando" para fora parte a
   entidade entre dois **sistemas** — e aí olhar uma campanha em `/config/outbound` não diz mais
   quando ela roda.

**E o scheduler sai ileso de graça**, porque a agenda **aciona um pool, nunca um skill** (invariante
S4). O pool continua o endereço; muda só o que roda atrás. Diff zero — como a Fase 2 do scheduler já
provou quando o promote agendado entrou sem tocar no serviço.

> **Correção registrada:** *"campanha de qualidade precisa do scheduler"* **não** é motivo válido — a
> campanha de avaliação é **event-driven**. `period_start`/`period_end` são **filtro de
> elegibilidade** sobre `closed_at` (`evaluation-api/main.py:162-176`, aplicado com `continue` em
> `:304-306`), não agenda. Instâncias nascem do consumidor Kafka de `conversations.session_closed`
> (`main.py:401-409`). Zero dependência do `scheduler-api`.

### 10.3 `calendar-api` — autoridade temporal de dois módulos

Não é só dependência do scheduler. A **evaluation-api também a consulta**: o `_run_dispatch_scanner`
(`main.py:513-529`) gateia o despacho de instâncias por `campaign_dispatch_open()`
(`sampling.py:454-479`), que chama a calendar-api com `entity_type=evaluation_campaign`.

Há portanto **duas janelas** na campanha de qualidade, e só uma é temporal:

| Janela | Mecanismo | Papel |
|---|---|---|
| **De dados** | `period_start`/`period_end` | Filtro sobre `closed_at` — quais sessões entram |
| **De despacho** | dispatch scanner + calendar-api | Quando instâncias `scheduled` viram `evaluation.requested` |

⇒ `calendar-api` fica, com dois consumidores, e é autoridade compartilhada — não detalhe do
scheduler.

### 10.4 `workflow-api` e `skill-flow-worker` — correção da revisão 3

Escrito na revisão 3 como *"Sai"* e *"Sai ou conserta"*. A triagem de 2026-08-17 mediu, e **nome de
pacote não era unidade de decisão**: são três coisas dentro de um pacote só, com três destinos.

| Parte do `workflow-api` | Destino | Evidência |
|---|---|---|
| Lifecycle já-410 (`persist-suspend`, `complete`, `fail`, `collect/persist`, `collect/respond`) | **Sai** | `router.py:223, 357, 379, 509, 542` |
| `POST /v1/workflow/trigger` | **Sai** | Proxy sem estado para a borda: `router.py:158-194`, `gw_url` em `:184` |
| **Trigger por token + `workflow.instances`** | **FICA** até a costura A absorver | **Único** escritor: `db_create_instance` em `router.py:797`, dentro de `trigger_via_webhook` (`:727`). **11 leitores** na UI: `platform-ui/src/modules/workflows/api/hooks.ts:57,88,115,158,202,272,303,326,342,355,365` |

**`skill-flow-worker` → Sai, sem alternativa.** Das **cinco** saídas HTTP, **quatro** são endpoints hoje
410 (`workflow-client.ts:79, 90, 104, 120`), a quinta posta em `${mcpServerUrl}/mcp`
(`engine-runner.ts:131`) — rota que **não existe**: o mcp-server expõe `/sse` (`server.ts:1182`) e
`/messages` (`:1258`) — e a única que funciona é um `GET` de leitura (`:71` → `router.py:444`).
Consertar seria reconstruir para um caminho sendo abandonado. *(O kickoff dizia "três saídas, dois
410"; o número certo é este, conferido em 2026-08-17.)* **Não leva junto**
o tópico `workflow.events`, que tem dois consumidores vivos e independentes do pacote: evaluation-api
(`main.py:37-124`) e analytics-api (`consumer.py:332`).

⚠️ **O `collect` está longe dos dois.** Executa no `skill-flow-engine`
(`src/executor.ts:308` → `src/steps/collect.ts:33`), hospedado pelo `skill-flow-service`; quem cria a
sessão-filho de contato é o channel-gateway (`adapters/webhook.py:2023` `handle_collect_engage`, cujo
docstring em `:2032-2033` diz *"this is the only place a session is created"*). **Aborta-se o motor de
workflow; não se aborta o `collect`.**

---

## 11. Fases

| Fase | Conteúdo | Depende de |
|---|---|---|
| **0a** | Fechar a rota anônima `POST /v1/channels/webhook/pool/{pool_id}` — **frente NOVA, sem item no backlog**; ver nota abaixo. Junto: posse do item no resume externo e `source` não-asserido-pelo-cliente | — |
| **0b** | Costura A + E; contrato de propagação de `root_session_id`; **sub-workflow template** | 0a |
| **0c** | Logar auto-mint de `root_session_id` em disparo externo | — |
| **1** | Superfície de resultado honesta: status de 3 estados + artefato buscável | 0b |
| **2a** | **Promover `skill-flow-service`** a pacote de primeira classe (§9) | — |
| **2b** | Catálogo `mcp_servers`; roteamento por `mcp_server`; remover fallback mudo | 2a |
| **2c** | `tools/list` + snapshot no slot + pin de `versionId` e reconcile | 2b |
| **2d** | `session_id`/`root_session_id` no envelope MCP; `execution_id` em `mcp_audit_log` | 2b |
| **2e** | Seletor `server → tool` na tela de config do skill | 2c |
| **3-gate** | **Instrumentar** latência de turno e travessias no perfil `workflow` — **condição de prosseguir** | 2b |
| **3** | Costura B: principal externo (compartilhado com A2A fase A2) | 3-gate |
| **4** | Migrar perfil `workflow`; fachada OpenAI no ai-gateway | 3 |
| **5** | **Bloqueantes:** promover o interpretador a serviço (§5.3) + mapeadores de `flow_definition`/`pipeline_state` (§5.4). Só então migrar perfil `agent`; node compilado | 4 |
| **6** | Remover o editor `agent-flow`; reavaliar o que sobrou da §10 | 5 |

> **A medição vem antes do ponto sem volta.** O gate da fase 3 não pergunta "devemos prosseguir?" —
> o alvo está decidido. Pergunta "batemos num impedimento?".

> ⚠️ **A fase 0a não tinha dono** (achado da triagem de 2026-08-17). Era natural supor que o arco
> *"Autenticação de endpoint webhook"* (`TODO.md:1599`) a cobrisse — ele fecha a porta **por
> identificador**, com `auth_required` por `ChannelEndpoint`, token, hash e rotação. Mas a rota **por
> pool** fica de fora por construção: `channel-gateway/…/main.py:1011-1048` recebe só
> `(pool_id: str, request: Request)`, sem `Depends`, sem `_require_*`, sem middleware global — e o
> `tenant_id` vem do **corpo** (`:1037`), logo é cross-tenant assim que a superfície for publicada.
> Nenhum `auth_required` muda isso, porque a rota não passa pelo registro de endpoint. É trabalho novo,
> e é o primeiro trabalho da fase 0.

> **Fusão obrigatória na fase 3.** A costura B (principal externo), a fase **A2** do
> `adr-a2a-server-binding.md` (`a2a_client`, `:126-127`) e o item `Agent Principal`
> (`agent_principals`, `agent-principal-identity-spec.md:40-46`) são **três nomes para o mesmo
> mecanismo** — duas tabelas no mesmo serviço, com campos quase idênticos. Construir as duas viola o
> §13 (*"não criar segundo mecanismo de principal externo"*). A fusão precisa preservar o que a A2
> sozinha não cobre e a **fase 2d** exige: `origin: native` (`spec:43, 68-71`) e
> `principal_id`/`subject_type` no `AuditRecord` (`spec:77`).

---

## 12. Invariantes novos

1. **n8n toca sistemas; PlugHub toca pessoas.** Todo contato atravessa o channel-gateway.
2. **Proibido publicar tool de canal como domain MCP server do n8n.**
3. **O PlugHub não constrói tela para execução de n8n.**
4. **Execução de n8n nunca é gravada em `sessions`, `segments` ou como journey.** Correlação por
   `session_id`; `execution_id` em `mcp_audit_log`.
5. **n8n é recurso chamado, nunca alocável.**
6. **Registro de domain server n8n exige retenção de execução desligada** quando a tool recebe PII.
7. **`updatedAt` nunca é identidade de versão.**
8. **DialogForm não recebe control-flow.** Skip-logic declarativa é o limite.
9. **Valor mascarado nunca atravessa o n8n** — o n8n recebe o resultado da transação, jamais o valor.

---

## 13. Riscos

| Risco | Sinal de que aconteceu |
|---|---|
| Journey partida por falta de propagação | `root_session_id == session_id` em disparo externo |
| Reconstrução do n8n por dentro | Aparece tela de "automações" no PlugHub |
| DialogForm virando linguagem de fluxo | Pedidos de `next` condicional, laço ou variável no form |
| **Throughput de campanha regredindo** | Campanha grande passa a levar horas — Loop Over Items serial substituindo fan-out (§10.1) |
| Avaliação tier-2 apagando sem alarme | Faithfulness/tool-correctness ficam planas após a migração |
| Vazamento de PII pelo execution log do n8n | Auditoria encontra PII em execução retida |
| n8n virando pool | `available` não fecha; `deploy_version` sem snapshot correspondente |
| Edição silenciosa atrás do ponteiro de tool | `versionId` diverge do pin sem promote |
| Contrato Kafka congelado cedo demais | Mudança de schema interno quebra automação de cliente |

**Não fazer:** registrar n8n como agente/pool/skill; estender `origin` para marcar autoria de
disparo; criar tabela de execuções; criar segundo mecanismo de principal externo; abrir HTTP de saída
genérico no `invoke`; manter o editor como "fallback" sem investimento.

---

## 14. Defeitos colaterais encontrados

Achados no levantamento, **independentes do n8n**, e que valem correção por si:

1. **`llm_tokens_*` não é emitido no caminho principal.** `emit_llm_tokens` tem um único call site —
   `InferenceEngine.infer()` (`ai-gateway/inference.py:149-161`) = `POST /inference`. O
   **`/v1/reason`**, que é o step `reason` dos skill flows, **não emite**; `/v1/turn` também não.
   Verificar se alguma cota ou relatório de tokens está sendo lido como se tivesse dado.
2. **O hint de backfill mente.** A UI de campanha de qualidade diz *"Past start = reprocesses history
   (backfill)"*, mas o backfill é endpoint manual (`POST /v1/evaluation/campaigns/{id}/backfill`,
   `router.py:1151-1186`) e **a UI nunca o chama** — grep por `backfill` em `modules/evaluation/`
   devolve só a própria string do hint (`CampaignsPage.tsx:600`). O operador põe data no passado e
   não acontece nada, sem sinal.
3. **Campo morto homônimo.** JSONB `schedule` com `window_start`/`window_end`/`days_of_week`
   documentado em `evaluation-api/db.py:73`, sem nenhum leitor. Resíduo de desenho substituído pela
   calendar-api — e quem procurar "janela" acha esse primeiro.
4. **Fallback silencioso do resolvedor MCP** (`skill-flow-service/src/index.ts:142-144`): servidor
   desconhecido é roteado ao mcp-server-plughub em vez de falhar. Produz o erro errado e desvia o
   diagnóstico.
5. **`MCP_PROXY_URL` aponta para serviço inexistente.** `tools/evaluation.ts:803` faz `fetch` em
   `localhost:7422`; não há proxy em nenhum compose; o `catch` só emite `console.warn`.
6. **DECR de `hook_pending` não inspeciona outcome** (`orchestrator-bridge/main.py:4952`); o
   tratamento de `suspended` é guardado por `not conference_id` (`:4663`, `:4784`). Borda desguardada.
7. **Não foi localizado o guard do engine** que rejeitaria `delegate` fora de sessão webhook — o
   comportamento se apoia em comentário e wiring.
8. **`inline` tem dois significados** em `PoolHookEntry` conforme o `side`.
9. **Assimetria de permissão entre bordas** (`lib/invoke-audit.ts:40-46`): o sidecar aceita
   `server:*` e trata lista vazia como sem filtro; o `invoke` nega. Dívida já no ADR de borda única.

**Acrescentados pela triagem de 2026-08-17** (`docs/product/n8n-triagem-2026-08-17.md` §8):

10. **Avaliador de `ask_when` triplicado.** Canônico em `packages/schemas/src/dialog.ts:423`
    (`evaluateAskWhen`); espelho JS em `channel-gateway/…/survey_web.py:386`; **terceiro** espelho em
    `platform-ui/…/DialogFormRenderer.tsx:400`, cujo comentário em `:75` se declara *"mirror of
    `evaluateAskWhen`"*. O ADR previu dois. Três implementações do mesmo veredicto divergem — é o
    mesmo modo de falha do item 9.
11. **`masked_input_fields` é um contador de ausência sem testemunha.** Existe em
    `analytics-api/…/audit.py` e é filtro do endpoint de auditoria LGPD, mas **não tem escritor** —
    sempre `[]`. Para o DPO, *"nenhum campo mascarado nesta sessão"* e *"ninguém nunca escreveu"* são
    indistinguíveis.
12. **`EventsView` pede `period=24h` a um endpoint que só aceita `from_dt`/`to_dt`**
    (`MonitorTab.tsx:794` × `reports.py:1431`): a janela real é de 7 dias e o i18n diz *"últimas 24h"*.
13. **`spawn_reason` tem zero amostras de `collect`/`delegate`** no demo (só `NULL` 349 e `trigger`
    71) — e é dele que a visão 2 do histórico deriva a direção do acesso. Medir antes de renderizar.

---

## Referências

- `docs/product/agentes-externos-reclassificacao.md` — precedente que **não** decide este caso (§2.2)
- `docs/adr/adr-mcp-interception-single-border.md` — fase B2 hospeda a costura C
- `docs/adr/adr-a2a-server-binding.md` — fase A2 hospeda o principal externo da costura B
- `docs/adr/adr-journey-session-segment-model.md` — as três fronteiras
- `docs/adr/adr-dialog-conditional-skip-logic.md` — a guarda do §5.5
- `docs/adr/adr-otp-workflow-and-dialog-primitive.md` — o padrão do §5.3
- `docs/arcos/arc19-unified-session-model.md` — perfis `workflow` × `agent`
- `docs/product/n8n-backlog-triage-kickoff.md` — triagem do backlog contra esta linha
