# Resolvedor de Identidade — Nível (b) · Especificação

> **Contexto:** parte do conceito *Business in Any Media*. Implementa o item 4.2 do `business-in-any-media-arquitetura-alvo.md` — o **resolvedor identidade ↔ fluxo de negócio pendente**, que renasce, no lugar arquitetural correto (nível b), do que a entidade Journey fazia antes de ser eliminada no Arc 19 Fase F.
> **Objetivo:** quando um cliente reaparece — possivelmente em **outro canal** — o inbound retoma o fluxo negocial suspenso dele, em vez de iniciar um novo. "Nunca mais perca um negócio por causa de canal."
> **Decisões travadas:** cadastro de cliente com **`customer_id` nativo canônico**; ids de sistemas externos guardados **dentro** do cadastro como `external_refs`; **duas buscas** (resolver identidade → localizar pendências); registro de pendência no **`delegate()`**, não no `suspend` de sinal.
> **Status:** especificação (não implementado). Diagrama de sequência cross-canal: `identity-resolver-sequencia.mermaid`.
> **Data:** Junho 2026.

---

## 1. Problema e escopo

Hoje a continuidade existe **dentro de um canal** (`channel:{ch}:{handle}:session`, TTL 24h) e por **token explícito** (`{tenant}:resume_tokens`, usado em callbacks de terceiros via `POST /v1/channels/webhook/resume/{token}`). Não existe continuidade **cross-canal iniciada pelo cliente**: quem deixou um fluxo no WhatsApp e volta pelo webchat hoje abre uma sessão nova — o negócio "se perde no canal". Esse era o papel do `inbound_journey_resume` + lookup por `customer_id`, **removidos** no Arc 19 Fase F junto com a entidade Journey.

**Escopo:** o resolvedor vive no **nível (b)** (fluxo de acesso aos canais). Ele mantém um **cadastro de identidade** (Lookup 1) e um índice **`customer_id` → pendências** (Lookup 2); no inbound de qualquer canal, resolve quem é o interlocutor e localiza fluxo pendente antes de criar sessão nova; retoma o fluxo (a) — que é channel-abstract e **não sabe** que mudou de canal.

**Fora de escopo:** a lógica de negócio (a), o render por canal (c), a negociação de mídia (Arc 16, serviço consumido por b), o resume por token explícito (já existe), e — princípio firme — **a autenticação/verificação de identidade**, que é **sempre** do sistema do contratante (§2). A plataforma captura e repassa; nunca decide nem é autoridade de identidade.

## 1.1 Ponto de partida — o que JÁ existe (prior art no código atual)

O resolvedor **não é greenfield**. Há um mecanismo funcional, no nível de sessão (não de Journey), que sobreviveu ao Arc 19 Fase F:

- **`pending_workflow_get(contact_identifier, tenant_id)`** (`mcp-server-plughub/src/tools/workflow.ts`) → consulta `GET /v1/channels/webhook/pending/{contact_identifier}` no channel-gateway e retorna `{ found, resume_token, context }`. Lookup **O(1)** sobre `{tenant}:pending_workflow:{contact_id}` (string, contact_id em claro), escrita pelo channel-gateway quando o `delegate()` cria a sessão-filho de I/O (Session C), validada contra os `resume_tokens`.
- **`workflow_resume(resume_token, decision, payload)`** → executa a retomada via `POST /v1/channels/webhook/resume/{token}`.
- **Padrão de uso já estabelecido:** um agente de intake coleta o identificador, chama `pending_workflow_get` e, se `found`, oferece um menu para continuar.

Já existe também o anchor durável de cliente: **`{t}:insight:h:{customer_id}:{item_id}`** — memória de longo prazo *"indexada por `customer_id`, persiste entre contatos"*. Ou seja, **o `customer_id` já é a identidade cross-contato e channel-agnostic da plataforma**; as pendências devem pendurar nele, no mesmo grão.

> Os apagados no Arc 19 Fase F (`journey_list_suspended`, `journey_check_pending`, `journey_resume`) eram a versão atrelada à **entidade Journey**. `tools/journey.ts` é hoje uma lápide. A capacidade migrou para `pending_workflow_get` + `workflow_resume`.

**Logo, esta spec é a generalização desse mecanismo, não uma reconstrução.** O delta para o "business in any media":

