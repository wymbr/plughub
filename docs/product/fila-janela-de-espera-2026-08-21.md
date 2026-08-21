# Janela de espera, vocabulário de segmento e os três defeitos de fila — 2026-08-21

> Registro de uma sessão de **discussão conceitual + medição**. Sucede a discussão do tema
> *Sessão, Segmento e Journey* e substitui, por medição, o diagnóstico que o `TODO.md` carregava
> desde 2026-08-14.
>
> **O que este documento é:** as quatro decisões de vocabulário acordadas, a medição que as
> sustenta, e a separação de três defeitos que estavam contados como um.
>
> **O que ele não é:** implementação. Nada aqui foi construído.

---

## 1. As quatro decisões de vocabulário

Todas caem sob a mesma regra, e é isso que dá confiança de que o modelo está consistente:
**põe o fato no escopo dele.**

> ✅ **Aceitas e registradas em 2026-08-21** no
> [`adr-journey-session-segment-model.md`](../adr/adr-journey-session-segment-model.md) como
> **D10 (=D-A) · D11 (=D-C) · D12 (=D-B) · D13 (=D-D)** — a numeração do ADR continua a série D1–D9.
> A D12 **refuta** a linha *(espera)* da D9, que atribuía a espera ao `duration_ms` de `role='queue'`.

| # | Decisão | Regra que a sustenta |
|---|---|---|
| **D-A** | **Pool de entrada** é fato da **sessão** (`entry_pool_id`, imutável). **Pool que atende** é fato do **segmento**; `attended_pool_ids` na linha da sessão é **projeção**, nunca verdade | *"never store a narrower-scope fact in a wider-scope field — derive it where the scope is known"* |
| **D-B** | A **janela de espera** e o **trabalho do agente de fila** são fatos distintos. O agente de fila é `specialist` (é agente); a espera é janela própria, produzida na borda de **roteamento** | escopo próprio; a espera existe sem agente, o agente não existe sem config |
| **D-C** | `session` = **qualquer acesso à plataforma por qualquer canal**. **"Contato" deixa de ser um nível do modelo e vira FILTRO** sobre um atributo da sessão | os três níveis fechados são `segment · session · journey`; *"outro agrupamento é filtro, não journey"* — e agora *"contato é filtro, não nível"* |
| **D-D** | Discriminador **único e ternário**, derivado de `spawn_reason`: `NULL`=inbound · `collect`=outbound · `trigger`/`delegate`=interno. `pools.purpose` **deixa de ser critério de contato** e volta a ser só atributo de pool | "esta sessão teve cliente" é fato da SESSÃO, não do pool |

### Por que journey não substitui o discriminador

Journey **agrupa**, não **classifica**. Uma journey com 4 sessões não diz quais 2 o cliente tocou.
Usar journey como resposta a *"quantas vezes o cliente nos procurou"* apenas move o erro de cabeçalho
um nível acima ("1 processo" para quem procurou 2 vezes). E `Journey` como **entidade** está proibido
(removida no Arc 19 Fase F; a `WorkflowInstance` antes dela pelo mesmo motivo).

### O defeito concreto que D-D fecha

O discriminador hoje é uma mistura de `pools.purpose` e `spawn_reason`, e os dois **discordam**:
`aprovacao_credito` não é `purpose=internal`, logo `_apply_contact_scope` não a exclui. Escolher **um**
é pré-requisito de renderizar qualquer contagem de contato na F4.

---

## 2. Medição — `spawn_reason`, e a classe *outbound* sem amostra

| `spawn_reason` | 2026-08-14 | 2026-08-21 |
|---|---|---|
| `NULL` (inbound) | 349 | **422** |
| `trigger` (interno) | 71 | **96** |
| `collect` (outbound) | 0 | **ausente** |
| `delegate` (interno) | 0 | **ausente** |
| total | 420 | **518** |

**Consequência para a F4 (visão 2):** a classe de linha *"acesso outbound"* segue com **zero amostras**,
13 dias depois do F0 (o gate do `collect`, entregue em 14/08). Construir a classe na tela é o ramo que
nada exercita — a armadilha ANI/DNIS que o ADR do histórico unificado já registra.

A direção ternária (D-D) continua certa **no modelo**; o que não se sustenta é **renderizar** a terceira
classe agora. `delegate = 0` segue **não explicado**, embora o carimbo exista em `webhook.py:1604`.

---

## 3. A janela de espera não tem produtor confiável

### 3.1 A medição que reordenou o tema

Contato real, manual, no WebChat — sessão `81d194ad-…-ce81b30e8343`:

