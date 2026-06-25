# ADR: Isolamento do substrato de avaliação por `origin` (híbrido)

**Status:** Aceito — implementado (2026-06-25). Passos 1–6 + fix de rótulo concluídos; ver `CHANGELOG.md`.
Fase 2 (partição CH por origem + `pool.origin_class`) permanece backlog.
**Data:** 2026-06-25
**Componentes:** `packages/analytics-api`, `packages/evaluation-api`, `packages/session-replayer`, `packages/quality-ingest`, `packages/quality-export`, `packages/schemas`, `packages/platform-ui`
**Relacionado:** `docs/arcos/quality-ingest.md` (R13a–R13d, §9 concerns), Arc 6 Fase 2 (epochs ancorados em pool)

---

## Contexto

O arco Quality Ingest (R13a–R13d) tornou o pipeline de avaliação plugável: históricos
**externos** (CCaaS) e a **reavaliação interna** entram pela mesma porta
(`ingestion_event_v1`) e **reusam a infra interna** — eventos canônicos →
consumers de analytics → `session_stream_events`/ReplayContext → sampling → avaliador.
Esse reuso foi uma decisão deliberada (evitar um pipeline paralelo).

O preço do reuso é que os contatos **importados** e **reavaliados** gravam linhas nas
**mesmas tabelas de substrato** de produção (`analytics.sessions/segments/messages`,
`session_stream_events`) e, no R13d, reusam o **`pool_id` original**. Consequências
(registradas em `quality-ingest.md` §9):

- **Contaminação de relatório**: métricas por pool (volume, TMA, outcome, performance)
  passam a somar sessões de avaliação junto com atendimento real.
- **Cross-fire de amostragem**: uma campanha de produção e uma de reavaliação no mesmo
  pool pegam as sessões uma da outra (possível dupla avaliação do mesmo contato de origem).

**Diagnóstico da raiz.** O problema **não** é "reusar o pool". O `pool_id` reusado é o
sintoma mais visível; a raiz é que (1) o **substrato é compartilhado** com produção e
(2) a **procedência do contato nunca foi modelada** como atributo de primeira classe.
Qualquer caminho — reuso de pool ou pools separados — desemboca na mesma necessidade de
um atributo de procedência por-contato (ver Alternativas, "pools separados").

Observação de escopo: os **resultados** de avaliação (`evaluation_finalized`,
instâncias, calibração) **já são separados** (schema próprio na evaluation-api). Este ADR
trata do **substrato do contato avaliado** (transcript/segments/stream), não dos resultados.

---

## Decisão

Adotar um **discriminador de procedência por-sessão (`origin`)** como o eixo de
isolamento, com partição lógica (e opcionalmente física) — **sem** duplicar o substrato
vivo e **sem** um segundo banco.

### 1. `origin` por-sessão (verdade universal)

Coluna `origin` nas tabelas de substrato (`analytics.sessions/segments/messages`,
`session_stream_events`), domínio `live | import | reeval`, **default `live`**. Derivada
do `source` que os eventos canônicos já carregam:

| `source` do evento | `origin` |
|---|---|
| `external_import` (quality-ingest, fonte externa) | `import` |
| `internal:reeval` (quality-export) | `reeval` |
| demais (`channel_gateway`/bridge — tráfego vivo) | `live` |

O consumer da analytics-api **já vê** o `source`; passa a **persistir** o `origin`
derivado. Default `live` torna a mudança forward-compatible (linhas existentes e tráfego
atual continuam corretos sem backfill).

### 2. Filtro padrão no backend (a garantia de correção)

Um helper único na camada de report da analytics-api aplica `origin = 'live'` **por
default**. Relatórios de qualidade/curadoria pedem explicitamente outra origem. Isto é a
**fonte de verdade** da separação: qualquer consumidor da API que não passe pela UI
continua vendo só produção (defense-in-depth). A amostragem (evaluation-api) ganha um
filtro **opcional** de `origin` — campanha de produção mira `live`, campanha de
reavaliação mira `reeval` — eliminando o cross-fire sem pool dedicado.

