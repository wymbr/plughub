# n8n × PlugHub — fronteiras, costuras e escopo

> **Status: proposto** — 2026-08-17 (revisão 2)
> **Decide:** o que o n8n assume, o que o PlugHub retém, por onde os dois se falam, onde vivem
> relatórios e histórico, e o que sai de escopo por ser duplicação.
> **Alvo declarado:** substituir o **bloco de fluxo** do skill pelo n8n, eliminando o editor de
> fluxo local. O skill sobrevive como **envelope de configuração**.
> **Não decide:** cronograma, nem hospedagem do n8n.

---

## 0. Resumo

O n8n cobre, com um container e centenas de conectores prontos, uma faixa que o PlugHub
reimplementou. A faixa foi **medida** (§1) e é menor do que parece — ~12% do código de produção —,
mas é onde o PlugHub compete pior e onde nenhum argumento sustenta construção própria.

A decisão é **parar de competir nessa faixa e integrar**, com quatro costuras (§4), uma regra de
fronteira, e a preservação integral da borda de canal:

> **n8n toca sistemas. PlugHub toca pessoas.**

O PlugHub retém o que produz as três fronteiras do modelo — `journey`, `session`, `segment` —
porque elas nascem da **travessia da borda**, não do código que decidiu atravessar (§3). Enquanto
todo contato, inbound e outbound, passar pelo channel-gateway, o n8n pode ser o autor da lógica sem
nunca ser o autor da fronteira.

**O alvo é mais ambicioso que integração:** eliminar o editor de fluxo local (§5). Ele não
acompanha os editores de mercado, e restaurá-lo não resolveria isso — o seguro contra dependência
não é um editor apodrecendo, é a **portabilidade** que a plataforma já vende (`certify`,
`verify-portability`, `skill-extract`, `regenerate`). Com o skill preservado como configuração, o
resíduo não-movível se reduz a **dois itens**: evidência de execução para avaliação tier-2 (§5.3) e
hook de cliente inline (§5.4).

---

## 1. A medição

Arquivos de produção em `packages/` (excluídos testes, `dist`, `node_modules`), 2026-08-17.
**575 arquivos.**

| Classe | Arquivos | % | Composição |
|---|---:|---:|---|
| **Fosso real** | ~257 | 45% | routing-engine (16), channel-gateway (33), evaluation-api (12), quality-ingest+export (14), session-replayer (9), mcp-server-knowledge (6), auth-api (10), rules-engine (13), orchestrator-bridge (4), ~26 do mcp-server-plughub, ~104 da platform-ui |
| **Território n8n** | ~66 | 12% | workflow-api (9), skill-flow-worker (5), scheduler-api (8), mailing-api (7), calendar-api (5), UI workflows/outbound/schedules (16), 7 dos 17 step types |
| **Infra compartilhada** | ~250 | 43% | schemas (32), sdk (34), analytics-api (28), agent-registry (25), ai-gateway (18), config-api, pricing-api, usage-aggregator, componentes de UI |

**Duas limitações declaradas.** (a) Contagem de arquivo não é esforço nem valor, e a platform-ui
infla. Serve para ordem de grandeza; para orçar, refaça por linhas ou commits. (b) **A medição
excluiu `packages/e2e-tests/`** — e o runtime de produção dos skills conversacionais vive lá
(§9). O número está subestimado no exato ponto onde a integração aterrissa.

**Leitura:** não foi construído um clone de n8n. Foi construída uma sobreposição de ~12% que carrega
peso narrativo desproporcional, enquanto os 45% que ninguém mais tem não estão sendo contados como
o produto.

### 1.1 O corte fino dos step types

`packages/skill-flow-engine/src/steps/` tem **17 arquivos**:

| Conversacionais — exigem sessão, canal, participante, masking | Genéricos — o n8n faz melhor |
|---|---|
| `menu`, `notify`, `collect`, `receive`, `resolve`, `task`, `escalate`, `delegate`, `begin-transaction`, `end-transaction` (10) | `choice`, `catch`, `invoke`, `reason`, `complete`, `suspend`, `loop` (7) |

A segregação de perfis do Arc 19 já é a fronteira com o n8n, desenhada antes de a pergunta existir:
perfil `workflow` (`channel_type: webhook`) é quase todo território n8n; perfil `agent` é fosso.
Mas **o critério final não é o perfil, é o resíduo** (§5.3).

---

## 2. O que o n8n é — e o que nunca é

**Teste canônico:** *a plataforma precisa saber quantos deles existem e se estão vivos?*

- **Sim** → recurso **alocável**: exige instância com identidade, heartbeat, `max_concurrent`,
  pausa, `claim_instance`, `agent_done` com `issue_status`.
- **Não** → recurso **chamado**: MCP server ou canal.

O n8n gerencia a própria concorrência (queue mode, workers). O PlugHub não sabe nem deve saber
quantos workers n8n existem. **Logo: recurso chamado.**

| Papel | Correto? | Observação |
|---|---|---|
| Canal / cliente do canal `webhook` (entrada) | ✅ | O Arc 19 já modelou workflow como canal `webhook` |
| Domain MCP server (saída) | ✅ | É `mcp-server-crm`, não `agente_retencao_v1`. Mesma natureza do `external-mcp` |
| Skill publicado num pool | ❌ | Seria o **runtime importado** |
| Agente / instância / pool | ❌ | Idem |