```
18:14:45.417 → 18:14:46.926   primary    sac_ia / skill_atendimento_sac_v1   (escalated_human)
                                          ← 21,35 s sem registro algum
18:15:08.276 → 18:15:30.680   primary    retencao_humano / human_agent
18:15:22.103 → 18:15:24.314   specialist nps_ia / skill_nps_v1
```

**Três segmentos. Nenhum de fila.** A espera de 21,35 s não existe em `segments`, não existe em
`session_transitions`, não existe em lugar nenhum. E existem **52** segmentos `role='queue'` no tenant —
logo não é vazamento, é **ausência seletiva**: o segmento de fila nasce em alguns caminhos e não no
caminho que um contato real percorre.

### 3.1b O segmento `role='queue'` nunca foi a espera — ele é o segmento do AGENTE

*(Refinamento medido no mesmo dia, com transcrição na tela. Corrige o enunciado da §3.2, que dizia
"a janela é produzida como efeito colateral"; mais precisamente, **o que é produzido é o segmento do
agente, e ele vinha fazendo as vezes de janela**.)*

Sessão `sess-e2e-2920b0d1-…-c803d28a171a`, segmento `queue · agente fila`, 6 s:

```
09:29:10  PRIMARY   "Olá! No momento todos os nossos especialistas estão ocupados.
                     Você está na fila de atendimento… pode enviar mensagens — vou te acompanhar."
09:29:13  CUSTOMER  "Olá, preciso de ajuda com minha fatura"
09:29:16  PRIMARY   "Recebemos sua mensagem sobre a fatura… um de nossos especialistas
                     estará com você em breve."
```

Três fatos que decorrem daí:

1. **É trabalho, não espera.** Há conversa real dentro da janela; o participante é o sintético
   `queue-{session_id}`. A duração (6 s) é a do **flow**, não a do tempo que o cliente aguardou —
   `_q_joined_at` é carimbado antes de `activate_native_agent` e o fim é o retorno dessa chamada.
2. **Confirma a D-B / D12:** o agente de fila é `specialist`; a espera é outro fato, sem produtor.
3. **As mensagens são rotuladas `PRIMARY`, o segmento é `queue`.** Papel de mensagem (`author_role`) e
   papel de participante são **vocabulários distintos** e já discordam no mesmo caso — relevante à §6,
   porque o masking autoriza por um e a tela exibe o outro.

### 3.2 A razão estrutural

A janela de espera é produzida como **efeito colateral da ativação do agente de fila**
(`main.py:5924-5939`, `participant_joined` publicado imediatamente antes de `activate_native_agent`).
Ela só existe se o agente ativar. Não existe quando:

- o pool de fila não tem slot `current` (`:5845-5855` retorna cedo, e o log **diz** que nada foi criado);
- o `queue_config` não resolve (§4 abaixo);
- o cliente abandona antes de qualquer coisa.

**Para servir de SLA, a janela tem de nascer na borda de ROTEAMENTO**, que é onde o fato existe nos
quatro casos — atendida, muda, abandonada e `max_wait_exceeded`. O routing-engine já loga as duas
bordas (`Queued session=… — no agents available` / `Contact persisted to queue` na entrada;
`Queue cleanup: removed … reason=…` na saída). O bridge, que hoje produz o segmento, é o componente
errado: ele só sabe da espera quando decide entreter.

### 3.3 O veículo continua sendo o segmento — mas com id determinístico

`session_transitions` **não** comporta a espera: medido, é um **livro-razão de suspend/resume com
token** (`resume_token`, `step_id`, `suspend_reason`, `suspended_at`, `resume_expires_at`, `resumed_at`,
`resume_origin`), sem `from_state`/`to_state` e sem enum de estado. **O nome é mais largo que o
conteúdo** — mesma família da colisão `SessionMeta` × `ReplayContext`. Alargá-la faria a maioria das
colunas ficar nula por linha; criar tabela nova violaria *"nunca inventar o 3º mecanismo"*.

Fica como **segmento** (reusa mecanismo, aparece no drill, sem tabela nova), com duas emendas:

1. **`segment_id` determinístico** derivado do `session_id` — hoje é `uuid.uuid4()` por invocação
   (`:5924`). O padrão já existe no repositório: o `quality-ingest` *"deriva `session_id`/`segment_id`
   determinísticos (idempotência)"*. Com id determinístico, invocação repetida produz a **mesma** linha
   e o `ReplacingMergeTree` deduplica — o defeito da §5.1 sumiria sem guard novo.
