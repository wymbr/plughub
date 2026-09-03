# Business in Any Media — Arquitetura-Alvo (Modelo de Três Níveis + Framework de Loja)

> **Conceito-guarda-chuva:** *Business in Any Media — nunca mais perca um negócio por causa de canal.*
> **Status:** proposta / arquitetura-alvo. Mistura o que já está implementado com o que precisa ser construído (sinalizado explicitamente).
> **Relação com o resto:** este documento é a base para (1) reposicionar o `plughub-descritivo-tecnico-funcional.md` após a eliminação da entidade Journey (Arc 19 Fase F) e (2) especificar o framework de loja.
> **Diagrama:** `business-in-any-media-3-niveis.svg`.
> **Data:** Junho 2026.

> ⚠️ **Correção de 2026-08-19 — medido.** A classificação do nível (c) como **"Implementado — adapters de
> webchat, WhatsApp, SMS, e-mail, voz, WebRTC"** é **falsa quanto a voz e WebRTC**, e o `dialer` pressuposto no
> §5 não existe. `VoiceAdapter.handle_inbound` chama cinco métodos inexistentes em `packages/channel-gateway`
> (`_open_session`, `_route_inbound`, `_publish_inbound`, `_normalize_text`, `_normalize_menu_result` —
> `adapters/voice.py:236,247,433,558,565`), mockados em `tests/test_voice_adapter.py:116-121`: `AttributeError`
> em runtime real, sem uma única sessão de voz no ambiente; `collect`/menu por voz está morto
> (`voice.py:624-629,657`), sobrando no máximo DTMF. Em WebRTC só a sinalização roda — plano de mídia nunca
> provisionado (zero LiveKit em compose, SDK fora de `packages/channel-gateway/pyproject.toml:6-23`, `_dev_mode`
> placebo em `webrtc_provider.py:167`). **Nenhum dos dois canais de áudio funciona hoje**, e o discador está
> **bloqueado por falta de plano de mídia**. O modelo de três níveis e os contratos abaixo seguem válidos como
> arquitetura-alvo. Ver [`adr-voice-media-plane.md`](../adr/adr-voice-media-plane.md) (proposto, V-F0..V-F5).

---

## 1. Tese

A unidade de valor do PlugHub deixa de ser *a interação* (modelo CCaaS, interaction-centric) e passa a ser *o processo de negócio* (process-centric). O contato/sessão vira um **episódio** dentro do processo — não a unidade de medida. Isso permite remapear não só o fluxo de atendimento, mas o **fluxo de negócio inteiro** (oferta → cesta → pagamento → entrega → troca, ou aprovação → coleta → execução, etc.).

A entidade `Journey` foi eliminada no Arc 19 Fase F (2026-05-28) por ser redundante: era "conceitualmente apenas um workflow Tier-1 que invoca outros workflows via step `task`", e o *session trace* do modelo unificado dá a hierarquia sem entidade separada. O que substitui a Journey é a combinação **workflow Tier-1 + session trace + suspend/resume**, organizada nos três níveis abaixo.

O slogan operacional disso é: **o negócio não pertence a um canal**. Ele nasce, dorme, troca de canal e se completa onde o cliente estiver — e nenhuma transição de canal pode fazer o negócio se perder.

---

## 2. O modelo de três níveis

> ⚠️ **A pendência do nível (b) foi RESPONDIDA em 2026-09-03, e a resposta inverte a
> intenção** — ver
> [`adr-agent-flow-single-authored-level.md`](../adr/adr-agent-flow-single-authored-level.md).
> A tabela abaixo classifica (b) como *"parcial / a consolidar — as peças existem
> dispersas, mas não como camada explícita e reusável"*. Medido: as peças dispersas
> **já são primitivos de plataforma** (`customer_resolve`, `pending_workflow_get`,
> `workflow_resume`, `otp_*`, `select_channel`), e consolidá-las numa camada de FLUXO
> criaria contêiner para o que já tem casa. Decisão: **(b) não vira camada**; o tenant
> autora só (a), e (c) se parte entre canal (mecânica) e `DialogForm` (roteiro).
>
> A **regra de ouro** logo abaixo da tabela continua valendo palavra por palavra — ela
> descreve uma separação de responsabilidades, não uma contagem de artefatos.

