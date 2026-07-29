# PlugHub — Descritivo Técnico v2 · PROTÓTIPO PARA AVALIAÇÃO

> **O que é este arquivo:** um protótipo para você avaliar antes de eu reescrever o descritivo completo.
> Contém (1) a auditoria do que envelheceu no `plughub-descritivo-tecnico-funcional.md` de junho,
> (2) a estrutura proposta para a v2, e (3) três seções escritas por inteiro como amostra do tratamento.
> **Não é o documento final** — as demais seções aparecem só como cabeçalho e nota de escopo.
>
> Data: 2026-07-28 · Base: `CLAUDE.md`, `CHANGELOG.md` (até 2026-07-27), `packages/`, `docs/arcos/`

---

## Parte 0 — Achado principal

**O descritivo técnico já existe e é bom.** `plughub-descritivo-tecnico-funcional.md` tem 536 linhas, 21 seções,
uma seção de honestidade de engenharia (§19) e uma de roadmap apartado (§20). A estrutura está correta e não
proponho refazê-la.

O problema é que ele fecha em **09/06/2026** e o produto andou muito em sete semanas. Há três tipos de defasagem,
e o segundo é o que preocupa:

| Tipo | Gravidade | Exemplo |
|---|---|---|
| **Omissão** — existe e não está descrito | Média | Arco Outbound completo, Scheduler, Dialog primitive, fila de trabalho pull |
| **Afirmação hoje incorreta** | **Alta** | "A entidade Journey foi eliminada por redundância" — o processo multi-contato **voltou** em julho, com outro desenho |
| **Subdimensionamento** | Média | A tabela de pacotes lista 16 serviços; o monorepo tem 22 serviços Python + os Node |

O terceiro é curioso: o documento **subvende** a plataforma. Quem lê imagina um sistema menor do que o que existe.

---

## Parte 1 — Auditoria da v1 (itens a corrigir)

### 1.1 Correções factuais (afirmações que hoje estão erradas)

**§4 — "a entidade Journey foi eliminada por redundância".** Era verdade no Arc 19. Deixou de ser: o processo
multi-contato retornou em julho como **componente conexa de sessões** — identidade por `root_session_id`
imutável, união por `journey_merge` sobre um topic `journey.merges`, resolução por **union-find** na leitura,
raiz canônica valorada em `session_id`. J1–J3 e J5a (contexto compartilhado `@ctx.journey.*`, migrado no merge)
estão entregues e validados. Não é a entidade do Arc 10 ressuscitada — é um modelo sem entidade, sem lifecycle e
sem split, e a distinção precisa ficar explícita para não parecer que a arquitetura oscilou.

**§8 — perfis de modelo `realtime`.** Renomeado: hoje são `fast | balanced | powerful | evaluation`.

**§6.1 — "treze+ tipos de step".** São **15** — entrou o `loop` (percorre um array em N turnos sequenciais,
item em path fixo, contador no `pipeline_state`, acumulando `collect` em `results_as`).

**§17 — matriz competitiva, linha "MCP nativo".** Precisa sair como diferencial. Em julho/2026 MCP é
infraestrutura (~97M downloads/mês, +10.000 servidores, governança na Linux Foundation). O diferencial migrou
para o **guard obrigatório por chamada com audit não-optável** — que já está bem descrito na §9 e só precisa
assumir o lugar da linha antiga. Manter "MCP nativo (único)" queima credibilidade com avaliador informado.

**§19.9 — "AHT pode ser inflado por wrap-up".** **Corrigido em 27/07.** O `on_human_end` ganhou
`dispatch: detached`: o contato fecha quando o cliente sai e o wrap-up vira item de fila pull no inbox do agente,
com o outcome gravado por referência no segmento de origem (`segment_outcome_record`). Validado com atendimento
real, sem seed. Isto sai da lista de limitações e vira **prova de que a separação em três camadas era real** — é
o melhor exemplo do documento inteiro e hoje está enterrado numa nota de rodapé negativa.

**§20.7 — identidade cross-canal como roadmap.** Parcialmente entregue: Resolvedor Fase A (slices 1–4) e Fase B
(identidade progressiva, posse de canal via OTP, gate seguro para retomada) estão implementados. Permanece
roadmap a Fase C (`external_refs`, merge de clientes, wiring de CRM) e o framework de loja / commerce-cards.

