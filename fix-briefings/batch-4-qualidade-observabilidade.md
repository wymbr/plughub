# Batch 4 — Qualidade e Observabilidade
**Sessão Claude Code: 1 sessão estimada (~60 min)**  
**Pacotes afetados:** `analytics-api`, `channel-gateway`, `docker-compose.demo.yml`  
**Risco de regressão:** Baixo — adição de auth em endpoints e health checks, sem mudança de lógica de negócio  
**Verificação final:** `pytest packages/analytics-api/tests/` + `pytest packages/channel-gateway/tests/` + `docker compose config --quiet`

---

## Fix B4-01 — analytics-api/dashboard.py: adicionar RBAC nos endpoints SSE

**Arquivo:** `packages/analytics-api/src/plughub_analytics_api/dashboard.py`  
**Localização:** funções `dashboard_operational()` (linha 50) e `dashboard_sentiment()` (linha 143)

### O problema
Ambos os endpoints aceitam `tenant_id` como query param sem qualquer autenticação. Qualquer cliente pode consultar dados operacionais de qualquer tenant.

### Código atual (linhas 50–54)
```python
@router.get("/operational")
async def dashboard_operational(
    request:   Request,
    tenant_id: str = Query(..., description="Tenant identifier"),
) -> StreamingResponse:
```

### Código atual (linhas 143–147)
```python
@router.get("/sentiment")
async def dashboard_sentiment(
    request:   Request,
    tenant_id: str = Query(..., description="Tenant identifier"),
) -> JSONResponse:
```

### O que mudar — ambas as funções

Verificar como `require_principal` e `Principal` estão implementados em `packages/analytics-api/src/plughub_analytics_api/auth.py`. Deve haver algo como:
```python
def require_principal(authorization: str = Header(None)) -> Principal: ...
class Principal:
    def effective_tenant(self, requested_tenant_id: str) -> str: ...
```

**Aplicar o mesmo padrão do `/admin/consolidated`** em ambos os endpoints:

```python
@router.get("/operational")
async def dashboard_operational(
    request:   Request,
    tenant_id: str      = Query(..., description="Tenant identifier"),
    principal: Principal = Depends(require_principal),   # ADD
) -> StreamingResponse:
    # ADD: enforce tenant isolation
    effective_tenant = principal.effective_tenant(tenant_id)
    # Replace all uses of `tenant_id` in the function body with `effective_tenant`
    ...
```

```python
@router.get("/sentiment")
async def dashboard_sentiment(
    request:   Request,
    tenant_id: str      = Query(..., description="Tenant identifier"),
    principal: Principal = Depends(require_principal),   # ADD
) -> JSONResponse:
    # ADD: enforce tenant isolation
    effective_tenant = principal.effective_tenant(tenant_id)
    # Replace all uses of `tenant_id` in the function body with `effective_tenant`
    ...
```

**Atenção:** O endpoint `/dashboard/metrics` (GET sem SSE) deve ser verificado também — aplicar o mesmo padrão se não tiver auth.

### Impacto no Operator Console
O frontend em `packages/operator-console/src/api/` provavelmente já envia o Bearer token nas chamadas — verificar `usePoolSnapshots` e `useSentimentLive` hooks. Se não enviarem, adicionar `Authorization: Bearer ${token}` nos headers das chamadas SSE e fetch.

### Testes a verificar
```bash
cd packages/analytics-api && pytest tests/test_dashboard.py -v
# Expected: 18/18 pass
```

Adicionar:
```python
async def test_dashboard_operational_requires_auth(client):
    """GET /dashboard/operational without token must return 401."""
    response = await client.get("/dashboard/operational?tenant_id=tenant_demo")
    assert response.status_code == 401

async def test_dashboard_operational_tenant_isolation(client, tenant_a_token, tenant_b_id):
    """Tenant A token cannot access tenant B data."""
    response = await client.get(
        f"/dashboard/operational?tenant_id={tenant_b_id}",
        headers={"Authorization": f"Bearer {tenant_a_token}"},
    )
    assert response.status_code == 403
```

---

## Fix B4-02 — channel-gateway: substituir check EXISTS por XREAD direto no StreamSubscriber

**Arquivo:** `packages/channel-gateway/src/plughub_channel_gateway/stream_subscriber.py`

