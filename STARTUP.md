# PlugHub Pilot — Startup Guide

Complete step-by-step instructions to bring up all services for a live presentation.
Run the steps below **in order**: infrastructure first, then backend services, then frontends.

---

## Prerequisites

| Tool | Minimum version |
|---|---|
| Docker + Docker Compose | 24+ |
| Node.js | 20+ |
| Python | 3.11+ |
| `pip` / `npm` | latest |

All commands are run from the **repository root** (`plughub/`) unless noted otherwise.

---

## Windows 11 — Checklist de inicialização (PowerShell)

> Execute o PowerShell **como Administrador** e navegue até a raiz do repositório antes de começar:
> ```powershell
> cd C:\caminho\para\plughub
> ```
> Abra um **terminal separado** para cada serviço de backend (seção 6).
> O [Windows Terminal](https://aka.ms/terminal) com abas é altamente recomendado.

### Passos únicos — apenas na primeira vez

- [ ] **[1] Definir variáveis de ambiente** — cole o bloco abaixo em cada terminal que abrir

```powershell
# Cole este bloco no início de cada sessão / aba de terminal
$env:PLUGHUB_KAFKA_BROKERS          = "localhost:9092"
$env:PLUGHUB_REDIS_URL              = "redis://localhost:6379"
$env:DATABASE_URL                   = "postgresql://plughub:plughub@localhost:5432/plughub"
$env:PLUGHUB_POSTGRES_DSN           = "postgresql://plughub:plughub@localhost:5432/plughub"
$env:PLUGHUB_CLICKHOUSE_HOST        = "localhost"
$env:PLUGHUB_CLICKHOUSE_PORT        = "8123"
$env:PLUGHUB_CLICKHOUSE_DATABASE    = "plughub"
$env:PLUGHUB_CLICKHOUSE_USER        = "plughub"
$env:PLUGHUB_CLICKHOUSE_PASSWORD    = "plughub"
$env:PLUGHUB_ANTHROPIC_API_KEY      = "sk-ant-SUA_CHAVE_AQUI"
$env:PLUGHUB_JWT_SECRET             = "change-me-for-production"
$env:JWT_SECRET                     = "change-me-for-production"
$env:MCP_PROXY_URL                  = "http://localhost:7422"
$env:SKILL_REGISTRY_URL             = "http://localhost:3400"
$env:PLUGHUB_SKILL_FLOW_SERVICE_URL = "http://localhost:3400"
$env:PLUGHUB_INSTANCE_TTL_SECONDS   = "3600"
```

> **Dica:** salve este bloco em `scripts\set-env.ps1` e execute `. .\scripts\set-env.ps1` em cada terminal.

- [ ] **[2] Inicializar ClickHouse** — criar database e usuário (substitua `plughub-clickhouse` pelo nome real do container se necessário)

```powershell
docker exec -it plughub-clickhouse `
  clickhouse-client `
  --query "CREATE DATABASE IF NOT EXISTS plughub; CREATE USER IF NOT EXISTS plughub IDENTIFIED BY 'plughub'; GRANT ALL ON plughub.* TO plughub"
```

> Se o container exigir autenticação no usuário `default`, adicione `--user default --password SUA_SENHA` antes de `--query`.

- [ ] **[3] Criar tópicos Kafka** — substitua `plughub-kafka` pelo nome real do container se necessário

```powershell
foreach ($topic in @(
    "conversations.inbound",
    "conversations.outbound",
    "conversations.routed",
    "conversations.queued",
    "conversations.events",
    "agent.lifecycle",
    "agent.registry.events",
    "evaluation.events",
    "evaluation.results"
)) {
    docker exec plughub-kafka `
        /opt/kafka/bin/kafka-topics.sh `
        --bootstrap-server localhost:9092 `
        --create --if-not-exists `
        --topic $topic --partitions 3 --replication-factor 1
    Write-Host "$topic OK"
}
```

- [ ] **[4] Instalar pacotes Python**

```powershell
pip install -e packages/channel-gateway        --break-system-packages
pip install -e packages/ai-gateway             --break-system-packages
pip install -e packages/routing-engine         --break-system-packages
pip install -e packages/rules-engine           --break-system-packages
pip install -e packages/orchestrator-bridge    --break-system-packages
pip install -e packages/conversation-writer    --break-system-packages
pip install -e packages/clickhouse-consumer    --break-system-packages
pip install -e packages/dashboard/api          --break-system-packages
```

> Se `--break-system-packages` falhar, use um virtualenv:
> ```powershell
> python -m venv .venv
> .\.venv\Scripts\Activate.ps1
> # depois repita os pip install acima sem a flag
> ```

- [ ] **[5] Instalar e compilar pacotes Node**

```powershell
# Agent Registry
Set-Location packages\agent-registry
npm install
npm run db:generate
npm run build
Set-Location ..\..

# MCP Server
Set-Location packages\mcp-server-plughub
npm install
npm run build
Set-Location ..\..

# Skill Flow Engine
Set-Location packages\skill-flow-engine
npm install
npm run build
Set-Location ..\..

# Skill Flow Service
Set-Location packages\e2e-tests\services\skill-flow-service
npm install
npm run build
Set-Location ..\..\..\..

# Agent Assist UI
Set-Location packages\agent-assist-ui
npm install
Set-Location ..\..

# Dashboard UI
Set-Location packages\dashboard\ui
npm install
Set-Location ..\..\..
```

> Após cada bloco, rode `Get-Location` para confirmar que voltou para a raiz do repositório antes de continuar para o próximo pacote.

- [ ] **[6] Criar schema do banco (Prisma)**

```powershell
Set-Location packages\agent-registry
$env:DATABASE_URL = "postgresql://plughub:plughub@localhost:5432/plughub"

# Primeira vez — sem pasta migrations ainda:
npx prisma migrate dev --name init

# Se der conflito com tabelas existentes, reseta o banco e recria:
# npx prisma migrate reset --force
Set-Location ..\..
```

> `migrate dev` cria `prisma/migrations/TIMESTAMP_init/migration.sql`, aplica no banco e registra o histórico.
> Nas próximas vezes use `npx prisma migrate deploy` para aplicar apenas migrations novas.
> O Conversation Writer e o ClickHouse Consumer criam suas tabelas automaticamente no primeiro start — sem migration manual.

---

### Passos recorrentes — a cada apresentação

- [ ] **[7] Confirmar que a infraestrutura Docker está saudável**

```powershell
docker ps --format "table {{.Names}}`t{{.Status}}"
# Redis, Kafka, PostgreSQL e ClickHouse devem aparecer como "healthy" ou "running"
```

- [ ] **[6.1] Abrir aba → Agent Registry (porta 3300)**

```powershell
. .\scripts\set-env.ps1     # carrega variáveis
$env:PORT = "3300"
node packages\agent-registry\dist\index.js
```

- [ ] **[6.2] Abrir aba → MCP Server (porta 3100)**

```powershell
. .\scripts\set-env.ps1
$env:PORT                = "3100"
$env:REDIS_URL           = $env:PLUGHUB_REDIS_URL
$env:KAFKA_BROKERS       = $env:PLUGHUB_KAFKA_BROKERS
$env:AGENT_REGISTRY_URL  = "http://localhost:3300"
$env:POSTGRES_DSN        = $env:PLUGHUB_POSTGRES_DSN
node packages\mcp-server-plughub\dist\index.js
```

- [ ] **[6.3] Abrir aba → AI Gateway (porta 3200)**

```powershell
. .\scripts\set-env.ps1
uvicorn plughub_ai_gateway.main:app --host 0.0.0.0 --port 3200
```

- [ ] **[6.4] Abrir aba → Routing Engine (consumer Kafka, sem porta HTTP)**

```powershell
. .\scripts\set-env.ps1
$env:PLUGHUB_AGENT_REGISTRY_URL     = "http://localhost:3300"
$env:PLUGHUB_MCP_SERVER_URL         = "http://localhost:3100"
plughub-routing
```

- [ ] **[6.5a] Abrir aba → Rules Engine API (porta 3201)**

```powershell
. .\scripts\set-env.ps1
uvicorn plughub_rules.api:app --host 0.0.0.0 --port 3201
```

- [ ] **[6.5b] Abrir aba → Rules Engine Monitor (consumer Kafka)**

```powershell
. .\scripts\set-env.ps1
python -m plughub_rules.main
```

- [ ] **[6.6] Abrir aba → Channel Gateway (porta 8010)**

```powershell
. .\scripts\set-env.ps1
$env:PLUGHUB_ROUTING_ENGINE_URL  = "http://localhost:3200"
# Pool de entrada — o canal webchat serve o pool de IA do SAC
# Quando o cliente conecta, o adapter publica automaticamente um ConversationInboundEvent
# com pool_id = sac_ia, eliminando o passo manual de publicação via kafka-console-producer.
$env:PLUGHUB_ENTRY_POINT_POOL_ID = "sac_ia"
$env:PLUGHUB_TENANT_ID           = "default"
plughub-channel-gateway
```

- [ ] **[6.7] Abrir aba → Conversation Writer (consumer Kafka, sem porta HTTP)**

```powershell
. .\scripts\set-env.ps1
plughub-conversation-writer
```

- [ ] **[6.8] Abrir aba → Skill Flow Service (porta 3400)**

```powershell
. .\scripts\set-env.ps1
$env:PORT             = "3400"
$env:REDIS_URL        = $env:PLUGHUB_REDIS_URL
$env:MCP_SERVER_URL   = "http://localhost:3100"
$env:AI_GATEWAY_URL   = "http://localhost:3200"
node packages\e2e-tests\services\skill-flow-service\dist\index.js
```

- [ ] **[6.9] Abrir aba → ClickHouse Consumer (consumer Kafka, sem porta HTTP)**

```powershell
. .\scripts\set-env.ps1
plughub-clickhouse-consumer
```

- [ ] **[6.10] Abrir aba → Orchestrator Bridge (consumer Kafka, sem porta HTTP)**

O Orchestrator Bridge liga o Routing Engine ao Skill Flow Service (agentes AI) e ao Agent Assist UI (agentes humanos).
A decisão de ativação é determinada pelo campo `framework` do tipo de agente no Agent Registry:

| `framework`       | Ação do Bridge                                                              |
|-------------------|-----------------------------------------------------------------------------|
| `plughub-native`  | Busca o skill flow no Agent Registry → `POST /execute` no Skill Flow Service |
| `human`           | Publica `conversation.assigned` no Redis pub/sub → Agent Assist UI recebe   |
| outros (LangGraph, CrewAI…) | Apenas loga — o runtime externo gerencia a ativação            |

```powershell
. .\scripts\set-env.ps1
$env:SKILL_FLOW_URL      = "http://localhost:3400"
$env:AGENT_REGISTRY_URL  = "http://localhost:3300"
plughub-bridge
```

> O Bridge **não** mantém nenhuma lista local de tipos de agente ou flags AI/humano.
> Tudo deriva de `GET /v1/agent-types/{id}` no Agent Registry (campo `framework`).
> Fallback automático para YAML local (`packages/skill-flow-engine/skills/`) se o Registry estiver indisponível ou o agent type não estiver cadastrado.

- [ ] **[6.11] Abrir aba → Dashboard API (porta 8090)**

```powershell
. .\scripts\set-env.ps1
uvicorn plughub_dashboard_api.app:app --host 0.0.0.0 --port 8090
```

- [ ] **[7.1] Abrir aba → Agent Assist UI (porta 5173)**

```powershell
Set-Location packages\agent-assist-ui
npm run dev
```

Acesse: `http://localhost:5173?agent=NOME&pool=sac_humano`

- [ ] **[7.2] Abrir aba → Dashboard UI (porta 5174)**

```powershell
Set-Location packages\dashboard\ui
npm run dev
```

Acesse: `http://localhost:5174`

- [ ] **[8] Verificar que tudo está no ar**

```powershell
# Redis
docker exec plughub-redis redis-cli ping                          # → PONG

# ClickHouse
Invoke-WebRequest http://localhost:8123/ping | Select-Object -ExpandProperty Content   # → Ok.

# Backends
foreach ($url in @(
    "http://localhost:3300/v1/health",   # agent-registry
    "http://localhost:3100/health",   # mcp-server
    "http://localhost:3200/v1/health",   # ai-gateway
    "http://localhost:3201/health",   # rules-engine
    "http://localhost:8010/health",   # channel-gateway
    "http://localhost:3400/health",   # skill-flow-service
    "http://localhost:8090/health"    # dashboard-api
)) {
    try   { $r = Invoke-WebRequest $url -TimeoutSec 3; Write-Host "$url → $($r.StatusCode)" }
    catch { Write-Host "$url → FALHOU" }
}

# Tópicos Kafka
docker exec plughub-kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list
```

---

## Mapa de portas

| # | Serviço | Porta | Tipo |
|---|---|---|---|
| — | Redis | 6379 | Infra |
| — | Kafka | 9092 | Infra |
| — | PostgreSQL | 5432 | Infra |
| — | ClickHouse HTTP | 8123 | Infra |
| — | Kafka UI | 8080 | Debug UI |
| 6.1 | agent-registry | 3300 | HTTP REST |
| 6.2 | mcp-server-plughub | 3100 | HTTP / SSE |
| 6.3 | ai-gateway | 3200 | HTTP REST |
| 6.5 | rules-engine API | 3201 | HTTP REST |
| 6.6 | channel-gateway | 8010 | HTTP + WebSocket |
| 6.8 | skill-flow-service | 3400 | HTTP REST |
| 6.10 | dashboard-api | 8090 | HTTP REST |
| 7.1 | agent-assist-ui | 5173 | Browser |
| 7.2 | dashboard-ui | 5174 | Browser |

Serviços 6.4, 6.7, 6.9 e 6.10 são consumers Kafka — sem porta HTTP.

---

## Encerrar tudo

```powershell
# Para todos os processos Node e Python do plughub
Get-Process -Name "node","python","uvicorn" -ErrorAction SilentlyContinue | Stop-Process -Force

# Para a infra Docker (preserva dados)
docker compose -f docker-compose.infra.yml down

# Para apagar dados também (Redis, Kafka, Postgres, ClickHouse volumes)
docker compose -f docker-compose.infra.yml down -v
```

---

## Troubleshooting

**Kafka consumer lag** — verifique o Kafka UI em `http://localhost:8080` → Consumer Groups.

**ClickHouse unhealthy no Docker Desktop (Windows/WSL2)** — o container exibe repetidamente `get_mempolicy: Operation not permitted` nos logs e o health check marca o container como unhealthy. Isso é um bloqueio de syscall do WSL2, não uma falha real. Verifique se o servidor responde antes de tomar qualquer ação:
```powershell
Invoke-WebRequest http://localhost:8123/ping | Select-Object -ExpandProperty Content
# → Ok.   (servidor funcionando normalmente, ignore o status unhealthy)
```
Se retornar `Ok.` pode continuar — o ClickHouse está operacional. Se não responder, adicione `security_opt` no `docker-compose.infra.yml` para liberar a syscall bloqueada:
```yaml
clickhouse:
  security_opt:
    - seccomp:unconfined
```
Depois recrie o container:
```powershell
docker compose -f docker-compose.infra.yml up -d --force-recreate clickhouse
```

**ClickHouse connection refused** — confirme que o usuário `plughub` existe:
```powershell
docker exec -it plughub-clickhouse `
  clickhouse-client --user plughub --password plughub --query "SELECT 1"
```

**Prisma migration fails** — reset e re-migrate:
```powershell
Set-Location packages\agent-registry
$env:DATABASE_URL = "postgresql://plughub:plughub@localhost:5432/plughub"
npx prisma migrate reset --force
Set-Location ..\..
```

**Port already in use** — encontre e encerre o processo conflitante:
```powershell
# substitua 3300 pela porta em conflito
Get-Process -Id (Get-NetTCPConnection -LocalPort 3300 -State Listen).OwningProcess | Stop-Process -Force
```

**`ModuleNotFoundError: No module named 'plughub_ai_gateway'`** — o diretório fonte do ai-gateway tinha hífen (`plughub-ai-gateway`) em vez de underscore (`plughub_ai_gateway`), impedindo o Python de encontrar o módulo após o `pip install -e`. O repositório já foi corrigido. Se o erro ocorrer, verifique se o diretório existe com underscore e reinstale:
```powershell
# Confirma que o diretório correto existe
Test-Path packages\ai-gateway\src\plughub_ai_gateway   # deve retornar True

# Reinstala
pip install -e packages\ai-gateway --break-system-packages
```

**pip --break-system-packages não aceito** — use virtualenv:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
# repita os pip install sem a flag
```

**docker exec com nome de container errado** — liste os containers ativos:
```powershell
docker ps --format "table {{.Names}}`t{{.Ports}}"
```

**`@plughub/schemas` ou `@plughub/skill-flow-engine` — I/O error (symlinks quebrados no Windows)** — o npm no Windows cria symlinks que o WSL2/Linux não consegue ler. Corrija copiando o diretório manualmente:
```powershell
# MCP Server
Remove-Item -Force packages\mcp-server-plughub\node_modules\@plughub\schemas
Copy-Item -Recurse packages\schemas packages\mcp-server-plughub\node_modules\@plughub\schemas

# Skill Flow Engine
Remove-Item -Force packages\skill-flow-engine\node_modules\@plughub\schemas
Copy-Item -Recurse packages\schemas packages\skill-flow-engine\node_modules\@plughub\schemas

# Skill Flow Service
Remove-Item -Force packages\e2e-tests\services\skill-flow-service\node_modules\@plughub\schemas
Copy-Item -Recurse packages\schemas packages\e2e-tests\services\skill-flow-service\node_modules\@plughub\schemas

Remove-Item -Recurse -Force packages\e2e-tests\services\skill-flow-service\node_modules\@plughub\skill-flow-engine
New-Item -ItemType Directory -Force packages\e2e-tests\services\skill-flow-service\node_modules\@plughub\skill-flow-engine
Copy-Item packages\skill-flow-engine\package.json packages\e2e-tests\services\skill-flow-service\node_modules\@plughub\skill-flow-engine\
Copy-Item -Recurse packages\skill-flow-engine\dist packages\e2e-tests\services\skill-flow-service\node_modules\@plughub\skill-flow-engine\dist

# Dependências de @plughub/skill-flow-engine que o npm não instala automaticamente
# no service (jsonpath-plus é requerido por steps/task.js e steps/choice.js)
Copy-Item -Recurse packages\skill-flow-engine\node_modules\jsonpath-plus packages\e2e-tests\services\skill-flow-service\node_modules\jsonpath-plus
```
Depois recompile os pacotes afetados: `npm run build` em cada um.

---

## Demo Completo — Fluxo AI → Escalar → Agente Humano

Este roteiro testa o fluxo ponta-a-ponta: cliente conecta → agente AI atende → escala para pool humano → agente humano vê a conversa no Agent Assist UI.

### Pré-requisitos

Todos os serviços dos passos 6.1–6.11 devem estar rodando, incluindo o **Orchestrator Bridge (6.10)**.

### Convenção de nomenclatura de pools

| Pool | Serviço | Recurso | Entry point |
|---|---|---|---|
| `sac_ia` | SAC | IA (plughub-native) | webchat configurado com `PLUGHUB_ENTRY_POINT_POOL_ID=sac_ia` |
| `sac_humano` | SAC | Humano | escalação via step `escalate → target.pool: sac_humano` |

Pools de IA e humano do mesmo serviço são **sempre separados** — garante que a escalação nunca re-aloca um agente IA para uma sessão já escalada para humano.

### Passo 1 — Registrar pools, skill e tipos de agentes

Abra uma aba PowerShell e execute:

```powershell
. .\scripts\set-env.ps1

# 1a. Registrar pool IA do SAC
Invoke-RestMethod -Method POST http://localhost:3300/v1/pools -ContentType "application/json" `
  -Headers @{"x-tenant-id"="default"} -Body '{
  "pool_id": "sac_ia",
  "name": "SAC — IA",
  "channel_types": ["chat"],
  "sla_target_ms": 480000,
  "routing_expression": {
    "weight_sla": 1.0, "weight_wait": 0.8,
    "weight_tier": 0.6, "weight_churn": 0.9, "weight_business": 0.4
  }
}' | ConvertTo-Json

