# Batch 2 — Corretude Crítica
**Sessão Claude Code: 1–2 sessões estimadas (~90 min total)**  
**Pacotes afetados:** `skill-flow-engine`, `usage-aggregator`, `workflow-api`  
**Risco de regressão:** Médio — mudanças em lógica de estado e persistência; rodar testes completos após cada fix  
**Verificação final:** `npm test --workspace=packages/skill-flow-engine` + `pytest packages/usage-aggregator/tests/` + `pytest packages/workflow-api/tests/`

---

## Fix B2-01 — suspend.ts: tornar persistSuspend idempotente por resume_token

**Arquivo principal:** `packages/skill-flow-engine/src/steps/suspend.ts`  
**Arquivo secundário:** `packages/workflow-api/src/plughub_workflow_api/db.py` (endpoint persist-suspend)

### O problema
O fluxo atual no `suspend.ts`:

1. **Phase 1 (linha ~120):** escreve `tokenKey` + `sentinelKey: "suspending"` → `saveState`
2. Calcula deadline: `if (ctx.state.results[expiresKey])` reusa; senão chama `ctx.persistSuspend()`
3. Escreve `expiresKey` → `saveState`
4. **Phase 2 (linha ~186):** escreve `sentinelKey: "suspended"` → `saveState`

**O crash perigoso:** entre Phase 1 e o save do `expiresKey` (step 3). Nesse caso:
- `tokenKey` está salvo ✓ — token será reutilizado corretamente
- `expiresKey` não está salvo → na retomada, `persistSuspend()` é chamado novamente
- Se `workflow-api` não tem constraint `UNIQUE` em `resume_token`, dois registros são criados

### Fix parte A — `suspend.ts`
**Não há mudança de lógica no engine.** O engine já reutiliza o token corretamente. O problema é no `persistSuspend` do workflow-api.

Adicionar um comentário explícito na linha onde checa `expiresKey` para documentar a dependência de idempotência:
```typescript
  // NOTE: persistSuspend MUST be idempotent on resume_token — if this process
  // crashes before expiresKey is saved, the next retry will call persistSuspend
  // again with the same resumeToken. The workflow-api must handle this via
  // UPSERT (INSERT ... ON CONFLICT (resume_token) DO NOTHING RETURNING *).
  const result = await ctx.persistSuspend(persistParams)
```

### Fix parte B — `workflow-api/db.py` (a mudança real)

**Localizar** a função que persiste o suspend (provavelmente `persist_suspend` ou similar, chamada pelo endpoint `POST /v1/workflow/instances/{id}/persist-suspend`).

**O que mudar:** A query de INSERT deve usar `ON CONFLICT DO NOTHING RETURNING *` ou equivalente.

Encontrar o INSERT em `db.py` relacionado a suspend/resume_token. Provavelmente algo como:
```python
await conn.execute(
    """
    INSERT INTO workflow.instances (id, status, resume_token, resume_expires_at, ...)
    VALUES ($1, 'suspended', $2, $3, ...)
    """,
    instance_id, resume_token, expires_at, ...
)
```

**Substituir por:**
```python
row = await conn.fetchrow(
    """
    INSERT INTO workflow.instances (id, status, resume_token, resume_expires_at, ...)
    VALUES ($1, 'suspended', $2, $3, ...)
    ON CONFLICT (resume_token) DO UPDATE
      SET updated_at = now()   -- no-op update to allow RETURNING
    RETURNING resume_expires_at
    """,
    instance_id, resume_token, expires_at, ...
)
# Return the stored expires_at (may be from a previous attempt)
return {"resume_expires_at": row["resume_expires_at"].isoformat()}
```

Se a coluna `resume_token` ainda não tem `UNIQUE` constraint, adicionar migration:
```sql
ALTER TABLE workflow.instances
  ADD CONSTRAINT uq_resume_token UNIQUE (resume_token);
```

