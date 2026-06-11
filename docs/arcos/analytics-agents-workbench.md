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

### Grão do sinal (decisão 2026-06-09) — segmento × contato × jornada

NPS/wrap-up têm **grão**, e o grão decide onde o sinal mora e como a bancada o trata:

- **Grão segmento** (F5 implementado): NPS/wrap-up configurados no `on_human_end` de um pool →
  atribuídos ao **segmento humano específico** que encerrou (chave `session_id`+`segment_id`,
  carimbada no `hook_conf` do disparo à conclusão). Moram em `segments` (`nps_score`, `outcome`,
  `issue_status`). Comparáveis **por agente** na bancada. Suportam N humanos por contato (handoff
  sequencial: cada participação tem seu sinal).
- **Grão contato**: NPS/CSAT perguntado **uma vez no fim do contato** (hook de fechamento de contato,
  não `on_human_end` de pool) — sobre a experiência geral, **não atribuível a um agente**. Não cabe
  em `segments`. Vai para `session_signal` (`grain=contact`); na bancada é métrica de **contexto do
  contato**, não linha de comparação por agente.
- **Grão jornada**: pesquisa **diferida** (collect/workflow dias depois), religada por
  `journey_id`/`origin_session_id` — `captured_at ≠ session_at`. `session_signal` (`grain=journey`).

A tabela `session_signal` (§7) é justificada pelos grãos **contato/jornada** (futuro); o grão
**segmento** dispensa-a (mora no próprio `segments`). Campo `grain` (segment|contact|journey) +
`journey_id` na `session_signal` quando ela for criada.

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
| **F4 — UI da bancada** ✅ (2026-06-09) | `AgentsBenchPage` em `/analise/agents` (legado→`/analise/agents-legacy`): árvore pools→agentes (chevron/checkbox/detalhe), seletor de 5 lentes c/ domínio, gráfico por lente + média de referência, detalhe type-aware (donut humano), combo de pool, persistência na URL, export CSV, cor estável, i18n en+pt-BR. **Subfases F4.1–F4.5 — ver CHANGELOG 2026-06-09.** Pendentes na UI: nps/wrapup (F5), quality_criteria; refinamento: pool-average como série agregada (pseudo-entidade `pool:`) | ✅ validado E2E (série reflete F1; n=8; quality n=5) |
| **F5 — NPS + wrap-up (grão segmento)** ✅ (2026-06-09) | **decisão**: derivar de `segments` (não `session_signal`) — grão segmento. `segments.nps_score`; refator per-segmento no bridge (NPS/wrap-up atribuídos ao segmento do pool que disparou o hook, via `hook_conf` 5º campo + acumulador `seg_signal`; corrige single-segment da F1.4). Lentes nps/wrapup no compare + UI (7 lentes). `session_signal` (grãos contato/jornada) e session_at×captured_at (surveys diferidas) → futuro | ✅ validado E2E single-humano (nps=9 + escalado no mesmo segmento); multi-humano correto por construção. Ver CHANGELOG + conference-mechanics §Mudança 7 |
| **F6 — Cruzamentos (§8)** ✅ (2026-06-09) | endpoint `/reports/agents/cross` (`query_agents_cross`: seg_agg `LEFT JOIN` eval_agg por `agent_key` — resolution/escalation/quality/nps lado a lado por agente); UI view **Cross-cut** (toggle Lentes↔Cruzamento): tabela de concordância com 3 flags de divergência (★ destaque, ⚠ lacuna de percepção, ◑ disposição) + quadrante resolução×qualidade (bolha=sessões, cor=NPS); export CSV sensível à view; linha clicável reusa o detalhe type-aware (F4.4). **Só flag de divergência** (sem score combinado); calibração do avaliador (Arc 13, IA×NPS) fica para o Calibration Dashboard | ✅ validado E2E (7 agentes; humano res 0.64/qual 0/NPS 100; sac_ia res 0.14/esc 0.35; 2 estrelas por res≥0.7 sem qual). Ver CHANGELOG 2026-06-09 |
| **F8 — Lente `quality_criteria` (eixo dimensão) + radar no detalhe** ✅ (2026-06-09) | **decisões**: eixo = **dimensão** (não critério cru — critério vira drill-down); comparável **só dentro do mesmo formulário** (guard na UI). **F8.1** ingest: `analytics.evaluation_dimension_scores` (ReplacingMergeTree); parser emite 1 linha/dimensão de `dimensions[]` **ou** fallback `dimension_threads[]` (caminho real do `agente_avaliacao_v1`/Arc13). **F8.2** `_compare_quality_criteria_lens`: nota média por (agente, dimensão) via atribuição por `session_id`, `summary.dimensions[]`+`form_id`; sai de `_COMPARE_LENSES_PENDING`. **F8.3** UI heatmap agente×dimensão (cor=nota, legenda 0–10, guard multi-form). **F8.4** radar das dimensões no detalhe (cor estável do agente). Atribuição query-time ao agente AVALIADO (como F2) | ✅ validado E2E com fixture (seed de `evaluation_dimension_scores` a partir dos `evaluation_results` reais — demo sem pipeline de form/avaliador): heatmap admin(n=6)×sac_ia(n=1) + radares. Ingest unit-tested (parser 2 fontes) |
| **F9 — pool-average como pseudo-entidade `pool:`** ✅ (2026-06-09) | compare aceita `entities=pool:<id>`: recomputa per_agent escopado ao pool e devolve entidade `{agent_key:"pool:<id>", agent_type:"__pool__", label:"média · <id>", series, summary}` com a MESMA média aritmética por bucket (gap≠0). Helpers fatorados: `_per_agent_for_lens`, `_mean_series`, `_aggregate_pool_summary` (escalar→média, reasons/dispositions→soma por id). UI: botão **μ** no cabeçalho do pool fixa a média como linha **tracejada** (cor estável, persiste em `sel=pool:`). Sem ingest | ✅ validado (13 testes; curl `pool:sac_ia` → summary 57 sess/res 0.14/esc 0.35 batendo com o cross) |
| **F7 — Motivo de escalação normalizado** ✅ (2026-06-09) | **decisões**: seed de 8 motivos (`customer_request`, `out_of_scope`, `needs_authorization`, `technical_issue`, `specialist_needed`, `retention`, `policy_exception`, `other`); captura **humano + IA**. Config `agent_activity/escalation_reasons` (espelha `pause_reasons`, override por pool). Coluna `segments.escalation_reason` (+`ConversationParticipantEvent`/`EscalateStep.reason`). **Humano**: menu no `agente_wrapup_v1` só quando classificação=escalado (choice) → bridge grava no acumulador `seg_signal` (só p/ outcome escalate). **IA**: `escalate` step `reason` → `output_as` em `pipeline_state.results.escalation_reason` → bridge lê na conclusão do agente IA. Lente `escalation_reason` (barras empilhadas por contagem, `summary.reasons[]`, só família escalate); UI remapeia id→label pelo config. `handoff_reason` segue como nota livre | ✅ validado E2E com fixture (55 segmentos escalados via `ALTER UPDATE` + labels do config na legenda); testes parser/lente |

