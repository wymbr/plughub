/**
 * lens-contract.ts — F1 do ADR `adr-relatorios-duas-superficies-e-lentes.md` (D5 + D6).
 *
 * O QUE ESTE ARQUIVO CONSERTA
 * ---------------------------
 * A `LensDef` anterior tinha quatro campos — `{ id, domain, primaryKey, pct }` — e,
 * medido em 2026-08-28, **dois deles nunca eram lidos**: `primaryKey` e `pct` estavam
 * declarados dez vezes cada e consumidos zero. Só `domain` era vivo.
 *
 * A consequência não era cosmética: como a declaração não carregava métrica nem
 * formato, o `LensChart` teve de hardcodá-los inline (`metricKey="resolution_rate"`
 * `fmt="pct"`), e a pergunta *"esta lente está vazia?"* virou uma **lista de exceção
 * nomeando cinco lentes** — que envelhece a cada lente nova.
 *
 * AS TRÊS PERGUNTAS QUE UMA LENTE PRECISA RESPONDER (D5)
 * ------------------------------------------------------
 *   1. `aggregation` (por MÉTRICA, não por lente — `sessions` soma e `aht_ms` promedia
 *      dentro da MESMA lente, então o campo não cabe no topo).
 *   2. `evidence` — onde vive a prova de que HÁ dado. É a forma operacional de
 *      *"vazio ≠ zero"*: sem ela, ausência de amostra e amostra valendo zero são
 *      indistinguíveis na tela.
 *   3. `comparability` — o que torna duas entidades comparáveis nesta lente. É o campo
 *      que a mesa descobriu e resolveu UMA vez, inline, na guarda cross-form de
 *      `quality`.
 *
 * Mais um quarto que a D6 exige e que hoje é um booleano espalhado por cinco
 * condicionais do seletor (`deployLens`):
 *   4. `entity` — o TIPO de coisa comparada. A lente `deploy` compara POOLS, não
 *      agentes, e é só isso que aquele booleano dizia.
 *
 * ONDE ELE MORA, E POR QUE NÃO EM `@plughub/schemas`
 * --------------------------------------------------
 * A F1 do ADR dizia "@plughub/schemas". Medido antes de escrever: **o platform-ui não
 * importa esse pacote** — não é dependência, não há alias em `vite.config.ts`, e a UI
 * *espelha* tipos à mão em quatro lugares (`outbound/api.ts`, `schedules/api.ts`,
 * `DialogFormRenderer.tsx`, `AgentFlowDeployPage.tsx`). Pôr o contrato lá o deixaria
 * sem nenhum consumidor — criando o órfão que a F0 acabou de caçar. O backend é
 * Python e não importaria TS de qualquer forma.
 *
 * Ele mora, então, onde é consumido; e a coerência com o backend é imposta por
 * MECANISMO, não por convenção: a seção D de `infra/test/probe_report_surface.sh`
 * compara estes ids com o `_COMPARE_LENSES` de `reports_query.py` e reprova na
 * divergência.
 */

// ── Vocabulário ───────────────────────────────────────────────────────────────

/** Formato de exibição da métrica. Antes vivia inline no JSX do `LensChart`. */
export type MetricFormat = 'pct' | 'time' | 'count' | 'score'

/**
 * Como a métrica se compõe entre buckets e entidades.
 *
 * `recomputed` é o valor que impede o erro mais caro: taxa e índice **não se somam
 * nem se promediam** — recalculam-se da população. Promediar `resolution_rate` entre
 * dois agentes com volumes diferentes, ou o índice NPS entre dois buckets, dá um
 * número que parece certo e não é.
 *
 * `interval_union` e `max` ainda não têm métrica declarada aqui: entram com as lentes
 * de recurso (F3). Não os declaro antes de haver quem os use.
 */
export type MetricAggregation = 'sum' | 'avg' | 'recomputed'

/**
 * O TIPO de coisa que a lente compara (D6).
 *
 * `contact` entrou na F2 com a superfície A: ali a lente não compara entidades ao
 * longo do tempo — ela agrega a POPULAÇÃO filtrada em buckets. É outra família, e
 * confundi-las foi o que produziu a mesa como página (D6).
 */