| Nível | Responsabilidade | Perfil / primitivas | Estado hoje |
|---|---|---|---|
| **(a) Fluxo negocial** | A lógica de negócio, **totalmente abstraída de canal**. Não importa por onde o cliente acessou. | Perfil `workflow` (pool `webhook`). Steps `task/choice/catch/escalate/complete/invoke/reason/suspend/collect/receive`. **Proibido** `menu/notify/begin/end_transaction`. `session_id` persistente; `suspend`/`resume` via Redis TTL. | **Implementado** — e o perfil `workflow` do Arc 19 *força por contrato* que (a) não toque canal. |
| **(b) Fluxo de acesso aos canais** | Conduz episódios de interação nos canais e **concilia as trocas de canal**. É a ponte entre o negócio abstrato e o canal concreto. | Perfil `agent` (fino), apoiado por serviço de negociação de mídia (Arc 16). | **Parcial / a consolidar** — as peças existem dispersas (`collect`, negociação Arc 16, mapas por canal), mas não como camada explícita e reusável. |
| **(c) Agente de I/O no canal** | A interação concreta naquele canal: render nativo, captura de input, mídia. | Perfil `agent` (`menu/notify/masked input`) + Channel Adapter (WS / webhook / bot-leg, STT/TTS, upload). | **Parcial** — adapters de webchat, WhatsApp, SMS e e-mail implementados; **voz e WebRTC são projeto** (não funcionam — ver banner no topo). |

A regra de ouro que mantém os níveis limpos: **(a) nunca sabe por onde fala; (c) nunca conhece o negócio; (b) traduz entre os dois e é o único que entende "canal".**

---

## 3. Contratos entre as camadas

Os níveis só se sustentam se os contratos entre eles forem estritos. Vazamento de canal em (a) é o anti-padrão a evitar.

### 3.1 (a) ↔ (b) — contrato abstrato de interação

`(a)` pede a `(b)` algo como *"preciso de uma interação com este interlocutor para obter X, com estas restrições e este prazo"* — **sem mencionar canal**. `(b)` devolve um **resultado canônico** (status + payload), também sem vazar canal.

Hoje isso já existe na forma do step **`collect`**: o workflow suspende, `(b)` cria uma sessão-filho com canal negociado por capabilities, um agente channel-aware atende e retorna o resultado, e *"o workflow nunca conhece o canal usado"*. O step `task` (delegação a um sub-fluxo perfil-`agent`) é o outro caminho dessa costura.

Forma sugerida do contrato (a consolidar como tipo explícito):

```
InteractionRequest  { party_ref, intent, payload, constraints, deadline, visibility }
InteractionResult   { status, payload, completed_at }      # nenhum campo de canal
```

### 3.2 (b) ↔ (c) — contrato de episódio

Dado um interlocutor e um canal (escolhido por `(b)`), `(c)` conduz o turno-a-turno e devolve o resultado canônico. `(c)` é quem conhece `menu/notify/masked input` e o adapter; `(b)` só orquestra **qual** episódio acontece e **onde**.

### 3.3 (a) → sistemas — contrato MCP

`(a)` integra sistemas de negócio (catálogo, estoque, pagamento, ERP, transportadora, CRM) **exclusivamente via MCP**, com **interception guard** (permissão + injeção + auditoria) obrigatório por chamada. Nenhum nível acessa sistema de negócio fora do MCP.

### 3.4 Serviços compartilhados (transversais, de ninguém)

Routing Engine, AI Gateway (multi-conta, BYO LLM), ContextStore, Masking/LGPD, Quality/Bancada e Analytics são consumidos por todos os níveis e pertencem a nenhum. O **especialista humano** entra como participante (`specialist`/`supervisor`) na mesma sessão, em qualquer nível — humano e IA simétricos.