### O problema
O check `EXISTS` antes do `XREAD` cria uma race condition: o stream pode expirar entre os dois comandos. Se Redis está lento, `graceful degradation` presume que o stream existe e o XREAD bloqueia desnecessariamente.

### Localizar o trecho (linhas ~100–110)
```python
if self._cursor != "0":
    try:
        exists = await self._redis.exists(self._stream_key)
    except asyncio.CancelledError:
        return
    except Exception:
        exists = 1  # graceful degradation
```

### O que mudar

**Remover** o bloco `if self._cursor != "0":` com o check EXISTS. A função `messages()` deve tentar `XREAD` diretamente e tratar a ausência do stream como `StreamExpiredError`:

```python
# ANTES do loop de XREAD, substituir o check EXISTS por:
# (verificar a estrutura exata da função messages() antes de editar)

async def messages(self) -> AsyncIterator[dict]:
    if self._cursor == "0":
        # Fresh connection — stream is guaranteed to exist (just created)
        pass
    else:
        # Reconnection with cursor — verify stream existence via a lightweight XREAD
        # instead of a separate EXISTS call (eliminates race condition window)
        try:
            probe = await self._redis.xrange(
                self._stream_key, self._cursor, "+", count=1
            )
            # If stream doesn't exist, xrange raises ResponseError or returns empty
            # with no key present — treat as expired
        except asyncio.CancelledError:
            return
        except Exception:
            # Stream expired or Redis error
            raise StreamExpiredError(self._stream_key)
    
    # Normal XREAD loop below (unchanged)
    while True:
        ...
```

**Nota:** A lógica exata depende de como `messages()` está estruturada. O objetivo é: **eliminar o `EXISTS` separado** e detectar stream ausente diretamente no primeiro XREAD/XRANGE.

### Testes a verificar
```bash
cd packages/channel-gateway && pytest tests/test_stream_subscriber.py -v
# Expected: 25/25 pass

# Verificar especificamente o reconexão com stream expirado
pytest tests/test_stream_subscriber.py -v -k "expired"
```

---

## Fix B4-03 — docker-compose.demo.yml: adicionar health checks nos Kafka consumers

**Arquivo:** `docker-compose.demo.yml` (raiz do repositório)  
**Serviços sem healthcheck:** `routing-engine`, `session-replayer`, `usage-aggregator`, `skill-flow-worker`

### O que mudar para cada serviço

**Padrão a aplicar** (adaptar conforme porta HTTP de cada serviço):

```yaml
# Para serviços Python com endpoint /health (FastAPI):
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:{PORT}/health"]
  interval: 10s
  timeout: 5s
  retries: 5
  start_period: 20s
```

**Localizar a porta de cada serviço** nos arquivos `main.py` ou `config.py` de cada pacote. Se não houver endpoint HTTP (consumers Kafka puros), usar verificação de processo:

```yaml
# Para workers sem HTTP endpoint:
healthcheck:
  test: ["CMD-SHELL", "ps aux | grep -q '[p]ython.*consumer' || exit 1"]
  interval: 15s
  timeout: 5s
  retries: 3
  start_period: 30s
```

**Routing Engine** (provavelmente tem HTTP, verificar porta):
```yaml
  routing-engine:
    # ... existing config ...
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:3400/health"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 20s
    restart: on-failure
```

**Session Replayer** (Kafka consumer — usar ps check):
```yaml
  session-replayer:
    # ... existing config ...
    healthcheck:
      test: ["CMD-SHELL", "ps aux | grep -q '[p]ython' || exit 1"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 30s
    restart: on-failure
```

**Usage Aggregator** (Kafka consumer — usar ps check):
```yaml
  usage-aggregator:
    # ... existing config ...
    healthcheck:
      test: ["CMD-SHELL", "ps aux | grep -q '[p]ython' || exit 1"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 30s
    restart: on-failure
```

**Skill Flow Worker** (Node Kafka consumer — usar ps check):
```yaml
  skill-flow-worker:
    # ... existing config ...
    healthcheck:
      test: ["CMD-SHELL", "ps aux | grep -q '[n]ode' || exit 1"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 30s
    restart: on-failure
```

### Segundo passo — wiring no e2e-runner

Após adicionar healthchecks, verificar se o serviço `e2e-runner` (ou equivalente) tem `depends_on` com `condition: service_healthy` para esses serviços. Se não tiver, adicionar:

```yaml
  e2e-runner:
    depends_on:
      routing-engine:
        condition: service_healthy
      usage-aggregator:
        condition: service_healthy
      # ... etc
```

### Verificação
```bash
# Validar sintaxe do compose
docker compose -f docker-compose.demo.yml config --quiet

# Subir e verificar health checks
docker compose -f docker-compose.demo.yml up -d
docker compose -f docker-compose.demo.yml ps
# Confirmar que todos os serviços aparecem com "healthy" (não "starting")
```

---

## Fix B4-04 — ClickHouse: auditoria de queries sem FINAL

**Arquivo:** `packages/analytics-api/src/plughub_analytics_api/reports_query.py`

### O problema
Tabelas `ReplacingMergeTree` no ClickHouse têm deduplicação lazy (background). Queries sem `FINAL` podem retornar linhas duplicadas durante períodos de alta ingestão.

### O que fazer — auditoria

Verificar cada query em `reports_query.py` que lê das tabelas principais:

```bash
grep -n "FROM sessions\|FROM agent_events\|FROM messages\|FROM usage_events\|FROM sentiment_events" \
  packages/analytics-api/src/plughub_analytics_api/reports_query.py
```

Para qualquer query que **conta** ou **agrega** dados sem `FINAL`, adicionar:

```sql
-- ANTES:
SELECT count(*) FROM sessions WHERE tenant_id = %(tenant_id)s

-- DEPOIS:
SELECT count(*) FROM sessions FINAL WHERE tenant_id = %(tenant_id)s
```

**Prioridade:** queries de relatório exportável (CSV/XLSX) e de billing têm prioridade. Queries de dashboard em tempo real podem tolerar inconsistência temporária.

### Verificação
```bash
cd packages/analytics-api && pytest tests/test_reports.py -v
# Expected: 26/26 pass
```

---

## Fix B4-05 (Opcional — baixa prioridade) — magic bytes validation no upload

**Arquivo:** `packages/channel-gateway/src/plughub_channel_gateway/upload_router.py`

Este fix foi classificado como 🟢 baixo porque é phase 2 planejada, mas é simples de implementar:

```bash
pip install filetype
```

**Na função que processa o upload, após receber os bytes:**
```python
import filetype

def _validate_magic_bytes(content: bytes, declared_mime: str) -> None:
    """Validate that file content matches its declared MIME type."""
    kind = filetype.guess(content[:1024])  # read only header bytes
    if kind is None:
        raise ValueError("Cannot determine file type from content")
    if kind.mime != declared_mime:
        raise ValueError(
            f"MIME mismatch: declared {declared_mime!r}, "
            f"detected {kind.mime!r}"
        )

# No handler de upload:
try:
    _validate_magic_bytes(file_content, attachment.mime_type)
except ValueError as exc:
    raise HTTPException(status_code=415, detail=str(exc))
```

---

## Checklist de verificação do Batch 4

```bash
# Analytics API
cd packages/analytics-api && pytest tests/ -v
# Expected: 149/149 pass (incluindo novos tests de auth)

# Channel Gateway
cd packages/channel-gateway && pytest tests/ -v
# Expected: 168/168 pass

# Docker Compose sintaxe
docker compose -f docker-compose.demo.yml config --quiet
echo "Exit code: $?"  # deve ser 0

# Verificar health checks funcionando
docker compose -f docker-compose.demo.yml up -d routing-engine usage-aggregator
sleep 30
docker compose -f docker-compose.demo.yml ps
# routing-engine e usage-aggregator devem aparecer como (healthy)

# E2E completo (se infraestrutura disponível)
cd e2e-tests && ts-node runner.ts
# Sem flags = roda cenários default; verificar que não há mais flakiness
```

---

## Resumo do que NÃO está neste batch (backlog)

Os seguintes itens foram identificados no code review mas têm prioridade menor e podem ir para o próximo sprint:

- **C1-03/04/05** — Refinements de schema Zod e testes negativos
- **C2-07** — Auditoria de mudança de permissions[] em reconexão  
- **C3-05** — TTL pool_config (já coberto no Batch 3)
- **C6-04** — Distributed lock no timeout_scanner do workflow-api
- **C7-02/03/05** — Cobertura de testes E2E, logs estruturados, validação de grafo de deps
- **C7-04** — Sincronização do CLAUDE.md com implementação
