# Customer Contact History — Histórico de Contatos do Cliente (spec / ADR)

> Estado: **spec / ADR** (parcialmente implementado — ver §2). Capacidade **transversal**: serve a
> **qualquer atendimento**, não a um módulo específico. Promovido a arco próprio a partir de
> `docs/arcos/customer-surveys.md` §20 (o briefing de retorno de survey foi o gatilho, mas o valor é geral).
> Relacionados: `docs/adr/adr-customer-360-two-surfaces.md` (**reconciliação Console × Analytics** — §6/§9),
> `docs/product/journey-provenance-tree-spec.md` (Journey T1–T6 — jornadas do cliente + contexto),
> `docs/arcos/customer-surveys.md` (§7.3 `customer_key`, §19 briefing, §20),
> `docs/arcos/platform-ui.md` (Agent Assist/Console), `docs/arcos/session-replayer.md` (persistência de stream),
> `docs/arcos/audit-lgpd.md` (ACL/masking de conteúdo), `docs/arcos/arc5-segments.md` (ClickHouse).

---

## 1. Visão

Quando o cliente está **identificado**, qualquer agente (humano ou IA) deve poder ver **a vida do cliente
com a marca**: a lista dos contatos anteriores, abrir a transcrição de qualquer um deles (ex.: o atendimento
que originou uma pesquisa), e **buscar** no histórico ("o cliente já reclamou de cobrança antes?"). É útil no
briefing de retorno de survey (`customer-surveys.md` §19), mas também em retenção, suporte recorrente,
escalação e qualquer cenário em que o contexto histórico muda a conversa.

A entidade é o **cliente** (`customer_id` = `customer_key` do `customer-surveys.md` §7.3), não a sessão.
Tudo rola para o cliente via a coluna `sessions.customer_id` já existente.

---

## 2. Estado atual (validado no código)

**Já existe (~60% da capacidade):**

- **Lista de contatos** — `GET /analytics/sessions/customer/{customer_id}?tenant_id&limit`
  (`analytics-api/sessions.py`, `_fetch_customer_history`): consulta ClickHouse
  `sessions WHERE customer_id = … ORDER BY opened_at DESC` → últimas N sessões fechadas
  (`opened_at`, `channel`, `duration_ms`, `outcome`, `close_reason`, `pool_id`, `session_id`). A tabela
  ClickHouse **já tem a coluna `customer_id`** — é o `customer_key`.
- **UI da lista** — hook `useCustomerHistory(customerId)` + `HistoricoTab` no Agent Assist (painel direito):
  renderiza cada contato (data, ícone de canal, duração, badge de outcome, close_reason); expandir mostra
  `pool_id`/`channel`/`closed_at`/`session_id`.
- **Transcrição por sessão** — `GET /analytics/transcript/sessions/{session_id}`
  (`analytics-api/transcript.py`, `_fetch_transcript`/`_fetch_messages` por janela de segmento) — já usada
  por replay/auditoria.

**Limitações do que existe:**

- A `HistoricoTab` **expande apenas para `session_id`** — **não abre a transcrição** (o endpoint existe, mas
  não está ligado à lista).
- **Não há busca** — o endpoint de lista devolve as últimas N por `opened_at`, sem termo nem filtros ricos.

---

## 3. O que falta (escopo deste arco)

1. **Drill lista → transcrição** — ligar a linha da `HistoricoTab` ao endpoint de transcrição existente
   (abrir a conversa do contato anterior, inline ou em painel), com **mascaramento/ACL LGPD** (§8). Resolve
   diretamente "ver o atendimento que originou a pesquisa" — é o `origin_session_id`, que já aparece na lista
   por `customer_id`. É majoritariamente **wiring** (o endpoint já existe).
2. **Busca no histórico do cliente** — capacidade nova: endpoint + UI para pesquisar **dentro** dos
   atendimentos do cliente (termo livre + filtros), devolvendo sessões e **trechos** (snippets). §5.

---

## 4. Contrato de endpoints

### 4.1 Existentes (reusar)