export type LensEntity = 'agent' | 'pool' | 'contact'

/**
 * De ONDE a lente tira o dado. Campo acrescentado na F2, e não por simetria: a
 * seção D de `probe_report_surface.sh` compara os ids declarados aqui com o
 * `_COMPARE_LENSES` de `reports_query.py`. Sem `source`, a primeira lente da
 * superfície A faria o gate ficar VERMELHO pelo motivo errado — ela não é, nem deve
 * ser, conhecida por aquele endpoint.
 *
 * É a mesma pergunta que `BACKEND_ONLY_LENSES` já respondia por exceção nomeada; com
 * duas fontes reais, a exceção vira campo.
 */
export type LensSource = 'agents_compare' | 'contacts_series' | 'own'

/**
 * Quais filtros da barra a lente REALMENTE aplica.
 *
 * `all` — a lente consulta o mesmo predicado da lista (`/reports/contacts/series`
 *         compartilha `_session_conditions` com `/reports/sessions`).
 * `period_only` — a lente honra só o intervalo. É o caso da disposição, cujo
 *         agregado é sobre pools internos (`-int`), onde o filtro de pool da barra
 *         não se aplica.
 *
 * O campo existe para a tela poder DIZER isso. Uma barra de filtro que não filtra é
 * a mentira mais barata desta superfície — e ela não fica vermelha em lugar nenhum.
 */
export type LensHonors = 'all' | 'period_only'

/** Quem é elegível: 'universal' (humano + IA) | 'human' | 'ai'. */
export type LensDomain = 'universal' | 'human' | 'ai'

/**
 * Onde vive a prova de que há dado — a forma operacional de *"vazio ≠ zero"*.
 *
 *   `series`    — a lente está vazia se nenhuma entidade tem série no período.
 *   `delegated` — o componente da lente decide e desenha o próprio estado vazio.
 *
 * Só existem dois porque só há dois em uso. Um `reasons`/`dimensions` genérico seria
 * vocabulário sem consumidor — a mesma dívida que este arquivo remove.
 */
export type LensEvidence = 'series' | 'delegated'

/**
 * O que torna duas entidades comparáveis nesta lente.
 *
 *   `always`    — a grandeza é comparável entre quaisquer entidades.
 *   `same_form` — só dentro do mesmo formulário de avaliação (a régua é o eixo).
 */
export type LensComparability = 'always' | 'same_form'

export interface ReportMetric {
  key:         string
  format:      MetricFormat
  aggregation: MetricAggregation
}

export interface ReportLens {
  id:            string
  entity:        LensEntity
  domain:        LensDomain
  /** Vazio quando a lente não plota escalar (heatmap, barras empilhadas). */
  metrics:       ReportMetric[]
  evidence:      LensEvidence
  comparability: LensComparability
  source:        LensSource
  honors:        LensHonors
}

// ── Declaração das lentes vivas ───────────────────────────────────────────────
// Toda lente NOVA entra aqui, e o tipo obriga a responder as quatro perguntas.
// Não há default: omitir um campo é erro de compilação, que é o ponto.

