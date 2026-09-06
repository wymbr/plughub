# ADR — Orquestrador dono do contato, especialista executor, e o contrato de contexto entre eles

**Status:** proposto · **Data:** 2026-09-06 · **Depende de:** `adr-orchestrator-tree-navigation`
(F0–F5 implementadas), `adr-a2a-server-binding` (proposto), `adr-dialog-conditional-skip-logic`,
`adr-journey-session-segment-model`

> **Tese em uma frase:** o orquestrador **humano** já mantém a titularidade do contato enquanto
> especialistas entram e saem; o orquestrador **IA** a perde no primeiro `escalate` — e essa
> assimetria, não a frequência de re-roteamento, é o que justifica o modelo. Torná-los simétricos
> exige três coisas que hoje não existem juntas: **um verbo que devolve** (`delegate`), **uma folha
> que não navega**, e **um contrato de contexto com as duas metades**.

---

## Contexto — o que foi MEDIDO antes de decidir (2026-09-06)

### 1. A assimetria está na trilha de segmentos, não na teoria

Contato completo real (`34e20c62`), validado pelo dono ponta a ponta:

```
demo_ia          native  primary       ← orquestrador IA: escalou e PERDEU o contato
sac_ia           native  primary       ← virou dono
retencao_humano  system  queue
fila_humano      native  specialist
retencao_humano  human   primary       ← orquestrador humano: assumiu
auth_form_ia     native  specialist    ← entrou SOB ele, e saiu
nps_ia           native  specialist    ← idem
```

Quando quem orquestra é **humano**, o especialista entra como `specialist`, executa e sai — o humano
nunca deixa de ser `primary`. Quando quem orquestra é **IA**, o `escalate` **transfere a
titularidade**: o próximo vira `primary`.

**O dano observado, na mesma bateria de testes:** no contato `330cfdd1` o cliente mudou de assunto
(*"agora sobre o custo do meu plano, está muito alto"*) e foi para o humano — enquanto a plataforma
tinha em mãos um mapa (`navigation_pools`) que o levaria a um especialista de financeiro. Quem
poderia ter decidido já não existia.

### 2. A frequência NÃO sustenta o argumento, e é preciso dizer por quê

Em 1 151 contatos (dados de demo, com tráfego de teste):

| pools de atendimento tocados | contatos |
|---|---|
| 1 | 887 (77 %) |
| 2 | 259 (22,5 %) |
| 3 | 5 (0,4 %) |

**2+ pools de IA: 5 em 1 151.** **IA → humano: 262 (23 %).**

⚠️ **Esses 5 não são evidência contra o modelo, e usá-los como tal seria o erro.** O modelo atual
**não consegue produzir** salto IA→IA: o `escalate` é terminal. É medir a demanda por uma capacidade
com dados gerados pela ausência dela. O número serve para uma coisa só: **proibir que a mudança seja
justificada por frequência observada**. A justificativa é a da §1 — governança e medição.

E o número **diz** algo útil: o handoff dominante é IA→humano, e esse um orquestrador que retoma
**não evitaria** — *"isto precisa de humano"* é legitimamente decisão do especialista.

### 3. Os especialistas de hoje não são executores — são atendentes exclusivos

Os cinco destinos declarados em `demo_ia.navigation_pools`:

| pool | skill | decide sozinho |
|---|---|---|
| `sac_ia` | `skill_atendimento_sac_v1` | escala(1) encerra(3) pergunta(3) |
| `auth_sac_ia` | `skill_atendimento_auth_v1` | escala(1) encerra(4) pergunta(3) |
| `auth_form_ia` | `skill_auth_form_v1` | escala(2) encerra(1) pergunta(2) |
| `portabilidade_ia` | `skill_portabilidade_intake_v1` | encerra(2) pergunta(5) |
| `reembolso_ia` | `skill_reembolso_intake_v1` | encerra(2) pergunta(2) |

