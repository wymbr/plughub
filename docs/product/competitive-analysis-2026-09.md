# PlugHub — Análise Competitiva por Segmento (Setembro 2026)

> Estado: síntese de produto · Data: 2026-09-24 · Supersede
> [`competitive-analysis-2026-07.md`](competitive-analysis-2026-07.md) (27/07), que fica como snapshot datado.
> Complementa [`mercado-brasil-2026.md`](mercado-brasil-2026.md) e
> [`mercado-portugal-europa-2026.md`](mercado-portugal-europa-2026.md) (jun/2026), cujo dimensionamento de
> mercado **não** foi refeito aqui.

## Como ler este documento

Mesmo método do snapshot de julho, com três reforços.

1. **Assimetria de maturidade continua sendo o gap dominante.** Os concorrentes são produtos em produção
   com milhares de clientes. O PlugHub, medido no repositório em 24/09, não tem piloto, sponsor, deploy de
   produção nem certificação emitida (`material-sponsor-operacao-piloto.md` — *"não temos certificações
   emitidas"*; a Rodada 2 de `operacao-piloto-e-rodada-2.md` não começou). Toda linha abaixo compara
   **capacidade**; nenhuma compara prova em campo.
2. **Claims do PlugHub foram medidas no código e no ledger** (`pending.md`/`done.md`/`CLAUDE.md`), não na
   prosa de produto. Rótulos: **[E]** entregue e validado · **[P]** parcial · **[N]** verdadeiro como
   arquitetura, não como produto · **[ausente]** · **[rebaixado]** quando julho afirmava mais do que o
   código sustenta.
3. **Dados de mercado são de pesquisa web em 24/09/2026**, com fonte e data na § Fontes. Números de
   blogs e agregadores estão marcados *(est.)*. Preço enterprise é negociado; trate como ordem de grandeza.

---

## 1. Resumo executivo

- **O que mudou no mercado (jul→set):** o vocabulário da tese do PlugHub virou discurso dos grandes.
  Genesys (Xperience, 02/09) anunciou *AI Control Plane* e um *Orchestrator* de IA+humanos+sistemas; NiCE
  fundiu Cognigy no núcleo do CXone com *Guardian AI*; Salesforce comprou a Fin (Intercom), concluída em
  10/09; Zendesk comprou a Forethought; AWS, Microsoft, Kong e Palo Alto vendem **política e auditoria
  por chamada de ferramenta** como produto de prateleira.
- **O que mudou no PlugHub:** voz saiu do papel — WebRTC e SIP entrante validados com gente, celular real
  atendido pela Twilio em 23/09, gravação auditada, PIN pelo teclado sob pausa de mídia. Em compensação, a
  **governança de MCP, que julho chamava de diferencial nº 1, foi medida como não imposta** em duas das três
  bordas, e a terceira não tem destino configurado (§ 3).
- **O que ainda sustenta posição:** (a) humano e IA **ativos na mesma sessão**, com papel e visibilidade
  por participante — os concorrentes seguem em *revezamento* (o Google documenta que a IA **silencia** no
  handoff); (b) motor declarativo único para inbound/outbound/workflow/hook; (c) coleta de dado sensível
  com a IA na sala e o humano fora dela. Nenhum dos três tem prova em campo.
- **Recomendação central:** parar de vender governança de MCP como diferencial até a borda única existir
  (CAP-07/08/10 + ADR proposto), e concentrar a mensagem na **sala compartilhada auditável**, que é o que
  nenhum segmento copiou.

---

## 2. Correções ao snapshot de julho

| Afirmação de julho | Estado medido em set/2026 |
|---|---|
| EU AI Act: obrigações de alto risco **enforçáveis em 02/08/2026** | **Falso hoje.** O *Digital Omnibus* (Regulation (EU) 2026/1744, em vigor 27/07/2026) adiou alto risco do Anexo III para **02/12/2027** e Anexo I para **02/08/2028**. O que vale desde 02/08/2026 é a **transparência do art. 50**: chatbot tem de se declarar IA — inclusive na voz. |
| Governança MCP por invariante — **[E] diferencial real** | **[rebaixado]**. Ver § 3. |
| Voz nativa — **[E] Arc 15** | Era falso em julho (nota de 19/08); **hoje [P] forte**, validado com gente (§ 4.1). |
| BYO framework — **[E]** agnóstico + proxy do SDK | **[rebaixado]** por decisão de produto (13/08): runtime importado saiu do roadmap; o substituto (A2A servidor) é ADR **proposto**, sem código. |
| MCP "~97 mi downloads/mês", A2A e MCP sob a mesma fundação | MCP passou de **110 mi/mês**, spec **2026-07-28** final (núcleo sem estado, autorização OAuth/OIDC). **A2A é projeto próprio da Linux Foundation**, não da AAIF. |
| Intercom Fin como concorrente independente | Renomeada **Fin** (mai/2026) e **comprada pela Salesforce** (US$ 3,6 bi, concluída 10/09). |
| LangGraph US$ 0,001/nó; CrewAI Pro US$ 25 | Preços saíram das páginas oficiais: LangSmith cobra deploy por recurso (LCU/LSU); CrewAI ficou só Basic grátis + Enterprise sob consulta. |
| n8n: avaliação US$ 2,5 bi, ARR US$ 40 mi | SAP investiu (mai/2026) a **US$ 5,2 bi**; ARR ~US$ 100 mi *(est.)*; canvas embutido no Joule Studio. |

---

## 3. O diferencial que caiu: governança de MCP

Julho dizia *"McpInterceptor + proxy sidecar, guard < 1 ms, audit Kafka obrigatório"*. Medido:

- o `McpInterceptor` in-process **nunca é instanciado** — o caminho real de tool do agente nativo
  (`skill-flow-service.mcpCall`) faz `fetch` cru, sem permissão, guard nem audit (`CLAUDE.md` § MCP
  Interception);
- o sidecar de agente externo só existe se o operador subir o processo, e o runtime importado saiu do
  roadmap;
- a única borda em vigor (`external-mcp` → `invoke`) tem **zero** `MCP_SERVER_*_URL` configurados
  (CAP-07), o `mcp_server` declarado não é validado (CAP-08) e **0 de 44 skills** declaram `tools[]`
  (CAP-06, opt-in);
- CAP-10: 50 de 74 tools em dívida de credencial. O ADR de borda única
  ([`adr-mcp-interception-single-border.md`](../adr/adr-mcp-interception-single-border.md)) está **proposto**.

E o mercado andou na direção oposta: **AWS AgentCore Policy** (GA mar/2026, Cedar, imposta no gateway fora
do código do agente, políticas temporais em ago), **Microsoft Agent 365 + Entra Agent ID** (GA 01/05,
US$ 15/usuário/mês), **Kong AI Gateway 2.0** (GA 01/09, MCP + A2A), **Salesforce** com nota de risco por
conexão MCP (Dreamforce, set), **Palo Alto/Portkey** e **Snowflake/Natoma** por aquisição. *Permissão +
injection guard + audit por chamada* é hoje categoria comprada, não diferencial.

**O que ainda é defensável aqui** não é o guard, é a **ligação do audit ao modelo de sessão**: saber qual
participante, em qual segmento, sob qual deploy de pool chamou a tool. Isso só vale quando a borda existir.

---

## 4. PlugHub hoje — capacidade medida

| Capacidade | Julho | Hoje | Evidência |
|---|---|---|---|
| Humano + IA na mesma sessão (co-presença) | [E] | **[E] reforçado** | MEN-01/02/05/06/08: só quem conduz menciona e escala, com identidade assinada, nos dois caminhos (MCP e WS do Console) |
| Visibilidade por participante × campo × papel | [E] | **[E] com ressalvas** | NIV-07/VOZ-37 (PIN sob pausa de mídia). Ressalvas abertas: ALW-15, CTX-09, CAP-14 |
| Motor único (inbound/outbound/workflow/hook) | [E] | **[E]** | CTR-01 (perfil de step recusado no deploy), CTR-06 (delegação aninhada), PID-16 (promote em lote) |
| Voz (WebRTC + SIP + gravação + supervisão) | [E]→falso | **[P] forte** | VOZ-01/02/04/05/06/31/32/35/36/37/38 |
| Outbound de voz / dialer com compliance | [P] | **[ausente], bloqueado** | OUT-04 ← VOZ-33 (chamada sainte) |
| Governança MCP por chamada | [E] | **[rebaixado]** | § 3 |
| BYO LLM | [E] | **[E]** | catálogo `llm_accounts`, `AccountSelector` |
| BYO framework / interoperabilidade A2A | [E] | **[rebaixado]** | `adr-a2a-server-binding.md` proposto, sem código |
| Journey multi-contato | [P] | **[P]** | JRN-04 (drill N3), OUT-03 (outbound ↔ journey) |
| Replay + avaliação de qualidade | [E]/[P] | **[P]** | RPL-01 (até 16/09 o avaliador não via o texto do cliente); RPL-02 (histórico) |
| Resolução auditável | [P] | **[P], com defeito** | SFE-02: `issue_status` em 0 de 3 143 segmentos de IA; SFE-01: `outcome_from` inválido vira `resolved` |
| Painel de qualidade para o comprador | ausente | **[ausente]** | — |
| Billing por capacidade | [N] | **[N]** | PRC-01, USG-01: sem fatura real; 4 dimensões sem chamador |
| Audit LGPD / SAR / erasure | [P] | **[P]** | AUD-01..05 |
| Identidade / retomada cross-canal | [P] | **[P] avançado** | PID-*, IDN-06..14; merge e `external_refs` abertos (IDN-01/03) |
| Aprovação humana como passo | [P] | **[E] v1** | PUL-02, APR-10/11; anexos, quatro-olhos e SLA abertos |
| Orquestrador com navegação em árvore | — | **[E] novo** | ORQ-11..19 (classificador, esclarecimento, lente de qualidade) |
| Escala, certificação, referência | ausente | **[ausente]** | — |

### 4.1 Voz, em detalhe (a maior mudança)

Entregue e validado com pessoas: SFU LiveKit + TURN; contato ponta a ponta no browser; bot leg STT/TTS
self-hosted com barge-in; SIP entrante no mesmo modelo de sala; DTMF (RFC 4733); celular real pelo tronco
Twilio; toda chamada SIP atendida em ~0,3 s; gravação por política do pool com escuta e exportação
auditadas; supervisor oculto; chamada como meio dentro do webchat, com a IA falando e ouvindo.

Ainda falta, e é o que um comprador de contact center pergunta primeiro: **chamada sainte** (VOZ-33) e
portanto **dialer**; mídia para browser em outra máquina; IA conversacional abaixo de 1,5 s (VOZ-13);
N réplicas sem IA muda (WCH-12); aviso de IA por fala (art. 50 da UE).

---

## 5. Segmento a segmento

### 5.1 CCaaS incumbentes (Genesys, NiCE, Five9, Talkdesk, Amazon Connect, Microsoft, Zoom, Twilio)

**Movimento do trimestre:** todos agora vendem uma **camada de orquestração + memória + governança** sobre
o contact center.

- **Genesys** — comprou a Pinkfish (30/06; ~25 mil tools MCP, nativo até jan/2027). Na Xperience (02/09):
  *Contextual Intelligence* e *AI Control Plane* (GA), *Orchestrator* para IA+humanos (fev–abr/2027), A2A
  com Agentforce e ServiceNow. Cloud ARR ~US$ 2,9 bi, AI ARR > US$ 400 mi. Preço: 1,2 AI token por
  interação agêntica.
- **NiCE** — Cognigy nativa no CXone com *Guardian AI* e *Agentic Engagement Plane*; *Workforce Empowerment
  Suite* (WFM/QM/coaching para humanos **e** IA). AI ARR US$ 362 mi (+52%). Maior deal: HMRC.
- **Five9** — Voice AI Agents como substituto de URA; deal de US$ 100 mi em modelo *revenue-commit* em que
  o cliente troca assento humano por agente de IA dentro do mesmo compromisso. Margem caindo (63→61%).
- **Talkdesk** — *Agent Builder* (jun), *CXA Operations Center* (mar: gerir humanos e IA como uma força de
  trabalho), agentes outbound proativos (mai), compra via crédito Azure (set).
- **Amazon Connect** — voz agêntica ~US$ 0,038/min *(est.)*; cobra **colaboração com agentes de IA de
  terceiros** (US$ 0,001/msg, US$ 0,01/min).
- **Microsoft D365 Contact Center** — ferramentas MCP de WFM; fim dos *release waves*.
- **Zoom** — *Agent Performance Suite*; líder no IDC MarketScape Agentic CCaaS (material do fornecedor).
- **Analistas:** Gartner MQ CCaaS 2026 não encontrado (2025: NiCE, Genesys, AWS, Five9, Talkdesk).

**PlugHub vs. segmento:**
- *Perde* em tudo que é operação: telefonia madura, dialer, WFM, escala, certificação, ecossistema.
- *Empata no discurso* de orquestração IA+humano — que agora é deles.
- *Ganha na estrutura*: os incumbentes somam a IA **sobre** um modelo de interação que continua sendo
  agente→fila→agente. No PlugHub a conferência é o modelo base. A Genesys só entrega o *Orchestrator* em 2027.
  A janela existe, mas é de cerca de 6 a 12 meses e depende de prova.

### 5.2 Agentes de IA por resultado (Fin, Sierra, Decagon, Zendesk, Ada, Parloa, PolyAI, Cresta, Crescendo, OpenAI)

**Movimento do trimestre:** **consolidação por suíte** e **governança de publicação** como table-stakes.

- **Fin** → Salesforce (10/09). Preço US$ 0,99 por *outcome*, que agora inclui handoff por *Procedure* e
  roteamento — "outcome" está se afastando de "resolution". *Fin Operator*: IA que depura o agente com
  aprovação humana tipo pull request.
- **Sierra** — US$ 15,8 bi (mai); agentes *Horizon* de longo prazo; **release governance** (20/08:
  simulação obrigatória, revisor, canário, rollback por snapshot imutável) — é o que mais se parece com o
  deploy por slot de pool do PlugHub.
- **Decagon** — US$ 4,5 bi; ARR ~US$ 100 mi *(est.)*; **abriu escritório em São Paulo em 15/09** (Mercado
  Libre, NG.Cash). **É concorrente direto no mercado de entrada do PlugHub.**
- **Zendesk** — comprou Forethought; cobra só por resolução automatizada (~US$ 1,50–2,00 *(est.)*),
  "validada por modelo de avaliação independente" — que é da própria Zendesk; Quality Score em 100% das
  interações, humanas e de IA.
- **OpenAI Presence** (22/07) — plataforma de voz e chat com aprovações e evals, implantada por
  engenheiros da OpenAI; cliente BBVA México.
- **Cresta** — o único que se posiciona explicitamente em *humano + IA*, pelo lado do assistente ao atendente.
- **Contestação das taxas de resolução:** benchmark Comm100 dá 44,8% contra ~70% anunciados *(est.)*;
  nenhuma verificação de terceiro existe no segmento.

**PlugHub vs. segmento:** a tese de julho — *não brigar no nível 1* — ficou mais certa: o nível 1 agora é
recurso de CRM (Salesforce, Zendesk). A cunha **"resolução auditável"** continua sem dono, **mas o PlugHub
também não a entrega hoje**: SFE-01/02 mostram que o próprio dado de desfecho de IA é plausível-e-errado.
Consertar isso é pré-requisito para qualquer conversa neste segmento.

### 5.3 Plataformas enterprise de agentes e hyperscalers (Salesforce, Google, Microsoft, AWS, ServiceNow, OpenAI)

- **Salesforce** (Dreamforce 15–17/09) — *AIforce* (humanos e agentes com dados e governança
  compartilhados), nota de risco por conexão MCP, edições reduzidas a três níveis com Agentforce incluso;
  preço vigente US$ 2/conversa ou Flex Credits.
- **Google** — Gemini Enterprise Agent Platform (abr); no CX Agent Studio o handoff **silencia a IA** e
  cria o humano: é revezamento, não co-presença.
- **Microsoft** — Agent 365 GA (US$ 15/usuário), M365 E7 a US$ 99; identidade de agente no Entra para todo
  agente do Copilot Studio.
- **AWS** — AgentCore Policy, Guardrails dentro de política, políticas temporais open source, OAuth gerenciado.
- **ServiceNow** — AI Control Tower GA (ago): descoberta de agentes multi-cloud, observabilidade (Traceloop),
  desligar agente fora de permissão, risco alinhado a EU AI Act/NIST.
- **OpenAI** — Frontier (fev); **Agent Builder e Evals descontinuados em 30/11/2026**, ~13 meses após o
  lançamento.

**PlugHub vs. segmento:** aqui o PlugHub não disputa governança de agente em geral — perde por escala e
distribuição. O argumento que sobra é o **anti-lock-in com prova**: a OpenAI aposentando o próprio
construtor de agentes em 13 meses e a Salesforce absorvendo a Fin são os dois exemplos concretos do trimestre.
Esse argumento exige a interoperabilidade que o PlugHub **ainda não tem** (A2A servidor proposto, sem código).

### 5.4 Frameworks e orquestradores (LangChain, CrewAI, n8n, Temporal, Zapier, Microsoft Agent Framework, OpenAI Agents SDK, Google ADK, Mastra) + infraestrutura de voz (Vapi, Retell, LiveKit)

- **Capital correndo para execução durável e orquestração embutida:** Temporal a **US$ 12,55 bi** (14/09;
  run-rate > US$ 250 mi); n8n a US$ 5,2 bi com SAP; Google ADK 2.0 com motor em **grafo** determinístico+IA.
- **Avaliação em produção virou item de catálogo:** LangSmith *Tuned Evaluators* e *LLM Gateway*;
  Retell *Assure* (QA de chamadas).
- **Voz entrando em contact center por baixo:** Retell ARR ~US$ 80 mi *(est.)* com QA e multicanal; Vapi
  (Amazon Ring roteia 100% do inbound); **LiveKit** — de quem o PlugHub depende — lançou números de
  telefone e *Connectors* para Twilio e WhatsApp Business Calling (01/09), o que **baixa a barreira** para
  qualquer um montar contact center sobre o mesmo SFU.
- **Nenhum** framework tem roteamento, fila, SLA ou console de atendente humano.

**PlugHub vs. segmento:** a camada de contact center continua sendo a fronteira que os frameworks não
cruzaram. O risco mudou de forma: não é mais *"n8n comoditiza a orquestração"* (o arco n8n foi abortado em
18/08, ver [`n8n-arco-abortado-2026-08-18.md`](n8n-arco-abortado-2026-08-18.md)), é **Retell/Vapi subindo
de infraestrutura de voz para produto de atendimento** — mais rápido que o PlugHub desce da orquestração
para a telefonia.

### 5.5 Brasil

- **Concorrentes:** Blip (Blip Studio, Builder + AI Agent com handoff — o posicionamento mais próximo),
  VTEX CX Platform (ex-Weni, "comércio agêntico"), Zenvia, Sinch, Gupshup; **Decagon com escritório em SP**;
  BPOs virando agentes: Algar com 127 agentes de IA em operação, Atento investindo ~R$ 85 mi em IA.
- **WhatsApp:** proibição de chatbot de IA de uso geral na API desde 15/01/2026, **suspensa no Brasil pelo
  CADE** (multa diária mantida em abr/2026); **a partir de 01/10/2026 mensagem de serviço passa a ser
  cobrada por mensagem**, inclusive a gerada por IA de terceiros; faturamento em BRL obrigatório até jul/2027.
  Isso muda a conta de custo de todo bot em WhatsApp — e favorece quem mede consumo por canal (hoje o
  PlugHub não mede: USG-01).
- **Regulação:** PL 2338/2023 votação **depois das eleições de outubro**; ANPD com IA entre os quatro eixos
  de fiscalização 2026–27 (transparência e art. 20 LGPD), sem regulamento final; Anatel: Chamada Verificada,
  bloqueio de volume de chamadas curtas, multa até R$ 50 mi — **requisito de entrada para qualquer dialer**.

**PlugHub vs. segmento:** o diferencial de LGPD (mascaramento por papel, dado sensível sem o humano na
sala) é mais vendável aqui do que qualquer governança de MCP. O ICP de BPO que está montando dezenas de
agentes (Algar) é exatamente quem precisa de **gerir humanos e IA como uma força só** — e o PlugHub
precisa de um deles como piloto.

### 5.6 Portugal e Europa

- **Concorrentes:** Parloa (US$ 3 bi, Allianz/Booking/SAP), NiCE Cognigy (MCP ampliado, avaliação de
  transcrição por LLM), Talkdesk (origem portuguesa; *CXA Operations Center*, outbound de IA), Vonage,
  Sprinklr, Puzzel.
- **Regulação:** alto risco adiado para dez/2027; **art. 50 exigível desde 02/08/2026** — declarar IA em
  todo canal, inclusive na voz e no SIP. Em Portugal, ANACOM é a autoridade coordenadora.

**PlugHub vs. segmento:** o argumento regulatório *de curto prazo* encolheu (alto risco saiu de 2026), e
o que ficou vigente — transparência — é barato para todos. A tese europeia do PlugHub passa a depender de
2027 e de certificação, o que reforça **Brasil primeiro**.

---

## 6. Onde o PlugHub lidera, empata e perde (set/2026)

**Lidera (estrutural, sem prova em campo):**
- Humano e IA ativos na mesma sessão, com papel por posição (quem conduz × convidado) e visibilidade por
  participante — nenhum segmento entregou; Genesys promete para 2027.
- Dado sensível coletado pela IA com o humano fora da sala de mídia (PIN por teclado, validado com gente).
- Deploy por pool com snapshot imutável e promote atômico — paridade com o *release governance* da Sierra.

**Empata ou está em jogo:**
- Motor declarativo único — Google ADK 2.0 e Temporal convergem para o mesmo desenho.
- Avaliação de qualidade de IA — virou catálogo (NiCE, Zoom, Zendesk, LangSmith, Retell); o PlugHub tem
  matéria-prima, não painel, e tem defeito de desfecho aberto (SFE-01/02).
- Voz — núcleo real, sem outbound.

**Perde:**
- Prova em campo, certificação, referências, escala.
- Governança de MCP por chamada — produto de hyperscaler; aqui não imposta.
- Dialer e outbound de voz com compliance Anatel/TCPA.
- Interoperabilidade (A2A servidor sem código) — enfraquece o próprio discurso de "sem lock-in".
- Velocidade de venda: Decagon chegou a São Paulo com clientes nomeados.

---

## 7. Riscos atualizados

| Risco | Movimento jul→set | Probabilidade | Mitigação |
|---|---|---|---|
| Incumbentes entregam co-presença IA+humano | Genesys *Orchestrator* (2027), NiCE suite unificada, Talkdesk Ops Center | Alto em 12 meses | Provar a sala compartilhada em um piloto antes de 2027; é a única janela nomeável |
| Governança MCP vendida como diferencial e desmentida em due diligence | Medida não imposta; mercado com produto GA | **Certo se não corrigido** | Retirar do material; fechar CAP-07/08/10 + ADR de borda única antes de voltar a falar |
| Outcome-agents ocupam o Brasil | Decagon em SP (15/09) | Alto | Não disputar nível 1; entrar por BPO/vertical regulado com o argumento de LGPD |
| Voz subindo de infraestrutura (Retell/Vapi/LiveKit) | Connectors LiveKit, ARR Retell | Médio-alto | Ser a camada de governança e sessão *sobre* eles, não um concorrente de voz |
| Custo de WhatsApp muda o caso de negócio de bots | Cobrança por mensagem de serviço em 01/10 | Certo | Fechar USG-01 (metering por canal) para ter o número de custo que o cliente vai pedir |
| Resolução auditável sem dado confiável | SFE-01/02 medidos | Certo hoje | Corrigir antes de qualquer demo de qualidade |
| Gap de maturidade | Inalterado; concorrentes capitalizados | Alto | Piloto com um BPO; certificação |

---

## 8. Recomendações

1. **Retirar "governança de MCP por invariante" do material** até a borda única existir. A mensagem de
   julho sobreviveu dois meses e não aguentaria uma due diligence técnica.
2. **Bandeira única: a sala compartilhada auditável** — humano e IA ativos no mesmo contato, cada um vendo
   só o que o papel permite, com o desfecho de cada participante registrado. É o que nenhum segmento tem e o
   que a Genesys promete para 2027.
3. **Consertar o dado de desfecho (SFE-01/02) antes de vender qualidade.** "Resolução auditável" com
   `issue_status` ausente em 100% dos segmentos de IA é o valor plausível que a postura de engenharia manda
   caçar — agora em material comercial.
4. **Voz: fechar VOZ-33 (chamada sainte) e aviso de IA por fala** antes de qualquer conversa europeia ou
   de outbound; o dialer só depois, com os invariantes Anatel.
5. **Brasil primeiro, pelo BPO.** O ICP que está montando dezenas de agentes de IA ao lado de milhares de
   humanos é o que precisa do modelo de sessão do PlugHub — e é o único caminho de referência em 2026.
6. **Interoperabilidade com código, não com ADR:** o A2A servidor é o que torna o "sem lock-in" verificável,
   e o trimestre deu os exemplos (OpenAI descontinuando o Agent Builder; Fin absorvida pela Salesforce).
7. **Metering por canal (USG-01)** antes de 01/10: o custo de WhatsApp por mensagem vai ser a primeira
   pergunta de todo cliente de bot no Brasil.

---

## Fontes

**PlugHub (medido em 24/09/2026):** `CLAUDE.md` (§ MCP Interception, § Arc 15), `pending.md`, `done.md`,
`docs/adr/adr-mcp-interception-single-border.md`, `docs/adr/adr-a2a-server-binding.md`,
`docs/product/agentes-externos-reclassificacao.md`, `docs/product/n8n-arco-abortado-2026-08-18.md`,
`docs/product/material-sponsor-operacao-piloto.md`, `packages/sdk/src/mcp-interceptor.ts`.

**CCaaS:**
[Genesys Xperience 2026 — CMSWire](https://www.cmswire.com/contact-center/genesys-touts-nearly-2-9b-in-cloud-arr-unveils-new-agentic-orchestration-tools-at-xperience-2026/) ·
[Genesys adquire Pinkfish — BusinessWire, 30/06](https://www.businesswire.com/news/home/20260630075287/en/Genesys-Acquires-Pinkfish-to-Accelerate-the-Future-of-Autonomous-Customer-Experiences) ·
[Genesys AI billing](https://help.genesys.cloud/articles/virtual-agent-and-agentic-virtual-agent-billing-scenarios/) ·
[NiCE World 2026 — CX Foundation](https://cxfoundation.com/news/nice-world-2026) ·
[NiCE Q2 2026 — Globe and Mail](https://www.theglobeandmail.com/investing/markets/stocks/NICE-Q/pressreleases/3694709/nice-beats-q2-revenue-guidance-and-raises-2026-eps-outlook-on-strong-cloud-and-ai-growth/) ·
[Five9 Q2 2026 — CMSWire](https://www.cmswire.com/customer-experience/five9-raises-full-year-ai-outlook-to-60-growth-after-strong-q2/) ·
[Talkdesk + Microsoft, 10/09](https://www.globenewswire.com/news-release/2026/09/10/3359773/0/en/talkdesk-and-microsoft-expand-partnership-to-accelerate-ai-automation-for-enterprise-contact-centers.html) ·
[Talkdesk CXA Operations Center](https://www.talkdesk.com/news-and-press/press-releases/cxa-operations-center/) ·
[Amazon Connect pricing appendix](https://aws.amazon.com/products/connect/customer/pricing/appendix/) ·
[D365 Contact Center 2026 wave 1](https://learn.microsoft.com/en-us/dynamics365/release-plan/2026wave1/service/dynamics365-contact-center/) ·
[Zoom Agent Performance Suite](https://news.zoom.com/introducing-agent-architect-and-agent-performance-suite-for-zoom-virtual-agent/) ·
[Twilio SIGNAL 2026](https://www.twilio.com/en-us/blog/products/signal-2026-product-announcements)

**Agentes por resultado:**
[Salesforce conclui compra da Fin, 10/09](https://www.salesforce.com/news/press-releases/2026/09/10/salesforce-completes-acquisition-of-fin/) ·
[Fin pricing](https://fin.ai/pricing) ·
[Fin Operator — VentureBeat](https://venturebeat.com/technology/intercom-now-called-fin-launches-an-ai-agent-whose-only-job-is-managing-another-ai-agent) ·
[Sierra release governance](https://sierra.ai/blog/release-governance-guardrails-for-agents-at-scale) ·
[Sierra US$ 950 mi — TechCrunch](https://techcrunch.com/2026/05/04/sierra-raises-950m-as-the-race-to-own-enterprise-ai-gets-serious/) ·
[Decagon no Brasil](https://decagon.ai/blog/decagon-expands-into-brazil) ·
[Decagon — Sacra (est.)](https://sacra.com/c/decagon/) ·
[Zendesk Relate 2026 — CMSWire](https://www.cmswire.com/customer-experience/zendesk-unveils-autonomous-ai-workforce-at-relate-2026/) ·
[OpenAI Presence — HelpNetSecurity](https://www.helpnetsecurity.com/2026/07/22/openai-presence-ai-agent-platform/) ·
[Cresta press](https://cresta.com/press) ·
[Resolution rates — Lorikeet (est.)](https://www.lorikeetcx.ai/articles/ai-support-resolution-rate-what-the-numbers-hide-2026)

**Hyperscalers e governança:**
[Dreamforce 2026 — Salesforce](https://www.salesforce.com/blog/dreamforce-2026-top-it-announcements/) ·
[Google CX Agent Studio handoff](https://docs.cloud.google.com/agent-assist/docs/handoff-cxas) ·
[Agent 365 GA](https://techcommunity.microsoft.com/discussions/agent-365-discussions/agent-365-will-be-generally-available-on-may-1-2026/4500380) ·
[AgentCore Policy GA](https://aws.amazon.com/about-aws/whats-new/2026/03/policy-amazon-bedrock-agentcore-generally-available/) ·
[AgentCore — AWS ML blog](https://aws.amazon.com/blogs/machine-learning/control-agent-behaviors-and-cost-beyond-a-single-action-new-capabilities-in-amazon-bedrock-agentcore/) ·
[ServiceNow AI Control Tower](https://newsroom.servicenow.com/press-releases/details/2026/ServiceNow-expands-AI-Control-Tower-to-discover-observe-govern-secure-and-measure-AI-deployed-across-any-system-in-the-enterprise/default.aspx) ·
[OpenAI Agent Builder deprecation](https://therouter.ai/news/openai-evals-agent-builder-prompts-deprecation-november-2026/) ·
[Palo Alto conclui Portkey](https://www.paloaltonetworks.com/company/press/2026/palo-alto-networks-completes-acquisition-of-portkey-to-secure-ai-agents) ·
[Snowflake/Natoma](https://www.snowflake.com/en/news/press-releases/snowflake-announces-intent-to-acquire-natoma-providing-secure-connectivity-for-the-agentic-enterprise/) ·
[Kong A2A](https://konghq.com/company/press-room/press-release/kong-ai-gateway-now-supports-agent-to-agent-traffic-becoming-the-most-comprehensive-ai-gateway-for-the-agentic-era) ·
[MCP spec 2026-07-28](https://blog.modelcontextprotocol.io/posts/2026-07-28/) ·
[AAIF — MCP Dev Summit](https://aaif.io/blog/mcp-is-now-enterprise-infrastructure-everything-that-happened-at-mcp-dev-summit-north-america-2026/) ·
[A2A 150 organizações — Linux Foundation](https://www.linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year)

**Frameworks e voz:**
[LangChain pricing](https://www.langchain.com/pricing) ·
[LangChain ago/2026](https://www.langchain.com/blog/august-2026-langchain-newsletter) ·
[CrewAI pricing](https://crewai.com/pricing) ·
[n8n + SAP — PRNewswire](https://www.prnewswire.com/news-releases/n8n-valuation-doubles-to-5-2bn-as-sap-makes-strategic-investment-and-plans-to-embed-the-ai-platform-into-joule-studio-302767227.html) ·
[Temporal US$ 12,55 bi](https://temporal.io/news/temporal-raises-550m-at-a-12-55b-valuation) ·
[Microsoft Agent Framework harness GA — InfoQ](https://www.infoq.com/news/2026/08/agent-framework-harness-ga/) ·
[Google ADK 2.0](https://adk.dev/2.0/) ·
[Vapi — TechCrunch](https://techcrunch.com/2026/05/12/vapi-hits-500m-valuation-as-amazon-ring-chose-its-ai-platform-over-40-rivals/) ·
[Retell — Sacra (est.)](https://sacra.com/c/retell-ai/) ·
[LiveKit Connectors](https://livekit.com/blog/introducing-livekit-connectors)

**Brasil e Europa:**
[Marco da IA — Mobile Time, 24/08](https://www.mobiletime.com.br/noticias/24/08/2026/marco-ia-voto-fim-do-ano/) ·
[Algar 127 agentes — Mobile Time](https://www.mobiletime.com.br/noticias/20/08/2026/algar-possui-127-agentes/) ·
[Atento R$ 85 mi em IA — Exame](https://exame.com/negocios/depois-da-crise-teleatendimento-atento-investe-r-85-milhoes-em-ia-e-cria-agente-robocop/) ·
[WhatsApp non-template pricing — Meta](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing/non-template-messages) ·
[CADE mantém multa à Meta](https://www.ecommercebrasil.com.br/noticias/cade-mantem-multa-diaria-contra-meta-por-restricoes-a-chatbots-no-whatsapp) ·
[Anatel — chamadas abusivas](https://www.gov.br/anatel/pt-br/assuntos/noticias/anatel-intensifica-combate-as-chamadas-abusivas-com-novas-medidas-aprovadas-em-reuniao-do-conselho-diretor) ·
[Parloa US$ 3 bi — TechCrunch](https://techcrunch.com/2026/01/15/parloa-triples-its-valuation-in-8-months-to-3b-with-350m-raise/) ·
[NiCE Cognigy Nexus 2026](https://www.businesswire.com/news/home/20260310494828/en/NiCE-Cognigy-Unveils-Breakthrough-Agentic-AI-Innovations-at-Nexus-2026) ·
[Digital Omnibus — EUR-Lex](https://eur-lex.europa.eu/eli/reg/2026/1744/oj/eng) ·
[Art. 50 em vigor — Goodwin](https://www.goodwinlaw.com/en/insights/publications/2026/08/alerts-technology-dpc-eu-ai-act-transparency-obligations-now-in-force) ·
[ANACOM autoridade AI Act — Andersen](https://pt.andersen.com/2025/10/03/ai-act-anacom-assume-supervisao-em-portugal/)
