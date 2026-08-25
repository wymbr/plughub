# Módulo: Contatos

> Última atualização: 2026-08-14 · Estado: Arc 16 + histórico unificado F3

> Rota UI: `/analise/sessions` (`/contacts` é redirect) | Roles: operator, supervisor, admin, business

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

### Lista — `/analise/sessions` *(reescrita na F3, 2026-08-14)*

A **única** lista de contatos da plataforma. Servida por `SessionsPage` + `ListaTab`
(`modules/contacts/`). Fonte: `analytics-api` → ClickHouse `sessions FINAL`.

**Colunas (9).** direção · contato (canal + id) · `entrou por → atendido por` · [contato de origem] ·
iniciado · duração · desfecho (badge de estado + `outcome`) · segmentos · processo.

> **Largura é requisito, não estética.** A 1ª versão levou 11 colunas — as 7 do desenho mais
> `ended`/`status`/`segments`, mantidas por instinto de mudança mínima — e a tabela passou a exigir
> scroll horizontal, jogando **`processo` para fora da tela**. Como o chip é o único caminho para a
> visão 2, "mudança mínima" escondeu justamente a entrega da fase. `ended` saiu (início + duração a
> dão) e `status` foi fundida em `desfecho`, que é a coluna 6 do desenho.

| Coluna | Fonte | O que não é |
|---|---|---|
| **direção** ⇣⇡⚙ | campo `direction`, DERIVADO no backend (`reports_query._DIRECTION_EXPR`) a partir de `spawn_reason` + canal efetivo; a UI só exibe | não é armazenada, e **não** se chama `origin` — essa chave i18n já significava ANI. A UI **não re-deriva** desde a F4: coluna e filtro são a mesma expressão |
| **entrou por → atendido por** | `pool_id` (a porta, first-write-wins desde a F1b) e `attended_pool_ids` (pools com segmento) | nunca **um** filtro/coluna chamado "Pool": foi o operador ler um e receber o outro que originou este arco |
| **duração** | `elapsed_time_ms` — wall-clock do caso, esperas incluídas (D9) | **nunca** `agent_time_ms`, **nunca** Σ segmentos (eles se sobrepõem; a soma não é uma duração) |
| **processo** | chip `PRC-{journey_id[:8]} · N`, só quando `N > 1` | `journey_id` é a raiz **canônica** (union-find), não `root_session_id` cru. O corte é de **8** desde a F4 — eram dois rótulos (4 aqui, 8 no cabeçalho da visão 2) para o mesmo processo |

**Filtros.** período · canal · **direção** · status · **entrou por** (`entry_pool_id`) · **atendido
por** (`pool_id`) · agente · evento · tags. Os dois pools compõem por AND — é assim que se pergunta
*"entrou no `sac_ia` e terminou no humano"*.

**O filtro de direção usa a MESMA expressão que a coluna** (`?direction=inbound|outbound|internal`).
Não é detalhe de implementação: é o que impede a linha de dizer `interno` e o filtro `inbound` a
devolver. Sessão que o backend não classificou fica **fora das três** — logo `Σ das três ≤ total`, e a
diferença é a população não classificada. Valor fora do domínio é **recusado (422)**, nunca ignorado.

**O chip conta o processo INTEIRO, de propósito.** Uma janela que pega 2 de 3 contatos mostra `· 3`.
O rodapé da tabela nomeia isso, e só aparece quando `meta.window_applied` é verdadeiro **e** há chip na
página — no drill não há divergência a explicar. O N usa o mesmo predicado de contato do card de
`/reports/journeys`, para que chip e cabeçalho da visão 2 nunca discordem.

**Clicar no chip abre o processo na MESMA rota** (`?journey=…`, nível 2). Não existe lista livre de
processos: processo é pivô, nunca navegação (ADR D2). `/analise/processos` redireciona preservando a
query — havia 4 deep-links vivos para lá.

#### Visão 2 — o processo *(F4, 2026-08-25)*

`/analise/sessions?journey=<raiz canônica>`, servida por `AnaliseJourneysPage`. Fonte:
`GET /reports/journeys?root_session_id=` (o card) + `GET /reports/sessions?root_session_id=` (as
sessões-membro, **isento** da janela de período e do escopo de contato — pedir UM processo não é
listar).

**Duas classes de linha (ADR D4), e é isso que responde a pergunta do operador:**

| Classe | Quem é | Como aparece |
|---|---|---|
| **acesso do cliente** | `direction` = `inbound` ou `outbound` | protagonista, sempre visível |
| **etapa interna** | `direction` = `internal` | **dobrada** sob o acesso que a originou, com contador |
| **não classificada** | `direction` vazio | linha visível, `—` em destaque, nunca dobrada |