# 1b. Registrar pool Humano do SAC
Invoke-RestMethod -Method POST http://localhost:3300/v1/pools -ContentType "application/json" `
  -Headers @{"x-tenant-id"="default"} -Body '{
  "pool_id": "sac_humano",
  "name": "SAC — Humano",
  "channel_types": ["chat"],
  "sla_target_ms": 480000,
  "routing_expression": {
    "weight_sla": 1.0, "weight_wait": 0.8,
    "weight_tier": 0.6, "weight_churn": 0.9, "weight_business": 0.4
  },
  "supervisor_config": { "enabled": true }
}' | ConvertTo-Json

# 1c. Registrar skill com o flow do agente IA
#     O Orchestrator Bridge busca GET /v1/skills/skill_sac_v1 para obter o flow.
#     O step menu captura a intenção do cliente antes de saudar e escalar.
#     Use PUT para upsert (cria se não existe, substitui se já existe).
Invoke-RestMethod -Method PUT http://localhost:3300/v1/skills/skill_sac_v1 -ContentType "application/json" `
  -Headers @{"x-tenant-id"="default"} -Body '{
  "skill_id":    "skill_sac_v1",
  "name":        "Skill SAC v1",
  "version":     "1.0",
  "description": "Skill de atendimento SAC — captura intenção, sauda o cliente e escala para agente humano.",
  "classification": { "type": "orchestrator" },
  "instruction": { "prompt_id": "skill_sac_v1", "language": "pt-BR" },
  "flow": {
    "entry": "captura_intencao",
    "steps": [
      {
        "id": "captura_intencao",
        "type": "menu",
        "prompt": "como posso te ajudar ?",
        "interaction": "text",
        "output_as": "intencao_cliente",
        "timeout_s": 300,
        "on_success": "saudacao",
        "on_failure": "finalizar",
        "on_timeout": "saudacao",
        "on_disconnect": "finalizar"
      },
      {
        "id": "saudacao",
        "type": "notify",
        "message": "Olá! Sou o assistente virtual da PlugHub. Vou conectar você a um especialista.",
        "channel": "session",
        "on_success": "escalar",
        "on_failure": "escalar"
      },
      {
        "id": "escalar",
        "type": "escalate",
        "target": { "pool": "sac_humano" },
        "context": "pipeline_state",
        "error_reason": "escalated_to_human"
      },
      {
        "id": "finalizar",
        "type": "complete",
        "outcome": "resolved"
      }
    ]
  }
}' | ConvertTo-Json

