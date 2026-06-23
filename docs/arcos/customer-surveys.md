# Customer Surveys — Módulo de Pesquisas de Satisfação (spec / ADR)

> Estado: **spec / ADR** (não implementado). Generaliza o NPS de fim-de-contato
> (`skill_nps_v1` + hook `on_contact_end` + `survey_record` → `session_signal`) num
> **módulo de pesquisas de satisfação** com 5 instrumentos (CSAT, NPS, CES, PMF, FCR),
> gatilho condicionado ao **outcome** do ciclo de atendimento, **política de quarentena**,
> **interface web** com envio de link, e integração com a **bancada 360°** (lente cliente).
> Relacionados: `docs/arcos/analytics-agents-workbench.md` (§7, §8, §14 — `session_signal`),
> `docs/guias/pool-hooks.md`, `docs/guias/conference-mechanics.md` (§ Mudança 23),
> `docs/arcos/arc6-evaluation.md` (forms/campaigns/sampling), `docs/arcos/arc19-unified-session-model.md`.
> Fora de escopo nesta fase (referenciados em §13): **Health Score** (métrica composta) e
> **cadastro dinâmico de cliente** (identidade cross-canal, hábitos, produtos).

---

## 1. Visão e escopo

A pergunta do módulo: **"o cliente saiu satisfeito deste ciclo, e o que isso diz sobre o
relacionamento?"** — coletada no momento certo, sem fadigar o cliente, atribuída ao agente
(humano ou IA) responsável, e comparável na mesma bancada que o quantitativo e a avaliação de QA.

O que o módulo define:

1. **5 instrumentos** de pesquisa — CSAT, NPS, CES, PMF, FCR — mais **Health Score** como
   métrica composta futura (§13).
2. **Gatilho por outcome** — uma pesquisa só inicia quando há **ciclo fechado de atendimento**
   (§5). Segmento transferido/abandonado nunca dispara pesquisa.
3. **Modelo de dados** dos 5 instrumentos + **associação à base de cliente** existente (§7).
4. **Política de quarentena** anti-fadiga (janela de bloqueio, amostragem, prioridade, expiração) (§6).
5. **Interface web** mobile-first com **envio de link** para pesquisa diferida (§9).
6. **Integração com a bancada** — generaliza a lente `nps` numa **"Voz do cliente"** com os 5
   instrumentos + cruzamentos (§10).
7. **Editor, leitura e ação** — form-builder (§9.3/§16/§17), navegador de respostas (§10b), agente IA
   analista de verbatims (§10c) e retorno outbound via caixa de ações do Console (§19); mais a capacidade
   **transversal** de histórico de contatos do cliente (§20).

**Premissa central**: o módulo **reusa o pipeline existente** (`survey_record` → `session.signals`
→ `analytics.session_signal`) como camada analítica, e **adiciona** uma camada operacional em
PostgreSQL (definições, instâncias, tokens de link, respostas, quarentena) que hoje não existe.

---

## 2. Achados no código (estado atual)

Validado nos artefatos do repositório:

- **`survey_record` (MCP) já é genérico** — `SurveyRecordInputSchema` (`packages/schemas/src/survey.ts`)
  aceita `grain ∈ {segment, session, workflow, journey}` e **N métricas** por chamada
  (`signals[{metric, value, value_label?}]`, até 20). `metric` é snake_case livre; o consumer
  **normaliza apenas `nps` e `csat`** hoje. Tópico `session.signals` → `parse_session_signal_event`
  → `analytics.session_signal` (ClickHouse, ReplacingMergeTree, dedup
  `(tenant, session, grain, segment_id, metric)`, bucket por `session_at`).
- **`session_signal.source`** hoje = `customer_nps | customer_csat | customer_survey`. As métricas
  extras (CES/PMF/FCR) caem em `customer_survey` sem normalização de label.
- **Hook `on_contact_end` é o mecanismo genérico de fim-de-contato** (G7 Fase 3b). O bridge segura
  a sessão do cliente (`posatt:customer_active`) e roda o skill do pool configurado **na conferência**
  — válido para qualquer pool, **humano OU IA** (`conference-mechanics.md` § Mudança 23). Config do
  hook no pool YAML: `{ pool, side, nps_on_disconnect }`. `on_human_end` = fim-de-**segmento** humano
  (hoje usado para wrap-up `side: agent`).
- **`skill_nps_v1`** (arquivo `agente_nps_v1.yaml`) é a "pesquisa simples" citada: pergunta 0–10
  ("recomendaria…"), disparada no fim de contato, ramifica o grão (`segment` se há
  `@ctx.session.surveyed_segment_id`, senão `session`) e grava via `survey_record`.
  **→ Achado de nomenclatura (reportado pelo usuário):** o slot é **transacional** (fim de cada ciclo)
  — estruturalmente um **CSAT** —, mas o instrumento embutido é **NPS** (escala 0–10, "recomendaria").
  Instrumento e gatilho estão **colados** num skill por métrica. O §3 corrige isso.
- **`skill_survey_v1`** é a survey **OUTBOUND/diferida** (perfil webhook, pool `survey_processo_ia`):
  disparada por `workflow_trigger` no passo final do fluxo primário, religa via `origin_session_id`,
  coleta NPS real via `delegate` → `survey_collector_ia` (reconexão `inbound_only`), grava com
  `survey_record(grain=session)`. **Não há gate de quarentena nem link web** — só o canal de reconexão.
- **Bancada**: lentes `nps` (grão segment, atribuível ao agente) e `session_nps` (grão session,
  contexto) já existem em `/reports/agents/compare`; seção "Voz do cliente" no detalhe type-aware
  (`AgentsBenchPage`). Sem lentes CSAT/CES/PMF/FCR.
- **Outcome do segmento**: `segments.outcome` (cânone `SegmentOutcomeSchema`) já é propagado do
  wrap-up para o `primary` humano (F1) e do `complete` para a IA; valores normalizados
  `resolved | escalated | suspended | abandoned | …`. `close_reason` (enum) distingue
  iniciativa (`customer_*` vs `agent_*`).

**Conclusão**: ~70% da plumbing existe. O que falta é (a) separar instrumento de gatilho,
(b) normalizar CES/PMF/FCR, (c) a camada operacional (definições/instâncias/tokens/quarentena),
(d) a interface web pública, (e) as lentes da bancada.

---

## 3. Princípio central — separar INSTRUMENTO de GATILHO

A dívida atual ("um skill por métrica", instrumento colado ao gatilho) não escala para 5
instrumentos × N posições de disparo. O módulo separa três eixos ortogonais:

| Eixo | O que é | Onde vive |
|---|---|---|
| **Instrumento** | qual pergunta, qual escala, qual normalização (CSAT/NPS/CES/PMF/FCR) | `survey_definition` (dado, UI-editável) |
| **Gatilho** | quando dispara (a **decisão** lê o outcome e a quarentena; ou agendado/diferido) | **skill-flow** (choice + `survey_eligibility_check`) + agendador |
| **Veículo** | como o cliente responde (na conferência; ou link web outbound) | survey-runner skill + Channel Gateway |

A plataforma só provê o **mecanismo** (despachar o hook + segurar a sessão + expor o outcome no
ContextStore); **quando** e **se** pesquisar é decisão do skill customizável (§5).

Um **único** `skill_survey_runner_v1` (genérico) renderiza **qualquer** instrumento a partir da
`survey_definition` referenciada pelo gatilho — em vez de `skill_nps_v1`, `skill_csat_v1`, … . O
`survey_record` já comporta isso (N métricas, `metric` livre). `skill_nps_v1`/`skill_survey_v1`
permanecem funcionando (não quebrar), marcados **legado**, e migram para o runner no cutover (§12).

> **Decisão de nomenclatura**: o slot de fim-de-contato passa a ser, por padrão, **CSAT**
> (satisfação com *esta* interação — escala 1–5). NPS vira instrumento **relacional** (periódico,
> §6) ou bloco adicional ocasional do questionário híbrido (§4.6). O instrumento é escolhido na
> `survey_definition`, não no nome do skill.

---

## 4. Os 5 instrumentos + normalização

Todos cabem em `signals[{metric, value, value_label}]` e numa linha por métrica em `session_signal`.
A **comparabilidade é propriedade da métrica** (regra da bancada): cada instrumento normaliza para
escala/label comum, viabilizando comparação cross-pool.

| Instrumento | `metric` | `source` | Escala bruta | Normalização (`value_label`) | Foco / momento |
|---|---|---|---|---|---|
| **CSAT** | `csat` | `customer_csat` | 1–5 (ou 1–10) | `satisfied`(≥4) / `neutral`(3) / `dissatisfied`(≤2) | satisfação com a interação — fim do ciclo |
| **NPS** | `nps` | `customer_nps` | 0–10 | `promoter`(≥9) / `passive`(7–8) / `detractor`(≤6) | lealdade/relacionamento — periódico |
| **CES** | `ces` | `customer_ces` | 1–7 (concordância) | `low_effort`(≥5) / `neutral`(4) / `high_effort`(≤3) | esforço — pós-resolução/ativação |
| **PMF** | `pmf` | `customer_pmf` | 1–3 categórico | `very_disappointed`(1) / `somewhat`(2) / `not`(3) | product-market fit — relacional/produto |
| **FCR** | `fcr` | `customer_survey` | 0/1 binário | `resolved`(1) / `unresolved`(0) | resolução no 1º contato — fim do ciclo |

Notas:

- **CES** — escala invertida em "esforço": nota alta = bom (baixo esforço). A normalização guarda a
  semântica "≥5 = bom" no `value_label`; o eixo da bancada exibe o rótulo, não o número cru.
- **PMF** — categórico puro; o KPI de sucesso é **% `very_disappointed` ≥ 40%** (Sean Ellis), computado
  na agregação, não na linha.
- **FCR — duas fontes** (a referência separa "monitorado internamente" × "validado pelo cliente"):
  - **FCR perguntado** (cliente, binário) → `session_signal(metric=fcr)`.
  - **FCR computado** (sistema) → derivado de `segments` (contato único na janela + `outcome=resolved`)
    no namespace determinístico **`session_metric.fcr`** (metodologia §, `arc-evaluation-metrics-methodology.md`).
    Serve de cross-check da disposição auto-reportada (§8).
- **Health Score** — **não** é instrumento de pergunta única; é composto (§13). Consome o histórico
  de surveys (entre outras fontes). Fora de escopo agora.

