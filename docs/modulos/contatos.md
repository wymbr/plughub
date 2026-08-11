# Módulo: Contatos

> Última atualização: 2026-05-25 · Estado: Arc 16

> Rota UI: `/contacts` | Roles: operator, supervisor, admin, business

## O que é

O módulo Contatos é o hub central de visibilidade do contact center. Unifica em uma única tela a listagem de contatos ativos e históricos, o monitor em tempo real por pool, e a análise agregada de métricas.

A partir do Arc 11, o Monitor deixa de ser apenas uma superfície de visualização read-only: ele é a porta de entrada para o Console — a **superfície de orquestração** onde o operador humano dirige, delega e monitora agentes IA como coparticipantes de primeira classe da sessão (ver módulo Agent Assist). O drill-down de uma sessão ativa leva da observação ao comando.

## Hierarquia de observabilidade

O módulo Contatos opera no nível intermediário de uma hierarquia aditiva de três camadas (Arc 10 / Arc 16):

```
Journey  →  Session  →  Segment
```

- **Journey** — a unidade de serviço que transcende a sessão; agrupa todos os contatos de um mesmo processo de atendimento. Monitorada no módulo Processos (`/agent-flow/processos`).
- **Session** — o contato individual; é o objeto principal das abas Lista e Monitor.
- **Segment** — a janela de cada participante dentro da sessão (primary, specialist, supervisor).

Sessões standalone não têm `journey_id`; sessões que pertencem a um processo carregam o vínculo e podem ser navegadas a partir do módulo Processos.

## Abas

### Lista

Tabela de contatos (ativos e finalizados) com filtros por canal, pool, agente e período. Colunas: identificador do cliente, canal, pool, agente atribuído, status, duração, sentimento médio.

Fonte: `analytics-api` → ClickHouse `sessions FINAL`.

#### Toggle "Incluir sessões internas (wrap-up, dispatch)"

Desligado por padrão (`scope=contacts`, o comportamento fechado pela E2f). Ligado (`scope=all`), a
tabela ganha as **linhas** de pool interno — sessão de wrap-up destacado, dispatch — com tag `INTERNAL`
por `row.is_internal` (veredicto do backend; a UI não reclassifica por `pool_id`) e a coluna **Contato
de origem**, que linka ao contato pai por `origin_session_id`.

**Visibilidade ≠ contagem** (ADR `adr-wrapup-detached-pull` §7.2). O cabeçalho lê `meta.total_contacts`
e **não muda** ao ligar o toggle; a interna é reportada num segundo número (`"12 contacts · 9 internal"`),
nunca somada. Quem dimensiona a paginação é `meta.total` (o que está listado). Nenhum agregado —
TMA, contagem de contato, métricas de pool/agente — aceita ou lê `scope`.

Dois limites que a tela nomeia no tooltip, e que não são acidente:

- Mostra sessão de **pool interno**, não "tudo que é interno". Hook que roda NA CONFERÊNCIA (NPS
  inline) não tem sessão própria e continua invisível mesmo com `scope=all` — `all` relaxa a regra do
  POOL, nunca a do CANAL (relaxar a do canal duplicaria sessão ativa na tela).
- Com `meta.internal_pools_known == 0` o toggle **não é oferecido**: sem conjunto que classifique, não
  há o que distinguir, e prometer o recurso seria pior que omiti-lo.

#### Detalhe do contato — linha do tempo única

A tela de detalhe funde num **eixo só (tempo)** duas coisas que o modelo mantém separadas:

- **segmentos** — participação de cada agente DENTRO da sessão (entram pelo `started_at`);
- **sessões originadas** — sessões que este contato gerou (entram pelo `opened_at`), buscadas por
  `GET /reports/sessions?origin_session_id=<sessão>`: a aresta de **um salto**, não a journey.

Cada originada leva tag por dois eixos: `is_internal` → **interna** (wrap-up, dispatch); raiz de
journey ≠ a do pai → **processo** (nasceu com `journey: new` — **linka** para a Vista Processos e
**não expande**, senão desfaria o corte que o `journey: new` pediu); senão **contato** (ex.: filha de
um `collect`). O breadcrumb acumula a trilha de ancestrais, para que abrir uma filha não perca o
contato de origem.