### Testes a verificar
```bash
# Testes existentes do suspend step (13 assertions)
cd packages/skill-flow-engine && npm test -- --grep "suspend"

# Testes do workflow-api router
cd packages/workflow-api && pytest tests/test_router.py -v -k "persist_suspend"
```

---

## Fix B2-02 — UsageAggregator: substituir pipeline por MULTI/EXEC

**Arquivo:** `packages/usage-aggregator/src/plughub_usage_aggregator/aggregator.py`  
**Localização:** método `_increment_redis()`, linhas 47–66

### O problema
`redis.pipeline()` não é transacional — dois workers podem fazer INCRBY simultâneos no mesmo counter, causando double count em caso de redelivery Kafka.

### Código atual (linhas 47-66)
```python
async def _increment_redis(self, event: UsageEvent) -> None:
    counter_key = f"{event.tenant_id}:usage:current:{event.dimension}"
    cycle_key   = f"{event.tenant_id}:usage:cycle_start"

    pipe = self._redis.pipeline()
    pipe.incrbyfloat(counter_key, event.quantity)
    pipe.expire(counter_key, COUNTER_TTL_SECONDS)
    pipe.set(cycle_key, event.timestamp, ex=COUNTER_TTL_SECONDS, nx=True)
    try:
        await pipe.execute()
    except Exception as exc:
        logger.warning(
            "Redis INCRBY failed for tenant=%s dim=%s: %s",
            event.tenant_id, event.dimension, exc,
        )
```

### O que mudar — substituir o método inteiro
```python
async def _increment_redis(self, event: UsageEvent) -> None:
    """
    Increments the usage counter atomically using MULTI/EXEC.
    
    Using MULTI/EXEC (not pipeline) ensures that concurrent workers
    processing the same event (e.g. Kafka redelivery) cannot interleave
    the INCRBY + EXPIRE pair, which would cause double-counting.
    
    Note: deduplication at the event_id level is enforced by the PostgreSQL
    INSERT (PRIMARY KEY), but Redis counters are the fast-path for quota checks.
    """
    counter_key = f"{event.tenant_id}:usage:current:{event.dimension}"
    cycle_key   = f"{event.tenant_id}:usage:cycle_start"

    try:
        async with self._redis.pipeline(transaction=True) as pipe:
            await pipe.incrbyfloat(counter_key, event.quantity)
            await pipe.expire(counter_key, COUNTER_TTL_SECONDS)
            # SET NX: only sets cycle_start on first event of the cycle
            await pipe.set(cycle_key, event.timestamp, ex=COUNTER_TTL_SECONDS, nx=True)
            await pipe.execute()
    except Exception as exc:
        logger.warning(
            "Redis MULTI/EXEC failed for tenant=%s dim=%s: %s — counter may be stale",
            event.tenant_id, event.dimension, exc,
        )
```

**Nota técnica:** `pipeline(transaction=True)` em `redis-py` (async) emite `MULTI` antes dos commands e `EXEC` no final — isso é a transação real. O `pipeline()` sem `transaction=True` é apenas pipelining de rede.

### Testes a verificar
```bash
cd packages/usage-aggregator && pytest tests/test_aggregator.py -v
# Esperado: 10 testes passando
# O mock de Redis nos testes existentes aceita pipeline(transaction=True) — verificar
```

Se os testes mockam o pipeline com `AsyncMock`, atualizar o mock para:
```python
mock_redis.pipeline.return_value.__aenter__.return_value = mock_pipe
mock_redis.pipeline.assert_called_with(transaction=True)  # adicionar assertion
```

---

## Fix B2-03 — crash_detector.py: adicionar session activity flag

**Arquivo:** `packages/routing-engine/src/plughub_routing/crash_detector.py`  
**Localização:** método que itera `meta.active_conversations` (linhas 138–157)

