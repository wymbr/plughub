# PlugHub — Proposta de Valor e Diferenciadores

> Última atualização: 2026-05-25 · Estado: Arc 16

## Posicionamento

> *"A camada de orquestração neutra entre humanos e IA para contact center enterprise — MCP-first, sem lock-in de framework ou LLM, com billing previsível por instância configurada."*

O PlugHub entra no mercado como uma **camada nova** — não substitui o CCaaS nem o framework de agente, mas orquestra ambos sob um único modelo de sessão, com protocolo de integração padronizado e compliance embutido.

---

## Os seis diferenciais defensáveis

### 1. Igualdade humano/IA no primitivo de roteamento

Todos os nove competidores analisados (Gemini, Agentforce, Genesys, NICE, Five9, Talkdesk, LangGraph, CrewAI, n8n) tratam o humano como "o que recebe o handoff quando o bot falha". O PlugHub trata humano e IA como duas implementações da mesma interface:

- **Mesmo modelo de competência** — pool_id, channel_types, skills, score de performance
- **Mesmo mecanismo de roteamento** — Routing Engine multicritério: SLA, canal, competência, senioridade, performance histórica
- **Mesma sessão** — o "conference room" comporta humano e IA simultaneamente, com visibilidade configurável por participante

Isso resolve o caso real de contact centers onde o humano supervisiona ou assume a sessão **sem que o cliente perceba uma transição**. Não é uma feature — é um modelo de operação.

### 2. MCP-first com interception guard como invariante arquitetural

MCP está sendo adotado como protocolo de integração por Salesforce (Agentforce v3), Talkdesk (agent layer), Google (serviços GCP) e n8n (nó nativo). O que **não** está sendo adotado como primitive é o **guard layer obrigatório** em cada chamada:

| Verificação | PlugHub | Agentforce | Gemini | Talkdesk |
|---|---|---|---|---|
| Permissões do JWT por chamada | ✅ Invariante | Plataforma | Plugin | Não declarado |
| Detecção de injeção de prompt | ✅ 13+ padrões | Trust Layer | Model Armor | Não declarado |
| Audit trail por chamada (Kafka) | ✅ Obrigatório | Opcional | Opcional | Não declarado |
| Mascaramento tokenizado reversível | ✅ Por role | Trust Layer | Model Armor | Não declarado |

No PlugHub, **nenhuma chamada MCP escapa do guard** — seja via `McpInterceptor` in-process (agentes nativos via SDK) ou via `plughub-sdk proxy` sidecar (agentes externos). O overhead é < 1ms por chamada. Isso é argumento direto com CISO/DPO em verticais regulados.

### 3. Ecossistema de especialistas por pool — um único primitivo declarativo

Todos os competidores analisados oferecem alguma versão de "AI copilot": um módulo global com configuração fixa que sugere respostas ao agente humano numa barra lateral. No PlugHub, essa lógica é completamente diferente.

O mesmo primitivo que define o comportamento de um agente IA — o Skill Flow YAML — define também quais especialistas um agente humano tem disponíveis **por pool**. Uma equipe de retenção tem um conjunto de especialistas; uma equipe de SAP técnico tem outro; uma equipe comercial tem um terceiro. A configuração vive em `mentionable_pools`:

```yaml
pools:
  - id: retencao_humano
    mentionable_pools:
      billing:  billing_especialista   # @billing → recruta especialista de faturamento
      juridico: juridico_ia            # @juridico → recruta consultor jurídico IA
      copilot:  copilot_retencao       # @copilot → co-pilot configurado para retenção
```

O especialista convocado não é uma sugestão — é um **participante real da sessão**, roteado pelo mesmo Routing Engine, com regras de visibilidade configuráveis (o cliente pode ou não ver as mensagens). Pode ser convidado como `assist` (conferência paralela, sem o cliente perceber) ou `transfer` (handoff completo).

Além do acionamento manual via `@mention`, os Pool Lifecycle Hooks disparam especialistas automaticamente em pontos do ciclo do agente humano:

| Hook | Uso típico |
|---|---|
| `on_human_start` | Co-pilot ativado automaticamente ao início do atendimento |
| `on_human_end` | Pesquisa NPS + wrap-up IA disparam sem intervenção do agente |
| `post_human` | Processamento batch pós-atendimento (sumarização, tagging) |

O mesmo mecanismo de invocação — recrutar um pool — é acessível tanto ao agente humano quanto ao agente-IA. Um orquestrador IA usa o step `task` para convocar `billing_especialista`; um operador usa `@billing` para convocar o mesmo pool. O especialista não sabe quem o chamou e se comporta de forma idêntica nos dois casos.

Isso permite **fatorar agentes-IA complexos em orquestrador + especialistas reutilizáveis**:

| Componente | Responsabilidade | Chamado por |
|---|---|---|
| `agente_triage_ia_v1` | Triagem, identificação, roteamento | Routing Engine (inbound) |
| `billing_especialista` | Cobrança e negociação | Orquestrador IA (`task` step) + operador (`@billing`) |
| `juridico_ia` | Análise contratual e compliance | Orquestrador IA (`task` step) + operador (`@juridico`) |

Três consequências diretas: **padronização de atendimento** (o especialista se comporta igual para robô e para humano na mesma sessão híbrida); **desenvolvimento e testes uma única vez para múltiplos consumidores** (certificar `billing_especialista` cobre todos os caminhos de invocação); e **trajetória de automação gradual** (a operação começa com humanos usando @mention e migra para orquestrador automático incrementalmente, sem reescrever o especialista).

**Especialistas podem atuar ou sugerir.** O mesmo specialist pode ser declarado como **atuante** (participante visível na sessão, conduz a interação diretamente com o cliente no canal) ou **sugestivo** (roda em background, popula sugestões e ações recomendadas para o agente humano, sem aparecer ao cliente). É o mesmo artefato YAML em dois modos de operação — a escolha vive na declaração, não no código. Um especialista de retenção pode ser atuante numa campanha automatizada e sugestivo num atendimento humano-conduzido. O objetivo das duas formas é o mesmo: **aumentar a produtividade e a capacidade de atendimento do humano** — seja substituindo-o em trechos onde a automação é confiável, seja amplificando sua decisão em tempo real onde o humano deve permanecer no comando.

**Delegação de dados sensíveis sem perda de supervisão.** Um padrão operacional que decorre direto do modelo: o agente humano em conversa pode delegar a captura ou o tratamento de dados sensíveis (cartão de crédito, CPF, credenciais bancárias, dados médicos) a um especialista. O humano **vê o progresso** da operação — etapa atual, status de validação, tempo decorrido — mas **não vê os dados em si**, que ficam mascarados na sua visualização enquanto o especialista, com permissão explícita, opera sobre o conteúdo real. O humano pode **retomar controle a qualquer momento** se o fluxo precisar ajuste. Ao concluir, o especialista entrega de volta apenas o resultado (ex.: `payment_token`, status de validação) — o dado bruto nunca passou pela tela do humano.

Isso resolve em paralelo um conjunto duro de requisitos: **escopo PCI-DSS reduzido** (o operador da conversa não tem acesso ao PAN), **LGPD** (minimização de exposição por role), **SOX** (audit trail completo de quem viu o quê em cada etapa). Para o cliente final não há transferência, não há atrito, não há aviso de "agora você fala com o sistema seguro" — ele continua na mesma conversa, no mesmo canal. Para a operação, há ganho de produtividade porque o humano não precisa sair da conversa para registrar dados sensíveis em sistemas paralelos.

**Visibilidade é configurável por participante, por campo e por role — não binária (masked/unmasked).** Em uma sessão híbrida, a mesma mensagem do cliente pode ter o CPF tokenizado para o agente humano que conduz, em texto pleno para o especialista cadastral que valida, com auditoria completa para o supervisor que monitora, e suprimida no log de avaliação de qualidade. Cada papel vê o necessário; nada mais.