O consumer (`parse_session_signal_event`) ganha as normalizações `ces/pmf/fcr` (hoje só `nps/csat`);
`source` ganha `customer_ces | customer_pmf`. Mudança aditiva, sem migração destrutiva.

### 4.6 Questionário híbrido (CSAT + NPS + motivo + próximo passo)

A referência descreve um questionário de 4 blocos. Mapeamento direto no runner (uma execução,
múltiplos signals + ramificação):

1. **CSAT** (1–5) — experiência recente → `signals[{metric:csat}]`.
2. **NPS** (0–10) — relacionamento global → `signals[{metric:nps}]` (mesma chamada).
3. **Motivo** (texto aberto) — **detalhe sob demanda**, não vai ao ledger em massa (LGPD):
   gravado em ContextStore/stream com controle de acesso; exibido só no detalhe da bancada.
4. **Próximo passo** (ação por nota) — `choice` no runner: detrator → oferta de callback do gerente
   (Sim/Não → grava intenção); promotor → link de avaliação pública (G2/Reviews).

Branching e múltiplas métricas já são nativos do skill-flow + `survey_record`.

---

## 5. Gatilho por outcome — decisão no skill, mecanismo na plataforma

> **Decisão (2026-06-23, revisão):** a regra "só pesquisar com ciclo fechado" **não** vira lógica de
> plataforma. O hook `on_contact_end`/`on_human_end` já é **genérico e outcome-independente**; ele
> **despacha sempre**, e **o skill-flow decide** — recebe o outcome no ContextStore, consulta a
> quarentena se precisar, e auto-encerra quando não deve pesquisar. Mantém a lógica de negócio
> **fora da plataforma e dentro de skills customizáveis** (princípio do repo: skills são customização
> da instalação; a plataforma provê os 4 hooks + segurar a sessão + despachar o skill —
> `analytics-agents-workbench.md` §14.2).

### Contrato (separação plataforma × skill)

- **Plataforma (mecanismo)** — despacha o hook, segura a sessão do cliente (`posatt:customer_active`)
  e **expõe o outcome no ContextStore** antes de disparar: além de `session.close_origin`/
  `surveyed_segment_id`/`surveyed_agent_key` (já escritos), carimba **`session.contact_outcome`** e
  **`session.segment_outcome`** (o fato; não a decisão). Esse é o **único pré-requisito de plataforma**
  desta revisão — expor um dado, não uma regra.
- **Skill (decisão)** — o `skill_survey_runner_v1` abre com um `choice` que lê
  `@ctx.session.contact_outcome` e ramifica; quem ditou "só `resolved`" é o YAML do skill, não a config
  do hook. A mesma cadeia consulta `survey_eligibility_check` (quarentena/amostragem, §6) e escolhe o
  instrumento. **Toda** a decisão de pesquisa mora num lugar: o skill.

### Convenção do runner padrão (ciclo fechado)

O `skill_survey_runner_v1` **vem com** o gate de ciclo fechado — é convenção do skill, customizável,
não invariante de plataforma. Comportamento default:

| Situação (lida do ContextStore) | `contact_outcome` | Default do runner |
|---|---|---|
| Agente resolveu e o contato encerra | `resolved` | **pesquisa** (atribuível ao segmento resolvedor via `surveyed_segment_id`) |
| Transferido/escalado (contato continua) | `escalated` / `transferred` | auto-encerra (ciclo não fechou) |
| Cliente abandonou / desconectou | `abandoned` + `close_origin=customer_disconnect` | auto-encerra |
| Suspenso (workflow vai retomar) | `suspended` | auto-encerra (não terminal) |

Posição do disparo = `on_contact_end` (fim-de-contato, 1ª classe) → "uma pesquisa por ciclo", não por
handoff. Tenant que queira pesquisar segmentos escalados (ex.: medir o agente que transbordou) só
edita o `choice` do seu skill — sem tocar na plataforma.

### Config de hook (enxuta — só mecanismo)

```yaml
# pool YAML — hooks. Sem outcome_filter/sampling: isso é decisão do skill.
on_contact_end:
  - pool: survey_ia                 # pool que roda o skill_survey_runner_v1
    side: customer
    survey_id: survey_csat_default   # → survey_definition (instrumento + branching)
    survey_on_disconnect: skip       # OPCIONAL: short-circuit de PERF (não segura a sala se o
                                     # cliente caiu). Não é onde a lógica vive — o skill ainda
                                     # checa o outcome. Default: despachar e deixar o skill decidir.
```

**FCR computado** não usa hook (é determinístico): emitido pelo extractor de `session_metric.*` no
ingest, condicionado a contato único + `outcome=resolved`.

---

## 6. Política de quarentena (anti-fadiga) — net new

Hoje **não existe** controle de frequência: um cliente com 3 contatos na semana receberia 3
pesquisas. O módulo adiciona uma **política de quarentena por cliente × tipo de instrumento**.

### Regras (configuráveis por tenant, override por pool)

1. **Janela de bloqueio** — intervalo mínimo entre surveys do mesmo tipo por cliente.
   Default B2B 15d, B2C 7d (`block_window_days` por tipo).
2. **Amostragem** — em alto volume, `sampling_pct` por gatilho (ex.: 20% dos contatos resolvidos).
   Reusa a lógica determinística por hash já usada na amostragem de QA (Arc 6).
3. **Prioridade de gatilho** — se múltiplos elegíveis no mesmo dia, vence o mais crítico
   (taxonomia `priority` por categoria/pool; ex.: logística > produto).
4. **Expiração do link** — survey transacional por link expira (default 48h); **sem lembretes**.
5. **Exceção** — nota negativa anterior + categoria crítica (cobrança/cancelamento) **quebra** a
   quarentena.
6. **NPS relacional × transacional** — um CSAT/CES respondido coloca o cliente em quarentena de NPS
   na janela (o transacional "anula" o relacional próximo — regra de convivência da referência).

### Onde mora o estado

- **Ledger durável**: tabela `survey_quarantine` (PostgreSQL, §7) — `(customer_key, survey_type) →
  last_sent_at, last_score, last_category`.
- **Fast-path**: Redis `{tenant}:survey:quarantine:{customer_key}:{type}` (TTL = `block_window`),
  consultado no despacho.
- **Veículo de checagem — tool MCP `survey_eligibility_check`** (contrato concreto):

  | | |
  |---|---|
  | **Host** | exposta pelo `mcp-server-plughub` (thin — invariante "mcp-server só expõe tools"); a **lógica** vive na **evaluation-api** `POST /v1/survey/eligibility/claim` (lê Redis fast-path + PG ledger, aplica política). |
  | **Input** | `tenant_id` (explícito), `customer_key` (nullable), `survey_type` (csat\|nps\|ces\|pmf\|fcr), `category?`, `origin_session_id`, `claim` (default `true`). |
  | **Output** | `{ eligible: bool, reason: ok\|quarantined\|sampled_out\|locked, block_until?: iso }`. |
  | **Semântica** | `customer_key` null/ausente (cliente anônimo) → `eligible=true` (não dá pra fatigar quem não se identifica entre contatos). Senão: quarentena (janela) → amostragem (hash determinístico) → prioridade (lock curto por cliente). `claim=true` em elegível **grava `last_sent_at`** (a janela começa no **envio**, não na resposta) — a resposta depois finaliza `last_score/last_category`. Auditada via McpInterceptor. |

  O `skill_survey_runner_v1` chama a tool **depois** do gate de outcome (não antes — não se reserva
  quarentena de um ciclo que nem vai pesquisar) e auto-encerra se `eligible=false`. Mantém o bridge
  "burro" e respeita "agentes só acessam backend via MCP".

> **Decisão (host)**: definições + política + ledger ficam na **evaluation-api** (já dona de
> forms/campaigns/sampling/result-ingest no Arc 6) sob um namespace `survey` novo — evita um pacote
> novo e reusa o motor de amostragem. Se o domínio crescer, gradua para um `survey-api` dedicado.
> A **separação de ator** (avaliador interno × cliente externo) fica nas tabelas/rotas, não no pacote.

---

## 7. Modelo de dados — operacional (PostgreSQL) + analítico (ClickHouse)

Dois substratos com papéis distintos:

### 7.1 Analítico (ClickHouse) — já existe, estendido

`analytics.session_signal` permanece a **fato-tabela** (1 linha por métrica, bucket `session_at`),
agora alimentada também por CES/PMF/FCR. **Nenhuma mudança de esquema** além de aceitar os novos
`source`/`metric` e normalizações. É a fonte das lentes da bancada (§10).

### 7.2 Operacional (PostgreSQL, schema `survey`) — NOVO

O ClickHouse não basta para a operação: link tokens, expiração, idempotência de resposta,
quarentena e surveys diferidas exigem estado mutável transacional.

```
survey_question              -- BIBLIOTECA de perguntas reutilizáveis (form-builder, §9.3)
  question_id (PK)           -- question_{name}_v{n}
  tenant_id
  metric                     -- csat | nps | ces | pmf | fcr | open_text
  prompt, help_text          -- enunciado + apoio (i18n)
  scale                      -- jsonb: { min, max, kind: numeric|categorical|binary|text }
  options                    -- jsonb[]: rótulos por opção (botão/lista)
  active, created_at, updated_at

survey_definition            -- o FORMULÁRIO (template), composto de perguntas — UI-editável
  survey_id (PK)             -- survey_{type}_{name}_v{n}
  tenant_id
  type                       -- csat | nps | ces | pmf | fcr  (governa normalização)
  title, intro_text
  questions                  -- jsonb[]: blocos ORDENADOS = refs a survey_question (question_id)
                             --   OU perguntas inline; mistura permitida
  normalization              -- jsonb: faixas → value_label (herda do type; override por form)
  branching                  -- jsonb: ações por faixa (callback, review link, §4.6)
  channels                   -- [webchat, whatsapp, email, sms, web_link]
  active, created_at, updated_at

survey_quarantine_policy     -- regras por tipo (§6), override por pool
  policy_id (PK), tenant_id, type, pool_id (nullable)
  block_window_days, sampling_pct, timeout_hours
  priority, exception_rules  -- jsonb

survey_instance              -- uma OCORRÊNCIA de pesquisa
  instance_id (PK)
  tenant_id, survey_id
  origin_session_id          -- a sessão/contato pesquisado (chave de religação)
  grain                      -- segment | session | workflow | journey
  segment_id (nullable)      -- quando grain=segment
  agent_key (nullable)       -- atribuição (segment)
  pool_id
  customer_key               -- *** JOIN com a base de cliente (§7.3) ***
  channel
  status                     -- pending | sent | responded | expired | skipped_quarantine
  link_token_hash (nullable) -- SHA-256 do token de link web (single-use)
  link_expires_at
  session_at, sent_at, responded_at

survey_response              -- a RESPOSTA (fonte operacional da verdade)
  response_id (PK)
  instance_id (FK)
  signals                    -- jsonb[]: { metric, value, value_label }
  open_text (nullable)       -- LGPD: acesso controlado, NÃO replicado em massa ao ledger
  response_channel, responded_at

survey_quarantine            -- ledger por cliente × tipo (§6)
  tenant_id, customer_key, type (PK composta)
  last_sent_at, last_score, last_category
```