export const REPORT_LENSES = [
  {
    id: 'resolution', entity: 'agent', domain: 'universal',
    metrics: [
      { key: 'resolution_rate', format: 'pct', aggregation: 'recomputed' },
      { key: 'escalation_rate', format: 'pct', aggregation: 'recomputed' },
    ],
    evidence: 'series', comparability: 'always',
    source: 'agents_compare', honors: 'all',
  },
  {
    id: 'sessions_aht', entity: 'agent', domain: 'universal',
    metrics: [
      { key: 'sessions', format: 'count', aggregation: 'sum' },
      { key: 'aht_ms',   format: 'time',  aggregation: 'avg' },
    ],
    evidence: 'series', comparability: 'always',
    source: 'agents_compare', honors: 'all',
  },
  {
    // A régua é o eixo: comparar a média de qualidade entre AGENTES exige o mesmo
    // formulário. Era guarda inline; virou declaração.
    id: 'quality', entity: 'agent', domain: 'universal',
    metrics: [{ key: 'avg_score', format: 'score', aggregation: 'avg' }],
    evidence: 'series', comparability: 'same_form',
    source: 'agents_compare', honors: 'all',
  },
  {
    // Mesma régua, mesma exigência — e a guarda NÃO existia aqui.
    // ⚠️ Ela ainda não é EXECUTÁVEL: `form_ids` é produzido só pela lente `quality`
    //    (`reports_query.py:4613`). Declarar `same_form` sem o dado faria uma guarda
    //    que nunca dispara, então a UI mostra "não verificável" em vez de silêncio.
    //    O conserto é de backend (expor `form_ids` nesta lente) e não é da F1.
    id: 'quality_criteria', entity: 'agent', domain: 'universal',
    metrics: [],
    evidence: 'delegated', comparability: 'same_form',
    source: 'agents_compare', honors: 'all',
  },
  {
    // Índice NPS não se soma nem se promedia entre buckets — recalcula-se.
    id: 'nps', entity: 'agent', domain: 'universal',
    metrics: [
      { key: 'nps',     format: 'count', aggregation: 'recomputed' },
      { key: 'avg_nps', format: 'score', aggregation: 'avg' },
    ],
    evidence: 'series', comparability: 'always',
    source: 'agents_compare', honors: 'all',
  },
  {
    id: 'wrapup', entity: 'agent', domain: 'universal',
    metrics: [], evidence: 'delegated', comparability: 'always',
    source: 'agents_compare', honors: 'all',
  },
  {
    id: 'escalation_reason', entity: 'agent', domain: 'universal',
    metrics: [], evidence: 'delegated', comparability: 'always',
    source: 'agents_compare', honors: 'all',
  },
  {
    // Arc 6 Fase 2 — âncora no POOL: o mesmo skill roda em N pools, e âncora-skill
    // misturaria pools. É a única lente cuja ENTIDADE não é o agente, e era isso que
    // o booleano `deployLens` dizia em cinco condicionais do seletor.
    id: 'deploy', entity: 'pool', domain: 'ai',
    metrics: [{ key: 'avg_score', format: 'score', aggregation: 'avg' }],
    evidence: 'delegated', comparability: 'always',
    source: 'agents_compare', honors: 'all',
  },
  {
    id: 'availability', entity: 'agent', domain: 'human',
    metrics: [
      { key: 'occupancy_pct', format: 'pct', aggregation: 'recomputed' },
      { key: 'pause_pct',     format: 'pct', aggregation: 'recomputed' },
    ],
    evidence: 'series', comparability: 'always',
    source: 'agents_compare', honors: 'all',
  },
  {
    id: 'pause_reason', entity: 'agent', domain: 'human',
    metrics: [], evidence: 'delegated', comparability: 'always',
    source: 'agents_compare', honors: 'all',
  },
  // ── Superfície A · Contatos (F2) ────────────────────────────────────────────
  //
  // Família DIFERENTE das de cima: aqui a lente não compara entidades ao longo do
  // tempo — agrega a população FILTRADA em buckets. Por isso `entity: 'contact'` e
  // `source: 'contacts_series'`; a mesa não as conhece nem deve conhecer.
  {
    // A lista de contatos. É lente, não "a tela": ela ocupa o mesmo lugar das
    // outras e responde sob os mesmos filtros. Tratá-la como pano de fundo foi o
    // que fez as demais virarem páginas.
    id: 'list', entity: 'contact', domain: 'universal',
    metrics: [], evidence: 'delegated', comparability: 'always',
    source: 'own', honors: 'all',
  },
  {
    id: 'volume', entity: 'contact', domain: 'universal',
    metrics: [{ key: 'contacts', format: 'count', aggregation: 'sum' }],
    evidence: 'series', comparability: 'always',
    source: 'contacts_series', honors: 'all',
  },
  {
    // Do PRÓPRIO elemento (`sessions.handle_time_ms`), nunca da soma dos segmentos:
    // eles se sobrepõem. Ver D9 do Arc 19.
    id: 'duration', entity: 'contact', domain: 'universal',
    metrics: [{ key: 'handle_time_ms', format: 'time', aggregation: 'avg' }],
    evidence: 'series', comparability: 'always',
    source: 'contacts_series', honors: 'all',
  },
  {
    // D4 — três grandezas, e nenhuma deriva das outras: quantos agentes o contato
    // gastou (`resources`), quantas vezes trocou de mão (`handoffs`) e quantos
    // estiveram nele AO MESMO TEMPO (`peak`). É a lente que a revisão pediu.
    id: 'resources', entity: 'contact', domain: 'universal',
    metrics: [
      { key: 'resources', format: 'count', aggregation: 'avg' },
      { key: 'handoffs',  format: 'count', aggregation: 'avg' },
      { key: 'peak',      format: 'count', aggregation: 'avg' },
    ],
    evidence: 'series', comparability: 'always',
    source: 'contacts_series', honors: 'all',
  },
  {
    // T3 — consumo de LLM atribuído ao contato.
    //
    // ⚠️ **Não há `tokens_total`**, e a ausência é decisão: entrada e saída têm preços
    // diferentes em todo provedor, então somá-las daria o número mais fácil de
    // publicar e o menos utilizável — mesma família da soma de licença humana com
    // licença de IA que a admissão recusa.
    //
    // `tokens_per_contact` responde outra pergunta que a soma: um bucket pode ter o
    // dobro de tokens por ter o dobro de contatos, ou por os contatos custarem o
    // dobro. Só os dois números juntos separam os casos.
    id: 'tokens', entity: 'contact', domain: 'universal',
    metrics: [
      { key: 'tokens_in',          format: 'count', aggregation: 'sum' },
      { key: 'tokens_out',         format: 'count', aggregation: 'sum' },
      { key: 'tokens_per_contact', format: 'count', aggregation: 'avg' },
    ],
    evidence: 'series', comparability: 'always',
    source: 'contacts_series', honors: 'all',
  },
  {
    // Absorve `/analise/wrapup`. **`period_only` é medição, não folga**: o agregado
    // é sobre pools internos (`-int`), onde o filtro de pool da barra não se aplica
    // — e é por o campo existir que a tela consegue DIZER isso em vez de exibir uma
    // barra que não filtra.
    id: 'disposition', entity: 'contact', domain: 'human',
    metrics: [], evidence: 'delegated', comparability: 'always',
    source: 'own', honors: 'period_only',
  },
] as const satisfies readonly ReportLens[]