| Hoje (`pending_workflow`) | Alvo (cadastro + resolvedor) |
|---|---|
| Pendência keyed pelo handle (`contact_identifier`) em **claro** | Pendência keyed por **`customer_id` nativo** (como `insight:h`) |
| Sem cadastro de cliente | **Customer master** nativo + `secondary_keys` (hasheadas) + `external_refs` |
| Uma âncora, um pendente | N chaves secundárias resolvem → `customer_id`; N pendências por cliente |
| Identidade não persiste / não se funde | Identidade progressiva e **merge** como operação de cadastro |
| Sem eventos/métricas cross-canal | `session_resumed` com `resume_origin` + relatórios |

## 1.2 Por que o `delegate()` é o hook certo (premissa validada)

No modelo de 3 camadas, (a) é channel-abstract e **proibido por perfil** de fazer I/O com cliente (`menu/notify` são de (b)/(c)). Logo, sempre que (a) precisa que **o cliente** aja, ele **delega** via `task`/`collect`. Há duas naturezas de pausa, e só uma tem o cliente como aguardado:

| Pausa em (a) | Parte aguardada | Quem retoma | Indexa pendência? |
|---|---|---|---|
| **Delegação de interação** (`task`/`collect`) | **o cliente** | o cliente, respondendo | **Sim** — onde o `delegate()` já grava. Retorno = **resume**. |
| **`suspend` de sinal** (`webhook/timer/approval`) | máquina / token / relógio | callback / timer / token | **Não** — retomado por token. |

Registrar um `suspend` de webhook como `customer_resumable` confundiria: ali quem destrava é o callback de pagamento, não o cliente. O **retorno espontâneo** do cliente durante um `suspend` de sinal **não é resume** — é **status/attach** ("seu pedido está aguardando confirmação de pagamento; quer que eu te avise por aqui?"), tratado à parte (fase C).

---

## 2. Princípios

1. **(b) é o único dono** do cadastro e do índice. (a) só declara política; nunca conhece canal, handle nem `customer_id` concreto. (c) nunca conhece negócio nem identidade de longo prazo.
2. **`customer_id` nativo é canônico e imutável.** Ids de sistemas externos vivem **dentro** do cadastro (`external_refs`), nunca como chave. Resiliente a múltiplos sistemas de registro e a troca de CRM.
3. **PII nunca em claro no índice de resolução.** Chaves secundárias normalizadas e **hasheadas com salt por tenant** (coerente com `channel:email:{contact_email_hash}`).
4. **Duas buscas, O(1) cada, no caminho frio.** Só rodam quando a continuidade intra-canal falha. Lookup 1 (quem é) → Lookup 2 (o que está pendente).
5. **Resume reusa o caminho existente.** Retomar = `status: active` + routing aloca nova instância do pool + novo `segment_id` no **mesmo `session_id`** (invariante Arc 19).
6. **Cliente no controle.** Retomada proativa cross-canal é gated por política/consentimento; default `offer`, não `auto`.
7. **A plataforma NÃO é autoridade de identidade.** Autenticação/verificação (inclusive biometria) é **sempre** do sistema do contratante. A plataforma **captura e repassa** os dados à retaguarda (via MCP de domínio) e guarda só o **veredito** + as chaves resultantes — nunca decide, nunca armazena credenciais ou templates biométricos (estes são tratados como masked input: `@masked.*` in-memory, nunca persistidos).
8. **Só chaves + atributos mascarados, só uso interno (purpose limitation).** Guardamos chaves de cliente (`secondary_keys`, `external_refs`, status) e atributos **mascarados e não sensíveis** usados na identificação — nunca perfil completo, credencial ou dado sensível. Uso **interno** (resolução/retomada): não vira CRM, relatório de PII, export nem diretório. O perfil é do CRM do tenant, via MCP.
9. **Identificação é função de NEGÓCIO do fluxo de entrada; channel-gateway é genérico.** O channel-gateway só entrega um envelope padronizado de **origem** (ANI/`from`/ids passados) e roteia ao pool de entrada — não resolve identidade nem tem cadastro. Quem identifica é o **fluxo de entrada** (intake, customizado por negócio), chamando as MCP tools do serviço de identidade. O platform fornece o **mecanismo** (índice + tools + base); o **quando/o quê** identificar é do fluxo.

---

## 3. Cadastro de cliente (Customer master)

Entidade nativa, tenant-scoped. O `customer_id` é gerado por nós e nunca muda.