### 3. Partição ClickHouse por origem (lifecycle/LGPD) — forward-compatible

`PARTITION BY (toYYYYMM(date), origin)` dá retenção/erasure independentes por origem e
`DROP PARTITION` do substrato de import/reeval sem tocar produção. **Pegadinha**: o
ClickHouse **não altera partition key in-place** → coluna `origin` agora (aditivo,
barato); partição-por-origem só em tabelas novas ou via migração versionada. **Mesmo sem**
a partição física, o filtro de leitura (item 2) já resolve a correção; a partição é só o
ganho de lifecycle.

### 4. Superfície de UI: seletor de **origem** (não de pool)

A UI expõe um seletor de **origem** com default **"Produção (live)"**; incluir
`Importação`/`Reavaliação` exige ação explícita. O multiselect de pool vive **dentro** da
origem escolhida. O eixo é `origin` (não pool) porque, com reuso de pool, um mesmo pool é
origin-misto — agrupar por pool reintroduziria o proxy-furado. A UI espelha o default do
backend; não é a garantia (o default do backend é).

### 5. `pool.origin_class` opcional (conveniência para pools dedicados) — ortogonal a `agent_kind`

Para pools **genuinamente dedicados** (import pass-through, ex. "Genesys-Q-42"; ou pool de
revisão), um campo **novo e ortogonal** `pool.origin_class: production | import | review`
(default `production`). **Não** estender `pool.agent_kind` (human/ai): esse campo governa
gates de capacidade (`C_ai`/`C_human`), validação de registro (instância IA × login humano)
e a proibição de pool misto — "external" não é uma natureza de recurso, é procedência, e
adicioná-lo lá quebra esses invariantes. `origin_class`, quando setado, é **atalho/validador**
do `origin` naquele pool e eixo de agrupamento na UI — **não** substitui o `origin` por-sessão
(necessário porque o R13d reusa pools de produção, que são origin-mistos).

### 6. Reavaliação interna: R13d (re-emite) mantido; zero-cópia como futuro

O R13d **re-emite** o contato (cópia limitada e intencional do snapshot, `origin=reeval`).
Caminho **zero-cópia** (criar uma nova instância de avaliação apontando para o `session_id`
**original**, re-disparando o Replayer) fica registrado como evolução — o trade-off é ser um
**caminho divergente** (o R13d unificou tudo no contrato). Migrar só se o volume de
reavaliação tornar a cópia custosa.

---

## Alternativas consideradas

### A) Compartilhado + remap de pool (paliativo) — rejeitada como solução

Manter tudo nas tabelas de produção e usar o `source_map` (R13c) para mandar reavaliação a
um pool dedicado. Resolve só o caso interno, no grão de pool, e usa pool como proxy de
procedência (furado: import mapeado a pool interno re-contamina). Band-aid, não correção.

### B) Pools separados (dedicados) — contrafactual analisado, rejeitada como mecanismo principal

"E se tivéssemos usado pools separados em vez de reusar?" Consequências:

1. **Explosão de inventário de pool.** `pool.agent_kind` proíbe pool misto; uma reavaliação
   tem segmentos humano **e** IA → exigiria `reeval_human`/`reeval_ai`, e por pool de origem.
   Não é um pool a mais: é um conjunto paralelo espelhando a taxonomia de produção.
2. **Não escapa do atributo por-contato.** Reavaliação quer a dimensão "mesmo pool" (Arc 6
   Fase 2 ancora epochs em pool) → teria que carregar `pool_original` como metadado
   por-contato → reintroduz procedência por-sessão de qualquer forma.
3. **Mesmo store físico.** Pool separado não dá retenção/LGPD independente (CH particiona por
   data). Resolve relatório, não lifecycle.
4. **Briga com objetivo do R13.** Avaliar pools internos **e** importados uniformemente sob o
   mesmo pool (R13c map external→interno) fica proibido.

Conclusão: pools separados trocam um problema por outro **e ainda precisam** de um campo de
procedência. Logo `origin` é o fix independente da decisão de pool.

### C) Banco/produto totalmente isolado — adiada (opt-in futuro)