**Fluxo de escrita**: resposta capturada (conferência ou web) → grava `survey_response` (PG, fonte
operacional) → **emite `survey_record`** → `session.signals` → `session_signal` (projeção analítica).
PG = verdade operacional; ClickHouse = verdade analítica. Mesma resposta, dois destinos, um caminho
de escrita canônico (`survey_record`).

### 7.3 Associação à base de cliente existente

A entidade comparável da pesquisa precisa de uma **chave de cliente estável** para: deduplicar,
aplicar quarentena, e ligar surveys diferidas. Hoje a identidade do contato vive em:

- `@ctx.caller.*` (ContextStore — dados do cliente resolvidos do CRM no atendimento),
- `@ctx.session.contact_identifier` (handle do canal — usado na religação outbound, `skill_survey_v1`),
- `analytics.sessions.origin_session_id` (rastreabilidade multi-sessão, pós-Arc 19).

**Decisão**: `survey_instance.customer_key` é a coluna-junção, populada na ordem de precedência:
`caller.customer_id` (se o CRM resolveu) → senão `contact_identifier` normalizado (canal+handle).
Isto **associa a pesquisa à base de cliente já existente** sem depender do cadastro dinâmico futuro;
quando o **cadastro de cliente** (§13) entrar, `customer_key` passa a apontar para `customer.id`
(identidade positiva cross-canal) — **sem migração de esquema** (só re-resolução da chave). A
quarentena e o histórico de surveys já ficam keyed por `customer_key`, prontos para o Health Score.

---

## 8. O cruzamento (o payoff) — alinhado à bancada §8

O valor de gestão está na **divergência** entre as camadas (a bancada já formaliza isso em §8 e na
view Cross-cut). O módulo de surveys adiciona linhas comparáveis:

- **CSAT alto × avaliação de IA baixa** → gap percepção-vs-processo.
- **FCR perguntado "sim" × FCR computado "não"** (reabertura/segundo contato) → acurácia da resolução.
- **CES alto esforço × resolution alta** → resolveu, mas com atrito (risco de churn — melhor preditor
  de deslealdade).
- **NPS baixo × CSAT alto recorrente** → bom no tático, fraco no relacional (alerta de Health Score).

Efeito de 2ª ordem (já previsto na bancada): **NPS/CSAT são ground-truth externo que calibra o
avaliador de IA** (Arc 13) — divergência sistemática alimenta o Calibration Dashboard, ajusta o
rubric, não o agente.

---

## 9. Interface web + envio de link

Dois veículos, escolhidos pela presença do cliente e pelo tipo:

### 9.1 Na conferência (síncrono) — transacional, cliente presente

O `skill_survey_runner_v1` renderiza o instrumento via `notify`/`menu` no canal ativo
(webchat/whatsapp — botões, lista, escala). É o caminho do CSAT/CES/FCR de fim-de-contato quando o
cliente está conectado. **Sem link** — zero atrito (referência: "widget abre ao encerrar a conversa").
Já funciona para NPS; generalizado pelo runner.

### 9.2 Link web (diferido/outbound) — relacional ou cliente ausente

Para NPS/PMF relacional, ou quando o cliente já saiu:

- **Página pública** `/{tenant}/survey/:token` — **módulo público no platform-ui** (rota sem auth,
  layout próprio, mobile-first; **não** um app novo — respeita a regra "nunca criar packages/*-ui").
  Renderiza o instrumento da `survey_definition`, ≤ 45s, um clique por bloco.
- **Token** — `plughub_sv_{43-char}`, SHA-256 em `survey_instance.link_token_hash`, **single-use**,
  TTL = `link_expires_at` (default 48h). JWT/segredo nunca na URL (padrão webhook/webchat do repo).
- **Envio do link** — via **Channel Gateway outbound** (email/SMS/WhatsApp template), reusando o
  mecanismo `collect`/outbound (perfil workflow, Arc 19). O disparo é um `survey_instance` em
  `status=sent`.
- **Submit** — endpoint público (na evaluation-api, sub-router público com rate-limit) valida o token
  → grava `survey_response` → `survey_record` → `session_signal`. **Idempotente** (token single-use;
  segunda submissão = 409). **Sem lembrete** após expiração.
- **Religação** — `survey_instance.origin_session_id` liga a resposta diferida à sessão original;
  `session_at` é **enriquecido** com o `opened_at` da origem (regra de ouro §7 da bancada — F11.1 já
  faz isso para `session.signals`).

### 9.3 Operação (operador) — editor de formulários no platform-ui autenticado

Módulo `/config/surveys` (role admin, ABAC `config`), **form-builder**:

- **Biblioteca de perguntas** (`survey_question`) — CRUD de perguntas reutilizáveis (enunciado,
  métrica, escala, opções, i18n). Uma pergunta serve a vários formulários.
- **Formulários** (`survey_definition`) — **vários por tipo** (ex.: `survey_csat_suporte`,
  `survey_csat_logistica`): o operador monta o formulário **selecionando e ordenando** perguntas da
  biblioteca (ou inline), define branching (§4.6) e canais. O `type` governa a normalização; o
  `survey_id` identifica o formulário específico.
- **Binding** — na config do hook (ou do agendado/diferido), o operador **seleciona qual formulário**
  (`survey_id`) aquele gatilho usa. O mesmo pool pode ter formulários diferentes por contexto.
- **Política de quarentena** (`survey_quarantine_policy`) — editável por tipo, override por pool.

Todo campo UI-editável (invariante de configuração do repo). i18n en + pt-BR. **No publish, o formulário
ganha draft/published na evaluation-api e é lido em runtime por um único interpretador**
(`skill_survey_runner_v1`) — aproximação **B decidida** (§17); o compile-to-skill (§16) é a alternativa.

---

## 10. Integração com a bancada 360°

A bancada já tem `nps` (grão segment, por agente) e `session_nps` (grão session, contexto). Em vez de
5 lentes novas poluindo o seletor, o módulo agrupa numa **lente "Voz do cliente"** com sub-seletor de
métrica — a **"lente cliente"** sugerida pelo usuário, que passa a visão do cliente:

- **Lente `customer_voice`** (universal humano + IA): sub-métrica `{csat, nps, ces, pmf, fcr}`, curva
  no tempo bucketizada por `session_at`, normalizada (rótulo no eixo, não o cru), **N/cobertura sempre
  visível** (toda métrica de survey é amostral). Grão `segment` = linha por agente; grão `session` =
  contexto do contato (não atribuível, exibido como faixa de referência).
- **View "Visão do cliente"** (toggle, como a Cross-cut atual): consolida **todas** as métricas de
  survey por agente/pool lado a lado, com as divergências do §8 destacadas (★ concordância,
  ⚠ gap de percepção CSAT×QA, ◑ esforço×resolução). Tabela + quadrante (volume×satisfação).
- **Detalhe type-aware**: a seção "Voz do cliente" ganha as 5 métricas (hoje só NPS), com response-rate
  por instrumento e o **motivo (texto aberto)** sob demanda (acesso controlado, LGPD).
- **Regras transversais herdadas** (§10 da bancada): média = aritmética dos agentes (N visível);
  ausente ≠ zero (gap); cor estável por entidade; escopo ABAC (`accessible_pools`/
  `supervised_agent_types`); persistência seleção+lente na URL; export CSV.

**Endpoint**: estende `/reports/agents/compare?lens=customer_voice&metric=csat|nps|ces|pmf|fcr` —
mesmo contrato multi-entidade/multi-série já existente; lê `session_signal` filtrando `source/metric`.
A view consolidada reusa `/reports/agents/cross` com as novas métricas.

---

## 10b. Navegador de respostas — Analytics/Survey (lista por tipo, ver/ouvir)

A bancada (§10) **agrega** (curvas, médias — fonte ClickHouse `session_signal`). Complementar a ela,
uma superfície dedicada lista **resposta a resposta**, para inspeção qualitativa.

- **Rota** `/analise/surveys` (ABAC `visualizar`/report). **Lista agrupada por tipo** (CSAT/NPS/CES/
  PMF/FCR), com filtros (período, pool, canal, faixa de nota, categoria de classificação do §10c) e
  **N/cobertura** sempre visível.
- **Fonte = PG `survey_response`/`survey_instance`** (operacional, por-resposta) — não o agregado. Cada
  linha: nota normalizada, categoria, agente/segmento atribuído, cliente (`customer_key`), `session_at`.
- **Detalhe da resposta**: signals + **verbatim (texto aberto)** e, para canais de voz/WebRTC,
  **áudio reproduzível** — o `survey_response` linka o artefato de áudio (upload do Channel Gateway) +
  transcrição. Player inline ("escutar"). **LGPD**: verbatim/áudio com acesso controlado (mesma postura
  do detalhe da bancada — papéis autorizados), nunca replicados em massa ao ledger analítico.
- **Ações**: abrir a sessão original (`origin_session_id`), reendereçar manualmente, exportar CSV.

> O áudio exige reter o artefato além da sessão (TTL próprio do `survey`); decisão de retenção/expiração
> herda a política de uploads do Channel Gateway (soft-delete + grace), parametrizável por tenant (LGPD).

---

## 10c. Agente IA analista de verbatims — avalia, classifica e endereça

As mensagens abertas do cliente (texto ou áudio transcrito) são a parte mais rica e a menos
estruturada. Um agente IA fecha o loop — análogo ao `agente_avaliacao_v1` (Arc 6), mas para a **voz
aberta do cliente**, não para a qualidade do agente.