### 2.1 O padrão único: pool com skill que chama o n8n

**Pool com skill nativo do PlugHub cujo corpo delega ao n8n por `invoke`.** A instância é do
PlugHub (contabilidade correta), o segmento existe, `segments.deploy_version` descreve o deploy, e
o n8n entra como domain server auditado.

> **O pool contém um skill que CHAMA o n8n. O pool não contém o n8n.**

Vale para os **dois** perfis, não só o `workflow`.

**Modo de falha se registrado como pool "n8n"** — quatro defeitos simultâneos: `available` publica
capacidade que ninguém garante; a admissão debita `{t}:admission:kind:ai` para algo que não é
licença de IA; `deploy_version` carimba snapshot que não descreve o que rodou; não há `agent_done`
honesto para fechar o segmento.

**Condição de capacidade:** capacidade n8n ≥ Σ `max_concurrent` dos pools que delegam a ele. Nessa
forma o PlugHub **é** o limitante e a garantia de capacidade permanece real. Vira item de
monitoramento, não bloqueio arquitetural.

### 2.2 A decisão de 2026-08-13 não decide esta questão

`docs/product/agentes-externos-reclassificacao.md` rebaixou o **runtime importado** com o argumento
de que a plataforma passaria a garantir capacidade, heartbeat, pausa e `agent_done` sobre código que
não controla. Aquilo era sobre agente de **terceiro** (LangGraph, CrewAI de outro fornecedor).

Um n8n operado pelo **próprio tenant** tem os dois lados sob a mesma administração. O argumento não
transfere limpo, e este documento **não** o invoca como se fechasse a questão. O que fecha é o §2.1:
na shape do pool-com-skill, não há runtime importado nenhum — o n8n é chamado, não hospedado.

---

## 3. As fronteiras: quem produz journey, session, segment

**Nenhuma das três é declarada pelo fluxo.**

| Fronteira | Quem produz | Natureza |
|---|---|---|
| **segment** | orchestrator-bridge, quando um participante entra e sai | **Estrutural** |
| **session** | channel-gateway, na travessia da borda | **Estrutural** |
| **journey** | `root_session_id` propagado pelo chamador (∪ `journey_merge`) | **Contratual** |

O fluxo **dispara** fronteiras; nunca as define — e sempre chamando um primitivo que é dono dela.
`choice`, `catch`, `loop` e `reason` produzem **zero** fronteiras; é por isso que são os que o n8n
pode assumir sem consequência.

### 3.1 A borda é a única fábrica de sessão

Contato inbound de canal real, webhook, `collect` outbound e survey web nascem todos no
channel-gateway. **É isso que torna o modelo robusto à substituição da lógica.**

**Regra de fronteira, normativa:**

> **n8n toca sistemas. PlugHub toca pessoas.**

Único modo de quebra: se um workflow n8n mandar um WhatsApp direto pela API da Meta, aquele contato
**não existe** para o PlugHub — sem sessão, sem segmento, sem journey, sem masking, sem avaliação,
sem audit. Não é o editor que importa; é quem encosta no canal.

### 3.2 A journey é a fronteira frágil

`root_session_id` é *"param propagado do chamador ou auto-mint = `self`"*. Se o n8n disparar o
segundo contato de um processo sem propagar, **não dá erro**: a sessão vira sua própria raiz e nasce
uma journey nova. O processo se parte em dois e ambos parecem válidos — *um valor plausível
escondendo um bug*, e o auto-mint é degradação silenciosa.

Mitigações, em ordem de força:

1. **Propagação explícita e obrigatória** em qualquer disparo n8n que continue um processo. O
   sub-workflow/node publicado carrega o campo como cidadão de primeira classe (§4.5).
2. **Logar o auto-mint** quando o disparo vem de origem externa. Fecha uma violação existente da
   postura de engenharia, independente do n8n.
3. `journey_merge` como reparo — existe, mas reparo é remendo.

---

## 4. As costuras

Estado medido no código em 2026-08-17.

### 4.0 Quatro fatos que condicionam tudo

1. **O PlugHub não tem saída HTTP genérica.** A única chamada a URL de terceiro é o
   `WebhookProvider` de `channel-gateway/.../survey_web.py` (entrega de link de survey).
2. **A entrada existe e está aberta.** `POST /v1/channels/webhook/pool/{pool_id}` é **anônima**.
   `POST /channel/webhook/{slug}` é a porta limpa (`allowed_origins={"external"}`, token opcional
   via `X-Webhook-Token`).
3. **Não existe superfície de resultado.** O trigger devolve só `{session_id}`; o
   `GET /v1/channels/webhook/{session_id}/status` responde `"closed"` tanto quando terminou quanto
   quando a chave não existe.
4. **O caminho de cliente MCP existe mas nunca funcionou em produção** (§9). A tool `invoke` usa
   `Client` + `SSEClientTransport` oficiais, mas resolve endereço por convenção de env
   `MCP_SERVER_{NOME}_URL` — **não definida em lugar nenhum do repositório**. E `tools/list` não
   é chamado em nenhum ponto do código.

