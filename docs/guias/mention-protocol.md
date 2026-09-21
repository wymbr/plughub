# Protocolo @mention — Endereçamento de Participantes em Conferência

> Última atualização: 2026-05-25 · Estado: Arc 16

> Spec de referência: v1.0 · Módulos: `mcp-server-plughub`, `agent-registry`, `skill-flow-engine`, `agent-assist-ui`

---

## O que é

O protocolo `@mention` permite que um agente humano envie comandos diretamente para qualquer agente especialista participante (ou disponível para convite) em uma sessão de conferência, usando uma sintaxe natural baseada em alias.

É um protocolo de **coordenação entre participantes** — não uma feature específica do Co-pilot. Qualquer agente especialista configurado no pool pode ser endereçado pelo agente humano via `@alias`.

---

## Sintaxe

```
@<alias> [texto livre] [chave=valor ...] [@ctx.<campo>|"fallback"] ...
```

Exemplos:

```
@copilot ativa
@copilot pausa
@billing conta=@ctx.caller.account_id motivo=@ctx.caller.motivo_contato
@captura campos=@ctx.caller.campos_ausentes|"cpf,telefone"
@suporte cliente tem plano @ctx.caller.plano_atual, sentimento @ctx.session.sentimento.categoria
@billing @suporte analise o contexto    ← múltiplos destinatários
```

A mensagem é enviada com `visibility: "agents_only"`. Todos os participantes a recebem (transparência da coordenação). O(s) agente(s) endereçado(s) recebem adicionalmente um evento de roteamento específico.

---

## Permissões — quem pode emitir

**Quem CONDUZ a sessão menciona; quem foi CONVIDADO não convida.** Apenas participantes com
`role: primary` emitem `@mention` com efeito de roteamento — `specialist`, `supervisor`,
`evaluator` e `reviewer` não.

**O eixo é POSIÇÃO, nunca espécie.** Humano e IA são tratados igual, como no resto do modelo de
sessão: a IA que conduz a conversa É a `primary` e menciona; o humano convidado como especialista
não menciona. O `agent_type` (`human` | `native` | `ai`) vive na MESMA entrada do roster e **não
entra nesta decisão**.

> **Como esta seção era, e por que mudou** *(MEN-01, decidida pelo dono em 2026-09-12)*. Ela dizia
> *"apenas `role: primary` ou `role: human`"* e, logo abaixo, *"agentes IA não podem usar
> @mention"* — duas frases que não se seguem uma da outra. Medido em 2026-09-01 em `tenant_demo`:
> **1144 segmentos `native/primary` + 100 `ai/primary`** contra 333 `human/primary`, ou seja, o
> gate deixava passar exatamente a população que a segunda frase proibia. E `role: human` **nunca
> existiu** no domínio de papel — o roster escreve `primary`/`specialist` — logo era ramo morto.
> A análise de cenário que a ficha exigia mostrou que o que o gate contém, e sempre conteve, é
> *"quem foi convidado não convida"*. A regra passou a dizer isso, e a valer nos dois caminhos.

Para convidar especialistas ou coordenar outros agentes sem conduzir a sessão, o caminho é o `task`
step com `mode: assist` — que tem controle de fluxo próprio e auditável.