- **`agente_survey_analyst_v1`** (perfil orchestrator, pool `survey_analyst_ia`): acionado por evento
  ao gravar uma resposta com verbatim (consumer de `session.signals`/`survey_response`, ou hook
  `post_survey`). Para áudio, primeiro STT (reusa os FallbackProviders de voz, Arc 15).
- **Avalia e classifica** via AI Gateway `reason` (com `output_schema`):
  `{ sentiment, theme[], urgency, actionable, suggested_action }`. As classificações voltam como
  **métricas/tags** no `survey_response` (e como `session_signal` extra para agregação — ex.:
  `metric=verbatim_theme`, `value_label=billing`).
- **Endereça** ("addressing"): conforme a classificação, dispara a ação — `escalate` para uma fila
  (detrator + cobrança → retenção), `workflow_trigger` (abrir caso/tarefa), ou tag para o time de
  produto (bug/feature). O roteamento passa pelo **Rules Engine** (publica consequência) ou por um
  `workflow_trigger` direto, respeitando a invariante "só o Routing Engine roteia conversas".
- **Conecta** ao "próximo passo" do §4.6 (a oferta automática ao detrator/promotor) e ao §8 (verbatim
  classificado é insumo de calibração do avaliador de IA — Arc 13).
- **Governança**: classificação é amostral/assistiva; o verbatim cru permanece LGPD-controlado. A
  ação de endereçamento é auditável (McpInterceptor).

### Retorno ao cliente (endereçamento outbound) — *detalhado em §19*

O endereçamento **ativo** (ex.: callback do gerente ao detrator) é um **contato outbound** no canal
escolhido (`collect`/Arc 19). O módulo de surveys só **emite o pedido** (classificação +
`suggested_action`); o gatilho outbound, a **caixa de ações no Console** (inbox pull **já existente** —
`PullInboxPanel`/`dispatch_mode`) e o fluxo claim → briefing → collect estão **detalhados no §19**.

---

## 11. Decisões travadas

- **Instrumento ≠ gatilho ≠ veículo** (§3) — um `skill_survey_runner_v1` genérico parametrizado por
  `survey_definition`; fim do "um skill por métrica". `skill_nps_v1`/`skill_survey_v1` = legado, migram
  no cutover.
- **Slot de fim-de-contato = CSAT por padrão** (corrige o achado de nomenclatura); NPS é relacional ou
  bloco híbrido opcional.
- **Formulários compostos de perguntas reutilizáveis** (§7.2/§9.3) — biblioteca `survey_question` +
  N `survey_definition` por tipo; o gatilho referencia o `survey_id` escolhido na config.
- **Editor: interpretador genérico (B) — decidido** (ADR §16×§17, mudança de engine aprovada
  2026-06-23) — 1 skill interpretador + form JSON; **engine estendido em 2 peças** (`$.config` do slot no
  flow + `menu.options/fields` dinâmicos, §17.3); **binding via `interface_schema` →
  `PoolSkillSlot.config_json`** (`form_id` + `survey_form_get`, §17.6). A (compile-to-skill) = alternativa.
- **Decisão de gatilho mora no skill, não na plataforma** (§5, revisão 2026-06-23) — o hook despacha
  sempre; o `skill_survey_runner_v1` lê `@ctx.session.contact_outcome` e decide (ciclo fechado vira
  convenção customizável do runner, não invariante de plataforma). Único pré-requisito de plataforma:
  carimbar `contact_outcome`/`segment_outcome` no ContextStore antes do hook. `survey_on_disconnect` =
  só short-circuit de performance.
- **Navegador de respostas** (§10b) separado da bancada — lista por tipo, fonte PG `survey_response`,
  verbatim + áudio reproduzível, LGPD-controlado.
- **Agente IA analista de verbatims** (§10c) — `agente_survey_analyst_v1` classifica (sentiment/tema/
  urgência) e endereça via Rules Engine/`workflow_trigger`; reusa `reason` + STT de voz.
- **`survey_record` é o caminho único de escrita** para a camada analítica (§7) — todos os grãos e
  instrumentos; PG é a verdade operacional, ClickHouse a projeção.
- **`customer_key` é a chave de associação à base de cliente** (§7.3) — `caller.customer_id` →
  fallback `contact_identifier`; forward-compatível com o cadastro dinâmico futuro, sem migração.
- **Quarentena via `survey_eligibility_check` (MCP)** no runner (§6) — bridge permanece dumb;
  ledger PG + fast-path Redis; host = evaluation-api (namespace `survey`), sem pacote novo.
- **Interface web = módulo público no platform-ui** (rota sem auth) + envio por Channel Gateway
  outbound; token single-use SHA-256, expira sem lembrete (§9).
- **Bancada = lente `customer_voice` + view "Visão do cliente"** (§10), não 5 lentes soltas.
- **Health Score e cadastro de cliente = fora de escopo** (§13) — só os ganchos de dados (customer_key,
  histórico de survey) ficam prontos.

---

## 12. Plano de fases (validável incrementalmente)

| Fase | Escopo | Entrega validável |
|---|---|---|
| **S1 — Normalização dos 5 instrumentos** | consumer `parse_session_signal_event` ganha `ces/pmf/fcr` (escala+label); `source` += `customer_ces/customer_pmf`; testes de normalização | seed `session.signals` dos 5 metrics → linhas normalizadas no ClickHouse |
| **S2 — Survey-runner genérico + survey_definition** | `survey_question` (biblioteca) + `survey_definition` (PG, evaluation-api) + CRUD admin-token; `skill_survey_runner_v1` lê a definição e renderiza (notify/menu); migra o caso CSAT de fim-de-contato | hook `on_contact_end → survey_ia(survey_csat_default)` dispara CSAT na conferência → `session_signal(csat)` |
| **S3 — Gatilho decidido no skill** | plataforma carimba `contact_outcome`/`segment_outcome` no ContextStore pré-hook; `choice` no runner gateia (default ciclo fechado); `survey_on_disconnect` = short-circuit opcional. **Sem `outcome_filter` na plataforma.** | escalado/abandonado: runner auto-encerra; resolvido: pesquisa — validado E2E, lógica 100% no YAML |
| **S4 — Quarentena** | `survey_quarantine_policy` + `survey_quarantine` (PG) + Redis fast-path + tool `survey_eligibility_check`; runner gateia | 2º contato do mesmo cliente na janela = `skipped_quarantine`; amostragem % honrada |
| **S5 — Interface web + link** | página pública `/survey/:token` (platform-ui), token single-use, endpoint público submit; envio via Channel Gateway outbound; religação `origin_session_id` + enrich `session_at` | link enviado por email → resposta web → `survey_response` + `session_signal(grain=session/journey)` |
| **S6 — Bancada: lente cliente** | lente `customer_voice` (sub-métrica) no compare; view "Visão do cliente"; detalhe type-aware com 5 métricas + motivo sob demanda; i18n | lente mostra CSAT/CES/FCR por agente; divergências no cross |
| **S7 — Form-builder** (B recomendada §17; A fallback §16) | `/config/surveys`: biblioteca `survey_question` + montagem do **form JSON** (selecionar/ordenar, N por tipo) + política. **B** (decidida): 1 interpretador `skill_survey_runner_v1` + engine estendido (`$.config` no flow + `menu.options/fields` dinâmicos) + draft/published do form (evaluation-api) + `survey_form_get`; binding `interface_schema`→`PoolSkillSlot.config_json` (`form_id`). **A** (alternativa): `SurveyCompiler` gera `skill_survey_{id}`. UI-editável, ABAC | admin cria 2 CSAT distintos; publica → 2 form JSON, o slot de cada hook aponta `form_id`, 1 interpretador renderiza |
| **S8 — Navegador de respostas (Analytics/Survey)** | rota `/analise/surveys`: lista por tipo (PG `survey_response`), filtros, detalhe com verbatim; player de **áudio** + transcrição (canais de voz); LGPD-controlado; export | abrir uma resposta, ler o verbatim, ouvir o áudio, ir à sessão original |
| **S9 — Agente IA analista de verbatims** | `agente_survey_analyst_v1` (pool `survey_analyst_ia`): STT (voz) + `reason` classifica `{sentiment, theme, urgency, action}` → tags no `survey_response` + `session_signal`; endereçamento via Rules Engine/`workflow_trigger` | verbatim "cobrança errada" → classificado detrator/billing/alta → caso aberto na fila de retenção |
| **S10 — Retorno outbound + caixa de ações** | pool pull `retorno_survey_humano`; analista parqueia a sessão outbound-intent (`work_queue`); skill de retorno: `on_human_start` briefing + menu `agents_only` (canal) → `collect`; modo auto (rules) alternativo. Reusa `PullInboxPanel`/`dispatch_mode` — **sem mudança na inbox** (§19) | analista classifica → item na inbox → operador Pull → briefing → dispara collect → cliente retorna |
| **S11 (futuro)** | NPS/PMF relacional **agendado** (scheduler contra a base de cliente, 90/180d) + grão `journey` E2E | survey diferida agendada dispara respeitando quarentena |

Ordem honra "espinha → gatilho → governança → captura → leitura → ação": normalização → runner →
outcome(skill) → quarentena → web/link → bancada → form-builder → navegador → analista IA → retorno outbound.
O **histórico de contatos do cliente** (§20) é transversal (qualquer atendimento) — arco próprio, fora desta fila.

---

## 13. Fora de escopo (referência) — Health Score e cadastro de cliente

### Health Score (métrica composta — arco próprio)
Não é survey de pergunta única: é uma **nota composta** (ex.: 0–100, faixa verde/amarelo/vermelho)
que cruza **comportamento** (frequência de uso/login, adoção de recursos), **financeiro** (faturas em
dia), **suporte** (volume de chamados recentes) e **voz do cliente** (CSAT/NPS/CES históricos). O
módulo de surveys **prepara o gancho**: histórico de survey keyed por `customer_key`, agregável por
cliente. O cálculo, as fontes não-survey (uso/financeiro) e o alerta proativo de churn ficam para o
arco Health Score / Customer Success.