# 1d. Registrar tipo de agente IA
#     framework = "plughub-native" → Bridge ativa via Skill Flow Service
Invoke-RestMethod -Method POST http://localhost:3300/v1/agent-types -ContentType "application/json" `
  -Headers @{"x-tenant-id"="default"} -Body '{
  "agent_type_id": "agente_sac_ia_v1",
  "name": "Agente SAC IA v1",
  "framework": "plughub-native",
  "pools": ["sac_ia"],
  "skills": [{ "skill_id": "skill_sac_v1" }],
  "max_concurrent_sessions": 10,
  "execution_model": "stateless"
}' | ConvertTo-Json

# 1e. Registrar tipo de agente humano
#     framework = "human" → Bridge publica conversation.assigned via Redis pub/sub
Invoke-RestMethod -Method POST http://localhost:3300/v1/agent-types -ContentType "application/json" `
  -Headers @{"x-tenant-id"="default"} -Body '{
  "agent_type_id": "agente_sac_humano_v1",
  "name": "Atendente SAC",
  "framework": "human",
  "pools": ["sac_humano"],
  "skills": [],
  "max_concurrent_sessions": 3,
  "execution_model": "stateful"
}' | ConvertTo-Json
```

### Passo 2 — Registrar agente IA no Redis (para Routing Engine)

```powershell
. .\scripts\set-env.ps1