Para replicar tudo isso, um competidor precisaria redesenhar o modelo de sessão — não adicionar uma feature.

### 4. Motor único, primitivo único, billing por concorrência

A divisão típica dos competidores: **um motor para cada tipo de fluxo**. Salesforce roda Atlas Reasoning Engine para agentes, Flow para automação, Marketing Cloud para outbound e Service Cloud para wrap-up — quatro stacks com configuração e billing próprios. Genesys tem Architect para IVR, AI Studio para bots e Outbound Engagement como módulo separado. NICE tem CXone para inbound, Cognigy para conversational AI e Outbound como produto à parte (ex-Mature). Gemini Enterprise tem Agent Engine + CCAI + Vertex Agent Builder.

PlugHub colapsa tudo em **um motor que executa um único primitivo declarativo (Skill Flow YAML)**. Todas as variantes de fluxo seguem o mesmo modelo:

| Tipo de fluxo | Exemplo |
|---|---|
| Inbound | Atendimento conversacional ao cliente que chega |
| Outbound | Campanha de cobrança, onboarding ativo, pesquisa NPS proativa |
| Workflow / processo | Aprovação de crédito, coleta assíncrona multicanal |
| Agente especialista | `billing_especialista`, `juridico_ia`, `wrap_up`, `nps_pos_atendimento` — convocados por `@mention`, step `task` ou Pool Hook |
| Pool Hook | `on_human_start`, `on_human_end`, `post_human` — disparam especialistas automaticamente |

Note que **wrap-up de atendimento, NPS pós-conversa e demais automações de pós-atendimento são especialistas como quaisquer outros** — não há categoria à parte. Cada **pool (fila de espera) customiza todos esses fluxos independentemente**: o pool de retenção tem inbound, outbound, especialistas e hooks diferentes do pool de SAP técnico. A configuração vive em YAML versionado, não em UI fragmentada por módulo. O mesmo motor executa todos — não há "engine de outbound" separado do "engine de inbound" separado do "engine de workflow".

**Billing acompanha a unificação**. A métrica de licenciamento é **agentes simultâneos logados — humanos e IA tratados pela mesma unidade**. É exatamente o modelo de "concurrent agent license" que o comprador enterprise já conhece de CCaaS tradicional, estendido para incluir agentes IA na mesma curva. Um agente IA logado é uma instância de skill-flow disponível para receber alocação do Routing Engine; um agente humano logado é um operador disponível na sua estação. Os dois competem pelos mesmos slots da fila e contam pela mesma métrica de licença.

Em pico de carga, o cliente paga pelo que está logado *naquele momento*; em vale, proporcionalmente menos. Não há SKU separado por tipo de fluxo (todos os flows compartilham o pool de licenças), não há "módulo de outbound" com pricing próprio, e não há cobrança por skill-flow implantado mas sem instância logada.

| Modelo | Variáveis de custo | Previsibilidade |
|---|---|---|
| Gemini Enterprise | Seats + runtime GB-hora + tokens + storage + indexação + CCAI para outbound | Muito baixa |
| Agentforce | Actions + créditos + tokens + implementação + Marketing Cloud para outbound | Baixa |
| Genesys | Seats + AI tokens por consumo + Outbound Engagement separado | Média |
| NICE Mpower | Seats + Outbound como produto à parte + Cognigy | Média |
| LangGraph / n8n | Seats + execuções por nó (só engine IA / só workflow) | Baixa |
| **PlugHub** | **Licenças simultâneas de agentes logados (humanos + IA)** | **Alta** |

"Bill shock" — documentado como o principal problema de adoção do Agentforce — é impossível por design. Para o CFO, isso resolve previsibilidade ("compro N licenças concurrent, sei o que pago no pico"), capacity planning ("sigo o pico de agentes logados, não a soma de tudo configurado") e simplicidade ("um SKU, um SLA, uma curva de utilização"). Para a operação, elimina a complexidade de operar dois ou três produtos com configurações fragmentadas e times distintos.

### 5. Outbound unificado — sem módulo separado