### Cadastro dinâmico de cliente (identidade cross-canal — outra sessão)
O usuário sinalizou tratar em sessão separada. O seam já está desenhado: `customer_key` (§7.3) é a
junção; quando o cadastro entrar (identidade positiva em todos os canais, hábitos/preferências —
horários de contato/acesso —, produtos possuídos/de interesse, histórico de surveys), `customer_key`
passa a referenciar `customer.id` sem migração de esquema. O histórico de surveys deste módulo já
nasce associável ao cadastro.

---

## 14. Pendências / dependências de implementação

1. **Consumer** — normalização `ces/pmf/fcr` + `source` novos (`parse_session_signal_event`, analytics-api).
2. **PG schema `survey`** — `survey_question` (biblioteca), `survey_definition`,
   `survey_quarantine_policy`, `survey_instance`, `survey_response`, `survey_quarantine` (evaluation-api).
3. **`skill_survey_runner_v1`** — runner genérico parametrizado por `survey_definition`, com `choice`
   de outcome + `survey_eligibility_check`.
4. **Plataforma (mecanismo do hook)** — carimbar `session.contact_outcome`/`session.segment_outcome`
   no ContextStore **antes** de disparar o hook; config de hook enxuta (`survey_id`,
   `survey_on_disconnect`). **Sem `outcome_filter`/`sampling_pct` na plataforma** — decisão no skill.
5. **Tool MCP `survey_eligibility_check`** — quarentena + amostragem + prioridade; ledger PG + Redis.
6. **Interface web pública** — rota platform-ui `/survey/:token` + endpoint público submit
   (evaluation-api) + envio outbound (Channel Gateway).
7. **FCR computado** — extractor `session_metric.fcr` (contato único + `outcome=resolved`).
8. **Bancada** — lente `customer_voice` (compare), view "Visão do cliente" (cross), detalhe type-aware,
   i18n en + pt-BR.
9. **Editor de formulários (B, §17)** — `/config/surveys` (biblioteca + form-builder → **form JSON**);
   `survey_definition` com **draft/published** na evaluation-api; tool MCP **`survey_form_get`**
   (form_id → JSON publicado); `interface_schema` `{ form_id }` no `skill_survey_runner_v1` +
   `PoolSkillSlot.config_json` no deploy. *(Alternativa A: `SurveyCompiler` → `skill_survey_{id}`, §16.)*
9b. **Extensão do engine (2 peças, §17.3/§18)** — (i) plumbing do `PoolSkillSlot.config_json` ao launch
   (bridge/worker) + exposição `$.config.*`; (ii) `menu.options`/`menu.fields` união `array | ref` +
   resolução via `resolveInputValue` no `menu.ts`; (iii) render `interaction=form` (fallback no adapter).
9c. **Refinamento `session.signals`** — `open_text?`/`survey_id?`/`instance_id?` no evento + consumer
   evaluation-api materializando `survey_response`/`survey_instance` do mesmo tópico (§16.5).
10. **Navegador de respostas** — `/analise/surveys` (lista por tipo, detalhe, player de áudio + STT,
    LGPD), fonte PG `survey_response`.
11. **Agente IA analista** — `agente_survey_analyst_v1` (classificação `reason` + endereçamento via
    Rules Engine/`workflow_trigger`; STT para áudio).
11b. **Retorno outbound + caixa de ações no Console (§19)** — outbound via `collect`/Arc 19; modo auto
    (rules → `workflow_trigger`) OU **inbox pull JÁ EXISTENTE** (`PullInboxPanel`/`dispatch_mode`/
    `work_queue`): retorno = **sessão outbound-intent parqueada** num pool pull (`retorno_survey_humano`),
    skill de retorno faz o `collect` pós-claim. Novo = pool + enfileirar a sessão + skill. Sem mudança na inbox.
12. **Cutover** — `skill_nps_v1`/`skill_survey_v1` → runner; manter compat durante a transição.
13. **Histórico de contatos do cliente (transversal, §20)** — drill lista→transcrição na `HistoricoTab`
    (wiring do endpoint existente + ACL/masking LGPD) + busca `/sessions/customer/{id}/search`. Útil a
    qualquer atendimento; **arco próprio (spec)**: `docs/arcos/customer-contact-history.md`.

---

## 15. Apêndice — esqueleto do `skill_survey_runner_v1` + tool de elegibilidade

### 15.1 Tool MCP `survey_eligibility_check` (schema)

```ts
// packages/schemas/src/survey.ts (adição)
export const SurveyEligibilityInputSchema = z.object({
  tenant_id: z.string().min(1),
  customer_key: z.string().nullable(),                 // null = cliente anônimo → elegível
  survey_type: z.enum(["csat", "nps", "ces", "pmf", "fcr"]),
  category: z.string().optional(),                     // prioridade/exceção
  origin_session_id: z.string().min(1),                // trace/auditoria
  claim: z.boolean().default(true),                    // true = grava last_sent_at (reserva a janela)
})
export const SurveyEligibilityOutputSchema = z.object({
  eligible: z.boolean(),
  reason: z.enum(["ok", "quarantined", "sampled_out", "locked"]),
  block_until: z.string().datetime().nullable().default(null),
})
```

Tool no `mcp-server-plughub` (thin) → `evaluation-api POST /v1/survey/eligibility/claim` (lógica:
quarentena Redis + ledger PG + amostragem + lock). Auditada (AuditPolicy → McpInterceptor).

### 15.2 Runner YAML (espinha genérica — o bloco do instrumento é gerado do `survey_definition`)

```yaml
# skill_survey_runner_v1.yaml — DECIDE-NO-SKILL: outcome → elegibilidade → instrumento → registro.
# Plataforma despacha SEMPRE; este skill decide se pesquisa. Substitui o skill_nps_v1.
# Pré-req ContextStore (bridge, pré-hook): session.contact_outcome, session.close_origin,
#   session.customer_participant_id, session.surveyed_segment_id, session.surveyed_agent_key,
#   session.contact_identifier (null = anônimo), session.survey_id.
id: skill_survey_runner_v1
name: "Survey Runner v1 — pesquisa de satisfação genérica"
version: "1.0"
classification: { type: orchestrator, domain: quality }
entry: gate_outcome
steps:

  # 1) Gate de ciclo fechado — CONVENÇÃO do runner (customizável), não da plataforma.
  - id: gate_outcome
    type: choice
    conditions:
      - { field: "@ctx.session.close_origin",   operator: eq, value: "customer_disconnect", next: encerrar }
      - { field: "@ctx.session.contact_outcome", operator: eq, value: "resolved",            next: check_eligibility }
    default: encerrar          # escalated/abandoned/suspended → não pesquisa

  # 2) Gate de quarentena/amostragem — lógica na evaluation-api, exposta via MCP.
  - id: check_eligibility
    type: invoke
    tool: survey_eligibility_check
    input:
      tenant_id:         "$.tenant_id"
      customer_key:      "@ctx.session.contact_identifier"
      survey_type:       "csat"               # gerado do survey_definition.type
      origin_session_id: "$.session_id"
      claim:             true
    output_as: eligibility
    on_success: gate_eligibility
    on_failure: encerrar                       # degrada seguro
  - id: gate_eligibility
    type: choice
    conditions:
      - { field: "$.pipeline_state.eligibility.eligible", operator: eq, value: true, next: agradecer }
    default: encerrar                          # quarentena/amostra → encerra silenciosamente

  # 3) INSTRUMENTO — bloco GERADO do survey_definition (form-builder). Ex.: CSAT 1–5.
  #    visibility isola o cliente (agente não vê a nota).
  - id: agradecer
    type: notify
    message: "Obrigado pelo contato! Pode avaliar este atendimento?"
    visibility: ["@ctx.session.customer_participant_id"]
    on_success: pergunta_csat
    on_failure: encerrar
  - id: pergunta_csat
    type: menu
    interaction: button
    prompt: "Em uma escala de 1 a 5, quão satisfeito você ficou com este atendimento?"
    visibility: ["@ctx.session.customer_participant_id"]
    timeout_s: 60
    options: [ {id: "1", label: "1"}, {id: "2", label: "2"}, {id: "3", label: "3"}, {id: "4", label: "4"}, {id: "5", label: "5"} ]
    output_as: csat_resposta
    on_success: agradecer_final
    on_failure: encerrar
    on_timeout: encerrar
    on_disconnect: encerrar
  - id: agradecer_final
    type: notify
    message: "Agradecemos sua avaliação! ✅"
    visibility: ["@ctx.session.customer_participant_id"]
    on_success: escolher_grao
    on_failure: escolher_grao
  # (branching do §4.6: detrator → oferta de callback; promotor → link de review — choice opcional aqui)

  # 4) Grão pelo contexto (idêntico ao agente_nps_v1): segment se há surveyed_segment_id, senão session.
  - id: escolher_grao
    type: choice
    conditions:
      - { field: "@ctx.session.surveyed_segment_id", operator: exists, next: gravar_segmento }
    default: gravar_sessao
  - id: gravar_segmento
    type: invoke
    tool: survey_record
    input:
      tenant_id: "$.tenant_id"
      origin_session_id: "$.session_id"
      grain: "segment"
      segment_id: "@ctx.session.surveyed_segment_id"
      agent_key: "@ctx.session.surveyed_agent_key"
      signals: [ { metric: "csat", value: "$.pipeline_state.csat_resposta" } ]
    on_success: encerrar
    on_failure: encerrar
  - id: gravar_sessao
    type: invoke
    tool: survey_record
    input:
      tenant_id: "$.tenant_id"
      origin_session_id: "$.session_id"
      grain: "session"
      survey_session_id: "$.session_id"
      signals: [ { metric: "csat", value: "$.pipeline_state.csat_resposta" } ]
    on_success: encerrar
    on_failure: encerrar

  - id: encerrar
    type: complete
    outcome: resolved
```

> **Instrumento dinâmico × compilado**: a *espinha* (gate→elegibilidade→grão→record) é comum. O
> *bloco do instrumento* (prompt/escala/opções/branching) vem do `survey_definition`. Como `menu` é
> estático, há duas implementações: **(a) compile-to-skill** — o form-builder gera/deploya um skill por
> definição reusando o deploy lifecycle (draft/published + hot-reload), espinha idêntica; **(b)** evoluir
> `menu` para opções dinâmicas (de `pipeline_state`) + `survey_definition_get`. Ver o **ADR §16 (A) ×
> §17 (B, recomendada)** — B adota exatamente a opção (b).

---

## 16. Form-builder — compile-to-skill (aproximação A — alternativa)