```
GET /analytics/sessions/customer/{customer_id}?tenant_id&limit=20
    → ContactHistoryEntry[] (opened_at, channel, duration_ms, outcome, close_reason, pool_id, session_id)

GET /analytics/v1/transcript/sessions/{session_id}?tenant_id&scope=contact
    → { session_id, scope, messages[], masked } — conteúdo MASKED
      (o router real é /v1/transcript; scope=contact devolve a sessão inteira,
       sem janela de segmento)
```

> **Nota de roteamento (validado 2026-07-02):** o prefixo real do router é `/v1/transcript`
> (não `/transcript`). O drill do platform-ui chama `/analytics/v1/transcript/sessions/{id}`;
> o proxy `/analytics/*` → analytics-api (com strip do prefixo) foi **adicionado ao platform-ui**
> (nginx no `Dockerfile` + `vite.config.ts`), espelhando o legado `agent-assist-ui`. Antes disso
> o platform-ui (:5174) não tinha rota `/analytics`, então tanto a lista quanto a transcrição
> eram inalcançáveis nesse app (degradavam para vazio) — o proxy conserta ambos.

### 4.2 Novo — busca

```
GET /analytics/sessions/customer/{customer_id}/search
    ?tenant_id
    &q=<termo>                       # texto livre (full-text/keyword sobre transcrições)
    &from&to                         # janela temporal (sobre session_at/opened_at)
    &channel&outcome&pool            # filtros estruturados (espelham a lista)
    &limit&offset
    → SearchHit[] {
        session_id, opened_at, channel, outcome, pool_id,
        snippet,            # trecho com o match (conteúdo MASKED)
        score               # relevância
      }
```

`customer_id` é sempre o escopo (tenant isolation + cliente). Os filtros estruturados reaproveitam as
colunas da lista; o `q` é o diferencial (busca textual).

### 4.3 Novo — jornadas do cliente (reuso do `/reports/journeys`)

> Decisão (ADR §D2): **NÃO** um endpoint dedicado. As "jornadas do cliente" são journeys (grupos por raiz
> canônica, union-find sobre `journey_aliases`) com **≥1 sessão-membro** daquele `customer_id`. Basta um
> **filtro `customer_id`** no `query_journeys_report`, espelhando o filtro `pool_id` já existente:

```
GET /reports/journeys?tenant_id&customer_id=<id>[&from&to&significant_only]
    → JourneyRow[] (journey_id, session_count, started_at, business_outcome, channels[], …)
      — mesmos campos da Vista Processos, recortados às journeys do cliente
```

```sql
-- filtro novo, espelha o padrão do pool_id:
s.session_id IN (SELECT session_id FROM {db}.sessions FINAL
                 WHERE tenant_id = {tenant_id:String} AND customer_id = {customer_id:String})
```

Console e Analytics leem o **mesmo endpoint**, variando só o recorte (`customer_id` no Console; janela/pool
no Analytics) — *uma verdade, duas lentes*. Cada `journey_id` (raiz canônica, prefixo `PRC-`) **linka** para
a Vista Processos/rastro T6.

### 4.4 Novo — 360 agregado (quality + survey por cliente) e cadastro manual

> Decisão (ADR §D3/§D4). Tudo por `customer_id`, **reuso com recorte** (sem store novo p/ o 360):

```
# 360 — quality (Arc 6): avaliações das sessões do cliente
GET /reports/evaluations?tenant_id&customer_id=<id>            # reuso c/ filtro customer_id
    → última nota, tendência, links por sessão (evaluation_finalized/results)

# 360 — survey: sinais de satisfação do cliente
GET /reports/... (session_signal por customer_id)             # NPS/CSAT/CES: últimos + histórico

# Cadastro manual (identity/, channel-gateway) — v1 = buscar + criar/atacar
GET  /identity/customers/search?tenant_id&q=<nome|ancora|customer_id>   # NOVO — busca manual
POST /identity/customers                                        # criar cadastro (reusa resolve_or_provision)
POST /identity/customers/{customer_id}/attach                   # vincular âncora do contato (attach_anchor)
POST /identity/customers/{customer_id}/attributes              # completar atributos (update_attributes)
```

