# PlugHub — Análise Competitiva Atualizada e Honesta (Julho 2026)

> Estado: síntese de produto · Data: 2026-07-27 · Autor: sessão de análise
> Supersedes parcial de [`competitive-analysis.md`](competitive-analysis.md) (mai/2026) e complementa
> [`fin-intercom-comparativo-e-cunha-resolucao-auditavel.md`](fin-intercom-comparativo-e-cunha-resolucao-auditavel.md) (jun/2026)
> e [`value-proposition.md`](value-proposition.md) (mai/2026).

## Como ler este documento (nota de honestidade)

Esta é uma revisão **honesta**, não um pitch. Três ressalvas de método vêm primeiro porque mudam o peso de tudo
que segue:

1. **Assimetria de maturidade.** Os concorrentes aqui comparados são produtos comerciais **em produção**, com
   milhares de clientes pagantes. O PlugHub, pelo estado do repositório (CHANGELOG + CLAUDE.md em jul/2026), é uma
   plataforma **em desenvolvimento ativo, validada por smoke tests e tenant demo** — não há evidência no código de
   deployments enterprise em produção nem de certificações (SOC 2 / ISO 27001 / LGPD auditada) emitidas. Toda
   comparação de *capacidade* abaixo é real no nível de arquitetura/implementação; nenhuma comparação de *tração,
   escala operacional ou prova em campo* favorece o PlugHub hoje. Este é o gap dominante — maior que qualquer
   diferencial de feature.

2. **Dados de concorrentes = jul/2026, verificados na web, mas voláteis.** Pricing enterprise é negociado e
   opaco; os números abaixo têm fonte e data, mas listas públicas divergem de contratos reais (add-ons,
   implementação, mínimos de assento tipicamente levam o TCO a 2–3× o preço de tabela). Trate-os como ordem de
   grandeza, não cotação.

3. **Claims do PlugHub cruzadas com o código.** Onde a matriz antiga marcava "✅", reclassifiquei em três níveis:
   **Entregue** (existe e foi validado no CHANGELOG), **Parcial/roadmap** (desenhado ou em fase), **Narrativa**
   (verdadeiro como arquitetura, mas ainda não é um outcome de produto empacotado). Ver §5.

---

## 1. O que mudou no mercado desde maio/2026

A tese central dos documentos anteriores — a virada *lifecycle-centric* e a migração de valor do "resolver um
contato" (nível 1) para "rever o processo" (nível 2) — **continua correta e, se algo, foi reforçada.** Mas três
movimentos concretos alteram o tabuleiro competitivo:

**1.1 MCP deixou de ser diferencial — virou infraestrutura.** Em nov/2024 o MCP era novo; em 2026 é onipresente:
~97 milhões de downloads mensais, mais de 10.000 servidores publicados, integrado a ChatGPT, Gemini, Copilot,
Cursor e VS Code, e agora sob governança da **Linux Foundation** (junto com A2A e ACP). A afirmação antiga
"**MCP nativo (único)**" está **morta** — todo mundo tem MCP. O que *não* virou commodity é o **gateway de
governança** sobre o MCP: OAuth 2.1 default, transporte HTTP streamable atrás de load balancers, e
**"tool governance + observability como table-stakes"** é literalmente a direção do roadmap do ecossistema.
É exatamente onde o interception guard do PlugHub vive — então o diferencial **migrou de "ter MCP" para "governar
MCP por invariante"**, um alvo mais estreito e mais defensável, porém também na mira de todos.

**1.2 O "kill factor" de 2026 é governança, não capacidade.** Só **11–14% dos pilotos de MCP chegam à produção**,
travados por identidade, auditabilidade e lock-in. O Gartner projeta que **>40% dos projetos de IA agêntica podem
ser cancelados até 2027** por valor incerto, custo crescente e governança fraca. E o **EU AI Act** tornou
obrigações de alto risco **enforçáveis a partir de 02/08/2026** — gateways MCP sobre dado regulado entram no
escopo. Isto valida a aposta de fundo do PlugHub (audit, guard, evaluation, reversibilidade) **melhor do que
qualquer feature** — mas valida a *categoria*, não o *produto*: os incumbentes vão correr para o mesmo discurso.