### 4.1 Costura A — n8n → PlugHub por webhook  *(existe hoje)*

```
n8n HTTP Request
  → POST /channel/webhook/{slug}
    X-Webhook-Token: <token>
    { tenant_id, customer_id, root_session_id?, context, journey }
  ← 201 { session_id }
```

**A não é utilizável sozinho.** Das três saídas para o n8n saber que terminou, só a terceira
funciona hoje: polling no `status` mente; callback HTTP não existe; **Kafka Trigger em
`conversations.session_closed`** funciona.

⇒ **A fase 0 é `A + E`, nunca A isolado.**

### 4.2 Costura E — n8n consome eventos  *(existe hoje)*

n8n Kafka Trigger em `conversations.session_closed`, `evaluation.events`, `session.signals`,
`agent.lifecycle`, `journey.merges`.

**Preço:** tópico interno vira contrato público. **Mitigação diferida:** consumidor de fan-out
traduzindo para envelope versionado, quando o contrato precisar de estabilidade — não antes.

### 4.3 Costura B — n8n como cliente MCP  *(construir)*

O mcp-server-plughub expõe ~60 tools sobre SSE (`GET /sse` + `POST /messages`). Com B o n8n deixa de
ser gatilho e vira **participante**: consulta antes (`pool_status_get`), dispara
(`workflow_trigger`), acompanha (`work_queue_list`), lê contexto.

**Bloqueio: principal externo.** Toda tool valida `session_token` de `agent_login`, que é identidade
de *instância de agente*. Um caller n8n não é isso. Precisa de principal novo, token por endpoint,
`tenant_id` **nunca do corpo**, escopo de tools por credencial.

> Mesmo trabalho que a fase A2 do `adr-a2a-server-binding.md`. **Não criar dois mecanismos de
> principal externo.**

⚠️ Não foi encontrada auth de transporte na frente de `/sse` e `/messages`. Verificar antes de expor.

### 4.4 Costura C — n8n como domain MCP server  *(construir — maior retorno)*

```
skill (config):
  invoke → target: { mcp_server: "mcp-server-n8n-itsm", tool: "servicenow_case_create" }

engine → ctx.mcpCall(tool, input, server)
       → mcp-server-plughub: permissão (JWT) → injection guard → mcp.audit
       → despacha ao servidor registrado
       → n8n MCP Server Trigger → sub-workflow → ServiceNow
       ← retorno vira resultado do step
```

Três propriedades preservadas:

- **Borda única intacta.** O veredicto continua no mcp-server; o n8n é *domain server*.
- **Sem HTTP arbitrário.** O skill escolhe nome de servidor registrado, não URL.
- **Audit LGPD por construção.** Cada chamada vira linha em `mcp.audit`. Não se compra conectores —
  compra-se **conectores governados**.

**Invariante de registro:** é **proibido** publicar tool de canal como domain MCP server do n8n.
`salesforce_case_create` é legítimo; `whatsapp_send` transforma C num buraco na borda e viola §3.1.

#### 4.4.1 Endereçamento — não é um id por workflow

O endereço **já existe** e é o par `(mcp_server, tool)` — é literalmente a string de permissão que o
`invoke` exige no JWT (`invoke-audit.ts`, `judgeInvoke`). Um workflow n8n publicado vira **tool
dentro de um server**.

```
1 domínio     = 1 workflow n8n com MCP Server Trigger = 1 mcp_server
N automações  = N sub-workflows                       = N tools
```

Agrupar por domínio, não por automação: server-por-workflow explodiria a lista de permissões e
mataria o curinga `server:*`.

**Duas disciplinas de contrato:** o id do server é **estável e sem versão** (mesmo raciocínio do
`skill_id`); **nome de tool é contrato público** — renomear quebra os skills que referenciam e faz a
permissão parar de casar, falhando fechada mas em silêncio para o autor.

**Superfície de configuração (net-new, requisito):** a convenção de env `MCP_SERVER_{NOME}_URL` é
impopulável pela UI e hoje está vazia. Substituir por catálogo no config-api, copiando o precedente
do **LLM Accounts Catalog**: namespace `mcp_servers` com `{id, display_name, url, transport,
active}`, token só em env por convenção de nome. No editor de config do skill, o `invoke` passa a
ter seletor `server → tool` sobre o catálogo.

**Simetria com a entrada:** inbound o endereçável é o **pool**, nunca o `skill_id`; outbound é
**`(mcp_server, tool)`**, nunca o id do workflow. Nos dois sentidos o endereço é o *endpoint
estável*, jamais o artefato que roda atrás dele.

#### 4.4.2 Atribuição de versão

**Verificado no n8n (agosto/2026):** `versionId` e `updatedAt` existem na API pública; Workflow
History guarda versões (completo em Enterprise, 5 dias no Cloud Pro, 24h nos demais). **Mas o
workflow em execução não vê a própria versão** — `$workflow` expõe `id`, `name` e `active` apenas;
há feature request aberto pedindo o metadado. Logo **o eco da versão pela própria tool não é
implementável nativamente.**

Desenho que funciona hoje, com duas fontes que se conferem:

| Fonte | Natureza | Cobre |
|---|---|---|
| **Nome da tool** (`agent_atendimento_v3`) | In-band, por execução — já gravado em `mcp.audit` como `tool_name` | Atribuição |
| **Snapshot de `tools/list` no slot, no promote** | Conteúdo do contrato (nome + JSON schema) | Drift de **assinatura** |
| **`versionId` fixado no promote + reconcile** | Identidade da versão | Drift de **comportamento** com mesma assinatura |

A discrepância entre as fontes é o alarme. O snapshot de `tools/list` é superior ao `versionId` para
contrato, porque é **conteúdo canonicalizado** — satisfaz a regra de comparar conteúdo em vez de
timestamp — e não depende de tier Enterprise.

**Nunca usar `updatedAt` como identidade** (a lição do `updated_at` bumpado a cada boot).
**Consequência de licença:** o alarme de drift funciona em qualquer tier; a pergunta forense *"o que
a v2 continha?"* exige Workflow History (Enterprise).

### 4.5 Costura D — node/template PlugHub no n8n  *(fase 5; precursor na fase 0)*

**Não descartada — sequenciada.** O node é onde mora a **disciplina de propagação de
`root_session_id`** (§3.2), a única fronteira sem enforcement estrutural. Um HTTP Request genérico
vai esquecer; um artefato PlugHub faz do campo cidadão de primeira classe.

- **Fase 0:** **sub-workflow template** ("PlugHub: disparar contato"). Versiona como dado, custa
  quase nada, entrega a maior parte da ergonomia.
- **Fase 5:** node compilado publicado, se a adoção justificar — e só depois da superfície de
  resultado, para não distribuir algo quebrado nem herdar compatibilidade retroativa cedo demais.

---

## 5. O alvo: eliminar o editor de fluxo local

**Justificativa:** o editor não acompanha os editores de mercado, e **restaurá-lo não resolveria
isso** — cair no fallback é voltar ao estado julgado insuficiente, e um editor sem investimento
apodrece contra a plataforma que continua andando.

**O seguro correto não é um editor de reserva — é portabilidade.** `certify`,
`verify-portability`, `skill-extract`, `regenerate` respondem *"posso sair daqui?"*, e o CLAUDE.md
os mantém deliberadamente separados da reclassificação por isso. Com os workflows endereçados por
`(mcp_server, tool)` sobre MCP aberto, trocar de n8n é mudar entrada de registro, não reescrever.
**Ação devida:** o alvo da portabilidade passa a incluir o JSON do n8n; `skill-extract` perde o
YAML como entrada.

### 5.1 O skill sobrevive como envelope de configuração

**Só o bloco `flow:` sai.** O skill continua sendo a entidade que carrega:

`config_params` · `interface_schema` · política de masking · declaração de perfil ·
`mention_commands` · identidade e versionamento

Consequências:

| Item | Efeito |
|---|---|
| Campos não-fluxo do YAML | **Mantêm a casa** — zero migração |
| Modelo de deploy (slot, `set-next`/`promote`, `deploy_version`) | **Inalterado.** Muda só a carga do snapshot: de `yaml_snapshot` para config + referência + snapshot de `tools/list` |
| `interface_schema` → `config_json` → `$.config` | **Intacto** — é o que parametriza o dialog primitive |
| Guard de perfil | **Migra**, não some: de parse-time para boundary-time (a tool recusa quando o perfil do pool não permite). Mais fraco, porque pega na execução |
| `mention_commands.trigger_step` | **Redesenho**: id de step → nome de tool no mesmo server (sub-workflows n8n são as unidades endereçáveis) |

**Consequência de segunda ordem:** um skill que é config declarativa sem lógica **não precisa de
YAML escrito à mão** — vira formulário na UI, satisfazendo *"every config field is UI-editable"* sem
canvas nenhum. O que morre é a **tela de fluxo**; o que a substitui é uma **tela de config**.

### 5.2 O que morre, o que sobrevive

| Camada | Destino |
|---|---|
| UI do editor de fluxo (`agent-flow`, 7 arquivos) | **Morre** — parar de investir a partir de agora |
| Bloco `flow:` do YAML | **Morre** — vira referência a `(mcp_server, tool)` |
| Skill como entidade de config | **Sobrevive**, e simplifica |
| Engine (`executor`, `interpolate`, `state`, `masking-policy`) | **Sobrevive** — o dialog primitive, os hooks e a coleta mascarada dependem dele. Não é hedge, é requisito |
| Modelo de slot/deploy | **Sobrevive**, troca de carga |
| Editor de DialogForm (`/config/dialog-forms`) | **Sobrevive e ganha importância** |

### 5.3 O resíduo #1 — evidência de execução para avaliação tier-2

**É o item aberto mais caro, e é dano ao fosso, não ao editor.**

`evaluation_context_get` injeta `flow_definition` (trajetória **esperada**, do agent-registry) e
`pipeline_state` (trajetória **real**, de `session_pipeline_state`). Os dois são artefatos do engine
local. Com o fluxo no n8n, a avaliação de IA degrada para **grau-transcript** — exatamente a
limitação que o quality-ingest documenta para históricos *externos*. Seria importar voluntariamente
a restrição criada para o caso alheio.

