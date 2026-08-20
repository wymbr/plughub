# PlugHub — Análise Competitiva

> Fonte: `plughub_analise_competitiva_2026.md` — Abril 2026  
> Atualização desta síntese: Maio 2026

> ⚠️ **Correção de 2026-08-19 — medido.** Duas linhas da Matriz de capacidades foram corrigidas: **"Voz com
> stack interno — ✅ SIP + WebRTC nativos"** e **"Outbound unificado — ✅ Mesmo motor + dialer interno"** eram
> **falsas**. `VoiceAdapter.handle_inbound` chama cinco métodos que não existem em `packages/channel-gateway`
> (`_open_session`, `_route_inbound`, `_publish_inbound`, `_normalize_text`, `_normalize_menu_result` —
> `adapters/voice.py:236,247,433,558,565`), mockados em `tests/test_voice_adapter.py:116-121`: em runtime real dá
> `AttributeError` antes de publicar em `conversations.inbound`, e não há uma única sessão de voz no ambiente;
> `collect`/menu por voz está morto (`voice.py:624-629,657`). Em WebRTC só a sinalização roda — plano de mídia
> nunca provisionado (zero LiveKit em compose, SDK fora de `packages/channel-gateway/pyproject.toml:6-23`,
> `_dev_mode` placebo em `webrtc_provider.py:167`). **Nenhum dos dois canais de áudio funciona hoje**, e o
> dialer — que depende do `VoiceAdapter` — está **bloqueado por falta de plano de mídia**. **Material de venda:
> não usar estes trechos em proposta comercial** até o arco fechar V-F2. Ver
> [`adr-voice-media-plane.md`](../adr/adr-voice-media-plane.md) (proposto, V-F0..V-F5).

## Contexto do mercado

Em 2025–2026, o mercado de agentes IA para contact center e automação enterprise convergiu em torno de três arquétipos, cada um com um gap que o PlugHub pode ocupar.

**Arquétipo 1 — Hyperscale Agent Platforms** (Gemini Enterprise, Salesforce Agentforce): vendem "agentic everything" com lock-in estrutural — Gemini exige GCP, Agentforce exige Enterprise Edition. Pricing multidimensional e imprevisível. TCO documentado do Agentforce ultrapassa USD 550/usuário/mês + implementação de USD 2–6k por agente.

**Arquétipo 2 — CCaaS com IA agentiva** (Genesys, NICE/Cognigy, Five9, Talkdesk): maduros em telefonia e omnichannel, mas a camada agentic é evolução de NLU legado. Flexibility de framework de agente é limitada. Apenas Talkdesk declara suporte MCP explícito.

**Arquétipo 3 — Orquestradores dev-first** (LangGraph, CrewAI, n8n): excelentes como primitives de orquestração, mas não são CCaaS — faltam operator console, session replay, heatmap de sentimento, roteamento skill-based e o conceito de agente humano como participante igual.

---

## A virada de categoria — lifecycle-centric

Os três arquétipos competem dentro de dois mental models históricos. **CCaaS opera num modelo interaction-centric**: a unidade de gestão é a interação individual (chamada, chat, ticket), e os KPIs são AHT, FCR e SLA por interação. **CRM opera num modelo record-centric**: a unidade é o registro (case, opportunity, account), e a interação é apenas um campo no registro. Os dois mundos coexistem mal — uma "case" no Service Cloud pode agrupar seis interações, mas o roteamento, SLA e qualidade continuam medidos por interação.

Pointillist (adquirida pela Genesys), Adobe Customer Journey Analytics e similares tentam ser camada de analytics de jornada, mas sem amarração operacional ao roteador. Pega tem cases como primitive operacional, mas é BPM, não contact center.

O PlugHub propõe um terceiro modelo: **lifecycle-centric**. A unidade é a **Journey** — processo completo do cliente atravessando múltiplos contatos, canais, dias e participantes (humanos e IA), com SLA, roteamento e analytics medidos no nível da jornada e drillable até o turno individual. Essa terceira via tem três consequências competitivas:

- **Torna o CRM redundante** para casos de uso onde o registro central é o processo, não a entidade (cobrança, onboarding, retenção, suporte recorrente)
- **Comoditiza a interação no CCaaS** — interação vira instância dentro de uma jornada, não a unidade de medida
- **Unifica inbound e outbound** sob a mesma definição declarativa (Skill Flow), sem módulos separados, com o media gateway interno executando o pacing de discagem

Para o comprador enterprise, isso muda a pergunta inicial. Não é mais "qual é meu melhor CCaaS?" ou "qual é meu melhor CRM?" — é "como gerencio o lifecycle do cliente como uma unidade coerente, com analytics e operação no mesmo lugar?".

---

## Comparativo por plataforma

### Google Vertex AI / Gemini Enterprise

**Pricing:** Gemini Enterprise USD 21–60/user/mês + Vertex AI Agent Engine USD 0.0090/GB-hora + USD 0.25/1.000 eventos de sessão + tokens Gemini + indexação.

**Pontos fortes:** Suporte nativo a LangChain, LangGraph, CrewAI, AG2/AutoGen e ADK proprietário. A2A Protocol v1.0 em produção. Model Armor como guardrail. CCAI Platform com voice, chat, SMS, WhatsApp.

**Gap vs. PlugHub:** Lock-in GCP. Pricing multidimensional (impossível estimar TCO). LLM Gemini como first-class citizen. Outros LLMs funcionam como second-class. Sem equivalente ao conference room unificado (humano + IA na mesma sessão).

---

### Salesforce Agentforce

**Pricing:** USD 0.10/ação padrão via Flex Credits. Alternativa Enterprise ~USD 550/user/mês. Implementação USD 2k–6k por agente.

**Pontos fortes:** Atlas Reasoning Engine com event-driven pub/sub. BYO LLM (4 providers). Claude disponível via VPC Salesforce. MCP como pilar da Agentforce 3. Einstein Trust Layer com PII masking. Até 70% de resolução autônoma em casos de referência.

**Gap vs. PlugHub:** Limite de 15 tópicos × 15 ações por agente. Máximo 20 agentes por org. Três pricing overhauls em 18 meses ("whiplash"). Adoção real: ~8.000 de 150.000+ clientes Salesforce. Enterprise Edition obrigatória.

---

### Genesys Cloud CX + Genesys AI

**Pricing:** CX 1–4 USD 75–240/seat/mês + AI tokens consumption-based. 100 agentes em CX 3 ~USD 186k/ano base + USD 24–120k/ano em IA.

**Pontos fortes:** LLMs externos suportados (OpenAI, Anthropic, Google, Bedrock). BYO framework via AI Studio. A2A via ServiceNow. Sentiment multilíngue (Radarr, 100+ idiomas).

**Gap vs. PlugHub:** Token consumption opaco. NLU ainda depende de modelos proprietários com LLM por cima. Sem MCP nativo declarado.

---

### NICE CXone Mpower + Cognigy

**Pricing:** Omnichannel Suite USD 110/seat. CXone Mpower USD 249/seat inclui Enlighten (Actions, Autopilot, Copilot, XM). Cognigy pós-aquisição USD 955M em set/2025.

**Pontos fortes:** CXone Mpower Agents fully automated multi-canal. Multi-agent orchestration built-in. Enlighten Copilot para agentes e supervisores. End-to-end bot + routing + RPA com SLA único pós-Cognigy.

**Gap vs. PlugHub:** Integração NICE + Cognigy ainda em progresso. Lock-in CXone forte. NLU hybrid menos flexível que LLM fine-tune direto. Sem interception guard por chamada MCP.

---

### Five9

**Pricing:** Digital USD 119 → Core USD 159. AI Agents e add-ons não disclosed.

