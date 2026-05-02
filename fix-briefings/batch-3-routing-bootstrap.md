# Batch 3 — Routing e Bootstrap
**Sessão Claude Code: 1 sessão estimada (~60 min)**  
**Pacotes afetados:** `routing-engine`, `orchestrator-bridge`  
**Risco de regressão:** Médio — mudanças em algoritmo de scoring e reconciliação Redis  
**Verificação final:** `pytest packages/routing-engine/tests/` + `pytest packages/orchestrator-bridge/tests/`

---

## Fix B3-01 — decide.py: implementar tie-breaking por queue_length

**Arquivo:** `packages/routing-engine/src/plughub_routing/decide.py`  
**Localização:** linhas 200–204

### O problema
Quando múltiplos pools têm o mesmo score, a ordenação preserva a ordem de iteração original (não determinística). A spec (CLAUDE.md) exige tie-breaking por menor `queue_length`.

### Código atual (linhas 200-204)
```python
        # Sort by descending score (inf comes first)
        scored_pools.sort(key=lambda x: x[0] if x[0] != float("inf") else 1e18, reverse=True)

        # ── 6. Primary agent and fallback ────────────────────────────────────
        first_score, first_pool, first_instance = scored_pools[0]
```

### O que mudar

**Passo 1:** Antes do sort, construir dict com queue_length por pool_id. Verificar onde `queue_length` está disponível no objeto pool (provavelmente `pool.queue_length` ou via Redis snapshot). Ajustar conforme estrutura real do objeto `scored_pools`:

```python
        # Build queue_length lookup for deterministic tie-breaking.
        # Each element of scored_pools is (score, pool_object, instance_object).
        queue_lengths: dict[str, int] = {}
        for _, pool_obj, _ in scored_pools:
            # Adjust attribute name to match actual pool object structure
            queue_lengths[pool_obj.pool_id] = getattr(pool_obj, "queue_length", 0)

        # Sort by: 1) descending score, 2) ascending queue_length (tie-breaker),
        # 3) pool_id (deterministic last resort)
        scored_pools.sort(
            key=lambda x: (
                -(x[0] if x[0] != float("inf") else 1e18),  # descending score
                queue_lengths.get(x[1].pool_id, 0),          # ascending queue_length
                x[1].pool_id,                                 # deterministic last resort
            )
        )

        # ── 6. Primary agent and fallback ────────────────────────────────────
        first_score, first_pool, first_instance = scored_pools[0]
```

**Nota:** Se `queue_length` não for um atributo direto do pool object, verificar onde o dado está disponível no contexto do `_decide_inner()` (Redis snapshot, parâmetro, etc.) e adaptar o lookup.

### Teste a verificar / adicionar
**Arquivo:** `packages/routing-engine/tests/test_decide.py`

```python
def test_tie_breaking_by_queue_length():
    """Equal-score pools must be broken by queue_length (ascending)."""
    # Two pools with identical score but different queue lengths
    pool_a = make_pool("pool_a", queue_length=5)
    pool_b = make_pool("pool_b", queue_length=2)
    
    # Give both the same score
    result = decide_with_fixed_score([pool_a, pool_b], score=0.5)
    
    assert result.pool_id == "pool_b"  # shorter queue wins

def test_tie_breaking_is_deterministic():
    """Same input must always produce same routing decision."""
    pools = [make_pool(f"pool_{i}", queue_length=3) for i in range(5)]
    results = set()
    for _ in range(100):
        result = decide_with_fixed_score(pools, score=0.5)
        results.add(result.pool_id)
    assert len(results) == 1  # always same pool
```

---

## Fix B3-02 — instance_bootstrap.py: completar campos em _pool_config_diverged

**Arquivo:** `packages/orchestrator-bridge/src/plughub_orchestrator_bridge/instance_bootstrap.py`  
**Localização:** função `_pool_config_diverged()`, linhas 891–909

### O problema
O conjunto `MANAGED` não inclui campos que impactam o algoritmo de scoring (`routing_expression`, `competency_weights`, `aging_factor`, `breach_factor`, `remote_sites`). Mudanças nesses campos no Agent Registry não propagam para o Redis cache.

### Código atual (linhas 898-907)
```python
    MANAGED = {
        "pool_id", "name", "channel_types", "sla_target_ms",
        "max_queue_size", "scoring_weights", "routing_mode",
        "active", "skills",
    }
```

### O que mudar — substituir apenas o set MANAGED
```python
    MANAGED = {
        # Core identity and routing hard-filters
        "pool_id", "name", "channel_types", "active",
        # Queue and SLA parameters
        "sla_target_ms", "max_queue_size",
        # Scoring parameters (impact decide.py / scorer.py)
        "scoring_weights", "routing_mode", "routing_expression",
        "competency_weights", "aging_factor", "breach_factor",
        # Skills and cross-site routing
        "skills", "remote_sites",
    }
```

**Nota:** Verificar nos objetos de pool do Agent Registry quais desses campos existem de fato (alguns podem não estar presentes dependendo da versão atual do schema). Adicionar apenas os que existem — campos ausentes em ambos os dicts retornarão `None == None` (False negativo inócuo).

