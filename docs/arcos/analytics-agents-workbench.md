# Analytics/Agents — Bancada de Comparação 360° (spec / ADR)

> Estado: **spec / ADR** (não implementado). Base da reformulação da aba Analytics/Agents.
> Reescreve a aba atual (daily-trend + tabela por aba Humano/IA) como uma **bancada de
> comparação** unificando quantitativo + qualitativo + voz do cliente + voz do agente.
> Relacionados: `docs/arcos/arc6-evaluation.md`, `docs/arcos/arc13-review-contestation.md`,
> `docs/arcos/arc12-agent-business-events.md`, `docs/arcos/arc8-agent-availability.md`,
> `docs/arcos/pools-infra-report.md` (Fase 2).

---

## 1. Visão

A pergunta que a tela responde deixa de ser "qual a tendência agregada" e passa a ser
**"onde este agente se posiciona em relação aos colegas — em quantidade E em qualidade"**.

Quatro camadas de informação, todas reduzidas à **mesma entidade** (`agent_key`) + pool, no
**mesmo eixo de tempo**, comparáveis na mesma bancada:

1. **Quantitativa** — sessions, AHT, resolution, escalation, ocupação/disponibilidade/pausa.
2. **Qualitativa sistêmica** — nota da avaliação de IA/QA (Arc 6).
3. **Voz do cliente** — NPS/CSAT/pesquisa (no ato ou diferida).
4. **Voz do agente** — wrap-up (disposição/motivo).

O valor de gestão está no **cruzamento** das camadas (onde elas discordam), não em cada uma isolada.

---

## 2. Estrutura da UI

Área de **comparação** no topo (gráfico) + **seletor de lente** ao lado + **lista de
seleção** (pools → agentes) abaixo, com filtros (período + pool).

- **Lista** (formato da aba AI Agents atual): pools expansíveis (chevron) → agentes. Cada linha
  tem **checkbox** (inclui no gráfico) e abre **detalhe** ao clicar no nome. Colunas: a métrica
  da lente em destaque + essenciais (sessions, AHT, nota); **bruto (quantidade/tempo) só no detalhe**.
- **Gráfico de comparação**: sobrepõe as entidades marcadas + a **média de referência** (linha
  grossa/tracejada). Default (nada marcado) = média do(s) pool(s) filtrado(s).
- **Seletor de lente** (botões, junto ao gráfico) — ver §4.
- **Detalhe** (pop-up ao clicar num item) — ver §9.

### Interação da lista (sem sobrecarregar o clique)
- **chevron** = expandir agentes do pool.
- **checkbox** = incluir no gráfico (checkbox do pool = média do pool; de agentes = aqueles indivíduos).
- **clique no nome** = abrir detalhe.

---

## 3. Modelo de entidade

A entidade comparável é o **`agent_key`** (já definido na C1b):

- **Humano** → `user_id` (display `user_login`).
- **IA** → `flow_id` (skill deployado).

Tudo (quanti, quali, sinais) é atribuído ao agente da sessão via o **segmento primário**:
`session_id → segmento primary → agent_key + pool_id`.

---

## 4. Lentes e regra de domínio de métrica

Cada lente é uma visão da mesma seleção. **A comparabilidade é propriedade da métrica**, aplicada
no seletor: numa lente de domínio humano, as linhas de IA ficam **desabilitadas** (não há alerta
pós-fato; a comparação inválida não é oferecível). Caption de 1 linha explica o domínio.

| Lente | Viz | Domínio |
|---|---|---|
| resolution / escalation | 2 curvas % no tempo | universal (humano + IA) |
| sessions / AHT | 2 mini-gráficos (eixos/unidades distintos) | universal |
| availability / busy / pause | 3 barras agrupadas | **humano** |
| pause-motivo | barra **empilhada** por entidade | **humano** |
| nota de qualidade (geral) | curva no tempo | universal |
| nota por critério/dimensão | barras por dimensão | universal |
| NPS / CSAT | curva no tempo | universal (cross-pool, normalizado) |
| wrap-up (disposição) | barras | **pool-scoped** (comparar dentro do pool, ou via campo de topo) |

Notas:
- **sessions + AHT não cabem no mesmo eixo** (contagem × tempo) → eixo duplo ou dois mini-gráficos.
- **Ocupação de IA ≠ ocupação humana**: humano = busy ÷ disponível (tempo); IA = concorrência ÷
  capacidade (Fase 2). **Não mapear IA na lente de ocupação humana** — é N/A ali.

