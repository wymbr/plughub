# Wrap-up unificado — Phase 2: hand-off da vaga (escopo reduzido)

> **Status: IMPLEMENTADO ✅ e validado E2E (2026-07-27).** Esta spec é o desenho as-built. O que mudou na
> execução está anotado inline (§5 pontos de corte, §7 R3) e o resultado, com os 3 defeitos pré-existentes que o
> E2E destravou, está no `CHANGELOG.md` e em `docs/guias/conference-mechanics.md` § Mudança 27.
>
> Validação: `test_instance_semaphore.py` (14 casos, 8 novos) · `infra/test/smoke_wrapup_slot_handoff.sh`
> (18 PASS) · E2E com 4 contatos (3 atendidos + 1 na fila), wrap-ups respondidos e fila drenada sem perda.
> Contexto: `TODO.md` § Wrap-up unificado — Phase 2 · `CHANGELOG.md` § Wrap-up unificado (Phase 0+1+3) ·
> `docs/adr/adr-wrapup-detached-pull.md` · `docs/product/finalization-hooks-detach-and-directed-pull-design.md`

---

## 1. Problema

No modo `inline` (auto-atendimento) a ocupação da vaga **oscila** entre o fim do contato e o
auto-claim do wrap-up:

```
humano finaliza → /api/agent_done (mcp-server) → contact_closed (conversations.events)
   ├─ bridge publica agent_done → agent.lifecycle            [bridge main.py:6047]
   │     → kafka_listener                                     [kafka_listener.py:310]
   │     → InstanceRegistry.remove_conversation               [registry.py:402]
   │     → release_instance  SREM "{origin}::*"               [registry.py:466]   ← LIBERA
   └─ bridge fire_pool_hooks → _fire_detached_hook            [main.py:1474 / 1094]
         → webhook pool → nova sessão → item de pull (assigned_to + auto_attend)
         → Console auto-pull (poll ~2-3 s)                    [PullInboxPanel.tsx:218]
         → Router.work_task_claim → claim_instance            [router.py:672]      ← RECLAMA
```

Entre `release` e `claim` a vaga fica livre. Com `max_concurrent = 1`, um contato **push** que chegue
na janela toma a vaga; o auto-claim recebe `-1` → `no_capacity` → o item cai na inbox (degrada para
pull manual — o wrap-up não se perde). O dano real não é o clique extra: é que o agente recebe
**contato novo com wrap-up pendente**, que é exatamente o que a ocupação do wrap-up deveria impedir.

**Os dois caminhos correm concorrentes** (o `agent_done`→Kafka→release e o webhook→sessão→item de
pull não têm ordem garantida). Hoje o poll de 2-3 s mascara a ordem invertida. **Consequência**: o
polish "auto-claim instantâneo" (`refreshSignal` no `conversation.assigned`) **aumenta** a chance do
claim chegar ANTES do release → `-1`. Ele só é seguro depois desta Phase 2.

---

## 2. Decisões de escopo

| # | Decisão | Motivo |
|---|---|---|
| **D1** | O flag de "segurar a vaga" nasce no **bridge**, no publish de `agent_done` (`main.py:6047`) — **não** no `/api/agent_done` do mcp-server | Esse endpoint publica `contact_closed` em `conversations.events`, não o evento de ciclo de vida que aciona o release. O bridge já lê `get_pool_config` no mesmo handler (`main.py:6355`) e conhece os hooks. Preserva o invariante "o routing não consulta hooks". |
| **D2** | O hold é **fungível por instância** — sem plumbar `origin_session_id` até o `contact_data` | O casamento não precisa da origem: a instância é a mesma (`human-{userId}`) e o item já carrega `assigned_to` + `auto_attend` (`models.py:89-95`). Cada hold vale uma vaga; cada wrap-up consome uma. Corta 5 arquivos e 4 rebuilds do escopo. A origem fica **no nome do membro** só para observabilidade. |
| **D3** | Hold **só no `dispatch: inline`** (auto-atendimento) | No `detached` o agente puxa quando quiser — segurar vaga por minutos seria reservar capacidade sem consumidor iminente. |
| **D4** | Uma **única** Lua `claim_or_transfer` substitui `claim_instance`, tolerante às duas ordens | Se o hold existe → swap (net 0); se o release já ocorreu → claim normal respeitando `max_concurrent`. Evita dois caminhos no `router.py` e cobre a ordem invertida (§1). |
| **D5** | Expiração do hold é **passiva, na própria Lua** (timestamp no membro), sem sweeper nem endpoint novo | Sem isso, um wrap-up que nunca chega (webhook non-2xx, workflow falha, logout, browser fechado) deixa a vaga presa até o `EXPIRE` de 24 h do SET → agente bloqueado a `max_concurrent = 1`. Este é o **maior risco da Phase 2** e não estava registrado no TODO. |