export type LensId = (typeof REPORT_LENSES)[number]['id']

/** Lentes da superfície A, na ordem em que a faixa as apresenta. */
export const CONTACT_LENSES = REPORT_LENSES.filter(l => l.entity === 'contact')

export type ContactLensId = (typeof CONTACT_LENSES)[number]['id']

export function isContactLens(id: string): id is ContactLensId {
  return CONTACT_LENSES.some(l => l.id === id)
}

/**
 * `session_nps` existe no backend (`_COMPARE_LENSES`) e é buscada pelo painel de
 * detalhe do agente, mas **não é selecionável** — não tem botão nem gráfico próprio.
 * Não está em `REPORT_LENSES` porque este contrato descreve lente PLOTÁVEL; declará-la
 * aqui exigiria responder quatro perguntas sobre algo que não se desenha.
 * A seção D do probe conhece a exceção e a nomeia, em vez de acusar divergência.
 */
export const BACKEND_ONLY_LENSES = ['session_nps'] as const

export type BackendOnlyLensId = (typeof BACKEND_ONLY_LENSES)[number]

/**
 * Lente que se pode BUSCAR — plotável ou não. O painel de detalhe do agente
 * consome as duas famílias; a faixa de botões, só as plotáveis. Separar os dois
 * conjuntos foi exigência do compilador, não escolha: ao derivar `LensId` da
 * declaração, o `session_nps` do painel deixou de compilar — e a distinção que
 * estava só num comentário virou tipo.
 */
export type FetchableLensId = LensId | BackendOnlyLensId

export function lensById(id: string): ReportLens | undefined {
  return (REPORT_LENSES as readonly ReportLens[]).find(l => l.id === id)
}