> **`reason` — normalização**: `close_reason` é *enum* fechado e derivado pela plataforma → já
> normalizado (só fiar no segmento). `handoff_reason`/`error_reason` são *texto livre* por contrato →
> ficam detalhe-only em F1; a dimensão agregável de "motivo de escalação" é a **F7**, opcional/futura.

**Pontos deixados explícitos para a fase respectiva** (não travados agora): mecanismo da propagação
wrap-up→primary (bridge write-back vs join na leitura) — F1; mecanismo do join de qualidade
(query-time vs desnormalização no ingest) — F2; campo de origem do `handoff_reason` no wrap-up
(reusar `resumo`/`proximos_passos` vs menu próprio) — F1.

---

## 14. `session_signal` — grão session/workflow (F10)

> Recon 2026-06-10 (ETAPA 0) validada no código. Implementa o item **deferred** mais estrutural
> do §7: NPS/CSAT/pesquisa no grão **session** (a sessão/contato inteiro) e **workflow** (uma
> execução de workflow), não atrelados a um segmento. O grão **segmento** já mora em `segments`
> (F5) e não usa esta tabela.
>
> **Vocabulário (2026-06-10)**: o §7 original falava em "contato/jornada"; o termo **journey foi
> abandonado** (entidade eliminada no Arc 19 — confunde com docs desatualizados). O grão descreve
> **o quê** a pesquisa cobre — `session` (≈ "contato" do §7) ou `workflow`; o **timing** (no ato ×
> diferido) é `captured_at` × `session_at`, **não** um grão.

### 14.1 Achados da recon (estado atual ≠ premissas do §7 original)

