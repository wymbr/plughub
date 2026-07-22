# Customer Voice — lente genérica (grain × metric) + KPIs operacionais

> **Status:** Fatia 1 ✅ (2026-07-22) — carimbo de escala + catálogo source-aware + query
> genérica + superfície "Voz do Cliente" com overlay SLA. NPS (índice) + CSAT/CES/PMF/FCR
> (avg) + SLA (% dentro do alvo). Reenquadra o **J4** como a fatia journey desta camada.

## Ideia

A `session_signal` já é **uniforme** — `(grain, metric, value_num, scale)`. Logo "lente" não é
plumbing novo: é **parametrizar** a consulta por grão × instrumento (as lentes antigas hard-codavam
`metric='nps'` e o grão). A superfície única **Voz do Cliente** lê essa base; **J4** (journey) e
**Cliente 360** são consumidores da mesma query.

## Lares por grão (não tudo no /agents)

- **segment** → atribuível ao agente → board de Agentes (lente NPS que já existia).
- **session** → o contato.
- **journey** → o processo N3 (**J4**).
- **cross-cutting por `customer_id`** → **Cliente 360** (já agrega por métrica).

Uma journey cruza vários agentes → grão journey/session **não** cabe no board de agentes.

## Carimbo de escala (imutabilidade)

A escala do instrumento vive na `DialogDimension` do form — **mas o form é editável**. Resolver a
escala por `form_id` na leitura reescreveria o roll-up de respostas passadas. Então a escala é
**snapshot no sinal** (`SurveySignal.scale` → `session_signal.scale_min/max`), carimbada por
`composeSurveySignals` no momento da resposta. Nunca referência mutável ao form. Habilita top-box/%alvo
exatos depois, sem reprocessar histórico. (Mesmo princípio de `deploy_version`/`yaml_snapshot`.)

## Catálogo source-aware + roll-up

`CV_INSTRUMENTS` (`reports_query.py`): `metric → {source: survey|operational, rollup, label,
higher_is_better, grains}`. O **roll-up populacional** vive no analytics (o form define escala +
composição **por-respondente** via `composeScore`, não a estatística **entre** respondentes):

- **nps** → índice `(%prom≥9 − %detr≤6)`.
- **csat/ces/pmf/fcr** → `avg` (agnóstico de escala). top-box/%alvo = refinamento com a escala
  carimbada + polaridade.
- **sla** (operational) → `% dentro do alvo` (`wait_time_ms ≤ sla_target_ms`).

Princípio: **só KPIs sem interpretação** primeiro. AHT (inflado por wrap-up, G1), taxa de resolução
(ambiguidade de `close_reason`) e qualidade avaliada (rubrica) ficam de fora até a definição acordar.

## Substrato / endpoints

- `query_customer_voice(grain, metric, período)` — série diária (`session_at`) + roll-up do catálogo;
  `_cv_sla_series` — overlay SLA sobre `sessions` no mesmo eixo. grain=journey: 1 survey/processo →
  agrupar por dia basta (sem union-find na série).
- `GET /reports/customer-voice?grain&metric&from_dt&to_dt&pool_id` + `GET /reports/customer-voice/instruments`.
- **Leitura descritiva** — justapõe percebido (survey) × objetivo (SLA); **não conclui causa** (nota na UI).
  N de survey pequeno → correlação por-entidade é ruidosa; o overlay temporal agregado é mais estável.

## UI

`modules/analise/CustomerVoicePage.tsx` (`/analise/customer-voice`) — seletor grão × instrumento (do
catálogo) + gráfico SVG de overlay (survey + SLA). Nav em Analytics (ABAC `contacts.visualizar`), i18n
`customerVoice`.

## Cresce

Outros instrumentos (módulo Customer Surveys), outros KPIs operacionais **um por vez** (só sem
interpretação), **perceived FCR (survey) × actual FCR (operacional)** lado a lado, correlação por-entidade
(scatter), N3 na própria Vista Processos (pendurar o sinal no drill), Cliente 360 estendido.
