# Arc — Métricas de Avaliação & Metodologia de Qualidade (Quantitativo + Qualitativo IA)

> Última atualização: 2026-06-20 · Estado: **design fechado, implementação pendente**
> Escopo: domínio canônico de métricas de sessão (`session_metric.*`), dimensões
> qualitativas específicas de avaliação de agentes **IA**, metodologia (LLM-as-judge) com
> referências de mercado, **amostragem de contatos (cota por agente + versão)**, módulo
> agnóstico/contatos externos, e roteiro de fiação.
> Complementa [`arc6-evaluation.md`](arc6-evaluation.md) (plataforma) e
> [`arc13-review-contestation.md`](arc13-review-contestation.md) (revisão/calibração).

Este documento consolida uma discussão de produto sobre **o que o avaliador mede** e **como
mede** — distinto de *como o resultado é revisado/contestado* (Arc 13). Cobre duas trilhas:

1. **Quantitativo** — fatos objetivos, determinísticos, sem LLM, extraídos da sessão. É
   agnóstico de tipo de agente (serve humano e IA).
2. **Qualitativo de IA** — dimensões de julgamento específicas de avaliar uma IA (não um
   humano), e a metodologia para que esse julgamento seja confiável.

---

## 0. Estado atual (achados de código, 2026-06-20)

Mapeamento do que existe na `eval-baseline` antes deste arco. Crítico para o roteiro: vários
componentes existem como **código órfão** (definido, não chamado).

| Componente | Arquivo | Estado |
|---|---|---|
| Modelo `auto_computed` (tipo, `computation_source`, thresholds, `comparison`) | `schemas/evaluation.ts` | ✅ existe |
| Coluna `evaluation.instances.session_metrics JSONB` + `set_instance_session_metrics` | `evaluation-api/db.py` | ✅ existe |
| Lado LLM do `auto_computed` (prompt manda **ignorar**, router exclui do output-schema, scoring trata como `score`) | `prompt_composer.py`, `router.py`, `scoring.py` | ✅ correto |
| `SessionMetricsExtractor.extract()`, `compute_auto_criterion_score()`, `fill_auto_computed_criteria()` | `session_metrics_extractor.py` | ⚠️ **órfão — nunca chamado** |
| Condicionamento de critério `applies_when` + `na_guidance` | `schemas`, `FormsPage.tsx`, `prompt_composer.py` | ✅ existe (resolvido pelo LLM) |
| `session_meta.channel` no contexto do avaliador | `session-replayer/replayer.py` | ✅ populado (default `webchat`) |
| Trace de tool-calls (`mcp.audit`) no contexto do avaliador | `session-replayer`, `evaluation_context_get` | ❌ **ausente** |
| `AuditRecord.input_snapshot`/`output_snapshot` | `schemas/audit.ts` | ✅ no schema, gated por `AuditPolicy.capture_input/output` (default false) |

**Consequência prática do órfão:** um critério `auto_computed` adicionado a um formulário hoje
(a) é pulado pelo LLM, (b) nunca é preenchido por ninguém, (c) fica sem resposta → o
`aggregate_scores` o **descarta e re-normaliza os pesos** entre os demais critérios. Ou seja,
é um **no-op que ainda distorce o peso** das perguntas qualitativas. Ligar o fio órfão é o
item 1 do roteiro.

---

# PARTE I — Domínio Quantitativo (`session_metric.*`)

## I.1 Princípios

- **Catálogo fechado.** Um conjunto finito e nomeado de fatos. Adicionar um fato novo é uma
  decisão de produto (entra no catálogo), não composição livre pelo operador. Evita virar um
  query-builder na tela.
- **Determinístico, sem LLM, sem custo.** Tudo computável de eventos já existentes.
- **Agnóstico de agente.** Os mesmos fatos valem para humano e IA; o que muda é o **peso** e
  **quais** importam em cada campanha.
- **Fato nomeado, tipado, com unidade explícita.** Namespace `session_metric.*` — o mesmo que
  os critérios `auto_computed` referenciam via `computation_source`. **O domínio é o catálogo
  fechado**: fechá-lo serve de uma só vez (a) a fonte dos critérios quantitativos do
  formulário, (b) KPIs de dashboard, (c) o "kit" que uma futura ferramenta consome.

## I.2 Decomposição genérica (catálogo + parâmetros)

Cada fato do catálogo é, internamente, uma composição de quatro peças. O **operador não
compõe** essas peças (catálogo fechado) — elas são o vocabulário com que *nós* definimos cada
fato e com que o interpretador o computa:

1. **Seletor** — quais eventos do stream interessam (`author_role`, `event_type`, `tool_name`,
   `step_id`, match de conteúdo, `visibility`).
2. **Medida** — o que medir em cada evento (timestamp, latência até o próximo, duração,
   contagem, tamanho em chars, campo numérico).
3. **Redutor** — como colapsar (`first`/`last`/`max`/`min`/`sum`/`avg`/`count`/`exists`).
4. **Pontuação** — comparar o número com threshold(s) → score 0..1. **Já existe e está
   correto** em `compute_auto_criterion_score()`.

> Nota: muitas perguntas paramétricas colapsam num fato de catálogo + threshold. "Teve resposta
> > X s?" = `max_response_time_s` com `comparison=lte`, `threshold=X`. Só "**quantas** passaram
> de X" exigiria um redutor `count-where` — que motiva guardar as **séries** (decisão B).

## I.3 Catálogo canônico

Fonte de cada fato indicada. Fatos marcados *(série)* guardam o array bruto além do agregado.

### Tempo e latência (universal)

| `session_metric.*` | Definição | Unidade | Fonte |
|---|---|---|---|
| `total_session_duration_s` | abertura → fechamento | s | stream `session_opened/closed` |
| `segment_duration_s` | duração do segmento avaliado | s | `analytics.segments` |
| `time_to_first_agent_message_s` | abertura → 1ª msg do agente (proxy de "tempo até saudação") | s | stream |
| `first_response_time_s` | 1ª msg do cliente → 1ª resposta do agente | s | stream |
| `agent_response_latencies_s` *(série)* | latência do agente após cada msg do cliente | s[] | stream pares cliente→agente |
| `avg_response_time_s` / `median_response_time_s` / `p90_response_time_s` / `max_response_time_s` | derivados da série | s | calculado |