# Pool IA do SAC (TTL 24h — não expira entre reinicializações)
docker exec plughub-redis redis-cli SET "default:pool_config:sac_ia" '{"pool_id":"sac_ia","tenant_id":"default","channel_types":["webchat"],"sla_target_ms":480000,"routing_expression":{"weight_sla":1.0,"weight_wait":0.8,"weight_tier":0.6,"weight_churn":0.9,"weight_business":0.4}}' EX 86400

# Instância do agente IA (TTL 1h — suficiente para o demo sem heartbeat real)
$ts = (Get-Date -Format "o")
$iaJson = '{"instance_id":"sac-ia-001","agent_type_id":"agente_sac_ia_v1","tenant_id":"default","pool_id":"sac_ia","pools":["sac_ia"],"execution_model":"stateless","max_concurrent":10,"current_sessions":0,"state":"ready","registered_at":"' + $ts + '"}'
docker exec plughub-redis redis-cli SET "default:instance:sac-ia-001" $iaJson EX 3600

docker exec plughub-redis redis-cli SADD "default:pool:sac_ia:instances" "sac-ia-001"
docker exec plughub-redis redis-cli HSET "default:routing:instance:sac-ia-001:meta" pools '["sac_ia"]' agent_type_id "agente_sac_ia_v1"
```

### Passo 3 — Registrar agente humano no Redis (para Routing Engine)

```powershell
. .\scripts\set-env.ps1