Qualidade como deployable standalone com store próprio. Máxima limpeza, mas implica
**copy-on-sample** de todo contato vivo amostrado para o store de qualidade + Replayer/
sampling/analytics lendo de lá + relatório cross-produto via join entre bases. Custo alto;
o híbrido entrega ~80% do benefício a ~20% do custo. A separação física vira evolução
opt-in caso o módulo precise mesmo ser vendido/implantado isolado.

---

## Consequências

**Positivas**
- Correção: relatórios de produção e populações de amostragem deixam de misturar.
- Reuso de `pool_id` (R13d) vira **inócuo** (o filtro é por origem, não por pool).
- Lifecycle/LGPD independente por origem (com a partição).
- Custo baixo: uma coluna + um filtro; sem duplicar o vivo; sem segundo banco.
- Não bloqueia a avaliação uniforme interno+externo sob o mesmo pool (objetivo do R13).

**Negativas / custos**
- Toda query de produção precisa passar pelo helper com o default `origin='live'` (risco de
  esquecer numa query nova → vazamento de re-eval no relatório). Mitigar centralizando o
  filtro e cobrindo com teste.
- Partição-por-origem em tabelas existentes exige migração versionada (não in-place).
- A cópia de snapshot do R13d permanece (limitada; zero-cópia adiado).
- `pool.origin_class` adiciona um campo de config (opcional) a manter.

---

## Plano de migração (quando implementar)

1. **schemas/DDL**: coluna `origin String DEFAULT 'live'` em `sessions/segments/messages`
   (CH, aditivo) e `session_stream_events` (PG, aditivo). Sem backfill (default cobre).
2. **analytics-api consumer**: derivar `origin` do `source` e persistir nas linhas.
3. **session-replayer (consumer Y)**: gravar `origin` nas linhas de `session_stream_events`.
4. **analytics-api report layer**: helper de filtro default `origin='live'`; endpoints de
   qualidade/curadoria parametrizam a origem. Teste cobrindo o default.
5. **evaluation-api sampling**: filtro opcional de `origin` na campanha.
6. **platform-ui**: seletor de origem (default Produção), pool dentro da origem.
7. **(opcional, fase 2)** partição CH por origem em tabelas novas/migração; `pool.origin_class`.

**Implementado (2026-06-25):** passos 1–6 + fix de rótulo do mapper (quality-ingest preserva
`internal:reeval` e normaliza sources externos ao marker; consumer Y reconstrói stream de ambos).
Validado E2E por `infra/test/smoke_origin_reeval.sh`. Detalhe por passo em `CHANGELOG.md`.

**Fase 2 (item 7) — ADIADA por decisão (2026-06-25), não enterrada.** A fase 2 (partição CH
`PARTITION BY (…, origin)` + `pool.origin_class`) é **governança/lifecycle, não correção**: a separação
dos dados já é garantida pelo filtro de leitura default `live` (item 2) + sampling — a partição não altera
isso. Como não há importação externa real e a reavaliação tem volume mínimo, o custo/benefício não fecha.
**Gatilho que a reativa:** importação externa real com obrigação de **retenção/erasure própria** (LGPD —
dado de terceiro com prazo distinto, ou direito ao esquecimento escopo `import`/`reeval`), onde só a
separação **física** permite `DROP PARTITION` barato (mutation `ALTER … DELETE` é pesada e não-particionada).
Detalhe e gatilho registrados em `TODO.md`.

**Revisão de UX do item 4 (2026-06-25):** o seletor de origem **não** é exibido nas telas de Analytics
operacionais (Sessions/Pools/Agents). Motivo: a re-emissão é detalhe de implementação e a distinção
import/reeval é contexto de qualidade (produzido por uma ação deliberada — importar/reavaliar — e olhado
num fluxo de revisão), não uma escolha ad hoc num dashboard operacional. A correção não depende da UI (o
default `live` no backend é a garantia). O `OriginSelector`/i18n e o query-param ficam reservados para uma
superfície de qualidade contextual futura, onde a origem é o contexto em que o usuário já está.