Todo CCaaS no mercado vende inbound e outbound como dois produtos com modelos de configuração distintos. Genesys, NICE e Talkdesk têm "Outbound Campaigns" como módulo licenciado à parte; Five9 tem outbound nativo mas em configuração separada do inbound; Salesforce Agentforce praticamente não tem outbound fora do Marketing Cloud.

No PlugHub o motor é um só. Uma campanha de cobrança, pesquisa NPS ou onboarding com etapas manuais é um Skill Flow declarativo, idêntico em primitivos a um atendimento inbound. O step `collect` inicia o contato outbound assíncrono multicanal (WhatsApp, e-mail, SMS e voz/PSTN). O canal de **voz/PSTN** (tronco Twilio, STT Deepgram, TTS ElevenLabs) e o canal **WebRTC** (Arc 15) cobrem o atendimento por áudio com gravação, STT e TTS server-side.

> **Roadmap.** O canal de voz/PSTN já está entregue (tronco Twilio, com STT e TTS server-side). O que permanece **planejado** é o **dialer preditivo** — loop de discagem com pacing (power, predictive, progressive, preview), compliance guard de abandonment ratio TCPA/LGPD e listas DNC. Uma decisão arquitetural em aberto avalia ainda fazer a ponte PSTN → WebRTC via LiveKit SIP Ingress para unificar os canais de áudio.

A consequência arquitetural elegante: **após a conexão, o fluxo outbound é tratado como inbound, sem diferenciação**. Mesmo context package, mesmo roteamento, mesmo session replay, mesma avaliação de qualidade. O agente que atende não precisa saber se o contato foi inbound ou outbound — a experiência operacional é uniforme.

Quando o dialer for entregue, a compliance regulatória (abandonment ratio TCPA/LGPD, listas DNC, janela horária por timezone do contato) será **invariante do motor**, não responsabilidade do YAML — um cliente que configure errado o flow não conseguirá violar regulação, porque o guard fica no media gateway.

Para o comprador: uma licença, uma operação, uma configuração, um SLA. Para o CFO: redução de custo de licença e de complexidade operacional comparada a operar dialer separado.

### 6. Journey — gestão lifecycle-centric

A indústria divide-se historicamente entre dois mental models. **CCaaS é interaction-centric**: mede interações individuais (chamada, chat, ticket) e os KPIs são AHT, FCR e SLA por interação. **CRM é record-centric**: mantém cases como registros e a interação é um campo no registro. Os dois mundos coexistem mal — uma "case" no Service Cloud pode agrupar seis interações, mas o roteamento, SLA e qualidade continuam medidos por interação.

Pointillist (adquirida pela Genesys) e Adobe Customer Journey Analytics tentam ser camada de analytics de jornada, mas sem amarração operacional ao roteador. Pega tem cases operacionais, mas é BPM, não contact center.

PlugHub trata **Journey como primitive simultaneamente operacional e analítica**:

| Dimensão | Como funciona |
|---|---|
| **Operacional** | Routing Engine conhece a jornada — pode preferir o especialista que já atendeu o contato anterior; SLA é medido na jornada, não no contato individual; ContextStore agrupa estado da jornada inteira |
| **Analítica** | Relatórios em ClickHouse rolam para nível de jornada (TTR, contagem de contatos, distribuição de canais, custo total) e drillam até turno individual |
| **Multi-canal e multi-sessão** | Uma jornada de onboarding atravessa webchat → e-mail → SMS → voz → callback em sessões e dias diferentes, e ainda é tratada como unidade coerente |
| **Multi-contato** | Inbound e outbound da mesma jornada compartilham `journey_id`; relatórios cruzam ambos lados naturalmente |

Essa é a virada de categoria: o PlugHub não compete como "contact center" nem como "CRM". Compete como **gestão de lifecycle**. Para casos de uso onde o registro central é o processo (cobrança, onboarding, retenção, suporte recorrente), isso **torna o CRM redundante**; para atendimento ad-hoc, **comoditiza o CCaaS** ao transformar interação em instância dentro de uma jornada.