> **Saudação = primeira mensagem do agente** (decisão de simplificação). Não detectamos a
> saudação semanticamente; `time_to_first_agent_message_s` é o proxy. Evolui depois se preciso
> (step nomeado `saudacao` no skill-flow seria a âncora determinística sem LLM).

### Silêncio e espera (universal; voice refina)

| `session_metric.*` | Definição | Unidade | Fonte |
|---|---|---|---|
| `customer_wait_time_s` | soma dos períodos em que **o cliente** aguardou resposta | s | stream |
| `max_customer_wait_s` | maior espera individual do cliente | s | stream |
| `inter_message_gaps_s` *(série)* | todos os gaps entre mensagens consecutivas | s[] | stream |
| `total_silence_s` | dead air agregado (qualquer lado) | s | stream |
| `max_silence_s` | maior gap | s | stream |

> **`customer_wait_time_s` ≠ `total_silence_s`** (decisão C): o primeiro é só o cliente
> esperando o agente (o que importa para qualidade); o segundo é dead air de qualquer lado
> (interessa a voice). Mantidos separados, nunca fundidos.

### Volume e composição (universal)

`total_messages`, `agent_messages`, `customer_messages`, `agent_message_pct`,
`customer_message_pct`, `turns_to_resolution`, `avg_agent_message_length`,
`avg_customer_message_length`, `max_consecutive_agent_messages` (monólogo). Fonte: stream
(`message_sent`, `visibility=all`, `author_role`).

### Ferramentas e coleta

`tool_calls_total`, `tool_calls_failed`, `tool_error_rate` (fonte: `mcp.audit`),
`required_fields_captured_pct` (ContextStore vs skill spec), `collect_retries` (steps
`menu`/`collect`).

### Fluxo / steps

`steps_completed`, `steps_retried`, `step_avg_duration_ms`, `step_max_duration_ms`. Fonte:
`pipeline_state` transitions.

### Resultado / escalada

`outcome`, `close_reason`, `escalated` (bool), `escalation_reason` (`handoff_reason`),
`resolved` (bool). Fonte: `agent_done`, sessão.

### Custo de inferência (fonte: `usage.events`, não o transcript)

`llm_calls_total`, `tokens_input_total`, `tokens_output_total`, `cost_estimate_usd`.

### Sentimento (fonte: `sentiment_timeline` persistido)

`sentiment_start`, `sentiment_end`, `sentiment_min`, `sentiment_avg`, `sentiment_delta`.

## I.4 Decisões fechadas (A–E)

| # | Decisão | Resolução |
|---|---|---|
| **A** | Escopo: contato × segmento avaliado | **Ambos.** Computa a maioria dos fatos nos dois escopos (modelo de 3 camadas; gap G1 — AHT inflado por wrap-up); o avaliador consome o do **segmento** por padrão. |
| **B** | Guardar séries além de agregados? | **Sim.** `agent_response_latencies_s` e `inter_message_gaps_s` guardadas brutas — habilitam perguntas paramétricas futuras (`count > X`) **sem recompute**. Custo: JSONB um pouco maior. |
| **C** | Definição de "silêncio" | **Dois fatos separados**: `customer_wait_time_s` (cliente esperando) × `total_silence_s` (dead air). Nunca fundidos. |
| **D** | Dado ausente / não-aplicável = `na` × `0` | **`na`** (sai da conta, re-normaliza peso) quando não-aplicável/sem dado — não é culpa do agente. Condicionável por canal (ver I.5). Evolui a cobertura conforme casos aparecem. |
| **E** | Quando computar | **Lazy, no ingest da avaliação.** Computa só para o `% de contatos` amostrado para avaliação (a info só serve a esse fim). Bate com a intenção original do extractor (docstring: "Called from /v1/evaluation/ingest after create_result"). Dado sobrevive porque as fontes são duráveis (PG `stream_events`, `usage_events`, `agent_done`). Trade-off aceito: dashboard "de todas as sessões" não enxerga (não é requisito). |

## I.5 `auto_computed` no formulário + condicionamento por canal

`auto_computed` **faz parte do formulário** e entra na nota junto com as qualitativas — não é
KPI de dashboard separado. O operador monta um form misturando perguntas qualitativas (vão ao
LLM) e quantitativas (`auto_computed`, calculadas pelo sistema), e ambas contam no score
ponderado.

**Condicionamento por canal (decisão D — viabilidade).** O primitivo `applies_when` existe no
critério e `session_meta.channel` está disponível. Mas há duas formas no código:

- *Nível critério* (formulário): string livre, resolvida **pelo LLM** lendo o texto +
  `session_meta`. Funciona hoje para critério **qualitativo** (ex.: `applies_when: "canal de
  voz"`). Não-determinística.
- *Nível seção* (mcp-server, caminho legado): estrutura avaliada **deterministicamente** por
  `sectionApplies()`.

Para `auto_computed`, o `fill_auto_computed_criteria` **ignora `applies_when`** hoje. Como nesse
caminho não há LLM, condicionar por canal exige um **gate determinístico** no fill (canal ∈
permitidos?), no molde do `sectionApplies()` — condição **estruturada**, não texto livre. É o
item 2 do roteiro.

**Gap conhecido (backfill):** o T17-backfill registra que `channel` não vem no segmento
persistido → regras por canal não se aplicam no backfill. Condicionamento por canal funciona no
caminho **forward** (onde `session_meta` está presente); backfill por canal fica pendente de
origem do dado.

---

# PARTE II — Dimensões Qualitativas de Avaliação de IA

## II.1 Por que avaliar IA ≠ avaliar humano

O domínio quantitativo é agnóstico de agente. O **qualitativo** não: o avaliador genérico de
hoje (lê transcript, pontua critérios do form) não tem nada específico de "isto é uma IA". A
diferença de foco:

- **Humano** — empatia, julgamento, soft skills; erros são episódicos ("teve um dia ruim").
- **IA** — alucinação, uso de ferramenta, aderência a política, e **saber abster-se/escalar**
  sob incerteza; erros são **sistemáticos** (derivam do modelo/knowledge base, consistentes por
  versão).

Hoje PlugHub oferece, de fato, instrumentação **quantitativa** para IA; o lado qualitativo é
genérico. Esta parte define o que adicionar.

## II.2 Catálogo de dimensões qualitativas de IA × insumo PlugHub