Recuperável, mas com trabalho que ninguém lembra até o relatório de qualidade ficar plano:

- `flow_definition` ← snapshot de `tools/list` + JSON do workflow n8n via API, com mapeador.
- `pipeline_state` ← trace de execução do n8n, com mapeador.

**Atenuante:** a atribuição se parte em duas e a parte que mais importa fica em casa. O
**DialogForm já é versionado** na dialog-api (draft/published). Então n8n versiona a **lógica**;
dialog-api versiona o **conteúdo conversacional** — que é o que a avaliação mais olha.

### 5.4 O resíduo #2 — hook de cliente inline

**Verificado no código (2026-08-17).** `_is_workflow_dispatch_entry` (`orchestrator-bridge/main.py:1233`):

```python
return dispatch == "detached" or (side == "agent" and dispatch == "inline")
```

Ou seja: **só `side=customer` + `inline` permanece na conferência.** Todo o resto já migrou para o
veículo de workflow. O wrap-up, que foi `detached` em 27/07, voltou a `inline` no wrap-up unificado —
e ali `inline` significa *"auto-atendimento sobre a MESMA máquina destacada"*, não conferência.

O que resta na conferência é o NPS (`agente_nps_v1`, `on_contact_end`, `side: customer`,
`dispatch` ausente = inline em `tenant_demo.yaml:174-178` e `:405-410`), que faz `form_get` → `menu`
dinâmico → `notify` → `complete`, sem `delegate`/`suspend`.

**E ele não é destacável por natureza, não por implementação:** destacar fecha o contato na hora
(`main.py:2083`) e o cliente sai do WS; NPS destacado teria de virar outbound (o J4c collect).

⚠️ **Usar DialogForm não muda isso.** DialogForm é *conteúdo*; inline × destacado é *veículo*. O NPS
usa DialogForm **inline**.

⚠️ **Armadilha de nomenclatura:** `inline` tem dois significados dependendo do `side` — conferência
quando `customer`, máquina destacada quando `agent`.

### 5.5 A guarda que precisa ser defendida

O **DialogForm foi desenhado para NÃO ser linguagem de fluxo** — sem `next` condicional; *"branching
é do skill, senão vira linguagem em JSON"*. Com o editor de fluxo morto, haverá **pressão para
empurrar controle de fluxo para dentro do form**, e o ADR pendente
`adr-dialog-conditional-skip-logic.md` (guarda declarativa `ask_when`, *"não control-flow"*) vira
**load-bearing**.

> Se essa guarda ceder, o editor de fluxo é reconstruído dentro do editor de formulário — com uma
> linguagem pior.

**Consequência correlata:** o interpretador genérico é hoje, ele mesmo, um skill em YAML
(`skill_dialog_runner_v1`, pool `dialog_runner`). Se o YAML de fluxo morre, o runner precisa virar
**código** — serviço de primeira classe em vez de skill. Não é preservação, é promoção. O mesmo vale
para os agentes de hook.

**Direção já em curso:** `$.config` do slot, `menu.options/fields` como ref, `form_get`, as quatro
superfícies do dialog primitive, o `DialogFormRenderer` como tratamento genérico de collect-form. A
plataforma vem migrando de *"lógica em YAML"* para *"config + interpretador genérico"* há meses.
Este alvo é a extrapolação da trajetória, não uma ruptura.

---

## 6. Autoria de agente no n8n — avaliação dos quatro pontos

### 6.1 Capacidade — sem perda, na shape do §2.1

A instância é do PlugHub; vaga, heartbeat e pausa continuam no routing. Com capacidade n8n ≥ Σ
`max_concurrent`, o PlugHub é o limitante e a garantia é real, não esperança. A shape que quebraria
é n8n fazendo `agent_login` e disputando `wait_for_assignment` como instância própria — **não é o
padrão adotado**.

### 6.2 Latência — imposto mensurável, não bloqueio

O custo não é o protocolo (MCP e webhook são ambos um round trip); é a **quantidade** de travessias.
O que hoje é implícito vira explícito: `@ctx.*` interpolado em inputs e condições, `context_tags`
com confidence e merge pós-chamada, e o `resolve` de 5 fases (com a otimização de 0 LLM quando o CRM
resolve).

**Mitigação:** contrato de granularidade grossa, com forma de turno, em vez de tools finas.

> **Nota de método:** a estimativa de "5–8 round-trips" é palpite, não medição. **A decisão sobre o
> perfil `agent` exige instrumentação prévia** (§11, gate da fase 3).

### 6.3 Masking — retenção, não acesso

O agente **precisa** ver o dado aberto; o `resolveToken` existe para isso. O invariante é que
`@masked.*` **não é persistido** — não vai a Redis, pipeline_state, stream nem log; retry recoleta.

O runtime do PlugHub é construído para o valor **transitar sem pousar**; o n8n é construído para
**tudo pousar** (inspeção por nó é seu principal atrativo). Arquiteturalmente opostos nesse eixo.

Duas ressalvas sobrevivem:

- **Escopo.** Como domain server a exposição é por chamada, com params conhecidos. Como runtime de
  agente, veria **toda mensagem do cliente** — onde a PII mora de verdade.