---

## 3. Modelo do hold

Membro do SET de ocupantes (`{tenant}:instance:{iid}:sessions`, `registry.py:47`):

```
__wrapup_hold__::{origin_session_id}::{expires_at_ms}
```

- Prefixo `__wrapup_hold__::` **não colide** com o prefixo de sessão `{session_id}::` (uuid) — logo o
  `release_instance` por prefixo de sessão nunca remove um hold, e vice-versa.
- `origin_session_id` = observabilidade/log (não é usado para casar).
- `expires_at_ms` = base da expiração passiva (D5).
- Ocupa **uma vaga** no `SCARD` → toda a contabilidade existente (espelho `current_sessions`,
  `state`, snapshots) continua válida sem mudança.

---

## 4. Lua

### 4.1 `_SWAP_TO_HOLD_LUA` (novo)

Substitui o `release` quando o contato tem wrap-up inline seguindo. **Net 0** na ocupação.

```lua
-- KEYS[1]=sessions set; ARGV[1]=prefixo de sessão ("{origin}::");
-- ARGV[2]=membro do hold; ARGV[3]=ttl_s do SET
-- Idempotente: só cria o hold se DE FATO removeu a origem (redelivery de agent_done
-- não ressuscita um hold já consumido pelo wrap-up).
local members = redis.call('SMEMBERS', KEYS[1])
local prefix  = ARGV[1]
local plen    = string.len(prefix)
local removed = 0
for i = 1, #members do
  if string.sub(members[i], 1, plen) == prefix then
    redis.call('SREM', KEYS[1], members[i])
    removed = removed + 1
  end
end
if removed > 0 then
  redis.call('SADD', KEYS[1], ARGV[2])
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
end
local n = redis.call('SCARD', KEYS[1])
if n <= 0 then redis.call('DEL', KEYS[1]); return 0 end
return n
```

### 4.2 `_CLAIM_INSTANCE_LUA` (substitui o atual, `registry.py:145`)

```lua
-- KEYS[1]=sessions set; ARGV[1]=occupant_id; ARGV[2]=max_concurrent; ARGV[3]=ttl_s;
-- ARGV[4]=now_ms; ARGV[5]="1" se este claim PODE herdar um hold (auto_attend), senão "0"
-- Retorna: >=1 ocupação nova · -1 lotado.
if redis.call('SISMEMBER', KEYS[1], ARGV[1]) == 1 then
  return redis.call('SCARD', KEYS[1])
end
local HOLD  = '__wrapup_hold__::'
local hlen  = string.len(HOLD)
local now   = tonumber(ARGV[4])
local members = redis.call('SMEMBERS', KEYS[1])
local inherit = nil
for i = 1, #members do
  local m = members[i]
  if string.sub(m, 1, hlen) == HOLD then
    -- último campo do membro = expires_at_ms
    local exp = tonumber(string.match(m, '::(%d+)$'))
    if exp == nil or exp <= now then
      redis.call('SREM', KEYS[1], m)          -- hold expirado: some p/ QUALQUER claim
    elseif inherit == nil and ARGV[5] == '1' then
      inherit = m                              -- candidato a herança (fungível)
    end
  end
end
if inherit ~= nil then
  redis.call('SREM', KEYS[1], inherit)
  redis.call('SADD', KEYS[1], ARGV[1])         -- net 0
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
  return redis.call('SCARD', KEYS[1])
end
local n = redis.call('SCARD', KEYS[1])
if n >= tonumber(ARGV[2]) then return -1 end
redis.call('SADD', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return n + 1
```