**§2.5 — tabela de pacotes.** Faltam, confirmados no monorepo: `config-api`, `orchestrator-bridge`,
`session-replayer`, `conversation-writer`, `clickhouse-consumer`, `usage-aggregator`, `workflow-api`,
`scheduler-api` (3650), `mailing-api` (3660), `dialog-api` (3760), `quality-ingest` (3850),
`quality-export` (3852), `skill-flow-worker`.

### 1.2 Omissões (existe, entregue, e não está no documento)

- **Arco Outbound completo (Fases 1–5)** — mailing + campaign + delivery, governança de contato (frequency caps,
  quarentena, opt-out global, janela de calendário), importador CSV/xlsx em duas camadas, fan-out
  dispatcher/worker, survey outbound ponta a ponta. É a prova mais forte do "motor único" e não aparece.
- **Scheduler / Agenda (`scheduler-api`, Fases 1–3)** — aciona pool por webhook em horário, recorrência,
  ledger de disparos, UI de autoria e monitor, fire-now, ABAC próprio.
- **Dialog primitive + runner** — `DialogForm` versionado na `dialog-api`, quatro superfícies de renderização
  (chat via runner, inline em hook, página web pública, Console), editor multi-locale, retry por formato.
- **Fila de trabalho humano (dispatch pull)** — claim atômico, lease, pull direcionado com `assigned_to` e
  transbordo por idade, `PullInboxPanel`, e o renderer genérico de collect-form no Console.
- **Quality Ingest / Export (R13a–R13d)** — histórico externo de CCaaS entra no mesmo pipeline de avaliação por
  stream de eventos canônicos; reavaliação interna pela mesma porta. É o que viabiliza o empacotamento
  "qualidade como garantia" sobre base instalada de terceiros.
- **Isolamento por `origin`** (`live|import|reeval`) com filtro default no report layer e no sampling.
- **Customer Voice** — lente `grain × metric` sobre `session_signal`, com catálogo source-aware.
- **Governança de capacidade e fila sempre atendida** — está citada de passagem na §7; merece tratamento próprio.

### 1.3 O que a v1 acerta e deve ser preservado

A estrutura de 21 seções; a §18 de invariantes (é o que avaliador técnico mais valoriza); a §19 de honestidade
de engenharia; a §20 de roadmap apartado com aviso explícito; e os blocos "Vs. concorrência" ao fim de cada
seção. Nada disso muda na v2.

---

## Parte 2 — Estrutura proposta para a v2

Mantém a espinha da v1, com quatro mudanças estruturais:

**Mudança 1 — abrir com um mapa de serviços.** Hoje o leitor chega na §2.5 e vê 16 linhas de tabela. Proponho um
**diagrama de topologia** (canais → core/sessão → roteamento → agentes → dados) e depois o inventário completo,
com porta e responsabilidade em uma linha.

**Mudança 2 — nível de evidência por capacidade.** Cada seção principal ganha um selo discreto: **entregue e
validado em ambiente real** / **entregue e validado por smoke test** / **parcial** / **roadmap**. Isso substitui
a necessidade de o leitor confiar na prosa e é o que diferencia um descritivo honesto de um datasheet.

**Mudança 3 — promover o ciclo de vida em três camadas.** Hoje é a §3.1, uma subseção. Vira seção própria, com o
caso do wrap-up destacado como prova. É o argumento mais forte e o mais concreto.

**Mudança 4 — seção nova de processo (journey).** Substitui a afirmação incorreta da §4 e descreve o modelo por
union-find, incluindo por que a entidade foi eliminada e por que o modelo sem entidade voltou.

Mapa de seções proposto:

| # | Seção | Estado |
|---|---|---|
| 1 | Sumário executivo — a virada de categoria | revisar |
| 2 | **Arquitetura e topologia de serviços** | **reescrever** — *amostra abaixo* |
| 3 | Modelo de sessão unificado (sala de conferência) | preservar |
| 4 | **Ciclo de vida em três camadas** | **promover a seção** |
| 5 | **Processo multi-contato (journey por union-find)** | **novo** — *amostra abaixo* |
| 6 | Canais — omnichannel, voz e WebRTC | atualizar |
| 7 | Skill Flow — motor único (15 steps) | atualizar |
| 8 | **Outbound — mailing, campanha, governança de contato** | **novo** — *amostra abaixo* |
| 9 | Scheduler / Agenda | novo |
| 10 | Dialog primitive — conteúdo scriptado em 4 superfícies | novo |
| 11 | Routing Engine — alocação, fila atendida, dispatch pull | atualizar |
| 12 | Identidade e retomada cross-canal | atualizar (era roadmap) |
| 13 | AI Gateway — agnóstico e multi-conta | atualizar |
| 14 | MCP-first + interception guard | preservar |
| 15 | Masking e LGPD secure by design | preservar |
| 16 | RBAC + ABAC + Pool + Grupo | preservar |
| 17 | Quality — avaliação, contestação, calibração, bancada | atualizar |
| 18 | Quality Ingest/Export — substrato externo e reavaliação | novo |
| 19 | Monitoria, relatórios e auditoria | atualizar |
| 20 | Console — superfície de orquestração e inbox pull | atualizar |
| 21 | Billing por capacidade | preservar |
| 22 | SDK, portabilidade e anti-lock-in | preservar |
| 23 | Invariantes arquiteturais | expandir |
| 24 | Notas de honestidade e limitações | reescrever |
| 25 | Roadmap (apartado) | reescrever |

---

## Parte 3 — Amostras (seções escritas por inteiro)

> As três seções abaixo demonstram o tratamento proposto. Uma **reescrita** (§2), uma **nova por correção** (§5)
> e uma **nova por omissão** (§8).

---

### §2 — Arquitetura e topologia de serviços

> **Evidência:** entregue · em operação no ambiente demo

#### 2.1 Princípio: event-driven, stateless, estado externalizado

O backbone é **Kafka**; os componentes são **stateless por padrão**. O estado de tempo real vive no **Redis**, não
nos processos: `pipeline_state`, filas, heartbeats, ContextStore e o *canonical stream* da sessão. Qualquer
instância de qualquer componente pode cair e ser substituída sem perda de estado de sessão, porque o estado não
está nela.

Duas consequências que um avaliador deve verificar:

- Agentes que precisam manter estado entre turnos declaram `execution_model: stateful` e o Routing Engine
  garante **afinidade de sessão**. O AI Gateway é estritamente stateless — um turno por chamada de LLM, sem
  estado entre turnos. É invariante, não convenção.
- Consumers Kafka críticos têm retry e **dead-letter queue** (`events.dead_letter`). A reconciliação de
  instâncias é estilo Kubernetes: um controlador compara estado desejado (Agent Registry) contra estado real
  (Redis) e aplica o diff mínimo, com heartbeat de 15s e reconciliação periódica de 5min.

#### 2.2 Topologia de persistência

| Banco | Uso |
|---|---|
| **Redis** | Estado de conversa em tempo real, `pipeline_state`, filas, heartbeats, ContextStore, canonical stream, tokens de retomada |
| **PostgreSQL + pgvector** | Agent Registry; schemas `auth`, `workflow`, `evaluation`, `identity`, `dialog`, `scheduler`, `outbound`; histórico de sessões; base vetorial (RAG) |
| **ClickHouse** | Analytics operacional, audit log, métricas de qualidade, sinais de cliente |
| **Object Storage** | Áudio de ligações, anexos, gravações WebRTC |

#### 2.3 Inventário de serviços

O monorepo tem **22 serviços Python** e **7 pacotes Node**, com dependências explícitas e sem ciclos
(`schemas` não depende de ninguém; pacotes TypeScript nunca dependem do `ai-gateway`).

**Núcleo de sessão e roteamento**

| Serviço | Runtime | Responsabilidade |
|---|---|---|
| `mcp-server-plughub` | Node | Runtime de agente e ferramentas MCP (Core da sessão) |
| `routing-engine` | Python | Único árbitro de alocação: filas, scoring, dispatch push/pull, `close_reason` |
| `orchestrator-bridge` | Python | Reconciliação de instâncias, hooks de pool, ponte skill-flow↔sessão |
| `channel-gateway` | Python | Adapters de canal, normalização inbound, render outbound, identidade |
| `rules-engine` | Python | Avaliação de eventos pós-roteamento |
| `conversation-writer` | Python | Persistência do stream canônico |