- **`begin_transaction`/`end_transaction`.** O valor não pode pousar em lugar nenhum. **Este step
  não move.**

### 6.4 AI Gateway e a fachada OpenAI

**O overhead não é o problema** — um hop interno contra uma chamada de LLM de 1–5 s é ruído. O
problema é ergonomia: o que faz valer autorar no n8n é em boa parte o AI Agent node; chamar
`/v1/inference` cru significa reconstruir o loop agêntico à mão, cancelando metade do ganho.

**Solução: fachada compatível com OpenAI no ai-gateway.** Os nós de LLM do n8n aceitam base URL
customizada. Com `/v1/chat/completions` na borda, preservam-se rotação multi-conta, throttle
429/529, fallback cross-provider e `preferred_config_ids` por pool.

Estado medido: as rotas são `/inference`, `/v1/turn` (legacy), `/v1/reason`,
`/v1/copilot/analyze`, `/v1/health`, com contrato proprietário (`model_profile`, não `model`). **Não
há shim.** Mas `providers/openai_provider.py` já converte o formato interno *para* Chat Completions
no caminho de saída — a fachada de entrada é largamente reverter isso. `model_profile` vira
pseudo-modelo (`plughub/fast`, `plughub/balanced`).

⚠️ **A metering de tokens já está apagada no caminho principal** — ver §14.

---

## 7. Relatórios e histórico

**Separados por dono, costurados por correlação, nunca fundidos.** Uma execução de n8n não é
segment, não é session, não é journey — é uma quarta coisa.

### 7.1 Quem é dono de qual fato

| Fato | Dono |
|---|---|
| "o workflow rodou, o nó X recebeu Y e devolveu Z" | **n8n** |
| "houve um contato, durou T, com estes participantes" | **PlugHub** (`sessions`/`segments`) |
| "o cliente disse isto, o agente respondeu aquilo" | **PlugHub** (`messages` + stream canônico) |
| "a qualidade daquele atendimento foi N" | **PlugHub** (`evaluation_finalized`) |
| "o processo atravessou 3 contatos" | **PlugHub** (journey por union-find) |
| "o Salesforce foi chamado" | **ambos** — n8n tem o *detalhe*; PlugHub tem o *veredicto* |

A última não é duplicação: *"por que falhou?"* é do n8n; *"estava autorizado?"* é do PlugHub, por
invariante — o chamador não pode optar por sair do audit.

### 7.2 A costura é por ID, não por cópia

Chave de correlação `session_id`; chave de processo `root_session_id`. **Ambas já existem. Nenhuma
tabela nova.** Para o drill PlugHub → n8n, o `execution_id` vai em **`mcp_audit_log`** — é fato de
uma chamada, não da sessão inteira.

### 7.3 Onde cada pergunta se responde

| Pergunta | Superfície |
|---|---|
| "O que aconteceu com o cliente?" | Console / Analytics do PlugHub — **sempre** |
| "Por que a automação falhou?" | Executions do n8n — **sempre** |
| "A integração está autorizada e auditada?" | `mcp_audit_log` / AuditPage |

**Regra que impede a recaída:** o PlugHub **não constrói tela para execução de n8n**.

### 7.4 Contaminação de relatório — já resolvida

Sessão disparada por webhook é *etapa interna* (pool `channel_types: [webhook]`, e `channel_type` já
é filtro de primeira classe); o contato gerado via `collect` é *acesso do cliente*. Mesma distinção
"duas classes de linha" do `adr-historico-unificado-duas-visoes.md`. **Não precisa de campo novo, e
não estender o enum `origin`** — ele é procedência do substrato de qualidade, não autoria do
disparo.

---

## 8. Compliance

Na costura C o domain MCP server recebe params **desmascarados** (é para isso que existe o
`resolveToken`), e o n8n **retém input/output por default**. Reintroduz-se pelo lado de fora o
vazamento que a decisão R7 recusou por dentro.

| Mitigação | Força |
|---|---|
| Desligar retenção de execução nos workflows que tocam PII | **Obrigatória** — gate de registro do domain server |
| n8n no escopo do DPO, com retenção declarada | Obrigatória |
| Passar token em vez de valor, resolvendo no n8n | Rejeitada — exigiria n8n falando com o `TokenVault`, reabrindo a borda |

**Item de segurança urgente e independente:** `POST /v1/channels/webhook/pool/{pool_id}` é anônima.
Com n8n do outro lado é exposição ativa — qualquer um dispara qualquer pool webhook do tenant,
inclusive os que promovem deploy e contatam clientes. **A fase 0 não sobe sem fechar isso.**

---

## 9. Achado paralelo — o runtime de produção é um pacote de e2e-tests

> **Recomendação: promover `skill-flow-service` a pacote de primeira classe.** Não é assunto
> separado: é **onde a integração com n8n aterrissa**, e a promoção e a fase 2 são a mesma obra.

**Fatos medidos:**