### O problema
O detector só verifica `{tenant_id}:pipeline:{conversation_id}:running` (pipeline lock). Um agente bloqueado em `BLPOP` (aguardando menu reply) não segura esse lock → detector re-queues a conversa → dois agentes na mesma sessão.

### Estratégia do fix
Introduzir um segundo check: flag de atividade da sessão renovado pelo agente nativo via heartbeat.

**Passo 1 — crash_detector.py:** Adicionar check do activity flag
```python
# Localizar (linha ~138):
        for conversation_id in meta.active_conversations:
            lock_key = f"{tenant_id}:pipeline:{conversation_id}:running"
            engine_lock_exists = await self._redis.exists(lock_key)
```

**Substituir por:**
```python
        for conversation_id in meta.active_conversations:
            lock_key     = f"{tenant_id}:pipeline:{conversation_id}:running"
            activity_key = f"{tenant_id}:session:{conversation_id}:active_instance:{instance_id}"

            engine_lock_exists    = await self._redis.exists(lock_key)
            session_active_exists = await self._redis.exists(activity_key)

            if engine_lock_exists or session_active_exists:
                # Agent still active (executing or waiting for menu reply)
                skipped_locked.append(conversation_id)
                logger.info(
                    "Crash recovery: skipping active session "
                    "tenant=%s instance=%s conversation=%s "
                    "lock=%s activity_flag=%s",
                    tenant_id, instance_id, conversation_id,
                    bool(engine_lock_exists), bool(session_active_exists),
                )
                continue
```

**Passo 2 — agent lifecycle no SDK:** Localizar onde o agente nativo entra em `BLPOP` para aguardar menu result. Antes do BLPOP e durante a espera, renovar o activity flag:

**Arquivo:** `packages/sdk/src/agent-lifecycle.ts` (ou onde estiver o menu wait handler)

```typescript
// Antes do BLPOP:
const activityKey = `${tenantId}:session:${sessionId}:active_instance:${instanceId}`
const ACTIVITY_TTL_S = 30

// Setar flag com TTL
await redis.set(activityKey, "1", "EX", ACTIVITY_TTL_S)

// Renovar a cada 15s enquanto BLPOP está ativo
const renewInterval = setInterval(async () => {
  await redis.expire(activityKey, ACTIVITY_TTL_S)
}, 15_000)

try {
  const result = await redis.blpop(menuResultKey, timeoutSec)
  // ...
} finally {
  clearInterval(renewInterval)
  await redis.del(activityKey)  // cleanup imediato
}
```

### Testes a verificar
```bash
cd packages/routing-engine && pytest tests/ -v -k "crash"
cd packages/sdk && npm test -- --grep "menu"
```

Adicionar test case no crash_detector:
```python
async def test_skips_conversation_with_activity_flag(self):
    """Conversations with active_instance flag must not be re-queued."""
    # Setup: instance has active conversation, activity flag exists, no pipeline lock
    self.redis.exists.side_effect = lambda key: (
        "active_instance" in key  # activity flag exists, pipeline lock does not
    )
    result = await self.detector.recover_instance("tenant_demo", "instance_001")
    assert result.recovered == []
    assert result.skipped_locked == ["conv_001"]
```

---

## Checklist de verificação do Batch 2

```bash
# Skill Flow Engine — suspend tests
cd packages/skill-flow-engine && npm test
# Expected: all 13 suspend assertions pass

# Usage Aggregator
cd packages/usage-aggregator && pytest tests/ -v
# Expected: 10/10 pass; verify pipeline(transaction=True) is called

# Workflow API
cd packages/workflow-api && pytest tests/test_router.py -v
# Expected: 27/27 pass; test persist-suspend with duplicate resume_token

# Routing Engine crash detector
cd packages/routing-engine && pytest tests/ -v -k "crash"

# SDK
cd packages/sdk && npm test

# Integration: se houver E2E runner disponível
# cd e2e-tests && ts-node runner.ts --only 13  # workflow automation
```
