# ADR — Cliente 360: histórico, jornadas, contexto, cadastro e quality/survey em duas superfícies

> **Status:** Proposto (2026-07-15; revisado no mesmo dia com os ajustes de colocação de abas + cadastro manual + agregação quality/survey).
> **Contexto de origem:** ao retomar `docs/arcos/customer-contact-history.md` (fases H3–H5) com o
> **Journey fechado** (proveniência/`root_session_id`, J1–J5 + árvore T1–T6), ficou claro que o
> histórico do cliente entra em **dois pontos** do produto — Analytics (Sessions + Vista Processos) e
> **Console** (dados do cliente no atendimento identificado) — e que faltava um contrato que
> reconciliasse os dois sem duplicar modelo.
> **Relacionados:** `docs/arcos/customer-contact-history.md`, `docs/arcos/customer-surveys.md` (§7.3
> `customer_key`, §19 briefing), `docs/arcos/platform-ui.md` (Agent Assist/Console),
> `docs/product/journey-provenance-tree-spec.md` (T1–T6), `docs/product/identity-resolver-fase-a-plano.md`
> + `docs/adr/adr-identity-channel-possession.md` (Resolvedor de Identidade), `docs/arcos/arc6-evaluation.md`
> (quality), `docs/arcos/customer-surveys.md` (survey/`session_signal`), `docs/arcos/arc11-console-orchestration.md`.

---

## 1. Contexto

A entidade é o **cliente** (`sessions.customer_id` = `customer_key`), não a sessão. Em torno dele giram
quatro coisas que hoje vivem espalhadas: **contexto** da sessão viva, **contatos** passados, **jornadas**
(processos de longa duração) e o **cadastro** (identidade) — além de sinais de **quality** e **survey**.

**O achado que motiva este ADR:** a `HistoricoTab` do Console carrega a nota *"(Arc 19 Fase F) 'Processos
em aberto' (Open Journeys) section removed — Journey entity eliminated."* O Console **já teve** jornadas do
cliente; a seção foi removida quando a **entidade** Journey do Arc 10 morreu. O Journey voltou — mas **como
lente sobre proveniência, não como entidade** — então a reconexão é desenhada sobre o modelo novo.

**Uma verdade, duas lentes:**

| | **Console** (Agent Assist) | **Analytics** (`/analise`) |
|---|---|---|
| Quando | **ao vivo**, atendimento identificado | **retrospectivo**, supervisão/CS |
| Pergunta | *"quem é este cliente, e o que já rolou?"* | *"como vai este processo / este cliente?"* |
| Forma | cockpit **enxuto**, acionável | **explorador** com drill completo |
| Jornada | jornadas em aberto (Histórico) + contexto atual (Contexto); chips linkam pro Analytics | Vista Processos (journey→sessions→segments) + rastro T6 |

---

## 2. Decisões

### D1 — Mapa das quatro abas do Console

O painel direito (hoje **Ações / Contexto / Histórico**) ganha uma aba **Cliente**, e os papéis se
redistribuem (correção do rascunho inicial, que punha as jornadas na aba Cliente):

- **Contexto** *(existente — papel afirmado)* — dados da **sessão corrente** herdados de segments
  anteriores (e, com o Journey, do namespace `journey.*` cross-sessão): o snapshot do **ContextStore** que a
  `ContextoTab` já mostra (namespaces `caller/account/service/journey/session/agent/history` + insights
  históricos). **O que persiste é configurado por pool** (campos de persistência de contexto /
  `insight.historico.*`) — a aba **reflete** essa config, não a define. Estruturalmente nada muda.
- **Histórico** *(existente — reatribuído)* — a **atividade** do cliente: **jornadas em aberto**
  (re-introduz a seção "Processos em aberto" que o Arc 19 removeu **daqui**) + **contatos anteriores** (H1,
  drill→transcrição MASKED) + **busca** (H3). As jornadas vivem **só aqui** (a aba Cliente linka, não duplica).
- **Cliente** *(nova)* — o **registro** do cliente: **cadastro manual** (D3) + **360 agregado** (D4). É o
  *"quem é este cliente, em tudo"*: identidade + rollup dos dados que não cabem nas outras abas (quality,
  survey), com **resumo/contagem** de contatos e jornadas que **linka** para o Histórico.
- **Ações** — inalterada.

**Por que a aba Cliente e não empilhar tudo na `HistoricoTab`:** o Histórico é a **linha do tempo** do
cliente (eventos); a aba Cliente é a **ficha** (a entidade: identidade + agregados). Misturar as duas mistura
*"o que aconteceu"* com *"quem é"*. Custo: novo `ClienteTab` + entrada no `ActiveTab` + tab bar + i18n.