O **360** reusa endpoints existentes com filtro `customer_id` (quality) + agrega `session_signal` (survey).
O **cadastro manual** reusa `resolve_or_provision`/`attach_anchor`/`update_attributes` (Resolvedor de
Identidade Fase A/B); o **único net-new de backend** é a **busca manual** (`/identity/customers/search`) e a
superfície REST/UI. **Merge de cadastros + `external_refs` (CRM) = Fase C** (fora daqui). Correção de vínculo
é ação de operador (ABAC + auditável).

---

## 5. Busca — design

**Sobre o quê buscar.** As transcrições já são persistidas para replay/auditoria:

- **Stream Persister → PostgreSQL** (`session-replayer.md`): o stream de cada sessão fechada vai para uma
  tabela durável (mensagens com `content` MASKED). É a fonte natural para **full-text** (índice de texto do
  Postgres — `tsvector`/`GIN` — ou `ILIKE` para um v1).
- **ClickHouse `session_timeline`** (Arc 5): timeline enriquecida por sessão/segmento — alternativa para
  filtros/agregação, menos indicada para full-text livre.

**Decisão implementada (v1, 2026-07-02 — diverge da recomendação original):** busca sobre **ClickHouse
`messages` JOIN `sessions FINAL`** na **analytics-api**, não sobre o Postgres `session_stream_events`.
Motivos: (a) **LGPD por construção** — `analytics.messages` **não tem** coluna `original_content` (o
Postgres `session_stream_events` **tem** `original_content` ao lado do texto mascarado → superfície de risco
desnecessária); (b) `sessions.customer_id` dá o **escopo por cliente** sem join cross-store; (c) fica
**colocado** com a lista (H1) e a transcrição, e já **alcançável** pelo proxy `/sessions`/`/analytics`.
Match = **substring case-insensitive** (`positionCaseInsensitiveUTF8`) — sem stemming, suficiente p/ o v1;
filtros estruturados (from/to/channel/outcome/pool) vêm de `sessions`; resultado = **1 hit por sessão** com
snippet mascarado + `score` (nº de mensagens que casaram). O Postgres `sessions_stream` + `GIN(tsvector)`
(full-text tokenizado real) fica reservado p/ **escala futura (H5)**.

**Regra de ouro:** a busca indexa e devolve **apenas conteúdo MASKED** (`messages.content`). O
`original_content` **não existe** nessa tabela — a busca nunca o lê nem o expõe; sem `audit_access_log`
(mesma postura do H1).

**Endpoint (implementado):** `GET /sessions/customer/{customer_id}/search` na analytics-api (`sessions.py`),
alcançável em `/analytics/sessions/customer/{id}/search`. Query-params: `q` (obrigatório), `from`/`to`
(ISO, via `parseDateTimeBestEffort`), `channel`/`outcome`/`pool`, `limit`/`offset` (paginam **sessões**).

---

## 6. Superfície na UI — Cliente 360 em duas lentes (Console × Analytics)

> **Reconciliação (decisão 2026-07-15 — `docs/adr/adr-customer-360-two-surfaces.md`).** O histórico entra
> em **dois pontos**, com propósitos distintos, sobre **os mesmos dados** (`customer_id` → sessões +
> jornadas): o **Console** (ao vivo, cockpit enxuto) e o **Analytics** (retrospectivo, explorador). O
> gatilho foi retomar as fases H3–H5 com o **Journey fechado** (proveniência/`root_session_id`, T1–T6): o
> Console **já teve** jornadas do cliente (seção "Processos em aberto" na `HistoricoTab`, removida no Arc 19
> com a **entidade** Journey) e agora as reconecta **sobre o modelo de proveniência**, não a entidade.

**Console (Agent Assist) — quatro abas** (mapa fechado no ADR §D1; correção do rascunho, que punha jornadas na aba Cliente):