**O cabeçalho conta ACESSOS** (`2 acessos do cliente · 2 etapas internas`). Nunca um total somado, e
o toggle de internas é **visibilidade**: o cabeçalho não muda com ele. É essa separação por domínio —
não o toggle — que dissolve o *"cabeçalho diz 3, tabela mostra 4"* registrado no ADR D11.

**Duas lentes, um componente** (ADR D6). `Árvore` ordena por proveniência (indentação = quem criou
quem); `Cronologia` ordena por `opened_at`, com cada acesso como cabeçalho do seu grupo. A única
diferença é a ORDEM. Ao lado do horário absoluto vai o **offset desde a abertura do processo**
(`+7m54s`) — é o único lugar onde a **sobreposição** fica legível na árvore, que mostra como irmão o
acesso que rodou dentro da janela de outro.

Etapa interna sem acesso ancestral cai num grupo **órfão** com rótulo próprio: pendurá-la no
primeiro acesso afirmaria uma origem que o dado não tem.

Continuam: o rastro forense por sessão (drawer, atravessa fronteiras de processo), as arestas
`journey: new` como **link** (nunca expansão) e o painel de sinal N3 do processo.

**Fora, e não voltam:** colunas e filtros ANI/DNIS (permanentemente vazios nos dois canais existentes).
O seletor «Inbound / Outbound» **voltou na F4** — mas o que voltou não é o mesmo controle: o antigo
nunca virou parâmetro e devolvia a lista inteira; o novo tem predicado no backend e testemunha
negativa no gate.

#### Deep-link por `?session_id=` *(F4, 2026-08-25)*

`/analise/sessions?session_id=<id>` abre o **nível 2 daquela sessão**. A URL é a fonte única desse
nível (o mesmo desenho de `?journey=`), e quatro telas linkam para cá: `WorkItemsPage`,
`SchedulesMonitorPage`, `DeliveriesTab` e `ProcessosPage`. Até a F4 o parâmetro era **ignorado** — o
operador clicava em "ver a sessão" e caía na lista, sem sinal de que o pedido tinha sido descartado.

O **canal** decide a tela (`webhook` → trace de workflow; demais → segmentos) e nem sempre viaja no
link: quando falta, é resolvido por lookup (`?session_id=`, isento de escopo de contato). Adivinhar
renderizaria um workflow como conversa. O canal fica **amarrado ao id** (`{id, ch}`): navegar entre
sessões pelo botão VOLTAR do navegador não passa pelo clique, e um canal solto sobreviveria à troca.

**Este é o ÚNICO nível de sessão da plataforma.** A visão 2 não tem drill próprio: clicar numa
sessão-membro vai para `?journey=X&session_id=Y`, que cai aqui com o processo no breadcrumb
(`← Sessions › PRC-… · N › …id`, e o selo volta ao processo). Até 2026-08-25 havia dois, e a mesma
sessão interna aparecia como *Workflow trace* por um caminho e como transcrição **vazia** pelo
outro. `?journey=…&session=…` sobrevive como redirect.

Não encontrou: **tela própria**, nomeando as duas causas possíveis (não existe × fora do escopo de
pools do usuário), com botão de volta. Nunca a lista — uma listagem cheia é indistinguível de "nada
foi pedido".

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

> ⚠️ **Esta aba não está alcançável.** `AnaliseTab` era montada por `ContactsPage` e
> `AnaliseContatosPage`, ambas removidas na F4 — e já estava sem rota desde a F3.3. O componente
> continua no repositório porque **é uma feature, e se ela volta (como aba de `/analise/sessions`,
> ou não volta) é decisão do dono**, não consequência de uma limpeza. Registrado no `TODO.md`.

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
| `platform-ui` | `modules/contacts/` — SessionsPage + ListaTab (+ MonitorTab); `modules/analise/AnaliseJourneysPage` para a visão 2. `ContactsPage.tsx` e `AnaliseContatosPage.tsx` **removidas na F4**; `AnaliseTab` ficou sem consumidor (ver aba Análise) |

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
| **Processos (visão 2)** | Desde a F3 é o **nível 2 desta rota** (`/analise/sessions?journey=…`), alcançado pelo chip da linha — não é página nem item de menu. `/agent-flow/processos` é outra coisa: o KPI dashboard de instâncias de workflow, que permanece |

## Referências

- ADR: `docs/adr/adr-contact-segments.md`
- Guia: `docs/sections/conferencia-e-historico.md`
- Arc 11 (Console como superfície de orquestração): `docs/arcos/arc11-console-orchestration.md`
- Backend: `packages/analytics-api/`, `packages/channel-gateway/`
- Frontend: `packages/platform-ui/src/modules/contacts/`
