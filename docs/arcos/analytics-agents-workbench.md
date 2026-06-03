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

**Dependência dura**: hoje o humano sai 0% em resolution/escalation porque os `segments` humanos
não gravam `outcome`. Para a lente resolution/escalation valer no humano, o bridge precisa gravar
`outcome`/`issue_status` (vêm do `agent_done`) no segmento humano. **Pré-requisito.**

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

**Join central a verificar**: `evaluation_results` já carrega `user_id`/`flow_id`/`pool_id`, ou
precisa enriquecer no join `session → segments`? Decidir antes de codar.

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