2. **`queue` admitido no `ParticipantRoleSchema`** — ver §6.

### 3.4 Hipótese aberta: existem DOIS tipos de espera

Se `retencao_humano` opera em **pull**, o contato vira item de inbox e espera **reivindicação**, que não
é *"sem agente disponível"*. São duas esperas distintas, e para SLA as duas contam. Hoje nenhuma das
duas tem registro confiável. **Não medido** — registrado para não ser redescoberto.

---

## 4. O `skill_id` legado bloqueia o tenant default

Estado medido do pool (agent-registry, não YAML):

```
"internal_queue_enabled": true
"queue_config": {"skill_id": "skill_fila_v1"}      ← sem `pool_id`
```

Cadeia, com o código na mão:

| Linha | O que acontece |
|---|---|
| `main.py:5735` | `if not queue_cfg:` → o `queue_config` é **truthy** (tem `skill_id`) ⇒ **ramo do tenant default PULADO** |
| `:5781` | `queue_pool_id = ""` |
| `:5788-5789` | `agent_type_id = explicit_skill_id` = `skill_fila_v1` |
| `:5841` | `_flow_pool_id = "" or pool_id` = **`retencao_humano`** |
| `:5845-5855` | `resolve_flow_for_agent` devolve `None` → ERROR, retorno cedo, nada criado |

Log de produção, casando linha a linha:

```
ERROR — Agente de fila NÃO ativado: destino=retencao_humano fila=retencao_humano
        agent=skill_fila_v1 — nenhum flow executável
```

**A tela afirma o oposto do que o código faz.** O seletor mostra *"— Tenant default —"* e o texto de
ajuda diz *"Empty = tenant default"*. Mas o default só vale quando **não há `queue_config` algum** — e o
`skill_id` legado, que a mesma tela descreve como *"preserved, but it does not resolve the deploy"*,
mantém o objeto não-vazio e **suprime o default**. Ele não resolve o deploy **e** bloqueia o default;
a tela só conta a primeira metade.

Agrava: `PoolsPage.tsx:927` envia `queue_config` sempre que **qualquer** dos dois campos está
preenchido, então limpar o Queue pool na UI **não** esvazia o objeto enquanto o skill legado estiver lá.

**Família do defeito:** *"preservado" lido como inerte*. E `queue_pool_id or pool_id` é *valor
plausível* — transforma config ausente num alvo que parece razoável (o pool de destino) e nunca pode
funcionar como pool de fila, convertendo lacuna de config em erro de runtime num log que ninguém lê,
com o contato esperando mudo.

**Conserto sugerido, em três frentes independentes:**

- **config-time:** a tela do pool sabe se o queue pool escolhido tem slot promovido — deve **recusar ou
  avisar**, não deixar o operador salvar um tratamento de fila que não roda;
- **semântica:** o gatilho do tenant default deve ser *"não há pool de fila resolvível"*, não *"o objeto
  `queue_config` é vazio"*;
- **fallback:** `queue_pool_id or pool_id` deve **recusar alto**, não adivinhar.

---

## 5. Três defeitos de fila que estavam contados como um

O `TODO.md` § *"Segmento que nunca fecha — `participant_left` não publicado na saída por SUPERAÇÃO"*
descrevia **uma** causa cobrindo `primary`/`specialist`/`queue`. A medição separa três, e a maior nem
é vazamento.

Contagem de segmentos abertos **em sessão fechada** (o escopo que julga; aberto em sessão viva é normal):

| Papel | 2026-08-14 | 2026-08-21 |
|---|---|---|
| `primary` | 5 | **0** |
| `specialist` | 2 | **0** |
| `queue` | 2 | **4** — e **4 de 4 posteriores a 2026-08-18** |

### 5.1 Re-entrância — `activate_queue_agent` roda duas vezes

Sessão `sess_20260821T122910_6BWRJZ…`, em 114 ms:

```
.220  Queue agent marker SET          ← nasce o segmento #1
.220  Activating queue agent
.232  Conference specialist completed   (outcome=resolved)
.252  Queue agent marker SET          ← nasce o segmento #2 (MESMA sessão)
.252  Activating queue agent
.256  Skill already running: active_job=unknown
.257  Queue agent marker DELETE: deleted=1
.257  Queue agent completed: outcome=abandoned wait_ms=4
```

O segmento aberto no ClickHouse está em `12:29:10.220` — é o **#1**. Quem fechou foi o **#2**.

**Dois defeitos de ORDEM, ambos independentes:**

