# Dashboard de Qualidade — Protótipo (Piloto)

> Responsabilidade: visualização de dados de qualidade de atendimento —
> scores por agente, perfil de desempenho por dimensão e drill-down até
> item individual e transcript.

---

## Visão geral

SPA React que lê diretamente do ClickHouse via uma API de consulta
leve (FastAPI). Não tem estado próprio — é uma camada de visualização
sobre os dados já persistidos pelo ClickHouse Consumer.

O objetivo da demo é mostrar três níveis de inteligência em sequência:
**visão de pool** (quem está performando bem?), **perfil de agente**
(em quais dimensões?), e **drill-down de contato** (por quê?).

---

## Stack técnica

```
Frontend:  React 18 + TypeScript + Vite
           Tailwind CSS
           Recharts         ← gráficos de linha e barras
           shadcn/ui        ← componentes base

Backend:   FastAPI (Python)  ← API de consulta ao ClickHouse
           clickhouse-driver ← cliente Python para ClickHouse
```

A API backend é propositalmente fina — apenas traduz chamadas REST
em queries ClickHouse e devolve JSON. Sem ORM, sem cache, sem auth
no piloto.

---

## Estrutura de navegação

```
/                         → redireciona para /pool/retencao_humano
/pool/:pool_id            → Visão do Pool
/agent/:agent_id          → Perfil do Agente  (com ?pool_id=...)
/contact/:contact_id      → Drill-down do Contato
```

Navegação linear: Pool → Agente → Contato.
Cada nível tem um link "← voltar" para o nível anterior.

---

## Tela 1 — Visão do Pool

**Rota:** `/pool/:pool_id`

### Header do pool

```
retencao_humano
Período: [últimos 7 dias ▾]     Tipo: [Todos ▾]  [Humano]  [IA]
```

Filtro de período (7d / 30d / 90d) e filtro de tipo de agente.
Ambos disparam re-fetch das queries.

### Cards de resumo

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ Atendimentos │  │ base_score   │  │ Agentes      │  │ Avaliações   │
│     142      │  │  médio 7.8   │  │  ativos  12  │  │  pendentes 0 │
└──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘
```

### Tabela de agentes — ranking por base_score

| Agente | Tipo | base_score | n aval. | churn | portab. | autent. |
|---|---|---|---|---|---|---|
| Agente X | humano | 8.4 | 31 | 6.1 (n=8) | 7.5 (n=12) | — |
| Agente Y | ia | 7.9 | 45 | — | 8.2 (n=20) | 9.0 (n=6) |

- `base_score` sempre exibido com n de avaliações
- `context_scores` exibidos apenas quando `n ≥ 5`; quando `n < 5`,
  exibir `— (n=X)` para indicar que há dados mas ainda instáveis
- Clicar em qualquer linha navega para `/agent/:agent_id?pool_id=...`
- Ordenação por coluna clicável; default: base_score DESC

### Gráfico de tendência do pool

Recharts LineChart com base_score médio do pool ao longo do tempo
(série diária ou semanal dependendo do período selecionado).
Uma linha para agentes humanos, outra para agentes IA.

---

## Tela 2 — Perfil do Agente

**Rota:** `/agent/:agent_id?pool_id=...`

### Header do agente

```
← retencao_humano

Agente X  ·  humano  ·  retencao_humano
Período: [últimos 30 dias ▾]
```

### Card de base_score

```
┌─────────────────────────────────────────────────────┐
│  base_score  8.4  (n=31)                            │
│                                                     │
│  [gráfico de linha — evolução do base_score no      │
│   período selecionado, ponto por avaliação]         │
└─────────────────────────────────────────────────────┘
```

### Cards de context_scores

Um card por seção contextual com `n ≥ 5`. Cards com `n < 5`
exibidos com estado "dados insuficientes" e barra de progresso
mostrando quantas avaliações faltam para atingir o threshold.

```
┌──────────────────────┐  ┌──────────────────────┐
│ gestao_churn         │  │ resolucao_portabilidade│
│  6.1  (n=8)          │  │  7.5  (n=12)          │
│  ██████░░░░          │  │  ████████░░           │
└──────────────────────┘  ┌──────────────────────┐
                          │ autenticacao_complexa │
                          │  dados insuficientes  │
                          │  ░░░  2 de 5          │
                          └──────────────────────┘