```jsonc
// Customer
{
  "customer_id":   "cus_7Xa…",            // NATIVO, canônico, imutável
  "tenant_id":     "tenant_demo",
  "status":        "prospect",            // prospect | identified | merged
  "merged_into":   null,                  // customer_id canônico, se status=merged
  "secondary_keys":[                       // alimentam o Lookup 1
    { "kind": "phone", "value_hash": "9f3a…", "confidence": 0.9,  "verified_at": "…" },
    { "kind": "cpf",   "value_hash": "b71c…", "confidence": 0.95, "verified_at": "…" }
  ],
  "external_refs":[                         // ← N sistemas, cada um com seu id
    { "system": "crm_salesforce", "external_id": "0035x…", "confidence": 0.95, "resolved_at": "…" },
    { "system": "erp_totvs",      "external_id": "CLI-8842", "confidence": 0.80, "resolved_at": "…" }
  ],
  "attributes": {                           // ← DIVERSIFICADO (JSONB) — por negócio
    // mascarado e não sensível; gravado pelo fluxo de entrada na identificação.
    // Forma livre por tenant/fluxo: telco {operadora,plano}, banco {agencia}, varejo {…}
  },
  "created_at": "…", "updated_at": "…"
}
```

**Padronizado vs. diversificado:**
- **Núcleo padronizado (colunas):** `customer_id`, `status`, e as **chaves de origem** vindas dos channel-gateways (phone/email/principal). É o único contrato universal.
- **`attributes` diversificado (`JSONB`):** o que cada **fluxo de negócio** capturou/validou — forma livre por tenant, mascarado e não sensível. Permite a cada sistema cachear a sua base ou guardar só o básico.
- **Índice de resolução normalizado:** mesmo com `attributes` livre, as **chaves usadas para resolução cross-canal** (`secondary_keys`) permanecem **tipadas + hasheadas** (`kind`+`value_hash`) — senão o Lookup 1 não casa de forma eficiente/consistente. O fluxo, ao validar uma chave que quer tornar "resolvível", a registra como `secondary_key` (e pode guardar contexto extra em `attributes`).

- **`status: prospect`** — criado automaticamente no primeiro contato anônimo (só com handle de canal). Vira `identified` quando uma identidade forte (login, CPF, match no CRM) é anexada.
- **`external_refs`** — `system` é o identificador lógico do sistema consultado; permite mapear ids divergentes de múltiplos sistemas sem que nenhum vire a chave. Quem precisa do id do CRM lê daqui.
- **Fonte de verdade de identidade é nativa**; os externos são enriquecimento mapeado. Migração/troca de CRM mexe só em `external_refs`.
- **Só chaves, uso interno.** O registro guarda identificadores/refs e status de validação — **não** perfil (nome, endereço, etc.), **não** credenciais, **não** biometria. Perfil vive no CRM do tenant (via MCP); memória de longo prazo em `insight.historico`. Estes dados servem só à orquestração interna (resolução/retomada) — não são exportados nem expostos como diretório de cliente.
- **Atualização contínua.** Sempre que um fluxo **captura e valida** uma chave do cliente, faz upsert no cadastro (carona no `context_tags.outputs` que já promove `caller.*`). Identidade progressiva é comportamento permanente, não evento pontual.
- **Validação é da retaguarda.** Uma chave só vira `verified` quando o backend do contratante confirma (CRM `resolve` / `identity_verify` via MCP). A `confidence` reflete o veredito do backend, não um palpite nosso. Biometria/credencial capturada é repassada mascarada e **não** persiste — guarda-se só o resultado.

**Persistência em dois andares:**

- **Efêmero (Redis, TTL deslizante):** todo `customer_id` nasce aqui — prospect (`{t}:customer:prospect:{customer_id}`) + índice de resolução (§4). Browse/contato anônimo nunca toca o banco; expira sozinho.
- **Durável (PostgreSQL, schema `identity` — novo, separado de `auth`):** promovido só por **gatilho concreto** (§5). O `customer_id` nativo nasce na criação efêmera e é **reusado** na promoção (upsert, id estável) — nada downstream quebra.

> **Cliente final ≠ usuário da plataforma.** O schema `auth` é dos operadores (RBAC/ABAC, `accessible_pools`, `module_config`). O cliente final não loga na plataforma nem tem permissões → schema próprio `identity`. Quando o cliente **é autenticado**, é pela auth **da loja/app do tenant** (federada), representada como âncora `princ` (`sub` do JWT do tenant) e/ou `external_ref` — **nunca** uma linha em `auth.users`.

TTLs do andar efêmero são parâmetros de Config API (namespace `identity`: `prospect_ttl_s`, `resolution_index_ttl_s`), override por tenant como os demais tempos.

O Lookup 1 lê o índice no Redis (cobre os dois andares); em miss de um cliente já promovido (Redis frio), cai no PG e re-hidrata o índice. `insight.historico.*` permanece keyed por `customer_id` — agora explicitamente o **nativo**.

