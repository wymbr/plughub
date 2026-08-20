# PlugHub — Decompor e orquestrar: o caminho para o payback que a IA prometeu

> Texto de visão e divulgação · Revisão 3 (síntese; payback como destino) · 2026-06-16
> Backbone de engenharia, payoff de negócio.

> ⚠️ **Correção de 2026-08-19 — medido.** Em §4, a afirmação de que os canais com o cliente incluem **voz** e
> **WebRTC** como parte **nativa** da plataforma é **falsa**. `VoiceAdapter.handle_inbound` chama cinco métodos
> que não existem em `packages/channel-gateway` (`_open_session`, `_route_inbound`, `_publish_inbound`,
> `_normalize_text`, `_normalize_menu_result` — `adapters/voice.py:236,247,433,558,565`), mockados em
> `tests/test_voice_adapter.py:116-121`: em runtime real dá `AttributeError` antes de publicar em
> `conversations.inbound`, e não há uma única sessão de voz no ambiente; `collect`/menu por voz está morto
> (`voice.py:624-629,657`). Em WebRTC só a sinalização roda — plano de mídia nunca provisionado (zero LiveKit em
> compose, SDK fora de `packages/channel-gateway/pyproject.toml:6-23`, `_dev_mode` placebo em
> `webrtc_provider.py:167`). **Nenhum dos dois canais de áudio funciona hoje.** **Material de venda: não usar
> estes trechos em proposta comercial** até o arco fechar V-F2. Ver
> [`adr-voice-media-plane.md`](../adr/adr-voice-media-plane.md) (proposto, V-F0..V-F5).

A queixa é real: muitas empresas não acham payback na IA. A causa raramente é falta de um modelo maior — é a arquitetura. A nossa proposta vai na linha de **decompor agentes e amarrá-los em workflow** — atacando as duas alavancas do retorno, **custo e resultado**, para chegar ao payback. O PlugHub foi concebido sobre essas premissas.

---

## 1. Por que a IA no atendimento começou onde começou

A primeira geração foi moldada por uma restrição real — contexto curto, modelos menos capazes. Dadas elas, duas estratégias eram as racionais, e funcionaram: **substituir sistemas e plataformas pontuais** (um bot no lugar da URA, um classificador, um sumarizador) e **agentes especialistas de escopo estreito**. Quanto mais bounded o problema, melhor o resultado, porque cabia na janela e o modelo não se perdia. Não foi erro — foi a arquitetura possível. Mas a restrição que a justificava afrouxou.

## 2. Por que um modelo ou contexto maior não entrega o payback

A tentação é jogar o processo inteiro num contexto gigante e deixar um modelo resolver tudo. Três coisas não mudaram e derrubam essa ideia:

- **Contexto grande degrada** — diluição, *lost-in-the-middle*, *context rot*. Um contexto específico e menor é mais preciso e mais estável.
- **Ciclo longo é problema de estado, não de tamanho** — um processo de dias, que atravessa sessões e espera terceiros, não cabe em contexto nenhum: precisa de persistência e retomada, não de mais tokens.
- **Custo** — um contexto máximo é reprocessado por inteiro a cada chamada; você paga o todo mesmo quando a etapa usa uma fração.

Modelo maior é necessário, não suficiente. Ele habilita o próximo passo; não é o próximo passo.

## 3. O salto: decompor agentes + workflow

Aplicar ao processo a estratégia que a engenharia sempre usou contra complexidade — decompor — e coordenar as partes: um **orquestrador** que não sabe tudo, mas convoca quem sabe cada etapa; **especialistas reaproveitados** (humanos e IA, próprios e externos, inclusive os já construídos na primeira fase); e um **workflow com estado persistente** que costura as etapas ao longo do tempo, sobrevivendo a esperas, quedas e troca de canal. Isso move as duas alavancas do payback de uma vez:

**Custo cai por construção.** Cada etapa carrega só o contexto que usa, sem reenviar o histórico a cada turno — o custo para de crescer com o acumulado. Cada etapa usa o modelo certo: barato e rápido para classificar, extrair e rotear; caro só nas poucas que exigem raciocínio. E as etapas que nem precisam de LLM — uma chamada determinística, uma regra, um passo de workflow — não pagam inferência.