**Nenhum é executor puro.** Isto é herança de terem sido construídos como agentes únicos e
exclusivos de um atendimento — é a razão pela qual o histórico da §2 não tem salto IA→IA, e é custo
de migração, não defeito de modelo.

### 4. Há menus de NAVEGAÇÃO dentro de folhas — e o conserto da F4 tratou só um deles

Classificando os menus dos cinco pelo critério que a D3 do ADR da árvore já enunciava
(*"navegação sobe, coleta fica"*):

**Coleta — legítima na folha (8):** `coletar_dados`, `coletar_numero`, `coletar_operadora`,
`coletar_contato`, `coletar_numero_pedido`, `coletar_motivo`, `coletar_codigo`, `coletar_descricao`.
Todas preenchem dado de um serviço **já escolhido**.

**Navegação de demanda — não deveria estar aí (3):**

* `sac.menu_motivo` — *"Qual é o motivo do seu contato?"* — o duplicado que o dono viu na tela;
* **`auth.menu_continuar` — *"Como posso te ajudar?"*** — **o mesmo defeito, ainda não observado**;
* `sac.menu_resolucao` — *"Posso te ajudar com mais alguma coisa?"*.

⚠️ **Isto reclassifica a F4.** O atalho por `session.navegacao.path` foi tratado como conserto do
menu duplicado; ele é **tratamento de sintoma de um problema estrutural** — uma folha que se comporta
como pasta. Há pelo menos mais uma instância viva (`auth_sac_ia`), e ela produzirá exatamente a mesma
tela que o dono confundiu com bug.

E note **qual** step mandou aquele contato ao humano: o `menu_resolucao`. *"Posso te ajudar com mais
alguma coisa?"* é pergunta de demanda, e a folha teve de decidir porque não tinha para quem devolver.

### 5. O contrato de contexto: a metade de ENTRADA existe e é ociosa; a de SAÍDA não existe

| peça | estado | adoção |
|---|---|---|
| **onde guardar** — `session.*` 4 h / `journey.*` 30 d / `core.customer.*` 90 d, rotas declaradas | existe e é **imposto** | — |
| **como guardar** — context-map `escopo.dominio.campo` + `tipo`; tag não cadastrada é **recusada** (422) | existe e é **imposto** | — |
| **o que falta ao agente** — `required_context` no flow; o engine computa `@ctx.__gaps__` (`missing` + `low_confidence`) **antes do primeiro step** (`engine.ts:507`) | existe ponta a ponta | **1 de 41** declara |
| leitura desses gaps por algum skill | — | **0 de 41** |
| step `resolve` (acumulação inline de contexto) | existe | **0 de 41** |
| `ask_when` (pular pergunta já respondida) | existe | 2 de 14 formas |
| **o que o agente PRODUZ** | **não existe** | — |

O engine calcula um relatório de lacunas antes do primeiro step e **ninguém o lê**. É a mesma família
de `options_tree`, `process_context` e `WsMenuRender`: declaração sem consumidor.

### 6. Os dois verbos disponíveis, e por que só um serve

| | `task` | `delegate` |
|---|---|---|
| endereça | **skill_id** | **pool** (aceita ref) |
| retorno | `sync`: polling inline 2 s × 150 (**teto 5 min**) → `on_success`; `async`: `__awaiting_task__`, retorna na próxima invocação | `on_resume` / `on_timeout` com `timeout_hours` |
| carrega o flow de | **`SKILLS_DIR/<skill_id>.yaml`, do disco** — ignora o slot `current` | o pool → slot promovido |
| correlação | `job_id` por step (não colide em cadeia) | `core.workflow.delegate_resume_token` — **tag única da sessão** |

**`task` retorna ao chamador** — a afirmação contrária, feita antes nesta discussão, estava errada e
descrevia só o caminho `async`. Mas ele é inutilizável aqui por três razões medidas:

1. endereçar skill **desfaz o invariante** *"o POOL é a unidade endereçável"* e o mapa caminho→pool;
2. carregar o YAML do disco **contorna o modelo de deploy inteiro** (promote, rollback,
   `yaml_snapshot`) — *"qual config está rodando?"* volta a ser indecidível;
3. **3 dos 5 especialistas nem carregam**: o loader casa por nome de arquivo e `skill_auth_form_v1`
   mora em `agente_auth_form_v1.yaml`. No repositório, **14 de 41** skills têm `id` ≠ nome de arquivo.

Somando: no teto de 5 min do `sync`, o step vai a `on_failure` **sem cancelar o job** — o especialista
continua atendendo enquanto o orquestrador já seguiu. Dois agentes falando com a mesma pessoa.

### 7. A regra que proíbe `delegate` em agente não tem mecanismo

O schema diz, em comentário: *"Only valid in workflow profile (channel_type: webhook). Agents must
never use delegate."* O `CLAUDE.md` (Arc 19) afirma que o perfil é *"validado em parse do YAML +
guard no engine"*.

**Procurei allowlist de step por perfil no validador do agent-registry, no `executor.ts` e no
`engine.ts`: não existe.** É promessa sem mecanismo — a família do DDL de `participation_intervals`.
Nada impediria tecnicamente a mudança hoje, e é exatamente por isso que ela precisa ser decidida em
vez de feita.

---

## Decisões

### D1 — O orquestrador é DONO do contato; o especialista entra e sai

O orquestrador IA passa a manter `role: primary` enquanto o especialista executa como `specialist`,
como o orquestrador humano já faz hoje (§1). É **simetria**, não capacidade nova: o modelo de sessão
já suporta os dois papéis, e o Console já opera assim.

**Por que esta é a decisão-raiz:** ela é o que dá endereço à pergunta *"quem responde pelo
desfecho?"*. Hoje, sete segmentos e nenhum dono — cada salto decide o próximo, e não há a quem
atribuir o resultado. O ganho é de **governança e medição**, não de eficiência de roteamento; vendê-lo
como *"vai rotear melhor"* não sobrevive à §2.

### D2 — A folha COLETA; nunca navega demanda

Invariante de **runtime**, não só de autoria. A D3 do ADR da árvore já dizia *"navegação sobe, coleta
fica"*, mas foi aplicada só na hora de escrever a forma — e por isso três menus de demanda
sobreviveram dentro de folhas (§4).

**Teste decidível:** *a pergunta muda o DESTINO do contato?* → navegação, sobe para o orquestrador.
*Preenche dado de um serviço já escolhido?* → coleta, fica na folha.

**Corolário que dissolve o "orquestrador dentro de orquestrador":** navegação de demanda aninhada
nunca é composição de agentes — é **árvore mais funda**. A árvore já recursa a qualquer
profundidade (`optionsAtPath`; o teto de 2 é do achatamento em canal, que é renderização, não
modelo). Se um "especialista" precisa perguntar *o que você quer*, o que ele tem é subárvore no lugar
errado. O que genuinamente precisa de composição é o **retorno** — o `menu_resolucao` subindo.

### D3 — Fluxo ATIVO é o mesmo modelo com o primeiro caminho pré-resolvido

Reativo: pergunta → executa. Ativo (o caso do `portabilidade.menu_continuidade`): a demanda **já é
conhecida** (o processo pendente), então o orquestrador entra com o `path` preenchido, executa a
folha, e depois pergunta *"algo mais?"*.

Não é um terceiro modelo. Nos dois, navegação é do orquestrador e coleta é da folha; muda apenas se o
primeiro caminho foi **perguntado** ou **dado**.

### D4 — O evento de demanda carrega a ORIGEM do caminho

Um contato ativo emite a mesma `category` — mas o caminho veio do **processo**, não do cliente. Sem
distinguir, a série passa a misturar *"o que o cliente pediu"* com *"o que nós fomos oferecer"*, e
perde exatamente o sentido que a F1 lhe deu.