Invariante: **a varredura de expirados roda em TODO claim** (inclusive push com `ARGV[5]="0"`) —
senão um hold vazado bloquearia o push permanentemente. Só a **herança** é gated por `auto_attend`.

`_RELEASE_INSTANCE_LUA` fica **inalterado** (o prefixo do hold nunca casa com prefixo de sessão).

---

## 5. Pontos de corte

| # | Arquivo | O quê |
|---|---|---|
| 1 | `routing-engine/registry.py:145` | substitui `_CLAIM_INSTANCE_LUA` (§4.2); `claim_instance(..., can_inherit_hold: bool = False)` passa `now_ms` + flag |
| 2 | `routing-engine/registry.py` (após :176) | novo `_SWAP_TO_HOLD_LUA` (§4.1) + `swap_to_hold(tenant, instance, session_id, hold_ttl_s)` |
| 3 | `routing-engine/registry.py:466` | em `remove_conversation`, se `hold_for_wrapup` → `swap_to_hold(...)` em vez de `release_instance(...)`; **o resto do fluxo não muda** (o `remaining` retornado alimenta o espelho `current_sessions`, o `state`, o DECR de `pool:active_count` e a exclusão guardada do `serving_pool` exatamente como hoje) |
| 4 | `routing-engine/registry.py:402` | assinatura `remove_conversation(..., hold_for_wrapup: bool = False)` |
| 5 | `routing-engine/kafka_listener.py:310` | lê `event.get("keep_slot_for_wrapup")` e repassa |
| 6 | `routing-engine/router.py:672` | `claim_instance(..., can_inherit_hold = bool(contact.get("auto_attend")) and claimant == assigned_to)` — o rollback do `-1` (re-enfileira) fica igual |
| 7 | `orchestrator-bridge/main.py:1189` | novo `_has_inline_agent_wrapup(pool_cfg)` — espelha a decisão de `fire_pool_hooks` (`side=agent` + `dispatch=inline` ⇒ workflow com `auto_attend`) |
| 8 | `orchestrator-bridge/main.py:6047` | carimba `"keep_slot_for_wrapup"` no payload do `agent_done`. **As-built**: a resolução (`get_pool_config` + `_has_inline_agent_wrapup`) roda **dentro da task** que já publicava fire-and-forget — o GET ao registry não pode atrasar o handler de fechamento. Leitura própria (o `get_pool_config` do bloco de hooks, :6355, é posterior, condicional e usa `_pool_id_hooks`, definido só em :6242); `get_pool_config` não é cacheado, então é 1 GET extra por close. Registry fora → `False` = release normal (nunca segura vaga sem certeza) |

**Não muda**: `_fire_detached_hook`, `delegate.ts`, engine, channel-gateway, `models.py`,
`PullInboxPanel.tsx`, schemas. Rebuild só de `routing-engine` + `orchestrator-bridge`.

`hold_ttl_s`: default **90 s** (poll da inbox = 2-3 s; 90 s cobre reload do Console e latência do
webhook). Definir pela mesma via do `_claim_lease_s` já existente no `Router`, para não abrir frente
nova de config.

---

## 6. Validação

### 6.1 pytest — `routing-engine/src/plughub_routing/tests/test_instance_semaphore.py`

O arquivo **já existe** (teste de integração com Redis real, skip automático se indisponível). Casos
a acrescentar:

1. **hand-off feliz** — claim(origem) → `swap_to_hold` → `SCARD` continua 1 → claim do wrap-up com
   `can_inherit_hold=True` → `SCARD` continua 1 (net 0, nunca 0 nem 2).
2. **ordem invertida** — claim do wrap-up ANTES do `swap_to_hold`, `max=1` → `-1` (esperado: o item
   fica na inbox) — e, após o swap, o retry herda. Documenta que a inversão não corrompe a contagem.