# Pool Humano do SAC (TTL 24h)
docker exec plughub-redis redis-cli SET "default:pool_config:sac_humano" '{"pool_id":"sac_humano","tenant_id":"default","channel_types":["webchat"],"sla_target_ms":480000,"routing_expression":{"weight_sla":1.0,"weight_wait":0.8,"weight_tier":0.6,"weight_churn":0.9,"weight_business":0.4}}' EX 86400

# Instância do agente humano (TTL 1h — suficiente para o demo sem heartbeat real)
$ts = (Get-Date -Format "o")
$humJson = '{"instance_id":"sac-humano-001","agent_type_id":"agente_sac_humano_v1","tenant_id":"default","pool_id":"sac_humano","pools":["sac_humano"],"execution_model":"stateful","max_concurrent":3,"current_sessions":0,"state":"ready","registered_at":"' + $ts + '"}'
docker exec plughub-redis redis-cli SET "default:instance:sac-humano-001" $humJson EX 3600

docker exec plughub-redis redis-cli SADD "default:pool:sac_humano:instances" "sac-humano-001"
docker exec plughub-redis redis-cli HSET "default:routing:instance:sac-humano-001:meta" pools '["sac_humano"]' agent_type_id "agente_sac_humano_v1"
```

> **Importante:** o TTL da instância é 300 segundos. Se o routing falhar com "no agents available", repita o bloco acima para renovar as chaves.

### Passo 4 — Abrir o Agent Assist UI para o agente humano

Abra o Agent Assist UI **antes** de iniciar a conversa do cliente:

```
http://localhost:5173?session_id=&contact_id=&agent=Carlos&pool=sac_humano
```

O `session_id` não é fixo — o Agent Assist UI ficará aguardando o evento `conversation.assigned` que chega via Redis pub/sub quando o Routing Engine alocar a sessão para o pool `sac_humano`. O status deve aparecer como **connected**.

### Passo 5 — Simular cliente conectando via WebSocket

```powershell
# Instala wscat se não tiver
npm install -g wscat