Decisão: tag `origem: cliente | processo` no `agent_event_record`. É uma tag, não um segmento novo de
`category` — a árvore continua sendo a mesma, e o recorte por origem é filtro.

### D5 — O verbo é `delegate` (endereça POOL), nunca `task`

Pelas três razões medidas em §6. `delegate` já aceita ref em `pool`, então consome o mesmo
`navigation_pools` que a F3 construiu, sem uma linha nova.

⚠️ **Pré-requisito honesto:** a regra que hoje proíbe `delegate` em perfil de agente **não tem
mecanismo** (§7). Antes de usá-la a nosso favor ou contra nós, ela precisa virar mecanismo — decidir
com um comentário como se fosse um guard é como esta base acumulou o catálogo que ela mesma cataloga.

### D6 — O contrato de contexto tem DUAS metades, e a de saída é nova

* **Entrada** — `required_context` **já existe** e o engine já computa `@ctx.__gaps__` antes do
  primeiro step. Falta **disciplina**: 1 de 41 declara, 0 de 41 lê.
* **Saída** — declaração do que o skill **deixa no store** ao terminar. **Não existe.** Sem ela o
  orquestrador não tem como saber, *antes* de despachar, se o próximo pode sequer começar, e a cadeia
  orquestrador→especialista→orquestrador não se compõe.

⚠️ **Declarar `provides` é promessa; o mecanismo é comparar.** Ao fim do skill, confrontar o
`provides` declarado com o que está de fato no store e **contar a diferença**. Sem isso, `provides`
entra no catálogo de declarações sem consumidor no dia em que nasce.

### D7 — Cada tag do contrato declara a sua ORIGEM: `cliente | consulta | derivado`

*"Falta CPF → pergunte ao cliente"* e *"falta identificação → execute a transação"* têm custo e modo
de falha diferentes. Inferir é errar dos dois lados: perguntar ao cliente o que se poderia consultar
(o pior atendimento possível), ou chamar sistema externo pelo que ele confirmaria em dois segundos.

**`confidence` já resolve o meio-termo e não é usado:** `ContextEntry.confidence` +
`required_context.confidence_min`. *"Tenho o CPF com 0,5"* não é *"não tenho"* — o agente
**reconfirma** em vez de recoletar.

⚠️ **E o escopo é onde isto morde mais forte.** *"Cliente identificado"* é fato da **sessão** (4 h) ou
do **cliente** (90 d)? Se for do cliente, o próximo contato **pula autenticação** — decisão de
segurança, não de conveniência. A regra *"nunca guardar fato de escopo estreito em campo largo"* vale
aqui com o custo mais alto do repositório. **Nenhuma tag de autenticação sobe de escopo sem decisão
explícita neste ADR ou em outro.**

### D8 — Contrato de contexto ≠ `inputModes`/`outputModes` do A2A

O `adr-a2a-server-binding` (D2) decide que o **AgentCard é projeção do agent-registry**, nunca
documento editável. Logo `requires`/`provides` declarados no registry enriquecem o card **de graça**,
mantendo one-source — e essa convergência é um argumento a favor de fazer o contrato uma vez, bem.

⚠️ Mas os campos do A2A são **modalidade de interface** (media types). Contrato de contexto é
**pré-condição semântica**. Colapsá-los no mesmo campo faria um parceiro externo ler *"preciso de
CPF"* como *"aceito text/plain"*. Campo próprio, ou extensão declarada — nunca reuso do campo do
protocolo.

---

## Alternativas consideradas e RECUSADAS