---

## 5. Camada quantitativa

Reaproveita o que já existe (C1b-A/B, Arc 8, ocupação):

- sessions, AHT, resolution, escalation (de `segments`).
- logged, available, busy, paused, occupancy (de `agent_login_intervals` + `agent_pause_intervals`
  + `segments`).
- **Denominadores fixos** (para os % fecharem): `pause% = paused/logged`,
  `available% = (logged−paused)/logged` (pause% + available% = 100% do logado),
  `occupancy% = busy/(logged−paused)`.

**Dependência dura** *(premissa corrigida na recon 2026-06-07 — ver §13)*: o segmento humano
**já grava** `outcome`, mas é **placeholder** — a Console envia `resolved` (encerramento manual) /
`abandoned` (desconexão) hardcoded; nunca a disposição real. A disposição real do atendimento é
coletada pelo `agente_wrapup_v1` e fica presa em `session.wrapup.classificacao` (ContextStore).
O pré-requisito real não é "gravar o outcome do `agent_done`", e sim **propagar a disposição do
wrap-up para o segmento `primary` humano** via um `complete` dinâmico (decisão travada em §13).

---

## 6. Camada qualitativa (avaliação sistêmica — Arc 6)

A avaliação pontua uma **sessão**; rola para o agente via o segmento primário. Vira lente +
enriquece o detalhe.

- **Nota geral** (média das avaliações do agente no período).
- **Nota por dimensão/critério** do form — o ouro da curadoria (em que o agente é fraco).
- **Cobertura** (nº avaliações / % das sessões) — avaliação é **amostral**; N sempre visível.
- **% contestação / desfecho** (Arc 13), opcional.

Qualidade é **universal** (a IA também é avaliada — `agente_avaliacao_v1` + fluxo de IA do Arc 13),
então é uma das melhores comparações cross-type.

**Join central — respondido na recon 2026-06-07 (§13)**: `evaluation_results` **não** carrega
`user_id`/`flow_id`/`pool_id`/`agent_key` — só `evaluator_id` (quem avaliou), `session_id`,
`form_id`, `campaign_id`, `overall_score`. O relatório atual agrupa por `evaluator_id`/`campaign_id`/
`form_id`, nunca pelo agente avaliado. Para rolar a nota ao `agent_key`+`pool_id` é **obrigatório o
join `evaluation_results → segments` por `session_id`** (segmento primário), ou desnormalizar
essas colunas no ingest. Decisão de mecanismo fica para a fase respectiva (§13, Fase 2).

---

## 7. Camada de sinais de sessão (NPS / wrap-up / pesquisa) — `session_signal`

As três vantagens (sistêmica/cliente/agente) convergem numa camada normalizada — é ela que
viabiliza "tudo num comparativo" sem caso-especial por fonte.

```
session_signal(
  session_id, agent_key, pool_id,
  source,         -- ai_eval | customer_nps | agent_wrapup | customer_survey
  metric,         -- score | nps | disposition | criterio.* ...
  value_num,      -- nota
  value_label,    -- categoria
  session_at,     -- data da SESSÃO original (base de bucketização)
  captured_at,    -- quando o sinal chegou (≠ session_at em pesquisa diferida)
  journey_id      -- liga pesquisa diferida à sessão/jornada original
)
```

### Captura
NPS e wrap-up são hooks (`on_human_end`) que rodam como agentes especialistas. Veículo natural:
**Arc 12 `agent_event`** — o hook de NPS emite `agent_event(nps, score)`, o de wrap-up
`agent_event(wrapup.disposition, …)`; um consumer normaliza para `session_signal`. Sem pipeline
novo — convenção de categoria + normalização.

### Variação por pool
- **NPS/CSAT é cross-pool comparável** — normalizar para escala comum (0–10 → promotor/neutro/detrator).
- **Wrap-up é taxonomia do pool** (configurada por hook). Comparar **dentro do pool**, ou via um
  **campo de topo padronizado** (resolvido/não-resolvido) entre pools. O `pool_id` no sinal informa
  quando agrupar dentro do pool vs cross-pool.