**Fluxo e agentes**

| Serviço | Runtime | Responsabilidade |
|---|---|---|
| `skill-flow-engine` | Node | Interpretador do grafo de estados (15 tipos de step) |
| `skill-flow-worker` | Node | Consumer Kafka para instâncias de workflow |
| `agent-registry` | Node | CRUD de AgentType/Pool/Skill, slots de deploy, hot-reload |
| `ai-gateway` | Python | Inferência LLM, multi-conta, fallback cross-provider |
| `mcp-server-knowledge` | Node · 3401 | Base vetorial para RAG |

**Processo, agenda e contato ativo**

| Serviço | Runtime | Responsabilidade |
|---|---|---|
| `workflow-api` | Python · 3800 | Ciclo de vida de instância de workflow |
| `scheduler-api` | Python · 3650 | Agenda: aciona pool por webhook em horário/recorrência |
| `mailing-api` | Python · 3660 | Mailing, campanha, delivery, governança de fadiga de contato |
| `calendar-api` | Python · 3700 | Motor de calendário: horários, feriados, timezone |
| `dialog-api` | Python · 3760 | `DialogForm` versionado (draft/published), i18n embutido |

**Qualidade e conformidade**

| Serviço | Runtime | Responsabilidade |
|---|---|---|
| `evaluation-api` | Python · 3400 | Formulários, campanhas, instâncias, contestação, calibração |
| `session-replayer` | Python | Replay de sessão, hidratação, `ReplayContext`, comparison mode |
| `quality-ingest` | Python · 3850 | Leitor plugável de histórico externo (produtor puro) |
| `quality-export` | Python · 3852 | Histórico interno → reavaliação |
| `auth-api` | Python · 3200 | Auth, JWT, RBAC+ABAC, grupos e escopo de supervisor |

**Dados, custo e interface**

| Serviço | Runtime | Responsabilidade |
|---|---|---|
| `analytics-api` | Python | Consolidação ClickHouse e relatórios |
| `clickhouse-consumer` | Python | Ingestão de eventos para analytics |
| `usage-aggregator` | Python | Metering por dimensão, quotas |
| `pricing-api` | Python · 3900 | Billing por capacidade, invoice |
| `config-api` | Python | Configuração por namespace e tenant, hot-reload |
| `platform-ui` | React/Vite | Toda a UI de operação |

#### 2.4 Multi-tenant

Isolamento por `tenant_id` pervasivo: chaves Redis (`{tenantId}:...`), schemas PostgreSQL, `accessible_pools[]` e
`module_config` no JWT, segredos por tenant. A Config API dá override por namespace de praticamente todo
parâmetro operacional.

> **Nota de honestidade:** a fundação é pervasiva, mas o **isolamento operacional multi-tenant completo está em
> maturação**. Para SaaS multi-tenant em produção este é o item nº 1 a validar em prova de conceito.

#### 2.5 Invariantes de configuração

Quatro regras que evitam a deriva típica de plataformas configuráveis, com guard automatizado
(`infra/check_config_invariants.py`): **uma fonte por domínio** (config nunca duplicada entre stores);
**provisionamento só via API oficial** (proibida escrita direta em Redis/DB de config); **todo campo de config é
editável na UI** (campo que só existe em YAML é dívida declarada); **env só para segredo e topologia** — quando
env e config-api têm a mesma chave, config-api vence.

> **Vs. concorrência:** CCaaS tradicionais carregam estado em componentes proprietários acoplados; hyperscalers
> escondem o modelo de estado atrás de serviços gerenciados. Aqui o modelo é explícito (Redis/Kafka) e o time de
> arquitetura do cliente pode auditá-lo e dimensioná-lo.

---

### §5 — Processo multi-contato: journey por union-find

> **Evidência:** J1–J3 e J5a entregues e validados · drill N3 na Vista Processos pendente

#### 5.1 O problema