**Pontos fortes:** FlexLM framework — o mais LLM-agnostic dos CCaaS tradicionais. Knowledge Node com RAG. AI Trust & Governance com detecção de alucinação. Agentic CX com System 2 reasoning.

**Gap vs. PlugHub:** Arquitetura blended (workflows tradicionais + agentic) cria trade-offs. Documentação MCP/A2A limitada. AI add-ons com pricing opaco.

---

### Talkdesk

**Pricing:** Standard USD 85 → Elite USD 165/seat. Autopilot Agentic e CXA Platform: add-on custom.

**Pontos fortes:** **Único CCaaS analisado com MCP nativo explícito na camada de AI agent.** CXA Platform co-construída com AWS (EKS-native, Bedrock-native). Automation Flows com MCP (fev/2026). Multi-agent orchestration.

**Gap vs. PlugHub:** CXA e Automation Flows em early adoption. AWS-centric. MCP usado em camadas específicas — não como protocolo único com guard obrigatório. Sem conference room unificado (humano/IA).

> **Nota:** Talkdesk é o competidor direto mais perigoso. Tem MCP + automação + CCaaS maduro. O diferencial do PlugHub é ser MCP-first com interception guard como invariante, e ter billing por capacidade.

---

### LangGraph Platform (LangSmith Deployment)

**Pricing:** Plus USD 39/seat/mês + USD 0.001/execução de nó.

**Pontos fortes:** Checkpointing nativo. Multi-agent via grafo explícito. Human-in-the-loop robusto. LangSmith tracing. Deploy Docker/K8s/managed.

**Gap vs. PlugHub:** Zero features CCaaS. Sem operator console, session replay, heatmap, roteamento skill-based, canais nativos, compliance contact-center.

---

### CrewAI Enterprise

**Pricing:** Free 50 execuções → Starter USD 99/mês → Ultra USD 120k/ano.

**Pontos fortes:** Role-driven multi-agent (YAML + Python). Human-in-the-loop nativo. Deploy K8s (Helm). p99 <500ms para 10-agent crews. 47.8K GitHub stars, 2B execuções em 12 meses.

**Gap vs. PlugHub:** Framework de agente dev-first, sem canais nem operator tooling. Mesmo gap do LangGraph.

---

### n8n

**Pricing:** Self-hosted grátis. Cloud Starter €24 → Business €800/mês (40K execuções + SSO + Git).

**Pontos fortes:** Nó "AI Agent" nativo + 70+ nós LangChain. MCP Client Tool node nativo. Templates de contact center. Canais via integração (WhatsApp, Telegram, Email, Voice). Handoff cold/warm. USD 40M ARR, valuation USD 2.5B.

**Gap vs. PlugHub:** Workflow builder, não CCaaS. Falta operator console, session replay, compliance, workforce management, skill-based routing robusto. Billing por execução.

> **Nota:** n8n é o competidor de baixo risco no curto prazo, mas o de mais alto risco no médio prazo — cresce rapidamente no low/mid-market e pode comoditizar "orquestração de agentes" antes do PlugHub subir no enterprise.

---

## Matriz de capacidades