- **Journey foi eliminada** (Arc 19 Fase F). Não há tabela `contacts`/`journeys`; o "contato" **é** a
  `session_id`. A rastreabilidade multi-sessão é o par **`origin_session_id`** (materializado em
  `analytics.sessions`) — `parent_session_id` foi planejado mas **não materializado** no DDL.
- **`journey_id` é vestigial** — sobrevive como coluna `Nullable` em `agent_business_events`, resolvido
  pelo McpInterceptor de `session:{id}:meta`, mas quase sempre `null` pós-Arc 19. **Não é mais a chave
  de religação** — o §7 foi escrito antes da eliminação. O link canônico é `origin_session_id`.
- **Captura de NPS hoje**: grão segmento (F5) via hook `on_human_end` → acumulador `seg_signal` →
  `segments.nps_score`. O pipeline `agent_event`→`agent_business_events` (Arc 12) funciona, mas os
  hooks NPS/wrap-up **não** emitem `agent_event` (gravam ContextStore + `seg_signal`).
- **Falta p/ grão session/workflow**: a pesquisa é uma **survey outbound** — sessão própria que
  religa à sessão original e grava o sinal contra ela (não um hook inline na mesma conferência).

### 14.2 Decisões travadas (2026-06-10)

- **Modelo (decisão do produto)**: a pesquisa roda numa **survey OUTBOUND** — sessão própria, outro
  pool/canal. **Quem dispara é o fluxo primário que descreve o processo, no seu passo final** (ele
  conhece a semântica do "processo terminou"), delegando a um sub-workflow de pesquisa ao qual passa o
  `session_id` atual como `origin`. Especialmente útil em fluxos que orquestram **múltiplos agentes
  humanos**: uma pesquisa de `session` no fim, em vez de N de segmento. Um hook de fechamento de pool é
  **fallback** para pools puramente humanos sem fluxo orquestrador. Os dois caem no mesmo substrato.
- **Veículo de captura (revisado)**: **tool MCP dedicada `survey_record`** (não reuso de `agent_event`).
  `origin_session_id`, `grain` e a lista de `signals[{metric,value,value_label?}]` são parâmetros
  estruturados de 1ª classe → sem a checagem de namespace `category[0]==pool` do Arc 12 nem convenção
  de sufixo. Publica em tópico novo `session.signals` → parser `parse_session_signal_event` → 1 linha
  por métrica. *(O dual-write sobre `agent_event` da F10.1 foi retirado — ver F10.2a.)*
- **Religação**: o sinal é **chaveado pelo `origin_session_id`** (`session_signal.session_id = origin`).
  Coluna `journey_id` mantida por compat, populada com a chave canônica.
- **Grão (taxonomia canônica)**: `{segment, session, workflow, journey}` (`SignalGrainSchema`).
  **Armazenamento unificado (decisão 2026-06-10, opção 2)**: TODOS os grãos moram em `session_signal`,
  gravados **explicitamente** via `survey_record` (um `invoke` no skill-flow de pesquisa) — sem
  mecanismo de eventos/derivação. `segment` carrega `segment_id` + `agent_key` (atribuição ao agente);
  os demais não são atribuíveis (`agent_key=''`). `journey` é rótulo de grão (relacionamento
  multi-sessão), **não** a entidade Journey eliminada. Timing (no ato × diferido) = `captured_at` ×
  `session_at`, não grão. Dedup `(tenant, session, grain, segment_id, metric)` — `segment_id` na chave
  evita colidir N segmentos da mesma sessão. **`segments.nps_score` (F5) vira legado**, aposentado no
  cutover da bancada (F10.3). Motivo: sem unificar, a bancada duplicaria o plumbing de NPS/CSAT entre
  duas tabelas; e `segments` só tem `nps_score` (1 métrica) — `session_signal` é genérico (N métricas).
- **Extensibilidade**: skill-flows de pesquisa são customizáveis → `survey_record` aceita **N métricas**
  numa chamada; `nps`/`csat` normalizam escala+label, métricas extras passam o valor cru (label None).
- **Escopo F10**: infra + grão **session** E2E. Survey diferida (`captured_at ≠ session_at`; `session_at`
  da sessão original via enrichment) e grão **journey** ponta-a-ponta = **F11** futura.
- **Atribuição**: grãos session/workflow/journey **não** são atribuíveis a um agente → `agent_key=''`,
  `pool_id` só como contexto (na bancada = contexto da sessão). Grão **segment** É atribuível
  (`segment_id` + `agent_key`) → será a fonte da lente `nps`/`csat` por agente após o cutover (F10.3),
  substituindo `segments.nps_score`.