1. **O `participant_joined` é publicado ANTES do guard.** O `Skill already running` existe e funciona —
   só chega tarde: o segmento fantasma já nasceu. Guard depois do efeito não previne, aborta.
2. **O `DELETE` não confere posse.** A invocação #2 apaga `queue:agent_active:{session}` que a #1
   escreveu; a #1 fica bloqueada esperando um sinal que depende da chave que a gêmea apagou. Mesma
   família de "posse" do arco de requeue.

**Gatilho provável:** transferência/escalate chegando para sessão **já enfileirada**. No routing
aparecem três eventos para a mesma sessão — um `Queued … pool=None`, depois `Queued … pool=retencao_humano`
**duas vezes**, com um `Unrecognised inbound event` (carregando `handoff_reason`/`mode`/`pipeline_state`)
no meio. Cada `queued` com pool vira uma ativação. **Não é corrida — é re-entrância sem idempotência.**

### 5.2 Ativação única que nunca retorna — a causa DOMINANTE

`sess_…XUDB0V` e `sess_…WSYVFV` têm outra forma:

```
Queue agent marker SET
Activating queue agent
Conference specialist completed   (outro fluxo, 1–12 ms depois)
                                  ← silêncio. fim.
```

Sem `Skill already running`, sem `DELETE`, sem `Queue agent completed`. Uma ativação só, e o
`activate_native_agent` **nunca retorna**. Confirmado pelo contador: `grep -c "Skill already running"`
= **2** em 6 h, para **4** segmentos abertos ⇒ a dupla ativação explica no máximo metade.

### 5.3 Ausência no caminho real — §3, e é a mais grave

Não é um segmento que não fecha. É um segmento que **não nasce**.

### 5.4 O INVERSO — sessão que fica `active` com o segmento fechado

*(Observado na tela em 2026-08-21, não medido em volume.)*

`/analise/sessions` mostra `sess-e2e-2920b0d1-…-c803d28a171a` com **status `active`**, outcome
`abandoned`, duração `—`, **1 segmento** — e o segmento está **`closed`** (`09:29:10 → 09:29:16`,
`abandoned`). É o espelho exato da §5.1/§5.2: lá o segmento fica aberto numa sessão fechada; aqui o
segmento fecha e a **sessão** nunca fecha.

Na mesma listagem há ao menos mais duas linhas com a assinatura (`…f-087dd5d958d6`, `active`/`abandoned`,
1 segmento; `…T62XU4QOY7ZN8N`, `active`, sem segmentos).

**É o que o operador observou por outro caminho:** derrubar o webchat enquanto o contato está na fila
não gera histórico utilizável. O `abandoned` é registrado no segmento, mas a sessão não transiciona —
então ela nunca entra em nenhuma contagem de contato encerrado, e `close_reason` `customer_abandon` /
`max_wait_exceeded` perdem casos em silêncio.

⚠️ **`active` + `abandoned` na mesma linha é estado impossível** e deveria ser barulhento na própria
tela. Item próprio; não medido em volume nesta sessão.

### 5.5 O que caiu, e por quê

`primary` e `specialist` foram de 5 e 2 para **0 e 0**. O fix de 2026-08-18 (`key=session_id` no
`conversations.participants` + `ReplacingMergeTree(row_version)`) fechou a produção **e** reparou o
passado desses dois papéis. O "resíduo 1 — o passado não foi reparado" precisa ser re-medido antes de
virar decisão de reprocessamento.

---

## 6. Masking por papel não tem vocabulário

**Existem DOIS enums de papel de participante, declarados independentemente — e já divergentes:**

| Schema | Valores | Quem usa |
|---|---|---|
| `ParticipantRoleSchema` (`common.ts:81`) | 5 — **sem `queue`** | `SessionParticipant.role` **e `authorized_roles` do masking** |
| literal inline em `ContactSegmentSchema.role` (`contact-segment.ts:62`) e `participant_role` (`:115`) | 6 — **com `queue`** | segmento e evento Kafka |

O segundo **não referencia** o primeiro: repete a lista à mão. A divergência de um valor não é descuido
isolado, é a consequência esperada de duas fontes para o mesmo vocabulário.

Somando o furo já registrado na **D9.1** do ADR:

- **`queue` existe no enum do segmento e NÃO no `ParticipantRoleSchema`** — que é justamente o que o
  masking usa;
- **`supervisor`, `evaluator` e `reviewer` estão nos dois e nenhum caminho os emite.**
  `_part_role = "specialist" if conference_id else "primary"` (`main.py:4503`) é a única decisão de papel
  do sistema, e é binária;
