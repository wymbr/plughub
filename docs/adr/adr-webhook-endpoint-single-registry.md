# ADR — Webhook: registro único de endpoint e identificador opaco

**Status:** aceito — **Fases A–F ✅ (arco completo, 2026-08-07)**. A remoção física do legado e a
autenticação de webhook saem como arcos próprios (§7.8.4). O arco de auth ganhou uma seção aqui
(**§7.9**, 2026-08-10) porque a sua fatia 3 se resolveu por um argumento sobre a topologia das DUAS
portas — matéria deste ADR, não do arco filho.
**Contexto imediato:** levantamento da I5 § "Lacuna 4b", que ao remover um botão sem alvo expôs
três superfícies de webhook e um registro só.

---

## 1. O problema, medido

Hoje existem **três formas de ser acionável por webhook** e **uma** aparece em Configuration:

| Caminho | Registro | Visível na UI |
|---|---|---|
| `POST /channel/webhook/{slug}` | `ChannelEndpoint` (`identifier` → `pool_id`) | ✅ `/config/channels` › Webhook |
| `POST /v1/channels/webhook/{skill_id}` · `…/pool/{pool_id}` | **nenhum** | ❌ não existe linha |
| `POST /v1/workflow/webhook/{id}` (token `plughub_wh_…`) | `workflow.webhooks` | ❌ editor em `/workflow/calendar`, rota **fora do menu** |

**O defeito não é colisão — é ausência de inventário.** A colisão foi procurada e não se
reproduz: `@@unique([tenant_id, channel, identifier])` (`schema.prisma:289`) bloqueia duplicata no
registro, e os caminhos interno e externo vivem em **prefixos de URL diferentes**, logo não
compartilham namespace. O que não existe é resposta para *"quais URLs disparam alguma coisa neste
tenant?"*: todo pool com `webhook_skill_id` está acionável **agora**, sem linha em lugar nenhum.

Isso viola duas invariantes que o `CLAUDE.md` já declara — **"One source per domain"** e **"Every
config field is UI-editable"**. Webchat, WhatsApp e voz passam todos por `ChannelEndpoint`;
**webhook é o desviante, não o caso especial.** Ou seja: não estamos criando regra nova, estamos
aplicando a existente ao domínio que escapou dela.

---

## 2. O achado que dissolve a discussão de nomenclatura

A pergunta que iniciou isto era *"eliminar `flow_id`?"*. Ela some sozinha, porque o endereçamento
por skill **não é inevitável — é convenção datada, e já rebaixada pelo próprio projeto**:

1. **O mecanismo não precisa dela.** O caminho por slug chama o MESMO `handle_trigger` com
   `skill_id = ""` e `pool_id` preenchido (`channel-gateway/main.py:1191`, *"pool-driven: runs the
   pool's deployed skill"*). Duas portas, uma função; uma delas ignora o campo.
2. **A resolução por skill é fallback, não primário.** `routing-engine/models.py:55-57`: o adapter
   publica `pool_id=None` + `skill_id=<endpoint>` e o router casa contra `webhook_skill_id` de cada
   pool — explicitamente *"router.route fallback"*.
3. **Já foi rebaixada por escrito.** Arc 19 definiu *"cada skill registrada num pool webhook é um
   endpoint (análogo a DIN)"*; o invariante posterior inverteu: *"O POOL é a unidade endereçável —
   nunca o `skill_id`… sobrevive só como endereço legado, válido enquanto **um único** pool o
   declara."*

E há um **regime legítimo em que o endereço por skill tem de falhar**: um
`skill_survey_outbound_v1` deployado em três pools (um por grão de sinal) torna a resolução
ambígua, e o router **rejeita** (`Webhook endpoint AMBÍGUO`). Não é acidente — é caso de uso
previsto no próprio `CLAUDE.md`. *Manter o endereço por skill não é neutro: é manter um endereço
com modo de falha conhecido e data marcada para acontecer.*

---

## 3. Decisões

### D1 — `ChannelEndpoint` é o registro único de entrada acionável por webhook

Nenhuma URL dispara sessão sem linha no registro. Mesma regra dos demais canais.

### D2 — O `identifier` é OPACO: sem semântica, com validação sintática

O identificador **não codifica** qual skill roda, e **nada o interpreta** — é endereço externo,
como um E.164 ou um DID. É `identifier`, o mesmo campo dos outros canais.

**"Sem regra de formação" significa sem regra SEMÂNTICA, não sem validação.** O identificador vira
URL pública, logo mantém restrição **sintática**: URL-safe (`^[a-z0-9][a-z0-9_-]{1,63}$`),
case-insensitive na resolução. Sem isso, "opaco" viraria "aceita barra e espaço", e o endereço
quebraria o roteamento — trocaríamos um acoplamento semântico por um defeito de transporte.

### D3 — "Qual skill roda" é o slot `current` do pool — o path NUNCA foi fonte válida

A informação não se perde: a autoridade já é `PoolSkillSlot.current` (+ `deploy_version`), por
Skill Versioning Fase B/C. **Derivar skill do path sempre foi a fonte errada**, não apenas
redundante: depois de um `promote`, o path continua dizendo `skill_x` enquanto o pool executa outra
coisa. Um endereço que descreve o que roda envelhece no primeiro deploy — é o mesmo defeito de
categoria que o modelo de slots existe para fechar.

### D4 — O caminho por `skill_id` vira endereço REGISTRADO, não caminho especial

Cada `webhook_skill_id` em uso ganha **uma linha `ChannelEndpoint`** com aquele mesmo texto como
`identifier`. Consequência que torna a mudança barata: **os chamadores internos não mudam** —
continuam mandando a mesma string; o que muda é o servidor, que passa a resolvê-la pelo registro em
vez de pelo fallback. É **backfill, não reescrita**.

Depois disso a string não é mais "um skill_id": é um identificador como outro qualquer, que por
acaso tem aquele texto. A ambiguidade do §2 desaparece **por construção** — três pools servindo a
mesma skill viram três identificadores distintos, e `@@unique` garante um alvo por endereço.

### D5 — `Pool.webhook_skill_id` deixa de ser endereço

Ele é a direção pool→endereço do mesmo fato, e dois donos de um fato é o que a invariante proíbe.
⚠️ **Atenção ao segundo uso**: ele também carimba o DNIS no `conversations.inbound`
(`routing-engine/models.py:272`). Esse carimbo passa a levar o **`identifier`**, não a skill — senão
a analítica continua descrevendo o endereço por um nome que deixou de sê-lo.

### D6 — Endpoint derivado aparece DECLARADO, não editável

Linha de origem `internal` é **visível e read-only** na tela: nasce do pool, não do cadastro.
*Ausência honesta vira presença declarada* — a diferença entre "não sei o que dispara isto" e "sei
que é interno". Origem carimbada na listagem: `external` (cadastrada) · `internal` (derivada) ·
`legacy_token` (`workflow.webhooks`).

### D7 — Ordem de migração é parte da decisão, não detalhe

**Semear ANTES de trocar a resolução; remover o fallback POR ÚLTIMO.** Invertido, todo disparo
interno passa a dar 404 — e 404 em webhook é falha **muda** do lado de quem chama. A janela em que
os dois caminhos coexistem é intencional.

### D8 — Guard e gate

- `POST /v1/channel-endpoints` recusa (**409 nomeado**) `identifier` que colida com outro endpoint
  do mesmo tenant/canal — já garantido por `@@unique`, mas o erro precisa **dizer qual** linha
  colidiu, não devolver violação de constraint crua.
- Gate novo: falhar se existir pool com `channel_types` contendo `webhook` **sem** linha
  correspondente no registro. É o inventário virando teste — o mesmo padrão de
  `gate_orphan_ui_callers.sh`, com contador-testemunha (zero pools webhook ⇒ INCONCLUSIVO).

---

## 4. Alternativas descartadas

| Alternativa | Por que não |
|---|---|
| **Renomear `flow_id` → `skill_id` em tudo** | 306 ocorrências, 76 arquivos, incluindo coluna ClickHouse e evento Kafka. E não resolve nada: o problema é **ausência de registro**, não o nome do campo. Renomear deixaria os mesmos endpoints invisíveis, com nome mais bonito. |
| **Matar o caminho interno de uma vez** | Quebra `workflow_trigger` e os fluxos de intake, e **não há inventário** do que quebraria — decidir a remoção antes de listar é o erro que a § Lacuna 3 registra. |
| **Documentar e deixar como está** | A invisibilidade permanece, e ela é o defeito. Documentação não responde *"quais URLs disparam?"* para um tenant específico. |
| **Registro próprio para webhook (4ª tabela)** | Seria a quarta fonte para o mesmo domínio. `ChannelEndpoint` já é o registro de entrada de todos os canais; webhook não tem nada de especial que justifique tabela própria. |

---

## 5. Fases

| Fase | Entrega | Reprova se |
|---|---|---|
| **A** ✅ | **Inventário** — script que lista toda superfície acionável (pools webhook, `webhook_skill_id`, `ChannelEndpoint`, `workflow.webhooks`) e cruza com o registro | achar 0 superfícies ⇒ INCONCLUSIVO (detector quebrado) |
| **B** ✅ | **Seed idempotente** — uma linha `ChannelEndpoint` por endereço em uso, origem `internal`. **Declarada no YAML, não derivada do pool** (§7.1) | linha divergente do pool que a originou |
| **C** ✅ | **Resolução pelo registro** no `/v1/channels/webhook/{identifier}`, com o fallback por `webhook_skill_id` **ainda ativo** e **logando** quando é ele que responde (o log é a medida de quem ainda não migrou) | disparo que hoje funciona passar a 404 |
| **D** ✅ | **Tela** — listagem com origem carimbada; `internal` read-only | endpoint acionável ausente da tela |
| **E** ✅ | **Remoção do fallback** — ~~só quando o log da fase C zerar~~ **critério trocado por inventário estático de discadores (§7.7)** | discador de produção sem linha no registro |
| **F** ✅ | **Legado por token** — decidido: **aposentar**, com a autenticação virando item PRÓPRIO (§7.8) | — |

---

## 6. Resultado da Fase A — inventário *(medido 2026-08-07)*

`infra/test/probe_webhook_endpoint_inventory.sh`, tenant demo. **As 4 previsões bateram exatamente**
(contadas antes na fonte declarativa) — o que também prova que, para estas entidades, **não há drift
entre `infra/registry/*.yaml` e o store**:

| | Previsto | Medido |
|---|---|---|
| F1 — pools com canal webhook | 10 | **10** |
| F2 — `ChannelEndpoint(webhook)` | 1 | **1** |
| F4a — endereços internos SEM registro | 10 de 10 | **10 de 10** |
| F4b — endpoint registrado com pool inválido p/ o canal | 1 de 1 | **1 de 1** |
| F3 — `workflow.webhooks` (token) | — | **0** |

**Onze superfícies acionáveis; uma aparece na tela, e é justamente a suspeita.**

Os dez internos: `skill_gate_promocao_v1`, `skill_formfill_demo_v1`, `skill_wrapup_detached_v1`,
`skill_deploy_promote_v1`, `skill_outbound_demo_v1`, `skill_outbound_dispatch_v1`,
`skill_outbound_worker_v1`, `skill_outbound_survey_dispatch_v1`, `skill_outbound_survey_worker_v1`,
`skill_portabilidade_demo_v1`.

**`F3 = 0` fecha um laço aberto na mesma manhã:** `workflow.instances` está vazia **porque**
`workflow.webhooks` está vazia — o único escritor daquela tabela (`router.py:794`) exige uma linha
desta. Os dois achados eram um só. Isso **não** decide a fase F (demo vazio ≠ nenhum tenant usa),
mas mostra que nada no demo depende do caminho por token.

### 6.1 `crm-callback` — medido, e o resultado REFUTOU a hipótese *(2026-08-07)*

**O endpoint FUNCIONA.** Sequência real, ponta a ponta:

```
POST /channel/webhook/crm-callback           → HTTP 201 (sessão criada)
routing: Queued … pool=retencao_humano — no agents available   ← nenhum humano logado AINDA
[recursos conectados]
→ o contato foi ENTREGUE a um agente humano, atendido e encerrado
```

**Por quê:** `router.py:86-92` — quando `event.pool_id` é explícito, `pools = [pool]`, **sem filtro
de canal**. O filtro por `channel_types` vive só no ramo *legado* de descoberta (`:94`, "scan all
pools compatible with the channel"). O comentário do código diz a regra: *"the channel entry point
declares the service pool, so the routing engine never needs to infer it"*.

Ou seja: **canal é hard filter sobre a DESCOBERTA de pool, não sobre um pool endereçado.** E um
`ChannelEndpoint` é exatamente um entry point que declara o pool. `crm-callback` →
`retencao_humano` é configuração **válida**.

#### Correção de método — o erro que produziu a hipótese falsa

A versão anterior desta seção afirmava "hard filter provado" a partir de um snapshot com
`available: 2` lido **depois** de os recursos serem conectados. **Uma leitura pós-intervenção foi
usada como controle do estado anterior.** A contemporaneidade foi *inferida* de um `updated_at`
isolado, em vez de *provada* comparando-o com o instante do disparo.

> **Regra que este caso deixa:** *um controle só vale se for CONTEMPORÂNEO do fenômeno, e
> contemporaneidade se prova comparando instantes — nunca se deduz de um timestamp lido sozinho.*
> É a versão temporal do erro que a § Postura já cataloga: o valor era plausível (`available: 2`
> explicava tudo), e por isso passou.

#### O que isso muda na D8

- **Validar existência do pool: mantém.** Endpoint apontando para pool inexistente não tem defesa.
- **Validar canal declarado: REBAIXADO a aviso.** Rejeitar quebraria configuração legítima — esta
  aqui. O `channel_types` do pool governa descoberta e alimenta o Monitor; divergir dele é
  higiene/confusão, não falha.
- A justificativa "fabrica contato abandonado" **cai**: a sessão enfileirou por não haver agente
  logado, não pelo canal. Enfileirar sem agente é comportamento correto.

## 7. Resultado da Fase B — seed idempotente *(implementado 2026-08-07)*

Probe verde: `F1=10 · F2=11 · F3=0` → **21** superfícies · `sem registro: 0` · `pool inexistente: 0` ·
`procedência: 0 errada(s) de 10 checada(s)` · 1 aviso (`crm-callback`) · **exit 0**. As quatro previsões
bateram exatas. Detalhe de implementação e arquivos no `CHANGELOG.md`.

### 7.1 A decisão da fase — **declarado, não derivado** (emenda que precisa a D6)

A D6 diz *"nasce do pool, não do cadastro"*, o que lido rápido sugere que o `RegistrySyncer` deveria
**derivar** a linha de `pool.webhook_skill_id` no boot. **Recusado**, e a recusa vale como emenda:

1. **Derivar inverte a D5.** Ela retira de `webhook_skill_id` o papel de endereço; copiá-lo ao registro
   a cada boot mantém a autoridade nele e rebaixa o `ChannelEndpoint` a projeção — o contrário da D1.
   A derivação nem sobrevive à Fase E: retirado o campo, ela fica sem fonte e as linhas sem declaração.
2. **Derivar torna o gate da D8 um teste que não pode reprovar** — a linha concordaria com o pool por
   ter sido copiada dele. Declarada, a reprovação tem causa cotidiana: pool webhook novo sem entrada.

**Leitura correta da D6:** *read-only* é sobre o operador não editar no console; "declarado" é literal —
a superfície de declaração da plataforma é `infra/registry/*.yaml`. Derivado não é declarado, é inferido.
A derivação sobrevive como **checador** (`_validate_webhook_endpoints`, ERROR fail-open), nunca como
escritor. Guard e probe respondem perguntas distintas: o guard lê o **YAML** (declaração faltando), o
probe lê o **store** (drift arquivo↔registro).

### 7.2 `origin` é coluna porque a D2 obriga

`channel_endpoints.origin` (`external|internal|legacy_token`, default `external`). Não é conveniência de
UI: a D2 torna o `identifier` opaco e decide que *nada o interpreta* — sem a coluna, "esta linha é
interna" só seria decidível lendo o texto do identificador (`skill_…`), que é a semântica que a D2 retira.
Não participa da resolução; fora do update (procedência é fato de nascimento, não atributo editável).

### 7.3 D8 — o que ficou implementado

`POST /v1/channel-endpoints`: **pool inexistente reprova** (400 nomeado) · **canal não declarado só avisa**
(§6.1) · **409 nomeia a linha colidida** (pool, display_name, origin, id). Falta a Fase E do gate — o
inventário como teste de CI.

### 7.4 Erro de método cometido nesta fase *(catálogo, mesma família do §6.1)*

A primeira execução do probe deu `F2=1` e pareceu *"o seed não aplicou"*; o log mostrava os 10 `created`
segundos depois. **`up -d` retornar não é "o syncer rodou"** — o bridge sobe após `agent-registry` +
`skill-flow-service` ficarem healthy, e a medição foi feita **antes de a intervenção terminar**. É o §6.1
espelhado: lá, controle lido *depois* da intervenção; aqui, medição *antes*. Nos dois casos o número era
plausível (`F2=1` é exatamente o valor pré-Fase-B) e por isso não pareceu erro.

> **Regra que este caso deixa:** *entre agir e medir, prove que a ação terminou. Um comando que retorna
> não é uma ação concluída* — o par da regra do §6.1 sobre contemporaneidade do controle.

Segundo achado, no instrumento: o bloco novo `F4c` imprimiu *"(todas carimbadas internal)"* sobre **zero**
linhas checadas. Corrigido com contador-testemunha (`CHECKED`); sem amostra o bloco se declara
**NÃO CHECADO**. Não afetou o veredicto (`F4a = 0 ⇒ CHECKED = 10` por construção), mas a linha impressa
mentia — e induzia a leitura "procedência ok, só falta semear".

---

## 7.5 Fase C — resolução pelo registro *(implementada e MEDIDA 2026-08-07)*

**Gate verde, quatro previsões exatas:** positivo `via REGISTRO 1` · `fallback 0`; negativo
`fallback 1` (`motivo=not_found`); routing `FALLBACK ROTEOU 0`. Nenhum disparo virou 404.

`/v1/channels/webhook/{identifier}` passou a resolver pelo **registro** (`resolve_pool_ex` →
`ChannelEndpoint`) e a publicar `pool_id` explícito. O fallback por `webhook_skill_id` segue
**ativo e intocado**; o que mudou é quem responde primeiro.

**Choke point único, verificado:** todo caminho de disparo passa pela porta HTTP do gateway
(`workflow_trigger` do mcp-server e o proxy `/v1/workflow/trigger` da workflow-api). Nenhum produtor
publica `conversations.inbound` com `pool_id=None` por fora — por isso um log de fallback no ROUTER,
se aparecer sozinho, é achado, não ruído.

**Dois logs, em camadas diferentes e de propósito.** O gateway loga a *intenção* ("não resolvi pelo
registro", com o motivo); o router loga o *fato* ("o fallback roteou"). O primeiro nomeia o endereço a
semear; o segundo cobre qualquer produtor futuro que contorne a porta HTTP.

### 7.5.1 `skill_id` continua no evento — e isso é decisão, não descuido

Quando o registro resolve, o evento leva `pool_id` **e** `skill_id`. Ele deixa de ser chave de
roteamento (o router o ignora quando há `pool_id`) e sobrevive como registro de **qual endereço foi
discado** — o papel de DNIS que a D5 quer que o carimbo passe a ter. Zerá-lo, como faz o caminho da
slug, apagaria a única evidência do endereço numa fase cujo propósito é inventário.

> ⚠️ **Emenda de fato à D5.** Ela adverte que `webhook_skill_id` "também carimba o DNIS no
> `conversations.inbound` (`routing-engine/models.py:272`)". **Isso não acontece no código.** A linha
> 272 é um COMENTÁRIO de campo; quem popula `sessions.dnis` é `analytics-api/models.py:89`, lendo
> `payload.dnis|dialed_number|to` ou `metadata.*` — nenhum dos quais o adapter de webhook escreve.
> Ou seja: **`dnis` é NULL para toda sessão webhook hoje**, nas duas portas, e a tabela "por endpoint
> (DNIS)" da `AnalisePoolsPage` mostra vazio para elas. A D5 descrevia uma intenção como se fosse
> comportamento. Consequência prática: carimbar o DNIS é trabalho a FAZER (candidato natural à Fase D,
> onde a tela já mostra o endereço), não um efeito a preservar — e manter `skill_id` no evento é o que
> mantém esse trabalho possível sem arqueologia.

### 7.5.2 `not_found` ≠ `unavailable` — separado agora porque a Fase E depende disso

`resolve_pool` colapsava em `None` *"perguntei e não existe"* e *"não consegui perguntar"*. Enquanto
todo caminho tinha fallback permissivo a diferença não aparecia. Ela passa a importar por dois motivos
independentes: **(1)** o log da Fase C é uma MEDIDA, e uma indisponibilidade do agent-registry contada
como "chamador não migrado" contamina o número que decide a Fase E; **(2)** na Fase E, `not_found` deve
virar 404 e `unavailable` **não pode** — 404 afirmaria que o endereço não existe por causa de uma falha
de rede. `resolve_pool_ex` devolve o motivo; `resolve_pool` fica como wrapper para o webchat, cujo
fallback permissivo não muda com o motivo. Correção de tabela junto: **`unavailable` não é mais
cacheado** — cachear falha transformava um soluço de 2 s em 30 s de resolução degradada.

### 7.5.3 Gate — `infra/test/gate_webhook_registry_resolution.sh`

Duas metades, e a segunda é o que torna a primeira falseável:

| | Endereço | Espera |
|---|---|---|
| **positivo** | `skill_formfill_demo_v1` (tem linha) | 201 · resolve pelo **registro** · 0 fallback |
| **negativo** | `skill_gate_c_controle_negativo` (sem linha) | 201 · cai no **fallback** (`motivo=not_found`) |

Sem a metade negativa, um código que logasse "via REGISTRO" incondicionalmente — ou um fallback já
removido — passaria verde. Ela também prova que **o fallback segue vivo**, que é a exigência da D7.
*Não* dispara os dez endereços: entre eles há `skill_deploy_promote_v1` (promove um pool de verdade) e
os dispatchers de outbound (drenam campanha e contatam gente) — gate com efeito colateral de produção
é dano, não cobertura. A existência das dez linhas é do probe da Fase B; o que este gate acrescenta é
que o gateway as CONSULTA.

### 7.5.4 Critério de entrada da Fase E

`FASE C · FALLBACK webhook` zerado em produção por uma janela representativa — descontado o 1× que o
controle negativo produz de propósito a cada execução do gate. E, antes de remover o fallback,
`unavailable` precisa ter tratamento próprio (503/retry), nunca 404.

---

## 7.6 Fase D — a tela *(implementada 2026-08-07)*

`/config/channels` › Webhook passou a carimbar **origem** por linha (`cadastrado` · `declarado` ·
`legado (token)`) e a tratar `internal` como **read-only**.

**Read-only não é permissão, é honestidade sobre quem manda.** A linha `internal` nasce da declaração
do ambiente (`infra/registry/*.yaml`), e o provisionamento é seed-if-absent: uma edição feita aqui
sobrevive só até o próximo boot do bridge, que repõe o que foi apagado. Aceitar a edição e perdê-la em
silêncio é pior que recusá-la — por isso a coluna de ações mostra *"declarado"* com a razão no título,
em vez de um botão desabilitado (que leria como falta de permissão).

### 7.6.1 O critério da Fase D obrigava a resolver o §7 — e a resposta não foi registrar

*"Endpoint acionável ausente da tela"* reprovaria assim que escrito: além dos identificadores
registrados, **todo pool webhook é acionável em `/v1/channels/webhook/pool/{pool_id}`** — dez endereços
vivos sem linha nenhuma, porque a Fase B decidiu não registrá-los.

A tela ganhou uma seção **"endereço implícito de cada pool webhook"**, derivada dos pools e sem linha no
registro. O raciocínio é o mesmo da §7.1, aplicado do outro lado: uma linha `identifier = pool_id →
pool_id` é a **função identidade do pool** — um registro que não pode discordar da fonte, logo um
inventário incapaz de denunciar qualquer coisa. E o endereço por pool não tem o defeito do §2: já
endereça a unidade canônica, e não fica ambíguo quando um skill está deployado em N pools.

> **O inventário fica completo na TELA — que é onde a pergunta *"quais URLs disparam?"* é feita — sem
> inflar o REGISTRO com linhas tautológicas.** Isto resolve o primeiro item do §8 (em aberto): a
> inclinação registrada era (b) "também vira registro"; a medição da Fase B e o critério da Fase D
> juntos mostram que (a) — atalho legítimo sem registro — é o certo, desde que apareça na tela.

### 7.6.2 O que a tela AINDA não mostra, e é de propósito

O registro legado por token (`workflow.webhooks`) é acionável, vive em **outra tabela** e tem editor numa
rota fora do menu. Ele **não** entra nesta tela. No demo isso é inócuo (`F3 = 0`), mas num tenant com
linhas a Fase D estaria incompleta por construção — então o probe passou a **reprovar** quando `F3 > 0`,
apontando a Fase F (migrar ou aposentar) como o conserto. Maquiar isso na tela seria dar aparência de
inventário completo a um inventário que não é.

### 7.6.3 ⚠️ Emenda à Fase B — ela MUDOU comportamento, num lugar que ninguém olhou

A Fase B foi anunciada (aqui e no `CHANGELOG`) como *"não muda comportamento: a resolução continua pelo
fallback"*. **Isso vale para a porta interna, e só para ela.**

A porta externa `POST /channel/webhook/{slug}` sempre resolveu pelo registro e **404 quando não achava**.
Ao semear os dez identificadores internos, eles passaram a existir no registro — logo
`/channel/webhook/skill_formfill_demo_v1` deixou de dar 404 e passa a criar sessão. **Dez endereços
internos ganharam uma segunda porta, sem que nada no ADR previsse isso.**

Não é falha de autenticação (as duas rotas vivem no mesmo gateway e nenhuma exige credencial), e é
consequência coerente da D1 — um registro, resolvido igual pelas duas portas. Mas vira **relevante em
deploy**: se um ambiente publica `/channel/webhook/*` na borda e mantém `/v1/*` restrito à rede interna,
a Fase B tornou publicamente acionáveis endereços que eram internos.

**Medido (2026-08-07):** `POST /channel/webhook/skill_formfill_demo_v1` → **201**. Confirmado, não inferido.

Duas saídas, e a escolha depende da topologia de exposição do ambiente — não do código:

| | |
|---|---|
| **(a) aceitar** | as duas portas servem o mesmo registro; é a unificação que a D1 pede. Correto se ambos os prefixos têm a mesma exposição. |
| **(b) filtrar por procedência** ✅ | a porta externa passa a servir só `origin='external'`. Preserva exatamente a alcançabilidade pré-Fase-B, e usa `origin` para o que ele existe — governança. |

**Decidido (b)**, e o argumento não foi "é mais seguro" e sim a **assimetria dos erros**: escolher (a) e
a topologia divergir depois expõe endereço interno **em silêncio**; escolher (b) e a exposição ser
uniforme custa um alias que ninguém usa. Quando a informação que decidiria (a topologia futura) não está
disponível, decide-se pelo custo de errar, não pela probabilidade.

**Implementação:** `resolve_pool_ex(..., allowed_origins=frozenset({"external"}))` na rota da slug; novo
desfecho `origin_refused`. Dois cuidados que valem registro:

- **O filtro é aplicado NA SAÍDA do cache, nunca antes.** A chave é `(tenant, canal, identificador)` e as
  duas portas a compartilham. Cachear o veredicto filtrado faria a primeira porta a consultar decidir
  pela outra — um `origin_refused` gravado pela externa viraria "não existe" para a interna, e o endereço
  sumiria por até `cache_ttl_s`. Intermitência dependente de quem chamou primeiro é o tipo de defeito que
  não se reproduz sob investigação.
- **A resposta é 404, o log é que nomeia.** 403 (ou "existe, mas é interno") confirmaria a existência do
  endereço a quem chama de fora, o oposto do que filtrar pretende. Quem precisa do motivo é o operador, e
  ele lê o log — degradar sem dizer por quê é que não vale.
- Linha **sem** `origin` (resposta anterior à coluna) conta como `external`: era o único caso que existia
  antes do campo, e recusá-la abriria uma janela de deploy derrubando endpoint legítimo.

**A lição de método é a de sempre, numa variação nova:** "não muda comportamento" foi verificado no
caminho que a fase estava mexendo, e não no caminho que a fase estava alimentando. *Semear um registro
muda todo mundo que lê aquele registro* — e a lista de leitores é maior que a lista de arquivos tocados.

### 7.6.4 Limite conhecido: read-only é da TELA, não da API

`PUT`/`DELETE` continuam aceitando linha `internal`. **É deliberado:** bloquear no servidor tiraria a
única forma de remover uma linha interna obsoleta depois que ela sai do YAML — o provisionamento é
seed-if-absent e **nunca poda**, então a linha ficaria imortal. O buraco prático (apagar pela UI e ver a
linha voltar no próximo boot) fecha com a tela não oferecendo a ação. Registrado em `TODO.md`; um
enforcement server-side só faz sentido junto de um caminho de poda explícito.

---

## 7.7 Fase E — remoção do fallback *(implementada e MEDIDA 2026-08-07)*

**Gate verde nas duas execuções**, previsões exatas: positivo **201** + `via REGISTRO`; negativo **404**
(era 201) + recusa nomeada; externa **404** + recusa por origem; testemunhas de fallback **0/0**. Com
`GATE_TEST_UNAVAILABLE=1`, registro parado → **503**, confirmando que indisponibilidade não vira 404.

**O caminho de PRODUÇÃO foi exercido à parte:** `infra/test/smoke_journey_root.sh` verde — ele dispara
`skill_portabilidade_demo_v1` (o discador nº 1 do inventário do §7.7.1) três vezes pela porta interna,
agora com o registro como único resolvedor, e confirma a raiz transitiva da journey (W3 herda a raiz de
W1, não de W2). É a evidência de que a troca de critério não custou o caminho real: o gate mede a
mecânica de resolução, este smoke mede um fluxo que alguém usa.

O registro é agora o **único** resolvedor de webhook. Não resolveu ⇒ a sessão não nasce.

- **channel-gateway** (`/v1/channels/webhook/{identifier}`): `not_found` → **404** nomeado ·
  `unavailable` → **503 + `Retry-After`**.
- **routing-engine**: apagado o ramo que casava `event.skill_id` contra `pool.webhook_skill_id`.

### 7.7.1 O critério escrito na tabela de fases foi TROCADO — e isso é a decisão

A Fase E dizia *"só quando o log da fase C zerar"*. **Esse critério não era satisfazível**: o demo é
reiniciado com frequência e o log tem minutos de vida; esperar por ele significaria ou nunca remover, ou
— pior — aceitar "não vi fallback nos últimos três minutos" como prova. Seria a mesma ausência-de-amostra
que este arco já pegou duas vezes (o `F4c` sobre zero linhas, a medição antes do boot terminar).

Substituído por **inventário estático dos discadores**, o mesmo método que funcionou na Fase A: contar na
fonte declarativa antes de medir. Discadores da porta por identificador:

| Chamador | Identificador | Linha? |
|---|---|---|
| `agente_portabilidade_intake_v1` (`workflow_trigger`) | `skill_portabilidade_demo_v1` | ✅ |
| `infra/test/smoke_journey_root.sh` | `skill_portabilidade_demo_v1` | ✅ |
| e2e via proxy `/v1/workflow/trigger` (cenários 03/13/14/18/28) | `flow_id` dinâmico | ❌ |

Os outros três `workflow_trigger` dos skills (`skill_survey_trigger_v1`, `skill_outbound_dispatch_v1`,
`skill_outbound_survey_dispatch_v1`) já endereçam **pool**. Bridge, scheduler e os demais smokes usam
`/pool/{id}`, `/resume/`, `/delegate` ou `/identity/*` — não tocam esta porta.

**Por que o inventário é evidência mais forte que o log aqui:** o log responde *"alguém usou o fallback
na janela observada?"* e depende de a janela ser representativa. O inventário responde *"quem PODE
usá-lo?"*, e a lista é fechada porque todos os produtores passam pela porta HTTP do gateway (verificado na
Fase C). Log mede amostra; inventário mede o espaço.

**Fallout aceito:** os cenários e2e que discam `flow_id` arbitrário passam a receber 404. Eles **já não
funcionavam** — nenhum pool declara aqueles skills, então o router os rejeitava e a sessão morria
enfileirada. A mudança troca *"201 + sessão que não vai a lugar nenhum"* por *"404"*, o que é mais
honesto, mas quebra asserção de 201. Registrado em `TODO.md`.

### 7.7.2 Apagar o ramo do router não é faxina — é o que fecha a D1

Enquanto o ramo existisse, um evento de webhook sem `pool_id` continuaria sendo roteado por um endereço
que o ADR aposentou: **um segundo resolvedor**, que é exatamente o que a D1 proíbe. Mantê-lo "por
segurança" preservaria a ambiguidade do §2 (mesmo skill em N pools) e o envelhecimento da D3 (o path
segue dizendo `skill_x` depois de um promote). *Um fallback que ninguém usa não é inofensivo — é uma
segunda fonte esperando alguém tropeçar nela.*

`Pool.webhook_skill_id` **sobrevive como campo**, e não por inércia: é a fonte declarativa de qual
identificador semear, e o guard `_validate_webhook_endpoints` do RegistrySyncer o cruza contra os
`channel_endpoints` declarados. Deixou de ser **endereço** (D5); continua sendo a declaração de que o
pool tem um.

### 7.7.3 O 503 é o ponto mais delicado da fase

`not_found` e `unavailable` estavam separados desde a Fase C, mas a distinção era ornamental enquanto o
fallback absorvia os dois. Aqui ela vira load-bearing: com 404 sobre indisponibilidade, um soluço de rede
do agent-registry faria todo disparo interno parecer endereço inexistente — e o chamador típico é
fire-and-forget, que não retenta o que "não existe". Falha de infraestrutura é retentável; endereço
inexistente não é.

Por ser o ramo que mais importa e o único que exige quebrar algo de propósito, o gate o exercita **por
opt-in** (`GATE_TEST_UNAVAILABLE=1`, com `trap` religando o serviço) e, sem a flag, **declara que não
mediu** em vez de omitir. Um ramo não exercido que aparece como verde é a forma mais barata de comprar
confiança sem dar nada.

### 7.7.4 Defeito do instrumento, achado na 2ª execução: janela de log por DURAÇÃO

As contagens saíram **2** onde deveria haver 1. A janela era `--since "${elapsed}s"`, calculada no fim do
teste; duas execuções seguidas ficam a poucos segundos uma da outra, e a duração da segunda alcançava o
log da **primeira**.

Não deu verde falso — as asserções eram `≥1` e os códigos HTTP são medidos à parte —, mas **poderia**: se
o disparo da execução atual não logasse nada, a sobra da anterior satisfaria o `≥1` sozinha, e o gate
aprovaria um caminho que parou de funcionar. *Contagem que soma execuções passadas não mede esta
execução.* Corrigido para instante **absoluto** (UTC, mesmo relógio do daemon), com 1 s de margem antes do
primeiro disparo — margem tarde demais perderia a primeira linha e daria vermelho falso.

Com a janela exata, as asserções subiram de `-ge 1` para **`-eq 1`**: `≥1` toleraria exatamente o defeito
que acabara de acontecer. A mensagem de falha distingue os dois lados (`0` = comportamento sumiu; `>1` =
janela suja), porque um número errado para cima e para baixo tem causas opostas.

### 7.7.5 O gate INVERTEU, e a inversão é o registro da fase

A metade negativa esperava **201** (o fallback atendia) e passou a esperar **404**. *Um gate cujo
resultado esperado não muda quando o comportamento muda não estava medindo o comportamento.* As duas
contagens de fallback (gateway e routing) viraram **testemunhas impressas, não asserções**: seus
produtores foram removidos, então elas só podem dar zero — e verificação que só pode dar o valor esperado
não distingue nada.

---

## 7.8 Fase F — legado por token: **aposentar**, e a autenticação vira item próprio *(decidido 2026-08-07)*

A Fase F pedia *"migrar `workflow.webhooks` para o registro OU aposentá-lo"*. **A pergunta estava mal
posta**, e reformulá-la é o resultado da fase.

### 7.8.1 O levantamento

| | |
|---|---|
| `workflow.webhooks` (demo) | **0 linhas** (F3, Fase A) |
| `workflow.instances` | vazia **porque** `webhooks` está vazia — `router.py:794` é o único escritor e exige uma linha desta |
| Execução | **viva**: o POST emite `workflow.started` e o `skill-flow-worker` (ainda no compose) consome e roda o flow |
| Ciclo de vida da instância | **morto**: `persist-suspend`, `complete`, `fail`, `cancel`, `collect/*` são todos 410. A linha nasce `active` e nunca muda de estado; flow que suspenda trava |
| Editor | `WebhooksTab` em `/workflow/calendar` — rota existente, **sem entrada em nav nenhuma** |
| Autenticação | **`X-Webhook-Token`**, SHA-256 + verificação em tempo constante + flag `active` + log de entrega |

### 7.8.2 O achado que reenquadra a fase

**O caminho por token é o único autenticado.** O `ChannelEndpoint` — nas duas portas — **não tem
autenticação alguma**: todo disparo desta sessão foi `curl` sem credencial, contra pools que promovem
deploy e contatam clientes.

Isso desmonta a dicotomia da tabela de fases. O legado tem **uma** coisa valiosa (autenticação) acoplada
a **um** modelo de execução morto (instância imortal, mutadores 410). Tratá-los como pacote força uma
escolha falsa: preservar o modelo morto para manter a auth, ou perder a auth ao matar o modelo. *Quando as
duas saídas oferecidas são ruins, desconfie de que a pergunta amarrou coisas separáveis.*

### 7.8.3 Decisão

**Aposentar o legado**, e tratar autenticação de webhook como **requisito de plataforma**, não como
resquício a resgatar. Duas consequências que precisam ficar explícitas:

1. **A janela protegida está vazia.** `F3 = 0` — nenhuma linha de token em uso. Aposentar não retira
   proteção de ninguém hoje; retira uma capacidade que nada exercita, e cuja ausência passará a estar
   **declarada** em vez de mascarada por um caminho que ninguém usa.
2. **A ausência de auth no registro canônico é anterior a este ADR e não foi criada por ele** — mas
   passou a ser visível por causa dele. É o mesmo movimento da Fase A: *ausência honesta vira presença
   declarada*.

### 7.8.4 Escopo (arco próprio, não este ADR)

**Remoção** — `workflow.webhooks` + CRUD + `POST /v1/workflow/webhook/{id}`; `workflow.instances` e o que
resta do lifecycle; `WebhooksTab` e a rota órfã `/workflow/calendar`; avaliar o `skill-flow-worker` e o
tópico `workflow.events` (⚠️ o cenário e2e 18 depende do worker, e a evaluation-api **consome**
`workflow.events` para `suspended`/`completed` — o tópico **não** morre junto sem análise própria).

**Autenticação (item novo)** — token opcional por endpoint no `ChannelEndpoint`: segredo só em hash,
`active`, log de entrega. O desenho já existe e funciona; o que muda é onde mora. Enquanto não existir,
**todo endpoint webhook é anônimo** — fato a registrar em `TODO.md`, não a esconder.

---

## 7.9 Auth fatia 3 — de onde os chamadores internos tiram o segredo: **de lugar nenhum** *(decidido 2026-08-10)*

A fatia 3 foi aberta como *"fazer os chamadores internos carregarem credencial, para que
`auth_required` possa ser ligado nos dez endpoints internos"*. **O inventário estático dos discadores
— mesmo método das Fases A e E — mostrou que a tarefa não deve ser feita.**

### 7.9.1 O inventário

Porta por identificador (`/v1/channels/webhook/{identifier}`), a única que tem onde pendurar credencial:

| Chamador | Local | Endereço discado |
|---|---|---|
| `agente_portabilidade_intake_v1` → `workflow_trigger` | `skills/agente_portabilidade_intake_v1.yaml:484` | `skill_portabilidade_demo_v1` (literal) |
| proxy `/v1/workflow/trigger` | `workflow-api/router.py:184` | `flow_id` dinâmico — **404 desde a Fase E** |
| ↳ único produtor vivo do proxy | `platform-ui/modules/workflows/api/hooks.ts:115` | `flow_id` digitado |
| `smoke_journey_root.sh:33` · os dois gates · `smoke_close_bugs_20260713.sh:57` | — | fixos de teste |

Porta por pool (`/v1/channels/webhook/pool/{id}`), **anônima por construção** (§7.6.1): `workflow_trigger`
quando há `pool_id` (`tools/workflow.ts:176` — `skill_survey_trigger_v1`, `skill_outbound_dispatch_v1`,
`skill_outbound_survey_dispatch_v1`) · orchestrator-bridge `_fire_detached_hook`
(`main.py:1294`) · scheduler-api `dispatcher.py:98` · ~10 smokes.

**O achado: dos dez `origin=internal`, NOVE não têm chamador nenhum na porta por identificador.** Os pools
deles são disparados pela porta por pool. Só `skill_portabilidade_demo_v1` é discado por identificador. O
`_fire_detached_hook`, citado no enunciado da fatia, **não entra no escopo** — é porta por pool.

*"Dez anônimos" foi lido como dez caminhos de disparo desprotegidos. São dez ENDEREÇOS e um DISCADOR* —
a mesma confusão entre presença e conteúdo que a § Postura cataloga.

### 7.9.2 O argumento que decide, e ele é estrutural

- `/v1/*` exposto na borda ⇒ a porta por **pool** está exposta junto ⇒ todo pool webhook é disparável
  anonimamente, **independente** de `auth_required`. Auth na porta por identificador é **teatro**.
- `/v1/*` não exposto ⇒ os dez internos são inalcançáveis de fora ⇒ auth neles é **redundante**.

Nos dois ramos, `auth_required` em `origin=internal` compra **zero** — não porque o risco seja baixo, mas
porque a porta por pool, que não tem onde pendurar token, **já força a topologia a ser a resposta**. É a
mesma conclusão que o §7.6.3 antecipou (*"a escolha depende da topologia, não do código"*), agora do lado
de dentro.

### 7.9.3 Por que não (a) token por endpoint nem (b) credencial de serviço

| | |
|---|---|
| **(a) distribuir o token de cada endpoint** | `workflow_trigger` é genérico: não sabe em build-time qual endereço vai discar, logo teria de resolver o token por identificador em runtime. O mcp-server passaria a guardar **N** segredos em vez de 1 — pior que (b), e reintroduz a rotação coordenada que o enunciado já temia. |
| **(b) credencial de serviço única** | Um segredo que diz *"sou da plataforma"* vale para **todo** endpoint, inclusive os `external` que o tenant protegeu com token próprio: vazou, discou tudo. Escopá-lo a `origin=internal` conserta a elevação — e aí ele é (c) com passos extras, porque continua sem fechar a porta por pool. |

### 7.9.4 O que (c) exige para não ser mentira

(c) tem o defeito clássico: é proteção que **não deixa evidência no código**, e *"está restrito na borda"*
é afirmação que ninguém verifica. Sem contrapartida, o `F6` do probe nunca desceria de 10 e a lista de
anônimos viraria ruído permanente — treinando a ignorar o número, que é o mesmo dano de um gate que não
pode reprovar. Fecha com três itens, todos implementados junto com esta decisão:

1. **`F6` reclassificado** — `internal` sai de *"anônimos pendentes"* e vira *"anônimo por DECISÃO"*, com
   o bloco **declarando que não mediu** a restrição de borda (o probe lê o store; exposição é infra). O
   número que resta como ordem de serviço é só o dos `external`.
2. **Guard `INTERNAL_AUTH_REFUSAL`** — `POST /v1/channel-endpoints` com `auth_required=true` em linha
   `internal` e `POST /v1/channel-endpoints/{id}/token` sobre linha `internal` passam a **422 nomeado**
   (era só `console.warn`). A segunda metade é a que importa: a tela já não oferece o botão desde a Fase D,
   mas **read-only da tela não é read-only da API** (§7.6.4) — e todo disparo deste arco foi por `curl`.
3. **A decisão registra sua dependência** — ela vale *enquanto a porta por pool for anônima por
   construção*. Dar endereço registrável ao pool (o que a §7.6.1 recusou) ou fechá-la reabre a pergunta.

**Consequência para o TODO:** o item 3 (*"plumbing de credencial nos chamadores internos"*) é **cancelado**,
não adiado. Nenhum chamador muda, o que satisfaz o invariante da fatia (*"um erro aqui não dá 401 num gate,
silencia processo real de produção"*) por não haver o que errar. O trabalho de segurança que sobra é a
**fatia 4** (default ON para `external` novo) e deixar o requisito de borda **escrito**, em vez de suposto.

---

## 7.10 Auth fatia 4 — endpoint `external` novo: **sem default, com decisão obrigatória** *(2026-08-10)*

O item pedia *"default ON para endpoint `external` novo"*. O inventário dos **criadores** (mesmo método
das fatias anteriores) mostrou que a formulação embutia uma premissa falsa: a de que existe **um** tipo de
criador.

### 7.10.1 Os criadores, e a assimetria entre eles

| Quem cria | `POST /v1/channel-endpoints` de onde | Consegue receber o token? |
|---|---|---|
| Operador pela UI | `ChannelEndpointList.tsx` (aba Webhook é standalone; os outros dois forms de criação ficam ocultos nela) | ✅ o 201 traz o token em claro e a tela mostra o banner (fatia 2) |
| `RegistrySyncer` | `registry_syncer.py:867`, a partir de `infra/registry/*.yaml` | ❌ lê só o código HTTP e **descarta o corpo** |

Com default ON no route, uma **instalação limpa** faria o syncer criar `crm-callback` (external,
declarado no YAML) já exigindo um token gerado e imediatamente perdido: **401 permanente, sem caminho de
recuperação**. Hoje não morde porque o provisionamento é seed-if-absent e as linhas existem (409) — é o
*"ambiente que só sobe porque já subiu antes"* da § Postura, com o defeito dormindo até o `--wipe`.

### 7.10.2 A decisão

**Não há default. `auth_required` ausente em `channel=webhook` + `origin=external` ⇒ 422 nomeado.**

*"Este chamador consegue guardar um segredo?"* é informação que só existe **no chamador**. O route não a
tem e não deve adivinhar: adivinhar ON quebra o provisionamento automático, adivinhar OFF é o opt-in que a
fatia 1 já diagnosticou como proteção que ninguém liga. Exigir a declaração torna as **duas** falhas
impossíveis de acontecer em silêncio. É o mesmo movimento do *"declarado, não derivado"* da Fase B (§7.1):
quando a autoridade está fora, importe-a em vez de inferi-la.

Onde cada criador declara:

- **UI** — caixa *"Exigir token em todo disparo"*, **marcada por padrão** (é aqui que a intenção original
  do "default ON" vive, e legitimamente: este criador recebe o segredo). Só aparece em webhook e só na
  criação; depois, ligar/desligar é pelos botões de token, que são os caminhos que entregam ou destroem o
  segredo. O texto de apoio **muda com o estado**, porque as duas escolhas têm consequências opostas e
  nenhuma é óbvia.
- **YAML** — `auth_required: false`, e **só `false` é válido ali**. O syncer rebaixa um `true` para
  `false` com log ERROR em vez de obedecer: obedecer criaria o endpoint inalcançável que a §7.10.1
  descreve. Endereço externo que precise de token nasce pela UI.

### 7.10.3 Achado paralelo — a flag só é aplicada em webhook

`_check_endpoint_auth` é chamado nas duas rotas de webhook e em nenhuma outra; webchat, WhatsApp, voz, SMS
e e-mail resolvem por `resolve_pool`, que não consulta `auth_required` (cada um tem handshake próprio —
JWT por tenant no webchat, assinatura no WhatsApp). Uma linha não-webhook com a flag ligada **afirmaria
proteção que ninguém aplica**, e a tela a mostraria como protegida.

**Decidido recusar (422)**, no create e no `POST /{id}/token`, pelo mesmo critério do §7.9: anônimo
declarado é honesto; protegido-mentiroso convida a parar de procurar. Estender o enforcement aos demais
canais é arco próprio, não uma linha aqui.

### 7.10.4 Cobertura

`gate_webhook_endpoint_auth.sh` ganhou **P11** (webhook external sem `auth_required` ⇒ 422) e **P12**
(`auth_required` em canal não-webhook ⇒ 422). P11 é o que impede um default de ser reintroduzido em
qualquer dos dois sentidos sem ficar vermelho. Os ids dos creates que *deveriam* falhar entram no `trap`:
quando o guard está ausente a linha nasce, e é justamente aí — com a atenção no erro, não no ambiente —
que o lixo passaria despercebido.

---

## 8. Em aberto

- ~~**O caminho por `pool_id` (`/v1/channels/webhook/pool/{id}`) sobrevive?**~~ **RESOLVIDO na Fase D
  (§7.6.1) — venceu (a), contra a inclinação registrada aqui.** Ele se mantém como atalho legítimo
  **sem registro**, e o inventário fica exaustivo **na tela**, não na tabela. Registrá-lo criaria uma
  linha que é a função identidade do pool — incapaz de discordar da fonte, logo incapaz de denunciar
  nada. O valor do inventário é ser exaustivo *onde a pergunta é feita*, e ela é feita na tela.
- ~~**`workflow.webhooks`**: migrar ou aposentar~~ **RESOLVIDO na Fase F (§7.8) — aposentar.** A pergunta
  estava mal posta: o legado acopla autenticação (valiosa) a um ciclo de vida de instância morto. Separados,
  a decisão é óbvia. A auth vira item de plataforma; a remoção física, arco próprio.
- **Endpoints de canal não-webhook** (`collect`, `delegate`, `identity/*`) compartilham o prefixo
  `/v1/channels/webhook/` mas **não são endpoints de tenant** — são RPC interno com nome infeliz.
  Fora do escopo deste ADR; anotado porque confunde a leitura da tabela de rotas.