```

### Drill-down por seção (expansível)

Clicar em qualquer card de seção expande uma tabela de sub-seções
e itens com a média do agente naquele item:

```
▼ gestao_churn  6.1
  ├─ identificacao_motivador    5.8
  │    sondagem_motivador       5.5   (peso 3)
  │    identificou_motivador    6.0   (peso 5)
  └─ proposta_retencao          6.3
       conhecimento_portfolio   7.0   (peso 3)
       personalizacao_oferta    5.8   (peso 4)
       tratamento_objecoes      6.5   (peso 3)
       resultado_retencao       5.0   (peso 1)
```

Cada linha de item tem botão "Ver contatos" que filtra a tela
para os contatos desse agente onde esse item teve nota baixa
(abaixo da média do pool naquele item).

### Lista de atendimentos recentes

Tabela com os últimos N atendimentos avaliados do agente:

| Data | Contact ID | base_score | Seções contextuais | Ação |
|---|---|---|---|---|
| 06/04 14:00 | abc-123 | 8.7 | churn: 6.1 | [Ver detalhes →] |

Clicar em "Ver detalhes" navega para `/contact/:contact_id`.

---

## Tela 3 — Drill-down do Contato

**Rota:** `/contact/:contact_id`

### Header do contato

```
← Agente X

Contato abc-123  ·  06/04/2026 13:45–14:00  ·  15 turnos  ·  resolved
Agente X  ·  humano  ·  retencao_humano
```

### Scores do contato

```
base_score:  8.7            context[gestao_churn]:  6.1
```

### Formulário de avaliação preenchido

Hierarquia expansível com os valores atribuídos pelo Evaluation Agent:

```
▼ mandatory  →  8.7
  ▼ postura_atendimento  →  9.1
      saudacao_adequada          10  (boolean ✓)
      escuta_ativa                9  "O agente realizou sondagem..."
      clareza_comunicacao         9  "Mensagens objetivas e..."
      empatia                     8  "Reconheceu a frustração no turno 4..."
      encerramento_adequado      10  (boolean ✓)
  ▼ conformidade  →  8.0
      identificacao_cliente       8  "Identificação realizada no turno 1..."
      registro_issue_status      10  (boolean ✓)
      handoff_reason_adequado    10  (boolean ✓  —  outcome: resolved)

▼ gestao_churn  →  6.1
  ▼ identificacao_motivador  →  5.8
      sondagem_motivador          5  "Sondagem presente mas superficial..."
      identificou_motivador_real  6  "Identificou preço como motivador..."
  ▼ proposta_retencao  →  6.3
      ...
```

Cada item com justificativa exibe o texto completo num tooltip ou
expansão inline. Itens excluídos (`items_excluded`) exibidos em
cinza com o motivo da exclusão.

### Transcript do contato

Painel lateral (ou seção inferior) com o transcript completo,
lado a lado com o formulário de avaliação.

```
13:45:02  Cliente:   "Quero cancelar meu plano"
13:45:08  Agente X:  "Olá! Posso ajudá-lo. Pode me contar..."
          sentiment: -0.10  intent: cancellation_request
13:45:45  Cliente:   "O preço está alto, o concorrente..."
          sentiment: -0.25  intent: cancellation_request  flag: churn_signal
13:46:10  Agente X:  "Entendo sua situação. Temos uma oferta..."
          sentiment: -0.20  intent: portability_check