- **Contexto** *(existe — papel afirmado)*: dados da **sessão corrente** herdados de segments anteriores (e,
  com o Journey, do namespace `journey.*` cross-sessão) — o snapshot do **ContextStore** que a `ContextoTab`
  já mostra (`caller/account/service/journey/session/agent/history` + insights históricos). **O que persiste é
  configurado por pool** (campos de persistência de contexto / `insight.historico.*`); a aba **reflete** essa
  config, não a define. Estruturalmente inalterada.
- **Histórico** *(existe — reatribuído)*: **jornadas em aberto** (re-introduz o "Processos em aberto" que o
  Arc 19 removeu **daqui** — backend §4.3, recorte `open_count>0`) + **contatos anteriores** (H1, drill→
  transcrição MASKED) + **busca** (H3, §4.2). As jornadas vivem **só aqui**; a aba Cliente **linka**, não duplica.
- **Cliente** *(NOVA)*: a **ficha** do cliente — **cadastro manual** (buscar/criar/atacar âncora quando a
  identificação automática falha ou erra — ver §7) + **360 agregado** (§4.4): resumo/contagem de contatos e
  jornadas (linka Histórico), **quality** (avaliações Arc 6 das sessões do cliente), **survey** (sinais
  `session_signal` NPS/CSAT/CES), identidade/atributos.
- **Ações** — inalterada.

A separação Histórico × Cliente é semântica: **linha do tempo** (o que aconteceu) × **ficha** (quem é).

**Analytics (`/analise`) — retrospectivo/supervisório:**

- **Vista Processos** (journey→sessions→segments + **rastro T6**) — o drill completo de um processo.
- **Sessions** — a lista de sessões.
- **Visão por cliente (H5, futuro)** — página dedicada fora do atendimento ao vivo; reusa os mesmos endpoints.

**H4 — contexto de jornada:** o **generalizado** (raiz/origem da sessão atual, Journey-powered) entra agora
(Contexto/Histórico). O **survey-específico** (origem + resultado no topo do **briefing de retorno**,
`customer-surveys.md` §19) fica **bloqueado** — a superfície de briefing (inbox pull + `on_human_start`) não
está construída.

ABAC/escopo: respeita `accessible_pools`/`supervised_agent_types` já aplicados na analytics-api; i18n en + pt-BR.

---

## 7. Identidade e cadastro

A chave é `sessions.customer_id`, **resolvido na identificação do contato** (CRM/caller). Hoje pode ser
**parcial/por-canal** (um handle resolve um `customer_id`; o mesmo cliente em outro canal pode não unificar),
e a identificação automática pode **falhar** (sem `customer_id`) ou **errar** (vincular o cliente errado).

**Recepção automática — JÁ EXISTE (as-built).** O Console recebe a identidade sem código novo: o
`AgentAssistPage` deriva o `customerId` de `supervisorState.customer_context.context_snapshot["caller.customer_id"].value`
(a tag `caller.customer_id` do **ContextStore**, escrita pelo fluxo do Resolvedor de Identidade no intake —
Fase A/B/Slice 4), com **fallback ao `contactId` efêmero** quando não resolveu. Consequências para o desenho:

- o `customerId` **já flui** para o `RightPanel` → o 360 (§4.4) e o histórico/busca **já têm a chave** quando
  a identificação automática funciona; nada a construir aí;
- o **caso de falha** que o cadastro manual ataca é detectável na UI: `customerId === contactId` ⇒ *"não
  identificado"* ⇒ a aba Cliente oferece buscar/criar/vincular;
- o **write-back já tem trilho**: a `ContextoTab` grava tags via `POST /api/inject-context/:sessionId`
  (`ManualTagForm`). "Vincular/corrigir" (§D3) grava `caller.customer_id` por esse caminho (+ os endpoints
  `identity/` p/ o vínculo durável); o próximo poll do `supervisorState` reflete e o histórico/360
  **re-chaveiam sozinhos**. Ou seja, o gap do cadastro manual é a **correção/busca**, não a recepção.

**Dois bugs de plataforma achados ao validar (2026-07-15) — sem eles a identificação NÃO chega ao console:**