- **Bucketização**: sempre por **`session_at`** (regra de ouro §7).

### 14.3 Tabela e ingest

`analytics.session_signal` (ReplacingMergeTree, dedup por `(tenant, session, grain, segment_id, metric)`,
TTL 2a em `session_at`): `signal_id, tenant_id, session_id, grain(segment|session|workflow|journey),
segment_id, agent_key, pool_id, source(customer_nps|customer_csat|customer_survey), metric, value_num,
value_label, session_at, captured_at, origin_session_id, journey_id, date`.

Ingest: `survey_record` (mcp-server) → Kafka `session.signals` → `parse_session_signal_event`
(analytics-api) → 1 linha/métrica, `session_id = origin_session_id`. `segment` exige `segment_id`.
Normalização: NPS 0–10 → promotor(≥9)/neutro(7–8)/detrator(≤6); CSAT 1–5 → satisfeito/neutro/insatisfeito.

### 14.4 Sub-fases

| Sub-fase | Escopo | Entrega validável |
|---|---|---|
| **F10.1 — Camada de dados** ✅ (2026-06-10) | DDL `session_signal`+migração; `insert_session_signal` + dispatch. *(Ingest inicial via dual-write sobre `agent_event` — substituído na F10.2a.)* | seed → linha em `session_signal` |
| **F10.2a — Tool `survey_record` + tópico `session.signals` (store unificado)** ✅ (2026-06-10) | tool MCP dedicada (`@plughub/schemas` `SurveyRecordInputSchema`/`SessionSignalEventSchema`/`SignalGrainSchema`); `parse_session_signal_event` (1 linha/métrica, chaveado por `origin_session_id`, N métricas, normalização nps/csat); **gravação explícita de TODOS os grãos** incl. `segment` (com `segment_id`+`agent_key`); `segment_id` na chave de dedup. Dual-write de `agent_event` retirado. Testes. **Não toca conferência.** | seed `session.signals` → linhas chaveadas à sessão original; grão segment com atribuição |
| **F10.2b.1 — Esqueleto trigger→record** ✅ (2026-06-10) | `survey_record` com `tenant_id` **explícito**; `skill_survey_v1` (perfil workflow, pool `survey_processo_ia`) lê `@ctx.session.origin_session_id` → `survey_record(grain=session)` valor semeado; `skill_atendimento_sac_v1` + step `disparar_survey` (`workflow_trigger`). **4 fixes de plataforma** (ver CHANGELOG): input array no `StepInputValueSchema`; resolução webhook `skill_id`→pool no routing (`webhook_skill_id` casado); `skill_id` no `ConversationInboundEvent`; auth tenant-explícita. **Sem `collect`.** | ✅ trigger → `pool=survey_processo_ia` → `session_signal(grain=session, nps=9)` chaveado ao origin |
| **F10.2b.2 — I/O real do cliente (`delegate`, inbound_only)** ✅ (2026-06-10) | mecanismo = **`delegate`** (proven Arc 19). `skill_survey_v1` delega a `survey_collector_ia` com `contact_identifier` → pending; reconexão via `survey_reconnect_ia` (`agente_survey_reconnect_v1` + `pending_workflow_get`) → coletor `agente_survey_nps_v1` (menu NPS + `workflow_resume`) → `survey_record($.pipeline_state.coletar_nps.nps)`. **Fix de plataforma**: recursão de arrays no `interpolate.ts` (refs dentro de `signals[]`). `webchat-test.html` ganha `survey_reconnect_ia`. | ✅ E2E real: webchat responde NPS=8 → `session_signal(grain=session, nps=8, origin=sess-real-2)` |
| **F10.3a — Exposição dos novos grãos na bancada** ✅ (2026-06-10) | lente `session_nps` no `/reports/agents/compare`: `session_signal` (grain=session, metric=nps) ⋈ atribuição por `session_id` → NPS de sessão dos contatos do agente (cruzamento §8, **contexto** não-atribuível). Seção "Voz do cliente" no detalhe type-aware: NPS do agente (segmento, F5) × NPS da sessão (contexto). i18n en+pt-BR. **Não toca F5.** | detalhe mostra NPS agente × NPS sessão; lente válida no compare |
| **F10.3b — Cutover F5 (unificação Opção 2)** ✅ (2026-06-10) | caminho **B unificado** (um fluxo de escrita): `agente_nps_v1` chama `survey_record(grain=segment)`; bridge escreve `session.surveyed_segment_id`/`agent_key` via `@ctx` (`fire_pool_hooks`); `_compare_nps_lens` migra para `session_signal` (grão segment, `INNER JOIN segments` p/ agent_type/label) → lentes `nps`+`session_nps` leem a mesma tabela (**acaba a duplicação**). **Legado removido**: bridge não escreve mais `segments.nps_score` (`_apply_nps_to_segment` deletado); coluna vestigial. | ✅ testes + seed + **E2E do hook real** (fluxo humano → `survey_record grain:segment, nps=8`). Cutover finalizado |