> **Consistência a corrigir (migração):** hoje o step CRM `resolve` grava `caller.customer_id` possivelmente com o **id do CRM**. No modelo novo, `caller.customer_id` deve ser o **nativo**, e o id do CRM vai para `external_refs`. Ver §12 (migração).

---

## 4. As duas buscas

### 4.1 Lookup 1 — Resolução de identidade (chave secundária / ref externo → `customer_id`)

```
{t}:identity:{kind}:{value_hash}        → customer_id      (kind ∈ phone|email|cpf|princ|dev)
{t}:identity:ext:{system}:{external_id} → customer_id      (cross-ref de sistema externo)

value_hash = hex(sha256(tenant_salt + valor_normalizado))
tenant_salt em {t}:config:identity:salt   (rotação invalida o índice de resolução; rebuild a partir do cadastro PG)
```

Mantido em sincronia com `Customer.secondary_keys`/`external_refs`. Resolver com **mais de um candidato** → desambiguação por confiança (âncoras autoritativas — `princ`, `ext` — pesam mais que `phone`); colisão real → `ask`.

### 4.2 Lookup 2 — Pendências (`customer_id` → fluxos pendentes)

```
{t}:pending_by_customer:{customer_id}   → HASH  field={session_id}  value=PendingEntry
  TTL = max(TTL dos pendentes); renovado a cada novo pending
```

Hash porque um cliente pode ter **mais de um** pendente (carrinho aberto + troca em andamento). Mesmo grão de `{t}:insight:h:{customer_id}`. Não há fan-out por âncora: identidade progressiva mexe só no Lookup 1; as pendências ficam paradas aqui.

```jsonc
// PendingEntry
{
  "session_id":      "sess_…",
  "customer_id":     "cus_7Xa…",
  "resume_token":    "plughub_wh_…",     // mesma fonte de verdade do resume atual
  "pool":            "loja_checkout",
  "skill_id":        "skill_checkout_v3",
  "suspended_at":    "2026-06-09T12:00:00Z",
  "expires_at":      "2026-06-11T12:00:00Z",
  "policy":          "offer",            // offer | auto
  "intent":          "retomar_checkout",
  "context_preview": { "itens": 3, "valor": "[brl:tk…:R$ ***]" }  // mascarado; full vem do ContextStore
}
```

### 4.3 Relação com chaves existentes

- `{tenant}:resume_tokens` (Arc 19) — **mantém**: fonte de verdade do "pendente vivo". A leitura valida o `resume_token` aqui (token consumido → limpa a entrada).
- `channel:{ch}:{handle}:session` — **mantém**: continuidade intra-canal (camada por baixo do resolvedor).
- `{tenant}:pending_workflow:{id}` (existente, via `delegate()`) — **é a semente**, generalizada para `pending_by_customer` (§12 migração).

### 4.4 `origin_identity` — contrato (genérico) do channel-gateway + dois momentos

O channel-gateway entrega ao fluxo de entrada um envelope **padronizado** com o que a **origem** do canal sabe — e nada mais:

```jsonc
// session.origin_identity  (preenchido pelo channel-gateway)
{
  "channel": "whatsapp",
  "ani":     "+5511999990000",   // PSTN / SMS / WhatsApp — quando há
  "from":    null,                // e-mail — quando há
  "provided_ids": [               // webchat/WebRTC: ids passados pela app (padronizados)
    { "kind": "email", "value": "…" },
    { "kind": "cpf",   "value": "…" }
  ]
}
```

**Dois momentos da identificação (função do fluxo, não do gateway):**

1. **Origem / fraca** — automática, a partir de `origin_identity` (ANI/`from`/ids). Suficiente para resolver/provisionar um `customer_id` e checar pendência. Pode faltar (webchat anônimo).
2. **Positiva / negocial** — exigida antes de tocar qualquer sistema de negócio (ANI sozinho não basta). O fluxo de entrada conduz: coleta o que o **negócio** define como identificação positiva, **valida na retaguarda** (`identity_verify`), e pede a gravação (`customer_upsert`) das chaves/atributos adicionais (mascarados, não sensíveis). Cada fluxo é customizado → a base resultante é diversificada (§3, `attributes`).

---

## 5. Provisionamento, identificação e merge