1. **`useSupervisorState` não mandava `Authorization`** → o `/api/supervisor_state/:id` (mcp-server,
   `requireJwtRole`) respondia **401** → `customer_context`/`context_snapshot` (e sentiment/insights)
   **nunca chegavam** ao console (falha silenciosa: `if (res.ok)`). Corrigido: o hook anexa
   `Bearer <getAccessToken()>`. Bug pré-existente, invisível até precisarmos do `customer_context`.
2. **Masking escondia `caller.customer_id` do operador.** A *namespace gate* (`applyContextMaskingDynamic`)
   só deixa o operador ver os namespaces do pool (default `["service","session"]`) — **`caller` fica de fora**,
   e a gate roda **antes** das regras de masking. Como `default_unmatched_operator="plain"`, abrir o
   namespace inteiro exporia PII (`caller.cpf/nome`) em claro. **Decisão (C1/H4):** o `customer_id` é um id
   **interno** (`cus_…`), não PII, e é **pré-requisito** de identificação/histórico/360. **Corrigido tornando
   a política config-driven** (crítica do usuário — nada fixo em código):
   - **`context_visibility.operator_allow_tags`** (campo NOVO por pool, editável em Config › Resources ›
     Pools): tags exatas que o operador vê **plain**, bypassando gate+masking. Default de plataforma
     `["caller.customer_id"]`. A PII de `caller.*` **não** entra aqui → segue gated+mascarada.
   - **`supervisor_roles`** (campo NOVO no masking config, editável na tela de Masking): *quem* é tratado como
     supervisor (bypassa a gate, vê PII plain) deixou de ser a lista hardcoded `["supervisor","admin",…]` no
     código — agora vem do config. Default preserva o comportamento anterior.
   Contrato do C1/H4: *o operador vê a identidade do cliente (customer_id), não a PII* — e **tudo é config,
   não código**. (Havia também uma regra exata `caller.customer_id → operator → plain` semeada no masking,
   redundante com o allow_tags-plain mas correta p/ pools que colocam `caller` nos namespaces.)

**Cadastro manual (v1, na aba `Cliente` — ADR §D3).** O operador tem um caminho manual quando o automático
não resolve: **buscar** o cadastro (nome/âncora/`customer_id`), **criar** um novo, ou **vincular/corrigir** o
contato atual ao cliente certo (`attach_anchor` + `update_attributes`). Reusa a Fase A/B do Resolvedor de
Identidade; net-new = a **busca manual** (`GET /identity/customers/search`, §4.4). Correção de vínculo é ação
de operador — **ABAC + auditável**. **Merge de cadastros + `external_refs` (CRM) = Fase C** (fora do v1).

**Unificação cross-canal automática** (todos os handles → um `customer_id`) segue no **cadastro dinâmico**
(Fase C / `customer-surveys.md` §13). Quando entrar:

- a lista e a busca passam a **abranger todos os canais** do cliente **sem mudança de contrato** (mesma
  coluna `customer_id`, agora resolvida pelo cadastro);
- o histórico vira insumo do **Health Score** e do cadastro (hábitos/preferências derivados dos contatos).

Até lá, o histórico é por `customer_id` resolvido — já entrega valor por canal e por identidade conhecida.

---

## 8. LGPD

Transcrição e busca expõem **conteúdo de conversa** → **acesso controlado**:

- Conteúdo **MASKED por padrão** (mesma postura do detalhe da bancada e do `customer-surveys.md` §10b);
  `original_content` só por **papel autorizado**, com **trilha de auditoria** (coerente com o módulo
  **Audit LGPD**).
- A **busca** indexa e devolve apenas conteúdo MASKED (snippets MASKED); nunca o original.
- Acesso à transcrição/busca registra linha em `audit_access_log` quando o papel exige (alinhar com
  `audit-lgpd.md`).

---

## 9. Fases