**Onde a regra é aplicada.** Numa casa só, `lib/participant-role.ts::mayRouteMentions`, consumida
pelos **dois** caminhos que roteiam menção: a tool MCP `message_send` e o WebSocket do Console
(`server.ts`). Até 2026-09-12 o segundo não checava papel nenhum, por desenho declarado (*"o WS
conhece o agente pela conexão"*) — e conhecer o agente pela conexão prova QUEM ele é, não em que
POSIÇÃO está nesta sessão. Enquanto valeu numa porta só, não era garantia: era regra de uma porta,
com a outra aberta ao lado (MEN-02).

## Comando não é conteúdo *(MEN-05, decidido pelo dono em 2026-09-12)*

O `@alias` é **comando de plataforma**: ele é traduzido em efeitos — convite de pool,
`trigger_step`, `set_context` — que nunca aparecem como mensagem. Carregá-lo no texto de um
`message` misturava as duas coisas, e isso tinha duas consequências medidas: o cliente lia
`"@auth_form"` quando quem emitia não era `primary`, e a AUTORIA do convite existia **só** ali.

O que acontece hoje com `@billing conta=@ctx.caller.account_id, pode conferir?`:

| parte | destino |
|---|---|
| `@billing` + args | **evento** `mention_command` no stream (`agents_only`), com o emissor como autor |
| `pode conferir?` | mensagem `agents_only` — é instrução ao especialista convidado |
| o alias, como texto | **não é persistido em lugar nenhum** |

E com `@auth_form` sozinho, sem prosa: **não há mensagem**. Só o evento e o aviso. A mensagem
vazia seria pior que a ausência dela — ela apareceria na conversa sem dizer nada.

⚠️ **`stripped_text` perdeu o fallback `|| text` na mesma ficha**, e a ordem das duas remoções
(key=value antes de `@ctx.*`) foi invertida. Os dois eram latentes porque o campo não tinha
consumidor de produção: o primeiro devolvia o texto INTEIRO quando não sobrava prosa, e o segundo
deixava um `conta=` órfão que virava "mensagem". Um valor que ninguém lê é um valor que ninguém vê
errado.

## Os DOIS avisos, e por que são dois

| evento | quem publica | o que afirma |
|---|---|---|
| `mention.ack` | mcp-server (`routeMentions`) | a menção foi **roteada**, ou o alias **não existe** |
| `mention_command.ack` | orchestrator-bridge | o comando **executou** dentro do skill mencionado |

O primeiro vale para qualquer alias, inclusive o convite de pool; o segundo só existe para os
`mention_commands` declarados — hoje um skill em 44. São momentos diferentes da mesma ação.

**O `mention.ack` nomeia e endereça o emissor** (`from_participant_id`, `recipient_participant_id`).
⚠️ A Agent Assist **não filtra** por esse campo: filtrar exigiria um id próprio confiável no
cliente, e errá-lo esconderia o aviso de quem precisa dele — direção errada de falha para um
retorno que SUBSTITUI o eco da mensagem.

⚠️ **O ack não é a casa da autoria.** Pub/sub é efêmero e best-effort; quem responde *"quem
convidou este especialista?"* é o `mention_command` no stream. O `participant_joined` do convidado
registra quem ENTROU, nunca quem PEDIU — e foi por isso que remover o texto sem criar o evento
teria apagado o elo sem nada ficar vermelho.

A mensagem com `@alias` é sempre `agents_only`; o que o gate decide é apenas se alguém é CONVIDADO.
Negado o roteamento, o comando não acontece — e o emissor é avisado.

---

## Configuração de pools disponíveis

O pool de origem declara quais agentes podem ser endereçados via `@mention` naquela fila. Domínio fechado — o administrador controla o que pode ser convidado.

```yaml
# infra/registry/tenant_demo.yaml
pools:
  - id: retencao_humano
    agent_type_id: agente_retencao_humano_v1
    mentionable_pools:
      copilot:   copilot_retencao      # @copilot → pool copilot_retencao
      billing:   billing_especialista  # @billing → pool billing_especialista
      suporte:   suporte_tecnico       # @suporte → pool suporte_tecnico

  - id: billing_humano
    agent_type_id: agente_billing_humano_v1
    mentionable_pools:
      copilot:   copilot_billing       # Co-pilot específico desta fila
      # sem outros aliases — domínio fechado
```

`mentionable_pools` é um mapa `alias → pool_id`. Se o alias não está no mapa do pool de origem, o mention não é roteado. A mensagem é entregue normalmente como texto `agents_only` sem efeito de roteamento.

---

## Resolução de alias — fluxo completo

```
mcp-server recebe message_send:
  visibility: "agents_only"
  text: "@billing conta=@ctx.caller.account_id"

1. Verifica a POSIÇÃO do remetente no roster (role: primary ⇒ conduz a sessão)
   → não autorizado, ou papel não resolvido: entrega sem roteamento

2. Detecta prefixo "@" → extrai aliases e texto do comando
   aliases detectados: ["billing"]
   texto do comando:   "conta=@ctx.caller.account_id"

3. Interpola referências @ctx.* no texto do comando
   → lê ContextStore: account_id = "ACC-00291"
   texto resolvido: "conta=ACC-00291"

4. Para cada alias:
   a. Consulta mentionable_pools do pool da sessão
      → "billing" → pool_id = "billing_especialista"

   b. Participante com agent_type de "billing_especialista" está na sessão?
      SIM → publica em agent:events:{session_id} com campo mention_target + comando resolvido
      NÃO → aciona routing engine para convidar agente do pool "billing_especialista"
            (equivale a task step mode:assist — o join do agente é a confirmação)

5. Entrega a mensagem normalmente (visibility: agents_only) para todos os participantes
```

---

## Interpolação de `@ctx.*` no comando

Antes de rotear o comando, o mcp-server-plughub resolve referências `@ctx.*` usando a mesma lógica do `interpolate.ts` do skill-flow-engine (função movida para `@plughub/sdk` para uso compartilhado).

### Sintaxe de fallback

```
@ctx.<namespace>.<campo>|"valor default"
```

Se o campo não existe no ContextStore, o fallback é usado. Se não há fallback e o campo está ausente, o valor resolve para string vazia `""`.

Exemplos:

```
@ctx.caller.account_id|"não identificado"
@ctx.caller.campos_ausentes|"cpf,telefone"
@ctx.session.sentimento.categoria|"neutro"
```

---

## Múltiplos destinatários

Todos os aliases detectados **antes do primeiro token que não é um alias** recebem o mesmo comando resolvido:

```
"@billing @suporte analise o contexto"
  → aliases: ["billing", "suporte"]
  → comando: "analise o contexto"
  → ambos recebem o evento de roteamento
```

Não existe destinatário "principal" — todos os aliases são tratados simetricamente.

---

## Confirmação de recebimento

Não existe ack explícito do mcp-server. A confirmação de que o agente especialista foi convidado e chegou é o evento `participant_joined` publicado no stream, que o Agent Assist UI já renderiza como indicador de presença.

Para comandos enviados a agentes já presentes (ex: `@copilot ativa`), o Co-pilot pode opcionalmente responder com uma mensagem `agents_only` de confirmação — definido no `mention_commands` do skill, não pelo protocolo em si.

---

## `mention_commands` — declaração no skill YAML

Cada agente especialista declara os comandos que reconhece no seu skill YAML:

```yaml
# copilot_retencao_v1.yaml
mention_commands:
  ativa:
    description: "Ativa o Co-pilot para falar diretamente com o cliente"
    action:
      set_context:
        session.copilot.mode: "active"
    acknowledge: true          # responde com confirmação agents_only

  pausa:
    description: "Silencia o Co-pilot para o cliente (continua em background)"
    action:
      set_context:
        session.copilot.mode: "passive"
    acknowledge: true

  resumo:
    description: "Gera resumo da conversa até o momento"
    action:
      trigger_step: gerar_resumo
    acknowledge: false         # o step responde por conta própria

  para:
    description: "Encerra a participação do Co-pilot na sessão"
    action:
      terminate_self: true
    acknowledge: false
```

Comandos não reconhecidos são ignorados silenciosamente. O texto do comando pode ser texto livre — nesse caso o skill pode alimentar um `reason` step para interpretação.

### Ações disponíveis em `mention_commands`

| Ação | Efeito |
|---|---|
| `set_context: { tag: value }` | Escreve no ContextStore (fire-and-forget) |
| `trigger_step: <step_id>` | Salta para o step declarado no skill flow |
| `terminate_self: true` | Agente sai da conferência via `agent_done` |

**Transporte — fila de SINAL, nunca a da resposta** *(MEN-07, 2026-09-16)*. `trigger_step` e
`terminate_self` chegam ao step bloqueado (`menu`, `resolve`) pela lista
`menu:signal:{sid}[:{iid}]`, que **só o bridge escreve** (`dispatch_mention_command`). Até esta
data viajavam em `menu:result` — a fila da resposta do cliente — e o motor as reconhecia por
`JSON.parse` do texto: um cliente que digitasse `{"_mention_trigger_step":"<passo>"}` saltava o
fluxo, e isso foi reproduzido ao vivo. Hoje nada em `menu:result` é interpretado; a mesma fila de
sinal leva o desfecho de coleta do canal (`timeout`/`invalid`). Gate:
`infra/test/probe_menu_signal_contract.sh`.

### Standby do especialista — `standby: true` no menu (fix 2026-06-04)

O step de espera do especialista (`menu` `agents_only`, `timeout_s: -1`) deve
declarar **`standby: true`**. Sem a flag, os roteadores de mensagem comum
(handler de texto do humano e `menu_submit` no mcp-server, regra "agents_only
recebe qualquer mensagem de agente") entregavam **todo texto do primary** —
inclusive o próprio `@copilot ...` — ao BLPOP do standby, estourando-o na
entrada (segmento 0s, `specialist_key` apagado, re-convite em loop).

Mecânica do fix (duas pontas):

1. **`standby: true`** (`MenuStepSchema`) → `menu.ts` grava `standby` no hash
   `menu:waiting:{sid}`; os dois roteadores do mcp-server **pulam** entradas
   standby ao rotear mensagens comuns. O standby acorda exclusivamente por
   interrupts do dispatch e por `session:closed` (disconnect).
2. **Dispatch instance-scoped**: o specialist roda com `instance_id` → seu
   BLPOP é em `menu:result:{sid}:{iid}` (e o interrupt, desde a MEN-07, em `menu:signal:{sid}:{iid}`). O `dispatch_mention_command` (bridge)
   mira a chave instance-scoped (lendo `instance_id` do `specialist_key`); antes
   empurrava para a session-scoped e o interrupt nunca chegava.
   ⚠️ **Casa ÚNICA desde 2026-09-02 (ALW-07).** Havia uma segunda, a tool MCP
   `mention_command_dispatch` (`bpm.ts`), cuja descrição dizia ser chamada pelo
   bridge — e o bridge nunca a chamou. Ela divergia da que roda (roteava
   `journey.*` para o hash do processo; a do bridge grava no da sessão e avisa)
   e foi REMOVIDA: com uma das duas morta, não há merge, há escolha.
3. **Validador de ciclos do engine**: o ciclo do copilot
   (`aguardar → analisar → sugerir → aguardar`) era rejeitado pelo
   `validateFlow` ("unguarded cycles") e o flow morria na entrada — segmento
   0s, sem anúncio, re-convite por mention (este era o killer imediato do
   sintoma). Política consolidada (2026-06-04): guarda de ciclo = step
   **bloqueante** — `receive` com `max_iterations`, qualquer `menu` (inclui o
   standby), `suspend` ou `collect`. A adjacência foi fechada na mesma data
   (`conditions[].next`/`default` do choice, `strategies[]` do catch, campos
   `{next}` de suspend/collect) — auditoria confirmou que os 6 ciclos dos
   YAMLs existentes passam por guarda bloqueante.

---

## Alias não resolvido — comportamento

Se o alias não está em `mentionable_pools` do pool de origem:

- A mensagem é entregue normalmente como `agents_only`
- Nenhum roteamento especial ocorre
- O Agent Assist UI exibe o alias em cinza (não sublinhado como link) indicando que não foi resolvido
- Nenhum erro é gerado — o agente humano pode ter digitado um alias incorreto

---

## Auto-invite — quando o especialista não está na sessão

Quando o alias está em `mentionable_pools` mas o agente não está na conferência:

1. mcp-server-plughub publica um `session_invite` request para o routing engine com `pool_id` do alias
2. O routing engine aloca uma instância do pool e convida para a sessão
3. O agente entra com `role: specialist`
4. O evento `participant_joined` confirma a chegada
5. O mcp-server entrega o comando ao agente recém-chegado

O auto-invite é equivalente a um `task` step `mode: assist` executado manualmente. O comportamento de fila (SLA, disponibilidade) é o mesmo.

---

## Superfície de implementação

| Componente | Mudança |
|---|---|
| `@plughub/schemas / agent-registry.ts` | campo `mentionable_pools: Record<string, string>` em `Pool` |
| `@plughub/schemas / skill.ts` | seção `mention_commands` em `SkillDefinition` |
| `@plughub/sdk / interpolate.ts` | mover função de interpolação de `skill-flow-engine` para `sdk` (uso compartilhado) |
| `mcp-server-plughub / lib/mention-routing.ts` | **implementação única do roteamento** (F5, 2026-07-28): resolve aliases contra `mentionable_pools`, interpola `@ctx.*` e publica os dois eventos por alias |
| `mcp-server-plughub / message_send` | gate de permissão + resolução do pool do remetente pelo registro por-(sessão, instância); delega o roteamento ao módulo acima |
| `mcp-server-plughub / server.ts` (WS de agente) | superfície do Console: detecta o mention, força `agents_only`, ecoa, e delega o roteamento ao mesmo módulo passando o pool DA CONEXÃO |
| `agent-registry` | persistência de `mentionable_pools` no pool; API de leitura para mcp-server |
| `skill-flow-engine` | processamento de `mention_commands` recebidos; ações `set_context`, `trigger_step`, `terminate_self` |
| `agent-assist-ui` | autocomplete de `@alias` no input interno (lê `mentionable_pools` do pool ativo); indicador visual de alias resolvido vs não resolvido |

---

## Duas superfícies, um roteador

Uma menção pode nascer em duas superfícies, e a diferença entre elas é **como cada uma sabe o pool do
remetente** — que é o que fecha o domínio de aliases:

| Superfície | Quem emite | Pool do remetente vem de |
|---|---|---|
| WebSocket de agente (`server.ts`) — **Console** | humano no Console | query-param da conexão. Há **uma conexão WS por pool**, então o escopo é correto por construção |
| Tool MCP `message_send` (`session.ts`) | agente via SDK/MCP | `session:{sid}:routing:{iid}.pool_id` — o registro por-(sessão, instância) escrito pelo bridge |

**Nunca** do `pool_id` do registro global da instância: um humano logado em N pools tem UM registro, e
aquele campo só poderia guardar o pool do último login. Ver
[`adr-human-agent-pool-scoped-identity`](../adr/adr-human-agent-pool-scoped-identity.md) § B6 — o campo
foi **removido** na F5 justamente para que ninguém volte a fazer essa pergunta ao lugar errado.

O roteamento em si é o mesmo código nas duas (`lib/mention-routing.ts`); o pool é parâmetro.

---

## Invariantes

- `@mention` só é roteado em mensagens com `visibility: "agents_only"`
- Apenas `role: primary` — quem CONDUZ a sessão — emite mentions com efeito de roteamento, e o
  gate **falha fechado**: sem prova positiva do role, não roteia (e loga por quê). Um gate de
  autorização que falha aberto não é gate. A decisão é a MESMA nos dois caminhos que roteiam
  (`message_send` e WS do Console), porque mora numa função só.
- O domínio de aliases possíveis é sempre fechado pela configuração `mentionable_pools` do pool
- A mensagem original é sempre entregue a todos os participantes `agents_only`, independente do roteamento
- Aliases não resolvidos nunca geram erro — são texto inerte
- O eixo é POSIÇÃO e nunca ESPÉCIE: a IA que conduz menciona, o humano convidado não. Quem não
  conduz coordena pelo `task` step com `mode: assist`
- Menus `standby: true` nunca recebem mensagens comuns — acordam só por
  interrupt do dispatch (chave instance-scoped) ou `session:closed`
- Interrupt é SINAL, nunca conteúdo: mora em `menu:signal`, escrito só pelo bridge; texto do
  cliente em `menu:result` nunca é interpretado como comando (MEN-07)
- **O convidado não decide o destino do contato** (MEN-08, 2026-09-21) — é o mesmo eixo, do lado
  da saída: quem CONDUZ convida e quem CONDUZ escala. `conversation_escalate` recebe o token
  LIGADO À SESSÃO (injetado pelo skill-flow-service, lista `SESSION_IDENTIFIED_TOOLS` de
  `@plughub/schemas`), lê o papel da instância ASSINADA no roster e **recusa**
  (`escalate_not_conductor`) quando a leitura é positiva e não é `primary`; a recusa vira
  `on_failure` no step. Sem token ou papel não resolvido, **segue como antes**, com WARN — ao
  contrário do gate de @mention, que falha fechado: recusar escalação por falha de leitura
  deixaria o contato de quem conduz sem destino. O skill convidado ramifica por
  `$.session.is_conference` e devolve o veredito a quem o chamou, ANTES de qualquer mensagem de
  transferência ao cliente (`agente_auth_form_v1`, `agente_auth_ia_v1`). A saída que a tool escreve
  no stream leva a instância real, não o rótulo `ai-agent`
- **A saída da FILA é reconhecida pelo NOME, nunca pela ausência** (MEN-09, 2026-09-21) — o agente
  de fila não é participante do roster nem segura vaga, mas é quem entrega o contato ao atendente.
  O bridge assina no token a identidade sintética `queue-{session_id}` (`queue_agent_participant_id`),
  a mesma dos segmentos dele no analytics, e o mcp-server a reconhece por
  `isQueueAgentInstance(instance, sessão)` — casando a sessão inteira, então token de outra sessão
  não vira fila desta. Antes chegava com instância vazia e passava como *"nao conferido"* em TODA
  saída de fila