| Fato | Evidência |
|---|---|
| Skills conversacionais executam em `packages/e2e-tests/services/skill-flow-service/` | `orchestrator-bridge/main.py:672` faz `POST {SKILL_FLOW_URL}/execute`; compose linhas 763-801, porta 3460 |
| É dependência `service_healthy` do bridge, do mcp-server e da evaluation-api | `docker-compose.demo.yml:1480`, `:620`, `:666` |
| Seu próprio cabeçalho diz *"Thin HTTP wrapper … for E2E testing"* | `src/index.ts:1-7` |
| O `skill-flow-worker` está morto para MCP, e o código admite | `orchestrator-bridge/main.py:638-639`: *"o `engine-runner.ts` … morreu junto com o skill-flow-worker no Arc 19; a doc nunca foi corrigida"* |
| O worker ainda recebe tráfego (consumer de `workflow.events`, produzido por `workflow-api/kafka_emitter.py`), mas suas chamadas MCP dão 404 | `engine-runner.ts:131` posta em `/mcp`; o mcp-server expõe `/sse` e `/messages`, sem rota `/mcp` |
| O mapa de servidores MCP é hardcoded com 2 entradas, com **fallback silencioso** | `skill-flow-service/src/index.ts:35-38`, `resolveMcpServerUrl():142-144` — servidor desconhecido vai para o mcp-server-plughub em vez de falhar |
| `agente_contexto_ia_v1.yaml:96` aponta para `mcp-server-crm`, **que não existe** | Nenhum pacote, nenhum serviço no compose. O erro que aparece é "tool desconhecida", não "servidor não configurado" |
| `mcp-server-auth` é domain server MCP legítimo | `packages/mcp-server-auth/`, compose 745-761, porta 3150 — prova que o conceito funciona |

**Consequência para a fase 2:** a costura C **não é reusar um caminho que funciona** — é fazer o
caminho funcionar pela primeira vez em forma de produção. Não é do zero (o cliente MCP está
escrito), mas também não é "uma linha".

**Escopo da promoção:** mover para `packages/skill-flow-service/`, absorver o catálogo de servidores
do config-api (§4.4.1), implementar `tools/list`, **remover o fallback silencioso**, e decidir o
destino do `skill-flow-worker` (consertar a rota ou aposentar com o `workflow-api`).

*Ressalva de método: as conclusões de "404" e "env ausente" são estáticas (ausência de rota e de env
em todos os compose), não observadas em execução; `.env` fora do versionamento não foi inspecionado.*

---

## 10. Escopo que sai

| Componente | Arquivos | Substituto | Ressalva |
|---|---:|---|---|
| `scheduler-api` | 8 | n8n Schedule Trigger + costura A | `business_day_policy` volta como tool MCP consultada pelo n8n |
| `mailing-api` (drain/pacing) | 7 | n8n + costura B | **Reter** `contact_policy`/`contact_log`/`contact_eligibility_check` — fadiga e opt-out são governança de contato com pessoa |
| Importador CSV/xlsx | — | n8n (nativo) | Camada A (`batch_ingest`) sobrevive como seam público |
| `workflow-api` | 9 | Já em deprecação pelo Arc 19 | Decidir junto com o `skill-flow-worker` |
| `skill-flow-worker` | 5 | — | Ver §9 |
| UI `agent-flow` (editor de fluxo) | 7 | n8n | **Parar de investir agora** |
| UI workflows / schedules / outbound | 16 | Telas do n8n | Manter só governança (policies, deliveries) |

**Não sai:** engine do skill-flow, skill como entidade de config, `calendar-api` (motor consultado),
`dialog-api` e seu editor, tudo em "fosso real".

**Critério permanente:** se o componente não produz nem consome fronteira de
`journey`/`session`/`segment`, e não é governança de contato com pessoa, é candidato.

---

## 11. Fases

| Fase | Conteúdo | Depende de |
|---|---|---|
| **0a** | Fechar a rota anônima `POST /v1/channels/webhook/pool/{pool_id}` | — |
| **0b** | Costura A + E; contrato de propagação de `root_session_id`; **sub-workflow template** (§4.5) | 0a |
| **0c** | Logar auto-mint de `root_session_id` em disparo externo | — |
| **1** | Superfície de resultado honesta: status de 3 estados + artefato buscável | 0b |
| **2a** | **Promover `skill-flow-service`** a pacote de primeira classe (§9) | — |
| **2b** | Catálogo `mcp_servers` no config-api; roteamento por `mcp_server`; remover fallback mudo | 2a |
| **2c** | `tools/list` + snapshot no slot + pin de `versionId` e reconcile (§4.4.2) | 2b |
| **2d** | `session_id`/`root_session_id` no envelope MCP; `execution_id` em `mcp_audit_log` | 2b |
| **2e** | Seletor `server → tool` na tela de config do skill | 2c |
| **3-gate** | **Instrumentar** latência de turno e contagem de travessias no perfil `workflow` | 2b |
| **3** | Costura B: principal externo (compartilhado com A2A fase A2) | 3-gate |
| **4** | Migrar perfil `workflow`; fachada OpenAI no ai-gateway (§6.4) | 3 |
| **5** | Migrar perfil `agent`; mapeadores de `flow_definition`/`pipeline_state` (§5.3); node compilado | 4 |
| **6** | Aposentadorias da §10, uma a uma, com gate de paridade | 5 |

