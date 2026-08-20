# Revisão da Documentação PlugHub — Maio 2026

> Avaliação completa do acervo `docs/` · Data: 2026-05-25 · Estado da plataforma: Arc 16
> Escopo auditado: 123 arquivos `.md` · Fonte da verdade: `CLAUDE.md`, `CHANGELOG.md`, `TODO.md`

> ⚠️ **Superseção parcial de 2026-08-19 — não reescrever, mas não citar como prova.**
>
> Este documento é registro datado de uma auditoria e permanece intacto. Uma de suas conclusões, porém,
> foi **derrubada por medição** e este arquivo é o mais citado como prova dela: a *"Correção pós-auditoria
> (2026-05-25)"* que reclassificou o canal **`voice` de "lacuna aspiracional" para "implementado"**
> (linhas `:127`, `:175`, `:187`, `:266`, `:300`) está **errada**. Medido em 2026-08-19:
> `VoiceAdapter.handle_inbound` chama cinco métodos que não existem em `packages/channel-gateway`
> (`adapters/voice.py:236,247,433,558,565`), mockados em `tests/test_voice_adapter.py:116-121` —
> `AttributeError` em runtime real, e nenhuma sessão de voz jamais existiu no ambiente. Ver
> [`adr/adr-voice-media-plane.md`](adr/adr-voice-media-plane.md).
>
> **A lição de método vale mais que a correção**, e é o motivo de este aviso existir: a reclassificação
> foi feita **lendo `docs/arcos/channel-gateway-multi-channel.md` §9**, que descreve o canal em detalhe
> convincente — e não executando nada. Uma auditoria que compara documento com documento **propaga** a
> afirmação em vez de verificá-la, e produz o pior resultado possível: converte uma dúvida honesta
> ("aspiracional") em uma certeza falsa ("implementado"), que então sobrevive três meses e chega ao
> material comercial.
>
> **Critério para a próxima auditoria:** "implementado" significa *existe caminho executado*, não *existe
> seção no doc*. Onde não houver execução observável, a classificação correta é **inconclusivo** — nunca
> o valor plausível.

---

## 1. Sumário executivo

O acervo `docs/` cumpre o propósito de ser um portal de conhecimento, mas **sofreu drift acumulado**: a documentação foi escrita feature a feature e raramente revisitada quando Arcs posteriores alteraram o que estava descrito. O resultado é um acervo onde **pouco mais da metade dos arquivos está desalinhada** do estado real da plataforma.

| Veredito | Arquivos | % |
|---|---|---|
| **ATUAL** — reflete o estado atual | 26 | 21% |
| **DRIFT-MENOR** — pequenas correções pontuais | 29 | 24% |
| **DRIFT-MAIOR** — reescrita significativa necessária | 35 | 28% |
| **OBSOLETO** — superado, deve ser removido/arquivado | 13 | 11% |
| **HISTÓRICO-OK** — referência congelada, correto manter | 20 | 16% |
| **Total** | 123 | 100% |

**Causa-raiz única:** ausência de um processo de manutenção da documentação acoplado ao ciclo de implementação. O `CLAUDE.md` tem regras de manutenção rígidas (e está atualizado até o Arc 16); o acervo `docs/` não tem equivalente e ficou para trás.

**Os três documentos mais críticos do wiki são justamente os mais defasados:**

- `docs/visao-geral.md` — congelado em "spec v24.0 / 2026-03-31"; descreve 9 tipos de step (são 14), cita o componente inexistente "Notification Agent" e tem **15+ links quebrados**. Ignora Journey, ContextStore, Auth/ABAC, Evaluation, WebRTC e tudo o que veio depois do Arc 4.
- `docs/INDEX.md` — ponto de entrada do wiki; **omite 15+ arquivos reais**, incluindo todos os Arcs 11–16, Audit LGPD e 6 pacotes Python.
- `docs/kafka-eventos.md` e `docs/modelos-de-dados.md` — descrevem ~9 tópicos Kafka de ~30, ignoram o canonical stream e o ContextStore inteiros.

A boa notícia: os documentos dos **Arcs recentes (12, 13, 15, 16)** e a maioria dos **guias temáticos** (`conference-mechanics`, `masked-input`, `mention-protocol`) estão atualizados e bem mantidos. O problema concentra-se em (a) documentos de visão geral/entrada, (b) documentos de pacote (`pacotes/`) e camadas conceituais (`layers/`) congelados no vocabulário da spec v24.0, e (c) propostas que nunca foram conciliadas com o que foi efetivamente construído.

---

## 2. Metodologia

Cada arquivo foi lido integralmente e comparado contra a fonte da verdade do estado atual da plataforma (`CLAUDE.md` — arquitetura viva; `CHANGELOG.md` — histórico de implementação; `TODO.md` — pendências genuínas). Para cada arquivo foram registrados: stamp de data/versão no cabeçalho, drift de conteúdo (afirmações específicas erradas ou superadas), links internos quebrados, cobertura faltante e um veredito.

Critérios de veredito:

- **ATUAL** — reflete o estado atual; no máximo o stamp de data precisa de atualização.
- **DRIFT-MENOR** — partes pontuais desatualizadas, corrigíveis com poucas edições.
- **DRIFT-MAIOR** — substancialmente desatualizado; precisa de reescrita de seções inteiras.
- **OBSOLETO** — conteúdo superado que deve ser removido ou movido para `deprecated/`.
- **HISTÓRICO-OK** — referência histórica congelada intencionalmente (ADRs, extrações da spec v24.0); correto manter como está.

---

## 3. Problemas transversais

Estes padrões aparecem repetidamente e devem ser tratados como itens de processo, não apenas por arquivo.

### 3.1 Ausência de stamp de data/versão

A maioria dos arquivos em `pacotes/`, `arcos/` e **todos os 14 de `modulos/`** não tem cabeçalho de data ou versão. Sem isso é impossível rastrear a idade de um documento. **Recomendação:** adotar um cabeçalho padrão obrigatório em todo documento vivo:

```
> Última atualização: AAAA-MM-DD · Estado: Arc N · Veredito de revisão: ATUAL
```

### 3.2 Links quebrados `modulos/*` → `pacotes/*`

Os documentos de pacote foram movidos de `docs/modulos/` para `docs/pacotes/` em algum ponto, mas as referências antigas não foram corrigidas. Afeta: `visao-geral.md` (15+ links), `layers/04`, `layers/05`, `layers/06` (8 links), `standards/frontend-architecture.md` e `pacotes/platform-ui.md`. Todos os links para `modulos/{schemas,sdk,mcp-server-plughub,skill-flow-engine,ai-gateway,agent-registry,routing-engine,rules-engine}.md` estão quebrados.

### 3.3 Nomenclatura obsoleta repetida