**1.3 Surgiu uma nova classe de concorrente direto: agentes outcome-based.** Sierra (avaliação ~US$ 15,8 bi,
pricing por outcome) e Decagon (contratos medianos ~US$ 400k/ano, por conversa ou por resolução) não existiam com
esse peso na análise de abril. Eles atacam **exatamente a cunha de entrada** que o doc do Fin propõe ("Resolução
Auditável", nível 1). O Fin, aliás, agora publica um *hub* comparando a si mesmo contra Sierra — sinal de que a
guerra do nível 1 está madura e sangrenta. Consequência estratégica direta: **a cunha nível 1 do PlugHub nasce num
mercado já lotado**; o valor real está mesmo no nível 2, e a cunha só se justifica como *land*, nunca como
bandeira (o doc do Fin já dizia isso; o mercado de jul/2026 torna isso mais urgente).

---

## 2. O que mudou no PlugHub desde os docs (mai–jun → jul/2026)

Os docs competitivos foram escritos quando vários diferenciais eram *roadmap*. Desde então, o repositório fechou
peças que antes eram promessa. Reclassificação honesta (fonte: CHANGELOG + CLAUDE.md):

| Área | Nos docs (mai/jun) | Estado real (jul/2026) |
|---|---|---|
| **Outbound unificado** | "mesmo motor" (tese) | **Entregue** — arco Outbound completo (Fases 1–5): mailing + campaign + delivery, governança de contato/fadiga (frequency caps, quarentena, opt-out, janela de calendário), importador de arquivo (CSV/xlsx), fan-out dispatcher/worker, survey outbound e2e. |
| **Journey (lifecycle)** | Arc 10 removido; "primitive" era tese | **Parcial/entregue** — modelo de 3 níveis por union-find (`root_session_id` + `journey_merge`): J1–J3 + J5a (contexto `@ctx.journey.*`) entregues; **Customer Voice** (lente grain×metric) entregue. Falta o drill N3 na Vista Processos. |
| **Dialer preditivo (voz ativa)** | "roadmap" | **Ainda roadmap** — power/predictive/progressive/preview + guard TCPA/LGPD + DNC **não** implementados. Outbound assíncrono usa `collect` lazy (link/mensagem), não discagem síncrona com pacing. |
| **Voz/áudio** | PSTN + WebRTC | **Entregue** — Arc 15 (WebRTC/LiveKit SFU: gravação, STT/TTS, supervisão hidden). PSTN via Twilio. Bridge PSTN→WebRTC ainda em aberto. |
| **Fila de trabalho humano / inbox** | não existia | **Parcial** — pull dispatch + `PullInboxPanel` + renderer genérico de collect-form no Console entregues; aprovação humana como passo de workflow com ABAC completo **em fase** (R1). |
| **Identidade / cadastro de cliente** | ausente (gap do "nível b") | **Parcial** — Resolvedor Fase A/B: identidade progressiva, posse de canal via OTP, gate seguro para retomada cross-canal. Merge de clientes / `external_refs` / wiring de CRM = Fase C (pendente). |
| **Scheduler / agenda** | ausente | **Entregue** — `scheduler-api` (Fases 1–3): dispara pool por webhook em horário, recorrência, UI + fire-now. |
| **Cliente 360** | ausente | **Parcial** — desenho fechado (Console 4 abas × Analytics); backend de histórico/busca pronto; cadastro + 360 agregado pendentes. |
| **Audit LGPD** | módulo DPO | **Parcial** — Fase 1 (sessions + mcp_calls) entregue; desmascaramento de `original_content`, user_access logs, SAR/erasure = fases 2–5 pendentes. |

**Leitura honesta:** o PlugHub *avançou substancialmente* na direção que os docs prometiam — o outbound
unificado e a espinha de journey deixaram de ser slide e viraram código validado. Mas os itens que dependem de
**maturidade operacional** (dialer com compliance no motor, certificações, Cliente 360 completo, aprovação com
ABAC/anexos/masking) seguem parciais, e a **prova em produção não existe no repositório**.

---

## 3. Comparativo por plataforma (pricing jul/2026, com fonte)

> Todos os valores são preço público de tabela; TCO real costuma ser 2–3× com add-ons, implementação e mínimos.

**Intercom / Fin.** US$ 0,99 por *outcome* (resolução, handoff via procedure ou desqualificação; lead
qualification US$ 9,99), plano base US$ 49/mês com 50 resoluções, mínimo de 50 outcomes/mês; roda sobre
Zendesk/Salesforce/HubSpot. *Força:* legibilidade de outcome + adoção incremental. *Gap vs. PlugHub:*
deflection unidirecional (IA→humano), sem co-presença humano+IA, taxa de resolução autorreportada e não auditável.

**Sierra (novo).** Outcome-based custom, não público; estimativas de terceiros ~US$ 150k/ano entrada, US$
750k–1,5 mi+ enterprise; avaliação ~US$ 15,8 bi. *Força:* voz + outcome + marca premium. *Gap:* mesmo limite
estrutural do Fin (agente, não camada); paga-se *mais* conforme a IA melhora (contenção maior = fatura maior).

**Decagon (novo).** Custom; mediano ~US$ 400k/ano; ~US$ 50k base + ~US$ 0,99/conversa, ou ~US$ 0,50/resolução.
*Gap:* cobra por esforço (conversa tocada), não por resultado — o oposto da promessa "auditável".

**Salesforce Agentforce.** Migrou para **Flex Credits**: US$ 0,10/ação padrão (20 créditos), US$ 0,15/ação de voz
(30 créditos), US$ 500 por 100k créditos; modelo fixo alternativo ~US$ 2/conversa (24h); Foundations dá 200k
créditos grátis (exige Enterprise Edition). *Gap vs. PlugHub:* três overhauls de pricing em 18 meses ("whiplash"),
lock-in de EE, sem conference room humano+IA.

**Google Gemini Enterprise.** Relançado out/2025 como plataforma GCP separada do Workspace: US$ 21 (Business) /
30–35 (Standard) / 50–60 (Plus) por usuário/mês + billing de token e compute à parte para agentes custom. *Gap:*
lock-in GCP, pricing multidimensional, Gemini como cidadão de primeira classe.

**Genesys Cloud CX.** US$ 75–155/usuário/mês + AI Experience Tokens por consumo (fair-use + overage em arrears;
CX 4 inclui 30 tokens/agente/mês). *Força:* telefonia/omnichannel maduros, LLMs externos via AI Studio. *Gap:*
consumo de token opaco, NLU proprietário sob o LLM, sem MCP nativo declarado.

**NICE CXone Mpower.** Digital US$ 71 / Voice US$ 94 / Complete US$ 209 / Ultimate (Mpower) US$ 249 por
agente/mês, com Enlighten (Actions/Autopilot/Copilot); add-ons US$ 20–60+/seat; **uso adicional por sessão de
Autopilot/Copilot**. *Gap:* lock-in CXone, integração NICE+Cognigy ainda amadurecendo, sem guard por chamada MCP.

**Five9.** US$ 119–159/seat + 3.000 minutos de IA/seat inclusos; IVA e AI Agents com fee de uso adicional; TCO
carregado US$ 300–600/seat. *Gap:* arquitetura blended (workflow tradicional + agentic), documentação MCP/A2A
limitada, add-ons opacos.

**Talkdesk.** US$ 85–165/seat, TCO real US$ 200–300; Autopilot Agentic (inclui e-mail). Continua o CCaaS que
mais fala MCP, mas **em 2026 "falar MCP" não diferencia** (§1.1). *Gap:* AWS-centric, MCP em camadas específicas
sem guard obrigatório, sem conference room unificado.

**LangGraph Platform.** Open-source grátis; Plus US$ 0,001/execução de nó + standby + US$ 39/usuário (LangSmith,
máx 10). **CrewAI:** Free / Pro US$ 25/mês / Enterprise ~US$ 60k/ano (10k execuções, 50 crews, RBAC/SSO/audit).
**n8n:** self-hosted grátis; Starter €24 → Pro €60 → Business €800; por execução de workflow; ARR US$ 40M,
avaliação US$ 2,5 bi (out/2025, Accel + NVIDIA). *Gap comum:* são frameworks/orquestradores, **não CCaaS** — sem
operator console, session replay, roteamento skill-based, canais nativos, compliance de contact center. n8n segue
o risco de médio prazo (comoditiza "orquestração" no low/mid-market).

---

## 4. Matriz de capacidades — reavaliada com flags de honestidade

Mantive as capacidades da matriz original, mas troquei o "✅ genérico" por um rótulo honesto do lado PlugHub:
**[E]** entregue e validado · **[P]** parcial/roadmap · **[N]** verdadeiro como arquitetura, ainda não empacotado
como produto/prova.

| Capacidade | Melhor concorrente hoje | PlugHub | Veredito honesto |
|---|---|---|---|
| Governança de MCP por invariante (guard + audit por chamada) | Trust Layer (SF), Model Armor (Google) — pré-LLM, não por-chamada obrigatório | **[E]** McpInterceptor + proxy sidecar, guard < 1ms, audit Kafka obrigatório | **Diferencial real e agora mais relevante** (§1.1–1.2). Mas a categoria "MCP gateway" está se formando no mercado — janela estreita. |
| Humano + IA na **mesma** sessão (co-presença, não handoff) | Todos fazem só handoff | **[E]** Conference room, visibilidade por participante | **O diferencial estrutural mais forte.** Nenhum concorrente cruza sem redesenhar o produto. Sustenta os empacotamentos A/C/E do doc do Fin. |
| Visibilidade por participante × campo × role (delegação sem ver o dado) | Nenhum | **[E]** mascaramento tokenizado por role + begin/end_transaction | **Diferencial real** (PCI/LGPD/SOX). Depende de co-presença — inimitável sem redesenho. |
| BYO LLM / BYO framework | Agnóstico nos dev-first; parcial nos CCaaS | **[E]** agnóstico + regenerate/proxy do SDK | Real, mas **commoditizando** (dev-first já são agnósticos; Genesys/Five9 abriram LLM externo). |
| Motor único p/ todos os fluxos (inbound/outbound/workflow/especialista/hook) | Todos têm motores separados | **[E]** Skill Flow (14+ steps); outbound unificado agora entregue | **Diferencial real e recém-provado** (Outbound 1–5). Elegante e difícil de copiar sem refazer a stack. |
| Journey multi-contato como primitive operacional **e** analítica | Pointillist/Adobe (só analytics); case de CRM (fora do roteador) | **[P]** union-find `root_session_id` + merge; Customer Voice entregue; drill N3 pendente | **Tese forte, implementação parcial.** É a virada de categoria — mas ainda não é um produto redondo end-to-end. |
| Billing previsível (licença por concorrência, humano+IA) | NICE (por seat) é previsível mas caro | **[N]** modelo de capacidade desenhado | **Verdadeiro como modelo, não provado em fatura real.** Anti-bill-shock é argumento genuíno vs. Agentforce/Gemini/Genesys (todos consumo-based e opacos). |
| Session replay + comparison/diff | QM dos CCaaS; tracing dos dev-first | **[E]** Session Replayer + Comparison Mode (Jaccard) | Real; profundidade acima da média. Falta o empacotamento "Simulações" cliente-facing. |
| Evaluation/qualidade como flywheel auditável | Fin Flywheel (visível, cliente-facing); Enlighten (NICE) | **[P]** Arc 6/13 + calibração + observabilidade por deploy | **Matéria-prima superior, empacotamento inferior.** O Fin *vê e opera* o loop; o PlugHub tem painel de supervisor, não de comprador. |
| Voz nativa (gravação + STT/TTS + supervisão) | Genesys/NICE/Five9 maduros | **[E]** Arc 15 WebRTC/LiveKit + PSTN Twilio | Entregue no núcleo; **sem dialer preditivo com compliance no motor** (roadmap). |
| Outbound com dialer preditivo + compliance guard | Genesys/NICE/Five9 (maduros) | **[P/roadmap]** | **Concorrentes ganham hoje.** Honestidade: o "outbound unificado" do PlugHub é forte em orquestração assíncrona, fraco em discagem de voz em massa. |
| Escala em produção, certificações, referências enterprise | Todos | **[ausente]** | **Concorrentes ganham decisivamente.** Este é o gap dominante. |

---

## 5. Onde o PlugHub genuinamente lidera, empata e perde

**Lidera (defensável, entregue):**
- Co-presença humano+IA na mesma sessão + visibilidade por campo/role/participante — o núcleo inimitável.
- Governança de MCP por invariante (guard + audit obrigatório) — agora *validado pela direção do mercado* (§1.2).
- Motor único declarativo cobrindo inbound/outbound/workflow/especialista/hook — recém-provado com o arco Outbound.

**Empata ou está em jogo (real, mas commoditizando ou incompleto):**
- BYO LLM/framework (todos os dev-first já têm; CCaaS abrindo).
- Journey lifecycle-centric — tese vencedora, implementação parcial (drill N3, Cliente 360 pendentes).
- Evaluation-as-garantia — matéria-prima superior, **narrativa/painel cliente-facing ausente** (o gap central do doc do Fin, ainda aberto).

**Perde hoje (honestamente):**
- Maturidade, escala, certificações, prova em campo — nenhuma evidência de produção enterprise.
- Dialer preditivo de voz com compliance regulatório — roadmap; incumbentes entregam.
- Legibilidade de produto e velocidade de venda — a plataforma "não cabe numa frase"; Fin/Sierra/Decagon vendem em 30s.
- Mercado da cunha de entrada (nível 1) já saturado por outcome-agents bem capitalizados.

---

## 6. Riscos atualizados

| Risco | Movimento desde mai/2026 | Probabilidade | Mitigação |
|---|---|---|---|
| "MCP nativo" deixou de diferenciar | **Concretizou-se** (§1.1) | Certo | Reposicionar de "temos MCP" para "governamos MCP por invariante + audit"; correr para certificações antes do gateway-MCP virar categoria comprada. |
| Incumbentes copiam o guard/gov de MCP | Roadmap do ecossistema aponta "tool governance = table-stakes" | Alto (12 meses) | Moat = modelo de sessão humano/IA (mais fundo que o guard); empacotar A/C/E (impossíveis sem co-presença). |
| Outcome-agents (Sierra/Decagon) tomam o nível 1 | **Novo e agressivo** | Alto | Não brigar no nível 1 como bandeira; usar "Resolução Auditável" só como *land* em vertical regulado; foco no nível 2 (processo auditável). |
| EU AI Act / LGPD elevam a barra de compliance | Enforcement 02/08/2026 | Certo | Virar isso em *vantagem* (audit/guard nativos), mas exige as certificações que ainda não existem — **prioridade máxima**. |
| Gap de maturidade/tração | Inalterado; concorrentes escalaram | Alto | Modelo de entrega via professional service (doc do Fin §8) para fechar referências; produtizar cada engajamento (flywheel, não esteira). |
| n8n comoditiza orquestração no mid-market | Avaliação US$ 2,5 bi, credits de IA subindo | Médio (médio prazo) | Subir em compliance enterprise + verticalização. |

---

## 7. Recomendações (o que fazer com esta análise)

1. **Aposentar a claim "MCP nativo (único)"** em todo material — é falsa em jul/2026 e enfraquece a credibilidade
   do resto. Substituir por "**governança de MCP por invariante + audit por chamada**", ancorada no EU AI Act e no
   dado dos 11–14% de pilotos que chegam à produção.
2. **Tratar a matriz como capability map, não scorecard.** Sempre acompanhar de uma linha sobre maturidade/prova —
   a honestidade compra credibilidade com o comprador enterprise cético (que é o ICP certo).
3. **Não ancorar no nível 1.** O mercado de resolução isolada saturou (Fin, Sierra, Decagon). A cunha "Resolução
   Auditável" serve de *land* barato em vertical regulado; a bandeira é o **nível 2 auditável** (§1.3).
4. **Fechar o gap de empacotamento antes do de engenharia.** O maior déficit competitivo *não* é técnico — é o
   painel cliente-facing (Resolução & Qualidade) e a narrativa de outcome (RA%) que o doc do Fin especifica e que
   ainda não existe. Baixo custo de engenharia, alto retorno de "formatação".
5. **Priorizar certificações (SOC 2 / ISO 27001 / LGPD).** São o pré-requisito literal para todo o discurso
   "auditável" e para operar sob o EU AI Act. Sem elas, o diferencial de governança é só arquitetura.
6. **Escolher o flagship de nível 2** (doc do Fin: A = dado sensível, B = quality/assurance via importadores, ou
   C = control plane de governança de agentes). O contexto de jul/2026 (agent sprawl + kill factor de governança)
   fortalece **C como visão** e **B como venda mais leve** — mas ambos exigem a prova em campo que falta.

---

## Fontes

Concorrentes (verificadas jul/2026):

- [Intercom Fin AI Pricing 2026 — Gleap](https://www.gleap.io/blog/intercom-fin-ai-pricing-2026), [Fin AI pricing — Featurebase](https://www.featurebase.app/blog/fin-ai-pricing)
- [Sierra AI outcome-based pricing / valuation — Value Add VC](https://valueaddvc.com/blog/how-does-sierra-ai-make-money-outcome-based-pricing-enterprise-agents-and-the-business-model-breakdown), [Sierra vs Decagon — eesel AI](https://www.eesel.ai/blog/decagon-vs-sierra)
- [Salesforce Agentforce Flex Credits — eesel AI](https://www.eesel.ai/blog/agentforce-pricing), [Agentforce credits & cost model — Jitendra Zaa](https://www.jitendrazaa.com/blog/salesforce/salesforce-agentforce-credits-cost-model-complete-guide-2026/)
- [Gemini Enterprise pricing 2026 — Coworker AI](https://coworker.ai/blog/gemini-enterprise-pricing)
- [Genesys Cloud pricing 2026 — Krispcall](https://krispcall.com/general/genesys-pricing/), [AI token billing — Genesys](https://help.genesys.cloud/articles/ai-token-billing/)
- [NICE Mpower pricing — UC Today](https://www.uctoday.com/unified-communications/nice-mpower-guide-features-pricing-benefits-and-more/), [NICE Mpower cost — CX Today](https://www.cxtoday.com/contact-center/nice-mpower-whats-included-how-much-does-it-cost/)
- [Five9 pricing 2026 — Platform28](https://www.platform28.com/blog/five9-pricing-guide), [State of Five9 2026 — InflectionCX](https://www.inflectioncx.com/intelligence/analysis/state-of-five9-2026-ai-pivots-stock-collapse-cx-leaders)
- [Talkdesk pricing 2026 — Prospeo](https://prospeo.io/s/talkdesk-pricing-reviews-pros-and-cons), [MCP em 2026 — WorkOS](https://workos.com/blog/everything-your-team-needs-to-know-about-mcp-in-2026)
- [LangGraph pricing 2026 — AIToolTier](https://aitooltier.com/pricing/langgraph), [CrewAI pricing — ZenML](https://www.zenml.io/blog/crewai-pricing), [n8n pricing 2026 — Goodspeed](https://goodspeed.studio/blog/n8n-pricing)
- [Future of MCP: enterprise adoption — Toloka](https://toloka.ai/blog/the-future-of-mcp-enterprise-adoption/), [Agent protocol stack MCP/A2A/ACP — Zuplo](https://zuplo.com/blog/agent-protocol-stack-mcp-a2a-acp-2026)

PlugHub (repositório, jul/2026): `CLAUDE.md`, `CHANGELOG.md`, e docs de produto citados no cabeçalho.
