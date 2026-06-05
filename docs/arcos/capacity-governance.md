# Governança de Capacidade — contratado como fonte única

> Estado: **spec / ADR** (não implementado). Modelo fechado em 2026-06-04, na
> validação do fechamento da Fase 2 (Pools/Infra) — ver `pools-infra-report.md`.

---

## Problema

O contratado (pricing) hoje não governa nada:

- A config aceita `Σ session_reservation > contratado` → o shared derivado
  (`shared = contratado − Σ reservas`) fica **negativo**, quebrando a semântica da
  admissão híbrida (`reservation_full`/`shared_full`/`quota`).
- O gate de admissão por quota (`{t}:quota:*` lidas pelo `assertQuota`) está
  **documentado mas não existe** — pricing-api não tem código Redis (verificado
  2026-06-04: chaves vazias após POST de resources).
- O demo deploya ~295 slots com 25 contratados — incoerência invisível e sem alerta.

---

## Modelo (decisões fechadas — 2026-06-04)

Por `resource_type` (`ai_agent`, `human_agent`), por tenant/instalação:

- **C (contratado)** = base + reservas comerciais ativas no pricing. **Fonte única**;
  tudo deriva e respeita C.
- **Princípio central: recursos são criados no momento do uso**, não pré-instanciados
  — IA é instanciada on-demand; humano conta ao **logar**. O gate primário é na
  **criação**, sempre contra o C **vigente** naquele momento.
- **Declaração no deploy**: as quantidades são declaradas no flow/deploy no momento
  do deploy (Config + Deploy, Fase 3) — não há YAML de provisionamento. Validação da
  soma declarada contra C acontece **no deploy**.
- **Humano = concorrentes logados** (concurrent licensing): C_human limita logins
  simultâneos; login além de C é negado.
- **Reservas**: `Σ session_reservation ≤ C` e `shared = C − Σ reservas ≥ 0`
  (**zero permitido** — tudo reservado é estado legítimo; **negativo nunca**).
  Validado na config do pool.
- **Redução de contrato: sempre aceita** (decisão comercial não é refém da config).
  Efeito imediato no gate de criação — pode faltar recurso no pico (comportamento
  esperado) e uma reserva mal dimensionada passa a sobre-consumir → revalidar
  configs no contract-change e **alertar não-conformidade** (não bloquear).
- **P (alocado/provisionado)** muda de papel: deixa de ser "segunda capacidade" e
  vira **medidor de consumo do contrato** — corrente (instâncias vivas + humanos
  logados) e declarado (somas dos deploys). A UI mostra **C, P e o saldo (C − P)**
  ("contratado ainda não utilizado"); P > C = alerta de incoerência;
  P declarado ≪ C = alerta de subentrega (pagando capacidade que não atende).

---

## Pontos de enforcement

1. **Criação/uso (primário)**: instanciação IA, login humano, admissão de sessão →
   quota derivada de C em Redis (`{t}:quota:*`), gravada pelo pricing-api no
   upsert/ativação de resources (implementar a integração hoje inexistente).
2. **Deploy**: soma declarada ≤ C → rejeita/alerta no momento do deploy.
3. **Config de pool**: `Σ reservas ≤ C`, `shared ≥ 0`.
4. **Contract-change**: revalida configs, marca não-conformes com alerta
   (UI/Monitor); nunca bloqueia a redução.

---

## Analytics (aba Capacidade — ajuste pós-arco)

Teto único do gráfico/utilização/headroom = **C**; P vira "consumo do contrato"
(KPI saldo) + diagnóstico de incoerência. A linha da provisionada sai de vez (já
removida da visão do total no fechamento da Fase 2; aqui sai do modelo).

---

## Tipagem de pool — decisões fechadas (2026-06-05, pré item 2)

- **`agent_kind: "human" | "ai"` no pool** (Prisma + `PoolRegistrationSchema` +
  YAML + UI). Canal NUNCA é associado a tipo — canal aponta para pool; o pool
  declara o tipo. A pergunta "webhook conta como ai_agent?" se dissolve: webhook
  pools declaram `ai`.
- **Backfill por inferência, uma vez**: pool existente sem o campo → deploy slot
  ⇒ `ai`, senão `human`; daí em diante declaração explícita (RegistrySyncer não
  quebra).
- **Validação no registro de recurso** ("login no pool"): bootstrap/bridge
  registra instância IA, `registerHumanAgent` registra humano — ambos checam
  `tipo do recurso == agent_kind do pool`. Pool misto proibido.
- **`queue_config` ⇒ `agent_kind: human`** (422): fila atendida só faz sentido
  para recurso escasso/lento (humano) — para IA, o slot da fila instanciaria o
  próprio agente solicitado; para máquina-a-máquina, backpressure ≠ fila.
