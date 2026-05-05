# PlugHub — Público-Alvo e Personas de Comprador

> Última atualização: Maio 2026

## Três perfis de comprador

### 1. Contact Center Enterprise

**Perfil:** Empresas com 50+ agentes humanos em operações de atendimento (SAC, retenção, cobrança, suporte técnico) que querem adicionar agentes de IA sem substituir o time humano nem se prender a um único provedor de LLM.

**Dores principais:**
- Agentes de IA de plataformas CCaaS existentes (Genesys, NICE, Five9) são engessados — usa o bot deles ou integra via canal
- Handoff humano→IA e IA→humano é uma transição visível para o cliente (scripts diferentes, contexto perdido)
- Pricing por seat + consumo + add-ons de IA é imprevisível; volume alto gera "bill shock"
- Auditoria de chamadas MCP e mascaramento de dados sensíveis são exigências regulatórias não atendidas por soluções existentes

**Como o PlugHub responde:**
- Humanos e IA são participantes iguais na mesma sessão ("conference room") — o cliente não percebe a transição
- Billing por capacidade configurada: N instâncias = preço fixo, independente do volume de turnos
- Interception guard obrigatório em todas as chamadas MCP + mascaramento tokenizado com acesso por role = argumento compliance nativo para CISO/DPO
- Troca de LLM (Anthropic, OpenAI, Google) sem mudar o agente — o AI Gateway é o único ponto de troca

**Buyer personas típicos:**
- Head de Contact Center / VP Customer Experience: foco em SLA, produtividade e qualidade
- CTO / Head de IA: foco em framework-agnosticism, arquitetura e portabilidade
- CFO: foco em TCO previsível vs. modelos de consumo dos incumbentes
- CISO / DPO: foco em LGPD, mascaramento, auditoria e interception guard

---

### 2. Automação de Processos com Aprovação Humana

**Perfil:** Empresas de qualquer setor com processos que envolvem múltiplos passos, aprovações humanas, coleta assíncrona de dados e acionamento de sistemas externos (CRM, ERP, core bancário, sistemas legados).

**Casos de uso típicos:**
- Cobrança outbound: contatar lista de clientes via WhatsApp/SMS, coletar promessa de pagamento, acionar sistema de cobrança
- Onboarding de produto: coletar documentos, validar dados, aguardar aprovação de crédito, notificar cliente
- Pesquisa pós-atendimento: NPS automatizado após encerramento da sessão humana, com análise de resposta
- Aprovação de pedidos/contratos: coletar assinatura eletrônica, aguardar aprovação interna, confirmar execução

**Dores principais:**
- BPMs tradicionais (Camunda, Pega) têm steps rígidos sem inteligência adaptativa
- Frameworks de agente IA (LangGraph, CrewAI) não têm canais de comunicação nativos com cliente
- Aprovações e coletas assíncronas exigem código custom em qualquer plataforma
- Billing por execução (n8n, LangGraph) é imprevisível em processos com volume variável

**Como o PlugHub responde:**
- `suspend` step com timers em horas úteis para aprovações com prazo
- `collect` step para contato outbound multicanal com resposta assíncrona
- Integração com BPMs externos via MCP — o BPM aciona um flow e recebe o outcome de volta
- Workflow API com webhook triggers autenticados para sistemas externos

---

### 3. Integradores e Parceiros Tecnológicos

**Perfil:** SIs, consultorias e empresas de software que constroem soluções customizadas sobre infraestrutura de orquestração para clientes finais.

**Necessidades:**
- Multi-tenancy: cada cliente é um tenant isolado com configuração própria
- Framework-agnosticism: clientes podem trazer seus próprios agentes (LangGraph, CrewAI, Anthropic SDK, Python genérico)
- Extensibilidade: MCP Servers customizados por domínio de negócio do cliente
- White-label: portal de operação com visual e idioma adaptáveis
- Contrato de execução estável: agente nativo via SDK ou externo via proxy sidecar — mesmo contrato

**Como o PlugHub responde:**
- `@plughub/sdk` (TypeScript + Python) com contrato de execução formal: `agent_login → agent_ready → agent_busy → agent_done`
- Proxy sidecar (`plughub-sdk proxy`) para agentes externos sem alterar código
- ABAC (Attribute-Based Access Control) granular por módulo e pool para isolamento de acesso
- Config API com namespaces por tenant para override de todos os parâmetros operacionais
- CLI: `plughub-sdk certify`, `plughub-sdk verify-portability`, `plughub-sdk regenerate`

---

## Mensagens por persona

| Persona | Mensagem central |
|---|---|
| Head de Contact Center | "Os CCaaS te dão roteamento maduro mas agente IA engessado. PlugHub te dá motor de fluxo declarativo acoplado a roteador multicritério, com heatmap de sentimento e session replay." |
| CTO / Head de IA | "Traga o agente que quiser — LangGraph, CrewAI, Anthropic SDK, seu próprio em Python. Ele pluga sem mudar uma linha para trocar de LLM, de framework ou de provedor." |
| CFO | "Agentforce e Gemini vendem seat + consumo + tokens + storage + implementação. PlugHub vende capacidade. Você sabe no mês 1 o que vai pagar no mês 13." |
| CISO / DPO | "Mascaramento tokenizado reversível por role, interception guard em todas as chamadas MCP, audit trail LGPD nativo. Compliance não é plugin, é primitive arquitetural." |
| Integrador / SI | "SDK com contrato estável, proxy sidecar para agentes externos, multi-tenancy nativo, MCP Servers customizáveis por domínio. Construa uma vez, entregue para N clientes." |

---

## Verticais prioritárias

| Vertical | Fit com PlugHub | Argumento compliance |
|---|---|---|
| Financeiro (bancos, fintechs, seguros) | Alto — retenção, cobrança, onboarding | LGPD, BACEN, mascaramento de CPF/conta |
| Telecomunicações | Alto — portabilidade, suporte técnico, retenção | ANATEL, mascaramento de dados de rede |
| Saúde | Médio-alto — triagem, agendamento, follow-up | LGPD, CFM, mascaramento de dados de saúde |
| Varejo / e-commerce | Médio — SAC, pós-venda, cobrança | LGPD, PROCON |
| Utilidades (energia, água) | Médio — suporte, cobrança, agendamento de visitas | ANEEL, ANATEL |
