# Arc 6 Fase 2 — Observabilidade por Deploy — Spec de Implementação (arco próprio)

> Spec fechada (2026-06-19) para o arco próprio que implementa a Fase 2 do Arc 6. Escrita após a
> auditoria do T16 (confirmou que **nada deste arco existe no código** — era ✅ falso) e revisada com
> a decisão de **consolidação no bench**: a observabilidade por deploy entra como **uma nova lente no
> board de Agentes (Analytics→Agents)**, não como página/abas separadas.
>
> Visão/mockups (alvo de design original): `docs/arcos/arc6-phase2-observability.md`.
> **Esta é a fonte de implementação.**

---

## 1. Objetivo

Responder **"o deploy melhorou ou piorou a qualidade?"** ancorando a leitura em **deploys** (fatos
datados objetivos) em vez de intervalos arbitrários — **dentro do board de comparação de agentes que
já existe**, como mais uma lente.

---

## 2. Decisões fechadas (2026-06-19)

| # | Decisão | Racional |
|---|---|---|
| **D1** | **Deploy timeline via REST do agent-registry no query-time** (cache curto). SEM tabela `analytics.deploy_events`, SEM consumer Kafka, SEM mudar o emit do agent-registry. | `skill_deployments` já existe; `GET /v1/skills/:id/deployments` serve. Evita pipeline de ingest. |
| **D2** | **Qualidade = `evaluation_finalized` (T11), modo Oficial.** `evaluation_score` = `avg(final_score)`. | Invariante "`evaluation_finalized` é a única fonte de verdade"; não mistura provisório entre versões. |
| **D3** | **A Fase 2 é uma NOVA LENTE `deploy` no board de Agentes** (`/reports/agents/compare?lens=deploy`), não uma tela/aba nova. Reusa todo o mecanismo de lente (entidades, série, média, escopo). As views mortas `TimeseriesView`/`ComparisonView` da `AnaliseQualidadePage` são **deletadas** (cleanup). | Consolidação 2026-06-16: TODA comparação vive no bench; abas Trend/Comparison da Quality foram removidas para isso. A lente de deploy é o jeito nativo de adicionar a capacidade sem refragmentar. |
| **D4** | **Comparação de versão = epochs como buckets da própria lente** (+ N por bucket p/ significância). NÃO há endpoint dual-slice A-vs-B separado. | "Antes/depois do deploy" é ler dois buckets de epoch adjacentes — emerge da lente, sem form A/B. Período-arbitrário-A-vs-B e overlay multi-métrica seguem no backlog (não-objetivo). |

---

## 3. Como o board funciona hoje (a base que a lente herda)

- **`GET /reports/agents/compare?tenant_id&from_dt&to_dt&lens&entities&pool_id&include_average`**
  (`reports_query.query_agents_compare`, set `_COMPARE_LENSES`) → por entidade (agent_key = `user_id`
  humano / `flow_id` IA) devolve **série diária** da lente + **"média dos agentes"** (aritmética por
  bucket, N visível, gap quando sem dado), escopado por `accessible_pools`/`supervised_agent_types`.
- Lentes atuais: `resolution`, `sessions_aht`, `availability`, `pause_reason`, `quality`, `nps`,
  `session_nps`, `wrapup`, `quality_criteria`, `escalation_reason`. Algumas são **domain-gated**
  (`human`/`ai`).
- **Frontend** `AgentsBenchPage`: lista `LENSES`, seletor de entidades, `CompareChart` com **branch por
  lente** (ex. `if (lens === 'quality')`), Recharts já com `ReferenceLine` em uso. A lente `quality`
  já plota `avg(final_score)` por dia.
- A atribuição sessão→agente (último primary não-sintético; `agent_key` = `flow_id` p/ IA) vem de
  `_session_agent_attribution_sql` — a lente `quality` já junta `evaluation_finalized`/results a
  `segments` por `session_id`. **A lente `deploy` herda exatamente essa atribuição.**

---

## 4. A lente `deploy` — comportamento

Para a(s) entidade(s) **IA** selecionada(s) (agent_key = `flow_id` = `skill_id`):

1. **Série de qualidade por epoch de deploy.** Bucket = intervalo `[deploy N, deploy N+1)` do skill da
   entidade (em vez do bucket diário). Cada ponto: `version_label` (do deploy N), `avg_score`
   (`avg(final_score)` das `evaluation_finalized` atribuídas àquela entidade na janela do epoch),
   `n_evaluations`. *(Alternativa de visualização: série diária + linhas verticais de deploy — ver §6
   `deploy_markers`. As duas saem da mesma query; o front decide.)*
2. **Marcadores de deploy** (`deploy_markers`) para desenhar `ReferenceLine` verticais e rotular versão.
3. **Significância por bucket**: `n_evaluations` por epoch; o front sinaliza `N < MIN` (MIN=30, config
   `quality_comparison_min_sample`). Comparar versão N vs N+1 = ler dois buckets adjacentes.
