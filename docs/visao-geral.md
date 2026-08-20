# PlugHub — Visão Geral Técnica da Plataforma

> Última atualização: 2026-05-25 · Estado da plataforma: Arc 16 · Veredito de revisão: ATUAL
> Documento técnico de referência do wiki. Para a visão comercial, ver [`product/overview.md`](product/overview.md).

Este documento é o mapa técnico do PlugHub: percorre todas as capacidades da plataforma e aponta, em cada tópico, para a documentação detalhada (`arcos/`, `guias/`, `pacotes/`, `adr/`). É mais técnico e detalhado que os documentos de produto e serve como ponto de partida para integradores, arquitetos e desenvolvedores.

---

## 1. O que é o PlugHub

O PlugHub é uma **plataforma de orquestração enterprise construída para a era dos agentes de IA**. Conecta agentes — humanos e de IA, de qualquer origem ou framework — a sistemas de negócio e a clientes, com qualidade mensurável e sem criar lock-in. Não é um agente nem um framework: é a camada de controle que torna a entrega de serviço possível, governável e auditável.

A plataforma orquestra **toda a jornada de atendimento**, não apenas uma interação isolada. Um cliente que liga, recebe um retorno por WhatsApp dois dias depois e finaliza por webchat está, do ponto de vista do PlugHub, dentro de uma única **Journey** — e todo o contexto é preservado entre os contatos.

Quatro dimensões complementares definem a plataforma:

**Acesso omnichannel.** Abstrai e normaliza todos os canais de comunicação — voz/PSTN, SMS, WhatsApp, webchat e WebRTC — entregando ao núcleo um envelope de evento uniforme. Nenhum agente precisa conhecer o protocolo do canal.

**Orquestração de agentes e jornadas.** Gerencia o ciclo de vida de agentes humanos e de IA, decide quem atende cada conversa, executa fluxos declarativos (Skill Flow) e costura múltiplos contatos numa Journey contínua.

**Integração com sistemas externos.** Via MCP, conecta-se bidirecionalmente ao ecossistema do tenant — CRM, ERP, cobrança, BPMs externos — com autorização granular e auditoria em cada chamada.

**Qualidade e auditoria nativas.** Cada atendimento é avaliável por critérios configuráveis, os mesmos aplicados a agentes de IA e humanos; cada decisão, transação e linha de raciocínio é rastreável por design.

---

## 2. Princípios arquiteturais

**Event-driven first.** Toda comunicação entre componentes passa pelo Kafka. Nenhum componente chama outro diretamente de forma síncrona, exceto onde a latência é crítica.

**Stateless por padrão.** Agentes de IA, gateways e roteadores são stateless. O estado vive no Redis e no Kafka, não nos processos. Agentes que mantêm estado interno entre turnos declaram `execution_model: stateful` e o Routing Engine garante afinidade de sessão.

**Degradação graciosa.** Cada componente tem comportamento definido em caso de falha de seus dependentes. Não há falha catastrófica.

**Canal-aware.** O contexto do canal trafega com cada evento. Nenhum componente ignora as restrições físicas do canal de origem.

**Menor privilégio.** Agentes acessam sistemas de negócio exclusivamente via MCP Servers autorizados, com permissão validada por chamada.

**Observabilidade nativa.** Toda decisão de agente, handoff e ação em sistema de negócio é rastreável por design.

---

## 3. Invariantes — nunca violar

| Invariante | Consequência se violado |
|---|---|
| AI Gateway é stateless — processa um turno por chamada LLM | Inconsistência de parâmetros de sessão entre turnos |
| Routing Engine é o único árbitro de alocação | Conversas roteadas fora do audit log |
| MCP é o único protocolo de integração entre componentes internos | Chamadas diretas REST sem autorização nem auditoria |
| `pipeline_state` persiste no Redis a cada transição de step | Perda de estado em falha do orquestrador |
| Contrato do agente: `agent_login` → `agent_ready` → `agent_busy` → `agent_done` | Instâncias fantasma no pool, conversas abertas indefinidamente |
| `agent_done` exige `handoff_reason` quando `outcome !== "resolved"` | Analytics e escalações sem contexto de motivo |
| `issue_status` é sempre obrigatório no `agent_done` | Relatórios de qualidade sem dados |
| Agentes nunca acessam sistemas backend diretamente — só via MCP Servers autorizados | Ações não auditadas, sem controle de permissão por tenant |
| Toda chamada MCP de domínio é interceptada (`McpInterceptor` ou proxy sidecar) | Chamadas sem validação de permissão, injection guard ou auditoria |
| Todo XADD no canonical stream passa por `writeStreamEntry()` | Eventos sem `event_id`, `segment_id` ou validação Zod |
| `insight.historico.*` persiste via Kafka, nunca por escrita direta no PostgreSQL | Granularidade de persistência violada |

