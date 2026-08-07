# ADR — Webhook: registro único de endpoint e identificador opaco

**Status:** proposto (2026-08-07)
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
| **A** | **Inventário** — script que lista toda superfície acionável (pools webhook, `webhook_skill_id`, `ChannelEndpoint`, `workflow.webhooks`) e cruza com o registro | achar 0 superfícies ⇒ INCONCLUSIVO (detector quebrado) |
| **B** | **Seed idempotente** — uma linha `ChannelEndpoint` por endereço em uso, origem `internal` | linha divergente do pool que a originou |
| **C** | **Resolução pelo registro** no `/v1/channels/webhook/{identifier}`, com o fallback por `webhook_skill_id` **ainda ativo** e **logando** quando é ele que responde (o log é a medida de quem ainda não migrou) | disparo que hoje funciona passar a 404 |
| **D** | **Tela** — listagem com origem carimbada; `internal` read-only | endpoint acionável ausente da tela |
| **E** | **Remoção do fallback** — só quando o log da fase C zerar | log não-zerado |
| **F** | **Legado por token** — decidir entre migrar `workflow.webhooks` para o registro ou aposentá-lo junto com a listagem de instâncias (ver I5 § "Quatro sintomas") | — |

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

## 7. Em aberto

- **O caminho por `pool_id` (`/v1/channels/webhook/pool/{id}`) sobrevive?** Ele já é endereço-por-pool
  e não tem o defeito do §2. Duas leituras: (a) mantém-se como atalho interno legítimo, sem registro,
  porque endereça a unidade canônica diretamente; (b) também vira registro, para que o inventário
  seja completo. Inclina-se para **(b)** — o valor do inventário é ser exaustivo —, mas isso é
  decisão a tomar na fase A, com o número na mão.
- **`workflow.webhooks`**: migrar ou aposentar depende de haver tenant real disparando por token.
- **Endpoints de canal não-webhook** (`collect`, `delegate`, `identity/*`) compartilham o prefixo
  `/v1/channels/webhook/` mas **não são endpoints de tenant** — são RPC interno com nome infeliz.
  Fora do escopo deste ADR; anotado porque confunde a leitura da tabela de rotas.