Um processo de negócio real — portabilidade, cobrança, onboarding — atravessa vários contatos, canais e dias.
O CCaaS mede interação; o CRM guarda registro. Nenhum dos dois dá **SLA por etapa com o roteador ciente**.

A consequência operacional é conhecida: quatro contatos pelo mesmo problema viram quatro registros e três falhas
de FCR, e ninguém sabe responder quanto custou resolver o processo do início ao fim.

#### 5.2 Por que a entidade foi eliminada — e por que o modelo voltou

O Arc 10 tinha uma entidade `Journey` com lifecycle, merge e split próprios. Foi **removida no Arc 19** por
redundância e custo de manutenção: duas entidades de agrupamento (sessão e journey) com ciclos de vida
independentes geram estados inconsistentes que ninguém consegue reconciliar.

O que voltou em julho **não é a entidade**. É um modelo sem entidade, sem lifecycle e sem split:

- **Identidade por proveniência.** Toda sessão carrega `root_session_id` imutável, nunca nulo — propagado do
  chamador ou auto-atribuído como si mesma. É a espinha.
- **União por alias.** A tool MCP `journey_merge` publica em `journey.merges`; o consumer materializa
  `journey_aliases` no ClickHouse. O merge é **sempre novo → antigo**, o que garante ordem total e, portanto,
  ausência de ciclo — sem depender de relógio.
- **Resolução na leitura.** A journey é a **componente conexa** de sessões sob (proveniência ∪ alias),
  identificada pela **raiz canônica**, resolvida por **union-find** no momento da consulta.

A decisão de projeto que fecha o desenho: a aciclicidade **não pode depender de timestamp**. Uma versão anterior
comparava `started_at` para ordenar o merge — e metade dos canais não escrevia esse campo. A correção não foi
fazer o timestamp funcionar; foi perceber que a ordem "novo → antigo" já é total por construção.

#### 5.3 Contexto compartilhado do processo

`@ctx.journey.*` resolve no hash do **processo**, não da sessão: `{tenant}:ctx:journey:{raiz canônica}`, TTL 30
dias. A raiz é resolvida pela mesma via do bridge (proveniência → union-find), e no merge o contexto migra com a
regra "canônica vence". Leitura, escrita automática (via `context_tags`), escrita imperativa (`context_set`,
injeção de supervisor) e migração no merge passam todas pelo mesmo helper — não há caminho que grave contexto de
processo no lugar errado.

#### 5.4 O que isso entrega

Drill de três níveis (processo → contato → segmento); SLA e métricas no nível do processo com detalhe até o turno;
sinal de cliente (survey/NPS) endereçável ao **grão** processo e não só ao contato; e contexto que sobrevive à
troca de canal e à espera de dias.

> **Vs. concorrência:** Pointillist (Genesys) e Adobe CJA fazem analytics de jornada **sem amarração ao
> roteador**; o `case` do Salesforce é record-centric e vive fora do motor de atendimento; o `thread` do
> LangGraph é técnico, não de negócio. Aqui o processo é simultaneamente operacional e analítico.

---

### §8 — Outbound: mailing, campanha e governança de contato

> **Evidência:** Fases 1–5 entregues, validadas por smoke test end-to-end · UI entregue

#### 8.1 O princípio

Outbound não é um módulo — é **o mesmo motor** com um substrato de audiência. Três entidades no schema
`outbound`, todas genéricas: **mailing** (audiência), **campaign** (orquestrador fino que endereça um **pool**,
nunca um skill) e **campaign_delivery** (estado por campanha).

Duas invariantes de modelagem que evitam o acoplamento típico:

- **O metadado da entrada é opaco** — contrato entre produtor e consumidor, a plataforma não o interpreta.
- **Membership ≠ supressão.** Estar na audiência (`mailing_entries`) é diferente de ter sido contatado
  (`campaign_deliveries`). Confundir os dois é o que faz campanhas reenviarem para quem pediu para sair.

A unidade de entrada é o par **(pessoa, contexto)** — não a pessoa. O mesmo cliente pode estar em duas entradas
por dois motivos diferentes.

#### 8.2 Governança de contato — o motor de fadiga