- **Fila atendida é `ai` e cobrável** (opção "2" — sem terceiro kind): racional
  comercial — fila inteligente É um agente IA licenciado; o tier gratuito é a
  **fila de sistema** (sem agente), arco futuro registrado no TODO. Isolamento
  operacional da fila via `session_reservation` no pool de fila; segmentar fila
  nos relatórios via vínculo estrutural (`queue_config`) para não poluir o
  sinal de dimensionamento de C_ai.
- **Gates por tipo** (item 2) contam contra `C_ai`/`C_human` pelo `agent_kind`
  do pool da sessão/login.

## Questões em aberto (resolver na implementação)

- ~~Mapeamento pool→`resource_type`~~ ✅ resolvida acima (`agent_kind` no pool).
- ~~Granularidade das chaves de quota~~ ✅ resolvida no item 1 (ver § Pendente):
  uma chave por tenant agora; por `resource_type` junto com os gates por tipo.
- Interação com reservas comerciais ativáveis (C muda ao ativar/desativar reserva
  → mesma revalidação do contract-change).

---

## Pendente (implementação)

1. ✅ (2026-06-04) pricing-api: **quota sync** (`quota_sync.py`) — toda mutação de
   resources (upsert/delete/activate/deactivate) recalcula C (ai_agent +
   human_agent, base + reservas ativas, todas as instalações) e grava
   `{t}:quota:max_concurrent_sessions` (DEL quando C=0 → sem limite); `sync_all`
   no boot (auto-cura pós flush); `PLUGHUB_PRICING_REDIS_URL` (vazia = off,
   Redis fora = warning, billing nunca quebra). `pricing.md` § Quota Side Effects
   corrigido. **Granularidade resolvida**: uma chave por tenant (a que tem
   leitores — admissão híbrida + checkConcurrentSessions); chaves por
   resource_type ficam para os gates por tipo (itens 2) quando existirem leitores.
2. Gates de criação — **Etapa 1 ✅** (2026-06-05, fundação): `agent_kind` ponta a
   ponta — `PoolRegistrationSchema` (@plughub/schemas), Prisma + **backfill por
   inferência no boot** do registry (deploy slot ⇒ ai; senão human), persistência
   e validação `queue_config ⇒ human` (422, estado resultante) no POST/PUT de
   pool, propagação ao routing (`PoolConfig.agent_kind` via pool.registered/
   updated), `tenant_demo.yaml` com declaração explícita (16 ai + retencao
   human), e quotas por tipo no pricing (`{t}:quota:capacity:ai_agent` /
   `:human_agent`, mesmo recompute/DEL do quota sync).
   **Etapa 2 ✅** (2026-06-05) — os gates:
   - **Humano** (`registerHumanAgent`, mcp-server): (a) kind do pool — login só
     em `agent_kind: human` (lê o pool_config cacheado; fail-open se ausente);
     (b) logins concorrentes (`{t}:instance:human-*`) ≤ C_human — re-login do
     mesmo usuário nunca bloqueia; recusa → `login_denied` no WS + toast
     persistente de erro no Console (`AgentAssistContext`); pool auto-criado no
     login declara `agent_kind: human`. Falha de Redis → fail-open (gate nunca
     derruba login por infra).
   - **IA** (`AdmissionController`, routing): sessões entrando em pool
     `agent_kind: ai` ≤ C_ai — SET `{t}:admission:kind:ai` + member key, mesma
     mecânica idempotente dos buckets (rollback em rejeição, migração ai↔human
     atualiza tracking, mid-session fail-open mantém atribuição de origem,
     reconciler libera via session:closed). Rejeição na porta → outage
     **cause `quota`** (visível na demanda reprimida como "Teto contratado").
   - **Recurso × kind**: deploy de skill em pool `human` → 422 (pool-slots);
     login humano em pool `ai` → `login_denied` (acima).
3. Validações de config:
   - **3a ✅** (2026-06-04) pool: `Σ session_reservation ≤ C` / `shared ≥ 0` no
     agent-registry (POST/PUT de pool) — C lido de `{t}:quota:max_concurrent_sessions`;
     sem C ou Redis fora → fail-open (runtime segue protegido pela admissão);
     **reduções sempre passam** (heal gradual de legado não-conforme; re-PUT do
     RegistrySyncer com valor igual não quebra), só aumentos que estourem C → 422
     com detalhe. Conformidade **derivada, não persistida**:
     `GET /v1/pools/capacity/conformance` (contracted/reserved_total/shared/conform
     /pools) — relê C a cada chamada, então mudança de contrato revalida
     implicitamente; alerta visual fica com o item 4.
   - **3b ✅** (2026-06-04) deploy: `Σ declarada nos deploys ≤ C` no agent-registry
     (`lib/capacity.ts` compartilhado com 3a) — declarada por pool = slot `current`
     × `config_json.max_concurrent_sessions` (default 1). Validado em **dois
     pontos**: `PUT /slots/next` (feedback cedo, na declaração) e `POST /promote`
     (momento em que vira efetiva — revalida contra o C vigente, que pode ter
     mudado). Mesmas regras do 3a: sem C → fail-open; reduções/iguais sempre
     passam (re-sync idempotente do RegistrySyncer não quebra o boot); só
     aumentos que estourem C → 422 com detalhe. **Rollback é isento** (operação
     de emergência nunca bloqueia). Nota: compara contra o C total do tenant
     (chave única do item 1); refinamento por resource_type entra com o item 2.