A lista completa de invariantes operacionais está no `CLAUDE.md` do repositório.

---

## 4. Modelo de sessão unificado

Todo contato é uma **sala de conferência**. O Core cria a sessão a cada novo contato; agentes entram na sala com suas filas e recebem mensagens conforme as opções de visibilidade. Agentes de IA e humanos são **simétricos** neste modelo.

### Papéis de participante

| Papel | Descrição |
|---|---|
| `primary` | Agente principal responsável pela interação |
| `specialist` | Especialista convidado (step de tarefa, modo assist) |
| `supervisor` | Supervisor humano ou de IA monitorando a sessão |
| `evaluator` | Agente de qualidade avaliando a sessão |
| `reviewer` | Agente humano revisando a saída do evaluator |

### Status da sessão e motivos de encerramento

`active` (em andamento), `closed` (encerrada normalmente), `abandoned` (nenhum agente entrou). O domínio `close_reason` cobre `no_resource`, `max_wait_exceeded`, `customer_disconnect`, `customer_hangup`, `customer_abandon`, `flow_complete`, `agent_transfer`, `agent_hangup`, `session_timeout` e `system_error`.

### Visibilidade de mensagens

`all` (todos, incluindo o cliente), `agents_only` (todos os agentes, sem o cliente) ou um array de `participant_id` (mensagem privada para participantes específicos).

### Canonical stream

`session:{id}:stream` é a fonte única da verdade de todos os eventos da sessão. Mensagens carregam `content` (mascarado) e `original_content` (não mascarado, visível só a papéis autorizados para auditoria LGPD).

O modelo de conferência opera em **três camadas independentes** — ciclo de vida do contato, ciclo de vida do segmento de cada agente e infraestrutura da conferência. Detalhes em [`arcos/session-conference-lifecycle.md`](arcos/session-conference-lifecycle.md) e [`guias/conference-mechanics.md`](guias/conference-mechanics.md).

---

## 5. Journey — orquestração da jornada completa

A **Journey** é a unidade de serviço que transcende a sessão: agrupa todos os contatos de um mesmo processo de atendimento — o "connect the dots" dos contatos. É o nível acima de Session na hierarquia de observabilidade:

```
Journey  →  Session (Contact)  →  Segment (atendimento por um agente)
```

- **Journey** — o processo de negócio inteiro, multi-sessão e multi-canal.
- **Session / Contact** — cada contato que mapeia uma etapa do atendimento.
- **Segment** — o atendimento prestado por um agente dentro de um contato (ver [`arcos/arc5-segments.md`](arcos/arc5-segments.md)).

**Preservação de contexto.** Em caso de queda, espera ou necessidade de retorno assíncrono, todo o contexto é preservado até a resolução ou o próximo contato. O namespace `@ctx.journey.*` é um hash Redis compartilhado entre todas as sessões da Journey, com TTL de 30 dias.

**Troca de canais em qualquer etapa.** O step `collect` aceita `requires: [text|audio|video|file_upload|masked_input|rich_menu]` em vez de um canal fixo; o Channel Gateway negocia o canal de saída pela matriz de capacidades e pela preferência do cliente.

**Retomada.** Workflows em aberto podem ser retomados a qualquer momento — por iniciativa de um agente de IA ou humano, ou automaticamente no próximo contato inbound do cliente em qualquer canal suportado (Inbound Journey Resume).

Documentação detalhada: [`arcos/arc10-journey.md`](arcos/arc10-journey.md) (modelo Journey, fases A–F), [`arcos/arc16-flow-orchestration.md`](arcos/arc16-flow-orchestration.md) (orquestração em três camadas, channel capability negotiation) e o módulo de UI [`modulos/processos.md`](modulos/processos.md).

---

## 6. Canais suportados

O canal é um **filtro rígido** de roteamento (match obrigatório); o medium (`voice`, `video`, `message`, `email`) é um fator de score.

| Canal | Estado | Outbound | Documentação |
|---|---|---|---|
| `webchat` | Implementado | Não | [`adr/adr-webchat-channel.md`](adr/adr-webchat-channel.md), [`pacotes/channel-gateway-webchat.md`](pacotes/channel-gateway-webchat.md) |
| `whatsapp` | Implementado | Sim | [`arcos/channel-gateway-multi-channel.md`](arcos/channel-gateway-multi-channel.md) |
| `sms` | Implementado (via provedores externos) | Sim | [`arcos/channel-gateway-multi-channel.md`](arcos/channel-gateway-multi-channel.md) |
| `email` | Implementado | Sim | [`arcos/channel-gateway-multi-channel.md`](arcos/channel-gateway-multi-channel.md) |
| `webrtc` | ⚠️ **Sinalização sim, MÍDIA não** — SFU nunca provisionado; ver correção abaixo | Não | [`arcos/arc15-webrtc.md`](arcos/arc15-webrtc.md), [`adr/adr-voice-media-plane.md`](adr/adr-voice-media-plane.md) |
| `voice` / PSTN | ⚠️ **NÃO FUNCIONA** — ver correção abaixo | — | [`adr/adr-voice-media-plane.md`](adr/adr-voice-media-plane.md) |