### Teste a verificar / adicionar
```python
def test_pool_config_diverged_detects_routing_expression_change():
    existing = {"pool_id": "pool_a", "routing_expression": "score * 1.0"}
    desired  = {"pool_id": "pool_a", "routing_expression": "score * 1.5"}
    assert _pool_config_diverged(existing, desired) is True

def test_pool_config_diverged_detects_competency_weights_change():
    existing = {"pool_id": "pool_a", "competency_weights": {"billing": 0.5}}
    desired  = {"pool_id": "pool_a", "competency_weights": {"billing": 0.8}}
    assert _pool_config_diverged(existing, desired) is True
```

---

## Fix B3-03 — instance_bootstrap.py: reduzir TTL de pool_config

**Arquivo:** `packages/orchestrator-bridge/src/plughub_orchestrator_bridge/instance_bootstrap.py`  
**Localização:** linha 71

### O problema
`_POOL_CONFIG_TTL_S = 86400` (24 horas). Pools deletados do Registry permanecem visíveis no Redis (e no dashboard operacional) por até 24 horas.

### Código atual (linha 71)
```python
_POOL_CONFIG_TTL_S     = 86400  # 24h
```

### O que mudar
```python
_POOL_CONFIG_TTL_S     = 3600   # 1h — sufficient for crash recovery, fast enough for cleanup
```

**Justificativa:** O heartbeat a cada 15s renova o TTL de pools ativos. O TTL é apenas um fallback para pools deletados. 1 hora é suficiente para crash recovery e curto o suficiente para que o dashboard reflita a realidade após remoção de pool.

### Verificação
Sem teste específico necessário — é uma constante. Confirmar que nenhum teste existente asserta `86400` como valor esperado:
```bash
grep -r "86400" packages/orchestrator-bridge/tests/
```

---

## Fix B3-04 — instance_bootstrap.py: proteger instâncias busy em _write_instance

**Arquivo:** `packages/orchestrator-bridge/src/plughub_orchestrator_bridge/instance_bootstrap.py`  
**Localização:** loop `to_create` (linhas 350–355) e função `_write_instance()`

### O problema
Após crash com Redis parcialmente atualizado, na reconciliação seguinte, instâncias que estavam com `draining=True` no Redis são re-vistas como ausentes do desired state e sobrescritas com status `ready` — interrompendo sessões ativas.

### O que mudar na função `_write_instance()` (ou no loop de apply)

**Localizar** a função `_write_instance()` que escreve a instância no Redis. Adicionar uma verificação defensiva:

```python
async def _write_instance(
    self, tenant_id: str, instance_id: str, desired: InstanceState
) -> None:
    """
    Writes instance state to Redis.
    
    Safety guard: never overwrite a busy instance with status=ready.
    This protects against post-crash reconciliation overwriting live sessions.
    """
    instance_key = f"{tenant_id}:instance:{instance_id}"
    
    # Check current state before overwriting
    existing_raw = await self._redis.get(instance_key)
    if existing_raw:
        try:
            existing = json.loads(existing_raw)
            if existing.get("status") in ("busy", "paused"):
                # Instance is live — apply pending_update instead of overwriting
                logger.warning(
                    "Bootstrap: instance %s is %s — marking pending_update instead of overwrite",
                    instance_id, existing["status"],
                )
                patch = {**existing, "pending_update": True}
                await self._redis.set(
                    instance_key,
                    json.dumps(patch),
                    ex=_INSTANCE_TTL_S,
                )
                return
        except (json.JSONDecodeError, KeyError):
            pass  # Corrupt state — overwrite is safe
    
    # Normal write
    await self._redis.set(
        instance_key,
        json.dumps(desired.model_dump()),
        ex=_INSTANCE_TTL_S,
    )
```

### Testes a verificar
```bash
cd packages/orchestrator-bridge && pytest tests/ -v

# Verificar especificamente o comportamento de reconciliação
pytest tests/ -v -k "reconcil"
```

Adicionar:
```python
async def test_write_instance_does_not_overwrite_busy():
    """Bootstrap must not overwrite a busy instance with status=ready."""
    existing = {"instance_id": "inst_001", "status": "busy", "session_id": "sess_x"}
    redis.get.return_value = json.dumps(existing)
    
    await bootstrap._write_instance("tenant_demo", "inst_001", desired_ready_state)
    
    written = json.loads(redis.set.call_args[0][1])
    assert written["status"] == "busy"          # status preserved
    assert written["pending_update"] is True    # marked for later update
```

---

## Checklist de verificação do Batch 3

```bash
# Routing Engine — todos os testes
cd packages/routing-engine && pytest tests/ -v
# Confirmar que test_tie_breaking_* passam

# Orchestrator Bridge — todos os testes
cd packages/orchestrator-bridge && pytest tests/ -v

# E2E — bootstrap e reconciliação (se infraestrutura E2E disponível)
cd e2e-tests && ts-node runner.ts --only 15   # instance bootstrap
cd e2e-tests && ts-node runner.ts --only 16   # live reconciliation

# Buscar por regressões de integração no routing
grep -r "queue_length" packages/routing-engine/src/ | head -20
# Confirmar que o campo existe nos objetos de pool onde esperado
```