Para o comprador enterprise, a pergunta troca. Não é mais "qual é meu melhor CCaaS?" ou "qual é meu melhor CRM?" — é "como gerencio o lifecycle do cliente como uma unidade coerente, com analytics e operação no mesmo lugar?".

---

## Diferenciais secundários

**Skill Flow com 14 tipos de step declarativos** incluindo `suspend` (timers em horas úteis via Calendar API), `collect` (contato outbound assíncrono multicanal), `resolve` (acumulação de contexto inline), `receive` (aguarda a próxima mensagem do stream sem prompt) e `begin_transaction`/`end_transaction` (captura segura de dados sensíveis em bloco atômico). Nenhum CCaaS analisado tem motor de fluxo comparável em expressividade declarativa.

**Journey — processo multi-sessão sem CRM externo**: unidade de serviço acima da sessão que agrupa todo o histórico de um processo num único `journey_id`, com KPIs de resolução mensuráveis por tipo de processo. Equivale ao "case" de CRMs enterprise, mas nativo ao roteador — sem integração adicional.

**Agent Groups com escopo de supervisor por grupo, turno e módulo**: supervisores recebem no JWT um escopo filtrado pelos grupos e turnos ativos (`supervised_agent_types[]`) que **cada módulo aplica automaticamente** — Contatos, Avaliação, Dashboards e Relatórios pré-filtram dados, listas de agentes e filas para o escopo autorizado. O supervisor não vê dados fora do grupo, não acessa pools que não supervisiona, não intervém em sessões de outros pools. Granularidade que o Enlighten Actions da NICE não oferece sem customização extensa.

**Session Replayer com Comparison Mode** permite diff turn-a-turn entre duas execuções usando similaridade de Jaccard. Útil para avaliação de agentes IA pré-produção e para auditoria de mudanças de prompt.

**ContextStore unificado** persiste estado de sessão no Redis com namespaces por escopo (`caller.*`, `session.*`, `account.*`, `segment.*`), TTL por campo e rastreabilidade de fonte (`mcp_call`, `ai_inferred`, `customer_input`). Qualquer componente lê e escreve no mesmo hash — sem cópia entre agentes.

**AI Gateway com rotação multi-conta** e fallback cross-provider (Anthropic → OpenAI) com score de utilização por chave. Troca de LLM é configuração, não reescrita de agente.

**Platform-UI como shell único** para todos os perfis de operador: supervisor, administrador, operador, desenvolvedor e perfil comercial (business) com gates ABAC por módulo e campo.

---

## O que o PlugHub não é

- **Não é um framework de agente** — não substitui LangGraph, CrewAI ou Anthropic SDK; orquestra agentes construídos com eles
- **Não é um BPM** — não substitui Camunda ou Pega; integra com eles via MCP
- **Não é um LLM** — o AI Gateway é stateless e troca de provedor por configuração
- **Não substitui Workforce Management dedicado** — não inclui forecasting de demanda, scheduling de turnos nem gestão de aderência de WFM clássicos (Verint, Calabrio); integra com WFM externos via MCP quando necessário

O PlugHub combina **canais digitais e de voz — WebRTC e PSTN (CCaaS), motor de orquestração (Skill Flow), runtime de agentes (humanos + IA), gestão de jornada multi-contato e camada de compliance (MCP guard)** em uma stack unificada — substituindo as três a quatro plataformas que tradicionalmente compõem essa função no contact center enterprise. O dialer preditivo (com seu compliance guard) é uma capacidade de roadmap.

---

## Janela de mercado

A análise de Abril 2026 identifica uma **janela estreita** de 12–18 meses antes que incumbentes CCaaS (NICE+Cognigy, Talkdesk+AWS) ou hyperscalers (Salesforce, Google) possam incorporar os três diferenciais simultaneamente. A recomendação estratégica é usar esse período para fechar referências enterprise em verticais regulados e consolidar os diferenciais como certificações (SOC 2, ISO 27001, LGPD).

Veja a [análise competitiva completa](competitive-analysis.md) para detalhes por competidor.