### No ato × diferida (antecipar agora)
- Hoje NPS é síncrono (`session_at ≈ captured_at`).
- Pesquisa pós-atendimento = um `collect`/workflow que sai por outro canal (e-mail/WhatsApp) dias
  depois e volta como interação separada, **religada via `journey_id`/`origin_session_id`** (Arc 10/19).
  Escreve `session_signal(source=customer_survey)` com `captured_at`=agora e **`session_at`=data da
  sessão original**.
- **Regra de ouro**: bucketizar por **`session_at`**, nunca por `captured_at` — senão a quali
  desalinha do quanti. Vale para o eval de IA também.
- **Cobertura/N visível** — NPS e pesquisa têm taxa de resposta.

---

## 8. O cruzamento das vantagens (o payoff)

Onde a gestão acontece — destacar a **divergência**:

- **Wrap-up "resolvido" × IA "não-resolvido"** → acurácia da disposição (agente super-reporta?).
- **IA alta × NPS baixo** → gap processo-vs-percepção.
- **Três concordando alto** → bom de verdade.

Efeito de 2ª ordem: o **NPS é ground-truth externo que calibra o avaliador de IA** (Arc 13). Diver-
gência sistemática IA×NPS = ajustar o rubric, não o agente → alimenta o Calibration Dashboard.

Visões dedicadas a considerar:
- **Concordância das vantagens** — as 3 notas lado a lado por agente, divergência destacada.
- **Quadrante** (opcional) — volume/resolução no X × qualidade no Y: separa "estrelas" de
  "precisa de coaching" num olhar.

---

## 9. Detalhe (pop-up) — type-aware

Ao abrir um agente, o detalhe **diverge por tipo**:
- **Humano**: timeline (login/pool/pausa) + donuts (pausa; logged/available/busy/pause) + quebra de
  qualidade (notas por critério, avaliações recentes com evidência, contestação) + sinais (NPS/wrap-up).
- **IA**: sem timeline humana — resolution no tempo, sessions, nota por critério, sinais.

Consolida o agente inteiro num lugar, eliminando o pulo para a página de Avaliação.

---

## 10. Regras transversais

- **Média = aritmética dos agentes**, rotulada **"média dos agentes"** (não "taxa do pool"), com
  **N visível**. É a métrica adequada à comparação com pares (coaching) — não precisa de métrica
  proprietária. Taxa pooled fica como 2ª linha opcional, se um dia pedirem.
- **Ausente ≠ zero**: agente sem dado num bucket → **gap**, nunca 0.
- **Cor estável por entidade** entre as lentes (não recolorir ao trocar de métrica).
- **Escopo ABAC**: lista respeita `accessible_pools`/`supervised_agent_types` (já no backend).
- **Persistência da bancada**: seleção + lente sobrevivem à navegação (URL/localStorage).
- **Export CSV** do conjunto comparado.
- **N/cobertura** visível em toda métrica amostral (avaliação, NPS, pesquisa).

---

## 11. Contrato de endpoint (esboço)

Um endpoint que recebe a **lista de entidades + a lente** e devolve todas as séries de uma vez
(não N chamadas):

```
GET /reports/agents/compare
  ?tenant_id&from_dt&to_dt&pool_id?
  &lens=resolution|sessions_aht|availability|pause_reason|quality|quality_criteria|nps|wrapup
  &entities=agent_key1,agent_key2,...    (vazio = média do pool)
  &include_average=true
```
```json
{ "data": {
    "average": { "label": "média dos agentes", "n": 7, "series": [ … ] },
    "entities": [ { "agent_key": "…", "label": "…", "agent_type": "human|native", "series": [ … ], "summary": { … } } ]
  },
  "meta": { "lens": "…", "bucket": "day", "from_dt": "…", "to_dt": "…" } }
```

`series` bucketizada por `session_at`; `summary` = consolidado do período (com N quando amostral).

---

## 12. Pendências / dependências de implementação

1. **Backend — outcome humano**: bridge grava `outcome`/`issue_status` no segmento humano
   (pré-requisito de resolution/escalation para humano).
2. **Join avaliação → agente**: garantir `agent_key`/`pool_id` em `evaluation_results` (ou enriquecer).
3. **Camada `session_signal`**: tabela ClickHouse + consumer que normaliza `agent_event`
   (NPS/wrap-up) e respostas de pesquisa diferida (collect/journey) → `session_signal`.