> **A medição vem antes do ponto sem volta.** O gate da fase 3 não é "devemos prosseguir?" — o alvo
> está decidido. É "batemos num impedimento?". Por isso tem que ser cedo e barato.

---

## 12. Invariantes novos

1. **n8n toca sistemas; PlugHub toca pessoas.** Nenhum workflow n8n contata cliente por conta
   própria — todo contato atravessa o channel-gateway.
2. **Proibido publicar tool de canal como domain MCP server do n8n.**
3. **O PlugHub não constrói tela para execução de n8n.**
4. **Execução de n8n nunca é gravada em `sessions`, `segments` ou como journey.** Correlação por
   `session_id`; `execution_id` vive em `mcp_audit_log`.
5. **n8n é recurso chamado, nunca alocável** — jamais registrado como pool, skill ou agent type.
6. **Registro de domain server n8n exige retenção de execução desligada** quando a tool recebe PII.
7. **`updatedAt` nunca é identidade de versão** — usar `versionId` ou hash do JSON canonicalizado.
8. **DialogForm não recebe control-flow.** Skip-logic declarativa é o limite (§5.5).

---

## 13. Riscos e o que não fazer

| Risco | Sinal de que aconteceu |
|---|---|
| Journey partida por falta de propagação | Processos com 1 contato onde deveria haver N; `root_session_id == session_id` em disparo externo |
| Reconstrução do n8n por dentro | Aparece tela de "automações" no PlugHub |
| DialogForm virando linguagem de fluxo | Pedidos de `next` condicional, laço ou variável no form |
| Vazamento de PII pelo execution log do n8n | Auditoria encontra PII em execução retida |
| n8n virando pool | `available` não fecha; `untagged` persistente; `deploy_version` sem snapshot correspondente |
| Edição silenciosa atrás do ponteiro de tool | `versionId` diverge do pin sem promote |
| Avaliação tier-2 apagando sem alarme | Dimensões de faithfulness/tool-correctness ficam planas após a migração |
| Contrato Kafka congelado cedo demais | Mudança de schema interno quebra automação de cliente |

**Não fazer:** registrar n8n como agente/pool/skill; estender `origin` para marcar autoria de
disparo; criar tabela de execuções; criar segundo mecanismo de principal externo paralelo ao do A2A;
abrir HTTP de saída genérico no `invoke`; manter o editor de fluxo como "fallback" sem investimento.

---

## 14. Defeitos colaterais encontrados

Achados durante o levantamento, **independentes do n8n** e que valem correção por si:

1. **`llm_tokens_*` não é emitido no caminho principal.** `emit_llm_tokens` tem um único call site —
   `InferenceEngine.infer()` (`inference.py:149-161`), ou seja `POST /inference`. O **`POST
   /v1/reason`**, que é o step `reason` dos skill flows, **não emite**; `/v1/turn` também não. Sem
   comentário justificando. Verificar se algum relatório ou cota de tokens está sendo lido como se
   tivesse dado.
2. **Fallback silencioso do resolvedor MCP** (`skill-flow-service/src/index.ts:142-144`): servidor
   desconhecido é roteado ao mcp-server-plughub em vez de falhar. Produz o erro errado ("tool
   desconhecida") e desvia o diagnóstico. Contra a postura *degradação nunca é silenciosa*.
3. **`MCP_PROXY_URL` aponta para um serviço inexistente.** `evaluation_context_resolve`
   (`tools/evaluation.ts:803`) faz `fetch` em `localhost:7422`; não há serviço proxy em nenhum
   compose; o `catch` emite `console.warn` e segue.
4. **DECR de `hook_pending` não inspeciona outcome** (`orchestrator-bridge/main.py:4952`). O
   tratamento de `suspended` é guardado por `not conference_id` (`:4663`, `:4784`). Não morde hoje
   porque nada suspende em conferência, mas é borda desguardada do tipo "valor plausível".
5. **Não foi localizado o guard do engine** que rejeitaria `delegate` fora de sessão webhook. O
   comportamento se apoia em comentário e wiring, não em guard lido.
6. **`inline` tem dois significados** em `PoolHookEntry`, dependendo do `side`: conferência quando
   `customer`, máquina destacada quando `agent`. Armadilha de nomenclatura.
7. **Assimetria de permissão entre bordas** (documentada em `lib/invoke-audit.ts:40-46`): o sidecar
   aceita curinga `server:*` e trata lista vazia como sem filtro; o `invoke` nega. Dívida já
   registrada no ADR de borda única.

---

## Referências

- `docs/product/agentes-externos-reclassificacao.md` — runtime importado (precedente que **não**
  decide este caso; ver §2.2)
- `docs/adr/adr-mcp-interception-single-border.md` — fase B2 hospeda a costura C
- `docs/adr/adr-a2a-server-binding.md` — fase A2 hospeda o principal externo da costura B
- `docs/adr/adr-journey-session-segment-model.md` — as três fronteiras
- `docs/adr/adr-historico-unificado-duas-visoes.md` — duas classes de linha
- `docs/adr/adr-dialog-conditional-skip-logic.md` — a guarda do §5.5
- `docs/arcos/arc19-unified-session-model.md` — perfis `workflow` × `agent`
- `docs/product/dialog-primitive-and-runner-design.md` — os veículos inline × delegate