4. ✅ (2026-06-04) platform-ui: aba **Capacidade** na BillingPage — KPIs
   contratado (C, pricing `/capacity`) × alocado (provisionada corrente, último
   bucket do occupancy) × saldo (C − alocado, verde/vermelho) × reservado/shared
   (registry `/capacity/conformance`); tabela por resource_type (base + reserva
   ativa) e pools com reserva; **alertas**: vermelho `conform=false` (reservas >
   C), âmbar alocado > C (deploy acima do contrato — admissão corta em C, custo
   ocioso), info sem contrato configurado. i18n en + pt-BR (`billing.capacity.*`).
5. ✅ (2026-06-04) analytics-api/UI: aba Capacidade contratado-cêntrica — teto
   único do gráfico/headroom/utilização = C (desde o fechamento da Fase 2); KPI
   **Alocado (provisionada)** entra como diagnóstico (vermelho + "acima do
   contrato" quando > C); hints reescritos (valores por pool = alocação física;
   teto do tenant = C).
6. ✅ (2026-06-04) Demo: `pricing-seed` (`infra/seed/seed_pricing.py` + serviço
   no compose) — ai_agent×300 + human_agent×10 → **C=310**, coerente com a Σ
   declarada do YAML (280) + pools de teste/humanos. **Não-destrutivo**: pula se
   o tenant já tiver resources (experimentos do operador sobrevivem ao
   re-`up`); quota de admissão gravada pelo quota sync na primeira subida.
7. **Revisão do "available" — design fechado 2026-06-05** (pós system-queue;
   decisões com o usuário):
   - **Problema**: físico (slots livres) ≠ admissível (`reserva − uso` ou
     `shared restante`, e IA também `C_ai − uso`); pool pode exibir 20 e negar.
     Pools no shared NÃO têm teto individual (só físico + throttle webhook
     display-only) — um pool pode consumir o shared inteiro.
   - **Exibição**: **dois números** (físico / admissível, ⊕ nos compartilhados);
     organização por regime — seções **Reservados × Compartilhado** + **Fila
     gratuita**; **donuts** "total e como está sendo consumido": Compartilhado
     (fatias por pool + disponível), mini-donuts por pool reservado
     (ocupado+disponível), Fila gratuita (mudos por pool + livre, vs teto);
     **tiles** do pipeline: Contratado usado/C + folga, Em atendimento, Em fila
     (atendida/muda), Fila gratuita usado/teto. Tabelas por seção continuam
     para diagnóstico (atend., fila at/muda, disp fís/adm).
   - **Atribuição exata do shared**: novo HASH `{t}:admission:shared_pools`
     {sid→pool} ao lado do SET (SET continua sendo o limite; HASH é índice;
     HSET/HDEL no admit/migração/release; higiene no reconciler). Elimina o
     proxy — fatias somam SCARD por construção.
   - **7a ✅** (2026-06-05, ver CHANGELOG): HASH `shared_pools` + agregador no
     `/v1/operational/pools` + Monitor/Pools com tiles/donuts/seções + tiles no
     Monitor/Sessions. Validado com o cenário-prova (agente livre + contrato
     cheio → `Disp 20/0⊕` vermelho).
   - **7b ✅** (2026-06-05, ver CHANGELOG): occupancy sampler amostra a admissão
     (reservas usadas por pool, shared por pool via HASH, buffer) nas mesmas
     chaves que o Monitor lê → coluna `admitted_peak` + linhas agregadas
     `__reserved__`/`__shared__`/`__buffer__` em `pool_occupancy_peaks`
     (ALTER idempotente) → `/reports/pools/occupancy` com bloco `admission`
     (used vs limit por bucket) → aba Capacidade com **"Admissão no tempo"**
     (reservado+compartilhado empilhados vs linha C) e **"Sala de espera
     gratuita no tempo"** (uso vs teto). Donut = foto; área empilhada = filme.
     **ITEM 7 COMPLETO — ARCO CAPACITY-GOVERNANCE CONCLUÍDO.**
   - Verificações na validação: render do segmento sintético `system-queue` no
     detalhe de Sessions; nenhum `system` vazando em Analytics/Agents.
     Refinamentos futuros registrados: SLA por tier de fila.