- **Provisionamento efêmero (sempre, barato):** inbound sem `customer_id` resolvível → cria prospect **só no Redis** com `customer_id` nativo + TTL deslizante; indexa o handle (`{t}:identity:{kind}:{hash}` → `customer_id`). Sem insert no PG. Cobre browse anônimo sem inflar o cadastro.
- **Promoção ao durável (PG) — gatilhos "concretos":** (a) **identificação** (login / CPF / match no CRM); (b) **registro de pendência `customer_resumable`** que precise sobreviver à janela efêmera; (c) **evento de negócio** (pedido/pagamento). Promoção = upsert no PG **reusando o mesmo `customer_id`**.
- **Gate de identificação:** `customer_resumable: true` (e ações sensíveis como pagamento) **exigem ao menos uma âncora durável**. Se a identidade está fraca/anônima no momento, (b) dispara uma identificação em (c) ("quer que eu guarde seu carrinho? me confirma seu WhatsApp") antes de registrar. Carrinho 100% anônimo retoma só **no mesmo canal** (mapa de handle); cross-canal exige âncora durável.
- **Identificação/enriquecimento (contínuo):** a verificação é delegada à retaguarda do contratante via MCP de domínio (`identity_verify(captured) → {ok, external_id, confidence}`, no molde do CRM `resolve`) — a plataforma só repassa o que capturou (campos sensíveis/biometria mascarados, não persistidos) e recebe o veredito. Quando volta `ok`, anexa `secondary_keys`/`external_refs` ao mesmo `customer_id` (nativo não muda), `status → identified`, e promove se ainda efêmero. Upsert acontece **sempre** que uma chave é validada num fluxo, não só na primeira identificação.
- **Merge:** descobre-se que dois `customer_id` nativos são a mesma pessoa (ex.: o `external_id` do CRM já mapeava para outro nativo) → escolhe canônico; move `secondary_keys`, `external_refs`, `pending_by_customer` e `insight.historico` para o canônico; marca o perdedor `merged_into=canônico`; reaponta os índices. Como tudo pendura no `customer_id`, o merge é bounded e local.

---

## 6. Contrato com o nível (a)

A política de retomada é declarada na **delegação** — o step **`delegate`** (primitivo canônico, alvo = **pool**; ver `delegate-contrato-por-pool-spec.md`) ou o `collect`. Nunca no `suspend` de sinal (§1.2). Campos novos, **opcionais e channel-abstract**:

```yaml
- id: confirmar_checkout
  type: delegate
  pool: loja_checkout_io           # ALVO É O POOL (skill publicada nele = agente de I/O)
  customer_resumable: true          # (b) indexa a pendência em pending_by_customer
  resume_policy: offer               # offer | auto  (default: offer)
  on_resume: revisar_pedido
  # gate de identificação NÃO é campo — é wirado no fluxo antes (coleta + identity_verify
  # + choice); só chama este delegate com customer_resumable quando há âncora durável.
  # sem canal nem identidade concreta — (b) resolve/provisiona o customer_id; pool define a skill
```

- `customer_resumable: false` (default) → pendente só retomável por token (comportamento atual do `delegate()`).
- **`suspend` (webhook/timer/approval) não recebe esses campos** — sinal de máquina; retorno espontâneo do cliente vira status/attach (§1.2).
- (b) descobre o `customer_id` no ato da delegação (resolve a partir de `caller.*`; provisiona prospect efêmero se não houver).
- **`customer_resumable: true` implica gate de identificação:** se a identidade é fraca/anônima, (b) pede uma âncora durável em (c) antes de registrar a pendência e promove o cliente ao PG (§5). Sem âncora durável → não há pendência cross-canal (só intra-canal).

---

## 7. Fluxo de resolução no inbound

Executado por (b) em todo inbound, depois do guard intra-canal e só quando ele não resolve.

```
1. Continuidade intra-canal? (channel:X:{handle}:session vivo) → segue sessão. FIM.
2. Lookup 1 — resolve customer_id a partir das chaves do inbound (handle + identidade já dada):
     0 candidatos → provisiona prospect (cobre anônimo) ; segue ao passo 3 (sem pendência → sessão nova)
     1 candidato  → customer_id
     >1           → desambiguação por confiança / ask
3. Lookup 2 — {t}:pending_by_customer:{customer_id}:
     limpa entradas com resume_token já consumido
     0  → sessão nova (caminho padrão). FIM.
     1  → candidato a retomada (passo 4)
     >1 → resume_policy_multi (most_recent | ask | by_pool)
4. Política do pendente:
     auto  → retoma direto (passo 5)
     offer → (c) pergunta ao cliente; sim → passo 5 ; não → sessão nova (pendência preservada)
5. Retomada:
     usa resume_token → caminho de resume existente
     suspended → active ; routing aloca instância do pool ; novo segment_id no MESMO session_id
     (b) faz bind channel:X:{handle}:session → session_id retomado
     emite session_resumed (resume_origin: identity)
     consome a entrada do índice (idempotência)
```

