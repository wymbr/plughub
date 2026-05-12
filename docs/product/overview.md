# PlugHub — Visão Geral da Plataforma

> Última atualização: Maio 2026

## O que é o PlugHub

O **PlugHub** é uma plataforma de orquestração enterprise que conecta agentes humanos e agentes de IA — de qualquer origem — a sistemas de negócio e clientes, com qualidade mensurável e sem criar dependência de fornecedor.

Diferente de contact centers tradicionais (que apenas incluem IA como add-on) e de frameworks dev-first (que não têm tooling operacional), o PlugHub é construído sobre um único primitivo: **a sessão é uma sala de conferência**, e humanos e IA são tratados como participantes iguais pelo roteador.

## Para quem é

O PlugHub atende três perfis de comprador:

**Contact Centers Enterprise** que precisam adicionar agentes de IA ao seu atendimento sem substituir o time humano nem se prender a um único provedor de LLM ou framework.

**Empresas com processos que envolvem aprovação humana, coleta assíncrona e automação multicanal** — fluxos como cobrança outbound, aprovação de crédito, pesquisas NPS pós-atendimento e onboarding com etapas manuais.

**Integradores e parceiros tecnológicos** que constroem soluções customizadas sobre infraestrutura de orquestração robusta, sem reinventar routing, sessão, auditoria e compliance.

## O que a plataforma entrega

### Atendimento omnichannel

Suporte nativo a **WhatsApp, Webchat, Voz (WebRTC), E-mail, SMS, Instagram e Telegram**. O Channel Gateway normaliza cada canal para um envelope de evento uniforme — agentes nunca conhecem o protocolo do canal de origem. Menus são renderizados nativamente (botões no WhatsApp, formulários no Webchat) ou coletados sequencialmente em canais sem suporte nativo.

### Roteamento multicritério

O Routing Engine aloca o agente mais adequado considerando simultaneamente: disponibilidade, canal, competência declarada, senioridade, SLA estimado e — opcionalmente — performance histórica via score calculado em ClickHouse. Humanos e IA competem pelos mesmos slots com as mesmas regras.

### Especialistas IA configuráveis por pool

Cada pool de agentes humanos pode declarar um conjunto próprio de agentes especialistas disponíveis para acionamento durante o atendimento — sem módulo adicional, sem configuração separada. Um pool de retenção acessa especialistas diferentes de um pool de suporte técnico.

O acionamento acontece de duas formas: o agente humano digita `@alias [comando]` para convocar um especialista mid-conversation, ou o pool declara hooks que disparam automaticamente nos momentos certos (`on_human_start` para o co-pilot, `on_human_end` para pesquisa NPS e wrap-up). Em ambos os casos o especialista entra na sessão como participante real — com visibilidade configurável — e o cliente não percebe a transição.

O mesmo Skill Flow YAML que define o comportamento do agente IA define quais comandos ele aceita via `@mention` e quais ações executa ao ser convocado. Não há ferramenta adicional: é o mesmo primitivo do atendimento IA aplicado à produtividade do agente humano.

O ponto mais relevante para operações em escala: o mesmo especialista pode ser convocado por um agente-IA orquestrador (via step `task`) e por um agente humano (via `@mention`) — sem nenhuma adaptação. Isso permite fatorar agentes-IA complexos em orquestrador + especialistas reutilizáveis, padronizar o comportamento entre sessões automáticas e híbridas, e migrar gradualmente de semi-automático para totalmente automático sem reescrever nenhum especialista.

### Skill Flow declarativo

Flows de orquestração são declarados em YAML com 13 tipos de step (`task`, `choice`, `reason`, `invoke`, `notify`, `menu`, `suspend`, `collect`, `escalate`, `complete`, `resolve`, `begin_transaction`, `end_transaction`). Suportam timers em horas úteis via Calendar API, coleta assíncrona multicanal, aprovações e contestações, e captura mascarada de dados sensíveis em bloco atômico (`begin_transaction`/`end_transaction`). O mesmo motor roda tanto flows de atendimento em tempo real quanto workflows de processo batch.

