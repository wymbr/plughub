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

## Questões em aberto (resolver na implementação)

- Mapeamento pool→`resource_type`: webhook pools contam como `ai_agent`?
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
2. Gates de criação: instância IA (bootstrap/routing), login humano concorrente
   (auth/registry), admissão de sessão (`assertQuota` passa a ter chave).
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
   - **3b** deploy: Σ declarada nos deploys ≤ C (validação no fluxo de deploy) —
     pendente.
4. ✅ (2026-06-04) platform-ui: aba **Capacidade** na BillingPage — KPIs
   contratado (C, pricing `/capacity`) × alocado (provisionada corrente, último
   bucket do occupancy) × saldo (C − alocado, verde/vermelho) × reservado/shared
   (registry `/capacity/conformance`); tabela por resource_type (base + reserva
   ativa) e pools com reserva; **alertas**: vermelho `conform=false` (reservas >
   C), âmbar alocado > C (deploy acima do contrato — admissão corta em C, custo
   ocioso), info sem contrato configurado. i18n en + pt-BR (`billing.capacity.*`).
5. analytics-api/UI: aba Capacidade contratado-cêntrica (teto único = C).
6. Demo: recursos do pricing coerentes com os deploys.
7. **Revisão da exibição de "available" nos Monitores** (atividade registrada
   2026-06-04, pós item 4): com o modelo reserved+shared, o "available" exibido
   (slots físicos livres do snapshot) diverge do **admissível** — o que a
   admissão de fato deixa entrar: pool com reserva → `reserva − uso do bucket
   reservado`; pool sem reserva → `min(slots livres, shared restante = C −
   Σ reservas − uso do shared)`. Um pool pode exibir 20 e rejeitar por
   `shared_full`. Telas a revisar: **Monitor/Sessions** (KPI Available/Online +
   coluna Available), **Monitor/Pools** (KPI "Available agents" + coluna
   Available), **Monitor/Agents**, e demais consumidores do snapshot
   (`available` escrito pelo `write_pool_snapshot` do routing — PresenceSidebar
   /Console, dashboard cards `pool_status`). **A definir**: exibir um número só
   (admissível, o que importa operacionalmente) ou os dois (físico × admissível);
   como representar o shared (bucket comum não é "do pool" — linha própria?);
   fonte (routing já conhece os buckets de admissão → enriquecer o snapshot com
   `admissible`/`bucket`). Decisão de UX antes de implementar.