# Conecta como cliente
wscat -c "ws://localhost:8010/ws/chat"
```

Ao conectar, o channel-gateway responde com:
```json
{"type":"connection.accepted","contact_id":"<UUID>","session_id":"<UUID>"}
```

**Neste momento o channel-gateway já publica automaticamente o `ConversationInboundEvent` com `pool_id: "sac_ia"` para o Routing Engine** — não é mais necessário publicar manualmente.

### Passo 6 — Observar o fluxo automático

Monitore os logs das abas dos serviços. A sequência esperada é:

1. **Channel Gateway** → `routing_event published: session=<ID> pool=sac_ia`
2. **Routing Engine** → `Routed session=<ID> → instance=sac-ia-001 pool=sac_ia`
3. **Orchestrator Bridge** → `Routing: session=<ID> agent=agente_sac_ia_v1 pool=sac_ia framework=plughub-native`
4. **Skill Flow Service** → executa `agente_sac_ia_v1`
5. **MCP Server** → `notification_send` → publica `message.text` em `conversations.outbound`
6. **Channel Gateway** → entrega mensagem ao cliente via WebSocket
7. No `wscat`, o cliente recebe: `{"type":"message.text","text":"Olá! Sou o assistente virtual..."}`
8. **Skill Flow Service** → executa step `escalate` → chama `conversation_escalate`
9. **MCP Server** → `conversation_escalate` → publica em `conversations.inbound` com `pool_id: sac_humano`
10. **Routing Engine** → `Routed session=<ID> → instance=sac-humano-001 pool=sac_humano`
11. **Orchestrator Bridge** → `Routing: session=<ID> agent=agente_sac_humano_v1 pool=sac_humano framework=human`
12. **Agent Assist UI** (aba do navegador) → recebe evento `conversation.assigned`

### Passo 7 — Conversar como agente humano

Após o `conversation.assigned` aparecer no Agent Assist UI, o agente humano pode enviar mensagens diretamente pelo campo de texto.

Para enviar uma mensagem como cliente no `wscat`:
```json
{"type":"message.text","text":"Quero cancelar meu plano"}
```

---

## Notas sobre o fluxo de roteamento

### Dois formatos no mesmo tópico `conversations.inbound`

O tópico `conversations.inbound` carrega dois tipos de evento com schemas distintos:

**`ConversationInboundEvent`** — evento de roteamento, consumido pelo Routing Engine:
```json
{
  "session_id":  "...",
  "tenant_id":   "default",
  "customer_id": "...",
  "channel":     "webchat",
  "pool_id":     "sac_ia",
  "started_at":  "2026-...",
  "elapsed_ms":  0
}
```
Publicado pelo **Channel Gateway** na conexão do cliente (com `pool_id` do entry point)
e pelo **MCP Server** (`conversation_escalate`) quando o agente IA escala para humano (com `pool_id` do pool humano).

**`NormalizedInboundEvent`** — mensagem do cliente, consumida pelo Orchestrator Bridge:
```json
{
  "contact_id": "...",
  "session_id": "...",
  "author":     { "type": "customer" },
  "content":    { "type": "text", "text": "..." }
}
```
Publicado pelo Channel Gateway a cada mensagem recebida do cliente.

O Routing Engine ignora silenciosamente eventos que não passam em `ConversationInboundEvent.model_validate()` (presença do campo `author` é o discriminador — o Routing Engine descarta eventos com esse campo).
O Orchestrator Bridge usa a presença de `author` para distinguir mensagens de cliente dos eventos de roteamento.

---

## Demo Completo 2 — Fluxo LLM com Sentimento em Tempo Real

Este roteiro usa a `skill_demo_chat_v1` — um orquestrador que conversa com o cliente via LLM (step `reason`), analisa sentimento, e pode escalar para humano. O Agent Assist UI exibe o sentimento detectado em tempo real na strip acima do histórico de mensagens.

### Diferença em relação ao Demo 1

| | Demo 1 (`skill_sac_v1`) | Demo 2 (`skill_demo_chat_v1`) |
|---|---|---|
| Interação IA | Saudação fixa + escalação | Loop de conversa real via LLM |
| Sentimento | Não alimentado | Atualizado a cada turno pelo AI Gateway |
| Escalação | Automática após saudação | Condicional — só se `deve_escalar=true` |
| Seed | Manual (passos 1a–1e) | Script `seed_demo.ts` |

### Pré-requisitos

Todos os serviços dos passos 6.1–6.11 devem estar rodando.
`PLUGHUB_ANTHROPIC_API_KEY` deve estar configurada — o step `reason` chama o AI Gateway.

### Passo 1 — Seed automático

```powershell
. .\scripts\set-env.ps1
$env:AGENT_REGISTRY_URL = "http://localhost:3300"
$env:TENANT_ID          = "default"