4. **Convenção de categoria** dos hooks NPS/wrap-up no `agent_event` (Arc 12).
5. **Normalização**: NPS → escala comum; wrap-up → campo de topo padronizado + taxonomia pool-scoped.
6. **Endpoint** `/reports/agents/compare` (multi-entidade, multi-lente).
7. **platform-ui**: bancada (lista + seletor de lente + gráfico + média de referência), detalhe
   type-aware, quadrante/concordância, i18n en + pt-BR.
8. **Ordem sugerida**: outcome humano → session_signal + normalização → endpoint compare → UI da
   bancada → camadas quali/sinais → cruzamentos (quadrante/concordância).

> **Nota**: o §12 é o esboço original. A recon de 2026-06-07 (§13) corrige a premissa do item 1,
> trava as decisões de domínio/contrato e substitui a ordem do item 8 por um plano de fases.

---

## 13. Recon 2026-06-07 — Achados, Decisões Travadas e Plano de Fases

> Auditoria dirigida das fontes que a bancada consome, validada **no código** (não só nos docs) e
> com números do ClickHouse (`tenant_demo`). Substitui as premissas de §5/§6/§12.1 e a ordem de §12.8.

### 13.1 Achados empíricos

- **Qualidade (Arc 6)** — `evaluation_results` (ClickHouse) **não tem atribuição a agente**
  (`evaluator_id`/`session_id`/`form_id`/`campaign_id`/`overall_score` apenas; sem
  `user_id`/`flow_id`/`pool_id`/`agent_key`). O produtor (`parse_evaluation_event`) também não recebe
  isso. Atribuir nota a agente exige **join `evaluation_results → segments` por `session_id`**.
- **Voz do cliente/agente (Arc 12)** — pipeline `agent_event` → `agent.events` →
  `analytics.agent_business_events` **está completo e funcional**. Porém os hooks `agente_nps_v1` e
  `agente_wrapup_v1` **não emitem `agent_event`** — só gravam no ContextStore (`session.nps_score`,
  `session.wrapup.*`). Nada chega ao ClickHouse. Não existe tabela/consumer `session_signal`.
- **Outcome humano** — o segmento `primary` humano **já grava `outcome`** (Fase A do
  queue-attended-model), mas é **placeholder**: a Console (`AgentAssistPage`) envia
  `{outcome:"resolved"}` no botão Encerrar e `{outcome:"abandoned"}` na desconexão — **nunca a
  disposição real**. Não há `CloseModal` com seletor (aposentado no Arc 14). Números: dos **55**
  segmentos `human/primary`, outcome = `resolved` (24) / `abandoned` (12) / `NULL` (19); `issue_status`
  = **0/55**. IA (`native/primary`) tem outcome real em **219/228**. A disposição real do humano
  (`resolvido/pendente/escalado/cancelado`) é coletada pelo `agente_wrapup_v1` em
  `session.wrapup.classificacao` (ContextStore, scope segment) — **presa lá**.
- **`complete` é literal-only** — `executeComplete` retorna `step.outcome` do YAML; não resolve
  nada dinâmico. O `pipeline_state` já guarda `wrapup_classificacao` (via `output_as`), e o engine já
  resolve refs contra `pipeline_state` (choice) e ContextStore — tornar dinâmico é mudança pequena.
- **`close_reason` / `handoff_reason` / `issue_status`** — já são **colunas do segmento** e
  **parâmetros do `_publish_participant_event`**, mas **não são passados** no evento do segmento
  humano hoje. O bridge **já deriva** o `close_reason` correto no fechamento (`_close_reason_biz`).
- **delegate (F3) vs hooks** — delegate é `@mention` do Console; wrap-up/NPS são **pool hooks
  `on_human_end`** disparados pelo bridge (`conversations.inbound` sintético com `conference_id`).
  Mesmo *runtime* (specialist na conferência, `agent_done`, segmento próprio), gatilho distinto.
  `hook_pending:{hook_type}` → quando chega a 0, `_trigger_contact_close()` derruba a conferência
  (por isso o último hook a terminar fecha a sala).
- **Quatro enums de outcome inconsistentes** — `OutcomeSchema` (4, contrato do agente),
  `CompleteStepSchema.outcome` (5, com `transferred_agent`), `SegmentOutcomeSchema` (11, **cânone**),
  `SessionOutcomeSchema` (= Segment + `error`). Forks: `transferred_agent` (agente) × `transferred`
  (ledger); o `complete` não declara `escalated_ai`/`suspended`/`abandoned`.