**WebRTC com negociação de canais** *(⚠️ projeto — o plano de sinalização roda, o de mídia nunca foi provisionado: zero serviço LiveKit em compose algum, SDK fora de `channel-gateway/pyproject.toml:6-23`, e sem credencial o provider entra em `_dev_mode` devolvendo token e sala placebo — `webrtc_provider.py:167`)*. O canal WebRTC (browser-to-SFU, LiveKit self-hosted) negocia o medium em tempo real com fallback **vídeo → voz → texto**. Agentes de IA atendem em texto; agentes humanos atendem em vídeo, voz e texto. A negociação considera as `media_capabilities` do agente e a ordem de fallback do pool. Ver [`arcos/arc15-webrtc.md`](arcos/arc15-webrtc.md).

> ⚠️ **Correção de 2026-08-19 — medido.** Esta tabela classificou `voice`/PSTN como *"Implementado"*
> desde a auditoria de 2026-05, e a seção abaixo descreve um canal que **não roda**. Medido:
> `VoiceAdapter.handle_inbound` chama **cinco** métodos que não existem em lugar nenhum de
> `packages/channel-gateway` — `_open_session`, `_route_inbound`, `_publish_inbound`,
> `_normalize_text`, `_normalize_menu_result` (`adapters/voice.py:236,247,433,558,565`; ausentes em
> `adapters/base.py:44-77`). Os cinco são **mockados** em `tests/test_voice_adapter.py:116-121`, que é
> por isso que a suíte é verde. Em runtime real o inbound levanta `AttributeError` **antes de publicar
> qualquer coisa** em `conversations.inbound`, e não há uma única sessão de voz no ambiente.
>
> Correlatos no mesmo adapter: `channel_name` em vez de `channel` (`voice.py:90`, viola a ABC);
> `stt_queue` nunca drenada e `_handle_stt_result` sem chamador ⇒ **collect por voz morto**, só DTMF
> (`voice.py:624-629,657`); `hangup` lê chave nunca escrita (`voice.py:884`); `_get_contact_id`
> retorna `None` por construção (`voice.py:1032-1037`); `deliver_outbound` nunca invocado
> (`voice.py:772` vs `outbound_consumer.py:95-106`).
>
> **O desenho descrito abaixo continua válido e é reaproveitado** — o que não existe é a execução.
> A reconstrução vive em [`adr/adr-voice-media-plane.md`](adr/adr-voice-media-plane.md), onde as
> interfaces de provider, o `FallbackSTTProvider`/`FallbackTTSProvider` e a gravação por segmento com
> aviso LGPD são preservados. Leia o que segue como **projeto**, não como estado.

**Voz/PSTN com STT e TTS.** O canal `voice` opera sobre um tronco PSTN externo: o `VoiceAdapter` faz a ponte entre o plano de eventos da plataforma e o plano de mídia (conference room do CPaaS). Os provedores são abstraídos por interfaces (`IVoiceProvider`, `ISTTProvider`, `ITTSProvider`), trocáveis sem refatorar o adapter:

- **CPaaS / tronco PSTN:** Twilio (`TwilioProvider`) — controle de chamada e conference. O Twilio é exclusivamente o tronco de voz, não produz TTS no plano de dados.
- **STT (fala do cliente → texto):** Deepgram (`DeepgramSTTProvider`, WebSocket streaming) como primário, com `MockSTTProvider` de fallback silencioso. Como o agente de IA é sempre texto, o STT converte a fala na entrada.
- **TTS (texto da IA → fala):** ElevenLabs (`ElevenLabsTTSProvider`) como primário, Deepgram Aura como alternativo de alta qualidade, e Twilio Say (Amazon Polly) como último recurso que nunca falha — orquestrados por um `FallbackTTSProvider`.

O STT do agente humano é opcional e "ligável" por configuração. O canal `voice` tem outbound: o step `collect` aciona uma ligação de saída via `TwilioVoiceProvider.create_call`. Detalhe completo em [`arcos/channel-gateway-multi-channel.md`](arcos/channel-gateway-multi-channel.md) § 9.

**Canais com e sem outbound.** Canais com outbound (WhatsApp, SMS, email, voz) executam tentativas de contato e fluxos de retorno assíncrono. Em canais sem outbound (webchat, WebRTC), um fluxo que precise contatar o cliente tenta outros canais com outbound; não havendo, fica pendente e disponível para retomada no primeiro contato inbound.