---

## 8. Retomada — binding de canal e guards

- (b) vincula o novo canal (`channel:X:{handle}:session`) ao `session_id` retomado.
- `_check_already_routed` (5 chaves do orchestrator-bridge) evita duplo roteamento.
- (a) é channel-abstract: retomar via webchat um fluxo vindo do WhatsApp é transparente; (b) só troca o canal do próximo episódio; Arc 16 ajusta o render.
- Entrada do índice **consumida** na retomada; dois inbounds concorrentes → o segundo acha token já consumido e cai na continuidade normal.

---

## 9. Ambiguidade e políticas

| Situação | Tratamento |
|---|---|
| Vários pendentes p/ o `customer_id` | `resume_policy_multi`: `most_recent` \| `ask` \| `by_pool` |
| Lookup 1 com >1 candidato | desambiguação por confiança (autoritativa > fuzzy); `ask` em colisão real |
| Identidade fraca/anônima | prospect; sem match cross-canal forte até identificar |
| Pendente expirado | inexistente (limpeza preguiçosa) |
| Cliente recusa a oferta | sessão nova; pendência **preservada** |

---

## 10. Privacidade, LGPD e auditoria

- **Não somos autoridade de identidade** — autenticação/verificação (inclusive biometria) é do contratante; nunca armazenamos credenciais nem templates biométricos (tratados como masked input, in-memory, repassados e descartados). Reduz drasticamente a superfície de responsabilidade.
- **Purpose limitation / uso interno** — o cadastro `identity` guarda **só chaves**, **só** para resolução/retomada interna. Não é CRM, relatório de PII, export nem diretório. Perfil fica no CRM do tenant.
- **Efêmero-por-default** — identidade anônima vive só no Redis com TTL e se auto-expira; PII só persiste no PG por gatilho concreto (em geral após o cliente fornecer/consentir). Minimização de dados nativa.
- **Chaves secundárias hasheadas com salt por tenant** — índice de resolução nunca guarda telefone/e-mail/CPF em claro.
- **Centralização ajuda a conformidade**: erasure (SAR) vira cascata a partir do `customer_id` (cadastro + `secondary_keys` + `external_refs` + `pending_by_customer` + `insight.historico`). Um ponto, não N.
- **Consentimento** para retomada proativa cross-canal; default `offer`.
- **Masking** no `context_preview`; cliente vê só `display_partial`.
- **Auditoria**: toda resolução que casa e retoma é evento auditável (identity_key hasheada, customer_id, session). Sem opt-out.
- **Anti-enumeração**: nunca confirmar "esse telefone tem conta aqui" antes do cliente iniciar contato espontâneo.

---

## 11. Observabilidade

- `session_resumed` ganha `resume_origin: same_channel | token | identity` e, p/ `identity`, `resume_channel_from`/`resume_channel_to`.
- Métricas ClickHouse: taxa de retomada cross-canal; recuperação por reaparecimento (carrinhos retomados/suspensos); matriz canal-origem × canal-retorno; tempo suspend→retomada. É o KPI de venda do "business in any media".

---

## 12. Fases de implementação

| Fase | Entrega |
|---|---|
| **A — cadastro + duas buscas** | Cadastro **em dois andares** (prospect efêmero no Redis + promoção ao PG por gatilho concreto, id estável); Lookup 1 (`{t}:identity:*`, Redis→PG fallback) e Lookup 2 (`{t}:pending_by_customer`); `delegate()` resolve/provisiona `customer_id`, aplica o **gate de identificação** quando `customer_resumable`, e grava a pendência sob o cliente; campos `customer_resumable`/`resume_policy` na delegação; `offer`/`auto`; bind de canal + `session_resumed` com `resume_origin`. Reusa `workflow_resume` inalterado. |
| **B — identidade progressiva + external_refs** | Anexar `secondary_keys`/`external_refs` quando resolvidos mid-flow; consolidar `caller.customer_id = nativo` (corrigir o resolve do CRM); Lookup 1 por ref externo. |
| **C — desambiguação + attach + analytics** | Múltiplos pendentes; **status/attach** no retorno durante `suspend` de sinal (§1.2); merge de clientes; consent gating; eventos e relatórios cross-canal. |
| **D — anônimo/dispositivo** | Device id webchat (baixa confiança) + merge ao identificar. |

---