- **Segmentos sintéticos** — `agent_type='system'` (outage) e `role='queue'` existem (system/primary
  19, system/queue 10, native/queue 21). `query_agent_performance_report` já filtra
  `agent_type != 'system'`; queries novos da bancada devem replicar o filtro.

### 13.2 Decisões de design travadas

- **Princípio (simetria na produção)**: o `complete` de **todo** agente devolve um `outcome`
  **dinâmico** definido pela lógica do YAML. O segmento `primary` **humano** apenas **propaga** o
  outcome que o **wrap-up** devolveu (o humano não é um flow; seu companion reporta por ele). A
  bancada lê `segment.outcome` de forma **idêntica** para humano e IA — nenhum caso-especial na leitura.
- **Domínio (modelo do produto: `pending ≡ suspended`, `transfer ≡ escalate`)** — **nenhum valor
  novo**. Mapa de normalização do wrap-up: `resolvido→resolved`, `escalado→escalated`,
  `cancelado→abandoned`, `pendente→suspended`. O rótulo cru pool-scoped vai para `issue_status`.
- **Reconciliação de enums** — `SegmentOutcomeSchema` é o cânone (ledger = fonte da verdade).
  `CompleteStepSchema.outcome` é ampliado para incluir `escalated`, `suspended`, `abandoned` (valores
  já válidos no cânone) — o subset declarável do wrap-up. **`transferred_agent` NÃO é renomeado**
  (recon F1.1: é valor de contrato load-bearing — SDK certify/adapter, e2e, mcp-client; há adapter
  que mapeia `transferred`↔`transferred_agent`). A divergência `transferred_agent`↔`transferred` e o
  folding da família `{escalated, escalated_human, escalated_ai, transferred}` → `escalated` são
  tratados **na leitura** (F3); o alvo humano-vs-IA é recuperável pela topologia do segmento seguinte.
  Sem migração destrutiva, sem mexer no `OutcomeSchema` (contrato v1) nesta fase.
- **`pending` = rótulo terminal** — `pendente→suspended` é **disposição terminal** (o contato fecha);
  **não** dispara suspend de sessão real (isso seria arco separado, fora desta bancada). **Check de
  implementação**: garantir que nenhum consumidor leia `segment.outcome=suspended` como "a sessão vai
  resumir" (o status de sessão é campo separado do outcome do segmento).
- **Contrato do segmento** (lido igual p/ humano e IA):
  - `outcome` — disposição normalizada (humano: do wrap-up via `complete` dinâmico; IA: do próprio complete).
  - `close_reason` — *enum*, iniciativa/causa do encerramento (`customer_*` vs `agent_*`). Já derivado
    pelo bridge; **passar a fiar no evento do segmento**. Distingue "abandono do cliente" de
    "encerrado pelo agente" sem inchar o `outcome`.
  - `handoff_reason` — *texto livre*, motivo da escalação (obrigatório quando `outcome != resolved`).
    Fica texto livre, exibido **só no detalhe** por ora; a versão normalizada/agregável (taxonomia +
    lente) é a **F7 (futura)**. `close_reason` (enum) já é normalizado — nada a fazer além de fiá-lo.
  - `issue_status` — rótulo curto pool-scoped (a `classificacao` crua do wrap-up).
  - **Texto livre rico** (`resumo`/`proximos_passos`) → **detalhe sob demanda** da origem
    (ContextStore/stream), com controle de acesso. **Não** vai em massa para o ledger (LGPD).
- **`session_signal`** — necessário para **NPS** (voz do cliente — não tem casa no `segment.outcome`)
  e leva o wrap-up **de carona** para os cruzamentos do §8. Bucketizar por **`session_at`**.
- **Filtros sintéticos** — todo query novo exclui `agent_type='system'` e `role='queue'`.

### 13.3 Item 1 revisado (substitui §12.1)

> **Outcome real do agente — `complete` dinâmico + propagação ao `primary` humano.**
> (1) Engine: `complete` resolve `outcome` dinâmico do `pipeline_state` (síncrono; o valor já está lá
> via `output_as`). (2) Schema: reconciliar os enums (§13.2). (3) `agente_wrapup_v1`: `complete`
> devolve a `classificacao` normalizada; grava o rótulo cru em `issue_status`; define `handoff_reason`
> no caso `escalado`. (4) Bridge: propagar a disposição do wrap-up para o segmento `primary` humano e
> fiar `close_reason`/`handoff_reason`/`issue_status` no evento. (5) Validar no ClickHouse: outcomes
> humanos reais e variados + `close_reason` + `issue_status`; resolution/escalation por agente humano
> passa a ter sentido.