A normalização inbound, a renderização outbound e a negociação de capacidade vivem exclusivamente no `channel-gateway` — ver [`pacotes/channel-gateway.md`](pacotes/channel-gateway.md) e a camada [`layers/01-channel-layer.md`](layers/01-channel-layer.md).

---

## 7. MCP Server PlugHub e interceptação

O `mcp-server-plughub` é o Agent Runtime: expõe as ferramentas (tools) que os agentes usam — sessão, conferência, contexto, Journey, avaliação, eventos de negócio e tools operacionais. Ele apenas expõe tools; **nenhuma lógica de negócio reside nele**.

**Interceptação MCP — modelo híbrido de proxy.** Toda chamada a um domain MCP Server (`mcp-server-crm`, `mcp-server-telco`, etc., operados pelo tenant) é interceptada:

| Tipo de agente | Mecanismo | Salto de rede |
|---|---|---|
| Agente nativo (SDK) | `McpInterceptor` in-process (`@plughub/sdk`) | Nenhum |
| Agente externo (LangGraph, CrewAI) | `plughub-sdk proxy` sidecar em `localhost:7422` | Apenas loopback |

Cada chamada (< 1 ms) passa por: validação de permissão (decode local do JWT) → injection guard (13+ padrões heurísticos) → registro de auditoria (Kafka `mcp.audit`, fire-and-forget). A política de auditoria é definida por tool — o chamador não pode optar por sair dela (LGPD).

Documentação: [`pacotes/mcp-server-plughub.md`](pacotes/mcp-server-plughub.md), [`layers/06-mcp-layer.md`](layers/06-mcp-layer.md), [`pacotes/sdk.md`](pacotes/sdk.md). O segundo MCP Server da plataforma é o `mcp-server-knowledge` (base de conhecimento vetorial para RAG, ver [`arcos/arc6-evaluation.md`](arcos/arc6-evaluation.md)).

---

## 8. Skill Flow — ferramenta única de design de fluxos e workflows

O Skill Flow é a ferramenta única de design tanto de fluxos de atendimento quanto de workflows de negócio. O modelo de uso incentiva a modelagem voltada ao **compartilhamento de agentes especialistas** entre orquestradores de IA e humanos.

### Os 14 tipos de step

| Tipo | O que faz |
|---|---|
| `task` | Delega subtarefa a um agente via A2A (`assist`/`transfer`) |
| `choice` | Ramificação condicional via JSONPath |
| `catch` | Retry e fallback antes de escalação |
| `escalate` | Roteia para um pool |
| `complete` | Encerra o pipeline com outcome definido |
| `invoke` | Chama uma tool MCP diretamente |
| `reason` | Invoca o AI Gateway com `output_schema` |
| `notify` | *(depreciado no Arc 16 — usar `invoke: notification_send`)* |
| `menu` | Captura input do cliente e suspende até a resposta |
| `suspend` | Suspende o workflow até um sinal externo |
| `collect` | Contata um alvo por canal e aguarda resposta |
| `resolve` | Acumulação de contexto inline (pipeline de 5 fases) |
| `begin_transaction` / `end_transaction` | Bloco atômico de masked input |
| `receive` | Suspende aguardando a próxima mensagem do stream |

### Ciclo de vida de deploy

O Skill Flow suporta o ciclo de deploy completo: **controle de versão** (`skill_{name}_v{n}`, campo `deploy_status` draft/published), **agendamento de deploy** (workflow `skill_scheduled_deploy_v1`), **hot deployment** (skill hot-reload de 3 elos — sem reinício), **graceful shutdown** de contatos em andamento e **rollback de versão** (`skill_deployments` table). O `PUT /v1/skills` nunca altera `deploy_status`; apenas `POST /v1/skills/:id/deploy` publica.

Documentação: [`pacotes/skill-flow-engine.md`](pacotes/skill-flow-engine.md), [`modulos/agentflow.md`](modulos/agentflow.md), [`arcos/arc4-workflow.md`](arcos/arc4-workflow.md) (Skill Deploy lifecycle), [`arcos/instance-bootstrap.md`](arcos/instance-bootstrap.md) (hot-reload, reconciliação).

---

## 9. Outbound e workflows de negócio

O outbound é baseado em workflows e **compartilha todas as ferramentas e agentes especialistas** do inbound. Um workflow de negócio (Tier 1) é channel-agnostic e Journey-scoped; delega a workflows de execução (Tier 2) que sequenciam agentes de interação (Tier 3). Ver o modelo de três camadas em [`arcos/arc16-flow-orchestration.md`](arcos/arc16-flow-orchestration.md).

A `workflow-api` (porta 3800) gerencia o ciclo de vida das instâncias de workflow: `/trigger`, `/persist-suspend`, `/resume`, `/complete`, `/fail`, `/cancel`, com timeout scanner em background. O step `collect` cumpre a função de retorno assíncrono; o step `suspend` aguarda aprovação, input, webhook ou timer.