> **F11.1** ✅ (2026-06-11) — **enrichment de `session_at` para surveys diferidas**. O consumer
> resolve `analytics.sessions.opened_at` da sessão original (por `origin_session_id`) e sobrescreve
> `session_at` no ramo `session.signals`; `date`/TTL seguem do row builder. Fallback seguro =
> `captured_at`. `AnalyticsStore.lookup_session_opened_at` + `consumer._enrich_signal_session_at`
> (cache FIFO). Grão `journey` já aceito (parser/schema). Ver CHANGELOG 2026-06-11.
>
> **F11.2 (validação)** — diferido **simulado via curl/seed** (decisão do usuário): publicar
> `session.signals`/`survey_record` com `origin` de sessão `opened_at` dias anterior + grão `journey`,
> conferir `session_at = opened_at` no ClickHouse. Sem agendador (workflow agendado real fica futuro;
> schema/tool já comportam, sem migração).

### 14.5 Fechamento da bancada (follow-ups A)

| Item | Estado | Nota |
|---|---|---|
| 1 — built-in `$.segment_id` | ✅ (2026-06-11) | `resolveJsonPathRef` (`interpolate.ts`) expõe `segment_id: ctx.segmentId` no `evalContext`. Skill lê `$.segment_id` e passa a `survey_record(grain=segment)` — sinal de segmento **sobre si mesmo** sem injeção do bridge. Atribuição NPS-sobre-o-humano (segment de OUTRO agente, via `hook_conf`/`@ctx`) segue na F10.3b. |
| 2 — F11.1 enrichment `session_at` | ✅ (2026-06-11) | Consumer resolve `opened_at` da origem e sobrescreve `session_at` no ramo `session.signals` (regra de ouro §7 no diferido). Fallback `captured_at`. F11.2 = validação E2E diferido simulado (curl/seed); workflow agendado real fica futuro. |
| 3 — quality cross-form | ✅ (2026-06-11) — **re-escopado** | Merge de dimensões cross-form **descartado** (inventa equivalência inexistente — rubricas/pesos/escala diferem por form). **Regra de comparabilidade**: cross-agente exige mesmo form; cross-form só p/ um único agente. `_compare_quality_lens` expõe `summary.form_ids`; UI da lente `quality` faz guard (multi-agente+multi-form) ou ressalva (1 agente+multi-form). Corrige o ponto cego de a lente já mediar cross-form silenciosamente. `quality_criteria` (dimensões) segue same-form. **Catálogo canônico de dimensões** (única base rigorosa p/ comparar dimensões entre forms) fica como arco futuro. |
| 4 — E2E real F5/F7 + fixtures | 🔄 (2026-06-11) | **F7** código destravado: `escalate` da IA (`skill_atendimento_sac_v1`) ganhou `reason: specialist_needed` (gap — sem isso o segmento IA não grava `escalation_reason`); humano já cabeado. Falta rodada E2E (limpar sintético → fluxo real → conferir). **F5** multi-humano valida na sequência. **F8 ADIADO**: avaliador `agente_avaliacao_v1` não roda no demo (test-grade) → conserto do pipeline de avaliação é arco próprio; `evaluation_dimension_scores` segue com fixture documentado. |
| 5 — DROP `segments.nps_score` | ✅ (2026-06-11) | Coluna vestigial aposentada. Leitor esquecido no `query_agents_cross` migrado p/ `session_signal` (grain=segment). Removida de DDL/cols/row/parser + bridge. DROP idempotente auto-aplica no startup do analytics-api. `session_signal` = fonte única de NPS de segmento. |
| 6 — débitos de teste | ✅ (2026-06-11) | Drift teste×impl (produção OK). `TestQueryAgentAvailabilityReport` (6) → sinal `(client, database, tenant_id, …)`. `resolve.test.ts` (3) → modelo multi-instância (result key c/ `instanceId` + `hdel` no hash `menu:waiting`). Só testes. **Follow-ups A (1–6) completos.** |
