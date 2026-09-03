# ADR — Fluxo de agente tem UM nível autorado; o resto é plataforma

> **Status:** proposto · **Data:** 2026-09-03
> **Substitui como modelo de trabalho:** o modelo de três níveis para **fluxos de agente**
> descrito em [`business-in-any-media-arquitetura-alvo.md`](../product/business-in-any-media-arquitetura-alvo.md) §2
> e materializado em [`limite-credito-3-niveis-design.md`](../product/limite-credito-3-niveis-design.md) §1.
> Aqueles documentos ficam **intactos como raciocínio de época** — reescrevê-los corromperia a
> evidência; ambos ganharam nota datada apontando para cá.

> ⚠️ **DESAMBIGUAÇÃO — "três níveis" nomeia DOIS modelos neste repositório, e esta ADR só
> dissolve um.** O outro, **intocado**, é o modelo de escopo `segment` / `session` / `journey`
> (`CLAUDE.md` § *Never create a wide container…*), que continua vigente e não tem relação com
> este. Se você chegou aqui procurando por aquele, esta ADR não é sobre ele.

---

## 1. O modelo que existia

| Nível | Responsabilidade | Artefato previsto |
|---|---|---|
| **N3 · (a)** | processo negocial, abstraído de canal | `workflow` (pool `webhook`) |
| **N2 · (b)** | acesso a canal: identidade, processo pendente, retomada, eleição de canal | `agent` fino — `skill_acesso_canal_v1` |
| **N1 · (c)** | I/O no canal: render nativo, captura, mídia | `agent` — `skill_limite_intake_v1`, `…_retorno_v1` |

A referência-mãe já classificava **(b) como *"parcial / a consolidar — as peças existem
dispersas, mas não como camada explícita e reusável"***. Esta ADR responde àquela pendência —
e a resposta **inverte a intenção**: as peças ficam dispersas *como primitivos de plataforma*,
e (b) **não** vira camada de fluxo.

## 2. O que foi medido (2026-09-03)

**(1) Dois dos três artefatos previstos nunca foram escritos.** `skill_acesso_canal_v1` e
`skill_limite_intake_v1` não existem. O modelo de três níveis **nunca teve as-built** — o
próprio `limite-credito-3-niveis-design.md` já o dizia no cabeçalho, em 2026-08-11.

**(2) O N2 já é plataforma, e o fluxo só o sequencia.** O `skill_limite_entrada_v1` — o
artefato que o modelo chama de N1 — tem **10 `invoke`**, e a lista é a pauta inteira do N2:

```
customer_resolve         → identidade
pending_workflow_get ×2  → localizar processo pendente
workflow_resume          → retomada
otp_challenge / verify   → posse de canal
journey_merge · form_get · context_set · workflow_trigger
```

Todas são **tools MCP da plataforma**. O fluxo não implementa nenhuma dessas capacidades; ele
escolhe a ordem. Ou seja: a substância do N2 já está fora do fluxo — o que o "nível" acrescentava
era um contêiner.

**(3) A eleição de canal já existe, implementada e pura, e nunca recebe exigência.**
`channel_capability_registry.py` mapeia canal → capacidades, `masked_input` é capacidade
declarada e **só o webchat a tem**, e `select_channel()` é chamada de `main.py:365` no
`collect.requested`. Mas o `requires[]` é **declarado pelo autor do fluxo**, e **nenhum YAML do
repositório declara `requires:`**. Portão vivo que nunca recebeu uma exigência real.