> Ver **§17** para a aproximação **B (interpretador genérico, recomendada)**. Esta §16 detalha **A**:
> gerar um skill por formulário, sem mudança de engine. O mapeamento bloco→render (§16.3) e o sink de
> verbatim (§16.5) são compartilhados pelas duas aproximações.

### 16.1 Princípio

`survey_definition` (dado, evaluation-api) é **compilada** num skill-flow **deployado** (agent-registry),
reusando o **deploy lifecycle** existente (draft/published + hot-reload). O formulário é **dado do
tenant**; o skill gerado é um **artefato compilado DB-owned**, provisionado pela **API oficial**
(`PUT`/`deploy`), **nunca** por YAML no repo. Respeita "provisioning only via official API" + "DB-owned"
+ "skills são código" (o código aqui é gerado, não escrito à mão).

### 16.2 Pipeline de compilação (no publish do formulário)

`SurveyCompiler` na **evaluation-api** (dona da definição):

1. **Valida** a definição — perguntas existem na biblioteca, escala coerente com o `type`, branching
   referencia perguntas válidas.
2. **Emite** o skill-flow (JSON) = **prólogo fixo** (`gate_outcome` → `check_eligibility` →
   `gate_eligibility` → `agradecer` com `intro_text`) + **corpo gerado** (1 render+capture por pergunta,
   na ordem, + `choice` de branching) + **epílogo fixo** (`escolher_grao` → `survey_record` com os
   signals de todas as perguntas → `complete`). O prólogo/epílogo são exatamente a espinha do §15.2.
3. **Deriva** o `skill_id` = `skill_survey_{survey_id}_v{n}` (regex `^skill_[a-z0-9_]+_v\d+$`).
4. **Dry-parse** no validador do skill-flow-engine (perfil **`agent`** — `menu`/`notify` permitidos;
   `suspend`/`collect` proibidos). Falha → **bloqueia o publish** (não deploya YAML inválido).
5. `PUT /v1/skills/:id` (agent-registry) → `deploy_status=draft`.
6. `POST /v1/skills/:id/deploy` → `published` → `registry.changed` (Kafka) → `_skill_flow_cache`
   invalida → **ativo sem restart** (hot-reload 3-elo).

### 16.3 Mapeamento bloco → step

| Pergunta (`survey_question.metric`) | Step gerado | `interaction` | Captura |
|---|---|---|---|
| `csat` (1–5) | `menu` | `button` | `output_as: q{n}` |
| `nps` (0–10) | `menu` | `list` (11 opções > 3) | `output_as: q{n}` |
| `ces` (1–7) | `menu` | `list` | `output_as: q{n}` |
| `pmf` (3 opções) | `menu` | `button` | `output_as: q{n}` |
| `fcr` (sim/não) | `menu` | `button` (2) | `output_as: q{n}` |
| `open_text` (motivo) | `menu` | `text` | `output_as: q{n}_text` → sink LGPD (§16.5) |

Branching (§4.6) → `choice` lendo `$.pipeline_state.q{n}`. A `interaction` é escolhida pela **contagem
de opções**; o **fallback por canal** (botão ≤3 no WhatsApp etc.) é resolvido **no Channel Gateway
adapter** — o compilador nunca emite render channel-specific (invariante).

### 16.4 Montagem dos signals

O epílogo agrega as perguntas **numéricas** num **único** `survey_record(signals=[{metric, value} por
pergunta])` — uma chamada, N métricas (o `survey_record` já suporta até 20). O `type` da definição
governa a normalização no consumer; cada pergunta carrega seu `metric`.

### 16.5 Verbatim (`open_text`) — sink operacional/LGPD

Texto aberto **não** é signal numérico → não vai ao `session_signal`. Vai ao **`survey_response.open_text`**
(PG operacional) + ContextStore (`session.survey.verbatim`, scope controlado) para o `agente_survey_analyst_v1`
(§10c). **Mecanismo** (refinamento de §7, registrar): o evento `session.signals` ganha `open_text?` +
`survey_id?`/`instance_id?` opcionais; um **consumer da evaluation-api materializa
`survey_response`/`survey_instance`** do **mesmo** tópico — mantendo `survey_record` como **caminho único
de escrita** (§11) — enquanto a analytics-api consome o mesmo tópico para o ClickHouse. Um write, dois
stores (PG operacional + ClickHouse analítico).

### 16.6 Binding hook → skill (sem proliferar pools)

O hook referencia **um pool `survey_ia` compartilhado** + `survey_id`; o bridge despacha esse pool
rodando `skill_survey_{survey_id}` por **resolução de skill no dispatch** — a mesma mecânica já usada
para agentes de fila ("o bridge resolve o flow direto pela skill"). Evita um pool por formulário; a
capacidade do pool governa a concorrência. Forward-compatível com `PoolSkillSlot` (pool↔skill N:N).

### 16.7 Versionamento e deploy seguro

Editar um formulário publicado → **nova versão** `_v{n+1}` → recompila → `PUT`(draft) → `deploy`. O
hook referencia o `survey_id` **lógico**; o registry serve a versão **published** (edições draft não
afetam produção). `handoff-status`/safe deploy aplicam; **rollback** = re-deploy da versão anterior.

### 16.8 Preview/teste (opcional)

Preview do formulário num webchat de teste (reusa o caminho `webchat-test.html` / sessão de teste)
**antes** do publish — valida render e branching sem tocar produção.

---

## 17. Editor de formulários — interpretador genérico (aproximação B, **recomendada**)

> **ADR**: §16 (compile-to-skill, A) e §17 (interpretador genérico, B) são as duas aproximações para
> "N formulários por tipo + deploy formal". **Recomendação: B** — alinha com o princípio do repo
> (skill = código/mecanismo; formulário = dado), deixa **um único** skill e dá deploy formal ao
> **formulário**. Custo: mudança de engine delimitada (§17.3) — **aprovada** (2026-06-23).

### 17.1 A proposta

Um único `skill_survey_runner_v1` (interpretador), fixo; a UI só produz o **form JSON**. O binding
("qual formulário") usa o **mecanismo de parâmetros de skill já existente** (validado no código):

- `Skill.interface_schema` (agent-registry) declara os parâmetros do skill — para o interpretador,
  `{ form_id }` (recomendado) ou `{ form_json }`.
- O **slot de deploy** `PoolSkillSlot.config_json` (a tela **Flow › Deploy › SKILL PARAMETERS**, slots
  **Next/Current/Previous**) carrega o valor. **Promover Next → Current** (snapshot imutável) é o **deploy
  formal** da troca de formulário; **rollback = Previous**; promote dispara `registry.changed` (hot-reload).

O interpretador lê `$.config.form_id` e resolve o JSON **publicado** via `survey_form_get` (MCP) — ou lê
`$.config.form_json` direto, no caminho inline (§17.6).

### 17.2 O que o engine suporta hoje (validado no código)

- `menu.options`/`menu.fields` são **estáticos** (schema = array tipado `{id,label}`; só o `prompt` é
  interpolado — `menu.ts` L40 × L64). → render dinâmico de pergunta **não** é possível sem mudança.
- **Ciclos são permitidos** (guarda anti-runaway: cada iteração exige input humano/externo — `engine.ts`
  L246-250), **mas** não há **contador/aritmética** nem **índice variável** (`jsonpath-plus` só indexa
  por literal). → loop sobre N perguntas arbitrárias exigiria mais que uma mudança.

### 17.3 A mudança de engine (aprovada — duas peças delimitadas)

1. **Expor o `config_json` do slot ao flow.** Hoje o `PoolSkillSlot.config_json` (preenchido do
   `interface_schema`) **não** chega ao runtime — o `evalContext` (`interpolate.ts` L262-276) tem
   `pipeline_state`/`session`/`session_id`/`tenant_id`/`customer_id`/`instance_id`/`segment_id`, mas **não**
   `config`. Injetar `config_json` no `sessionContext` → acessível como **`$.config.*`**. É por aqui que o
   `form_id` (parâmetro do slot) chega ao interpretador.
2. **`menu.options`/`menu.fields` dinâmicos.** Passam a aceitar **ref** resolvida de
   `pipeline_state`/ContextStore/`$.config` (o `menu.ts` já interpola o `prompt` — estende-se a
   options/fields; o schema `MenuStepSchema` vira união `array estático | ref`).

Com as duas, o interpretador lê `$.config.form_id`, carrega o JSON e renderiza. Ambas são pequenas e
locais (sem novo step, sem loop). Para o caso comum (1 pergunta / `interaction=form`), nada além disso.
**Detalhe file-level (arquivos, linhas, snippets) em §18.**

### 17.4 Estratégia de render (evita o loop)

- **1 pergunta** (CSAT/NPS — maioria transacional): 1 `menu` com `options` dinâmicas. Sem loop.
- **multi-pergunta, canal rico**: 1 `menu interaction=form` com `fields` dinâmicos (formulário inteiro
  num payload). Sem loop.
- **canal pobre (WhatsApp) multi-pergunta**: fallback no Channel Gateway adapter (sequencia o form) **ou**
  o **link web** (§9.2), cuja página já interpreta o JSON — zero engine.
- **loop sequencial sobre N perguntas arbitrárias em canal pobre**: só se exigido → precisa contador/
  iteração (mudança maior) → **DEFERIDO** (interaction=form + web cobrem o realista).

### 17.5 Fluxo do interpretador

`gate_outcome` → `check_eligibility` → `survey_form_get($.config.form_id)` (carrega o JSON publicado no
`pipeline_state`) → render (1 menu dinâmico **ou** `menu=form` com options/fields vindos do JSON) →
`survey_record(signals do JSON)` → verbatim (§16.5) → `complete`. **Espinha idêntica ao §15.2**; muda só
o render (dinâmico) + o `survey_form_get` (+ o `$.config.form_id`). O mapeamento bloco→render (§16.3) e o
sink de verbatim (§16.5) valem igual. No caminho inline, dispensa o `survey_form_get` (lê `$.config.form_json`).

### 17.6 Deploy formal do formulário (reusa o slot Next/Current) — id × json

O **slot** (`PoolSkillSlot`, tela Flow › Deploy) é o gate: `config_json` define o formulário; **Next →
promote → Current** (imutável) é a publicação; **Previous** é o rollback; promote dispara
`registry.changed` (hot-reload). Duas formas de carregar o formulário no parâmetro:

| | **`form_id` (recomendado)** | `form_json` inline |
|---|---|---|
| `config_json` carrega | `{ form_id }` (+ `form_version` opcional, pinada) | o JSON do formulário inteiro |
| fonte canônica do form | evaluation-api (`survey_definition`) — **única** | duplicada no snapshot do slot |
| resolução em runtime | `survey_form_get(form_id)` (MCP) → JSON publicado | lê `$.config.form_json` direto |
| tool nova | sim (`survey_form_get`) | **não** |
| analítica (§10b/§10c) lê o form de | evaluation-api (mesma fonte) | snapshot do slot (acoplado) |
| gate de deploy | promote do slot **e/ou** publish do form na evaluation-api | só o promote do slot |

**Recomendado: `form_id`.** O módulo já precisa do form **canônico na evaluation-api** (o navegador §10b e
o analista §10c leem/rotulam respostas pelo form); tendo essa fonte única, passar o id é estritamente mais
limpo que duplicar o JSON no slot. O `form_json` inline é o caminho **mínimo** (zero tool, o snapshot do
slot já é o "published"), bom para um v1 enxuto — ao custo de a analítica ler o form do snapshot do slot.

### 17.7 Comparação

| | **B — interpretador (§17, recomendada)** | A — compile-to-skill (§16, alternativa) |
|---|---|---|
| nº de skills | **1** | 1 por formulário (sprawl) |
| mudança de engine | 2 peças (`$.config` no flow + options/fields dinâmicos) | nenhuma |
| artefato versionado | form JSON (draft/published) | skill gerado (deploy lifecycle) |
| trocar formulário | publica versão do JSON | recompila + deploya skill |
| alinhamento "skill=código, form=dado" | **forte** | fraco (dado vira código) |
| risco | engine change pontual | sprawl de skills |

**Decisão: B** — mudança de engine **aprovada** (2026-06-23). Binding via `interface_schema` →
`PoolSkillSlot.config_json` (`form_id`, §17.6). A (§16) fica registrada como alternativa histórica.
Detalhe file-level da extensão em **§18**.

---

## 18. Extensão do engine — detalhe de implementação (file-level)

> Detalha as 2 peças do §17.3 nos arquivos reais. Núcleo do engine = **pequeno e local**; o grosso é
> *plumbing* (levar o `config_json` ao launch) e o *adapter* de `form` (fora do core do interpretador).

### 18.1 Peça 1 — `config_json` do slot no runtime do flow

**Estado atual (validado):** `engine.run({ sessionContext })` recebe o `sessionContext` do **launcher**
(`engine-runner.ts` L84-94 monta de `instance.pipeline_state.contact_context`). O `evalContext`
(`interpolate.ts` L262-276) expõe `session: ctx.sessionContext` (→ `$.session.*`) + builtins
(`session_id`/`tenant_id`/`segment_id`/…), **mas não `config`**. O `PoolSkillSlot.config_json` existe no
registry (já lido por `slotDeclared()` p/ capacidade) mas **não chega ao launch**.

- **(1a) Plumbing — o trabalho real.** O dispatcher (bridge p/ hooks `on_*`; worker p/ webhook) lê o
  `PoolSkillSlot.config_json` do skill deployado e o inclui no launch (metadata da instância / payload do
  dispatch sintético). O bridge já resolve o flow por `skill_id`; carregar o `config_json` do slot ao lado
  é adição pequena (a fonte já é acessível — `agent-registry` `skill-slots`).
- **(1b) Exposição no flow — trivial, duas opções:**
  - *Mínima (zero toque no interpolate):* o launcher mescla o `config_json` em `sessionContext.config` →
    resolve **hoje** como `$.session.config.form_id` (porque `session: ctx.sessionContext`).
  - *Limpa (`$.config.*`, recomendada):* adicionar `config: ctx.config` no `evalContext` (`interpolate.ts`
    ~L262) + threading `config` em `StepContext` (`executor.ts` L36-51) → params de `run()`/`_execute`
    (`engine.ts` L342/L417/L423) → launcher. ~5-6 linhas; namespace próprio, não colide com `session.*`.

### 18.2 Peça 2 — `menu.options`/`menu.fields` dinâmicos

- **Schema** (`MenuStepSchema`, `skill.ts` L351-418): `options`/`fields` viram **união** `array | ref`:

  ```ts
  const OptionsArray = z.array(z.object({ id: z.string(), label: z.string() }))
  options: z.union([OptionsArray, z.string()]).optional()   // string = "$.pipeline_state.survey_form.fields"
  // idem fields (union do array de fields | string ref)
  ```

- **Runtime** (`menu.ts` L62-68): antes de montar o payload, resolver se for ref — **reusar
  `resolveInputValue`** (já resolve refs e arrays/objetos aninhados p/ inputs de `invoke`, `interpolate.ts`
  L120-148):

  ```ts
  const resolvedOptions = typeof step.options === "string"
    ? await resolveInputValue(step.options, ctx, ctx.contextStore)
    : (step.options ?? [])
  const resolvedFields  = typeof step.fields === "string"
    ? await resolveInputValue(step.fields, ctx, ctx.contextStore)
    : (step.fields ?? [])
  // menu: { interaction: step.interaction, options: resolvedOptions, fields: resolvedFields, ... }
  ```

  O valor resolvido (array de `{id,label}`/fields) entra no payload **idêntico** ao caminho estático.
  Validação opcional pós-resolução (garantir o shape). **Indexação**: `jsonpath-plus` resolve índice
  **literal** (`$.pipeline_state.survey_form.fields`) — suficiente; **sem índice variável, sem loop**.

### 18.3 Render recomendado — 1 `menu interaction=form`, apresentação no adapter

O interpretador emite **um** render step: `menu interaction=form` com `fields =
"$.pipeline_state.survey_form.fields"` (carregado pelo `survey_form_get`). O **Channel Gateway adapter**
(dono do rendering channel-specific — invariante) decide a apresentação: web-form, botões sequenciais
(WhatsApp), lista. 1 pergunta = 1 field; N perguntas = N fields no mesmo step. Sem `choice` de shape, sem
loop. **Pendência de adapter (não-engine):** fallback de `interaction=form` multi-field nos canais pobres
(sequenciar). O enum `form` já existe em `MenuStepSchema.interaction`.

### 18.4 `survey_form_get` (tool, não-engine)

`mcp-server-plughub` (thin) → `evaluation-api GET /v1/survey/forms/:id?status=published` → JSON do
formulário (fields normalizados p/ o menu + metadados de signals/branching). O runner: `invoke
survey_form_get` `output_as: survey_form` → menu lê `$.pipeline_state.survey_form.fields` → `survey_record`
monta os signals a partir de `$.pipeline_state.survey_form.*` + respostas.

### 18.5 Resumo do esforço

| Item | Onde | Tamanho |
|---|---|---|
| 1a — plumbing `config_json` no dispatch | bridge + worker (+ read do registry) | **médio** (o grosso) |
| 1b — `$.config` no flow | `interpolate.ts` + `executor.ts` + `engine.ts` + launcher | pequeno |
| 2 — menu dinâmico (schema + resolve) | `skill.ts` + `menu.ts` (reusa `resolveInputValue`) | pequeno |
| 3 — render via `interaction=form` | skill YAML + **fallback no adapter** | pequeno (engine) / médio (adapter) |
| 4 — `survey_form_get` | `mcp-server` + `evaluation-api` | pequeno |

Núcleo do engine (1b + 2) é pequeno e local. O esforço real é o *plumbing* (1a) e o *adapter* de `form`
(3) — ambos fora do core do interpretador, e o (3) reusa um modo de interação que já existe no schema.

---

## 19. Retorno ao cliente (outbound) + ação no Console

> Detalha o endereçamento **ativo** de uma resposta/verbatim (o thread do §10c). Reusa os primitivos
> existentes (`collect`/Arc 19, Channel Gateway outbound) e a **inbox pull já existente**
> (`PullInboxPanel`/`dispatch_mode`, confirmada no §19.4) — onde a "ação de outbound" mora.

### 19.1 O que é

Abrir um **novo contato outbound** ao cliente, no **canal escolhido**, a partir de uma resposta de survey
classificada (ex.: callback do gerente ao detrator de cobrança). **Não** está atrelado à sessão original
(que já fechou) — é um contato novo, religado por `origin_session_id`/`customer_key`.

### 19.2 Dois modos de disparo

- **Automático (rules-driven)** — o `agente_survey_analyst_v1` classifica → **Rules Engine** /
  `workflow_trigger` dispara o outbound para classes configuradas (ex.: detrator + cobrança → retenção).
  Sem humano. **MVP sem dependência de Console.**
- **Humano-gated (Console — preferido)** — o `suggested_action` **parqueia uma sessão** na fila pull do
  Console (a caixa de ações **já existente**); um operador faz o claim, revisa (verbatim + survey),
  **escolhe o canal** e o skill dispara o outbound.

Config por classe/pool: `auto | review` (crítico auto, demais review).

### 19.3 O primitivo de outbound (reusa o existente)

O contato ativo = um workflow (perfil workflow) com step **`collect`** (Arc 4/19): cria **sessão-filho de
contato** no canal negociado por capabilities (Arc 16), envia via **Channel Gateway outbound**
(email/SMS/WhatsApp template; **voz = discador, dependência futura**), suspende até resposta. Religação por
`origin_session_id`/`customer_key` (o `survey_instance` carrega ambos). Quando o cliente engaja, vira
**sessão normal** — roteada pelo **Routing Engine** (nunca bypass) e aparecendo no Console ativo
(atribuível ao operador que reivindicou, ou a um pool).

### 19.4 A caixa de ações JÁ EXISTE — reusar a inbox pull (confirmado no código)

O Console (agent-assist) **já exibe a inbox automaticamente** quando o agente está logado num pool
`dispatch_mode: pull`: `AgentAssistPage.tsx` (L146-151) calcula `pullPoolIds = activePools ∩
dispatch_mode==="pull"` e renderiza o **`PullInboxPanel`** (que some sozinho — `return null` — sem pool
pull). A inbox lista **sessões claimáveis** parqueadas na fila (`GET /api/work_queue/list`, ZSET
`poolQueue` no Redis); o claim (`POST /api/work_queue/claim` → **Routing Engine**, árbitro único:
`ZREM`/lease/routed) **anexa o contato** como atendimento normal. Há também a tool MCP **`work_queue`**
(mesma `lib/work-queue.ts`) para clientes IA. `dispatch_mode` é enum `push|pull` (`schemas/agent-registry`).