- e há um **terceiro** vocabulário adjacente: `author_role` da mensagem, que na transcrição da §3.1b
  exibe `PRIMARY` para o participante cujo segmento é `queue`.

**Onde isso morde:** `authorized_roles` (`schemas/src/audit.ts:247`) é `z.array(ParticipantRoleSchema)`,
default `["evaluator","reviewer"]`. Logo:

- o papel `queue` **não pode nem ser expresso** numa política de masking — nem para autorizar, nem para
  negar. E o agente de fila é justamente quem conversa com o cliente (é o único step `reason` sobre fala
  de cliente no repositório);
- o supervisor **não vê `original_content`** por default — decisão nunca exercitada, porque o papel
  nunca é emitido.

**E o supervisor hoje lê a sessão SEM ser participante:** `supervisor_state`, `supervisor_capabilities`,
`copilot_state`, `/api/inject-context` são tools/endpoints, não entrada na conferência. Consequências:
o acesso não aparece no roster (logo não é auditável como participação), e **não há evento de entrada
para anunciar** — então "o Console deve avisar que um supervisor entrou" só é implementável depois que
o supervisor virar participante de verdade.

Encadeia com o pré-requisito 2 do R8 (*"produzir o vocabulário"*).

---

## 7. Erros de previsão desta sessão — método

**4 de 6 previsões erradas.** Isso é informação sobre o modelo mental de quem previu, não ruído — e
cada erro mudou o diagnóstico, não só o escopo.

| Previsão | Resultado | O que o erro ensinou |
|---|---|---|
| `spawn_reason`: collect/delegate = 0 | ✅ | — |
| `session_transitions` não registra fila | ✅ pelo motivo **errado** | não falta um valor num enum: **não existe enum**. A tabela é outra coisa |
| 3 valores de `role`, sem `supervisor` | ✅ | confirma o R8 |
| travados = 14 (primary 8–12) | ❌ **4, todos `queue`** | minha query contava `ended_at is null` em **todas** as sessões — aberto em sessão viva é normal. *Igualdade sem discriminador* |
| ≥ 4 × `Skill already running` | ❌ **2** | duas causas produzindo o mesmo sintoma |
| par de 12:29 = dupla ativação nos dois | ❌ só num | amostra de 1 generalizada |
| órfãos: `queue` > 0 | ❌ **só `primary` 15** | o contador anterior filtrava `role='primary'` e por isso "fechava a conta" |

**Duas regras que esta sessão comprou:**

- **Um zero só julga se o filtro que o produziu não puder tê-lo fabricado.** O `primary = 0` travados
  parecia conserto e era escopo: 15 dos 16 abertos estão em `session_id` que **não existem em
  `sessions`** — não são segmentos que não fecham, são segmentos de sessões que nunca existiram.
- **"Preservado" não é sinônimo de inerte.** Um campo legado que a UI declara sem efeito pode ser
  load-bearing pelo lado negativo (§4). Mesma família de *"campo morto pode gatear tela viva"*.

---

## 8. Pendente

| # | Item | Natureza |
|---|---|---|
| 1 | Janela de espera produzida na borda de roteamento, id determinístico | construção (§3) |
| 2 | Idempotência da ativação de fila (`SET NX` + posse no `DELETE`) | defeito (§5.1) |
| 3 | Ativação única que não retorna — causa não identificada | investigação (§5.2) |
| 4 | Tenant default suprimido pelo `skill_id` legado + aviso na tela | defeito de config (§4) |
| 5 | `queue` no `ParticipantRoleSchema`; produtor de `supervisor` | vocabulário (§6) |
| 6 | Re-medir o "resíduo 1" (passado não reparado) antes de decidir reprocessamento | medição (§5.4) |
| 7 | Os 15 órfãos `sac_ia`/`primary` — estáticos, fenômeno separado | investigação |
| 8 | `spawn_reason` como discriminador único; `purpose` sai do critério de contato | D-D (§1) |
| 9 | Dois tipos de espera (fila × reivindicação em pull) | hipótese não medida (§3.4) |
| 10 | **Abandono na fila não fecha a sessão** — `active` + `abandoned` na mesma linha | defeito (§5.4) |
| 11 | Dois enums de papel divergentes + um terceiro vocabulário (`author_role`) | vocabulário (§6) |

**Gate possível e barato:** os quatro casos de fila são reproduzíveis pelo **e2e**
(instâncias `e2e-inbound-*` / `e2e-conference-specialist-*`), não por teste manual. Um gate com
testemunha — os 47 segmentos de fila que fecham têm de continuar fechando — é escrevível hoje.