**Ganho sobe, porque decompor é a hora de redesenhar.** Boa parte do payback some quando se automatiza o processo humano como ele é — desenhado em torno de limitações humanas: conferências porque pessoas erram, filas porque têm jornada, aprovações em série porque a confiança era manual, silos porque cada área via um pedaço. Botar IA para executar esse mesmo desenho faz mais rápido o que talvez nem precisasse existir. Ao decompor, cada etapa fica visível para a pergunta certa — isso é raciocínio, regra, sistema, ou não é trabalho nenhum? O payback estava no processo que ficou mais curto, não na inferência mais barata.

Redesenhar, aqui, não conflita com reaproveitar — são níveis diferentes. O redesenho acontece na **composição**: o workflow que reordena, paraleliza ou elimina *etapas*. Os **especialistas** seguem como blocos reusáveis; recompõem-se numa ordem nova em vez de reescrever um monólito. É por isso que decompor torna o redesenho barato. E os poucos blocos que não sobrevivem ao corte costumam ser os que só existiam para compensar uma limitação humana — *cow-paths* que valia eliminar, não reuso perdido.

## 4. O PlugHub foi concebido para isto

Não é um modelo nem um agente — e não é só um orquestrador. É a **plataforma onde o processo roda de ponta a ponta**: orquestrar costura as etapas, e a plataforma dá a elas tudo o que falta para virar operação real. Cada premissa acima é um primitivo — e há mais:

- **Orquestrador + especialistas por pool**, convocados declarativamente, com o orquestrador guardando só a memória de trabalho — não o contexto de cada especialista.
- **Humano e IA como a mesma interface de especialista** — roteados e convocados igualmente; é o que torna a automação gradual.
- **Workflow de ciclo longo com estado persistente** — Skill Flow decompõe em passos; `suspend`/`resume` sustenta dias; o ContextStore isola contexto por etapa.
- **Modelo certo por etapa** — o AI Gateway aplica o perfil adequado (rápido/barato vs. raciocínio) por passo e troca de provedor por configuração.
- **Reuso e portabilidade** — traga o especialista que já existe, inclusive externo; integração só por MCP, auditada; sem lock-in.
- **A operação ao redor, nativa** — os canais com o cliente (WhatsApp, webchat, e-mail, SMS; **voz e WebRTC são projeto, não entrega** — ver banner), a medição de qualidade e o compliance (mascaramento, auditoria) são parte da mesma plataforma, não integrações avulsas. É o que torna o processo decomposto **operável, medível e auditável** de ponta a ponta — sem o que a decomposição seria só teoria.

## 5. O resultado: custo menor, ganho maior, payback

O retorno melhora pelas duas pontas — decompor aperta o denominador (menos tokens, modelo certo por etapa, inferência só onde agrega) e levanta o numerador (o valor do processo inteiro, redesenhado, não da resposta isolada). E como cada etapa é bounded, é mensurável e auditável: a confiança que permite, enfim, entregar um processo real à automação — com o humano como um especialista a mais, que começa no comando e recua na velocidade que a avaliação liberar.

Esse é o payback que a IA prometeu: não vem de um cérebro maior, e sim do **processo decomposto, orquestrado e redesenhado** — exatamente para o que o PlugHub foi feito.

---

> Nota de uso interno (não publicar literalmente): linha de raciocínio limites → decompor agentes + workflow → custo↓ e ganho↑ → payback; redesenho de processo entra como causa do ganho (§3, tom medido — "boa parte do payback", não "o motivo único"); confiança/humano como consequência (§5). Para o site: suavizar nomes de primitivos (§4) e traduzir §5 para linguagem de negócio. Detalhe de capacidades em [`value-proposition.md`](value-proposition.md); empacotamento/mercado e o braço de serviço em [`fin-intercom-comparativo-e-cunha-resolucao-auditavel.md`](fin-intercom-comparativo-e-cunha-resolucao-auditavel.md).