npx ts-node packages\e2e-tests\fixtures\seed_demo.ts
```

O seed cria:
- Pool `demo_ia` — entry point do orquestrador
- Pool `suporte_humano` — destino das escalações
- Skill `skill_demo_chat_v1` — flow com reason + sentiment + loop
- AgentType `orquestrador_demo_v1` (`framework: plughub-native`, pool: `demo_ia`)
- AgentType `agente_suporte_humano_v1` (`framework: human`, pool: `suporte_humano`)

### Passo 2 — Registrar instâncias no Redis

```powershell
. .\scripts\set-env.ps1

docker exec plughub-redis redis-cli SET "default:pool_config:demo_ia" `
  '{"pool_id":"demo_ia","tenant_id":"default","channel_types":["webchat"],"sla_target_ms":480000,"routing_expression":{"weight_sla":1.0,"weight_wait":0.8,"weight_tier":0.6,"weight_churn":0.9,"weight_business":0.4}}' EX 86400

$ts = (Get-Date -Format "o")
$iaJson = '{"instance_id":"demo-ia-001","agent_type_id":"orquestrador_demo_v1","tenant_id":"default","pool_id":"demo_ia","pools":["demo_ia"],"execution_model":"stateless","max_concurrent":10,"current_sessions":0,"state":"ready","registered_at":"' + $ts + '"}'
docker exec plughub-redis redis-cli SET "default:instance:demo-ia-001" $iaJson EX 3600
docker exec plughub-redis redis-cli SADD "default:pool:demo_ia:instances" "demo-ia-001"
docker exec plughub-redis redis-cli HSET "default:routing:instance:demo-ia-001:meta" pools '["demo_ia"]' agent_type_id "orquestrador_demo_v1"

docker exec plughub-redis redis-cli SET "default:pool_config:suporte_humano" `
  '{"pool_id":"suporte_humano","tenant_id":"default","channel_types":["webchat"],"sla_target_ms":300000,"routing_expression":{"weight_sla":1.0,"weight_wait":0.8,"weight_tier":0.6,"weight_churn":0.9,"weight_business":0.4}}' EX 86400

$humJson = '{"instance_id":"demo-humano-001","agent_type_id":"agente_suporte_humano_v1","tenant_id":"default","pool_id":"suporte_humano","pools":["suporte_humano"],"execution_model":"stateful","max_concurrent":3,"current_sessions":0,"state":"ready","registered_at":"' + $ts + '"}'
docker exec plughub-redis redis-cli SET "default:instance:demo-humano-001" $humJson EX 3600
docker exec plughub-redis redis-cli SADD "default:pool:suporte_humano:instances" "demo-humano-001"
docker exec plughub-redis redis-cli HSET "default:routing:instance:demo-humano-001:meta" pools '["suporte_humano"]' agent_type_id "agente_suporte_humano_v1"
```

### Passo 3 — Channel Gateway com entry point demo_ia

Reinicie o Channel Gateway (aba 6.6) apontando para o pool do orquestrador:

```powershell
. .\scripts\set-env.ps1
$env:PLUGHUB_ROUTING_ENGINE_URL  = "http://localhost:3200"
$env:PLUGHUB_ENTRY_POINT_POOL_ID = "demo_ia"
$env:PLUGHUB_TENANT_ID           = "default"
plughub-channel-gateway
```

### Passo 4 — Abrir o Agent Assist UI para o agente humano

```
http://localhost:5173?agent=Carlos&pool=suporte_humano
```

O UI fica no lobby aguardando `conversation.assigned`. Quando o orquestrador escalar (`deve_escalar=true`), a sessão é atribuída automaticamente.

### Passo 5 — Simular cliente e conversar com a IA

```powershell
wscat -c "ws://localhost:8010/ws/chat"
```

Responda ao prompt da IA com texto livre:
```json
{"type":"message.text","text":"Quero cancelar meu plano"}
```

O Skill Flow executa o loop: `coleta_msg` → `analisar` (AI Gateway) → `responder` ou `escalar`.

### O que observar no Agent Assist UI

- **Strip de sentimento** (acima das mensagens): ponto colorido + percentual + tendência + intenção + flags
- **Aba Estado**: gráfico de trajetória do sentimento por turno, histórico de intenções
- **Histórico completo**: cliente (cinza), IA (violeta), agente humano (azul), sistema/menu (âmbar)
- **Menu render**: quando a IA envia botões, as opções aparecem listadas na mensagem de sistema

### Nota sobre timeout_s

O campo `timeout_ms` foi renomeado para `timeout_s` (segundos, inteiro >= 0). O valor `0` significa espera indefinida — o menu só desbloqueia quando o cliente responde ou desconecta. A skill `skill_sac_v1` do Demo 1 já usa `timeout_s: 300` neste documento.

---

## Startup Simplificado — Ubuntu + PM2

Para ambientes Ubuntu, o PlugHub pode ser iniciado com três comandos após configurar a infraestrutura (Docker). O PM2 gerencia todos os 13 serviços como processos persistentes com restart automático e logs centralizados.

### Pré-requisitos

- Ubuntu 22.04+ (ou WSL2)
- Node.js 20+ (`nvm install 20` ou `apt install nodejs`)
- Python 3.11+ (`apt install python3.11 python3.11-venv python3-pip`)
- Docker + Compose plugin (`https://docs.docker.com/engine/install/ubuntu/`)
- Infra rodando: `docker compose -f docker-compose.infra.yml up -d`