**Webhooks.** Dois padrões — trigger (nova sessão) e resume (retorno de sistema externo) — com token `plughub_wh_{token}` e log de entrega. Ver [`guias/webhook-patterns.md`](guias/webhook-patterns.md) e [`arcos/arc4-workflow.md`](arcos/arc4-workflow.md).

---

## 10. Calendário unificado do sistema

A `calendar-api` (porta 3700) é um motor puro de calendário, consumido por workflows e pelo step `suspend` com `business_hours`. Cobre:

- **Feriados** recorrentes (formato `MM-DD`) e pontuais.
- **Horários de funcionamento** — status de 3 estados: `open`, `closed`, `holiday`, com timezone por tenant.
- **Horário de deploy** — janelas usadas pelo agendamento de deploy de skills.
- **Horário de retomada** — quando um workflow suspenso volta a ser elegível.

Funções principais: `is_open`, `next_open_slot`, `add_business_duration`, `business_duration`, expostas também como 4 tools MCP. Ver [`arcos/arc4-workflow.md`](arcos/arc4-workflow.md).

---

## 11. Agent Routing

O Routing Engine é o **único árbitro de alocação**. O algoritmo combina quatro dimensões:

- **SLA-based** — avaliação preguiçosa do SLA no topo da fila: `min(wait_time / sla_target, max_score)`.
- **Skill-based** — match de competência entre a skill exigida e o `agent_type`.
- **Usage-based** — carga e capacidade do agente/pool; tie-break por fila mais curta.
- **Availability-based** — pausa de agente é filtro rígido; gateways com heartbeat > 90 s são excluídos.

Regras-chave: o canal é filtro rígido; a pausa do agente é filtro rígido; o `close_reason` é detectado pelo próprio Routing Engine (`no_resource` quando não há fila, `max_wait_exceeded` pela avaliação preguiçosa). O **Performance routing** (Arc 7d) mistura competência e `performance_score = resolution_rate × (1 − escalation_rate)`, com peso configurável.

Após cada alocação, o Routing Engine grava o snapshot operacional do pool no Redis e enriquece o ContextStore com `session.pool.*`. Documentação: [`pacotes/routing-engine.md`](pacotes/routing-engine.md), [`layers/04-orchestration-layer.md`](layers/04-orchestration-layer.md).

---

## 12. AI Gateway

O AI Gateway é stateless — processa um turno por chamada LLM — e administra o **compartilhamento de todas as contas de API do ambiente**:

- **Multi-conta.** Administra diversas contas de API de IA (`PLUGHUB_ANTHROPIC_API_KEYS`, `PLUGHUB_OPENAI_API_KEYS`), com limites de uso distintos por conta (RPM, TPM, etc.).
- **Designação e fallback pelo chamador.** O `AccountSelector` escolhe a conta de menor score de carga; em 429/529 marca a conta como throttled e faz fallback para a próxima conta e, se necessário, cross-provider.
- **Perfis de modelo.** `realtime` (Sonnet → gpt-4o), `balanced` (Haiku → gpt-4o-mini) e `evaluation` (Haiku isolado do realtime).

Isso dá **controle de consumo de IA**: cada chamador designa contas e fallbacks, e os limites por tempo/token são administrados por conta. Documentação: [`arcos/ai-gateway.md`](arcos/ai-gateway.md), [`pacotes/ai-gateway.md`](pacotes/ai-gateway.md), [`adr/adr-ai-gateway-separation.md`](adr/adr-ai-gateway-separation.md).

---

## 13. Agentes — orquestradores, especialistas e console humano

### Orquestradores de IA e humanos

Orquestradores — de IA ou humanos — conduzem o atendimento com a **lógica definida pelos fluxos** (Skill Flow), garantindo comportamento previsível e controle de consumo de IA. Agentes de IA e humanos são simétricos no modelo de sessão.

### Agentes especialistas

Agentes especialistas podem participar de **qualquer etapa e momento** do atendimento e são **compartilhados entre orquestradores de IA e humanos**. São convocados via step `task` (A2A `assist`/`transfer`) ou via `@mention` — ver [`guias/mention-protocol.md`](guias/mention-protocol.md).

### Deployment de versão de agentes e workflows

Hot deploy sem parada, agendamento de deploy, graceful shutdown para atendimento em andamento e rollback de versão. O ambiente de homologação/teste é apartado: uma vez validada, a versão é promovida para produção. Ver [`arcos/instance-bootstrap.md`](arcos/instance-bootstrap.md) e a seção 8.

### Console de agentes humanos

O Console é a ferramenta de atendimento e orquestração voltada à produtividade, em que o operador humano dirige, delega e monitora agentes de IA como coparticipantes de primeira classe:

- Acesso a recursos distintos conforme o pool de entrada do contato.
- **Histórico completo das jornadas** — incluindo não finalizadas — com busca de qualquer Journey no histórico.
- **Oferta de retomada** de qualquer Journey em aberto e capacidade de **iniciar novas Journeys**.
- Cartões de participantes de IA em tempo real, ação "Adicionar Especialista", "Delegar Tarefa" e a aba de Orquestração para supervisores.

Documentação: [`arcos/arc11-console-orchestration.md`](arcos/arc11-console-orchestration.md), [`arcos/arc11-phase2-console-redesign.md`](arcos/arc11-phase2-console-redesign.md), [`modulos/agent-assist.md`](modulos/agent-assist.md), [`modulos/contatos.md`](modulos/contatos.md).

---

## 14. Masking — LGPD secure by design

O mascaramento de dados é nativo e by design:

- **Por categoria de dado.** Tokens no stream no formato `[{category}:{token_id}:{display_partial}]` (ex.: `[cpf:tk_b7d2:***-00]`).
- **Por perfil e por papel no contato.** O acesso ao `original_content` é restrito a `authorized_roles` (padrão `evaluator`, `reviewer`); o Channel Gateway reduz ao `display_partial` antes da entrega ao cliente.
- **Na base histórica.** O stream armazena `content` mascarado e `original_content`; o módulo de Auditoria LGPD dá acesso escalonado e auditado ao DPO.
- **Masked Input.** Captura segura de PINs/senhas com `begin_transaction`/`end_transaction`; o namespace `@masked.*` é in-memory e nunca escrito em Redis, `pipeline_state`, stream ou logs.
- **Mascaramento de contexto.** Regras dinâmicas `ContextMaskingRule` aplicadas ao ContextStore.

Documentação: [`adr/adr-message-masking.md`](adr/adr-message-masking.md), [`guias/masked-input.md`](guias/masked-input.md), [`guias/context-masking-rules.md`](guias/context-masking-rules.md), [`modulos/mascaramento.md`](modulos/mascaramento.md).

---

## 15. Perfis de usuário e permissões

O controle de acesso combina quatro mecanismos:

- **RBAC** — papéis `operator`, `supervisor`, `admin`, `developer`, `business`.
- **ABAC** — `module_config` no JWT, com 9 módulos (`evaluation`, `contacts`, `billing`, `config`, `skill_flows`, `workflows`, `agent_assist`, `campaigns`, `audit`); cada campo tem `access` (`none|read_only|write_only|read_write`) e `scope[]`. O `PermissionChecker.can(module, field)` valida no frontend e no backend.
- **Pool** — acesso a dados limitado pelos pools acessíveis (`accessible_pools[]` no JWT; filtro row-level na analytics-api).
- **Grupo** — `AgentGroup` é a entidade de organização de pessoas (org chart, ortogonal a Pool); o escopo do supervisor é denormalizado no JWT (`supervised_groups[]`, `supervised_agent_types[]`, `supervised_user_ids[]`) na emissão do token.

A **visibilidade de dados** é controlada pelo masking (seção 14). Documentação: [`arcos/arc7-auth.md`](arcos/arc7-auth.md), [`arcos/arc9-agent-groups.md`](arcos/arc9-agent-groups.md), [`guias/abac-permission-system.md`](guias/abac-permission-system.md), [`modulos/controle-acesso.md`](modulos/controle-acesso.md), [`modulos/grupos.md`](modulos/grupos.md).

---

## 16. Monitoria e relatórios

### Subjetivos

- **Captura de sentimento do cliente em tempo real**, ao longo do tempo. Score-only no Redis durante a sessão, com labels calculados em tempo de leitura por faixas configuráveis por tenant; persistido em `sentiment_timeline JSONB` no fechamento da sessão.

### Objetivos

- Tempos e volumes ao longo do tempo — contatos, atendimentos, recursos e filas. Consolidados no ClickHouse via `analytics.segments`, `session_timeline` e materialized views; expostos por endpoints `/reports/*` da analytics-api.

### Operacionais

- Deploys de versão ao longo do tempo. O Arc 6 Fase 2 usa eventos de deploy como **âncoras temporais** ("deploy epochs"), permitindo comparar performance antes/depois de cada deploy. Ver [`arcos/arc6-phase2-observability.md`](arcos/arc6-phase2-observability.md).

A UI de monitoria e relatórios está em [`modulos/contatos.md`](modulos/contatos.md), [`modulos/dashboards.md`](modulos/dashboards.md) e [`modulos/relatorios-agentes.md`](modulos/relatorios-agentes.md); o sistema de cards configuráveis em [`arcos/dashboard.md`](arcos/dashboard.md); o modelo de segmentos em [`arcos/arc5-segments.md`](arcos/arc5-segments.md).

---

## 17. Quality — avaliação de agentes

A qualidade é parte da operação desde o primeiro dia, sobre os dados que a plataforma já produz.