**Consequência de design (corrige a v1 deste §19)**: a inbox claima **sessões** parqueadas num pool pull —
**não** um `work_item` genérico. Logo, o retorno de survey **não é entidade nova**: é uma **sessão de
contato outbound-intent parqueada num pool pull** (ex.: `retorno_survey_humano`). O blob `queueContact`
(`summary`/`channel`/`title`) carrega a classificação ("Detrator · cobrança · retornar"). Ela aparece
sozinha na inbox; o claim a anexa; o **skill da sessão** faz o `collect` outbound pós-claim. **Zero mudança
na inbox** — reuso integral do modelo de sessão + dispatch pull.

### 19.5 Fluxo (reuso integral, sem UI nova) — **claim ≠ collect**

1. **Analista enfileira** — o `agente_survey_analyst_v1`, na classe que pede retorno, **parqueia uma sessão
   outbound-intent** no pool `retorno_survey_humano` (via a tool MCP `work_queue` ou um `workflow_trigger`
   que roteia ao pool pull). `summary` = classificação + cliente. Semeia o ContextStore da sessão com
   `origin_session_id` + `customer_key` + resultado do survey (verbatim/notas).
2. **Inbox auto-exibe** — operadores logados no pool veem o item (sem UI nova). Clicar = preview; **Pull** =
   claim.
3. **Claim anexa + briefing (sem cliente)** — o Routing Engine aloca a sessão; ela **abre só com o agente**,
   cliente **ainda ausente**. Um hook **`on_human_start`** dispara um agente de **briefing/copilot** que
   monta o contexto do caso (transcrição da origem via `origin_session_id` + verbatim classificado +
   histórico do cliente por `customer_key`) e o exibe (tabs **Contexto/Histórico** do Agent Assist +
   `session.copilot.*`). O agente **lê e se prepara**.
4. **Agente coordena o collect** — o claim **NÃO** força o dial. O skill apresenta ao agente um menu
   **`agents_only`** ("Iniciar retorno" + canal sugerido/preferido); quando o agente dispara, o skill roda
   `collect`/dial → o cliente entra → conversa normal. O agente controla **timing e canal**.
5. **Wrap-up** — encerra como contato normal (outcome/wrap-up). Religação por `origin_session_id`/`customer_key`.

**Briefing — duas camadas combináveis** (sua pergunta): **(a)** reusar ContextStore + histórico (semear a
sessão com os ponteiros de origem; as tabs Contexto/Histórico já renderizam) — o mínimo, quase de graça;
**(b)** hook `on_human_start` → copilot que **agrega e resume** o histórico na tela — o briefing rico.
Recomendo **(a) sempre + (b) opcional por pool**. `on_human_start` é o hook certo: dispara quando o humano
**inicia o segmento** (no claim), **antes** do cliente entrar. O histórico do cliente (lista + drill +
busca) é a **capacidade transversal do §20** — o briefing destaca o contato de origem.

A seleção de canal e o disparo do collect ficam no **menu `agents_only` pós-claim** — não na inbox (que
fica intacta). Lease/auto-release e anti-duplo-claim já são do dispatch pull.

> **Lifecycle a validar** (estende o ponto do §19.8): a sessão de retorno **começa só com o agente** (sem
> cliente); `on_human_start` e o ciclo de conferência precisam tolerar "humano inicia sem cliente presente",
> com o cliente entrando depois via `collect`. Mesmo ponto do "parquear sessão outbound-intent".

### 19.6 Seleção de canal

O agente escolhe o canal **no momento do disparo** (menu `agents_only`, §19.5 passo 4), pré-preenchido com:
o canal sugerido pela classificação + (futuro) **preferências do cliente** (cadastro dinâmico —
horários/canais preferidos). Validado por **capabilities** (Arc 16). Default: o canal de maior alcance
disponível para o `customer_key`. Voz outbound exige discador (dependência/futuro).

### 19.7 Invariantes

- Roteamento do contato outbound **sempre via Routing Engine**.
- Disparo via `workflow_trigger`/Rules Engine (**MCP**), nunca Redis/routing direto.
- Verbatim no item = **referência LGPD-controlada** (não copiado em massa).
- Tudo auditável (McpInterceptor).

### 19.8 O que é reuso × o que é novo

- **Reuso (já existe, confirmado)**: `dispatch_mode: pull` no pool; `PullInboxPanel` auto-exibido;
  `work_queue` (HTTP `/api/work_queue/*` + tool MCP); claim via Routing Engine; `collect` outbound; modelo
  de sessão.
- **Novo (pequeno)**: pool `retorno_survey_humano` (pull); o analista **parquear a sessão outbound-intent**;
  o **skill de retorno** (collect + canal) pós-claim; conteúdo do `summary`.
- **Ponto a validar**: parquear uma sessão **iniciada pelo sistema** (cliente ainda ausente) num pool pull
  → **investigado em §19.9 — confirmado viável** (faltam 2 ajustes pequenos).
- **MVP sem Console**: modo automático (rules → `workflow_trigger` → `collect`) segue valendo sem a inbox.
- **Voz outbound**: discador — dependência separada.

### 19.9 Investigação do parking (2026-06-23) — **confirmado viável, sem infra nova**

Auditado no `routing-engine`:

- **Parking é genérico** — `Router.route()` (router.py L100-125) parqueia qualquer sessão cujo
  `ConversationInboundEvent.pool_id` aponte a um pool `dispatch_mode=pull`: pula o `_allocate` e persiste
  via `InstanceRegistry.add_queued_contact()` (registry.py L868) = `ZADD {tenant}:pool_queue:{pool}` +
  `{tenant}:queue_contact:{session}` (JSON do evento = `contact_data`, TTL 4h). A inbox lê exatamente
  essas chaves.
- **Não exige cliente vivo** — o park só escreve no Redis + publica `queue.position_updated` (que a
  channel-gateway entregaria a um cliente conectado — no-op inócuo se não há). Nada no caminho exige
  conexão de cliente.
- **`channel` é obrigatório, mas `"webhook"` é valor válido** (models.py L47, Arc 19) → uma sessão
  **system-initiated** (outbound-intent) é representável: um `conversations.inbound` sintético com
  `channel="webhook"` + `pool_id`=pool pull + `summary` no `contact_data`.
- **O bridge já emite `conversations.inbound` sintético** (hooks de pool com `conference_id`; triggers
  webhook do Arc 19) → enfileirar o retorno é a **mesma mecânica provada**.

**Conclusão: viável reusando tudo.** Faltam **dois ajustes pequenos** (não estruturais):

1. **`summary` no enqueue** — a inbox exibe `contact_data.summary ?? title` (work-queue.ts L51); o inbound
   sintético do retorno precisa carregar a classificação ali (inbound comum pode não popular `summary`).
2. **Lifecycle agent-only** — garantir que a conferência **não** trate a sessão como `abandoned` por "sem
   cliente" antes do `collect` (o agente inicia sozinho; o cliente entra depois). O Arc 19 já roda
   skill/agente sem cliente conectado e o `collect` traz o alvo — consistente; validar o não-fechamento
   precoce no `on_human_start`/teardown.

---

## 20. Histórico de contatos do cliente — capacidade transversal

> **Não é específico de survey** — serve a **qualquer atendimento**. Surge aqui porque o briefing do retorno
> (§19.5) o consome. **Arco próprio (spec):** `docs/arcos/customer-contact-history.md`.

### 20.1 O que JÁ existe (validado no código)

- **Lista** — `GET /analytics/sessions/customer/{customer_id}` (`sessions.py`): `sessions WHERE customer_id
  = …` (a tabela ClickHouse **já tem `customer_id`** — é o `customer_key` do §7.3) → últimas N sessões
  fechadas (`opened_at`, `channel`, `duration_ms`, `outcome`, `close_reason`, `pool_id`). O hook
  `useCustomerHistory` + a **`HistoricoTab`** já renderizam no Agent Assist.
- **Transcrição por sessão** — `GET /analytics/transcript/sessions/{session_id}` (`transcript.py`,
  `_fetch_transcript`/`_fetch_messages` por janela de segmento) já existe (usada por replay/audit).

### 20.2 O que FALTA (vale fazer agora — transversal)

1. **Drill lista → transcrição** — a `HistoricoTab` hoje expande só para `session_id`. Ligar a linha ao
   endpoint de transcrição existente (abrir a conversa do contato anterior), com **mascaramento/ACL LGPD**
   (masked por padrão; unmask só por papel autorizado — coerente com o módulo Audit LGPD). Já resolve "ver o
   atendimento que levou à pesquisa" (= `origin_session_id`, presente na lista por `customer_id`).
2. **Busca no histórico do cliente** — **não existe**. Endpoint novo `GET
   /analytics/sessions/customer/{customer_id}/search?q=…&from&to&channel&outcome&pool` sobre as
   **transcrições persistidas** (Stream Persister → PostgreSQL `sessions_stream`, ou ClickHouse
   `session_timeline`) por `customer_id` + termo → sessões + **snippets**. Respeita masking. UI: caixa de
   busca + filtros na `HistoricoTab`.

### 20.3 Onde aparece

- **Qualquer atendimento**: a `HistoricoTab` (lista + drill + busca) já está no painel do Agent Assist —
  ganha o drill e a busca para **todos** os cenários.
- **Retorno de survey (§19)**: o briefing reusa a mesma tab e **destaca o contato de origem**
  (`origin_session_id`) + o resultado do survey no topo.

### 20.4 Identidade (liga ao §13 — cadastro futuro)

Hoje a chave é `sessions.customer_id` (resolvido na identificação do contato). A unificação **cross-canal**
(todos os handles do mesmo cliente → um `customer_id`) é o **cadastro dinâmico** (§13, outra sessão). Até lá,
o histórico é por `customer_id` resolvido; quando o cadastro entrar, lista e busca passam a abranger todos os
canais **sem mudança de contrato** (mesma coluna).

### 20.5 LGPD

Transcrição e busca expõem conteúdo de conversa → **acesso controlado** (masked por padrão; unmask só por
papel autorizado, com trilha de auditoria — coerente com o módulo Audit LGPD). A busca indexa o conteúdo
**masked** salvo no stream; o original só via resolução autorizada.
```