> ⚠️ **CORREÇÃO de 2026-09-03, ao implementar a F2 — a premissa acima está PELA METADE, e a
> metade errada era a tranquilizadora.** *"Portão vivo"* é falso: `select_channel` só é chamada
> de `main.py::_dispatch_collect`, consumidor de `collect.requested`, e o **único** produtor
> desse evento (`workflow_api.kafka_emitter.emit_collect_requested`) tem **zero chamadores**
> (medido por AST — `grep` acha o nome, mas ele aparece só no `import`). As duas rotas de
> collect da workflow-api respondem **410** desde o Arc 19 Fase D, e a analytics-api já
> carregava o comentário *"GATILHO: quando `collect_events` tiver produtor"*. Alimentar
> `requires[]` ali não mudaria nada — seria construir o insumo de um portão que não roda.
>
> A eleição que **roda** é `WebhookAdapter._negotiate_channel` (via
> `POST /v1/channels/webhook/collect`), e ela era **cega a capacidade**: escolhia por
> `preferred_order` e nunca perguntava o que o canal sabe fazer. Pior, o `requires` **já
> chegava até ela** — engine → skill-flow-service → corpo do POST → parâmetro declarado — e era
> **descartado sem uso** (zero ocorrências em `Load` no corpo de 228 linhas).
>
> São **duas implementações de eleição, e a que decide é a permissiva** — exatamente a forma
> que a F1 fechou um nível abaixo, no INVENTÁRIO de capacidade. Por isso a F2 entregue faz as
> duas metades: derivar a exigência **e** fazer a eleição viva consultá-la. *A lição de método
> é a de sempre: antes de alimentar um portão, meça se ele roda — "está implementado e testado"
> não é "está no caminho".*

**(4) O roteiro está cravado no fluxo.** O mesmo `skill_limite_entrada_v1` tem **11 `notify`**
com texto em português literal (*"Olá! Posso te ajudar com o aumento de limite…"*). Isso não é
I/O nem negócio.

**(5) Há duas casas para "este canal sabe mascarar?"**, e elas não se falam:
`CHANNEL_CAPABILITIES` (dict hardcoded em Python, com comentário *"keep in sync"*) e
`ChannelCapabilities.supports_masked_input` (campo de config por tenant, **zero consumidores**).

## 3. Decisão

**O tenant autora UM nível: o processo.** O que o modelo chamava de N1 e N2 se distribui em
**três destinos** — e são três, não dois:

| o que era | destino | dono | estado |
|---|---|---|---|
| N1 **mecânica** — eco, `type=password`, bipe, supressão de DTMF | **canal** | adapter; cliente onde houver | disperso, ver §5 |
| N1 **roteiro** — as frases, a ordem, a validação | **conteúdo** (`DialogForm`) | tenant, mas como dado editável | existe |
| N1 **execução** do roteiro | **plataforma** — `skill_dialog_runner_v1` | plataforma, genérico | existe |
| N2 identidade · pendência · retomada · OTP | **plataforma** — tools MCP | plataforma | existem |
| N2 **eleição de canal** | **plataforma** — `select_channel` | plataforma | existe, sem insumo |
| N3 processo | **o único fluxo autorado** | tenant | existe |

### 3.1 "Um nível" é um nível AUTORADO, não um fluxo no sistema

`menu`/`notify` são exclusivos do perfil **`agent`**; `suspend`/`collect` são exclusivos do
perfil **`workflow`**, e a segregação é validada no parse. **Um workflow não fala com o cliente.**

Logo, existir "um nível só" no sistema é impossível por contrato — e não é isso que se decide.
Decide-se que **o tenant escreve um**: o processo. Do outro lado do `collect` continua havendo
um agente, e ele é **fornecido pela plataforma** (`skill_dialog_runner_v1`), genérico, não
escrito por quem monta o processo.

Precedente que já tinha chegado aqui por outro caminho: `adr-wrapup-detached-pull` escolheu
*"o renderer é o tratamento genérico de collect-form no Console, servindo aprovação + wrap-up +
survey **sem skill por caso**"*. Esta ADR generaliza aquela escolha.

### 3.2 O I/O é do CANAL — e "canal" nem sempre é um cliente

A formulação *"o cliente trata o I/O"* só vale onde existe cliente nosso: **webchat e webrtc**.
Em **voz, WhatsApp, SMS e e-mail não há ponta que controlemos** — é um telefone, o app da Meta,
a operadora. Ali o I/O é do **adapter**.

É o mesmo argumento que decidiu a ALW-15: um desenho que dependa do cliente resolver política é
implementável em exatamente um canal, e esse canal é o fixture de teste.

### 3.3 O roteiro NÃO vai para o canal

Se "N1 vai para o canal" for lido sem esta cláusula, as 11 frases acabam dentro do adapter —
roteiro conversacional em código de transporte, versionado com o deploy do serviço e sem editor.
O Dialog Primitive já nomeia as costuras: **conteúdo × controle × canal × segredo**. Roteiro é
conteúdo.

## 4. Consequências