---

## 4. O que precisa ser construído — foco no nível (b)

O nível (b) é o que falta promover a primeira classe. As peças estão dispersas; o trabalho é consolidá-las e fechar o gap aberto pela remoção da Journey.

1. **Channel Access Workflow como padrão reusável** — uma camada/biblioteca nomeada de fluxos perfil-`agent` que orquestram episódios de interação, em vez do arranjo implícito atual (`collect` + negociação + mapas por canal espalhados).

2. **Resolvedor identidade ↔ fluxo de negócio pendente (retomada cross-canal)** — *este é o renascimento, no lugar certo, do que a Journey fazia.* O Arc 19 Fase F removeu o `inbound_journey_resume` e o lookup por `customer_id`. Reconstruir como responsabilidade de (b): ao chegar um inbound em qualquer canal, resolver a identidade do interlocutor → localizar o fluxo (a) suspenso → retomar com novo segmento (via `resume_token` ou índice por identidade). É o que entrega "começa no WhatsApp, paga no site, recebe status no SMS" como **propriedade do sistema**, não modelagem manual por cliente.

3. **Decisão de seleção/troca de canal como step de (b)** — a *decisão* de trocar de canal é lógica de fluxo em (b); a *mecânica* de negociação/switch permanece serviço de infra (Arc 16) que (b) consome. Manter (b) **fino** evita que ele engorde e vire um mini-CCaaS dentro de cada processo.

4. **Guardrails de não-vazamento** — validação de que (a) nunca embute suposição de canal e que (b) sempre devolve resultado canônico (estende o guard de perfil do Arc 19 ao contrato de interação).

5. **Vocabulário de interação de comércio (fronteira b/c)** — primitivas *product card, carrossel/catálogo, cesta, checkout, status de pedido* como extensões do `menu`/`notify`, definidas uma vez no fluxo e renderizadas no formato nativo mais rico de cada canal (WhatsApp interactive, widget webchat, TTS+DTMF na voz, lista numerada no SMS).

---

## 5. Aplicação ao framework de loja

A loja é o caso de uso emblemático do "business in any media".

- **A espinha transacional é o fluxo (a).** Oferta → cesta → pagamento → entrega → troca é um workflow perfil-`workflow`, abstraído de canal. Esperas longas (confirmação de pagamento, status de entrega, decisão de troca) usam `suspend`/`collect`. Pagamento usa `begin/end_transaction` (input mascarado nunca tocando Redis/stream/log) — mas como `begin/end_transaction` é proibido no perfil `workflow`, a coleta de pagamento é delegada a (b)/(c) via o contrato de interação. **Isto é composição, não monólito:** (a) abstrato delega a (b) interativo.
- **(b) faz "falar com o comprador onde ele estiver"** e concilia trocas de canal; **(c) renderiza** os cards/cesta/checkout por canal.
- **Navegação/catálogo permanece leitura pura** — alta frequência, baixa latência, read-heavy. Não rotear cada page view por sessão/agente (seria caro e bate com o billing por capacidade). Sessões nascem nos estágios **transacionais** (cesta em diante).
- **Outbound gerador de receita** via `collect` + dialer: recuperação de carrinho, recompra, win-back. *(**Projeto quanto ao dialer**: ele não existe e está bloqueado por falta de plano de mídia — banner no topo. Pelos canais de texto o `collect` outbound é real.)*
- **Humano só na exceção** (ticket alto, fraude, troca complexa): entra como especialista na mesma sessão, sem handoff visível.
- **Prova da substituição IA → humano:** a Bancada de Agentes já compara **humano × IA** e correlaciona com deploy epochs; o Cross-cut (resolução × qualidade × NPS) remapeia para **conversão × qualidade × satisfação**. Você demonstra com dado que o "vendedor IA v3" converte melhor que o v2 e que o humano.

### 5.1 Identidade do comprador

