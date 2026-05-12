# PlugHub — Proposta de Valor e Diferenciadores

> Última atualização: Maio 2026

## Posicionamento

> *"A camada de orquestração neutra entre humanos e IA para contact center enterprise — MCP-first, sem lock-in de framework ou LLM, com billing previsível por instância configurada."*

O PlugHub entra no mercado como uma **camada nova** — não substitui o CCaaS nem o framework de agente, mas orquestra ambos sob um único modelo de sessão, com protocolo de integração padronizado e compliance embutido.

---

## Os quatro diferenciais defensáveis

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

Para replicar isso, um competidor precisaria redesenhar o modelo de sessão — não adicionar uma feature.

### 4. Billing por capacidade configurada

| Modelo | Variáveis de custo | Previsibilidade |
|---|---|---|
| Gemini Enterprise | Seats + runtime GB-hora + tokens + storage + indexação | Muito baixa |
| Agentforce (Flex Credits) | Actions + créditos + tokens + implementação por agente | Baixa |
| Genesys | Seats + AI tokens por consumo | Média |
| LangGraph / n8n | Seats + execuções por nó | Baixa |
| **PlugHub** | **Instâncias configuradas** | **Alta** |

O cliente compra N instâncias de agente IA e M agentes humanos. O preço é fixo independente do volume de turnos, tokens ou mensagens. "Bill shock" — documentado como o principal problema de adoção do Agentforce — é impossível por design.

---

## Diferenciais secundários

**Skill Flow com 13 tipos de step declarativos** incluindo `suspend` (timers em horas úteis via Calendar API), `collect` (contato outbound assíncrono multicanal), `resolve` (acumulação de contexto inline) e `begin_transaction`/`end_transaction` (captura segura de dados sensíveis em bloco atômico). Nenhum CCaaS analisado tem motor de fluxo comparável em expressividade declarativa.

**Journey — processo multi-sessão sem CRM externo**: unidade de serviço acima da sessão que agrupa todo o histórico de um processo num único `journey_id`, com KPIs de resolução mensuráveis por tipo de processo. Equivale ao "case" de CRMs enterprise, mas nativo ao roteador — sem integração adicional.

**Agent Groups com escopo de supervisor por turno**: supervisores recebem no JWT um escopo filtrado pelos grupos e turnos ativos (`supervised_agent_types[]`), com granularidade que o Enlighten Actions da NICE não oferece sem customização. Relatórios e dashboards já chegam pré-filtrados sem configuração por sessão.

**Session Replayer com Comparison Mode** permite diff turn-a-turn entre duas execuções usando similaridade de Jaccard. Útil para avaliação de agentes IA pré-produção e para auditoria de mudanças de prompt.

**ContextStore unificado** persiste estado de sessão no Redis com namespaces por escopo (`caller.*`, `session.*`, `account.*`, `segment.*`), TTL por campo e rastreabilidade de fonte (`mcp_call`, `ai_inferred`, `customer_input`). Qualquer componente lê e escreve no mesmo hash — sem cópia entre agentes.

**AI Gateway com rotação multi-conta** e fallback cross-provider (Anthropic → OpenAI) com score de utilização por chave. Troca de LLM é configuração, não reescrita de agente.

**Platform-UI como shell único** para todos os perfis de operador: supervisor, administrador, operador, desenvolvedor e perfil comercial (business) com gates ABAC por módulo e campo.

---

## O que o PlugHub não é

- **Não é um CCaaS** — não inclui PSTN, linha 0800, workforce management, discagem preditiva
- **Não é um framework de agente** — não substitui LangGraph, CrewAI ou Anthropic SDK
- **Não é um BPM** — não substitui Camunda ou Pega; integra com eles via MCP
- **Não é um LLM** — o AI Gateway é stateless e troca de provedor por configuração

O PlugHub é a **camada de orquestração** entre esses mundos.

---

## Janela de mercado

A análise de Abril 2026 identifica uma **janela estreita** de 12–18 meses antes que incumbentes CCaaS (NICE+Cognigy, Talkdesk+AWS) ou hyperscalers (Salesforce, Google) possam incorporar os três diferenciais simultaneamente. A recomendação estratégica é usar esse período para fechar referências enterprise em verticais regulados e consolidar os diferenciais como certificações (SOC 2, ISO 27001, LGPD).

Veja a [análise competitiva completa](competitive-analysis.md) para detalhes por competidor.
