# PlugHub — Visão Geral da Plataforma

> Última atualização: 2026-05-25 · Estado: Arc 16

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

Suporte nativo a **WhatsApp, Webchat, E-mail, SMS, Instagram e Telegram**, além de **WebRTC** (web/mobile) — implementado no Arc 15. O Channel Gateway normaliza cada canal para um envelope de evento uniforme — agentes nunca conhecem o protocolo do canal de origem. Menus são renderizados nativamente (botões no WhatsApp, formulários no Webchat) ou coletados sequencialmente em canais sem suporte nativo.

O canal **WebRTC** é construído sobre um SFU LiveKit self-hosted, com negociação de medium em tempo real (video → voice → text), gravação por egress, STT e TTS server-side. O canal de **voz/PSTN** opera sobre tronco Twilio, com STT (Deepgram) e TTS (ElevenLabs, com fallbacks) — o agente de IA atende em texto, convertido de/para áudio pelo `VoiceAdapter`. Uma decisão arquitetural em aberto avalia, no futuro, fazer a ponte PSTN → WebRTC via LiveKit SIP Ingress para unificar os dois canais de áudio.

### Roteamento multicritério

O Routing Engine aloca o agente mais adequado considerando simultaneamente: disponibilidade, canal, competência declarada, senioridade, SLA estimado e — opcionalmente — performance histórica via score calculado em ClickHouse. Humanos e IA competem pelos mesmos slots com as mesmas regras.

### Especialistas IA configuráveis por pool

Cada pool de agentes humanos pode declarar um conjunto próprio de agentes especialistas disponíveis para acionamento durante o atendimento — sem módulo adicional, sem configuração separada. Um pool de retenção acessa especialistas diferentes de um pool de suporte técnico.

O acionamento acontece de duas formas: o agente humano digita `@alias [comando]` para convocar um especialista mid-conversation, ou o pool declara hooks que disparam automaticamente nos momentos certos (`on_human_start` para o co-pilot, `on_human_end` para pesquisa NPS e wrap-up). Em ambos os casos o especialista entra na sessão como participante real — com visibilidade configurável — e o cliente não percebe a transição.

O mesmo Skill Flow YAML que define o comportamento do agente IA define quais comandos ele aceita via `@mention` e quais ações executa ao ser convocado. Não há ferramenta adicional: é o mesmo primitivo do atendimento IA aplicado à produtividade do agente humano.

O ponto mais relevante para operações em escala: o mesmo especialista pode ser convocado por um agente-IA orquestrador (via step `task`) e por um agente humano (via `@mention`) — sem nenhuma adaptação. Isso permite fatorar agentes-IA complexos em orquestrador + especialistas reutilizáveis, padronizar o comportamento entre sessões automáticas e híbridas, e migrar gradualmente de semi-automático para totalmente automático sem reescrever nenhum especialista.

### Skill Flow declarativo

Flows de orquestração são declarados em YAML com 14 tipos de step (`task`, `choice`, `catch`, `escalate`, `complete`, `invoke`, `reason`, `notify`, `menu`, `suspend`, `collect`, `resolve`, `begin_transaction`/`end_transaction`, `receive`). Suportam timers em horas úteis via Calendar API, coleta assíncrona multicanal, aprovações e contestações, e captura mascarada de dados sensíveis em bloco atômico (`begin_transaction`/`end_transaction`). O mesmo motor roda tanto flows de atendimento em tempo real quanto workflows de processo batch.

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

A plataforma oferece supervisão operacional distribuída pelos módulos relevantes — heatmap de sentimento por pool nos dashboards, drill-down de sessões ativas com transcrição ao vivo no Monitor de Contatos, intervenção direta do supervisor numa sessão sem passar pelo ciclo MCP. O Co-pilot analisa cada turno em background e popula sugestões de resposta e flags de risco para o agente humano.

**O supervisor opera com escopo granular por grupo, turno e módulo**. O sistema de Agent Groups define quais agentes cada supervisor acompanha em cada turno do dia, e esse escopo é resolvido no JWT no login. **Cada módulo (Contatos, Avaliação, Dashboards, Relatórios) aplica o escopo automaticamente** — o supervisor não vê dados de agentes fora do grupo, não acessa filas (pools) que não supervisiona, não pode intervir em sessões de outros pools, e não enxerga relatórios fora do escopo autorizado. Sem configuração por sessão e sem possibilidade de bypass.

### Motor único para todos os fluxos

Inbound, outbound, workflow de processo, agentes especialistas (convocados por `@mention` ou `task` step), wrap-up pós-atendimento e Pool Hooks (`on_human_start`, `on_human_end`, `post_human`) seguem o **mesmo primitivo declarativo (Skill Flow YAML) executado pelo mesmo motor**. Cada pool customiza seu conjunto completo de fluxos independentemente — o pool de retenção tem inbound, outbound, especialistas e wrap-up distintos do pool de SAP técnico. Não há engine separado de outbound, dialer separado, workflow tool separada ou "copilot" como módulo à parte. Um motor, uma configuração, uma versão YAML.

### Faturamento por licenças simultâneas (humanos + IA)

Diferente de todas as alternativas do mercado (que faturam por seat + consumo + tokens + storage + implementação, com SKUs separados para outbound e workflow), o PlugHub fatura por **agentes simultâneos logados — humanos e IA tratados pela mesma unidade**. É exatamente o modelo de "concurrent agent license" que o CCaaS tradicional já usa para operadores humanos, estendido para incluir agentes IA na mesma curva. Inbound, outbound, especialistas (incluindo wrap-up e NPS) compartilham o mesmo pool de licenças. O cliente compra N licenças e sabe exatamente o que pagará no mês 13 — não há cobrança extra por skill-flow implantado-mas-sem-instância-logada, e capacity planning segue o pico real de agentes ativos.

### Tratamento de dados sensíveis com supervisão sem visibilidade

Visibilidade dentro de uma sessão é **configurável por participante, por campo e por role** — não binária. O padrão operacional mais distintivo: o agente humano pode delegar a captura de dados sensíveis (cartão, CPF, credenciais) a um especialista que tem permissão para operar sobre o dado, enquanto o próprio humano vê o progresso da operação mas não o conteúdo. Permite escopo PCI-DSS reduzido, conformidade LGPD/SOX e ainda assim continuidade da conversa sem transferência do cliente.

## Arquitetura em uma linha

> Channel Gateway → Kafka → Routing Engine → Skill Flow / Agent → MCP Servers (interceptados) → ContextStore → Analytics

O estado da sessão vive no **Redis** (tempo real), os eventos persistem em **ClickHouse** (analytics) e a configuração em **PostgreSQL** (Agent Registry). O AI Gateway é stateless — processa um turno por chamada LLM, sem estado entre turnos.

## Links rápidos

- [Proposta de valor e diferenciadores](value-proposition.md)
- [Público-alvo e personas](target-audience.md)
- [Análise competitiva](competitive-analysis.md)
- [Mapa de módulos funcionais](../INDEX.md)