### D2 — "Jornadas do cliente" = filtro `customer_id` no `/reports/journeys` (reuso)

A lista de jornadas de um cliente é derivável: journeys (grupos por **raiz canônica**, union-find sobre
`journey_aliases`) com **≥1 sessão-membro** daquele `customer_id`. Implementação = **filtro `customer_id`**
no `query_journeys_report`, espelhando o filtro `pool_id` existente; o recorte **"em aberto"** (Histórico) =
`open_count > 0`. **Sem entidade nova, sem endpoint dedicado** (a alternativa duplicaria a agregação de
journey). Console e Analytics leem o **mesmo endpoint**, variando só o recorte — a reconciliação que este ADR
busca. Cada `journey_id` (raiz canônica, prefixo `PRC-`) **linka** para a Vista Processos/rastro T6.

### D3 — Cadastro manual do cliente (na aba Cliente), v1 = buscar + criar/atacar âncora

Quando a identificação automática **falha** (sem `customer_id`) ou **erra** (vinculou o cliente errado), o
operador precisa de um caminho manual, **dentro da aba Cliente**:

- **buscar** por âncora (telefone/email/CPF) ou por nome/`customer_id`, sobre o store `identity` (PG
  `identity.customers`/`secondary_keys`);
- **criar** um cadastro novo quando não existe;
- **vincular/corrigir** — atacar a âncora do contato atual ao cliente escolhido (`attach_anchor`) e completar
  atributos (`update_attributes`).

**v1 reusa a Fase A/B do Resolvedor de Identidade** (`resolve_or_provision`, `attach_anchor`,
`update_attributes`, `_pg_resolve` — já prontos); **falta o endpoint de busca manual** (por âncora/nome) e a
superfície REST/UI. **Merge de dois cadastros e `external_refs` (CRM) ficam para a Fase C** do Resolvedor —
**fora** desta proposta. Classes de verificação/posse (OTP) seguem as regras existentes; correção manual de
vínculo é ação de operador (ABAC), auditável.

**As-built que este D3 aproveita** (ver `customer-contact-history.md` §7): a **recepção automática já existe**
— o Console lê `caller.customer_id` do ContextStore (`supervisorState.customer_context.context_snapshot`),
resolvido pelo intake, com fallback ao `contactId` efêmero. Logo (a) o `customerId` já flui ao painel (o 360 e
o histórico já têm a chave quando o automático funciona); (b) a falha é detectável (`customerId === contactId`
⇒ não identificado); (c) o **write-back reusa `POST /api/inject-context/:sessionId`** (o mesmo trilho do
`ManualTagForm` da `ContextoTab`) — corrigir o vínculo grava `caller.customer_id` e o próximo poll re-chaveia
histórico/360. O gap é a **correção/busca**, não a recepção.

### D4 — 360 agregado: integrar todos os dados do cliente (incl. quality e survey)

A aba Cliente **integra** o que hoje está espalhado, tudo recortado por `customer_id`:

- **contatos** (resumo/contagem → linka Histórico) e **jornadas** (abertas/total → linka Histórico);
- **quality** — avaliações das sessões do cliente (`evaluation_finalized`/results, Arc 6): última nota,
  tendência, links;
- **survey** — sinais de satisfação por cliente (`session_signal` NPS/CSAT/CES): últimos valores + histórico;
- **identidade/atributos** — de `caller.*` / store `identity`.

Backend = **reuso com recorte por `customer_id`** (avaliações pelas sessões do cliente; `session_signal` por
cliente), sem store novo. Materializa *"o cliente é a entidade; tudo rola por `customer_id`"*.

---

## 3. Invariantes

- **A entidade é o cliente, não a sessão.** Tudo rola por `sessions.customer_id`; nenhuma tabela nova.
- **Nada de entidade Journey** (Arc 10). Journey segue a **componente conexa** pela raiz canônica; o Console a
  **exibe e linka** (Histórico), nunca a materializa.
- **O Console percorre por link, não renderiza a árvore.** Vista Processos/rastro T6 (Analytics) é o drill;
  o Console mostra listas/chips + contexto e **navega** para lá (decisão do T6 §6).
- **Cadastro manual não reabre a Fase C.** v1 = buscar + criar + atacar âncora (reuso). Merge/`external_refs`
  permanecem no Resolvedor de Identidade Fase C; correção manual de vínculo é operação de operador **auditável**.