## 13. Assinaturas detalhadas — Fase A

> Delta sobre `channel-gateway/adapters/webhook.py` + `mcp-server-plughub/src/tools/workflow.ts`. Contrato existente mantido por compatibilidade onde substituído.

### 13.0 Estado atual (fiel ao código)

```
Escrita (delegate, _open_child_session):
  key   = "{tenant}:pending_workflow:{contact_id}"   # contact_id em CLARO, string + TTL
  value = {"resume_token","child_session_id","pool","context"}   # 1 por contact_id
Leitura (get_pending_workflow):
  GET {tenant}:pending_workflow:{contact_identifier}; valida token ∈ {tenant}:resume_tokens
HTTP: GET /v1/channels/webhook/pending/{contact_identifier}?tenant_id=…
MCP : pending_workflow_get(contact_identifier, tenant_id) → {found, resume_token, context}
      workflow_resume(resume_token, decision, payload)
```

### 13.1 Resolução / provisionamento — `resolve_or_provision_customer`

```python
async def resolve_or_provision_customer(
    self,
    tenant_id: str,
    anchors:   list[IdentityAnchor],     # {kind, value} crus (loopback); hash é server-side
    provision: bool = True,              # cria prospect se não achar
) -> CustomerRef:                        # {customer_id, status, matched_by, confidence}
    """Lookup 1. Normaliza+hasheia âncoras; resolve via {t}:identity:* (Redis;
    miss → PG + re-hidrata); se múltiplos, desambigua por confiança; se nenhum e
    provision=True, cria prospect EFÊMERO no Redis (TTL) com customer_id nativo e indexa.
    Promoção ao PG é feita à parte, por gatilho concreto (§5)."""
```

### 13.2 Escrita da pendência — `write_pending` (estende `_write_pending_workflow`)

```python
async def write_pending(
    self,
    tenant_id:   str,
    customer_id: str,                    # resolvido/provisionado no delegate
    entry:       PendingEntry,
    ttl_s:       int,
) -> None:
    """HSET {tenant}:pending_by_customer:{customer_id} {session_id} {entry}; EXPIRE."""
```

### 13.3 Leitura da pendência — `find_pending` (estende `get_pending_workflow`)

```python
async def find_pending(
    self, tenant_id: str, customer_id: str,
) -> list[PendingEntry]:
    """Lookup 2. Retorna pendentes vivos (resume_token ∈ resume_tokens), limpa stale."""
```

### 13.4 Consumo na retomada — `consume_pending`

```python
async def consume_pending(self, tenant_id: str, customer_id: str, session_id: str) -> None:
    """HDEL {tenant}:pending_by_customer:{customer_id} {session_id}. Idempotente."""
```

### 13.5 HTTP

```
NOVO  POST /v1/channels/webhook/identity/resolve
  body: { tenant_id, anchors:[{kind,value}], provision? }   # PII só no loopback; hash server-side
  resp: { customer_id, status, matched_by, confidence }

NOVO  GET  /v1/channels/webhook/pending/by-customer/{customer_id}?tenant_id=…
  resp: { found, count, pendings:[{session_id, resume_token, pool, skill_id,
                                   intent, suspended_at, context_preview}] }

LEGADO (mantido)  GET /v1/channels/webhook/pending/{contact_identifier}?tenant_id=…
  → wrapper: resolve_or_provision_customer([1 âncora inferida], provision=False) → find_pending → 1º pendente.
```

### 13.6 MCP tools

```ts
// pending_workflow_get — estendida, backward-compatible (faz Lookup 1 → Lookup 2 internamente)
{
  tenant_id:          z.string().min(1),
  anchors:            z.array(z.object({
                        kind:  z.enum(["phone","email","cpf","princ","dev"]),
                        value: z.string().min(1),
                      })).optional(),
  contact_identifier: z.string().optional(),   // LEGADO: 1 âncora inferida
  provision:          z.boolean().optional(),  // default false p/ esta tool (só consulta)
}
// → { customer_id, found, count, pendings:[{session_id, resume_token, pool, intent,
//                                           suspended_at, context_preview}] }

// customer_resolve — NOVA: identidade sem pendências (p/ carregar histórico, etc.)
{ tenant_id, anchors:[{kind,value}], provision? } // → { customer_id, status, matched_by, confidence }

// workflow_resume — INALTERADA. Único ponto que destrava (a). Recebe o resume_token escolhido.
```

`count > 1` → o fluxo de intake (perfil `agent`) aplica `resume_policy_multi`.

### 13.7 Normalização de âncoras