**Avaliação baseada em formulários** com critérios de avaliação configuráveis, aplicados por **campanhas** que definem: qual formulário usar; avaliação continuada ou de período definido de um pool; percentual de contatos avaliados por agente; períodos de avaliação; e o agendamento do horário de processamento.

**Avaliação por IA e por humanos.** O avaliador de IA (`agente_avaliacao_v1`) pontua cada critério com evidência; o fluxo humano inclui o **Módulo de Contestação e Revisão** (Arc 13): revisor de IA pré-publicação, contestação por dimensão, revisor humano com decisão final, e curadoria/calibração com feedback contínuo ao avaliador via RAG.

**Índices de qualidade** — objetivos (tempos, resolução) e subjetivos (comportamento, conhecimento, agilidade, suporte à resolução, aderência).

**Avaliação comparativa** — entre versões de agentes de IA (KPIs × dados de monitoria operacional correlacionados aos deploys), entre humano × IA e entre humano × humano, sempre gerando feedback para melhoria continuada.

Documentação: [`arcos/arc6-evaluation.md`](arcos/arc6-evaluation.md), [`arcos/arc13-review-contestation.md`](arcos/arc13-review-contestation.md), [`arcos/arc6-phase2-observability.md`](arcos/arc6-phase2-observability.md), [`modulos/avaliacao.md`](modulos/avaliacao.md), [`arcos/session-replayer.md`](arcos/session-replayer.md).

---

## 18. Auditoria

A auditoria cobre quatro eixos:

- **Atendimento** — o canonical stream é a fonte imutável de todos os eventos de sessão.
- **Transações** — toda chamada MCP de domínio gera um `AuditRecord` no tópico `mcp.audit` (`server_name`, `tool_name`, `allowed`, `injection_detected`, `duration_ms`, `source`).
- **Consumo de recursos** — usage metering por dimensão (`sessions`, `messages`, `llm_tokens_*`, etc.) no tópico `usage.events`.
- **Linhas de raciocínio** — steps `reason` e decisões de agente registrados no stream e nos eventos de negócio (`agent.events`, Arc 12).

O módulo **Auditoria LGPD** dá ao DPO/compliance acesso escalonado e auditado (módulo ABAC `audit`): todo acesso a sessões e a chamadas MCP é registrado em log imutável (`audit_access_log`). Documentação: [`arcos/audit-lgpd.md`](arcos/audit-lgpd.md), [`arcos/usage-metering.md`](arcos/usage-metering.md), [`arcos/arc12-agent-business-events.md`](arcos/arc12-agent-business-events.md).

---

## 19. Arquitetura, dados e consolidação

**Alta disponibilidade e alto volume.** A arquitetura event-driven sobre Kafka, com componentes stateless e estado externalizado em Redis, sustenta escala horizontal e degradação graciosa. O **multi-tenant** está no roadmap próximo (isolamento por `tenant_id` já presente em chaves Redis, tópicos e schemas).

**Consolidação e relacionamento de dados.** O ClickHouse é o destino analítico: `analytics.segments`, `session_timeline`, `agent_pause_intervals`, `evaluation_results`/`events`, `journey_events`, `agent_business_events`, `mcp_audit_log`, `audit_access_log`, `deploy_events`, `calibration_events`. A consolidação alimenta relatórios e suporta **exportação**.

**Topologia de persistência:**

| Banco | Uso principal |
|---|---|
| Redis Cluster | Estado de conversa em tempo real, `pipeline_state`, filas, heartbeats, ContextStore, canonical stream |
| PostgreSQL + pgvector | Agent Registry, schemas `auth`/`workflow`/`evaluation`, histórico de conversas, base de conhecimento vetorial |
| ClickHouse | Analytics operacional, audit log, métricas de qualidade |
| Object Storage | Áudio de ligações, anexos, gravações WebRTC, datasets |

Ver [`modelos-de-dados.md`](modelos-de-dados.md), [`layers/07-data-layer.md`](layers/07-data-layer.md), [`guias/context-store.md`](guias/context-store.md).

---

## 20. Estrutura do repositório

Monorepo em `packages/`. Cada pacote tem responsabilidade única e dependências explícitas.