...
```

Cada mensagem exibe:
- Timestamp
- Autor com cor por tipo (cliente / agente humano / agente IA)
- Conteúdo da mensagem
- `intent` e `sentiment_score` do `context_snapshot` (colapsável)

Linha do tempo de sentimento (Recharts) acima do transcript,
com marcadores nos turnos onde flags foram ativadas.

---

## API Backend (FastAPI)

Endpoints mínimos necessários para as três telas:

```
GET /api/pool/{pool_id}/summary
    ?period=7d|30d|90d
    ?agent_type=all|human|ai
    → cards de resumo + tendência temporal do pool

GET /api/pool/{pool_id}/agents
    ?period=7d|30d|90d
    ?agent_type=all|human|ai
    → tabela de agentes com base_score e context_scores

GET /api/agent/{agent_id}/profile
    ?pool_id=...
    ?period=7d|30d|90d
    → base_score + evolução temporal + context_scores + itens médios

GET /api/agent/{agent_id}/contacts
    ?pool_id=...
    ?period=7d|30d|90d
    ?limit=20
    → lista de atendimentos recentes do agente

GET /api/contact/{contact_id}/evaluation
    → scores + formulário preenchido + items_excluded

GET /api/contact/{contact_id}/transcript
    → mensagens ordenadas por turno com context_snapshot
```

---

## Queries ClickHouse de referência

### Tela 1 — tabela de agentes

```sql
-- base_score por agente
SELECT
    agent_id,
    agent_type,
    round(avg(score), 2) AS base_score,
    count()              AS n
FROM evaluation_scores
WHERE pool_id    = :pool_id
  AND section_id = 'mandatory'
  AND evaluated_at >= now() - INTERVAL :days DAY
GROUP BY agent_id, agent_type
HAVING n >= 1
ORDER BY base_score DESC;

-- context_scores por agente (join ou segunda query)
SELECT
    agent_id,
    section_id,
    round(avg(score), 2) AS context_score,
    count()              AS n
FROM evaluation_scores
WHERE pool_id    = :pool_id
  AND score_type = 'context_score'
  AND evaluated_at >= now() - INTERVAL :days DAY
GROUP BY agent_id, section_id;
```

### Tela 2 — médias por item do agente

```sql
SELECT
    section_id,
    subsection_id,
    item_id,
    round(avg(value), 2) AS avg_value,
    count()              AS n
FROM evaluation_items
WHERE pool_id  = :pool_id
  AND agent_id = :agent_id
  AND evaluated_at >= now() - INTERVAL :days DAY
GROUP BY section_id, subsection_id, item_id
ORDER BY section_id, subsection_id, item_id;
```

### Tela 3 — formulário preenchido de um contato

```sql
-- scores por seção
SELECT section_id, score_type, score, triggered_by_key, triggered_by_val
FROM evaluation_scores
WHERE contact_id = :contact_id;

-- itens com justificativa
SELECT section_id, subsection_id, item_id, value, weight, justification
FROM evaluation_items
WHERE contact_id = :contact_id
ORDER BY section_id, subsection_id;
```

### Tela 3 — transcript

```sql
-- lido do PostgreSQL (via API separada ou JOIN — piloto usa API separada)
SELECT turn_number, timestamp, direction, author_type, display_name,
       content_text, intent, sentiment_score
FROM transcript_messages
WHERE transcript_id = :transcript_id
ORDER BY turn_number;
```

---

## O que fica fora do escopo do protótipo

- Autenticação no dashboard
- Filtros avançados (por skill_id, por flag, por outcome)
- Exportação de relatórios (CSV, PDF)
- Alertas automáticos (agente caiu abaixo de threshold)
- Comparação entre pools
- Visão de supervisão em tempo real (atendimentos em andamento)
- Multi-tenancy

---

## Relações com outros módulos

| Módulo | Relação |
|---|---|
| `ClickHouse` | Fonte principal — `evaluation_scores` e `evaluation_items` |
| `PostgreSQL` | Fonte do transcript — `transcript_messages` |
| `ClickHouse consumer` | Produtor dos dados lidos |
| `Conversation Writer` | Produtor do transcript lido |