- **Posse ≠ identidade-de-registro.** O cadastro manual atribui/corrige o `customer_id` **nativo** (posse de
  canal / conhecimento do operador); identidade-de-registro/KYC segue delegada à retaguarda (princípio 7 do
  Resolvedor de Identidade), inalterada.
- **LGPD por construção.** Transcrição/busca MASKED-by-construction (`analytics.messages` sem
  `original_content`); a aba Cliente e a agregação quality/survey não abrem exceção.
- **ABAC preservado.** `accessible_pools`/`supervised_agent_types` já aplicados na analytics-api valem para o
  filtro `customer_id` e para os agregados quality/survey.

---

## 4. Consequências

**Positivas.** Reconecta a lente de jornada ao Console (o que o Arc 19 removeu), sobre o modelo correto;
uma única superfície de backend para journeys; o operador ganha um caminho para **corrigir identificação
errada** (fonte recorrente de contexto trocado); o 360 junta quality/survey ao atendimento ao vivo pela
primeira vez; Console↔Analytics cross-linkados fecham o ciclo com o rastro T6.

**Custos / riscos.** (a) Nova aba + novo endpoint de busca de cadastro = trabalho de UI e backend (i18n
en+pt-BR). (b) O `customer_id` de contatos ao vivo pode ainda não unificar cross-canal (depende do cadastro
dinâmico / Fase C); a busca manual **mitiga** (o operador vincula na mão), mas a unificação automática fica
para depois. (c) `/reports/journeys?customer_id=` carrega o union-find por request (alias table pequena;
materializar se medir exigir). (d) Correção manual de vínculo é poderosa → exige ABAC + trilha de auditoria.

**Fora de escopo (dependências não-construídas / adiadas).**
- **Briefing de retorno de survey** (`customer-surveys` §19: inbox pull + `on_human_start`) — o H4
  *survey-específico* (origem + resultado no topo do briefing) fica **bloqueado** nele; o H4 **generalizado**
  (contexto de jornada) entra agora via a linha do Journey.
- **Merge de cadastros + `external_refs` (CRM)** — Resolvedor de Identidade **Fase C**.
- **Unificação cross-canal automática do `customer_id`** — cadastro dinâmico (Fase C / `customer-surveys` §13).

---

## 5. Alternativas consideradas

- **Enriquecer a `HistoricoTab`** com tudo (em vez de aba Cliente) — rejeitado: mistura linha-do-tempo com
  ficha do cliente (§D1).
- **Jornadas nas duas abas** (Histórico + Cliente) — rejeitado: duplica listas a manter coerentes; Cliente
  **linka**, não duplica (§D1/§D2).
- **Seletor de cliente global no topo do Console** (em vez do cadastro na aba Cliente) — considerado; ficou o
  cadastro **dentro da aba Cliente** (decisão do usuário). O re-vínculo da sessão continua possível pela aba.
- **Puxar merge/`external_refs` já nesta proposta** — rejeitado por escopo: v1 é buscar+criar+atacar; o resto
  é Fase C (§D3).
- **Endpoint dedicado `/sessions/customer/{id}/journeys`** — rejeitado por duplicar a agregação (§D2).
- **Ressuscitar a entidade Journey** — rejeitado: reabre o que o Arc 19 removeu; o gatilho para reconsiderar
  entidade segue o do `journey-provenance-tree-spec.md` §7 — não é o caso.

---

## 6. Fases (detalhe em `customer-contact-history.md` §9)

| Fase | Entrega |
|---|---|
| **H3** | Busca na UI (caixa + filtros) na `HistoricoTab`; hit → drill (H1). Backend H2 pronto. Self-contained. |
| **C1** | Aba **Cliente** no Console: **cadastro manual** (buscar/criar/atacar — D3) + **360** (contatos/jornadas resumo+link, **quality**, **survey**, atributos — D4). |
| **HJ** | Jornadas em aberto na `HistoricoTab` (re-introdução) — backend = filtro `customer_id`+`open` no `/reports/journeys` (D2). |
| **H4-geral** | Contexto de jornada (raiz/origem) da sessão atual — no Contexto/Histórico, Journey-powered. *(H4-survey: bloqueado no briefing de retorno.)* |
| **H5** | Visão por cliente no Analytics (fora do atendimento) + índice `GIN(tsvector)` para escala. |

Ordem sugerida: **H3** (self-contained) → **HJ + H4-geral** (jornadas + contexto, ambos Journey-powered) →
**C1** (aba Cliente: cadastro + 360) → **H5** (escala/visão dedicada). C1 é o maior (cadastro + agregação
quality/survey) e pode fatiar: C1a cadastro manual, C1b agregação 360.