| kind | normalização |
|---|---|
| phone | E.164 (`+55…`) |
| email | trim + lowercase |
| cpf   | só dígitos |
| princ | `sub` do JWT |
| dev   | device/cookie id (fase D, baixa confiança) |

`value_hash = hex(sha256(tenant_salt + normalizado))`. `external_id` é indexado por `{t}:identity:ext:{system}:{external_id}` (não-PII em geral; hashear se for PII).

### 13.8 Migração a partir da chave atual

1. **Dual-write** atrás de flag: escreve `pending_workflow:{id}` (legado) **e** resolve/provisiona `customer_id` + grava `pending_by_customer`.
2. `get_pending_workflow` legado vira wrapper (§13.5).
3. Após validação, para de escrever a chave legada; wrapper GET permanece p/ clientes antigos.
4. Sem backfill: o TTL curto drena a chave legada.
5. **Consolidar `caller.customer_id = nativo`**: ajustar o step CRM `resolve` para gravar o nativo e empurrar o id do CRM para `external_refs` (fase B).

---

## 14. Critérios de aceitação (núcleo, fase A)

1. Fluxo suspenso com `customer_resumable: true` no WhatsApp → cliente escreve no webchat com mesmo telefone/identidade → é **oferecida** a retomada; ao aceitar, novo segmento no **mesmo `session_id`**, fluxo (a) continua do ponto suspenso.
2. `resume_policy: auto` → retoma sem perguntar.
3. Cliente anônimo novo → prospect provisionado; sem pendência → sessão nova.
4. Continuidade intra-canal → inalterada (não passa pelo resolvedor).
5. Índice de resolução nunca contém PII em claro (teste inspeciona chaves Redis).
6. Resume por token (callback de terceiro) → inalterado.
7. Dois inbounds concorrentes p/ o mesmo pendente → exatamente uma retomada.
8. CRM retorna id → guardado em `external_refs` do `customer_id` nativo; o nativo não muda.

---

## 15. Questões em aberto

1. ~~Quem executa (b)?~~ **Resolvido (e corrigido):** a **identificação é orquestrada pelo fluxo de entrada** (intake), que chama MCP tools (`customer_resolve`/`pending_workflow_get`/`customer_upsert`) — exatamente como o `agente_portabilidade_intake_v1` já faz. O **channel-gateway é genérico**: só entrega o envelope `origin_identity` (§4.4) e roteia; **não** resolve identidade nem hospeda o cadastro. O índice + base + tools são um **serviço de domínio de identidade** do platform (schema `identity`), invocado pelo fluxo — não responsabilidade do gateway. (Correção da versão anterior, que dizia "resolvedor é serviço no channel-gateway".)
2. ~~**Persistência do cadastro**~~ **Resolvido:** schema PG **novo `identity`** (não `auth` — cliente final ≠ usuário de plataforma) + índice de resolução no Redis. Cliente autenticado = auth federada da loja → âncora `princ`/`external_ref`, nunca `auth.users`. Tabelas: `customers`, `customer_secondary_keys`, `customer_external_refs`, `customer_merges`.
3. ~~**Política de provisionamento de prospect**~~ **Resolvido:** provisiona **sempre**, mas **efêmero no Redis** (TTL deslizante); promove ao PG só por gatilho concreto (identificação / pendência `customer_resumable` / evento de negócio). `customer_id` nativo estável no efêmero→durável. Gate de identificação pede âncora durável quando necessário. **TTLs via Config API** (namespace `identity`: `prospect_ttl_s` deslizante, `resolution_index_ttl_s`), override por tenant. Regra: TTL do prospect ≥ TTL de pendência registrada — na prática garantida porque registrar pendência `customer_resumable` já promove ao PG.
4. ~~**Confiança por sistema externo**~~ **Resolvido:** peso de confiança por `system` é parâmetro de Config API (namespace `identity`, ex.: `system_trust: { crm_salesforce: 0.95, erp_totvs: 0.80 }`). Usado em (a) desambiguação do Lookup 1 — maior confiança vence — e (b) merge — qual `external_id` é autoritativo. Ordem de autoridade das âncoras: `princ`/`ext` de alta confiança > `cpf`/`email` > `phone` > `dev`. Colisão entre âncoras de confiança similar → `ask`.
5. ~~**Anti-enumeração / consent default**~~ **Resolvido:** default global `offer` (nunca `auto`), override por skill via `resume_policy`. Anti-enumeração: nunca confirmar "esse contato tem cadastro/pendência aqui" antes de o cliente iniciar contato espontâneo no canal e confirmar a oferta.