| alternativa | por que não |
|---|---|
| Trocar `escalate` por `task` | Endereça skill, contorna o slot de deploy, e 3 dos 5 especialistas nem carregam (§6) |
| Manter `escalate` e aceitar a perda de titularidade | É o estado atual; produz sete segmentos sem dono e perdeu um re-roteamento observado (§1) |
| Apagar os menus de navegação das folhas | Quebra a entrada direta: `sac_ia` tem endpoint de canal próprio. O menu **fica**; deixa de ser o único caminho (foi o que a F4 fez, e a D2 generaliza) |
| Orquestrador aninhado como folha | Navegação aninhada é árvore mais funda, não composição (D2) |
| Justificar o modelo pela frequência de re-roteamento | Os 0,4 % foram produzidos por uma arquitetura que não permite o salto — medir demanda pela ausência do mecanismo (§2) |
| `provides` declarado sem conferência | Vira o próximo `options_tree` (D6) |

---

## Fases

| fase | entrega | depende de |
|---|---|---|
| **G0** ✅ | **Medir** — este documento: a assimetria na trilha, os 3 menus de demanda em folhas, o contrato em 1/41 e 0/41, os dois verbos, a regra sem mecanismo | — |
| **G1** | **Dar mecanismo à regra de perfil** (§7): allowlist de step por perfil, no validador, com gate. Vale sozinha, independe do resto | G0 |
| **G2** | **`provides` simétrico ao `requires`** + conferência ao fim do skill + gate da disciplina `__gaps__` (hoje 1/41 e 0/41) | G0 |
| **G3** | **`delegate` no orquestrador**: mantém `primary`, especialista entra como `specialist`; ramo F do gate passa a aceitar `delegate.pool` | G1 |
| **G4** | **Especialistas viram executores**: os 3 menus de demanda sobem; `menu_resolucao` vira o retorno | G3, D2 |
| **G5** | **Origem no evento** (D4) e **fronteira do AgentCard** (D8) | G2 |

> **Por que G1 vem antes de G3, e sozinha:** decidir usar `delegate` em perfil de agente contra uma
> regra que existe só como comentário é escolher com base no que ninguém impõe. Dar mecanismo à regra
> **primeiro** transforma a decisão em mudança declarada, e a G1 vale mesmo que o resto seja adiado.

---

## Riscos e questões abertas

- **Cadeia `delegate → delegate` colide.** `core.workflow.delegate_resume_token` é tag única da
  sessão, e `agente_portabilidade_intake_v1` **já delega** ao `dialog_runner`. Orquestrador →
  portabilidade → dialog_runner sobrescreve o token do orquestrador, que nunca retoma. **É o risco
  número um e não tem solução declarada aqui.**
- **Hooks de fim de contato.** `sac_ia` tem `on_contact_end` rodando NPS. Se o orquestrador retomar,
  o NPS do especialista dispara no meio do contato. Quem fecha o contato — e quando — é pergunta
  aberta.
- **Duração.** `agent_time_ms` soma segmentos de `primary`+`specialist`, e sob a D1 os segmentos
  passam a se **sobrepor por desenho**. A regra *"nunca somar segmentos para obter tempo de sessão"*
  deixa de ser advertência sobre casos raros e vira o caso comum.
- **Eixo de demanda por contato.** Deixa de ser uma marca e vira N. `branch_marks` × `branch_contacts`
  sai de curiosidade e vira leitura obrigatória — a árvore já sabe fazer, os consumidores não.
- **`timeout_hours` do `delegate` contra conversa longa com o cliente.** Precisa de política, e o
  modo de falha (orquestrador volta enquanto o especialista ainda atende) é o mesmo do teto de 5 min
  do `task`.
- **`portabilidade.menu_continuidade` é caso de fronteira.** Não é demanda nem coleta: é pergunta de
  **estado de processo**. A D3 o acomoda como fluxo ativo, mas se aparecerem outros do mesmo tipo,
  pode ser um terceiro gênero que este ADR não nomeou.
- **A ORQ-07 virou alicerce sem ter sido feita para isso.** O conserto da sentinela de idempotência
  (2026-09-06) é o que permite ao orquestrador delegar **várias vezes no mesmo contato**; sem ele, a
  segunda delegação devolveria o resultado da primeira, em silêncio. Registrado aqui porque, se
  alguém reverter aquele conserto, este modelo quebra sem erro visível.