| Fase | Escopo | Entrega validável |
|---|---|---|
| **H1 — Drill lista → transcrição** ✅ | `HistoricoTab` (platform-ui) liga ao `GET /v1/transcript/sessions/{id}?scope=contact` via `useSessionTranscript`; expandir a linha carrega a transcrição MASKED inline (lazy). Proxy `/analytics/*` adicionado ao platform-ui (nginx+vite). Masking por construção (`analytics.messages` sem `original_content`) → sem exposição de original, sem `audit_access_log`. | ✅ abrir um contato anterior e ler a transcrição MASKED; LGPD respeitado |
| **H2 — Busca (backend)** ✅ | endpoint `GET /sessions/customer/{id}/search` — **decisão v1: ClickHouse `messages` JOIN `sessions`** (não `sessions_stream`), substring `positionCaseInsensitiveUTF8`, 1 hit/sessão (ordem `opened_at` DESC) + snippet mascarado + score, filtros from/to/channel/outcome/pool, tenant/cliente isolation, masked-by-construction | ✅ 13 unit tests + smoke `test_h2_customer_history_search.sh` (score/filtros/case-insensitive/proxy) |
| **H3 — Busca (UI)** | caixa de busca + filtros na `HistoricoTab` (data/canal/outcome/pool), clique no hit → drill (H1); i18n. Self-contained (backend H2 pronto). | buscar termo no atendimento e abrir o contato achado |
| **HJ — Jornadas em aberto na `HistoricoTab`** *(re-introdução, ADR §D1/§D2)* | seção "jornadas em aberto" volta ao Histórico (removida no Arc 19), agora Journey-powered. Backend = **filtro `customer_id`+`open` no `/reports/journeys`** (§4.3, reuso). Cada jornada = chip → Vista Processos/rastro. | no Histórico: ver os processos abertos do cliente e pular pro rastro |
| **H4-geral — contexto de jornada** | destacar `root_session_id`/`origin_session_id` (raiz + origem) da sessão atual (Contexto/Histórico). Journey-powered (dado real pós-T1). | ver a que processo a sessão atual pertence e o que a originou |
| **C1 — Aba `Cliente` (360)** *(nova, ADR §D1/§D3/§D4)* | **ficha** do cliente: **C1a cadastro manual** (buscar/criar/atacar âncora — §7, net-new = `GET /identity/customers/search`) + **C1b 360 agregado** (resumo de contatos/jornadas c/ link ao Histórico + **quality** Arc 6 + **survey** `session_signal` + atributos — §4.4). i18n en+pt-BR. | corrigir identificação errada; ver quality+survey do cliente num lugar |
| **H4-survey (bloqueado)** | destacar origem + resultado do survey no **briefing de retorno** (`customer-surveys.md` §19) | *depende da superfície de briefing de retorno — não construída* |
| **H5 (futuro)** | visão por cliente fora do atendimento (página analytics, reusa §4.3/§4.4) + índice `GIN(tsvector)` para escala | supervisão consulta histórico/jornadas/quality/survey de um cliente |

Ordem: **H3** (self-contained) → **HJ + H4-geral** (jornadas + contexto, Journey-powered, andam juntos) →
**C1** (aba Cliente; fatiável em **C1a** cadastro e **C1b** 360) → **H5** (escala/visão dedicada). O
H4-survey aguarda o briefing de retorno. **Nota de escopo (ADR §D3):** merge de cadastros + `external_refs`
(CRM) ficam no Resolvedor de Identidade **Fase C**, fora deste arco.

---

## 10. Pendências / dependências

1. **Drill** — wiring `HistoricoTab` → endpoint de transcrição existente + ACL/masking LGPD.
2. **Busca backend** — novo endpoint sobre `sessions_stream` (Postgres) com full-text + filtros + snippets,
   escopo `customer_id`, masking.
3. **Busca UI** — caixa + filtros na `HistoricoTab`; i18n en + pt-BR.
4. **LGPD** — masking por padrão; `original_content` só autorizado + `audit_access_log` (alinhar `audit-lgpd.md`).
5. **Índice de texto** — `GIN(tsvector)` em `sessions_stream` para escala (v1 pode usar `ILIKE`).
6. **Cross-canal** — depende do **cadastro dinâmico de cliente** (unifica `customer_id`); este arco fica
   forward-compatível sem mudança de contrato.