### Automação de processos multicanal

O step `collect` permite que um workflow inicie proativamente um contato — via WhatsApp, e-mail, SMS ou voz — no horário configurado, suspenda aguardando resposta por N horas em horário comercial e retome automaticamente quando o cliente responder (ou escale para um agente humano no timeout). Uma campanha de cobrança, pesquisa NPS ou onboarding com etapas manuais é inteiramente descrita num único YAML, sem módulo separado de outbound.

O mesmo mecanismo suporta **retornos multicanal**: um fluxo iniciado num canal (webchat de atendimento) pode aguardar uma etapa posterior em outro (aprovação por SMS, upload por e-mail). O cliente experimenta continuidade; o ContextStore mantém o estado da sessão independente do canal de retorno.

### Journey — processos que transcendem a sessão

Uma **Journey** é a unidade de serviço acima da sessão individual — agrupa todos os contatos de um mesmo processo de atendimento num único `journey_id`, independente de quantas sessões, dias ou canais o processo atravesse. Um processo de onboarding com análise de crédito, aprovação interna e confirmação com o cliente pode envolver três sessões separadas e ainda ser tratado como uma jornada coesa, com histórico unificado e KPIs de resolução mensuráveis por tipo de processo.

A Journey pode ser iniciada por um agente IA (via MCP tool `journey_start`), por um agente humano (via `@journey:<skill_id>` no Agent Assist) ou automaticamente pela própria skill (flag `creates_journey: true` no YAML). Sessões subsequentes criadas por `collect` steps são vinculadas à Journey automaticamente. Isso é equivalente ao conceito de "case" em CRMs como Salesforce Service Cloud — mas nativo ao roteador, sem exigir CRM externo.

### Integração via MCP com proteção nativa

MCP (Model Context Protocol) é o único protocolo de integração entre agentes e sistemas de negócio. Toda chamada MCP passa obrigatoriamente por um **interception guard** que valida permissões do JWT, detecta injeção de prompt (13+ padrões) e grava audit record no Kafka. Sem exceções — é um invariante arquitetural, não uma opção.

### Avaliação de qualidade nativa

Todo atendimento pode ser avaliado por um Agente Avaliador com formulários configuráveis por campanha, RAG sobre base de conhecimento vetorial, ciclos de revisão e contestação via Workflow API, e relatórios analíticos em ClickHouse. A mesma infraestrutura avalia agentes de IA e agentes humanos.

### Supervisão operacional em tempo real

O Operator Console oferece heatmap de sentimento por pool, drill-down de sessões ativas com transcrição ao vivo, e capacidade de intervenção direta do supervisor sem passar pelo ciclo MCP. O Co-pilot analisa cada turno em background e popula sugestões de resposta e flags de risco para o agente humano.

Supervisores operam com **escopo por grupo e turno**: o sistema de Agent Groups permite definir quais agentes cada supervisor acompanha em cada turno do dia, e esse escopo é resolvido no JWT no momento do login — relatórios, heatmaps e listas de agentes já chegam pré-filtrados, sem configuração adicional por sessão.

### Faturamento por capacidade configurada

Diferente de todas as alternativas do mercado (que faturam por seat + consumo + tokens + storage + implementação), o PlugHub fatura por **capacidade configurada**: número de instâncias de agente ativas. O cliente sabe no mês 1 o que vai pagar no mês 13.

## Arquitetura em uma linha

> Channel Gateway → Kafka → Routing Engine → Skill Flow / Agent → MCP Servers (interceptados) → ContextStore → Analytics

O estado da sessão vive no **Redis** (tempo real), os eventos persistem em **ClickHouse** (analytics) e a configuração em **PostgreSQL** (Agent Registry). O AI Gateway é stateless — processa um turno por chamada LLM, sem estado entre turnos.

## Links rápidos

- [Proposta de valor e diferenciadores](value-proposition.md)
- [Público-alvo e personas](target-audience.md)
- [Análise competitiva](competitive-analysis.md)
- [Mapa de módulos funcionais](../INDEX.md)