| Dimensão | O que avalia | Insumo PlugHub |
|---|---|---|
| **Faithfulness / groundedness** (anti-alucinação) | Afirmou só o que a KB/ferramentas suportam? | Parcial — `mcp-server-knowledge` (RAG) dá ground truth de KB; falta o trace de ferramenta |
| **Tool correctness / trajectory** | Ferramenta certa, argumentos certos, ordem, recuperou de falha? | `mcp.audit` (tool, allowed, duration, injection) — **forte**, mas não chega ao avaliador (ver II.4) |
| **Policy / instruction adherence** | Seguiu o skill-flow / política de negócio? | `pipeline_state` transitions = trajetória esperada vs real |
| **Abstenção / escalada apropriada** | Escalou quando devia? Não inventou sob incerteza? | `agent_done.outcome`/`handoff_reason` |
| **Plan quality / coerência multi-turno** | Manteve contexto, não se contradisse? | transcript (já disponível) |
| **Safety / guardrails** | Vazou PII? Resistiu a injeção/jailbreak? | Parcial — masking + `injection_guard` (flag `injection_detected`) |

## II.3 Dois tiers de habilitação

- **Tier 1 — avaliável só do transcript** (coerência, tom, instruction-following visível no
  texto). O avaliador atual **já consegue**; falta só o operador pôr o critério no formulário.
  Sem lacuna de engenharia.
- **Tier 2 — exige evidência de execução** (trace de `mcp.audit`, snippets de KB como
  referência, trajetória esperada do skill-flow). **Lacuna real** — o avaliador não vê o que
  aconteceu por baixo do texto.

## II.4 Enabler do Tier 2 — o trace de ferramenta (3 camadas)

1. **Lacuna de fiação (principal).** O `ReplayContext` é montado só do stream canônico +
   `session_meta` + sentiment + participants + form + knowledge + calibration. O session-replayer
   **não** lê `mcp.audit`, e `evaluation_context_get` também não. Como tool-calls não vão ao
   stream (invariante), o avaliador vê o *texto* do agente mas não **o que ele fez**. Tier-2 é
   inavaliável hoje por ausência de dado no contexto.
2. **O dado existe e é durável, noutro lugar.** `mcp.audit` → ClickHouse `mcp_audit_log`
   (Audit-LGPD Fase 1), exposto via analytics-api `GET /mcp-calls`. Habilitar = buscar o trace
   da sessão/segmento para dentro do contexto como campo novo (`tool_trace[]`), como já se faz
   com `knowledge_snippets`.
3. **Profundidade depende da `AuditPolicy` por tool:**

   | Dimensão | Campo necessário | Disponibilidade |
   |---|---|---|
   | **Tool correctness** | `tool_name`, `allowed`, `duration_ms`, `injection_detected` — **sempre gravados** | pronta ao fiar |
   | **Argument correctness** | `input_snapshot` — só com `capture_input=true` (default false) | condicional |
   | **Faithfulness vs saída de ferramenta** | `output_snapshot` — só com `capture_output=true` (default false) | condicional + tensão LGPD (guardar retorno potencialmente PII) |

**Dois tipos de faithfulness** (distinção importante):
- *vs knowledge base* (ex.: citou a política certa) — **parcialmente habilitado** (avaliador já
  recebe top-5 `knowledge_snippets`).
- *vs saída de ferramenta* (ex.: "seu saldo é X" — X foi o que o CRM retornou?) — precisa de
  `output_snapshot`.

**Núcleo recomendado de baixo custo:** **tool correctness + policy adherence + faithfulness-vs-KB**
— habilitável fiando o `tool_trace` e comparando trajetória `pipeline_state` × skill-flow.
Argument correctness e faithfulness-vs-ferramenta custam mais (ligar `capture_input/output` por
tool + LGPD).

## II.5 Captura de tool I/O para faithfulness (R7) — masking + LGPD

**Achado (assimetria + vazamento latente).** No `McpInterceptor`: o `input_snapshot` redige os
campos vindos via `@masked` (`_sanitizeSnapshotForAudit` → `[MASKED]` + `masked_input_fields`); o
`output_snapshot` é `capture_output ? result : undefined` — **resultado cru, sem máscara**. Logo,
ligar `capture_output` numa tool que retorna PII grava **PII bruto no `mcp_audit_log`** (ClickHouse).
Isso é vazamento **independente de avaliação** → o R7 tem um componente de **hardening de segurança**,
não só feature.

**Tensão.** Faithfulness-vs-ferramenta precisa do **valor cru** que a tool retornou para verificar
("agente disse saldo R$500" vs retorno real); mascarar o output **destrói** a verificação. Mesmo
problema de `content` × `original_content`.

**Decisões (fechadas):**
- **(a) Modelo masked+original** para output snapshots (espelho de mensagem).
- **(b) Baseline = aplicar masking ao `output_snapshot`** (simétrico ao input) + `masked_categories`
  — **fecha o vazamento** e habilita tool/argument correctness + **faithfulness sobre fatos não-PII**.
  `capture_output` segue **opt-in por tool** (default off), dirigido por `data_categories`, com
  `retention_days` curto para tools que tocam PII.
- **(c) Faithfulness sobre VALOR PII = tier estendido (deferido):** reter o **original** só p/ papéis
  autorizados (vault), com retenção limitada + `requires_consent`.
- **(d) Captura opt-in por tool + retenção limitada.**
- **Exposição ao avaliador (IA) = campo mínimo transiente.** O critério **declara o campo do output**
  que verifica (ex.: `output.saldo`); o sistema faz **reveal escopado só daquele campo, just-in-time,
  auditado**; o resultado guarda só o **veredito + evidência mascarada**, nunca o valor cru → **PII
  jamais aterrissa no store de avaliação** (mais forte que o modelo de mensagem).

**Externo:** N/A — contato externo não tem tool trace (tier-2 indisponível, IV.5).

---

# PARTE III — Metodologia (LLM-as-judge) & Referências de Mercado

## III.1 Consenso de mercado (2025–2026)

- **Combinar determinístico + rubrica.** Unit tests para correção objetiva (o domínio
  quantitativo) + rubrica LLM para qualidade aberta. É exatamente o modelo `auto_computed` +
  critérios qualitativos.
- **Rubrica confiável = explícita, com critérios separados e calibrada.** Controles de viés a
  embutir: verbosity, self-enhancement, surface-fluency, authority/emotional. → orientação
  direta para o `prompt_composer` / rubrica-template (T8).
- **Juiz off-the-shelf não transfere de domínio sem calibração.** Divergência **> 20–25%** vs
  spot-check humano é gatilho para recalibrar a rubrica.