### Primeiro uso (executar uma vez)

**1. Configure sua API key**

Edite `scripts/linux/set-env.sh` e substitua `sk-ant-SUA_CHAVE_AQUI` pela sua chave real:

```bash
nano scripts/linux/set-env.sh
# Altere: export PLUGHUB_ANTHROPIC_API_KEY="sk-ant-SUA_CHAVE_AQUI"
```

**2. Execute o setup**

```bash
bash scripts/linux/setup.sh
```

O script instala PM2, compila os pacotes TypeScript, instala pacotes Python, cria os Kafka topics, aplica o schema do ClickHouse e roda as migrações Prisma. É idempotente — pode ser re-executado sem problemas.

**3. Suba os serviços**

```bash
source scripts/linux/set-env.sh
pm2 start ecosystem.config.js
```

**4. Seed do demo**

```bash
bash scripts/linux/seed-demo.sh
```

Registra os pools `demo_ia` e `suporte_humano`, a skill `skill_demo_chat_v1`, os AgentTypes e as instâncias no Redis.

### Uso diário

```bash
# Carregar variáveis de ambiente e iniciar (se PM2 não estiver rodando)
source scripts/linux/set-env.sh && pm2 start ecosystem.config.js

# Verificar status de todos os serviços
pm2 status

# Acompanhar logs em tempo real
pm2 logs

# Logs de um serviço específico
pm2 logs ai-gateway
pm2 logs agent-registry

# Reiniciar um serviço após alteração de código
pm2 restart ai-gateway

# Reiniciar todos
pm2 restart all

# Parar tudo
pm2 stop all

# Remover do registro PM2 (para reconfigurar)
pm2 delete all
```

### Fazer o PM2 iniciar com o sistema (opcional)

```bash
pm2 startup          # gera o comando systemd — execute o comando que aparecer
pm2 save             # salva a lista de processos atual
```

Na próxima vez que o servidor reiniciar, o PM2 sobe automaticamente todos os serviços.

### Serviços gerenciados

| Nome PM2 | Tipo | Porta |
|---|---|---|
| `agent-registry` | Node.js | 3300 |
| `mcp-server` | Node.js | 3100 |
| `ai-gateway` | Python (uvicorn) | 3200 |
| `routing-engine` | Python (Kafka consumer) | — |
| `rules-engine-api` | Python (uvicorn) | 3201 |
| `rules-engine-monitor` | Python (Kafka consumer) | — |
| `channel-gateway` | Python (Kafka + HTTP) | configurável |
| `conversation-writer` | Python (Kafka consumer) | — |
| `skill-flow-service` | Node.js | 3400 |
| `clickhouse-consumer` | Python (Kafka consumer) | — |
| `orchestrator-bridge` | Python (Kafka consumer) | — |
| `dashboard-api` | Python (entry point) | configurável |
| `agent-assist-ui` | Vite dev server | 5173 |
| `dashboard-ui` | Vite dev server | 5174 |

### Scripts disponíveis

| Script | Descrição |
|---|---|
| `scripts/linux/set-env.sh` | Template de variáveis de ambiente (edite antes de usar) |
| `scripts/linux/setup.sh` | Setup completo (primeira vez) |
| `scripts/linux/seed-demo.sh` | Seed do Demo 2 — fluxo LLM com sentimento |
| `ecosystem.config.js` | Configuração PM2 (raiz do repositório) |

### Alternando entre Demo 1 (IVR) e Demo 2 (LLM)

O `PLUGHUB_ENTRY_POINT_POOL_ID` no `set-env.sh` define qual pool recebe as conversas inbound:

```bash
# Demo 2 — fluxo LLM com sentimento em tempo real (padrão)
export PLUGHUB_ENTRY_POINT_POOL_ID="demo_ia"

# Demo 1 — fluxo IVR baseado em regras
export PLUGHUB_ENTRY_POINT_POOL_ID="sac_ia"
```

Após alterar, reinicie o `channel-gateway`:

```bash
source scripts/linux/set-env.sh
pm2 restart channel-gateway
```