**Ganha-se:** a política de canal passa a valer para *todo* fluxo, inclusive os que ninguém
escreveu ainda — em vez de depender de o autor lembrar de declarar `requires`. *Remover a
alternativa custa menos que lembrar de não usá-la.*

**Perde-se:** um fluxo que queira interação fora do que o `DialogForm` expressa deixa de ter
onde escrevê-la sem criar um agente próprio. Isso é aceito: a escapatória continua existindo
(escrever um agente), ela só deixa de ser o caminho padrão.

**Não muda:** o perfil `workflow` já *força por contrato* que o processo não toque canal. Esta
ADR não afrouxa nada disso — ela remove o nível intermediário que existia para traduzir, porque
a tradução já é feita por primitivos.

## 5. Fatias em aberto (nenhuma implementada)

- ~~**F1 — uma casa só para capacidade de canal.**~~ **Entregue 2026-09-03** (NIV-01).
  `CHANNEL_CAPABILITIES` fica (capacidade é fato do PROTOCOLO); `ChannelCapabilitiesSchema`
  saiu, e o que era política sobreviveu em `MaskedFallbackPolicySchema`. A tabela virou
  **exaustiva** sobre o `ChannelSchema` — cobria 6 dos 9 canais, e os 3 ausentes nunca eram
  eleitos, em silêncio. Gate: `infra/test/probe_channel_capability_single_house.sh`.
- ~~**F2 — derivar `requires` ⊇ `{masked_input}` da declaração `masked:`**~~ **Entregue
  2026-09-03** (NIV-02), em **duas** metades e não uma — a redação original supunha que
  faltasse só o insumo, e a premissa (3) acima está corrigida no lugar. A derivação tem um
  sítio (`collect_requirements.derive_collect_requires`, alimentado pelo `DialogForm` que o
  collect renderiza) **e** a eleição VIVA passou a consultá-la, inclusive no ramo do
  `channel:` fixo — sem isso o portão seria desligável escrevendo uma linha no YAML. Gate:
  `infra/test/probe_collect_masked_requirement.sh`.
- ~~**F3 — recusa em DOIS momentos.**~~ **Entregue 2026-09-03** (NIV-03), e a divisão
  bloquear × avisar não é gradação de rigor: é natureza da pergunta. No **runtime**, o
  `notification_send` do mcp-server não publica menu mascarado em canal sem `masked_input`
  — recusa ANTES do Kafka (nada mascarado viaja) e devolve `isError`, que o `menu` step já
  converte em `on_failure`, sem protocolo novo. No **deploy**, `set-next`/`promote`
  recusam (422) skill que mascara em pool sem NENHUM canal capaz — estático, decidível sem
  cliente do outro lado — e **avisam** no pool parcialmente capaz, porque ali o desfecho
  depende de por onde o contato chega; recusar proibiria a configuração legítima que
  `auth_ia`/`auth_form_ia` têm hoje. Rollback isento (emergência nunca bloqueia).
  Fecha a **MSK-01**. Pré-requisito que a fatia descobriu: o mapa canal→capacidade virou
  canônico em `@plughub/schemas`, com o gêmeo Python sob gate de paridade — **refina a F1,
  não a reverte**: a casa única precisa ser LEGÍVEL por toda linguagem que decide, e os
  dois decisores novos são TypeScript. Gate:
  `infra/test/probe_masked_channel_gate.sh`.
- **F4 — os `notify` cravados migram para `DialogForm`**, caso a caso, quando o fluxo for
  tocado. Não é varredura.

## 6. O que esta ADR NÃO decide

- Não decide se um "N2 padrão" jamais faz sentido — decide que **as partes universais de N2 já
  são plataforma** e que o resto (quais identidades contam, quais processos são retomáveis) é
  regra de tenant, que não se congela em código de plataforma.
- Não promete *eleição inteligente de canal*. `_reachable_channels` devolve `[]` hardcoded,
  consentimento e política de tenant são slots vazios, `urgency` é parseado e nunca lido — como
  o `limite-credito-3-niveis-design.md` já registrava. F2 alimenta o mecanismo com **uma**
  exigência real; não o torna esperto.
- Não toca o modelo de escopo `segment`/`session`/`journey`. Ver a desambiguação no topo.
