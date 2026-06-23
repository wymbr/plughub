# Customer Contact History — Histórico de Contatos do Cliente (spec / ADR)

> Estado: **spec / ADR** (parcialmente implementado — ver §2). Capacidade **transversal**: serve a
> **qualquer atendimento**, não a um módulo específico. Promovido a arco próprio a partir de
> `docs/arcos/customer-surveys.md` §20 (o briefing de retorno de survey foi o gatilho, mas o valor é geral).
> Relacionados: `docs/arcos/customer-surveys.md` (§7.3 `customer_key`, §19 briefing, §20),
> `docs/arcos/platform-ui.md` (Agent Assist), `docs/arcos/session-replayer.md` (persistência de stream),
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

GET /analytics/transcript/sessions/{session_id}?tenant_id
    → mensagens da sessão (por janela de segmento), conteúdo MASKED
```

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

---

## 5. Busca — design

**Sobre o quê buscar.** As transcrições já são persistidas para replay/auditoria:

- **Stream Persister → PostgreSQL** (`session-replayer.md`): o stream de cada sessão fechada vai para uma
  tabela durável (mensagens com `content` MASKED). É a fonte natural para **full-text** (índice de texto do
  Postgres — `tsvector`/`GIN` — ou `ILIKE` para um v1).
- **ClickHouse `session_timeline`** (Arc 5): timeline enriquecida por sessão/segmento — alternativa para
  filtros/agregação, menos indicada para full-text livre.

**Decisão (recomendada):** full-text sobre o **store persistido de mensagens** (Postgres `sessions_stream`),
filtrando por `customer_id` (join com `sessions` por `session_id`), devolvendo sessões + snippets; filtros
estruturados (data/canal/outcome/pool) aplicados na mesma query. Índice `GIN(tsvector)` para escala; `ILIKE`
aceitável no v1.

**Regra de ouro:** a busca indexa o **conteúdo MASKED** (o que está no stream). O original só é resolvido sob
autorização (§8) — a busca **não** indexa nem expõe `original_content`.

---

## 6. Superfície na UI

- **Agent Assist — `HistoricoTab`** (já existe): ganha **(a)** o drill para a transcrição (abrir o contato),
  e **(b)** uma **caixa de busca + filtros** no topo, chamando o endpoint do §4.2. Disponível em **qualquer
  atendimento** (a tab já está no painel direito).
- **Retorno de survey** (`customer-surveys.md` §19): o briefing reusa a mesma tab e **destaca o contato de
  origem** (`origin_session_id`) + o resultado do survey no topo.
- **Analytics (opcional, futuro)** — uma visão por cliente fora do atendimento ao vivo (página dedicada),
  útil a supervisão/CS; reusa os mesmos endpoints. Fora do escopo inicial.

ABAC/escopo: respeita `accessible_pools`/`supervised_agent_types` já aplicados na analytics-api; i18n en + pt-BR.

---

## 7. Identidade e cadastro futuro

A chave é `sessions.customer_id`, **resolvido na identificação do contato** (CRM/caller). Hoje pode ser
**parcial/por-canal** (um handle resolve um `customer_id`; o mesmo cliente em outro canal pode não unificar).
A **unificação cross-canal** (todos os handles → um `customer_id`) é o **cadastro dinâmico de cliente**
(arco à parte — `customer-surveys.md` §13). Quando ele entrar:

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
| **H1 — Drill lista → transcrição** | ligar `HistoricoTab` ao `GET /transcript/sessions/{id}` (painel/inline) + ACL/masking + (se exigido) `audit_access_log` | abrir um contato anterior da lista e ler a transcrição MASKED; LGPD respeitado |
| **H2 — Busca (backend)** | endpoint `GET /sessions/customer/{id}/search` (full-text sobre `sessions_stream` + filtros + snippets), tenant/cliente isolation, masking | `curl` com `q` retorna sessões + snippets do cliente |
| **H3 — Busca (UI)** | caixa de busca + filtros na `HistoricoTab` (data/canal/outcome/pool), clique no hit → drill (H1); i18n | buscar termo no atendimento e abrir o contato achado |
| **H4 — Destaque no briefing de retorno** | no contexto de `customer-surveys.md` §19: destacar `origin_session_id` + resultado do survey no topo da tab | briefing mostra o contato de origem em primeiro |
| **H5 (futuro)** | visão por cliente fora do atendimento (página analytics); índice `GIN(tsvector)` para escala | supervisão consulta histórico/busca de um cliente |

Ordem: drill (reuso) → busca backend → busca UI → integração com o briefing → escala/visão dedicada.

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