### 13.4 Plano de fases (substitui §12.8)

Cada fase é validável com o usuário (ele executa build/restart/queries). Ordem honra "§12 item 2 antes
de 3-5": espinha → qualidade → endpoint → UI → sinais → cruzamentos.

| Fase | Escopo | Entrega validável |
|---|---|---|
| **F1 — Espinha: outcome real** ✅ (2026-06-07) | enums reconciliados (§13.2, sem rename de `transferred_agent`); `outcome_from` no complete (engine); wrap-up `scope: session` (ids crus mantidos); bridge **B1′** (re-publish do primário humano, 2 gatilhos NX, normalização no bridge, `issue_status`=cru, `handoff_reason`=resumo, `close_reason` via `close_origin`); **causa-raiz corrigida**: notify nunca implementara `context_tags` (destrava NPS p/ F5) | ✅ validado E2E: `escalated·escalado·resumo` / `resolved·resolvido·agent_hangup`. Ver `CHANGELOG.md` 2026-06-07 + `conference-mechanics.md` Mudança 5 |
| **F2 — Atribuição da qualidade** ✅ (2026-06-07) | join em query-time (`_session_agent_attribution_sql`: último primary não-sintético, argMax) — retroativo, sem mudança de ingest; `/reports/evaluations` com agente avaliado + `summary group_by=agent_key\|pool_id`. De brinde: **pipeline de avaliação Arc 3/6 religado** (7 elos dormentes — ver CHANGELOG) + `agent_login` no avaliador (test-grade) | ✅ validado E2E: 5 avaliações atribuídas (agent_key/user_login/pool). Limitações test-grade no CHANGELOG (ReplayContext sem session_meta; sem associação campanha/form) |
| **F3 — Endpoint `/reports/agents/compare`** ✅ (2026-06-07) | `query_agents_compare`: lentes v1 resolution (folding escalate-family), sessions_aht, availability/pause_reason (humano, denominadores §5), quality (por `session_started_at`, N amostral); média aritmética por bucket c/ gap ≠ zero; pendentes 400+lista (nps/wrapup→F5, quality_criteria); ABAC + filtros sintéticos | ✅ validado com dado real (n=8 em escopo; série reflete efeito F1; quality n=5). Ver CHANGELOG |
| **F4 — UI da bancada** | lista (pools→agentes, checkbox/chevron/detalhe) + seletor de lente + gráfico + média de referência; detalhe type-aware; cor estável; ABAC; URL/localStorage; export CSV; i18n en+pt-BR | bancada quanti+quali funcional |
| **F5 — Camada `session_signal`** | tabela + consumer normalizando `agent_event`; hooks NPS/wrap-up emitem `agent_event` (convenção de categoria); normalização NPS; bucketização por `session_at`; lentes NPS/wrap-up | voz do cliente (NPS) na bancada + cobertura/N |
| **F6 — Cruzamentos (§8)** | concordância das vantagens; quadrante (volume/resolução × qualidade); calibração do avaliador (Arc 13, IA×NPS) | visões de divergência/payoff de gestão |
| **F7 — (opcional/futuro) Motivo de escalação normalizado** | taxonomia pool-scoped configurável (espelha `pause_reasons`/Arc 8) + menu no `agente_wrapup_v1` p/ caso `escalado` + código normalizado no segmento (`handoff_reason` livre vira nota) | lente "motivo de escalação" empilhada (à la pause-motivo) |

> **`reason` — normalização**: `close_reason` é *enum* fechado e derivado pela plataforma → já
> normalizado (só fiar no segmento). `handoff_reason`/`error_reason` são *texto livre* por contrato →
> ficam detalhe-only em F1; a dimensão agregável de "motivo de escalação" é a **F7**, opcional/futura.

**Pontos deixados explícitos para a fase respectiva** (não travados agora): mecanismo da propagação
wrap-up→primary (bridge write-back vs join na leitura) — F1; mecanismo do join de qualidade
(query-time vs desnormalização no ingest) — F2; campo de origem do `handoff_reason` no wrap-up
(reusar `resumo`/`proximos_passos` vs menu próprio) — F1.