4. **Domain-gated `ai`**: humanos não têm deploy de skill → lente indisponível/oculta p/ entidade humana.
5. **Média**: mantém a "média dos agentes" da lente (referência), por epoch.

Deploys vêm do agent-registry (D1): `GET {agent_registry_url}/v1/skills/{flow_id}/deployments`
(httpx, cache `(tenant,skill)` TTL ~60s); indisponível → série sem markers (não 500).

---

## 5. Gap (construir)

1. **analytics-api → agent-registry**: config `agent_registry_url` + helper `fetch_skill_deployments(tenant, skill_id)` (httpx + cache).
2. **Lente `deploy`** em `query_agents_compare` (`_COMPARE_LENSES += 'deploy'`, domain `ai`): qualidade de
   `evaluation_finalized` atribuída por `flow_id`, bucketizada por epoch + `deploy_markers` + `n_evaluations` por bucket.
3. **Frontend bench**: `LENSES += deploy` + branch no `CompareChart` (linha de score por versão/epoch +
   `ReferenceLine` por deploy + rótulo de versão + flag `N<30`). Domain-gate `ai`.
4. **Cleanup**: deletar `TimeseriesView`/`ComparisonView` mortas de `AnaliseQualidadePage`
   (e `MetricSelector` se ficar órfão — ver §8).

**Não muda**: o contrato externo `/reports/agents/compare` (só ganha um valor de `lens`); nenhum endpoint
novo; nenhuma tabela/consumer/evento novo.

---

## 6. Contrato (extensão do endpoint existente)

`GET /reports/agents/compare?...&lens=deploy` → mesmo envelope das outras lentes, com a série por epoch +
markers. Forma sugerida (alinhar ao shape de `CompareResp` no front ao implementar):
```
{
  series: [ { entity: <agent_key>, points: [
      { bucket: <version_label|deploy_id>, deploy_id, deployed_at (ISO),
        avg_score (0–1|null), n_evaluations } ] } ],
  average: [ { bucket, avg_score, n_evaluations } ],     // "média dos agentes" por epoch
  deploy_markers: [ { deploy_id, skill_id, version_label, deployed_at, deployed_by } ],
  meta: { lens: "deploy", bucket: "deploy_epoch", min_sample: 30 }
}
```
*(Se a leitura por epoch ficar custosa no 1º corte, entregar série diária + `deploy_markers` — o front já
desenha `ReferenceLine`; a granularidade por epoch entra como refinamento. Decidir no P2-B.)*

---

## 7. Fases / chunks (pequenos, revisáveis)

> **Status de entrega (2026-06-20):** P2-A/B/C ✅ entregues e validados no browser, **mas no formato do
> 1º corte da §6** (série DIÁRIA + `deploy_markers`), **não** na granularidade por **epoch/versão** do §4.1.
> O bucket por epoch + a reavaliação da média/multi-seleção ficaram **diferidos** (decisão do usuário:
> "deixar como está e reavaliar"). Ver CHANGELOG "P2 — ... (1º corte §6)" e CLAUDE.md § Arc 6 Fase 2.

| Chunk | Entrega | Onde | Teste | Status |
|---|---|---|---|---|
| **P2-A** | `fetch_skill_deployments` (REST agent-registry + cache + config `agent_registry_url`) | analytics-api | seed deploy no agent-registry → helper retorna a lista | ✅ (`test_deployments_client.py`, 9) |
| **P2-B** | Lente `deploy` em `query_agents_compare` (qualidade + markers + N; domain `ai`) | analytics-api | seed `evaluation_finalized`+`segments`+deploys → série + markers + N | ✅ **diária** (epoch/versão diferido) (`test_deploy_lens.py`, 5) |
| **P2-C** | Bench: `LENSES += deploy` + branch `CompareChart` (linha + `ReferenceLine` + flag N<30); **deletar views mortas** da Quality | platform-ui | browser | ✅ (série diária; média/multi-seleção a rever) |

Ordem A → B → C. C inclui o cleanup das views mortas.

**Follow-up (núcleo do objetivo, não entregue):** série por **epoch/versão** (§4.1) — eixo X = versões da
skill, ponto = qualidade média da versão, N por versão; e reavaliar a "média dos agentes"/multi-seleção
(§4.5/D3) que viram ruído nesta lente. Limitações registradas: markers exigem `flow_id == skill_id` (§8);
`ReferenceLine` em eixo categórico só rende com o dia do deploy como categoria (o front injeta).

---

## 8. Pontos de atenção / riscos

- **Atribuição query-time** (`segments`): avaliações sem segmento primary atribuível ficam fora — igual
  às outras lentes; documentar.
- **Entidade IA = `flow_id` = `skill_id`**: a lente assume isso (o deploy é por skill). Confirmar no P2-B
  que `agent_key` da entidade IA bate com o `skill_id` do `skill_deployments`.