O modelo de sessão/contato assume identidade de interlocutor. Comprador anônimo navegando → identificado no checkout é um passo de modelagem: definir **quando a sessão nasce** e como (b) vincula a identidade ao fluxo pendente (item 4.2).

---

## 6. O nicho de mercado (por que não brigamos com CCaaS)

- **CCaaS = suporte:** reativo, centro de custo, comprador Head de CX, métrica AHT/FCR/CSAT, concorrência sangrenta (Genesys/NICE/Five9/Talkdesk).
- **Business in Any Media = comércio/operação transacional:** proativo, centro de receita, comprador Head de Digital Commerce / Growth / Vendas, métrica conversão/GMV/recuperação/custo-por-pedido. Outro comprador, outro orçamento, outros concorrentes.

**Onde a substituição IA → humano fecha a conta:** trabalho hoje feito por humanos em conversas transacionais repetitivas e roteirizáveis, em escala, onde (1) mão de obra é o custo dominante, (2) a tarefa é limitada, (3) throughput vira valor (mais conversas simultâneas = mais receita) e (4) qualidade é mensurável. Caso emblemático: **operações de venda via WhatsApp** (enorme no Brasil/LatAm). Verticais quentes e humano-intensivas: varejo/D2C no WhatsApp, distribuição/B2B com recompra, food/farmácia, venda de produto financeiro (seguro, crédito, consórcio), turismo, imobiliário, e cobrança/negociação outbound (regulada — onde a interceptação de compliance é vantagem real).

**Vs. os incumbentes de comércio conversacional (Blip/Take, Yalo, Zenvia):** são bot-builders colados na API do WhatsApp; "IA" = NLU + fluxos; handoff humano = mesa separada. O PlugHub entrega o que eles estruturalmente não têm: humano + IA simétricos na mesma sessão; prova empírica da substituição (Bancada + deploy epochs); pagamento/venda regulada com compliance nativo; billing por capacidade; outbound no mesmo motor; agnosticismo de LLM/framework; e — o ponto estrutural — o processo (cesta → pagar → entregar → trocar) como **máquina de estado com canal**, não um bot de interação.

**Risco a encarar:** é fronteira em movimento rápido (WhatsApp empurra commerce nativo; trilhos de "agentic commerce" + pagamento emergindo). O fosso não é o bot de chat — é a orquestração de processo + simetria humano/IA + compliance + agnosticismo + prova de qualidade. O pitch não pode virar "mais um bot de WhatsApp".

---

## 7. Impacto no descritivo técnico-funcional (a revisar)

1. **Remover o enquadramento "Journey como primitiva" (§1 e §4 do descritivo)** — substituir por "workflow Tier-1 + session trace + suspend/resume" e pelo modelo de três níveis deste documento. A entidade Journey não existe desde Arc 19 Fase F.
2. **Atualizar a matriz competitiva** — trocar a linha "Journey multi-contato como primitiva" por "processo multi-etapa/multi-contato como workflow + trace unificado, agnóstico de canal".
3. **Adicionar o caso de uso "comércio conversacional / business in any media"** ao lado de "contact center" e "automação de processo" no perfil de cliente competitivo (§21).
4. **Registrar a dívida de documentação** — o `CLAUDE.md` ainda descreve Journey nas seções Arc 10/16/17; essas seções estão defasadas e devem ser marcadas como aposentadas (já há nota no CHANGELOG Arc 19 Fase F).

---

## 8. Limites e riscos (honestidade de engenharia)

- **Composição obrigatória:** o fluxo de negócio (a) não pode fazer interação rica nem input mascarado; precisa delegar a (b)/(c). É o padrão correto, mas exige disciplina de modelagem.
- **(b) ainda não é primeira classe:** hoje é híbrido implícito; o resolvedor identidade↔fluxo-pendente é um build (pequeno, mas necessário).
- **Identidade de browse anônimo:** definir o nascimento da sessão no funil de comércio.
- **Fronteira competitiva veloz:** ver §6.