3. **push não herda** — com hold vivo e `max=1`, claim de push (`can_inherit_hold=False`) → `-1`.
4. **hold expirado** — hold com `expires_at_ms` no passado → claim de push **passa** e o hold some.
5. **idempotência do swap** — `swap_to_hold` 2× (redelivery de `agent_done`) → 1 hold, `SCARD` 1.
6. **swap após consumo** — wrap-up já herdou; `swap_to_hold` redelivered → **não** cria hold novo.
7. **concorrência** — `swap_to_hold` × N claims em paralelo (`max=1`) → ocupação final ≤ 1.
8. **max_concurrent > 1** — hold ocupa 1 de 3; push ainda entra nas 2 restantes.

Rodar: `docker compose exec -T routing-engine pip install -q pytest pytest-asyncio` e
`REDIS_URL=redis://redis:6379 pytest .../tests/test_instance_semaphore.py`.

### 6.2 smoke — `infra/test/smoke_wrapup_slot_handoff.sh` (novo)

- **S1** `max_concurrent=1`: atendimento real → finaliza → inspeciona o SET (`SMEMBERS`) e confirma
  o membro `__wrapup_hold__::` presente **imediatamente após o close**; wrap-up abre sozinho;
  `SCARD` nunca sai de 1 entre close e auto-claim.
- **S2** anti-regressão do push: durante a janela, injeta um contato push no pool → deve ser
  **enfileirado** (não roteado ao agente).
- **S3** vazamento: fecha o Console logo após o `agent_done` (wrap-up nunca reivindicado) → após
  `hold_ttl_s`, um push volta a ser roteado ao agente.

---

## 7. Riscos e degradações

| Id | Risco | Tratamento |
|---|---|---|
| **R1** | Bug de over/under-alloc no semáforo compartilhado push+pull (componente crítico) | Lua única e idempotente (D4); 8 casos de pytest (§6.1) antes de qualquer E2E |
| **R2** | Vaga presa se o wrap-up nunca chega | Expiração passiva (D5) + smoke S3 |
| **R3** | Espelho `current_sessions` pessimista quando um hold expira passivamente. As-built: com o hold a max=1, `remove_conversation` grava `current_sessions=1` e o `state` permanece `busy` — o que é **o comportamento desejado** enquanto o hold vive (a instância sai do push). Quando o hold expira, o SET já não o conta, mas o espelho só sincroniza no próximo `mark_busy`/`remove_conversation`/`_upsert_instance` (heartbeat do agente) | **v1: aceitar**. Para o humano logado no Console o heartbeat restaura em segundos. Log obrigatório no descarte do hold expirado. Sync explícito = follow-up se aparecer na prática |
| **R4** | Ordem invertida (claim antes do release) segue caindo na inbox | É o comportamento atual e **não regride**; o hold cobre a ordem comum. Fechar totalmente exigiria o hold nascer no `fire_pool_hooks` (antes do `agent_done`), o que reintroduz acoplamento hook↔routing |

**Degradação nunca silenciosa** (CLAUDE.md § Postura de Engenharia): logar `hold criado`
(`swap_to_hold`, com origem e `expires_at`), `hold herdado` (com a origem do membro consumido) e
`hold expirado descartado` (com a idade). Um hand-off que não acontece tem que **nomear o motivo**.

---

## 8. Deploy

`build routing-engine orchestrator-bridge && up -d --force-recreate routing-engine orchestrator-bridge`.
Sem mudança de YAML de skill, de schema Zod ou de UI. Sem migration.

---

## 9. Não-objetivos

- Plumbing de `origin_session_id` até o `contact_data` (D2 — o hold é fungível).
- Auto-claim instantâneo (`refreshSignal` no `conversation.assigned`) — **depois** desta Phase 2 (§1).
- UI para a config `dispatch: inline|detached` do hook (dívida "config UI-editável" para hooks de pool).
- Reviver o `acw_gate` / `acw_pending` (revertidos na Phase 0). **Cleanup relacionado**:
  `infra/test/smoke_acw_gate.sh` ficou órfão (testa gate inexistente).