| Pacote | Runtime | Porta | Documentação |
|---|---|---|---|
| `schemas` | Node 20+ | — | [`pacotes/schemas.md`](pacotes/schemas.md) |
| `sdk` | Node / Python | — | [`pacotes/sdk.md`](pacotes/sdk.md) |
| `mcp-server-plughub` | Node 20+ | — | [`pacotes/mcp-server-plughub.md`](pacotes/mcp-server-plughub.md) |
| `skill-flow-engine` | Node 20+ | — | [`pacotes/skill-flow-engine.md`](pacotes/skill-flow-engine.md) |
| `ai-gateway` | Python 3.11+ | — | [`pacotes/ai-gateway.md`](pacotes/ai-gateway.md) |
| `agent-registry` | Node 20+ | — | [`pacotes/agent-registry.md`](pacotes/agent-registry.md) |
| `routing-engine` | Python 3.11+ | — | [`pacotes/routing-engine.md`](pacotes/routing-engine.md) |
| `rules-engine` | Python 3.11+ | — | [`pacotes/rules-engine.md`](pacotes/rules-engine.md) |
| `channel-gateway` | Python 3.11+ | — | [`pacotes/channel-gateway.md`](pacotes/channel-gateway.md) |
| `calendar-api` | Python 3.11+ | 3700 | (ver [`arcos/arc4-workflow.md`](arcos/arc4-workflow.md)) |
| `workflow-api` | Python 3.11+ | 3800 | (ver [`arcos/arc4-workflow.md`](arcos/arc4-workflow.md)) |
| `skill-flow-worker` | Node 20+ | — | (ver [`arcos/arc4-workflow.md`](arcos/arc4-workflow.md)) |
| `auth-api` | Python 3.11+ | 3200 | [`pacotes/auth-api.md`](pacotes/auth-api.md) |
| `evaluation-api` | Python 3.11+ | 3400 | (ver [`arcos/arc6-evaluation.md`](arcos/arc6-evaluation.md)) |
| `pricing-api` | Python 3.11+ | 3900 | (ver [`arcos/pricing.md`](arcos/pricing.md)) |
| `mcp-server-knowledge` | Node 20+ | 3401 | (ver [`arcos/arc6-evaluation.md`](arcos/arc6-evaluation.md)) |
| `analytics-api` | Python 3.11+ | — | [`pacotes/clickhouse-consumer.md`](pacotes/clickhouse-consumer.md) |
| `platform-ui` | React 18 / Vite | — | [`pacotes/platform-ui.md`](pacotes/platform-ui.md) |

`schemas` nunca depende de outro pacote. Pacotes TypeScript nunca dependem de `ai-gateway` (Python). Nunca criar dependências circulares.

---

## 21. Tópicos Kafka

O Kafka é o backbone de eventos. Os principais tópicos:

`conversations.inbound`, `conversations.routed`, `conversations.queued`, `conversations.abandoned`, `conversations.session_opened/closed`, `conversations.message_sent`, `conversations.participants`, `agent.done`, `agent.lifecycle`, `registry.changed`, `config.changed`, `gateway.heartbeat`, `queue.position_updated`, `rules.escalation.events`, `rules.shadow.events`, `mcp.audit`, `sentiment.updated`, `evaluation.events`, `calibration.events`, `workflow.events`, `collect.events`, `journey.events`, `usage.events`, `agent.events`, `events.dead_letter`.

Todos os eventos cross-package têm schema Zod em `@plughub/schemas`. Detalhe completo de produtores, consumidores e schemas em [`kafka-eventos.md`](kafka-eventos.md) e [`layers/03-message-bus.md`](layers/03-message-bus.md).

---

## 22. Convenções de nomenclatura

```
skill_id:       skill_{name}_v{n}      →  skill_portabilidade_telco_v2
agent_type_id:  {name}_v{n}            →  agente_retencao_v1
pool_id:        snake_case sem versão  →  retencao_humano
mcp_server:     mcp-server-{name}      →  mcp-server-crm
tool:           snake_case             →  customer_get
insight:        insight.historico.*    →  memória de longo prazo do cliente
                insight.conversa.*     →  gerado na sessão atual, expira no fechamento
```

**Regra de idioma:** todos os identificadores técnicos (rotas, variáveis, funções, tipos, chaves i18n, tópicos Kafka, chaves Redis) são em inglês. Português é permitido apenas em strings de tradução e em IDs de entidade de domínio configurados pelo tenant.

---

## 23. Mapa da documentação

| Quero entender... | Vá para |
|---|---|
| Visão comercial e proposta de valor | [`product/`](product/overview.md) |
| Como cada tela da UI funciona | [`modulos/`](INDEX.md#módulos-funcionais-modulos) |
| Como um Arc/feature funciona internamente | [`arcos/`](INDEX.md#arcos-de-implementação-arcos) |
| Como um pacote funciona internamente | [`pacotes/`](INDEX.md#pacotes-técnicos-pacotes) |
| Padrões transversais (masking, mention, hooks) | [`guias/`](INDEX.md#guias-temáticos-guias) |
| Por que uma decisão arquitetural foi tomada | [`adr/`](INDEX.md#adrs-adr) |
| Eventos Kafka e schemas | [`kafka-eventos.md`](kafka-eventos.md) |
| Modelos de dados e persistência | [`modelos-de-dados.md`](modelos-de-dados.md) |
| Índice completo do acervo | [`INDEX.md`](INDEX.md) |

O `CLAUDE.md` na raiz do repositório é a arquitetura viva e a fonte da verdade para invariantes e regras de implementação.