| Termo no doc | Termo canônico atual | Onde aparece |
|---|---|---|
| `PlugHubAdapter` | `McpInterceptor` | visao-geral, layers/05, layers/06, sections/* |
| "Notification Agent" (componente) | step depreciado; usar `invoke: notification_send` | visao-geral, modelos-de-dados, layers/04, channel-gateway, skill-flow-engine |
| `conversations.events` | `conversations.session_opened/closed` + `agent.done` | kafka-eventos, layers/03, mcp-server-plughub, rules-engine |
| `agent.registry.events` | `registry.changed` | kafka-eventos, layers/03, agent-registry, routing-engine |
| `mcp-server-omnichannel` | `mcp-server-plughub` | sections/* (aceitável — histórico) |

### 3.4 Contagem de tipos de step do Skill Flow

Os documentos divergem entre **8, 9, 11 e 13** tipos de step. O estado atual é **14**: `task`, `choice`, `catch`, `escalate`, `complete`, `invoke`, `reason`, `notify`, `menu`, `suspend`, `collect`, `resolve`, `begin_transaction`/`end_transaction`, `receive`. Além disso, `notify` foi **depreciado como step type** no Arc 16 (substituído por `invoke: notification_send`); o sub-campo `notify` dentro de `suspend` permanece válido.

### 3.5 Pacotes reais sem documentação

13 pacotes do monorepo não têm arquivo em `docs/pacotes/`:

`calendar-api`, `workflow-api`, `skill-flow-worker`, `pricing-api`, `evaluation-api`, `mcp-server-knowledge`, `analytics-api`, `orchestrator-bridge`, `usage-aggregator`, `config-api`, `dashboard`, `session-replayer`, `gitagent`.

Vários têm documentação de Arc equivalente em `arcos/` (ex.: `arcos/pricing.md`, `arcos/usage-metering.md`, `arcos/session-replayer.md`), mas falta a entrada formal em `pacotes/` e o INDEX não os reconhece.

### 3.6 Propostas que divergem do que foi construído

Documentos escritos como "proposta" e nunca conciliados com a implementação real, hoje contradizem o estado atual:

- `arcos/journey-analytics.md` — propõe modelo analítico de 4 níveis (journey→contato→sessão→turno) com tipos de evento e tabelas que **não existem**; o Arc 10 implementado usa journey→session→segment. **Veredito: OBSOLETO.**
- `sections/conferencia-e-historico.md` — rascunho "v25.0 proposta" cujo tema já foi implementado e consolidado em `guias/conference-mechanics.md`.
- `arcos/dashboard.md` e `arcos/arc6-phase2-observability.md` — escritos como plano futuro, embora o `CLAUDE.md` os trate como arquitetura ativa.

### 3.7 Auto-contradição interna

Documentos cujo cabeçalho declara "implementado/completo" mas cujo corpo ainda descreve tudo como "proposta/pendente": `arcos/session-conference-lifecycle.md` (gaps G1–G6 descritos como abertos no corpo, todos resolvidos na seção final), `arcos/arc11-phase2-console-redesign.md` (rodapé "Pronto para iniciar Fase A"), `arcos/arc14-posatt-independent-segments.md` (rodapé "Pendente de validação"), `pacotes/platform-ui.md`.

### 3.8 Changelogs no lugar errado

`guias/changelog-2026-04-15.md`, `-04-16.md`, `-04-16b.md`, `-04-29.md` são changelogs históricos já listados na seção `deprecated/` do INDEX, mas continuam fisicamente em `guias/`. Devem ser movidos para `docs/deprecated/guias/`.

### 3.9 `layers/` deixou de ser documentação viva

A pasta `layers/` se propõe a mapear as 9 camadas arquiteturais de forma viva, mas 7 de 9 arquivos estão em DRIFT-MAIOR — congelados no vocabulário da spec v24.0. Requer **decisão editorial**: ou reescrever a fundo derivando do `CLAUDE.md`, ou rebaixá-la explicitamente a "mapa conceitual histórico".

### 3.10 Observação para o time de engenharia (fora do escopo do wiki)

O `CLAUDE.md` **não tem seção própria de Arc 14** (Arcs 13, 15 e 16 têm; o 14 está ausente), embora `arcos/arc14-posatt-independent-segments.md` declare o Arc 14 completo. Além disso, a seção "Session & Conference Lifecycle" do `CLAUDE.md` ainda repete os gaps G1–G6 como "known gaps" quando o próprio `arcos/session-conference-lifecycle.md` mostra os fixes aplicados. Recomenda-se conciliar.

---

## 4. Avaliação por pasta

### 4.1 Raiz `docs/` (5 arquivos)

| Arquivo | Veredito | Principais problemas |
|---|---|---|
| `INDEX.md` | DRIFT-MENOR (crítico) | Omite 15+ arquivos reais: Arcs 11–16, Audit LGPD, 6 pacotes Python, `conference-mechanics.md`, `orchestrator-working-memory.md`. Stamp "v25+ / 2026-05-12". |
| `visao-geral.md` | DRIFT-MAIOR | Congelado em spec v24.0. 9 step types (são 14). "Notification Agent". 15+ links quebrados. Ignora Arcs 5–16. |
| `modelos-de-dados.md` | DRIFT-MAIOR | Sem stamp. Ignora canonical stream e ContextStore. Cobre 1 tabela ClickHouse de ~12. Faltam schemas PostgreSQL de auth/workflow/evaluation. |
| `kafka-eventos.md` | DRIFT-MAIOR | Sem stamp. Lista 9 tópicos de ~30. Nomes obsoletos. `journey.events` marcado "Proposta" com schema errado. |
| `plughub_analise_competitiva_2026.md` | HISTÓRICO-OK | Análise de mercado datada (abr/2026); congelada por design. |

### 4.2 `product/` (4 arquivos)

| Arquivo | Veredito | Principais problemas |
|---|---|---|
| `overview.md` | ATUAL | Sem drift relevante (o canal voz/PSTN citado está de fato implementado). |
| `target-audience.md` | ATUAL | — |
| `value-proposition.md` | ATUAL | Menor: "13 tipos de step" (são 14). Corrigido. |
| `competitive-analysis.md` | ATUAL | — |

A pasta `product/` é a mais saudável do acervo. Foi atualizada em maio/2026 e está consistente.

### 4.3 `modulos/` (14 arquivos)

| Arquivo | Veredito | Principais problemas |
|---|---|---|
| `contatos.md` | DRIFT-MAIOR | Não cobre o Console como superfície de orquestração (Arc 11); Monitor descrito como read-only. Link quebrado para `guias/conferencia-e-historico.md` (real em `sections/`). |
| `agent-assist.md` | DRIFT-MAIOR | RightPanel com 4 abas — falta a 5ª (OrchestrationTab, Arc 11 Fase D). Sem AiParticipantCard, Adicionar Especialista, Delegar Tarefa. |
| `avaliacao.md` | DRIFT-MAIOR | Ignora todo o Arc 13: páginas Calibração e Curadoria, UX de threads por dimensão, agentes pré-revisor/revisor, critérios `auto_computed`, `calibration.events`. |
| `relatorios-agentes.md` | DRIFT-MAIOR | Descreve o backend do Arc 8 como "pendente" — está totalmente implementado. Rota e componente do endpoint de pausa errados. |
| `controle-acesso.md` | DRIFT-MENOR | Falta módulo ABAC `audit`; campos `journey.*`; claims de supervisor scope (Arc 9). |
| `agentflow.md` | DRIFT-MENOR | "11 tipos de step" (são 14); `notify` sem nota de depreciação; falta `collect.requires`, `creates_journey`. |
| `mascaramento.md` | DRIFT-MENOR | Nomes de tool possivelmente desatualizados; sem relação com módulo Auditoria LGPD. |
| `workflow.md` | DRIFT-MENOR | Sem integração com Journey/Processos; `notify` sem nota; redirect `/campaigns` suspeito. |
| `configuracao-recursos.md` | DRIFT-MENOR | Falta `media_capabilities` (Arc 15), `inbound_journey_resume`, `mentionable_journeys` (Arc 16). |
| `dashboards.md` | DRIFT-MENOR | 4 tipos de card; faltam cards de Journey (Arc 10), agent_event (Arc 12), comparação por deploy (Arc 6 Fase 2); DisplayTool registry. |
| `configuracao-plataforma.md` | DRIFT-MENOR | Namespace `routing` duplicado na tabela. |
| `faturamento.md` | ATUAL | — |
| `grupos.md` | ATUAL | — |
| `processos.md` | ATUAL | Menor: poderia citar Arc 16 (Journey como superfície pública). |

Nenhum dos 14 arquivos de `modulos/` tem stamp de data.

### 4.4 `arcos/` (31 arquivos)

| Arquivo | Veredito | Principais problemas |
|---|---|---|
| `arc4-workflow.md` | DRIFT-MAIOR | Documenta a UI no pacote obsoleto `operator-console`; sem integração Arc 16 (`collect.requires`, `@ctx.journey.*`). |
| `arc6-evaluation.md` | DRIFT-MAIOR | Ignora todo o Arc 13 e o Arc 6 Fase 2. |
| `arc6-phase2-observability.md` | DRIFT-MAIOR | Escrito como plano futuro; sem seção de status. |
| `platform-ui.md` | DRIFT-MAIOR | Descreve features já entregues como propostas; "Build: NNN kB" e "Task #N" não pertencem a doc viva; contradição interna sobre CloseModal. |
| `session-conference-lifecycle.md` | DRIFT-MAIOR | Auto-contradição: gaps G1–G6 abertos no corpo, todos resolvidos na seção final. |
| `dashboard.md` | DRIFT-MAIOR | Escrito como proposta pré-implementação; catálogo de cards superado. |
| `task-30-contacts-restructure.md` | DRIFT-MAIOR | Design de pré-implementação superado pela estrutura de nav atual; candidato a `deprecated/`. |
| `journey-analytics.md` | OBSOLETO | Modelo de 4 níveis incompatível com o Arc 10 real; nunca implementado. Mover para `deprecated/`. |
| `arc7-auth.md` | DRIFT-MENOR | Falta módulo ABAC `audit`; campos `journey.*` no módulo `workflows`. |
| `arc9-agent-groups.md` | DRIFT-MENOR | Escrito como spec/plano; falta seção de status confirmando conclusão. |
| `arc10-journey.md` | DRIFT-MENOR | "8 tipos de evento" (são 9); sem integração Arc 16. |
| `arc11-phase2-console-redesign.md` | DRIFT-MENOR | Rodapé contraditório; referencia Arc 14 sem doc rastreável. |
| `arc14-posatt-independent-segments.md` | DRIFT-MENOR | Corpo descreve tudo como proposta; rodapé "Pendente de validação" contradiz cabeçalho "completo". |
| `ai-gateway.md` | DRIFT-MENOR | Falta `InferenceRequest.journey_id` (Arc 16). |
| `usage-metering.md` | DRIFT-MENOR | Seção "Pending" desatualizada — adapters de canal já existem. |
| `evaluation-agents.md` | DRIFT-MENOR | Trata step `receive` como "planejado" — está implementado; usa `notify`. |
| `channel-gateway-multi-channel.md` | DRIFT-MENOR | WebRTC marcado "futuro" — implementado (Arc 15); cita `TelnyxProvider` inexistente. |
| `arc5-segments.md` | ATUAL | — |
| `arc8-agent-availability.md` | ATUAL | — |
| `arc11-console-orchestration.md` | ATUAL | — |
| `arc12-agent-business-events.md` | ATUAL | — |
| `arc13-review-contestation.md` | ATUAL | Menor: tabela de planejamento cita Prisma/arquivos não usados. |
| `arc15-webrtc.md` | ATUAL | — |
| `arc16-flow-orchestration.md` | ATUAL | Menor: rótulos de status das Fases B/C. |
| `audit-lgpd.md` | ATUAL | Falta stamp de data. |
| `pricing.md` | ATUAL | Falta stamp. |
| `session-replayer.md` | ATUAL | Falta stamp. |
| `instance-bootstrap.md` | ATUAL | — |
| `dialer-compliance-invariants.md` | HISTÓRICO-OK | Proposta nunca implementada; honestamente rotulada. |
| `design-system-audit.md` | HISTÓRICO-OK | Relatório de auditoria pontual datado. |
| `accessibility-audit.md` | HISTÓRICO-OK | Relatório de auditoria pontual datado. |

### 4.5 `pacotes/` (16 arquivos)

A pasta mais defasada do acervo: **11 de 16 em DRIFT-MAIOR**, nenhum com stamp de data.

| Arquivo | Veredito | Principais problemas |
|---|---|---|
| `schemas.md` | DRIFT-MAIOR | Lista 4 arquivos `.ts` (reais: 28); 8 step types; sem schemas de Journey/Workflow/Usage/Evaluation/Audit. |
| `mcp-server-plughub.md` | DRIFT-MAIOR | "22 tools"; sem tools de Journey, `agent_event`, operacionais, review/contestação; tools marcadas "stub" estão implementadas. |
| `skill-flow-engine.md` | DRIFT-MAIOR | "9 tipos de step"; faltam `resolve`, `suspend`, `collect`, `receive`, `begin/end_transaction`. |
| `ai-gateway.md` | DRIFT-MAIOR | Profiles `fast/balanced/powerful` (atuais: `realtime/balanced/evaluation`); sem AccountSelector; typo de caminho `plughib`. |
| `agent-registry.md` | DRIFT-MAIOR | Tópico `agent.registry.events`; sem `media_capabilities`, `mentionable_pools`, `inbound_journey_resume`, skill deploy. |
| `channel-gateway.md` | DRIFT-MAIOR | Sem canal WebRTC (Arc 15); sem channel capability negotiation (Arc 16); cita "Notification Agent". |
| `channel-gateway-webchat.md` | DRIFT-MAIOR | Enquadrado como "piloto"; protocolo WS antigo; sem upload de anexos nem masked fields. |
| `platform-ui.md` | DRIFT-MAIOR | Lista 5 rotas; marca como roadmap features já entregues; sem ABAC. |
| `evaluation-agent.md` | DRIFT-MAIOR | Fluxo de avaliação pré-Arc 6; tools e tabelas ClickHouse superadas; sem Arc 13. |
| `clickhouse-consumer.md` | DRIFT-MAIOR | Descreve escopo de piloto; afirma que o Dashboard lê ClickHouse direto (há analytics-api). |
| `conversation-writer.md` | DRIFT-MAIOR | Descreve agregador de mensagens pré-canonical-stream; sem Stream Persister/Hydrator. |
| `routing-engine.md` | DRIFT-MENOR | Tópico Kafka errado; sem Pool Context Enrichment, performance routing (Arc 7d). |
| `rules-engine.md` | DRIFT-MENOR | Nomes de tópico errados; responsabilidade de amostragem de avaliação superada pelo Arc 6. |
| `auth-api.md` | DRIFT-MENOR | Sem Arc 9 (Agent Groups); sem módulo ABAC `audit`. |
| `sdk.md` | ATUAL | — |
| `notification-agent.md` | OBSOLETO | Pacote nunca implementado; mover para `deprecated/`. |

### 4.6 `guias/` (17 arquivos)

| Arquivo | Veredito | Principais problemas |
|---|---|---|
| `pool-hooks.md` | DRIFT-MAIOR | Fase C `post_human` marcada "pendente" — implementada; terminologia de close superada pelo modelo de 3 camadas. |
| `context-store-taxonomy.md` | DRIFT-MAIOR | Declara-se "implementação pendente"; o sucessor `context-masking-rules.md` já implementou Fases A–C. |
| `conferencia-agente-ia-mapeamento.md` | OBSOLETO | Lista como "gaps a implementar" features já prontas (@mention, conferência multi-agente). |
| `changelog-2026-04-15/16/16b/29.md` | OBSOLETO (×4) | Changelogs históricos em pasta errada; mover para `deprecated/guias/`. |
| `timeouts-e-deteccao-de-falhas.md` | DRIFT-MENOR | `wait_for_message` descrito com BLPOP; migrou para XREADGROUP. |
| `webhook-patterns.md` | DRIFT-MENOR | `collect` sem `requires`/capability negotiation (Arc 16); usa `notify`. |
| `context-store.md` | DRIFT-MENOR | Sem namespaces `segment.*` e `journey.*`; sem Pool Context Enrichment. |
| `abac-permission-system.md` | DRIFT-MENOR | Sem o conjunto completo de 8+ módulos; sem módulo `audit`; sem `PermissionChecker` backend. |
| `mention-protocol.md` | ATUAL | — |
| `masked-input.md` | ATUAL | — |
| `gitagent.md` | ATUAL | — |
| `orchestrator-working-memory.md` | ATUAL | — |
| `context-masking-rules.md` | ATUAL | — |
| `conference-mechanics.md` | ATUAL | Documento crítico, bem mantido; falta apenas stamp de data. |

### 4.7 `adr/` (6 arquivos)

ADRs registram decisões e são, por natureza, imutáveis. A correção recomendada limita-se ao campo **Status**.

| Arquivo | Veredito | Principais problemas |
|---|---|---|
| `adr-contact-segments.md` | DRIFT-MENOR | Status "Proposto / pendente Arc 5" — o Arc 5 está implementado. |
| `adr-ai-gateway-separation.md` | DRIFT-MENOR | Status "Proposto"; a separação foi resolvida via perfis de modelo. |
| `adr-webchat-channel.md` | HISTÓRICO-OK | — |
| `adr-message-masking.md` | HISTÓRICO-OK | Seção "Pendente" levemente superada. |
| `adr-instance-bootstrap.md` | HISTÓRICO-OK | — |
| `adr-session-replayer.md` | HISTÓRICO-OK | Seção "Pendente" cita Comparison Mode já implementado. |

### 4.8 `layers/` (9 arquivos)

7 de 9 em DRIFT-MAIOR. Ver §3.9. Verdictos: `01` a `07` — DRIFT-MAIOR; `08-mlops` e `09-observability` — DRIFT-MENOR. Problemas dominantes: nomes obsoletos, tabelas Kafka/ClickHouse muito desatualizadas, WebRTC/Evaluation como "Horizonte 2", links quebrados para `modulos/*`.

### 4.9 `sections/` (14 arquivos) e `deprecated/` (6 arquivos)

`sections/` são extrações congeladas da spec v24.0 — corretamente **HISTÓRICO-OK** (13 de 14). Exceção: `sections/conferencia-e-historico.md` (DRIFT-MAIOR — rascunho "v25.0" cujo tema já foi implementado). Recomenda-se um aviso de cabeçalho em `sections/10-evaluation.md` e `sections/3.4-context-package.md` apontando para a doc viva equivalente.

`deprecated/` — todos os 6 corretamente isolados como **OBSOLETO**. Nada a resgatar.

---

## 5. Cobertura da lista de funcionalidades de interesse

Mapa da lista de funcionalidades fornecida contra a documentação existente. O mapeamento detalhado com links navegáveis foi incorporado à nova `visao-geral.md`.

| Funcionalidade | Documentação existente | Estado da cobertura |
|---|---|---|
| Plataforma para agentes em tempo de IA | `product/overview.md`, `visao-geral.md` | Boa (após reescrita da visao-geral) |
| MCP Server PlugHub | `pacotes/mcp-server-plughub.md`, `layers/06-mcp-layer.md` | **Fraca** — ambos DRIFT-MAIOR |
| Canal voz/PSTN via provedores externos | `arcos/channel-gateway-multi-channel.md` (§ 9 — Voice) | Adequada — implementado (tronco Twilio, STT Deepgram, TTS ElevenLabs) |
| Canal SMS | `arcos/channel-gateway-multi-channel.md` | Adequada (DRIFT-MENOR) |
| Canal WebRTC + fallback de negociação | `arcos/arc15-webrtc.md` | **Boa** — ATUAL |
| Canal WhatsApp | `arcos/channel-gateway-multi-channel.md` | Adequada (DRIFT-MENOR) |
| Canal Webchat | `adr/adr-webchat-channel.md`, `pacotes/channel-gateway-webchat.md` | Mista — ADR bom, pacote DRIFT-MAIOR |
| Journey ("connect the dots") | `arcos/arc10-journey.md`, `arcos/arc16-flow-orchestration.md`, `modulos/processos.md` | Boa |
| Contact / Segments | `arcos/arc5-segments.md`, `adr/adr-contact-segments.md` | Boa |
| Troca de canais em qualquer etapa | `arcos/arc16-flow-orchestration.md` (capability negotiation) | Boa |
| Preservação de contexto / retomada | `arcos/arc16-flow-orchestration.md`, `guias/context-store.md` | Adequada |
| Arquitetura: alta disponibilidade / alto volume | `visao-geral.md` | **Lacuna** — não há doc dedicado |
| Multi-tenant (futuro) | `sections/14-multi-tenant.md` (histórico) | **Lacuna** — só referência v24.0 |
| Consolidação de dados (ClickHouse + exportação) | `arcos/arc5-segments.md`, `modelos-de-dados.md` | Mista — Arc 5 bom, modelos-de-dados DRIFT-MAIOR |
| Agentes especialistas em qualquer etapa | `guias/mention-protocol.md`, `sections/9.5-a2a-protocol.md` | Adequada |
| Outbound baseado em workflows | `arcos/arc4-workflow.md`, `guias/webhook-patterns.md` | Mista — webhook bom, arc4 DRIFT-MAIOR |
| Canais com/sem outbound + retomada de workflow | `arcos/arc16-flow-orchestration.md` | Boa |
| Calendário unificado | `arcos/arc4-workflow.md` (calendar-api) | Adequada — sem doc standalone |
| Agent Routing (SLA/skill/usage/availability) | `pacotes/routing-engine.md`, `sections/3.3-routing-engine.md` | Mista — pacote DRIFT-MENOR |
| Masking LGPD | `adr/adr-message-masking.md`, `guias/masked-input.md`, `guias/context-masking-rules.md`, `modulos/mascaramento.md` | Boa |
| Perfis de usuário RBAC/ABAC/Pool/Grupo | `arcos/arc7-auth.md`, `arcos/arc9-agent-groups.md`, `guias/abac-permission-system.md`, `modulos/controle-acesso.md`, `modulos/grupos.md` | Boa |
| AI Gateway | `arcos/ai-gateway.md`, `pacotes/ai-gateway.md` | Mista — arco bom, pacote DRIFT-MAIOR |
| skill-flow (design de fluxos/workflow) | `pacotes/skill-flow-engine.md`, `modulos/agentflow.md`, `arcos/arc4-workflow.md` | **Fraca** — todos com drift |
| Controle de versão / deploy / hot deploy / rollback | `arcos/arc4-workflow.md` (Skill Deploy), `arcos/instance-bootstrap.md` | Adequada |
| Monitoria e relatórios (objetivos) | `modulos/contatos.md`, `modulos/dashboards.md`, `arcos/dashboard.md`, `arcos/arc5-segments.md` | Mista |
| Captura de sentimento em tempo real | `CLAUDE.md` (§ Sentiment Tracking) | **Lacuna** — sem doc no acervo `docs/` |
| Relatórios operacionais (deploys ao longo do tempo) | `arcos/arc6-phase2-observability.md` | Adequada (após reescrita) |
| Auditoria (atendimento, transações, consumo, raciocínio) | `arcos/audit-lgpd.md` | Adequada |
| Console de agentes humanos | `arcos/arc11-console-orchestration.md`, `arcos/arc11-phase2-console-redesign.md`, `modulos/agent-assist.md` | Mista — arcos bons, módulo DRIFT-MAIOR |
| Quality (avaliação, campanhas, contestação, comparação) | `arcos/arc6-evaluation.md`, `arcos/arc13-review-contestation.md`, `arcos/arc6-phase2-observability.md`, `modulos/avaliacao.md` | Mista — Arc 13 bom, resto com drift |

**Lacunas reais identificadas** (funcionalidades de interesse sem documentação adequada, independentemente de drift):

1. **Captura de sentimento em tempo real** — existe no `CLAUDE.md` (§ Sentiment Tracking) e está implementado, mas não há nenhum guia ou doc no acervo `docs/`. Recomenda-se criar `guias/sentiment-tracking.md`.
2. **Alta disponibilidade / alto volume / multi-tenant** — temas de arquitetura sem documento dedicado vivo. A nova `visao-geral.md` deve abrir uma seção; multi-tenant pode ganhar um doc próprio quando sair do estado "futuro próximo".

> **Correção pós-auditoria (2026-05-25):** o canal **voz/PSTN** foi inicialmente classificado como lacuna ("sem Arc, aspiracional"). Revisão confirmou que o canal está **implementado** — tronco PSTN via Twilio, STT via Deepgram e TTS via ElevenLabs, documentado em `arcos/channel-gateway-multi-channel.md` § 9. Apenas o **dialer preditivo** (loop de discagem com pacing e compliance guard TCPA/LGPD/DNC) permanece como roadmap — ver `arcos/dialer-compliance-invariants.md`.

---

## 6. Plano de remediação priorizado

### Prioridade 1 — Documentos de entrada do wiki (executados nesta revisão)

1. **`docs/visao-geral.md`** — reescrita completa como documento técnico consolidado, cobrindo toda a lista de funcionalidades com links para os docs detalhados.
2. **`docs/INDEX.md`** — atualização para listar os 123 arquivos reais, com a classificação correta de cada pasta.

### Prioridade 2 — DRIFT-MAIOR de alta visibilidade

`modelos-de-dados.md`, `kafka-eventos.md`, os 11 arquivos de `pacotes/` em DRIFT-MAIOR, `modulos/{contatos,agent-assist,avaliacao,relatorios-agentes}.md`, `arcos/{arc4-workflow,arc6-evaluation,platform-ui,dashboard,session-conference-lifecycle}.md`, `guias/{pool-hooks,context-store-taxonomy}.md`, e os 7 arquivos de `layers/`.

### Prioridade 3 — DRIFT-MENOR

Os 29 arquivos com correções pontuais: stamps de data, contagem de step types, nomes de tópicos Kafka, status de ADRs, namespaces faltantes.

### Prioridade 4 — Ações estruturais

Ver §7.

---

## 7. Ações estruturais recomendadas

1. **Mover para `deprecated/`:** os 4 `guias/changelog-2026-*.md` → `deprecated/guias/`; `arcos/journey-analytics.md` → `deprecated/arcos/`; `guias/conferencia-agente-ia-mapeamento.md` → `deprecated/guias/`; `pacotes/notification-agent.md` → `deprecated/pacotes/`. Avaliar `arcos/task-30-contacts-restructure.md`.
2. **Criar docs de pacote faltantes:** entradas em `pacotes/` para `workflow-api`, `calendar-api`, `evaluation-api`, `pricing-api`, `mcp-server-knowledge`, `analytics-api`, `skill-flow-worker`, `orchestrator-bridge` — ou, no mínimo, registrá-los no INDEX apontando para a doc de Arc equivalente.
3. **Padronizar cabeçalho de data/versão** em todo documento vivo (§3.1).
4. **Decidir o futuro de `layers/`** (§3.9): reescrever a fundo ou rebaixar a histórico.
5. **Criar `guias/sentiment-tracking.md`** para fechar a lacuna de captura de sentimento.
6. **Conciliar `sections/conferencia-e-historico.md`** — arquivar como ADR histórica ou substituir por ponteiro para `guias/conference-mechanics.md`.
7. **Acoplar a manutenção de `docs/` ao ciclo de implementação** — estender ao acervo `docs/` a regra que o `CLAUDE.md` já aplica a si mesmo: toda entrada no `CHANGELOG.md` deve ter o doc correspondente criado/atualizado antes de ser considerada concluída.
8. **Conciliar o `CLAUDE.md`** com o time de engenharia: adicionar seção de Arc 14; atualizar o status dos gaps G1–G6.

---

## Apêndice — Veredito de todos os 123 arquivos

Resumo consolidado. Detalhamento de drift por arquivo nas seções §4.1–§4.9.

**ATUAL (26):** `product/overview.md`, `product/target-audience.md`, `product/value-proposition.md`, `product/competitive-analysis.md`, `modulos/faturamento.md`, `modulos/grupos.md`, `modulos/processos.md`, `arcos/arc5-segments.md`, `arcos/arc8-agent-availability.md`, `arcos/arc11-console-orchestration.md`, `arcos/arc12-agent-business-events.md`, `arcos/arc13-review-contestation.md`, `arcos/arc15-webrtc.md`, `arcos/arc16-flow-orchestration.md`, `arcos/audit-lgpd.md`, `arcos/pricing.md`, `arcos/session-replayer.md`, `arcos/instance-bootstrap.md`, `pacotes/sdk.md`, `guias/mention-protocol.md`, `guias/masked-input.md`, `guias/gitagent.md`, `guias/orchestrator-working-memory.md`, `guias/context-masking-rules.md`, `guias/conference-mechanics.md`.

**DRIFT-MENOR (29):** `INDEX.md`, `standards/frontend-architecture.md`, `modulos/controle-acesso.md`, `modulos/agentflow.md`, `modulos/mascaramento.md`, `modulos/workflow.md`, `modulos/configuracao-recursos.md`, `modulos/dashboards.md`, `modulos/configuracao-plataforma.md`, `arcos/arc7-auth.md`, `arcos/arc9-agent-groups.md`, `arcos/arc10-journey.md`, `arcos/arc11-phase2-console-redesign.md`, `arcos/arc14-posatt-independent-segments.md`, `arcos/ai-gateway.md`, `arcos/usage-metering.md`, `arcos/evaluation-agents.md`, `arcos/channel-gateway-multi-channel.md`, `pacotes/routing-engine.md`, `pacotes/rules-engine.md`, `pacotes/auth-api.md`, `guias/timeouts-e-deteccao-de-falhas.md`, `guias/webhook-patterns.md`, `guias/context-store.md`, `guias/abac-permission-system.md`, `adr/adr-contact-segments.md`, `adr/adr-ai-gateway-separation.md`, `layers/08-mlops-layer.md`, `layers/09-observability-layer.md`.

**DRIFT-MAIOR (35):** `visao-geral.md`, `modelos-de-dados.md`, `kafka-eventos.md`, `modulos/contatos.md`, `modulos/agent-assist.md`, `modulos/avaliacao.md`, `modulos/relatorios-agentes.md`, `arcos/arc4-workflow.md`, `arcos/arc6-evaluation.md`, `arcos/arc6-phase2-observability.md`, `arcos/platform-ui.md`, `arcos/session-conference-lifecycle.md`, `arcos/dashboard.md`, `arcos/task-30-contacts-restructure.md`, `pacotes/schemas.md`, `pacotes/mcp-server-plughub.md`, `pacotes/skill-flow-engine.md`, `pacotes/ai-gateway.md`, `pacotes/agent-registry.md`, `pacotes/channel-gateway.md`, `pacotes/channel-gateway-webchat.md`, `pacotes/platform-ui.md`, `pacotes/evaluation-agent.md`, `pacotes/clickhouse-consumer.md`, `pacotes/conversation-writer.md`, `guias/pool-hooks.md`, `guias/context-store-taxonomy.md`, `layers/01-channel-layer.md`, `layers/02-gateway-layer.md`, `layers/03-message-bus.md`, `layers/04-orchestration-layer.md`, `layers/05-agent-layer.md`, `layers/06-mcp-layer.md`, `layers/07-data-layer.md`, `sections/conferencia-e-historico.md`.

**OBSOLETO (13):** `arcos/journey-analytics.md`, `pacotes/notification-agent.md`, `guias/conferencia-agente-ia-mapeamento.md`, `guias/changelog-2026-04-15.md`, `guias/changelog-2026-04-16.md`, `guias/changelog-2026-04-16b.md`, `guias/changelog-2026-04-29.md`, `deprecated/standards/operator-console-migration.md`, `deprecated/sections/visao_negocial.md`, `deprecated/sections/visao_negocial_v24.md`, `deprecated/modulos/agent-assist-piloto.md`, `deprecated/modulos/dashboard-piloto.md`, `deprecated/modulos/evaluation.md`.

**HISTÓRICO-OK (20):** `plughub_analise_competitiva_2026.md`, `arcos/dialer-compliance-invariants.md`, `arcos/design-system-audit.md`, `arcos/accessibility-audit.md`, `adr/adr-webchat-channel.md`, `adr/adr-message-masking.md`, `adr/adr-instance-bootstrap.md`, `adr/adr-session-replayer.md`, `sections/INDEX.md`, `sections/spec_completa.md`, `sections/3.2-rules-engine.md`, `sections/3.3-routing-engine.md`, `sections/3.4-context-package.md`, `sections/4.2-contrato-execucao.md`, `sections/4.5-agent-registry.md`, `sections/4.6-sdk.md`, `sections/4.7-skill-registry.md`, `sections/9.4-agent-runtime-tools.md`, `sections/9.5-a2a-protocol.md`, `sections/10-evaluation.md`, `sections/14-multi-tenant.md`.

---

*Relatório gerado em 2026-05-25 como parte da revisão do acervo de documentação para o wiki de mercado.*