Um motor **agnóstico de canal e de campanha** decide se um contato pode acontecer, em camadas de precedência:

1. **Opt-out global** (`do_not_contact` no cadastro do cliente) — precedência máxima, salvo campanha marcada
   como transacional.
2. **Janela de contato** — consulta o `calendar-api` (`is_open`) pelo calendário da campanha; fora da janela,
   `outside_window` sem consumir cota.
3. **Fadiga** — `contact_policy` em camadas (tenant sobre campanha): `frequency_caps`, `quarantine_after`,
   `channel_caps`, com janelas de `30s` a `7d`.
4. **Supressão de mailing** — `mailing_unsubscribe` marca a entrada, sem afetar outras campanhas.

A decisão sempre **nomeia a regra** que a produziu. E o `claim=true` grava o fato de contato na mesma transação da
decisão — a janela de fadiga começa no envio, não na tentativa. Falha do calendário degrada para *aberto*, mas
**barulhento** (nunca em silêncio).

#### 8.3 Execução: dispatcher + worker

O disparo é uma agenda recorrente que drena um lote (`campaign_drain`, com claim atômico `FOR UPDATE SKIP LOCKED`)
— o **pacing é a própria recorrência**, sem loop de discagem. Cada entrada drenada vira um `workflow_trigger`
fire-and-forget para um pool de worker, que roda um contato por vez em paralelo: verifica elegibilidade com claim,
ramifica, e contata via `collect`.

O paralelismo é o `max_concurrent` do pool mais a fila normal — nenhuma infraestrutura nova de concorrência.

#### 8.4 Importador

Adaptador anti-corrupção em duas camadas. A **camada pública** recebe linhas já normalizadas, resolve o
`customer_id` (nativo ou por âncoras via resolvedor de identidade), valida e reporta
`{total, added, deduped, resolved, unresolved, rejected}`. A **camada de arquivo** lê CSV/xlsx aplicando o
`column_map` do mailing e remapeia rejeições para número de linha. Rejeita-linha-e-continua: um arquivo sujo
nunca aborta a importação.

#### 8.5 Limite honesto

O outbound aqui é forte em **orquestração assíncrona** — link, mensagem, engajamento adiável. Não há **discador
preditivo**: pacing power/predictive/progressive/preview, guard de abandonment ratio TCPA/LGPD e listas DNC são
roadmap. Para operação de voz ativa em massa, os incumbentes (Genesys, NICE, Five9) entregam hoje e o PlugHub
não.

> **Vs. concorrência:** nos incumbentes, outbound é módulo licenciado à parte, com configuração, billing e time
> próprios, e os agentes de IA não atravessam a fronteira inbound/outbound. Aqui é o mesmo motor declarativo, o
> mesmo pool de licenças e os mesmos especialistas — a fronteira não existe.

---

## Parte 4 — O que falta decidir antes da v2 completa

Três decisões suas, que mudam o resultado:

**Substituir ou versionar.** A v2 substitui o arquivo atual (que vira histórico no git) ou coexiste? Recomendo
substituir — dois descritivos técnicos circulando é como surge material desatualizado em reunião de cliente.

**Público-alvo primário.** A v1 mira "avaliador técnico de cliente prospectivo" e por isso carrega os blocos "Vs.
concorrência". Se o uso principal passar a ser due diligence de investidor, esses blocos mudam de tom (menos
comparação, mais durabilidade do fosso). Dá para servir aos dois, mas fica mais longo.

**Nível de profundidade.** A v1 tem ~536 linhas. Com o que shipou, a v2 honesta fica entre 750 e 900. Se o
objetivo é leitura em reunião, cabe uma versão condensada de ~400 linhas com o detalhe remetido a `docs/arcos/`.

---

## Apêndice — Base de verificação

`CLAUDE.md` (arquitetura viva, invariantes), `CHANGELOG.md` até 27/07/2026, inventário de `packages/` (22
serviços Python confirmados por varredura de `pyproject.toml`), `docs/arcos/` e `docs/product/`.

Nenhuma afirmação de estado neste protótipo foi escrita sem lastro no CHANGELOG ou no `CLAUDE.md`. Onde o estado
é parcial, está marcado como parcial.