O cabeçalho mostra **dois números** (`3 total · 1 originada`), nunca somados — segmento e sessão são
escopos diferentes. Mesmo guardrail da listagem (ADR §7.2).

A **prosa do wrap-up** (`wrapup_summary`/`wrapup_next_steps`) aparece sob o segmento que a gravou —
não sob a sessão de wrap-up que a coletou —, porque é ali que o `segment_outcome_record` a escreve por
referência. O drill até as respostas completas está na janela de execução da sessão originada, que
renderiza `answers` como pergunta→resposta usando o DialogForm como **dicionário de rótulos** (chave
que o formulário atual não conhece aparece crua; nunca um rótulo inventado).

### Monitor

Visualização em tempo real dos contatos ativos organizados por pool. Duas visualizações disponíveis:

- **Cards por pool**: heatmap de sentimento por pool, ordenados do pior para o melhor sentimento
- **Lista de sessões ativas**: tabela com wait time, SLA urgency e sinalização de "próximo sugerido"

Drill-down disponível: pool → sessões ativas → transcrição ao vivo com SSE. A partir da transcrição ao vivo, supervisores e operadores entram no Console (Agent Assist) para atuar sobre a sessão — não apenas observá-la: cartões de participantes IA, "Adicionar Especialista", "Delegar Tarefa" e a tab de Orquestração (Arc 11). O Monitor é, portanto, o ponto de partida da orquestração humana.

Fonte: `analytics-api` → Redis snapshots (SSE `/dashboard/operational`, poll 5s) + `session:{id}:stream` (XREAD bloqueante via `/sessions/{id}/stream`).

### Análise

Métricas agregadas do conjunto filtrado: volume por canal, handle time médio, score de qualidade médio, distribuição por outcome. Gráficos de timeseries com interval picker.

Fonte: `analytics-api` → ClickHouse + endpoints `/reports/sessions`, `/reports/agents`, `/reports/timeseries/*`.

## Gate ABAC

| Campo | Efeito |
|---|---|
| `contacts.operacao` | Exibe as abas Monitor e Agent Assist |
| `contacts.visualizar` | Exibe a aba Análise |

Usuários `business` com `operacao: none` veem apenas a aba Lista.

## Pacotes envolvidos

| Pacote | Responsabilidade |
|---|---|
| `analytics-api` | Consumer Kafka→ClickHouse, SSE de snapshots, endpoints de reports e sessions |
| `channel-gateway` | Produz `conversations.inbound`, assina Redis pub/sub para WS delivery |
| `routing-engine` | Produz snapshots de pool no Redis a cada evento de roteamento |
| `mcp-server-plughub` | Tool `supervisor_state` lê ContextStore e retorna `context_snapshot` |
| `platform-ui` | `modules/contacts/` — ContactsPage + ListaTab + MonitorTab + AnaliseTab |

## Eventos Kafka relevantes

- `conversations.inbound` — nova sessão inbound normalizada pelo Channel Gateway
- `conversations.routed` / `conversations.queued` — alocação ou enfileiramento pelo Routing Engine
- `conversations.session_opened` / `conversations.session_closed` — lifecycle da sessão
- `sentiment.updated` — AI Gateway publica score de sentimento após cada turno LLM
- `conversations.participants` — participantes que entram/saem de sessões (analytics de participação)

## Relação com outros módulos

| Módulo | Relação |
|---|---|
| **Agent Assist / Console** | O Monitor exibe as mesmas sessões ativas que o Console atende; o drill-down leva da observação à orquestração (Arc 11) |
| **Processos** | Journeys agrupam múltiplas sessões; o `journey_id` da sessão liga o contato ao processo monitorado em `/agent-flow/processos` (Arc 10/16) |

## Referências

- ADR: `docs/adr/adr-contact-segments.md`
- Guia: `docs/sections/conferencia-e-historico.md`
- Arc 11 (Console como superfície de orquestração): `docs/arcos/arc11-console-orchestration.md`
- Backend: `packages/analytics-api/`, `packages/channel-gateway/`
- Frontend: `packages/platform-ui/src/modules/contacts/`