## III.2 Alinhamento com PlugHub (Arc 13)

O risco que o Arc 13 já nomeia ("o avaliador pode ser tão falível quanto o avaliado se
compartilham modelo/KB") **é** o *self-enhancement bias* da literatura. O loop de calibração do
Arc 13 (curadoria amostral → `CalibrationNote` → RAG) é precisamente o mecanismo recomendado.
**Falta**: fechar a **métrica de divergência (>20–25%)** como gatilho explícito de recalibração.

## III.3 Referências

- **τ-bench / τ²-bench / τ³-bench (Sierra)** — benchmark de tool-agent-user para atendimento
  (retail/airline/telecom); foco em **policy adherence**; expansões τ-knowledge (docs internos)
  e τ-voice. `https://github.com/sierra-research/tau2-bench`,
  `https://sierra.ai/blog/bench-advancing-agent-benchmarking-to-knowledge-and-voice`
- **DeepEval** — 6 métricas agênticas: task completion, argument correctness, tool correctness,
  step efficiency, plan adherence, plan quality. `https://deepeval.com/docs/metrics-introduction`,
  `https://deepeval.com/blog/llm-as-a-judge`
- **RAGAS** — faithfulness, context precision/recall (faithfulness-vs-KB).
  `https://atlan.com/know/llm-evaluation-frameworks-compared/`
- **Rubric-based evals & vieses** — A. Masood (2026):
  `https://medium.com/@adnanmasood/rubric-based-evals-llm-as-a-judge-methodologies-and-empirical-validation-in-domain-context-71936b989e80`;
  Scoring bias (arXiv 2506.22316): `https://arxiv.org/abs/2506.22316`
- **Panorama 2026** — Future AGI: `https://futureagi.com/blog/llm-evaluation-frameworks-metrics-best-practices/`

## III.4 Detecção de divergência & redução de viés (R8)

Duas alavancas complementares — **reduzir** o viés (upstream) e **medir/detectar** (downstream) —
mais a simetria humano×IA.

**Redução — revisor heterogêneo (recomendado, configurável).** Fixar o modelo do revisor numa
**família diferente** do avaliador descorrelaciona vieses de *modelo* (verbosity, position,
self-enhancement). Barato (o AI Gateway já tem perfis + cross-provider). Default forte, **não
obrigatório**. **Teto:** descorrelaciona viés de *modelo*, não de *dado* — mesma KB errada → ambos
concordam no erro. Não substitui o check humano cego.

**Redução — controles de viés na rubrica.** No `prompt_composer`/rubrica-template: verbosity,
self-enhancement, surface-fluency, authority/emotional (literatura). Texto, por critério.

**Detecção — Estágio 1 (gatilho barato).** Divergência = `1 − calibration_score` por `skill_version`
(já computado em `query_evaluator_calibration`). `> limiar` (default 25%) ∧ `N ≥ mínimo` (default 30)
→ sinaliza "recalibração recomendada" no Calibration Dashboard + roteia p/ fila. **Sinal, não
auto-mutação** (humano recalibra). Caveat: a fonte (curadoria) é *ancorada* → subdetecta viés
compartilhado.

**Detecção — Estágio 2 (curadoria cega-primeiro, `%`-gated, SLA).** Upgrade da curadoria: o humano
re-pontua o **mesmo form sem ver a nota da IA**; só depois o sistema revela + mostra o diff. Reusa a
infra de curadoria/contestação (fila, SLA, `review_workflow_skill_id`/`ContestationPolicy`,
`CuradoriaPage`). Entrega **divergência por dimensão** (dois `EvaluationResult` sobre o mesmo form),
gera `CalibrationNote` no desacordo (RAG), e **pega o viés de KB** — porque o humano pontua **contra
a realidade, não contra a KB** (diversidade de modelo não pega isso). `%`=0 desliga; `%`>0 liga.
**A nota humana é autoritativa no desacordo** (como contestação `revised`): corrige o registro +
alimenta a divergência por `skill_version` + o gatilho de recalibração; timeout de SLA → vale a nota
da IA, sinalizada. Eixo de amostragem **distinto** do primário (ADR), chaveado por `skill_version`.

**Simetria dos fluxos.** Humano avaliado: recurso = **contestação** (o avaliado dispara, SLA). IA
avaliada: o agente **não** contesta (invariante Arc 13) → controle **proativo** = Estágio 2
(`%`-amostral, SLA). Mesma máquina, gatilho diferente — não fere "IA nunca contesta" (não é o
avaliado contestando, é QA humano re-pontuando cego).

**Config (UI em Configurations):** limiar de divergência (25%), `N` mínimo (30), `%` do Estágio 2,
e o modelo do revisor (≠ avaliador, recomendado).

---

# PARTE IV — Amostragem de Contatos (cota por agente)

Define **quais contatos viram avaliação**. Vale para humano **e** IA — a mesma porta de
entrada dos dois fluxos. (Distinta da `CurationSamplingRule` do Arc 13, que decide quais
avaliações de IA o humano audita — esta corre *depois*, sobre as avaliações já finalizadas.)

## IV.1 Estado atual (achado de código)

Amostragem **stateless e determinística**: `_sample_percentage(session_id, rate)` faz bucketing
por SHA-256 do `session_id` — re-rodar dá o mesmo resultado, sem estado, e o `%` é **por
campanha (global)**. Há filtros (`min_duration_s`, `agent_type_ids`, `pool_ids`, `channels`,
`outcome_filter`) + `priority`, e `evaluated_user_id` na instance/result. **Não há contador por
agente.** Limitação: não garante cobertura por agente — um agente de baixo volume pode tirar 0.

## IV.2 Modelo-alvo: cota por agente, cumulativa, por déficit

**Objetivo: cobertura justa** (todo agente auditado), não representatividade estatística da
população de contatos. Amostragem por hash global falha nisso; cota por agente resolve.

**Algoritmo (déficit cumulativo):** o primeiro contato elegível de cada agente é **sempre**
amostrado (piso); a cada contato recomputa-se `avaliados/total`; se `< x%`, amostra o
contato-gatilho. Converge para x% e garante o piso. Ex. x=10%: contato 1 selecionado (100%),
fica 1/N até 1/N<10% (contato 11) → seleciona → estabiliza em ~10%.

**Cumulativo (não diário)** — decisão fechada. Elimina o viés do "primeiro-do-dia": só o
primeiríssimo contato do agente é front-load (benigno); depois a seleção se espalha pelos
horários naturalmente. Trade-off aceito: **sem garantia de cobertura diária**, mas justiça no
longo prazo. Consequência de baixo volume: o piso (primeiro sempre) eleva a taxa efetiva acima
de x% — coerente com o objetivo de cobertura.

**Chave do contador:**

| Agente | Chave | Observação |
|---|---|---|
| Humano | `(campaign, user_id)` | sem versão, sem reset |
| IA | `(campaign, pool_id, skill_id, deploy_version)` | chavear por versão **é** a semântica de "reset no deploy" sem reset destrutivo: a versão nova cai num bucket novo → 1º contato amostrado (= `deploy_baseline` de graça) → converge dentro da versão. **Não** chavear por `agent_type` — eixo legado (entidade `AgentType` aposentada, Fase 3d). A identidade de versão é o `SkillDeployment` ativo para `(pool, skill)`; deploy é pool-centric, daí a chave alinhar com a âncora-pool da lente do Arc 6 Fase 2 |

**Implicações de estado** (é uma virada de paradigma vs. o hash determinístico):
- contador mutável → **`INCR` atômico por chave** no Redis (race-safe entre contatos paralelos
  do mesmo agente);
- a seleção é **dependente de ordem** → backfill deve processar por `closed_at` para
  reprodutibilidade (o hash determinístico dava isso de graça);
- **denominador conta só contatos elegíveis** (após `min_duration`/`outcome`/`channel`), senão
  um contato filtrado infla o `total` e atrasa a próxima seleção.

**Mudança de semântica do `%`:** passa de "x% de todos os contatos" para "**x% por agente**".
Com volumes desiguais os dois divergem — deixar explícito na config (humano e IA têm `%`
próprios; IA tipicamente menor por operar 24×7).

## IV.3 Pré-requisito: versão do agente gravada no contato

**Achado:** o `ContactSegment` grava `pool_id`, `agent_type_id`, `instance_id`,
`participant_id` — mas **não** `skill_id`/`deploy_version`. Sem isso, a chave de cota por versão
(IV.2) é impossível, e o Arc 6 Fase 2 infere a versão pela **timeline de deploys** (errado no
overlap de hot-deploy).

**Hot-reload:** `_skill_flow_cache` é chaveado por `skill_id` (estável — deploy **não** muda o
id; troca o flow body); a invalidação faz a próxima leitura pegar a versão nova. Logo,
atribuição por `start_time` do segmento seria **exata** só se a sessão em andamento segurasse a
versão até o fim; **aproximada** se re-lê o flow por step (overlap = fração mínima).

**Decisão (fechada):** **carimbar `skill_id` + `deploy_version` (resolvido do `SkillDeployment`
ativo para `(pool, skill)`) no `ContactSegment`, ancorado no início do segmento** (registra o que
de fato rodou — exato por construção, imune ao hot-reload). Propagar para `analytics.segments`,
para a evaluation instance e para `evaluation_finalized`. Resolve a amostragem por versão **e**
conserta a precisão do Arc 6 Fase 2 de uma só vez.

**Modelo de identidade de versão (racional do esquema de deploy).** O `skill_id` está hoje
sobrecarregado: a convenção `skill_{nome}_v{n}` põe versão no nome, mas o deploy trata `skill_id`
como **estável** e rastreia versão à parte (`skill_deployments`) — versão expressa em dois
lugares. Decisão: **manter `skill_id` como identidade do ARTEFATO (estável)**, versão = registro
de deploy, e o `_v{n}` no nome passa a ser **cosmético** (não pode ser fonte de versão). O
caminho alternativo (`skill_id` muda por deploy) elimina a regra de nome mas funde
artefato+versão, quebra referências (`PoolSkillSlot`, `mention_commands`) e **não** resolve "um
skill roda em vários pools" — ganho parcial, custo alto. Por isso `skill_id` estável + carimbar
`deploy_version`.

**Por que carimbar e não inferir:** a inferência por `start_time` × timeline só funciona porque
a *plataforma* tem o histórico de deploys. O **módulo agnóstico** (IV.5) é alimentado por
contatos **externos**, que não têm timeline de deploy para cruzar — a versão precisa vir
**dentro** do contato. Como o carimbo é necessário para o externo, fazê-lo também no nativo é um
mecanismo só, robusto para os dois e sem dependência de histórico de deploy retido.

**Binding skill↔pool (cleanup relacionado).** Hoje a associação aparece em dois lugares:
`PoolSkillSlot.current` (estado atual, caminho quente de alocação) e `SkillDeployment.pool_ids`
(histórico). Não é duplicação pura (estado × log), mas o `pool_ids` pode **divergir** do slot.
Alvo: `PoolSkillSlot` é o binding **autoritativo**; o histórico vira append-log das mudanças de
slot (o `SkillDeployment` deixa de precisar do próprio `pool_ids`). Uma relação, com histórico.

## IV.4 Humano × IA

Mesma mecânica de déficit cumulativo; diferenças: humano não trabalha 24×7 (cumulativo garante
x% no longo prazo de cada agente, independente dos dias logados); IA opera 24×7 → `%`
tipicamente menor e a chave inclui `skill_version`. A amostragem primária ciente de versão já
entrega `deploy_baseline`; a `CurationSamplingRule` (Arc 13) segue depois, ortogonal.

## IV.5 Módulo agnóstico + contatos externos

**Objetivo:** módulo de qualidade apartado do resto da plataforma, alimentado por um
**importador** que grava contatos de terceiros no formato esperado.

**Escopo honesto — externo = grau-transcript.** O `ReplayContext` e o domínio `session_metric.*`
puxam de fontes nativas (stream canônico, `session_meta`, segments, `mcp.audit`,
`pipeline_state`, `usage.events`, `sentiment_timeline`). Contato externo não tem isso → cobre
**tier-1 qualitativo** + os `session_metric.*` deriváveis **só do transcript** (timing,
composição, silêncio — *se* o importador trouxer timestamps + papéis por mensagem). **Tier-2 de
IA** (tool correctness, faithfulness-vs-ferramenta, policy-por-trajetória) e métricas de
step/custo ficam **indisponíveis** para externo.

**Requisitos do importador:**
- **Contrato de ingestão público e versionado** — stream canônico + `session_meta` + segments +
  métricas opcionais. Hoje os schemas são internos (`@plughub/schemas`) e há o invariante "stream
  só via `writeStreamEntry`"; o caminho externo precisa de um writer seguro e estabilidade de
  contrato.
- **Masking/LGPD** — externo entra mascarado ou o importador aplica masking (a revisão cega D3
  depende de conteúdo mascarado em `analytics.messages`).
- **Identidade + versão** — `agent_id` estável e, para IA, `skill_version` (senão a cota por
  versão e a correlação deploy/calibração quebram).
- **Gatilho batch** — sem `session_closed` ao vivo, a cota por déficit roda **na importação**;
  ordenar por `closed_at` para reprodutibilidade.
- **Segmento mínimo** — sintetizar 1 segmento (= contato inteiro) para o escopo-A funcionar.

## IV.6 Arquitetura de ingestão (decisões fechadas)

**Forma = A2 (document-ingest).** Um contrato único de alto nível (`QualityContact`,
`ingestion_contract_v1`) entra num serviço de ingestão — em vez de o importador falar o protocolo
de eventos cru ou escrever stores direto.

**Fan-out = emitir eventos canônicos (fechado).** O serviço emite os eventos
(`conversations.events` message_sent/closed, `conversations.participants`, lifecycle) e os
**consumers existentes** fazem as escritas. Por quê: acoplamento mínimo (depende só dos schemas
Zod de evento, não do layout do ClickHouse); reuso de dedup (ReplacingMergeTree → re-import não
duplica), SegmentEnricher, DLQ/retry; zero drift com o nativo; e **o gatilho de amostragem vem de
graça** (`session_closed` dispara o consumer de sampling). Preterido o acesso direto ao ClickHouse
(acoplaria ao schema de storage).

**Stream durável = opção Y (produtor puro — isola o ambiente interno).** O importador **não toca
store nenhum** (Redis/PG/ClickHouse): é só produtor de eventos. Um **consumer interno** reconstrói
`session_stream_events` (PG) **a partir dos eventos** (o Persister vivo lê do Redis; este lê de
eventos). Assim as garantias do `writeStreamEntry` ficam 100% **dentro** da plataforma e o
invariante nunca é dobrado por componente externo. Cuidados de implementação: **compartilhar a
construção de linha** com o Persister vivo (sem drift de shape); `delta_ms` recalculado dos `ts`
dos eventos; **append incremental** por `message_sent` (idempotente por `event_id`), `session_closed`
finaliza; `original_content = null` (cego por construção, coerente com D3). *(Preterido: Z —
importador escreve o Redis transiente e o Persister snapshota; reusa mais, mas escreve Redis ao
vivo. Y isola melhor.)*

**Masking = pré-processador externo (primário) + net no ingest.** O PII deixa de existir **fora**
do PlugHub: o contrato exige conteúdo já mascarado + `masked_categories`. Como a responsabilidade
LGPD recai no **armazenamento**, o serviço ainda roda uma passada-rede com as `MaskingRule` do
PlugHub. `original_content = null`, sem tokens resolvíveis (sem vault/reveal) — revisão cega por
construção.

**Store-alvo do consumer Y:** `session_stream_events` (PG) — colunas `event_id`, `event_type`,
`author` (flat), `payload`/content (mascarado), `original_content` (null externo),
`masked_categories`, `delta_ms`, `segment_id`, `tenant_id`, `session_id`, `ts`.

## IV.7 Detalhamento — `QualityContact` + mapeamento para eventos

### Contrato (`ingestion_contract_v1`)

```
QualityContact
  contract_version: "ingestion_contract_v1"
  tenant_id
  external_id            # id na origem → deriva session_id determinístico (idempotência/re-import)
  source                 # proveniência, ex: "ccaas:genesys" → flag source=external_import
  channel, medium
  opened_at, closed_at   # ISO-8601
  outcome, close_reason
  customer_ref?          # pseudônimo do cliente (mascarado) → customer_id/contact_id

  segments: [ {          # 1..N (mínimo 1 = contato inteiro)
     segment_ref         # id local no contato → segment_id
     external_agent_id   # → mapeado p/ user_id (humano) / agent_id (IA) via mapa por source
     agent_kind: human | ai
     role: primary | specialist | ...   # default primary
     pool_id
     skill_id?, deploy_version?         # só IA — habilita cota por versão (ADR)
     started_at, ended_at
     outcome?, close_reason?
  } ]

  messages: [ {          # ordenadas por ts
     ts                  # ISO-8601
     author_role         # customer | agent | ...
     author_id?          # participant_id (opcional)
     segment_ref         # amarra a msg do agente ao segmento
     content             # JÁ mascarado
     content_type?       # default "text"
     visibility?         # default "all"
     masked: true        # obrigatório
     masked_categories?: [ ... ]
  } ]

  metrics?: { ... }      # session_metric.* pré-computado (opcional; senão derivamos do transcript)
  tool_trace?: [ ... ]   # opcional/raro → se presente, destrava tier-2
```

### Mapeamento → eventos canônicos

O serviço deriva `session_id` de `(tenant_id, external_id)` e emite em ordem de `ts`. Não escreve
store nenhum (produtor puro).

| Origem no contrato | Evento emitido | Campos-chave |
|---|---|---|
| abertura | `conversations.events` `contact_open` | session_id, channel, customer_id=`customer_ref`, opened_at, source |
| cada `segment` | `conversations.participants` `participant_joined` | segment_id=`segment_ref`, participant_id, pool_id, agent_type_id, role, agent_type, **skill_id/deploy_version** (R9), started_at |
| cada `message` | `conversations.events` `message_sent` | event_id (determinístico de `external_id`+índice), author_role, author{id,role}, content, content_type, visibility, masked_categories, timestamp, segment_id |
| fim de `segment` | `agent.lifecycle` `agent_done` + `participants` `participant_left` | outcome, handoff_reason, agent_type_id, pool_id, ended_at |
| fechamento | `conversations.events` `contact_closed` | outcome, close_reason, closed_at |

Consequências: os consumers existentes populam ClickHouse (`messages`/`segments`/`sessions`); o
`contact_closed` dispara a **amostragem** (ADR; emitir em ordem de `closed_at` no batch).

### Consumer Y (`source=external_import` → `session_stream_events`)

Único net-novo. Consome `conversations.events` **só de `source=external_import`** (gating — nativo
segue via Persister-do-Redis, sem dupla-persistência). Para `contact_open`/`message_sent`/
`contact_closed`: **append** de uma linha por evento (`event_id` único → `ON CONFLICT DO NOTHING`,
idempotente), com `author={id,role}`, `payload={content,content_type}`, `original_content=null`,
`masked_categories`. No `contact_closed`, **recalcula `delta_ms`** sobre as linhas ordenadas por
`ts` — mesma lógica do Persister vivo, **extraída para um helper compartilhado** (sem drift, sem
estado por sessão). Hydrator/Replayer leem transparente.

### Sub-decisões fechadas

1. **Mapa de identidade/versão por `source`** no Config API (`external_agent_id → user_id/agent_id`
   `+ skill_id/deploy_version`) — não se repete por contato. Sem versão → fallback do ADR
   `(campaign, pool, skill)`.
2. **Gating do Y por `source=external_import`** — confirmado.
3. **`delta_ms` recalculado na finalização** (`contact_closed`), helper compartilhado com o
   Persister — confirmado.

## IV.8 Núcleo epoch/versão (Arc 6 Fase 2) — destravado pelo R9

Pendência herdada: a lente `deploy` entregue (P3) é **diária + markers**; o núcleo §4.1/D4 (série
por **epoch/versão**, eixo X = versões, ponto = qualidade média da versão) ficou pendente porque a
atribuição de versão era **inferida pela timeline de deploys** (errada no overlap de hot-deploy). O
**R9** (carimbo `deploy_version` no segmento → `evaluation_finalized`) torna isso um **`GROUP BY
deploy_version` exato** — e mais simples que a timeline-bucket que o D4 imaginava.

Decisões (fechadas):
- **Âncora = `(pool, skill)`**, X = `deploy_version` (coerente com a âncora-pool entregue + a chave
  do R9).
- **`deploy_version` denormalizado no `evaluation_finalized`** (query sem join; o `segment_id` já
  está lá como alternativa de join).
- **Query** `lens=deploy&mode=epoch`: ponto = `avg(final_score)` por versão, `n` por versão,
  ordenado por `deployed_at`, `min_sample=30`, indicador de **"N pendentes de fechamento"** (lag de
  finalização — sobretudo no fluxo humano).
- **UI epoch:** foco single-`(pool,skill)`, **esconde a média dos agentes + multi-seleção**; o modo
  diário+markers (1º corte) permanece como visão alternativa.
- **Dependência dura: R9.** Substitui o plano antigo de tabela/consumer `analytics.deploy_events`
  (já preterido por D1/REST). Roadmap: R15a (query epoch) + R15b (UI epoch).

---

# PARTE V — Roteiro de Implementação

Ordenado por custo/benefício. Cada item é fiação concreta sobre código existente.

| # | Item | Onde | Decisão |
|---|---|---|---|
| **R1** | Fiar o extractor órfão: chamar `SessionMetricsExtractor.extract()` + `fill_auto_computed_criteria()` + `set_instance_session_metrics()` no `_ingest_core` (lazy) | evaluation-api `router.py` / `_ingest_core` | A, E |
| **R2** | Gate determinístico de canal no `auto_computed`: condição estruturada (molde `sectionApplies`) no `fill_auto_computed_criteria` (hoje ignora `applies_when`) | `session_metrics_extractor.py` | D |
| **R3** | Persistir as séries (`agent_response_latencies_s`, `inter_message_gaps_s`) no `session_metrics` JSONB | `session_metrics_extractor.py` | B |
| **R4** | Completar o catálogo I.3 no extractor (faltam derivados p90/median, `max_consecutive_agent_messages`, `step_*`, `required_fields_captured_pct`, sentimento) nos **dois escopos** (contato + segmento) | `session_metrics_extractor.py` | A |
| **R5** | **Tier-2 enabler:** buscar `tool_trace[]` do `mcp_audit_log` (via analytics-api `GET /mcp-calls`) para o `ReplayContext` / `evaluation_context_get` | session-replayer + mcp-server | II.4 |
| **R6** | Dimensões qualitativas de IA como critérios de 1ª classe: **tool correctness**, **policy adherence** (trajetória `pipeline_state` × skill-flow), **faithfulness-vs-KB** | form templates + skill `agente_avaliacao_v1` | II.2 |
| **R7a** | **Fix de segurança + baseline:** aplicar masking ao `output_snapshot` no `McpInterceptor` (hoje cru — vazamento) + `masked_categories`; habilita tool/argument correctness + faithfulness **não-PII**; `capture_output` opt-in por tool, `retention_days` curto p/ PII | sdk `mcp-interceptor.ts` + audit | II.5 |
| **R7b** | **Faithfulness-PII (tier estendido, deferido):** vault de `output_snapshot` original p/ papéis autorizados + `requires_consent` + retenção limitada | audit/Core + config | II.5 |
| **R7c** | **Reveal campo-mínimo transiente:** critério declara o campo verificável (`output.*`); reveal escopado just-in-time + auditado; resultado guarda só veredito + evidência mascarada (PII não entra no store de avaliação) | evaluation-api + skill avaliador | II.5 |
| **R8a** | Controles de viés na rubrica (verbosity, self-enhancement, surface-fluency, authority) no `prompt_composer`/rubrica-template | `prompt_composer.py` (T8) | III.4 |
| **R8b** | **Estágio 1** — gatilho de divergência sobre `calibration_score` (`1 − score` por `skill_version`; `>limiar` ∧ `N≥mín` → sinaliza no Calibration Dashboard + roteia); sinal, não auto-mutação | analytics-api + evaluation-api | III.4 |
| **R8c** | **Estágio 2** — curadoria **cega-primeiro** (`%`-gated, SLA): humano re-pontua o form sem ver a IA → reveal + diff por dimensão; nota humana autoritativa no desacordo; reusa workflow de revisão; alimenta divergência + `CalibrationNote` | evaluation-api + `CuradoriaPage` | III.4 |
| **R8d** | **Revisor heterogêneo** (recomendado): config de campanha modelo revisor ≠ avaliador (preferência outra família); caveat KB documentado | AI Gateway + config campanha | III.4 |
| **R8e** | **UI em Configurations** dos parâmetros: limiar de divergência (25%), `N` mínimo (30), `%` do Estágio 2, modelo do revisor | platform-ui | III.4 |
| **R9** | **Carimbar `skill_id` + `deploy_version` no `ContactSegment`** (resolvido do `SkillDeployment` ativo, ancorado no início do segmento); propagar a `analytics.segments` + evaluation instance + `evaluation_finalized` | orchestrator-bridge + schemas + analytics | IV.3 |
| **R10** | Cota por agente cumulativa (déficit) no sampling engine: contador Redis `INCR` atômico; chave humano `(campaign, user_id)` / IA `(campaign, pool_id, skill_id, deploy_version)`; denominador = elegíveis | evaluation-api sampling | IV.2 |
| **R11** | Config de campanha: `%` **por agente** (humano) + `%` por agente IA (menor); deixar explícito que o `%` é por-agente | evaluation-api + CampaignsPage | IV.2 |
| **R12** | Backfill ordenado por `closed_at` para reprodutibilidade do déficit | evaluation-api `backfill.py` | IV.2 |
| **R13a** | **Contrato `QualityContact` (`ingestion_contract_v1`)** + serviço de ingestão A2 (document → emite eventos canônicos; produtor puro, sem acesso a store); masking pré-processador + net no ingest; segmento mínimo; emitir em ordem de `closed_at` — escopo grau-transcript | novo pacote/contrato | IV.5/IV.6/IV.7 |
| **R13b** | **Consumer interno from-events** (opção Y, gated por `source=external_import`): reconstrói `session_stream_events` a partir dos eventos (append por `message_sent`, idempotente por `event_id`, `delta_ms` recalculado no `contact_closed`, `original_content=null`), **compartilhando a construção de linha** com o Persister vivo | session-replayer (ou novo consumer) | IV.6/IV.7 |
| **R13c** | **Mapa de identidade/versão por `source`** no Config API (`external_agent_id → user_id/agent_id` `+ skill_id/deploy_version`); fallback ADR `(campaign, pool, skill)` quando sem versão | config-api + serviço de ingestão | IV.7 |
| **R14** | **Affordances de criação + disciplina de versão no editor de skill** (a edição em si já existe). Achado: `/agent-flow/editor` (`SkillFlowsPage`) é editor YAML Monaco **read/write** — edita/cola, `PUT /v1/skills` (upsert), deleta, valida ao vivo; `/agent-flow/deploy` associa a pools (`POST /:id/deploy` → snapshot `SkillDeployment`). Gaps reais de UX/regra: (a) **não há botão "Novo skill"** — a criação só existe no estado inicial em branco (`skill_novo_v1`) ou após Delete; criar um `skill_id` é implícito (editar o campo `skill_id` + salvar), não-descobrível; (b) **Save é gated por `isModified`** (só habilita após editar) — parece "sempre desabilitado" ao apenas visualizar; (c) `deploy` deve atribuir **`deploy_version` automático** do histórico `SkillDeployment` (hoje `version` é texto livre, manual) e tratar o `version` do YAML como **rótulo**; (d) **reconciliar `409`/`_v2`** do `POST` com `skill_id` estável + `_v{n}` cosmético | platform-ui + agent-registry | IV.3 |
| **R15a** | **Núcleo epoch (query)**: `deploy_version` denormalizado no `evaluation_finalized` (via R9); `lens=deploy&mode=epoch` → série por `(pool, skill)` bucketizada por `deploy_version` (`avg(final_score)`, `n`/versão, ordem `deployed_at`, `min_sample=30`, "N pendentes") | analytics-api | IV.8 |
| **R15b** | **Núcleo epoch (UI)**: modo epoch single-`(pool,skill)`, eixo X = versões, esconde média + multi-seleção; diário+markers como modo alternativo | platform-ui `AgentsBenchPage`/`DeployChart` | IV.8 |

---

## Pendências / decisões em aberto

- **Origem de `channel` no backfill** — necessária para condicionamento por canal em sessões
  históricas (R2 só cobre forward).
- **Política LGPD de `output_snapshot`** — ✅ design fechado em §II.5 (fix do masking de output +
  masked+original + reveal campo-mínimo transiente); implementação em R7a–c.
- **Saudação por step nomeado** — se/quando instrumentar `saudacao` no skill-flow como âncora
  determinística (hoje proxy = primeira mensagem do agente).
- **Métrica de divergência avaliador×humano** — ✅ design fechado em §III.4 (Estágio 1 gatilho +
  Estágio 2 curadoria cega-primeiro + revisor heterogêneo); implementação em R8a–e.
- **Amostragem é virada para estado** — ✅ formalizado em
  [`docs/adr/adr-evaluation-sampling.md`](../adr/adr-evaluation-sampling.md) (contexto, decisão,
  trade-offs, alternativas). Implementação pendente (R9–R12).
- **Criação/versionamento de skill via UI** → **R14**. O editor YAML (`/agent-flow/editor`)
  **já é read/write** (edita/cola/salva via `PUT`, deleta, valida) e o deploy associa a pools —
  ao contrário do que se assumiu antes (não é read-only). Faltam, porém: **botão "Novo skill"**
  (criação hoje é implícita — editar `skill_id` + salvar; Save gated por `isModified`, parece
  travado ao só visualizar) e a **disciplina de versão** (`deploy_version` auto, `version` como
  rótulo, reconciliar `409`/`_v2`). Distinto do importador de **contatos** externos (IV.5).
- **Cleanup binding skill↔pool (2→1)** — tornar `PoolSkillSlot` autoritativo e o histórico um
  append-log das mudanças de slot (remover `SkillDeployment.pool_ids` redundante). Exige ler o
  modelo exato do agent-registry antes de cravar.
- **`_v{n}` cosmético** — se mantido `skill_id` estável, o sufixo de versão no nome deixa de ser
  fonte de versão; alinhar a convenção de nomenclatura para não induzir erro.
- **Fetch do flow (resolvido):** o flow é cacheado por `skill_id` estável no orchestrator-bridge
  (`get_skill_flow`) e invalidado no deploy → sessão que retoma após deploy pode pegar a versão
  nova; `start_time` é aproximado, e o carimbo no início do segmento (R9) é a convenção robusta.
- **Carga: piso de cobertura × teto de `%`** — em baixo volume o piso (1º contato sempre) eleva
  a taxa efetiva acima do `%` configurado; decisão consciente de produto.
- **Núcleo epoch/versão (Arc 6 Fase 2)** — ✅ design fechado em §IV.8 (destravado pelo R9: `GROUP BY
  deploy_version` exato; âncora `(pool,skill)`; UI epoch single-skill). Implementação em R15a/R15b,
  **dependente do R9**. Substitui o plano antigo de `analytics.deploy_events`.