| Capacidade | Gemini | Agentforce | Genesys | NICE | Five9 | Talkdesk | LangGraph | CrewAI | n8n | **PlugHub** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| MCP nativo | Parcial | Sim | Não | Não | Não | Sim | Via ext. | Custom | Sim | **Sim (único)** |
| Interception guard obrigatório | Plugin | Trust Layer | Não | Não | Parcial | Não | Custom | Custom | Custom | **✅ Invariante** |
| BYO LLM | Parcial | 4 providers | Sim | Sim | Sim | Bedrock | Agnóstico | Agnóstico | Agnóstico | **✅ Agnóstico** |
| BYO framework | LangChain+ | Apex/DX | AI Studio | Cognigy | Templates | CXA | Nativo | Nativo | 70+ nodes | **✅ Qualquer** |
| Humano + IA mesma sessão | Handoff | Handoff | Handoff | Handoff | Handoff | Handoff | N/A | N/A | N/A | **✅ Conference** |
| Session Replay | CCAI | Test Center | QM | QM | QM | QM | Tracing | Tracing | Logs | **✅ + Diff** |
| Billing previsível | Não | Não | Parcial | Sim | Parcial | Parcial | Parcial | Não | Parcial | **✅ Licenças simultâneas (humanos + IA)** |
| Supervisão operacional em tempo real | Sim | Sim | Sim | Sim | Sim | Sim | Não | Não | Não | **✅** |
| Escopo granular de supervisor (grupo + turno + módulo) | Parcial | Parcial | Custom | Custom | Custom | Custom | N/A | N/A | N/A | **✅ JWT-resolved nativo** |
| Voz com stack interno (gravação + transcrição + STT/TTS) | Parcial (CCAI) | Parceiro (Vonage) | Sim | Sim | Sim | Parceiro (AWS) | N/A | N/A | N/A | **⚠️ Não — projeto.** Voz não roda (`voice.py:236,247,433,558,565`); WebRTC só sinaliza, sem SFU |
| Motor único para todos os fluxos | Múltiplos (Agent Engine + CCAI + Vertex) | Múltiplos (Atlas + Flow + MC + SC) | Múltiplos (Architect + AI Studio + Outbound) | Múltiplos (CXone + Cognigy + Outbound) | Parcial | Múltiplos (CXA + Autopilot + Outbound) | Só engine IA | Só engine IA | Só workflow | **✅ Skill Flow unificado** |
| Customização completa por pool/fila | Limitada | Tópicos+ações | Por queue | Por queue | Por queue | Por queue | N/A | N/A | N/A | **✅ Inbound + outbound + especialistas + wrap-up + hooks por pool** |
| Visibilidade por participante (per-field per-role) | Não (Model Armor pré-LLM) | Parcial (Trust Layer pré-LLM) | Não | Não | Não | Não | N/A | N/A | N/A | **✅ Por participante + por campo + por role** |
| Delegação de dados sensíveis com supervisão | Não | Não | Não | Não | Não | Não | N/A | N/A | N/A | **✅ Humano supervisiona sem ver o dado** |
| Outbound unificado (mesmo motor que inbound) | Não (CCAI sep.) | Não | Módulo sep. | Módulo sep. | Config sep. | Módulo sep. | N/A | N/A | Custom | **✅ Mesmo motor** (canais de texto) · **⚠️ dialer interno: NÃO — bloqueado por falta de plano de mídia** |
| Journey multi-contato como primitive (routing + analytics) | Não | Case (CRM-side) | Pointillist (analytics) | XM (parcial) | Não | Não | Thread (técnico) | Não | Não | **✅ Routing + Analytics** |

---

## Riscos e mitigações

| Risco | Probabilidade | Mitigação |
|---|---|---|
| Hyperscalers copiam interception guard em MCP | Médio (12–18 meses) | Certificações compliance (SOC 2, LGPD) + modelo de sessão humano/IA como moat |
| NICE+Cognigy ou Talkdesk+AWS convergem para ICP similar | Alto | Posicionar como neutro (sem lock-in CXone/AWS) + MCP-first como protocolo único |
| n8n cresce no low/mid-market e comoditiza orquestração | Médio (médio prazo) | Subir em compliance enterprise + verticalização (financeiro, saúde, telco) |
| Clientes Genesys/NICE perguntam "rip-and-replace ou camada por cima?" | Alto | PlugHub como camada de orquestração plugando via MCP no CCaaS existente |

---

## Fonte completa

O documento de análise completo (303 linhas, Abril 2026) com fontes primárias para cada afirmação está em [`docs/plughub_analise_competitiva_2026.md`](../plughub_analise_competitiva_2026.md).