- **Acoplamento analytics→agent-registry** (D1): novo hop HTTP; indisponibilidade → markers vazios, nunca 500.
- **`evaluation_finalized` 0–1**: não re-normalizar.
- **`MetricSelector` órfão**: hoje só as views mortas o usam (overlay `agent_event:*`). Ao deletar as
  views, ou (i) deletar o `MetricSelector` junto, ou (ii) mantê-lo se formos plugar overlay `agent_event`
  na lente depois (backlog d). Decidir no P2-C.
- **Shape do `CompareResp`**: validar os nomes contra `AgentsBenchPage`/`CompareChart` antes de fechar P2-C.

---

## 9. Fora de escopo (backlog — capacidades NÃO presentes no bench, diferidas)

- **(c) Comparação de período arbitrário A-vs-B** do mesmo agente (a lente `deploy` cobre versão/epoch,
  não janelas arbitrárias).
- **(d) Overlay multi-métrica** num só gráfico + overlays `agent_event:*` na lente de qualidade.
- **C3** — painel de N grupos (`Analytics→Comparação`).
- **Tabela `analytics.deploy_events` + consumer Kafka** (substituída por D1/REST).
- **NPS** como métrica e **export PDF/XLSX**; **timeline tenant-wide** (todos os skills).

---

## 10. Referências
- Consolidação 2026-06-16 (TODO.md § "Consolidação Quality ✅"): toda comparação no bench; backlog (a)-(d).
- T11 (`evaluation_finalized`), Arc 5 (`segments`/MV), Arc 12 (`agent_business_events`), Arc 4
  (`skill_deployments`/deploy), Arc 7/9 (escopo).
- Código: `reports_query.py` (`query_agents_compare`, `_COMPARE_LENSES`, `_session_agent_attribution_sql`),
  `AgentsBenchPage.tsx`/`CompareChart` (lentes + `ReferenceLine`), agent-registry `GET /v1/skills/:id/deployments`.
- Visão/mockups: `docs/arcos/arc6-phase2-observability.md`.

---

## 11. Decisão de âncora — POOL (revisão pós-validação, 2026-06-20)

> Revê a premissa §4/§8 ("entidade = `flow_id = skill_id`") **a favor do pool**, após walkthrough com o
> usuário. Motivada pelo cenário **um skill em vários pools** e pela natureza do modelo de dados real.

**Modelo de dados confirmado** (`agent-registry/prisma/schema.prisma`): `skill_id` é **estável** (o editor
edita o `flow` do mesmo `skill_id`; `version` é campo à parte; deploy não muda o id). Deploy é
**pool-centric**: `PoolSkillSlot` (3 slots previous/current/next por pool, snapshots imutáveis,
promote/rollback) + `SkillDeployment.pool_ids[]` (um deploy atinge vários pools). `segments` carrega
**`pool_id` E `flow_id`**.

**Decisão:** a unidade da lente `deploy` é o **pool** (precisamente o par `(pool, skill)`, que colapsa em
pool enquanto um pool roda um skill por vez via o slot `current`).
- **Curva** = `avg(final_score)` das sessões do pool no tempo (atribuição já devolve `pool_id`).
- **Timeline de versão** = deploys **do pool** (`SkillDeployment` onde `pool ∈ pool_ids`), cada um com
  `skill_id`+`version`+`deployed_at`. Cobre bump de versão (mesmo skill), troca de skill e multi-pool.
- **Multi-pool:** skill S em pools A e B → **uma curva por pool**; o deploy de S (que atinge A e B) é o
  **mesmo marcador** nas duas curvas → lê-se "a v2 ajudou em A, piorou em B".
- **Sem a "média dos agentes"** nesta lente (vira ruído — §4.5/D3 revisto para esta lente).

**Por que muda o que foi entregue:** a P2-B atual ancora por `flow_id` (skill) → mistura pools e não mostra
S@A vs S@B. Re-ancorar por `pool_id`.

**Chunks (P3) — ✅ ENTREGUES e validados no browser (2026-06-20):**
- **P3-A** ✅ `agent-registry`: `GET /v1/pools/:id/deployments` (deploys onde `pool ∈ pool_ids`, desc por
  `deployed_at`). Header `x-tenant-id`. Validado por curl.
- **P3-B** ✅ `analytics-api`: `_compare_deploy_lens` agrupa por `attr.pool_id`; `_fetch_deploy_markers` usa
  `fetch_pool_deployments` (markers com `pool_id`+`skill_id`+`version_label`). Test `test_deploy_lens.py` (5).
  Fix CH: `any(attr.agent_type)` (constante `'ai'` colidia com o `WHERE` → query falhava). **Bucket por epoch
  NÃO entrou** (eixo segue = tempo) — é o P4.
- **P3-C** ✅ `platform-ui`: entidades = pools (curva por pool, marcadores = `ReferenceDot` por pool na altura
  da curva, chip `pool · skill versão`); sem "média dos agentes"; estado-vazio "selecione um pool".

**Não-objetivo ainda:** `(pool, skill)` explícito (só se um pool rodar vários skills em paralelo). **P4 (núcleo
§4.1):** série por **epoch/versão** — eixo X = versões do pool, ponto = qualidade média da versão, N por versão.
